"""Tests for request_lift, Bash write checks, and boundary enforcement."""

import importlib.util
import io
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


HOOK_SOURCE_DIR = Path(__file__).resolve().parents[1] / "templates" / "hooks"
HOOK_FILES = (
    "check_bash_writes.py",
    "check_boundaries.py",
    "check_dangerous_commands.py",
    "feature_lock.py",
    "hook_logger.py",
    "hook_utils.py",
    "request_lift.py",
)


class HookProject:
    """A copied hook pack running inside an isolated temporary Git project."""

    def __init__(self, root, hooks_dir, runtime_dir, env):
        self.root = root
        self.hooks_dir = hooks_dir
        self.runtime_dir = runtime_dir
        self.env = env
        self.control_dir = root / ".controlcoding"
        self.lift_path = self.control_dir / "lift_request.json"
        self.hook_log_path = root / "cc_hook_log.jsonl"

    def run_hook(self, script, payload, *, raw=False):
        return subprocess.run(
            [sys.executable, str(self.hooks_dir / script)],
            input=payload if raw else json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=self.root,
            env=self.env,
            timeout=30,
            check=False,
        )

    def run_lift(self, *args):
        return subprocess.run(
            [sys.executable, str(self.hooks_dir / "request_lift.py"), *args],
            capture_output=True,
            text=True,
            cwd=self.root,
            env=self.env,
            timeout=30,
            check=False,
        )


def _run_git(root, env, *args):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
        timeout=30,
        check=False,
    )


@pytest.fixture
def hook_project(tmp_path):
    """Install the production hook pack in a clean, disposable Git project."""
    root = tmp_path / "project with spaces-caf\u00e9"
    hooks_dir = root / "hooks"
    control_dir = root / ".controlcoding"
    runtime_dir = root / ".runtime"
    core_dir = root / "core"
    for directory in (hooks_dir, control_dir, runtime_dir, core_dir):
        directory.mkdir(parents=True)

    for filename in HOOK_FILES:
        shutil.copy2(HOOK_SOURCE_DIR / filename, hooks_dir / filename)

    (control_dir / "cc_config.json").write_text(
        json.dumps(
            {
                "protected_zones": [
                    {
                        "path": "core/",
                        "level": "deny",
                        "description": "Temporary test core",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "test.py").write_text("# protected test file\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "CONTROLCODING_PROJECT_ROOT": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(runtime_dir),
            "TMP": str(runtime_dir),
            "TMPDIR": str(runtime_dir),
        }
    )
    env.pop("CC_ACTIVE_MODULE", None)
    env.pop("PYTHONPATH", None)

    init_result = _run_git(root, env, "init", "--quiet")
    assert init_result.returncode == 0, init_result.stderr
    add_result = _run_git(root, env, "add", "--all")
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(
        root,
        env,
        "-c",
        "user.name=ControlCoding Tests",
        "-c",
        "user.email=tests@controlcoding.invalid",
        "commit",
        "--quiet",
        "-m",
        "temporary hook test baseline",
    )
    assert commit_result.returncode == 0, commit_result.stderr

    project = HookProject(root, hooks_dir, runtime_dir, env)
    yield project

    if project.lift_path.exists():
        clear_result = project.run_lift("--clear")
        assert clear_result.returncode == 0
    assert not project.lift_path.exists()
    for hook_log in tmp_path.rglob("cc_hook_log.jsonl"):
        assert hook_log.resolve().is_relative_to(tmp_path.resolve())


def _edit_payload(file_path):
    return {"tool_name": "Edit", "tool_input": {"file_path": file_path}}


def _bash_payload(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _create_pending_lift(project):
    result = project.run_lift(
        "--file",
        "core/test.py",
        "--reason",
        "unit test",
    )
    assert result.returncode == 0
    return result


def test_t1_boundaries_empty_input_allows(hook_project):
    result = hook_project.run_hook("check_boundaries.py", {})
    assert result.returncode == 0


def test_t2_boundaries_self_protection_blocks(hook_project):
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("hooks/check_boundaries.py"),
    )
    assert result.returncode == 2
    assert "self-protected" in result.stdout
    assert hook_project.hook_log_path.exists()
    assert hook_project.hook_log_path.resolve().is_relative_to(
        hook_project.root.resolve()
    )


def test_t3_request_lift_is_self_protected(hook_project):
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("hooks/request_lift.py"),
    )
    assert result.returncode == 2
    assert "self-protected" in result.stdout


def test_t4_bash_write_hook_is_self_protected(hook_project):
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("hooks/check_bash_writes.py"),
    )
    assert result.returncode == 2
    assert "self-protected" in result.stdout


def test_t5_safe_command_allows(hook_project):
    result = hook_project.run_hook(
        "check_dangerous_commands.py",
        _bash_payload("ls -la"),
    )
    assert result.returncode == 0


def test_t6_git_reset_hard_blocks(hook_project):
    result = hook_project.run_hook(
        "check_dangerous_commands.py",
        _bash_payload("git reset --hard HEAD~1"),
    )
    assert result.returncode == 2
    assert "block" in result.stdout


def test_t7_non_bash_tool_skips_bash_write_check(hook_project):
    result = hook_project.run_hook(
        "check_bash_writes.py",
        {"tool_name": "Edit"},
    )
    assert result.returncode == 0


def test_t8_clean_bash_command_preserves_project(hook_project):
    result = hook_project.run_hook(
        "check_bash_writes.py",
        _bash_payload("ls"),
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert (hook_project.root / "core" / "test.py").exists()


def _use_module_perimeter(project):
    """Exercise module denial independently of configured DENY zones."""
    (project.control_dir / "cc_config.json").write_text(
        json.dumps({"protected_zones": []}), encoding="utf-8",
    )
    module_dir = project.root / "modules" / "active"
    module_dir.mkdir(parents=True)
    (module_dir / ".feature-lock.json").write_text(
        json.dumps({"version": 1, "module": "active", "owns": ["modules/active/**"]}),
        encoding="utf-8",
    )
    (project.control_dir / "active_module.json").write_text(
        json.dumps({"module": "active", "mode": "enforce"}), encoding="utf-8",
    )
    # Keep the policy itself clean so the old hook cannot erase the policy
    # before reaching the user file that is the counterexample.
    staged = _run_git(project.root, project.env, "add", "--", ".controlcoding", "modules")
    assert staged.returncode == 0, staged.stderr
    committed = _run_git(
        project.root, project.env,
        "-c", "user.name=ControlCoding Tests",
        "-c", "user.email=tests@controlcoding.invalid",
        "commit", "--quiet", "-m", "temporary module test policy",
    )
    assert committed.returncode == 0, committed.stderr


@pytest.mark.parametrize("protection", ["deny", "module"])
@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_f2_read_command_preserves_existing_work(hook_project, protection, tracked):
    if protection == "module":
        _use_module_perimeter(hook_project)
    target = hook_project.root / "core" / ("test.py" if tracked else "new.py")
    if tracked:
        target.write_bytes(b"# staged user work\r\n")
        staged = _run_git(hook_project.root, hook_project.env, "add", "--", "core/test.py")
        assert staged.returncode == 0, staged.stderr
    user_content = b"# pre-existing unstaged user work\r\n"
    target.write_bytes(user_content)
    index_path = hook_project.root / ".git" / "index"
    index_before = index_path.read_bytes()

    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("ls"))

    assert result.returncode == 0
    assert target.exists(), "post-action inspection deleted pre-existing work"
    assert target.read_bytes() == user_content
    assert index_path.read_bytes() == index_before
    message = _observation_message(result.stdout)
    assert "core/" + target.name in message
    assert ("DENY zone" if protection == "deny" else "module perimeter") in message
    assert "may predate this command" in message
    events = [json.loads(line) for line in hook_project.hook_log_path.read_text().splitlines()]
    assert events and all(event["decision"] == "WARN" for event in events)


def _observation_message(output):
    # A single JSON object is the host contract, including both user and agent
    # feedback. The previous top-level decision="warn" was not that contract.
    payload = json.loads(output)
    message = payload["systemMessage"]
    assert payload["hookSpecificOutput"] == {
        "hookEventName": "PostToolUse", "additionalContext": message,
    }
    assert "decision" not in payload
    return message


@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_f2_actual_command_write_is_preserved_for_review(hook_project, tracked):
    relative = "core/test.py" if tracked else "core/created.py"
    command = (
        "from pathlib import Path; "
        f"Path({relative!r}).write_bytes(b'# written by the command\\n')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command], cwd=hook_project.root,
        env=hook_project.env, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = hook_project.run_hook("check_bash_writes.py", _bash_payload(command))
    assert result.returncode == 0
    assert (hook_project.root / relative).read_bytes() == b"# written by the command\n"
    assert "cannot attribute" in _observation_message(result.stdout)


@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_f2_spaces_unicode_and_whitespace_in_paths(hook_project, tracked):
    target = hook_project.root / "core" / " leading space-caf\u00e9-\u6587\u4ef6.py"
    target.write_bytes(b"staged baseline\n")
    if tracked:
        result = _run_git(hook_project.root, hook_project.env, "add", "--", str(target))
        assert result.returncode == 0, result.stderr
    target.write_bytes(b"user bytes\r\n")
    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("pwd"))
    assert result.returncode == 0
    assert target.read_bytes() == b"user bytes\r\n"
    # Filenames are escaped inside the message as well as in the JSON envelope.
    assert json.dumps("core/" + target.name) in _observation_message(result.stdout)


def test_f2_tracked_deletion_is_reported_without_restoration(hook_project):
    target = hook_project.root / "core" / "test.py"
    target.unlink()
    index = hook_project.root / ".git" / "index"
    before = index.read_bytes()
    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("ls"))
    assert result.returncode == 0
    assert not target.exists()
    assert index.read_bytes() == before
    assert "core/test.py" in _observation_message(result.stdout)


