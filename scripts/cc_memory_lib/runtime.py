"""Side-effect-free Core/runtime checks shared by setup and memory entry points."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import importlib
import sys
import zlib


CORE_MINIMUM = (3, 11)

# A 1024-byte SQLite image, page_size=512, containing one table/row:
# CREATE TABLE cc_runtime_probe(value TEXT);
# INSERT INTO cc_runtime_probe VALUES ('cc-deserialize-probe-v1');
# Fixed data avoids requiring serialize() at runtime or touching a disk database.
_PROBE_IMAGE = (
    "eJwLDvTJLElVSMsvyk0sUTBmYGJgZGRwUFBggAAmBgRgBGIWND5RgBekmHElAxCNAnJBKCOb"
    "uK4uY3xJYlJOanJyfFFpXklmbmp8QVF+EgafyTnI1THEVSHE0cnHVQFdVqMsMac0VSHENSJE"
    "ExI3TxmAaBQMVyDJyGSdnKybklqcWpSZmJNZlaoLTgi6ZYYAeCMwMQ=="
)


@dataclass(frozen=True)
class RuntimeIssue:
    code: str
    detail: str
    python: str
    sqlite: str

    @property
    def message(self) -> str:
        return (f"{self.detail} Detected Python {self.python}, SQLite {self.sqlite}. "
                "Use Python 3.11+ with a working sqlite3 deserialize API for memory; "
                "no project changes were made by this check.")

    def payload(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message,
                "pythonVersion": self.python, "sqliteVersion": self.sqlite}


def core_runtime_error() -> RuntimeIssue | None:
    """Check the Core minimum/import, without probing or creating memory."""
    python = ".".join(str(part) for part in sys.version_info[:3])
    sqlite_version = "unavailable"
    import_error = None
    try:
        sqlite = importlib.import_module("sqlite3")
        sqlite_version = sqlite.sqlite_version
    except (ImportError, OSError) as exc:
        import_error = exc
    if sys.version_info[:2] < CORE_MINIMUM:
        return RuntimeIssue("unsupported_python_runtime", "ControlCoding Core requires Python 3.11+.",
                            python, sqlite_version)
    if import_error is not None:
        return RuntimeIssue("sqlite_runtime_unavailable", f"Cannot import sqlite3: {import_error}.",
                            python, sqlite_version)
    return None


def memory_runtime_error() -> RuntimeIssue | None:
    """Deserialize and query a private image; no project path or files involved."""
    issue = core_runtime_error()
    if issue is not None:
        return issue
    sqlite = importlib.import_module("sqlite3")
    connection = None
    failure = None
    try:
        connection = sqlite.connect(":memory:")
        connection.execute("PRAGMA temp_store = MEMORY")
        deserialize = getattr(connection, "deserialize", None)
        if not callable(deserialize):
            raise sqlite.NotSupportedError("deserialize is missing or not callable")
        deserialize(zlib.decompress(base64.b64decode(_PROBE_IMAGE)))
        row = connection.execute("SELECT value FROM cc_runtime_probe").fetchone()
        if row != ("cc-deserialize-probe-v1",):
            raise sqlite.DatabaseError("deserialize probe returned unexpected data")
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                detail = f"connection close failed: {type(exc).__name__}: {exc}"
                failure = f"{failure}; {detail}" if failure else detail
    if failure is not None:
        return RuntimeIssue("memory_runtime_unavailable", f"SQLite deserialize check failed ({failure}).",
                            ".".join(str(part) for part in sys.version_info[:3]), sqlite.sqlite_version)
    return None
