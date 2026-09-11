"""Documentation maintenance checks for ControlCoding.

The command family is deliberately proposal-first. It audits architecture and
system-document drift, but it does not rewrite canonical documents.
"""

from __future__ import annotations

import ast
import datetime
import fnmatch
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility.
    tomllib = None

DOCS_MAINTENANCE_SCHEMA_VERSION = "cc-docs-maintenance/v1"
DOCS_MAINTENANCE_ROUTED_COMMANDS = frozenset({
    ("docs",),
    ("docs", "audit"),
    ("docs", "check"),
    ("docs", "propose"),
})
DOCS_MAINTENANCE_CAPABILITY_REGISTRY_ITEM = {
    "id": "docs_maintenance",
    "label": "Architecture and system documentation maintenance audit",
    "state": "experimental",
    "control_level": "conditional",
    "hosts": ["all"],
    "commands": ["docs audit", "docs check", "docs propose"],
    "evidence": ["docs/docs-maintenance-plan.md", "docs/controlcoding-system-architecture.md", "tests/test_cc_cli.py"],
}

REQUIRED_SYSTEM_DOCS = [
    ("CONTROLCODING.md", "canonical operational context"),
    ("docs/INDEX.md", "public docs index"),
    ("docs/controlcoding-system-architecture.md", "system architecture overview"),
    ("docs/docs-maintenance-plan.md", "docs maintenance design slice"),
    ("docs/release-model.md", "release and repository-role model"),
    ("docs/project-memory-engine.md", "Project Memory Engine and ControlWork plane guide"),
    ("docs/memory-system-schema.md", "memory and GraphRAG architecture schema"),
    ("docs/memory-graph-contract.md", "shared memory graph contract"),
    ("docs/file-organization-standard.md", "documentation governance standard"),
    ("docs/controlwork-advanced-memory-phase-2-plan.md", "ControlWork portability and advanced memory plan"),
    ("docs/session-graphrag-plan.md", "Session GraphRAG plan and status"),
]

REQUIRED_ARCHITECTURE_REFERENCES = [
    "../CONTROLCODING.md",
    "release-model.md",
    "project-memory-engine.md",
    "memory-system-schema.md",
    "memory-graph-contract.md",
    "file-organization-standard.md",
    "controlwork-advanced-memory-phase-2-plan.md",
    "session-graphrag-plan.md",
]

RELEASE_MANIFEST_FILE = "controlcoding.release.json"
RELEASE_REQUIRED_LEGAL_FILES = (
    "NOTICE",
    "TRADEMARKS.md",
)
WORK_PLANE_CONTRACT_IDENTITY = "controlwork-work-plane/1.0.0"
WORK_PLANE_CONTRACT_DOCUMENT = "docs/work-plane-compatibility-contract.md"
WORK_PLANE_EMBEDDED_PROFILE = "ControlCoding embedded conformance profile"

_PUBLIC_PRODUCT_VERSION_SOURCES = (
    "README.md",
    "CHANGELOG.md",
    "docs/release-model.md",
)
_PUBLIC_TRUTH_DOCUMENTS = (
    "README.md",
    "docs/release-model.md",
    "docs/memory-graphrag-release-notes.md",
    "docs/cross-tool-guide.md",
    "docs/install-controlcoding-on-your-project.md",
    "docs/quick-start.md",
    "docs/ccdocs/tools-reference.md",
    "docs/file-organization-standard.md",
)
_CURRENT_TEST_COUNT_PATTERNS = (
    re.compile(r"!\[tests[^\]\d]*\d+[^\]]*(?:passing|passed)[^\]]*\]", re.IGNORECASE),
    re.compile(r"test suite\s*\(\s*\d+\s+tests?\s*\)", re.IGNORECASE),
    re.compile(
        r"\bcurrent\s+(?:automated\s+)?test(?:\s+suite)?\s+"
        r"(?:count|total)\s*(?:is|:|=)\s*\d+",
        re.IGNORECASE,
    ),
)
_REQUIRED_PUBLIC_CLI_ROUTES = frozenset({
    ("benchmark",),
    ("benchmark", "run"),
    ("benchmark", "compare"),
    ("benchmark", "report"),
    ("organize",),
    ("resume",),
})
_SAFE_ROUTED_CLI_EXTENSION_NAMES = frozenset({
    "DOCS_MAINTENANCE_ROUTED_COMMANDS",
    "FEATURE_ROUTED_COMMANDS",
    "INIT_MODULE_ROUTED_COMMANDS",
    "MEMORY_AUX_ROUTED_COMMANDS",
    "REVIEW_ROUTED_COMMANDS",
})
_REQUIRED_PUBLIC_CLI_ARGUMENTS = {
    ("benchmark", "compare"): frozenset({"baseline", "current"}),
    ("organize",): frozenset({"--apply"}),
    ("resume",): frozenset({"--brief"}),
}
_REQUIRED_BENCHMARK_DEFAULTS = {
    "BENCHMARK_RUN_OUTPUT": "benchmarks/run.json",
    "BENCHMARK_REPORT_OUTPUT": "benchmarks/report.md",
}
_REQUIRED_BENCHMARK_OUTPUT_ARGUMENTS = {
    ("benchmark", "run"): "BENCHMARK_RUN_OUTPUT",
    ("benchmark", "report"): "BENCHMARK_REPORT_OUTPUT",
}
_REQUIRED_PUBLIC_CLI_PARSER_HELP = {
    ("resume",): "Print a provider-neutral context brief without launching a provider or subprocess",
}
_REQUIRED_PUBLIC_CLI_ARGUMENT_HELP = {
    (
        ("resume",),
        "--brief",
    ): "Accepted for CLI compatibility; the current command always prints the brief",
}
_REQUIRED_PUBLIC_CLI_DISPATCH_HANDLERS = {
    ("benchmark", "run"): "cmd_benchmark_run",
    ("benchmark", "compare"): "cmd_benchmark_compare",
    ("benchmark", "report"): "cmd_benchmark_report",
    ("organize",): "cmd_organize",
    ("resume",): "cmd_resume",
}

DOC_INDEX_REQUIRED_REFERENCES = [
    "controlcoding-system-architecture.md",
    "docs-maintenance-plan.md",
]

DOC_OWNER_RULES = [
    {
        "id": "cli_surface",
        "prefixes": ["scripts/cc.py", "scripts/cc_feature.py", "scripts/cc_docs.py"],
        "docs": [
            "docs/controlcoding-system-architecture.md",
            "docs/docs-maintenance-plan.md",
            "docs/ccdocs/tools-reference.md",
        ],
    },
    {
        "id": "setup_and_adoption",
        "prefixes": ["scripts/cc_setup.py", "templates/phase0_discovery.md"],
        "docs": [
            "docs/install-controlcoding-on-your-project.md",
            "docs/quick-start.md",
            "docs/adoption-guide.md",
        ],
    },
    {
        "id": "memory_system",
        "prefixes": ["scripts/cc_memory.py", "scripts/cc_memory_lib/"],
        "docs": [
            "docs/memory-system-schema.md",
            "docs/project-memory-engine.md",
            "docs/memory-graph-contract.md",
        ],
    },
    {
        "id": "controlwork_project_plane",
        "prefixes": ["scripts/controlwork_mcp.py", "templates/CONTROLWORK.md.template"],
        "docs": [
            "docs/project-memory-engine.md",
            "docs/controlwork-advanced-memory-phase-2-plan.md",
        ],
    },
    {
        "id": "hooks_and_gates",
        "prefixes": ["templates/hooks/"],
        "docs": [
            "docs/hooks-reference.md",
            "docs/ccdocs/hooks-reference.md",
            "docs/controlcoding-system-architecture.md",
        ],
    },
    {
        "id": "documentation_governance",
        "prefixes": ["docs/file-organization-standard.md"],
        "docs": [
            "docs/controlcoding-system-architecture.md",
            "docs/adoption-guide.md",
        ],
    },
]