@pytest.mark.parametrize("raw_input", ["", "{", "null", "[]", '"text"', '{"tool_name":"Edit"}'])
def test_f2_invalid_or_unrelated_input_preserves_dirty_work(hook_project, raw_input):
    target = hook_project.root / "core" / "test.py"
    target.write_bytes(b"pre-existing edit\n")
    result = hook_project.run_hook("check_bash_writes.py", raw_input, raw=True)
    assert result.returncode == 0
    assert result.stdout == ""
    assert target.read_bytes() == b"pre-existing edit\n"


def test_f2_unprotected_dirty_file_is_preserved_without_warning(hook_project):
    target = hook_project.root / "notes.txt"
    target.write_bytes(b"unrelated work\n")
    result = hook_project.run_hook("check_bash_writes.py", {"tool_name": "Bash"})
    assert result.returncode == 0
    assert result.stdout == ""
    assert target.read_bytes() == b"unrelated work\n"


def test_f2_staged_only_protected_content_is_not_reported(hook_project):
    target = hook_project.root / "core" / "test.py"
    expected = b"staged protected content\n"
    target.write_bytes(expected)
    staged = _run_git(hook_project.root, hook_project.env, "add", "--", "core/test.py")
    assert staged.returncode == 0, staged.stderr
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("pwd"))

    assert result.returncode == 0
    assert result.stdout == ""
    assert target.read_bytes() == expected
    assert index.read_bytes() == index_before


def test_f2_ignored_untracked_protected_path_remains_outside_scan(hook_project):
    ignored = hook_project.root / "core" / "ignored.py"
    (hook_project.root / ".gitignore").write_text("core/ignored.py\n", encoding="utf-8")
    staged = _run_git(hook_project.root, hook_project.env, "add", "--", ".gitignore")
    assert staged.returncode == 0, staged.stderr
    ignored.write_bytes(b"ignored protected bytes\n")
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("pwd"))

    assert result.returncode == 0
    assert result.stdout == ""
    assert ignored.read_bytes() == b"ignored protected bytes\n"
    assert index.read_bytes() == index_before


@pytest.fixture
def bash_inspector(hook_project, monkeypatch):
    monkeypatch.syspath_prepend(str(hook_project.hooks_dir))
    spec = importlib.util.spec_from_file_location(
        "bash_inspector_under_test", hook_project.hooks_dir / "check_bash_writes.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_find_project_root", lambda: hook_project.root)
    monkeypatch.setattr(module, "HAS_LOGGER", False)
    # Real module-perimeter policy is covered through subprocesses above.
    monkeypatch.setattr(module, "HAS_FEATURE_LOCK", False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_bash_payload("ls"))))
    return module


def _git_blob_id(module, content):
    digest = module._blob_hasher("0" * 40, len(content))
    digest.update(content)
    return digest.hexdigest()


def _create_windows_junction(link_path, target_path):
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr or result.stdout}")
    assert link_path.exists()


