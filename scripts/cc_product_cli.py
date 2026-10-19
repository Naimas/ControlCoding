"""Core Product CLI; optional desktop, memory and models are not prerequisites."""
import json
from pathlib import Path

from cc_development_lib import DevelopmentError, read, prepare, commit
import cc_product_documents as documents

COMMANDS = ('status', 'progress', 'source', 'files', 'template', 'adoption-preview', 'planning-preview', 'preview', 'apply', 'export-preview', 'export', 'roundtrip', 'change-export-preview', 'change-export', 'stage-export-preview', 'stage-export', 'stage-import-preview')
PRODUCT_ROUTED_COMMANDS = frozenset({('product',), *(('product', c) for c in COMMANDS)})
PRODUCT_CAPABILITY_REGISTRY_ITEM = {'id': 'product_workflow', 'label': 'Reviewed Project Document, versioned scope and dependency planning',
    'state': 'experimental', 'control_level': 'conditional', 'hosts': ['all'],
    'commands': ['product ' + c for c in COMMANDS], 'evidence': ['scripts/cc_product.py', 'tests/test_cc_product_documents.py']}


def add_product_parser(subparsers, path_type):
    parser = subparsers.add_parser('product', help='Review Project Documents and selected feature planning')
    parser.add_argument('--project-root', type=path_type, default=Path.cwd())
    commands = parser.add_subparsers(dest='product_command')
    for name in COMMANDS:
        p = commands.add_parser(name)
        p.add_argument('--project-root', type=path_type, default=__import__('argparse').SUPPRESS)
        p.add_argument('--json', dest='json_output', action='store_true')
        if name == 'progress':
            p.add_argument('--view', default='overview')
            p.add_argument('--entity-id')
            p.add_argument('--collection')
            p.add_argument('--cursor')
            p.add_argument('--observation', dest='observation_id')
        if name == 'source':
            p.add_argument('--path', default=''); p.add_argument('--text', default='')
        if name == 'files': p.add_argument('--path', action='append', required=True)
        if name == 'adoption-preview': p.add_argument('--source', required=True)
        if name == 'planning-preview':
            p.add_argument('--change-id', required=True); p.add_argument('--stage', choices=['design', 'plan'], required=True)
        if name in ('stage-export-preview', 'stage-export', 'stage-import-preview'):
            p.add_argument('--change-id', required=True); p.add_argument('--stage', choices=['design', 'plan'], required=True)
            p.add_argument('--path', required=True)
            if name != 'stage-import-preview': p.add_argument('--mode', choices=['managed', 'project_managed'], default='managed')
            if name == 'stage-export': p.add_argument('--preview-file', type=path_type, required=True)
        if name == 'template':
            p.add_argument('--change-id', required=True); p.add_argument('--stage', choices=['design', 'plan'], required=True)
        if name in ('preview', 'apply'):
            p.add_argument('--action-file', type=path_type, required=True); p.add_argument('--revision', required=True)
            if name == 'apply': p.add_argument('--approval-id', required=True)
        if name in ('export-preview', 'export', 'roundtrip', 'change-export-preview', 'change-export'):
            p.add_argument('--path', default='')
        if name in ('export-preview', 'export', 'change-export-preview', 'change-export'):
            p.add_argument('--ownership', choices=['managed', 'project_managed'], default='managed')
        if name in ('change-export-preview', 'change-export'):
            p.add_argument('--change-id', required=True); p.add_argument('--stage', choices=['intent', 'design', 'plan'], required=True)
        if name in ('export', 'change-export'):
            p.add_argument('--expected-hash', required=True); p.add_argument('--preview-id', required=True)
            p.add_argument('--replace-reviewed', action='store_true')
    return parser


