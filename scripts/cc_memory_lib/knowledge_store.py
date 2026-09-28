"""Embedded knowledge coordinator storage; portable Work records remain read-only.

One OS-owned lock serializes writers, including workers and desktop processes.
SQLite's rollback journal commits sources, wiki and index as one generation.
Ordinary local files only; hostile concurrent mount replacement is out of scope.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import stat
import time

SCHEMA = 1
SUPPORTED_SCHEMAS = (1, 2, 3)
MAX_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
INDEXES = (
    'CREATE INDEX IF NOT EXISTS cc_chunks_source ON chunks(source)',
    'CREATE INDEX IF NOT EXISTS cc_chunks_pending ON chunks(id) WHERE vector IS NULL',
)
DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,
 title TEXT NOT NULL,revision TEXT NOT NULL,body TEXT NOT NULL,kind TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(source TEXT,revision TEXT,body TEXT NOT NULL,
 observed TEXT NOT NULL,PRIMARY KEY(source,revision));
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,source TEXT NOT NULL,
 revision TEXT NOT NULL,line INTEGER NOT NULL,end_line INTEGER NOT NULL,text TEXT NOT NULL,
 vector TEXT,model TEXT);
CREATE TABLE IF NOT EXISTS edges(source TEXT,target TEXT,revision TEXT,kind TEXT,
 PRIMARY KEY(source,target,kind));
CREATE TABLE IF NOT EXISTS wiki(id TEXT PRIMARY KEY,title TEXT,body TEXT,revision TEXT,
 dependencies TEXT,notes TEXT NOT NULL DEFAULT '',updated TEXT);
CREATE TABLE IF NOT EXISTS wiki_history(id TEXT,revision TEXT,body TEXT,dependencies TEXT,
 updated TEXT,PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,title TEXT,retention TEXT,
 summary TEXT NOT NULL DEFAULT '',status TEXT,updated TEXT);
CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,conversation TEXT,sequence INTEGER,
 role TEXT,content TEXT,provenance TEXT,created TEXT,
 UNIQUE(conversation,sequence));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,
 state TEXT,reason TEXT,created TEXT,finished TEXT,error TEXT);
"""


class KnowledgeError(ValueError):
    pass


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ordinary(path, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
            or (directory and not stat.S_ISDIR(info.st_mode))):
        raise KnowledgeError('unsupported_path')
    return info


def location(root, create=False):
    from cc_setup_service import _root
    root = _root(str(root))
    for path in [root / '.controlcoding', root / '.controlcoding' / 'knowledge']:
        if create:
            try:
                path.mkdir()
            except FileExistsError:
                pass
        if not path.exists():
            if path.is_symlink():
                raise KnowledgeError('unsupported_path')
            return None
        ordinary(path, True)
    return path


@contextmanager
def database(root, create=False):
    directory = location(root, create)
    if directory is None:
        yield None
        return
    dbpath = directory / 'knowledge.db'
    if not create and not dbpath.exists():
        yield None
        return
    lockpath = directory / 'writer.lock'
    if lockpath.exists() or lockpath.is_symlink():
        ordinary(lockpath)
    fd = os.open(lockpath, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    locked = False
    connection = None
    try:
        ordinary(lockpath)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise KnowledgeError('knowledge_busy') from exc
        for name in ['knowledge.db', 'knowledge.db-journal', 'knowledge.db-wal', 'knowledge.db-shm']:
            p = directory / name
            if p.exists() or p.is_symlink():
                ordinary(p)
        if dbpath.exists() and dbpath.stat().st_size > MAX_DATABASE_BYTES:
            raise KnowledgeError('knowledge_storage_limit')
        connection = sqlite3.connect(str(dbpath), timeout=2)
        connection.row_factory = sqlite3.Row
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        connection.execute('PRAGMA max_page_count=' + str(max(1, MAX_DATABASE_BYTES // page_size)))
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, *SUPPORTED_SCHEMAS):
            raise KnowledgeError('unsupported_knowledge_schema')
        if version == 0:
            if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise KnowledgeError('unknown_knowledge_database')
            connection.executescript(DDL)
            connection.execute('PRAGMA user_version=1')
            connection.commit()
        connection.execute('PRAGMA synchronous=FULL')
        # Rebuildable additive indexes; existing version-one archives and older
        # readers remain compatible. DDL is fixed locally, never archive SQL.
        for statement in INDEXES:
            connection.execute(statement)
        connection.commit()
        yield connection
    except sqlite3.Error as exc:
        if getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
            raise KnowledgeError('knowledge_storage_limit') from exc
        raise
    finally:
        if connection is not None:
            connection.close()  # Any unfinished transaction rolls back.
        if locked:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def get(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value)))


@contextmanager
def worker_lease(root):
    directory = location(root)
    if directory is None:
        raise KnowledgeError('knowledge_not_enabled')
    lock = directory / 'worker.lock'
    if lock.exists() or lock.is_symlink():
        ordinary(lock)
    with open(lock, 'a+b') as stream:
        ordinary(lock)
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        locked = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        finally:
            if locked and os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