def _remove_fixture_junction(link_path, target_path):
    # Remove only the checked fixture reparse point, never traverse its target.
    assert getattr(os.lstat(link_path), "st_reparse_tag", None) == 0xA0000003
    assert link_path.resolve() == target_path.resolve()
    os.rmdir(link_path)


@pytest.mark.parametrize("failed_enumeration", ["tracked-index", "untracked-index"])
@pytest.mark.parametrize("failure", ["missing", "timeout", "nonzero", "oserror", "partial"])
def test_f2_git_failure_reports_incomplete_and_preserves_other_observations(
    hook_project, bash_inspector, monkeypatch, capsys, failed_enumeration, failure,
):
    tracked = hook_project.root / "core" / "test.py"
    untracked = hook_project.root / "core" / "new.py"
    tracked.write_bytes(b"tracked user edit\n")
    untracked.write_bytes(b"untracked user edit\n")
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()
    real_run = subprocess.run
    calls = 0

    def git_result(args, **kwargs):
        nonlocal calls
        assert args[0:4] == ["git", "--no-optional-locks", "-c", "core.fsmonitor="]
        assert args[4] == "ls-files", "inspection attempted a mutating command"
        calls += 1
        enumeration = "tracked-index" if calls == 1 else "untracked-index"
        if enumeration == "tracked-index":
            assert "--stage" in args and "-v" in args and "-z" in args
        else:
            assert "--others" in args and "--exclude-standard" in args and "-z" in args
        if enumeration == failed_enumeration:
            if failure == "missing":
                raise FileNotFoundError("simulated missing Git")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 10)
            if failure == "oserror":
                raise PermissionError("simulated access failure")
            return subprocess.CompletedProcess(
                args, 128 if failure == "nonzero" else 0,
                b"core/untrusted-output.py\0partial", b"simulated error",
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", git_result)
    with pytest.raises(SystemExit) as exit_result:
        bash_inspector.main()
    assert exit_result.value.code == 0
    assert tracked.read_bytes() == b"tracked user edit\n"
    assert untracked.read_bytes() == b"untracked user edit\n"
    assert index.read_bytes() == index_before
    message = _observation_message(capsys.readouterr().out)
    assert "inspection incomplete" in message
    assert "core/untrusted-output.py" not in message
    assert (
        "core/new.py" if failed_enumeration == "tracked-index" else "core/test.py"
    ) in message
    detail = {
        "missing": "Git is unavailable", "timeout": "Git timed out",
        "nonzero": "status 128", "oserror": "Git could not start",
        "partial": "incomplete Git path output",
    }[failure]
    assert detail in message


@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_f2_concurrent_edit_after_enumeration_is_never_recovered(
    hook_project, bash_inspector, monkeypatch, capsys, tracked,
):
    target = hook_project.root / "core" / ("test.py" if tracked else "new.py")
    target.write_bytes(b"first user edit\n")
    enumerator_name = "_git_changed_files" if tracked else "_git_untracked_files"
    enumerate_paths = getattr(bash_inspector, enumerator_name)

    def edit_after_enumeration(project_root, *args):
        paths = enumerate_paths(project_root, *args)
        # Deterministic checkpoint: another actor replaces the bytes after Git
        # observes the dirty path and before policy reporting consumes it.
        target.write_bytes(b"concurrent actor edit\n")
        return paths

    def unexpected_unlink(*args, **kwargs):
        pytest.fail("the inspector attempted to delete a path")

    monkeypatch.setattr(bash_inspector, enumerator_name, edit_after_enumeration)
    monkeypatch.setattr(Path, "unlink", unexpected_unlink)
    index = hook_project.root / ".git" / "index"
    before = index.read_bytes()
    with pytest.raises(SystemExit) as exit_result:
        bash_inspector.main()
    assert exit_result.value.code == 0
    assert target.read_bytes() == b"concurrent actor edit\n"
    assert index.read_bytes() == before
    assert "core/" + target.name in _observation_message(capsys.readouterr().out)


def test_f2_concurrent_edit_during_tracked_read_reports_incomplete(
    hook_project, bash_inspector, monkeypatch, capsys, record_property,
):
    target = hook_project.root / "core" / "test.py"
    target.write_bytes(b"first user edit\n")
    real_read = bash_inspector.os.read
    changed = False
    identity = target.stat()
    observations = []

    def read_then_replace(descriptor, size):
        nonlocal changed
        held = os.fstat(descriptor)
        tracked = (held.st_dev, held.st_ino) == (identity.st_dev, identity.st_ino)
        chunk = real_read(descriptor, size)
        if chunk and tracked and not changed:
            observations.append({"descriptor": descriptor, "device": held.st_dev,
                                 "inode": held.st_ino, "bytesRead": len(chunk)})
            changed = True
            target.write_bytes(b"actor user edit\n")
        return chunk

    monkeypatch.setattr(bash_inspector.os, "read", read_then_replace)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    with pytest.raises(SystemExit) as exit_result:
        bash_inspector.main()

    assert exit_result.value.code == 0
    record_property("tracked_read_mutation", json.dumps(observations))
    assert changed and len(observations) == 1
    assert target.read_bytes() == b"actor user edit\n"
    assert index.read_bytes() == index_before
    message = _observation_message(capsys.readouterr().out)
    assert "inspection incomplete" in message
    assert "path changed during inspection" in message


def test_f2_missing_git_in_subprocess_is_not_reported_as_clean(hook_project):
    hook_project.env["PATH"] = str(hook_project.runtime_dir)
    target = hook_project.root / "core" / "test.py"
    target.write_bytes(b"user edit without Git\n")
    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("ls"))
    assert result.returncode == 0
    assert "Git is unavailable" in _observation_message(result.stdout)
    assert target.read_bytes() == b"user edit without Git\n"


def test_f2_unexpected_inspection_error_is_visible_and_preserves_work(hook_project):
    (hook_project.control_dir / "cc_config.json").write_text("[]", encoding="utf-8")
    target = hook_project.root / "core" / "test.py"
    target.write_bytes(b"edit before unexpected error\n")
    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("ls"))
    assert result.returncode == 0
    assert "inspection incomplete" in _observation_message(result.stdout)
    assert target.read_bytes() == b"edit before unexpected error\n"


