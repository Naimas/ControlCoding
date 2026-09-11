#!/usr/bin/env python3
"""
phase0_discover.py - ControlCoding Phase 0 Auto-Discovery

Scans a codebase and generates a draft CONTROLCODING.md with:
- Detected languages, frameworks, build tools
- Suggested module boundaries (stable/shared/features/workspace)
- Suggested domain invariants based on test files
- Git-based stability analysis (if available)

Usage:
    python phase0_discover.py [--project-root PATH] [--output CONTROLCODING.md.draft]

The output is a DRAFT for human review, not a final file.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directories to skip during scanning
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache", ".cache",
    "dist", "build", "target", "out", "bin", "obj", ".next", ".nuxt",
    ".controlcoding", ".claude", ".bridge", "devlog",
}

# File extensions to language mapping
EXT_TO_LANG = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript (React)",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".h": "C/C++",
    ".c": "C",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala",
    ".lua": "Lua",
    ".r": "R", ".R": "R",
    ".sql": "SQL",
    ".sh": "Shell", ".bash": "Shell",
    ".ps1": "PowerShell",
}

# Marker files for framework/tool detection
# (marker_file, framework_name, category)
FRAMEWORK_MARKERS = [
    # Python
    ("requirements.txt", "pip", "package-manager"),
    ("pyproject.toml", "pyproject", "build"),
    ("setup.py", "setuptools", "build"),
    ("Pipfile", "pipenv", "package-manager"),
    ("poetry.lock", "Poetry", "package-manager"),
    ("manage.py", "Django", "framework"),
    ("alembic.ini", "Alembic", "migration"),
    ("conftest.py", "pytest", "test"),
    (".flake8", "flake8", "linter"),
    ("mypy.ini", "mypy", "type-checker"),
    (".pre-commit-config.yaml", "pre-commit", "tool"),
    # JavaScript/TypeScript
    ("package.json", "npm/Node.js", "runtime"),
    ("yarn.lock", "Yarn", "package-manager"),
    ("pnpm-lock.yaml", "pnpm", "package-manager"),
    ("bun.lockb", "Bun", "runtime"),
    ("tsconfig.json", "TypeScript", "language"),
    ("vite.config.ts", "Vite", "bundler"),
    ("vite.config.js", "Vite", "bundler"),
    ("webpack.config.js", "Webpack", "bundler"),
    ("next.config.js", "Next.js", "framework"),
    ("next.config.ts", "Next.js", "framework"),
    ("nuxt.config.ts", "Nuxt", "framework"),
    ("angular.json", "Angular", "framework"),
    ("svelte.config.js", "SvelteKit", "framework"),
    ("jest.config.js", "Jest", "test"),
    ("jest.config.ts", "Jest", "test"),
    ("vitest.config.ts", "Vitest", "test"),
    (".eslintrc.json", "ESLint", "linter"),
    ("eslint.config.js", "ESLint", "linter"),
    # C/C++
    ("CMakeLists.txt", "CMake", "build"),
    ("Makefile", "Make", "build"),
    ("meson.build", "Meson", "build"),
    ("vcpkg.json", "vcpkg", "package-manager"),
    ("conanfile.txt", "Conan", "package-manager"),
    # Go
    ("go.mod", "Go Modules", "build"),
    # Rust
    ("Cargo.toml", "Cargo", "build"),
    # Java/JVM
    ("pom.xml", "Maven", "build"),
    ("build.gradle", "Gradle", "build"),
    ("build.gradle.kts", "Gradle (Kotlin)", "build"),
    # Ruby
    ("Gemfile", "Bundler", "package-manager"),
    ("Rakefile", "Rake", "build"),
    # Docker/Infra
    ("Dockerfile", "Docker", "infra"),
    ("docker-compose.yml", "Docker Compose", "infra"),
    ("docker-compose.yaml", "Docker Compose", "infra"),
    (".github/workflows", "GitHub Actions", "ci"),
    (".gitlab-ci.yml", "GitLab CI", "ci"),
    # Misc
    ("CONTROLCODING.md", "ControlCoding", "methodology"),
    ("CLAUDE.md", "Claude host context", "methodology"),
    ("AGENTS.md", "Agent conventions", "methodology"),
]

# package.json dependency patterns for framework detection
PKG_FRAMEWORK_PATTERNS = {
    "react": "React",
    "react-dom": "React",
    "next": "Next.js",
    "vue": "Vue.js",
    "nuxt": "Nuxt",
    "@angular/core": "Angular",
    "svelte": "Svelte",
    "express": "Express",
    "fastify": "Fastify",
    "koa": "Koa",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "prisma": "Prisma",
    "drizzle-orm": "Drizzle",
    "sequelize": "Sequelize",
    "typeorm": "TypeORM",
    "mongoose": "Mongoose",
    "tailwindcss": "Tailwind CSS",
    "styled-components": "styled-components",
    "electron": "Electron",
}

# Directory name patterns for zone classification
STABLE_PATTERNS = {"core", "models", "auth", "database", "db", "schema", "domain", "entities"}
SHARED_PATTERNS = {"utils", "helpers", "shared", "common", "lib", "pkg", "internal"}
FEATURE_PATTERNS = {"features", "modules", "routes", "api", "pages", "views", "controllers", "handlers", "components"}
WORKSPACE_PATTERNS = {"scratch", "tmp", "temp", "experiments", "prototypes", "playground", "draft", "sandbox"}


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_files(root: Path) -> list[Path]:
    """Collect all files, skipping ignored directories."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Modify dirnames in-place to skip unwanted directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            files.append(Path(dirpath) / f)
    return files