DOC_PLAN_STATUS_TERMS = (
    "archive",
    "current",
    "future",
    "implemented",
    "internal",
    "living",
    "local",
    "plan",
    "proposal",
    "release",
    "stable",
    "superseded",
)
AUDIT_SCAN_SUFFIXES = {".md", ".txt", ".json"}
AUDIT_NON_FINAL_MARKERS = (
    "audit summary",
    "linked-audit-summary",
    "not a final audit",
    "problem matrix",
    "redirect",
    "summary",
    "superseded",
)


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _rel(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _parse_project_version_fallback(text: str) -> tuple[str, str]:
    """Read one simple [project].version assignment without parsing general TOML."""
    in_project = False
    project_sections = 0
    versions: list[str] = []
    version_line = re.compile(
        r'''version\s*=\s*(?:"([^"\\]*)"|'([^']*)')\s*(?:#.*)?'''
    )

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            if re.fullmatch(r"\[project\]\s*(?:#.*)?", stripped):
                project_sections += 1
                in_project = True
            else:
                in_project = False
            continue
        if not in_project:
            continue
        if not re.match(r'''(?:version\b|["']version["'])''', stripped):
            continue
        match = version_line.fullmatch(stripped)
        if not match:
            return "", "[project].version is ambiguous or uses an unsupported TOML form."
        versions.append(match.group(1) if match.group(1) is not None else match.group(2))

    if project_sections == 0:
        return "", "pyproject.toml does not contain a [project] section."
    if project_sections != 1:
        return "", "pyproject.toml contains duplicate [project] sections."
    if not versions:
        return "", "[project].version is missing."
    if len(versions) != 1 or not versions[0].strip():
        return "", "[project].version is duplicate, empty, or ambiguous."
    return versions[0].strip(), ""


def _read_project_version(path: Path) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "", "pyproject.toml is missing or is not readable UTF-8."

    if tomllib is None:
        return _parse_project_version_fallback(text)

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return "", "pyproject.toml is not valid unambiguous TOML."
    project_table = document.get("project")
    if not isinstance(project_table, dict):
        return "", "pyproject.toml does not contain a [project] table."
    version = project_table.get("version")
    if not isinstance(version, str) or not version.strip():
        return "", "[project].version is missing or is not a non-empty string."
    return version.strip(), ""


def _assignment_name(statement: ast.stmt) -> str:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        return target.id if isinstance(target, ast.Name) else ""
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id
    return ""


def _assignment_value(statement: ast.stmt) -> ast.expr | None:
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return statement.value
    return None


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _static_route_registry(tree: ast.Module) -> tuple[set[tuple[str, ...]], str]:
    assignments = [
        statement
        for statement in tree.body
        if _assignment_name(statement) == "_ROUTED_CLI_COMMANDS"
    ]
    if len(assignments) != 1:
        return set(), "_ROUTED_CLI_COMMANDS must have exactly one module-level assignment."

    assignment = assignments[0]
    allowed_target = (
        assignment.targets[0]
        if isinstance(assignment, ast.Assign)
        else assignment.target
    )
    invalid_writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "_ROUTED_CLI_COMMANDS"
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node is not allowed_target
    ]
    if invalid_writes:
        contexts = ", ".join(sorted({type(node.ctx).__name__ for node in invalid_writes}))
        return set(), (
            "_ROUTED_CLI_COMMANDS has a disallowed additional write or deletion "
            f"({contexts})."
        )

    value = _assignment_value(assignment)
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and len(value.args) == 1
        and not value.keywords
        and isinstance(value.args[0], (ast.Set, ast.List, ast.Tuple))
    ):
        return set(), "_ROUTED_CLI_COMMANDS is not a safely readable frozenset literal."

    routes: set[tuple[str, ...]] = set()
    for item in value.args[0].elts:
        if isinstance(item, ast.Starred) and isinstance(item.value, ast.Name):
            if item.value.id not in _SAFE_ROUTED_CLI_EXTENSION_NAMES:
                return set(), (
                    "_ROUTED_CLI_COMMANDS contains an unexpected starred authority."
                )
            continue
        if not isinstance(item, ast.Tuple) or not item.elts:
            return set(), "_ROUTED_CLI_COMMANDS contains a non-literal route entry."
        route = tuple(_literal_string(part) for part in item.elts)
        if any(not part for part in route):
            return set(), "_ROUTED_CLI_COMMANDS contains a non-string route component."
        if route in routes:
            return set(), "_ROUTED_CLI_COMMANDS contains a duplicate literal route."
        routes.add(route)
    return routes, ""


def _static_path_value(node: ast.AST | None) -> str:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and len(node.args) == 1
        and not node.keywords
    ):
        return _literal_string(node.args[0]).replace("\\", "/")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _static_path_value(node.left)
        right = _literal_string(node.right).replace("\\", "/")
        if left and right:
            return f"{left.rstrip('/')}/{right.lstrip('/')}"
    return ""


def _static_benchmark_defaults(tree: ast.Module) -> tuple[dict[str, str], list[str]]:
    defaults: dict[str, str] = {}
    issues: list[str] = []
    for name in _REQUIRED_BENCHMARK_DEFAULTS:
        assignments = [
            statement
            for statement in tree.body
            if _assignment_name(statement) == name
        ]
        if len(assignments) != 1:
            issues.append(f"{name} must have exactly one module-level assignment.")
            continue
        value = _static_path_value(_assignment_value(assignments[0]))
        if not value:
            issues.append(f"{name} is not a safely readable Path assignment.")
            continue
        defaults[name] = value
    return defaults, issues


def _call_receiver_name(call: ast.Call) -> tuple[str, str]:
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return call.func.value.id, call.func.attr
    return "", ""


def _call_keyword_string(call: ast.Call, name: str) -> str:
    for keyword in call.keywords:
        if keyword.arg == name:
            return _literal_string(keyword.value)
    return ""


def _parse_result_name(statement: ast.stmt, method: str) -> str:
    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return ""
    target: ast.expr
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1:
            return ""
        target = statement.targets[0]
    else:
        target = statement.target
    if method == "parse_args" and isinstance(target, ast.Name):
        return target.id
    if (
        method == "parse_known_args"
        and isinstance(target, ast.Tuple)
        and len(target.elts) == 2
        and all(isinstance(item, ast.Name) for item in target.elts)
        and target.elts[0].id != target.elts[1].id
    ):
        return target.elts[0].id
    return ""


def _compared_route(
    test: ast.expr,
    args_name: str,
    attribute: str,
    aliases: set[str] | None = None,
) -> str:
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
    ):
        return ""

    aliases = aliases or set()

    def is_selector(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == args_name
            and node.attr == attribute
        ) or (isinstance(node, ast.Name) and node.id in aliases)

    if is_selector(test.left):
        return _literal_string(test.comparators[0])
    if is_selector(test.comparators[0]):
        return _literal_string(test.left)
    return ""


def _matching_if_branches(
    statements: list[ast.stmt],
    args_name: str,
    attribute: str,
    aliases: set[str] | None = None,
) -> dict[str, list[ast.If]]:
    matches: dict[str, list[ast.If]] = {}
    for statement in statements:
        if not isinstance(statement, ast.If):
            continue
        branch = statement
        while True:
            if not (isinstance(branch.test, ast.Constant) and branch.test.value is False):
                route = _compared_route(branch.test, args_name, attribute, aliases)
                if route:
                    matches.setdefault(route, []).append(branch)
            if len(branch.orelse) != 1 or not isinstance(branch.orelse[0], ast.If):
                break
            branch = branch.orelse[0]
    return matches


def _getattr_aliases(
    statements: list[ast.stmt],
    args_name: str,
    attribute: str,
) -> set[str]:
    aliases: set[str] = set()
    for statement in statements:
        target = _assignment_name(statement)
        call = _assignment_value(statement)
        if not (
            target
            and isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "getattr"
            and len(call.args) >= 2
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == args_name
            and _literal_string(call.args[1]) == attribute
        ):
            continue
        aliases.add(target)
    return aliases


def _expression_call_names(node: ast.AST | None) -> list[str]:
    if node is None or isinstance(node, ast.Lambda):
        return []
    names: list[str] = []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        names.append(node.func.id)
    for child in ast.iter_child_nodes(node):
        names.extend(_expression_call_names(child))
    return names