def test_f2_git_paths_preserve_embedded_newlines_and_trailing_spaces(
    hook_project, bash_inspector, monkeypatch,
):
    # These valid POSIX names cannot all be created on Windows. Verify the raw
    # Git boundary independently of the host filesystem's filename restrictions.
    names = [
        "core/ leading.py", "core/line\nbreak.py", "core/tab\tname.py",
        "core/trailing ", "core/caf\u00e9.py",
    ]
    object_id = b"0" * 40
    stdout = b"\0".join(
        b"H 100644 " + object_id + b" 0\t" + name.encode("utf-8")
        for name in names
    ) + b"\0"
    monkeypatch.setattr(
        subprocess, "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout, b""),
    )
    assert [entry[4] for entry in bash_inspector._git_index_entries(hook_project.root)] == names


def test_f2_raw_comparison_discloses_git_normalization(hook_project):
    target = hook_project.root / "core" / "test.py"
    (hook_project.root / ".git" / "info" / "attributes").write_text(
        "core/test.py text eol=lf\n", encoding="utf-8",
    )
    expected = b"# normalized worktree bytes\r\n"
    target.write_bytes(expected)
    staged = _run_git(hook_project.root, hook_project.env, "add", "--", "core/test.py")
    assert staged.returncode == 0, staged.stderr
    git_clean = _run_git(
        hook_project.root, hook_project.env, "diff", "--quiet", "--", "core/test.py",
    )
    assert git_clean.returncode == 0, "fixture must be Git-clean after text normalization"
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("pwd"))

    assert result.returncode == 0
    assert target.read_bytes() == expected
    assert index.read_bytes() == index_before
    message = _observation_message(result.stdout)
    assert "core/test.py" in message
    assert "raw worktree/index mismatch" in message
    assert "normalization" in message


def test_f2_tracked_read_limit_is_reported_as_incomplete(
    hook_project, bash_inspector, monkeypatch, capsys,
):
    target = hook_project.root / "core" / "test.py"
    expected = b"protected bytes above the synthetic limit\n"
    target.write_bytes(expected)
    monkeypatch.setattr(bash_inspector, "_MAX_TRACKED_FILE_BYTES", 8)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    with pytest.raises(SystemExit) as exit_result:
        bash_inspector.main()

    assert exit_result.value.code == 0
    assert target.read_bytes() == expected
    assert index.read_bytes() == index_before
    message = _observation_message(capsys.readouterr().out)
    assert "inspection incomplete" in message
    assert "inspection byte limit exceeded" in message


def test_f2_concurrent_growth_never_exceeds_read_budgets(
    hook_project, bash_inspector, monkeypatch,
):
    target = hook_project.root / "core" / "test.py"
    initial = b"12345678"
    target.write_bytes(initial)
    entry = ("H", "100644", _git_blob_id(bash_inspector, initial), 0, "core/test.py")
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: [entry])
    monkeypatch.setattr(bash_inspector, "_MAX_TRACKED_FILE_BYTES", 8)
    monkeypatch.setattr(bash_inspector, "_MAX_TRACKED_TOTAL_BYTES", 8)
    monkeypatch.setattr(bash_inspector, "_READ_CHUNK_BYTES", 4)

    real_read = bash_inspector.os.read
    requested = []
    returned = 0
    appended = False

    def read_then_grow(descriptor, size):
        nonlocal returned, appended
        requested.append(size)
        chunk = real_read(descriptor, size)
        returned += len(chunk)
        if chunk and not appended:
            with target.open("ab", buffering=0) as stream:
                stream.write(b"x" * 64)
            appended = True
        return chunk

    monkeypatch.setattr(bash_inspector.os, "read", read_then_grow)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    changed, errors = bash_inspector._git_changed_files(
        hook_project.root, lambda _path: True,
    )

    assert changed == []
    assert appended
    assert sum(requested) <= 8
    assert returned <= 8
    assert target.read_bytes() == initial + b"x" * 64
    assert index.read_bytes() == index_before
    assert any("path changed during inspection" in error for error in errors)


def test_f2_failed_reads_remain_charged_to_shared_event_budget(
    hook_project, bash_inspector, monkeypatch,
):
    core = hook_project.root / "core"
    initial = b"base"
    targets = [core / name for name in ("a.py", "b.py", "c.py")]
    for target in targets:
        target.write_bytes(initial)
    entries = [
        ("H", "100644", _git_blob_id(bash_inspector, initial), 0,
         target.relative_to(hook_project.root).as_posix())
        for target in targets
    ]
    entries.insert(
        0,
        ("H", "100644", _git_blob_id(bash_inspector, initial), 0,
         "core/0-deleted.py"),
    )
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: entries)
    monkeypatch.setattr(bash_inspector, "_MAX_TRACKED_FILE_BYTES", 8)
    monkeypatch.setattr(bash_inspector, "_MAX_TRACKED_TOTAL_BYTES", 8)
    monkeypatch.setattr(bash_inspector, "_READ_CHUNK_BYTES", 4)

    real_read = bash_inspector.os.read
    by_inode = {target.stat().st_ino: target for target in targets}
    appended = set()
    read_inodes = []
    requested = []
    returned = 0

    def read_then_grow(descriptor, size):
        nonlocal returned
        inode = os.fstat(descriptor).st_ino
        read_inodes.append(inode)
        requested.append(size)
        chunk = real_read(descriptor, size)
        returned += len(chunk)
        if chunk and inode in by_inode and inode not in appended:
            with by_inode[inode].open("ab", buffering=0) as stream:
                stream.write(b"x" * 8)
            appended.add(inode)
        return chunk

    monkeypatch.setattr(bash_inspector.os, "read", read_then_grow)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    changed, errors = bash_inspector._git_changed_files(
        hook_project.root, lambda _path: True,
    )

    assert changed == ["core/0-deleted.py"]
    assert sum(requested) <= 8
    assert returned <= 8
    assert targets[2].stat().st_ino not in read_inodes
    assert len(appended) == 2
    assert len(errors) == 3
    assert any("inspection byte limit exceeded" in error for error in errors)
    assert index.read_bytes() == index_before
    assert targets[0].read_bytes() == initial + b"x" * 8
    assert targets[1].read_bytes() == initial + b"x" * 8
    assert targets[2].read_bytes() == initial


