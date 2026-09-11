#!/usr/bin/env python3
"""Tests for cc_dashboard.py - ControlCoding Web UI Dashboard."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock gradio before importing cc_dashboard
sys.modules["gradio"] = MagicMock()
sys.modules["gradio.themes"] = MagicMock()

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
)
if "cc_dashboard" in sys.modules:
    del sys.modules["cc_dashboard"]
import cc_dashboard


# ------------------------------------------------------------------ helpers ---


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
    )


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ----------------------------------------------------------- TestLoadHelpers ---


class TestLoadHelpers:
    def test_load_jsonl_valid(self, tmp_path):
        p = tmp_path / "data.jsonl"
        entries = [{"a": 1}, {"b": 2}]
        _write_jsonl(p, entries)
        result = cc_dashboard.load_jsonl(p)
        assert result == entries

    def test_load_jsonl_missing(self, tmp_path):
        assert cc_dashboard.load_jsonl(tmp_path / "nope.jsonl") == []

    def test_load_jsonl_corrupt_lines(self, tmp_path):
        p = tmp_path / "mixed.jsonl"
        p.write_text('{"ok":1}\nBAD LINE\n{"ok":2}\n', encoding="utf-8")
        result = cc_dashboard.load_jsonl(p)
        assert len(result) == 2
        assert result[0] == {"ok": 1}
        assert result[1] == {"ok": 2}

    def test_load_json_valid(self, tmp_path):
        p = tmp_path / "data.json"
        _write_json(p, {"key": "val"})
        assert cc_dashboard.load_json(p) == {"key": "val"}

    def test_load_json_missing(self, tmp_path):
        assert cc_dashboard.load_json(tmp_path / "nope.json") == {}

    def test_load_md_missing(self, tmp_path):
        result = cc_dashboard.load_md(tmp_path / "nope.md")
        assert result == "*File not found.*"

    def test_load_md_valid(self, tmp_path):
        p = tmp_path / "status.md"
        p.write_text("# Hello", encoding="utf-8")
        assert cc_dashboard.load_md(p) == "# Hello"


# --------------------------------------------------------- TestRenderAgents ---


class TestRenderAgents:
    def test_no_agents(self, tmp_path):
        result = cc_dashboard.render_agents(tmp_path)
        assert "No agents registered" in result

    def test_renders_table(self, tmp_path):
        _write_json(
            tmp_path / "agents.json",
            {
                "coder": {"last_seen": "2026-03-01T10:00:00Z", "status": "online"},
                "helper": {"last_seen": "2026-03-01T09:00:00Z", "status": "offline"},
            },
        )
        result = cc_dashboard.render_agents(tmp_path)
        assert "coder" in result
        assert "helper" in result
        assert "online" in result
        assert "| Agent |" in result


# --------------------------------------------------------- TestRenderBridge ---


class TestRenderBridge:
    def test_no_messages_dir(self, tmp_path):
        result = cc_dashboard.render_bridge(tmp_path)
        assert "No bridge messages directory" in result

    def test_renders_messages(self, tmp_path):
        msg_dir = tmp_path / "messages"
        msg_dir.mkdir(parents=True)
        _write_json(
            msg_dir / "001_msg.json",
            {
                "id": "001",
                "from": "coder",
                "to": "helper",
                "timestamp": "2026-03-01T10:00:00Z",
                "status": "pending",
                "content": "Fix the bug please",
            },
        )
        result = cc_dashboard.render_bridge(tmp_path)
        assert "coder" in result
        assert "helper" in result
        assert "Fix the bug" in result

    def test_filter_by_agent(self, tmp_path):
        msg_dir = tmp_path / "messages"
        msg_dir.mkdir(parents=True)
        _write_json(
            msg_dir / "001_msg.json",
            {
                "id": "001",
                "from": "coder",
                "to": "helper",
                "timestamp": "2026-03-01T10:00:00Z",
                "status": "pending",
                "content": "msg1",
            },
        )
        _write_json(
            msg_dir / "002_msg.json",
            {
                "id": "002",
                "from": "analyst",
                "to": "reviewer",
                "timestamp": "2026-03-01T11:00:00Z",
                "status": "pending",
                "content": "msg2",
            },
        )
        result = cc_dashboard.render_bridge(tmp_path, filter_agent="coder")
        assert "coder" in result
        assert "analyst" not in result


# -------------------------------------------------------- TestRenderSession ---


class TestRenderSession:
    def test_no_status(self, tmp_path):
        status, devlogs, history = cc_dashboard.render_session(tmp_path)
        assert "*File not found.*" in status

    def test_with_status_and_devlogs(self, tmp_path):
        (tmp_path / "STATUS.md").write_text("# Active", encoding="utf-8")
        devlog_dir = tmp_path / "devlog"
        devlog_dir.mkdir()
        (devlog_dir / "2026-03-01_001_init.md").write_text(
            "# Init devlog", encoding="utf-8"
        )
        status, devlogs, history = cc_dashboard.render_session(tmp_path)
        assert "Active" in status
        assert "Init devlog" in devlogs

    def test_with_history(self, tmp_path):
        (tmp_path / "STATUS.md").write_text("ok", encoding="utf-8")
        (tmp_path / "STATUS_HISTORY.md").write_text(
            "## Entry 1\nOld status", encoding="utf-8"
        )
        status, devlogs, history = cc_dashboard.render_session(tmp_path)
        assert "Old status" in history


# ---------------------------------------------------------- TestRenderDebug ---


class TestRenderDebug:
    def test_no_data(self, tmp_path):
        consult_md, violations_md, screenshots = cc_dashboard.render_debug(tmp_path)
        assert "*No consultations recorded.*" in consult_md
        assert "*No violations recorded.*" in violations_md
        assert screenshots == []

    def test_with_consultations(self, tmp_path):
        _write_jsonl(
            tmp_path / ".controlcoding" / "consult_log.jsonl",
            [
                {
                    "timestamp": "2026-03-01T10:00:00Z",
                    "role": "debug",
                    "backend": "ollama",
                    "prompt_chars": 500,
                    "problem_summary": "Null pointer in renderer",
                },
            ],
        )
        consult_md, _, _ = cc_dashboard.render_debug(tmp_path)
        assert "debug" in consult_md
        assert "ollama" in consult_md
        assert "Null pointer" in consult_md

    def test_with_violations(self, tmp_path):
        _write_jsonl(
            tmp_path / ".controlcoding" / "codewarden_violations.jsonl",
            [
                {
                    "timestamp": "2026-03-01T10:00:00Z",
                    "file": "src/core/engine.py",
                    "rule": "stable zone boundary",
                    "severity": "DENY",
                },
            ],
        )
        _, violations_md, _ = cc_dashboard.render_debug(tmp_path)
        assert "engine.py" in violations_md
        assert "DENY" in violations_md


# -------------------------------------------------------- TestRenderMetrics ---


class TestRenderMetrics:
    def test_no_ops_log(self, tmp_path):
        result = cc_dashboard.render_metrics(tmp_path)
        assert "*No operations log found.*" in result

    def test_operations_by_tool(self, tmp_path):
        _write_jsonl(
            tmp_path / ".controlcoding" / "ops_log.jsonl",
            [
                {"tool_name": "Edit", "file": "a.py"},
                {"tool_name": "Edit", "file": "b.py"},
                {"tool_name": "Write", "file": "c.py"},
            ],
        )
        result = cc_dashboard.render_metrics(tmp_path)
        assert "Edit" in result
        assert "Write" in result
        assert "Total operations" in result

    def test_stuck_loop_detection(self, tmp_path):
        # 6 consecutive edits to the same file
        ops = [{"tool_name": "Edit", "file": "stuck.py"} for _ in range(6)]
        _write_jsonl(tmp_path / ".controlcoding" / "ops_log.jsonl", ops)
        result = cc_dashboard.render_metrics(tmp_path)
        assert "Stuck" in result
        assert "stuck.py" in result

    def test_consultation_summary(self, tmp_path):
        _write_jsonl(
            tmp_path / ".controlcoding" / "ops_log.jsonl",
            [{"tool_name": "Edit", "file": "a.py"}],
        )
        _write_jsonl(
            tmp_path / ".controlcoding" / "consult_log.jsonl",
            [
                {"role": "debug"},
                {"role": "debug"},
                {"role": "architect"},
            ],
        )
        result = cc_dashboard.render_metrics(tmp_path)
        assert "Consultations" in result
        assert "debug" in result
        assert "architect" in result
