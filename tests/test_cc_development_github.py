"""Offline GitHub reader tests: local processes, no authenticated API calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_development_github as github


HEAD, BASE = "a" * 40, "b" * 40
REPO = "example/project"
PR_ROUTE = f"repos/{REPO}/pulls/7"
COMMIT_ROUTE = f"repos/{REPO}/commits/{HEAD}"


def request():
    return {"schemaVersion": 1, "candidateDigest": "c" * 64, "repository": REPO,
            "number": 7, "baseRef": "main", "base": BASE, "head": HEAD,
            "headRepository": REPO, "requiredChecks": [{"kind": "check_run", "name": "build", "appId": 1}],
            "requiredReviewers": ["reviewer"]}


def pr():
    return {"number": 7, "state": "open", "draft": False, "merged": False,
            "merge_commit_sha": "d" * 40,
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPO}},
            "head": {"sha": HEAD, "repo": {"full_name": REPO}}}


def check(id=1, status="completed", conclusion="success"):
    return {"id": id, "name": "build", "head_sha": HEAD, "app": {"id": 1},
            "status": status, "conclusion": conclusion, "started_at": "2026-10-02T00:00:00Z",
            "completed_at": "2026-10-02T00:01:00Z" if status == "completed" else None,
            "html_url": f"https://github.com/{REPO}/runs/{id}"}


def routes(checks=None):
    rows = checks if checks is not None else [check()]
    return {PR_ROUTE: pr(), COMMIT_ROUTE + "/check-suites?per_page=100&page=1": {"total_count": 1},
            COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1": {"total_count": len(rows), "check_runs": rows},
            COMMIT_ROUTE + "/statuses?per_page=100&page=1": [{"id": 2, "context": "security", "creator": {"login": "ci-bot"}, "state": "pending", "created_at": "2026-10-02T00:01:00Z", "target_url": None}],
            PR_ROUTE + "/reviews?per_page=100&page=1": [{"id": 3, "user": {"login": "reviewer"}, "commit_id": HEAD, "state": "DISMISSED", "submitted_at": "2026-10-02T00:02:00Z"}]}


def test_collect_preserves_pending_failed_and_dismissed_facts_without_grading():
    data = routes([check(), check(4, "queued", None), check(5, conclusion="failure")])
    result = github.collect(request(), data.__getitem__)
    assert result["complete"] is True and result["provider"] == "github"
    assert [c["status"] for c in result["checks"]] == ["completed", "queued", "completed", "pending"]
    assert result["checks"][2]["conclusion"] == "failure"
    assert result["reviews"][0] == {"id": 3, "login": "reviewer", "commit": HEAD, "state": "DISMISSED", "submittedAt": "2026-10-02T00:02:00Z"}
    assert "passed" not in result and "qualified" not in result


def test_complete_check_pagination():
    data = routes()
    key = COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"
    data[key] = {"total_count": 101, "check_runs": [check(i) for i in range(1, 101)]}
    data[key.replace("&page=1", "&page=2")] = {"total_count": 101, "check_runs": [check(101)]}
    assert len(github.collect(request(), data.__getitem__)["checks"]) == 102


@pytest.mark.parametrize("mutation,error", [
    (lambda d: d[COMMIT_ROUTE + "/check-suites?per_page=100&page=1"].update(total_count=1000), "suite_limit"),
    (lambda d: d[COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"].update(total_count=2), "incomplete"),
    (lambda d: d[COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"]["check_runs"].append(check()), "duplicate"),
    (lambda d: d[COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"]["check_runs"][0].update(head_sha=BASE), "check_invalid"),
    (lambda d: d[COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"]["check_runs"][0].update(app=None), "app_missing"),
    (lambda d: d[PR_ROUTE]["head"].update(sha=BASE), "binding_changed"),
])
def test_invalid_incomplete_or_wrong_binding_blocks(mutation, error):
    data = routes()
    mutation(data)
    if error == "duplicate":
        data[COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"]["total_count"] = 2
    with pytest.raises(github.WorkerError, match=error):
        github.collect(request(), data.__getitem__)


@pytest.mark.parametrize("field,value", [("state", "closed"), ("draft", True), ("merged", True), ("merge_commit_sha", "e" * 40)])
def test_changed_pr_during_collection_blocks(field, value):
    data, calls = routes(), []
    def get(endpoint):
        calls.append(endpoint)
        if endpoint == PR_ROUTE and calls.count(endpoint) == 2:
            changed = pr()
            changed[field] = value
            return changed
        return data[endpoint]
    with pytest.raises(github.WorkerError, match="changed_during"):
        github.collect(request(), get)


def test_collection_total_changed_between_pages_blocks():
    data = routes()
    key = COMMIT_ROUTE + "/check-runs?filter=all&per_page=100&page=1"
    data[key] = {"total_count": 101, "check_runs": [check(i) for i in range(1, 101)]}
    data[key.replace("&page=1", "&page=2")] = {"total_count": 102, "check_runs": [check(101), check(102)]}
    with pytest.raises(github.WorkerError, match="collection_changed"):
        github.collect(request(), data.__getitem__)


def test_page_limit_is_fail_closed():
    data = routes()
    endpoint = COMMIT_ROUTE + "/statuses?per_page=100&page="
    for p in range(1, 11):
        data[endpoint + str(p)] = [{"id": p * 100 + i} for i in range(100)]
    with pytest.raises(github.WorkerError, match="page_limit"):
        github.collect(request(), data.__getitem__)


@pytest.mark.parametrize("field,value", [("number", True), ("schemaVersion", True), ("head", "main"), ("repository", "../project"), ("headRepository", "example/repo?evil"), ("candidateDigest", "unknown"), ("baseRef", "main\n--evil")])
def test_request_rejects_unsafe_or_unbound_fields(field, value):
    value_request = request()
    value_request[field] = value
    with pytest.raises(github.WorkerError):
        github.request_value(value_request)


def test_fixed_read_only_command_has_no_payload_or_cache():
    argv = github.command_preview("C:/gh.exe", PR_ROUTE)
    assert argv[1:5] == ["api", PR_ROUTE, "--method", "GET"]
    assert "github.com" in argv and "X-GitHub-Api-Version: 2026-03-10" in argv
    assert not set(argv) & {"--input", "--field", "--raw-field", "--cache", "--verbose", "--paginate", "graphql"}


def test_real_local_fake_cli_worker_protocol(tmp_path, monkeypatch):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    binary = tmp_path / "gh.exe"
    binary.write_bytes(b"offline-placeholder-pinned")
    (tmp_path / "routes.json").write_text(json.dumps(routes()), encoding="utf-8")
    script = tmp_path / "fake.py"
    script.write_text("import json,sys\nfrom pathlib import Path\nassert sys.argv[1]=='api' and sys.argv[3:5]==['--method','GET']\ndata=json.loads(Path(__file__).with_name('routes.json').read_text())\nprint(json.dumps(data[sys.argv[2]]))\n", encoding="utf-8")
    original = github.command_preview
    monkeypatch.setattr(github, "command_preview", lambda exe, endpoint: [sys.executable, "-I", "-B", str(script)] + original(exe, endpoint)[1:])
    result = github.worker(path, str(binary), hashlib.sha256(binary.read_bytes()).hexdigest(), timeout=5)
    assert result["head"] == HEAD and result["checks"][1]["status"] == "pending"
    assert result["reviews"][0]["state"] == "DISMISSED"


def test_binary_change_after_read_blocks(tmp_path, monkeypatch):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    binary = tmp_path / "gh.exe"
    binary.write_bytes(b"pinned")
    def changed(*args):
        binary.write_bytes(b"changed")
        return json.dumps(pr()).encode(), b"", None
    monkeypatch.setattr(github, "run_bounded", changed)
    with pytest.raises(github.WorkerError, match="binary_changed"):
        github.worker(path, str(binary), hashlib.sha256(b"pinned").hexdigest())


@pytest.mark.parametrize("failure", ["github_timeout", "github_process_failed", "github_output_limit", "github_output_incomplete"])
def test_process_failure_never_reflects_credential_text(tmp_path, monkeypatch, failure):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    binary = tmp_path / "gh.exe"
    binary.write_bytes(b"pinned")
    monkeypatch.setattr(github, "run_bounded", lambda *args: (b"", b"SECRET credential text", failure))
    with pytest.raises(github.WorkerError) as error:
        github.worker(path, str(binary), hashlib.sha256(b"pinned").hexdigest())
    assert str(error.value) == failure and "SECRET" not in str(error.value)


def test_real_process_limits(tmp_path):
    stdout, stderr, failure = github.run_bounded([sys.executable, "-I", "-B", "-c", "print('local')"], tmp_path, 5, 4096)
    assert stdout.strip() == b"local" and stderr == b"" and failure is None
    stdout, _, failure = github.run_bounded([sys.executable, "-I", "-B", "-c", "print('x'*20000)"], tmp_path, 5, 4096)
    assert len(stdout) <= 4096 and failure == "github_output_limit"
    _, _, failure = github.run_bounded([sys.executable, "-I", "-B", "-c", "import time;time.sleep(5)"], tmp_path, 0.1, 4096)
    assert failure == "github_timeout"


def test_standalone_copy_help_and_stable_failure(tmp_path):
    script = tmp_path / "reader.py"
    script.write_bytes(Path(github.__file__).read_bytes())
    result = subprocess.run([sys.executable, "-I", "-B", str(script), "--help"], capture_output=True)
    assert result.returncode == 0 and b"--gh-sha256" in result.stdout
    result = subprocess.run([sys.executable, "-I", "-B", str(script), "request.json", "--gh", str(tmp_path / "missing.exe"), "--gh-sha256", "a" * 64], capture_output=True)
    assert result.returncode == 1 and result.stdout == b""
    assert json.loads(result.stderr)["error"] == "github_native_executable_required"


def test_duplicate_json_keys_rejected():
    with pytest.raises(github.WorkerError, match="duplicate_key"):
        github.decode('{"head":"first","head":"second"}')