def test_f2_parent_junction_is_incomplete_without_outside_content_read(
    hook_project, bash_inspector, monkeypatch,
):
    relative = "core/linked/file.txt"
    link_parent = hook_project.root / "core" / "linked"
    inside = hook_project.root / relative
    inside.parent.mkdir()
    content = b"outside payload"
    inside.write_bytes(content)
    staged = _run_git(hook_project.root, hook_project.env, "add", "--", relative)
    assert staged.returncode == 0, staged.stderr
    entries = bash_inspector._git_index_entries(hook_project.root)
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: entries)
    inside.unlink()
    link_parent.rmdir()

    outside = hook_project.root.parent / "outside-junction"
    outside.mkdir()
    outside_file = outside / "file.txt"
    outside_file.write_bytes(content)
    _create_windows_junction(link_parent, outside)
    assert inside.resolve() == outside_file.resolve()

    real_read = bash_inspector.os.read
    outside_inode = outside_file.stat().st_ino
    outside_reads = 0

    def observed_read(descriptor, size):
        nonlocal outside_reads
        if os.fstat(descriptor).st_ino == outside_inode:
            outside_reads += 1
        return real_read(descriptor, size)

    monkeypatch.setattr(bash_inspector.os, "read", observed_read)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()
    try:
        changed, errors = bash_inspector._git_changed_files(
            hook_project.root, lambda path: path == relative,
        )
        outside_after = outside_file.read_bytes()
        index_after = index.read_bytes()
    finally:
        if link_parent.exists():
            _remove_fixture_junction(link_parent, outside)

    assert outside_reads == 0
    assert changed == []
    assert len(errors) == 1
    assert relative in errors[0]
    assert "outside project" in errors[0]
    assert outside_after == content
    assert index_after == index_before


def test_f2_parent_replacement_before_open_never_reads_redirected_content(
    hook_project, bash_inspector, monkeypatch,
):
    if os.name != "nt":
        pytest.skip("Windows junction race regression")

    relative = "core/swap/file.txt"
    parent = hook_project.root / "core" / "swap"
    target = hook_project.root / relative
    parent.mkdir()
    content = b"same inode through redirected parent"
    target.write_bytes(content)
    staged = _run_git(hook_project.root, hook_project.env, "add", "--", relative)
    assert staged.returncode == 0, staged.stderr
    entries = bash_inspector._git_index_entries(hook_project.root)
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: entries)

    outside = hook_project.root.parent / "outside-swap"
    outside.mkdir()
    outside_file = outside / "file.txt"
    os.link(target, outside_file)
    outside_inode = outside_file.stat().st_ino
    real_open = bash_inspector.os.open
    real_read = bash_inspector.os.read
    swapped = False
    outside_reads = 0

    def swap_then_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path) == target:
            target.unlink()
            parent.rmdir()
            _create_windows_junction(parent, outside)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    def observed_read(descriptor, size):
        nonlocal outside_reads
        if os.fstat(descriptor).st_ino == outside_inode:
            outside_reads += 1
        return real_read(descriptor, size)

    monkeypatch.setattr(bash_inspector.os, "open", swap_then_open)
    monkeypatch.setattr(bash_inspector.os, "read", observed_read)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()
    try:
        changed, errors = bash_inspector._git_changed_files(
            hook_project.root, lambda path: path == relative,
        )
        outside_after = outside_file.read_bytes()
        index_after = index.read_bytes()
    finally:
        if parent.exists():
            _remove_fixture_junction(parent, outside)

    assert swapped
    assert outside_reads == 0
    assert changed == []
    assert len(errors) == 1
    assert relative in errors[0]
    assert "outside project" in errors[0]
    assert outside_after == content
    assert index_after == index_before


@pytest.mark.parametrize("checkpoint", ["first-leaf", "between-entries"])
def test_f2_root_replacement_cannot_reanchor_inspection(
    hook_project, bash_inspector, monkeypatch, checkpoint,
):
    if os.name != "nt":
        pytest.skip("Windows root-sharing regression")
    root = hook_project.root
    outside = root.parent / "outside-root"
    retained = root.parent / "retained-root"
    (outside / "core").mkdir(parents=True)
    relatives = ["core/root-a.txt", "core/root-b.txt"]
    content = b"finite root identity counterexample"
    for relative in relatives:
        (root / relative).write_bytes(content)
        os.link(root / relative, outside / relative)
    staged = _run_git(root, hook_project.env, "add", "--", *relatives)
    assert staged.returncode == 0, staged.stderr
    index_before = (root / ".git" / "index").read_bytes()
    trigger = root / relatives[checkpoint == "between-entries"]
    real_open, real_read = os.open, os.read
    attempted = swapped = refused = False
    outside_reads = 0

    def swap_then_open(path, flags, *args, **kwargs):
        nonlocal attempted, swapped, refused
        if not attempted and Path(path) == trigger:
            attempted = True
            assert root.parent == retained.parent == outside.parent
            assert not getattr(os.lstat(root), "st_reparse_tag", 0)
            try:
                root.rename(retained)
            except PermissionError:
                refused = True
            else:
                _create_windows_junction(root, outside)
                swapped = True
        return real_open(path, flags, *args, **kwargs)

    def observe_read(descriptor, size):
        nonlocal outside_reads
        final_path = bash_inspector._windows_final_path(
            bash_inspector._windows_handle_api()[1],
            bash_inspector.msvcrt.get_osfhandle(descriptor),
        )
        if os.path.commonpath((str(outside), final_path)).lower() == str(outside).lower():
            outside_reads += 1
        return real_read(descriptor, size)

    monkeypatch.setattr(os, "open", swap_then_open)
    monkeypatch.setattr(os, "read", observe_read)
    try:
        changed, errors = bash_inspector._git_changed_files(
            root, lambda path: path in relatives,
        )
        original = retained if swapped else root
        assert (original / ".git" / "index").read_bytes() == index_before
        for relative in relatives:
            assert (original / relative).read_bytes() == content
            assert (outside / relative).read_bytes() == content
        assert attempted
        assert outside_reads == 0
        assert refused and not swapped
        # A clean result is valid only for the original unchanged root/index.
        assert changed == [] and errors == []
    finally:
        if swapped:
            _remove_fixture_junction(root, outside)


@pytest.mark.parametrize("failure", ["none", "acquisition", "index", "read"])
def test_f2_root_handles_are_released_on_every_exit(
    hook_project, bash_inspector, monkeypatch, failure,
):
    if os.name != "nt":
        pytest.skip("Windows handle ownership regression")
    root = hook_project.root
    native_attributes = bash_inspector._windows_directory_attributes
    acquired = []

    def attributes(handle):
        result = native_attributes(handle)
        acquired.append(handle)
        if failure == "acquisition" and len(acquired) == len(root.parents) + 1:
            raise OSError("injected root acquisition failure")
        return result

    def fail(*_args, **_kwargs):
        raise bash_inspector._InspectionError("injected inspection failure")

    monkeypatch.setattr(bash_inspector, "_windows_directory_attributes", attributes)
    if failure == "index":
        monkeypatch.setattr(bash_inspector, "_git_index_entries", fail)
    if failure == "read":
        monkeypatch.setattr(bash_inspector, "_read_regular_file", fail)
    if failure in ("acquisition", "index"):
        with pytest.raises(bash_inspector._InspectionError):
            bash_inspector._git_changed_files(root, lambda p: p == "core/test.py")
    else:
        changed, errors = bash_inspector._git_changed_files(
            root, lambda p: p == "core/test.py",
        )
        assert changed == []
        assert bool(errors) == (failure == "read")
    assert len(acquired) == len(root.parents) + 1
    for handle in acquired:
        with pytest.raises(OSError):
            native_attributes(handle)


