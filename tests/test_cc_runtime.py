"""Core prerequisite diagnostics and actual private SQLite capability checks."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cc_memory_lib import runtime


def test_runtime_probe_uses_only_private_memory_and_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    real_connect = sqlite3.connect
    opened = []

    def connect(database, *args, **kwargs):
        assert database == ":memory:"
        conn = real_connect(database, *args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert runtime.memory_runtime_error() is None
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["deserialize", "query", "wrong_data", "close"])
def test_runtime_probe_operational_failures_close_and_reject(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    closed = []

    class Probe:
        def execute(self, sql):
            if sql.startswith("SELECT") and failure == "query":
                raise sqlite3.DatabaseError("query failed")
            return self

        def deserialize(self, image):
            assert len(image) == 1024
            if failure == "deserialize":
                raise sqlite3.NotSupportedError("deserialize failed")

        def fetchone(self):
            return ("wrong",) if failure == "wrong_data" else ("cc-deserialize-probe-v1",)

        def close(self):
            closed.append(True)
            if failure == "close":
                raise sqlite3.OperationalError("close failed")

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: Probe())
    issue = runtime.memory_runtime_error()
    assert issue.code == "memory_runtime_unavailable"
    assert "deserialize" in issue.message
    assert issue.sqlite == sqlite3.sqlite_version
    assert closed == [True]
    assert list(tmp_path.iterdir()) == []


def test_missing_sqlite_has_prerequisite_diagnostic(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlite3", None)
    issue = runtime.memory_runtime_error()
    assert issue.code == "sqlite_runtime_unavailable"
    assert issue.sqlite == "unavailable"


def test_core_check_does_not_require_deserialize(monkeypatch):
    def unexpected_connect(*args, **kwargs):
        pytest.fail("Core check attempted to initialize/probe memory")
    monkeypatch.setattr(sqlite3, "connect", unexpected_connect)
    assert runtime.core_runtime_error() is None


def test_unsupported_core_rejects_direct_setup_and_main_before_writes(tmp_path, monkeypatch, capsys):
    import cc
    import cc_setup
    monkeypatch.setattr(sys, "version_info", (3, 10, 99))
    assert cc.main() == 1
    assert cc_setup.cmd_setup(tmp_path) == 1
    out = capsys.readouterr()
    assert "Python 3.11+" in out.err and "Python 3.11+" in out.out
    assert list(tmp_path.iterdir()) == []


def test_deferred_setup_and_guide_skip_memory_probe(tmp_path, monkeypatch, capsys):
    import cc_setup
    # Stop at the first installation boundary after all preflight checks.
    reached = []
    monkeypatch.setattr(cc_setup, "_ensure_setup_repo_boundary", lambda *args: reached.append(True) or False)
    monkeypatch.setattr(cc_setup, "memory_runtime_error", lambda: pytest.fail("deferred memory was probed"))
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps({"setup": {"user_host": "codex_cli", "memory_default_policy": "deferred"}}), encoding="utf-8")
    assert cc_setup.cmd_setup_chat_guide(tmp_path / "absent", host_hint="codex_cli") == 0
    assert not (tmp_path / "absent").exists()
    assert cc_setup.cmd_setup(tmp_path, answers_file=handoff) == 1
    assert reached == [True]
    assert list(tmp_path.iterdir()) == [handoff]
    capsys.readouterr()


@pytest.mark.parametrize("missing_sqlite", [False, True])
def test_source_entrypoint_prerequisite_diagnostic(tmp_path, missing_sqlite):
    script = Path(__file__).resolve().parents[1] / "scripts/cc.py"
    driver = tmp_path / "driver.py"
    prelude = "sys.modules['sqlite3'] = None" if missing_sqlite else "sys.version_info = (3, 10, 99)"
    driver.write_text(
        "import runpy,sys\n" + prelude + "\n"
        + f"sys.path.insert(0, {str(script.parent)!r})\n"
        + f"sys.argv = [{str(script)!r}, 'setup', '--project-root', {str(tmp_path / 'absent')!r}]\n"
        + f"runpy.run_path({str(script)!r}, run_name='__main__')\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(driver)], capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert ("Cannot import sqlite3" if missing_sqlite else "requires Python 3.11+") in result.stderr
    assert not (tmp_path / "absent").exists()


def test_real_python310_source_rejection(tmp_path):
    candidates = [os.environ.get("CC_TEST_PYTHON310", ""), "C:/Python310/python.exe",
                  str(Path.home() / "AppData/Local/Programs/Python/Python310/python.exe")]
    executable = next((path for path in candidates if path and Path(path).is_file()), None)
    if not executable:
        pytest.skip("real Python 3.10 unavailable in inspected paths; simulated rejection is separate evidence")
    version = subprocess.run([executable, "-B", "-c", "import sys; print(sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=30)
    assert version.stdout.strip() == "(3, 10)"
    script = Path(__file__).resolve().parents[1] / "scripts/cc.py"
    result = subprocess.run([executable, "-B", str(script), "setup", "--project-root", str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "requires Python 3.11+" in result.stderr
    assert list(tmp_path.iterdir()) == []
