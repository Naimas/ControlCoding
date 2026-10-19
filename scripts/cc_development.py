"""CLI routing for revision-bound controlled development (stdlib only)."""
from __future__ import annotations

import json
import argparse
from pathlib import Path

from cc_development_lib import DevelopmentError, read, prepare, commit
from cc_evidence_inputs import EvidenceError
from cc_development_store import StoreError

COMMANDS = ('status', 'show', 'method', 'init', 'create', 'preview', 'apply', 'check',
            'handoff', 'bindings', 'job-preview', 'job-run', 'recovery-preview',
            'backup', 'restore-preview', 'restore', 'eval-check', 'execution-preview',
            'execution-run', 'execution-status', 'execution-cancel-preview', 'execution-cancel',
            'proposal-preview', 'proposal-apply', 'release-preview', 'release-prepare', 'release-status',
            'compatibility', 'migration-preview', 'migrate', 'checkpoint-preview', 'checkpoint', 'history')
DEVELOPMENT_ROUTED_COMMANDS = frozenset({('development',), *(('development', c) for c in COMMANDS)})
DEVELOPMENT_CAPABILITY_REGISTRY_ITEM = {
    'id': 'controlled_development',
    'label': 'Revision-bound development artifacts, decisions and evidence gates',
    'state': 'experimental', 'control_level': 'conditional', 'hosts': ['all'],
    'commands': ['development ' + c for c in COMMANDS],
    'evidence': ['.controlcoding/development.json', 'development method contract',
                 'tests/test_cc_development_core.py', 'tests/test_cc_development_cli.py'],
}