def _bounded_call_names(statements: list[ast.stmt]) -> list[str]:
    names: list[str] = []
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(statement, ast.If):
            if isinstance(statement.test, ast.Constant) and statement.test.value is False:
                names.extend(_bounded_call_names(statement.orelse))
            else:
                names.extend(_bounded_call_names(statement.body))
                names.extend(_bounded_call_names(statement.orelse))
            continue
        if isinstance(statement, ast.Try):
            names.extend(_bounded_call_names(statement.body))
            for handler in statement.handlers:
                names.extend(_bounded_call_names(handler.body))
            names.extend(_bounded_call_names(statement.orelse))
            names.extend(_bounded_call_names(statement.finalbody))
            continue
        if isinstance(statement, ast.Return):
            names.extend(_expression_call_names(statement.value))
        elif isinstance(statement, ast.Expr):
            names.extend(_expression_call_names(statement.value))
        elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            names.extend(_expression_call_names(_assignment_value(statement)))
        elif isinstance(statement, ast.Raise):
            names.extend(_expression_call_names(statement.exc))
    return names


def _static_dispatch_issues(
    main_body: list[ast.stmt],
    args_name: str,
) -> list[str]:
    issues: list[str] = []
    root_branches = _matching_if_branches(main_body, args_name, "command")
    required_roots = ("benchmark", "organize", "resume")
    for route in required_roots:
        observed = len(root_branches.get(route, []))
        if observed != 1:
            issues.append(
                f"main dispatch must contain exactly one root branch for {route}; "
                f"observed={observed}."
            )

    benchmark_branches = root_branches.get("benchmark", [])
    if len(benchmark_branches) == 1:
        benchmark_branch = benchmark_branches[0]
        aliases = _getattr_aliases(
            benchmark_branch.body,
            args_name,
            "benchmark_run_command",
        )
        if len(aliases) > 1:
            issues.append(
                "Benchmark dispatch has ambiguous benchmark_run_command selector aliases."
            )
        subcommands = _matching_if_branches(
            benchmark_branch.body,
            args_name,
            "benchmark_run_command",
            aliases,
        )
        for command in ("run", "compare", "report"):
            route = ("benchmark", command)
            branches = subcommands.get(command, [])
            if len(branches) != 1:
                issues.append(
                    f"main dispatch must contain exactly one branch for benchmark {command}; "
                    f"observed={len(branches)}."
                )
                continue
            handler = _REQUIRED_PUBLIC_CLI_DISPATCH_HANDLERS[route]
            calls = _bounded_call_names(branches[0].body).count(handler)
            if calls != 1:
                issues.append(
                    f"Dispatch route benchmark {command} must call {handler} exactly once; "
                    f"observed={calls}."
                )

    for route in ("organize", "resume"):
        branches = root_branches.get(route, [])
        if len(branches) != 1:
            continue
        handler = _REQUIRED_PUBLIC_CLI_DISPATCH_HANDLERS[(route,)]
        calls = _bounded_call_names(branches[0].body).count(handler)
        if calls != 1:
            issues.append(
                f"Dispatch route {route} must call {handler} exactly once; observed={calls}."
            )
    return issues


def _static_main_parser(
    tree: ast.Module,
) -> tuple[
    set[tuple[str, ...]],
    dict[tuple[str, ...], dict[str, str]],
    dict[tuple[str, ...], str],
    dict[tuple[tuple[str, ...], str], str],
    list[str],
]:
    main_functions = [
        statement
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "main"
    ]
    if len(main_functions) != 1 or isinstance(main_functions[0], ast.AsyncFunctionDef):
        return set(), {}, {}, {}, ["The CLI must define exactly one synchronous main function."]

    root_parser_assignments: list[str] = []
    root_parsers: set[str] = set()
    subparser_parents: dict[str, tuple[str, ...]] = {}
    parser_routes: dict[str, tuple[str, ...]] = {}
    routes: set[tuple[str, ...]] = set()
    arguments: dict[tuple[str, ...], dict[str, str]] = {}
    parser_help: dict[tuple[str, ...], str] = {}
    argument_help: dict[tuple[tuple[str, ...], str], str] = {}
    issues: list[str] = []

    main_body: list[ast.stmt] = []
    for statement in main_functions[0].body:
        if isinstance(statement, (ast.Return, ast.Raise)):
            break
        main_body.append(statement)

    for statement in main_body:
        target = _assignment_name(statement)
        call = _assignment_value(statement)
        if (
            target
            and isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "argparse"
            and call.func.attr == "ArgumentParser"
        ):
            root_parser_assignments.append(target)
            root_parsers.add(target)
            continue

        if target and isinstance(call, ast.Call):
            receiver, method = _call_receiver_name(call)
            if method == "add_subparsers":
                if receiver in root_parsers:
                    parent_route: tuple[str, ...] = ()
                elif receiver in parser_routes:
                    parent_route = parser_routes[receiver]
                else:
                    continue
                if target in subparser_parents:
                    issues.append(f"Parser variable {target} has ambiguous subparser ownership.")
                else:
                    subparser_parents[target] = parent_route
                continue
            if method == "add_parser" and receiver in subparser_parents:
                command = _literal_string(call.args[0]) if call.args else ""
                if not command:
                    issues.append(f"Parser variable {target} has a non-literal command name.")
                    continue
                route = (*subparser_parents[receiver], command)
                if target in parser_routes or route in routes:
                    issues.append(f"Parser route {' '.join(route)} is ambiguous or duplicated.")
                    continue
                parser_routes[target] = route
                routes.add(route)
                arguments[route] = {}
                parser_help[route] = _call_keyword_string(call, "help")
                continue

        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            receiver, method = _call_receiver_name(statement.value)
            if method != "add_argument" or receiver not in parser_routes:
                continue
            argument = _literal_string(statement.value.args[0]) if statement.value.args else ""
            if not argument:
                continue
            default_name = ""
            for keyword in statement.value.keywords:
                if keyword.arg == "default" and isinstance(keyword.value, ast.Name):
                    default_name = keyword.value.id
            arguments[parser_routes[receiver]][argument] = default_name
            argument_help[(parser_routes[receiver], argument)] = _call_keyword_string(
                statement.value,
                "help",
            )

    if not root_parser_assignments:
        issues.append("main does not declare an argparse.ArgumentParser root.")
    elif len(root_parser_assignments) != 1:
        issues.append(
            "main must declare exactly one argparse.ArgumentParser root; "
            f"observed={len(root_parser_assignments)}."
        )
    else:
        root_parser = root_parser_assignments[0]
        consumptions: list[tuple[str, str]] = []
        for statement in main_body:
            call = _assignment_value(statement)
            if isinstance(statement, ast.Expr):
                call = statement.value
            if not isinstance(call, ast.Call):
                continue
            receiver, method = _call_receiver_name(call)
            if receiver != root_parser or method not in {"parse_args", "parse_known_args"}:
                continue
            consumptions.append((method, _parse_result_name(statement, method)))
        if len(consumptions) != 1:
            issues.append(
                f"The recognized root parser must be consumed exactly once; observed={len(consumptions)}."
            )
        elif not consumptions[0][1]:
            issues.append("The root parser result must use a supported unambiguous assignment.")
        else:
            issues.extend(_static_dispatch_issues(main_body, consumptions[0][1]))
    return routes, arguments, parser_help, argument_help, issues


