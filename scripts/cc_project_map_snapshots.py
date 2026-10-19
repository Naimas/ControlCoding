"""Bounded map reads across descriptor windows with a final identity/content pass."""
from dataclasses import replace
import hashlib
from pathlib import Path

from cc_setup_service import _Snapshots, SetupServiceError


class MapSnapshotBatches:
    def __init__(self, root, policy, tick):
        self.root, self.policy, self.tick = root, policy, tick
        self.reader = None
        self.ledger = {}
        self.seen = set()
        self.used = {'target': 0, 'trusted': 0}
        self.peak_entries = self.windows = 0

    @property
    def entries(self):
        return self.reader.entries if self.reader else {}

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, kind, value, traceback):
        self._close(validate=kind is None)

    @staticmethod
    def _identity(directory, snapshot):
        if snapshot is None:
            return directory, None
        if directory:
            return True, snapshot
        return False, (*snapshot[:-1], hashlib.sha256(snapshot[-1]).hexdigest())

    def _remember(self):
        for path, (directory, snapshot, _) in self.reader.entries.items():
            if path in self.seen:
                continue
            self.tick()
            identity = self._identity(directory, snapshot)
            if path in self.ledger:
                if self.ledger[path] != identity:
                    raise SetupServiceError('changed_input', 'source')
            else:
                # Enumeration is bounded separately; include pinned ancestors and marker.
                if len(self.ledger) >= self.policy.max_entries + len(self.root.parents) + 4:
                    raise SetupServiceError('input_limit', 'source')
                if not directory and snapshot is not None:
                    self.used['target'] += len(snapshot[-1])
                    if self.used['target'] > self.policy.target_bytes:
                        raise SetupServiceError('too_large', 'source')
                self.ledger[path] = identity
            self.seen.add(path)
        self.peak_entries = max(self.peak_entries, len(self.reader.entries))

    def _start(self):
        self.tick()
        reader = _Snapshots(self.root, {}, self.policy)
        try:
            reader.__enter__()
            self.reader, self.seen = reader, set()
            self.windows += 1
            self._remember()
        except BaseException:
            reader.__exit__(None, None, None)
            self.reader = None
            raise

    def _close(self, validate=True):
        if self.reader is not None:
            reader, self.reader = self.reader, None
            try:
                if validate:
                    self.tick()
                    reader.recheck()
                    self.tick()
            finally:
                reader.__exit__(None, None, None)

    def observe(self, path, observations, *, directory=False):
        path = Path(path)
        self.tick()
        if self.reader is None:
            self._start()
        needed = {path, *path.parents} - self.reader.entries.keys()
        if len(self.reader.entries) + len(needed) > self.policy.input_count:
            self._close()
            self._start()
        # Global unique-content budget, independent of descriptor rotations.
        allowance = self.policy.file_bytes if path in self.ledger else self.policy.target_bytes - self.used['target']
        self.reader.policy = replace(self.policy, target_bytes=self.reader.used['target'] + allowance)
        snapshot = self.reader.observe(path, observations, directory=directory)
        self._remember()
        return snapshot

    def recheck(self):
        self.tick()
        if self.windows == 1:
            # Small maps retain the original simultaneous-pin behavior.
            self.reader.recheck()
        else:
            expected = list(self.ledger.items())
            self._close()
            for path, (directory, _) in expected:
                # Ancestors are reopened and compared when their project child opens.
                if path == self.root or path.is_relative_to(self.root):
                    self.observe(path, {}, directory=directory)
            self.reader.recheck()
        self.tick()
