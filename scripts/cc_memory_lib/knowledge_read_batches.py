"""Reuse pinned ancestors within a small same-directory file window."""
from .knowledge_sources import KnowledgeReadPolicy
from .knowledge_store import KnowledgeError


class SnapshotBatches:
    def __init__(self, root, factory):
        self.root, self.factory = root, factory
        self.reader = None
        self.parent = self.cap = None
        self.count = 0

    def __enter__(self):
        return self

    def close(self, validate=True):
        if self.reader is not None:
            reader, self.reader = self.reader, None
            try:
                if validate:
                    reader.recheck()
            finally:
                reader.__exit__(None, None, None)

    def __exit__(self, kind, value, traceback):
        self.close(validate=kind is None)

    def read(self, relative, cap, sidecar=False):
        path = self.root / relative
        if self.reader is None or self.parent != path.parent or self.cap != cap or self.count >= 8:
            self.close()
            self.reader = self.factory(self.root, {}, KnowledgeReadPolicy(file_bytes=cap))
            self.reader.__enter__()
            self.parent, self.cap, self.count = path.parent, cap, 0
        self.count += 1
        observed = self.reader.observe(path, {})
        if observed is None:
            raise KnowledgeError('changed_input')
        companion = self.reader.observe(self.root / (relative + '.ocr.json'), {}) if sidecar else None
        return observed[-1], companion[-1] if companion else None
