"""Optional semantic adapter registry for project memory retrieval."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Protocol

from .scoring import DEFAULT_RETRIEVAL_SCORING
from .schema import CONTROL_DIRNAME, MEMORY_DIRNAME
from .store import _print_json_error_or_text, _print_json_or_text, _read_json, _relative_path, _require_initialized
from .vector import LOCAL_SPARSE_ADAPTER

SEMANTIC_ADAPTER_INTERFACE_VERSION = "cc-semantic-adapter/v1"
SEMANTIC_ADAPTER_CONFIG_FILENAME = "semantic_adapters.json"
SEMANTIC_LOCAL_RUNTIME_DEFAULT_TIMEOUT_SECONDS = DEFAULT_RETRIEVAL_SCORING.semantic_runtime.default_timeout_seconds
SEMANTIC_LOCAL_RUNTIME_MAX_TIMEOUT_SECONDS = DEFAULT_RETRIEVAL_SCORING.semantic_runtime.max_timeout_seconds
SEMANTIC_LOCAL_RUNTIME_DEFAULT_MAX_CANDIDATES = DEFAULT_RETRIEVAL_SCORING.semantic_runtime.default_max_candidates
SEMANTIC_LOCAL_RUNTIME_MAX_CANDIDATES_LIMIT = DEFAULT_RETRIEVAL_SCORING.semantic_runtime.max_candidates_limit
SEMANTIC_LOCAL_RUNTIME_SCORE_MULTIPLIER = DEFAULT_RETRIEVAL_SCORING.semantic_runtime.score_multiplier


def _effective_local_runtime_limit(value: Any, default: int, maximum: int) -> int:
    return max(1, min(int(value or default), maximum))


class SemanticAdapter(Protocol):
    """Minimal interface for optional semantic retrieval adapters."""

    adapter_id: str
    kind: str

    def status(self) -> dict[str, Any]:
        """Return adapter availability and configuration status."""
        ...

    def score(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Return semantic scores keyed to candidate ids."""
        ...


class LocalSparseSemanticAdapter:
    adapter_id = LOCAL_SPARSE_ADAPTER
    kind = "local_sparse"

    def status(self) -> dict[str, Any]:
        return {
            "id": self.adapter_id,
            "kind": self.kind,
            "configured": True,
            "executionSupported": True,
            "executionEligible": True,
            "available": True,
            "default": True,
            "externalRuntime": False,
            "network": False,
            "notes": [
                "Default adapter uses the rebuildable local sparse vector index.",
                "No external process, network, or API key is required.",
            ],
        }

    def score(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "used": False,
            "adapter": self.adapter_id,
            "reason": "local sparse scoring is already included through the vector index",
            "scores": {},
        }


