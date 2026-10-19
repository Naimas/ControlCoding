"""Bounded local planning transport. This CLI never calls a model provider."""
import argparse
import json
from pathlib import Path

COMMANDS = ('read', 'create', 'append', 'preview', 'prepare', 'reserve', 'finish', 'review')
PLANNING_ROUTED_COMMANDS = frozenset({('planning',), *(('planning', c) for c in COMMANDS)})
PLANNING_CAPABILITY_REGISTRY_ITEM = {
    'id': 'planning_discussion', 'label': 'Durable bounded planning and reviewed proposals',
    'state': 'experimental', 'control_level': 'conditional', 'hosts': ['all'],
    'commands': ['planning ' + c for c in COMMANDS],
    'evidence': ['scripts/cc_planning.py', 'tests/test_cc_planning_transport.py'],
}
MAX_REQUEST_BYTES = 1024 * 1024


def add_planning_parser(subparsers, path_type):
    parser = subparsers.add_parser('planning', help='Persist planning discussion and review bounded proposals; no provider call')
    parser.add_argument('--project-root', type=path_type, default=Path.cwd())
    commands = parser.add_subparsers(dest='planning_command')
    for name in COMMANDS:
        p = commands.add_parser(name)
        p.add_argument('--project-root', type=path_type, default=argparse.SUPPRESS)
        p.add_argument('--json', dest='json_output', action='store_true')
        p.add_argument('--request-file', type=path_type, required=name != 'read')
        if name == 'read':
            p.add_argument('--session-id', default='')
    return parser


def dispatch_planning_command(args, project, parser):
    from cc_development_lib import DevelopmentError
    import cc_planning
    command = args.planning_command
    if not command:
        parser.print_help()
        return 1
    try:
        if args.request_file:
            with args.request_file.open('rb') as stream:
                raw = stream.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise DevelopmentError('planning_request_limit')
            from cc_development_lib.storage import pairs
            payload = json.loads(raw, object_pairs_hook=pairs)
            if type(payload) is not dict:
                raise DevelopmentError('invalid_planning_request')
            if 'operation' in payload and payload['operation'] != 'planning_' + command:
                raise DevelopmentError('planning_operation_mismatch')
            payload = {**payload, 'operation': 'planning_' + command}
        else:
            if command != 'read':
                raise DevelopmentError('planning_session_required')
            payload = {'operation': 'planning_read', 'session_id': args.session_id}
        if getattr(args, 'session_id', None) and args.request_file:
            raise DevelopmentError('invalid_planning_request')
        result = cc_planning.dispatch(project, payload)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (DevelopmentError, ValueError, OSError, TypeError, KeyError, RecursionError) as error:
        result = {'ok': False, 'error': {'code': getattr(error, 'code', 'invalid_planning_request')}}
        print(json.dumps(result) if getattr(args, 'json_output', False) else 'Planning operation failed: ' + result['error']['code'])
        return 1