def _cli_public_truth_findings(source: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source, filename="scripts/cc.py")
    except (SyntaxError, ValueError) as exc:
        return [_finding(
            "error",
            "cli_public_truth",
            "Public CLI source is not statically parseable.",
            path="scripts/cc.py",
            detail=exc.msg if isinstance(exc, SyntaxError) else str(exc),
        )]

    findings: list[dict[str, Any]] = []
    registry_routes, registry_issue = _static_route_registry(tree)
    (
        parser_routes,
        parser_arguments,
        parser_help,
        argument_help,
        parser_issues,
    ) = _static_main_parser(tree)
    defaults, default_issues = _static_benchmark_defaults(tree)

    if registry_issue:
        findings.append(_finding(
            "error",
            "cli_public_truth",
            "Public CLI route registry cannot be evaluated safely.",
            path="scripts/cc.py",
            detail=registry_issue,
        ))
    else:
        for route in sorted(_REQUIRED_PUBLIC_CLI_ROUTES - registry_routes):
            findings.append(_finding(
                "error",
                "cli_public_truth",
                f"_ROUTED_CLI_COMMANDS is missing required route {' '.join(route)}.",
                path="scripts/cc.py",
            ))
        if any(route and route[0] == "replace" for route in registry_routes):
            findings.append(_finding(
                "error",
                "removed_command_public_truth",
                "_ROUTED_CLI_COMMANDS registers the removed public replace route.",
                path="scripts/cc.py",
            ))

    for issue in (*parser_issues, *default_issues):
        findings.append(_finding(
            "error",
            "cli_public_truth",
            "Public CLI parser structure cannot be verified safely.",
            path="scripts/cc.py",
            detail=issue,
        ))
    for route in sorted(_REQUIRED_PUBLIC_CLI_ROUTES - parser_routes):
        findings.append(_finding(
            "error",
            "cli_public_truth",
            f"main parser is missing required route {' '.join(route)}.",
            path="scripts/cc.py",
        ))
    if any(route and route[0] == "replace" for route in parser_routes):
        findings.append(_finding(
            "error",
            "removed_command_public_truth",
            "The main parser registers the removed public replace route.",
            path="scripts/cc.py",
        ))

    for route, required_arguments in _REQUIRED_PUBLIC_CLI_ARGUMENTS.items():
        present = set(parser_arguments.get(route, {}))
        for argument in sorted(required_arguments - present):
            findings.append(_finding(
                "error",
                "cli_public_truth",
                f"Parser route {' '.join(route)} is missing argument {argument}.",
                path="scripts/cc.py",
            ))

    for name, expected in _REQUIRED_BENCHMARK_DEFAULTS.items():
        if name in defaults and defaults[name] != expected:
            findings.append(_finding(
                "error",
                "cli_public_truth",
                f"{name} must resolve statically to {expected}.",
                path="scripts/cc.py",
                detail=f"observed={defaults[name]}",
            ))
    for route, default_name in _REQUIRED_BENCHMARK_OUTPUT_ARGUMENTS.items():
        observed = parser_arguments.get(route, {}).get("--output", "")
        if observed != default_name:
            findings.append(_finding(
                "error",
                "cli_public_truth",
                f"Parser route {' '.join(route)} must use default {default_name} for --output.",
                path="scripts/cc.py",
                detail=f"observed={observed or 'missing or non-literal'}",
            ))
    for route, expected_help in _REQUIRED_PUBLIC_CLI_PARSER_HELP.items():
        if parser_help.get(route) != expected_help:
            findings.append(_finding(
                "error",
                "cli_public_truth",
                f"Parser route {' '.join(route)} has missing or unexpected help text.",
                path="scripts/cc.py",
            ))
    for key, expected_help in _REQUIRED_PUBLIC_CLI_ARGUMENT_HELP.items():
        route, argument = key
        if argument_help.get(key) != expected_help:
            findings.append(_finding(
                "error",
                "cli_public_truth",
                f"Parser argument {' '.join(route)} {argument} has missing or unexpected help text.",
                path="scripts/cc.py",
            ))
    return findings


def _current_changelog_sections(
    text: str,
    product_version: str,
) -> tuple[list[str], list[str]]:
    headings = list(re.finditer(r"^##(?!#)[ \t]+(.+?)[ \t]*$", text, re.MULTILINE))
    unreleased = [
        index
        for index, heading in enumerate(headings)
        if heading.group(1).strip().casefold() == "unreleased"
    ]
    version_heading = re.compile(
        rf"^v?{re.escape(product_version)}(?:\s+\([^\r\n)]*\))?$",
        re.IGNORECASE,
    )
    current_version = [
        index
        for index, heading in enumerate(headings)
        if version_heading.fullmatch(heading.group(1).strip())
    ]
    issues: list[str] = []
    if len(unreleased) > 1:
        issues.append("CHANGELOG.md contains duplicate Unreleased sections.")
    if not current_version:
        issues.append(
            f"CHANGELOG.md does not contain a section for product version {product_version}."
        )
    elif len(current_version) > 1:
        issues.append(
            f"CHANGELOG.md contains duplicate sections for product version {product_version}."
        )
    if issues:
        return [], issues

    selected = [*unreleased, *current_version]
    sections = []
    for index in selected:
        start = headings[index].end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections.append(text[start:end])
    return sections, []


def _release_manifest_pattern_entries(
    raw_manifest: dict[str, Any],
    key: str,
) -> tuple[list[dict[str, str]], list[str]]:
    raw_entries = raw_manifest.get(key, [])
    if not isinstance(raw_entries, list):
        return [], [f"{key} must be a list"]

    entries: list[dict[str, str]] = []
    issues: list[str] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            issues.append(f"{key}[{index}] must be an object with pattern and reason")
            continue
        pattern = str(item.get("pattern", "")).strip().replace("\\", "/")
        reason = str(item.get("reason", "")).strip()
        if not pattern:
            issues.append(f"{key}[{index}].pattern is required")
            continue
        if not reason:
            issues.append(f"{key}[{index}].reason is required")
        entries.append({"pattern": pattern, "reason": reason})
    return entries, issues


def _release_manifest_required_entries(
    raw_manifest: dict[str, Any],
) -> tuple[list[str], list[str]]:
    raw_entries = raw_manifest.get("required", [])
    if not isinstance(raw_entries, list):
        return [], ["required must be a list"]

    entries: list[str] = []
    issues: list[str] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, str) or not item.strip():
            issues.append(f"required[{index}] must be a non-empty string")
            continue
        entries.append(item.strip().replace("\\", "/"))
    return entries, issues


def release_manifest_path_matches(relative_path: str, pattern: str) -> bool:
    normalized_path = relative_path.replace("\\", "/")
    normalized_pattern = pattern.strip().replace("\\", "/")
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def load_release_manifest_contract(project: Path) -> dict[str, Any]:
    manifest_path = project / RELEASE_MANIFEST_FILE
    policy: dict[str, Any] = {
        "source": "missing",
        "path": RELEASE_MANIFEST_FILE,
        "present": False,
        "ok": True,
        "package": "",
        "workPlaneContract": "",
        "allow": [],
        "deny": [],
        "required": [],
        "issues": [],
    }
    if not manifest_path.exists():
        return policy

    policy["source"] = "project"
    policy["present"] = True
    if not manifest_path.is_file():
        policy["ok"] = False
        policy["issues"].append(f"{RELEASE_MANIFEST_FILE} must be a regular file")
        return policy

    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        policy["ok"] = False
        policy["issues"].append(f"{RELEASE_MANIFEST_FILE} is invalid JSON: {exc}")
        return policy
    if not isinstance(raw_manifest, dict):
        policy["ok"] = False
        policy["issues"].append(f"{RELEASE_MANIFEST_FILE} must contain a JSON object")
        return policy

    if raw_manifest.get("schemaVersion") != 1:
        policy["issues"].append("schemaVersion must be 1")
    package = str(raw_manifest.get("package", "")).strip()
    policy["package"] = package
    if not package:
        policy["issues"].append("package is required")

    if "workPlaneContract" not in raw_manifest:
        policy["issues"].append("workPlaneContract is required")
    else:
        work_plane_contract = raw_manifest["workPlaneContract"]
        if not isinstance(work_plane_contract, str):
            policy["issues"].append("workPlaneContract must be a string")
        else:
            policy["workPlaneContract"] = work_plane_contract
            if work_plane_contract != WORK_PLANE_CONTRACT_IDENTITY:
                policy["issues"].append(
                    f"workPlaneContract must equal {WORK_PLANE_CONTRACT_IDENTITY}"
                )

    allow_entries, allow_issues = _release_manifest_pattern_entries(raw_manifest, "allow")
    deny_entries, deny_issues = _release_manifest_pattern_entries(raw_manifest, "deny")
    required_entries, required_issues = _release_manifest_required_entries(raw_manifest)
    policy["allow"] = allow_entries
    policy["deny"] = deny_entries
    policy["required"] = required_entries
    policy["issues"].extend(allow_issues)
    policy["issues"].extend(deny_issues)
    policy["issues"].extend(required_issues)
    if not allow_entries:
        policy["issues"].append("allow must contain at least one pattern")
    for relative in RELEASE_REQUIRED_LEGAL_FILES:
        if relative not in required_entries:
            policy["issues"].append(f"required must include {relative}")
        required_path = project / relative
        if not required_path.exists():
            policy["issues"].append(f"{relative} is missing")
        elif not required_path.is_file():
            policy["issues"].append(f"{relative} must be a regular file")
    if WORK_PLANE_CONTRACT_DOCUMENT not in required_entries:
        policy["issues"].append(
            f"required must include {WORK_PLANE_CONTRACT_DOCUMENT}"
        )

    contract_path = project / WORK_PLANE_CONTRACT_DOCUMENT
    if not contract_path.exists():
        policy["issues"].append(f"{WORK_PLANE_CONTRACT_DOCUMENT} is missing")
    elif not contract_path.is_file():
        policy["issues"].append(
            f"{WORK_PLANE_CONTRACT_DOCUMENT} must be a regular file"
        )
    else:
        try:
            contract_text = contract_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            policy["issues"].append(
                f"{WORK_PLANE_CONTRACT_DOCUMENT} is not readable UTF-8"
            )
        else:
            identity_lines = [
                line.strip()
                for line in contract_text.splitlines()
                if line.strip().startswith("Contract identity:")
            ]
            expected_identity_line = (
                f"Contract identity: `{WORK_PLANE_CONTRACT_IDENTITY}`"
            )
            if not identity_lines:
                policy["issues"].append(
                    f"{WORK_PLANE_CONTRACT_DOCUMENT} does not declare Contract identity"
                )
            elif identity_lines != [expected_identity_line]:
                policy["issues"].append(
                    f"{WORK_PLANE_CONTRACT_DOCUMENT} Contract identity must be exactly "
                    f"{WORK_PLANE_CONTRACT_IDENTITY}"
                )

            profile_lines = [
                line.strip()
                for line in contract_text.splitlines()
                if line.strip().startswith("Profile:")
            ]
            status_lines = [
                line.strip()
                for line in contract_text.splitlines()
                if line.strip().startswith("Status:")
            ]
            expected_profile_line = f"Profile: `{WORK_PLANE_EMBEDDED_PROFILE}`"
            expected_status_line = "Status: `non-canonical`"
            declares_canonical_profile = any(
                line.casefold() in {"status: canonical", "status: `canonical`"}
                for line in status_lines
            ) or any(
                "canonical" in line.casefold()
                and "non-canonical" not in line.casefold()
                for line in profile_lines
            )
            if declares_canonical_profile:
                policy["issues"].append(
                    f"{WORK_PLANE_CONTRACT_DOCUMENT} must not declare a canonical profile"
                )
            elif (
                profile_lines != [expected_profile_line]
                or status_lines != [expected_status_line]
            ):
                policy["issues"].append(
                    f"{WORK_PLANE_CONTRACT_DOCUMENT} must declare the embedded "
                    "non-canonical profile"
                )

    required_denied = [
        relative
        for relative in required_entries
        if any(release_manifest_path_matches(relative, entry["pattern"]) for entry in deny_entries)
    ]
    policy["issues"].extend(
        f"required path matches deny pattern: {relative}"
        for relative in required_denied
    )
    policy["ok"] = not policy["issues"]
    return policy


