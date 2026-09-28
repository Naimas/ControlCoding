"""Explicit version-two storage for reviewed, project-local memory consolidation.

Opening an archive never upgrades it. ``migrate`` creates and verifies a new
external version-one backup before entering the migration transaction.
"""
import hashlib
import json
import sqlite3

from .knowledge_store import KnowledgeError, database, location


TABLES = (
    'consolidation_jobs', 'consolidation_inputs', 'consolidation_proposals',
    'memory_claims', 'memory_claim_revisions', 'consolidation_events',
)
DDL_STATEMENTS = (
    'CREATE TABLE consolidation_jobs(id TEXT PRIMARY KEY,created TEXT,updated TEXT,state TEXT,binding TEXT,manifest TEXT,error TEXT,receipt TEXT)',
    'CREATE TABLE consolidation_inputs(job TEXT,source TEXT,revision TEXT,PRIMARY KEY(job,source))',
    'CREATE TABLE consolidation_proposals(id TEXT PRIMARY KEY,job TEXT,ordinal INTEGER,kind TEXT,target TEXT,base_revision TEXT,title TEXT,body TEXT,dependencies TEXT,reason TEXT,status TEXT,created TEXT,decided TEXT)',
    'CREATE TABLE memory_claims(id TEXT PRIMARY KEY,target TEXT,kind TEXT,title TEXT,body TEXT,revision TEXT,dependencies TEXT,state TEXT,updated TEXT)',
    'CREATE TABLE memory_claim_revisions(id TEXT,revision TEXT,target TEXT,kind TEXT,title TEXT,body TEXT,dependencies TEXT,state TEXT,updated TEXT,PRIMARY KEY(id,revision))',
    'CREATE TABLE consolidation_events(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,kind TEXT,payload TEXT,created TEXT)',
)
DDL = ';\n'.join(DDL_STATEMENTS) + ';'
V3_TABLES = ('consolidation_attempts', 'consolidation_queue', 'consolidation_episode_budget', 'consolidation_progress', 'consolidation_passage_progress',
             'consolidation_proposal_metadata', 'consolidation_claim_metadata')
V3_DDL_STATEMENTS = (
    'CREATE TABLE consolidation_progress(source TEXT PRIMARY KEY,revision TEXT NOT NULL,analyzed TEXT NOT NULL)',
    'CREATE TABLE consolidation_passage_progress(chunk TEXT PRIMARY KEY,source TEXT NOT NULL,revision TEXT NOT NULL,analyzed TEXT NOT NULL)',
    'CREATE INDEX consolidation_passage_progress_source ON consolidation_passage_progress(source)',
    'CREATE TABLE consolidation_attempts(request_id TEXT PRIMARY KEY,job TEXT NOT NULL,ordinal INTEGER NOT NULL,mode TEXT NOT NULL,packet TEXT NOT NULL,packet_digest TEXT NOT NULL,state TEXT NOT NULL,owner TEXT,lease_until TEXT,started TEXT,finished TEXT,outcome TEXT,result_digest TEXT,error TEXT,usage TEXT)',
    'CREATE TABLE consolidation_queue(id TEXT PRIMARY KEY,trigger TEXT NOT NULL,dedup TEXT NOT NULL,state TEXT NOT NULL,created TEXT NOT NULL,job TEXT)',
    'CREATE INDEX consolidation_attempts_job ON consolidation_attempts(job,ordinal)',
    'CREATE INDEX consolidation_queue_dedup ON consolidation_queue(dedup,state)',
    'CREATE TABLE consolidation_episode_budget(prefix TEXT PRIMARY KEY,used INTEGER NOT NULL,closed INTEGER NOT NULL,updated TEXT NOT NULL)',
    'CREATE TABLE consolidation_proposal_metadata(proposal TEXT PRIMARY KEY,epistemic_status TEXT NOT NULL,scope TEXT NOT NULL,conflicting TEXT NOT NULL,prerequisites TEXT NOT NULL)',
    'CREATE TABLE consolidation_claim_metadata(claim_id TEXT PRIMARY KEY,epistemic_status TEXT NOT NULL,scope TEXT NOT NULL)',
)
MAX_JSON_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_EVENT_ROWS = 100000
JSON_FIELDS = {
    'consolidation_jobs': {'binding': dict, 'manifest': dict, 'error': (dict, type(None)),
                           'receipt': (dict, list, type(None))},
    'consolidation_proposals': {'dependencies': list},
    'memory_claims': {'dependencies': list},
    'memory_claim_revisions': {'dependencies': list},
    'consolidation_events': {'payload': (dict, list, type(None))},
}


