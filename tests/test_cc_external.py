"""External observer acceptance tests with one guarded process per request.

The product checkout and pytest process never import the external dispatcher.
Each request runs in a fresh isolated Python process, matching the public entry
point and keeping its permanent audit hook alive for that process's lifetime.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "cc_external.py"


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def source_snapshot(root):
    """Include excluded VCS data and directories when checking source immutability."""
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.stat()
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            result[rel] = ("dir", info.st_mtime_ns, info.st_ctime_ns)
        else:
            data = path.read_bytes()
            result[rel] = ("file", len(data), hashlib.sha256(data).hexdigest(),
                           info.st_mtime_ns, info.st_ctime_ns)
    root_stat = root.stat()
    result["."] = ("dir", root_stat.st_mtime_ns, root_stat.st_ctime_ns)
    return result


def request(action, workspace, value=None):
    return {"action": action, "workspace": str(workspace), "value": value}


def stdio_call(payload):
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(ENTRY), "--stdio"],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=30,
    )
    assert completed.stdout.strip(), completed.stderr
    assert completed.stderr == ""
    return completed, json.loads(completed.stdout)


def call(action, workspace, value=None):
    completed, response = stdio_call(request(action, workspace, value))
    assert completed.returncode == (0 if response["ok"] else 1), completed.stderr
    return response


def initialize(source, workspace, include):
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(ENTRY), "init", "--workspace", str(workspace),
         "--source", str(source), *[arg for name in include for arg in ("--include", name)]],
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=30,
    )
    assert completed.stderr == ""
    assert completed.stdout.strip()
    return completed, json.loads(completed.stdout)


def initialized(tmp_path, files=None, include=None):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    if files:
        for name, content in files.items():
            put(source / name, content)
    else:
        source.mkdir()
    completed, response = initialize(source, workspace, include or ["."])
    assert completed.returncode == 0, response
    assert response["ok"] is True, response
    return source, workspace, response["result"]


def test_init_requires_explicit_scope_and_creates_external_state(tmp_path):
    source = tmp_path / "source"
    put(source / "docs" / "guide.md", "# Guide\n\nThe external archive is separate.\n")
    put(source / "outside.md", "not selected\n")
    workspace = tmp_path / "workspace"

    completed, response = initialize(source, workspace, ["docs"])

    assert completed.returncode == 0, response
    result = response["result"]
    assert result["mode"] == "external"
    assert result["include"] == ["docs"]
    assert result["fresh"] is False
    assert result["files"] == 0
    assert result["indexed_documents"] == 0
    assert result["generation"] == 0
    assert result["changes"] == ["docs/guide.md"]
    assert (workspace / "external.json").is_file()
    assert (workspace / "state.json").is_file()
    assert not (source / ".controlcoding").exists()
    assert "outside.md" not in (workspace / "state.json").read_text(encoding="utf-8")

    missing_scope = subprocess.run(
        [sys.executable, "-I", "-B", str(ENTRY), "init", "--workspace", str(tmp_path / "no-scope"),
         "--source", str(source)],
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert missing_scope.returncode == 1
    assert missing_scope.stderr == ""
    assert json.loads(missing_scope.stdout) == {"ok": False, "error": "invalid_scope"}


def test_refresh_reuses_lexical_engine_for_search_wiki_catalog_and_provenance(tmp_path):
    source, workspace, _ = initialized(
        tmp_path,
        {"project/docs/guide.md": "# Guide\n\nExternal snapshots keep the source read only.\n"},
        ["project"],
    )
    original_bytes = (source / "project/docs/guide.md").read_bytes()
    original = original_bytes.decode("utf-8")
    source_hash = hashlib.sha256(original_bytes).hexdigest()

    refreshed = call("refresh", workspace)["result"]
    assert refreshed["fresh"] is True
    assert refreshed["generation"] == 1
    assert refreshed["indexed_documents"] == 1

    catalog = call("catalog", workspace)["result"]
    guide = next(item for item in catalog["sources"] if item["path"] == "project/docs/guide.md")
    assert guide["kind"] == "document"
    assert any(item["id"].startswith("wiki:topic:") for item in catalog["wiki"])
    page_id = next(item["id"] for item in catalog["wiki"] if item["id"].startswith("wiki:topic:"))
    page = call("page", workspace, page_id)["result"]
    assert "External snapshots keep the source read only." in page["body"]

    query = call("query", workspace, "source read only")["result"]
    citation = next(item for item in query["citations"] if item["path"] == "project/docs/guide.md")
    assert query["mode"] == "lexical BM25"
    assert citation["source_root"] == str(source.resolve())
    assert citation["source_sha256"] == source_hash
    assert citation["original_path"] == "project/docs/guide.md"
    assert citation["physicalPath"] == "project/docs/guide.md"
    assert citation["cached_path"].startswith("docs/source/")

    document = call("document", workspace, "project/docs/guide.md")["result"]
    assert document == {"path": "project/docs/guide.md", "sha256": source_hash,
                        "source_root": str(source.resolve()), "markdown": original}


def test_conversation_records_are_readable_without_model_or_source_writes(tmp_path):
    source, workspace, _ = initialized(
        tmp_path, {"notes.md": "# Notes\n\nAn external record can cite this note.\n"}, ["notes.md"]
    )
    call("refresh", workspace)
    digest = hashlib.sha256((source / "notes.md").read_bytes()).hexdigest()
    record = {"id": "decision-1", "kind": "decision", "title": "Keep the archive external",
              "body": "The source tree remains unchanged.",
              "sources": [{"path": "notes.md", "sha256": digest}], "links": []}
    saved = call("record", workspace, record)["result"]
    assert saved["id"] == "decision-1"
    assert call("records", workspace)["result"][0]["sources"] == record["sources"]

    conversation = {"id": "conversation-1", "title": "External session", "retention": "transcript",
                    "summary": "One recorded turn.", "status": "closed",
                    "turns": [{"id": "turn-1", "sequence": 0, "role": "user",
                               "content": "Keep this in the external workspace.", "provenance": {}}]}
    saved_conversation = call("conversation", workspace, conversation)["result"]
    assert saved_conversation["saved"] is True
    loaded = call("conversation-read", workspace, "conversation-1")["result"]
    assert loaded["summary"] == "One recorded turn."
    assert loaded["turns"][0]["content"] == conversation["turns"][0]["content"]
    assert not (source / ".controlcoding").exists()


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_refresh_invalidates_source_bound_records_and_indexed_sources(tmp_path, change):
    source, workspace, _ = initialized(
        tmp_path,
        {"docs/a.md": "# A\n\nEvidence from the original source.\n",
         "docs/b.md": "# B\n\nIndependent evidence for a linked record.\n"},
        ["docs"],
    )
    call("refresh", workspace)
    a_hash = hashlib.sha256((source / "docs/a.md").read_bytes()).hexdigest()
    b_hash = hashlib.sha256((source / "docs/b.md").read_bytes()).hexdigest()
    call("record", workspace, {"id": "root-record", "kind": "evidence", "title": "Root evidence",
          "body": "Bound to A.", "sources": [{"path": "docs/a.md", "sha256": a_hash}], "links": []})
    call("record", workspace, {"id": "linked-record", "kind": "note", "title": "Linked note",
          "body": "Depends on the root record.", "sources": [{"path": "docs/b.md", "sha256": b_hash}],
          "links": ["root-record"]})

    if change == "edit":
        put(source / "docs/a.md", "# A\n\nChanged evidence.\n")
    else:
        (source / "docs/a.md").unlink()

    stale = call("status", workspace)["result"]
    assert stale["fresh"] is False
    assert stale["error"] == "stale_sources"
    assert {item["id"] for item in stale["records"] if item["stale"]} == {"root-record", "linked-record"}

    refreshed = call("refresh", workspace)["result"]
    assert refreshed["fresh"] is True
    catalog = call("catalog", workspace)["result"]
    paths = {item["path"] for item in catalog["sources"]}
    assert ("docs/a.md" in paths) is (change == "edit")
    assert "docs/b.md" in paths
    assert {item["id"] for item in refreshed["records"] if item["stale"]} == {"root-record", "linked-record"}


@pytest.mark.parametrize(("action", "value"), [
    ("query", "evidence"),
    ("page", "wiki:topic:reference"),
    ("document", "docs/a.md"),
])
def test_stale_sources_refuse_query_wiki_and_document_reads(tmp_path, action, value):
    source, workspace, _ = initialized(tmp_path, {"docs/a.md": "# A\n\nOriginal evidence.\n"}, ["docs"])
    call("refresh", workspace)
    put(source / "docs/a.md", "# A\n\nChanged evidence.\n")

    response = call(action, workspace, value)

    assert response == {"ok": False, "error": "stale_sources"}


def test_inventory_skips_git_tracks_hidden_and_never_changes_source_mtimes(tmp_path):
    source = tmp_path / "source"
    put(source / "project/.git/HEAD", "ref: refs/heads/main\n")
    put(source / "project/.hidden/secret.md", "# Hidden\n\nStill selected.\n")
    put(source / "project/.hidden.bin", "binary inventory\n")
    put(source / "project/docs/guide.md", "# Guide\n\nA guide.\n")
    workspace = tmp_path / "workspace"
    before = source_snapshot(source)
    completed, response = initialize(source, workspace, ["project"])
    assert completed.returncode == 0, response

    status = call("refresh", workspace)["result"]
    manifest = json.loads((workspace / "state.json").read_text(encoding="utf-8"))["manifest"]
    catalog = call("catalog", workspace)["result"]
    paths = {item["path"] for item in catalog["sources"]}
    assert status["files"] == 3
    assert set(manifest) == {"project/.hidden.bin", "project/.hidden/secret.md", "project/docs/guide.md"}
    assert not any(".git" in path for path in manifest)
    assert "project/.hidden/secret.md" in paths
    assert "project/.hidden.bin" not in paths
    assert source_snapshot(source) == before


@pytest.mark.parametrize(("include", "error"), [
    (["../outside"], "invalid_scope"),
    ([".git"], "unsupported_scope"),
    (["docs", "DOCS"], "duplicate_scope"),
])
def test_init_rejects_malformed_or_ambiguous_scope(tmp_path, include, error):
    source = tmp_path / "source"
    put(source / "docs/guide.md", "# Guide\n")
    completed, response = initialize(source, tmp_path / "workspace", include)
    assert completed.returncode == 1
    assert response == {"ok": False, "error": error}
    assert not (tmp_path / "workspace").exists()


@pytest.mark.parametrize("relation", ["same", "workspace_inside_source", "source_inside_workspace"])
def test_init_rejects_overlapping_roots_without_creating_state(tmp_path, relation):
    source = tmp_path / "source"
    source.mkdir()
    if relation == "same":
        workspace = source
    elif relation == "workspace_inside_source":
        workspace = source / "workspace"
    else:
        workspace = tmp_path / "workspace"
        (workspace / "nested-source").mkdir(parents=True)
        source = workspace / "nested-source"
    completed, response = initialize(source, workspace, ["."])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "overlapping_roots"}
    assert not (workspace / "external.json").exists()


def test_init_rejects_workspace_inside_product_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sentinel = ROOT / "tests" / "test_cc_external.py"
    before = sentinel.read_bytes()
    entries = sorted(p.name for p in (ROOT / "tests").iterdir())
    completed, response = initialize(source, ROOT / "tests", ["."])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "overlapping_roots"}
    assert sentinel.read_bytes() == before
    assert sorted(p.name for p in (ROOT / "tests").iterdir()) == entries


def test_runtime_inside_source_is_rejected_without_creating_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    completed, response = initialize(ROOT, workspace, ["README.md"])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "runtime_inside_source"}
    assert not workspace.exists()


@pytest.mark.parametrize("include", [["CON"], ["docs/NUL.txt"], ["LPT1.md"], ["docs/trailing."]])
def test_reserved_device_and_ambiguous_scope_components_are_rejected(tmp_path, include):
    source = tmp_path / "source"
    put(source / "docs/guide.md", "# Guide\n")
    completed, response = initialize(source, tmp_path / "workspace", include)
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "invalid_scope"}
    assert not (tmp_path / "workspace").exists()


def test_windows_case_alias_cannot_split_source_and_workspace_roots(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows path alias semantics")
    source = tmp_path / "source"
    put(source / "guide.md", "# Guide\n")
    workspace_alias = source.parent / source.name.swapcase()
    completed, response = initialize(source, workspace_alias, ["guide.md"])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "overlapping_roots"}


def test_windows_short_name_alias_cannot_split_source_and_workspace_roots(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows 8.3 path alias semantics")
    import ctypes

    source = tmp_path / "source directory with long name"
    put(source / "guide.md", "# Guide\n")
    get_short = ctypes.windll.kernel32.GetShortPathNameW
    get_short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
    get_short.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_short(str(source), buffer, len(buffer))
    if not length or Path(buffer.value).resolve() == source.resolve():
        pytest.skip("filesystem did not provide an 8.3 alias")
    completed, response = initialize(source, Path(buffer.value), ["guide.md"])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "overlapping_roots"}


def test_replacing_source_directory_identity_invalidates_descriptor(tmp_path):
    source, workspace, _ = initialized(tmp_path, {"notes.md": "# Notes\n"}, ["notes.md"])
    moved = tmp_path / "source-old"
    source.rename(moved)
    source.mkdir()

    response = call("status", workspace)

    assert response == {"ok": False, "error": "source_identity_changed"}
    assert (workspace / "external.json").is_file()


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_init_refuses_linked_source_files(tmp_path, kind):
    source = tmp_path / "source"
    put(source / "docs/original.md", "# Source\n")
    linked = source / "docs/linked.md"
    if kind == "symlink":
        try:
            linked.symlink_to(source / "docs/original.md")
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation is unavailable")
    else:
        try:
            os.link(source / "docs/original.md", linked)
        except (OSError, NotImplementedError):
            pytest.skip("hard links are unavailable")
    completed, response = initialize(source, tmp_path / "workspace", ["docs"])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "unsupported_path"}
    assert not (tmp_path / "workspace").exists()


def test_workspace_symlink_or_junction_is_rejected(tmp_path):
    source = tmp_path / "source"
    put(source / "guide.md", "# Guide\n")
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace_link = tmp_path / "workspace-link"
    try:
        workspace_link.symlink_to(real_workspace, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory links are unavailable")
    completed, response = initialize(source, workspace_link, ["guide.md"])
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "unsupported_path"}
    assert list(real_workspace.iterdir()) == []


def test_existing_workspace_hardlink_to_source_is_rejected(tmp_path):
    source, workspace, _ = initialized(tmp_path, {"guide.md": "# Guide\n"}, ["guide.md"])
    call("refresh", workspace)
    alias = workspace / "source-hardlink"
    try:
        os.link(source / "guide.md", alias)
    except (OSError, NotImplementedError):
        pytest.skip("hard links are unavailable")

    response = call("status", workspace)

    assert response == {"ok": False, "error": "unsupported_path"}
    assert (workspace / "external.json").is_file()


@pytest.mark.parametrize("source_ref", [
    {"path": "notes.md", "sha256": "0" * 64},
    {"path": "missing.md", "sha256": "0" * 64},
    {"path": "notes.md", "sha256": None},
])
def test_record_requires_current_well_formed_source_hash(tmp_path, source_ref):
    _, workspace, _ = initialized(tmp_path, {"notes.md": "# Notes\n"}, ["notes.md"])
    call("refresh", workspace)
    value = {"id": "bad-evidence", "kind": "evidence", "title": "Bad evidence",
             "body": "This must not be accepted.", "sources": [source_ref], "links": []}

    response = call("record", workspace, value)

    assert response == {"ok": False, "error": "stale_evidence"}
    assert call("records", workspace)["result"] == []


def test_missing_selected_source_scope_reports_not_fresh(tmp_path):
    source = tmp_path / "source"
    put(source / "selected/guide.md", "# Guide\n")
    _, workspace, _ = initialized(tmp_path, {"selected/guide.md": "# Guide\n"}, ["selected/guide.md"])
    (source / "selected/guide.md").unlink()

    response = call("status", workspace)

    assert response["ok"] is True
    assert response["result"]["fresh"] is False
    assert response["result"]["error"] == "source_unavailable"


def test_projection_drift_marks_stale_then_refresh_repairs_it(tmp_path):
    source, workspace, _ = initialized(tmp_path, {"docs/guide.md": "# Guide\n\nOriginal.\n"}, ["docs"])
    call("refresh", workspace)
    projection = next((workspace / "projection/docs/source").glob("*.md"))
    projection.write_text("# Tampered projection\n", encoding="utf-8")

    stale = call("status", workspace)["result"]
    assert stale["fresh"] is False
    assert stale["error"] == "projection_changed"
    assert call("query", workspace, "Original") == {"ok": False, "error": "stale_sources"}

    repaired = call("refresh", workspace)["result"]
    assert repaired["fresh"] is True
    expected = (source / "docs/guide.md").read_bytes().decode("utf-8")
    assert call("document", workspace, "docs/guide.md")["result"]["markdown"] == expected


def test_interrupted_refresh_repairs_partial_projection_and_advances_generation(tmp_path):
    source, workspace, _ = initialized(tmp_path, {"docs/guide.md": "# Guide\n\nBefore.\n"}, ["docs"])
    call("refresh", workspace)
    projection = next((workspace / "projection/docs/source").glob("*.md"))
    put(source / "docs/guide.md", "# Guide\n\nAfter interrupted refresh.\n")
    state_path = workspace / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(fresh=False, error="refresh_incomplete")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    projection.write_text("partial bytes", encoding="utf-8")

    recovered = call("refresh", workspace)["result"]

    assert recovered["fresh"] is True
    assert recovered["generation"] == 2
    expected = (source / "docs/guide.md").read_bytes().decode("utf-8")
    assert call("document", workspace, "docs/guide.md")["result"]["markdown"] == expected


def test_original_markdown_links_become_edges_between_original_sources(tmp_path):
    _, workspace, _ = initialized(
        tmp_path,
        {"docs/a.md": "# A\n\n[See B](b.md)\n", "docs/b.md": "# B\n\nLinked source.\n"},
        ["docs"],
    )
    call("refresh", workspace)
    catalog = call("catalog", workspace)["result"]
    by_path = {item["path"]: item["id"] for item in catalog["sources"]}
    links = [edge for edge in catalog["edges"] if edge["kind"] == "original_document_link"]
    assert links == [{"source": by_path["docs/a.md"], "target": by_path["docs/b.md"],
                      "revision": next(item["revision"] for item in catalog["sources"] if item["path"] == "docs/a.md"),
                      "kind": "original_document_link"}]


def test_archive_tampering_is_detected_and_remains_untrusted(tmp_path):
    _, workspace, _ = initialized(tmp_path, {"notes.md": "# Notes\n"}, ["notes.md"])
    call("refresh", workspace)
    archive = workspace / "projection/.controlcoding/knowledge/knowledge.db"
    with archive.open("ab") as stream:
        stream.write(b"tamper")

    status = call("status", workspace)["result"]
    assert status["fresh"] is False
    assert status["error"] == "archive_changed"
    assert call("refresh", workspace) == {"ok": False, "error": "archive_changed"}


def test_cli_and_stdio_return_bounded_errors_for_invalid_requests(tmp_path):
    cli = subprocess.run(
        [sys.executable, "-I", "-B", str(ENTRY), "refresh", "--workspace", str(tmp_path / "missing")],
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert cli.returncode == 1
    assert cli.stderr == ""
    assert json.loads(cli.stdout) == {"ok": False, "error": "missing_directory"}

    completed, response = stdio_call({"action": "run-command", "workspace": str(tmp_path), "value": None})
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert response == {"ok": False, "error": "unsupported_external_action"}

    completed, response = stdio_call({"action": "status", "workspace": str(tmp_path)})
    assert completed.returncode == 1
    assert response == {"ok": False, "error": "unsupported_external_action"}


def test_guard_stays_active_and_blocks_writes_rename_sqlite_links_process_and_network(tmp_path):
    source, workspace, _ = initialized(tmp_path, {"notes.md": "# Notes\n"}, ["notes.md"])
    outside_file = tmp_path / "outside.txt"
    put(outside_file, "untouched\n")
    outside_db = tmp_path / "outside.sqlite"
    rename_target = tmp_path / "renamed.txt"
    link_target = workspace / "symlink-probe"
    hardlink_target = workspace / "hardlink-probe"
    code = r'''
import json, os, socket, sqlite3, subprocess, sys
sys.path.insert(0, sys.argv[1])
import cc_external
request = json.loads(sys.stdin.readline())
real_popen = subprocess.Popen
launches = []
def sentinel(*a, **k):
    launches.append(a)
    return real_popen(*a, **k)
subprocess.Popen = sentinel
response = cc_external.respond(request)
subprocess.Popen = real_popen
probe = json.loads(sys.stdin.readline())
denied = []
def attempt(name, call):
    try:
        call()
    except (PermissionError, OSError):
        denied.append(name)
attempt("write", lambda: open(probe["outside_file"], "w").close())
attempt("rename", lambda: os.rename(probe["outside_file"], probe["rename_target"]))
attempt("sqlite", lambda: sqlite3.connect(probe["outside_db"]))
attempt("symlink", lambda: os.symlink(probe["outside_file"], probe["link_target"]))
attempt("hardlink", lambda: os.link(probe["outside_file"], probe["hardlink_target"]))
attempt("process", lambda: subprocess.Popen([sys.executable, "-c", "pass"]))
attempt("network", lambda: socket.create_connection(("127.0.0.1", 9), timeout=1))
print(json.dumps({"response": response, "denied": denied, "launches": len(launches)}))
'''
    probe = {"outside_file": str(outside_file), "rename_target": str(rename_target),
             "outside_db": str(outside_db), "link_target": str(link_target),
             "hardlink_target": str(hardlink_target)}
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(ROOT / "scripts")],
        input=json.dumps(request("status", workspace)) + "\n" + json.dumps(probe) + "\n",
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["response"]["ok"] is True
    assert set(result["denied"]) == {"write", "rename", "sqlite", "symlink", "hardlink", "process", "network"}
    assert result["launches"] == 0
    assert outside_file.read_text(encoding="utf-8") == "untouched\n"
    assert not rename_target.exists()
    assert not outside_db.exists()
    assert not link_target.exists()
    assert not hardlink_target.exists()
    assert not (source / ".controlcoding").exists()


def test_refresh_never_launches_git_or_other_child_processes(tmp_path):
    source, workspace, _ = initialized(tmp_path, {"docs/guide.md": "# Guide\n\nNo subprocess.\n"}, ["docs"])
    code = r'''
import json, subprocess, sys
sys.path.insert(0, sys.argv[1])
import cc_external
launches = []
subprocess.Popen = lambda *a, **k: launches.append(a) or (_ for _ in ()).throw(RuntimeError("launched"))
print(json.dumps({"response": cc_external.respond(json.loads(sys.stdin.readline())),
                  "launches": len(launches)}))
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(ROOT / "scripts")],
        input=json.dumps(request("refresh", workspace)) + "\n",
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["response"]["ok"] is True, result
    assert result["launches"] == 0
