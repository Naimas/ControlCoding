"""Privacy scrub helpers for local memory packets and evidence previews."""

from __future__ import annotations

import re
from typing import Any

PRIVACY_SCRUB_VERSION = "cc-privacy-scrub/v1"

_SCRUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "authorization_header",
        re.compile(r"(?i)\bAuthorization\s*:\s*(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"),
    ),
    (
        "env_secret_assignment",
        re.compile(
            r"(?im)^(\s*[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CLIENT_SECRET|ACCESS_TOKEN|REFRESH_TOKEN)\s*=\s*)([^\s#]+)"
        ),
    ),
    (
        "inline_secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|client_secret|access_token|refresh_token)\s*[:=]\s*([\"']?)[A-Za-z0-9._~+/=-]{8,}\2"
        ),
    ),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_access_key", re.compile(r"\bA(?:KIA|SIA)[A-Z0-9]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)


def _blank_receipt() -> dict[str, Any]:
    return {
        "schemaVersion": PRIVACY_SCRUB_VERSION,
        "enabled": True,
        "replacementCount": 0,
        "categories": {},
        "secretValuesIncluded": False,
    }


def _merge_receipt(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    target["replacementCount"] = int(target.get("replacementCount") or 0) + int(source.get("replacementCount") or 0)
    target_categories = target.setdefault("categories", {})
    for category, count in (source.get("categories") or {}).items():
        target_categories[category] = int(target_categories.get(category) or 0) + int(count or 0)
    return target


def scrub_text(text: str) -> tuple[str, dict[str, Any]]:
    """Redact common secret shapes and return a count-only receipt."""
    receipt = _blank_receipt()
    redacted = str(text or "")

    for category, pattern in _SCRUB_PATTERNS:
        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if category == "env_secret_assignment":
                return f"{match.group(1)}[REDACTED:{category}]"
            if category == "inline_secret_assignment":
                return f"{match.group(1)}=[REDACTED:{category}]"
            if category == "authorization_header":
                prefix = match.group(0).split(None, 1)[0]
                return f"{prefix} [REDACTED:{category}]"
            return f"[REDACTED:{category}]"

        redacted = pattern.sub(replace, redacted)
        if count:
            receipt["categories"][category] = int(receipt["categories"].get(category) or 0) + count
            receipt["replacementCount"] += count

    return redacted, receipt


def scrub_value(value: Any) -> tuple[Any, dict[str, Any]]:
    """Recursively redact strings in JSON-compatible values."""
    receipt = _blank_receipt()
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        items = []
        for item in value:
            scrubbed, item_receipt = scrub_value(item)
            items.append(scrubbed)
            _merge_receipt(receipt, item_receipt)
        return items, receipt
    if isinstance(value, dict):
        scrubbed_dict: dict[str, Any] = {}
        for key, item in value.items():
            scrubbed, item_receipt = scrub_value(item)
            scrubbed_dict[key] = scrubbed
            _merge_receipt(receipt, item_receipt)
        return scrubbed_dict, receipt
    return value, receipt


__all__ = ["PRIVACY_SCRUB_VERSION", "scrub_text", "scrub_value"]
