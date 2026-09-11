#!/usr/bin/env python3
"""Tests for cc.py - ControlCoding CLI (commands and helpers).

test_gitignore.py already covers: _ensure_gitignore, _central_hooks_dir,
_get_hooks_location, _save_hooks_location, GITIGNORE_BLOCK.

This file covers: load_settings, save_settings, merge_hooks, merge_mcp_servers,
_resolve_hook_commands, cmd_init, cmd_doctor, cmd_review, _collect_project_files.
"""

import ast
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 compatibility.
    tomllib = None

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
if "cc" in sys.modules:
    del sys.modules["cc"]
import cc
import cc_feature
import cc_docs
import cc_review
import cc_init_module
from cc_memory_lib import lockfile as cc_lockfile
if "cc_setup" in sys.modules:
    del sys.modules["cc_setup"]
import cc_setup


# ------------------------------------------------------------------ helpers ---


CC_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cc.py"


def _run_cc(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CC_SCRIPT), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
    )


def _assert_pure_json_object(stdout: str) -> dict:
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    assert stdout.lstrip().startswith("{")
    assert stdout.rstrip().endswith("}")
    decoded, end = json.JSONDecoder().raw_decode(stdout)
    assert isinstance(decoded, dict)
    assert stdout[end:].strip() == ""
    return payload


def _make_healthy_project(tmp_path: Path) -> Path:
    """Create a project that passes cmd_doctor with 0 issues."""
    # CLAUDE.md with enough content
    lines = ["# TestProject", "", "## Architecture Rules", ""]
    lines += ["## Boundaries", "", "## Invariants", ""]
    lines += [f"## Zone Stable rule {i}" for i in range(50)]
    (tmp_path / "CLAUDE.md").write_text("\n".join(lines), encoding="utf-8")

    # STATUS.md
    (tmp_path / "STATUS.md").write_text("# Status", encoding="utf-8")

    # devlog/
    (tmp_path / "devlog").mkdir()

    # hooks/ with essential files
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "check_boundaries.py").write_text("# stub", encoding="utf-8")
    (hooks / "check_dangerous_commands.py").write_text("# stub", encoding="utf-8")
    (hooks / "check_repo_boundaries.py").write_text("# stub", encoding="utf-8")
    (hooks / "codewarden_review.py").write_text("# stub", encoding="utf-8")

    # .controlcoding/settings.json
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "python hooks/check_boundaries.py"}]}
            ]
        }
    }
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    (control_dir / "settings.json").write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )
    (control_dir / "cc_config.json").write_text(
        json.dumps(
            {
                "documentation_mode": "managed",
                "cc_artifact_mode": "local_only",
                "hooks_location": "local",
                "protected_zones": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".claude").mkdir()

    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "fitness_check.py").write_text("# stub", encoding="utf-8")

    # .gitignore with CC block
    (tmp_path / ".gitignore").write_text(
        f"{cc.GITIGNORE_MARKER_START}\n# stuff\n{cc.GITIGNORE_MARKER_END}\n",
        encoding="utf-8",
    )

    return tmp_path


def _install_repo_side_gate_baseline(project: Path) -> None:
    git_hooks = project / ".git" / "hooks"
    git_hooks.mkdir(parents=True, exist_ok=True)
    hooks_dir = project / "hooks"
    fitness_path = project / "tools" / "fitness_check.py"
    (git_hooks / "pre-commit").write_text(
        cc._build_repo_precommit_hook_script(hooks_dir, fitness_path),
        encoding="utf-8",
    )
    (git_hooks / "post-commit").write_text(
        cc._build_repo_postcommit_hook_script(hooks_dir),
        encoding="utf-8",
    )


def _write_minimal_verification_contract(project: Path) -> None:
    (project / "controlcoding.verification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredKinds": ["targeted", "regression", "invariant"],
                "suites": [
                    {
                        "id": "targeted",
                        "kind": "targeted",
                        "required": True,
                        "command": "python -c \"print('targeted')\"",
                    },
                    {
                        "id": "regression",
                        "kind": "regression",
                        "required": True,
                        "command": "python -c \"print('regression')\"",
                    },
                    {
                        "id": "invariant",
                        "kind": "invariant",
                        "required": True,
                        "command": "python -c \"print('invariant')\"",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_docs_maintenance_baseline(project: Path) -> None:
    docs_dir = project / "docs"
    ccdocs_dir = docs_dir / "ccdocs"
    ccdocs_dir.mkdir(parents=True, exist_ok=True)
    (project / "CONTROLCODING.md").write_text("# ControlCoding\n", encoding="utf-8")
    (docs_dir / "INDEX.md").write_text(
        "\n".join(
            [
                "# Docs Index",
                "",
                "- [controlcoding-system-architecture.md](./controlcoding-system-architecture.md)",
                "- [docs-maintenance-plan.md](./docs-maintenance-plan.md)",
            ]
        ),
        encoding="utf-8",
    )
    (docs_dir / "controlcoding-system-architecture.md").write_text(
        "\n".join(
            [
                "# ControlCoding System Architecture",
                "",
                "- [`../CONTROLCODING.md`](../CONTROLCODING.md)",
                "- [`release-model.md`](./release-model.md)",
                "- [`project-memory-engine.md`](./project-memory-engine.md)",
                "- [`memory-system-schema.md`](./memory-system-schema.md)",
                "- [`memory-graph-contract.md`](./memory-graph-contract.md)",
                "- [`file-organization-standard.md`](./file-organization-standard.md)",
                "- [`controlwork-advanced-memory-phase-2-plan.md`](./controlwork-advanced-memory-phase-2-plan.md)",
                "- [`session-graphrag-plan.md`](./session-graphrag-plan.md)",
                "- [`docs-maintenance-plan.md`](./docs-maintenance-plan.md)",
            ]
        ),
        encoding="utf-8",
    )
    for relative in [
        "docs/docs-maintenance-plan.md",
        "docs/release-model.md",
        "docs/project-memory-engine.md",
        "docs/memory-system-schema.md",
        "docs/memory-graph-contract.md",
        "docs/file-organization-standard.md",
        "docs/controlwork-advanced-memory-phase-2-plan.md",
        "docs/session-graphrag-plan.md",
        "docs/ccdocs/tools-reference.md",
        "docs/ccdocs/hooks-reference.md",
    ]:
        (project / relative).write_text(f"# {Path(relative).stem}\n", encoding="utf-8")


def _write_work_plane_contract_document(
    project: Path,
    *,
    identity: str | None = "controlwork-work-plane/1.0.0",
    status: str = "non-canonical",
) -> None:
    docs_dir = project / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Work Plane Compatibility Contract",
        "",
        "Contract ID: `controlwork-work-plane`",
        "Contract version: `1.0.0`",
    ]
    if identity is not None:
        lines.append(f"Contract identity: `{identity}`")
    lines.extend([
        "Canonical authority: ControlWork repository, docs/work-plane-compatibility-contract.md",
        "Profile: `ControlCoding embedded conformance profile`",
        f"Status: `{status}`",
        "Technical effective date: `2026-09-02`",
        "",
    ])
    (project / cc_docs.WORK_PLANE_CONTRACT_DOCUMENT).write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _write_work_plane_release_fixture(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    _write_work_plane_contract_document(project)
    for relative in cc_docs.RELEASE_REQUIRED_LEGAL_FILES:
        (project / relative).write_text("# public release fixture\n", encoding="utf-8")
    (project / "controlcoding.release.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "package": "controlcoding-core-source",
                "workPlaneContract": "controlwork-work-plane/1.0.0",
                "allow": [
                    {"pattern": "NOTICE", "reason": "Public notice."},
                    {"pattern": "TRADEMARKS.md", "reason": "Public trademark guidance."},
                    {"pattern": "docs/**", "reason": "Public documentation."},
                ],
                "deny": [],
                "required": [
                    *cc_docs.RELEASE_REQUIRED_LEGAL_FILES,
                    cc_docs.WORK_PLANE_CONTRACT_DOCUMENT,
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_public_docs_release_fixture(project: Path) -> None:
    _write_docs_maintenance_baseline(project)
    _write_work_plane_contract_document(project)
    for relative in cc_docs.RELEASE_REQUIRED_LEGAL_FILES:
        (project / relative).write_text("# public release fixture\n", encoding="utf-8")
    (project / "scripts" / "cc_memory_lib").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "controlcoding"\nversion = "3.0.0"\n',
        encoding="utf-8",
    )
    (project / "scripts" / "cc_memory_lib" / "schema.py").write_text(
        'SCHEMA_VERSION = 5\nCONTROLCODING_VERSION = "v3.0.0"\n',
        encoding="utf-8",
    )
    (project / "scripts" / "cc.py").write_text(
        "\n".join([
            "import argparse",
            "from pathlib import Path",
            "",
            'BENCHMARK_RUN_OUTPUT = Path("benchmarks") / "run.json"',
            'BENCHMARK_REPORT_OUTPUT = Path("benchmarks") / "report.md"',
            "_ROUTED_CLI_COMMANDS = frozenset({",
            '    ("benchmark",),',
            '    ("benchmark", "run"),',
            '    ("benchmark", "compare"),',
            '    ("benchmark", "report"),',
            '    ("organize",),',
            '    ("resume",),',
            "})",
            "",
            "def cmd_benchmark_run():",
            "    return 0",
            "",
            "def cmd_benchmark_compare():",
            "    return 0",
            "",
            "def cmd_benchmark_report():",
            "    return 0",
            "",
            "def cmd_organize():",
            "    return 0",
            "",
            "def cmd_resume():",
            "    return 0",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            '    sub = parser.add_subparsers(dest="command")',
            '    p_benchmark = sub.add_parser("benchmark")',
            '    benchmark_sub = p_benchmark.add_subparsers(dest="benchmark_run_command")',
            '    p_benchmark_run = benchmark_sub.add_parser("run")',
            '    p_benchmark_run.add_argument("--output", default=BENCHMARK_RUN_OUTPUT)',
            '    p_benchmark_compare = benchmark_sub.add_parser("compare")',
            '    p_benchmark_compare.add_argument("baseline", type=Path)',
            '    p_benchmark_compare.add_argument("current", type=Path)',
            '    p_benchmark_report = benchmark_sub.add_parser("report")',
            '    p_benchmark_report.add_argument("--output", default=BENCHMARK_REPORT_OUTPUT)',
            '    p_organize = sub.add_parser("organize")',
            '    p_organize.add_argument("--apply", action="store_true")',
            '    p_resume = sub.add_parser("resume", help="Print a provider-neutral context brief without launching a provider or subprocess")',
            '    p_resume.add_argument("--brief", action="store_true", help="Accepted for CLI compatibility; the current command always prints the brief")',
            "",
            "    args, extra_args = parser.parse_known_args()",
            '    if args.command == "benchmark":',
            '        benchmark_run_command = getattr(args, "benchmark_run_command", "")',
            '        if benchmark_run_command == "run":',
            "            return cmd_benchmark_run()",
            '        if benchmark_run_command == "compare":',
            "            return cmd_benchmark_compare()",
            '        if benchmark_run_command == "report":',
            "            return cmd_benchmark_report()",
            '    if args.command == "organize":',
            "        return cmd_organize()",
            '    if args.command == "resume":',
            "        return cmd_resume()",
            "",
        ]),
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# ControlCoding\n\nCurrent LAB release candidate: `3.0.0`.\n",
        encoding="utf-8",
    )
    (project / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 3.0.0\n\nMajor public CLI alignment.\n",
        encoding="utf-8",
    )
    (project / "docs" / "release-model.md").write_text(
        "# Release Model\n\n`3.0.0` is a candidate, not a published release.\n",
        encoding="utf-8",
    )
    (project / "docs" / "ccdocs" / "tools-reference.md").write_text(
        "\n".join([
            "# Tools Reference",
            "",
            "`cc benchmark compare <baseline> <current>` compares snapshots.",
            "`cc benchmark report` writes `benchmarks/report.md`.",
            "Benchmark run defaults to `benchmarks/run.json`.",
            "`cc organize` is preview-only; use `--apply` to apply.",
            "`--dry-run` and `--check` do not apply changes.",
            "`cc resume` is provider-neutral and does not select or launch a provider.",
            "`--brief` remains accepted for compatibility.",
            "`cc replace start/status/complete` was removed.",
            "",
        ]),
        encoding="utf-8",
    )
    (project / "docs" / "file-organization-standard.md").write_text(
        "# File Organization\n\n`cc organize` is preview-only; use `--apply`. "
        "`--dry-run` and `--check` do not apply changes.\n",
        encoding="utf-8",
    )
    (project / "docs" / "memory-graphrag-release-notes.md").write_text(
        "\n".join([
            "# Memory GraphRAG Release Notes",
            "",
            "Standalone ControlWork checkout only:",
            "`scripts/cw.py` is not included in ControlCoding V1.",
            "Embedded equivalents are `cc.py memory work-graph status`,",
            "`cc.py memory work-graph suggestions`, `cc.py memory work-retrieve`,",
            "`cc.py memory work-rag-pack`, and `cc.py memory work-views`.",
            "",
        ]),
        encoding="utf-8",
    )
    (project / "docs" / "cross-tool-guide.md").write_text(
        "# Cross-Tool Guide\n\nPreview, explicit adoption, then sync.\n",
        encoding="utf-8",
    )
    (project / "docs" / "install-controlcoding-on-your-project.md").write_text(
        "# Install\n\nPreview, explicit adoption, then sync.\n",
        encoding="utf-8",
    )
    (project / "docs" / "quick-start.md").write_text(
        "# Quick Start\n\nPreview, explicit adoption, then sync.\n",
        encoding="utf-8",
    )
    denied_docs = {
        "docs/controlwork-advanced-memory-phase-2-plan.md",
        "docs/session-graphrag-plan.md",
    }
    for relative in denied_docs:
        (project / relative).unlink()

    architecture = project / "docs" / "controlcoding-system-architecture.md"
    architecture.write_text(
        "\n".join(
            line
            for line in architecture.read_text(encoding="utf-8").splitlines()
            if not any(Path(relative).name in line for relative in denied_docs)
        )
        + "\n",
        encoding="utf-8",
    )
    (project / "controlcoding.release.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "package": "controlcoding-core-source",
                "workPlaneContract": "controlwork-work-plane/1.0.0",
                "allow": [
                    {"pattern": "NOTICE", "reason": "Public notice."},
                    {"pattern": "TRADEMARKS.md", "reason": "Public trademark guidance."},
                    {"pattern": "CONTROLCODING.md", "reason": "Public architecture contract."},
                    {"pattern": "docs/**", "reason": "Public documentation."},
                ],
                "deny": [
                    {
                        "pattern": relative,
                        "reason": "Internal LAB plan excluded from the public package.",
                    }
                    for relative in sorted(denied_docs)
                ],
                "required": [
                    *cc_docs.RELEASE_REQUIRED_LEGAL_FILES,
                    "CONTROLCODING.md",
                    "docs/INDEX.md",
                    "docs/controlcoding-system-architecture.md",
                    "docs/project-memory-engine.md",
                    "docs/work-plane-compatibility-contract.md",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_docs_checker_regression_fixture(project: Path) -> None:
    _write_public_docs_release_fixture(project)
    (project / "docs" / "ccdocs" / "tools-reference.md").write_text(
        "\n".join([
            "# Tools Reference",
            "",
            "| Command | What it does |",
            "|---|---|",
            "| `cc init` | Non-interactive: copy hooks, create settings.json, generate CLAUDE.md skeleton |",
            "| `cc write-path status|enable|disable|prepare|check|apply` | Manage the opt-in controlled write path prototype for non-inline hosts |",
            "| `cc benchmark run|compare|report` | Capture, compare, and render local benchmark evidence |",
            "| `cc organize` | Preview file-organization changes by default; apply only with `--apply` |",
            "| `cc resume` | Print a provider-neutral context brief without launching a provider or subprocess |",
            "",
            "```bash",
            "python scripts/cc.py benchmark run",
            "python scripts/cc.py benchmark compare <baseline> <current>",
            "python scripts/cc.py benchmark report",
            "```",
            "",
            "`compare` takes baseline and current as positional inputs.",
            "Benchmark run defaults to `benchmarks/run.json`.",
            "Benchmark report defaults to `benchmarks/report.md`.",
            "`--dry-run` and `--check` do not apply changes.",
            "`cc resume` is provider-neutral and does not select or launch a provider.",
            "`--brief` remains accepted for compatibility.",
            "`cc replace start/status/complete` was removed.",
            "",
        ]),
        encoding="utf-8",
    )
    (project / "docs" / "file-organization-standard.md").write_text(
        "\n".join([
            "# File Organization",
            "",
            "Tool support:",
            "",
            "- `cc organize` is preview-only by default and reports proposed repairs without changing files",
            "- `cc organize --apply` is required to execute proposed moves and writes",
            "- `--dry-run` remains an explicit preview, and `--check` remains a non-applying control",
            "",
        ]),
        encoding="utf-8",
    )
    (project / "docs" / "release-model.md").write_text(
        "\n".join([
            "# Release Model",
            "",
            "ControlCoding `3.0.0` is a candidate, not a published release.",
            "The public route `cc replace start/status/complete` was",
            "removed, while the current benchmark interface was retained.",
            "",
        ]),
        encoding="utf-8",
    )


def _write_minimal_invariant_manifest(project: Path) -> None:
    (project / "controlcoding.invariants.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "projectType": "test",
                "domains": ["test"],
                "invariants": [
                    {
                        "id": "test-invariant",
                        "title": "Test Invariant",
                        "domain": "test",
                        "kind": "consistency",
                        "severity": "blocking",
                        "status": "active",
                        "property": "The test invariant must hold.",
                        "threshold": "0 failures",
                        "command": "python -c \"print('ok')\"",
                        "evidence": ["tests"],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_gateway_config(project: Path, user_host: str = "codex_cli") -> None:
    gateway = {
        "userHost": user_host,
        "enabledHosts": [user_host],
        "hostProfile": cc._derive_host_profile(user_host),
        "uiMode": "visualizer",
        "backends": {
            "claude_cli": {
                "id": "claude_cli",
                "type": "claude_cli",
            }
        },
        "agents": {
            "mainCoder": {"active": True, "backend": "claude_cli"},
            "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
            "consultants": {"maxSlots": 3, "defaults": []},
            "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
        },
    }
    (project / ".controlcoding" / "gateway_config.json").write_text(
        json.dumps(gateway, indent=2),
        encoding="utf-8",
    )


def _init_git_repo(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True, text=True)


def _make_surface_project(tmp_path: Path, ui_mode: str = "api_studio", user_host: str = "vscode") -> Path:
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir(exist_ok=True)
    (control_dir / "gateway_config.json").write_text(
        json.dumps(
            {
                "uiMode": ui_mode,
                "userHost": user_host,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return tmp_path


class TestPackagingMetadata:
    def test_pyproject_exposes_cc_entry_point_and_runtime_boundary(self):
        if tomllib is None:
            pytest.skip("tomllib is not available on this Python runtime")

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        project = data["project"]
        assert project["scripts"]["cc"] == "cc:main"
        assert project.get("dependencies", []) == []
        assert project["optional-dependencies"]["mcp"] == ["fastmcp"]
        assert project["optional-dependencies"]["dashboard"] == ["gradio"]
        assert project["optional-dependencies"]["studio"] == ["gradio"]
        assert project["optional-dependencies"]["visual"] == ["pyautogui"]
        assert project["optional-dependencies"]["vision"] == ["pyautogui"]
        assert project["optional-dependencies"]["semantic"] == []

        setuptools_config = data["tool"]["setuptools"]
        assert setuptools_config["package-dir"] == {"": "scripts"}
        assert set(setuptools_config["py-modules"]) >= {
            "cc",
            "cc_memory",
            "cc_setup",
            "cc_feature",
            "cc_docs",
            "cc_review",
            "cc_init_module",
        }
        finder = setuptools_config["packages"]["find"]
        assert finder["where"] == ["scripts"]
        assert "cc_memory_lib*" in finder["include"]

    def test_verification_workflow_commands_are_exact_step_specific_and_unique(self):
        workflow_path = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "controlcoding-verification.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")
        expected_commands = [
            (
                "Install test dependencies",
                ["python", "-m", "pip", "install", "--upgrade", "pip", "pytest", ".[mcp]"],
            ),
            (
                "Check verification contract",
                ["python", "scripts/cc.py", "verify", "status", "--project-root", "."],
            ),
            (
                "Run verification contract",
                ["python", "scripts/cc.py", "verify", "run", "--project-root", ".", "--json"],
            ),
        ]
        expected_names = {name for name, _ in expected_commands}
        execution_control_keys = {"if", "continue-on-error"}

        name_line = re.compile(
            r"(?P<prefix>(?P<step_indent>[ ]*)-[ ]+(?P<key>name)[ ]*:[ ]*)"
            r"(?P<value>.*?)[ ]*"
        )
        run_line = re.compile(
            r"(?P<prefix>(?P<indent>[ ]*)(?P<key>run)[ ]*:[ ]*)"
            r"(?P<value>.*?)[ ]*"
        )
        mapping_line = re.compile(
            r"(?P<indent>[ ]*)(?P<key>"
            r"[A-Za-z0-9_.-]+|'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\""
            r")[ ]*:[ ]*(?P<value>.*?)[ ]*"
        )
        sequence_item_line = re.compile(r"(?P<indent>[ ]*)-[ ]+.*")

        def yaml_scalar(value):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == "'":
                return value[1:-1].replace("''", "'")
            if len(value) >= 2 and value[0] == value[-1] == '"':
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    pass
            return value

        def line_indent(line):
            return len(line) - len(line.lstrip(" "))

        def step_from_name_match(name_match):
            return {
                "name": yaml_scalar(name_match["value"]),
                "sequence_indent": len(name_match["step_indent"]),
                "field_indent": name_match.start("key"),
                "runs": [],
                "execution_controls": [],
            }

        def step_has_ended(line, step):
            return (
                line.strip()
                and not line.lstrip().startswith("#")
                and line_indent(line) <= step["sequence_indent"]
            )

        def is_yaml_content(line):
            return bool(line.strip()) and not line.lstrip().startswith("#")

        def section_end(lines, start, parent_indent, limit=None):
            end = len(lines) if limit is None else limit
            index = start + 1
            while index < end:
                line = lines[index]
                if is_yaml_content(line) and line_indent(line) <= parent_indent:
                    break
                index += 1
            return index

        def direct_mapping_children(lines, start, end, parent_indent):
            candidates = []
            for index in range(start + 1, end):
                line = lines[index]
                if not is_yaml_content(line):
                    continue
                match = mapping_line.fullmatch(line)
                if match is not None and match.start("key") > parent_indent:
                    candidates.append((index, match))

            if not candidates:
                return []
            child_indent = min(match.start("key") for _, match in candidates)
            return [
                (index, match)
                for index, match in candidates
                if match.start("key") == child_indent
            ]

        def direct_execution_controls(fields):
            controls = []
            for _, field_match in fields:
                key = yaml_scalar(field_match["key"])
                if key in execution_control_keys:
                    controls.append((key, yaml_scalar(field_match["value"])))
            return controls

        def workflow_step_groups(lines):
            root_mappings = []
            for index, line in enumerate(lines):
                match = mapping_line.fullmatch(line)
                if (
                    match is not None
                    and match.start("key") == 0
                    and yaml_scalar(match["key"]) == "jobs"
                ):
                    root_mappings.append((index, match))

            job_groups = []
            step_groups = []
            for jobs_index, jobs_match in root_mappings:
                jobs_indent = jobs_match.start("key")
                jobs_end = section_end(lines, jobs_index, jobs_indent)
                jobs = direct_mapping_children(
                    lines,
                    jobs_index,
                    jobs_end,
                    jobs_indent,
                )
                job_groups.extend(jobs)
                for job_index, job_match in jobs:
                    job_indent = job_match.start("key")
                    job_end = section_end(lines, job_index, job_indent, jobs_end)
                    fields = direct_mapping_children(
                        lines,
                        job_index,
                        job_end,
                        job_indent,
                    )
                    job_execution_controls = direct_execution_controls(fields)
                    for field_index, field_match in fields:
                        if yaml_scalar(field_match["key"]) != "steps":
                            continue
                        steps_indent = field_match.start("key")
                        steps_end = section_end(
                            lines,
                            field_index,
                            steps_indent,
                            job_end,
                        )
                        sequence_indents = [
                            len(sequence_match["indent"])
                            for line in lines[field_index + 1 : steps_end]
                            if (sequence_match := sequence_item_line.fullmatch(line))
                            is not None
                            and len(sequence_match["indent"]) > steps_indent
                        ]
                        step_groups.append(
                            {
                                "start": field_index + 1,
                                "end": steps_end,
                                "job_execution_controls": job_execution_controls,
                                "job_steps_index": field_index,
                                "job_field_indent": steps_indent,
                                "sequence_indent": (
                                    min(sequence_indents)
                                    if sequence_indents
                                    else None
                                ),
                            }
                        )

            structure = {
                "jobs_sections": len(root_mappings),
                "jobs": len(job_groups),
                "steps_lists": len(step_groups),
            }
            return step_groups, structure

        def direct_step_run(lines, expected_name):
            direct_runs = []
            active_step = None
            for index, line in enumerate(lines):
                name_match = name_line.fullmatch(line)
                if name_match:
                    candidate_step = step_from_name_match(name_match)
                    if (
                        active_step is None
                        or candidate_step["sequence_indent"]
                        <= active_step["sequence_indent"]
                    ):
                        active_step = candidate_step
                    continue

                if active_step is not None and step_has_ended(line, active_step):
                    active_step = None

                run_match = run_line.fullmatch(line)
                if (
                    active_step is not None
                    and run_match is not None
                    and run_match.start("key") == active_step["field_indent"]
                    and active_step["name"] == expected_name
                ):
                    direct_runs.append((index, run_match, active_step))

            assert len(direct_runs) == 1
            return direct_runs[0]

        def workflow_commands(text):
            commands = []
            steps = []
            lines = text.splitlines()
            step_groups, structure = workflow_step_groups(lines)
            job_execution_controls = []
            for step_group in step_groups:
                job_execution_controls.extend(step_group["job_execution_controls"])
                active_step = None
                index = step_group["start"]
                while index < step_group["end"]:
                    line = lines[index]
                    name_match = name_line.fullmatch(line)
                    if (
                        name_match is not None
                        and len(name_match["step_indent"])
                        == step_group["sequence_indent"]
                    ):
                        active_step = step_from_name_match(name_match)
                        steps.append(active_step)
                        index += 1
                        continue

                    if active_step is not None and step_has_ended(line, active_step):
                        active_step = None

                    field_match = mapping_line.fullmatch(line)
                    if (
                        active_step is not None
                        and field_match is not None
                        and field_match.start("key") == active_step["field_indent"]
                    ):
                        key = yaml_scalar(field_match["key"])
                        if key in execution_control_keys:
                            active_step["execution_controls"].append(
                                (key, yaml_scalar(field_match["value"]))
                            )

                    run_match = run_line.fullmatch(line)
                    if (
                        active_step is not None
                        and run_match is not None
                        and run_match.start("key") == active_step["field_indent"]
                    ):
                        raw_command = run_match["value"].strip()
                        command_lines = []
                        if raw_command == "|":
                            block_lines = []
                            index += 1
                            while index < step_group["end"]:
                                block_line = lines[index]
                                if (
                                    block_line.strip()
                                    and line_indent(block_line)
                                    <= run_match.start("key")
                                ):
                                    break
                                block_lines.append(block_line)
                                index += 1

                            nonempty_block_lines = [
                                block_line
                                for block_line in block_lines
                                if block_line.strip()
                            ]
                            if nonempty_block_lines:
                                block_indent = min(
                                    line_indent(block_line)
                                    for block_line in nonempty_block_lines
                                )
                                command_lines = [
                                    block_line[block_indent:]
                                    for block_line in nonempty_block_lines
                                ]
                        else:
                            command = yaml_scalar(run_match["value"])
                            if command.strip():
                                command_lines = [command]
                            index += 1

                        for command_line in command_lines:
                            argv = shlex.split(command_line)
                            active_step["runs"].append(argv)
                            commands.append((active_step["name"], argv))
                        continue

                    index += 1
            return commands, steps, structure, job_execution_controls

        def render_workflow(lines, original_text):
            return "\n".join(lines) + ("\n" if original_text.endswith("\n") else "")

        def replace_step_run(text, expected_name, replacement_command):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines[index] = f"{run_match['prefix']}{replacement_command}"
            return render_workflow(lines, text)

        def nest_step_run(text, expected_name, mapping_name):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines[index : index + 1] = [
                f"{run_match['indent']}{mapping_name}:",
                f"{run_match['indent']}  run: {run_match['value']}",
            ]
            return render_workflow(lines, text)

        def block_step_run(text, expected_name):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines[index : index + 1] = [
                f"{run_match['indent']}run: |",
                f"{run_match['indent']}  {run_match['value']}",
            ]
            return render_workflow(lines, text)

        def add_direct_step_control(text, expected_name, key, value):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines.insert(index, f"{run_match['indent']}{key}: {value}")
            return render_workflow(lines, text)

        def add_direct_job_control(text, key, value):
            lines = text.splitlines()
            step_groups, structure = workflow_step_groups(lines)
            assert structure == {
                "jobs_sections": 1,
                "jobs": 1,
                "steps_lists": 1,
            }
            step_group = step_groups[0]
            lines.insert(
                step_group["job_steps_index"],
                f"{' ' * step_group['job_field_indent']}{key}: {value}",
            )
            return render_workflow(lines, text)

        def add_nested_step_controls(text, expected_name, mapping_name, controls):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines[index:index] = [
                f"{run_match['indent']}{mapping_name}:",
                *[
                    f"{run_match['indent']}  {key}: {value}"
                    for key, value in controls
                ],
            ]
            return render_workflow(lines, text)

        def add_nested_job_controls(text, mapping_name, controls):
            lines = text.splitlines()
            step_groups, structure = workflow_step_groups(lines)
            assert structure == {
                "jobs_sections": 1,
                "jobs": 1,
                "steps_lists": 1,
            }
            step_group = step_groups[0]
            job_indent = " " * step_group["job_field_indent"]
            lines[step_group["job_steps_index"] : step_group["job_steps_index"]] = [
                f"{job_indent}{mapping_name}:",
                *[
                    f"{job_indent}  {key}: {value}"
                    for key, value in controls
                ],
            ]
            return render_workflow(lines, text)

        def multi_command_block_step_run(text, expected_name, commands):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines[index : index + 1] = [
                f"{run_match['indent']}run: |",
                *[
                    f"{run_match['indent']}  {command}"
                    for command in commands
                ],
            ]
            return render_workflow(lines, text)

        def remove_step_run(text, expected_name):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines[index] = f"{run_match['indent']}uses: actions/cache@v4"
            return render_workflow(lines, text)

        def move_step_run_outside_step(text, expected_name):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            del lines[index]
            return f"{render_workflow(lines, text).rstrip()}\n\nrun: {run_match['value']}\n"

        def duplicate_direct_step_run(text, expected_name):
            lines = text.splitlines()
            index, run_match, _ = direct_step_run(lines, expected_name)
            lines.insert(index + 1, f"{run_match['prefix']}{run_match['value']}")
            return render_workflow(lines, text)

        def move_step_run_to_successor_step(text, expected_name):
            lines = text.splitlines()
            index, run_match, step = direct_step_run(lines, expected_name)
            lines[index : index + 1] = [
                f"{run_match['indent']}uses: actions/cache@v4",
                f"{' ' * step['sequence_indent']}- name: {expected_name}",
                f"{run_match['prefix']}{run_match['value']}",
            ]
            return render_workflow(lines, text)

        def quote_step_names(text, quote):
            lines = text.splitlines()
            quoted_names = set()
            for index, line in enumerate(lines):
                name_match = name_line.fullmatch(line)
                if not name_match:
                    continue
                step_name = yaml_scalar(name_match["value"])
                if step_name in expected_names:
                    lines[index] = f"{name_match['prefix']}{quote}{step_name}{quote}"
                    quoted_names.add(step_name)

            assert quoted_names == expected_names
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

        def reindent_steps(text):
            lines = text.splitlines()
            steps_found = False
            for index, line in enumerate(lines):
                if re.fullmatch(r"\s*steps\s*:\s*", line):
                    steps_found = True
                    continue
                if steps_found and line:
                    lines[index] = f"  {line}"

            assert steps_found
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

        def append_step(text, step_name, command):
            return (
                f"{text.rstrip()}\n\n"
                f"      - name: {step_name}\n"
                f"        run: {command}\n"
            )

        def replace_expected_steps_with_metadata_pseudo_steps(text):
            for expected_name, _ in expected_commands:
                text = remove_step_run(text, expected_name)
                text = text.replace(
                    f"- name: {expected_name}",
                    f"- name: Disabled {expected_name}",
                    1,
                )
            pseudo_steps = "".join(
                f"  - name: {expected_name}\n"
                f"    run: {shlex.join(expected_argv)}\n"
                for expected_name, expected_argv in expected_commands
            )
            return f"{text.rstrip()}\n\nmetadata:\n{pseudo_steps}"

        def assert_execution_controls_are_safe(controls):
            for key, value in controls:
                assert key != "if"
                assert key != "continue-on-error" or value.casefold() == "false"

        def assert_workflow_contract(text):
            commands, steps, structure, job_execution_controls = workflow_commands(text)
            assert structure == {
                "jobs_sections": 1,
                "jobs": 1,
                "steps_lists": 1,
            }
            assert_execution_controls_are_safe(job_execution_controls)
            assert commands == expected_commands
            for expected_name, expected_argv in expected_commands:
                matching_steps = [
                    step for step in steps if step["name"] == expected_name
                ]
                assert len(matching_steps) == 1
                assert matching_steps[0]["runs"] == [expected_argv]
                assert commands.count((expected_name, expected_argv)) == 1
                if expected_name == "Run verification contract":
                    assert_execution_controls_are_safe(
                        matching_steps[0]["execution_controls"]
                    )

        assert_workflow_contract(workflow)

        equivalent_workflows = {
            "unquoted mcp extra": replace_step_run(
                workflow,
                "Install test dependencies",
                "python -m pip install --upgrade pip pytest .[mcp]",
            ),
            "double-quoted CI step names": quote_step_names(workflow, '"'),
            "single-quoted CI step names": quote_step_names(workflow, "'"),
            "equivalently indented run block": reindent_steps(workflow),
            "direct literal run block": block_step_run(
                workflow,
                "Run verification contract",
            ),
            "explicitly false execution controls": add_direct_job_control(
                add_direct_step_control(
                    workflow,
                    "Run verification contract",
                    "continue-on-error",
                    '"false"',
                ),
                "continue-on-error",
                "'false'",
            ),
        }
        for representation_name, equivalent_workflow in equivalent_workflows.items():
            try:
                assert_workflow_contract(equivalent_workflow)
            except AssertionError as error:
                pytest.fail(
                    f"equivalent YAML representation was rejected: {representation_name}: {error}"
                )

        scoped_control_workflows = {
            "nested verification step controls": add_nested_step_controls(
                workflow,
                "Run verification contract",
                "env",
                [
                    ("if", "${{ false }}"),
                    ("continue-on-error", '"true"'),
                ],
            ),
            "nested verification job controls": add_nested_job_controls(
                workflow,
                "metadata",
                [
                    ("if", "${{ false }}"),
                    ("continue-on-error", "'true'"),
                ],
            ),
            "controls on a different step": add_direct_step_control(
                add_direct_step_control(
                    workflow,
                    "Check verification contract",
                    "if",
                    "${{ false }}",
                ),
                "Check verification contract",
                "continue-on-error",
                "true",
            ),
        }
        for representation_name, scoped_control_workflow in scoped_control_workflows.items():
            try:
                assert_workflow_contract(scoped_control_workflow)
            except AssertionError as error:
                pytest.fail(
                    "nested or unrelated execution control was treated as direct: "
                    f"{representation_name}: {error}"
                )

        negative_workflows = {
            "extra install dependency": replace_step_run(
                workflow,
                "Install test dependencies",
                "python -m pip install --upgrade pip pytest .[mcp] coverage",
            ),
            "missing mcp extra": replace_step_run(
                workflow,
                "Install test dependencies",
                "python -m pip install --upgrade pip pytest",
            ),
            "changed mcp extra": replace_step_run(
                workflow,
                "Install test dependencies",
                "python -m pip install --upgrade pip pytest .[dashboard]",
            ),
            "changed status arguments": replace_step_run(
                workflow,
                "Check verification contract",
                "python scripts/cc.py verify status --project-root ./controlcoding",
            ),
            "changed run arguments": replace_step_run(
                workflow,
                "Run verification contract",
                "python scripts/cc.py verify run --project-root .",
            ),
            "test filter": replace_step_run(
                workflow,
                "Run verification contract",
                "python scripts/cc.py verify run --project-root . --json -k smoke",
            ),
            "selective suite": replace_step_run(
                workflow,
                "Run verification contract",
                "python scripts/cc.py verify run --project-root . --json --suite targeted",
            ),
            "direct step if": add_direct_step_control(
                workflow,
                "Run verification contract",
                "if",
                "${{ false }}",
            ),
            "direct step continue-on-error": add_direct_step_control(
                workflow,
                "Run verification contract",
                "continue-on-error",
                "true",
            ),
            "quoted direct step continue-on-error": add_direct_step_control(
                workflow,
                "Run verification contract",
                "continue-on-error",
                "'true'",
            ),
            "direct job if": add_direct_job_control(
                workflow,
                "if",
                "${{ false }}",
            ),
            "direct job continue-on-error": add_direct_job_control(
                workflow,
                "continue-on-error",
                "true",
            ),
            "quoted direct job continue-on-error": add_direct_job_control(
                workflow,
                "continue-on-error",
                '"true"',
            ),
            "duplicate command": append_step(
                workflow,
                "Check verification contract",
                "python scripts/cc.py verify status --project-root .",
            ),
            "alternative command": replace_step_run(
                workflow,
                "Run verification contract",
                "python -m pytest tests/test_cc_cli.py",
            ),
            "two physical commands in literal run block": multi_command_block_step_run(
                workflow,
                "Run verification contract",
                [
                    "python scripts/cc.py verify",
                    "run --project-root . --json",
                ],
            ),
            "metadata pseudo-steps": replace_expected_steps_with_metadata_pseudo_steps(
                workflow,
            ),
            "env.run": nest_step_run(
                workflow,
                "Install test dependencies",
                "env",
            ),
            "with.run": nest_step_run(
                workflow,
                "Check verification contract",
                "with",
            ),
            "if.run": nest_step_run(
                workflow,
                "Run verification contract",
                "if",
            ),
            "dedented external run": move_step_run_outside_step(
                workflow,
                "Run verification contract",
            ),
            "missing direct run": remove_step_run(
                workflow,
                "Check verification contract",
            ),
            "duplicate direct run": duplicate_direct_step_run(
                workflow,
                "Install test dependencies",
            ),
            "run in following same-named step": move_step_run_to_successor_step(
                workflow,
                "Install test dependencies",
            ),
        }
        for mutation_name, mutated_workflow in negative_workflows.items():
            try:
                assert_workflow_contract(mutated_workflow)
            except AssertionError:
                continue
            pytest.fail(f"workflow mutation was not rejected: {mutation_name}")


class TestHostProfileDerivation:
    def test_claude_code_profile_is_inline_first(self):
        profile = cc._derive_host_profile("claude_code")
        assert profile["capabilityClass"] == "native_inline_hooks"
        assert profile["inlineBoundaryGate"] == "native_hooks"
        assert profile["protectionModel"] == "inline_first"
        assert profile["contextFile"] == "CLAUDE.md"

    def test_codex_cli_profile_is_repo_side(self):
        profile = cc._derive_host_profile("codex_cli")
        assert profile["capabilityClass"] == "sandbox_approval"
        assert profile["permissionGate"] == "sandbox_approvals"
        assert profile["inlineBoundaryGate"] == "none"
        assert profile["protectionModel"] == "repo_side"
        assert profile["contextFile"] == "AGENTS.md"

    def test_gemini_cli_profile_is_review_driven(self):
        profile = cc._derive_host_profile("gemini_cli")
        assert profile["capabilityClass"] == "instruction_first"
        assert profile["permissionGate"] == "none"
        assert profile["inlineBoundaryGate"] == "none"
        assert profile["protectionModel"] == "review_driven"
        assert profile["contextFile"] == "GEMINI.md"

    def test_cursor_and_windsurf_profiles_have_context_exports(self):
        assert cc._derive_host_profile("cursor")["contextFile"] == ".cursor/rules/project.mdc"
        assert cc._derive_host_profile("windsurf")["contextFile"] == ".windsurfrules"

    def test_vscode_profile_is_manual_without_context_export(self):
        profile = cc._derive_host_profile("vscode")
        assert profile["contextFile"] is None
        assert profile["protectionModel"] == "review_driven"

    def test_codex_gate_contract_marks_repo_boundary_as_primary(self):
        contract = cc._derive_host_gate_contract(cc._derive_host_profile("codex_cli"))
        by_id = {entry["id"]: entry for entry in contract}
        assert by_id["inline_gate"]["nature"] == "unavailable"
        assert by_id["repo_boundary_gate"]["primary"] is True
        assert by_id["repo_boundary_gate"]["nature"] == "mechanical"
        assert by_id["review_gate"]["nature"] == "conditional"


# ----------------------------------------------------- TestLoadSaveSettings ---


class TestLoadSaveSettings:
    def test_load_missing_returns_empty(self, tmp_path):
        assert cc.load_settings(tmp_path) == {}

    def test_load_valid(self, tmp_path):
        settings = {"hooks": {"PreToolUse": []}}
        (tmp_path / ".controlcoding").mkdir()
        (tmp_path / ".controlcoding" / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )
        result = cc.load_settings(tmp_path)
        assert result == settings

    def test_save_creates_dir(self, tmp_path):
        cc.save_settings(tmp_path, {"foo": "bar"})
        assert (tmp_path / ".controlcoding" / "settings.json").exists()
        loaded = json.loads(
            (tmp_path / ".controlcoding" / "settings.json").read_text(encoding="utf-8")
        )
        assert loaded == {"foo": "bar"}

    def test_roundtrip(self, tmp_path):
        data = {"hooks": {"PreToolUse": [{"matcher": "Edit"}]}, "mcpServers": {}}
        cc.save_settings(tmp_path, data)
        loaded = cc.load_settings(tmp_path)
        assert loaded == data

    def test_write_failure_preserves_existing_bytes_and_cleans_temp(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".controlcoding" / "settings.json"
        settings_path.parent.mkdir()
        original = b'{"old": true}\n'
        settings_path.write_bytes(original)
        monkeypatch.setattr(cc.json, "dump", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")))
        with pytest.raises(OSError, match="write failed"):
            cc.save_settings(tmp_path, {"new": True})
        assert settings_path.read_bytes() == original
        assert list(settings_path.parent.glob(".settings.json.*.tmp")) == []

    def test_replace_failure_preserves_existing_bytes_and_cleans_temp(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".controlcoding" / "settings.json"
        settings_path.parent.mkdir()
        original = b'{"old": true}\n'
        settings_path.write_bytes(original)
        monkeypatch.setattr(cc.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace failed")))
        with pytest.raises(OSError, match="replace failed"):
            cc.save_settings(tmp_path, {"new": True})
        assert settings_path.read_bytes() == original
        assert list(settings_path.parent.glob(".settings.json.*.tmp")) == []


class TestTransactionalPackInstall:
    @pytest.mark.parametrize("pack", [
        "debug-tools",
        "multi-agent",
        "session-manager",
        "dashboard",
        "vision",
        "visual-check",
    ])
    def test_every_pack_installs_without_provider_configuration(
            self, tmp_path, monkeypatch, pack):
        project = tmp_path / pack
        project.mkdir()
        monkeypatch.setattr(cc.shutil, "which", lambda name: None)
        for name in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CONSULT_BACKEND",
            "CONCIERGE_BACKEND", "CODEWARDEN_BACKEND",
        ):
            monkeypatch.delenv(name, raising=False)

        assert cc.cmd_install(project, pack) == 0
        settings = cc.load_settings(project)
        serialized = json.dumps(settings)
        assert "CONSULT_BACKEND" not in serialized
        assert "CODEWARDEN_BACKEND" not in serialized

    def test_session_manager_orders_helper_before_consumers(self):
        assert [path.name for path in cc.PACK_FILES["session-manager"]["tools/"]] == [
            "cc_lockfile.py",
            "mcp_session.py",
            "mcp_agent_memory.py",
            "mcp_handoff.py",
        ]

    def test_preflight_failure_copies_nothing_and_preserves_settings(self, tmp_path, monkeypatch):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        settings_path = control_dir / "settings.json"
        original = b'{"existing": true}\n'
        settings_path.write_bytes(original)
        missing = tmp_path / "missing.py"
        monkeypatch.setitem(cc.PACK_FILES, "session-manager", {"tools/": [missing]})
        assert cc.cmd_install(tmp_path, "session-manager") == 1
        assert not (tmp_path / "tools").exists()
        assert settings_path.read_bytes() == original

    def test_directory_source_fails_preflight(self, tmp_path, monkeypatch):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        monkeypatch.setitem(cc.PACK_FILES, "session-manager", {"tools/": [source_dir]})
        assert cc.cmd_install(tmp_path, "session-manager") == 1
        assert not (tmp_path / "tools").exists()

    def test_atomic_copy_replaces_managed_files_and_imports_fresh(self, tmp_path):
        assert cc.cmd_install(tmp_path, "session-manager") == 0
        tools_dir = tmp_path / "tools"
        assert (tools_dir / "cc_lockfile.py").is_file()
        assert (tools_dir / "mcp_handoff.py").is_file()
        script = (
            "import os,sys; "
            f"sys.path.insert(0, {str(tools_dir)!r}); "
            f"os.environ['SESSION_PROJECT_ROOT']={str(tmp_path)!r}; "
            "import cc_lockfile,mcp_handoff; "
            f"assert cc_lockfile.__file__.startswith({str(tools_dir)!r}); "
            f"assert mcp_handoff.__file__.startswith({str(tools_dir)!r})"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_debug_tools_install_is_backend_neutral_without_ai_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", lambda name: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        assert cc.SUPPORTED_CONSULTATION_BACKENDS == frozenset({
            "claude", "ollama", "openai", "anthropic",
        })
        assert cc.cmd_install(tmp_path, "debug-tools") == 0

        settings = cc.load_settings(tmp_path)
        server = settings["mcpServers"]["debug-consultant"]
        assert server == {
            "command": "python",
            "args": ["tools/mcp_consultant.py"],
        }
        assert (tmp_path / "tools" / "mcp_consultant.py").is_file()
        assert (tmp_path / "tools" / "consult.py").is_file()

    def test_debug_tools_reinstall_preserves_existing_backend_configuration(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        existing_server = {
            "command": "custom-python",
            "args": ["custom_consultant.py"],
            "env": {
                "CONSULT_BACKEND": "openai",
                "CONSULT_FALLBACK_BACKEND": "anthropic",
            },
        }
        (control_dir / "settings.json").write_text(
            json.dumps({"mcpServers": {"debug-consultant": existing_server}}),
            encoding="utf-8",
        )

        assert cc.cmd_install(tmp_path, "debug-tools") == 0
        assert cc.load_settings(tmp_path)["mcpServers"]["debug-consultant"] == existing_server


# ---------------------------------------------------------- TestMergeHooks ---


class TestMergeHooks:
    def test_merge_new_event(self):
        existing = {"PreToolUse": []}
        new = {"Stop": [{"hooks": [{"type": "command", "command": "python hooks/stop.py"}]}]}
        result = cc.merge_hooks(existing, new)
        assert "Stop" in result
        assert len(result["Stop"]) == 1

    def test_dedup_same_hook(self):
        entry = {
            "matcher": "Edit|Write",
            "hooks": [{"type": "command", "command": "python hooks/check_boundaries.py"}],
        }
        existing = {"PreToolUse": [entry]}
        new = {"PreToolUse": [entry.copy()]}
        result = cc.merge_hooks(existing, new)
        assert len(result["PreToolUse"]) == 1

    def test_different_matchers_both_kept(self):
        e1 = {"matcher": "Edit", "hooks": [{"type": "command", "command": "python hooks/a.py"}]}
        e2 = {"matcher": "Write", "hooks": [{"type": "command", "command": "python hooks/a.py"}]}
        existing = {"PreToolUse": [e1]}
        new = {"PreToolUse": [e2]}
        result = cc.merge_hooks(existing, new)
        assert len(result["PreToolUse"]) == 2

    def test_merge_empty_existing(self):
        new = {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "x"}]}]}
        result = cc.merge_hooks({}, new)
        assert len(result["PreToolUse"]) == 1


# ------------------------------------------------------ TestMergeMcpServers ---


class TestMergeMcpServers:
    def test_adds_new_server(self):
        existing = {}
        new = {"consultant": {"command": "python", "args": ["tools/mcp_consultant.py"]}}
        result = cc.merge_mcp_servers(existing, new)
        assert "consultant" in result

    def test_skips_existing(self):
        existing = {"consultant": {"command": "old"}}
        new = {"consultant": {"command": "new"}}
        result = cc.merge_mcp_servers(existing, new)
        assert result["consultant"]["command"] == "old"

    def test_empty_existing(self):
        new = {"a": {"cmd": "1"}, "b": {"cmd": "2"}}
        result = cc.merge_mcp_servers({}, new)
        assert len(result) == 2


# ------------------------------------------------ TestResolveHookCommands ---


class TestResolveHookCommands:
    def test_resolves_relative_to_absolute(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"command": "python hooks/check_boundaries.py"}]}
                ]
            }
        }
        result = cc._resolve_hook_commands(settings, hooks_dir)
        cmd = result["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert str(hooks_dir.resolve()).replace("\\", "/") in cmd
        assert "check_boundaries.py" in cmd

    def test_ignores_already_absolute(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"command": "python /abs/path/check.py"}]}
                ]
            }
        }
        result = cc._resolve_hook_commands(settings, hooks_dir)
        cmd = result["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert cmd == "python /abs/path/check.py"

    def test_handles_nested_hooks(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"command": "python hooks/a.py"}]},
                    {"hooks": [{"command": "python hooks/b.py"}]},
                ],
                "Stop": [
                    {"hooks": [{"command": "python hooks/c.py"}]},
                ],
            }
        }
        result = cc._resolve_hook_commands(settings, hooks_dir)
        for event_entries in result["hooks"].values():
            for entry in event_entries:
                for hook in entry["hooks"]:
                    assert "hooks/" not in hook["command"] or str(hooks_dir.resolve()).replace("\\", "/") in hook["command"]


# ------------------------------------------------------------ TestCmdInit ---


class TestCmdInit:
    def test_creates_canonical_context_only(self, tmp_path):
        cc.cmd_init(tmp_path)
        assert (tmp_path / "CONTROLCODING.md").exists()
        assert (tmp_path / "ROADMAP.md").exists()
        assert (tmp_path / "BUGS.md").exists()

    def test_creates_hooks_dir(self, tmp_path):
        cc.cmd_init(tmp_path)
        hooks = tmp_path / "hooks"
        assert hooks.is_dir()
        assert (hooks / "check_boundaries.py").exists()
        assert (hooks / "check_dangerous_commands.py").exists()
        assert (hooks / "check_repo_boundaries.py").exists()

    def test_creates_settings_json(self, tmp_path):
        cc.cmd_init(tmp_path)
        settings_path = tmp_path / ".controlcoding" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "hooks" in settings

    def test_creates_cc_config(self, tmp_path):
        cc.cmd_init(tmp_path)
        cc_config = tmp_path / ".controlcoding" / "cc_config.json"
        assert cc_config.exists()

    def test_init_installs_repo_side_git_hooks_when_git_present(self, tmp_path):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        cc.cmd_init(tmp_path)
        precommit = (tmp_path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        postcommit = (tmp_path / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
        assert "check_repo_boundaries.py" in precommit
        assert "fitness_check.py" in precommit
        assert '"$PYTHON_BIN" "$REPO_BOUNDARY" || exit 2' in precommit
        assert "exit 1" not in precommit
        assert "codewarden_review.py" in postcommit

    def test_idempotent_skips_existing(self, tmp_path):
        cc.cmd_init(tmp_path)
        # Write custom content to CONTROLCODING.md
        (tmp_path / "CONTROLCODING.md").write_text("custom content", encoding="utf-8")
        cc.cmd_init(tmp_path)
        # Should NOT overwrite
        assert (tmp_path / "CONTROLCODING.md").read_text(encoding="utf-8") == "custom content"


# ---------------------------------------------------------- TestCmdDoctor ---


class TestCmdDoctor:
    def test_invalid_project_root_json_stdout_is_json(self, tmp_path):
        missing_dir = tmp_path / "missing-project"

        result = _run_cc(
            "doctor",
            "--project-root", missing_dir,
            "--json",
        )

        assert result.returncode == 1
        payload = _assert_pure_json_object(result.stdout)
        assert "Error:" not in result.stdout
        assert payload["ok"] is False
        assert payload["error"] == "invalid_project_root"
        assert payload["message"]
        assert payload["projectRoot"] == str(missing_dir.resolve())
        assert "Traceback" not in result.stderr

    def test_invalid_project_root_non_json_remains_human(self, tmp_path):
        missing_dir = tmp_path / "missing-project"

        result = _run_cc("doctor", "--project-root", missing_dir)

        assert result.returncode == 1
        assert result.stdout.startswith("Error: ")
        assert f"{missing_dir.resolve()} is not a directory" in result.stdout
        assert "Traceback" not in result.stderr

    def test_healthy_project(self, tmp_path):
        _make_healthy_project(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            result = cc.cmd_doctor(tmp_path)
        assert result == 0

    def test_doctor_json_reports_operational_contract_for_basic_install(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        contract = payload["operationalContract"]
        assert contract["mode"] == "installed_project"
        assert contract["installedProjectReady"] is True
        assert contract["safeForHumanWork"] is True
        assert contract["safeForAiAssistedWork"] is False
        assert contract["safeForAutonomousWork"] is False
        assert "no primary host profile is configured" in contract["aiAssistedGaps"]
        assert contract["gates"]["verificationContract"]["status"] == "info"

    def test_doctor_json_marks_repo_side_project_ready_when_gates_and_contracts_are_wired(self, tmp_path, capsys, monkeypatch):
        _make_healthy_project(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
        _write_gateway_config(tmp_path, user_host="codex_cli")
        _install_repo_side_gate_baseline(tmp_path)
        _write_minimal_verification_contract(tmp_path)
        _write_minimal_invariant_manifest(tmp_path)
        monkeypatch.setattr(cc.shutil, "which", lambda command: "git" if command == "git" else None)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        contract = payload["operationalContract"]
        assert contract["safeForHumanWork"] is True
        assert contract["safeForAiAssistedWork"] is True
        assert contract["safeForAutonomousWork"] is False
        assert contract["hostControlLevel"]["boundaryGate"] == "repoBoundaryGate"
        assert contract["gates"]["repoBoundaryGate"]["controlLevel"] == "mechanical"
        assert contract["gates"]["reviewGate"]["status"] == "ok"
        assert contract["gates"]["verificationGate"]["status"] == "ok"
        assert "reviewGate is not mechanical or explicitly bounded" in contract["autonomousGaps"]

    def test_missing_claude_md(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / "CLAUDE.md").unlink()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_missing_hooks(self, tmp_path):
        _make_healthy_project(tmp_path)
        import shutil
        shutil.rmtree(tmp_path / "hooks")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_invalid_settings_json(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "settings.json").write_text(
            "NOT JSON", encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_git_check_fails_gracefully(self, tmp_path):
        _make_healthy_project(tmp_path)
        with patch("subprocess.run", side_effect=Exception("git not found")):
            # Should not raise
            result = cc.cmd_doctor(tmp_path)
        # May or may not have issues, but should not crash
        assert result in (0, 1)

    def test_gateway_config_rejects_plaintext_api_key(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "uiMode": "api_studio",
            "backends": {
                "anthropic": {
                    "id": "anthropic",
                    "type": "anthropic",
                    "class": "official_api",
                    "apiKey": "sk-test",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "anthropic"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_consumer_session_fields(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                    "sessionToken": "secret-session-token",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_api_studio_with_official_cli_backend(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "uiMode": "api_studio",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_invalid_user_host(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "userHost": "embedded_oauth_magic",
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_invalid_enabled_hosts(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["codex_cli", "telepathy_shell"],
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_enabled_hosts_without_primary(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["gemini_cli"],
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_invalid_host_instruction_mode(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "userHost": "vscode",
            "hostInstructions": {
                "mode": "break_the_rules",
                "customNotes": [],
            },
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_gateway_config_rejects_mismatched_host_profile(self, tmp_path):
        _make_healthy_project(tmp_path)
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["codex_cli"],
            "hostProfile": cc._derive_host_profile("claude_code"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_doctor_accepts_codex_repo_side_profile_with_precommit(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
        _install_repo_side_gate_baseline(tmp_path)
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["codex_cli"],
            "hostProfile": cc._derive_host_profile("codex_cli"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 0

    def test_doctor_json_reports_context_sync_for_canonical_source(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        legacy_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        (tmp_path / "CONTROLCODING.md").write_text(
            legacy_text.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        _install_repo_side_gate_baseline(tmp_path)
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["codex_cli"],
            "hostProfile": cc._derive_host_profile("codex_cli"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        capsys.readouterr()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["contextSync"]["sourceState"] == "canonical"
        assert payload["contextSync"]["hosts"][0]["state"] == "current"
        assert payload["contextSync"]["hosts"][0]["ownershipState"] == "owned_current"
        assert checks["context_sync"]["status"] == "ok"

    def test_doctor_json_reports_constitution_drift_for_canonical_source(self, tmp_path, capsys, monkeypatch):
        _make_healthy_project(tmp_path)
        legacy_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        (tmp_path / "CONTROLCODING.md").write_text(
            legacy_text.replace("CLAUDE.md", "CONTROLCODING.md")
            + "\n## Executable Invariant References\n\n- `test-invariant`\n",
            encoding="utf-8",
        )
        _write_gateway_config(tmp_path, user_host="codex_cli")
        _install_repo_side_gate_baseline(tmp_path)
        _write_minimal_verification_contract(tmp_path)
        _write_minimal_invariant_manifest(tmp_path)
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        capsys.readouterr()
        monkeypatch.setattr(cc.shutil, "which", lambda command: "git" if command == "git" else None)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["constitutionDrift"]["ok"] is True
        assert checks["constitution_drift"]["status"] == "ok"
        assert payload["operationalContract"]["gates"]["constitutionDrift"]["status"] == "ok"

    def test_doctor_rejects_codex_profile_without_real_repo_boundary_gate(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
        git_hooks = tmp_path / ".git" / "hooks"
        git_hooks.mkdir(parents=True)
        (git_hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["codex_cli"],
            "hostProfile": cc._derive_host_profile("codex_cli"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".claude" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_doctor_json_reports_codex_repo_side_gate_contract_and_failures(self, tmp_path, capsys, monkeypatch):
        _make_healthy_project(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
        git_hooks = tmp_path / ".git" / "hooks"
        git_hooks.mkdir(parents=True)
        (git_hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gateway = {
            "userHost": "codex_cli",
            "enabledHosts": ["codex_cli"],
            "hostProfile": cc._derive_host_profile("codex_cli"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        monkeypatch.setattr(cc.shutil, "which", lambda command: "git" if command == "git" else None)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        contract = {entry["id"]: entry for entry in payload["gateContract"]}
        assert payload["primaryHost"] == "codex_cli"
        assert payload["enabledHosts"] == ["codex_cli"]
        assert payload["hostProfile"]["capabilityClass"] == "sandbox_approval"
        assert contract["inline_gate"]["nature"] == "unavailable"
        assert contract["repo_boundary_gate"]["primary"] is True
        assert checks["inline_gate"]["status"] == "info"
        assert checks["repo_boundary_gate"]["status"] == "fail"
        assert checks["verification_gate"]["status"] == "fail"

    def test_doctor_json_smoke_reports_class_a_inline_host_contract(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        gateway = {
            "userHost": "claude_code",
            "enabledHosts": ["claude_code"],
            "hostProfile": cc._derive_host_profile("claude_code"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        contract = {entry["id"]: entry for entry in payload["gateContract"]}
        assert payload["primaryHost"] == "claude_code"
        assert payload["enabledHosts"] == ["claude_code"]
        assert payload["hostProfile"]["capabilityClass"] == "native_inline_hooks"
        assert payload["hostProfile"]["protectionModel"] == "inline_first"
        assert payload["hostProfile"]["contextFile"] == "CLAUDE.md"
        assert contract["inline_gate"]["primary"] is True
        assert contract["inline_gate"]["nature"] == "mechanical"
        assert contract["repo_boundary_gate"]["requirement"] == "backstop"
        assert checks["inline_gate"]["status"] == "ok"

    def test_doctor_json_smoke_reports_class_c_repo_side_host_contract(self, tmp_path, capsys, monkeypatch):
        _make_healthy_project(tmp_path)
        (tmp_path / "GEMINI.md").write_text("# GEMINI.md\n", encoding="utf-8")
        _install_repo_side_gate_baseline(tmp_path)
        gateway = {
            "userHost": "gemini_cli",
            "enabledHosts": ["gemini_cli"],
            "hostProfile": cc._derive_host_profile("gemini_cli"),
            "uiMode": "visualizer",
            "backends": {
                "claude_cli": {
                    "id": "claude_cli",
                    "type": "claude_cli",
                }
            },
            "agents": {
                "mainCoder": {"active": True, "backend": "claude_cli"},
                "codewarden": {"active": False, "backend": "", "model": "", "mode": "off"},
                "consultants": {"maxSlots": 3, "defaults": []},
                "narrator": {"active": False, "backend": "", "model": "", "language": "en", "updateIntervalSeconds": 30},
            },
        }
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(gateway, indent=2), encoding="utf-8"
        )
        monkeypatch.setattr(cc.shutil, "which", lambda command: "git" if command == "git" else None)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        contract = {entry["id"]: entry for entry in payload["gateContract"]}
        assert payload["primaryHost"] == "gemini_cli"
        assert payload["enabledHosts"] == ["gemini_cli"]
        assert payload["hostProfile"]["capabilityClass"] == "instruction_first"
        assert payload["hostProfile"]["protectionModel"] == "review_driven"
        assert payload["hostProfile"]["contextFile"] == "GEMINI.md"
        assert contract["inline_gate"]["nature"] == "unavailable"
        assert contract["repo_boundary_gate"]["primary"] is True
        assert contract["repo_boundary_gate"]["requirement"] == "required"
        assert checks["inline_gate"]["status"] == "info"
        assert checks["repo_boundary_gate"]["status"] == "ok"
        assert checks["verification_gate"]["status"] == "ok"

    def test_doctor_flags_disabled_local_tandem_capacity(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        (tmp_path / ".claude" / "cc_engagement.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "tier": "agents",
                    "level": 4,
                    "ui_intent": "host_assist",
                    "backend_policy": "local_only",
                    "tandem": {
                        "mode": "auto",
                        "backend_a": "ollama",
                        "backend_b": "ollama",
                        "model_a": "qwen-a",
                        "model_b": "qwen-b",
                    },
                    "budget_policy": {"max_calls": 0, "exhaustion_behavior": "degrade"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run, patch.object(
            cc,
            "_assess_local_tandem_capacity",
            return_value={
                "status": "disabled",
                "preferred_mode": "single_model_consult",
                "total_ram_gb": 16.0,
                "gpu_vram_gb": 8.0,
                "summary": "Prefer single-model local consult; do not rely on tandem.",
                "reasons": [],
            },
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        output = capsys.readouterr().out
        assert result == 1
        assert "Local tandem capacity" in output
        assert "single_model_consult" in output

    def test_doctor_does_not_flag_local_tandem_without_explicit_intent(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                    "hostProfile": cc._derive_host_profile("codex_cli"),
                    "uiMode": "api_studio",
                    "backends": {
                        "ollama_local": {
                            "type": "ollama",
                            "class": "local_runtime",
                            "endpoint": "http://127.0.0.1:11434",
                        }
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (tmp_path / ".controlcoding" / "cc_engagement.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "tier": "agents",
                    "level": 4,
                    "backend_policy": "approved",
                    "planning_mode": "orchestrated_specialists",
                    "planning_authority": "orchestrated_multi_role",
                    "specialist_paths": [
                        {
                            "role_id": "codewarden",
                            "label": "CodeWarden",
                            "path_type": "review",
                            "active": True,
                            "backend": "ollama_local",
                            "model": "qwen",
                            "execution_mode": "cc_routed",
                            "permission": "approval_required",
                            "max_calls": 1,
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert cc.cmd_doctor(tmp_path, json_output=True) == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["local_tandem"]["detail"] == "tandem inactive in current tier/config"

    def test_cc_config_rejects_invalid_documentation_mode(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "illegal_mode",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_cc_config_accepts_project_managed_mode(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "project_managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 0

    def test_cc_config_accepts_legacy_protected_zone_map(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": {
                        "deny": ["src/core/"],
                        "warn": ["src/shared/"],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 0

    def test_cc_config_rejects_invalid_cc_artifact_mode(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "bad_mode",
                    "hooks_location": "local",
                    "protected_zones": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_shared_repo_still_rejects_tracked_devlog(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "project_managed",
                    "cc_artifact_mode": "shared_repo",
                    "hooks_location": "local",
                    "protected_zones": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        def _run_side_effect(cmd, **kwargs):
            if cmd[:2] == ["git", "ls-files"] and "devlog/" in cmd:
                return MagicMock(returncode=0, stdout="devlog/index.md\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_run_side_effect):
            result = cc.cmd_doctor(tmp_path)
        assert result == 1


class TestControlledWritePath:
    def test_host_status_json_includes_controlled_write_path(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        assert cc.cmd_host_status(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "controlledWritePath" in payload
        assert payload["controlledWritePath"]["mode"] == "off"

    def test_host_status_json_includes_specialist_consent_matrix(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (tmp_path / ".controlcoding" / "cc_engagement.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "tier": "agents",
                    "level": 4,
                    "backend_policy": "approved",
                    "planning_mode": "orchestrated_specialists",
                    "planning_authority": "orchestrated_multi_role",
                    "specialist_paths": [
                        {
                            "role_id": "codewarden",
                            "label": "CodeWarden",
                            "path_type": "watchdog",
                            "active": True,
                            "backend": "anthropic_prod",
                            "model": "claude-sonnet-4-6",
                            "permission": "approval_required",
                            "max_calls": 2,
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        assert cc.cmd_host_status(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["specialistConsentMatrix"][0]["backend"] == "anthropic_prod"
        assert payload["specialistConsentMatrix"][0]["execution_mode"] == "cc_routed"
        assert payload["specialistExecutionSummary"]["cc_routed"] == 1

    def test_doctor_fails_agents_without_specialist_matrix(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (tmp_path / ".controlcoding" / "cc_engagement.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "tier": "agents",
                    "level": 4,
                    "backend_policy": "approved",
                    "planning_mode": "orchestrated_specialists",
                    "planning_authority": "orchestrated_multi_role",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_write_path_enable_persists_config(self, tmp_path):
        _make_healthy_project(tmp_path)

        result = cc.cmd_write_path_enable(tmp_path)

        assert result == 0
        config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
        assert config["controlled_write_path"]["enabled"] is True
        assert config["controlled_write_path"]["mode"] == "patch_gateway"

    def test_write_path_enable_can_require_manifest(self, tmp_path):
        _make_healthy_project(tmp_path)

        result = cc.cmd_write_path_enable(tmp_path, require_manifest_for_apply=True)

        assert result == 0
        config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
        assert config["controlled_write_path"]["require_manifest_for_apply"] is True

    def test_write_path_enable_can_enable_shadow_preflight(self, tmp_path):
        _make_healthy_project(tmp_path)

        result = cc.cmd_write_path_enable(tmp_path, preflight_fitness_for_apply=True)

        assert result == 0
        config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
        assert config["controlled_write_path"]["preflight_fitness_for_apply"] is True

    def test_write_path_check_requires_opt_in(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        patch_file = tmp_path / "change.diff"
        patch_file.write_text("", encoding="utf-8")

        result = cc.cmd_write_path_check(tmp_path, patch_file)

        assert result == 1

    def test_write_path_check_blocks_deny_zone_before_apply(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src" / "core").mkdir(parents=True)
        target = tmp_path / "src" / "core" / "engine.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [{"path": "src/core/", "level": "deny", "description": "Core"}],
                    "controlled_write_path": {"enabled": True, "mode": "patch_gateway"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/core/engine.py b/src/core/engine.py",
                    "--- a/src/core/engine.py",
                    "+++ b/src/core/engine.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = cc.cmd_write_path_check(tmp_path, patch_file, json_output=False)

        assert result == 1
        assert target.read_text(encoding="utf-8") == "value = 1\n"
        metrics = json.loads((tmp_path / ".controlcoding" / cc.WRITE_PATH_METRICS_FILENAME).read_text(encoding="utf-8"))
        assert metrics["blocked"] == 1
        assert metrics["last_status"] == "blocked"

    def test_write_path_apply_warn_zone_updates_file_and_metrics(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src" / "shared").mkdir(parents=True)
        target = tmp_path / "src" / "shared" / "util.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [{"path": "src/shared/", "level": "warn", "description": "Shared"}],
                    "controlled_write_path": {"enabled": True, "mode": "patch_gateway"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/shared/util.py b/src/shared/util.py",
                    "--- a/src/shared/util.py",
                    "+++ b/src/shared/util.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 3",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = cc.cmd_write_path_apply(tmp_path, patch_file, reason="test patch")

        assert result == 0
        assert target.read_text(encoding="utf-8") == "value = 3\n"
        metrics = json.loads((tmp_path / ".controlcoding" / cc.WRITE_PATH_METRICS_FILENAME).read_text(encoding="utf-8"))
        assert metrics["applied"] == 1
        assert metrics["warned"] == 1
        events = (tmp_path / ".controlcoding" / cc.WRITE_PATH_EVENTS_FILENAME).read_text(encoding="utf-8").splitlines()
        assert events
        last_event = json.loads(events[-1])
        assert last_event["reason"] == "test patch"

    def test_write_path_status_json_includes_latest_receipt(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {"enabled": True, "mode": "patch_gateway"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_check(tmp_path, patch_file) == 0
        capsys.readouterr()

        assert cc.cmd_write_path_status(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["receiptCount"] == 1
        assert payload["latestReceipt"]["operation"] == "check"
        assert payload["latestReceipt"]["status"] == "validated"

    def test_write_path_receipts_lists_saved_receipts(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {"enabled": True, "mode": "patch_gateway"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_check(tmp_path, patch_file) == 0
        capsys.readouterr()

        assert cc.cmd_write_path_receipts(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["receiptCount"] == 1
        assert payload["receipts"][0]["status"] == "validated"

    def test_write_path_receipt_show_returns_full_receipt(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {"enabled": True, "mode": "patch_gateway"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 3",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_check(tmp_path, patch_file) == 0
        capsys.readouterr()
        receipts = list((tmp_path / ".controlcoding" / cc.WRITE_PATH_RECEIPT_DIRNAME).glob("*.json"))
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))

        assert cc.cmd_write_path_receipt_show(tmp_path, receipt["receipt_id"], json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["receipt"]["status"] == "validated"
        assert payload["receipt"]["operation"] == "check"

    def test_write_path_prepare_creates_manifest(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {
                        "enabled": True,
                        "mode": "patch_gateway",
                        "require_manifest_for_apply": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_prepare(tmp_path, patch_file) == 0
        manifests = list((tmp_path / ".controlcoding" / cc.WRITE_PATH_MANIFEST_DIRNAME).glob("*.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["validation_ok"] is True
        assert manifest["touched_files"] == ["src/module.py"]
        assert manifest["baseline_snapshot"][0]["path"] == "src/module.py"
        assert manifest["baseline_snapshot"][0]["state"] == "present"

    def test_write_path_apply_requires_manifest_when_enabled(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {
                        "enabled": True,
                        "mode": "patch_gateway",
                        "require_manifest_for_apply": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_apply(tmp_path, patch_file) == 1
        metrics = json.loads((tmp_path / ".controlcoding" / cc.WRITE_PATH_METRICS_FILENAME).read_text(encoding="utf-8"))
        assert metrics["manifest_blocked"] == 1
        assert target.read_text(encoding="utf-8") == "value = 1\n"

    def test_write_path_apply_with_manifest_marks_it_used(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {
                        "enabled": True,
                        "mode": "patch_gateway",
                        "require_manifest_for_apply": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 4",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_prepare(tmp_path, patch_file) == 0
        manifest_path = next((tmp_path / ".controlcoding" / cc.WRITE_PATH_MANIFEST_DIRNAME).glob("*.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert cc.cmd_write_path_apply(tmp_path, patch_file, manifest_id=manifest["manifest_id"]) == 0
        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert updated_manifest["used"] is True
        assert target.read_text(encoding="utf-8") == "value = 4\n"

    def test_write_path_apply_blocks_when_target_drifted_since_prepare(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {
                        "enabled": True,
                        "mode": "patch_gateway",
                        "require_manifest_for_apply": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 5",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_prepare(tmp_path, patch_file) == 0
        manifest_path = next((tmp_path / ".controlcoding" / cc.WRITE_PATH_MANIFEST_DIRNAME).glob("*.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        target.write_text("value = 99\n", encoding="utf-8")

        assert cc.cmd_write_path_apply(tmp_path, patch_file, manifest_id=manifest["manifest_id"]) == 1
        metrics = json.loads((tmp_path / ".controlcoding" / cc.WRITE_PATH_METRICS_FILENAME).read_text(encoding="utf-8"))
        assert metrics["manifest_blocked"] == 1
        assert target.read_text(encoding="utf-8") == "value = 99\n"

    def test_write_path_apply_blocks_when_shadow_preflight_fails(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "src" / "module.py"
        target.write_text("value = 1\n", encoding="utf-8")
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {
                        "enabled": True,
                        "mode": "patch_gateway",
                        "require_manifest_for_apply": True,
                        "preflight_fitness_for_apply": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_file = tmp_path / "change.diff"
        patch_file.write_text(
            "\n".join(
                [
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 7",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        assert cc.cmd_write_path_prepare(tmp_path, patch_file) == 0
        manifest_path = next((tmp_path / ".controlcoding" / cc.WRITE_PATH_MANIFEST_DIRNAME).glob("*.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        with patch.object(
            cc,
            "_run_shadow_fitness_preflight",
            return_value={
                "ok": False,
                "blocked": True,
                "detail": "Layer rule violation in shadow preflight.",
                "shadowRoot": str(tmp_path / ".controlcoding" / "write_path_shadow" / "test"),
                "warning": "",
            },
        ) as mock_preflight:
            assert cc.cmd_write_path_apply(tmp_path, patch_file, manifest_id=manifest["manifest_id"]) == 1

        mock_preflight.assert_called_once()
        metrics = json.loads((tmp_path / ".controlcoding" / cc.WRITE_PATH_METRICS_FILENAME).read_text(encoding="utf-8"))
        assert metrics["preflight_runs"] == 1
        assert metrics["preflight_blocked"] == 1
        assert metrics["last_operation"] == "preflight"
        assert metrics["last_status"] == "preflight_blocked"
        assert target.read_text(encoding="utf-8") == "value = 1\n"

    def test_invalid_controlled_write_path_mode_fails_doctor(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {"enabled": True, "mode": "shadow_workspace"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_doctor_fails_when_shadow_preflight_prereqs_missing(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / ".controlcoding" / "cc_config.json").write_text(
            json.dumps(
                {
                    "documentation_mode": "managed",
                    "cc_artifact_mode": "local_only",
                    "hooks_location": "local",
                    "protected_zones": [],
                    "controlled_write_path": {
                        "enabled": True,
                        "mode": "patch_gateway",
                        "preflight_fitness_for_apply": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        with patch.object(cc, "_check_write_path_preflight_prereqs", return_value="fitness preflight unavailable"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)
        assert result == 1

    def test_main_routes_write_path_enable(self, tmp_path):
        argv = [
            "cc.py",
            "write-path",
            "enable",
            "--project-root", str(tmp_path),
            "--mode", "patch_gateway",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_write_path_enable", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            mode="patch_gateway",
            patch_format="unified_diff",
            require_manifest_for_apply=False,
            preflight_fitness_for_apply=False,
            json_output=False,
        )

    def test_main_routes_write_path_enable_with_preflight(self, tmp_path):
        argv = [
            "cc.py",
            "write-path",
            "enable",
            "--project-root", str(tmp_path),
            "--mode", "patch_gateway",
            "--preflight-fitness",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_write_path_enable", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            mode="patch_gateway",
            patch_format="unified_diff",
            require_manifest_for_apply=False,
            preflight_fitness_for_apply=True,
            json_output=False,
        )

    def test_main_routes_write_path_receipts(self, tmp_path):
        argv = [
            "cc.py",
            "write-path",
            "receipts",
            "--project-root", str(tmp_path),
            "--limit", "5",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_write_path_receipts", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            limit=5,
            json_output=False,
        )

    def test_main_routes_write_path_receipt_show(self, tmp_path):
        argv = [
            "cc.py",
            "write-path",
            "receipt",
            "--project-root", str(tmp_path),
            "--receipt-id", "wpr_check_20260414T000000Z_deadbeef00",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_write_path_receipt_show", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            receipt_id="wpr_check_20260414T000000Z_deadbeef00",
            json_output=False,
        )

    def test_main_routes_write_path_prepare(self, tmp_path):
        argv = [
            "cc.py",
            "write-path",
            "prepare",
            "--project-root", str(tmp_path),
            "--patch-file", "change.diff",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_write_path_prepare", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            patch_file=Path("change.diff"),
            reason="",
            json_output=False,
        )

    def test_benchmark_matrix_generate_writes_markdown(self, tmp_path):
        output = tmp_path / "benchmarks" / "matrix.md"

        assert cc.cmd_benchmark_matrix_generate(tmp_path, output_path=output) == 0
        content = output.read_text(encoding="utf-8")
        assert "# Multi-Host Capability Matrix" in content
        assert "Codex CLI (`codex_cli`)" in content
        assert "curated_partial" in content

    def test_benchmark_matrix_generate_includes_local_snapshot(self, tmp_path):
        _make_healthy_project(tmp_path)
        _init_git_repo(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (tmp_path / ".controlcoding" / "cc_engagement.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "tier": "agents",
                    "level": 4,
                    "backend_policy": "approved",
                    "planning_mode": "orchestrated_specialists",
                    "planning_authority": "orchestrated_multi_role",
                    "specialist_paths": [
                        {
                            "role_id": "architect",
                            "label": "Architect",
                            "path_type": "consultant",
                            "active": True,
                            "backend": "openai_prod",
                            "model": "gpt-5.4-mini",
                            "permission": "user_mediated",
                            "execution_mode": "human_mediated",
                            "max_calls": 1,
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert cc.cmd_write_path_enable(
            tmp_path,
            require_manifest_for_apply=True,
            preflight_fitness_for_apply=True,
        ) == 0

        output = tmp_path / "benchmarks" / "matrix.md"
        assert cc.cmd_benchmark_matrix_generate(tmp_path, output_path=output) == 0
        content = output.read_text(encoding="utf-8")
        assert "## Local Dogfooding Snapshot" in content
        assert "- Primary host: `codex_cli`" in content
        assert "`patch_gateway / required / shadow-preflight`" in content

    def test_benchmark_local_report_writes_markdown(self, tmp_path):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        output = tmp_path / "benchmarks" / "local-evidence.md"

        assert cc.cmd_benchmark_local_report(tmp_path, output_path=output) == 0
        content = output.read_text(encoding="utf-8")
        assert "# Local Dogfooding Evidence" in content
        assert "- Primary host: `codex_cli`" in content

    def test_benchmark_local_report_json_includes_snapshot(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        assert cc.cmd_benchmark_local_report(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["localSnapshot"]["primaryHost"] == "codex_cli"

    def test_main_routes_benchmark_matrix_generate(self, tmp_path):
        argv = [
            "cc.py",
            "benchmark-matrix",
            "generate",
            "--project-root", str(tmp_path),
            "--output", "benchmarks/out.md",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_benchmark_matrix_generate", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            output_path=Path("benchmarks/out.md"),
            json_output=False,
        )

    def test_main_routes_benchmark_matrix_report_local(self, tmp_path):
        argv = [
            "cc.py",
            "benchmark-matrix",
            "report-local",
            "--project-root", str(tmp_path),
            "--output", "benchmarks/local.md",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_benchmark_local_report", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            output_path=Path("benchmarks/local.md"),
            json_output=False,
        )


class TestHumanMediatedAgents:
    def _write_engagement(self, project: Path, specialist_paths: list[dict]) -> None:
        (project / ".controlcoding" / "cc_engagement.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "tier": "agents",
                    "level": 4,
                    "backend_policy": "approved",
                    "planning_mode": "orchestrated_specialists",
                    "planning_authority": "orchestrated_multi_role",
                    "specialist_paths": specialist_paths,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_doctor_accepts_human_mediated_agents_without_gateway(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)

        assert result == 0

    def test_doctor_fails_routed_agents_without_gateway(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "codewarden",
                    "label": "CodeWarden",
                    "path_type": "watchdog",
                    "active": True,
                    "backend": "anthropic_prod",
                    "model": "claude-sonnet-4-6",
                    "permission": "approval_required",
                    "execution_mode": "cc_routed",
                    "max_calls": 2,
                }
            ],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)

        assert result == 1

    def test_doctor_fails_routed_agents_with_unknown_gateway_backend(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "codewarden",
                    "label": "CodeWarden",
                    "path_type": "watchdog",
                    "active": True,
                    "backend": "anthropic_prod",
                    "model": "claude-sonnet-4-6",
                    "permission": "approval_required",
                    "execution_mode": "cc_routed",
                    "max_calls": 2,
                }
            ],
        )
        (tmp_path / ".controlcoding" / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "other",
                    "enabledHosts": ["other"],
                    "hostProfile": cc._derive_host_profile("other"),
                    "backends": {},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path)

        assert result == 1

    def test_consult_packet_create_and_import_manual_result(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )

        result = cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate whether the repo-side boundary split is still coherent.",
            questions=["Should the gateway rule move into a dedicated module?"],
            context_summary="Current enforcement spans CLI and hooks.",
            constraints=["No hidden backend invocation", "Keep .controlcoding canonical"],
            expected_answer_shape="Decision + top risk + next step",
        )

        assert result == 0
        packet_files = list(cc._consult_packets_dir(tmp_path).glob("*.json"))
        assert len(packet_files) == 1
        packet = json.loads(packet_files[0].read_text(encoding="utf-8"))
        assert packet["execution_mode"] == "human_mediated"
        assert packet["semantic_role"] == "architect"
        assert packet["thread_id"]
        assert packet["topic_key"]

        thread_path = cc._consult_thread_state_path(tmp_path, packet["thread_id"])
        assert thread_path.exists()
        thread_payload = json.loads(thread_path.read_text(encoding="utf-8"))
        assert thread_payload["status"] == "drafted"
        assert thread_payload["packet_ids"] == [packet["packet_id"]]
        assert thread_payload["topic_key"] == packet["topic_key"]
        assert cc._consult_thread_memory_path(tmp_path, packet["thread_id"]).exists()
        assert cc._consult_thread_resume_path(tmp_path, packet["thread_id"]).exists()

        result = cc.cmd_consult_result_import(
            tmp_path,
            packet_id=packet["packet_id"],
            summary="Keep the current split, but isolate gateway-specific branching into a dedicated module.",
            decision="partial",
            rationale_summary="The current split is acceptable, but gateway branching is starting to leak cross-layer knowledge.",
            next_action="Extract gateway branching into a dedicated adapter module.",
            constraints=["No hidden backend invocation"],
            evidence=["CLI and hook logic currently share gateway-specific branching"],
            source="chatgpt_manual",
        )

        assert result == 0
        result_files = list(cc._consult_results_dir(tmp_path).glob("*.json"))
        assert len(result_files) == 1
        result_payload = json.loads(result_files[0].read_text(encoding="utf-8"))
        assert result_payload["decision"] == "partial"
        assert result_payload["source_packet_id"] == packet["packet_id"]

        updated_packet = json.loads(packet_files[0].read_text(encoding="utf-8"))
        assert updated_packet["status"] == "imported"

        consult_log = cc._consult_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
        assert consult_log
        log_entry = json.loads(consult_log[-1])
        assert log_entry["role"] == "architect"
        assert log_entry["decision"] == "partial"
        assert log_entry["source"] == "chatgpt_manual"

        updated_thread = json.loads(thread_path.read_text(encoding="utf-8"))
        assert updated_thread["status"] == "imported"
        assert updated_thread["latest_result_id"] == result_payload["result_id"]
        assert updated_thread["decision_history"][-1]["decision"] == "partial"
        assert updated_thread["decision_history"][-1]["thread_id"] == packet["thread_id"]

        thread_decisions = cc._consult_thread_decisions_path(tmp_path, packet["thread_id"]).read_text(encoding="utf-8").splitlines()
        assert thread_decisions
        decision_log = json.loads(thread_decisions[-1])
        assert decision_log["decision"] == "partial"
        assert "dedicated module" in cc._consult_thread_resume_path(tmp_path, packet["thread_id"]).read_text(encoding="utf-8")
        role_memory_path = cc._consult_role_memory_state_path(tmp_path, "architect")
        assert role_memory_path.exists()
        role_memory = json.loads(role_memory_path.read_text(encoding="utf-8"))
        assert role_memory["decisionCounts"]["partial"] == 1
        assert role_memory["unresolvedThreadIds"] == [packet["thread_id"]]
        assert role_memory["mergedTopics"][0]["topic_key"] == packet["topic_key"]
        assert role_memory["mergedTopics"][0]["status"] == "needs_resolution"
        assert cc._consult_convergence_summary_path(tmp_path).exists()
        convergence_summary = json.loads(cc._consult_convergence_summary_path(tmp_path).read_text(encoding="utf-8"))
        assert convergence_summary["pendingThreadCount"] == 1
        assert convergence_summary["roleMemoryCount"] == 1
        assert convergence_summary["topicMergeCount"] == 1
        assert convergence_summary["resolutionTopicCount"] == 0

    def test_consult_packet_show_json_includes_latest_result(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate layering.",
            questions=["Should adapter logic move?"],
        ) == 0
        packet_files = list(cc._consult_packets_dir(tmp_path).glob("*.json"))
        packet = json.loads(packet_files[0].read_text(encoding="utf-8"))
        assert cc.cmd_consult_result_import(
            tmp_path,
            packet_id=packet["packet_id"],
            summary="Yes, isolate adapter logic.",
            decision="accepted",
            rationale_summary="Cross-layer branching is starting to leak.",
            next_action="Extract the adapter.",
        ) == 0

        capsys.readouterr()
        assert cc.cmd_consult_packet_show(tmp_path, packet["packet_id"], json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["packet"]["packet_id"] == packet["packet_id"]
        assert payload["latestResult"]["decision"] == "accepted"
        assert payload["thread"]["thread_id"] == packet["thread_id"]
        assert payload["resumePromptPath"].endswith("resume_prompt.md")

    def test_consult_status_json_reports_packet_and_result_counts(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate layering.",
            questions=["Should adapter logic move?"],
        ) == 0
        packet_files = list(cc._consult_packets_dir(tmp_path).glob("*.json"))
        packet = json.loads(packet_files[0].read_text(encoding="utf-8"))
        assert cc.cmd_consult_result_import(
            tmp_path,
            packet_id=packet["packet_id"],
            summary="Yes, isolate adapter logic.",
            decision="accepted",
            rationale_summary="Cross-layer branching is starting to leak.",
            next_action="Extract the adapter.",
        ) == 0

        capsys.readouterr()
        assert cc.cmd_consult_status(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["executionSummary"]["human_mediated"] == 1
        assert payload["packetCount"] == 1
        assert payload["resultCount"] == 1
        assert payload["threadCount"] == 1
        assert payload["roleMemoryCount"] == 1
        assert payload["decisionsByType"]["accepted"] == 1
        assert payload["threadsByStatus"]["accepted"] == 1
        assert payload["latestThread"]["status"] == "accepted"
        assert payload["latestRoleMemory"]["semantic_role"] == "architect"
        assert payload["convergenceSummary"]["pendingThreadCount"] == 0
        assert payload["convergenceSummary"]["topicMergeCount"] == 1
        assert payload["resolutionTopicCount"] == 0
        assert payload["convergenceSummaryPath"].endswith("convergence_summary.json")

    def test_consult_packet_create_can_continue_existing_thread(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate the initial boundary split.",
            questions=["Should the adapter stay inside CLI?"],
        ) == 0
        first_packet = json.loads(next(cc._consult_packets_dir(tmp_path).glob("*.json")).read_text(encoding="utf-8"))

        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Follow up on the adapter extraction path.",
            questions=["What should move first?"],
            thread_id=first_packet["thread_id"],
        ) == 0

        packets = sorted(cc._consult_packets_dir(tmp_path).glob("*.json"))
        assert len(packets) == 2
        second_packet = json.loads(packets[-1].read_text(encoding="utf-8"))
        assert second_packet["thread_id"] == first_packet["thread_id"]

        thread_payload = json.loads(cc._consult_thread_state_path(tmp_path, first_packet["thread_id"]).read_text(encoding="utf-8"))
        assert len(thread_payload["packet_ids"]) == 2
        assert thread_payload["latest_packet_id"] == second_packet["packet_id"]
        assert thread_payload["topic_key"] == first_packet["topic_key"]
        assert "What should move first?" in cc._consult_thread_resume_path(tmp_path, first_packet["thread_id"]).read_text(encoding="utf-8")

    def test_convergence_summary_flags_role_level_conflicts(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate adapter split option A.",
            questions=["Should option A be accepted?"],
            topic_key="adapter_split",
        ) == 0
        packets = sorted(cc._consult_packets_dir(tmp_path).glob("*.json"))
        first_packet = json.loads(packets[-1].read_text(encoding="utf-8"))
        assert cc.cmd_consult_result_import(
            tmp_path,
            packet_id=first_packet["packet_id"],
            summary="Option A is acceptable.",
            decision="accepted",
            rationale_summary="The split stays coherent.",
            next_action="Document option A.",
        ) == 0

        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate adapter split option B.",
            questions=["Should option B be rejected?"],
            topic_key="adapter_split",
        ) == 0
        packets = sorted(cc._consult_packets_dir(tmp_path).glob("*.json"))
        second_packet = json.loads(packets[-1].read_text(encoding="utf-8"))
        assert cc.cmd_consult_result_import(
            tmp_path,
            packet_id=second_packet["packet_id"],
            summary="Option B should be rejected.",
            decision="rejected",
            rationale_summary="It leaks boundary knowledge.",
            next_action="Drop option B.",
        ) == 0

        role_memory = json.loads(cc._consult_role_memory_state_path(tmp_path, "architect").read_text(encoding="utf-8"))
        assert role_memory["decisionCounts"]["accepted"] == 1
        assert role_memory["decisionCounts"]["rejected"] == 1
        assert role_memory["divergenceSignals"]
        assert any(topic["topic_key"] == "adapter_split" and topic["status"] == "conflicted" for topic in role_memory["mergedTopics"])

        convergence_summary = json.loads(cc._consult_convergence_summary_path(tmp_path).read_text(encoding="utf-8"))
        assert convergence_summary["divergenceSignalCount"] >= 1
        assert any(signal["semantic_role"] == "architect" for signal in convergence_summary["divergenceSignals"])
        assert any(topic["topic_key"] == "adapter_split" and topic["status"] == "conflicted" for topic in convergence_summary["topicMerges"])
        assert convergence_summary["resolutionTopicCount"] == 1
        resolution_topic = convergence_summary["resolutionTopics"][0]
        assert resolution_topic["topic_key"] == "adapter_split"
        assert resolution_topic["resolutionPromptPath"].endswith("resolution_prompt.md")
        resolution_json = tmp_path / resolution_topic["resolutionJsonPath"]
        resolution_prompt = tmp_path / resolution_topic["resolutionPromptPath"]
        assert resolution_json.exists()
        assert resolution_prompt.exists()
        assert "conflicting manual consultation" in resolution_prompt.read_text(encoding="utf-8")

    def test_consult_resolution_show_json_returns_conflicted_topic(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate option A.",
            questions=["Should option A be accepted?"],
            topic_key="adapter_split",
        ) == 0
        first_packet = json.loads(sorted(cc._consult_packets_dir(tmp_path).glob("*.json"))[-1].read_text(encoding="utf-8"))
        assert cc.cmd_consult_result_import(
            tmp_path,
            packet_id=first_packet["packet_id"],
            summary="Option A is acceptable.",
            decision="accepted",
            rationale_summary="It keeps boundaries clean.",
            next_action="Document option A.",
        ) == 0
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate option B.",
            questions=["Should option B be rejected?"],
            topic_key="adapter_split",
        ) == 0
        second_packet = json.loads(sorted(cc._consult_packets_dir(tmp_path).glob("*.json"))[-1].read_text(encoding="utf-8"))
        assert cc.cmd_consult_result_import(
            tmp_path,
            packet_id=second_packet["packet_id"],
            summary="Option B should be rejected.",
            decision="rejected",
            rationale_summary="It leaks boundary knowledge.",
            next_action="Drop option B.",
        ) == 0

        capsys.readouterr()
        assert cc.cmd_consult_resolution_show(tmp_path, role="architect", topic_key="adapter_split", json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["resolutionTopic"]["topic_key"] == "adapter_split"
        assert payload["resolutionPromptPath"].endswith("resolution_prompt.md")

    def test_consult_result_import_rejects_verbose_rationale_dump(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "chatgpt_manual",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        )
        assert cc.cmd_consult_packet_create(
            tmp_path,
            role="architect",
            objective="Validate layering.",
            questions=["Should adapter logic move?"],
        ) == 0
        packet_files = list(cc._consult_packets_dir(tmp_path).glob("*.json"))
        packet = json.loads(packet_files[0].read_text(encoding="utf-8"))

        result = cc.cmd_consult_result_import(
            tmp_path,
            packet_id=packet["packet_id"],
            summary="Yes, isolate adapter logic.",
            decision="accepted",
            rationale_summary="x" * (cc._CONSULT_RATIONALE_LIMIT + 1),
            next_action="Extract the adapter.",
        )

        assert result == 1

    def test_consult_packet_create_rejects_non_human_mediated_path(self, tmp_path):
        _make_healthy_project(tmp_path)
        self._write_engagement(
            tmp_path,
            [
                {
                    "role_id": "codewarden",
                    "label": "CodeWarden",
                    "path_type": "watchdog",
                    "active": True,
                    "backend": "anthropic_prod",
                    "model": "claude-sonnet-4-6",
                    "permission": "approval_required",
                    "execution_mode": "cc_routed",
                    "max_calls": 2,
                }
            ],
        )

        result = cc.cmd_consult_packet_create(
            tmp_path,
            role="codewarden",
            objective="Review the architecture findings.",
            questions=["Is the current finding category split acceptable?"],
        )

        assert result == 1

    def test_main_routes_consult_packet_create(self, tmp_path):
        argv = [
            "cc.py",
            "consult-packet",
            "create",
            "--project-root", str(tmp_path),
            "--role", "architect",
            "--objective", "Validate boundary layering",
            "--question", "Is the split coherent?",
            "--context-summary", "Current split spans CLI and hooks.",
            "--constraint", "No hidden backend invocation",
            "--expected-answer-shape", "Decision + top risk",
            "--topic-key", "boundary_split",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_consult_packet_create", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            role="architect",
            objective="Validate boundary layering",
            questions=["Is the split coherent?"],
            context_summary="Current split spans CLI and hooks.",
            constraints=["No hidden backend invocation"],
            expected_answer_shape="Decision + top risk",
            topic_key="boundary_split",
            thread_id="",
            json_output=False,
        )

    def test_main_routes_consult_result_import(self, tmp_path):
        argv = [
            "cc.py",
            "consult-result",
            "import",
            "--project-root", str(tmp_path),
            "--packet-id", "architect_packet_20260413T000000Z",
            "--summary", "Keep split, isolate adapter logic.",
            "--decision", "accepted",
            "--rationale-summary", "The change reduces leakage across layers.",
            "--evidence", "Gateway branching currently spans CLI and hooks",
            "--next-action", "Extract adapter module.",
            "--source", "manual_chat",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_consult_result_import", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            packet_id="architect_packet_20260413T000000Z",
            summary="Keep split, isolate adapter logic.",
            decision="accepted",
            rationale_summary="The change reduces leakage across layers.",
            next_action="Extract adapter module.",
            constraints=[],
            evidence=["Gateway branching currently spans CLI and hooks"],
            source="manual_chat",
            json_output=False,
        )

    def test_main_routes_consult_packet_show(self, tmp_path):
        argv = [
            "cc.py",
            "consult-packet",
            "show",
            "--project-root", str(tmp_path),
            "--packet-id", "architect_packet_20260413T000000Z",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_consult_packet_show", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            packet_id="architect_packet_20260413T000000Z",
            json_output=False,
        )

    def test_main_routes_consult_status(self, tmp_path):
        argv = [
            "cc.py",
            "consult",
            "status",
            "--project-root", str(tmp_path),
            "--role", "architect",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_consult_status", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            role="architect",
            json_output=False,
        )

    def test_main_routes_consult_resolution(self, tmp_path):
        argv = [
            "cc.py",
            "consult",
            "resolution",
            "--project-root", str(tmp_path),
            "--role", "architect",
            "--topic-key", "adapter_split",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_consult_resolution_show", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            role="architect",
            topic_key="adapter_split",
            json_output=False,
        )


class TestSurfaceAuthorityCommands:
    def test_surface_claim_acquires_and_releases_surface_lock(self, tmp_path, monkeypatch):
        _make_surface_project(tmp_path)
        events = []
        real_guard = cc_lockfile.surface_lock_guard

        def spy_guard(project, timeout_seconds=cc_lockfile.SURFACE_LOCK_TIMEOUT_SECONDS, poll_seconds=cc_lockfile.SURFACE_LOCK_POLL_SECONDS):
            lockfile_path = cc_lockfile.surface_lockfile_path(project)
            guard = real_guard(project, timeout_seconds, poll_seconds)

            class GuardSpy:
                def __enter__(self):
                    result = guard.__enter__()
                    assert lockfile_path.exists()
                    events.append("acquired")
                    return result

                def __exit__(self, exc_type, exc, traceback):
                    result = guard.__exit__(exc_type, exc, traceback)
                    assert not lockfile_path.exists()
                    events.append("released")
                    return result

            return GuardSpy()

        monkeypatch.setattr(cc_lockfile, "surface_lock_guard", spy_guard)

        assert cc.cmd_surface_claim(tmp_path, "host:vscode", "official_host", "Claude Code (VS Code)") == 0

        assert events == ["acquired", "released"]

    def test_surface_lock_cleans_up_when_mutation_fails(self, tmp_path, monkeypatch):
        _make_surface_project(tmp_path)
        lockfile_path = cc_lockfile.surface_lockfile_path(tmp_path)

        def fail_save(project, state):
            raise RuntimeError("forced surface save failure")

        monkeypatch.setattr(cc, "_save_surface_lock", fail_save)

        with pytest.raises(RuntimeError, match="forced surface save failure"):
            cc.cmd_surface_claim(tmp_path, "host:vscode", "official_host", "Claude Code (VS Code)")

        assert not lockfile_path.exists()

    def test_surface_lock_timeout_does_not_modify_state(self, tmp_path, monkeypatch, capsys):
        _make_surface_project(tmp_path)
        lock_path = cc._surface_lock_path(tmp_path)
        lock_path.write_text(
            json.dumps(
                {
                    "surfaces": [],
                    "takeoverRequest": None,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        original_state = lock_path.read_text(encoding="utf-8")
        cc_lockfile.surface_lockfile_path(tmp_path).write_text("busy\n", encoding="utf-8")
        real_guard = cc_lockfile.surface_lock_guard

        def immediate_timeout_guard(project, timeout_seconds=cc_lockfile.SURFACE_LOCK_TIMEOUT_SECONDS, poll_seconds=cc_lockfile.SURFACE_LOCK_POLL_SECONDS):
            return real_guard(project, timeout_seconds=0, poll_seconds=0)

        monkeypatch.setattr(cc_lockfile, "surface_lock_guard", immediate_timeout_guard)

        assert cc.cmd_surface_claim(tmp_path, "host:vscode", "official_host", "Claude Code (VS Code)") == 1

        assert "timed out waiting for surface lock" in capsys.readouterr().out
        assert lock_path.read_text(encoding="utf-8") == original_state

    def test_surface_claim_sets_official_host_primary(self, tmp_path):
        _make_surface_project(tmp_path)
        result = cc.cmd_surface_claim(
            tmp_path,
            "host:vscode",
            "official_host",
            "Claude Code (VS Code)",
        )
        assert result == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["authorityMode"] == "host_locked"
        assert lock["primarySurfaceId"] == "host:vscode"
        assert lock["primarySurfaceType"] == "official_host"
        assert any(
            surface["surfaceId"] == "host:vscode" and surface["role"] == "primary"
            for surface in lock["surfaces"]
        )

    def test_surface_observe_keeps_existing_primary(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:local", "cc_ui_local", "Local Concierge") == 0
        assert cc.cmd_surface_observe(tmp_path, "host:claude", "official_host", "Claude Code") == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["authorityMode"] == "stable"
        assert lock["primarySurfaceId"] == "ui:local"
        observer = next(surface for surface in lock["surfaces"] if surface["surfaceId"] == "host:claude")
        assert observer["role"] == "observer"

    def test_surface_claim_blocks_cc_ui_while_official_host_is_primary(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "host:codex", "official_host", "Codex CLI") == 0
        result = cc.cmd_surface_claim(tmp_path, "ui:api", "cc_ui_api", "API Studio")
        assert result == 1
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["primarySurfaceId"] == "host:codex"
        assert lock["primarySurfaceType"] == "official_host"

    def test_surface_release_reassigns_primary(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:a", "cc_ui_local", "UI A") == 0
        assert cc.cmd_surface_observe(tmp_path, "ui:b", "cc_ui_local", "UI B") == 0
        assert cc.cmd_surface_release(tmp_path, "ui:a") == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["primarySurfaceId"] == "ui:b"
        assert any(
            surface["surfaceId"] == "ui:b" and surface["role"] == "primary"
            for surface in lock["surfaces"]
        )

    def test_surface_heartbeat_refreshes_last_seen(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:local", "cc_ui_local", "Local Concierge") == 0
        with patch.object(cc, "_utc_now_iso", return_value="2030-01-01T00:00:00.000Z"):
            assert cc.cmd_surface_heartbeat(tmp_path, "ui:local") == 0
        lock_path = tmp_path / ".controlcoding" / "cc_surface_lock.json"
        refreshed = json.loads(lock_path.read_text(encoding="utf-8"))
        row = next(surface for surface in refreshed["surfaces"] if surface["surfaceId"] == "ui:local")
        assert row["lastSeenAt"] == "2030-01-01T00:00:00.000Z"

    def test_surface_status_json(self, tmp_path, capsys):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "host:claude", "official_host", "Claude Code") == 0
        capsys.readouterr()
        assert cc.cmd_surface_status(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["primarySurfaceId"] == "host:claude"
        assert payload["authorityMode"] == "host_locked"

    def test_surface_status_fail_closes_ambiguous_lock(self, tmp_path, capsys):
        _make_surface_project(tmp_path)
        now = "2030-01-01T00:00:00.000Z"
        (tmp_path / ".controlcoding" / "cc_surface_lock.json").write_text(
            json.dumps(
                {
                    "surfaces": [
                        {
                            "surfaceId": "ui:a",
                            "surfaceType": "cc_ui_api",
                            "label": "API A",
                            "role": "primary",
                            "desiredRole": "primary",
                            "active": True,
                            "synthetic": False,
                            "attachedAt": now,
                            "lastSeenAt": now,
                        },
                        {
                            "surfaceId": "ui:b",
                            "surfaceType": "cc_ui_api",
                            "label": "API B",
                            "role": "primary",
                            "desiredRole": "primary",
                            "active": True,
                            "synthetic": False,
                            "attachedAt": now,
                            "lastSeenAt": now,
                        },
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        assert cc.cmd_surface_status(tmp_path, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["authorityMode"] == "blocked"
        assert payload["primarySurfaceId"] is None
        assert "Ambiguous surface authority" in payload["rationale"]

    def test_surface_request_takeover_repairs_ambiguous_lock(self, tmp_path):
        _make_surface_project(tmp_path)
        now = "2030-01-01T00:00:00.000Z"
        (tmp_path / ".controlcoding" / "cc_surface_lock.json").write_text(
            json.dumps(
                {
                    "surfaces": [
                        {
                            "surfaceId": "ui:a",
                            "surfaceType": "cc_ui_api",
                            "label": "API A",
                            "role": "primary",
                            "desiredRole": "primary",
                            "active": True,
                            "synthetic": False,
                            "attachedAt": now,
                            "lastSeenAt": now,
                        },
                        {
                            "surfaceId": "ui:b",
                            "surfaceType": "cc_ui_api",
                            "label": "API B",
                            "role": "primary",
                            "desiredRole": "primary",
                            "active": True,
                            "synthetic": False,
                            "attachedAt": now,
                            "lastSeenAt": now,
                        },
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        assert cc.cmd_surface_request_takeover(tmp_path, "ui:a", "Repair blocked authority") == 0

        repaired = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert repaired["authorityMode"] == "stable"
        assert repaired["primarySurfaceId"] == "ui:a"
        assert sum(1 for surface in repaired["surfaces"] if surface["role"] == "primary") == 1

    def test_surface_request_takeover_marks_pending_request(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:a", "cc_ui_api", "API A") == 0
        assert cc.cmd_surface_observe(tmp_path, "ui:b", "cc_ui_api", "API B") == 0

        assert cc.cmd_surface_request_takeover(tmp_path, "ui:b", "Need API authority") == 0

        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["authorityMode"] == "takeover_pending"
        assert lock["primarySurfaceId"] == "ui:a"
        assert lock["takeoverRequest"]["requestedBySurfaceId"] == "ui:b"
        assert lock["takeoverRequest"]["currentPrimarySurfaceId"] == "ui:a"
        assert lock["takeoverRequest"]["reason"] == "Need API authority"

    def test_surface_resolve_takeover_approved_reassigns_primary(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:a", "cc_ui_api", "API A") == 0
        assert cc.cmd_surface_observe(tmp_path, "ui:b", "cc_ui_api", "API B") == 0
        assert cc.cmd_surface_request_takeover(tmp_path, "ui:b", "Need API authority") == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))

        assert cc.cmd_surface_resolve_takeover(
            tmp_path,
            lock["takeoverRequest"]["requestId"],
            "approved",
            "ui:a",
        ) == 0

        resolved = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert resolved["authorityMode"] == "stable"
        assert resolved["primarySurfaceId"] == "ui:b"
        assert resolved["takeoverRequest"] is None
        assert sum(1 for surface in resolved["surfaces"] if surface["role"] == "primary") == 1

    def test_surface_resolve_takeover_denied_returns_clean_state(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:a", "cc_ui_local", "Local A") == 0
        assert cc.cmd_surface_observe(tmp_path, "ui:b", "cc_ui_local", "Local B") == 0
        assert cc.cmd_surface_request_takeover(tmp_path, "ui:b", "Need local control") == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))

        assert cc.cmd_surface_resolve_takeover(
            tmp_path,
            lock["takeoverRequest"]["requestId"],
            "denied",
            "ui:a",
        ) == 0

        resolved = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        denied_surface = next(surface for surface in resolved["surfaces"] if surface["surfaceId"] == "ui:b")
        assert resolved["authorityMode"] == "stable"
        assert resolved["primarySurfaceId"] == "ui:a"
        assert resolved["takeoverRequest"] is None
        assert denied_surface["role"] == "observer"
        assert denied_surface["desiredRole"] == "observer"

    def test_surface_resolve_takeover_requires_current_primary(self, tmp_path):
        _make_surface_project(tmp_path)
        assert cc.cmd_surface_claim(tmp_path, "ui:a", "cc_ui_api", "API A") == 0
        assert cc.cmd_surface_observe(tmp_path, "ui:b", "cc_ui_api", "API B") == 0
        assert cc.cmd_surface_request_takeover(tmp_path, "ui:b", "Need API authority") == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))

        assert cc.cmd_surface_resolve_takeover(
            tmp_path,
            lock["takeoverRequest"]["requestId"],
            "approved",
            "ui:b",
        ) == 1

        pending = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert pending["authorityMode"] == "takeover_pending"
        assert pending["takeoverRequest"]["requestedBySurfaceId"] == "ui:b"

    def test_surface_run_releases_lock_after_child_exit(self, tmp_path):
        _make_surface_project(tmp_path)
        exit_code = cc.cmd_surface_run(
            tmp_path,
            "host:codex",
            "official_host",
            "Codex CLI",
            "primary",
            0.01,
            False,
            [sys.executable, "-c", "print('surface child ok')"],
        )
        assert exit_code == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["primarySurfaceId"] is None
        assert not [surface for surface in lock["surfaces"] if not surface.get("synthetic")]

    def test_surface_run_keep_lock_preserves_surface(self, tmp_path):
        _make_surface_project(tmp_path)
        exit_code = cc.cmd_surface_run(
            tmp_path,
            "ui:api",
            "cc_ui_api",
            "API Studio",
            "primary",
            0.01,
            True,
            [sys.executable, "-c", "print('done')"],
        )
        assert exit_code == 0
        lock = json.loads((tmp_path / ".controlcoding" / "cc_surface_lock.json").read_text(encoding="utf-8"))
        assert lock["primarySurfaceId"] == "ui:api"
        assert any(
            surface["surfaceId"] == "ui:api" and surface["role"] == "primary"
            for surface in lock["surfaces"]
        )

    def test_surface_run_main_routes_known_and_child_args(self, tmp_path):
        _make_surface_project(tmp_path)
        argv = [
            "cc.py",
            "surface",
            "run",
            "--project-root", str(tmp_path),
            "host:claude",
            "--type", "official_host",
            "--role", "observer",
            "--label", "Claude Code",
            "--heartbeat-seconds", "1.5",
            "--keep-lock",
            "--",
            "child-tool",
            "--flag",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_surface_run", return_value=0) as mock_run:
            assert cc.main() == 0
        mock_run.assert_called_once()
        _, _, _, _, role, heartbeat_seconds, keep_lock, command_args = mock_run.call_args.args
        assert role == "observer"
        assert heartbeat_seconds == 1.5
        assert keep_lock is True
        assert command_args == ["--", "child-tool", "--flag"]

    def test_surface_main_routes_request_takeover(self, tmp_path):
        argv = [
            "cc.py",
            "surface",
            "request-takeover",
            "--project-root", str(tmp_path),
            "ui:b",
            "--reason", "Need API authority",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_surface_request_takeover", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            "ui:b",
            reason="Need API authority",
        )

    def test_surface_main_routes_resolve_takeover(self, tmp_path):
        argv = [
            "cc.py",
            "surface",
            "resolve-takeover",
            "--project-root", str(tmp_path),
            "takeover_123",
            "--decision", "approved",
            "--resolved-by", "ui:a",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_surface_resolve_takeover", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            "takeover_123",
            "approved",
            "ui:a",
        )

    def test_setup_chat_wizard_routes_to_chat_guide(self, tmp_path):
        argv = [
            "cc.py",
            "setup",
            "--chat-wizard",
            "--host-hint", "codex_cli",
            "--project-root", str(tmp_path),
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup_chat_guide", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), host_hint="codex_cli")

    def test_setup_answers_file_routes_to_setup(self, tmp_path):
        answers_file = tmp_path / "answers.json"
        answers_file.write_text("{}", encoding="utf-8")
        argv = [
            "cc.py",
            "setup",
            "--project-root", str(tmp_path),
            "--answers-file", str(answers_file),
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), answers_file=answers_file, apply_answers=False)

    def test_setup_apply_answers_routes_to_setup(self, tmp_path):
        answers_file = tmp_path / "answers.json"
        answers_file.write_text("{}", encoding="utf-8")
        argv = [
            "cc.py",
            "setup",
            "--project-root", str(tmp_path),
            "--answers-file", str(answers_file),
            "--apply-answers",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), answers_file=answers_file, apply_answers=True)

    def test_setup_engagement_apply_answers_routes_to_engagement(self, tmp_path):
        answers_file = tmp_path / "answers.json"
        answers_file.write_text("{}", encoding="utf-8")
        argv = [
            "cc.py",
            "setup",
            "--engagement",
            "--project-root", str(tmp_path),
            "--answers-file", str(answers_file),
            "--apply-answers",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup_engagement", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), answers_file=answers_file, apply_answers=True)

    def test_setup_project_chat_wizard_routes_to_project_chat_guide(self, tmp_path):
        argv = [
            "cc.py",
            "setup-project",
            "--chat-wizard",
            "--host-hint", "codex_cli",
            "--project-root", str(tmp_path),
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup_project_chat_guide", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), host_hint="codex_cli")

    def test_setup_project_answers_file_routes_to_project_setup(self, tmp_path):
        answers_file = tmp_path / "answers.json"
        answers_file.write_text("{}", encoding="utf-8")
        argv = [
            "cc.py",
            "setup-project",
            "--project-root", str(tmp_path),
            "--answers-file", str(answers_file),
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup_project", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), answers_file=answers_file, apply_answers=False)

    def test_setup_project_apply_answers_routes_to_project_setup(self, tmp_path):
        answers_file = tmp_path / "answers.json"
        answers_file.write_text("{}", encoding="utf-8")
        argv = [
            "cc.py",
            "setup-project",
            "--project-root", str(tmp_path),
            "--answers-file", str(answers_file),
            "--apply-answers",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_setup_project", return_value=0) as mock_setup:
            assert cc.main() == 0
        mock_setup.assert_called_once_with(tmp_path.resolve(), answers_file=answers_file, apply_answers=True)


# ---------------------------------------------------------- TestCmdReview ---


class TestCmdReview:
    def test_main_routes_review_stdout_to_cmd_review(self, tmp_path):
        argv = [
            "cc.py",
            "review",
            "--stdout",
            "--project-root", str(tmp_path),
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_review", return_value=0) as mock_review:
            assert cc.main() == 0
        mock_review.assert_called_once_with(tmp_path.resolve(), to_stdout=True)

    def test_review_route_is_registered_for_truth_contract(self):
        assert cc._truth_route_exists(("review",))

    def test_cc_review_import_does_not_import_cc_py(self):
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(scripts_dir)!r})\n"
            "import cc_review\n"
            "raise SystemExit(1 if 'cc' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert cc_review.REVIEW_ROUTED_COMMANDS == frozenset({("review",)})

    def test_main_routes_init_module_to_cmd_init_module(self, tmp_path):
        argv = [
            "cc.py",
            "init-module",
            "demo",
            "--dir",
            "custom/path",
            "--project-root",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_init_module", return_value=0) as mock_init:
            assert cc.main() == 0
        mock_init.assert_called_once_with(tmp_path.resolve(), "demo", module_dir="custom/path")

    def test_init_module_route_is_registered_for_truth_contract(self):
        assert cc._truth_route_exists(("init-module",))

    def test_cc_init_module_import_does_not_import_cc_py(self):
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(scripts_dir)!r})\n"
            "import cc_init_module\n"
            "raise SystemExit(1 if 'cc' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert cc_init_module.INIT_MODULE_ROUTED_COMMANDS == frozenset({("init-module",)})

    def test_no_claude_md_fails(self, tmp_path):
        result = cc.cmd_review(tmp_path)
        assert result == 1

    def test_generates_prompt(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# MyProject\nRules here.", encoding="utf-8")
        # cc_config
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps({"protected_zones": [
                {"path": "src/core/", "level": "deny", "description": "Core"}
            ]}),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="CLAUDE.md\nsrc/app.py\n",
            )
            result = cc.cmd_review(tmp_path)
        assert result == 0
        output = tmp_path / "docs" / "review_prompt.md"
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "MyProject" in content

    def test_stdout_mode(self, tmp_path, capsys):
        (tmp_path / "CLAUDE.md").write_text("# Proj\nContent.", encoding="utf-8")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="CLAUDE.md\n"
            )
            result = cc.cmd_review(tmp_path, to_stdout=True)
        assert result == 0
        captured = capsys.readouterr()
        assert "Proj" in captured.out

    def test_review_accepts_legacy_protected_zone_map(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# MyProject\nRules here.", encoding="utf-8")
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps({"protected_zones": {"deny": ["src/core/"], "warn": ["src/shared/"]}}),
            encoding="utf-8",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="CLAUDE.md\nsrc/app.py\n",
            )
            result = cc.cmd_review(tmp_path)
        assert result == 0
        output = tmp_path / "docs" / "review_prompt.md"
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "src/core/" in content


# ------------------------------------------------ TestCollectProjectFiles ---


class TestCollectProjectFiles:
    def test_git_mode(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="CLAUDE.md\nsrc/app.py\ntests/test_app.py\n",
            )
            result = cc._collect_project_files(tmp_path)
        assert any("CLAUDE.md" in r for r in result)
        assert any("app.py" in r for r in result)

    def test_fallback_mode(self, tmp_path):
        (tmp_path / "app.py").write_text("", encoding="utf-8")
        (tmp_path / "readme.md").write_text("", encoding="utf-8")
        with patch("subprocess.run", side_effect=Exception("no git")):
            result = cc._collect_project_files(tmp_path)
        assert len(result) >= 2

    def test_binary_filtered(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="app.py\nimage.png\nlib.dll\n",
            )
            result = cc._collect_project_files(tmp_path)
        texts = " ".join(result)
        assert "app.py" in texts
        assert "image.png" not in texts
        assert "lib.dll" not in texts


# -------------------------------------------------------------- export ------


class TestExportAgentsMd:
    """Tests for cc export agents-md command."""

    SAMPLE_CLAUDE_MD = """\
# MyProject - CLAUDE.md

## Project Identity

- **Name**: MyProject
- **Stack**: Python 3.12

## Architecture Rules

1. All DB access through repository classes

## Module Boundaries

### Stable
- `core/` - Data models

## Domain Invariants

1. total_debits == total_credits

## Protected Zones [hook-enforced]

- `core/` - DENY

## Session Start Ritual [advisory]

At the start of every session:

1. Load project memory before editing.
2. Run `python scripts/cc.py chat-start --project-root . --scope dev --topic "<current task>"` when available.

## Commit Ceremony [advisory]

Every commit produces 6 artifacts.

## Operative Rules [advisory]

- Before creating a new function, search the codebase
- Before non-trivial work, run `python scripts/cc.py work-start --project-root . --scope dev --topic "<current task>"`
- Before closeout, run `python scripts/cc.py work-close --project-root . --topic "<current task>" --summary "<work completed>"`

## Current Focus

- [ ] Build feature X

## Semantic Fidelity [advisory]

- Never paraphrase requirements
"""

    def test_generates_agents_md(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        result = cc.cmd_export_agents_md(tmp_path)
        assert result == 0
        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert "## Project Identity" in content
        assert "## Architecture Rules" in content
        assert "## Module Boundaries" in content
        assert "## Domain Invariants" in content
        assert "## Protected Zones" in content
        marker, state, _detail = cc._adapter_marker_payload(content)
        assert state == "owned"
        assert marker["owner"] == "ControlCoding"
        assert marker["schema"] == "controlcoding.host-adapter-ownership"
        assert marker["target"] == "AGENTS.md"
        assert marker["host"] == "codex_cli"
        assert marker["source"] == "CONTROLCODING.md"
        assert marker["format"] == "agents-md-portable-v1"

    def test_excludes_cc_specific_sections(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        cc.cmd_export_agents_md(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "Commit Ceremony" not in content
        assert "Session Start Ritual" not in content
        assert "Operative Rules" not in content
        assert "Current Focus" not in content
        assert "Semantic Fidelity" not in content

    def test_force_does_not_adopt_foreign_file(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("old content", encoding="utf-8")
        result = cc.cmd_export_agents_md(tmp_path, force=True)
        assert result == 1
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "old content"

    def test_force_replaces_only_valid_owned_file(self, tmp_path):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical.write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        assert cc.cmd_export_agents_md(tmp_path) == 0
        canonical.write_text(
            canonical.read_text(encoding="utf-8").replace("Python 3.12", "Python 3.13"),
            encoding="utf-8",
        )

        assert cc.cmd_export_agents_md(tmp_path) == 1
        assert "Python 3.12" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert cc.cmd_export_agents_md(tmp_path, force=True) == 0
        assert "Python 3.13" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    def test_no_force_existing_file_errors(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")
        result = cc.cmd_export_agents_md(tmp_path, force=False)
        assert result == 1
        # Original file should be unchanged
        assert (tmp_path / "AGENTS.md").read_text(
            encoding="utf-8") == "existing"

    def test_missing_section_warns_no_crash(self, tmp_path, capsys):
        minimal = "# Minimal\n\n## Project Identity\n\n- Name: Test\n"
        (tmp_path / "CONTROLCODING.md").write_text(minimal, encoding="utf-8")
        result = cc.cmd_export_agents_md(tmp_path)
        assert result == 0
        output = capsys.readouterr().out
        assert "Warning:" in output
        # Should still include the section that exists
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "## Project Identity" in content

    def test_no_claude_md_errors(self, tmp_path):
        result = cc.cmd_export_agents_md(tmp_path)
        assert result == 1

    def test_strips_enforcement_markers(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        cc.cmd_export_agents_md(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        # Should have "## Protected Zones" not "## Protected Zones [hook-enforced]"
        assert "## Protected Zones" in content
        assert "[hook-enforced]" not in content

    def test_uses_canonical_context_source_when_present(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# stale legacy", encoding="utf-8")

        result = cc.cmd_export_agents_md(tmp_path)

        assert result == 0
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "## Project Identity" in content
        assert "stale legacy" not in content

    def test_preview_only_never_creates_target(self, tmp_path, capsys):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")

        assert cc.cmd_export_agents_md(tmp_path, preview_only=True) == 0

        assert not (tmp_path / "AGENTS.md").exists()
        output = capsys.readouterr().out
        assert "Adapter preview" in output
        assert "Target: AGENTS.md" in output
        assert "Ownership: none" in output
        assert "Action: create" in output
        assert "Replaces existing: no" in output


class TestExportHostContext:
    SAMPLE_CLAUDE_MD = TestExportAgentsMd.SAMPLE_CLAUDE_MD

    def test_generates_codex_agents_md_with_runtime_sections(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")

        result = cc.cmd_export_host_context(tmp_path, host="codex_cli")

        assert result == 0
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "## Project Identity" in content
        assert "## Protected Zones" in content
        assert "## Session Start Ritual" in content
        assert "chat-start --project-root" in content
        assert "## Operative Rules" in content
        assert "work-start --project-root" in content
        assert "work-close --project-root" in content
        assert "## Current Focus" in content
        marker, state, _detail = cc._adapter_marker_payload(content)
        assert state == "owned"
        assert marker["format"] == "host-context-section-export-v1"

    def test_infers_host_from_gateway_config(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "gateway_config.json").write_text(
            json.dumps({"userHost": "gemini_cli"}, indent=2),
            encoding="utf-8",
        )

        result = cc.cmd_export_host_context(tmp_path)

        assert result == 0
        assert (tmp_path / "GEMINI.md").exists()

    def test_existing_target_requires_force(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")

        result = cc.cmd_export_host_context(tmp_path, host="codex_cli")

        assert result == 1
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "existing"
        assert cc.cmd_export_host_context(tmp_path, host="codex_cli", force=True) == 1
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "existing"

    def test_generates_cursor_rules_context(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")

        result = cc.cmd_export_host_context(tmp_path, host="cursor")

        assert result == 0
        content = (tmp_path / ".cursor" / "rules" / "project.mdc").read_text(encoding="utf-8")
        assert "Target host: Cursor." in content
        assert "## Project Identity" in content

    def test_generates_windsurf_rules_context(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")

        result = cc.cmd_export_host_context(tmp_path, host="windsurf")

        assert result == 0
        content = (tmp_path / ".windsurfrules").read_text(encoding="utf-8")
        assert "Target host: Windsurf." in content
        assert "## Project Identity" in content

    def test_manual_vscode_context_check_is_not_applicable(self, tmp_path, capsys):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        result = cc.cmd_context_check(tmp_path, host="vscode", json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["hosts"][0]["state"] == "not_applicable"

    def test_generates_claude_md_from_canonical_source(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        result = cc.cmd_export_host_context(tmp_path, host="claude_code")

        assert result == 0
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Generated from CONTROLCODING.md" in content
        assert "Canonical source of truth: CONTROLCODING.md" in content
        assert "CLAUDE.md" in content

    def test_generates_agents_md_from_controlwork_source(self, tmp_path):
        controlwork = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLWORK.md")
        (tmp_path / "CONTROLWORK.md").write_text(controlwork, encoding="utf-8")

        result = cc.cmd_export_host_context(
            tmp_path,
            host="codex_cli",
            source="CONTROLWORK.md",
        )

        assert result == 0
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "Generated from CONTROLWORK.md" in content
        assert "Selected source: CONTROLWORK.md (non-canonical)." in content
        assert "Canonical source of truth: CONTROLWORK.md" not in content
        assert "## Project Identity" in content
        assert "## Operative Rules" in content
        marker, state, _detail = cc._adapter_marker_payload(content)
        assert state == "owned"
        assert marker["source"] == "CONTROLWORK.md"
        assert cc._host_context_source_state(
            tmp_path,
            source="CONTROLWORK.md",
        ) == "explicit"

    def test_preview_only_host_export_is_read_only(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        assert cc.cmd_export_host_context(tmp_path, host="cursor", preview_only=True) == 0

        assert not (tmp_path / ".cursor").exists()

    def test_host_export_requires_force_to_replace_valid_owned_target(self, tmp_path):
        canonical_path = tmp_path / "CONTROLCODING.md"
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        canonical_path.write_text(canonical, encoding="utf-8")
        assert cc.cmd_export_host_context(tmp_path, host="codex_cli") == 0
        canonical_path.write_text(canonical.replace("Python 3.12", "Python 3.13"), encoding="utf-8")

        assert cc.cmd_export_host_context(tmp_path, host="codex_cli") == 1
        assert cc.cmd_export_host_context(tmp_path, host="codex_cli", force=True) == 0
        assert "Python 3.13" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    def test_explicit_legacy_source_target_alias_is_blocked(self, tmp_path):
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(self.SAMPLE_CLAUDE_MD, encoding="utf-8")

        assert cc.cmd_export_host_context(
            tmp_path,
            host="claude_code",
            source="CLAUDE.md",
            force=True,
        ) == 1

        assert legacy.read_text(encoding="utf-8") == self.SAMPLE_CLAUDE_MD

    def test_main_routes_export_host_context_with_source(self, tmp_path):
        argv = [
            "cc.py",
            "export",
            "host-context",
            "--project-root", str(tmp_path),
            "--host", "codex_cli",
            "--source", "CONTROLWORK.md",
            "--force",
        ]

        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_export_host_context", return_value=0) as mock_cmd:
            assert cc.main() == 0

        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            host="codex_cli",
            force=True,
            source="CONTROLWORK.md",
            preview_only=False,
        )


class TestContextCommands:
    SAMPLE_CLAUDE_MD = TestExportAgentsMd.SAMPLE_CLAUDE_MD

    def test_context_sync_host_generates_agents_from_canonical(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        result = cc.cmd_context_sync(tmp_path, host="codex_cli")

        assert result == 0
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "Generated from CONTROLCODING.md" in content
        assert "Canonical source of truth: CONTROLCODING.md" in content
        assert "## Project Identity" in content
        assert "## Operative Rules" in content

    def test_context_sync_rejects_automatic_agents_format_rebinding(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        assert cc.cmd_export_agents_md(tmp_path) == 0
        portable_marker, state, _detail = cc._adapter_marker_payload(
            (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        )
        assert state == "owned"
        assert portable_marker["format"] == "agents-md-portable-v1"

        portable_content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1

        runtime_marker, state, _detail = cc._adapter_marker_payload(
            (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        )
        assert state == "owned"
        assert runtime_marker["format"] == "agents-md-portable-v1"
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == portable_content

    def test_source_rebind_is_invalid_even_with_explicit_source_or_force(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        controlwork = canonical.replace("CONTROLCODING.md", "CONTROLWORK.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        (tmp_path / "CONTROLWORK.md").write_text(controlwork, encoding="utf-8")
        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            source="CONTROLWORK.md",
        ) == 0
        target = tmp_path / "AGENTS.md"
        controlwork_bytes = target.read_bytes()

        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
        assert target.read_bytes() == controlwork_bytes
        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            source="CONTROLCODING.md",
        ) == 1
        assert cc.cmd_export_agents_md(tmp_path) == 1
        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        marker, state, _detail = cc._adapter_marker_payload(target.read_text(encoding="utf-8"))
        assert state == "owned"
        assert marker["source"] == "CONTROLWORK.md"
        assert marker["format"] == "host-context-section-export-v1"

    def test_context_check_reports_current_and_stale_json(self, tmp_path, capsys):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        capsys.readouterr()

        assert cc.cmd_context_check(tmp_path, host="codex_cli", json_output=True) == 0
        current_payload = json.loads(capsys.readouterr().out)
        assert current_payload["ok"] is True
        assert current_payload["hosts"][0]["state"] == "current"
        assert current_payload["hosts"][0]["ownershipState"] == "owned_current"

        (tmp_path / "CONTROLCODING.md").write_text(
            canonical.replace("Python 3.12", "Python 3.13"),
            encoding="utf-8",
        )
        assert cc.cmd_context_check(tmp_path, host="codex_cli", json_output=True) == 1
        stale_payload = json.loads(capsys.readouterr().out)
        assert stale_payload["ok"] is False
        assert stale_payload["hosts"][0]["state"] == "stale"
        assert stale_payload["hosts"][0]["ownershipState"] == "owned_stale"

    def test_context_json_is_serializable_sanitized_and_keeps_legacy_schema(self, tmp_path, capsys):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        missing_payload = cc._context_sync_payload(tmp_path, host="codex_cli")
        json.dumps(missing_payload)
        assert missing_payload["hosts"][0]["state"] == "missing"
        assert missing_payload["hosts"][0]["ownershipState"] == "target_absent"

        def assert_public(value):
            if isinstance(value, dict):
                assert all(not str(key).startswith("_") for key in value)
                for child in value.values():
                    assert_public(child)
            elif isinstance(value, list):
                for child in value:
                    assert_public(child)
            else:
                assert not isinstance(value, bytes)

        assert_public(missing_payload)
        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            json_output=True,
        ) == 0
        sync_payload = json.loads(capsys.readouterr().out)
        assert sync_payload["results"] == [{
            "host": "codex_cli",
            "label": "Codex CLI",
            "path": "AGENTS.md",
            "ok": True,
            "detail": "synced",
        }]
        assert sync_payload["contextSync"]["hosts"][0]["state"] == "current"
        assert sync_payload["contextSync"]["hosts"][0]["ownershipState"] == "owned_current"
        assert_public(sync_payload)

    def test_context_sync_and_check_use_explicit_controlwork_source(self, tmp_path, capsys):
        controlwork = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLWORK.md")
        (tmp_path / "CONTROLWORK.md").write_text(controlwork, encoding="utf-8")

        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            source="CONTROLWORK.md",
            json_output=True,
        ) == 0
        sync_payload = json.loads(capsys.readouterr().out)
        assert sync_payload["contextSync"]["canonicalPath"] == "CONTROLCODING.md"
        rendered = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "Selected source: CONTROLWORK.md (non-canonical)." in rendered
        assert "Canonical source of truth: CONTROLWORK.md" not in rendered

        assert cc.cmd_context_check(
            tmp_path,
            host="codex_cli",
            source="CONTROLWORK.md",
            json_output=True,
        ) == 0
        check_payload = json.loads(capsys.readouterr().out)
        assert check_payload["ok"] is True
        assert check_payload["sourceState"] == "explicit"
        assert check_payload["sourcePath"] == "CONTROLWORK.md"

    def test_nested_canonical_basename_is_explicit_but_not_root_canonical(self, tmp_path):
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )

        assert cc._host_context_source_state(
            tmp_path,
            source="nested/CONTROLCODING.md",
        ) == "explicit"

    @pytest.mark.parametrize(
        ("source_name", "expected_state"),
        [
            ("SOURCE.md", "explicit"),
            ("CLAUDE.md", "explicit"),
        ],
    )
    def test_context_sync_accepts_safe_explicit_noncanonical_source(
        self,
        tmp_path,
        source_name,
        expected_state,
    ):
        source_text = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", source_name)
        (tmp_path / source_name).write_text(source_text, encoding="utf-8")

        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            source=source_name,
        ) == 0

        payload = cc._context_sync_payload(
            tmp_path,
            host="codex_cli",
            source=source_name,
        )
        assert payload["ok"] is True
        assert payload["sourceState"] == expected_state
        marker, state, _detail = cc._adapter_marker_payload(
            (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        )
        assert state == "owned"
        assert marker["source"] == source_name

    def test_default_context_sync_blocks_legacy_only_source_without_write(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text(self.SAMPLE_CLAUDE_MD, encoding="utf-8")

        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1

        assert cc._host_context_source_state(tmp_path) == "legacy_only"
        assert not (tmp_path / "AGENTS.md").exists()

    @pytest.mark.parametrize("source_name", ["SOURCE.md", "CONTROLWORK.md"])
    def test_context_check_labels_override_as_explicit_not_canonical(
        self,
        tmp_path,
        capsys,
        source_name,
    ):
        (tmp_path / source_name).write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", source_name),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            source=source_name,
        ) == 0
        capsys.readouterr()

        assert cc.cmd_context_check(
            tmp_path,
            host="codex_cli",
            source=source_name,
        ) == 0

        output = capsys.readouterr().out
        assert f"Selected explicit context source: {source_name}" in output
        assert f"{source_name} is the canonical source" not in output

    def test_main_routes_context_check_with_source(self, tmp_path):
        argv = [
            "cc.py",
            "context",
            "check",
            "--project-root", str(tmp_path),
            "--host", "codex_cli",
            "--source", "CONTROLWORK.md",
            "--json",
        ]

        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_context_check", return_value=0) as mock_cmd:
            assert cc.main() == 0

        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            host="codex_cli",
            all_hosts=False,
            source="CONTROLWORK.md",
            json_output=True,
        )

    def test_main_routes_context_sync_with_source(self, tmp_path):
        argv = [
            "cc.py",
            "context",
            "sync",
            "--project-root", str(tmp_path),
            "--host", "codex_cli",
            "--source", "CONTROLWORK.md",
            "--json",
        ]

        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_context_sync", return_value=0) as mock_cmd:
            assert cc.main() == 0

        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            host="codex_cli",
            all_hosts=False,
            source="CONTROLWORK.md",
            json_output=True,
            preview_only=False,
        )

    def test_main_routes_context_adopt_apply(self, tmp_path):
        argv = [
            "cc.py",
            "context",
            "adopt",
            "--project-root", str(tmp_path),
            "--host", "codex_cli",
            "--source", "CONTROLWORK.md",
            "--apply",
            "--json",
        ]

        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_context_adopt", return_value=0) as mock_cmd:
            assert cc.main() == 0

        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            host="codex_cli",
            all_hosts=False,
            source="CONTROLWORK.md",
            apply=True,
            json_output=True,
        )

    def test_context_sync_all_hosts_generates_claude_and_agents(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        result = cc.cmd_context_sync(tmp_path, all_hosts=True)

        assert result == 0
        assert (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / "GEMINI.md").exists()
        assert (tmp_path / ".clinerules").exists()
        assert "Generated from CONTROLCODING.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Generated from CONTROLCODING.md" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    def test_context_check_fails_when_only_legacy_context_exists(self, tmp_path, capsys):
        (tmp_path / "CLAUDE.md").write_text(self.SAMPLE_CLAUDE_MD, encoding="utf-8")

        result = cc.cmd_context_check(tmp_path, host="codex_cli", json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["sourceState"] == "legacy_only"

    def test_context_sync_preview_only_is_read_only(self, tmp_path, capsys):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")

        assert cc.cmd_context_sync(
            tmp_path,
            host="codex_cli",
            preview_only=True,
        ) == 0

        assert not (tmp_path / "AGENTS.md").exists()
        output = capsys.readouterr().out
        assert "Adapter preview" in output
        assert "Action: create" in output

    def test_context_sync_all_hosts_writes_six_owned_markers(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        targets = {
            "claude_code": "CLAUDE.md",
            "codex_cli": "AGENTS.md",
            "gemini_cli": "GEMINI.md",
            "cline": ".clinerules",
            "cursor": ".cursor/rules/project.mdc",
            "windsurf": ".windsurfrules",
        }

        assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 0

        for host, target in targets.items():
            content = (tmp_path / target).read_text(encoding="utf-8")
            marker, state, _detail = cc._adapter_marker_payload(content)
            assert state == "owned"
            assert marker["target"] == target
            assert marker["host"] == host
            assert marker["source"] == "CONTROLCODING.md"

    def test_context_sync_global_preflight_blocks_every_write_for_foreign_target(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        foreign = tmp_path / "GEMINI.md"
        foreign.write_text("user-authored rules\n", encoding="utf-8")

        assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1

        assert foreign.read_text(encoding="utf-8") == "user-authored rules\n"
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()
        assert not (tmp_path / ".cursor").exists()

    def test_context_adopt_is_separate_preview_first_operation(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        target = tmp_path / "AGENTS.md"
        target.write_text("historical unmarked adapter\n", encoding="utf-8")

        assert cc.cmd_context_adopt(tmp_path, host="codex_cli") == 0
        assert target.read_text(encoding="utf-8") == "historical unmarked adapter\n"
        assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 0

        content = target.read_text(encoding="utf-8")
        assert "historical unmarked adapter" in content
        marker, state, _detail = cc._adapter_marker_payload(content)
        assert state == "owned"
        assert marker["format"] == "host-context-section-export-v1"

    def test_context_sync_rejects_invalid_and_ambiguous_markers(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        target = tmp_path / "AGENTS.md"
        target.write_text("<!-- controlcoding-managed: not-json -->\n", encoding="utf-8")
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
        assert target.read_text(encoding="utf-8") == "<!-- controlcoding-managed: not-json -->\n"

        target.write_text(
            "<!-- controlcoding-managed: {} -->\n"
            + ("ordinary adapter content\n" * 20)
            + "<!-- controlcoding-managed: {} -->\n",
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1

    def test_direct_export_force_rejects_circular_declared_marker_source(self, tmp_path):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        target = tmp_path / "AGENTS.md"
        circular = (
            cc._adapter_marker_line(
                "AGENTS.md",
                "codex_cli",
                "AGENTS.md",
                "agents-md-portable-v1",
            )
            + "\ncircular managed-looking content\n"
        )
        target.write_text(circular, encoding="utf-8")

        marker, state, detail = cc._adapter_marker_payload(circular)
        assert marker is None
        assert state == "invalid"
        assert "different files" in detail
        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        assert target.read_text(encoding="utf-8") == circular

    def test_direct_export_force_rejects_case_alias_self_binding(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        target = tmp_path / "AGENTS.md"
        case_alias = (
            "# AGENTS.md\n\n"
            + cc._adapter_marker_line(
                "AGENTS.md",
                "codex_cli",
                "agents.md",
                "agents-md-portable-v1",
            )
            + "\n\ncase-alias managed-looking content\n"
        )
        target.write_text(case_alias, encoding="utf-8")
        platform_normcase = cc.os.path.normcase
        monkeypatch.setattr(
            cc.os.path,
            "normcase",
            lambda value: platform_normcase(value).casefold(),
        )

        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        assert target.read_text(encoding="utf-8") == case_alias
        plan = cc._agents_md_export_plan(tmp_path, force=True)
        assert plan["entries"][0]["state"] == "invalid"
        assert plan["entries"][0]["bindingAllowed"] is False

    def test_direct_export_force_rejects_hardlink_self_binding(self, tmp_path):
        target = tmp_path / "AGENTS.md"
        hardlink_marker = (
            "# AGENTS.md\n\n"
            + cc._adapter_marker_line(
                "AGENTS.md",
                "codex_cli",
                "CONTROLCODING.md",
                "agents-md-portable-v1",
            )
            + "\n\nhardlink managed-looking content\n"
        )
        target.write_text(hardlink_marker, encoding="utf-8")
        canonical = tmp_path / "CONTROLCODING.md"
        try:
            os.link(target, canonical)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"hardlinks are unavailable on this filesystem: {exc}")

        assert os.path.samefile(target, canonical)
        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        assert target.read_text(encoding="utf-8") == hardlink_marker
        plan = cc._agents_md_export_plan(tmp_path, force=True)
        assert plan["entries"][0]["state"] == "invalid"
        assert plan["entries"][0]["bindingAllowed"] is False

    def test_direct_export_force_rejects_proposed_source_hardlink_to_target(self, tmp_path):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical.write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        target = tmp_path / "AGENTS.md"
        target_before = target.read_bytes()
        source_alias = tmp_path / "source-alias.md"
        try:
            os.link(target, source_alias)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"hardlinks are unavailable on this filesystem: {exc}")

        plan = cc._agents_md_export_plan(
            tmp_path,
            force=True,
            source="source-alias.md",
        )

        assert plan["preflightOk"] is False
        assert plan["entries"][0]["action"] == "block"
        assert plan["entries"][0]["state"] == "invalid"
        assert "same filesystem file" in plan["entries"][0]["detail"]
        assert cc.cmd_export_agents_md(
            tmp_path,
            force=True,
            source="source-alias.md",
        ) == 1
        assert target.read_bytes() == target_before

    def test_force_rejects_mismatched_declared_source_before_staging(
        self,
        tmp_path,
        monkeypatch,
    ):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical.write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        target = tmp_path / "AGENTS.md"
        owned_with_old_source = (
            "# AGENTS.md\n\n"
            + cc._adapter_marker_line(
                "AGENTS.md",
                "codex_cli",
                "old-source.md",
                "agents-md-portable-v1",
            )
            + "\n\nowned content with an old source binding\n"
        )
        target.write_text(owned_with_old_source, encoding="utf-8")
        plan = cc._agents_md_export_plan(tmp_path, force=True)
        assert plan["preflightOk"] is False
        assert plan["entries"][0]["state"] == "invalid"
        assert plan["entries"][0]["action"] == "block"

        def unexpected_stage(*args, **kwargs):
            raise AssertionError("staging started for an invalid adapter binding")

        monkeypatch.setattr(cc, "_stage_transaction_bytes", unexpected_stage)
        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        assert target.read_text(encoding="utf-8") == owned_with_old_source

    def test_force_rejects_proposed_source_rebind_before_staging(
        self,
        tmp_path,
        monkeypatch,
    ):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical.write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        target = tmp_path / "AGENTS.md"
        target_before = target.read_bytes()
        proposed_source = tmp_path / "foo.md"
        proposed_source.write_bytes(target_before)
        plan = cc._agents_md_export_plan(
            tmp_path,
            force=True,
            source="foo.md",
        )
        assert plan["preflightOk"] is False
        assert plan["entries"][0]["state"] == "invalid"
        assert plan["entries"][0]["action"] == "block"

        def unexpected_stage(*args, **kwargs):
            raise AssertionError("staging started for a source binding mismatch")

        monkeypatch.setattr(cc, "_stage_transaction_bytes", unexpected_stage)
        assert cc.cmd_export_agents_md(tmp_path, force=True, source="foo.md") == 1
        assert target.read_bytes() == target_before

    @pytest.mark.parametrize("explicit_source", [False, True])
    def test_generated_output_with_embedded_marker_is_blocked_before_write(
        self,
        tmp_path,
        explicit_source,
    ):
        source_name = "SOURCE.md" if explicit_source else "CONTROLCODING.md"
        embedded_marker = cc._adapter_marker_line(
            "UNRELATED.md",
            "codex_cli",
            source_name,
            "agents-md-portable-v1",
        )
        source_text = (
            "# Context\n\n"
            "## Project Identity\n\n"
            f"{embedded_marker}\n\n"
            "- Name: Embedded marker fixture\n\n"
            "## Architecture Rules\n\n"
            "1. Keep generated output deterministic.\n"
        )
        (tmp_path / source_name).write_text(source_text, encoding="utf-8")
        source_arg = source_name if explicit_source else ""

        plan = cc._agents_md_export_plan(
            tmp_path,
            force=True,
            source=source_arg,
        )

        assert plan["preflightOk"] is False
        assert plan["entries"][0]["action"] == "block"
        assert plan["entries"][0]["state"] == "ambiguous"
        assert "multiple ControlCoding ownership markers" in plan["entries"][0]["detail"]
        assert cc.cmd_export_agents_md(
            tmp_path,
            force=True,
            source=source_arg,
        ) == 1
        assert not (tmp_path / "AGENTS.md").exists()

    def test_direct_export_force_rejects_broken_symlink_declared_source(self, tmp_path):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        declared_source = tmp_path / "declared-source.md"
        try:
            declared_source.symlink_to("missing-declared-source.md")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks are unavailable on this filesystem: {exc}")
        target = tmp_path / "AGENTS.md"
        unsafe_marker = (
            "# AGENTS.md\n\n"
            + cc._adapter_marker_line(
                "AGENTS.md",
                "codex_cli",
                "declared-source.md",
                "agents-md-portable-v1",
            )
            + "\n\nunsafe managed-looking content\n"
        )
        target.write_text(unsafe_marker, encoding="utf-8")

        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        assert target.read_text(encoding="utf-8") == unsafe_marker
        plan = cc._agents_md_export_plan(tmp_path, force=True)
        assert plan["entries"][0]["state"] == "unreadable_or_unsafe"
        assert plan["entries"][0]["bindingAllowed"] is False
        assert "declared marker source path is unsafe" in plan["entries"][0]["detail"]

    @pytest.mark.parametrize(
        ("target_binding", "host_binding", "format_binding"),
        [
            ("GEMINI.md", "codex_cli", "host-context-section-export-v1"),
            ("AGENTS.md", "gemini_cli", "host-context-section-export-v1"),
            ("AGENTS.md", "codex_cli", "host-context-full-copy-v1"),
        ],
    )
    def test_wrong_marker_binding_blocks_even_direct_export_force(
        self,
        tmp_path,
        target_binding,
        host_binding,
        format_binding,
    ):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        target = tmp_path / "AGENTS.md"
        original = (
            "# AGENTS.md\n\n"
            + cc._adapter_marker_line(
                target_binding,
                host_binding,
                "CONTROLCODING.md",
                format_binding,
            )
            + "\n\nmanaged-looking content\n"
        )
        target.write_text(original, encoding="utf-8")

        assert cc.cmd_export_host_context(
            tmp_path,
            host="codex_cli",
            force=True,
        ) == 1
        assert target.read_text(encoding="utf-8") == original
        plan = cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="sync")
        assert plan["preflightOk"] is False
        assert plan["entries"][0]["bindingAllowed"] is False
        assert plan["entries"][0]["remediation"]

    @pytest.mark.parametrize(
        "marker_update",
        [
            {"version": True},
            {"target": "../AGENTS.md"},
            {"source": "C:/outside/CONTROLCODING.md"},
        ],
    )
    def test_marker_rejects_boolean_version_and_non_relative_paths(self, marker_update):
        marker = {
            "owner": "ControlCoding",
            "schema": "controlcoding.host-adapter-ownership",
            "version": 1,
            "target": "AGENTS.md",
            "host": "codex_cli",
            "source": "CONTROLCODING.md",
            "format": "host-context-section-export-v1",
        }
        marker.update(marker_update)
        content = (
            "<!-- controlcoding-managed: "
            + json.dumps(marker, sort_keys=True, separators=(",", ":"))
            + " -->\n"
        )

        parsed, state, detail = cc._adapter_marker_payload(content)

        assert parsed is None
        assert state == "invalid"
        assert detail

    def test_context_sync_classifies_directory_and_unreadable_targets(self, tmp_path, monkeypatch):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        target = tmp_path / "AGENTS.md"
        target.mkdir()

        directory_plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
        assert directory_plan["preflightOk"] is False
        assert directory_plan["entries"][0]["state"] == "unreadable_or_unsafe"

        target.rmdir()
        target.write_text("unmarked\n", encoding="utf-8")
        original_snapshot = cc._identity_safe_file_snapshot

        def unreadable_selected_path(project, path, purpose, expected_parents=None):
            if path == target:
                raise PermissionError("injected unreadable target")
            return original_snapshot(
                project,
                path,
                purpose,
                expected_parents=expected_parents,
            )

        monkeypatch.setattr(cc, "_identity_safe_file_snapshot", unreadable_selected_path)
        unreadable_plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
        assert unreadable_plan["preflightOk"] is False
        assert unreadable_plan["entries"][0]["state"] == "unreadable_or_unsafe"

    def test_context_sync_rolls_back_on_second_replace_failure(self, tmp_path, monkeypatch):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        original_replace = cc.os.replace
        stage_replaces = {"count": 0}

        def fail_second_stage_replace(source, target):
            if str(source).endswith(".stage"):
                stage_replaces["count"] += 1
                if stage_replaces["count"] == 2:
                    raise OSError("injected adapter replacement failure")
            return original_replace(source, target)

        monkeypatch.setattr(cc.os, "replace", fail_second_stage_replace)

        assert cc.cmd_context_sync(tmp_path, all_hosts=True) == 1
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()
        assert not (tmp_path / "GEMINI.md").exists()
        assert not (tmp_path / ".clinerules").exists()
        assert not (tmp_path / ".cursor").exists()
        assert not (tmp_path / ".windsurfrules").exists()

    def test_adapter_source_text_and_hash_use_one_byte_snapshot(self, tmp_path, monkeypatch):
        canonical = tmp_path / "CONTROLCODING.md"
        original_bytes = self.SAMPLE_CLAUDE_MD.replace(
            "CLAUDE.md", "CONTROLCODING.md"
        ).encode("utf-8")
        canonical.write_bytes(original_bytes)
        original_snapshot = cc._identity_safe_file_snapshot
        source_reads = {"count": 0}

        def count_source_snapshot(project, path, purpose, expected_parents=None):
            if path == canonical:
                source_reads["count"] += 1
            return original_snapshot(
                project,
                path,
                purpose,
                expected_parents=expected_parents,
            )

        monkeypatch.setattr(cc, "_identity_safe_file_snapshot", count_source_snapshot)

        plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")

        assert source_reads["count"] == 1
        assert "Python 3.12" in plan["entries"][0]["_expectedContent"]
        assert plan["entries"][0]["_sourceHash"] == cc.hashlib.sha256(original_bytes).hexdigest()

    def test_transaction_revalidates_after_staging_before_first_replace(self, tmp_path, monkeypatch):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical_text = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        canonical.write_text(canonical_text, encoding="utf-8")
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        canonical.write_text(
            canonical_text.replace("Python 3.12", "Python 3.13"),
            encoding="utf-8",
        )
        target = tmp_path / "AGENTS.md"
        original_stage = cc._stage_transaction_bytes
        injected = {"done": False}

        def stage_and_inject(target_path, content, suffix, mode=None, **kwargs):
            staged = original_stage(
                target_path,
                content,
                suffix,
                mode=mode,
                **kwargs,
            )
            if Path(target_path) == target and suffix == ".stage" and not injected["done"]:
                injected["done"] = True
                target.write_text("concurrent user change\n", encoding="utf-8")
            return staged

        monkeypatch.setattr(cc, "_stage_transaction_bytes", stage_and_inject)

        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 1
        assert target.read_text(encoding="utf-8") == "concurrent user change\n"

    def test_transaction_rejects_replaced_stage_before_target_write(self, tmp_path, monkeypatch):
        target = tmp_path / "existing.txt"
        target.write_text("original content\n", encoding="utf-8")
        entry = cc._text_write_entry(tmp_path, target, "transaction content\n")
        original_stage = cc._stage_transaction_bytes
        retired_stage = tmp_path / "retired-original-stage"
        injected = {"done": False}

        def replace_stage_identity(target_path, content, suffix, mode=None, **kwargs):
            record = original_stage(
                target_path,
                content,
                suffix,
                mode=mode,
                **kwargs,
            )
            if suffix == ".stage" and not injected["done"]:
                stage_path = Path(record["path"])
                original_key = cc._transaction_file_object_key(os.lstat(stage_path))
                stage_path.rename(retired_stage)
                stage_path.write_bytes(content)
                stage_path.chmod(record["mode"])
                replacement_key = cc._transaction_file_object_key(os.lstat(stage_path))
                injected.update({
                    "done": True,
                    "record": record,
                    "stage_path": stage_path,
                    "original_key": original_key,
                    "replacement_key": replacement_key,
                })
            return record

        monkeypatch.setattr(cc, "_stage_transaction_bytes", replace_stage_identity)

        result = cc._apply_text_transaction(tmp_path, [entry])

        assert injected["done"] is True
        assert injected["stage_path"] != retired_stage
        original_key = tuple(injected["record"]["objectKey"])
        assert injected["original_key"] == original_key
        assert cc._transaction_file_object_key(os.lstat(retired_stage)) == original_key
        assert cc._transaction_file_object_key(
            os.lstat(injected["stage_path"])
        ) == injected["replacement_key"]
        assert injected["replacement_key"] != original_key
        assert result["ok"] is False
        assert result["attempted"] is False
        assert result["written"] == []
        assert "stage" in result["error"]
        assert "identity changed" in result["error"]
        assert target.read_text(encoding="utf-8") == "original content\n"

    def test_stage_exception_cleanup_preserves_replaced_reserved_path(self, tmp_path):
        reserved_path = tmp_path / ".target.controlcoding.reserved.stage"
        reserved_path.write_text("reserved stage\n", encoding="utf-8")
        reserved_identity = cc._transaction_file_object_key(os.lstat(reserved_path))
        reserved_path.rename(tmp_path / "retired-reserved-stage")
        reserved_path.write_text("concurrent replacement\n", encoding="utf-8")

        cleanup_error = cc._cleanup_reserved_transaction_path(
            reserved_path,
            reserved_identity,
        )

        assert "identity changed" in cleanup_error
        assert reserved_path.read_text(encoding="utf-8") == "concurrent replacement\n"

    def test_transaction_rejects_corrupted_backup_before_first_target_replace(
        self,
        tmp_path,
        monkeypatch,
    ):
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("first original\n", encoding="utf-8")
        second.write_text("second original\n", encoding="utf-8")
        entries = [
            cc._text_write_entry(tmp_path, first, "first transaction\n"),
            cc._text_write_entry(tmp_path, second, "second transaction\n"),
        ]
        original_stage = cc._stage_transaction_bytes
        backup_records = []

        def corrupt_first_backup_after_staging(target_path, content, suffix, mode=None, **kwargs):
            record = original_stage(
                target_path,
                content,
                suffix,
                mode=mode,
                **kwargs,
            )
            if suffix == ".backup":
                backup_records.append(record)
                if len(backup_records) == 2:
                    Path(backup_records[0]["path"]).write_bytes(b"corrupted backup\n")
            return record

        monkeypatch.setattr(
            cc,
            "_stage_transaction_bytes",
            corrupt_first_backup_after_staging,
        )

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        assert result["attempted"] is False
        assert result["written"] == []
        assert "rollback backup for first.txt" in result["error"]
        assert first.read_text(encoding="utf-8") == "first original\n"
        assert second.read_text(encoding="utf-8") == "second original\n"

    def test_transaction_revalidates_before_creating_any_stage(self, tmp_path, monkeypatch):
        target = tmp_path / "new.txt"
        entry = cc._text_write_entry(tmp_path, target, "transaction content\n")
        target.write_text("concurrent content\n", encoding="utf-8")

        def unexpected_stage(*args, **kwargs):
            raise AssertionError("staging started before global revalidation")

        monkeypatch.setattr(cc, "_stage_transaction_bytes", unexpected_stage)

        result = cc._apply_text_transaction(tmp_path, [entry])

        assert result["ok"] is False
        assert result["attempted"] is False
        assert result["written"] == []
        assert "target identity or state changed after preview" in result["error"]
        assert target.read_text(encoding="utf-8") == "concurrent content\n"

    def test_concurrently_created_parent_is_not_removed_during_cleanup(self, tmp_path, monkeypatch):
        parent = tmp_path / "concurrent-parent"
        target = parent / "created.txt"
        entry = cc._text_write_entry(tmp_path, target, "transaction content\n")
        original_mkdir = type(parent).mkdir
        injected = {"done": False}

        def concurrent_mkdir(path, mode=0o777, parents=False, exist_ok=False):
            if path == parent and not injected["done"]:
                injected["done"] = True
                original_mkdir(path, mode=mode, parents=parents, exist_ok=False)
                raise FileExistsError("injected concurrent parent creation")
            return original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

        original_replace = cc.os.replace

        def fail_stage_replace(source, destination):
            if str(source).endswith(".stage"):
                raise OSError("injected replacement failure")
            return original_replace(source, destination)

        monkeypatch.setattr(type(parent), "mkdir", concurrent_mkdir)
        monkeypatch.setattr(cc.os, "replace", fail_stage_replace)

        result = cc._apply_text_transaction(tmp_path, [entry])

        assert result["ok"] is False
        assert parent.is_dir()
        assert list(parent.iterdir()) == []

    def test_nested_parent_failure_cleans_only_transaction_created_levels(
        self,
        tmp_path,
        monkeypatch,
    ):
        preexisting_parent = tmp_path / "preexisting"
        preexisting_parent.mkdir()
        created_outer = preexisting_parent / "transaction-outer"
        failing_inner = created_outer / "transaction-inner"
        target = failing_inner / "created.txt"
        entry = cc._text_write_entry(tmp_path, target, "transaction content\n")
        original_mkdir = type(preexisting_parent).mkdir

        def fail_after_outer_creation(path, mode=0o777, parents=False, exist_ok=False):
            if path == failing_inner:
                raise OSError("injected nested parent failure")
            return original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

        monkeypatch.setattr(type(preexisting_parent), "mkdir", fail_after_outer_creation)

        result = cc._apply_text_transaction(tmp_path, [entry])

        assert result["ok"] is False
        assert result["attempted"] is False
        assert preexisting_parent.is_dir()
        assert not created_outer.exists()
        assert not failing_inner.exists()

    def test_recreated_empty_parent_is_preserved_during_transaction_cleanup(
        self,
        tmp_path,
        monkeypatch,
    ):
        parent = tmp_path / "transaction-parent"
        target = parent / "created.txt"
        entry = cc._text_write_entry(tmp_path, target, "transaction content\n")

        def replace_parent_then_fail(*args, **kwargs):
            parent.rename(tmp_path / "retired-transaction-parent")
            parent.mkdir()
            raise OSError("injected failure after parent replacement")

        monkeypatch.setattr(cc, "_stage_transaction_bytes", replace_parent_then_fail)

        result = cc._apply_text_transaction(tmp_path, [entry])

        assert result["ok"] is False
        assert result["cleanupOk"] is False
        assert "created directory identity changed" in result["error"]
        assert parent.is_dir()
        assert list(parent.iterdir()) == []

    @pytest.mark.skipif(os.name == "nt", reason="POSIX umask semantics")
    def test_transaction_new_file_mode_matches_write_text_umask_semantics(self, tmp_path):
        target = tmp_path / "created.txt"
        entry = cc._text_write_entry(tmp_path, target, "created atomically\n")
        previous_umask = os.umask(0o027)
        try:
            result = cc._apply_text_transaction(tmp_path, [entry])
        finally:
            os.umask(previous_umask)

        assert result["ok"] is True
        assert stat.S_IMODE(target.stat().st_mode) == 0o640

    def test_transaction_preserves_existing_mode_bits(self, tmp_path):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical_text = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        canonical.write_text(canonical_text, encoding="utf-8")
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        target = tmp_path / "AGENTS.md"
        target.chmod(0o640)
        original_mode = stat.S_IMODE(target.stat().st_mode)
        canonical.write_text(
            canonical_text.replace("Python 3.12", "Python 3.13"),
            encoding="utf-8",
        )

        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        assert stat.S_IMODE(target.stat().st_mode) == original_mode

    def test_rollback_preserves_concurrent_content_and_reports_incomplete(self, tmp_path, monkeypatch):
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("first original\n", encoding="utf-8")
        second.write_text("second original\n", encoding="utf-8")
        entries = [
            cc._text_write_entry(tmp_path, first, "first transaction\n"),
            cc._text_write_entry(tmp_path, second, "second transaction\n"),
        ]
        original_replace = cc.os.replace
        stage_replaces = {"count": 0}

        def fail_after_concurrent_change(source, target):
            if str(source).endswith(".stage"):
                stage_replaces["count"] += 1
                if stage_replaces["count"] == 2:
                    first.write_text("concurrent user change\n", encoding="utf-8")
                    raise OSError("injected second replacement failure")
            return original_replace(source, target)

        monkeypatch.setattr(cc.os, "replace", fail_after_concurrent_change)

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        assert result["applied"] is True
        assert result["rollbackOk"] is False
        assert result["rollbackErrors"]
        assert first.read_text(encoding="utf-8") == "concurrent user change\n"
        assert second.read_text(encoding="utf-8") == "second original\n"

    @pytest.mark.parametrize("first_original", [None, "first original\n"])
    def test_rollback_recognizes_only_identity_safe_restored_state(
        self,
        tmp_path,
        monkeypatch,
        first_original,
    ):
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        if first_original is not None:
            first.write_text(first_original, encoding="utf-8")
        entries = [
            cc._text_write_entry(tmp_path, first, "first transaction\n"),
            cc._text_write_entry(tmp_path, second, "second transaction\n"),
        ]
        original_replace = cc.os.replace
        stage_replaces = {"count": 0}

        def restore_first_before_second_failure(source, target):
            if str(source).endswith(".stage"):
                stage_replaces["count"] += 1
                if stage_replaces["count"] == 2:
                    if first_original is None:
                        first.unlink()
                    else:
                        first.write_text(first_original, encoding="utf-8")
                    raise OSError("injected second replacement failure")
            return original_replace(source, target)

        monkeypatch.setattr(cc.os, "replace", restore_first_before_second_failure)

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        if first_original is None:
            assert result["applied"] is False
            assert result["rolledBack"] is True
            assert result["rollbackOk"] is True
            assert result["written"] == []
            assert not first.exists()
        else:
            assert result["applied"] is True
            assert result["rolledBack"] is False
            assert result["rollbackOk"] is False
            assert result["written"] == ["first.txt"]
            assert first.read_text(encoding="utf-8") == first_original
        assert not second.exists()

    def test_final_rollback_verification_rejects_late_drift(self, tmp_path, monkeypatch):
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        third = tmp_path / "third.txt"
        for target, content in (
            (first, "first original\n"),
            (second, "second original\n"),
            (third, "third original\n"),
        ):
            target.write_text(content, encoding="utf-8")
        entries = [
            cc._text_write_entry(tmp_path, first, "first transaction\n"),
            cc._text_write_entry(tmp_path, second, "second transaction\n"),
            cc._text_write_entry(tmp_path, third, "third transaction\n"),
        ]
        original_replace = cc.os.replace
        stage_replaces = {"count": 0}

        def fail_third_and_drift_second_during_first_restore(source, target):
            if str(source).endswith(".stage"):
                stage_replaces["count"] += 1
                if stage_replaces["count"] == 3:
                    raise OSError("injected third replacement failure")
            result = original_replace(source, target)
            if str(source).endswith(".backup") and Path(target) == first:
                second.write_text("late rollback drift\n", encoding="utf-8")
            return result

        monkeypatch.setattr(
            cc.os,
            "replace",
            fail_third_and_drift_second_during_first_restore,
        )

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        assert result["rollbackOk"] is False
        assert result["rolledBack"] is False
        assert result["written"] == ["second.txt"]
        assert first.read_text(encoding="utf-8") == "first original\n"
        assert second.read_text(encoding="utf-8") == "late rollback drift\n"
        assert third.read_text(encoding="utf-8") == "third original\n"

    def test_final_set_verification_rejects_drift_in_earlier_committed_target(
        self,
        tmp_path,
        monkeypatch,
    ):
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("first original\n", encoding="utf-8")
        second.write_text("second original\n", encoding="utf-8")
        entries = [
            cc._text_write_entry(tmp_path, first, "first transaction\n"),
            cc._text_write_entry(tmp_path, second, "second transaction\n"),
        ]
        original_replace = cc.os.replace
        stage_replaces = {"count": 0}

        def mutate_first_after_second_replace(source, target):
            result = original_replace(source, target)
            if str(source).endswith(".stage"):
                stage_replaces["count"] += 1
                if stage_replaces["count"] == 2:
                    first.write_text("late concurrent change\n", encoding="utf-8")
            return result

        monkeypatch.setattr(cc.os, "replace", mutate_first_after_second_replace)

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        assert result["rollbackOk"] is False
        assert result["written"] == ["first.txt"]
        assert first.read_text(encoding="utf-8") == "late concurrent change\n"
        assert second.read_text(encoding="utf-8") == "second original\n"

    def test_final_set_verification_rejects_adapter_source_drift(self, tmp_path, monkeypatch):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical.write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
        original_replace = cc.os.replace

        def mutate_source_after_adapter_replace(source, target):
            result = original_replace(source, target)
            if str(source).endswith(".stage"):
                canonical.write_text("late concurrent source change\n", encoding="utf-8")
            return result

        monkeypatch.setattr(cc.os, "replace", mutate_source_after_adapter_replace)

        result = cc._apply_text_transaction(tmp_path, plan["entries"])

        assert result["ok"] is False
        assert result["rollbackOk"] is True
        assert result["written"] == []
        assert canonical.read_text(encoding="utf-8") == "late concurrent source change\n"
        assert not (tmp_path / "AGENTS.md").exists()

    def test_final_set_verification_rejects_noop_target_drift(self, tmp_path, monkeypatch):
        candidate = tmp_path / "candidate.txt"
        noop_target = tmp_path / "noop.txt"
        candidate.write_bytes(b"candidate original\n")
        noop_target.write_bytes(b"noop original\n")
        entries = [
            cc._text_write_entry(tmp_path, candidate, "candidate transaction\n"),
            cc._text_write_entry(tmp_path, noop_target, "noop original\n"),
        ]
        assert entries[1]["action"] == "noop"
        original_replace = cc.os.replace

        def mutate_noop_after_candidate_replace(source, target):
            result = original_replace(source, target)
            if str(source).endswith(".stage") and Path(target) == candidate:
                noop_target.write_bytes(b"late concurrent noop change\n")
            return result

        monkeypatch.setattr(cc.os, "replace", mutate_noop_after_candidate_replace)

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        assert result["rollbackOk"] is True
        assert result["written"] == []
        assert candidate.read_text(encoding="utf-8") == "candidate original\n"
        assert noop_target.read_text(encoding="utf-8") == "late concurrent noop change\n"

    def test_all_noop_transaction_rejects_target_drift_after_preview(self, tmp_path):
        target = tmp_path / "noop.txt"
        target.write_bytes(b"preview content\n")
        entry = cc._text_write_entry(tmp_path, target, "preview content\n")
        assert entry["action"] == "noop"
        target.write_bytes(b"concurrent target change\n")

        result = cc._apply_text_transaction(tmp_path, [entry])

        assert result["ok"] is False
        assert result["attempted"] is False
        assert result["written"] == []
        assert target.read_text(encoding="utf-8") == "concurrent target change\n"

    def test_all_noop_adapter_transaction_rejects_source_drift_after_preview(self, tmp_path):
        canonical = tmp_path / "CONTROLCODING.md"
        canonical.write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        plan = cc._adapter_plan_payload(tmp_path, host="codex_cli")
        assert [entry["action"] for entry in plan["entries"]] == ["noop"]
        target = tmp_path / "AGENTS.md"
        target_before = target.read_bytes()
        canonical.write_text("concurrent source change\n", encoding="utf-8")

        result = cc._apply_text_transaction(tmp_path, plan["entries"])

        assert result["ok"] is False
        assert result["attempted"] is False
        assert result["written"] == []
        assert target.read_bytes() == target_before
        assert canonical.read_text(encoding="utf-8") == "concurrent source change\n"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit semantics")
    def test_rollback_preserves_mode_only_race_and_reports_only_unrestored_target(
        self,
        tmp_path,
        monkeypatch,
    ):
        targets = [tmp_path / name for name in ("first.txt", "second.txt", "third.txt")]
        for index, target in enumerate(targets, start=1):
            target.write_text(f"original {index}\n", encoding="utf-8")
        entries = [
            cc._text_write_entry(tmp_path, target, f"transaction {index}\n")
            for index, target in enumerate(targets, start=1)
        ]
        original_replace = cc.os.replace
        stage_replaces = {"count": 0}
        concurrent_mode = {"value": None}

        def fail_third_after_mode_change(source, target):
            if str(source).endswith(".stage"):
                stage_replaces["count"] += 1
                if stage_replaces["count"] == 3:
                    first_mode = stat.S_IMODE(targets[0].stat().st_mode)
                    concurrent_mode["value"] = first_mode ^ stat.S_IXUSR
                    targets[0].chmod(concurrent_mode["value"])
                    raise OSError("injected third replacement failure")
            return original_replace(source, target)

        monkeypatch.setattr(cc.os, "replace", fail_third_after_mode_change)

        result = cc._apply_text_transaction(tmp_path, entries)

        assert result["ok"] is False
        assert result["rollbackOk"] is False
        assert result["written"] == ["first.txt"]
        assert targets[0].read_text(encoding="utf-8") == "transaction 1\n"
        assert stat.S_IMODE(targets[0].stat().st_mode) == concurrent_mode["value"]
        assert targets[1].read_text(encoding="utf-8") == "original 2\n"
        assert targets[2].read_text(encoding="utf-8") == "original 3\n"

    def test_preview_flushes_missing_section_warnings_before_apply(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            "# Minimal\n\n## Project Identity\n\n- **Name**: Minimal\n",
            encoding="utf-8",
        )

        class FlushRecorder(io.StringIO):
            flushed = False

            def flush(self):
                self.flushed = True
                super().flush()

        recorder = FlushRecorder()
        monkeypatch.setattr(cc.sys, "stdout", recorder)

        def assert_preview_before_apply(project, entries):
            assert recorder.flushed is True
            assert "Warning: source section 'Architecture Rules' is missing" in recorder.getvalue()
            return {
                "ok": True,
                "applied": False,
                "rollbackOk": True,
                "written": [],
            }

        monkeypatch.setattr(cc, "_apply_text_transaction", assert_preview_before_apply)

        assert cc.cmd_export_agents_md(tmp_path) == 0

    def test_context_drift_accepts_synced_context_with_zones_and_invariants(self, tmp_path, capsys):
        canonical = (
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
            + "\n- Active invariant id: `ledger-balanced`\n"
        )
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {"protected_zones": [{"path": "core/", "level": "deny"}]},
                indent=2,
            ),
            encoding="utf-8",
        )
        (tmp_path / "controlcoding.invariants.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "projectType": "test",
                    "domains": ["finance"],
                    "invariants": [
                        {
                            "id": "ledger-balanced",
                            "title": "Ledger Balanced",
                            "domain": "finance",
                            "kind": "domain",
                            "severity": "blocking",
                            "status": "active",
                            "property": "Debits and credits remain balanced.",
                            "threshold": "0 drift",
                            "command": "python -c \"print('ok')\"",
                            "evidence": ["tests"],
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        capsys.readouterr()

        result = cc.cmd_context_drift(tmp_path, host="codex_cli", json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["activeInvariantIds"] == ["ledger-balanced"]
        assert payload["protectedZones"][0]["path"] == "core/"

    def test_context_drift_reports_stale_generated_host_file(self, tmp_path, capsys):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        capsys.readouterr()
        (tmp_path / "AGENTS.md").write_text("# stale\n", encoding="utf-8")

        result = cc.cmd_context_drift(tmp_path, host="codex_cli", json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert any(finding["check"] == "context_sync" for finding in payload["findings"])

    def test_context_drift_reports_missing_protected_zone_reference(self, tmp_path, capsys):
        canonical = self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md")
        (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps({"protected_zones": [{"path": "restricted/", "level": "deny"}]}),
            encoding="utf-8",
        )
        assert cc.cmd_context_sync(tmp_path, host="codex_cli") == 0
        capsys.readouterr()

        result = cc.cmd_context_drift(tmp_path, host="codex_cli", json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert any(
            finding["check"] == "protected_zone_reference"
            for finding in payload["findings"]
        )


class TestTruthCommands:
    @staticmethod
    def _write_truth_docs_fixture(project, readme, quick_start="", install=""):
        docs_dir = project / "docs"
        docs_dir.mkdir()
        (project / "README.md").write_text(readme, encoding="utf-8")
        (docs_dir / "quick-start.md").write_text(quick_start, encoding="utf-8")
        (docs_dir / "install-controlcoding-on-your-project.md").write_text(
            install,
            encoding="utf-8",
        )

    def test_truth_report_json_lists_core_capabilities(self, tmp_path, capsys):
        result = cc.cmd_truth_report(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        by_id = {entry["id"]: entry for entry in payload["capabilities"]}
        assert payload["registrySource"] == "builtin"
        assert by_id["context_sync"]["state"] == "shipped"
        assert by_id["context_sync"]["control_level"] == "mechanical"
        assert by_id["verification_contract"]["state"] == "shipped"
        assert by_id["first_class_invariants"]["state"] == "shipped"
        assert by_id["promotion_path"]["state"] == "experimental"
        assert by_id["feature_state_machine"]["state"] == "experimental"
        assert by_id["constitution_drift"]["state"] == "shipped"
        assert "feature verify" in by_id["feature_state_machine"]["commands"]
        assert "promote check" in by_id["promotion_path"]["commands"]

    def test_truth_check_passes_builtin_registry(self, tmp_path, capsys):
        result = cc.cmd_truth_check(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["findings"] == []

    def test_truth_route_registry_includes_top_level_operational_commands(self):
        assert cc._truth_route_exists(("install",))
        assert cc._truth_route_exists(("resume",))

    def test_truth_extract_route_handles_top_level_positional_commands(self):
        assert cc._truth_extract_route_from_tokens(
            shlex.split("python scripts/cc.py init-module my-module")
        ) == ("init-module",)
        assert cc._truth_extract_route_from_tokens(
            shlex.split("python scripts/cc.py init-module my-module --dir src/packages/my-module")
        ) == ("init-module",)
        assert cc._truth_extract_route_from_tokens(
            shlex.split("python scripts/cc.py install vision")
        ) == ("install",)
        assert cc._truth_extract_route_from_tokens(
            shlex.split("cc resume --brief")
        ) == ("resume",)

    def test_truth_extract_route_preserves_subcommand_validation(self):
        assert cc._truth_extract_route_from_tokens(
            shlex.split("python scripts/cc.py truth check --project-root .")
        ) == ("truth", "check")
        assert cc._truth_extract_route_from_tokens(
            shlex.split("cc ghost command")
        ) == ("ghost", "command")
        assert cc._truth_route_exists(("ghost", "command")) is False
        assert cc._truth_extract_route_from_tokens(
            shlex.split("cc truth ghost")
        ) == ("truth", "ghost")
        assert cc._truth_route_exists(("truth", "ghost")) is False

    def test_truth_check_docs_validates_documented_commands(self, tmp_path, capsys):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (tmp_path / "README.md").write_text(
            "Run `cc doctor` and `cc context sync --host codex_cli`.\n",
            encoding="utf-8",
        )
        (docs_dir / "quick-start.md").write_text(
            "```bash\npython scripts/cc.py truth check --include-docs\n```\n",
            encoding="utf-8",
        )
        (docs_dir / "install-controlcoding-on-your-project.md").write_text(
            "Use `cc export host-context --host <host>`.\n",
            encoding="utf-8",
        )

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["commandCount"] == 4
        assert payload["controlClaimCount"] == 0
        assert {tuple(entry["route"]) for entry in payload["commands"]} == {
            ("doctor",),
            ("context", "sync"),
            ("truth", "check"),
            ("export", "host-context"),
        }

    @pytest.mark.parametrize(
        "reference",
        [
            "cc replace start/status/complete",
            "cc retired-surface inspect",
        ],
    )
    def test_truth_check_docs_excludes_explicit_non_public_inline_reference(
        self,
        tmp_path,
        capsys,
        reference,
    ):
        self._write_truth_docs_fixture(
            tmp_path,
            f"- `{reference}` is no longer a public command surface.\n",
        )

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["commandCount"] == 0
        assert payload["commands"] == []
        assert payload["findings"] == []

    @pytest.mark.parametrize(
        ("readme", "expected_line"),
        [
            (
                "- `cc archived-surface inspect` is no longer a public command surface. "
                "Run `cc ghost command`.\n",
                1,
            ),
            (
                "- `cc archived-surface inspect` is no longer a public command surface.\n"
                "  Run `cc ghost command`.\n",
                2,
            ),
        ],
        ids=["same_line", "same_paragraph"],
    )
    def test_truth_check_docs_keeps_positive_missing_reference_beside_history(
        self,
        tmp_path,
        capsys,
        readme,
        expected_line,
    ):
        self._write_truth_docs_fixture(tmp_path, readme)

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["commandCount"] == 1
        assert [
            (finding["command"], finding["line"], finding["message"])
            for finding in payload["findings"]
        ] == [
            ("cc ghost command", expected_line, "route is not defined in CLI")
        ]

    def test_truth_check_docs_keeps_missing_command_in_executable_fence(
        self,
        tmp_path,
        capsys,
    ):
        self._write_truth_docs_fixture(
            tmp_path,
            "- `cc archived-surface inspect` is no longer a public command surface.\n"
            "\n"
            "```bash\n"
            "cc ghost command\n"
            "```\n",
        )

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["commandCount"] == 1
        assert payload["findings"][0]["command"] == "cc ghost command"
        assert payload["findings"][0]["line"] == 4
        assert payload["findings"][0]["message"] == "route is not defined in CLI"

    @pytest.mark.parametrize(
        "readme",
        [
            "`cc ghost command` will no longer be a public command surface.\n",
            "`cc ghost command` may no longer be a public command surface.\n",
            (
                "It is not true that `cc ghost command` is no longer a public "
                "command surface.\n"
            ),
        ],
        ids=["future", "ambiguous", "negated"],
    )
    def test_truth_check_docs_does_not_exempt_inexact_non_public_claims(
        self,
        tmp_path,
        capsys,
        readme,
    ):
        self._write_truth_docs_fixture(tmp_path, readme)

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["commandCount"] == 1
        assert payload["findings"][0]["command"] == "cc ghost command"
        assert payload["findings"][0]["line"] == 1
        assert payload["findings"][0]["message"] == "route is not defined in CLI"

    def test_truth_check_docs_accepts_bounded_same_item_wrapping(
        self,
        tmp_path,
        capsys,
    ):
        self._write_truth_docs_fixture(
            tmp_path,
            "- `cc archived-surface inspect`\n"
            "  is no longer a public command\n"
            "  surface.\n",
        )

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["commandCount"] == 0
        assert payload["commands"] == []

    @pytest.mark.parametrize(
        "readme",
        [
            "- `cc ghost command`\n- is no longer a public command surface.\n",
            "`cc ghost command`\n\nis no longer a public command surface.\n",
            (
                "- `cc ghost command`\n"
                "```text\n"
                "is no longer a public command surface.\n"
                "```\n"
            ),
        ],
        ids=["next_list_item", "new_paragraph", "code_fence"],
    )
    def test_truth_check_docs_wrapping_does_not_cross_structure_boundaries(
        self,
        tmp_path,
        capsys,
        readme,
    ):
        self._write_truth_docs_fixture(tmp_path, readme)

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["commandCount"] == 1
        assert payload["findings"][0]["command"] == "cc ghost command"
        assert payload["findings"][0]["line"] == 1
        assert payload["findings"][0]["message"] == "route is not defined in CLI"

    def test_public_wizards_do_not_document_rejected_setup_project_apply_command(self):
        repo_root = Path(__file__).resolve().parent.parent
        wizard_paths = [
            repo_root / "INSTALL_WIZARD.md",
            repo_root / "PROJECT_SETUP_WIZARD.md",
        ]

        bad_lines = []
        for path in wizard_paths:
            for line in path.read_text(encoding="utf-8").splitlines():
                if "python " not in line or "setup-project" not in line:
                    continue
                valid_chat_guide = "--chat-guide" in line
                valid_apply = "--answers-file" in line and "--apply-answers" in line
                if not (valid_chat_guide or valid_apply):
                    bad_lines.append(f"{path.name}: {line}")

        assert bad_lines == []

    def test_truth_check_docs_rejects_unknown_documented_command(self, tmp_path, capsys):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (tmp_path / "README.md").write_text("Run `cc ghost command`.\n", encoding="utf-8")
        (docs_dir / "quick-start.md").write_text("", encoding="utf-8")
        (docs_dir / "install-controlcoding-on-your-project.md").write_text("", encoding="utf-8")

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["findings"][0]["command"] == "cc ghost command"

    def test_truth_check_docs_rejects_unqualified_control_claim(self, tmp_path, capsys):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (tmp_path / "README.md").write_text(
            "ControlCoding provides mechanical enforcement for AI work.\n",
            encoding="utf-8",
        )
        (docs_dir / "quick-start.md").write_text("", encoding="utf-8")
        (docs_dir / "install-controlcoding-on-your-project.md").write_text("", encoding="utf-8")

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["controlClaimCount"] == 1
        assert payload["controlClaims"][0]["state"] == "unqualified"
        assert any("strong control claim" in finding["message"] for finding in payload["findings"])

    def test_truth_check_docs_accepts_contextualized_control_claim(self, tmp_path, capsys):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (tmp_path / "README.md").write_text(
            "Boundary enforcement is mechanical through the pre-commit gate.\n",
            encoding="utf-8",
        )
        (docs_dir / "quick-start.md").write_text("", encoding="utf-8")
        (docs_dir / "install-controlcoding-on-your-project.md").write_text("", encoding="utf-8")

        result = cc.cmd_truth_check_docs(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["controlClaimCount"] == 1
        assert payload["controlClaims"][0]["state"] == "contextualized"

    def test_truth_check_can_include_docs_payload(self, tmp_path, capsys):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (tmp_path / "README.md").write_text("Run `cc doctor`.\n", encoding="utf-8")
        (docs_dir / "quick-start.md").write_text("", encoding="utf-8")
        (docs_dir / "install-controlcoding-on-your-project.md").write_text("", encoding="utf-8")

        result = cc.cmd_truth_check(tmp_path, json_output=True, include_docs=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["docs"]["ok"] is True
        assert payload["docs"]["commandCount"] == 1

    def test_truth_check_rejects_project_registry_with_unknown_command(self, tmp_path, capsys):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "capabilities.json").write_text(
            json.dumps(
                {
                    "capabilities": [
                        {
                            "id": "broken_claim",
                            "label": "Broken claim",
                            "state": "shipped",
                            "control_level": "mechanical",
                            "hosts": ["all"],
                            "commands": ["ghost command"],
                            "evidence": ["test evidence"],
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = cc.cmd_truth_check(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["registrySource"] == "project"
        assert any("ghost command" in finding["message"] for finding in payload["findings"])

    def test_doctor_strict_claims_reports_claim_integrity(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True, strict_claims=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["claimIntegrity"]["ok"] is True
        assert checks["claim_integrity"]["status"] == "ok"


class TestDocsMaintenanceCommands:
    def test_real_release_manifest_pins_work_plane_contract(self):
        repo_root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (repo_root / "controlcoding.release.json").read_text(encoding="utf-8")
        )

        assert manifest["workPlaneContract"] == "controlwork-work-plane/1.0.0"
        assert "docs/work-plane-compatibility-contract.md" in manifest["required"]

    def test_real_release_manifest_requires_legal_documents(self):
        repo_root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (repo_root / "controlcoding.release.json").read_text(encoding="utf-8")
        )
        allow_patterns = [entry["pattern"] for entry in manifest["allow"]]

        for relative in cc_docs.RELEASE_REQUIRED_LEGAL_FILES:
            assert allow_patterns.count(relative) == 1
            assert manifest["required"].count(relative) == 1

    def test_release_manifest_loader_accepts_coherent_work_plane_contract(self, tmp_path):
        _write_work_plane_release_fixture(tmp_path)

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is True
        assert policy["workPlaneContract"] == "controlwork-work-plane/1.0.0"
        assert policy["issues"] == []

    @pytest.mark.parametrize("relative", cc_docs.RELEASE_REQUIRED_LEGAL_FILES)
    def test_release_manifest_loader_requires_legal_document(self, tmp_path, relative):
        _write_work_plane_release_fixture(tmp_path)
        manifest_path = tmp_path / "controlcoding.release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["required"].remove(relative)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert f"required must include {relative}" in policy["issues"]

    @pytest.mark.parametrize("relative", cc_docs.RELEASE_REQUIRED_LEGAL_FILES)
    def test_release_manifest_loader_rejects_missing_legal_document(self, tmp_path, relative):
        _write_work_plane_release_fixture(tmp_path)
        (tmp_path / relative).unlink()

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert f"{relative} is missing" in policy["issues"]

    @pytest.mark.parametrize("relative", cc_docs.RELEASE_REQUIRED_LEGAL_FILES)
    def test_release_manifest_loader_rejects_non_file_legal_document(self, tmp_path, relative):
        _write_work_plane_release_fixture(tmp_path)
        (tmp_path / relative).unlink()
        (tmp_path / relative).mkdir()

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert f"{relative} must be a regular file" in policy["issues"]

    @pytest.mark.parametrize(
        ("case", "value", "expected_issue"),
        [
            ("missing", None, "workPlaneContract is required"),
            ("wrong_type", 1, "workPlaneContract must be a string"),
            (
                "unexpected",
                "controlwork-work-plane/2.0.0",
                "workPlaneContract must equal controlwork-work-plane/1.0.0",
            ),
        ],
    )
    def test_release_manifest_loader_rejects_invalid_work_plane_pin(
        self,
        tmp_path,
        case,
        value,
        expected_issue,
    ):
        _write_work_plane_release_fixture(tmp_path)
        manifest_path = tmp_path / "controlcoding.release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if case == "missing":
            del manifest["workPlaneContract"]
        else:
            manifest["workPlaneContract"] = value
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert expected_issue in policy["issues"]

    def test_release_manifest_loader_requires_work_plane_document(self, tmp_path):
        _write_work_plane_release_fixture(tmp_path)
        manifest_path = tmp_path / "controlcoding.release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["required"] = []
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert (
            "required must include docs/work-plane-compatibility-contract.md"
            in policy["issues"]
        )

    def test_release_manifest_loader_rejects_missing_work_plane_document(self, tmp_path):
        _write_work_plane_release_fixture(tmp_path)
        (tmp_path / cc_docs.WORK_PLANE_CONTRACT_DOCUMENT).unlink()

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert "docs/work-plane-compatibility-contract.md is missing" in policy["issues"]

    def test_release_manifest_loader_rejects_unreadable_utf8_work_plane_document(
        self,
        tmp_path,
    ):
        _write_work_plane_release_fixture(tmp_path)
        (tmp_path / cc_docs.WORK_PLANE_CONTRACT_DOCUMENT).write_bytes(b"\xff")

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert any("is not readable UTF-8" in issue for issue in policy["issues"])

    @pytest.mark.parametrize(
        ("identity", "expected_issue"),
        [
            (None, "does not declare Contract identity"),
            (
                "controlwork-work-plane/9.9.9",
                "Contract identity must be exactly controlwork-work-plane/1.0.0",
            ),
        ],
    )
    def test_release_manifest_loader_rejects_missing_or_incoherent_document_identity(
        self,
        tmp_path,
        identity,
        expected_issue,
    ):
        _write_work_plane_release_fixture(tmp_path)
        _write_work_plane_contract_document(tmp_path, identity=identity)

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert any(expected_issue in issue for issue in policy["issues"])

    def test_release_manifest_loader_rejects_canonical_embedded_profile(self, tmp_path):
        _write_work_plane_release_fixture(tmp_path)
        _write_work_plane_contract_document(tmp_path, status="canonical")

        policy = cc_docs.load_release_manifest_contract(tmp_path)

        assert policy["ok"] is False
        assert (
            "docs/work-plane-compatibility-contract.md must not declare a canonical profile"
            in policy["issues"]
        )

    def test_docs_gate_propagates_work_plane_contract_finding(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        manifest_path = tmp_path / "controlcoding.release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        del manifest["workPlaneContract"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert any(
            finding["check"] == "release_manifest_contract"
            and "workPlaneContract is required" in finding["message"]
            for finding in payload["findings"]
        )

    def test_docs_audit_accepts_complete_system_doc_map(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)

        result = cc_docs.cmd_docs_audit(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["schemaVersion"] == cc_docs.DOCS_MAINTENANCE_SCHEMA_VERSION
        assert payload["summary"]["errors"] == 0
        assert any(
            doc["path"] == "docs/controlcoding-system-architecture.md" and doc["exists"]
            for doc in payload["documents"]
        )

    def test_docs_audit_rejects_missing_architecture_map(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        (tmp_path / "docs" / "controlcoding-system-architecture.md").unlink()

        result = cc_docs.cmd_docs_audit(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert any(
            finding["check"] == "required_doc_exists"
            and finding.get("path") == "docs/controlcoding-system-architecture.md"
            for finding in payload["findings"]
        )

    def test_docs_audit_and_check_accept_public_manifest_with_absent_denied_plans(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)

        audit_result = cc_docs.cmd_docs_audit(tmp_path, json_output=True)
        audit_payload = json.loads(capsys.readouterr().out)
        check_result = cc_docs.cmd_docs_check(tmp_path, json_output=True)
        check_payload = json.loads(capsys.readouterr().out)

        assert audit_result == check_result == 0
        assert audit_payload["ok"] is check_payload["ok"] is True
        assert audit_payload["profile"] == check_payload["profile"] == "release_manifest"
        assert audit_payload["documents"] == check_payload["documents"]
        assert audit_payload["summary"] == check_payload["summary"]
        assert audit_payload["findings"] == check_payload["findings"]
        denied = {
            doc["path"]: doc
            for doc in check_payload["documents"]
            if doc["path"] in {
                "docs/controlwork-advanced-memory-phase-2-plan.md",
                "docs/session-graphrag-plan.md",
            }
        }
        assert all(doc["exists"] is False for doc in denied.values())
        assert all(doc["required"] is False for doc in denied.values())
        assert all(doc["releaseDenied"] is True for doc in denied.values())
        assert not any(
            finding["check"] == "architecture_reference"
            and any(Path(path).name in finding["message"] for path in denied)
            for finding in check_payload["findings"]
        )

    def test_docs_check_keeps_lab_documents_required_without_release_manifest(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        missing = "docs/controlwork-advanced-memory-phase-2-plan.md"
        (tmp_path / missing).unlink()

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["profile"] == "development"
        document = next(doc for doc in payload["documents"] if doc["path"] == missing)
        assert document["required"] is True
        assert any(
            finding["check"] == "required_doc_exists" and finding.get("path") == missing
            for finding in payload["findings"]
        )

    def test_docs_check_fails_when_public_manifest_required_doc_is_missing(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        missing = "docs/project-memory-engine.md"
        (tmp_path / missing).unlink()

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        document = next(doc for doc in payload["documents"] if doc["path"] == missing)
        assert document["required"] is True
        assert document["releaseDenied"] is False
        assert any(
            finding["check"] == "required_doc_exists" and finding.get("path") == missing
            for finding in payload["findings"]
        )

    def test_docs_check_fails_when_public_required_architecture_reference_is_missing(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        architecture = tmp_path / "docs" / "controlcoding-system-architecture.md"
        architecture.write_text(
            architecture.read_text(encoding="utf-8").replace(
                "- [`memory-graph-contract.md`](./memory-graph-contract.md)\n",
                "",
            ),
            encoding="utf-8",
        )

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert any(
            finding["check"] == "architecture_reference"
            and "memory-graph-contract.md" in finding["message"]
            for finding in payload["findings"]
        )

    def test_docs_check_fails_closed_for_invalid_release_manifest(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        (tmp_path / "controlcoding.release.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "package": "controlcoding-core-source",
                    "allow": [{"pattern": "docs/**", "reason": "Public docs."}],
                    "deny": "docs/internal/**",
                    "required": [],
                }
            ),
            encoding="utf-8",
        )

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["profile"] == "release_manifest"
        assert any(finding["check"] == "release_manifest_contract" for finding in payload["findings"])

    def test_docs_check_strict_fails_on_warning(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="true\n", stderr=""),
                MagicMock(returncode=0, stdout="scripts/cc.py\n", stderr=""),
                MagicMock(returncode=0, stdout=" M scripts/cc.py\n", stderr=""),
            ]

            result = cc_docs.cmd_docs_check(tmp_path, strict=True, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert any(finding["check"] == "possible_doc_drift" for finding in payload["findings"])
        assert any(finding["check"] == "strict_warnings" for finding in payload["findings"])

    def test_docs_check_warns_on_docs_plan_without_status(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        (tmp_path / "docs" / "new-feature-plan.md").write_text(
            "# New Feature Plan\n\nImplementation notes.\n",
            encoding="utf-8",
        )

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert any(
            finding["check"] == "docs-plan-without-status"
            and finding.get("path") == "docs/new-feature-plan.md"
            for finding in payload["findings"]
        )

    def test_docs_check_warns_on_duplicate_audit_output(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        audit_report = tmp_path / "audit_reports" / "final-audit.md"
        output_copy = tmp_path / ".controlwork" / "memory" / "outputs" / "final-audit.md"
        audit_report.parent.mkdir()
        output_copy.parent.mkdir(parents=True)
        content = "# Final Audit\n\nFinding body.\n"
        audit_report.write_text(content, encoding="utf-8")
        output_copy.write_text(content, encoding="utf-8")

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert any(
            finding["check"] == "duplicate-audit"
            and finding.get("path") == ".controlwork/memory/outputs/final-audit.md"
            for finding in payload["findings"]
        )

    def test_docs_check_warns_on_audit_outside_audit_reports(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        (tmp_path / "legacy-audit.md").write_text(
            "# Legacy Audit Report\n\nFinal audit body.\n",
            encoding="utf-8",
        )

        result = cc_docs.cmd_docs_check(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert any(
            finding["check"] == "audit-outside-audit_reports"
            and finding.get("path") == "legacy-audit.md"
            for finding in payload["findings"]
        )

    def test_docs_propose_writes_review_artifact(self, tmp_path, capsys):
        _write_docs_maintenance_baseline(tmp_path)
        output = Path(".controlcoding/docs/proposals/manual.md")

        result = cc_docs.cmd_docs_propose(tmp_path, output=output, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        proposal = tmp_path / payload["proposal"]
        assert proposal.exists()
        assert "Docs Maintenance Proposal" in proposal.read_text(encoding="utf-8")

    def test_docs_routes_are_registered_for_truth_contract(self):
        assert cc._truth_route_exists(("docs", "audit"))
        assert cc._truth_route_exists(("docs", "check"))
        assert cc._truth_route_exists(("docs", "propose"))


class TestMajorVersionPublicTruth:
    @staticmethod
    def _run_gate(project, capsys):
        git_payload = {
            "available": True,
            "since": "HEAD",
            "changedFiles": [],
            "error": "",
        }
        with patch.object(
            cc_docs,
            "_git_changed_files",
            return_value=git_payload,
        ) as changed_files:
            result = cc_docs.cmd_docs_check(project, json_output=True)
        changed_files.assert_called_once_with(project, since="HEAD")
        payload = json.loads(capsys.readouterr().out)
        return result, payload

    @staticmethod
    def _mutate_cli(project, old, new):
        path = project / "scripts" / "cc.py"
        source = path.read_text(encoding="utf-8")
        assert old in source
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def test_project_version_reads_project_table(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text(
            '[project]\nname = "controlcoding"\nversion = "3.0.0"\n',
            encoding="utf-8",
        )

        version, issue = cc_docs._read_project_version(path)

        assert version == "3.0.0"
        assert issue == ""

    def test_project_version_ignores_version_outside_project_table(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text(
            '\n'.join([
                '[tool.release]',
                'version = "99.0.0"',
                '',
                '[project]',
                'version = "3.0.0"',
                '',
            ]),
            encoding="utf-8",
        )

        version, issue = cc_docs._read_project_version(path)

        assert version == "3.0.0"
        assert issue == ""

    def test_project_version_missing_fails_closed(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\nname = "controlcoding"\n', encoding="utf-8")

        version, issue = cc_docs._read_project_version(path)

        assert version == ""
        assert "version" in issue

    @pytest.mark.parametrize("use_fallback", [False, True])
    @pytest.mark.parametrize(
        ("content", "relevant_terms"),
        [
            (
                '[project]\nversion = "3.0.0"\n[project]\nname = "duplicate"\n',
                ("pyproject.toml", "project"),
            ),
            ('[project]\nversion = ""\n', ("version", "empty")),
            ('[project]\nversion = 3\n', ("version", "ambiguous")),
        ],
    )
    def test_project_version_rejects_invalid_project_version_forms(
        self,
        tmp_path,
        monkeypatch,
        use_fallback,
        content,
        relevant_terms,
    ):
        path = tmp_path / "pyproject.toml"
        path.write_text(content, encoding="utf-8")
        if use_fallback:
            monkeypatch.setattr(cc_docs, "tomllib", None)

        version, issue = cc_docs._read_project_version(path)

        assert version == ""
        assert issue
        assert any(term in issue.casefold() for term in relevant_terms)

    def test_project_version_rejects_non_utf8_before_toml_parsing(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_bytes(b'[project]\nversion = "3.0.0"\n\xff')

        version, issue = cc_docs._read_project_version(path)

        assert version == ""
        assert issue
        assert "utf-8" in issue.casefold()

    def test_python_3_10_fallback_rejects_duplicate_project_version(
        self,
        tmp_path,
        monkeypatch,
    ):
        path = tmp_path / "pyproject.toml"
        path.write_text(
            '[project]\nversion = "3.0.0"\nversion = "4.0.0"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(cc_docs, "tomllib", None)

        version, issue = cc_docs._read_project_version(path)

        assert version == ""
        assert "duplicate" in issue or "ambiguous" in issue

    def test_python_3_10_fallback_rejects_ambiguous_project_version(
        self,
        tmp_path,
        monkeypatch,
    ):
        path = tmp_path / "pyproject.toml"
        path.write_text(
            '[project]\nversion = resolve_version()\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(cc_docs, "tomllib", None)

        version, issue = cc_docs._read_project_version(path)

        assert version == ""
        assert "ambiguous" in issue

    def test_python_3_10_fallback_is_scoped_to_project_table(
        self,
        tmp_path,
        monkeypatch,
    ):
        path = tmp_path / "pyproject.toml"
        path.write_text(
            '\n'.join([
                '[tool.release]',
                'version = "99.0.0"',
                '',
                '[project]',
                'version = "3.0.0"',
                '',
            ]),
            encoding="utf-8",
        )
        monkeypatch.setattr(cc_docs, "tomllib", None)

        version, issue = cc_docs._read_project_version(path)

        assert version == "3.0.0"
        assert issue == ""

    def test_generic_public_truth_gate_accepts_coherent_future_version(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        replacements = {
            "pyproject.toml": ('version = "3.0.0"', 'version = "4.1.0"'),
            "scripts/cc_memory_lib/schema.py": (
                'CONTROLCODING_VERSION = "v3.0.0"',
                'CONTROLCODING_VERSION = "v4.1.0"',
            ),
            "README.md": ("3.0.0", "4.1.0"),
            "CHANGELOG.md": ("3.0.0", "4.1.0"),
            "docs/release-model.md": ("3.0.0", "4.1.0"),
        }
        for relative, (old, new) in replacements.items():
            path = tmp_path / relative
            text = path.read_text(encoding="utf-8")
            assert old in text
            path.write_text(text.replace(old, new), encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] == "product_version_consistency"
            for finding in payload["findings"]
        )

    def test_real_repository_product_version_is_current_3_0_2_release(self):
        repo_root = Path(__file__).resolve().parent.parent

        version, issue = cc_docs._read_project_version(repo_root / "pyproject.toml")

        assert version == "3.0.2"
        assert issue == ""

    def test_real_release_docs_describe_published_clean_history(self):
        repo_root = Path(__file__).resolve().parent.parent
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
        release_model = (repo_root / "docs" / "release-model.md").read_text(
            encoding="utf-8"
        )

        combined = " ".join((readme + "\n" + changelog + "\n" + release_model).split())

        assert "ControlCoding V1/Core `3.0.2` is the current public release" in readme
        assert (
            "Licensed current ControlCoding material and embedded ControlWork "
            "components" in " ".join(changelog.split())
        )
        assert "current published V1/Core release" in release_model
        assert "clean public repository begins at `3.0.1`" in release_model
        assert "not a published release" not in combined
        assert "future `v3.0.2` tag" not in combined
        assert "earlier private development commits and tags" in readme.lower()
        assert "stage identity regression test" in readme
        assert "PolyForm Shield 1.0.0" in readme

    def test_real_changelog_current_heading_syntax_is_selected_unambiguously(self):
        repo_root = Path(__file__).resolve().parent.parent
        changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

        sections, issues = cc_docs._current_changelog_sections(changelog, "3.0.2")

        assert issues == []
        expected_unreleased = changelog.split("## Unreleased", 1)[1].split(
            "## 3.0.2", 1
        )[0]
        expected_release = changelog.split("## 3.0.2", 1)[1].split(
            "## 3.0.1", 1
        )[0]
        assert sections == [expected_unreleased, expected_release]
        assert "## 3.0.0" in changelog

    def test_production_cli_registry_and_parser_routes_are_structurally_recognized(self):
        repo_root = Path(__file__).resolve().parent.parent
        source = (repo_root / "scripts" / "cc.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        registry_routes, registry_issue = cc_docs._static_route_registry(tree)
        parser_routes, _, _, _, parser_issues = cc_docs._static_main_parser(tree)

        assert registry_issue == ""
        assert parser_issues == []
        assert cc_docs._REQUIRED_PUBLIC_CLI_ROUTES <= registry_routes
        assert cc_docs._REQUIRED_PUBLIC_CLI_ROUTES <= parser_routes
        assert not any(route[0] == "replace" for route in registry_routes)
        assert not any(route[0] == "replace" for route in parser_routes)

    def test_structural_cli_fixture_is_not_a_literal_list(self, tmp_path):
        _write_public_docs_release_fixture(tmp_path)
        source = (tmp_path / "scripts" / "cc.py").read_text(encoding="utf-8")

        findings = cc_docs._cli_public_truth_findings(source)

        assert "def main():" in source
        assert 'sub = parser.add_subparsers(dest="command")' in source
        assert 'p_benchmark = sub.add_parser("benchmark")' in source
        assert not any(finding["check"] == "cli_public_truth" for finding in findings)

    def test_static_cli_gate_rejects_parser_built_but_never_consumed(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "    args, extra_args = parser.parse_known_args()",
            "    # recognized root parser is intentionally not consumed",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "root parser must be consumed" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_parsing_on_a_different_parser(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "    args, extra_args = parser.parse_known_args()",
            "\n".join([
                "    other_parser = object()",
                "    args = other_parser.parse_args()",
            ]),
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "root parser must be consumed" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_accepts_parse_args_consumption(self, tmp_path):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "    args, extra_args = parser.parse_known_args()",
            "    args = parser.parse_args()",
        )
        source = (tmp_path / "scripts" / "cc.py").read_text(encoding="utf-8")

        findings = cc_docs._cli_public_truth_findings(source)

        assert not any(finding["check"] == "cli_public_truth" for finding in findings)

    def test_static_cli_gate_rejects_parser_result_not_used_by_dispatch(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "    args, extra_args = parser.parse_known_args()",
            "    parsed_args, extra_args = parser.parse_known_args()",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "root branch for benchmark" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_duplicate_root_parser_consumption(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "    args, extra_args = parser.parse_known_args()",
            "\n".join([
                "    args, extra_args = parser.parse_known_args()",
                "    duplicate_args = parser.parse_args()",
            ]),
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "consumed exactly once" in finding.get("detail", "")
            and "observed=2" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_missing_benchmark_dispatch_branch(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            '    if args.command == "benchmark":',
            '    if args.command == "not-benchmark":',
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "root branch for benchmark" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        ("command", "handler"),
        [
            ("run", "cmd_benchmark_run"),
            ("compare", "cmd_benchmark_compare"),
            ("report", "cmd_benchmark_report"),
        ],
    )
    def test_static_cli_gate_rejects_missing_benchmark_handler_call(
        self,
        tmp_path,
        capsys,
        command,
        handler,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            f"            return {handler}()",
            f"            return 1  # {handler} intentionally not called",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and f"benchmark {command} must call {handler}" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        ("route", "handler"),
        [
            ("organize", "cmd_organize"),
            ("resume", "cmd_resume"),
        ],
    )
    def test_static_cli_gate_rejects_missing_root_handler_call(
        self,
        tmp_path,
        capsys,
        route,
        handler,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            f"        return {handler}()",
            f"        return 1  # {handler} intentionally not called",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and f"route {route} must call {handler}" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_ignores_dispatch_names_in_non_dispatch_text_and_nested_code(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "\n".join([
                '    if args.command == "organize":',
                "        return cmd_organize()",
            ]),
            "\n".join([
                '    dispatch_note = "args.command == organize; cmd_organize()"',
                "    # args.command == organize; cmd_organize()",
                "    def nested_dispatch():",
                '        if args.command == "organize":',
                "            return cmd_organize()",
            ]),
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "root branch for organize" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_ambiguous_duplicate_dispatch_branch(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            "\n".join([
                '    if args.command == "resume":',
                "        return cmd_resume()",
            ]),
            "\n".join([
                '    if args.command == "resume":',
                "        return cmd_resume()",
                '    if args.command == "resume":',
                "        return cmd_resume()",
            ]),
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "root branch for resume" in finding.get("detail", "")
            and "observed=2" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_missing_registry_route(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            '    ("benchmark", "report"),',
            '    # benchmark report route intentionally absent',
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and finding["severity"] == "error"
            and finding.get("path") == "scripts/cc.py"
            and finding["message"]
            == "_ROUTED_CLI_COMMANDS is missing required route benchmark report."
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_route_present_only_as_string(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            '    p_benchmark_report = benchmark_sub.add_parser("report")',
            '    route_note = "benchmark report"',
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "main parser" in finding["message"]
            and "benchmark report" in finding["message"]
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize("argument", ["baseline", "current"])
    def test_static_cli_gate_rejects_missing_benchmark_positional(
        self,
        tmp_path,
        capsys,
        argument,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            f'    p_benchmark_compare.add_argument("{argument}", type=Path)',
            f'    # missing positional {argument}',
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and f"missing argument {argument}" in finding["message"]
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        ("route", "line"),
        [
            ("organize", '    p_organize.add_argument("--apply", action="store_true")'),
            (
                "resume",
                '    p_resume.add_argument("--brief", action="store_true", help="Accepted for CLI compatibility; the current command always prints the brief")',
            ),
        ],
    )
    def test_static_cli_gate_rejects_missing_required_option(
        self,
        tmp_path,
        capsys,
        route,
        line,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(tmp_path, line, f"    # missing required option for {route}")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and f"Parser route {route} is missing argument" in finding["message"]
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_wrong_benchmark_default(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(tmp_path, 'Path("benchmarks") / "run.json"', 'Path("benchmarks") / "wrong.json"')

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "BENCHMARK_RUN_OUTPUT" in finding["message"]
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_reregistered_replace_parser(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            '    p_resume.add_argument("--brief", action="store_true", help="Accepted for CLI compatibility; the current command always prints the brief")',
            '\n'.join([
                '    p_resume.add_argument("--brief", action="store_true", help="Accepted for CLI compatibility; the current command always prints the brief")',
                '    p_replace = sub.add_parser("replace")',
            ]),
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "removed_command_public_truth"
            and "main parser" in finding["message"]
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_unparseable_python_source(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "scripts" / "cc.py"
        path.write_text(
            path.read_text(encoding="utf-8") + "\ndef broken(:\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "not statically parseable" in finding["message"]
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_missing_python_source(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        (tmp_path / "scripts" / "cc.py").unlink()

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "missing or is not readable" in finding["message"]
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_registry_not_safely_evaluable(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        registry = '\n'.join([
            "_ROUTED_CLI_COMMANDS = frozenset({",
            '    ("benchmark",),',
            '    ("benchmark", "run"),',
            '    ("benchmark", "compare"),',
            '    ("benchmark", "report"),',
            '    ("organize",),',
            '    ("resume",),',
            "})",
        ])
        self._mutate_cli(
            tmp_path,
            registry,
            "_ROUTED_CLI_COMMANDS = frozenset(load_routes())",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "cannot be evaluated safely" in finding["message"]
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_unapproved_starred_registry_authority(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            '    ("benchmark",),',
            '\n'.join([
                '    ("benchmark",),',
                '    *runtime_routes,',
            ]),
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "unexpected starred authority" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_static_cli_gate_rejects_ambiguous_registry_assignment(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "scripts" / "cc.py"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n_ROUTED_CLI_COMMANDS = frozenset()\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "cannot be evaluated safely" in finding["message"]
            and "exactly one" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        ("case", "extra_source"),
        [
            ("augassign", "_ROUTED_CLI_COMMANDS += frozenset()"),
            (
                "nested_if",
                "if True:\n    _ROUTED_CLI_COMMANDS = frozenset()",
            ),
            (
                "nested_try",
                "try:\n    _ROUTED_CLI_COMMANDS = frozenset()\nexcept Exception:\n    pass",
            ),
            (
                "nested_loop",
                "for item in ():\n    _ROUTED_CLI_COMMANDS = frozenset()",
            ),
            (
                "nested_function",
                "def rewrite_registry():\n    _ROUTED_CLI_COMMANDS = frozenset()",
            ),
            (
                "nested_class",
                "class RegistryRewrite:\n    _ROUTED_CLI_COMMANDS = frozenset()",
            ),
            ("named_expression", "(_ROUTED_CLI_COMMANDS := frozenset())"),
            ("delete", "del _ROUTED_CLI_COMMANDS"),
            (
                "multiple_target",
                "_ROUTED_CLI_COMMANDS = other_registry = frozenset()",
            ),
            (
                "destructured_target",
                "(_ROUTED_CLI_COMMANDS,) = (frozenset(),)",
            ),
        ],
    )
    def test_static_cli_gate_rejects_disallowed_registry_store_or_delete(
        self,
        tmp_path,
        capsys,
        case,
        extra_source,
    ):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "scripts" / "cc.py"
        path.write_text(
            path.read_text(encoding="utf-8") + f"\n{extra_source}\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1, case
        assert any(
            finding["check"] == "cli_public_truth"
            and "cannot be evaluated safely" in finding["message"]
            for finding in payload["findings"]
        ), case

    def test_static_cli_gate_ignores_parser_structure_in_dead_code(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        self._mutate_cli(
            tmp_path,
            '    p_organize = sub.add_parser("organize")',
            '\n'.join([
                '    if False:',
                '        p_organize = sub.add_parser("organize")',
            ]),
        )
        self._mutate_cli(
            tmp_path,
            '    p_organize.add_argument("--apply", action="store_true")',
            '        p_organize.add_argument("--apply", action="store_true")',
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "cli_public_truth"
            and "main parser is missing required route organize" in finding["message"]
            for finding in payload["findings"]
        )

    def test_current_test_count_in_unreleased_changelog_is_rejected(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "CHANGELOG.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                "## 3.0.0",
                "## Unreleased\n\nCurrent test count: 123.\n\n## 3.0.0",
                1,
            ),
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "unverified_current_test_count"
            and finding.get("path") == "CHANGELOG.md"
            for finding in payload["findings"]
        )

    def test_current_test_count_in_version_changelog_section_is_rejected(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "CHANGELOG.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                "Major public CLI alignment.",
                "Major public CLI alignment. Current test count: 123.",
                1,
            ),
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "unverified_current_test_count"
            and finding.get("path") == "CHANGELOG.md"
            for finding in payload["findings"]
        )

    def test_historical_changelog_test_count_is_not_current(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "CHANGELOG.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n## v2.5.2 (May 2026)\n\nCurrent test count: 123.\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] == "unverified_current_test_count"
            and finding.get("path") == "CHANGELOG.md"
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize("heading", ["Unreleased", "3.0.0"])
    def test_duplicate_current_changelog_section_fails_closed(
        self,
        tmp_path,
        capsys,
        heading,
    ):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "CHANGELOG.md"
        text = path.read_text(encoding="utf-8")
        if heading == "Unreleased":
            text = text.replace("## 3.0.0", "## Unreleased\n\nPending.\n\n## 3.0.0", 1)
        path.write_text(text + f"\n## {heading}\n\nDuplicate.\n", encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "current_changelog_sections"
            and "duplicate" in finding.get("detail", "").casefold()
            for finding in payload["findings"]
        )

    def test_missing_current_changelog_version_section_fails_closed(
        self,
        tmp_path,
        capsys,
    ):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "CHANGELOG.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("## 3.0.0", "## 2.9.0", 1), encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "current_changelog_sections"
            and "does not contain a section" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_readme_current_test_count_remains_rejected(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / "README.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\nCurrent test count: 123.\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "unverified_current_test_count"
            and finding.get("path") == "README.md"
            for finding in payload["findings"]
        )

    def test_docs_checker_accepts_realistic_current_public_passages(
        self,
        tmp_path,
        capsys,
    ):
        _write_docs_checker_regression_fixture(tmp_path)

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] in {
                "organize_public_truth",
                "benchmark_public_truth",
                "removed_command_public_truth",
            }
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "relative_path",
        [
            "docs/ccdocs/tools-reference.md",
            "docs/file-organization-standard.md",
        ],
        ids=["tools-reference", "file-organization-standard"],
    )
    def test_organize_default_mutation_reports_its_source_document(
        self,
        tmp_path,
        capsys,
        relative_path,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        assert "`cc organize`" in text
        claim = "`cc organize` repairs files by default."
        path.write_text(text.rstrip("\n") + f"\n\n{claim}\n", encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert payload["ok"] is False
        assert any(
            finding["check"] == "organize_public_truth"
            and finding["severity"] == "error"
            and finding.get("path") == relative_path
            and finding.get("detail") == claim
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "claim",
        [
            "`cc organize` repairs files by default. Use `--apply` only for benchmark exports.",
            "`cc organize` is preview-only by default. It repairs files by default.",
            "`cc organize` repairs files by default, while `cc init` does not create settings.",
        ],
        ids=["irrelevant-apply", "adjacent-correct-preview", "irrelevant-negation"],
    )
    def test_organize_scoped_context_does_not_hide_default_mutation(
        self,
        tmp_path,
        capsys,
        claim,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        relative_path = "docs/ccdocs/tools-reference.md"
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        assert "# Tools Reference" in text
        path.write_text(text.rstrip("\n") + f"\n\n{claim}\n", encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "organize_public_truth"
            and finding["severity"] == "error"
            and finding.get("path") == relative_path
            and "repairs files by default" in finding.get("detail", "")
            for finding in payload["findings"]
        )

    def test_organize_claims_do_not_cross_list_items_or_other_commands(
        self,
        tmp_path,
        capsys,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        relative_path = "docs/ccdocs/tools-reference.md"
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        assert "| `cc organize` |" in text
        separate_items = "\n".join([
            "- `cc init` creates files by default.",
            "- `cc write-path apply` writes reviewed files.",
            "- `cc organize` previews changes by default and applies only with `--apply`.",
        ])
        path.write_text(
            text.rstrip("\n") + f"\n\n{separate_items}\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] == "organize_public_truth"
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "tools_final_newline",
        [False, True],
        ids=["without-final-newline", "with-final-newline"],
    )
    def test_organize_claims_do_not_cross_document_boundaries(
        self,
        tmp_path,
        capsys,
        tools_final_newline,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        tools_path = tmp_path / "docs" / "ccdocs" / "tools-reference.md"
        standard_path = tmp_path / "docs" / "file-organization-standard.md"
        tools_text = tools_path.read_text(encoding="utf-8")
        standard_text = standard_path.read_text(encoding="utf-8")
        assert "# Tools Reference" in tools_text
        assert "# File Organization" in standard_text
        tools_text = tools_text.rstrip("\n") + "\n\n`cc organize` repairs files"
        if tools_final_newline:
            tools_text += "\n"
        tools_path.write_text(tools_text, encoding="utf-8")
        standard_path.write_text(
            "by default.\n\n" + standard_text.lstrip("\n"),
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] == "organize_public_truth"
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "invocation_prefix",
        ["cc", "python scripts/cc.py"],
        ids=["cc", "python-script"],
    )
    def test_benchmark_examples_accept_supported_public_invocations(
        self,
        tmp_path,
        capsys,
        invocation_prefix,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        path = tmp_path / "docs" / "ccdocs" / "tools-reference.md"
        text = path.read_text(encoding="utf-8")
        old_compare = "python scripts/cc.py benchmark compare <baseline> <current>"
        old_report = "python scripts/cc.py benchmark report"
        assert old_compare in text
        assert old_report in text
        text = text.replace(
            old_compare,
            f"{invocation_prefix} benchmark compare <baseline> <current>",
            1,
        )
        text = text.replace(
            old_report,
            f"{invocation_prefix} benchmark report",
            1,
        )
        path.write_text(text, encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] == "benchmark_public_truth"
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "invalid_compare",
        [
            "python scripts/cc.py benchmark compare",
            "python scripts/cc.py benchmark compare --baseline <baseline> --current <current>",
        ],
        ids=["missing-positionals", "obsolete-options"],
    )
    def test_benchmark_compare_rejects_missing_or_obsolete_inputs(
        self,
        tmp_path,
        capsys,
        invalid_compare,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        relative_path = "docs/ccdocs/tools-reference.md"
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        valid_compare = "python scripts/cc.py benchmark compare <baseline> <current>"
        assert valid_compare in text
        path.write_text(
            text.replace(valid_compare, invalid_compare, 1),
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "benchmark_public_truth"
            and finding["severity"] == "error"
            and finding.get("path") == relative_path
            and finding["message"]
            == "Public benchmark guidance is missing cc benchmark compare <baseline> <current>."
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "replacement",
        ["", "cc benchmark run|compare|report"],
        ids=["missing", "compact-enumeration-only"],
    )
    def test_benchmark_report_requires_a_complete_invocation(
        self,
        tmp_path,
        capsys,
        replacement,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        relative_path = "docs/ccdocs/tools-reference.md"
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        valid_report = "python scripts/cc.py benchmark report"
        assert valid_report in text
        path.write_text(
            text.replace(valid_report, replacement, 1),
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == "benchmark_public_truth"
            and finding["severity"] == "error"
            and finding.get("path") == relative_path
            and finding["message"]
            == "Public benchmark guidance is missing cc benchmark report."
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        "historical_note",
        [
            "The public route `cc replace start/status/complete` was\nremoved from the current interface.",
            "- The public route `cc replace start/status/complete` was\n  removed from the current interface.",
        ],
        ids=["wrapped-paragraph", "wrapped-list-item"],
    )
    def test_removed_replace_accepts_wrapped_historical_note(
        self,
        tmp_path,
        capsys,
        historical_note,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        path = tmp_path / "docs" / "release-model.md"
        text = path.read_text(encoding="utf-8")
        assert "3.0.0" in text
        path.write_text(
            "# Release Model\n\n"
            "ControlCoding `3.0.0` is a candidate, not a published release.\n\n"
            f"{historical_note}\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] == "removed_command_public_truth"
            and finding.get("path") == "docs/release-model.md"
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        ("case", "historical_and_current"),
        [
            (
                "same-paragraph",
                "`cc replace status` was removed. Run cc replace status.",
            ),
            (
                "paragraph-boundary",
                "`cc replace status` was removed.\n\nRun cc replace status.",
            ),
            (
                "same-list-item",
                "- `cc replace status` was removed. Run cc replace status.",
            ),
            (
                "list-boundary",
                "- `cc replace status` was removed.\n- Run cc replace status.",
            ),
            (
                "code-fence-boundary",
                "`cc replace status` was removed.\n\n```text\ncc replace status\n```",
            ),
            (
                "table-row-boundary",
                "| History | `cc replace status` was removed. |\n"
                "| Action | Run cc replace status. |",
            ),
        ],
        ids=[
            "same-paragraph",
            "paragraph-boundary",
            "same-list-item",
            "list-boundary",
            "code-fence-boundary",
            "table-row-boundary",
        ],
    )
    def test_removed_replace_history_does_not_exempt_current_instruction(
        self,
        tmp_path,
        capsys,
        case,
        historical_and_current,
    ):
        _write_docs_checker_regression_fixture(tmp_path)
        relative_path = "docs/release-model.md"
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        assert "3.0.0" in text
        path.write_text(
            "# Release Model\n\n"
            "ControlCoding `3.0.0` is a candidate, not a published release.\n\n"
            f"{historical_and_current}\n",
            encoding="utf-8",
        )

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1, case
        assert any(
            finding["check"] == "removed_command_public_truth"
            and finding["severity"] == "error"
            and finding.get("path") == relative_path
            and "cc replace status" in finding.get("detail", "")
            and "removed" not in finding.get("detail", "").casefold()
            for finding in payload["findings"]
        ), case

    def test_public_truth_gate_accepts_coherent_3_0_candidate(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 0
        assert payload["ok"] is True
        assert not any(
            finding["check"] in {
                "product_version_consistency",
                "unverified_current_test_count",
                "organize_public_truth",
                "resume_public_truth",
                "benchmark_public_truth",
                "removed_command_public_truth",
                "controlwork_command_scope",
                "cli_public_truth",
            }
            for finding in payload["findings"]
        )

    @pytest.mark.parametrize(
        ("case", "relative_path", "old", "new", "expected_check"),
        [
            (
                "version",
                "scripts/cc_memory_lib/schema.py",
                'CONTROLCODING_VERSION = "v3.0.0"',
                'CONTROLCODING_VERSION = "v9.0.0"',
                "product_version_consistency",
            ),
            (
                "test_count",
                "README.md",
                "Current LAB release candidate: `3.0.0`.",
                "Current LAB release candidate: `3.0.0`. Current test count: 123.",
                "unverified_current_test_count",
            ),
            (
                "organize",
                "docs/ccdocs/tools-reference.md",
                "`cc organize` is preview-only; use `--apply` to apply.",
                "`cc organize` repairs files by default; use `--apply` later.",
                "organize_public_truth",
            ),
            (
                "resume",
                "docs/ccdocs/tools-reference.md",
                "`cc resume` is provider-neutral and does not select or launch a provider.",
                "`cc resume` launches Claude automatically.",
                "resume_public_truth",
            ),
            (
                "resume_cli",
                "scripts/cc.py",
                "Print a provider-neutral context brief without launching a provider or subprocess",
                "Print the context brief or launch a new Claude session from it",
                "cli_public_truth",
            ),
            (
                "benchmark",
                "docs/ccdocs/tools-reference.md",
                "benchmarks/run.json",
                "benchmark-run.json",
                "benchmark_public_truth",
            ),
            (
                "benchmark_report_default",
                "docs/ccdocs/tools-reference.md",
                "benchmarks/report.md",
                "benchmark-report.md",
                "benchmark_public_truth",
            ),
            (
                "replace",
                "docs/cross-tool-guide.md",
                "Preview, explicit adoption, then sync.",
                "Preview, explicit adoption, then sync. Run cc replace status.",
                "removed_command_public_truth",
            ),
            (
                "controlwork_scope",
                "docs/memory-graphrag-release-notes.md",
                "Standalone ControlWork checkout only",
                "ControlWork commands",
                "controlwork_command_scope",
            ),
        ],
    )
    def test_public_truth_gate_rejects_known_drift(
        self,
        tmp_path,
        capsys,
        case,
        relative_path,
        old,
        new,
        expected_check,
    ):
        _write_public_docs_release_fixture(tmp_path)
        path = tmp_path / relative_path
        text = path.read_text(encoding="utf-8")
        assert old in text, case
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

        result, payload = self._run_gate(tmp_path, capsys)

        assert result == 1
        assert any(
            finding["check"] == expected_check
            and finding["severity"] == "error"
            and finding.get("path") == relative_path
            for finding in payload["findings"]
        ), case

    def test_resume_help_and_brief_are_provider_neutral(self, tmp_path, capsys):
        with patch.object(sys, "argv", ["cc.py", "resume", "--help"]):
            with pytest.raises(SystemExit) as raised:
                cc.main()

        assert raised.value.code == 0
        help_text = " ".join(capsys.readouterr().out.split())
        assert "provider-neutral context brief" in help_text
        assert "without launching a provider or subprocess" in help_text
        assert "Accepted for CLI compatibility" in help_text
        assert "launch a new Claude session" not in help_text

        argv = ["cc.py", "resume", "--brief", "--project-root", str(tmp_path)]
        with patch.object(sys, "argv", argv), patch.object(
            cc,
            "cmd_resume",
            return_value=0,
        ) as resume:
            assert cc.main() == 0
        resume.assert_called_once_with(
            tmp_path.resolve(),
            brief_only=True,
            agent="",
        )

    def test_benchmark_parser_uses_positional_compare_and_current_defaults(self, tmp_path):
        compare_argv = [
            "cc.py",
            "benchmark",
            "compare",
            "baseline.json",
            "current.json",
            "--project-root",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", compare_argv), patch.object(
            cc,
            "cmd_benchmark_compare",
            return_value=0,
        ) as compare:
            assert cc.main() == 0
        compare.assert_called_once_with(
            tmp_path.resolve(),
            baseline_path=Path("baseline.json"),
            current_path=Path("current.json"),
            json_output=False,
        )

        report_argv = [
            "cc.py",
            "benchmark",
            "report",
            "--project-root",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", report_argv), patch.object(
            cc,
            "cmd_benchmark_report",
            return_value=0,
        ) as report:
            assert cc.main() == 0
        report.assert_called_once_with(
            tmp_path.resolve(),
            output_path=Path("benchmarks/report.md"),
            json_output=False,
        )
        assert cc.BENCHMARK_RUN_OUTPUT == Path("benchmarks/run.json")
        assert cc.BENCHMARK_REPORT_OUTPUT == Path("benchmarks/report.md")

    def test_organize_is_preview_only_until_apply(self, tmp_path, capsys):
        assert cc.cmd_organize(tmp_path, json_output=True) == 0
        preview = json.loads(capsys.readouterr().out)
        assert preview["moves_executed"] == []
        assert preview["index_generated"] is False
        assert not (tmp_path / "devlog").exists()

        assert cc.cmd_organize(tmp_path, json_output=True, apply=True) == 0
        applied = json.loads(capsys.readouterr().out)
        assert applied["index_generated"] is True
        assert (tmp_path / "devlog").is_dir()

    def test_root_help_does_not_present_replace_as_current(self, capsys):
        with patch.object(sys, "argv", ["cc.py", "--help"]):
            with pytest.raises(SystemExit) as raised:
                cc.main()

        assert raised.value.code == 0
        help_text = capsys.readouterr().out
        assert "replace" not in help_text
        assert cc._truth_route_exists(("replace",)) is False

    def test_real_3_0_docs_cover_breaks_and_explicit_adapter_adoption(self):
        repo_root = Path(__file__).resolve().parent.parent
        changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
        tools = (repo_root / "docs" / "ccdocs" / "tools-reference.md").read_text(encoding="utf-8")
        cross_tool = (repo_root / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8")
        install = (repo_root / "docs" / "install-controlcoding-on-your-project.md").read_text(encoding="utf-8")
        quick_start = (repo_root / "docs" / "quick-start.md").read_text(encoding="utf-8")

        for expected in (
            "cc replace start/status/complete",
            "cc benchmark compare",
            "cc organize",
            "cc resume",
        ):
            assert expected in changelog
        assert "There is no direct replacement command" in tools
        assert "context adopt --host <host> --apply" in cross_tool
        assert "`--force` applies only to adapters that are already valid-owned" in " ".join(cross_tool.split())
        for state in ("valid-owned", "unmarked", "foreign", "Invalid", "ambiguous"):
            assert state in install
        assert "Migrating From v2.5.2" in quick_start
        assert "normal sync never overwrites" in quick_start

    def test_real_tools_reference_has_current_title_and_executable_benchmark_examples(self):
        repo_root = Path(__file__).resolve().parent.parent
        tools = (repo_root / "docs" / "ccdocs" / "tools-reference.md").read_text(
            encoding="utf-8"
        )
        section_start = tools.index("### 4.4e cc benchmark")
        section_end = tools.find("\n### ", section_start + 1)
        benchmark_section = tools[
            section_start:section_end if section_end >= 0 else len(tools)
        ]

        assert tools.startswith("# ControlCoding v3.0 - Tools Reference\n")
        assert "python cc.py" not in tools
        for command in (
            "python scripts/cc.py benchmark run",
            "python scripts/cc.py benchmark compare <baseline> <current>",
            "python scripts/cc.py benchmark report",
        ):
            assert command in benchmark_section
        for breaking_change in (
            "former `--baseline`, `--current`, and report `--input` interface",
            "preview-only",
            "provider-neutral",
            "There is no direct replacement command",
        ):
            assert breaking_change in tools


class TestReleaseDoctor:
    _RELEASE_FIXTURE_REQUIRED_FILES = (
        *cc._RELEASE_REQUIRED_FILES,
        cc_docs.WORK_PLANE_CONTRACT_DOCUMENT,
    )

    def _write_public_architecture_index(self, project: Path):
        rows = [
            "# Public Architecture Index",
            "",
            "This release-visible index covers the Python implementation shipped by ControlCoding Core.",
            "",
        ]
        for parts, pattern, section in cc._ARCHITECTURE_INDEX_TRACKED_AREAS:
            root = project.joinpath(*parts)
            files = [] if not root.is_dir() else sorted(root.rglob(pattern), key=lambda path: path.as_posix())
            names = [
                path.relative_to(root).as_posix()
                for path in files
                if path.name not in cc._ARCHITECTURE_INDEX_SKIP_NAMES
                and not path.name.startswith("__")
            ]
            if not names:
                continue
            rows.extend([
                f"## {section}",
                "",
                "| File | Release role |",
                "|------|--------------|",
            ])
            rows.extend(f"| `{name}` | Shipped Python implementation. |" for name in names)
            rows.append("")
        (project / "docs" / "architecture-index.md").write_text(
            "\n".join(rows),
            encoding="utf-8",
        )

    def _make_release_repo(self, project: Path):
        for directory in ("docs", "scripts", "templates", "templates/hooks", "tests"):
            (project / directory).mkdir(parents=True, exist_ok=True)
        for relative in self._RELEASE_FIXTURE_REQUIRED_FILES:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# public release fixture\n", encoding="utf-8")
        _write_work_plane_contract_document(project)
        (project / "README.md").write_text(
            "Run `cc release-doctor` before publishing.\n",
            encoding="utf-8",
        )
        (project / "docs" / "quick-start.md").write_text(
            "Run `cc truth check --include-docs` for public command truth.\n",
            encoding="utf-8",
        )
        (project / "docs" / "install-controlcoding-on-your-project.md").write_text(
            "Run `cc verify status` after setup.\n",
            encoding="utf-8",
        )
        (project / "controlcoding.release.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "package": "controlcoding-core-source",
                    "workPlaneContract": cc_docs.WORK_PLANE_CONTRACT_IDENTITY,
                    "architectureIndex": "docs/architecture-index.md",
                    "allow": [
                        {"pattern": "README.md", "reason": "Public entrypoint."},
                        {"pattern": "LICENSE", "reason": "Public license."},
                        {"pattern": "COMMERCIAL_LICENSE.md", "reason": "Public commercial permission request path."},
                        {"pattern": "NOTICE", "reason": "Public notice."},
                        {"pattern": "TRADEMARKS.md", "reason": "Public trademark guidance."},
                        {"pattern": "CHANGELOG.md", "reason": "Public history."},
                        {"pattern": "CONTRIBUTING.md", "reason": "Public contribution guide."},
                        {"pattern": "INSTALL_WIZARD.md", "reason": "Public installation guide."},
                        {"pattern": "PROJECT_SETUP_WIZARD.md", "reason": "Public setup guide."},
                        {"pattern": "controlcoding.release.json", "reason": "Release allowlist."},
                        {"pattern": "controlcoding.verification.json", "reason": "Verification contract."},
                        {"pattern": "controlcoding.invariants.json", "reason": "Invariant contract."},
                        {"pattern": "docs/**", "reason": "Public documentation."},
                        {"pattern": "scripts/**", "reason": "Public CLI implementation."},
                        {"pattern": "templates/**", "reason": "Public templates."},
                        {"pattern": "tests/**", "reason": "Public tests."},
                        {"pattern": "benchmarks/*.md", "reason": "Public benchmark summaries."},
                    ],
                    "deny": [
                        {"pattern": ".controlcoding/**", "reason": "Local runtime state."},
                        {"pattern": ".controlwork/**", "reason": "Local work memory state."},
                        {"pattern": "AGENTS.md", "reason": "Host-local Codex context."},
                        {"pattern": "devlog/**", "reason": "Local session logs."},
                    ],
                    "required": list(self._RELEASE_FIXTURE_REQUIRED_FILES),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._write_public_architecture_index(project)
        (project / "controlcoding.verification.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "requiredKinds": ["targeted", "regression", "invariant"],
                    "suites": [
                        {
                            "id": "targeted",
                            "kind": "targeted",
                            "required": True,
                            "command": "python -c \"print('targeted')\"",
                        },
                        {
                            "id": "regression",
                            "kind": "regression",
                            "required": True,
                            "command": "python -c \"print('regression')\"",
                        },
                        {
                            "id": "invariant",
                            "kind": "invariant",
                            "required": True,
                            "command": "python -c \"print('invariant')\"",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (project / "controlcoding.invariants.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "projectType": "release",
                    "domains": ["release"],
                    "invariants": [
                        {
                            "id": "release-truth",
                            "title": "Release Truth",
                            "domain": "release",
                            "kind": "consistency",
                            "severity": "blocking",
                            "status": "active",
                            "property": "Public release claims must remain executable.",
                            "threshold": "0 failures",
                            "command": "python -c \"print('ok')\"",
                            "evidence": ["README.md"],
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_release_doctor_does_not_require_installed_project_artifacts(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(self._RELEASE_FIXTURE_REQUIRED_FILES),
                stderr="",
            )
            result = cc.cmd_doctor(tmp_path, json_output=True, release_mode=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["doctorMode"] == "release"
        assert payload["healthy"] is True
        assert payload["operationalContract"]["mode"] == "source_release"
        assert payload["operationalContract"]["releaseReady"] is True
        assert payload["operationalContract"]["safeForHumanWork"] is True
        assert payload["operationalContract"]["safeForAiAssistedWork"] is False
        assert checks["release_required_files"]["status"] == "ok"
        assert checks["claim_integrity"]["status"] == "ok"
        assert checks["verification_contract"]["status"] == "ok"
        assert checks["invariant_manifest"]["status"] == "ok"
        assert checks["release_manifest"]["status"] == "ok"
        assert payload["releaseManifest"]["ok"] is True
        assert payload["operationalContract"]["releaseManifestReady"] is True
        assert "cc_config" not in checks

    def test_release_index_requires_configured_public_index_when_dev_is_absent(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        public_index = tmp_path / "docs" / "architecture-index.md"
        public_index.unlink()

        assert cc.cmd_index(tmp_path, check=True) == 1
        assert "architecture-index.md" in capsys.readouterr().out

        self._write_public_architecture_index(tmp_path)
        assert cc.cmd_index(tmp_path, check=True) == 0
        assert "up to date" in capsys.readouterr().out

    def test_release_doctor_uses_release_aware_architecture_index(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(self._RELEASE_FIXTURE_REQUIRED_FILES),
                stderr="",
            )
            result = cc.cmd_doctor(tmp_path, json_output=True, release_mode=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["architecture_index"]["status"] == "ok"
        assert payload["architectureIndex"]["path"] == "docs/architecture-index.md"

    def test_release_doctor_cannot_bypass_incomplete_architecture_index(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / "docs" / "architecture-index.md").write_text(
            "# Public Architecture Index\n",
            encoding="utf-8",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(self._RELEASE_FIXTURE_REQUIRED_FILES),
                stderr="",
            )
            result = cc.cmd_doctor(tmp_path, json_output=True, release_mode=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["architecture_index"]["status"] == "fail"
        assert payload["architectureIndex"]["available"] is False
        assert payload["architectureIndex"]["missingCount"] is None
        assert "malformed" in payload["architectureIndex"]["issues"][0].lower()

    def test_release_doctor_alias_uses_release_profile(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(self._RELEASE_FIXTURE_REQUIRED_FILES),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["doctorMode"] == "release"
        assert payload["healthy"] is True
        assert payload["operationalContract"]["releaseReady"] is True
        assert payload["operationalContract"]["releaseManifestReady"] is True

    def test_main_routes_release_doctor(self, tmp_path):
        argv = [
            "cc.py",
            "release-doctor",
            "--project-root", str(tmp_path),
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_release_doctor", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            json_output=True,
        )

    def test_release_doctor_route_is_registered_for_truth_contract(self):
        assert cc._truth_route_exists(("release-doctor",))

    def test_release_doctor_static_required_files_include_legal_documents(self):
        for relative in cc_docs.RELEASE_REQUIRED_LEGAL_FILES:
            assert cc._RELEASE_REQUIRED_FILES.count(relative) == 1

    @pytest.mark.parametrize("relative", cc_docs.RELEASE_REQUIRED_LEGAL_FILES)
    def test_release_doctor_fails_missing_required_legal_file(
        self,
        tmp_path,
        capsys,
        relative,
    ):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / relative).unlink()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(self._RELEASE_FIXTURE_REQUIRED_FILES),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_required_files"]["status"] == "fail"
        assert relative in checks["release_required_files"]["detail"]
        assert payload["operationalContract"]["releaseReady"] is False

    @pytest.mark.parametrize("relative", cc_docs.RELEASE_REQUIRED_LEGAL_FILES)
    def test_release_doctor_fails_untracked_required_legal_file(
        self,
        tmp_path,
        capsys,
        relative,
    ):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        tracked = [
            candidate
            for candidate in self._RELEASE_FIXTURE_REQUIRED_FILES
            if candidate != relative
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_manifest"]["status"] == "fail"
        assert f"required file is not tracked: {relative}" in checks["release_manifest"]["detail"]
        assert relative in payload["releaseManifest"]["untrackedRequiredFiles"]
        assert payload["operationalContract"]["releaseReady"] is False

    def test_release_doctor_fails_missing_release_manifest(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / "controlcoding.release.json").unlink()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(self._RELEASE_FIXTURE_REQUIRED_FILES),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_manifest"]["status"] == "fail"
        assert "controlcoding.release.json is missing" in checks["release_manifest"]["detail"]
        assert payload["operationalContract"]["releaseManifestReady"] is False
        assert payload["operationalContract"]["releaseReady"] is False

    def test_release_doctor_fails_uncovered_tracked_file(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + ["internal/notes.md"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_manifest"]["status"] == "fail"
        assert "tracked file is not covered by allow patterns: internal/notes.md" in checks["release_manifest"]["detail"]
        assert "internal/notes.md" in payload["releaseManifest"]["uncoveredTrackedFiles"]

    def test_release_doctor_fails_manifest_deny_pattern(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + ["AGENTS.md"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_manifest"]["status"] == "fail"
        assert "tracked file matches deny pattern: AGENTS.md" in checks["release_manifest"]["detail"]
        assert "AGENTS.md" in payload["releaseManifest"]["deniedTrackedFiles"]

    def test_release_doctor_fails_tracked_local_artifacts(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + [".controlcoding/settings.json"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_doctor(tmp_path, json_output=True, release_mode=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_tracked_local_artifacts"]["status"] == "fail"
        assert ".controlcoding/settings.json" in checks["release_tracked_local_artifacts"]["detail"]
        assert payload["operationalContract"]["releaseReady"] is False
        assert payload["operationalContract"]["safeForHumanWork"] is False
        assert any(
            "release_tracked_local_artifacts" in reason
            for reason in payload["operationalContract"]["blockingReasons"]
        )

    def test_release_doctor_fails_private_plan_artifacts(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        private_plan = "docs/" + "external" + "-graph-tool-gap-implementation-plan.md"
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + [private_plan]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_doctor(tmp_path, json_output=True, release_mode=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_tracked_local_artifacts"]["status"] == "fail"
        assert private_plan in checks["release_tracked_local_artifacts"]["detail"]
        assert payload["operationalContract"]["releaseReady"] is False

    def test_release_doctor_fails_public_hygiene_terms(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        benchmark = tmp_path / "benchmarks" / "comparison.md"
        benchmark.parent.mkdir()
        benchmark.write_text(
            "Comparison draft for " + ("Gra" + "phify") + ".\n",
            encoding="utf-8",
        )
        rank_claim = tmp_path / "docs" / "rank-claim.md"
        rank_claim.write_text(
            "A/B " + ("super" + "iority") + " draft.\n",
            encoding="utf-8",
        )
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + [
            "benchmarks/comparison.md",
            "docs/rank-claim.md",
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_doctor(tmp_path, json_output=True, release_mode=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_public_hygiene"]["status"] == "fail"
        assert "benchmarks/comparison.md" in checks["release_public_hygiene"]["detail"]
        assert "named external graph tool reference" in checks["release_public_hygiene"]["detail"]
        assert "docs/rank-claim.md" in checks["release_public_hygiene"]["detail"]
        assert "competitive rank claim" in checks["release_public_hygiene"]["detail"]
        assert payload["operationalContract"]["releaseReady"] is False

    @staticmethod
    def _private_path_token(style: str) -> str:
        separator = chr(92) if style == "backslash" else "/"
        return "J:" + separator

    @pytest.mark.parametrize(
        ("relative", "style"),
        [
            ("benchmarks/release-scorecard.md", "backslash"),
            ("templates/helper-session/SETUP.md", "slash"),
            ("tests/test_public_contract.py", "backslash"),
        ],
        ids=("benchmark-allowlisted", "template-allowlisted", "test-shipped"),
    )
    def test_release_hygiene_finds_private_paths_in_shipped_text(self, tmp_path, relative, style):
        token = self._private_path_token(style)
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"private fixture: {token}author-workspace/project\n", encoding="utf-8")

        findings = cc._release_public_hygiene_findings(tmp_path, [relative])

        assert any(relative in finding and token in finding for finding in findings)

    def test_release_hygiene_allows_generic_paths_and_public_urls(self, tmp_path):
        relative = "docs/portable-examples.md"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "Windows examples: C:" + chr(92) + "example-workspace and C:/example-workspace\n"
            "Public URL: https://example.com/ControlCoding/setup\n",
            encoding="utf-8",
        )

        assert cc._release_public_hygiene_findings(tmp_path, [relative]) == []

    def test_release_doctor_fails_private_path_in_allowlisted_payload(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        relative = "benchmarks/release-scorecard.md"
        token = self._private_path_token("backslash")
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"private fixture: {token}author-workspace/project\n", encoding="utf-8")
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + [relative]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_release_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["release_public_hygiene"]["status"] == "fail"
        assert relative in checks["release_public_hygiene"]["detail"]
        assert token in checks["release_public_hygiene"]["detail"]

    def test_truth_check_with_docs_fails_private_path_in_shipped_payload(self, tmp_path, capsys):
        self._make_release_repo(tmp_path)
        (tmp_path / ".git").mkdir()
        relative = "templates/helper-session/SETUP.md"
        token = self._private_path_token("slash")
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"private fixture: {token}author-workspace/project\n", encoding="utf-8")
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + [relative]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join(tracked),
                stderr="",
            )
            result = cc.cmd_truth_check(tmp_path, json_output=True, include_docs=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert any(
            relative in finding["message"] and token in finding["message"]
            for finding in payload["findings"]
        )

    def test_release_hygiene_excludes_manifest_denied_file(self, tmp_path):
        self._make_release_repo(tmp_path)
        relative = "docs/internal/private-note.md"
        token = self._private_path_token("slash")
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"private fixture: {token}author-workspace/project\n", encoding="utf-8")
        tracked = list(self._RELEASE_FIXTURE_REQUIRED_FILES) + [relative]
        manifest_path = tmp_path / cc._RELEASE_MANIFEST_FILE
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["deny"].append({"pattern": "docs/internal/**", "reason": "Internal notes."})
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest_payload = cc._release_manifest_payload(tmp_path, tracked, "")

        findings = cc._release_public_hygiene_findings(tmp_path, tracked, manifest_payload)

        assert all(relative not in finding for finding in findings)


class TestVerifyCommands:
    def _write_contract(self, project: Path, suites: list[dict] | None = None):
        contract = {
            "schemaVersion": 1,
            "requiredKinds": ["targeted", "regression"],
            "suites": suites or [
                {
                    "id": "targeted-check",
                    "kind": "targeted",
                    "required": True,
                    "command": "python -c \"print('targeted')\"",
                    "description": "Targeted check",
                },
                {
                    "id": "regression-check",
                    "kind": "regression",
                    "required": True,
                    "command": "python -c \"print('regression')\"",
                    "description": "Regression check",
                },
            ],
        }
        (project / "controlcoding.verification.json").write_text(
            json.dumps(contract, indent=2),
            encoding="utf-8",
        )
        return contract

    def test_verify_init_writes_tracked_contract(self, tmp_path, capsys):
        result = cc.cmd_verify_init(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["path"] == "controlcoding.verification.json"
        assert (tmp_path / "controlcoding.verification.json").exists()

    def test_default_verification_contract_covers_v1_core_regressions(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        core_tests = [
            "test_cc_cli.py",
            "test_cc_memory.py",
            "test_gitignore.py",
            "test_check_boundaries.py",
            "test_check_dangerous_commands.py",
            "test_check_repo_boundaries.py",
            "test_check_file_organization.py",
            "test_cc_organize.py",
            "test_session.py",
        ]
        for test_name in core_tests:
            (tests_dir / test_name).write_text("", encoding="utf-8")

        contract = cc._default_verification_contract(tmp_path)
        suites = {suite["id"]: suite for suite in contract["suites"]}

        assert suites["docs-maintenance"]["required"] is True
        assert "docs check" in suites["docs-maintenance"]["command"]
        assert suites["memory-eval-ranking"]["required"] is True
        assert "memory eval" in suites["memory-eval-ranking"]["command"]
        assert suites["core-governance-regression"]["required"] is True
        command = suites["core-governance-regression"]["command"]
        for test_name in core_tests[1:]:
            if test_name == "test_gitignore.py":
                continue
            assert f"tests/{test_name}" in command

        tracked = json.loads(
            (Path(__file__).parents[1] / "controlcoding.verification.json").read_text(encoding="utf-8")
        )
        tracked_suites = {suite["id"]: suite for suite in tracked["suites"]}
        assert tracked_suites["docs-maintenance"]["required"] is True
        assert "docs check" in tracked_suites["docs-maintenance"]["command"]

    def test_verify_status_fails_without_contract(self, tmp_path, capsys):
        result = cc.cmd_verify_status(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert "verification contract is missing" in payload["issues"]

    def test_verify_status_accepts_required_targeted_and_regression(self, tmp_path, capsys):
        self._write_contract(tmp_path)

        result = cc.cmd_verify_status(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["requiredSummary"]["targeted"] == 1
        assert payload["requiredSummary"]["regression"] == 1

    def test_verify_run_executes_required_suites_and_writes_receipt(self, tmp_path, capsys):
        self._write_contract(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = cc.cmd_verify_run(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["status"] == "passed"
        assert len(payload["receipt"]["suites"]) == 2
        assert payload["receipt"]["tempDir"]
        assert mock_run.call_count == 2
        assert (tmp_path / payload["receiptPath"]).exists()

    def test_verify_run_expands_temp_placeholder_to_run_temp(self, tmp_path, capsys):
        self._write_contract(
            tmp_path,
            suites=[
                {
                    "id": "targeted-check",
                    "kind": "targeted",
                    "required": True,
                    "command": "python -c \"print('targeted')\" --basetemp {temp}/pytest",
                    "description": "Targeted check",
                },
                {
                    "id": "regression-check",
                    "kind": "regression",
                    "required": True,
                    "command": "python -c \"print('regression')\"",
                    "description": "Regression check",
                },
            ],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_verify_run(tmp_path, suite_ids=["targeted-check"], json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        command_args = " ".join(mock_run.call_args.args[0])
        assert "{temp}" not in command_args
        assert payload["receipt"]["tempDir"] in command_args
        assert command_args.endswith("/pytest")

    def test_verify_run_keeps_temp_path_with_spaces_as_single_arg(self, tmp_path, capsys):
        project = tmp_path / "project with spaces"
        project.mkdir()
        self._write_contract(
            project,
            suites=[
                {
                    "id": "targeted-check",
                    "kind": "targeted",
                    "required": True,
                    "command": "python -c \"print('targeted')\" --basetemp {temp}/pytest",
                    "description": "Targeted check",
                },
                {
                    "id": "regression-check",
                    "kind": "regression",
                    "required": True,
                    "command": "python -c \"print('regression')\"",
                    "description": "Regression check",
                },
            ],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_verify_run(project, suite_ids=["targeted-check"], json_output=True)

        assert result == 0
        args = mock_run.call_args.args[0]
        assert isinstance(args, list)
        assert mock_run.call_args.kwargs.get("shell") in (None, False)
        assert args[-2] == "--basetemp"
        assert args[-1].endswith("/pytest")
        assert "project with spaces" in args[-1]
        assert "with" not in args
        assert "spaces" not in args

    def test_verify_run_fails_when_suite_fails(self, tmp_path, capsys):
        self._write_contract(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=1, stdout="", stderr="failed"),
            ]
            result = cc.cmd_verify_run(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["status"] == "failed"
        assert payload["receipt"]["suites"][1]["status"] == "failed"

    def test_verify_run_propagates_real_docs_check_failure(self, tmp_path, capsys):
        _write_public_docs_release_fixture(tmp_path)
        (tmp_path / "docs" / "project-memory-engine.md").unlink()
        python = Path(sys.executable).resolve().as_posix()
        cc_script = Path(cc.__file__).resolve().as_posix()
        self._write_contract(
            tmp_path,
            suites=[
                {
                    "id": "docs-maintenance",
                    "kind": "targeted",
                    "required": True,
                    "command": (
                        f'"{python}" "{cc_script}" docs check '
                        "--project-root {project} --json"
                    ),
                    "description": "Run the effective docs maintenance check.",
                },
                {
                    "id": "regression-check",
                    "kind": "regression",
                    "required": True,
                    "command": f'"{python}" -c "print(\'regression\')"',
                    "description": "Regression check",
                },
            ],
        )

        result = cc.cmd_verify_run(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        docs_suite = next(
            suite for suite in payload["receipt"]["suites"]
            if suite["id"] == "docs-maintenance"
        )
        assert docs_suite["status"] == "failed"
        assert docs_suite["returnCode"] == 1
        assert "docs/project-memory-engine.md" in docs_suite["stdoutTail"]

    def test_verify_run_can_select_kind(self, tmp_path, capsys):
        self._write_contract(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_verify_run(tmp_path, kinds=["targeted"], json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert [suite["kind"] for suite in payload["receipt"]["suites"]] == ["targeted"]
        assert mock_run.call_count == 1

    def test_doctor_strict_verification_fails_without_contract(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True, strict_verification=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["verification_contract"]["status"] == "fail"

    def test_doctor_strict_verification_accepts_contract(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        self._write_contract(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True, strict_verification=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["verificationContract"]["ok"] is True
        assert checks["verification_contract"]["status"] == "ok"


class TestInvariantCommands:
    def _write_manifest(self, project: Path, invariants: list[dict] | None = None):
        manifest = {
            "schemaVersion": 1,
            "projectType": "test",
            "domains": ["test-domain"],
            "invariants": invariants or [
                {
                    "id": "target-property",
                    "title": "Target Property",
                    "domain": "test-domain",
                    "kind": "domain",
                    "severity": "blocking",
                    "status": "active",
                    "property": "The target property must hold.",
                    "threshold": "0 failures",
                    "command": "python -c \"print('invariant')\"",
                    "evidence": ["tests"],
                }
            ],
        }
        (project / "controlcoding.invariants.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        return manifest

    def test_invariants_init_writes_manifest(self, tmp_path, capsys):
        result = cc.cmd_invariants_init(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["path"] == "controlcoding.invariants.json"
        assert payload["summary"]["executable"] >= 1
        assert (tmp_path / "controlcoding.invariants.json").exists()

    def test_invariants_elicit_returns_domain_questions_and_candidates(self, tmp_path, capsys):
        result = cc.cmd_invariants_elicit(tmp_path, domain="finance", json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["domain"] == "finance"
        assert len(payload["questions"]) >= 5
        candidate_ids = {item["id"] for item in payload["candidates"]}
        assert "balance-consistency" in candidate_ids
        assert payload["candidates"][0]["status"] == "draft"
        assert payload["candidates"][0]["suggestedTestPath"].startswith("tests/invariants/")
        assert payload["candidates"][0]["suggestedCommand"].startswith("python -m pytest")

    def test_invariants_elicit_write_creates_markdown_draft(self, tmp_path, capsys):
        result = cc.cmd_invariants_elicit(
            tmp_path,
            domain="game-state",
            write=True,
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["written"] is True
        assert payload["outputPath"] == "docs/invariants/game-elicitation.md"
        content = (tmp_path / payload["outputPath"]).read_text(encoding="utf-8")
        assert "Invariant Elicitation - Game State" in content
        assert "replay-deterministic" in content

    def test_invariants_elicit_rejects_output_outside_project(self, tmp_path, capsys):
        result = cc.cmd_invariants_elicit(
            tmp_path,
            domain="web",
            write=True,
            output_path=tmp_path.parent / "outside.md",
            json_output=True,
        )

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["issues"] == ["output path is outside project root"]

    def test_invariants_wire_ci_dry_run_reports_github_workflow(self, tmp_path, capsys):
        self._write_manifest(tmp_path)
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "cc.py").write_text("# placeholder\n", encoding="utf-8")

        result = cc.cmd_invariants_wire_ci(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["written"] is False
        assert payload["workflowPath"] == ".github/workflows/controlcoding-invariants.yml"
        assert payload["command"] == "python scripts/cc.py invariants run --project-root ."
        assert "ControlCoding Invariants" in payload["workflow"]

    def test_invariants_wire_ci_write_creates_workflow(self, tmp_path, capsys):
        self._write_manifest(tmp_path)

        result = cc.cmd_invariants_wire_ci(
            tmp_path,
            command="python scripts/cc.py verify run --project-root .",
            write=True,
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["written"] is True
        workflow = (tmp_path / payload["workflowPath"]).read_text(encoding="utf-8")
        assert "python scripts/cc.py verify run --project-root ." in workflow

    def test_invariants_wire_ci_fails_without_manifest(self, tmp_path, capsys):
        result = cc.cmd_invariants_wire_ci(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert "controlcoding.invariants.json is missing" in payload["issues"]

    def test_main_routes_invariants_wire_ci_without_clobbering_root_command(self, tmp_path):
        argv = [
            "cc.py",
            "invariants",
            "wire-ci",
            "--project-root", str(tmp_path),
            "--command", "python scripts/cc.py verify run --project-root .",
            "--write",
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_invariants_wire_ci", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            provider="github-actions",
            command="python scripts/cc.py verify run --project-root .",
            output_path=None,
            write=True,
            force=False,
            json_output=True,
        )

    def test_invariants_status_fails_without_manifest(self, tmp_path, capsys):
        result = cc.cmd_invariants_status(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert "invariant manifest is missing" in payload["issues"]

    def test_invariants_status_accepts_active_executable_invariant(self, tmp_path, capsys):
        self._write_manifest(tmp_path)

        result = cc.cmd_invariants_status(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["summary"]["activeBlocking"] == 1
        assert payload["summary"]["executable"] == 1

    def test_invariants_add_appends_to_manifest(self, tmp_path, capsys):
        self._write_manifest(tmp_path, invariants=[])
        manifest = json.loads((tmp_path / "controlcoding.invariants.json").read_text(encoding="utf-8"))
        manifest["invariants"] = [
            {
                "id": "existing",
                "title": "Existing",
                "domain": "test-domain",
                "kind": "domain",
                "severity": "blocking",
                "status": "active",
                "property": "Existing property",
                "threshold": "0 failures",
                "command": "python -c \"print('ok')\"",
                "evidence": [],
            }
        ]
        (tmp_path / "controlcoding.invariants.json").write_text(json.dumps(manifest), encoding="utf-8")

        result = cc.cmd_invariants_add(
            tmp_path,
            invariant_id="new-property",
            domain="test-domain",
            property_text="New property",
            kind="consistency",
            command="python -c \"print('new')\"",
            threshold="0 failures",
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["total"] == 2
        assert any(item["id"] == "new-property" for item in payload["invariants"])

    def test_invariants_run_executes_active_invariants_and_writes_receipt(self, tmp_path, capsys):
        self._write_manifest(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = cc.cmd_invariants_run(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["status"] == "passed"
        assert len(payload["receipt"]["invariants"]) == 1
        assert payload["receipt"]["tempDir"]
        assert mock_run.call_count == 1
        assert (tmp_path / payload["receiptPath"]).exists()

    def test_invariants_run_keeps_temp_path_with_spaces_as_single_arg(self, tmp_path, capsys):
        project = tmp_path / "project with spaces"
        project.mkdir()
        self._write_manifest(
            project,
            invariants=[
                {
                    "id": "target-property",
                    "title": "Target Property",
                    "domain": "test-domain",
                    "kind": "domain",
                    "severity": "blocking",
                    "status": "active",
                    "property": "The target property must hold.",
                    "threshold": "0 failures",
                    "command": "python -c \"print('invariant')\" --artifact {temp}/payload",
                    "evidence": ["tests"],
                }
            ],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_invariants_run(project, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        args = mock_run.call_args.args[0]
        assert isinstance(args, list)
        assert mock_run.call_args.kwargs.get("shell") in (None, False)
        assert args[-2] == "--artifact"
        assert args[-1].endswith("/payload")
        assert payload["receipt"]["tempDir"] in args[-1]
        assert "{temp}" not in args[-1]
        assert "project with spaces" in args[-1]
        assert "with" not in args
        assert "spaces" not in args

    def test_invariants_run_keeps_quoted_json_with_spaces_as_single_arg(self, tmp_path, capsys):
        self._write_manifest(
            tmp_path,
            invariants=[
                {
                    "id": "target-property",
                    "title": "Target Property",
                    "domain": "test-domain",
                    "kind": "domain",
                    "severity": "blocking",
                    "status": "active",
                    "property": "The target property must hold.",
                    "threshold": "0 failures",
                    "command": "python -c \"print('invariant')\" --payload '{\"label\": \"two words\"}'",
                    "evidence": ["tests"],
                }
            ],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_invariants_run(tmp_path, json_output=True)

        assert result == 0
        capsys.readouterr()
        args = mock_run.call_args.args[0]
        payload_index = args.index("--payload") + 1
        assert args[payload_index] == '{"label": "two words"}'
        assert "two" not in args
        assert "words" not in args

    def test_invariants_report_names_protected_properties_and_latest_run(self, tmp_path, capsys):
        self._write_manifest(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            assert cc.cmd_invariants_run(tmp_path, json_output=True) == 0
        capsys.readouterr()

        result = cc.cmd_invariants_report(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["protectedProperties"][0]["id"] == "target-property"
        assert payload["protectedProperties"][0]["property"] == "The target property must hold."
        assert payload["protectedProperties"][0]["latestRun"]["status"] == "passed"
        assert payload["latestReceipt"]["status"] == "passed"

    def test_invariants_report_fails_without_manifest(self, tmp_path, capsys):
        result = cc.cmd_invariants_report(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["source"] == "missing"
        assert payload["protectedProperties"] == []

    def test_invariants_doctor_reports_missing_manifest(self, tmp_path, capsys):
        result = cc.cmd_invariants_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "missing"
        assert payload["controlLevel"] == "unavailable"
        assert payload["ok"] is False

    def test_invariants_doctor_reports_documented_only_manifest(self, tmp_path, capsys):
        self._write_manifest(
            tmp_path,
            invariants=[
                {
                    "id": "draft-property",
                    "title": "Draft Property",
                    "domain": "test-domain",
                    "kind": "domain",
                    "severity": "blocking",
                    "status": "draft",
                    "property": "The draft property should eventually hold.",
                    "threshold": "0 failures",
                    "command": "",
                    "evidence": [],
                }
            ],
        )

        result = cc.cmd_invariants_doctor(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "documented_only"
        assert payload["controlLevel"] == "advisory"
        assert "no active blocking invariants are declared" in payload["issues"]

    def test_invariants_doctor_reports_local_executable_without_ci(self, tmp_path, capsys):
        self._write_manifest(tmp_path)

        result = cc.cmd_invariants_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "local_executable"
        assert payload["controlLevel"] == "conditional"
        assert payload["ci"]["runsInvariantGate"] is False

    def test_invariants_doctor_reports_ci_wired_when_workflow_is_tracked(self, tmp_path, capsys, monkeypatch):
        self._write_manifest(tmp_path)
        workflow = tmp_path / ".github" / "workflows" / "controlcoding-invariants.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "name: ControlCoding Invariants\n"
            "jobs:\n"
            "  invariants:\n"
            "    steps:\n"
            "      - run: python scripts/cc.py invariants run --project-root .\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cc,
            "_release_git_tracked_files",
            lambda project: ([".github/workflows/controlcoding-invariants.yml"], ""),
        )

        result = cc.cmd_invariants_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "ci_wired"
        assert payload["controlLevel"] == "mechanical_once_committed_and_ci_enabled"
        assert payload["ci"]["runsInvariantGate"] is True
        assert payload["ci"]["gitTracked"] is True

    def test_main_routes_invariants_doctor(self, tmp_path):
        argv = [
            "cc.py",
            "invariants",
            "doctor",
            "--project-root", str(tmp_path),
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_invariants_doctor", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            json_output=True,
        )

    def test_invariants_run_fails_when_command_fails(self, tmp_path, capsys):
        self._write_manifest(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="failed")
            result = cc.cmd_invariants_run(tmp_path, json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["status"] == "failed"
        assert payload["receipt"]["status"] == "failed"
        assert payload["receipt"]["invariants"][0]["status"] == "failed"
        assert payload["receipt"]["invariants"][0]["returnCode"] == 1
        assert (tmp_path / payload["receiptPath"]).exists()
        assert isinstance(mock_run.call_args.args[0], list)
        assert mock_run.call_args.kwargs.get("shell") in (None, False)

    def test_doctor_strict_invariants_fails_without_manifest(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True, strict_invariants=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert checks["invariant_manifest"]["status"] == "fail"

    def test_doctor_strict_invariants_accepts_manifest(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        self._write_manifest(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True, strict_invariants=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["invariantManifest"]["ok"] is True
        assert payload["invariantDoctor"]["state"] == "local_executable"
        assert checks["invariant_manifest"]["status"] == "ok"


def test_p3c0_r4_architecture_index_absent_wrong_type_unreadable_and_unstable_are_unavailable(tmp_path, monkeypatch):
    def assert_unavailable(payload):
        assert payload["ok"] is False
        assert payload["available"] is False
        assert payload["path"] == "dev/ARCHITECTURE_INDEX.md"
        assert payload["missingCount"] is None
        assert payload["missing"] is None
        assert payload["issues"]

    assert_unavailable(cc._architecture_index_payload(tmp_path / "absent"))

    wrong_type = tmp_path / "wrong-type"
    (wrong_type / "dev" / "ARCHITECTURE_INDEX.md").mkdir(parents=True)
    assert_unavailable(cc._architecture_index_payload(wrong_type))

    index_file = tmp_path / "captured" / "dev" / "ARCHITECTURE_INDEX.md"
    index_file.parent.mkdir(parents=True)
    index_file.write_text("# Architecture Index\n", encoding="utf-8")
    for reason in ("architecture_index_unreadable", "architecture_index_unstable"):
        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                cc,
                "_capture_architecture_index_file",
                lambda path, label, observed_reason=reason: (None, None, observed_reason),
            )
            assert_unavailable(cc._architecture_index_payload(index_file.parents[1]))

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            cc,
            "_observed_architecture_index_payload",
            lambda project: (_ for _ in ()).throw(OSError("simulated observer failure")),
        )
        assert_unavailable(cc._architecture_index_payload(index_file.parents[1]))


def _p3c0_r4_write_architecture_index_fixture(
    project: Path,
    index_content: str,
    *tracked_paths: str,
) -> None:
    for relative_path in tracked_paths:
        filepath = project / relative_path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text('"""Index fixture."""\n', encoding="utf-8")
    index_path = project / "dev" / "ARCHITECTURE_INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_content, encoding="utf-8")


def _p3c0_r4_run_index_check(project: Path) -> int:
    argv = ["cc.py", "index", "--project-root", str(project), "--check"]
    with patch.object(sys, "argv", argv):
        return cc.main()


def test_p3c0_r4_index_check_rejects_basename_substring_only(tmp_path, capsys):
    _p3c0_r4_write_architecture_index_fixture(
        tmp_path,
        "cc.py\n",
        "scripts/cc.py",
    )

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    assert "malformed" in capsys.readouterr().out.lower()


def test_p3c0_r4_index_check_rejects_index_without_title(tmp_path, capsys):
    _p3c0_r4_write_architecture_index_fixture(
        tmp_path,
        """## CLI Tool (`scripts/`)

| File | What it does |
|---|---|
| `cc.py` | CLI fixture. |
""",
        "scripts/cc.py",
    )

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    assert "malformed" in capsys.readouterr().out.lower()


def test_p3c0_r4_index_check_rejects_index_without_required_sections(tmp_path, capsys):
    _p3c0_r4_write_architecture_index_fixture(
        tmp_path,
        """# Architecture Index

| File | What it does |
|---|---|
| `scripts/cc.py` | CLI fixture. |
""",
        "scripts/cc.py",
    )

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    assert "malformed" in capsys.readouterr().out.lower()


@pytest.mark.parametrize(
    "table",
    [
        "| File | What it does |\n| not-a-separator | --- |\n| `cc.py` | CLI fixture. |",
        "| File | What it does |\n|---|---|\n| cc.py | CLI fixture. |",
    ],
    ids=["malformed-separator", "unstructured-entry"],
)
def test_p3c0_r4_index_check_rejects_malformed_file_table(tmp_path, capsys, table):
    _p3c0_r4_write_architecture_index_fixture(
        tmp_path,
        f"# Architecture Index\n\n## CLI Tool (`scripts/`)\n\n{table}\n",
        "scripts/cc.py",
    )

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    assert "malformed" in capsys.readouterr().out.lower()


def test_p3c0_r4_index_check_rejects_ambiguous_basename_outside_canonical_scope(tmp_path, capsys):
    _p3c0_r4_write_architecture_index_fixture(
        tmp_path,
        """# Architecture Index

## CLI Tool (`scripts/`)

| File | What it does |
|---|---|
| `cc.py` | Main CLI fixture. |

## MCP Servers (`templates/scripts/`)

| File | What it does |
|---|---|
| `other.py` | The cc.py basename appears here only as prose. |
""",
        "scripts/cc.py",
        "templates/scripts/cc.py",
    )

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    output = capsys.readouterr().out
    assert "MCP Servers (`templates/scripts/`)" in output


def test_p3c0_r4_index_check_accepts_current_lab_canonical_index(capsys):
    project = CC_SCRIPT.parents[1]

    assert _p3c0_r4_run_index_check(project) == 0
    assert "up to date" in capsys.readouterr().out


def test_p3c0_r4_index_check_accepts_public_title_scope_and_full_path(tmp_path, capsys):
    _p3c0_r4_write_architecture_index_fixture(
        tmp_path,
        """# Public Architecture Index

## CLI Tool and Supporting Modules (`scripts/`)

| File | Public role |
|---|---|
| `scripts/cc.py` | Main CLI fixture. |
""",
        "scripts/cc.py",
    )

    assert _p3c0_r4_run_index_check(tmp_path) == 0
    assert "up to date" in capsys.readouterr().out


def test_p3c0_r4_index_check_fails_closed_for_absent_and_incomplete_index(tmp_path, capsys):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "cc.py").write_text('"""Index fixture."""\n', encoding="utf-8")

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    assert "No dev/ARCHITECTURE_INDEX.md" in capsys.readouterr().out

    index_path = tmp_path / "dev" / "ARCHITECTURE_INDEX.md"
    index_path.parent.mkdir()
    index_path.write_text("# Architecture Index\n", encoding="utf-8")

    assert _p3c0_r4_run_index_check(tmp_path) == 1
    assert "malformed" in capsys.readouterr().out.lower()


def test_p3c0_r4_candidate_release_excludes_controlwork_project_map():
    project = CC_SCRIPT.parents[1]
    relative = "docs/controlwork-project-map.md"
    manifest = json.loads((project / "controlcoding.release.json").read_text(encoding="utf-8"))
    docs_index = (project / "docs" / "INDEX.md").read_text(encoding="utf-8")

    assert not (project / relative).exists()
    assert relative not in manifest["required"]
    assert all(entry["pattern"] != relative for entry in manifest["allow"])
    assert any(entry["pattern"] == relative for entry in manifest["deny"])
    assert "controlwork-project-map.md" not in docs_index


def test_p3c0_r4_candidate_release_docs_reject_audit_hygiene_residues():
    project = CC_SCRIPT.parents[1]
    memory_schema = (project / "docs" / "memory-system-schema.md").read_text(encoding="utf-8")
    contributing = (project / "CONTRIBUTING.md").read_text(encoding="utf-8")
    public_docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((project / "docs").rglob("*.md"))
    )

    assert re.search(r"(?i)\b[a-z]:[\\/]", memory_schema) is None
    for forbidden in (
        "snapshot date:",
        "controlcoding laboratory state snapshot",
        "private checkout",
        "currently reports:\n563 entities",
        "initialized in the laboratory checkout",
    ):
        assert forbidden not in memory_schema.casefold()
    for forbidden in (
        "dev/methodology_full.md",
        "see `claude.md`",
        "new auditor skills",
        "manual editing outside claude code",
    ):
        assert forbidden not in contributing.casefold()
    assert "file:///" not in public_docs.casefold()
    assert "dev/ui-prototypes" not in public_docs.casefold()


def test_architecture_index_keeps_dev_precedence_when_present(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "cc.py").write_text("# CLI\n", encoding="utf-8")
    dev_index = tmp_path / "dev" / "ARCHITECTURE_INDEX.md"
    dev_index.parent.mkdir()
    dev_index.write_text(
        """# Architecture Index

## CLI Tool (`scripts/`)

| File | What it does |
|---|---|
| `cc.py` | Main CLI fixture. |
""",
        encoding="utf-8",
    )
    (tmp_path / "controlcoding.release.json").write_text(
        json.dumps({"architectureIndex": "docs/architecture-index.md"}),
        encoding="utf-8",
    )

    payload = cc._architecture_index_payload(tmp_path)

    assert payload["ok"] is True
    assert payload["path"] == "dev/ARCHITECTURE_INDEX.md"


class TestWorkStartCommand:
    def test_work_start_is_read_only_without_initialized_memory(self, tmp_path, capsys):
        before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

        result = cc.cmd_work_start(
            tmp_path,
            topic="release hygiene",
            scope="dev",
            json_output=True,
        )

        after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
        payload = json.loads(capsys.readouterr().out)

        assert result == 0
        assert before == after
        assert payload["command"] == "work-start"
        assert payload["readOnly"] is True
        assert payload["hiddenWrites"] is False
        assert payload["readiness"]["state"] == "YELLOW"
        assert payload["retrieval"]["available"] is False
        assert "Startup does not query SQLite" in payload["retrieval"]["message"]

    def test_work_start_json_escapes_unicode_payload_for_windows_stdout(self, tmp_path, capsys, monkeypatch):
        calls = []
        observation = {
            "state": "fresh",
            "color": "GREEN",
            "reason": "",
            "legacyStatus": {},
        }
        monkeypatch.setattr(cc, "observe_freshness", lambda project: observation)
        monkeypatch.setattr(
            cc,
            "_work_start_startup_payloads",
            lambda project, scope="dev", topic="", observation=None: (
                {"ok": True, "readOnly": True, "hiddenWrites": False},
                {
                    "ok": True,
                    "planes": {"project": {}},
                    "workingTree": {"trackedDirtyCount": 0, "changes": []},
                    "actionQueue": [],
                    "warnings": [],
                },
            ),
        )
        monkeypatch.setattr(
            cc,
            "_architecture_index_payload",
            lambda project: {"ok": True, "available": True, "missingCount": 0, "missing": [], "issues": []},
        )
        monkeypatch.setattr(cc, "_context_sync_payload", lambda project: {"ok": True, "hosts": []})
        monkeypatch.setattr(
            cc,
            "_work_start_retrieve_payload",
            lambda project, topic, scope, limit, observation=None: (
                calls.append(observation) or {
                "ok": True,
                "available": True,
                "query": topic,
                "scope": scope,
                "matches": [{"path": "notes.md", "title": "\ufeffRecovered context"}],
                }
            ),
        )

        result = cc.cmd_work_start(tmp_path, topic="encoding", scope="dev", json_output=True)
        output = capsys.readouterr().out
        payload = json.loads(output)

        assert result == 0
        assert calls == [observation]
        assert "\\ufeffRecovered context" in output
        assert payload["retrieval"]["matches"][0]["title"] == "\ufeffRecovered context"

    def test_work_and_chat_start_preserve_unknown_health_contract(self, tmp_path, capsys, monkeypatch):
        observation = {
            "state": "unknown",
            "color": "YELLOW",
            "reason": "projection_missing",
            "message": "Projection is missing.",
            "remediation": "Publish explicitly.",
            "readOnly": True,
            "sourceOfTruth": False,
            "projectionPath": str(tmp_path / ".controlcoding" / "memory" / "freshness_projection.json"),
            "capabilities": {"memoryHealthProjection": "available"},
            "legacyStatus": None,
        }
        work_start = {
            "ok": True,
            "command": "work-start",
            "projectRoot": str(tmp_path),
            "scope": "dev",
            "topic": "health",
            "limit": 10,
            "readOnly": True,
            "hiddenWrites": False,
            "health": observation,
            "readiness": {"ok": True, "state": "YELLOW", "redReasons": [], "yellowReasons": []},
            "retrieval": {"available": False, "matchCount": None, "matches": None},
            "projectPlane": {"present": False},
            "workingTree": {"trackedDirtyCount": None},
            "nextCommands": [],
        }
        monkeypatch.setattr(cc, "_build_work_start_payload", lambda project, topic, scope, limit: work_start)
        monkeypatch.setattr(
            cc,
            "_chat_start_project_packet",
            lambda project, scope, topic, limit, include_legacy: {"ok": False, "present": False, "message": "not loaded", "packetMarkdown": ""},
        )

        assert cc.cmd_chat_start(tmp_path, topic="health", scope="dev", json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["health"] == observation
        assert payload["workStart"]["health"] == observation

    def test_p3c0_r3_git_unknown_keeps_work_start_yellow_and_requests_diagnostic(self, tmp_path):
        payload = {
            "topic": "observational boundary",
            "scope": "dev",
            "limit": 10,
            "health": {
                "state": "unknown",
                "color": "YELLOW",
                "reason": "projection_missing",
            },
            "startup": {"ok": True},
            "opIndex": {"ok": True, "warnings": []},
            "architectureIndex": {"ok": True, "available": True},
            "context": {"ok": True},
            "retrieval": {"available": True},
            "projectPlane": {
                "available": False,
                "present": None,
                "embeddedVsExternalDifferent": None,
            },
            "workingTree": {
                "available": False,
                "trackedDirtyCount": None,
                "changes": None,
            },
        }

        payload["readiness"] = cc._work_start_readiness(payload)
        payload["nextCommands"] = cc._work_start_next_commands(payload)
        rendered = cc._work_start_text(payload)

        assert payload["readiness"]["state"] == "YELLOW"
        assert "working-tree status is unknown because Git was not observed" in payload["readiness"]["yellowReasons"]
        assert "git status --short" in payload["nextCommands"]
        assert "Project Plane present: unknown (not observed)" in rendered
        assert "ControlWork drift: unknown (not observed)" in rendered
        assert "Working tree dirty files: unknown (not observed)" in rendered
        assert "None" not in rendered

    def test_main_routes_work_start(self, tmp_path):
        argv = [
            "cc.py",
            "work-start",
            "--project-root", str(tmp_path),
            "--topic", "release hygiene",
            "--scope", "dev",
            "--limit", "3",
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_work_start", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            topic="release hygiene",
            scope="dev",
            limit=3,
            json_output=True,
        )


class TestChatStartCommand:
    def test_chat_start_prints_ai_ready_packet_without_writes(self, tmp_path, capsys, monkeypatch):
        before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

        monkeypatch.setattr(
            cc,
            "_build_work_start_payload",
            lambda project, topic, scope, limit: {
                "ok": True,
                "command": "work-start",
                "projectRoot": str(project),
                "scope": scope,
                "topic": topic,
                "limit": limit,
                "readOnly": True,
                "hiddenWrites": False,
                "readiness": {"ok": True, "state": "GREEN", "redReasons": [], "yellowReasons": []},
                "startup": {"ok": True},
                "opIndex": {"ok": True},
                "architectureIndex": {"ok": True},
                "context": {"ok": True},
                "projectPlane": {"present": True},
                "retrieval": {"available": True, "matchCount": 1, "matches": [{"title": "Decision"}]},
                "workingTree": {"trackedDirtyCount": 0},
                "nextCommands": [],
            },
        )
        monkeypatch.setattr(
            cc,
            "_chat_start_project_packet",
            lambda project, scope, topic, limit, include_legacy: {
                "ok": True,
                "present": True,
                "message": "Project Plane context packet loaded read-only.",
                "packetMarkdown": "# ControlWork Context Packet\n\n- Decision loaded.\n",
            },
        )

        result = cc.cmd_chat_start(tmp_path, topic="login", scope="implementation")

        after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
        output = capsys.readouterr().out

        assert result == 0
        assert before == after
        assert "Chat start packet" in output
        assert "do not ask the user to run this command for routine startup" in output
        assert "AI startup contract" in output
        assert "Keep current work, prior work, blockers, and next steps in context" in output
        assert "Project Plane packet: loaded" in output
        assert "# ControlWork Context Packet" in output

    def test_main_routes_chat_start(self, tmp_path):
        argv = [
            "cc.py",
            "chat-start",
            "--project-root", str(tmp_path),
            "--topic", "release hygiene",
            "--scope", "planning",
            "--limit", "4",
            "--include-legacy",
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_chat_start", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            topic="release hygiene",
            scope="planning",
            limit=4,
            include_legacy=True,
            json_output=True,
        )

    def test_chat_start_maps_dev_scope_to_project_plane_implementation(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLWORK.md").write_text("# Project\n", encoding="utf-8")
        seen = {}

        def fake_context_pack(project, scope, topic, limit, include_legacy, refresh_views):
            seen["scope"] = scope
            seen["refresh_views"] = refresh_views
            return "# Packet\n"

        monkeypatch.setattr(cc.work_features, "build_context_pack", fake_context_pack)

        payload = cc._chat_start_project_packet(
            tmp_path,
            scope="dev",
            topic="next task",
            limit=5,
            include_legacy=False,
        )

        assert payload["ok"] is True
        assert payload["scope"] == "implementation"
        assert seen == {"scope": "implementation", "refresh_views": False}

    def test_p3c0_r3_chat_start_does_not_build_packet_for_unobservable_project_plane(self, tmp_path, monkeypatch):
        observation = {
            "available": False,
            "state": "unknown",
            "color": "YELLOW",
            "reason": "project_plane_config_malformed",
            "message": "Project Plane config is malformed.",
        }

        def forbidden(*_args, **_kwargs):
            raise AssertionError("chat-start must not build a packet from unobservable inputs")

        monkeypatch.setattr(cc, "observe_project_plane_inputs", lambda project: observation)
        monkeypatch.setattr(cc.work_features, "build_context_pack", forbidden)

        payload = cc._chat_start_project_packet(
            tmp_path,
            scope="dev",
            topic="observational boundary",
            limit=5,
            include_legacy=False,
        )

        assert payload["ok"] is False
        assert payload["available"] is False
        assert payload["present"] is None
        assert payload["packetMarkdown"] is None
        assert payload["health"] == observation

    def test_p3c0_r3_chat_start_rejects_project_plane_change_during_packet(self, tmp_path, monkeypatch):
        observation = {
            "available": True,
            "state": "observed",
            "reason": "",
            "message": "Project Plane inputs were observed.",
        }
        captures = iter([
            ({"embedded": {"root": {"exists": True}, "context": {"exists": True}}, "generation": 1}, ""),
            ({"embedded": {"root": {"exists": True}, "context": {"exists": True}}, "generation": 2}, ""),
        ])
        monkeypatch.setattr(cc, "observe_project_plane_inputs", lambda project: observation)
        monkeypatch.setattr(cc, "capture_project_plane_inputs", lambda project: next(captures))
        monkeypatch.setattr(cc.work_features, "build_context_pack", lambda *args, **kwargs: "# Unstable packet\n")

        payload = cc._chat_start_project_packet(
            tmp_path,
            scope="dev",
            topic="observational boundary",
            limit=5,
            include_legacy=False,
        )

        assert payload["ok"] is False
        assert payload["available"] is False
        assert payload["present"] is None
        assert payload["packetMarkdown"] is None
        assert payload["health"]["state"] == "unknown"
        assert payload["health"]["color"] == "YELLOW"
        assert payload["health"]["reason"] == "project_plane_inputs_changed_during_packet"


class TestWorkCloseCommand:
    def test_work_close_is_read_only_and_reports_missing_summary(self, tmp_path, capsys):
        before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

        result = cc.cmd_work_close(tmp_path, topic="release hygiene", json_output=True)

        after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
        payload = json.loads(capsys.readouterr().out)

        assert result == 0
        assert before == after
        assert payload["command"] == "work-close"
        assert payload["readOnly"] is True
        assert payload["hiddenWrites"] is False
        assert payload["commit"]["automatic"] is False
        assert payload["readiness"]["state"] == "YELLOW"
        assert any("summary" in reason for reason in payload["readiness"]["yellowReasons"])

    def test_work_close_classifies_changed_files_and_requires_tests(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(
            cc,
            "_op_index_payload",
            lambda project, scope="dev", topic="": {
                "ok": True,
                "workingTree": {
                    "trackedDirtyCount": 3,
                    "changes": [
                        {"status": " M", "path": "scripts/cc.py"},
                        {"status": " M", "path": "tests/test_cc_cli.py"},
                        {"status": " M", "path": "CONTROLCODING.md"},
                    ],
                },
            },
        )
        monkeypatch.setattr(cc, "_context_sync_payload", lambda project: {"ok": True, "hosts": []})
        monkeypatch.setattr(
            cc,
            "_architecture_index_payload",
            lambda project: {"ok": True, "available": True, "missingCount": 0, "missing": [], "issues": []},
        )

        result = cc.cmd_work_close(
            tmp_path,
            topic="release hygiene",
            summary="Implemented closeout gate.",
            json_output=True,
        )

        payload = json.loads(capsys.readouterr().out)

        assert result == 0
        assert payload["areas"]["code"] == ["scripts/cc.py"]
        assert payload["areas"]["tests"] == ["tests/test_cc_cli.py"]
        assert payload["areas"]["canonical_context"] == ["CONTROLCODING.md"]
        assert any(action["kind"] == "test" for action in payload["requiredActions"])
        assert any("test evidence" in reason for reason in payload["readiness"]["yellowReasons"])

    def test_main_routes_work_close(self, tmp_path):
        argv = [
            "cc.py",
            "work-close",
            "--project-root", str(tmp_path),
            "--topic", "release hygiene",
            "--summary", "Implemented closeout gate.",
            "--test", "python -m pytest tests/test_cc_cli.py",
            "--next-action", "Implement release doctor.",
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_work_close", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            topic="release hygiene",
            summary="Implemented closeout gate.",
            tests=["python -m pytest tests/test_cc_cli.py"],
            next_action="Implement release doctor.",
            json_output=True,
        )


class TestFeatureStateMachine:
    def test_feature_start_writes_contract_and_enforces_wip_one(self, tmp_path, capsys):
        result = cc_feature.cmd_feature_start(
            tmp_path,
            feature_id="Payment Memory",
            title="Payment memory contract",
            objective="Keep payment memory work bounded.",
            scope="scripts/cc_feature.py",
            acceptance=["Feature has a work contract."],
            non_goal=["No application runtime memory changes."],
            check=["python -m pytest tests/test_cc_cli.py"],
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["inProgressCount"] == 1
        assert payload["feature"]["id"] == "payment-memory"
        assert payload["feature"]["state"] == "active"
        assert (tmp_path / ".controlcoding" / "features" / "features.json").exists()

        result = cc_feature.cmd_feature_start(
            tmp_path,
            feature_id="Other Work",
            title="Other work",
            objective="This should wait.",
            acceptance=["Other work is defined."],
            check=["python -m pytest tests/test_other.py"],
            json_output=True,
        )

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert any("WIP limit is 1" in issue for issue in payload["issues"])

    def test_feature_verify_run_moves_to_passing_then_complete(self, tmp_path, capsys):
        assert cc_feature.cmd_feature_start(
            tmp_path,
            feature_id="feature-a",
            title="Feature A",
            objective="Deliver feature A.",
            acceptance=["A works."],
            check=["python -c \"print('ok')\""],
            json_output=True,
        ) == 0
        capsys.readouterr()

        with patch("cc_feature.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            result = cc_feature.cmd_feature_verify(
                tmp_path,
                "feature-a",
                run_checks=["python -c \"print('ok')\""],
                evidence=["targeted test command passed"],
                json_output=True,
            )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["feature"]["state"] == "passing"
        assert payload["receipt"]["status"] == "passed"
        assert payload["receipt"]["runChecks"][0]["returnCode"] == 0
        mock_run.assert_called_once()

        result = cc_feature.cmd_feature_complete(
            tmp_path,
            "feature-a",
            summary="Feature A completed after verification.",
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["feature"]["state"] == "completed"
        assert payload["summary"]["inProgressCount"] == 0

    def test_feature_manual_evidence_does_not_allow_completion(self, tmp_path, capsys):
        assert cc_feature.cmd_feature_start(
            tmp_path,
            feature_id="manual-only",
            title="Manual only",
            objective="Record manual evidence without passing.",
            acceptance=["Manual evidence is stored."],
            check=["manual review"],
            json_output=True,
        ) == 0
        capsys.readouterr()

        assert cc_feature.cmd_feature_verify(
            tmp_path,
            "manual-only",
            evidence=["Reviewer looked at the output."],
            json_output=True,
        ) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["feature"]["state"] == "verifying"

        result = cc_feature.cmd_feature_complete(tmp_path, "manual-only", json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert "feature must be in passing state before completion" in payload["issues"]

    def test_feature_verify_failed_command_blocks_feature(self, tmp_path, capsys):
        assert cc_feature.cmd_feature_start(
            tmp_path,
            feature_id="failing-feature",
            title="Failing feature",
            objective="Show failed checks block completion.",
            acceptance=["Failure is recorded."],
            check=["python -c \"raise SystemExit(1)\""],
            json_output=True,
        ) == 0
        capsys.readouterr()

        with patch("cc_feature.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="failed")
            result = cc_feature.cmd_feature_verify(
                tmp_path,
                "failing-feature",
                run_checks=["python -c \"raise SystemExit(1)\""],
                json_output=True,
            )

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["feature"]["state"] == "blocked"
        assert payload["receipt"]["status"] == "failed"

    def test_doctor_json_includes_feature_state(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        assert cc_feature.cmd_feature_start(
            tmp_path,
            feature_id="doctor-feature",
            title="Doctor feature",
            objective="Expose feature state in doctor.",
            acceptance=["Doctor JSON includes feature state."],
            check=["python --version"],
            json_output=True,
        ) == 0
        capsys.readouterr()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert result in {0, 1}
        assert payload["featureState"]["initialized"] is True
        assert payload["featureState"]["currentFeature"]["id"] == "doctor-feature"
        assert checks["feature_state"]["status"] == "ok"

    def test_main_routes_feature_verify(self, tmp_path):
        argv = [
            "cc.py",
            "feature",
            "verify",
            "--project-root", str(tmp_path),
            "feature-a",
            "--run", "python -c \"print('ok')\"",
            "--evidence", "targeted test",
            "--json",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc_feature, "cmd_feature_verify", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(
            tmp_path.resolve(),
            feature_id="feature-a",
            run_checks=["python -c \"print('ok')\""],
            evidence=["targeted test"],
            json_output=True,
        )


class TestPromotionCommands:
    def _write_stable_prereqs(self, project: Path):
        contract = {
            "schemaVersion": 1,
            "requiredKinds": ["targeted", "regression", "invariant"],
            "suites": [
                {
                    "id": "targeted",
                    "kind": "targeted",
                    "required": True,
                    "command": "python -c \"print('targeted')\"",
                    "description": "Targeted validation",
                },
                {
                    "id": "regression",
                    "kind": "regression",
                    "required": True,
                    "command": "python -c \"print('regression')\"",
                    "description": "Regression validation",
                },
                {
                    "id": "invariant",
                    "kind": "invariant",
                    "required": True,
                    "command": "python -c \"print('invariant')\"",
                    "description": "Invariant validation",
                },
            ],
        }
        (project / "controlcoding.verification.json").write_text(
            json.dumps(contract, indent=2),
            encoding="utf-8",
        )
        manifest = {
            "schemaVersion": 1,
            "projectType": "test",
            "domains": ["architecture"],
            "invariants": [
                {
                    "id": "stable-promotion-safe",
                    "title": "Stable Promotion Safe",
                    "domain": "architecture",
                    "kind": "structural",
                    "severity": "blocking",
                    "status": "active",
                    "property": "Stable promotion requires explicit verification evidence.",
                    "threshold": "0 failures",
                    "command": "python -c \"print('ok')\"",
                    "evidence": ["controlcoding.verification.json"],
                }
            ],
        }
        (project / "controlcoding.invariants.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

    def test_promote_check_accepts_features_to_shared(self, tmp_path, capsys):
        source = tmp_path / "features" / "payments" / "retry.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")

        result = cc.cmd_promote_check(
            tmp_path,
            "features/payments/retry.py",
            target_zone="shared",
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["sourceZone"] == "features"
        assert payload["targetZone"] == "shared"
        assert payload["targetPath"] == "shared/payments/retry.py"

    def test_promote_plan_writes_local_manifest(self, tmp_path, capsys):
        source = tmp_path / "features" / "payments" / "retry.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")

        result = cc.cmd_promote_plan(
            tmp_path,
            "features/payments/retry.py",
            target_zone="shared",
            reason="Used by more than one feature.",
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "planned"
        manifest_path = tmp_path / payload["manifestPath"]
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["reason"] == "Used by more than one feature."
        assert manifest["targetPath"] == "shared/payments/retry.py"

    def test_promote_apply_moves_shared_to_stable_and_writes_adr(self, tmp_path, capsys):
        self._write_stable_prereqs(tmp_path)
        source = tmp_path / "shared" / "payments" / "retry.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")

        result = cc.cmd_promote_apply(
            tmp_path,
            "shared/payments/retry.py",
            target_zone="stable",
            reason="API has stabilized across consumers.",
            create_adr=True,
            json_output=True,
        )

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "applied"
        assert payload["targetPath"] == "stable/payments/retry.py"
        assert not source.exists()
        assert (tmp_path / "stable" / "payments" / "retry.py").exists()
        assert payload["adrPath"].startswith("docs/adr/ADR-")
        assert (tmp_path / payload["adrPath"]).exists()
        assert (tmp_path / payload["manifestPath"]).exists()

    def test_promote_apply_blocks_stable_without_adr(self, tmp_path, capsys):
        self._write_stable_prereqs(tmp_path)
        source = tmp_path / "shared" / "payments" / "retry.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")

        result = cc.cmd_promote_apply(
            tmp_path,
            "shared/payments/retry.py",
            target_zone="stable",
            json_output=True,
        )

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert "stable promotion requires --adr" in payload["issues"]
        assert source.exists()

    def test_promote_check_blocks_skipping_staged_path(self, tmp_path, capsys):
        source = tmp_path / "workspace" / "proto.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")

        result = cc.cmd_promote_check(
            tmp_path,
            "workspace/proto.py",
            target_zone="stable",
            json_output=True,
        )

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert any("workspace -> features" in issue for issue in payload["issues"])

    def test_doctor_reports_promotion_path_manifests(self, tmp_path, capsys):
        _make_healthy_project(tmp_path)
        source = tmp_path / "features" / "payments" / "retry.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")
        assert cc.cmd_promote_plan(
            tmp_path,
            "features/payments/retry.py",
            target_zone="shared",
            json_output=True,
        ) == 0
        capsys.readouterr()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cc.cmd_doctor(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {entry["name"]: entry for entry in payload["checks"]}
        assert payload["promotionPath"]["count"] == 1
        assert checks["promotion_path"]["status"] == "ok"


class TestSetupHostContextSync:
    def test_setup_generates_codex_agents_md(self, tmp_path, monkeypatch):
        answers_file = tmp_path / "setup_answers.json"
        answers_file.write_text(
            json.dumps(
                {
                    "setup": {
                        "name": "Demo Project",
                        "planning": {
                            "tier": "core",
                            "planning_mode": "solo_structured",
                            "planning_authority": "single_author",
                            "manual_consultation_allowed": False,
                        },
                        "documentation_mode": "managed",
                        "user_host": "codex_cli",
                        "host_instruction_mode": "recommended",
                        "hooks_location": "local",
                    }
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(cc_setup, "_ask", lambda prompt, default="": default)
        monkeypatch.setattr(cc_setup, "_ask_yn", lambda prompt, default=True: default)
        monkeypatch.setattr(cc_setup, "_scan_dirs", lambda project: [])
        monkeypatch.setattr(cc_setup, "_detect_backends", lambda: {})
        monkeypatch.setattr(
            cc_setup,
            "_detect_obvious_environment_inventory",
            lambda: {"toolchains": ["Python"], "package_managers": ["pip"]},
        )
        monkeypatch.setattr(cc_setup, "cmd_init", lambda project, central_hooks=False, quiet=False: 0)
        monkeypatch.setattr(cc_setup, "cmd_doctor", lambda project: 0)
        monkeypatch.setattr(cc_setup, "_write_host_integration_assets", lambda project, user_host, host_instructions=None: [])

        result = cc_setup.cmd_setup(tmp_path, answers_file=answers_file)

        assert result == 0
        assert (tmp_path / "CONTROLCODING.md").exists()
        assert (tmp_path / "AGENTS.md").exists()
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "dev" / "design" / "01_DSN_ProjectFoundation_InProgress.md").exists()
        assert not (tmp_path / "dev" / "plans" / "01_DEV_InitialImplementationPlan_Plan.md").exists()
        assert not (tmp_path / "ROADMAP.md").exists()
        assert not (tmp_path / "BUGS.md").exists()
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "## Project Identity" in content
        assert "## Operative Rules" in content
        gateway = json.loads((tmp_path / ".controlcoding" / "gateway_config.json").read_text(encoding="utf-8"))
        cc_config = json.loads((tmp_path / ".controlcoding" / "cc_config.json").read_text(encoding="utf-8"))
        assert gateway["userHost"] == "codex_cli"
        assert gateway["enabledHosts"] == ["codex_cli"]
        assert gateway["hostProfile"]["userHost"] == "codex_cli"
        assert gateway["hostProfile"]["capabilityClass"] == "sandbox_approval"
        assert gateway["hostProfile"]["contextFile"] == "AGENTS.md"
        assert cc_config["project_definition_mode"] == "skip"
        assert "environment" not in cc_config


class TestHostSwitch:
    SAMPLE_CLAUDE_MD = TestExportAgentsMd.SAMPLE_CLAUDE_MD

    def test_main_host_parent_project_root_routes_to_subcommand(self, tmp_path):
        argv = [
            "cc.py",
            "host",
            "--project-root", str(tmp_path),
            "switch",
            "codex_cli",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_host_switch", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(tmp_path.resolve(), "codex_cli", preview_only=False)

        status_argv = [
            "cc.py",
            "host",
            "--project-root", str(tmp_path),
            "status",
            "--json",
        ]
        with patch.object(sys, "argv", status_argv), patch.object(cc, "cmd_host_status", return_value=0) as mock_status:
            assert cc.main() == 0
        mock_status.assert_called_once_with(tmp_path.resolve(), json_output=True)

    def test_main_host_subcommand_project_root_still_routes_to_subcommand(self, tmp_path):
        argv = [
            "cc.py",
            "host",
            "switch",
            "--project-root", str(tmp_path),
            "codex_cli",
        ]
        with patch.object(sys, "argv", argv), patch.object(cc, "cmd_host_switch", return_value=0) as mock_cmd:
            assert cc.main() == 0
        mock_cmd.assert_called_once_with(tmp_path.resolve(), "codex_cli", preview_only=False)

    def test_switch_updates_primary_host_and_syncs_context(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        claude_dir = tmp_path / ".controlcoding"
        claude_dir.mkdir()
        (claude_dir / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "claude_code",
                    "enabledHosts": ["claude_code", "gemini_cli"],
                    "hostInstructions": {"mode": "recommended", "customNotes": []},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cc,
            "_write_host_integration_assets",
            lambda project, user_host, host_instructions=None: [],
        )

        result = cc.cmd_host_switch(tmp_path, "codex_cli")

        assert result == 0
        gateway = json.loads((claude_dir / "gateway_config.json").read_text(encoding="utf-8"))
        assert gateway["userHost"] == "codex_cli"
        assert gateway["enabledHosts"] == ["codex_cli", "claude_code", "gemini_cli"]
        assert gateway["hostProfile"]["userHost"] == "codex_cli"
        assert gateway["hostProfile"]["capabilityClass"] == "sandbox_approval"
        assert (tmp_path / "AGENTS.md").exists()
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "## Operative Rules" in content

    def test_switch_preview_only_changes_nothing(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        gateway = control_dir / "gateway_config.json"
        original = json.dumps({"userHost": "claude_code", "enabledHosts": ["claude_code"]}, indent=2)
        gateway.write_text(original, encoding="utf-8")
        monkeypatch.setattr(cc, "_write_host_integration_assets", lambda *args, **kwargs: [])

        assert cc.cmd_host_switch(tmp_path, "codex_cli", preview_only=True) == 0

        assert gateway.read_text(encoding="utf-8") == original
        assert not (tmp_path / "AGENTS.md").exists()

    def test_switch_foreign_adapter_blocks_adapter_write(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        gateway = control_dir / "gateway_config.json"
        original = json.dumps({"userHost": "claude_code", "enabledHosts": ["claude_code"]}, indent=2)
        gateway.write_text(original, encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("user-owned\n", encoding="utf-8")
        monkeypatch.setattr(cc, "_write_json_atomic", lambda *args, **kwargs: None)
        monkeypatch.setattr(cc, "_write_host_integration_assets", lambda *args, **kwargs: [])

        assert cc.cmd_host_switch(tmp_path, "codex_cli") == 1

        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "user-owned\n"

    def test_switch_does_not_silently_rebind_owned_adapter_source(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"),
            encoding="utf-8",
        )
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        gateway = control_dir / "gateway_config.json"
        original_gateway = json.dumps(
            {"userHost": "claude_code", "enabledHosts": ["claude_code"]},
            indent=2,
        )
        gateway.write_text(original_gateway, encoding="utf-8")
        target = tmp_path / "AGENTS.md"
        original_adapter = (
            "# AGENTS.md\n\n"
            + cc._adapter_marker_line(
                "AGENTS.md",
                "codex_cli",
                "CONTROLWORK.md",
                "host-context-section-export-v1",
            )
            + "\n\nowned CONTROLWORK adapter\n"
        )
        target.write_text(original_adapter, encoding="utf-8")
        monkeypatch.setattr(cc, "_write_json_atomic", lambda *args, **kwargs: None)
        monkeypatch.setattr(cc, "_write_host_integration_assets", lambda *args, **kwargs: [])

        assert cc.cmd_host_switch(tmp_path, "codex_cli") == 1
        assert target.read_text(encoding="utf-8") == original_adapter

    def test_switch_propagates_adapter_transaction_failure(self, tmp_path, monkeypatch):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CLAUDE_MD.replace("CLAUDE.md", "CONTROLCODING.md"), encoding="utf-8")
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        gateway = control_dir / "gateway_config.json"
        original_gateway = json.dumps({"userHost": "claude_code", "enabledHosts": ["claude_code"]}, indent=2)
        gateway.write_text(original_gateway, encoding="utf-8")
        monkeypatch.setattr(cc, "_write_json_atomic", lambda *args, **kwargs: None)
        monkeypatch.setattr(cc, "_write_host_integration_assets", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            cc,
            "_apply_adapter_transaction",
            lambda *args, **kwargs: {
                "ok": False,
                "applied": False,
                "attempted": False,
                "rollbackOk": True,
                "cleanupOk": True,
                "error": "injected adapter-only failure",
                "written": [],
            },
        )

        assert cc.cmd_host_switch(tmp_path, "codex_cli") == 1
        assert not (tmp_path / "AGENTS.md").exists()

    def test_host_status_json_reports_enabled_hosts(self, tmp_path, capsys):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli", "gemini_cli"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = cc.cmd_host_status(tmp_path, json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["primaryHost"] == "codex_cli"
        assert payload["enabledHosts"] == ["codex_cli", "gemini_cli"]
        assert payload["hostProfile"]["userHost"] == "codex_cli"
        assert payload["hostProfile"]["capabilityClass"] == "sandbox_approval"
        assert payload["hostProfile"]["contextFile"] == "AGENTS.md"
        assert any(entry["id"] == "repo_boundary_gate" and entry["primary"] for entry in payload["gateContract"])




class TestSlice1PreviewOnlyRouting:
    SAMPLE_CONTEXT = TestExportAgentsMd.SAMPLE_CLAUDE_MD.replace(
        "CLAUDE.md",
        "CONTROLCODING.md",
    )

    def test_f04_c01_context_export_preview_only_never_reaches_mutation_boundary(
        self,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CONTEXT,
            encoding="utf-8",
        )

        def forbidden_mutation(*args, **kwargs):
            raise AssertionError("preview-only export reached mutation boundary")

        monkeypatch.setattr(cc, "_apply_text_transaction", forbidden_mutation)
        assert cc.cmd_export_host_context(
            tmp_path,
            host="cursor",
            preview_only=True,
        ) == 0
        assert not (tmp_path / ".cursor").exists()
        assert not list(tmp_path.glob(".*.controlcoding.*"))

    def test_f04_c02_context_sync_preview_only_never_reaches_mutation_boundary(
        self,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CONTEXT,
            encoding="utf-8",
        )

        def forbidden_mutation(*args, **kwargs):
            raise AssertionError("preview-only sync reached mutation boundary")

        monkeypatch.setattr(cc, "_apply_text_transaction", forbidden_mutation)
        assert cc.cmd_context_sync(
            tmp_path,
            all_hosts=True,
            preview_only=True,
        ) == 0
        for relative in (
            "CLAUDE.md",
            "AGENTS.md",
            "GEMINI.md",
            ".clinerules",
            ".cursor",
            ".windsurfrules",
        ):
            assert not (tmp_path / relative).exists()

    def test_f04_c03_context_switch_preview_only_keeps_gateway_assets_and_adapter_absent(
        self,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CONTEXT,
            encoding="utf-8",
        )
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        gateway = control_dir / "gateway_config.json"
        original = '{"userHost":"claude_code","enabledHosts":["claude_code"]}\n'
        gateway.write_text(original, encoding="utf-8")
        monkeypatch.setattr(
            cc,
            "_build_host_integration_assets",
            lambda *args, **kwargs: {".controlcoding/launchers/new.txt": "new\n"},
        )

        def forbidden_mutation(*args, **kwargs):
            raise AssertionError("preview-only switch reached mutation boundary")

        monkeypatch.setattr(cc, "_apply_text_transaction", forbidden_mutation)
        assert cc.cmd_host_switch(tmp_path, "codex_cli", preview_only=True) == 0
        assert gateway.read_text(encoding="utf-8") == original
        assert not (tmp_path / "AGENTS.md").exists()
        assert not (control_dir / "launchers").exists()

    def test_f04_c03b_switch_routes_adapter_transaction_separately(
        self,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CONTEXT,
            encoding="utf-8",
        )
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        gateway = control_dir / "gateway_config.json"
        gateway.write_text('{"userHost":"claude_code"}\n', encoding="utf-8")
        adapter_calls = []

        def record_adapter(project, entries):
            adapter_calls.append(entries)
            assert entries
            assert all(entry["kind"] == "adapter" for entry in entries)
            return {
                "ok": True,
                "applied": True,
                "attempted": True,
                "rolledBack": False,
                "rollbackOk": True,
                "cleanupOk": True,
                "error": "",
                "written": ["AGENTS.md"],
            }

        monkeypatch.setattr(cc, "_write_json_atomic", lambda *args, **kwargs: None)
        monkeypatch.setattr(cc, "_write_host_integration_assets", lambda *args, **kwargs: [])
        monkeypatch.setattr(cc, "_apply_adapter_transaction", record_adapter)

        assert cc.cmd_host_switch(tmp_path, "codex_cli") == 0
        assert len(adapter_calls) == 1

    def test_f04_c04_only_valid_adopt_apply_reaches_mutation_boundary(
        self,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CONTEXT,
            encoding="utf-8",
        )
        target = tmp_path / "AGENTS.md"
        target.write_text("foreign adapter\n", encoding="utf-8")
        calls = []

        def record_mutation(project, entries):
            calls.append(entries)
            return {
                "ok": True,
                "applied": True,
                "attempted": True,
                "rolledBack": False,
                "rollbackOk": True,
                "cleanupOk": True,
                "written": ["AGENTS.md"],
            }

        monkeypatch.setattr(cc, "_apply_text_transaction", record_mutation)
        assert cc.cmd_context_adopt(tmp_path, host="codex_cli") == 0
        assert calls == []
        assert target.read_text(encoding="utf-8") == "foreign adapter\n"
        assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 0
        assert len(calls) == 1
        assert calls[0][0]["action"] == "adopt"

        calls.clear()
        ambiguous = (
            '<!-- controlcoding-managed: {"owner":"ControlCoding",'
            '"owner":"ControlCoding"} -->\n'
        )
        target.write_text(ambiguous, encoding="utf-8")
        assert cc.cmd_context_adopt(tmp_path, host="codex_cli", apply=True) == 1
        assert calls == []
        assert target.read_text(encoding="utf-8") == ambiguous

    def test_f04_c05_force_command_matrix_routes_flags_but_never_bypasses_ambiguity(
        self,
        tmp_path,
        monkeypatch,
    ):
        agents_argv = [
            "cc.py",
            "export",
            "agents-md",
            "--project-root", str(tmp_path),
            "--force",
            "--preview-only",
        ]
        with patch.object(sys, "argv", agents_argv), patch.object(
            cc,
            "cmd_export_agents_md",
            return_value=0,
        ) as agents_export:
            assert cc.main() == 0
        agents_export.assert_called_once_with(
            tmp_path.resolve(),
            force=True,
            preview_only=True,
            source="",
        )

        host_argv = [
            "cc.py",
            "export",
            "host-context",
            "--project-root", str(tmp_path),
            "--host", "codex_cli",
            "--force",
            "--preview-only",
        ]
        with patch.object(sys, "argv", host_argv), patch.object(
            cc,
            "cmd_export_host_context",
            return_value=0,
        ) as host_export:
            assert cc.main() == 0
        host_export.assert_called_once_with(
            tmp_path.resolve(),
            host="codex_cli",
            force=True,
            source="",
            preview_only=True,
        )

        (tmp_path / "CONTROLCODING.md").write_text(
            self.SAMPLE_CONTEXT,
            encoding="utf-8",
        )
        target = tmp_path / "AGENTS.md"
        ambiguous = (
            '<!-- controlcoding-managed: {"owner":"ControlCoding",'
            '"owner":"ControlCoding"} -->\n'
        )
        target.write_text(ambiguous, encoding="utf-8")

        def forbidden_mutation(*args, **kwargs):
            raise AssertionError("ambiguous marker bypassed force preflight")

        monkeypatch.setattr(cc, "_apply_text_transaction", forbidden_mutation)
        assert cc.cmd_export_agents_md(tmp_path, force=True) == 1
        assert cc.cmd_export_host_context(
            tmp_path,
            host="codex_cli",
            force=True,
        ) == 1
        assert target.read_text(encoding="utf-8") == ambiguous




def test_f06_f04_canonical_authority_and_selected_controlwork_source_remain_distinct(
    tmp_path,
):
    canonical = TestExportAgentsMd.SAMPLE_CLAUDE_MD.replace(
        "CLAUDE.md",
        "CONTROLCODING.md",
    )
    controlwork = canonical.replace("CONTROLCODING.md", "CONTROLWORK.md")
    (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
    (tmp_path / "CONTROLWORK.md").write_text(controlwork, encoding="utf-8")

    assert cc._host_context_source_state(
        tmp_path,
        source="CONTROLCODING.md",
    ) == "canonical"
    assert cc._host_context_source_state(
        tmp_path,
        source="CONTROLWORK.md",
    ) == "explicit"
    assert cc.cmd_export_agents_md(
        tmp_path,
        source="CONTROLWORK.md",
    ) == 0

    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Selected source: CONTROLWORK.md (non-canonical)." in content
    assert "Canonical source of truth: CONTROLWORK.md" not in content
    marker, state, _detail = cc._adapter_marker_payload(content)
    assert state == "owned"
    assert marker["source"] == "CONTROLWORK.md"

    payload = cc._context_sync_payload(
        tmp_path,
        host="codex_cli",
        source="CONTROLWORK.md",
    )
    assert payload["sourceState"] == "explicit"
    assert payload["sourcePath"] == "CONTROLWORK.md"
    assert payload["canonicalPath"] == "CONTROLCODING.md"


@pytest.mark.parametrize(
    "managed_gap",
    [
        "\n\n\n",
        "\n\n\n\n",
        (
            "\n\n"
            "> Generated from CONTROLCODING.md by ControlCoding host-context export.\n"
            "> Target host: Claude Code.\n"
            "> Canonical source of truth: CONTROLCODING.md.\n\n"
        ),
    ],
    ids=["two_blank_lines", "three_blank_lines", "recognized_provenance"],
)
def test_f06_f03_inverse_full_copy_layout_blocks_all_real_command_routers_before_mutation(
    tmp_path,
    monkeypatch,
    managed_gap,
):
    canonical = TestExportAgentsMd.SAMPLE_CLAUDE_MD.replace(
        "CLAUDE.md",
        "CONTROLCODING.md",
    )
    (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
    marker_line = cc._adapter_marker_line(
        "CLAUDE.md",
        "claude_code",
        "CONTROLCODING.md",
        "host-context-full-copy-v1",
    )
    inverse = marker_line + managed_gap + (
        "---\ntitle: inverse fixture\n---\n# Existing payload\n"
    )
    target = tmp_path / "CLAUDE.md"
    target.write_text(inverse, encoding="utf-8")

    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    gateway = control_dir / "gateway_config.json"
    gateway_text = '{"userHost":"codex_cli","enabledHosts":["codex_cli"]}\n'
    gateway.write_text(gateway_text, encoding="utf-8")

    plan = cc._adapter_plan_payload(
        tmp_path,
        host="claude_code",
        operation="switch",
    )
    entry = plan["entries"][0]
    assert plan["preflightOk"] is False
    assert entry["state"] == "invalid"
    assert entry["ownership"] == "invalid"
    assert entry["action"] == "block"

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("inverse full-copy layout reached a mutation boundary")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    monkeypatch.setattr(cc_setup, "_apply_adapter_transaction", forbidden_mutation)
    monkeypatch.setattr(cc, "_write_json_atomic", forbidden_mutation)
    monkeypatch.setattr(cc, "_write_host_integration_assets", forbidden_mutation)

    assert cc.cmd_export_host_context(
        tmp_path,
        host="claude_code",
        force=True,
    ) == 1
    assert cc.cmd_context_sync(tmp_path, host="claude_code") == 1
    assert cc.cmd_host_switch(tmp_path, "claude_code") == 1
    assert cc_setup._sync_host_context_file(tmp_path, "claude_code") is None
    assert cc.cmd_context_adopt(
        tmp_path,
        host="claude_code",
        apply=True,
    ) == 1

    assert target.read_text(encoding="utf-8") == inverse
    assert gateway.read_text(encoding="utf-8") == gateway_text
    assert not (control_dir / "launchers").exists()


def test_m01_res01_selected_source_inverse_full_copy_blocks_force_and_adoption(
    tmp_path,
    monkeypatch,
):
    source_identity = "CONTROLWORK.md"
    source = tmp_path / source_identity
    source.write_text(
        TestExportAgentsMd.SAMPLE_CLAUDE_MD.replace(
            "CLAUDE.md",
            source_identity,
        ),
        encoding="utf-8",
    )
    marker_line = cc._adapter_marker_line(
        "CLAUDE.md",
        "claude_code",
        source_identity,
        "host-context-full-copy-v1",
    )
    provenance = cc._adapter_source_authority_line(source_identity)
    assert provenance == "> Selected source: CONTROLWORK.md (non-canonical)."
    inverse = (
        marker_line
        + "\n"
        + provenance
        + "\n---\ntitle: selected source fixture\n---\n# Existing payload\n"
    )
    target = tmp_path / "CLAUDE.md"
    target.write_text(inverse, encoding="utf-8")
    source_before = source.read_bytes()
    target_before = target.read_bytes()

    payload_index = cc._adapter_full_copy_payload_line_index(inverse)
    assert inverse.splitlines()[payload_index] == "---"
    marker, state, detail = cc._adapter_marker_payload(inverse)
    assert marker is None
    assert state == "invalid"
    assert state not in {"unmarked", "foreign", "ambiguous"}
    assert "front matter must precede" in detail

    export_plan = cc._adapter_plan_payload(
        tmp_path,
        host="claude_code",
        source=source_identity,
        operation="export",
        force=True,
    )
    adopt_plan = cc._adapter_plan_payload(
        tmp_path,
        host="claude_code",
        source=source_identity,
        operation="adopt",
    )
    for plan in (export_plan, adopt_plan):
        entry = plan["entries"][0]
        assert plan["preflightOk"] is False
        assert entry["source"] == source_identity
        assert entry["managedFormat"] == "host-context-full-copy-v1"
        assert entry["state"] == "invalid"
        assert entry["ownership"] == "invalid"
        assert entry["bindingAllowed"] is False
        assert entry["action"] == "block"

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("selected-source inverse layout reached mutation")

    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_mutation)
    assert cc.cmd_export_host_context(
        tmp_path,
        host="claude_code",
        source=source_identity,
        force=True,
    ) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="claude_code",
        source=source_identity,
        apply=True,
    ) == 1
    assert source.is_file()
    assert source.read_bytes() == source_before
    assert target.is_file()
    assert target.read_bytes() == target_before


@pytest.mark.parametrize("state", sorted(cc._ADAPTER_NORMATIVE_STATES))
def test_f06_command_router_matrix_covers_all_eight_normative_states_without_writes(
    tmp_path,
    monkeypatch,
    state,
):
    canonical = TestExportAgentsMd.SAMPLE_CLAUDE_MD.replace(
        "CLAUDE.md",
        "CONTROLCODING.md",
    )
    (tmp_path / "CONTROLCODING.md").write_text(canonical, encoding="utf-8")
    target = tmp_path / "AGENTS.md"
    expected = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="sync",
    )["entries"][0]["_expectedContent"]
    if state == "owned_current":
        target.write_bytes(expected.encode("utf-8"))
    elif state == "owned_stale":
        target.write_text(expected + "stale payload\n", encoding="utf-8")
    elif state == "unmarked":
        target.write_text(
            "# AGENTS.md\n\nunmarked payload\n",
            encoding="utf-8",
        )
    elif state == "foreign":
        target.write_text(
            "# AGENTS.md\n\n<!-- managed-by: OtherTool -->\nforeign payload\n",
            encoding="utf-8",
        )
    elif state == "invalid":
        target.write_text(
            "# AGENTS.md\n\n<!-- controlcoding-managed {} -->\ninvalid payload\n",
            encoding="utf-8",
        )
    elif state == "ambiguous":
        target.write_text(
            "# AGENTS.md\n\n"
            "<!-- controlcoding-managed -->\n"
            "<!-- controlcoding-managed=not-json -->\n",
            encoding="utf-8",
        )
    elif state == "unreadable_or_unsafe":
        target.mkdir()
    elif state != "target_absent":
        raise AssertionError(f"unsupported normative state: {state}")

    classified = cc._adapter_plan_payload(
        tmp_path,
        host="codex_cli",
        operation="sync",
    )["entries"][0]
    assert classified["state"] == state
    before = target.read_bytes() if target.is_file() else None
    before_is_dir = target.is_dir()
    transaction_calls = []

    def observe_setup_transaction(project, entries):
        transaction_calls.append(entries)
        return {
            "ok": True,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "error": "",
            "written": [],
        }

    monkeypatch.setattr(cc, "_apply_adapter_transaction", observe_setup_transaction)
    monkeypatch.setattr(
        cc_setup,
        "_apply_adapter_transaction",
        observe_setup_transaction,
    )

    export_results = {
        "target_absent": (0, 0),
        "owned_current": (0, 0),
        "owned_stale": (1, 0),
        "unmarked": (1, 1),
        "foreign": (1, 1),
        "invalid": (1, 1),
        "ambiguous": (1, 1),
        "unreadable_or_unsafe": (1, 1),
    }
    update_states = {"target_absent", "owned_current", "owned_stale"}
    adoptable_states = update_states | {"unmarked", "foreign"}

    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        preview_only=True,
    ) == export_results[state][0]
    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
        preview_only=True,
    ) == export_results[state][1]
    assert cc.cmd_context_sync(
        tmp_path,
        host="codex_cli",
        preview_only=True,
    ) == (0 if state in update_states else 1)
    assert cc.cmd_host_switch(
        tmp_path,
        "codex_cli",
        preview_only=True,
    ) == (0 if state in update_states else 1)

    setup_result = cc_setup._sync_host_context_file(tmp_path, "codex_cli")
    assert setup_result == ("AGENTS.md" if state in update_states else None)
    assert len(transaction_calls) == (1 if state in update_states else 0)
    if transaction_calls:
        expected_setup_action = {
            "target_absent": "create",
            "owned_current": "noop",
            "owned_stale": "update",
        }[state]
        assert transaction_calls[0][0]["action"] == expected_setup_action

    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=False,
    ) == (0 if state in adoptable_states else 1)
    assert not (tmp_path / ".controlcoding" / "gateway_config.json").exists()
    assert target.is_dir() is before_is_dir
    if before is None:
        assert not target.exists() or target.is_dir()
    else:
        assert target.read_bytes() == before


def _s4_static_file_state(path: Path) -> tuple[bytes, int, tuple[int, ...], int]:
    file_stat = os.lstat(path)
    return (
        path.read_bytes(),
        stat.S_IMODE(file_stat.st_mode),
        cc._transaction_file_object_key(file_stat),
        stat.S_IFMT(file_stat.st_mode),
    )


def _s4_static_write_source(project: Path) -> Path:
    source = project / "CONTROLCODING.md"
    source.write_text(
        "# ControlCoding fixture\n\n"
        "## Project Identity\n\n"
        "- **Name**: Static finding fixture\n\n"
        "## Architecture Rules\n\n"
        "1. Keep the fixture deterministic.\n",
        encoding="utf-8",
    )
    return source


def _s4_static_marker_json(**extra) -> str:
    marker = {
        "owner": "ControlCoding",
        "schema": "controlcoding.host-adapter-ownership",
        "version": 1,
        "target": "AGENTS.md",
        "host": "codex_cli",
        "source": "CONTROLCODING.md",
        "format": "host-context-section-export-v1",
    }
    marker.update(extra)
    return json.dumps(marker, sort_keys=True, separators=(",", ":"))


def _s4_static_marker_content(encoded: str) -> str:
    return (
        "# AGENTS.md\n\n"
        f"<!-- controlcoding-managed: {encoded} -->\n\n"
        "Adapter payload.\n"
    )


def test_s4_stat_01_post_mkdir_fault_keeps_provisional_directory_visible_to_cleanup(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "provisional-parent"
    target = parent / "target.txt"
    entry = cc._text_write_entry(tmp_path, target, "transaction payload\n")
    observed = {}

    def fail_after_mkdir(name, **context):
        if name == "after_parent_create":
            observed["record"] = context["record"]
            assert parent.is_dir()
            assert context["record"]["creationState"] == "created"
            assert context["record"]["identityState"] == "unavailable"
            raise OSError("injected fault immediately after mkdir")

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_after_mkdir)
    result = cc._apply_text_transaction(tmp_path, [entry])

    record = observed["record"]
    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["rollbackOk"] is True
    assert result["cleanupOk"] is False
    assert result["written"] == []
    assert result["cleanupErrors"]
    assert str(parent) in result["cleanupErrors"][0]
    assert "identity is unavailable" in result["cleanupErrors"][0]
    assert record["cleanupState"] == "preserved"
    assert parent.is_dir()
    assert list(parent.iterdir()) == []
    assert not target.exists()


def test_s4_stat_01_replaced_created_directory_is_preserved_identity_safe(
    tmp_path,
    monkeypatch,
):
    ancestor = tmp_path / "parent-provenance"
    ancestor.mkdir()
    parent = ancestor / "replaceable-parent"
    target = parent / "target.txt"
    retired_ancestor = tmp_path / "retired-parent-provenance"
    retired_parent = retired_ancestor / parent.name
    entry = cc._text_write_entry(tmp_path, target, "transaction payload\n")
    foreign_bytes = b"foreign parent object\n"
    observed = {}
    rmdir_calls = []
    original_provisional_record = cc._transaction_provisional_record
    original_rmdir = Path.rmdir

    def observe_provisional_record(path, role, parent_snapshots=None):
        record = original_provisional_record(path, role, parent_snapshots)
        if role == "directory":
            observed["record"] = record
        return record

    def observe_rmdir(path, *args, **kwargs):
        rmdir_calls.append(path)
        return original_rmdir(path, *args, **kwargs)

    def replace_after_identity(name, **context):
        if name == "before_parent_create":
            record = observed["record"]
            assert context["path"] == parent
            assert not parent.exists()
            assert record["parents"]
            assert record["parents"][-1]["path"] == str(ancestor)
            observed["parentsBeforeMkdir"] = [
                dict(snapshot) for snapshot in record["parents"]
            ]
        if name == "after_parent_identity":
            record = context["record"]
            assert record is observed["record"]
            assert record["parents"] == observed["parentsBeforeMkdir"]
            observed["ownedKey"] = tuple(record["objectKey"])
            ancestor.rename(retired_ancestor)
            ancestor.mkdir()
            parent.mkdir()
            foreign = parent / "foreign.txt"
            foreign.write_bytes(foreign_bytes)
            observed["foreign"] = foreign
            observed["foreignState"] = _s4_static_file_state(foreign)
            observed["concurrentKey"] = cc._transaction_file_object_key(
                os.lstat(parent)
            )
            raise OSError("injected parent replacement after directory identity capture")

    monkeypatch.setattr(cc, "_transaction_provisional_record", observe_provisional_record)
    monkeypatch.setattr(cc, "_transaction_checkpoint", replace_after_identity)
    monkeypatch.setattr(Path, "rmdir", observe_rmdir)
    result = cc._apply_text_transaction(tmp_path, [entry])

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["rollbackOk"] is True
    assert result["cleanupOk"] is False
    assert len(result["cleanupErrors"]) == 1
    assert str(parent) in result["cleanupErrors"][0]
    assert "parent identity or mode changed" in result["cleanupErrors"][0]
    assert result["written"] == []
    assert observed["record"]["parents"] == observed["parentsBeforeMkdir"]
    assert observed["record"]["parents"]
    assert observed["record"]["identityState"] == "known"
    assert observed["record"]["cleanupState"] == "preserved"
    assert rmdir_calls == []
    assert parent.is_dir()
    assert _s4_static_file_state(observed["foreign"]) == observed["foreignState"]
    assert observed["foreignState"][0] == foreign_bytes
    assert observed["concurrentKey"] != observed["ownedKey"]
    assert retired_parent.is_dir()
    assert list(retired_parent.iterdir()) == []
    assert not target.exists()


def test_s4_stat_02_created_stage_failure_cleanup_succeeds_before_any_replace(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "stage-successful-cleanup.txt"
    target.write_bytes(b"original target bytes\n")
    target_before = _s4_static_file_state(target)
    entry = cc._text_write_entry(tmp_path, target, "replacement bytes\n")
    observed = {}

    def fail_after_identity(name, **context):
        if name == "after_stage_identity" and context["suffix"] == ".stage":
            observed["record"] = context["record"]
            raise OSError("injected stage failure after identity capture")

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_after_identity)
    result = cc._apply_text_transaction(tmp_path, [entry])

    record = observed["record"]
    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["rollbackOk"] is True
    assert result["cleanupOk"] is True
    assert result["cleanupErrors"] == []
    assert result["written"] == []
    assert record["creationState"] == "created"
    assert record["identityState"] == "known"
    assert record["cleanupState"] == "succeeded"
    assert not Path(record["path"]).exists()
    assert _s4_static_file_state(target) == target_before


def test_s4_stat_03_decoder_recursion_is_unreadable_or_unsafe(monkeypatch):
    content = _s4_static_marker_content(_s4_static_marker_json())
    original_loads = cc.json.loads

    def recursive_decoder(encoded, *args, **kwargs):
        if "controlcoding.host-adapter-ownership" in encoded:
            raise RecursionError("injected decoder recursion")
        return original_loads(encoded, *args, **kwargs)

    monkeypatch.setattr(cc.json, "loads", recursive_decoder)
    marker, state, detail = cc._adapter_marker_payload(content)

    assert marker is None
    assert state == "unreadable_or_unsafe"
    assert detail == "ownership marker exceeds safe JSON nesting depth"


def test_s4_stat_03_post_decode_recursion_is_unreadable_or_unsafe(monkeypatch):
    content = _s4_static_marker_content(_s4_static_marker_json())

    def recursive_visit(value):
        raise RecursionError("injected decoded-value recursion")

    monkeypatch.setattr(cc, "_adapter_contains_non_json_constant", recursive_visit)
    marker, state, detail = cc._adapter_marker_payload(content)

    assert marker is None
    assert state == "unreadable_or_unsafe"
    assert detail == "ownership marker exceeds safe JSON nesting depth"


def test_s4_stat_03_recursion_blocks_planning_force_and_adoption_without_mutation(
    tmp_path,
    monkeypatch,
    capsys,
):
    _s4_static_write_source(tmp_path)
    target = tmp_path / "AGENTS.md"
    target.write_text(
        _s4_static_marker_content(_s4_static_marker_json()),
        encoding="utf-8",
    )
    before = _s4_static_file_state(target)
    original_loads = cc.json.loads

    def recursive_decoder(encoded, *args, **kwargs):
        if "controlcoding.host-adapter-ownership" in encoded:
            raise RecursionError("injected decoder recursion")
        return original_loads(encoded, *args, **kwargs)

    def forbidden_apply(*args, **kwargs):
        raise AssertionError("recursive marker reached adapter mutation")

    monkeypatch.setattr(cc.json, "loads", recursive_decoder)
    monkeypatch.setattr(cc, "_apply_adapter_transaction", forbidden_apply)
    plans = [
        cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="sync"),
        cc._adapter_plan_payload(
            tmp_path,
            host="codex_cli",
            operation="export",
            force=True,
        ),
        cc._adapter_plan_payload(tmp_path, host="codex_cli", operation="adopt"),
    ]
    for plan in plans:
        entry = plan["entries"][0]
        assert plan["preflightOk"] is False
        assert entry["state"] == "unreadable_or_unsafe"
        assert entry["ownership"] == "unreadable_or_unsafe"
        assert entry["bindingAllowed"] is False
        assert entry["action"] == "block"

    assert cc.cmd_export_host_context(
        tmp_path,
        host="codex_cli",
        force=True,
    ) == 1
    assert cc.cmd_context_adopt(
        tmp_path,
        host="codex_cli",
        apply=True,
    ) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert _s4_static_file_state(target) == before


def test_s4_stat_03_deep_duplicate_member_remains_ambiguous():
    nested_duplicate = (
        '{"child":' * 24
        + '{"duplicate":1,"duplicate":2}'
        + "}" * 24
    )
    encoded = (
        '{"owner":"ControlCoding",'
        '"schema":"controlcoding.host-adapter-ownership",'
        '"version":1,"target":"AGENTS.md","host":"codex_cli",'
        '"source":"CONTROLCODING.md",'
        '"format":"host-context-section-export-v1",'
        f'"extra":{nested_duplicate}'
        "}"
    )

    marker, state, detail = cc._adapter_marker_payload(
        _s4_static_marker_content(encoded)
    )

    assert marker is None
    assert state == "ambiguous"
    assert "duplicate decoded JSON member 'duplicate'" in detail


def test_s4_stat_03_repeated_array_scalars_remain_valid():
    content = _s4_static_marker_content(
        _s4_static_marker_json(extra=[1, 1, "same", "same"])
    )

    marker, state, detail = cc._adapter_marker_payload(content)

    assert state == "owned"
    assert detail == "valid ControlCoding ownership marker"
    assert marker["extra"] == [1, 1, "same", "same"]


def test_s4_stat_06_context_adopt_help_describes_safe_foreign_targets(capsys):
    argv = ["cc.py", "context", "adopt", "--help"]
    with patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit) as raised:
            cc.main()

    assert raised.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "unmarked or explicitly foreign safe adapter targets" in help_text
    assert "Invalid, ambiguous, and unreadable or unsafe targets remain blocked" in help_text


def test_s4_stat_02_created_stage_cleanup_failure_is_structured(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "stage-failed-cleanup.txt"
    target.write_bytes(b"original target bytes\n")
    target_before = _s4_static_file_state(target)
    entry = cc._text_write_entry(tmp_path, target, "replacement bytes\n")
    observed = {}
    original_unlink = Path.unlink
    original_chmod = cc.os.chmod
    cleanup_calls = {"unlink": 0, "chmod": 0}

    def fail_after_identity(name, **context):
        if name == "after_stage_identity" and context["suffix"] == ".stage":
            observed["record"] = context["record"]
            raise OSError("injected stage failure before write")

    def fail_stage_unlink(path, *args, **kwargs):
        if str(path).endswith(".stage"):
            cleanup_calls["unlink"] += 1
            raise OSError("injected stage unlink failure")
        return original_unlink(path, *args, **kwargs)

    def fail_stage_chmod(path, mode, *args, **kwargs):
        if str(path).endswith(".stage"):
            cleanup_calls["chmod"] += 1
            raise OSError("injected stage cleanup chmod failure")
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_after_identity)
    monkeypatch.setattr(Path, "unlink", fail_stage_unlink)
    monkeypatch.setattr(cc.os, "chmod", fail_stage_chmod)
    result = cc._apply_text_transaction(tmp_path, [entry])

    record = observed["record"]
    stage_path = Path(record["path"])
    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["rollbackOk"] is True
    assert result["cleanupOk"] is False
    assert len(result["cleanupErrors"]) == 1
    cleanup_error = result["cleanupErrors"][0]
    assert str(stage_path) in cleanup_error
    assert "injected stage cleanup chmod failure" in cleanup_error
    assert result["error"] == (
        "injected stage failure before write; cleanup incomplete: " + cleanup_error
    )
    assert result["error"].count(cleanup_error) == 1
    assert result["error"].count("injected stage failure before write") == 1
    assert result["written"] == []
    assert cleanup_calls == {"unlink": 1, "chmod": 1}
    assert record["creationState"] == "created"
    assert record["identityState"] == "known"
    assert record["cleanupState"] == "failed"
    assert stage_path.read_bytes() == b""
    assert cc._transaction_file_object_key(os.lstat(stage_path)) == tuple(
        record["objectKey"]
    )
    assert stat.S_IMODE(os.lstat(stage_path).st_mode) == record["mode"]
    assert _s4_static_file_state(target) == target_before


def test_s4_stat_02_replaced_reserved_stage_is_preserved_without_chmod_or_unlink(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "stage-replaced.txt"
    target.write_bytes(b"original target bytes\n")
    target_before = _s4_static_file_state(target)
    entry = cc._text_write_entry(tmp_path, target, "replacement bytes\n")
    retired = tmp_path / "retired-owned-stage"
    foreign_bytes = b"foreign reserved-path bytes\n"
    observed = {"foreignInstalled": False, "unlink": 0, "chmod": 0}
    original_unlink = Path.unlink
    original_chmod = cc.os.chmod

    def replace_after_close(name, **context):
        if name == "after_stage_close" and context["suffix"] == ".stage":
            record = context["record"]
            stage_path = Path(record["path"])
            observed["record"] = record
            stage_path.rename(retired)
            stage_path.write_bytes(foreign_bytes)
            original_chmod(stage_path, 0o440)
            observed["foreignState"] = _s4_static_file_state(stage_path)
            observed["foreignInstalled"] = True
            raise OSError("injected reserved-stage replacement")

    def observe_unlink(path, *args, **kwargs):
        if observed["foreignInstalled"] and Path(path) == Path(observed["record"]["path"]):
            observed["unlink"] += 1
        return original_unlink(path, *args, **kwargs)

    def observe_chmod(path, mode, *args, **kwargs):
        if observed["foreignInstalled"] and Path(path) == Path(observed["record"]["path"]):
            observed["chmod"] += 1
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(cc, "_transaction_checkpoint", replace_after_close)
    monkeypatch.setattr(Path, "unlink", observe_unlink)
    monkeypatch.setattr(cc.os, "chmod", observe_chmod)
    result = cc._apply_text_transaction(tmp_path, [entry])

    record = observed["record"]
    stage_path = Path(record["path"])
    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["cleanupOk"] is False
    assert result["cleanupErrors"]
    assert "identity changed" in result["cleanupErrors"][0]
    assert record["cleanupState"] == "preserved"
    assert observed["unlink"] == 0
    assert observed["chmod"] == 0
    assert _s4_static_file_state(stage_path) == observed["foreignState"]
    assert observed["foreignState"][0] == foreign_bytes
    assert observed["foreignState"][2] != tuple(record["objectKey"])
    assert observed["foreignState"][3] == stat.S_IFREG
    assert retired.read_bytes() == b"replacement bytes\n"
    assert _s4_static_file_state(target) == target_before


def test_s4_stat_02_identity_unavailable_stage_is_preserved_without_deletion(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "stage-identity-unavailable.txt"
    target.write_bytes(b"original target bytes\n")
    target_before = _s4_static_file_state(target)
    entry = cc._text_write_entry(tmp_path, target, "replacement bytes\n")
    observed = {"unlink": 0, "chmod": 0}
    original_unlink = Path.unlink
    original_chmod = cc.os.chmod

    def fail_before_identity(name, **context):
        if name == "after_stage_create" and context["suffix"] == ".stage":
            observed["record"] = context["record"]
            raise OSError("injected failure before stage identity")

    def observe_unlink(path, *args, **kwargs):
        if str(path).endswith(".stage"):
            observed["unlink"] += 1
        return original_unlink(path, *args, **kwargs)

    def observe_chmod(path, mode, *args, **kwargs):
        if str(path).endswith(".stage"):
            observed["chmod"] += 1
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(cc, "_transaction_checkpoint", fail_before_identity)
    monkeypatch.setattr(Path, "unlink", observe_unlink)
    monkeypatch.setattr(cc.os, "chmod", observe_chmod)
    result = cc._apply_text_transaction(tmp_path, [entry])

    record = observed["record"]
    stage_path = Path(record["path"])
    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["cleanupOk"] is False
    assert result["cleanupErrors"]
    assert "identity is unavailable" in result["cleanupErrors"][0]
    assert result["written"] == []
    assert record["creationState"] == "created"
    assert record["identityState"] == "unavailable"
    assert record["cleanupState"] == "preserved"
    assert observed["unlink"] == 0
    assert observed["chmod"] == 0
    assert stage_path.is_file()
    assert stage_path.read_bytes() == b""
    assert _s4_static_file_state(target) == target_before


def test_s4_stat_05_reserved_cleanup_retries_identity_safe_after_first_unlink_failure(
    tmp_path,
    monkeypatch,
):
    reserved = tmp_path / ".reserved-retry.stage"
    reserved.write_bytes(b"owned reserved bytes\n")
    reserved.chmod(0o400)
    expected_key = cc._transaction_file_object_key(os.lstat(reserved))
    parent_snapshots = cc._snapshot_transaction_parents(tmp_path, reserved)
    original_unlink = Path.unlink
    original_chmod = cc.os.chmod
    calls = {"unlink": 0, "chmod": 0}

    def fail_first_unlink(path, *args, **kwargs):
        if Path(path) == reserved:
            calls["unlink"] += 1
            if calls["unlink"] == 1:
                raise OSError("injected first unlink failure")
        return original_unlink(path, *args, **kwargs)

    def record_chmod(path, mode, *args, **kwargs):
        if Path(path) == reserved:
            calls["chmod"] += 1
            assert cc._transaction_file_object_key(os.lstat(path)) == expected_key
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    monkeypatch.setattr(cc.os, "chmod", record_chmod)
    cleanup_error = cc._cleanup_reserved_transaction_path(
        reserved,
        expected_key,
        project=tmp_path,
        parent_snapshots=parent_snapshots,
    )

    assert cleanup_error == ""
    assert calls == {"unlink": 2, "chmod": 1}
    assert not reserved.exists()


def test_s4_stat_05_reserved_cleanup_preserves_replacement_before_chmod(
    tmp_path,
    monkeypatch,
):
    reserved = tmp_path / ".reserved-replacement.stage"
    reserved.write_bytes(b"owned reserved bytes\n")
    reserved.chmod(0o400)
    expected_key = cc._transaction_file_object_key(os.lstat(reserved))
    parent_snapshots = cc._snapshot_transaction_parents(tmp_path, reserved)
    retired = tmp_path / "retired-reserved-stage"
    foreign_bytes = b"foreign replacement bytes\n"
    original_unlink = Path.unlink
    original_chmod = cc.os.chmod
    observed = {"unlink": 0, "chmod": 0, "foreignInstalled": False}

    def fail_first_unlink(path, *args, **kwargs):
        if Path(path) == reserved:
            observed["unlink"] += 1
            if observed["unlink"] == 1:
                raise OSError("injected first unlink failure")
        return original_unlink(path, *args, **kwargs)

    def replace_before_chmod(name, **context):
        if name == "before_cleanup_chmod":
            reserved.rename(retired)
            reserved.write_bytes(foreign_bytes)
            original_chmod(reserved, 0o440)
            observed["foreignState"] = _s4_static_file_state(reserved)
            observed["foreignInstalled"] = True

    def observe_chmod(path, mode, *args, **kwargs):
        if observed["foreignInstalled"] and Path(path) == reserved:
            observed["chmod"] += 1
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    monkeypatch.setattr(cc, "_transaction_checkpoint", replace_before_chmod)
    monkeypatch.setattr(cc.os, "chmod", observe_chmod)
    cleanup_error = cc._cleanup_reserved_transaction_path(
        reserved,
        expected_key,
        project=tmp_path,
        parent_snapshots=parent_snapshots,
    )

    assert "immediately before cleanup chmod" in cleanup_error
    assert "preserved" in cleanup_error
    assert observed["unlink"] == 1
    assert observed["chmod"] == 0
    assert _s4_static_file_state(reserved) == observed["foreignState"]
    assert observed["foreignState"][0] == foreign_bytes
    assert observed["foreignState"][2] != expected_key
    assert observed["foreignState"][3] == stat.S_IFREG
    assert retired.read_bytes() == b"owned reserved bytes\n"


def test_s4_stat_05_stage_without_fchmod_revalidates_then_uses_path_chmod(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "fallback-chmod.txt"
    target.write_bytes(b"original mode bytes\n")
    target.chmod(0o640)
    original_mode = stat.S_IMODE(os.lstat(target).st_mode)
    entry = cc._text_write_entry(tmp_path, target, "replacement mode bytes\n")
    original_revalidate = cc._revalidate_transaction_parents
    original_chmod = cc.os.chmod
    events = []
    observed = {}

    def record_checkpoint(name, **context):
        if name == "before_stage_chmod" and context["suffix"] == ".stage":
            observed["record"] = context["record"]
            events.append(("checkpoint", Path(context["path"])))

    def record_revalidation(project, path, expected):
        result = original_revalidate(project, path, expected)
        if str(path).endswith(".stage") and "record" in observed:
            events.append(("parents", Path(path)))
        return result

    def record_path_chmod(path, mode, *args, **kwargs):
        if str(path).endswith(".stage"):
            record = observed["record"]
            current_stat = os.lstat(path)
            assert events[-1] == ("parents", Path(path))
            assert cc._transaction_stat_is_safe_regular(current_stat)
            assert cc._transaction_file_object_key(current_stat) == tuple(
                record["objectKey"]
            )
            observed["chmod"] = (Path(path), mode)
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(cc.os, "fchmod", None, raising=False)
    monkeypatch.setattr(cc, "_transaction_checkpoint", record_checkpoint)
    monkeypatch.setattr(cc, "_revalidate_transaction_parents", record_revalidation)
    monkeypatch.setattr(cc.os, "chmod", record_path_chmod)
    result = cc._apply_text_transaction(tmp_path, [entry])

    assert result["ok"] is True
    assert result["cleanupOk"] is True
    assert result["cleanupErrors"] == []
    assert observed["chmod"][1] == original_mode
    assert observed["record"]["identityState"] == "known"
    assert target.read_bytes() == b"replacement mode bytes\n"
    assert stat.S_IMODE(os.lstat(target).st_mode) == original_mode


def test_s4_stat_05_fallback_chmod_failure_cleans_stage_and_preserves_target(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "fallback-chmod-failure.txt"
    target.write_bytes(b"original target bytes\n")
    target.chmod(0o640)
    target_before = _s4_static_file_state(target)
    entry = cc._text_write_entry(tmp_path, target, "replacement bytes\n")
    original_chmod = cc.os.chmod
    observed = {}

    def record_checkpoint(name, **context):
        if name == "before_stage_chmod" and context["suffix"] == ".stage":
            observed["record"] = context["record"]

    def fail_stage_chmod(path, mode, *args, **kwargs):
        if str(path).endswith(".stage"):
            raise OSError("injected fallback chmod failure")
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(cc.os, "fchmod", None, raising=False)
    monkeypatch.setattr(cc, "_transaction_checkpoint", record_checkpoint)
    monkeypatch.setattr(cc.os, "chmod", fail_stage_chmod)
    result = cc._apply_text_transaction(tmp_path, [entry])

    record = observed["record"]
    assert result["ok"] is False
    assert result["applied"] is False
    assert result["attempted"] is False
    assert result["rolledBack"] is False
    assert result["rollbackOk"] is True
    assert result["cleanupOk"] is True
    assert result["cleanupErrors"] == []
    assert result["written"] == []
    assert "injected fallback chmod failure" in result["error"]
    assert record["cleanupState"] == "succeeded"
    assert not Path(record["path"]).exists()
    assert _s4_static_file_state(target) == target_before
