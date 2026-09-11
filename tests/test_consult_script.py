"""Tests for consult.py runtime specialist gating."""

from contextlib import contextmanager
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import consult as consult_script


def _argv(*extra):
    return ["consult.py", "--prompt", "test prompt", *extra]


@contextmanager
def _patched_backends(result="backend result"):
    with patch.object(consult_script, "call_ollama", return_value=result) as mock_ollama, \
         patch.object(consult_script, "call_openai", return_value=result) as mock_openai, \
         patch.object(consult_script, "call_anthropic", return_value=result) as mock_anthropic, \
         patch.object(consult_script, "call_claude", return_value=result) as mock_claude:
        yield mock_ollama, mock_openai, mock_anthropic, mock_claude


def _assert_backends_not_called(*mocks):
    for mock in mocks:
        mock.assert_not_called()


def test_main_requires_explicit_backend(capsys):
    with patch.object(sys, "argv", _argv()), \
         patch.object(consult_script, "_check_runtime_specialist_gate") as mock_gate, \
         _patched_backends() as backend_mocks, \
         pytest.raises(SystemExit) as exc_info:
        consult_script.main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "the following arguments are required: --backend" in captured.err
    mock_gate.assert_not_called()
    _assert_backends_not_called(*backend_mocks)


def test_main_rejects_unknown_runtime_gate_backend(capsys):
    gate = {
        "allowed": True,
        "backend": " Unknown-Provider ",
        "model": "test-model",
    }
    with patch.object(sys, "argv", _argv("--backend", "ollama")), \
         patch.object(
             consult_script,
             "_check_runtime_specialist_gate",
             return_value=gate,
         ), \
         _patched_backends() as backend_mocks:
        exit_code = consult_script.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "runtime gate returned an unsupported consultation backend" in out
    _assert_backends_not_called(*backend_mocks)


def test_main_gates_when_control_plane_utils_unavailable(capsys):
    with patch.dict(sys.modules, {"control_plane_utils": None}), \
         patch.object(sys, "argv", _argv("--backend", "ollama")), \
         _patched_backends() as backend_mocks:
        consult_script.main()

    out = capsys.readouterr().out
    assert "[GATED] control_plane_utils_unavailable:" in out
    _assert_backends_not_called(*backend_mocks)


def test_main_gates_when_runtime_governance_check_fails(capsys):
    with patch(
        "control_plane_utils.check_specialist_runtime",
        side_effect=RuntimeError("boom"),
    ), \
         patch.object(sys, "argv", _argv("--backend", "ollama")), \
         _patched_backends() as backend_mocks:
        consult_script.main()

    out = capsys.readouterr().out
    assert "[GATED] governance_check_failed:" in out
    _assert_backends_not_called(*backend_mocks)


def test_main_explicit_backend_works_without_project_config(
        tmp_path, capsys):
    config_path = tmp_path / ".controlcoding" / "cc_engagement.json"
    assert not config_path.exists()
    with patch.dict(os.environ, {"SESSION_PROJECT_ROOT": str(tmp_path)}, clear=False), \
         patch.object(sys, "argv", _argv("--backend", "ollama")), \
         patch.object(consult_script, "call_ollama",
                      return_value="Analysis result") as mock_ollama:
        consult_script.main()

    out = capsys.readouterr().out
    assert "Analysis result" in out
    mock_ollama.assert_called_once()


@pytest.mark.parametrize("backend", [" ", "fallback", "unknown-provider"])
def test_main_rejects_invalid_backend_before_gate_or_adapter(backend, capsys):
    with patch.object(sys, "argv", _argv("--backend", backend)), \
         patch.object(consult_script, "_check_runtime_specialist_gate") as gate, \
         _patched_backends() as backend_mocks, \
         pytest.raises(SystemExit) as exc_info:
        consult_script.main()
    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    gate.assert_not_called()
    _assert_backends_not_called(*backend_mocks)


def test_main_blocks_human_mediated_runtime_path(tmp_path, capsys):
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
    with patch.dict(os.environ, {"SESSION_PROJECT_ROOT": str(tmp_path)}, clear=False), \
         patch.object(sys, "argv", _argv("--role", "architect",
                                          "--backend", "ollama")), \
         patch.object(consult_script, "call_ollama") as mock_call:
        consult_script.main()
    out = capsys.readouterr().out
    assert "[GATED] human_mediated_only:" in out
    assert "consult-packet create" in out
    mock_call.assert_not_called()
