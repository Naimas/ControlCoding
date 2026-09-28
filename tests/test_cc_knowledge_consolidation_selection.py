"""Successful batches advance exact original passages without scan-order loss."""
import json

import pytest

from test_cc_knowledge import project, record
from test_cc_knowledge_consolidation_execution import CONFIG, prepare, reply
from cc_memory_lib import knowledge_service as service, knowledge_backup as backups
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import KnowledgeError, database


@pytest.fixture
def ready(project, tmp_path):
    migrate(project, tmp_path / 'before.ccmemory')
    return project


def complete(root, packet, analyzed=None):
    return service.dispatch(root, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'text': reply(packet, analyzed=analyzed)})


def progress(root):
    return service.dispatch(root, 'consolidation-view')['progress']


def test_advances_beyond_first_twenty_and_edits_only_reopen_changed_source(ready):
    for i in range(27):
        (ready / 'docs' / f'file-{i:02}.md').write_text(f'# Topic {i}\nEvidence {i}.', encoding='utf-8')
    service.reconcile(ready)
    seen = set()
    while progress(ready)['eligible']:
        packet = prepare(ready)
        ids = {s['source'] for s in packet['input_manifest']}
        assert not ids & seen
        seen.update(ids)
        complete(ready, packet)
    assert len(seen) == 29
    assert progress(ready)['analyzed'] == 29
    with pytest.raises(KnowledgeError, match='consolidation_no_new_sources'):
        prepare(ready)
    (ready / 'docs/file-26.md').write_text('# Topic 26\nChanged decision.', encoding='utf-8')
    service.reconcile(ready)
    packet = prepare(ready)
    assert len(packet['input_manifest']) == 1
    assert packet['input_manifest'][0]['path'] == 'docs/file-26.md'
    assert progress(ready)['analyzed'] == 28


def test_full_long_passages_are_not_skipped_or_truncated(ready):
    (ready / 'docs/design.md').write_text('# Long source\n' + '\n'.join(
        f'Unique passage {i}: ' + ('significant evidence ' * 55) for i in range(6)), encoding='utf-8')
    service.reconcile(ready)
    with database(ready) as db:
        chunks = {tuple(r) for r in db.execute('SELECT source,revision,line,end_line,text FROM chunks')}
    observed = set()
    while progress(ready)['pending_passages']:
        packet = prepare(ready)
        for item in packet['input_manifest']:
            original = tuple(item[k] for k in ('source', 'revision', 'line', 'end_line', 'excerpt'))
            assert original in chunks and original not in observed
            observed.add(original)
        complete(ready, packet)
    assert observed == chunks
    assert progress(ready)['analyzed'] == 2
    assert progress(ready)['analyzed_passages'] == len(chunks)


def test_failed_zero_coverage_and_overlapping_jobs_do_not_skip_passages(ready):
    packet = prepare(ready)
    same = prepare(ready)
    assert packet['input_manifest'] == same['input_manifest']
    complete(ready, packet, analyzed=0)
    assert progress(ready)['analyzed_passages'] == 0
    complete(ready, same, analyzed=1)
    assert progress(ready)['analyzed_passages'] == 1
    failed = prepare(ready)
    service.dispatch(ready, 'consolidation-fail', {
        'job': failed['job'], 'request_id': failed['request_id'],
        'code': 'provider_timeout', 'message': 'Controlled fixture timeout'})
    assert progress(ready)['analyzed_passages'] == 1
    next_packet = prepare(ready)
    assert next_packet['input_manifest'] == failed['input_manifest']
    complete(ready, next_packet)
    assert progress(ready)['pending_passages'] == 0


def test_backup_restores_with_progress_disarmed_and_privacy_erases_ledger(ready, tmp_path):
    service.conversation(ready, {**record(), 'summary': 'Private decision retained for consolidation.'})
    service.reconcile(ready)
    packet = prepare(ready)
    complete(ready, packet)
    path = tmp_path / 'after.ccmemory'
    backups.backup(ready, path)
    target = tmp_path / 'restore'
    target.mkdir()
    backups.restore(target, path)
    with database(target) as db:
        assert not db.execute('SELECT * FROM consolidation_progress').fetchall()
        assert not db.execute('SELECT * FROM consolidation_passage_progress').fetchall()
    with database(ready) as db:
        private = db.execute("SELECT id FROM sources WHERE kind='conversation'").fetchone()[0]
        assert db.execute('SELECT 1 FROM consolidation_passage_progress WHERE source=?', (private,)).fetchone()
    service.dispatch(ready, 'forget', 'chat-1')
    with database(ready) as db:
        assert not db.execute('SELECT 1 FROM consolidation_progress WHERE source=?', (private,)).fetchone()
        assert not db.execute('SELECT 1 FROM consolidation_passage_progress WHERE source=?', (private,)).fetchone()
    backups.backup(ready, tmp_path / 'purged.ccmemory')


def test_empty_originals_are_excluded_from_completed_source_count(ready):
    (ready / 'docs/empty.md').write_text(' \n\t', encoding='utf-8')
    service.reconcile(ready)
    value = progress(ready)
    assert value['inventory'] == 3 and value['excluded'] == 1
    assert value['analyzed'] == 0 and value['eligible'] == 2


def test_unicode_packet_preserves_exact_passages(ready):
    (ready / 'docs/design.md').write_text('# 設計\n' + '根拠と決定。' * 300, encoding='utf-8')
    service.reconcile(ready)
    packet = prepare(ready)
    assert len(json.dumps(packet).encode()) < 60 * 1024
    with database(ready) as db:
        for item in packet['input_manifest']:
            assert db.execute('SELECT 1 FROM chunks WHERE source=? AND revision=? AND text=?',
                              (item['source'], item['revision'], item['excerpt'])).fetchone()
    complete(ready, packet)


def test_continuations_reuse_current_wiki_but_source_edits_recompile(ready, monkeypatch):
    from cc_memory_lib import knowledge_wiki_review as wiki
    original, calls = wiki.compile_pages, []
    def counted(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(wiki, 'compile_pages', counted)
    first = prepare(ready)
    assert len(calls) == 1
    with database(ready) as db:
        before = [tuple(row) for row in db.execute('SELECT id,revision FROM wiki ORDER BY id')]
    prepare(ready)
    assert len(calls) == 1
    with database(ready) as db:
        assert before == [tuple(row) for row in db.execute('SELECT id,revision FROM wiki ORDER BY id')]
    (ready / 'docs/design.md').write_text('# Changed design\nA new process decision.', encoding='utf-8')
    service.reconcile(ready)
    assert len(calls) == 2
    prepare(ready)
    assert len(calls) == 2
