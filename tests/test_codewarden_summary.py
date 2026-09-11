#!/usr/bin/env python3
"""Tests for scripts/codewarden_summary.py."""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import codewarden_summary


def test_control_plane_store_path_prefers_canonical(tmp_path):
    canonical = tmp_path / ".controlcoding" / "codewarden_violations.jsonl"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    legacy = tmp_path / ".claude" / "codewarden_violations.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")

    assert codewarden_summary.control_plane_store_path(tmp_path) == canonical


def test_control_plane_store_path_falls_back_to_legacy(tmp_path):
    legacy = tmp_path / ".claude" / "codewarden_violations.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")

    assert codewarden_summary.control_plane_store_path(tmp_path) == legacy


def test_read_violations_reads_canonical_store(tmp_path):
    canonical = tmp_path / ".controlcoding" / "codewarden_violations.jsonl"
    canonical.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    payload = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "severity": "warn",
        "hook": "review",
        "rule_quoted": "Keep boundaries explicit",
        "file": "src/module.py",
    }
    canonical.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    rows = codewarden_summary.read_violations(canonical, now - timedelta(days=1))

    assert len(rows) == 1
    assert rows[0]["file"] == "src/module.py"


def test_generate_report_groups_structured_architecture_findings(tmp_path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    violations = [
        {
            "timestamp": now,
            "severity": "high",
            "hook": "review",
            "rule_quoted": "shared cannot import features",
            "file": "shared/api.py",
            "category": "architecture",
            "architecture_tag": "layer_violation",
            "evidence_source": "fitness_check",
        },
        {
            "timestamp": now,
            "severity": "medium",
            "hook": "review",
            "rule_quoted": "tests missing",
            "file": "tests/test_api.py",
            "category": "test_gap",
        },
    ]

    report = codewarden_summary.generate_report(
        violations,
        30,
        tmp_path / ".controlcoding" / "codewarden_violations.jsonl",
    )

    assert "## Structured Review Signals" in report
    assert "| architecture | 1 |" in report
    assert "| test gap | 1 |" in report
    assert "### Architecture Tags" in report
    assert "| layer violation | 1 |" in report
    assert "fitness_check" in report