def detect_languages(files: list[Path]) -> dict[str, int]:
    """Count files per detected language."""
    lang_counts = Counter()
    for f in files:
        ext = f.suffix.lower()
        lang = EXT_TO_LANG.get(ext)
        if lang:
            lang_counts[lang] += 1
    return dict(lang_counts.most_common())


def detect_frameworks(root: Path) -> list[tuple[str, str]]:
    """Detect frameworks and tools by marker files. Returns (name, category) pairs."""
    found = []
    for marker, name, category in FRAMEWORK_MARKERS:
        # Handle directory markers (e.g., .github/workflows)
        target = root / marker
        if target.exists():
            found.append((name, category))
    return found


def detect_pkg_frameworks(root: Path) -> list[str]:
    """Detect frameworks from package.json dependencies."""
    pkg = root / "package.json"
    if not pkg.exists():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    deps = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if key in data:
            deps.update(data[key].keys())
    found = []
    for pattern, name in PKG_FRAMEWORK_PATTERNS.items():
        if pattern in deps:
            found.append(name)
    return list(dict.fromkeys(found))  # deduplicate preserving order


def detect_pyproject_frameworks(root: Path) -> list[str]:
    """Detect Python frameworks from pyproject.toml."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        content = pyproject.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    found = []
    patterns = {
        "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
        "starlette": "Starlette", "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic", "celery": "Celery", "pytest": "pytest",
    }
    for pattern, name in patterns.items():
        if pattern in content.lower():
            found.append(name)
    return found


def analyze_directories(root: Path, files: list[Path]) -> dict:
    """Analyze directory structure for zone classification."""
    # Get top-level source directories (skip dotfiles, common non-source dirs)
    skip_toplevel = {"docs", "doc", "documentation", "devlog", "benchmarks", "examples"}
    top_dirs = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith(".") and d.name not in skip_toplevel:
            file_count = sum(1 for f in files if f.is_relative_to(d))
            if file_count > 0:
                top_dirs.append((d.name, file_count))

    # Classify directories into zones
    stable = []
    shared = []
    features = []
    workspace = []
    unclassified = []

    for dirname, count in top_dirs:
        lower = dirname.lower()
        if lower in STABLE_PATTERNS:
            stable.append((dirname, count))
        elif lower in SHARED_PATTERNS:
            shared.append((dirname, count))
        elif lower in FEATURE_PATTERNS:
            features.append((dirname, count))
        elif lower in WORKSPACE_PATTERNS:
            workspace.append((dirname, count))
        else:
            unclassified.append((dirname, count))

    return {
        "stable": stable,
        "shared": shared,
        "features": features,
        "workspace": workspace,
        "unclassified": unclassified,
        "all": top_dirs,
    }


def find_test_files(files: list[Path], root: Path) -> list[dict]:
    """Find test files and extract potential invariant patterns."""
    test_files = []
    for f in files:
        name = f.name.lower()
        rel = str(f.relative_to(root))
        is_test = (
            name.startswith("test_") or
            name.endswith("_test.py") or
            name.endswith(".test.js") or
            name.endswith(".test.ts") or
            name.endswith(".test.tsx") or
            name.endswith("_test.go") or
            name.endswith("_test.rs") or
            "tests/" in rel.replace("\\", "/") or
            "test/" in rel.replace("\\", "/") or
            "__tests__/" in rel.replace("\\", "/")
        )
        if is_test:
            test_files.append({"path": rel, "name": f.name})
    return test_files


def extract_test_patterns(test_files: list[dict], root: Path) -> list[str]:
    """Try to extract invariant-like patterns from test file names and content."""
    patterns = []
    # From test file names
    for tf in test_files[:20]:  # limit to avoid reading too many
        name = tf["name"]
        # Strip test prefix/suffix to get the subject
        subject = name
        for prefix in ("test_", "Test"):
            if subject.startswith(prefix):
                subject = subject[len(prefix):]
        for suffix in ("_test.py", ".test.js", ".test.ts", ".test.tsx", "_test.go", "_test.rs", ".py", ".js", ".ts"):
            if subject.endswith(suffix):
                subject = subject[:-len(suffix)]
        if subject and subject not in ("__init__",):
            patterns.append(subject)

    # Try reading a few test files for assertion patterns
    invariant_hints = []
    for tf in test_files[:5]:
        fpath = root / tf["path"]
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            # Look for assert patterns that suggest invariants
            for line in content.splitlines():
                line = line.strip()
                if any(kw in line.lower() for kw in ["assert", "expect", "should"]):
                    # Look for equality checks that might be invariants
                    if "==" in line or "equal" in line.lower() or "tobe" in line.lower():
                        # Clean up and add if it looks like a property
                        clean = line[:120]
                        invariant_hints.append(clean)
                        if len(invariant_hints) >= 10:
                            break
        except (OSError, UnicodeDecodeError):
            continue

    return patterns, invariant_hints


def git_stable_files(root: Path, months: int = 3) -> list[str]:
    """Find files unchanged for N months (git-based stability analysis)."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={months} months ago", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, cwd=root, timeout=10,
        )
        if result.returncode != 0:
            return []
        recently_changed = set(line.strip() for line in result.stdout.splitlines() if line.strip())

        # Get all tracked files
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, cwd=root, timeout=10,
        )
        if result.returncode != 0:
            return []
        all_files = set(line.strip() for line in result.stdout.splitlines() if line.strip())

        # Stable = tracked but not recently changed
        stable = all_files - recently_changed
        # Filter to source files only
        source_exts = set(EXT_TO_LANG.keys())
        stable_source = [f for f in sorted(stable) if Path(f).suffix.lower() in source_exts]
        return stable_source
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def git_hotspots(root: Path) -> list[tuple[str, int]]:
    """Find files with most commits (potential hotspots or core files)."""
    try:
        result = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "--since=6 months ago"],
            capture_output=True, text=True, cwd=root, timeout=10,
        )
        if result.returncode != 0:
            return []
        counts = Counter(
            line.strip() for line in result.stdout.splitlines()
            if line.strip() and Path(line.strip()).suffix.lower() in set(EXT_TO_LANG.keys())
        )
        return counts.most_common(15)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def count_imports(files: list[Path], root: Path) -> dict[str, int]:
    """Count how many files import each module (rough dependency analysis)."""
    import_counts = Counter()
    for f in files:
        if f.suffix not in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs"):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                line = line.strip()
                # Python: from X import / import X
                if f.suffix == ".py":
                    m = re.match(r"^(?:from|import)\s+([\w.]+)", line)
                    if m:
                        import_counts[m.group(1).split(".")[0]] += 1
                # JS/TS: import ... from "X" / require("X")
                elif f.suffix in (".js", ".ts", ".tsx", ".jsx"):
                    m = re.search(r"""(?:from|require\()\s*['"]([^'"]+)['"]""", line)
                    if m:
                        mod = m.group(1)
                        if mod.startswith("."):
                            # Relative import - get the directory
                            resolved = (f.parent / mod).resolve()
                            try:
                                rel = resolved.relative_to(root)
                                import_counts[str(rel.parts[0])] += 1
                            except ValueError:
                                pass
                        else:
                            import_counts[mod.split("/")[0]] += 1
        except (OSError, UnicodeDecodeError):
            continue
    return dict(import_counts.most_common(20))


