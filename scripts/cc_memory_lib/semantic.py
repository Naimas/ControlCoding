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
        return {
            "id": self.adapter_id,
            "kind": self.kind,
            "configured": enabled and bool(command_args),
            "available": enabled and bool(command_args),
            "default": False,
            "externalRuntime": True,
            "network": False,
            "commandConfigured": bool(command_args),
            "timeoutSeconds": int(self.config.get("timeoutSeconds") or SEMANTIC_LOCAL_RUNTIME_DEFAULT_TIMEOUT_SECONDS),
            "maxCandidates": int(self.config.get("maxCandidates") or SEMANTIC_LOCAL_RUNTIME_DEFAULT_MAX_CANDIDATES),
            "scoring": {
                "scoreMultiplier": SEMANTIC_LOCAL_RUNTIME_SCORE_MULTIPLIER,
                "maxCandidatesLimit": SEMANTIC_LOCAL_RUNTIME_MAX_CANDIDATES_LIMIT,
            },
            "notes": [
                "Optional local runtime adapter is considered available only when explicitly enabled with a command.",
                "Status inspection does not execute the configured command.",
            ],
        }

    def score(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        status = self.status()
        if not status["available"]:
            return {"used": False, "adapter": self.adapter_id, "reason": "local runtime adapter is not available", "scores": {}}
        command_args = _runtime_command_args(self.config.get("command"))
        timeout = max(
            1,
            min(
                int(self.config.get("timeoutSeconds") or SEMANTIC_LOCAL_RUNTIME_DEFAULT_TIMEOUT_SECONDS),
                SEMANTIC_LOCAL_RUNTIME_MAX_TIMEOUT_SECONDS,
            ),
        )
        max_candidates = max(
            1,
            min(
                int(self.config.get("maxCandidates") or SEMANTIC_LOCAL_RUNTIME_DEFAULT_MAX_CANDIDATES),
                SEMANTIC_LOCAL_RUNTIME_MAX_CANDIDATES_LIMIT,
            ),
        )
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
            "available": configured and env_available,
            "default": False,
            "externalRuntime": True,
            "network": True,
            "explicitOnly": True,
            "provider": provider,
            "model": model,
            "apiKeyEnv": api_key_env,
            "apiKeyAvailable": env_available,
            "notes": [
                "Official API adapter is disabled unless explicitly configured.",
                "This registry does not reuse consumer login sessions or tokens.",
                "Runtime API calls must use official APIs and project-approved configuration.",
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
    active = LOCAL_SPARSE_ADAPTER
    for adapter in adapters:
        if adapter["id"] != LOCAL_SPARSE_ADAPTER and adapter["available"] and config.get("activeAdapter") == adapter["id"]:
            active = str(adapter["id"])
            break
    return {
        "ok": True,
        "interfaceVersion": SEMANTIC_ADAPTER_INTERFACE_VERSION,
        "configPath": _relative_path(project, _semantic_config_path(project)),
        "hasConfig": _semantic_config_path(project).exists(),
        "activeAdapter": active,
        "defaultAdapter": LOCAL_SPARSE_ADAPTER,
        "adapters": adapters,
        "policy": {
            "localSparseDefault": True,
            "officialApiExplicitOnly": True,
            "consumerLoginReuseAllowed": False,
            "statusCommandExecutesAdapters": False,
        },
    }


def semantic_score_candidates(project: Path, query: str, candidates: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    config = _semantic_config(project)
    local_runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    official_api_config = config.get("officialApi") if isinstance(config.get("officialApi"), dict) else {}
    active = str(config.get("activeAdapter") or LOCAL_SPARSE_ADAPTER)
    if active == "local_runtime_v1":
        result = LocalRuntimeSemanticAdapter(local_runtime_config).score(query, candidates)
    elif active == "official_api_v1":
        result = OfficialApiSemanticAdapter(official_api_config).score(query, candidates)
    else:
        result = LocalSparseSemanticAdapter().score(query, candidates)
    scores = result.get("scores", {}) if isinstance(result.get("scores"), dict) else {}
    report = {key: value for key, value in result.items() if key != "scores"}
    report.setdefault("adapter", active)
    report.setdefault("used", False)
    return scores, report


def _semantic_status_text(payload: dict[str, Any]) -> str:
    lines = [
        "Memory semantic adapter status",
        f"  Interface: {payload['interfaceVersion']}",
        f"  Config: {payload['configPath']} ({'present' if payload['hasConfig'] else 'missing'})",
        f"  Active adapter: {payload['activeAdapter']}",
        f"  Default adapter: {payload['defaultAdapter']}",
        "  Policy: official API adapters require explicit project configuration",
        "  Adapters:",
    ]
    for adapter in payload["adapters"]:
        lines.append(
            f"    {adapter['id']}: kind={adapter['kind']}, configured={adapter['configured']}, available={adapter['available']}"
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
