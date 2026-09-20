"""Unit tests for mcp_session.py and mcp_handoff.py.

Covers:
- update_status, write_devlog, checkpoint, read_status, read_history, read_devlog (mcp_session)
- F1 Context Handoff: write_decision, generate_brief, warm.md (mcp_handoff)
  - verbatim preservation guarantee (REQ-HO-01, REQ-HO-02)
  - supersedes exclusion (REQ-HO-03)
  - 6KB truncation by omission, never by compression (REQ-HO-04)
  - all five decision types (DECISION, CONSTRAINT, PLAN, FINDING, QUESTION)
  - auto-ID sequencing, manual ID, duplicate rejection
  - cc resume --brief output
"""

import importlib
import errno
import json
import multiprocessing
import os
import sys
import traceback
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_handoff_worker(project_root, tools_dir):
    os.environ["SESSION_PROJECT_ROOT"] = project_root
    sys.path.insert(0, tools_dir)
    for module_name in ("mcp_handoff", "cc_lockfile"):
        sys.modules.pop(module_name, None)
    importlib.invalidate_caches()
    import mcp_handoff

    return mcp_handoff


def _worker_failure(worker, phase, exc):
    return {
        "ok": False,
        "worker": worker,
        "phase": phase,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "errno": getattr(exc, "errno", None),
        "winerror": getattr(exc, "winerror", None),
        "filename": str(exc.filename) if getattr(exc, "filename", None) else None,
        "traceback": traceback.format_exc(),
    }


def _decision_writer_worker(project_root, tools_dir, barrier, result_queue, decision_id=""):
    phase = "import"
    try:
        mcp_handoff = _load_handoff_worker(project_root, tools_dir)
        phase = "barrier"
        barrier.wait(timeout=10)
        phase = "write_decision"
        result = mcp_handoff.write_decision(
            "DECISION",
            "Concurrent entry.",
            decision_id=decision_id,
        )
        result_queue.put({"ok": True, "worker": "writer", "result": result})
    except Exception as exc:
        result_queue.put(_worker_failure("writer", phase, exc))


def _decision_lock_timeout_worker(project_root, tools_dir, result_queue):
    phase = "import"
    try:
        mcp_handoff = _load_handoff_worker(project_root, tools_dir)
        mcp_handoff._DECISIONS_LOCK_TIMEOUT_SECONDS = 0.2
        mcp_handoff._DECISIONS_LOCK_POLL_SECONDS = 0.01
        phase = "write_decision"
        result = mcp_handoff.write_decision("DECISION", "Must time out.")
        result_queue.put({"ok": True, "worker": "timeout-writer", "result": result})
    except Exception as exc:
        result_queue.put(_worker_failure("timeout-writer", phase, exc))


