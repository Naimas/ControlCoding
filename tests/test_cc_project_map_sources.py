"""External-fixture tests for the read-only Project Map source adapter."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_project_map_sources as source
import cc_setup_service as setup


@pytest.fixture
def request_data(tmp_path):
    return {"project_root": str(tmp_path), "project_id": "fixture",
            "observed_at": "2026-09-21T12:00:00Z", "design_paths": []}


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def failure(request_data, code, **kwargs):
    with pytest.raises(source.MapSourceError) as caught:
        source.observe_project_map(request_data, **kwargs)
    assert caught.value.code == code


def test_static_inventory_provenance_no_execution_or_mutation(tmp_path, request_data):
    write(tmp_path, "src/main.py", 'import os\nfrom . import helper\nSECRET="DO_NOT_LEAK_732"\nclass Engine:\n    def start(self):\n        return SECRET\nraise RuntimeError("MUST_NOT_EXECUTE")\n')
    write(tmp_path, "web/app.ts", 'throw new Error("DO_NOT_LEAK_732");')
    write(tmp_path, "package.json", '{"dependencies":{"example":"DO_NOT_LEAK_732"},"scripts":{"start":"DO_NOT_LEAK_732"}}')
    write(tmp_path, "pyproject.toml", '[project]\nname="DO_NOT_LEAK_732"\ndependencies=["example"]\n')
    write(tmp_path, "docs/design.md", '# DO_NOT_LEAK_732\nDesign body DO_NOT_LEAK_732\n')
    request_data["design_paths"] = ["docs/design.md"]
    before, original = files(tmp_path), deepcopy(request_data)
    preview = source.preview_project_map_scope(request_data)
    result = source.observe_project_map(request_data, expected_preview=preview["preview_id"])
    assert files(tmp_path) == before and request_data == original
    serialized = json.dumps(result)
    assert "DO_NOT_LEAK_732" not in serialized and str(tmp_path) not in serialized
    assert result["canonical"] is False and result["mode"] == "observation"
    projection = result["projection"]
    assert projection["summary"]["units"] == 5
    assert projection["summary"]["verified_units"] == 0
    assert projection["summary"]["coverage"] == "partial"
    assert not any(row["verified_accepted"] for row in projection["statuses"])
    assert projection["bundle"]["assessments"] == projection["bundle"]["constraints"] == []
    assert {n["title"] for n in projection["bundle"]["nodes"] if n["kind"] == "symbol"} == {"Engine", "Engine.start"}
    python = next(o for o in result["observations"] if o["adapter"] == "python-static")
    assert python["details"]["imports"] == [{"level": 0, "module": "os"}, {"level": 1, "module": ""}]
    for record in projection["bundle"]["sources"]:
        assert record["identity"] == "sha256:" + before[record["locator"]["path"]]
    assert next(o for o in result["observations"] if o["adapter"] == "design-document")["details"]["headings"] == 1
    assert next(o for o in result["observations"] if o["adapter"] == "manifest" and o["details"]["ecosystem"] == "python")["details"]["script_declarations"] is None


def test_preview_is_pure_and_bound_to_exact_scope(tmp_path, request_data, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Preview must not inspect filesystem")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", forbidden)
        patch.setattr(os, "scandir", forbidden)
        preview = source.preview_project_map_scope(request_data)
        changed = deepcopy(request_data)
        changed["design_paths"] = ["design.md"]
        failure(changed, "preview_mismatch", expected_preview=preview["preview_id"])
    assert preview["design_paths"] == []
    assert not preview["sensitive_override_supported"]


def test_empty_and_design_only_are_not_completed(tmp_path, request_data):
    empty = source.observe_project_map(request_data)
    assert len(empty["projection"]["bundle"]["nodes"]) == 1
    assert not empty["projection"]["summary"]["declared_scope_verified"]
    write(tmp_path, "design.md", "# Intended system\nNo implementation exists.\n")
    request_data["design_paths"] = ["design.md"]
    result = source.observe_project_map(request_data)
    assert not any(n["kind"] == "feature" for n in result["projection"]["bundle"]["nodes"])
    assert result["projection"]["summary"]["verified_units"] == 0
    assert result["observations"][0]["adapter"] == "design-document"


def test_stable_ids_repeated_observation_and_changed_source(tmp_path, request_data):
    path = write(tmp_path, "main.py", "def run():\n    return 1\n")
    first = source.observe_project_map(request_data)
    assert source.observe_project_map(request_data) == first
    path.write_text("\n\ndef run():\n    return 2\n", encoding="utf-8")
    second = source.observe_project_map(request_data)
    assert [n["id"] for n in first["projection"]["bundle"]["nodes"]] == [n["id"] for n in second["projection"]["bundle"]["nodes"]]
    assert first["projection"]["bundle"]["snapshot_id"] != second["projection"]["bundle"]["snapshot_id"]


def test_project_identity_does_not_leak_between_roots(tmp_path, request_data):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir(); two.mkdir()
    write(one, "main.py", "x=1"); write(two, "main.py", "x=1")
    a = source.observe_project_map(dict(request_data, project_root=str(one)))
    b = source.observe_project_map(dict(request_data, project_root=str(two)))
    assert a["root_identity"] != b["root_identity"]
    assert {n["id"] for n in a["projection"]["bundle"]["nodes"]}.isdisjoint(n["id"] for n in b["projection"]["bundle"]["nodes"])


def test_exclusions_and_unselected_documents_are_not_opened(tmp_path, request_data, monkeypatch):
    for path in (".env", ".controlcoding/features/features.json", ".controlwork/config.json", "node_modules/bad/index.py", "secrets/key.py", "docs/design.md", "opaque.bin"):
        write(tmp_path, path, "SENSITIVE_SENTINEL")
    write(tmp_path, "main.py", "x=1")
    original = setup._Snapshots._open
    opened = []
    def guarded(self, path, directory):
        if not directory:
            opened.append(path.relative_to(tmp_path).as_posix())
        return original(self, path, directory)
    monkeypatch.setattr(setup._Snapshots, "_open", guarded)
    result = source.observe_project_map(request_data)
    assert opened == ["main.py"]
    assert "SENSITIVE_SENTINEL" not in json.dumps(result)
    assert {f["code"] for f in result["findings"]} >= {"excluded_path", "content_not_selected"}


@pytest.mark.parametrize("path", ["../x.md", "/x.md", "C:/x.md", "a\\b.md", "a:stream.md", "a//b.md", "a/./b.md", "CON.md", "nul.md", "a\x00.md", "*.md", "x./a.md"])
def test_reject_unsafe_selection(request_data, path):
    request_data["design_paths"] = [path]
    failure(request_data, "invalid_path")


@pytest.mark.parametrize("path", [".env", ".controlwork/design.md", "secrets/design.md", "docs/private/design.md", "x.py"])
def test_reject_excluded_or_non_document_selection(request_data, path):
    request_data["design_paths"] = [path]
    failure(request_data, "excluded_design")


@pytest.mark.parametrize("case", ["extra", "id", "time", "root", "bool", "duplicate", "array", "custom"])
def test_strict_request(request_data, case):
    if case == "extra": request_data["execute"] = True
    elif case == "id": request_data["project_id"] = "../bad"
    elif case == "time": request_data["observed_at"] = "2026-02-30T00:00:00Z"
    elif case == "root": request_data["project_root"] = "relative"
    elif case == "bool": request_data["project_root"] = True
    elif case == "duplicate": request_data["design_paths"] = ["a.md", "a.md"]
    elif case == "array": request_data["design_paths"] = "a.md"
    else:
        class Custom(dict): pass
        request_data = Custom(request_data)
    failure(request_data, "invalid_request")


def test_missing_root_and_selected_design(tmp_path, request_data):
    failure(dict(request_data, project_root=str(tmp_path / "absent")), "root_unavailable")
    request_data["design_paths"] = ["missing.md"]
    result = source.observe_project_map(request_data)
    assert {"code": "selected_design_unavailable", "source": "missing.md"} in result["findings"]


@pytest.mark.parametrize("path,data", [("bad.py", b"def :"), ("package.json", b'{"scripts":[],"secret":"HIDDEN"}'),
    ("package.json", b'{"x":1,"x":2}'), ("package.json", b'{"x":NaN}'), ("pyproject.toml", b"["),
    ("design.md", b"\xffSECRET")])
def test_parse_failures_are_unknown_without_content_leak(tmp_path, request_data, path, data):
    write(tmp_path, path, data)
    if path.endswith(".md"): request_data["design_paths"] = [path]
    result = source.observe_project_map(request_data)
    assert {"code": "parse_unavailable", "source": path} in result["findings"]
    assert "HIDDEN" not in json.dumps(result) and "SECRET" not in json.dumps(result)
    assert result["projection"]["summary"]["verified_units"] == 0


def test_dynamic_imports_are_unresolved_not_broken(tmp_path, request_data):
    write(tmp_path, "main.py", 'import importlib\nimportlib.import_module("DO_NOT_LEAK")\n')
    result = source.observe_project_map(request_data)
    assert {"code": "dynamic_import_unresolved", "source": "main.py"} in result["findings"]
    assert "DO_NOT_LEAK" not in json.dumps(result)
    assert not any(e["relation"] == "depends_on" for e in result["projection"]["bundle"]["edges"])


@pytest.mark.parametrize("kind", ["entries", "depth", "file", "aggregate", "handles", "design"])
def test_limits_fail_without_partial_current_result(tmp_path, request_data, kind):
    policy = source.SourcePolicy()
    code = "scope_limit"
    if kind == "entries":
        write(tmp_path, "a.py", "x=1"); write(tmp_path, "b.py", "x=1")
        policy = replace(policy, max_entries=1)
    elif kind == "depth":
        write(tmp_path, "a/b/main.py", "x=1")
        policy = replace(policy, max_depth=1)
    elif kind == "file":
        write(tmp_path, "main.py", "x=123456789")
        policy = replace(policy, file_bytes=3)
    elif kind == "aggregate":
        write(tmp_path, "a.py", "x=1"); write(tmp_path, "b.py", "x=2")
        policy = replace(policy, target_bytes=5)
    elif kind == "handles":
        policy = replace(policy, input_count=1)
    else:
        request_data["design_paths"] = ["a.md", "b.md"]
        policy = replace(policy, max_design_files=1)
        code = "invalid_request"
    failure(request_data, code, policy=policy)


@pytest.mark.parametrize("value", [0, True, 5001])
def test_policy_never_relaxes_limits(request_data, value):
    failure(request_data, "invalid_policy", policy=replace(source.SourcePolicy(), max_entries=value))


def test_time_limit_is_explicit(request_data, monkeypatch):
    ticks = iter([0, 11])
    monkeypatch.setattr(source.time, "monotonic", lambda: next(ticks))
    failure(request_data, "time_limit")


def test_model_node_limit_keeps_files_without_symbol_expansion(tmp_path, request_data):
    write(tmp_path, "many.py", "\n".join(f"def f{i}(): pass" for i in range(2001)))
    write(tmp_path, "later.txt", "Unselected document")
    result = source.observe_project_map(request_data)
    assert result["projection"]["summary"]["units"] == 2
    assert {"code": "symbol_detail_omitted", "source": "scope"} in result["findings"]
    assert not any(n["kind"] == "symbol" for n in result["projection"]["bundle"]["nodes"])


def test_transport_output_limit_applies_to_expanded_metadata(tmp_path, request_data):
    # Valid symbols individually, but the complete projection exceeds 768 KiB.
    write(tmp_path, "many.py", "\n".join(f"def function_with_a_long_descriptive_name_{i}(): pass" for i in range(900)))
    result = source.observe_project_map(request_data)
    assert result["projection"]["summary"]["units"] == 1
    assert {"code": "symbol_detail_omitted", "source": "scope"} in result["findings"]
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) < 768 * 1024
    assert result == source.observe_project_map(request_data)


def test_hardlink_content_never_read(tmp_path, request_data):
    target = write(tmp_path.parent, "outside.py", 'raise RuntimeError("OUTSIDE_SECRET")')
    os.link(target, tmp_path / "alias.py")
    result = source.observe_project_map(request_data)
    assert {"code": "unsupported_path", "source": "alias.py"} in result["findings"]
    assert result["counts"]["content_bytes"] == 0


def test_real_symlink_content_never_read(tmp_path, request_data):
    target = write(tmp_path.parent, "outside-link.py", 'raise RuntimeError("OUTSIDE_SECRET")')
    try:
        os.symlink(target, tmp_path / "alias.py")
    except OSError:
        pytest.skip("Creating a real symlink is not permitted on this Windows host")
    result = source.observe_project_map(request_data)
    assert {"code": "unsupported_path", "source": "alias.py"} in result["findings"]
    assert result["counts"]["content_bytes"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_real_junction_is_not_traversed(tmp_path, request_data):
    target = tmp_path.parent / "junction-target"
    target.mkdir(exist_ok=True)
    write(target, "bad.py", 'raise RuntimeError("OUTSIDE_SECRET")')
    # Only creates a fixture junction; never used to delete/move paths.
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "link"), str(target)], check=True, capture_output=True)
    result = source.observe_project_map(request_data)
    assert {"code": "unsupported_path", "source": "link"} in result["findings"]
    assert result["counts"]["content_bytes"] == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO")
def test_fifo_is_not_opened(tmp_path, request_data):
    os.mkfifo(tmp_path / "pipe.py")
    result = source.observe_project_map(request_data)
    assert {"code": "unsupported_path", "source": "pipe.py"} in result["findings"]


def test_directory_membership_change_rejects_snapshot(tmp_path, request_data, monkeypatch):
    original = source._Inventory.recheck
    def changed(self):
        write(tmp_path, "late.py", "x=1")
        return original(self)
    monkeypatch.setattr(source._Inventory, "recheck", changed)
    failure(request_data, "changed_input")


def test_unread_metadata_change_rejects_snapshot(tmp_path, request_data, monkeypatch):
    path = write(tmp_path, "opaque.bin", b"a")
    original = source._Inventory.recheck
    def changed(self):
        path.write_bytes(b"longer")
        return original(self)
    monkeypatch.setattr(source._Inventory, "recheck", changed)
    failure(request_data, "changed_input")


def test_retained_file_write_is_blocked_or_detected_and_handles_release(tmp_path, request_data, monkeypatch):
    path = write(tmp_path, "main.py", "x=1")
    original = source._Inventory.recheck
    blocked = []
    def changed(self):
        try:
            path.write_text("x=2", encoding="utf-8")
        except PermissionError:
            blocked.append(True)
        return original(self)
    monkeypatch.setattr(source._Inventory, "recheck", changed)
    if os.name == "nt":
        source.observe_project_map(request_data)
        assert blocked == [True]
    else:
        failure(request_data, "changed_input")
    path.write_text("x=3", encoding="utf-8")


def test_retained_root_replacement_is_blocked_or_detected(tmp_path, request_data, monkeypatch):
    write(tmp_path, "main.py", "x=1")
    destination = tmp_path.with_name(tmp_path.name + "-moved")
    original = source._Inventory.recheck
    blocked = []
    def changed(self):
        try:
            tmp_path.rename(destination)
        except PermissionError:
            blocked.append(True)
        return original(self)
    monkeypatch.setattr(source._Inventory, "recheck", changed)
    if os.name == "nt":
        source.observe_project_map(request_data)
        assert blocked == [True] and tmp_path.exists()
    else:
        failure(request_data, "changed_input")


def test_exact_byte_budget_and_handles_released_after_failure(tmp_path, request_data):
    path = write(tmp_path, "main.py", "x=1")
    policy = replace(source.SourcePolicy(), file_bytes=3, target_bytes=3)
    assert source.observe_project_map(request_data, policy=policy)["counts"]["content_bytes"] == 3
    failure(request_data, "scope_limit", policy=replace(policy, file_bytes=2))
    path.write_text("x=2", encoding="utf-8")


def test_file_disappearing_before_open_is_not_success(tmp_path, request_data, monkeypatch):
    path = write(tmp_path, "main.py", "x=1")
    original = setup._Snapshots.observe
    def changed(self, selected, observations, **kwargs):
        if selected == path:
            path.unlink()
        return original(self, selected, observations, **kwargs)
    monkeypatch.setattr(setup._Snapshots, "observe", changed)
    failure(request_data, "changed_input")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction root")
def test_junction_as_selected_root_is_rejected(tmp_path, request_data):
    target = tmp_path / "target-root"
    target.mkdir()
    alias = tmp_path / "alias-root"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True)
    failure(dict(request_data, project_root=str(alias)), "unsupported_path")


def test_nested_symbols_and_duplicate_names_keep_distinct_ids(tmp_path, request_data):
    write(tmp_path, "nested.py", "class Engine:\n    async def run(self):\n        def helper(): pass\n        return helper\ndef run(): pass\ndef run(): pass\n")
    result = source.observe_project_map(request_data)
    nodes = result["projection"]["bundle"]["nodes"]
    symbols = [n for n in nodes if n["kind"] == "symbol"]
    assert len(symbols) == len({n["id"] for n in symbols}) == 5
    helper = next(n for n in symbols if n["title"] == "Engine.run.helper")
    parent = next(n for n in symbols if n["title"] == "Engine.run")
    assert any(e["source"] == parent["id"] and e["target"] == helper["id"] for e in result["projection"]["bundle"]["edges"])


def test_manifest_budget_is_separate_from_bytes(tmp_path, request_data):
    write(tmp_path, "package.json", '{"dependencies":{"a":"1","b":"2"}}')
    result = source.observe_project_map(request_data, policy=replace(source.SourcePolicy(), max_ast_nodes=2))
    assert result["projection"]["summary"]["units"] == 1
    assert {"code": "parse_limit", "source": "package.json"} in result["findings"]
    assert result["observations"][0]["details"]["parse"] == "limit"


def test_ast_limit_preserves_file_identity_and_other_files_without_partial_symbols(tmp_path, request_data):
    limited = write(tmp_path, "large.py", 'def partial(): pass\n' + 'value="DO_NOT_LEAK"\n' * 100)
    write(tmp_path, "small.py", 'def visible(): pass\n')
    before = files(tmp_path)
    result = source.observe_project_map(request_data, policy=replace(source.SourcePolicy(), max_ast_nodes=30))
    projection = result["projection"]
    assert projection["summary"]["units"] == 2 and projection["summary"]["verified_units"] == 0
    assert {n["title"] for n in projection["bundle"]["nodes"] if n["kind"] == "symbol"} == {"visible"}
    assert {"code": "parse_limit", "source": "large.py"} in result["findings"]
    large = next(s for s in projection["bundle"]["sources"] if s["locator"]["path"] == "large.py")
    assert large["identity"] == "sha256:" + hashlib.sha256(limited.read_bytes()).hexdigest()
    assert "DO_NOT_LEAK" not in json.dumps(result) and files(tmp_path) == before


def test_symbol_fallback_preserves_all_file_nodes_and_has_no_dangling_references(tmp_path, request_data):
    for name in ("a.py", "b.py", "c.py"):
        write(tmp_path, name, "import os\n" + "\n".join(f"def f{i}(): pass" for i in range(800)))
    result = source.observe_project_map(request_data)
    bundle = result["projection"]["bundle"]
    assert result["projection"]["summary"]["units"] == 3
    assert len(bundle["nodes"]) == 4
    ids = {n["id"] for n in bundle["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in bundle["edges"])
    for observation in result["observations"]:
        assert observation["node"] in ids and observation["details"]["symbols"] == []
        assert observation["details"]["symbol_detail"] == "omitted"
        assert observation["details"]["imports"] == [{"level": 0, "module": "os"}]


def test_file_inventory_itself_is_never_silently_truncated(tmp_path, request_data):
    for i in range(2000):
        write(tmp_path, f"item-{i}.txt", "metadata only")
    failure(request_data, "scope_limit")


def test_parser_time_failure_is_not_downgraded_to_a_finding(tmp_path, request_data, monkeypatch):
    write(tmp_path, "main.py", "x=1")
    def timeout(*args):
        raise source.MapSourceError("time_limit")
    monkeypatch.setattr(source, "_python", timeout)
    failure(request_data, "time_limit")


def test_model_reference_failure_does_not_trigger_detail_retry(tmp_path, request_data, monkeypatch):
    write(tmp_path, "main.py", "def f(): pass")
    calls = []
    def invalid(*args):
        calls.append(True)
        raise source.model.MapValidationError("reference")
    monkeypatch.setattr(source.model, "build_map_projection", invalid)
    failure(request_data, "scope_limit")
    assert len(calls) == 1


@pytest.mark.parametrize("folder", ["_work", "devlog"])
def test_local_maintainer_material_is_explicitly_excluded(tmp_path, request_data, folder):
    write(tmp_path, folder + "/large.py", 'value="PRIVATE_BODY"\n' * 6000)
    write(tmp_path, "main.py", "x=1")
    preview = source.preview_project_map_scope(request_data)
    assert folder in preview["excluded_names"]
    assert preview["detail_policy"] == "file-overview-with-bounded-symbols-v2"
    result = source.observe_project_map(request_data)
    assert result["projection"]["summary"]["units"] == 1
    assert "PRIVATE_BODY" not in json.dumps(result)
    request_data["design_paths"] = [folder + "/design.md"]
    failure(request_data, "excluded_design")


def test_no_process_network_database_or_target_writes(tmp_path, request_data, monkeypatch):
    write(tmp_path, "main.py", 'from pathlib import Path\nPath("sentinel").write_text("executed")\n')
    before = files(tmp_path)
    before_core = sys.modules.get("cc")
    def forbidden(*args, **kwargs):
        pytest.fail("Observer attempted execution or mutation")
    with monkeypatch.context() as patch:
        for owner, name in ((subprocess, "Popen"), (socket, "socket"), (sqlite3, "connect"),
                            (Path, "write_text"), (Path, "write_bytes"), (os, "mkdir"), (os, "unlink"), (os, "rename")):
            patch.setattr(owner, name, forbidden)
        result = source.observe_project_map(request_data)
    assert files(tmp_path) == before and result["counts"]["content_bytes"] > 0
    assert sys.modules.get("cc") is before_core


def test_documented_example_and_registration(tmp_path, request_data):
    import tomllib
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs/project-map-sources.md").read_text(encoding="utf-8")
    assert "observe_project_map" in doc and "preview_project_map_scope" in doc
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cc_project_map_sources" in metadata["tool"]["setuptools"]["py-modules"]
    assert "`cc_project_map_sources.py`" in (root / "docs/architecture-index.md").read_text(encoding="utf-8")
