"""Adversarial bounded-pipe tests using disposable child processes."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_evidence_process as process


def run(tmp_path, code, **kwargs):
    return process.run_command([sys.executable, "-B", "-c", code], cwd=tmp_path,
                               timeout=kwargs.pop("timeout", 5), **kwargs)


def test_both_streams_drained_without_retaining_secrets(tmp_path):
    value = run(tmp_path, "import os; [(os.write(1,b'SECRET'*8000),os.write(2,b'SECRET'*8000)) for _ in range(8)]")
    assert value["status"] == "passed"
    assert value["output"]["stdoutBytes"] == value["output"]["stderrBytes"] == 384000
    assert "SECRET" not in str(value)
    assert value["stdoutTail"] == value["stderrTail"] == ""


def test_output_limit_terminates_without_unbounded_capture(tmp_path):
    before = time.monotonic()
    value = run(tmp_path, "import os\nwhile True: os.write(1,b'x'*65536)", output_limit=100000)
    assert value["status"] == "incomplete" and value["error"] == "output_limit"
    assert value["output"]["stdoutBytes"] == 100001
    assert time.monotonic() - before < 4


def test_timeout_is_structured_and_kills_owned_child(tmp_path):
    value = run(tmp_path, "import time; time.sleep(30)", timeout=0.2)
    assert value["error"] == "timeout" and value["returnCode"] is not None
    assert value["durationMs"] < 2500


def test_descendant_retaining_pipe_does_not_block(tmp_path):
    value = run(tmp_path, "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'])", timeout=5)
    assert value["error"] == "pipe_timeout"
    assert value["durationMs"] < 1800


def test_missing_executable_does_not_expose_path(tmp_path):
    value = process.run_command([str(tmp_path / "PRIVATE_MISSING")], cwd=tmp_path, timeout=1)
    assert value["error"] == "spawn_error"
    assert "PRIVATE_MISSING" not in str(value) and str(tmp_path) not in str(value)


def test_normal_nonzero_preserved(tmp_path):
    value = run(tmp_path, "raise SystemExit(7)")
    assert value["status"] == "failed" and value["returnCode"] == 7


def test_internal_capture_has_shared_bound(tmp_path):
    value = run(tmp_path, "import os; os.write(1,b'a'*50); os.write(2,b'b'*50)", capture_limit=70)
    assert sum(map(len, value["_captured"].values())) == 70
    assert value["_captureComplete"] is False


def test_interrupt_is_structured(tmp_path, monkeypatch):
    def interrupted(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(process, "_available", interrupted)
    value = run(tmp_path, "import time; time.sleep(30)")
    assert value["error"] == "interrupted" and value["returnCode"] is not None
