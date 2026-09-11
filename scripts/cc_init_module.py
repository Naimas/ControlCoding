"""Init-module command parser and dispatch helpers for ControlCoding."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

INIT_MODULE_ROUTED_COMMANDS = frozenset({
    ("init-module",),
})


def add_init_module_parser(subparsers: Any) -> Any:
    p_initmod = subparsers.add_parser("init-module", help="Create a new module with .feature-lock.json")
    p_initmod.add_argument(
        "name", help="Module name (used as directory and identifier)",
    )
    p_initmod.add_argument(
        "--dir", dest="module_dir", default=None,
        help="Custom module directory (default: modules/<name>)",
    )
    p_initmod.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    return p_initmod


def dispatch_init_module_command(
    args: Any,
    project: Path,
    cmd_init_module: Callable[..., int],
) -> int:
    return cmd_init_module(project, args.name, module_dir=getattr(args, "module_dir", None))


__all__ = [
    "INIT_MODULE_ROUTED_COMMANDS",
    "add_init_module_parser",
    "dispatch_init_module_command",
]
