"""Reviewed, confined source application of a retained coding-host proposal.

This is a cooperative local write gateway, not an OS sandbox. Each file is
atomically replaced; a multi-file operation retains durable progress and may be
partial. It never deletes files or silently recovers an interrupted operation.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

import cc_evidence_inputs as inputs
from cc_layout import managed_relative
from cc_development_adapters import require
from cc_development_lib.method import digest, canonical
from cc_development_lib.storage import identity, pairs, process_state

RECEIPTS = '.controlcoding/development_apply_receipts'
LOCK = '.controlcoding/development_apply.lock'
ID = re.compile(r'execution_[0-9a-f]{32}\Z')
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


def validate_scope(paths):
    """Hardcoded control-plane exclusions cannot be lifted by project config."""
    require(type(paths) is list and 1 <= len(paths) <= 256, 'execution_scope_invalid')
    seen = set()
    for path in paths:
        require(type(path) is str, 'execution_scope_invalid')
        components = inputs.parts(path)
        lower = [part.casefold() for part in components]
        require(path.casefold() not in seen, 'execution_scope_duplicate')
        seen.add(path.casefold())
        require(not any(p in {'.git', '.controlcoding', '.codex', '.agents', '.claude',
                              'hooks', '__pycache__'} for p in lower), 'execution_protected_path')
        leaf = lower[-1]
        require(leaf not in {'agents.md', 'controlcoding.md', 'cc_config.json', 'layout.json'}
                and not (leaf.startswith('controlcoding') and leaf.endswith('.json')),
                'execution_protected_path')
    return paths


def validate_proposal(handoff, proposal):
    """Return bounded UTF-8 content and exact before/after identities."""
    require(type(handoff) is dict and type(proposal) is dict, 'execution_proposal_invalid')
    rows = proposal.get('files')
    require(type(rows) is list and rows, 'execution_proposal_empty')
    validate_scope([row.get('path') if type(row) is dict else None for row in rows])
    scope = handoff.get('plan', {}).get('files', [])
    baseline = {row['path']: row for row in handoff.get('baseline', [])}
    total, result = 0, []
    for row in rows:
        require(set(row) == {'path', 'content'} and type(row['content']) is str,
                'execution_proposal_invalid')
        path = row['path']
        require(path in scope and path in baseline, 'execution_proposal_outside_plan')
        raw = row['content'].encode('utf-8')
        total += len(raw)
        require(len(raw) <= MAX_FILE and total <= MAX_TOTAL, 'execution_proposal_byte_limit')
        before = baseline[path]
        require(before.get('state') in ('existing', 'absent'), 'execution_baseline_invalid')
        result.append({'path': path, 'before_sha256': before.get('sha256'),
                       'before_size': before.get('size'), 'after_sha256': hashlib.sha256(raw).hexdigest(),
                       'after_size': len(raw), 'content': raw})
    return sorted(result, key=lambda row: row['path'])


def _canonical_change(root, change, *, locked=False):
    import cc_development_lib as development
    development.check_root(root)
    from cc_development_lib.storage import load
    with inputs.SafeRoot(root) as reader:
        view, _ = load(reader, locked=locked)
    wanted = change.get('id') if type(change) is dict else change
    matches = [row for row in view['changes'] if row['id'] == wanted]
    require(len(matches) == 1, 'execution_change_missing')
    return matches[0]


def _execution(root, change, receipt_id):
    require(type(receipt_id) is str and ID.fullmatch(receipt_id), 'execution_receipt_id_invalid')
    import cc_development_execution as engine
    receipt = engine.read_receipt(root, receipt_id, change, current=False)
    require(receipt['state'] == 'allowed', 'execution_proposal_not_allowed')
    handoff = receipt['plan']['handoff']
    from cc_development_lib.evaluator import latest, accepted, upstream
    plan = latest(change, 'plan')
    require(plan and accepted(change, 'plan') and plan['digest'] == handoff['planDigest']
            and plan['upstream'] == upstream(change, 'plan'), 'execution_plan_stale')
    require(all(accepted(change, stage) for stage in ('intent', 'design')), 'execution_upstream_not_accepted')
    from cc_evidence_inputs import SafeRoot
    from cc_development_lib.storage import load
    from cc_product_planning import approved_detail
    with SafeRoot(root) as safe:
        state, _ = load(safe, locked=True)
        current_detail = approved_detail(safe, state, change['id'], 'plan')
        require((current_detail['binding'] if current_detail is not None else None) == handoff.get('planningBinding')
                and (current_detail['content'] if current_detail is not None else None) == handoff.get('planningDetail'),
                'execution_planning_detail_stale')
        safe.validate()
    from cc_development_adapters import assess_owner_bindings
    require(assess_owner_bindings(root, change)['state'] == 'allowed', 'execution_owner_binding_stale')
    require(handoff['changeId'] == change['id'] and handoff['method'] == change['method'],
            'execution_change_mismatch')
    return receipt, validate_proposal(handoff, receipt['proposal'])


def _path(root, receipt_id):
    return managed_relative(root.path, RECEIPTS) + '/' + receipt_id + '.json'


def _read_apply(root, receipt_id):
    item = root.read(_path(root, receipt_id), inputs.Budget(total=2 * 1024 * 1024), missing=True)
    if item is None:
        return None
    raw, info = item
    require(info.st_nlink == 1, 'execution_apply_unsafe_receipt')
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, 'execution_apply_invalid_receipt'))
        require(type(value) is dict and value.get('schemaVersion') == 1
                and value.get('id') == receipt_id and value.get('projectIdentity') == identity(root)
                and value.get('state') in ('running', 'partial', 'applied')
                and type(value.get('history')) is list and type(value.get('files')) is list,
                'execution_apply_invalid_receipt')
        content = {k: v for k, v in value.items() if k != 'integrity'}
        require(value.get('integrity') == digest(content), 'execution_apply_receipt_changed')
        require(type(value.get('approvals')) is list and type(value.get('previews')) is list
                and len(value['approvals']) == len(value['previews']) > 0,
                'execution_apply_approval_history_invalid')
        require(all(digest(preview) == approval for preview, approval in zip(value['previews'], value['approvals'])),
                'execution_apply_approval_history_invalid')
        if value['state'] == 'applied':
            require(value['history'] and value['history'][-1] == {'kind': 'applied'},
                    'execution_apply_history_invalid')
            for row in value['files']:
                require(any(event.get('kind') == 'write_intent' and event.get('path') == row['path']
                            and event.get('before') == row['before_sha256'] and event.get('after') == row['after_sha256']
                            for event in value['history']) and
                        any(event == {'kind': 'write_observed', 'path': row['path'], 'sha256': row['after_sha256']}
                            for event in value['history']), 'execution_apply_history_invalid')
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        require(False, 'execution_apply_invalid_receipt')


def _save(root, record):
    value = dict(record)
    value.pop('integrity', None)
    record['integrity'] = digest(value)
    root.write_atomic(_path(root, record['id']), record)


def _observed(root, row):
    item = root.read(row['path'], inputs.Budget(total=MAX_FILE, per_file=MAX_FILE), missing=True)
    if item is None:
        return None, None
    raw, info = item
    require(info.st_nlink == 1, 'execution_source_hardlink')
    return hashlib.sha256(raw).hexdigest(), raw


def _zones(root, paths):
    import cc
    relative = managed_relative(root.path, 'cc_config.json')
    item = root.read(relative, inputs.Budget(total=2 * 1024 * 1024), missing=True)
    config = {}
    raw = None
    if item is not None:
        raw, info = item
        require(info.st_nlink == 1, 'execution_config_unsafe')
        config = json.loads(raw, object_pairs_hook=pairs)
        require(type(config) is dict, 'execution_config_invalid')
    zones = cc._normalize_protected_zones(config.get('protected_zones', []))
    deny, warn = cc._evaluate_controlled_write_paths(paths, zones)
    require(not deny, 'execution_configured_protected_path')
    return {'path': relative, 'digest': hashlib.sha256(raw).hexdigest() if raw is not None else None,
            'warnings': warn}


def _lock_status(root):
    relative = managed_relative(root.path, LOCK)
    entered = False
    try:
        with root.directory(relative) as directory:
            entered = True
            info = os.stat(directory) if os.name == 'nt' else os.fstat(directory)
            names = {name for name, _ in root.listing(relative, inputs.Budget(entries=4))}
            require(names == {'owner.json'}, 'execution_apply_lock_unknown')
            raw, owner_info = root.read(relative + '/owner.json', inputs.Budget(total=4096, per_file=4096))
            require(owner_info.st_nlink == 1, 'execution_apply_lock_unsafe')
            owner = json.loads(raw, object_pairs_hook=pairs)
            require(type(owner) is dict and set(owner) == {'pid', 'projectIdentity', 'receiptId'}
                    and owner['projectIdentity'] == identity(root), 'execution_apply_lock_unknown')
            state = process_state(owner['pid'])
            require(state == 'inactive', 'execution_apply_lock_' + state)
            return {'fingerprint': digest([info.st_dev, info.st_ino, hashlib.sha256(raw).hexdigest()]),
                    'receiptId': owner['receiptId'], 'info': info}
    except FileNotFoundError:
        require(not entered, 'execution_apply_lock_unknown')
        return None


def _release_lock(root, expected):
    """Delete only a known owner and empty directory, never foreign additions."""
    relative = managed_relative(root.path, LOCK)
    signature = lambda info: (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
    with root.directory(relative) as parent:
        current = os.stat(parent) if os.name == 'nt' else os.fstat(parent)
        require(signature(current) == signature(expected), 'execution_apply_lock_changed')
        require({name for name, _ in root.listing(relative, inputs.Budget(entries=4))} == {'owner.json'},
                'execution_apply_lock_changed')
        info = root._stat(parent, 'owner.json')
        require(info.st_nlink == 1 and stat.S_ISREG(info.st_mode), 'execution_apply_lock_unsafe')
        if os.name == 'nt':
            root._win_remove_file(parent, 'owner.json', info)
        else:
            os.unlink('owner.json', dir_fd=parent)
    parent_name, leaf = relative.rsplit('/', 1)
    with root.directory(parent_name) as parent:
        require(signature(root._stat(parent, leaf)) == signature(expected), 'execution_apply_lock_changed')
        if os.name == 'nt':
            os.rmdir(parent / leaf)
        else:
            os.rmdir(leaf, dir_fd=parent)


def _preview(root, change, receipt_id):
    from cc_product_evolution import guard
    change=guard(root,change,'build')
    execution, files = _execution(root.path, change, receipt_id)
    retained = _read_apply(root, receipt_id)
    lock = _lock_status(root)
    require(lock is None or lock['receiptId'] == receipt_id, 'execution_apply_other_recovery_required')
    if retained is not None:
        require(retained['executionDigest'] == digest(execution), 'execution_apply_execution_changed')
        require(retained['state'] != 'running' or process_state(retained['pid']) == 'inactive',
                'execution_apply_recovery_not_inactive')
    changed_paths = {row['path'] for row in files}
    for baseline in execution['plan']['handoff']['baseline']:
        if baseline['path'] not in changed_paths:
            require(_observed(root, baseline)[0] == baseline.get('sha256'), 'execution_baseline_source_stale')
    views = []
    for row in files:
        current, raw = _observed(root, row)
        allowed = (row['before_sha256'], row['after_sha256']) if retained is not None else (row['before_sha256'],)
        require(current in allowed, 'execution_source_conflict')
        require(retained is None or retained['state'] != 'applied' or current == row['after_sha256'],
                'execution_applied_source_stale')
        before_text = raw.decode('utf-8', errors='replace') if raw else ''
        after_text = row['content'].decode('utf-8')
        difference = ''.join(difflib.unified_diff(before_text.splitlines(True), after_text.splitlines(True),
                                                 fromfile=row['path'], tofile=row['path']))
        views.append({k: v for k, v in row.items() if k != 'content'} | {
            'current_sha256': current, 'operation': 'add' if row['before_sha256'] is None else 'modify',
            'already_applied': retained is not None and current == row['after_sha256'],
            'diff': difference[:65536], 'diff_truncated': len(difference) > 65536})
    binding = {'schemaVersion': 1, 'kind': 'execution_apply', 'receiptId': receipt_id,
               'changeId': change['id'], 'method': change['method'], 'projectIdentity': identity(root),
               'executionDigest': digest(execution), 'files': views, 'zones': _zones(root, [f['path'] for f in files]),
               'retainedDigest': digest(retained) if retained else None,
               'recoveryLock': lock['fingerprint'] if lock else None,
               'recovery': retained is not None and retained['state'] != 'applied',
               'limitations': ['local_actor_labels_not_authenticated', 'per_file_atomic_multi_file_partial',
                               'cooperative_lock_not_os_sandbox']}
    require(len(canonical(binding)) <= 512 * 1024, 'execution_apply_preview_byte_limit')
    return binding | {'approval_id': digest(binding), 'state': 'allowed', 'reasons': []}, files, lock


def prepare(root, change, receipt_id):
    change = _canonical_change(root, change)
    with inputs.SafeRoot(root) as reader:
        result, _, _ = _preview(reader, change, receipt_id)
        reader.validate()
    return result


def _write_bytes(root, row):
    """Anchor parent directories, flush a private leaf, and atomically publish."""
    components = inputs.parts(row['path'])
    with root.directory('/'.join(components[:-1]), create=True) as parent:
        name = '.development-pending-' + uuid.uuid4().hex
        options = {} if os.name == 'nt' else {'dir_fd': parent}
        temporary = parent / name if os.name == 'nt' else name
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600, **options)
        try:
            with os.fdopen(fd, 'wb') as stream:
                if os.name != 'nt' and row['before_sha256'] is not None:
                    original = root._stat(parent, components[-1])
                    os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
                stream.write(row['content'])
                stream.flush()
                os.fsync(stream.fileno())
            current, _ = _observed(root, row)
            require(current == row['before_sha256'], 'execution_source_conflict')
            root.validate()
            target = parent / components[-1] if os.name == 'nt' else components[-1]
            if row['before_sha256'] is None:
                if os.name == 'nt':
                    os.link(temporary, target)
                else:
                    os.link(name, target, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            elif os.name == 'nt':
                os.replace(temporary, target)
            else:
                os.replace(name, target, src_dir_fd=parent, dst_dir_fd=parent)
            if os.name != 'nt':
                os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, **options)
            except FileNotFoundError:
                pass
    current, _ = _observed(root, row)
    require(current == row['after_sha256'], 'execution_source_postwrite_conflict')


def apply(root, change, receipt_id, approval_id):
    """Serialize source promotion with the Core's artifact/approval writes.

    The Core lock precedes the apply lock. An abrupt process exit therefore
    requires explicit Core recovery first, followed by reviewed apply recovery.
    """
    import cc_development_lib as development
    from cc_development_lib.storage import transaction
    development.check_root(root)
    require(type(receipt_id) is str and ID.fullmatch(receipt_id), 'execution_receipt_id_invalid')
    with inputs.SafeRoot(root) as reader:
        with transaction(reader, operation_id='source_apply_' + receipt_id):
            current = _canonical_change(root, change, locked=True)
            return _apply_locked(root, current, receipt_id, approval_id)


def _apply_locked(root, change, receipt_id, approval_id):
    with inputs.SafeRoot(root) as reader:
        prior = _read_apply(reader, receipt_id)
        # A completed retry retains its original identity, yet still checks pins
        # and current source bytes; it cannot bless a changed implementation.
        if prior is not None and prior['state'] == 'applied' and approval_id in prior['approvals']:
            execution, rows = _execution(root, change, receipt_id)
            require(prior['executionDigest'] == digest(execution), 'execution_apply_execution_changed')
            require(_zones(reader, [row['path'] for row in rows]) == prior['previews'][-1]['zones'],
                    'execution_apply_config_changed')
            require(all(_observed(reader, row)[0] == row['after_sha256'] for row in rows),
                    'execution_applied_not_current')
            return prior | {'idempotent': True}
        preview, files, abandoned = _preview(reader, change, receipt_id)
        require(preview['approval_id'] == approval_id, 'execution_apply_approval_conflict')
        if abandoned:
            _release_lock(reader, abandoned['info'])
        relative = managed_relative(reader.path, LOCK)
        info = None
        try:
            with reader.directory(relative, create=True, exclusive=True) as directory:
                info = os.stat(directory) if os.name == 'nt' else os.fstat(directory)
                reader.write_atomic(relative + '/owner.json', {'pid': os.getpid(), 'projectIdentity': identity(reader),
                                                               'receiptId': receipt_id}, replace_existing=False)
            # Recheck after acquiring lock, before recording any intent to write.
            execution, current_files = _execution(root, change, receipt_id)
            require(current_files == files, 'execution_apply_execution_changed')
            require(_zones(reader, [f['path'] for f in files]) == preview['zones'], 'execution_apply_config_changed')
            record = prior or {'schemaVersion': 1, 'id': receipt_id, 'projectIdentity': identity(reader),
                'method': change['method'], 'changeId': change['id'], 'executionDigest': preview['executionDigest'],
                'planDigest': execution['plan']['handoff']['planDigest'], 'files': [], 'history': [],
                'approvals': [], 'previews': []}
            record['pid'] = os.getpid()
            record['state'] = 'running'
            record['approvals'].append(approval_id)
            record['previews'].append({k: v for k, v in preview.items() if k not in ('approval_id', 'state', 'reasons')})
            record['history'].append({'kind': 'approved_recovery' if preview['recovery'] else 'approved_apply',
                                      'approvalId': approval_id})
            _save(reader, record)
            try:
                for row in files:
                    current, _ = _observed(reader, row)
                    require(current in (row['before_sha256'], row['after_sha256']), 'execution_source_conflict')
                    record['history'].append({'kind': 'write_intent', 'path': row['path'], 'before': row['before_sha256'],
                                              'after': row['after_sha256'], 'observed': current})
                    _save(reader, record)
                    if current != row['after_sha256']:
                        _write_bytes(reader, row)
                    expected = {k: v for k, v in row.items() if k != 'content'}
                    record['files'] = [f for f in record['files'] if f['path'] != row['path']] + [expected]
                    record['history'].append({'kind': 'write_observed', 'path': row['path'], 'sha256': row['after_sha256']})
                    _save(reader, record)
                record['state'] = 'applied'
                record['history'].append({'kind': 'applied'})
                _save(reader, record)
                return record | {'idempotent': False}
            except BaseException as error:
                record['state'] = 'partial'
                record['history'].append({'kind': 'interrupted', 'reason': getattr(error, 'code', 'execution_apply_write_failed')})
                _save(reader, record)
                raise
        finally:
            if info is not None:
                _release_lock(reader, info)


def assess_applied(root, change, execution_id, apply_id=None):
    """Canonical source proof; an observed worker alone never proves application."""
    try:
        require(apply_id is None or apply_id == execution_id, 'execution_apply_id_mismatch')
        # The Core may assess its prospective candidate during a prepare. Do
        # not recursively read its evaluated snapshot or discard that candidate.
        import cc_development_lib as development
        development.check_root(root)
        with inputs.SafeRoot(root) as reader:
            execution, files = _execution(root, change, execution_id)
            record = _read_apply(reader, execution_id)
            require(record is not None and record['state'] == 'applied', 'execution_apply_not_complete')
            require(record['executionDigest'] == digest(execution) and record['method'] == change['method']
                    and record['changeId'] == change['id'], 'execution_apply_binding_changed')
            require(_zones(reader, [row['path'] for row in files]) == record['previews'][-1]['zones'],
                    'execution_apply_config_changed')
            expected = sorted([{k: v for k, v in row.items() if k != 'content'} for row in files], key=lambda f: f['path'])
            require(sorted(record['files'], key=lambda f: f['path']) == expected, 'execution_apply_files_changed')
            for row in files:
                require(_observed(reader, row)[0] == row['after_sha256'], 'execution_applied_source_stale')
            baseline = execution['plan']['handoff']['baseline']
            after = {row['path']: {'path': row['path'], 'sha256': row['after_sha256'], 'size': row['after_size']} for row in files}
            candidate_files = []
            for old in baseline:
                if old['path'] in after:
                    candidate_files.append(after[old['path']])
                elif old['state'] == 'existing':
                    expected_hash = old['sha256']
                    require(_observed(reader, {'path': old['path']})[0] == expected_hash, 'execution_baseline_source_stale')
                    candidate_files.append({'path': old['path'], 'sha256': expected_hash, 'size': old['size']})
                else:
                    require(_observed(reader, old)[0] is None, 'execution_baseline_source_stale')
            from cc_development_lib.evaluator import candidate_identity
            candidate_files.sort(key=lambda f: f['path'])
            source_digest = candidate_identity(change, candidate_files)
            candidate = change.get('candidate')
            if candidate is not None:
                require(candidate['digest'] == source_digest and candidate['files'] == candidate_files,
                        'execution_applied_candidate_mismatch')
            reader.validate()
            return {'state': 'allowed', 'reasons': [], 'provenance': 'observed_execution_and_reviewed_apply',
                    'executionId': execution_id, 'applyDigest': digest(record), 'sourceIdentity': source_digest,
                    'files': candidate_files, 'planDigest': record['planDigest']}
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as error:
        return {'state': 'blocked', 'reasons': [getattr(error, 'code', 'execution_apply_unavailable')]}
