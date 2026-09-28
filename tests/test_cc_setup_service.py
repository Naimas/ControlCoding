"""Service effects, canonical parity, bounds and containment on external fixtures."""

from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc
import cc_setup_service as service


def isolated_import(monkeypatch):
    # Do not reload the shared module: already imported adapters retain its
    # exception/classes, and replacing them contaminates unrelated suite tests.
    spec = importlib.util.spec_from_file_location('cc_setup_service_import_probe', service.__file__)
    probe = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, probe)
    spec.loader.exec_module(probe)
    return probe


def request(root, **extra):
    return {"schema_version": 1, "operation": "minimal_init", "project_root": str(root), **extra}


def inventory(root):
    result = {}
    for p in [root, *sorted(root.rglob("*"))]:
        s = p.lstat()
        result[p.relative_to(root).as_posix()] = (
            s.st_mode, s.st_ino, s.st_mtime_ns, s.st_ctime_ns,
            p.read_bytes() if p.is_file() else None)
    return result


def write_json(root, relative, value):
    p = root / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=3) + "\n", encoding="utf-8")
    return p


def assert_parity(root):
    canonical = cc._plan_init(root)
    actual = service.preview_minimal_init(request(root))
    assert actual["directories"] == [
        {"path": p.relative_to(root).as_posix(), "action": a}
        for p, a in canonical["directories"].items()]
    assert len(actual["files"]) == len(canonical["files"])
    for item in actual["files"]:
        path = root / item["path"]
        expected = canonical["files"][path]
        assert item["action"] == expected["action"]
        assert item["reason"] == expected["reason"]
        assert item["generated_sha256"] == service._digest(expected["data"])
        intended = path.read_bytes() if item["action"] == "keep" else expected["data"]
        assert item["sha256"] == service._digest(intended)
        assert item["bytes"] == len(intended)
    assert actual["scope"] == "minimal_init"
    assert actual["apply_supported"] is False
    assert actual["freshness"] == "observed_only"
    assert actual == service.preview_minimal_init(request(root))
    return actual


@pytest.mark.parametrize("name", ["plain", "owner's progetto é"])
@pytest.mark.parametrize("git", [False, True])
def test_fresh_parity_and_complete_preservation(tmp_path, name, git):
    root = tmp_path / name
    root.mkdir()
    (tmp_path / "unrelated.secret").write_bytes(b"outside-secret")
    (root / "unrelated.txt").write_bytes(b"retain")
    if git:
        (root / ".git/hooks").mkdir(parents=True)
    before = inventory(tmp_path)
    service.read_setup_state(str(root))
    service.validate_setup_request(request(root))
    result = assert_parity(root)
    assert inventory(tmp_path) == before
    assert (".git/hooks/pre-commit" in {i["path"] for i in result["files"]}) == git


@pytest.mark.parametrize("location", [".controlcoding", ".claude"])
@pytest.mark.parametrize("zones", [None, [], {"deny": ["private/"], "warn": ["shared/"]},
                                   ["shared/", {"path": "core/", "level": "deny", "custom": 2}]])
def test_existing_custom_and_legacy_inputs(tmp_path, location, zones):
    root = tmp_path / "owner's é project"
    root.mkdir()
    write_json(root, location + "/cc_config.json", {
        "custom": {"secret": "CANARY-private-field"}, "protected_zones": zones})
    settings = deepcopy(cc.BASE_SETTINGS)
    if location == ".controlcoding":
        settings = cc._resolve_hook_commands(settings, root / "hooks")
    settings["mcpServers"] = {"CANARY-server": {"env": {"API_KEY": "CANARY-key"}}}
    write_json(root, location + "/settings.json", settings)
    (root / "CLAUDE.md").write_bytes(b"CANARY-context")
    before = inventory(tmp_path)
    result = assert_parity(root)
    state = service.read_setup_state(str(root))
    assert "CANARY" not in json.dumps([result, state])
    assert inventory(tmp_path) == before
    assert state["configuration"]["mcp_server_count"] == 1
    assert next(s for s in state["sources"] if s.get("selected"))["source"].startswith("target/" + location)


