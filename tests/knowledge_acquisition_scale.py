"""Manual real-file benchmark; experimental ceilings are confined to this process.

Usage: python -B tests/knowledge_acquisition_scale.py EXTERNAL_WORKBENCH_RUN
The supplied directory must not exist and must be outside the checkout.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service, knowledge_staging as staging
from cc_memory_lib.knowledge_sources import LIMITS, capture, passages
from cc_memory_lib.knowledge_store import database


def run(destination, full=False):
    root = Path(destination).resolve()
    checkout = Path(__file__).resolve().parents[1]
    if root.exists() or root == checkout or checkout in root.parents or root in checkout.parents:
        raise ValueError('A new external fixture directory is required')
    docs = root / 'project/docs'
    docs.mkdir(parents=True)
    for number in range(5000):
        folder = docs / f'group-{number//100:02}'
        folder.mkdir(exist_ok=True)
        (folder / f'item-{number:04}.md').write_text(f'# Source {number}\n\nUnique requirement {number}. A documented behavior for scale measurement.\n', encoding='utf-8')
    project = docs.parent
    service.configure(project, {**service.DEFAULT, 'embedding': '', 'automatic': False})
    LIMITS.update(files=5000, seconds=180)
    if full:
        LIMITS['sources'] = 5000
    report = {'files': 5000, 'experimental_limits': dict(LIMITS), 'production_limits_unchanged': True,
              'scope': 'acquisition and passage preparation only, not full reconcile or capacity acceptance', 'timings': {}}
    def timed(name, call):
        start = time.perf_counter()
        result = call()
        report['timings'][name] = time.perf_counter() - start
        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'phase': name, 'seconds': report['timings'][name]}), flush=True)
        return result
    if full:
        report['scope'] = 'full reconcile, wiki and lexical retrieval; no embeddings or session scale certification'
        initial = timed('cold_reconcile', lambda: service.reconcile(project))
        assert initial['counts']['sources'] == 5000 and initial['counts']['chunks'] == 5000
        unchanged = timed('unchanged_reconcile', lambda: service.reconcile(project))
        assert unchanged['generation'] == initial['generation']
        changed = docs / 'group-00/item-0000.md'
        moved = docs / 'group-00/item-0001.md'
        with database(project) as db:
            moved_id = db.execute('SELECT id FROM sources WHERE path=?', ('docs/group-00/item-0001.md',)).fetchone()[0]
        changed.write_text('# Changed source\n\nscaleneedle updated requirement.', encoding='utf-8')
        moved.rename(moved.with_name('renamed.md'))
        (docs / 'group-00/item-0002.md').unlink()
        refreshed = timed('edit_rename_delete_reconcile', lambda: service.reconcile(project))
        assert refreshed['counts']['sources'] == refreshed['counts']['chunks'] == 4999
        assert refreshed['generation'] == initial['generation'] + 1
        with database(project) as db:
            assert db.execute('SELECT id FROM sources WHERE path=?', ('docs/group-00/renamed.md',)).fetchone()[0] == moved_id
            assert db.execute('SELECT count(*) FROM wiki WHERE id NOT LIKE ?', ('wiki:%',)).fetchone()[0] == 4999
        result = timed('lexical_query', lambda: service.query(project, 'scaleneedle', False))
        assert result['citations'][0]['path'] == 'docs/group-00/item-0000.md'
        stable = timed('post_rename_unchanged_reconcile', lambda: service.reconcile(project))
        assert stable['generation'] == refreshed['generation']
        with database(project) as db:
            assert db.execute('SELECT id FROM sources WHERE path=?', ('docs/group-00/renamed.md',)).fetchone()[0] == moved_id
        report.update(passed=True, sources=4999, passages=4999,
                      database_bytes=(project / '.controlcoding/knowledge/knowledge.db').stat().st_size)
        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return
    with database(project) as db:
        sources = timed('cold_capture', lambda: capture(project, ['project'], db))
        assert len(sources) == 5000 and len({s['id'] for s in sources}) == 5000
        report['source_bytes'] = sum(len(s['body'].encode()) for s in sources)
        report['passages'] = sum(sum(1 for _ in passages(s)) for s in sources)
        cached = timed('matching_checkpoint_capture', lambda: capture(project, ['project'], db))
        assert staging.identity(sources) == staging.identity(cached)
        header = staging.begin(db, project, service.DEFAULT, None, sources, sources)
        timed('prepare_passages', lambda: staging.prepare(db, header, sources, passages))
        timed('matching_preparation_retry', lambda: staging.prepare(db, header, sources, passages))
        assert header['prepared'] == 5000 and header['passages'] == report['passages']
        changed = docs / 'group-00/item-0000.md'
        changed.write_text('# Changed source\n\nUpdated requirement.', encoding='utf-8')
        refreshed = timed('changed_manifest_capture', lambda: capture(project, ['project'], db))
        assert next(s for s in refreshed if s['path'].endswith('item-0000.md'))['revision'] != next(s for s in sources if s['path'].endswith('item-0000.md'))['revision']
    report['passed'] = True
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    run(sys.argv[1], '--full' in sys.argv[2:])
