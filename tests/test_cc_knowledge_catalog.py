"""Transport scale fixtures are pre-indexed, not ingestion benchmarks."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database, KnowledgeError


def read(root, kind='sources', offset=0, snapshot=None):
    return service.dispatch(root, 'catalog-window', dict(kind=kind, offset=offset, snapshot=snapshot))


def test_5000_sources_and_all_edges_traverse_bounded_windows(project):
    with database(project) as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                       [(f's{i}', f'large/{i:04}.md', f'Document {i}', 'a'*64, 'body', 'document', 0, 'now') for i in range(5000)])
        db.executemany('INSERT INTO edges VALUES(?,?,?,?)',
                       [(f's{i}', f's{i+1}', 'a'*64, 'reference') for i in range(4999)])
        db.commit()
    snapshot = None
    for kind, minimum in [('sources', 5002), ('wiki', 0), ('edges', 4999), ('conversations', 0)]:
        rows, offset = [], 0
        while offset is not None:
            request = {'version': 1, 'id': 'catalog-scale', 'operation': 'knowledge_v1',
                       'project_root': str(project), 'action': 'catalog-window',
                       'value': dict(kind=kind, offset=offset, snapshot=snapshot)}
            process = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve().parents[1] / 'scripts/cc_panel_bridge.py')],
                                     input=json.dumps(request).encode(), capture_output=True, timeout=30, check=True)
            assert len(process.stdout) < 524288
            response = json.loads(process.stdout)
            assert response['status'] == 'ok', response
            page = response['result']['knowledge']
            assert len(page['rows']) <= 200
            assert len(json.dumps(page).encode()) < 524288
            snapshot = page['snapshot']
            rows.extend(page['rows'])
            offset = page['next_offset']
        assert len(rows) == page['total'] >= minimum
        assert len({json.dumps(r, sort_keys=True) for r in rows}) == len(rows)


def test_cross_category_snapshot_rejects_chat_and_source_edits(project):
    snapshot = read(project)['snapshot']
    service.conversation(project, record())
    with pytest.raises(KnowledgeError, match='catalog_snapshot_changed'):
        read(project, 'conversations', snapshot=snapshot)
    snapshot = read(project)['snapshot']
    (project / 'docs/design.md').write_text('# Changed\nNew content.', encoding='utf-8')
    service.reconcile(project)
    with pytest.raises(KnowledgeError, match='catalog_snapshot_changed'):
        read(project, 'wiki', snapshot=snapshot)


@pytest.mark.parametrize('value', [None, {}, {'kind': [], 'offset': 0, 'snapshot': None},
                                  {'kind': 'sources', 'offset': True, 'snapshot': None},
                                  {'kind': 'sources', 'offset': 1, 'snapshot': None}])
def test_invalid_windows(project, value):
    with pytest.raises(KnowledgeError):
        service.dispatch(project, 'catalog-window', value)


def test_byte_budget_does_not_drop_rows(project):
    with database(project) as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                       [(f'wide{i}', f'wide/{i}.md', '\u754c'*4000, 'a'*64, 'body', 'document', 0, 'now') for i in range(30)])
        db.commit()
    first = read(project)
    assert 0 < len(first['rows']) < 32 and first['next_offset'] is not None
    second = read(project, offset=first['next_offset'], snapshot=first['snapshot'])
    assert len(first['rows']) + len(second['rows']) == 32