def test_repeat_installed_bytes_and_retained_hashes(tmp_path):
    # Materialize detached bytes as a fixture; never call apply from this test.
    for path, action in cc._plan_init(tmp_path)["directories"].items():
        path.mkdir(parents=True, exist_ok=True)
    for path, item in cc._plan_init(tmp_path)["files"].items():
        path.write_bytes(item["data"])
    config = tmp_path / ".controlcoding/cc_config.json"
    value = json.loads(config.read_bytes())
    value["custom"] = "keep"
    config.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    before = inventory(tmp_path)
    result = assert_parity(tmp_path)
    retained = next(x for x in result["files"] if x["path"] == ".controlcoding/cc_config.json")
    assert retained["sha256"] != retained["generated_sha256"]
    assert all(x["action"] == "keep" for x in result["files"])
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("name,content,code", [
    ("cc_config.json", b'{CANARY', "invalid_json"),
    ("cc_config.json", b'[]', "invalid_type"),
    ("cc_config.json", b'{"documentation_mode": 1}', "invalid_config"),
    ("cc_config.json", b'{"protected_zones": {"deny": false}}', "invalid_config"),
    ("settings.json", b'{"hooks": []}', "invalid_config"),
    ("settings.json", b'{"hooks": {"CANARY": [{"hooks": [{"command": 7}]}]}}', "invalid_config"),
    ("settings.json", b'{"mcpServers": []}', "invalid_config"),
    ("settings.json", b'\xff', "invalid_json"),
])
def test_invalid_source_is_not_empty_success(tmp_path, name, content, code):
    directory = tmp_path / ".controlcoding"
    directory.mkdir()
    (directory / name).write_bytes(content)
    before = inventory(tmp_path)
    for action in (lambda: service.read_setup_state(str(tmp_path)),
                   lambda: service.preview_minimal_init(request(tmp_path))):
        with pytest.raises(service.SetupServiceError) as caught:
            action()
        assert caught.value.code == code
        assert caught.value.source == "target/.controlcoding/" + name
        assert "CANARY" not in json.dumps(caught.value.as_dict()) + str(caught.value)
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("extra,code", [
    ({"operation": "setup"}, "unsupported_operation"),
    ({"operation": "apply"}, "unsupported_operation"),
    ({"operation": "test_provider"}, "unsupported_operation"),
    ({"operation": None}, "invalid_type"),
    ({"central_hooks": True}, "unsupported_operation"),
    ({"central_hooks": 1}, "invalid_type"),
    ({"central_hooks": "false"}, "invalid_type"),
    ({"schema_version": True}, "invalid_type"),
    ({"schema_version": 2}, "unsupported_version"),
    ({"CANARY": {"apply": True, "api_key": "CANARY"}}, "unknown_control"),
    ({"project_root": 7}, "invalid_root"),
    ({"project_root": "."}, "invalid_root"),
])
def test_strict_requests(tmp_path, extra, code):
    with pytest.raises(service.SetupServiceError) as caught:
        service.preview_minimal_init(request(tmp_path, **extra))
    assert caught.value.code == code
    assert "CANARY" not in json.dumps(caught.value.as_dict())


def test_missing_root_and_file_root(tmp_path):
    absent = tmp_path / "absent"
    with pytest.raises(service.SetupServiceError, match="missing_root"):
        service.validate_setup_request(request(absent))
    assert not absent.exists()
    absent.write_bytes(b"file")
    with pytest.raises(service.SetupServiceError, match="unsupported_path"):
        service.read_setup_state(str(absent))


@pytest.mark.parametrize("name", ["hooks/check_boundaries.py", ".gitignore", ".git"])
def test_conflicts_identify_fixed_source_and_preserve(tmp_path, name):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"CANARY-foreign")
    before = inventory(tmp_path)
    with pytest.raises(service.SetupServiceError) as caught:
        service.preview_minimal_init(request(tmp_path))
    assert caught.value.code in {"plan_conflict", "unsupported_path"}
    assert caught.value.source == "target/" + name
    assert "CANARY" not in str(caught.value)
    assert inventory(tmp_path) == before


def test_canonical_precedence_and_shadowed_invalid_are_explicit(tmp_path):
    write_json(tmp_path, ".controlcoding/cc_config.json", {"documentation_mode": "managed"})
    p = write_json(tmp_path, ".claude/cc_config.json", {"documentation_mode": "project_managed"})
    state = service.read_setup_state(str(tmp_path))
    assert state["configuration"]["documentation_mode"] == "managed"
    p.write_bytes(b"invalid")
    with pytest.raises(service.SetupServiceError, match="invalid_json"):
        service.read_setup_state(str(tmp_path))


