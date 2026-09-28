"""Streaming format compatibility, corruption boundaries and large histories."""
import hashlib
import json
import time
import tracemalloc

import pytest

from test_cc_knowledge import project
from cc_memory_lib import knowledge_backup as archive
from cc_memory_lib.knowledge_store import database, KnowledgeError


def rewrite(path, value=None, raw=None):
    raw = raw if raw is not None else json.dumps(value, ensure_ascii=False).encode('utf-8')
    path.write_bytes(b'CCMEMORY/1 ' + hashlib.sha256(raw).hexdigest().encode() + b'\n' + raw)


def test_large_history_roundtrip_uses_bounded_python_memory(project, tmp_path):
    body = '\u00e9' * 100000
    with database(project) as db, db:
        source = db.execute('SELECT id FROM sources LIMIT 1').fetchone()[0]
        db.executemany('INSERT INTO revisions VALUES(?,?,?,?)',
                       ((source, f'{i:064x}', body, 'test') for i in range(360)))
        expected = db.execute('SELECT count(*) FROM revisions').fetchone()[0]
    output = tmp_path / 'large.ccmemory'
    target = tmp_path / 'recovered'
    target.mkdir()
    started = time.perf_counter()
    tracemalloc.start()
    try:
        result = archive.backup(project, output)
        assert result['bytes'] > 64 * 1024 * 1024
        assert archive.restore(target, output)['requires_reconcile']
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 16 * 1024 * 1024  # Python allocations, not SQLite/process RSS.
    with database(target) as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == expected
        assert db.execute('SELECT body FROM revisions WHERE revision=?', ('0' * 64,)).fetchone()[0] == body
    print(json.dumps({'archive_bytes': result['bytes'], 'peak_python_bytes': peak,
                      'elapsed_seconds': round(time.perf_counter() - started, 3)}))


def test_reordered_legacy_json_and_unicode_boundaries(project, tmp_path):
    output = tmp_path / 'reordered.ccmemory'
    archive.backup(project, output)
    value = json.loads(output.read_bytes().split(b'\n', 1)[1])
    tables = {name: {'rows': block['rows'], 'columns': block['columns']}
              for name, block in reversed(list(value['tables'].items()))}
    rewrite(output, {'tables': tables, 'created': 'legacy', 'schema': value['schema']})
    target = tmp_path / 'target'
    target.mkdir()
    assert archive.restore(target, output)['restored']


@pytest.mark.parametrize('corruption', ['trailing', 'duplicate', 'truncated', 'row', 'nonfinite'])
def test_stream_corruption_does_not_initialize_target(project, tmp_path, corruption):
    output = tmp_path / 'invalid.ccmemory'
    archive.backup(project, output)
    raw = output.read_bytes().split(b'\n', 1)[1]
    if corruption == 'trailing':
        raw += b' false'
    elif corruption == 'duplicate':
        raw = raw.replace(b'{"schema":1,', b'{"schema":1,"schema":1,', 1)
    elif corruption == 'truncated':
        raw = raw[:-5]
    elif corruption == 'nonfinite':
        raw = raw.replace(b'"schema":1', b'"schema":NaN', 1)
    else:
        value = json.loads(raw)
        value['tables']['sources']['rows'][0].append('extra field')
        raw = json.dumps(value).encode()
    rewrite(output, raw=raw)
    target = tmp_path / 'untouched'
    target.mkdir()
    with pytest.raises((KnowledgeError, ValueError)):
        archive.restore(target, output)
    assert not (target / '.controlcoding').exists()


def test_failed_output_copy_removes_only_owned_incomplete_file(project, tmp_path, monkeypatch):
    output = tmp_path / 'failed.ccmemory'

    def fail(source, target, length):
        target.write(b'partial')
        raise OSError('simulated disk failure')

    monkeypatch.setattr(archive.shutil, 'copyfileobj', fail)
    with pytest.raises(OSError):
        archive.backup(project, output)
    assert not output.exists()
    assert not list(tmp_path.glob('tmp*'))


def test_export_budget_fails_before_output_creation(project, tmp_path, monkeypatch):
    monkeypatch.setattr(archive, 'MAX_BYTES', 512)
    output = tmp_path / 'over-budget.ccmemory'
    with pytest.raises(KnowledgeError, match='backup_limit'):
        archive.backup(project, output)
    assert not output.exists()
