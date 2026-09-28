"""Bounded logical backups; restore never replaces an existing archive.

Only fixed table/column names from our own DDL become SQL. Imported SQL is never
executed, and restored workers, automatic refresh and vector caches are disabled.
"""
import base64
import os
from pathlib import Path
import re
import sqlite3
import shutil
import tempfile

from .knowledge_store import database, location, ordinary, get, put, now, DDL, SCHEMA, KnowledgeError

MAX_BYTES = 2 * 1024 * 1024 * 1024
TABLES = ('meta', 'sources', 'revisions', 'chunks', 'edges', 'wiki', 'wiki_history', 'conversations', 'turns', 'jobs')
# Restore always discards these projections before current-source reconciliation.
# Keep their schema columns for compatibility with existing version-one readers,
# but do not export redundant text or large vectors that cannot be restored as
# current evidence. Canonical records and retained histories remain unchanged.
REBUILDABLE = frozenset(('chunks', 'edges'))


def archive_path(root, value, reading=False):
    from cc_setup_service import _root
    path = _root(str(value))
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,160}\.ccmemory', path.name)
            or re.match(r'(?i)^(con|prn|aux|nul|com\d|lpt\d)\.', path.name)):
        raise KnowledgeError('invalid_backup_path')
    for parent in [path.parent, *path.parent.parents]:
        ordinary(parent, True)
    path = path.parent.resolve(strict=True) / path.name
    if path.is_relative_to(Path(root).resolve(strict=True)):
        raise KnowledgeError('backup_must_be_external')
    if reading:
        if ordinary(path).st_size > MAX_BYTES:
            raise KnowledgeError('backup_limit')
    elif path.exists() or path.is_symlink():
        raise KnowledgeError('backup_already_exists')
    return path


def encode(value):
    return {'bytes': base64.b64encode(value).decode('ascii')} if isinstance(value, bytes) else value


def decode(value):
    if type(value) is dict and set(value) == {'bytes'} and type(value['bytes']) is str:
        return base64.b64decode(value['bytes'], validate=True)
    if value is None or type(value) in (str, int, float):
        return value
    raise KnowledgeError('invalid_backup_value')


def backup(root, output):
    from .knowledge_archive_stream import write_payload
    from .knowledge_consolidation_store import TABLES as V2_TABLES, V3_TABLES, schema, validate_storage
    output = archive_path(root, output)
    # Stage beside the explicitly selected output, not in the project or RAM.
    with tempfile.TemporaryFile(dir=output.parent) as staged:
        with database(root) as db:
            if db is None or get(db, 'policy') is None:
                raise KnowledgeError('knowledge_not_enabled')
            version = schema(db)
            if version in (2, 3):
                validate_storage(db)
            tables = TABLES + V2_TABLES + (V3_TABLES if version == 3 else ()) if version in (2, 3) else TABLES
            sha, size = write_payload(db, staged, tables, REBUILDABLE, version, now(), encode, MAX_BYTES - 256)
        header = ('CCMEMORY/' + str(version) + ' ' + sha + '\n').encode('ascii')
        staged.seek(0)
        owned = None
        try:
            with open(output, 'xb') as stream:
                info = os.fstat(stream.fileno())
                owned = (info.st_dev, info.st_ino)
                stream.write(header)
                shutil.copyfileobj(staged, stream, 65536)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # Remove only the file this operation created, never a replacement.
            if owned is not None:
                try:
                    current = output.lstat()
                    if (current.st_dev, current.st_ino) == owned:
                        output.unlink()
                except OSError:
                    pass
            raise
    return {'saved': True, 'bytes': size + len(header), 'sha256': sha, 'path': str(output)}