@pytest.mark.parametrize("size,success", [(1048576, True), (1048577, False)])
def test_file_byte_boundary(tmp_path, size, success):
    (tmp_path / "CONTROLCODING.md").write_bytes(b"x" * size)
    if success:
        assert service.read_setup_state(str(tmp_path))["sources"][-2]["state"] == "present_unvalidated"
    else:
        with pytest.raises(service.SetupServiceError, match="too_large"):
            service.read_setup_state(str(tmp_path))


@pytest.mark.parametrize("budget,success", [(9, False), (10, True)])
def test_aggregate_byte_boundary(tmp_path, budget, success):
    (tmp_path / "CONTROLCODING.md").write_bytes(b"12345")
    (tmp_path / "CLAUDE.md").write_bytes(b"67890")
    policy = service.ReadPolicy(target_bytes=budget)
    if success:
        service.read_setup_state(str(tmp_path), policy=policy)
    else:
        with pytest.raises(service.SetupServiceError, match="too_large"):
            service.read_setup_state(str(tmp_path), policy=policy)


def test_trusted_budget_and_input_count(tmp_path):
    with pytest.raises(service.SetupServiceError, match="too_large: trusted/"):
        service.preview_minimal_init(request(tmp_path), policy=service.ReadPolicy(trusted_bytes=1))
    with service._Snapshots(tmp_path, {}, service.ReadPolicy()) as reader:
        service._require_root(reader)
        count = len(reader.entries)
    service.validate_setup_request(request(tmp_path), policy=service.ReadPolicy(input_count=count))
    with pytest.raises(service.SetupServiceError, match="input_limit"):
        service.validate_setup_request(request(tmp_path), policy=service.ReadPolicy(input_count=count - 1))


@pytest.mark.parametrize("key,value", [("file_bytes", True), ("file_bytes", 1048577),
                                       ("target_bytes", 0), ("input_count", 257)])
def test_policy_cannot_raise_hard_limits(key, value):
    with pytest.raises(service.SetupServiceError, match="invalid_policy"):
        service.ReadPolicy(**{key: value})


@pytest.mark.parametrize("value", [{"unknown": [0] * 10001}, {"unknown": [[[[0]]]]}])
def test_json_structure_budget(tmp_path, value):
    if len(value["unknown"]) == 1:
        for _ in range(33):
            value = {"unknown": value}
    write_json(tmp_path, ".controlcoding/cc_config.json", value)
    with pytest.raises(service.SetupServiceError, match="structure_limit"):
        service.preview_minimal_init(request(tmp_path))


