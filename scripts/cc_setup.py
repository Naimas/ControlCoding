#!/usr/bin/env python3
"""
cc_setup - Chat-first setup/apply functions extracted from cc.py (TL4).

Contains:
- cmd_setup: base setup executor for chat-generated answers files
- cmd_setup_engagement: engagement level configuration executor
- Helper functions: _ask, _ask_yn, _detect_backends, _scan_dirs, _generate_claude_md
"""

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import date
from pathlib import Path

from cc_memory_lib.runtime import core_runtime_error, memory_runtime_error

# Import shared utilities and constants from cc.py
# cc_setup.py lives in the same directory as cc.py
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from cc import (
    ok, warn, info, green,
    TEMPLATES_DIR, SCRIPTS_DIR,
    CANONICAL_CONTEXT_FILENAME,
    LEGACY_CONTEXT_FILENAME,
    CONTROL_PLANE_DIRNAME,
    LEGACY_CONTROL_PLANE_DIRNAME,
    PUBLIC_CHAT_STARTER_RELATIVE_PATH,
    load_settings, save_settings,
    cmd_init, cmd_install, cmd_doctor,
    cmd_memory_init, cmd_memory_scan,
    SUPPORTED_CONSULTATION_BACKENDS,
    _GATEWAY_VALID_USER_HOSTS,
    _control_plane_path,
    _control_plane_read_path,
    _control_plane_display_path,
    _derive_host_profile,
    _format_host_gate_contract_lines,
    _host_context_export_spec,
    _adapter_plan_payload,
    _adapter_marker_payload,
    _emit_adapter_preview,
    _apply_adapter_transaction,
    _normalize_enabled_hosts,
    _read_context_source,
    _write_host_launcher_assets,
    _write_host_integration_assets,
)


def _cleanup_temporary_file(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


def _save_settings_atomic(path: Path, settings: dict, *, os_module=os, json_module=json):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary_name)
    try:
        temporary_file = os_module.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with temporary_file as stream:
            json_module.dump(settings, stream, indent=2)
            stream.write("\n")
            stream.flush()
        os_module.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os_module.close(descriptor)
        _cleanup_temporary_file(temporary_path)


def _install_pack_files_atomic(project: Path, pack_files: dict, *, warn_callback, ok_callback):
    """Compatibility entry point for a single pack, with conservative publication."""
    plan = _plan_pack_files(project, [pack_files])
    _apply_pack_files(plan, ok_callback=ok_callback)


def _pack_lstat(path: Path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _pack_safe_kind(path: Path, *, expected: str | None = None):
    entry = _pack_lstat(path)
    if entry is None:
        return None
    reparse = getattr(entry, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(entry.st_mode) or reparse:
        raise OSError(f"unsafe symlink or reparse path: {path}")
    kind = "file" if stat.S_ISREG(entry.st_mode) else "dir" if stat.S_ISDIR(entry.st_mode) else "unsafe"
    if kind == "unsafe" or (expected is not None and kind != expected):
        raise OSError(f"unsafe {kind} path: {path} (expected {expected or 'regular file or directory'})")
    return entry


def _pack_check_parents(path: Path):
    for parent in reversed(path.parents):
        _pack_safe_kind(parent, expected="dir")


def _pack_snapshot(path: Path, data: bytes):
    entry = _pack_safe_kind(path, expected="file")
    return (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns,
            entry.st_ctime_ns, data)


def _pack_target_decision(path: Path, wanted: bytes):
    _pack_check_parents(path)
    entry = _pack_safe_kind(path)
    if entry is None:
        return "create", None
    _pack_safe_kind(path, expected="file")
    current = path.read_bytes()
    if current != wanted:
        raise OSError(f"content conflict at {path}: existing file differs; reconcile it before install")
    return "skip", _pack_snapshot(path, current)


def _pack_read_source(path: Path):
    _pack_check_parents(path)
    _pack_safe_kind(path, expected="file")
    if _pack_lstat(path) is None:
        raise OSError(f"pack source missing: {path}")
    return path.read_bytes()


def _plan_pack_files(project: Path, pack_maps: list[dict]):
    """Read every source and inspect every target before the first write."""
    _pack_check_parents(project)
    _pack_safe_kind(project, expected="dir")
    files = {}
    for pack_files in pack_maps:
        for dest_dir, sources in pack_files.items():
            relative = Path(dest_dir)
            if relative.is_absolute() or ".." in relative.parts:
                raise OSError(f"unsafe pack destination: {dest_dir}")
            for source in sources:
                data = _pack_read_source(source)
                target = project / relative / source.name
                if target in files:
                    if files[target]["data"] != data:
                        raise OSError(f"conflicting selected pack sources for {target}")
                    continue
                decision, snapshot = _pack_target_decision(target, data)
                files[target] = {"source": source, "data": data,
                                 "decision": decision, "snapshot": snapshot}
    return files


def _pack_recheck_file(path: Path, item: dict):
    decision, snapshot = _pack_target_decision(path, item["data"])
    if decision != item["decision"] or (decision == "skip" and snapshot != item["snapshot"]):
        raise OSError(f"pack target changed after preflight: {path}")


def _pack_ensure_directory(path: Path):
    _pack_check_parents(path)
    entry = _pack_safe_kind(path)
    if entry is None:
        path.mkdir()
    else:
        _pack_safe_kind(path, expected="dir")


def _pack_publish_file(path: Path, item: dict):
    """Publish a staged copy only if no destination has appeared."""
    _pack_check_parents(path)
    if _pack_lstat(path) is not None:
        raise OSError(f"pack target appeared after preflight: {path}")
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(item["source"], temporary)
        if temporary.read_bytes() != item["data"]:
            raise OSError(f"pack source changed after preflight: {item['source']}")
        _pack_check_parents(path)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_pack_files(files: dict, *, ok_callback):
    for path, item in files.items():
        _pack_recheck_file(path, item)
    for path, item in files.items():
        if item["decision"] == "skip":
            ok_callback(f"Skipped identical {path}")
            continue
        for parent in reversed(path.parents):
            if _pack_lstat(parent) is None:
                _pack_ensure_directory(parent)
        _pack_publish_file(path, item)
        ok_callback(f"Copied {path}")


def _plan_pack_settings(project: Path, packs: list[str], mcp_configs: dict):
    target = project / CONTROL_PLANE_DIRNAME / "settings.json"
    legacy = project / LEGACY_CONTROL_PLANE_DIRNAME / "settings.json"
    _pack_check_parents(target)
    target_entry = _pack_safe_kind(target)
    read_path = target if target_entry is not None else legacy
    _pack_check_parents(read_path)
    read_entry = _pack_safe_kind(read_path)
    original = None
    settings = {}
    if read_entry is not None:
        _pack_safe_kind(read_path, expected="file")
        original = read_path.read_bytes()
        settings = json.loads(original.decode("utf-8"))
        if not isinstance(settings, dict):
            raise OSError(f"settings must be a JSON object: {read_path}")
    merged = deepcopy(settings)
    for pack in packs:
        if pack in mcp_configs:
            servers = merged.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise OSError(f"mcpServers must be a JSON object: {read_path}")
            for name, config in mcp_configs[pack].items():
                servers.setdefault(name, deepcopy(config))
    if target_entry is not None and merged != settings:
        raise OSError(
            f"settings conflict at {target}: required MCP entries are missing; "
            "reconcile settings explicitly before install (existing settings are never overwritten)"
        )
    action = "skip" if target_entry is not None else "create"
    snapshot = _pack_snapshot(target, original) if target_entry is not None else None
    return {"path": target, "read_path": read_path, "read_bytes": original,
            "snapshot": snapshot, "action": action,
            "data": (json.dumps(merged, indent=2) + "\n").encode("utf-8")}


def _plan_pack_helper(project: Path, helper_dir: Path):
    _pack_check_parents(helper_dir)
    _pack_safe_kind(helper_dir, expected="dir")
    if _pack_lstat(helper_dir) is None:
        raise OSError(f"pack helper source missing: {helper_dir}")
    directories = []
    files = {}
    for root, names, filenames in os.walk(helper_dir, followlinks=False):
        root = Path(root)
        for name in names:
            source = root / name
            _pack_safe_kind(source, expected="dir")
            directories.append(source.relative_to(helper_dir))
        for name in filenames:
            source = root / name
            files[source.relative_to(helper_dir)] = {"source": source, "data": _pack_read_source(source)}
    destination = project.parent / (project.name + "-helper")
    _pack_check_parents(destination)
    existing = _pack_safe_kind(destination)
    if existing is not None:
        _pack_safe_kind(destination, expected="dir")
    return {"path": destination, "action": "skip" if existing is not None else "create",
            "snapshot": (existing.st_dev, existing.st_ino, existing.st_mtime_ns) if existing else None,
            "directories": directories, "files": files}


def _plan_pack_install(project: Path, packs: list[str], pack_files: dict,
                       mcp_configs: dict, helper_dir: Path):
    files = _plan_pack_files(project, [pack_files.get(pack, {}) for pack in packs])
    settings = _plan_pack_settings(project, packs, mcp_configs)
    bridge = project / ".bridge" if "multi-agent" in packs else None
    if bridge is not None:
        _pack_check_parents(bridge)
        _pack_safe_kind(bridge, expected="dir")
    helper = _plan_pack_helper(project, helper_dir) if bridge is not None else None
    return {"files": files, "settings": settings, "bridge": bridge, "helper": helper}


def _pack_recheck_settings(settings: dict):
    target = settings["path"]
    if settings["action"] not in {"skip", "create"}:
        raise OSError(f"settings conflict at {target}: stale update plan; reconcile settings and replan")
    _pack_check_parents(target)
    current = _pack_safe_kind(target)
    if settings["snapshot"] is None:
        if current is not None:
            raise OSError(f"settings appeared after preflight: {target}")
    elif current is None or _pack_snapshot(target, target.read_bytes()) != settings["snapshot"]:
        raise OSError(f"settings changed after preflight: {target}")
    read_path = settings["read_path"]
    if read_path != target:
        _pack_check_parents(read_path)
        entry = _pack_safe_kind(read_path)
        current_bytes = read_path.read_bytes() if entry is not None else None
        if current_bytes != settings["read_bytes"]:
            raise OSError(f"legacy settings changed after preflight: {read_path}")


def _pack_recheck_helper(helper: dict):
    if helper is None:
        return
    path = helper["path"]
    _pack_check_parents(path)
    current = _pack_safe_kind(path)
    if helper["action"] == "create" and current is not None:
        raise OSError(f"helper target appeared after preflight: {path}")
    if helper["action"] == "skip":
        if current is None or (current.st_dev, current.st_ino, current.st_mtime_ns) != helper["snapshot"]:
            raise OSError(f"helper target changed after preflight: {path}")


def _pack_write_settings(settings: dict):
    _pack_recheck_settings(settings)
    if settings["action"] == "skip":
        return
    target = settings["path"]
    _pack_ensure_directory(target.parent)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(settings["data"])
            stream.flush()
        os.link(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _apply_pack_install(plan: dict, *, ok_callback):
    files = plan["files"]
    for path, item in files.items():
        _pack_recheck_file(path, item)
    _pack_recheck_settings(plan["settings"])
    _pack_recheck_helper(plan["helper"])
    bridge = plan["bridge"]
    if bridge is not None:
        _pack_check_parents(bridge)
        _pack_safe_kind(bridge, expected="dir")
    _apply_pack_files(files, ok_callback=ok_callback)
    if bridge is not None:
        _pack_ensure_directory(bridge)
        ok_callback(f"Ready {bridge}")
    helper = plan["helper"]
    if helper is not None and helper["action"] == "create":
        _pack_recheck_helper(helper)
        helper["path"].mkdir()
        for relative in helper["directories"]:
            _pack_ensure_directory(helper["path"] / relative)
        for relative, item in helper["files"].items():
            _pack_publish_file(helper["path"] / relative,
                               {"source": item["source"], "data": item["data"]})
        ok_callback(f"Created helper session template at {helper['path']}")
    elif helper is not None:
        ok_callback(f"Skipped existing helper directory {helper['path']}")
    _pack_write_settings(plan["settings"])


SETUP_INTENT_FILENAME = "setup_intent.json"
MEMORY_BOOTSTRAP_RECEIPT_FILENAME = "memory_bootstrap_receipt.json"
MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE = "governed_scope"
ANSWERS_FILE_SECTIONS = {"setup", "engagement", "project_setup"}
SETUP_ADVANCED_PACKS = (
    "debug-tools",
    "session-manager",
    "multi-agent",
    "dashboard",
)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    """Prompt user for input with optional default."""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"  {prompt}: ").strip()


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt."""
    suffix = "[Y/n]" if default else "[y/N]"
    result = input(f"  {prompt} {suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes", "si")


def _ask_or_default(prompt: str, default: str = "", apply_answers: bool = False) -> str:
    if apply_answers:
        return default
    return _ask(prompt, default)


def _ask_yn_or_default(prompt: str, default: bool = True, apply_answers: bool = False) -> bool:
    if apply_answers:
        return default
    return _ask_yn(prompt, default)


def _host_requires_repo_boundary(host_profile: dict[str, object]) -> bool:
    return str(host_profile.get("inlineBoundaryGate", "none")) == "none"


def _ensure_setup_repo_boundary(project: Path, host_profile: dict[str, object]) -> bool:
    if not _host_requires_repo_boundary(host_profile):
        return True
    if (project / ".git").exists():
        return True
    git_binary = shutil.which("git")
    if git_binary is None:
        warn(
            "Selected host has no inline gate and git is not available. "
            "Install git or run `git init` manually, then re-run `cc doctor`."
        )
        return False
    info(
        "Selected host has no inline gate. Initializing a local git repository so "
        "repo boundary and verification gates can be wired during setup."
    )
    try:
        result = subprocess.run(
            [git_binary, "init"],
            cwd=str(project),
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        warn(
            f"Automatic git init failed: {exc}. "
            "Run `git init` manually, then re-run `cc doctor`."
        )
        return False
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        warn(
            "Automatic git init failed: "
            f"{detail}. Run `git init` manually, then re-run `cc doctor`."
        )
        return False
    ok("Initialized git repository for repo-side boundary and verification gates")
    return True


def _normalize_stack_value(value: str) -> str:
    cleaned = _normalize_uncertain_text(value)
    if cleaned:
        return cleaned
    return "(to be finalized after kickoff)"


def _normalize_arch_value(value: str) -> str:
    cleaned = _normalize_uncertain_text(value)
    if cleaned:
        return cleaned
    return "(to be clarified after kickoff)"


def _normalize_uncertain_text(value: str, fallback: str = "") -> str:
    cleaned = value.strip()
    if not cleaned:
        return fallback
    normalized = " ".join(cleaned.lower().split())
    uncertain_markers = {
        "i have no idea",
        "i have no idea?",
        "i dont know",
        "i don't know",
        "i dunno",
        "idk",
        "not sure",
        "unknown",
        "n/a",
        "?",
    }
    if normalized in uncertain_markers:
        return fallback
    if normalized.startswith("what do you mean") or normalized.startswith("what does this even mean"):
        return fallback
    if normalized.startswith("what that this even means"):
        return fallback
    return cleaned


def _format_found_items(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if isinstance(item, str) and item.strip()]
    return ", ".join(cleaned) if cleaned else "(none detected)"


def _normalize_memory_default_policy(value: str | None) -> str:
    normalized = str(value or MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE).strip().lower()
    if normalized in {"", "default", "core", "governed", "governed-scope"}:
        return MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE
    if normalized == MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE:
        return normalized
    if normalized in {"deferred", "disabled", "off"}:
        return "deferred"
    return MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE


def _write_memory_bootstrap_receipt(project: Path, payload: dict) -> str:
    path = _control_plane_path(project, MEMORY_BOOTSTRAP_RECEIPT_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return _control_plane_display_path(MEMORY_BOOTSTRAP_RECEIPT_FILENAME)


def _bootstrap_default_project_memory(project: Path,
                                      project_short: str,
                                      memory_default_policy: str) -> dict:
    policy = _normalize_memory_default_policy(memory_default_policy)
    receipt = {
        "schema": "controlcoding.memory_bootstrap_receipt.v1",
        "date": date.today().isoformat(),
        "memory_default_policy": policy,
        "memoryEnabledByDefault": policy == MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE,
        "scanScope": "governed" if policy == MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE else "none",
        "fullRepoScan": False,
        "ocrRun": False,
        "fileMoves": False,
        "graphPromotion": False,
        "initialized": False,
        "scanned": False,
        "status": "skipped",
    }
    if policy != MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE:
        receipt["status"] = "deferred"
        receipt_path = _write_memory_bootstrap_receipt(project, receipt)
        receipt["receiptPath"] = receipt_path
        return receipt

    init_result = cmd_memory_init(
        project,
        mode="full",
        project_short=project_short,
        profile="project",
    )
    receipt["initialized"] = init_result == 0
    if init_result != 0:
        receipt["status"] = "memory_init_failed"
        receipt_path = _write_memory_bootstrap_receipt(project, receipt)
        receipt["receiptPath"] = receipt_path
        return receipt

    scan_result = cmd_memory_scan(project, scope="governed")
    receipt["scanned"] = scan_result == 0
    receipt["status"] = "completed" if scan_result == 0 else "governed_scan_failed"
    receipt_path = _write_memory_bootstrap_receipt(project, receipt)
    receipt["receiptPath"] = receipt_path
    return receipt


def _detect_obvious_environment_inventory() -> dict:
    toolchains: list[str] = []
    package_managers: list[str] = []
    toolchain_checks = [
        ("git", "Git"),
        ("python", "Python"),
        ("py", "Python launcher"),
        ("node", "Node.js"),
        ("cmake", "CMake"),
        ("java", "Java"),
        ("dotnet", ".NET SDK"),
        ("go", "Go"),
        ("cargo", "Rust/Cargo"),
    ]
    package_manager_checks = [
        ("pip", "pip"),
        ("uv", "uv"),
        ("npm", "npm"),
        ("pnpm", "pnpm"),
        ("yarn", "yarn"),
        ("ollama", "Ollama"),
    ]
    for command, label in toolchain_checks:
        if shutil.which(command):
            toolchains.append(label)
    for command, label in package_manager_checks:
        if shutil.which(command):
            package_managers.append(label)
    return {
        "toolchains": toolchains,
        "package_managers": package_managers,
    }


def _normalize_platforms_text(value: str) -> list[str]:
    cleaned = _normalize_uncertain_text(value, "")
    if not cleaned:
        return []
    items: list[str] = []
    for line in cleaned.replace("\r", "\n").split("\n"):
        for part in line.split(","):
            normalized = part.strip()
            if normalized:
                items.append(normalized)
    return items


def _build_environment_policy(prefill: dict | None,
                              obvious_inventory: dict,
                              apply_answers: bool = False) -> dict:
    defaults = prefill if isinstance(prefill, dict) else {}
    inspection_default = str(defaults.get("inspection_permission", "denied"))
    install_default = str(defaults.get("install_permission", "denied"))
    isolation_default = str(defaults.get("install_isolation_preference", "unspecified"))
    venv_default = str(defaults.get("venv_preference", "unspecified"))
    preferred_locations = defaults.get("preferred_install_locations", {})
    if not isinstance(preferred_locations, dict):
        preferred_locations = {}

    print(f"\n--- Environment and Install Policy ---\n")
    print("  Obvious local discovery found these existing tools before any deeper inspection:")
    print(f"  Toolchains: {_format_found_items(obvious_inventory.get('toolchains', []))}")
    print(f"  Package managers: {_format_found_items(obvious_inventory.get('package_managers', []))}")
    print("  Broader inspection is optional and requires your permission.")
    allow_broad_inspection = _ask_yn_or_default(
        "Allow broader local environment inspection if needed later?",
        default=inspection_default == "granted",
        apply_answers=apply_answers,
    )
    print("  If setup later finds missing dependencies, it will ask before installing anything.")
    allow_install = _ask_yn_or_default(
        "May setup install missing dependencies if they become necessary?",
        default=install_default == "granted",
        apply_answers=apply_answers,
    )

    install_isolation_preference = "unspecified"
    venv_preference = "not_applicable"
    preferred_location = ""
    if allow_install:
        print("  When installation is needed, I recommend isolation by default to avoid polluting the machine.")
        print("  [1] Isolated environment (recommended)")
        print("      Good default when the ecosystem supports it. For Python, this usually means a venv.")
        print("  [2] Project-local install")
        print("      Keep tools or packages local to the project without a full virtual environment strategy.")
        print("  [3] Direct/system install")
        print("      Only choose this if you intentionally want machine-wide installation.")
        install_mapping = {
            "1": "isolated",
            "2": "project_local",
            "3": "direct",
        }
        install_reverse = {value: key for key, value in install_mapping.items()}
        install_choice = _ask_or_default(
            "Preferred install style",
            install_reverse.get(isolation_default, "1"),
            apply_answers=apply_answers,
        )
        install_isolation_preference = install_mapping.get(install_choice, "isolated")
        if install_isolation_preference == "isolated":
            venv_preference = "prefer" if _ask_yn_or_default(
                "If Python packages are needed, prefer a venv?",
                default=venv_default != "avoid",
                apply_answers=apply_answers,
            ) else "avoid"
        preferred_location = _normalize_uncertain_text(
            _ask_or_default(
                "Preferred install location or path override (optional)",
                str(preferred_locations.get("default", "")),
                apply_answers=apply_answers,
            ),
            "",
        )

    return {
        "obvious_discovery_complete": True,
        "inspection_permission": "granted" if allow_broad_inspection else "denied",
        "detected_toolchains": list(obvious_inventory.get("toolchains", [])),
        "detected_package_managers": list(obvious_inventory.get("package_managers", [])),
        "missing_requirements": [],
        "install_permission": "granted" if allow_install else "denied",
        "install_isolation_preference": install_isolation_preference,
        "preferred_install_locations": {"default": preferred_location} if preferred_location else {},
        "venv_preference": venv_preference,
    }


def _ask_product_form(default: str = "other", apply_answers: bool = False) -> str:
    print("  What kind of product is this?")
    print("  [1] Desktop application or game")
    print("  [2] Web application")
    print("  [3] Mobile application")
    print("  [4] Backend service or API")
    print("  [5] Library or SDK")
    print("  [6] Plugin or integration")
    print("  [7] Other")
    mapping = {
        "1": "desktop",
        "2": "web",
        "3": "mobile",
        "4": "service",
        "5": "library",
        "6": "plugin",
        "7": "other",
    }
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "7"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "other")


def _ask_tooling_tolerance(default: str = "medium", apply_answers: bool = False) -> str:
    print("  How much external tooling or SDK setup are you willing to accept?")
    print("  [1] Low")
    print("      Prefer lighter setups and fewer downloads.")
    print("  [2] Medium")
    print("      Balanced. Some external tooling is acceptable if it improves fit.")
    print("  [3] High")
    print("      Heavy toolchains or engines are acceptable if they are the best fit.")
    mapping = {"1": "low", "2": "medium", "3": "high"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "2"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "medium")


def _ask_control_preference(default: str = "balanced", apply_answers: bool = False) -> str:
    print("  What matters more right now?")
    print("  [1] Fastest iteration")
    print("      Reach a usable slice quickly, even with a higher-level stack.")
    print("  [2] Balanced")
    print("      Practical delivery with reasonable technical control.")
    print("  [3] Maximum low-level control")
    print("      Accept more engineering cost for deeper technical control.")
    mapping = {"1": "speed", "2": "balanced", "3": "low_level"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "2"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "balanced")


def _build_product_profile(prefill: dict | None, apply_answers: bool = False) -> dict:
    defaults = prefill if isinstance(prefill, dict) else {}
    print(f"\n--- Product Framing ---\n")
    product_form = _ask_product_form(str(defaults.get("product_form", "other")), apply_answers=apply_answers)
    delivery_model = _normalize_uncertain_text(
        _ask_or_default(
            "Runtime or delivery constraints (optional, plain language is fine)",
            str(defaults.get("runtime_constraints", defaults.get("delivery_model", ""))),
            apply_answers=apply_answers,
        ),
        "",
    )
    target_platforms = _normalize_platforms_text(
        _ask_or_default(
            "Target platforms (optional, comma-separated is fine)",
            ", ".join(defaults.get("target_platforms", [])) if isinstance(defaults.get("target_platforms"), list) else str(defaults.get("target_platforms", "")),
            apply_answers=apply_answers,
        )
    )
    tooling_tolerance = _ask_tooling_tolerance(
        str(defaults.get("tooling_tolerance", "medium")),
        apply_answers=apply_answers,
    )
    control_preference = _ask_control_preference(
        str(defaults.get("control_preference", "balanced")),
        apply_answers=apply_answers,
    )
    return {
        "product_form": product_form,
        "runtime_constraints": delivery_model,
        "target_platforms": target_platforms,
        "tooling_tolerance": tooling_tolerance,
        "control_preference": control_preference,
    }


VALID_PLANNING_MODES = frozenset({
    "solo_structured",
    "solo_structured_manual_consultation_ready",
    "orchestrated_specialists",
})

VALID_PLANNING_AUTHORITIES = frozenset({
    "single_author",
    "single_author_with_manual_consultation",
    "orchestrated_multi_role",
})


def _planning_profile_for_tier(tier: str, manual_consultation_allowed: bool = False) -> dict:
    normalized_tier = str(tier or "core").strip().lower()
    if normalized_tier == "core":
        if manual_consultation_allowed:
            return {
                "tier": "core",
                "planning_mode": "solo_structured_manual_consultation_ready",
                "planning_authority": "single_author_with_manual_consultation",
                "manual_consultation_allowed": True,
            }
        return {
            "tier": "core",
            "planning_mode": "solo_structured",
            "planning_authority": "single_author",
            "manual_consultation_allowed": False,
        }
    if normalized_tier not in {"agents", "studio"}:
        normalized_tier = "core"
    return {
        "tier": normalized_tier,
        "planning_mode": "orchestrated_specialists",
        "planning_authority": "orchestrated_multi_role",
        "manual_consultation_allowed": False,
    }


def _planning_mode_label(value: str) -> str:
    labels = {
        "solo_structured": "Direct structured planning",
        "solo_structured_manual_consultation_ready": "Direct structured planning + manual consultation ready",
        "orchestrated_specialists": "Specialist-assisted planning",
    }
    return labels.get(value, value or "(not set)")


def _planning_authority_label(value: str) -> str:
    labels = {
        "single_author": "Single author",
        "single_author_with_manual_consultation": "Single author + manual consultation",
        "orchestrated_multi_role": "Orchestrated multi-role planning",
    }
    return labels.get(value, value or "(not set)")


def _planning_summary_line(planning: dict) -> str:
    return (
        f"{_planning_mode_label(str(planning.get('planning_mode', 'solo_structured')))} / "
        f"{_planning_authority_label(str(planning.get('planning_authority', 'single_author')))}"
    )


def _ask_controlcoding_usage_model(default_tier: str = "core",
                                   default_manual_consultation: bool = False,
                                   apply_answers: bool = False) -> dict:
    print("  Choose the baseline way you want to use ControlCoding in this project.")
    print("  The host controls where enforcement happens (inline vs repo-side). This choice controls whether CC stays direct, allows one manual consultation path, or assumes specialist-assisted workflows.")
    print("  [1] Core")
    print("      Direct structured ControlCoding in the main chat. Default serious baseline.")
    print("  [2] Core + manual consultation")
    print("      Still direct and user-mediated. One bounded external consultation path may be prepared if a real blocker appears.")
    print("  [3] Agents")
    print("      Bounded helper roles on the chosen host, kept explicit through prompt, folder, and behavior contracts.")
    print("  [4] Studio")
    print("      Later optional extra surface. Keep this off the current release path unless you are intentionally working on future/internal UI.")
    mapping = {
        "1": ("core", False),
        "2": ("core", True),
        "3": ("agents", False),
        "4": ("studio", False),
    }
    if default_tier == "core" and default_manual_consultation:
        default_choice = "2"
    else:
        default_choice = {"core": "1", "agents": "3", "studio": "4"}.get(default_tier, "1")
    choice = _ask_or_default("Choice", default_choice, apply_answers=apply_answers)
    selected_tier, manual_consultation_allowed = mapping.get(choice, mapping[default_choice])
    profile = _planning_profile_for_tier(selected_tier, manual_consultation_allowed)
    info("Usage model: " + _planning_summary_line(profile))
    if profile["manual_consultation_allowed"]:
        info("Manual consultation remains explicit and user-mediated. It does not turn Core into hidden agent orchestration.")
    elif profile["tier"] in {"agents", "studio"}:
        info("Specialist/runtime details will be finalized in `cc setup --engagement`.")
        if profile["tier"] == "agents":
            info("Current release path: keep helper roles explicit on the chosen host. Think prompt, folder, and behavior contract before any API routing.")
        else:
            info("Studio/UI is a later optional extra and is not part of the current public release path.")
    return profile


def _ask_pre_kickoff_planning(default_tier: str = "core",
                              default_manual_consultation: bool = False,
                              apply_answers: bool = False) -> dict:
    print("  Choose how the first design and implementation plan should be produced.")
    print("  [1] Core - direct planning in the main chat")
    print("      Strong single-chat software-engineering kickoff. No external consultation by default.")
    print("  [2] Core - direct planning + manual consultation if needed")
    print("      Main chat leads. A bounded external second opinion may be prepared explicitly if needed.")
    print("  [3] Agents - specialist-assisted planning")
    print("      Kickoff planning may assume explicit helper roles on the chosen host with defined prompt/folder/behavior contracts.")
    print("  [4] Studio - specialist-assisted planning + full CC UX")
    print("      Future/internal path only. Do not treat UI as part of the current public release.")
    mapping = {
        "1": ("core", False),
        "2": ("core", True),
        "3": ("agents", False),
        "4": ("studio", False),
    }
    if default_tier == "core" and default_manual_consultation:
        default_choice = "2"
    else:
        default_choice = {"core": "1", "agents": "3", "studio": "4"}.get(default_tier, "1")
    choice = _ask_or_default("Choice", default_choice, apply_answers=apply_answers)
    selected_tier, manual_consultation_allowed = mapping.get(choice, mapping[default_choice])
    profile = _planning_profile_for_tier(selected_tier, manual_consultation_allowed)
    info(
        "Kickoff planning profile: "
        + _planning_summary_line(profile)
    )
    if profile["manual_consultation_allowed"]:
        info("Manual consultation remains user-mediated and explicit. It does not turn Core into hidden agent orchestration.")
    return profile


def _detect_backends() -> dict:
    """Detect available consultant backends."""
    backends = {}
    # claude CLI
    if shutil.which("claude"):
        backends["claude"] = "claude CLI found in PATH"
    # ollama
    if shutil.which("ollama"):
        backends["ollama"] = "ollama found in PATH"
    # API keys
    if os.environ.get("ANTHROPIC_API_KEY"):
        backends["anthropic"] = "ANTHROPIC_API_KEY set"
    if os.environ.get("OPENAI_API_KEY"):
        backends["openai"] = "OPENAI_API_KEY set"
    return backends


def _scan_dirs(project: Path) -> list[str]:
    """List boundary candidates, preferring real source modules over container dirs.

    If the project has common source roots such as ``src/`` with visible child
    modules, return the child paths (for example ``src/game``) instead of the
    coarse container directory.
    """
    skip = {
        ".git", ".controlcoding", ".claude", ".vscode", ".idea", "node_modules", "__pycache__",
        ".pytest_cache", "venv", ".venv", "env", ".env", "dist", "build",
    }
    source_roots = {"src", "app", "lib"}
    dirs: list[str] = []
    for entry in sorted(project.iterdir()):
        if not entry.is_dir() or entry.name in skip or entry.name.startswith("."):
            continue

        if entry.name in source_roots:
            children = [
                child for child in sorted(entry.iterdir())
                if child.is_dir() and child.name not in skip and not child.name.startswith(".")
            ]
            if children:
                dirs.extend(f"{entry.name}/{child.name}" for child in children)
                continue

        dirs.append(entry.name)
    return dirs


def _collect_module_boundaries(project: Path,
                               prefill: dict | None = None,
                               existing: dict | None = None,
                               apply_answers: bool = False) -> tuple[list[str], list[str], list[str]]:
    """Collect stable/shared/feature boundaries from the current project tree."""
    defaults = prefill if isinstance(prefill, dict) else {}
    existing_boundaries = existing if isinstance(existing, dict) else {}
    dirs = _scan_dirs(project)
    if not dirs:
        print(f"  No directories found. You can configure boundaries later in {CANONICAL_CONTEXT_FILENAME}.")
        return [], [], []

    print(f"  Found directories: {', '.join(dirs)}\n")
    stable_defaults = defaults.get("stable", existing_boundaries.get("stable", []))
    shared_defaults = defaults.get("shared", existing_boundaries.get("shared", []))
    feature_defaults = defaults.get("features", existing_boundaries.get("features", []))
    if not isinstance(stable_defaults, list):
        stable_defaults = []
    if not isinstance(shared_defaults, list):
        shared_defaults = []
    if not isinstance(feature_defaults, list):
        feature_defaults = []

    if apply_answers:
        stable = [d for d in dirs if d in stable_defaults]
        shared = [d for d in dirs if d in shared_defaults]
        features = [d for d in dirs if d in feature_defaults]
        return stable, shared, features

    print("  Classify each directory (s=stable, h=shared, f=feature, enter=skip):")
    stable: list[str] = []
    shared: list[str] = []
    features: list[str] = []
    for d in dirs:
        choice = input(f"    {d}/ [s/h/f/skip]: ").strip().lower()
        if choice == "s":
            stable.append(d)
        elif choice == "h":
            shared.append(d)
        elif choice == "f":
            features.append(d)
    return stable, shared, features


def _normalize_module_boundaries(boundaries: dict | None) -> dict:
    if not isinstance(boundaries, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for zone_name in ("stable", "shared", "features"):
        values = boundaries.get(zone_name, [])
        if not isinstance(values, list):
            continue
        cleaned = [str(value).strip().replace("\\", "/") for value in values if str(value).strip()]
        if cleaned:
            normalized[zone_name] = cleaned
    return normalized


def _merge_protected_zones(existing: list | dict | None,
                           derived: list[dict]) -> list[dict]:
    """Merge derived protected zones into existing config without duplication."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()

    raw_existing = existing if isinstance(existing, list) else []
    for entry in raw_existing:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", "")).replace("\\", "/").strip()
        if not path:
            continue
        level = str(entry.get("level", "warn")).strip().lower() or "warn"
        key = (path, level)
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)

    for entry in derived:
        path = str(entry.get("path", "")).replace("\\", "/").strip()
        level = str(entry.get("level", "warn")).strip().lower() or "warn"
        key = (path, level)
        if not path or key in seen:
            continue
        seen.add(key)
        merged.append(entry)

    return merged


def _collect_protected_zones(stable_dirs: list[str],
                             shared_dirs: list[str],
                             host_profile: dict | None = None,
                             apply_answers: bool = False) -> list[dict]:
    """Collect protected-zone defaults from the classified module boundaries."""
    host = host_profile if isinstance(host_profile, dict) else {}
    inline_supported = str(host.get("inlineBoundaryGate", "none")) == "native_hooks"
    derived: list[dict] = []

    if stable_dirs or shared_dirs:
        print(f"\n--- Protected Zones ---\n")

    if stable_dirs:
        print("  Stable directories can be protected with DENY (block AI edits).")
        for d in stable_dirs:
            if _ask_yn_or_default(f"Protect {d}/ with DENY?", default=False, apply_answers=apply_answers):
                derived.append({"path": f"{d}/", "description": f"{d} - stable zone", "level": "deny"})
            else:
                derived.append({"path": f"{d}/", "description": f"{d} - stable zone", "level": "warn"})

    if shared_dirs:
        print("  Shared directories should usually at least warn on edits so important structure changes stay visible.")
        shared_default = (not inline_supported) or not stable_dirs
        for d in shared_dirs:
            if _ask_yn_or_default(f"Protect {d}/ with WARN?", default=shared_default, apply_answers=apply_answers):
                derived.append({"path": f"{d}/", "description": f"{d} - shared zone", "level": "warn"})

    return derived


def _load_json_object(path: Path) -> dict:
    """Load a JSON object from disk or return an empty dict."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_answers_payload(path: Path | None, section: str, strict_section: bool = False) -> dict:
    """Load a prefilled answers payload for setup/engagement handoff."""
    if path is None:
        return {}
    payload = _load_json_object(path)
    if not payload:
        warn(f"Answers file is missing, invalid, or empty: {path}")
        return {}
    scoped = payload.get(section)
    if isinstance(scoped, dict):
        return scoped
    if strict_section and ANSWERS_FILE_SECTIONS.intersection(payload):
        warn(f"Answers file does not contain a valid `{section}` payload: {path}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_cc_config(existing_config: dict,
                     protected_zones: list,
                     documentation_mode: str,
                     hooks_location: str,
                     cc_artifact_mode: str,
                     memory_default_policy: str = MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE,
                     project_definition_mode: str | None = None,
                     module_boundaries: dict | None = None,
                     planning: dict | None = None,
                     product: dict | None = None,
                     environment: dict | None = None) -> dict:
    """Build cc_config.json, preserving unrelated existing keys."""
    config = dict(existing_config)
    config["documentation_mode"] = documentation_mode
    config["cc_artifact_mode"] = cc_artifact_mode
    config["protected_zones"] = protected_zones
    config["hooks_location"] = hooks_location
    config["memory_default_policy"] = _normalize_memory_default_policy(memory_default_policy)
    if project_definition_mode in {"skip", "existing_brief", "guided", "existing_project"}:
        config["project_definition_mode"] = project_definition_mode
    normalized_boundaries = _normalize_module_boundaries(module_boundaries)
    if normalized_boundaries:
        config["module_boundaries"] = normalized_boundaries
    if isinstance(planning, dict) and planning:
        config["planning"] = dict(planning)
    if isinstance(product, dict) and product:
        config["product"] = dict(product)
    if isinstance(environment, dict) and environment:
        config["environment"] = dict(environment)
    return config


def _setup_phase(phase_id: str,
                 label: str,
                 status: str,
                 evidence: list[str] | None = None,
                 required_fields: list[str] | None = None) -> dict:
    normalized_status = status if status in {"completed", "open", "skipped"} else "open"
    return {
        "id": phase_id,
        "label": label,
        "status": normalized_status,
        "required_fields": list(required_fields or []),
        "evidence": [str(item).strip() for item in (evidence or []) if str(item).strip()],
    }


def _recommendation_card(field: str,
                         recommended_value: str,
                         reason: str,
                         alternative_value: str,
                         alternative_when: str,
                         user_prompt: str,
                         explanation_level: str = "short",
                         recommendation_class: str = "governance") -> dict:
    """Build the minimum recommendation-card contract used by setup surfaces."""
    level = explanation_level if explanation_level in {"short", "medium", "long"} else "short"
    card_class = recommendation_class if recommendation_class else "governance"
    return {
        "field": str(field),
        "recommended_value": str(recommended_value),
        "reason": str(reason),
        "alternative_value": str(alternative_value),
        "alternative_when": str(alternative_when),
        "user_prompt": str(user_prompt),
        "explanation_level": level,
        "recommendation_class": str(card_class),
    }


def _render_recommendation_card_example(title: str, card: dict) -> str:
    """Render one compact example for chat-guide prompts and snapshot evidence."""
    return (
        f"{title}\n"
        f"Decision: {card['field']}\n"
        f"Recommended: `{card['recommended_value']}` because {card['reason']}.\n"
        f"Alternative: `{card['alternative_value']}` - {card['alternative_when']}.\n"
        f"Question: {card['user_prompt']}"
    )


def _setup_project_recommendation_examples() -> list[dict]:
    """Canonical examples for Core greenfield and Core brownfield setup."""
    greenfield_card = _recommendation_card(
        "project_definition_mode",
        "guided",
        "no authoritative brief is confirmed, so the safest Core path is to collect the minimum framing before generating kickoff docs",
        "existing_brief",
        "use it if there is a real brief, README, requirements file, or design document to treat as authoritative",
        "Should we define this as a new Core project now, or use an existing source file as the brief?",
        recommendation_class="strategy",
    )
    brownfield_card = _recommendation_card(
        "project_definition_mode",
        "existing_project",
        "the repository already appears to contain source, tests, docs, or manifests, so setup should preserve existing truth before generating new plans",
        "existing_brief",
        "use it if there is one authoritative brief and you do not need a repo inventory or adoption pass",
        "Should this be treated as a brownfield adoption pass for an existing project?",
        recommendation_class="strategy",
    )
    return [
        {
            "scenario": "core_greenfield",
            "card": greenfield_card,
            "rendered": _render_recommendation_card_example(
                "Core greenfield example",
                greenfield_card,
            ),
        },
        {
            "scenario": "core_brownfield",
            "card": brownfield_card,
            "rendered": _render_recommendation_card_example(
                "Core brownfield example",
                brownfield_card,
            ),
        },
    ]


def _setup_recommendation_profile(answers: dict) -> str:
    planning = answers.get("planning", {})
    if not isinstance(planning, dict):
        planning = {}
    tier = str(planning.get("tier", "core")).strip().lower() or "core"
    definition_mode = str(answers.get("project_definition_mode", "guided")).strip().lower()
    if definition_mode == "existing_project":
        return "core_brownfield" if tier == "core" else f"{tier}_brownfield"
    if tier == "core":
        return "core_greenfield"
    return f"{tier}_project_setup"


def _source_mode_reason(project_definition_mode: str, brief_sources: list[str]) -> str:
    if project_definition_mode == "existing_project":
        return "the repository appears mature enough that adoption should preserve existing truth before generating new plans"
    if project_definition_mode == "existing_brief":
        if brief_sources:
            return "existing brief or spec candidates were found and should anchor the kickoff package"
        return "the user supplied a reference, so setup should ground the package in that source instead of inventing context"
    if project_definition_mode == "guided":
        return "no authoritative brief is confirmed, so guided framing is safer than pretending the design is already known"
    return "project setup can be deferred without changing the installed ControlCoding baseline"


def _source_mode_alternative(project_definition_mode: str) -> tuple[str, str]:
    if project_definition_mode == "existing_project":
        return (
            "existing_brief",
            "use it if there is one authoritative brief and you do not need a brownfield inventory or adoption pass",
        )
    if project_definition_mode == "existing_brief":
        return (
            "existing_project",
            "use it if the repository already has implemented scope, tests, source trees, and multiple source-of-truth documents",
        )
    if project_definition_mode == "guided":
        return (
            "existing_brief",
            "use it if there is a real brief, README, requirements file, or design document to treat as authoritative",
        )
    return (
        "guided",
        "use it if you want ControlCoding to build the first design and implementation baseline now",
    )


def _build_setup_project_recommendation_cards(answers: dict,
                                              brief_sources: list[str]) -> list[dict]:
    """Create deterministic recommendation cards from the accepted setup intent."""
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    product = answers.get("product", {})
    if not isinstance(product, dict):
        product = {}
    environment = answers.get("environment", {})
    if not isinstance(environment, dict):
        environment = {}
    planning = answers.get("planning", {})
    if not isinstance(planning, dict):
        planning = {}

    project_definition_mode = str(answers.get("project_definition_mode", "guided"))
    alt_mode, alt_mode_when = _source_mode_alternative(project_definition_mode)
    references = str(kickoff.get("references", "")).strip()
    first_brief = brief_sources[0] if brief_sources else "ask_user"
    selected_reference = references or first_brief
    kickoff_mode = str(answers.get("kickoff_mode", kickoff.get("mode", "idea")))
    product_form = str(product.get("product_form", "other"))
    planning_mode = str(planning.get("planning_mode", "solo_structured"))
    stack = str(answers.get("stack", "")).strip()
    architecture = str(answers.get("arch", "")).strip()
    truth = str(answers.get("truth", "")).strip()
    view = str(answers.get("view", "")).strip()

    cards = [
        _recommendation_card(
            "project_definition_mode",
            project_definition_mode,
            _source_mode_reason(project_definition_mode, brief_sources),
            alt_mode,
            alt_mode_when,
            "Which source mode should the project setup use?",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "selected_reference",
            selected_reference,
            "the kickoff package should be grounded in the most authoritative visible source before filling gaps",
            "guided_questions",
            "use this if no file should be treated as authoritative yet",
            "Which brief, README, requirements file, or design document should be treated as the source?",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "kickoff_mode",
            kickoff_mode,
            "the chosen readiness level controls how much setup should infer versus preserve from existing source material",
            "partial_spec" if kickoff_mode != "partial_spec" else "existing_design",
            "use the alternative if the source is less or more mature than the current answer implies",
            "How prepared is the source right now?",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "product.product_form",
            product_form,
            "product form determines the question branches, generated design defaults, and likely verification shape",
            "other",
            "use it when the project does not fit desktop, web, mobile, service, library, or plugin categories",
            "Which product form should drive the kickoff package?",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "technicalDirection.stack",
            stack or "defer_until_constraints_are_clearer",
            "stack direction should follow product form, target platform, tooling tolerance, and speed/control tradeoff",
            "provisional_stack",
            "use a provisional stack when coding can start with explicit uncertainty and later replacement cost is acceptable",
            "Which implementation stack direction should be recorded now?",
            explanation_level="medium",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "technicalDirection.architecture",
            architecture or "defer_until_boundaries_are_clearer",
            "architecture direction should not be locked until source truth, runtime shape, and module boundaries are clear enough",
            "thin_vertical_slice_first",
            "use it when the safest next step is to prove one end-to-end path before freezing architecture",
            "Which architecture direction should be recorded now?",
            explanation_level="medium",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "technicalDirection.truthViewSplit",
            f"truth={truth or 'unresolved'}; view={view or 'unresolved'}",
            "truth/view separation prevents generated docs from mixing authoritative state with presentation or adapter concerns",
            "leave_unresolved",
            "use it when the project is too early and the split would be fake precision",
            "Is there a real truth/view split to record now?",
            recommendation_class="strategy",
        ),
        _recommendation_card(
            "governance.planning",
            planning_mode,
            "the current release keeps Core direct, allows manual consultation inside Core, and defers API-routed specialists to Version II",
            "solo_structured",
            "use it when no manual consultation or specialist-assisted planning path is needed for this kickoff",
            "Which planning basis should this kickoff use?",
            recommendation_class="governance",
        ),
        _recommendation_card(
            "governance.documentationMode",
            str(answers.get("documentation_mode", "managed")),
            "managed mode lets ControlCoding keep local design, plan, criteria, and roadmap taxonomy aligned",
            "project_managed",
            "use it if the repo already has a mature documentation lifecycle that should not be reorganized",
            "Which documentation ownership mode should govern generated project docs?",
            recommendation_class="governance",
        ),
        _recommendation_card(
            "environment.install_policy",
            str(environment.get("install_permission", "denied")),
            "setup should not install missing tools unless the user explicitly grants permission",
            "granted",
            "use it only when missing dependency installation is acceptable for this project and machine",
            "May setup install missing dependencies if they become necessary?",
            recommendation_class="baseline_safety",
        ),
    ]

    return cards


def _build_setup_intent_snapshot(project: Path,
                                 answers: dict,
                                 brief_sources: list[str],
                                 protected_zone_updates: list[dict],
                                 synced_context_path: str | None,
                                 kickoff_written: list[str],
                                 fitness_label: str | None) -> dict:
    """Build the canonical project-setup intent snapshot for all setup surfaces."""
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    product = answers.get("product", {})
    if not isinstance(product, dict):
        product = {}
    environment = answers.get("environment", {})
    if not isinstance(environment, dict):
        environment = {}
    planning = answers.get("planning", {})
    if not isinstance(planning, dict):
        planning = {}

    module_boundaries = {
        "stable": list(answers.get("stable", []) or []),
        "shared": list(answers.get("shared", []) or []),
        "features": list(answers.get("features", []) or []),
    }
    references = str(kickoff.get("references", "")).strip()
    boundary_evidence = [
        f"{zone}: {', '.join(paths)}"
        for zone, paths in module_boundaries.items()
        if paths
    ]
    apply_evidence = [CANONICAL_CONTEXT_FILENAME]
    if synced_context_path:
        apply_evidence.append(synced_context_path)
    if kickoff_written:
        apply_evidence.append(f"{len(kickoff_written)} kickoff files")
    if fitness_label:
        apply_evidence.append(fitness_label)

    return {
        "schema": "controlcoding.setup_intent.v1",
        "flow": "project_setup",
        "projectRoot": str(project),
        "project": {
            "name": answers.get("name", project.name),
            "definitionMode": answers.get("project_definition_mode", "guided"),
            "kickoffMode": answers.get("kickoff_mode", kickoff.get("mode", "idea")),
        },
        "source": {
            "briefCandidates": list(brief_sources),
            "selectedReferences": references,
            "adoption": answers.get("adoption", {}) if isinstance(answers.get("adoption"), dict) else {},
        },
        "environment": dict(environment),
        "product": dict(product),
        "technicalDirection": {
            "stack": answers.get("stack", ""),
            "architecture": answers.get("arch", ""),
            "truthRepresentation": answers.get("truth", ""),
            "viewRepresentation": answers.get("view", ""),
        },
        "governance": {
            "userHost": answers.get("user_host", "other"),
            "documentationMode": answers.get("documentation_mode", "managed"),
            "ccArtifactMode": answers.get("cc_artifact_mode", "local_only"),
            "hooksLocation": answers.get("hooks_location", "local"),
            "planning": dict(planning),
        },
        "moduleBoundaries": module_boundaries,
        "protection": {
            "derivedProtectedZones": list(protected_zone_updates),
        },
        "apply": {
            "canonicalContext": CANONICAL_CONTEXT_FILENAME,
            "hostContext": synced_context_path or "",
            "kickoffFilesWritten": list(kickoff_written),
            "fitnessConfig": fitness_label or "",
        },
        "recommendationCards": _build_setup_project_recommendation_cards(
            answers,
            brief_sources,
        ),
        "recommendationGuidance": {
            "profile": _setup_recommendation_profile(answers),
            "examples": _setup_project_recommendation_examples(),
        },
        "phaseGraph": [
            _setup_phase(
                "workspace_and_brief_discovery",
                "Workspace and brief discovery",
                "completed",
                evidence=list(brief_sources)[:5] or [answers.get("project_definition_mode", "guided")],
                required_fields=["project.definitionMode"],
            ),
            _setup_phase(
                "environment_and_install_policy",
                "Environment discovery and install policy",
                "completed" if environment else "open",
                evidence=[
                    f"inspection={environment.get('inspection_permission', 'unknown')}",
                    f"install={environment.get('install_permission', 'unknown')}",
                ],
                required_fields=["environment.inspection_permission", "environment.install_permission"],
            ),
            _setup_phase(
                "product_framing",
                "Product framing",
                "completed" if product.get("product_form") else "open",
                evidence=[
                    f"form={product.get('product_form', 'unknown')}",
                    f"runtime={product.get('runtime_constraints', '')}",
                ],
                required_fields=["product.product_form"],
            ),
            _setup_phase(
                "technical_strategy",
                "Technical strategy and architecture direction",
                "completed" if (answers.get("stack") or answers.get("arch")) else "open",
                evidence=[answers.get("stack", ""), answers.get("arch", "")],
                required_fields=["technicalDirection.stack", "technicalDirection.architecture"],
            ),
            _setup_phase(
                "controlcoding_governance",
                "ControlCoding governance choices",
                "completed" if planning else "open",
                evidence=[
                    str(answers.get("user_host", "other")),
                    str(planning.get("planning_mode", "")),
                ],
                required_fields=["governance.userHost", "governance.planning"],
            ),
            _setup_phase(
                "boundaries_and_protection",
                "Module boundaries and protection bootstrap",
                "completed" if boundary_evidence or protected_zone_updates else "open",
                evidence=boundary_evidence,
                required_fields=["moduleBoundaries"],
            ),
            _setup_phase(
                "apply_and_validation",
                "Apply and validation",
                "completed",
                evidence=apply_evidence,
                required_fields=["apply.canonicalContext"],
            ),
        ],
    }


def _write_setup_intent_snapshot(project: Path,
                                 answers: dict,
                                 brief_sources: list[str],
                                 protected_zone_updates: list[dict],
                                 synced_context_path: str | None,
                                 kickoff_written: list[str],
                                 fitness_label: str | None) -> str:
    payload = _build_setup_intent_snapshot(
        project,
        answers,
        brief_sources,
        protected_zone_updates,
        synced_context_path,
        kickoff_written,
        fitness_label,
    )
    path = _control_plane_path(project, SETUP_INTENT_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _control_plane_display_path(SETUP_INTENT_FILENAME)


def _build_gateway_config(existing_config: dict, user_host: str) -> dict:
    """Build gateway_config.json, preserving unrelated existing keys."""
    config = dict(existing_config)
    primary_host = user_host if user_host in _GATEWAY_VALID_USER_HOSTS else "other"
    config["userHost"] = primary_host
    config["enabledHosts"] = _normalize_enabled_hosts(
        existing_config.get("enabledHosts", []),
        primary_host,
    )
    config["hostProfile"] = _derive_host_profile(primary_host)
    return config


def _detect_existing_brief_sources(project: Path) -> list[str]:
    exact_priority = [
        "project_brief.md",
        "project-brief.md",
        "brief.md",
        "requirements.md",
        "design_brief.md",
        "prompt_framework.md",
        "README.md",
        "docs/README.md",
        "docs/brief.md",
        "docs/requirements.md",
        "docs/design.md",
    ]
    ignored_names = {
        "CONTROLCODING.md",
        "AGENTS.md",
        "CLAUDE.md",
        "ROADMAP.md",
        "BUGS.md",
        "STATUS.md",
    }
    keyword_tokens = ("brief", "requirement", "requirements", "spec", "design", "proposal")

    def rel_label(path: Path) -> str:
        return str(path.relative_to(project)).replace("\\", "/")

    found: list[str] = []
    seen: set[str] = set()

    def record(path: Path):
        label = rel_label(path)
        if label in seen:
            return
        seen.add(label)
        found.append(label)

    for relative in exact_priority:
        candidate = project / relative
        if candidate.exists() and candidate.is_file():
            record(candidate)

    candidate_files: list[Path] = []
    for pattern in ("*.md", "*.txt", "*.rst", "docs/*.md", "docs/*.txt", "docs/*.rst", "docs/*/*.md"):
        candidate_files.extend(project.glob(pattern))

    scored: list[tuple[int, str, Path]] = []
    for path in candidate_files:
        if not path.is_file() or path.name in ignored_names:
            continue
        name_lower = path.name.lower()
        if path.name == "README.md" or path.parent.name == "docs" and path.name == "README.md":
            score = 1
        else:
            matches = sum(1 for token in keyword_tokens if token in name_lower)
            if matches == 0:
                continue
            score = 10 - min(matches, 5)
        scored.append((score, rel_label(path), path))

    for _, _, path in sorted(scored, key=lambda item: (item[0], item[1].count("/"), len(item[1]), item[1])):
        record(path)

    return found


def _load_existing_context_identity(project: Path) -> dict:
    source_path, source_text = _read_context_source(project)
    if source_path is None or not source_text:
        return {}

    patterns = {
        "name": r"^- \*\*Name\*\*: (.*)$",
        "stack": r"^- \*\*Stack\*\*: (.*)$",
        "arch": r"^- \*\*Architecture\*\*: (.*)$",
        "truth": r"^- \*\*Truth representation\*\*: (.*)$",
        "view": r"^- \*\*View representation\*\*: (.*)$",
    }
    identity: dict[str, str] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, source_text, flags=re.MULTILINE)
        if match:
            identity[key] = match.group(1).strip()
    return identity


def _update_existing_context_identity(project: Path, answers: dict) -> bool:
    source_path, source_text = _read_context_source(project)
    if source_path is None or not source_text:
        return False

    updated = source_text
    replacements = [
        (r"^# .*$", f"# {answers['name']}"),
        (r"^- \*\*Name\*\*: .*$", f"- **Name**: {answers['name']}"),
        (r"^- \*\*Stack\*\*: .*$", f"- **Stack**: {answers['stack']}"),
        (r"^- \*\*Architecture\*\*: .*$", f"- **Architecture**: {answers['arch']}"),
        (r"^- \*\*Truth representation\*\*: .*$", f"- **Truth representation**: {answers.get('truth', '(not specified)') or '(not specified)'}"),
        (r"^- \*\*View representation\*\*: .*$", f"- **View representation**: {answers.get('view', '(not specified)') or '(not specified)'}"),
    ]
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, updated, count=1, flags=re.MULTILINE)

    source_path.write_text(updated, encoding="utf-8")
    return True


def _normalize_host_instruction_mode(mode: str) -> str:
    return mode if mode in {"preset_only", "recommended", "custom"} else "recommended"


def _build_host_instructions_config(mode: str, custom_notes: list[str]) -> dict:
    normalized_mode = _normalize_host_instruction_mode(mode)
    cleaned_notes = [note.strip() for note in custom_notes if isinstance(note, str) and note.strip()]
    if normalized_mode != "custom":
        cleaned_notes = []
    return {
        "mode": normalized_mode,
        "customNotes": cleaned_notes[:12],
    }


def _render_documentation_policy(documentation_mode: str, cc_artifact_mode: str) -> str:
    """Render the CLAUDE.md documentation-governance block."""
    artifact_policy = (
        "- **CC artifact storage**: Local-only. CC working docs (`STATUS.md`, `ROADMAP.md`, `BUGS.md`, and `dev/` when CC-managed) are updated locally at milestones and stay gitignored by default. `devlog/` is always local session memory."
        if cc_artifact_mode == "local_only"
        else "- **CC artifact storage**: Shared-repo. The team intentionally allows governed CC working docs to be tracked in git. `devlog/` remains local session memory and is not part of the shared repo."
    )
    if documentation_mode == "project_managed":
        return (
            "- **Mode**: Project-managed\n"
            "- This project keeps its own documentation structure and naming conventions.\n"
            "- Do not reorganize docs, rename governed files, or enforce the ControlCoding taxonomy unless the user explicitly asks.\n"
            "- Respect the project's existing indexes, archives, lifecycle rules, and documentation ownership decisions.\n"
            f"{artifact_policy}"
        )
    return (
        "- **Mode**: Managed by ControlCoding\n"
        "- Governed documentation follows the ControlCoding taxonomy and filename standard.\n"
        "- Use category-local `INDEX.md` files plus `archive/` and `deprecated/` for lifecycle changes.\n"
        "- Preserve legacy filename traceability when renaming governed documents.\n"
        f"{artifact_policy}"
    )


def _ask_kickoff_mode(default: str = "idea", apply_answers: bool = False) -> str:
    """Ask whether setup should scaffold the initial engineering documents."""
    print("  How prepared is this project right now?")
    print("  [1] Just an idea")
    print("      Early concept. You do not have a usable spec yet.")
    print("  [2] Partial spec or brief")
    print("      You have a prompt, notes, or requirement file, but not a real design doc yet.")
    print("  [3] Existing design direction")
    print("      You already know the intended architecture and major technical direction.")
    print("  [4] Skip kickoff docs for now")
    mapping = {
        "1": "idea",
        "2": "partial_spec",
        "3": "existing_design",
        "4": "skip",
    }
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "idea")


def _ask_project_definition_mode(default: str = "skip", apply_answers: bool = False) -> str:
    """Ask whether ControlCoding should also help define the project now."""
    print("  Do you want ControlCoding to help define the project now?")
    print("  [1] Skip for now")
    print("      Install and configure ControlCoding only. Do not scaffold design/plan docs yet.")
    print("  [2] Use my existing brief or project document")
    print("      Read an existing brief/doc as source and scaffold the initial engineering package from it.")
    print("  [3] Guide me interactively")
    print("      Ask the necessary product questions and build the initial engineering package with me.")
    print("  [4] Adopt an existing project or mature repository")
    print("      Inventory the current repo, map document truth, assess maturity, and scaffold an adoption package.")
    mapping = {
        "1": "skip",
        "2": "existing_brief",
        "3": "guided",
        "4": "existing_project",
    }
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "skip")


def _infer_project_definition_mode(prefill: dict) -> str:
    explicit = str(prefill.get("project_definition_mode", "")).strip().lower()
    if explicit in {"skip", "existing_brief", "guided", "existing_project"}:
        return explicit
    kickoff_mode = str(prefill.get("kickoff_mode", "")).strip().lower()
    if kickoff_mode == "skip":
        return "skip"
    references = prefill.get("kickoff", {})
    if isinstance(references, dict) and str(references.get("references", "")).strip():
        return "existing_brief"
    if isinstance(prefill.get("adoption"), dict) and prefill.get("adoption"):
        return "existing_project"
    if kickoff_mode in {"idea", "partial_spec", "existing_design"}:
        return "guided"
    return "skip"


def _collect_existing_project_kickoff_answers(mode: str,
                                              defaults: dict | None = None,
                                              apply_answers: bool = False) -> dict:
    """Collect lightweight baseline context for brownfield/adoption mode."""
    if mode == "skip":
        return {"mode": "skip"}

    defaults = defaults or {}

    print("  Treat the existing repository as the primary source material.")
    print("  Capture only the minimum product/design truth that is not already obvious from the repo.")
    references = _normalize_uncertain_text(
        _ask_or_default(
            "Known authoritative docs or source files to anchor the adoption pass (optional)",
            str(defaults.get("references", "")),
            apply_answers=apply_answers,
        ),
        "",
    )
    return {
        "mode": mode,
        "vision": _normalize_uncertain_text(
            _ask_or_default(
                "Current product purpose or mission (optional if obvious from the repo)",
                str(defaults.get("vision", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "users": _normalize_uncertain_text(
            _ask_or_default(
                "Primary users and active workflows already implemented (optional)",
                str(defaults.get("users", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "must_haves": _normalize_uncertain_text(
            _ask_or_default(
                "Current must-keep capabilities or committed scope (optional)",
                str(defaults.get("must_haves", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "invariants": _normalize_uncertain_text(
            _ask_or_default(
                "Known correctness or safety constraints already in force (optional)",
                str(defaults.get("invariants", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "quality": _normalize_uncertain_text(
            _ask_or_default(
                "Quality or compliance expectations already promised (optional)",
                str(defaults.get("quality", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "anti_goals": _normalize_uncertain_text(
            _ask_or_default(
                "Known non-goals or forbidden scope (optional)",
                str(defaults.get("anti_goals", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "references": references,
    }


def _collect_existing_project_adoption_answers(defaults: dict | None = None,
                                               apply_answers: bool = False) -> dict:
    defaults = defaults or {}
    return {
        "authoritative_docs": _normalize_uncertain_text(
            _ask_or_default(
                "Which existing docs or files are most authoritative today? (optional)",
                str(defaults.get("authoritative_docs", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "implemented_scope": _normalize_uncertain_text(
            _ask_or_default(
                "Which capabilities are already implemented and relied on? (optional)",
                str(defaults.get("implemented_scope", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "stable_areas": _normalize_uncertain_text(
            _ask_or_default(
                "Which modules, files, or subsystems already feel stable? (optional)",
                str(defaults.get("stable_areas", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "known_drift": _normalize_uncertain_text(
            _ask_or_default(
                "Where do docs and implementation already drift or feel outdated? (optional)",
                str(defaults.get("known_drift", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "verification_signals": _normalize_uncertain_text(
            _ask_or_default(
                "What existing tests, checks, or review gates already exist? (optional)",
                str(defaults.get("verification_signals", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
    }


def _looks_like_existing_project(project: Path, brief_sources: list[str]) -> bool:
    candidate_dirs = [
        path for path in _scan_dirs(project)
        if path not in {"hooks", "tools", "devlog"}
    ]
    if candidate_dirs:
        return True
    interesting_files = (
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "CMakeLists.txt",
        "requirements.txt",
        "src",
        "tests",
    )
    if any((project / name).exists() for name in interesting_files):
        return True
    return len([item for item in brief_sources if not item.startswith("docs/")]) > 1


def _collect_kickoff_answers(mode: str,
                             answers: dict,
                             defaults: dict | None = None,
                             apply_answers: bool = False) -> dict:
    """Collect the minimum structured input for kickoff documents."""
    if mode == "skip":
        return {"mode": "skip"}

    defaults = defaults or {}

    print("  Capture the initial engineering intent. Short sentences are fine.")
    print("  Plain-language answers are acceptable here. Exact frameworks and libraries can be refined later.")
    return {
        "mode": mode,
        "vision": _normalize_uncertain_text(_ask_or_default("Project idea / vision", str(defaults.get("vision", "")), apply_answers=apply_answers), ""),
        "users": _normalize_uncertain_text(_ask_or_default("Primary user(s) and the main useful flow", str(defaults.get("users", "")), apply_answers=apply_answers), ""),
        "must_haves": _normalize_uncertain_text(_ask_or_default("Must-have v1 features", str(defaults.get("must_haves", "")), apply_answers=apply_answers), ""),
        "invariants": _normalize_uncertain_text(_ask_or_default("Correctness rules / must-never-happen states", str(defaults.get("invariants", "")), apply_answers=apply_answers), ""),
        "quality": _normalize_uncertain_text(_ask_or_default("Quality constraints (performance, security, etc.)", str(defaults.get("quality", "")), apply_answers=apply_answers), ""),
        "anti_goals": _normalize_uncertain_text(_ask_or_default("Out-of-scope / anti-goals (leave empty if none)", str(defaults.get("anti_goals", "")), apply_answers=apply_answers), ""),
        "references": _normalize_uncertain_text(_ask_or_default("Existing brief file, notes, or docs to carry forward (optional)", str(defaults.get("references", "")), apply_answers=apply_answers), ""),
    }


def _collect_existing_brief_kickoff_answers(mode: str,
                                            defaults: dict | None = None,
                                            apply_answers: bool = False) -> dict:
    """Collect a lighter kickoff package when the user already has a brief or doc."""
    if mode == "skip":
        return {"mode": "skip"}

    defaults = defaults or {}

    print("  Use your existing brief or project document as the source of truth for the initial package.")
    print("  You can leave the gap-filling fields empty if the brief already covers them.")
    references = _normalize_uncertain_text(
        _ask_or_default(
            "Existing brief file, notes, or docs to carry forward",
            str(defaults.get("references", "")),
            apply_answers=apply_answers,
        ),
        "",
    )
    return {
        "mode": mode,
        "vision": _normalize_uncertain_text(
            _ask_or_default(
                "Project idea / vision (optional if already covered by the brief)",
                str(defaults.get("vision", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "users": _normalize_uncertain_text(
            _ask_or_default(
                "Primary user(s) and the main useful flow (optional if already covered)",
                str(defaults.get("users", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "must_haves": _normalize_uncertain_text(
            _ask_or_default(
                "Must-have v1 features (optional if already covered)",
                str(defaults.get("must_haves", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "invariants": _normalize_uncertain_text(
            _ask_or_default(
                "Correctness rules / must-never-happen states (optional if already covered)",
                str(defaults.get("invariants", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "quality": _normalize_uncertain_text(
            _ask_or_default(
                "Quality constraints (optional if already covered)",
                str(defaults.get("quality", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "anti_goals": _normalize_uncertain_text(
            _ask_or_default(
                "Out-of-scope / anti-goals (optional if already covered)",
                str(defaults.get("anti_goals", "")),
                apply_answers=apply_answers,
            ),
            "",
        ),
        "references": references,
    }


def _kickoff_paths(project: Path, documentation_mode: str) -> dict:
    """Return the canonical kickoff document paths for the selected doc mode."""
    if documentation_mode == "managed":
        return {
            "design": project / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md",
            "plan": project / "dev" / "plans" / "01_DEV_InitialImplementationPlan_Plan.md",
            "design_index": project / "dev" / "design" / "INDEX.md",
            "plans_index": project / "dev" / "plans" / "INDEX.md",
            "design_label": "01_DSN_ProjectFoundation_InProgress.md",
            "plan_label": "01_DEV_InitialImplementationPlan_Plan.md",
        }
    return {
        "design": project / "DESIGN.md",
        "plan": project / "IMPLEMENTATION_PLAN.md",
        "design_index": None,
        "plans_index": None,
        "design_label": "DESIGN.md",
        "plan_label": "IMPLEMENTATION_PLAN.md",
    }


def _project_definition_paths(project: Path, documentation_mode: str) -> dict:
    if documentation_mode == "managed":
        root = project / "dev" / "project-definition"
        return {
            "index": root / "INDEX.md",
            "source_assessment": root / "01_DEF_SourceAssessment_InProgress.md",
            "consultation_plan": root / "02_DEF_ConsultationPlanning_InProgress.md",
            "inventory": root / "03_DEF_ExistingProjectInventory_InProgress.md",
            "truth_map": root / "04_DEF_DocumentTruthMap_InProgress.md",
            "architecture_extraction": root / "05_DEF_ArchitectureExtraction_InProgress.md",
            "maturity_gap": root / "06_DEF_MaturityAndGapAssessment_InProgress.md",
            "adoption_plan": root / "07_DEF_AdoptionPlan_InProgress.md",
            "manual_root": root / "manual-consultation",
            "manual_index": root / "manual-consultation" / "INDEX.md",
            "agent_root": root / "agent-specs",
            "agent_index": root / "agent-specs" / "INDEX.md",
            "index_label": "INDEX.md",
            "source_assessment_label": "01_DEF_SourceAssessment_InProgress.md",
            "consultation_plan_label": "02_DEF_ConsultationPlanning_InProgress.md",
            "inventory_label": "03_DEF_ExistingProjectInventory_InProgress.md",
            "truth_map_label": "04_DEF_DocumentTruthMap_InProgress.md",
            "architecture_extraction_label": "05_DEF_ArchitectureExtraction_InProgress.md",
            "maturity_gap_label": "06_DEF_MaturityAndGapAssessment_InProgress.md",
            "adoption_plan_label": "07_DEF_AdoptionPlan_InProgress.md",
            "manual_index_label": "manual-consultation/INDEX.md",
            "agent_index_label": "agent-specs/INDEX.md",
        }
    root = project / "project-definition"
    return {
        "index": root / "INDEX.md",
        "source_assessment": root / "01_DEF_SourceAssessment.md",
        "consultation_plan": root / "02_DEF_ConsultationPlanning.md",
        "inventory": root / "03_DEF_ExistingProjectInventory.md",
        "truth_map": root / "04_DEF_DocumentTruthMap.md",
        "architecture_extraction": root / "05_DEF_ArchitectureExtraction.md",
        "maturity_gap": root / "06_DEF_MaturityAndGapAssessment.md",
        "adoption_plan": root / "07_DEF_AdoptionPlan.md",
        "manual_root": root / "manual-consultation",
        "manual_index": root / "manual-consultation" / "INDEX.md",
        "agent_root": root / "agent-specs",
        "agent_index": root / "agent-specs" / "INDEX.md",
        "index_label": "INDEX.md",
        "source_assessment_label": "01_DEF_SourceAssessment.md",
        "consultation_plan_label": "02_DEF_ConsultationPlanning.md",
        "inventory_label": "03_DEF_ExistingProjectInventory.md",
        "truth_map_label": "04_DEF_DocumentTruthMap.md",
        "architecture_extraction_label": "05_DEF_ArchitectureExtraction.md",
        "maturity_gap_label": "06_DEF_MaturityAndGapAssessment.md",
        "adoption_plan_label": "07_DEF_AdoptionPlan.md",
        "manual_index_label": "manual-consultation/INDEX.md",
        "agent_index_label": "agent-specs/INDEX.md",
    }


def _criteria_paths(project: Path, documentation_mode: str) -> dict:
    """Return the canonical acceptance-criteria document paths."""
    if documentation_mode == "managed":
        return {
            "criteria": project / "dev" / "criteria" / "01_ACC_FirstSliceAcceptance_Checklist.md",
            "criteria_index": project / "dev" / "criteria" / "INDEX.md",
            "criteria_label": "01_ACC_FirstSliceAcceptance_Checklist.md",
        }
    return {
        "criteria": project / "ACCEPTANCE_CRITERIA.md",
        "criteria_index": None,
        "criteria_label": "ACCEPTANCE_CRITERIA.md",
    }


def _kickoff_mode_label(mode: str) -> str:
    labels = {
        "idea": "Just an idea",
        "partial_spec": "Partial spec",
        "existing_design": "Existing design direction",
        "skip": "Skip kickoff docs",
    }
    return labels.get(mode, mode)


def _project_definition_mode_label(mode: str) -> str:
    labels = {
        "skip": "Skip project definition for now",
        "existing_brief": "Use existing brief or project document",
        "guided": "Guide me interactively",
        "existing_project": "Adopt an existing project or mature repository",
    }
    return labels.get(mode, mode)


def _bulletize(text: str, fallback: str) -> str:
    """Render a free-text field as markdown bullets without over-parsing it."""
    items = _split_text_items(text)
    if not items:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in items[:12])


def _split_text_items(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    raw_parts: list[str] = []
    for line in text.replace("\r", "\n").split("\n"):
        raw_parts.extend(part.strip() for part in line.split(";"))
    return [part for part in raw_parts if part]


def _domain_focus_records(answers: dict) -> list[dict]:
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    corpus = " ".join(
        str(value)
        for value in (
            answers.get("name", ""),
            answers.get("stack", ""),
            answers.get("arch", ""),
            kickoff.get("vision", ""),
            kickoff.get("users", ""),
            kickoff.get("must_haves", ""),
            kickoff.get("invariants", ""),
            kickoff.get("quality", ""),
            kickoff.get("anti_goals", ""),
            kickoff.get("references", ""),
        )
    ).lower()
    rules = [
        {
            "id": "security_trust",
            "label": "Security, privacy, and trust",
            "tokens": ("security", "privacy", "auth", "authentication", "credential", "password", "vault", "secret", "token", "login", "compliance"),
        },
        {
            "id": "physics_simulation",
            "label": "Physics or simulation modeling",
            "tokens": ("physics", "simulation", "dynamics", "kinematic", "force", "trajectory", "mechanics", "particle", "motion"),
        },
        {
            "id": "mathematics_modeling",
            "label": "Mathematics or formal modeling",
            "tokens": ("mathematics", "mathematical", "algebra", "geometry", "calculus", "statistics", "optimization", "theorem", "proof"),
        },
        {
            "id": "engineering_systems",
            "label": "Engineering systems",
            "tokens": ("engineering", "embedded", "firmware", "sensor", "control system", "electrical", "mechanical", "cad"),
        },
        {
            "id": "data_information",
            "label": "Data, information, or knowledge systems",
            "tokens": ("data", "dataset", "etl", "pipeline", "analytics", "reporting", "knowledge", "search", "index", "metadata"),
        },
        {
            "id": "software_system_design",
            "label": "Software architecture and product workflows",
            "tokens": ("software", "desktop", "web", "mobile", "service", "api", "sdk", "plugin", "ui", "workflow", "application", "app"),
        },
    ]
    records: list[dict] = []
    for rule in rules:
        hits = [token for token in rule["tokens"] if token in corpus]
        if not hits:
            continue
        confidence = "high" if len(hits) >= 3 else "medium"
        records.append(
            {
                "id": rule["id"],
                "label": rule["label"],
                "confidence": confidence,
                "trigger_terms": hits[:5],
            }
        )
    if not records:
        records.append(
            {
                "id": "project_domain_generalist",
                "label": "General project domain framing",
                "confidence": "unresolved",
                "trigger_terms": [],
            }
        )
    return records


def _build_source_assessment(project: Path, answers: dict) -> dict:
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    project_definition_mode = str(answers.get("project_definition_mode", "guided") or "guided")
    brief_candidates = _detect_existing_brief_sources(project)
    references = str(kickoff.get("references", "") or "").strip()
    primary_reference = references or (brief_candidates[0] if brief_candidates else "(none)")
    reference_resolution = (
        "confirmed_reference"
        if references
        else "candidate_only"
        if brief_candidates
        else "missing_reference"
    )
    fields = [
        ("vision", "Vision"),
        ("users", "Primary users and main flow"),
        ("must_haves", "Must-have v1 features"),
        ("invariants", "Correctness rules and must-never-happen states"),
        ("quality", "Quality constraints"),
        ("anti_goals", "Out-of-scope items"),
    ]
    field_records: list[dict] = []
    missing_fields: list[str] = []
    for field_id, label in fields:
        value = str(kickoff.get(field_id, "") or "").strip()
        status = "provided" if value else "missing"
        field_records.append(
            {
                "id": field_id,
                "label": label,
                "status": status,
                "value_preview": " ".join(value.split())[:140] if value else "",
            }
        )
        if not value:
            missing_fields.append(label)

    provided_count = sum(1 for record in field_records if record["status"] == "provided")
    required_core_missing = [record["label"] for record in field_records if record["id"] in {"vision", "must_haves"} and record["status"] != "provided"]
    domain_focus = _domain_focus_records(answers)
    needs_consultation = bool(
        reference_resolution != "confirmed_reference"
        or len(required_core_missing) > 0
        or provided_count < 4
        or str(kickoff.get("mode", "idea")) == "idea"
    )
    if reference_resolution == "missing_reference" and provided_count < 3:
        approval_gate = "not_ready_for_design"
        recommended_next_step = "Collect or confirm a real brief before treating the kickoff package as design-ready."
    elif needs_consultation:
        approval_gate = "consultation_recommended"
        recommended_next_step = "Use source assessment and consultation packets to formalize the design baseline before implementation."
    else:
        approval_gate = "ready_for_design_baseline"
        recommended_next_step = "Proceed to design-baseline generation while keeping any listed gaps explicit."

    return {
        "project_definition_mode": project_definition_mode,
        "source_readiness": _kickoff_mode_label(str(kickoff.get("mode", "idea"))),
        "primary_reference": primary_reference,
        "reference_resolution": reference_resolution,
        "brief_candidates": brief_candidates,
        "field_records": field_records,
        "provided_field_count": provided_count,
        "missing_fields": missing_fields,
        "required_core_missing": required_core_missing,
        "domain_focus": domain_focus,
        "approval_gate": approval_gate,
        "recommended_next_step": recommended_next_step,
        "needs_consultation": needs_consultation,
        "source_record_count": len(_kickoff_source_records(answers)),
    }


def _existing_project_doc_candidates(project: Path, brief_candidates: list[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in brief_candidates:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    root_markdown = sorted(
        entry.name
        for entry in project.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower() in {".md", ".rst", ".txt"}
    )
    for candidate in root_markdown:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    docs_root = project / "docs"
    if docs_root.exists():
        for entry in sorted(docs_root.rglob("*.md"))[:12]:
            rel = str(entry.relative_to(project)).replace("\\", "/")
            if rel not in seen:
                seen.add(rel)
                candidates.append(rel)

    return candidates[:18]


def _build_existing_project_inventory(project: Path, answers: dict) -> dict:
    brief_candidates = _detect_existing_brief_sources(project)
    visible_dirs = _scan_dirs(project)
    visible_files = [
        entry.name
        for entry in sorted(project.iterdir())
        if entry.is_file() and not entry.name.startswith(".")
    ]
    tooling_files = [
        name for name in (
            "pyproject.toml",
            "requirements.txt",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "CMakeLists.txt",
            "pom.xml",
            "build.gradle",
            "Makefile",
        )
        if (project / name).exists()
    ]
    verification_assets = [
        label for label, exists in (
            ("tests/", (project / "tests").exists()),
            ("docs/", (project / "docs").exists()),
            (".github/workflows/", (project / ".github" / "workflows").exists()),
            ("pytest.ini", (project / "pytest.ini").exists()),
            ("tox.ini", (project / "tox.ini").exists()),
            ("noxfile.py", (project / "noxfile.py").exists()),
            ("CMakeLists.txt", (project / "CMakeLists.txt").exists()),
        )
        if exists
    ]
    adoption = answers.get("adoption", {})
    if not isinstance(adoption, dict):
        adoption = {}
    candidate_docs = _existing_project_doc_candidates(project, brief_candidates)
    return {
        "candidate_dirs": visible_dirs,
        "top_level_files": visible_files[:18],
        "tooling_files": tooling_files,
        "verification_assets": verification_assets,
        "document_candidates": candidate_docs,
        "brief_candidates": brief_candidates,
        "authoritative_hints": _split_text_items(str(adoption.get("authoritative_docs", ""))),
        "implemented_scope_hints": _split_text_items(str(adoption.get("implemented_scope", ""))),
        "stable_area_hints": _split_text_items(str(adoption.get("stable_areas", ""))),
        "known_drift_hints": _split_text_items(str(adoption.get("known_drift", ""))),
        "verification_signal_hints": _split_text_items(str(adoption.get("verification_signals", ""))),
        "boundary_seed_count": sum(len(answers.get(zone, [])) for zone in ("stable", "shared", "features")),
    }


def _build_document_truth_map(answers: dict, inventory: dict) -> dict:
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    adoption = answers.get("adoption", {})
    if not isinstance(adoption, dict):
        adoption = {}

    explicit_authority = _split_text_items(str(adoption.get("authoritative_docs", "")))
    kickoff_reference = str(kickoff.get("references", "") or "").strip()
    if kickoff_reference:
        explicit_authority.append(kickoff_reference)
    explicit_authority = [item.replace("\\", "/") for item in explicit_authority if item]
    explicit_set = {item.lower(): item for item in explicit_authority}

    records: list[dict] = []
    for candidate in inventory.get("document_candidates", []):
        normalized = str(candidate).replace("\\", "/")
        lower = normalized.lower()
        if lower in explicit_set:
            authority_state = "explicit_authority"
            suggested_role = "authoritative_source"
            notes = "Explicitly named as current project truth."
        elif normalized in inventory.get("brief_candidates", []):
            authority_state = "candidate"
            suggested_role = "brief_or_scope_candidate"
            notes = "Likely source document, but not yet confirmed as authoritative."
        elif normalized.lower().endswith("readme.md"):
            authority_state = "candidate"
            suggested_role = "project_overview_candidate"
            notes = "Useful overview candidate; verify whether it matches current product truth."
        else:
            authority_state = "candidate"
            suggested_role = "supporting_context"
            notes = "Useful context, but not automatically authoritative."
        records.append(
            {
                "path": normalized,
                "authority_state": authority_state,
                "suggested_role": suggested_role,
                "notes": notes,
            }
        )

    unresolved: list[str] = []
    if not records:
        unresolved.append("No candidate project documents were found locally.")
    if not explicit_authority:
        unresolved.append("No authoritative document or file was explicitly confirmed.")

    conflict_notes: list[str] = []
    if len(explicit_authority) > 1:
        conflict_notes.append("Multiple authoritative sources were named. Reconcile precedence before freezing project truth.")
    conflict_notes.extend(inventory.get("known_drift_hints", [])[:6])

    recommended_action = (
        "Freeze one authoritative source set, mark supporting docs explicitly, and flag stale documents before new implementation work."
        if unresolved or conflict_notes
        else "Treat the explicit authority set as the baseline truth map and keep supporting docs secondary."
    )
    return {
        "records": records,
        "explicit_authority": explicit_authority,
        "unresolved": unresolved,
        "conflict_notes": conflict_notes,
        "recommended_action": recommended_action,
    }


def _build_existing_project_architecture_extraction(answers: dict,
                                                    inventory: dict,
                                                    subsystems: list[dict]) -> dict:
    truth = str(answers.get("truth", "") or "").strip()
    view = str(answers.get("view", "") or "").strip()
    subsystem_rows: list[dict] = []
    for subsystem in subsystems:
        subsystem_rows.append(
            {
                "path": subsystem["path"],
                "zone": subsystem["zone"],
                "zone_label": subsystem["zone_label"],
                "signal": "explicit_boundary_seed",
                "note": "Named during adoption setup as an initial ControlCoding boundary candidate.",
            }
        )

    if not subsystem_rows:
        for path in inventory.get("candidate_dirs", [])[:12]:
            subsystem_rows.append(
                {
                    "path": path,
                    "zone": "unclassified",
                    "zone_label": "Unclassified",
                    "signal": "repo_inventory_candidate",
                    "note": "Visible repo directory that likely deserves ownership mapping before protection is enabled.",
                }
            )

    protection_bootstrap: list[dict] = []
    for zone_name, level in (("stable", "warn"), ("shared", "warn")):
        for path in answers.get(zone_name, []):
            normalized_path = str(path).rstrip("/\\")
            protection_bootstrap.append(
                {
                    "path": f"{normalized_path}/",
                    "suggested_level": level,
                    "reason": f"Named as `{zone_name}` during brownfield adoption bootstrap.",
                }
            )

    open_questions: list[str] = []
    if not truth:
        open_questions.append("Define the authoritative state or source-of-truth boundary before trusting architectural ownership.")
    if not view:
        open_questions.append("Define the presentation/view boundary so adapters and UI do not absorb authoritative logic.")
    if not answers.get("stable") and not answers.get("shared") and not answers.get("features"):
        open_questions.append("No explicit module boundaries were confirmed yet. Classify the main repo areas before enabling stronger protection.")
    if not inventory.get("tooling_files"):
        open_questions.append("No obvious build or package manifest was detected. Confirm the real execution/build surface explicitly.")

    return {
        "truth": truth,
        "view": view,
        "subsystems": subsystem_rows,
        "protection_bootstrap": protection_bootstrap,
        "open_questions": open_questions,
        "implemented_scope_hints": inventory.get("implemented_scope_hints", []),
        "stable_area_hints": inventory.get("stable_area_hints", []),
    }


def _build_existing_project_maturity_assessment(source_assessment: dict,
                                                inventory: dict,
                                                truth_map: dict,
                                                architecture_snapshot: dict) -> dict:
    areas: list[dict] = []

    source_status = (
        "strong"
        if source_assessment.get("approval_gate") == "ready_for_design_baseline"
        else "partial"
        if source_assessment.get("provided_field_count", 0) >= 3
        else "weak"
    )
    areas.append(
        {
            "area": "Source package and intent",
            "status": source_status,
            "signals": [
                f"approval_gate={source_assessment.get('approval_gate', 'unknown')}",
                f"primary_reference={source_assessment.get('primary_reference', '(none)')}",
            ],
        }
    )

    docs_status = "strong" if truth_map.get("explicit_authority") else "partial" if truth_map.get("records") else "weak"
    areas.append(
        {
            "area": "Document truth map",
            "status": docs_status,
            "signals": [
                f"authoritative_docs={len(truth_map.get('explicit_authority', []))}",
                f"candidate_docs={len(truth_map.get('records', []))}",
            ],
        }
    )

    architecture_status = (
        "strong"
        if architecture_snapshot.get("truth") and architecture_snapshot.get("subsystems")
        else "partial"
        if architecture_snapshot.get("subsystems")
        else "weak"
    )
    areas.append(
        {
            "area": "Architecture ownership and boundaries",
            "status": architecture_status,
            "signals": [
                f"boundary_candidates={len(architecture_snapshot.get('subsystems', []))}",
                f"protection_bootstrap={len(architecture_snapshot.get('protection_bootstrap', []))}",
            ],
        }
    )

    verification_status = "strong" if inventory.get("verification_assets") else "weak"
    areas.append(
        {
            "area": "Verification and review signals",
            "status": verification_status,
            "signals": inventory.get("verification_assets", [])[:6] or ["No obvious tests or workflow gates detected."],
        }
    )

    gaps: list[str] = []
    if source_assessment.get("required_core_missing"):
        gaps.extend(f"Missing or weak source field: {item}" for item in source_assessment["required_core_missing"])
    if truth_map.get("unresolved"):
        gaps.extend(truth_map["unresolved"])
    if truth_map.get("conflict_notes"):
        gaps.extend(truth_map["conflict_notes"])
    gaps.extend(architecture_snapshot.get("open_questions", []))
    if not inventory.get("verification_assets"):
        gaps.append("No obvious test, CI, or review gate was detected in the repo inventory.")

    adoption_gate = "adoption_ready_with_gaps" if gaps else "adoption_ready"
    if source_status == "weak" or architecture_status == "weak":
        adoption_gate = "adoption_alignment_required"

    next_actions = [
        "Freeze document authority and stale-doc policy.",
        "Confirm subsystem boundaries and their ownership semantics.",
        "Bootstrap protected zones only on the most stable paths first.",
        "Align design, implementation plan, and verification artifacts to the current repo reality.",
    ]
    return {
        "areas": areas,
        "gaps": gaps,
        "adoption_gate": adoption_gate,
        "next_actions": next_actions,
    }


def _build_existing_project_adoption_plan(answers: dict,
                                          source_assessment: dict,
                                          truth_map: dict,
                                          architecture_snapshot: dict,
                                          maturity_assessment: dict) -> dict:
    consultation_needed = source_assessment.get("approval_gate") != "ready_for_design_baseline"
    steps = [
        {
            "id": "ADP-01",
            "title": "Freeze current authority and stale-doc policy",
            "goal": "Decide which docs or files are authoritative now and which are supporting or stale.",
            "outputs": [
                "Confirmed authority set",
                "Stale-doc list or explicit 'none' decision",
            ],
        },
        {
            "id": "ADP-02",
            "title": "Map implemented scope and subsystem ownership",
            "goal": "Tie existing workflows and code areas to named subsystem responsibilities before new work continues.",
            "outputs": [
                "Subsystem ownership notes",
                "Boundary seeds for stable/shared/feature areas",
            ],
        },
        {
            "id": "ADP-03",
            "title": "Assess maturity and verification reality",
            "goal": "Separate what is merely implemented from what is verified, stable, or safe to protect.",
            "outputs": [
                "Maturity and gap assessment",
                "Verification baseline updates",
            ],
        },
        {
            "id": "ADP-04",
            "title": "Bootstrap protection progressively",
            "goal": "Start with warn-level protection on the most stable paths, then tighten after explicit review.",
            "outputs": [
                "Initial protected-zone proposal",
                "Change-control candidate list",
            ],
        },
        {
            "id": "ADP-05",
            "title": "Regenerate living design and implementation baselines",
            "goal": "Make the generated design, implementation, and verification docs describe the existing repo honestly.",
            "outputs": [
                "Updated design baseline",
                "Implementation planning aligned to current repo state",
            ],
        },
    ]
    if consultation_needed:
        steps.insert(
            1,
            {
                "id": "ADP-01B",
                "title": "Run bounded consultation before freezing the baseline",
                "goal": "Use manual packets or specialist roles to close the source or architecture gaps before trusting the design package.",
                "outputs": [
                    "Consultation notes merged into project docs",
                    "Explicit accept/reject decisions for contested assumptions",
                ],
            },
        )

    return {
        "steps": steps,
        "adoption_gate": maturity_assessment.get("adoption_gate", "adoption_alignment_required"),
        "recommended_authority_action": truth_map.get("recommended_action", "(define authority action)"),
        "protection_bootstrap": architecture_snapshot.get("protection_bootstrap", []),
    }


def _build_consultation_roles(answers: dict, source_assessment: dict) -> list[dict]:
    planning = _planning_data_from_answers(answers)
    tier = str(planning.get("tier", "core"))
    manual_in_core = tier == "core" or bool(planning.get("manual_consultation_allowed"))
    roles: list[dict] = []

    def add_role(role_id: str,
                 title: str,
                 purpose: str,
                 when_to_use: str,
                 expected_outputs: list[str],
                 prompt_focus: list[str],
                 capability_profile: list[str]):
        if any(existing["id"] == role_id for existing in roles):
            return
        roles.append(
            {
                "id": role_id,
                "title": title,
                "purpose": purpose,
                "when_to_use": when_to_use,
                "expected_outputs": expected_outputs,
                "prompt_focus": prompt_focus,
                "capability_profile": capability_profile,
                "manual_enabled": manual_in_core,
                "agentic_enabled": planning.get("planning_mode") == "orchestrated_specialists",
            }
        )

    add_role(
        "software_architecture_specialist",
        "Software Architecture Specialist",
        "Turn the brief and early assumptions into subsystem boundaries, owned state, and dependency rules.",
        "Use when architecture direction, truth/view split, or module ownership still feels provisional.",
        [
            "Boundary proposal with owned state per subsystem",
            "Dependency direction and interface contract notes",
            "Change-control risks before coding starts",
        ],
        [
            "separate authoritative truth from presentation or adapters",
            "name subsystem contracts and dependency rules",
            "identify which parts should become stable first",
        ],
        [
            "Strong long-context reasoning",
            "Architecture decomposition and interface design",
            "Ability to preserve brief intent without silent scope deletion",
        ],
    )
    add_role(
        "verification_acceptance_specialist",
        "Verification And Acceptance Specialist",
        "Translate aggregate goals into atomic acceptance criteria, evidence expectations, and review obligations.",
        "Use when success criteria, evidence paths, or human review boundaries are still unclear.",
        [
            "Atomic acceptance criteria",
            "Verification paths and required evidence",
            "Separation between implemented, self-tested, evidence-produced, and human-verified",
        ],
        [
            "split broad goals into concrete pass/fail checks",
            "define commands, scenarios, evidence, and review notes",
        ],
        [
            "Acceptance engineering",
            "Verification planning",
            "Ability to distinguish machine-verifiable from human-reviewed claims",
        ],
    )
    add_role(
        "implementation_planning_specialist",
        "Implementation Planning Specialist",
        "Turn the approved design baseline into a staged implementation plan with stabilization and protection milestones.",
        "Use when build order, feature decomposition, or protection timing is still vague.",
        [
            "Master implementation plan",
            "Feature-by-feature implementation docs",
            "Progressive protection policy for files, modules, and interfaces",
        ],
        [
            "sequence work packages",
            "map features to modules and likely files",
            "define when artifacts become stable and protected",
        ],
        [
            "Implementation sequencing",
            "Change-control planning",
            "Ability to connect design maturity to ControlCoding protection levels",
        ],
    )

    domain_ids = {record["id"] for record in source_assessment.get("domain_focus", []) if isinstance(record, dict)}
    if "physics_simulation" in domain_ids:
        add_role(
            "physics_simulation_specialist",
            "Physics And Simulation Specialist",
            "Formalize physical assumptions, models, constraints, and observables before implementation hides them in code.",
            "Use when the project depends on simulation fidelity, dynamics, or physically meaningful outputs.",
            [
                "Model assumptions and simplifications",
                "Observable variables, units, and constraints",
                "Design sections required for simulation correctness",
            ],
            [
                "formalize physical concepts before coding",
                "state assumptions, units, limits, and failure modes",
                "identify what must be verified empirically or numerically",
            ],
            [
                "Scientific reasoning",
                "Simulation-model decomposition",
                "Ability to separate domain assumptions from implementation details",
            ],
        )
    if "mathematics_modeling" in domain_ids:
        add_role(
            "mathematics_modeling_specialist",
            "Mathematical Modeling Specialist",
            "Formalize the mathematical structures, constraints, and definitions that the software must preserve.",
            "Use when the project depends on formal derivations, transformations, optimization, or statistical interpretation.",
            [
                "Definitions, variables, and constraints",
                "Required proofs or justification notes",
                "Verification obligations for mathematically critical behavior",
            ],
            [
                "define variables and invariants explicitly",
                "surface assumptions and edge cases",
                "state what can be validated automatically versus reviewed manually",
            ],
            [
                "Formal reasoning",
                "Model definition and constraint analysis",
                "Ability to communicate rigorous outputs in engineering-friendly language",
            ],
        )
    if "engineering_systems" in domain_ids:
        add_role(
            "engineering_systems_specialist",
            "Engineering Systems Specialist",
            "Clarify domain constraints, interfaces, safety assumptions, and operational boundaries before coding begins.",
            "Use when the project touches hardware, embedded systems, sensors, controls, or multidisciplinary engineering logic.",
            [
                "System constraints and integration assumptions",
                "Interface and safety-critical considerations",
                "Implementation-stage risks and required design deep dives",
            ],
            [
                "separate domain constraints from software implementation choices",
                "surface integration and safety assumptions",
                "identify mandatory design chapters or subsystem docs",
            ],
            [
                "Systems engineering reasoning",
                "Interface and constraint modeling",
                "Ability to reduce ambiguous engineering ideas into implementable contracts",
            ],
        )
    if "security_trust" in domain_ids:
        add_role(
            "security_trust_specialist",
            "Security And Trust Specialist",
            "Clarify trust boundaries, sensitive assets, threat assumptions, and non-claims before the design overpromises safety.",
            "Use when the project stores secrets, credentials, identity data, or claims any security posture.",
            [
                "Trust-boundary notes",
                "Threat-model assumptions and explicit non-goals",
                "Verification and review requirements for sensitive flows",
            ],
            [
                "identify sensitive assets and trust boundaries",
                "separate security claims from engineering aspirations",
                "flag required human review or external audit needs",
            ],
            [
                "Security design review",
                "Threat modeling",
                "Ability to prevent false confidence and overclaiming",
            ],
        )
    if "data_information" in domain_ids:
        add_role(
            "data_information_specialist",
            "Data And Information Modeling Specialist",
            "Clarify data models, persistence boundaries, metadata responsibilities, and lifecycle rules.",
            "Use when the project relies on durable records, indexing, search, or structured information flows.",
            [
                "Data model and persistence responsibilities",
                "Indexing and metadata rules",
                "Verification obligations for consistency after save/load/update",
            ],
            [
                "define authoritative data versus derived views",
                "state persistence and indexing responsibilities",
                "flag consistency invariants and migration risks",
            ],
            [
                "Data modeling",
                "Persistence and consistency design",
                "Ability to connect information architecture to implementation boundaries",
            ],
        )

    if source_assessment.get("approval_gate") != "ready_for_design_baseline":
        add_role(
            "domain_design_specialist",
            "Domain And Design Baseline Specialist",
            "Turn a weak or ambiguous source package into a design-ready baseline without skipping unresolved issues.",
            "Use when the brief is incomplete, the domain is unclear, or the team needs help deciding which design chapters must exist before coding.",
            [
                "Refined scope and clarified domain assumptions",
                "Recommended design-document structure",
                "Explicit open questions and missing evidence",
            ],
            [
                "preserve all meaningful source statements",
                "recommend the minimum design package needed before coding",
                "do not silently delete scope or ambiguity",
            ],
            [
                "Domain framing",
                "Requirements preservation",
                "Ability to propose document structures for unfamiliar domains",
            ],
        )

    return roles


def _canonical_status_model() -> list[str]:
    return [
        "planned",
        "implemented",
        "self_tested",
        "evidence_produced",
        "human_verified",
        "stable",
        "blocked",
    ]


def _kickoff_source_records(answers: dict) -> list[dict]:
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    references = str(kickoff.get("references", "") or "").strip()
    source_kinds = (
        ("vision", "context"),
        ("users", "context"),
        ("must_haves", "requirement_candidate"),
        ("invariants", "requirement_candidate"),
        ("quality", "requirement_candidate"),
        ("anti_goals", "scope_constraint"),
    )
    records: list[dict] = []
    for source_type, role in source_kinds:
        for index, item in enumerate(_split_text_items(str(kickoff.get(source_type, "")))[:12], start=1):
            record_id = f"SRC-{source_type.upper()}-{index:02d}"
            refs = [f"kickoff.{source_type}"]
            if references:
                refs.append(references)
            records.append(
                {
                    "id": record_id,
                    "source_type": source_type,
                    "role": role,
                    "text": " ".join(item.split()),
                    "source_brief_refs": refs,
                }
            )
    return records


def _numbered_text(text: str, fallbacks: list[str]) -> str:
    items = _split_text_items(text)
    if not items:
        items = fallbacks
    return "\n".join(f"{idx}. {item}" for idx, item in enumerate(items[:12], start=1))


def _slugify(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or fallback


def _project_relative_label(project: Path, path: Path) -> str:
    return str(path.relative_to(project)).replace("\\", "/")


def _design_package_paths(project: Path, documentation_mode: str) -> dict:
    if documentation_mode == "managed":
        root = project / "dev" / "design"
        return {
            "index": root / "INDEX.md",
            "overview": root / "02_DSN_SystemOverview_InProgress.md",
            "architecture": root / "03_DSN_ArchitectureAndBoundaries_InProgress.md",
            "mechanics": root / "04_DSN_CoreMechanics_InProgress.md",
            "subsystems_index": root / "subsystems" / "INDEX.md",
            "index_label": "INDEX.md",
            "overview_label": "02_DSN_SystemOverview_InProgress.md",
            "architecture_label": "03_DSN_ArchitectureAndBoundaries_InProgress.md",
            "mechanics_label": "04_DSN_CoreMechanics_InProgress.md",
            "subsystems_index_label": "subsystems/INDEX.md",
        }
    root = project / "design"
    return {
        "index": root / "INDEX.md",
        "overview": root / "00_DSN_SystemOverview.md",
        "architecture": root / "01_DSN_Architecture.md",
        "mechanics": root / "02_DSN_CoreMechanics.md",
        "subsystems_index": root / "subsystems" / "INDEX.md",
        "index_label": "INDEX.md",
        "overview_label": "00_DSN_SystemOverview.md",
        "architecture_label": "01_DSN_Architecture.md",
        "mechanics_label": "02_DSN_CoreMechanics.md",
        "subsystems_index_label": "subsystems/INDEX.md",
    }


def _implementation_package_paths(project: Path, documentation_mode: str) -> dict:
    if documentation_mode == "managed":
        root = project / "dev" / "implementation"
        return {
            "index": root / "INDEX.md",
            "master": root / "00_IMP_MasterImplementationPlan_InProgress.md",
            "protection": root / "01_IMP_ProgressiveProtectionPlan_InProgress.md",
            "features_root": root / "features",
            "features_index": root / "features" / "INDEX.md",
            "index_label": "INDEX.md",
            "master_label": "00_IMP_MasterImplementationPlan_InProgress.md",
            "protection_label": "01_IMP_ProgressiveProtectionPlan_InProgress.md",
            "features_index_label": "features/INDEX.md",
        }
    root = project / "implementation"
    return {
        "index": root / "INDEX.md",
        "master": root / "00_IMP_MasterImplementationPlan.md",
        "protection": root / "01_IMP_ProgressiveProtectionPlan.md",
        "features_root": root / "features",
        "features_index": root / "features" / "INDEX.md",
        "index_label": "INDEX.md",
        "master_label": "00_IMP_MasterImplementationPlan.md",
        "protection_label": "01_IMP_ProgressiveProtectionPlan.md",
        "features_index_label": "features/INDEX.md",
    }


def _acceptance_package_paths(project: Path, documentation_mode: str) -> dict:
    if documentation_mode == "managed":
        root = project / "dev" / "criteria"
        return {
            "index": root / "INDEX.md",
            "traceability": root / "02_ACC_RequirementsTraceability.md",
            "verification": root / "03_ACC_VerificationMatrix.md",
            "coverage": root / "04_ACC_CoverageLedger.md",
            "index_label": "INDEX.md",
            "traceability_label": "02_ACC_RequirementsTraceability.md",
            "verification_label": "03_ACC_VerificationMatrix.md",
            "coverage_label": "04_ACC_CoverageLedger.md",
        }
    root = project / "acceptance"
    return {
        "index": root / "INDEX.md",
        "traceability": root / "requirements_matrix.md",
        "verification": root / "verification_matrix.md",
        "coverage": root / "coverage_ledger.md",
        "index_label": "INDEX.md",
        "traceability_label": "requirements_matrix.md",
        "verification_label": "verification_matrix.md",
        "coverage_label": "coverage_ledger.md",
    }


def _contracts_paths(project: Path, documentation_mode: str) -> dict:
    root = project / ("dev" if documentation_mode == "managed" else "") / "contracts"
    if documentation_mode == "managed":
        root = project / "dev" / "contracts"
    else:
        root = project / "contracts"
    return {
        "index": root / "INDEX.md",
        "requirements": root / "requirements.json",
        "mechanics": root / "core_mechanics.json",
        "architecture": root / "architecture_contract.json",
        "verification": root / "verification_contract.json",
        "protection": root / "protection_contract.json",
        "index_label": "INDEX.md",
        "requirements_label": "requirements.json",
        "mechanics_label": "core_mechanics.json",
        "architecture_label": "architecture_contract.json",
        "verification_label": "verification_contract.json",
        "protection_label": "protection_contract.json",
    }


def _subsystem_doc_records(project: Path, answers: dict, documentation_mode: str) -> list[dict]:
    if documentation_mode == "managed":
        root = project / "dev" / "design" / "subsystems"
        suffix = "_InProgress"
    else:
        root = project / "design" / "subsystems"
        suffix = ""

    zone_labels = {
        "stable": "Stable",
        "shared": "Shared",
        "features": "Feature",
    }
    records: list[dict] = []
    seen: set[str] = set()
    for zone in ("stable", "shared", "features"):
        values = answers.get(zone, [])
        if not isinstance(values, list):
            continue
        for raw_path in values:
            if not isinstance(raw_path, str):
                continue
            normalized = raw_path.strip().strip("/\\")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            slug = _slugify(normalized.replace("\\", "_").replace("/", "_"), fallback=zone)
            filename = f"{len(records) + 1:02d}_SUB_{slug}{suffix}.md"
            records.append(
                {
                    "zone": zone,
                    "zone_label": zone_labels.get(zone, zone.title()),
                    "path": normalized,
                    "slug": slug,
                    "basename": Path(normalized).name or normalized,
                    "doc_path": root / filename,
                    "doc_label": f"subsystems/{filename}",
                }
            )
    return records


def _implementation_feature_doc_records(paths: dict, mechanics: list[dict], documentation_mode: str) -> list[dict]:
    records: list[dict] = []
    suffix = "_InProgress.md" if documentation_mode == "managed" else ".md"
    for index, mechanic in enumerate(mechanics, start=1):
        slug = _slugify(str(mechanic.get("name", f"feature_{index}")), fallback=f"feature_{index}")
        filename = f"{index:02d}_IMF_{slug}{suffix}"
        records.append(
            {
                "mechanic": mechanic,
                "path": paths["features_root"] / filename,
                "label": f"features/{filename}",
                "filename": filename,
            }
        )
    return records


def _subsystem_focus_from_path(path_value: str, zone: str) -> list[str]:
    lower = path_value.lower()
    if any(token in lower for token in ("render", "shader", "visual", "graphics", "ui", "hud")):
        return [
            "Rendering, presentation, and visual-quality behavior stay isolated from authoritative business or gameplay truth.",
            "Interfaces into this subsystem should be driven by named data contracts, not hidden cross-module state access.",
        ]
    if any(token in lower for token in ("platform", "bootstrap", "runtime", "host", "os")):
        return [
            "Platform bootstrap, runtime integration, and host-specific concerns remain separate from feature logic.",
            "This subsystem should expose stable integration surfaces rather than absorbing feature-specific branching.",
        ]
    if any(token in lower for token in ("game", "feature", "play", "domain", "workflow", "app")):
        return [
            "User-facing or player-facing workflow logic lives here rather than leaking into stable foundations.",
            "This subsystem should compose stable and shared services without turning into a God module.",
        ]
    if zone == "stable":
        return [
            "Long-lived contracts, foundations, and invariants belong here.",
            "Changes should become progressively harder once downstream systems rely on this path.",
        ]
    if zone == "shared":
        return [
            "Cross-feature services and reusable adapters belong here.",
            "Shared code should not depend on feature-specific workflow decisions.",
        ]
    return [
        "Active feature delivery belongs here until responsibilities become stable enough to promote.",
        "Keep this area thin enough that emerging shared or stable responsibilities remain visible.",
    ]


def _looks_like_process_requirement(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "design doc",
            "implementation plan",
            "roadmap",
            "status",
            "criteria",
            "documentation",
            "devlog",
            "brief",
        )
    )


def _infer_requirement_category(text: str, source_type: str) -> str:
    lower = text.lower()
    process_tokens = (
        "design doc", "implementation plan", "roadmap", "status", "criteria",
        "documentation", "devlog", "brief", "playbook", "runbook",
    )
    visual_tokens = (
        "light", "lighting", "shadow", "shadows", "texture", "textures", "material",
        "materials", "render", "renderer", "shader", "visual", "animation", "animations",
        "sprite", "audio", "sound",
    )
    presentation_tokens = (
        "hud", "ui", "menu", "screen", "display", "panel", "dashboard", "minimap",
        "orientation", "navigation aid", "compass", "overlay",
    )
    gameplay_tokens = (
        "movement", "combat", "inventory", "loot", "enemy", "player", "puzzle",
        "door", "collision", "traversal", "jump", "quest", "score", "scoring",
        "interaction", "npc", "gameplay",
    )
    technical_tokens = (
        "config", "persistence", "database", "network", "runtime", "platform",
        "build", "install", "package", "performance", "memory", "security",
        "auth", "schema", "tooling",
    )
    if any(token in lower for token in process_tokens):
        return "process"
    if any(token in lower for token in visual_tokens):
        return "visual"
    if any(token in lower for token in presentation_tokens):
        return "presentation"
    if any(token in lower for token in gameplay_tokens):
        return "gameplay"
    if any(token in lower for token in technical_tokens):
        return "technical"
    if source_type == "quality":
        return "technical"
    if source_type == "invariants":
        return "technical"
    return "functional"


def _infer_requirement_semantics(text: str, source_type: str, category: str) -> str:
    lower = text.lower()
    if category == "process":
        return "process_artifact"
    if any(token in lower for token in ("boundary", "layer", "ownership", "depends on", "dependency", "interface", "contract")):
        return "architecture_invariant"
    if any(token in lower for token in ("build", "compile", "package", "install", "artifact", "log")):
        return "build_artifact"
    if category == "visual":
        return "visual_output"
    if category == "presentation":
        return "presentation"
    if any(token in lower for token in ("state", "inventory", "equipment", "equip", "progression", "persistence", "save", "load", "coherent", "consistency", "world")):
        return "authoritative_state"
    if any(token in lower for token in ("interaction", "use action", "open", "close", "activate", "pickup", "pick up", "trigger", "input")):
        return "interaction_flow"
    if category == "gameplay":
        return "behavior_flow"
    if source_type == "invariants":
        return "architecture_invariant"
    return "general_behavior"


def _resolve_owner_subsystem(answers: dict, category: str, semantics: str, text: str = "") -> dict:
    text_lower = text.lower()
    candidates: list[str] = []
    for zone in ("stable", "shared", "features"):
        values = answers.get(zone, [])
        if isinstance(values, list):
            candidates.extend(str(value) for value in values if isinstance(value, str))

    if semantics in {"process_artifact", "build_artifact", "architecture_invariant"}:
        return {
            "owner_subsystem": "owner_unresolved",
            "owner_confidence": "unresolved",
            "owner_rationale": "Requirement is cross-cutting or process-oriented; do not assign a code subsystem automatically.",
        }

    for candidate in candidates:
        parts = [part.lower() for part in re.split(r"[\\/]", candidate) if part]
        tokens = [part for part in parts if part not in {"src", "lib", "app", "code"}]
        if any(token in text_lower for token in tokens):
            return {
                "owner_subsystem": candidate,
                "owner_confidence": "high",
                "owner_rationale": "Owner inferred from direct path-token match in the requirement text.",
            }

    preferences = {
        "presentation": ("shared", "features", "stable"),
        "visual": ("shared", "features", "stable"),
        "gameplay": ("features", "shared", "stable"),
        "technical": ("stable", "shared", "features"),
        "operational": ("stable", "shared", "features"),
        "functional": ("features", "shared", "stable"),
        "process": (),
    }
    for zone in preferences.get(category, ("features", "shared", "stable")):
        values = answers.get(zone, [])
        if isinstance(values, list) and len(values) == 1:
            return {
                "owner_subsystem": str(values[0]),
                "owner_confidence": "medium",
                "owner_rationale": f"Owner inferred from the category-to-zone default for `{category}`.",
            }
    return {
        "owner_subsystem": "owner_unresolved",
        "owner_confidence": "unresolved",
        "owner_rationale": "No safe automatic owner match was available from the current boundaries.",
    }


def _infer_verification_mode(text: str, category: str, source_type: str, semantics: str) -> str:
    lower = text.lower()
    subjective_tokens = (
        "realistic", "believable", "credible", "convincing", "feel", "clear",
        "coherent", "immersive", "good looking", "readable", "intuitive",
    )
    if semantics == "process_artifact":
        return "verification_unresolved"
    if semantics in {"architecture_invariant", "build_artifact", "authoritative_state"}:
        return "automated"
    if semantics in {"visual_output", "presentation", "interaction_flow", "behavior_flow"}:
        if any(token in lower for token in subjective_tokens):
            return "hybrid"
        return "machine_assisted"
    if any(token in lower for token in subjective_tokens):
        return "hybrid"
    if source_type == "quality" and category in {"visual", "gameplay"}:
        return "hybrid"
    if category in {"visual", "presentation", "gameplay"}:
        return "machine_assisted"
    if source_type == "invariants":
        return "automated"
    return "automated"


def _default_evidence_for_mode(mode: str, category: str, semantics: str) -> list[str]:
    if mode == "verification_unresolved":
        return ["define the concrete evidence path before approval"]
    if mode == "automated":
        if semantics in {"architecture_invariant", "build_artifact"}:
            return ["test output", "build or validation log"]
        return ["test output", "runtime log"]
    if mode == "machine_assisted":
        if semantics == "presentation":
            return ["scripted scenario or replay", "annotated screenshot or clip", "runtime log"]
        return ["scripted scenario or replay", "runtime log", "screenshot or clip"]
    if mode == "human_required":
        return ["manual review checklist", "annotated screenshot or clip"]
    evidence = ["automated prerequisite checks", "screenshot or clip", "manual review checklist"]
    if semantics in {"interaction_flow", "behavior_flow"} or category == "gameplay":
        evidence.insert(1, "scripted scenario or replay")
    return evidence


def _default_automated_checks(text: str, category: str, mode: str, semantics: str) -> list[str]:
    lower = text.lower()
    checks: list[str] = []
    if mode == "verification_unresolved":
        return []
    if semantics == "visual_output":
        checks.extend(
            [
                "assert the required render/material/shader path is wired into the runtime",
                "capture deterministic scene evidence from a named camera pose",
            ]
        )
    elif semantics == "presentation":
        checks.extend(
            [
                "run a named scenario that makes the presentation state visible",
                "capture evidence that the visible state matches the underlying authoritative state",
            ]
        )
    elif semantics == "interaction_flow":
        checks.extend(
            [
                "run a scripted interaction scenario that exercises the requirement end to end",
                "assert the resulting state transition or observable outcome",
            ]
        )
    elif semantics == "behavior_flow":
        checks.extend(
            [
                "run a scripted core-flow scenario or replay",
                "assert the resulting gameplay state invariants",
            ]
        )
    elif semantics == "authoritative_state":
        checks.extend(
            [
                "run a reproducible state-focused test or replay",
                "assert the authoritative state invariants for the requirement",
            ]
        )
    elif semantics == "architecture_invariant":
        checks.extend(
            [
                "validate the impacted boundary or architectural invariant mechanically",
                "record the rule, command, or report that proved the invariant still holds",
            ]
        )
    elif semantics == "build_artifact":
        checks.extend(
            [
                "run the relevant build or packaging path",
                "record the resulting artifact or validation log",
            ]
        )
    elif category in {"technical", "process"}:
        checks.extend(
            [
                "run unit or integration checks for the affected subsystem",
                "validate the impacted boundary or configuration path mechanically",
            ]
        )
    else:
        checks.extend(
            [
                "exercise the target behavior through a reproducible test or scripted path",
                "record the resulting state transition or observable output",
            ]
        )

    if "shadow" in lower or "light" in lower or "texture" in lower or "animation" in lower:
        checks.append("capture before/after visual evidence showing the feature is active")
    if mode == "hybrid":
        checks.append("attach a human review note for the perceptual or experiential quality threshold")
    return checks


def _needs_requirement_formalization(text: str, source_type: str, semantics: str) -> bool:
    lower = text.lower()
    ambiguity_tokens = (" or ", " and/or ", " etc", "maybe ", "possibly ", "/")
    if semantics in {"process_artifact", "general_behavior"}:
        return True
    if source_type == "quality":
        return True
    if any(token in lower for token in ambiguity_tokens):
        return True
    if source_type == "must_haves":
        return len(text.split()) > 4
    if source_type == "invariants":
        return not (lower.startswith("no ") or "must not" in lower or "never " in lower)
    return True


def _seed_acceptance_criteria(text: str, source_type: str, formalization_needed: bool) -> list[str]:
    if formalization_needed:
        return [
            "Split this brief statement into one or more atomic pass/fail criteria before approving the baseline.",
            "Record the exact evidence path, command, scenario, or review note required to prove it.",
        ]
    if source_type == "invariants":
        return [f"Demonstrate through a reproducible check that this condition holds: {text}"]
    return [f"Demonstrate the capability exists through one reproducible scenario focused on: {text}"]


def _build_requirement_records(answers: dict) -> list[dict]:
    source_records = _kickoff_source_records(answers)
    records: list[dict] = []
    seen: set[str] = set()
    index = 1
    for source_record in source_records:
        if source_record.get("role") != "requirement_candidate":
            continue
        normalized = str(source_record.get("text", "")).strip()
        if not normalized:
            continue
        marker = normalized.lower()
        if marker in seen:
            continue
        seen.add(marker)
        source_type = str(source_record.get("source_type", "must_haves"))
        category = _infer_requirement_category(normalized, source_type)
        semantics = _infer_requirement_semantics(normalized, source_type, category)
        owner = _resolve_owner_subsystem(answers, category, semantics, normalized)
        formalization_needed = _needs_requirement_formalization(normalized, source_type, semantics)
        formalization_state = "formalization_needed" if formalization_needed else "provisional"
        verification_mode = _infer_verification_mode(normalized, category, source_type, semantics)
        verification_resolution = "verification_unresolved" if verification_mode == "verification_unresolved" else "defined"
        baseline_flags: list[str] = []
        if formalization_needed:
            baseline_flags.append("formalization_needed")
        if owner["owner_subsystem"] == "owner_unresolved":
            baseline_flags.append("owner_unresolved")
        if verification_resolution == "verification_unresolved":
            baseline_flags.append("verification_unresolved")
        requirement_id = f"REQ-{index:02d}"
        records.append(
            {
                "id": requirement_id,
                "title": normalized,
                "description": normalized,
                "source_type": source_type,
                "source_item_ids": [source_record["id"]],
                "source_brief_refs": list(source_record.get("source_brief_refs", [])),
                "priority": "mandatory_mvp",
                "category": category,
                "semantics": semantics,
                "owner_subsystem": owner["owner_subsystem"],
                "owner_confidence": owner["owner_confidence"],
                "owner_rationale": owner["owner_rationale"],
                "owner_resolution": "owner_unresolved" if owner["owner_subsystem"] == "owner_unresolved" else "assigned",
                "depends_on": ["design_baseline", "verification_contract"],
                "acceptance_criteria": _seed_acceptance_criteria(normalized, source_type, formalization_needed),
                "formalization_state": formalization_state,
                "verification_mode": verification_mode,
                "verification_resolution": verification_resolution,
                "required_evidence": _default_evidence_for_mode(verification_mode, category, semantics),
                "evidence_paths": [],
                "automated_checks": _default_automated_checks(normalized, category, verification_mode, semantics),
                "human_review_required": verification_mode in {"human_required", "hybrid"},
                "baseline_flags": baseline_flags,
                "status": "planned",
            }
        )
        index += 1

    if records:
        return records

    fallback = "Translate the main project outcome into one atomic, verifiable requirement before coding."
    owner = _resolve_owner_subsystem(answers, "functional", "general_behavior", fallback)
    return [
        {
            "id": "REQ-01",
            "title": fallback,
            "description": fallback,
            "source_type": "process",
            "source_item_ids": ["SRC-PLACEHOLDER-01"],
            "source_brief_refs": ["kickoff.placeholder"],
            "priority": "mandatory_mvp",
            "category": "functional",
            "semantics": "general_behavior",
            "owner_subsystem": owner["owner_subsystem"],
            "owner_confidence": owner["owner_confidence"],
            "owner_rationale": owner["owner_rationale"],
            "owner_resolution": "owner_unresolved",
            "depends_on": ["design_baseline", "verification_contract"],
            "acceptance_criteria": [
                "Replace the placeholder with a real product requirement.",
                "Split it into atomic pass/fail criteria before implementation starts.",
            ],
            "formalization_state": "formalization_needed",
            "verification_mode": "verification_unresolved",
            "verification_resolution": "verification_unresolved",
            "required_evidence": ["reviewed requirement record"],
            "evidence_paths": [],
            "automated_checks": [],
            "human_review_required": False,
            "baseline_flags": ["coverage_unresolved", "formalization_needed", "owner_unresolved", "verification_unresolved"],
            "status": "planned",
        }
    ]


def _build_core_mechanics_records(answers: dict, requirements: list[dict]) -> list[dict]:
    mechanics: list[dict] = []
    for requirement in requirements:
        if requirement.get("source_type") != "must_haves":
            continue
        description = str(requirement.get("description", ""))
        if _looks_like_process_requirement(description):
            continue
        owner = str(requirement.get("owner_subsystem", "to_define"))
        participating = [owner] if owner and owner != "to_define" else []
        if requirement.get("verification_mode") in {"machine_assisted", "hybrid"}:
            shared_values = answers.get("shared", [])
            if isinstance(shared_values, list) and shared_values:
                shared_owner = str(shared_values[0])
                if shared_owner not in participating:
                    participating.append(shared_owner)
        mechanics.append(
            {
                "id": f"MECH-{len(mechanics) + 1:02d}",
                "name": description,
                "description": f"Deliver and stabilize the mechanic around: {description}",
                "player_or_user_value": f"Provides first-slice value through: {description}",
                "owning_subsystem": owner,
                "participating_subsystems": participating,
                "required_for_mvp": True,
                "related_requirement_ids": [requirement["id"]],
                "preconditions": ["design baseline reviewed", "acceptance criteria written"],
                "completion_criteria": [
                    f"{requirement['id']} reaches at least evidence-produced state",
                    "Any required human review is explicitly recorded before calling the mechanic stable",
                ],
                "verification_requirements": {
                    "verification_mode": requirement["verification_mode"],
                    "required_evidence": list(requirement.get("required_evidence", [])),
                    "human_review_required": bool(requirement.get("human_review_required")),
                },
                "change_policy": "Free to evolve while draft; once stable, update linked design and acceptance artifacts before code-level mutation.",
                "maturity_state": "draft",
            }
        )

    if mechanics:
        return mechanics

    owner = _resolve_owner_subsystem(answers, "functional", "general_behavior")
    owner_subsystem = str(owner.get("owner_subsystem", "owner_unresolved"))
    return [
        {
            "id": "MECH-01",
            "name": "Primary end-to-end user workflow",
            "description": "Define the main mechanic or workflow before feature coding expands.",
            "player_or_user_value": "Ensures the project has one coherent slice to implement and verify first.",
            "owning_subsystem": owner_subsystem,
            "participating_subsystems": [owner_subsystem] if owner_subsystem and owner_subsystem != "owner_unresolved" else [],
            "required_for_mvp": True,
            "related_requirement_ids": [requirements[0]["id"]] if requirements else [],
            "preconditions": ["design baseline reviewed", "acceptance criteria written"],
            "completion_criteria": [
                "Replace this placeholder with one or more named mechanics derived from the brief.",
                "Define the verification and stabilization path before claiming MVP completion.",
            ],
            "verification_requirements": {
                "verification_mode": "hybrid",
                "required_evidence": ["requirements matrix", "manual review note"],
                "human_review_required": True,
            },
            "change_policy": "May evolve during planning, but must not remain implicit once implementation starts.",
            "maturity_state": "draft",
        }
    ]


def _build_verification_contract(answers: dict, requirements: list[dict], mechanics: list[dict]) -> dict:
    return {
        "version": 1,
        "project": answers.get("name", "Project"),
        "status_model": _canonical_status_model(),
        "requirements": [
            {
                "requirement_id": requirement["id"],
                "formalization_state": requirement.get("formalization_state", "formalization_needed"),
                "owner_resolution": requirement.get("owner_resolution", "owner_unresolved"),
                "owner_confidence": requirement.get("owner_confidence", "unresolved"),
                "verification_resolution": requirement.get("verification_resolution", "verification_unresolved"),
                "baseline_flags": list(requirement.get("baseline_flags", [])),
                "verification_mode": requirement["verification_mode"],
                "automated_checks": list(requirement.get("automated_checks", [])),
                "required_evidence": list(requirement.get("required_evidence", [])),
                "evidence_paths": list(requirement.get("evidence_paths", [])),
                "human_review_required": bool(requirement.get("human_review_required")),
                "completion_gate": "Do not call this complete until the required evidence exists and the declared verification path actually ran.",
            }
            for requirement in requirements
        ],
        "core_mechanics": [
            {
                "mechanic_id": mechanic["id"],
                "required_maturity_before_complete_claim": "human_verified"
                if mechanic.get("verification_requirements", {}).get("human_review_required")
                else "evidence_produced",
                "change_control_note": "Stable mechanics require linked design and acceptance updates before mutation.",
            }
            for mechanic in mechanics
        ],
    }


def _build_coverage_ledger(answers: dict, requirements: list[dict]) -> list[dict]:
    source_records = _kickoff_source_records(answers)
    linked_requirements: dict[str, list[dict]] = {}
    requirements_by_text: dict[str, list[dict]] = {}
    for requirement in requirements:
        requirements_by_text.setdefault(str(requirement.get("description", "")).strip().lower(), []).append(requirement)
        for source_id in requirement.get("source_item_ids", []):
            linked_requirements.setdefault(str(source_id), []).append(requirement)

    ledger: list[dict] = []
    for source_record in source_records:
        source_id = str(source_record["id"])
        linked = linked_requirements.get(source_id, [])
        if not linked:
            linked = requirements_by_text.get(str(source_record.get("text", "")).strip().lower(), [])
        role = str(source_record.get("role", "context"))
        if linked:
            coverage_status = "requirement_covered"
            notes = "Covered by generated requirement records; review any baseline flags before approval."
        elif role == "scope_constraint":
            coverage_status = "context_only"
            notes = "Recorded as scope guidance or anti-goal, not yet translated into a requirement."
        elif role == "context":
            coverage_status = "context_only"
            notes = "Captured as kickoff context; review if it hides a capability or verification obligation."
        else:
            coverage_status = "coverage_unresolved"
            notes = "This candidate did not become a requirement record and must be reviewed before approval."
        ledger.append(
            {
                "source_item_id": source_id,
                "source_type": source_record.get("source_type", "unknown"),
                "role": role,
                "text": source_record.get("text", ""),
                "coverage_status": coverage_status,
                "linked_requirement_ids": [requirement["id"] for requirement in linked],
                "notes": notes,
            }
        )

    if ledger:
        return ledger
    return [
        {
            "source_item_id": "SRC-PLACEHOLDER-01",
            "source_type": "placeholder",
            "role": "coverage_unresolved",
            "text": "No kickoff source fragments were available to seed the baseline.",
            "coverage_status": "coverage_unresolved",
            "linked_requirement_ids": [],
            "notes": "Collect real kickoff statements before treating the baseline as approved.",
        }
    ]


def _baseline_gap_summary(requirements: list[dict], coverage_ledger: list[dict]) -> dict:
    return {
        "formalization": [req for req in requirements if req.get("formalization_state") == "formalization_needed"],
        "owner": [req for req in requirements if req.get("owner_resolution") == "owner_unresolved"],
        "verification": [req for req in requirements if req.get("verification_resolution") == "verification_unresolved"],
        "coverage": [entry for entry in coverage_ledger if entry.get("coverage_status") == "coverage_unresolved"],
    }


def _zone_dependency_contract(zone: str) -> dict:
    if zone == "stable":
        return {
            "allowed_outbound_zones": ["stable"],
            "expected_inbound_zones": ["shared", "features"],
            "contract_note": "Stable subsystems may be consumed by less-stable zones but should not absorb feature-specific policy.",
        }
    if zone == "shared":
        return {
            "allowed_outbound_zones": ["stable", "shared"],
            "expected_inbound_zones": ["features"],
            "contract_note": "Shared subsystems may bridge stable foundations and feature code, but should not become feature policy owners.",
        }
    return {
        "allowed_outbound_zones": ["stable", "shared", "features"],
        "expected_inbound_zones": ["features"],
        "contract_note": "Feature subsystems may orchestrate shared and stable capabilities, but should stay replaceable and local in scope.",
    }


def _subsystem_owned_hints(subsystem: dict, related_requirements: list[dict]) -> dict:
    state_hints: list[str] = []
    artifact_hints: list[str] = []
    interface_hints: list[str] = []

    semantics = {str(requirement.get("semantics", "")) for requirement in related_requirements}
    zone = str(subsystem.get("zone", "features"))
    path = str(subsystem.get("path", "subsystem"))

    if "authoritative_state" in semantics:
        state_hints.append("Own the authoritative state transitions and invariant enforcement for linked requirements.")
        interface_hints.append("Expose named commands, events, or read models instead of shared mutable state.")
    if "presentation" in semantics:
        state_hints.append("Own presentation state derived from authoritative data without becoming the source of truth.")
        interface_hints.append("Accept derived view models or events, not direct mutation of authoritative state.")
    if "visual_output" in semantics:
        artifact_hints.append("Own rendering, asset, or visual-output paths without redefining authoritative product truth.")
        interface_hints.append("Consume explicit view contracts rather than domain internals.")
    if "interaction_flow" in semantics or "behavior_flow" in semantics:
        state_hints.append("Own the workflow or interaction sequencing for linked capabilities.")
        interface_hints.append("Keep cross-subsystem workflow steps explicit and named.")
    if "build_artifact" in semantics or "process_artifact" in semantics:
        artifact_hints.append("Own the generated artifacts or process outputs explicitly referenced by linked requirements.")
    if "architecture_invariant" in semantics:
        artifact_hints.append("Own the local boundary rules and dependency expectations recorded for this subsystem.")

    if zone == "stable":
        if not state_hints:
            state_hints.append("Prefer long-lived foundations, deterministic state handling, or platform responsibilities.")
        interface_hints.append("Changes here should be small, reviewable, and justified through linked design updates.")
    elif zone == "shared":
        if not artifact_hints:
            artifact_hints.append("Prefer reusable adapters, shared services, or presentation/integration surfaces.")
        interface_hints.append("Avoid absorbing feature-only branching; keep shared contracts reusable across features.")
    else:
        if not state_hints:
            state_hints.append("Prefer feature-local orchestration and short-lived product workflows.")
        interface_hints.append("Feature code may compose shared and stable services, but should not redefine their contracts.")

    if not artifact_hints:
        artifact_hints.append(f"Record any files, generated outputs, or interfaces that `{path}` owns before implementation expands.")

    return {
        "owned_state_hints": state_hints,
        "owned_artifact_hints": artifact_hints,
        "interface_expectations": list(dict.fromkeys(interface_hints)),
    }


def _build_architecture_contract(subsystems: list[dict], requirements: list[dict], coverage_ledger: list[dict]) -> dict:
    subsystem_contracts: list[dict] = []
    for subsystem in subsystems:
        related_requirements = [req for req in requirements if req.get("owner_subsystem") == subsystem["path"]]
        dependency_contract = _zone_dependency_contract(str(subsystem.get("zone", "features")))
        owned_hints = _subsystem_owned_hints(subsystem, related_requirements)
        subsystem_contracts.append(
            {
                "path": subsystem["path"],
                "zone": subsystem["zone"],
                "zone_label": subsystem["zone_label"],
                "linked_requirement_ids": [req["id"] for req in related_requirements],
                "owned_semantics": sorted({str(req.get("semantics", "")) for req in related_requirements if req.get("semantics")}),
                "owned_state_hints": owned_hints["owned_state_hints"],
                "owned_artifact_hints": owned_hints["owned_artifact_hints"],
                "interface_expectations": owned_hints["interface_expectations"],
                "dependency_contract": dependency_contract,
            }
        )

    gaps = _baseline_gap_summary(requirements, coverage_ledger)
    return {
        "version": 1,
        "subsystems": subsystem_contracts,
        "unresolved_requirement_ids": sorted(
            {
                req["id"]
                for bucket in ("formalization", "owner", "verification")
                for req in gaps[bucket]
            }
        ),
        "coverage_gap_source_ids": [entry["source_item_id"] for entry in gaps["coverage"]],
    }


def _implementation_lifecycle_states() -> list[str]:
    return [
        "draft",
        "design_baselined",
        "implementation_planned",
        "implemented",
        "verified",
        "stable",
        "protected",
    ]


def _bootstrap_lifecycle_state_for_zone(zone: str) -> str:
    if zone == "stable":
        return "design_baselined"
    if zone == "shared":
        return "implementation_planned"
    return "draft"


def _initial_protection_level_for_zone(zone: str) -> str:
    if zone in {"stable", "shared"}:
        return "warn"
    return "advisory"


def _target_protection_level_for_zone(zone: str) -> str:
    if zone == "stable":
        return "deny_candidate"
    if zone == "shared":
        return "warn"
    return "advisory"


def _build_progressive_protection_contract(subsystems: list[dict],
                                           requirements: list[dict],
                                           mechanics: list[dict]) -> dict:
    stage_policies = [
        {
            "state": "draft",
            "rule": "Keep paths flexible while scope and ownership are still moving.",
        },
        {
            "state": "design_baselined",
            "rule": "Update design and acceptance artifacts first when boundary assumptions change.",
        },
        {
            "state": "implementation_planned",
            "rule": "Map work packages and likely file touch points before enabling stronger protection.",
        },
        {
            "state": "implemented",
            "rule": "Do not call the area stable yet; verify behavior and evidence first.",
        },
        {
            "state": "verified",
            "rule": "Promote warn-level protection once reproducible evidence exists and the interface is reviewable.",
        },
        {
            "state": "stable",
            "rule": "Use explicit change control for cross-subsystem mutations and prepare stronger protection if downstream code relies on the interface.",
        },
        {
            "state": "protected",
            "rule": "Treat the area as intentionally hard to mutate; update linked design, implementation, and verification artifacts before code changes.",
        },
    ]

    subsystem_policies: list[dict] = []
    for subsystem in subsystems:
        related_requirements = [req for req in requirements if req.get("owner_subsystem") == subsystem["path"]]
        related_mechanics = [
            mechanic for mechanic in mechanics
            if subsystem["path"] == mechanic.get("owning_subsystem")
            or subsystem["path"] in mechanic.get("participating_subsystems", [])
        ]
        zone = str(subsystem.get("zone", "features"))
        subsystem_policies.append(
            {
                "path": subsystem["path"],
                "zone": zone,
                "bootstrap_state": _bootstrap_lifecycle_state_for_zone(zone),
                "first_protection_level": _initial_protection_level_for_zone(zone),
                "target_protection_level": _target_protection_level_for_zone(zone),
                "linked_requirement_ids": [req["id"] for req in related_requirements],
                "linked_mechanic_ids": [mechanic["id"] for mechanic in related_mechanics],
                "promotion_gate": (
                    "Require verified evidence plus explicit subsystem review before raising protection."
                    if zone == "features"
                    else "Require verified evidence and interface review before raising protection."
                ),
                "change_control_trigger": (
                    "Any mutation that changes owned state, named interfaces, or linked evidence obligations."
                ),
                "file_scope_note": (
                    "Start with path-level protection. Add narrower file or block-level protection only after the subsystem contract is stable."
                ),
            }
        )

    return {
        "version": 1,
        "lifecycle_states": _implementation_lifecycle_states(),
        "stage_policies": stage_policies,
        "subsystem_policies": subsystem_policies,
    }


def _planning_gap_questions(requirements: list[dict], coverage_ledger: list[dict]) -> list[str]:
    gaps = _baseline_gap_summary(requirements, coverage_ledger)
    questions: list[str] = []
    if gaps["coverage"]:
        source_ids = ", ".join(entry["source_item_id"] for entry in gaps["coverage"][:4])
        questions.append(
            f"Which uncovered kickoff statements ({source_ids}) must become explicit baseline requirements before implementation starts?"
        )
    if gaps["formalization"]:
        req_ids = ", ".join(req["id"] for req in gaps["formalization"][:4])
        questions.append(
            f"How should {req_ids} be split into atomic pass/fail criteria with concrete evidence paths?"
        )
    if gaps["owner"]:
        req_ids = ", ".join(req["id"] for req in gaps["owner"][:4])
        questions.append(
            f"Which subsystem should own {req_ids}, and what state or artifact must stay outside presentation or feature convenience code?"
        )
    if gaps["verification"]:
        req_ids = ", ".join(req["id"] for req in gaps["verification"][:4])
        questions.append(
            f"What exact verification path should prove {req_ids} without relying on category-level placeholders?"
        )
    if not questions:
        questions.append("Which architectural or product decision is still expensive enough to justify external refinement?")
    return questions


def _planning_data_from_answers(answers: dict) -> dict:
    planning = answers.get("planning", {})
    if not isinstance(planning, dict):
        planning = {}
    tier = str(planning.get("tier", "core"))
    manual = bool(planning.get("manual_consultation_allowed", False))
    return _planning_profile_for_tier(tier, manual)


def _planning_consultation_packet_label(documentation_mode: str) -> str:
    if documentation_mode == "managed":
        return "dev/handoffs/01_HOF_PlanningConsultationPacket_Template.md"
    return "PLANNING_CONSULTATION_PACKET.md"


def _planning_specialist_packet_label(documentation_mode: str) -> str:
    if documentation_mode == "managed":
        return "dev/handoffs/02_HOF_SpecialistPlanningRefinementPacket_Template.md"
    return "SPECIALIST_PLANNING_REFINEMENT_PACKET.md"


def _render_planning_design_guidance(planning: dict) -> str:
    mode = planning.get("planning_mode", "solo_structured")
    if mode == "solo_structured_manual_consultation_ready":
        return (
            "- The main chat remains the primary software-engineering planner.\n"
            "- If a critical domain or architecture question blocks progress, a bounded manual consultation packet may be prepared.\n"
            "- Any external answer remains advisory until the main chat explicitly accepts or rejects it.\n"
        )
    if mode == "orchestrated_specialists":
        return (
            "- Kickoff planning may incorporate specialist perspectives such as planner, architecture, domain, or review roles.\n"
            "- Design tradeoffs should record where specialist input materially changed the direction.\n"
            "- The final design truth must still be consolidated into one coherent project-level document.\n"
        )
    return (
        "- The main chat is responsible for the first engineering package without relying on specialist orchestration.\n"
        "- Tradeoffs should be resolved directly in the design and implementation plan rather than deferred to hidden helpers.\n"
        "- The kickoff must remain strong enough to guide a real vertical slice on its own.\n"
    )


def _render_planning_plan_guidance(planning: dict) -> str:
    mode = planning.get("planning_mode", "solo_structured")
    if mode == "solo_structured_manual_consultation_ready":
        return (
            "1. Freeze the direct engineering proposal in the main chat first.\n"
            "2. If a blocking uncertainty remains, prepare a bounded manual consultation packet.\n"
            "3. Integrate or reject the returned advice explicitly before implementation starts.\n"
        )
    if mode == "orchestrated_specialists":
        return (
            "1. Consolidate the base planner proposal.\n"
            "2. Run specialist checkpoints on the highest-risk architectural or domain decisions.\n"
            "3. Resolve disagreements into one implementation order before coding begins.\n"
        )
    return (
        "1. Consolidate the stack, architecture, and first slice directly in the main chat.\n"
        "2. Record the rationale for major tradeoffs in the design/plan pair.\n"
        "3. Move to implementation once the direct plan is coherent and testable.\n"
    )


def _render_planning_design_protocol(planning: dict, documentation_mode: str) -> str:
    mode = planning.get("planning_mode", "solo_structured")
    packet_label = _planning_consultation_packet_label(documentation_mode)
    if mode == "solo_structured_manual_consultation_ready":
        return (
            "- The kickoff still belongs to one lead software-engineering conversation.\n"
            f"- If one bounded uncertainty survives the direct planning pass, prepare `{packet_label}` instead of opening a vague second opinion loop.\n"
            "- The packet must contain one specific question, the current proposal, rejected alternatives, and the constraints the answer must respect.\n"
            "- Returned advice stays advisory until the main chat records an explicit accept / reject / partially accept decision.\n"
        )
    if mode == "orchestrated_specialists":
        return (
            "- The kickoff may incorporate a lead planner plus bounded specialist viewpoints.\n"
            "- Record specialist influence only when it materially changes stack choice, architecture boundaries, validation strategy, or build order.\n"
            "- Final design truth still lands in one coherent document owned at the project level.\n\n"
            "### Specialist Provenance Note\n\n"
            "| Role | Expected scope | When it matters |\n"
            "|---|---|---|\n"
            "| Lead planner / software engineer | Consolidate stack, architecture, and slice order | Always |\n"
            "| Domain specialist | Product/domain-fit tradeoffs | When product realism or domain complexity changes the scope |\n"
            "| Architecture specialist | High-cost runtime/boundary decisions | When rendering, platform, or structural choices are expensive to reverse |\n"
            "| Review / verification specialist | Acceptance criteria and risk checks | Before coding starts on high-risk slices |\n"
        )
    return (
        "- The kickoff is owned directly by the main chat without outside specialist routing.\n"
        "- The first design package should already contain enough tradeoff rationale to start implementation honestly.\n"
        "- If a question is not yet answerable, record it as an open question rather than pretending hidden expert input exists.\n"
    )


def _render_planning_plan_control(planning: dict, documentation_mode: str) -> str:
    mode = planning.get("planning_mode", "solo_structured")
    packet_label = _planning_consultation_packet_label(documentation_mode)
    if mode == "solo_structured_manual_consultation_ready":
        return (
            "- Trigger manual consultation only for a bounded blocking uncertainty, not for general reassurance.\n"
            f"- Use `{packet_label}` to capture the question, current proposal, alternatives, and constraints.\n"
            "- When the answer returns, record one of: accept / reject / partially accept.\n"
            "- If the answer changes the direction, update the design doc, implementation plan, and any impacted roadmap/bugs entries in the same planning pass.\n"
        )
    if mode == "orchestrated_specialists":
        return (
            "- Specialist checkpoints should stay bounded to the highest-cost or hardest-to-reverse decisions.\n"
            "- Record specialist influence only when it changes the selected direction.\n\n"
            "| Decision area | Lead owner | Specialist input | Outcome |\n"
            "|---|---|---|---|\n"
            "| Stack / runtime choice | Lead planner | (record if used) | (accepted direction) |\n"
            "| Architecture / boundaries | Lead planner | (record if used) | (accepted direction) |\n"
            "| Validation strategy | Lead planner | (record if used) | (accepted direction) |\n"
        )
    return (
        "- Keep planning decisions in the main chat and record major tradeoffs directly in the project docs.\n"
        "- Resolve uncertainty by simplifying scope or refining assumptions before implementation, rather than inventing hidden reviewers.\n"
    )


def _render_manual_consultation_packet(answers: dict,
                                       design_label: str,
                                       plan_label: str,
                                       requirements: list[dict],
                                       coverage_ledger: list[dict]) -> str:
    today = date.today().isoformat()
    gaps = _baseline_gap_summary(requirements, coverage_ledger)
    question_lines = "\n".join(f"- {question}" for question in _planning_gap_questions(requirements, coverage_ledger))
    gap_lines = [
        f"- **Requirements needing atomic formalization**: {', '.join(req['id'] for req in gaps['formalization']) or '(none)'}",
        f"- **Requirements with unresolved owner**: {', '.join(req['id'] for req in gaps['owner']) or '(none)'}",
        f"- **Requirements with unresolved verification**: {', '.join(req['id'] for req in gaps['verification']) or '(none)'}",
        f"- **Uncovered kickoff source statements**: {', '.join(entry['source_item_id'] for entry in gaps['coverage']) or '(none)'}",
    ]
    return (
        "# Handoff: Planning Consultation Packet\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: Template\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{design_label}`, `{plan_label}`, `CONTROLCODING.md`\n"
        "> **Purpose**: Prepare one bounded external consultation without turning Core planning into hidden specialist orchestration.\n\n"
        "---\n\n"
        "## 1. When this packet is allowed\n\n"
        "- The main chat has already produced a direct proposal.\n"
        "- One bounded domain or architecture uncertainty is still blocking the kickoff.\n"
        "- The user explicitly approved asking for outside input.\n\n"
        "## 2. Rules\n\n"
        "- Ask one bounded question, not a vague brainstorm.\n"
        "- Include the current proposal and the best rejected alternatives.\n"
        "- Keep the answer format constrained enough that it can be evaluated against the existing plan.\n"
        "- Returned advice remains advisory until the main chat records an explicit accept / reject / partially accept decision.\n"
        "- If the advice changes the direction, update the design doc, implementation plan, and any impacted roadmap/bugs entries.\n\n"
        "## 3. Open Kickoff Gaps\n\n"
        + "\n".join(gap_lines)
        + "\n\n## 4. Suggested Bounded Question Candidates\n\n"
        + question_lines
        + "\n\n## 5. Packet Template\n\n"
        "- **Question to external specialist**:\n"
        "- **Why the current kickoff is blocked**:\n"
        "- **Current proposed direction**:\n"
        "- **Alternatives already considered**:\n"
        "- **Constraints the answer must respect**:\n"
        "- **Preferred answer format**:\n"
        "- **Integration decision after answer returns**: accept / reject / partially accept\n"
        "- **Changed project docs after integration**:\n"
    )


def _render_specialist_refinement_packet(answers: dict,
                                         design_label: str,
                                         plan_label: str,
                                         requirements: list[dict],
                                         coverage_ledger: list[dict]) -> str:
    today = date.today().isoformat()
    gaps = _baseline_gap_summary(requirements, coverage_ledger)
    formalization_ids = ", ".join(req["id"] for req in gaps["formalization"]) or "(none)"
    owner_ids = ", ".join(req["id"] for req in gaps["owner"]) or "(none)"
    verification_ids = ", ".join(req["id"] for req in gaps["verification"]) or "(none)"
    coverage_ids = ", ".join(entry["source_item_id"] for entry in gaps["coverage"]) or "(none)"
    return (
        "# Handoff: Specialist Planning Refinement Packet\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: Template\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{design_label}`, `{plan_label}`, `CONTROLCODING.md`\n"
        "> **Purpose**: Prepare explicit specialist prompts for refining the kickoff baseline without changing the project truth model.\n\n"
        "---\n\n"
        "## 1. Open Kickoff Gaps\n\n"
        f"- **Requirements needing atomic formalization**: {formalization_ids}\n"
        f"- **Requirements with unresolved owner**: {owner_ids}\n"
        f"- **Requirements with unresolved verification**: {verification_ids}\n"
        f"- **Uncovered kickoff source statements**: {coverage_ids}\n\n"
        "## 2. Specialist Packets\n\n"
        "### Architecture specialist prompt\n\n"
        "- **Goal**: Resolve subsystem ownership, dependency direction, and state boundaries for the unresolved items.\n"
        f"- **Focus requirements**: {owner_ids}\n"
        "- **Expected output**: updated owner map, owned state per subsystem, allowed inbound/outbound dependencies, and any required interface contracts.\n"
        "- **Must respect**: existing stable/shared/feature boundaries unless there is an explicit reason to revise them.\n\n"
        "### Verification specialist prompt\n\n"
        "- **Goal**: Turn unresolved or aggregate baseline statements into atomic criteria with concrete proof paths.\n"
        f"- **Focus requirements**: {formalization_ids}; {verification_ids}\n"
        "- **Expected output**: atomic acceptance criteria, commands/scenarios, required evidence, and required human review notes.\n"
        "- **Must respect**: implemented vs verified separation and the canonical status model.\n\n"
        "### Domain or product specialist prompt\n\n"
        "- **Goal**: Resolve missing coverage from the brief or kickoff package.\n"
        f"- **Focus source statements**: {coverage_ids}\n"
        "- **Expected output**: which statements must become requirements now, which are deferred, and what ambiguity still remains.\n"
        "- **Must respect**: no silent scope deletion.\n\n"
        "## 3. Consolidation Rule\n\n"
        "- Specialist output is input to the main planning authority, not final project truth by itself.\n"
        "- Merge accepted specialist output back into the design package, contracts, and implementation plan in one coherent pass.\n"
    )


def _render_system_overview_doc(answers: dict, requirements: list[dict], mechanics: list[dict], doc_map: dict) -> str:
    kickoff = answers.get("kickoff", {})
    today = date.today().isoformat()
    product = answers.get("product", {}) if isinstance(answers.get("product"), dict) else {}
    users = _bulletize(kickoff.get("users", ""), "Clarify the primary user or player and their main journey.")
    core_values = "\n".join(
        f"- `{record['id']}` - {record['description']} ({record['verification_mode']})"
        for record in requirements[:8]
    )
    mechanic_lines = "\n".join(
        f"- `{mechanic['id']}` - {mechanic['name']} (owner: `{mechanic['owning_subsystem']}`)"
        for mechanic in mechanics[:8]
    )
    return (
        f"# Design: System Overview for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('design_foundation', 'design baseline')}`, `{doc_map.get('requirements_matrix', 'requirements matrix')}`\n"
        "> **Purpose**: Formalize the product-level system view that sits between the brief and subsystem implementation.\n\n"
        "---\n\n"
        "## 1. Product Summary\n\n"
        f"- **Vision**: {kickoff.get('vision', '(to be filled)') or '(to be filled)'}\n"
        f"- **Product form**: `{product.get('product_form', '(not specified yet)')}`\n"
        f"- **Architecture direction**: `{answers.get('arch', '(not specified yet)')}`\n"
        f"- **Primary references**: {kickoff.get('references', '(none)') or '(none)'}\n\n"
        "## 2. Primary User or Player Journey\n\n"
        f"{users}\n\n"
        "## 3. Requirement Baseline Snapshot\n\n"
        f"{core_values if core_values else '- No requirement records yet.'}\n\n"
        "## 4. Core Mechanics Snapshot\n\n"
        f"{mechanic_lines if mechanic_lines else '- No core mechanics defined yet.'}\n\n"
        "## 5. Verification Posture\n\n"
        f"- Detailed requirement traceability lives in `{doc_map.get('requirements_matrix', 'requirements matrix')}`.\n"
        f"- Verification modes and evidence gates live in `{doc_map.get('verification_matrix', 'verification matrix')}`.\n"
        f"- Machine-readable contracts live in `{doc_map.get('requirements_contract', 'requirements.json')}`, `{doc_map.get('core_mechanics_contract', 'core_mechanics.json')}`, `{doc_map.get('architecture_contract', 'architecture_contract.json')}`, and `{doc_map.get('verification_contract', 'verification_contract.json')}`.\n"
        "- The system must distinguish implementation progress from verified product behavior.\n"
    )


def _render_architecture_doc(answers: dict,
                             subsystems: list[dict],
                             requirements: list[dict],
                             architecture_contract: dict,
                             coverage_ledger: list[dict],
                             doc_map: dict) -> str:
    today = date.today().isoformat()
    stable = ", ".join(answers.get("stable", [])) if answers.get("stable") else "(to classify)"
    shared = ", ".join(answers.get("shared", [])) if answers.get("shared") else "(to classify)"
    features = ", ".join(answers.get("features", [])) if answers.get("features") else "(to classify)"
    subsystem_lines = ["| Subsystem | Zone | Owned state or artifacts | Allowed outbound | Expected inbound | Linked requirements |",
                       "|---|---|---|---|---|---|"]
    contract_map = {
        entry["path"]: entry
        for entry in architecture_contract.get("subsystems", [])
        if isinstance(entry, dict) and entry.get("path")
    }
    for subsystem in subsystems:
        contract = contract_map.get(subsystem["path"], {})
        owned_summary = "; ".join(
            list(contract.get("owned_state_hints", []))[:1] + list(contract.get("owned_artifact_hints", []))[:1]
        ) or "(define during review)"
        dependency_contract = contract.get("dependency_contract", {})
        linked = ", ".join(contract.get("linked_requirement_ids", [])) or "(assign during review)"
        subsystem_lines.append(
            f"| `{subsystem['path']}` | {subsystem['zone_label']} | {owned_summary} | {', '.join(dependency_contract.get('allowed_outbound_zones', [])) or '(define)'} | {', '.join(dependency_contract.get('expected_inbound_zones', [])) or '(define)'} | {linked} |"
        )
    gaps = _baseline_gap_summary(requirements, coverage_ledger)
    return (
        f"# Design: Architecture And Boundaries for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('design_foundation', 'design baseline')}`, `{doc_map.get('subsystems_index', 'subsystems/INDEX.md')}`\n"
        "> **Purpose**: Turn the brief and kickoff assumptions into explicit subsystem boundaries, ownership, and change-control expectations.\n\n"
        "---\n\n"
        "## 1. Boundary Baseline\n\n"
        f"- **Truth representation**: {answers.get('truth', '(not specified yet)')}\n"
        f"- **View representation**: {answers.get('view', '(not specified yet)')}\n"
        f"- **Stable candidates**: {stable}\n"
        f"- **Shared candidates**: {shared}\n"
        f"- **Feature candidates**: {features}\n\n"
        "## 2. Architectural Rules\n\n"
        "- Keep authoritative truth, presentation/view logic, and integration/platform concerns as separate responsibilities.\n"
        "- Shared code may support multiple features but should not absorb feature-specific policy.\n"
        "- Stable code should not drift in response to temporary slice pressure without an explicit change-control decision.\n"
        "- The design package, requirement baseline, and verification contract all need updates when a stable boundary changes.\n\n"
        "## 3. Operational Subsystem Map\n\n"
        + "\n".join(subsystem_lines)
        + "\n\n## 4. Dependency And Interface Direction\n\n"
        "- `stable` should only depend outward on `stable` responsibilities.\n"
        "- `shared` may depend on `stable` or `shared`, but should not depend on feature-only policy.\n"
        "- `features` may orchestrate `shared` and `stable`, but should not become hidden foundations for more stable zones.\n"
        "- Interfaces that cross subsystem boundaries should be named and reviewable rather than implied through shared mutable state.\n\n"
        "## 5. Open Baseline Risks\n\n"
        f"- **Requirements needing owner resolution**: {', '.join(req['id'] for req in gaps['owner']) or '(none)'}\n"
        f"- **Requirements needing verification refinement**: {', '.join(req['id'] for req in gaps['verification']) or '(none)'}\n"
        f"- **Coverage gaps still unresolved**: {', '.join(entry['source_item_id'] for entry in gaps['coverage']) or '(none)'}\n\n"
        "## 6. Progressive Protection Policy\n\n"
        "- New projects may start with little or no hard locking.\n"
        "- As subsystem contracts stabilize, warn-level protection should be added first.\n"
        "- When a subsystem becomes stable and externally relied upon, linked docs and code zones should move into stronger change control.\n"
        "- Protection should follow maturity, not guesswork.\n"
    )


def _render_core_mechanics_doc(answers: dict, mechanics: list[dict], requirements: list[dict], doc_map: dict) -> str:
    today = date.today().isoformat()
    table_lines = [
        "| ID | Mechanic | Owner | Required for MVP | Maturity | Related requirements |",
        "|---|---|---|---|---|---|",
    ]
    detail_blocks: list[str] = []
    for mechanic in mechanics:
        related = ", ".join(mechanic.get("related_requirement_ids", [])) or "(none)"
        table_lines.append(
            f"| {mechanic['id']} | {mechanic['name']} | `{mechanic['owning_subsystem']}` | {'yes' if mechanic.get('required_for_mvp') else 'no'} | {mechanic.get('maturity_state', 'draft')} | {related} |"
        )
        detail_blocks.append(
            "### "
            + mechanic["id"]
            + f" - {mechanic['name']}\n\n"
            + f"- **Description**: {mechanic['description']}\n"
            + f"- **User value**: {mechanic['player_or_user_value']}\n"
            + f"- **Participating subsystems**: {', '.join(mechanic.get('participating_subsystems', [])) or '(assign during review)'}\n"
            + f"- **Verification mode**: `{mechanic.get('verification_requirements', {}).get('verification_mode', 'unspecified')}`\n"
            + f"- **Required evidence**: {', '.join(mechanic.get('verification_requirements', {}).get('required_evidence', [])) or '(define during review)'}\n"
            + f"- **Change policy**: {mechanic['change_policy']}\n"
            + f"- **Completion criteria**: {'; '.join(mechanic.get('completion_criteria', [])) or '(define during review)'}\n"
        )
    return (
        f"# Design: Core Mechanics for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('requirements_matrix', 'requirements matrix')}`, `{doc_map.get('core_mechanics_contract', 'core_mechanics.json')}`\n"
        "> **Purpose**: Make core mechanics explicit so they can gain maturity, evidence, and protection instead of remaining implicit in feature chatter.\n\n"
        "---\n\n"
        "## 1. Mechanics Table\n\n"
        + "\n".join(table_lines)
        + "\n\n## 2. Lifecycle Rule\n\n"
        "- A mechanic starts as `draft`.\n"
        "- It becomes `designed` once the design and acceptance package describe it explicitly.\n"
        "- It becomes `implemented` only when the behavior exists in the project.\n"
        "- It becomes `verified` only when the declared evidence is attached.\n"
        "- It becomes `stable` only when the team is ready to protect it with change control.\n\n"
        "## 3. Mechanic Details\n\n"
        + ("\n".join(detail_blocks) if detail_blocks else "- No core mechanics defined yet.\n")
    )


def _render_subsystem_doc(subsystem: dict,
                          requirements: list[dict],
                          mechanics: list[dict],
                          architecture_contract: dict) -> str:
    today = date.today().isoformat()
    related_requirements = [req for req in requirements if req.get("owner_subsystem") == subsystem["path"]]
    related_mechanics = [
        mechanic for mechanic in mechanics if subsystem["path"] in mechanic.get("participating_subsystems", [])
        or subsystem["path"] == mechanic.get("owning_subsystem")
    ]
    contract_map = {
        entry["path"]: entry
        for entry in architecture_contract.get("subsystems", [])
        if isinstance(entry, dict) and entry.get("path")
    }
    contract = contract_map.get(subsystem["path"], {})
    dependency_contract = contract.get("dependency_contract", _zone_dependency_contract(str(subsystem.get("zone", "features"))))
    focus_lines = "\n".join(f"- {item}" for item in _subsystem_focus_from_path(subsystem["path"], subsystem["zone"]))
    owned_state_lines = "\n".join(f"- {item}" for item in contract.get("owned_state_hints", [])) or "- Define owned state explicitly during review."
    owned_artifact_lines = "\n".join(f"- {item}" for item in contract.get("owned_artifact_hints", [])) or "- Define owned artifacts explicitly during review."
    interface_lines = "\n".join(f"- {item}" for item in contract.get("interface_expectations", [])) or "- Record explicit interface expectations during review."
    requirement_lines = (
        "\n".join(
            f"- `{req['id']}` - {req['description']} ({req['verification_mode']}, owner confidence: {req.get('owner_confidence', 'unresolved')})"
            for req in related_requirements
        )
        if related_requirements
        else "- No requirement ownership assigned yet."
    )
    mechanic_lines = (
        "\n".join(f"- `{mechanic['id']}` - {mechanic['name']} ({mechanic['maturity_state']})" for mechanic in related_mechanics)
        if related_mechanics
        else "- No core mechanics linked yet."
    )
    return (
        f"# Subsystem Design: {subsystem['path']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Zone**: {subsystem['zone_label']}\n"
        "> **Purpose**: Capture subsystem responsibilities, ownership, invariants, and verification obligations before implementation drifts.\n\n"
        "---\n\n"
        "## 1. Responsibility\n\n"
        f"{focus_lines}\n\n"
        "## 2. Owned State And Artifacts\n\n"
        f"{owned_state_lines}\n"
        f"{owned_artifact_lines}\n\n"
        "## 3. Dependency Contract\n\n"
        f"- **Allowed outbound zones**: {', '.join(dependency_contract.get('allowed_outbound_zones', [])) or '(define during review)'}\n"
        f"- **Expected inbound zones**: {', '.join(dependency_contract.get('expected_inbound_zones', [])) or '(define during review)'}\n"
        f"- **Zone contract note**: {dependency_contract.get('contract_note', '(define during review)')}\n\n"
        "## 4. Interface Contract\n\n"
        f"{interface_lines}\n\n"
        "## 5. Linked Requirements\n\n"
        f"{requirement_lines}\n\n"
        "## 6. Linked Core Mechanics\n\n"
        f"{mechanic_lines}\n\n"
        "## 7. Change Control And Verification Obligations\n\n"
        "- Define at least one reproducible verification path for each owned requirement.\n"
        "- If the subsystem supports perceptual quality, require evidence and, where needed, human review.\n"
        "- If this subsystem becomes stable, update linked docs before mutating it.\n"
    )


def _render_subsystems_index(subsystems: list[dict]) -> str:
    lines = [
        "# Subsystems Index",
        "",
        "## Purpose",
        "",
        "Subsystem-level design documents and ownership boundaries.",
        "",
        "## Active / current documents",
        "",
        "| File | Zone | Purpose |",
        "|---|---|---|",
    ]
    if subsystems:
        for subsystem in subsystems:
            lines.append(
                f"| [{Path(subsystem['doc_label']).name}](./{Path(subsystem['doc_label']).name}) | {subsystem['zone_label']} | Subsystem contract for `{subsystem['path']}`. |"
            )
    else:
        lines.append("| (none yet) | - | No subsystem docs were generated. |")
    lines.extend(
        [
            "",
            "## Archive",
            "",
            "No archived subsystem docs yet.",
            "",
            "## Deprecated",
            "",
            "No deprecated subsystem docs yet.",
        ]
    )
    return "\n".join(lines) + "\n"


def _manual_consultation_doc_records(paths: dict, roles: list[dict], documentation_mode: str) -> list[dict]:
    records: list[dict] = []
    suffix = "_Template.md" if documentation_mode == "managed" else ".md"
    for index, role in enumerate([role for role in roles if role.get("manual_enabled")], start=1):
        slug = _slugify(str(role.get("id", f"role_{index}")))
        filename = f"{index:02d}_MAN_{slug}{suffix}"
        records.append(
            {
                "role": role,
                "path": paths["manual_root"] / filename,
                "label": f"manual-consultation/{filename}",
                "filename": filename,
            }
        )
    return records


def _agent_spec_doc_records(paths: dict, roles: list[dict], documentation_mode: str) -> list[dict]:
    records: list[dict] = []
    suffix = "_Spec.md" if documentation_mode == "managed" else ".md"
    for index, role in enumerate([role for role in roles if role.get("agentic_enabled")], start=1):
        slug = _slugify(str(role.get("id", f"role_{index}")))
        filename = f"{index:02d}_AGT_{slug}{suffix}"
        records.append(
            {
                "role": role,
                "path": paths["agent_root"] / filename,
                "label": f"agent-specs/{filename}",
                "filename": filename,
            }
        )
    return records


def _render_source_assessment_doc(answers: dict, assessment: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    field_lines = [
        "| Field | Status | Preview |",
        "|---|---|---|",
    ]
    for field in assessment.get("field_records", []):
        field_lines.append(
            f"| {field['label']} | {field['status']} | {field['value_preview'] or '(empty)'} |"
        )
    domain_lines = [
        f"- `{record['label']}` ({record['confidence']})"
        + (f" - trigger terms: {', '.join(record['trigger_terms'])}" if record.get("trigger_terms") else "")
        for record in assessment.get("domain_focus", [])
    ] or ["- No domain focus inferred yet."]
    candidates = assessment.get("brief_candidates", [])
    candidate_lines = "\n".join(f"- `{candidate}`" for candidate in candidates) if candidates else "- (none found)"
    return (
        f"# Project Definition: Source Assessment for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `CONTROLCODING.md`\n"
        "> **Purpose**: Assess the quality of the starting material before treating the kickoff as design-ready.\n\n"
        "---\n\n"
        "## 1. Source Intake Summary\n\n"
        f"- **Project definition mode**: `{_project_definition_mode_label(assessment.get('project_definition_mode', 'guided'))}`\n"
        f"- **Source readiness**: `{assessment.get('source_readiness', 'Unknown')}`\n"
        f"- **Primary reference**: {assessment.get('primary_reference', '(none)')}\n"
        f"- **Reference resolution**: `{assessment.get('reference_resolution', 'missing_reference')}`\n"
        f"- **Kickoff source records captured**: {assessment.get('source_record_count', 0)}\n\n"
        "## 2. Brief Candidates Found Locally\n\n"
        f"{candidate_lines}\n\n"
        "## 3. Source Field Coverage\n\n"
        + "\n".join(field_lines)
        + "\n\n## 4. Domain Focus Signals\n\n"
        + "\n".join(domain_lines)
        + "\n\n## 5. Immediate Gaps\n\n"
        + (
            "\n".join(f"- {item}" for item in assessment.get("missing_fields", []))
            if assessment.get("missing_fields")
            else "- No major source-field gaps detected."
        )
        + "\n\n## 6. Assessment Gate\n\n"
        f"- **Approval gate**: `{assessment.get('approval_gate', 'consultation_recommended')}`\n"
        f"- **Recommended next step**: {assessment.get('recommended_next_step', '(define next step)')}\n"
        f"- **Consultation planning doc**: `{doc_map.get('consultation_plan', '(generate consultation plan)')}`\n"
        f"- **Design baseline should not be treated as final until this stage is reviewed**.\n"
    )


def _render_consultation_planning_doc(answers: dict,
                                      assessment: dict,
                                      roles: list[dict],
                                      manual_records: list[dict],
                                      agent_records: list[dict],
                                      doc_map: dict) -> str:
    today = date.today().isoformat()
    planning = _planning_data_from_answers(answers)
    role_lines = [
        "| Role | Manual in Core | Specialist mode | Purpose | Output focus |",
        "|---|---|---|---|---|",
    ]
    manual_map = {record["role"]["id"]: record["label"] for record in manual_records}
    agent_map = {record["role"]["id"]: record["label"] for record in agent_records}
    for role in roles:
        output_focus = "; ".join(role.get("expected_outputs", [])[:2]) or "(define)"
        role_lines.append(
            f"| {role['title']} | {'yes' if role.get('manual_enabled') else 'no'}"
            f" ({manual_map.get(role['id'], '(no packet)')}) | "
            f"{'yes' if role.get('agentic_enabled') else 'no'} ({agent_map.get(role['id'], '(no spec)' )}) | "
            f"{role['purpose']} | {output_focus} |"
        )
    return (
        f"# Project Definition: Consultation Planning for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('source_assessment', 'source assessment')}`\n"
        "> **Purpose**: Decide which external expertise, if any, should refine the project definition before coding starts.\n\n"
        "---\n\n"
        "## 1. Planning Context\n\n"
        f"- **Kickoff tier basis**: `{planning['tier']}`\n"
        f"- **Planning mode**: `{_planning_mode_label(planning['planning_mode'])}`\n"
        f"- **Planning authority**: `{_planning_authority_label(planning['planning_authority'])}`\n"
        f"- **Source assessment gate**: `{assessment.get('approval_gate', 'consultation_recommended')}`\n"
        f"- **Recommended next step**: {assessment.get('recommended_next_step', '(define next step)')}\n\n"
        "## 2. Consultation Rules\n\n"
        "- Manual consultation remains explicit and user-mediated.\n"
        "- Manual packets are design-development aids, not hidden delegation.\n"
        "- Specialist execution, when enabled, must still feed back into the main planning authority as explicit accepted/rejected changes.\n"
        "- No consultation output becomes project truth until the project docs are updated.\n\n"
        "## 3. Recommended Roles\n\n"
        + "\n".join(role_lines)
        + "\n\n## 4. Design Development Rule\n\n"
        "- Do not treat the kickoff package as design-complete just because documents now exist.\n"
        "- Review the source-assessment and consultation outputs first, then freeze the design baseline.\n"
        f"- Manual packet index: `{doc_map.get('manual_index', '(none)')}`\n"
        f"- Agent spec index: `{doc_map.get('agent_index', '(none)')}`\n"
    )


def _render_existing_project_inventory_doc(answers: dict, inventory: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    dir_lines = "\n".join(f"- `{path}/`" for path in inventory.get("candidate_dirs", [])) or "- (none found)"
    file_lines = "\n".join(f"- `{path}`" for path in inventory.get("top_level_files", [])) or "- (none found)"
    tooling_lines = "\n".join(f"- `{path}`" for path in inventory.get("tooling_files", [])) or "- (none found)"
    verification_lines = "\n".join(f"- `{path}`" for path in inventory.get("verification_assets", [])) or "- (none found)"
    doc_lines = "\n".join(f"- `{path}`" for path in inventory.get("document_candidates", [])) or "- (none found)"
    return (
        f"# Project Definition: Existing Project Inventory for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('source_assessment', 'source assessment')}`\n"
        "> **Purpose**: Capture what already exists in the repo before ControlCoding tries to govern it.\n\n"
        "---\n\n"
        "## 1. Visible Repo Areas\n\n"
        f"{dir_lines}\n\n"
        "## 2. Visible Top-Level Files\n\n"
        f"{file_lines}\n\n"
        "## 3. Tooling and Build Signals\n\n"
        f"{tooling_lines}\n\n"
        "## 4. Verification Signals\n\n"
        f"{verification_lines}\n\n"
        "## 5. Document Candidates\n\n"
        f"{doc_lines}\n\n"
        "## 6. Adoption Notes\n\n"
        f"- **Boundary seeds already named**: {inventory.get('boundary_seed_count', 0)}\n"
        f"- **Truth-map follow-up**: `{doc_map.get('truth_map', '(generate document truth map)')}`\n"
        f"- **Architecture extraction follow-up**: `{doc_map.get('architecture_extraction', '(generate architecture extraction)')}`\n"
    )


def _render_document_truth_map_doc(answers: dict, truth_map: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    rows = [
        "| Document | Authority state | Suggested role | Notes |",
        "|---|---|---|---|",
    ]
    for record in truth_map.get("records", []):
        rows.append(
            f"| `{record['path']}` | `{record['authority_state']}` | `{record['suggested_role']}` | {record['notes']} |"
        )
    if len(rows) == 2:
        rows.append("| (none) | - | - | No candidate project docs found locally. |")

    unresolved = "\n".join(f"- {item}" for item in truth_map.get("unresolved", [])) or "- None."
    conflict_lines = "\n".join(f"- {item}" for item in truth_map.get("conflict_notes", [])) or "- No explicit truth-map conflicts recorded."
    explicit_authority = ", ".join(f"`{item}`" for item in truth_map.get("explicit_authority", [])) or "(none confirmed)"
    return (
        f"# Project Definition: Document Truth Map for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('inventory', 'existing project inventory')}`\n"
        "> **Purpose**: Make document authority explicit before ControlCoding adds more governance artifacts.\n\n"
        "---\n\n"
        "## 1. Explicit Authority Set\n\n"
        f"- **Confirmed authoritative documents/files**: {explicit_authority}\n"
        f"- **Recommended next action**: {truth_map.get('recommended_action', '(define authority action)')}\n\n"
        "## 2. Candidate Document Map\n\n"
        + "\n".join(rows)
        + "\n\n## 3. Unresolved Truth Questions\n\n"
        + unresolved
        + "\n\n## 4. Conflict And Drift Signals\n\n"
        + conflict_lines
        + "\n"
    )


def _render_architecture_extraction_doc(answers: dict, architecture_snapshot: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    rows = [
        "| Path | Zone | Signal | Note |",
        "|---|---|---|---|",
    ]
    for record in architecture_snapshot.get("subsystems", []):
        rows.append(
            f"| `{record['path']}` | `{record['zone_label']}` | `{record['signal']}` | {record['note']} |"
        )
    if len(rows) == 2:
        rows.append("| (none) | - | - | No subsystem candidates identified yet. |")

    protection_lines = "\n".join(
        f"- `{entry['path']}` -> `{entry['suggested_level']}` ({entry['reason']})"
        for entry in architecture_snapshot.get("protection_bootstrap", [])
    ) or "- No protection bootstrap candidates yet."
    questions = "\n".join(f"- {item}" for item in architecture_snapshot.get("open_questions", [])) or "- No major open architecture questions recorded."
    return (
        f"# Project Definition: Architecture Extraction for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('inventory', 'existing project inventory')}`, `{doc_map.get('truth_map', 'document truth map')}`\n"
        "> **Purpose**: Derive a first honest architecture baseline from the repo as it exists today.\n\n"
        "---\n\n"
        "## 1. Truth / View Baseline\n\n"
        f"- **Truth representation**: {architecture_snapshot.get('truth', '(not explicitly defined yet)') or '(not explicitly defined yet)'}\n"
        f"- **View representation**: {architecture_snapshot.get('view', '(not explicitly defined yet)') or '(not explicitly defined yet)'}\n\n"
        "## 2. Subsystem And Boundary Candidates\n\n"
        + "\n".join(rows)
        + "\n\n## 3. Protection Bootstrap Candidates\n\n"
        + protection_lines
        + "\n\n## 4. Open Architecture Questions\n\n"
        + questions
        + "\n"
    )


def _render_maturity_gap_assessment_doc(answers: dict, maturity_assessment: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    rows = [
        "| Area | Status | Signals |",
        "|---|---|---|",
    ]
    for area in maturity_assessment.get("areas", []):
        signals = "; ".join(area.get("signals", [])[:4]) or "(none)"
        rows.append(f"| {area['area']} | `{area['status']}` | {signals} |")
    gaps = "\n".join(f"- {item}" for item in maturity_assessment.get("gaps", [])) or "- No major brownfield gaps recorded."
    next_actions = "\n".join(f"- {item}" for item in maturity_assessment.get("next_actions", [])) or "- (define next actions)"
    return (
        f"# Project Definition: Maturity And Gap Assessment for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('truth_map', 'document truth map')}`, `{doc_map.get('architecture_extraction', 'architecture extraction')}`\n"
        "> **Purpose**: Separate what the existing project already knows from what still needs explicit alignment before strong governance.\n\n"
        "---\n\n"
        "## 1. Maturity Snapshot\n\n"
        + "\n".join(rows)
        + "\n\n## 2. Adoption Gate\n\n"
        f"- **Current gate**: `{maturity_assessment.get('adoption_gate', 'adoption_alignment_required')}`\n\n"
        "## 3. Major Gaps\n\n"
        + gaps
        + "\n\n## 4. Next Actions\n\n"
        + next_actions
        + "\n"
    )


def _render_adoption_plan_doc(answers: dict, adoption_plan: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    sections: list[str] = []
    for step in adoption_plan.get("steps", []):
        outputs = "\n".join(f"- {item}" for item in step.get("outputs", [])) or "- (define outputs)"
        sections.append(
            f"## {step['id']}: {step['title']}\n\n"
            f"- **Goal**: {step['goal']}\n"
            f"- **Outputs**:\n{outputs}\n"
        )
    protection_lines = "\n".join(
        f"- `{entry['path']}` -> `{entry['suggested_level']}` ({entry['reason']})"
        for entry in adoption_plan.get("protection_bootstrap", [])
    ) or "- No protection bootstrap candidates yet."
    return (
        f"# Project Definition: Adoption Plan for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('maturity_gap', 'maturity and gap assessment')}`\n"
        "> **Purpose**: Phase ControlCoding into an existing repo without pretending the repo started from zero.\n\n"
        "---\n\n"
        "## 1. Adoption Gate Summary\n\n"
        f"- **Adoption gate**: `{adoption_plan.get('adoption_gate', 'adoption_alignment_required')}`\n"
        f"- **Authority action**: {adoption_plan.get('recommended_authority_action', '(define authority action)')}\n\n"
        "## 2. Protection Bootstrap\n\n"
        f"{protection_lines}\n\n"
        + "\n".join(sections)
        + "\n"
    )


def _render_master_implementation_doc(answers: dict,
                                      subsystems: list[dict],
                                      mechanics: list[dict],
                                      protection_contract: dict,
                                      feature_records: list[dict],
                                      doc_map: dict) -> str:
    today = date.today().isoformat()
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    lifecycle_states = ", ".join(f"`{state}`" for state in protection_contract.get("lifecycle_states", []))
    subsystem_lines = [
        f"- `{policy['path']}` ({policy['zone']}) - starts at `{policy['bootstrap_state']}`, first protection `{policy['first_protection_level']}`, target `{policy['target_protection_level']}`."
        for policy in protection_contract.get("subsystem_policies", [])
    ] or ["- No subsystem protection records generated yet."]
    feature_lines = [
        f"- `{record['mechanic']['id']}` -> `{record['label']}`"
        for record in feature_records
    ] or ["- No feature implementation docs generated yet."]
    brownfield_line = ""
    if str(answers.get("project_definition_mode", "")) == "existing_project":
        brownfield_line = f"- **Adoption plan**: `{doc_map.get('adoption_plan', '(generate adoption plan)')}`\n"
    return (
        f"# Implementation: Master Plan for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('design_foundation', 'design baseline')}`, `{doc_map.get('architecture_doc', 'architecture doc')}`, `{doc_map.get('requirements_matrix', 'requirements traceability')}`\n"
        "> **Purpose**: Turn the approved design baseline into staged implementation work with explicit stabilization and protection milestones.\n\n"
        "---\n\n"
        "## 1. Implementation Package Map\n\n"
        f"- **Master implementation plan**: `{doc_map.get('implementation_master', '(current doc)')}`\n"
        f"- **Progressive protection plan**: `{doc_map.get('implementation_protection', '(generate protection plan)')}`\n"
        f"- **Feature implementation index**: `{doc_map.get('implementation_features_index', '(generate feature index)')}`\n"
        f"- **Protection contract**: `{doc_map.get('protection_contract', 'protection_contract.json')}`\n"
        + brownfield_line
        + "\n## 2. Implementation Lifecycle\n\n"
        f"- **Lifecycle states**: {lifecycle_states or '(define lifecycle states)'}\n"
        "- Work should move through design, implementation, verification, stabilization, and only then protection.\n"
        "- Protection follows maturity. It does not replace design review or verification.\n\n"
        "## 3. Build Order\n\n"
        "1. Freeze the source assessment and consultation outputs as an accepted baseline.\n"
        "2. Confirm architecture ownership and subsystem interfaces.\n"
        "3. Turn must-have capabilities into explicit feature implementation docs.\n"
        "4. Implement the first end-to-end flow with evidence hooks already in place.\n"
        "5. Move the most stable subsystems to warn-level protection only after verification.\n"
        "6. Tighten change control further only when interfaces are explicitly relied upon.\n\n"
        "## 4. Subsystem Workstreams\n\n"
        + "\n".join(subsystem_lines)
        + "\n\n## 5. Feature Implementation Docs\n\n"
        + "\n".join(feature_lines)
        + "\n\n## 6. Definition Of Ready For Coding\n\n"
        f"{_bulletize(kickoff.get('must_haves', ''), 'Replace placeholders with concrete feature implementation docs before coding.')}\n\n"
        "## 7. Protection Promotion Rule\n\n"
        "- Do not promote a path to stronger protection while its owner, interface, or verification evidence is still unresolved.\n"
        "- File-level or block-level protection should only be added after the subsystem-level contract is stable.\n"
    )


def _render_progressive_protection_plan_doc(answers: dict,
                                            protection_contract: dict,
                                            doc_map: dict) -> str:
    today = date.today().isoformat()
    rows = [
        "| Subsystem | Zone | Bootstrap state | First protection | Target protection | Promotion gate |",
        "|---|---|---|---|---|---|",
    ]
    for policy in protection_contract.get("subsystem_policies", []):
        rows.append(
            f"| `{policy['path']}` | `{policy['zone']}` | `{policy['bootstrap_state']}` | `{policy['first_protection_level']}` | `{policy['target_protection_level']}` | {policy['promotion_gate']} |"
        )
    stage_lines = "\n".join(
        f"- `{record['state']}` - {record['rule']}"
        for record in protection_contract.get("stage_policies", [])
    ) or "- No stage policies generated."
    return (
        f"# Implementation: Progressive Protection Plan for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('architecture_doc', 'architecture doc')}`, `{doc_map.get('implementation_master', 'master implementation plan')}`\n"
        "> **Purpose**: Define how ControlCoding protection should increase only when subsystem maturity justifies it.\n\n"
        "---\n\n"
        "## 1. Lifecycle Rules\n\n"
        f"{stage_lines}\n\n"
        "## 2. Subsystem Protection Matrix\n\n"
        + "\n".join(rows)
        + "\n\n## 3. Change-Control Rule\n\n"
        "- If a protected or nearly-stable subsystem changes its owned state, interface contract, or linked evidence obligations, update design, implementation, and verification artifacts in the same pass.\n"
        "- Path-level protection is the default bootstrap. Narrower file or block-level protection comes later, after explicit review.\n"
    )


def _render_feature_implementation_doc(record: dict, protection_contract: dict) -> str:
    today = date.today().isoformat()
    mechanic = record["mechanic"]
    owner = str(mechanic.get("owning_subsystem", "owner_unresolved"))
    policy = next(
        (entry for entry in protection_contract.get("subsystem_policies", []) if entry.get("path") == owner),
        None,
    )
    related_requirements = ", ".join(mechanic.get("related_requirement_ids", [])) or "(none)"
    participating = ", ".join(mechanic.get("participating_subsystems", [])) or "(none)"
    evidence = ", ".join(mechanic.get("verification_requirements", {}).get("required_evidence", [])) or "(define during review)"
    protection_note = (
        f"Owner subsystem `{owner}` starts at `{policy['bootstrap_state']}` and first protection `{policy['first_protection_level']}`."
        if policy
        else "Owner subsystem protection policy is still unresolved."
    )
    return (
        f"# Feature Implementation: {mechanic['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Mechanic ID**: `{mechanic['id']}`\n"
        "> **Purpose**: Define a bounded implementation path for one capability before code starts drifting.\n\n"
        "---\n\n"
        "## 1. Scope\n\n"
        f"- **Owning subsystem**: `{owner}`\n"
        f"- **Participating subsystems**: {participating}\n"
        f"- **Related requirements**: {related_requirements}\n\n"
        "## 2. Implementation Steps\n\n"
        "- Confirm the required interfaces and owned state before editing code.\n"
        "- Implement the smallest end-to-end path that proves the capability exists.\n"
        "- Add or update verification hooks and evidence capture in the same work package.\n"
        "- Stabilize the subsystem contract before broadening scope.\n\n"
        "## 3. Verification And Evidence\n\n"
        f"- **Verification mode**: `{mechanic.get('verification_requirements', {}).get('verification_mode', 'unspecified')}`\n"
        f"- **Required evidence**: {evidence}\n"
        f"- **Completion criteria**: {'; '.join(mechanic.get('completion_criteria', [])) or '(define during review)'}\n\n"
        "## 4. Protection Impact\n\n"
        f"- {protection_note}\n"
        f"- **Change policy**: {mechanic.get('change_policy', '(define change policy)')}\n"
    )


def _render_manual_consultation_packet_doc(answers: dict,
                                           assessment: dict,
                                           role: dict,
                                           doc_map: dict) -> str:
    today = date.today().isoformat()
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    expected_outputs = "\n".join(f"- {item}" for item in role.get("expected_outputs", []))
    prompt_focus = "\n".join(f"- {item}" for item in role.get("prompt_focus", []))
    domain_summary = ", ".join(record["label"] for record in assessment.get("domain_focus", [])[:3]) or "General project domain framing"
    missing_fields = ", ".join(assessment.get("missing_fields", [])[:5]) or "(none)"
    return (
        f"# Manual Consultation Packet: {role['title']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: Template\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('source_assessment', 'source assessment')}`, `{doc_map.get('consultation_plan', 'consultation plan')}`, `CONTROLCODING.md`\n"
        "> **Purpose**: Give the user a ready-to-paste prompt for bounded external consultation in another chat.\n\n"
        "---\n\n"
        "## 1. When To Use This Packet\n\n"
        f"- {role.get('when_to_use', '(define trigger)')}\n"
        "- Use this only to improve the design baseline, not to outsource implementation blindly.\n\n"
        "## 2. Materials To Provide\n\n"
        f"- Project summary from `{doc_map.get('source_assessment', 'source assessment')}`\n"
        f"- Consultation context from `{doc_map.get('consultation_plan', 'consultation plan')}`\n"
        f"- Any confirmed brief or source reference: {assessment.get('primary_reference', '(none)')}\n"
        "- Any existing design draft, notes, or diagrams relevant to this specialist.\n\n"
        "## 3. Expected Output\n\n"
        f"{expected_outputs or '- Define the expected output before use.'}\n\n"
        "## 4. Prompt Focus\n\n"
        f"{prompt_focus or '- Clarify the focus before use.'}\n\n"
        "## 5. Prompt To Paste Into Another Chat\n\n"
        "```text\n"
        f"Act as a {role['title']} helping formalize a project design baseline.\n\n"
        "You are not writing production code. Your job is to improve the design and implementation preparation.\n\n"
        "Project context:\n"
        f"- Project: {answers['name']}\n"
        f"- Source readiness: {assessment.get('source_readiness', 'Unknown')}\n"
        f"- Domain focus: {domain_summary}\n"
        f"- Primary reference: {assessment.get('primary_reference', '(none)')}\n"
        f"- Vision: {kickoff.get('vision', '(not provided)') or '(not provided)'}\n"
        f"- Must-have scope: {kickoff.get('must_haves', '(not provided)') or '(not provided)'}\n"
        f"- Quality constraints: {kickoff.get('quality', '(not provided)') or '(not provided)'}\n"
        f"- Known source gaps: {missing_fields}\n\n"
        "Your task:\n"
        + "\n".join(f"{idx}. {item}" for idx, item in enumerate(role.get("prompt_focus", []), start=1))
        + "\n"
        "Return format:\n"
        "## Recommended decisions\n"
        "## Required design documents or subsections\n"
        "## Open questions that still need confirmation\n"
        "## Risks or false assumptions to avoid\n"
        "## What must be verified before implementation starts\n"
        "```\n"
    )


def _render_agent_spec_doc(answers: dict,
                           assessment: dict,
                           role: dict,
                           doc_map: dict) -> str:
    today = date.today().isoformat()
    capability_lines = "\n".join(f"- {item}" for item in role.get("capability_profile", []))
    expected_outputs = "\n".join(f"- {item}" for item in role.get("expected_outputs", []))
    prompt_focus = "\n".join(f"- {item}" for item in role.get("prompt_focus", []))
    return (
        f"# Agent Spec: {role['title']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{doc_map.get('source_assessment', 'source assessment')}`, `{doc_map.get('consultation_plan', 'consultation plan')}`\n"
        "> **Purpose**: Define the responsibility and output contract for a specialist-assisted planning role.\n\n"
        "---\n\n"
        "## 1. Responsibility\n\n"
        f"- {role.get('purpose', '(define purpose)')}\n"
        f"- {role.get('when_to_use', '(define trigger)')}\n\n"
        "## 2. Required Capability Profile\n\n"
        f"{capability_lines or '- Define the required capability profile.'}\n\n"
        "## 3. Expected Deliverables\n\n"
        f"{expected_outputs or '- Define the expected deliverables.'}\n\n"
        "## 4. Specialist Prompt Focus\n\n"
        f"{prompt_focus or '- Define the specialist prompt focus.'}\n\n"
        "## 5. Execution Notes\n\n"
        "- Assign a concrete backend, model, or local runtime during engagement configuration.\n"
        "- The specialist result remains advisory until merged into the project docs by the main planning authority.\n"
        "- Do not let specialist output silently change project truth without updating design and implementation artifacts.\n"
    )


def _render_project_definition_index(source_label: str,
                                     consultation_label: str,
                                     brownfield_rows: list[tuple[str, str]],
                                     manual_index_label: str,
                                     agent_index_label: str,
                                     has_agent_specs: bool) -> str:
    rows = [
        f"| [{source_label}](./{source_label}) | InProgress | Assess the source material before design generation. |",
        f"| [{consultation_label}](./{consultation_label}) | InProgress | Plan the expertise and consultation path before freezing the design baseline. |",
    ]
    for label, purpose in brownfield_rows:
        rows.append(f"| [{label}](./{label}) | InProgress | {purpose} |")
    rows.append(f"| [{manual_index_label}](./{manual_index_label}) | InProgress | Manual consultation packets for user-mediated external design help. |")
    if has_agent_specs:
        rows.append(f"| [{agent_index_label}](./{agent_index_label}) | InProgress | Specialist role specs for agent-assisted planning paths. |")
    return (
        "# Project Definition Index\n\n"
        "## Purpose\n\n"
        "Source-intake, assessment, and consultation-planning artifacts that exist before the design baseline is trusted.\n\n"
        "## Active / current documents\n\n"
        "| File | Current status | Purpose |\n"
        "|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n## Archive\n\nNo archived project-definition documents yet.\n\n## Deprecated\n\nNo deprecated project-definition documents yet.\n"
    )


def _render_manual_consultation_index(records: list[dict]) -> str:
    rows = [
        "| File | Role | Purpose |",
        "|---|---|---|",
    ]
    if records:
        for record in records:
            role = record["role"]
            rows.append(
                f"| [{record['filename']}](./{record['filename']}) | {role['title']} | {role['purpose']} |"
            )
    else:
        rows.append("| (none) | - | No manual consultation packets were generated. |")
    return (
        "# Manual Consultation Packets\n\n"
        "## Purpose\n\n"
        "Ready-to-paste bounded prompts for external manual consultation in other chats.\n\n"
        "## Active / current documents\n\n"
        + "\n".join(rows)
        + "\n\n## Notes\n\n- These packets are aids for design development, not hidden delegation.\n"
    )


def _render_agent_specs_index(records: list[dict]) -> str:
    rows = [
        "| File | Role | Purpose |",
        "|---|---|---|",
    ]
    if records:
        for record in records:
            role = record["role"]
            rows.append(
                f"| [{record['filename']}](./{record['filename']}) | {role['title']} | {role['purpose']} |"
            )
    else:
        rows.append("| (none) | - | No specialist agent specs were generated. |")
    return (
        "# Agent Specs Index\n\n"
        "## Purpose\n\n"
        "Specialist role specifications for agent-assisted planning paths.\n\n"
        "## Active / current documents\n\n"
        + "\n".join(rows)
        + "\n\n## Notes\n\n- Backend/model assignment happens later during engagement setup.\n"
    )


def _render_kickoff_design_doc(answers: dict, doc_map: dict) -> str:
    """Render the initial design document for a greenfield or early project."""
    kickoff = answers.get("kickoff", {})
    today = date.today().isoformat()
    stable = answers.get("stable", [])
    shared = answers.get("shared", [])
    features = answers.get("features", [])
    planning = _planning_data_from_answers(answers)
    product = answers.get("product", {}) if isinstance(answers.get("product"), dict) else {}
    environment = answers.get("environment", {}) if isinstance(answers.get("environment"), dict) else {}
    target_platforms = product.get("target_platforms", [])
    if not isinstance(target_platforms, list):
        target_platforms = []
    brownfield_map = ""
    if str(answers.get("project_definition_mode", "")) == "existing_project":
        brownfield_map = (
            "## 3. Existing Project Adoption Inputs\n\n"
            f"- **Existing project inventory**: `{doc_map.get('inventory', '(generate inventory)')}`\n"
            f"- **Document truth map**: `{doc_map.get('truth_map', '(generate truth map)')}`\n"
            f"- **Architecture extraction**: `{doc_map.get('architecture_extraction', '(generate architecture extraction)')}`\n"
            f"- **Maturity and gap assessment**: `{doc_map.get('maturity_gap', '(generate maturity assessment)')}`\n"
            f"- **Adoption plan**: `{doc_map.get('adoption_plan', '(generate adoption plan)')}`\n\n"
        )
    return (
        f"# Design: {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: InProgress\n"
        f"> **Date**: {today}\n"
        f"> **Source readiness**: {_kickoff_mode_label(kickoff.get('mode', 'idea'))}\n"
        f"> **Purpose**: Establish the initial design truth before implementation starts.\n\n"
        "---\n\n"
        "## 1. Project Framing\n\n"
        f"- **Project**: `{answers['name']}`\n"
        f"- **Stack**: `{answers['stack']}`\n"
        f"- **Architecture direction**: `{answers['arch']}`\n"
        f"- **Product form**: `{product.get('product_form', '(not specified yet)')}`\n"
        f"- **Runtime / delivery constraints**: {product.get('runtime_constraints', '(not specified yet)') or '(not specified yet)'}\n"
        f"- **Target platforms**: {', '.join(target_platforms) if target_platforms else '(not specified yet)'}\n"
        f"- **Tooling tolerance**: `{product.get('tooling_tolerance', '(not specified yet)')}`\n"
        f"- **Control preference**: `{product.get('control_preference', '(not specified yet)')}`\n"
        f"- **Kickoff tier basis**: `{planning['tier']}`\n"
        f"- **Planning mode**: `{_planning_mode_label(planning['planning_mode'])}`\n"
        f"- **Planning authority**: `{_planning_authority_label(planning['planning_authority'])}`\n"
        f"- **Manual consultation allowed**: {'yes' if planning['manual_consultation_allowed'] else 'no'}\n"
        f"- **Project definition mode**: `{_project_definition_mode_label(str(answers.get('project_definition_mode', 'guided')))}`\n"
        f"- **Vision**: {kickoff.get('vision', '(to be filled)') or '(to be filled)'}\n"
        f"- **References**: {kickoff.get('references', '(none)') or '(none)'}\n\n"
        "## 2. Design Package Map\n\n"
        f"- **Source assessment**: `{doc_map.get('source_assessment', '(generate source assessment)')}`\n"
        f"- **Consultation planning**: `{doc_map.get('consultation_plan', '(generate consultation planning)')}`\n"
        f"- **Foundation doc**: `{doc_map.get('design_foundation', '(current doc)')}`\n"
        f"- **System overview**: `{doc_map.get('system_overview', '(generate system overview)')}`\n"
        f"- **Architecture and boundaries**: `{doc_map.get('architecture_doc', '(generate architecture doc)')}`\n"
        f"- **Core mechanics registry doc**: `{doc_map.get('core_mechanics_doc', '(generate core mechanics doc)')}`\n"
        f"- **Subsystem index**: `{doc_map.get('subsystems_index', '(generate subsystem index)')}`\n"
        f"- **Acceptance checklist**: `{doc_map.get('acceptance_checklist', '(generate acceptance checklist)')}`\n"
        f"- **Requirements traceability**: `{doc_map.get('requirements_matrix', '(generate requirements matrix)')}`\n"
        f"- **Verification matrix**: `{doc_map.get('verification_matrix', '(generate verification matrix)')}`\n"
        f"- **Implementation master plan**: `{doc_map.get('implementation_master', '(generate master implementation plan)')}`\n"
        f"- **Progressive protection plan**: `{doc_map.get('implementation_protection', '(generate protection plan)')}`\n"
        f"- **Feature implementation index**: `{doc_map.get('implementation_features_index', '(generate feature implementation index)')}`\n"
        f"- **Machine-readable contracts**: `{doc_map.get('requirements_contract', 'requirements.json')}`, `{doc_map.get('core_mechanics_contract', 'core_mechanics.json')}`, `{doc_map.get('verification_contract', 'verification_contract.json')}`, `{doc_map.get('protection_contract', 'protection_contract.json')}`\n\n"
        + brownfield_map
        + "## 3. Planning Stance\n\n"
        f"{_render_planning_design_guidance(planning)}\n"
        "## 4. Planning Protocol\n\n"
        f"{_render_planning_design_protocol(planning, answers.get('documentation_mode', 'managed'))}\n"
        "## 5. Environment and Install Policy\n\n"
        f"- **Obvious discovery complete**: {'yes' if environment.get('obvious_discovery_complete') else 'no'}\n"
        f"- **Detected toolchains**: {_format_found_items(environment.get('detected_toolchains', []))}\n"
        f"- **Detected package managers**: {_format_found_items(environment.get('detected_package_managers', []))}\n"
        f"- **Broader inspection permission**: `{environment.get('inspection_permission', 'unknown')}`\n"
        f"- **Install permission**: `{environment.get('install_permission', 'unknown')}`\n"
        f"- **Install style preference**: `{environment.get('install_isolation_preference', 'unspecified')}`\n"
        f"- **Python venv preference**: `{environment.get('venv_preference', 'unspecified')}`\n\n"
        "## 6. Users and Main Workflow\n\n"
        f"{_bulletize(kickoff.get('users', ''), 'Clarify the main users and their primary workflow.')}\n\n"
        "## 7. Must-Have Scope for v1\n\n"
        f"{_bulletize(kickoff.get('must_haves', ''), 'List the must-have v1 features before coding.')}\n\n"
        "## 8. Architecture and Boundaries\n\n"
        f"- **Truth representation**: {answers.get('truth', '(not specified yet)')}\n"
        f"- **View representation**: {answers.get('view', '(not specified yet)')}\n"
        f"- **Stable candidates**: {', '.join(stable) if stable else '(to classify)'}\n"
        f"- **Shared candidates**: {', '.join(shared) if shared else '(to classify)'}\n"
        f"- **Feature candidates**: {', '.join(features) if features else '(to classify)'}\n"
        "- **Rule**: simulation/business truth, rendering/view, and integration boundaries must be made explicit before heavy implementation.\n\n"
        "## 9. Domain Invariants and Failure Conditions\n\n"
        f"{_bulletize(kickoff.get('invariants', ''), 'Define what must always remain true and what must never happen.')}\n\n"
        "## 10. Quality Constraints\n\n"
        f"{_bulletize(kickoff.get('quality', ''), 'Record performance, security, operability, and verification constraints.')}\n\n"
        "## 11. Explicit Non-Goals\n\n"
        f"{_bulletize(kickoff.get('anti_goals', ''), 'List scope that is explicitly out for the first version.')}\n\n"
        "## 12. Initial Open Questions\n\n"
        "- Is the starting brief strong enough to freeze a real design baseline, or do the project-definition artifacts still require outside refinement?\n"
        "- Which boundaries should become DENY or WARN after the first real structure appears?\n"
        "- Which requirements still need a better owner, verification mode, or evidence definition?\n"
        "- Which mechanics are still implicit and need a named lifecycle?\n"
        "- Which invariants deserve dedicated tests first?\n"
        "- Which part of the system is the minimum useful vertical slice?\n"
    )


def _render_kickoff_plan_doc(answers: dict, design_label: str) -> str:
    """Render the initial implementation plan based on kickoff answers."""
    kickoff = answers.get("kickoff", {})
    today = date.today().isoformat()
    must_haves = _bulletize(kickoff.get("must_haves", ""), "Translate the v1 scope into concrete tasks.")
    invariants = _bulletize(kickoff.get("invariants", ""), "Translate core failure conditions into tests.")
    planning = _planning_data_from_answers(answers)
    environment = answers.get("environment", {}) if isinstance(answers.get("environment"), dict) else {}
    planning_gate = (
        "- If a domain or architecture question blocks progress, prepare an explicit manual consultation packet and only integrate the result after user approval.\n"
        if planning["planning_mode"] == "solo_structured_manual_consultation_ready"
        else "- Keep planning decisions in the main chat and record tradeoffs directly in the project docs.\n"
        if planning["planning_mode"] == "solo_structured"
        else "- Run specialist review/planning checkpoints before locking down high-cost architectural decisions.\n"
    )
    return (
        f"# Plan: Initial Implementation Plan for {answers['name']}\n\n"
        f"> **Version**: v1\n"
        f"> **Status**: PLAN\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{design_label}`, `CONTROLCODING.md`, `STATUS.md`, `ROADMAP.md`, acceptance criteria, implementation package\n"
        f"> **Purpose**: Turn the initial project idea into an executable implementation path.\n\n"
        "---\n\n"
        "## 1. Goal\n\n"
        f"Build the first correct and testable version of `{answers['name']}` without skipping design, boundaries, or verification.\n\n"
        "## 2. Package Handoff\n\n"
        "- This plan is the kickoff summary, not the final operational implementation package.\n"
        "- Turn accepted workstreams into the generated master implementation plan and feature implementation docs before large code changes.\n\n"
        "## 3. Planning Basis\n\n"
        f"- **Kickoff tier basis**: `{planning['tier']}`\n"
        f"- **Planning mode**: `{_planning_mode_label(planning['planning_mode'])}`\n"
        f"- **Planning authority**: `{_planning_authority_label(planning['planning_authority'])}`\n"
        f"- **Manual consultation allowed**: {'yes' if planning['manual_consultation_allowed'] else 'no'}\n\n"
        "## 4. Planning Workflow\n\n"
        f"{_render_planning_plan_guidance(planning)}\n"
        "## 5. Planning Decision Control\n\n"
        f"{_render_planning_plan_control(planning, answers.get('documentation_mode', 'managed'))}\n"
        "## 6. Build Order\n\n"
        "1. Review source assessment and consultation planning before freezing the design truth.\n"
        "2. Confirm environment constraints, reuse what is already installed, and avoid installation until permission is explicit.\n"
        "3. Establish the project structure, module boundaries, and asset/loading paths.\n"
        "4. Build the first vertical slice that proves the core workflow works end to end.\n"
        "5. Add invariant-oriented verification and runtime checks.\n"
        "6. Expand only after the vertical slice is stable.\n\n"
        "## 7. Work Packages\n\n"
        "### W1 - Foundation and Boundaries\n\n"
        "- Refine `CONTROLCODING.md` with real architecture rules, boundaries, and invariants.\n"
        f"- Reuse detected local tooling where possible: {_format_found_items(environment.get('detected_toolchains', [])) or '(none detected yet)' }.\n"
        "- Review the generated requirements and core-mechanics registries before treating the kickoff as approved.\n"
        "- Create the initial folder/module structure if it does not exist yet.\n"
        "- Lock down the most stable zones only after the structure is real.\n\n"
        "### W2 - Minimum Useful Vertical Slice\n\n"
        f"{must_haves}\n\n"
        "### W3 - Verification and Evidence\n\n"
        f"{invariants}\n"
        "- Add at least one end-to-end runtime verification path when the project type allows it.\n"
        "- Make sure success is measured by behavior, not compilation alone.\n\n"
        "### W4 - Review and Hardening\n\n"
        "- Re-read the design document against the implementation.\n"
        "- Keep acceptance status split between `implemented` and `verified`; do not collapse them into one checkbox.\n"
        "- Update `ROADMAP.md`, `STATUS.md`, and `BUGS.md` as real work emerges.\n"
        f"{planning_gate}"
        "- Decide what remains explicitly out of scope after the first slice.\n\n"
        "## 8. Definition of Done for the First Slice\n\n"
        "- the design document is no longer only aspirational\n"
        "- the implementation plan has real completed items\n"
        "- one core user flow works end to end\n"
        "- brief requirements have been translated into acceptance criteria with evidence fields\n"
        "- the verification contract distinguishes automated, evidence-based, and human-required checks where needed\n"
        "- completed work is labeled `verified`, not merely `implemented`\n"
        "- invariants or equivalent correctness checks exist\n"
        "- remaining gaps are written down honestly in `ROADMAP.md` or `BUGS.md`\n"
    )


def _render_roadmap_doc(answers: dict, design_label: str, plan_label: str) -> str:
    """Render the initial roadmap for a freshly bootstrapped project."""
    kickoff = answers.get("kickoff", {})
    today = date.today().isoformat()
    return (
        f"# Project Roadmap\n"
        f"> Updated: {today}\n\n"
        "## Current State\n\n"
        f"Project bootstrapped with ControlCoding. Initial engineering preparation exists in `{design_label}` and `{plan_label}`.\n\n"
        "## Active Items\n\n"
        "- [ ] Review and approve the source assessment and consultation-planning docs\n"
        "- [ ] Review and approve the initial design document\n"
        "- [ ] Review the generated design package and split any subsystem that still hides multiple responsibilities\n"
        "- [ ] Refine `CONTROLCODING.md` with real architecture rules and invariants\n"
        "- [ ] Translate the brief into acceptance criteria with explicit verification states\n"
        "- [ ] Review the generated requirements and core-mechanics registries for missing MVP obligations\n"
        "- [ ] Turn the initial implementation plan into real work packages\n"
        "- [ ] Build the first useful vertical slice\n"
        "- [ ] Add verification for the core user flow\n\n"
        "## Suggested Feature Backlog\n\n"
        f"{_bulletize(kickoff.get('must_haves', ''), 'Convert the v1 feature scope into concrete roadmap items.')}\n\n"
        "## Deferred / Explicitly Out of Scope\n\n"
        f"{_bulletize(kickoff.get('anti_goals', ''), 'Record what is intentionally deferred.')}\n"
    )


def _render_bugs_doc() -> str:
    """Render the initial BUGS.md skeleton."""
    today = date.today().isoformat()
    return (
        "# Known Bugs\n"
        f"> Updated: {today}\n\n"
        "No known bugs recorded yet.\n\n"
        "## How to use this file\n\n"
        "- Add a new entry when a real defect is found.\n"
        "- Update the same entry when it is fixed.\n"
        "- Record how the bug was reproduced and how the fix was verified.\n"
    )


def _render_design_index(design_label: str,
                         overview_label: str | None = None,
                         architecture_label: str | None = None,
                         mechanics_label: str | None = None,
                         subsystem_index_label: str | None = None) -> str:
    rows = [
        f"| [{design_label}](./{design_label}) | InProgress | Foundation design truth and kickoff-level project contract. |",
    ]
    if overview_label:
        rows.append(f"| [{overview_label}](./{overview_label}) | InProgress | Product-level system overview between brief and subsystem implementation. |")
    if architecture_label:
        rows.append(f"| [{architecture_label}](./{architecture_label}) | InProgress | Boundary, ownership, and subsystem architecture contract. |")
    if mechanics_label:
        rows.append(f"| [{mechanics_label}](./{mechanics_label}) | InProgress | Core mechanics lifecycle, maturity, and change-control baseline. |")
    if subsystem_index_label:
        rows.append(f"| [{subsystem_index_label}](./{subsystem_index_label}) | InProgress | Subsystem-level design documents and local ownership contracts. |")
    return (
        "# Design Index\n\n"
        "## Purpose\n\n"
        "Active technical design documents for this project.\n\n"
        "## Active / current documents\n\n"
        "| File | Current status | Purpose |\n"
        "|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n"
        "## Archive\n\n"
        "No archived design documents yet.\n\n"
        "## Deprecated\n\n"
        "No deprecated design documents yet.\n"
    )


def _render_plans_index(plan_label: str) -> str:
    return (
        "# Plans Index\n\n"
        "## Purpose\n\n"
        "Execution plans and implementation order for this project.\n\n"
        "## Active / current documents\n\n"
        "| File | Current status | Purpose |\n"
        "|---|---|---|\n"
        f"| [{plan_label}](./{plan_label}) | Plan | Initial implementation path for the project kickoff. |\n\n"
        "## Archive\n\n"
        "No archived plans yet.\n\n"
        "## Deprecated\n\n"
        "No deprecated plans yet.\n"
    )


def _render_acceptance_doc(answers: dict, design_label: str, plan_label: str, doc_map: dict, requirements: list[dict]) -> str:
    kickoff = answers.get("kickoff", {})
    references = kickoff.get("references", "(none)") or "(none)"
    today = date.today().isoformat()
    table_lines = [
        "| ID | Source | Criterion | Status | Formalization | Verification Mode | Baseline flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for requirement in requirements[:18]:
        table_lines.append(
            f"| {requirement['id']} | {requirement['source_type']} | {requirement['description']} | {requirement['status']} | {requirement.get('formalization_state', 'formalization_needed')} | {requirement['verification_mode']} | {', '.join(requirement.get('baseline_flags', [])) or '(none)'} |"
        )

    return (
        f"# Acceptance Criteria - First Slice for {answers['name']}\n\n"
        f"> **Date**: {today}\n"
        f"> **Depends on**: `{design_label}`, `{plan_label}`\n"
        f"> **References**: {references}\n\n"
        "## Status Model\n\n"
        "- Use only `planned`, `implemented`, `self_tested`, `evidence_produced`, `human_verified`, `stable`, or `blocked`.\n"
        "- `implemented` means code exists.\n"
        "- `self_tested` means the team ran the intended check or scenario.\n"
        "- `evidence_produced` means the promised output, log, screenshot, clip, or report now exists.\n"
        "- `human_verified` means a required human review was explicitly completed.\n"
        "- `stable` means the team is ready to protect this as a baseline instead of leaving it fluid.\n"
        "- Do not jump from `planned` to `stable` just because code exists.\n\n"
        "## Baseline Honesty Rules\n\n"
        "- `formalization_needed` means the brief statement still needs one or more atomic pass/fail criteria.\n"
        "- `owner_unresolved` means the framework could not safely assign a subsystem owner yet.\n"
        "- `verification_unresolved` means the framework could not safely define the proof path yet.\n"
        "- If any of those flags remain, the kickoff package is not yet approved even if coding has started.\n\n"
        "## Linked Acceptance Package\n\n"
        f"- **Requirements traceability**: `{doc_map.get('requirements_matrix', '(generate requirements matrix)')}`\n"
        f"- **Verification matrix**: `{doc_map.get('verification_matrix', '(generate verification matrix)')}`\n"
        f"- **Coverage ledger**: `{doc_map.get('coverage_ledger', '(generate coverage ledger)')}`\n"
        f"- **Requirement registry**: `{doc_map.get('requirements_contract', 'requirements.json')}`\n"
        f"- **Core mechanics registry**: `{doc_map.get('core_mechanics_contract', 'core_mechanics.json')}`\n"
        f"- **Verification contract**: `{doc_map.get('verification_contract', 'verification_contract.json')}`\n\n"
        "## How To Use This File\n\n"
        "- Translate brief statements into atomic criteria before claiming slice completion.\n"
        "- Add the concrete verification method for each criterion.\n"
        "- Record the evidence path, command, screenshot, or manual playtest note that proved it.\n"
        "- If a criterion is intentionally deferred, move it to `blocked` and explain why in `ROADMAP.md` or `BUGS.md`.\n\n"
        "## Seed Checklist\n\n"
        + "\n".join(table_lines)
        + "\n"
    )


def _render_requirements_traceability_doc(answers: dict, requirements: list[dict], doc_map: dict) -> str:
    today = date.today().isoformat()
    lines = [
        f"# Requirements Traceability - {answers['name']}",
        "",
        f"> **Date**: {today}",
        f"> **Depends on**: `{doc_map.get('design_foundation', 'design baseline')}`, `{doc_map.get('requirements_contract', 'requirements.json')}`",
        "",
        "## Traceability Matrix",
        "",
        "| ID | Requirement | Priority | Owner subsystem | Owner confidence | Source | Verification | Formalization | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for requirement in requirements:
        lines.append(
            f"| {requirement['id']} | {requirement['description']} | {requirement['priority']} | `{requirement['owner_subsystem']}` | {requirement.get('owner_confidence', 'unresolved')} | {', '.join(requirement.get('source_brief_refs', []))} | {requirement['verification_mode']} | {requirement.get('formalization_state', 'formalization_needed')} | {requirement['status']} |"
        )
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Every `mandatory_mvp` requirement must have a visible owner, verification mode, and evidence path.",
            "- If a requirement remains ownerless or unverifiable, treat it as open risk rather than hidden scope.",
            "- Low-confidence automatic ownership is a review hint, not final architecture truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_coverage_ledger_doc(answers: dict, coverage_ledger: list[dict], doc_map: dict) -> str:
    today = date.today().isoformat()
    lines = [
        f"# Coverage Ledger - {answers['name']}",
        "",
        f"> **Date**: {today}",
        f"> **Depends on**: `{doc_map.get('design_foundation', 'design baseline')}`, `{doc_map.get('requirements_contract', 'requirements.json')}`",
        "",
        "## Coverage Ledger",
        "",
        "| Source ID | Source type | Role | Coverage status | Linked requirements | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for entry in coverage_ledger:
        lines.append(
            f"| {entry['source_item_id']} | {entry['source_type']} | {entry['role']} | {entry['coverage_status']} | {', '.join(entry.get('linked_requirement_ids', [])) or '(none)'} | {entry.get('notes', '')} |"
        )
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Do not silently drop source statements from the brief or kickoff package.",
            "- `context_only` means the statement is preserved, but not yet translated into a requirement.",
            "- `coverage_unresolved` means the kickoff package is incomplete until someone resolves the gap.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_verification_matrix_doc(answers: dict, requirements: list[dict], verification_contract: dict, doc_map: dict) -> str:
    today = date.today().isoformat()
    requirement_contracts = {
        entry["requirement_id"]: entry
        for entry in verification_contract.get("requirements", [])
        if isinstance(entry, dict) and "requirement_id" in entry
    }
    lines = [
        f"# Verification Matrix - {answers['name']}",
        "",
        f"> **Date**: {today}",
        f"> **Depends on**: `{doc_map.get('acceptance_checklist', 'acceptance checklist')}`, `{doc_map.get('verification_contract', 'verification_contract.json')}`",
        "",
        "## Verification Matrix",
        "",
        "| ID | Mode | Resolution | Automated checks | Evidence | Evidence paths | Human review | Completion gate |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for requirement in requirements:
        contract = requirement_contracts.get(requirement["id"], {})
        lines.append(
            f"| {requirement['id']} | {requirement['verification_mode']} | {contract.get('verification_resolution', requirement.get('verification_resolution', 'verification_unresolved'))} | {'; '.join(contract.get('automated_checks', requirement.get('automated_checks', []))) or '(define)'} | {'; '.join(contract.get('required_evidence', requirement.get('required_evidence', []))) or '(define)'} | {'; '.join(contract.get('evidence_paths', requirement.get('evidence_paths', []))) or '(record during execution)'} | {'yes' if contract.get('human_review_required', requirement.get('human_review_required')) else 'no'} | {contract.get('completion_gate', 'Define before implementation')} |"
        )
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Do not collapse `implemented` and `verified` into the same state.",
            "- If a requirement is visual or experiential, pair objective checks with evidence and, when needed, human review.",
            "- If verification remains unresolved, treat the requirement as a planning gap rather than a completed contract.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_criteria_index(criteria_label: str,
                           traceability_label: str | None = None,
                           verification_label: str | None = None,
                           coverage_label: str | None = None) -> str:
    rows = [
        f"| [{criteria_label}](./{criteria_label}) | Checklist | Seed acceptance criteria and verification evidence for the first useful slice. |",
    ]
    if traceability_label:
        rows.append(f"| [{traceability_label}](./{traceability_label}) | Matrix | Requirement traceability from brief to owner, verification, and status. |")
    if verification_label:
        rows.append(f"| [{verification_label}](./{verification_label}) | Matrix | Verification modes, evidence, and completion gates per requirement. |")
    if coverage_label:
        rows.append(f"| [{coverage_label}](./{coverage_label}) | Ledger | Coverage of kickoff source statements, linked requirements, and unresolved gaps. |")
    return (
        "# Criteria Index\n\n"
        "## Purpose\n\n"
        "Acceptance and verification checklists that separate implementation from proof.\n\n"
        "## Active / current documents\n\n"
        "| File | Current status | Purpose |\n"
        "|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n"
        "## Archive\n\n"
        "No archived criteria documents yet.\n\n"
        "## Deprecated\n\n"
        "No deprecated criteria documents yet.\n"
    )


def _render_contracts_index() -> str:
    return (
        "# Contracts Index\n\n"
        "## Purpose\n\n"
        "Machine-readable governance artifacts generated from the kickoff baseline.\n\n"
        "## Active / current documents\n\n"
        "| File | Purpose |\n"
        "|---|---|\n"
        "| [requirements.json](./requirements.json) | Canonical requirement records with ownership, verification mode, and state. |\n"
        "| [core_mechanics.json](./core_mechanics.json) | Core mechanic lifecycle, ownership, and change-control scaffold. |\n"
        "| [architecture_contract.json](./architecture_contract.json) | Subsystem ownership, dependency direction, interface expectations, and unresolved baseline risks. |\n"
        "| [verification_contract.json](./verification_contract.json) | Verification modes, evidence expectations, and completion gates. |\n"
        "| [protection_contract.json](./protection_contract.json) | Progressive protection lifecycle and subsystem-level promotion rules. |\n\n"
        "## Archive\n\n"
        "No archived machine-readable contracts yet.\n\n"
        "## Deprecated\n\n"
        "No deprecated machine-readable contracts yet.\n"
    )


def _render_implementation_index(master_label: str,
                                 protection_label: str,
                                 features_index_label: str) -> str:
    return (
        "# Implementation Index\n\n"
        "## Purpose\n\n"
        "Operational implementation planning artifacts that translate design truth into ordered work and protection milestones.\n\n"
        "## Active / current documents\n\n"
        "| File | Current status | Purpose |\n"
        "|---|---|---|\n"
        f"| [{master_label}](./{master_label}) | InProgress | Master implementation sequencing and stabilization plan. |\n"
        f"| [{protection_label}](./{protection_label}) | InProgress | Progressive protection lifecycle and promotion rules. |\n"
        f"| [{features_index_label}](./{features_index_label}) | InProgress | Feature implementation docs linked to mechanics/capabilities. |\n\n"
        "## Archive\n\n"
        "No archived implementation documents yet.\n\n"
        "## Deprecated\n\n"
        "No deprecated implementation documents yet.\n"
    )


def _render_feature_implementation_index(records: list[dict]) -> str:
    rows = [
        "| File | Mechanic | Owning subsystem | Purpose |",
        "|---|---|---|---|",
    ]
    if records:
        for record in records:
            mechanic = record["mechanic"]
            rows.append(
                f"| [{record['filename']}](./{record['filename']}) | {mechanic['name']} | `{mechanic.get('owning_subsystem', 'owner_unresolved')}` | Bounded implementation plan for `{mechanic['id']}`. |"
            )
    else:
        rows.append("| (none) | - | - | No feature implementation docs were generated yet. |")
    return (
        "# Feature Implementation Index\n\n"
        "## Purpose\n\n"
        "Per-capability implementation docs linked to design, verification, and protection expectations.\n\n"
        "## Active / current documents\n\n"
        + "\n".join(rows)
        + "\n\n## Notes\n\n- Add new feature implementation docs when a capability needs its own bounded delivery plan.\n"
    )


def _render_handoffs_index(packet_rows: list[tuple[str, str]]) -> str:
    if not packet_rows:
        packet_rows = [("(none yet)", "No active handoff packets generated.")]
    rendered_rows = "\n".join(
        f"| [{label}](./{label}) | Template | {purpose} |"
        for label, purpose in packet_rows
    )
    return (
        "# Handoffs Index\n\n"
        "## Purpose\n\n"
        "Bounded handoff or consultation packets that remain explicit and traceable.\n\n"
        "## Active / current documents\n\n"
        "| File | Current status | Purpose |\n"
        "|---|---|---|\n"
        f"{rendered_rows}\n\n"
        "## Archive\n\n"
        "No archived handoff packets yet.\n\n"
        "## Deprecated\n\n"
        "No deprecated handoff packets yet.\n"
    )


def _is_generic_bootstrap_doc(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    normalized = " ".join(text.split())
    if path.name == "ROADMAP.md":
        return "> Updated: (date)" in text or "Project initialized with ControlCoding." in normalized
    if path.name == "BUGS.md":
        return "> Updated: (date)" in text or "No known bugs recorded yet." in normalized
    return False


def _project_has_source_extensions(project: Path, suffixes: set[str]) -> bool:
    skip = {
        ".git", ".controlcoding", ".claude", ".vscode", ".idea", "node_modules", "__pycache__",
        ".pytest_cache", "venv", ".venv", "env", ".env", "dist", "build", "devlog",
    }
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [name for name in dirnames if name not in skip and not name.startswith(".")]
        for filename in filenames:
            if Path(filename).suffix.lower() in suffixes:
                return True
    return False


def _default_layer_rules(boundaries: dict[str, str]) -> list[dict]:
    rules: list[dict] = []
    if "stable" in boundaries:
        forbidden = [zone for zone in ("shared", "features", "workspace") if zone in boundaries]
        if forbidden:
            rules.append({
                "source_zone": "stable",
                "forbidden_target_zones": forbidden,
                "severity": "fail",
                "message": "stable cannot depend on less-stable implementation zones",
            })
    if "shared" in boundaries:
        forbidden = [zone for zone in ("features", "workspace") if zone in boundaries]
        if forbidden:
            rules.append({
                "source_zone": "shared",
                "forbidden_target_zones": forbidden,
                "severity": "fail",
                "message": "shared cannot depend on feature-only or workspace code",
            })
    if "features" in boundaries and "workspace" in boundaries:
        rules.append({
            "source_zone": "features",
            "forbidden_target_zones": ["workspace"],
            "severity": "warn",
            "message": "features should not depend on workspace-only experiments",
        })
    return rules


def _minimal_or_missing_fitness_config(config: dict | None) -> bool:
    if not isinstance(config, dict) or not config:
        return True
    interesting_lists = ("layer_rules", "gateway_rules", "ownership_rules", "mutation_rules")
    if any(config.get(key) for key in interesting_lists):
        return False
    import_patterns = config.get("import_patterns", {})
    if isinstance(import_patterns, dict):
        for key, value in import_patterns.items():
            if key != "python" and str(value).strip():
                return False
    thresholds = config.get("thresholds", {})
    if isinstance(thresholds, dict):
        if "unclassified_warn_pct" in thresholds or "unclassified_fail_pct" in thresholds:
            return False
    return True


def _build_fitness_config_payload(project: Path,
                                  answers: dict,
                                  existing_config: dict | None = None) -> dict | None:
    boundaries = {
        zone: values[0]
        for zone, values in (
            ("stable", answers.get("stable", [])),
            ("shared", answers.get("shared", [])),
            ("features", answers.get("features", [])),
        )
        if isinstance(values, list) and len(values) == 1
    }
    if not boundaries:
        return None

    config = dict(existing_config) if isinstance(existing_config, dict) else {}
    zones = dict(config.get("zones", {})) if isinstance(config.get("zones", {}), dict) else {}
    zones.update(boundaries)
    config["zones"] = zones

    import_patterns = dict(config.get("import_patterns", {})) if isinstance(config.get("import_patterns", {}), dict) else {}
    stack_text = str(answers.get("stack", "")).lower()
    if "c++" in stack_text or "cpp" in stack_text or _project_has_source_extensions(project, {".cpp", ".cc", ".cxx", ".hpp", ".hh"}):
        import_patterns.setdefault("cpp", "auto")
    if re.search(r"(^|[^a-z])c([^a-z+]|$)", stack_text) or _project_has_source_extensions(project, {".c", ".h"}):
        import_patterns.setdefault("c", "auto")
    if import_patterns:
        config["import_patterns"] = import_patterns

    thresholds = dict(config.get("thresholds", {})) if isinstance(config.get("thresholds", {}), dict) else {}
    thresholds.setdefault("unclassified_warn_pct", 20)
    thresholds.setdefault("unclassified_fail_pct", 45)
    config["thresholds"] = thresholds

    if not isinstance(config.get("layer_rules"), list) or not config.get("layer_rules"):
        config["layer_rules"] = _default_layer_rules(boundaries)

    config.setdefault(
        "skip_dirs",
        [
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "dist", "build", "target", "out", "bin", "obj",
            ".controlcoding", ".claude", ".bridge", "devlog", ".tox", ".mypy_cache",
        ],
    )
    return config


def _scaffold_fitness_config(project: Path, answers: dict, force_refresh: bool = False) -> tuple[bool, str]:
    """Create or upgrade a starter fitness.json when the current one is weak."""
    config_path = project / "fitness.json"
    existing = _load_json_object(config_path)
    if config_path.exists() and not force_refresh and not _minimal_or_missing_fitness_config(existing):
        return False, "fitness.json"

    payload = _build_fitness_config_payload(project, answers, existing)
    if payload is None:
        return False, "fitness.json"

    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True, "fitness.json"


def _write_kickoff_file(project: Path, path: Path, content: str, overwrite_existing: bool = False) -> tuple[bool, str]:
    """Write a kickoff scaffold file and return (written, relative_label)."""
    rel = str(path.relative_to(project)).replace("\\", "/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite_existing:
        return False, rel
    path.write_text(content, encoding="utf-8")
    return True, rel


def _scaffold_kickoff_docs(project: Path, answers: dict) -> list[str]:
    """Create the initial engineering-preparation documents."""
    kickoff = answers.get("kickoff", {})
    if kickoff.get("mode") == "skip":
        return []

    paths = _kickoff_paths(project, answers.get("documentation_mode", "managed"))
    project_definition = _project_definition_paths(project, answers.get("documentation_mode", "managed"))
    criteria_paths = _criteria_paths(project, answers.get("documentation_mode", "managed"))
    design_package = _design_package_paths(project, answers.get("documentation_mode", "managed"))
    implementation_package = _implementation_package_paths(project, answers.get("documentation_mode", "managed"))
    acceptance_package = _acceptance_package_paths(project, answers.get("documentation_mode", "managed"))
    contracts_package = _contracts_paths(project, answers.get("documentation_mode", "managed"))
    subsystems = _subsystem_doc_records(project, answers, answers.get("documentation_mode", "managed"))
    requirements = _build_requirement_records(answers)
    mechanics = _build_core_mechanics_records(answers, requirements)
    verification_contract = _build_verification_contract(answers, requirements, mechanics)
    coverage_ledger = _build_coverage_ledger(answers, requirements)
    architecture_contract = _build_architecture_contract(subsystems, requirements, coverage_ledger)
    protection_contract = _build_progressive_protection_contract(subsystems, requirements, mechanics)
    planning = _planning_data_from_answers(answers)
    source_assessment = _build_source_assessment(project, answers)
    consultation_roles = _build_consultation_roles(answers, source_assessment)
    existing_project_mode = str(answers.get("project_definition_mode", "")) == "existing_project"
    inventory = _build_existing_project_inventory(project, answers) if existing_project_mode else {}
    truth_map = _build_document_truth_map(answers, inventory) if existing_project_mode else {}
    architecture_snapshot = (
        _build_existing_project_architecture_extraction(answers, inventory, subsystems)
        if existing_project_mode
        else {}
    )
    maturity_assessment = (
        _build_existing_project_maturity_assessment(source_assessment, inventory, truth_map, architecture_snapshot)
        if existing_project_mode
        else {}
    )
    adoption_plan = (
        _build_existing_project_adoption_plan(
            answers,
            source_assessment,
            truth_map,
            architecture_snapshot,
            maturity_assessment,
        )
        if existing_project_mode
        else {}
    )
    implementation_feature_records = _implementation_feature_doc_records(
        implementation_package,
        mechanics,
        answers.get("documentation_mode", "managed"),
    )
    manual_records = _manual_consultation_doc_records(project_definition, consultation_roles, answers.get("documentation_mode", "managed"))
    agent_records = _agent_spec_doc_records(project_definition, consultation_roles, answers.get("documentation_mode", "managed"))
    doc_map = {
        "project_definition_index": _project_relative_label(project, project_definition["index"]),
        "source_assessment": _project_relative_label(project, project_definition["source_assessment"]),
        "consultation_plan": _project_relative_label(project, project_definition["consultation_plan"]),
        "inventory": _project_relative_label(project, project_definition["inventory"]),
        "truth_map": _project_relative_label(project, project_definition["truth_map"]),
        "architecture_extraction": _project_relative_label(project, project_definition["architecture_extraction"]),
        "maturity_gap": _project_relative_label(project, project_definition["maturity_gap"]),
        "adoption_plan": _project_relative_label(project, project_definition["adoption_plan"]),
        "manual_index": _project_relative_label(project, project_definition["manual_index"]),
        "agent_index": _project_relative_label(project, project_definition["agent_index"]),
        "design_foundation": _project_relative_label(project, paths["design"]),
        "design_index": _project_relative_label(project, design_package["index"]),
        "system_overview": _project_relative_label(project, design_package["overview"]),
        "architecture_doc": _project_relative_label(project, design_package["architecture"]),
        "core_mechanics_doc": _project_relative_label(project, design_package["mechanics"]),
        "subsystems_index": _project_relative_label(project, design_package["subsystems_index"]),
        "implementation_index": _project_relative_label(project, implementation_package["index"]),
        "implementation_master": _project_relative_label(project, implementation_package["master"]),
        "implementation_protection": _project_relative_label(project, implementation_package["protection"]),
        "implementation_features_index": _project_relative_label(project, implementation_package["features_index"]),
        "acceptance_checklist": _project_relative_label(project, criteria_paths["criteria"]),
        "requirements_matrix": _project_relative_label(project, acceptance_package["traceability"]),
        "verification_matrix": _project_relative_label(project, acceptance_package["verification"]),
        "coverage_ledger": _project_relative_label(project, acceptance_package["coverage"]),
        "requirements_contract": _project_relative_label(project, contracts_package["requirements"]),
        "core_mechanics_contract": _project_relative_label(project, contracts_package["mechanics"]),
        "architecture_contract": _project_relative_label(project, contracts_package["architecture"]),
        "verification_contract": _project_relative_label(project, contracts_package["verification"]),
        "protection_contract": _project_relative_label(project, contracts_package["protection"]),
    }
    design_content = _render_kickoff_design_doc(answers, doc_map)
    plan_content = _render_kickoff_plan_doc(answers, paths["design_label"])
    roadmap_content = _render_roadmap_doc(answers, paths["design_label"], paths["plan_label"])
    bugs_content = _render_bugs_doc()
    acceptance_content = _render_acceptance_doc(
        answers,
        paths["design_label"],
        paths["plan_label"],
        doc_map,
        requirements,
    )
    system_overview_content = _render_system_overview_doc(answers, requirements, mechanics, doc_map)
    architecture_content = _render_architecture_doc(
        answers,
        subsystems,
        requirements,
        architecture_contract,
        coverage_ledger,
        doc_map,
    )
    mechanics_content = _render_core_mechanics_doc(answers, mechanics, requirements, doc_map)
    requirements_traceability_content = _render_requirements_traceability_doc(answers, requirements, doc_map)
    verification_matrix_content = _render_verification_matrix_doc(answers, requirements, verification_contract, doc_map)
    coverage_ledger_content = _render_coverage_ledger_doc(answers, coverage_ledger, doc_map)
    master_implementation_content = _render_master_implementation_doc(
        answers,
        subsystems,
        mechanics,
        protection_contract,
        implementation_feature_records,
        doc_map,
    )
    protection_plan_content = _render_progressive_protection_plan_doc(
        answers,
        protection_contract,
        doc_map,
    )
    source_assessment_content = _render_source_assessment_doc(answers, source_assessment, doc_map)
    consultation_plan_content = _render_consultation_planning_doc(
        answers,
        source_assessment,
        consultation_roles,
        manual_records,
        agent_records,
        doc_map,
    )
    inventory_content = _render_existing_project_inventory_doc(answers, inventory, doc_map) if existing_project_mode else ""
    truth_map_content = _render_document_truth_map_doc(answers, truth_map, doc_map) if existing_project_mode else ""
    architecture_extraction_content = (
        _render_architecture_extraction_doc(answers, architecture_snapshot, doc_map)
        if existing_project_mode
        else ""
    )
    maturity_gap_content = (
        _render_maturity_gap_assessment_doc(answers, maturity_assessment, doc_map)
        if existing_project_mode
        else ""
    )
    adoption_plan_content = _render_adoption_plan_doc(answers, adoption_plan, doc_map) if existing_project_mode else ""

    written_labels = []
    for path, content in (
        (project_definition["source_assessment"], source_assessment_content),
        (project_definition["consultation_plan"], consultation_plan_content),
        (paths["design"], design_content),
        (paths["plan"], plan_content),
        (project / "ROADMAP.md", roadmap_content),
        (project / "BUGS.md", bugs_content),
        (criteria_paths["criteria"], acceptance_content),
        (design_package["overview"], system_overview_content),
        (design_package["architecture"], architecture_content),
        (design_package["mechanics"], mechanics_content),
        (implementation_package["master"], master_implementation_content),
        (implementation_package["protection"], protection_plan_content),
        (acceptance_package["traceability"], requirements_traceability_content),
        (acceptance_package["verification"], verification_matrix_content),
        (acceptance_package["coverage"], coverage_ledger_content),
        (contracts_package["requirements"], json.dumps(requirements, indent=2)),
        (contracts_package["mechanics"], json.dumps(mechanics, indent=2)),
        (contracts_package["architecture"], json.dumps(architecture_contract, indent=2)),
        (contracts_package["verification"], json.dumps(verification_contract, indent=2)),
        (contracts_package["protection"], json.dumps(protection_contract, indent=2)),
    ):
        wrote, label = _write_kickoff_file(
            project,
            path,
            content,
            overwrite_existing=_is_generic_bootstrap_doc(path),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    if existing_project_mode:
        for path, content in (
            (project_definition["inventory"], inventory_content),
            (project_definition["truth_map"], truth_map_content),
            (project_definition["architecture_extraction"], architecture_extraction_content),
            (project_definition["maturity_gap"], maturity_gap_content),
            (project_definition["adoption_plan"], adoption_plan_content),
        ):
            wrote, label = _write_kickoff_file(
                project,
                path,
                content,
                overwrite_existing=_is_generic_bootstrap_doc(path),
            )
            if wrote:
                ok(f"Created {label}")
                written_labels.append(label)
            else:
                info(f"Keeping existing {label}")

    for subsystem in subsystems:
        wrote, label = _write_kickoff_file(
            project,
            subsystem["doc_path"],
            _render_subsystem_doc(subsystem, requirements, mechanics, architecture_contract),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    for record in implementation_feature_records:
        wrote, label = _write_kickoff_file(
            project,
            record["path"],
            _render_feature_implementation_doc(record, protection_contract),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        project_definition["index"],
        _render_project_definition_index(
            project_definition["source_assessment_label"],
            project_definition["consultation_plan_label"],
            [
                (project_definition["inventory_label"], "Inventory the current repo before governance is layered on top.")
                ,
                (project_definition["truth_map_label"], "Map current document authority, stale docs, and supporting references.")
                ,
                (project_definition["architecture_extraction_label"], "Extract boundary and ownership candidates from the existing repo.")
                ,
                (project_definition["maturity_gap_label"], "Assess maturity, drift, and brownfield adoption gaps.")
                ,
                (project_definition["adoption_plan_label"], "Phase ControlCoding into the existing repo without pretending it started greenfield.")
                ,
            ] if existing_project_mode else [],
            project_definition["manual_index_label"],
            project_definition["agent_index_label"],
            bool(agent_records),
        ),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        project_definition["manual_index"],
        _render_manual_consultation_index(manual_records),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        project_definition["agent_index"],
        _render_agent_specs_index(agent_records),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    for record in manual_records:
        wrote, label = _write_kickoff_file(
            project,
            record["path"],
            _render_manual_consultation_packet_doc(
                answers,
                source_assessment,
                record["role"],
                doc_map,
            ),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    for record in agent_records:
        wrote, label = _write_kickoff_file(
            project,
            record["path"],
            _render_agent_spec_doc(
                answers,
                source_assessment,
                record["role"],
                doc_map,
            ),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    if paths["plans_index"] is not None:
        wrote, label = _write_kickoff_file(
            project,
            paths["plans_index"],
            _render_plans_index(paths["plan_label"]),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    design_index_label = paths["design_label"] if answers.get("documentation_mode", "managed") == "managed" else "../DESIGN.md"
    wrote, label = _write_kickoff_file(
        project,
        design_package["index"],
        _render_design_index(
            design_index_label,
            design_package["overview_label"],
            design_package["architecture_label"],
            design_package["mechanics_label"],
            design_package["subsystems_index_label"],
        ),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        design_package["subsystems_index"],
        _render_subsystems_index(subsystems),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        implementation_package["index"],
        _render_implementation_index(
            implementation_package["master_label"],
            implementation_package["protection_label"],
            implementation_package["features_index_label"],
        ),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        implementation_package["features_index"],
        _render_feature_implementation_index(implementation_feature_records),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    criteria_index_label = criteria_paths["criteria_label"] if answers.get("documentation_mode", "managed") == "managed" else "../ACCEPTANCE_CRITERIA.md"
    wrote, label = _write_kickoff_file(
        project,
        acceptance_package["index"],
        _render_criteria_index(
            criteria_index_label,
            acceptance_package["traceability_label"],
            acceptance_package["verification_label"],
            acceptance_package["coverage_label"],
        ),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    wrote, label = _write_kickoff_file(
        project,
        contracts_package["index"],
        _render_contracts_index(),
    )
    if wrote:
        ok(f"Created {label}")
        written_labels.append(label)
    else:
        info(f"Keeping existing {label}")

    handoff_rows: list[tuple[str, str]] = []
    if planning.get("tier") == "core" or planning.get("manual_consultation_allowed"):
        if answers.get("documentation_mode", "managed") == "managed":
            handoff_path = project / "dev" / "handoffs" / "01_HOF_PlanningConsultationPacket_Template.md"
            handoff_index_path = project / "dev" / "handoffs" / "INDEX.md"
            packet_label = "01_HOF_PlanningConsultationPacket_Template.md"
            wrote, label = _write_kickoff_file(
                project,
                handoff_path,
                _render_manual_consultation_packet(
                    answers,
                    paths["design_label"],
                    paths["plan_label"],
                    requirements,
                    coverage_ledger,
                ),
            )
            if wrote:
                ok(f"Created {label}")
                written_labels.append(label)
            else:
                info(f"Keeping existing {label}")
            handoff_rows.append((packet_label, "Use only when Core planning needs one bounded external consultation grounded in the current kickoff gaps."))
        else:
            wrote, label = _write_kickoff_file(
                project,
                project / "PLANNING_CONSULTATION_PACKET.md",
                _render_manual_consultation_packet(
                    answers,
                    paths["design_label"],
                    paths["plan_label"],
                    requirements,
                    coverage_ledger,
                ),
            )
            if wrote:
                ok(f"Created {label}")
                written_labels.append(label)
            else:
                info(f"Keeping existing {label}")

    if planning.get("planning_mode") == "orchestrated_specialists":
        if answers.get("documentation_mode", "managed") == "managed":
            specialist_path = project / "dev" / "handoffs" / "02_HOF_SpecialistPlanningRefinementPacket_Template.md"
            handoff_index_path = project / "dev" / "handoffs" / "INDEX.md"
            specialist_label = "02_HOF_SpecialistPlanningRefinementPacket_Template.md"
            wrote, label = _write_kickoff_file(
                project,
                specialist_path,
                _render_specialist_refinement_packet(
                    answers,
                    paths["design_label"],
                    paths["plan_label"],
                    requirements,
                    coverage_ledger,
                ),
            )
            if wrote:
                ok(f"Created {label}")
                written_labels.append(label)
            else:
                info(f"Keeping existing {label}")
            handoff_rows.append((specialist_label, "Use when specialist-assisted planning needs explicit prompts for architecture, verification, or domain refinement."))
        else:
            wrote, label = _write_kickoff_file(
                project,
                project / "SPECIALIST_PLANNING_REFINEMENT_PACKET.md",
                _render_specialist_refinement_packet(
                    answers,
                    paths["design_label"],
                    paths["plan_label"],
                    requirements,
                    coverage_ledger,
                ),
            )
            if wrote:
                ok(f"Created {label}")
                written_labels.append(label)
            else:
                info(f"Keeping existing {label}")

    if handoff_rows and answers.get("documentation_mode", "managed") == "managed":
        handoff_index_path = project / "dev" / "handoffs" / "INDEX.md"
        wrote, label = _write_kickoff_file(
            project,
            handoff_index_path,
            _render_handoffs_index(handoff_rows),
        )
        if wrote:
            ok(f"Created {label}")
            written_labels.append(label)
        else:
            info(f"Keeping existing {label}")

    return written_labels


def _render_context_content(answers: dict, file_label: str) -> str | None:
    """Render the canonical context document text for the requested file label."""
    kickoff = answers.get("kickoff", {})
    if not isinstance(kickoff, dict):
        kickoff = {}
    stable = answers.get("stable", [])
    shared = answers.get("shared", [])
    features = answers.get("features", [])

    stable_lines = "\n".join(f"- `{d}/` - Stable zone" for d in stable) if stable else "- (none defined yet)"
    shared_lines = "\n".join(f"- `{d}/` - Shared module" for d in shared) if shared else "- (none defined yet)"
    feature_lines = "\n".join(f"- `{d}/` - Active development" for d in features) if features else "- (none defined yet)"

    # Read template and fill in values
    template = TEMPLATES_DIR / "CLAUDE.md.template"
    if not template.exists():
        return None

    content = template.read_text(encoding="utf-8")
    if file_label != LEGACY_CONTEXT_FILENAME:
        content = content.replace(LEGACY_CONTEXT_FILENAME, file_label)

    # Behavioral rules
    behavioral_rules = answers.get("behavioral_rules", [])
    if behavioral_rules:
        operative_text = "\n".join(f"- {r}" for r in behavioral_rules)
    else:
        operative_text = "- (none added yet)"

    arch_rules = (
        "1. Keep the authoritative project truth separate from rendering, presentation, or adapter code.\n"
        "2. Keep module boundaries explicit; cross-module behavior should flow through named interfaces, data files, or dedicated integration points.\n"
        "3. Prefer externalized configuration/content data and small focused modules over hardcoded cross-cutting behavior."
    )
    domain_invariants = _numbered_text(
        kickoff.get("invariants", ""),
        [
            "The main user flow must not fail immediately with a crash, black screen, or equivalent fatal startup failure.",
            "Authoritative state must remain internally consistent across the core workflow.",
            "Critical inputs, content, or configuration data must be validated before use.",
        ],
    )
    current_focus = (
        "- [ ] Review the kickoff design and implementation plan for coherence\n"
        "- [ ] Replace provisional architecture assumptions with project-specific decisions\n"
        "- [ ] Start the first useful vertical slice with verification in mind"
    )
    documentation_policy = _render_documentation_policy(
        answers.get("documentation_mode", "managed"),
        answers.get("cc_artifact_mode", "local_only"),
    )

    # Use named markers (new template format)
    marker_replacements = {
        "<!-- CC:PROJECT_NAME -->": answers["name"],
        "<!-- CC:PROJECT_STACK -->": answers["stack"],
        "<!-- CC:PROJECT_ARCH -->": answers["arch"],
        "<!-- CC:TRUTH_REPR -->": answers.get("truth", "(not specified)"),
        "<!-- CC:VIEW_REPR -->": answers.get("view", "(not specified)"),
        "<!-- CC:DOCUMENTATION_POLICY -->": documentation_policy,
        "<!-- CC:ARCH_RULES -->": arch_rules,
        "<!-- CC:STABLE_ZONES -->": stable_lines,
        "<!-- CC:SHARED_ZONES -->": shared_lines,
        "<!-- CC:FEATURE_ZONES -->": feature_lines,
        "<!-- CC:WORKSPACE_ZONES -->": "- (none defined yet)",
        "<!-- CC:DOMAIN_INVARIANTS -->": domain_invariants,
        "<!-- CC:OPERATIVE_RULES -->": operative_text,
        "<!-- CC:CURRENT_FOCUS -->": current_focus,
    }

    has_markers = "<!-- CC:PROJECT_NAME -->" in content
    if has_markers:
        for marker, value in marker_replacements.items():
            content = content.replace(marker, value)
    else:
        # Legacy template without markers - fall back to literal matching
        print("  [warn] Template uses old placeholder format. Consider updating to marker-based template.")
        content = content.replace("[PROJECT_NAME]", answers["name"])
        content = content.replace("[LANGUAGE / FRAMEWORK / KEY LIBRARIES]", answers["stack"])
        content = content.replace(
            "[BRIEF ARCHITECTURE DESCRIPTION -e.g., \"Modular plugin system with shared core\"]",
            answers["arch"])
        content = content.replace(
            "[WHICH DATA FORMAT IS AUTHORITATIVE -e.g., \"Spherical coordinates\"]",
            answers.get("truth", "(not specified)"))
        content = content.replace(
            "[WHICH DATA FORMAT IS DERIVED -e.g., \"Cartesian coordinates for rendering\"]",
            answers.get("view", "(not specified)"))
        content = content.replace(
            "- `[PATH_1]/` -[DESCRIPTION -e.g., \"Core data models and interfaces\"]\n"
            "- `[PATH_2]/` -[DESCRIPTION -e.g., \"Authentication and authorization\"]",
            stable_lines)
        content = content.replace(
            "- `[PATH_3]/` -[DESCRIPTION -e.g., \"Utility functions used across modules\"]",
            shared_lines)
        content = content.replace(
            "- `[PATH_4]/` -[DESCRIPTION -e.g., \"New dashboard module\"]",
            feature_lines)
        content = content.replace(
            "- `[PATH_5]/` -[DESCRIPTION -e.g., \"Prototype for new data pipeline\"]",
            "- (none defined yet)")
        content = content.replace("[RULE_1 -e.g., \"All state mutations go through the Store\"]",
                                  "(define your architecture rules)")
        content = content.replace("[RULE_2 -e.g., \"No direct database access outside repository classes\"]",
                                  "(add more rules as needed)")
        content = content.replace("[RULE_3 -e.g., \"All public API responses must include a schema version\"]",
                                  "(add more rules as needed)")
        content = content.replace("[INVARIANT_1]", "INVARIANT_1")
        content = content.replace("[DESCRIPTION] -Test: `[TEST_COMMAND_OR_FILE]`",
                                  "(define your domain invariants)")
        content = content.replace("[INVARIANT_2]", "INVARIANT_2")
        content = content.replace("[INVARIANT_3]", "INVARIANT_3")
        content = content.replace("[CURRENT_TASK_1]", "(your current task)")
        content = content.replace("[CURRENT_TASK_2]", "(next task)")
        if behavioral_rules:
            rules_text = "\n".join(f"- {r}" for r in behavioral_rules)
            content = content.replace("- [ADD YOUR PROJECT-SPECIFIC RULES HERE]", rules_text)
        else:
            content = content.replace("- [ADD YOUR PROJECT-SPECIFIC RULES HERE]",
                                      "- (add your project-specific rules here)")

    return content


def _generate_context_source(project: Path, answers: dict) -> bool:
    """Generate the canonical ControlCoding context source from user answers."""
    content = _render_context_content(answers, CANONICAL_CONTEXT_FILENAME)
    if content is None:
        return False
    context_source = project / CANONICAL_CONTEXT_FILENAME
    context_source.write_text(content, encoding="utf-8")
    return True


def _generate_claude_md(project: Path, answers: dict):
    """Generate the canonical context source and a derived CLAUDE.md."""
    if not _generate_context_source(project, answers):
        return False

    plan = _adapter_plan_payload(
        project,
        host="claude_code",
        operation="setup",
    )
    _emit_adapter_preview(plan)
    if not plan["preflightOk"]:
        return False
    result = _apply_adapter_transaction(project, plan["entries"])
    return bool(result.get("ok"))


def _ask_user_host(default: str = "claude_code", apply_answers: bool = False) -> str:
    """Ask the user which host/IDE is the primary working surface."""
    print("  I need to know the primary coding/chat host, not just the editor shell around it.")
    print("  The recommendation should follow the actual AI coding surface because that drives context export and protection behavior.")
    print("  Which AI host will the user actually work through?")
    print("  Pick the primary coding/chat host, not just the editor shell around it.")
    print("  Example: if you use Codex inside VS Code, choose Codex CLI, not VS Code.")
    print("  [1] Claude Code")
    print("  [2] Codex CLI / OpenAI coding host")
    print("  [3] Cursor")
    print("  [4] Windsurf")
    print("  [5] VS Code (generic editor/manual workflow)")
    print("  [6] Cline")
    print("  [7] Gemini CLI / official Gemini host")
    print("  [8] Other / custom host")
    mapping = {
        "1": "claude_code",
        "2": "codex_cli",
        "3": "cursor",
        "4": "windsurf",
        "5": "vscode",
        "6": "cline",
        "7": "gemini_cli",
        "8": "other",
    }
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in _GATEWAY_VALID_USER_HOSTS else "other")


def _ask_host_instruction_mode(default: str = "recommended", apply_answers: bool = False) -> str:
    print("  I recommend `recommended` for most projects.")
    print("  `preset_only` keeps only the baseline host assets. `custom` is an advanced override for extra local notes.")
    print("  How much host-specific workflow guidance should ControlCoding generate?")
    print("  [1] Preset only")
    print("  [2] Recommended guidance")
    print("  [3] Recommended guidance + my own extra local notes (advanced)")
    mapping = {
        "1": "preset_only",
        "2": "recommended",
        "3": "custom",
    }
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "2"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "recommended")


def _ask_host_custom_notes() -> list[str]:
    print("  Add custom host workflow notes. Press Enter immediately to keep recommended guidance without extra notes.")
    notes = []
    while True:
        note = _ask("Custom note", "")
        if not note:
            break
        notes.append(note)
    return notes


def _sync_host_context_file(project: Path, user_host: str, force_refresh: bool = False) -> str | None:
    """Sync a host adapter while preserving the legacy ``force_refresh`` argument.

    ``force_refresh`` is retained for caller compatibility only. It intentionally
    cannot bypass adapter preview, preflight, or ownership checks, and it is not
    routed into force, replacement, or adoption semantics.
    """
    # Keep the compatibility parameter inert at the ownership boundary.
    spec = _host_context_export_spec(user_host)
    if spec is None:
        info(f"No derived host-context file is defined for {user_host}")
        return None

    plan = _adapter_plan_payload(
        project,
        host=user_host,
        operation="setup",
    )
    _emit_adapter_preview(plan)
    if not plan["preflightOk"]:
        warn(
            "Host-native context sync was blocked by adapter ownership preflight"
        )
        return None
    result = _apply_adapter_transaction(project, plan["entries"])
    if not result.get("ok"):
        warn(f"Host-native context sync failed: {result.get('error', 'unknown error')}")
        return None

    ok(
        f"Synced {spec['file_label']} for {spec['host_label']} "
        f"from {plan['sourcePath']}"
    )
    return spec["relative_path"]


def _sync_host_context_file_compat(project: Path, user_host: str, force_refresh: bool = False) -> str | None:
    try:
        return _sync_host_context_file(project, user_host, force_refresh=force_refresh)
    except TypeError:
        return _sync_host_context_file(project, user_host)


def _ask_backend_preference(available: list[str], default: str, apply_answers: bool = False) -> str | None:
    print("  This backend choice is only for the optional consultant/debug pack.")
    print("  It does not change the primary user host.")
    detected = set(available)
    ordered_backends = ("claude", "ollama", "openai", "anthropic")
    mapping: dict[str, str | None] = {}
    for index, backend in enumerate(ordered_backends, start=1):
        detection_label = " (detected)" if backend in detected else ""
        print(f"  [{index}] {backend}{detection_label}")
        mapping[str(index)] = backend
    configure_later_choice = str(len(ordered_backends) + 1)
    print(f"  [{configure_later_choice}] Configure later")
    mapping[configure_later_choice] = None
    reverse = {value: key for key, value in mapping.items() if value is not None}
    normalized_default = str(default).strip().lower()
    default_choice = reverse.get(normalized_default, configure_later_choice)
    choice = _ask_or_default("Choice", default_choice, apply_answers=apply_answers)
    return mapping.get(choice)


def _ask_documentation_mode(default: str = "managed", apply_answers: bool = False) -> str:
    print("  I recommend `managed` for most first-run setups.")
    print("  `managed` lets ControlCoding keep the local design/plan/roadmap taxonomy aligned.")
    print("  The main alternative is `project_managed`, which fits when the project already has a document structure you want to preserve as-is.")
    print("  [1] managed")
    print("  [2] project_managed")
    mapping = {"1": "managed", "2": "project_managed"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "managed")


def _ask_hook_location(default: str = "local", apply_answers: bool = False) -> str:
    print("  I recommend `local` on the public baseline path.")
    print("  `local` keeps hooks isolated to this project. `central` is an expert/shared-install choice for multi-project reuse.")
    print("  [1] local")
    print("  [2] central")
    mapping = {"1": "local", "2": "central"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Choice", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "local")


def _ask_configure_advanced_packs(default: bool = False, apply_answers: bool = False) -> bool:
    print("  I recommend skipping advanced packs on the first baseline setup.")
    print("  Packs add optional capabilities and can be installed later once the baseline framework is working.")
    return _ask_yn_or_default(
        "Configure advanced packs now?",
        default=default,
        apply_answers=apply_answers,
    )


def _ask_engagement_tier(default: str = "core", apply_answers: bool = False) -> str:
    print("  I recommend `Core` unless you already know you need specialist-assisted workflows.")
    print("  `Core` keeps the primary coding work in the chosen host. `Agents` adds explicit helper roles on that host. `Studio` stays a later optional extra, not the current release path.")
    print("  [1] Core")
    print("  [2] Agents")
    print("  [3] Studio")
    mapping = {"1": "core", "2": "agents", "3": "studio"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Product tier", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "core")


def _ask_backend_policy(default: str = "local_only",
                        tier: str = "core",
                        apply_answers: bool = False) -> str:
    print("  This choice controls which model backends are allowed, not which user host you work from.")
    normalized_tier = str(tier or "core").strip().lower()
    if normalized_tier in {"agents", "studio"}:
        print("  For the current release path, keep Agents explicit and chat-defined on the chosen host.")
        print("  Use `approved` only if you are intentionally preparing the later Version II API-backed path.")
        print("  Use `local_only` when you do not want extra backend activation beyond the current host/manual flow.")
    else:
        print("  For `Core`, I usually recommend `local_only` unless you already know an approved backend must be prepared now.")
        print("  Use `approved` when local plus approved cloud APIs should be allowed. Use `all` only for explicit non-public experiments.")
    print("  [1] local_only")
    print("  [2] approved")
    print("  [3] all")
    mapping = {"1": "local_only", "2": "approved", "3": "all"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Backend policy", reverse.get(default, "1"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "local_only")


def _coerce_non_negative_int(value, fallback: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else 0


def _ask_specialist_permission(default: str = "approval_required", apply_answers: bool = False) -> str:
    print("  Permission envelope for this specialist path:")
    print("  [1] user_mediated")
    print("      The path exists, but each use stays explicit and user-triggered.")
    print("  [2] approval_required")
    print("      CC may prepare or route the work, but execution still needs an approval gate.")
    print("  [3] within_budget_auto")
    print("      This path may run automatically within the saved backend and budget policy.")
    mapping = {"1": "user_mediated", "2": "approval_required", "3": "within_budget_auto"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Permission envelope", reverse.get(default, "2"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "approval_required")


def _canonical_permission_for_execution_mode(execution_mode: str) -> str:
    normalized = str(execution_mode or "").strip().lower()
    if normalized == "human_mediated":
        return "user_mediated"
    if normalized == "auto_bounded":
        return "within_budget_auto"
    if normalized == "disabled":
        return "disabled"
    return "approval_required"


def _infer_specialist_execution_mode(permission: str, active: bool = True) -> str:
    if not active:
        return "disabled"
    normalized = str(permission or "").strip().lower()
    if normalized == "user_mediated":
        return "human_mediated"
    if normalized == "within_budget_auto":
        return "auto_bounded"
    return "cc_routed"


def _ask_specialist_execution_mode(default: str = "cc_routed",
                                   apply_answers: bool = False) -> str:
    print("  Execution mode for this specialist path:")
    print("  Current release bias: `human_mediated` is the public `Agents` path when specialist help is exposed externally.")
    print("  [1] human_mediated")
    print("      Current public release path: explicit prompt, folder, and behavior contracts on the chosen host.")
    print("  [2] cc_routed")
    print("      Later Version II path when API-backed specialist routing is intentionally enabled.")
    print("  [3] auto_bounded")
    print("      Later automation path after routed specialist flows are proven.")
    mapping = {"1": "human_mediated", "2": "cc_routed", "3": "auto_bounded"}
    reverse = {value: key for key, value in mapping.items()}
    choice = _ask_or_default("Execution mode", reverse.get(default, "2"), apply_answers=apply_answers)
    return mapping.get(choice, default if default in mapping.values() else "cc_routed")


def _normalize_specialist_paths_prefill(value) -> list[dict]:
    paths = value if isinstance(value, list) else []
    normalized: list[dict] = []
    for index, item in enumerate(paths, start=1):
        if not isinstance(item, dict):
            continue
        active = item.get("active", True) is not False
        permission = str(item.get("permission") or item.get("permission_mode") or "approval_required").strip() or "approval_required"
        execution_mode = str(
            item.get("execution_mode")
            or item.get("executionMode")
            or _infer_specialist_execution_mode(permission, active=active)
        ).strip() or _infer_specialist_execution_mode(permission, active=active)
        permission = _canonical_permission_for_execution_mode(execution_mode)
        role_id = str(item.get("role_id") or item.get("id") or f"specialist_{index}").strip() or f"specialist_{index}"
        normalized.append({
            "role_id": role_id,
            "label": str(item.get("label") or item.get("role") or role_id).strip(),
            "path_type": str(item.get("path_type") or item.get("kind") or "specialist").strip() or "specialist",
            "active": active,
            "backend": str(item.get("backend") or item.get("backend_id") or "").strip(),
            "model": str(item.get("model") or "").strip(),
            "permission": permission,
            "execution_mode": execution_mode,
            "max_calls": _coerce_non_negative_int(item.get("max_calls", item.get("maxCalls", 0)), 0),
        })
    return normalized


def _specialist_path_by_role(paths: list[dict], role_id: str) -> dict:
    for path in paths:
        if str(path.get("role_id", "")).strip() == role_id:
            return path
    return {}


def _specialist_paths_by_prefix(paths: list[dict], prefix: str) -> list[dict]:
    return [
        path
        for path in paths
        if str(path.get("role_id", "")).strip().startswith(prefix)
    ]


def _collect_specialist_paths(default_paths: list[dict],
                              tier: str,
                              backend_policy: str,
                              tandem_config: dict,
                              apply_answers: bool = False) -> list[dict]:
    if tier not in {"agents", "studio"}:
        return []

    print("\n  Manual consultation and agent orchestration stay separate.")
    print("  `Core + manual consultation` remains the public manual second-opinion path.")
    print("  The current public `Agents` path is explicit chat-defined specialist help on the chosen host.")
    print("  Think prompt, folder, and behavior contract, not API-backed routing.")
    print("  API-backed or auto-routed specialist paths are later Version II work, not the current release claim.")
    if backend_policy == "approved":
        print("  You selected `approved`, which prepares the later Version II API-backed path rather than the current release baseline.")
    elif backend_policy == "all":
        print("  You selected `all`, which goes beyond the current release baseline and should stay an explicit internal/advanced choice.")
    elif backend_policy == "local_only":
        print("  You selected `local_only`, which keeps extra backend activation off unless you are intentionally testing local specialist experiments.")
    print("  The matrix below records the explicit specialist/runtime paths for Agents or Studio.")

    specialist_paths: list[dict] = []

    codewarden_default = _specialist_path_by_role(default_paths, "codewarden")
    if _ask_yn_or_default(
        "Enable CodeWarden specialist path?",
        default=codewarden_default.get("active", True) is not False,
        apply_answers=apply_answers,
    ):
        print("\n  CodeWarden specialist path")
        permission_choice = _ask_specialist_permission(
            str(codewarden_default.get("permission", "approval_required")),
            apply_answers=apply_answers,
        )
        execution_mode = _ask_specialist_execution_mode(
            str(codewarden_default.get("execution_mode") or _infer_specialist_execution_mode(permission_choice)),
            apply_answers=apply_answers,
        )
        permission = _canonical_permission_for_execution_mode(execution_mode)
        specialist_paths.append({
            "role_id": "codewarden",
            "label": "CodeWarden",
            "path_type": "watchdog",
            "active": True,
            "backend": _normalize_uncertain_text(
                _ask_or_default(
                    "Backend/API label or configured backend id",
                    str(codewarden_default.get("backend", "")),
                    apply_answers=apply_answers,
                ),
                "",
            ),
            "model": _normalize_uncertain_text(
                _ask_or_default("Model (optional)", str(codewarden_default.get("model", "")), apply_answers=apply_answers),
                "",
            ),
            "permission": permission,
            "execution_mode": execution_mode,
            "max_calls": _coerce_non_negative_int(
                _ask_or_default(
                    "Per-session call limit for this path (0 = follow global budget/unlimited)",
                    str(codewarden_default.get("max_calls", 0)),
                    apply_answers=apply_answers,
                ),
                0,
            ),
        })

    narrator_default = _specialist_path_by_role(default_paths, "narrator")
    if _ask_yn_or_default(
        "Enable Narrator specialist path?",
        default=narrator_default.get("active", False) is True,
        apply_answers=apply_answers,
    ):
        print("\n  Narrator specialist path")
        permission_choice = _ask_specialist_permission(
            str(narrator_default.get("permission", "approval_required")),
            apply_answers=apply_answers,
        )
        execution_mode = _ask_specialist_execution_mode(
            str(narrator_default.get("execution_mode") or _infer_specialist_execution_mode(permission_choice)),
            apply_answers=apply_answers,
        )
        permission = _canonical_permission_for_execution_mode(execution_mode)
        specialist_paths.append({
            "role_id": "narrator",
            "label": "Narrator",
            "path_type": "narration",
            "active": True,
            "backend": _normalize_uncertain_text(
                _ask_or_default(
                    "Backend/API label or configured backend id",
                    str(narrator_default.get("backend", "")),
                    apply_answers=apply_answers,
                ),
                "",
            ),
            "model": _normalize_uncertain_text(
                _ask_or_default("Model (optional)", str(narrator_default.get("model", "")), apply_answers=apply_answers),
                "",
            ),
            "permission": permission,
            "execution_mode": execution_mode,
            "max_calls": _coerce_non_negative_int(
                _ask_or_default(
                    "Per-session call limit for this path (0 = follow global budget/unlimited)",
                    str(narrator_default.get("max_calls", 0)),
                    apply_answers=apply_answers,
                ),
                0,
            ),
        })

    consultant_defaults = _specialist_paths_by_prefix(default_paths, "consultant_")
    for slot in range(3):
        role_id = f"consultant_{slot + 1}"
        default_path = consultant_defaults[slot] if slot < len(consultant_defaults) else {}
        if not _ask_yn_or_default(
            f"Configure specialist slot {slot + 1}?",
            default=default_path.get("active", False) is True,
            apply_answers=apply_answers,
        ):
            continue
        print(f"\n  Specialist slot {slot + 1}")
        label = _normalize_uncertain_text(
            _ask_or_default(
                "Specialist role label",
                str(default_path.get("label", f"Specialist {slot + 1}")),
                apply_answers=apply_answers,
            ),
            f"Specialist {slot + 1}",
        )
        permission_choice = _ask_specialist_permission(
            str(default_path.get("permission", "approval_required")),
            apply_answers=apply_answers,
        )
        execution_mode = _ask_specialist_execution_mode(
            str(default_path.get("execution_mode") or _infer_specialist_execution_mode(permission_choice)),
            apply_answers=apply_answers,
        )
        permission = _canonical_permission_for_execution_mode(execution_mode)
        specialist_paths.append({
            "role_id": role_id,
            "label": label,
            "path_type": "consultant",
            "active": True,
            "backend": _normalize_uncertain_text(
                _ask_or_default(
                    "Backend/API label or configured backend id",
                    str(default_path.get("backend", "")),
                    apply_answers=apply_answers,
                ),
                "",
            ),
            "model": _normalize_uncertain_text(
                _ask_or_default("Model (optional)", str(default_path.get("model", "")), apply_answers=apply_answers),
                "",
            ),
            "permission": permission,
            "execution_mode": execution_mode,
            "max_calls": _coerce_non_negative_int(
                _ask_or_default(
                    "Per-session call limit for this path (0 = follow global budget/unlimited)",
                    str(default_path.get("max_calls", 0)),
                    apply_answers=apply_answers,
                ),
                0,
            ),
        })

    backend_a = str(tandem_config.get("backend_a", "")).strip()
    backend_b = str(tandem_config.get("backend_b", "")).strip()
    model_a = str(tandem_config.get("model_a", "")).strip()
    model_b = str(tandem_config.get("model_b", "")).strip()
    tandem_ready = bool(backend_a and backend_b)
    tandem_default = _specialist_path_by_role(default_paths, "tandem")
    if tier in {"agents", "studio"} and tandem_ready:
        if _ask_yn_or_default(
            "Record the local tandem path in the specialist matrix?",
            default=tandem_default.get("active", True) is not False,
            apply_answers=apply_answers,
        ):
            permission_choice = _ask_specialist_permission(
                str(tandem_default.get("permission", "within_budget_auto")),
                apply_answers=apply_answers,
            )
            execution_mode = _ask_specialist_execution_mode(
                str(tandem_default.get("execution_mode") or _infer_specialist_execution_mode(permission_choice)),
                apply_answers=apply_answers,
            )
            permission = _canonical_permission_for_execution_mode(execution_mode)
            specialist_paths.append({
                "role_id": "tandem",
                "label": "Tandem",
                "path_type": "tandem",
                "active": True,
                "backend": backend_a if backend_a == backend_b else f"{backend_a}+{backend_b}",
                "model": f"{model_a} vs {model_b}" if model_a or model_b else "",
                "permission": permission,
                "execution_mode": execution_mode,
                "max_calls": _coerce_non_negative_int(
                    _ask_or_default(
                        "Per-session call limit for tandem (0 = follow global budget/unlimited)",
                        str(tandem_default.get("max_calls", 0)),
                        apply_answers=apply_answers,
                    ),
                    0,
                ),
            })

    return specialist_paths


def _format_specialist_matrix_lines(paths: list[dict], global_max_calls: int) -> list[str]:
    lines: list[str] = []
    for path in paths:
        if path.get("active") is False:
            continue
        label = str(path.get("label") or path.get("role_id") or "Specialist").strip()
        backend = str(path.get("backend", "")).strip() or "(missing backend)"
        model = str(path.get("model", "")).strip()
        permission = str(path.get("permission", "approval_required")).strip()
        execution_mode = str(
            path.get("execution_mode")
            or _infer_specialist_execution_mode(permission, active=path.get("active", True) is not False)
        ).strip()
        max_calls = _coerce_non_negative_int(path.get("max_calls", 0), 0)
        limit_text = "follow global budget" if max_calls == 0 else f"{max_calls} call(s)"
        if global_max_calls > 0 and max_calls == 0:
            limit_text = f"follow global budget ({global_max_calls} call(s))"
        details = f"{label}: {backend}"
        if model:
            details += f" / {model}"
        details += f" / {execution_mode} / {permission} / {limit_text}"
        lines.append(details)
    return lines


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _require_chat_or_answers_file(
    command_label: str,
    guide_flag: str,
    answers_file: Path | None,
    apply_answers: bool,
) -> tuple[bool, int | None]:
    """Disable terminal questionnaires and require chat guidance or a handoff file."""
    if answers_file is None:
        warn(
            f"Terminal questionnaire removed for `{command_label}`. "
            f"Use `{command_label} {guide_flag}` from your AI host or provide `--answers-file handoff.json`."
        )
        return apply_answers, 1
    if not apply_answers:
        info("Terminal questionnaire removed. Applying the provided answers file directly.")
    return True, None

def cmd_setup(project: Path, answers_file: Path | None = None, apply_answers: bool = False):
    """Base setup executor for ControlCoding using a prefilled handoff."""
    runtime_issue = core_runtime_error()
    if runtime_issue is not None:
        warn(runtime_issue.message)
        return 1
    apply_answers, early_exit = _require_chat_or_answers_file(
        "cc setup",
        "--chat-guide",
        answers_file,
        apply_answers,
    )
    if early_exit is not None:
        return early_exit
    print(f"\n{'='*50}")
    print("  ControlCoding Setup Apply")
    print(f"{'='*50}")
    print(f"\n  Project directory: {project}\n")
    if not (project / ".git").exists():
        info(
            "No git repository detected yet. Setup will initialize one automatically "
            "if the selected host needs repo-side gates."
        )

    # Check if already initialized
    if ((project / CANONICAL_CONTEXT_FILENAME).exists() or (project / LEGACY_CONTEXT_FILENAME).exists()) and _control_plane_read_path(project, "settings.json").exists():
        if not _ask_yn_or_default("ControlCoding already initialized. Re-run setup?", default=apply_answers, apply_answers=apply_answers):
            return 0

    existing_cc_config = _load_json_object(_control_plane_read_path(project, "cc_config.json"))
    existing_planning = existing_cc_config.get("planning", {})
    if not isinstance(existing_planning, dict):
        existing_planning = {}
    existing_boundaries = _normalize_module_boundaries(existing_cc_config.get("module_boundaries", {}))
    prefill = _load_answers_payload(answers_file, "setup", strict_section=apply_answers)
    if apply_answers and not prefill:
        warn("`--apply-answers` requires a valid answers file with a `setup` payload.")
        return 1
    raw_backend_pref = prefill.get("backend_pref")
    backend_pref_from_handoff = (
        str(raw_backend_pref).strip().lower()
        if raw_backend_pref is not None
        else ""
    )
    if (
        backend_pref_from_handoff
        and backend_pref_from_handoff not in SUPPORTED_CONSULTATION_BACKENDS
    ):
        supported = ", ".join(sorted(SUPPORTED_CONSULTATION_BACKENDS))
        warn(
            f"Unsupported consultant backend '{backend_pref_from_handoff}'. "
            f"Supported backends: {supported}."
        )
        return 1
    requested_backend_pref = backend_pref_from_handoff or None
    raw_selected_packs = prefill.get("selected_packs", [])
    if not isinstance(raw_selected_packs, list):
        warn("`selected_packs` must be a list of supported pack IDs.")
        return 1
    selected_packs_from_handoff: list[str] = []
    for raw_pack in raw_selected_packs:
        if not isinstance(raw_pack, str):
            warn("`selected_packs` must contain only supported pack IDs.")
            return 1
        normalized_pack = raw_pack.strip()
        if normalized_pack not in SETUP_ADVANCED_PACKS:
            supported = ", ".join(SETUP_ADVANCED_PACKS)
            warn(
                f"Unsupported setup pack '{normalized_pack}'. "
                f"Supported packs: {supported}."
            )
            return 1
        if normalized_pack in selected_packs_from_handoff:
            warn(f"Duplicate setup pack '{normalized_pack}' in `selected_packs`.")
            return 1
        selected_packs_from_handoff.append(normalized_pack)
    if answers_file is not None and prefill:
        info(f"Using prefilled setup defaults from {answers_file}")
    if apply_answers:
        info("Applying setup directly from the answers file.")

    # 1. Project identity
    print(f"\n--- Project Info ---\n")
    default_name = project.name
    answers = {
        "name": _normalize_uncertain_text(
            _ask_or_default("Project name", str(prefill.get("name", default_name)), apply_answers=apply_answers),
            str(default_name),
        ),
    }

    planning_prefill = prefill.get("planning", {})
    if not isinstance(planning_prefill, dict):
        planning_prefill = {}
    answers["kickoff_mode"] = "skip"
    answers["kickoff"] = {"mode": "skip"}
    answers["project_definition_mode"] = _infer_project_definition_mode(prefill)
    answers["planning"] = {}
    answers["product"] = {}
    answers["environment"] = {}
    answers["stack"] = ""
    answers["arch"] = ""
    answers["truth"] = ""
    answers["view"] = ""

    # 2. Documentation governance
    print(f"\n--- Documentation Governance ---\n")
    answers["documentation_mode"] = _ask_documentation_mode(
        str(prefill.get("documentation_mode", "managed")),
        apply_answers=apply_answers,
    )

    print(f"\n--- CC Artifact Storage ---\n")
    print("  CC working documents stay local-only in the public install path.")
    print("  This covers STATUS, ROADMAP, BUGS, and CC-managed dev/ docs.")
    print("  devlog/ is always local session memory and is never meant for git.")
    info("Artifact policy fixed for setup: local_only")
    answers["cc_artifact_mode"] = "local_only"
    answers["memory_default_policy"] = _normalize_memory_default_policy(
        str(prefill.get("memory_default_policy", MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE))
    )
    if answers["memory_default_policy"] == MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE:
        runtime_issue = memory_runtime_error()
        if runtime_issue is not None:
            warn(runtime_issue.message)
            return 1
    info(
        "Project Memory Engine and GraphRAG are Core defaults. "
        "Base setup prepares local memory with governed-scope indexing only."
    )

    print(f"\n--- User Host ---\n")
    print("  ControlCoding distinguishes the user's host surface from optional CC UI.")
    print("  On official CLI paths, the canonical chat stays in the chosen host.")
    answers["user_host"] = _ask_user_host(str(prefill.get("user_host", "claude_code")), apply_answers=apply_answers)
    selected_host_profile = _derive_host_profile(answers["user_host"])
    info(
        f"{selected_host_profile['label']}: {selected_host_profile['summary']}"
    )
    info("Host gate contract:")
    for line in _format_host_gate_contract_lines(selected_host_profile):
        info(f"  {line}")
    if selected_host_profile["inlineBoundaryGate"] == "none" and not (project / ".git").exists():
        info(
            "Selected host has no inline gate. Setup will initialize a local git "
            "repository before wiring the repo boundary and verification gates."
        )
    if selected_host_profile.get("contextFile"):
        info(
            "Primary host context file: "
            f"{selected_host_profile['contextFile']}"
        )
    print(f"\n--- Host Workflow Guidance ---\n")
    answers["host_instruction_mode"] = _ask_host_instruction_mode(
        str(prefill.get("host_instruction_mode", "recommended")),
        apply_answers=apply_answers,
    )
    prefill_host_notes = prefill.get("host_custom_notes", [])
    if not isinstance(prefill_host_notes, list):
        prefill_host_notes = []
    if answers["host_instruction_mode"] == "custom" and prefill_host_notes:
        info(f"Using {len(prefill_host_notes)} prefilled custom host note(s)")
        answers["host_custom_notes"] = [str(note).strip() for note in prefill_host_notes if str(note).strip()]
    else:
        answers["host_custom_notes"] = (
            [] if apply_answers else _ask_host_custom_notes()
        ) if answers["host_instruction_mode"] == "custom" else []
    if answers["host_instruction_mode"] == "custom" and not answers["host_custom_notes"]:
        info("No custom notes added. Falling back to recommended host guidance.")
        answers["host_instruction_mode"] = "recommended"

    usage_tier_default = str(
        planning_prefill.get("tier")
        or prefill.get("product_tier")
        or existing_planning.get("tier")
        or "core"
    )
    usage_manual_default = bool(
        planning_prefill.get("manual_consultation_allowed", False)
        or prefill.get("manual_consultation_allowed", False)
        or existing_planning.get("manual_consultation_allowed", False)
    )
    print(f"\n--- ControlCoding Usage Model ---\n")
    answers["planning"] = _ask_controlcoding_usage_model(
        default_tier=usage_tier_default,
        default_manual_consultation=usage_manual_default,
        apply_answers=apply_answers,
    )

    # 3. Directory scanning
    print(f"\n--- Module Boundaries ---\n")
    stable, shared, features = _collect_module_boundaries(
        project,
        prefill=prefill,
        existing=existing_boundaries,
        apply_answers=apply_answers,
    )
    answers["stable"] = stable
    answers["shared"] = shared
    answers["features"] = features

    # 4. Protected zones -> cc_config.json
    deny_zones = _collect_protected_zones(
        answers["stable"],
        answers["shared"],
        selected_host_profile,
        apply_answers=apply_answers,
    )

    # 5. Advanced packs (optional, usually after engagement)
    selected_packs = []
    backend_pref = None
    print(f"\n--- Advanced Packs ---\n")
    configure_advanced_packs = _ask_configure_advanced_packs(
        default=bool(prefill.get("configure_advanced_packs", False)),
        apply_answers=apply_answers,
    )
    if configure_advanced_packs:
        print(f"\n--- Backend Detection ---\n")
        backends = _detect_backends()
        if backends:
            for name, desc in backends.items():
                ok(f"{name}: {desc}")
        else:
            info("No consultant backends detected.")

        print(f"\n--- Optional Packs ---\n")
        pack_descriptions = {
            "debug-tools": "Debug escalation and consultant MCP",
            "session-manager": "Session continuity MCP",
            "multi-agent": "Bridge MCP + helper session",
            "dashboard": "Local dashboard for hooks, logs, and fitness",
        }
        default_selected_packs = selected_packs_from_handoff
        if apply_answers:
            selected_packs = list(default_selected_packs)
        else:
            for pack, desc in pack_descriptions.items():
                pack_default = (
                    pack in default_selected_packs
                    if default_selected_packs
                    else pack in ("debug-tools", "session-manager")
                )
                if _ask_yn_or_default(
                    f"Install {pack}? ({desc})",
                    default=pack_default,
                    apply_answers=apply_answers,
                ):
                    selected_packs.append(pack)

        if "debug-tools" in selected_packs:
            print(f"\n--- Consultant Backend ---\n")
            available = list(backends.keys())
            if available:
                print(f"  Detected: {', '.join(available)}")
            else:
                print("  Detected: none")
            backend_pref = _ask_backend_preference(
                available,
                requested_backend_pref or "",
                apply_answers=apply_answers,
            )
    else:
        info("Skipping advanced packs for now. Install them later with `cc install <pack>` if needed.")

    # 6. Hook location
    prefill_hooks_location = str(prefill.get("hooks_location", "local")).strip().lower()
    hooks_location = "central" if prefill_hooks_location == "central" else "local"
    use_central_hooks = hooks_location == "central"
    if use_central_hooks:
        info("Using advanced central hooks override from the provided handoff.")

    # 7. Behavioral constraints
    print(f"\n--- Agent Behavioral Constraints ---\n")
    print("  Deterministic behavioral rules the AI must follow.")
    print(f"  These go into {CANONICAL_CONTEXT_FILENAME} as [advisory] rules.")
    print("  Examples: 'no random without seed', 'all API calls need auth',")
    print("            'never modify database schema without migration'")
    print()
    behavioral_rules = []
    prefill_behavioral_rules = prefill.get("behavioral_rules", [])
    if isinstance(prefill_behavioral_rules, list) and prefill_behavioral_rules:
        behavioral_rules = [str(rule).strip() for rule in prefill_behavioral_rules if str(rule).strip()]
        info(f"Using {len(behavioral_rules)} prefilled behavioral rule(s)")
    elif apply_answers:
        behavioral_rules = []
    else:
        while True:
            rule = _ask("Add a behavioral rule (empty to stop)", "")
            if not rule:
                break
            behavioral_rules.append(rule)
    answers["behavioral_rules"] = behavioral_rules

    if prefill.get("project_definition_mode") not in {None, "", "skip"}:
        info("Ignoring project-definition fields during base install. Use `cc.py setup-project` after ControlCoding is installed.")

    # 8. Confirm and execute
    print(f"\n--- Summary ---\n")
    print(f"  Project: {answers['name']}")
    print(f"  Documentation mode: {answers['documentation_mode']}")
    print("  CC working docs: local_only (public default)")
    print(f"  Project memory: {answers['memory_default_policy']} (local, governed-scope)")
    print(f"  User host: {answers['user_host']}")
    print(f"  ControlCoding usage model: {_planning_summary_line(answers['planning'])}")
    print(
        "  Enforcement profile: "
        f"{selected_host_profile['capabilityClass']} / {selected_host_profile['protectionModel']}"
    )
    print("  Gate contract:")
    for line in _format_host_gate_contract_lines(selected_host_profile):
        print(f"    - {line}")
    if selected_host_profile.get("contextFile"):
        print(f"  Primary context file: {selected_host_profile['contextFile']}")
    print(f"  Host guidance: {answers['host_instruction_mode']}")
    if answers["host_instruction_mode"] == "custom" and answers["host_custom_notes"]:
        print(f"  Host custom notes: {len(answers['host_custom_notes'])}")
    if use_central_hooks:
        print("  Hook location: central (~/.controlcoding/hooks/) [advanced override]")
    if deny_zones:
        deny_only = [z["path"] for z in deny_zones if z.get("level") == "deny"]
        warn_only = [z["path"] for z in deny_zones if z.get("level") == "warn"]
        if deny_only:
            print(f"  DENY zones: {', '.join(deny_only)}")
        if warn_only:
            print(f"  WARN zones: {', '.join(warn_only)}")
    if selected_packs:
        print(f"  Packs: {', '.join(selected_packs)}")
    if backend_pref:
        print(f"  Consultant backend: {backend_pref}")
    if behavioral_rules:
        print(f"  Behavioral rules: {len(behavioral_rules)}")
    print("  Project setup: run separately after install if you want kickoff docs and project framing.")
    print()

    if not _ask_yn_or_default("Proceed with setup?", default=True, apply_answers=apply_answers):
        print("\nSetup cancelled.")
        return 0

    print(f"\n--- Installing ---\n")

    if not _ensure_setup_repo_boundary(project, selected_host_profile):
        return 1

    # Generate the non-adapter canonical context source in its preexisting order.
    existing_context_files = [
        path.name
        for path in (project / CANONICAL_CONTEXT_FILENAME, project / LEGACY_CONTEXT_FILENAME)
        if path.exists()
    ]
    if existing_context_files:
        if _ask_yn_or_default(
            "Overwrite existing context file(s) with a tailored canonical version?",
            default=apply_answers,
            apply_answers=apply_answers,
        ):
            if not _generate_context_source(project, answers):
                warn(f"Could not generate {CANONICAL_CONTEXT_FILENAME} from the template")
                return 1
            ok(f"Generated tailored {CANONICAL_CONTEXT_FILENAME}")
        else:
            warn(f"Keeping existing context file(s): {', '.join(existing_context_files)}")
    else:
        if not _generate_context_source(project, answers):
            warn(f"Could not generate {CANONICAL_CONTEXT_FILENAME} from the template")
            return 1
        ok(f"Generated tailored {CANONICAL_CONTEXT_FILENAME}")

    # Write cc_config.json before init so init preserves the configured mode.
    cc_config = _control_plane_path(project, "cc_config.json")
    cc_config.parent.mkdir(parents=True, exist_ok=True)
    existing_config = _load_json_object(cc_config)
    cc_config_payload = _build_cc_config(
        existing_config,
        protected_zones=_merge_protected_zones(existing_config.get("protected_zones", []), deny_zones),
        documentation_mode=answers["documentation_mode"],
        hooks_location="central" if use_central_hooks else "local",
        cc_artifact_mode=answers["cc_artifact_mode"],
        memory_default_policy=answers["memory_default_policy"],
        project_definition_mode=answers.get("project_definition_mode"),
        module_boundaries={
            "stable": answers["stable"],
            "shared": answers["shared"],
            "features": answers["features"],
        },
        planning=answers["planning"],
        product=answers.get("product"),
        environment=answers.get("environment"),
    )
    cc_config.write_text(json.dumps(cc_config_payload, indent=2), encoding="utf-8")
    ok(
        "Updated cc_config.json "
        f"(documentation_mode={answers['documentation_mode']}, cc_artifact_mode={answers['cc_artifact_mode']}, zones={len(deny_zones)})"
    )

    gateway_config = _control_plane_path(project, "gateway_config.json")
    existing_gateway = _load_json_object(gateway_config)
    gateway_payload = _build_gateway_config(existing_gateway, answers["user_host"])
    gateway_payload["hostInstructions"] = _build_host_instructions_config(
        answers["host_instruction_mode"],
        answers["host_custom_notes"],
    )
    gateway_config.write_text(json.dumps(gateway_payload, indent=2), encoding="utf-8")
    ok(
        "Updated gateway_config.json "
        f"(userHost={gateway_payload['userHost']}, enabledHosts={len(gateway_payload.get('enabledHosts', []))}, "
        f"profile={gateway_payload['hostProfile']['capabilityClass']}/{gateway_payload['hostProfile']['protectionModel']})"
    )

    # Earlier context/config/Git stages may already have changed the project.
    init_result = cmd_init(project, central_hooks=use_central_hooks, quiet=True)
    if init_result != 0:
        warn(
            "Partial setup: minimal initialization did not complete. "
            "Earlier context/config/Git output may remain. Inspect the reported conflict "
            "and existing setup output before retrying; no rollback was performed."
        )
        return init_result

    launcher_files = _write_host_integration_assets(
        project,
        gateway_payload["userHost"],
        gateway_payload.get("hostInstructions"),
    )
    ok(f"Generated {len(launcher_files)} local host integration asset(s) for this project")
    synced_context_paths = []
    host_context_spec = _host_context_export_spec(gateway_payload["userHost"])
    host_context_path = _sync_host_context_file_compat(
        project,
        gateway_payload["userHost"],
        force_refresh=apply_answers,
    )
    adapter_sync_failed = host_context_spec is not None and host_context_path is None
    if host_context_path:
        synced_context_paths.append(host_context_path)
    try:
        memory_receipt = _bootstrap_default_project_memory(
            project,
            answers["name"],
            answers["memory_default_policy"],
        )
    except Exception as exc:
        warn(f"Partial setup: Project Memory Engine bootstrap failed ({type(exc).__name__}: {exc}). "
             "Inspect the existing setup output before retrying; no rollback was performed.")
        return 1
    if memory_receipt.get("status") == "completed":
        ok(
            "Initialized Project Memory Engine with governed-scope scan "
            f"({memory_receipt.get('receiptPath')})"
        )
    elif memory_receipt.get("status") == "deferred":
        info("Project Memory Engine initialization was deferred by setup policy.")
    else:
        warn(
            "Partial setup: Project Memory Engine bootstrap did not complete: "
            f"{memory_receipt.get('status')}. Inspect its receipt and existing setup output "
            "before retrying; no rollback was performed."
        )
        return 1
    host_manifest = _load_json_object(_control_plane_path(project, "launchers", "manifest.json"))
    next_steps = host_manifest.get("nextSteps", [])
    if isinstance(next_steps, list):
        for step in next_steps[:3]:
            if isinstance(step, str) and step.strip():
                info(step)

    kickoff_written: list[str] = []

    # Install selected packs
    for pack in selected_packs:
        install_result = cmd_install(project, pack)
        if install_result != 0:
            warn(
                f"Partial setup: pack '{pack}' did not complete. "
                "Inspect the reported conflict and existing setup output before retrying; "
                "no rollback was performed."
            )
            return install_result

    # Update consultant backend env if selected
    if backend_pref and "debug-tools" in selected_packs:
        settings = load_settings(project)
        servers = settings.get("mcpServers", {})
        if "debug-consultant" in servers:
            servers["debug-consultant"].setdefault("env", {})["CONSULT_BACKEND"] = backend_pref
            save_settings(project, settings)
            ok(f"Set consultant backend to '{backend_pref}'")

    # Run doctor
    print()
    cmd_doctor(project)

    context_source, _source_text = _read_context_source(project)
    context_label = context_source.name if context_source is not None else CANONICAL_CONTEXT_FILENAME
    completion_parts = [
        context_label,
        "cc_config.json",
    ]
    for path_label in synced_context_paths:
        if path_label not in completion_parts:
            completion_parts.append(path_label)
    if memory_receipt.get("receiptPath"):
        completion_parts.append(str(memory_receipt["receiptPath"]))
    completion_parts.append(f"the local host assets in {CONTROL_PLANE_DIRNAME}/launchers/ (plus .vscode/tasks.json if installed)")
    if adapter_sync_failed:
        warn("Base setup completed its non-adapter steps, but adapter sync failed")
        return 1
    print(
        f"\n{green('Base setup complete!')} Review "
        + ", ".join(completion_parts)
        + f", then continue from chat with `{PUBLIC_CHAT_STARTER_RELATIVE_PATH.as_posix()}`. "
        + "That public assistant flow should guide engagement and optional project setup without making the user memorize backend commands."
    )
    return 0


def cmd_setup_project(project: Path, answers_file: Path | None = None, apply_answers: bool = False):
    """Project-definition executor after ControlCoding base installation."""
    apply_answers, early_exit = _require_chat_or_answers_file(
        "cc setup-project",
        "--chat-guide",
        answers_file,
        apply_answers,
    )
    if early_exit is not None:
        return early_exit
    print(f"\n{'='*50}")
    print("  ControlCoding Project Setup Apply")
    print(f"{'='*50}")
    print(f"\n  Project directory: {project}\n")

    if not ((project / CANONICAL_CONTEXT_FILENAME).exists() or (project / LEGACY_CONTEXT_FILENAME).exists()):
        warn("ControlCoding is not installed in this project yet. Run `cc setup --project-root .` first.")
        return 1

    existing_cc_config = _load_json_object(_control_plane_read_path(project, "cc_config.json"))
    gateway_config = _load_json_object(_control_plane_read_path(project, "gateway_config.json"))
    existing_planning = existing_cc_config.get("planning", {})
    if not isinstance(existing_planning, dict):
        existing_planning = {}
    existing_product = existing_cc_config.get("product", {})
    if not isinstance(existing_product, dict):
        existing_product = {}
    existing_environment = existing_cc_config.get("environment", {})
    if not isinstance(existing_environment, dict):
        existing_environment = {}
    existing_boundaries = _normalize_module_boundaries(existing_cc_config.get("module_boundaries", {}))

    prefill = _load_answers_payload(answers_file, "project_setup", strict_section=apply_answers)
    if apply_answers and not prefill:
        warn("`--apply-answers` requires a valid answers file with a `project_setup` payload.")
        return 1
    if answers_file is not None and prefill:
        info(f"Using prefilled project-setup defaults from {answers_file}")
    if apply_answers:
        info("Applying project setup directly from the answers file.")

    current_identity = _load_existing_context_identity(project)
    brief_sources = _detect_existing_brief_sources(project)
    if brief_sources:
        info("Existing brief/doc candidates found: " + ", ".join(brief_sources))

    answers = {
        "name": current_identity.get("name", project.name),
        "documentation_mode": str(existing_cc_config.get("documentation_mode", "managed")),
        "cc_artifact_mode": str(existing_cc_config.get("cc_artifact_mode", "local_only")),
        "hooks_location": str(existing_cc_config.get("hooks_location", "local")),
        "user_host": str(gateway_config.get("userHost", "other")),
        "planning": dict(existing_planning),
        "behavioral_rules": [],
        "stable": [],
        "shared": [],
        "features": [],
    }

    print(f"\n--- Project Definition Mode ---\n")
    project_definition_default = _infer_project_definition_mode(prefill)
    if project_definition_default == "skip":
        if _looks_like_existing_project(project, brief_sources):
            project_definition_default = "existing_project"
        else:
            project_definition_default = "existing_brief" if brief_sources else "guided"
    answers["project_definition_mode"] = _ask_project_definition_mode(
        project_definition_default,
        apply_answers=apply_answers,
    )
    if answers["project_definition_mode"] == "skip":
        info("Project setup cancelled. ControlCoding install remains unchanged.")
        return 0

    print(f"\n--- Project Definition and Kickoff ---\n")
    print("  This command scaffolds the first design and implementation package after CC is already installed.")
    kickoff_defaults = prefill.get("kickoff", {})
    if not isinstance(kickoff_defaults, dict):
        kickoff_defaults = {}
    if brief_sources and not str(kickoff_defaults.get("references", "")).strip():
        kickoff_defaults["references"] = brief_sources[0]
    default_kickoff_mode = str(
        prefill.get(
            "kickoff_mode",
            "existing_design" if answers["project_definition_mode"] == "existing_project"
            else "partial_spec" if answers["project_definition_mode"] == "existing_brief"
            else "idea",
        )
    )
    if default_kickoff_mode == "skip":
        default_kickoff_mode = (
            "existing_design"
            if answers["project_definition_mode"] == "existing_project"
            else "partial_spec" if answers["project_definition_mode"] == "existing_brief"
            else "idea"
        )
    answers["kickoff_mode"] = _ask_kickoff_mode(
        default_kickoff_mode,
        apply_answers=apply_answers,
    )
    if answers["project_definition_mode"] == "existing_brief":
        answers["kickoff"] = _collect_existing_brief_kickoff_answers(
            answers["kickoff_mode"],
            defaults=kickoff_defaults,
            apply_answers=apply_answers,
        )
        answers["adoption"] = {}
    elif answers["project_definition_mode"] == "existing_project":
        answers["kickoff"] = _collect_existing_project_kickoff_answers(
            answers["kickoff_mode"],
            defaults=kickoff_defaults,
            apply_answers=apply_answers,
        )
        adoption_defaults = prefill.get("adoption", {})
        if not isinstance(adoption_defaults, dict):
            adoption_defaults = {}
        answers["adoption"] = _collect_existing_project_adoption_answers(
            defaults=adoption_defaults,
            apply_answers=apply_answers,
        )
    else:
        answers["kickoff"] = _collect_kickoff_answers(
            answers["kickoff_mode"],
            answers,
            defaults=kickoff_defaults,
            apply_answers=apply_answers,
        )
        answers["adoption"] = {}

    obvious_inventory = _detect_obvious_environment_inventory()
    answers["product"] = _build_product_profile(
        prefill.get("product", existing_product),
        apply_answers=apply_answers,
    )
    answers["environment"] = _build_environment_policy(
        prefill.get("environment", existing_environment),
        obvious_inventory,
        apply_answers=apply_answers,
    )

    print(f"\n--- Technical Shape ---\n")
    print("  Plain-language answers are fine. Exact frameworks and libraries can be refined after kickoff.")
    answers["stack"] = _normalize_uncertain_text(
        _ask_or_default(
            "Implementation stack (optional, plain language is fine)",
            str(prefill.get("stack", current_identity.get("stack", ""))),
            apply_answers=apply_answers,
        ),
        current_identity.get("stack", ""),
    )
    answers["arch"] = _normalize_uncertain_text(
        _ask_or_default(
            "System shape / architecture direction (optional)",
            str(prefill.get("arch", current_identity.get("arch", ""))),
            apply_answers=apply_answers,
        ),
        current_identity.get("arch", ""),
    )
    truth_default = str(prefill.get("truth", current_identity.get("truth", ""))).strip()
    view_default = str(prefill.get("view", current_identity.get("view", ""))).strip()
    know_truth_view_default = bool(truth_default or view_default)
    if _ask_yn_or_default(
        "Do you already know a truth/view split for this project?",
        default=know_truth_view_default,
        apply_answers=apply_answers,
    ):
        answers["truth"] = _normalize_uncertain_text(
            _ask_or_default("Truth representation (leave empty to skip)", truth_default, apply_answers=apply_answers),
            truth_default,
        )
        answers["view"] = _normalize_uncertain_text(
            _ask_or_default("View representation (leave empty to skip)", view_default, apply_answers=apply_answers),
            view_default,
        )
    else:
        answers["truth"] = truth_default
        answers["view"] = view_default

    print(f"\n--- Module Boundaries ---\n")
    answers["stable"], answers["shared"], answers["features"] = _collect_module_boundaries(
        project,
        prefill=prefill,
        existing=existing_boundaries,
        apply_answers=apply_answers,
    )

    selected_host_profile = _derive_host_profile(answers["user_host"])
    protected_zone_updates = _collect_protected_zones(
        answers["stable"],
        answers["shared"],
        selected_host_profile,
        apply_answers=apply_answers,
    )

    print(f"\n--- Summary ---\n")
    print(f"  Project: {answers['name']}")
    print(f"  Source mode: {_project_definition_mode_label(answers['project_definition_mode'])}")
    print(f"  Kickoff readiness: {_kickoff_mode_label(answers['kickoff_mode'])}")
    print(f"  Stack: {answers['stack'] or '(to be refined)'}")
    print(f"  Architecture: {answers['arch'] or '(to be refined)'}")
    kickoff_references = str(answers.get("kickoff", {}).get("references", "")).strip()
    if kickoff_references:
        print(f"  References: {kickoff_references}")
    if answers["project_definition_mode"] == "existing_project":
        adoption = answers.get("adoption", {})
        if isinstance(adoption, dict):
            if adoption.get("authoritative_docs"):
                print(f"  Authoritative docs: {adoption['authoritative_docs']}")
            if adoption.get("stable_areas"):
                print(f"  Stable areas hint: {adoption['stable_areas']}")
    if answers.get("planning"):
        print(f"  Planning mode: {_planning_summary_line(answers['planning'])}")
    product = answers.get("product", {})
    if isinstance(product, dict):
        print(f"  Product form: {product.get('product_form', '(not set)')}")
        print(f"  Runtime/delivery: {product.get('runtime_constraints', '(not set)') or '(not set)'}")
    if answers["stable"]:
        print(f"  Stable boundaries: {', '.join(answers['stable'])}")
    if answers["shared"]:
        print(f"  Shared boundaries: {', '.join(answers['shared'])}")
    if answers["features"]:
        print(f"  Feature boundaries: {', '.join(answers['features'])}")
    print()

    if not _ask_yn_or_default("Proceed with project setup?", default=True, apply_answers=apply_answers):
        print("\nProject setup cancelled.")
        return 0

    cc_config_path = _control_plane_path(project, "cc_config.json")
    updated_cc_config = _build_cc_config(
        existing_cc_config,
        protected_zones=_merge_protected_zones(existing_cc_config.get("protected_zones", []), protected_zone_updates),
        documentation_mode=answers["documentation_mode"],
        hooks_location=answers["hooks_location"],
        cc_artifact_mode=answers["cc_artifact_mode"],
        memory_default_policy=str(existing_cc_config.get("memory_default_policy", MEMORY_DEFAULT_POLICY_GOVERNED_SCOPE)),
        project_definition_mode=answers["project_definition_mode"],
        module_boundaries={
            "stable": answers["stable"],
            "shared": answers["shared"],
            "features": answers["features"],
        },
        planning=answers["planning"],
        product=answers.get("product"),
        environment=answers.get("environment"),
    )
    cc_config_path.write_text(json.dumps(updated_cc_config, indent=2), encoding="utf-8")
    ok("Updated cc_config.json with project-definition metadata")

    if _update_existing_context_identity(project, answers):
        ok(f"Updated {CANONICAL_CONTEXT_FILENAME} project identity fields")
    else:
        warn(f"Could not update {CANONICAL_CONTEXT_FILENAME} identity block automatically")

    host_context_spec = _host_context_export_spec(answers["user_host"])
    synced_context_path = _sync_host_context_file_compat(
        project,
        answers["user_host"],
        force_refresh=apply_answers,
    )
    adapter_sync_failed = host_context_spec is not None and synced_context_path is None
    kickoff_written = _scaffold_kickoff_docs(project, answers)
    if kickoff_written:
        ok("Initial project package written: " + ", ".join(kickoff_written))
    else:
        info("Kickoff docs already existed or nothing new was written.")

    fitness_written, fitness_label = _scaffold_fitness_config(
        project,
        answers,
        force_refresh=apply_answers,
    )
    if fitness_written:
        ok(f"Seeded {fitness_label} with stronger default verification rules")
    else:
        info("Keeping existing fitness.json")
    setup_intent_label = _write_setup_intent_snapshot(
        project,
        answers,
        brief_sources,
        protected_zone_updates,
        synced_context_path,
        kickoff_written,
        fitness_label if fitness_written else "",
    )
    ok(f"Wrote {setup_intent_label} as the project setup intent snapshot")

    completion_parts = ["cc_config.json", CANONICAL_CONTEXT_FILENAME]
    completion_parts.append(setup_intent_label)
    if synced_context_path:
        completion_parts.append(synced_context_path)
    if kickoff_written:
        completion_parts.append("the kickoff package (design/plan/criteria/contracts/roadmap)")
    if fitness_written:
        completion_parts.append("fitness.json")
    if adapter_sync_failed:
        warn("Project setup completed its non-adapter steps, but adapter sync failed")
        return 1
    print(
        f"\n{green('Project setup complete!')} Review "
        + ", ".join(completion_parts)
        + " before coding."
    )
    return 0


def _setup_guide_command(project: Path, *arguments: str) -> str:
    """Quote a command for PowerShell on Windows, or a POSIX shell elsewhere."""
    parts = [sys.executable, str(SCRIPT_DIR / "cc.py"), *arguments,
             "--project-root", str(project.resolve())]
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts)
    return shlex.join(parts)


def cmd_setup_chat_guide(project: Path, host_hint: str = ""):
    """Print a copy/paste prompt for using setup through an IDE or host chat."""
    normalized_host = host_hint if host_hint in _GATEWAY_VALID_USER_HOSTS else ""
    host_label = _derive_host_profile(normalized_host)["label"] if normalized_host else "the chosen AI host"
    setup_cmd = _setup_guide_command(project, "setup", "--answers-file", "./handoff.json", "--apply-answers")
    engagement_cmd = _setup_guide_command(project, "setup", "--engagement", "--answers-file", "./handoff.json", "--apply-answers")
    doctor_cmd = _setup_guide_command(project, "doctor")
    project_setup_cmd = _setup_guide_command(project, "setup-project", "--chat-guide")
    prompt = (
        "Read this project and act as the official chat-guided ControlCoding installation assistant.\n\n"
        "Important:\n"
        "- In your first visible reply, briefly explain in the user's language what ControlCoding is, what it is for, and how this setup flow will work.\n"
        "- Use the same language as the user unless the user asks otherwise.\n"
        "- Keep the intro short and onboarding-friendly, then move directly into the setup flow.\n"
        "- If git status is unknown, the first visible reply should contain that short intro and then exactly one question: whether this folder is already a git repository.\n"
        "- If git is missing, stop there and tell me to run `git init` before continuing.\n"
        "- Ask one question at a time and wait for my answer before asking the next one.\n"
        "- The actual next question must appear in the visible user-facing reply, not only in hidden reasoning, chain-of-thought, or tool metadata.\n"
        "- End each turn with exactly one explicit user-facing question.\n"
        "- Do not batch multiple setup questions into one message.\n"
        "- Do not expose raw tool-call metadata, function envelopes, shell JSON, or internal command traces in user-facing chat.\n"
        "- If the host surface leaks raw tool metadata anyway, treat it as interface noise and continue from the last meaningful setup step.\n"
        "- Translate technical setup fields into plain language when possible.\n"
        "- When asking later confirmation choices such as host, planning behavior, documentation mode, host guidance, advanced packs, consultant backend, engagement tier, or backend policy, first explain in one short sentence what the options change.\n"
        "- Do not ask a naked `confermi X?` question if the user has not just been reminded what the alternative means.\n"
        "- Present defaults as recommendations, not as forced choices. Mention at least one real alternative and when it is appropriate.\n"
        "- Do not silently choose or apply high-impact setup choices for me. High-impact choices include: project name, primary host, usage model, documentation mode, advanced packs, consultant backend, whether to stop after install or also define the project now, source brief selection, engagement tier, and backend policy.\n"
        "- You may recommend defaults for those choices, but you must ask me to confirm them before writing files or running setup.\n"
        "- A short install request is permission to start the setup flow, not permission to auto-apply all defaults.\n"
        "- Leave room for the user to choose differently; do not collapse a multi-option choice into an apparent yes/no unless the user has already clearly committed to that option.\n"
        "- Once a choice is already clear, do not replay it as a redundant confirmation loop.\n"
        "- Do not narrate internal plumbing such as checking JSON shape, preparing shell commands, or verifying the handoff format. Perform those steps silently and report only concrete outcomes or the next real question.\n"
        "- Avoid product-immature phrasing like `now I verify the expected format`. Either ask the next necessary question or report a completed result.\n"
        "- If I do not know the exact stack or architecture yet, help me choose a provisional answer and label it as provisional.\n"
        "- Do not ask banal questions such as whether I want bugs or crashes. Turn vague goals into concrete engineering defaults and ask for confirmation.\n"
        "- Do not treat the stack as predetermined just because the brief suggests a certain kind of product. First collect the framing constraints you need, then propose a short list of sensible options.\n"
        "- When I give a vague answer like `bug vari`, convert it into 3-5 concrete failure states or invariants and ask me to confirm or refine them.\n"
        "- If I only say something short like `Install ControlCoding in this project using the local repository at X`, treat that as enough to start; do not require a longer meta-prompt from me.\n"
        "- Before suggesting installation, check what is already present through obvious local discovery first.\n"
        "- If broader environment inspection would help, ask for permission before doing it.\n"
        "- Do not install anything without explicit permission.\n"
        "- If installation becomes necessary, ask whether I prefer an isolated environment, a project-local install, or a direct/system install.\n"
        "- If Python-style package installation is relevant, ask whether I prefer a `venv` before direct install.\n"
        "- This flow is only for installing and configuring ControlCoding itself. Do not collect product framing, stack, architecture, or kickoff-document answers here.\n"
        "- Project setup is a separate flow that may run only after ControlCoding installation is complete and only if I explicitly want it.\n"
        "- First finish the ControlCoding install choices: host, ControlCoding usage model, documentation mode, host guidance, advanced packs, consultant backend when debug-tools is selected, engagement tier, and backend policy.\n"
        "- Use project-local hooks as the public default. Do not ask me to choose between local and central hooks unless I explicitly ask for an advanced multi-project override.\n"
        "- Immediately after host selection, explain the host gate contract using this vocabulary: inline gate, repo boundary gate, review gate, verification gate.\n"
        "- For each gate, say clearly whether it is mechanical, conditional, advisory, or unavailable on that host.\n"
        "- If the host lacks native inline hooks, say that explicitly and do not imply Claude-style pre-write parity.\n"
        "- Ask the ControlCoding usage model early: Core, Core + manual consultation, Agents, or Studio.\n"
        "- Keep the current release freeze explicit: `Core + manual consultation` is the public manual second-opinion path, not a hidden `Agents` promise.\n"
        "- If I choose Agents or Studio, explain that specialist/runtime details will be finalized in the engagement step rather than improvising them during the base setup.\n"
        "- If I choose Agents, explain that the current release path is explicit host-chat helper roles with prompt, folder, and behavior contracts.\n"
        "- Explain that API-backed specialist routing belongs to the later Version II path, not to the current release baseline.\n"
        "- Explain that `Studio` / CC UI is a later optional extra and is not part of the current public release path.\n"
        "- Treat the likely current host as a recommendation, not as a forced choice. If you infer or are given a host hint, propose it clearly and let me choose differently.\n"
        "- If I do not care about a low-impact choice, use the recommended default and say so.\n"
        "- Keep the setup on the public baseline path: local-only CC working docs and no advanced packs unless I explicitly ask.\n"
        "- If I select debug-tools, ask for `backend_pref` explicitly. Supported values are `claude`, `ollama`, `openai`, and `anthropic`; also offer Configure later.\n"
        "- Represent Configure later in the handoff as an absent or empty `backend_pref`, never as the string `configure later`.\n"
        "- PATH and API-key discovery are informational only. Never derive `backend_pref` from discovery, the primary user host, or a launcher.\n"
        "- A supported backend may be selected even when it is not detected locally. Claude remains an optional official CLI adapter; do not reuse consumer login, OAuth, or session tokens.\n"
        "- Explain that Project Memory Engine and GraphRAG are Core defaults, local only, and initialized with governed-folder scope only unless I explicitly opt into more.\n"
        "- Ask whether to initialize local project memory now with governed-folder scope only. Recommend yes.\n"
        "- Do not run a full repository memory scan, OCR, document layout extraction, vector rebuild, file relocation, or graph promotion during base setup.\n"
        f"- If I am likely working through {host_label}, present that as the recommended primary AI host even if I am inside VS Code or another editor shell, but still let me pick another host.\n"
        "- Do not make me re-enter setup answers that were already collected in chat.\n"
        "- Preserve the exact accepted values in the handoff payload; do not silently rename the project or swap the chosen stack later.\n"
        "- After enough answers are collected, summarize the chosen values and generate a JSON handoff file payload with top-level `setup` and `engagement` objects.\n"
        "- Do not write files or run setup/apply commands until those high-impact choices are explicitly confirmed.\n"
        "- Review the completed handoff and existing-file conflicts before applying. Supplying an answers file applies immediately, even without --apply-answers; there is no setup dry-run.\n"
        f"- Commands below use {'PowerShell' if os.name == 'nt' else 'a POSIX shell'}. Run from the folder containing the reviewed handoff.json, and stop if any command returns nonzero.\n"
        f"- If this host can execute local commands, save that payload as `handoff.json`, run `{setup_cmd}`, then `{engagement_cmd}`, then `{doctor_cmd}`. Do this yourself instead of sending me to the terminal.\n"
        f"- After installation succeeds, ask me whether I want to start the separate project setup flow. Only if I explicitly say yes may you continue by collecting a new `project_setup` payload and running `{project_setup_cmd}`.\n"
        "- Only if this host truly cannot execute local commands may you fall back to asking me to run the commands manually.\n"
        "- When you finish applying the setup, review the generated files and tell me exactly what was written.\n"
        "- Do not print internal 'operational plan' narration like `I will now search X, then do Y`. Either ask the next necessary question, request permission briefly, or report a completed result.\n"
        "\n"
        "Question order:\n"
        "1. Project name\n"
        f"2. Primary AI host (default {host_label})\n"
        "3. ControlCoding usage model: Core / Core + manual consultation / Agents / Studio\n"
        "4. Documentation mode (default managed)\n"
        "5. Local project memory with governed-folder scope only (default yes)\n"
        "6. Host workflow guidance (default recommended)\n"
        "7. Advanced packs now? default no\n"
        "8. If debug-tools is selected, consultant `backend_pref`: explicit supported backend or Configure later (absent/empty)\n"
        "9. Engagement tier (default to the chosen ControlCoding usage model; only change it if I explicitly want a different runtime tier)\n"
        "10. Backend policy for engagement (for Agents or Studio, explain that current release Agents stay chat-defined and `approved` only prepares later API-backed paths)\n"
        "11. After install/doctor: ask whether I want to start the separate project setup flow\n"
    )
    print(prompt)
    return 0


def cmd_setup_project_chat_guide(project: Path, host_hint: str = ""):
    """Print a copy/paste prompt for chat-guided project setup after CC installation."""
    normalized_host = host_hint if host_hint in _GATEWAY_VALID_USER_HOSTS else ""
    host_label = _derive_host_profile(normalized_host)["label"] if normalized_host else "the chosen AI host"
    project_setup_cmd = f"python {SCRIPT_DIR / 'cc.py'} setup-project --project-root ."
    prompt = (
        "Read this project and act as the official chat-guided ControlCoding project setup assistant.\n\n"
        "Important:\n"
        "- Assume ControlCoding is already installed in this project. Verify that first through obvious local files before asking anything else.\n"
        "- If ControlCoding is missing, stop and tell me to run the base installation flow first.\n"
        "- In your first visible reply, briefly explain that this second flow is for project framing and kickoff docs, not for installing ControlCoding itself.\n"
        "- Use the same language as the user unless the user asks otherwise.\n"
        "- Ask one question at a time and wait for my answer before asking the next one.\n"
        "- The actual next question must appear in the visible reply, not only in hidden reasoning or tool metadata.\n"
        "- End each turn with exactly one explicit user-facing question.\n"
        "- Before asking me to name a brief manually, look for likely brief candidates read-only, such as `project_brief.md`, files with `brief`, `requirements`, or `spec` in the name, and the most relevant root `README.md`.\n"
        "- Do not move, rename, copy, OCR, or import candidate brief files during discovery.\n"
        "- If you find exactly one plausible brief candidate, ask me whether that file is the brief before asking for another reference.\n"
        "- If you find multiple plausible candidates, show a short list and ask me which one should be treated as the brief.\n"
        "- Also look for signs that this is already a mature repository: visible source trees, tests, docs, manifests, or multiple existing product documents.\n"
        "- If the repo already looks mature, offer an explicit brownfield adoption path instead of pretending the project is starting from scratch.\n"
        "- Treat existing brief/doc files as reusable sources when present.\n"
        "- Do not silently choose the major project-shape answers for me. You may recommend defaults, but you must ask me to confirm them before writing docs.\n"
        "- Major project-shape answers include: source mode, brief/reference file, kickoff readiness, brownfield adoption path, product form, runtime constraints, target platforms, stack direction, architecture direction, and truth/view split.\n"
        "- For each major project-shape answer, present a recommendation card before asking the question.\n"
        "- Recommendation card contract: decision, recommended option, why it fits, one real alternative, when that alternative is better, and exactly one user-facing question.\n"
        "- Do not ask naked confirmation questions such as `confirm existing_brief?`, `confermi desktop?`, or `skip for now?`.\n"
        "- If the answer is uncertain, label the recommendation as provisional and state what evidence would change it.\n"
        "\n"
        "Recommendation card examples:\n"
        + "\n\n".join(example["rendered"] for example in _setup_project_recommendation_examples())
        + "\n\n"
        "- Do not reinstall ControlCoding, do not rerun base setup, and do not revisit the host/runtime install questions unless I explicitly ask.\n"
        "- Preserve the exact accepted values in the handoff payload.\n"
        "- After enough answers are collected, summarize the chosen values and generate a JSON handoff file payload with top-level `project_setup`.\n"
        f"- If this host can execute local commands, save that payload as `handoff.json` and run `{project_setup_cmd}` with `--answers-file .\\handoff.json --apply-answers`.\n"
        "- When you finish applying the project setup, tell me exactly which kickoff docs were written or refreshed.\n"
        "\n"
        f"- If I am likely working through {host_label}, keep the explanations compatible with that host, but do not revisit the install host selection itself.\n"
        "\n"
        "Question order:\n"
        "1. Choose source mode: existing brief/doc, interactive guidance, or adopt an existing mature repo\n"
        "2. If brownfield looks likely, confirm whether this should be treated as an existing-project adoption pass\n"
        "3. How prepared is the source right now: idea / partial spec / existing design\n"
        "4. Existing brief or reference file when relevant\n"
        "5. Project idea / vision or current product purpose\n"
        "6. Primary users and main useful flow\n"
        "7. Must-have v1 features or current must-keep capabilities\n"
        "8. Correctness rules / must-never-happen states\n"
        "9. Quality constraints\n"
        "10. Out-of-scope items or anti-goals\n"
        "11. If adopting an existing repo: authoritative docs, implemented scope, stable areas, known drift, and current verification signals\n"
        "12. Product form and delivery/runtime constraints\n"
        "13. Target platforms, tooling tolerance, and fastest-iteration vs low-level-control preference\n"
        "14. Implementation stack direction\n"
        "15. System shape / architecture direction\n"
        "16. Truth representation if relevant\n"
        "17. View representation if relevant\n"
    )
    print(prompt)
    return 0


def cmd_setup_engagement(project: Path, answers_file: Path | None = None, apply_answers: bool = False):
    """Product-tier/runtime configuration, optionally applied directly from a handoff file."""
    apply_answers, early_exit = _require_chat_or_answers_file(
        "cc setup --engagement",
        "--chat-guide",
        answers_file,
        apply_answers,
    )
    if early_exit is not None:
        return early_exit
    # Import engagement utilities
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from control_plane_utils import (
            ENGAGEMENT_LEVELS, ENGAGEMENT_DEFAULTS,
            ENGAGEMENT_SCHEMA_VERSION,
            PRODUCT_TIER_ORDER,
            PRODUCT_TIER_PROFILES,
            assess_local_tandem_capacity,
            best_effort_tier,
            format_local_tandem_assessment,
            get_active_components,
            legacy_level_for_tier,
            load_engagement, save_engagement,
            normalize_specialist_paths,
        )
    except ImportError:
        print("Error: control_plane_utils not found in templates/scripts/")
        return 1

    config_path = str(_control_plane_path(project, "cc_engagement.json"))
    config_exists = _control_plane_read_path(project, "cc_engagement.json").exists()

    print(f"\n{'='*50}")
    print("  ControlCoding Tier Setup Apply")
    print(f"{'='*50}\n")

    # Load existing config if present
    existing = load_engagement(config_path)
    existing_cc_config = _load_json_object(_control_plane_read_path(project, "cc_config.json"))
    existing_planning = existing_cc_config.get("planning", {})
    if not isinstance(existing_planning, dict):
        existing_planning = {}
    prefill = _load_answers_payload(answers_file, "engagement", strict_section=apply_answers)
    if apply_answers and not prefill:
        warn("`--apply-answers` requires a valid answers file with an `engagement` payload.")
        return 1
    if answers_file is not None and prefill:
        info(f"Using prefilled engagement defaults from {answers_file}")
    if apply_answers:
        info("Applying engagement directly from the answers file.")
    current_tier = best_effort_tier(existing) if config_exists else "core"
    current_level = existing.get("level", ENGAGEMENT_DEFAULTS["level"])
    if prefill.get("tier") in PRODUCT_TIER_ORDER:
        current_tier = str(prefill["tier"])
    elif existing_planning.get("tier") in PRODUCT_TIER_ORDER:
        current_tier = str(existing_planning["tier"])

    # Q1: Product tier
    tier = _ask_engagement_tier(current_tier, apply_answers=apply_answers)
    legacy_level = legacy_level_for_tier(tier)

    print(f"\n  Planning behavior for kickoff/design:\n")
    if tier == "core":
        default_manual = bool(
            prefill.get("manual_consultation_allowed", existing.get("manual_consultation_allowed",
                existing_planning.get("manual_consultation_allowed", False)))
        )
        print("    [1] Direct planning in the main chat only")
        print("    [2] Direct planning + manual consultation if needed")
        planning_choice = _ask_or_default("Planning behavior", "2" if default_manual else "1", apply_answers=apply_answers)
        planning = _planning_profile_for_tier("core", planning_choice == "2")
    else:
        planning = _planning_profile_for_tier(tier, False)
        info(
            "Selected tier uses specialist-assisted planning: "
            + _planning_summary_line(planning)
        )

    # Q2: Backend policy
    current_bp = str(prefill.get("backend_policy", existing.get("backend_policy", "local_only")))
    backend_policy = _ask_backend_policy(current_bp, tier=tier, apply_answers=apply_answers)

    # Q3: Budget
    print(f"\n  Budget limits LLM calls per session.")
    print("    0 = unlimited")
    prefill_budget = prefill.get("budget_policy", {})
    if not isinstance(prefill_budget, dict):
        prefill_budget = {}
    current_max = prefill_budget.get("max_calls", existing.get("budget_policy", {}).get("max_calls", 0))
    max_calls_str = _ask_or_default("Max LLM calls per session", str(current_max), apply_answers=apply_answers)
    try:
        max_calls = int(max_calls_str)
        if max_calls < 0:
            max_calls = 0
    except ValueError:
        max_calls = current_max

    tandem_config = (
        dict(existing.get("tandem"))
        if isinstance(existing.get("tandem"), dict)
        else {}
    )
    if isinstance(prefill.get("tandem"), dict):
        tandem_config.update(prefill["tandem"])
    if not str(tandem_config.get("backend_a", "")).strip() or not str(tandem_config.get("backend_b", "")).strip():
        tandem_config = {
            "mode": "off",
            "backend_a": "",
            "backend_b": "",
            "model_a": "",
            "model_b": "",
        }

    current_specialist_paths = normalize_specialist_paths(
        prefill.get("specialist_paths", existing.get("specialist_paths", []))
    )
    specialist_paths = _collect_specialist_paths(
        current_specialist_paths,
        tier=tier,
        backend_policy=backend_policy,
        tandem_config=tandem_config,
        apply_answers=apply_answers,
    )
    if tier in {"agents", "studio"}:
        if not specialist_paths:
            warn(
                "Agents/Studio requires an explicit specialist/backend consent matrix. "
                "At least one active specialist path must be recorded."
            )
            return 1
        invalid_paths = [
            path["label"]
            for path in specialist_paths
            if not str(path.get("backend", "")).strip()
        ]
        if invalid_paths:
            warn(
                "Active specialist paths are missing a backend/API label: "
                + ", ".join(invalid_paths)
            )
            return 1

    # Build config
    config = {
        "schema_version": ENGAGEMENT_SCHEMA_VERSION,
        "tier": tier,
        "level": legacy_level,
        "ui_intent": PRODUCT_TIER_PROFILES[tier]["ui_intent"],
        "planning_mode": planning["planning_mode"],
        "planning_authority": planning["planning_authority"],
        "manual_consultation_allowed": planning["manual_consultation_allowed"],
        "backend_policy": backend_policy,
        "tandem": tandem_config,
        "budget_policy": {
            "max_calls": max_calls,
            "exhaustion_behavior": "degrade",
        },
        "specialist_paths": specialist_paths,
    }

    # Confirm
    print(f"\n--- Summary ---\n")
    print(f"  Tier: {PRODUCT_TIER_PROFILES[tier]['label']}")
    print(
        f"  Legacy runtime: level {legacy_level} "
        f"({ENGAGEMENT_LEVELS[legacy_level]})"
    )
    print(f"  UI intent: {PRODUCT_TIER_PROFILES[tier]['ui_intent']}")
    print(f"  Planning behavior: {_planning_summary_line(planning)}")
    print(f"  Manual consultation allowed: {'yes' if planning['manual_consultation_allowed'] else 'no'}")
    print(f"  Backend: {backend_policy}")
    print(f"  Budget: {'unlimited' if max_calls == 0 else f'{max_calls} calls'}")
    if tier in {"agents", "studio"}:
        print("  Specialist/backend consent matrix:")
        for line in _format_specialist_matrix_lines(specialist_paths, max_calls):
            print(f"    - {line}")
    active_components = sorted(get_active_components(config=config))
    if active_components:
        print(f"  Active components: {', '.join(active_components)}")
    if tandem_config.get("mode") != "off":
        print(
            "  Tandem backends: "
            f"{tandem_config.get('backend_a', '')} + {tandem_config.get('backend_b', '')}"
        )
    print()

    if not _ask_yn_or_default("Save engagement configuration?", default=True, apply_answers=apply_answers):
        print("\nCancelled.")
        return 0

    save_engagement(config, config_path)
    ok(f"Saved engagement config to {_control_plane_display_path('cc_engagement.json')}")
    print(
        f"\n  {PRODUCT_TIER_PROFILES[tier]['label']} will be active in your next session."
    )
    if tier == "core":
        info("Core keeps the framework baseline active while leaving specialist-agent workflows disabled.")
        if planning["manual_consultation_allowed"]:
            info("Core manual consultation is enabled for planning: second opinions stay explicit and user-mediated.")
    return 0
