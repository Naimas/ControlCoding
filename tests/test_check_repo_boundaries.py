import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "hooks"))
if "check_repo_boundaries" in sys.modules:
    del sys.modules["check_repo_boundaries"]
import check_repo_boundaries  # noqa: E402


def test_evaluate_staged_paths_blocks_deny_zone():
    deny_hits, warn_hits = check_repo_boundaries.evaluate_staged_paths(
        ["src/core/engine.py"],
        [{"path": "src/core/", "level": "deny", "description": "Core"}],
    )

    assert len(deny_hits) == 1
    assert deny_hits[0]["file"] == "src/core/engine.py"
    assert deny_hits[0]["zone"] == "src/core/"
    assert warn_hits == []


def test_evaluate_staged_paths_normalizes_traversal_segments():
    deny_hits, warn_hits = check_repo_boundaries.evaluate_staged_paths(
        ["src/public/../core/engine.py"],
        [{"path": "src/core/", "level": "deny", "description": "Core"}],
    )

    assert len(deny_hits) == 1
    assert deny_hits[0]["file"] == "src/public/../core/engine.py"
    assert deny_hits[0]["zone"] == "src/core/"
    assert warn_hits == []


def test_evaluate_staged_paths_warns_on_warn_zone_without_blocking():
    deny_hits, warn_hits = check_repo_boundaries.evaluate_staged_paths(
        ["src/shared/utils.py"],
        [{"path": "src/shared/", "level": "warn", "description": "Shared"}],
    )

    assert deny_hits == []
    assert len(warn_hits) == 1
    assert warn_hits[0]["file"] == "src/shared/utils.py"


def test_evaluate_staged_paths_respects_approved_lift():
    deny_hits, warn_hits = check_repo_boundaries.evaluate_staged_paths(
        ["src/core/engine.py"],
        [{"path": "src/core/", "level": "deny", "description": "Core"}],
        approved_lifts={"src/core"},
    )

    assert deny_hits == []
    assert warn_hits == []


def test_evaluate_staged_paths_reports_consumed_lift():
    consumed = set()
    deny_hits, warn_hits = check_repo_boundaries.evaluate_staged_paths(
        ["src/core/engine.py"],
        [{"path": "src/core/", "level": "deny", "description": "Core"}],
        approved_lifts={"src/core"},
        consumed_lifts=consumed,
    )

    assert deny_hits == []
    assert warn_hits == []
    assert consumed == {"src/core"}


def test_consume_approved_lifts_marks_request_consumed(tmp_path):
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    lift_path = control_dir / "lift_request.json"
    lift_path.write_text(
        json.dumps({
            "status": "APPROVED",
            "zones": ["src/core/"],
            "expires_at": expires,
        }),
        encoding="utf-8",
    )

    check_repo_boundaries._consume_approved_lifts(tmp_path, {"src/core"})

    payload = json.loads(lift_path.read_text(encoding="utf-8"))
    assert payload["status"] == "CONSUMED"
    assert payload["zones"] == []
    assert payload["consumed_at"]


def test_main_blocks_deny_zone_with_exit_two(monkeypatch, tmp_path):
    monkeypatch.setattr(check_repo_boundaries, "_find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        check_repo_boundaries,
        "_load_protected_zones",
        lambda _root: [{"path": "src/core/", "level": "deny", "description": "Core"}],
    )
    monkeypatch.setattr(
        check_repo_boundaries,
        "_staged_paths",
        lambda _root: ["src/core/engine.py"],
    )

    assert check_repo_boundaries.main() == 2


def test_main_git_inspection_failure_returns_exit_two(monkeypatch, tmp_path):
    def fail_staged_paths(_root):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(check_repo_boundaries, "_find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        check_repo_boundaries,
        "_load_protected_zones",
        lambda _root: [{"path": "src/core/", "level": "deny", "description": "Core"}],
    )
    monkeypatch.setattr(check_repo_boundaries, "_staged_paths", fail_staged_paths)

    assert check_repo_boundaries.main() == 2


@pytest.mark.parametrize(
    ("protected_zones", "staged_paths", "expected"),
    [
        ([], ["src/core/engine.py"], 0),
        ([{"path": "src/core/", "level": "deny"}], [], 0),
        ([{"path": "src/shared/", "level": "warn"}], ["src/shared/utils.py"], 0),
        ([{"path": "src/core/", "level": "deny"}], ["src/core/engine.py"], 2),
    ],
)
def test_main_never_returns_exit_one_for_boundary_cases(
    monkeypatch,
    tmp_path,
    protected_zones,
    staged_paths,
    expected,
):
    monkeypatch.setattr(check_repo_boundaries, "_find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        check_repo_boundaries,
        "_load_protected_zones",
        lambda _root: protected_zones,
    )
    monkeypatch.setattr(
        check_repo_boundaries,
        "_staged_paths",
        lambda _root: staged_paths,
    )

    result = check_repo_boundaries.main()
    assert result == expected
    assert result in (0, 2)
