"""Filesystem and SQLite store helpers for project memory."""

from __future__ import annotations

import datetime as _dt
import errno
import hashlib
import json
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _WindowsFileBasicInfo(ctypes.Structure):
        _fields_ = (
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        )
else:
    import fcntl

from .migrations import (
    ensure_retrieval_fts,
    inspect_schema,
    migrate_schema,
)
from .schema import (
    CONTROL_DIRNAME,
    DB_FILENAME,
    LOGS_DIRNAME,
    MANIFEST_FILENAME,
    MEMORY_DIRNAME,
    REQUIRED_LOGS,
    VIEWS_DIRNAME,
)

SQLITE_CONNECT_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30000
MEMORY_DB_DOCTOR_COMMAND = "python scripts/cc.py memory doctor --project-root ."
MEMORY_DB_UNREADABLE_CODE = "memory_db_unreadable"
MEMORY_DB_SQLITE_ERROR_CODE = "memory_db_sqlite_error"
MEMORY_DB_UNREADABLE_ERROR_FRAGMENTS = (
    "file is not a database",
    "database disk image is malformed",
    "malformed database schema",
    "unable to open database file",
)


@dataclass
class _MemoryTransactionEffects:
    connection: Any
    project: Path
    rollback_actions: list[Callable[[], None]] = field(default_factory=list)
    transactional_jsonl: dict[Path, "_JsonlRollbackState"] = field(default_factory=dict)
    transactional_parent_generations: dict[Path, list["_DirectoryGeneration"]] = field(
        default_factory=dict
    )
    transactional_owned_baselines: dict[
        Path,
        list["_FileRollbackState | None"],
    ] = field(default_factory=dict)


@dataclass(frozen=True)
class _FileObjectIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class _FileRollbackState:
    existed: bool
    content: bytes = b""
    mode: int = 0
    atime_ns: int = 0
    mtime_ns: int = 0
    identity: _FileObjectIdentity | None = None
    generation: _FileEntryGeneration | None = None
    parent_generation: _DirectoryGeneration | None = None


@dataclass(frozen=True)
class _FileIdentityState:
    size: int
    sha256: str
    mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class _FileEntryGeneration:
    identity: _FileObjectIdentity
    change_time_ns: int
    link_count: int


@dataclass(frozen=True)
class _DirectoryGeneration:
    identity: _FileObjectIdentity
    modified_time_ns: int
    change_time_ns: int


@dataclass(frozen=True)
class _DirectoryEntrySnapshot:
    identity: _FileObjectIdentity
    mode: int
    size: int
    modified_time_ns: int
    change_time_ns: int
    link_count: int


@dataclass
class _JsonlRollbackState:
    previous: _FileRollbackState
    owned_suffix: bytearray = field(default_factory=bytearray)
    identity: _FileObjectIdentity | None = None
    owned_state: list[_FileRollbackState | None] = field(default_factory=lambda: [None])
    predecessor_owned_state: list[_FileRollbackState | None] | None = None


@dataclass(frozen=True)
class _DatabaseArtifactBaseline:
    existed: bool
    regular: bool = False
    identity: _FileObjectIdentity | None = None
    rollback: _FileRollbackState | None = None


@dataclass(frozen=True)
class _OwnedDirectory:
    path: Path
    identity: _FileObjectIdentity


_ACTIVE_MEMORY_TRANSACTION: ContextVar[_MemoryTransactionEffects | None] = ContextVar(
    "active_memory_transaction",
    default=None,
)


def is_memory_db_unreadable_error(exc: sqlite3.Error) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in MEMORY_DB_UNREADABLE_ERROR_FRAGMENTS)


def _memory_db_error_payload(
    project: Path,
    exc: sqlite3.Error,
    *,
    operation: str,
    command: str,
    degraded: bool = False,
) -> dict[str, Any]:
    code = (
        MEMORY_DB_UNREADABLE_CODE
        if is_memory_db_unreadable_error(exc)
        else MEMORY_DB_SQLITE_ERROR_CODE
    )
    db_path = _db_path(project)
    sqlite_error = str(exc) or exc.__class__.__name__
    message = (
        f"{code}: memory database is not readable at {db_path}. "
        f"Run `{MEMORY_DB_DOCTOR_COMMAND}`. "
        "Preserve the existing memory.db file, inspect or back it up manually, "
        "then restore from a known-good backup or intentionally reinitialize memory."
    )
    return {
        "ok": False,
        "available": False,
        "degraded": degraded,
        "code": code,
        "error": code,
        "message": message,
        "operation": operation,
        "command": command,
        "databasePath": str(db_path),
        "diagnosticCommand": MEMORY_DB_DOCTOR_COMMAND,
        "manualAction": (
            "No automatic repair was attempted. Preserve memory.db, run the "
            "diagnostic command, and choose a manual restore or intentional "
            "reinitialization path after backing up the unreadable file."
        ),
        "sqliteError": sqlite_error,
    }


def _memory_db_error_text(payload: dict[str, Any]) -> str:
    return "\n".join([
        f"Error: {payload['message']}",
        f"  Code: {payload['code']}",
        f"  Database: {payload['databasePath']}",
        f"  Diagnostic: {payload['diagnosticCommand']}",
        f"  Manual action: {payload['manualAction']}",
        f"  SQLite detail: {payload['sqliteError']}",
    ])

def _now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

def _today_yyyymmdd() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")

def _control_dir(project: Path) -> Path:
    return project / CONTROL_DIRNAME

def _memory_dir(project: Path) -> Path:
    return _control_dir(project) / MEMORY_DIRNAME

def _logs_dir(project: Path) -> Path:
    return _control_dir(project) / LOGS_DIRNAME

def _views_dir(project: Path) -> Path:
    return _control_dir(project) / VIEWS_DIRNAME

def _db_path(project: Path) -> Path:
    return _memory_dir(project) / DB_FILENAME

def _manifest_path(project: Path) -> Path:
    return _control_dir(project) / MANIFEST_FILENAME

def _json_dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)

def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

def _directory_object_identity(path: Path) -> _FileObjectIdentity:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or getattr(details, "st_reparse_tag", 0):
        raise RuntimeError(f"transactional memory directory must be physical: {path}")
    return _object_identity(details)


def _windows_directory_change_time_ns(path: Path) -> int:
    if os.name != "nt":
        raise RuntimeError("Windows directory ChangeTime is unavailable on this platform")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(Path(os.path.abspath(path))),
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    primary_error: BaseException | None = None
    try:
        information = _WindowsFileBasicInfo()
        if not get_information(
            handle,
            0,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(information.ChangeTime) * 100
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if not close_handle(handle):
            close_error = ctypes.WinError(ctypes.get_last_error())
            if primary_error is None:
                raise close_error
            primary_error.add_note(
                f"{close_error.__class__.__name__}: {close_error}"
            )


def _directory_generation(path: Path) -> _DirectoryGeneration:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or getattr(details, "st_reparse_tag", 0):
        raise RuntimeError(f"transactional memory directory must be physical: {path}")
    change_time_ns = details.st_ctime_ns
    if os.name == "nt":
        change_time_ns = _windows_directory_change_time_ns(path)
        final_details = path.lstat()
        if (
            not stat.S_ISDIR(final_details.st_mode)
            or getattr(final_details, "st_reparse_tag", 0)
            or _object_identity(final_details) != _object_identity(details)
            or final_details.st_mtime_ns != details.st_mtime_ns
        ):
            raise RuntimeError(
                f"transactional memory directory changed while capturing generation: {path}"
            )
        details = final_details
    return _DirectoryGeneration(
        identity=_object_identity(details),
        modified_time_ns=details.st_mtime_ns,
        change_time_ns=change_time_ns,
    )


def _directory_entry_snapshot(details: os.stat_result) -> _DirectoryEntrySnapshot:
    return _DirectoryEntrySnapshot(
        identity=_object_identity(details),
        mode=details.st_mode,
        size=details.st_size,
        modified_time_ns=details.st_mtime_ns,
        change_time_ns=details.st_ctime_ns,
        link_count=details.st_nlink,
    )


def _parent_entry_snapshot(path: Path) -> dict[str, _DirectoryEntrySnapshot]:
    """Capture a comparable, non-recursive snapshot of a physical parent's entries."""
    try:
        opening_generation = _directory_generation(path)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            f"transactional memory parent entry snapshot is unavailable: {path}"
        ) from exc

    entries: dict[str, _DirectoryEntrySnapshot] = {}
    try:
        with os.scandir(path) as scanned_entries:
            for entry in scanned_entries:
                details = (path / entry.name).lstat()
                if entry.name in entries:
                    raise RuntimeError(
                        f"transactional memory parent entry snapshot is ambiguous: {path}"
                    )
                entries[entry.name] = _directory_entry_snapshot(details)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            f"transactional memory parent entry snapshot is unavailable: {path}"
        ) from exc

    try:
        closing_generation = _directory_generation(path)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            f"transactional memory parent entry snapshot is unavailable: {path}"
        ) from exc
    if closing_generation != opening_generation:
        raise RuntimeError(
            f"transactional memory parent changed while capturing entry snapshot: {path}"
        )
    for name, expected in entries.items():
        try:
            observed = _directory_entry_snapshot((path / name).lstat())
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(
                f"transactional memory parent entry snapshot is unavailable: {path}"
            ) from exc
        if observed != expected:
            raise RuntimeError(
                f"transactional memory parent entry changed while capturing snapshot: {path}"
            )
    if _directory_generation(path) != closing_generation:
        raise RuntimeError(
            f"transactional memory parent changed while capturing entry snapshot: {path}"
        )
    return entries


