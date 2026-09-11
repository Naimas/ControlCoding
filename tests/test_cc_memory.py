#!/usr/bin/env python3
"""Tests for the Project Memory Engine CLI surface."""

import ast
import errno
import hashlib
import io
import json
import multiprocessing
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import traceback
import zipfile
from copy import deepcopy
from contextlib import contextmanager, redirect_stdout
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
if "cc" in sys.modules:
    del sys.modules["cc"]
import cc
import cc_feature
import cc_memory
import cc_memory_lib.bootstrap as memory_bootstrap
import cc_memory_lib.chunks as memory_chunks
import cc_memory_lib.commands as memory_commands
import cc_memory_lib.cross_plane as cross_plane
import cc_memory_lib.export as memory_export
import cc_memory_lib.entities as memory_entities
import cc_memory_lib.freshness_projection as freshness_projection
import cc_memory_lib.graph as memory_graph
import cc_memory_lib.impact as memory_impact
import cc_memory_lib.lifecycle as memory_lifecycle
import cc_memory_lib.ledger as memory_ledger
import cc_memory_lib.op_index as memory_op_index
import cc_memory_lib.migrations as memory_migrations
import cc_memory_lib.rag_pack as rag_pack
import cc_memory_lib.retrieve as memory_retrieve
import cc_memory_lib.scanner as memory_scanner
import cc_memory_lib.scoring as memory_scoring
import cc_memory_lib.sessions as memory_sessions
import cc_memory_lib.store as memory_store
import cc_memory_lib.vector as memory_vector
import cc_memory_lib.views as memory_views
import cc_memory_lib.work_features as work_features
import cc_memory_lib.work_project_map as work_project_map
import cc_memory_lib.work_dashboard as work_dashboard
import cc_memory_lib.work_review_queue as work_review_queue


def _p3b2_decision_worker(request: dict, result_queue, transaction_boundary) -> None:
    """Apply one decision after both spawned workers reach the real lock boundary."""
    try:
        original_acquire = work_review_queue._acquire_lock

        def synchronized_acquire(lock_path: Path, timeout_seconds: float = 5.0) -> None:
            transaction_boundary.wait(timeout=10)
            original_acquire(lock_path, timeout_seconds=timeout_seconds)

        work_review_queue._acquire_lock = synchronized_acquire
        output = io.StringIO()
        with redirect_stdout(output):
            status = work_features.cmd_scan_review(SimpleNamespace(
                project_root=Path(request["project"]),
                path="",
                review_status="",
                sensitivity="",
                note=request.get("note", ""),
                filter="all",
                batch=False,
                item=request["item"],
                action=request["action"],
                canonical=request.get("canonical", ""),
                fingerprint=request["fingerprint"],
                proposal=False,
                output=None,
                limit=50,
                json_output=True,
            ))
        payload = json.loads(output.getvalue())
        result = {"status": status, "ok": status == 0}
        if status != 0:
            result["error"] = payload.get("error", payload)
        result_queue.put(result)
    except BaseException:
        result_queue.put({"status": -1, "error": traceback.format_exc()})


def _p3c0_projection_writer_worker(project_text: str, worker_id: int, boundary, start_event, result_queue) -> None:
    """Spawn target that reaches the real projection lock through _memory_connection."""
    try:
        project = Path(project_text)
        boundary.wait(timeout=15)
        if not start_event.wait(timeout=15):
            raise TimeoutError("writer start event timed out")
        now = "2026-08-12T00:00:00Z"
        with memory_store._memory_connection(project) as conn:
            conn.execute(
                """
                INSERT INTO entities(
                  id, type, title, project_short, plane, path, lifecycle,
                  source_refs, provenance, body, data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"P3C0WRITER{worker_id}",
                    "note",
                    f"P3-C0 writer {worker_id}",
                    "P3C0",
                    "controlcoding_dev",
                    f"notes/p3c0-writer-{worker_id}.md",
                    "active",
                    "[]",
                    "{}",
                    "writer concurrency probe",
                    "{}",
                    now,
                    now,
                ),
            )
        result_queue.put({"ok": True, "worker": worker_id})
    except BaseException:
        result_queue.put({"ok": False, "worker": worker_id, "error": traceback.format_exc()})


def _p3c0_intake_record_worker(project_text: str, worker_id: int, boundary, result_queue) -> None:
    """Race two transactionally guarded intake records against one path."""
    try:
        project = Path(project_text)
        destination = project / "docs" / "inbox" / "concurrent-intake.md"
        content = b"# Concurrent intake\n"
        boundary.wait(timeout=15)
        entity = memory_entities._record_entity(
            project,
            "note",
            f"Concurrent intake {worker_id}",
            area="Inbox",
            path="docs/inbox/concurrent-intake.md",
            command="p3c0 intake concurrency probe",
            transactional_filesystem_action=lambda conn: memory_store._transactional_write_bytes(
                conn,
                destination,
                content,
                require_absent=True,
            ),
            require_unique_path=True,
        )
        result_queue.put({"ok": True, "entity": entity["id"]})
    except FileExistsError as exc:
        result_queue.put({"ok": False, "conflict": True, "error": str(exc)})
    except BaseException:
        result_queue.put({"ok": False, "conflict": False, "error": traceback.format_exc()})


def _run_main(args: list[str]) -> int:
    with patch.object(sys, "argv", ["cc.py", *args]):
        return cc.main()


def _p3c0_file_snapshot(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "isFile": path.is_file(),
        "size": stat.st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
        "mtime_ns": stat.st_mtime_ns,
        "identity": (stat.st_dev, stat.st_ino),
    }


def _p3c0_hardlink_snapshot(path: Path) -> dict:
    details = path.lstat()
    return {
        "bytes": path.read_bytes(),
        "identity": (details.st_dev, details.st_ino),
        "linkCount": details.st_nlink,
        "modeType": stat.S_IFMT(details.st_mode),
        "reparseTag": getattr(details, "st_reparse_tag", 0),
    }


def _p3c0_is_directory_not_empty_error(error: BaseException) -> bool:
    return isinstance(error, OSError) and (
        error.errno in {errno.ENOTEMPTY, errno.EEXIST}
        or getattr(error, "winerror", None) == 145
    )


def _p3c0_is_windows_symlink_privilege_error(
    error: OSError,
    *,
    platform_name: str | None = None,
) -> bool:
    effective_platform = os.name if platform_name is None else platform_name
    return (
        effective_platform == "nt"
        and getattr(error, "winerror", None) == 1314
    )


def _p3c0_skip_if_windows_symlink_privilege_error(
    error: OSError,
    context: str,
    *,
    platform_name: str | None = None,
) -> None:
    if _p3c0_is_windows_symlink_privilege_error(
        error,
        platform_name=platform_name,
    ):
        pytest.skip(f"{context}: {error}")
    raise error


def _p3c0_create_directory_link(
    link: Path,
    target: Path,
    *,
    platform_name: str | None = None,
) -> str:
    effective_platform = os.name if platform_name is None else platform_name
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError as symlink_error:
        if not _p3c0_is_windows_symlink_privilege_error(
            symlink_error,
            platform_name=effective_platform,
        ):
            raise
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip() or (
                f"mklink exited with status {completed.returncode}"
            )
            raise RuntimeError(
                "Windows directory junction creation failed after symlink "
                f"WinError 1314: {detail}"
            ) from symlink_error
        return "junction"


def _p3c0_synthetic_os_error(message: str, *, winerror: int | None = None) -> OSError:
    error = OSError(errno.EPERM, message)
    if winerror is not None:
        error.winerror = winerror
    return error


@pytest.mark.parametrize(
    ("platform_name", "winerror", "expected"),
    [
        ("posix", 1314, "raise"),
        ("nt", 5, "raise"),
        ("nt", 1314, "skip"),
    ],
    ids=[
        "posix-oserror-reraised",
        "windows-winerror-5-reraised",
        "windows-winerror-1314-skipped",
    ],
)
def test_p3c0_symlink_capability_policy(platform_name, winerror, expected):
    error = _p3c0_synthetic_os_error(
        f"synthetic {platform_name} symlink failure",
        winerror=winerror,
    )

    if expected == "skip":
        with pytest.raises(pytest.skip.Exception):
            _p3c0_skip_if_windows_symlink_privilege_error(
                error,
                "synthetic symlink capability",
                platform_name=platform_name,
            )
        return

    with pytest.raises(OSError) as caught:
        _p3c0_skip_if_windows_symlink_privilege_error(
            error,
            "synthetic symlink capability",
            platform_name=platform_name,
        )
    assert caught.value is error


def test_p3c0_directory_link_junction_failure_is_not_skipped(tmp_path, monkeypatch):
    link = tmp_path / "junction-link"
    target = tmp_path / "junction-target"
    target.mkdir()
    symlink_error = _p3c0_synthetic_os_error(
        "synthetic symlink privilege failure",
        winerror=1314,
    )
    junction_calls: list[list[str]] = []
    skip_calls: list[str] = []

    def fail_symlink(*_args, **_kwargs):
        raise symlink_error

    def fail_junction(command, **_kwargs):
        junction_calls.append(command)
        return SimpleNamespace(
            returncode=1,
            stderr="synthetic junction setup failure",
            stdout="",
        )

    def unexpected_skip(reason):
        skip_calls.append(str(reason))
        raise AssertionError(f"junction failure was converted into skip: {reason}")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(Path, "symlink_to", fail_symlink)
        patch_context.setattr(subprocess, "run", fail_junction)
        patch_context.setattr(pytest, "skip", unexpected_skip)
        with pytest.raises(RuntimeError, match="junction") as caught:
            _p3c0_create_directory_link(link, target, platform_name="nt")

    assert caught.value.__cause__ is symlink_error
    assert len(junction_calls) == 1
    assert junction_calls[0][:4] == ["cmd.exe", "/d", "/c", "mklink"]
    assert skip_calls == []
    assert not link.exists()


def test_p3c0_symlink_capability_policy_is_statically_narrow():
    source = Path(__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = {
        node.name: node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    skip_helper = "_p3c0_skip_if_windows_symlink_privilege_error"
    direct_link_tests = {
        "test_p3c0_writer_rejects_symlink_database_before_connect",
        "test_p3c0_transaction_move_rejects_dangling_destination_without_overwrite",
        "test_p3c0_intake_promote_classifies_dangling_source_as_non_file",
    }
    required = {
        skip_helper,
        "_p3c0_create_directory_link",
        *direct_link_tests,
    }
    assert required <= functions.keys()

    directory_helper = functions["_p3c0_create_directory_link"]
    directory_skip_calls = [
        node
        for node in ast.walk(directory_helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
        and node.func.attr == "skip"
    ]
    assert directory_skip_calls == []

    for function_name in direct_link_tests:
        helper_calls = [
            node
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == skip_helper
        ]
        assert len(helper_calls) == 1
        broad_error_sets = [
            {
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, int)
            }
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Set)
        ]
        assert not any({5, 1314} <= values for values in broad_error_sets)


def _p3c0_directory_snapshot(root: Path) -> list[tuple[str, dict]]:
    if not root.exists():
        return []
    return [
        (path.relative_to(root).as_posix(), _p3c0_file_snapshot(path))
        for path in sorted(root.rglob("*"))
    ]


def _p3c0_memory_bytes_snapshot(project: Path) -> list[tuple[str, bytes]]:
    control_root = project / ".controlcoding"
    return [
        (path.relative_to(control_root).as_posix(), path.read_bytes())
        for path in sorted(control_root.rglob("*"))
        if path.is_file()
    ]


def _p3c0_watch_public_intake_descendants(
    patch_context,
    *forbidden_roots: Path,
) -> dict[str, bool]:
    roots = tuple(Path(path) for path in forbidden_roots)
    path_type = type(roots[0])
    real_resolve = path_type.resolve
    real_exists = path_type.exists
    real_stat = path_type.stat
    real_lstat = path_type.lstat
    real_open = path_type.open
    real_read_bytes = path_type.read_bytes
    real_unique = memory_commands._unique_project_file
    real_move = memory_commands._transactional_move_file
    real_hash = memory_commands._content_hash
    real_rollback_state = memory_store._file_rollback_state
    real_path_entry_exists = memory_store._path_entry_exists
    real_regular_identity = memory_store._regular_file_object_identity
    real_file_identity_state = memory_store._file_identity_state
    real_identity_open = memory_store._open_identity_bound_regular
    real_os_open = memory_store.os.open
    real_link = memory_store.os.link
    real_replace = memory_store.os.replace
    boundaries = {
        "resolve": False,
        "exists": False,
        "stat": False,
        "lstat": False,
        "open": False,
        "read": False,
        "unique": False,
        "move": False,
        "hash": False,
        "rollback": False,
        "path_entry": False,
        "regular_identity": False,
        "file_identity": False,
        "identity_open": False,
        "os_open": False,
        "link": False,
        "replace": False,
    }

    def is_forbidden(path_value) -> bool:
        observed = Path(path_value)
        return any(observed != root and root in observed.parents for root in roots)

    def observe_resolve(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["resolve"] = True
        return real_resolve(path, *args, **kwargs)

    def observe_exists(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["exists"] = True
        return real_exists(path, *args, **kwargs)

    def observe_stat(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["stat"] = True
        return real_stat(path, *args, **kwargs)

    def observe_lstat(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["lstat"] = True
        return real_lstat(path, *args, **kwargs)

    def observe_open(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["open"] = True
        return real_open(path, *args, **kwargs)

    def observe_read_bytes(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["read"] = True
        return real_read_bytes(path, *args, **kwargs)

    def observe_unique(path):
        if is_forbidden(path):
            boundaries["unique"] = True
        return real_unique(path)

    def observe_move(conn, source, destination):
        if is_forbidden(source) or is_forbidden(destination):
            boundaries["move"] = True
        return real_move(conn, source, destination)

    def observe_hash(path):
        if is_forbidden(path):
            boundaries["hash"] = True
        return real_hash(path)

    def observe_rollback_state(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["rollback"] = True
        return real_rollback_state(path, *args, **kwargs)

    def observe_path_entry_exists(path):
        if is_forbidden(path):
            boundaries["path_entry"] = True
        return real_path_entry_exists(path)

    def observe_regular_identity(path):
        if is_forbidden(path):
            boundaries["regular_identity"] = True
        return real_regular_identity(path)

    def observe_file_identity_state(path):
        if is_forbidden(path):
            boundaries["file_identity"] = True
        return real_file_identity_state(path)

    def observe_identity_open(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["identity_open"] = True
        return real_identity_open(path, *args, **kwargs)

    def observe_os_open(path, *args, **kwargs):
        if is_forbidden(path):
            boundaries["os_open"] = True
        return real_os_open(path, *args, **kwargs)

    def observe_link(source, destination, *args, **kwargs):
        if is_forbidden(source) or is_forbidden(destination):
            boundaries["link"] = True
        return real_link(source, destination, *args, **kwargs)

    def observe_replace(source, destination, *args, **kwargs):
        if is_forbidden(source) or is_forbidden(destination):
            boundaries["replace"] = True
        return real_replace(source, destination, *args, **kwargs)

    patch_context.setattr(path_type, "resolve", observe_resolve)
    patch_context.setattr(path_type, "exists", observe_exists)
    patch_context.setattr(path_type, "stat", observe_stat)
    patch_context.setattr(path_type, "lstat", observe_lstat)
    patch_context.setattr(path_type, "open", observe_open)
    patch_context.setattr(path_type, "read_bytes", observe_read_bytes)
    patch_context.setattr(memory_commands, "_unique_project_file", observe_unique)
    patch_context.setattr(memory_commands, "_transactional_move_file", observe_move)
    patch_context.setattr(memory_commands, "_content_hash", observe_hash)
    patch_context.setattr(memory_store, "_file_rollback_state", observe_rollback_state)
    patch_context.setattr(memory_store, "_path_entry_exists", observe_path_entry_exists)
    patch_context.setattr(memory_store, "_regular_file_object_identity", observe_regular_identity)
    patch_context.setattr(memory_store, "_file_identity_state", observe_file_identity_state)
    patch_context.setattr(memory_store, "_open_identity_bound_regular", observe_identity_open)
    patch_context.setattr(memory_store.os, "open", observe_os_open)
    patch_context.setattr(memory_store.os, "link", observe_link)
    patch_context.setattr(memory_store.os, "replace", observe_replace)
    return boundaries


def _p3c0_exercise_locked_descriptor_path(call_path: str, path: Path) -> None:
    baseline = b'{"baseline":true}\n'
    path.write_bytes(baseline)
    identity = memory_store._regular_file_object_identity(path)
    details = path.lstat()
    if call_path == "restore-owned":
        memory_store._restore_owned_file_bytes(
            path,
            identity,
            baseline,
            b'{"restored":true}\n',
            mode=stat.S_IMODE(details.st_mode),
            atime_ns=details.st_atime_ns,
            mtime_ns=details.st_mtime_ns,
        )
        return
    if call_path == "validate-owned":
        memory_store._validate_owned_file_bytes(path, identity, baseline)
        return
    if call_path == "append-jsonl":
        conn = object()
        effects = memory_store._MemoryTransactionEffects(connection=conn, project=path.parent)
        token = memory_store._ACTIVE_MEMORY_TRANSACTION.set(effects)
        try:
            memory_store._transactional_append_jsonl(conn, path, {"appended": True})
        finally:
            memory_store._ACTIVE_MEMORY_TRANSACTION.reset(token)
        return
    raise AssertionError(f"unknown descriptor call path: {call_path}")


def _p3c0_cleanup_owned_descriptors(
    descriptors,
    real_fstat,
    real_close,
    *,
    primary_error: BaseException | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    for descriptor in dict.fromkeys(descriptors):
        descriptor_is_open = True
        try:
            real_fstat(descriptor)
        except OSError as probe_error:
            if probe_error.errno == errno.EBADF:
                descriptor_is_open = False
            else:
                cleanup_errors.append(probe_error)
        except BaseException as probe_error:
            cleanup_errors.append(probe_error)
        if not descriptor_is_open:
            continue
        try:
            real_close(descriptor)
        except BaseException as close_error:
            cleanup_errors.append(close_error)

    if not cleanup_errors:
        return
    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            memory_store._append_exception_notes(primary_error, cleanup_error)
        return
    first_error = cleanup_errors[0]
    for cleanup_error in cleanup_errors[1:]:
        memory_store._append_exception_notes(first_error, cleanup_error)
    raise first_error


def _p3c0_cleanup_owned_connections(
    connections,
    *,
    primary_error: BaseException | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    for connection in dict.fromkeys(connections):
        connection_is_open = True
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError as probe_error:
            if "closed" in str(probe_error).lower():
                connection_is_open = False
            else:
                cleanup_errors.append(probe_error)
        except BaseException as probe_error:
            cleanup_errors.append(probe_error)
        if not connection_is_open:
            continue
        try:
            connection.close()
        except BaseException as close_error:
            cleanup_errors.append(close_error)

    if not cleanup_errors:
        return
    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            memory_store._append_exception_notes(primary_error, cleanup_error)
        return
    first_error = cleanup_errors[0]
    for cleanup_error in cleanup_errors[1:]:
        memory_store._append_exception_notes(first_error, cleanup_error)
    raise first_error


def _p3c0_cleanup_owned_paths(
    owned_paths,
    *,
    primary_error: BaseException | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    ownership_by_path = {}
    for path, identity in owned_paths:
        candidate = Path(path)
        previous_identity = ownership_by_path.setdefault(candidate, identity)
        if previous_identity != identity:
            cleanup_errors.append(
                RuntimeError(f"conflicting harness path ownership: {candidate}")
            )
    for path, expected_identity in ownership_by_path.items():
        try:
            details = path.lstat()
            observed_identity = (details.st_dev, details.st_ino)
            if (
                not stat.S_ISREG(details.st_mode)
                or getattr(details, "st_reparse_tag", 0)
                or observed_identity != expected_identity
            ):
                raise RuntimeError(f"harness path ownership changed: {path}")
            final_details = path.lstat()
            if (final_details.st_dev, final_details.st_ino) != expected_identity:
                raise RuntimeError(f"harness path ownership changed before cleanup: {path}")
            path.unlink()
        except FileNotFoundError:
            continue
        except BaseException as unlink_error:
            cleanup_errors.append(unlink_error)
    if not cleanup_errors:
        return
    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            memory_store._append_exception_notes(primary_error, cleanup_error)
        return
    first_error = cleanup_errors[0]
    for cleanup_error in cleanup_errors[1:]:
        memory_store._append_exception_notes(first_error, cleanup_error)
    raise first_error


def _p3c0_assert_descriptor_closed(descriptor: int, real_fstat) -> None:
    try:
        real_fstat(descriptor)
    except OSError as closed:
        assert closed.errno == errno.EBADF
        return
    raise AssertionError(f"descriptor remained open: {descriptor}")


def _p3c0_create_wal_fixture(project: Path, capsys) -> tuple[Path, sqlite3.Connection]:
    assert cc.cmd_memory_init(project, mode="document-only", project_short="P3C0") == 0
    capsys.readouterr()
    database = project / ".controlcoding" / "memory" / "memory.db"
    conn = sqlite3.connect(database)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE p3c0_wal_probe(value TEXT NOT NULL)")
    conn.execute("INSERT INTO p3c0_wal_probe(value) VALUES ('committed-in-wal')")
    conn.commit()
    return database, conn


def _p3c0_init_project(project: Path, capsys) -> Path:
    assert cc.cmd_memory_init(project, mode="document-only", project_short="P3C0") == 0
    capsys.readouterr()
    assert freshness_projection.observe_freshness(project)["state"] == "fresh"
    return project / ".controlcoding" / "memory" / "memory.db"


def _p3c0_projection_payload(project: Path) -> dict:
    return json.loads(freshness_projection.projection_path(project).read_text(encoding="utf-8"))


def _p3c0_republish_current(project: Path) -> dict:
    payload = _p3c0_projection_payload(project)
    fingerprint, reason = freshness_projection.capture_source_fingerprint(project)
    assert reason == ""
    assert fingerprint is not None
    filesystem, reason = freshness_projection.capture_filesystem_fingerprint(project)
    assert reason == ""
    assert filesystem is not None
    freshness_projection.publish_freshness_projection(
        project,
        payload["legacyStatus"],
        payload["sessionEvidence"],
        payload["graphEvidence"],
        fingerprint,
        filesystem,
    )
    return _p3c0_projection_payload(project)


def _p3c0_commit_projection_marker(project: Path, marker: str) -> dict:
    """Exercise the real writer publication path after a filesystem fixture change."""
    with memory_store._memory_connection(project) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("p3c0_r3_projection_marker", marker),
        )
    payload = _p3c0_projection_payload(project)
    assert freshness_projection.observe_freshness(project)["state"] == "fresh"
    return payload


def _p3c0_replace_with_identical_bytes(path: Path) -> tuple[dict, dict]:
    before = _p3c0_file_snapshot(path)
    replacement = path.with_name(f".{path.name}.p3c0-replacement")
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    after = _p3c0_file_snapshot(path)
    assert before["identity"] != after["identity"]
    assert before["sha256"] == after["sha256"]
    return before, after


def _p3c0_assert_projection_rejected(project: Path, payload: dict, reason: str = "projection_incoherent") -> dict:
    projection = freshness_projection.projection_path(project)
    projection.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    observation = freshness_projection.observe_freshness(project)
    assert observation["state"] == "unknown"
    assert observation["color"] == "YELLOW"
    assert observation["reason"] == reason
    assert observation["legacyStatus"] is None
    public_status = freshness_projection.projected_status_payload(project)
    assert public_status["available"] is False
    assert public_status["health"]["state"] == "unknown"
    assert public_status["health"]["color"] == "YELLOW"
    assert public_status["health"]["reason"] == reason
    return observation


def _p3c0_assert_unknown_yellow(observation: dict) -> None:
    assert observation["state"] == "unknown"
    assert observation["color"] == "YELLOW"
    assert observation["legacyStatus"] is None


def test_p3c0_r5_manifest_claims_fail_closed_for_real_absent_and_wrong_type_inputs(tmp_path, capsys):
    real_manifest = tmp_path / "real-manifest"
    _p3c0_init_project(real_manifest, capsys)
    forged = _p3c0_projection_payload(real_manifest)
    dev_plane = forged["legacyStatus"]["bootstrap"]["devPlane"]
    dev_plane["hasManifest"] = False
    dev_plane["initialized"] = False
    dev_plane["manifest"] = {}
    _p3c0_assert_projection_rejected(real_manifest, forged)

    absent_manifest = tmp_path / "absent-manifest"
    _p3c0_init_project(absent_manifest, capsys)
    absent_projection = _p3c0_projection_payload(absent_manifest)
    assert absent_projection["legacyStatus"]["bootstrap"]["devPlane"]["hasManifest"] is True
    (absent_manifest / ".controlcoding" / "module_manifest.json").unlink()
    absent = freshness_projection.observe_freshness(absent_manifest)
    _p3c0_assert_unknown_yellow(absent)
    assert absent["reason"] == "projection_outdated"

    wrong_type_manifest = tmp_path / "wrong-type-manifest"
    _p3c0_init_project(wrong_type_manifest, capsys)
    wrong_type_projection = _p3c0_projection_payload(wrong_type_manifest)
    assert wrong_type_projection["legacyStatus"]["bootstrap"]["devPlane"]["hasManifest"] is True
    manifest_path = wrong_type_manifest / ".controlcoding" / "module_manifest.json"
    manifest_path.unlink()
    manifest_path.mkdir()
    wrong_type = freshness_projection.observe_freshness(wrong_type_manifest)
    _p3c0_assert_unknown_yellow(wrong_type)
    assert wrong_type["reason"] == "source_filesystem_manifest_not_regular"


def test_p3c0_r5_link_claims_and_captured_facts_must_match_real_bytes(tmp_path, capsys):
    absent_link = tmp_path / "absent-link"
    _p3c0_init_project(absent_link, capsys)
    forged = _p3c0_projection_payload(absent_link)
    project_plane = forged["legacyStatus"]["bootstrap"]["projectPlane"]
    assert forged["filesystemFingerprint"]["linkFacts"] == {}
    project_plane["attachedExternal"] = True
    project_plane["link"]["exists"] = True
    _p3c0_assert_projection_rejected(absent_link, forged)

    forged_path = _p3c0_projection_payload(absent_link)
    forged_path["legacyStatus"]["bootstrap"]["projectPlane"]["link"]["path"] = ".controlwork/forged-link.json"
    _p3c0_assert_projection_rejected(absent_link, forged_path)

    linked_project = tmp_path / "linked-project"
    external_project = tmp_path / "external-project"
    _p3c0_init_project(linked_project, capsys)
    (external_project / ".controlwork" / "memory").mkdir(parents=True)
    (external_project / "CONTROLWORK.md").write_text("# External ControlWork\n", encoding="utf-8")
    (external_project / ".controlwork" / "config.json").write_text("{}\n", encoding="utf-8")
    link_path = linked_project / ".controlwork" / "link.json"
    link_path.parent.mkdir(parents=True)
    link = {
        "schemaVersion": 1,
        "mode": "linked_external_controlwork",
        "externalPath": str(external_project),
        "externalContext": str(external_project / "CONTROLWORK.md"),
        "syncPolicy": "manual_explicit",
        "attachedAt": "2026-08-13T00:00:00Z",
    }
    link_path.write_text(json.dumps(link, indent=2) + "\n", encoding="utf-8")
    coherent = _p3c0_commit_projection_marker(linked_project, "linked-input")

    link["syncPolicy"] = "manual_review_required"
    link_path.write_text(json.dumps(link, indent=2) + "\n", encoding="utf-8")
    current_filesystem, reason = freshness_projection.capture_filesystem_fingerprint(linked_project)
    assert reason == ""
    assert current_filesystem is not None
    assert current_filesystem["linkFacts"]["syncPolicy"] == "manual_review_required"

    stale_claim = deepcopy(coherent)
    stale_claim["filesystemFingerprint"] = current_filesystem
    assert stale_claim["legacyStatus"]["bootstrap"]["projectPlane"]["link"]["syncPolicy"] == "manual_explicit"
    _p3c0_assert_projection_rejected(linked_project, stale_claim)

    forged_attachment = deepcopy(coherent)
    forged_attachment["filesystemFingerprint"]["externalAttachment"]["root"] = str(
        external_project / "forged-root"
    )
    _p3c0_assert_projection_rejected(linked_project, forged_attachment)


def test_p3c0_r4_controlwork_root_presence_type_and_identity_invalidate_projection(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    work_root = tmp_path / ".controlwork"

    work_root.mkdir()
    _p3c0_assert_unknown_yellow(freshness_projection.observe_freshness(tmp_path))

    _p3c0_commit_projection_marker(tmp_path, "controlwork-root-present")
    before_identity = (work_root.stat().st_dev, work_root.stat().st_ino)
    displaced = tmp_path / ".controlwork-displaced"
    work_root.rename(displaced)
    work_root.mkdir()
    after_identity = (work_root.stat().st_dev, work_root.stat().st_ino)
    assert after_identity != before_identity
    _p3c0_assert_unknown_yellow(freshness_projection.observe_freshness(tmp_path))

    work_root.rmdir()
    displaced.rename(work_root)
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"

    work_root.rmdir()
    _p3c0_assert_unknown_yellow(freshness_projection.observe_freshness(tmp_path))
    _p3c0_commit_projection_marker(tmp_path, "controlwork-root-absent")

    work_root.write_text("not a directory\n", encoding="utf-8")
    _p3c0_assert_unknown_yellow(freshness_projection.observe_freshness(tmp_path))


def test_p3c0_r4_config_json_is_validated_from_the_captured_bytes(tmp_path, capsys, monkeypatch):
    invalid_documents = [
        (b"{not-json\n", "filesystem_project_config_malformed"),
        (b"[]\n", "filesystem_project_config_malformed"),
        (
            json.dumps({
                **memory_commands._controlwork_config(),
                "schemaVersion": 999,
            }, sort_keys=True).encode("utf-8"),
            "filesystem_project_config_incompatible",
        ),
    ]
    for index, (raw, expected_reason) in enumerate(invalid_documents):
        project = tmp_path / f"invalid-config-{index}"
        _p3c0_init_project(project, capsys)
        config_path = project / ".controlwork" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes(raw)

        captured, reason = freshness_projection.capture_filesystem_fingerprint(project)
        assert captured is None
        assert reason == expected_reason
        observation = freshness_projection.observe_freshness(project)
        _p3c0_assert_unknown_yellow(observation)
        assert observation["reason"] == expected_reason

    project = tmp_path / "valid-config"
    _p3c0_init_project(project, capsys)
    config = memory_commands._controlwork_config()
    raw = (json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    config_path = project / ".controlwork" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(raw)

    parsed_inputs = []
    real_strict_loads = freshness_projection._strict_json_loads

    def track_strict_input(value):
        parsed_inputs.append(value)
        return real_strict_loads(value)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "_strict_json_loads", track_strict_input)
        captured, reason = freshness_projection.capture_filesystem_fingerprint(project)
    assert reason == ""
    assert captured is not None
    assert parsed_inputs == [raw.decode("utf-8")]
    config_record = captured["contentFiles"][".controlwork/config.json"]
    assert config_record["sha256"] == hashlib.sha256(raw).hexdigest()
    assert captured["configFacts"] == {
        "exists": True,
        "schemaVersion": config["schemaVersion"],
        "product": config["product"],
        "distribution": config["distribution"],
        "standaloneCompatible": config["standaloneCompatible"],
        "canonicalContext": config["canonicalContext"],
    }
    projection = _p3c0_commit_projection_marker(project, "same-byte-config-capture")
    assert projection["filesystemFingerprint"]["contentFiles"][".controlwork/config.json"] == config_record
    assert projection["filesystemFingerprint"]["configFacts"] == captured["configFacts"]
    assert freshness_projection.observe_freshness(project)["state"] == "fresh"


def test_p3c0_r4_artifact_exists_count_and_latest_file_must_match_inventory(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    packet_root = tmp_path / ".controlcoding" / "context-packets"
    packet_root.mkdir(parents=True, exist_ok=True)
    packet = packet_root / "observed.md"
    packet.write_text("# Observed packet\n", encoding="utf-8")
    valid = _p3c0_commit_projection_marker(tmp_path, "artifact-inventory-binding")
    artifact = valid["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    assert artifact["exists"] is True
    assert artifact["fileCount"] == 1
    assert artifact["latestFile"]["path"] == ".controlcoding/context-packets/observed.md"

    count_mismatch = deepcopy(valid)
    count_mismatch["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]["fileCount"] = 2
    _p3c0_assert_projection_rejected(tmp_path, count_mismatch)

    latest_mismatch = deepcopy(valid)
    latest = latest_mismatch["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]["latestFile"]
    latest["path"] = ".controlcoding/context-packets/not-captured.md"
    _p3c0_assert_projection_rejected(tmp_path, latest_mismatch)

    exists_mismatch = deepcopy(valid)
    missing_artifact = exists_mismatch["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    missing_artifact["exists"] = False
    missing_artifact["fileCount"] = 0
    missing_artifact["latestFile"] = {}
    _p3c0_assert_projection_rejected(tmp_path, exists_mismatch)

    invalid_timestamp = deepcopy(valid)
    entry = invalid_timestamp["filesystemFingerprint"]["inventories"][
        ".controlcoding/context-packets"
    ]["entries"][0]
    entry["mtimeNs"] = 10**30
    real_gmtime = freshness_projection.time.gmtime

    def platform_limited_gmtime(value):
        if value > 10**12:
            raise OSError("timestamp outside the platform range")
        return real_gmtime(value)

    monkeypatch.setattr(freshness_projection.time, "gmtime", platform_limited_gmtime)
    _p3c0_assert_projection_rejected(tmp_path, invalid_timestamp)


def test_p3c0_r4_graph_contract_must_match_canonical_runtime_constant(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    payload = _p3c0_projection_payload(tmp_path)
    forged = memory_graph.GRAPH_CONTRACT_VERSION + "-forged"
    payload["legacyStatus"]["graph"]["contractVersion"] = forged
    payload["legacyStatus"]["bootstrap"]["devPlane"]["graphContractVersion"] = forged

    _p3c0_assert_projection_rejected(tmp_path, payload)


def test_p3c0_r4_lifecycle_attention_matches_canonical_lifecycle_buckets(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    now = "2026-08-13T00:00:00Z"
    with memory_store._memory_connection(tmp_path) as conn:
        conn.executemany(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              source_refs, provenance, body, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("P3C0R4ACTIVE", "note", "Active", "P3C0", "controlcoding_dev", "notes/active.md", "active", "[]", "{}", "active", "{}", now, now),
                ("P3C0R4REVIEW", "note", "Review", "P3C0", "controlcoding_dev", "notes/review.md", "needs_review", "[]", "{}", "review", "{}", now, now),
            ],
        )

    payload = _p3c0_projection_payload(tmp_path)
    status = payload["legacyStatus"]
    expected_attention = sum(
        count
        for lifecycle, count in status["byLifecycle"].items()
        if lifecycle in memory_lifecycle.LIFECYCLE_ATTENTION_STATES
    )
    assert status["lifecycleAttention"] == expected_attention == 1
    assert status["entities"] == 2

    status["lifecycleAttention"] = 0
    status["staleOrNeedsReview"] = 0
    _p3c0_assert_projection_rejected(tmp_path, payload)


def test_p3c0_r5_context_packet_add_remove_races_and_wrong_type_are_unavailable(tmp_path, monkeypatch):
    category_path = tmp_path / work_features.CATEGORY_PATH
    category_path.parent.mkdir(parents=True)
    category_path.write_text(json.dumps(work_features._default_registry()), encoding="utf-8")
    packet_root = tmp_path / work_features.CONTEXT_PACKET_ROOT
    packet_root.mkdir(parents=True)
    (packet_root / "observed.md").write_text("# Observed\n", encoding="utf-8")
    (packet_root / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    observed = work_features.feature_status_payload(tmp_path)
    assert observed["available"] is True
    assert observed["contextPackets"] == 1

    (packet_root / "wrong-type.md").mkdir()
    unavailable = freshness_projection.category_feature_status_payload(
        category_path,
        packet_root,
        tmp_path / work_features.WIKI_ROOT,
        set(work_features.CAPTURE_AREAS),
    )
    assert unavailable["available"] is False
    assert unavailable["contextPackets"] is None
    assert unavailable["health"]["state"] == "unknown"
    assert unavailable["health"]["reason"] == "context_packets_unreadable"

    real_inventory = freshness_projection._markdown_inventory_for_feature_status
    for race_kind in ("add", "remove"):
        project = tmp_path / race_kind
        race_category_path = project / work_features.CATEGORY_PATH
        race_category_path.parent.mkdir(parents=True)
        race_category_path.write_text(
            json.dumps(work_features._default_registry()),
            encoding="utf-8",
        )
        race_packet_root = project / work_features.CONTEXT_PACKET_ROOT
        race_packet_root.mkdir(parents=True)
        raced_packet = race_packet_root / "raced.md"
        if race_kind == "remove":
            raced_packet.write_text("# Removed during inventory\n", encoding="utf-8")
        calls = 0

        def raced_inventory(path, *, mode=race_kind, target=raced_packet):
            nonlocal calls
            inventory, reason = real_inventory(path)
            calls += 1
            if calls == 1:
                if mode == "add":
                    target.write_text("# Added during inventory\n", encoding="utf-8")
                else:
                    target.unlink()
            return inventory, reason

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                freshness_projection,
                "_markdown_inventory_for_feature_status",
                raced_inventory,
            )
            raced = freshness_projection.category_feature_status_payload(
                race_category_path,
                race_packet_root,
                project / work_features.WIKI_ROOT,
                set(work_features.CAPTURE_AREAS),
            )
        assert calls == 2
        assert raced["available"] is False
        assert raced["categories"]["available"] is True
        assert raced["contextPackets"] is None
        assert raced["health"]["state"] == "unknown"
        assert raced["health"]["reason"] == "context_packets_unreadable"


def test_p3c0_r4_latest_checkpoint_requires_stable_regular_file_and_guarded_packet(tmp_path, monkeypatch):
    checkpoint_root = tmp_path / work_features.CHECKPOINT_ROOT
    checkpoint_root.mkdir(parents=True)
    candidate = checkpoint_root / "20260813T000000Z-checkpoint.md"
    candidate.mkdir()

    with pytest.raises(RuntimeError, match="checkpoint"):
        work_features.latest_checkpoint(tmp_path)

    stable_capture = {
        "embedded": {
            "root": {"exists": True},
            "context": {"exists": False},
        },
        "generation": 1,
    }
    packet = freshness_projection.guarded_project_plane_packet(
        tmp_path,
        lambda: work_features.build_context_pack(
            tmp_path,
            scope="dev",
            topic="P3-C0-R4",
            refresh_views=False,
        ),
        observer=lambda project: {
            "available": True,
            "state": "observed",
            "color": "GREEN",
            "reason": "",
            "message": "Project Plane inputs observed.",
        },
        capture=lambda project: (deepcopy(stable_capture), ""),
    )
    assert packet["ok"] is False
    assert packet["available"] is False
    assert packet["packetMarkdown"] is None
    assert packet["health"]["state"] == "unknown"
    assert packet["health"]["color"] == "YELLOW"
    assert packet["health"]["reason"] == "project_plane_packet_unavailable"

    candidate.rmdir()
    candidate.write_text("# Stable checkpoint\n", encoding="utf-8")
    real_capture = freshness_projection._capture_file

    def unstable_checkpoint(path, label):
        if label.startswith("project_checkpoint_"):
            return None, f"source_{label}_changed_during_capture"
        return real_capture(path, label)

    monkeypatch.setattr(freshness_projection, "_capture_file", unstable_checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint input is unavailable"):
        work_features.latest_checkpoint(tmp_path)


def test_p3c0_r4_expected_views_require_regular_captured_files(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    assert _run_main([
        "memory", "session", "start", "--project-root", str(tmp_path),
        "--id", "p3c0-r4-views", "--topic", "Regular views", "--mode", "maintenance",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory", "session", "views", "--project-root", str(tmp_path),
    ]) == 0
    capsys.readouterr()
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"

    valid = _p3c0_projection_payload(tmp_path)
    view_path = ".controlcoding/views/SESSION_INDEX.md"
    inventory = valid["filesystemFingerprint"]["inventories"][".controlcoding/views"]
    entry = next(item for item in inventory["entries"] if item["path"] == view_path)
    assert entry["type"] == "regular"
    forged = deepcopy(valid)
    forged_entry = next(
        item
        for item in forged["filesystemFingerprint"]["inventories"][".controlcoding/views"]["entries"]
        if item["path"] == view_path
    )
    forged_entry["type"] = "directory"
    _p3c0_assert_projection_rejected(tmp_path, forged)

    freshness_projection.projection_path(tmp_path).write_text(json.dumps(valid), encoding="utf-8")
    expected_view = tmp_path / view_path
    expected_view.unlink()
    expected_view.mkdir()
    _p3c0_assert_unknown_yellow(freshness_projection.observe_freshness(tmp_path))

    project_plane = tmp_path / "project-plane"
    work_root = project_plane / ".controlwork"
    view_root = work_root / "memory" / "views"
    view_root.mkdir(parents=True)
    (work_root / "config.json").write_text(json.dumps({
        "distribution": "embedded_controlcoding",
        "standaloneCompatible": True,
    }), encoding="utf-8")
    (view_root / "index.md").mkdir()

    def forbidden_adapter(_project):
        raise AssertionError("wrong-type Project Plane view must block the legacy adapter")

    monkeypatch.setattr(memory_op_index, "_work_status_payload", forbidden_adapter)
    project_observation = freshness_projection.observe_project_plane_inputs(project_plane)
    assert project_observation["available"] is False
    assert project_observation["state"] == "unknown"
    assert project_observation["reason"] == "project_plane_views_wrong_type"
    work_status = memory_op_index._observed_work_status_payload(project_plane)
    assert work_status["available"] is False
    assert work_status["drift"] is None
    assert work_status["health"]["state"] == "unknown"
    assert work_status["health"]["color"] == "YELLOW"
    assert work_status["health"]["reason"] == "project_plane_views_wrong_type"


def test_p3c0_r5_public_work_status_reports_all_expected_markdown_view_directories(tmp_path, capsys):
    (tmp_path / "CONTROLWORK.md").write_text("# ControlWork project\n", encoding="utf-8")
    work_root = tmp_path / ".controlwork"
    work_root.mkdir()
    (work_root / "config.json").write_text(
        json.dumps(memory_commands._controlwork_config()),
        encoding="utf-8",
    )
    view_root = work_root / "memory" / "views"
    expected = {
        "index.md",
        "active-decisions.md",
        "open-questions.md",
        "source-ledger.md",
        "handoff-packet.md",
        "category-registry.md",
    }
    for filename in expected:
        (view_root / filename).mkdir(parents=True)

    assert _run_main([
        "memory", "work-status", "--project-root", str(tmp_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    view_drift = payload["drift"]["derivedArtifacts"]["views"]

    assert view_drift["stale"] is True
    assert {
        Path(item["path"]).name: item["state"]
        for item in view_drift["drift"]
    } == {filename: "wrong_type" for filename in expected}


def test_p3c0_r5_public_work_status_uses_stable_regular_context_packet_inventory(
    tmp_path,
    capsys,
    monkeypatch,
):
    def initialize_project(project: Path) -> Path:
        (project / "CONTROLWORK.md").write_text("# ControlWork project\n", encoding="utf-8")
        work_root = project / ".controlwork"
        work_root.mkdir()
        (work_root / "config.json").write_text(
            json.dumps(memory_commands._controlwork_config()),
            encoding="utf-8",
        )
        packet_root = work_root / "context-packets"
        packet_root.mkdir()
        return packet_root

    packet_root = initialize_project(tmp_path)
    observed_packet = packet_root / "observed.md"
    observed_packet.write_text("# Observed\n", encoding="utf-8")
    wrong_type = packet_root / "wrong-type.md"
    wrong_type.mkdir()

    assert _run_main([
        "memory", "work-status", "--project-root", str(tmp_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    packets = payload["drift"]["derivedArtifacts"]["contextPackets"]
    assert packets == {
        "path": ".controlwork/context-packets",
        "available": False,
        "count": None,
        "latest": None,
        "stale": None,
        "health": {"state": "unknown", "reason": "context_packets_unreadable"},
    }

    wrong_type.rmdir()
    assert _run_main([
        "memory", "work-status", "--project-root", str(tmp_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    packets = payload["drift"]["derivedArtifacts"]["contextPackets"]
    assert packets["available"] is True
    assert packets["count"] == 1
    assert packets["latest"] == ".controlwork/context-packets/observed.md"
    assert packets["stale"] is False
    assert packets["health"] == {"state": "observed", "reason": ""}

    real_inventory = freshness_projection._markdown_inventory_for_feature_status
    for race_kind in ("add", "remove"):
        project = tmp_path / race_kind
        project.mkdir()
        race_packet_root = initialize_project(project)
        raced_packet = race_packet_root / "raced.md"
        if race_kind == "remove":
            raced_packet.write_text("# Removed during capture\n", encoding="utf-8")
        calls = 0

        def raced_inventory(path, *, mode=race_kind, target=raced_packet):
            nonlocal calls
            inventory, reason = real_inventory(path)
            calls += 1
            if calls == 1:
                if mode == "add":
                    target.write_text("# Added during capture\n", encoding="utf-8")
                else:
                    target.unlink()
            return inventory, reason

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                freshness_projection,
                "_markdown_inventory_for_feature_status",
                raced_inventory,
            )
            patch_context.setattr(
                work_features,
                "feature_status_payload",
                lambda _project: {
                    "available": False,
                    "contextPackets": None,
                    "health": {"state": "unknown", "reason": "not_observed"},
                },
            )
            assert _run_main([
                "memory", "work-status", "--project-root", str(project), "--json",
            ]) == 0
        payload = json.loads(capsys.readouterr().out)
        packets = payload["drift"]["derivedArtifacts"]["contextPackets"]
        assert calls == 2
        assert packets["available"] is False
        assert packets["count"] is None
        assert packets["latest"] is None
        assert packets["stale"] is None
        assert packets["health"] == {
            "state": "unknown",
            "reason": "context_packets_unreadable",
        }


def test_p3c0_r4_feature_registry_invalid_schema_is_unknown_without_synthesized_wip(tmp_path):
    registry_path = tmp_path / ".controlcoding" / "features" / "features.json"
    registry_path.parent.mkdir(parents=True)
    invalid_registries = []

    features_not_list = cc_feature._empty_registry()
    features_not_list["features"] = {"active": []}
    invalid_registries.append(features_not_list)

    invalid_record = cc_feature._empty_registry()
    invalid_record["features"] = [{}]
    invalid_registries.append(invalid_record)

    for registry in invalid_registries:
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        status = memory_op_index._feature_status_payload(tmp_path)
        assert status["available"] is False
        assert status["initialized"] is None
        assert status["features"] is None
        assert status["summary"] is None
        assert status["currentFeature"] is None
        assert status["blockedFeatureIds"] is None
        assert status["health"]["state"] == "unknown"
        assert status["health"]["color"] == "YELLOW"
        assert status["health"]["reason"] == "feature_registry_invalid"

        queue = memory_op_index._action_queue(
            {"recommendations": [], "derivedArtifacts": {"graphPackets": {"fileCount": None}}},
            {"available": False},
            {"suggestionCounts": None},
            {"views": None, "openFollowups": None},
            status,
            {"trackedDirtyCount": None},
            "P3-C0-R4",
            "dev",
        )
        assert not any(item.get("source") == "feature-state" for item in queue)
        assert not any("WIP" in str(item.get("reason", "")) for item in queue)


def test_p3c0_r5_invalid_nested_feature_records_are_null_and_do_not_create_feature_actions(tmp_path):
    registry_path = tmp_path / ".controlcoding" / "features" / "features.json"
    registry_path.parent.mkdir(parents=True)
    timestamp = "2026-08-13T00:00:00Z"
    feature = {
        "id": "p3c0-r5-feature",
        "title": "P3-C0-R5 feature",
        "state": "active",
        "objective": "Reject malformed nested records.",
        "scope": "tests",
        "acceptanceCriteria": ["Malformed nested records are unavailable."],
        "nonGoals": [],
        "verificationChecks": ["Run the targeted regression."],
        "verificationReceipts": [],
        "events": [{
            "at": timestamp,
            "kind": "start",
            "message": "feature started",
            "data": {},
        }],
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
    base_registry = {
        "schemaVersion": cc_feature.FEATURE_STATE_SCHEMA_VERSION,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "wipLimit": 1,
        "features": [feature],
    }

    registry_path.write_text(json.dumps(base_registry), encoding="utf-8")
    valid_status = memory_op_index._feature_status_payload(tmp_path)
    assert valid_status["initialized"] is True
    assert valid_status["currentFeature"]["id"] == feature["id"]
    assert "available" not in valid_status

    invalid_registries = []
    invalid_receipt = deepcopy(base_registry)
    invalid_receipt["features"][0]["verificationReceipts"] = [{}]
    invalid_registries.append(invalid_receipt)
    invalid_event = deepcopy(base_registry)
    invalid_event["features"][0]["events"] = [{}]
    invalid_registries.append(invalid_event)

    for registry in invalid_registries:
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        status = memory_op_index._feature_status_payload(tmp_path)

        assert status["available"] is False
        assert status["initialized"] is None
        assert status["features"] is None
        assert status["summary"] is None
        assert status["currentFeature"] is None
        assert status["blockedFeatureIds"] is None
        assert status["health"]["state"] == "unknown"
        assert status["health"]["color"] == "YELLOW"
        assert status["health"]["reason"] == "feature_registry_invalid"

        queue = memory_op_index._action_queue(
            {"recommendations": [], "derivedArtifacts": {"graphPackets": {"fileCount": None}}},
            {"available": False},
            {"suggestionCounts": None},
            {"views": None, "openFollowups": None},
            status,
            {"trackedDirtyCount": None},
            "P3-C0-R5",
            "dev",
        )
        assert not any(item.get("source") == "feature-state" for item in queue)
        assert not any("WIP" in str(item.get("reason", "")) for item in queue)


def test_p3c0_r5_feature_registry_accepts_canonical_terminal_history_retention_and_ids(
    tmp_path,
    capsys,
    monkeypatch,
):
    tick = 0

    def deterministic_now():
        nonlocal tick
        tick += 1
        return f"2026-08-13T00:00:00.{tick:03d}Z"

    monkeypatch.setattr(cc_feature, "_now_iso", deterministic_now)
    checks = ["python -c \"print('ok')\""]
    assert cc_feature.cmd_feature_start(
        tmp_path,
        feature_id="canonical-history",
        title="Canonical history",
        objective="Exercise canonical terminal history and retention.",
        acceptance=["Canonical records remain observable."],
        check=checks,
    ) == 0
    capsys.readouterr()

    run_result = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    monkeypatch.setattr(cc_feature.subprocess, "run", lambda *_args, **_kwargs: run_result)
    assert cc_feature.cmd_feature_verify(
        tmp_path,
        "canonical-history",
        run_checks=checks,
    ) == 0
    assert cc_feature.cmd_feature_complete(
        tmp_path,
        "canonical-history",
        summary="Completed once.",
    ) == 0
    registry_path = tmp_path / ".controlcoding" / "features" / "features.json"
    completed_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    completed_status = memory_op_index._feature_status_payload(tmp_path)
    assert completed_status["initialized"] is True
    assert completed_status["features"][0]["state"] == "completed"

    missing_verify_event = deepcopy(completed_registry)
    del missing_verify_event["features"][0]["events"][-2]
    registry_path.write_text(json.dumps(missing_verify_event), encoding="utf-8")
    incoherent_completed = memory_op_index._feature_status_payload(tmp_path)
    assert incoherent_completed["available"] is False
    assert incoherent_completed["summary"] is None
    assert incoherent_completed["currentFeature"] is None
    registry_path.write_text(json.dumps(completed_registry), encoding="utf-8")

    assert cc_feature.cmd_feature_start(
        tmp_path,
        feature_id="canonical-history",
        title="Canonical history",
        objective="Restart the canonical producer output.",
        acceptance=["Terminal history remains valid."],
        check=checks,
    ) == 0
    assert memory_op_index._feature_status_payload(tmp_path)["currentFeature"]["state"] == "active"

    assert cc_feature.cmd_feature_abort(
        tmp_path,
        "canonical-history",
        reason="Exercise retained abort history.",
    ) == 0
    aborted = memory_op_index._feature_status_payload(tmp_path)
    assert aborted["initialized"] is True
    assert aborted["features"][0]["state"] == "aborted"
    assert {"completedAt", "completionSummary", "abortedAt", "abortReason"} <= set(
        aborted["features"][0]
    )

    assert cc_feature.cmd_feature_start(
        tmp_path,
        feature_id="canonical-history",
        title="Canonical history",
        objective="Fill the canonical receipt retention window.",
        acceptance=["Pruned receipt history remains observable."],
        check=checks,
    ) == 0
    for _ in range(51):
        assert cc_feature.cmd_feature_verify(
            tmp_path,
            "canonical-history",
            run_checks=checks,
        ) == 0
    retained = memory_op_index._feature_status_payload(tmp_path)
    capsys.readouterr()
    assert retained["initialized"] is True
    assert retained["currentFeature"]["state"] == "passing"
    assert len(retained["currentFeature"]["verificationReceipts"]) == 50
    assert len(retained["currentFeature"]["events"]) > 50

    long_id_project = tmp_path / "long-id"
    long_id = "a" * 79 + "-tail"
    assert cc_feature.cmd_feature_start(
        long_id_project,
        feature_id=long_id,
        title="Long canonical ID",
        objective="Accept the exact producer truncation boundary.",
        acceptance=["The produced ID remains observable."],
        check=checks,
    ) == 0
    capsys.readouterr()
    long_id_status = memory_op_index._feature_status_payload(long_id_project)
    assert long_id_status["initialized"] is True
    assert long_id_status["currentFeature"]["id"] == "a" * 79 + "-"


def test_p3c0_r3_builder_projection_round_trip_satisfies_observational_contract(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    projection = _p3c0_projection_payload(tmp_path)
    encoded = json.dumps(projection, sort_keys=True, allow_nan=False)
    decoded = json.loads(encoded)
    validated, reason = freshness_projection._validate_projection(decoded)

    assert reason == ""
    assert validated == decoded
    assert "databaseFingerprint" in decoded
    assert "filesystemFingerprint" in decoded

    status = decoded["legacyStatus"]
    bootstrap = status["bootstrap"]
    dev = bootstrap["devPlane"]
    counts = dev["counts"]
    graph = status["graph"]
    schema = status["schema"]
    sessions = status["sessions"]
    views = sessions["views"]
    artifacts = bootstrap["derivedArtifacts"]

    assert sum(status["byType"].values()) == status["entities"]
    assert sum(status["byLifecycle"].values()) == status["entities"]
    assert status["lifecycleAttention"] <= status["entities"]
    assert status["staleOrNeedsReview"] <= status["entities"]
    assert counts["applicationMemoryComponents"] <= counts["entities"]
    assert bootstrap["applicationOwnedMemory"]["referencedComponents"] == counts["applicationMemoryComponents"]
    assert sum(graph["nodeCounts"].values()) == counts["entities"] + counts["semanticChunks"]
    assert sum(graph["edgeCounts"].values()) == counts["edges"]
    assert graph["semanticChunks"] == counts["semanticChunks"]
    assert graph["contractVersion"] == dev["graphContractVersion"]
    assert graph["sqliteSchemaVersion"] == dev["schema"]["targetVersion"]
    assert str(graph["sqliteSchemaVersion"]) == dev["sqliteSchemaVersion"]
    assert decoded["databaseFingerprint"]["files"]["memory.db"]["exists"] is dev["hasDatabase"]
    assert dev["hasControlDir"] is True
    assert dev["initialized"] is (dev["hasManifest"] and dev["hasDatabase"])

    assert schema == dev["schema"]
    assert schema["targetVersion"] == memory_migrations.TARGET_SCHEMA_VERSION
    assert 0 <= schema["effectiveVersion"] <= memory_migrations.TARGET_SCHEMA_VERSION
    assert schema["missingTables"] == []
    assert schema["missingColumns"] == {}
    if schema["schemaState"] == "current":
        assert schema["userVersion"] == memory_migrations.TARGET_SCHEMA_VERSION
        assert schema["metadataVersion"] == memory_migrations.TARGET_SCHEMA_VERSION
        assert schema["effectiveVersion"] == memory_migrations.TARGET_SCHEMA_VERSION
        assert memory_migrations.TARGET_SCHEMA_VERSION in schema["schemaVersionRows"]
        assert schema["issues"] == []

    assert sessions["available"] is True
    assert sessions["message"] == ""
    assert (sessions["latestSession"] == {}) is (sessions["sessionCount"] == 0)
    assert views["implemented"] is True
    assert len(views["paths"]) == views["requiredViewCount"]
    assert len(set(views["paths"])) == len(views["paths"])
    assert all(views["paths"])
    assert views["generatedViewCount"] <= views["requiredViewCount"]
    assert views["stale"] is bool(
        sessions["sessionCount"]
        and (
            views["generatedViewCount"] < views["requiredViewCount"]
            or not views["oldestGeneratedAt"]
            or views["latestSessionUpdatedAt"] > views["oldestGeneratedAt"]
        )
    )

    for name in ("views", "devContextPackets", "projectPlaneViews", "projectPlaneContextPackets"):
        artifact = artifacts[name]
        if not artifact["exists"]:
            assert artifact["fileCount"] == 0
            assert artifact["latestFile"] == {}
        if artifact["fileCount"] == 0:
            assert artifact["latestFile"] == {}
    graph_packets = artifacts["graphPackets"]
    assert graph_packets["stale"] is (False if graph_packets["fileCount"] else None)


def test_p3c0_r3_semantic_schema_and_graph_incoherence_degrade_to_unknown(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    valid = _p3c0_projection_payload(tmp_path)
    target = memory_migrations.TARGET_SCHEMA_VERSION
    mutations = []

    payload = deepcopy(valid)
    payload["legacyStatus"]["byType"] = {"note": payload["legacyStatus"]["entities"] + 1}
    payload["legacyStatus"]["bootstrap"]["devPlane"]["entityTypes"] = deepcopy(payload["legacyStatus"]["byType"])
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["byLifecycle"] = {"active": payload["legacyStatus"]["entities"] + 1}
    payload["legacyStatus"]["bootstrap"]["devPlane"]["lifecycles"] = deepcopy(payload["legacyStatus"]["byLifecycle"])
    mutations.append(payload)

    payload = deepcopy(valid)
    entities = payload["legacyStatus"]["entities"]
    payload["legacyStatus"]["lifecycleAttention"] = entities + 1
    payload["legacyStatus"]["staleOrNeedsReview"] = entities + 1
    mutations.append(payload)

    payload = deepcopy(valid)
    dev = payload["legacyStatus"]["bootstrap"]["devPlane"]
    dev["counts"]["applicationMemoryComponents"] = dev["counts"]["entities"] + 1
    payload["legacyStatus"]["bootstrap"]["applicationOwnedMemory"]["referencedComponents"] = dev["counts"]["applicationMemoryComponents"]
    mutations.append(payload)

    payload = deepcopy(valid)
    for schema in (payload["legacyStatus"]["schema"], payload["legacyStatus"]["bootstrap"]["devPlane"]["schema"]):
        schema["targetVersion"] = target + 1
    mutations.append(payload)

    payload = deepcopy(valid)
    for schema in (payload["legacyStatus"]["schema"], payload["legacyStatus"]["bootstrap"]["devPlane"]["schema"]):
        schema["schemaState"] = "current"
        schema["metadataVersion"] = target - 1
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["graph"]["contractVersion"] += "-mismatch"
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["graph"]["sqliteSchemaVersion"] += 1
    mutations.append(payload)

    payload = deepcopy(valid)
    dev = payload["legacyStatus"]["bootstrap"]["devPlane"]
    dev["hasDatabase"] = False
    dev["initialized"] = False
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["bootstrap"]["devPlane"]["hasControlDir"] = False
    mutations.append(payload)

    payload = deepcopy(valid)
    inventory = payload["filesystemFingerprint"]["inventories"][".controlcoding/context-packets"]
    inventory.update({"exists": True, "type": "regular", "entries": []})
    mutations.append(payload)

    for malformed in mutations:
        _p3c0_assert_projection_rejected(tmp_path, malformed)

    freshness_projection.projection_path(tmp_path).write_text(json.dumps(valid, allow_nan=False), encoding="utf-8")
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"


def test_p3c0_observation_is_wal_byte_invariant_and_tripwired(tmp_path, capsys, monkeypatch):
    database, writer = _p3c0_create_wal_fixture(tmp_path, capsys)
    memory_root = database.parent
    watched = [database, Path(f"{database}-wal"), Path(f"{database}-shm")]
    before = {path.name: _p3c0_file_snapshot(path) for path in watched}
    before_directory = _p3c0_directory_snapshot(memory_root)
    assert before[f"{database.name}-wal"]["exists"] is True
    assert before[f"{database.name}-shm"]["exists"] is True

    def forbidden_sqlite(*_args, **_kwargs):
        raise AssertionError("P3-C0 observational path must not open SQLite")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("P3-C0 observational path reached a writer")

    status_calls = []
    op_index_calls = []
    work_start_calls = []
    retrieval_calls = []
    real_status = memory_impact.projected_status_payload
    real_op_index_observe = memory_op_index.observe_freshness
    real_work_start_observe = cc.observe_freshness
    real_projected_retrieval = cc.projected_retrieval_payload

    def tracked_status(*args, **kwargs):
        status_calls.append(True)
        return real_status(*args, **kwargs)

    def tracked_op_index_observe(*args, **kwargs):
        op_index_calls.append(True)
        return real_op_index_observe(*args, **kwargs)

    def tracked_work_start_observe(*args, **kwargs):
        work_start_calls.append(True)
        return real_work_start_observe(*args, **kwargs)

    def tracked_projected_retrieval(*args, **kwargs):
        retrieval_calls.append(True)
        return real_projected_retrieval(*args, **kwargs)

    real_run = subprocess.run
    git_envs = []

    def tracked_run(*args, **kwargs):
        if args and args[0] and args[0][0] == "git":
            git_envs.append(kwargs.get("env"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", forbidden_sqlite)
    monkeypatch.setattr(memory_op_index.subprocess, "run", tracked_run)
    monkeypatch.setattr(memory_impact, "projected_status_payload", tracked_status)
    monkeypatch.setattr(memory_op_index, "observe_freshness", tracked_op_index_observe)
    monkeypatch.setattr(cc, "observe_freshness", tracked_work_start_observe)
    monkeypatch.setattr(cc, "projected_retrieval_payload", tracked_projected_retrieval)
    monkeypatch.setattr(memory_commands, "cmd_memory_scan", forbidden)
    monkeypatch.setattr(memory_vector, "_rebuild_vector_index", forbidden)
    monkeypatch.setattr(memory_sessions, "cmd_memory_session_views", forbidden)
    monkeypatch.setattr(memory_store, "migrate_schema", forbidden)
    monkeypatch.setattr(memory_commands, "cmd_memory_work_sync", forbidden)
    monkeypatch.setattr(memory_commands, "cmd_memory_work_checkpoint", forbidden)
    monkeypatch.setattr(work_features, "ensure_category_registry", forbidden)

    assert _run_main(["memory", "status", "--project-root", str(tmp_path), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["health"]["state"] == "unknown"
    assert status["health"]["color"] == "YELLOW"
    assert _run_main(["memory", "startup", "--project-root", str(tmp_path), "--json"]) == 0
    startup = json.loads(capsys.readouterr().out)
    assert startup["readOnly"] is True
    assert startup["hiddenWrites"] is False
    assert _run_main(["memory", "op-index", "--project-root", str(tmp_path), "--json"]) == 0
    index = json.loads(capsys.readouterr().out)
    assert index["health"]["state"] == "unknown"
    assert _run_main(["work-start", "--project-root", str(tmp_path), "--topic", "p3c0", "--json"]) == 0
    work_start = json.loads(capsys.readouterr().out)
    assert work_start["readOnly"] is True
    assert work_start["hiddenWrites"] is False
    assert _run_main(["chat-start", "--project-root", str(tmp_path), "--topic", "p3c0", "--json"]) == 0
    chat_start = json.loads(capsys.readouterr().out)
    assert chat_start["readOnly"] is True
    assert chat_start["hiddenWrites"] is False

    assert git_envs
    assert all(environment and environment.get("GIT_OPTIONAL_LOCKS") == "0" for environment in git_envs)
    assert status_calls
    assert op_index_calls
    assert work_start_calls
    assert retrieval_calls
    assert {path.name: _p3c0_file_snapshot(path) for path in watched} == before
    assert _p3c0_directory_snapshot(memory_root) == before_directory
    writer.close()


def test_p3c0_projection_unknown_cases_atomic_write_and_capability_profiles(tmp_path, capsys, monkeypatch):
    projection = freshness_projection.projection_path(tmp_path)
    before_directory = _p3c0_directory_snapshot(tmp_path)
    missing = freshness_projection.observe_freshness(tmp_path)
    assert missing["state"] == "unknown"
    assert missing["reason"] == "source_db_absent"
    assert missing["capabilities"]["controlWorkStandaloneVector"] == "available"
    assert _p3c0_directory_snapshot(tmp_path) == before_directory

    missing_registry = work_features.feature_status_payload(tmp_path)
    assert missing_registry["categories"] == {"available": False, "approved": None, "proposed": None}
    assert missing_registry["health"]["reason"] == "category_registry_missing"
    registry_path = tmp_path / ".controlwork" / "categories.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_bytes(b"\xffnot-json")
    malformed_registry = work_features.feature_status_payload(tmp_path)
    assert malformed_registry["categories"] == {"available": False, "approved": None, "proposed": None}
    assert malformed_registry["health"]["reason"] == "category_registry_invalid"

    projection.parent.mkdir(parents=True)
    projection.write_text('{"schemaVersion": 999}\n', encoding="utf-8")
    incompatible = freshness_projection.observe_freshness(tmp_path)
    assert incompatible["state"] == "unknown"
    assert incompatible["reason"] == "source_db_absent"

    database, writer = _p3c0_create_wal_fixture(tmp_path / "atomic", capsys)
    writer.close()
    atomic_projection = freshness_projection.projection_path(database.parents[2])
    old_content = atomic_projection.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection.os, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failure"):
            freshness_projection._atomic_write_json(atomic_projection, {"new": "payload"})
    assert atomic_projection.read_bytes() == old_content
    assert not list(atomic_projection.parent.glob(f".{atomic_projection.name}.*.tmp"))

    def fail_fsync(*_args, **_kwargs):
        raise OSError("simulated write failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection.os, "fsync", fail_fsync)
        with pytest.raises(OSError, match="write failure"):
            freshness_projection._atomic_write_json(atomic_projection, {"new": "payload"})
    assert atomic_projection.read_bytes() == old_content
    assert not list(atomic_projection.parent.glob(f".{atomic_projection.name}.*.tmp"))

    freshness_projection._atomic_write_json(atomic_projection, {"stable": True})
    assert json.loads(atomic_projection.read_text(encoding="utf-8")) == {"stable": True}

    v1 = tmp_path / "ControlCoding_V1"
    v1.mkdir()
    assert freshness_projection.observe_freshness(v1)["state"] == "unknown"
    v1_profile = freshness_projection.capability_profile_path(v1)
    v1_profile.parent.mkdir(parents=True)
    v1_profile.write_text(json.dumps({
        "schemaVersion": 1,
        "checkout": "controlcoding_v1",
        "capabilities": {"memoryHealthProjection": "not_applicable"},
    }), encoding="utf-8")
    assert freshness_projection.observe_freshness(v1)["state"] == "not_applicable"

    controlwork = tmp_path / "ControlWork"
    profile = freshness_projection.capability_profile_path(controlwork)
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({
        "schemaVersion": 1,
        "checkout": "controlwork_standalone",
        "capabilities": {"vector": "not_applicable"},
    }), encoding="utf-8")
    assert freshness_projection.observe_freshness(controlwork)["capabilities"]["controlWorkStandaloneVector"] == "not_applicable"


def test_p3c0_stable_fingerprint_rejects_byte_identical_db_and_wal_replacement(tmp_path, capsys):
    database = _p3c0_init_project(tmp_path, capsys)
    wal = Path(f"{database}-wal")
    wal.write_bytes(b"P3-C0 WAL identity probe\n")
    _p3c0_republish_current(tmp_path)
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"

    _p3c0_replace_with_identical_bytes(wal)
    wal_replaced = freshness_projection.observe_freshness(tmp_path)
    assert wal_replaced["state"] == "unknown"
    assert wal_replaced["color"] == "YELLOW"
    assert wal_replaced["reason"] == "projection_outdated"

    _p3c0_republish_current(tmp_path)
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"
    _p3c0_replace_with_identical_bytes(database)
    db_replaced = freshness_projection.observe_freshness(tmp_path)
    assert db_replaced["state"] == "unknown"
    assert db_replaced["color"] == "YELLOW"
    assert db_replaced["reason"] == "projection_outdated"


def test_p3c0_r3_present_database_and_source_directory_require_identity(tmp_path, capsys, monkeypatch):
    database = _p3c0_init_project(tmp_path, capsys)
    valid = _p3c0_projection_payload(tmp_path)

    payload = deepcopy(valid)
    payload["databaseFingerprint"]["files"]["memory.db"]["identity"] = None
    _p3c0_assert_projection_rejected(tmp_path, payload)

    payload = deepcopy(valid)
    payload["databaseFingerprint"]["sourceDirectory"]["identity"] = None
    _p3c0_assert_projection_rejected(tmp_path, payload)

    payload = deepcopy(valid)
    manifest_key = next(
        key
        for key in payload["filesystemFingerprint"]["contentFiles"]
        if key.endswith("/module_manifest.json")
    )
    payload["filesystemFingerprint"]["contentFiles"][manifest_key]["identity"] = None
    _p3c0_assert_projection_rejected(tmp_path, payload)

    payload = deepcopy(valid)
    payload["filesystemFingerprint"]["projectDirectory"]["identity"] = None
    _p3c0_assert_projection_rejected(tmp_path, payload)

    freshness_projection.projection_path(tmp_path).write_text(json.dumps(valid, allow_nan=False), encoding="utf-8")
    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "_identity", lambda _stat_result: None)
        record, reason = freshness_projection._capture_file(database, "db")
        assert record is None
        assert reason == "source_db_identity_unavailable"

        fingerprint, reason = freshness_projection.capture_source_fingerprint(tmp_path)
        assert fingerprint is None
        assert reason == "source_directory_identity_unavailable"

        observation = freshness_projection.observe_freshness(tmp_path)
        assert observation["state"] == "unknown"
        assert observation["color"] == "YELLOW"
        assert observation["reason"] == "source_directory_identity_unavailable"

    manifest = tmp_path / ".controlcoding" / memory_store.MANIFEST_FILENAME
    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "_identity", lambda _stat_result: None)
        record, reason = freshness_projection._capture_content_file(manifest, "filesystem_manifest")
        assert record is None
        assert reason == "source_filesystem_manifest_identity_unavailable"

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            freshness_projection,
            "capture_filesystem_fingerprint",
            lambda project: (None, "source_filesystem_manifest_identity_unavailable"),
        )
        observation = freshness_projection.observe_freshness(tmp_path)
        assert observation["state"] == "unknown"
        assert observation["color"] == "YELLOW"
        assert observation["reason"] == "source_filesystem_manifest_identity_unavailable"


def test_p3c0_fingerprint_races_degrade_without_observer_exception(tmp_path, capsys, monkeypatch):
    database = _p3c0_init_project(tmp_path, capsys)
    wal = Path(f"{database}-wal")
    wal.write_bytes(b"W" * (2 * 1024 * 1024))
    _p3c0_republish_current(tmp_path)
    wal_size = wal.stat().st_size
    real_read = freshness_projection.os.read
    real_stat = freshness_projection.os.stat
    removed = False

    def remove_wal_during_hash(descriptor, size):
        nonlocal removed
        chunk = real_read(descriptor, size)
        if not removed and os.fstat(descriptor).st_size == wal_size:
            removed = True
            try:
                wal.unlink()
            except PermissionError:
                # Windows does not permit removing this still-open descriptor.
                # The pathname simulation below models the same observer race.
                pass
        return chunk

    def missing_wal_after_hash(path, *args, **kwargs):
        if removed and Path(path) == wal:
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection.os, "read", remove_wal_during_hash)
        patch_context.setattr(freshness_projection.os, "stat", missing_wal_after_hash)
        removed_observation = freshness_projection.observe_freshness(tmp_path)
    assert removed is True
    assert removed_observation["state"] == "unknown"
    assert removed_observation["reason"] == "source_wal_path_replaced"

    wal.write_bytes(b"R" * (2 * 1024 * 1024))
    _p3c0_republish_current(tmp_path)
    replaced = False
    wal_identity = (wal.stat().st_dev, wal.stat().st_ino)

    def replace_wal_during_hash(descriptor, size):
        nonlocal replaced
        chunk = real_read(descriptor, size)
        descriptor_stat = os.fstat(descriptor)
        if not replaced and (descriptor_stat.st_dev, descriptor_stat.st_ino) == wal_identity:
            replaced = True
            replacement = wal.with_name(f".{wal.name}.during-hash")
            replacement.write_bytes(wal.read_bytes())
            try:
                os.replace(replacement, wal)
            except PermissionError:
                # Keep the descriptor stable and model the replaced pathname
                # below when Windows refuses replacement of an open file.
                replacement.unlink(missing_ok=True)
        return chunk

    def replaced_wal_after_hash(path, *args, **kwargs):
        if replaced and Path(path) == wal:
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection.os, "read", replace_wal_during_hash)
        patch_context.setattr(freshness_projection.os, "stat", replaced_wal_after_hash)
        replaced_observation = freshness_projection.observe_freshness(tmp_path)
    assert replaced is True
    assert replaced_observation["state"] == "unknown"
    assert replaced_observation["reason"] == "source_wal_path_replaced"

    _p3c0_republish_current(tmp_path)
    current = _p3c0_projection_payload(tmp_path)["databaseFingerprint"]
    calls = 0

    def changed_between_captures(_project):
        nonlocal calls
        calls += 1
        fingerprint = deepcopy(current)
        if calls == 2:
            fingerprint["files"]["memory.db"]["mtimeNs"] += 1
        return fingerprint, ""

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "capture_source_fingerprint", changed_between_captures)
        changed_observation = freshness_projection.observe_freshness(tmp_path)
    assert calls == 2
    assert changed_observation["state"] == "unknown"
    assert changed_observation["reason"] == "source_changed_during_validation"


def test_p3c0_r3_filesystem_race_during_observation_degrades_without_exception(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    current = _p3c0_projection_payload(tmp_path)["filesystemFingerprint"]
    calls = 0

    def changed_between_captures(_project):
        nonlocal calls
        calls += 1
        fingerprint = deepcopy(current)
        if calls == 2:
            fingerprint["projectDirectory"]["identity"]["inode"] += 1
        return fingerprint, ""

    monkeypatch.setattr(
        freshness_projection,
        "capture_filesystem_fingerprint",
        changed_between_captures,
    )

    observation = freshness_projection.observe_freshness(tmp_path)

    assert calls == 2
    assert observation["state"] == "unknown"
    assert observation["color"] == "YELLOW"
    assert observation["reason"] == "filesystem_changed_during_validation"
    assert observation["legacyStatus"] is None


def test_p3c0_projection_schema_and_capability_contracts_are_strict(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    valid = _p3c0_projection_payload(tmp_path)

    invalid_payloads = [
        {},
        {"schemaVersion": 999},
        {**valid, "schemaVersion": True},
        {**valid, "schemaVersion": 1.0},
        {**valid, "legacyStatus": {}},
        {**valid, "databaseFingerprint": {"algorithm": "sha256", "files": {}}},
        {**valid, "sourceOfTruth": True},
    ]
    incomplete_nested = deepcopy(valid)
    del incomplete_nested["legacyStatus"]["bootstrap"]["derivedArtifacts"]["views"]
    invalid_payloads.append(incomplete_nested)
    non_hex_fingerprint = deepcopy(valid)
    non_hex_fingerprint["databaseFingerprint"]["files"]["memory.db"]["sha256"] = "g" * 64
    invalid_payloads.append(non_hex_fingerprint)
    for payload in invalid_payloads:
        projection.write_text(json.dumps(payload), encoding="utf-8")
        observation = freshness_projection.observe_freshness(tmp_path)
        assert observation["state"] == "unknown"
        assert observation["color"] == "YELLOW"

    projection.write_text(json.dumps(valid), encoding="utf-8")
    without_profile = freshness_projection.observe_freshness(tmp_path)
    assert without_profile["state"] == "fresh"
    assert without_profile["capabilities"]["controlWorkStandaloneVector"] == "available"

    absent_db_project = tmp_path / "absent-db"
    absent_projection = freshness_projection.projection_path(absent_db_project)
    absent_projection.parent.mkdir(parents=True)
    absent_payload = deepcopy(valid)
    absent_payload["databaseFingerprint"]["files"]["memory.db"] = {
        "exists": False,
        "type": "absent",
        "identity": None,
        "sizeBytes": 0,
        "mtimeNs": 0,
        "sha256": "",
    }
    absent_payload["databaseFingerprint"]["files"]["memory.db-wal"] = deepcopy(absent_payload["databaseFingerprint"]["files"]["memory.db"])
    absent_projection.write_text(json.dumps(absent_payload), encoding="utf-8")
    absent_database = freshness_projection.observe_freshness(absent_db_project)
    assert absent_database["state"] == "unknown"
    assert absent_database["reason"] == "source_db_absent"

    profile = freshness_projection.capability_profile_path(tmp_path)
    profile.write_text("{not-json", encoding="utf-8")
    malformed_profile = freshness_projection.observe_freshness(tmp_path)
    assert malformed_profile["state"] == "unknown"
    assert malformed_profile["reason"] == "capability_profile_malformed"

    profile.write_text(json.dumps({
        "schemaVersion": 1,
        "checkout": "controlwork_standalone",
        "capabilities": {"vector": "not_applicable"},
    }), encoding="utf-8")
    assert freshness_projection.observe_freshness(tmp_path)["capabilities"]["controlWorkStandaloneVector"] == "not_applicable"

    profile.write_text(json.dumps({
        "schemaVersion": 1,
        "checkout": "controlcoding_v1",
        "capabilities": {"memoryHealthProjection": "not_applicable"},
    }), encoding="utf-8")
    not_applicable = freshness_projection.observe_freshness(tmp_path)
    assert not_applicable["state"] == "not_applicable"
    assert not_applicable["legacyStatus"] is None


def test_p3c0_r3_projection_json_pathologies_and_non_finite_values_fail_closed(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    valid = _p3c0_projection_payload(tmp_path)

    pathological_documents = [
        ("[]", None),
        ('{"integer":' + ("9" * 10000) + "}", ValueError),
        ('{"nested":' + ("[" * 2000) + "0" + ("]" * 2000) + "}", RecursionError),
    ]
    real_strict_loads = freshness_projection._strict_json_loads
    for raw, forced_error in pathological_documents:
        projection.write_text(raw, encoding="utf-8")
        if forced_error is None:
            observation = freshness_projection.observe_freshness(tmp_path)
        else:
            def raising_parser(_raw, error=forced_error):
                raise error("simulated pathological JSON parser failure")

            with monkeypatch.context() as patch_context:
                patch_context.setattr(freshness_projection, "_strict_json_loads", raising_parser)
                observation = freshness_projection.observe_freshness(tmp_path)
        assert observation["state"] == "unknown"
        assert observation["color"] == "YELLOW"
        assert observation["reason"] == "projection_malformed"
        assert observation["legacyStatus"] is None
    monkeypatch.setattr(freshness_projection, "_strict_json_loads", real_strict_loads)

    for non_finite in (float("nan"), float("inf"), float("-inf")):
        payload = deepcopy(valid)
        payload["legacyStatus"]["entities"] = non_finite
        projection.write_text(json.dumps(payload), encoding="utf-8")
        observation = freshness_projection.observe_freshness(tmp_path)
        assert observation["state"] == "unknown"
        assert observation["reason"] == "projection_malformed"
        assert freshness_projection._valid_json_value(non_finite) is False

    projection.write_text(json.dumps(valid, allow_nan=False), encoding="utf-8")
    before = projection.read_bytes()
    with pytest.raises(ValueError):
        freshness_projection._atomic_write_json(projection, {"nonFinite": float("nan")})
    assert projection.read_bytes() == before
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))


def test_p3c0_r3_projection_size_limit_is_checked_before_json_parse(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    projection.write_bytes(b"{" + (b" " * freshness_projection.HEALTH_PROJECTION_MAX_BYTES) + b"}")

    observation = freshness_projection.observe_freshness(tmp_path)

    assert projection.stat().st_size > freshness_projection.HEALTH_PROJECTION_MAX_BYTES
    assert observation["state"] == "unknown"
    assert observation["color"] == "YELLOW"
    assert observation["reason"] == "projection_too_large"
    assert observation["legacyStatus"] is None


def test_p3c0_projection_atomic_replacement_during_read_is_rejected(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    replacement = projection.with_name(f".{projection.name}.replacement")
    replacement.write_bytes(projection.read_bytes())
    real_capture = freshness_projection._capture_file_observation
    projection_captures = 0

    def replacing_capture(path, label, **kwargs):
        nonlocal projection_captures
        result = real_capture(path, label, **kwargs)
        if Path(path) == projection and label == "projection":
            projection_captures += 1
            if projection_captures == 1:
                os.replace(replacement, projection)
        return result

    monkeypatch.setattr(freshness_projection, "_capture_file_observation", replacing_capture)

    observation = freshness_projection.observe_freshness(tmp_path)

    assert projection_captures == 2
    assert observation["state"] == "unknown"
    assert observation["color"] == "YELLOW"
    assert observation["reason"] == "projection_changed_during_read"
    assert observation["legacyStatus"] is None


def test_p3c0_memory_connection_is_the_only_production_transaction_owner():
    package_root = Path(__file__).resolve().parent.parent / "scripts" / "cc_memory_lib"
    transaction_sites: dict[str, list[tuple[str, int]]] = {
        "begin": [],
        "commit": [],
        "rollback": [],
    }
    for module_path in sorted(package_root.glob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
            ):
                if node.func.attr in {"commit", "rollback"}:
                    transaction_sites[node.func.attr].append((module_path.name, node.lineno))
                elif (
                    node.func.attr == "execute"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.strip().upper() == "BEGIN"
                ):
                    transaction_sites["begin"].append((module_path.name, node.lineno))

    assert set(transaction_sites) == {"begin", "commit", "rollback"}
    for sites in transaction_sites.values():
        assert len(sites) == 1
        assert sites[0][0] == "store.py"


def test_p3c0_schema_migration_requires_caller_owned_transaction():
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(memory_migrations.SchemaMigrationError, match="caller-owned transaction"):
            memory_migrations.migrate_schema(conn)
        assert conn.in_transaction is False
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_p3c0_writer_publication_failure_contracts_and_cleanup(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    before_commit_failure = projection.read_bytes()
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    event_log_before_commit_failure = event_log.read_bytes()
    real_connect = memory_store._connect

    class CommitFailureConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def commit(self):
            raise sqlite3.OperationalError("simulated commit failure")

    monkeypatch.setattr(memory_store, "_connect", lambda project: CommitFailureConnection(real_connect(project)))
    with pytest.raises(sqlite3.OperationalError, match="commit failure"):
        with memory_store._memory_connection(tmp_path) as conn:
            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("p3c0_commit", "fail"))
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "create",
                "cc memory commit failure rollback probe",
            )
    assert projection.read_bytes() == before_commit_failure
    assert event_log.read_bytes() == event_log_before_commit_failure
    assert not freshness_projection.writer_lock_path(tmp_path).exists()
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))

    monkeypatch.setattr(memory_store, "_connect", real_connect)
    before_status_failure = projection.read_bytes()
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    decision_log = tmp_path / ".controlcoding" / "logs" / "decision_log.jsonl"
    event_log_before = event_log.read_bytes()
    decision_log_before = decision_log.read_bytes()

    def fail_status_snapshot(*_args, **_kwargs):
        raise RuntimeError("simulated status snapshot failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="status snapshot failure"):
            memory_entities._record_entity(
                tmp_path,
                "note",
                "P3-C0 production writer rollback probe",
                body="must roll back before projection snapshot failure",
                mirror_log="decision_log.jsonl",
            )
    assert projection.read_bytes() == before_status_failure
    assert event_log.read_bytes() == event_log_before
    assert decision_log.read_bytes() == decision_log_before
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT id FROM entities WHERE title = ?",
            ("P3-C0 production writer rollback probe",),
        ).fetchone() is None
    rolled_back = freshness_projection.observe_freshness(tmp_path)
    assert rolled_back["state"] in {"fresh", "unknown"}
    if rolled_back["state"] == "unknown":
        assert rolled_back["reason"] == "projection_outdated"
    assert not freshness_projection.writer_lock_path(tmp_path).exists()
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))

    _p3c0_republish_current(tmp_path)
    before_replace_failure = projection.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated projection replace failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection.os, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failure"):
            with memory_store._memory_connection(tmp_path) as conn:
                conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("p3c0_replace", "fail"))
    assert projection.read_bytes() == before_replace_failure
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "unknown"
    assert not freshness_projection.writer_lock_path(tmp_path).exists()
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))

    _p3c0_republish_current(tmp_path)
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("p3c0_success", "published"))
    coherent = _p3c0_projection_payload(tmp_path)
    fingerprint, reason = freshness_projection.capture_source_fingerprint(tmp_path)
    assert reason == ""
    assert coherent["databaseFingerprint"] == fingerprint
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"


def test_p3c0_transaction_rollback_restores_intake_move_and_event_log(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    assert cc.cmd_memory_intake_add(
        tmp_path,
        title="Rollback intake",
        summary="The source must remain retryable.",
        source_type="chat",
        path="docs/inbox/rollback-intake.md",
        json_output=True,
    ) == 0
    capsys.readouterr()
    source = tmp_path / "docs" / "inbox" / "rollback-intake.md"
    destination = tmp_path / "docs" / "research" / "rollback-intake.md"
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    source_before = source.read_bytes()
    event_log_before = event_log.read_bytes()
    projection_before = freshness_projection.projection_path(tmp_path).read_bytes()

    def fail_status_snapshot(*_args, **_kwargs):
        raise RuntimeError("simulated intake promotion snapshot failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="intake promotion snapshot failure"):
            cc.cmd_memory_intake_promote(
                tmp_path,
                "docs/inbox/rollback-intake.md",
                target="research",
                json_output=True,
            )

    assert source.read_bytes() == source_before
    assert not destination.exists()
    assert event_log.read_bytes() == event_log_before
    assert freshness_projection.projection_path(tmp_path).read_bytes() == projection_before
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        row = conn.execute(
            "SELECT path, lifecycle FROM entities WHERE title = ?",
            ("Rollback intake",),
        ).fetchone()
    assert dict(row) == {"path": "docs/inbox/rollback-intake.md", "lifecycle": "captured"}
    assert not freshness_projection.writer_lock_path(tmp_path).exists()


def test_p3c0_transaction_rollback_removes_new_intake_file(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    destination = tmp_path / "docs" / "inbox" / "rollback-add.md"
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    event_log_before = event_log.read_bytes()

    def fail_status_snapshot(*_args, **_kwargs):
        raise RuntimeError("simulated intake add snapshot failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="intake add snapshot failure"):
            cc.cmd_memory_intake_add(
                tmp_path,
                title="Rollback add",
                summary="This file must not survive rollback.",
                path="docs/inbox/rollback-add.md",
            )

    assert not destination.exists()
    assert event_log.read_bytes() == event_log_before
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT id FROM entities WHERE title = ?",
            ("Rollback add",),
        ).fetchone() is None


def test_p3c0_transaction_rollback_restores_manifest_and_generated_views(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    views_dir = tmp_path / ".controlcoding" / "views"
    manifest_before = manifest.read_bytes()
    event_log_before = event_log.read_bytes()
    views_before = {path.name: path.read_bytes() for path in views_dir.glob("*.md")}

    def fail_status_snapshot(*_args, **_kwargs):
        raise RuntimeError("simulated filesystem publication snapshot failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="filesystem publication snapshot failure"):
            cc.cmd_memory_init(tmp_path, mode="full", project_short="Changed")
    assert manifest.read_bytes() == manifest_before
    assert event_log.read_bytes() == event_log_before

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="filesystem publication snapshot failure"):
            memory_views._generate_views(tmp_path, command="cc memory rollback view probe", quiet=True)
    assert {path.name: path.read_bytes() for path in views_dir.glob("*.md")} == views_before
    assert event_log.read_bytes() == event_log_before

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="filesystem publication snapshot failure"):
            memory_sessions.cmd_memory_session_views(tmp_path)
    assert {path.name: path.read_bytes() for path in views_dir.glob("*.md")} == views_before
    assert event_log.read_bytes() == event_log_before


def test_p3c0_reinit_reads_manifest_only_after_writer_lock(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    real_lock = freshness_projection.projection_writer_lock
    locked_created_at = "2026-08-16T00:00:00Z"

    @contextmanager
    def mutate_manifest_under_lock(project, timeout_seconds=30.0):
        with real_lock(project, timeout_seconds=timeout_seconds):
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["created_at"] = locked_created_at
            payload["project_short"] = "LockedProject"
            manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            yield

    monkeypatch.setattr(
        freshness_projection,
        "projection_writer_lock",
        mutate_manifest_under_lock,
    )

    assert memory_commands.cmd_memory_init(tmp_path, mode="full") == 0
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["created_at"] == locked_created_at
    assert payload["project_short"] == "LockedProject"


def test_p3c0_reinit_projection_failure_restores_database_and_filesystem(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    projection = freshness_projection.projection_path(tmp_path)
    views_dir = tmp_path / ".controlcoding" / "views"
    database_artifacts = memory_store._initial_database_artifacts(tmp_path)
    manifest_before = manifest.read_bytes()
    event_log_before = event_log.read_bytes()
    projection_before = projection.read_bytes()
    views_before = {path.name: path.read_bytes() for path in views_dir.glob("*.md")}
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        metadata_before = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    def persistent_database_snapshot(path):
        snapshot = _p3c0_file_snapshot(path)
        snapshot.pop("identity", None)
        return snapshot

    database_before = {path: persistent_database_snapshot(path) for path in database_artifacts}

    def fail_publication(*_args, **_kwargs):
        raise RuntimeError("simulated re-init projection publication failure")

    monkeypatch.setattr(freshness_projection, "publish_freshness_projection", fail_publication)

    with pytest.raises(RuntimeError, match="re-init projection publication failure"):
        memory_commands.cmd_memory_init(tmp_path, mode="full", project_short="Changed")

    assert {path: persistent_database_snapshot(path) for path in database_artifacts} == database_before
    assert manifest.read_bytes() == manifest_before
    assert event_log.read_bytes() == event_log_before
    assert projection.read_bytes() == projection_before
    assert {path.name: path.read_bytes() for path in views_dir.glob("*.md")} == views_before
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert dict(conn.execute("SELECT key, value FROM metadata").fetchall()) == metadata_before
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before


def test_p3c0_writer_rejects_linked_directory_ancestor(tmp_path, monkeypatch):
    project = tmp_path / "linked-project"
    external_control = tmp_path / "external-control"
    project.mkdir()
    (external_control / "memory").mkdir(parents=True)
    sentinel = external_control / "sentinel.bin"
    sentinel.write_bytes(b"outside-project\n")
    link_kind = _p3c0_create_directory_link(project / ".controlcoding", external_control)
    external_before = _p3c0_directory_snapshot(external_control)

    failure = None
    try:
        memory_commands.cmd_memory_init(project, mode="document-only", project_short="Linked")
    except BaseException as exc:
        failure = exc

    assert link_kind in {"symlink", "junction"}
    assert isinstance(failure, RuntimeError)
    assert "physical" in str(failure)
    assert ".controlcoding" in str(failure)
    assert _p3c0_directory_snapshot(external_control) == external_before
    assert sentinel.read_bytes() == b"outside-project\n"
    assert not freshness_projection.writer_lock_path(project).exists()


def test_p3c0_writer_rejects_nonregular_database_before_connect(
    tmp_path,
    capsys,
    monkeypatch,
):
    database = _p3c0_init_project(tmp_path, capsys)
    database.unlink()
    database.mkdir()
    connect_reached = False

    def unexpected_connect(_project):
        nonlocal connect_reached
        connect_reached = True
        raise AssertionError("SQLite connect reached a nonregular database pathname")

    monkeypatch.setattr(memory_store, "_connect", unexpected_connect)
    failure = None
    try:
        memory_commands.cmd_memory_init(tmp_path, mode="document-only", project_short="Changed")
    except BaseException as exc:
        failure = exc

    assert connect_reached is False
    assert isinstance(failure, RuntimeError)
    assert "database artifact must be a physical regular file" in str(failure)
    assert str(database) in str(failure)
    assert database.is_dir()


def test_p3c0_writer_rejects_symlink_database_before_connect(
    tmp_path,
    capsys,
    monkeypatch,
):
    database = _p3c0_init_project(tmp_path, capsys)
    external_database = tmp_path / "external-memory.db"
    external_database.write_bytes(database.read_bytes())
    external_before = external_database.read_bytes()
    database.unlink()
    try:
        database.symlink_to(external_database)
    except OSError as exc:
        _p3c0_skip_if_windows_symlink_privilege_error(
            exc,
            "database symlink creation is unavailable",
        )
    connect_reached = False

    def unexpected_connect(_project):
        nonlocal connect_reached
        connect_reached = True
        raise AssertionError("SQLite connect reached a symlink database pathname")

    monkeypatch.setattr(memory_store, "_connect", unexpected_connect)
    failure = None
    try:
        memory_commands.cmd_memory_init(tmp_path, mode="document-only", project_short="Changed")
    except BaseException as exc:
        failure = exc

    assert connect_reached is False
    assert isinstance(failure, RuntimeError)
    assert "database artifact must be a physical regular file" in str(failure)
    assert str(database) in str(failure)
    assert database.is_symlink()
    assert external_database.read_bytes() == external_before


def test_p3c0_writer_rejects_hardlinked_main_database_before_connect(
    tmp_path,
    capsys,
    monkeypatch,
):
    database = _p3c0_init_project(tmp_path, capsys)
    alias = tmp_path / "external-memory.db"
    os.link(database, alias, follow_symlinks=False)
    artifacts = memory_store._initial_database_artifacts(tmp_path)
    artifacts_before = {
        path: _p3c0_file_snapshot(path)
        for path in artifacts
    }
    database_before = _p3c0_hardlink_snapshot(database)
    alias_before = _p3c0_hardlink_snapshot(alias)
    identity_opens: list[Path] = []
    connect_reached = False
    real_identity_open = memory_store._open_identity_bound_regular

    def observe_identity_open(path, flags, mode=0o666):
        identity_opens.append(Path(path))
        return real_identity_open(path, flags, mode)

    def unexpected_connect(_project):
        nonlocal connect_reached
        connect_reached = True
        raise AssertionError("SQLite connect reached a hardlinked main database")

    monkeypatch.setattr(
        memory_store,
        "_open_identity_bound_regular",
        observe_identity_open,
    )
    monkeypatch.setattr(memory_store, "_connect", unexpected_connect)

    with pytest.raises(RuntimeError, match="exactly one hard link") as caught:
        memory_commands.cmd_memory_init(
            tmp_path,
            mode="document-only",
            project_short="HardlinkedMain",
        )

    assert str(database) in str(caught.value)
    assert connect_reached is False
    assert database in identity_opens
    assert _p3c0_hardlink_snapshot(database) == database_before
    assert _p3c0_hardlink_snapshot(alias) == alias_before
    assert database_before["identity"] == alias_before["identity"]
    assert database_before["linkCount"] == alias_before["linkCount"] == 2
    assert {
        path: _p3c0_file_snapshot(path)
        for path in artifacts
    } == artifacts_before


@pytest.mark.parametrize(
    "suffix",
    ["-wal", "-shm", "-journal"],
    ids=["wal", "shm", "journal"],
)
def test_p3c0_writer_rejects_hardlinked_existing_database_sidecar_before_connect(
    tmp_path,
    capsys,
    monkeypatch,
    suffix,
):
    database = _p3c0_init_project(tmp_path, capsys)
    artifacts = memory_store._initial_database_artifacts(tmp_path)
    sidecar = Path(f"{database}{suffix}")
    assert sidecar in artifacts[1:]
    sidecar.write_bytes(f"sentinel:{suffix}\n".encode("utf-8"))
    alias = tmp_path / f"external-memory.db{suffix}"
    os.link(sidecar, alias, follow_symlinks=False)
    artifacts_before = {
        path: _p3c0_file_snapshot(path)
        for path in artifacts
    }
    database_before = _p3c0_hardlink_snapshot(database)
    sidecar_before = _p3c0_hardlink_snapshot(sidecar)
    alias_before = _p3c0_hardlink_snapshot(alias)
    identity_opens: list[Path] = []
    connect_reached = False
    real_identity_open = memory_store._open_identity_bound_regular

    def observe_identity_open(path, flags, mode=0o666):
        identity_opens.append(Path(path))
        return real_identity_open(path, flags, mode)

    def unexpected_connect(_project):
        nonlocal connect_reached
        connect_reached = True
        raise AssertionError(f"SQLite connect reached a hardlinked sidecar: {sidecar}")

    monkeypatch.setattr(
        memory_store,
        "_open_identity_bound_regular",
        observe_identity_open,
    )
    monkeypatch.setattr(memory_store, "_connect", unexpected_connect)

    with pytest.raises(RuntimeError, match="exactly one hard link") as caught:
        memory_commands.cmd_memory_init(
            tmp_path,
            mode="document-only",
            project_short="HardlinkedSidecar",
        )

    assert str(sidecar) in str(caught.value)
    assert connect_reached is False
    assert sidecar in identity_opens
    assert _p3c0_hardlink_snapshot(database) == database_before
    assert _p3c0_hardlink_snapshot(sidecar) == sidecar_before
    assert _p3c0_hardlink_snapshot(alias) == alias_before
    assert sidecar_before["identity"] == alias_before["identity"]
    assert sidecar_before["linkCount"] == alias_before["linkCount"] == 2
    assert {
        path: _p3c0_file_snapshot(path)
        for path in artifacts
    } == artifacts_before


def test_p3c0_writer_accepts_physical_directory_chain(tmp_path, capsys):
    project = tmp_path / "physical-project"
    memory_directory = project / ".controlcoding" / "memory"
    memory_directory.mkdir(parents=True)

    assert memory_commands.cmd_memory_init(
        project,
        mode="document-only",
        project_short="Physical",
    ) == 0
    capsys.readouterr()

    database = memory_store._db_path(project)
    assert database.is_file()
    assert database.lstat().st_nlink == 1
    assert memory_commands.cmd_memory_init(
        project,
        mode="document-only",
        project_short="PhysicalAgain",
    ) == 0
    capsys.readouterr()
    assert database.lstat().st_nlink == 1
    assert not any(
        getattr(path.lstat(), "st_reparse_tag", 0)
        for path in (project, project / ".controlcoding", memory_directory)
    )


def test_p3c0_transactional_write_same_bytes_rejects_linked_parent_before_descendant_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "linked-write-project"
    external = tmp_path / "linked-write-external"
    _p3c0_init_project(project, capsys)
    (project / "nested").mkdir()
    external.mkdir()
    linked_parent = project / "nested" / "linked-parent"
    link_kind = _p3c0_create_directory_link(linked_parent, external)
    content = b"same bytes outside the project\n"
    external_target = external / "same-bytes.bin"
    external_target.write_bytes(content)
    logical_target = linked_parent / external_target.name
    external_before = _p3c0_directory_snapshot(external)
    real_rollback_state = memory_store._file_rollback_state
    descendant_access_reached = False

    def observe_descendant_baseline(path, *args, **kwargs):
        nonlocal descendant_access_reached
        if Path(path) == logical_target:
            descendant_access_reached = True
        return real_rollback_state(path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "_file_rollback_state", observe_descendant_baseline)
    failure = None
    with memory_store._memory_connection(project) as conn:
        try:
            memory_store._transactional_write_bytes(conn, logical_target, content)
        except BaseException as exc:
            failure = exc

    assert link_kind in {"symlink", "junction"}
    assert descendant_access_reached is False
    assert isinstance(failure, RuntimeError)
    assert "physical" in str(failure)
    assert str(linked_parent) in str(failure)
    assert _p3c0_directory_snapshot(external) == external_before
    assert external_target.read_bytes() == content
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_transaction_move_rejects_linked_destination_parent_before_descendant_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "linked-move-project"
    external = tmp_path / "linked-move-external"
    _p3c0_init_project(project, capsys)
    source_parent = project / "docs" / "source"
    source_parent.mkdir(parents=True)
    external.mkdir()
    source = source_parent / "move-source.md"
    source.write_bytes(b"physical move source\n")
    linked_parent = project / "docs" / "linked-destination"
    link_kind = _p3c0_create_directory_link(linked_parent, external)
    destination = linked_parent / "move-destination.md"
    (external / "sentinel.bin").write_bytes(b"external sentinel\n")
    source_before = _p3c0_file_snapshot(source)
    external_before = _p3c0_directory_snapshot(external)
    real_file_identity_state = memory_store._file_identity_state
    real_path_entry_exists = memory_store._path_entry_exists
    real_link = memory_store.os.link
    real_replace = memory_store.os.replace
    boundaries = {
        "source_baseline": False,
        "destination_access": False,
        "link": False,
        "replace": False,
    }

    def observe_source_baseline(path):
        if Path(path) == source:
            boundaries["source_baseline"] = True
        return real_file_identity_state(path)

    def observe_destination_access(path):
        if Path(path) == destination:
            boundaries["destination_access"] = True
        return real_path_entry_exists(path)

    def observe_link(source_path, destination_path, *args, **kwargs):
        if Path(source_path) == source or Path(destination_path) == destination:
            boundaries["link"] = True
        return real_link(source_path, destination_path, *args, **kwargs)

    def observe_replace(source_path, destination_path, *args, **kwargs):
        if Path(source_path) in {source, destination} or Path(destination_path) in {
            source,
            destination,
        }:
            boundaries["replace"] = True
        return real_replace(source_path, destination_path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "_file_identity_state", observe_source_baseline)
    monkeypatch.setattr(memory_store, "_path_entry_exists", observe_destination_access)
    monkeypatch.setattr(memory_store.os, "link", observe_link)
    monkeypatch.setattr(memory_store.os, "replace", observe_replace)
    failure = None
    with memory_store._memory_connection(project) as conn:
        try:
            memory_store._transactional_move_file(conn, source, destination)
        except BaseException as exc:
            failure = exc

    assert link_kind in {"symlink", "junction"}
    assert boundaries == {
        "source_baseline": False,
        "destination_access": False,
        "link": False,
        "replace": False,
    }
    assert isinstance(failure, RuntimeError)
    assert "physical" in str(failure)
    assert str(linked_parent) in str(failure)
    assert _p3c0_file_snapshot(source) == source_before
    assert not (external / destination.name).exists()
    assert _p3c0_directory_snapshot(external) == external_before
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_transactional_write_uses_one_normalized_path_for_validation_and_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "normalized-write-project"
    external = tmp_path / "normalized-write-external"
    _p3c0_init_project(project, capsys)
    nested = project / "nested"
    nested.mkdir()
    (external / "child").mkdir(parents=True)
    linked_parent = nested / "linked-parent"
    link_kind = _p3c0_create_directory_link(linked_parent, external / "child")
    content = b"normalized write content\n"
    external_target = external / "normalized-target.bin"
    external_target.write_bytes(content)
    logical_target = linked_parent / ".." / external_target.name
    normalized_target = nested / external_target.name
    external_before = _p3c0_directory_snapshot(external)
    real_rollback_state = memory_store._file_rollback_state
    access_paths = {"original": False, "normalized": False}

    def observe_baseline_path(path, *args, **kwargs):
        observed = Path(path)
        if observed == logical_target:
            access_paths["original"] = True
        if observed == normalized_target:
            access_paths["normalized"] = True
        return real_rollback_state(path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "_file_rollback_state", observe_baseline_path)

    with memory_store._memory_connection(project) as conn:
        memory_store._transactional_write_bytes(conn, logical_target, content)

    assert link_kind in {"symlink", "junction"}
    assert access_paths == {"original": False, "normalized": True}
    assert normalized_target.read_bytes() == content
    assert external_target.read_bytes() == content
    assert _p3c0_directory_snapshot(external) == external_before
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_transaction_move_uses_one_normalized_destination_for_validation_and_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "normalized-move-project"
    external = tmp_path / "normalized-move-external"
    _p3c0_init_project(project, capsys)
    source_parent = project / "docs" / "source"
    source_parent.mkdir(parents=True)
    (external / "child").mkdir(parents=True)
    source = source_parent / "move-source.md"
    source_content = b"normalized move source\n"
    source.write_bytes(source_content)
    linked_parent = project / "docs" / "linked-destination"
    link_kind = _p3c0_create_directory_link(linked_parent, external / "child")
    logical_destination = linked_parent / ".." / "move-destination.md"
    normalized_destination = project / "docs" / "move-destination.md"
    (external / "sentinel.bin").write_bytes(b"external sentinel\n")
    external_before = _p3c0_directory_snapshot(external)
    real_path_entry_exists = memory_store._path_entry_exists
    access_paths = {"original": False, "normalized": False}

    def guard_destination_access(path):
        observed = Path(path)
        if observed == logical_destination:
            access_paths["original"] = True
            raise AssertionError("move observed a destination path different from the validated path")
        if observed == normalized_destination:
            access_paths["normalized"] = True
        return real_path_entry_exists(path)

    monkeypatch.setattr(memory_store, "_path_entry_exists", guard_destination_access)
    failure = None
    with memory_store._memory_connection(project) as conn:
        try:
            memory_store._transactional_move_file(conn, source, logical_destination)
        except BaseException as exc:
            failure = exc

    assert link_kind in {"symlink", "junction"}
    assert failure is None
    assert access_paths == {"original": False, "normalized": True}
    assert not source.exists()
    assert normalized_destination.read_bytes() == source_content
    assert not (external / logical_destination.name).exists()
    assert _p3c0_directory_snapshot(external) == external_before
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_writer_lock_exit_failure_compensates_committed_init(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    projection = freshness_projection.projection_path(tmp_path)
    views_dir = tmp_path / ".controlcoding" / "views"
    database_artifacts = memory_store._initial_database_artifacts(tmp_path)
    manifest_before = manifest.read_bytes()
    event_log_before = event_log.read_bytes()
    projection_before = projection.read_bytes()
    views_before = {path.name: path.read_bytes() for path in views_dir.glob("*.md")}

    def persistent_database_snapshot(path):
        snapshot = _p3c0_file_snapshot(path)
        snapshot.pop("identity", None)
        return snapshot

    with memory_store._readonly_memory_connection(tmp_path) as conn:
        metadata_before = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    database_before = {path: persistent_database_snapshot(path) for path in database_artifacts}

    real_lock = freshness_projection.projection_writer_lock
    exit_reached = False
    committed_publication_seen = False
    lock_release_error = RuntimeError("simulated projection writer lock exit failure")

    @contextmanager
    def fail_after_real_lock_release(project, timeout_seconds=30.0):
        nonlocal exit_reached, committed_publication_seen
        with real_lock(project, timeout_seconds=timeout_seconds):
            yield
            committed_publication_seen = (
                manifest.read_bytes() != manifest_before
                and projection.read_bytes() != projection_before
            )
        exit_reached = True
        raise lock_release_error

    monkeypatch.setattr(
        freshness_projection,
        "projection_writer_lock",
        fail_after_real_lock_release,
    )

    with pytest.raises(RuntimeError) as caught:
        memory_commands.cmd_memory_init(tmp_path, mode="full", project_short="Changed")

    assert exit_reached is True
    assert committed_publication_seen is True
    assert caught.value is lock_release_error
    assert {path: persistent_database_snapshot(path) for path in database_artifacts} == database_before
    assert manifest.read_bytes() == manifest_before
    assert event_log.read_bytes() == event_log_before
    assert projection.read_bytes() == projection_before
    assert {path.name: path.read_bytes() for path in views_dir.glob("*.md")} == views_before
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert dict(conn.execute("SELECT key, value FROM metadata").fetchall()) == metadata_before
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
    assert not freshness_projection.writer_lock_path(tmp_path).exists()


@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_writer_lock_exit_failure_preserves_primary_when_compensation_fails(
    tmp_path,
    capsys,
    monkeypatch,
    primary_type,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    decision_log = tmp_path / ".controlcoding" / "logs" / "decision_log.jsonl"
    projection = freshness_projection.projection_path(tmp_path)
    view = sorted((tmp_path / ".controlcoding" / "views").glob("*.md"))[0]
    database_artifacts = memory_store._initial_database_artifacts(tmp_path)
    manifest_before = manifest.read_bytes()
    event_log_before = event_log.read_bytes()
    decision_log_before = decision_log.read_bytes()
    projection_before = projection.read_bytes()
    view_before = view.read_bytes()

    def persistent_database_snapshot(path):
        snapshot = _p3c0_file_snapshot(path)
        snapshot.pop("identity", None)
        return snapshot

    database_before = {path: persistent_database_snapshot(path) for path in database_artifacts}
    manifest_payload = json.loads(manifest_before)
    manifest_payload["project_short"] = "LockPrimary"
    preexisting_cause = ValueError("pre-existing lock release cause")
    original_lock_release_error = primary_type("original lock release error")
    original_lock_release_error.__cause__ = preexisting_cause
    original_lock_release_error.add_note("pre-existing lock release note")
    secondary_compensation_error = OSError(
        errno.EIO,
        "secondary compensation error",
    )
    secondary_compensation_error.add_note("secondary compensation nested note")
    real_lock = freshness_projection.projection_writer_lock
    exit_reached = False
    committed_publication_seen = False
    compensation_calls = 0
    observed_primary: dict[str, object] = {}

    @contextmanager
    def fail_after_real_lock_release(project, timeout_seconds=30.0):
        nonlocal exit_reached, committed_publication_seen
        with real_lock(project, timeout_seconds=timeout_seconds):
            yield
            committed_publication_seen = (
                persistent_database_snapshot(database_artifacts[0])
                != database_before[database_artifacts[0]]
                and manifest.read_bytes() != manifest_before
                and event_log.read_bytes() != event_log_before
                and decision_log.read_bytes() != decision_log_before
                and projection.read_bytes() != projection_before
                and view.read_bytes() != view_before
            )
        exit_reached = True
        raise original_lock_release_error

    def fail_secondary_compensation():
        nonlocal compensation_calls
        compensation_calls += 1
        active_exception = sys.exception()
        observed_primary["exception"] = active_exception
        observed_primary["traceback"] = getattr(active_exception, "__traceback__", None)
        observed_primary["cause"] = getattr(active_exception, "__cause__", None)
        raise secondary_compensation_error

    monkeypatch.setattr(
        freshness_projection,
        "projection_writer_lock",
        fail_after_real_lock_release,
    )

    with pytest.raises(primary_type) as caught:
        with memory_store._memory_connection(
            tmp_path,
            compensate_committed_failure=True,
        ) as conn:
            memory_store._transactional_write_json(conn, manifest, manifest_payload)
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "edit",
                "p3c0 lock primary composition",
                mirror_log="decision_log.jsonl",
            )
            memory_store._transactional_write_bytes(
                conn,
                view,
                view_before + b"\nLock primary composition probe.\n",
            )
            memory_store._register_memory_rollback(conn, fail_secondary_compensation)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("p3c0_lock_primary", "committed"),
            )

    assert exit_reached is True
    assert committed_publication_seen is True
    assert compensation_calls == 1
    assert observed_primary["exception"] is original_lock_release_error
    assert caught.value is original_lock_release_error
    observed_traceback = observed_primary["traceback"]
    final_traceback = caught.value.__traceback__
    traceback_cursor = final_traceback
    while traceback_cursor is not None and traceback_cursor is not observed_traceback:
        traceback_cursor = traceback_cursor.tb_next
    assert traceback_cursor is observed_traceback
    observed_frames = traceback.extract_tb(observed_traceback)
    assert traceback.extract_tb(final_traceback)[-len(observed_frames):] == observed_frames
    assert caught.value.__cause__ is preexisting_cause
    assert observed_primary["cause"] is preexisting_cause
    assert caught.value.__notes__[0] == "pre-existing lock release note"
    assert "memory transaction cleanup did not restore the initial filesystem" in caught.value.__notes__
    assert any(
        secondary_compensation_error.__class__.__name__ in note
        and str(secondary_compensation_error) in note
        for note in caught.value.__notes__
    )
    assert "nested: secondary compensation nested note" in caught.value.__notes__
    assert {path: persistent_database_snapshot(path) for path in database_artifacts} == database_before
    assert manifest.read_bytes() == manifest_before
    assert event_log.read_bytes() == event_log_before
    assert decision_log.read_bytes() == decision_log_before
    assert projection.read_bytes() == projection_before
    assert view.read_bytes() == view_before
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    assert not freshness_projection.writer_lock_path(tmp_path).exists()


def test_p3c0_writer_lock_exit_failure_compensates_default_first_init(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "default-first-init"
    project.mkdir()
    projection = freshness_projection.projection_path(project)
    database = memory_store._db_path(project)
    real_lock = freshness_projection.projection_writer_lock
    lock_release_error = RuntimeError("simulated default first-init lock exit failure")
    exit_reached = False
    committed_publication_seen = False

    @contextmanager
    def fail_after_real_lock_release(locked_project, timeout_seconds=30.0):
        nonlocal exit_reached, committed_publication_seen
        with real_lock(locked_project, timeout_seconds=timeout_seconds):
            yield
            committed_publication_seen = database.is_file() and projection.is_file()
        exit_reached = True
        raise lock_release_error

    monkeypatch.setattr(
        freshness_projection,
        "projection_writer_lock",
        fail_after_real_lock_release,
    )

    failure = None
    try:
        with memory_store._memory_connection(project) as conn:
            memory_store._ensure_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("default_first_init", "committed"),
            )
    except BaseException as exc:
        failure = exc

    assert exit_reached is True, repr(failure)
    assert committed_publication_seen is True, repr(failure)
    assert failure is lock_release_error
    assert not (project / ".controlcoding").exists()
    assert not freshness_projection.writer_lock_path(project).exists()


def test_p3c0_file_rollback_state_rejects_path_replacement_during_baseline_capture(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "rollback-baseline.txt"
    replacement = tmp_path / ".rollback-baseline-replacement"
    target.write_bytes(b"baseline-a\n")
    real_lstat = Path.lstat
    injected = False

    def replace_after_initial_observation(path):
        nonlocal injected
        details = real_lstat(path)
        if Path(path) == target and not injected:
            replacement.write_bytes(b"foreign-b\n")
            os.replace(replacement, target)
            injected = True
        return details

    monkeypatch.setattr(Path, "lstat", replace_after_initial_observation)
    failure = None
    try:
        memory_store._file_rollback_state(target)
    except BaseException as exc:
        failure = exc

    assert injected is True
    assert isinstance(failure, RuntimeError)
    assert "changed while capturing rollback baseline" in str(failure)
    assert str(target) in str(failure)
    assert target.read_bytes() == b"foreign-b\n"


def test_p3c0_file_rollback_state_rejects_same_inode_path_reincarnation(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "rollback-same-inode.txt"
    anchor = tmp_path / ".rollback-same-inode-anchor"
    target.write_bytes(b"baseline-same-inode\n")
    os.link(target, anchor, follow_symlinks=False)
    initial = target.lstat()
    real_lstat = Path.lstat
    real_open = os.open
    real_link = os.link
    reincarnated = False

    def reincarnate_before_open(path, flags, mode=0o777):
        nonlocal reincarnated
        if Path(path) == target and not reincarnated:
            target.unlink()
            real_link(anchor, target, follow_symlinks=False)
            reincarnated = True
        return real_open(path, flags, mode)

    def observe_path_generation(path):
        details = real_lstat(path)
        if Path(path) != target or not reincarnated:
            return details
        return SimpleNamespace(
            st_mode=details.st_mode,
            st_dev=details.st_dev,
            st_ino=details.st_ino,
            st_size=details.st_size,
            st_atime_ns=details.st_atime_ns,
            st_mtime_ns=details.st_mtime_ns,
            st_ctime_ns=initial.st_ctime_ns + 1,
            st_nlink=details.st_nlink,
            st_reparse_tag=getattr(details, "st_reparse_tag", 0),
        )

    monkeypatch.setattr(memory_store.os, "open", reincarnate_before_open)
    monkeypatch.setattr(Path, "lstat", observe_path_generation)
    failure = None
    try:
        memory_store._file_rollback_state(target)
    except BaseException as exc:
        failure = exc

    assert reincarnated is True
    assert isinstance(failure, RuntimeError)
    assert "pathname changed while capturing rollback baseline" in str(failure)
    assert target.read_bytes() == b"baseline-same-inode\n"
    assert target.stat().st_ino == anchor.stat().st_ino


def test_p3c0_transaction_failure_resets_context_and_surfaces_compensation_error(
    tmp_path,
    capsys,
):
    _p3c0_init_project(tmp_path, capsys)

    def fail_compensation():
        raise OSError("simulated compensation failure")

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect") as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._register_memory_rollback(conn, fail_compensation)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("p3c0_compensation", "must-roll-back"),
            )
            raise ValueError("trigger transaction rollback")

    assert any("simulated compensation failure" in note for note in caught.value.__notes__)
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT value FROM metadata WHERE key = 'p3c0_compensation'"
        ).fetchone() is None


def test_p3c0_transaction_jsonl_rollback_fails_closed_on_foreign_suffix(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    baseline = event_log.read_bytes()
    foreign = b'{"foreign":true}\n'

    def append_foreign_then_fail(*_args, **_kwargs):
        with event_log.open("ab") as handle:
            handle.write(foreign)
            handle.flush()
            os.fsync(handle.fileno())
        raise RuntimeError("simulated snapshot failure after foreign append")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            freshness_projection,
            "build_legacy_status_snapshot",
            append_foreign_then_fail,
        )
        with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect"):
            with memory_store._memory_connection(tmp_path) as conn:
                memory_ledger._insert_event(
                    conn,
                    tmp_path,
                    "create",
                    "cc memory owned append one",
                )
                memory_ledger._insert_event(
                    conn,
                    tmp_path,
                    "edit",
                    "cc memory owned append two",
                )

    current = event_log.read_bytes()
    assert current.startswith(baseline)
    assert current.endswith(foreign)
    assert b"cc memory owned append one" in current
    assert b"cc memory owned append two" in current
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE command IN (?, ?)",
            ("cc memory owned append one", "cc memory owned append two"),
        ).fetchone()[0] == 0


def test_p3c0_failed_first_init_removes_owned_layout_logs_database_and_profile_dirs(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "fresh-project"

    def fail_status_snapshot(*_args, **_kwargs):
        raise RuntimeError("simulated first init snapshot failure")

    monkeypatch.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
    with pytest.raises(RuntimeError, match="first init snapshot failure"):
        memory_commands.cmd_memory_init(
            project,
            mode="document-only",
            profile="work",
            project_short="Fresh",
        )

    assert not (project / ".controlcoding").exists()
    assert not (project / "docs").exists()
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_first_init_captures_database_baseline_after_writer_lock(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "waiting-initializer"
    database = project / ".controlcoding" / "memory" / "memory.db"
    wal = Path(f"{database}-wal")
    database_bytes = b"completed initializer database"
    wal_bytes = b"completed initializer wal"

    @contextmanager
    def completed_initializer_lock(_project):
        database.parent.mkdir(parents=True)
        database.write_bytes(database_bytes)
        wal.write_bytes(wal_bytes)
        yield

    def fail_connect(_project):
        raise sqlite3.OperationalError("simulated waiting initializer connect failure")

    monkeypatch.setattr(freshness_projection, "projection_writer_lock", completed_initializer_lock)
    monkeypatch.setattr(memory_store, "_connect", fail_connect)

    with pytest.raises(sqlite3.OperationalError, match="waiting initializer connect failure"):
        with memory_store._memory_connection(project):
            pass

    assert database.read_bytes() == database_bytes
    assert wal.read_bytes() == wal_bytes
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_default_first_init_restores_preexisting_sqlite_sidecars_and_filesystem_outputs(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "preexisting-sidecar-first-init"
    parking = tmp_path / "preexisting-sidecar-parking"
    parking.mkdir()
    _p3c0_init_project(project, capsys)
    database_artifacts = memory_store._initial_database_artifacts(project)
    database = database_artifacts[0]
    sidecars = database_artifacts[1:]
    manifest = memory_store._manifest_path(project)
    projection = freshness_projection.projection_path(project)
    views = sorted((project / ".controlcoding" / "views").glob("*.md"))
    logs = [project / ".controlcoding" / "logs" / name for name in memory_store.REQUIRED_LOGS]
    manifest_before = manifest.read_bytes()
    projection_before = projection.read_bytes()
    views_before = {path: path.read_bytes() for path in views}
    logs_before = {path: path.read_bytes() for path in logs}
    manifest_payload = json.loads(manifest_before)
    manifest_payload["project_short"] = "SidecarFirstInit"
    sentinel_bytes = {
        path: f"sentinel:{path.name}\n".encode("utf-8")
        for path in sidecars
    }
    mutated_bytes = {
        path: f"mutated:{path.name}\n".encode("utf-8")
        for path in sidecars
    }
    for path in database_artifacts:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    for path, content in sentinel_bytes.items():
        path.write_bytes(content)
    sentinel_identities = {
        path: memory_store._regular_file_object_identity(path)
        for path in sidecars
    }
    parked_paths = {
        path: parking / f"{index}-{path.name}"
        for index, path in enumerate(sidecars)
    }
    seam_flags = {
        "capture_content_false": False,
        "main_absent": False,
        "sentinel_sidecars_seen": False,
        "sidecar_rollbacks_complete": False,
        "sidecars_mutated": False,
        "post_publication_failure": False,
    }
    real_baselines = memory_store._database_artifact_baselines
    real_connect = memory_store._connect
    real_lock = freshness_projection.projection_writer_lock
    original_lock_release_error = RuntimeError("post-publication sidecar failure")

    def observe_database_baselines(locked_project, *, capture_content=False):
        seam_flags["capture_content_false"] = capture_content is False
        baselines = real_baselines(
            locked_project,
            capture_content=capture_content,
        )
        seam_flags["main_absent"] = baselines[database].existed is False
        seam_flags["sentinel_sidecars_seen"] = all(
            baselines[path].existed
            and baselines[path].identity == sentinel_identities[path]
            for path in sidecars
        )
        seam_flags["sidecar_rollbacks_complete"] = all(
            baselines[path].rollback is not None
            and baselines[path].rollback.content == sentinel_bytes[path]
            and baselines[path].rollback.identity == sentinel_identities[path]
            for path in sidecars
        )
        return baselines

    class RestoreSentinelsOnClose:
        def __init__(self, connection):
            self._connection = connection
            self._closed = False

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            for path, parked in parked_paths.items():
                try:
                    current = memory_store._regular_file_object_identity(path)
                except FileNotFoundError:
                    current = None
                if current is not None:
                    path.unlink()
                os.replace(parked, path)
                with path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(mutated_bytes[path])
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
            seam_flags["sidecars_mutated"] = all(
                path.read_bytes() == mutated_bytes[path]
                and memory_store._regular_file_object_identity(path) == sentinel_identities[path]
                for path in sidecars
            )

    def connect_after_parking_sentinels(locked_project):
        for path, parked in parked_paths.items():
            os.replace(path, parked)
        try:
            return RestoreSentinelsOnClose(real_connect(locked_project))
        except BaseException:
            for path, parked in parked_paths.items():
                if parked.exists() and not path.exists():
                    os.replace(parked, path)
            raise

    @contextmanager
    def fail_after_real_lock_release(locked_project, timeout_seconds=30.0):
        with real_lock(locked_project, timeout_seconds=timeout_seconds):
            yield
            seam_flags["post_publication_failure"] = (
                database.is_file()
                and manifest.read_bytes() != manifest_before
                and projection.read_bytes() != projection_before
                and any(path.read_bytes() != views_before[path] for path in views)
                and any(path.read_bytes() != logs_before[path] for path in logs)
                and all(path.read_bytes() == mutated_bytes[path] for path in sidecars)
            )
        raise original_lock_release_error

    monkeypatch.setattr(memory_store, "_database_artifact_baselines", observe_database_baselines)
    monkeypatch.setattr(memory_store, "_connect", connect_after_parking_sentinels)
    monkeypatch.setattr(
        freshness_projection,
        "projection_writer_lock",
        fail_after_real_lock_release,
    )

    with pytest.raises(RuntimeError) as caught:
        with memory_store._memory_connection(
            project,
            compensate_committed_failure=False,
        ) as conn:
            memory_store._ensure_schema(conn)
            memory_store._transactional_write_json(conn, manifest, manifest_payload)
            memory_store._transactional_write_bytes(
                conn,
                views[0],
                views_before[views[0]] + b"\nPre-existing sidecar rollback probe.\n",
            )
            memory_ledger._insert_event(
                conn,
                project,
                "edit",
                "p3c0 pre-existing sidecar rollback",
                mirror_log="decision_log.jsonl",
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("p3c0_preexisting_sidecars", "committed"),
            )

    assert caught.value is original_lock_release_error
    assert all(seam_flags.values()), seam_flags
    assert not database.exists()
    assert {
        path: path.read_bytes()
        for path in sidecars
    } == sentinel_bytes
    assert {
        path: memory_store._regular_file_object_identity(path)
        for path in sidecars
    } == sentinel_identities
    assert manifest.read_bytes() == manifest_before
    assert projection.read_bytes() == projection_before
    assert {path: path.read_bytes() for path in views} == views_before
    assert {path: path.read_bytes() for path in logs} == logs_before
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    assert not freshness_projection.writer_lock_path(project).exists()


def test_p3c0_failed_init_cleanup_rejects_replaced_database_artifact(tmp_path):
    project = tmp_path / "replaced-database-artifact"
    database = project / ".controlcoding" / "memory" / "memory.db"
    database.parent.mkdir(parents=True)
    baselines = memory_store._database_artifact_baselines(project)
    database.write_bytes(b"transaction-owned database")
    owned: dict[Path, memory_store._FileObjectIdentity] = {}
    memory_store._capture_owned_database_artifacts(baselines, owned)
    replacement = database.with_name(".memory.db.foreign")
    replacement.write_bytes(b"foreign replacement")
    os.replace(replacement, database)

    errors = memory_store._cleanup_failed_initial_database(baselines, owned)

    assert len(errors) == 1
    assert "not rollback-owned" in str(errors[0])
    assert database.read_bytes() == b"foreign replacement"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_p3c0_first_init_sidecar_restore_rejects_foreign_replacement(
    tmp_path,
    suffix,
):
    project = tmp_path / f"foreign-sidecar-{suffix[1:]}"
    database = memory_store._db_path(project)
    database.parent.mkdir(parents=True)
    sidecar = Path(f"{database}{suffix}")
    sentinel = f"sentinel:{suffix}\n".encode("utf-8")
    foreign = f"foreign:{suffix}\n".encode("utf-8")
    sidecar.write_bytes(sentinel)
    baseline_identity = memory_store._regular_file_object_identity(sidecar)
    baselines = memory_store._database_artifact_baselines(
        project,
        capture_content=False,
    )
    replacement = sidecar.with_name(f".{sidecar.name}.foreign")
    replacement.write_bytes(foreign)
    os.replace(replacement, sidecar)
    foreign_identity = memory_store._regular_file_object_identity(sidecar)

    errors = memory_store._restore_committed_database_artifacts(baselines, {})

    assert baselines[database].existed is False
    assert baselines[sidecar].rollback is not None
    assert baselines[sidecar].rollback.content == sentinel
    assert baselines[sidecar].rollback.identity == baseline_identity
    assert foreign_identity != baseline_identity
    assert len(errors) == 1
    assert "artifact identity changed" in str(errors[0])
    assert sidecar.read_bytes() == foreign
    assert memory_store._regular_file_object_identity(sidecar) == foreign_identity


def test_p3c0_failed_init_cleanup_aggregates_multiple_nonregular_artifacts(tmp_path):
    project = tmp_path / "nonregular-database-artifacts"
    database = project / ".controlcoding" / "memory" / "memory.db"
    wal = Path(f"{database}-wal")
    baselines = memory_store._database_artifact_baselines(project)
    database.mkdir(parents=True)
    wal.mkdir()

    errors = memory_store._cleanup_failed_initial_database(baselines, {})

    assert len(errors) == 2
    assert all("must be regular" in str(error) for error in errors)
    assert database.is_dir()
    assert wal.is_dir()


def test_p3c0_failed_first_init_cleans_database_while_writer_lock_is_held(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "locked-first-init-cleanup"
    real_cleanup = memory_store._cleanup_failed_initial_database
    cleanup_observed = False

    def cleanup_while_locked(baselines, owned):
        nonlocal cleanup_observed
        cleanup_observed = True
        assert freshness_projection.writer_lock_path(project).is_file()
        with pytest.raises(TimeoutError, match="writer lock"):
            with freshness_projection.projection_writer_lock(project, timeout_seconds=0):
                pass
        return real_cleanup(baselines, owned)

    def fail_publication(*_args, **_kwargs):
        raise RuntimeError("simulated locked cleanup publication failure")

    monkeypatch.setattr(memory_store, "_cleanup_failed_initial_database", cleanup_while_locked)
    monkeypatch.setattr(freshness_projection, "publish_freshness_projection", fail_publication)

    with pytest.raises(RuntimeError, match="locked cleanup publication failure"):
        memory_commands.cmd_memory_init(project, project_short="Locked")

    assert cleanup_observed is True
    assert not (project / ".controlcoding").exists()


def test_p3c0_writer_lock_release_rejects_foreign_replacement(
    tmp_path,
    monkeypatch,
):
    lock_path = freshness_projection.writer_lock_path(tmp_path)
    foreign = b"foreign-lock\n"
    real_rename = freshness_projection.os.rename
    injected = False

    def inject_foreign_lock_before_release(source, destination):
        nonlocal injected
        if Path(source) == lock_path and not injected:
            injected = True
            replacement = lock_path.with_name(".foreign-writer-lock")
            replacement.write_bytes(foreign)
            os.replace(replacement, lock_path)
        return real_rename(source, destination)

    monkeypatch.setattr(freshness_projection.os, "rename", inject_foreign_lock_before_release)

    with pytest.raises(RuntimeError, match="writer lock ownership changed"):
        with freshness_projection.projection_writer_lock(tmp_path):
            assert lock_path.is_file()

    assert injected is True
    assert lock_path.read_bytes() == foreign
    assert not list(tmp_path.glob(f".{lock_path.name}.*.release"))


def test_p3c0_writer_lock_preserves_secondary_restore_failure(
    tmp_path,
    monkeypatch,
):
    lock_path = freshness_projection.writer_lock_path(tmp_path)
    real_open = freshness_projection.os.open
    real_rename = freshness_projection.os.rename
    primary_error = OSError(errno.EIO, "simulated writer lock inspection failure")
    secondary_error = OSError(errno.EACCES, "simulated writer lock restore failure")
    primary_seen = False
    secondary_seen = False

    def fail_tombstone_inspection(path, flags, *args, **kwargs):
        nonlocal primary_seen
        if Path(path).name.endswith(".release"):
            primary_seen = True
            raise primary_error
        return real_open(path, flags, *args, **kwargs)

    def fail_tombstone_restore(source, destination, *args, **kwargs):
        nonlocal secondary_seen
        if Path(source).name.endswith(".release") and Path(destination) == lock_path:
            secondary_seen = True
            raise secondary_error
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(freshness_projection.os, "open", fail_tombstone_inspection)
    monkeypatch.setattr(freshness_projection.os, "rename", fail_tombstone_restore)
    try:
        with pytest.raises(OSError) as caught:
            with freshness_projection.projection_writer_lock(tmp_path):
                assert lock_path.is_file()
    finally:
        release_paths = list(tmp_path.glob(f".{lock_path.name}.*.release"))
        for release_path in release_paths:
            release_path.unlink()

    assert primary_seen is True
    assert secondary_seen is True
    assert caught.value is primary_error
    assert any(
        secondary_error.__class__.__name__ in note
        and str(secondary_error) in note
        for note in caught.value.__notes__
    )
    assert not lock_path.exists()
    assert not list(tmp_path.glob(f".{lock_path.name}.*.release"))


def test_p3c0_writer_lock_partial_token_failure_does_not_leave_stale_lock(
    tmp_path,
    monkeypatch,
):
    lock_path = freshness_projection.writer_lock_path(tmp_path)
    real_write = freshness_projection.os.write
    writes = 0

    def partial_then_fail(descriptor, payload):
        nonlocal writes
        writes += 1
        if writes == 1:
            return real_write(descriptor, payload[:1])
        raise OSError("simulated writer lock token failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection.os, "write", partial_then_fail)
        with pytest.raises(OSError, match="token failure"):
            with freshness_projection.projection_writer_lock(tmp_path):
                pass

    assert not lock_path.exists()
    assert not list(tmp_path.glob(f".{lock_path.name}.*.release"))
    with freshness_projection.projection_writer_lock(tmp_path, timeout_seconds=0.5):
        assert lock_path.is_file()
    assert not lock_path.exists()


@pytest.mark.parametrize("failure_site", ["views", "publication"])
def test_p3c0_first_init_rolls_back_database_layout_ledger_and_views_together(
    tmp_path,
    monkeypatch,
    failure_site,
):
    project = tmp_path / f"atomic-init-{failure_site}"

    if failure_site == "views":
        def fail_views(*_args, **_kwargs):
            raise RuntimeError("simulated first init view failure")

        monkeypatch.setattr(memory_views, "_generated_view_contents", fail_views)
        expected = "first init view failure"
    else:
        def fail_publication(*_args, **_kwargs):
            raise RuntimeError("simulated first init publication failure")

        monkeypatch.setattr(freshness_projection, "publish_freshness_projection", fail_publication)
        expected = "first init publication failure"

    with pytest.raises(RuntimeError, match=expected):
        memory_commands.cmd_memory_init(
            project,
            mode="document-only",
            profile="work",
            project_short="Atomic",
        )

    assert not (project / ".controlcoding").exists()
    assert not (project / "docs").exists()
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_directory_compensation_failure_is_not_suppressed(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    owned_directory = tmp_path / ".controlcoding" / "owned-directory"
    foreign_file = owned_directory / "foreign.txt"
    observed_cleanup_errors: list[BaseException] = []
    real_append_exception_notes = memory_store._append_exception_notes

    def observe_cleanup_error(target, error):
        if isinstance(error, OSError):
            observed_cleanup_errors.append(error)
        real_append_exception_notes(target, error)

    monkeypatch.setattr(memory_store, "_append_exception_notes", observe_cleanup_error)

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect") as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_ensure_directory(conn, owned_directory)
            foreign_file.write_text("foreign\n", encoding="utf-8")
            raise RuntimeError("trigger directory compensation")

    assert foreign_file.read_text(encoding="utf-8") == "foreign\n"
    notes = "\n".join(caught.value.__notes__)
    assert len(observed_cleanup_errors) == 1
    assert _p3c0_is_directory_not_empty_error(observed_cleanup_errors[0])
    assert Path(observed_cleanup_errors[0].filename) == owned_directory
    assert "owned-directory" in notes
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_directory_cleanup_reports_every_owned_failure(tmp_path, monkeypatch):
    first = tmp_path / "first-owned"
    second = tmp_path / "second-owned"
    first.mkdir()
    second.mkdir()
    (first / "foreign.txt").write_text("first\n", encoding="utf-8")
    (second / "foreign.txt").write_text("second\n", encoding="utf-8")
    owned = [
        memory_store._OwnedDirectory(first, memory_store._directory_object_identity(first)),
        memory_store._OwnedDirectory(second, memory_store._directory_object_identity(second)),
    ]
    observed_cleanup_errors: list[BaseException] = []
    real_append_exception_notes = memory_store._append_exception_notes

    def observe_cleanup_error(target, error):
        if isinstance(error, OSError):
            observed_cleanup_errors.append(error)
        real_append_exception_notes(target, error)

    monkeypatch.setattr(memory_store, "_append_exception_notes", observe_cleanup_error)

    with pytest.raises(RuntimeError, match="multiple failures") as caught:
        memory_store._remove_owned_empty_directories(owned)

    notes = "\n".join(caught.value.__notes__)
    assert len(observed_cleanup_errors) == 2
    assert all(_p3c0_is_directory_not_empty_error(error) for error in observed_cleanup_errors)
    assert {str(error.filename) for error in observed_cleanup_errors} == {
        str(first),
        str(second),
    }
    assert "first-owned" in notes
    assert "second-owned" in notes


def test_p3c0_directory_cleanup_preserves_original_os_error(tmp_path, monkeypatch):
    owned_path = tmp_path / "permission-owned"
    owned_path.mkdir()
    owned = [
        memory_store._OwnedDirectory(
            owned_path,
            memory_store._directory_object_identity(owned_path),
        )
    ]
    real_rmdir = Path.rmdir

    def deny_owned_directory(path):
        if Path(path) == owned_path:
            raise PermissionError(13, "simulated cleanup permission denied", str(path))
        return real_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", deny_owned_directory)

    with pytest.raises(PermissionError, match="cleanup permission denied"):
        memory_store._remove_owned_empty_directories(owned)


def test_p3c0_output_rollback_preserves_same_byte_foreign_replacement(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["updated_at"] = "2099-01-01T00:00:00Z"
    replacement_identity = None

    def replace_with_same_bytes_then_fail(*_args, **_kwargs):
        nonlocal replacement_identity
        replacement = manifest.with_name(".manifest-foreign-replacement")
        replacement.write_bytes(manifest.read_bytes())
        os.replace(replacement, manifest)
        replacement_identity = memory_store._regular_file_object_identity(manifest)
        raise RuntimeError("simulated same-byte replacement")

    monkeypatch.setattr(
        freshness_projection,
        "build_legacy_status_snapshot",
        replace_with_same_bytes_then_fail,
    )

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_write_json(conn, manifest, payload)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("same_byte_replacement_probe", "rollback"),
            )

    assert replacement_identity is not None
    assert memory_store._regular_file_object_identity(manifest) == replacement_identity
    assert json.loads(manifest.read_text(encoding="utf-8"))["updated_at"] == "2099-01-01T00:00:00Z"


def test_p3c0_atomic_output_ownership_precedes_replace_and_rejects_same_byte_race(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["updated_at"] = "2099-02-01T00:00:00Z"
    expected = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    real_replace = os.replace
    real_link = os.link
    foreign_identity = None
    injected = False

    def replace_then_inject_same_bytes(source, destination, *args, **kwargs):
        nonlocal foreign_identity, injected
        result = real_link(source, destination, *args, **kwargs)
        if Path(destination) == manifest and not injected:
            injected = True
            replacement = manifest.with_name(".manifest-post-replace-foreign")
            replacement.write_bytes(expected)
            real_replace(replacement, manifest)
            foreign_identity = memory_store._regular_file_object_identity(manifest)
        return result

    monkeypatch.setattr(memory_store.os, "link", replace_then_inject_same_bytes)

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_write_json(conn, manifest, payload)

    assert injected is True
    assert foreign_identity is not None
    assert memory_store._regular_file_object_identity(manifest) == foreign_identity
    assert manifest.read_bytes() == expected


def test_p3c0_compensation_reports_output_and_directory_failures_separately(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    owned_directory = tmp_path / ".controlcoding" / "double-compensation"
    output = owned_directory / "owned.json"
    observed_cleanup_errors: list[BaseException] = []
    real_append_exception_notes = memory_store._append_exception_notes

    def observe_cleanup_error(target, error):
        if isinstance(error, OSError):
            observed_cleanup_errors.append(error)
        real_append_exception_notes(target, error)

    monkeypatch.setattr(memory_store, "_append_exception_notes", observe_cleanup_error)

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect") as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_write_bytes(conn, output, b"owned\n")
            replacement = output.with_name(".owned-foreign-replacement")
            replacement.write_bytes(b"owned\n")
            os.replace(replacement, output)
            (owned_directory / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            raise RuntimeError("trigger double compensation")

    notes = "\n".join(caught.value.__notes__)
    assert "output changed outside rollback ownership" in notes
    assert len(observed_cleanup_errors) == 1
    assert _p3c0_is_directory_not_empty_error(observed_cleanup_errors[0])
    assert Path(observed_cleanup_errors[0].filename) == owned_directory
    assert "double-compensation" in notes
    assert output.read_bytes() == b"owned\n"
    assert (owned_directory / "foreign.txt").read_text(encoding="utf-8") == "foreign\n"


def test_p3c0_failed_view_generation_removes_new_view_directory(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    views_dir = tmp_path / ".controlcoding" / "views"
    shutil.rmtree(views_dir)

    def fail_status_snapshot(*_args, **_kwargs):
        raise RuntimeError("simulated absent view directory snapshot failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="absent view directory snapshot failure"):
            memory_views._generate_views(tmp_path, command="cc memory absent view probe", quiet=True)
    assert not views_dir.exists()

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "build_legacy_status_snapshot", fail_status_snapshot)
        with pytest.raises(RuntimeError, match="absent view directory snapshot failure"):
            memory_sessions.cmd_memory_session_views(tmp_path)
    assert not views_dir.exists()
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_transaction_move_rollback_rejects_tampered_destination(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    assert memory_commands.cmd_memory_intake_add(
        tmp_path,
        title="Tampered rollback",
        summary="The replacement must not be moved back.",
        path="docs/inbox/tampered-rollback.md",
        json_output=True,
    ) == 0
    capsys.readouterr()
    source = tmp_path / "docs" / "inbox" / "tampered-rollback.md"
    destination = tmp_path / "docs" / "research" / "tampered-rollback.md"

    def tamper_destination_then_fail(*_args, **_kwargs):
        replacement = destination.with_name(f".{destination.name}.foreign")
        replacement.write_bytes(b"foreign replacement\n")
        os.replace(replacement, destination)
        raise RuntimeError("simulated snapshot failure after destination tamper")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            freshness_projection,
            "build_legacy_status_snapshot",
            tamper_destination_then_fail,
        )
        with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect"):
            memory_commands.cmd_memory_intake_promote(
                tmp_path,
                "docs/inbox/tampered-rollback.md",
                target="research",
                json_output=True,
            )

    assert not source.exists()
    assert destination.read_bytes() == b"foreign replacement\n"
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_jsonl_rollback_preserves_path_replacement_after_descriptor_validation(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    replacement = event_log.with_name(".event-log-foreign-replacement")
    foreign = b'{"foreignReplacement":true}\n'
    real_read = memory_store._read_descriptor_bytes
    replaced = False
    replacement_blocked = False

    def replace_path_after_descriptor_read(descriptor):
        nonlocal replaced, replacement_blocked
        content = real_read(descriptor)
        if not replaced and b"descriptor-bound rollback probe" in content:
            replaced = True
            replacement.write_bytes(foreign)
            try:
                os.replace(replacement, event_log)
            except OSError as exc:
                replacement_blocked = True
                raise RuntimeError("platform blocked open-file replacement") from exc
        return content

    monkeypatch.setattr(memory_store, "_read_descriptor_bytes", replace_path_after_descriptor_read)

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "create",
                "cc memory descriptor-bound rollback probe",
            )
            raise RuntimeError("trigger descriptor-bound rollback")

    assert replaced is True
    if replacement_blocked:
        assert replacement.read_bytes() == foreign
        assert b"descriptor-bound rollback probe" in event_log.read_bytes()
    else:
        assert event_log.read_bytes() == foreign
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_jsonl_rollback_holds_exclusive_descriptor_lock_during_rewrite(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    real_write = memory_store._write_descriptor_bytes
    lock_observed = False

    def assert_lock_then_write(descriptor, content):
        nonlocal lock_observed
        contender = os.open(event_log, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            if os.name == "nt":
                os.lseek(contender, 0, os.SEEK_SET)
                with pytest.raises(OSError):
                    memory_store.msvcrt.locking(
                        contender,
                        memory_store.msvcrt.LK_NBLCK,
                        0x7FFFFFFF,
                    )
            else:
                with pytest.raises(BlockingIOError):
                    memory_store.fcntl.flock(
                        contender,
                        memory_store.fcntl.LOCK_EX | memory_store.fcntl.LOCK_NB,
                    )
            lock_observed = True
        finally:
            os.close(contender)
        real_write(descriptor, content)

    monkeypatch.setattr(memory_store, "_write_descriptor_bytes", assert_lock_then_write)

    with pytest.raises(RuntimeError, match="trigger locked JSONL rollback"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "create",
                "cc memory locked JSONL rollback probe",
            )
            raise RuntimeError("trigger locked JSONL rollback")

    assert lock_observed is True


def test_p3c0_jsonl_baseline_is_captured_from_the_locked_descriptor(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    foreign = b'{"foreignBaseline":true}\n'
    real_open = memory_store._open_identity_bound_regular
    injected = False

    def replace_before_open(path, flags, mode=0o666):
        nonlocal injected
        if Path(path) == event_log and not injected:
            injected = True
            replacement = event_log.with_name(".event-log-pre-open-foreign")
            replacement.write_bytes(foreign)
            os.replace(replacement, event_log)
        return real_open(path, flags, mode)

    monkeypatch.setattr(memory_store, "_open_identity_bound_regular", replace_before_open)

    with pytest.raises(RuntimeError, match="trigger descriptor baseline rollback"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "create",
                "cc memory descriptor baseline probe",
            )
            raise RuntimeError("trigger descriptor baseline rollback")

    assert injected is True
    assert event_log.read_bytes() == foreign


def test_p3c0_missing_jsonl_uses_transactional_no_clobber_creation(
    tmp_path,
    capsys,
):
    _p3c0_init_project(tmp_path, capsys)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    event_log.unlink()

    with pytest.raises(RuntimeError, match="trigger missing log rollback"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "create",
                "cc memory missing log probe",
            )
            raise RuntimeError("trigger missing log rollback")

    assert not event_log.exists()


def test_p3c0_missing_jsonl_rejects_file_created_after_enoent(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    event_log.unlink()
    foreign = b'{"foreign":true}\n'
    real_link = memory_store.os.link
    hard_link_reached = False
    temporary_path = None

    def inject_at_final_hard_link(source, destination, *args, **kwargs):
        nonlocal hard_link_reached, temporary_path
        if Path(destination) == event_log:
            hard_link_reached = True
            temporary_path = Path(source)
            assert temporary_path.parent == event_log.parent
            assert temporary_path.name.startswith(f".{event_log.name}.")
            event_log.write_bytes(foreign)
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(memory_store.os, "link", inject_at_final_hard_link)

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect") as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_ledger._insert_event(
                conn,
                tmp_path,
                "create",
                "cc memory missing log race probe",
            )

    assert hard_link_reached is True
    assert isinstance(caught.value.__cause__, FileExistsError)
    assert any(
        "changed outside rollback ownership" in note and str(event_log) in note
        for note in caught.value.__notes__
    )
    assert temporary_path is not None
    assert not temporary_path.exists()
    assert event_log.read_bytes() == foreign


def test_p3c0_concurrent_intake_records_preserve_one_file_and_one_entity(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    context = multiprocessing.get_context("spawn")
    boundary = context.Barrier(3)
    result_queue = context.Queue()
    children = [
        context.Process(
            target=_p3c0_intake_record_worker,
            args=(str(tmp_path), worker_id, boundary, result_queue),
        )
        for worker_id in (1, 2)
    ]
    for child in children:
        child.start()
    boundary.wait(timeout=15)
    results = [result_queue.get(timeout=20) for _child in children]
    for child in children:
        child.join(timeout=20)
        assert child.exitcode == 0

    assert sum(1 for item in results if item["ok"]) == 1
    assert sum(1 for item in results if item.get("conflict")) == 1
    destination = tmp_path / "docs" / "inbox" / "concurrent-intake.md"
    assert destination.read_bytes() == b"# Concurrent intake\n"
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE path = ?",
            ("docs/inbox/concurrent-intake.md",),
        ).fetchone()[0]
    assert count == 1


def test_p3c0_public_intake_add_rejects_linked_destination_parent_before_descendant_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "public-add-project"
    external = tmp_path / "public-add-external"
    _p3c0_init_project(project, capsys)
    linked_parent = project / "docs" / "linked-inbox"
    linked_parent.parent.mkdir(parents=True, exist_ok=True)
    external.mkdir()
    external_target = external / "public-add.md"
    external_target.write_bytes(b"external intake candidate\n")
    link_kind = _p3c0_create_directory_link(linked_parent, external)
    external_before = _p3c0_directory_snapshot(external)
    memory_before = _p3c0_memory_bytes_snapshot(project)
    validation_reached = False
    real_physical_chain = memory_store._physical_directory_chain

    def observe_validation(path, trust_anchor):
        nonlocal validation_reached
        if Path(path) == linked_parent:
            validation_reached = True
        return real_physical_chain(path, trust_anchor)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(memory_store, "_physical_directory_chain", observe_validation)
        boundaries = _p3c0_watch_public_intake_descendants(patch_context, linked_parent)
        result = _run_main([
            "memory",
            "intake",
            "add",
            "Public boundary add",
            "--project-root",
            str(project),
            "--summary",
            "must reject a linked destination parent",
            "--path",
            "docs/linked-inbox/public-add.md",
            "--json",
        ])
        observed_boundaries = dict(boundaries)

    payload = json.loads(capsys.readouterr().out)
    assert link_kind in {"symlink", "junction"}
    assert validation_reached is True
    assert result == 1
    assert payload["error"] == "intake_path_outside_project"
    assert observed_boundaries == {key: False for key in observed_boundaries}
    assert _p3c0_directory_snapshot(external) == external_before
    assert external_target.read_bytes() == b"external intake candidate\n"
    assert _p3c0_memory_bytes_snapshot(project) == memory_before


def test_p3c0_public_intake_add_validates_default_parent_before_unique_candidate_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "public-add-default-project"
    external = tmp_path / "public-add-default-external"
    _p3c0_init_project(project, capsys)
    linked_parent = project / "docs" / "inbox"
    assert not linked_parent.exists()
    linked_parent.parent.mkdir(parents=True, exist_ok=True)
    external.mkdir()
    title = "Public default boundary"
    candidate = external / (
        f"{memory_commands._today_yyyymmdd()}-"
        f"{memory_commands._slug_for_filename(title)}.md"
    )
    candidate.write_bytes(b"external default intake candidate\n")
    link_kind = _p3c0_create_directory_link(linked_parent, external)
    external_before = _p3c0_directory_snapshot(external)
    memory_before = _p3c0_memory_bytes_snapshot(project)
    validation_reached = False
    real_physical_chain = memory_store._physical_directory_chain

    def observe_validation(path, trust_anchor):
        nonlocal validation_reached
        if Path(path) == linked_parent:
            validation_reached = True
        return real_physical_chain(path, trust_anchor)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(memory_store, "_physical_directory_chain", observe_validation)
        boundaries = _p3c0_watch_public_intake_descendants(patch_context, linked_parent)
        result = _run_main([
            "memory",
            "intake",
            "add",
            title,
            "--project-root",
            str(project),
            "--summary",
            "must validate before probing unique candidates",
            "--json",
        ])
        observed_boundaries = dict(boundaries)

    payload = json.loads(capsys.readouterr().out)
    assert link_kind in {"symlink", "junction"}
    assert validation_reached is True
    assert result == 1
    assert payload["error"] == "intake_path_outside_project"
    assert observed_boundaries == {key: False for key in observed_boundaries}
    assert _p3c0_directory_snapshot(external) == external_before
    assert candidate.read_bytes() == b"external default intake candidate\n"
    assert _p3c0_memory_bytes_snapshot(project) == memory_before


def test_p3c0_public_intake_promote_rejects_linked_source_parent_before_descendant_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "public-promote-source-project"
    external = tmp_path / "public-promote-source-external"
    _p3c0_init_project(project, capsys)
    source_rel = "docs/inbox/public-source.md"
    assert memory_commands.cmd_memory_intake_add(
        project,
        title="Public linked source",
        summary="must reject a linked source parent",
        path=source_rel,
        json_output=True,
    ) == 0
    capsys.readouterr()
    source = project / Path(*PurePosixPath(source_rel).parts)
    source_parent = source.parent
    source_parent.replace(external)
    link_kind = _p3c0_create_directory_link(source_parent, external)
    source_before = (external / source.name).read_bytes()
    external_before = _p3c0_directory_snapshot(external)
    memory_before = _p3c0_memory_bytes_snapshot(project)
    validation_reached = False
    real_physical_chain = memory_store._physical_directory_chain

    def observe_validation(path, trust_anchor):
        nonlocal validation_reached
        if Path(path) == source_parent:
            validation_reached = True
        return real_physical_chain(path, trust_anchor)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(memory_store, "_physical_directory_chain", observe_validation)
        boundaries = _p3c0_watch_public_intake_descendants(patch_context, source_parent)
        result = _run_main([
            "memory",
            "intake",
            "promote",
            source_rel,
            "--project-root",
            str(project),
            "--to",
            "research",
            "--json",
        ])
        observed_boundaries = dict(boundaries)

    payload = json.loads(capsys.readouterr().out)
    assert link_kind in {"symlink", "junction"}
    assert validation_reached is True
    assert result == 1
    assert payload["error"] == "intake_source_outside_project"
    assert observed_boundaries == {key: False for key in observed_boundaries}
    assert (external / source.name).read_bytes() == source_before
    assert not (project / "docs" / "research" / source.name).exists()
    assert _p3c0_directory_snapshot(external) == external_before
    assert _p3c0_memory_bytes_snapshot(project) == memory_before


def test_p3c0_public_intake_promote_rejects_linked_destination_parent_before_descendant_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "public-promote-destination-project"
    external = tmp_path / "public-promote-destination-external"
    _p3c0_init_project(project, capsys)
    source_rel = "docs/inbox/public-destination.md"
    assert memory_commands.cmd_memory_intake_add(
        project,
        title="Public linked destination",
        summary="must reject a linked destination parent",
        path=source_rel,
        json_output=True,
    ) == 0
    capsys.readouterr()
    source = project / Path(*PurePosixPath(source_rel).parts)
    source_before = source.read_bytes()
    destination_parent = project / "docs" / "research"
    assert not destination_parent.exists()
    external.mkdir()
    (external / source.name).write_bytes(b"external destination candidate\n")
    link_kind = _p3c0_create_directory_link(destination_parent, external)
    external_before = _p3c0_directory_snapshot(external)
    memory_before = _p3c0_memory_bytes_snapshot(project)
    validated_parents: list[Path] = []
    real_physical_chain = memory_store._physical_directory_chain

    def observe_validation(path, trust_anchor):
        if Path(path) in {source.parent, destination_parent}:
            validated_parents.append(Path(path))
        return real_physical_chain(path, trust_anchor)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(memory_store, "_physical_directory_chain", observe_validation)
        boundaries = _p3c0_watch_public_intake_descendants(
            patch_context,
            source.parent,
            destination_parent,
        )
        result = _run_main([
            "memory",
            "intake",
            "promote",
            source_rel,
            "--project-root",
            str(project),
            "--to",
            "research",
            "--json",
        ])
        observed_boundaries = dict(boundaries)

    payload = json.loads(capsys.readouterr().out)
    assert link_kind in {"symlink", "junction"}
    assert validated_parents == [source.parent, destination_parent]
    assert result == 1
    assert payload["error"] == "intake_promoted_path_outside_project"
    assert observed_boundaries == {key: False for key in observed_boundaries}
    assert source.read_bytes() == source_before
    assert _p3c0_directory_snapshot(external) == external_before
    assert _p3c0_memory_bytes_snapshot(project) == memory_before


def test_p3c0_public_intake_promote_uses_one_normalized_source_path_for_validation_and_access(
    tmp_path,
    capsys,
    monkeypatch,
):
    project = tmp_path / "public-promote-normalized-project"
    external = tmp_path / "public-promote-normalized-external"
    _p3c0_init_project(project, capsys)
    normalized_parent = project / "docs" / "target"
    normalized_parent.mkdir(parents=True)
    normalized_source = normalized_parent / "normalized-public-source.md"
    normalized_content = b"normalized public source\n"
    normalized_source.write_bytes(normalized_content)
    (external / "child").mkdir(parents=True)
    (external / "sentinel.bin").write_bytes(b"external sentinel\n")
    linked_parent = project / "docs" / "linked-source"
    link_kind = _p3c0_create_directory_link(linked_parent, external / "child")
    raw_source_rel = "docs/linked-source/../target/normalized-public-source.md"
    raw_source = project / Path(*PurePosixPath(raw_source_rel).parts)
    entity = memory_entities._record_entity(
        project,
        "note",
        "Normalized public source",
        body="public lexical normalization probe",
        area="Inbox",
        path=raw_source_rel,
        command="p3c0 normalized public source setup",
    )
    capsys.readouterr()
    external_before = _p3c0_directory_snapshot(external)
    destination_parent = project / "docs" / "research"
    validated_parents: list[Path] = []
    real_physical_chain = memory_store._physical_directory_chain

    def observe_validation(path, trust_anchor):
        if Path(path) in {normalized_parent, destination_parent}:
            validated_parents.append(Path(path))
        return real_physical_chain(path, trust_anchor)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(memory_store, "_physical_directory_chain", observe_validation)
        boundaries = _p3c0_watch_public_intake_descendants(patch_context, linked_parent)
        result = _run_main([
            "memory",
            "intake",
            "promote",
            str(entity["id"]),
            "--project-root",
            str(project),
            "--to",
            "research",
            "--json",
        ])
        observed_boundaries = dict(boundaries)

    payload = json.loads(capsys.readouterr().out)
    destination = destination_parent / normalized_source.name
    assert link_kind in {"symlink", "junction"}
    assert validated_parents[:2] == [normalized_parent, destination_parent]
    assert result == 0
    assert payload["fromPath"] == raw_source_rel
    assert payload["path"] == "docs/research/normalized-public-source.md"
    assert observed_boundaries == {key: False for key in observed_boundaries}
    assert raw_source != normalized_source
    assert not normalized_source.exists()
    assert destination.read_bytes() == normalized_content
    assert _p3c0_directory_snapshot(external) == external_before
    with memory_store._readonly_memory_connection(project) as conn:
        stored_path = conn.execute(
            "SELECT path FROM entities WHERE id = ?",
            (entity["id"],),
        ).fetchone()[0]
    assert stored_path == "docs/research/normalized-public-source.md"


def test_p3c0_intake_promote_rejects_same_physical_parent_before_link(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source_relative = "docs/research/same-parent.md"
    assert memory_commands.cmd_memory_intake_add(
        tmp_path,
        title="Same parent",
        summary="same-parent promotion guard",
        path=source_relative,
    ) == 0
    capsys.readouterr()
    source = tmp_path / Path(*source_relative.split("/"))
    destination = memory_commands._unique_project_file(source)
    source_before = source.read_bytes()
    event_log = tmp_path / ".controlcoding" / "logs" / "event_log.jsonl"
    projection = freshness_projection.projection_path(tmp_path)
    views_dir = tmp_path / ".controlcoding" / "views"
    database_artifacts = memory_store._initial_database_artifacts(tmp_path)

    def persistent_database_snapshot(path):
        snapshot = _p3c0_file_snapshot(path)
        snapshot.pop("identity", None)
        return snapshot

    database_before = {path: persistent_database_snapshot(path) for path in database_artifacts}
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        entity_before = tuple(
            conn.execute(
                "SELECT path, lifecycle, content_hash, data FROM entities WHERE path = ?",
                (source_relative,),
            ).fetchone()
        )
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    event_log_before = event_log.read_bytes()
    projection_before = projection.read_bytes()
    views_before = {path.name: path.read_bytes() for path in views_dir.glob("*.md")}
    real_link = memory_store.os.link
    filesystem_link_reached = False

    def observe_move_link(link_source, link_destination, *args, **kwargs):
        nonlocal filesystem_link_reached
        if Path(link_source) == source and Path(link_destination) == destination:
            filesystem_link_reached = True
        return real_link(link_source, link_destination, *args, **kwargs)

    monkeypatch.setattr(memory_store.os, "link", observe_move_link)
    failure = None
    try:
        memory_commands.cmd_memory_intake_promote(
            tmp_path,
            selector=source_relative,
            target="research",
        )
    except BaseException as exc:
        failure = exc

    assert filesystem_link_reached is False
    assert isinstance(failure, RuntimeError)
    assert "same physical parent" in str(failure)
    assert source.read_bytes() == source_before
    assert not destination.exists()
    assert {path: persistent_database_snapshot(path) for path in database_artifacts} == database_before
    assert event_log.read_bytes() == event_log_before
    assert projection.read_bytes() == projection_before
    assert {path.name: path.read_bytes() for path in views_dir.glob("*.md")} == views_before
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert tuple(
            conn.execute(
                "SELECT path, lifecycle, content_hash, data FROM entities WHERE path = ?",
                (source_relative,),
            ).fetchone()
        ) == entity_before
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before


def test_p3c0_transaction_move_rejects_dangling_destination_without_overwrite(
    tmp_path,
    capsys,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "move-source.md"
    destination = tmp_path / "docs" / "research" / "move-destination.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n", encoding="utf-8")
    try:
        destination.symlink_to(destination.with_name("missing-target.md"))
    except OSError as exc:
        _p3c0_skip_if_windows_symlink_privilege_error(
            exc,
            "dangling destination symlink creation is unavailable",
        )

    with pytest.raises(RuntimeError, match="destination already exists"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)

    assert source.read_text(encoding="utf-8") == "source\n"
    assert destination.is_symlink()
    assert os.readlink(destination).endswith("missing-target.md")


def test_p3c0_intake_promote_classifies_dangling_source_as_non_file(
    tmp_path,
    capsys,
):
    _p3c0_init_project(tmp_path, capsys)
    assert memory_commands.cmd_memory_intake_add(
        tmp_path,
        title="Dangling source",
        summary="A dangling source must never be treated as absent.",
        path="docs/inbox/dangling-source.md",
        json_output=True,
    ) == 0
    capsys.readouterr()
    source = tmp_path / "docs" / "inbox" / "dangling-source.md"
    source.unlink()
    try:
        source.symlink_to(source.with_name("missing-target.md"))
    except OSError as exc:
        _p3c0_skip_if_windows_symlink_privilege_error(
            exc,
            "dangling source symlink creation is unavailable",
        )

    assert memory_commands.cmd_memory_intake_promote(
        tmp_path,
        "docs/inbox/dangling-source.md",
        target="research",
        json_output=True,
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "intake_source_not_file"
    assert source.is_symlink()


def test_p3c0_intake_promote_source_validation_does_not_use_exists() -> None:
    source = Path(memory_commands.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "cmd_memory_intake_promote"
    )
    source_abs_calls = {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "source_abs"
    }

    assert "lstat" in source_abs_calls
    assert "exists" not in source_abs_calls


def test_p3c0_transaction_move_no_clobber_preserves_racing_destination(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "racing-source.md"
    destination = tmp_path / "docs" / "research" / "racing-destination.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n", encoding="utf-8")
    real_link = memory_store.os.link
    injected = False

    def inject_destination_before_link(link_source, link_destination, **kwargs):
        nonlocal injected
        if Path(link_destination) == destination and not injected:
            injected = True
            destination.write_text("foreign\n", encoding="utf-8")
        return real_link(link_source, link_destination, **kwargs)

    monkeypatch.setattr(memory_store.os, "link", inject_destination_before_link)

    with pytest.raises(FileExistsError):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)

    assert injected is True
    assert source.read_text(encoding="utf-8") == "source\n"
    assert destination.read_text(encoding="utf-8") == "foreign\n"


def test_p3c0_transaction_move_preserves_racing_same_inode_hardlink(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "same-inode-source.md"
    destination = tmp_path / "docs" / "research" / "same-inode-destination.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n", encoding="utf-8")
    real_link = memory_store.os.link
    injected = False

    def inject_same_inode_before_link(link_source, link_destination, **kwargs):
        nonlocal injected
        if Path(link_destination) == destination and not injected:
            injected = True
            real_link(link_source, link_destination, **kwargs)
        return real_link(link_source, link_destination, **kwargs)

    monkeypatch.setattr(memory_store.os, "link", inject_same_inode_before_link)

    with pytest.raises(FileExistsError):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)

    assert injected is True
    assert memory_store._file_identity_state(source) == memory_store._file_identity_state(destination)


def test_p3c0_transaction_move_rollback_rejects_observable_same_inode_destination_reincarnation(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "reincarnated-source.md"
    destination = tmp_path / "docs" / "research" / "reincarnated-destination.md"
    anchor = tmp_path / "docs" / "inbox" / ".reincarnated-anchor"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n", encoding="utf-8")
    os.link(source, anchor)
    real_directory_generation = memory_store._directory_generation
    real_os_link = memory_store.os.link
    rollback_phase = False
    post_relink_validation_seen = False
    reincarnated = False
    generation_g0 = None
    generation_g1 = None
    controlled_generations = []

    def controlled_directory_generation(path):
        nonlocal post_relink_validation_seen, reincarnated
        if not rollback_phase or Path(path) != destination.parent:
            return real_directory_generation(path)
        assert generation_g0 is not None
        assert generation_g1 is not None
        if not source.exists():
            controlled_generations.append(generation_g0)
            return generation_g0
        if not post_relink_validation_seen:
            post_relink_validation_seen = True
            controlled_generations.append(generation_g0)
            return generation_g0
        if not reincarnated:
            destination.unlink()
            real_os_link(anchor, destination)
            reincarnated = True
        controlled_generations.append(generation_g1)
        return generation_g1

    monkeypatch.setattr(memory_store, "_directory_generation", controlled_directory_generation)
    original_os_error = OSError("trigger observable same-inode destination reincarnation rollback")

    with pytest.raises(RuntimeError) as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)
            generation_g0 = real_directory_generation(destination.parent)
            generation_g1 = memory_store._DirectoryGeneration(
                identity=generation_g0.identity,
                modified_time_ns=generation_g0.modified_time_ns + 1,
                change_time_ns=generation_g0.change_time_ns + 1,
            )
            assert generation_g0 != generation_g1
            rollback_phase = True
            raise original_os_error

    assert str(caught.value) == "memory transaction rollback did not restore every owned side effect"
    assert caught.value.__cause__ is original_os_error
    assert post_relink_validation_seen is True
    assert reincarnated is True
    assert controlled_generations[-1] == generation_g1
    assert generation_g0 in controlled_generations
    notes = "\n".join(caught.value.__notes__)
    assert "parent generation changed outside rollback ownership" in notes
    assert str(destination.parent) in notes
    assert source.exists()
    assert destination.exists()
    assert anchor.exists()
    destination_details = destination.stat()
    anchor_details = anchor.stat()
    assert (destination_details.st_dev, destination_details.st_ino) == (
        anchor_details.st_dev,
        anchor_details.st_ino,
    )


def test_p3c0_transaction_move_rollback_rejects_reincarnated_destination_before_relink(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "prelink-source.md"
    destination = tmp_path / "docs" / "research" / "prelink-destination.md"
    anchor = tmp_path / "docs" / "inbox" / ".prelink-anchor"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n", encoding="utf-8")
    os.link(source, anchor)
    real_os_link = memory_store.os.link
    real_directory_generation = memory_store._directory_generation
    generation_g0 = real_directory_generation(destination.parent)
    generation_g1 = memory_store._DirectoryGeneration(
        identity=generation_g0.identity,
        modified_time_ns=generation_g0.modified_time_ns + 1,
        change_time_ns=generation_g0.change_time_ns + 1,
    )
    reincarnated = False

    def controlled_directory_generation(path):
        if Path(path) == destination.parent:
            return generation_g1 if reincarnated else generation_g0
        return real_directory_generation(path)

    def reincarnate_destination_before_reverse_link(link_source, link_destination, **kwargs):
        nonlocal reincarnated
        if Path(link_source) == destination and Path(link_destination) == source:
            destination.unlink()
            real_os_link(anchor, destination)
            reincarnated = True
            return real_os_link(destination, source, **kwargs)
        return real_os_link(link_source, link_destination, **kwargs)

    monkeypatch.setattr(memory_store, "_directory_generation", controlled_directory_generation)
    monkeypatch.setattr(memory_store.os, "link", reincarnate_destination_before_reverse_link)
    original_os_error = OSError("trigger prelink destination reincarnation rollback")

    with pytest.raises(RuntimeError) as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)
            raise original_os_error

    assert reincarnated is True
    assert str(caught.value) == "memory transaction rollback did not restore every owned side effect"
    assert caught.value.__cause__ is original_os_error
    assert any(
        str(destination) in note and ("ownership" in note or "generation" in note)
        for note in caught.value.__notes__
    )
    assert destination.exists()
    assert anchor.exists()
    destination_details = destination.stat()
    anchor_details = anchor.stat()
    assert (destination_details.st_dev, destination_details.st_ino) == (
        anchor_details.st_dev,
        anchor_details.st_ino,
    )


def test_p3c0_transaction_move_detects_destination_parent_swap_after_link(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "parent-swap-source.md"
    destination = tmp_path / "docs" / "research" / "parent-swap-destination.md"
    moved_parent = tmp_path / "docs" / "research-before-swap"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n", encoding="utf-8")
    real_link = memory_store.os.link
    injected = False

    def swap_parent_after_link(link_source, link_destination, **kwargs):
        nonlocal injected
        result = real_link(link_source, link_destination, **kwargs)
        if Path(link_destination) == destination and not injected:
            injected = True
            os.replace(destination.parent, moved_parent)
            destination.parent.mkdir()
            destination.write_text("foreign\n", encoding="utf-8")
        return result

    monkeypatch.setattr(memory_store.os, "link", swap_parent_after_link)

    with pytest.raises(RuntimeError, match="rollback did not restore every owned side effect"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)

    assert injected is True
    assert source.read_text(encoding="utf-8") == "source\n"
    assert destination.read_text(encoding="utf-8") == "foreign\n"
    assert (moved_parent / destination.name).read_text(encoding="utf-8") == "source\n"


def _p3c0_primary_exception(primary_type, message: str) -> BaseException:
    if primary_type is OSError:
        return OSError(errno.EIO, message)
    return primary_type(message)


def _p3c0_assert_primary_exception_preserved(
    propagated: BaseException,
    primary: BaseException,
    cause: BaseException,
    first_note: str,
    injected_traceback,
) -> None:
    assert propagated is primary
    assert propagated.__cause__ is cause
    assert propagated.__notes__[0] == first_note
    assert injected_traceback is not None
    traceback_cursor = propagated.__traceback__
    while traceback_cursor is not None and traceback_cursor is not injected_traceback:
        traceback_cursor = traceback_cursor.tb_next
    assert traceback_cursor is injected_traceback


class _P3C0FailingCloseHandle:
    def __init__(
        self,
        descriptor: int,
        close_error: BaseException,
        close_attempts: list[int],
        tracebacks: dict[str, object],
        *,
        operation_error: BaseException | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.close_error = close_error
        self.close_attempts = close_attempts
        self.tracebacks = tracebacks
        self.operation_error = operation_error

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def write(self, content: bytes) -> int:
        if self.operation_error is not None:
            try:
                raise self.operation_error
            except BaseException as active_error:
                self.tracebacks["operation"] = active_error.__traceback__
                raise
        return os.write(self.descriptor, content)

    def flush(self) -> None:
        return None

    def fileno(self) -> int:
        return self.descriptor

    def close(self) -> None:
        self.close_attempts.append(self.descriptor)
        try:
            raise self.close_error
        except BaseException as active_error:
            self.tracebacks["close"] = active_error.__traceback__
            raise


def test_p3c0_atomic_replace_closes_descriptor_when_fdopen_fails(tmp_path, monkeypatch):
    target = tmp_path / "atomic" / "target.json"
    captured_descriptors: list[int] = []
    owned_descriptors: list[int] = []
    production_close_attempts: list[int] = []
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat

    def observe_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        owned_descriptors.append(descriptor)
        return descriptor

    def fail_fdopen(descriptor, *_args, **_kwargs):
        captured_descriptors.append(descriptor)
        raise OSError("simulated fdopen failure")

    def observe_close(descriptor):
        production_close_attempts.append(descriptor)
        return real_close(descriptor)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", observe_open)
            patch_context.setattr(memory_store.os, "fdopen", fail_fdopen)
            patch_context.setattr(memory_store.os, "close", observe_close)
            with pytest.raises(OSError, match="fdopen failure"):
                memory_store._atomic_replace_bytes(target, b"{}\n")

        assert len(captured_descriptors) == 1
        assert production_close_attempts == captured_descriptors
        _p3c0_assert_descriptor_closed(captured_descriptors[0], real_fstat)
        assert not target.exists()
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_atomic_replace_preserves_fdopen_failure_when_raw_close_fails(
    tmp_path,
    monkeypatch,
    primary_type,
):
    target = tmp_path / "atomic-fdopen-dual" / "target.json"
    backing = tmp_path / "atomic-fdopen-dual-backing.bin"
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    descriptor: int | None = None
    owned_descriptors: list[int] = []
    cause = ValueError("pre-existing fdopen failure cause")
    primary = _p3c0_primary_exception(primary_type, "simulated primary fdopen failure")
    primary.__cause__ = cause
    primary.add_note("pre-existing fdopen failure note")
    close_error = OSError(errno.EIO, "simulated secondary raw close failure")
    close_error.add_note("secondary raw close nested note")
    fdopen_calls: list[int] = []
    raw_close_attempts: list[int] = []
    injected_traceback = None

    def return_backing_descriptor(*_args, **_kwargs):
        return descriptor

    def fail_fdopen(opened_descriptor, *_args, **_kwargs):
        nonlocal injected_traceback
        fdopen_calls.append(opened_descriptor)
        try:
            raise primary
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    def fail_raw_close(opened_descriptor):
        raw_close_attempts.append(opened_descriptor)
        raise close_error

    harness_error: BaseException | None = None
    try:
        descriptor = real_open(
            backing,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o666,
        )
        owned_descriptors.append(descriptor)
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", return_backing_descriptor)
            patch_context.setattr(memory_store.os, "fdopen", fail_fdopen)
            patch_context.setattr(memory_store.os, "close", fail_raw_close)
            with pytest.raises(primary_type) as caught:
                memory_store._atomic_replace_bytes(target, b"{}\n")

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            primary,
            cause,
            "pre-existing fdopen failure note",
            injected_traceback,
        )
        assert fdopen_calls == [descriptor]
        assert raw_close_attempts == [descriptor]
        assert any(
            close_error.__class__.__name__ in note and str(close_error) in note
            for note in caught.value.__notes__
        )
        assert "nested: secondary raw close nested note" in caught.value.__notes__
        assert stat.S_ISREG(real_fstat(descriptor).st_mode)
        assert not target.exists()
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_atomic_replace_file_object_close_failure_is_primary_without_raw_fallback(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "atomic-file-close" / "target.json"
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    cause = ValueError("pre-existing file close cause")
    close_error = OSError(errno.EIO, "simulated file-object close failure")
    close_error.__cause__ = cause
    close_error.add_note("pre-existing file close note")
    file_close_attempts: list[int] = []
    raw_close_attempts: list[int] = []
    tracebacks: dict[str, object] = {}
    captured_descriptors: list[int] = []
    owned_descriptors: list[int] = []
    owned_temporary_paths = []

    def observe_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        owned_descriptors.append(descriptor)
        candidate = Path(path)
        if flags & os.O_EXCL and candidate.name.startswith(f".{target.name}."):
            details = real_fstat(descriptor)
            owned_temporary_paths.append(
                (candidate, (details.st_dev, details.st_ino))
            )
        return descriptor

    def return_handle(opened_descriptor, *_args, **_kwargs):
        captured_descriptors.append(opened_descriptor)
        return _P3C0FailingCloseHandle(
            opened_descriptor,
            close_error,
            file_close_attempts,
            tracebacks,
        )

    def fail_unexpected_raw_close(opened_descriptor):
        raw_close_attempts.append(opened_descriptor)
        raise AssertionError("raw fallback attempted after fdopen ownership transfer")

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", observe_open)
            patch_context.setattr(memory_store.os, "fdopen", return_handle)
            patch_context.setattr(memory_store.os, "close", fail_unexpected_raw_close)
            with pytest.raises(OSError) as caught:
                memory_store._atomic_replace_bytes(target, b"{}\n")

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            close_error,
            cause,
            "pre-existing file close note",
            tracebacks.get("close"),
        )
        assert len(captured_descriptors) == 1
        assert file_close_attempts == captured_descriptors
        assert raw_close_attempts == []
        assert stat.S_ISREG(real_fstat(captured_descriptors[0]).st_mode)
        assert not target.exists()
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        descriptor_cleanup_error: BaseException | None = None
        try:
            _p3c0_cleanup_owned_descriptors(
                owned_descriptors,
                real_fstat,
                real_close,
                primary_error=harness_error,
            )
        except BaseException as exc:
            descriptor_cleanup_error = exc
        _p3c0_cleanup_owned_paths(
            owned_temporary_paths,
            primary_error=harness_error or descriptor_cleanup_error,
        )
        if descriptor_cleanup_error is not None:
            raise descriptor_cleanup_error


@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_atomic_replace_preserves_operation_failure_when_file_object_close_fails(
    tmp_path,
    monkeypatch,
    primary_type,
):
    target = tmp_path / "atomic-operation-dual" / "target.json"
    backing = tmp_path / "atomic-operation-dual-backing.bin"
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    descriptor: int | None = None
    owned_descriptors: list[int] = []
    cause = ValueError("pre-existing atomic operation cause")
    primary = _p3c0_primary_exception(primary_type, "simulated atomic operation failure")
    primary.__cause__ = cause
    primary.add_note("pre-existing atomic operation note")
    close_error = OSError(errno.EIO, "simulated secondary file-object close failure")
    close_error.add_note("secondary file-object close nested note")
    file_close_attempts: list[int] = []
    raw_close_attempts: list[int] = []
    tracebacks: dict[str, object] = {}
    handle = None

    def return_backing_descriptor(*_args, **_kwargs):
        return descriptor

    def return_handle(opened_descriptor, *_args, **_kwargs):
        assert opened_descriptor == descriptor
        return handle

    def fail_unexpected_raw_close(opened_descriptor):
        raw_close_attempts.append(opened_descriptor)
        raise AssertionError("raw fallback attempted after fdopen ownership transfer")

    harness_error: BaseException | None = None
    try:
        descriptor = real_open(
            backing,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o666,
        )
        owned_descriptors.append(descriptor)
        handle = _P3C0FailingCloseHandle(
            descriptor,
            close_error,
            file_close_attempts,
            tracebacks,
            operation_error=primary,
        )
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", return_backing_descriptor)
            patch_context.setattr(memory_store.os, "fdopen", return_handle)
            patch_context.setattr(memory_store.os, "close", fail_unexpected_raw_close)
            with pytest.raises(primary_type) as caught:
                memory_store._atomic_replace_bytes(target, b"{}\n")

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            primary,
            cause,
            "pre-existing atomic operation note",
            tracebacks.get("operation"),
        )
        assert file_close_attempts == [descriptor]
        assert raw_close_attempts == []
        assert any(
            close_error.__class__.__name__ in note and str(close_error) in note
            for note in caught.value.__notes__
        )
        assert "nested: secondary file-object close nested note" in caught.value.__notes__
        assert stat.S_ISREG(real_fstat(descriptor).st_mode)
        assert not target.exists()
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_open_identity_bound_regular_closes_once_when_validation_fails(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "identity-validation.bin"
    target.write_bytes(b"identity\n")
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    descriptor: int | None = None
    owned_descriptors: list[int] = []
    cause = ValueError("pre-existing identity validation cause")
    primary = RuntimeError("simulated identity validation failure")
    primary.__cause__ = cause
    primary.add_note("pre-existing identity validation note")
    close_attempts: list[int] = []
    injected_traceback = None

    def return_backing_descriptor(*_args, **_kwargs):
        return descriptor

    def fail_identity(*_args, **_kwargs):
        nonlocal injected_traceback
        try:
            raise primary
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    def observe_close(opened_descriptor):
        close_attempts.append(opened_descriptor)
        return real_close(opened_descriptor)

    harness_error: BaseException | None = None
    try:
        descriptor = real_open(
            target,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        owned_descriptors.append(descriptor)
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", return_backing_descriptor)
            patch_context.setattr(memory_store, "_descriptor_identity", fail_identity)
            patch_context.setattr(memory_store.os, "close", observe_close)
            with pytest.raises(RuntimeError) as caught:
                memory_store._open_identity_bound_regular(target, os.O_RDONLY)

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            primary,
            cause,
            "pre-existing identity validation note",
            injected_traceback,
        )
        assert close_attempts == [descriptor]
        _p3c0_assert_descriptor_closed(descriptor, real_fstat)
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_open_identity_bound_regular_preserves_validation_failure_when_close_fails(
    tmp_path,
    monkeypatch,
    primary_type,
):
    target = tmp_path / "identity-validation-dual.bin"
    target.write_bytes(b"identity\n")
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    descriptor: int | None = None
    owned_descriptors: list[int] = []
    cause = ValueError("pre-existing identity dual-failure cause")
    primary = _p3c0_primary_exception(primary_type, "simulated identity validation failure")
    primary.__cause__ = cause
    primary.add_note("pre-existing identity dual-failure note")
    close_error = OSError(errno.EIO, "simulated secondary identity close failure")
    close_error.add_note("secondary identity close nested note")
    close_attempts: list[int] = []
    injected_traceback = None

    def return_backing_descriptor(*_args, **_kwargs):
        return descriptor

    def fail_identity(*_args, **_kwargs):
        nonlocal injected_traceback
        try:
            raise primary
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    def fail_close(opened_descriptor):
        close_attempts.append(opened_descriptor)
        raise close_error

    harness_error: BaseException | None = None
    try:
        descriptor = real_open(
            target,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        owned_descriptors.append(descriptor)
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", return_backing_descriptor)
            patch_context.setattr(memory_store, "_descriptor_identity", fail_identity)
            patch_context.setattr(memory_store.os, "close", fail_close)
            with pytest.raises(primary_type) as caught:
                memory_store._open_identity_bound_regular(target, os.O_RDONLY)

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            primary,
            cause,
            "pre-existing identity dual-failure note",
            injected_traceback,
        )
        assert close_attempts == [descriptor]
        assert any(
            close_error.__class__.__name__ in note and str(close_error) in note
            for note in caught.value.__notes__
        )
        assert "nested: secondary identity close nested note" in caught.value.__notes__
        assert stat.S_ISREG(real_fstat(descriptor).st_mode)
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "call_path",
    ["restore-owned", "validate-owned", "append-jsonl"],
)
@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_descriptor_cleanup_closes_once_when_unlock_fails(
    tmp_path,
    monkeypatch,
    call_path,
    primary_type,
):
    target = tmp_path / f"{call_path}.jsonl"
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    preexisting_cause = ValueError("pre-existing descriptor release cause")
    unlock_error = primary_type("simulated descriptor unlock failure")
    unlock_error.__cause__ = preexisting_cause
    unlock_error.add_note("pre-existing descriptor release note")
    unlock_calls: list[int] = []
    close_calls: list[int] = []
    opened_descriptors: list[int] = []
    injected_traceback = None

    def observe_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        opened_descriptors.append(descriptor)
        return descriptor

    def fail_unlock(descriptor):
        nonlocal injected_traceback
        unlock_calls.append(descriptor)
        try:
            raise unlock_error
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    def observe_close(descriptor):
        if descriptor in unlock_calls:
            close_calls.append(descriptor)
        return real_close(descriptor)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", observe_open)
            patch_context.setattr(memory_store, "_unlock_descriptor", fail_unlock)
            patch_context.setattr(memory_store.os, "close", observe_close)
            with pytest.raises(primary_type) as caught:
                _p3c0_exercise_locked_descriptor_path(call_path, target)

        assert caught.value is unlock_error
        assert caught.value.__cause__ is preexisting_cause
        assert caught.value.__notes__[0] == "pre-existing descriptor release note"
        assert injected_traceback is not None
        traceback_cursor = caught.value.__traceback__
        while traceback_cursor is not None and traceback_cursor is not injected_traceback:
            traceback_cursor = traceback_cursor.tb_next
        assert traceback_cursor is injected_traceback
        assert len(unlock_calls) == 1
        assert close_calls == unlock_calls
        with pytest.raises(OSError) as invalid_descriptor:
            real_fstat(unlock_calls[0])
        assert invalid_descriptor.value.errno == errno.EBADF
        assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            opened_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "call_path",
    ["restore-owned", "validate-owned", "append-jsonl"],
)
def test_p3c0_descriptor_cleanup_preserves_unlock_when_close_also_fails(
    tmp_path,
    monkeypatch,
    call_path,
):
    target = tmp_path / f"{call_path}-dual-failure.jsonl"
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    preexisting_cause = ValueError("pre-existing dual descriptor cause")
    unlock_error = RuntimeError("simulated primary descriptor unlock failure")
    unlock_error.__cause__ = preexisting_cause
    unlock_error.add_note("pre-existing dual descriptor note")
    close_error = OSError(errno.EIO, "simulated secondary descriptor close failure")
    close_error.add_note("secondary descriptor close nested note")
    unlock_calls: list[int] = []
    production_close_attempts: list[int] = []
    opened_descriptors: list[int] = []
    injection_flags = {"unlock": False, "close": False}
    injected_traceback = None

    def observe_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        opened_descriptors.append(descriptor)
        return descriptor

    def fail_unlock(descriptor):
        nonlocal injected_traceback
        injection_flags["unlock"] = True
        unlock_calls.append(descriptor)
        try:
            raise unlock_error
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    def fail_close(descriptor):
        injection_flags["close"] = True
        production_close_attempts.append(descriptor)
        raise close_error

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", observe_open)
            patch_context.setattr(memory_store, "_unlock_descriptor", fail_unlock)
            patch_context.setattr(memory_store.os, "close", fail_close)
            with pytest.raises(RuntimeError) as caught:
                _p3c0_exercise_locked_descriptor_path(call_path, target)

        assert injection_flags == {"unlock": True, "close": True}
        assert caught.value is unlock_error
        assert caught.value.__cause__ is preexisting_cause
        assert caught.value.__notes__[0] == "pre-existing dual descriptor note"
        assert any(
            close_error.__class__.__name__ in note and str(close_error) in note
            for note in caught.value.__notes__
        )
        assert "nested: secondary descriptor close nested note" in caught.value.__notes__
        assert injected_traceback is not None
        traceback_cursor = caught.value.__traceback__
        while traceback_cursor is not None and traceback_cursor is not injected_traceback:
            traceback_cursor = traceback_cursor.tb_next
        assert traceback_cursor is injected_traceback
        assert len(unlock_calls) == 1
        assert len(production_close_attempts) == 1
        assert production_close_attempts == unlock_calls
        assert close_error.errno == errno.EIO
        assert close_error.errno != errno.EBADF
        descriptor_details = real_fstat(production_close_attempts[0])
        assert stat.S_ISREG(descriptor_details.st_mode)
        assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            opened_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "call_path",
    ["restore-owned", "validate-owned", "append-jsonl"],
)
def test_p3c0_descriptor_cleanup_propagates_close_failure_after_unlock(
    tmp_path,
    monkeypatch,
    call_path,
):
    target = tmp_path / f"{call_path}-close-failure.jsonl"
    real_unlock = memory_store._unlock_descriptor
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    preexisting_cause = ValueError("pre-existing descriptor close cause")
    close_error = OSError(errno.EIO, "simulated descriptor close-only failure")
    close_error.__cause__ = preexisting_cause
    close_error.add_note("pre-existing descriptor close note")
    unlock_calls: list[int] = []
    production_close_attempts: list[int] = []
    opened_descriptors: list[int] = []
    injection_flags = {"unlock": False, "close": False}
    injected_traceback = None

    def observe_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        opened_descriptors.append(descriptor)
        return descriptor

    def observe_unlock(descriptor):
        injection_flags["unlock"] = True
        unlock_calls.append(descriptor)
        return real_unlock(descriptor)

    def fail_close(descriptor):
        nonlocal injected_traceback
        injection_flags["close"] = True
        production_close_attempts.append(descriptor)
        try:
            raise close_error
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", observe_open)
            patch_context.setattr(memory_store, "_unlock_descriptor", observe_unlock)
            patch_context.setattr(memory_store.os, "close", fail_close)
            with pytest.raises(OSError) as caught:
                _p3c0_exercise_locked_descriptor_path(call_path, target)

        assert injection_flags == {"unlock": True, "close": True}
        assert caught.value is close_error
        assert caught.value.__cause__ is preexisting_cause
        assert caught.value.__notes__[0] == "pre-existing descriptor close note"
        assert injected_traceback is not None
        traceback_cursor = caught.value.__traceback__
        while traceback_cursor is not None and traceback_cursor is not injected_traceback:
            traceback_cursor = traceback_cursor.tb_next
        assert traceback_cursor is injected_traceback
        assert len(unlock_calls) == 1
        assert len(production_close_attempts) == 1
        assert production_close_attempts == unlock_calls
        assert caught.value.errno == errno.EIO
        assert caught.value.errno != errno.EBADF
        descriptor_details = real_fstat(production_close_attempts[0])
        assert stat.S_ISREG(descriptor_details.st_mode)
        assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            opened_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_transactional_replace_preserves_existing_mode_on_success(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    os.chmod(manifest, 0o640)
    expected_mode = stat.S_IMODE(manifest.stat().st_mode)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["updated_at"] = "2099-01-01T00:00:00Z"

    with memory_store._memory_connection(tmp_path) as conn:
        memory_store._transactional_write_json(conn, manifest, payload)

    assert stat.S_IMODE(manifest.stat().st_mode) == expected_mode
    assert json.loads(manifest.read_text(encoding="utf-8"))["updated_at"] == "2099-01-01T00:00:00Z"
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_r4_transactional_publish_preserves_foreign_destination_at_boundary(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    manifest = memory_store._manifest_path(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["updated_at"] = "2099-04-01T00:00:00Z"
    foreign = b'{"foreign":"publication-boundary"}\n'
    real_replace = memory_store.os.replace
    real_link = memory_store.os.link
    injected = False
    foreign_snapshot = None

    def inject_foreign(destination) -> None:
        nonlocal injected, foreign_snapshot
        if Path(destination) != manifest or injected:
            return
        injected = True
        replacement = manifest.with_name(".manifest-r4-foreign")
        replacement.write_bytes(foreign)
        real_replace(replacement, manifest)
        foreign_snapshot = _p3c0_file_snapshot(manifest)

    def inject_before_replace(source, destination, *args, **kwargs):
        inject_foreign(destination)
        return real_replace(source, destination, *args, **kwargs)

    def inject_before_link(source, destination, *args, **kwargs):
        inject_foreign(destination)
        return real_link(source, destination, *args, **kwargs)

    failure = None
    with monkeypatch.context() as patch_context:
        patch_context.setattr(memory_store.os, "replace", inject_before_replace)
        patch_context.setattr(memory_store.os, "link", inject_before_link)
        try:
            with memory_store._memory_connection(tmp_path) as conn:
                memory_store._transactional_write_json(conn, manifest, payload)
        except BaseException as exc:
            failure = exc

    assert injected is True
    assert failure is not None
    assert foreign_snapshot is not None
    assert _p3c0_file_snapshot(manifest) == foreign_snapshot
    assert manifest.read_bytes() == foreign


def test_p3c0_r4_projection_ownership_precedes_publication_return(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    projection_before = _p3c0_file_snapshot(projection)
    real_link = memory_store.os.link
    primary = RuntimeError("simulated failure at the projection publication boundary")
    primary.__cause__ = ValueError("pre-existing projection cause")
    primary.add_note("pre-existing projection note")
    published_snapshot = None

    def link_then_fail(source, destination, *args, **kwargs):
        nonlocal published_snapshot
        result = real_link(source, destination, *args, **kwargs)
        if Path(destination) == projection:
            published_snapshot = _p3c0_file_snapshot(projection)
            raise primary
        return result

    monkeypatch.setattr(memory_store.os, "link", link_then_fail)

    with pytest.raises(BaseException) as caught:
        with memory_store._memory_connection(
            tmp_path,
            compensate_committed_failure=True,
        ) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("p3c0_r4_projection_registration", "published"),
            )

    assert published_snapshot is not None
    assert published_snapshot != projection_before
    assert caught.value is primary
    projection_after = _p3c0_file_snapshot(projection)
    projection_after.pop("identity", None)
    projection_before.pop("identity", None)
    assert projection_after == projection_before
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_r4_projection_default_publication_creates_absent_target_no_clobber(
    tmp_path,
):
    projection = freshness_projection.projection_path(tmp_path)

    published = freshness_projection.publish_freshness_projection(
        tmp_path,
        {"state": "fresh"},
        [],
        {},
        {"snapshot": "database"},
        {"snapshot": "filesystem"},
    )

    assert published == projection
    assert json.loads(projection.read_text(encoding="utf-8"))["kind"] == (
        freshness_projection.HEALTH_PROJECTION_KIND
    )
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))


def test_p3c0_r4_atomic_publication_rejects_parent_replacement_between_observation_and_link(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "publication-parent"
    parked_parent = tmp_path / "publication-parent-parked"
    parent.mkdir()
    target = parent / "projection.json"
    foreign_sentinel = parent / "foreign-sentinel.txt"
    foreign_content = b"foreign parent content\n"
    real_directory_generation = memory_store._directory_generation
    real_link = memory_store.os.link
    injected = False
    destination_link_calls = []

    def replace_parent_when_temporary_is_prepared(path):
        nonlocal injected
        observed_path = Path(path)
        temporary_files = list(parent.glob(f".{target.name}.*.tmp"))
        if observed_path == parent and temporary_files and not injected:
            injected = True
            temporary_name = temporary_files[0].name
            parent.rename(parked_parent)
            parent.mkdir()
            os.replace(parked_parent / temporary_name, parent / temporary_name)
            foreign_sentinel.write_bytes(foreign_content)
        return real_directory_generation(observed_path)

    def observe_link(source, destination, *args, **kwargs):
        if Path(destination) == target:
            destination_link_calls.append((Path(source), Path(destination)))
        return real_link(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            memory_store,
            "_directory_generation",
            replace_parent_when_temporary_is_prepared,
        )
        patch_context.setattr(memory_store.os, "link", observe_link)
        with pytest.raises(RuntimeError, match="parent"):
            memory_store._atomic_replace_bytes(
                target,
                b'{"owned":true}\n',
                publication_state=[None],
            )

    assert injected is True
    assert destination_link_calls == []
    assert not target.exists()
    assert foreign_sentinel.read_bytes() == foreign_content


def test_p3c0_r4_projection_default_existing_target_restored_when_link_fails(
    tmp_path,
    monkeypatch,
):
    projection = freshness_projection.projection_path(tmp_path)
    projection.parent.mkdir(parents=True)
    baseline_content = b'{"baseline":"existing"}\n'
    projection.write_bytes(baseline_content)
    os.chmod(projection, 0o640)
    baseline = memory_store._file_rollback_state(projection)
    primary = OSError(errno.EIO, "simulated projection link failure")
    real_link = memory_store.os.link
    link_calls = []

    def fail_projection_link(source, destination, *args, **kwargs):
        if Path(destination) == projection:
            link_calls.append(Path(destination))
            if len(link_calls) == 1:
                assert not projection.exists()
                raise primary
            return real_link(source, destination, *args, **kwargs)
        raise AssertionError(f"unexpected link destination: {destination}")

    monkeypatch.setattr(memory_store.os, "link", fail_projection_link)

    with pytest.raises(OSError) as caught:
        freshness_projection.publish_freshness_projection(
            tmp_path,
            {"state": "fresh"},
            [],
            {},
            {"snapshot": "database"},
            {"snapshot": "filesystem"},
        )

    restored = memory_store._file_rollback_state(projection)
    assert caught.value is primary
    assert link_calls == [projection, projection]
    assert restored.existed is True
    assert restored.content == baseline_content
    assert restored.mode == baseline.mode
    assert restored.mtime_ns == baseline.mtime_ns
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))


def test_p3c0_r4_projection_default_existing_target_restored_when_link_succeeds_then_raises(
    tmp_path,
    monkeypatch,
):
    projection = freshness_projection.projection_path(tmp_path)
    projection.parent.mkdir(parents=True)
    baseline_content = b'{"baseline":"linked"}\n'
    projection.write_bytes(baseline_content)
    os.chmod(projection, 0o640)
    baseline = memory_store._file_rollback_state(projection)
    primary = RuntimeError("simulated exception after projection link")
    primary.__cause__ = ValueError("pre-existing projection link cause")
    primary.add_note("pre-existing projection link note")
    real_link = memory_store.os.link
    published = None

    def link_projection_then_raise(source, destination, *args, **kwargs):
        nonlocal published
        result = real_link(source, destination, *args, **kwargs)
        if Path(destination) == projection:
            published = memory_store._file_rollback_state(projection)
            raise primary
        return result

    monkeypatch.setattr(memory_store.os, "link", link_projection_then_raise)

    with pytest.raises(RuntimeError) as caught:
        freshness_projection.publish_freshness_projection(
            tmp_path,
            {"state": "fresh"},
            [],
            {},
            {"snapshot": "database"},
            {"snapshot": "filesystem"},
        )

    restored = memory_store._file_rollback_state(projection)
    assert published is not None
    assert published.content != baseline_content
    assert caught.value is primary
    assert caught.value.__cause__.__class__ is ValueError
    assert caught.value.__notes__[0] == "pre-existing projection link note"
    assert restored.existed is True
    assert restored.content == baseline_content
    assert restored.mode == baseline.mode
    assert restored.mtime_ns == baseline.mtime_ns
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))


def test_p3c0_r4_projection_default_preserves_foreign_replacement_after_link(
    tmp_path,
    monkeypatch,
):
    projection = freshness_projection.projection_path(tmp_path)
    projection.parent.mkdir(parents=True)
    projection.write_bytes(b'{"baseline":"foreign-case"}\n')
    foreign_content = b'{"foreign":"replacement"}\n'
    primary = RuntimeError("simulated exception after foreign projection replacement")
    primary.add_note("pre-existing foreign replacement note")
    real_link = memory_store.os.link
    real_replace = memory_store.os.replace
    foreign_snapshot = None

    def link_then_install_foreign(source, destination, *args, **kwargs):
        nonlocal foreign_snapshot
        result = real_link(source, destination, *args, **kwargs)
        if Path(destination) == projection:
            replacement = projection.with_name(".foreign-projection-replacement")
            replacement.write_bytes(foreign_content)
            real_replace(replacement, projection)
            foreign_snapshot = _p3c0_file_snapshot(projection)
            raise primary
        return result

    monkeypatch.setattr(memory_store.os, "link", link_then_install_foreign)

    with pytest.raises(RuntimeError) as caught:
        freshness_projection.publish_freshness_projection(
            tmp_path,
            {"state": "fresh"},
            [],
            {},
            {"snapshot": "database"},
            {"snapshot": "filesystem"},
        )

    assert caught.value is primary
    assert foreign_snapshot is not None
    assert _p3c0_file_snapshot(projection) == foreign_snapshot
    assert projection.read_bytes() == foreign_content
    assert caught.value.__notes__[0] == "pre-existing foreign replacement note"
    assert any("compensation" in note for note in caught.value.__notes__[1:])
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))


def test_p3c0_r4_atomic_publication_rejects_parent_generation_change_before_link(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "publication-generation"
    parent.mkdir()
    target = parent / "projection.json"
    foreign_sibling = parent / "foreign-sibling.txt"
    foreign_content = b"foreign sibling content\n"
    real_file_rollback_state = memory_store._file_rollback_state
    real_directory_generation = memory_store._directory_generation
    real_link = memory_store.os.link
    unchanged_parent_generation = real_directory_generation(parent)
    injected = False
    destination_link_calls = []

    def preserve_parent_generation(path):
        if Path(path) == parent:
            return unchanged_parent_generation
        return real_directory_generation(path)

    def change_parent_generation_after_temporary_capture(path, *args, **kwargs):
        nonlocal injected
        observed = real_file_rollback_state(path, *args, **kwargs)
        candidate = Path(path)
        if (
            candidate.parent == parent
            and candidate.name.startswith(f".{target.name}.")
            and candidate.name.endswith(".tmp")
            and observed.existed
            and not injected
        ):
            foreign_sibling.write_bytes(foreign_content)
            injected = True
        return observed

    def observe_link(source, destination, *args, **kwargs):
        if Path(destination) == target:
            destination_link_calls.append((Path(source), Path(destination)))
        return real_link(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            memory_store,
            "_file_rollback_state",
            change_parent_generation_after_temporary_capture,
        )
        patch_context.setattr(
            memory_store,
            "_directory_generation",
            preserve_parent_generation,
        )
        patch_context.setattr(memory_store.os, "link", observe_link)
        with pytest.raises(RuntimeError, match="parent"):
            memory_store._atomic_replace_bytes(
                target,
                b'{"owned":true}\n',
                publication_state=[None],
            )

    assert injected is True
    assert destination_link_calls == []
    assert not target.exists()
    assert foreign_sibling.read_bytes() == foreign_content


def test_p3c0_r4_atomic_publication_parent_entries_admit_owned_temporary(
    tmp_path,
):
    parent = tmp_path / "publication-owned-temporary"
    parent.mkdir()
    target = parent / "projection.json"

    published_identity = memory_store._atomic_replace_bytes(
        target,
        b'{"owned":true}\n',
        publication_state=[None],
    )

    assert memory_store._regular_file_object_identity(target) == published_identity
    assert target.read_bytes() == b'{"owned":true}\n'
    assert not list(parent.glob(f".{target.name}.*.tmp"))


def test_p3c0_r4_atomic_publication_rejects_foreign_sibling_replacement_before_link(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "publication-entry-replacement"
    parent.mkdir()
    target = parent / "projection.json"
    foreign_sibling = parent / "foreign-sibling.txt"
    foreign_sibling.write_bytes(b"baseline sibling content\n")
    foreign_content = b"replacement sibling content\n"
    real_directory_generation = memory_store._directory_generation
    real_file_rollback_state = memory_store._file_rollback_state
    real_link = memory_store.os.link
    real_replace = memory_store.os.replace
    unchanged_parent_generation = real_directory_generation(parent)
    injected = False
    destination_link_calls = []

    def preserve_parent_generation(path):
        if Path(path) == parent:
            return unchanged_parent_generation
        return real_directory_generation(path)

    def replace_foreign_sibling_after_temporary_capture(path, *args, **kwargs):
        nonlocal injected
        observed = real_file_rollback_state(path, *args, **kwargs)
        candidate = Path(path)
        if (
            candidate.parent == parent
            and candidate.name.startswith(f".{target.name}.")
            and candidate.name.endswith(".tmp")
            and observed.existed
            and not injected
        ):
            replacement = parent / ".foreign-sibling-replacement"
            replacement.write_bytes(foreign_content)
            real_replace(replacement, foreign_sibling)
            injected = True
        return observed

    def observe_link(source, destination, *args, **kwargs):
        if Path(destination) == target:
            destination_link_calls.append((Path(source), Path(destination)))
        return real_link(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            memory_store,
            "_file_rollback_state",
            replace_foreign_sibling_after_temporary_capture,
        )
        patch_context.setattr(
            memory_store,
            "_directory_generation",
            preserve_parent_generation,
        )
        patch_context.setattr(memory_store.os, "link", observe_link)
        with pytest.raises(RuntimeError, match="parent entries"):
            memory_store._atomic_replace_bytes(
                target,
                b'{"owned":true}\n',
                publication_state=[None],
            )

    assert injected is True
    assert destination_link_calls == []
    assert not target.exists()
    assert foreign_sibling.read_bytes() == foreign_content
    assert not list(parent.glob(f".{target.name}.*.tmp"))


def test_p3c0_r4_atomic_publication_rejects_unavailable_parent_entry_snapshot_before_link(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "publication-snapshot-unavailable"
    parent.mkdir()
    target = parent / "projection.json"
    real_scandir = memory_store.os.scandir
    real_link = memory_store.os.link
    parent_snapshot_attempts = 0
    destination_link_calls = []

    def fail_final_parent_snapshot(path):
        nonlocal parent_snapshot_attempts
        if Path(path) == parent:
            parent_snapshot_attempts += 1
            if parent_snapshot_attempts == 2:
                raise OSError(errno.EIO, "simulated parent snapshot failure")
        return real_scandir(path)

    def observe_link(source, destination, *args, **kwargs):
        if Path(destination) == target:
            destination_link_calls.append((Path(source), Path(destination)))
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(memory_store.os, "scandir", fail_final_parent_snapshot)
    monkeypatch.setattr(memory_store.os, "link", observe_link)

    with pytest.raises(RuntimeError, match="parent entry snapshot is unavailable"):
        memory_store._atomic_replace_bytes(
            target,
            b'{"owned":true}\n',
            publication_state=[None],
        )

    assert parent_snapshot_attempts == 2
    assert destination_link_calls == []
    assert not target.exists()
    assert not list(parent.glob(f".{target.name}.*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="requires real POSIX file generations")
def test_p3c0_r4_posix_missing_jsonl_create_append_rollback_cleans_all_owned_paths(
    tmp_path,
    capsys,
):
    _p3c0_init_project(tmp_path, capsys)
    owned_root = tmp_path / "docs" / "r4-jsonl-rollback"
    target = owned_root / "nested" / "event-log.jsonl"

    with pytest.raises(RuntimeError, match="trigger POSIX JSONL rollback"):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_append_jsonl(
                conn,
                target,
                {"event": "created-and-appended"},
            )
            assert target.read_bytes() == b'{"event": "created-and-appended"}\n'
            raise RuntimeError("trigger POSIX JSONL rollback")

    assert not target.exists()
    assert not owned_root.exists()
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


@pytest.mark.parametrize("foreign_action", ["mutate", "replace"])
def test_p3c0_r4_jsonl_foreign_change_after_owned_compensation_fails_closed(
    tmp_path,
    capsys,
    monkeypatch,
    foreign_action,
):
    _p3c0_init_project(tmp_path, capsys)
    target = tmp_path / "docs" / "r4-jsonl-foreign" / "event-log.jsonl"
    foreign_content = f'{{"foreign":"{foreign_action}"}}\n'.encode()
    real_restore = memory_store._restore_owned_file_bytes
    injected = False
    foreign_snapshot = None

    def restore_then_change(path, *args, **kwargs):
        nonlocal injected, foreign_snapshot
        restored = real_restore(path, *args, **kwargs)
        if Path(path) == target and not injected:
            injected = True
            if foreign_action == "replace":
                replacement = target.with_name(".foreign-jsonl-replacement")
                replacement.write_bytes(foreign_content)
                os.replace(replacement, target)
            else:
                target.write_bytes(foreign_content)
            foreign_snapshot = _p3c0_hardlink_snapshot(target)
        return restored

    monkeypatch.setattr(
        memory_store,
        "_restore_owned_file_bytes",
        restore_then_change,
    )

    with pytest.raises(
        RuntimeError,
        match="rollback did not restore every owned side effect",
    ):
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_append_jsonl(
                conn,
                target,
                {"event": "owned-before-foreign-change"},
            )
            raise RuntimeError("trigger JSONL foreign-change rollback")

    assert injected is True
    assert foreign_snapshot is not None
    assert _p3c0_hardlink_snapshot(target) == foreign_snapshot
    assert target.read_bytes() == foreign_content


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows directory API")
def test_p3c0_r4_windows_parent_guard_uses_native_change_time(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "windows-parent-change-time"
    parent.mkdir()
    target = parent / "owned.json"
    content = b'{"owned":true}\n'
    target.write_bytes(content)
    expected = memory_store._file_rollback_state(target)
    native_queries = []

    def changed_native_token(path):
        native_queries.append(Path(path))
        return expected.parent_generation.change_time_ns + 100

    monkeypatch.setattr(
        memory_store,
        "_windows_directory_change_time_ns",
        changed_native_token,
    )

    with pytest.raises(RuntimeError, match="parent generation changed"):
        memory_store._identity_bound_unlink(
            target,
            expected.identity,
            expected_content=content,
            expected_generation=expected.generation,
            expected_parent_generation=expected.parent_generation,
        )

    assert native_queries == [parent]
    assert target.read_bytes() == content


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows directory API")
def test_p3c0_r4_windows_native_change_time_error_fails_closed_without_ctime_fallback(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "windows-parent-native-error"
    parent.mkdir()
    native_error = OSError(errno.EIO, "simulated FILE_BASIC_INFO query failure")
    native_queries = []

    def fail_native_query(path):
        native_queries.append(Path(path))
        raise native_error

    monkeypatch.setattr(
        memory_store,
        "_windows_directory_change_time_ns",
        fail_native_query,
    )

    with pytest.raises(OSError) as caught:
        memory_store._directory_generation(parent)

    assert caught.value is native_error
    assert native_queries == [parent]


def test_p3c0_r4_atomic_publication_preserves_foreign_temporary_on_create_collision(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "publication-collision"
    parent.mkdir()
    target = parent / "projection.json"
    fixed_hex = "f" * 32
    foreign_temporary = parent / f".{target.name}.{fixed_hex}.tmp"
    foreign_temporary.write_bytes(b"foreign temporary content\n")
    foreign_snapshot = _p3c0_file_snapshot(foreign_temporary)

    monkeypatch.setattr(
        memory_store.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=fixed_hex),
    )

    with pytest.raises(FileExistsError):
        memory_store._atomic_replace_bytes(target, b'{"owned":true}\n')

    assert _p3c0_file_snapshot(foreign_temporary) == foreign_snapshot
    assert not target.exists()


def test_p3c0_r4_projection_default_restores_existing_target_when_unlink_completes_then_raises(
    tmp_path,
    monkeypatch,
):
    projection = freshness_projection.projection_path(tmp_path)
    projection.parent.mkdir(parents=True)
    baseline_content = b'{"baseline":"unlink-completed"}\n'
    projection.write_bytes(baseline_content)
    os.chmod(projection, 0o640)
    baseline = memory_store._file_rollback_state(projection)
    primary = OSError(errno.EIO, "simulated completed projection unlink failure")
    primary.__cause__ = ValueError("pre-existing completed unlink cause")
    primary.add_note("pre-existing completed unlink note")
    real_identity_bound_unlink = memory_store._identity_bound_unlink
    injected_traceback = None
    unlink_calls = []

    def unlink_projection_then_raise(path, expected, **kwargs):
        nonlocal injected_traceback
        result = real_identity_bound_unlink(path, expected, **kwargs)
        if Path(path) == projection:
            unlink_calls.append(Path(path))
            assert not projection.exists()
            try:
                raise primary
            except BaseException as active_error:
                injected_traceback = active_error.__traceback__
                raise
        return result

    monkeypatch.setattr(
        memory_store,
        "_identity_bound_unlink",
        unlink_projection_then_raise,
    )

    with pytest.raises(OSError) as caught:
        freshness_projection.publish_freshness_projection(
            tmp_path,
            {"state": "fresh"},
            [],
            {},
            {"snapshot": "database"},
            {"snapshot": "filesystem"},
        )

    restored = memory_store._file_rollback_state(projection)
    _p3c0_assert_primary_exception_preserved(
        caught.value,
        primary,
        primary.__cause__,
        "pre-existing completed unlink note",
        injected_traceback,
    )
    assert unlink_calls == [projection]
    assert restored.existed is True
    assert restored.content == baseline_content
    assert restored.mode == baseline.mode
    assert restored.mtime_ns == baseline.mtime_ns
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))


def test_p3c0_r4_managed_projection_rejects_replacement_parent_before_publication(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    parent = projection.parent
    parked_parent = parent.with_name(f"{parent.name}-r4-parked")
    foreign_sentinel = parent / "foreign-parent-sentinel.txt"
    foreign_content = b"foreign managed parent content\n"
    real_file_rollback_state = memory_store._file_rollback_state
    real_link = memory_store.os.link
    projection_observations = 0
    injected = False
    destination_link_calls = []

    def replace_parent_before_publication_observation(path, *args, **kwargs):
        nonlocal injected, projection_observations
        candidate = Path(path)
        if candidate == projection:
            projection_observations += 1
            if projection_observations == 2:
                parent.rename(parked_parent)
                parent.mkdir()
                os.replace(parked_parent / projection.name, projection)
                foreign_sentinel.write_bytes(foreign_content)
                injected = True
        return real_file_rollback_state(path, *args, **kwargs)

    def observe_link(source, destination, *args, **kwargs):
        if Path(destination) == projection:
            destination_link_calls.append((Path(source), Path(destination)))
        return real_link(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            memory_store,
            "_file_rollback_state",
            replace_parent_before_publication_observation,
        )
        patch_context.setattr(memory_store.os, "link", observe_link)
        with pytest.raises(BaseException):
            with memory_store._memory_connection(tmp_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                    ("p3c0_r4_parent_replacement", "committed"),
                )

    assert injected is True
    assert destination_link_calls == []
    assert foreign_sentinel.read_bytes() == foreign_content


def test_p3c0_r4_managed_projection_preserves_primary_when_foreign_blocks_compensation(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    foreign_content = b'{"foreign":"managed-replacement"}\n'
    primary = RuntimeError("simulated managed projection publication failure")
    cause = ValueError("pre-existing managed projection cause")
    primary.__cause__ = cause
    primary.add_note("pre-existing managed projection note")
    real_link = memory_store.os.link
    real_replace = memory_store.os.replace
    foreign_snapshot = None
    injected_traceback = None

    def link_then_install_foreign(source, destination, *args, **kwargs):
        nonlocal foreign_snapshot, injected_traceback
        result = real_link(source, destination, *args, **kwargs)
        if Path(destination) == projection:
            replacement = projection.with_name(".managed-foreign-projection")
            replacement.write_bytes(foreign_content)
            real_replace(replacement, projection)
            foreign_snapshot = _p3c0_file_snapshot(projection)
            try:
                raise primary
            except BaseException as active_error:
                injected_traceback = active_error.__traceback__
                raise
        return result

    monkeypatch.setattr(memory_store.os, "link", link_then_install_foreign)

    with pytest.raises(RuntimeError) as caught:
        with memory_store._memory_connection(
            tmp_path,
            compensate_committed_failure=True,
        ) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("p3c0_r4_managed_foreign", "published"),
            )

    _p3c0_assert_primary_exception_preserved(
        caught.value,
        primary,
        cause,
        "pre-existing managed projection note",
        injected_traceback,
    )
    assert foreign_snapshot is not None
    assert _p3c0_file_snapshot(projection) == foreign_snapshot
    assert projection.read_bytes() == foreign_content
    assert any("cleanup" in note or "compensation" in note for note in caught.value.__notes__[1:])
    assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None


def test_p3c0_r4_atomic_cleanup_only_propagates_inside_unrelated_except(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "atomic-cleanup-only" / "projection.json"
    cleanup_error = OSError(errno.EIO, "simulated atomic cleanup-only failure")
    cleanup_error.__cause__ = ValueError("pre-existing atomic cleanup cause")
    cleanup_error.add_note("pre-existing atomic cleanup note")
    ambient = RuntimeError("unrelated ambient atomic exception")
    real_unlink = Path.unlink
    temporary_paths = []
    temporary_unlink_calls = []
    injected_traceback = None

    def leave_then_fail_temporary_cleanup(path, *args, **kwargs):
        nonlocal injected_traceback
        candidate = Path(path)
        if (
            candidate.parent == target.parent
            and candidate.name.startswith(f".{target.name}.")
            and candidate.name.endswith(".tmp")
        ):
            if not temporary_paths:
                details = candidate.lstat()
                temporary_paths.append(
                    (candidate, (details.st_dev, details.st_ino))
                )
            temporary_unlink_calls.append(candidate)
            if len(temporary_unlink_calls) == 1:
                return None
            try:
                raise cleanup_error
            except BaseException as active_error:
                injected_traceback = active_error.__traceback__
                raise
        return real_unlink(path, *args, **kwargs)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(Path, "unlink", leave_then_fail_temporary_cleanup)
            try:
                raise ambient
            except RuntimeError:
                with pytest.raises(OSError) as caught:
                    memory_store._atomic_replace_bytes(target, b'{"owned":true}\n')

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            cleanup_error,
            cleanup_error.__cause__,
            "pre-existing atomic cleanup note",
            injected_traceback,
        )
        assert len(temporary_unlink_calls) == 2
        assert getattr(ambient, "__notes__", []) == []
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_paths(
            temporary_paths,
            primary_error=harness_error,
        )


def test_p3c0_r4_transaction_move_rejects_same_inode_source_reincarnation(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "r4-source.md"
    destination = tmp_path / "docs" / "research" / "r4-destination.md"
    anchor = tmp_path / "docs" / "inbox" / ".r4-source-anchor"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("same inode source\n", encoding="utf-8")
    os.link(source, anchor)
    real_unlink = memory_store._identity_bound_unlink
    real_link = os.link
    reincarnated = False
    reincarnated_snapshot = None

    def reincarnate_before_source_removal(path, expected, **kwargs):
        nonlocal reincarnated, reincarnated_snapshot
        if Path(path) == source and not reincarnated:
            source.unlink()
            real_link(anchor, source)
            reincarnated = True
            reincarnated_snapshot = _p3c0_file_snapshot(source)
        return real_unlink(path, expected, **kwargs)

    monkeypatch.setattr(
        memory_store,
        "_identity_bound_unlink",
        reincarnate_before_source_removal,
    )

    failure = None
    try:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)
    except BaseException as exc:
        failure = exc

    assert reincarnated is True
    assert failure is not None
    assert reincarnated_snapshot is not None
    assert _p3c0_file_snapshot(source) == reincarnated_snapshot
    assert anchor.exists()


def test_p3c0_r4_transaction_move_rollback_rejects_source_only_reincarnation(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    source = tmp_path / "docs" / "inbox" / "r4-source-only.md"
    destination = tmp_path / "docs" / "research" / "r4-source-only.md"
    anchor = tmp_path / "docs" / "inbox" / ".r4-source-only-anchor"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("same inode source-only rollback\n", encoding="utf-8")
    os.link(source, anchor)
    real_link = os.link
    primary = RuntimeError("simulated failure after source-only reincarnation")
    reincarnated_snapshot = None

    def leave_reincarnated_source_without_destination(path, _expected, **_kwargs):
        nonlocal reincarnated_snapshot
        if Path(path) == source:
            destination.unlink()
            source.unlink()
            real_link(anchor, source)
            reincarnated_snapshot = _p3c0_file_snapshot(source)
            raise primary
        raise AssertionError(f"unexpected transactional unlink: {path}")

    monkeypatch.setattr(
        memory_store,
        "_identity_bound_unlink",
        leave_reincarnated_source_without_destination,
    )

    with pytest.raises(BaseException) as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_move_file(conn, source, destination)

    assert caught.value is not primary
    assert "rollback did not restore every owned side effect" in str(caught.value)
    assert reincarnated_snapshot is not None
    assert _p3c0_file_snapshot(source) == reincarnated_snapshot
    assert not destination.exists()
    assert anchor.exists()


def test_p3c0_r4_new_output_rollback_preserves_same_inode_foreign_replacement(
    tmp_path,
    capsys,
):
    _p3c0_init_project(tmp_path, capsys)
    output_parent = tmp_path / "docs" / "outputs"
    anchor_parent = tmp_path / "docs" / "anchors"
    output_parent.mkdir(parents=True, exist_ok=True)
    anchor_parent.mkdir(parents=True, exist_ok=True)
    output = output_parent / "r4-new-output.md"
    anchor = anchor_parent / "r4-new-output-anchor.md"
    primary = RuntimeError("trigger r4 new-output rollback")
    reincarnated_snapshot = None

    with pytest.raises(BaseException) as caught:
        with memory_store._memory_connection(tmp_path) as conn:
            memory_store._transactional_write_bytes(conn, output, b"owned output\n")
            os.link(output, anchor)
            output.unlink()
            os.link(anchor, output)
            reincarnated_snapshot = _p3c0_file_snapshot(output)
            raise primary

    assert caught.value is not primary
    assert "rollback did not restore every owned side effect" in str(caught.value)
    assert reincarnated_snapshot is not None
    assert _p3c0_file_snapshot(output) == reincarnated_snapshot
    assert anchor.exists()


def test_p3c0_r4_identity_bound_unlink_accepts_own_posix_rename_ctime_change(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "posix-rename-ctime"
    parent.mkdir()
    target = parent / "owned.json"
    content = b'{"owned":true}\n'
    target.write_bytes(content)
    expected = memory_store._file_rollback_state(target)
    real_file_entry_generation = memory_store._file_entry_generation
    tombstone_observations = []

    def simulate_posix_rename_ctime(path):
        candidate = Path(path)
        observed = real_file_entry_generation(candidate)
        if (
            candidate.parent == parent
            and candidate.name.startswith(f".{target.name}.")
            and candidate.name.endswith(".rollback")
        ):
            tombstone_observations.append(candidate)
            return memory_store._FileEntryGeneration(
                identity=observed.identity,
                change_time_ns=expected.generation.change_time_ns + 1,
                link_count=observed.link_count,
            )
        return observed

    monkeypatch.setattr(
        memory_store,
        "_file_entry_generation",
        simulate_posix_rename_ctime,
    )

    memory_store._identity_bound_unlink(
        target,
        expected.identity,
        expected_content=content,
        expected_generation=expected.generation,
        expected_parent_generation=expected.parent_generation,
    )

    assert len(tombstone_observations) == 1
    assert not target.exists()
    assert not list(parent.glob(f".{target.name}.*.rollback"))


def test_p3c0_r4_identity_bound_unlink_rejects_foreign_generation_before_rename(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "foreign-generation"
    parent.mkdir()
    target = parent / "owned.json"
    content = b'{"owned":true}\n'
    target.write_bytes(content)
    expected = memory_store._file_rollback_state(target)
    real_file_entry_generation = memory_store._file_entry_generation

    def simulate_foreign_generation(path):
        candidate = Path(path)
        observed = real_file_entry_generation(candidate)
        if candidate == target:
            return memory_store._FileEntryGeneration(
                identity=observed.identity,
                change_time_ns=observed.change_time_ns + 1,
                link_count=observed.link_count,
            )
        return observed

    monkeypatch.setattr(
        memory_store,
        "_file_entry_generation",
        simulate_foreign_generation,
    )

    with pytest.raises(RuntimeError, match="generation changed outside rollback ownership"):
        memory_store._identity_bound_unlink(
            target,
            expected.identity,
            expected_content=content,
            expected_generation=expected.generation,
            expected_parent_generation=expected.parent_generation,
        )

    assert target.read_bytes() == content
    assert not list(parent.glob(f".{target.name}.*.rollback"))


@pytest.mark.parametrize(
    "close_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_r4_writer_lock_close_failure_removes_owned_path(
    tmp_path,
    monkeypatch,
    close_type,
):
    lock_path = freshness_projection.writer_lock_path(tmp_path)
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    writer_descriptor = None
    close_attempts = []
    owned_descriptors = []
    inspection_descriptors = []
    close_error = _p3c0_primary_exception(close_type, "simulated writer lock close failure")

    def observe_open(path, flags, mode=0o777):
        nonlocal writer_descriptor
        descriptor = real_open(path, flags, mode)
        owned_descriptors.append(descriptor)
        if Path(path) == lock_path and flags & os.O_EXCL:
            writer_descriptor = descriptor
        if Path(path).name.endswith(".release"):
            inspection_descriptors.append(descriptor)
        return descriptor

    def close_writer_then_fail(descriptor):
        if descriptor == writer_descriptor:
            close_attempts.append(descriptor)
            real_close(descriptor)
            raise close_error
        return real_close(descriptor)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(freshness_projection.os, "open", observe_open)
            patch_context.setattr(freshness_projection.os, "close", close_writer_then_fail)
            with pytest.raises(close_type) as caught:
                with freshness_projection.projection_writer_lock(tmp_path):
                    pass

        assert caught.value is close_error
        assert writer_descriptor is not None
        assert close_attempts == [writer_descriptor]
        assert len(inspection_descriptors) == 1
        _p3c0_assert_descriptor_closed(inspection_descriptors[0], real_fstat)
        assert not lock_path.exists()
        assert not list(tmp_path.glob(f".{lock_path.name}.*.release"))
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_r4_writer_lock_close_failure_preserves_foreign_path(
    tmp_path,
    monkeypatch,
):
    lock_path = freshness_projection.writer_lock_path(tmp_path)
    foreign = b"foreign lock replacement\n"
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    real_replace = os.replace
    real_rename = os.rename
    writer_descriptor = None
    close_attempts = []
    rename_attempts = []
    owned_descriptors = []
    inspection_descriptors = []
    foreign_snapshot = None
    close_error = OSError(errno.EIO, "simulated foreign writer lock close failure")

    def observe_open(path, flags, mode=0o777):
        nonlocal writer_descriptor
        descriptor = real_open(path, flags, mode)
        owned_descriptors.append(descriptor)
        if Path(path) == lock_path and flags & os.O_EXCL:
            writer_descriptor = descriptor
        if Path(path).name.endswith(".release"):
            inspection_descriptors.append(descriptor)
        return descriptor

    def replace_lock_then_fail(descriptor):
        nonlocal foreign_snapshot
        if descriptor == writer_descriptor:
            close_attempts.append(descriptor)
            real_close(descriptor)
            replacement = tmp_path / ".r4-foreign-lock"
            replacement.write_bytes(foreign)
            real_replace(replacement, lock_path)
            foreign_snapshot = _p3c0_file_snapshot(lock_path)
            raise close_error
        return real_close(descriptor)

    def observe_rename(source, destination, *args, **kwargs):
        if Path(source) == lock_path:
            rename_attempts.append((Path(source), Path(destination)))
        return real_rename(source, destination, *args, **kwargs)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(freshness_projection.os, "open", observe_open)
            patch_context.setattr(freshness_projection.os, "close", replace_lock_then_fail)
            patch_context.setattr(freshness_projection.os, "rename", observe_rename)
            with pytest.raises(OSError) as caught:
                with freshness_projection.projection_writer_lock(tmp_path):
                    pass

        assert caught.value is close_error
        assert close_attempts == [writer_descriptor]
        assert len(rename_attempts) == 1
        assert len(inspection_descriptors) == 1
        _p3c0_assert_descriptor_closed(inspection_descriptors[0], real_fstat)
        assert foreign_snapshot is not None
        assert _p3c0_file_snapshot(lock_path) == foreign_snapshot
        assert lock_path.read_bytes() == foreign
        assert any("ownership changed" in note for note in caught.value.__notes__)
        assert not list(tmp_path.glob(f".{lock_path.name}.*.release"))
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_r4_descriptor_primary_survives_unlock_failure(
    tmp_path,
    monkeypatch,
    primary_type,
):
    target = tmp_path / "r4-primary-unlock.jsonl"
    content = b'{"baseline":true}\n'
    target.write_bytes(content)
    identity = memory_store._regular_file_object_identity(target)
    primary = _p3c0_primary_exception(primary_type, "simulated descriptor operation failure")
    cause = ValueError("pre-existing descriptor primary cause")
    primary.__cause__ = cause
    primary.add_note("pre-existing descriptor primary note")
    unlock_error = RuntimeError("simulated secondary unlock failure")
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    opened_descriptors = []
    unlock_calls = []
    close_calls = []
    injected_traceback = None

    def observe_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        opened_descriptors.append(descriptor)
        return descriptor

    def fail_read(_descriptor):
        nonlocal injected_traceback
        try:
            raise primary
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    def fail_unlock(descriptor):
        unlock_calls.append(descriptor)
        raise unlock_error

    def observe_close(descriptor):
        if descriptor in unlock_calls:
            close_calls.append(descriptor)
        return real_close(descriptor)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "open", observe_open)
            patch_context.setattr(memory_store, "_read_descriptor_bytes", fail_read)
            patch_context.setattr(memory_store, "_unlock_descriptor", fail_unlock)
            patch_context.setattr(memory_store.os, "close", observe_close)
            with pytest.raises(primary_type) as caught:
                memory_store._validate_owned_file_bytes(target, identity, content)

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            primary,
            cause,
            "pre-existing descriptor primary note",
            injected_traceback,
        )
        assert len(unlock_calls) == 1
        assert close_calls == unlock_calls
        assert any(str(unlock_error) in note for note in caught.value.__notes__)
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            opened_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


class _P3C0CloseFailingConnection:
    def __init__(self, connection, close_error):
        self.connection = connection
        self.close_error = close_error
        self.close_calls = 0

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        self.close_calls += 1
        self.connection.close()
        raise self.close_error


def test_p3c0_r4_connection_primary_survives_close_failure(
    tmp_path,
    capsys,
    monkeypatch,
):
    _p3c0_init_project(tmp_path, capsys)
    real_connect = memory_store._connect
    close_error = OSError(errno.EIO, "simulated secondary connection close failure")
    primary = RuntimeError("simulated writer connection body failure")
    cause = ValueError("pre-existing connection cause")
    primary.__cause__ = cause
    primary.add_note("pre-existing connection note")
    raw_connection = None
    proxy = None
    owned_connections = []
    injected_traceback = None

    def connect_with_failing_close(_project):
        return proxy

    harness_error: BaseException | None = None
    try:
        raw_connection = real_connect(tmp_path)
        owned_connections.append(raw_connection)
        proxy = _P3C0CloseFailingConnection(raw_connection, close_error)
        monkeypatch.setattr(memory_store, "_connect", connect_with_failing_close)
        with pytest.raises(RuntimeError) as caught:
            with memory_store._memory_connection(tmp_path):
                try:
                    raise primary
                except BaseException as active_error:
                    injected_traceback = active_error.__traceback__
                    raise

        _p3c0_assert_primary_exception_preserved(
            caught.value,
            primary,
            cause,
            "pre-existing connection note",
            injected_traceback,
        )
        assert proxy.close_calls == 1
        assert any(str(close_error) in note for note in caught.value.__notes__)
        assert memory_store._ACTIVE_MEMORY_TRANSACTION.get() is None
        assert not freshness_projection.writer_lock_path(tmp_path).exists()
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_connections(
            owned_connections,
            primary_error=harness_error,
        )


def test_p3c0_r4_cleanup_failure_without_primary_preserves_first_error(
    tmp_path,
    monkeypatch,
):
    lock_path = freshness_projection.writer_lock_path(tmp_path)
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    real_unlink = Path.unlink
    writer_descriptor = None
    unlink_attempts = []
    owned_descriptors = []
    inspection_descriptors = []
    close_error = OSError(errno.EIO, "simulated primary cleanup close failure")
    unlink_error = PermissionError(errno.EACCES, "simulated secondary cleanup unlink failure")

    def observe_open(path, flags, mode=0o777):
        nonlocal writer_descriptor
        descriptor = real_open(path, flags, mode)
        owned_descriptors.append(descriptor)
        if Path(path) == lock_path and flags & os.O_EXCL:
            writer_descriptor = descriptor
        if Path(path).name.endswith(".release"):
            inspection_descriptors.append(descriptor)
        return descriptor

    def close_writer_then_fail(descriptor):
        if descriptor == writer_descriptor:
            real_close(descriptor)
            raise close_error
        return real_close(descriptor)

    def fail_release_unlink(path, *args, **kwargs):
        if Path(path).name.endswith(".release"):
            unlink_attempts.append(Path(path))
            raise unlink_error
        return real_unlink(path, *args, **kwargs)

    harness_error: BaseException | None = None
    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(freshness_projection.os, "open", observe_open)
            patch_context.setattr(freshness_projection.os, "close", close_writer_then_fail)
            patch_context.setattr(Path, "unlink", fail_release_unlink)
            with pytest.raises(OSError) as caught:
                with freshness_projection.projection_writer_lock(tmp_path):
                    pass

        assert caught.value is close_error
        assert len(unlink_attempts) == 1
        assert len(inspection_descriptors) == 1
        _p3c0_assert_descriptor_closed(inspection_descriptors[0], real_fstat)
        assert any(str(unlink_error) in note for note in caught.value.__notes__)
        assert lock_path.exists()
        real_unlink(lock_path)
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "cleanup_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_r4_release_descriptor_cleanup_only_propagates_inside_unrelated_except(
    tmp_path,
    monkeypatch,
    cleanup_type,
):
    target = tmp_path / "cleanup-only-descriptor.bin"
    target.write_bytes(b"descriptor cleanup ownership\n")
    real_close = os.close
    real_fstat = os.fstat
    descriptor: int | None = None
    owned_descriptors: list[int] = []
    cleanup_error = _p3c0_primary_exception(
        cleanup_type,
        "simulated cleanup-only descriptor failure",
    )
    cleanup_error.__cause__ = ValueError("pre-existing cleanup-only descriptor cause")
    cleanup_error.add_note("pre-existing cleanup-only descriptor note")
    ambient = RuntimeError("unrelated ambient descriptor exception")
    close_attempts = []
    injected_traceback = None

    def close_then_fail(observed_descriptor):
        nonlocal injected_traceback
        assert observed_descriptor == descriptor
        close_attempts.append(observed_descriptor)
        real_close(observed_descriptor)
        try:
            raise cleanup_error
        except BaseException as active_error:
            injected_traceback = active_error.__traceback__
            raise

    harness_error: BaseException | None = None
    try:
        descriptor = os.open(target, os.O_RDWR | getattr(os, "O_BINARY", 0))
        owned_descriptors.append(descriptor)
        with monkeypatch.context() as patch_context:
            patch_context.setattr(memory_store.os, "close", close_then_fail)
            try:
                raise ambient
            except RuntimeError:
                with pytest.raises(cleanup_type) as caught:
                    memory_store._release_descriptor(
                        descriptor,
                        locked=False,
                        primary_error=None,
                    )

        assert caught.value is cleanup_error
        assert caught.value.__cause__.__class__ is ValueError
        assert caught.value.__notes__[0] == "pre-existing cleanup-only descriptor note"
        assert injected_traceback is not None
        traceback_cursor = caught.value.__traceback__
        while traceback_cursor is not None and traceback_cursor is not injected_traceback:
            traceback_cursor = traceback_cursor.tb_next
        assert traceback_cursor is injected_traceback
        assert close_attempts == [descriptor]
        assert getattr(ambient, "__notes__", []) == []
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            owned_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


@pytest.mark.parametrize(
    "cleanup_type",
    [RuntimeError, OSError, KeyboardInterrupt, SystemExit],
    ids=["runtime-error", "os-error", "keyboard-interrupt", "system-exit"],
)
def test_p3c0_r4_close_connection_cleanup_only_propagates_inside_unrelated_except(
    monkeypatch,
    cleanup_type,
):
    raw_connection = None
    proxy = None
    owned_connections = []
    cleanup_error = _p3c0_primary_exception(
        cleanup_type,
        "simulated cleanup-only connection failure",
    )
    cleanup_error.__cause__ = ValueError("pre-existing cleanup-only connection cause")
    cleanup_error.add_note("pre-existing cleanup-only connection note")
    ambient = RuntimeError("unrelated ambient connection exception")

    harness_error: BaseException | None = None
    try:
        raw_connection = sqlite3.connect(":memory:")
        owned_connections.append(raw_connection)
        proxy = _P3C0CloseFailingConnection(raw_connection, cleanup_error)
        try:
            raise ambient
        except RuntimeError:
            with pytest.raises(cleanup_type) as caught:
                memory_store._close_connection(proxy, primary_error=None)

        assert caught.value is cleanup_error
        assert caught.value.__cause__.__class__ is ValueError
        assert caught.value.__notes__[0] == "pre-existing cleanup-only connection note"
        assert proxy.close_calls == 1
        assert getattr(ambient, "__notes__", []) == []
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_connections(
            owned_connections,
            primary_error=harness_error,
        )


def test_p3c0_r4_descriptor_harness_closes_when_sut_does_not_raise(
    tmp_path,
    monkeypatch,
):
    opened_descriptors = []
    real_close = os.close
    real_fstat = os.fstat

    def sut_does_not_raise(_call_path, target):
        descriptor = memory_store.os.open(
            target,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
        opened_descriptors.append(descriptor)

    monkeypatch.setattr(
        sys.modules[__name__],
        "_p3c0_exercise_locked_descriptor_path",
        sut_does_not_raise,
    )

    harness_error: BaseException | None = None
    try:
        with pytest.raises(BaseException, match="DID NOT RAISE"):
            test_p3c0_descriptor_cleanup_closes_once_when_unlock_fails(
                tmp_path,
                monkeypatch,
                "validate-owned",
                RuntimeError,
            )
        assert len(opened_descriptors) == 1
        with pytest.raises(OSError) as closed:
            real_fstat(opened_descriptors[0])
        assert closed.value.errno == errno.EBADF
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            opened_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_r4_descriptor_harness_closes_when_sut_raises_wrong_type(
    tmp_path,
    monkeypatch,
):
    opened_descriptors = []
    real_close = os.close
    real_fstat = os.fstat
    wrong_error = ValueError("simulated wrong descriptor SUT exception")

    def sut_raises_wrong_type(_call_path, target):
        descriptor = memory_store.os.open(
            target,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
        opened_descriptors.append(descriptor)
        raise wrong_error

    monkeypatch.setattr(
        sys.modules[__name__],
        "_p3c0_exercise_locked_descriptor_path",
        sut_raises_wrong_type,
    )

    harness_error: BaseException | None = None
    try:
        with pytest.raises(ValueError) as caught:
            test_p3c0_descriptor_cleanup_closes_once_when_unlock_fails(
                tmp_path,
                monkeypatch,
                "validate-owned",
                RuntimeError,
            )
        assert caught.value is wrong_error
        assert len(opened_descriptors) == 1
        with pytest.raises(OSError) as closed:
            real_fstat(opened_descriptors[0])
        assert closed.value.errno == errno.EBADF
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            opened_descriptors,
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_r4_connection_harness_closes_on_assertion_failure(
    tmp_path,
    capsys,
    monkeypatch,
):
    raw_connections = []
    real_connect = memory_store._connect
    assertion_error = AssertionError("simulated post-SUT connection assertion failure")

    def capture_connect(project):
        connection = real_connect(project)
        raw_connections.append(connection)
        return connection

    def leave_connection_open(proxy):
        proxy.close_calls += 1
        raise proxy.close_error

    def fail_post_sut_assertion(*_args, **_kwargs):
        raise assertion_error

    monkeypatch.setattr(memory_store, "_connect", capture_connect)
    monkeypatch.setattr(_P3C0CloseFailingConnection, "close", leave_connection_open)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_p3c0_assert_primary_exception_preserved",
        fail_post_sut_assertion,
    )

    harness_error: BaseException | None = None
    try:
        with pytest.raises(AssertionError) as caught:
            test_p3c0_r4_connection_primary_survives_close_failure(
                tmp_path,
                capsys,
                monkeypatch,
            )
        assert caught.value is assertion_error
        assert raw_connections
        for connection in raw_connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_connections(
            raw_connections,
            primary_error=harness_error,
        )


def test_p3c0_r4_writer_lock_inspection_descriptor_is_closed(
    tmp_path,
    monkeypatch,
):
    opened = []
    real_open = os.open
    real_close = os.close
    real_fdopen = os.fdopen
    real_fstat = os.fstat
    suppressed_inspection_closes = set()

    class NoCloseHandle:
        def close(self):
            return None

    def capture_open(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        opened.append((descriptor, Path(path), flags))
        return descriptor

    def is_inspection_descriptor(descriptor):
        return any(
            opened_descriptor == descriptor and path.name.endswith(".release")
            for opened_descriptor, path, _flags in opened
        )

    def suppress_first_inspection_close(descriptor):
        if (
            is_inspection_descriptor(descriptor)
            and descriptor not in suppressed_inspection_closes
        ):
            suppressed_inspection_closes.add(descriptor)
            return None
        return real_close(descriptor)

    def suppress_first_inspection_fdopen(descriptor, *args, **kwargs):
        if (
            is_inspection_descriptor(descriptor)
            and descriptor not in suppressed_inspection_closes
        ):
            suppressed_inspection_closes.add(descriptor)
            return NoCloseHandle()
        return real_fdopen(descriptor, *args, **kwargs)

    monkeypatch.setattr(freshness_projection.os, "open", capture_open)
    monkeypatch.setattr(freshness_projection.os, "close", suppress_first_inspection_close)
    monkeypatch.setattr(
        freshness_projection.os,
        "fdopen",
        suppress_first_inspection_fdopen,
    )

    harness_error: BaseException | None = None
    try:
        with pytest.raises(AssertionError) as caught:
            test_p3c0_r4_writer_lock_close_failure_removes_owned_path(
                tmp_path,
                monkeypatch,
                RuntimeError,
            )
        inspection = [
            descriptor
            for descriptor, path, _flags in opened
            if path.name.endswith(".release")
        ]
        assert len(inspection) == 1
        assert caught.value.args == (f"descriptor remained open: {inspection[0]}",)
        assert suppressed_inspection_closes == {inspection[0]}
        with pytest.raises(OSError) as closed:
            real_fstat(inspection[0])
        assert closed.value.errno == errno.EBADF
    except BaseException as exc:
        harness_error = exc
        raise
    finally:
        _p3c0_cleanup_owned_descriptors(
            [item[0] for item in opened],
            real_fstat,
            real_close,
            primary_error=harness_error,
        )


def test_p3c0_r4_readonly_real_query_creates_no_sqlite_artifacts(
    tmp_path,
    capsys,
):
    database = _p3c0_init_project(tmp_path, capsys)
    artifacts = [
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
        freshness_projection.writer_lock_path(tmp_path),
    ]
    assert all(not path.exists() for path in artifacts)
    memory_parent_before = memory_store._directory_generation(database.parent)
    project_parent_before = memory_store._directory_generation(tmp_path)

    conn = memory_store._connect_readonly_db(database)
    try:
        assert conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()[0]
        during = {path.name: _p3c0_file_snapshot(path) for path in artifacts}
    finally:
        conn.close()

    assert all(not snapshot["exists"] for snapshot in during.values())
    assert all(not path.exists() for path in artifacts)
    assert memory_store._directory_generation(database.parent) == memory_parent_before
    assert memory_store._directory_generation(tmp_path) == project_parent_before


def test_p3c0_r4_readonly_real_query_is_correct_and_filesystem_invariant(
    tmp_path,
    capsys,
):
    database = _p3c0_init_project(tmp_path, capsys)
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("p3c0_r4_readonly_marker", "current committed value"),
        )

    watched = [
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
        freshness_projection.writer_lock_path(tmp_path),
    ]
    before = {path.name: _p3c0_file_snapshot(path) for path in watched}
    memory_parent_before = memory_store._directory_generation(database.parent)
    project_parent_before = memory_store._directory_generation(tmp_path)

    with memory_store._readonly_memory_connection(tmp_path) as conn:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            ("p3c0_r4_readonly_marker",),
        ).fetchone()

    assert row[0] == "current committed value"
    assert {path.name: _p3c0_file_snapshot(path) for path in watched} == before
    assert memory_store._directory_generation(database.parent) == memory_parent_before
    assert memory_store._directory_generation(tmp_path) == project_parent_before


def test_p3c0_r3_filesystem_change_during_writer_snapshot_aborts_publication(tmp_path, capsys, monkeypatch):
    _p3c0_init_project(tmp_path, capsys)
    projection = freshness_projection.projection_path(tmp_path)
    before = projection.read_bytes()
    real_capture = freshness_projection.capture_filesystem_fingerprint
    calls = 0

    def changing_filesystem(project):
        nonlocal calls
        calls += 1
        fingerprint, reason = real_capture(project)
        if calls == 2 and fingerprint is not None:
            fingerprint = deepcopy(fingerprint)
            fingerprint["p3c0TestMutation"] = True
        return fingerprint, reason

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "capture_filesystem_fingerprint", changing_filesystem)
        with pytest.raises(RuntimeError) as captured:
            with memory_store._memory_connection(tmp_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                    ("p3c0_filesystem_race", "committed-without-publication"),
                )

    assert calls == 2
    assert "filesystem" in str(captured.value).lower()
    assert "changed" in str(captured.value).lower()
    assert projection.read_bytes() == before
    assert not freshness_projection.writer_lock_path(tmp_path).exists()
    assert not list(projection.parent.glob(f".{projection.name}.*.tmp"))
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        assert conn.execute("SELECT value FROM metadata WHERE key = 'p3c0_filesystem_race'").fetchone() is None
    observation = freshness_projection.observe_freshness(tmp_path)
    assert observation["state"] in {"fresh", "unknown"}
    if observation["state"] == "unknown":
        assert observation["reason"] == "projection_outdated"


def test_p3c0_projection_preserves_legacy_snapshot_and_ignores_foreign_markdown(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    now = "2026-08-12T00:00:00Z"
    with memory_store._memory_connection(tmp_path) as conn:
        conn.executemany(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              source_refs, provenance, body, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("P3C0DECISION", "decision", "P3-C0 decision", "P3C0", "controlcoding_dev", "docs/p3c0-decision.md", "active", "[]", "{}", "decision", "{}", now, now),
                ("P3C0APP", "application_memory_component", "P3-C0 app record", "P3C0", "controlcoding_dev", "docs/p3c0-app.md", "active", "[]", "{}", "application record", "{}", now, now),
            ],
        )
        conn.execute(
            "INSERT INTO edges(id, source_id, target_id, type, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("P3C0EDGE", "P3C0DECISION", "P3C0APP", "references", "{}", now),
        )
        conn.execute(
            """
            INSERT INTO events(id, type, timestamp, actor_type, command, target_entity_ids,
                               affected_paths, before_state, after_state, reason, provenance, review_required)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("P3C0EVENT", "create", now, "human", "p3c0", "[]", "[]", "{}", "{}", "P3-C0 projection", "{}", 0),
        )

    projection = _p3c0_projection_payload(tmp_path)
    authoritative = memory_bootstrap._bootstrap_payload(tmp_path, scope="general", topic="")
    actual = projection["legacyStatus"]
    assert actual["bootstrap"] == authoritative
    assert sum(actual["graph"]["edgeCounts"].values()) == authoritative["devPlane"]["counts"]["edges"]
    assert actual["bootstrap"]["devPlane"]["counts"]["events"] == authoritative["devPlane"]["counts"]["events"]

    # The direct SQLite equivalence probe can create an empty WAL on Windows.
    # Republish only inside this isolated fixture before checking that foreign
    # Markdown invalidates freshness but remains outside GraphRAG classification.
    _p3c0_republish_current(tmp_path)

    packet_root = tmp_path / ".controlcoding" / "context-packets"
    packet_root.mkdir(parents=True, exist_ok=True)
    (packet_root / "foreign-markdown.md").write_text("# unrelated markdown\n", encoding="utf-8")
    outdated = freshness_projection.observe_freshness(tmp_path)
    assert outdated["state"] == "unknown"
    assert outdated["color"] == "YELLOW"
    assert outdated["reason"] == "projection_outdated"

    _p3c0_commit_projection_marker(tmp_path, "foreign-markdown")
    index = memory_op_index._op_index_payload(tmp_path, scope="dev", topic="P3 C0")
    assert index["indexHealth"]["graphPackets"]["fileCount"] == 0
    assert index["indexHealth"]["devContextPackets"]["fileCount"] == 1
    assert index["nextReads"]["activeFocus"] == authoritative["activeFocus"]
    assert any("memory rag-pack" in item.get("command", "") for item in index["actionQueue"])

    recognized_packet = packet_root / "recognized-rag.md"
    recognized_packet.write_text("# ControlCoding GraphRAG Packet\n\nAuthoritative test packet.\n", encoding="utf-8")
    outdated = freshness_projection.observe_freshness(tmp_path)
    assert outdated["state"] == "unknown"
    assert outdated["reason"] == "projection_outdated"

    _p3c0_commit_projection_marker(tmp_path, "recognized-markdown")
    recognized_index = memory_op_index._op_index_payload(tmp_path, scope="dev", topic="P3 C0")
    assert recognized_index["indexHealth"]["graphPackets"]["fileCount"] == 1
    assert recognized_index["nextReads"]["latestPackets"]["devGraphRag"]["path"].endswith("recognized-rag.md")
    assert not any("memory rag-pack" in item.get("command", "") for item in recognized_index["actionQueue"])


def test_p3c0_r3_filesystem_fingerprint_detects_add_remove_and_modify(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    artifact = tmp_path / ".controlcoding" / "context-packets" / "filesystem-probe.md"

    def assert_filesystem_only_change(change) -> None:
        database_before, reason = freshness_projection.capture_source_fingerprint(tmp_path)
        assert reason == ""
        projection_before = freshness_projection.projection_path(tmp_path).read_bytes()
        change()
        database_after, reason = freshness_projection.capture_source_fingerprint(tmp_path)
        assert reason == ""
        observation = freshness_projection.observe_freshness(tmp_path)
        assert database_after == database_before
        assert freshness_projection.projection_path(tmp_path).read_bytes() == projection_before
        assert observation["state"] == "unknown"
        assert observation["color"] == "YELLOW"
        assert observation["reason"] == "projection_outdated"

    def add_artifact() -> None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# Filesystem probe\n\nFirst version.\n", encoding="utf-8")

    assert_filesystem_only_change(add_artifact)
    _p3c0_commit_projection_marker(tmp_path, "after-add")

    assert_filesystem_only_change(artifact.unlink)
    _p3c0_commit_projection_marker(tmp_path, "after-remove")

    assert_filesystem_only_change(add_artifact)
    _p3c0_commit_projection_marker(tmp_path, "before-modify")
    assert_filesystem_only_change(
        lambda: artifact.write_text("# Filesystem probe\n\nSecond version.\n", encoding="utf-8")
    )


def test_p3c0_r3_real_rag_pack_invalidates_projection_without_mutating_database(tmp_path, capsys):
    database = _p3c0_init_project(tmp_path, capsys)
    assert cc.cmd_memory_note_add(
        tmp_path,
        "P3-C0 retrieval evidence",
        body="Filesystem projection evidence for a real GraphRAG packet.",
        area="P3-C0",
    ) == 0
    capsys.readouterr()
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"

    output = tmp_path / ".controlcoding" / "context-packets" / "p3c0-r3-rag-pack.md"
    # A read-only SQLite probe can create or remove an empty WAL on Windows.
    # Normalize that incidental DB state through the explicit writer first.
    _p3c0_commit_projection_marker(tmp_path, "before-real-rag-pack")
    database_before, reason = freshness_projection.capture_source_fingerprint(tmp_path)
    assert reason == ""
    database_file_before = _p3c0_file_snapshot(database)
    projection_before = freshness_projection.projection_path(tmp_path).read_bytes()

    assert _run_main([
        "memory", "rag-pack", "--project-root", str(tmp_path),
        "P3-C0 retrieval evidence", "--limit", "4", "--output", str(output), "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)

    database_after, reason = freshness_projection.capture_source_fingerprint(tmp_path)
    assert reason == ""
    assert result["ok"] is True
    assert result["outputPath"] == ".controlcoding/context-packets/p3c0-r3-rag-pack.md"
    assert output.is_file()
    assert output.read_text(encoding="utf-8").startswith("# ControlCoding GraphRAG Packet")
    assert database_after["files"]["memory.db"] == database_before["files"]["memory.db"]
    assert _p3c0_file_snapshot(database) == database_file_before
    assert freshness_projection.projection_path(tmp_path).read_bytes() == projection_before

    observation = freshness_projection.observe_freshness(tmp_path)
    assert observation["state"] == "unknown"
    assert observation["color"] == "YELLOW"
    assert observation["reason"] == "projection_outdated"


def test_p3c0_spawned_writers_publish_only_the_final_committed_snapshot(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    context = multiprocessing.get_context("spawn")
    boundary = context.Barrier(3)
    start_event = context.Event()
    result_queue = context.Queue()
    children = [
        context.Process(
            target=_p3c0_projection_writer_worker,
            args=(str(tmp_path), worker_id, boundary, start_event, result_queue),
        )
        for worker_id in (1, 2)
    ]
    for child in children:
        child.start()
    boundary.wait(timeout=15)
    start_event.set()
    results = [result_queue.get(timeout=20) for _item in children]
    for child in children:
        child.join(timeout=20)
        assert child.exitcode == 0
    assert all(item["ok"] for item in results)

    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"
    projection = _p3c0_projection_payload(tmp_path)
    with memory_store._readonly_memory_connection(tmp_path) as conn:
        final_entity_count = int(conn.execute("SELECT COUNT(*) AS count FROM entities").fetchone()["count"])
    assert projection["legacyStatus"]["entities"] == final_entity_count
    assert projection["legacyStatus"]["bootstrap"]["devPlane"]["counts"]["entities"] == final_entity_count
    assert not freshness_projection.writer_lock_path(tmp_path).exists()
    assert not list(freshness_projection.projection_path(tmp_path).parent.glob(".freshness_projection.json.*.tmp"))


def test_p3c0_r2_closed_world_nested_records_and_counts_degrade_to_unknown(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    now = "2026-08-12T00:00:00Z"
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              source_refs, provenance, body, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "P3C0R2ENTITY", "decision", "P3-C0-R2 entity", "P3C0", "controlcoding_dev",
                "docs/p3c0-r2.md", "active", "[]", "{}", "closed world probe", "{}", now, now,
            ),
        )

    projection_path = freshness_projection.projection_path(tmp_path)
    valid = _p3c0_projection_payload(tmp_path)
    assert valid["legacyStatus"]["hotDocuments"]
    mutations = []

    payload = deepcopy(valid)
    payload["unexpected"] = True
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["schema"]["unexpected"] = "no"
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["vectors"]["unexpected"] = 0
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["views"]["unexpected"] = False
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["views"] = []
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["hotDocuments"][0]["unexpected"] = "no"
    mutations.append(payload)

    payload = deepcopy(valid)
    link = payload["legacyStatus"]["bootstrap"]["projectPlane"]["link"]
    link["externalPath"] = "C:/external-controlwork"
    link["validation"] = {
        "path": "C:/external-controlwork",
        "hasCanonicalContext": True,
        "hasConfig": True,
        "hasMemoryRoot": True,
        "ok": True,
        "unexpected": True,
    }
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["vectors"]["rowCount"] = -1
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["bootstrap"]["devPlane"]["counts"]["entities"] = True
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["activeCount"] = payload["legacyStatus"]["sessions"]["sessionCount"] + 1
    mutations.append(payload)

    payload = deepcopy(valid)
    views = payload["legacyStatus"]["sessions"]["views"]
    views["generatedViewCount"] = views["requiredViewCount"] + 1
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["entities"] += 1
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["hotDocuments"].append({})
    mutations.append(payload)

    payload = deepcopy(valid)
    del payload["databaseFingerprint"]["sourceDirectory"]
    mutations.append(payload)

    for malformed in mutations:
        projection_path.write_text(json.dumps(malformed), encoding="utf-8")
        observation = freshness_projection.observe_freshness(tmp_path)
        assert observation["state"] == "unknown"
        assert observation["color"] == "YELLOW"
        assert observation["reason"] == "projection_incoherent"

    projection_path.write_text(json.dumps(valid), encoding="utf-8")
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"


def test_p3c0_r2_known_legacy_session_link_shape_round_trips_without_open_maps(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    assert _run_main([
        "memory", "session", "start", "--project-root", str(tmp_path),
        "--id", "p3c0-r2-legacy-link", "--topic", "Legacy link", "--mode", "maintenance",
    ]) == 0
    capsys.readouterr()

    legacy_link = {"type": "references_entry", "target": "docs/legacy-link.md"}
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute(
            "UPDATE session_records SET links = ? WHERE id = ?",
            (json.dumps([legacy_link]), "p3c0-r2-legacy-link"),
        )

    observation = freshness_projection.observe_freshness(tmp_path)
    assert observation["state"] == "fresh"
    assert observation["legacyStatus"]["sessions"]["latestSession"]["links"] == [legacy_link]

    projection_path = freshness_projection.projection_path(tmp_path)
    valid = _p3c0_projection_payload(tmp_path)
    malformed = deepcopy(valid)
    malformed["legacyStatus"]["sessions"]["latestSession"]["unexpected"] = True
    projection_path.write_text(json.dumps(malformed), encoding="utf-8")
    malformed_observation = freshness_projection.observe_freshness(tmp_path)
    assert malformed_observation["state"] == "unknown"
    assert malformed_observation["reason"] == "projection_incoherent"
    projection_path.write_text(json.dumps(valid), encoding="utf-8")
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"


def test_p3c0_r3_session_enums_lifecycle_summary_and_views_fail_closed(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    assert _run_main([
        "memory", "session", "start", "--project-root", str(tmp_path),
        "--id", "p3c0-r3-session", "--topic", "Session validation", "--mode", "maintenance",
    ]) == 0
    capsys.readouterr()
    valid = _p3c0_projection_payload(tmp_path)
    latest = valid["legacyStatus"]["sessions"]["latestSession"]
    assert latest["schemaVersion"] == cc_memory.SESSION_RECORD_SCHEMA_VERSION
    assert latest["mode"] in cc_memory.VALID_SESSION_MODES
    assert latest["status"] == "active"
    assert latest["endedAt"] == ""
    mutations = []

    for field, value in (
        ("schemaVersion", cc_memory.SESSION_RECORD_SCHEMA_VERSION + 1),
        ("mode", "unsupported_mode"),
        ("status", "unsupported_status"),
        ("id", ""),
        ("startedAt", "not-a-timestamp"),
        ("updatedAt", ""),
        ("endedAt", latest["startedAt"]),
    ):
        payload = deepcopy(valid)
        payload["legacyStatus"]["sessions"]["latestSession"][field] = value
        mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["latestSession"]["status"] = "completed"
    payload["legacyStatus"]["sessions"]["latestSession"]["endedAt"] = ""
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["latestSession"]["links"] = [{"type": "", "target": "docs/target.md"}]
    mutations.append(payload)

    payload = deepcopy(valid)
    session_id = payload["legacyStatus"]["sessions"]["latestSession"]["id"]
    payload["legacyStatus"]["sessions"]["latestSession"]["edges"] = [{
        "id": "edge-1",
        "sessionId": session_id + "-other",
        "type": "references_entry",
        "target": "docs/target.md",
        "targetType": "document",
        "data": {},
        "createdAt": latest["createdAt"],
    }]
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["openFollowups"] = [{
        "sessionId": session_id,
        "topic": "",
        "status": "unsupported_status",
        "text": "",
    }]
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["available"] = False
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["message"] = "unexpected warning"
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["sessionCount"] = 0
    payload["legacyStatus"]["sessions"]["activeCount"] = 0
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["views"]["implemented"] = False
    mutations.append(payload)

    payload = deepcopy(valid)
    views = payload["legacyStatus"]["sessions"]["views"]
    views["paths"] = [views["paths"][0], views["paths"][0], *views["paths"][2:]]
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["views"]["stale"] = not payload["legacyStatus"]["sessions"]["views"]["stale"]
    mutations.append(payload)

    payload = deepcopy(valid)
    payload["legacyStatus"]["sessions"]["views"]["latestSessionUpdatedAt"] = "2026-08-13T23:59:59Z"
    mutations.append(payload)

    for malformed in mutations:
        _p3c0_assert_projection_rejected(tmp_path, malformed)

    freshness_projection.projection_path(tmp_path).write_text(json.dumps(valid, allow_nan=False), encoding="utf-8")
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"


def test_p3c0_postcommit_projection_rejects_forged_graph_lifecycle_and_followup(
    tmp_path, capsys, monkeypatch
):
    _p3c0_init_project(tmp_path, capsys)
    now = "2026-08-14T08:00:00+00:00"
    with memory_store._memory_connection(tmp_path) as conn:
        conn.executemany(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              source_refs, provenance, body, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "P3C0POSTCOMMITENTITY", "note", "Projection coherence note", "P3C0",
                    "controlcoding_dev", "docs/projection-coherence.md", "verified", "[]", "{}",
                    "projection coherence probe", "{}", now, now,
                ),
                (
                    "P3C0POSTCOMMITTARGET", "note", "Projection coherence target", "P3C0",
                    "controlcoding_dev", "docs/projection-coherence-target.md", "verified", "[]", "{}",
                    "projection coherence target", "{}", now, now,
                ),
            ],
        )
        conn.execute(
            "INSERT INTO edges(id, source_id, target_id, type, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "P3C0POSTCOMMITEDGE", "P3C0POSTCOMMITENTITY", "P3C0POSTCOMMITTARGET",
                "custom_projection_edge", "{}", now,
            ),
        )
    session_timestamp = "2026-08-14T08:00:00Z"
    older_session_id = "p3c0-postcommit-session-a"
    latest_session_id = "p3c0-postcommit-session-b"
    monkeypatch.setattr(memory_sessions, "_now_iso", lambda: session_timestamp)
    assert _run_main([
        "memory", "session", "start", "--project-root", str(tmp_path),
        "--id", older_session_id, "--topic", "Projection coherence",
        "--mode", "maintenance",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory", "session", "note", "--project-root", str(tmp_path),
        older_session_id, "Older canonical follow-up", "--kind", "followup",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory", "session", "start", "--project-root", str(tmp_path),
        "--id", latest_session_id, "--topic", "Latest projection coherence",
        "--mode", "maintenance",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory", "session", "note", "--project-root", str(tmp_path),
        latest_session_id, "Canonical projected follow-up", "--kind", "followup",
    ]) == 0
    capsys.readouterr()

    valid = _p3c0_projection_payload(tmp_path)
    assert valid["legacyStatus"]["hotDocuments"][0]["lifecycle"] == "verified"
    assert valid["graphEvidence"]["edgeTypes"] == {"custom_projection_edge": 1}
    assert valid["legacyStatus"]["graph"]["edgeTypeCoverage"]["unmappedTypes"] == [
        "custom_projection_edge"
    ]
    expected_session_ids = sorted([older_session_id, latest_session_id], reverse=True)
    assert [item["id"] for item in valid["sessionEvidence"]] == expected_session_ids
    latest = valid["legacyStatus"]["sessions"]["latestSession"]
    assert latest["id"] == latest_session_id
    assert latest["followups"] == ["Canonical projected follow-up"]
    assert valid["legacyStatus"]["sessions"]["openFollowups"][0]["text"] == latest["followups"][0]
    assert len(valid["sessionEvidence"]) == 2

    def assert_public_reader_rejects(payload):
        _p3c0_assert_projection_rejected(tmp_path, payload)
        assert _run_main([
            "memory", "status", "--project-root", str(tmp_path), "--json",
        ]) == 0
        public_status = json.loads(capsys.readouterr().out)
        assert public_status["available"] is False
        assert public_status["health"]["state"] == "unknown"
        assert public_status["health"]["color"] == "YELLOW"
        assert public_status["health"]["reason"] == "projection_incoherent"

    forged_entity_coverage = deepcopy(valid)
    forged_entity_coverage["legacyStatus"]["graph"]["entityTypeCoverage"] = {
        "knownCurrentTypes": ["forged_entity_type"],
        "mappedTypes": ["forged_entity_type"],
        "unmappedTypes": [],
    }
    assert_public_reader_rejects(forged_entity_coverage)

    forged_edge_coverage = deepcopy(valid)
    forged_edge_coverage["legacyStatus"]["graph"]["edgeTypeCoverage"] = {
        "mappedTypes": ["forged_edge_type"],
        "unmappedTypes": ["forged_edge_type"],
    }
    assert_public_reader_rejects(forged_edge_coverage)

    forged_unmapped_edge = deepcopy(valid)
    forged_unmapped_edge["legacyStatus"]["graph"]["edgeTypeCoverage"]["unmappedTypes"] = [
        "forged_edge_type"
    ]
    assert_public_reader_rejects(forged_unmapped_edge)

    forged_lifecycle = deepcopy(valid)
    forged_lifecycle["legacyStatus"]["hotDocuments"][0]["lifecycle"] = "forged_lifecycle"
    forged_lifecycle["legacyStatus"]["bootstrap"]["hotDocuments"][0]["lifecycle"] = "forged_lifecycle"
    assert_public_reader_rejects(forged_lifecycle)

    contradictory_followup = deepcopy(valid)
    contradictory_followup["legacyStatus"]["sessions"]["openFollowups"][0]["text"] = (
        "Forged contradictory follow-up"
    )
    assert_public_reader_rejects(contradictory_followup)

    contradictory_older_followup = deepcopy(valid)
    older_followup = next(
        item
        for item in contradictory_older_followup["legacyStatus"]["sessions"]["openFollowups"]
        if item["sessionId"] == older_session_id
    )
    older_followup["text"] = "Forged older-session follow-up"
    assert_public_reader_rejects(contradictory_older_followup)

    freshness_projection.projection_path(tmp_path).write_text(
        json.dumps(valid, allow_nan=False), encoding="utf-8"
    )
    assert freshness_projection.projected_status_payload(tmp_path)["health"]["state"] == "fresh"


def test_p3c0_r3_artifact_count_latest_file_and_paths_fail_closed(tmp_path, capsys):
    _p3c0_init_project(tmp_path, capsys)
    valid = _p3c0_projection_payload(tmp_path)
    artifacts = valid["legacyStatus"]["bootstrap"]["derivedArtifacts"]
    latest_file = {
        "path": ".controlcoding/context-packets/p3c0.md",
        "exists": True,
        "isFile": True,
        "isDirectory": False,
        "sizeBytes": 12,
        "updatedAt": "2026-08-13T00:00:00Z",
    }
    mutations = []

    payload = deepcopy(valid)
    artifact = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    artifact["exists"] = False
    artifact["fileCount"] = 1
    artifact["latestFile"] = deepcopy(latest_file)
    mutations.append(payload)

    payload = deepcopy(valid)
    artifact = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    artifact["exists"] = True
    artifact["fileCount"] = 0
    artifact["latestFile"] = deepcopy(latest_file)
    mutations.append(payload)

    payload = deepcopy(valid)
    artifact = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    artifact["exists"] = True
    artifact["fileCount"] = 1
    artifact["latestFile"] = {}
    mutations.append(payload)

    payload = deepcopy(valid)
    artifact = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    artifact["exists"] = True
    artifact["fileCount"] = 1
    artifact["latestFile"] = {**latest_file, "isDirectory": True}
    mutations.append(payload)

    payload = deepcopy(valid)
    artifact = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["devContextPackets"]
    artifact["path"] = "../outside-project"
    mutations.append(payload)

    payload = deepcopy(valid)
    graph_packets = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["graphPackets"]
    graph_packets["stale"] = False
    mutations.append(payload)

    payload = deepcopy(valid)
    graph_packets = payload["legacyStatus"]["bootstrap"]["derivedArtifacts"]["graphPackets"]
    graph_packets["exists"] = True
    graph_packets["fileCount"] = 1
    graph_packets["latestFile"] = {}
    graph_packets["stale"] = False
    mutations.append(payload)

    for malformed in mutations:
        _p3c0_assert_projection_rejected(tmp_path, malformed)

    assert artifacts["graphPackets"]["fileCount"] == 0
    freshness_projection.projection_path(tmp_path).write_text(json.dumps(valid, allow_nan=False), encoding="utf-8")
    assert freshness_projection.observe_freshness(tmp_path)["state"] == "fresh"


def test_p3c0_r2_absent_wal_races_and_directory_metadata_are_observable(tmp_path, capsys, monkeypatch):
    database = _p3c0_init_project(tmp_path, capsys)
    wal = Path(f"{database}-wal")
    wal.unlink(missing_ok=True)

    stable, reason = freshness_projection.capture_source_fingerprint(tmp_path)
    assert reason == ""
    assert stable is not None
    assert stable["files"][wal.name]["exists"] is False
    assert set(stable["sourceDirectory"]) == {"type", "identity"}

    absent = {"exists": False, "type": "absent", "identity": None, "sizeBytes": 0, "mtimeNs": 0, "sha256": ""}
    regular = {"exists": True, "type": "regular", "identity": {"device": 1, "inode": 1}, "sizeBytes": 1, "mtimeNs": 1, "sha256": "a" * 64}
    replacement = {**regular, "identity": {"device": 1, "inode": 2}}
    real_capture_file = freshness_projection._capture_file

    for before, after in ((absent, regular), (regular, absent), (regular, replacement)):
        calls = 0

        def changing_wal(path, label):
            nonlocal calls
            if label == "wal":
                calls += 1
                return deepcopy(before if calls == 1 else after), ""
            return real_capture_file(path, label)

        with monkeypatch.context() as patch_context:
            patch_context.setattr(freshness_projection, "_capture_file", changing_wal)
            fingerprint, observed_reason = freshness_projection.capture_source_fingerprint(tmp_path)
        assert calls == 2
        assert fingerprint is None
        assert observed_reason == "source_wal_changed_during_capture"

    real_capture_directory = freshness_projection._capture_directory
    directory_calls = 0

    def changing_directory(path, label):
        nonlocal directory_calls
        record, observed_reason = real_capture_directory(path, label)
        directory_calls += 1
        if directory_calls == 2 and record is not None:
            record = {**record, "mtimeNs": record["mtimeNs"] + 1}
        return record, observed_reason

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "_capture_directory", changing_directory)
        fingerprint, observed_reason = freshness_projection.capture_source_fingerprint(tmp_path)
    assert directory_calls == 2
    assert fingerprint is None
    assert observed_reason == "source_directory_changed_during_capture"


def test_p3c0_r2_git_feature_and_category_unavailable_values_are_not_observed(tmp_path, monkeypatch):
    unavailable_root = {"available": False, "isProjectGitRoot": False, "root": "", "message": "git missing"}
    monkeypatch.setattr(memory_op_index, "_git_root", lambda project: unavailable_root)
    unavailable = memory_op_index._working_tree(tmp_path)
    assert unavailable["available"] is False
    assert unavailable["trackedDirtyCount"] is None
    assert unavailable["changes"] is None
    assert unavailable["health"]["state"] == "unknown"
    assert unavailable["health"]["reason"] == "git_root_unavailable"

    observed_root = {"available": True, "isProjectGitRoot": True, "root": str(tmp_path), "message": ""}
    monkeypatch.setattr(memory_op_index, "_git_root", lambda project: observed_root)

    def unavailable_status(*_args, **_kwargs):
        raise OSError("git executable unavailable")

    monkeypatch.setattr(memory_op_index.subprocess, "run", unavailable_status)
    status_unavailable = memory_op_index._working_tree(tmp_path)
    assert status_unavailable["available"] is False
    assert status_unavailable["trackedDirtyCount"] is None
    assert status_unavailable["changes"] is None
    assert status_unavailable["health"]["reason"] == "git_status_unavailable"

    monkeypatch.setattr(
        memory_op_index.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stdout="", stderr="fatal: simulated failure"),
    )
    status_failed = memory_op_index._working_tree(tmp_path)
    assert status_failed["available"] is False
    assert status_failed["trackedDirtyCount"] is None
    assert status_failed["changes"] is None
    assert status_failed["health"]["reason"] == "git_status_failed"

    monkeypatch.setattr(
        memory_op_index.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" M docs/changed.md\n?? scratch.txt\n", stderr=""),
    )
    observed = memory_op_index._working_tree(tmp_path)
    assert observed["available"] is True
    assert observed["trackedDirtyCount"] == 2
    assert isinstance(observed["changes"], list)
    assert observed["health"]["state"] == "observed"

    missing = work_features.feature_status_payload(tmp_path)
    assert missing["available"] is False
    assert missing["categories"]["approved"] is None
    assert missing["health"]["reason"] == "category_registry_missing"

    category_path = tmp_path / work_features.CATEGORY_PATH
    category_path.parent.mkdir(parents=True)
    category_path.write_text(json.dumps({"schemaVersion": 1, "categories": [{}]}), encoding="utf-8")
    malformed = work_features.feature_status_payload(tmp_path)
    assert malformed["available"] is False
    assert malformed["categories"] == {"available": False, "approved": None, "proposed": None}
    assert malformed["health"]["reason"] == "category_registry_invalid"

    invalid_status_registry = work_features._default_registry()
    invalid_status_registry["categories"][0]["status"] = "unsupported"
    category_path.write_text(json.dumps(invalid_status_registry), encoding="utf-8")
    invalid_status = work_features.feature_status_payload(tmp_path)
    assert invalid_status["available"] is False
    assert invalid_status["health"]["reason"] == "category_registry_invalid"

    category_path.write_text(json.dumps(work_features._default_registry()), encoding="utf-8")
    monkeypatch.setattr(freshness_projection, "_markdown_directory_count_for_feature_status", lambda path, reason: (None, "context_packets_unreadable"))
    filesystem_unavailable = work_features.feature_status_payload(tmp_path)
    assert filesystem_unavailable["available"] is False
    assert filesystem_unavailable["contextPackets"] is None
    assert filesystem_unavailable["health"]["state"] == "unknown"

    monkeypatch.setattr(freshness_projection, "_markdown_directory_count_for_feature_status", lambda path, reason: (0, ""))
    monkeypatch.setattr(freshness_projection, "_directory_exists_for_feature_status", lambda path, reason: (None, "wiki_unreadable"))
    wiki_unavailable = work_features.feature_status_payload(tmp_path)
    assert wiki_unavailable["available"] is False
    assert wiki_unavailable["hasWiki"] is None
    assert wiki_unavailable["health"]["reason"] == "wiki_unreadable"

    def broken_feature_status(_project):
        raise RuntimeError("feature adapter failed")

    monkeypatch.setattr(cc_feature, "feature_status_payload", broken_feature_status)
    feature_unavailable = memory_op_index._feature_status_payload(tmp_path)
    assert feature_unavailable["available"] is False
    assert feature_unavailable["initialized"] is None
    assert feature_unavailable["summary"] is None
    assert feature_unavailable["features"] is None
    assert feature_unavailable["blockedFeatureIds"] is None
    assert feature_unavailable["health"]["state"] == "unknown"


def test_p3c0_r2_startup_health_is_top_level_and_text_does_not_measure_unknown(tmp_path):
    observation = {
        "state": "unknown",
        "color": "YELLOW",
        "reason": "projection_missing",
        "message": "Projection is missing.",
        "remediation": "Publish explicitly.",
        "readOnly": True,
        "sourceOfTruth": False,
        "projectionPath": str(tmp_path / ".controlcoding" / "memory" / "freshness_projection.json"),
        "capabilities": {"memoryHealthProjection": "available"},
        "legacyStatus": None,
    }
    payload = memory_op_index._startup_payload(tmp_path, scope="dev", topic="health", observation=observation)
    text = memory_op_index._startup_text(payload)

    assert payload["health"] is observation
    assert "Memory health" in text
    assert "State: UNKNOWN (YELLOW)" in text
    assert "Reason: projection_missing" in text
    assert "Session records: unknown (not observed)" in text
    assert "Session records: None" not in text


def test_p3c0_r3_project_plane_adapter_preserves_unknowns_and_action_queue(tmp_path, monkeypatch):
    observations = [
        {
            "available": False,
            "state": "unknown",
            "color": "YELLOW",
            "reason": "project_plane_config_malformed",
            "message": "Project Plane config is malformed.",
        },
        {
            "available": False,
            "state": "unknown",
            "color": "YELLOW",
            "reason": "project_plane_context_unreadable",
            "message": "Project Plane context is unreadable.",
        },
        {
            "available": False,
            "state": "unknown",
            "color": "YELLOW",
            "reason": "project_plane_attachment_unverifiable",
            "message": "Project Plane attachment cannot be verified.",
        },
    ]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy work-status adapter must not run for unobservable inputs")

    monkeypatch.setattr(memory_op_index, "_work_status_payload", forbidden)
    for observation in observations:
        monkeypatch.setattr(memory_op_index, "observe_project_plane_inputs", lambda project, result=observation: result)
        work_status = memory_op_index._observed_work_status_payload(tmp_path)

        assert work_status["ok"] is True
        assert work_status["available"] is False
        assert work_status["distribution"] is None
        assert work_status["memoryCounts"] is None
        assert work_status["features"] is None
        assert work_status["attached"] is None
        assert work_status["attachment"] is None
        assert work_status["drift"] is None
        assert work_status["health"]["state"] == "unknown"
        assert work_status["health"]["color"] == "YELLOW"
        assert work_status["health"]["reason"] == observation["reason"]

        queue = memory_op_index._action_queue(
            {"recommendations": []},
            work_status,
            {"suggestionCounts": None},
            {"views": None, "openFollowups": None},
            {},
            {"trackedDirtyCount": None},
            "p3c0",
            "dev",
        )
        assert any(item["source"] == "work-status" for item in queue)
        assert not any("no drift" in str(item.get("reason", "")).lower() for item in queue)


def test_p3c0_r3_project_plane_observer_rejects_malformed_unreadable_and_attachment_inputs(tmp_path, monkeypatch):
    absent = freshness_projection.observe_project_plane_inputs(tmp_path)
    assert absent == {
        "available": True,
        "state": "absent",
        "reason": "project_plane_absent",
        "message": "Project Plane inputs are observably absent.",
    }

    work_root = tmp_path / ".controlwork"
    work_root.mkdir()
    config = work_root / "config.json"
    link = work_root / "link.json"

    config.write_text("{not-json", encoding="utf-8")
    malformed_config = freshness_projection.observe_project_plane_inputs(tmp_path)
    assert malformed_config["available"] is False
    assert malformed_config["state"] == "unknown"
    assert malformed_config["reason"] == "project_plane_config_malformed"

    config.write_text(json.dumps({
        "distribution": "embedded_controlcoding",
        "standaloneCompatible": True,
    }), encoding="utf-8")
    link.write_text("[not-an-object]", encoding="utf-8")
    malformed_link = freshness_projection.observe_project_plane_inputs(tmp_path)
    assert malformed_link["available"] is False
    assert malformed_link["reason"] == "project_plane_link_malformed"

    link.unlink()
    categories = work_root / "categories.json"
    categories.write_text("{not-json", encoding="utf-8")
    malformed_categories = freshness_projection.observe_project_plane_inputs(tmp_path)
    assert malformed_categories["available"] is False
    assert malformed_categories["reason"] == "project_plane_categories_malformed"
    categories.unlink()

    context = tmp_path / "CONTROLWORK.md"
    context.write_text("# Project Plane\n", encoding="utf-8")
    real_capture_file = freshness_projection._capture_file

    def unreadable_context(path, label):
        if label == "project_plane_embedded_context":
            return None, "source_project_plane_embedded_context_unreadable"
        return real_capture_file(path, label)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(freshness_projection, "_capture_file", unreadable_context)
        unreadable = freshness_projection.observe_project_plane_inputs(tmp_path)
    assert unreadable["available"] is False
    assert unreadable["state"] == "unknown"
    assert unreadable["reason"] == "project_plane_context_unreadable"

    link.write_text(json.dumps({
        "externalPath": str(tmp_path / "missing-external-project-plane"),
        "syncPolicy": "manual",
        "attachedAt": "2026-08-13T00:00:00Z",
    }), encoding="utf-8")
    attachment = freshness_projection.observe_project_plane_inputs(tmp_path)
    assert attachment["available"] is False
    assert attachment["state"] == "unknown"
    assert attachment["reason"] == "project_plane_attachment_unverifiable"


def test_p3c0_r3_project_plane_adapter_rejects_inputs_changed_while_reading(tmp_path, monkeypatch):
    observation = {
        "available": True,
        "state": "observed",
        "reason": "",
        "message": "Project Plane inputs were observed.",
    }
    captures = iter([({"generation": 1}, ""), ({"generation": 2}, "")])
    monkeypatch.setattr(memory_op_index, "observe_project_plane_inputs", lambda project: observation)
    monkeypatch.setattr(memory_op_index, "capture_project_plane_inputs", lambda project: next(captures))
    monkeypatch.setattr(memory_op_index, "_work_status_payload", lambda project: {"ok": True, "drift": {}})

    payload = memory_op_index._observed_work_status_payload(tmp_path)

    assert payload["available"] is False
    assert payload["drift"] is None
    assert payload["health"]["state"] == "unknown"
    assert payload["health"]["color"] == "YELLOW"
    assert payload["health"]["reason"] == "project_plane_inputs_changed_during_adapter"


def test_p3c0_r3_op_index_text_renders_state_color_and_reason_together(tmp_path):
    observation = {
        "state": "unknown",
        "color": "YELLOW",
        "reason": "projection_missing",
        "message": "Projection is missing.",
        "remediation": "Publish explicitly.",
        "readOnly": True,
        "sourceOfTruth": False,
        "projectionPath": str(tmp_path / ".controlcoding" / "memory" / "freshness_projection.json"),
        "capabilities": {"memoryHealthProjection": "available"},
        "legacyStatus": None,
    }
    work_status = memory_op_index._project_plane_unknown_payload(
        tmp_path,
        {
            "available": False,
            "state": "unknown",
            "color": "YELLOW",
            "reason": "project_plane_unavailable",
            "message": "Project Plane is not observed.",
        },
    )
    payload = memory_op_index._unknown_op_index_payload(
        tmp_path,
        "dev",
        "p3c0",
        observation,
        work_status,
        {},
        {"available": False, "trackedDirtyCount": None, "changes": None},
    )

    rendered = memory_op_index._op_index_text(payload)

    assert "Health projection: UNKNOWN/YELLOW [projection_missing]" in rendered
    assert "Project Plane present: unknown (not observed)" in rendered
    assert "Project Plane drift: unknown (not observed)" in rendered
    assert "Tracked dirty files: unknown (not observed)" in rendered
    assert "None" not in rendered


def _p3b2_make_project(root: Path, *, pending_only: bool = False, include_pdf: bool = False) -> Path:
    docs = root / "docs"
    docs.mkdir(parents=True)
    if pending_only:
        (docs / "pending.md").write_text("Pending-only evidence.\n", encoding="utf-8")
    else:
        duplicate_text = "Shared duplicate evidence.\n"
        (docs / "duplicate-a.md").write_text(duplicate_text, encoding="utf-8")
        (docs / "duplicate-b.md").write_text(duplicate_text, encoding="utf-8")
        (docs / "report-draft.md").write_text("Draft report evidence.\n", encoding="utf-8")
        (docs / "report-final.md").write_text("Final report evidence.\n", encoding="utf-8")
        (docs / "conflict.md").write_text(
            "<<<<<<< ours\nCurrent evidence.\n=======\nOther evidence.\n>>>>>>> theirs\n",
            encoding="utf-8",
        )
        (docs / "pending.md").write_text("Independent pending evidence.\n", encoding="utf-8")
    if include_pdf:
        (docs / "scan.pdf").write_bytes(b"%PDF-1.4\nP3-B2 fixture\n%%EOF\n")
    work_features.refresh_file_index(root)
    return root


def _p3b2_queue(project: Path, *, generated_at: str = "2026-08-09T00:00:00Z") -> dict:
    payload = work_features.read_file_index(project)
    analysis = work_features.build_scan_analysis(project, payload, limit=max(1, len(payload.get("files", []))))
    return work_review_queue.build_review_queue(
        payload,
        analysis,
        generated_at=generated_at,
        index_path=work_features.FILE_INDEX_PATH.as_posix(),
    )


def _p3b2_item(project: Path, kind: str) -> dict:
    return next(item for item in _p3b2_queue(project)["items"] if item["kind"] == kind and item["status"] != "obsolete")


def _p3b2_apply_decision(
    project: Path,
    item: dict,
    *,
    action: str,
    canonical: str = "",
    note: str = "",
    decided_at: str = "2026-08-09T00:00:00Z",
) -> dict:
    def queue_builder(payload: dict) -> dict:
        analysis = work_features.build_scan_analysis(project, payload, limit=max(1, len(payload.get("files", []))))
        return work_review_queue.build_review_queue(
            payload,
            analysis,
            generated_at=decided_at,
            index_path=work_features.FILE_INDEX_PATH.as_posix(),
        )

    return work_review_queue.apply_relational_decision(
        work_features.file_index_path(project),
        queue_builder,
        item_id=item["id"],
        action=action,
        canonical_path=canonical,
        note=note,
        fingerprint=item["fingerprint"],
        decided_at=decided_at,
    )


def _p3b2_review_namespace(project: Path, **overrides) -> SimpleNamespace:
    values = {
        "project_root": project,
        "path": "",
        "review_status": "",
        "sensitivity": "",
        "note": "",
        "filter": "pending",
        "batch": False,
        "item": "",
        "action": "",
        "canonical": "",
        "fingerprint": "",
        "proposal": False,
        "output": None,
        "limit": 20,
        "json_output": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _db_rows(project: Path, query: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(project / ".controlcoding" / "memory" / "memory.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def _db_scalar(project: Path, query: str):
    rows = _db_rows(project, query)
    return rows[0][0] if rows else None


def _memory_db_path(project: Path) -> Path:
    return project / ".controlcoding" / "memory" / "memory.db"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_simple_native_pdf(path: Path, *lines: str) -> None:
    text_ops = b" T* ".join(f"({line}) Tj".encode("ascii") for line in lines)
    pdf_text = b"BT /F1 12 Tf 72 720 Td " + text_ops + b" ET"
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length "
        + str(len(pdf_text)).encode("ascii")
        + b" >>\nstream\n"
        + pdf_text
        + b"\nendstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"%%EOF\n"
    )
    path.write_bytes(pdf_bytes)


def _write_minimal_docx(path: Path, *paragraphs: str) -> None:
    body = "".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def _source_ledger_records(project: Path) -> list[dict]:
    payload = json.loads((project / ".controlwork" / "source-ledger.json").read_text(encoding="utf-8"))
    return payload["records"]


def _prepare_corrupt_memory_db(project: Path) -> tuple[Path, bytes, str]:
    assert cc.cmd_memory_init(project, mode="document-only", project_short="Demo") == 0
    db_path = _memory_db_path(project)
    corrupt_bytes = b"not a sqlite database\nmemory corruption fixture\n"
    db_path.write_bytes(corrupt_bytes)
    return db_path, corrupt_bytes, _file_sha256(db_path)


def _assert_db_bytes_unchanged(db_path: Path, before_bytes: bytes, before_hash: str) -> None:
    assert db_path.read_bytes() == before_bytes
    assert _file_sha256(db_path) == before_hash


def _assert_actionable_memory_db_error(payload: dict[str, object], db_path: Path, operation: str) -> None:
    assert payload["ok"] is False
    assert payload["code"] == "memory_db_unreadable"
    assert payload["error"] == "memory_db_unreadable"
    assert payload["operation"] == operation
    assert payload["databasePath"] == str(db_path)
    assert payload["diagnosticCommand"] == "python scripts/cc.py memory doctor --project-root ."
    assert "No automatic repair was attempted" in str(payload["manualAction"])
    assert "memory.db" in str(payload["message"])
    assert "file is not a database" in str(payload["sqliteError"])


def _assert_actionable_memory_db_sqlite_error(
    payload: dict[str, object],
    db_path: Path,
    operation: str,
) -> None:
    assert payload["ok"] is False
    assert payload["code"] == "memory_db_sqlite_error"
    assert payload["error"] == "memory_db_sqlite_error"
    assert payload["operation"] == operation
    assert payload["databasePath"] == str(db_path)
    assert payload["diagnosticCommand"] == "python scripts/cc.py memory doctor --project-root ."
    assert "No automatic repair was attempted" in str(payload["manualAction"])
    assert "memory.db" in str(payload["message"])
    assert "database is locked" in str(payload["sqliteError"])


def _assert_single_json_stdout(captured) -> dict[str, object]:
    assert captured.out.lstrip().startswith("{")
    assert captured.out.rstrip().endswith("}")
    assert "Error:" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload, end = json.JSONDecoder().raw_decode(captured.out)
    assert captured.out[end:].strip() == ""
    assert isinstance(payload, dict)
    return payload


def _assert_plain_text_stdout(captured, expected_prefix: str) -> None:
    assert captured.out.startswith(expected_prefix)
    assert not captured.out.lstrip().startswith("{")
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured.out)


def _assert_default_scoring_payload(scoring: dict[str, object]) -> None:
    assert scoring["schemaVersion"] == "cc-memory-retrieval-scoring/v1"
    assert scoring["text"]["exactPathOrId"] == 42.0
    assert scoring["text"]["tokenInText"] == 3.0
    assert scoring["sparseVector"]["retrievalMultiplier"] == 35.0
    assert scoring["sparseVector"]["prefilter"]["minCap"] == 50
    assert scoring["sparseVector"]["prefilter"]["maxCap"] == 250
    assert scoring["sparseVector"]["prefilter"]["limitMultiplier"] == 25
    assert scoring["candidateFilter"]["limitMultiplier"] == 25
    assert scoring["graph"]["adjacencyEdgeRows"]["maxRows"] == 5000
    assert scoring["semantic"]["localRuntime"]["scoreMultiplier"] == 30.0
    assert scoring["semantic"]["localRuntime"]["defaultMaxCandidates"] == 200


def _seed_work_packet_project(project: Path, capsys) -> None:
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(project),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "notes",
        "--project-root", str(project),
        "--title", "Launch Plan",
        "--body", "Launch plan source notes and implementation direction.",
        "--json",
    ]) == 0
    capsys.readouterr()


def test_memory_retrieval_scoring_defaults_are_observable_and_unchanged():
    payload = memory_scoring.retrieval_scoring_config_payload()

    _assert_default_scoring_payload(payload)
    assert payload["confidence"]["points"]["canonical"] == 8.0
    assert payload["lifecycle"]["stale"] == -12.0
    assert payload["sourceTrust"]["typePoints"]["decision"] == 10.0


def _assert_json_early_failure(captured, error_code: str) -> dict[str, object]:
    assert "Error:" not in captured.out
    payload = _assert_single_json_stdout(captured)
    assert set(payload) == {"ok", "error", "message"}
    assert payload["ok"] is False
    assert payload["error"] == error_code
    assert str(payload["message"]).strip()
    return payload


def _assert_json_failure_payload(captured, error_code: str) -> dict[str, object]:
    assert "Error:" not in captured.out
    payload = _assert_single_json_stdout(captured)
    assert {"ok", "error", "message"}.issubset(payload)
    assert payload["ok"] is False
    assert payload["error"] == error_code
    assert str(payload["message"]).strip()
    return payload


class _RecordingConnection:
    def __init__(self) -> None:
        self.row_factory = None
        self.statements: list[str] = []
        self.closed = False

    def execute(self, statement: str):
        self.statements.append(statement)
        return []

    def close(self) -> None:
        self.closed = True


def _schema_marker_snapshot(project: Path) -> dict[str, object]:
    conn = sqlite3.connect(_memory_db_path(project))
    try:
        metadata = conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'",
        ).fetchone()
        schema_rows = tuple(
            int(row[0])
            for row in conn.execute("SELECT version FROM schema_version ORDER BY version")
        )
        tables = tuple(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
            )
        )
        return {
            "userVersion": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "metadataVersion": str(metadata[0]) if metadata else None,
            "schemaRows": schema_rows,
            "tables": tables,
        }
    finally:
        conn.close()


def _make_legacy_adoptable_memory(project: Path) -> None:
    assert cc.cmd_memory_init(project, mode="document-only", project_short="Demo") == 0
    conn = sqlite3.connect(_memory_db_path(project))
    try:
        conn.execute("PRAGMA user_version = 0")
        conn.execute("UPDATE metadata SET value = '4' WHERE key = 'schema_version'")
        conn.execute("DELETE FROM schema_version WHERE version = ?", (cc_memory.SCHEMA_VERSION,))
        conn.commit()
        assert memory_migrations.inspect_schema(conn).state == "legacy_adoptable"
    finally:
        conn.close()


def test_memory_store_connect_configures_sqlite_reliability(tmp_path):
    connection = _RecordingConnection()

    with patch("cc_memory_lib.store.sqlite3.connect", return_value=connection) as connect:
        assert memory_store._connect(tmp_path) is connection

    connect.assert_called_once_with(
        tmp_path / ".controlcoding" / "memory" / "memory.db",
        timeout=memory_store.SQLITE_CONNECT_TIMEOUT_SECONDS,
    )
    assert memory_store.SQLITE_CONNECT_TIMEOUT_SECONDS == 30.0
    assert memory_store.SQLITE_BUSY_TIMEOUT_MS == 30000
    assert connection.row_factory is sqlite3.Row
    assert f"PRAGMA busy_timeout = {memory_store.SQLITE_BUSY_TIMEOUT_MS}" in connection.statements
    assert "PRAGMA journal_mode = WAL" in connection.statements
    assert connection.closed is False


def test_memory_store_readonly_connect_configures_sqlite_reliability_without_wal(tmp_path):
    db_path = tmp_path / ".controlcoding" / "memory" / "memory.db"
    db_path.parent.mkdir(parents=True)
    writer = sqlite3.connect(db_path)
    try:
        writer.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        writer.execute("INSERT INTO marker(value) VALUES ('captured')")
        writer.commit()
    finally:
        writer.close()

    with patch("cc_memory_lib.store.sqlite3.connect", wraps=sqlite3.connect) as connect:
        connection = memory_store._connect_readonly_db(db_path)

    connect.assert_called_once_with(
        ":memory:",
        timeout=memory_store.SQLITE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "captured"
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("INSERT INTO marker(value) VALUES ('forbidden')")
    finally:
        connection.close()
    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()
    assert not Path(f"{db_path}-journal").exists()


def test_memory_bootstrap_readonly_uses_store_sqlite_policy(tmp_path):
    db_path = tmp_path / ".controlcoding" / "memory" / "memory.db"
    db_path.parent.mkdir(parents=True)
    writer = sqlite3.connect(db_path)
    try:
        writer.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        writer.execute("INSERT INTO marker(value) VALUES ('bootstrap')")
        writer.commit()
    finally:
        writer.close()

    with patch("cc_memory_lib.store.sqlite3.connect", wraps=sqlite3.connect) as connect:
        connection = memory_bootstrap._connect_readonly(db_path)

    connect.assert_called_once_with(
        ":memory:",
        timeout=memory_store.SQLITE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "bootstrap"
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("INSERT INTO marker(value) VALUES ('forbidden')")
    finally:
        connection.close()
    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()
    assert not Path(f"{db_path}-journal").exists()


def test_memory_facade_preserves_public_constants():
    assert cc_memory.CONTROL_DIRNAME == ".controlcoding"
    assert cc_memory.MEMORY_DIRNAME == "memory"
    assert cc_memory.DB_FILENAME == "memory.db"
    assert cc_memory.SCHEMA_VERSION == 5
    assert cc_memory.CONTROLCODING_VERSION == "v3.0.2"
    assert "document-only" in cc_memory.VALID_INSTALL_MODES
    assert "active" in cc_memory.VALID_LIFECYCLES
    assert "STATUS.md" in cc_memory.REQUIRED_VIEWS
    assert "GRAPH_INDEX.md" in cc_memory.REQUIRED_VIEWS
    assert "VECTOR_INDEX.md" in cc_memory.REQUIRED_VIEWS
    assert "WORK_HANDOFF.md" in cc_memory.REQUIRED_VIEWS
    assert "SOURCE_LEDGER.md" in cc_memory.REQUIRED_VIEWS
    assert "OPEN_QUESTIONS.md" in cc_memory.REQUIRED_VIEWS
    assert "ACTIVE_DECISIONS.md" in cc_memory.REQUIRED_VIEWS
    assert cc_memory.SEMANTIC_ADAPTER_INTERFACE_VERSION == "cc-semantic-adapter/v1"
    assert cc_memory.EVIDENCE_REF_VERSION == "cc-evidence-ref/v1"
    assert cc_memory.PRIVACY_SCRUB_VERSION == "cc-privacy-scrub/v1"
    assert cc_memory.MEMORY_EVAL_VERSION == "cc-memory-eval/v1"
    assert cc_memory.LOCAL_SPARSE_ADAPTER == "local_sparse_v1"
    assert cc_memory.OCR_ADAPTER_INTERFACE_VERSION == "cc-ocr-adapter/v1"
    assert cc_memory.SESSION_RECORD_SCHEMA_VERSION == 1
    assert "paragraph" in cc_memory.DOCUMENT_LAYOUT_NODE_TYPES
    assert "figure" in cc_memory.DOCUMENT_LAYOUT_NODE_TYPES
    assert "table_cell" in cc_memory.DOCUMENT_LAYOUT_NODE_TYPES
    assert "schema_field" in cc_memory.DOCUMENT_LAYOUT_NODE_TYPES
    assert "represented_by_chunk" in cc_memory.DOCUMENT_LAYOUT_EDGE_TYPES
    assert "caption_for" in cc_memory.DOCUMENT_LAYOUT_EDGE_TYPES
    assert "table_cell_of" in cc_memory.DOCUMENT_LAYOUT_EDGE_TYPES
    assert "schema_field_of" in cc_memory.DOCUMENT_LAYOUT_EDGE_TYPES
    assert "visual_below" in cc_memory.DOCUMENT_LAYOUT_EDGE_TYPES
    assert "plan" in cc_memory.VALID_DOCUMENT_TYPES
    assert "weak_lexical_similarity" in cc_memory.CORRELATION_CONFIDENCE_LEVELS
    assert "note" in cc_memory.ENTITY_TYPE_CODES
    assert "session" in cc_memory.ENTITY_TYPE_CODES
    assert cc_memory.VALID_MEMORY_PROFILES == {"project", "work"}
    assert "continue_previous_work" in cc_memory.VALID_SESSION_MODES
    assert "needs_followup" in cc_memory.VALID_SESSION_STATUSES
    assert "produced_commit" in cc_memory.SESSION_EDGE_TYPES
    assert "continue_previous_work" in cc_memory.STARTUP_INTENTS
    assert "docs/inbox" in cc_memory.WORK_MEMORY_PROFILE_DIRS
    assert cc_memory.CROSS_PLANE_PACKET_TYPE == "controlcoding-cross-plane-graphrag-packet/v1"
    assert callable(cc_memory.cmd_memory_bootstrap)
    assert callable(cc_memory.cmd_memory_cross_pack)
    assert callable(cc_memory.cmd_memory_evidence_list)
    assert callable(cc_memory.cmd_memory_evidence_show)
    assert callable(cc_memory.cmd_memory_eval_run)
    assert callable(cc_memory.cmd_memory_graph_export)
    assert callable(cc_memory.cmd_memory_layout)
    assert callable(cc_memory.cmd_memory_ocr_run)
    assert callable(cc_memory.cmd_memory_ocr_status)
    assert callable(cc_memory.cmd_memory_op_index)
    assert callable(cc_memory.cmd_memory_rag_pack)
    assert callable(cc_memory.cmd_memory_retrieve)
    assert callable(cc_memory.cmd_memory_semantic_status)
    assert callable(cc_memory.cmd_memory_session_start)
    assert callable(cc_memory.cmd_memory_session_close)
    assert callable(cc_memory.cmd_memory_session_views)
    assert callable(cc_memory.cmd_memory_session_pack)
    assert callable(cc_memory.cmd_memory_startup)


def test_product_version_work_plane_pin_and_controlwork_command_scope_are_coherent():
    repo_root = Path(__file__).resolve().parent.parent
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    manifest = json.loads(
        (repo_root / "controlcoding.release.json").read_text(encoding="utf-8")
    )
    memory_notes = (
        repo_root / "docs" / "memory-graphrag-release-notes.md"
    ).read_text(encoding="utf-8")

    assert f'version = "{cc_memory.CONTROLCODING_VERSION.removeprefix("v")}"' in pyproject
    assert cc_memory.SCHEMA_VERSION == 5
    assert manifest["workPlaneContract"] == "controlwork-work-plane/1.0.0"
    assert "Standalone ControlWork checkout only" in memory_notes
    assert "`scripts/cw.py` is not included in ControlCoding V1" in memory_notes
    for embedded_command in (
        "cc.py memory work-graph status",
        "cc.py memory work-graph suggestions",
        "cc.py memory work-retrieve",
        "cc.py memory work-rag-pack",
        "cc.py memory work-views",
    ):
        assert embedded_command in memory_notes


def test_memory_output_writers_reject_paths_outside_project(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    with pytest.raises(ValueError, match="output path must stay inside"):
        rag_pack._write_output(tmp_path, outside, "packet")
    with pytest.raises(ValueError, match="output path must stay inside"):
        cross_plane._write_output(tmp_path, outside, "packet")
    with pytest.raises(ValueError, match="output path must stay inside"):
        memory_export._write_output(tmp_path, outside, "packet")
    with pytest.raises(ValueError, match="output path must stay inside"):
        memory_sessions._resolve_output_path(tmp_path, outside)


def test_memory_facade_stays_thin_public_import_surface():
    facade = Path(__file__).resolve().parent.parent / "scripts" / "cc_memory.py"
    source = facade.read_text(encoding="utf-8")

    assert len(source.splitlines()) < 450
    assert "from cc_memory_lib." in source
    assert "sqlite3.connect" not in source
    assert "CREATE TABLE" not in source
    assert "def cmd_memory_scan(" not in source
    assert "def cmd_memory_retrieve(" not in source
    assert "def cmd_memory_rag_pack(" not in source


def test_work_dashboard_implementation_is_extracted_from_feature_module():
    scripts = Path(__file__).resolve().parent.parent / "scripts" / "cc_memory_lib"
    features = (scripts / "work_features.py").read_text(encoding="utf-8")
    dashboard = (scripts / "work_dashboard.py").read_text(encoding="utf-8")

    assert "def build_dashboard_payload(" in dashboard
    assert "def dashboard_html(" in dashboard
    assert "# ControlWork Static Dashboard" in dashboard
    assert "# ControlWork Static Dashboard" not in features
    assert "return work_dashboard.cmd_dashboard(args)" in features


def test_work_query_implementation_is_extracted_from_feature_module():
    scripts = Path(__file__).resolve().parent.parent / "scripts" / "cc_memory_lib"
    features = (scripts / "work_features.py").read_text(encoding="utf-8")
    query = (scripts / "work_query.py").read_text(encoding="utf-8")

    assert "def build_query_payload(" in query
    assert "def _query_workflow_commands(" in query
    assert "# ControlWork Query Packet:" in query
    assert "# ControlWork Query Packet:" not in features
    assert "return work_query.cmd_query(args)" in features


def test_work_project_map_implementation_is_extracted_from_dashboard_module():
    scripts = Path(__file__).resolve().parent.parent / "scripts" / "cc_memory_lib"
    dashboard = (scripts / "work_dashboard.py").read_text(encoding="utf-8")
    project_map = (scripts / "work_project_map.py").read_text(encoding="utf-8")

    assert "def build_project_map_payload(" in project_map
    assert "def validate_project_map_payload(" in project_map
    assert "controlwork-project-map/v1" in project_map
    assert "work_project_map.build_project_map_payload" in dashboard


def test_work_project_map_session_changes_memory_links_take_precedence(tmp_path):
    changed_target = ".controlwork/memory/outputs/current.md"
    legacy_target = ".controlwork/memory/outputs/legacy.md"
    base_session = {
        "id": "project-map-precedence",
        "topic": "Project Map precedence",
        "links": [
            {"type": "references_entry", "target": ".controlwork/memory/sources/source.md"},
            {"type": "changes_memory", "target": changed_target},
        ],
    }

    for memory_changed in (None, "not-a-list", [legacy_target]):
        session = dict(base_session)
        if memory_changed is not None:
            session["memoryChanged"] = memory_changed

        record = work_project_map._session_record(tmp_path, session)

        assert record["documentsChanged"] == [changed_target]

    fallback_record = work_project_map._session_record(
        tmp_path,
        {
            "id": "project-map-legacy",
            "topic": "Project Map legacy fallback",
            "links": [],
            "memoryChanged": [legacy_target],
        },
    )

    assert fallback_record["documentsChanged"] == [legacy_target]


def test_work_graph_ops_implementation_is_extracted_from_feature_module():
    scripts = Path(__file__).resolve().parent.parent / "scripts" / "cc_memory_lib"
    features = (scripts / "work_features.py").read_text(encoding="utf-8")
    graph_ops = (scripts / "work_graph_ops.py").read_text(encoding="utf-8")

    assert "def build_graph_neighbors_payload(" in graph_ops
    assert "def build_graph_stale_payload(" in graph_ops
    assert "def build_graph_diff_payload(" in graph_ops
    assert "Stale graph output is review evidence" in graph_ops
    assert "Stale graph output is review evidence" not in features
    assert "return work_graph_ops.cmd_graph_neighbors(args)" in features
    assert "return work_graph_ops.cmd_graph_diff(args)" in features


def test_memory_init_document_only_creates_detachable_control_plane(tmp_path):
    (tmp_path / "README.md").write_text("# Demo", encoding="utf-8")

    result = cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo")

    assert result == 0
    control = tmp_path / ".controlcoding"
    manifest = json.loads((control / "module_manifest.json").read_text(encoding="utf-8"))
    assert manifest["install_mode"] == "document_memory_only"
    assert manifest["project_short"] == "Demo"
    assert (control / "memory" / "memory.db").exists()
    assert (control / "logs" / "event_log.jsonl").exists()
    assert (control / "views" / "STATUS.md").exists()
    assert (control / "views" / "WORK_HANDOFF.md").exists()
    assert (control / "views" / "SOURCE_LEDGER.md").exists()
    assert not (tmp_path / "hooks").exists()
    assert not (tmp_path / "tools").exists()
    assert not (tmp_path / "src").exists()
    assert not (tmp_path / "tests").exists()

    tables = {row["name"] for row in _db_rows(tmp_path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "metadata",
        "schema_version",
        "entities",
        "edges",
        "events",
        "sources",
        "views",
        "semantic_chunks",
        "document_layout_nodes",
        "document_layout_edges",
        "correlation_suggestions",
        "derived_vector_index",
        "session_records",
        "session_edges",
    } <= tables
    assert cc.cmd_memory_doctor(tmp_path) == 0

    shutil.rmtree(control)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["README.md"]


def test_memory_init_sets_schema_user_version_and_metadata(tmp_path):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0

    assert _db_scalar(tmp_path, "PRAGMA user_version") == cc_memory.SCHEMA_VERSION
    metadata_version = _db_scalar(
        tmp_path,
        "SELECT value FROM metadata WHERE key = 'schema_version'",
    )
    assert metadata_version == str(cc_memory.SCHEMA_VERSION)
    schema_rows = {
        row["version"]
        for row in _db_rows(tmp_path, "SELECT version FROM schema_version")
    }
    assert cc_memory.SCHEMA_VERSION in schema_rows


def test_memory_schema_current_migration_is_idempotent(tmp_path):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0

    with memory_store._memory_connection(tmp_path) as conn:
        before_tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        memory_store._ensure_schema(conn)
        memory_store._ensure_schema(conn)
        after_tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        inspection = memory_migrations.inspect_schema(conn)

    assert inspection.state == "current"
    assert before_tables == after_tables
    assert _db_scalar(tmp_path, "PRAGMA user_version") == cc_memory.SCHEMA_VERSION


def test_memory_schema_adopts_legacy_current_shape_without_data_loss(tmp_path):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    before_events = _db_scalar(tmp_path, "SELECT COUNT(*) FROM events")

    db_path = tmp_path / ".controlcoding" / "memory" / "memory.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA user_version = 0")
        conn.execute(
            "UPDATE metadata SET value = '4' WHERE key = 'schema_version'",
        )
        conn.execute("DELETE FROM schema_version WHERE version = ?", (cc_memory.SCHEMA_VERSION,))
        conn.commit()
        assert memory_migrations.inspect_schema(conn).state == "legacy_adoptable"
        conn.execute("BEGIN")
        memory_migrations.migrate_schema(conn)
        conn.commit()
        assert memory_migrations.inspect_schema(conn).state == "current"
    finally:
        conn.close()

    assert _db_scalar(tmp_path, "PRAGMA user_version") == cc_memory.SCHEMA_VERSION
    assert _db_scalar(tmp_path, "SELECT value FROM metadata WHERE key = 'schema_version'") == "5"
    assert _db_scalar(tmp_path, "SELECT COUNT(*) FROM events") == before_events


def test_memory_retrieve_payload_does_not_adopt_legacy_queryable_schema(tmp_path):
    _make_legacy_adoptable_memory(tmp_path)
    before = _schema_marker_snapshot(tmp_path)

    payload = memory_retrieve._retrieve_payload(
        tmp_path,
        "legacy retrieval",
        "dev",
        5,
        False,
    )

    assert payload["ok"] is True
    assert payload["available"] is True
    assert payload["degraded"] is False
    assert payload["schema"]["schemaState"] == "legacy_adoptable"
    assert _schema_marker_snapshot(tmp_path) == before


def test_work_start_retrieve_payload_does_not_adopt_legacy_queryable_schema(tmp_path):
    _make_legacy_adoptable_memory(tmp_path)
    before = _schema_marker_snapshot(tmp_path)

    payload = cc._work_start_retrieve_payload(
        tmp_path,
        "legacy retrieval",
        "dev",
        5,
    )

    assert payload["ok"] is True
    assert payload["available"] is False
    assert payload["health"]["state"] == "unknown"
    assert payload["health"]["color"] == "YELLOW"
    assert _schema_marker_snapshot(tmp_path) == before


def test_chat_start_does_not_adopt_legacy_queryable_schema(tmp_path, capsys):
    _make_legacy_adoptable_memory(tmp_path)
    capsys.readouterr()
    before = _schema_marker_snapshot(tmp_path)

    assert _run_main([
        "chat-start",
        "--project-root",
        str(tmp_path),
        "--topic",
        "legacy retrieval",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["workStart"]["opIndex"]["health"]["state"] == "unknown"
    assert payload["workStart"]["retrieval"]["available"] is False
    assert _schema_marker_snapshot(tmp_path) == before


def test_memory_bootstrap_does_not_adopt_legacy_queryable_schema(tmp_path):
    _make_legacy_adoptable_memory(tmp_path)
    before = _schema_marker_snapshot(tmp_path)

    payload = memory_bootstrap._bootstrap_payload(
        tmp_path,
        scope="dev",
        topic="legacy retrieval",
    )

    assert payload["ok"] is True
    assert payload["devPlane"]["initialized"] is True
    assert payload["devPlane"]["schema"]["schemaState"] == "legacy_adoptable"
    assert payload["devPlane"]["schema"]["userVersion"] == before["userVersion"]
    assert _schema_marker_snapshot(tmp_path) == before


def test_memory_schema_migrates_quasi_empty_db_with_stale_marker(tmp_path):
    db_dir = tmp_path / ".controlcoding" / "memory"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "memory.db")
    try:
        conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO metadata(key, value) VALUES ('schema_version', '4')")
        conn.commit()

        conn.execute("BEGIN")
        inspection = memory_migrations.migrate_schema(conn)
        conn.commit()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()

    assert inspection.state == "current"
    assert "entities" in tables
    assert "retrieval_records" in tables
    assert _db_scalar(tmp_path, "PRAGMA user_version") == cc_memory.SCHEMA_VERSION


@pytest.mark.parametrize("legacy_version", [3, 4])
def test_memory_schema_migrates_old_v3_v4_additively(tmp_path, legacy_version):
    db_dir = tmp_path / ".controlcoding" / "memory"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "memory.db")
    try:
        now = "2026-01-01T00:00:00Z"
        memory_migrations._apply_v1(conn, now)
        memory_migrations._apply_v2(conn, now)
        memory_migrations._apply_v3(conn, now)
        if legacy_version >= 4:
            memory_migrations._apply_v4(conn, now)
        conn.execute(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              source_refs, provenance, body, data, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "DOC1",
                "doc_node",
                "Legacy Doc",
                "Demo",
                "dev",
                "docs/legacy.md",
                "active",
                "[]",
                "{}",
                "Legacy body",
                "{}",
                now,
                now,
            ),
        )
        conn.commit()
        assert memory_migrations.inspect_schema(conn).state == "old_compatible"

        conn.execute("BEGIN")
        memory_migrations.migrate_schema(conn)
        conn.commit()
        inspection = memory_migrations.inspect_schema(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        semantic_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(semantic_chunks)")
        }
        entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    finally:
        conn.close()

    assert inspection.state == "current"
    assert "metadata" in semantic_columns
    assert "session_records" in tables
    assert "retrieval_records" in tables
    assert "derived_vector_terms" in tables
    assert entity_count == 1
    assert _db_scalar(tmp_path, "PRAGMA user_version") == cc_memory.SCHEMA_VERSION


def test_memory_schema_partial_and_future_fail_without_mutation(tmp_path):
    partial_path = tmp_path / "partial.db"
    conn = sqlite3.connect(partial_path)
    try:
        conn.execute("CREATE TABLE entities(id TEXT PRIMARY KEY)")
        conn.commit()
        before_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        with pytest.raises(memory_migrations.SchemaMigrationError):
            memory_migrations.migrate_schema(conn)
        after_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert before_tables == after_tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    db_path = tmp_path / ".controlcoding" / "memory" / "memory.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        with pytest.raises(memory_migrations.SchemaMigrationError):
            memory_migrations.migrate_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    finally:
        conn.close()


def test_memory_schema_allows_fts5_unavailable_degraded_mode(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(db_path)
    monkeypatch.setattr(memory_migrations, "_fts5_available", lambda _conn: False)
    try:
        conn.execute("BEGIN")
        inspection = memory_migrations.migrate_schema(conn)
        conn.commit()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()

    assert inspection.state == "current"
    assert "retrieval_records" in tables
    assert "retrieval_fts" not in tables


def test_memory_counts_by_allows_type_and_lifecycle():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE entities(type TEXT NOT NULL, lifecycle TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO entities(type, lifecycle) VALUES (?, ?)",
            [
                ("doc_node", "active"),
                ("doc_node", "needs_review"),
                ("plan", "active"),
            ],
        )

        assert memory_impact._counts_by(conn, "type") == {"doc_node": 2, "plan": 1}
        assert memory_impact._counts_by(conn, "lifecycle") == {"active": 2, "needs_review": 1}
    finally:
        conn.close()


def test_memory_counts_by_rejects_unallowlisted_field_before_execute():
    class SpyConnection:
        def __init__(self):
            self.execute_called = False

        def execute(self, *_args, **_kwargs):
            self.execute_called = True
            raise AssertionError("execute must not be called for an unsupported count field")

    conn = SpyConnection()

    with pytest.raises(ValueError, match="Unsupported count field"):
        memory_impact._counts_by(conn, "type; DROP TABLE entities")

    assert conn.execute_called is False


def test_memory_doctor_status_bootstrap_report_schema_read_only(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()

    assert _run_main(["memory", "doctor", "--project-root", str(tmp_path), "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["schema"]["schemaState"] == "current"
    assert doctor["schema"]["userVersion"] == cc_memory.SCHEMA_VERSION
    assert doctor["schema"]["metadataVersion"] == cc_memory.SCHEMA_VERSION
    assert doctor["schema"]["targetVersion"] == cc_memory.SCHEMA_VERSION

    assert _run_main(["memory", "status", "--project-root", str(tmp_path), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["health"]["state"] == "fresh"
    assert status["health"]["reason"] == ""

    assert _run_main(["memory", "bootstrap", "--project-root", str(tmp_path), "--json"]) == 0
    bootstrap = json.loads(capsys.readouterr().out)
    assert bootstrap["devPlane"]["schema"]["schemaState"] == "current"
    assert bootstrap["devPlane"]["schema"]["targetVersion"] == cc_memory.SCHEMA_VERSION

    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    try:
        conn.execute("DROP TABLE session_records")
        conn.commit()
    finally:
        conn.close()

    assert _run_main(["memory", "doctor", "--project-root", str(tmp_path), "--json"]) == 1
    partial_doctor = json.loads(capsys.readouterr().out)
    assert partial_doctor["schema"]["schemaState"] == "partial"

    assert _run_main(["memory", "status", "--project-root", str(tmp_path), "--json"]) == 0
    partial_status = json.loads(capsys.readouterr().out)
    assert partial_status["health"]["state"] == "unknown"
    assert partial_status["health"]["reason"] == "projection_outdated"

    assert _run_main(["memory", "bootstrap", "--project-root", str(tmp_path), "--json"]) == 0
    partial_bootstrap = json.loads(capsys.readouterr().out)
    assert partial_bootstrap["devPlane"]["schema"]["schemaState"] == "partial"
    assert not _db_rows(
        tmp_path,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'session_records'",
    )


def test_memory_bootstrap_readonly_does_not_call_schema_repair(tmp_path, monkeypatch):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0

    def fail_schema_repair(*_args, **_kwargs):
        raise AssertionError("bootstrap must not repair or migrate schema")

    monkeypatch.setattr(memory_store, "_ensure_schema", fail_schema_repair)
    monkeypatch.setattr(memory_migrations, "migrate_schema", fail_schema_repair)

    payload = memory_bootstrap._bootstrap_payload(
        tmp_path,
        scope="dev",
        topic="read-only schema inspection",
    )

    assert payload["ok"] is True
    assert payload["devPlane"]["schema"]["schemaState"] == "current"


def test_memory_status_preserves_type_and_lifecycle_count_fields(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    now = "2026-01-01T00:00:00Z"
    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    try:
        conn.executemany(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              source_refs, provenance, body, data, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "DOC1",
                    "doc_node",
                    "Status Doc",
                    "Demo",
                    "controlcoding_dev",
                    "docs/status.md",
                    "active",
                    "[]",
                    "{}",
                    "Status body",
                    "{}",
                    now,
                    now,
                ),
                (
                    "PLAN1",
                    "plan",
                    "Status Plan",
                    "Demo",
                    "controlcoding_dev",
                    "docs/plan.md",
                    "needs_review",
                    "[]",
                    "{}",
                    "Plan body",
                    "{}",
                    now,
                    now,
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert _run_main(["memory", "status", "--project-root", str(tmp_path), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["health"]["state"] == "unknown"
    assert status["health"]["reason"] == "projection_outdated"
    assert status.get("byType") is None
    assert status.get("byLifecycle") is None


def test_memory_doctor_status_bootstrap_report_future_schema_without_mutation(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    try:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
    finally:
        conn.close()

    assert _run_main(["memory", "doctor", "--project-root", str(tmp_path), "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["schema"]["schemaState"] == "future"
    assert doctor["schema"]["userVersion"] == 99

    assert _run_main(["memory", "status", "--project-root", str(tmp_path), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["health"]["state"] == "unknown"
    assert status["health"]["reason"] == "projection_outdated"

    assert _run_main(["memory", "bootstrap", "--project-root", str(tmp_path), "--json"]) == 0
    bootstrap = json.loads(capsys.readouterr().out)
    assert bootstrap["devPlane"]["schema"]["schemaState"] == "future"
    assert _db_scalar(tmp_path, "PRAGMA user_version") == 99


def test_memory_retrieve_degrades_partial_and_future_schema_without_mutation(tmp_path):
    partial_project = tmp_path / "partial"
    partial_project.mkdir()
    assert cc.cmd_memory_init(partial_project, mode="document-only", project_short="Demo") == 0
    conn = sqlite3.connect(_memory_db_path(partial_project))
    try:
        conn.execute("DROP TABLE session_records")
        conn.commit()
        assert memory_migrations.inspect_schema(conn).state == "partial"
    finally:
        conn.close()
    partial_before = _schema_marker_snapshot(partial_project)

    partial_payload = memory_retrieve._retrieve_payload(
        partial_project,
        "partial retrieval",
        "dev",
        5,
        False,
    )

    assert partial_payload["ok"] is True
    assert partial_payload["available"] is False
    assert partial_payload["degraded"] is True
    assert partial_payload["matches"] == []
    assert partial_payload["schema"]["schemaState"] == "partial"
    assert partial_payload["warnings"][0]["code"] == "schema_not_queryable"
    assert "schema_not_queryable" in partial_payload["message"]
    assert _schema_marker_snapshot(partial_project) == partial_before

    future_project = tmp_path / "future"
    future_project.mkdir()
    assert cc.cmd_memory_init(future_project, mode="document-only", project_short="Demo") == 0
    conn = sqlite3.connect(_memory_db_path(future_project))
    try:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        assert memory_migrations.inspect_schema(conn).state == "future"
    finally:
        conn.close()
    future_before = _schema_marker_snapshot(future_project)

    future_payload = memory_retrieve._retrieve_payload(
        future_project,
        "future retrieval",
        "dev",
        5,
        False,
    )

    assert future_payload["ok"] is True
    assert future_payload["available"] is False
    assert future_payload["degraded"] is True
    assert future_payload["matches"] == []
    assert future_payload["schema"]["schemaState"] == "future"
    assert future_payload["warnings"][0]["code"] == "schema_future"
    assert "schema_future" in future_payload["message"]
    assert _schema_marker_snapshot(future_project) == future_before


def test_memory_db_unreadable_classifier_does_not_match_every_sqlite_error():
    assert memory_store.is_memory_db_unreadable_error(
        sqlite3.DatabaseError("file is not a database")
    )
    assert memory_store.is_memory_db_unreadable_error(
        sqlite3.DatabaseError("database disk image is malformed")
    )
    assert memory_store.is_memory_db_unreadable_error(
        sqlite3.OperationalError("unable to open database file")
    )
    assert not memory_store.is_memory_db_unreadable_error(
        sqlite3.IntegrityError("UNIQUE constraint failed: metadata.key")
    )
    assert not memory_store.is_memory_db_unreadable_error(
        sqlite3.OperationalError("database is locked")
    )


def test_memory_bootstrap_locked_db_degrades_with_actionable_sqlite_warning_without_repair(
    tmp_path,
    capsys,
    monkeypatch,
):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    before = _schema_marker_snapshot(tmp_path)

    def locked_connect(_db_path):
        raise sqlite3.OperationalError("database is locked")

    def fail_schema_repair(*_args, **_kwargs):
        raise AssertionError("bootstrap must not repair or migrate schema")

    monkeypatch.setattr(memory_bootstrap, "_connect_readonly", locked_connect)
    monkeypatch.setattr(memory_store, "_ensure_schema", fail_schema_repair)
    monkeypatch.setattr(memory_migrations, "migrate_schema", fail_schema_repair)

    assert _run_main([
        "memory",
        "bootstrap",
        "--project-root",
        str(tmp_path),
        "--scope",
        "dev",
        "--topic",
        "locked sqlite",
        "--json",
    ]) == 0
    captured = capsys.readouterr()

    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)
    database_error = payload["devPlane"]["databaseError"]
    warning_text = "\n".join(str(warning) for warning in payload["warnings"])

    assert payload["ok"] is True
    assert payload["devPlane"]["initialized"] is False
    assert payload["devPlane"]["schema"]["schemaState"] == "sqlite_error"
    assert database_error["code"] == "memory_db_sqlite_error"
    assert database_error["degraded"] is True
    assert "database is locked" in database_error["sqliteError"]
    assert "retry after the concurrent writer finishes" in database_error["manualAction"]
    assert "memory_db_unreadable" not in warning_text
    assert "memory_db_sqlite_error" in warning_text
    assert "database is locked" in warning_text
    assert "python scripts/cc.py memory doctor --project-root ." in warning_text
    assert _schema_marker_snapshot(tmp_path) == before


def test_memory_scan_locked_db_returns_actionable_json_without_repair(
    tmp_path,
    capsys,
    monkeypatch,
):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    db_path = _memory_db_path(tmp_path)
    before_bytes = db_path.read_bytes()
    before_hash = _file_sha256(db_path)

    def locked_connection(_project):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(memory_commands, "_memory_connection", locked_connection)

    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)

    _assert_actionable_memory_db_sqlite_error(payload, db_path, "memory_scan")
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_retrieve_locked_db_degrades_without_traceback(
    tmp_path,
    capsys,
    monkeypatch,
):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    db_path = _memory_db_path(tmp_path)
    before_bytes = db_path.read_bytes()
    before_hash = _file_sha256(db_path)

    def locked_connection(_project):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(memory_retrieve, "_readonly_memory_connection", locked_connection)

    direct_payload = memory_retrieve._retrieve_payload(
        tmp_path,
        "locked retrieval",
        "dev",
        5,
        False,
    )
    assert direct_payload["ok"] is True
    assert direct_payload["available"] is False
    assert direct_payload["degraded"] is True
    assert direct_payload["code"] == "memory_db_sqlite_error"
    assert direct_payload["warnings"][0]["code"] == "memory_db_sqlite_error"
    assert direct_payload["warnings"][0]["sqliteError"] == "database is locked"
    assert direct_payload["vectorIndex"]["fallbackReason"] == "memory_db_sqlite_error"

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "locked retrieval",
        "--json",
    ]) == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    routed_payload = json.loads(captured.out)
    assert routed_payload["ok"] is True
    assert routed_payload["available"] is False
    assert routed_payload["degraded"] is True
    assert routed_payload["code"] == "memory_db_sqlite_error"
    assert routed_payload["warnings"][0]["sqliteError"] == "database is locked"
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_vector_rebuild_locked_db_returns_sqlite_error(
    tmp_path,
    capsys,
    monkeypatch,
):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    db_path = _memory_db_path(tmp_path)
    before_bytes = db_path.read_bytes()
    before_hash = _file_sha256(db_path)

    def locked_connection(_project):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(memory_vector, "_memory_connection", locked_connection)

    assert _run_main([
        "memory",
        "vector",
        "rebuild",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)

    _assert_actionable_memory_db_sqlite_error(payload, db_path, "memory_vector_rebuild")
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_vector_search_locked_db_returns_sqlite_error(
    tmp_path,
    capsys,
    monkeypatch,
):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    db_path = _memory_db_path(tmp_path)
    before_bytes = db_path.read_bytes()
    before_hash = _file_sha256(db_path)

    def locked_connection(_project):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(memory_vector, "_memory_connection", locked_connection)

    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "locked retrieval",
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)

    _assert_actionable_memory_db_sqlite_error(payload, db_path, "memory_vector_search")
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_scan_corrupt_db_returns_actionable_json_without_auto_repair(tmp_path, capsys):
    db_path, before_bytes, before_hash = _prepare_corrupt_memory_db(tmp_path)
    capsys.readouterr()

    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)

    _assert_actionable_memory_db_error(payload, db_path, "memory_scan")
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_init_work_profile_json_validation_error_is_single_json(tmp_path, capsys):
    assert _run_main([
        "memory",
        "init",
        "--project-root",
        str(tmp_path),
        "--profile",
        "work",
        "--json",
    ]) == 1

    payload = _assert_json_early_failure(
        capsys.readouterr(),
        "memory_init_work_profile_requires_document_only",
    )
    assert "document-only" in str(payload["message"])
    assert not (tmp_path / ".controlcoding").exists()


def test_memory_scan_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 1

    payload = _assert_json_early_failure(capsys.readouterr(), "memory_scan_memory_not_initialized")
    assert "Memory manifest is missing" in str(payload["message"])


def test_memory_scan_json_invalid_scope_via_cli_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
        "--scope",
        "invalid",
        "--json",
    ]) == 2

    payload = _assert_json_early_failure(capsys.readouterr(), "memory_scan_invalid_scope")
    assert "invalid" in str(payload["message"])


def test_memory_scan_non_json_missing_init_keeps_human_error(tmp_path, capsys):
    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
    ]) == 1

    captured = capsys.readouterr()
    assert captured.out.startswith("Error: Memory manifest is missing. Run: cc memory init")
    assert not captured.out.lstrip().startswith("{")
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_memory_impact_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "impact",
        "--project-root",
        str(tmp_path),
        "missing init",
        "--json",
    ]) == 1

    payload = _assert_json_early_failure(capsys.readouterr(), "memory_impact_memory_not_initialized")
    assert "Memory manifest is missing" in str(payload["message"])


def test_memory_context_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "context",
        "--project-root",
        str(tmp_path),
        "missing init",
        "--json",
    ]) == 1

    payload = _assert_json_early_failure(capsys.readouterr(), "memory_context_memory_not_initialized")
    assert "Memory manifest is missing" in str(payload["message"])


@pytest.mark.parametrize(
    ("args", "error_code"),
    [
        (["memory", "evidence", "show", "ccref-demo"], "memory_evidence_memory_not_initialized"),
        (["memory", "evidence", "list"], "memory_evidence_memory_not_initialized"),
        (["memory", "session", "list"], "memory_session_memory_not_initialized"),
        (["memory", "session", "start", "--topic", "Launch"], "memory_session_memory_not_initialized"),
        (["memory", "session", "views"], "memory_session_memory_not_initialized"),
        (["memory", "session-pack"], "memory_session_memory_not_initialized"),
        (["memory", "graph", "status"], "memory_graph_memory_not_initialized"),
        (["memory", "graph", "suggestions"], "memory_graph_memory_not_initialized"),
        (["memory", "graph", "accept", "suggestion-1"], "memory_graph_memory_not_initialized"),
        (["memory", "graph", "around", "Launch"], "memory_graph_memory_not_initialized"),
        (["memory", "graph", "export"], "memory_graph_export_memory_not_initialized"),
        (["memory", "cross-pack", "launch plan"], "memory_cross_pack_memory_not_initialized"),
        (["memory", "semantic", "status"], "memory_semantic_memory_not_initialized"),
        (["memory", "ocr", "status"], "memory_ocr_memory_not_initialized"),
        (["memory", "ocr", "run", "docs/missing.pdf"], "memory_ocr_memory_not_initialized"),
        (["memory", "chunks", "docs/memory.md"], "memory_chunks_memory_not_initialized"),
        (["memory", "layout", "docs/memory.md"], "memory_layout_memory_not_initialized"),
        (["memory", "views", "generate"], "memory_views_memory_not_initialized"),
        (["memory", "sync-report"], "memory_sync_report_memory_not_initialized"),
        (["memory", "intake", "add", "Launch note"], "memory_intake_memory_not_initialized"),
        (["memory", "lifecycle", "mark", "Launch", "--state", "active"], "memory_lifecycle_memory_not_initialized"),
        (["memory", "note", "add", "Launch note"], "memory_note_memory_not_initialized"),
        (["memory", "idea", "add", "Launch idea"], "memory_idea_memory_not_initialized"),
        (["memory", "decision", "add", "Launch decision"], "memory_decision_memory_not_initialized"),
        (["memory", "consult", "record"], "memory_consult_memory_not_initialized"),
        (["memory", "agent-run", "record"], "memory_agent_run_memory_not_initialized"),
    ],
)
def test_memory_residual_json_missing_init_returns_failure_object(tmp_path, capsys, args, error_code):
    command = [*args, "--project-root", str(tmp_path), "--json"]

    assert _run_main(command) == 1

    payload = _assert_json_failure_payload(capsys.readouterr(), error_code)
    assert "message" in payload


@pytest.mark.parametrize(
    ("args", "error_code"),
    [
        (["memory", "evidence", "show", ""], "evidence_selector_required"),
        (["memory", "session", "start", "--topic", ""], "session_topic_required"),
        (["memory", "session", "show", "missing-session"], "session_not_found"),
        (["memory", "session", "link", "missing-session", "--type", "references_session", "--target", ""], "session_link_target_required"),
        (["memory", "graph", "accept", ""], "graph_suggestion_id_required"),
        (["memory", "graph", "around", "missing-entity"], "graph_around_selector_not_found"),
        (["memory", "cross-pack", ""], "cross_pack_query_required"),
        (["memory", "ocr", "run", "docs/missing.pdf"], "ocr_source_not_found"),
        (["memory", "intake", "add", ""], "intake_title_required"),
        (["memory", "lifecycle", "mark", "missing-entity", "--state", "active"], "lifecycle_entity_not_found"),
        (["memory", "consult", "record"], "consult_record_summary_required"),
        (["memory", "agent-run", "record"], "agent_run_summary_required"),
    ],
)
def test_memory_residual_json_validation_errors_are_failure_objects(tmp_path, capsys, args, error_code):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    command = [*args, "--project-root", str(tmp_path), "--json"]

    assert _run_main(command) == 1

    payload = _assert_json_failure_payload(capsys.readouterr(), error_code)
    assert "message" in payload


@pytest.mark.parametrize(
    ("command", "operation"),
    [
        ("impact", "memory_impact"),
        ("context", "memory_context"),
    ],
)
def test_memory_impact_context_corrupt_db_returns_actionable_json_without_auto_repair(
    tmp_path,
    capsys,
    command,
    operation,
):
    db_path, before_bytes, before_hash = _prepare_corrupt_memory_db(tmp_path)
    capsys.readouterr()

    assert _run_main([
        "memory",
        command,
        "--project-root",
        str(tmp_path),
        "corrupt lookup",
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Error:" not in captured.out
    payload = _assert_single_json_stdout(captured)

    _assert_actionable_memory_db_error(payload, db_path, operation)
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_retrieve_corrupt_db_degrades_json_and_payload_without_auto_repair(tmp_path, capsys):
    db_path, before_bytes, before_hash = _prepare_corrupt_memory_db(tmp_path)
    capsys.readouterr()

    direct_payload = memory_retrieve._retrieve_payload(
        tmp_path,
        "corrupt retrieval",
        "dev",
        5,
        False,
    )

    assert direct_payload["ok"] is True
    assert direct_payload["available"] is False
    assert direct_payload["degraded"] is True
    assert direct_payload["code"] == "memory_db_unreadable"
    assert direct_payload["matches"] == []
    assert direct_payload["warnings"][0]["code"] == "memory_db_unreadable"
    assert direct_payload["warnings"][0]["databasePath"] == str(db_path)
    assert direct_payload["warnings"][0]["diagnosticCommand"] == "python scripts/cc.py memory doctor --project-root ."
    assert direct_payload["vectorIndex"]["fallbackReason"] == "memory_db_unreadable"
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "corrupt retrieval",
        "--json",
    ]) == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    routed_payload = json.loads(captured.out)
    assert routed_payload["ok"] is True
    assert routed_payload["available"] is False
    assert routed_payload["degraded"] is True
    assert routed_payload["code"] == "memory_db_unreadable"
    assert routed_payload["warnings"][0]["manualAction"] == direct_payload["warnings"][0]["manualAction"]
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_retrieve_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "missing init",
        "--json",
    ]) == 1

    payload = _assert_json_early_failure(capsys.readouterr(), "memory_not_initialized")
    assert "Memory manifest is missing" in str(payload["message"])


def test_memory_retrieve_json_blank_query_returns_failure_object(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "   ",
        "--json",
    ]) == 1

    _assert_json_early_failure(capsys.readouterr(), "retrieve_query_required")


def test_memory_retrieve_non_json_missing_init_keeps_human_error(tmp_path, capsys):
    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "missing init",
    ]) == 1

    captured = capsys.readouterr()
    assert "Error: Memory manifest is missing. Run: cc memory init" in captured.out


def test_memory_vector_rebuild_corrupt_db_returns_actionable_json_without_auto_repair(tmp_path, capsys):
    db_path, before_bytes, before_hash = _prepare_corrupt_memory_db(tmp_path)
    capsys.readouterr()

    assert _run_main([
        "memory",
        "vector",
        "rebuild",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)

    _assert_actionable_memory_db_error(payload, db_path, "memory_vector_rebuild")
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_vector_rebuild_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "vector",
        "rebuild",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 1

    _assert_json_early_failure(capsys.readouterr(), "vector_memory_not_initialized")


def test_memory_vector_search_corrupt_db_returns_actionable_json_without_auto_repair(tmp_path, capsys):
    db_path, before_bytes, before_hash = _prepare_corrupt_memory_db(tmp_path)
    capsys.readouterr()

    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "corrupt retrieval",
        "--json",
    ]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)

    _assert_actionable_memory_db_error(payload, db_path, "memory_vector_search")
    _assert_db_bytes_unchanged(db_path, before_bytes, before_hash)


def test_memory_vector_search_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "missing init",
        "--json",
    ]) == 1

    _assert_json_early_failure(capsys.readouterr(), "vector_memory_not_initialized")


def test_memory_rag_pack_json_missing_init_returns_failure_object(tmp_path, capsys):
    assert _run_main([
        "memory",
        "rag-pack",
        "--project-root",
        str(tmp_path),
        "missing init",
        "--json",
    ]) == 1

    payload = _assert_json_early_failure(capsys.readouterr(), "rag_pack_memory_not_initialized")
    assert "Memory manifest is missing" in str(payload["message"])


def test_memory_rag_pack_json_blank_query_returns_failure_object(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "rag-pack",
        "--project-root",
        str(tmp_path),
        "   ",
        "--json",
    ]) == 1

    _assert_json_early_failure(capsys.readouterr(), "rag_pack_query_required")


def test_memory_scan_governed_scope_indexes_only_governed_surfaces(tmp_path):
    (tmp_path / "CONTROLCODING.md").write_text("# Control\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Public readme\n", encoding="utf-8")
    (tmp_path / "project-definition").mkdir()
    (tmp_path / "project-definition" / "brief.md").write_text("# Brief\n", encoding="utf-8")
    (tmp_path / "dev" / "design").mkdir(parents=True)
    (tmp_path / "dev" / "design" / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (tmp_path / ".controlwork" / "memory" / "notes").mkdir(parents=True)
    (tmp_path / ".controlwork" / "memory" / "notes" / "note.md").write_text("# Note\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('no scan')\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text("# Requirements\n", encoding="utf-8")

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path, scope="governed") == 0

    paths = {
        row["path"]
        for row in _db_rows(
            tmp_path,
            "SELECT path FROM entities WHERE path IS NOT NULL AND path != ''",
        )
    }
    assert {
        "CONTROLCODING.md",
        "AGENTS.md",
        "project-definition/brief.md",
        "dev/design/architecture.md",
        ".controlwork/memory/notes/note.md",
    } <= paths
    assert "README.md" not in paths
    assert "src/app.py" not in paths
    assert "docs/requirements.md" not in paths

    metadata = {
        row["key"]: row["value"]
        for row in _db_rows(tmp_path, "SELECT key, value FROM metadata")
    }
    assert metadata["last_scan_scope"] == "governed"


def test_memory_init_work_profile_creates_non_code_workspace(tmp_path):
    result = cc.cmd_memory_init(
        tmp_path,
        mode="document-only",
        project_short="Work",
        profile="work",
    )

    assert result == 0
    manifest = json.loads((tmp_path / ".controlcoding" / "module_manifest.json").read_text(encoding="utf-8"))
    assert manifest["install_mode"] == "document_memory_only"
    assert manifest["profile"] == "work"
    assert manifest["work_memory_layout"] == cc_memory.WORK_MEMORY_PROFILE_DIRS
    for relative in cc_memory.WORK_MEMORY_PROFILE_DIRS:
        assert (tmp_path / relative).is_dir()
    assert not (tmp_path / "hooks").exists()
    assert not (tmp_path / "tools").exists()
    assert not (tmp_path / "src").exists()
    assert not (tmp_path / "tests").exists()


def test_memory_init_work_profile_requires_document_only(tmp_path):
    result = cc.cmd_memory_init(tmp_path, mode="full", profile="work")

    assert result == 1
    assert not (tmp_path / ".controlcoding").exists()


def test_memory_work_init_creates_embedded_controlwork_project_plane(tmp_path):
    assert cc.cmd_memory_work_init(
        tmp_path,
        project_name="SGC",
        purpose="Compliance work memory",
        json_output=True,
    ) == 0

    controlwork = tmp_path / ".controlwork"
    config = json.loads((controlwork / "config.json").read_text(encoding="utf-8"))
    assert config["distribution"] == "embedded_controlcoding"
    assert config["standaloneCompatible"] is True
    assert config["canonicalContext"] == "CONTROLWORK.md"
    assert config["ownerProduct"] == "ControlCoding"
    assert (tmp_path / "CONTROLWORK.md").exists()
    assert "Embedded ControlWork project plane" in (tmp_path / "CONTROLWORK.md").read_text(encoding="utf-8")
    assert not (tmp_path / "AGENTS.md").exists()

    for area in config["memory"]["areas"]:
        assert (controlwork / "memory" / area / ".gitkeep").exists()
    assert config["features"]["categories"] is True
    assert (controlwork / "categories.json").exists()
    assert (controlwork / "checkpoints" / ".gitkeep").exists()
    assert (controlwork / "proposals" / ".gitkeep").exists()
    assert (controlwork / "ingestion" / "project-understanding.json").exists()
    understanding = json.loads((controlwork / "ingestion" / "project-understanding.json").read_text(encoding="utf-8"))
    assert understanding["status"] == "needs_human_confirmation"
    assert understanding["blockedUntilApproval"]

    assert cc.cmd_memory_work_status(tmp_path, json_output=True) == 0
    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        source="CONTROLWORK.md",
        force=True,
        quiet=True,
    ) == 0
    payload = cc._context_sync_payload(tmp_path, host="codex_cli", source="CONTROLWORK.md")
    assert payload["ok"] is True
    assert payload["hosts"][0]["warnings"] == []


def test_main_routes_memory_work_init_and_status(tmp_path):
    assert _run_main([
        "memory",
        "work-init",
        "--project-root", str(tmp_path),
        "--name", "SGC",
        "--purpose", "Compliance work memory",
        "--json",
    ]) == 0
    assert (tmp_path / "CONTROLWORK.md").exists()

    assert _run_main([
        "memory",
        "work-status",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0


def test_main_routes_memory_work_quickstart_dry_run(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--dry-run",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["dryRun"] is True
    assert "CONTROLWORK.md" in payload["wouldWriteOrRefresh"]
    assert "PROJECT.md" in payload["wouldWriteOrRefresh"]
    assert ".controlwork/ingestion/project-understanding.json" in payload["wouldWriteOrRefresh"]
    assert payload["externalCalls"] == {
        "aiCalls": False,
        "networkCalls": False,
        "subprocessCalls": False,
    }
    assert not (tmp_path / "CONTROLWORK.md").exists()
    assert not (tmp_path / "PROJECT.md").exists()
    assert not (tmp_path / ".controlwork").exists()


def test_memory_work_quickstart_write_mode_is_idempotent(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "brief.md").write_text("# Brief\n\nLaunch plan and source notes.\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Existing ControlCoding adapter.\n", encoding="utf-8")

    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    first = json.loads(capsys.readouterr().out)
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["contextPacket"] == second["contextPacket"]
    assert (tmp_path / "CONTROLWORK.md").exists()
    assert (tmp_path / ".controlwork" / "ingestion" / "file-index.json").exists()
    assert (tmp_path / ".controlwork" / "ingestion" / "scan-analysis.json").exists()
    assert (tmp_path / ".controlwork" / "ingestion" / "project-understanding.json").exists()
    assert first["projectUnderstanding"]["status"] == "needs_human_confirmation"
    assert first["projectUnderstanding"]["questions"]
    assert (tmp_path / ".controlwork" / "memory" / "views" / "index.md").exists()
    assert (tmp_path / ".controlwork" / "context-packets" / "quickstart-general-launch-plan.md").exists()
    assert "Existing ControlCoding adapter." in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert not second["staleViewWarnings"]


def test_memory_work_quickstart_has_no_hidden_external_calls(tmp_path, capsys):
    with patch("subprocess.run") as run:
        assert _run_main([
            "memory",
            "work-quickstart",
            "--project-root", str(tmp_path),
            "--json",
        ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert not run.called
    assert payload["externalCalls"] == {
        "aiCalls": False,
        "networkCalls": False,
        "subprocessCalls": False,
    }


def test_memory_work_query_composes_query_first_surface(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "notes",
        "--project-root", str(tmp_path),
        "--title", "Launch Plan",
        "--body", "Launch plan source notes and implementation direction.",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-query",
        "launch plan",
        "--project-root", str(tmp_path),
        "--path-to", "CONTROLWORK.md",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["queryWorkflow"]["surface"] == "embedded"
    assert payload["topMatches"]
    assert payload["topMatches"][0]["path"].startswith(".controlwork/memory/notes/")
    assert payload["queryWorkflow"]["externalCalls"] == {
        "aiCalls": False,
        "networkCalls": False,
        "subprocessCalls": False,
    }
    assert any("work-retrieve" in item for item in payload["queryWorkflow"]["commands"])
    assert any("work-rag-pack" in item for item in payload["queryWorkflow"]["commands"])
    assert any("work-graph explain" in item for item in payload["queryWorkflow"]["commands"])
    assert "ControlWork Query Packet: launch plan" in payload["packetMarkdown"]


def test_memory_work_query_json_cli_is_accepted(tmp_path, capsys):
    _seed_work_packet_project(tmp_path, capsys)

    assert _run_main([
        "memory",
        "work-query",
        "launch plan",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0
    payload = _assert_single_json_stdout(capsys.readouterr())
    assert payload["ok"] is True
    assert payload["queryWorkflow"]["surface"] == "embedded"


def test_memory_work_query_stdout_json_is_pure_json(tmp_path, capsys):
    _seed_work_packet_project(tmp_path, capsys)

    assert _run_main([
        "memory",
        "work-query",
        "launch plan",
        "--project-root", str(tmp_path),
        "--stdout",
        "--json",
    ]) == 0
    payload = _assert_single_json_stdout(capsys.readouterr())
    assert payload["ok"] is True
    assert "ControlWork Query Packet: launch plan" in payload["packetMarkdown"]

    assert _run_main([
        "memory",
        "work-query",
        "launch plan",
        "--project-root", str(tmp_path),
        "--stdout",
    ]) == 0
    _assert_plain_text_stdout(capsys.readouterr(), "# ControlWork Query Packet: launch plan")


def test_memory_work_rag_pack_json_cli_is_accepted(tmp_path, capsys):
    _seed_work_packet_project(tmp_path, capsys)

    assert _run_main([
        "memory",
        "work-rag-pack",
        "launch plan",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0
    payload = _assert_single_json_stdout(capsys.readouterr())
    assert payload["ok"] is True
    assert "ControlWork GraphRAG Packet: launch plan" in payload["packetMarkdown"]


def test_memory_work_rag_pack_stdout_json_is_pure_json(tmp_path, capsys):
    _seed_work_packet_project(tmp_path, capsys)

    assert _run_main([
        "memory",
        "work-rag-pack",
        "launch plan",
        "--project-root", str(tmp_path),
        "--stdout",
        "--json",
    ]) == 0
    payload = _assert_single_json_stdout(capsys.readouterr())
    assert payload["ok"] is True
    assert "ControlWork GraphRAG Packet: launch plan" in payload["packetMarkdown"]

    assert _run_main([
        "memory",
        "work-rag-pack",
        "launch plan",
        "--project-root", str(tmp_path),
        "--stdout",
    ]) == 0
    _assert_plain_text_stdout(capsys.readouterr(), "# ControlWork GraphRAG Packet: launch plan")


def test_memory_work_context_pack_stdout_json_is_pure_json(tmp_path, capsys):
    _seed_work_packet_project(tmp_path, capsys)

    assert _run_main([
        "memory",
        "work-context-pack",
        "--project-root", str(tmp_path),
        "--scope", "research",
        "--topic", "launch plan",
        "--stdout",
        "--json",
    ]) == 0
    payload = _assert_single_json_stdout(capsys.readouterr())
    assert payload["ok"] is True
    assert "ControlWork Context Packet: launch plan" in payload["packetMarkdown"]

    assert _run_main([
        "memory",
        "work-context-pack",
        "--project-root", str(tmp_path),
        "--scope", "research",
        "--topic", "launch plan",
        "--stdout",
    ]) == 0
    _assert_plain_text_stdout(capsys.readouterr(), "# ControlWork Context Packet: launch plan")


def test_memory_work_graph_practical_commands(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-graph",
        "export",
        "--project-root", str(tmp_path),
        "--output", ".controlwork/graph-baseline.json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "notes",
        "--project-root", str(tmp_path),
        "--title", "Launch Plan",
        "--body", "Launch plan source notes and implementation direction.",
        "--json",
    ]) == 0
    note = json.loads(capsys.readouterr().out)
    assert _run_main([
        "memory",
        "work-capture",
        "legacy",
        "--project-root", str(tmp_path),
        "--title", "Legacy Launch Plan",
        "--body", "Old launch plan material.",
        "--lifecycle", "legacy",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "notes",
        "--project-root", str(tmp_path),
        "--title", "Launch Open Question?",
        "--body", "What launch evidence still needs review?",
        "--lifecycle", "needs_review",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-graph",
        "neighbors",
        note["path"],
        "--project-root", str(tmp_path),
        "--include-suggestions",
    ]) == 0
    neighbors = json.loads(capsys.readouterr().out)
    assert neighbors["ok"] is True
    assert neighbors["neighborCount"] >= 1

    assert _run_main(["memory", "work-graph", "stale", "--project-root", str(tmp_path)]) == 0
    stale = json.loads(capsys.readouterr().out)
    assert stale["staleNodeCount"] >= 1
    assert stale["staleNodes"][0]["node"]["lifecycle"] == "legacy"

    assert _run_main(["memory", "work-graph", "unresolved", "--project-root", str(tmp_path)]) == 0
    unresolved = json.loads(capsys.readouterr().out)
    assert unresolved["unresolvedCount"] >= 1
    assert any("question marker" in item["reasons"] for item in unresolved["unresolved"])

    assert _run_main([
        "memory",
        "work-graph",
        "diff",
        "--project-root", str(tmp_path),
        "--baseline", ".controlwork/graph-baseline.json",
    ]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["summary"]["addedNodes"] >= 1


def test_memory_work_dashboard_generates_static_projection(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "decisions",
        "--project-root", str(tmp_path),
        "--title", "Dashboard Decision",
        "--body", "Use the static dashboard as the local project status surface.",
        "--lifecycle", "active",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "sources",
        "--project-root", str(tmp_path),
        "--title", "Dashboard Source?",
        "--body", "This source still needs review before it becomes project truth.",
        "--lifecycle", "needs_review",
        "--source", "docs/source.md",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "legacy",
        "--project-root", str(tmp_path),
        "--title", "Old Dashboard Note",
        "--body", "Superseded dashboard planning note.",
        "--lifecycle", "legacy",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-checkpoint",
        "--project-root", str(tmp_path),
        "--title", "Dashboard checkpoint",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-dashboard",
        "--project-root", str(tmp_path),
        "--format", "html",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    html_path = tmp_path / payload["path"]
    html_text = html_path.read_text(encoding="utf-8")
    assert payload["ok"] is True
    assert payload["path"] == ".controlwork/dashboard/index.html"
    assert payload["summary"]["activeDecisions"] == 1
    assert payload["summary"]["needsReview"] >= 1
    assert payload["handoffReadiness"]["latestCheckpoint"]
    assert payload["externalCalls"] == {
        "aiCalls": False,
        "networkCalls": False,
        "subprocessCalls": False,
    }
    assert "ControlWork Static Dashboard" in html_text
    assert "Active Decisions" in html_text
    assert "Open Questions" in html_text

    assert _run_main([
        "memory",
        "work-dashboard",
        "--project-root", str(tmp_path),
        "--format", "md",
        "--output", ".controlwork/dashboard/index.md",
    ]) == 0
    md_payload = json.loads(capsys.readouterr().out)
    md_text = (tmp_path / md_payload["path"]).read_text(encoding="utf-8")
    assert md_payload["path"] == ".controlwork/dashboard/index.md"
    assert "# ControlWork Static Dashboard" in md_text
    assert "## Graph Health" in md_text


def test_memory_work_dashboard_generates_project_map_json_contract(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "python learning project map",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-capture",
        "sources",
        "--project-root", str(tmp_path),
        "--title", "Python Learning Source",
        "--body", "Reviewed source summary for AI and Python learning materials.",
        "--lifecycle", "active",
        "--source", "source.pdf",
        "--json",
    ]) == 0
    source_payload = json.loads(capsys.readouterr().out)
    assert _run_main([
        "memory",
        "work-capture",
        "decisions",
        "--project-root", str(tmp_path),
        "--title", "Local Cockpit Boundary",
        "--body", "The Project Map explains generated state and does not execute commands.",
        "--lifecycle", "active",
        "--json",
    ]) == 0
    decision_payload = json.loads(capsys.readouterr().out)
    assert _run_main([
        "memory",
        "work-capture",
        "plans",
        "--project-root", str(tmp_path),
        "--title", "Project Map Plan",
        "--body", "Build a learning plan from reviewed AI Python sources and produce a local output.",
        "--lifecycle", "active",
        "--json",
    ]) == 0
    plan_payload = json.loads(capsys.readouterr().out)
    assert _run_main([
        "memory",
        "work-capture",
        "outputs",
        "--project-root", str(tmp_path),
        "--title", "Learning Path Output",
        "--body", "Generated learning path output based on reviewed source summaries.",
        "--lifecycle", "needs_review",
        "--json",
    ]) == 0
    output_payload = json.loads(capsys.readouterr().out)
    assert _run_main([
        "memory",
        "work-session",
        "start",
        "--project-root", str(tmp_path),
        "--id", "project-map-session",
        "--topic", "Project Map fixture generation",
        "--mode", "implementation",
        "--operator", "Codex CLI",
        "--summary", "Generated the Project Map contract fixture and linked sources, decisions, plans, and follow-ups.",
    ]) == 0
    capsys.readouterr()
    for link_type, target, target_type in (
        ("references_entry", source_payload["path"], "source"),
        ("references_decision", decision_payload["path"], "decision"),
        ("changes_memory", output_payload["path"], "output"),
        ("references_entry", plan_payload["path"], "plan"),
        ("left_followup", "Review PDF extraction support before relying on extracted text.", "followup"),
    ):
        assert _run_main([
            "memory",
            "work-session",
            "link",
            "--project-root", str(tmp_path),
            "project-map-session",
            "--type", link_type,
            "--target", target,
            "--target-type", target_type,
        ]) == 0
        capsys.readouterr()
    assert _run_main([
        "memory",
        "work-session",
        "close",
        "--project-root", str(tmp_path),
        "project-map-session",
        "--status", "needs_followup",
        "--summary", "Generated contract fixture with an open extraction follow-up.",
        "--decision", "Keep Project Map local and projection-only.",
    ]) == 0
    capsys.readouterr()
    (tmp_path / "fresh-source.md").write_text(
        "# Fresh Source\n\nNew local file added after the last Project Plane scan.\n",
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "work-dashboard",
        "--project-root", str(tmp_path),
        "--format", "json",
        "--output", ".controlwork/dashboard/project-map.json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    payload = json.loads((tmp_path / result["path"]).read_text(encoding="utf-8"))

    assert result["contractVersion"] == work_project_map.PROJECT_MAP_CONTRACT_VERSION
    assert result["scanRefresh"]["enabled"] is True
    assert result["scanRefresh"]["summary"]["new"] >= 1
    assert work_project_map.validate_project_map_payload(payload) == []
    assert payload["scanRefresh"]["summary"]["new"] >= 1
    assert payload["project"]["distribution"] == "embedded_controlcoding"
    assert payload["project"]["baseDocumentPath"] == "PROJECT.md"
    assert payload["project"]["canonicalContextPath"] == "CONTROLWORK.md"
    assert (tmp_path / "PROJECT.md").exists()
    assert payload["externalCalls"]["networkCalls"] is False
    assert payload["documents"]
    base_documents = [document for document in payload["documents"] if document.get("isBaseDocument")]
    assert len(base_documents) == 1
    assert base_documents[0]["path"] == "PROJECT.md"
    assert all(document.get("path") != "CONTROLWORK.md" for document in payload["documents"])
    assert payload["structure"]["scannedFiles"]
    assert payload["structure"]["scannedFolders"]
    assert payload["structure"]["countsByInfrastructureScope"]
    assert payload["infrastructure"]["project"]["baseDocumentPath"] == "PROJECT.md"
    assert payload["infrastructure"]["controlWork"]["scope"] == "controlwork_runtime"
    assert payload["infrastructure"]["controlCoding"]["present"] is True
    assert payload["contract"]["infrastructureScopeValues"]
    assert any(document.get("isScanEvidence") for document in payload["documents"])
    assert any(document.get("isFolderEvidence") for document in payload["documents"])
    scan_documents = [document for document in payload["documents"] if document.get("isScanEvidence")]
    assert all(document.get("projectRole") == "study_research" for document in scan_documents)
    assert all(document.get("corpusSubtype") for document in scan_documents)
    assert payload["structure"]["countsByProjectRole"]["study_research"] >= 1
    assert payload["structure"]["countsByCorpusSubtype"]
    assert payload["graphRag"]["scanEvidenceDocuments"] >= 1
    assert payload["graphRag"]["folderEvidenceDocuments"] >= 1
    assert payload["graphRag"]["filesystemEdges"] >= 1
    assert payload["graphRag"]["mode"] == "metadata_graph_only"
    assert payload["projectProfile"]["projectCorpusPolicy"]
    assert payload["projectProfile"]["infrastructurePolicy"]
    assert payload["projectUnderstanding"]["status"] == "needs_human_confirmation"
    assert payload["projectUnderstanding"]["questions"]
    assert base_documents[0]["infrastructureScope"] == "project_work_infrastructure"
    assert all(
        document.get("projectScope") in {"project_corpus", "project_infrastructure"}
        for document in payload["documents"]
        if document.get("isScanEvidence")
    )
    assert payload["topics"]
    assert payload["sessions"][0]["operator"] == "Codex CLI"
    session_id = payload["sessions"][0]["id"]
    assert any(edge["from"] == session_id and edge["group"] == "session" for edge in payload["edges"])
    assert all(not (edge["relationStatus"] == "suggested" and edge["canonical"]) for edge in payload["edges"])
    assert any(node["type"] == "followup" for node in payload["views"]["generatedSessionNodes"])
    scan_index = json.loads((tmp_path / ".controlwork" / "ingestion" / "file-index.json").read_text(encoding="utf-8"))
    assert any(item["path"] == "fresh-source.md" and item["scanStatus"] == "new" for item in scan_index["files"])


def test_memory_doctor_reports_project_plane_readiness(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo", json_output=True) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-quickstart",
        "--project-root", str(tmp_path),
        "--topic", "launch plan",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert cc.cmd_memory_doctor(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["project_plane"]["status"] == "ok"
    assert "contextPackets=1" in checks["project_plane"]["detail"]


def test_memory_work_export_import_attach_and_sync(tmp_path):
    coding_project = tmp_path / "coding"
    work_project = tmp_path / "work"
    imported_project = tmp_path / "imported"
    coding_project.mkdir()
    imported_project.mkdir()

    assert cc.cmd_memory_work_init(
        coding_project,
        project_name="SGC",
        purpose="Compliance work memory",
    ) == 0
    (coding_project / ".controlwork" / "memory" / "notes" / "20260101-note.md").write_text(
        "# Note\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_work_export(coding_project, work_project, force=False, json_output=True) == 0
    exported_config = json.loads((work_project / ".controlwork" / "config.json").read_text(encoding="utf-8"))
    assert exported_config["distribution"] == "standalone_repo"
    assert exported_config["embeddedCompatible"] is True
    assert (work_project / "AGENTS.md").exists()

    assert cc.cmd_memory_work_import(imported_project, work_project, force=False, json_output=True) == 0
    imported_config = json.loads((imported_project / ".controlwork" / "config.json").read_text(encoding="utf-8"))
    assert imported_config["distribution"] == "embedded_controlcoding"
    assert (imported_project / ".controlwork" / "memory" / "notes" / "20260101-note.md").exists()

    linked_project = tmp_path / "linked"
    linked_project.mkdir()
    assert cc.cmd_memory_work_attach(linked_project, work_project, json_output=True) == 0
    link = json.loads((linked_project / ".controlwork" / "link.json").read_text(encoding="utf-8"))
    assert link["mode"] == "linked_external_controlwork"
    assert Path(link["externalPath"]).resolve() == work_project.resolve()
    assert cc.cmd_memory_work_status(linked_project, json_output=True) == 0

    assert cc.cmd_memory_work_sync(linked_project, direction="pull", force=True, json_output=True) == 0
    assert (linked_project / "CONTROLWORK.md").exists()
    assert (linked_project / ".controlwork" / "link.json").exists()

    (linked_project / ".controlwork" / "memory" / "plans" / "20260102-plan.md").write_text(
        "# Plan\n",
        encoding="utf-8",
    )
    assert cc.cmd_memory_work_sync(linked_project, direction="push", force=True, json_output=True) == 0
    assert (work_project / ".controlwork" / "memory" / "plans" / "20260102-plan.md").exists()


def test_memory_work_status_treats_distribution_only_drift_as_non_actionable(tmp_path, capsys):
    coding_project = tmp_path / "coding"
    work_project = tmp_path / "work"
    coding_project.mkdir()
    work_project.mkdir()

    assert cc.cmd_memory_work_init(
        coding_project,
        project_name="ControlCoding Project Plane",
        purpose="Embedded project plane",
    ) == 0
    assert cc.cmd_memory_work_init(
        work_project,
        project_name="ControlWork",
        purpose="Standalone project plane",
    ) == 0

    (coding_project / "CONTROLWORK.md").write_text(
        "\n".join([
            "# ControlCoding Project Plane",
            "",
            "## Project Identity",
            "",
            "- **Product Boundary**: Embedded ControlWork project plane inside a ControlCoding project.",
            "",
            "## Implemented Embedded Work Features",
            "",
            "- Category registry: `cc memory work-category list` manages categories.",
            "- Views: `cc memory work-views` builds generated views.",
            "- Checkpoints: `cc memory work-checkpoint` creates receipts.",
            "- Handoff: `cc memory work-handoff` prepares transfer packets.",
            "- Context packets: `cc memory work-context-pack` builds scoped packets.",
            "- Obsidian projection: `cc memory work-obsidian check` reviews generated wiki pages.",
            "- Wiki edit review: `cc memory work-wiki import-edits --review` creates proposals.",
            "- Read-only MCP inspection: `cc memory work-mcp tools` exposes local queries.",
            "- Operational index: `cc memory op-index` coordinates memory planes without hidden writes.",
            "",
        ]),
        encoding="utf-8",
    )
    (work_project / "CONTROLWORK.md").write_text(
        "\n".join([
            "# ControlWork",
            "",
            "## Project Identity",
            "",
            "- **Product Boundary**: Standalone ControlWork repository.",
            "",
            "## AI Host Onboarding",
            "",
            "- Generate host adapters from the canonical context.",
            "",
            "## License And Attribution",
            "",
            "- Preserve attribution in public redistributions.",
            "",
        ]),
        encoding="utf-8",
    )
    standalone_config = json.loads((work_project / ".controlwork" / "config.json").read_text(encoding="utf-8"))
    standalone_config["distribution"] = "standalone_repo"
    (work_project / ".controlwork" / "config.json").write_text(
        json.dumps(standalone_config, indent=2) + "\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_work_attach(coding_project, work_project, json_output=True) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-status",
        "--project-root",
        str(coding_project),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift"]["embeddedVsExternalDifferent"] is True
    assert payload["drift"]["semantic"]["actionableDrift"] is False
    assert payload["drift"]["semantic"]["context"]["classification"] == "expected_distribution_context"
    assert payload["drift"]["semantic"]["config"]["distributionDifferent"] is True
    assert payload["drift"]["semantic"]["config"]["sharedContractDifferent"] is False
    assert payload["drift"]["semantic"]["categories"]["semanticDifferent"] is False
    assert payload["drift"]["semantic"]["memoryContent"]["different"] is False
    assert payload["drift"]["semantic"]["memoryContent"]["inventory"] == {
        "embeddedOnly": [],
        "externalOnly": [],
        "changed": [],
    }
    request_kinds = {item["kind"] for item in payload["drift"]["updateRequests"]}
    assert "manual_sync_review" not in request_kinds
    assert "manual_context_merge_review" not in request_kinds
    assert "manual_category_merge_review" not in request_kinds

    assert _run_main([
        "memory",
        "op-index",
        "--project-root",
        str(coding_project),
        "--json",
    ]) == 0
    op_index = json.loads(capsys.readouterr().out)
    assert not any(
        item.get("kind") == "manual_sync_review"
        for item in op_index["actionQueue"]
    )

    assert _run_main([
        "memory",
        "work-parity",
        "--project-root",
        str(coding_project),
        "--json",
    ]) == 0
    parity = json.loads(capsys.readouterr().out)
    assert parity["state"] == "GREEN"
    assert parity["readOnly"] is True
    assert parity["hiddenWrites"] is False
    assert parity["workPlaneContract"] == {
        "identity": "controlwork-work-plane/1.0.0",
        "profile": "ControlCoding embedded conformance profile",
        "status": "non-canonical",
        "canonicalAuthority": (
            "ControlWork repository, docs/work-plane-compatibility-contract.md"
        ),
    }
    assert parity["compatibility"]["contractCompatible"] is True
    assert parity["compatibility"]["expectedDistributionContext"] is True
    assert parity["compatibility"]["memoryAligned"] is True
    assert parity["commandSurface"]["mechanicallyComparable"] is True
    shared_commands = {
        (item["standalone"], item["embedded"])
        for item in parity["commandSurface"]["shared"]
    }
    shared_entries = parity["commandSurface"]["shared"]
    assert sum(item["embedded"] == "cc memory work-quickstart" for item in shared_entries) == 1
    assert sum(item["embedded"] == "cc memory work-dashboard" for item in shared_entries) == 1
    assert sum(item["embedded"] == "cc memory work-query" for item in shared_entries) == 1
    assert ("cw.py quickstart", "cc memory work-quickstart") in shared_commands
    assert ("cw.py init", "cc memory work-init") in shared_commands
    assert ("cw.py scan-review", "cc memory work-review") in shared_commands
    assert ("cw.py scan-promote", "cc memory work-promote") in shared_commands
    assert ("cw.py checkpoint", "cc memory work-checkpoint") in shared_commands
    assert ("cw.py handoff", "cc memory work-handoff") in shared_commands
    assert ("cw.py context-pack", "cc memory work-context-pack") in shared_commands
    assert ("cw.py dashboard", "cc memory work-dashboard") in shared_commands
    assert ("cw.py query", "cc memory work-query") in shared_commands
    assert ("cw.py mcp", "cc memory work-mcp") in shared_commands
    divergences = {
        item["surface"]: set(item["features"])
        for item in parity["commandSurface"]["acceptedDivergences"]
    }
    embedded_only = divergences["embedded_controlcoding"]
    standalone_only = divergences["standalone_controlwork"]
    assert embedded_only == {
        "verification receipts",
        "hook and boundary enforcement",
        "code-specific GraphRAG",
        "release doctor integration",
        "agent runs",
        "code impact",
        "Dev Plane",
        "Code Plane",
        "developer graph routing",
        "work-attach",
        "work-import",
        "work-export",
        "work-sync",
        "work-parity",
    }
    assert standalone_only == {
        "standalone product docs maintenance",
        "maintenance carousel",
        "host adapter maintenance",
        "install-guide",
    }
    assert "coding agents or agent runs" not in embedded_only
    assert "ControlWork product documentation maintenance" not in standalone_only
    assert "maintain or maintenance carousel" not in standalone_only
    assert "standalone host adapter maintenance" not in standalone_only
    runtime_config = json.loads(
        (coding_project / ".controlwork" / "config.json").read_text(encoding="utf-8")
    )
    assert runtime_config["compatibility"]["sharedMemoryContractVersion"] == 1
    assert "workPlaneContract" not in runtime_config["compatibility"]
    assert parity["drift"]["recommendedAction"] == "none"


def test_memory_work_status_reports_project_plane_drift_without_sync(tmp_path, capsys):
    coding_project = tmp_path / "coding"
    work_project = tmp_path / "work"
    coding_project.mkdir()
    work_project.mkdir()

    assert cc.cmd_memory_work_init(
        coding_project,
        project_name="Embedded",
        purpose="Embedded project plane",
    ) == 0
    assert cc.cmd_memory_work_init(
        work_project,
        project_name="Standalone",
        purpose="Standalone project plane",
    ) == 0
    (coding_project / ".controlwork" / "memory" / "notes" / "embedded-note.md").write_text(
        "# Embedded Note\n\n- **Lifecycle**: active\n\n## Body\n\nEmbedded context.\n",
        encoding="utf-8",
    )
    (work_project / ".controlwork" / "memory" / "notes" / "standalone-note.md").write_text(
        "# Standalone Note\n\n- **Lifecycle**: active\n\n## Body\n\nStandalone context.\n",
        encoding="utf-8",
    )
    (coding_project / ".controlwork" / "memory" / "plans" / "shared-plan.md").write_text(
        "# Shared Plan\n\nEmbedded version.\n",
        encoding="utf-8",
    )
    (work_project / ".controlwork" / "memory" / "plans" / "shared-plan.md").write_text(
        "# Shared Plan\n\nStandalone version.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_work_attach(coding_project, work_project, json_output=True) == 0
    capsys.readouterr()

    def file_snapshot(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    coding_before = file_snapshot(coding_project)
    work_before = file_snapshot(work_project)

    assert _run_main([
        "memory",
        "work-status",
        "--project-root",
        str(coding_project),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift"]["embeddedVsExternalDifferent"] is True
    assert payload["drift"]["policy"] == {
        "autoPull": False,
        "autoPush": False,
        "hiddenWrites": False,
    }
    assert payload["drift"]["semantic"]["actionableDrift"] is True
    assert payload["drift"]["semantic"]["memoryContent"]["different"] is True
    assert payload["drift"]["derivedArtifacts"]["views"]["stale"] is True
    assert payload["drift"]["derivedArtifacts"]["contextPackets"]["stale"] is True
    request_kinds = {item["kind"] for item in payload["drift"]["updateRequests"]}
    assert "manual_sync_review" in request_kinds
    assert "regenerate_project_plane_views" in request_kinds
    assert "build_project_plane_context_packet" in request_kinds
    required_contract_fields = {
        "owner", "severity", "recommendedAction", "writesRequired",
        "decisionRequired", "preconditions", "steps",
    }
    assert all(required_contract_fields <= set(item) for item in payload["drift"]["updateRequests"])
    assert all(item["owner"] == "human_operator" for item in payload["drift"]["updateRequests"])
    assert all(item["recommendedAction"] == "manual_review" for item in payload["drift"]["updateRequests"])
    assert all(item["writesRequired"] is False for item in payload["drift"]["updateRequests"])
    sync_request = next(
        item for item in payload["drift"]["updateRequests"]
        if item["kind"] == "manual_sync_review"
    )
    assert sync_request["owner"] == "human_operator"
    assert sync_request["severity"] == "high"
    assert sync_request["recommendedAction"] == "manual_review"
    assert sync_request["writesRequired"] is False
    assert sync_request["decisionRequired"] is True
    assert sync_request["inventory"] == {
        "embeddedOnly": [".controlwork/memory/notes/embedded-note.md"],
        "externalOnly": [".controlwork/memory/notes/standalone-note.md"],
        "changed": [".controlwork/memory/plans/shared-plan.md"],
    }
    assert sync_request["preconditions"] == [
        "Review the memory drift inventory.",
        "Choose whether embedded or standalone ControlWork is authoritative.",
    ]
    assert sync_request["commands"] == [
        "python scripts/cc.py memory work-status --project-root .",
        "python scripts/cc.py memory work-sync --project-root . --direction pull --force",
        "python scripts/cc.py memory work-sync --project-root . --direction push --force",
    ]
    assert sync_request["steps"][0]["kind"] == "read_only_review"
    alternatives = sync_request["steps"][1:]
    assert [(item["order"], item["alternative"]) for item in alternatives] == [(2, "pull"), (2, "push")]
    assert {item["alternativeGroup"] for item in alternatives} == {"memory_sync_direction"}
    assert all(item["writesRequired"] is True for item in alternatives)
    assert payload["drift"]["semantic"]["memoryContent"]["inventory"] == {
        "embeddedOnly": [".controlwork/memory/notes/embedded-note.md"],
        "externalOnly": [".controlwork/memory/notes/standalone-note.md"],
        "changed": [".controlwork/memory/plans/shared-plan.md"],
    }
    assert (coding_project / ".controlwork" / "memory" / "notes" / "embedded-note.md").exists()
    assert not (coding_project / ".controlwork" / "memory" / "notes" / "standalone-note.md").exists()

    assert _run_main([
        "memory",
        "work-parity",
        "--project-root",
        str(coding_project),
        "--json",
    ]) == 1
    parity = json.loads(capsys.readouterr().out)
    assert parity["state"] == "RED"
    assert parity["compatibility"]["contractCompatible"] is False
    assert parity["compatibility"]["contextCompatible"] is False
    assert parity["compatibility"]["memoryAligned"] is False
    assert parity["drift"]["actionableSemanticDrift"] is True
    assert parity["drift"]["policy"] == {
        "autoPull": False,
        "autoPush": False,
        "hiddenWrites": False,
    }
    parity_request_kinds = {item["kind"] for item in parity["drift"]["updateRequests"]}
    assert "manual_context_merge_review" in parity_request_kinds
    assert "manual_sync_review" in parity_request_kinds
    assert next(
        item for item in parity["drift"]["updateRequests"]
        if item["kind"] == "manual_sync_review"
    ) == sync_request

    assert _run_main([
        "memory",
        "op-index",
        "--project-root",
        str(coding_project),
        "--scope",
        "dev",
        "--topic",
        "memory drift",
        "--json",
    ]) == 0
    op_index = json.loads(capsys.readouterr().out)
    queued_request = next(
        item for item in op_index["actionQueue"]
        if item.get("kind") == "manual_sync_review"
    )
    for field in ("owner", "severity", "recommendedAction", "writesRequired", "decisionRequired", "inventory", "preconditions", "steps"):
        assert queued_request[field] == sync_request[field]
    assert queued_request["writes"] is False
    op_index_text = memory_op_index._op_index_text(op_index)
    assert "--direction pull --force; python scripts/cc.py memory work-sync --project-root . --direction push --force" not in op_index_text
    assert "[read] python scripts/cc.py memory work-sync" not in op_index_text
    assert "Choice required: select exactly one mutative alternative" in op_index_text
    assert "[write, alternative pull]" in op_index_text
    assert "[write, alternative push]" in op_index_text

    assert _run_main([
        "memory",
        "startup",
        "--project-root",
        str(coding_project),
        "--scope",
        "dev",
        "--topic",
        "memory drift",
        "--intent",
        "continue_previous_work",
        "--json",
    ]) == 0
    startup = json.loads(capsys.readouterr().out)
    startup_request = next(
        item for item in startup["actionQueue"]
        if item.get("kind") == "manual_sync_review"
    )
    for field in ("owner", "severity", "recommendedAction", "writesRequired", "decisionRequired", "inventory", "preconditions", "steps"):
        assert startup_request[field] == sync_request[field]
    startup_text = memory_op_index._startup_text(startup)
    assert "--direction pull --force; python scripts/cc.py memory work-sync --project-root . --direction push --force" not in startup_text
    assert "[read] python scripts/cc.py memory work-sync" not in startup_text
    assert "Choice required: select exactly one mutative alternative" in startup_text
    assert file_snapshot(coding_project) == coding_before
    assert file_snapshot(work_project) == work_before


def test_main_routes_memory_work_bridge_commands(tmp_path):
    work_project = tmp_path / "work"
    coding_project = tmp_path / "coding"
    coding_project.mkdir()
    assert cc.cmd_memory_work_init(work_project, project_name="Work") == 0

    assert _run_main([
        "memory",
        "work-attach",
        "--project-root", str(coding_project),
        str(work_project),
        "--json",
    ]) == 0

    assert _run_main([
        "memory",
        "work-sync",
        "--project-root", str(coding_project),
        "--direction", "pull",
        "--force",
        "--json",
    ]) == 0

    assert _run_main([
        "memory",
        "work-parity",
        "--project-root", str(coding_project),
        "--json",
    ]) == 0

    exported = tmp_path / "exported"
    assert _run_main([
        "memory",
        "work-export",
        "--project-root", str(coding_project),
        str(exported),
        "--json",
    ]) == 0

    imported = tmp_path / "imported"
    imported.mkdir()
    assert _run_main([
        "memory",
        "work-import",
        "--project-root", str(imported),
        str(exported),
        "--json",
    ]) == 0


def test_main_routes_memory_work_feature_commands(tmp_path):
    assert _run_main([
        "memory",
        "work-init",
        "--project-root", str(tmp_path),
        "--name", "Feature Work",
        "--json",
    ]) == 0

    assert _run_main([
        "memory",
        "work-category",
        "propose",
        "--project-root", str(tmp_path),
        "normative",
        "--area", "sources",
        "--json",
    ]) == 0
    assert _run_main([
        "memory",
        "work-category",
        "approve",
        "--project-root", str(tmp_path),
        "normative",
        "--json",
    ]) == 0
    assert _run_main([
        "memory",
        "work-category",
        "list",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0

    source = tmp_path / ".controlwork" / "memory" / "sources" / "20260101-source.md"
    source.write_text(
        "# Source\n\n"
        "- **Area**: sources\n"
        "- **Lifecycle**: active\n"
        "- **Category**: normative\n\n"
        "## Body\n\n"
        "Regulatory source summary.\n",
        encoding="utf-8",
    )

    assert _run_main(["memory", "work-views", "--project-root", str(tmp_path), "--json"]) == 0
    assert (tmp_path / ".controlwork" / "memory" / "views" / "index.md").exists()

    assert _run_main([
        "memory",
        "work-checkpoint",
        "--project-root", str(tmp_path),
        "--title", "Feature checkpoint",
        "--json",
    ]) == 0
    assert list((tmp_path / ".controlwork" / "checkpoints").glob("*-checkpoint.md"))

    handoff = tmp_path / "handoff.md"
    assert _run_main([
        "memory",
        "work-handoff",
        "--project-root", str(tmp_path),
        "--output", str(handoff),
        "--json",
    ]) == 0
    assert handoff.exists()

    assert _run_main([
        "memory",
        "work-context-pack",
        "--project-root", str(tmp_path),
        "--scope", "research",
        "--topic", "Regulatory",
        "--json",
    ]) == 0
    context_packets = list((tmp_path / ".controlwork" / "context-packets").glob("*.md"))
    assert context_packets
    packet_text = context_packets[0].read_text(encoding="utf-8")
    assert "ControlWork Context Packet" in packet_text
    assert "Legacy Or Superseded Warning" in packet_text

    assert _run_main(["memory", "work-obsidian", "init", "--project-root", str(tmp_path), "--json"]) == 0
    assert (tmp_path / ".obsidian" / "app.json").exists()
    home = tmp_path / "wiki" / "Home.md"
    assert home.exists()
    home.write_text(
        "\n".join(
            "generated_at: 2000-01-01T00:00:00Z" if line.startswith("generated_at: ") else line
            for line in home.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    source_ledger = tmp_path / "wiki" / "Source Ledger.md"
    source_ledger.write_text(
        "\n".join(
            "Generated: 2000-01-01T00:00:00Z" if line.startswith("Generated: ") else line
            for line in source_ledger.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    assert _run_main(["memory", "work-obsidian", "check", "--project-root", str(tmp_path), "--json"]) == 0

    assert _run_main(["memory", "work-mcp", "tools", "--project-root", str(tmp_path), "--json"]) == 0
    assert _run_main([
        "memory",
        "work-mcp",
        "call",
        "controlwork_list_categories",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0

    args_file = tmp_path / "context-pack-args.json"
    args_file.write_text(json.dumps({"scope": "research", "topic": "Regulatory"}), encoding="utf-8")
    assert _run_main([
        "memory",
        "work-mcp",
        "call",
        "controlwork_context_pack",
        "--project-root", str(tmp_path),
        "--args-file", str(args_file),
        "--json",
    ]) == 0


def test_work_wiki_import_edits_review_creates_proposal(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-init",
        "--project-root", str(tmp_path),
        "--name", "Wiki Review",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-obsidian",
        "init",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0
    capsys.readouterr()

    home = tmp_path / "wiki" / "Home.md"
    home.write_text(
        home.read_text(encoding="utf-8") + "\nHuman reviewed wiki edit.\n",
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "work-wiki",
        "import-edits",
        "--project-root", str(tmp_path),
        "--review",
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    drift = {item["path"]: item["state"] for item in payload["drift"]}
    assert payload["ok"] is True
    assert len(payload["drift"]) == 1
    assert drift == {"wiki/Home.md": "drifted"}
    assert len(payload["proposals"]) == 1

    proposal = (tmp_path / payload["proposals"][0]).resolve()
    proposal_root = (tmp_path / ".controlwork" / "proposals").resolve()
    assert proposal.is_relative_to(proposal_root)
    proposal_text = proposal.read_text(encoding="utf-8")
    assert "# Proposed Wiki Edit: wiki/Home.md" in proposal_text
    assert "Human reviewed wiki edit." in proposal_text


def test_work_ocr_import_sidecar_validates_and_persists(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-init",
        "--project-root", str(tmp_path),
        "--name", "OCR Import",
        "--json",
    ]) == 0
    capsys.readouterr()

    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\n%% scanned fixture\n")

    assert _run_main([
        "memory",
        "work-scan",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 0
    capsys.readouterr()

    index_path = tmp_path / ".controlwork" / "ingestion" / "file-index.json"
    index_before_failure = index_path.read_bytes()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    record = next(item for item in index["files"] if item["path"] == "docs/scan.pdf")

    input_sidecar = tmp_path / "incoming-scan.ocr.json"
    sidecar_payload = {
        "schemaVersion": "controlwork-ocr-sidecar/v1",
        "sourcePath": "docs/scan.pdf",
        "sourceHash": "0" * 64,
        "extractor": "fixture_ocr",
        "pages": [{"page": 1, "text": "Imported OCR evidence."}],
    }
    input_sidecar.write_text(json.dumps(sidecar_payload), encoding="utf-8")

    command = [
        "memory",
        "work-ocr",
        "import-sidecar",
        "docs/scan.pdf",
        "--sidecar", str(input_sidecar),
        "--project-root", str(tmp_path),
        "--json",
    ]
    assert _run_main(command) == 1
    error_payload = json.loads(capsys.readouterr().out)
    assert "source hash does not match" in error_payload["error"]
    assert not list((tmp_path / ".controlwork" / "extracts").glob("*.ocr.json"))
    assert index_path.read_bytes() == index_before_failure

    sidecar_payload["sourceHash"] = record["contentHash"]
    input_sidecar.write_text(json.dumps(sidecar_payload), encoding="utf-8")

    assert _run_main(command) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["sourcePath"] == "docs/scan.pdf"
    assert payload["importSource"] is False
    assert payload["dryRun"] is False

    target = (tmp_path / payload["sidecarPath"]).resolve()
    extracts_root = (tmp_path / ".controlwork" / "extracts").resolve()
    assert target.is_relative_to(extracts_root)
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["sourcePath"] == "docs/scan.pdf"
    assert persisted["sourceHash"] == record["contentHash"]
    assert persisted["pages"][0]["text"] == "Imported OCR evidence."

    updated_index = json.loads(index_path.read_text(encoding="utf-8"))
    updated_record = next(
        item for item in updated_index["files"] if item["path"] == "docs/scan.pdf"
    )
    assert updated_record["ocrSidecar"] == payload["sidecarPath"]
    assert updated_record["ocrTextHash"] == payload["textHash"]


def test_work_mcp_read_entry_allows_controlwork_memory_file(tmp_path, capsys):
    target = tmp_path / ".controlwork" / "memory" / "notes" / "entry.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Entry\n\nGoverned memory.\n", encoding="utf-8")
    args_file = tmp_path / "read-entry-args.json"
    args_file.write_text(json.dumps({"path": ".controlwork/memory/notes/entry.md"}), encoding="utf-8")

    assert _run_main([
        "memory",
        "work-mcp",
        "call",
        "controlwork_read_entry",
        "--project-root", str(tmp_path),
        "--args-file", str(args_file),
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["ok"] is True
    assert payload["result"]["path"] == ".controlwork/memory/notes/entry.md"
    assert "Governed memory." in payload["result"]["text"]


def test_work_mcp_read_entry_blocks_arbitrary_project_file(tmp_path, capsys):
    secret = tmp_path / "src" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("local secret\n", encoding="utf-8")
    args_file = tmp_path / "read-entry-args.json"
    args_file.write_text(json.dumps({"path": "src/secret.txt"}), encoding="utf-8")

    assert _run_main([
        "memory",
        "work-mcp",
        "call",
        "controlwork_read_entry",
        "--project-root", str(tmp_path),
        "--args-file", str(args_file),
        "--json",
    ]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "ControlWork memory/wiki roots" in payload["error"]


def test_work_import_source_blocks_stale_scan_record(tmp_path, capsys):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    source = docs_dir / "source.md"
    source.write_text("# Source\n\nOriginal scan text.\n", encoding="utf-8")

    assert _run_main(["memory", "work-scan", "--project-root", str(tmp_path), "--json"]) == 0
    capsys.readouterr()

    source.write_text("# Source\n\nChanged after scan.\n", encoding="utf-8")

    assert _run_main([
        "memory",
        "work-import-source",
        "docs/source.md",
        "--project-root", str(tmp_path),
        "--json",
    ]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "changed after the last scan" in payload["error"]
    assert payload["expectedHash"] != payload["currentHash"]


def test_work_import_source_pdf_marks_stdlib_fallback_metadata(tmp_path, capsys):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    _write_simple_native_pdf(docs_dir / "native-report.pdf", "Native PDF import evidence", "Second line for import")

    assert _run_main(["memory", "work-scan", "--project-root", str(tmp_path), "--json"]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-import-source",
        "docs/native-report.pdf",
        "--project-root", str(tmp_path),
        "--title", "Native Report",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True

    record = _source_ledger_records(tmp_path)[-1]
    assert record["extractor"] == "pdf_native_text_best_effort"
    metadata = record["extractionMetadata"]
    assert metadata["extractorFamily"] == "stdlib_fallback"
    assert metadata["extractorDependency"] == "stdlib"
    assert metadata["pageCount"] == 1

    imported = (tmp_path / record["importedMemoryPath"]).read_text(encoding="utf-8")
    assert "- **Extractor**: pdf_native_text_best_effort" in imported
    assert "- **Import Metadata extractorFamily**: stdlib_fallback" in imported
    assert "Native PDF import evidence" in imported


def test_work_import_source_docx_marks_stdlib_fallback_metadata(tmp_path, capsys):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    _write_minimal_docx(docs_dir / "brief.docx", "DOCX fallback evidence", "Second DOCX paragraph")

    assert _run_main(["memory", "work-scan", "--project-root", str(tmp_path), "--json"]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-import-source",
        "docs/brief.docx",
        "--project-root", str(tmp_path),
        "--title", "Brief",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True

    record = _source_ledger_records(tmp_path)[-1]
    assert record["extractor"] == "docx_text"
    metadata = record["extractionMetadata"]
    assert metadata["extractorFamily"] == "stdlib_fallback"
    assert metadata["extractorDependency"] == "stdlib"
    assert metadata["paragraphCount"] == 2

    imported = (tmp_path / record["importedMemoryPath"]).read_text(encoding="utf-8")
    assert "- **Extractor**: docx_text" in imported
    assert "- **Import Metadata extractorFamily**: stdlib_fallback" in imported
    assert "DOCX fallback evidence" in imported


def test_main_routes_memory_work_ready_to_promote_gate(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-init",
        "--project-root", str(tmp_path),
        "--name", "Ready Gate Work",
        "--json",
    ]) == 0
    capsys.readouterr()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "clean.md").write_text("# Clean\n\nReviewed unique source.\n", encoding="utf-8")
    (docs_dir / "duplicate-a.md").write_text("# Duplicate\n\nSame text.\n", encoding="utf-8")
    (docs_dir / "duplicate-b.md").write_text("# Duplicate\n\nSame text.\n", encoding="utf-8")

    assert _run_main(["memory", "work-scan", "--project-root", str(tmp_path), "--json"]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-review",
        "docs/duplicate-a.md",
        "--project-root", str(tmp_path),
        "--review-status", "ready_to_promote",
        "--json",
    ]) == 1
    blocked_ready = capsys.readouterr().out
    assert "promotionBlockers" in blocked_ready
    assert "duplicate_source" in blocked_ready

    assert _run_main([
        "memory",
        "work-review",
        "docs/clean.md",
        "--project-root", str(tmp_path),
        "--review-status", "ready_to_promote",
        "--json",
    ]) == 0
    ready_payload = json.loads(capsys.readouterr().out)
    assert ready_payload["promotionBlockers"] == []

    assert _run_main([
        "memory",
        "work-promote",
        "docs/clean.md",
        "--project-root", str(tmp_path),
        "--area", "sources",
        "--title", "Clean Source",
        "--summary", "Promoted clean source.",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "work-review",
        "docs/duplicate-a.md",
        "--project-root", str(tmp_path),
        "--review-status", "reviewed",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-promote",
        "docs/duplicate-a.md",
        "--project-root", str(tmp_path),
        "--area", "sources",
        "--title", "Duplicate Source",
        "--summary", "Should require override.",
        "--json",
    ]) == 1
    blocked_promote = capsys.readouterr().out
    assert "readiness gate" in blocked_promote
    assert "duplicate_source" in blocked_promote

    assert _run_main([
        "memory",
        "work-promote",
        "docs/duplicate-a.md",
        "--project-root", str(tmp_path),
        "--area", "sources",
        "--title", "Duplicate Source",
        "--summary", "Should require override.",
        "--force",
        "--json",
    ]) == 1
    force_block = capsys.readouterr().out
    assert "--force-note is required" in force_block

    assert _run_main([
        "memory",
        "work-promote",
        "docs/duplicate-a.md",
        "--project-root", str(tmp_path),
        "--area", "sources",
        "--title", "Duplicate Source",
        "--summary", "Override with explicit note.",
        "--force",
        "--force-note", "Reviewed duplicate intentionally.",
        "--json",
    ]) == 0
    forced_payload = json.loads(capsys.readouterr().out)
    assert forced_payload["forced"] is True


def test_main_routes_memory_work_pending_keeps_unchanged_unreviewed_sources(tmp_path, capsys):
    assert _run_main([
        "memory",
        "work-init",
        "--project-root", str(tmp_path),
        "--name", "Pending Work",
        "--json",
    ]) == 0
    capsys.readouterr()
    docs_dir = tmp_path / "docs"
    src_dir = tmp_path / "src"
    assets_dir = tmp_path / "assets"
    docs_dir.mkdir()
    src_dir.mkdir()
    assets_dir.mkdir()
    (docs_dir / "spec.md").write_text("# Spec\n\nInvoice settlement evidence.\n", encoding="utf-8")
    (src_dir / "app.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
    (assets_dir / "pixel.png").write_bytes(bytes([
        137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72,
        68, 82, 0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0, 0, 0,
        144, 119, 83, 222, 0, 0, 0, 12, 73, 68, 65, 84,
        8, 215, 99, 248, 255, 255, 63, 0, 5, 254, 2, 254,
        167, 53, 129, 132, 0, 0, 0, 0, 73, 69, 78, 68,
        174, 66, 96, 130,
    ]))

    assert _run_main(["memory", "work-scan", "--project-root", str(tmp_path), "--json"]) == 0
    capsys.readouterr()
    assert _run_main(["memory", "work-scan", "--project-root", str(tmp_path), "--json"]) == 0
    capsys.readouterr()

    index = json.loads((tmp_path / ".controlwork" / "ingestion" / "file-index.json").read_text(encoding="utf-8"))
    unchanged_unreviewed = [
        item for item in index["files"]
        if item["scanStatus"] == "unchanged" and item["reviewStatus"] == "unreviewed"
    ]
    assert unchanged_unreviewed

    assert _run_main([
        "memory",
        "work-review",
        "--project-root", str(tmp_path),
        "--filter", "pending",
        "--json",
    ]) == 0
    pending = json.loads(capsys.readouterr().out)["summary"]
    assert pending["byReviewStatus"]["unreviewed"] > 0
    assert pending["reviewQueueCount"] > 0
    assert pending["pendingReviewCount"] > 0

    assert _run_main([
        "memory",
        "work-dashboard",
        "--project-root", str(tmp_path),
        "--format", "html",
    ]) == 0
    dashboard = json.loads(capsys.readouterr().out)
    assert dashboard["summary"]["pendingScanReview"] > 0
    assert dashboard["handoffReadiness"]["status"] == "attention"

    assert _run_main([
        "memory",
        "work-review",
        "docs/spec.md",
        "--project-root", str(tmp_path),
        "--review-status", "reviewed",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "work-review",
        "--project-root", str(tmp_path),
        "--filter", "pending",
        "--json",
    ]) == 0
    after_review = json.loads(capsys.readouterr().out)["summary"]
    assert after_review["pendingReviewCount"] < pending["pendingReviewCount"]


def test_file_index_no_rehash_fast_path_reuses_hashes_and_rehashes_legacy(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nStable indexed text.\n", encoding="utf-8")
    stat_result = source.stat()
    previous_record = {
        "path": "source.md",
        "kind": "markdown",
        "sizeBytes": stat_result.st_size,
        "modifiedAtNs": stat_result.st_mtime_ns,
        "contentHash": "cached-content",
        "textHash": "cached-text",
        "normalizedTextHash": "cached-normalized-text",
        "reviewStatus": "reviewed",
        "reviewedAt": "2026-06-22T00:00:00Z",
        "reviewedContentHash": "cached-content",
    }

    def fail_sha256_file(path: Path) -> str:
        raise AssertionError(f"sha256_file should not be called for {path}")

    def fail_text_hashes_for_file(path: Path, kind: str, size_bytes: int) -> tuple[str, str]:
        raise AssertionError(f"text_hashes_for_file should not be called for {path} {kind} {size_bytes}")

    monkeypatch.setattr(work_features, "sha256_file", fail_sha256_file)
    monkeypatch.setattr(work_features, "text_hashes_for_file", fail_text_hashes_for_file)

    payload = work_features.build_file_index(tmp_path, {"files": [previous_record]})
    record = payload["files"][0]

    assert record["scanStatus"] == "unchanged"
    assert record["modifiedAtNs"] == stat_result.st_mtime_ns
    assert record["contentHash"] == "cached-content"
    assert record["textHash"] == "cached-text"
    assert record["normalizedTextHash"] == "cached-normalized-text"
    assert record["reviewStatus"] == "reviewed"
    assert record["reviewedAt"] == "2026-06-22T00:00:00Z"
    assert record["reviewedContentHash"] == "cached-content"

    calls = []

    def record_sha256_file(path: Path) -> str:
        calls.append(("sha256", path.as_posix()))
        return "fresh-content"

    def record_text_hashes_for_file(path: Path, kind: str, size_bytes: int) -> tuple[str, str]:
        calls.append(("text", kind, size_bytes))
        return "fresh-text", "fresh-normalized-text"

    monkeypatch.setattr(work_features, "sha256_file", record_sha256_file)
    monkeypatch.setattr(work_features, "text_hashes_for_file", record_text_hashes_for_file)

    legacy_record = dict(previous_record)
    legacy_record.pop("modifiedAtNs")
    calls.clear()
    legacy_payload = work_features.build_file_index(tmp_path, {"files": [legacy_record]})
    assert calls == [("sha256", source.as_posix()), ("text", "markdown", stat_result.st_size)]
    assert legacy_payload["files"][0]["contentHash"] == "fresh-content"
    assert legacy_payload["files"][0]["textHash"] == "fresh-text"
    assert legacy_payload["files"][0]["normalizedTextHash"] == "fresh-normalized-text"

    stale_mtime_record = dict(previous_record, modifiedAtNs=stat_result.st_mtime_ns - 1)
    calls.clear()
    stale_payload = work_features.build_file_index(tmp_path, {"files": [stale_mtime_record]})
    assert calls == [("sha256", source.as_posix()), ("text", "markdown", stat_result.st_size)]
    assert stale_payload["files"][0]["contentHash"] == "fresh-content"
    assert stale_payload["files"][0]["textHash"] == "fresh-text"
    assert stale_payload["files"][0]["normalizedTextHash"] == "fresh-normalized-text"


def test_memory_intake_add_writes_inbox_document_and_entity(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Work", profile="work") == 0
    capsys.readouterr()

    result = cc.cmd_memory_intake_add(
        tmp_path,
        title="PDF Extract",
        summary="Important extracted source text.",
        source_type="pdf",
        source_refs=["source.pdf"],
        json_output=True,
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    intake_path = tmp_path / payload["path"]
    assert intake_path.exists()
    content = intake_path.read_text(encoding="utf-8")
    assert "# PDF Extract" in content
    assert "Source type: pdf" in content
    assert "source.pdf" in content
    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    conn.row_factory = sqlite3.Row
    try:
        entity_rows = conn.execute("SELECT type, path, lifecycle FROM entities WHERE path = ?", (payload["path"],)).fetchall()
    finally:
        conn.close()
    assert entity_rows[0]["type"] == "note"
    assert entity_rows[0]["lifecycle"] == "captured"


def test_memory_intake_promote_moves_document_updates_entity_and_views(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Work", profile="work") == 0
    capsys.readouterr()
    assert cc.cmd_memory_intake_add(
        tmp_path,
        title="Open Research Question?",
        summary="Should this project use the reviewed workflow?",
        source_type="chat",
        path="docs/inbox/research-question.md",
        json_output=True,
    ) == 0
    capsys.readouterr()

    result = cc.cmd_memory_intake_promote(
        tmp_path,
        "docs/inbox/research-question.md",
        target="research",
        reason="Reviewed and ready for synthesis",
        json_output=True,
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fromPath"] == "docs/inbox/research-question.md"
    assert payload["path"] == "docs/research/research-question.md"
    assert payload["lifecycle"] == "triaged"
    assert not (tmp_path / "docs" / "inbox" / "research-question.md").exists()
    assert (tmp_path / "docs" / "research" / "research-question.md").exists()
    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    conn.row_factory = sqlite3.Row
    try:
        entity_rows = conn.execute("SELECT path, lifecycle FROM entities WHERE id = ?", (payload["entityId"],)).fetchall()
    finally:
        conn.close()
    assert entity_rows[0]["path"] == "docs/research/research-question.md"
    assert entity_rows[0]["lifecycle"] == "triaged"
    assert "Open Research Question" in (tmp_path / ".controlcoding" / "views" / "OPEN_QUESTIONS.md").read_text(encoding="utf-8")
    assert "research-question.md" in (tmp_path / ".controlcoding" / "views" / "SOURCE_LEDGER.md").read_text(encoding="utf-8")


def test_memory_init_routes_work_profile_through_main(tmp_path):
    argv = [
        "cc.py",
        "memory",
        "init",
        "--project-root", str(tmp_path),
        "--mode", "document-only",
        "--profile", "work",
        "--project-short", "Work",
    ]

    with patch.object(sys, "argv", argv), patch.object(cc, "cmd_memory_init", return_value=0) as mock_cmd:
        assert cc.main() == 0

    mock_cmd.assert_called_once_with(
        tmp_path.resolve(),
        mode="document-only",
        project_short="Work",
        profile="work",
        json_output=False,
    )


def test_memory_intake_promote_routes_through_main(tmp_path):
    argv = [
        "cc.py",
        "memory",
        "intake",
        "promote",
        "--project-root", str(tmp_path),
        "docs/inbox/item.md",
        "--to", "research",
        "--reason", "triaged",
    ]

    with patch.object(sys, "argv", argv), patch.object(cc, "cmd_memory_intake_promote", return_value=0) as mock_cmd:
        assert cc.main() == 0

    mock_cmd.assert_called_once_with(
        tmp_path.resolve(),
        selector="docs/inbox/item.md",
        target="research",
        lifecycle="",
        reason="triaged",
        json_output=False,
    )


def test_memory_scan_indexes_files_in_place_and_generates_lifecycle_views(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "corpus").mkdir()
    (tmp_path / "docs" / "legacy").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("# Demo Project", encoding="utf-8")
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "corpus" / "runtime-note.txt").write_text("application-owned memory reference\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    (tmp_path / "docs" / "legacy" / "old-plan.md").write_text("# Old Plan\n", encoding="utf-8")

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    rows = _db_rows(tmp_path, "SELECT type, path, lifecycle FROM entities ORDER BY path")
    indexed_paths = {row["path"] for row in rows}
    assert "README.md" in indexed_paths
    assert "src/app.py" in indexed_paths
    assert "corpus/runtime-note.txt" in indexed_paths
    assert "tests/test_app.py" in indexed_paths
    assert "docs/legacy/old-plan.md" in indexed_paths
    assert not any(str(path).startswith(".controlcoding/") for path in indexed_paths)
    assert (tmp_path / "docs" / "legacy" / "old-plan.md").exists()
    assert any(row["type"] == "test_node" for row in rows)
    assert any(row["lifecycle"] == "stale" for row in rows)
    app_memory = _db_rows(tmp_path, "SELECT id, plane FROM entities WHERE type = 'application_memory_component'")
    assert app_memory[0]["id"].startswith("Demo_MEM_MEMCOMP_")
    assert app_memory[0]["plane"] == "application_memory"

    assert cc.cmd_memory_views_generate(tmp_path) == 0
    stale_index = (tmp_path / ".controlcoding" / "views" / "STALE_INDEX.md").read_text(encoding="utf-8")
    legacy_index = (tmp_path / ".controlcoding" / "views" / "LEGACY_INDEX.md").read_text(encoding="utf-8")
    graph_index = (tmp_path / ".controlcoding" / "views" / "GRAPH_INDEX.md").read_text(encoding="utf-8")
    vector_index = (tmp_path / ".controlcoding" / "views" / "VECTOR_INDEX.md").read_text(encoding="utf-8")
    assert "Old Plan" in stale_index
    assert "Old Plan" in legacy_index
    assert "Semantic chunks" in graph_index
    assert "Memory Vector Index" in vector_index


def test_memory_graph_classifies_chunks_correlates_and_explains_impact(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    long_text = "\n\n".join(["Implementation detail keeps semantic context."] * 120)
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Implementation\n\n"
        f"{long_text}\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Implementation\n\n"
        "Use the local ledger for payment memory evidence.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    rows = _db_rows(tmp_path, "SELECT path, type, data FROM entities WHERE path LIKE 'docs/%' ORDER BY path")
    data_by_path = {row["path"]: json.loads(row["data"]) for row in rows}
    assert data_by_path["docs/plan.md"]["document_type"] == "plan"
    assert "implementation" in data_by_path["docs/plan.md"]["heading_keys"]
    assert data_by_path["docs/decision.md"]["document_type"] == "decision"

    chunk_rows = _db_rows(
        tmp_path,
        "SELECT source_path, heading_path, sequence_index FROM semantic_chunks ORDER BY source_path, ordinal, sequence_index",
    )
    assert any(row["source_path"] == "docs/plan.md" for row in chunk_rows)
    assert any(row["heading_path"] == "Payment Plan > Implementation" for row in chunk_rows)
    assert any(row["sequence_index"] > 1 for row in chunk_rows)

    suggestions = _db_rows(
        tmp_path,
        "SELECT type, confidence, reason FROM correlation_suggestions ORDER BY confidence, type",
    )
    assert any(row["type"] == "references" and row["confidence"] == "explicit_link" for row in suggestions)
    assert any(row["confidence"] == "strong_title_or_heading_match" for row in suggestions)

    assert cc.cmd_memory_chunks(tmp_path, "docs/plan.md") == 0
    chunk_output = capsys.readouterr().out
    assert "Memory chunks: docs/plan.md" in chunk_output
    assert "Payment Plan > Implementation" in chunk_output

    assert cc.cmd_memory_impact(tmp_path, "docs/plan.md") == 0
    impact_output = capsys.readouterr().out
    assert "Payment Decision" in impact_output
    assert "Reasons:" in impact_output


def test_memory_correlation_refresh_preserves_reviewed_suggestions(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Shared Scope\n\n"
        "Payment settlement and ledger evidence stay local.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Shared Scope\n\n"
        "Use local ledger evidence for payment settlement.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    rows = _db_rows(
        tmp_path,
        """
        SELECT id, status, created_at
        FROM correlation_suggestions
        ORDER BY confidence, type, source_id
        """,
    )
    assert len(rows) >= 2
    accepted_id = rows[0]["id"]
    rejected_id = rows[1]["id"]
    original_created = {
        accepted_id: rows[0]["created_at"],
        rejected_id: rows[1]["created_at"],
    }

    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    try:
        conn.execute(
            "UPDATE correlation_suggestions SET status = 'accepted', reason = 'Reviewed accepted' WHERE id = ?",
            (accepted_id,),
        )
        conn.execute(
            "UPDATE correlation_suggestions SET status = 'rejected', reason = 'Reviewed rejected' WHERE id = ?",
            (rejected_id,),
        )
        conn.commit()
    finally:
        conn.close()

    assert cc.cmd_memory_scan(tmp_path) == 0

    reviewed = _db_rows(
        tmp_path,
        f"""
        SELECT id, status, reason, created_at
        FROM correlation_suggestions
        WHERE id IN ('{accepted_id}', '{rejected_id}')
        ORDER BY id
        """,
    )
    assert {row["id"] for row in reviewed} == {accepted_id, rejected_id}
    by_id = {row["id"]: row for row in reviewed}
    assert by_id[accepted_id]["status"] == "accepted"
    assert by_id[accepted_id]["reason"] == "Reviewed accepted"
    assert by_id[accepted_id]["created_at"] == original_created[accepted_id]
    assert by_id[rejected_id]["status"] == "rejected"
    assert by_id[rejected_id]["reason"] == "Reviewed rejected"
    assert by_id[rejected_id]["created_at"] == original_created[rejected_id]


def test_memory_graph_cli_status_suggestions_accept_reject_and_around(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Shared Scope\n\n"
        "Payment settlement and ledger evidence stay local.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Shared Scope\n\n"
        "Use local ledger evidence for payment settlement.\n",
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "init",
        "--mode",
        "document-only",
        "--project-root",
        str(tmp_path),
        "--project-short",
        "Demo",
    ]) == 0
    assert _run_main(["memory", "scan", "--project-root", str(tmp_path)]) == 0
    capsys.readouterr()

    assert _run_main(["memory", "graph", "status", "--project-root", str(tmp_path)]) == 0
    status_output = capsys.readouterr().out
    assert "Memory graph status" in status_output
    assert "memory-graph-contract/v1" in status_output
    assert "document:" in status_output

    assert _run_main([
        "memory",
        "graph",
        "suggestions",
        "--project-root",
        str(tmp_path),
        "--limit",
        "10",
    ]) == 0
    suggestions_output = capsys.readouterr().out
    assert "Memory graph suggestions: suggested" in suggestions_output
    assert "strong_title_or_heading_match" in suggestions_output

    suggestions = _db_rows(
        tmp_path,
        """
        SELECT id, source_id, target_id, type
        FROM correlation_suggestions
        ORDER BY type, source_id
        """,
    )
    accepted = next(row for row in suggestions if row["type"] == "related_heading")
    rejected = next(row for row in suggestions if row["id"] != accepted["id"])

    assert _run_main([
        "memory",
        "graph",
        "accept",
        "--project-root",
        str(tmp_path),
        accepted["id"],
        "--reason",
        "Reviewed relation",
    ]) == 0
    accept_output = capsys.readouterr().out
    assert "Accepted graph suggestion" in accept_output

    accepted_rows = _db_rows(
        tmp_path,
        f"SELECT status, data FROM correlation_suggestions WHERE id = '{accepted['id']}'",
    )
    assert accepted_rows[0]["status"] == "accepted"
    accepted_data = json.loads(accepted_rows[0]["data"])
    assert accepted_data["review"]["reason"] == "Reviewed relation"
    accepted_edges = _db_rows(
        tmp_path,
        f"""
        SELECT type, data
        FROM edges
        WHERE source_id = '{accepted['source_id']}'
          AND target_id = '{accepted['target_id']}'
          AND type = 'mentions'
        """,
    )
    assert accepted_edges
    assert json.loads(accepted_edges[0]["data"])["from_suggestion_id"] == accepted["id"]

    assert _run_main([
        "memory",
        "graph",
        "reject",
        "--project-root",
        str(tmp_path),
        rejected["id"],
        "--reason",
        "False relation",
    ]) == 0
    reject_output = capsys.readouterr().out
    assert "Rejected graph suggestion" in reject_output
    rejected_rows = _db_rows(
        tmp_path,
        f"SELECT status, data FROM correlation_suggestions WHERE id = '{rejected['id']}'",
    )
    assert rejected_rows[0]["status"] == "rejected"
    assert json.loads(rejected_rows[0]["data"])["review"]["reason"] == "False relation"

    assert _run_main([
        "memory",
        "graph",
        "around",
        "--project-root",
        str(tmp_path),
        "docs/plan.md",
        "--depth",
        "2",
    ]) == 0
    around_output = capsys.readouterr().out
    assert "Memory graph around: docs/plan.md" in around_output
    assert "Payment Decision" in around_output
    assert "Suggestions touching center" in around_output

    graph_index = (tmp_path / ".controlcoding" / "views" / "GRAPH_INDEX.md").read_text(encoding="utf-8")
    assert "Accepted correlations: 1" in graph_index
    assert "Rejected correlations: 1" in graph_index
    assert accepted["id"] in graph_index
    assert rejected["id"] in graph_index


def test_memory_graph_export_filters_projection_and_html_viewer(tmp_path, capsys):
    docs = tmp_path / "docs"
    corpus = tmp_path / "corpus"
    docs.mkdir()
    corpus.mkdir()
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Shared Scope\n\n"
        "Payment settlement and ledger evidence stay local.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Shared Scope\n\n"
        "Use local ledger evidence for payment settlement.\n",
        encoding="utf-8",
    )
    (corpus / "runtime-note.txt").write_text(
        "Application-owned memory reference for runtime RAG.",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_decision_add(
        tmp_path,
        "Payment memory decision",
        body="Use local ledger evidence for payment memory.",
        area="Memory",
    ) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "graph",
        "export",
        "--project-root",
        str(tmp_path),
        "--type",
        "chunk",
        "--source",
        "docs",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["projection"]["sourceOfTruth"] is False
    assert payload["filters"]["type"] == ["chunk"]
    assert payload["filters"]["source"] == ["docs"]
    assert payload["nodes"]
    assert {node["type"] for node in payload["nodes"]} == {"chunk"}
    assert all(node["path"].startswith("docs/") for node in payload["nodes"])

    assert _run_main([
        "memory",
        "graph",
        "export",
        "--project-root",
        str(tmp_path),
        "--type",
        "decision",
        "--lifecycle",
        "active",
        "--source",
        "dev",
        "--json",
    ]) == 0
    lifecycle_payload = json.loads(capsys.readouterr().out)
    assert lifecycle_payload["filters"]["lifecycle"] == ["active"]
    assert lifecycle_payload["nodes"]
    assert all(
        node["type"] == "decision" or node["documentType"] == "decision"
        for node in lifecycle_payload["nodes"]
    )
    assert all(node["lifecycle"] == "active" for node in lifecycle_payload["nodes"])

    output_path = tmp_path / "exports" / "graph-view.html"
    assert _run_main([
        "memory",
        "graph",
        "export",
        "--project-root",
        str(tmp_path),
        "--format",
        "html",
        "--output",
        str(output_path),
        "--confidence",
        "strong_title_or_heading_match",
        "--json",
    ]) == 0
    html_payload = json.loads(capsys.readouterr().out)
    assert html_payload["outputPath"] == "exports/graph-view.html"
    assert html_payload["projection"]["derived"] is True
    assert html_payload["suggestions"]
    assert all(
        item["confidence"] == "strong_title_or_heading_match"
        for item in html_payload["suggestions"]
    )
    html_text = output_path.read_text(encoding="utf-8")
    assert "ControlCoding Memory Graph Export" in html_text
    assert "Derived projection only" in html_text


def test_memory_bootstrap_reports_missing_dev_memory_without_writes(tmp_path, capsys):
    assert not (tmp_path / ".controlcoding").exists()
    assert _run_main([
        "memory",
        "bootstrap",
        "--project-root",
        str(tmp_path),
        "--scope",
        "dev",
        "--topic",
        "GraphRAG",
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "read_only_no_sync_no_hidden_writes"
    assert payload["devPlane"]["initialized"] is False
    assert payload["devPlane"]["hasManifest"] is False
    assert payload["devPlane"]["hasDatabase"] is False
    assert payload["projectPlane"]["hasControlWorkRoot"] is False
    assert payload["applicationOwnedMemory"]["referencedComponents"] == 0
    assert any("memory init" in item["command"] for item in payload["recommendations"])
    assert any("did not create" in warning for warning in payload["warnings"])
    assert not (tmp_path / ".controlcoding").exists()
    assert not (tmp_path / ".controlwork").exists()


def test_memory_op_index_reports_missing_memory_without_writes(tmp_path, capsys):
    assert _run_main([
        "memory",
        "op-index",
        "--project-root",
        str(tmp_path),
        "--scope",
        "dev",
        "--topic",
        "GraphRAG",
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "RAG Operations Index"
    assert payload["alias"] == "RAG-O"
    assert payload["kind"] == "operations_index"
    assert payload["ragEngine"] is False
    assert payload["mode"] == "read_only_no_sync_no_hidden_writes"
    assert payload["sourceOfTruth"] is False
    assert payload["planes"]["dev"]["initialized"] is None
    assert payload["graph"]["available"] is False
    assert payload["health"]["state"] == "unknown"
    assert payload["health"]["reason"] == "source_db_absent"
    assert any(item.get("source") == "health-projection" for item in payload["actionQueue"])
    assert any(route["plane"] == "Dev Plane" and "memory retrieve" in route["command"] for route in payload["routes"])
    assert ".controlcoding/**" in payload["workingTree"]["commitPolicy"]["doNotCommit"]
    assert "CONTROLWORK.md" in payload["workingTree"]["commitPolicy"]["releaseExportExclusions"]
    assert not (tmp_path / ".controlcoding").exists()
    assert not (tmp_path / ".controlwork").exists()


def test_memory_import_and_op_index_fallback_when_cc_feature_unavailable(tmp_path):
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    code = (
        "import importlib.abc\n"
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(scripts_dir)!r})\n"
        f"project = Path({str(tmp_path)!r})\n"
        "class BlockCcFeature(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path, target=None):\n"
        "        if fullname == 'cc_feature':\n"
        "            raise ImportError('blocked cc_feature for import-boundary test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, BlockCcFeature())\n"
        "import cc_memory\n"
        "import cc_memory_lib.op_index as op_index\n"
        "payload = op_index._op_index_payload(project, scope='dev', topic='Lazy feature')\n"
        "startup = op_index._startup_payload(project, scope='dev', topic='Lazy feature')\n"
        "print(json.dumps({\n"
        "    'ccFeatureImported': 'cc_feature' in sys.modules,\n"
        "    'facadeHasOpIndex': callable(cc_memory.cmd_memory_op_index),\n"
        "    'payload': payload,\n"
        "    'startup': startup,\n"
        "}))\n"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    data = json.loads(result.stdout)
    payload = data["payload"]
    feature_state = payload["featureState"]
    feature_warning = feature_state["warnings"][0]

    assert data["ccFeatureImported"] is False
    assert data["facadeHasOpIndex"] is True
    assert feature_state["initialized"] is None
    assert feature_state["available"] is False
    assert feature_state["features"] is None
    assert feature_state["currentFeature"] is None
    assert feature_state["blockedFeatureIds"] is None
    assert feature_state["summary"] is None
    assert feature_state["health"]["state"] == "unknown"
    assert feature_state["health"]["reason"] == "feature_status_unavailable"
    assert feature_warning["code"] == "feature_status_unavailable"
    assert "could not be observed" in feature_warning["message"]
    assert feature_warning["error"]["type"] == "ImportError"
    assert "blocked cc_feature" in feature_warning["error"]["message"]
    assert feature_state["error"] == feature_warning["error"]
    assert any(
        isinstance(warning, dict) and warning.get("code") == "feature_status_unavailable"
        for warning in payload["warnings"]
    )
    assert any(
        isinstance(warning, dict) and warning.get("code") == "feature_status_unavailable"
        for warning in data["startup"]["warnings"]
    )


def test_memory_bootstrap_reports_initialized_planes_and_stale_artifacts(tmp_path, capsys):
    docs = tmp_path / "docs"
    corpus = tmp_path / "corpus"
    docs.mkdir()
    corpus.mkdir()
    (docs / "plan.md").write_text(
        "# Search Plan\n\n"
        "## Active Retrieval\n\n"
        "Local sparse search and graph suggestions support explainable retrieval.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Search Decision\n\n"
        "## Active Retrieval\n\n"
        "Use local graph context for explainable retrieval.\n",
        encoding="utf-8",
    )
    (corpus / "runtime-note.txt").write_text(
        "Application-owned memory component reference for app RAG boundaries.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_work_init(tmp_path, project_name="Demo", purpose="Project plane") == 0
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0
    assert cc.cmd_memory_views_generate(tmp_path) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "bootstrap",
        "--project-root",
        str(tmp_path),
        "--scope",
        "dev",
        "--topic",
        "GraphRAG",
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["devPlane"]["initialized"] is True
    assert payload["devPlane"]["sqliteSchemaVersion"] == str(cc_memory.SCHEMA_VERSION)
    assert payload["devPlane"]["counts"]["semanticChunks"] > 0
    assert payload["derivedArtifacts"]["vectors"]["stale"] is False
    assert payload["derivedArtifacts"]["views"]["stale"] is False
    assert payload["derivedArtifacts"]["graphPackets"]["implemented"] is True
    assert payload["derivedArtifacts"]["graphPackets"]["path"] == ".controlcoding/context-packets"
    assert "rag-pack" in payload["derivedArtifacts"]["graphPackets"]["note"]
    assert payload["projectPlane"]["hasControlWorkRoot"] is True
    assert payload["projectPlane"]["hasCanonicalContext"] is True
    assert payload["applicationOwnedMemory"]["referencedComponents"] == 1
    assert "application-owned" in payload["applicationOwnedMemory"]["boundary"].lower()
    assert any(item["path"] == "docs/plan.md" for item in payload["hotDocuments"])
    assert any("work-status" in item["command"] for item in payload["recommendations"])
    assert any("work-context-pack" in item["command"] for item in payload["recommendations"])
    assert all("--scope dev" not in item["command"] for item in payload["recommendations"])


def test_memory_op_index_coordinates_planes_graph_routes_and_commit_hygiene(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "plan.md").write_text(
        "# Search Plan\n\n"
        "## Active Retrieval\n\n"
        "Local sparse search and graph suggestions support explainable retrieval.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Search Decision\n\n"
        "## Active Retrieval\n\n"
        "Use local graph context for explainable retrieval.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_work_init(tmp_path, project_name="Demo", purpose="Project plane") == 0
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0
    assert cc.cmd_memory_views_generate(tmp_path) == 0
    assert cc.cmd_memory_session_start(
        tmp_path,
        topic="GraphRAG",
        mode="continue_previous_work",
        scope="dev",
        session_id="session-op-index-1",
        categories=["graphrag"],
    ) == 0
    assert cc.cmd_memory_session_note(
        tmp_path,
        "session-op-index-1",
        "Regenerate session views after session changes.",
        kind="followup",
    ) == 0
    assert cc_feature.cmd_feature_start(
        tmp_path,
        feature_id="graphrag-op",
        title="GraphRAG operation state",
        objective="Keep GraphRAG operation work bounded.",
        acceptance=["RAG-O shows the active feature."],
        check=["python -m pytest tests/test_cc_memory.py"],
    ) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "op-index",
        "--project-root",
        str(tmp_path),
        "--scope",
        "dev",
        "--topic",
        "GraphRAG",
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "RAG Operations Index"
    assert payload["ragEngine"] is False
    assert payload["planes"]["dev"]["initialized"] is True
    assert payload["planes"]["project"]["present"] is True
    assert payload["planes"]["project"]["actionableSemanticDrift"] is False
    assert payload["indexHealth"]["vectors"]["stale"] is False
    assert payload["indexHealth"]["graphPackets"]["implemented"] is True
    assert payload["graph"]["available"] is True
    assert payload["graph"]["semanticChunks"] > 0
    assert payload["sessions"]["available"] is True
    assert payload["sessions"]["activeCount"] == 1
    assert payload["sessions"]["latestSession"]["id"] == "session-op-index-1"
    assert payload["sessions"]["views"]["stale"] is True
    assert payload["sessions"]["openFollowups"][0]["text"] == "Regenerate session views after session changes."
    assert payload["featureState"]["initialized"] is True
    assert payload["featureState"]["summary"]["activeFeatureIds"] == ["graphrag-op"]
    assert payload["featureState"]["currentFeature"]["state"] == "active"
    assert "available" not in payload["featureState"]
    assert not any(
        isinstance(warning, dict) and warning.get("code") == "feature_status_unavailable"
        for warning in payload["warnings"]
    )
    assert any("memory graph suggestions" in item["command"] for item in payload["actionQueue"])
    assert any("memory session views" in item["command"] and item["writes"] for item in payload["actionQueue"])
    assert any("memory session list" in item["command"] and not item["writes"] for item in payload["actionQueue"])
    assert any("feature show" in item["command"] and not item["writes"] for item in payload["actionQueue"])
    assert any("memory rag-pack" in item["command"] and item["writes"] for item in payload["actionQueue"])
    assert any(route["plane"] == "Project Plane" and "work-context-pack" in route["command"] for route in payload["routes"])
    assert any(route["plane"] == "Session GraphRAG" and "memory session list" in route["command"] for route in payload["routes"])
    assert payload["nextReads"]["hotDocuments"]
    assert ".controlwork/**" in payload["workingTree"]["commitPolicy"]["doNotCommit"]
    assert not (tmp_path / ".controlcoding" / "context-packets").exists()

    assert _run_main([
        "memory",
        "startup",
        "--project-root",
        str(tmp_path),
        "--scope",
        "dev",
        "--topic",
        "GraphRAG",
        "--intent",
        "continue_previous_work",
        "--json",
    ]) == 0
    startup = json.loads(capsys.readouterr().out)
    assert startup["name"] == "Memory Startup Protocol"
    assert startup["readOnly"] is True
    assert startup["hiddenWrites"] is False
    assert startup["intent"] == "continue_previous_work"
    assert startup["status"]["latestSession"]["id"] == "session-op-index-1"
    assert startup["status"]["featureState"]["currentFeature"]["id"] == "graphrag-op"
    assert any("memory op-index" in item["command"] for item in startup["visibleCommands"])
    assert any("memory session show" in item["command"] for item in startup["visibleCommands"])
    assert any("memory session-pack" in item["command"] for item in startup["visibleCommands"])
    assert any("feature status" in item["command"] for item in startup["visibleCommands"])
    assert any("feature show" in item["command"] for item in startup["visibleCommands"])
    assert all(not item["writes"] for item in startup["visibleCommands"])


def test_memory_retrieve_hybrid_ranking_and_reports(tmp_path, capsys):
    docs = tmp_path / "docs"
    legacy = docs / "legacy"
    corpus = tmp_path / "corpus"
    legacy.mkdir(parents=True)
    corpus.mkdir()
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Reconciliation\n\n"
        "Payment settlement reconciliation requires ledger evidence and source citations.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Reconciliation\n\n"
        "Use the local ledger for payment settlement evidence.\n"
        "OPENAI_API_KEY=sk-testsecretvalue1234567890\n",
        encoding="utf-8",
    )
    (legacy / "old-payment.md").write_text(
        "# Old Payment Plan\n\n"
        "Legacy settlement process kept for audit history.\n",
        encoding="utf-8",
    )
    (corpus / "runtime-note.txt").write_text(
        "Application-owned memory component for payment RAG runtime data.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0
    assert cc.cmd_memory_session_start(
        tmp_path,
        topic="Payment settlement ledger evidence",
        mode="continue_previous_work",
        scope="dev",
        session_id="session-retrieve-payment",
        categories=["payment"],
        summary="Reviewed payment settlement ledger evidence and linked the decision document.",
    ) == 0
    assert cc.cmd_memory_session_link(
        tmp_path,
        "session-retrieve-payment",
        link_type="changes_doc",
        target="docs/decision.md",
        target_type="doc",
    ) == 0
    assert cc.cmd_memory_session_link(
        tmp_path,
        "session-retrieve-payment",
        link_type="produced_commit",
        target="abc1234",
        target_type="commit",
    ) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "payment settlement ledger evidence",
        "--limit",
        "8",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["vectorIndex"]["used"] is True
    assert payload["vectorIndex"]["prefilterMode"] == "postings"
    assert payload["vectorIndex"]["usedFullVectorScan"] is False
    assert payload["vectorIndex"]["scoredVectorRows"] <= payload["vectorIndex"]["prefilterCap"]
    assert "prefilterMode" not in payload["candidateFilter"]
    assert "sparse_vector" in payload["signalsUsed"]
    assert payload["matches"]
    assert any(match["path"] == "docs/decision.md" for match in payload["matches"])
    session_matches = [match for match in payload["matches"] if match["recordType"] == "session"]
    assert session_matches
    assert session_matches[0]["id"] == "session-retrieve-payment"
    assert "session trace not canonical" in session_matches[0]["demotions"]
    assert any(match["signals"]["sparseVector"]["points"] > 0 for match in payload["matches"])
    assert any(match["signals"]["graphProximity"]["points"] > 0 for match in payload["matches"])
    assert any(match["demotions"] for match in payload["matches"])
    assert payload["demotionReport"]["counts"].get("lifecycle_stale", 0) >= 1
    assert payload["demotionReport"]["counts"].get("application-owned_memory_boundary", 0) >= 1
    assert payload["demotionReport"]["counts"].get("session_trace_not_canonical", 0) >= 1
    assert any("docs/decision.md" in match["citation"] for match in payload["matches"])

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "payment settlement ledger evidence",
        "--limit",
        "3",
    ]) == 0
    output = capsys.readouterr().out
    assert "Memory retrieve: payment settlement ledger evidence" in output
    assert "Exclusion and demotion report" in output


def test_memory_retrieve_uses_fts5_candidate_filter_when_available(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    for index in range(80):
        (docs / f"filler-{index:02d}.md").write_text(
            f"# Filler {index}\n\nToolbar density and menu spacing note {index}.\n",
            encoding="utf-8",
        )
    (docs / "target.md").write_text(
        "# Indexed Retrieval Target\n\n"
        "Needleledger settlement audit evidence belongs in the indexed retrieval target.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    with memory_store._memory_connection(tmp_path) as conn:
        if not memory_retrieve._fts5_available(conn):
            pytest.skip("SQLite FTS5 is not available in this runtime")
    assert cc.cmd_memory_scan(tmp_path) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "needleledger settlement audit evidence",
        "--limit",
        "5",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    _assert_default_scoring_payload(payload["scoring"])
    candidate_filter = payload["candidateFilter"]
    assert candidate_filter["mode"] == "fts5"
    assert candidate_filter["fts5Available"] is True
    assert candidate_filter["usedFullScan"] is False
    assert candidate_filter["indexMissing"] is False
    assert candidate_filter["indexStale"] is False
    assert candidate_filter["candidateCountBeforeScoring"] <= candidate_filter["candidateCap"]
    assert candidate_filter["health"]["liveRecordCount"] > candidate_filter["candidateCountBeforeScoring"]
    assert any(match["path"] == "docs/target.md" for match in payload["matches"])


def test_memory_retrieve_uses_sql_inverted_fallback_when_fts_unavailable(tmp_path, capsys, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# SQL Fallback Target\n\n"
        "Fallbackneedle candidate retrieval uses the deterministic SQL inverted index.\n",
        encoding="utf-8",
    )
    for index in range(12):
        (docs / f"unrelated-{index}.md").write_text(
            f"# Unrelated {index}\n\nInterface polish note {index}.\n",
            encoding="utf-8",
        )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    monkeypatch.setattr(memory_retrieve, "_fts5_available", lambda _conn: False)
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "fallbackneedle deterministic inverted",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    candidate_filter = payload["candidateFilter"]
    assert candidate_filter["mode"] == "sql_inverted"
    assert candidate_filter["fts5Available"] is False
    assert candidate_filter["fallbackReason"] == "fts5_unavailable"
    assert candidate_filter["usedFullScan"] is False
    assert any(match["path"] == "docs/target.md" for match in payload["matches"])


def test_memory_retrieve_protects_exact_matches_and_caps_ordinary_candidates(tmp_path, capsys, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    for index in range(20):
        (docs / f"alpha-filler-{index:02d}.md").write_text(
            f"# Alpha Filler {index}\n\nalpha alpha alpha filler record {index}.\n",
            encoding="utf-8",
        )
    (docs / "z-target.md").write_text(
        "# alpha\n\n"
        "This exact title match must survive the ordinary candidate cap.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    monkeypatch.setattr(memory_retrieve, "_fts5_available", lambda _conn: False)
    monkeypatch.setattr(memory_retrieve, "CANDIDATE_FILTER_MIN_CAP", 3)
    monkeypatch.setattr(memory_retrieve, "CANDIDATE_FILTER_MAX_CAP", 3)
    monkeypatch.setattr(memory_retrieve, "CANDIDATE_FILTER_LIMIT_MULTIPLIER", 1)
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "alpha",
        "--limit",
        "8",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    candidate_filter = payload["candidateFilter"]
    assert candidate_filter["candidateCap"] == 3
    assert candidate_filter["ordinaryCandidateCountBeforeScoring"] <= 3
    assert candidate_filter["protectedCandidateCount"] >= 1
    assert candidate_filter["usedFullScan"] is False
    assert any(match["path"] == "docs/z-target.md" for match in payload["matches"])


def test_memory_retrieve_expands_candidates_with_bounded_graph_neighbors(tmp_path, capsys, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "seed.md").write_text(
        "# Seed Ledger\n\n"
        "Seedalpha settlement evidence is the only lexical query match.\n",
        encoding="utf-8",
    )
    (docs / "neighbor.md").write_text(
        "# Neighbor Graph\n\n"
        "This adjacent document has no lexical retrieval token from the query.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    entity_ids = {
        row["path"]: row["id"]
        for row in _db_rows(
            tmp_path,
            "SELECT id, path FROM entities WHERE path IN ('docs/seed.md', 'docs/neighbor.md')",
        )
    }
    seed_id = entity_ids["docs/seed.md"]
    neighbor_id = entity_ids["docs/neighbor.md"]
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute(
            """
            INSERT INTO edges(id, source_id, target_id, type, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "edge-seed-neighbor",
                seed_id,
                neighbor_id,
                "references",
                "{}",
                "2026-06-20T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO correlation_suggestions(
              id, source_id, target_id, type, confidence, reason, status, data, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "suggestion-seed-neighbor",
                seed_id,
                neighbor_id,
                "related_terms",
                "high",
                "Manual test suggestion linking the lexical seed to a graph-only neighbor.",
                "suggested",
                "{}",
                "2026-06-20T00:00:00Z",
                "2026-06-20T00:00:00Z",
            ),
        )

    original_memory_connection = memory_retrieve._readonly_memory_connection
    traced_sql: list[str] = []

    @contextmanager
    def traced_memory_connection(project):
        with original_memory_connection(project) as conn:
            conn.set_trace_callback(traced_sql.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    original_load_adjacency = memory_retrieve._load_adjacency
    adjacency_calls: list[set[str]] = []

    def recording_load_adjacency(conn, node_ids):
        adjacency_calls.append(set(node_ids))
        return original_load_adjacency(conn, node_ids)

    monkeypatch.setattr(memory_retrieve, "_readonly_memory_connection", traced_memory_connection)
    monkeypatch.setattr(memory_retrieve, "_load_adjacency", recording_load_adjacency)
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "seedalpha",
        "--limit",
        "10",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    candidate_filter = payload["candidateFilter"]
    assert candidate_filter["usedFullScan"] is False
    assert candidate_filter["graphAdjacencyMode"] == "bounded"
    assert candidate_filter["graphExpansionCap"] > 0
    assert candidate_filter["graphExpansionCount"] > 0
    assert candidate_filter["candidateCountBeforeScoring"] > candidate_filter["candidateCountBeforeGraphExpansion"]

    neighbor_matches = [match for match in payload["matches"] if match["path"] == "docs/neighbor.md"]
    assert neighbor_matches
    neighbor_match = neighbor_matches[0]
    assert neighbor_match["signals"]["text"]["points"] == 0.0
    assert neighbor_match["signals"]["graphProximity"]["points"] > 0.0
    assert neighbor_match["edgesUsed"]

    assert adjacency_calls
    assert all(call for call in adjacency_calls)
    edge_reads = [" ".join(sql.split()) for sql in traced_sql if "FROM edges" in sql]
    suggestion_reads = [" ".join(sql.split()) for sql in traced_sql if "FROM correlation_suggestions" in sql]
    assert edge_reads
    assert suggestion_reads
    assert all(("WHERE source_id IN" in sql or "WHERE source_id =" in sql) and "LIMIT" in sql for sql in edge_reads)
    assert all(
        "WHERE status IN" in sql
        and ("source_id IN" in sql or "source_id =" in sql)
        and "LIMIT" in sql
        for sql in suggestion_reads
    )


def test_memory_retrieve_reports_missing_index_explicitly(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# Missing Index Target\n\nMissingindex telemetry must be explicit.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute("DELETE FROM retrieval_records")
        conn.execute("DELETE FROM retrieval_terms")
        conn.execute("DELETE FROM retrieval_term_stats")
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "missingindex telemetry",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    candidate_filter = payload["candidateFilter"]
    assert candidate_filter["mode"] == "full_scan"
    assert candidate_filter["indexMissing"] is True
    assert candidate_filter["usedFullScan"] is True
    assert candidate_filter["fallbackReason"] == "retrieval_index_missing"
    assert any(match["path"] == "docs/target.md" for match in payload["matches"])


def test_memory_retrieve_reports_stale_index_explicitly(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# Stale Index Target\n\nStaleindex telemetry must be explicit.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute("UPDATE retrieval_records SET content_hash = 'stale-for-test'")
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "staleindex telemetry",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    candidate_filter = payload["candidateFilter"]
    assert candidate_filter["mode"] == "full_scan"
    assert candidate_filter["indexStale"] is True
    assert candidate_filter["usedFullScan"] is True
    assert candidate_filter["fallbackReason"] == "retrieval_index_stale"
    assert "content_hash_mismatch" in candidate_filter["health"]["staleReasons"]
    assert any(match["path"] == "docs/target.md" for match in payload["matches"])


def test_memory_rag_pack_outputs_markdown_json_citations_and_edges(tmp_path, capsys):
    docs = tmp_path / "docs"
    legacy = docs / "legacy"
    docs.mkdir()
    legacy.mkdir()
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Reconciliation\n\n"
        "Payment settlement reconciliation requires ledger evidence and citations.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Reconciliation\n\n"
        "Use the local ledger for payment settlement evidence.\n"
        "OPENAI_API_KEY=sk-testsecretvalue1234567890\n",
        encoding="utf-8",
    )
    (legacy / "old-payment.md").write_text(
        "# Old Payment Plan\n\n"
        "Legacy settlement process kept for warning coverage.\n",
        encoding="utf-8",
    )
    (docs / "unrelated.md").write_text(
        "# UX Notes\n\n"
        "Toolbar spacing and menu density are tracked separately.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0
    capsys.readouterr()

    output_path = tmp_path / ".controlcoding" / "context-packets" / "payment-rag.md"
    assert _run_main([
        "memory",
        "rag-pack",
        "--project-root",
        str(tmp_path),
        "payment settlement ledger evidence",
        "--limit",
        "8",
        "--output",
        str(output_path),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["packetMarkdown"].startswith("# ControlCoding GraphRAG Packet")
    assert payload["privacyReceipt"]["enabled"] is True
    assert payload["citations"]
    assert payload["evidenceRefs"]
    assert all(citation["nodeId"].startswith("ccref:") for citation in payload["citations"])
    assert all(citation["evidenceRefId"] == citation["nodeId"] for citation in payload["citations"])
    assert payload["evidenceRefs"][0]["nodeId"] == payload["citations"][0]["nodeId"]
    assert payload["edgesUsed"]
    assert payload["warnings"]
    assert payload["excludedRecords"]
    assert payload["nextReads"]
    assert payload["outputPath"] == ".controlcoding/context-packets/payment-rag.md"
    assert any("docs/decision.md" in citation["citation"] for citation in payload["citations"])
    assert "## Source Citations" in output_path.read_text(encoding="utf-8")
    assert "## Edges Used" in payload["packetMarkdown"]
    assert "Evidence ref:" in payload["packetMarkdown"]

    node_id = next(citation["nodeId"] for citation in payload["citations"] if citation["path"] == "docs/decision.md")
    assert _run_main([
        "memory",
        "evidence",
        "show",
        "--project-root",
        str(tmp_path),
        node_id,
        "--json",
    ]) == 0
    evidence_payload = json.loads(capsys.readouterr().out)
    assert evidence_payload["ok"] is True
    assert evidence_payload["nodeId"] == node_id
    assert evidence_payload["readOnly"] is True
    assert evidence_payload["source"]["available"] is True
    assert evidence_payload["path"].startswith("docs/")
    evidence_json = json.dumps(evidence_payload)
    assert "sk-testsecretvalue1234567890" not in evidence_json
    assert "[REDACTED:env_secret_assignment]" in evidence_json
    assert evidence_payload["privacyReceipt"]["replacementCount"] >= 1

    assert _run_main([
        "memory",
        "evidence",
        "list",
        "--project-root",
        str(tmp_path),
        "--record-type",
        "chunk",
        "--limit",
        "5",
        "--json",
    ]) == 0
    evidence_list = json.loads(capsys.readouterr().out)
    assert evidence_list["ok"] is True
    assert evidence_list["storage"] == "derived_from_memory_index_no_table"
    assert any(item["nodeId"].startswith("ccref:dev:chunk:") for item in evidence_list["items"])

    assert _run_main([
        "memory",
        "bootstrap",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    bootstrap_payload = json.loads(capsys.readouterr().out)
    graph_packets = bootstrap_payload["derivedArtifacts"]["graphPackets"]
    assert graph_packets["implemented"] is True
    assert graph_packets["fileCount"] == 1
    assert graph_packets["latestFile"]["path"] == ".controlcoding/context-packets/payment-rag.md"

    assert _run_main([
        "memory",
        "rag-pack",
        "--project-root",
        str(tmp_path),
        "payment settlement ledger evidence",
        "--limit",
        "3",
    ]) == 0
    markdown_output = capsys.readouterr().out
    assert "# ControlCoding GraphRAG Packet" in markdown_output
    assert "## Next Reads" in markdown_output


def test_memory_cross_pack_federates_planes_without_hidden_writes(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "dev.md").write_text(
        "# Payment Dev Evidence\n\n"
        "Payment settlement graph retrieval needs Dev Plane code evidence.\n",
        encoding="utf-8",
    )
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0
    assert cc.cmd_memory_work_init(tmp_path, project_name="Demo Work", purpose="Project plane") == 0
    plan = tmp_path / ".controlwork" / "memory" / "plans" / "payment-plan.md"
    plan.write_text(
        "# Payment Plan\n\n"
        "- **Area**: plans\n"
        "- **Lifecycle**: active\n\n"
        "## Body\n\n"
        "Payment settlement graph requirements belong to the Project Plane.\n",
        encoding="utf-8",
    )
    assert cc.cmd_memory_session_start(
        tmp_path,
        topic="payment settlement graph",
        session_id="session-cross-plane",
        summary="Cross-plane packet fixture.",
    ) == 0
    assert cc.cmd_memory_session_link(
        tmp_path,
        "session-cross-plane",
        "changes_doc",
        "docs/dev.md",
        target_type="doc",
    ) == 0
    capsys.readouterr()

    packet_root = tmp_path / ".controlcoding" / "context-packets"
    before_packets = set(packet_root.glob("*.md")) if packet_root.exists() else set()
    assert _run_main([
        "memory",
        "cross-pack",
        "--project-root",
        str(tmp_path),
        "payment settlement graph",
        "--limit",
        "5",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    after_packets = set(packet_root.glob("*.md")) if packet_root.exists() else set()
    assert after_packets == before_packets
    assert payload["packetType"] == "controlcoding-cross-plane-graphrag-packet/v1"
    assert payload["mode"] == "federated_read_only_no_sync_no_hidden_writes"
    assert payload["sourceOfTruth"] is False
    assert payload["planes"]["operations"]["alias"] == "RAG-O"
    assert payload["planes"]["operations"]["ragEngine"] is False
    assert payload["planes"]["project"]["syncPolicy"] == "manual_explicit_no_pull_no_push"
    assert payload["privacyReceipt"]["enabled"] is True
    assert payload["evidenceRefs"]
    assert any(item["nodeId"].startswith("ccref:") for item in payload["evidenceRefs"])
    planes = {citation["plane"] for citation in payload["citations"]}
    assert {"Dev Plane", "Project Plane", "Session GraphRAG"}.issubset(planes)
    assert any("work-context-pack" in item["target"] for item in payload["nextReads"])
    assert "# Cross-Plane GraphRAG Packet" in payload["packetMarkdown"]


def test_memory_eval_runs_structured_fixture_without_secret_leak(tmp_path, capsys):
    assert _run_main([
        "memory",
        "eval",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    payload_json = json.dumps(payload)
    check_ids = {check["id"] for check in payload["checks"]}

    assert payload["ok"] is True
    assert payload["schemaVersion"] == "cc-memory-eval/v1"
    assert payload["summary"]["failed"] == 0
    assert payload["secretValuesIncluded"] is False
    assert "sk-evalsecretvalue1234567890" not in payload_json
    assert payload["fixtureKept"] is False
    assert payload["fixturePath"] == ""
    assert not (tmp_path / ".controlcoding" / "tmp" / "memory-eval").exists()
    assert {
        "retrieval_target",
        "sparse_vector_signal",
        "bounded_vector_prefilter",
        "ranking_expected_document_order",
        "semantic_ranking_expected_order",
        "lifecycle_demotion",
        "application_boundary_demotion",
        "session_trace_demotion",
        "rag_pack_evidence_refs",
        "rag_pack_privacy_scrub",
        "evidence_drilldown",
        "evidence_privacy_scrub",
        "cross_plane_boundaries",
        "cross_plane_no_hidden_packet_write",
        "cross_plane_evidence_refs",
        "rago_evidence_route",
    }.issubset(check_ids)
    ranking_check = next(check for check in payload["checks"] if check["id"] == "ranking_expected_document_order")
    assert ranking_check["evidence"]["expectedPrefix"] == ["docs/decision.md", "docs/plan.md"]
    assert ranking_check["evidence"]["actualDocumentPaths"][:2] == ["docs/decision.md", "docs/plan.md"]
    semantic_check = next(check for check in payload["checks"] if check["id"] == "semantic_ranking_expected_order")
    assert semantic_check["evidence"]["adapter"]["used"] is True
    assert semantic_check["evidence"]["adapter"]["adapter"] == "local_runtime_v1"
    assert semantic_check["evidence"]["actualDocumentPaths"][:2] == ["docs/decision.md", "docs/plan.md"]
    assert semantic_check["evidence"]["semanticPointsByPath"]["docs/decision.md"] > 0
    assert semantic_check["evidence"]["scoringConfigVersion"] == "cc-memory-retrieval-scoring/v1"
    assert payload["observations"]["ragEvidenceRefCount"] >= 1
    assert payload["observations"]["crossPlaneCitationCount"] >= 3
    top_paths = payload["observations"]["retrievalTopPaths"]
    assert top_paths[0] == "docs/decision.md"
    assert top_paths.index("docs/decision.md") < top_paths.index("docs/plan.md")
    assert "session-memory-eval-payment" in top_paths[:4]
    assert payload["observations"]["semanticAdapter"]["used"] is True
    assert payload["observations"]["semanticAdapter"]["adapter"] == "local_runtime_v1"
    assert "docs/decision.md" in payload["observations"]["semanticRankingPaths"]
    _assert_default_scoring_payload(payload["observations"]["scoringConfig"])


def test_memory_semantic_status_defaults_and_explicit_adapters(tmp_path, capsys, monkeypatch):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "semantic",
        "status",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["interfaceVersion"] == "cc-semantic-adapter/v1"
    assert payload["activeAdapter"] == "local_sparse_v1"
    assert payload["policy"]["officialApiExplicitOnly"] is True
    adapters = {adapter["id"]: adapter for adapter in payload["adapters"]}
    assert adapters["local_sparse_v1"]["available"] is True
    assert adapters["local_runtime_v1"]["available"] is False
    assert adapters["official_api_v1"]["available"] is False
    assert adapters["official_api_v1"]["explicitOnly"] is True

    monkeypatch.delenv("CC_MEMORY_TEST_MISSING_KEY", raising=False)
    config_path = tmp_path / ".controlcoding" / "memory" / "semantic_adapters.json"
    config_path.write_text(
        json.dumps({
            "activeAdapter": "local_runtime_v1",
            "localRuntime": {
                "enabled": True,
                "command": "semantic-runtime --stdio",
                "timeoutSeconds": 20,
            },
            "officialApi": {
                "enabled": True,
                "provider": "openai",
                "model": "text-embedding-3-small",
                "apiKeyEnv": "CC_MEMORY_TEST_MISSING_KEY",
            },
        }),
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "semantic",
        "status",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    configured = json.loads(capsys.readouterr().out)
    configured_adapters = {adapter["id"]: adapter for adapter in configured["adapters"]}
    assert configured["activeAdapter"] == "local_runtime_v1"
    assert configured["hasConfig"] is True
    assert configured_adapters["local_runtime_v1"]["available"] is True
    assert configured_adapters["official_api_v1"]["configured"] is True
    assert configured_adapters["official_api_v1"]["available"] is False
    assert configured_adapters["official_api_v1"]["apiKeyAvailable"] is False


def test_memory_retrieve_uses_explicit_local_runtime_semantic_adapter(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "semantic.md").write_text(
        "# Runtime Semantic Adapter\n\n"
        "The semantic bridge target explains local runtime scoring.\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "semantic_runtime.py"
    runtime.write_text(
        "import json, sys\n"
        "payload = json.loads(sys.stdin.read())\n"
        "scores = []\n"
        "for candidate in payload.get('candidates', []):\n"
        "    text = candidate.get('text', '')\n"
        "    if 'semantic bridge target' in text:\n"
        "        scores.append({'id': candidate['id'], 'score': 0.91, 'reason': 'fixture semantic match'})\n"
        "print(json.dumps({'scores': scores}))\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    config_path = tmp_path / ".controlcoding" / "memory" / "semantic_adapters.json"
    config_path.write_text(
        json.dumps({
            "activeAdapter": "local_runtime_v1",
            "localRuntime": {
                "enabled": True,
                "command": [sys.executable, str(runtime)],
                "timeoutSeconds": 10,
                "maxCandidates": 25,
            },
        }),
        encoding="utf-8",
    )
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "semantic bridge",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["semanticAdapter"]["used"] is True
    assert payload["semanticAdapter"]["adapter"] == "local_runtime_v1"
    assert "semantic" in payload["signalsUsed"]
    assert any(match["signals"]["semantic"]["points"] > 0 for match in payload["matches"])
    assert any("fixture semantic match" in reason for match in payload["matches"] for reason in match["reasons"])


def test_memory_note_idea_decision_sync_report_and_context(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "memory.md").write_text("# Memory Design\n", encoding="utf-8")
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    capsys.readouterr()

    assert cc.cmd_memory_note_add(tmp_path, "Indexing note", body="Scan stores metadata only.", area="Memory") == 0
    assert cc.cmd_memory_idea_add(tmp_path, "Visual wiki later", body="Out of scope for this release.", area="Memory") == 0
    assert cc.cmd_memory_decision_add(
        tmp_path,
        "Use SQLite memory",
        body="SQLite is local and available in stdlib.",
        rationale="The store must stay project-local.",
        area="Memory",
    ) == 0
    assert cc.cmd_memory_context(tmp_path, "memory") == 0
    context_output = capsys.readouterr().out
    assert "Use SQLite memory" in context_output
    assert "Visual wiki later" in context_output

    assert cc.cmd_memory_dev_context_pack(
        tmp_path,
        scope="implementation",
        topic="memory",
        json_output=True,
    ) == 0
    dev_packets = list((tmp_path / ".controlcoding" / "context-packets").glob("*.md"))
    assert dev_packets
    assert "ControlCoding Dev Context Packet" in dev_packets[0].read_text(encoding="utf-8")

    decision_log = (tmp_path / ".controlcoding" / "logs" / "decision_log.jsonl").read_text(encoding="utf-8")
    assert "record_decision" in decision_log

    assert cc.cmd_memory_views_generate(tmp_path) == 0
    idea_inbox = (tmp_path / ".controlcoding" / "views" / "IDEA_INBOX.md").read_text(encoding="utf-8")
    assert "Visual wiki later" in idea_inbox

    assert cc.cmd_memory_sync_report(tmp_path) == 0
    report = capsys.readouterr().out
    assert "Memory sync:" in report
    assert "- Impact context checked: yes" in report
    assert "- New decisions recorded: 1" in report
    assert "- Views regenerated: yes" in report


def test_memory_dev_context_pack_stdout_json_is_pure_json(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "memory.md").write_text("# Memory Design\n", encoding="utf-8")
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_note_add(tmp_path, "Indexing note", body="Scan stores metadata only.", area="Memory") == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "dev-context-pack",
        "--project-root", str(tmp_path),
        "--scope", "implementation",
        "--topic", "memory",
        "--stdout",
        "--json",
    ]) == 0
    payload = _assert_single_json_stdout(capsys.readouterr())
    assert payload["ok"] is True
    assert "ControlCoding Dev Context Packet: memory" in payload["packetMarkdown"]

    assert _run_main([
        "memory",
        "dev-context-pack",
        "--project-root", str(tmp_path),
        "--scope", "implementation",
        "--topic", "memory",
        "--stdout",
    ]) == 0
    _assert_plain_text_stdout(capsys.readouterr(), "# ControlCoding Dev Context Packet: memory")


def test_memory_consult_and_agent_run_records_feed_ledgers_and_report(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_consult_record(
        tmp_path,
        summary="Manual review recommends consult records stay non-authoritative.",
        backend="manual",
        status="accepted",
        decision_outcome="accepted",
    ) == 0
    assert cc.cmd_memory_agent_run_record(
        tmp_path,
        role="reviewer",
        host="codex",
        task="Inspect memory engine",
        output_summary="Verified that agent runs do not own canonical truth.",
        changed_files=["scripts/cc_memory.py"],
        verification=["pytest targeted"],
    ) == 0
    capsys.readouterr()

    assert cc.cmd_memory_views_generate(tmp_path) == 0
    consult_ledger = (tmp_path / ".controlcoding" / "views" / "CONSULT_LEDGER.md").read_text(encoding="utf-8")
    agent_ledger = (tmp_path / ".controlcoding" / "views" / "AGENT_RUN_LEDGER.md").read_text(encoding="utf-8")
    assert "non-authoritative" in consult_ledger
    assert "Inspect memory engine" in agent_ledger
    assert "do not own canonical truth" in consult_ledger
    assert "do not own canonical truth" in agent_ledger

    assert cc.cmd_memory_sync_report(tmp_path) == 0
    report = capsys.readouterr().out
    assert "- Consults or agent runs recorded: 2" in report
    assert "not canonical ControlCoding truth" in report
    assert "record_consult" in (tmp_path / ".controlcoding" / "logs" / "consult_log.jsonl").read_text(encoding="utf-8")
    assert "record_agent_run" in (tmp_path / ".controlcoding" / "logs" / "agent_run_log.jsonl").read_text(encoding="utf-8")


def test_memory_commands_route_through_main(tmp_path):
    assert _run_main([
        "memory",
        "init",
        "--mode",
        "document-only",
        "--project-root",
        str(tmp_path),
        "--project-short",
        "Demo",
    ]) == 0
    assert _run_main([
        "memory",
        "decision",
        "add",
        "CLI decision",
        "--body",
        "The nested memory parser works.",
        "--area",
        "CLI",
        "--project-root",
        str(tmp_path),
    ]) == 0
    assert _run_main([
        "memory",
        "views",
        "generate",
        "--project-root",
        str(tmp_path),
    ]) == 0

    rows = _db_rows(tmp_path, "SELECT id, title FROM entities WHERE type = 'decision'")
    assert len(rows) == 1
    assert rows[0]["title"] == "CLI decision"


def test_memory_session_cli_records_links_notes_and_closes(tmp_path, capsys):
    assert _run_main([
        "memory",
        "init",
        "--mode",
        "document-only",
        "--project-root",
        str(tmp_path),
        "--project-short",
        "Demo",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "start",
        "--project-root",
        str(tmp_path),
        "--id",
        "session-test-1",
        "--topic",
        "Session GraphRAG",
        "--mode",
        "continue_previous_work",
        "--category",
        "graphrag",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "link",
        "--project-root",
        str(tmp_path),
        "session-test-1",
        "--commit",
        "abc1234",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "link",
        "--project-root",
        str(tmp_path),
        "session-test-1",
        "--type",
        "changes_doc",
        "--target",
        "docs/session-graphrag-plan.md",
        "--target-type",
        "doc",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "link",
        "--project-root",
        str(tmp_path),
        "session-test-1",
        "--type",
        "changes_file",
        "--target",
        "scripts/cc_memory_lib/sessions.py",
        "--target-type",
        "file",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "note",
        "--project-root",
        str(tmp_path),
        "session-test-1",
        "Port the portable subset later.",
        "--kind",
        "followup",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "close",
        "--project-root",
        str(tmp_path),
        "session-test-1",
        "--status",
        "needs_followup",
        "--summary",
        "Session CLI base is implemented.",
        "--decision",
        "Keep ControlWork port manual.",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "list",
        "--project-root",
        str(tmp_path),
        "--topic",
        "session",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "show",
        "--project-root",
        str(tmp_path),
        "session-test-1",
    ]) == 0
    assert _run_main([
        "memory",
        "session",
        "views",
        "--project-root",
        str(tmp_path),
    ]) == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "session-pack",
        "--project-root",
        str(tmp_path),
        "--topic",
        "Session",
        "--status",
        "needs_followup",
        "--output",
        ".controlcoding/context-packets/session-test.md",
        "--json",
    ]) == 0
    packet_payload = json.loads(capsys.readouterr().out)
    assert packet_payload["packetType"] == "session-graphrag-packet/v1"
    assert packet_payload["citations"][0]["id"] == "session-test-1"
    assert packet_payload["edgesUsed"]
    assert "docs/session-graphrag-plan.md" in packet_payload["nextReads"]
    assert "Session GraphRAG Packet" in packet_payload["markdown"]
    assert (tmp_path / ".controlcoding" / "context-packets" / "session-test.md").exists()

    rows = _db_rows(tmp_path, "SELECT * FROM session_records WHERE id = 'session-test-1'")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "needs_followup"
    assert row["summary"] == "Session CLI base is implemented."
    assert json.loads(row["categories"]) == ["graphrag"]
    assert json.loads(row["commits"]) == ["abc1234"]
    assert json.loads(row["docs_changed"]) == ["docs/session-graphrag-plan.md"]
    assert json.loads(row["files_changed"]) == ["scripts/cc_memory_lib/sessions.py"]
    assert "Port the portable subset later." in json.loads(row["followups"])
    assert "Keep ControlWork port manual." in json.loads(row["decisions"])

    edge_rows = _db_rows(tmp_path, "SELECT type, target FROM session_edges ORDER BY type")
    assert [(edge["type"], edge["target"]) for edge in edge_rows] == [
        ("changes_doc", "docs/session-graphrag-plan.md"),
        ("changes_file", "scripts/cc_memory_lib/sessions.py"),
        ("produced_commit", "abc1234"),
    ]
    event_rows = _db_rows(tmp_path, "SELECT command FROM events ORDER BY timestamp")
    assert "cc memory session start" in [row["command"] for row in event_rows]
    assert "cc memory session close" in [row["command"] for row in event_rows]
    assert "cc memory session views" in [row["command"] for row in event_rows]

    session_index = (tmp_path / ".controlcoding" / "views" / "SESSION_INDEX.md").read_text(encoding="utf-8")
    by_commit = (tmp_path / ".controlcoding" / "views" / "SESSION_BY_COMMIT.md").read_text(encoding="utf-8")
    by_file = (tmp_path / ".controlcoding" / "views" / "SESSION_BY_FILE.md").read_text(encoding="utf-8")
    followups = (tmp_path / ".controlcoding" / "views" / "SESSION_FOLLOWUPS.md").read_text(encoding="utf-8")
    handoff = (tmp_path / ".controlcoding" / "views" / "SESSION_HANDOFF.md").read_text(encoding="utf-8")
    assert "session-test-1" in session_index
    assert "abc1234" in by_commit
    assert "scripts/cc_memory_lib/sessions.py" in by_file
    assert "Port the portable subset later." in followups
    assert "Recommended Next Reads" in handoff
    view_rows = _db_rows(tmp_path, "SELECT path FROM views WHERE path LIKE '.controlcoding/views/SESSION_%'")
    assert len(view_rows) == 9


def test_memory_chunks_routes_through_main(tmp_path, capsys):
    (tmp_path / "README.md").write_text("# Demo\n\n## Usage\n\nRun the tool.\n", encoding="utf-8")
    assert _run_main([
        "memory",
        "init",
        "--mode",
        "document-only",
        "--project-root",
        str(tmp_path),
        "--project-short",
        "Demo",
    ]) == 0
    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
    ]) == 0
    assert _run_main([
        "memory",
        "chunks",
        "--project-root",
        str(tmp_path),
        "README.md",
    ]) == 0

    output = capsys.readouterr().out
    assert "Memory chunks: README.md" in output
    assert "Demo > Usage" in output


def test_memory_chunk_continuation_prefers_safe_boundaries_near_limit():
    first_line = "alpha " * 12
    newline_content = first_line + "\n" + ("beta " * 40)

    newline_parts = memory_chunks._continuation_parts(newline_content, max_chars=80)

    assert newline_parts[0] == first_line.strip()
    assert "".join("".join(newline_parts).split()) == "".join(newline_content.split())

    sentence_prefix = ("alpha " * 10) + "Done."
    sentence_content = sentence_prefix + " " + ("beta " * 40)

    sentence_parts = memory_chunks._continuation_parts(sentence_content, max_chars=80)

    assert sentence_parts[0].endswith("Done.")
    assert "".join("".join(sentence_parts).split()) == "".join(sentence_content.split())


def test_memory_chunk_continuation_falls_back_to_fixed_offset_without_boundaries():
    content = "x" * 205

    parts = memory_chunks._continuation_parts(content, max_chars=80)

    assert [len(part) for part in parts] == [80, 80, 45]
    assert "".join(parts) == content


def test_memory_scan_records_decoding_provenance_for_utf8_replacement(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bad-bytes.txt").write_bytes(
        b"# Bad Bytes\n\nRecoverable caf\xe9 evidence \xff stays indexed.\n"
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT metadata, content_preview
        FROM semantic_chunks
        WHERE source_path = 'docs/bad-bytes.txt'
        ORDER BY ordinal, sequence_index
        """,
    )
    assert chunk_rows
    assert any("\ufffd" in row["content_preview"] for row in chunk_rows)
    for row in chunk_rows:
        metadata = json.loads(row["metadata"])
        assert metadata["decoding_provenance"] == {
            "encoding": "utf-8",
            "errors": "replace",
            "fallback_used": True,
            "replacement_character_present": True,
        }


def test_memory_scanner_safe_read_text_metadata_handles_oserror(tmp_path, monkeypatch):
    target = tmp_path / "unreadable.md"
    target.write_text("# Unreadable\n", encoding="utf-8")
    original_open = Path.open

    def fake_open(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == target and "r" in mode and "b" not in mode:
            raise OSError("blocked test read")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)

    assert memory_scanner._safe_read_text_with_metadata(target) == ("", {})
    assert memory_scanner._safe_read_text(target) == ""


def test_memory_scan_indexes_large_file_from_bounded_text_sample(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "large.md"
    target.write_text(
        "# Large Document\n\n"
        "earlyboundedtoken appears inside the scanner read window.\n\n"
        + ("middle bounded scan text keeps chunking deterministic.\n" * 14000)
        + "\nlateboundedtoken appears after the scanner read window.\n",
        encoding="utf-8",
    )

    assert target.stat().st_size > 512 * 1024
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    entity = _db_rows(
        tmp_path,
        "SELECT content_hash, data FROM entities WHERE path = 'docs/large.md'",
    )[0]
    data = json.loads(entity["data"])
    assert entity["content_hash"] == _file_sha256(target)
    assert data["size_bytes"] == target.stat().st_size

    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT content_preview
        FROM semantic_chunks
        WHERE source_path = 'docs/large.md'
        ORDER BY ordinal, sequence_index
        """,
    )
    assert chunk_rows
    previews = "\n".join(row["content_preview"] for row in chunk_rows)
    assert "earlyboundedtoken" in previews
    assert "lateboundedtoken" not in previews


def test_memory_structural_chunk_metadata_extracts_roles_and_context_policy(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "structure.md").write_text(
        "# Memory Graph\n\n"
        "Page 12 introduces the structural metadata model.\n\n"
        "## Requirements\n\n"
        "The chunker must keep retrieval context explainable.\n\n"
        "## Table Contract\n\n"
        "| Field | Meaning |\n"
        "|---|---|\n"
        "| context_policy | Retrieval expansion rule |\n\n"
        "## Schema Contract\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        "  Document --> Chunk\n"
        "```\n\n"
        "## Annotation\n\n"
        "> [!NOTE]\n"
        "> Reviewer note about the schema.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    rows = _db_rows(
        tmp_path,
        """
        SELECT heading_path, metadata
        FROM semantic_chunks
        WHERE source_path = 'docs/structure.md'
        ORDER BY ordinal, sequence_index
        """,
    )
    assert rows
    metadata_by_heading = {
        row["heading_path"]: json.loads(row["metadata"])
        for row in rows
    }
    assert metadata_by_heading["Memory Graph"]["structural_level"] == "chapter"
    assert "title" in metadata_by_heading["Memory Graph"]["structural_types"]
    assert metadata_by_heading["Memory Graph"]["source_location"]["page"] == "12"
    assert metadata_by_heading["Memory Graph > Requirements"]["semantic_role"] == "requirement"
    assert metadata_by_heading["Memory Graph > Requirements"]["context_policy"] == "include_parent_heading"
    assert metadata_by_heading["Memory Graph > Table Contract"]["block_type"] == "table"
    assert metadata_by_heading["Memory Graph > Table Contract"]["context_policy"] == "include_table_context"
    assert metadata_by_heading["Memory Graph > Schema Contract"]["block_type"] == "schema"
    assert metadata_by_heading["Memory Graph > Schema Contract"]["context_policy"] == "include_schema_caption"
    assert metadata_by_heading["Memory Graph > Annotation"]["block_type"] == "annotation"
    assert metadata_by_heading["Memory Graph > Annotation"]["context_policy"] == "include_annotation_target"
    assert any("memory" in item["topics"] for item in metadata_by_heading.values())
    assert any("software" in item["domains"] for item in metadata_by_heading.values())


def test_memory_document_layout_graph_creates_first_class_nodes_and_edges(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "layout.md").write_text(
        "# Layout Model\n\n"
        "Page 7 defines the layout graph.\n\n"
        "## Section A\n\n"
        "First paragraph.\n\n"
        "| Field | Meaning |\n"
        "|---|---|\n"
        "| node | layout node |\n\n"
        "### Schema Detail\n\n"
        "```json\n"
        "{\"node\":\"schema\"}\n"
        "```\n\n"
        "## Annotation\n\n"
        "> [!NOTE]\n"
        "> Attached reviewer annotation.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    node_rows = _db_rows(
        tmp_path,
        """
        SELECT node_type, heading_path, parent_id, metadata
        FROM document_layout_nodes
        WHERE source_path = 'docs/layout.md'
        ORDER BY ordinal, sequence_index, node_type
        """,
    )
    assert node_rows
    node_types = {row["node_type"] for row in node_rows}
    assert {
        "annotation",
        "chapter",
        "document",
        "page",
        "paragraph",
        "schema",
        "schema_field",
        "section",
        "subsection",
        "table",
        "table_cell",
        "title",
    } <= node_types
    assert any(row["parent_id"] for row in node_rows if row["node_type"] == "paragraph")
    assert any(json.loads(row["metadata"]).get("chunk_id") for row in node_rows if row["node_type"] == "table")
    assert any(
        json.loads(row["metadata"]).get("column_header") == "Field"
        for row in node_rows
        if row["node_type"] == "table_cell"
    )
    assert any(
        json.loads(row["metadata"]).get("field_path") == "node"
        for row in node_rows
        if row["node_type"] == "schema_field"
    )

    edge_rows = _db_rows(
        tmp_path,
        """
        SELECT type
        FROM document_layout_edges
        WHERE document_id = (SELECT id FROM entities WHERE path = 'docs/layout.md')
        """,
    )
    edge_types = {row["type"] for row in edge_rows}
    assert {
        "contains",
        "represented_by_chunk",
        "has_layout_node",
        "in_page",
        "next_layout",
        "table_cell_of",
        "schema_field_of",
    } <= edge_types

    assert cc.cmd_memory_layout(tmp_path, "docs/layout.md") == 0
    output = capsys.readouterr().out
    assert "Document layout: docs/layout.md" in output
    assert "table:" in output

    assert _run_main([
        "memory",
        "layout",
        "--project-root",
        str(tmp_path),
        "docs/layout.md",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert any(node["node_type"] == "schema" for node in payload["nodes"])


def test_memory_deep_layout_sidecar_ingests_pdf_ocr_coordinates(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    sidecar = docs / "report.pdf.layout.json"
    sidecar.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "title": "Quarterly Report",
                "sourcePath": "docs/report.pdf",
                "sourceType": "pdf",
                "extractionMethod": "pdf_native_text",
                "coordinateSystem": "pdf_points",
                "engine": "fixture",
                "pages": [
                    {
                        "page": 1,
                        "width": 612,
                        "height": 792,
                        "blocks": [
                            {
                                "id": "fig1",
                                "type": "figure",
                                "text": "Revenue trend chart.",
                                "bbox": [100, 120, 300, 260],
                                "confidence": 0.98,
                            },
                            {
                                "id": "img1",
                                "type": "image",
                                "text": "Reference logo.",
                                "bbox": [330, 130, 430, 230],
                            },
                            {
                                "id": "cap1",
                                "type": "caption",
                                "text": "Figure 1: Revenue trend chart.",
                                "bbox": {"x": 100, "y": 270, "width": 210, "height": 24},
                                "targetBlockId": "fig1",
                            },
                            {
                                "id": "ocr1",
                                "type": "paragraph",
                                "text": "OCR extracted evidence text for the report.",
                                "bbox": [72, 320, 540, 360],
                                "ocrConfidence": 0.91,
                            },
                            {
                                "id": "fn1",
                                "type": "footnote",
                                "text": "1. Source note for OCR text.",
                                "bbox": [72, 740, 540, 760],
                                "targetBlockId": "ocr1",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT heading_path, metadata
        FROM semantic_chunks
        WHERE source_path = 'docs/report.pdf.layout.json'
        ORDER BY ordinal
        """,
    )
    assert len(chunk_rows) == 5
    first_metadata = json.loads(chunk_rows[0]["metadata"])
    assert first_metadata["source_document_path"] == "docs/report.pdf"
    assert first_metadata["block_type"] == "figure"
    assert first_metadata["source_location"]["bbox"]["x0"] == 100.0
    assert first_metadata["source_location"]["normalized_bbox"]["x1"] == round(300 / 612, 6)
    assert first_metadata["layout_extraction"]["method"] == "pdf_native_text"

    node_rows = _db_rows(
        tmp_path,
        """
        SELECT node_type, metadata
        FROM document_layout_nodes
        WHERE source_path = 'docs/report.pdf.layout.json'
        ORDER BY ordinal, sequence_index, node_type
        """,
    )
    node_types = {row["node_type"] for row in node_rows}
    assert {"caption", "figure", "footnote", "image", "page", "paragraph"} <= node_types
    figure_metadata = [
        json.loads(row["metadata"])
        for row in node_rows
        if row["node_type"] == "figure"
    ][0]
    assert figure_metadata["source_location"]["page"] == "1"
    assert figure_metadata["source_location"]["page_width"] == 612.0

    edge_rows = _db_rows(
        tmp_path,
        """
        SELECT type
        FROM document_layout_edges
        WHERE document_id = (SELECT id FROM entities WHERE path = 'docs/report.pdf.layout.json')
        """
    )
    edge_types = {row["type"] for row in edge_rows}
    assert {
        "caption_for",
        "footnote_for",
        "in_page",
        "visual_adjacent",
        "visual_below",
        "visual_right_of",
    } <= edge_types

    capsys.readouterr()
    assert cc.cmd_memory_layout(tmp_path, "docs/report.pdf.layout.json", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert any(node["node_type"] == "caption" for node in payload["nodes"])


def test_memory_deep_layout_sidecar_ingests_page_collections_and_table_cells(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    sidecar = docs / "statement.pdf.ocr.json"
    sidecar.write_text(
        json.dumps(
            {
                "title": "Statement Layout",
                "sourcePath": "docs/statement.pdf",
                "sourceType": "pdf",
                "extractionMethod": "external_layout",
                "coordinateSystem": "pdf_points",
                "pages": [
                    {
                        "page": 2,
                        "width": 600,
                        "height": 800,
                        "figures": [
                            {
                                "id": "fig-a",
                                "text": "Revenue chart",
                                "bbox": [80, 60, 280, 180],
                            }
                        ],
                        "captions": [
                            {
                                "id": "cap-a",
                                "text": "Figure A: Revenue chart",
                                "targetBlockId": "fig-a",
                                "bbox": [80, 190, 280, 214],
                            }
                        ],
                        "tables": [
                            {
                                "id": "tbl-a",
                                "bbox": [72, 240, 520, 380],
                                "cells": [
                                    {
                                        "id": "c-h-1",
                                        "text": "Metric",
                                        "rowIndex": 0,
                                        "columnIndex": 1,
                                        "isHeader": True,
                                        "bbox": [72, 240, 250, 264],
                                    },
                                    {
                                        "id": "c-h-2",
                                        "text": "Value",
                                        "rowIndex": 0,
                                        "columnIndex": 2,
                                        "isHeader": True,
                                        "bbox": [250, 240, 520, 264],
                                    },
                                    {
                                        "id": "c-1-1",
                                        "text": "ARR",
                                        "rowIndex": 1,
                                        "columnIndex": 1,
                                        "columnHeader": "Metric",
                                        "bbox": [72, 264, 250, 288],
                                    },
                                    {
                                        "id": "c-1-2",
                                        "text": "$120",
                                        "rowIndex": 1,
                                        "columnIndex": 2,
                                        "columnHeader": "Value",
                                        "bbox": [250, 264, 520, 288],
                                    },
                                ],
                            }
                        ],
                        "annotations": [
                            {
                                "id": "ann-a",
                                "text": "Reviewer note on the table.",
                                "bbox": [72, 420, 520, 450],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT content_preview, metadata
        FROM semantic_chunks
        WHERE source_path = 'docs/statement.pdf.ocr.json'
        ORDER BY ordinal
        """,
    )
    assert len(chunk_rows) == 4
    assert any("ARR" in row["content_preview"] and "$120" in row["content_preview"] for row in chunk_rows)

    node_rows = _db_rows(
        tmp_path,
        """
        SELECT node_type, title, metadata
        FROM document_layout_nodes
        WHERE source_path = 'docs/statement.pdf.ocr.json'
        ORDER BY ordinal, sequence_index, node_type
        """,
    )
    node_types = {row["node_type"] for row in node_rows}
    assert {"annotation", "caption", "figure", "page", "table", "table_cell"} <= node_types
    cell_metadata = [
        json.loads(row["metadata"])
        for row in node_rows
        if row["node_type"] == "table_cell" and row["title"] == "c-1-2"
    ][0]
    assert cell_metadata["row_index"] == 1
    assert cell_metadata["column_index"] == 2
    assert cell_metadata["column_header"] == "Value"
    assert cell_metadata["source_location"]["page"] == "2"
    assert cell_metadata["source_location"]["normalized_bbox"]["x1"] == round(520 / 600, 6)

    edge_rows = _db_rows(
        tmp_path,
        """
        SELECT type
        FROM document_layout_edges
        WHERE document_id = (SELECT id FROM entities WHERE path = 'docs/statement.pdf.ocr.json')
        """
    )
    edge_types = {row["type"] for row in edge_rows}
    assert {"caption_for", "table_cell_of", "visual_below"} <= edge_types


def test_memory_deep_layout_sidecar_invalid_payload_does_not_create_raw_json_chunks(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "broken.pdf.ocr.json").write_text(
        "{\"title\":\"Broken OCR sidecar\",\"blocks\":[]}",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT id
        FROM semantic_chunks
        WHERE source_path = 'docs/broken.pdf.ocr.json'
        """,
    )
    node_rows = _db_rows(
        tmp_path,
        """
        SELECT id
        FROM document_layout_nodes
        WHERE source_path = 'docs/broken.pdf.ocr.json'
        """,
    )
    assert chunk_rows == []
    assert node_rows == []


def test_memory_scan_extracts_text_from_simple_native_pdf(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    pdf_text = b"BT /F1 12 Tf 72 720 Td (Native PDF extraction evidence) Tj T* (Second line for retrieval) Tj ET"
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length "
        + str(len(pdf_text)).encode("ascii")
        + b" >>\nstream\n"
        + pdf_text
        + b"\nendstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"%%EOF\n"
    )
    (docs / "native-report.pdf").write_bytes(pdf_bytes)

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    entity_rows = _db_rows(
        tmp_path,
        """
        SELECT type, title
        FROM entities
        WHERE path = 'docs/native-report.pdf'
        """,
    )
    assert entity_rows[0]["type"] == "doc_node"
    assert entity_rows[0]["title"] == "native report"

    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT heading_path, metadata, content_preview
        FROM semantic_chunks
        WHERE source_path = 'docs/native-report.pdf'
        ORDER BY ordinal, sequence_index
        """,
    )
    assert any("Native PDF extraction evidence" in row["content_preview"] for row in chunk_rows)
    page_chunk = [
        json.loads(row["metadata"])
        for row in chunk_rows
        if row["heading_path"] == "native report > Page 1"
    ][0]
    assert page_chunk["source_location"]["page"] == "1"

    node_rows = _db_rows(
        tmp_path,
        """
        SELECT node_type
        FROM document_layout_nodes
        WHERE source_path = 'docs/native-report.pdf'
        """
    )
    node_types = {row["node_type"] for row in node_rows}
    assert {"document", "page", "paragraph", "section", "title"} <= node_types


def test_memory_ocr_adapter_status_and_run_create_sidecar(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\n%% scanned fixture\n")
    runtime = tmp_path / "ocr_runtime.py"
    runtime.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        "print(json.dumps({\n"
        "  'title': 'Adapter Scan',\n"
        "  'sourcePath': request['sourcePath'],\n"
        "  'sourceType': 'pdf',\n"
        "  'engine': request['sourceSha256'][:8],\n"
        "  'coordinateSystem': request['sidecarSchema'],\n"
        "  'extractionMethod': 'fixture_ocr',\n"
        "  'pages': [{\n"
        "    'page': 1,\n"
        "    'width': 100,\n"
        "    'height': 200,\n"
        "    'blocks': [{\n"
        "      'id': 'ocr1',\n"
        "      'type': 'paragraph',\n"
        "      'text': 'Adapter OCR text',\n"
        "      'bbox': [10, 20, 90, 40],\n"
        "      'ocrConfidence': 0.93\n"
        "    }]\n"
        "  }]\n"
        "} ))\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    config_path = tmp_path / ".controlcoding" / "memory" / "ocr_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "activeAdapter": "local_runtime_v1",
                "localRuntime": {
                    "enabled": True,
                    "command": [sys.executable, str(runtime)],
                    "timeoutSeconds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "ocr",
        "status",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["activeAdapter"] == "local_runtime_v1"
    assert status_payload["adapters"][0]["available"] is True
    assert status_payload["adapters"][0]["commandExecutable"] is True
    assert "pdf" in status_payload["adapters"][0]["acceptedSourceTypes"]

    assert _run_main([
        "memory",
        "ocr",
        "run",
        "--project-root",
        str(tmp_path),
        "docs/scan.pdf",
        "--json",
    ]) == 0
    run_payload = json.loads(capsys.readouterr().out)
    assert run_payload["outputPath"] == "docs/scan.pdf.ocr.json"
    sidecar = json.loads((docs / "scan.pdf.ocr.json").read_text(encoding="utf-8"))
    assert sidecar["sourcePath"] == "docs/scan.pdf"
    assert len(sidecar["engine"]) == 8
    assert sidecar["coordinateSystem"] == "cc-layout-sidecar/v1"
    assert sidecar["pages"][0]["blocks"][0]["text"] == "Adapter OCR text"

    assert _run_main([
        "memory",
        "ocr",
        "run",
        "--project-root",
        str(tmp_path),
        "docs/scan.pdf",
        "--json",
    ]) == 1
    assert "already exists" in capsys.readouterr().out

    assert _run_main([
        "memory",
        "ocr",
        "run",
        "--project-root",
        str(tmp_path),
        "docs/scan.pdf",
        "--json",
        "--force",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["force"] is True

    assert cc.cmd_memory_scan(tmp_path) == 0
    chunk_rows = _db_rows(
        tmp_path,
        """
        SELECT metadata, content_preview
        FROM semantic_chunks
        WHERE source_path = 'docs/scan.pdf.ocr.json'
        """,
    )
    assert len(chunk_rows) == 1
    metadata = json.loads(chunk_rows[0]["metadata"])
    assert metadata["layout_extraction"]["method"] == "fixture_ocr"
    assert metadata["ocr_confidence"] == 0.93
    assert "Adapter OCR text" in chunk_rows[0]["content_preview"]


def test_memory_ocr_adapter_rejects_mismatched_source_path(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "scan.pdf").write_bytes(b"%PDF-1.4\n%% scanned fixture\n")
    runtime = tmp_path / "ocr_runtime_bad_source.py"
    runtime.write_text(
        "import json\n"
        "print(json.dumps({\n"
        "  'sourcePath': 'docs/other.pdf',\n"
        "  'pages': []\n"
        "} ))\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    config_path = tmp_path / ".controlcoding" / "memory" / "ocr_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "activeAdapter": "local_runtime_v1",
                "localRuntime": {
                    "enabled": True,
                    "command": [sys.executable, str(runtime)],
                    "timeoutSeconds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "ocr",
        "run",
        "--project-root",
        str(tmp_path),
        "docs/scan.pdf",
    ]) == 1
    assert "sourcePath must match" in capsys.readouterr().out
    assert not (docs / "scan.pdf.ocr.json").exists()


def test_memory_lifecycle_workflows_update_entities_edges_chunks_and_logs(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "old-plan.md").write_text("# Old Plan\n\n## Scope\n\nLegacy approach.\n", encoding="utf-8")
    (docs / "new-plan.md").write_text("# New Plan\n\n## Scope\n\nReplacement approach.\n", encoding="utf-8")
    (docs / "decision-a.md").write_text("# Decision A\n\nKeep option A.\n", encoding="utf-8")
    (docs / "decision-b.md").write_text("# Decision B\n\nKeep option B.\n", encoding="utf-8")

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0

    assert cc.cmd_memory_lifecycle_mark(
        tmp_path,
        "docs/old-plan.md",
        "legacy",
        reason="Retained for historical reference.",
    ) == 0
    old_row = _db_rows(tmp_path, "SELECT lifecycle, data FROM entities WHERE path = 'docs/old-plan.md'")[0]
    assert old_row["lifecycle"] == "legacy"
    assert "legacy" in json.loads(old_row["data"])["document_facets"]
    chunk_rows = _db_rows(tmp_path, "SELECT lifecycle FROM semantic_chunks WHERE source_path = 'docs/old-plan.md'")
    assert chunk_rows
    assert {row["lifecycle"] for row in chunk_rows} == {"legacy"}

    assert cc.cmd_memory_lifecycle_supersede(
        tmp_path,
        "docs/old-plan.md",
        "docs/new-plan.md",
        reason="New plan replaces the legacy plan.",
    ) == 0
    old_entity = _db_rows(tmp_path, "SELECT id, lifecycle FROM entities WHERE path = 'docs/old-plan.md'")[0]
    new_entity = _db_rows(tmp_path, "SELECT id FROM entities WHERE path = 'docs/new-plan.md'")[0]
    assert old_entity["lifecycle"] == "superseded"
    supersede_edges = _db_rows(
        tmp_path,
        "SELECT source_id, target_id, type FROM edges WHERE type IN ('supersedes', 'superseded_by')",
    )
    assert any(
        row["source_id"] == new_entity["id"] and row["target_id"] == old_entity["id"] and row["type"] == "supersedes"
        for row in supersede_edges
    )

    assert _run_main([
        "memory",
        "lifecycle",
        "conflict",
        "--project-root",
        str(tmp_path),
        "docs/decision-a.md",
        "docs/decision-b.md",
        "--reason",
        "The decisions select incompatible defaults.",
    ]) == 0
    conflict_lifecycles = {
        row["path"]: row["lifecycle"]
        for row in _db_rows(tmp_path, "SELECT path, lifecycle FROM entities WHERE path LIKE 'docs/decision-%'")
    }
    assert conflict_lifecycles == {
        "docs/decision-a.md": "conflicting",
        "docs/decision-b.md": "conflicting",
    }
    conflict_edges = _db_rows(tmp_path, "SELECT type FROM edges WHERE type = 'conflicts_with'")
    assert len(conflict_edges) == 2

    event_log = (tmp_path / ".controlcoding" / "logs" / "event_log.jsonl").read_text(encoding="utf-8")
    assert "mark_legacy" in event_log
    assert "supersede" in event_log
    assert "mark_conflicting" in event_log
    output = capsys.readouterr().out
    assert "Marked conflict:" in output


def test_memory_vector_rebuild_and_search_are_derived_from_chunks(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "payments.md").write_text(
        "# Payment Ledger\n\n"
        "## Reconciliation\n\n"
        "Ledger reconciliation checks settlement totals and payment evidence.\n",
        encoding="utf-8",
    )
    (docs / "search.md").write_text(
        "# Search Notes\n\n"
        "## Retrieval\n\n"
        "Local sparse search uses semantic chunks and matched terms.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    assert cc.cmd_memory_vector_rebuild(tmp_path) == 0

    rows = _db_rows(
        tmp_path,
        "SELECT adapter, source_path, term_count, norm FROM derived_vector_index ORDER BY source_path",
    )
    assert rows
    assert {row["adapter"] for row in rows} == {"local_sparse_v1"}
    assert all(row["term_count"] > 0 for row in rows)
    assert all(row["norm"] > 0 for row in rows)
    posting_rows = _db_rows(
        tmp_path,
        "SELECT term, source_id, term_weight FROM derived_vector_terms WHERE adapter = 'local_sparse_v1'",
    )
    assert posting_rows
    assert any(row["term"] == "reconciliation" for row in posting_rows)
    assert all(row["term_weight"] > 0 for row in posting_rows)

    assert cc.cmd_memory_vector_search(tmp_path, "settlement payment reconciliation", limit=3) == 0
    output = capsys.readouterr().out
    assert "Memory vector search: settlement payment reconciliation" in output
    assert "docs/payments.md" in output
    assert "reconciliation" in output.lower()

    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "semantic sparse retrieval",
        "--limit",
        "2",
    ]) == 0
    route_output = capsys.readouterr().out
    assert "docs/search.md" in route_output

    assert cc.cmd_memory_views_generate(tmp_path) == 0
    vector_index = (tmp_path / ".controlcoding" / "views" / "VECTOR_INDEX.md").read_text(encoding="utf-8")
    assert "local_sparse_v1" in vector_index
    assert "docs/payments.md" in vector_index


def test_memory_vector_search_uses_bounded_postings_prefilter(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    for index in range(90):
        (docs / f"filler-{index:02d}.md").write_text(
            f"# Filler {index}\n\nToolbar density and menu spacing note {index}.\n",
            encoding="utf-8",
        )
    (docs / "target.md").write_text(
        "# Prefilter Target\n\n"
        "Needleprefilter settlement evidence belongs in the sparse vector target.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    capsys.readouterr()

    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "needleprefilter settlement evidence",
        "--limit",
        "5",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    vector_index = payload["vectorIndex"]
    assert payload["entryCount"] > vector_index["scoredVectorRows"]
    assert vector_index["prefilterMode"] == "postings"
    assert vector_index["scoredVectorRows"] <= vector_index["prefilterCap"]
    assert vector_index["usedFullVectorScan"] is False
    assert vector_index["postingRows"] > 0
    assert vector_index["postingSources"] == payload["entryCount"]
    assert any(match["sourcePath"] == "docs/target.md" for match in payload["matches"])


def test_memory_vector_score_terms_only_sees_bounded_candidates(tmp_path, capsys, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    for index in range(30):
        (docs / f"shared-{index:02d}.md").write_text(
            f"# Shared {index}\n\nBoundedcandidate shared sparse vector evidence {index}.\n",
            encoding="utf-8",
        )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    capsys.readouterr()

    monkeypatch.setattr(memory_vector, "VECTOR_PREFILTER_MIN_CAP", 5)
    monkeypatch.setattr(memory_vector, "VECTOR_PREFILTER_MAX_CAP", 5)
    monkeypatch.setattr(memory_vector, "VECTOR_PREFILTER_LIMIT_MULTIPLIER", 1)
    original_score_terms = memory_vector._score_terms
    scored_source_ids: list[str] = []

    def recording_score_terms(query_terms, query_norm, row):
        scored_source_ids.append(str(row["source_id"]))
        return original_score_terms(query_terms, query_norm, row)

    monkeypatch.setattr(memory_vector, "_score_terms", recording_score_terms)
    with memory_store._memory_connection(tmp_path) as conn:
        memory_store._ensure_schema(conn)
        results, report = memory_vector._search_vector_index_with_report(
            conn,
            "boundedcandidate shared sparse",
            limit=10,
        )

    assert results
    assert report["prefilterCap"] == 5
    assert report["candidateSourceCount"] == 5
    assert report["scoredVectorRows"] == 5
    assert len(scored_source_ids) == 5
    assert len(set(scored_source_ids)) == 5
    assert report["usedFullVectorScan"] is False


def test_memory_vector_missing_postings_warns_without_full_scan(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# Posting Target\n\nPostingneedle settlement evidence must not fall back to a full scan.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    assert cc.cmd_memory_scan(tmp_path) == 0
    with memory_store._memory_connection(tmp_path) as conn:
        conn.execute("DELETE FROM derived_vector_terms")
    capsys.readouterr()

    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "postingneedle settlement evidence",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["matches"] == []
    assert payload["vectorHealth"]["stale"] is True
    assert "missing_vector_postings" in payload["vectorHealth"]["staleReasons"]
    assert "python scripts/cc.py memory vector rebuild --project-root ." in payload["warning"]
    assert payload["vectorIndex"]["usedFullVectorScan"] is False
    assert payload["vectorIndex"]["fallbackReason"] == "vector_index_stale"

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "postingneedle settlement evidence",
        "--json",
    ]) == 0
    retrieval = json.loads(capsys.readouterr().out)
    assert retrieval["vectorIndex"]["used"] is False
    assert retrieval["vectorIndex"]["usedFullVectorScan"] is False
    assert retrieval["vectorIndex"]["fallbackReason"] == "vector_index_stale"
    assert "missing_vector_postings" in retrieval["vectorIndex"]["health"]["staleReasons"]
    assert "python scripts/cc.py memory vector rebuild --project-root ." in retrieval["vectorIndex"]["warning"]


def test_memory_scan_rebuilds_vectors_after_same_file_content_change(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "staleness.md"
    target.write_text(
        "# Retrieval Fixture\n\n"
        "## Current\n\n"
        "Alpha settlement evidence keeps the first sparse vector payload.\n",
        encoding="utf-8",
    )

    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    first_scan = json.loads(capsys.readouterr().out)
    assert first_scan["vectorRebuild"]["executed"] is True
    assert first_scan["vectorRebuild"]["indexed"] > 0
    assert first_scan["vectorRebuild"]["postingRows"] > 0
    assert _db_rows(
        tmp_path,
        "SELECT source_id FROM derived_vector_terms WHERE term = 'alpha'",
    )

    target.write_text(
        "# Retrieval Fixture\n\n"
        "## Current\n\n"
        "Zephyrledger settlement evidence replaces the first sparse vector payload.\n",
        encoding="utf-8",
    )

    assert _run_main([
        "memory",
        "scan",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    second_scan = json.loads(capsys.readouterr().out)
    assert second_scan["updated"] == 1
    assert second_scan["vectorRebuild"]["executed"] is True
    assert second_scan["vectorRebuild"]["indexed"] > 0
    assert second_scan["vectorRebuild"]["skipped"] == 0
    assert second_scan["vectorRebuild"]["postingRows"] > 0
    assert not _db_rows(
        tmp_path,
        "SELECT source_id FROM derived_vector_terms WHERE term = 'alpha'",
    )
    assert _db_rows(
        tmp_path,
        "SELECT source_id FROM derived_vector_terms WHERE term = 'zephyrledger'",
    )

    assert _run_main([
        "memory",
        "status",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["vectors"]["stale"] is False
    assert status["vectors"]["rowCount"] == status["vectors"]["semanticChunks"]
    assert status["vectors"]["lastRebuiltAt"]

    assert _run_main([
        "memory",
        "vector",
        "search",
        "--project-root",
        str(tmp_path),
        "zephyrledger",
        "--json",
    ]) == 0
    vector_search = json.loads(capsys.readouterr().out)
    assert vector_search["vectorHealth"]["stale"] is False
    assert vector_search["matches"]
    assert vector_search["matches"][0]["sourcePath"] == "docs/staleness.md"
    assert "zephyrledger" in vector_search["matches"][0]["matchedTerms"]

    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    try:
        conn.execute("UPDATE derived_vector_index SET content_hash = 'stale-test-hash'")
        conn.commit()
    finally:
        conn.close()

    assert _run_main([
        "memory",
        "status",
        "--project-root",
        str(tmp_path),
    ]) == 0
    status_text = capsys.readouterr().out
    assert "Health: UNKNOWN (YELLOW)" in status_text
    assert "projection_outdated" in status_text

    assert _run_main([
        "memory",
        "retrieve",
        "--project-root",
        str(tmp_path),
        "zephyrledger",
        "--json",
    ]) == 0
    retrieval = json.loads(capsys.readouterr().out)
    assert retrieval["vectorIndex"]["used"] is False
    assert "python scripts/cc.py memory vector rebuild --project-root ." in retrieval["vectorIndex"]["warning"]

    assert _run_main([
        "memory",
        "rag-pack",
        "--project-root",
        str(tmp_path),
        "zephyrledger",
        "--json",
    ]) == 0
    rag_payload = json.loads(capsys.readouterr().out)
    assert any(
        warning["kind"] == "vector"
        and "python scripts/cc.py memory vector rebuild --project-root ." in warning["message"]
        for warning in rag_payload["warnings"]
    )


def test_memory_vector_health_allows_skipped_chunks_after_rebuild(tmp_path, capsys):
    assert cc.cmd_memory_init(tmp_path, mode="document-only", project_short="Demo") == 0
    capsys.readouterr()
    conn = sqlite3.connect(tmp_path / ".controlcoding" / "memory" / "memory.db")
    try:
        conn.execute(
            """
            INSERT INTO semantic_chunks(
              id, document_id, source_path, heading_path, ordinal, sequence_index,
              parent_chunk_id, previous_chunk_id, next_chunk_id, lifecycle,
              content_hash, summary, keywords, metadata, content_preview, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "CHUNK_UNINDEXABLE_TEST",
                "DOC_UNINDEXABLE_TEST",
                "the",
                "and",
                1,
                1,
                "",
                "",
                "",
                "active",
                "hash-unindexable",
                "for and the",
                "[]",
                "{}",
                "and the for",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert cc.cmd_memory_vector_rebuild(tmp_path, json_output=True) == 0
    rebuild = json.loads(capsys.readouterr().out)
    assert rebuild["indexed"] == 0
    assert rebuild["skipped"] == 1

    assert _run_main([
        "memory",
        "status",
        "--project-root",
        str(tmp_path),
        "--json",
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    vectors = status["vectors"]
    assert vectors["semanticChunks"] == 1
    assert vectors["indexableChunks"] == 0
    assert vectors["skippedChunks"] == 1
    assert vectors["rowCount"] == 0
    assert vectors["missingRows"] == 0
    assert vectors["contentHashMismatches"] == 0
    assert vectors["orphanRows"] == 0
    assert vectors["stale"] is False
    assert vectors["rebuildCommand"] == ""


def test_memory_cleanup_temp_removes_only_verified_pytest_candidates(tmp_path):
    candidate = tmp_path / ".pytest-tmp"
    other = tmp_path / "regular-temp"
    candidate.mkdir()
    other.mkdir()

    assert cc.cmd_memory_temp_cleanup(tmp_path, apply=False) == 0
    assert candidate.exists()
    assert other.exists()

    assert cc.cmd_memory_temp_cleanup(tmp_path, apply=True) == 0
    assert not candidate.exists()
    assert other.exists()


def test_memory_cleanup_temp_apply_skips_blocked_candidates(tmp_path, monkeypatch):
    candidate = tmp_path / ".pytest-tmp"
    candidate.mkdir()
    removed: list[Path] = []

    def fake_diagnostics(project: Path):
        return [
            {
                "path": str(candidate),
                "status": "blocked",
                "reason": "directory probe failed",
                "manualStep": "manual cleanup",
            }
        ]

    def fake_rmtree(path: Path):
        removed.append(path)

    monkeypatch.setattr(memory_lifecycle, "diagnose_pytest_temp_dirs", fake_diagnostics)
    monkeypatch.setattr(memory_lifecycle.shutil, "rmtree", fake_rmtree)

    results = memory_lifecycle.cleanup_pytest_temp_dirs(tmp_path, apply=True)

    assert results[0]["status"] == "blocked"
    assert results[0]["cleanup"] == "not-removed"
    assert candidate.exists()
    assert removed == []


def test_memory_cleanup_temp_routes_through_main(tmp_path):
    candidate = tmp_path / ".pytest-local-demo"
    candidate.mkdir()

    assert _run_main([
        "memory",
        "cleanup-temp",
        "--project-root",
        str(tmp_path),
        "--apply",
    ]) == 0
    assert not candidate.exists()


def test_memory_parent_project_root_is_not_overwritten_by_subcommand_default(tmp_path, monkeypatch):
    target = tmp_path / "target"
    cwd = tmp_path / "cwd"
    target.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert _run_main([
        "memory",
        "--project-root",
        str(target),
        "init",
        "--mode",
        "document-only",
        "--project-short",
        "Demo",
    ]) == 0

    assert (target / ".controlcoding" / "module_manifest.json").exists()
    assert not (cwd / ".controlcoding").exists()


def test_p3b2_queue_contract_is_deterministic_read_only_and_prelimit(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "queue")
    index_path = work_features.file_index_path(project)
    before = index_path.read_bytes()
    expected_by_kind = {"conflict": 1, "duplicate": 1, "pending_review": 1, "version": 1}

    first = _p3b2_queue(project)
    second = _p3b2_queue(project)
    assert first == second
    assert first["schemaVersion"] == "controlwork-review-queue/v1"
    assert first["summary"]["byKind"] == expected_by_kind
    assert first["summary"]["openItemCount"] == 4
    assert first["summary"]["affectedRecordCount"] == 6
    assert {item["kind"] for item in first["items"]} == set(expected_by_kind)
    assert [
        (item["status"] == "obsolete", item["kind"], item["id"])
        for item in first["items"]
    ] == sorted(
        (item["status"] == "obsolete", item["kind"], item["id"])
        for item in first["items"]
    )
    for item in first["items"]:
        assert item["id"] == f"CW_REVIEW_{item['fingerprint'][:20].upper()}"
        assert len(item["fingerprint"]) == 64
        int(item["fingerprint"], 16)

    expected_filtered_kinds = {
        "all": set(expected_by_kind),
        "duplicate": {"duplicate"},
        "versions": {"version"},
        "conflict": {"conflict"},
        "pending": {"pending_review"},
        "new": set(expected_by_kind),
        "changed": set(),
        "missing": set(),
        "unsupported": set(),
        "unreviewed": set(expected_by_kind),
        "needs-human": set(),
        "needs-import": set(),
        "ready-to-promote": set(),
        "sensitive": set(),
    }
    for filter_name, expected_kinds in expected_filtered_kinds.items():
        status = work_features.cmd_scan_review(_p3b2_review_namespace(
            project,
            filter=filter_name,
            limit=50,
        ))
        output = json.loads(capsys.readouterr().out)
        assert status == 0
        assert output["queue"]["schemaVersion"] == "controlwork-review-queue/v1"
        assert output["queue"]["summary"]["byKind"] == expected_by_kind
        assert {item["kind"] for item in output["queue"]["items"]} == expected_kinds
        assert {"reviewQueue", "pendingReview", "duplicateGroups"} <= output["summary"].keys()
        assert index_path.read_bytes() == before
        assert not index_path.with_name("file-index.json.lock").exists()
        assert not list(index_path.parent.glob(".file-index.json.*.tmp"))

    limited_status = work_features.cmd_scan_review(_p3b2_review_namespace(
        project,
        filter="all",
        limit=1,
    ))
    limited_output = json.loads(capsys.readouterr().out)
    assert limited_status == 0
    assert len(limited_output["queue"]["items"]) == 1
    assert limited_output["queue"]["summary"]["openItemCount"] == 4
    assert index_path.read_bytes() == before


def test_p3b2_dotfile_and_plain_path_keep_distinct_queue_ids():
    content_hash = "a" * 64
    payload = {
        "schemaVersion": work_features.SCAN_SCHEMA_VERSION,
        "generatedAt": "2026-08-11T00:00:00Z",
        "files": [
            {"path": ".env", "contentHash": content_hash, "scanStatus": "new", "reviewStatus": "unreviewed"},
            {"path": "env", "contentHash": content_hash, "scanStatus": "new", "reviewStatus": "unreviewed"},
        ],
    }
    queue = work_review_queue.build_review_queue(
        payload,
        {"duplicateGroups": [], "versionCandidates": [], "conflictCandidates": []},
        generated_at="2026-08-11T00:00:00Z",
        index_path=work_features.FILE_INDEX_PATH.as_posix(),
    )
    pending = [item for item in queue["items"] if item["kind"] == "pending_review"]

    assert {item["paths"][0] for item in pending} == {".env", "env"}
    assert len({item["id"] for item in pending}) == 2


def test_p3b2_full_contract_p3b1_parity_golden_is_self_contained():
    fixture_root = "C:/P3B1_FIXTURE"
    shared_hash = "1" * 64
    files = [
        {"path": "docs/duplicate-a.md", "contentHash": "a" * 64, "normalizedTextHash": shared_hash, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        {"path": "docs/duplicate-b.md", "contentHash": "a" * 64, "normalizedTextHash": shared_hash, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        {"path": "docs/report-draft.md", "contentHash": "b" * 64, "normalizedTextHash": "2" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        {"path": "docs/report-final.md", "contentHash": "c" * 64, "normalizedTextHash": "3" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        {"path": "docs/conflict.md", "contentHash": "d" * 64, "normalizedTextHash": "4" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        {"path": "docs/pending.md", "contentHash": "e" * 64, "normalizedTextHash": "5" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
    ]
    duplicate_evidence = [{
        "sharedHash": shared_hash,
        "members": [
            {"path": "docs/duplicate-a.md", "contentHash": "a" * 64, "normalizedTextHash": shared_hash, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
            {"path": "docs/duplicate-b.md", "contentHash": "a" * 64, "normalizedTextHash": shared_hash, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        ],
    }]
    payload = {
        "schemaVersion": "controlwork-file-index/v1",
        "generatedAt": "2026-08-09T00:00:00Z",
        "projectRoot": fixture_root,
        "summary": {"files": 6},
        "files": files,
        "reviewState": {
            "schemaVersion": "controlwork-review-state/v1",
            "candidateDecisions": [{
                "candidateId": "CW_REVIEW_64C763498742A6AA15C5",
                "fingerprint": "64c763498742a6aa15c5a285c33d7c0a7dbf1453476ff747175bd1cbe6ec8ee8",
                "kind": "duplicate",
                "action": "select_canonical",
                "status": "resolved",
                "canonicalPath": "docs/duplicate-a.md",
                "note": "",
                "decidedAt": "2026-08-09T01:02:03Z",
                "paths": ["docs/duplicate-a.md", "docs/duplicate-b.md"],
                "evidence": duplicate_evidence,
                "blockedPaths": ["docs/duplicate-a.md", "docs/duplicate-b.md"],
                "severity": "medium",
            }],
        },
    }
    analysis = {
        "duplicateGroups": [{
            "hash": shared_hash,
            "records": [files[0], files[1]],
            "reason": "Multiple scanned files share the same normalized text or content hash.",
        }],
        "versionCandidates": [{
            "family": "report",
            "records": [files[3], files[2]],
            "reason": "Similar filenames with different content hashes suggest multiple versions.",
        }],
        "conflictCandidates": [
            {
                "severity": "high",
                "kind": "text_conflict_markers",
                "path": "docs/conflict.md",
                "reason": "The file contains merge conflict markers and needs human review before promotion.",
            },
            {
                "severity": "medium",
                "kind": "parallel_versions",
                "paths": ["docs/report-draft.md", "docs/report-final.md"],
                "reason": "Represented only as version.",
            },
        ],
    }
    queue = work_review_queue.build_review_queue(
        payload,
        analysis,
        generated_at="2026-08-09T01:00:00Z",
        index_path=fixture_root.replace("/", "\\") + "\\.controlwork\\ingestion\\file-index.json",
    )

    def normalize(value):
        if isinstance(value, dict):
            return {
                key: "<TIMESTAMP>" if key in {"generatedAt", "indexGeneratedAt", "decidedAt"} else normalize(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            return value.replace("\\", "/").replace(fixture_root, "<FIXTURE_ROOT>")
        return value

    expected_duplicate_evidence = [{
        "sharedHash": shared_hash,
        "members": [
            {"path": "docs/duplicate-a.md", "contentHash": "a" * 64, "normalizedTextHash": shared_hash, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
            {"path": "docs/duplicate-b.md", "contentHash": "a" * 64, "normalizedTextHash": shared_hash, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
        ],
    }]
    assert normalize(queue) == {
        "schemaVersion": "controlwork-review-queue/v1",
        "generatedAt": "<TIMESTAMP>",
        "source": {
            "indexPath": "<FIXTURE_ROOT>/.controlwork/ingestion/file-index.json",
            "indexGeneratedAt": "<TIMESTAMP>",
            "indexFingerprint": "491aaae0ae16683b17e4bb6e83cd3a88c23fa613777793ad544af79d3ad3889f",
        },
        "summary": {
            "openItemCount": 3,
            "affectedRecordCount": 6,
            "byKind": {"conflict": 1, "duplicate": 1, "pending_review": 1, "version": 1},
            "byStatus": {"open": 3, "resolved": 1},
            "bySeverity": {"high": 1, "medium": 3},
        },
        "items": [
            {
                "id": "CW_REVIEW_DBCDC24BF0C92A7069D5",
                "fingerprint": "dbcdc24bf0c92a7069d53ab63efba548f73399e2da64c4c42d61fa19a2a123e4",
                "kind": "conflict",
                "status": "open",
                "severity": "high",
                "owner": "human_operator",
                "decisionRequired": True,
                "reason": "The file contains merge conflict markers and needs human review before promotion.",
                "paths": ["docs/conflict.md"],
                "evidence": [{
                    "subtype": "text_conflict_markers",
                    "sourceRecord": "",
                    "records": [{"path": "docs/conflict.md", "contentHash": "d" * 64, "normalizedTextHash": "4" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"}],
                }],
                "blockedPaths": ["docs/conflict.md"],
                "recommendedAction": "Acknowledge the evidence or defer it; acknowledgement does not resolve the underlying conflict.",
                "allowedActions": ["acknowledge", "defer"],
                "preconditions": ["Review the current evidence and fingerprint before acting."],
                "steps": ["Inspect the conflict evidence.", "Resolve the source outside this queue when appropriate."],
            },
            {
                "id": "CW_REVIEW_64C763498742A6AA15C5",
                "fingerprint": "64c763498742a6aa15c5a285c33d7c0a7dbf1453476ff747175bd1cbe6ec8ee8",
                "kind": "duplicate",
                "status": "resolved",
                "severity": "medium",
                "owner": "human_operator",
                "decisionRequired": True,
                "reason": "Multiple scanned files share the same normalized text or content hash.",
                "paths": ["docs/duplicate-a.md", "docs/duplicate-b.md"],
                "evidence": expected_duplicate_evidence,
                "blockedPaths": ["docs/duplicate-a.md", "docs/duplicate-b.md"],
                "recommendedAction": "Select one canonical source or explicitly accept the parallel copies.",
                "allowedActions": ["select_canonical", "accept_parallel", "defer"],
                "preconditions": ["Review the current evidence and fingerprint before acting."],
                "steps": ["Compare the members.", "Record a human decision without importing or promoting content."],
                "decision": {
                    "candidateId": "CW_REVIEW_64C763498742A6AA15C5",
                    "fingerprint": "64c763498742a6aa15c5a285c33d7c0a7dbf1453476ff747175bd1cbe6ec8ee8",
                    "kind": "duplicate",
                    "action": "select_canonical",
                    "status": "resolved",
                    "canonicalPath": "docs/duplicate-a.md",
                    "note": "",
                    "decidedAt": "<TIMESTAMP>",
                    "paths": ["docs/duplicate-a.md", "docs/duplicate-b.md"],
                    "evidence": expected_duplicate_evidence,
                    "blockedPaths": ["docs/duplicate-a.md", "docs/duplicate-b.md"],
                    "severity": "medium",
                },
            },
            {
                "id": "CW_REVIEW_5E1F0802E147F92611F9",
                "fingerprint": "5e1f0802e147f92611f9ecc5edb3d310c2febf82f88ba3a9a4e798600781a96a",
                "kind": "pending_review",
                "status": "open",
                "severity": "medium",
                "owner": "human_operator",
                "decisionRequired": True,
                "reason": "This scanned source still needs its existing per-file review status.",
                "paths": ["docs/pending.md"],
                "evidence": [{"path": "docs/pending.md", "contentHash": "e" * 64, "normalizedTextHash": "5" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"}],
                "blockedPaths": ["docs/pending.md"],
                "recommendedAction": "Use the existing path-scoped --review-status action.",
                "allowedActions": ["review_status"],
                "preconditions": ["Review the current evidence and fingerprint before acting."],
                "steps": ["Review the source.", "Use scan-review <path> --review-status ... if a per-file status is appropriate."],
            },
            {
                "id": "CW_REVIEW_D0B62EA52E935A88C85F",
                "fingerprint": "d0b62ea52e935a88c85f1c0ae8e7214a07ed2362a1f29bea1cbb4237e0846aea",
                "kind": "version",
                "status": "open",
                "severity": "medium",
                "owner": "human_operator",
                "decisionRequired": True,
                "reason": "Similar filenames with different content hashes suggest multiple versions.",
                "paths": ["docs/report-draft.md", "docs/report-final.md"],
                "evidence": [{
                    "family": "report",
                    "members": [
                        {"path": "docs/report-draft.md", "contentHash": "b" * 64, "normalizedTextHash": "2" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
                        {"path": "docs/report-final.md", "contentHash": "c" * 64, "normalizedTextHash": "3" * 64, "sourceRecord": "", "scanStatus": "new", "reviewStatus": "unreviewed"},
                    ],
                }],
                "blockedPaths": ["docs/report-draft.md", "docs/report-final.md"],
                "recommendedAction": "Select a canonical version or accept parallel versions with a human note.",
                "allowedActions": ["select_canonical", "accept_parallel", "defer"],
                "preconditions": ["Review the current evidence and fingerprint before acting."],
                "steps": [
                    "Compare the version family.",
                    "Record the decision without renaming, moving, importing, or promoting files.",
                ],
            },
        ],
    }


def test_p3b2_valid_relational_decisions_and_zero_write_guards(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "decisions")
    index_path = work_features.file_index_path(project)
    items = {kind: _p3b2_item(project, kind) for kind in ("duplicate", "version", "conflict", "pending_review")}
    valid = [
        ("duplicate", "select_canonical", items["duplicate"]["paths"][0], ""),
        ("version", "accept_parallel", "", "Parallel versions are intentional."),
        ("conflict", "acknowledge", "", "Conflict evidence acknowledged."),
    ]
    for kind, action, canonical, note in valid:
        item = items[kind]
        status = work_features.cmd_scan_review(_p3b2_review_namespace(
            project,
            item=item["id"],
            action=action,
            canonical=canonical,
            note=note,
            fingerprint=item["fingerprint"],
            filter="all",
            limit=50,
        ))
        output = json.loads(capsys.readouterr().out)
        assert status == 0
        assert output["decision"]["kind"] == kind
        assert output["queue"]["schemaVersion"] == "controlwork-review-queue/v1"
    indexed = json.loads(index_path.read_text(encoding="utf-8"))
    assert indexed["reviewState"]["schemaVersion"] == "controlwork-review-state/v1"
    assert len(indexed["reviewState"]["candidateDecisions"]) == 3

    invalid_cases = [
        {"item": items["duplicate"]["id"], "action": "defer", "fingerprint": ""},
        {"item": items["duplicate"]["id"], "action": "defer", "fingerprint": "0" * 64},
        {"item": "CW_REVIEW_00000000000000000000", "action": "defer", "fingerprint": "1" * 64},
        {"item": items["version"]["id"], "action": "select_canonical", "canonical": "docs/not-a-member.md", "fingerprint": items["version"]["fingerprint"]},
        {"item": items["version"]["id"], "action": "accept_parallel", "note": "", "fingerprint": items["version"]["fingerprint"]},
        {"item": items["conflict"]["id"], "action": "select_canonical", "canonical": items["conflict"]["paths"][0], "fingerprint": items["conflict"]["fingerprint"]},
        {"item": items["pending_review"]["id"], "action": "defer", "fingerprint": items["pending_review"]["fingerprint"]},
    ]
    for values in invalid_cases:
        before = index_path.read_bytes()
        status = work_features.cmd_scan_review(_p3b2_review_namespace(project, **values))
        failure = json.loads(capsys.readouterr().out)
        assert status == 1
        assert failure["ok"] is False
        assert index_path.read_bytes() == before
        assert not index_path.with_name("file-index.json.lock").exists()
        assert not list(index_path.parent.glob(".file-index.json.*.tmp"))

    malformed = json.loads(index_path.read_text(encoding="utf-8"))
    malformed["reviewState"] = {"schemaVersion": "controlwork-review-state/v0", "candidateDecisions": []}
    index_path.write_text(json.dumps(malformed, indent=2) + "\n", encoding="utf-8")
    before = index_path.read_bytes()
    status = work_features.cmd_scan_review(_p3b2_review_namespace(
        project,
        item=items["duplicate"]["id"],
        action="defer",
        fingerprint=items["duplicate"]["fingerprint"],
    ))
    failure = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "reviewState.schemaVersion" in failure["error"]
    assert index_path.read_bytes() == before


def test_p3b2_decision_replacement_and_single_unique_obsolete_projection(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "obsolete")
    duplicate = _p3b2_item(project, "duplicate")
    _p3b2_apply_decision(project, duplicate, action="defer", note="First decision.")
    _p3b2_apply_decision(
        project,
        duplicate,
        action="select_canonical",
        canonical=duplicate["paths"][0],
        note="Replacement decision.",
        decided_at="2026-08-09T00:00:01Z",
    )
    index_path = work_features.file_index_path(project)
    state = json.loads(index_path.read_text(encoding="utf-8"))["reviewState"]
    assert len(state["candidateDecisions"]) == 1
    assert state["candidateDecisions"][0]["action"] == "select_canonical"

    (project / "docs" / "duplicate-b.md").write_text("Candidate changed.\n", encoding="utf-8")
    work_features.refresh_file_index(project)
    queue = _p3b2_queue(project)
    obsolete = [item for item in queue["items"] if item["status"] == "obsolete"]
    assert len(obsolete) == 1
    assert obsolete[0]["fingerprint"] == duplicate["fingerprint"]
    ids = [item["id"] for item in queue["items"]]
    assert len(ids) == len(set(ids))
    assert queue["items"][-1]["status"] == "obsolete"

    before = index_path.read_bytes()
    status = work_features.cmd_scan_review(_p3b2_review_namespace(
        project,
        item=obsolete[0]["id"],
        action="defer",
        fingerprint=obsolete[0]["fingerprint"],
    ))
    failure = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "obsolete" in failure["error"]
    assert index_path.read_bytes() == before


def test_p3b2_relation_batch_rejection_and_pending_only_batch(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "mixed")
    index_path = work_features.file_index_path(project)
    for filter_name in ("all", "pending"):
        before = index_path.read_bytes()
        status = work_features.cmd_scan_review(_p3b2_review_namespace(
            project,
            batch=True,
            filter=filter_name,
            review_status="reviewed",
        ))
        failure = json.loads(capsys.readouterr().out)
        assert status == 1
        assert "homogeneous pending_review" in failure["error"]
        assert index_path.read_bytes() == before

    pending_project = _p3b2_make_project(tmp_path / "pending-only", pending_only=True)
    status = work_features.cmd_scan_review(_p3b2_review_namespace(
        pending_project,
        batch=True,
        filter="pending",
        review_status="reviewed",
        note="Pending-only batch.",
    ))
    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["batchReview"]["updated"] == 1
    indexed = work_features.read_file_index(pending_project)
    assert indexed["files"][0]["reviewStatus"] == "reviewed"
    assert work_features.cmd_scan_review(_p3b2_review_namespace(
        pending_project,
        filter="pending",
        limit=50,
    )) == 0
    assert json.loads(capsys.readouterr().out)["queue"]["items"] == []


def test_p3b2_ready_to_promote_batch_preserves_structured_failure(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "blocked-batch", pending_only=True)
    source = project / "docs" / "pending.md"
    source.unlink()
    work_features.refresh_file_index(project)
    index = work_features.file_index_path(project)
    before = index.read_bytes()

    status = work_features.cmd_scan_review(_p3b2_review_namespace(
        project,
        filter="pending",
        batch=True,
        review_status="ready_to_promote",
        limit=50,
    ))
    output = json.loads(capsys.readouterr().out)

    assert status == 1
    assert output["error"] == "ready_to_promote cannot be applied while promotion blockers exist"
    assert output["filter"] == "pending"
    assert output["blockedCount"] == 1
    assert output["blocked"][0]["path"] == "docs/pending.md"
    assert output["blocked"][0]["reviewFlags"] == []
    assert [blocker["kind"] for blocker in output["blocked"][0]["promotionBlockers"]] == ["missing_source"]
    assert index.read_bytes() == before
    assert not index.with_name("file-index.json.lock").exists()
    assert not list(index.parent.glob(".file-index.json.*.tmp"))


def test_p3b2_concurrent_relational_decisions_do_not_lose_updates(tmp_path):
    project = _p3b2_make_project(tmp_path / "concurrent")
    duplicate = _p3b2_item(project, "duplicate")
    version = _p3b2_item(project, "version")
    context = multiprocessing.get_context("spawn")
    transaction_boundary = context.Barrier(2)
    results = context.Queue()
    requests = [
        {
            "project": str(project),
            "item": duplicate["id"],
            "action": "defer",
            "fingerprint": duplicate["fingerprint"],
            "note": "Concurrent duplicate decision.",
        },
        {
            "project": str(project),
            "item": version["id"],
            "action": "accept_parallel",
            "fingerprint": version["fingerprint"],
            "note": "Concurrent version decision.",
        },
    ]
    processes = [
        context.Process(target=_p3b2_decision_worker, args=(request, results, transaction_boundary))
        for request in requests
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
    alive = [process for process in processes if process.is_alive()]
    for process in alive:
        process.terminate()
        process.join(timeout=5)
    assert not [process for process in processes if process.is_alive()], "spawned review workers exceeded their finite timeout"

    child_results = [results.get(timeout=10) for _request in requests]
    assert all("error" not in result for result in child_results), child_results
    assert all(result["status"] == 0 for result in child_results), child_results
    decisions = work_features.read_file_index(project)["reviewState"]["candidateDecisions"]
    keys = {(decision["candidateId"], decision["fingerprint"]) for decision in decisions}
    assert (duplicate["id"], duplicate["fingerprint"]) in keys
    assert (version["id"], version["fingerprint"]) in keys
    index_path = work_features.file_index_path(project)
    assert not index_path.with_name("file-index.json.lock").exists()
    assert not list(index_path.parent.glob(".file-index.json.*.tmp"))


@pytest.mark.parametrize("writer", ["refresh", "scan", "init", "quickstart", "dashboard"])
def test_p3b2_refresh_writers_preserve_relational_state_and_unknown_fields(tmp_path, capsys, writer):
    project = _p3b2_make_project(tmp_path / writer)
    duplicate = _p3b2_item(project, "duplicate")
    decision = _p3b2_apply_decision(project, duplicate, action="defer", note=f"Preserve across {writer}.")["decision"]
    index_path = work_features.file_index_path(project)
    indexed = json.loads(index_path.read_text(encoding="utf-8"))
    indexed["p3b2UnknownTopLevel"] = {"writer": writer, "preserved": True}
    index_path.write_text(json.dumps(indexed, indent=2) + "\n", encoding="utf-8")
    pending_path = project / "docs" / "pending.md"
    pending_path.write_text(f"Updated by {writer}.\n", encoding="utf-8")

    if writer == "refresh":
        work_features.refresh_file_index(project)
    elif writer == "scan":
        assert work_features.cmd_scan(SimpleNamespace(project_root=project, json_output=True)) == 0
        capsys.readouterr()
    elif writer == "init":
        assert memory_commands.cmd_memory_work_init(project, json_output=True) == 0
        capsys.readouterr()
    elif writer == "quickstart":
        assert memory_commands.cmd_memory_work_quickstart(project, json_output=True) == 0
        capsys.readouterr()
    elif writer == "dashboard":
        refreshed = work_dashboard.refresh_scan_state(project)
        assert refreshed["enabled"] is True

    current = json.loads(index_path.read_text(encoding="utf-8"))
    assert current["p3b2UnknownTopLevel"] == {"writer": writer, "preserved": True}
    assert decision in current["reviewState"]["candidateDecisions"]
    pending = next(record for record in current["files"] if record["path"] == "docs/pending.md")
    assert pending["contentHash"] == work_features.sha256_file(pending_path)
    assert not index_path.with_name("file-index.json.lock").exists()
    assert not list(index_path.parent.glob(".file-index.json.*.tmp"))


def test_p3b2_path_and_pending_batch_preserve_relational_state(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "path-batch")
    duplicate = _p3b2_item(project, "duplicate")
    decision = _p3b2_apply_decision(project, duplicate, action="defer", note="Preserve across record reviews.")["decision"]

    assert work_features.cmd_scan_review(_p3b2_review_namespace(
        project,
        path="docs/pending.md",
        review_status="reviewed",
    )) == 0
    capsys.readouterr()
    assert decision in work_features.read_file_index(project)["reviewState"]["candidateDecisions"]

    (project / "docs" / "batch.md").write_text("New batch-only evidence.\n", encoding="utf-8")
    work_features.refresh_file_index(project)
    relational_paths = {
        path
        for item in _p3b2_queue(project)["items"]
        if item["kind"] in work_review_queue.RELATIONAL_KINDS and item["status"] != "obsolete"
        for path in item["paths"]
    }
    for path in sorted(relational_paths):
        assert work_features.cmd_scan_review(_p3b2_review_namespace(
            project,
            path=path,
            review_status="ignored",
        )) == 0
        capsys.readouterr()

    assert work_features.cmd_scan_review(_p3b2_review_namespace(
        project,
        batch=True,
        filter="pending",
        review_status="reviewed",
        note="Homogeneous pending batch.",
    )) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["batchReview"]["updated"] == 1
    current = work_features.read_file_index(project)
    assert decision in current["reviewState"]["candidateDecisions"]
    batch_record = next(record for record in current["files"] if record["path"] == "docs/batch.md")
    assert batch_record["reviewStatus"] == "reviewed"


@pytest.mark.parametrize("writer", ["scan-import", "ocr-run", "ocr-sidecar", "scan-promote"])
def test_p3b2_record_writers_preserve_relational_state(tmp_path, capsys, monkeypatch, writer):
    project = _p3b2_make_project(tmp_path / writer, include_pdf=True)
    duplicate = _p3b2_item(project, "duplicate")
    decision = _p3b2_apply_decision(project, duplicate, action="defer", note=f"Preserve across {writer}.")["decision"]

    if writer == "scan-import":
        status = work_features.cmd_scan_import(SimpleNamespace(
            project_root=project,
            path="docs/pending.md",
            area="sources",
            title="",
            summary="",
            lifecycle="needs_review",
            category="",
            max_chars=12000,
            as_reference=False,
            dry_run=False,
            proposal=False,
            output=None,
            force=False,
        ))
    elif writer == "ocr-run":
        monkeypatch.setattr(
            work_features,
            "run_local_ocr_adapter",
            lambda *_args, **_kwargs: (
                {"pages": [{"page": 1, "text": "P3-B2 local OCR evidence."}]},
                "",
                {"runSupported": True},
            ),
        )
        status = work_features.cmd_ocr_run(SimpleNamespace(
            project_root=project,
            source="docs/scan.pdf",
            output=None,
            force=False,
        ))
    elif writer == "ocr-sidecar":
        record = next(item for item in work_features.read_file_index(project)["files"] if item["path"] == "docs/scan.pdf")
        sidecar = project / "docs" / "supplied.ocr.json"
        sidecar.write_text(json.dumps({
            "schemaVersion": "controlwork-ocr-sidecar/v1",
            "sourcePath": "docs/scan.pdf",
            "sourceHash": record["contentHash"],
            "extractor": "p3b2-fixture",
            "pages": [{"page": 1, "text": "P3-B2 supplied OCR evidence."}],
        }), encoding="utf-8")
        status = work_features.cmd_ocr_import_sidecar(SimpleNamespace(
            project_root=project,
            source="docs/scan.pdf",
            sidecar=sidecar,
            force=False,
            import_source=False,
            title="",
            area="sources",
            lifecycle="needs_review",
            category="",
            summary="",
            dry_run=False,
        ))
    else:
        assert work_features.cmd_scan_review(_p3b2_review_namespace(
            project,
            path="docs/pending.md",
            review_status="reviewed",
        )) == 0
        capsys.readouterr()
        status = work_features.cmd_scan_promote(SimpleNamespace(
            project_root=project,
            path="docs/pending.md",
            area="sources",
            title="",
            summary="",
            lifecycle="captured",
            category="",
            include_excerpt=False,
            force=False,
            force_note="",
        ))

    output = json.loads(capsys.readouterr().out)
    assert status == 0, output
    current = work_features.read_file_index(project)
    assert decision in current["reviewState"]["candidateDecisions"]
    index_path = work_features.file_index_path(project)
    assert not index_path.with_name("file-index.json.lock").exists()
    assert not list(index_path.parent.glob(".file-index.json.*.tmp"))


def test_p3b2_record_writer_rejects_stale_preflight_without_index_replacement(tmp_path, capsys, monkeypatch):
    project = _p3b2_make_project(tmp_path / "stale-writer")
    index_path = work_features.file_index_path(project)
    original_mutate = work_review_queue.mutate_file_index
    concurrent_bytes = {}

    def change_before_transaction(target: Path, mutate, **kwargs):
        current = json.loads(target.read_text(encoding="utf-8"))
        record = next(item for item in current["files"] if item["path"] == "docs/pending.md")
        record["contentHash"] = "changed-after-preflight"
        current["concurrentWriter"] = {"preserved": True}
        target.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        concurrent_bytes["value"] = target.read_bytes()
        return original_mutate(target, mutate, **kwargs)

    monkeypatch.setattr(work_review_queue, "mutate_file_index", change_before_transaction)
    status = work_features.cmd_scan_import(SimpleNamespace(
        project_root=project,
        path="docs/pending.md",
        area="sources",
        title="",
        summary="",
        lifecycle="needs_review",
        category="",
        max_chars=12000,
        as_reference=False,
        dry_run=False,
        proposal=False,
        output=None,
        force=False,
    ))
    failure = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "precondition changed" in failure["error"]
    assert index_path.read_bytes() == concurrent_bytes["value"]
    assert not index_path.with_name("file-index.json.lock").exists()
    assert not list(index_path.parent.glob(".file-index.json.*.tmp"))


def test_p3b2_first_index_atomic_unknown_preservation_and_cleanup(tmp_path, monkeypatch):
    project = tmp_path / "first-index"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (docs / "pending.md").write_text("First index evidence.\n", encoding="utf-8")
    index_path = work_features.file_index_path(project)
    lock_path = index_path.with_name("file-index.json.lock")
    original_replace = work_review_queue.os.replace
    replacements = []

    def observed_replace(source, target):
        assert Path(target) == index_path
        assert lock_path.exists()
        assert not index_path.exists()
        replacements.append((Path(source), Path(target)))
        return original_replace(source, target)

    monkeypatch.setattr(work_review_queue.os, "replace", observed_replace)
    created = work_features.refresh_file_index(project)
    assert created["schemaVersion"] == work_features.SCAN_SCHEMA_VERSION
    assert len(replacements) == 1
    assert index_path.exists()
    assert not lock_path.exists()
    assert not list(index_path.parent.glob(".file-index.json.*.tmp"))

    monkeypatch.setattr(work_review_queue.os, "replace", original_replace)
    indexed = json.loads(index_path.read_text(encoding="utf-8"))
    indexed["p3b2UnknownTopLevel"] = {"preserved": True}
    indexed["reviewState"] = {
        "schemaVersion": "controlwork-review-state/v1",
        "candidateDecisions": [],
    }
    index_path.write_text(json.dumps(indexed, indent=2) + "\n", encoding="utf-8")
    work_features.refresh_file_index(project)
    refreshed = json.loads(index_path.read_text(encoding="utf-8"))
    assert refreshed["p3b2UnknownTopLevel"] == {"preserved": True}
    assert refreshed["reviewState"] == indexed["reviewState"]

    refreshed["reviewState"] = {"schemaVersion": "controlwork-review-state/v1", "candidateDecisions": {}}
    index_path.write_text(json.dumps(refreshed, indent=2) + "\n", encoding="utf-8")
    before = index_path.read_bytes()
    with pytest.raises(work_review_queue.ReviewQueueError, match="candidateDecisions"):
        work_features.refresh_file_index(project)
    assert index_path.read_bytes() == before
    assert not lock_path.exists()
    assert not list(index_path.parent.glob(".file-index.json.*.tmp"))


def test_p3b2_cli_parser_dispatch_adapter_and_legacy_positional_output(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "cli")
    assert _run_main([
        "memory",
        "work-review",
        "--project-root",
        str(project),
        "--filter",
        "all",
        "--limit",
        "1",
        "--json",
    ]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["queue"]["schemaVersion"] == "controlwork-review-queue/v1"
    assert {"summary", "queue", "indexPath", "ok"} <= listed.keys()

    with patch.object(cc, "cmd_memory_work_review", return_value=0) as dispatch:
        assert _run_main([
            "memory",
            "work-review",
            "--project-root",
            str(project),
            "--filter",
            "all",
            "--json",
        ]) == 0
    assert dispatch.call_args.kwargs["force_output"] is False

    with patch.object(cc, "cmd_memory_work_review", return_value=0) as dispatch:
        assert _run_main([
            "memory",
            "work-review",
            "--project-root",
            str(project),
            "--item",
            "CW_REVIEW_TEST",
            "--action",
            "defer",
            "--canonical",
            "docs/example.md",
            "--fingerprint",
            "f" * 64,
            "--force-output",
            "--note",
            "Dispatch evidence.",
            "--json",
        ]) == 0
    kwargs = dispatch.call_args.kwargs
    assert kwargs["item"] == "CW_REVIEW_TEST"
    assert kwargs["action"] == "defer"
    assert kwargs["canonical"] == "docs/example.md"
    assert kwargs["fingerprint"] == "f" * 64
    assert kwargs["force_output"] is True

    with patch.object(work_features, "cmd_scan_review", return_value=0) as feature_adapter:
        assert memory_commands.cmd_memory_work_review(
            project,
            item="CW_REVIEW_ADAPTER",
            action="accept_parallel",
            canonical="docs/canonical.md",
            fingerprint="a" * 64,
            note="Adapter evidence.",
            force_output=True,
        ) == 0
    namespace = feature_adapter.call_args.args[0]
    assert namespace.item == "CW_REVIEW_ADAPTER"
    assert namespace.action == "accept_parallel"
    assert namespace.canonical == "docs/canonical.md"
    assert namespace.fingerprint == "a" * 64
    assert not hasattr(namespace, "force_output")

    assert _run_main([
        "memory",
        "work-review",
        "docs/pending.md",
        "--project-root",
        str(project),
        "--review-status",
        "reviewed",
        "--json",
    ]) == 0
    positional = json.loads(capsys.readouterr().out)
    assert positional["path"] == "docs/pending.md"
    assert positional["reviewStatus"] == "reviewed"
    assert positional["indexPath"] == ".controlwork/ingestion/file-index.json"


def test_p3b2_force_output_preserves_work_review_behavior(tmp_path, capsys):
    project = _p3b2_make_project(tmp_path / "force-output")
    duplicate = _p3b2_item(project, "duplicate")

    assert _run_main([
        "memory",
        "work-review",
        "--project-root",
        str(project),
        "--item",
        duplicate["id"],
        "--action",
        "select_canonical",
        "--canonical",
        duplicate["paths"][0],
        "--fingerprint",
        duplicate["fingerprint"],
        "--force-output",
        "--json",
    ]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["decision"]["action"] == "select_canonical"

    missing_fingerprint = _p3b2_item(_p3b2_make_project(tmp_path / "missing-fingerprint"), "duplicate")
    missing_project = tmp_path / "missing-fingerprint"
    assert _run_main([
        "memory",
        "work-review",
        "--project-root",
        str(missing_project),
        "--item",
        missing_fingerprint["id"],
        "--action",
        "defer",
        "--force-output",
        "--json",
    ]) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False

    inert_output = project / "proposals" / "inert.md"
    inert_output.parent.mkdir(parents=True)
    inert_output.write_bytes(b"must remain unchanged\n")
    assert _run_main([
        "memory",
        "work-review",
        "--project-root",
        str(project),
        "--filter",
        "all",
        "--output",
        str(inert_output),
        "--force-output",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert inert_output.read_bytes() == b"must remain unchanged\n"

    proposal_output = project / "proposals" / "review.md"
    proposal_output.parent.mkdir(parents=True, exist_ok=True)
    proposal_output.write_text("before without flag\n", encoding="utf-8")
    base_args = [
        "memory",
        "work-review",
        "--project-root",
        str(project),
        "--filter",
        "all",
        "--proposal",
        "--output",
        str(proposal_output),
        "--json",
    ]
    assert _run_main(base_args) == 0
    without_force = json.loads(capsys.readouterr().out)
    without_force_content = proposal_output.read_text(encoding="utf-8")
    assert without_force_content != "before without flag\n"

    proposal_output.write_text("before with flag\n", encoding="utf-8")
    assert _run_main([*base_args[:-1], "--force-output", "--json"]) == 0
    with_force = json.loads(capsys.readouterr().out)
    with_force_content = proposal_output.read_text(encoding="utf-8")
    assert with_force_content != "before with flag\n"
    assert without_force["proposalPath"] == with_force["proposalPath"] == "proposals/review.md"
    assert "\n".join(
        line for line in without_force_content.splitlines()
        if not line.startswith("- **Generated**:")
    ) == "\n".join(
        line for line in with_force_content.splitlines()
        if not line.startswith("- **Generated**:")
    )

    external_output = tmp_path / "external-review.md"
    assert _run_main([
        "memory",
        "work-review",
        "--project-root",
        str(project),
        "--filter",
        "all",
        "--proposal",
        "--output",
        str(external_output),
        "--force-output",
        "--json",
    ]) == 1
    external_failure = json.loads(capsys.readouterr().out)
    assert external_failure["ok"] is False
    assert not external_output.exists()