def test_f2_root_junction_is_refused_before_index_or_content_read(
    hook_project, bash_inspector, monkeypatch,
):
    if os.name != "nt":
        pytest.skip("Windows root reparse-point regression")
    alias = hook_project.root.parent / "root-alias"
    _create_windows_junction(alias, hook_project.root)
    index_before = (hook_project.root / ".git/index").read_bytes()

    def unexpected(*_args, **_kwargs):
        pytest.fail("failed root acquisition reached index/content inspection")

    monkeypatch.setattr(bash_inspector, "_git_index_entries", unexpected)
    monkeypatch.setattr(os, "read", unexpected)
    try:
        with pytest.raises(bash_inspector._InspectionError, match="plain directory"):
            bash_inspector._git_changed_files(alias)
        assert (hook_project.root / ".git/index").read_bytes() == index_before
    finally:
        _remove_fixture_junction(alias, hook_project.root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX root descriptor regression")
@pytest.mark.parametrize("checkpoint", ["before-index", "after-index", "between-entries"])
def test_f2_posix_root_descriptor_retains_original_index_and_files(
    hook_project, bash_inspector, monkeypatch, checkpoint,
):
    root = hook_project.root
    outside = root.parent / "outside-posix-root"
    retained = root.parent / "retained-posix-root"
    (outside / "core").mkdir(parents=True)
    relatives = ["core/anchor-a", "core/anchor-b"]
    for relative in relatives:
        (root / relative).write_bytes(b"original baseline")
        (outside / relative).write_bytes(b"outside baseline")
    assert _run_git(root, hook_project.env, "add", "--", *relatives).returncode == 0
    assert _run_git(outside, hook_project.env, "init", "--quiet").returncode == 0
    assert _run_git(outside, hook_project.env, "add", "--", *relatives).returncode == 0
    (root / relatives[1]).write_bytes(b"original user edit")
    index_before = (root / ".git/index").read_bytes()
    outside_index = (outside / ".git/index").read_bytes()
    original_listing = bash_inspector._git_index_entries
    original_entry = bash_inspector._worktree_entry_matches_index
    original_read = os.read
    swapped = False
    seen = 0
    outside_reads = 0
    outside_inodes = {(outside / p).stat().st_ino for p in relatives}

    def swap():
        nonlocal swapped
        assert not swapped and root.parent == retained.parent == outside.parent
        root.rename(retained)
        root.symlink_to(outside, target_is_directory=True)
        swapped = True

    def listing(boundary):
        if checkpoint == "before-index":
            swap()
        entries = original_listing(boundary)
        if checkpoint == "after-index":
            swap()
        return entries

    def entry(*args):
        nonlocal seen
        seen += 1
        if checkpoint == "between-entries" and seen == 2:
            swap()
        return original_entry(*args)

    def read(descriptor, size):
        nonlocal outside_reads
        if os.fstat(descriptor).st_ino in outside_inodes:
            outside_reads += 1
        return original_read(descriptor, size)

    monkeypatch.setattr(bash_inspector, "_git_index_entries", listing)
    monkeypatch.setattr(bash_inspector, "_worktree_entry_matches_index", entry)
    monkeypatch.setattr(os, "read", read)
    try:
        changed, errors = bash_inspector._git_changed_files(root, lambda p: p in relatives)
        assert swapped and seen == 2
        assert outside_reads == 0
        assert changed == [relatives[1]] and errors == []
        assert (retained / relatives[0]).read_bytes() == b"original baseline"
        assert (retained / relatives[1]).read_bytes() == b"original user edit"
        assert (retained / ".git/index").read_bytes() == index_before
        assert (outside / ".git/index").read_bytes() == outside_index
        for relative in relatives:
            assert (outside / relative).read_bytes() == b"outside baseline"
    finally:
        if swapped:
            assert root.is_symlink() and root.resolve() == outside.resolve()
            root.unlink()


def test_f2_native_capability_gate_refuses_an_unsupported_open_wrapper(
    hook_project, bash_inspector, monkeypatch,
):
    def unsupported_open(*_args, **_kwargs):
        pytest.fail("unsupported open wrapper was invoked")

    monkeypatch.setattr(os, "open", unsupported_open)
    assert os.open not in os.supports_dir_fd
    with pytest.raises(bash_inspector._InspectionError, match="unavailable"):
        bash_inspector._InspectionRoot(hook_project.root)._open_posix()


def test_f2_tracked_symlink_is_incomplete_without_reading_target(
    hook_project, bash_inspector, monkeypatch,
):
    relative = "core/tracked-link"
    target = hook_project.root / relative
    target.write_bytes(b"placeholder")
    entry = ("H", "120000", "0" * 40, 0, relative)
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: [entry])

    if os.name == "nt":
        real_lstat = bash_inspector.os.lstat

        class LinkState:
            st_mode = bash_inspector.stat.S_IFLNK

        monkeypatch.setattr(
            bash_inspector.os,
            "lstat",
            lambda path: LinkState() if Path(path) == target else real_lstat(path),
        )
    else:
        target.unlink()
        target.symlink_to("elsewhere")

    monkeypatch.setattr(
        bash_inspector.os,
        "readlink",
        lambda *_args, **_kwargs: pytest.fail("tracked symlink target was read"),
    )
    changed, errors = bash_inspector._git_changed_files(
        hook_project.root, lambda path: path == relative,
    )

    assert changed == []
    assert len(errors) == 1
    assert relative in errors[0]
    assert "symlink inspection is unavailable" in errors[0]


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO/open regression")
def test_f2_leaf_fifo_replacement_is_nonblocking_in_bounded_child(
    hook_project, bash_inspector, monkeypatch,
):
    if not hasattr(os, "mkfifo") or "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("FIFO or fork process is unavailable")
    native_open = os.open
    if native_open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        pytest.skip("native directory-relative open/stat is unavailable")

    relative = "core/fifo-target"
    target = hook_project.root / relative
    content = b"regular before replacement"
    target.write_bytes(content)
    entry = ("H", "100644", _git_blob_id(bash_inspector, content), 0, relative)
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: [entry])
    real_open = native_open
    real_read = os.read
    replaced = False
    leaf_open_reached = False
    fifo_inode = None
    fifo_reads = 0

    def replace_then_open(path, flags, *args, **kwargs):
        nonlocal replaced, leaf_open_reached, fifo_inode
        path_text = os.fspath(path)
        is_target = Path(path_text) == target if os.path.isabs(path_text) else (
            path_text == target.name and kwargs.get("dir_fd") is not None
        )
        if not replaced and is_target:
            leaf_open_reached = bool(
                kwargs.get("dir_fd") is not None
                and flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
            )
            os.unlink(target)
            os.mkfifo(target)
            fifo_inode = target.stat().st_ino
            replaced = True
        return real_open(path, flags, *args, **kwargs)

    def observe_read(descriptor, size):
        nonlocal fifo_reads
        state = os.fstat(descriptor)
        if state.st_ino == fifo_inode and bash_inspector.stat.S_ISFIFO(state.st_mode):
            fifo_reads += 1
        return real_read(descriptor, size)

    monkeypatch.setattr(bash_inspector.os, "open", replace_then_open)
    # Preserve the native capability guard. Only a genuinely supported native
    # function may lend its capability to this forwarding test wrapper.
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {replace_then_open})
    monkeypatch.setattr(os, "read", observe_read)
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue()

    def inspect_in_child():
        try:
            result = bash_inspector._git_changed_files(
                hook_project.root, lambda path: path == relative,
            )
            result_queue.put(("result", {
                "inspection": result, "replaced": replaced,
                "leaf_open_reached": leaf_open_reached, "fifo_reads": fifo_reads,
            }))
        except BaseException as exc:  # pragma: no cover - diagnostic transport
            result_queue.put(("error", f"{type(exc).__name__}: {exc}"))

    process = context.Process(target=inspect_in_child)
    try:
        process.start()
        kind, payload = result_queue.get(timeout=5)
        process.join(2)
        assert not process.is_alive(), "FIFO inspection child did not exit"
        assert process.exitcode == 0
        assert kind == "result", payload
        assert payload["replaced"] and payload["leaf_open_reached"]
        assert payload["fifo_reads"] == 0
        changed, errors = payload["inspection"]
        assert changed == [relative]
        assert errors == []
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            assert not process.is_alive(), "test child could not be reaped"
        process.close()
        result_queue.close()
        result_queue.join_thread()