def dispatch_product_command(args, project, parser):
    command = args.product_command
    if not command:
        parser.print_help(); return 1
    try:
        if command == 'status': result = read(project)['product']
        elif command == 'progress':
            from cc_product_progress import read as progress_read, validate_query, validate_response
            query = validate_query({key: getattr(args, key) for key in
                                    ('view', 'entity_id', 'collection', 'cursor', 'observation_id')})
            result = validate_response(progress_read(project, **query))
        elif command == 'source': result = documents.observe_source(project, args.path, text=args.text)
        elif command == 'files': result = documents.observe_files(project, args.path)
        elif command == 'adoption-preview':
            from cc_product_adoption import preview
            result = preview(project, args.source)
        elif command == 'planning-preview':
            from cc_evidence_inputs import SafeRoot
            from cc_development_lib.storage import load
            from cc_product_planning import preview
            with SafeRoot(project) as safe:
                state, _ = load(safe)
                result = preview(safe, state, args.change_id, args.stage)
                safe.validate()
        elif command in ('stage-export-preview', 'stage-export', 'stage-import-preview'):
            options = {'change_id': args.change_id, 'stage': args.stage, 'path': args.path}
            if command == 'stage-import-preview': result = documents.stage_import_preview(project, **options)
            else:
                options['mode'] = args.mode
                if command == 'stage-export':
                    raw = args.preview_file.read_bytes()
                    if len(raw) > 512 * 1024: raise DevelopmentError('request_limit')
                    from cc_development_lib.storage import pairs
                    options['preview'] = json.loads(raw, object_pairs_hook=pairs)
                result = (documents.stage_export_commit if command == 'stage-export' else documents.stage_export_preview)(project, **options)
        elif command == 'template':
            from cc_product import template
            result = template(project, args.change_id, args.stage)
        elif command in ('preview', 'apply'):
            raw = args.action_file.read_bytes()
            if len(raw) > 512 * 1024: raise DevelopmentError('request_limit')
            from cc_development_lib.storage import pairs
            action = json.loads(raw, object_pairs_hook=pairs)
            if type(action) is not dict or not str(action.get('kind', '')).startswith('product_'): raise DevelopmentError('invalid_product_action')
            result = prepare(project, action, args.revision) if command == 'preview' else commit(project, action, args.revision, args.approval_id)
        elif command in ('change-export-preview', 'change-export'):
            options = {'path': args.path, 'ownership': args.ownership}
            if command == 'change-export': options.update(expected_hash=args.expected_hash, preview_id=args.preview_id, replacement=args.replace_reviewed)
            result = (documents.change_export_commit if command == 'change-export' else documents.change_export_preview)(project, args.change_id, args.stage, **options)
        else:
            product = read(project)['product']
            if product['document'] is None: raise DevelopmentError('product_document_required')
            if command == 'roundtrip': result = documents.roundtrip_preview(project, args.path, product['document'], product['current_revision'])
            else:
                options = {'path': args.path, 'ownership': args.ownership, 'status': 'accepted' if product['accepted_revision'] == product['current_revision'] else 'draft'}
                if command == 'export': options.update(expected_hash=args.expected_hash, preview_id=args.preview_id, replacement=args.replace_reviewed)
                result = (documents.export_commit if command == 'export' else documents.export_preview)(project, product['document'], product['current_revision'], **options)
        if getattr(args, 'json_output', False):
            print(json.dumps(result, ensure_ascii=False, separators=(',', ':')) if command == 'progress'
                  else json.dumps(result, indent=2, ensure_ascii=False))
        elif command == 'status':
            print('Project Document: ' + (result['document']['title'] if result['document'] else 'not saved'))
            print('Revision: ' + result['current_revision'])
            for reason in result.get('blockers', []): print('- ' + reason)
        elif command in ('export-preview', 'change-export-preview'): print(result['content']); print('Export preview: ' + result['preview_id'])
        else: print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (DevelopmentError, ValueError, OSError, TypeError, KeyError) as error:
        result = {'ok': False, 'error': {'code': getattr(error, 'code', 'invalid_product_request')}}
        print(json.dumps(result) if getattr(args, 'json_output', False) else 'Product operation failed: ' + result['error']['code'])
        return 1