def _json_file(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise DevelopmentError('duplicate_json_key')
            value[key] = item
        return value
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise DevelopmentError('request_limit')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
        if type(value) is not dict:
            raise DevelopmentError('invalid_action')
        return value
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise DevelopmentError('invalid_action_file') from None


def _change(snapshot, change_id):
    change = next((c for c in snapshot['changes'] if c['id'] == change_id), None)
    if change is None:
        raise DevelopmentError('unknown_change')
    return change


def _history_cursor(value):
    if value is None:
        return None
    from cc_development_compatibility import _json
    try:
        cursor = _json(value)
        if (type(cursor) is not dict or set(cursor) != {'revision', 'offset'}
                or type(cursor['revision']) is not str or len(cursor['revision']) != 64
                or any(c not in '0123456789abcdef' for c in cursor['revision'])
                or type(cursor['offset']) is not int or cursor['offset'] < 0):
            raise ValueError('invalid cursor')
        return cursor
    except (ValueError, TypeError):
        raise DevelopmentError('history_cursor_invalid') from None


def _emit(args, payload):
    if getattr(args, 'json_output', False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif 'error' in payload:
        print('Development operation failed: ' + payload['error']['code'])
    elif 'changes' in payload:
        print('Controlled development: ' + ('enabled' if payload['enabled'] else 'not enabled'))
        print('Revision: ' + payload['revision'])
        for change in payload['changes']:
            print(f"- {change['id']}: {change['title']} [{change.get('stage', 'intent')} / {change.get('status', 'draft')}]")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def add_development_parser(subparsers, path_type):
    parser = subparsers.add_parser('development', help='Control change artifacts, approvals and current evidence')
    parser.add_argument('--project-root', type=path_type, default=Path.cwd())
    commands = parser.add_subparsers(dest='development_command')
    for name in COMMANDS:
        p = commands.add_parser(name, help={
            'status': 'Read the change queue without initialization',
            'show': 'Inspect a change and current gate reasons',
            'method': 'Read the implemented method identity',
            'init': 'Explicitly activate controlled development',
            'create': 'Capture a new change',
            'preview': 'Preview a typed change operation',
            'apply': 'Apply the exact previewed operation',
            'check': 'Require a current allowed gate for a change',
            'handoff': 'Prepare an approved-plan manual host packet',
            'bindings': 'Inspect existing plan and feature owner references',
            'job-preview': 'Preview a fixed configured adapter job',
            'job-run': 'Run the exact approved configured adapter job',
            'recovery-preview': 'Inspect recovery of a lock whose owner has terminated',
            'backup': 'Emit a validated same-project registry backup',
            'restore-preview': 'Preview restoration to absent state in the same project',
            'restore': 'Restore the exact approved same-project registry archive',
            'eval-check': 'Check current qualification for an explicitly selected evaluated configuration',
            'execution-preview': 'Preview a qualified worker proposal execution',
            'execution-run': 'Run the exact approved proposal worker in a separate workspace',
            'execution-status': 'Read a retained worker execution and current application proof',
            'execution-cancel-preview': 'Preview cancellation of an owned running execution',
            'execution-cancel': 'Request the exact reviewed cancellation',
            'proposal-preview': 'Review the exact scoped proposal diff and application recovery',
            'proposal-apply': 'Apply the exact reviewed scoped proposal',
            'release-preview': 'Preview a PR-qualified release packet without deployment',
            'release-prepare': 'Retain the exact reviewed release packet without deployment',
            'release-status': 'Assess a retained release packet against current proof',
            'compatibility': 'Inspect an exact retained CD10 or PW00 registry without migration',
            'migration-preview': 'Review the exact retained predecessor upgrade and active-effect blockers',
            'migrate': 'Apply the exact reviewed storage-only predecessor upgrade',
            'checkpoint-preview': 'Review retained history checkpoint and bounded replay compaction',
            'checkpoint': 'Apply the exact reviewed history checkpoint without deleting history',
            'history': 'Read a bounded exact history page from the selected committed revision',
        }[name])
        p.add_argument('--project-root', type=path_type, default=argparse.SUPPRESS)
        p.add_argument('--json', action='store_true', dest='json_output')
        if name in ('migrate', 'checkpoint'):
            p.add_argument('--revision', required=True)
            p.add_argument('--approval-id', required=True)
            p.add_argument('--actor', required=True)
        if name == 'history':
            p.add_argument('change_id')
            p.add_argument('--cursor')
            p.add_argument('--limit', type=int, default=64)
        if name in ('show', 'check', 'handoff', 'bindings', 'job-preview', 'job-run', 'create', 'eval-check') or name.startswith(('execution-', 'proposal-', 'release-')):
            p.add_argument('change_id')
        if name in ('release-preview', 'release-prepare'):
            p.add_argument('--observation-id', required=True)
            p.add_argument('--environment', required=True)
            p.add_argument('--artifact-digest', required=True)
        if name == 'release-prepare':
            p.add_argument('--approval-id', required=True)
        if name == 'release-status':
            p.add_argument('--packet-id', required=True)
        if name in ('execution-preview', 'execution-run'):
            p.add_argument('--adapter-id', required=True)
        if name.startswith(('execution-', 'proposal-')) and name not in ('execution-preview', 'execution-run'):
            p.add_argument('--execution-id', required=True)
        if name in ('execution-run', 'execution-cancel', 'proposal-apply'):
            p.add_argument('--approval-id', required=True)
        if name == 'init':
            p.add_argument('--profile', choices=('routine', 'standard', 'critical'), default='standard')
        if name == 'create':
            p.add_argument('--title', required=True)
            p.add_argument('--owner', required=True)
            p.add_argument('--risk', choices=('routine', 'standard', 'critical'), default='standard')
        if name == 'eval-check':
            p.add_argument('--evaluation-id', required=True)
            p.add_argument('--configuration-digest', required=True)
        if name in ('recovery-preview', 'restore-preview', 'restore'):
            p.add_argument('--actor', required=True)
            p.add_argument('--reason', required=True)
        if name in ('restore-preview', 'restore'):
            p.add_argument('--archive-file', type=path_type, required=True)
        if name == 'restore':
            p.add_argument('--approval-id', required=True)
        if name in ('preview', 'apply'):
            p.add_argument('--action-file', type=path_type, required=True)
            p.add_argument('--revision', required=name == 'apply')
        if name in ('apply', 'job-run'):
            p.add_argument('--approval-id', required=True)
        if name == 'check':
            p.add_argument('--stage', choices=('intent', 'design', 'plan', 'build', 'verify', 'review_release', 'maintain'), required=True)
        if name == 'bindings':
            p.add_argument('--feature-id', action='append', default=[])
            p.add_argument('--planner-path')
            p.add_argument('--planner-digest')
        if name in ('job-preview', 'job-run'):
            p.add_argument('--kind', choices=('agent_eval', 'pull_request', 'release_status', 'deploy', 'rollback', 'monitor', 'diagnosis'), required=True)
            p.add_argument('--adapter-id', required=True)
            p.add_argument('--release-packet-id')
            p.add_argument('--status-receipt-id')
    return parser


def dispatch_development_command(args, project, parser):
    command = getattr(args, 'development_command', None)
    if command is None:
        parser.print_help()
        return 1
    try:
        if command in ('checkpoint-preview', 'checkpoint', 'history'):
            from cc_development_history import checkpoint_prepare, checkpoint_commit, page
            if command == 'checkpoint-preview':
                payload = checkpoint_prepare(project)
            elif command == 'checkpoint':
                payload = checkpoint_commit(project, args.revision, args.approval_id, args.actor)
            else:
                payload = page(project, args.change_id, cursor=_history_cursor(args.cursor), limit=args.limit)
            _emit(args, payload)
            return 1 if payload.get('blockers') else 0
        if command in ('compatibility', 'migration-preview', 'migrate'):
            if command == 'compatibility':
                from cc_development_compatibility import inspect_project
                payload = inspect_project(project)
            else:
                from cc_development_migration import preview, migrate
                payload = (preview(project) if command == 'migration-preview' else
                           migrate(project, args.revision, args.approval_id, args.actor))
            _emit(args, payload)
            return 1 if payload.get('blockers') or payload.get('blockingEffects') else 0
        if command in ('backup', 'restore-preview', 'restore'):
            from cc_development_archive import backup, read_archive, prepare_restore, restore
            if command == 'backup':
                payload = backup(project)
            else:
                archive = read_archive(args.archive_file)
                payload = (prepare_restore(project, archive, args.actor, args.reason) if command == 'restore-preview'
                           else restore(project, archive, args.actor, args.reason, args.approval_id))
            _emit(args, payload)
            return 1 if payload.get('blockers') else 0
        if command == 'recovery-preview':
            from cc_development_lib import recovery_prepare
            payload = recovery_prepare(project, args.actor, args.reason)
            _emit(args, payload)
            return 1 if payload.get('blockers') else 0
        # Recovery is allowed to inspect an abandoned lock; normal reads remain
        # fail-closed until the exact recovery preview is explicitly applied.
        if command in ('preview', 'apply'):
            action = _json_file(args.action_file)
            if action.get('kind') == 'recover':
                if not args.revision:
                    raise DevelopmentError('recovery_revision_required')
                payload = (prepare(project, action, args.revision) if command == 'preview' else
                           commit(project, action, args.revision, args.approval_id))
                _emit(args, payload)
                return 1 if payload.get('blockers') else 0
        state = read(project)
        if command == 'status':
            payload = state
        elif command == 'method':
            payload = {'method': state['method'], 'capabilities': state.get('capabilities', {}),
                       'enabled': state['enabled'], 'read_only': True}
        elif command in ('init', 'create'):
            action = ({'kind': 'activate', 'profile': args.profile} if command == 'init' else
                      {'kind': 'create', 'change_id': args.change_id, 'title': args.title,
                       'owner': args.owner, 'risk': args.risk})
            preview = prepare(project, action, state['revision'])
            payload = commit(project, action, state['revision'], preview['approval_id'])
        elif command in ('preview', 'apply'):
            action = _json_file(args.action_file)
            revision = args.revision or state['revision']
            payload = (prepare(project, action, revision) if command == 'preview' else
                       commit(project, action, revision, args.approval_id))
        else:
            change = _change(state, args.change_id)
            if command == 'show':
                payload = {'revision': state['revision'], 'change': change}
            elif command.startswith('release-'):
                import cc_development_release as release
                if command == 'release-status':
                    payload = release.packet_assess(project, change, args.packet_id)
                elif command == 'release-preview':
                    payload = release.packet_preview(project, change, args.observation_id, args.environment, args.artifact_digest)
                else:
                    payload = release.packet_prepare(project, change, args.observation_id, args.environment, args.artifact_digest, args.approval_id)
            elif command.startswith(('execution-', 'proposal-')):
                import cc_development_execution as execution
                import cc_development_gateway as gateway
                if command == 'execution-preview':
                    payload = execution.prepare(project, change, args.adapter_id)
                elif command == 'execution-run':
                    payload = execution.execute(project, change, args.adapter_id, args.approval_id)
                elif command == 'execution-status':
                    payload = {'execution': execution.inspect_receipt(project, args.execution_id, change),
                               'application': gateway.assess_applied(project, change, args.execution_id)}
                elif command == 'execution-cancel-preview':
                    payload = execution.cancel_prepare(project, args.execution_id)
                elif command == 'execution-cancel':
                    payload = execution.cancel(project, args.execution_id, args.approval_id)
                elif command == 'proposal-preview':
                    payload = gateway.prepare(project, change, args.execution_id)
                else:
                    payload = gateway.apply(project, change, args.execution_id, args.approval_id)
            elif command == 'eval-check':
                from cc_development_lib.policy import automation_qualification
                result = automation_qualification(project, change, {'evaluation_id': args.evaluation_id,
                                                  'configuration_digest': args.configuration_digest})
                payload = {'change_id': change['id'], 'revision': state['revision'], **result,
                           'autonomous_execution': False,
                           'execution_route_reason': 'coding_host_adapter_unavailable'}
            elif command == 'check':
                gate = change['gates'][args.stage]
                payload = {'change_id': change['id'], 'stage': args.stage,
                           'revision': state['revision'], 'gate': gate}
                _emit(args, payload)
                return 0 if gate['outcome'] == 'allowed' else 1
            elif command == 'handoff':
                from cc_development_adapters import manual_handoff
                payload = manual_handoff(project, change)
            elif command == 'bindings':
                from cc_development_adapters import planner_binding, feature_binding
                payload = {'planner': planner_binding(project, args.planner_path, args.planner_digest),
                           'features': feature_binding(project, args.feature_id)}
            else:
                from cc_development_jobs import prepare as prepare_job, execute as execute_job
                value = {'kind': args.kind, 'adapter_id': args.adapter_id}
                for key in ('release_packet_id', 'status_receipt_id'):
                    if getattr(args, key, None) is not None:
                        value[key] = getattr(args, key)
                payload = (prepare_job(project, value, change) if command == 'job-preview' else
                           execute_job(project, value, change, args.approval_id))
        _emit(args, payload)
        if payload.get('blockers'):
            return 1
        if payload.get('status') in ('failed', 'incomplete', 'unknown', 'blocked'):
            return 1
        if payload.get('state') in ('failed', 'incomplete', 'unknown', 'blocked', 'partial', 'running'):
            return 1
        return 0
    except (DevelopmentError, EvidenceError, StoreError) as error:
        _emit(args, {'ok': False, 'error': {'code': error.code}})
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        _emit(args, {'ok': False, 'error': {'code': 'development_operation_failed'}})
        return 1
