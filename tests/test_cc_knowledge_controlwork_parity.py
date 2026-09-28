"""Live cross-product Work Plane parity smoke against the installed source trees."""

from __future__ import annotations

import json

import pytest

from knowledge_controlwork_parity import WORK, execute


pytestmark = pytest.mark.skipif(
    not (WORK / "scripts/cw.py").is_file(),
    reason="separate ControlWork source checkout is unavailable",
)


def test_shared_portable_operations_and_standalone_import(tmp_path):
    receipt = execute(tmp_path)
    assert len(receipt["sourceHashesSha256"]) >= 7
    assert receipt["sourceStable"] is True
    assert receipt["contractMatch"] is True
    assert receipt["results"]["standalone_import"]["pass"] is True
    assert all(item["pass"] for item in receipt["results"].values()), json.dumps(receipt["results"], indent=2)
    assert receipt["results"]["capture_scan_refresh"]["indexChanged"] == [True, True]
    assert receipt["results"]["scan_records"]["pass"] is True
    assert receipt["pass"] is True
    # The two products intentionally write different initial CONTROLWORK.md
    # templates; the receipt keeps this visible rather than silently discarding it.
    assert "init_context" in receipt["profileVariances"]
    assert "default_query" in receipt["profileVariances"]


def test_fixture_drift_is_reported(tmp_path):
    receipt = execute(tmp_path, altered_embedded_fixture=True)
    assert receipt["pass"] is False
    assert receipt["results"]["source_fixture"]["pass"] is False
