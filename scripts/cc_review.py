"""Review command parser and dispatch helpers for ControlCoding."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

REVIEW_ROUTED_COMMANDS = frozenset({
    ("review",),
})


def add_review_parser(subparsers: Any) -> Any:
    p_review = subparsers.add_parser("review", help="Generate peer review prompt")
    p_review.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_review.add_argument(
        "--stdout", action="store_true",
        help="Print prompt to stdout instead of saving to file",
    )
    return p_review


def dispatch_review_command(
    args: Any,
    project: Path,
    cmd_review: Callable[..., int],
) -> int:
    return cmd_review(project, to_stdout=getattr(args, "stdout", False))


__all__ = [
    "REVIEW_ROUTED_COMMANDS",
    "add_review_parser",
    "dispatch_review_command",
]
