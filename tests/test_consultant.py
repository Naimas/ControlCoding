#!/usr/bin/env python3
"""Tests for mcp_consultant.py - External consultation MCP server.

Covers: _CallCounter, _load_image_b64, session management, logging,
backend calls (_call_ollama/anthropic/openai/claude), and the consult() tool.
"""

import base64
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Mock fastmcp before importing mcp_consultant
_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool = lambda f: f  # passthrough decorator
_mock_mcp_instance.run = MagicMock()

_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP = MagicMock(return_value=_mock_mcp_instance)

sys.modules["fastmcp"] = _mock_fastmcp

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
)
with patch.dict(
    os.environ,
    {
        "CONSULT_BACKEND": "ollama",
        "CONSULT_FALLBACK_BACKEND": "",
        "CONSULT_MAX_CALLS": "0",
    },
):
    if "mcp_consultant" in sys.modules:
        del sys.modules["mcp_consultant"]
    import mcp_consultant


# ------------------------------------------------------------------ helpers ---


def _deny_real_subprocess(*_args, **_kwargs):
    raise AssertionError("Unexpected real subprocess invocation")


def _deny_real_network(*_args, **_kwargs):
    raise AssertionError("Unexpected real network invocation")


def _allow_requested_backend_gate(
    role_ref,
    component="consultant",
    requested_backend="",
    requested_model="",
):
    if not requested_backend:
        return {
            "allowed": False,
            "reason": "missing_backend",
            "detail": "No backend configured for this test call.",
            "role_ref": role_ref,
            "component": component,
            "backend": "",
            "model": requested_model,
        }
    return {
        "allowed": True,
        "reason": "test_allowed",
        "detail": "Allowed by hermetic test gate.",
        "role_ref": role_ref,
        "component": component,
        "backend": requested_backend,
        "model": requested_model,
    }


@pytest.fixture(autouse=True)
def _hermetic_backend_runtime(monkeypatch):
    """Reset backend state and deny unmocked provider access for every test."""
    monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "")
    monkeypatch.setattr(
        mcp_consultant, "_call_counter", mcp_consultant._CallCounter(0)
    )
    monkeypatch.setenv("CONSULT_BACKEND", "ollama")
    monkeypatch.setenv("CONSULT_FALLBACK_BACKEND", "")
    monkeypatch.setattr(
        mcp_consultant.subprocess, "run", _deny_real_subprocess
    )
    monkeypatch.setattr(
        mcp_consultant.urllib.request, "urlopen", _deny_real_network
    )


@pytest.fixture(autouse=True)
def _isolated_project_root(tmp_path):
    """Isolate consultant runtime gating from the repo-local control plane."""
    original_root = mcp_consultant.PROJECT_ROOT
    original_log_dir = mcp_consultant.LOG_DIR
    mcp_consultant.PROJECT_ROOT = tmp_path
    mcp_consultant.LOG_DIR = str(tmp_path / ".controlcoding")
    yield
    mcp_consultant.PROJECT_ROOT = original_root
    mcp_consultant.LOG_DIR = original_log_dir


@pytest.fixture
def log_dir(tmp_path):
    """Redirect LOG_DIR to tmp_path for test isolation."""
    original = mcp_consultant.LOG_DIR
    mcp_consultant.LOG_DIR = str(tmp_path / ".controlcoding")
    yield tmp_path / ".controlcoding"
    mcp_consultant.LOG_DIR = original


def _make_png(tmp_path: Path, name: str = "test.png") -> Path:
    """Create a minimal valid PNG file."""
    # Minimal 1x1 PNG (67 bytes)
    png_data = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
        b'\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00'
        b'\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    p = tmp_path / name
    p.write_bytes(png_data)
    return p


# -------------------------------------------------------- TestCallCounter ---


class TestCallCounter:
    def test_unlimited(self):
        cc = mcp_consultant._CallCounter(0)
        assert cc.can_call() is True
        assert cc.remaining == "unlimited"
        cc.increment()
        assert cc.can_call() is True

    def test_limited_blocks(self):
        cc = mcp_consultant._CallCounter(2)
        assert cc.can_call() is True
        cc.increment()
        cc.increment()
        assert cc.can_call() is False
        assert cc.remaining == 0

    def test_increment_returns_count(self):
        cc = mcp_consultant._CallCounter(0)
        assert cc.increment() == 1
        assert cc.increment() == 2
        assert cc.count == 2

    def test_remaining_decrements(self):
        cc = mcp_consultant._CallCounter(5)
        assert cc.remaining == 5
        cc.increment()
        assert cc.remaining == 4
        cc.increment()
        assert cc.remaining == 3

    def test_thread_safe(self):
        """Counter works correctly under concurrent access."""
        import threading

        cc = mcp_consultant._CallCounter(0)
        errors = []

        def inc():
            try:
                for _ in range(100):
                    cc.increment()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=inc) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert cc.count == 400