def test_no_service_execution_or_unbounded_content_reads(tmp_path, monkeypatch, capsys):
    canary = tmp_path / "outside"
    canary.write_bytes(b"CANARY-never-read")
    root = tmp_path / "project"
    root.mkdir()
    before = inventory(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden side effect or unbounded read")

    for owner, names in [(subprocess, ("Popen", "run")), (os, ("system", "scandir")),
                         (socket, ("socket", "create_connection")),
                         (Path, ("read_bytes", "read_text", "write_bytes", "write_text", "mkdir")),
                         (cc, ("cmd_init", "_apply_init", "cmd_memory_init", "cmd_memory_bootstrap", "cmd_doctor"))]:
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    probe = isolated_import(monkeypatch)
    probe.validate_setup_request(request(root))
    probe.read_setup_state(str(root))
    probe.preview_minimal_init(request(root))
    assert capsys.readouterr() == ("", "")
    monkeypatch.undo()
    assert inventory(tmp_path) == before


def test_outside_planner_read_is_rejected_before_open(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    sentinel = tmp_path / "external-secret"
    sentinel.write_bytes(b"CANARY")
    original = cc._plan_init

    def injected(project, **kwargs):
        kwargs["observe"](sentinel, {})
        return original(project, **kwargs)

    monkeypatch.setattr(cc, "_plan_init", injected)
    with pytest.raises(service.SetupServiceError, match="unsupported_path: planner"):
        service.preview_minimal_init(request(root))


def test_changed_input_changes_fingerprint(tmp_path):
    context = tmp_path / "CONTROLCODING.md"
    context.write_bytes(b"one")
    before = service.preview_minimal_init(request(tmp_path))
    context.write_bytes(b"two")
    after = service.preview_minimal_init(request(tmp_path))
    assert before["plan_fingerprint"] != after["plan_fingerprint"]
    assert before["freshness"] == after["freshness"] == "observed_only"


def test_uninspectable_entry(tmp_path, monkeypatch):
    original = Path.lstat

    def denied(path, *args, **kwargs):
        if path == tmp_path / ".controlcoding":
            raise PermissionError("CANARY OS details")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied)
    # This injection targets Windows path metadata; POSIX uses dir_fd instead.
    if os.name != "nt":
        pytest.skip("Windows lstat injection")
    with pytest.raises(service.SetupServiceError, match="inaccessible") as caught:
        service.read_setup_state(str(tmp_path))
    assert "CANARY" not in str(caught.value)


def test_growth_during_read_is_bounded(tmp_path, monkeypatch):
    (tmp_path / "CONTROLCODING.md").write_bytes(b"12345")
    original = os.read
    count = 0

    def growing(fd, size):
        nonlocal count
        count += 1
        data = original(fd, size)
        return data or b"x" * size  # Simulate growth even where Windows denies writers.

    monkeypatch.setattr(os, "read", growing)
    with pytest.raises(service.SetupServiceError, match="too_large"):
        service.read_setup_state(str(tmp_path), policy=service.ReadPolicy(file_bytes=5))
    assert count <= 2


def test_missing_entry_created_during_preview_conflicts(tmp_path, monkeypatch):
    original = cc._plan_init

    def changed(*args, **kwargs):
        plan = original(*args, **kwargs)
        (tmp_path / "STATUS.md").write_bytes(b"concurrent owner")
        return plan

    monkeypatch.setattr(cc, "_plan_init", changed)
    with pytest.raises(service.SetupServiceError, match="changed_input: target/STATUS.md"):
        service.preview_minimal_init(request(tmp_path))
    assert (tmp_path / "STATUS.md").read_bytes() == b"concurrent owner"


def test_root_replacement_is_blocked_or_detected(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    original = cc._plan_init
    attempts = []

    def replace(*args, **kwargs):
        plan = original(*args, **kwargs)
        try:
            root.rename(tmp_path / "old")
        except PermissionError:
            attempts.append("blocked")
        else:
            attempts.append("replaced")
            root.mkdir()
        return plan

    monkeypatch.setattr(cc, "_plan_init", replace)
    if os.name == "nt":
        service.preview_minimal_init(request(root))
        assert attempts == ["blocked"]
    else:
        with pytest.raises(service.SetupServiceError, match="changed_input"):
            service.preview_minimal_init(request(root))
        assert attempts == ["replaced"]


@pytest.mark.parametrize("where", ["root", "parent", "file"])
def test_real_symlink_rejected(tmp_path, where):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"CANARY")
    root = tmp_path / "root"
    try:
        if where == "root":
            root.symlink_to(outside, target_is_directory=True)
        elif where == "parent":
            root.symlink_to(outside, target_is_directory=True)
            (outside / "child").mkdir()
            root = root / "child"
        else:
            root.mkdir()
            (root / "CONTROLCODING.md").symlink_to(outside / "secret")
    except OSError as exc:
        pytest.skip(f"real symlink creation unavailable: OS error {exc.errno}")
    with pytest.raises(service.SetupServiceError, match="unsupported_path"):
        service.read_setup_state(str(root))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
@pytest.mark.parametrize("where", ["root", "parent", "config"])
def test_real_junction_rejected(tmp_path, where):
    outside = tmp_path / "outside"
    outside.mkdir()
    write_json(outside, "cc_config.json", {"api_key": "CANARY"})
    root = tmp_path / "root"
    link = root
    if where == "config":
        root.mkdir()
        link = root / ".controlcoding"
    # Fixture setup only. The service itself is instrumented separately.
    completed = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                               capture_output=True)
    assert completed.returncode == 0
    if where == "parent":
        (outside / "child").mkdir()
        root = root / "child"
    try:
        with pytest.raises(service.SetupServiceError, match="unsupported_path"):
            service.read_setup_state(str(root))
    finally:
        link.rmdir()  # Remove the link itself, never recurse through the target.


def test_hardlink_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"CANARY")
    os.link(outside, root / "CONTROLCODING.md")
    with pytest.raises(service.SetupServiceError, match="unsupported_path"):
        service.read_setup_state(str(root))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO unavailable on Windows")
def test_fifo_rejected_without_open(tmp_path):
    os.mkfifo(tmp_path / "CONTROLCODING.md")
    with pytest.raises(service.SetupServiceError, match="unsupported_path"):
        service.read_setup_state(str(tmp_path))


