#!/usr/bin/env python3
"""Tests for templates/hooks/ops_logger.py."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "hooks"))
import ops_logger


def test_control_plane_log_path_prefers_canonical(tmp_path):
    canonical_dir = tmp_path / ".controlcoding"
    canonical_dir.mkdir()
    legacy_dir = tmp_path / ".claude"
    legacy_dir.mkdir()

    assert ops_logger.control_plane_log_path(tmp_path) == canonical_dir / "ops_log.jsonl"


def test_control_plane_log_path_falls_back_to_legacy(tmp_path):
    legacy_dir = tmp_path / ".claude"
    legacy_dir.mkdir()

    assert ops_logger.control_plane_log_path(tmp_path) == legacy_dir / "ops_log.jsonl"


def test_append_log_entry_writes_canonical_log(tmp_path):
    entry = {"tool": "Write", "action": "write", "file": "src/module.py"}

    ops_logger.append_log_entry(tmp_path, entry)

    log_path = tmp_path / ".controlcoding" / "ops_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["file"] == "src/module.py"
