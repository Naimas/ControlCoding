"""Small durable checkpoint batches; never canonical evidence publication."""
import json

from .knowledge_store import put

MAX_ROWS = 32
MAX_BYTES = 1024 * 1024


class Writer:
    def __init__(self, db, progress_key, first=False):
        self.db, self.progress_key, self.first = db, progress_key, first
        self.pending, self.size, self.progress = [], 0, None

    def add(self, key, value, progress):
        size = len(json.dumps(value).encode('utf-8'))
        if self.pending and self.size + size > MAX_BYTES:
            self.flush()
        self.pending.append((key, value))
        self.size += size
        self.progress = dict(progress)
        if self.first or len(self.pending) >= MAX_ROWS or self.size >= MAX_BYTES:
            self.flush()
            self.first = False

    def flush(self):
        if not self.pending:
            return
        with self.db:
            for key, value in self.pending:
                put(self.db, key, value)
            put(self.db, self.progress_key, self.progress)
        self.pending, self.size = [], 0
