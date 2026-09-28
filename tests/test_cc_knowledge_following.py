"""Operator ledger reports stored evidence without inferring live filesystem state."""
from test_cc_knowledge import project
from test_cc_knowledge_rich_refresh import docx
from test_cc_knowledge_rich_selection import config
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database


def ledger(root):
    return service.status(root)['following']


def test_pending_indexed_missing_and_removed_scope(project):
    service.configure(project, config(['docs/a.docx', 'docs/missing.pdf']))
    assert {r['state'] for r in ledger(project)['rows']} == {'refresh_required'}
    file = project / 'docs/a.docx'
    file.write_bytes(docx('Indexed knowledge'))
    service.reconcile(project)
    rows = ledger(project)['rows']
    assert rows[0]['state'] == 'indexed' and rows[0]['passages'] > 0 and rows[0]['embedded'] == 0
    assert rows[1]['state'] == 'absent_at_last_scan' and rows[1]['id'] is None
    file.unlink()
    # Reading status does not scan or silently declare a saved observation fresh.
    assert ledger(project)['rows'] == rows
    service.reconcile(project)
    assert ledger(project)['rows'][0]['state'] == 'absent_at_last_scan'
    assert ledger(project)['rows'][0]['passages'] == 0
    service.configure(project, service.DEFAULT.copy())
    assert ledger(project)['rows'][0]['state'] == 'not_followed'


def test_dirty_ledger_never_labels_retained_passages_as_current(project):
    (project / 'a.docx').write_bytes(docx('Before'))
    service.configure(project, config(['a.docx']))
    service.reconcile(project)
    service.configure(project, config(['a.docx', 'missing.pdf']))
    row = ledger(project)['rows'][0]
    assert row['state'] == 'refresh_required' and row['revision'] and row['passages'] > 0


def test_scope_ledger_is_bounded_and_read_only(project):
    with database(project) as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                       [(f'r{i}', f'docs/{i:03}.pdf', 'Title', 'a'*64, 'body', 'rich-document', 1, 'now') for i in range(150)])
        db.commit()
        before = db.total_changes
        from cc_memory_lib.knowledge_following import read
        result = read(db)
        assert db.total_changes == before
    assert result['total'] == 150 and len(result['rows']) == 128 and result['limited']
