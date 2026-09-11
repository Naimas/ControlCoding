"""Explicit OCR/layout extraction adapter interface for project memory."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .schema import CONTROL_DIRNAME, MEMORY_DIRNAME
from .store import _print_json_error_or_text, _print_json_or_text, _read_json, _relative_path, _require_initialized, _write_json

OCR_ADAPTER_INTERFACE_VERSION = "cc-ocr-adapter/v1"
OCR_ADAPTER_CONFIG_FILENAME = "ocr_adapters.json"
OCR_ADAPTER_DEFAULT_SOURCE_TYPES = ["pdf", "png", "jpg", "jpeg", "tif", "tiff"]


def _runtime_command_args(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return shlex.split(value, posix=os.name != "nt")
    return []


def _ocr_config_path(project: Path) -> Path:
    return project / CONTROL_DIRNAME / MEMORY_DIRNAME / OCR_ADAPTER_CONFIG_FILENAME


def _ocr_config(project: Path) -> dict[str, Any]:
    config = _read_json(_ocr_config_path(project))
    return config if isinstance(config, dict) else {}


def _command_executable(command_args: list[str]) -> bool:
    if not command_args:
        return False
    executable = command_args[0]
    if Path(executable).is_absolute() or "/" in executable or "\\" in executable:
        return Path(executable).exists()
    return shutil.which(executable) is not None


def _timeout_seconds(config: dict[str, Any]) -> tuple[int, str]:
    try:
        timeout = int(config.get("timeoutSeconds") or 60)
    except (TypeError, ValueError):
        return 60, "timeoutSeconds must be an integer"
    if timeout < 1 or timeout > 600:
        return max(1, min(timeout, 600)), "timeoutSeconds must be between 1 and 600"
    return timeout, ""


def _local_runtime_status(config: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(config.get("enabled"))
    command_args = _runtime_command_args(config.get("command"))
    command_executable = _command_executable(command_args)
    timeout, timeout_issue = _timeout_seconds(config)
    accepted_source_types = config.get("acceptedSourceTypes")
    if not isinstance(accepted_source_types, list) or not accepted_source_types:
        accepted_source_types = OCR_ADAPTER_DEFAULT_SOURCE_TYPES
    accepted_source_types = [
        str(item).strip().lower().lstrip(".")
        for item in accepted_source_types
        if str(item).strip()
    ]
    if not accepted_source_types:
        accepted_source_types = OCR_ADAPTER_DEFAULT_SOURCE_TYPES
    issues = []
    if enabled and not command_args:
        issues.append("localRuntime.command is required when enabled")
    if enabled and command_args and not command_executable:
        issues.append("localRuntime.command executable was not found")
    if timeout_issue:
        issues.append(timeout_issue)
    return {
        "id": "local_runtime_v1",
        "kind": "local_runtime",
        "configured": enabled and bool(command_args),
        "available": enabled and bool(command_args) and command_executable and not issues,
        "externalRuntime": True,
        "network": False,
        "commandConfigured": bool(command_args),
        "commandExecutable": command_executable,
        "timeoutSeconds": timeout,
        "acceptedSourceTypes": accepted_source_types,
        "configurationIssues": issues,
        "notes": [
            "Optional OCR adapter is considered available only when explicitly enabled with a command.",
            "Status inspection does not execute the configured command.",
            "The adapter must return project-owned layout sidecar JSON on stdout.",
        ],
    }


def ocr_adapter_status_payload(project: Path) -> dict[str, Any]:
    config = _ocr_config(project)
    local_runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    adapter = _local_runtime_status(local_runtime_config)
    active = str(config.get("activeAdapter") or "")
    active_adapter = active if active == adapter["id"] and adapter["available"] else ""
    return {
        "ok": True,
        "interfaceVersion": OCR_ADAPTER_INTERFACE_VERSION,
        "configPath": _relative_path(project, _ocr_config_path(project)),
        "hasConfig": _ocr_config_path(project).exists(),
        "activeAdapter": active_adapter,
        "adapters": [adapter],
        "policy": {
            "explicitOnly": True,
            "statusCommandExecutesAdapters": False,
            "writesSidecarOnly": True,
            "scanAfterRunRequired": True,
            "consumerLoginReuseAllowed": False,
        },
    }


def _ocr_status_text(payload: dict[str, Any]) -> str:
    lines = [
        "Memory OCR adapter status",
        f"  Interface: {payload['interfaceVersion']}",
        f"  Config: {payload['configPath']} ({'present' if payload['hasConfig'] else 'missing'})",
        f"  Active adapter: {payload['activeAdapter'] or '(none)'}",
        "  Policy: OCR execution requires explicit local runtime configuration",
        "  Adapters:",
    ]
    for adapter in payload["adapters"]:
        lines.append(
            f"    {adapter['id']}: kind={adapter['kind']}, configured={adapter['configured']}, available={adapter['available']}"
        )
    return "\n".join(lines)


def _resolve_project_path(project: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project / path
    return path.resolve()


def _require_inside_project(project: Path, path: Path) -> tuple[bool, str]:
    try:
        path.resolve().relative_to(project.resolve())
    except ValueError:
        return False, f"path must stay inside project root: {path}"
    return True, ""


def _default_ocr_sidecar_path(source_path: Path) -> Path:
    return source_path.with_name(source_path.name + ".ocr.json")


def _source_type_for_path(path: Path) -> str:
    return path.suffix.lower().lstrip(".") or "document"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _validated_sidecar_payload(payload: Any, source_rel: str) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict):
        return None, "adapter output must be a JSON object"
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return None, "adapter output must include a top-level pages list"
    provided_source = str(payload.get("sourcePath") or payload.get("source_path") or "").strip()
    if provided_source and provided_source.replace("\\", "/") != source_rel:
        return None, "adapter output sourcePath must match the requested source"
    normalized = dict(payload)
    normalized.setdefault("schemaVersion", 1)
    normalized.setdefault("sourcePath", source_rel)
    normalized.setdefault("sourceType", _source_type_for_path(Path(source_rel)))
    normalized.setdefault("extractionMethod", "ocr_adapter")
    normalized.setdefault("coordinateSystem", "adapter_output")
    return normalized, ""


def _run_local_ocr_adapter(
    project: Path,
    source_path: Path,
    source_rel: str,
    output_path: Path,
) -> tuple[dict[str, Any] | None, str]:
    config = _ocr_config(project)
    if str(config.get("activeAdapter") or "") != "local_runtime_v1":
        return None, "active OCR adapter is not local_runtime_v1"
    local_runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    status = _local_runtime_status(local_runtime_config)
    if not status["available"]:
        issues = "; ".join(status.get("configurationIssues", []))
        suffix = f": {issues}" if issues else ""
        return None, "local OCR runtime adapter is not available" + suffix
    command_args = _runtime_command_args(local_runtime_config.get("command"))
    timeout = int(status["timeoutSeconds"])
    source_type = _source_type_for_path(source_path)
    accepted_source_types = set(status.get("acceptedSourceTypes", []))
    if accepted_source_types and source_type not in accepted_source_types:
        return None, f"source type is not accepted by the OCR adapter: {source_type}"
    payload = {
        "interfaceVersion": OCR_ADAPTER_INTERFACE_VERSION,
        "adapter": "local_runtime_v1",
        "projectRoot": str(project.resolve()),
        "sourcePath": source_rel,
        "sourceAbsolutePath": str(source_path),
        "sourceType": source_type,
        "sourceSizeBytes": source_path.stat().st_size,
        "sourceSha256": _file_sha256(source_path),
        "outputPath": _relative_path(project, output_path),
        "outputAbsolutePath": str(output_path),
        "requestedOutput": "layout_sidecar_json",
        "sidecarSchema": "cc-layout-sidecar/v1",
        "acceptedSourceTypes": sorted(accepted_source_types),
    }
    try:
        result = subprocess.run(
            command_args,
            cwd=project,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or f"runtime exited {result.returncode}").strip()
        return None, reason[:500]
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return None, f"invalid OCR adapter JSON: {exc}"
    return _validated_sidecar_payload(parsed, source_rel)


def cmd_memory_ocr_status(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_ocr_memory_not_initialized", message)
        return 1
    payload = ocr_adapter_status_payload(project)
    _print_json_or_text(json_output, payload, _ocr_status_text(payload))
    return 0


def cmd_memory_ocr_run(
    project: Path,
    source: str,
    output: str | Path | None = None,
    force: bool = False,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_ocr_memory_not_initialized", message)
        return 1
    source_path = _resolve_project_path(project, source)
    inside, reason = _require_inside_project(project, source_path)
    if not inside:
        _print_json_error_or_text(json_output, "ocr_source_outside_project", reason)
        return 1
    if not source_path.exists() or not source_path.is_file():
        _print_json_error_or_text(json_output, "ocr_source_not_found", f"source file not found: {_relative_path(project, source_path)}")
        return 1
    output_path = _resolve_project_path(project, output) if output else _default_ocr_sidecar_path(source_path)
    inside, reason = _require_inside_project(project, output_path)
    if not inside:
        _print_json_error_or_text(json_output, "ocr_output_outside_project", reason)
        return 1
    if output_path.exists() and not force:
        _print_json_error_or_text(json_output, "ocr_output_exists", "output sidecar already exists. Pass --force to overwrite it.")
        return 1
    source_rel = _relative_path(project, source_path)
    sidecar_payload, error = _run_local_ocr_adapter(project, source_path, source_rel, output_path)
    if sidecar_payload is None:
        _print_json_error_or_text(json_output, "ocr_adapter_failed", str(error or "OCR adapter failed"))
        return 1
    _write_json(output_path, sidecar_payload)
    payload = {
        "ok": True,
        "sourcePath": source_rel,
        "outputPath": _relative_path(project, output_path),
        "adapter": "local_runtime_v1",
        "force": bool(force),
        "scanRequired": True,
        "nextCommand": "python scripts/cc.py memory scan --project-root .",
    }
    text = (
        "Memory OCR adapter run complete\n"
        f"  Source: {payload['sourcePath']}\n"
        f"  Output: {payload['outputPath']}\n"
        "  Next: python scripts/cc.py memory scan --project-root ."
    )
    _print_json_or_text(json_output, payload, text)
    return 0


__all__ = [
    "OCR_ADAPTER_INTERFACE_VERSION",
    "cmd_memory_ocr_run",
    "cmd_memory_ocr_status",
    "ocr_adapter_status_payload",
]
