"""Explicit qualified proposal execution. Workers run as the user, not a sandbox.

Only the separately approved gateway can apply returned text to project files.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import cc_evidence_inputs as inputs
from cc_layout import managed_relative
from cc_development_adapters import read_bytes, read_json, require, IDENTIFIER
from cc_development_jobs import _executable, _environment, FORBIDDEN, _project_identity

MANIFEST = '.controlcoding/development-execution.json'
RECEIPTS = '.controlcoding/development_execution_receipts'
ENGINE = Path(__file__).absolute().parent


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe_path(path):
    inputs.parts(path)
    lower = path.casefold().replace('\\', '/')
    require(lower.split('/')[0] not in {'.controlcoding', '.git', '.codex', '.agents', '.claude'}
            and lower not in {'agents.md', 'controlcoding.md'}
            and not lower.startswith(('hooks/', 'templates/hooks/', 'scripts/hooks/', 'cc/.controlcoding/')),
            'execution_protected_scope')


def manifest(root):
    relative = managed_relative(root, MANIFEST)
    value, digest = read_json(root, relative)
    require(type(value) is dict and set(value) == {'schemaVersion', 'adapters'}
            and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
            and type(value['adapters']) is list and 1 <= len(value['adapters']) <= 32,
            'execution_manifest_invalid')
    seen = set()
    for item in value['adapters']:
        require(type(item) is dict and set(item) == {'id', 'kind', 'argv', 'files', 'configurationPath',
                'evaluationId', 'timeoutSeconds', 'outputLimitBytes', 'maxInputTokens', 'maxOutputTokens', 'attemptToken'},
                'execution_manifest_invalid')
        require(type(item['id']) is str and IDENTIFIER.fullmatch(item['id']) and item['id'] not in seen
                and item['kind'] in {'proposal_worker', 'codex_cli'}, 'execution_manifest_invalid')
        seen.add(item['id'])
        require(type(item['attemptToken']) is str and IDENTIFIER.fullmatch(item['attemptToken'])
                and type(item['evaluationId']) is str and IDENTIFIER.fullmatch(item['evaluationId']), 'execution_selection_invalid')
        argv = item['argv']
        require(type(argv) is list and 1 <= len(argv) <= 64
                and all(type(a) is str and a and len(a) <= 4096 and not any(c in a for c in '\x00\r\n') for a in argv),
                'execution_argv_invalid')
        require(Path(argv[0]).name.casefold() not in FORBIDDEN
                and not any(a.casefold() in {'-c', '-e', '/c', '/k', '--eval', '-command', '-encodedcommand'} for a in argv[1:]),
                'execution_inline_code_forbidden')
        require(type(item['files']) is list and 1 <= len(item['files']) <= 64, 'execution_pins_invalid')
        paths = set()
        for pin in item['files']:
            require(type(pin) is dict and set(pin) == {'path', 'sha256'} and type(pin['sha256']) is str
                    and len(pin['sha256']) == 64 and all(c in '0123456789abcdef' for c in pin['sha256']), 'execution_pins_invalid')
            inputs.parts(pin['path'])
            require(pin['path'].casefold() not in paths, 'execution_pins_invalid')
            paths.add(pin['path'].casefold())
        require(item['configurationPath'].casefold() in paths, 'execution_configuration_unpinned')
        require(type(item['timeoutSeconds']) is int and 1 <= item['timeoutSeconds'] <= 300
                and type(item['outputLimitBytes']) is int and 1024 <= item['outputLimitBytes'] <= 131072
                and all(type(item[k]) is int and 0 < item[k] <= 10**9 for k in ('maxInputTokens', 'maxOutputTokens')),
                'execution_budget_invalid')
    return value, digest, relative


def _argv(root, spec):
    executable = _executable(spec['argv'][0], root)
    paths = {p['path'] for p in spec['files']}
    args = [executable['path']]
    for arg in spec['argv'][1:]:
        require(not ('{' in arg or '}' in arg) or arg == '{request}', 'execution_placeholder_invalid')
        if arg.lower().endswith(('.py', '.js', '.cjs', '.mjs', '.json')):
            require(arg in paths, 'execution_program_unpinned')
        args.append(str(Path(root).absolute() / arg) if arg in paths else arg)
    return args, executable


def _canonical_change(root, change, *, locked=False):
    """Compatibility is trusted only after canonical owner-state validation."""
    from cc_development_lib import storage
    require(type(change) is dict and type(change.get('id')) is str, 'execution_method_changed')
    with inputs.SafeRoot(root) as safe:
        state, _ = storage.load(safe, locked=locked)
        matches = [row for row in state['changes'] if row['id'] == change['id']]
        require(len(matches) == 1, 'execution_method_changed')
        canonical = matches[0]
        supplied = {key: change.get(key) for key in canonical}
        if type(supplied.get('candidate')) is dict:
            supplied['candidate'] = {key: value for key, value in supplied['candidate'].items() if key != 'current'}
        require(supplied == canonical, 'execution_method_changed')
        safe.validate()
        return canonical


def prepare(root, change, adapter_id, *, _locked=False):
    """Read-only exact preview; qualification is canonical CD07 evidence."""
    root = Path(root).absolute()
    from cc_development_lib import check_root
    from cc_development_lib.method import IDENTITY
    check_root(root)
    change = _canonical_change(root, change, locked=_locked)
    declaration, digest, relative = manifest(root)
    matches = [x for x in declaration['adapters'] if x['id'] == adapter_id]
    require(len(matches) == 1, 'execution_adapter_missing')
    spec = matches[0]
    from cc_development_adapters import manual_handoff
    from cc_development_lib.policy import automation_qualification
    handoff = manual_handoff(root, change)
    for row in handoff['baseline']:
        _safe_path(row['path'])
    import cc_development_gateway
    cc_development_gateway.validate_scope([r['path'] for r in handoff['baseline']])
    pins = spec['files']
    for pin in pins:
        require(_sha(read_bytes(root, pin['path'])) == pin['sha256'], 'execution_pin_changed')
    configuration, config_digest = read_json(root, spec['configurationPath'])
    qualified = automation_qualification(root, change, {'evaluation_id': spec['evaluationId'], 'configuration_digest': config_digest})
    require(qualified['state'] == 'allowed', 'execution_configuration_unqualified')
    evaluation = qualified['evaluation']
    evaluation_plan = evaluation['evaluationPlan']
    if 'planningDetail' in handoff:
        require(evaluation_plan['suite']['schemaVersion'] == 2
                and all('detail_behavior' in case for case in evaluation_plan['suite']['cases']),
                'execution_planning_protocol_unqualified')
    require(configuration == evaluation_plan['configuration'], 'execution_configuration_mismatch')
    argv, executable = _argv(root, spec)
    import cc_development_jobs as jobs
    records = [r for batch in change['evidence'] for r in batch['records']
               if r.get('kind') == 'agent_eval' and r.get('id') == spec['evaluationId']]
    require(records, 'execution_qualification_missing')
    qualification_path = managed_relative(root, jobs.RECEIPTS) + '/' + records[-1]['receiptId'] + '.json'
    retained, qualification_receipt_digest = read_json(root, qualification_path)
    eval_argv = retained['plan']['argv']
    eval_paths = {p['path'] for p in retained['plan']['pins']}
    normalized = [str(root / a) if a in eval_paths else a.replace('{evalRequest}', '{request}') for a in eval_argv]
    require(executable == retained['plan']['executable'], 'execution_executable_unqualified')
    qualified_pins = {(p['path'], p['sha256']) for p in retained['plan']['pins']}
    require(all((p['path'], p['sha256']) in qualified_pins for p in pins), 'execution_pin_unqualified')
    if spec['kind'] == 'codex_cli':
        # Codex is reached only through the same qualified file-worker wrapper.
        require('--codex' in argv and '--codex-sha256' in argv,
                'execution_codex_protocol_not_qualified')
        import cc_development_codex
        wrappers = [p for p in pins if Path(p['path']).name == 'cc_development_codex.py']
        require(len(wrappers) == 1 and wrappers[0]['sha256'] == _sha(Path(cc_development_codex.__file__).read_bytes()),
                'execution_codex_wrapper_untrusted')
        require(str(root / wrappers[0]['path']) in argv[1:], 'execution_codex_wrapper_not_invoked')
        require(argv.count('--codex') == argv.count('--codex-sha256') == 1,
                'execution_codex_native_binding_invalid')
        native_index, digest_index = argv.index('--codex') + 1, argv.index('--codex-sha256') + 1
        require(native_index < len(argv) and digest_index < len(argv), 'execution_codex_native_binding_invalid')
        native = _executable(argv[native_index], root)
        require(Path(argv[native_index]).is_absolute() and native['sha256'] == argv[digest_index],
                'execution_codex_native_binding_invalid')
    require(argv == normalized and argv.count('{request}') == 1, 'execution_argv_unqualified')
    engine_pins = {name: _sha(read_bytes(ENGINE, name)) for name in
                   ('cc_development_execution.py', 'cc_development_eval.py', 'cc_development_jobs.py')}
    plan = {'schemaVersion': 1, 'adapterId': adapter_id, 'specification': spec, 'manifestPath': relative,
            'manifestDigest': digest, 'root': str(root), 'projectIdentity': _project_identity(root),
            'changeId': change['id'], 'method': dict(IDENTITY), 'handoff': handoff,
            'configurationDigest': config_digest, 'configuration': configuration,
            'qualification': qualified, 'qualificationDigest': inputs.digest(qualified),
            'qualificationReceiptPath': qualification_path, 'qualificationReceiptDigest': qualification_receipt_digest,
            'argv': argv, 'executable': executable, 'pins': pins, 'enginePins': engine_pins,
            'effects': 'Execute the qualified worker with user privileges in a retained isolated working directory. This is not a security sandbox. Returned files are proposals only; no project source is applied.'}
    plan['approval_id'] = inputs.digest(plan)
    return plan


def _relative(root, receipt_id):
    require(type(receipt_id) is str and len(receipt_id) == 42 and receipt_id.startswith('execution_')
            and all(c in '0123456789abcdef' for c in receipt_id[10:]), 'execution_receipt_invalid')
    return managed_relative(root, RECEIPTS) + '/' + receipt_id + '.json'


def _load(root, receipt_id):
    receipt, _ = read_json(root, _relative(root, receipt_id))
    require(receipt.get('schemaVersion') == 1 and receipt.get('id') == receipt_id
            and receipt.get('state') in {'running', 'allowed', 'blocked'}, 'execution_receipt_invalid')
    require(receipt.get('integrity') == inputs.digest({k: v for k, v in receipt.items() if k != 'integrity'}), 'execution_receipt_changed')
    return receipt


def _write(root, relative, receipt, *, exclusive=False):
    receipt['integrity'] = inputs.digest({k: v for k, v in receipt.items() if k != 'integrity'})
    with inputs.SafeRoot(root) as reader:
        reader.write_atomic(relative, receipt, replace_existing=not exclusive)
        reader.validate()


def read_receipt(root, receipt_id, change=None, *, current=True):
    from cc_development_lib import check_root
    from cc_development_lib.method import IDENTITY
    check_root(root)
    receipt = _load(root, receipt_id)
    plan = receipt['plan']
    require(plan['method'] == IDENTITY, 'execution_method_changed')
    require(receipt['approvalId'] == plan['approval_id'] == inputs.digest({k: v for k, v in plan.items() if k != 'approval_id'}),
            'execution_preview_changed')
    require(plan['projectIdentity'] == _project_identity(root) and plan['root'] == str(Path(root).absolute()), 'execution_wrong_project')
    _, digest, _ = manifest(root)
    require(digest == plan['manifestDigest'], 'execution_manifest_changed')
    require(_executable(plan['argv'][0], root) == plan['executable'], 'execution_executable_changed')
    for pin in plan['pins']:
        require(_sha(read_bytes(root, pin['path'])) == pin['sha256'], 'execution_pin_changed')
    for name, digest in plan['enginePins'].items():
        require(_sha(read_bytes(ENGINE, name)) == digest, 'execution_engine_changed')
    qualification_receipt, digest = read_json(root, plan['qualificationReceiptPath'])
    require(digest == plan['qualificationReceiptDigest'], 'execution_qualification_changed')
    import cc_development_eval
    require(cc_development_eval.assess(plan['qualification']['evaluation']['evaluationPlan'],
                qualification_receipt['observation'], root=root)['state'] == 'allowed', 'execution_qualification_invalidated')
    require(receipt['proposalDigest'] == inputs.digest(receipt['proposal']), 'execution_proposal_changed')
    if change is not None:
        require(change['id'] == plan['changeId'] and change['method'] == plan['method'], 'execution_wrong_change')
        records = [r for batch in change['evidence'] for r in batch['records']
                   if r.get('kind') == 'agent_eval' and r.get('id') == plan['specification']['evaluationId']]
        require(records and records[-1].get('observation') == qualification_receipt['observation']
                and records[-1].get('receiptId') == qualification_receipt['id'], 'execution_qualification_selection_changed')
        if current:
            require(prepare(root, change, plan['adapterId'])['approval_id'] == receipt['approvalId'], 'execution_inputs_changed')
    return receipt


def inspect_receipt(root, receipt_id, change=None):
    """Historical owner record, never current execution qualification.

    Stale configuration, worker bytes or revoked evaluations must remain
    inspectable without granting permission to apply a historical proposal.
    """
    receipt = _load(root, receipt_id)
    plan = receipt['plan']
    require(receipt['approvalId'] == plan['approval_id'] == inputs.digest({k: v for k, v in plan.items() if k != 'approval_id'}),
            'execution_preview_changed')
    require(plan['projectIdentity'] == _project_identity(root) and plan['root'] == str(Path(root).absolute()),
            'execution_wrong_project')
    require(type(plan.get('method')) is dict and plan['method'].get('id') == 'controlcoding-controlled-development',
            'execution_method_invalid')
    require(receipt['proposalDigest'] == inputs.digest(receipt['proposal']), 'execution_proposal_changed')
    if change is not None:
        wanted = change.get('id') if type(change) is dict else change
        require(wanted == plan['changeId'], 'execution_wrong_change')
    assessment = {'state': 'not_assessed', 'reasons': ['historical_record_only'], 'qualification': False}
    if type(change) is dict:
        try:
            read_receipt(root, receipt_id, change, current=False)
            assessment = {'state': 'allowed', 'reasons': [], 'qualification': receipt['state'] == 'allowed'}
        except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as error:
            assessment = {'state': 'blocked', 'reasons': [getattr(error, 'code', str(error) if isinstance(error, inputs.EvidenceError)
                                                        else 'execution_current_assessment_failed')], 'qualification': False}
    return {**receipt, 'historical': True, 'currentAssessment': assessment}


def validate_proposal(root, change, receipt_id, *, allow_applied=False):
    receipt = read_receipt(root, receipt_id, change, current=not allow_applied)
    require(receipt['state'] == 'allowed', 'execution_proposal_unavailable')
    baseline = {r['path']: r for r in receipt['plan']['handoff']['baseline']}
    rows = []
    for row in receipt['proposal']['files']:
        before = baseline[row['path']]
        raw = row['content'].encode('utf-8')
        rows.append({'path': row['path'], 'before_sha256': before.get('sha256'), 'before_size': before.get('size', 0),
                     'after_sha256': _sha(raw), 'after_size': len(raw), 'content_base64': base64.b64encode(raw).decode()})
    plan = receipt['plan']
    return {**receipt, 'request': plan['handoff'], 'files': rows,
             'binding': {k: plan[k] for k in ('method', 'projectIdentity', 'configurationDigest', 'qualificationDigest')}
            | {'planDigest': plan['handoff']['planDigest']}
            | ({'planningBinding': plan['handoff']['planningBinding']} if 'planningBinding' in plan['handoff'] else {})}


def _proposal(value, plan):
    require(type(value) is dict and set(value) == {'model', 'usage', 'files'}
            and value['model'] == plan['configuration']['model'], 'execution_output_invalid')
    usage = value['usage']
    require(type(usage) is dict and set(usage) == {'inputTokens', 'outputTokens', 'costMicros'}
            and all(type(x) is int and 0 <= x <= 10**12 for x in usage.values()), 'execution_usage_invalid')
    spec = plan['specification']
    require(usage['inputTokens'] <= spec['maxInputTokens'] and usage['outputTokens'] <= spec['maxOutputTokens'],
            'execution_token_budget_exceeded')
    require(usage['costMicros'] <= plan['configuration']['budgets']['maxCostMicros'], 'execution_cost_budget_exceeded')
    require(type(value['files']) is list and 1 <= len(value['files']) <= 256, 'execution_output_invalid')
    allowed = {r['path'] for r in plan['handoff']['baseline']}
    seen = set()
    for row in value['files']:
        require(type(row) is dict and set(row) == {'path', 'content'} and type(row['content']) is str
                and len(row['content'].encode()) <= 131072, 'execution_output_invalid')
        _safe_path(row['path'])
        require(row['path'] in allowed and row['path'].casefold() not in seen, 'execution_output_out_of_scope')
        seen.add(row['path'].casefold())
    import cc_development_gateway
    cc_development_gateway.validate_proposal(plan['handoff'], value)
    return value


def _run(argv, cwd, timeout, limit, cancelled):
    """Bounded pipes and an owned child; cancellation never targets an external PID."""
    from cc_evidence_process import _available
    started = time.monotonic()
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    process = None
    error = None
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=_environment(), shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        streams = {'stdout': process.stdout, 'stderr': process.stderr}
        if os.name != 'nt':
            for stream in streams.values():
                os.set_blocking(stream.fileno(), False)
        exited = None
        while streams:
            now = time.monotonic()
            if cancelled():
                error = 'cancelled'
                break
            if now - started >= timeout:
                error = 'timeout'
                break
            if process.poll() is not None:
                exited = exited or now
                if now - exited > 0.5:
                    error = 'pipe_timeout'
                    break
            progressed = False
            for name, stream in list(streams.items()):
                available = _available(stream)
                if not available:
                    continue
                if available < 0:
                    del streams[name]
                    continue
                try:
                    data = os.read(stream.fileno(), min(65536, available, max(1, limit - sum(map(len, captured.values())) + 1)))
                except BlockingIOError:
                    continue
                if not data:
                    del streams[name]
                    continue
                progressed = True
                captured[name].extend(data)
                if sum(map(len, captured.values())) > limit:
                    error = 'output_limit'
                    break
            if error:
                break
            if not progressed:
                time.sleep(0.01)
        if not error:
            try:
                process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                error = 'timeout'
    except KeyboardInterrupt:
        error = 'interrupted'
    except (OSError, ValueError):
        error = 'spawn_or_capture_error'
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    error = error or 'cleanup_error'
            for stream in (process.stdout, process.stderr):
                stream.close()
    return {'status': 'incomplete' if error else ('passed' if process.returncode == 0 else 'failed'),
            'returnCode': process.returncode if process else None, 'error': error,
            'durationMs': int((time.monotonic() - started) * 1000),
            'outputBytes': sum(map(len, captured.values()))}, bytes(captured['stdout'])


def execute(root, change, adapter_id, approval_id):
    plan = prepare(root, change, adapter_id)
    require(plan['approval_id'] == approval_id, 'preview_mismatch')
    receipt_id = 'execution_' + inputs.digest(['execution-v1', approval_id])[:32]
    relative = _relative(root, receipt_id)
    with inputs.SafeRoot(root) as reader:
        prior = reader.read(relative, inputs.Budget(total=2 * 1024 * 1024, per_file=2 * 1024 * 1024), missing=True)
    if prior:
        previous = read_receipt(root, receipt_id, change)
        require(previous['state'] != 'running', 'execution_attempt_recovery_required')
        return {**previous, 'replayed': True}
    receipt = {'schemaVersion': 1, 'id': receipt_id, 'createdAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'approvalId': approval_id, 'plan': plan, 'state': 'running', 'reasons': ['execution_running'],
               'command': None, 'proposal': None, 'proposalDigest': inputs.digest(None)}
    _write(root, relative, receipt, exclusive=True)
    reasons = []
    workspace = managed_relative(root, RECEIPTS) + '/' + receipt_id + '_workspace'
    try:
        with inputs.SafeRoot(root) as reader:
            with reader.directory(workspace, create=True, exclusive=True):
                pass
        import cc_development_eval as evaluation
        with inputs.SafeRoot(root) as reader:
            for row in plan['handoff']['baseline']:
                if row['state'] == 'existing':
                    raw = read_bytes(root, row['path'], limit=8 * 1024 * 1024)
                    require(_sha(raw) == row['sha256'], 'execution_baseline_changed')
                    # Binary inputs remain exact; proposals are UTF-8 text only.
                    evaluation._write_text(reader, workspace + '/' + row['path'], raw.decode('utf-8'))
            request = {'schemaVersion': 1, 'model': plan['configuration']['model'],
                       'prompt': json.dumps(plan['handoff']['plan'], ensure_ascii=False),
                       'plan': plan['handoff']['plan'], 'files': plan['handoff']['baseline']}
            if 'planningDetail' in plan['handoff']:
                request.update(schemaVersion=2, planningDetail=plan['handoff']['planningDetail'],
                               planningBinding=plan['handoff']['planningBinding'])
                request['prompt'] = json.dumps({'plan': request['plan'], 'planningDetail': request['planningDetail'],
                                                'planningBinding': request['planningBinding']}, ensure_ascii=False)
            require(str(Path(root).absolute()) not in json.dumps(request), 'execution_request_exposes_project_root')
            reader.write_atomic(workspace + '/request.json', request, replace_existing=False)
        command = [str(Path(root).absolute() / workspace / 'request.json') if a == '{request}' else a for a in plan['argv']]
        cancel_path = relative + '.cancel.json'
        def cancelled():
            with inputs.SafeRoot(root) as reader:
                item = reader.read(cancel_path, inputs.Budget(total=4096, per_file=4096), missing=True)
                if item is None:
                    return False
                marker = json.loads(item[0], object_pairs_hook=inputs._unique_object)
                require(marker.get('receiptId') == receipt_id and marker.get('executionApprovalId') == approval_id
                        and marker.get('approval_id') == inputs.digest({k: v for k, v in marker.items() if k != 'approval_id'}),
                        'execution_cancel_marker_invalid')
                return True
        observed, raw = _run(command, Path(root).absolute() / workspace, plan['specification']['timeoutSeconds'],
                             plan['specification']['outputLimitBytes'], cancelled)
        receipt['command'] = observed
        require(observed['status'] == 'passed', 'execution_' + (observed['error'] or 'command_failed'))
        output = json.loads(raw, object_pairs_hook=inputs._unique_object,
                            parse_constant=lambda _: require(False, 'execution_output_invalid'))
        receipt['proposal'] = _proposal(output, plan)
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as exc:
        reasons.append(getattr(exc, 'code', str(exc) if isinstance(exc, inputs.EvidenceError) else 'execution_failed'))
    try:
        require(prepare(root, change, adapter_id)['approval_id'] == approval_id, 'execution_inputs_changed')
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError):
        reasons.append('execution_inputs_changed')
    receipt.update(state='blocked' if reasons else 'allowed', reasons=reasons,
                   finishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   proposalDigest=inputs.digest(receipt['proposal']))
    _write(root, relative, receipt)
    return receipt


def cancel_prepare(root, receipt_id):
    receipt = _load(root, receipt_id)
    require(receipt['state'] == 'running', 'execution_not_running')
    plan = {'receiptId': receipt_id, 'executionApprovalId': receipt['approvalId'],
            'projectIdentity': _project_identity(root), 'effect': 'Request cancellation of this owned attempt. No external PID is signalled.'}
    plan['approval_id'] = inputs.digest(plan)
    return plan


def cancel(root, receipt_id, approval_id):
    plan = cancel_prepare(root, receipt_id)
    require(plan['approval_id'] == approval_id, 'preview_mismatch')
    with inputs.SafeRoot(root) as reader:
        relative = _relative(root, receipt_id) + '.cancel.json'
        item = reader.read(relative, inputs.Budget(total=4096, per_file=4096), missing=True)
        if item is None:
            reader.write_atomic(relative, plan, replace_existing=False)
        reader.validate()
    return {'receiptId': receipt_id, 'state': 'cancellation_requested'}