# ------------------------------------------------------- TestLoadImageB64 ---


class TestLoadImageB64:
    def test_valid_png(self, tmp_path):
        p = _make_png(tmp_path)
        result = mcp_consultant._load_image_b64(str(p))
        assert result is not None
        b64_data, media_type = result
        assert media_type == "image/png"
        # Verify it's valid base64
        decoded = base64.standard_b64decode(b64_data)
        assert decoded[:4] == b'\x89PNG'

    def test_missing_file(self):
        assert mcp_consultant._load_image_b64("/nonexistent/img.png") is None

    def test_media_type_mapping(self, tmp_path):
        for ext, expected in [
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".gif", "image/gif"),
            (".webp", "image/webp"),
        ]:
            p = tmp_path / f"test{ext}"
            p.write_bytes(b"fake image data")
            result = mcp_consultant._load_image_b64(str(p))
            assert result is not None
            _, media_type = result
            assert media_type == expected


# ------------------------------------------------ TestSessionManagement ---


class TestSessionManagement:
    def test_generate_first_id(self, log_dir):
        sid = mcp_consultant._generate_session_id("debug")
        assert sid == "debug-001"

    def test_generate_incremental_id(self, log_dir):
        # Write existing sessions
        log_path = log_dir / "consult_sessions.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"session_id": "debug-001", "exchange_num": 1},
            {"session_id": "debug-002", "exchange_num": 1},
        ]
        log_path.write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        sid = mcp_consultant._generate_session_id("debug")
        assert sid == "debug-003"

    def test_load_empty_history(self, log_dir):
        result = mcp_consultant._load_session_history("debug-001")
        assert result == []

    def test_load_filters_by_session(self, log_dir):
        log_path = log_dir / "consult_sessions.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"session_id": "debug-001", "exchange_num": 1, "problem": "q1"},
            {"session_id": "debug-002", "exchange_num": 1, "problem": "q2"},
            {"session_id": "debug-001", "exchange_num": 2, "problem": "q3"},
        ]
        log_path.write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        result = mcp_consultant._load_session_history("debug-001")
        assert len(result) == 2
        assert result[0]["problem"] == "q1"
        assert result[1]["problem"] == "q3"

    def test_build_context_empty(self):
        assert mcp_consultant._build_session_context([]) == ""

    def test_build_context_formats(self):
        history = [
            {"exchange_num": 1, "problem": "What is X?", "response": "X is Y."},
        ]
        ctx = mcp_consultant._build_session_context(history)
        assert "Exchange 1" in ctx
        assert "What is X?" in ctx
        assert "X is Y." in ctx
        assert "Previous exchanges" in ctx


# -------------------------------------------------------- TestLogFunctions ---


class TestLogFunctions:
    def test_log_consultation_creates_file(self, log_dir):
        mcp_consultant._log_consultation(
            "debug", "test problem", "test response", "ollama", "qwen2.5:14b"
        )
        log_file = log_dir / "consult_log.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert entry["role"] == "debug"
        assert entry["backend"] == "ollama"
        assert entry["prompt_chars"] == len("test problem")

    def test_log_session_exchange(self, log_dir):
        mcp_consultant._log_session_exchange(
            session_id="debug-001",
            exchange_num=1,
            role="debug",
            problem="test q",
            response="test a",
            backend="ollama",
            model="qwen2.5:14b",
        )
        log_path = log_dir / "consult_sessions.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "debug-001"
        assert entry["exchange_num"] == 1
        assert entry["session_closed"] is False

    def test_log_session_exchange_closed(self, log_dir):
        mcp_consultant._log_session_exchange(
            session_id="debug-001",
            exchange_num=5,
            role="debug",
            problem="last q",
            response="last a",
            backend="ollama",
            model="test",
            closed=True,
        )
        log_path = log_dir / "consult_sessions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_closed"] is True