def schema(db):
    """Return SQLite's archive schema version, independent of status protocol."""
    return db.execute('PRAGMA user_version').fetchone()[0]


def validate_storage(db):
    """Reject malformed or oversized version-two rows before restore/publication.

    This is the storage shape/size gate; publication checks evidence semantics.
    """
    if schema(db) not in (2, 3):
        raise KnowledgeError('unsupported_knowledge_schema')
    for table in TABLES:
        rows = db.execute('SELECT * FROM ' + table)
        for row in rows:
            record = dict(row)
            for name, value in record.items():
                if value is None:
                    continue
                if name == 'ordinal' or (table == 'consolidation_events' and name == 'id'):
                    if type(value) is not int or value < 0:
                        raise KnowledgeError('invalid_consolidation_storage')
                    continue
                if type(value) is not str or len(value.encode('utf-8')) > MAX_TEXT_BYTES:
                    raise KnowledgeError('invalid_consolidation_storage')
                expected = JSON_FIELDS.get(table, {}).get(name)
                if expected is not None:
                    if len(value.encode('utf-8')) > MAX_JSON_BYTES:
                        raise KnowledgeError('consolidation_storage_limit')
                    try:
                        decoded = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    except (ValueError, TypeError) as exc:
                        raise KnowledgeError('invalid_consolidation_storage') from exc
                    if not isinstance(decoded, expected):
                        raise KnowledgeError('invalid_consolidation_storage')
        if table == 'consolidation_events':
            count = db.execute('SELECT COUNT(*) FROM consolidation_events').fetchone()[0]
            if count > MAX_EVENT_ROWS:
                raise KnowledgeError('consolidation_storage_limit')
    from .knowledge_consolidation_validation import validate
    validate(db)
    if schema(db) == 3:
        from .knowledge_consolidation_execution_validation import validate as validate_execution
        validate_execution(db)


def _archive_digest(root):
    """Digest the complete archive files while the writer lease is held."""
    directory = location(root)
    if directory is None:
        raise KnowledgeError('knowledge_not_enabled')
    digest = hashlib.sha256()
    for name in ('knowledge.db', 'knowledge.db-wal'):
        path = directory / name
        if not path.exists():
            continue
        digest.update(name.encode('ascii') + b'\0')
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
    return digest.hexdigest()


def _verify_backup(root, backup_path):
    """Read the just-created backup into disposable SQLite with fixed v1 DDL."""
    from .knowledge_archive_stream import read_payload
    from .knowledge_backup import MAX_BYTES, TABLES as V1_TABLES, archive_path, decode
    from .knowledge_store import DDL as V1_DDL
    import re

    path = archive_path(root, backup_path, True)
    candidate = sqlite3.connect('')
    candidate.row_factory = sqlite3.Row
    try:
        candidate.executescript(V1_DDL)
        with path.open('rb') as stream:
            header = stream.readline(90)
            if not re.fullmatch(rb'CCMEMORY/1 [a-f0-9]{64}\n', header):
                raise KnowledgeError('backup_integrity_failed')
            sha = read_payload(stream, candidate, V1_TABLES, 1, decode, MAX_BYTES - len(header))
            if header != b'CCMEMORY/1 ' + sha.encode('ascii') + b'\n':
                raise KnowledgeError('backup_integrity_failed')
        return candidate, sha
    except BaseException:
        candidate.close()
        raise