def _release_path_denied(relative_path: str, policy: dict[str, Any]) -> bool:
    if not policy.get("present") or not policy.get("ok"):
        return False
    required = set(policy.get("required", []))
    if relative_path in required:
        return False
    return any(
        release_manifest_path_matches(relative_path, entry["pattern"])
        for entry in policy.get("deny", [])
    )


def _reference_target_path(owner_path: str, reference: str) -> str:
    owner_dir = posixpath.dirname(owner_path.replace("\\", "/"))
    return posixpath.normpath(posixpath.join(owner_dir, reference.replace("\\", "/")))


def _finding(
    severity: str,
    check: str,
    message: str,
    path: str = "",
    detail: str = "",
    recommendation: str = "",
    related: list[str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "severity": severity,
        "check": check,
        "message": message,
    }
    if path:
        item["path"] = path
    if detail:
        item["detail"] = detail
    if recommendation:
        item["recommendation"] = recommendation
    if related:
        item["related"] = related
    return item


def _git_changed_files(project: Path, since: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": False,
        "since": since,
        "changedFiles": [],
        "error": "",
    }
    try:
        inside = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        payload["error"] = str(exc)
        return payload
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        payload["error"] = inside.stderr.strip() or "not a git worktree"
        return payload

    changed: set[str] = set()
    try:
        diff = subprocess.run(
            ["git", "-C", str(project), "diff", "--name-only", since, "--"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if diff.returncode == 0:
            changed.update(line.strip().replace("\\", "/") for line in diff.stdout.splitlines() if line.strip())
        else:
            payload["error"] = diff.stderr.strip() or f"git diff failed for {since}"
    except (OSError, subprocess.SubprocessError) as exc:
        payload["error"] = str(exc)

    try:
        status = subprocess.run(
            ["git", "-C", str(project), "status", "--short", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if status.returncode == 0:
            for line in status.stdout.splitlines():
                text = line[3:].strip() if len(line) > 3 else ""
                if " -> " in text:
                    text = text.split(" -> ", 1)[1].strip()
                if text:
                    changed.add(text.replace("\\", "/"))
        elif not payload["error"]:
            payload["error"] = status.stderr.strip() or "git status failed"
    except (OSError, subprocess.SubprocessError) as exc:
        if not payload["error"]:
            payload["error"] = str(exc)

    payload["available"] = True
    payload["changedFiles"] = sorted(changed)
    return payload


def _path_matches(path: str, prefixes: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized == prefix or normalized.startswith(prefix) for prefix in prefixes)


def _is_doc_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return normalized.endswith(".md") or normalized.startswith("docs/")


def _doc_alignment_findings(changed_files: list[str]) -> list[dict[str, Any]]:
    changed = {path.replace("\\", "/") for path in changed_files}
    changed_docs = {path for path in changed if _is_doc_path(path)}
    findings: list[dict[str, Any]] = []
    for rule in DOC_OWNER_RULES:
        impacted = sorted(path for path in changed if _path_matches(path, rule["prefixes"]))
        if not impacted:
            continue
        expected_docs = [doc for doc in rule["docs"] if doc not in changed_docs]
        if expected_docs:
            findings.append(
                _finding(
                    "warn",
                    "possible_doc_drift",
                    f"Changed files match {rule['id']} but expected owner docs were not changed.",
                    detail=", ".join(impacted[:8]),
                    recommendation="Review or update: " + ", ".join(expected_docs),
                    related=expected_docs,
                )
            )
    return findings


def _docs_plan_status_findings(project: Path) -> list[dict[str, Any]]:
    docs_dir = project / "docs"
    if not docs_dir.is_dir():
        return []

    findings: list[dict[str, Any]] = []
    for path in sorted(docs_dir.glob("*.md")):
        tokens = [
            token for token in path.stem.casefold().replace("_", "-").split("-")
            if token
        ]
        if not any(token in {"plan", "planning"} for token in tokens):
            continue
        text = _read_text(path)
        header = "\n".join(text.splitlines()[:24]).casefold()
        has_status_marker = (
            "status:" in header
            or "## status" in header
            or "**status**" in header
        )
        has_status_term = any(term in header for term in DOC_PLAN_STATUS_TERMS)
        if has_status_marker and has_status_term:
            continue
        findings.append(
            _finding(
                "warn",
                "docs-plan-without-status",
                "Plan-like document under docs/ lacks an explicit status marker.",
                path=_rel(project, path),
                recommendation=(
                    "Add an explicit status such as stable, proposal, internal, "
                    "superseded, release, living plan, or archive near the top."
                ),
            )
        )
    return findings


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_audit_scan_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIT_SCAN_SUFFIXES
    )


def _duplicate_audit_findings(project: Path) -> list[dict[str, Any]]:
    audit_reports = project / "audit_reports"
    outputs = project / ".controlwork" / "memory" / "outputs"
    if not audit_reports.is_dir() or not outputs.is_dir():
        return []

    audit_by_digest: dict[str, list[str]] = {}
    for path in _iter_audit_scan_files(audit_reports):
        try:
            digest = _file_digest(path)
        except OSError:
            continue
        audit_by_digest.setdefault(digest, []).append(_rel(project, path))

    findings: list[dict[str, Any]] = []
    for path in _iter_audit_scan_files(outputs):
        try:
            digest = _file_digest(path)
        except OSError:
            continue
        matches = audit_by_digest.get(digest, [])
        if not matches:
            continue
        findings.append(
            _finding(
                "warn",
                "duplicate-audit",
                "Full audit content is duplicated between audit_reports/ and ControlWork outputs.",
                path=_rel(project, path),
                recommendation=(
                    "Keep the final audit body in audit_reports/ and store only "
                    "a summary or link in .controlwork/memory/outputs/."
                ),
                related=matches,
            )
        )
    return findings


def _audit_candidate_files(project: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in sorted(project.iterdir()):
        if path.is_file() and path.suffix.lower() in AUDIT_SCAN_SUFFIXES:
            candidates.append(path)
    docs_dir = project / "docs"
    if docs_dir.is_dir():
        candidates.extend(_iter_audit_scan_files(docs_dir))
    outputs_dir = project / ".controlwork" / "memory" / "outputs"
    if outputs_dir.is_dir():
        candidates.extend(_iter_audit_scan_files(outputs_dir))
    return sorted(set(candidates))


def _audit_outside_reports_findings(project: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _audit_candidate_files(project):
        rel_path = _rel(project, path)
        normalized = rel_path.replace("\\", "/")
        if normalized.startswith("audit_reports/"):
            continue
        if "audit" not in path.name.casefold():
            continue
        text = _read_text(path).casefold()
        header = text[:4000]
        if any(marker in header for marker in AUDIT_NON_FINAL_MARKERS):
            continue
        findings.append(
            _finding(
                "warn",
                "audit-outside-audit_reports",
                "Audit-like report is outside audit_reports/ without a redirect, summary, or superseded marker.",
                path=normalized,
                recommendation=(
                    "Move final audit reports under audit_reports/ or mark this "
                    "file as a summary, redirect, or superseded pointer."
                ),
            )
        )
    return findings


_MARKDOWN_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_MARKDOWN_FENCE_CLOSE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*$")
_MARKDOWN_LIST_ITEM_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_MARKDOWN_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_ORGANIZE_MUTATION_RE = re.compile(
    r"\b(?:create(?:s|d|ing)?|move(?:s|d|ing)?|writ(?:e|es|ten|ing)|"
    r"refresh(?:es|ed|ing)?|repair(?:s|ed|ing)?|chang(?:e|es|ed|ing)|"
    r"modif(?:y|ies|ied|ying)|mutat(?:e|es|ed|ing))\b"
)
_PUBLIC_COMMAND_MENTION_RE = re.compile(r"\bcc\s+([a-z0-9][\w-]*)", re.IGNORECASE)
_REMOVED_COMMAND_MENTION_RE = re.compile(
    r"\bcc\s+replace\s+(?:start|status|complete)\b",
    re.IGNORECASE,
)


def _markdown_claim_blocks(text: str) -> list[tuple[str, str]]:
    """Group only the Markdown block boundaries needed by public-truth checks."""
    blocks: list[tuple[str, str]] = []
    current_kind = ""
    current_lines: list[str] = []
    fence_marker = ""

    def flush() -> None:
        nonlocal current_kind, current_lines
        if current_lines:
            separator = "\n" if current_kind == "code" else " "
            blocks.append((current_kind, separator.join(current_lines).strip()))
        current_kind = ""
        current_lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if fence_marker:
            closing = _MARKDOWN_FENCE_CLOSE_RE.match(raw_line)
            if (
                closing
                and closing.group(1)[0] == fence_marker[0]
                and len(closing.group(1)) >= len(fence_marker)
            ):
                flush()
                fence_marker = ""
            else:
                current_lines.append(stripped)
            continue

        opening = _MARKDOWN_FENCE_OPEN_RE.match(raw_line)
        if opening:
            flush()
            fence_marker = opening.group(1)
            current_kind = "code"
            continue
        if not stripped:
            flush()
            continue
        if _MARKDOWN_TABLE_ROW_RE.match(raw_line):
            flush()
            blocks.append(("table", stripped))
            continue
        if _MARKDOWN_LIST_ITEM_RE.match(raw_line):
            flush()
            current_kind = "list"
            current_lines = [stripped]
            continue
        if _MARKDOWN_HEADING_RE.match(raw_line):
            flush()
            blocks.append(("heading", stripped))
            continue
        if not current_kind:
            current_kind = "paragraph"
        current_lines.append(stripped)

    flush()
    return blocks


def _markdown_block_statements(kind: str, block: str) -> list[str]:
    source_parts = block.splitlines() if kind == "code" else [block]
    statements: list[str] = []
    for source_part in source_parts:
        statements.extend(
            part.strip()
            for part in re.split(
                r"(?<=[.!?;])\s+|,\s+(?=(?:and|but|however|while|whereas|although)\b)",
                source_part,
                flags=re.IGNORECASE,
            )
            if part.strip()
        )
    return statements


def _mutation_is_explicitly_negated(text: str, match: re.Match[str]) -> bool:
    prefix = text[max(0, match.start() - 48):match.start()]
    suffix = text[match.end():match.end() + 32]
    return bool(
        re.search(r"(?:does not|doesn't|never|will not|won't|without)\s+$", prefix)
        or re.search(r"no\s+(?:files?|changes?)\s+(?:are\s+|were\s+|will be\s+)?$", prefix)
        or re.match(r"\s+(?:no|zero)\s+(?:files?|changes?)\b", suffix)
        or re.match(r"\s+nothing\b", suffix)
    )


def _organize_statement_mutates_by_default(statement: str) -> bool:
    lowered = statement.casefold()
    if "by default" not in lowered:
        return False
    mutations = list(_ORGANIZE_MUTATION_RE.finditer(lowered))
    if not mutations:
        return False
    if all(_mutation_is_explicitly_negated(lowered, match) for match in mutations):
        return False

    preview_only = lowered.find("preview-only")
    first_unnegated = next(
        match
        for match in mutations
        if not _mutation_is_explicitly_negated(lowered, match)
    )
    if preview_only >= 0 and preview_only < first_unnegated.start():
        return False
    preview = re.search(r"\bpreview(?:s|ed|ing)?\b", lowered)
    if preview and preview.start() < first_unnegated.start():
        return False
    return True


def _has_public_cli_invocation(
    text: str,
    route: tuple[str, ...],
    arguments: tuple[str, ...] = (),
) -> bool:
    command_tokens = (*route, *arguments)
    command_pattern = r"[ \t]+".join(re.escape(token) for token in command_tokens)
    invocation = re.compile(
        rf"(?<![\w./-])(?:cc|python[ \t]+scripts/cc\.py)[ \t]+"
        rf"{command_pattern}(?![\w./<>=-])",
        re.IGNORECASE,
    )
    return bool(invocation.search(text))


def _public_truth_findings(project: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    schema_text = _read_text(project / "scripts" / "cc_memory_lib" / "schema.py")
    package_version, package_issue = _read_project_version(project / "pyproject.toml")
    runtime_match = re.search(
        r'^CONTROLCODING_VERSION\s*=\s*"([^"]+)"\s*$',
        schema_text,
        re.MULTILINE,
    )

    runtime_version = runtime_match.group(1) if runtime_match else ""
    if package_issue:
        findings.append(_finding(
            "error",
            "product_version_consistency",
            "pyproject.toml does not declare one readable [project].version.",
            path="pyproject.toml",
            detail=package_issue,
        ))
    if not runtime_version:
        findings.append(_finding(
            "error",
            "product_version_consistency",
            "scripts/cc_memory_lib/schema.py does not declare CONTROLCODING_VERSION.",
            path="scripts/cc_memory_lib/schema.py",
        ))
    if package_version and runtime_version != f"v{package_version}":
        findings.append(_finding(
            "error",
            "product_version_consistency",
            "Package and runtime product versions do not agree.",
            path="scripts/cc_memory_lib/schema.py",
            detail=f"package={package_version}; runtime={runtime_version or 'missing'}",
        ))
    if package_version:
        for relative_path in _PUBLIC_PRODUCT_VERSION_SOURCES:
            text = _read_text(project / relative_path)
            if package_version not in text:
                findings.append(_finding(
                    "error",
                    "product_version_consistency",
                    f"Public product-version source does not mention {package_version}.",
                    path=relative_path,
                ))

    for relative_path in _PUBLIC_TRUTH_DOCUMENTS:
        text = _read_text(project / relative_path)
        for pattern in _CURRENT_TEST_COUNT_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(_finding(
                    "error",
                    "unverified_current_test_count",
                    "Current numerical test-suite claims require a verified final release receipt.",
                    path=relative_path,
                    detail=match.group(0),
                ))
                break

    if package_version:
        changelog = _read_text(project / "CHANGELOG.md")
        changelog_sections, changelog_issues = _current_changelog_sections(
            changelog,
            package_version,
        )
        for issue in changelog_issues:
            findings.append(_finding(
                "error",
                "current_changelog_sections",
                "Current changelog sections cannot be selected unambiguously.",
                path="CHANGELOG.md",
                detail=issue,
            ))
        for section in changelog_sections:
            for pattern in _CURRENT_TEST_COUNT_PATTERNS:
                match = pattern.search(section)
                if match:
                    findings.append(_finding(
                        "error",
                        "unverified_current_test_count",
                        "Current numerical test-suite claims require a verified final release receipt.",
                        path="CHANGELOG.md",
                        detail=match.group(0),
                    ))
                    break

    tools_reference_path = "docs/ccdocs/tools-reference.md"
    file_standard_path = "docs/file-organization-standard.md"
    tools_reference = _read_text(project / tools_reference_path)
    file_standard = _read_text(project / file_standard_path)
    organize_sources = (
        (tools_reference_path, tools_reference),
        (file_standard_path, file_standard),
    )
    for required in ("cc organize", "preview-only", "--apply", "--dry-run", "--check"):
        if not any(required in text.casefold() for _, text in organize_sources):
            findings.append(_finding(
                "error",
                "organize_public_truth",
                f"Public organize guidance is missing {required}.",
                path=tools_reference_path,
            ))
    for relative_path, text in organize_sources:
        for kind, block in _markdown_claim_blocks(text):
            organize_context = False
            for statement in _markdown_block_statements(kind, block):
                command_names = _PUBLIC_COMMAND_MENTION_RE.findall(statement)
                if command_names:
                    organize_context = any(
                        name.casefold() == "organize" for name in command_names
                    )
                if organize_context and _organize_statement_mutates_by_default(statement):
                    findings.append(_finding(
                        "error",
                        "organize_public_truth",
                        "Public documentation claims that cc organize mutates by default.",
                        path=relative_path,
                        detail=statement,
                    ))

    resume_truth = tools_reference.casefold()
    for required in ("cc resume", "provider-neutral", "does not select or", "--brief"):
        if required not in resume_truth:
            findings.append(_finding(
                "error",
                "resume_public_truth",
                f"Public resume guidance is missing {required}.",
                path="docs/ccdocs/tools-reference.md",
            ))

    cli_path = project / "scripts" / "cc.py"
    try:
        cli_source = cli_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        findings.append(_finding(
            "error",
            "cli_public_truth",
            "Public CLI source is missing or is not readable UTF-8.",
            path="scripts/cc.py",
        ))
    else:
        findings.extend(_cli_public_truth_findings(cli_source))

    benchmark_examples = (
        (
            "cc benchmark compare <baseline> <current>",
            ("benchmark", "compare"),
            ("<baseline>", "<current>"),
        ),
        ("cc benchmark report", ("benchmark", "report"), ()),
    )
    for label, route, arguments in benchmark_examples:
        if not _has_public_cli_invocation(tools_reference, route, arguments):
            findings.append(_finding(
                "error",
                "benchmark_public_truth",
                f"Public benchmark guidance is missing {label}.",
                path="docs/ccdocs/tools-reference.md",
            ))
    for required in ("benchmarks/run.json", "benchmarks/report.md"):
        if required not in tools_reference:
            findings.append(_finding(
                "error",
                "benchmark_public_truth",
                f"Public benchmark guidance is missing {required}.",
                path="docs/ccdocs/tools-reference.md",
            ))
    for obsolete in ("benchmark-run.json", "benchmark-report.md"):
        if obsolete in tools_reference:
            findings.append(_finding(
                "error",
                "benchmark_public_truth",
                f"Public benchmark guidance still uses obsolete default {obsolete}.",
                path="docs/ccdocs/tools-reference.md",
            ))

    for relative_path in _PUBLIC_TRUTH_DOCUMENTS:
        text = _read_text(project / relative_path)
        for line in text.splitlines():
            lowered = line.casefold()
            if "cc resume" in lowered and any(
                phrase in lowered
                for phrase in ("launches claude", "launch a new claude", "starts claude", "starts a new claude")
            ) and not any(
                phrase in lowered
                for phrase in ("does not", "never", "no longer")
            ):
                findings.append(_finding(
                    "error",
                    "resume_public_truth",
                    "Public documentation claims that cc resume launches a provider.",
                    path=relative_path,
                    detail=line.strip(),
                ))
        for kind, block in _markdown_claim_blocks(text):
            for statement in _markdown_block_statements(kind, block):
                lowered = statement.casefold()
                if _REMOVED_COMMAND_MENTION_RE.search(lowered) and not any(
                    phrase in lowered
                    for phrase in ("removed", "no longer", "historical")
                ):
                    findings.append(_finding(
                        "error",
                        "removed_command_public_truth",
                        "Public documentation presents cc replace as a current command.",
                        path=relative_path,
                        detail=statement,
                    ))

    memory_notes_path = "docs/memory-graphrag-release-notes.md"
    memory_notes = _read_text(project / memory_notes_path)
    controlwork_requirements = (
        "Standalone ControlWork checkout only",
        "scripts/cw.py` is not included in ControlCoding V1",
        "cc.py memory work-graph status",
        "cc.py memory work-graph suggestions",
        "cc.py memory work-retrieve",
        "cc.py memory work-rag-pack",
        "cc.py memory work-views",
    )
    for required in controlwork_requirements:
        if required not in memory_notes:
            findings.append(_finding(
                "error",
                "controlwork_command_scope",
                f"ControlWork command guidance is missing {required}.",
                path=memory_notes_path,
            ))
    return findings


def docs_audit_payload(project: Path, since: str = "HEAD") -> dict[str, Any]:
    project = project.resolve()
    findings: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    release_policy = load_release_manifest_contract(project)

    if release_policy["present"] and not release_policy["ok"]:
        findings.extend(
            _finding(
                "error",
                "release_manifest_contract",
                f"Release manifest docs contract is invalid: {issue}",
                path=RELEASE_MANIFEST_FILE,
                recommendation="Repair the release manifest before relying on release-aware docs checks.",
            )
            for issue in release_policy["issues"]
        )

    required_doc_roles = dict(REQUIRED_SYSTEM_DOCS)
    if release_policy["present"] and release_policy["ok"]:
        for relative_path in release_policy["required"]:
            if relative_path.casefold().endswith(".md"):
                required_doc_roles.setdefault(relative_path, "release manifest required document")

    for relative_path, role in required_doc_roles.items():
        path = project / relative_path
        exists = path.is_file()
        release_denied = _release_path_denied(relative_path, release_policy)
        documents.append({
            "path": relative_path,
            "role": role,
            "exists": exists,
            "required": not release_denied,
            "releaseDenied": release_denied,
        })
        if not exists and not release_denied:
            findings.append(
                _finding(
                    "error",
                    "required_doc_exists",
                    f"Required system document is missing: {relative_path}",
                    path=relative_path,
                    recommendation="Create or restore the document before relying on docs maintenance checks.",
                )
            )

    index_text = _read_text(project / "docs" / "INDEX.md")
    for reference in DOC_INDEX_REQUIRED_REFERENCES:
        target_path = _reference_target_path("docs/INDEX.md", reference)
        if _release_path_denied(target_path, release_policy):
            continue
        if index_text and reference not in index_text:
            findings.append(
                _finding(
                    "error",
                    "docs_index_reference",
                    f"docs/INDEX.md does not reference {reference}.",
                    path="docs/INDEX.md",
                    recommendation=f"Add {reference} to docs/INDEX.md.",
                )
            )

    architecture_path = project / "docs" / "controlcoding-system-architecture.md"
    architecture_text = _read_text(architecture_path)
    for reference in REQUIRED_ARCHITECTURE_REFERENCES:
        target_path = _reference_target_path(
            "docs/controlcoding-system-architecture.md",
            reference,
        )
        if _release_path_denied(target_path, release_policy):
            continue
        if architecture_text and reference not in architecture_text:
            findings.append(
                _finding(
                    "error",
                    "architecture_reference",
                    f"System architecture overview does not link to {reference}.",
                    path="docs/controlcoding-system-architecture.md",
                    recommendation=f"Add a link or explicit reference to {reference}.",
                )
            )

    if release_policy["present"]:
        findings.extend(_public_truth_findings(project))
    findings.extend(_docs_plan_status_findings(project))
    findings.extend(_duplicate_audit_findings(project))
    findings.extend(_audit_outside_reports_findings(project))

    git_payload = _git_changed_files(project, since=since)
    if git_payload.get("available"):
        findings.extend(_doc_alignment_findings(list(git_payload.get("changedFiles", []))))
    else:
        findings.append(
            _finding(
                "info",
                "git_status_unavailable",
                "Git status is unavailable, so changed-file documentation drift was not evaluated.",
                detail=str(git_payload.get("error") or ""),
            )
        )

    errors = sum(1 for item in findings if item["severity"] == "error")
    warnings = sum(1 for item in findings if item["severity"] == "warn")
    infos = sum(1 for item in findings if item["severity"] == "info")
    return {
        "schemaVersion": DOCS_MAINTENANCE_SCHEMA_VERSION,
        "ok": errors == 0,
        "generatedAt": _now_iso(),
        "projectRoot": str(project),
        "profile": "release_manifest" if release_policy["present"] else "development",
        "releaseManifest": release_policy,
        "since": since,
        "documents": documents,
        "git": git_payload,
        "summary": {
            "documents": len(documents),
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "changedFiles": len(git_payload.get("changedFiles", [])),
            "proposalRecommended": bool(findings),
        },
        "findings": findings,
    }


def _print_payload(json_output: bool, payload: dict[str, Any]) -> None:
    if json_output:
        print(json.dumps(payload, indent=2))
        return
    summary = payload["summary"]
    status = "OK" if payload["ok"] else "ISSUES"
    print(f"Docs maintenance audit: {status}")
    print(
        f"  errors={summary['errors']} warnings={summary['warnings']} "
        f"infos={summary['infos']} changedFiles={summary['changedFiles']}"
    )
    for finding in payload["findings"]:
        path = f" [{finding['path']}]" if finding.get("path") else ""
        print(f"  {finding['severity'].upper()}: {finding['check']}{path} - {finding['message']}")
        if finding.get("recommendation"):
            print(f"    fix: {finding['recommendation']}")


def cmd_docs_audit(project: Path, since: str = "HEAD", json_output: bool = False) -> int:
    payload = docs_audit_payload(project, since=since)
    _print_payload(json_output, payload)
    return 0 if payload["ok"] else 1


def cmd_docs_check(
    project: Path,
    since: str = "HEAD",
    strict: bool = False,
    json_output: bool = False,
) -> int:
    payload = docs_audit_payload(project, since=since)
    if strict and payload["summary"]["warnings"]:
        payload["ok"] = False
        payload["findings"].append(
            _finding(
                "error",
                "strict_warnings",
                "Strict docs check treats documentation warnings as failures.",
                recommendation="Resolve warnings or rerun without --strict.",
            )
        )
        payload["summary"]["errors"] += 1
    _print_payload(json_output, payload)
    return 0 if payload["ok"] else 1


def _proposal_path(project: Path, output: Path | None) -> Path:
    if output is None:
        return project / ".controlcoding" / "docs" / "proposals" / f"{_stamp()}-docs-maintenance.md"
    target = output if output.is_absolute() else project / output
    target = target.resolve()
    project_resolved = project.resolve()
    try:
        target.relative_to(project_resolved)
    except ValueError as exc:
        raise ValueError("proposal output must stay inside the project root") from exc
    return target


def _render_proposal(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Docs Maintenance Proposal",
        "",
        f"- **Schema**: {payload['schemaVersion']}",
        f"- **Generated**: {payload['generatedAt']}",
        f"- **Since**: {payload['since']}",
        f"- **Errors**: {payload['summary']['errors']}",
        f"- **Warnings**: {payload['summary']['warnings']}",
        f"- **Infos**: {payload['summary']['infos']}",
        "",
        "## Purpose",
        "",
        "This proposal records architecture and system-document maintenance findings.",
        "It is a review artifact. It does not update canonical documents by itself.",
        "",
        "## Findings",
        "",
    ]
    if not payload["findings"]:
        lines.append("- No findings.")
    for finding in payload["findings"]:
        path = f" `{finding['path']}`" if finding.get("path") else ""
        lines.append(f"- **{finding['severity']}** `{finding['check']}`{path}: {finding['message']}")
        if finding.get("detail"):
            lines.append(f"  Detail: {finding['detail']}")
        if finding.get("recommendation"):
            lines.append(f"  Recommendation: {finding['recommendation']}")
        if finding.get("related"):
            lines.append("  Related: " + ", ".join(f"`{item}`" for item in finding["related"]))
    lines.extend([
        "",
        "## Changed Files",
        "",
    ])
    changed = payload.get("git", {}).get("changedFiles", [])
    if changed:
        lines.extend(f"- `{path}`" for path in changed)
    else:
        lines.append("- No changed files reported, or git status was unavailable.")
    lines.extend([
        "",
        "## Suggested Review Commands",
        "",
        "```powershell",
        "python scripts\\cc.py docs audit --project-root .",
        "python scripts\\cc.py docs check --project-root .",
        "python scripts\\cc.py truth check --include-docs --project-root .",
        "```",
        "",
        "## Non-Goals",
        "",
        "- Do not treat this proposal as canonical architecture.",
        "- Do not apply changes without reviewing the affected source documents.",
        "- Do not rewrite generated host context or memory views from this proposal.",
    ])
    return "\n".join(lines) + "\n"


def cmd_docs_propose(
    project: Path,
    since: str = "HEAD",
    output: Path | None = None,
    json_output: bool = False,
) -> int:
    project = project.resolve()
    payload = docs_audit_payload(project, since=since)
    try:
        path = _proposal_path(project, output)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_proposal(payload), encoding="utf-8")
    result = {
        "ok": True,
        "proposal": _rel(project, path),
        "auditOk": payload["ok"],
        "summary": payload["summary"],
    }
    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Docs maintenance proposal written: {_rel(project, path)}")
        if not payload["ok"]:
            print("Audit has errors; review the proposal before changing canonical docs.")
    return 0


def add_docs_parser(subparsers: Any, path_type: Any) -> Any:
    p_docs = subparsers.add_parser(
        "docs",
        help="Audit architecture and system-document maintenance state",
    )
    p_docs.add_argument(
        "--project-root", type=path_type, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    docs_sub = p_docs.add_subparsers(dest="docs_command")

    p_audit = docs_sub.add_parser("audit", help="Audit architecture and system-document drift")
    p_audit.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_audit.add_argument("--since", default="HEAD", help="Git ref used for changed-file drift checks")
    p_audit.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_check = docs_sub.add_parser("check", help="CI-friendly docs maintenance check")
    p_check.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_check.add_argument("--since", default="HEAD", help="Git ref used for changed-file drift checks")
    p_check.add_argument("--strict", action="store_true", help="Fail when warnings are present")
    p_check.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_propose = docs_sub.add_parser("propose", help="Write a reviewable docs maintenance proposal")
    p_propose.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_propose.add_argument("--since", default="HEAD", help="Git ref used for changed-file drift checks")
    p_propose.add_argument("--output", type=path_type, default=None, help="Proposal output path inside the project root")
    p_propose.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")
    return p_docs


def dispatch_docs_command(args: Any, project: Path, parser: Any) -> int:
    docs_command = getattr(args, "docs_command", "")
    if docs_command == "audit":
        return cmd_docs_audit(
            project,
            since=getattr(args, "since", "HEAD"),
            json_output=getattr(args, "json_output", False),
        )
    if docs_command == "check":
        return cmd_docs_check(
            project,
            since=getattr(args, "since", "HEAD"),
            strict=getattr(args, "strict", False),
            json_output=getattr(args, "json_output", False),
        )
    if docs_command == "propose":
        return cmd_docs_propose(
            project,
            since=getattr(args, "since", "HEAD"),
            output=getattr(args, "output", None),
            json_output=getattr(args, "json_output", False),
        )
    parser.print_help()
    return 1


__all__ = [
    "DOCS_MAINTENANCE_CAPABILITY_REGISTRY_ITEM",
    "DOCS_MAINTENANCE_ROUTED_COMMANDS",
    "DOCS_MAINTENANCE_SCHEMA_VERSION",
    "add_docs_parser",
    "cmd_docs_audit",
    "cmd_docs_check",
    "cmd_docs_propose",
    "dispatch_docs_command",
    "docs_audit_payload",
    "load_release_manifest_contract",
    "release_manifest_path_matches",
]
