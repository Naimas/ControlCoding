"""Explicit external 5,000-document tag and map transport experiment."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_tags import usage
from cc_memory_lib.knowledge_store import database, get, put, now
from cc_memory_lib.knowledge_tags import REGISTRY, ASSIGN
from knowledge_combined_scale import memory


def main(destination):
    root = destination.resolve()
    checkout = Path(__file__).resolve().parents[1]
    if root.exists() or checkout == root or checkout in root.parents or root in checkout.parents:
        raise ValueError('Use a new external workbench folder')
    root.mkdir(parents=True)
    project = root / 'project'
    docs = project / 'docs'
    docs.mkdir(parents=True)
    for number in range(5000):
        folder = docs / f'group-{number//100:02}'
        folder.mkdir(exist_ok=True)
        (folder / f'item-{number:04}.md').write_text(
            f'# Component {number}\n\nArchitecture of component {number}. Backend interface and security review.\n', encoding='utf-8')
    report = {'fixture': 'document-tags-scale-v1', 'sources': 5000, 'timings': {}}

    def step(name, operation):
        began = time.perf_counter()
        value = operation()
        report['timings'][name] = round(time.perf_counter() - began, 3)
        report['memory'] = memory()
        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(name, report['timings'][name], flush=True)
        return value

    service.configure(project, {**service.DEFAULT, 'embedding': ''})
    step('initial_reconcile', lambda: service.reconcile(project))
    def view(offset=0, tags=None):
        return service.dispatch(project, 'tag-view', dict(query='', section='', area='', document_type='',
            tags=tags or [], tag_mode='any', offset=offset))
    first = step('tag_view_5000', view)
    assert first['total'] == 5000 and len(first['rows']) == 25
    row = first['rows'][0]
    saved = step('single_tag_save', lambda: service.dispatch(project, 'tag-save', dict(source=row['id'],
        revision=row['revision'], expected=row['tags']['token'], registry=first['registry'],
        names=['Architecture'], reason='Reviewed source component architecture.', origin='human')))
    tag_id = next(t['id'] for t in view()['tags'] if t['name'] == 'Architecture')
    filtered = step('filtered_tag_view_5000', lambda: view(tags=[tag_id]))
    assert filtered['total'] == 1
    library = step('filtered_library_5000', lambda: service.dispatch(project, 'library',
        dict(kind='sources', query='', offset=0, snapshot=None, tags=[tag_id], tag_mode='any')))
    assert library['total'] == 1
    catalog = step('bounded_catalog', lambda: service.dispatch(project, 'catalog-window',
        dict(kind='sources', offset=0, snapshot=None)))
    assert catalog['total'] == 5000 and len(catalog['rows']) <= 200
    report['catalog_rows_in_memory'] = len(catalog['rows'])
    report['tagged_sources'] = 1
    report['saved'] = saved['saved']
    def populate():
        with database(project) as db:
            tagset = get(db, REGISTRY)
            synthetic = {f'{index:016x}': f'Topic {index:02}' for index in range(40)}
            tagset['tags'].update(synthetic)
            with db:
                put(db, REGISTRY, tagset)
                for number, row in enumerate(db.execute('SELECT id,path,revision FROM sources WHERE deleted=0 ORDER BY path,id')):
                    if number % 2 or get(db, ASSIGN + row['id']):
                        continue
                    identifiers = list(synthetic)
                    put(db, ASSIGN + row['id'], dict(path=row['path'], revision=row['revision'],
                        ids=[identifiers[number % 40], identifiers[(number + 7) % 40]],
                        reason='Synthetic benchmark topic distribution.', origin='human', updated=now()))
                put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    step('populate_2500_assignments', populate)
    populated = step('tag_view_populated_5000', view)
    assert populated['total'] == 5000 and len(populated['tags']) == 41
    topic_id = f'{0:016x}'
    filtered = step('filtered_library_populated_5000', lambda: service.dispatch(project, 'library',
        dict(kind='sources', query='', offset=0, snapshot=None, tags=[topic_id], tag_mode='any')))
    assert filtered['total'] > 0 and filtered['rows']
    graph = step('tagged_graph_populated_5000', lambda: service.dispatch(project, 'graph-view',
        dict(topic=None, query='', focus=None, offset=0, snapshot=None, tag=topic_id)))
    assert graph['graph']['total'] == filtered['total'] and len(graph['sources']) <= 200
    report['populated_assignments'] = 2500
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