def _remove_owned_empty_directories(
    directories: list[_OwnedDirectory],
    *,
    preserve_nonempty: bool = False,
) -> None:
    errors: list[BaseException] = []
    for owned in directories:
        path = owned.path
        try:
            if _directory_object_identity(path) != owned.identity:
                raise RuntimeError(f"transactional memory directory identity changed: {path}")
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            is_nonempty = exc.errno in {errno.ENOTEMPTY, errno.EEXIST} or getattr(
                exc,
                "winerror",
                None,
            ) == 145
            if preserve_nonempty and is_nonempty:
                try:
                    if _directory_object_identity(path) != owned.identity:
                        raise RuntimeError(
                            f"transactional memory directory identity changed: {path}"
                        )
                except BaseException as identity_exc:
                    errors.append(identity_exc)
                continue
            errors.append(exc)
        except BaseException as exc:
            errors.append(exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        failure = RuntimeError("transactional memory directory cleanup encountered multiple failures")
        for error in errors:
            _append_exception_notes(failure, error)
        raise failure from errors[0]


def _physical_directory_chain(
    path: Path,
    trust_anchor: Path,
) -> tuple[Path, list[Path]]:
    anchor = Path(os.path.abspath(trust_anchor))
    target = Path(os.path.abspath(path))
    try:
        relative = target.relative_to(anchor)
    except ValueError as exc:
        raise RuntimeError(
            f"transactional memory directory must stay inside the physical project root: {path}"
        ) from exc
    _directory_object_identity(anchor)
    missing: list[Path] = []
    current = anchor
    missing_observed = False
    for component in relative.parts:
        current /= component
        if missing_observed:
            missing.append(current)
            continue
        try:
            _directory_object_identity(current)
        except FileNotFoundError:
            missing.append(current)
            missing_observed = True
    return target, missing


def _physical_file_path(
    path: Path,
    trust_anchor: Path,
) -> tuple[Path, list[Path]]:
    target = Path(os.path.abspath(path))
    parent, missing = _physical_directory_chain(target.parent, trust_anchor)
    return parent / target.name, missing


def _create_physical_directory_chain(
    path: Path,
    trust_anchor: Path,
) -> list[_OwnedDirectory]:
    _target, missing = _physical_directory_chain(path, trust_anchor)
    owned: list[_OwnedDirectory] = []
    try:
        for directory in missing:
            try:
                directory.mkdir()
            except FileExistsError:
                _directory_object_identity(directory)
                continue
            owned.append(_OwnedDirectory(directory, _directory_object_identity(directory)))
    except BaseException as exc:
        cleanup_errors: list[BaseException] = []
        try:
            _remove_owned_empty_directories(list(reversed(owned)))
        except BaseException as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
        if cleanup_errors:
            failure = RuntimeError(f"directory creation cleanup failed: {path}")
            for cleanup_error in cleanup_errors:
                _append_exception_notes(failure, cleanup_error)
            raise failure from exc
        raise
    return list(reversed(owned))


def _run_compensation(
    label: str,
    action: Callable[[], None],
    directories: list[_OwnedDirectory],
) -> None:
    errors: list[BaseException] = []
    try:
        action()
    except BaseException as exc:
        errors.append(exc)
    try:
        _remove_owned_empty_directories(directories)
    except BaseException as exc:
        errors.append(exc)
    if not errors:
        return
    if len(errors) == 1:
        raise errors[0]
    failure = RuntimeError(f"{label} encountered multiple compensation failures")
    for error in errors:
        _append_exception_notes(failure, error)
    raise failure from errors[0]


def _append_exception_notes(target: BaseException, error: BaseException) -> None:
    if target is error:
        return
    nested_notes = tuple(getattr(error, "__notes__", ()))
    target.add_note(f"{error.__class__.__name__}: {error}")
    for note in nested_notes:
        target.add_note(f"nested: {note}")


def _close_connection(
    conn: Any,
    *,
    primary_error: BaseException | None,
) -> None:
    try:
        conn.close()
    except BaseException as close_error:
        if primary_error is None:
            raise
        _append_exception_notes(primary_error, close_error)


def _object_identity(details: os.stat_result) -> _FileObjectIdentity:
    return _FileObjectIdentity(device=details.st_dev, inode=details.st_ino)


def _regular_file_object_identity(path: Path) -> _FileObjectIdentity:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or getattr(details, "st_reparse_tag", 0):
        raise RuntimeError(f"transactional memory file must be regular: {path}")
    return _object_identity(details)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _descriptor_identity(descriptor: int, path: Path) -> _FileObjectIdentity:
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"transactional memory descriptor must reference a regular file: {path}")
    return _object_identity(details)


def _open_identity_bound_regular(path: Path, flags: int, mode: int = 0o666) -> tuple[int, _FileObjectIdentity]:
    descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0), mode)
    try:
        descriptor_identity = _descriptor_identity(descriptor, path)
        pathname_identity = _regular_file_object_identity(path)
        if descriptor_identity != pathname_identity:
            raise RuntimeError(f"transactional memory pathname changed while opening it: {path}")
        return descriptor, descriptor_identity
    except BaseException as exc:
        try:
            os.close(descriptor)
        except BaseException as close_exc:
            _append_exception_notes(exc, close_exc)
        raise


def _read_descriptor_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _lock_descriptor_exclusive(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 0x7FFFFFFF)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 0x7FFFFFFF)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _release_descriptor(
    descriptor: int,
    *,
    locked: bool,
    primary_error: BaseException | None,
) -> None:
    cleanup_error: BaseException | None = None
    if locked:
        try:
            _unlock_descriptor(descriptor)
        except BaseException as unlock_error:
            cleanup_error = unlock_error
    try:
        os.close(descriptor)
    except BaseException as close_error:
        if cleanup_error is None:
            cleanup_error = close_error
        else:
            _append_exception_notes(cleanup_error, close_error)
    if cleanup_error is None:
        return
    if primary_error is not None:
        _append_exception_notes(primary_error, cleanup_error)
        return
    raise cleanup_error


def _write_descriptor_bytes(descriptor: int, content: bytes) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("transactional memory descriptor write made no progress")
        offset += written


def _owned_file_rollback_state(
    path: Path,
    descriptor: int,
    expected_identity: _FileObjectIdentity,
    expected_content: bytes,
    *,
    expected_parent_generation: _DirectoryGeneration | None = None,
) -> _FileRollbackState:
    def cross_handle_generation(details: os.stat_result) -> tuple[Any, ...]:
        return (
            _object_identity(details),
            stat.S_IFMT(details.st_mode),
            stat.S_IMODE(details.st_mode),
            details.st_size,
            details.st_mtime_ns,
            details.st_nlink,
        )

    def full_generation(details: os.stat_result) -> tuple[Any, ...]:
        return cross_handle_generation(details) + (details.st_ctime_ns,)

    descriptor_details = os.fstat(descriptor)
    path_details = path.lstat()
    if (
        not stat.S_ISREG(descriptor_details.st_mode)
        or not stat.S_ISREG(path_details.st_mode)
        or getattr(path_details, "st_reparse_tag", 0)
        or _object_identity(descriptor_details) != expected_identity
        or _object_identity(path_details) != expected_identity
        or descriptor_details.st_size != len(expected_content)
        or cross_handle_generation(descriptor_details)
        != cross_handle_generation(path_details)
    ):
        raise RuntimeError(
            f"transactional memory file changed while capturing owned baseline: {path}"
        )
    parent_generation = _directory_generation(path.parent)
    if (
        expected_parent_generation is not None
        and parent_generation != expected_parent_generation
    ):
        raise RuntimeError(
            f"transactional memory parent changed while capturing owned baseline: {path.parent}"
        )
    final_descriptor_details = os.fstat(descriptor)
    final_path_details = path.lstat()
    if (
        full_generation(final_descriptor_details) != full_generation(descriptor_details)
        or full_generation(final_path_details) != full_generation(path_details)
        or _directory_generation(path.parent) != parent_generation
    ):
        raise RuntimeError(
            f"transactional memory file changed while capturing owned baseline: {path}"
        )
    return _FileRollbackState(
        existed=True,
        content=expected_content,
        mode=stat.S_IMODE(final_descriptor_details.st_mode),
        atime_ns=final_descriptor_details.st_atime_ns,
        mtime_ns=final_descriptor_details.st_mtime_ns,
        identity=expected_identity,
        generation=_FileEntryGeneration(
            identity=expected_identity,
            change_time_ns=final_path_details.st_ctime_ns,
            link_count=final_path_details.st_nlink,
        ),
        parent_generation=parent_generation,
    )


def _restore_owned_file_bytes(
    path: Path,
    expected_identity: _FileObjectIdentity,
    expected_content: bytes,
    replacement_content: bytes,
    *,
    mode: int,
    atime_ns: int,
    mtime_ns: int,
    expected_generation: _FileEntryGeneration | None = None,
    expected_parent_generation: _DirectoryGeneration | None = None,
) -> _FileRollbackState:
    descriptor, descriptor_identity = _open_identity_bound_regular(path, os.O_RDWR)
    locked = False
    primary_error: BaseException | None = None
    try:
        if descriptor_identity != expected_identity:
            raise RuntimeError(f"transactional memory file identity changed: {path}")
        _lock_descriptor_exclusive(descriptor)
        locked = True
        if (
            expected_parent_generation is not None
            and _directory_generation(path.parent) != expected_parent_generation
        ):
            raise RuntimeError(
                f"transactional memory parent changed outside rollback ownership: {path.parent}"
            )
        if (
            expected_generation is not None
            and _file_entry_generation(path) != expected_generation
        ):
            raise RuntimeError(
                f"transactional memory path generation changed outside rollback ownership: {path}"
            )
        if _read_descriptor_bytes(descriptor) != expected_content:
            raise RuntimeError(f"transactional memory file changed outside rollback ownership: {path}")
        if _read_descriptor_bytes(descriptor) != expected_content:
            raise RuntimeError(f"transactional memory file changed before rollback mutation: {path}")
        _write_descriptor_bytes(descriptor, replacement_content)
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        if os.utime in os.supports_fd:
            os.utime(descriptor, ns=(atime_ns, mtime_ns))
        else:
            if _regular_file_object_identity(path) != expected_identity:
                raise RuntimeError(f"transactional memory pathname changed before metadata restore: {path}")
            os.utime(path, ns=(atime_ns, mtime_ns))
            if _regular_file_object_identity(path) != expected_identity:
                raise RuntimeError(f"transactional memory pathname changed during metadata restore: {path}")
        if _regular_file_object_identity(path) != expected_identity:
            raise RuntimeError(f"transactional memory pathname changed after rollback: {path}")
        restored = _owned_file_rollback_state(
            path,
            descriptor,
            expected_identity,
            replacement_content,
            expected_parent_generation=expected_parent_generation,
        )
        if (
            expected_generation is not None
            and restored.generation is not None
            and restored.generation.link_count != expected_generation.link_count
        ):
            raise RuntimeError(
                f"transactional memory path link count changed during rollback: {path}"
            )
        return restored
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _release_descriptor(
            descriptor,
            locked=locked,
            primary_error=primary_error,
        )