def test_central_config_conflict_names_canonical_input(tmp_path):
    write_json(tmp_path, ".controlcoding/cc_config.json", {"hooks_location": "central"})
    with pytest.raises(service.SetupServiceError) as caught:
        service.preview_minimal_init(request(tmp_path))
    assert caught.value.code == "plan_conflict"
    assert caught.value.source == "target/.controlcoding/cc_config.json"


def test_missing_trusted_source(tmp_path, monkeypatch):
    # Add a missing fixed source in the trusted installation, without reading a
    # target-selected path or altering the real templates.
    monkeypatch.setattr(cc, "INIT_HOOKS", [*cc.INIT_HOOKS, "missing-panel-test.py"])
    with pytest.raises(service.SetupServiceError) as caught:
        service.preview_minimal_init(request(tmp_path))
    assert caught.value.source == "trusted/templates/hooks/missing-panel-test.py"


def test_read_never_touches_unselected_credentials_or_memory(tmp_path, monkeypatch):
    write_json(tmp_path, ".controlcoding/gateway.json", {"key": "CANARY-gateway"})
    (tmp_path / ".env").write_bytes(b"CANARY-env")
    (tmp_path / ".controlcoding/memory").mkdir()
    (tmp_path / ".controlcoding/memory/memory.db").write_bytes(b"CANARY-memory")
    opened = []
    original = service._Snapshots._open

    def record(self, path, directory):
        if not directory:
            opened.append(path)
        return original(self, path, directory)

    monkeypatch.setattr(service._Snapshots, "_open", record)
    result = service.read_setup_state(str(tmp_path))
    assert opened == []
    assert "CANARY" not in json.dumps(result)


@pytest.mark.skipif(os.name != "nt", reason="Windows pinned handle sharing")
def test_concurrent_file_write_is_denied_and_handle_released(tmp_path, monkeypatch):
    p = tmp_path / "CONTROLCODING.md"
    p.write_bytes(b"original")
    original = cc._plan_init
    attempts = []

    def changing(*args, **kwargs):
        plan = original(*args, **kwargs)
        with pytest.raises(PermissionError):
            p.write_bytes(b"foreign")
        attempts.append(True)
        return plan

    monkeypatch.setattr(cc, "_plan_init", changing)
    service.preview_minimal_init(request(tmp_path))
    assert attempts == [True] and p.read_bytes() == b"original"
    p.write_bytes(b"after return")


def test_file_removed_between_inspection_and_open_is_changed(tmp_path, monkeypatch):
    p = tmp_path / "CONTROLCODING.md"
    p.write_bytes(b"original")
    original = service._Snapshots._open

    def remove(self, path, directory):
        if path == p:
            p.unlink()
        return original(self, path, directory)

    monkeypatch.setattr(service._Snapshots, "_open", remove)
    with pytest.raises(service.SetupServiceError, match="changed_input"):
        service.read_setup_state(str(tmp_path))


def test_replacement_before_open_never_reads_outside_file(tmp_path, monkeypatch):
    p = tmp_path / "CONTROLCODING.md"
    p.write_bytes(b"original")
    outside = tmp_path / "external"
    outside.write_bytes(b"CANARY")
    original = service._Snapshots._open
    read_calls = []
    real_read = os.read

    def replace(self, path, directory):
        if path == p:
            p.unlink()
            os.link(outside, p)
        return original(self, path, directory)

    def read(fd, size):
        read_calls.append(fd)
        return real_read(fd, size)

    monkeypatch.setattr(service._Snapshots, "_open", replace)
    monkeypatch.setattr(os, "read", read)
    # Windows can deny hardlink publication itself while the directory is pinned.
    with pytest.raises(service.SetupServiceError, match="unsupported_path|inaccessible"):
        service.read_setup_state(str(tmp_path))
    assert read_calls == []


def test_changed_byte_recheck_even_when_metadata_matches(tmp_path, monkeypatch):
    p = tmp_path / "CONTROLCODING.md"
    p.write_bytes(b"one")
    original = os.read
    calls = 0

    def drift(fd, size):
        nonlocal calls
        calls += 1
        actual = original(fd, size)
        if calls == 3:  # Initial data + EOF, then final content recheck.
            return b"two"
        return actual

    monkeypatch.setattr(os, "read", drift)
    with pytest.raises(service.SetupServiceError, match="changed_input"):
        service.read_setup_state(str(tmp_path))