def _decision_append_failure_worker(project_root, tools_dir, result_queue):
    phase = "import"
    try:
        mcp_handoff = _load_handoff_worker(project_root, tools_dir)

        def fail_append(entry):
            raise OSError("simulated append failure")

        mcp_handoff._append_decision_unlocked = fail_append
        phase = "write_decision"
        try:
            result = mcp_handoff.write_decision("DECISION", "Must not succeed.")
        except OSError as exc:
            result_queue.put(
                {
                    "ok": True,
                    "worker": "append-failure-writer",
                    "outcome": "raised",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        else:
            result_queue.put(
                {
                    "ok": True,
                    "worker": "append-failure-writer",
                    "outcome": "returned",
                    "result": result,
                }
            )
    except Exception as exc:
        result_queue.put(_worker_failure("append-failure-writer", phase, exc))


def _partial_decision_writer_worker(
    project_root,
    tools_dir,
    partial_written,
    allow_finish,
    result_queue,
):
    phase = "import"
    try:
        mcp_handoff = _load_handoff_worker(project_root, tools_dir)

        def append_in_two_parts(entry):
            serialized = json.dumps(entry, ensure_ascii=False)
            split_at = max(1, len(serialized) // 2)
            with open(mcp_handoff.DECISIONS_FILE, "a", encoding="utf-8") as handle:
                handle.write(serialized[:split_at])
                handle.flush()
                partial_written.set()
                if not allow_finish.wait(timeout=10):
                    raise TimeoutError("partial writer was not released")
                handle.write(serialized[split_at:] + "\n")
                handle.flush()

        mcp_handoff._append_decision_unlocked = append_in_two_parts
        phase = "write_decision"
        result = mcp_handoff.write_decision(
            "DECISION",
            "Concurrent partial entry.",
        )
        result_queue.put({"ok": True, "worker": "partial-writer", "result": result})
    except Exception as exc:
        result_queue.put(_worker_failure("partial-writer", phase, exc))


def _brief_reader_worker(
    project_root,
    tools_dir,
    reader_started,
    reader_finished,
    result_queue,
):
    phase = "import"
    try:
        mcp_handoff = _load_handoff_worker(project_root, tools_dir)
        phase = "generate_brief"
        reader_started.set()
        result = mcp_handoff.generate_brief(current_task="Concurrent snapshot")
        result_queue.put({"ok": True, "worker": "brief-reader", "result": result})
    except Exception as exc:
        result_queue.put(_worker_failure("brief-reader", phase, exc))
    finally:
        reader_finished.set()


def _join_workers(workers, timeout=15):
    blocked = []
    failed = []
    for worker in workers:
        worker.join(timeout=timeout)
        if worker.is_alive():
            blocked.append(worker.name)
            worker.terminate()
            worker.join(timeout=5)
        elif worker.exitcode != 0:
            failed.append((worker.name, worker.exitcode))
    assert not blocked, f"worker timeout: {blocked}"
    assert not failed, f"worker exit failures: {failed}"


def _collect_worker_results(workers, result_queue, expected_count):
    try:
        payloads = [result_queue.get(timeout=15) for _ in range(expected_count)]
    finally:
        _join_workers(workers)
    failures = [payload for payload in payloads if not payload.get("ok")]
    if failures:
        # Preserve full worker traces in captured CI output, even when pytest
        # abbreviates assertion values or the verification runner keeps a tail.
        print(json.dumps(failures, indent=2, ensure_ascii=False))
        pytest.fail("spawned worker failures; full diagnostics above", pytrace=False)
    return payloads


def _load_session_module(tmp_project):
    """Load mcp_session with the temp project configured."""
    # Force re-import with new env vars
    os.environ["SESSION_PROJECT_ROOT"] = str(tmp_project)
    if "mcp_session" in sys.modules:
        del sys.modules["mcp_session"]
    import mcp_session
    # Override module-level paths
    mcp_session.PROJECT_ROOT = tmp_project
    mcp_session.DEVLOG_DIR = tmp_project / "devlog"
    mcp_session.STATUS_FILE = tmp_project / "STATUS.md"
    mcp_session.HISTORY_FILE = tmp_project / "STATUS_HISTORY.md"
    mcp_session.CLAUDE_MD = tmp_project / "CONTROLCODING.md"
    mcp_session.SESSION_NUMBER = 1
    return mcp_session


def test_import_does_not_allocate_session_counter(tmp_project):
    mod = _load_session_module(tmp_project)
    counter_file = tmp_project / ".controlcoding" / "session_counter.json"
    assert not counter_file.exists()

    mod.SESSION_NUMBER = None
    mod.update_status(what_done="Imported cleanly", what_next="Continue")

    assert counter_file.exists()
    assert "Session: 1" in (tmp_project / "STATUS.md").read_text(encoding="utf-8")


class TestUpdateStatus:
    def test_creates_status_file(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.update_status(
            what_done="Built the base project",
            what_next="Add lighting system",
        )
        assert "STATUS.md updated" in result
        assert (tmp_project / "STATUS.md").exists()

    def test_status_content(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.update_status(
            what_done="Built the base project",
            what_next="Add lighting system",
            blockers="None",
            notes="First session",
        )
        content = (tmp_project / "STATUS.md").read_text(encoding="utf-8")
        assert "Built the base project" in content
        assert "Add lighting system" in content
        assert "Session: 1" in content
        assert "First session" in content

    def test_archives_previous_status(self, tmp_project):
        mod = _load_session_module(tmp_project)
        # First update
        mod.update_status(what_done="Version 1", what_next="Do more")
        # Second update should archive the first
        result = mod.update_status(what_done="Version 2", what_next="Do even more")
        assert "Archived" in result
        assert (tmp_project / "STATUS_HISTORY.md").exists()
        history = (tmp_project / "STATUS_HISTORY.md").read_text(encoding="utf-8")
        assert "Version 1" in history

    def test_no_archive_on_first_update(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.update_status(what_done="First ever", what_next="Next")
        assert "No previous status" in result

    def test_empty_blockers_shows_none(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.update_status(what_done="Done", what_next="Next")
        content = (tmp_project / "STATUS.md").read_text(encoding="utf-8")
        assert "None" in content


class TestWriteDevlog:
    def test_creates_devlog_file(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.write_devlog(summary="Added lighting system")
        assert "Devlog created:" in result
        assert (tmp_project / "devlog").exists()
        files = list((tmp_project / "devlog").glob("*.md"))
        assert len(files) == 1

    def test_devlog_content(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.write_devlog(
            summary="Added lighting system",
            decisions="- Used Phong over PBR\n- 3 point lights",
            problems="- Shader compilation took 2 attempts",
            files_changed="- renderer/lighting.cpp\n- shaders/basic.frag",
        )
        files = list((tmp_project / "devlog").glob("*.md"))
        content = files[0].read_text(encoding="utf-8")
        assert "Added lighting system" in content
        assert "Phong over PBR" in content
        assert "Shader compilation" in content
        assert "lighting.cpp" in content

    def test_devlog_numbering(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.write_devlog(summary="First entry")
        mod.write_devlog(summary="Second entry")
        files = sorted((tmp_project / "devlog").glob("*.md"))
        assert len(files) == 2
        assert "_001_" in files[0].name
        assert "_002_" in files[1].name

    def test_devlog_slug(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.write_devlog(summary="Add Phong lighting with 3 point lights")
        files = list((tmp_project / "devlog").glob("*.md"))
        assert "add-phong-lighting-with" in files[0].name


class TestCheckpoint:
    def test_creates_both_status_and_devlog(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.checkpoint(
            summary="Built base project",
            what_next="Add lighting",
        )
        assert "Status:" in result
        assert "Devlog:" in result
        assert "CONTROLCODING.md:" in result
        assert (tmp_project / "STATUS.md").exists()
        devlogs = [p for p in (tmp_project / "devlog").glob("*.md") if p.name != "index.md"]
        assert len(devlogs) == 1

    def test_detects_claude_md_update_needed(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.checkpoint(
            summary="Modified engine",
            files_changed="src/core/engine.cpp, src/renderer/renderer.cpp",
        )
        assert "REVIEW NEEDED" in result
        assert "engine" in result.lower()

    def test_claude_md_ok_when_no_overlap(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.checkpoint(
            summary="Added new system",
            files_changed="src/systems/pathfinding.cpp",
        )
        assert "OK" in result

    def test_multiple_checkpoints_build_history(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.checkpoint(summary="First milestone", what_next="Second")
        mod.checkpoint(summary="Second milestone", what_next="Third")
        mod.checkpoint(summary="Third milestone", what_next="Done")

        # Should have 3 devlogs
        devlogs = [p for p in (tmp_project / "devlog").glob("*.md") if p.name != "index.md"]
        assert len(devlogs) == 3

        # History should have 2 archived entries (first and second)
        history = (tmp_project / "STATUS_HISTORY.md").read_text(encoding="utf-8")
        assert history.count("## Archived:") == 2

        # Current status should be third
        status = (tmp_project / "STATUS.md").read_text(encoding="utf-8")
        assert "Third milestone" in status

    def test_generates_devlog_index(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.checkpoint(summary="Built base project", what_next="Add lighting")
        index_path = tmp_project / "devlog" / "index.md"
        assert index_path.exists()
        index = index_path.read_text(encoding="utf-8")
        assert "Devlog Index" in index
        assert "built-base-project" in index

    def test_reviews_host_context_when_gateway_declares_agents_md(self, tmp_project):
        mod = _load_session_module(tmp_project)
        (tmp_project / ".controlcoding" / "gateway_config.json").write_text(
            '{"hostProfile":{"contextFile":"AGENTS.md"}}',
            encoding="utf-8",
        )
        (tmp_project / "AGENTS.md").write_text("# AGENTS.md\nengine renderer", encoding="utf-8")
        result = mod.checkpoint(
            summary="Modified engine",
            files_changed="src/core/engine.cpp",
        )
        assert "AGENTS.md:" in result


class TestReadStatus:
    def test_no_status_file(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.read_status()
        assert "No STATUS.md found" in result

    def test_reads_existing_status(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.update_status(what_done="Test content", what_next="More")
        result = mod.read_status()
        assert "Test content" in result


class TestReadHistory:
    def test_no_history(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.read_history()
        assert "No status history" in result

    def test_reads_history(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.update_status(what_done="V1", what_next="V2")
        mod.update_status(what_done="V2", what_next="V3")
        result = mod.read_history()
        assert "V1" in result

    def test_last_n_limit(self, tmp_project):
        mod = _load_session_module(tmp_project)
        for i in range(5):
            mod.update_status(what_done=f"Version {i}", what_next=f"Version {i+1}")
        result = mod.read_history(last_n=2)
        # Should have the last 2 entries
        assert "Version 3" in result or "Version 2" in result


class TestReadDevlog:
    def test_no_devlog_dir(self, tmp_project):
        mod = _load_session_module(tmp_project)
        result = mod.read_devlog()
        assert "No devlog" in result

    def test_reads_devlogs(self, tmp_project):
        mod = _load_session_module(tmp_project)
        mod.write_devlog(summary="Entry one")
        mod.write_devlog(summary="Entry two")
        result = mod.read_devlog(last_n=2)
        assert "Entry one" in result
        assert "Entry two" in result

    def test_last_n_limit(self, tmp_project):
        mod = _load_session_module(tmp_project)
        for i in range(5):
            mod.write_devlog(summary=f"Entry {i}")
        result = mod.read_devlog(last_n=2)
        assert "Entry 3" in result
        assert "Entry 4" in result


class TestSlugify:
    def test_basic(self, tmp_project):
        mod = _load_session_module(tmp_project)
        assert mod._slugify("Add Phong lighting") == "add-phong-lighting"

    def test_max_words(self, tmp_project):
        mod = _load_session_module(tmp_project)
        assert mod._slugify("one two three four five six", max_words=3) == "one-two-three"

    def test_special_chars(self, tmp_project):
        mod = _load_session_module(tmp_project)
        assert mod._slugify("Fix bug #123 in renderer!") == "fix-bug-123-in"


# ---------------------------------------------------------------------------
# F1 Context Handoff - write_decision
# ---------------------------------------------------------------------------


def _load_f1(tmp_project):
    """Load mcp_handoff with paths pointing at tmp_project."""
    os.environ["SESSION_PROJECT_ROOT"] = str(tmp_project)
    if "mcp_handoff" in sys.modules:
        del sys.modules["mcp_handoff"]
    import mcp_handoff
    sessions_dir = tmp_project / ".controlcoding" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    mcp_handoff.STATUS_FILE = tmp_project / "STATUS.md"
    mcp_handoff.SESSIONS_DIR = sessions_dir
    mcp_handoff.DECISIONS_FILE = sessions_dir / "decisions.jsonl"
    mcp_handoff.WARM_BRIEF_FILE = sessions_dir / "warm.md"
    return mcp_handoff


def _hold_windows_delete_pending(path):
    """Hold a real NTFS delete-pending entry, without changing permissions."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    dispose = kernel.SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    dispose.restype = wintypes.BOOL
    # GENERIC_READ | DELETE, share read/write/delete, CREATE_NEW.
    handle = create(str(path), 0x80000000 | 0x10000, 7, None, 1, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    pending = wintypes.BOOL(True)
    if not dispose(handle, 4, ctypes.byref(pending), ctypes.sizeof(pending)):
        error = ctypes.WinError(ctypes.get_last_error())
        close(handle)
        raise error

    def release():
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    return release


class TestAdopterLockfile:
    @pytest.mark.parametrize("platform_name, code, attempts", [
        ("nt", errno.EACCES, 2), ("posix", errno.EACCES, 1), ("nt", errno.EPERM, 1),
    ])
    def test_access_denial_keeps_original_error_and_never_enters_body(
        self, tmp_path, monkeypatch, platform_name, code, attempts
    ):
        import cc_lockfile
        from types import SimpleNamespace

        target = tmp_path / "resource.lock"
        error = PermissionError(code, "permanent access denial", str(target))
        calls = []
        def deny(*args):
            calls.append(args)
            raise error
        monkeypatch.setattr(cc_lockfile, "os", SimpleNamespace(
            name=platform_name, open=deny, close=os.close,
            O_CREAT=os.O_CREAT, O_EXCL=os.O_EXCL, O_WRONLY=os.O_WRONLY,
        ))
        ticks = iter([0.0, 0.0, 1.0])
        monkeypatch.setattr(cc_lockfile.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(cc_lockfile.time, "sleep", lambda _: None)
        guard = cc_lockfile.LockfileGuard(target, timeout_seconds=1, poll_seconds=0.1)
        with pytest.raises(PermissionError) as caught:
            with guard:
                pytest.fail("access denial entered the protected body")
        assert caught.value is error
        assert len(calls) == attempts
        assert not guard._acquired and not target.exists()

    def test_close_error_is_not_retried_as_acquisition(self, tmp_path, monkeypatch):
        import cc_lockfile

        real_open, real_close = cc_lockfile.os.open, cc_lockfile.os.close
        calls = []
        def record_open(*args):
            calls.append(args)
            return real_open(*args)
        error = PermissionError(errno.EACCES, "close failure")
        def fail_close(descriptor):
            real_close(descriptor)
            raise error
        monkeypatch.setattr(cc_lockfile.os, "open", record_open)
        monkeypatch.setattr(cc_lockfile.os, "close", fail_close)
        guard = cc_lockfile.LockfileGuard(tmp_path / "resource.lock")
        try:
            with pytest.raises(PermissionError) as caught:
                with guard:
                    pytest.fail("close failure entered protected body")
            assert caught.value is error and len(calls) == 1
            assert guard._acquired and guard.lock_path.exists()
        finally:
            guard.release()

    @pytest.mark.skipif(os.name != "nt", reason="Windows delete-pending semantics")
    def test_delete_pending_timeout_preserves_data_and_guard_reuses(self, tmp_path):
        import cc_lockfile

        data = tmp_path / "decisions.jsonl"
        data.write_bytes(b"existing decision\n")
        original = (data.read_bytes(), data.stat().st_mtime_ns)
        lock = cc_lockfile.adjacent_lockfile_path(data)
        release = _hold_windows_delete_pending(lock)
        guard = cc_lockfile.LockfileGuard(lock, timeout_seconds=0.02, poll_seconds=0.005)
        try:
            with pytest.raises(PermissionError) as caught:
                with guard:
                    pytest.fail("delete-pending lock entered the protected body")
            assert caught.value.errno == errno.EACCES
            assert not guard._acquired
            assert (data.read_bytes(), data.stat().st_mtime_ns) == original
        finally:
            release()
        with guard:
            assert lock.exists()
        assert not lock.exists()

    def test_validation_and_adjacent_path(self, tmp_path):
        import cc_lockfile

        for value in (-1, float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError):
                cc_lockfile.LockfileGuard(tmp_path / "x.lock", timeout_seconds=value)
            with pytest.raises(ValueError):
                cc_lockfile.LockfileGuard(tmp_path / "x.lock", poll_seconds=value)
        assert cc_lockfile.adjacent_lockfile_path(tmp_path / "data.jsonl") == tmp_path / "data.jsonl.lock"

    def test_acquire_release_reuse_and_noop_release(self, tmp_path):
        import cc_lockfile

        lock_path = tmp_path / "missing" / "resource.lock"
        guard = cc_lockfile.LockfileGuard(lock_path, timeout_seconds=0, poll_seconds=0)
        guard.release()
        with guard:
            assert lock_path.exists()
            with pytest.raises(RuntimeError):
                guard.__enter__()
        assert not lock_path.exists()
        with guard:
            assert lock_path.exists()
        assert not lock_path.exists()

    def test_timeout_does_not_remove_existing_lock(self, tmp_path):
        import cc_lockfile

        lock_path = tmp_path / "resource.lock"
        lock_path.write_text("owned", encoding="utf-8")
        with pytest.raises(cc_lockfile.LockfileTimeoutError):
            with cc_lockfile.lockfile_guard(lock_path, timeout_seconds=0, poll_seconds=0):
                pass
        assert lock_path.read_text(encoding="utf-8") == "owned"

    def test_release_tolerates_missing_owned_lock(self, tmp_path):
        import cc_lockfile

        lock_path = tmp_path / "resource.lock"
        guard = cc_lockfile.LockfileGuard(lock_path)
        guard.__enter__()
        lock_path.unlink()
        guard.release()
        with guard:
            assert lock_path.exists()

    def test_body_exception_releases_and_allows_same_guard_reuse(self, tmp_path):
        import cc_lockfile

        lock_path = tmp_path / "resource.lock"
        guard = cc_lockfile.LockfileGuard(lock_path)
        with pytest.raises(ValueError, match="body"):
            with guard:
                assert lock_path.exists()
                raise ValueError("body")
        assert not lock_path.exists()
        with guard:
            assert lock_path.exists()
        assert not lock_path.exists()

    def test_cleanup_preserves_body_error_and_propagates_cleanup_error(self, tmp_path, monkeypatch):
        import cc_lockfile

        guard = cc_lockfile.LockfileGuard(tmp_path / "resource.lock")
        with pytest.raises(ValueError, match="body"):
            with guard:
                monkeypatch.setattr(Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("cleanup")))
                raise ValueError("body")
        monkeypatch.undo()

        guard = cc_lockfile.LockfileGuard(tmp_path / "other.lock")
        with pytest.raises(OSError, match="cleanup"):
            with guard:
                monkeypatch.setattr(Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("cleanup")))


class TestConcurrentDecisions:
    @pytest.mark.skipif(os.name != "nt", reason="Windows delete-pending semantics")
    def test_delete_pending_lock_waits_before_appending(self, tmp_project, monkeypatch):
        import cc_lockfile

        handoff = _load_f1(tmp_project)
        assert handoff.write_decision("DECISION", "Existing entry.").startswith("Decision recorded: DEC-001")
        data = handoff.DECISIONS_FILE
        original = (data.read_bytes(), data.stat().st_mtime_ns)
        lock = cc_lockfile.adjacent_lockfile_path(data)
        release = _hold_windows_delete_pending(lock)
        encountered = threading.Event()
        released = threading.Event()
        errors = []

        def owner():
            try:
                if not encountered.wait(5):
                    raise TimeoutError("contender never reached pending entry")
            except Exception as exc:
                errors.append(exc)
            finally:
                try:
                    release()
                except Exception as exc:
                    errors.append(exc)
                released.set()

        thread = threading.Thread(target=owner)
        thread.start()
        real_open = cc_lockfile.os.open
        observations = []
        def observe_open(path, flags, *args, **kwargs):
            try:
                return real_open(path, flags, *args, **kwargs)
            except PermissionError as exc:
                if Path(path) == lock:
                    observations.append((exc.errno, data.read_bytes(), data.stat().st_mtime_ns))
                    encountered.set()
                raise
        monkeypatch.setattr(cc_lockfile.os, "open", observe_open)
        try:
            result = handoff.write_decision("DECISION", "After lock release.")
        finally:
            encountered.set()
            thread.join(6)
        assert not thread.is_alive() and released.is_set() and not errors
        assert observations and all(row == (errno.EACCES, *original) for row in observations)
        assert result.startswith("Decision recorded: DEC-002")
        records = [json.loads(line) for line in data.read_text(encoding="utf-8").splitlines()]
        assert [(row["id"], row["text"]) for row in records] == [
            ("DEC-001", "Existing entry."), ("DEC-002", "After lock release.")]
        assert not lock.exists()

    def test_spawn_writers_receive_unique_sequential_ids(self, tmp_project):
        context = multiprocessing.get_context("spawn")
        worker_count = 6
        barrier = context.Barrier(worker_count)
        result_queue = context.Queue()
        tools_dir = str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
        workers = [
            context.Process(
                target=_decision_writer_worker,
                args=(str(tmp_project), tools_dir, barrier, result_queue),
            )
            for _ in range(worker_count)
        ]
        for worker in workers:
            worker.start()
        payloads = _collect_worker_results(workers, result_queue, len(workers))
        results = [payload["result"] for payload in payloads]
        records = [json.loads(line) for line in (tmp_project / ".controlcoding" / "sessions" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        assert {record["id"] for record in records} == {f"DEC-{number:03d}" for number in range(1, worker_count + 1)}
        assert all(f"Decision recorded: {record['id']}" in "\n".join(results) for record in records)

    def test_spawn_duplicate_explicit_id_has_one_winner(self, tmp_project):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        result_queue = context.Queue()
        tools_dir = str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
        workers = [
            context.Process(
                target=_decision_writer_worker,
                args=(str(tmp_project), tools_dir, barrier, result_queue, "DEC-777"),
            )
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        payloads = _collect_worker_results(workers, result_queue, len(workers))
        results = [payload["result"] for payload in payloads]
        assert sum(result.startswith("Decision recorded:") for result in results) == 1
        assert sum(result == "ERROR: ID 'DEC-777' already exists. Use a different ID or leave decision_id empty to auto-assign." for result in results) == 1
        assert len((tmp_project / ".controlcoding" / "sessions" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()) == 1

    def test_spawn_preexisting_lock_times_out_without_mutation(self, tmp_project):
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        tools_dir = str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
        sessions_dir = tmp_project / ".controlcoding" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        decisions_file = sessions_dir / "decisions.jsonl"
        original_bytes = (
            json.dumps(
                {
                    "id": "DEC-001",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "type": "DECISION",
                    "text": "Existing entry.",
                    "supersedes": None,
                }
            )
            + "\n"
        ).encode("utf-8")
        decisions_file.write_bytes(original_bytes)
        lock_path = decisions_file.with_name(f"{decisions_file.name}.lock")
        lock_bytes = b"existing owner"
        lock_path.write_bytes(lock_bytes)
        worker = context.Process(
            target=_decision_lock_timeout_worker,
            args=(str(tmp_project), tools_dir, result_queue),
        )

        worker.start()
        payload = _collect_worker_results([worker], result_queue, 1)[0]

        assert payload["result"].startswith("ERROR: timed out waiting for lock:")
        assert decisions_file.read_bytes() == original_bytes
        assert lock_path.read_bytes() == lock_bytes

    def test_spawn_append_failure_has_no_false_success_and_cleans_lock(self, tmp_project):
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        tools_dir = str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
        worker = context.Process(
            target=_decision_append_failure_worker,
            args=(str(tmp_project), tools_dir, result_queue),
        )

        worker.start()
        payload = _collect_worker_results([worker], result_queue, 1)[0]

        decisions_file = tmp_project / ".controlcoding" / "sessions" / "decisions.jsonl"
        lock_path = decisions_file.with_name(f"{decisions_file.name}.lock")
        assert payload["outcome"] == "raised"
        assert payload["error_type"] == "OSError"
        assert payload["error"] == "simulated append failure"
        assert "result" not in payload
        assert not decisions_file.exists()
        assert not lock_path.exists()

    def test_spawn_reader_waits_for_complete_writer_snapshot(self, tmp_project):
        context = multiprocessing.get_context("spawn")
        partial_written = context.Event()
        allow_finish = context.Event()
        reader_started = context.Event()
        reader_finished = context.Event()
        writer_queue = context.Queue()
        reader_queue = context.Queue()
        tools_dir = str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
        decisions_file = tmp_project / ".controlcoding" / "sessions" / "decisions.jsonl"
        lock_path = decisions_file.with_name(f"{decisions_file.name}.lock")
        writer = context.Process(
            target=_partial_decision_writer_worker,
            args=(
                str(tmp_project),
                tools_dir,
                partial_written,
                allow_finish,
                writer_queue,
            ),
        )
        reader = context.Process(
            target=_brief_reader_worker,
            args=(
                str(tmp_project),
                tools_dir,
                reader_started,
                reader_finished,
                reader_queue,
            ),
        )
        started_workers = []

        writer.start()
        started_workers.append(writer)
        try:
            assert partial_written.wait(timeout=10)
            partial_bytes = decisions_file.read_bytes()
            assert partial_bytes
            with pytest.raises(json.JSONDecodeError):
                json.loads(partial_bytes.decode("utf-8"))
            assert lock_path.exists()

            reader.start()
            started_workers.append(reader)
            assert reader_started.wait(timeout=10)
            assert not reader_finished.wait(timeout=0.25)

            allow_finish.set()
            writer_payload = writer_queue.get(timeout=15)
            reader_payload = reader_queue.get(timeout=15)
        finally:
            allow_finish.set()
            _join_workers(started_workers)

        failures = [
            payload
            for payload in (writer_payload, reader_payload)
            if not payload.get("ok")
        ]
        assert not failures, failures
        assert writer_payload["result"].startswith("Decision recorded: DEC-001")
        assert not reader_payload["result"].startswith("ERROR:")
        records = [
            json.loads(line)
            for line in decisions_file.read_text(encoding="utf-8").splitlines()
        ]
        assert len(records) == 1
        assert records[0]["text"] == "Concurrent partial entry."
        warm_brief = (tmp_project / ".controlcoding" / "sessions" / "warm.md").read_text(
            encoding="utf-8"
        )
        assert "> Concurrent partial entry." in warm_brief
        assert not lock_path.exists()


class TestWriteDecision:
    def test_creates_decisions_jsonl(self, tmp_project):
        """write_decision creates decisions.jsonl on first call."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Use JSONL because append-only and no locking needed.")
        assert mod.DECISIONS_FILE.exists()

    def test_entry_is_valid_json(self, tmp_project):
        """Each line in decisions.jsonl is valid JSON."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Use JSONL because append-only.")
        import json
        lines = [l for l in mod.DECISIONS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "DECISION"
        assert entry["id"] == "DEC-001"

    def test_text_stored_verbatim(self, tmp_project):
        """REQ-HO-01: text field is the exact string passed in, not paraphrased."""
        import json
        mod = _load_f1(tmp_project)
        verbatim = "Use hub-and-spoke topology because it centralizes routing logic and avoids N*M direct connections between panels."
        mod.write_decision("DECISION", verbatim)
        entry = json.loads(mod.DECISIONS_FILE.read_text(encoding="utf-8").strip())
        assert entry["text"] == verbatim

    def test_auto_id_sequencing(self, tmp_project):
        """Auto-IDs increment correctly per type prefix."""
        import json
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "First decision.")
        mod.write_decision("DECISION", "Second decision.")
        mod.write_decision("CONSTRAINT", "First constraint.")
        lines = mod.DECISIONS_FILE.read_text(encoding="utf-8").splitlines()
        ids = [json.loads(l)["id"] for l in lines]
        assert ids == ["DEC-001", "DEC-002", "CON-001"]

    def test_all_five_types_accepted(self, tmp_project):
        """All five decision types are accepted and stored with correct prefix."""
        import json
        mod = _load_f1(tmp_project)
        for dtype, prefix in [
            ("DECISION", "DEC"),
            ("CONSTRAINT", "CON"),
            ("PLAN", "PLAN"),
            ("FINDING", "FIND"),
            ("QUESTION", "Q"),
        ]:
            mod.write_decision(dtype, f"A {dtype} entry.")
        lines = mod.DECISIONS_FILE.read_text(encoding="utf-8").splitlines()
        ids = [json.loads(l)["id"] for l in lines]
        assert ids[0].startswith("DEC-")
        assert ids[1].startswith("CON-")
        assert ids[2].startswith("PLAN-")
        assert ids[3].startswith("FIND-")
        assert ids[4].startswith("Q-")

    def test_invalid_type_rejected(self, tmp_project):
        """Unknown decision_type returns ERROR and writes nothing."""
        mod = _load_f1(tmp_project)
        result = mod.write_decision("MEMO", "Some text.")
        assert "ERROR" in result
        assert not mod.DECISIONS_FILE.exists()

    def test_empty_text_rejected(self, tmp_project):
        """Empty text returns ERROR."""
        mod = _load_f1(tmp_project)
        result = mod.write_decision("DECISION", "   ")
        assert "ERROR" in result

    def test_manual_id_accepted(self, tmp_project):
        """Explicit decision_id is used as-is."""
        import json
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Some decision.", decision_id="DEC-099")
        entry = json.loads(mod.DECISIONS_FILE.read_text(encoding="utf-8").strip())
        assert entry["id"] == "DEC-099"

    def test_duplicate_id_rejected(self, tmp_project):
        """Writing a duplicate ID returns ERROR without modifying the file."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "First.", decision_id="DEC-001")
        result = mod.write_decision("DECISION", "Second.", decision_id="DEC-001")
        assert "ERROR" in result
        lines = [l for l in mod.DECISIONS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1  # only the first entry

    def test_supersedes_recorded(self, tmp_project):
        """supersedes field is stored in the entry."""
        import json
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Original approach.", decision_id="DEC-001")
        mod.write_decision("DECISION", "Revised approach.", supersedes="DEC-001")
        lines = mod.DECISIONS_FILE.read_text(encoding="utf-8").splitlines()
        second = json.loads(lines[1])
        assert second["supersedes"] == "DEC-001"

    def test_return_value_contains_id(self, tmp_project):
        """Return value includes the assigned ID."""
        mod = _load_f1(tmp_project)
        result = mod.write_decision("CONSTRAINT", "Python stdlib only in hooks.")
        assert "CON-001" in result


# ---------------------------------------------------------------------------
# F1 Context Handoff - generate_brief
# ---------------------------------------------------------------------------


class TestGenerateBrief:
    def test_creates_warm_md(self, tmp_project):
        """generate_brief creates warm.md."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Use JSONL for decisions.")
        mod.generate_brief(current_task="Testing warm.md generation")
        assert mod.WARM_BRIEF_FILE.exists()

    def test_warm_md_contains_verbatim_text(self, tmp_project):
        """REQ-HO-02: warm.md contains the exact decision text as a blockquote."""
        mod = _load_f1(tmp_project)
        verbatim = "Use hub-and-spoke topology because it centralizes routing and avoids N*M connections."
        mod.write_decision("DECISION", verbatim)
        mod.generate_brief(current_task="Topology decision")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        # The verbatim text must appear as a blockquote line
        assert f"> {verbatim}" in warm

    def test_superseded_entry_excluded(self, tmp_project):
        """REQ-HO-03: superseded entries are omitted from warm.md."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Old approach: polling.", decision_id="DEC-001")
        mod.write_decision("DECISION", "New approach: file watcher.", supersedes="DEC-001")
        mod.generate_brief(current_task="File watching")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        assert "Old approach: polling." not in warm
        assert "New approach: file watcher." in warm

    def test_warm_md_under_6kb_by_default(self, tmp_project):
        """generate_brief output is under 6KB for a normal set of decisions."""
        mod = _load_f1(tmp_project)
        for i in range(5):
            mod.write_decision("DECISION", f"Decision {i}: use approach X because it solves problem Y.")
        mod.generate_brief(current_task="Normal session")
        size = len(mod.WARM_BRIEF_FILE.read_bytes())
        assert size < 6 * 1024

    def test_truncation_by_omission_not_compression(self, tmp_project):
        """REQ-HO-04: when over 6KB, oldest entries are dropped, not compressed."""
        mod = _load_f1(tmp_project)
        # Write 40 long decisions to exceed the 6KB limit
        long_text = "A" * 300  # 300 chars each
        for i in range(40):
            mod.write_decision("DECISION", f"Decision {i:02d}: {long_text}")
        mod.generate_brief(current_task="Overflow test")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        size = len(warm.encode("utf-8"))
        assert size <= 6 * 1024
        # Truncation notice must be present
        assert "truncated" in warm.lower()
        # Later (retained) decisions must appear verbatim - not compressed
        assert f"Decision 39: {long_text}" in warm

    def test_warm_md_contains_current_task(self, tmp_project):
        """generate_brief includes the current_task line."""
        mod = _load_f1(tmp_project)
        mod.generate_brief(current_task="Implementing WebSocket Gateway")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        assert "Implementing WebSocket Gateway" in warm

    def test_warm_md_contains_search_pointer(self, tmp_project):
        """warm.md always includes a pointer to the full decisions.jsonl log."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Some decision.")
        mod.generate_brief(current_task="Anything")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        assert "decisions.jsonl" in warm

    def test_decisions_constraints_plans_questions_in_separate_sections(self, tmp_project):
        """generate_brief groups types into separate labelled sections."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Decision text.")
        mod.write_decision("CONSTRAINT", "Constraint text.")
        mod.write_decision("PLAN", "Plan text.")
        mod.write_decision("QUESTION", "Question text.")
        mod.generate_brief(current_task="Multi-type test")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        assert "## Active decisions" in warm
        assert "## Active constraints" in warm
        assert "## Active plans" in warm
        assert "## Open questions" in warm

    def test_empty_decisions_still_generates(self, tmp_project):
        """generate_brief works even with no decisions recorded."""
        mod = _load_f1(tmp_project)
        result = mod.generate_brief(current_task="Empty session")
        assert mod.WARM_BRIEF_FILE.exists()
        assert "ERROR" not in result

    def test_generate_brief_overwrites_previous(self, tmp_project):
        """Calling generate_brief twice overwrites warm.md, not appends."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "First session decision.")
        mod.generate_brief(current_task="First session")
        mod.write_decision("DECISION", "Second session decision.")
        mod.generate_brief(current_task="Second session")
        warm = mod.WARM_BRIEF_FILE.read_text(encoding="utf-8")
        # Both decisions still in jsonl so both appear, but file was overwritten not appended
        assert warm.count("# Context Brief") == 1


# ---------------------------------------------------------------------------
# F1 Context Handoff - cc resume --brief
# ---------------------------------------------------------------------------


class TestCcResumeGlobalBrief:
    @pytest.mark.parametrize("brief_only", [False, True])
    def test_global_resume_never_launches_subprocess(
            self, tmp_project, capsys, brief_only):
        import cc
        sessions_dir = tmp_project / ".controlcoding" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "warm.md").write_text(
            "# Provider-neutral brief\n", encoding="utf-8"
        )
        with patch.object(cc.subprocess, "run") as subprocess_run:
            result = cc.cmd_resume(
                project=tmp_project, brief_only=brief_only
            )
        assert result == 0
        assert "Provider-neutral brief" in capsys.readouterr().out
        subprocess_run.assert_not_called()

    @pytest.mark.parametrize(
        "agent",
        ["concierge", "architect", "coder", "reviewer", "debugger"],
    )
    @pytest.mark.parametrize("brief_only", [False, True])
    def test_agent_resume_variants_never_launch_subprocess(
            self, tmp_project, capsys, agent, brief_only):
        import cc
        state_dir = (
            tmp_project / ".controlcoding" / "agents" / agent
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(json.dumps({
            "current_task": f"Resume {agent}",
            "current_task_id": "TASK-001",
            "phase": "active",
        }), encoding="utf-8")
        with patch.object(cc.subprocess, "run") as subprocess_run:
            result = cc.cmd_resume(
                project=tmp_project,
                brief_only=brief_only,
                agent=agent,
            )
        assert result == 0
        assert f"Resume Brief: {agent}" in capsys.readouterr().out
        subprocess_run.assert_not_called()

    def test_invalid_agent_is_rejected_before_subprocess(
            self, tmp_project, capsys):
        import cc
        with patch.object(cc.subprocess, "run") as subprocess_run:
            result = cc.cmd_resume(
                project=tmp_project, agent="not-an-agent"
            )
        assert result == 1
        assert "Unknown resume agent" in capsys.readouterr().out
        subprocess_run.assert_not_called()

    def test_brief_flag_prints_warm_md(self, tmp_project, capsys):
        """cc resume --brief prints warm.md content to stdout."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import cc
        # Write a warm.md manually
        sessions_dir = tmp_project / ".controlcoding" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        warm_path = sessions_dir / "warm.md"
        warm_path.write_text("# Context Brief\n\nTest content here.\n", encoding="utf-8")
        result = cc.cmd_resume(project=tmp_project, brief_only=True)
        captured = capsys.readouterr()
        assert result == 0
        assert "Test content Brief" in captured.out or "Context Brief" in captured.out

    def test_no_warm_md_returns_error(self, tmp_project):
        """cc resume --brief returns non-zero if warm.md does not exist."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import cc
        result = cc.cmd_resume(project=tmp_project, brief_only=True)
        assert result != 0


# ---------------------------------------------------------------------------
# F1 Context Handoff - active_entries / supersedes logic
# ---------------------------------------------------------------------------


class TestActiveEntries:
    def test_superseded_entry_not_active(self, tmp_project):
        """_active_entries excludes entries superseded by a later one."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Old.", decision_id="DEC-001")
        mod.write_decision("DECISION", "New.", supersedes="DEC-001")
        entries = mod._load_decisions()
        active = mod._active_entries(entries)
        active_ids = [e["id"] for e in active]
        assert "DEC-001" not in active_ids
        assert "DEC-002" in active_ids

    def test_non_superseded_entry_is_active(self, tmp_project):
        """_active_entries includes entries that have not been superseded."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "Stand-alone decision.")
        entries = mod._load_decisions()
        active = mod._active_entries(entries)
        assert len(active) == 1

    def test_chain_supersede(self, tmp_project):
        """Only the final entry in a supersede chain is active."""
        mod = _load_f1(tmp_project)
        mod.write_decision("DECISION", "v1", decision_id="DEC-001")
        mod.write_decision("DECISION", "v2", supersedes="DEC-001", decision_id="DEC-002")
        mod.write_decision("DECISION", "v3", supersedes="DEC-002", decision_id="DEC-003")
        entries = mod._load_decisions()
        active = mod._active_entries(entries)
        active_ids = [e["id"] for e in active]
        assert active_ids == ["DEC-003"]