def _validate_owned_file_bytes(
    path: Path,
    expected_identity: _FileObjectIdentity,
    expected_content: bytes,
) -> None:
    descriptor, descriptor_identity = _open_identity_bound_regular(path, os.O_RDWR)
    locked = False
    primary_error: BaseException | None = None
    try:
        if descriptor_identity != expected_identity:
            raise RuntimeError(f"transactional memory file identity changed: {path}")
        _lock_descriptor_exclusive(descriptor)
        locked = True
        if _read_descriptor_bytes(descriptor) != expected_content:
            raise RuntimeError(f"transactional memory file changed outside rollback ownership: {path}")
        if _regular_file_object_identity(path) != expected_identity:
            raise RuntimeError(f"transactional memory pathname changed during validation: {path}")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _release_descriptor(
            descriptor,
            locked=locked,
            primary_error=primary_error,
        )


def _identity_bound_unlink(
    path: Path,
    expected: _FileObjectIdentity,
    *,
    expected_content: bytes | None = None,
    expected_generation: _FileEntryGeneration | None = None,
    expected_parent_generation: _DirectoryGeneration | None = None,
) -> None:
    """Unlink a rollback-owned path under the cooperative-writer threat model.

    When supplied, file-entry and parent-directory generation snapshots detect
    replacements observable at their validation points. ControlCoding writers
    touching these entries must participate in the established writer
    coordination. A pathname plus an inode identity does not provide a comparable
    directory-entry-incarnation token, so after the last useful observation a
    non-cooperating actor's same-inode reincarnation with indistinguishable
    metadata is not portably distinguishable. These guards are defense in depth,
    not atomic compare-and-delete, and every observable ambiguity must remain
    fail-closed.
    """
    tombstone = path.parent / f".{path.name}.{uuid.uuid4().hex}.rollback"
    if (
        expected_parent_generation is not None
        and _directory_generation(path.parent) != expected_parent_generation
    ):
        raise RuntimeError(
            f"transactional memory parent generation changed outside rollback ownership: {path.parent}"
        )
    if (
        expected_generation is not None
        and _file_entry_generation(path) != expected_generation
    ):
        raise RuntimeError(
            f"transactional memory path generation changed outside rollback ownership: {path}"
        )
    os.replace(path, tombstone)
    try:
        observed = _regular_file_object_identity(tombstone)
        if observed != expected:
            if _path_entry_exists(path):
                raise RuntimeError(
                    f"transactional memory path changed and could not be restored safely: {path}"
                )
            os.replace(tombstone, path)
            raise RuntimeError(f"transactional memory path changed outside rollback ownership: {path}")
        if (
            expected_parent_generation is not None
            and _directory_object_identity(path.parent)
            != expected_parent_generation.identity
        ):
            raise RuntimeError(
                f"transactional memory parent identity changed outside rollback ownership: {path.parent}"
            )
        if expected_content is not None:
            _validate_owned_file_bytes(tombstone, expected, expected_content)
        if expected_generation is not None:
            observed_generation = _file_entry_generation(tombstone)
            if (
                observed_generation.identity != expected_generation.identity
                or observed_generation.link_count != expected_generation.link_count
            ):
                raise RuntimeError(
                    f"transactional memory path generation changed outside rollback ownership: {path}"
                )
        tombstone.unlink()
    except BaseException as exc:
        restore_errors: list[BaseException] = []
        if _path_entry_exists(tombstone):
            if _path_entry_exists(path):
                restore_errors.append(
                    RuntimeError(f"transactional memory rollback destination became occupied: {path}")
                )
            else:
                try:
                    os.replace(tombstone, path)
                except BaseException as restore_exc:
                    restore_errors.append(restore_exc)
        if restore_errors:
            failure = RuntimeError(f"transactional memory path could not be restored: {path}")
            for restore_error in restore_errors:
                _append_exception_notes(failure, restore_error)
            raise failure from exc
        raise