def test_f2_unmerged_protected_path_is_observed_without_blob_read(
    hook_project, bash_inspector, monkeypatch, capsys,
):
    target = hook_project.root / "core" / "test.py"
    expected = b"unmerged worktree bytes\n"
    target.write_bytes(expected)
    entries = [
        ("M", "100644", digit * 40, stage, "core/test.py")
        for digit, stage in (("1", 1), ("2", 2), ("3", 3))
    ]
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: entries)
    monkeypatch.setattr(bash_inspector, "_git_untracked_files", lambda _root: ([], []))
    monkeypatch.setattr(
        bash_inspector, "_worktree_entry_matches_index",
        lambda *_args, **_kwargs: pytest.fail("unmerged path attempted a blob read"),
    )
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    with pytest.raises(SystemExit) as exit_result:
        bash_inspector.main()

    assert exit_result.value.code == 0
    assert target.read_bytes() == expected
    assert index.read_bytes() == index_before
    assert "core/test.py" in _observation_message(capsys.readouterr().out)


def test_f2_protected_submodule_entry_is_reported_as_incomplete(
    hook_project, bash_inspector, monkeypatch,
):
    entries = [("H", "160000", "1" * 40, 0, "core/test.py")]
    monkeypatch.setattr(bash_inspector, "_git_index_entries", lambda _root: entries)
    changed, errors = bash_inspector._git_changed_files(
        hook_project.root, lambda _path: True,
    )
    assert changed == []
    assert len(errors) == 1
    assert "protected submodule content is not inspected" in errors[0]


def _configure_side_effect_filter(project, driver_kind):
    """Configure a real clean/process filter after every fixture index write."""
    runtime_script = project.runtime_dir / "side_effect_filter.py"
    marker = project.runtime_dir / "filter-invoked.txt"
    runtime_script.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                "root = Path(__file__).resolve().parents[1]",
                "(root / '.runtime' / 'filter-invoked.txt').write_text('ran', encoding='utf-8')",
                "(root / 'outside-tracked.txt').write_bytes(b'FILTER OVERWROTE TRACKED\\n')",
                "(root / 'outside-untracked.txt').write_bytes(b'FILTER OVERWROTE UNTRACKED\\n')",
                "if sys.argv[1] == 'clean':",
                "    sys.stdout.buffer.write(sys.stdin.buffer.read())",
                "else:",
                "    raise SystemExit(1)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    info_dir = project.root / ".git" / "info"
    (info_dir / "exclude").write_text(".runtime/\n", encoding="utf-8")
    (info_dir / "attributes").write_text(
        "core/test.py filter=ccsideeffect\n", encoding="utf-8",
    )
    command = (
        f'"{Path(sys.executable).as_posix()}" '
        f'"{runtime_script.as_posix()}" {driver_kind}'
    )
    configured = _run_git(
        project.root, project.env, "config",
        f"filter.ccsideeffect.{driver_kind}", command,
    )
    assert configured.returncode == 0, configured.stderr
    required = _run_git(
        project.root, project.env, "config", "filter.ccsideeffect.required", "true",
    )
    assert required.returncode == 0, required.stderr
    return marker