# ------------------------------------------------------- TestBackendCalls ---


class TestBackendCalls:
    def _mock_urlopen(self, response_data: dict):
        """Create a mock for urllib.request.urlopen."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_ollama_ok(self):
        mock_resp = self._mock_urlopen({"response": "Ollama says hello"})
        with patch.object(
            mcp_consultant.urllib.request, "urlopen", return_value=mock_resp
        ):
            result = mcp_consultant._call_ollama(
                "test", "qwen2.5:14b", "system prompt"
            )
        assert result == "Ollama says hello"

    def test_ollama_with_image(self):
        mock_resp = self._mock_urlopen({"response": "I see an image"})
        with patch.object(
            mcp_consultant.urllib.request, "urlopen", return_value=mock_resp
        ) as mock_url:
            result = mcp_consultant._call_ollama(
                "test", "model", "system", image=("base64data", "image/png")
            )
        assert result == "I see an image"
        # Verify images was included in payload
        call_args = mock_url.call_args
        req = call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert "images" in payload
        assert payload["images"] == ["base64data"]

    def test_anthropic_no_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = mcp_consultant._call_anthropic(
                "test", "claude-sonnet-4-6", "system"
            )
        assert "ERROR" in result
        assert "ANTHROPIC_API_KEY" in result

    def test_anthropic_ok(self):
        resp_data = {"content": [{"text": "Anthropic response"}]}
        mock_resp = self._mock_urlopen(resp_data)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.object(
                mcp_consultant.urllib.request, "urlopen", return_value=mock_resp
            ):
                result = mcp_consultant._call_anthropic(
                    "test", "claude-sonnet-4-6", "system"
                )
        assert result == "Anthropic response"

    def test_openai_no_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONSULT_OPENAI_API_KEY", None)
            result = mcp_consultant._call_openai("test", "gpt-4o", "system")
        assert "ERROR" in result
        assert "CONSULT_OPENAI_API_KEY" in result

    def test_openai_ok(self):
        resp_data = {
            "choices": [{"message": {"content": "OpenAI response"}}]
        }
        mock_resp = self._mock_urlopen(resp_data)
        with patch.dict(os.environ, {"CONSULT_OPENAI_API_KEY": "test-key"}):
            with patch.object(
                mcp_consultant.urllib.request, "urlopen", return_value=mock_resp
            ):
                result = mcp_consultant._call_openai("test", "gpt-4o", "system")
        assert result == "OpenAI response"

    def test_openai_empty_response(self):
        resp_data = {"choices": []}
        mock_resp = self._mock_urlopen(resp_data)
        with patch.dict(os.environ, {"CONSULT_OPENAI_API_KEY": "test-key"}):
            with patch.object(
                mcp_consultant.urllib.request, "urlopen", return_value=mock_resp
            ):
                result = mcp_consultant._call_openai("test", "gpt-4o", "system")
        assert "ERROR" in result

    def test_claude_ok(self):
        mock_result = MagicMock(returncode=0, stdout="Claude response\n", stderr="")
        with (
            patch.object(
                mcp_consultant.shutil, "which", return_value="C:\\fake\\claude.cmd"
            ),
            patch.object(
                mcp_consultant.subprocess, "run", return_value=mock_result
            ),
        ):
            result = mcp_consultant._call_claude("test", "", "system")
        assert result == "Claude response"

    def test_claude_with_tools(self):
        mock_result = MagicMock(returncode=0, stdout="response", stderr="")
        with (
            patch.object(
                mcp_consultant.shutil, "which", return_value="C:\\fake\\claude.cmd"
            ),
            patch.object(
                mcp_consultant.subprocess, "run", return_value=mock_result
            ) as mock_run,
        ):
            mcp_consultant._call_claude(
                "test", "model", "system", tools=["WebSearch", "WebFetch"]
            )
        cmd = mock_run.call_args[0][0]
        assert "--allowedTools" in cmd
        assert "WebSearch,WebFetch" in cmd

    def test_claude_error(self):
        mock_result = MagicMock(returncode=1, stdout="", stderr="CLI error")
        with (
            patch.object(
                mcp_consultant.shutil, "which", return_value="C:\\fake\\claude.cmd"
            ),
            patch.object(
                mcp_consultant.subprocess, "run", return_value=mock_result
            ),
        ):
            result = mcp_consultant._call_claude("test", "", "system")
        assert "ERROR" in result
        assert "CLI error" in result

    def test_claude_missing_does_not_start_subprocess(self):
        with (
            patch.object(mcp_consultant.shutil, "which", return_value=None) as which,
            patch.object(mcp_consultant.subprocess, "run") as mock_run,
        ):
            result = mcp_consultant._call_claude("test", "", "system")
        assert result == "ERROR: Claude CLI not found in PATH"
        assert which.call_count == 2
        mock_run.assert_not_called()


# ------------------------------------------------------- TestConsultFunction ---


class TestConsultFunction:
    @pytest.fixture(autouse=True)
    def _setup(self, log_dir):
        """Ensure log_dir is available for all consult tests."""
        self.log_dir = log_dir

    def test_missing_primary_precedes_gates_calls_fallback_and_logs(
        self, monkeypatch
    ):
        monkeypatch.delenv("CONSULT_BACKEND", raising=False)
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(mcp_consultant, "_check_engagement_gating", return_value=None) as engagement,
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as runtime,
            patch.object(mcp_consultant._call_counter, "can_call") as can_call,
            patch.object(mcp_consultant._call_counter, "increment") as increment,
            patch.object(mcp_consultant, "_call_ollama") as ollama,
            patch.object(mcp_consultant, "_call_claude") as claude,
            patch.object(mcp_consultant, "_call_anthropic") as anthropic,
            patch.object(mcp_consultant, "_call_openai") as openai,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
            patch.object(mcp_consultant, "_log_session_exchange") as session_log,
        ):
            result = mcp_consultant.consult(
                problem="test",
                session="new",
                image_path="missing.png",
            )

        assert result == (
            "ERROR: No consultation backend configured. "
            "Pass backend explicitly, configure a role binding, or set CONSULT_BACKEND."
        )
        for operation in (
            engagement,
            runtime,
            can_call,
            increment,
            ollama,
            claude,
            anthropic,
            openai,
            success_log,
            session_log,
        ):
            operation.assert_not_called()
        assert not self.log_dir.exists()

    def test_whitespace_configured_backend_is_absent(self, monkeypatch):
        monkeypatch.setenv("CONSULT_BACKEND", "  \t  ")
        with patch.object(
            mcp_consultant, "_check_engagement_gating", return_value=None
        ) as engagement:
            result = mcp_consultant.consult(problem="test")

        assert result == (
            "ERROR: No consultation backend configured. "
            "Pass backend explicitly, configure a role binding, or set CONSULT_BACKEND."
        )
        engagement.assert_not_called()

    def test_whitespace_argument_uses_configured_backend(self, monkeypatch):
        monkeypatch.setenv("CONSULT_BACKEND", " OLLAMA ")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_ollama", return_value="Configured response"
            ) as ollama,
        ):
            result = mcp_consultant.consult(problem="test", backend="  \t  ")

        assert "Configured response" in result
        assert gate.call_args.kwargs["requested_backend"] == "ollama"
        ollama.assert_called_once()

    def test_explicit_backend_precedes_configured_backend(self, monkeypatch):
        monkeypatch.setenv("CONSULT_BACKEND", "ollama")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_claude", return_value="Explicit response"
            ) as claude,
            patch.object(mcp_consultant, "_call_ollama") as ollama,
        ):
            result = mcp_consultant.consult(
                problem="test", backend=" CLAUDE "
            )

        assert "Explicit response" in result
        assert gate.call_args.kwargs["requested_backend"] == "claude"
        claude.assert_called_once()
        ollama.assert_not_called()

    def test_role_binding_precedes_component_environment(self, monkeypatch):
        control_dir = self.log_dir
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "cc_engagement.json").write_text(json.dumps({
            "tier": "agents",
            "specialist_paths": [{
                "role_id": "consultant_1",
                "label": "Architect Consultant",
                "path_type": "consultant",
                "active": True,
                "backend": "openai",
                "permission": "approval_required",
                "execution_mode": "cc_routed",
                "max_calls": 1,
            }],
        }), encoding="utf-8")
        (control_dir / "gateway_config.json").write_text(
            json.dumps({"backends": {"openai": {"type": "openai"}}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("CONSULT_BACKEND", "ollama")
        with patch.object(
            mcp_consultant, "_call_openai", return_value="Role response"
        ) as openai, patch.object(mcp_consultant, "_call_ollama") as ollama:
            result = mcp_consultant.consult(
                problem="test", role="architect"
            )
        assert "Role response" in result
        openai.assert_called_once()
        ollama.assert_not_called()

    def test_fallback_only_does_not_become_primary(self, monkeypatch):
        monkeypatch.delenv("CONSULT_BACKEND", raising=False)
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(mcp_consultant, "_call_anthropic") as fallback,
        ):
            result = mcp_consultant.consult(problem="test")

        assert result == (
            "ERROR: No consultation backend configured. "
            "Pass backend explicitly, configure a role binding, or set CONSULT_BACKEND."
        )
        gate.assert_not_called()
        fallback.assert_not_called()

    def test_unsupported_backend_rejected_before_runtime_gate(self):
        with (
            patch.object(mcp_consultant, "_check_runtime_specialist_gate") as gate,
            patch.object(mcp_consultant, "_call_ollama") as ollama,
            patch.object(mcp_consultant, "_call_claude") as claude,
            patch.object(mcp_consultant, "_call_anthropic") as anthropic,
            patch.object(mcp_consultant, "_call_openai") as openai,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(
                problem="test", backend=" Unsupported_Backend "
            )
        assert result == (
            "ERROR: Unsupported consultation backend 'unsupported_backend'."
        )
        gate.assert_not_called()
        for adapter in (ollama, claude, anthropic, openai):
            adapter.assert_not_called()
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_explicit_backend_is_normalized_before_runtime_gate(self):
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_ollama", return_value="Normalized response"
            ) as ollama,
        ):
            result = mcp_consultant.consult(
                problem="test", backend=" OLLAMA "
            )
        assert "Normalized response" in result
        assert gate.call_args.kwargs["requested_backend"] == "ollama"
        ollama.assert_called_once()

    def test_runtime_gate_backend_is_validated_before_dispatch(self):
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                return_value={
                    "allowed": True,
                    "backend": "unsupported_gateway_backend",
                    "model": "test-model",
                },
            ),
            patch.object(mcp_consultant, "_call_ollama") as ollama,
            patch.object(mcp_consultant, "_call_claude") as claude,
            patch.object(mcp_consultant, "_call_anthropic") as anthropic,
            patch.object(mcp_consultant, "_call_openai") as openai,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert "unsupported_gateway_backend" in result
        assert "runtime gate" in result
        for adapter in (ollama, claude, anthropic, openai):
            adapter.assert_not_called()
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_basic_consult(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="Analysis result"
        ):
            result = mcp_consultant.consult(problem="What is wrong?")
        assert "Analysis result" in result

    def test_missing_config_defaults_active_when_control_plane_available(self):
        config_path = mcp_consultant.PROJECT_ROOT / ".controlcoding" / "cc_engagement.json"
        assert not config_path.exists()
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="Analysis result"
        ) as mock_call:
            result = mcp_consultant.consult(problem="What is wrong?")
        assert "Analysis result" in result
        mock_call.assert_called_once()

    def test_engagement_gate_fails_closed_when_control_plane_utils_unavailable(self):
        with patch.dict(sys.modules, {"control_plane_utils": None}):
            with patch.object(
                mcp_consultant,
                "_call_ollama",
                side_effect=AssertionError("backend should not run"),
            ) as mock_call:
                result = mcp_consultant.consult(problem="test")
        assert result.startswith("[GATED]")
        assert "control_plane_utils_unavailable" in result
        mock_call.assert_not_called()

    def test_runtime_gate_fails_closed_when_control_plane_utils_unavailable(self):
        with patch.dict(sys.modules, {"control_plane_utils": None}):
            gate = mcp_consultant._check_runtime_specialist_gate(
                role_ref="architect",
                component="consultant",
                requested_backend="claude",
            )
        assert gate["allowed"] is False
        assert gate["reason"] == "control_plane_utils_unavailable"

    def test_runtime_gate_exception_blocks_backend(self):
        with patch(
            "control_plane_utils.check_specialist_runtime",
            side_effect=RuntimeError("boom"),
        ):
            with patch.object(
                mcp_consultant,
                "_call_ollama",
                side_effect=AssertionError("backend should not run"),
            ) as mock_call:
                result = mcp_consultant.consult(problem="test")
        assert result.startswith("[GATED]")
        assert "governance_check_failed" in result
        mock_call.assert_not_called()

    def test_prompt_too_large(self):
        original = mcp_consultant.MAX_PROMPT_CHARS
        mcp_consultant.MAX_PROMPT_CHARS = 10
        try:
            result = mcp_consultant.consult(problem="x" * 100)
            assert "ERROR" in result
            assert "too large" in result
        finally:
            mcp_consultant.MAX_PROMPT_CHARS = original

    def test_call_limit_reached(self):
        mcp_consultant._call_counter = mcp_consultant._CallCounter(1)
        mcp_consultant._call_counter.increment()  # exhaust the limit
        mcp_consultant.MAX_CALLS = 1
        try:
            result = mcp_consultant.consult(problem="test")
            assert "ERROR" in result
            assert "limit reached" in result
        finally:
            mcp_consultant.MAX_CALLS = 0

    def test_new_session(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="Session response"
        ):
            result = mcp_consultant.consult(
                problem="Debug this", session="new"
            )
        assert "Session" in result
        assert "debug-001" in result
        assert "Exchange 1" in result

    def test_domain_specialization(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="Domain answer"
        ) as mock_call:
            mcp_consultant.consult(
                problem="test", domain="3D graphics"
            )
        # The system prompt should contain domain info
        call_args = mock_call.call_args
        system_prompt = call_args[1].get("system_prompt") or call_args[0][2]
        assert "3D graphics" in system_prompt

    def test_role_selection(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="Review result"
        ) as mock_call:
            mcp_consultant.consult(
                problem="Check this code", role="reviewer"
            )
        call_args = mock_call.call_args
        system_prompt = call_args[1].get("system_prompt") or call_args[0][2]
        assert "code reviewer" in system_prompt

    def test_invalid_role_defaults_to_debug(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="Debug result"
        ) as mock_call:
            mcp_consultant.consult(
                problem="test", role="nonexistent"
            )
        call_args = mock_call.call_args
        system_prompt = call_args[1].get("system_prompt") or call_args[0][2]
        assert "debugging consultant" in system_prompt

    def test_fallback_backend(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_ollama",
                side_effect=ConnectionError("no ollama"),
            ),
            patch.object(
                mcp_consultant, "_call_anthropic",
                return_value="Fallback response",
            ),
        ):
            result = mcp_consultant.consult(problem="test")
        assert result.startswith("[Fallback: anthropic]")
        assert "Fallback response" in result
        assert gate.call_count == 2
        assert mcp_consultant._call_counter.count == 1

    def test_error_response_without_fallback_is_not_success(self):
        with (
            patch.object(
                mcp_consultant, "_call_ollama",
                return_value="  ERROR: provider unavailable",
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert result == (
            "ERROR: Consultation backend 'ollama' reported an operational failure."
        )
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_ollama_connection_failure_keeps_safe_diagnostic(self):
        with (
            patch.object(
                mcp_consultant,
                "_call_ollama",
                side_effect=ConnectionError("sensitive connection detail"),
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert result == (
            f"ERROR: Cannot connect to Ollama at {mcp_consultant.OLLAMA_URL}. "
            "Ensure Ollama is running and the configured model is available."
        )
        assert "sensitive connection detail" not in result
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    @pytest.mark.parametrize(
        ("backend", "adapter_name", "expected"),
        [
            ("anthropic", "_call_anthropic", "Cannot connect to Anthropic API"),
            (
                "openai",
                "_call_openai",
                "Cannot connect to the configured OpenAI-compatible API",
            ),
        ],
    )
    def test_api_connection_failure_keeps_safe_diagnostic(
        self, backend, adapter_name, expected
    ):
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ),
            patch.object(
                mcp_consultant,
                adapter_name,
                side_effect=mcp_consultant.urllib.error.URLError(
                    "sensitive API detail"
                ),
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test", backend=backend)
        assert expected in result
        assert "sensitive API detail" not in result
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_missing_claude_without_fallback_preserves_diagnostic(self):
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ),
            patch.object(mcp_consultant.shutil, "which", return_value=None),
            patch.object(mcp_consultant.subprocess, "run") as subprocess_run,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test", backend="claude")
        assert result == "ERROR: Claude CLI not found in PATH"
        subprocess_run.assert_not_called()
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    @pytest.mark.parametrize("invalid_response", [None, "", "   \t\n"])
    def test_invalid_response_shapes_are_not_success(
        self, invalid_response
    ):
        with (
            patch.object(
                mcp_consultant, "_call_ollama", return_value=invalid_response
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert "returned an invalid response" in result
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_error_response_uses_authorized_fallback(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: unavailable"
            ),
            patch.object(
                mcp_consultant, "_call_anthropic",
                return_value="Fallback response",
            ) as fallback,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert result.startswith("[Fallback: anthropic]\n\n")
        fallback.assert_called_once()
        assert gate.call_count == 2
        assert gate.call_args_list[1].kwargs == {
            "role_ref": "debug",
            "component": "consultant",
            "requested_backend": "anthropic",
            "requested_model": mcp_consultant.DEFAULT_ANTHROPIC_MODEL,
        }
        assert mcp_consultant._call_counter.count == 1
        success_log.assert_called_once()
        assert success_log.call_args.args[3] == "anthropic"
        assert success_log.call_args.args[4] == mcp_consultant.DEFAULT_ANTHROPIC_MODEL

    def test_fallback_denied_by_runtime_gate(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        gate_results = [
            _allow_requested_backend_gate("debug", requested_backend="ollama"),
            {
                "allowed": False,
                "reason": "test_denied",
                "detail": "Fallback denied by test gate.",
                "backend": "anthropic",
                "model": "",
            },
        ]
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=gate_results,
            ),
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: unavailable"
            ),
            patch.object(mcp_consultant, "_call_anthropic") as fallback,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert "denied by runtime gate" in result
        fallback.assert_not_called()
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_fallback_gate_cannot_rewrite_backend(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        gate_results = [
            _allow_requested_backend_gate("debug", requested_backend="ollama"),
            {
                "allowed": True,
                "reason": "test_rewrite",
                "detail": "Rewritten by test gate.",
                "backend": "openai",
                "model": "test-model",
            },
        ]
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=gate_results,
            ),
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: unavailable"
            ),
            patch.object(mcp_consultant, "_call_anthropic") as anthropic,
            patch.object(mcp_consultant, "_call_openai") as openai,
        ):
            result = mcp_consultant.consult(problem="test")
        assert "rewrote fallback backend 'anthropic' to 'openai'" in result
        anthropic.assert_not_called()
        openai.assert_not_called()
        assert mcp_consultant._call_counter.count == 0

    def test_fallback_gate_exception_blocks_adapter(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=[
                    _allow_requested_backend_gate(
                        "debug", requested_backend="ollama"
                    ),
                    RuntimeError("gate failure"),
                ],
            ),
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: unavailable"
            ),
            patch.object(mcp_consultant, "_call_anthropic") as fallback,
        ):
            result = mcp_consultant.consult(problem="test")
        assert "Runtime gate failed for fallback backend 'anthropic'" in result
        fallback.assert_not_called()
        assert mcp_consultant._call_counter.count == 0

    def test_primary_and_fallback_invalid_responses_are_not_success(
        self, monkeypatch
    ):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ),
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: primary"
            ),
            patch.object(
                mcp_consultant, "_call_anthropic", return_value="   "
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert "Primary backend (ollama) and fallback (anthropic) both failed" in result
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_unexpected_fallback_exception_is_not_masked(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ),
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: primary"
            ),
            patch.object(
                mcp_consultant,
                "_call_anthropic",
                side_effect=ValueError("sensitive fallback detail"),
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert result == (
            "ERROR: Fallback consultation backend 'anthropic' failed "
            "unexpectedly (ValueError)."
        )
        assert "sensitive fallback detail" not in result
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_unknown_fallback_is_rejected_before_gate(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "unknown")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: unavailable"
            ),
            patch.object(mcp_consultant, "_call_anthropic") as anthropic,
            patch.object(mcp_consultant, "_call_openai") as openai,
            patch.object(mcp_consultant, "_call_claude") as claude,
        ):
            result = mcp_consultant.consult(problem="test")
        assert result == (
            "ERROR: Unsupported consultation fallback backend 'unknown'."
        )
        assert gate.call_count == 1
        for adapter in (anthropic, openai, claude):
            adapter.assert_not_called()
        assert mcp_consultant._call_counter.count == 0

    def test_missing_claude_uses_authorized_fallback(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "ollama")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(mcp_consultant.shutil, "which", return_value=None),
            patch.object(mcp_consultant.subprocess, "run") as subprocess_run,
            patch.object(
                mcp_consultant, "_call_ollama", return_value="Fallback response"
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(
                problem="test", backend="claude"
            )
        assert result.startswith("[Fallback: ollama]")
        subprocess_run.assert_not_called()
        assert gate.call_count == 2
        assert mcp_consultant._call_counter.count == 1
        assert success_log.call_args.args[3] == "ollama"

    def test_unexpected_exception_does_not_use_fallback(self, monkeypatch):
        monkeypatch.setattr(mcp_consultant, "FALLBACK_BACKEND", "anthropic")
        with (
            patch.object(
                mcp_consultant,
                "_check_runtime_specialist_gate",
                side_effect=_allow_requested_backend_gate,
            ) as gate,
            patch.object(
                mcp_consultant, "_call_ollama", side_effect=ValueError("boom")
            ),
            patch.object(mcp_consultant, "_call_anthropic") as fallback,
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(problem="test")
        assert result == (
            "ERROR: Consultation backend 'ollama' failed unexpectedly "
            "(ValueError)."
        )
        assert "boom" not in result
        assert gate.call_count == 1
        fallback.assert_not_called()
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_routing_event_is_attempt_not_success(self):
        control_dir = self.log_dir
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "cc_engagement.json").write_text(
            json.dumps({
                "tier": "agents",
                "specialist_paths": [{
                    "role_id": "consultant_1",
                    "label": "Architect Consultant",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "ollama",
                    "model": "test-model",
                    "permission": "approval_required",
                    "execution_mode": "cc_routed",
                    "max_calls": 5,
                }],
            }),
            encoding="utf-8",
        )
        (control_dir / "gateway_config.json").write_text(
            json.dumps({"backends": {"ollama": {"type": "local"}}}),
            encoding="utf-8",
        )
        with (
            patch.object(
                mcp_consultant, "_call_ollama", return_value="ERROR: unavailable"
            ),
            patch.object(mcp_consultant, "_log_consultation") as success_log,
        ):
            result = mcp_consultant.consult(
                problem="test", role="architect"
            )
        events = [
            json.loads(line)
            for line in (control_dir / "event_log.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        assert any(event.get("event") == "llm_call_routed" for event in events)
        assert "reported an operational failure" in result
        assert mcp_consultant._call_counter.count == 0
        success_log.assert_not_called()

    def test_image_not_found(self):
        result = mcp_consultant.consult(
            problem="test", image_path="/nonexistent/img.png"
        )
        assert "ERROR" in result
        assert "not found" in result

    def test_code_snippets_included(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="ok"
        ) as mock_call:
            mcp_consultant.consult(
                problem="test", code_snippets="def foo(): pass"
            )
        prompt = mock_call.call_args[0][0]
        assert "def foo(): pass" in prompt
        assert "Relevant Code" in prompt

    def test_tried_already_included(self):
        with patch.object(
            mcp_consultant, "_call_ollama", return_value="ok"
        ) as mock_call:
            mcp_consultant.consult(
                problem="test", tried_already="Tried X, did not work"
            )
        prompt = mock_call.call_args[0][0]
        assert "Tried X" in prompt
        assert "Already Tried" in prompt

    def test_runtime_gate_blocks_human_mediated_consult(self, tmp_path):
        original_root = mcp_consultant.PROJECT_ROOT
        try:
            mcp_consultant.PROJECT_ROOT = tmp_path
            control_dir = tmp_path / ".controlcoding"
            control_dir.mkdir(parents=True, exist_ok=True)
            (control_dir / "cc_engagement.json").write_text(
                json.dumps({
                    "tier": "agents",
                    "specialist_paths": [{
                        "role_id": "consultant_1",
                        "label": "Architect Consultant",
                        "path_type": "consultant",
                        "active": True,
                        "backend": "anthropic_prod",
                        "model": "claude-sonnet",
                        "permission": "user_mediated",
                        "execution_mode": "human_mediated",
                        "max_calls": 1,
                    }],
                }),
                encoding="utf-8",
            )
            with patch.object(mcp_consultant, "_call_ollama", return_value="should not run") as mock_call:
                result = mcp_consultant.consult(problem="test", role="architect")
            assert result.startswith("[GATED]")
            assert "consult-packet create" in result
            mock_call.assert_not_called()
        finally:
            mcp_consultant.PROJECT_ROOT = original_root