def _file_rollback_state(
    path: Path,
    *,
    capture_content: bool = True,
    require_single_link: bool = False,
) -> _FileRollbackState:
    try:
        parent_generation = _directory_generation(path.parent)
    except FileNotFoundError:
        try:
            path.lstat()
        except FileNotFoundError:
            return _FileRollbackState(existed=False)
        raise RuntimeError(
            f"transactional memory output parent disappeared while capturing baseline: {path.parent}"
        )
    try:
        details = path.lstat()
    except FileNotFoundError:
        if _directory_generation(path.parent) != parent_generation:
            raise RuntimeError(
                f"transactional memory parent changed while capturing absent baseline: {path.parent}"
            )
        return _FileRollbackState(
            existed=False,
            parent_generation=parent_generation,
        )
    if not stat.S_ISREG(details.st_mode) or getattr(details, "st_reparse_tag", 0):
        raise RuntimeError(f"transactional memory output must be a regular file: {path}")

    def capture_cross_handle_generation(observation: os.stat_result) -> tuple[Any, ...]:
        return (
            _object_identity(observation),
            stat.S_IFMT(observation.st_mode),
            stat.S_IMODE(observation.st_mode),
            observation.st_size,
            observation.st_mtime_ns,
            observation.st_nlink,
        )

    def capture_full_generation(observation: os.stat_result) -> tuple[Any, ...]:
        return capture_cross_handle_generation(observation) + (observation.st_ctime_ns,)

    descriptor: int | None = None
    initial_path_generation = capture_full_generation(details)
    try:
        descriptor, descriptor_identity = _open_identity_bound_regular(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_details = os.fstat(descriptor)
        if require_single_link and opened_details.st_nlink != 1:
            raise RuntimeError(
                f"memory database artifact must have exactly one hard link: {path}"
            )
        if (
            descriptor_identity != _object_identity(details)
            or capture_cross_handle_generation(opened_details)
            != capture_cross_handle_generation(details)
        ):
            raise RuntimeError(
                f"transactional memory file changed while capturing rollback baseline: {path}"
            )
        content = _read_descriptor_bytes(descriptor) if capture_content else b""
        final_details = os.fstat(descriptor)
        if capture_full_generation(final_details) != capture_full_generation(opened_details):
            raise RuntimeError(
                f"transactional memory file changed while capturing rollback baseline: {path}"
            )
        final_path_details = path.lstat()
        if (
            not stat.S_ISREG(final_path_details.st_mode)
            or getattr(final_path_details, "st_reparse_tag", 0)
            or capture_cross_handle_generation(final_path_details)
            != capture_cross_handle_generation(final_details)
            or capture_full_generation(final_path_details) != initial_path_generation
        ):
            raise RuntimeError(
                f"transactional memory pathname changed while capturing rollback baseline: {path}"
            )
        final_parent_generation = _directory_generation(path.parent)
        if final_parent_generation != parent_generation:
            raise RuntimeError(
                f"transactional memory parent changed while capturing rollback baseline: {path.parent}"
            )
        rollback = _FileRollbackState(
            existed=True,
            content=content,
            mode=stat.S_IMODE(details.st_mode),
            atime_ns=details.st_atime_ns,
            mtime_ns=details.st_mtime_ns,
            identity=descriptor_identity,
            generation=_FileEntryGeneration(
                identity=descriptor_identity,
                change_time_ns=final_path_details.st_ctime_ns,
                link_count=final_path_details.st_nlink,
            ),
            parent_generation=final_parent_generation,
        )
    except BaseException as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as close_exc:
                _append_exception_notes(exc, close_exc)
        raise
    else:
        os.close(descriptor)
    return rollback


def _same_rollback_state(
    observed: _FileRollbackState,
    expected: _FileRollbackState,
    *,
    include_parent: bool = True,
) -> bool:
    if observed.existed != expected.existed:
        return False
    if include_parent and observed.parent_generation != expected.parent_generation:
        return False
    if not expected.existed:
        return True
    return (
        observed.identity == expected.identity
        and observed.content == expected.content
        and observed.mode == expected.mode
        and observed.mtime_ns == expected.mtime_ns
        and observed.generation == expected.generation
    )


def _file_identity_state(path: Path) -> _FileIdentityState:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or getattr(details, "st_reparse_tag", 0):
        raise RuntimeError(f"transactional memory file must be regular: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    final_details = path.lstat()
    if (
        final_details.st_dev != details.st_dev
        or final_details.st_ino != details.st_ino
        or final_details.st_size != details.st_size
        or final_details.st_mtime_ns != details.st_mtime_ns
    ):
        raise RuntimeError(f"transactional memory file changed while identifying it: {path}")
    return _FileIdentityState(
        size=details.st_size,
        sha256=digest.hexdigest(),
        mode=stat.S_IMODE(details.st_mode),
        device=details.st_dev,
        inode=details.st_ino,
    )


def _file_entry_generation(path: Path) -> _FileEntryGeneration:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or getattr(details, "st_reparse_tag", 0):
        raise RuntimeError(f"transactional memory file must be regular: {path}")
    return _FileEntryGeneration(
        identity=_object_identity(details),
        change_time_ns=details.st_ctime_ns,
        link_count=details.st_nlink,
    )


def _atomic_replace_bytes(
    path: Path,
    content: bytes,
    *,
    mode: int | None = None,
    replace_existing: bool = True,
    ownership: list[_FileObjectIdentity | None] | None = None,
    expected_state: _FileRollbackState | None = None,
    publication_state: list[_FileRollbackState | None] | None = None,
) -> _FileObjectIdentity:
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = expected_state if expected_state is not None else _file_rollback_state(path)
    if not replace_existing and baseline.existed:
        raise FileExistsError(errno.EEXIST, "transactional output already exists", str(path))
    if not _same_rollback_state(_file_rollback_state(path), baseline):
        raise RuntimeError(f"transactional memory output changed before publication: {path}")
    parent_entries_before_temporary = _parent_entry_snapshot(path.parent)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    temporary_identity: _FileObjectIdentity | None = None
    prepared_temporary: _FileRollbackState | None = None

    def cleanup_prepared_temporary() -> None:
        if temporary_identity is None:
            return
        if prepared_temporary is None:
            try:
                current_identity = _regular_file_object_identity(temporary)
            except FileNotFoundError:
                return
            current_parent_generation = _directory_generation(temporary.parent)
            if (
                current_identity != temporary_identity
                or baseline.parent_generation is None
                or current_parent_generation.identity
                != baseline.parent_generation.identity
                or _regular_file_object_identity(temporary) != current_identity
                or _directory_generation(temporary.parent) != current_parent_generation
            ):
                raise RuntimeError(
                    f"transactional temporary changed outside publication ownership: {temporary}"
                )
            temporary.unlink()
            return
        current_temporary = _file_rollback_state(temporary)
        if not current_temporary.existed:
            return
        if (
            current_temporary.identity != temporary_identity
            or current_temporary.parent_generation is None
            or baseline.parent_generation is None
            or current_temporary.parent_generation.identity
            != baseline.parent_generation.identity
        ):
            raise RuntimeError(
                f"transactional temporary changed outside publication ownership: {temporary}"
            )
        if (
            current_temporary.identity != prepared_temporary.identity
            or current_temporary.content != prepared_temporary.content
            or current_temporary.mode != prepared_temporary.mode
            or current_temporary.mtime_ns != prepared_temporary.mtime_ns
            or current_temporary.generation is None
            or prepared_temporary.generation is None
            or current_temporary.generation.identity
            != prepared_temporary.generation.identity
            or current_temporary.generation.link_count
            not in {
                prepared_temporary.generation.link_count,
                prepared_temporary.generation.link_count + 1,
            }
            or prepared_temporary.parent_generation is None
            or current_temporary.parent_generation.identity
            != prepared_temporary.parent_generation.identity
        ):
            raise RuntimeError(
                f"transactional temporary changed outside publication ownership: {temporary}"
            )
        if not _same_rollback_state(
            _file_rollback_state(temporary),
            current_temporary,
        ):
            raise RuntimeError(
                f"transactional temporary changed before cleanup: {temporary}"
            )
        temporary.unlink()

    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o666,
        )
        temporary_identity = _descriptor_identity(descriptor, temporary)
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException as exc:
            raw_descriptor = descriptor
            descriptor = None
            try:
                os.close(raw_descriptor)
            except BaseException as close_exc:
                _append_exception_notes(exc, close_exc)
            raise
        descriptor = None
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException as exc:
            try:
                handle.close()
            except BaseException as close_exc:
                _append_exception_notes(exc, close_exc)
            raise
        handle.close()
        if mode is not None:
            os.chmod(temporary, mode)
        pathname_temporary_identity = _regular_file_object_identity(temporary)
        if pathname_temporary_identity != temporary_identity:
            raise RuntimeError(
                f"transactional temporary pathname changed after preparation: {temporary}"
            )
        prepared_temporary = _file_rollback_state(temporary)
        if (
            not prepared_temporary.existed
            or prepared_temporary.identity != temporary_identity
            or prepared_temporary.content != content
            or prepared_temporary.generation is None
            or prepared_temporary.parent_generation is None
        ):
            raise RuntimeError(
                f"transactional temporary ownership is unavailable: {temporary}"
            )
        if temporary.name in parent_entries_before_temporary:
            raise RuntimeError(
                f"transactional temporary conflicts with parent entry baseline: {temporary}"
            )
        temporary_entry = _directory_entry_snapshot(temporary.lstat())
        if temporary_entry.identity != temporary_identity:
            raise RuntimeError(
                f"transactional temporary pathname changed after preparation: {temporary}"
            )
        expected_parent_entries = dict(parent_entries_before_temporary)
        expected_parent_entries[temporary.name] = temporary_entry
        if ownership is not None:
            ownership[0] = temporary_identity

        current = _file_rollback_state(path)
        if not _same_rollback_state(current, baseline, include_parent=False):
            raise RuntimeError(f"transactional memory output changed before publication: {path}")
        if (
            current.parent_generation is None
            or baseline.parent_generation is None
            or prepared_temporary.parent_generation is None
            or current.parent_generation.identity
            != baseline.parent_generation.identity
            or current.parent_generation != prepared_temporary.parent_generation
        ):
            raise RuntimeError(
                f"transactional memory output parent changed before publication: {path.parent}"
            )
        if replace_existing and current.existed:
            if current.identity is None or current.generation is None:
                raise RuntimeError(f"transactional memory output ownership is unavailable: {path}")
            captured_target = expected_parent_entries.get(path.name)
            if captured_target is None or captured_target.identity != current.identity:
                raise RuntimeError(
                    f"transactional memory output parent entries changed before publication: {path.parent}"
                )
            if publication_state is not None:
                publication_state[0] = _FileRollbackState(
                    existed=False,
                    parent_generation=current.parent_generation,
                )
            _identity_bound_unlink(
                path,
                current.identity,
                expected_content=current.content,
                expected_generation=current.generation,
                expected_parent_generation=current.parent_generation,
            )
            del expected_parent_entries[path.name]
        elif current.existed:
            raise FileExistsError(errno.EEXIST, "transactional output already exists", str(path))

        publication_baseline = _file_rollback_state(path)
        if (
            publication_baseline.existed
            or publication_baseline.parent_generation is None
            or publication_baseline.parent_generation.identity
            != baseline.parent_generation.identity
        ):
            raise RuntimeError(
                f"transactional memory output parent changed before link: {path.parent}"
            )
        if publication_state is not None:
            publication_state[0] = publication_baseline
        authorizing_state = _file_rollback_state(path)
        if not _same_rollback_state(authorizing_state, publication_baseline):
            raise RuntimeError(
                f"transactional memory output changed immediately before publication: {path}"
            )
        if _parent_entry_snapshot(path.parent) != expected_parent_entries:
            raise RuntimeError(
                f"transactional memory parent entries changed before link: {path.parent}"
            )
        os.link(temporary, path, follow_symlinks=False)
        published = _file_rollback_state(path)
        if (
            not published.existed
            or published.identity != temporary_identity
            or published.content != content
            or published.parent_generation is None
            or published.parent_generation.identity
            != authorizing_state.parent_generation.identity
        ):
            raise RuntimeError(f"transactional memory output ownership changed during publication: {path}")
        if publication_state is not None:
            publication_state[0] = published
        cleanup_prepared_temporary()
        published = _file_rollback_state(path)
        if (
            not published.existed
            or published.identity != temporary_identity
            or published.content != content
            or published.parent_generation is None
            or published.parent_generation.identity
            != authorizing_state.parent_generation.identity
        ):
            raise RuntimeError(f"transactional memory output ownership changed after publication: {path}")
        if publication_state is not None:
            publication_state[0] = published
        return temporary_identity
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if descriptor is not None:
            raw_descriptor = descriptor
            descriptor = None
            try:
                os.close(raw_descriptor)
            except BaseException as close_exc:
                cleanup_errors.append(close_exc)
        try:
            cleanup_prepared_temporary()
        except FileNotFoundError:
            pass
        except BaseException as unlink_exc:
            cleanup_errors.append(unlink_exc)
        if (
            primary_error is not None
            and publication_state is not None
            and temporary_identity is not None
        ):
            try:
                published_after_failure = _file_rollback_state(path)
            except BaseException as ownership_error:
                cleanup_errors.append(ownership_error)
            else:
                if (
                    published_after_failure.existed
                    and published_after_failure.identity == temporary_identity
                    and published_after_failure.content == content
                ):
                    publication_state[0] = published_after_failure
        if cleanup_errors:
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    _append_exception_notes(primary_error, cleanup_error)
            else:
                cleanup_error = cleanup_errors[0]
                for secondary_error in cleanup_errors[1:]:
                    _append_exception_notes(cleanup_error, secondary_error)
                raise cleanup_error


def _memory_transaction_effects(conn: Any) -> _MemoryTransactionEffects:
    effects = _ACTIVE_MEMORY_TRANSACTION.get()
    if effects is None or effects.connection is not conn:
        raise RuntimeError("memory filesystem side effect requires the active writer transaction")
    return effects


def _register_memory_rollback(conn: Any, action: Callable[[], None]) -> None:
    effects = _memory_transaction_effects(conn)
    effects.rollback_actions.append(action)


def _transactional_ensure_directory(conn: Any, path: Path) -> None:
    effects = _memory_transaction_effects(conn)
    owned_directories = _create_physical_directory_chain(path, effects.project)
    if owned_directories:
        _register_memory_rollback(
            conn,
            lambda: _remove_owned_empty_directories(owned_directories),
        )


def _transactional_ensure_layout(conn: Any, project: Path) -> None:
    for directory in (_memory_dir(project), _logs_dir(project), _views_dir(project)):
        _transactional_ensure_directory(conn, directory)
    for log_name in REQUIRED_LOGS:
        log_path = _logs_dir(project) / log_name
        try:
            _regular_file_object_identity(log_path)
        except FileNotFoundError:
            _transactional_write_bytes(conn, log_path, b"")


def _transactional_write_bytes(
    conn: Any,
    path: Path,
    content: bytes,
    *,
    require_absent: bool = False,
) -> None:
    effects = _memory_transaction_effects(conn)
    path, _missing_directories = _physical_file_path(path, effects.project)
    previous = _file_rollback_state(path)
    if require_absent and previous.existed:
        raise FileExistsError(errno.EEXIST, "transactional output already exists", str(path))
    if previous.existed and previous.content == content:
        return
    owned_directories = _create_physical_directory_chain(path.parent, effects.project)
    publication_baseline = _file_rollback_state(path)
    if previous.parent_generation is not None and not _same_rollback_state(
        publication_baseline,
        previous,
    ):
        raise RuntimeError(f"transactional memory output changed before publication: {path}")
    predecessor_owned_state = effects.transactional_owned_baselines.get(path)
    owned_state: list[_FileRollbackState | None] = [None]
    if publication_baseline.parent_generation is None:
        raise RuntimeError(f"transactional memory output parent generation is unavailable: {path.parent}")
    parent_generation = effects.transactional_parent_generations.setdefault(
        path.parent,
        [publication_baseline.parent_generation],
    )
    if parent_generation[0] != publication_baseline.parent_generation:
        raise RuntimeError(
            f"transactional memory output parent changed before publication: {path.parent}"
        )

    def restore() -> None:
        def publish_predecessor(restored: _FileRollbackState) -> None:
            if predecessor_owned_state is not None:
                predecessor_owned_state[0] = restored

        def restore_output() -> None:
            current = _file_rollback_state(path)
            if current.parent_generation != parent_generation[0]:
                raise RuntimeError(
                    "transactional memory output changed outside rollback ownership: "
                    f"{path} (parent generation changed: {path.parent})"
                )
            if previous.existed:
                if _same_rollback_state(current, previous, include_parent=False):
                    publish_predecessor(current)
                    return
                if not current.existed:
                    restored_state: list[_FileRollbackState | None] = [None]
                    _atomic_replace_bytes(
                        path,
                        previous.content,
                        mode=previous.mode,
                        replace_existing=False,
                        expected_state=current,
                        publication_state=restored_state,
                    )
                    restored = restored_state[0]
                    if restored is None or restored.identity is None:
                        raise RuntimeError(
                            f"transactional memory rollback did not own restored output: {path}"
                        )
                    restored = _restore_owned_file_bytes(
                        path,
                        restored.identity,
                        previous.content,
                        previous.content,
                        mode=previous.mode,
                        atime_ns=previous.atime_ns,
                        mtime_ns=previous.mtime_ns,
                    )
                    parent_generation[0] = _directory_generation(path.parent)
                    publish_predecessor(restored)
                    return
                owned = owned_state[0]
                if (
                    owned is None
                    or owned.identity is None
                    or not _same_rollback_state(current, owned, include_parent=False)
                ):
                    raise RuntimeError(f"transactional memory output changed outside rollback ownership: {path}")
                restored = _restore_owned_file_bytes(
                    path,
                    owned.identity,
                    content,
                    previous.content,
                    mode=previous.mode,
                    atime_ns=previous.atime_ns,
                    mtime_ns=previous.mtime_ns,
                    expected_generation=owned.generation,
                    expected_parent_generation=parent_generation[0],
                )
                parent_generation[0] = _directory_generation(path.parent)
                publish_predecessor(restored)
                return
            if not current.existed:
                return
            owned = owned_state[0]
            if (
                owned is None
                or owned.identity is None
                or owned.generation is None
                or not _same_rollback_state(current, owned, include_parent=False)
            ):
                raise RuntimeError(f"new transactional memory output changed outside rollback ownership: {path}")
            _identity_bound_unlink(
                path,
                owned.identity,
                expected_content=content,
                expected_generation=owned.generation,
                expected_parent_generation=parent_generation[0],
            )
            parent_generation[0] = _directory_generation(path.parent)

        _run_compensation("transactional memory output rollback", restore_output, owned_directories)

    _register_memory_rollback(conn, restore)
    effects.transactional_owned_baselines[path] = owned_state
    try:
        _atomic_replace_bytes(
            path,
            content,
            mode=previous.mode if previous.existed else None,
            replace_existing=previous.existed,
            expected_state=publication_baseline,
            publication_state=owned_state,
        )
    except BaseException as publication_error:
        try:
            current = _file_rollback_state(path)
            owned = owned_state[0]
            publication_still_owned = (
                owned is not None
                and _same_rollback_state(current, owned, include_parent=False)
            )
            baseline_still_present = _same_rollback_state(
                current,
                publication_baseline,
                include_parent=False,
            )
            baseline_was_detached = (
                previous.existed and owned is not None and not current.existed
            )
            if (
                current.parent_generation is not None
                and (publication_still_owned or baseline_still_present or baseline_was_detached)
            ):
                parent_generation[0] = current.parent_generation
        except BaseException as generation_error:
            _append_exception_notes(publication_error, generation_error)
        raise
    owned = owned_state[0]
    if owned is None or owned.parent_generation is None:
        raise RuntimeError(f"transactional memory output publication generation is unavailable: {path}")
    parent_generation[0] = owned.parent_generation


def _transactional_write_text(
    conn: Any,
    path: Path,
    content: str,
    *,
    require_absent: bool = False,
) -> None:
    _transactional_write_bytes(
        conn,
        path,
        content.encode("utf-8"),
        require_absent=require_absent,
    )


def _transactional_write_json(conn: Any, path: Path, data: dict[str, Any]) -> None:
    _transactional_write_text(conn, path, json.dumps(data, indent=2) + "\n")


def _transactional_append_jsonl(conn: Any, path: Path, row: dict[str, Any]) -> None:
    effects = _memory_transaction_effects(conn)
    path = Path(os.path.abspath(path))
    _transactional_ensure_directory(conn, path.parent)
    line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    path_key = path
    state = effects.transactional_jsonl.get(path_key)
    flags = os.O_APPEND | os.O_RDWR
    try:
        descriptor, current_identity = _open_identity_bound_regular(path, flags)
    except FileNotFoundError:
        _transactional_write_bytes(conn, path, b"", require_absent=True)
        descriptor, current_identity = _open_identity_bound_regular(path, flags)
    locked = False
    primary_error: BaseException | None = None
    try:
        _lock_descriptor_exclusive(descriptor)
        locked = True
        if _regular_file_object_identity(path) != current_identity:
            raise RuntimeError(f"transactional memory log pathname changed before append: {path}")
        if state is None:
            baseline = _read_descriptor_bytes(descriptor)
            previous = _owned_file_rollback_state(
                path,
                descriptor,
                current_identity,
                baseline,
            )
            state = _JsonlRollbackState(
                previous=previous,
                identity=current_identity,
                owned_state=[previous],
                predecessor_owned_state=effects.transactional_owned_baselines.get(path_key),
            )
            effects.transactional_jsonl[path_key] = state

            def restore() -> None:
                if state is None or state.identity is None:
                    raise RuntimeError(f"transactional memory log has no owned identity: {path}")
                owned = state.owned_state[0]
                if (
                    owned is None
                    or owned.identity is None
                    or owned.generation is None
                    or owned.parent_generation is None
                ):
                    raise RuntimeError(f"transactional memory log has no owned baseline: {path}")
                current = _file_rollback_state(path)
                if not _same_rollback_state(current, owned):
                    raise RuntimeError(
                        f"transactional memory log changed outside rollback ownership: {path}"
                    )
                if _same_rollback_state(current, state.previous):
                    restored = current
                else:
                    restored = _restore_owned_file_bytes(
                        path,
                        owned.identity,
                        owned.content,
                        state.previous.content,
                        mode=state.previous.mode,
                        atime_ns=state.previous.atime_ns,
                        mtime_ns=state.previous.mtime_ns,
                        expected_generation=owned.generation,
                        expected_parent_generation=owned.parent_generation,
                    )
                if (
                    restored.identity is None
                    or restored.generation is None
                    or restored.parent_generation is None
                    or restored.content != state.previous.content
                    or restored.mode != state.previous.mode
                    or restored.mtime_ns != state.previous.mtime_ns
                    or restored.generation.identity != restored.identity
                    or restored.generation.link_count != owned.generation.link_count
                    or restored.parent_generation != owned.parent_generation
                ):
                    raise RuntimeError(
                        f"transactional memory log rollback baseline is unstable: {path}"
                    )
                if state.predecessor_owned_state is not None:
                    state.predecessor_owned_state[0] = restored

            _register_memory_rollback(conn, restore)
            effects.transactional_owned_baselines[path_key] = state.owned_state
        elif state.identity != current_identity:
            raise RuntimeError(f"transactional memory log identity changed before append: {path}")
        if state.previous.identity != current_identity:
            raise RuntimeError(f"transactional memory log replaced before append: {path}")
        expected_before = state.previous.content + bytes(state.owned_suffix)
        if _read_descriptor_bytes(descriptor) != expected_before:
            raise RuntimeError(f"transactional memory log changed before append: {path}")
        offset = 0
        while offset < len(line):
            written = os.write(descriptor, line[offset:])
            if written <= 0:
                raise OSError(f"transactional memory log append made no progress: {path}")
            state.owned_suffix.extend(line[offset:offset + written])
            offset += written
            state.owned_state[0] = _owned_file_rollback_state(
                path,
                descriptor,
                current_identity,
                state.previous.content + bytes(state.owned_suffix),
                expected_parent_generation=state.previous.parent_generation,
            )
        os.fsync(descriptor)
        state.owned_state[0] = _owned_file_rollback_state(
            path,
            descriptor,
            current_identity,
            state.previous.content + bytes(state.owned_suffix),
            expected_parent_generation=state.previous.parent_generation,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _release_descriptor(
            descriptor,
            locked=locked,
            primary_error=primary_error,
        )


def _transactional_move_file(conn: Any, source: Path, destination: Path) -> None:
    effects = _memory_transaction_effects(conn)
    source, missing_source_directories = _physical_file_path(
        source,
        effects.project,
    )
    if missing_source_directories:
        raise RuntimeError(f"transactional memory move source is not a file: {source}")
    destination, _missing_destination_directories = _physical_file_path(
        destination,
        effects.project,
    )
    source_parent_identity = _directory_object_identity(source.parent)
    source_parent_generation = _directory_generation(source.parent)
    try:
        source_identity = _file_identity_state(source)
    except (FileNotFoundError, RuntimeError):
        raise RuntimeError(f"transactional memory move source is not a file: {source}")
    source_object_identity = _regular_file_object_identity(source)
    source_generation = _file_entry_generation(source)
    if source_object_identity != _FileObjectIdentity(
        device=source_identity.device,
        inode=source_identity.inode,
    ):
        raise RuntimeError(f"transactional memory move source changed while identifying it: {source}")
    if (
        source_generation.identity != source_object_identity
        or _directory_generation(source.parent) != source_parent_generation
    ):
        raise RuntimeError(f"transactional memory move source generation changed: {source}")
    if _path_entry_exists(destination):
        raise RuntimeError(f"transactional memory move destination already exists: {destination}")
    owned_directories = _create_physical_directory_chain(destination.parent, effects.project)
    try:
        destination_parent_identity = _directory_object_identity(destination.parent)
    except BaseException as exc:
        try:
            _remove_owned_empty_directories(owned_directories)
        except BaseException as cleanup_exc:
            failure = RuntimeError("transactional memory move parent setup cleanup failed")
            _append_exception_notes(failure, cleanup_exc)
            raise failure from exc
        raise
    if source_parent_identity == destination_parent_identity:
        raise RuntimeError(
            "transactional memory move source and destination have the same physical parent: "
            f"{source.parent}"
        )
    link_created = [False]
    linked_generation: list[_FileEntryGeneration | None] = [None]
    moved_generation: list[_FileEntryGeneration | None] = [None]
    linked_parent_generation: list[_DirectoryGeneration | None] = [None]
    moved_parent_generation: list[_DirectoryGeneration | None] = [None]
    linked_source_generation: list[_FileEntryGeneration | None] = [None]
    removed_source_parent_generation: list[_DirectoryGeneration | None] = [None]

    def restore() -> None:
        def restore_move() -> None:
            if not link_created[0]:
                return
            if _directory_object_identity(source.parent) != source_parent_identity:
                raise RuntimeError(f"transactional memory move source parent changed: {source.parent}")
            if _directory_object_identity(destination.parent) != destination_parent_identity:
                raise RuntimeError(
                    f"transactional memory move destination parent changed: {destination.parent}"
                )
            source_exists = _path_entry_exists(source)
            destination_exists = _path_entry_exists(destination)
            if source_exists and not destination_exists:
                if _file_identity_state(source) != source_identity:
                    raise RuntimeError(f"transactional memory move source identity changed: {source}")
                if (
                    linked_source_generation[0] is None
                    or _file_entry_generation(source) != linked_source_generation[0]
                ):
                    raise RuntimeError(
                        f"transactional memory move source generation changed: {source}"
                    )
                if _directory_generation(source.parent) != source_parent_generation:
                    raise RuntimeError(
                        f"transactional memory move source parent generation changed: {source.parent}"
                    )
                return
            if not source_exists and destination_exists:
                if _file_identity_state(destination) != source_identity:
                    raise RuntimeError(f"transactional memory move destination identity changed: {destination}")
                destination_generation_before_relink = _file_entry_generation(destination)
                if (
                    moved_generation[0] is None
                    or destination_generation_before_relink != moved_generation[0]
                ):
                    raise RuntimeError(
                        f"transactional memory move destination generation changed: {destination}"
                    )
                destination_parent_generation_before_relink = _directory_generation(destination.parent)
                if (
                    moved_parent_generation[0] is None
                    or destination_parent_generation_before_relink != moved_parent_generation[0]
                ):
                    raise RuntimeError(
                        f"transactional memory move destination parent generation changed: {destination.parent}"
                    )
                if _directory_object_identity(source.parent) != source_parent_identity:
                    raise RuntimeError(f"transactional memory move source parent changed: {source.parent}")
                if (
                    removed_source_parent_generation[0] is None
                    or _directory_generation(source.parent)
                    != removed_source_parent_generation[0]
                ):
                    raise RuntimeError(
                        f"transactional memory move source parent generation changed: {source.parent}"
                    )
                if _directory_object_identity(destination.parent) != destination_parent_identity:
                    raise RuntimeError(
                        f"transactional memory move destination parent changed: {destination.parent}"
                    )
                if source_parent_identity == destination_parent_identity:
                    raise RuntimeError(
                        "transactional memory move rollback ownership cannot be proven for destination "
                        f"with a shared physical parent: {destination}"
                )
                os.link(destination, source, follow_symlinks=False)
                restored_generation = _file_entry_generation(destination)
                if (
                    _directory_generation(destination.parent)
                    != destination_parent_generation_before_relink
                ):
                    raise RuntimeError(
                        "transactional memory move destination parent generation changed during rollback "
                        f"relink: {destination}"
                    )
                if _file_identity_state(source) != source_identity:
                    raise RuntimeError(f"transactional memory move failed to restore source identity: {source}")
                _identity_bound_unlink(
                    destination,
                    source_object_identity,
                    expected_generation=restored_generation,
                    expected_parent_generation=destination_parent_generation_before_relink,
                )
                return
            if source_exists and destination_exists:
                if (
                    _file_identity_state(source) == source_identity
                    and _file_identity_state(destination) == source_identity
                ):
                    if (
                        linked_source_generation[0] is None
                        or _file_entry_generation(source) != linked_source_generation[0]
                    ):
                        raise RuntimeError(
                            f"transactional memory move source generation changed: {source}"
                        )
                    if _directory_generation(source.parent) != source_parent_generation:
                        raise RuntimeError(
                            f"transactional memory move source parent generation changed: {source.parent}"
                        )
                    if (
                        linked_generation[0] is None
                        or _file_entry_generation(destination) != linked_generation[0]
                    ):
                        raise RuntimeError(
                            f"transactional memory move destination generation changed: {destination}"
                        )
                    if (
                        linked_parent_generation[0] is None
                        or _directory_generation(destination.parent) != linked_parent_generation[0]
                    ):
                        raise RuntimeError(
                            f"transactional memory move destination parent generation changed: {destination.parent}"
                        )
                    _identity_bound_unlink(
                        destination,
                        source_object_identity,
                        expected_generation=linked_generation[0],
                        expected_parent_generation=linked_parent_generation[0],
                    )
                    return
                raise RuntimeError(
                    "transactional memory move has conflicting source and destination identities: "
                    f"{source}, {destination}"
                )
            raise RuntimeError(
                "transactional memory move changed outside rollback ownership: "
                f"source={source_exists}, destination={destination_exists}"
            )

        _run_compensation("transactional memory move rollback", restore_move, owned_directories)

    _register_memory_rollback(conn, restore)
    if _directory_object_identity(source.parent) != source_parent_identity:
        raise RuntimeError(f"transactional memory move source parent changed: {source.parent}")
    if _directory_generation(source.parent) != source_parent_generation:
        raise RuntimeError(
            f"transactional memory move source parent generation changed: {source.parent}"
        )
    if _file_entry_generation(source) != source_generation:
        raise RuntimeError(f"transactional memory move source generation changed: {source}")
    if _directory_object_identity(destination.parent) != destination_parent_identity:
        raise RuntimeError(f"transactional memory move destination parent changed: {destination.parent}")
    os.link(source, destination, follow_symlinks=False)
    link_created[0] = True
    if _directory_object_identity(source.parent) != source_parent_identity:
        raise RuntimeError(f"transactional memory move source parent changed after link: {source.parent}")
    if _directory_object_identity(destination.parent) != destination_parent_identity:
        raise RuntimeError(
            f"transactional memory move destination parent changed after link: {destination.parent}"
        )
    if _file_identity_state(destination) != source_identity:
        raise RuntimeError(f"transactional memory move did not preserve source identity: {destination}")
    linked_generation[0] = _file_entry_generation(destination)
    linked_parent_generation[0] = _directory_generation(destination.parent)
    linked_source_generation[0] = _file_entry_generation(source)
    if linked_source_generation[0].identity != source_object_identity:
        raise RuntimeError(f"transactional memory move source changed before removal: {source}")
    if _directory_generation(source.parent) != source_parent_generation:
        raise RuntimeError(
            f"transactional memory move source parent generation changed before removal: {source.parent}"
        )
    _identity_bound_unlink(
        source,
        source_object_identity,
        expected_generation=linked_source_generation[0],
        expected_parent_generation=source_parent_generation,
    )
    if _directory_object_identity(source.parent) != source_parent_identity:
        raise RuntimeError(f"transactional memory move source parent changed after removal: {source.parent}")
    if _directory_object_identity(destination.parent) != destination_parent_identity:
        raise RuntimeError(
            f"transactional memory move destination parent changed after removal: {destination.parent}"
        )
    if _path_entry_exists(source) or _file_identity_state(destination) != source_identity:
        raise RuntimeError(f"transactional memory move did not preserve source identity: {destination}")
    removed_source_parent_generation[0] = _directory_generation(source.parent)
    moved_generation[0] = _file_entry_generation(destination)
    moved_parent_generation[0] = _directory_generation(destination.parent)


def _rollback_memory_effects(effects: _MemoryTransactionEffects) -> list[BaseException]:
    errors: list[BaseException] = []
    for action in reversed(effects.rollback_actions):
        try:
            action()
        except BaseException as exc:
            errors.append(exc)
    return errors

def _configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_READONLY_MAIN_MUTATION_ACTIONS = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_INSERT",
        "SQLITE_UPDATE",
        "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_DROP_VTABLE",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
    )
    if hasattr(sqlite3, name)
)
_READONLY_GLOBAL_MUTATION_ACTIONS = frozenset(
    getattr(sqlite3, name)
    for name in ("SQLITE_ATTACH", "SQLITE_DETACH")
    if hasattr(sqlite3, name)
)


