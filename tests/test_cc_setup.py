"""Focused tests for cc_setup helpers."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cc  # noqa: E402
import cc_setup  # noqa: E402


def _f3_broken_sqlite(monkeypatch, failure):
    """Fail only the private capability probe; never open project storage."""
    class BrokenProbe:
        def __init__(self):
            if failure != "missing":
                self.deserialize = None if failure == "noncallable" else self.fail

        def fail(self, image):
            raise sqlite3.NotSupportedError("probe deliberately unavailable")

        def execute(self, *args):
            return self

        def close(self):
            pass

    def connect(database, *args, **kwargs):
        assert database == ":memory:", "preflight opened persistent storage"
        return BrokenProbe()

    monkeypatch.setattr(sqlite3, "connect", connect)


@pytest.mark.parametrize("failure", ["missing", "noncallable", "operation"])
@pytest.mark.parametrize("existing", [False, True])
def test_f3_setup_capability_failure_precedes_writes(tmp_path, monkeypatch, capsys, failure, existing):
    project = tmp_path / "target"
    project.mkdir()
    if existing:
        (project / "CONTROLCODING.md").write_bytes(b"custom context\n")
        (project / ".controlcoding").mkdir()
        (project / ".controlcoding" / "cc_config.json").write_bytes(b"{}\n")
    answers = _write_base_setup_answers(tmp_path, memory_default_policy="governed_scope")
    before = {str(p.relative_to(project)): p.read_bytes() if p.is_file() else None
              for p in project.rglob("*")}
    _f3_broken_sqlite(monkeypatch, failure)

    def reached_writer(*args):
        pytest.fail("setup reached its first write boundary before runtime rejection")

    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", reached_writer)
    assert cc_setup.cmd_setup(project, answers_file=answers) == 1
    assert "deserialize" in capsys.readouterr().out
    assert before == {str(p.relative_to(project)): p.read_bytes() if p.is_file() else None
                      for p in project.rglob("*")}


@pytest.mark.parametrize("failure", ["missing", "noncallable", "operation"])
@pytest.mark.parametrize("mode", ["full", "document-only"])
def test_f3_direct_memory_init_rejects_before_writer(tmp_path, monkeypatch, capsys, failure, mode):
    from cc_memory_lib import commands
    _f3_broken_sqlite(monkeypatch, failure)

    def reached_writer(*args, **kwargs):
        pytest.fail("memory init entered the writer before runtime rejection")

    monkeypatch.setattr(commands, "_memory_connection", reached_writer)
    assert commands.cmd_memory_init(tmp_path, mode=mode, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "deserialize" in payload["message"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", ["memory_init_failed", "governed_scan_failed"])
def test_f3_setup_memory_failure_is_not_success(tmp_path, monkeypatch, capsys, status):
    answers = _write_base_setup_answers(tmp_path, memory_default_policy="governed_scope")
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(cc_setup, "_sync_host_context_file", lambda *args, **kwargs: "AGENTS.md")
    monkeypatch.setattr(cc_setup, "_bootstrap_default_project_memory", lambda *args: {"status": status})
    assert cc_setup.cmd_setup(tmp_path, answers_file=answers) == 1
    out = capsys.readouterr().out
    assert status in out
    assert "partial" in out.lower()
    assert "Base setup complete!" not in out


@pytest.mark.parametrize("stage", ["init", "scan"])
def test_f3_partial_setup_keeps_accurate_failure_receipt(tmp_path, monkeypatch, capsys, stage):
    answers = _write_base_setup_answers(tmp_path, memory_default_policy="governed_scope")
    bootstrap = cc_setup._bootstrap_default_project_memory
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(cc_setup, "_bootstrap_default_project_memory", bootstrap)
    monkeypatch.setattr(cc_setup, "_sync_host_context_file", lambda *args, **kwargs: "AGENTS.md")
    if stage == "init":
        monkeypatch.setattr(cc_setup, "cmd_memory_init", lambda *args, **kwargs: 1)
    else:
        monkeypatch.setattr(cc_setup, "cmd_memory_scan", lambda *args, **kwargs: 1)
    assert cc_setup.cmd_setup(tmp_path, answers_file=answers) == 1
    receipt = json.loads((tmp_path / ".controlcoding/memory_bootstrap_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == ("memory_init_failed" if stage == "init" else "governed_scan_failed")
    assert receipt["initialized"] == (stage == "scan")
    assert receipt["scanned"] is False
    assert "Base setup complete!" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["missing", "noncallable", "operation"])
def test_f3_existing_memory_runtime_rejection_preserves_files(tmp_path, monkeypatch, capsys, failure):
    assert cc.cmd_memory_init(tmp_path) == 0
    capsys.readouterr()
    def snapshot():
        return {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
                for p in tmp_path.rglob("*")}
    before = snapshot()
    _f3_broken_sqlite(monkeypatch, failure)
    assert cc.cmd_memory_init(tmp_path, json_output=True) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "memory_runtime_unavailable"
    assert snapshot() == before


def test_f3_bootstrap_exception_reports_partial_setup(tmp_path, monkeypatch, capsys):
    answers = _write_base_setup_answers(tmp_path)
    _patch_base_setup_runtime(monkeypatch)
    def fail(*args):
        raise OSError("synthetic bootstrap failure")
    monkeypatch.setattr(cc_setup, "_bootstrap_default_project_memory", fail)
    assert cc_setup.cmd_setup(tmp_path, answers_file=answers) == 1
    out = capsys.readouterr().out
    assert "Partial setup" in out and "synthetic bootstrap failure" in out
    assert "Base setup complete!" not in out


def _f1_document_block(marker, language):
    guide = Path(__file__).resolve().parents[1] / "docs" / "install-controlcoding-on-your-project.md"
    match = re.search(r"<!-- " + marker + r" -->\s+```" + language + r"\n(.*?)```",
                      guide.read_text(encoding="utf-8"), re.S)
    assert match, f"missing published example: {marker}"
    return match.group(1)


def _f1_cli_env():
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", GIT_CONFIG_COUNT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_OPTIONAL_LOCKS="0")
    env.pop("PYTHONPATH", None)
    return env


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.parametrize("policy", ["governed_scope", "deferred"])
@pytest.mark.parametrize("explicit_apply", [True, False])
def test_f1_published_installation_flow(tmp_path, shell, policy, explicit_apply):
    """Execute the actual published script and handoff without installer mocks."""
    if shell == "powershell":
        executable = shutil.which("powershell") or shutil.which("pwsh")
    elif os.name == "nt":
        executable = str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/bash.exe")
        if not Path(executable).is_file():
            executable = None
    else:
        executable = shutil.which("bash")
    if not executable:
        pytest.skip(f"{shell} is not installed")
    project = tmp_path / "example project-caf\u00e9"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src/app.py").write_bytes(b"# application must stay unchanged\n")
    handoff = tmp_path / "reviewed handoff-caf\u00e9.json"
    payload = json.loads(_f1_document_block("cc-install-handoff", "json"))
    payload["setup"]["memory_default_policy"] = policy
    handoff.write_text(json.dumps(payload), encoding="utf-8")
    repo = Path(__file__).resolve().parents[1]
    body = _f1_document_block("cc-install-" + shell, shell)
    values = [sys.executable, str(repo / "scripts/cc.py"), str(project), str(handoff)]
    placeholders = (["C:/path/to/python.exe", "C:/path/to/ControlCoding/scripts/cc.py",
                     "C:/path/to/your-project", "C:/path/to/handoff.json"]
                    if shell == "powershell" else
                    ["/path/to/python", "/path/to/ControlCoding/scripts/cc.py",
                     "/path/to/your-project", "/path/to/handoff.json"])
    for placeholder, value in zip(placeholders, values):
        value = Path(value).as_posix()
        quoted = "'" + (value.replace("'", "''") if shell == "powershell"
                        else value.replace("'", "'\"'\"'")) + "'"
        body = body.replace("'" + placeholder + "'", quoted)
    if not explicit_apply:
        body = body.replace(" --apply-answers", "")
    script = tmp_path / ("run-example.ps1" if shell == "powershell" else "run-example.sh")
    script.write_text(body, encoding="utf-8-sig" if shell == "powershell" else "utf-8")
    command = ([executable, "-NoProfile", "-NonInteractive", "-File", str(script)]
               if shell == "powershell" else [executable, "--noprofile", "--norc", str(script)])
    result = subprocess.run(command, cwd=tmp_path, env=_f1_cli_env(), capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "src/app.py").read_bytes() == b"# application must stay unchanged\n"
    config = json.loads((project / ".controlcoding/cc_config.json").read_text(encoding="utf-8"))
    gateway = json.loads((project / ".controlcoding/gateway_config.json").read_text(encoding="utf-8"))
    engagement = json.loads((project / ".controlcoding/cc_engagement.json").read_text(encoding="utf-8"))
    receipt = json.loads((project / ".controlcoding/memory_bootstrap_receipt.json").read_text(encoding="utf-8"))
    assert config["memory_default_policy"] == policy
    assert gateway["userHost"] == "codex_cli"
    assert engagement["tier"] == "core" and engagement["backend_policy"] == "local_only"
    assert "controlcoding-managed:" in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert receipt["fullRepoScan"] is False
    assert receipt["status"] == ("completed" if policy == "governed_scope" else "deferred")
    database = project / ".controlcoding/memory/memory.db"
    assert database.exists() == (policy == "governed_scope")
    if policy == "governed_scope":
        from cc_memory_lib.store import _connect_readonly_db
        conn = _connect_readonly_db(database)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        finally:
            conn.close()


def test_f1_generated_guide_command_preserves_arguments(tmp_path, monkeypatch):
    source = tmp_path / "source with spaces-caf\u00e9's"
    source.mkdir()
    (source / "cc.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
    target = tmp_path / "target with spaces-caf\u00e9's"
    monkeypatch.setattr(cc_setup, "SCRIPT_DIR", source)
    command = cc_setup._setup_guide_command(target, "setup", "--answers-file", "./reviewed handoff.json")
    shell = (["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
             if os.name == "nt" else ["/bin/sh", "-c", command])
    result = subprocess.run(shell, env=_f1_cli_env(), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["setup", "--answers-file", "./reviewed handoff.json",
                                       "--project-root", str(target.resolve())]


def _write_base_setup_answers(project: Path, **overrides) -> Path:
    setup = {
        "name": "Neutral Setup",
        "documentation_mode": "managed",
        "user_host": "codex_cli",
        "host_instruction_mode": "recommended",
        "hooks_location": "local",
    }
    setup.update(overrides)
    answers_file = project / "setup_answers.json"
    answers_file.write_text(json.dumps({"setup": setup}), encoding="utf-8")
    return answers_file


def _write_project_setup_answers(project: Path) -> Path:
    answers_file = project / "project_answers.json"
    answers_file.write_text(
        json.dumps({
            "project_setup": {
                "project_definition_mode": "existing_brief",
                "kickoff_mode": "partial_spec",
                "kickoff": {
                    "vision": "A verified vertical slice.",
                    "users": "Primary project users.",
                    "must_haves": "One complete workflow",
                    "invariants": "No fatal startup failure",
                    "quality": "Stable and readable",
                    "anti_goals": "No unrelated expansion",
                    "references": "README.md",
                },
                "stack": "Python",
                "arch": "Modular",
                "truth": "Project state",
                "view": "Generated views",
            }
        }),
        encoding="utf-8",
    )
    return answers_file


def _owned_codex_adapter(source: str = "CONTROLCODING.md") -> str:
    marker = {
        "owner": "ControlCoding",
        "schema": "controlcoding.host-adapter-ownership",
        "version": 1,
        "target": "AGENTS.md",
        "host": "codex_cli",
        "source": source,
        "format": "host-context-section-export-v1",
    }
    return (
        "# AGENTS.md\n\n<!-- controlcoding-managed: "
        + json.dumps(marker, sort_keys=True, separators=(",", ":"))
        + " -->\n\nManaged adapter fixture.\n"
    )


def _patch_base_setup_runtime(monkeypatch) -> None:
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", lambda *args: True)

    def generate_context(project, answers):
        (project / "CONTROLCODING.md").write_text("# Test\n", encoding="utf-8")
        return True

    monkeypatch.setattr(cc_setup, "_generate_context_source", generate_context)
    monkeypatch.setattr(
        cc_setup,
        "cmd_init",
        lambda project, central_hooks=False, quiet=False: 0,
    )
    monkeypatch.setattr(
        cc_setup,
        "_write_host_integration_assets",
        lambda project, user_host, host_instructions=None: [],
    )
    monkeypatch.setattr(
        cc_setup,
        "_bootstrap_default_project_memory",
        lambda *args: {"status": "deferred"},
    )
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)


@pytest.mark.parametrize("real_pack_conflict", [False, True, "settings"])
def test_cmd_setup_propagates_pack_failure_before_dependent_steps(
    tmp_path,
    monkeypatch,
    capsys,
    real_pack_conflict,
):
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "Pack Failure",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "configure_advanced_packs": True,
                    "selected_packs": ["session-manager"],
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(cc_setup, "_ask", lambda prompt, default="": default)
    def answer_with_confirmed_default(prompt, default=True, apply_answers=False):
        assert "Continue with advanced packs" not in prompt
        return default

    monkeypatch.setattr(cc_setup, "_ask_yn_or_default", answer_with_confirmed_default)
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {})
    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", lambda *args: True)

    def generate_context(project, answers):
        (project / "CONTROLCODING.md").write_text("# Test\n", encoding="utf-8")
        return True

    monkeypatch.setattr(cc_setup, "_generate_context_source", generate_context)
    monkeypatch.setattr(
        cc_setup,
        "cmd_init",
        lambda project, central_hooks=False, quiet=False: 0,
    )
    monkeypatch.setattr(
        cc_setup,
        "_write_host_integration_assets",
        lambda project, user_host, host_instructions=None: [],
    )
    monkeypatch.setattr(
        cc_setup,
        "_bootstrap_default_project_memory",
        lambda *args: {"status": "deferred"},
    )

    def fail_install(project, pack):
        calls.append(("install", pack))
        return 7

    def unexpected_doctor(project):
        calls.append(("doctor", None))
        return 0

    if real_pack_conflict:
        if real_pack_conflict == "settings":
            target = tmp_path / ".controlcoding" / "settings.json"
            target.parent.mkdir()
            target.write_bytes(b'{"custom":true,"mcpServers":{"foreign":{"command":"owner"}}}\n')
        else:
            target = tmp_path / "tools" / "cc_lockfile.py"
            target.parent.mkdir()
            target.write_bytes(b"CUSTOM SETUP PACK\n")
        original = target.read_bytes()
        at_pack = []
        def install_with_conflict(project, pack):
            calls.append(("install", pack))
            at_pack.append(target.read_bytes())
            return cc.cmd_install(project, pack)
        monkeypatch.setattr(cc_setup, "cmd_install", install_with_conflict)
    else:
        monkeypatch.setattr(cc_setup, "cmd_install", fail_install)
    monkeypatch.setattr(cc_setup, "cmd_doctor", unexpected_doctor)

    result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file)

    assert result == (1 if real_pack_conflict else 7)
    assert calls == [("install", "session-manager")]
    if real_pack_conflict:
        assert at_pack == [original]
        assert target.read_bytes() == original
        if real_pack_conflict == "settings":
            assert not (tmp_path / "tools").exists()
        assert (tmp_path / "CONTROLCODING.md").is_file()
    output = capsys.readouterr().out
    assert "Base setup complete!" not in output
    assert "Partial setup: pack 'session-manager' did not complete" in output
    assert "no rollback was performed" in output
    if real_pack_conflict == "settings":
        assert "settings conflict" in output and "reconcile settings" in output


def test_backend_preference_has_neutral_default_and_accepts_undetected_supported_backend(capsys):
    assert cc_setup._ask_backend_preference(
        ["claude"],
        "",
        apply_answers=True,
    ) is None
    assert cc_setup._ask_backend_preference(
        [],
        "openai",
        apply_answers=True,
    ) == "openai"

    out = capsys.readouterr().out
    assert "claude (detected)" in out
    assert "Configure later" in out


def test_cmd_setup_apply_answers_installs_exact_confirmed_selected_packs(tmp_path, monkeypatch):
    answers_file = _write_base_setup_answers(
        tmp_path,
        configure_advanced_packs=True,
        selected_packs=["session-manager", "debug-tools"],
    )
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {})
    installed = []

    def record_install(project, pack):
        installed.append(pack)
        return 0

    monkeypatch.setattr(cc_setup, "cmd_install", record_install)

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0
    assert installed == ["session-manager", "debug-tools"]


def test_cmd_setup_apply_answers_respects_explicit_empty_selected_packs(tmp_path, monkeypatch):
    answers_file = _write_base_setup_answers(
        tmp_path,
        configure_advanced_packs=True,
        selected_packs=[],
    )
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {"claude": "found"})
    installed = []
    monkeypatch.setattr(
        cc_setup,
        "cmd_install",
        lambda project, pack: installed.append(pack) or 0,
    )

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0
    assert installed == []


def test_cmd_setup_persists_explicit_supported_backend_when_not_detected(tmp_path, monkeypatch):
    answers_file = _write_base_setup_answers(
        tmp_path,
        configure_advanced_packs=True,
        selected_packs=["debug-tools"],
        backend_pref=" OpenAI ",
    )
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {})

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0

    settings = json.loads(
        (tmp_path / ".controlcoding" / "settings.json").read_text(encoding="utf-8")
    )
    env = settings["mcpServers"]["debug-consultant"]["env"]
    assert env == {"CONSULT_BACKEND": "openai"}


def test_cmd_setup_detected_providers_do_not_select_backend(tmp_path, monkeypatch):
    answers_file = _write_base_setup_answers(
        tmp_path,
        configure_advanced_packs=True,
        selected_packs=["debug-tools"],
        backend_pref="   ",
    )
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(
        cc_setup,
        "_detect_backends",
        lambda: {"claude": "found", "openai": "key found"},
    )

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0

    settings = json.loads(
        (tmp_path / ".controlcoding" / "settings.json").read_text(encoding="utf-8")
    )
    env = settings["mcpServers"]["debug-consultant"].get("env", {})
    assert "CONSULT_BACKEND" not in env
    assert "CONSULT_FALLBACK_BACKEND" not in env


def test_cmd_setup_without_new_choice_preserves_existing_backend_and_fallback(tmp_path, monkeypatch):
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    existing_server = {
        "command": "custom-python",
        "args": ["custom_consultant.py"],
        "env": {
            "CONSULT_BACKEND": "anthropic",
            "CONSULT_FALLBACK_BACKEND": "ollama",
            "CUSTOM_SETTING": "keep",
        },
    }
    (control_dir / "settings.json").write_text(
        json.dumps({"mcpServers": {"debug-consultant": existing_server}}),
        encoding="utf-8",
    )
    answers_file = _write_base_setup_answers(
        tmp_path,
        configure_advanced_packs=True,
        selected_packs=["debug-tools"],
    )
    _patch_base_setup_runtime(monkeypatch)
    monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {"claude": "found"})

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0

    settings = json.loads((control_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["mcpServers"]["debug-consultant"] == existing_server


def test_cmd_setup_rejects_unknown_backend_before_any_write(tmp_path, monkeypatch, capsys):
    def unexpected_write_gate(*args, **kwargs):
        raise AssertionError("setup reached its first write gate")

    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", unexpected_write_gate)

    for index, backend_pref in enumerate(("configure later", "unknown-provider"), start=1):
        project = tmp_path / f"case-{index}"
        project.mkdir()
        answers_file = _write_base_setup_answers(
            project,
            configure_advanced_packs=True,
            selected_packs=["debug-tools"],
            backend_pref=backend_pref,
        )

        assert cc_setup.cmd_setup(project, answers_file=answers_file, apply_answers=True) == 1
        assert list(project.iterdir()) == [answers_file]

    out = capsys.readouterr().out
    assert "Unsupported consultant backend 'configure later'" in out
    assert "Unsupported consultant backend 'unknown-provider'" in out


def test_cmd_setup_rejects_invalid_selected_packs_before_any_write(
    tmp_path, monkeypatch, capsys
):
    def unexpected_write_gate(*args, **kwargs):
        raise AssertionError("setup reached its first write gate")

    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", unexpected_write_gate)

    invalid_values = (["debug-tools", "unknown-pack"], "debug-tools", [7])
    for index, selected_packs in enumerate(invalid_values, start=1):
        project = tmp_path / f"invalid-packs-{index}"
        project.mkdir()
        answers_file = _write_base_setup_answers(
            project,
            configure_advanced_packs=True,
            selected_packs=selected_packs,
        )

        assert cc_setup.cmd_setup(
            project, answers_file=answers_file, apply_answers=True
        ) == 1
        assert list(project.iterdir()) == [answers_file]

    out = capsys.readouterr().out
    assert "Unsupported setup pack 'unknown-pack'" in out
    assert "`selected_packs` must be a list" in out
    assert "must contain only supported pack IDs" in out


def test_cmd_setup_preserves_foreign_adapter_and_keeps_non_adapter_flow_separate(
    tmp_path,
    monkeypatch,
    capsys,
):
    answers_file = _write_base_setup_answers(
        tmp_path,
        user_host="codex_cli",
        configure_advanced_packs=True,
        selected_packs=["session-manager"],
    )
    foreign = tmp_path / "AGENTS.md"
    original = (
        "# AGENTS.md\n\n"
        "<!-- managed-by: OtherTool -->\n"
        "user-authored agent rules\n"
    )
    foreign.write_text(original, encoding="utf-8")
    adapter_before = (
        foreign.read_bytes(),
        stat.S_IMODE(os.lstat(foreign).st_mode),
        cc._transaction_file_object_key(os.lstat(foreign)),
    )
    calls = []
    boundary_sentinel = tmp_path / "repo-boundary.completed"
    init_sentinel = tmp_path / "init.completed"
    assets_sentinel = tmp_path / "assets.completed"
    memory_sentinel = tmp_path / "memory.completed"
    pack_sentinel = tmp_path / "pack-session-manager.completed"
    doctor_sentinel = tmp_path / "doctor.completed"

    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {})

    def forbid_external_command(*args, **kwargs):
        raise AssertionError("setup attempted a real external command")

    monkeypatch.setattr(cc_setup.subprocess, "run", forbid_external_command)

    def record_repo_boundary(project, host_profile):
        assert project == tmp_path
        assert isinstance(host_profile, dict)
        calls.append(("repo_boundary",))
        boundary_sentinel.write_text("completed\n", encoding="utf-8")
        return True

    def record_canonical_context(project, answers):
        assert calls == [("repo_boundary",)]
        calls.append(("canonical_context", answers["name"]))
        (project / "CONTROLCODING.md").write_text(
            "# Canonical setup sentinel\n",
            encoding="utf-8",
        )
        return True

    def record_init(project, central_hooks=False, quiet=False):
        assert calls[-1][0] == "canonical_context"
        assert (project / ".controlcoding" / "cc_config.json").is_file()
        assert (project / ".controlcoding" / "gateway_config.json").is_file()
        calls.append(("init", central_hooks, quiet))
        init_sentinel.write_text("completed\n", encoding="utf-8")
        return 0

    def record_assets(project, user_host, host_instructions=None):
        assert init_sentinel.is_file()
        calls.append(("host_assets", user_host))
        assets_sentinel.write_text("completed\n", encoding="utf-8")
        return ["assets.completed"]

    original_sync = cc_setup._sync_host_context_file

    def record_adapter(project, user_host, force_refresh=False):
        assert assets_sentinel.is_file()
        assert not memory_sentinel.exists()
        calls.append(("adapter", user_host, force_refresh))
        return original_sync(project, user_host, force_refresh=force_refresh)

    def forbid_adapter_apply(*args, **kwargs):
        raise AssertionError("foreign adapter reached the mutation boundary")

    def record_memory(project, name, policy):
        assert calls[-1][0] == "adapter"
        assert foreign.read_bytes() == adapter_before[0]
        calls.append(("memory", name, policy))
        memory_sentinel.write_text("completed\n", encoding="utf-8")
        return {"status": "completed", "receiptPath": "memory.completed"}

    def record_pack(project, pack):
        assert memory_sentinel.is_file()
        calls.append(("pack", pack))
        pack_sentinel.write_text("completed\n", encoding="utf-8")
        return 0

    def record_doctor(project):
        assert pack_sentinel.is_file()
        calls.append(("doctor",))
        doctor_sentinel.write_text("completed\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", record_repo_boundary)
    monkeypatch.setattr(cc_setup, "_generate_context_source", record_canonical_context)
    monkeypatch.setattr(cc_setup, "cmd_init", record_init)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", record_assets)
    monkeypatch.setattr(cc_setup, "_sync_host_context_file", record_adapter)
    monkeypatch.setattr(cc_setup, "_apply_adapter_transaction", forbid_adapter_apply)
    monkeypatch.setattr(cc_setup, "_bootstrap_default_project_memory", record_memory)
    monkeypatch.setattr(cc_setup, "cmd_install", record_pack)
    monkeypatch.setattr(cc_setup, "cmd_doctor", record_doctor)

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 1
    assert calls == [
        ("repo_boundary",),
        ("canonical_context", "Neutral Setup"),
        ("init", False, True),
        ("host_assets", "codex_cli"),
        ("adapter", "codex_cli", True),
        ("memory", "Neutral Setup", "governed_scope"),
        ("pack", "session-manager"),
        ("doctor",),
    ]
    assert (
        foreign.read_bytes(),
        stat.S_IMODE(os.lstat(foreign).st_mode),
        cc._transaction_file_object_key(os.lstat(foreign)),
    ) == adapter_before
    assert (tmp_path / "CONTROLCODING.md").read_text(encoding="utf-8") == (
        "# Canonical setup sentinel\n"
    )
    cc_config = json.loads(
        (tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8")
    )
    gateway = json.loads(
        (tmp_path / ".controlcoding" / "gateway_config.json").read_text(encoding="utf-8")
    )
    assert cc_config["documentation_mode"] == "managed"
    assert gateway["userHost"] == "codex_cli"
    assert all(path.read_text(encoding="utf-8") == "completed\n" for path in (
        boundary_sentinel,
        init_sentinel,
        assets_sentinel,
        memory_sentinel,
        pack_sentinel,
        doctor_sentinel,
    ))
    assert not (tmp_path / ".git").exists()
    output = capsys.readouterr().out
    assert "Adapter preview" in output
    assert "Ownership: foreign" in output
    assert "Preflight: blocked" in output


def test_cmd_setup_blocks_silent_controlwork_source_rebind(tmp_path, monkeypatch):
    canonical = tmp_path / "CONTROLCODING.md"
    canonical.write_text("# Existing canonical\n", encoding="utf-8")
    adapter = tmp_path / "AGENTS.md"
    adapter.write_text(_owned_codex_adapter("CONTROLWORK.md"), encoding="utf-8")
    answers_file = _write_base_setup_answers(tmp_path, user_host="codex_cli")
    before_adapter = adapter.read_bytes()
    _patch_base_setup_runtime(monkeypatch)

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 1
    assert adapter.read_bytes() == before_adapter


def test_cmd_setup_keep_existing_preserves_exact_crlf_source_bytes(tmp_path, monkeypatch):
    canonical = tmp_path / "CONTROLCODING.md"
    original_bytes = (
        "# Existing\r\n\r\n## Project Identity\r\n\r\n"
        "- **Name**: Existing\r\n- **Stack**: Python\r\n"
        "- **Architecture**: Modular\r\n"
    ).encode("utf-8")
    canonical.write_bytes(original_bytes)
    answers_file = _write_base_setup_answers(tmp_path, user_host="codex_cli")
    _patch_base_setup_runtime(monkeypatch)

    def keep_existing(prompt, default=True, apply_answers=False):
        if prompt.startswith("Overwrite existing context file"):
            return False
        return default

    monkeypatch.setattr(cc_setup, "_ask_yn_or_default", keep_existing)

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0
    assert canonical.read_bytes() == original_bytes
    marker, state, _detail = cc_setup._adapter_marker_payload(
        (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    )
    assert state == "owned"
    assert marker["source"] == "CONTROLCODING.md"


def test_cmd_setup_keep_existing_never_enters_adapter_transaction_as_canonical_entry(
    tmp_path,
    monkeypatch,
):
    canonical = tmp_path / "CONTROLCODING.md"
    canonical.write_text(
        "# Existing\n\n## Project Identity\n\n- **Name**: Existing\n",
        encoding="utf-8",
    )
    answers_file = _write_base_setup_answers(tmp_path, user_host="codex_cli")
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", lambda *args: True)

    def keep_existing(prompt, default=True, apply_answers=False):
        if prompt.startswith("Overwrite existing context file"):
            return False
        return default

    monkeypatch.setattr(cc_setup, "_ask_yn_or_default", keep_existing)
    _patch_base_setup_runtime(monkeypatch)
    observed = []

    def record_adapter_transaction(project, entries):
        observed.extend(entries)
        assert all(entry["kind"] == "adapter" for entry in entries)
        return {
            "ok": True,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "error": "",
            "written": [],
        }

    monkeypatch.setattr(cc_setup, "_apply_adapter_transaction", record_adapter_transaction)

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0
    assert canonical.read_text(encoding="utf-8").startswith("# Existing")
    assert observed and {entry["kind"] for entry in observed} == {"adapter"}


def test_cmd_setup_project_preserves_foreign_adapter(tmp_path, monkeypatch):
    canonical = tmp_path / "CONTROLCODING.md"
    canonical.write_text(
        "# Existing\n\n## Project Identity\n\n- **Name**: Existing\n- **Stack**: Python\n- **Architecture**: Modular\n",
        encoding="utf-8",
    )
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    cc_config = control_dir / "cc_config.json"
    gateway = control_dir / "gateway_config.json"
    cc_config.write_text(json.dumps({"documentation_mode": "managed"}), encoding="utf-8")
    gateway.write_text(json.dumps({"userHost": "codex_cli"}), encoding="utf-8")
    foreign = tmp_path / "AGENTS.md"
    foreign.write_text("user-authored agent rules\n", encoding="utf-8")
    answers_file = _write_project_setup_answers(tmp_path)
    adapter_before = (
        foreign.read_bytes(),
        stat.S_IMODE(os.lstat(foreign).st_mode),
        cc._transaction_file_object_key(os.lstat(foreign)),
    )
    gateway_before = gateway.read_bytes()
    base_init_sentinel = tmp_path / "base-init.completed"
    base_init_sentinel.write_text("completed\n", encoding="utf-8")
    identity_sentinel = tmp_path / "context-identity.completed"
    kickoff_sentinel = tmp_path / "kickoff.completed"
    fitness_sentinel = tmp_path / "fitness.json"
    intent_sentinel = control_dir / "setup-intent.completed"
    calls = []

    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(
        cc_setup,
        "_detect_existing_brief_sources",
        lambda project: ["README.md"],
    )
    monkeypatch.setattr(
        cc_setup,
        "_detect_obvious_environment_inventory",
        lambda: {"toolchains": [], "package_managers": []},
    )

    def forbid_external_or_init(*args, **kwargs):
        raise AssertionError("project setup attempted git init or an external command")

    monkeypatch.setattr(cc_setup.subprocess, "run", forbid_external_or_init)
    monkeypatch.setattr(cc_setup, "cmd_init", forbid_external_or_init)

    def record_identity(project, answers):
        calls.append(("canonical_context", answers["name"]))
        canonical.write_text(
            "# Existing\n\n## Project Identity\n\n"
            "- **Name**: Project setup sentinel\n"
            "- **Stack**: Python\n"
            "- **Architecture**: Modular\n",
            encoding="utf-8",
        )
        identity_sentinel.write_text("completed\n", encoding="utf-8")
        return True

    original_sync = cc_setup._sync_host_context_file

    def record_adapter(project, user_host, force_refresh=False):
        assert calls[-1][0] == "canonical_context"
        assert identity_sentinel.is_file()
        assert (control_dir / "cc_config.json").is_file()
        calls.append(("adapter", user_host, force_refresh))
        return original_sync(project, user_host, force_refresh=force_refresh)

    def forbid_adapter_apply(*args, **kwargs):
        raise AssertionError("foreign project adapter reached mutation")

    def record_kickoff(project, answers):
        assert calls[-1][0] == "adapter"
        calls.append(("kickoff", answers["project_definition_mode"]))
        kickoff_sentinel.write_text("completed\n", encoding="utf-8")
        return ["kickoff.completed"]

    def record_fitness(project, answers, force_refresh=False):
        assert kickoff_sentinel.is_file()
        calls.append(("fitness", force_refresh))
        fitness_sentinel.write_text('{"completed": true}\n', encoding="utf-8")
        return True, "fitness.json"

    def record_intent(
        project,
        answers,
        brief_sources,
        protected_zones,
        synced_context_path,
        kickoff_written,
        fitness_label,
    ):
        assert fitness_sentinel.is_file()
        assert synced_context_path is None
        assert kickoff_written == ["kickoff.completed"]
        assert fitness_label == "fitness.json"
        calls.append(("setup_intent", tuple(brief_sources)))
        intent_sentinel.write_text("completed\n", encoding="utf-8")
        return ".controlcoding/setup-intent.completed"

    monkeypatch.setattr(cc_setup, "_update_existing_context_identity", record_identity)
    monkeypatch.setattr(cc_setup, "_sync_host_context_file", record_adapter)
    monkeypatch.setattr(cc_setup, "_apply_adapter_transaction", forbid_adapter_apply)
    monkeypatch.setattr(cc_setup, "_scaffold_kickoff_docs", record_kickoff)
    monkeypatch.setattr(cc_setup, "_scaffold_fitness_config", record_fitness)
    monkeypatch.setattr(cc_setup, "_write_setup_intent_snapshot", record_intent)

    assert cc_setup.cmd_setup_project(tmp_path, answers_file=answers_file, apply_answers=True) == 1
    assert calls == [
        ("canonical_context", "Existing"),
        ("adapter", "codex_cli", True),
        ("kickoff", "existing_brief"),
        ("fitness", True),
        ("setup_intent", ("README.md",)),
    ]
    assert (
        foreign.read_bytes(),
        stat.S_IMODE(os.lstat(foreign).st_mode),
        cc._transaction_file_object_key(os.lstat(foreign)),
    ) == adapter_before
    assert gateway.read_bytes() == gateway_before
    assert base_init_sentinel.read_text(encoding="utf-8") == "completed\n"
    assert identity_sentinel.read_text(encoding="utf-8") == "completed\n"
    assert kickoff_sentinel.read_text(encoding="utf-8") == "completed\n"
    assert json.loads(fitness_sentinel.read_text(encoding="utf-8")) == {
        "completed": True,
    }
    assert intent_sentinel.read_text(encoding="utf-8") == "completed\n"
    updated_config = json.loads(cc_config.read_text(encoding="utf-8"))
    assert updated_config["project_definition_mode"] == "existing_brief"
    assert not (tmp_path / ".git").exists()


def test_s4_stat_09_force_refresh_is_legacy_and_cannot_bypass_foreign_ownership(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "CONTROLCODING.md").write_text(
        "# Canonical\n\n## Project Identity\n\n- **Name**: Fixture\n",
        encoding="utf-8",
    )
    target = tmp_path / "AGENTS.md"
    target.write_text(
        "# AGENTS.md\n\n<!-- managed-by: OtherTool -->\nforeign payload\n",
        encoding="utf-8",
    )
    before = (
        target.read_bytes(),
        stat.S_IMODE(os.lstat(target).st_mode),
        cc._transaction_file_object_key(os.lstat(target)),
    )

    def forbidden_apply(*args, **kwargs):
        raise AssertionError("legacy force_refresh bypassed ownership preflight")

    monkeypatch.setattr(cc_setup, "_apply_adapter_transaction", forbidden_apply)
    outcomes = []
    for force_refresh in (False, True):
        result = cc_setup._sync_host_context_file(
            tmp_path,
            "codex_cli",
            force_refresh=force_refresh,
        )
        plan = cc._adapter_plan_payload(
            tmp_path,
            host="codex_cli",
            operation="setup",
        )
        entry = plan["entries"][0]
        outcomes.append((
            result,
            plan["preflightOk"],
            entry["state"],
            entry["ownership"],
            entry["action"],
            entry["bindingAllowed"],
        ))

    assert outcomes == [
        (None, False, "foreign", "foreign", "block", False),
        (None, False, "foreign", "foreign", "block", False),
    ]
    assert (
        target.read_bytes(),
        stat.S_IMODE(os.lstat(target).st_mode),
        cc._transaction_file_object_key(os.lstat(target)),
    ) == before


def test_cmd_setup_project_blocks_silent_controlwork_source_rebind(tmp_path):
    canonical = tmp_path / "CONTROLCODING.md"
    canonical.write_text(
        "# Existing\n\n## Project Identity\n\n"
        "- **Name**: Existing\n- **Stack**: Python\n- **Architecture**: Modular\n",
        encoding="utf-8",
    )
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    cc_config = control_dir / "cc_config.json"
    gateway = control_dir / "gateway_config.json"
    cc_config.write_text(json.dumps({"documentation_mode": "managed"}), encoding="utf-8")
    gateway.write_text(json.dumps({"userHost": "codex_cli"}), encoding="utf-8")
    adapter = tmp_path / "AGENTS.md"
    adapter.write_text(_owned_codex_adapter("CONTROLWORK.md"), encoding="utf-8")
    answers_file = _write_project_setup_answers(tmp_path)
    adapter_before = adapter.read_bytes()

    assert cc_setup.cmd_setup_project(tmp_path, answers_file=answers_file, apply_answers=True) == 1
    assert adapter.read_bytes() == adapter_before


def _memory_paths(project: Path) -> set[str]:
    conn = sqlite3.connect(project / ".controlcoding" / "memory" / "memory.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT path FROM entities WHERE path IS NOT NULL AND path != ''"
        ).fetchall()
        return {str(row["path"]) for row in rows}
    finally:
        conn.close()


def test_build_cc_config_preserves_existing_fields():
    config = cc_setup._build_cc_config(
        {
            "gateway_modules": {"payments": "src/gateway/payments.py"},
            "workflow_rules": {"require_plan_before_edit": True},
        },
        protected_zones=[{"path": "src/core/", "level": "deny", "description": "Core"}],
        documentation_mode="project_managed",
        hooks_location="central",
        cc_artifact_mode="local_only",
        product={"product_form": "service", "runtime_constraints": "local CLI"},
        environment={"inspection_permission": "denied", "install_permission": "denied"},
    )

    assert config["documentation_mode"] == "project_managed"
    assert config["cc_artifact_mode"] == "local_only"
    assert config["hooks_location"] == "central"
    assert config["memory_default_policy"] == "governed_scope"
    assert config["protected_zones"][0]["path"] == "src/core/"
    assert config["gateway_modules"]["payments"] == "src/gateway/payments.py"
    assert config["workflow_rules"]["require_plan_before_edit"] is True
    assert config["product"]["product_form"] == "service"
    assert config["environment"]["install_permission"] == "denied"


def test_build_environment_policy_prefers_explicit_install_isolation(tmp_path, monkeypatch):
    answers = iter(["1", str(tmp_path / ".venv")])

    monkeypatch.setattr(cc_setup, "_ask", lambda prompt, default="": next(answers))
    monkeypatch.setattr(
        cc_setup,
        "_ask_yn",
        lambda prompt, default=True: True if "install missing dependencies" in prompt.lower() or "broader local environment inspection" in prompt.lower() or "prefer a venv" in prompt.lower() else default,
    )

    policy = cc_setup._build_environment_policy({}, {"toolchains": ["Python"], "package_managers": ["pip"]})

    assert policy["inspection_permission"] == "granted"
    assert policy["install_permission"] == "granted"
    assert policy["install_isolation_preference"] == "isolated"
    assert policy["venv_preference"] == "prefer"
    assert policy["preferred_install_locations"]["default"] == str(tmp_path / ".venv")


def test_build_gateway_config_preserves_existing_fields():
    config = cc_setup._build_gateway_config(
        {
            "uiMode": "visualizer",
            "agents": {"mainCoder": {"active": True, "backend": "claude_cli"}},
        },
        user_host="claude_code",
    )

    assert config["userHost"] == "claude_code"
    assert config["enabledHosts"] == ["claude_code"]
    assert config["hostProfile"]["userHost"] == "claude_code"
    assert config["hostProfile"]["capabilityClass"] == "native_inline_hooks"
    assert config["hostProfile"]["contextFile"] == "CLAUDE.md"
    assert config["uiMode"] == "visualizer"
    assert config["agents"]["mainCoder"]["backend"] == "claude_cli"


def test_normalize_uncertain_text_maps_confused_answers_to_blank():
    assert cc_setup._normalize_uncertain_text("I have no idea?") == ""
    assert cc_setup._normalize_uncertain_text("what do you mean?") == ""
    assert cc_setup._normalize_uncertain_text("C++20 with OpenGL") == "C++20 with OpenGL"


def test_build_host_instructions_config_custom_sanitizes_notes():
    config = cc_setup._build_host_instructions_config(
        "custom",
        ["  remind me to check surface status  ", "", "keep cursor observer-only"],
    )
    assert config["mode"] == "custom"
    assert config["customNotes"] == [
        "remind me to check surface status",
        "keep cursor observer-only",
    ]


def test_write_host_launcher_assets_for_cli_host(tmp_path):
    written = cc_setup._write_host_launcher_assets(tmp_path, "claude_code")
    assert ".controlcoding/launchers/launch_primary_host.cmd" in written
    assert ".controlcoding/launchers/claim_primary_host.cmd" in written
    launch_cmd = (tmp_path / ".controlcoding" / "launchers" / "launch_primary_host.cmd").read_text(encoding="utf-8")
    assert "surface\" \"run\"" in launch_cmd
    assert "claude" in launch_cmd.lower()


def test_write_host_launcher_assets_for_editor_host_omits_run_wrapper(tmp_path):
    written = cc_setup._write_host_launcher_assets(tmp_path, "vscode")
    assert ".controlcoding/launchers/claim_primary_host.cmd" in written
    assert ".controlcoding/launchers/observe_host.cmd" in written
    assert ".controlcoding/launchers/launch_primary_host.cmd" not in written
    readme = (tmp_path / ".controlcoding" / "launchers" / "README.txt").read_text(encoding="utf-8")
    assert "claim_primary_host" in readme


def test_write_host_integration_assets_installs_vscode_tasks_when_missing(tmp_path):
    written = cc_setup._write_host_integration_assets(
        tmp_path,
        "windsurf",
        {"mode": "custom", "customNotes": ["keep Windsurf observer-only when Claude Code is primary"]},
    )
    assert ".controlcoding/launchers/windsurf.tasks.json" in written
    assert ".controlcoding/launchers/manifest.json" in written
    assert ".controlcoding/launchers/host_instructions.md" in written
    assert ".controlcoding/public_assistant/START_HERE_WITH_CHAT.md" in written
    assert ".controlcoding/public_assistant/INSTALL_ASSISTANT_CONTRACT.md" in written
    assert ".controlcoding/public_assistant/PROJECT_SETUP_ASSISTANT_CONTRACT.md" in written
    assert ".vscode/tasks.json" in written
    tasks = (tmp_path / ".vscode" / "tasks.json").read_text(encoding="utf-8")
    assert "ControlCoding: Claim Windsurf as Primary" in tasks
    assert "ControlCoding: Surface Status" in tasks
    manifest = (tmp_path / ".controlcoding" / "launchers" / "manifest.json").read_text(encoding="utf-8")
    assert '"userHost": "windsurf"' in manifest
    assert '"installed": true' in manifest
    assert '"presetId": "windsurf"' in manifest
    assert '"templatePath": ".controlcoding/launchers/windsurf.tasks.json"' in manifest
    assert '"nextSteps": [' in manifest
    assert '"instructionMode": "custom"' in manifest
    assert '"starterPath": ".controlcoding/public_assistant/START_HERE_WITH_CHAT.md"' in manifest
    assert '"installContractPath": ".controlcoding/public_assistant/INSTALL_ASSISTANT_CONTRACT.md"' in manifest
    assert '"projectSetupContractPath": ".controlcoding/public_assistant/PROJECT_SETUP_ASSISTANT_CONTRACT.md"' in manifest
    instructions = (tmp_path / ".controlcoding" / "launchers" / "host_instructions.md").read_text(encoding="utf-8")
    assert "keep Windsurf observer-only when Claude Code is primary" in instructions
    starter = (tmp_path / ".controlcoding" / "public_assistant" / "START_HERE_WITH_CHAT.md").read_text(encoding="utf-8")
    assert "start from chat without memorizing cli commands" in starter.lower()
    assert "INSTALL_ASSISTANT_CONTRACT.md" in starter
    install_contract = (tmp_path / ".controlcoding" / "public_assistant" / "INSTALL_ASSISTANT_CONTRACT.md").read_text(encoding="utf-8")
    assert "If the host can execute local commands" in install_contract
    setup_contract = (tmp_path / ".controlcoding" / "public_assistant" / "PROJECT_SETUP_ASSISTANT_CONTRACT.md").read_text(encoding="utf-8")
    assert "brownfield adoption path" in setup_contract


def test_write_host_integration_assets_keeps_cli_host_task_template_local_only(tmp_path):
    written = cc_setup._write_host_integration_assets(tmp_path, "claude_code")

    assert ".controlcoding/launchers/claude-code.tasks.json" in written
    assert ".controlcoding/public_assistant/START_HERE_WITH_CHAT.md" in written
    assert ".vscode/tasks.json" not in written

    manifest = (tmp_path / ".controlcoding" / "launchers" / "manifest.json").read_text(encoding="utf-8")
    assert '"autoInstallSupported": false' in manifest
    assert '"installed": false' in manifest
    assert '"preexisting": false' in manifest
    assert '"templatePath": ".controlcoding/launchers/claude-code.tasks.json"' in manifest


def test_write_host_integration_assets_preserves_existing_vscode_tasks(tmp_path):
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    existing = '{\n  "version": "2.0.0",\n  "tasks": [{"label": "User Task"}]\n}\n'
    (vscode_dir / "tasks.json").write_text(existing, encoding="utf-8")

    written = cc_setup._write_host_integration_assets(tmp_path, "claude_code")

    assert ".controlcoding/launchers/claude-code.tasks.json" in written
    assert ".controlcoding/launchers/manifest.json" in written
    assert ".controlcoding/launchers/host_instructions.md" in written
    assert ".controlcoding/public_assistant/START_HERE_WITH_CHAT.md" in written
    assert ".vscode/tasks.json" not in written
    assert (vscode_dir / "tasks.json").read_text(encoding="utf-8") == existing
    manifest = (tmp_path / ".controlcoding" / "launchers" / "manifest.json").read_text(encoding="utf-8")
    assert '"preexisting": true' in manifest
    assert "merge `.controlcoding/launchers/claude-code.tasks.json`" in manifest


def test_scan_dirs_prefers_src_children_over_coarse_container(tmp_path):
    (tmp_path / "src" / "game").mkdir(parents=True)
    (tmp_path / "src" / "renderer").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    assert cc_setup._scan_dirs(tmp_path) == ["src/game", "src/renderer", "tests"]


def test_detect_existing_brief_sources_finds_ranked_candidates(tmp_path):
    (tmp_path / "project_brief.md").write_text("# brief\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    (tmp_path / "vault_requirements.md").write_text("# reqs\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design-notes.md").write_text("# design\n", encoding="utf-8")
    (tmp_path / "CONTROLCODING.md").write_text("# cc\n", encoding="utf-8")

    assert cc_setup._detect_existing_brief_sources(tmp_path) == [
        "project_brief.md",
        "README.md",
        "vault_requirements.md",
        "docs/design-notes.md",
    ]


def test_generate_claude_md_includes_managed_documentation_policy(tmp_path):
    cc_setup._generate_claude_md(
        tmp_path,
        {
            "name": "Demo",
            "stack": "Python",
            "arch": "Modular",
            "documentation_mode": "managed",
            "cc_artifact_mode": "local_only",
        },
    )

    canonical = (tmp_path / "CONTROLCODING.md").read_text(encoding="utf-8")
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Documentation Governance" in canonical
    assert "## Documentation Governance" in content
    assert "Managed by ControlCoding" in content
    assert "ControlCoding taxonomy and filename standard" in content
    assert "Local-only" in content
    assert "devlog/" in content
    assert "always local session memory" in content


def test_generate_claude_md_includes_project_managed_policy(tmp_path):
    cc_setup._generate_claude_md(
        tmp_path,
        {
            "name": "Demo",
            "stack": "Python",
            "arch": "Modular",
            "documentation_mode": "project_managed",
            "cc_artifact_mode": "shared_repo",
        },
    )

    canonical = (tmp_path / "CONTROLCODING.md").read_text(encoding="utf-8")
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Documentation Governance" in canonical
    assert "## Documentation Governance" in content
    assert "Project-managed" in content
    assert "Do not reorganize docs" in content
    assert "Shared-repo" in content
    assert "devlog/" in content
    assert "not part of the shared repo" in content


def test_scaffold_kickoff_docs_managed_mode_creates_design_plan_and_indexes(tmp_path):
    written = cc_setup._scaffold_kickoff_docs(
        tmp_path,
        {
            "name": "Demo Project",
            "stack": "Python 3.12",
            "arch": "Layered CLI",
            "documentation_mode": "managed",
            "planning": {
                "tier": "core",
                "planning_mode": "solo_structured_manual_consultation_ready",
                "planning_authority": "single_author_with_manual_consultation",
                "manual_consultation_allowed": True,
            },
            "stable": ["src/core"],
            "shared": ["src/shared"],
            "features": ["src/features"],
            "kickoff": {
                "mode": "idea",
                "vision": "A tool that turns raw ideas into structured work.",
                "users": "Solo developer writing production code.",
                "must_haves": "design doc; implementation plan; roadmap",
                "invariants": "The plan must not drift from the design.",
                "quality": "Readable docs; explicit constraints.",
                "anti_goals": "No UI in v1.",
                "references": "",
            },
        },
    )

    assert "dev/design/01_DSN_ProjectFoundation_InProgress.md" in written
    assert "dev/project-definition/01_DEF_SourceAssessment_InProgress.md" in written
    assert "dev/project-definition/02_DEF_ConsultationPlanning_InProgress.md" in written
    assert "dev/project-definition/INDEX.md" in written
    assert "dev/project-definition/manual-consultation/INDEX.md" in written
    assert "dev/project-definition/manual-consultation/01_MAN_software_architecture_specialist_Template.md" in written
    assert "dev/project-definition/manual-consultation/02_MAN_verification_acceptance_specialist_Template.md" in written
    assert "dev/project-definition/manual-consultation/03_MAN_implementation_planning_specialist_Template.md" in written
    assert "dev/project-definition/manual-consultation/04_MAN_domain_design_specialist_Template.md" in written
    assert "dev/design/02_DSN_SystemOverview_InProgress.md" in written
    assert "dev/design/03_DSN_ArchitectureAndBoundaries_InProgress.md" in written
    assert "dev/design/04_DSN_CoreMechanics_InProgress.md" in written
    assert "dev/plans/01_DEV_InitialImplementationPlan_Plan.md" in written
    assert "dev/implementation/00_IMP_MasterImplementationPlan_InProgress.md" in written
    assert "dev/implementation/01_IMP_ProgressiveProtectionPlan_InProgress.md" in written
    assert "dev/implementation/INDEX.md" in written
    assert "dev/implementation/features/INDEX.md" in written
    assert "dev/implementation/features/01_IMF_primary_end_to_end_user_workflow_InProgress.md" in written
    assert "dev/criteria/01_ACC_FirstSliceAcceptance_Checklist.md" in written
    assert "dev/criteria/02_ACC_RequirementsTraceability.md" in written
    assert "dev/criteria/03_ACC_VerificationMatrix.md" in written
    assert "dev/criteria/04_ACC_CoverageLedger.md" in written
    assert "dev/contracts/requirements.json" in written
    assert "dev/contracts/core_mechanics.json" in written
    assert "dev/contracts/architecture_contract.json" in written
    assert "dev/contracts/verification_contract.json" in written
    assert "dev/contracts/protection_contract.json" in written
    assert "ROADMAP.md" in written
    assert "BUGS.md" in written
    assert "dev/design/INDEX.md" in written
    assert "dev/design/subsystems/INDEX.md" in written
    assert "dev/design/subsystems/01_SUB_src_core_InProgress.md" in written
    assert "dev/design/subsystems/02_SUB_src_shared_InProgress.md" in written
    assert "dev/design/subsystems/03_SUB_src_features_InProgress.md" in written
    assert "dev/plans/INDEX.md" in written
    assert "dev/criteria/INDEX.md" in written
    assert "dev/contracts/INDEX.md" in written
    assert "dev/handoffs/01_HOF_PlanningConsultationPacket_Template.md" in written
    assert "dev/handoffs/INDEX.md" in written

    design = (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").read_text(encoding="utf-8")
    source_assessment = (tmp_path / "dev" / "project-definition" / "01_DEF_SourceAssessment_InProgress.md").read_text(encoding="utf-8")
    consultation_plan = (tmp_path / "dev" / "project-definition" / "02_DEF_ConsultationPlanning_InProgress.md").read_text(encoding="utf-8")
    overview = (tmp_path / "dev" / "design" / "02_DSN_SystemOverview_InProgress.md").read_text(encoding="utf-8")
    architecture = (tmp_path / "dev" / "design" / "03_DSN_ArchitectureAndBoundaries_InProgress.md").read_text(encoding="utf-8")
    mechanics = (tmp_path / "dev" / "design" / "04_DSN_CoreMechanics_InProgress.md").read_text(encoding="utf-8")
    plan = (tmp_path / "dev" / "plans" / "01_DEV_InitialImplementationPlan_Plan.md").read_text(encoding="utf-8")
    master_implementation = (tmp_path / "dev" / "implementation" / "00_IMP_MasterImplementationPlan_InProgress.md").read_text(encoding="utf-8")
    protection_plan = (tmp_path / "dev" / "implementation" / "01_IMP_ProgressiveProtectionPlan_InProgress.md").read_text(encoding="utf-8")
    criteria = (tmp_path / "dev" / "criteria" / "01_ACC_FirstSliceAcceptance_Checklist.md").read_text(encoding="utf-8")
    traceability = (tmp_path / "dev" / "criteria" / "02_ACC_RequirementsTraceability.md").read_text(encoding="utf-8")
    verification = (tmp_path / "dev" / "criteria" / "03_ACC_VerificationMatrix.md").read_text(encoding="utf-8")
    coverage = (tmp_path / "dev" / "criteria" / "04_ACC_CoverageLedger.md").read_text(encoding="utf-8")
    contracts_index = (tmp_path / "dev" / "contracts" / "INDEX.md").read_text(encoding="utf-8")
    subsystem_doc = (tmp_path / "dev" / "design" / "subsystems" / "03_SUB_src_features_InProgress.md").read_text(encoding="utf-8")
    requirements = json.loads((tmp_path / "dev" / "contracts" / "requirements.json").read_text(encoding="utf-8"))
    mechanics_json = json.loads((tmp_path / "dev" / "contracts" / "core_mechanics.json").read_text(encoding="utf-8"))
    architecture_contract = json.loads((tmp_path / "dev" / "contracts" / "architecture_contract.json").read_text(encoding="utf-8"))
    verification_contract = json.loads((tmp_path / "dev" / "contracts" / "verification_contract.json").read_text(encoding="utf-8"))
    protection_contract = json.loads((tmp_path / "dev" / "contracts" / "protection_contract.json").read_text(encoding="utf-8"))
    roadmap = (tmp_path / "ROADMAP.md").read_text(encoding="utf-8")
    packet = (tmp_path / "dev" / "handoffs" / "01_HOF_PlanningConsultationPacket_Template.md").read_text(encoding="utf-8")
    manual_packet = (tmp_path / "dev" / "project-definition" / "manual-consultation" / "01_MAN_software_architecture_specialist_Template.md").read_text(encoding="utf-8")

    assert "Establish the initial design truth before implementation starts." in design
    assert "Design Package Map" in design
    assert "Source assessment" in design
    assert "Consultation planning" in design
    assert "requirements.json" in design
    assert "Implementation master plan" in design
    assert "Progressive protection plan" in design
    assert "Must-Have Scope for v1" in design
    assert "Direct structured planning + manual consultation ready" in design
    assert "bounded manual consultation packet" in design
    assert "Planning Protocol" in design
    assert "Source Assessment" in source_assessment
    assert "Brief Candidates Found Locally" in source_assessment
    assert "Approval gate" in source_assessment
    assert "consultation_recommended" in source_assessment
    assert "Consultation Planning" in consultation_plan
    assert "Recommended Roles" in consultation_plan
    assert "Manual in Core" in consultation_plan
    assert "Formalize the product-level system view" in overview
    assert "Core Mechanics Snapshot" in overview
    assert "explicit subsystem boundaries, ownership, and change-control expectations" in architecture
    assert "Operational Subsystem Map" in architecture
    assert "Open Baseline Risks" in architecture
    assert "Progressive Protection Policy" in architecture
    assert "Make core mechanics explicit" in mechanics
    assert "Lifecycle Rule" in mechanics
    assert "Initial Implementation Plan" in plan
    assert "Build Order" in plan
    assert "Single author + manual consultation" in plan
    assert "If a blocking uncertainty remains, prepare a bounded manual consultation packet." in plan
    assert "Planning Decision Control" in plan
    assert "Package Handoff" in plan
    assert "Turn the approved design baseline into staged implementation work" in master_implementation
    assert "Protection Promotion Rule" in master_implementation
    assert "Progressive Protection Plan" in protection_plan
    assert "Subsystem Protection Matrix" in protection_plan
    assert "Status Model" in criteria
    assert "implemented" in criteria
    assert "self_tested" in criteria
    assert "human_verified" in criteria
    assert "formalization_needed" in criteria
    assert "design doc" in criteria
    assert "implementation plan" in criteria
    assert "roadmap" in criteria
    assert "Requirements Traceability" in traceability
    assert "Owner confidence" in traceability
    assert "Verification Matrix" in verification
    assert "Verification Matrix" in verification
    assert "Resolution" in verification
    assert "Coverage Ledger" in coverage
    assert "context_only" in coverage
    assert "Machine-readable governance artifacts" in contracts_index
    assert "protection_contract.json" in contracts_index
    assert "Subsystem Design: src/features" in subsystem_doc
    assert "Owned State And Artifacts" in subsystem_doc
    assert "Dependency Contract" in subsystem_doc
    assert requirements[0]["id"] == "REQ-01"
    assert requirements[0]["priority"] == "mandatory_mvp"
    assert requirements[0]["owner_subsystem"] == "owner_unresolved"
    assert "formalization_needed" in requirements[0]["baseline_flags"]
    assert mechanics_json[0]["id"] == "MECH-01"
    assert architecture_contract["subsystems"][0]["dependency_contract"]["allowed_outbound_zones"] == ["stable"]
    assert verification_contract["status_model"][-1] == "blocked"
    assert verification_contract["requirements"][0]["owner_resolution"] == "owner_unresolved"
    assert protection_contract["lifecycle_states"][-1] == "protected"
    assert protection_contract["subsystem_policies"][0]["first_protection_level"] == "warn"
    assert "Open Kickoff Gaps" in packet
    assert "Suggested Bounded Question Candidates" in packet
    assert "Question to external specialist" in packet
    assert "Returned advice remains advisory" in packet
    assert "Prompt To Paste Into Another Chat" in manual_packet
    assert "Act as a Software Architecture Specialist" in manual_packet
    assert "You are not writing production code" in manual_packet
    assert "Project bootstrapped with ControlCoding" in roadmap


def test_scaffold_kickoff_docs_agents_mode_mentions_specialist_planning(tmp_path):
    written = cc_setup._scaffold_kickoff_docs(
        tmp_path,
        {
            "name": "Demo Project",
            "stack": "C++",
            "arch": "Game slice",
            "documentation_mode": "managed",
            "planning": {
                "tier": "agents",
                "planning_mode": "orchestrated_specialists",
                "planning_authority": "orchestrated_multi_role",
                "manual_consultation_allowed": False,
            },
            "kickoff": {
                "mode": "partial_spec",
                "vision": "Build a playable vertical slice.",
                "users": "Single player.",
                "must_haves": "movement; combat; HUD",
                "invariants": "No crash",
                "quality": "Readable and stable",
                "anti_goals": "No multiplayer",
                "references": "",
            },
        },
    )

    assert "dev/design/01_DSN_ProjectFoundation_InProgress.md" in written
    assert "dev/project-definition/01_DEF_SourceAssessment_InProgress.md" in written
    assert "dev/project-definition/02_DEF_ConsultationPlanning_InProgress.md" in written
    assert "dev/project-definition/agent-specs/INDEX.md" in written
    assert "dev/project-definition/agent-specs/01_AGT_software_architecture_specialist_Spec.md" in written
    assert "dev/contracts/architecture_contract.json" in written
    assert "dev/implementation/00_IMP_MasterImplementationPlan_InProgress.md" in written
    assert "dev/contracts/protection_contract.json" in written
    assert "dev/handoffs/02_HOF_SpecialistPlanningRefinementPacket_Template.md" in written
    assert "dev/handoffs/INDEX.md" in written
    design = (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").read_text(encoding="utf-8")
    plan = (tmp_path / "dev" / "plans" / "01_DEV_InitialImplementationPlan_Plan.md").read_text(encoding="utf-8")
    packet = (tmp_path / "dev" / "handoffs" / "02_HOF_SpecialistPlanningRefinementPacket_Template.md").read_text(encoding="utf-8")
    consultation_plan = (tmp_path / "dev" / "project-definition" / "02_DEF_ConsultationPlanning_InProgress.md").read_text(encoding="utf-8")
    agent_spec = (tmp_path / "dev" / "project-definition" / "agent-specs" / "01_AGT_software_architecture_specialist_Spec.md").read_text(encoding="utf-8")
    assert "Specialist-assisted planning" in design
    assert "specialist perspectives" in design
    assert "Specialist Provenance Note" in design
    assert "orchestrated multi-role" in plan.lower()
    assert "Run specialist checkpoints on the highest-risk architectural or domain decisions." in plan
    assert "Planning Decision Control" in plan
    assert "Decision area | Lead owner | Specialist input | Outcome" in plan
    assert "Architecture specialist prompt" in packet
    assert "Verification specialist prompt" in packet
    assert "Recommended Roles" in consultation_plan
    assert "agent-specs/01_AGT_software_architecture_specialist_Spec.md" in consultation_plan
    assert "Required Capability Profile" in agent_spec
    assert "Execution Notes" in agent_spec


def test_scaffold_kickoff_docs_project_managed_uses_root_docs(tmp_path):
    written = cc_setup._scaffold_kickoff_docs(
        tmp_path,
        {
            "name": "Demo Project",
            "stack": "Python 3.12",
            "arch": "Layered CLI",
            "documentation_mode": "project_managed",
            "kickoff": {
                "mode": "partial_spec",
                "vision": "A tool that turns raw ideas into structured work.",
                "users": "",
                "must_haves": "",
                "invariants": "",
                "quality": "",
                "anti_goals": "",
                "references": "notes.md",
            },
        },
    )

    assert "DESIGN.md" in written
    assert "IMPLEMENTATION_PLAN.md" in written
    assert "ACCEPTANCE_CRITERIA.md" in written
    assert "design/INDEX.md" in written
    assert "acceptance/INDEX.md" in written
    assert "contracts/INDEX.md" in written
    assert "contracts/requirements.json" in written
    assert "contracts/core_mechanics.json" in written
    assert "contracts/architecture_contract.json" in written
    assert "contracts/verification_contract.json" in written
    assert "contracts/protection_contract.json" in written
    assert "project-definition/01_DEF_SourceAssessment.md" in written
    assert "project-definition/02_DEF_ConsultationPlanning.md" in written
    assert "implementation/00_IMP_MasterImplementationPlan.md" in written
    assert "implementation/01_IMP_ProgressiveProtectionPlan.md" in written
    assert "implementation/INDEX.md" in written
    assert "implementation/features/INDEX.md" in written
    assert "project-definition/INDEX.md" in written
    assert "acceptance/coverage_ledger.md" in written
    assert "ROADMAP.md" in written
    assert "BUGS.md" in written
    assert not (tmp_path / "dev" / "design" / "INDEX.md").exists()
    assert (tmp_path / "DESIGN.md").exists()
    assert (tmp_path / "IMPLEMENTATION_PLAN.md").exists()
    assert (tmp_path / "ACCEPTANCE_CRITERIA.md").exists()
    assert (tmp_path / "project-definition" / "01_DEF_SourceAssessment.md").exists()
    assert (tmp_path / "design" / "INDEX.md").exists()
    assert (tmp_path / "acceptance" / "INDEX.md").exists()
    assert (tmp_path / "contracts" / "requirements.json").exists()
    assert (tmp_path / "implementation" / "00_IMP_MasterImplementationPlan.md").exists()
    assert (tmp_path / "implementation" / "features" / "INDEX.md").exists()


def test_scaffold_kickoff_docs_existing_project_mode_creates_adoption_docs(tmp_path):
    for relpath in ["src/core", "src/ui", "tests", "docs"]:
        (tmp_path / relpath).mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text("# Existing Product\n", encoding="utf-8")
    (tmp_path / "docs" / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    written = cc_setup._scaffold_kickoff_docs(
        tmp_path,
        {
            "name": "Existing Demo",
            "stack": "Python 3.12",
            "arch": "Existing layered desktop app",
            "documentation_mode": "managed",
            "project_definition_mode": "existing_project",
            "planning": {
                "tier": "core",
                "planning_mode": "solo_structured_manual_consultation_ready",
                "planning_authority": "single_author_with_manual_consultation",
                "manual_consultation_allowed": True,
            },
            "stable": ["src/core"],
            "shared": ["src/ui"],
            "features": [],
            "truth": "Authoritative vault records and lock state",
            "view": "List/detail UI and unlock presentation state",
            "kickoff": {
                "mode": "existing_design",
                "vision": "Adopt governance on an already working local credential tool.",
                "users": "Single user managing personal credentials.",
                "must_haves": "Preserve current CRUD; lock state; search",
                "invariants": "Locked state must hide sensitive records",
                "quality": "No false security claims; preserve local-only behavior",
                "anti_goals": "No cloud sync",
                "references": "README.md",
            },
            "adoption": {
                "authoritative_docs": "README.md; docs/architecture.md",
                "implemented_scope": "credential CRUD; search; lock/unlock",
                "stable_areas": "src/core",
                "known_drift": "README is newer than architecture doc",
                "verification_signals": "tests/; local manual testing",
            },
        },
    )

    assert "dev/project-definition/03_DEF_ExistingProjectInventory_InProgress.md" in written
    assert "dev/project-definition/04_DEF_DocumentTruthMap_InProgress.md" in written
    assert "dev/project-definition/05_DEF_ArchitectureExtraction_InProgress.md" in written
    assert "dev/project-definition/06_DEF_MaturityAndGapAssessment_InProgress.md" in written
    assert "dev/project-definition/07_DEF_AdoptionPlan_InProgress.md" in written
    assert "dev/implementation/00_IMP_MasterImplementationPlan_InProgress.md" in written
    assert "dev/implementation/01_IMP_ProgressiveProtectionPlan_InProgress.md" in written
    assert "dev/contracts/protection_contract.json" in written

    inventory = (tmp_path / "dev" / "project-definition" / "03_DEF_ExistingProjectInventory_InProgress.md").read_text(encoding="utf-8")
    truth_map = (tmp_path / "dev" / "project-definition" / "04_DEF_DocumentTruthMap_InProgress.md").read_text(encoding="utf-8")
    extraction = (tmp_path / "dev" / "project-definition" / "05_DEF_ArchitectureExtraction_InProgress.md").read_text(encoding="utf-8")
    maturity = (tmp_path / "dev" / "project-definition" / "06_DEF_MaturityAndGapAssessment_InProgress.md").read_text(encoding="utf-8")
    adoption_plan = (tmp_path / "dev" / "project-definition" / "07_DEF_AdoptionPlan_InProgress.md").read_text(encoding="utf-8")
    master_implementation = (tmp_path / "dev" / "implementation" / "00_IMP_MasterImplementationPlan_InProgress.md").read_text(encoding="utf-8")
    definition_index = (tmp_path / "dev" / "project-definition" / "INDEX.md").read_text(encoding="utf-8")

    assert "Existing Project Inventory" in inventory
    assert "Tooling and Build Signals" in inventory
    assert "`pyproject.toml`" in inventory
    assert "Document Truth Map" in truth_map
    assert "explicit_authority" in truth_map
    assert "`README.md`" in truth_map
    assert "Architecture Extraction" in extraction
    assert "Protection Bootstrap Candidates" in extraction
    assert "`src/core/` -> `warn`" in extraction
    assert "Maturity And Gap Assessment" in maturity
    assert "adoption_ready" in maturity or "adoption_ready_with_gaps" in maturity
    assert "Adoption Plan" in adoption_plan
    assert "ADP-01" in adoption_plan
    assert "Freeze current authority" in adoption_plan
    assert "Implementation Lifecycle" in master_implementation
    assert "Adoption plan" in master_implementation
    assert "ExistingProjectInventory" in definition_index
    assert "AdoptionPlan" in definition_index


def test_cmd_setup_requires_chat_or_answers_file(tmp_path, capsys):
    result = cc_setup.cmd_setup(tmp_path)

    assert result == 1
    out = capsys.readouterr().out
    assert "Terminal questionnaire removed" in out
    assert "cc setup --chat-guide" in out
    assert "--answers-file handoff.json" in out


def test_cmd_setup_prefills_from_answers_file(tmp_path, monkeypatch):
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "DungeonCrawler3D v14",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured_manual_consultation_ready",
                        "planning_authority": "single_author_with_manual_consultation",
                        "manual_consultation_allowed": True,
                    },
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cc_setup, "_ask", lambda prompt, default="": default)
    monkeypatch.setattr(cc_setup, "_ask_yn", lambda prompt, default=True: default)
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "cmd_init", lambda project, central_hooks=False, quiet=False: 0)
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md" if user_host == "codex_cli" else "CLAUDE.md",
    )

    result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file)

    assert result == 0
    gateway = json.loads((tmp_path / ".controlcoding" / "gateway_config.json").read_text(encoding="utf-8"))
    assert gateway["userHost"] == "codex_cli"
    cc_config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
    assert cc_config["cc_artifact_mode"] == "local_only"
    assert cc_config["planning"]["planning_mode"] == "solo_structured_manual_consultation_ready"
    assert cc_config["planning"]["manual_consultation_allowed"] is True
    assert not (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").exists()


def test_cmd_setup_accepts_utf8_bom_answers_file(tmp_path, monkeypatch):
    answers_file = tmp_path / "setup_answers_bom.json"
    answers_file.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "BOM Setup",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured",
                        "planning_authority": "single_author",
                        "manual_consultation_allowed": False,
                    },
                }
            }
        ),
        encoding="utf-8-sig",
    )

    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "cmd_init", lambda project, central_hooks=False, quiet=False: 0)
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md" if user_host == "codex_cli" else "CLAUDE.md",
    )

    result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 0
    gateway = json.loads((tmp_path / ".controlcoding" / "gateway_config.json").read_text(encoding="utf-8"))
    assert gateway["userHost"] == "codex_cli"


def test_cmd_setup_apply_answers_rejects_wrong_section_handoff(tmp_path, capsys):
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps({"engagement": {"tier": "core", "backend_policy": "local_only"}}),
        encoding="utf-8",
    )

    result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 1
    out = capsys.readouterr().out
    assert "valid `setup` payload" in out
    assert not (tmp_path / "CONTROLCODING.md").exists()


def test_cmd_setup_bootstraps_git_for_non_inline_host_before_init(tmp_path, monkeypatch):
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "VS Code Fresh Setup",
                    "documentation_mode": "managed",
                    "user_host": "vscode",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured",
                        "planning_authority": "single_author",
                        "manual_consultation_allowed": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    git_calls: list[list[str]] = []
    init_state: dict[str, bool] = {}

    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup.shutil, "which", lambda name: "git")

    def fake_run(command, cwd=None, capture_output=False, text=False):
        git_calls.append(list(command))
        git_hooks = Path(cwd) / ".git" / "hooks"
        git_hooks.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(returncode=0, stdout="Initialized empty Git repository", stderr="")

    def fake_cmd_init(project, central_hooks=False, quiet=False):
        init_state["git_ready"] = (project / ".git" / "hooks").is_dir()
        return 0

    monkeypatch.setattr(cc_setup.subprocess, "run", fake_run)
    monkeypatch.setattr(cc_setup, "cmd_init", fake_cmd_init)
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(cc_setup, "_sync_host_context_file", lambda project, user_host: None)

    result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 0
    assert git_calls == [["git", "init"]]
    assert init_state["git_ready"] is True
    gateway = json.loads((tmp_path / ".controlcoding" / "gateway_config.json").read_text(encoding="utf-8"))
    assert gateway["userHost"] == "vscode"


def test_cmd_setup_chat_guide_mentions_host_hint(tmp_path, capsys):
    result = cc_setup.cmd_setup_chat_guide(tmp_path, host_hint="codex_cli")

    assert result == 0
    out = capsys.readouterr().out
    assert "official chat-guided ControlCoding installation assistant" in out
    assert "Codex CLI" in out
    assert "what ControlCoding is" in out
    assert "Use the same language as the user" in out
    assert "Ask one question at a time" in out
    assert "visible user-facing reply" in out
    assert "End each turn with exactly one explicit user-facing question" in out
    assert "treat that as enough to start" in out
    assert "Do not silently choose or apply high-impact setup choices" in out
    assert "must ask me to confirm them before writing files or running setup" in out
    assert "not permission to auto-apply all defaults" in out
    assert "Do not expose raw tool-call metadata" in out
    assert "check what is already present" in out
    assert "Do not install anything without explicit permission" in out
    assert "prefer a `venv`" in out
    assert "JSON handoff file payload" in out
    assert "--answers-file" in out
    assert "--apply-answers" in out
    assert "This flow is only for installing and configuring ControlCoding itself" in out
    assert "Project setup is a separate flow" in out
    assert "Do not make me re-enter setup answers" in out
    assert "Only if this host truly cannot execute local commands" in out
    assert "Once a choice is already clear, do not replay it as a redundant confirmation loop" in out
    assert "Do not narrate internal plumbing" in out
    assert "recommendation, not as a forced choice" in out
    assert "Do not write files or run setup/apply commands until those high-impact choices are explicitly confirmed" in out
    assert "ask me whether I want to start the separate project setup flow" in out
    assert "public manual second-opinion path" in out
    assert "current release path is explicit host-chat helper roles" in out
    assert "API-backed specialist routing belongs to the later Version II path" in out
    assert "ask for `backend_pref` explicitly" in out
    assert "Represent Configure later" in out
    assert "Never derive `backend_pref` from discovery" in out
    assert "supported backend may be selected even when it is not detected" in out


def test_setup_project_recommendation_cards_have_required_contract():
    answers = {
        "project_definition_mode": "existing_brief",
        "kickoff_mode": "partial_spec",
        "documentation_mode": "managed",
        "stack": "Python desktop app",
        "arch": "thin vertical slice with separated runtime and UI",
        "truth": "domain model",
        "view": "desktop renderer",
        "product": {
            "product_form": "desktop",
            "runtime_constraints": "local runtime",
        },
        "environment": {
            "install_permission": "denied",
        },
        "planning": {
            "planning_mode": "solo_structured_manual_consultation_ready",
        },
        "kickoff": {
            "references": "project_brief.md",
        },
    }

    cards = cc_setup._build_setup_project_recommendation_cards(
        answers,
        ["project_brief.md"],
    )

    required = {
        "field",
        "recommended_value",
        "reason",
        "alternative_value",
        "alternative_when",
        "user_prompt",
        "explanation_level",
        "recommendation_class",
    }
    assert len(cards) >= 8
    assert all(required.issubset(card) for card in cards)
    assert {card["field"] for card in cards} >= {
        "project_definition_mode",
        "selected_reference",
        "product.product_form",
        "technicalDirection.stack",
        "technicalDirection.architecture",
        "governance.planning",
    }


def test_setup_project_recommendation_examples_cover_core_paths():
    examples = cc_setup._setup_project_recommendation_examples()

    by_scenario = {example["scenario"]: example for example in examples}
    assert set(by_scenario) == {"core_greenfield", "core_brownfield"}
    for example in by_scenario.values():
        card = example["card"]
        rendered = example["rendered"]
        assert card["field"] == "project_definition_mode"
        assert card["recommended_value"]
        assert card["alternative_value"]
        assert "Question:" in rendered
        assert "confermi" not in rendered.lower()
        assert "confirm " not in rendered.lower()
    assert by_scenario["core_greenfield"]["card"]["recommended_value"] == "guided"
    assert by_scenario["core_brownfield"]["card"]["recommended_value"] == "existing_project"


def test_ask_backend_policy_mentions_chat_defined_agents_release_bias(capsys):
    result = cc_setup._ask_backend_policy("approved", tier="agents", apply_answers=True)

    assert result == "approved"
    out = capsys.readouterr().out
    assert "keep Agents explicit and chat-defined on the chosen host" in out
    assert "later Version II API-backed path" in out
    assert "do not want extra backend activation beyond the current host/manual flow" in out


def test_ask_specialist_execution_mode_marks_human_mediated_as_bridge(capsys):
    result = cc_setup._ask_specialist_execution_mode("human_mediated", apply_answers=True)

    assert result == "human_mediated"
    out = capsys.readouterr().out
    assert "Current release bias: `human_mediated` is the public `Agents` path" in out
    assert "explicit prompt, folder, and behavior contracts on the chosen host" in out
    assert "Later Version II path when API-backed specialist routing is intentionally enabled" in out


def test_collect_specialist_paths_mentions_release_split_for_local_only_agents(capsys):
    result = cc_setup._collect_specialist_paths(
        [
            {"role_id": "codewarden", "active": False},
            {"role_id": "narrator", "active": False},
            {"role_id": "consultant_1", "active": False},
            {"role_id": "consultant_2", "active": False},
            {"role_id": "consultant_3", "active": False},
        ],
        tier="agents",
        backend_policy="local_only",
        tandem_config={},
        apply_answers=True,
    )

    assert result == []
    out = capsys.readouterr().out
    assert "`Core + manual consultation` remains the public manual second-opinion path" in out
    assert "The current public `Agents` path is explicit chat-defined specialist help on the chosen host" in out
    assert "Think prompt, folder, and behavior contract, not API-backed routing" in out
    assert "keeps extra backend activation off unless you are intentionally testing local specialist experiments" in out


def test_cmd_setup_project_prefills_from_answers_file(tmp_path, monkeypatch):
    install_answers = tmp_path / "install_answers.json"
    install_answers.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "DungeonCrawler3D v14",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured_manual_consultation_ready",
                        "planning_authority": "single_author_with_manual_consultation",
                        "manual_consultation_allowed": True,
                    },
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                }
            }
        ),
        encoding="utf-8",
    )
    project_answers = tmp_path / "project_answers.json"
    project_answers.write_text(
        json.dumps(
            {
                "project_setup": {
                    "project_definition_mode": "existing_brief",
                    "kickoff_mode": "partial_spec",
                    "product": {
                        "product_form": "desktop",
                        "runtime_constraints": "local and offline",
                        "target_platforms": ["Windows"],
                        "tooling_tolerance": "medium",
                        "control_preference": "balanced"
                    },
                    "environment": {
                        "inspection_permission": "granted",
                        "install_permission": "denied",
                        "install_isolation_preference": "unspecified",
                        "venv_preference": "not_applicable"
                    },
                    "kickoff": {
                        "vision": "Vertical slice fantasy dungeon crawler.",
                        "users": "Single local player.",
                        "must_haves": "HUD; minimap; inventory",
                        "invariants": "No crash; no black screen",
                        "quality": "Stable and readable",
                        "anti_goals": "No multiplayer",
                        "references": "prompt_crawler.md",
                    },
                    "stack": "C++ + SDL3 + OpenGL",
                    "arch": "Modular desktop game slice",
                    "truth": "Game state structs",
                    "view": "HUD and camera state",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cc_setup, "_ask", lambda prompt, default="": default)
    monkeypatch.setattr(cc_setup, "_ask_yn", lambda prompt, default=True: default)
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "cmd_init", lambda project, central_hooks=False, quiet=False: 0)
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    assert cc_setup.cmd_setup(tmp_path, answers_file=install_answers) == 0
    result = cc_setup.cmd_setup_project(tmp_path, answers_file=project_answers)

    assert result == 0
    design = (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").read_text(encoding="utf-8")
    assert "Vertical slice fantasy dungeon crawler." in design
    assert "prompt_crawler.md" in design
    canonical = (tmp_path / "CONTROLCODING.md").read_text(encoding="utf-8")
    assert "C++ + SDL3 + OpenGL" in canonical
    assert "Modular desktop game slice" in canonical
    adapter = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "C++ + SDL3 + OpenGL" in adapter
    marker, state, _detail = cc_setup._adapter_marker_payload(adapter)
    assert state == "owned"
    assert marker["source"] == "CONTROLCODING.md"


def test_cmd_setup_project_chat_guide_mentions_second_flow_contract(tmp_path, capsys):
    result = cc_setup.cmd_setup_project_chat_guide(tmp_path, host_hint="codex_cli")

    assert result == 0
    out = capsys.readouterr().out
    assert "project setup assistant" in out
    assert "Assume ControlCoding is already installed" in out
    assert "not for installing ControlCoding itself" in out
    assert "project_setup" in out
    assert "Do not reinstall ControlCoding" in out
    assert "project_brief.md" in out
    assert "which one should be treated as the brief" in out
    assert "existing mature repo" in out
    assert "brownfield adoption path" in out
    assert "Recommendation card contract" in out
    assert "recommended option" in out
    assert "one real alternative" in out
    assert "Do not ask naked confirmation questions" in out
    assert "Core greenfield example" in out
    assert "Core brownfield example" in out
    assert "Recommended: `guided`" in out
    assert "Recommended: `existing_project`" in out


def test_cmd_setup_project_requires_chat_or_answers_file(tmp_path, capsys):
    (tmp_path / "CONTROLCODING.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / ".controlcoding").mkdir()
    (tmp_path / ".controlcoding" / "cc_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".controlcoding" / "gateway_config.json").write_text("{}", encoding="utf-8")

    result = cc_setup.cmd_setup_project(tmp_path)

    assert result == 1
    out = capsys.readouterr().out
    assert "Terminal questionnaire removed" in out
    assert "cc setup-project --chat-guide" in out


def test_cmd_setup_engagement_requires_chat_or_answers_file(tmp_path, capsys):
    result = cc_setup.cmd_setup_engagement(tmp_path)

    assert result == 1
    out = capsys.readouterr().out
    assert "Terminal questionnaire removed" in out
    assert "cc setup --engagement --chat-guide" in out


def test_cmd_setup_engagement_prefills_from_answers_file(tmp_path, monkeypatch):
    (tmp_path / ".controlcoding").mkdir()
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "engagement": {
                    "tier": "core",
                    "backend_policy": "approved",
                    "budget_policy": {"max_calls": 7},
                    "manual_consultation_allowed": True,
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cc_setup, "_ask", lambda prompt, default="": default)
    monkeypatch.setattr(cc_setup, "_ask_yn", lambda prompt, default=True: default)

    result = cc_setup.cmd_setup_engagement(tmp_path, answers_file=answers_file)

    assert result == 0
    engagement = json.loads((tmp_path / ".controlcoding" / "cc_engagement.json").read_text(encoding="utf-8"))
    assert engagement["tier"] == "core"
    assert engagement["backend_policy"] == "approved"
    assert engagement["budget_policy"]["max_calls"] == 7
    assert engagement["planning_mode"] == "solo_structured_manual_consultation_ready"
    assert engagement["manual_consultation_allowed"] is True


def test_cmd_setup_engagement_apply_answers_requires_explicit_specialist_matrix_for_agents(tmp_path):
    (tmp_path / ".controlcoding").mkdir()
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "engagement": {
                    "tier": "agents",
                    "backend_policy": "approved",
                    "budget_policy": {"max_calls": 5},
                    "specialist_paths": [
                        {
                            "role_id": "codewarden",
                            "label": "CodeWarden",
                            "path_type": "watchdog",
                            "active": True,
                            "backend": "anthropic_prod",
                            "model": "claude-sonnet-4-6",
                            "permission": "approval_required",
                            "max_calls": 2,
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    result = cc_setup.cmd_setup_engagement(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 0
    engagement = json.loads((tmp_path / ".controlcoding" / "cc_engagement.json").read_text(encoding="utf-8"))
    assert engagement["tier"] == "agents"
    assert engagement["specialist_paths"][0]["backend"] == "anthropic_prod"
    assert engagement["specialist_paths"][0]["execution_mode"] == "cc_routed"


def test_cmd_setup_engagement_apply_answers_rejects_agents_without_specialist_matrix(tmp_path):
    (tmp_path / ".controlcoding").mkdir()
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "engagement": {
                    "tier": "agents",
                    "backend_policy": "approved",
                }
            }
        ),
        encoding="utf-8",
    )

    result = cc_setup.cmd_setup_engagement(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 1


def test_cmd_setup_engagement_apply_answers_rejects_wrong_section_handoff(tmp_path, capsys):
    (tmp_path / ".controlcoding").mkdir()
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps({"setup": {"name": "Wrong Section"}}),
        encoding="utf-8",
    )

    result = cc_setup.cmd_setup_engagement(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 1
    out = capsys.readouterr().out
    assert "valid `engagement` payload" in out
    assert not (tmp_path / ".controlcoding" / "cc_engagement.json").exists()


def test_cmd_setup_project_apply_answers_rejects_wrong_section_handoff(tmp_path, capsys):
    (tmp_path / "CONTROLCODING.md").write_text("# Installed\n", encoding="utf-8")
    answers_file = tmp_path / "project_answers.json"
    answers_file.write_text(
        json.dumps({"setup": {"name": "Wrong Section"}}),
        encoding="utf-8",
    )

    result = cc_setup.cmd_setup_project(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 1
    out = capsys.readouterr().out
    assert "valid `project_setup` payload" in out
    assert not (tmp_path / ".controlcoding" / "setup_intent.json").exists()


def test_cmd_setup_project_apply_answers_writes_clean_context_and_overwrites_generic_docs(tmp_path, monkeypatch):
    install_answers = tmp_path / "install_answers.json"
    install_answers.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "DungeonCrawler3D v14",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured",
                        "planning_authority": "single_author",
                        "manual_consultation_allowed": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    answers_file = tmp_path / "project_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "project_setup": {
                    "kickoff_mode": "partial_spec",
                    "project_definition_mode": "existing_brief",
                    "kickoff": {
                        "vision": "Playable first-person dungeon crawler vertical slice.",
                        "users": "Single player explores, fights, loots, and orients in the dungeon.",
                        "must_haves": "3 levels; doors; traps; loot; inventory; HUD; minimap",
                        "invariants": "No crash or black screen; no walking through walls; inventory and HUD stay coherent",
                        "quality": "Stable, readable, visually coherent, and honestly verified",
                        "anti_goals": "No multiplayer; no open world; no deep RPG systems",
                        "references": "prompt_crawler.md",
                    },
                    "stack": "C++ + SDL3 + OpenGL",
                    "arch": "Native desktop game slice with separated render and gameplay modules",
                    "truth": "Authoritative game state structs and level data",
                    "view": "FPS camera plus HUD/minimap presentation state",
                    "product": {
                        "product_form": "desktop",
                        "runtime_constraints": "local, offline, single-player",
                        "target_platforms": ["Windows"],
                        "tooling_tolerance": "medium",
                        "control_preference": "low_level"
                    },
                    "environment": {
                        "inspection_permission": "granted",
                        "install_permission": "denied",
                        "install_isolation_preference": "unspecified",
                        "venv_preference": "not_applicable"
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "ROADMAP.md").write_text(
        "# Project Roadmap\n> Updated: (date)\n\nProject initialized with ControlCoding.\n",
        encoding="utf-8",
    )
    (tmp_path / "BUGS.md").write_text(
        "# Known Bugs\n> Updated: (date)\n\nNo known bugs recorded yet.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md" if user_host == "codex_cli" else "CLAUDE.md",
    )

    assert cc_setup.cmd_setup(tmp_path, answers_file=install_answers, apply_answers=True) == 0
    result = cc_setup.cmd_setup_project(tmp_path, answers_file=answers_file, apply_answers=True)

    assert result == 0
    canonical = (tmp_path / "CONTROLCODING.md").read_text(encoding="utf-8")
    design = (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").read_text(encoding="utf-8")
    roadmap = (tmp_path / "ROADMAP.md").read_text(encoding="utf-8")
    bugs = (tmp_path / "BUGS.md").read_text(encoding="utf-8")

    assert "[Your project name]" not in canonical
    assert "[e.g." not in canonical
    assert "(define your architecture rules)" not in canonical
    assert "DungeonCrawler3D v14" in canonical
    assert "Native desktop game slice with separated render and gameplay modules" in canonical
    assert "no walking through walls" in design.lower()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert "> Updated: (date)" not in roadmap
    assert "Project initialized with ControlCoding." not in roadmap
    assert "> Updated: (date)" not in bugs
    cc_config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
    assert cc_config["product"]["product_form"] == "desktop"
    assert cc_config["environment"]["inspection_permission"] == "granted"


def test_cmd_setup_project_apply_answers_refreshes_boundaries_and_seeds_fitness(tmp_path, monkeypatch):
    install_answers = tmp_path / "install_answers.json"
    install_answers.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "DungeonCrawler3D v16",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured_manual_consultation_ready",
                        "planning_authority": "single_author_with_manual_consultation",
                        "manual_consultation_allowed": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    project_answers = tmp_path / "project_answers.json"
    project_answers.write_text(
        json.dumps(
            {
                "project_setup": {
                    "project_definition_mode": "existing_brief",
                    "kickoff_mode": "partial_spec",
                    "stable": ["src/platform"],
                    "shared": ["src/renderer"],
                    "features": ["src/game"],
                    "kickoff": {
                        "vision": "Playable crawler slice.",
                        "users": "Single player.",
                        "must_haves": "movement; combat; inventory",
                        "invariants": "No crash; no walking through walls",
                        "quality": "Readable and honestly verified",
                        "anti_goals": "No multiplayer",
                        "references": "project_brief.md",
                    },
                    "stack": "C++ + OpenGL",
                    "arch": "Split gameplay, renderer, and platform modules",
                    "truth": "Authoritative gameplay state",
                    "view": "Renderer and HUD state",
                    "product": {
                        "product_form": "desktop",
                        "runtime_constraints": "local and offline",
                        "target_platforms": ["Windows"],
                        "tooling_tolerance": "medium",
                        "control_preference": "low_level"
                    },
                    "environment": {
                        "inspection_permission": "granted",
                        "install_permission": "denied",
                        "install_isolation_preference": "unspecified",
                        "venv_preference": "not_applicable"
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for relpath in ["src/game", "src/renderer", "src/platform"]:
        (tmp_path / relpath).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md" if user_host == "codex_cli" else "CLAUDE.md",
    )
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: ["src/game", "src/renderer", "src/platform"])

    assert cc_setup.cmd_setup(tmp_path, answers_file=install_answers, apply_answers=True) == 0
    assert cc_setup.cmd_setup_project(tmp_path, answers_file=project_answers, apply_answers=True) == 0

    cc_config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
    assert cc_config["module_boundaries"]["stable"] == ["src/platform"]
    assert cc_config["module_boundaries"]["shared"] == ["src/renderer"]
    assert cc_config["module_boundaries"]["features"] == ["src/game"]
    protected_paths = {entry["path"]: entry["level"] for entry in cc_config["protected_zones"]}
    assert protected_paths["src/platform/"] == "warn"
    assert protected_paths["src/renderer/"] == "warn"

    fitness = json.loads((tmp_path / "fitness.json").read_text(encoding="utf-8"))
    assert fitness["zones"]["stable"] == "src/platform"
    assert fitness["zones"]["shared"] == "src/renderer"
    assert fitness["zones"]["features"] == "src/game"
    assert fitness["import_patterns"]["cpp"] == "auto"
    assert fitness["thresholds"]["unclassified_warn_pct"] == 20
    assert any(rule["source_zone"] == "shared" for rule in fitness["layer_rules"])

    setup_intent = json.loads((tmp_path / ".controlcoding" / "setup_intent.json").read_text(encoding="utf-8"))
    assert setup_intent["schema"] == "controlcoding.setup_intent.v1"
    assert setup_intent["flow"] == "project_setup"
    assert setup_intent["project"]["definitionMode"] == "existing_brief"
    assert setup_intent["source"]["selectedReferences"] == "project_brief.md"
    assert setup_intent["product"]["product_form"] == "desktop"
    assert setup_intent["governance"]["planning"]["planning_mode"] == "solo_structured_manual_consultation_ready"
    assert setup_intent["moduleBoundaries"]["stable"] == ["src/platform"]
    assert setup_intent["moduleBoundaries"]["shared"] == ["src/renderer"]
    assert setup_intent["apply"]["fitnessConfig"] == "fitness.json"
    cards = {card["field"]: card for card in setup_intent["recommendationCards"]}
    assert cards["project_definition_mode"]["recommended_value"] == "existing_brief"
    assert cards["selected_reference"]["recommended_value"] == "project_brief.md"
    assert cards["product.product_form"]["recommended_value"] == "desktop"
    assert cards["governance.planning"]["recommended_value"] == "solo_structured_manual_consultation_ready"
    assert cards["technicalDirection.stack"]["alternative_value"] == "provisional_stack"
    assert setup_intent["recommendationGuidance"]["profile"] == "core_greenfield"
    assert {example["scenario"] for example in setup_intent["recommendationGuidance"]["examples"]} == {
        "core_greenfield",
        "core_brownfield",
    }
    phase_ids = {phase["id"]: phase["status"] for phase in setup_intent["phaseGraph"]}
    assert phase_ids["workspace_and_brief_discovery"] == "completed"
    assert phase_ids["environment_and_install_policy"] == "completed"
    assert phase_ids["product_framing"] == "completed"
    assert phase_ids["technical_strategy"] == "completed"
    assert phase_ids["controlcoding_governance"] == "completed"
    assert phase_ids["boundaries_and_protection"] == "completed"
    assert phase_ids["apply_and_validation"] == "completed"

    requirements = json.loads((tmp_path / "dev" / "contracts" / "requirements.json").read_text(encoding="utf-8"))
    mechanics = json.loads((tmp_path / "dev" / "contracts" / "core_mechanics.json").read_text(encoding="utf-8"))
    architecture_contract = json.loads((tmp_path / "dev" / "contracts" / "architecture_contract.json").read_text(encoding="utf-8"))
    verification_contract = json.loads((tmp_path / "dev" / "contracts" / "verification_contract.json").read_text(encoding="utf-8"))
    architecture_doc = (tmp_path / "dev" / "design" / "03_DSN_ArchitectureAndBoundaries_InProgress.md").read_text(encoding="utf-8")
    verification_doc = (tmp_path / "dev" / "criteria" / "03_ACC_VerificationMatrix.md").read_text(encoding="utf-8")
    coverage_doc = (tmp_path / "dev" / "criteria" / "04_ACC_CoverageLedger.md").read_text(encoding="utf-8")

    assert any(req["owner_subsystem"] == "src/game" for req in requirements if req["source_type"] == "must_haves")
    assert any(req["verification_mode"] == "machine_assisted" for req in requirements if req["category"] == "gameplay")
    assert any(req["owner_confidence"] == "medium" for req in requirements if req["owner_subsystem"] == "src/game")
    assert mechanics[0]["related_requirement_ids"]
    assert architecture_contract["subsystems"][0]["path"] == "src/platform"
    assert verification_contract["requirements"][0]["completion_gate"].startswith("Do not call this complete")
    assert verification_contract["status_model"][-1] == "blocked"
    assert "src/platform" in architecture_doc
    assert "Operational Subsystem Map" in architecture_doc
    assert "Verification Matrix" in verification_doc
    assert "Coverage Ledger" in coverage_doc


def test_cmd_setup_project_apply_answers_existing_project_creates_adoption_package(tmp_path, monkeypatch):
    install_answers = tmp_path / "install_answers.json"
    install_answers.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "Vault Existing",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured_manual_consultation_ready",
                        "planning_authority": "single_author_with_manual_consultation",
                        "manual_consultation_allowed": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    project_answers = tmp_path / "project_answers.json"
    project_answers.write_text(
        json.dumps(
            {
                "project_setup": {
                    "project_definition_mode": "existing_project",
                    "kickoff_mode": "existing_design",
                    "stable": ["src/core"],
                    "shared": ["src/ui"],
                    "features": ["src/features"],
                    "kickoff": {
                        "vision": "Adopt ControlCoding on an existing local credential vault.",
                        "users": "Single local user.",
                        "must_haves": "Preserve CRUD; search; lock state",
                        "invariants": "Locked state hides sensitive data",
                        "quality": "No false security claims",
                        "anti_goals": "No cloud sync",
                        "references": "README.md",
                    },
                    "adoption": {
                        "authoritative_docs": "README.md; docs/architecture.md",
                        "implemented_scope": "credential CRUD; search; lock/unlock",
                        "stable_areas": "src/core; src/ui",
                        "known_drift": "Architecture doc is older than README",
                        "verification_signals": "tests/; CI workflow",
                    },
                    "stack": "Python desktop app",
                    "arch": "Existing layered local app",
                    "truth": "Vault records and lock state",
                    "view": "List/detail UI",
                    "product": {
                        "product_form": "desktop",
                        "runtime_constraints": "local and offline",
                        "target_platforms": ["Windows"],
                        "tooling_tolerance": "medium",
                        "control_preference": "balanced"
                    },
                    "environment": {
                        "inspection_permission": "granted",
                        "install_permission": "denied",
                        "install_isolation_preference": "unspecified",
                        "venv_preference": "not_applicable"
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for relpath in ["src/core", "src/ui", "src/features", "docs", "tests", ".github/workflows"]:
        (tmp_path / relpath).mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text("# Vault Existing\n", encoding="utf-8")
    (tmp_path / "docs" / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='vault-existing'\n", encoding="utf-8")

    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md" if user_host == "codex_cli" else "CLAUDE.md",
    )
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: ["src/core", "src/ui", "src/features", "docs", "tests"])

    assert cc_setup.cmd_setup(tmp_path, answers_file=install_answers, apply_answers=True) == 0
    assert cc_setup.cmd_setup_project(tmp_path, answers_file=project_answers, apply_answers=True) == 0

    cc_config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
    inventory = (tmp_path / "dev" / "project-definition" / "03_DEF_ExistingProjectInventory_InProgress.md").read_text(encoding="utf-8")
    adoption_plan = (tmp_path / "dev" / "project-definition" / "07_DEF_AdoptionPlan_InProgress.md").read_text(encoding="utf-8")
    design = (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").read_text(encoding="utf-8")
    master_implementation = (tmp_path / "dev" / "implementation" / "00_IMP_MasterImplementationPlan_InProgress.md").read_text(encoding="utf-8")
    protection_contract = json.loads((tmp_path / "dev" / "contracts" / "protection_contract.json").read_text(encoding="utf-8"))
    setup_intent = json.loads((tmp_path / ".controlcoding" / "setup_intent.json").read_text(encoding="utf-8"))

    assert cc_config["project_definition_mode"] == "existing_project"
    assert setup_intent["recommendationGuidance"]["profile"] == "core_brownfield"
    assert setup_intent["recommendationCards"][0]["recommended_value"] == "existing_project"
    assert "README.md" in inventory
    assert "docs/architecture.md" in inventory
    assert "ADP-01" in adoption_plan
    assert "Existing project inventory" in design
    assert "Implementation Lifecycle" in master_implementation
    assert protection_contract["subsystem_policies"][0]["target_protection_level"] in {"deny_candidate", "warn", "advisory"}


def test_cmd_setup_answers_file_falls_back_to_recommended_when_custom_notes_are_empty(tmp_path, monkeypatch):
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "Demo Project",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "custom",
                    "host_custom_notes": [],
                    "hooks_location": "local",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured",
                        "planning_authority": "single_author",
                        "manual_consultation_allowed": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "cmd_init", lambda project, central_hooks=False, quiet=False: 0)
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_detect_obvious_environment_inventory",
        lambda: {"toolchains": ["Python"], "package_managers": ["pip"]},
    )

    result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file)

    assert result == 0
    gateway = json.loads((tmp_path / ".controlcoding" / "gateway_config.json").read_text(encoding="utf-8"))
    assert gateway["hostInstructions"]["mode"] == "recommended"
    assert gateway["hostInstructions"]["customNotes"] == []


def test_cmd_setup_initializes_memory_with_governed_scope_only(tmp_path, monkeypatch):
    answers_file = tmp_path / "setup_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "setup": {
                    "name": "Memory Default",
                    "documentation_mode": "managed",
                    "user_host": "codex_cli",
                    "host_instruction_mode": "recommended",
                    "hooks_location": "local",
                    "memory_default_policy": "governed_scope",
                    "planning": {
                        "tier": "core",
                        "planning_mode": "solo_structured",
                        "planning_authority": "single_author",
                        "manual_consultation_allowed": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('outside governed')\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (tmp_path / "docs" / "scan.pdf").write_bytes(b"%PDF-1.4\n%% scanned fixture\n")
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "architecture.md").write_text("# Architecture\n", encoding="utf-8")

    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", lambda project, host_profile: True)
    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md" if user_host == "codex_cli" else "CLAUDE.md",
    )

    assert cc_setup.cmd_setup(tmp_path, answers_file=answers_file, apply_answers=True) == 0

    cc_config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
    receipt = json.loads((tmp_path / ".controlcoding" / "memory_bootstrap_receipt.json").read_text(encoding="utf-8"))
    assert cc_config["memory_default_policy"] == "governed_scope"
    assert (tmp_path / ".controlcoding" / "memory" / "memory.db").exists()
    assert receipt["status"] == "completed"
    assert receipt["scanScope"] == "governed"
    assert receipt["fullRepoScan"] is False
    assert receipt["ocrRun"] is False
    assert receipt["fileMoves"] is False
    assert receipt["graphPromotion"] is False

    paths = _memory_paths(tmp_path)
    assert "CONTROLCODING.md" in paths
    assert "design/architecture.md" in paths
    assert "src/app.py" not in paths
    assert "docs/requirements.md" not in paths
    assert "docs/scan.pdf" not in paths
    assert not (tmp_path / "docs" / "scan.pdf.ocr.json").exists()


def test_cmd_setup_project_discovers_brief_read_only_without_moving_it(tmp_path, monkeypatch):
    (tmp_path / ".controlcoding").mkdir()
    (tmp_path / ".controlcoding" / "cc_config.json").write_text(
        json.dumps(
            {
                "documentation_mode": "managed",
                "cc_artifact_mode": "local_only",
                "hooks_location": "local",
                "memory_default_policy": "governed_scope",
                "planning": {
                    "tier": "core",
                    "planning_mode": "solo_structured",
                    "planning_authority": "single_author",
                    "manual_consultation_allowed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
        json.dumps({"userHost": "codex_cli"}),
        encoding="utf-8",
    )
    (tmp_path / "CONTROLCODING.md").write_text("# Demo\n", encoding="utf-8")
    brief = tmp_path / "project_brief.md"
    original = "# Demo Brief\n\nBuild the thing.\n"
    brief.write_text(original, encoding="utf-8")
    answers_file = tmp_path / "project_answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "project_setup": {
                    "project_definition_mode": "existing_brief",
                    "kickoff_mode": "partial_spec",
                    "kickoff": {
                        "vision": "Build the thing.",
                        "users": "Operators.",
                        "must_haves": "One useful flow",
                        "invariants": "No data loss",
                        "quality": "Readable and verified",
                        "anti_goals": "No broad rewrite",
                        "references": "project_brief.md",
                    },
                    "stack": "Python",
                    "arch": "Layered CLI",
                    "environment": {
                        "inspection_permission": "denied",
                        "install_permission": "denied",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
    monkeypatch.setattr(
        cc_setup,
        "_sync_host_context_file",
        lambda project, user_host: "AGENTS.md",
    )

    assert cc_setup.cmd_setup_project(tmp_path, answers_file=answers_file, apply_answers=True) == 0

    assert brief.exists()
    assert brief.read_text(encoding="utf-8") == original
    assert not (tmp_path / "dev" / "project-definition" / "project_brief.md").exists()
    setup_intent = json.loads((tmp_path / ".controlcoding" / "setup_intent.json").read_text(encoding="utf-8"))
    assert setup_intent["source"]["briefCandidates"] == ["project_brief.md"]
    assert setup_intent["source"]["selectedReferences"] == "project_brief.md"



_F04_MARKER_VALUES = {
    "owner": "ControlCoding",
    "schema": "controlcoding.host-adapter-ownership",
    "version": 1,
    "target": "AGENTS.md",
    "host": "codex_cli",
    "source": "CONTROLCODING.md",
    "format": "host-context-section-export-v1",
}


def _f04_source_text() -> str:
    return (
        "# CONTROLCODING.md\n\n"
        "## Project Identity\n\n- **Name**: Slice 1 fixture\n\n"
        "## Architecture Rules\n\n1. Keep adapter writes managed.\n\n"
        "## Module Boundaries\n\n- `scripts/` - CLI.\n\n"
        "## Domain Invariants\n\n1. Fail closed.\n\n"
        "## Protected Zones\n\n- None.\n\n"
        "## Session Start Ritual\n\n1. Load context.\n\n"
        "## Operative Rules\n\n- Verify first.\n\n"
        "## Current Focus\n\n- Slice 1.\n"
    )


def _f04_write_source(project: Path) -> Path:
    source = project / "CONTROLCODING.md"
    source.write_text(_f04_source_text(), encoding="utf-8")
    return source


def _f04_raw_marker(duplicate_field: str,
                    duplicate_value,
                    *,
                    first_values: dict | None = None) -> str:
    values = dict(_F04_MARKER_VALUES)
    if first_values:
        values.update(first_values)
    pairs: list[tuple[str, object]] = []
    for key, value in values.items():
        pairs.append((key, value))
        if key == duplicate_field:
            pairs.append((key, duplicate_value))
    encoded = "{" + ",".join(
        json.dumps(key) + ":" + json.dumps(value)
        for key, value in pairs
    ) + "}"
    return (
        "# AGENTS.md\n\n"
        f"<!-- controlcoding-managed: {encoded} -->\n\n"
        "managed payload\n"
    )


def _f04_assert_no_batch_outputs(project: Path) -> None:
    for relative in (
        "CLAUDE.md",
        "GEMINI.md",
        ".clinerules",
        ".cursor/rules/project.mdc",
        ".windsurfrules",
    ):
        assert not (project / relative).exists()


def _f04_replace_with_same_bytes_and_mode(path: Path) -> tuple[int, int, int]:
    content = path.read_bytes()
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    previous_key = cc._transaction_file_object_key(os.lstat(path))
    replacement = path.with_name(path.name + ".identity-swap")
    replacement.write_bytes(content)
    replacement.chmod(mode)
    os.replace(replacement, path)
    assert cc._transaction_file_object_key(os.lstat(path)) != previous_key
    return previous_key


def test_f04_m01_marker_duplicates_same_value_block_entire_set(tmp_path):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    for field in ("version", "owner", "schema", "target", "host", "source", "format"):
        raw = _f04_raw_marker(field, _F04_MARKER_VALUES[field])
        target.write_text(raw, encoding="utf-8")
        marker, state, detail = cc._adapter_marker_payload(raw)
        assert marker is None
        assert state == "ambiguous"
        assert "duplicate decoded JSON member" in detail
        assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
        assert target.read_text(encoding="utf-8") == raw
        _f04_assert_no_batch_outputs(tmp_path)

    malformed_front_matter = (
        "---\ntitle: never closed\n"
        + _f04_raw_marker("version", _F04_MARKER_VALUES["version"])
    )
    target.write_text(malformed_front_matter, encoding="utf-8")
    plan = cc._adapter_plan_payload(tmp_path, all_hosts=True, operation="sync")
    assert plan["entries"][1]["state"] == "ambiguous"
    assert plan["preflightOk"] is False
    assert target.read_text(encoding="utf-8") == malformed_front_matter


def test_f04_m02_marker_duplicates_conflicting_value_block_entire_set(tmp_path):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    for field in ("version", "owner", "schema", "target", "host", "source", "format"):
        raw = _f04_raw_marker(field, f"conflicting-{field}")
        target.write_text(raw, encoding="utf-8")
        marker, state, _detail = cc._adapter_marker_payload(raw)
        assert marker is None
        assert state == "ambiguous"
        assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
        assert target.read_text(encoding="utf-8") == raw
        _f04_assert_no_batch_outputs(tmp_path)


def test_f04_m03_escape_unknown_and_deep_duplicates_precede_schema_validation():
    required_tail = (
        ',"schema":"controlcoding.host-adapter-ownership","version":1,'
        '"target":"AGENTS.md","host":"codex_cli",'
        '"source":"CONTROLCODING.md","format":"host-context-section-export-v1"}'
    )
    fixtures = [
        '{"owner":"ControlCoding","\\u006fwner":"ControlCoding"' + required_tail,
        '{"owner":"ControlCoding","unknown":1,"unknown":1' + required_tail,
        '{"owner":"ControlCoding","extra":{"deep":1,"deep":2}' + required_tail,
        '{"owner":"ControlCoding","extra":[{"deep":1,"deep":2}]' + required_tail,
        '{"owner":"ControlCoding","unknown":1,"unknown":2,"version":1,'
        '"target":"AGENTS.md","host":"codex_cli",'
        '"source":"CONTROLCODING.md","format":"host-context-section-export-v1"}',
    ]
    for encoded in fixtures:
        marker, state, detail = cc._adapter_marker_payload(
            f"<!-- controlcoding-managed: {encoded} -->\n"
        )
        assert marker is None
        assert state == "ambiguous"
        assert "duplicate decoded JSON member" in detail


def test_f04_m04_ambiguous_marker_cannot_be_forced_or_adopted(tmp_path):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    raw = _f04_raw_marker("owner", "ControlCoding")
    target.write_text(raw, encoding="utf-8")

    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
    ) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=True,
    ) == 1
    assert target.read_text(encoding="utf-8") == raw


def test_f04_m05_unique_markers_keep_legacy_classification_and_noop_identity(tmp_path):
    marker = dict(_F04_MARKER_VALUES)
    marker["extra"] = [{"same": 1}, {"same": 2}]
    raw = (
        "# AGENTS.md\n\n<!-- controlcoding-managed: "
        + json.dumps(marker, sort_keys=True, separators=(",", ":"))
        + " -->\n\n"
    )
    parsed, state, _detail = cc._adapter_marker_payload(raw)
    assert state == "owned"
    assert parsed["extra"] == [{"same": 1}, {"same": 2}]

    _f04_write_source(tmp_path)
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
    target = tmp_path / "AGENTS.md"
    before_bytes = target.read_bytes()
    before_key = cc._transaction_file_object_key(os.lstat(target))
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
    assert target.read_bytes() == before_bytes
    assert cc._transaction_file_object_key(os.lstat(target)) == before_key


def test_f04_i01_source_identity_swap_fails_at_global_precommit(tmp_path, monkeypatch):
    source = _f04_write_source(tmp_path)
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
    original_checkpoint = cc._transaction_checkpoint
    injected = {"done": False}

    def swap_source(name, **context):
        if name == "global_precommit" and not injected["done"]:
            injected["done"] = True
            _f04_replace_with_same_bytes_and_mode(source)
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", swap_source)
    result = cc._apply_text_transaction(tmp_path, plan["entries"])

    assert result["ok"] is False
    assert result["attempted"] is False
    assert not (tmp_path / "AGENTS.md").exists()


def test_f04_i02_present_and_absent_target_swaps_preserve_substitutes(tmp_path, monkeypatch):
    present = tmp_path / "present.txt"
    present.write_text("original\n", encoding="utf-8")
    present_entry = cc._text_write_entry(tmp_path, present, "replacement\n")
    present_original_key = cc._transaction_file_object_key(os.lstat(present))
    original_checkpoint = cc._transaction_checkpoint
    injected = {"done": False, "substituteKey": None}

    def swap_present(name, **context):
        if name == "global_precommit" and not injected["done"]:
            injected["done"] = True
            _f04_replace_with_same_bytes_and_mode(present)
            injected["substituteKey"] = cc._transaction_file_object_key(os.lstat(present))
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", swap_present)
    present_result = cc._apply_text_transaction(tmp_path, [present_entry])
    assert present_result["ok"] is False
    assert present.read_text(encoding="utf-8") == "original\n"
    assert cc._transaction_file_object_key(os.lstat(present)) == injected["substituteKey"]
    assert injected["substituteKey"] != present_original_key

    absent = tmp_path / "absent.txt"
    absent_entry = cc._text_write_entry(tmp_path, absent, "planned\n")
    injected["done"] = False

    def create_absent_substitute(name, **context):
        if name == "before_replace" and not injected["done"]:
            injected["done"] = True
            absent.write_text("foreign substitute\n", encoding="utf-8")
            injected["substituteKey"] = cc._transaction_file_object_key(os.lstat(absent))
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", create_absent_substitute)
    absent_result = cc._apply_text_transaction(tmp_path, [absent_entry])
    assert absent_result["ok"] is False
    assert absent.read_text(encoding="utf-8") == "foreign substitute\n"
    assert cc._transaction_file_object_key(os.lstat(absent)) == injected["substituteKey"]


def test_f04_i03_stage_and_post_install_identity_swaps_are_detected(tmp_path, monkeypatch):
    first = tmp_path / "first.txt"
    first.write_text("original\n", encoding="utf-8")
    first_key = cc._transaction_file_object_key(os.lstat(first))
    first_entry = cc._text_write_entry(tmp_path, first, "installed\n")
    original_checkpoint = cc._transaction_checkpoint
    injected = {"done": False}

    def swap_stage(name, **context):
        if name == "before_replace" and not injected["done"]:
            injected["done"] = True
            _f04_replace_with_same_bytes_and_mode(Path(context["source"]))
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", swap_stage)
    stage_result = cc._apply_text_transaction(tmp_path, [first_entry])
    assert stage_result["ok"] is False
    assert stage_result["attempted"] is False
    assert first.read_text(encoding="utf-8") == "original\n"
    assert cc._transaction_file_object_key(os.lstat(first)) == first_key

    second = tmp_path / "second.txt"
    second.write_text("second original\n", encoding="utf-8")
    second_entry = cc._text_write_entry(tmp_path, second, "second installed\n")
    injected.update({"done": False, "installedKey": None})

    def swap_installed(name, **context):
        if name == "after_replace" and not injected["done"]:
            injected["done"] = True
            _f04_replace_with_same_bytes_and_mode(Path(context["destination"]))
            injected["installedKey"] = cc._transaction_file_object_key(
                os.lstat(context["destination"])
            )
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", swap_installed)
    installed_result = cc._apply_text_transaction(tmp_path, [second_entry])
    assert installed_result["ok"] is False
    assert installed_result["rollbackOk"] is False
    assert second.read_text(encoding="utf-8") == "second installed\n"
    assert cc._transaction_file_object_key(os.lstat(second)) == injected["installedKey"]


def test_f04_i04_rollback_identity_loss_preserves_intruder_and_recovery_backup(
    tmp_path,
    monkeypatch,
):
    targets = [tmp_path / name for name in ("first.txt", "second.txt", "third.txt")]
    for index, target in enumerate(targets, start=1):
        target.write_text(f"original {index}\n", encoding="utf-8")
    entries = [
        cc._text_write_entry(tmp_path, target, f"installed {index}\n")
        for index, target in enumerate(targets, start=1)
    ]
    original_checkpoint = cc._transaction_checkpoint
    injected = {"failed": False, "swapped": False}

    def fail_and_swap(name, **context):
        if (
            name == "before_replace"
            and Path(context["destination"]) == targets[2]
            and not injected["failed"]
        ):
            injected["failed"] = True
            raise OSError("injected third replace failure")
        if (
            name == "before_rollback"
            and Path(context["target"]) == targets[1]
            and not injected["swapped"]
        ):
            injected["swapped"] = True
            _f04_replace_with_same_bytes_and_mode(targets[1])
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_and_swap)
    result = cc._apply_text_transaction(tmp_path, entries)

    assert result["ok"] is False
    assert result["rollbackOk"] is False
    assert "rollback incomplete" in result["error"]
    assert targets[0].read_text(encoding="utf-8") == "original 1\n"
    assert targets[1].read_text(encoding="utf-8") == "installed 2\n"
    assert targets[2].read_text(encoding="utf-8") == "original 3\n"
    assert list(tmp_path.glob(".second.txt.controlcoding.*.backup"))


def test_f04_i05_cleanup_identity_loss_preserves_foreign_file_and_directory(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "cleanup.txt"
    target.write_text("original\n", encoding="utf-8")
    entry = cc._text_write_entry(tmp_path, target, "installed\n")
    original_checkpoint = cc._transaction_checkpoint
    injected = {"cleanup": False, "cleanupPath": None, "cleanupKey": None}

    def fail_then_swap_cleanup(name, **context):
        if name == "global_precommit":
            raise OSError("injected precommit failure")
        if name == "before_cleanup" and not injected["cleanup"]:
            injected["cleanup"] = True
            cleanup_path = Path(context["path"])
            _f04_replace_with_same_bytes_and_mode(cleanup_path)
            injected["cleanupPath"] = cleanup_path
            injected["cleanupKey"] = cc._transaction_file_object_key(
                os.lstat(cleanup_path)
            )
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_then_swap_cleanup)
    file_result = cc._apply_text_transaction(tmp_path, [entry])
    assert file_result["ok"] is False
    assert file_result["cleanupOk"] is False
    assert list(tmp_path.glob(".cleanup.txt.controlcoding.*.stage"))
    assert cc._transaction_file_object_key(
        os.lstat(injected["cleanupPath"])
    ) == injected["cleanupKey"]

    nested_target = tmp_path / "created-parent" / "child.txt"
    nested_entry = cc._text_write_entry(tmp_path, nested_target, "child\n")
    injected = {"directory": False, "directoryKey": None}

    def fail_then_swap_directory(name, **context):
        if name == "before_prepare_entry":
            raise OSError("injected preparation failure")
        if name == "before_directory_cleanup" and not injected["directory"]:
            injected["directory"] = True
            directory = Path(context["path"])
            retired = tmp_path / "retired-created-parent"
            directory.rename(retired)
            directory.mkdir()
            injected["directoryKey"] = cc._transaction_file_object_key(
                os.lstat(directory)
            )
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_then_swap_directory)
    directory_result = cc._apply_text_transaction(tmp_path, [nested_entry])
    assert directory_result["ok"] is False
    assert directory_result["cleanupOk"] is False
    assert (tmp_path / "created-parent").is_dir()
    assert cc._transaction_file_object_key(
        os.lstat(tmp_path / "created-parent")
    ) == injected["directoryKey"]


def test_f04_i06_missing_or_zero_inode_blocks_before_mutation(tmp_path, monkeypatch):
    source = _f04_write_source(tmp_path)
    original_lstat = cc.os.lstat

    def zero_inode(path):
        result = original_lstat(path)
        if Path(path) != source:
            return result
        return SimpleNamespace(
            st_dev=result.st_dev,
            st_ino=0,
            st_mode=result.st_mode,
            st_size=result.st_size,
            st_ctime=result.st_ctime,
            st_mtime=result.st_mtime,
            st_ctime_ns=getattr(result, "st_ctime_ns", None),
            st_mtime_ns=getattr(result, "st_mtime_ns", None),
            st_file_attributes=getattr(result, "st_file_attributes", 0),
        )

    monkeypatch.setattr(cc.os, "lstat", zero_inode)
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
    assert plan["preflightOk"] is False
    assert not (tmp_path / "AGENTS.md").exists()

    for fake in (
        SimpleNamespace(st_dev=1, st_ino=0, st_mode=stat.S_IFREG),
        SimpleNamespace(st_dev=1, st_mode=stat.S_IFREG),
    ):
        try:
            cc._filesystem_object_key(fake)
        except OSError as exc:
            assert "identity is unavailable" in str(exc)
        else:
            raise AssertionError("identity-unavailable stat was accepted")


def test_f04_i07_parent_drift_blocks_mkdir_stage_backup_and_marker_preparation(
    tmp_path,
    monkeypatch,
):
    original_checkpoint = cc._transaction_checkpoint
    original_revalidate = cc._revalidate_transaction_parents
    drift = {"active": False, "boundary": ""}

    def inject_parent_drift(name, **context):
        if (
            name == "before_parent_create"
            or name == "before_stage_create" and context.get("suffix") == drift["boundary"]
        ):
            drift["active"] = True
        return original_checkpoint(name, **context)

    def reject_drift(project, target, expected):
        if drift["active"]:
            raise RuntimeError("injected parent object_key drift")
        return original_revalidate(project, target, expected)

    monkeypatch.setattr(cc, "_transaction_checkpoint", inject_parent_drift)
    monkeypatch.setattr(cc, "_revalidate_transaction_parents", reject_drift)

    nested = tmp_path / "missing-parent" / "target.txt"
    nested_result = cc._apply_text_transaction(
        tmp_path,
        [cc._text_write_entry(tmp_path, nested, "payload\n")],
    )
    assert nested_result["ok"] is False
    assert not (tmp_path / "missing-parent").exists()

    for suffix, existing in ((".stage", False), (".backup", True)):
        drift.update({"active": False, "boundary": suffix})
        case_root = tmp_path / suffix.lstrip(".")
        case_root.mkdir()
        target = case_root / "target.txt"
        if existing:
            target.write_text("original\n", encoding="utf-8")
        entry = cc._text_write_entry(tmp_path, target, "payload\n")
        result = cc._apply_text_transaction(tmp_path, [entry])
        assert result["ok"] is False
        if existing:
            assert target.read_text(encoding="utf-8") == "original\n"
        else:
            assert not target.exists()

    adapter_root = tmp_path / "adapter"
    adapter_root.mkdir()
    drift.update({"active": False, "boundary": ""})
    _f04_write_source(adapter_root)
    adapter_plan = cc._adapter_plan_payload(adapter_root, host="codex_cli")
    drift.update({"active": False, "boundary": ".stage"})
    adapter_result = cc._apply_text_transaction(adapter_root, adapter_plan["entries"])
    assert adapter_result["ok"] is False
    assert not (adapter_root / "AGENTS.md").exists()


def test_f04_i08_content_or_mode_drift_with_same_object_key_is_rejected(
    tmp_path,
    monkeypatch,
):
    source = _f04_write_source(tmp_path)
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
    original_checkpoint = cc._transaction_checkpoint
    injected = {"done": False, "intruderKey": None}

    def mutate_source_in_place(name, **context):
        if name == "global_precommit" and not injected["done"]:
            injected["done"] = True
            before_key = cc._transaction_file_object_key(os.lstat(source))
            source.write_text(_f04_source_text() + "\nchanged in place\n", encoding="utf-8")
            assert cc._transaction_file_object_key(os.lstat(source)) == before_key
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", mutate_source_in_place)
    result = cc._apply_text_transaction(tmp_path, plan["entries"])
    assert result["ok"] is False
    assert not (tmp_path / "AGENTS.md").exists()

    same_key = (1, 2, stat.S_IFREG)
    expected = {"state": "present", "objectKey": same_key, "hash": "a", "mode": 0o640}
    content_drift = {"state": "present", "objectKey": same_key, "hash": "b", "mode": 0o640}
    mode_drift = {"state": "present", "objectKey": same_key, "hash": "a", "mode": 0o600}
    assert cc._transaction_snapshots_match(expected, content_drift) is False
    assert cc._transaction_snapshots_match(expected, mode_drift) is False

    mode_root = tmp_path / "mode-transaction"
    mode_root.mkdir()
    mode_source = _f04_write_source(mode_root)
    mode_plan = cc._adapter_plan_payload(mode_root, host="codex_cli")
    original_snapshot = cc._identity_safe_file_snapshot

    def inject_mode_drift(project, path, purpose, expected_parents=None):
        snapshot = original_snapshot(
            project,
            path,
            purpose,
            expected_parents=expected_parents,
        )
        if path == mode_source and expected_parents is not None:
            snapshot["mode"] = int(snapshot["mode"]) ^ stat.S_IWUSR
        return snapshot

    monkeypatch.setattr(cc, "_identity_safe_file_snapshot", inject_mode_drift)
    mode_result = cc._apply_adapter_transaction(mode_root, mode_plan["entries"])
    assert mode_result["ok"] is False
    assert mode_result["attempted"] is False
    assert not (mode_root / "AGENTS.md").exists()


def test_f04_i09_complete_reverse_rollback_restores_two_installed_actions(
    tmp_path,
    monkeypatch,
):
    targets = [tmp_path / name for name in ("one.txt", "two.txt", "three.txt")]
    for index, target in enumerate(targets, start=1):
        target.write_text(f"original {index}\n", encoding="utf-8")
    entries = [
        cc._text_write_entry(tmp_path, target, f"installed {index}\n")
        for index, target in enumerate(targets, start=1)
    ]
    original_checkpoint = cc._transaction_checkpoint
    original_replace = cc.os.replace
    rollback_order: list[Path] = []

    def fail_third(name, **context):
        if name == "before_replace" and Path(context["destination"]) == targets[2]:
            raise OSError("injected third replace failure")
        return original_checkpoint(name, **context)

    def record_replace(source, destination):
        if str(source).endswith(".backup"):
            rollback_order.append(Path(destination))
        return original_replace(source, destination)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_third)
    monkeypatch.setattr(cc.os, "replace", record_replace)
    result = cc._apply_text_transaction(tmp_path, entries)

    assert result["ok"] is False
    assert result["rollbackOk"] is True
    assert result["rolledBack"] is True
    assert rollback_order == [targets[1], targets[0]]
    for index, target in enumerate(targets, start=1):
        assert target.read_text(encoding="utf-8") == f"original {index}\n"
    assert not list(tmp_path.glob(".*.controlcoding.*.stage"))
    assert not list(tmp_path.glob(".*.controlcoding.*.backup"))


def test_f04_i10_payload_marker_unit_rolls_back_or_reports_partial_identity_loss(
    tmp_path,
    monkeypatch,
):
    complete_root = tmp_path / "complete"
    complete_root.mkdir()
    _f04_write_source(complete_root)
    complete_plan = cc._adapter_plan_payload(complete_root, host="codex_cli")
    original_checkpoint = cc._transaction_checkpoint

    def fail_after_install(name, **context):
        if name == "after_replace":
            raise OSError("injected failure between adapter actions")
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_after_install)
    complete_result = cc._apply_text_transaction(complete_root, complete_plan["entries"])
    assert complete_result["ok"] is False
    assert complete_result["rollbackOk"] is True
    assert not (complete_root / "AGENTS.md").exists()

    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    _f04_write_source(partial_root)
    partial_plan = cc._adapter_plan_payload(partial_root, host="codex_cli")
    injected = {"done": False}

    def swap_after_install(name, **context):
        if name == "after_replace" and not injected["done"]:
            injected["done"] = True
            _f04_replace_with_same_bytes_and_mode(Path(context["destination"]))
            injected["intruderKey"] = cc._transaction_file_object_key(
                os.lstat(context["destination"])
            )
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", swap_after_install)
    partial_result = cc._apply_text_transaction(partial_root, partial_plan["entries"])
    assert partial_result["ok"] is False
    assert partial_result["rollbackOk"] is False
    assert "rollback incomplete" in partial_result["error"]
    marker, state, _detail = cc._adapter_marker_payload(
        (partial_root / "AGENTS.md").read_text(encoding="utf-8")
    )
    assert state == "owned"
    assert marker["target"] == "AGENTS.md"
    assert cc._transaction_file_object_key(
        os.lstat(partial_root / "AGENTS.md")
    ) == injected["intruderKey"]


def test_f04_r01_simulated_reparse_leaf_and_parents_fail_closed(tmp_path, monkeypatch):
    source = _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    target.write_text("foreign\n", encoding="utf-8")
    cursor_parent = tmp_path / ".cursor"
    cursor_rules = cursor_parent / "rules"
    cursor_rules.mkdir(parents=True)
    original_lstat = cc.os.lstat
    reparse_paths = {target}
    monkeypatch.setattr(cc.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)

    def simulated_reparse(path):
        result = original_lstat(path)
        if Path(path) not in reparse_paths:
            return result
        return SimpleNamespace(
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_mode=result.st_mode,
            st_size=result.st_size,
            st_ctime=result.st_ctime,
            st_mtime=result.st_mtime,
            st_ctime_ns=getattr(result, "st_ctime_ns", None),
            st_mtime_ns=getattr(result, "st_mtime_ns", None),
            st_file_attributes=0x400,
        )

    monkeypatch.setattr(cc.os, "lstat", simulated_reparse)
    leaf_plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
    assert leaf_plan["preflightOk"] is False
    assert target.read_text(encoding="utf-8") == "foreign\n"

    reparse_paths.clear()
    reparse_paths.add(cursor_rules)
    immediate_plan = cc._adapter_plan_payload(tmp_path, host="cursor")
    assert immediate_plan["preflightOk"] is False

    reparse_paths.clear()
    reparse_paths.add(cursor_parent)
    ancestor_plan = cc._adapter_plan_payload(tmp_path, host="cursor")
    assert ancestor_plan["preflightOk"] is False

    reparse_paths.clear()
    later_plan = cc._adapter_plan_payload(tmp_path, host="gemini_cli")
    original_checkpoint = cc._transaction_checkpoint

    def inject_later_reparse(name, **context):
        if name == "before_stage_create":
            reparse_paths.add(tmp_path)
        return original_checkpoint(name, **context)

    monkeypatch.setattr(cc, "_transaction_checkpoint", inject_later_reparse)
    later_result = cc._apply_text_transaction(tmp_path, later_plan["entries"])
    assert later_result["ok"] is False
    assert not (tmp_path / "GEMINI.md").exists()
    assert source.exists()


def test_f04_a01_adoption_target_absent_is_noop(tmp_path):
    _f04_write_source(tmp_path)
    assert cc.cmd_context_adopt(tmp_path, host="codex_cli") == 0
    assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 0
    assert not (tmp_path / "AGENTS.md").exists()


def test_f04_a02_adoption_already_owned_is_idempotent(tmp_path):
    _f04_write_source(tmp_path)
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
    target = tmp_path / "AGENTS.md"
    before_bytes = target.read_bytes()
    before_key = cc._transaction_file_object_key(os.lstat(target))

    assert cc.cmd_context_adopt(tmp_path, host="codex_cli") == 0
    assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 0
    assert target.read_bytes() == before_bytes
    assert cc._transaction_file_object_key(os.lstat(target)) == before_key


def test_f04_a03_adoption_invalid_or_ambiguous_never_writes(tmp_path):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    fixtures = (
        "<!-- controlcoding-managed: not-json -->\ninvalid\n",
        _f04_raw_marker("schema", _F04_MARKER_VALUES["schema"]),
    )
    for raw in fixtures:
        target.write_text(raw, encoding="utf-8")
        assert cc.cmd_context_adopt(tmp_path, host="codex_cli") == 1
        assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 1
        assert cc.cmd_export_host_context(
            tmp_path,
            host="codex_cli",
            force=True,
        ) == 1
        assert target.read_text(encoding="utf-8") == raw


def test_f04_a04_foreign_adoption_preserves_payload_mode_and_is_never_implicit(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    payload = (
        b"# AGENTS.md\n\n"
        b"<!-- managed-by: OtherTool -->\n"
        b"historical foreign adapter\n"
    )
    target.write_bytes(payload)
    target.chmod(0o640)
    original_mode = stat.S_IMODE(os.lstat(target).st_mode)
    original_key = cc._transaction_file_object_key(os.lstat(target))

    assert cc.cmd_context_adopt(tmp_path, host="codex_cli") == 0
    assert target.read_bytes() == payload
    assert cc._transaction_file_object_key(os.lstat(target)) == original_key
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
    ) == 1

    gateway_dir = tmp_path / ".controlcoding"
    gateway_dir.mkdir()
    gateway = gateway_dir / "gateway_config.json"
    gateway.write_text('{"userHost":"claude_code"}\n', encoding="utf-8")
    monkeypatch.setattr(cc, "_write_json_atomic", lambda *args, **kwargs: None)
    monkeypatch.setattr(cc, "_write_host_integration_assets", lambda *args, **kwargs: [])
    assert cc.cmd_host_switch(tmp_path, "codex_cli") == 1
    assert target.read_bytes() == payload

    assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 0
    adopted = target.read_bytes()
    assert b"<!-- managed-by: OtherTool -->" in adopted
    assert adopted.endswith(b"historical foreign adapter\n")
    assert b"controlcoding-managed" in adopted
    assert stat.S_IMODE(os.lstat(target).st_mode) == original_mode


def _f04_marker_line(managed_format: str = "host-context-section-export-v1") -> str:
    values = dict(_F04_MARKER_VALUES)
    values["format"] = managed_format
    return (
        "<!-- controlcoding-managed: "
        + json.dumps(values, sort_keys=True, separators=(",", ":"))
        + " -->"
    )


def _f04_prepare_normative_state(project: Path, state: str) -> Path:
    _f04_write_source(project)
    target = project / "AGENTS.md"
    expected = cc._adapter_plan_payload(
        project,
        host="codex_cli",
        operation="sync",
    )["entries"][0]["_expectedContent"]
    if state == "target_absent":
        return target
    if state == "owned_current":
        target.write_bytes(expected.encode("utf-8"))
    elif state == "owned_stale":
        target.write_text(expected + "stale payload\n", encoding="utf-8")
    elif state == "unmarked":
        target.write_text("historical unmarked adapter\n", encoding="utf-8")
    elif state == "foreign":
        target.write_text(
            "# AGENTS.md\n\n<!-- managed-by: OtherTool -->\nforeign payload\n",
            encoding="utf-8",
        )
    elif state == "invalid":
        target.write_text(_f04_marker_line() + "\npayload\n", encoding="utf-8")
    elif state == "ambiguous":
        target.write_text(expected + _f04_marker_line() + "\n", encoding="utf-8")
    elif state == "unreadable_or_unsafe":
        target.mkdir()
    else:
        raise AssertionError(f"unsupported fixture state: {state}")
    return target


def test_f04_b01_marker_is_owned_only_at_each_format_specific_managed_header():
    section_marker = _f04_marker_line("host-context-section-export-v1")
    portable_marker = _f04_marker_line("agents-md-portable-v1")
    full_marker = _f04_marker_line("host-context-full-copy-v1")
    fixtures = {
        "host-context-section-export-v1": f"# AGENTS.md\n\n{section_marker}\n\npayload\n",
        "agents-md-portable-v1": f"# AGENTS.md\n\n{portable_marker}\n\npayload\n",
        "host-context-full-copy-v1": f"{full_marker}\n\npayload\n",
    }
    for managed_format, content in fixtures.items():
        marker, state, _detail = cc._adapter_marker_payload(content)
        assert state == "owned"
        assert marker["format"] == managed_format


@pytest.mark.parametrize(
    "content",
    [
        "# AGENTS.md\n\npayload\n" + _f04_marker_line() + "\n",
        "# AGENTS.md\n\n```html\n" + _f04_marker_line() + "\n```\n",
        "---\ntitle: fixture\n---\n# AGENTS.md\n\n" + _f04_marker_line() + "\n",
    ],
)
def test_f04_b02_single_valid_marker_outside_managed_header_is_invalid(tmp_path, content):
    marker, state, detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "invalid"
    assert "managed header" in detail
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    target.write_text(content, encoding="utf-8")
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="adopt")
    assert plan["entries"][0]["state"] == "invalid"
    assert plan["entries"][0]["ownership"] == "invalid"
    assert plan["entries"][0]["action"] == "block"
    assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 1
    assert target.read_text(encoding="utf-8") == content


def test_f04_b03_front_matter_marker_position_and_competing_markers():
    marker_line = _f04_marker_line()
    valid = f"---\ntitle: fixture\n---\n{marker_line}\n\n# Payload\n"
    marker, state, _detail = cc._adapter_marker_payload(valid)
    assert state == "owned"
    assert marker["format"] == "host-context-section-export-v1"

    competing = valid + marker_line + "\n"
    marker, state, detail = cc._adapter_marker_payload(competing)
    assert marker is None
    assert state == "ambiguous"
    assert "multiple" in detail


def test_f04_b04_registry_renderers_and_both_agents_formats_agree_with_parser(tmp_path):
    source = _f04_write_source(tmp_path)
    source_text = source.read_text(encoding="utf-8")
    for host, spec in cc._HOST_CONTEXT_EXPORTS.items():
        content, entry = cc._render_expected_host_context(
            tmp_path,
            host,
            source,
            source_text,
            "CONTROLCODING.md",
        )
        assert content is not None, entry
        marker, state, _detail = cc._adapter_marker_payload(content)
        assert state == "owned"
        assert marker["format"] == cc._adapter_managed_format(spec)

    portable, _included, _warnings = cc._render_agents_md_output(
        source_text,
        "CONTROLCODING.md",
    )
    portable_marker, portable_state, _detail = cc._adapter_marker_payload(portable)
    runtime, _entry = cc._render_expected_host_context(
        tmp_path,
        "codex_cli",
        source,
        source_text,
        "CONTROLCODING.md",
    )
    runtime_marker, runtime_state, _detail = cc._adapter_marker_payload(runtime)
    assert portable_state == runtime_state == "owned"
    assert portable_marker["format"] == "agents-md-portable-v1"
    assert runtime_marker["format"] == "host-context-section-export-v1"

    front_matter_source = (
        "---\ntitle: Canonical context\n---\n" + source_text
    )
    full_copy, full_entry = cc._render_expected_host_context(
        tmp_path,
        "claude_code",
        source,
        front_matter_source,
        "CONTROLCODING.md",
    )
    assert full_copy is not None, full_entry
    full_lines = full_copy.splitlines()
    closing_index = full_lines.index("---", 1)
    assert full_lines[0] == "---"
    assert full_lines[closing_index + 1].startswith("<!-- controlcoding-managed:")
    full_marker, full_state, _detail = cc._adapter_marker_payload(full_copy)
    assert full_state == "owned"
    assert full_marker["format"] == "host-context-full-copy-v1"


def test_f04_h01_owned_current_force_is_noop_with_identity_and_no_transaction_work(
    tmp_path,
    monkeypatch,
):
    target = _f04_prepare_normative_state(tmp_path, "owned_current")
    before = (
        target.read_bytes(),
        stat.S_IMODE(os.lstat(target).st_mode),
        cc._transaction_file_object_key(os.lstat(target)),
    )

    def forbidden_transaction_work(*args, **kwargs):
        raise AssertionError("owned_current force reached stage, backup, or replace work")

    monkeypatch.setattr(cc, "_stage_transaction_bytes", forbidden_transaction_work)
    monkeypatch.setattr(cc.os, "replace", forbidden_transaction_work)
    plan = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="export",
        force=True,
    )
    assert plan["entries"][0]["state"] == "owned_current"
    assert plan["entries"][0]["action"] == "noop"
    monkeypatch.setattr(cc, "_render_expected_host_context", forbidden_transaction_work)
    result = cc._apply_adapter_transaction(tmp_path, plan["entries"])
    assert result["ok"] is True
    assert result["attempted"] is False
    assert (
        target.read_bytes(),
        stat.S_IMODE(os.lstat(target).st_mode),
        cc._transaction_file_object_key(os.lstat(target)),
    ) == before


@pytest.mark.parametrize("state", sorted(cc._ADAPTER_NORMATIVE_STATES))
def test_f04_h02_each_normative_state_is_produced_separately(tmp_path, state):
    _f04_prepare_normative_state(tmp_path, state)
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="sync")
    assert plan["entries"][0]["state"] == state


def test_f04_h02_unmarked_and_foreign_are_distinct_and_adoptable(tmp_path):
    for state in ("unmarked", "foreign"):
        project = tmp_path / state
        project.mkdir()
        _f04_prepare_normative_state(project, state)
        plan = cc._adapter_plan_payload(project, host="codex_cli", operation="adopt")
        assert plan["entries"][0]["state"] == state
        assert plan["entries"][0]["action"] == "adopt"


def test_f04_h02_complete_command_matrix_classifies_before_force_or_adoption(tmp_path):
    matrix = {
        "target_absent": {"export": "create", "export_force": "create", "sync": "create", "switch": "create", "setup": "create", "adopt": "noop"},
        "owned_current": {"export": "noop", "export_force": "noop", "sync": "noop", "switch": "noop", "setup": "noop", "adopt": "noop"},
        "owned_stale": {"export": "block", "export_force": "replace", "sync": "update", "switch": "update", "setup": "update", "adopt": "noop"},
        "unmarked": {"export": "block", "export_force": "block", "sync": "block", "switch": "block", "setup": "block", "adopt": "adopt"},
        "foreign": {"export": "block", "export_force": "block", "sync": "block", "switch": "block", "setup": "block", "adopt": "adopt"},
        "invalid": {"export": "block", "export_force": "block", "sync": "block", "switch": "block", "setup": "block", "adopt": "block"},
        "ambiguous": {"export": "block", "export_force": "block", "sync": "block", "switch": "block", "setup": "block", "adopt": "block"},
        "unreadable_or_unsafe": {"export": "block", "export_force": "block", "sync": "block", "switch": "block", "setup": "block", "adopt": "block"},
    }
    for state, expected_actions in matrix.items():
        project = tmp_path / state
        project.mkdir()
        _f04_prepare_normative_state(project, state)
        observed_states = set()
        for command, expected_action in expected_actions.items():
            operation = "export" if command == "export_force" else command
            plan = cc._adapter_plan_payload(
                project,
                host="codex_cli",
                operation=operation,
                force=command == "export_force",
            )
            entry = plan["entries"][0]
            observed_states.add(entry["state"])
            assert entry["action"] == expected_action
        assert observed_states == {state}


def test_f04_h04_adoption_preserves_front_matter_payload_and_mode(tmp_path):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    original = "---\ntitle: Existing\n---\n# Existing payload\nbody\n"
    target.write_text(original, encoding="utf-8")
    target.chmod(0o640)
    before_mode = stat.S_IMODE(os.lstat(target).st_mode)

    preview = cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="adopt")
    assert preview["entries"][0]["state"] == "unmarked"
    assert preview["entries"][0]["action"] == "adopt"
    assert target.read_text(encoding="utf-8") == original
    assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 0

    adopted = target.read_text(encoding="utf-8")
    assert adopted.startswith("---\ntitle: Existing\n---\n<!-- controlcoding-managed:")
    assert "# Existing payload\nbody\n" in adopted
    assert stat.S_IMODE(os.lstat(target).st_mode) == before_mode
    marker, state, _detail = cc._adapter_marker_payload(adopted)
    assert state == "owned"
    assert marker["target"] == "AGENTS.md"


@pytest.mark.parametrize(
    "malformed",
    [
        "---\ntitle: never closed\n# payload\n",
        "--- \ntitle: malformed opener\n---\n# payload\n",
        "---\ntitle: malformed closer\n ---\n# payload\n",
    ],
)
def test_f04_h04_malformed_front_matter_fails_closed(tmp_path, malformed):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    target.write_text(malformed, encoding="utf-8")
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="adopt")
    assert plan["entries"][0]["state"] == "unreadable_or_unsafe"
    assert plan["entries"][0]["action"] == "block"
    assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 1
    assert target.read_text(encoding="utf-8") == malformed


@pytest.mark.parametrize("blocking_state", ["invalid", "ambiguous", "unreadable_or_unsafe"])
def test_f04_h02_batch_blocker_stops_before_first_adapter_mutation(tmp_path, blocking_state):
    _f04_prepare_normative_state(tmp_path, blocking_state)
    plan = cc._adapter_plan_payload(tmp_path, all_hosts=True, operation="sync")
    assert plan["preflightOk"] is False
    assert plan["entries"][1]["state"] == blocking_state
    assert all(entry["action"] != "create" or entry["host"] != "codex_cli" for entry in plan["entries"])
    assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
    _f04_assert_no_batch_outputs(tmp_path)


@pytest.mark.parametrize(
    ("kind", "relative_path"),
    [
        ("canonical", "CONTROLCODING.md"),
        ("gateway", ".controlcoding/gateway_config.json"),
        ("init_file", ".controlcoding/settings.json"),
        ("host_asset", ".controlcoding/launchers/host.json"),
    ],
)
def test_f04_h03_adapter_transaction_rejects_non_adapter_actions(
    tmp_path,
    kind,
    relative_path,
):
    target = tmp_path / relative_path
    entry = cc._text_write_entry(tmp_path, target, "{}\n", kind=kind)
    result = cc._apply_adapter_transaction(tmp_path, [entry])
    assert result["ok"] is False
    assert result["attempted"] is False
    assert not target.exists()


def test_f04_h05_cleanup_chmod_revalidates_identity_after_checkpoint(
    tmp_path,
    monkeypatch,
):
    temporary = tmp_path / ".recovery.controlcoding.fixture.stage"
    temporary.write_text("owned recovery bytes\n", encoding="utf-8")
    temporary.chmod(0o400)
    original_key = cc._transaction_file_object_key(os.lstat(temporary))
    record = {
        "path": str(temporary),
        "identity": original_key,
        "objectKey": original_key,
        "parents": cc._snapshot_transaction_parents(tmp_path, temporary),
    }
    original_unlink = type(temporary).unlink
    unlink_calls = {"count": 0}
    recovery = tmp_path / "preserved-recovery.stage"
    intruder_bytes = b"intruder bytes\n"
    intruder_mode = 0o444

    def fail_first_unlink(path, *args, **kwargs):
        if path == temporary and unlink_calls["count"] == 0:
            unlink_calls["count"] += 1
            raise OSError("injected cleanup permission failure")
        return original_unlink(path, *args, **kwargs)

    def swap_before_chmod(name, **context):
        if name == "before_cleanup_chmod":
            temporary.rename(recovery)
            temporary.write_bytes(intruder_bytes)
            temporary.chmod(intruder_mode)

    monkeypatch.setattr(type(temporary), "unlink", fail_first_unlink)
    monkeypatch.setattr(cc, "_transaction_checkpoint", swap_before_chmod)
    errors = cc._cleanup_transaction_paths(tmp_path, [record])

    assert errors
    assert "immediately before cleanup chmod" in errors[0]
    assert temporary.read_bytes() == intruder_bytes
    assert stat.S_IMODE(os.lstat(temporary).st_mode) == intruder_mode
    assert cc._transaction_file_object_key(os.lstat(temporary)) != original_key
    assert recovery.read_text(encoding="utf-8") == "owned recovery bytes\n"


def test_f04_t01_all_hosts_comes_only_from_current_registry(tmp_path):
    _f04_write_source(tmp_path)
    plan = cc._adapter_plan_payload(tmp_path, all_hosts=True)
    assert plan["preflightOk"] is True
    assert [entry["target"] for entry in plan["entries"]] == [
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
        ".clinerules",
        ".cursor/rules/project.mdc",
        ".windsurfrules",
    ]
    assert all(entry.get("_actionSet") for entry in plan["entries"])


def test_f04_t02_targeted_routing_preflights_complete_selected_subset(tmp_path):
    _f04_write_source(tmp_path)
    plan = cc._adapter_plan_payload(tmp_path, host="cursor")
    assert plan["preflightOk"] is True
    assert [entry["host"] for entry in plan["entries"]] == ["cursor"]
    assert [entry["target"] for entry in plan["entries"]] == [
        ".cursor/rules/project.mdc"
    ]
    assert {action["role"] for action in plan["entries"][0]["_actionSet"]} >= {
        "payload_marker",
        "stage",
        "directory",
    }


def test_f04_t03_future_registry_entry_automatically_plans_preflights_and_applies(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    monkeypatch.setitem(
        cc._HOST_CONTEXT_EXPORTS,
        "future_host",
        {
            "relative_path": ".future/rules.md",
            "file_label": "rules.md",
            "host_label": "Future Host",
            "export_mode": "section_export",
            "include_sections": cc._HOST_CONTEXT_RUNTIME_SECTIONS,
        },
    )
    plan = cc._adapter_plan_payload(tmp_path, all_hosts=True)
    future = next(entry for entry in plan["entries"] if entry["host"] == "future_host")
    assert plan["preflightOk"] is True
    assert future["target"] == ".future/rules.md"
    assert future["_actionSet"]

    result = cc._apply_adapter_transaction(tmp_path, plan["entries"])
    assert result["ok"] is True
    assert (tmp_path / ".future" / "rules.md").exists()


def test_f04_t04_invalid_colliding_or_nonmanaged_registry_blocks_zero_mutations(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    duplicate_spec = dict(cc._HOST_CONTEXT_EXPORTS["codex_cli"])
    monkeypatch.setitem(cc._HOST_CONTEXT_EXPORTS, "duplicate_host", duplicate_spec)
    duplicate_plan = cc._adapter_plan_payload(tmp_path, all_hosts=True)
    assert duplicate_plan["preflightOk"] is False
    _f04_assert_no_batch_outputs(tmp_path)
    monkeypatch.delitem(cc._HOST_CONTEXT_EXPORTS, "duplicate_host")

    monkeypatch.setitem(cc._HOST_CONTEXT_EXPORTS, "invalid_entry", [])
    invalid_plan = cc._adapter_plan_payload(tmp_path, all_hosts=True)
    assert invalid_plan["preflightOk"] is False
    _f04_assert_no_batch_outputs(tmp_path)
    monkeypatch.delitem(cc._HOST_CONTEXT_EXPORTS, "invalid_entry")

    nonmanaged = {
        "relative_path": "NONMANAGED.md",
        "file_label": "NONMANAGED.md",
        "host_label": "Nonmanaged",
        "export_mode": "full_copy",
        "managed": False,
    }
    monkeypatch.setitem(cc._HOST_CONTEXT_EXPORTS, "nonmanaged", nonmanaged)
    nonmanaged_plan = cc._adapter_plan_payload(tmp_path, all_hosts=True)
    assert nonmanaged_plan["preflightOk"] is False
    assert not (tmp_path / "NONMANAGED.md").exists()
    monkeypatch.delitem(cc._HOST_CONTEXT_EXPORTS, "nonmanaged")

    alias_source = tmp_path / "AGENTS.md"
    alias_source.write_text(_f04_source_text(), encoding="utf-8")
    alias_plan = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        source="AGENTS.md",
    )
    assert alias_plan["preflightOk"] is False
    assert alias_source.read_text(encoding="utf-8") == _f04_source_text()

    alias_source.unlink()
    original_planner = cc._planned_transaction_path
    shared_action = tmp_path / ".shared-controlcoding-action"

    def collide_actions(project, target, suffix, occupied):
        if suffix == ".stage":
            return shared_action
        return original_planner(project, target, suffix, occupied)

    monkeypatch.setattr(cc, "_planned_transaction_path", collide_actions)
    action_plan = cc._adapter_plan_payload(tmp_path, all_hosts=True)
    assert action_plan["preflightOk"] is False
    assert any("action path collision" in error for error in action_plan["errors"])
    _f04_assert_no_batch_outputs(tmp_path)




def _f06_json_with_extra(extra_json: str) -> str:
    return (
        '{"owner":"ControlCoding",'
        '"schema":"controlcoding.host-adapter-ownership",'
        '"version":1,"target":"AGENTS.md","host":"codex_cli",'
        '"source":"CONTROLCODING.md",'
        '"format":"host-context-section-export-v1",'
        f'"extra":{extra_json}'
        "}"
    )


def _f06_section_marker_content(encoded: str) -> str:
    return (
        "# AGENTS.md\n\n"
        f"<!-- controlcoding-managed: {encoded} -->\n\n"
        "managed payload\n"
    )


@pytest.mark.parametrize(
    ("encoded", "case_id"),
    [
        ("NaN", "top_level_nan"),
        ("Infinity", "top_level_infinity"),
        ("-Infinity", "top_level_negative_infinity"),
        (_f06_json_with_extra('{"bad":NaN}'), "nested_object"),
        (_f06_json_with_extra('[{"bad":Infinity}]'), "object_in_array"),
    ],
    ids=lambda value: value if value in {
        "top_level_nan",
        "top_level_infinity",
        "top_level_negative_infinity",
        "nested_object",
        "object_in_array",
    } else None,
)
def test_f06_f01_non_json_constants_are_invalid_non_owned_non_adoptable_and_atomic(
    tmp_path,
    monkeypatch,
    encoded,
    case_id,
):
    del case_id
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    raw = _f06_section_marker_content(encoded)
    target.write_text(raw, encoding="utf-8")

    marker, state, detail = cc._adapter_marker_payload(raw)
    assert marker is None
    assert state == "invalid"
    assert "JSON" in detail

    plan = cc._adapter_plan_payload(
        tmp_path,
        all_hosts=True,
        operation="sync",
    )
    entry = next(item for item in plan["entries"] if item["host"] == "codex_cli")
    assert plan["preflightOk"] is False
    assert entry["state"] == "invalid"
    assert entry["ownership"] == "invalid"
    assert entry["bindingAllowed"] is False
    assert entry["action"] == "block"

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("non-JSON constant reached the adapter mutation boundary")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
    ) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=True,
    ) == 1
    assert target.read_text(encoding="utf-8") == raw
    _f04_assert_no_batch_outputs(tmp_path)


def test_f06_f01_duplicate_members_remain_ambiguous_with_parse_constant():
    encoded = (
        '{"owner":"ControlCoding","owner":NaN,'
        '"schema":"controlcoding.host-adapter-ownership",'
        '"version":1,"target":"AGENTS.md","host":"codex_cli",'
        '"source":"CONTROLCODING.md",'
        '"format":"host-context-section-export-v1"}'
    )
    marker, state, detail = cc._adapter_marker_payload(
        _f06_section_marker_content(encoded)
    )
    assert marker is None
    assert state == "ambiguous"
    assert "duplicate decoded JSON member" in detail


def test_f06_f01_repeated_array_scalars_are_not_duplicate_members():
    values = dict(_F04_MARKER_VALUES)
    values["extra"] = [1, 1, "same", "same", {"value": 1}, {"value": 1}]
    content = _f06_section_marker_content(
        json.dumps(values, sort_keys=True, separators=(",", ":"))
    )
    marker, state, _detail = cc._adapter_marker_payload(content)
    assert state == "owned"
    assert marker["extra"] == values["extra"]


@pytest.mark.parametrize(
    "content",
    [
        "# AGENTS.md\n\n<!-- controlcoding-managed {} -->\npayload\n",
        "# AGENTS.md\n\n<!-- controlcoding-managed: not-json -->\npayload\n",
        "# AGENTS.md\n\n<!-- controlcoding-managed: {}\npayload\n",
        "# AGENTS.md\n\npayload\n" + _f04_marker_line() + "\n",
        "# AGENTS.md\n\n```html\n" + _f04_marker_line() + "\n```\n",
    ],
)
def test_f06_f02_single_lexical_controlcoding_candidate_malformed_or_in_payload_is_invalid(
    content,
):
    marker, state, _detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "invalid"


def test_f06_f02_multiple_lexical_candidates_are_ambiguous():
    content = (
        "# AGENTS.md\n\n"
        "<!-- controlcoding-managed -->\n"
        "<!-- controlcoding-managed=not-json -->\n"
    )
    marker, state, detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "ambiguous"
    assert "multiple" in detail


def test_f06_f02_force_and_adopt_apply_do_not_bypass_malformed_candidate(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    raw = "# AGENTS.md\n\n<!-- controlcoding-managed {} -->\npayload\n"
    target.write_text(raw, encoding="utf-8")

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("malformed marker candidate reached mutation")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
    ) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=True,
    ) == 1
    assert target.read_text(encoding="utf-8") == raw


def test_f06_f02_foreign_header_after_long_front_matter_and_true_unmarked_are_distinct(
    tmp_path,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    front_matter = ["---", *(f"field_{index}: value" for index in range(40)), "---"]
    foreign = "\n".join([
        *front_matter,
        "<!-- managed-by: OtherTool -->",
        "foreign payload",
        "",
    ])
    target.write_text(foreign, encoding="utf-8")

    marker, state, _detail = cc._adapter_marker_payload(
        foreign,
        "host-context-section-export-v1",
    )
    assert marker is None
    assert state == "foreign"
    adopt_plan = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="adopt",
    )
    assert adopt_plan["entries"][0]["state"] == "foreign"
    assert adopt_plan["entries"][0]["action"] == "adopt"

    unmarked = (
        "# AGENTS.md\n\n"
        "Ordinary prose mentions controlcoding-managed without marker comment syntax.\n"
    )
    target.write_text(unmarked, encoding="utf-8")
    marker, state, _detail = cc._adapter_marker_payload(
        unmarked,
        "host-context-section-export-v1",
    )
    assert marker is None
    assert state == "unmarked"
    unmarked_plan = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="adopt",
    )
    assert unmarked_plan["entries"][0]["state"] == "unmarked"
    assert unmarked_plan["entries"][0]["action"] == "adopt"


def test_f06_f03_full_copy_front_matter_precedes_marker_or_is_invalid():
    marker_line = cc._adapter_marker_line(
        "CLAUDE.md",
        "claude_code",
        "CONTROLCODING.md",
        "host-context-full-copy-v1",
    )
    valid = f"---\ntitle: fixture\n---\n{marker_line}\n\n# Payload\n"
    parsed, state, _detail = cc._adapter_marker_payload(valid)
    assert state == "owned"
    assert parsed["format"] == "host-context-full-copy-v1"

    inverse = f"{marker_line}\n\n---\ntitle: fixture\n---\n# Payload\n"
    parsed, state, detail = cc._adapter_marker_payload(inverse)
    assert parsed is None
    assert state == "invalid"
    assert "front matter must precede" in detail


def test_f06_f05_invalid_target_encoding_is_invalid_and_never_mutates_whole_set(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    invalid_bytes = b"# AGENTS.md\n\n\xff\xfeinvalid UTF-8\n"
    target.write_bytes(invalid_bytes)

    plan = cc._adapter_plan_payload(tmp_path, all_hosts=True, operation="sync")
    entry = next(item for item in plan["entries"] if item["host"] == "codex_cli")
    assert plan["preflightOk"] is False
    assert entry["state"] == "invalid"
    assert entry["ownership"] == "invalid"
    assert entry["action"] == "block"

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("invalid target encoding reached mutation")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=True,
    ) == 1
    assert target.read_bytes() == invalid_bytes
    _f04_assert_no_batch_outputs(tmp_path)


def test_f06_f05_filesystem_oserror_remains_unreadable_or_unsafe(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    original = "# AGENTS.md\n\nunmarked payload\n"
    target.write_text(original, encoding="utf-8")
    original_snapshot = cc._identity_safe_file_snapshot

    def fail_target_snapshot(project, path, purpose):
        if path == target:
            raise OSError("injected filesystem read failure")
        return original_snapshot(project, path, purpose)

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("filesystem read failure reached mutation")

    monkeypatch.setattr(cc, "_identity_safe_file_snapshot", fail_target_snapshot)
    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    plan = cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="sync")
    entry = plan["entries"][0]
    assert plan["preflightOk"] is False
    assert entry["state"] == "unreadable_or_unsafe"
    assert entry["ownership"] == "unreadable_or_unsafe"
    assert entry["action"] == "block"
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
    assert target.read_text(encoding="utf-8") == original




@pytest.mark.parametrize(
    "content",
    [
        (
            "# AGENTS.md\n\npayload\n<!--\n"
            "controlcoding-managed: {}\n-->\n"
        ),
        (
            "# AGENTS.md\n\npayload\n<!--\n"
            "controlcoding-managed:\n{}\n-->\n"
        ),
        (
            "# AGENTS.md\n\n<!--\n"
            "controlcoding-managed: {}\nunterminated payload\n"
        ),
        (
            "# AGENTS.md\n\n<!-- controlcoding-managed:\n"
            "{}\n-->\nmanaged-header payload\n"
        ),
        (
            "# AGENTS.md\n\nordinary payload\n<!--\n"
            "controlcoding-managed: {}\n-->\n"
        ),
        (
            "# AGENTS.md\n\n```html\n<!--\n"
            "controlcoding-managed: {}\n-->\n```\n"
        ),
    ],
    ids=[
        "opener_then_token",
        "opener_token_payload_closer_separate",
        "unterminated_multiline_comment",
        "multiline_managed_header",
        "multiline_payload_candidate",
        "multiline_code_fence_candidate",
    ],
)
def test_residual_b01_multiline_controlcoding_comment_candidates_are_invalid(content):
    candidates = cc._adapter_marker_candidates(content)
    assert len(candidates) == 1
    assert "controlcoding-managed" in candidates[0][1].lower()
    marker, state, detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "invalid"
    assert "malformed" in detail


def test_residual_b01_two_multiline_candidates_are_ambiguous():
    content = (
        "# AGENTS.md\n\n"
        "<!--\ncontrolcoding-managed: {}\n-->\n"
        "<!--\ncontrolcoding-managed: {}\n-->\n"
    )
    marker, state, detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "ambiguous"
    assert "multiple" in detail


def test_residual_b01_ordinary_multiline_token_without_comment_is_unmarked():
    content = (
        "# AGENTS.md\n\n"
        "Ordinary prose continues on the next line.\n"
        "controlcoding-managed is mentioned without HTML comment syntax.\n"
    )
    assert cc._adapter_marker_candidates(content) == []
    marker, state, _detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "unmarked"


def test_m01_res01_ordinary_html_comment_is_unmarked_and_sync_preserves_it(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    content = (
        "# AGENTS.md\n\n"
        "<!-- Maintainers: preserve this local introduction. -->\n"
        "User-authored payload.\n"
    )
    target.write_text(content, encoding="utf-8")
    before = target.read_bytes()

    assert cc._adapter_marker_candidates(content) == []
    marker, state, detail = cc._adapter_marker_payload(
        content,
        "host-context-section-export-v1",
    )
    assert marker is None
    assert state == "unmarked"
    assert state not in {"invalid", "ambiguous"}
    assert detail == "no ControlCoding ownership marker"

    plan = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="sync",
    )
    entry = plan["entries"][0]
    assert plan["preflightOk"] is False
    assert entry["state"] == "unmarked"
    assert entry["ownership"] == "unmarked"
    assert entry["bindingAllowed"] is False
    assert entry["action"] == "block"

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("ordinary HTML comment reached adapter mutation")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
    assert target.is_file()
    assert target.read_bytes() == before


def test_residual_b01_multiline_candidate_blocks_force_adopt_and_all_hosts_sync(
    tmp_path,
    monkeypatch,
):
    _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    original = (
        "# AGENTS.md\n\n<!--\n"
        "controlcoding-managed: {}\n-->\nexisting payload\n"
    )
    target.write_text(original, encoding="utf-8")
    plan = cc._adapter_plan_payload(
        tmp_path,
        all_hosts=True,
        operation="sync",
    )
    entry = next(item for item in plan["entries"] if item["host"] == "codex_cli")
    assert plan["preflightOk"] is False
    assert entry["state"] == "invalid"
    assert entry["ownership"] == "invalid"
    assert entry["bindingAllowed"] is False
    assert entry["action"] == "block"

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("multiline ControlCoding candidate reached mutation")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
    ) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=True,
    ) == 1
    assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
    assert target.read_text(encoding="utf-8") == original
    _f04_assert_no_batch_outputs(tmp_path)


def _residual_full_copy_marker() -> str:
    return cc._adapter_marker_line(
        "CLAUDE.md",
        "claude_code",
        "CONTROLCODING.md",
        "host-context-full-copy-v1",
    )


def _residual_full_copy_provenance() -> str:
    return (
        "> Generated from CONTROLCODING.md by ControlCoding host-context export.\n"
        "> Target host: Claude Code.\n"
        "> Canonical source of truth: CONTROLCODING.md.\n"
    )


@pytest.mark.parametrize(
    "managed_gap",
    [
        "\n\n\n",
        "\n\n\n\n",
        "\n\n" + _residual_full_copy_provenance() + "\n",
    ],
    ids=["two_blank_lines", "three_blank_lines", "recognized_provenance"],
)
def test_residual_b02_inverse_full_copy_finds_front_matter_at_payload_boundary(
    managed_gap,
):
    content = (
        _residual_full_copy_marker()
        + managed_gap
        + "---\ntitle: inverse fixture\n---\n# Payload\n"
    )
    payload_index = cc._adapter_full_copy_payload_line_index(content)
    assert content.splitlines()[payload_index] == "---"
    marker, state, detail = cc._adapter_marker_payload(content)
    assert marker is None
    assert state == "invalid"
    assert "front matter must precede" in detail


def test_residual_b02_valid_front_matter_and_later_horizontal_rule_are_not_inverted():
    marker_line = _residual_full_copy_marker()
    valid_front_matter = (
        "---\ntitle: valid fixture\n---\n"
        + marker_line
        + "\n\n"
        + _residual_full_copy_provenance()
        + "\n# Payload\nbody\n"
    )
    parsed, state, _detail = cc._adapter_marker_payload(valid_front_matter)
    assert state == "owned"
    assert parsed["format"] == "host-context-full-copy-v1"

    later_horizontal_rule = (
        marker_line
        + "\n\n"
        + _residual_full_copy_provenance()
        + "\n# Payload\nbody before rule\n\n---\nbody after rule\n"
    )
    parsed, state, _detail = cc._adapter_marker_payload(later_horizontal_rule)
    assert state == "owned"
    assert parsed["format"] == "host-context-full-copy-v1"


def test_m01_res01_arbitrary_provenance_stops_full_copy_managed_header_scan(
    tmp_path,
):
    _f04_write_source(tmp_path)
    arbitrary_line = "> Imported by an unrelated local workflow."
    content = (
        _residual_full_copy_marker()
        + "\n"
        + arbitrary_line
        + "\n---\ntitle: payload data\n---\n# Existing payload\n"
    )

    payload_index = cc._adapter_full_copy_payload_line_index(content)
    assert payload_index == 1
    assert content.splitlines()[payload_index] == arbitrary_line
    expected_index, position_error = cc._adapter_expected_marker_line_index(
        content,
        "host-context-full-copy-v1",
    )
    assert expected_index == 0
    assert position_error == ""

    marker, state, detail = cc._adapter_marker_payload(content)
    assert state == "owned"
    assert state not in {"invalid", "ambiguous"}
    assert detail == "valid ControlCoding ownership marker"
    assert marker["format"] == "host-context-full-copy-v1"

    target = tmp_path / "CLAUDE.md"
    target.write_text(content, encoding="utf-8")
    before = target.read_bytes()
    plan = cc._adapter_plan_payload(
        tmp_path,
        host="claude_code",
        operation="sync",
    )
    entry = plan["entries"][0]
    assert plan["preflightOk"] is True
    assert entry["state"] == "owned_stale"
    assert entry["ownership"] == "owned"
    assert entry["bindingAllowed"] is True
    assert entry["action"] == "update"
    assert target.is_file()
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    ("snapshot_role", "checkpoint_name"),
    [
        ("source", "before_file_open"),
        ("source", "after_file_read"),
        ("target", "before_file_open"),
        ("target", "after_file_read"),
    ],
)
def test_residual_h01_initial_identity_drift_is_normative_and_router_fails_closed(
    tmp_path,
    monkeypatch,
    capsys,
    snapshot_role,
    checkpoint_name,
):
    source = _f04_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    target.write_text(
        "# AGENTS.md\n\nunmarked target payload\n",
        encoding="utf-8",
    )
    snapshot_path = source if snapshot_role == "source" else target
    source_before = source.read_bytes()
    target_before = target.read_bytes()
    original_object_key = cc._filesystem_object_key
    injection = {
        "armed": False,
        "alter_next_key": False,
        "hits": 0,
    }

    def inject_checkpoint(name, **context):
        if (
            injection["armed"]
            and name == checkpoint_name
            and Path(context["path"]) == snapshot_path
        ):
            injection["armed"] = False
            injection["alter_next_key"] = True
            injection["hits"] += 1

    def mismatched_object_key(file_stat):
        object_key = original_object_key(file_stat)
        if injection["alter_next_key"]:
            injection["alter_next_key"] = False
            return (object_key[0], object_key[1] + 1, object_key[2])
        return object_key

    monkeypatch.setattr(cc, "_transaction_checkpoint", inject_checkpoint)
    monkeypatch.setattr(cc, "_filesystem_object_key", mismatched_object_key)

    injection["armed"] = True
    plan = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="sync",
    )
    entry = plan["entries"][0]
    assert plan["preflightOk"] is False
    assert entry["state"] == "unreadable_or_unsafe"
    assert entry["ownership"] == "unreadable_or_unsafe"
    assert entry["action"] == "block"
    assert "drifted" in entry["detail"]

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("initial identity drift reached adapter mutation")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    capsys.readouterr()
    injection["armed"] = True
    assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert injection["hits"] >= 2
    assert source.read_bytes() == source_before
    assert target.read_bytes() == target_before


def test_residual_h01_unrelated_runtimeerror_is_not_normalized(
    tmp_path,
    monkeypatch,
):
    source = _f04_write_source(tmp_path)

    def programming_error(name, **context):
        if name == "before_file_open" and Path(context["path"]) == source:
            raise RuntimeError("injected unrelated programming error")

    monkeypatch.setattr(cc, "_transaction_checkpoint", programming_error)
    with pytest.raises(RuntimeError, match="unrelated programming error"):
        cc._adapter_plan_payload(
            tmp_path,
            host="codex_cli",
            operation="sync",
        )




def _identity_observation_stat(file_stat, *, ctime_ns: int) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=file_stat.st_dev,
        st_ino=file_stat.st_ino,
        st_mode=file_stat.st_mode,
        st_size=file_stat.st_size,
        st_ctime=ctime_ns / 1_000_000_000,
        st_mtime=file_stat.st_mtime,
        st_ctime_ns=ctime_ns,
        st_mtime_ns=file_stat.st_mtime_ns,
        st_file_attributes=getattr(file_stat, "st_file_attributes", 0),
    )


def _patch_identity_observation_channels(
    monkeypatch,
    target: Path,
    *,
    path_ctime_ns: tuple[int, int],
    descriptor_ctime_ns: tuple[int, int],
) -> dict:
    original_lstat = cc.os.lstat
    original_fstat = cc.os.fstat
    baseline_stat = original_lstat(target)
    observations = {"path": [], "descriptor": []}

    def observed_lstat(path):
        result = original_lstat(path)
        if Path(path) != target:
            return result
        index = len(observations["path"])
        assert index < len(path_ctime_ns)
        observed = _identity_observation_stat(
            result,
            ctime_ns=path_ctime_ns[index],
        )
        observations["path"].append(cc._filesystem_observation(observed))
        return observed

    def observed_fstat(descriptor):
        result = original_fstat(descriptor)
        index = len(observations["descriptor"])
        assert index < len(descriptor_ctime_ns)
        observed = _identity_observation_stat(
            result,
            ctime_ns=descriptor_ctime_ns[index],
        )
        observations["descriptor"].append(cc._filesystem_observation(observed))
        return observed

    monkeypatch.setattr(cc.os, "lstat", observed_lstat)
    monkeypatch.setattr(cc.os, "fstat", observed_fstat)
    return {
        "observations": observations,
        "objectKey": cc._filesystem_object_key(baseline_stat),
        "mode": stat.S_IMODE(baseline_stat.st_mode),
    }


def test_t_fs_01_stable_cross_channel_ctime_representation_mismatch(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "stable-observation.txt"
    payload = b"stable identity observation\n"
    target.write_bytes(payload)
    fixture = _patch_identity_observation_channels(
        monkeypatch,
        target,
        path_ctime_ns=(101, 101),
        descriptor_ctime_ns=(202, 202),
    )

    try:
        snapshot = cc._identity_safe_file_snapshot(
            tmp_path,
            target,
            "identity observation fixture",
        )
    except cc._AdapterIdentityDriftError as exc:
        pytest.fail(f"stable channel observations raised identity drift: {exc}")

    path_before, path_after = fixture["observations"]["path"]
    descriptor_before, descriptor_after = fixture["observations"]["descriptor"]
    assert path_before == path_after
    assert descriptor_before == descriptor_after
    assert path_before[:3] == descriptor_before[:3] == fixture["objectKey"]
    assert path_before[2] == stat.S_IFREG
    assert path_before[3:5] == descriptor_before[3:5]
    assert path_before[5] != descriptor_before[5]
    assert path_before[6] == descriptor_before[6]
    assert snapshot["state"] == "present"
    assert snapshot["bytes"] == payload
    assert snapshot["hash"] == hashlib.sha256(payload).hexdigest()
    assert snapshot["mode"] == fixture["mode"]
    assert snapshot["objectKey"] == fixture["objectKey"]
    assert target.read_bytes() == payload


def test_t_fs_02_real_pathname_channel_drift(tmp_path, monkeypatch):
    target = tmp_path / "pathname-drift.txt"
    payload = b"pathname channel remains unchanged\n"
    target.write_bytes(payload)
    fixture = _patch_identity_observation_channels(
        monkeypatch,
        target,
        path_ctime_ns=(101, 102),
        descriptor_ctime_ns=(202, 202),
    )

    with pytest.raises(
        cc._AdapterIdentityDriftError,
        match="identity or state drifted while reading",
    ) as raised:
        cc._identity_safe_file_snapshot(
            tmp_path,
            target,
            "pathname drift fixture",
        )

    path_before, path_after = fixture["observations"]["path"]
    descriptor_before, descriptor_after = fixture["observations"]["descriptor"]
    assert path_before[:5] == path_after[:5]
    assert path_before[:5] == descriptor_before[:5]
    assert path_before[:3] == fixture["objectKey"]
    assert path_before[2] == stat.S_IFREG
    assert path_before[5] != path_after[5]
    assert path_before[6] == path_after[6] == descriptor_before[6]
    assert descriptor_before == descriptor_after
    assert type(raised.value) is cc._AdapterIdentityDriftError
    assert target.read_bytes() == payload


def test_t_fs_03_real_descriptor_channel_drift(tmp_path, monkeypatch):
    target = tmp_path / "descriptor-drift.txt"
    payload = b"descriptor channel remains unchanged\n"
    target.write_bytes(payload)
    fixture = _patch_identity_observation_channels(
        monkeypatch,
        target,
        path_ctime_ns=(101, 101),
        descriptor_ctime_ns=(202, 203),
    )

    with pytest.raises(
        cc._AdapterIdentityDriftError,
        match="identity or state drifted while reading",
    ) as raised:
        cc._identity_safe_file_snapshot(
            tmp_path,
            target,
            "descriptor drift fixture",
        )

    path_before, path_after = fixture["observations"]["path"]
    descriptor_before, descriptor_after = fixture["observations"]["descriptor"]
    assert path_before == path_after
    assert descriptor_before[:5] == descriptor_after[:5]
    assert descriptor_before[:5] == path_before[:5]
    assert descriptor_before[:3] == fixture["objectKey"]
    assert descriptor_before[2] == stat.S_IFREG
    assert descriptor_before[5] != descriptor_after[5]
    assert descriptor_before[6] == descriptor_after[6] == path_before[6]
    assert type(raised.value) is cc._AdapterIdentityDriftError
    assert target.read_bytes() == payload

@pytest.mark.parametrize("failure", ["returned", "planner", "publication", "success"])
def test_cmd_setup_stops_after_init_failure(tmp_path, monkeypatch, capsys, record_property, failure):
    project = tmp_path / "project"; project.mkdir()
    answers = _write_base_setup_answers(tmp_path, memory_default_policy="deferred", selected_packs=[])
    _patch_base_setup_runtime(monkeypatch)
    calls = []
    target = project / ".gitignore"
    if failure == "planner": target.write_bytes(b"custom incomplete ignore\n")
    real_init = cc.cmd_init
    def init(*args, **kwargs):
        calls.append("init")
        return 7 if failure == "returned" else real_init(*args, **kwargs)
    monkeypatch.setattr(cc_setup, "cmd_init", init)
    def spy(name, result):
        def call(*args, **kwargs): calls.append(name); return result
        return call
    monkeypatch.setattr(cc_setup, "_write_host_integration_assets", spy("host", []))
    monkeypatch.setattr(cc_setup, "_sync_host_context_file_compat", spy("adapter", "AGENTS.md"))
    monkeypatch.setattr(cc_setup, "_bootstrap_default_project_memory", spy("memory", {"status": "deferred"}))
    monkeypatch.setattr(cc_setup, "cmd_install", spy("pack", 0))
    monkeypatch.setattr(cc_setup, "save_settings", spy("backend", None))
    monkeypatch.setattr(cc_setup, "cmd_doctor", spy("doctor", 0))
    if failure == "publication":
        real_link = os.link
        def link(src, dst, *args, **kwargs):
            if Path(dst).name == "settings.json":
                calls.append("publication_race"); Path(dst).write_bytes(b"concurrent settings\n")
            return real_link(src, dst, *args, **kwargs)
        monkeypatch.setattr(os, "link", link)
    result = cc_setup.cmd_setup(project, answers_file=answers, apply_answers=True)
    assert result == (0 if failure == "success" else 7 if failure == "returned" else 1)
    assert calls == (["init", "host", "adapter", "memory", "doctor"] if failure == "success"
                     else ["init", "publication_race"] if failure == "publication" else ["init"])
    assert (project / "CONTROLCODING.md").read_bytes() == ("# Test" + os.linesep).encode()
    assert (project / ".controlcoding/cc_config.json").exists()
    if failure == "planner": assert target.read_bytes() == b"custom incomplete ignore\n"
    if failure == "publication": assert (project / ".controlcoding/settings.json").read_bytes() == b"concurrent settings\n"
    output = capsys.readouterr().out
    if failure != "success": assert "Partial setup" in output and "Earlier context/config/Git" in output and "no rollback" in output
    record_property("events", json.dumps(calls)); record_property("returncode", result)
