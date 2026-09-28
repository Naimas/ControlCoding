"""Explicit desktop execution of a fixed set of canonical Core workflows."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).absolute().parent))

from cc_layout import managed_relative, logical_relative, is_contained
from cc_panel_configuration import encoded, digest, require, text, relative, load_json, HOSTS
from cc_setup_service import _root, _Snapshots, ReadPolicy

KINDS = ('layout_status', 'layout_init', 'install', 'engagement', 'project', 'memory', 'doctor', 'verify_status', 'verify_run', 'invariants')
CONTROLS = ('CONTROLCODING.md', 'CLAUDE.md', 'AGENTS.md', 'GEMINI.md', '.clinerules',
            '.controlcoding/cc_config.json', '.controlcoding/gateway_config.json',
            '.controlcoding/cc_engagement.json', '.controlcoding/settings.json',
            '.controlcoding/panel-setup-draft.json', '.controlcoding/panel-setup-transaction.json',
            'controlcoding.verification.json', 'controlcoding.invariants.json',
            '.git/config', '.git/hooks/pre-commit', '.git/hooks/commit-msg')


def choices(value):
    require(type(value) is dict and set(value) == {'kind', 'answers', 'suites'}, 'invalid_job')
    kind = value['kind']; require(kind in KINDS, 'invalid_job')
    a = value['answers']; require(type(a) is dict, 'invalid_job')
    suites = value['suites']
    require(type(suites) is list and len(suites) <= 32 and all(type(s) is str and 0 < len(s) <= 100 for s in suites), 'invalid_job')
    require(kind == 'verify_run' or not suites, 'invalid_job')
    if kind == 'install':
        require(set(a) == {'name', 'host', 'documentation', 'memory', 'manual', 'guidance', 'notes', 'stable', 'shared', 'features', 'rules'}, 'invalid_job')
        require(a['host'] in HOSTS and a['documentation'] in ('managed', 'project_managed') and
                a['memory'] in ('governed_scope', 'deferred') and type(a['manual']) is bool and
                a['guidance'] in ('recommended', 'preset_only', 'custom'), 'invalid_job')
        require(bool(text(a['name'], 120)), 'invalid_job')
        for key in ('notes', 'rules'):
            text(a[key], 4000, True)
        for key in ('stable', 'shared', 'features'):
            require(type(a[key]) is list and len(a[key]) <= 32, 'invalid_job')
            for p in a[key]: relative(p)
    elif kind == 'engagement':
        require(set(a) == {'manual'} and type(a['manual']) is bool, 'invalid_job')
    elif kind == 'project':
        require(set(a) == {'mode', 'summary', 'users', 'scope', 'out_of_scope', 'acceptance', 'references', 'stack', 'architecture'}, 'invalid_job')
        require(a['mode'] in ('guided', 'existing_brief', 'existing_project'), 'invalid_job')
        for key in a:
            text(a[key], 6000, True)
        require(all(a[k].strip() for k in ('summary', 'users', 'scope', 'acceptance')), 'invalid_job')
    else:
        require(not a, 'invalid_job')
    require(len(encoded(value)) <= 40000, 'invalid_job')
    return value


def handoff(value):
    a = value['answers']; kind = value['kind']
    if kind == 'install':
        return {'setup': {'name': a['name'], 'user_host': a['host'], 'documentation_mode': a['documentation'],
            'host_instruction_mode': a['guidance'], 'host_custom_notes': a['notes'].splitlines(),
            'hooks_location': 'local', 'memory_default_policy': a['memory'],
            'planning': {'tier': 'core', 'manual_consultation_allowed': a['manual']},
            'configure_advanced_packs': False, 'selected_packs': [], 'project_definition_mode': 'skip',
            'stable': a['stable'], 'shared': a['shared'], 'features': a['features'], 'behavioral_rules': a['rules'].splitlines()}}
    if kind == 'engagement':
        return {'engagement': {'tier': 'core', 'manual_consultation_allowed': a['manual'],
                'backend_policy': 'local_only', 'tandem': {'mode': 'off'}, 'specialist_paths': []}}
    if kind == 'project':
        return {'project_setup': {'project_definition_mode': a['mode'],
            'kickoff_mode': {'guided': 'idea', 'existing_brief': 'partial_spec', 'existing_project': 'existing_design'}[a['mode']],
            'stack': a['stack'], 'arch': a['architecture'],
            'kickoff': {'vision': a['summary'], 'users': a['users'], 'must_haves': a['scope'],
                        'anti_goals': a['out_of_scope'], 'invariants': a['acceptance'], 'references': a['references']}}}
    return None


def prepare(root_value, value):
    value = choices(value); root = _root(root_value)
    scripts = Path(__file__).absolute().parent
    trusted = {scripts / n: 'scripts/'+n for n in ('cc.py', 'cc_setup.py', 'cc_layout.py', 'cc_layout_cli.py', 'cc_panel_jobs.py', 'cc_evidence.py', 'cc_evidence_process.py')}
    with _Snapshots(root, trusted, ReadPolicy()) as reader:
        reader.observe(root, {}, directory=True)
        for p in trusted: reader.observe(p, {})
        existing, contracts = [], {}
        for name in CONTROLS:
            physical = managed_relative(root, name) if name.startswith(('.controlcoding/', 'controlcoding.')) or name == 'CONTROLCODING.md' else name
            snap = reader.observe(root / physical, {})
            if snap:
                require(name != '.controlcoding/panel-setup-transaction.json', 'recovery_required')
                existing.append({'path': physical, 'sha256': digest(snap[-1]), 'bytes': len(snap[-1])})
                if name in ('controlcoding.verification.json', 'controlcoding.invariants.json'):
                    contracts[name] = load_json(snap[-1])
        kind = value['kind']; blockers = []; suites = []
        if kind == 'verify_run':
            contract = contracts.get('controlcoding.verification.json')
            require(contract is not None and type(contract.get('suites')) is list, 'missing_verification_contract')
            require(all(type(s) is dict and type(s.get('id')) is str and type(s.get('command')) is str for s in contract['suites']), 'invalid_job')
            import cc
            suites, issues = cc._select_verification_suites(contract, value['suites'], [], False)
            require(suites and not issues, 'invalid_suite')
        if kind in ('project', 'engagement') and not any(logical_relative(root, p['path']) in ('CONTROLCODING.md', 'CLAUDE.md') for p in existing):
            blockers.append('Install ControlCoding before this step.')
        from cc_layout_cli import status, initialization_plan
        layout = status(root)
        if kind == 'layout_init':
            storage_plan = initialization_plan(root)
            blockers.extend(storage_plan['blockers'])
        command = {'layout_status': ['layout', 'status'], 'layout_init': ['layout', 'init', '--apply'], 'install': ['setup', '--answers-file', '<reviewed-handoff>', '--apply-answers'],
                   'engagement': ['setup', '--engagement', '--answers-file', '<reviewed-handoff>', '--apply-answers'],
                   'project': ['setup-project', '--answers-file', '<reviewed-handoff>', '--apply-answers'],
                   'memory': ['memory', 'init', 'then', 'memory', 'scan', '--scope', 'governed'],
                   'doctor': ['doctor', '--json'], 'verify_status': ['verify', 'status', '--json'],
                   'verify_run': ['verify', 'run', '--json'], 'invariants': ['invariants', 'run', '--json']}[kind]
        if kind == 'verify_run':
            for suite in value['suites']: command += ['--suite', suite]
        reader.recheck()
        plan = {'schema_version': 1, 'kind': kind, 'root': root_value, 'choices': value,
            'command': ['python', 'cc.py', *command, '--project-root', root_value],
            'handoff': handoff(value), 'existing': existing, 'suites': suites,
            'invariants': contracts.get('controlcoding.invariants.json') if kind in ('invariants', 'verify_run') else None,
            'blockers': blockers, 'inputs': reader.fingerprints(), 'storage': layout,
            'effects': 'Canonical Core command with user-level access. Setup may replace context/configuration, initialize Git, install local hooks and create selected memory. Project setup creates design/planning documents. Checks may execute project-defined commands and write receipts. This is not a sandbox or a transactional installer; cancellation/failure may leave partial changes. Host delivery remains unverified.'}
        if kind.startswith('layout_'):
            plan['effects'] = ('Read-only storage inspection.' if kind == 'layout_status' else 'Activate contained storage by creating cc/layout.json in a fresh project. Existing CC installations and unrelated cc folders are preserved and require explicit migration. No hooks, host adapters, memory or providers are initialized by this step.')
        else:
            plan['effects'] += ' Managed storage: ' + layout['storage'] + '. External adapters: host instruction files, host settings, Git hooks, .gitignore and optional editor tasks.'
        plan['approval_id'] = digest(encoded(plan))
        return plan


def execute(root_value, value, approval_id, answer_path):
    plan = prepare(root_value, value)
    require(plan['approval_id'] == approval_id, 'preview_mismatch')
    require(not plan['blockers'], 'job_blocked')
    root = _root(root_value)
    # This path is supplied only by the main process in its disjoint profile.
    p = Path(answer_path)
    require(p.is_absolute() and not p.is_relative_to(root), 'invalid_job')
    if plan['handoff']:
        with p.open('xb') as f: f.write(encoded(plan['handoff']))
    import cc
    import cc_setup
    kind = value['kind']
    if kind in ('layout_status', 'layout_init'):
        from cc_layout_cli import initialize, status
        print(json.dumps(initialize(root) if kind == 'layout_init' else status(root))); return 0
    if kind == 'install': return cc_setup.cmd_setup(root, p, True)
    if kind == 'engagement': return cc_setup.cmd_setup_engagement(root, p, True)
    if kind == 'project': return cc_setup.cmd_setup_project(root, p, True)
    if kind == 'memory':
        receipt = cc_setup._bootstrap_default_project_memory(root, root.name, 'governed_scope')
        print(json.dumps(receipt)); return 0 if receipt['status'] == 'completed' else 1
    if kind == 'doctor': return cc.cmd_doctor(root, json_output=True)
    if kind == 'verify_status': return cc.cmd_verify_status(root, json_output=True)
    if kind == 'invariants': return cc.cmd_invariants_run(root, json_output=True)
    return cc.cmd_verify_run(root, suite_ids=value['suites'], json_output=True)


def main():
    try:
        raw = sys.stdin.buffer.read(60001); require(len(raw) <= 60000, 'invalid_job')
        request = load_json(raw)
        if request.get('operation') == 'preview':
            require(set(request) == {'operation', 'root', 'value'}, 'invalid_job')
            with contextlib.redirect_stdout(io.StringIO()): plan = prepare(request['root'], request['value'])
            print(json.dumps({'ok': True, 'plan': plan})); return 0
        require(set(request) == {'operation', 'root', 'value', 'approval_id', 'answer_path'} and request['operation'] == 'run', 'invalid_job')
        return execute(request['root'], request['value'], request['approval_id'], request['answer_path']) or 0
    except Exception as error:
        print(json.dumps({'ok': False, 'code': getattr(error, 'code', 'job_failed')})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