def _readonly_snapshot_authorizer(
    action: int,
    _argument_one: str | None,
    _argument_two: str | None,
    database_name: str | None,
    _trigger_name: str | None,
) -> int:
    if action in _READONLY_GLOBAL_MUTATION_ACTIONS:
        return sqlite3.SQLITE_DENY
    if database_name == "main" and action in _READONLY_MAIN_MUTATION_ACTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK

def _connect(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(project), timeout=SQLITE_CONNECT_TIMEOUT_SECONDS)
    try:
        return _configure_connection(conn)
    except BaseException as exc:
        _close_connection(conn, primary_error=exc)
        raise


def _connect_readonly_db(db_path: Path) -> sqlite3.Connection:
    """Return a filesystem-inert SQLite snapshot of the canonical memory DB.

    SQLite read-only connections may still create WAL or shared-memory files.
    This reader therefore captures a stable main-database byte image while no
    cooperative writer or SQLite sidecar is present, then deserializes only the
    private copy into RAM.  Ambiguous capture state fails closed.
    """
    from .freshness_projection import writer_lock_path

    database = Path(os.path.abspath(db_path))
    if (
        database.name != DB_FILENAME
        or database.parent.name != MEMORY_DIRNAME
        or database.parent.parent.name != CONTROL_DIRNAME
    ):
        raise sqlite3.OperationalError(
            f"read-only memory database path is not canonical: {database}"
        )
    project = database.parent.parent.parent
    sidecars = (
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    )
    lock_path = writer_lock_path(project)

    def require_absent(path: Path) -> None:
        try:
            path.lstat()
        except FileNotFoundError:
            return
        raise RuntimeError(f"read-only memory snapshot boundary is occupied: {path}")

    try:
        physical_database, missing = _physical_file_path(database, project)
        if missing or physical_database != database:
            raise RuntimeError(f"read-only memory database path is unavailable: {database}")
        project_generation = _directory_generation(project)
        memory_generation = _directory_generation(database.parent)
        require_absent(lock_path)
        for sidecar in sidecars:
            require_absent(sidecar)
        baseline = _file_rollback_state(
            database,
            capture_content=True,
            require_single_link=True,
        )
        if not baseline.existed or baseline.identity is None:
            raise RuntimeError(f"read-only memory database is unavailable: {database}")
        if baseline.parent_generation != memory_generation:
            raise RuntimeError(
                f"read-only memory database parent changed during capture: {database.parent}"
            )
        if _directory_generation(project) != project_generation:
            raise RuntimeError(
                f"read-only memory project changed during capture: {project}"
            )
        require_absent(lock_path)
        for sidecar in sidecars:
            require_absent(sidecar)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise sqlite3.OperationalError(
            f"unable to capture a write-free memory database snapshot: {database}"
        ) from exc

    image = bytearray(baseline.content)
    if len(image) < 100 or image[:16] != b"SQLite format 3\x00":
        raise sqlite3.DatabaseError(f"file is not a database: {database}")
    format_versions = (image[18], image[19])
    if format_versions == (2, 2):
        # sqlite3_deserialize rejects a WAL-mode header.  Only the private RAM
        # copy is normalized; the persisted database is never opened by SQLite.
        image[18] = 1
        image[19] = 1
    elif format_versions != (1, 1):
        raise sqlite3.DatabaseError(
            f"unsupported SQLite file format versions {format_versions}: {database}"
        )

    def validate_capture_boundary() -> None:
        try:
            require_absent(lock_path)
            for sidecar in sidecars:
                require_absent(sidecar)
            current = _file_rollback_state(
                database,
                capture_content=True,
                require_single_link=True,
            )
            if not _same_rollback_state(current, baseline):
                raise RuntimeError(f"read-only memory database changed during capture: {database}")
            if _directory_generation(project) != project_generation:
                raise RuntimeError(
                    f"read-only memory project changed during capture: {project}"
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise sqlite3.OperationalError(
                f"memory database snapshot could not be validated without writes: {database}"
            ) from exc

    conn = sqlite3.connect(":memory:", timeout=SQLITE_CONNECT_TIMEOUT_SECONDS)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA temp_store = MEMORY")
        deserialize = getattr(conn, "deserialize", None)
        if not callable(deserialize):
            raise sqlite3.NotSupportedError(
                "this SQLite runtime cannot deserialize a write-free memory snapshot"
            )
        deserialize(bytes(image))
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        conn.set_authorizer(_readonly_snapshot_authorizer)
        validate_capture_boundary()
        return conn
    except BaseException as exc:
        _close_connection(conn, primary_error=exc)
        raise


@contextmanager
def _readonly_memory_connection(project: Path):
    conn = _connect_readonly_db(_db_path(project))
    primary_error: BaseException | None = None
    try:
        yield conn
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _close_connection(conn, primary_error=primary_error)


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("DROP TABLE IF EXISTS temp.cc_fts_probe")
        conn.execute("CREATE VIRTUAL TABLE temp.cc_fts_probe USING fts5(content)")
        conn.execute("DROP TABLE temp.cc_fts_probe")
    except sqlite3.Error:
        try:
            conn.execute("DROP TABLE IF EXISTS temp.cc_fts_probe")
        except sqlite3.Error:
            pass
        return False
    return True


def _ensure_retrieval_fts(conn: sqlite3.Connection) -> bool:
    return ensure_retrieval_fts(conn)


def _initial_database_artifacts(project: Path) -> tuple[Path, ...]:
    database = _db_path(project)
    return (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    )


def _database_artifact_baselines(
    project: Path,
    *,
    capture_content: bool = False,
) -> dict[Path, _DatabaseArtifactBaseline]:
    artifacts = _initial_database_artifacts(project)
    baselines: dict[Path, _DatabaseArtifactBaseline] = {}

    def capture(path: Path, *, with_content: bool) -> _DatabaseArtifactBaseline:
        try:
            details = path.lstat()
        except FileNotFoundError:
            return _DatabaseArtifactBaseline(existed=False)
        regular = stat.S_ISREG(details.st_mode) and not getattr(details, "st_reparse_tag", 0)
        if not regular:
            raise RuntimeError(
                f"memory database artifact must be a physical regular file: {path}"
            )
        initial_identity = _object_identity(details)
        captured = _file_rollback_state(
            path,
            capture_content=with_content,
            require_single_link=True,
        )
        if (
            not captured.existed
            or captured.identity is None
            or captured.identity != initial_identity
        ):
            raise RuntimeError(
                f"memory database artifact changed while capturing rollback baseline: {path}"
            )
        return _DatabaseArtifactBaseline(
            existed=True,
            regular=regular,
            identity=captured.identity,
            rollback=captured if with_content else None,
        )

    database = artifacts[0]
    main_baseline = capture(database, with_content=capture_content)
    baselines[database] = main_baseline
    effective_capture_content = capture_content or not main_baseline.existed
    for path in artifacts[1:]:
        baselines[path] = capture(path, with_content=effective_capture_content)
    return baselines


def _capture_owned_database_artifacts(
    baselines: dict[Path, _DatabaseArtifactBaseline],
    owned: dict[Path, _FileObjectIdentity],
) -> None:
    for path, baseline in baselines.items():
        if baseline.existed:
            continue
        try:
            current = _regular_file_object_identity(path)
        except FileNotFoundError:
            continue
        expected = owned.get(path)
        if expected is None:
            owned[path] = current
        elif expected != current:
            raise RuntimeError(f"new memory database artifact identity changed: {path}")


def _cleanup_failed_initial_database(
    baselines: dict[Path, _DatabaseArtifactBaseline],
    owned: dict[Path, _FileObjectIdentity],
) -> list[BaseException]:
    errors: list[BaseException] = []
    for path, baseline in baselines.items():
        if baseline.existed:
            continue
        try:
            current = _regular_file_object_identity(path)
        except FileNotFoundError:
            continue
        except BaseException as exc:
            errors.append(exc)
            continue
        try:
            expected = owned.get(path)
            if expected is None or current != expected:
                raise RuntimeError(f"new memory database artifact is not rollback-owned: {path}")
            _identity_bound_unlink(path, expected)
        except BaseException as exc:
            errors.append(exc)
    return errors


def _restore_committed_database_artifacts(
    baselines: dict[Path, _DatabaseArtifactBaseline],
    owned: dict[Path, _FileObjectIdentity],
) -> list[BaseException]:
    errors: list[BaseException] = []
    for path, baseline in baselines.items():
        if not baseline.existed:
            continue
        try:
            previous = baseline.rollback
            if not baseline.regular or previous is None or previous.identity is None:
                raise RuntimeError(f"memory database rollback baseline is unavailable: {path}")
            current = _file_rollback_state(path)
            if not current.existed:
                restored_identity: list[_FileObjectIdentity | None] = [None]
                _atomic_replace_bytes(
                    path,
                    previous.content,
                    mode=previous.mode,
                    replace_existing=False,
                    ownership=restored_identity,
                )
                if restored_identity[0] is None:
                    raise RuntimeError(f"memory database rollback did not own restored artifact: {path}")
                _restore_owned_file_bytes(
                    path,
                    restored_identity[0],
                    previous.content,
                    previous.content,
                    mode=previous.mode,
                    atime_ns=previous.atime_ns,
                    mtime_ns=previous.mtime_ns,
                )
                continue
            if current.identity != previous.identity:
                raise RuntimeError(f"memory database artifact identity changed after commit: {path}")
            _restore_owned_file_bytes(
                path,
                current.identity,
                current.content,
                previous.content,
                mode=previous.mode,
                atime_ns=previous.atime_ns,
                mtime_ns=previous.mtime_ns,
            )
        except BaseException as exc:
            errors.append(exc)
    errors.extend(_cleanup_failed_initial_database(baselines, owned))
    return errors


@contextmanager
def _memory_connection(
    project: Path,
    *,
    compensate_committed_failure: bool = False,
):
    """Open a coordinated SQLite writer and publish only its committed snapshot.

    The projection lock intentionally spans connect, filesystem/snapshot
    capture, commit, close, stable DB fingerprinting, and atomic publication.  This is the sole
    writer coordination point because all SQLite writers use this helper.
    """
    from .freshness_projection import (
        build_legacy_status_snapshot,
        capture_filesystem_fingerprint,
        capture_source_fingerprint,
        projection_path,
        projection_writer_lock,
        publish_freshness_projection,
    )

    writer_owned_directories: list[_OwnedDirectory] = []
    committed = False
    effects: _MemoryTransactionEffects | None = None
    effects_rolled_back = False
    artifact_baselines: dict[Path, _DatabaseArtifactBaseline] | None = None
    owned_database_artifacts: dict[Path, _FileObjectIdentity] = {}
    projection_file = projection_path(project)
    projection_baseline: _FileRollbackState | None = None
    projection_owned: list[_FileRollbackState | None] = [None]
    projection_publication_attempted = False

    def restore_projection() -> None:
        if projection_baseline is None:
            return
        current = _file_rollback_state(projection_file)
        if (
            projection_baseline.parent_generation is None
            or current.parent_generation is None
            or projection_baseline.parent_generation.identity
            != current.parent_generation.identity
        ):
            raise RuntimeError(
                f"memory projection parent changed outside rollback ownership: "
                f"{projection_file.parent}"
            )
        if _same_rollback_state(current, projection_baseline, include_parent=False):
            return
        if projection_baseline.existed and not current.existed:
            restored_state: list[_FileRollbackState | None] = [None]
            _atomic_replace_bytes(
                projection_file,
                projection_baseline.content,
                mode=projection_baseline.mode,
                replace_existing=False,
                expected_state=current,
                publication_state=restored_state,
            )
            restored = restored_state[0]
            if restored is None or restored.identity is None:
                raise RuntimeError(
                    f"memory projection rollback ownership is unavailable: {projection_file}"
                )
            _restore_owned_file_bytes(
                projection_file,
                restored.identity,
                projection_baseline.content,
                projection_baseline.content,
                mode=projection_baseline.mode,
                atime_ns=projection_baseline.atime_ns,
                mtime_ns=projection_baseline.mtime_ns,
            )
            return
        owned = projection_owned[0]
        if (
            owned is None
            or not owned.existed
            or owned.identity is None
            or owned.generation is None
            or not current.existed
            or not _same_rollback_state(current, owned)
        ):
            raise RuntimeError(
                f"memory projection changed outside rollback ownership: {projection_file}"
            )
        if projection_baseline.existed:
            _restore_owned_file_bytes(
                projection_file,
                owned.identity,
                owned.content,
                projection_baseline.content,
                mode=projection_baseline.mode,
                atime_ns=projection_baseline.atime_ns,
                mtime_ns=projection_baseline.mtime_ns,
            )
            return
        _identity_bound_unlink(
            projection_file,
            owned.identity,
            expected_content=owned.content,
            expected_generation=owned.generation,
            expected_parent_generation=owned.parent_generation,
        )

    def compensate_failure(
        exc: BaseException,
        *,
        preserve_primary_exception: bool = False,
    ) -> None:
        nonlocal effects_rolled_back
        cleanup_errors: list[BaseException] = []
        first_initialization = (
            artifact_baselines is not None
            and not artifact_baselines[_db_path(project)].existed
        )
        if (
            committed
            and effects is not None
            and not effects_rolled_back
            and (first_initialization or compensate_committed_failure)
        ):
            cleanup_errors.extend(_rollback_memory_effects(effects))
            effects_rolled_back = True
        database_artifacts_restored = False
        if artifact_baselines is not None and (
            first_initialization
            or (compensate_committed_failure and committed)
        ):
            cleanup_errors.extend(
                _restore_committed_database_artifacts(
                    artifact_baselines,
                    owned_database_artifacts,
                )
            )
            database_artifacts_restored = True
        if (
            first_initialization
            and not database_artifacts_restored
            and artifact_baselines is not None
        ):
            cleanup_errors.extend(
                _cleanup_failed_initial_database(
                    artifact_baselines,
                    owned_database_artifacts,
                )
            )
        if writer_owned_directories:
            try:
                _remove_owned_empty_directories(writer_owned_directories)
            except BaseException as directory_exc:
                cleanup_errors.append(directory_exc)
        if cleanup_errors:
            if preserve_primary_exception:
                exc.add_note(
                    "memory transaction cleanup did not restore the initial filesystem"
                )
                for cleanup_error in cleanup_errors:
                    _append_exception_notes(exc, cleanup_error)
                return
            for cleanup_error in cleanup_errors:
                if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit)):
                    _append_exception_notes(cleanup_error, exc)
                    for other_error in cleanup_errors:
                        if other_error is not cleanup_error:
                            _append_exception_notes(cleanup_error, other_error)
                    raise cleanup_error
            cleanup_message = "memory transaction cleanup did not restore the initial filesystem"
            failure = RuntimeError(
                cleanup_message
            )
            for cleanup_error in cleanup_errors:
                _append_exception_notes(failure, cleanup_error)
            raise failure from exc

    @contextmanager
    def compensating_projection_writer_lock():
        body_failed = False
        try:
            with projection_writer_lock(project):
                try:
                    yield
                except BaseException:
                    body_failed = True
                    raise
        except BaseException as exc:
            if not body_failed:
                compensate_failure(exc, preserve_primary_exception=True)
            raise

    with compensating_projection_writer_lock():
            try:
                writer_owned_directories = _create_physical_directory_chain(
                    _memory_dir(project),
                    project,
                )
                artifact_baselines = _database_artifact_baselines(
                    project,
                    capture_content=compensate_committed_failure,
                )
                projection_baseline = _file_rollback_state(projection_file)
                try:
                    conn = _connect(project)
                except BaseException:
                    _capture_owned_database_artifacts(artifact_baselines, owned_database_artifacts)
                    raise
                _capture_owned_database_artifacts(artifact_baselines, owned_database_artifacts)
                effects = _MemoryTransactionEffects(connection=conn, project=project)
                effects.rollback_actions.append(restore_projection)
                transaction_token = _ACTIVE_MEMORY_TRANSACTION.set(effects)
                projection_status: dict[str, Any] | None = None
                projection_session_evidence: list[dict[str, Any]] | None = None
                projection_graph_evidence: dict[str, dict[str, int]] | None = None
                filesystem_fingerprint: dict[str, Any] | None = None
                connection_error: BaseException | None = None
                try:
                    try:
                        conn.execute("BEGIN")
                        yield conn
                        if conn.total_changes:
                            filesystem_before, reason = capture_filesystem_fingerprint(project)
                            if filesystem_before is None:
                                raise RuntimeError(
                                    f"memory projection filesystem fingerprint unstable: {reason}"
                                )
                            (
                                projection_status,
                                projection_session_evidence,
                                projection_graph_evidence,
                            ) = build_legacy_status_snapshot(project, conn)
                            filesystem_after, reason = capture_filesystem_fingerprint(project)
                            if filesystem_after is None:
                                raise RuntimeError(
                                    f"memory projection filesystem fingerprint unstable: {reason}"
                                )
                            if filesystem_before != filesystem_after:
                                raise RuntimeError("memory projection filesystem changed during snapshot")
                            filesystem_fingerprint = filesystem_after
                        conn.commit()
                        committed = True
                    except BaseException as exc:
                        rollback_errors: list[BaseException] = []
                        try:
                            _capture_owned_database_artifacts(
                                artifact_baselines,
                                owned_database_artifacts,
                            )
                        except BaseException as ownership_exc:
                            rollback_errors.append(ownership_exc)
                        try:
                            if conn.in_transaction:
                                conn.rollback()
                        except BaseException as rollback_exc:
                            rollback_errors.append(rollback_exc)
                        rollback_errors.extend(_rollback_memory_effects(effects))
                        effects_rolled_back = True
                        if rollback_errors:
                            failure = RuntimeError(
                                "memory transaction rollback did not restore every owned side effect"
                            )
                            for rollback_error in rollback_errors:
                                _append_exception_notes(failure, rollback_error)
                            raise failure from exc
                        raise
                except BaseException as exc:
                    connection_error = exc
                    raise
                finally:
                    try:
                        _close_connection(
                            conn,
                            primary_error=connection_error,
                        )
                    finally:
                        _ACTIVE_MEMORY_TRANSACTION.reset(transaction_token)

                _capture_owned_database_artifacts(artifact_baselines, owned_database_artifacts)

                if (
                    committed
                    and projection_status is not None
                    and projection_session_evidence is not None
                    and projection_graph_evidence is not None
                    and filesystem_fingerprint is not None
                ):
                    fingerprint, reason = capture_source_fingerprint(project)
                    if fingerprint is None:
                        raise RuntimeError(f"memory projection fingerprint unstable: {reason}")
                    projection_publication_baseline = _file_rollback_state(projection_file)
                    if (
                        not _same_rollback_state(
                            projection_publication_baseline,
                            projection_baseline,
                            include_parent=False,
                        )
                        or projection_publication_baseline.parent_generation is None
                        or projection_baseline.parent_generation is None
                        or projection_publication_baseline.parent_generation.identity
                        != projection_baseline.parent_generation.identity
                    ):
                        raise RuntimeError(
                            f"memory projection changed before publication: {projection_file}"
                        )
                    projection_publication_attempted = True
                    publish_freshness_projection(
                        project,
                        projection_status,
                        projection_session_evidence,
                        projection_graph_evidence,
                        fingerprint,
                        filesystem_fingerprint,
                        expected_state=projection_publication_baseline,
                        publication_state=projection_owned,
                    )
                    if projection_owned[0] is None:
                        raise RuntimeError(
                            f"memory projection publication ownership is unavailable: {projection_file}"
                        )
            except BaseException as exc:
                compensate_failure(
                    exc,
                    preserve_primary_exception=projection_publication_attempted,
                )
                raise