@pytest.mark.parametrize("bytes_total,success", [(8388608, True), (8388609, False)])
def test_hard_aggregate_boundary(tmp_path, bytes_total, success):
    paths = []
    left = bytes_total
    while left:
        p = tmp_path / f"part-{len(paths)}"
        size = min(left, 1048576)
        p.write_bytes(b"x" * size)
        paths.append(p)
        left -= size
    with service._Snapshots(tmp_path, {}, service.ReadPolicy()) as reader:
        def consume():
            for p in paths:
                reader.observe(p, {})
            reader.recheck()
        if success:
            consume()
            assert reader.used["target"] == bytes_total
        else:
            with pytest.raises(service.SetupServiceError, match="too_large"):
                consume()


def test_loaded_planner_identity_drift_fails(tmp_path, monkeypatch):
    original = cc._plan_init
    monkeypatch.setattr(cc, "BASE_SETTINGS", deepcopy(cc.BASE_SETTINGS))

    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        cc.BASE_SETTINGS["custom"] = True
        return result

    monkeypatch.setattr(cc, "_plan_init", drift)
    with pytest.raises(service.SetupServiceError, match="changed_input: planner"):
        service.preview_minimal_init(request(tmp_path))


def test_expanded_generated_settings_limit(tmp_path):
    # Compact input can expand when canonical hook paths/JSON indentation are
    # generated. The service must reject the complete oversized result.
    settings = {"hooks": {"Custom": [{"hooks": [
        {"command": "python hooks/custom.py"} for _ in range(1900)]}]}}
    write_json(tmp_path, ".claude/settings.json", settings)
    # Test with a long, still supported project path; fixture path limits vary,
    # so inject the canonical planner result expansion without creating it.
    original = cc._plan_init

    def expanded(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan["files"][tmp_path / ".controlcoding/settings.json"]["data"] = b"x" * 1048577
        return plan

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(cc, "_plan_init", expanded)
        with pytest.raises(service.SetupServiceError, match="output_limit"):
            service.preview_minimal_init(request(tmp_path))


def test_trusted_exact_aggregate_boundary(tmp_path):
    used_paths = list(service._trusted(cc))
    total = sum(p.stat().st_size for p in used_paths)
    service.preview_minimal_init(request(tmp_path), policy=service.ReadPolicy(trusted_bytes=total))
    with pytest.raises(service.SetupServiceError, match="too_large: trusted/"):
        service.preview_minimal_init(request(tmp_path), policy=service.ReadPolicy(trusted_bytes=total - 1))


def test_import_itself_has_no_discovery(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("import discovered or mutated the filesystem")
    for name in ("lstat", "stat", "resolve", "read_bytes", "read_text", "mkdir"):
        monkeypatch.setattr(Path, name, forbidden)
    for owner, name in ((os, "scandir"), (os, "system"), (subprocess, "Popen"),
                        (socket, "socket")):
        monkeypatch.setattr(owner, name, forbidden)
    isolated_import(monkeypatch)


def test_distribution_declares_module():
    import tomllib
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cc_setup_service" in metadata["tool"]["setuptools"]["py-modules"]


def test_fresh_process_lazy_core_import_and_operations_have_no_effects(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git/hooks").mkdir(parents=True)
    (root / ".git/config").write_text(
        '[filter "trap"]\n clean = forbidden-filter\n process = forbidden-process\n', encoding="utf-8")
    (root / ".gitattributes").write_text('* filter=trap\n', encoding="utf-8")
    before = inventory(tmp_path)
    script = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
events = []
def audit(event, args):
    if event in {"subprocess.Popen", "os.system", "os.mkdir", "os.remove", "os.rename",
                 "os.rmdir", "os.chmod", "os.utime", "sqlite3.connect"} or event.startswith("socket."):
        raise AssertionError("forbidden effect: " + event)
    if event == "open":
        name, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("write open")
        if isinstance(name, str):
            events.append(name)
sys.addaudithook(audit)
import cc_setup_service as service
assert "cc" not in sys.modules
request = {"schema_version": 1, "operation": "minimal_init", "project_root": sys.argv[2]}
service.validate_setup_request(request)
service.read_setup_state(sys.argv[2])
service.preview_minimal_init(request)
assert not any(name.endswith((".git/config", ".git\\config", ".gitattributes")) for name in events)
print("no-effects")
'''
    process = subprocess.run([sys.executable, "-B", "-c", script,
                              str(Path(__file__).parents[1] / "scripts"), str(root)],
                             cwd=tmp_path, capture_output=True, text=True,
                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "no-effects"
    assert inventory(tmp_path) == before
