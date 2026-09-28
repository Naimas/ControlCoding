"""Bounded, coherent approved-source snapshots and derived passage identities."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from cc_setup_service import _Snapshots, _root
from cc_layout import source_path
from cc_documentation_observer import references
from .knowledge_store import KnowledgeError

SCOPES = ('project', 'work', 'plans', 'handoffs', 'dev-views', 'dev-memory', 'rich-documents')
LIMITS = {'files': 5000, 'sources': 6144, 'conversations': 1000, 'file_bytes': 262144, 'total_bytes': 128 * 1024 * 1024,
          'chunks': 50000, 'entries': 60000, 'seconds': 30}

_EXTERNAL_SNAPSHOT = ContextVar('cc_external_snapshot', default=False)


@contextmanager
def external_snapshot():
    """External facade binds original content hashes; projection has no Git HEAD."""
    token = _EXTERNAL_SNAPSHOT.set(True)
    try:
        yield
    finally:
        _EXTERNAL_SNAPSHOT.reset(token)


@dataclass(frozen=True)
class KnowledgeReadPolicy:
    """Fixed coordinator budget; does not expand the setup service's policy."""
    file_bytes: int = LIMITS['file_bytes']
    target_bytes: int = LIMITS['total_bytes']
    trusted_bytes: int = 1024
    input_count: int = 1536


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def physical_source_path(root, relative, kind=''):
    """Return a project-relative physical locator while retaining logical IDs."""
    if kind == 'conversation' or str(relative).startswith('dev-memory/'):
        return ''
    try:
        label = str(relative).replace('\\', '/')
        if label.split('/', 1)[0] in ('.controlcoding', '.controlwork'):
            physical = source_path(root, label)
        else:
            # Root-level documents belong to the adopter. Contained CC copies
            # have their own explicit `cc/<name>` logical source identity.
            physical = Path(root).resolve() / label
        return physical.relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return ''


def capture(root, scopes, db=None, rich_paths=None, ocr_records=None):
    from .knowledge_scan import capture as acquire
    return acquire(root, scopes, db, rich_paths=rich_paths, ocr_records=ocr_records)


def passages(source):
    lines = source['body'].splitlines()
    # Hard line splitting bounds provider input even for minified/long paragraphs.
    start, buffer, size, sequence = 1, [], 0, 0
    for number, line in enumerate(lines, 1):
        for offset in range(0, max(1, len(line)), 1600):
            piece = line[offset:offset + 1600]
            if buffer and size + len(piece) > 1800:
                text = '\n'.join(buffer)
                yield {'id': digest(source['id'] + source['revision'] + str(sequence) + text),
                       'source': source['id'], 'revision': source['revision'],
                       'line': start, 'end_line': number, 'text': text}
                buffer, size, start = [], 0, number
                sequence += 1
            buffer.append(piece)
            size += len(piece) + 1
    if buffer:
        text = '\n'.join(buffer)
        yield {'id': digest(source['id'] + source['revision'] + str(sequence) + text),
               'source': source['id'], 'revision': source['revision'],
               'line': start, 'end_line': max(1, len(lines)), 'text': text}


def head(root):
    if _EXTERNAL_SNAPSHOT.get():
        return None
    # No hooks run; no user-supplied command or revision expression.
    import subprocess
    try:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                                capture_output=True, timeout=3, check=False,
                                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        text = result.stdout.decode('ascii').strip()
        return text if result.returncode == 0 and re.fullmatch(r'[a-f0-9]{40,64}', text) else None
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None
