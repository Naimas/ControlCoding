"""Request-local accounting and endpoint binding for canonical progress reads.

The context is dormant outside progress. It owns no project files or global cache.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
import time

ACTIVE = ContextVar('cc_progress_observation', default=None)
MAX_SECONDS = 45
MAX_ENTRIES = 10000
MAX_BYTES = 256 * 1024 * 1024


class ObservationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def current():
    return ACTIVE.get()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def info_binding(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            stat.S_IFMT(info.st_mode), info.st_mode & 0o111, info.st_nlink]


class Observation:
    def __init__(self):
        self.started = time.monotonic()
        self.deadline = self.started + MAX_SECONDS
        self.observed_at = stamp()
        self.read_entries = 0
        self.input_bytes = 0
        self.attempts = 0
        self.exhausted = False
        self.verifying = False
        self.reset_attempt()

    def reset_attempt(self):
        self.transcript = {}
        self.facts = {}
        self.callbacks = {}
        self.memo = {}
        self.changed = False
        self.state = None
        self.revision = None
        self.project_path = None
        self.valid_until = None

    def check(self):
        if (self.exhausted or time.monotonic() >= self.deadline
                or self.read_entries > MAX_ENTRIES or self.input_bytes > MAX_BYTES):
            self.exhausted = True
            raise ObservationError('progress_limits_exhausted')

    def entry(self, count=1):
        self.read_entries += count
        self.check()

    def consume(self, count):
        self.input_bytes += count
        self.check()

    def remaining(self, owner_timeout=None):
        self.check()
        remaining = self.deadline - time.monotonic()
        return remaining if owner_timeout is None else min(owner_timeout, remaining)

    def record(self, root, relative, kind, value, bounds):
        key = (os.path.normcase(os.path.abspath(root)), relative, kind, *bounds)
        if key in self.transcript and self.transcript[key] != value:
            self.changed = True
        if not self.verifying:
            self.transcript.setdefault(key, value)

    def file(self, root, relative, item, bounds):
        value = None if item is None else [hashlib.sha256(item[0]).hexdigest(), info_binding(item[1])]
        self.record(root, relative, 'file', value, bounds)

    def listing(self, root, relative, rows, bounds):
        self.record(root, relative, 'directory', sorted([name, info_binding(info)] for name, info in rows), bounds)

    def failure(self, root, relative, kind, error, bounds):
        value = ['error', type(error).__name__, getattr(error, 'code', None)]
        self.record(root, relative, kind, value, bounds)

    def fact(self, key, value, callback=None):
        key = str(key)
        if key in self.facts and self.facts[key] != value:
            self.changed = True
        if not self.verifying:
            self.facts.setdefault(key, value)
            if callback is not None:
                self.callbacks.setdefault(key, callback)

    def expires(self, value):
        if value is not None:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ObservationError('progress_evidence_unreadable')
            if self.valid_until is None or parsed < datetime.fromisoformat(self.valid_until):
                self.valid_until = parsed.astimezone(timezone.utc).isoformat()

    def recheck(self):
        from cc_evidence_inputs import SafeRoot, Budget, EvidenceError
        self.check()
        self.verifying = True
        try:
            confirmed={}
            for (root, relative, kind, per_file, total, entries), expected in list(self.transcript.items()):
                self.check()
                failed=isinstance(expected,list) and expected and expected[0]=='error'
                key=(root,relative,kind)
                if not failed and key in confirmed:
                    actual=confirmed[key]
                    if actual is not None and kind=='file' and actual[1][2]>min(per_file,total):
                        self.changed=True
                    elif kind=='directory' and len(actual)>entries:
                        self.changed=True
                    elif actual!=expected:
                        self.changed=True
                    continue
                read_error=False
                try:
                    with SafeRoot(root) as safe:
                        timed_out = isinstance(expected,list) and expected and expected[0]=='error' and expected[-1]=='snapshot_timeout'
                        budget=Budget(per_file=per_file,total=total,entries=entries,seconds=-1 if timed_out else MAX_SECONDS)
                        if kind == 'file':
                            failed = isinstance(expected, list) and expected and expected[0] == 'error'
                            item = safe.read(relative, budget, missing=not failed)
                            actual = None if item is None else [hashlib.sha256(item[0]).hexdigest(), info_binding(item[1])]
                        else:
                            rows = safe.listing(relative, budget)
                            actual = sorted([name, info_binding(info)] for name, info in rows)
                        safe.validate()
                except (OSError, ValueError, EvidenceError) as error:
                    self.check()
                    read_error=True
                    actual = ['error', type(error).__name__, getattr(error, 'code', None)]
                if actual != expected:
                    self.changed = True
                if not failed and not read_error:
                    confirmed[key]=actual
            for key, callback in list(self.callbacks.items()):
                self.check()
                if callback() != self.facts[key]:
                    self.changed = True
        finally:
            self.verifying = False
        self.check()
        if self.valid_until and datetime.now(timezone.utc) >= datetime.fromisoformat(self.valid_until):
            self.changed = True
        return not self.changed

    def input_digest(self):
        return digest({'reads': [[*key, value] for key, value in sorted(self.transcript.items())],
                       'facts': self.facts})

    def accounting(self):
        return {'read_entries': self.read_entries, 'input_bytes': self.input_bytes, 'attempts': self.attempts}


@contextmanager
def observing():
    value = Observation()
    token = ACTIVE.set(value)
    try:
        yield value
    finally:
        ACTIVE.reset(token)