def _configure_side_effect_fsmonitor(project):
    """Configure and prove a real fsmonitor callback before the hook run."""
    callback = project.root / ".git" / "hooks" / "fsmonitor-watchman"
    marker = project.runtime_dir / "fsmonitor-invoked.txt"
    callback.write_text(
        "\n".join(
            [
                f"#!{Path(sys.executable).as_posix()}",
                "import sys",
                "from pathlib import Path",
                "root = Path.cwd()",
                "(root / '.runtime' / 'fsmonitor-invoked.txt').write_text('ran', encoding='utf-8')",
                "for name in ('outside-tracked.txt', 'outside-untracked.txt'):",
                "    path = root / name",
                "    if path.exists():",
                "        path.write_bytes(b'FSMONITOR OVERWROTE USER BYTES\\n')",
                "sys.stdout.buffer.write(b'cc-token\\0core/test.py\\0')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    callback.chmod(0o755)
    configured = _run_git(
        project.root, project.env, "config", "core.fsmonitor", ".git/hooks/fsmonitor-watchman",
    )
    assert configured.returncode == 0, configured.stderr
    versioned = _run_git(
        project.root, project.env, "config", "core.fsmonitorHookVersion", "2",
    )
    assert versioned.returncode == 0, versioned.stderr
    armed = _run_git(project.root, project.env, "status", "--porcelain")
    assert armed.returncode == 0, armed.stderr
    assert marker.exists(), "control command did not invoke the configured fsmonitor"
    marker.unlink()
    return marker


@pytest.mark.parametrize("protection", ["deny", "module"])
@pytest.mark.parametrize("driver_kind", ["clean", "process"])
def test_f2_configured_content_filters_are_not_invoked(
    hook_project, protection, driver_kind,
):
    if protection == "module":
        _use_module_perimeter(hook_project)

    tracked = hook_project.root / "outside-tracked.txt"
    tracked.write_bytes(b"tracked baseline\n")
    added = _run_git(hook_project.root, hook_project.env, "add", "--", tracked.name)
    assert added.returncode == 0, added.stderr
    committed = _run_git(
        hook_project.root, hook_project.env,
        "-c", "user.name=ControlCoding Tests",
        "-c", "user.email=tests@controlcoding.invalid",
        "commit", "--quiet", "-m", "temporary filter test baseline",
    )
    assert committed.returncode == 0, committed.stderr

    protected = hook_project.root / "core" / "test.py"
    protected.write_bytes(b"staged protected bytes\n")
    staged = _run_git(hook_project.root, hook_project.env, "add", "--", "core/test.py")
    assert staged.returncode == 0, staged.stderr

    expected = {
        protected: b"protected user bytes\n",
        tracked: b"unrelated tracked user bytes\n",
        hook_project.root / "outside-untracked.txt": b"unrelated untracked user bytes\n",
    }
    for path, content in expected.items():
        path.write_bytes(content)

    marker = _configure_side_effect_filter(hook_project, driver_kind)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("type README.md"))

    assert result.returncode == 0
    assert not marker.exists(), f"Git invoked the configured {driver_kind} filter"
    for path, content in expected.items():
        assert path.exists()
        assert path.read_bytes() == content
    assert index.read_bytes() == index_before
    message = _observation_message(result.stdout)
    assert "core/test.py" in message
    assert ("DENY zone" if protection == "deny" else "module perimeter") in message


def test_f2_git_listing_does_not_invoke_configured_fsmonitor(hook_project):
    tracked = hook_project.root / "outside-tracked.txt"
    tracked.write_bytes(b"tracked baseline\n")
    added = _run_git(hook_project.root, hook_project.env, "add", "--", tracked.name)
    assert added.returncode == 0, added.stderr
    committed = _run_git(
        hook_project.root, hook_project.env,
        "-c", "user.name=ControlCoding Tests",
        "-c", "user.email=tests@controlcoding.invalid",
        "commit", "--quiet", "-m", "temporary fsmonitor test baseline",
    )
    assert committed.returncode == 0, committed.stderr
    untracked = hook_project.root / "outside-untracked.txt"
    untracked.write_bytes(b"initial bytes\n")

    marker = _configure_side_effect_fsmonitor(hook_project)
    expected = {
        tracked: b"unrelated tracked user bytes\n",
        untracked: b"unrelated untracked user bytes\n",
    }
    for path, content in expected.items():
        path.write_bytes(content)
    index = hook_project.root / ".git" / "index"
    index_before = index.read_bytes()

    result = hook_project.run_hook("check_bash_writes.py", _bash_payload("type README.md"))

    assert result.returncode == 0
    assert result.stdout == ""
    assert not marker.exists(), "Git invoked the configured fsmonitor"
    for path, content in expected.items():
        assert path.read_bytes() == content
    assert index.read_bytes() == index_before


def test_t9_lift_status_is_empty_initially(hook_project):
    result = hook_project.run_lift("--status")
    assert result.returncode == 0
    assert "No lift request found" in result.stdout


def test_t10_lift_request_creates_pending_file(hook_project):
    result = _create_pending_lift(hook_project)
    assert "PENDING" in result.stdout
    assert hook_project.lift_path.exists()
    assert hook_project.lift_path.resolve().is_relative_to(
        hook_project.root.resolve()
    )


def test_t11_lift_status_remains_pending(hook_project):
    _create_pending_lift(hook_project)
    lift_data = json.loads(hook_project.lift_path.read_text(encoding="utf-8"))
    status_result = hook_project.run_lift("--status")
    assert lift_data["status"] == "PENDING"
    assert "Status:  PENDING" in status_result.stdout


def test_t12_pending_lift_does_not_bypass_boundary(hook_project):
    _create_pending_lift(hook_project)
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("core/test.py"),
    )
    assert result.returncode == 2
    assert "DENY-protected" in result.stdout


def test_t13_noninteractive_approval_is_blocked(hook_project):
    _create_pending_lift(hook_project)
    result = hook_project.run_lift("--approve")
    assert result.returncode == 2
    assert "interactive human terminal" in result.stdout


def test_t14_pending_request_has_no_expiry(hook_project):
    _create_pending_lift(hook_project)
    lift_data = json.loads(hook_project.lift_path.read_text(encoding="utf-8"))
    assert lift_data["status"] == "PENDING"
    assert not lift_data.get("expires_at")


def test_t15_repeated_noninteractive_approval_stays_blocked(hook_project):
    _create_pending_lift(hook_project)
    first_result = hook_project.run_lift("--approve")
    second_result = hook_project.run_lift("--approve")
    lift_data = json.loads(hook_project.lift_path.read_text(encoding="utf-8"))
    assert first_result.returncode == 2
    assert second_result.returncode == 2
    assert lift_data["status"] == "PENDING"


def test_t16_clear_lift_succeeds(hook_project):
    _create_pending_lift(hook_project)
    result = hook_project.run_lift("--clear")
    assert result.returncode == 0
    assert "cleared" in result.stdout.lower()


def test_t17_clear_removes_lift_file(hook_project):
    _create_pending_lift(hook_project)
    hook_project.run_lift("--clear")
    assert not hook_project.lift_path.exists()