def _ensure_schema(conn: sqlite3.Connection) -> None:
    migrate_schema(conn)

def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        (key, value),
    )

def _get_metadata(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default

def _relative_path(project: Path, path_value: str | Path) -> str:
    path = Path(path_value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(project.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()

def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _content_hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _require_initialized(project: Path) -> tuple[bool, str]:
    if not _manifest_path(project).exists():
        return False, "Memory manifest is missing. Run: cc memory init"
    if not _db_path(project).exists():
        return False, "Memory database is missing. Run: cc memory init"
    return True, ""

def _print_json_or_text(json_output: bool, payload: dict[str, Any], text: str) -> None:
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(text)


def _print_json_error_or_text(
    json_output: bool,
    error: str,
    message: str,
    text: str | None = None,
    **extra: Any,
) -> None:
    payload = {"ok": False, "error": error, "message": message}
    payload.update(extra)
    _print_json_or_text(json_output, payload, text or f"Error: {message}")


def _doctor_schema_issues(conn: sqlite3.Connection) -> list[str]:
    inspection = inspect_schema(conn)
    if inspection.state in {"current", "legacy_adoptable", "old_compatible"}:
        return []
    payload = inspection.to_payload()
    issues = [f"schemaState={payload['schemaState']}"]
    issues.extend(str(item) for item in payload.get("issues", []))
    for table in payload.get("missingTables", []):
        issues.append(f"missing table: {table}")
    for table, columns in payload.get("missingColumns", {}).items():
        issues.append(f"missing columns in {table}: {', '.join(columns)}")
    return issues


def _validate_jsonl(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle, start=1):
                if line.strip():
                    json.loads(line)
    except json.JSONDecodeError as exc:
        return f"invalid JSONL at line {index}: {exc}"
    except OSError as exc:
        return f"unreadable: {exc}"
    return ""
