"""Embedded knowledge service CLI and opt-in local maintenance worker.

Examples: python scripts/cc_knowledge.py --project-root PROJECT status
          python scripts/cc_knowledge.py --project-root PROJECT sync
          python scripts/cc_knowledge.py --project-root PROJECT worker
Configuration and records are JSON on stdin, not shell-interpolated arguments.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_memory_lib.knowledge_service import dispatch, status, reconcile, index
from cc_memory_lib.knowledge_store import database, put, now, worker_lease, KnowledgeError


def work(root):
    with worker_lease(root):
        while True:
            try:
                state = status(root)
                if not state.get('enabled') or not state['policy']['worker']:
                    return 0
                with database(root) as db:
                    with db:
                        put(db, 'worker_heartbeat', now())
                if state['policy']['automatic']:
                    reconcile(root, 'worker')
                    if state['policy']['embedding']:
                        index(root)
                from cc_memory_lib.knowledge_consolidation_execution import dispatch as consolidation_execution
                from uuid import uuid4
                try:
                    consolidation_execution(root, 'consolidation-queue',
                                            {'trigger': 'daily', 'event_id': uuid4().hex})
                except KnowledgeError:
                    pass
            except (KnowledgeError, OSError, ValueError):
                pass
            time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--event', default='manual', choices=['manual', 'commit-ceremony', 'source-change', 'startup'])
    parser.add_argument('--archive', type=Path, help='External .ccmemory file for explicit backup or restore')
    parser.add_argument('action', choices=['status', 'configure', 'sync', 'index', 'catalog', 'page', 'page-revision', 'notes',
                                          'conversation', 'conversation-read', 'query', 'forget', 'worker', 'wiki-lint', 'wiki-draft', 'backup', 'restore', 'library', 'catalog-window', 'graph-view', 'graph-source', 'source-retry', 'work-view', 'work-propose', 'work-review', 'work-schedule-view', 'work-schedule-save', 'ocr-preview', 'ocr-save', 'wiki-tools-view', 'wiki-tools-decide', 'wiki-tools-compare', 'wiki-tools-recover', 'wiki-tools-adopt', 'wiki-tools-propose-finding', 'wiki-review-view', 'wiki-review-propose', 'wiki-review-decide',
                                          'consolidation-migrate', 'consolidation-view', 'consolidation-create', 'consolidation-propose', 'consolidation-decide', 'consolidation-undo',
                                          'consolidation-settings', 'consolidation-prepare', 'consolidation-attempt', 'consolidation-import', 'consolidation-fail', 'consolidation-queue', 'consolidation-prune', 'consolidation-context'])
    args = parser.parse_args()
    try:
        if args.action == 'consolidation-migrate':
            if args.archive is None:
                raise KnowledgeError('archive_path_required')
            from cc_memory_lib.knowledge_consolidation_store import migrate
            print(json.dumps(migrate(args.project_root, args.archive)))
            return 0
        if args.action in ('backup', 'restore'):
            if args.archive is None:
                raise KnowledgeError('archive_path_required')
            from cc_memory_lib.knowledge_backup import backup, restore
            print(json.dumps((backup if args.action == 'backup' else restore)(args.project_root, args.archive)))
            return 0
        if args.archive is not None:
            raise KnowledgeError('unexpected_archive_path')
        if args.action == 'worker':
            return work(args.project_root)
        value = None
        if args.action in ('consolidation-view', 'consolidation-settings', 'consolidation-queue', 'consolidation-context') and not sys.stdin.isatty():
            raw = sys.stdin.buffer.read(65537)
            if len(raw) > 65536:
                raise KnowledgeError('request_limit')
            value = json.loads(raw) if raw.strip() else None
        if args.action not in ('status', 'sync', 'index', 'catalog', 'wiki-lint', 'wiki-review-view', 'work-schedule-view', 'consolidation-view', 'consolidation-settings', 'consolidation-queue', 'consolidation-context'):
            raw = sys.stdin.buffer.read(65537)
            if len(raw) > 65536:
                raise KnowledgeError('request_limit')
            value = json.loads(raw)
        if args.action == 'sync':
            value = args.event
        print(json.dumps(dispatch(args.project_root, args.action, value), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, KnowledgeError) else 'knowledge_operation_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