def _same_logical_archive(db, backup_db):
    """Compare every exported v1 row, including stable metadata and histories."""
    from itertools import zip_longest
    from .knowledge_backup import REBUILDABLE, TABLES as V1_TABLES
    marker = object()
    for table in V1_TABLES:
        if table in REBUILDABLE:
            continue
        condition = (" WHERE key NOT GLOB 'ingest-stage/*' AND key NOT GLOB 'source-scan/*'"
                     if table == 'meta' else '')
        current = db.execute('SELECT * FROM ' + table + condition)
        saved = backup_db.execute('SELECT * FROM ' + table)
        for left, right in zip_longest(current, saved, fillvalue=marker):
            if left is marker or right is marker or tuple(left) != tuple(right):
                return False
    return True


def migrate(root, backup_path):
    """Create a fresh verified external v1 backup, then atomically upgrade to v2.

    ``backup_path`` must not exist. A full file digest guards writes to tables
    omitted from the logical backup, including rebuildable caches.
    """
    from .knowledge_backup import archive_path, backup
    from .knowledge_store import get

    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        version = schema(db)
        if version == 3:
            return {'migrated': False, 'schema': 3}
        if version not in (1, 2):
            raise KnowledgeError('unsupported_knowledge_schema')
        archive_path(root, backup_path)  # Reject existing/in-project output up front.
        before = _archive_digest(root)
    receipt = backup(root, backup_path)
    candidate, backup_sha = _verify_backup(root, backup_path) if version == 1 else _verify_v2_backup(root, backup_path)
    try:
        with database(root) as db:
            if db is None or schema(db) != version:
                raise KnowledgeError('knowledge_schema_changed')
            if _archive_digest(root) != before or not _same_logical_archive(db, candidate):
                raise KnowledgeError('knowledge_changed_since_backup')
            try:
                db.execute('BEGIN IMMEDIATE')
                if version == 1:
                    for statement in DDL_STATEMENTS:
                        db.execute(statement)
                for statement in V3_DDL_STATEMENTS:
                    db.execute(statement)
                db.execute('PRAGMA user_version=3')
                db.commit()
            except BaseException:
                db.rollback()
                raise
    finally:
        candidate.close()
    return {'migrated': True, 'schema': 3, 'archive_sha256': before,
            'backup_sha256': backup_sha, 'backup': receipt['path']}


def _verify_v2_backup(root, backup_path):
    """Read and compare the complete version-two logical backup before upgrade."""
    from .knowledge_archive_stream import read_payload
    from .knowledge_backup import MAX_BYTES, TABLES as V1_TABLES, archive_path, decode
    from .knowledge_store import DDL as V1_DDL
    import re

    path = archive_path(root, backup_path, True)
    candidate = sqlite3.connect('')
    candidate.row_factory = sqlite3.Row
    try:
        candidate.executescript(V1_DDL)
        for statement in DDL_STATEMENTS:
            candidate.execute(statement)
        candidate.execute('PRAGMA user_version=2')
        with path.open('rb') as stream:
            header = stream.readline(90)
            if not re.fullmatch(rb'CCMEMORY/2 [a-f0-9]{64}\n', header):
                raise KnowledgeError('backup_integrity_failed')
            sha = read_payload(stream, candidate, V1_TABLES + TABLES, 2, decode, MAX_BYTES - len(header))
            if header != b'CCMEMORY/2 ' + sha.encode('ascii') + b'\n':
                raise KnowledgeError('backup_integrity_failed')
        if not _same_logical_archive_v2(root, candidate):
            raise KnowledgeError('knowledge_changed_since_backup')
        return candidate, sha
    except BaseException:
        candidate.close()
        raise


def _same_logical_archive_v2(root, candidate):
    from itertools import zip_longest
    from .knowledge_backup import TABLES as V1_TABLES, REBUILDABLE
    marker = object()
    with database(root) as db:
        for table in V1_TABLES + TABLES:
            if table in REBUILDABLE:
                continue
            condition = (" WHERE key NOT GLOB 'ingest-stage/*' AND key NOT GLOB 'source-scan/*'"
                         if table == 'meta' else '')
            for left, right in zip_longest(db.execute('SELECT * FROM ' + table + condition),
                                           candidate.execute('SELECT * FROM ' + table), fillvalue=marker):
                if left is marker or right is marker or tuple(left) != tuple(right):
                    return False
    return True