# ---------------------------------------------------------------------------
# CONTROLCODING.md generation
# ---------------------------------------------------------------------------


def generate_claude_md(
    project_name: str,
    languages: dict,
    frameworks: list[tuple[str, str]],
    pkg_frameworks: list[str],
    directories: dict,
    test_files: list[dict],
    test_patterns: tuple,
    git_stable: list[str],
    hotspots: list[tuple[str, int]],
    imports: dict[str, int],
) -> str:
    """Generate a draft CONTROLCODING.md from analysis results."""
    lines = []

    # Determine primary language/stack
    primary_langs = list(languages.keys())[:3]
    lang_str = ", ".join(primary_langs) if primary_langs else "[UNKNOWN]"

    # Build framework string
    fw_names = []
    for name, cat in frameworks:
        if cat == "framework":
            fw_names.append(name)
    fw_names.extend(pkg_frameworks)
    fw_str = ", ".join(fw_names) if fw_names else "[NO FRAMEWORK DETECTED]"

    # Build tool string
    tool_names = []
    for name, cat in frameworks:
        if cat in ("build", "bundler", "package-manager"):
            tool_names.append(name)
    tool_str = ", ".join(tool_names) if tool_names else ""

    stack_parts = [lang_str]
    if fw_str != "[NO FRAMEWORK DETECTED]":
        stack_parts.append(fw_str)
    if tool_str:
        stack_parts.append(tool_str)
    stack_str = " / ".join(stack_parts)

    lines.append(f"# {project_name}")
    lines.append("")
    lines.append("> This file is read by AI coding assistants at the start of every session.")
    lines.append("> It defines the rules, boundaries, and invariants of this project.")
    lines.append("> **Keep it under 200 lines.** If it grows beyond that, extract details into linked docs.")
    lines.append(">")
    lines.append("> **DRAFT** - Generated by phase0_discover.py. Review and customize before use.")
    lines.append("")

    # Project Identity
    lines.append("## Project Identity")
    lines.append("")
    lines.append(f"- **Name**: {project_name}")
    lines.append(f"- **Stack**: {stack_str}")
    lines.append("- **Architecture**: [DESCRIBE YOUR ARCHITECTURE]")
    lines.append("- **Truth representation**: [WHICH DATA FORMAT IS AUTHORITATIVE]")
    lines.append("- **View representation**: [WHICH DATA FORMAT IS DERIVED]")
    lines.append("")

    # Architecture Rules
    lines.append("## Architecture Rules")
    lines.append("")
    lines.append("1. [RULE_1 - e.g., \"All state mutations go through the Store\"]")
    lines.append("2. [RULE_2 - e.g., \"No direct database access outside repository classes\"]")
    lines.append("3. [RULE_3 - e.g., \"All public API responses must include a schema version\"]")

    # Add framework-specific suggestions
    suggested_rules = []
    fw_set = set(n.lower() for n, _ in frameworks) | set(n.lower() for n in pkg_frameworks)
    if "react" in fw_set:
        suggested_rules.append("Keep components pure: no side effects in render, use hooks for state")
    if "django" in fw_set:
        suggested_rules.append("All database queries go through Django ORM, no raw SQL")
    if "fastapi" in fw_set:
        suggested_rules.append("All endpoints must have Pydantic models for request/response")
    if "express" in fw_set or "fastify" in fw_set:
        suggested_rules.append("All routes must have validation middleware")
    if "sqlalchemy" in fw_set:
        suggested_rules.append("All database access goes through SQLAlchemy sessions")
    if suggested_rules:
        lines.append("")
        lines.append("> Suggested rules based on detected stack:")
        for i, rule in enumerate(suggested_rules, 4):
            lines.append(f"> {i}. \"{rule}\"")
    lines.append("")

    # Module Boundaries
    lines.append("## Module Boundaries")
    lines.append("")

    dirs = directories

    lines.append("### Stable (do NOT modify without explicit approval)")
    if dirs["stable"]:
        for name, count in dirs["stable"]:
            lines.append(f"- `{name}/` - {count} files [DESCRIBE PURPOSE]")
    else:
        lines.append("- [NO STABLE DIRECTORIES DETECTED - identify your core modules]")
    # Add git-stable suggestions
    if git_stable:
        stable_dirs = set()
        for f in git_stable[:50]:
            parts = Path(f).parts
            if len(parts) > 1:
                stable_dirs.add(parts[0])
        if stable_dirs - {d for d, _ in dirs["stable"]}:
            lines.append(">")
            lines.append("> Git analysis suggests these directories have stable files (unchanged 3+ months):")
            for d in sorted(stable_dirs)[:5]:
                lines.append(f">   `{d}/`")
    lines.append("")

    lines.append("### Shared (modify with care, run all tests after changes)")
    if dirs["shared"]:
        for name, count in dirs["shared"]:
            lines.append(f"- `{name}/` - {count} files [DESCRIBE PURPOSE]")
    else:
        lines.append("- [NO SHARED DIRECTORIES DETECTED - identify utility modules]")
    # Add import-based suggestions
    if imports:
        heavily_imported = [(mod, count) for mod, count in imports.items()
                          if count >= 3 and mod not in {d for d, _ in dirs["shared"]}]
        if heavily_imported:
            lines.append(">")
            lines.append("> Import analysis suggests these modules are widely used (3+ importers):")
            for mod, count in heavily_imported[:5]:
                lines.append(f">   `{mod}` (imported by {count} files)")
    lines.append("")

    lines.append("### Features (active development)")
    if dirs["features"]:
        for name, count in dirs["features"]:
            lines.append(f"- `{name}/` - {count} files [DESCRIBE PURPOSE]")
    else:
        lines.append("- [NO FEATURE DIRECTORIES DETECTED - identify active development areas]")
    # Add hotspot suggestions
    if hotspots:
        lines.append(">")
        lines.append("> Git analysis shows these files change most frequently (likely active features):")
        for f, count in hotspots[:5]:
            lines.append(f">   `{f}` ({count} commits)")
    lines.append("")

    lines.append("### Workspace (experimental, can be rewritten freely)")
    if dirs["workspace"]:
        for name, count in dirs["workspace"]:
            lines.append(f"- `{name}/` - {count} files")
    else:
        lines.append("- [NO WORKSPACE DIRECTORIES DETECTED - add scratch/experiment areas here]")
    lines.append("")

    # Unclassified directories
    if dirs["unclassified"]:
        lines.append("> **Unclassified directories** - assign these to a zone:")
        for name, count in dirs["unclassified"]:
            lines.append(f">   `{name}/` ({count} files)")
        lines.append("")

    # Domain Invariants
    lines.append("## Domain Invariants")
    lines.append("")
    lines.append("These properties MUST always be true. Run invariant tests before any commit.")
    lines.append("")

    subjects, hints = test_patterns
    if subjects:
        lines.append("> Test files suggest these areas need invariants:")
        for subj in subjects[:6]:
            lines.append(f">   - {subj}")
        lines.append("")
    if hints:
        lines.append("> Sample assertions from test files (potential invariant candidates):")
        for hint in hints[:5]:
            lines.append(f">   `{hint}`")
        lines.append("")

    lines.append("1. **[INVARIANT_1]**: [DESCRIPTION] - Test: `[TEST_COMMAND]`")
    lines.append("2. **[INVARIANT_2]**: [DESCRIPTION] - Test: `[TEST_COMMAND]`")
    lines.append("3. **[INVARIANT_3]**: [DESCRIPTION] - Test: `[TEST_COMMAND]`")
    lines.append("")

    # Protected Zones
    lines.append("## Protected Zones (enforcement: hooks)")
    lines.append("")
    lines.append("Hooks in `.controlcoding/settings.json` enforce boundary protection mechanically.")
    lines.append("")
    lines.append("**When you receive a WARN from a hook**: STOP. Explain to the user what you")
    lines.append("intended to modify and why. Wait for explicit approval before proceeding.")
    lines.append("")
    lines.append("**When you receive a DENY from a hook**: Do NOT attempt the operation again.")
    lines.append("Instead, follow the Temporary Lift Protocol:")
    lines.append("1. First explore alternatives: can the feature work via new files, delegation,")
    lines.append("   or interface extensions without touching the protected file?")
    lines.append("2. If the protected file must change, explain exactly which functions you need")
    lines.append("   to modify, estimate the line count, and describe each change.")
    lines.append("3. Changes to protected files must be wiring-only: delegation calls to external")
    lines.append("   systems, not inline business logic.")
    lines.append("4. Wait for human approval. The approval applies ONLY to the declared scope.")
    lines.append("5. After completing approved changes, restore DENY protections immediately.")
    lines.append("")

    # Delegation Boundary
    lines.append("## Delegation Boundary")
    lines.append("")
    lines.append("When using subagents (Task tool, parallel agents):")
    lines.append("- **Accept**: factual data (file inventories, pattern searches, measurements)")
    lines.append("- **Filter**: recommendations, architectural suggestions, design proposals")
    lines.append("- Verify any subagent suggestion against this project context before presenting it")
    lines.append("")

    # Debug Protocol
    lines.append("## Debug Protocol")
    lines.append("")
    lines.append("When a feature doesn't produce the expected visual or runtime result:")
    lines.append("1. VERIFY the component exists (render with debug color, add log output)")
    lines.append("2. VERIFY data flow (check values at each stage of the pipeline)")
    lines.append("3. ONLY THEN adjust parameters")
    lines.append("Never skip to parameter tweaking without confirming the hypothesis.")
    lines.append("")

    # Operative Rules
    lines.append("## Operative Rules")
    lines.append("")
    lines.append("- Before creating a new function, search the codebase for existing implementations")
    lines.append("- For non-trivial features or cross-layer changes, create or confirm a mini-design slice before implementation")
    lines.append("- Before modifying stable code, verify there's no alternative in features/ or workspace/")
    lines.append("- Never introduce non-determinism (random without seed, Date.now() in business logic)")
    lines.append("- [ADD YOUR PROJECT-SPECIFIC RULES HERE]")
    lines.append("")

    # Current Focus
    lines.append("## Current Focus")
    lines.append("")
    lines.append("- [ ] [CURRENT_TASK_1]")
    lines.append("- [ ] [CURRENT_TASK_2]")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def print_report(
    root: Path,
    files: list[Path],
    languages: dict,
    frameworks: list[tuple[str, str]],
    pkg_frameworks: list[str],
    directories: dict,
    test_files: list[dict],
    git_stable: list[str],
    hotspots: list[tuple[str, int]],
    imports: dict[str, int],
):
    """Print a summary report to stdout."""
    print(f"\n{'='*60}")
    print(f"  Phase 0 Discovery Report: {root.name}")
    print(f"{'='*60}\n")

    print(f"Total files scanned: {len(files)}")
    print()

    # Languages
    print("Languages detected:")
    for lang, count in languages.items():
        bar = "#" * min(count, 40)
        print(f"  {lang:20s}  {count:4d} files  {bar}")
    print()

    # Frameworks
    if frameworks or pkg_frameworks:
        print("Frameworks and tools:")
        for name, cat in frameworks:
            print(f"  [{cat:15s}]  {name}")
        for name in pkg_frameworks:
            print(f"  [{'framework':15s}]  {name} (from package.json)")
        print()

    # Directory zones
    dirs = directories
    print("Directory zone suggestions:")
    for zone, entries in [("STABLE", dirs["stable"]), ("SHARED", dirs["shared"]),
                          ("FEATURES", dirs["features"]), ("WORKSPACE", dirs["workspace"])]:
        if entries:
            for name, count in entries:
                print(f"  {zone:12s}  {name}/ ({count} files)")
    if dirs["unclassified"]:
        for name, count in dirs["unclassified"]:
            print(f"  {'???':12s}  {name}/ ({count} files)")
    print()

    # Tests
    print(f"Test files found: {len(test_files)}")
    if test_files:
        for tf in test_files[:8]:
            print(f"  {tf['path']}")
        if len(test_files) > 8:
            print(f"  ... and {len(test_files) - 8} more")
    print()

    # Git analysis
    if git_stable:
        print(f"Git-stable files (unchanged 3+ months): {len(git_stable)}")
    if hotspots:
        print("Git hotspots (most changed files):")
        for f, count in hotspots[:5]:
            print(f"  {count:3d} commits  {f}")
    if git_stable or hotspots:
        print()

    # Top imports
    if imports:
        print("Most imported modules:")
        for mod, count in list(imports.items())[:8]:
            print(f"  {count:3d} importers  {mod}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="phase0_discover",
        description="Scan a codebase and generate a draft CONTROLCODING.md",
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--output", type=str, default="CONTROLCODING.md.draft",
        help="Output file name (default: CONTROLCODING.md.draft)",
    )
    parser.add_argument(
        "--no-git", action="store_true",
        help="Skip git-based analysis",
    )
    args = parser.parse_args()

    root = args.project_root.resolve()
    if not root.is_dir():
        print(f"Error: {root} is not a directory")
        return 1

    project_name = root.name

    print(f"Scanning {root} ...")
    files = scan_files(root)
    if not files:
        print("No files found. Check the project path.")
        return 1

    # Run all analyses
    languages = detect_languages(files)
    frameworks = detect_frameworks(root)
    pkg_frameworks = detect_pkg_frameworks(root)
    pyproj_frameworks = detect_pyproject_frameworks(root)
    pkg_frameworks = list(dict.fromkeys(pkg_frameworks + pyproj_frameworks))

    directories = analyze_directories(root, files)
    test_files = find_test_files(files, root)
    test_patterns = extract_test_patterns(test_files, root)
    imports = count_imports(files, root)

    git_stable = []
    hotspots = []
    if not args.no_git:
        git_stable = git_stable_files(root)
        hotspots = git_hotspots(root)

    # Print report
    print_report(root, files, languages, frameworks, pkg_frameworks,
                 directories, test_files, git_stable, hotspots, imports)

    # Generate CONTROLCODING.md draft
    draft = generate_claude_md(
        project_name, languages, frameworks, pkg_frameworks,
        directories, test_files, test_patterns, git_stable, hotspots, imports,
    )

    output_path = root / args.output
    output_path.write_text(draft, encoding="utf-8")
    print(f"Draft written to: {output_path}")
    print(f"\nNext steps:")
    print(f"  1. Review and customize the draft")
    print(f"  2. Rename to CONTROLCODING.md when ready")
    print(f"  3. Run: python cc.py setup --project-root {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