class LocalRuntimeSemanticAdapter:
    adapter_id = "local_runtime_v1"
    kind = "local_runtime"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def status(self) -> dict[str, Any]:
        enabled = bool(self.config.get("enabled"))
        command_args = _runtime_command_args(self.config.get("command"))
        configured = enabled and bool(command_args)
        timeout_seconds = _effective_local_runtime_limit(
            self.config.get("timeoutSeconds"),
            SEMANTIC_LOCAL_RUNTIME_DEFAULT_TIMEOUT_SECONDS,
            SEMANTIC_LOCAL_RUNTIME_MAX_TIMEOUT_SECONDS,
        )
        max_candidates = _effective_local_runtime_limit(
            self.config.get("maxCandidates"),
            SEMANTIC_LOCAL_RUNTIME_DEFAULT_MAX_CANDIDATES,
            SEMANTIC_LOCAL_RUNTIME_MAX_CANDIDATES_LIMIT,
        )
        return {
            "id": self.adapter_id,
            "kind": self.kind,
            "configured": configured,
            "executionSupported": True,
            "executionEligible": configured,
            "available": configured,
            "default": False,
            "externalRuntime": True,
            "network": None,
            "networkAccess": "unknown",
            "networkRestrictionsImposed": False,
            "environmentIsolationImposed": False,
            "workingDirectoryIsolationImposed": False,
            "commandConfigured": bool(command_args),
            "timeoutSeconds": timeout_seconds,
            "maxCandidates": max_candidates,
            "scoring": {
                "scoreMultiplier": SEMANTIC_LOCAL_RUNTIME_SCORE_MULTIPLIER,
                "maxCandidatesLimit": SEMANTIC_LOCAL_RUNTIME_MAX_CANDIDATES_LIMIT,
            },
            "requestFields": {
                "topLevel": ["interfaceVersion", "adapter", "query", "candidates"],
                "candidate": ["id", "recordType", "type", "title", "path", "headingPath", "lifecycle", "text"],
            },
            "notes": [
                "Available means eligible for an execution attempt when explicitly enabled with a command; it does not prove that the command exists or will succeed.",
                "Status inspection does not execute the configured command.",
                "ControlCoding does not impose network, environment, or working-directory isolation on the child command.",
            ],
        }

    def score(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        status = self.status()
        if not status["available"]:
            return {"used": False, "adapter": self.adapter_id, "reason": "local runtime adapter is not available", "scores": {}}
        command_args = _runtime_command_args(self.config.get("command"))
        timeout = status["timeoutSeconds"]
        max_candidates = status["maxCandidates"]
        payload = {
            "interfaceVersion": SEMANTIC_ADAPTER_INTERFACE_VERSION,
            "adapter": self.adapter_id,
            "query": query,
            "candidates": candidates[:max_candidates],
        }
        try:
            result = subprocess.run(
                command_args,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"used": False, "adapter": self.adapter_id, "reason": str(exc), "scores": {}}
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or f"runtime exited {result.returncode}").strip()
            return {"used": False, "adapter": self.adapter_id, "reason": reason[:500], "scores": {}}
        try:
            parsed = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            return {"used": False, "adapter": self.adapter_id, "reason": f"invalid runtime JSON: {exc}", "scores": {}}
        raw_scores = parsed.get("scores", []) if isinstance(parsed, dict) else []
        scores: dict[str, dict[str, Any]] = {}
        if isinstance(raw_scores, list):
            for item in raw_scores:
                if not isinstance(item, dict):
                    continue
                candidate_id = str(item.get("id") or "")
                if not candidate_id:
                    continue
                try:
                    raw_score = float(item.get("score") or 0.0)
                except (TypeError, ValueError):
                    raw_score = 0.0
                score = max(0.0, min(raw_score, 1.0))
                if score <= 0:
                    continue
                scores[candidate_id] = {
                    "rawScore": round(score, 6),
                    "points": round(score * SEMANTIC_LOCAL_RUNTIME_SCORE_MULTIPLIER, 4),
                    "reasons": [str(item.get("reason") or f"local runtime semantic score {score:.3f}")],
                }
        return {
            "used": True,
            "adapter": self.adapter_id,
            "candidateCount": len(payload["candidates"]),
            "matchedEntries": len(scores),
            "scores": scores,
        }


class OfficialApiSemanticAdapter:
    adapter_id = "official_api_v1"
    kind = "official_api"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def status(self) -> dict[str, Any]:
        enabled = bool(self.config.get("enabled"))
        provider = str(self.config.get("provider") or "").strip()
        model = str(self.config.get("model") or "").strip()
        api_key_env = str(self.config.get("apiKeyEnv") or "").strip()
        configured = enabled and bool(provider and model and api_key_env)
        env_available = bool(api_key_env and os.environ.get(api_key_env))
        return {
            "id": self.adapter_id,
            "kind": self.kind,
            "configured": configured,
            "executionSupported": False,
            "executionEligible": False,
            "available": False,
            "unavailableReason": "official API semantic scoring is not implemented in this build",
            "default": False,
            "externalRuntime": True,
            "network": True,
            "explicitOnly": True,
            "provider": provider,
            "model": model,
            "apiKeyEnv": api_key_env,
            "apiKeyAvailable": env_available,
            "notes": [
                "Configuration and environment-variable presence do not make API scoring available in this build or validate credentials.",
                "This registry does not reuse consumer login sessions or tokens.",
                "No API request is implemented or executed by this adapter.",
            ],
        }

    def score(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "used": False,
            "adapter": self.adapter_id,
            "reason": "official API semantic scoring is explicit-only and not executed by this build",
            "scores": {},
        }


