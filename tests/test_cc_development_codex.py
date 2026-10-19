"""Offline worker contract tests; no provider requests or installations."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_development_codex as codex


MODEL = {"provider": "openai", "id": "explicit-model", "revision": "declared-revision"}


def events(files=None, usage=None):
    return "\n".join(json.dumps(x) for x in [
        {"type": "thread.started", "thread_id": "local-test"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"files": files or []})}},
        {"type": "turn.completed", "usage": usage or {"input_tokens": 5, "output_tokens": 3, "cached_input_tokens": 2}}])


def test_command_is_native_readonly_explicit_model(tmp_path):
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"offline-native-placeholder")
    argv = codex.command_preview(str(binary), MODEL, tmp_path, tmp_path / "schema.json", "intent")
    assert argv[:5] == [str(binary), "-a", "never", "exec", "--sandbox"]
    assert "read-only" in argv and "--ephemeral" in argv and "--json" in argv
    assert argv[argv.index("-m") + 1] == MODEL["id"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


@pytest.mark.parametrize("path", ["../outside", "/root", "a\\b", "a:b", "a//b", ".git/config", "request.json", "sub/.codex/a", ".cc-codex-trace.json", "NUL.txt", "file.", "file "])
def test_proposal_path_denied(tmp_path, path):
    with pytest.raises(codex.WorkerError):
        codex.parse_events(events([{"path": path, "content": "bad"}]), tmp_path)


def test_usage_is_actual_tokens_cost_unavailable_until_reviewed_estimate(tmp_path):
    files, usage = codex.parse_events(events([{"path": "src/new.py", "content": "pass\n"}]), tmp_path)
    assert files[0]["content"] == "pass\n"
    assert usage == {"inputTokens": 5, "outputTokens": 3, "costMicros": None}


@pytest.mark.parametrize("raw", [
    events() + '\n{"type":"turn.failed"}',
    events() + '\n{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
    events().replace('"input_tokens": 5', '"input_tokens": true'),
    events().replace('"output_tokens": 3', '"output_tokens": -1'),
    '{"type":"turn.completed","type":"error"}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}',
])
def test_incomplete_failed_duplicate_or_invalid_usage_never_passes(tmp_path, raw):
    with pytest.raises((codex.WorkerError, ValueError)):
        codex.parse_events(raw, tmp_path)


def test_duplicate_files_case_insensitive(tmp_path):
    with pytest.raises(codex.WorkerError, match="duplicate"):
        codex.parse_events(events([{"path": "a.py", "content": "x"}, {"path": "A.py", "content": "y"}]), tmp_path)


def test_conflicting_file_hierarchy_rejected_before_materialization(tmp_path):
    with pytest.raises(codex.WorkerError, match="hierarchy_conflict"):
        codex.parse_events(events([{"path": "a", "content": "x"}, {"path": "a/b", "content": "y"}]), tmp_path)


def test_hardlinked_target_rejected(tmp_path):
    original = tmp_path / "original"
    original.write_text("retain")
    (tmp_path / "linked").hardlink_to(original)
    with pytest.raises(codex.WorkerError, match="not_regular"):
        codex.safe_path(tmp_path, "linked")


def test_invalid_item_event_does_not_escape_worker_error(tmp_path):
    with pytest.raises(codex.WorkerError, match="item_invalid"):
        codex.parse_events('{"type":"item.completed","item":null}', tmp_path)


def test_link_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation privileges unavailable")
    with pytest.raises(codex.WorkerError, match="link"):
        codex.safe_path(tmp_path, "link/file")


def fixture_request(tmp_path, case=True):
    request = {"schemaVersion": 1, "model": MODEL, "prompt": "replace source", "files": ["source.py"]}
    if case:
        request["caseId"] = "case-one"
    (tmp_path / "source.py").write_text("old", encoding="utf-8")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def test_evaluation_materializes_and_retains_declared_estimate(tmp_path, monkeypatch):
    request = fixture_request(tmp_path)
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"pinned")
    digest = hashlib.sha256(b"pinned").hexdigest()
    monkeypatch.setattr(codex, "run_bounded", lambda *a: (events([{"path": "source.py", "content": "new"}]).encode(), b"", None))
    result = codex.worker(request, str(binary), digest, 1000001, 1)
    assert set(result) == {"model", "usage"}
    assert result["usage"]["costMicros"] == 6
    assert (tmp_path / "source.py").read_text() == "new"
    trace = json.loads((tmp_path / ".cc-codex-trace.json").read_text())
    assert trace["modelSource"] == "declared" and trace["costSource"] == "configured-estimate"


def test_host_returns_proposal_without_materialization(tmp_path, monkeypatch):
    request = fixture_request(tmp_path, case=False)
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"pinned")
    monkeypatch.setattr(codex, "run_bounded", lambda *a: (events([{"path": "source.py", "content": "new"}]).encode(), b"", None))
    result = codex.worker(request, str(binary), hashlib.sha256(b"pinned").hexdigest(), 3, 5)
    assert result["files"] == [{"path": "source.py", "content": "new"}]
    assert (tmp_path / "source.py").read_text() == "old"


def test_binary_changed_after_invocation_blocks_materialization(tmp_path, monkeypatch):
    request = fixture_request(tmp_path)
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"pinned")
    def change(*args):
        binary.write_bytes(b"changed")
        return events([{"path": "source.py", "content": "new"}]).encode(), b"", None
    monkeypatch.setattr(codex, "run_bounded", change)
    with pytest.raises(codex.WorkerError, match="binary_changed"):
        codex.worker(request, str(binary), hashlib.sha256(b"pinned").hexdigest(), 3, 5)
    assert (tmp_path / "source.py").read_text() == "old"


def test_failed_process_trace_retained(tmp_path, monkeypatch):
    request = fixture_request(tmp_path)
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"pinned")
    monkeypatch.setattr(codex, "run_bounded", lambda *a: (b"partial", b"provider-error", "codex_process_failed"))
    with pytest.raises(codex.WorkerError, match="process_failed"):
        codex.worker(request, str(binary), hashlib.sha256(b"pinned").hexdigest(), 3, 5)
    assert json.loads((tmp_path / ".cc-codex-trace.json").read_text())["status"] == "codex_process_failed"


def test_real_local_process_output_and_timeout_bounds(tmp_path):
    stdout, stderr, failure = codex.run_bounded([sys.executable, "-I", "-B", "-c", "print('local-only')"], tmp_path, 5, 4096)
    assert stdout.strip() == b"local-only" and not stderr and failure is None
    stdout, stderr, failure = codex.run_bounded([sys.executable, "-I", "-B", "-c", "print('x'*50000)"], tmp_path, 5, 4096)
    assert failure == "codex_output_limit" and len(stdout) <= 4096
    _, _, failure = codex.run_bounded([sys.executable, "-I", "-B", "-c", "import time;time.sleep(5)"], tmp_path, 0.1, 4096)
    assert failure == "codex_timeout"


def test_standalone_copied_worker_help_no_import_dependency(tmp_path):
    script = tmp_path / "worker.py"
    script.write_bytes(Path(codex.__file__).read_bytes())
    result = subprocess.run([sys.executable, "-I", "-B", str(script), "--help"], capture_output=True)
    assert result.returncode == 0 and b"--codex-sha256" in result.stdout
