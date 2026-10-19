"""Standalone pinned GitHub CLI reader for bounded pull-request observations.

Only fixed REST GET routes on github.com are used. Facts do not establish a
passing gate: the controller grades reviewed requirements and snapshot age.
This reader installs nothing and never creates, edits, merges or publishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


API_VERSION = "2026-03-10"
PAGE_SIZE = 100
MAX_PAGES = 10
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_FIELDS = {"schemaVersion", "candidateDigest", "repository", "number", "baseRef", "base", "head", "headRepository", "requiredChecks", "requiredReviewers"}


class WorkerError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise WorkerError(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "github_json_duplicate_key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, "github_json_invalid"))


def integer(value):
    return type(value) is int and 0 < value <= 10**16


def text(value, nullable=False, maximum=1024):
    return (nullable and value is None) or (type(value) is str and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value))


def request_value(value):
    require(type(value) is dict and set(value) == REQUEST_FIELDS
            and type(value["schemaVersion"]) is int and value["schemaVersion"] == 1
            and type(value["candidateDigest"]) is str and DIGEST.fullmatch(value["candidateDigest"])
            and integer(value["number"]) and text(value["baseRef"], maximum=255), "github_request_invalid")
    for key in ("repository", "headRepository"):
        require(type(value[key]) is str and REPOSITORY.fullmatch(value[key])
                and value[key].split("/")[1] not in {".", ".."}, "github_repository_invalid")
    for key in ("base", "head"):
        require(type(value[key]) is str and SHA.fullmatch(value[key]), "github_sha_invalid")
    require(type(value["requiredChecks"]) is list and len(value["requiredChecks"]) <= 128
            and all(type(v) is dict for v in value["requiredChecks"])
            and type(value["requiredReviewers"]) is list and len(value["requiredReviewers"]) <= 128
            and all(text(v, maximum=100) for v in value["requiredReviewers"]), "github_requirements_invalid")
    return value


def binary_digest(exe):
    path = Path(exe)
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
            and path.suffix.casefold() not in {".py", ".ps1", ".cmd", ".bat", ".sh"}, "github_native_executable_required")
    for part in (path, *path.parents):
        require(not part.is_symlink() and not (hasattr(part, "is_junction") and part.is_junction()), "github_binary_link")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command_preview(exe, endpoint):
    require(type(endpoint) is str and endpoint.startswith("repos/") and not any(c in endpoint for c in ("{", "}", "\\", "\x00", "\n", "\r")), "github_endpoint_invalid")
    return [str(exe), "api", endpoint, "--method", "GET", "--hostname", "github.com",
            "--header", "Accept: application/vnd.github+json", "--header", "X-GitHub-Api-Version: " + API_VERSION]


def run_bounded(argv, cwd, timeout, limit):
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env["GH_PROMPT_DISABLED"] = "1"
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    buffers, sizes, exceeded = [bytearray(), bytearray()], [0, 0], threading.Event()
    def collect(index, stream):
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            sizes[index] += len(chunk)
            buffers[index].extend(chunk[:max(0, limit - len(buffers[index]))])
            if sizes[index] > limit:
                exceeded.set()
                process.kill()
                break
        stream.close()
    threads = [threading.Thread(target=collect, args=(i, stream), daemon=True)
               for i, stream in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    failure = None
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        failure = "github_timeout"
    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        failure = "github_output_incomplete"
    if exceeded.is_set():
        failure = "github_output_limit"
    if process.returncode and failure is None:
        failure = "github_process_failed"
    return bytes(buffers[0]), bytes(buffers[1]), failure


def pr_identity(raw):
    require(type(raw) is dict and integer(raw.get("number"))
            and raw.get("state") in {"open", "closed"}
            and type(raw.get("draft")) is bool and type(raw.get("merged")) is bool
            and (raw.get("merge_commit_sha") is None or type(raw["merge_commit_sha"]) is str and SHA.fullmatch(raw["merge_commit_sha"])), "github_pr_invalid")
    try:
        value = {"repository": raw["base"]["repo"]["full_name"], "number": raw["number"],
                 "baseRef": raw["base"]["ref"], "base": raw["base"]["sha"], "head": raw["head"]["sha"],
                 "headRepository": raw["head"]["repo"]["full_name"], "state": raw["state"],
                 "draft": raw["draft"], "merged": raw["merged"], "mergeCommitSha": raw.get("merge_commit_sha")}
    except (KeyError, TypeError):
        raise WorkerError("github_pr_identity_missing") from None
    require(all(text(value[k]) for k in ("repository", "headRepository", "baseRef"))
            and SHA.fullmatch(str(value["base"])) and SHA.fullmatch(str(value["head"])), "github_pr_identity_invalid")
    return value


def check_run(raw, head):
    require(type(raw) is dict and integer(raw.get("id")) and text(raw.get("name"))
            and raw.get("head_sha") == head and raw.get("status") in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
            and text(raw.get("conclusion"), nullable=True) and text(raw.get("started_at"), nullable=True)
            and text(raw.get("completed_at"), nullable=True) and text(raw.get("html_url")), "github_check_invalid")
    app_id = raw.get("app", {}).get("id") if type(raw.get("app")) is dict else None
    require(integer(app_id), "github_check_app_missing")
    return {"kind": "check_run", "id": raw["id"], "name": raw["name"], "appId": app_id, "head": head,
            "status": raw["status"], "conclusion": raw["conclusion"], "startedAt": raw["started_at"],
            "completedAt": raw["completed_at"], "url": raw["html_url"]}


def commit_status(raw, head):
    creator = raw.get("creator", {}).get("login") if type(raw) is dict and type(raw.get("creator")) is dict else None
    require(type(raw) is dict and integer(raw.get("id")) and text(raw.get("context"))
            and text(creator, maximum=100) and raw.get("state") in {"error", "failure", "pending", "success"}
            and text(raw.get("created_at")) and text(raw.get("target_url"), nullable=True), "github_status_invalid")
    return {"kind": "commit_status", "id": raw["id"], "name": raw["context"], "creator": creator,
            "head": head, "status": raw["state"], "createdAt": raw["created_at"], "url": raw.get("target_url")}


def review(raw):
    login = raw.get("user", {}).get("login") if type(raw) is dict and type(raw.get("user")) is dict else None
    require(type(raw) is dict and integer(raw.get("id")) and text(login, maximum=100)
            and type(raw.get("commit_id")) is str and SHA.fullmatch(raw["commit_id"])
            and raw.get("state") in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}
            and text(raw.get("submitted_at"), nullable=True), "github_review_invalid")
    return {"id": raw["id"], "login": login, "commit": raw["commit_id"], "state": raw["state"], "submittedAt": raw.get("submitted_at")}


def collect(request, get):
    """Collect complete facts. `get` is bounded, authenticated native CLI GET."""
    route = "repos/" + request["repository"] + "/pulls/" + str(request["number"])
    before = pr_identity(get(route))
    for key in ("repository", "number", "baseRef", "base", "head", "headRepository"):
        require(before[key] == request[key], "github_pr_binding_changed")
    checks_repository = request["headRepository"]
    commit_route = "repos/" + checks_repository + "/commits/" + request["head"]
    suites = get(commit_route + "/check-suites?per_page=100&page=1")
    require(type(suites) is dict and type(suites.get("total_count")) is int
            and 0 <= suites["total_count"] < 1000, "github_check_suite_limit")
    def pages(endpoint, collection=None):
        rows, total = [], None
        for page in range(1, MAX_PAGES + 1):
            value = get(endpoint + ("&" if "?" in endpoint else "?") + f"per_page={PAGE_SIZE}&page={page}")
            if collection is not None:
                require(type(value) is dict and type(value.get("total_count")) is int
                        and 0 <= value["total_count"] < PAGE_SIZE * MAX_PAGES, "github_page_invalid")
                if total is None:
                    total = value["total_count"]
                require(value["total_count"] == total, "github_collection_changed")
                value = value.get(collection)
            require(type(value) is list and len(value) <= PAGE_SIZE, "github_page_invalid")
            rows.extend(value)
            if len(value) < PAGE_SIZE:
                require(total is None or len(rows) == total, "github_collection_incomplete")
                return rows
        raise WorkerError("github_page_limit")
    raw_checks = pages(commit_route + "/check-runs?filter=all", "check_runs")
    raw_statuses = pages(commit_route + "/statuses")
    raw_reviews = pages(route + "/reviews")
    for group in (raw_checks, raw_statuses, raw_reviews):
        ids = [r.get("id") for r in group if type(r) is dict]
        require(len(ids) == len(group) and len(set(ids)) == len(ids), "github_duplicate_observation")
    after = pr_identity(get(route))
    require(before == after, "github_pr_changed_during_observation")
    return {"schemaVersion": 1, "provider": "github", "candidateDigest": request["candidateDigest"], **before,
            "complete": True, "checks": [check_run(r, request["head"]) for r in raw_checks]
            + [commit_status(r, request["head"]) for r in raw_statuses], "reviews": [review(r) for r in raw_reviews]}


def worker(request_path, gh, expected_digest, timeout=120, output_limit=1024 * 1024):
    require(type(expected_digest) is str and DIGEST.fullmatch(expected_digest), "github_binary_pin_invalid")
    require(binary_digest(gh) == expected_digest, "github_binary_changed")
    path = Path(request_path).absolute()
    require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
            and path.stat().st_size <= 1024 * 1024, "github_request_file_invalid")
    request = request_value(decode(path.read_text(encoding="utf-8")))
    started, consumed = time.monotonic(), 0
    def get(endpoint):
        nonlocal consumed
        require(binary_digest(gh) == expected_digest, "github_binary_changed")
        remaining = timeout - (time.monotonic() - started)
        require(remaining > 0, "github_timeout")
        stdout, stderr, failure = run_bounded(command_preview(gh, endpoint), path.parent, remaining, output_limit - consumed)
        consumed += len(stdout) + len(stderr)
        require(consumed <= output_limit, "github_output_limit")
        require(failure is None, failure)
        require(binary_digest(gh) == expected_digest, "github_binary_changed")
        return decode(stdout.decode("utf-8"))
    result = collect(request, get)
    require(time.monotonic() - started <= timeout, "github_timeout")
    require(len(json.dumps(result).encode("utf-8")) <= output_limit, "github_output_limit")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--gh", required=True)
    parser.add_argument("--gh-sha256", required=True)
    parser.add_argument("--timeoutSeconds", type=int, default=120)
    parser.add_argument("--outputLimitBytes", type=int, default=1024 * 1024)
    args = parser.parse_args()
    try:
        require(1 <= args.timeoutSeconds <= 300 and 4096 <= args.outputLimitBytes <= 2 * 1024 * 1024, "github_limits_invalid")
        result = worker(args.request, args.gh, args.gh_sha256, args.timeoutSeconds, args.outputLimitBytes)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        # Provider stderr is deliberately never reflected: native authentication
        # stays in gh, and errors carry stable codes rather than credential text.
        print(json.dumps({"error": str(error) if isinstance(error, WorkerError) else "github_worker_invalid"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