def restore(root, source):
    source = archive_path(root, source, True)
    from .knowledge_archive_stream import read_payload
    # SQLite owns a disposable temporary database; no candidate rows accumulate
    # in a Python list or an in-memory SQLite database. Input SQL is never used.
    from .knowledge_consolidation_store import (TABLES as V2_TABLES, V3_TABLES, DDL_STATEMENTS, V3_DDL_STATEMENTS,
                                                 validate_storage)
    candidate = sqlite3.connect('')
    candidate.row_factory = sqlite3.Row
    try:
        candidate.execute('PRAGMA cache_size=-2048')
        candidate.execute('PRAGMA temp_store=FILE')
        with open(source, 'rb') as stream:
            header = stream.readline(90)
            match = re.fullmatch(rb'CCMEMORY/([123]) ([a-f0-9]{64})\n', header)
            if not match:
                raise KnowledgeError('backup_integrity_failed')
            version = int(match.group(1))
            candidate.executescript(DDL)
            if version in (2, 3):
                for statement in DDL_STATEMENTS:
                    candidate.execute(statement)
                if version == 3:
                    for statement in V3_DDL_STATEMENTS:
                        candidate.execute(statement)
                candidate.execute('PRAGMA user_version=' + str(version))
            tables = TABLES + V2_TABLES + (V3_TABLES if version == 3 else ()) if version in (2, 3) else TABLES
            sha = read_payload(stream, candidate, tables, version, decode, MAX_BYTES - len(header))
            if header != ('CCMEMORY/' + str(version) + ' ' + sha + '\n').encode('ascii'):
                raise KnowledgeError('backup_integrity_failed')
        # Reject an older oversized review state before installing any target
        # archive; the source backup stays untouched for a later migration.
        from .knowledge_wiki_review import validate_budget
        validate_budget(candidate)
        if version in (2, 3):
            validate_storage(candidate)
        from .knowledge_service import policy
        config = policy(get(candidate, 'policy'))
        put(candidate, 'policy', {**config, 'automatic': False, 'worker': False})
        put(candidate, 'needs_reconcile', True)
        put(candidate, 'embedding_identity', None)
        put(candidate, 'worker_heartbeat', None)
        # Imported cache content is never evidence about current original files.
        # Reconciliation must recapture every source, even when its stored hash
        # happens to match. Human notes and historical revisions are retained.
        for row in candidate.execute('SELECT id,notes FROM wiki'):
            put(candidate, 'wiki_notes:' + row['id'], row['notes'])
        candidate.execute('UPDATE sources SET deleted=1')
        candidate.execute('DELETE FROM chunks')
        candidate.execute('DELETE FROM edges')
        from .knowledge_staging import clear
        clear(candidate)
        from .knowledge_scan import clear as clear_scan
        clear_scan(candidate)
        candidate.execute("UPDATE jobs SET state='pending',error='restored' WHERE state='running'")
        if version in (2, 3):
            candidate.execute("UPDATE consolidation_jobs SET state='stale',error=? WHERE state!='purged'",
                              ('{"code":"restored"}',))
            candidate.execute("UPDATE consolidation_proposals SET status='stale'")
            candidate.execute("UPDATE memory_claims SET state='stale'")
            candidate.execute("DELETE FROM meta WHERE key LIKE 'consolidation-lease/%' OR key LIKE 'consolidation_lease/%'")
            if version == 3:
                candidate.execute("UPDATE consolidation_attempts SET state='interrupted',owner=NULL,lease_until=NULL,packet='{}',error='restored',outcome=NULL WHERE state!='purged'")
                candidate.execute("DELETE FROM consolidation_queue")
                candidate.execute("DELETE FROM consolidation_episode_budget")
                candidate.execute('DELETE FROM consolidation_progress')
                candidate.execute('DELETE FROM consolidation_passage_progress')
                put(candidate, 'consolidation_settings', None)
        put(candidate, 'restore_receipt', {'at': now(), 'backup_sha256': sha})
        candidate.commit()
        directory = location(root)
        if directory and ((directory / 'knowledge.db').exists() or (directory / 'knowledge.db').is_symlink()):
            raise KnowledgeError('restore_requires_empty_archive')
        with database(root, create=True) as target:
            # Recheck under the writer lease, guarding competing initialization.
            if target.execute('SELECT 1 FROM meta').fetchone() or target.execute('SELECT 1 FROM sources').fetchone():
                raise KnowledgeError('restore_requires_empty_archive')
            with target:
                if version in (2, 3):
                    for statement in DDL_STATEMENTS:
                        target.execute(statement)
                    if version == 3:
                        for statement in V3_DDL_STATEMENTS:
                            target.execute(statement)
                for table in tables:
                    columns = [r['name'] for r in candidate.execute('PRAGMA table_info(' + table + ')')]
                    target.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in columns) + ')',
                                       candidate.execute('SELECT * FROM ' + table))
                if version in (2, 3):
                    target.execute('PRAGMA user_version=' + str(version))
        return {'restored': True, 'requires_reconcile': True,
                'notice': 'Original source files are not in this archive. Automatic updates and workers are disabled.'}
    finally:
        candidate.close()