def _runtime_command_args(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return shlex.split(value, posix=os.name != "nt")
    return []


def _semantic_config_path(project: Path) -> Path:
    return project / CONTROL_DIRNAME / MEMORY_DIRNAME / SEMANTIC_ADAPTER_CONFIG_FILENAME


def _semantic_config(project: Path) -> dict[str, Any]:
    config = _read_json(_semantic_config_path(project))
    return config if isinstance(config, dict) else {}


def semantic_adapter_status_payload(project: Path) -> dict[str, Any]:
    config = _semantic_config(project)
    local_runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    official_api_config = config.get("officialApi") if isinstance(config.get("officialApi"), dict) else {}
    adapters = [
        LocalSparseSemanticAdapter().status(),
        LocalRuntimeSemanticAdapter(local_runtime_config).status(),
        OfficialApiSemanticAdapter(official_api_config).status(),
    ]
    requested = str(config.get("activeAdapter") or LOCAL_SPARSE_ADAPTER)
    selected = next((adapter for adapter in adapters if adapter["id"] == requested), None)
    if selected is None:
        active = ""
        selection_reason = f"requested semantic adapter is unknown: {requested}"
    elif selected["executionEligible"]:
        active = requested
        selection_reason = "requested semantic adapter is eligible for execution"
    else:
        active = ""
        selection_reason = str(
            selected.get("unavailableReason")
            or "requested semantic adapter is not configured or eligible for execution"
        )
    return {
        "ok": True,
        "interfaceVersion": SEMANTIC_ADAPTER_INTERFACE_VERSION,
        "configPath": _relative_path(project, _semantic_config_path(project)),
        "hasConfig": _semantic_config_path(project).exists(),
        "requestedAdapter": requested,
        "activeAdapter": active,
        "selectionReason": selection_reason,
        "defaultAdapter": LOCAL_SPARSE_ADAPTER,
        "adapters": adapters,
        "policy": {
            "localSparseDefault": True,
            "officialApiExplicitOnly": True,
            "consumerLoginReuseAllowed": False,
            "statusCommandExecutesAdapters": False,
            "availableMeansExecutionEligible": True,
        },
    }


def semantic_score_candidates(project: Path, query: str, candidates: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    config = _semantic_config(project)
    local_runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    official_api_config = config.get("officialApi") if isinstance(config.get("officialApi"), dict) else {}
    requested = str(config.get("activeAdapter") or LOCAL_SPARSE_ADAPTER)
    adapters: dict[str, SemanticAdapter] = {
        LOCAL_SPARSE_ADAPTER: LocalSparseSemanticAdapter(),
        "local_runtime_v1": LocalRuntimeSemanticAdapter(local_runtime_config),
        "official_api_v1": OfficialApiSemanticAdapter(official_api_config),
    }
    selected = adapters.get(requested)
    selected_status = selected.status() if selected is not None else None
    active = requested if selected_status and selected_status["executionEligible"] else ""
    attempted = ""
    if active == "local_runtime_v1":
        attempted = active
        result = LocalRuntimeSemanticAdapter(local_runtime_config).score(query, candidates)
    elif active == LOCAL_SPARSE_ADAPTER:
        result = LocalSparseSemanticAdapter().score(query, candidates)
    elif selected_status is None:
        result = {
            "used": False,
            "adapter": requested,
            "reason": f"requested semantic adapter is unknown: {requested}",
            "scores": {},
        }
    else:
        result = {
            "used": False,
            "adapter": requested,
            "reason": str(
                selected_status.get("unavailableReason")
                or "requested semantic adapter is not configured or eligible for execution"
            ),
            "scores": {},
        }
    scores = result.get("scores", {}) if isinstance(result.get("scores"), dict) else {}
    report = {key: value for key, value in result.items() if key != "scores"}
    report.setdefault("adapter", requested)
    report.setdefault("used", False)
    report["requestedAdapter"] = requested
    report["activeAdapter"] = active
    report["attemptedAdapter"] = attempted
    report["executionAttempted"] = bool(attempted)
    return scores, report


def _semantic_status_text(payload: dict[str, Any]) -> str:
    lines = [
        "Memory semantic adapter status",
        f"  Interface: {payload['interfaceVersion']}",
        f"  Config: {payload['configPath']} ({'present' if payload['hasConfig'] else 'missing'})",
        f"  Requested adapter: {payload['requestedAdapter']}",
        f"  Active adapter: {payload['activeAdapter']}",
        f"  Default adapter: {payload['defaultAdapter']}",
        f"  Selection: {payload['selectionReason']}",
        "  Policy: available means execution-eligible; status never executes adapters",
        "  Adapters:",
    ]
    for adapter in payload["adapters"]:
        lines.append(
            f"    {adapter['id']}: kind={adapter['kind']}, configured={adapter['configured']}, "
            f"supported={adapter['executionSupported']}, eligible={adapter['executionEligible']}, "
            f"available={adapter['available']}"
        )
        if adapter.get("unavailableReason"):
            lines.append(f"      Reason: {adapter['unavailableReason']}")
        if adapter["id"] == "local_runtime_v1":
            lines.append(
                f"      Effective limits: maxCandidates={adapter['maxCandidates']}, "
                f"timeoutSeconds={adapter['timeoutSeconds']}"
            )
            lines.append(
                "      Network access: unknown; ControlCoding imposes no network, environment, or working-directory isolation"
            )
            lines.append(
                "      Request: query plus bounded candidate id, recordType, type, title, path, headingPath, lifecycle, and text"
            )
    return "\n".join(lines)


def cmd_memory_semantic_status(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_semantic_memory_not_initialized", message)
        return 1
    payload = semantic_adapter_status_payload(project)
    _print_json_or_text(json_output, payload, _semantic_status_text(payload))
    return 0
