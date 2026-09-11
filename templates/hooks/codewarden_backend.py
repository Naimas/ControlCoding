#!/usr/bin/env python3
"""codewarden_backend.py - Shared LLM backend for CodeWarden hooks.

Provides LLM API calls (Claude CLI, Ollama, Anthropic, OpenAI-compatible),
project utilities, and prompt injection mitigation used by codewarden_review.py
and codewarden_plan_review.py.

Not a standalone script. Imported by other CodeWarden hooks.

Backends (configure via environment variables):
  CODEWARDEN_BACKEND   Explicit adapter: "ollama", "claude", "anthropic", or "openai"
  CODEWARDEN_CONSENT   Must be "approved" before any external review call
  CODEWARDEN_TIMEOUT   Timeout in seconds for LLM calls (default: 120, claude: 360)
  OLLAMA_URL           Ollama endpoint (default: http://localhost:11434)
  OLLAMA_MODEL         Model name (default: qwen2.5-coder:7b)
  ANTHROPIC_API_KEY    API key for Anthropic backend
  OPENAI_API_KEY       API key for OpenAI-compatible backend
  OPENAI_API_BASE      Base URL for OpenAI-compatible API
  CODEWARDEN_MODEL     Model name for cloud backends
"""

import json
import importlib.util
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"


def _get_timeout(default: int = 120) -> int:
    """Get timeout from CODEWARDEN_TIMEOUT env var or use default."""
    try:
        return int(os.environ.get("CODEWARDEN_TIMEOUT", str(default)))
    except ValueError:
        return default


def get_project_root() -> Path:
    """Find project root (parent of hooks/ directory, or git root)."""
    script_dir = Path(__file__).resolve().parent
    # If script is in hooks/, project root is parent
    if script_dir.name == "hooks":
        return script_dir.parent
    # Otherwise try git root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return Path.cwd()


def control_plane_file(project_root: Path, filename: str) -> Path:
    """Return canonical control-plane file path with legacy fallback."""
    canonical = project_root / CONTROL_PLANE_DIR / filename
    legacy = project_root / LEGACY_CONTROL_PLANE_DIR / filename
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    if canonical.parent.exists():
        return canonical
    if legacy.parent.exists():
        return legacy
    return canonical


def read_claude_md(project_root: Path, max_lines: int = 300) -> str:
    """Read CLAUDE.md from project root, truncating if necessary."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return "(No CLAUDE.md found in project root)"

    try:
        content = claude_md.read_text(encoding="utf-8")
        lines = content.split("\n")
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append("\n... (truncated)")
        return "\n".join(lines)
    except OSError:
        return "(Could not read CLAUDE.md)"


def sanitize_content(content: str, label: str) -> str:
    """Wrap untrusted content in XML data tags for prompt injection mitigation.

    Wraps content in <data> tags with a label and strips any existing closing
    tags to prevent tag escape. The corresponding prompt should instruct the
    model to treat <data> content strictly as data to review, not as
    instructions to follow.

    Not foolproof (no prompt injection mitigation is), but raises the bar
    significantly. The safe failure mode for CodeWarden is extra warnings,
    never missed violations.
    """
    # Strip existing </data> tags to prevent premature closure
    safe = content.replace("</data>", "")
    return f'<data type="{label}">\n{safe}\n</data>'


# --- LLM Backend Calls ---


def call_ollama(prompt: str) -> str:
    """Send prompt to Ollama local API."""
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "[ERROR] No response from model")
    except (urllib.error.URLError, TimeoutError) as e:
        return f"[ERROR] Ollama error: {e}"


def call_anthropic(prompt: str) -> str:
    """Send prompt to Anthropic API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "[ERROR] ANTHROPIC_API_KEY not set"

    model = os.environ.get("CODEWARDEN_MODEL", "claude-haiku-4-5-20251001")

    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "[ERROR] No response")
            return "[ERROR] Empty response from Anthropic"
    except (urllib.error.URLError, TimeoutError) as e:
        return f"[ERROR] Anthropic API error: {e}"


def call_openai(prompt: str) -> str:
    """Send prompt to OpenAI-compatible API."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "[ERROR] OPENAI_API_KEY not set"

    base_url = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    model = os.environ.get("CODEWARDEN_MODEL", "gpt-4o-mini")

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "[ERROR] No response")
            return "[ERROR] Empty response from OpenAI API"
    except (urllib.error.URLError, TimeoutError) as e:
        return f"[ERROR] OpenAI API error: {e}"


def call_claude(prompt: str) -> str:
    """Send prompt to Claude CLI (claude -p subprocess via stdin).

    Uses activity-based polling instead of a fixed timeout: the process
    is allowed to run as long as it is alive and producing output. Only
    killed after IDLE_TIMEOUT seconds of inactivity (no new stdout/stderr).
    """
    import selectors
    import time

    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        return "[ERROR] Claude CLI not found in PATH"

    idle_timeout = _get_timeout(default=120)  # kill after N seconds of silence
    poll_interval = 2  # check every 2 seconds

    try:
        proc = subprocess.Popen(
            [exe, "-p", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=(os.name == "nt"),
        )
    except FileNotFoundError:
        return "[ERROR] Claude CLI not found in PATH"
    except Exception as e:
        return f"[ERROR] Claude CLI launch error: {e}"

    # Send prompt and close stdin
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError as e:
        proc.kill()
        return f"[ERROR] Claude CLI stdin error: {e}"

    # Poll for output with activity-based timeout
    stdout_parts = []
    stderr_parts = []
    last_activity = time.time()

    while True:
        # Check if process finished
        ret = proc.poll()
        if ret is not None:
            # Process done - read remaining output
            rest_out = proc.stdout.read()
            rest_err = proc.stderr.read()
            if rest_out:
                stdout_parts.append(rest_out)
            if rest_err:
                stderr_parts.append(rest_err)
            break

        # Try to read available output (non-blocking on Windows via peek)
        got_output = False
        try:
            import msvcrt
            # Windows: check if data available on stdout
            if msvcrt.get_osfhandle(proc.stdout.fileno()):
                chunk = proc.stdout.read(4096) if proc.stdout.readable() else ""
                if chunk:
                    stdout_parts.append(chunk)
                    got_output = True
        except (ImportError, OSError, ValueError):
            pass

        if got_output:
            last_activity = time.time()
        elif time.time() - last_activity > idle_timeout:
            proc.kill()
            return (f"[ERROR] Claude CLI idle for {idle_timeout}s - killed. "
                    f"Partial output: {''.join(stdout_parts)[:200]}")

        time.sleep(poll_interval)

    if ret != 0:
        err = "".join(stderr_parts).strip()[:200]
        return f"[ERROR] Claude CLI error (rc={ret}): {err}"

    output = "".join(stdout_parts).strip()
    return output or "[ERROR] Empty response from Claude CLI"


def call_model(prompt: str) -> str:
    """Route only after explicit backend selection and approval."""
    backend = os.environ.get("CODEWARDEN_BACKEND", "").strip().lower()
    consent = os.environ.get("CODEWARDEN_CONSENT", "").strip().lower()
    supported = {"claude", "anthropic", "openai", "ollama"}

    if not backend or backend == "fallback":
        return "[ERROR] CodeWarden skipped: no backend configured explicitly"
    if backend not in supported:
        return f"[ERROR] CodeWarden skipped: unknown backend '{backend}'"
    if consent != "approved":
        return "[ERROR] CodeWarden skipped: explicit approval is required"

    if backend == "claude":
        return call_claude(prompt)
    elif backend == "anthropic":
        return call_anthropic(prompt)
    elif backend == "openai":
        return call_openai(prompt)
    return call_ollama(prompt)


# ---------------------------------------------------------------------------
# Domain detection and checklists (v3.1 / Sprint 2a)
# ---------------------------------------------------------------------------

def detect_domain(claude_md_text: str) -> str:
    """Detect the project domain from CLAUDE.md content.

    Looks at the Project Identity section and common keywords.
    Returns one of: financial, web, game, physics, data, generic.
    """
    text = claude_md_text.lower()

    domain_keywords = {
        "financial": ["trading", "order book", "financial", "ledger",
                      "transaction", "settlement", "portfolio", "exchange"],
        "web": ["web app", "api", "rest", "graphql", "frontend", "backend",
                "endpoint", "authentication", "spa", "ssr", "django",
                "flask", "express", "react", "vue", "angular"],
        "game": ["game", "player", "sprite", "render", "fps", "inventory",
                 "level", "scene", "gameloop", "unity", "godot", "unreal"],
        "physics": ["simulation", "physics", "conservation", "energy",
                    "momentum", "particle", "rigid body", "fluid",
                    "numerical", "integration", "verlet", "euler"],
        "data": ["pipeline", "etl", "kafka", "stream", "ingestion",
                 "batch", "schema evolution", "idempoten"],
    }

    scores = {d: 0 for d in domain_keywords}
    for domain, keywords in domain_keywords.items():
        for kw in keywords:
            if kw in text:
                scores[domain] += 1

    best = max(scores, key=scores.get)
    if scores[best] >= 2:
        return best
    return "generic"


# ---------------------------------------------------------------------------
# Impact Analysis (Section 9.18)
# ---------------------------------------------------------------------------

# Common identifiers to exclude from fingerprint extraction (too generic)
_COMMON_NAMES = frozenset({
    "main", "init", "__init__", "self", "cls", "args", "kwargs",
    "data", "test", "result", "value", "key", "item", "items",
    "name", "path", "file", "line", "text", "msg", "err", "log",
    "str", "int", "bool", "list", "dict", "set", "type", "none",
    "true", "false", "return", "import", "from", "class", "def",
    "get", "put", "post", "delete", "run", "call", "send",
    "read", "write", "open", "close", "start", "stop",
    "setup", "teardown", "config", "settings", "options",
})

# Maximum fingerprints to search (keeps grep fast)
MAX_FINGERPRINTS = 20

# Maximum lines to read from a file for identifier extraction
MAX_FILE_LINES = 500

# Maximum lines in impact section injected into prompt
MAX_IMPACT_LINES = 50

# Timeout per git grep pattern (seconds)
GREP_TIMEOUT = 5

# Total timeout for all greps (seconds)
GREP_TOTAL_TIMEOUT = 30


def extract_fingerprints(text: str, mode: str = "diff") -> list[str]:
    """Extract concept fingerprints from code diff or plan text.

    Fingerprints are patterns that identify related code elsewhere in the
    codebase. Used by Impact Analysis (Section 9.18) to find files that
    may be affected by a planned change.

    Args:
        text: Code diff or plan text to extract patterns from.
        mode: "diff" for git diff content, "plan" for natural language plan.

    Returns:
        Deduplicated list of search patterns, max MAX_FINGERPRINTS items.
    """
    import re

    patterns = []

    if mode == "diff":
        # Function definitions (Python, JS/TS, Go, Rust)
        patterns.extend(re.findall(r'def\s+(\w{3,})\s*\(', text))
        patterns.extend(re.findall(r'function\s+(\w{3,})\s*\(', text))
        patterns.extend(re.findall(r'func\s+(\w{3,})\s*\(', text))
        patterns.extend(re.findall(r'fn\s+(\w{3,})\s*\(', text))

        # Class definitions (class name + parent if present)
        patterns.extend(re.findall(r'class\s+(\w{3,})', text))
        patterns.extend(re.findall(r'class\s+\w+\((\w{3,})', text))

        # Python imports (the module being imported from)
        patterns.extend(re.findall(r'from\s+([\w.]+)\s+import', text))

        # Subprocess command names (both subprocess.run("cmd") and ["cmd", ...])
        patterns.extend(re.findall(
            r'subprocess\.\w+\([^)]*["\'](\w[\w.-]+)["\']', text
        ))
        patterns.extend(re.findall(
            r'subprocess\.\w+\(\s*\[\s*["\'](\w[\w.-]+)["\']', text
        ))
        patterns.extend(re.findall(
            r'create_subprocess_\w+\([^)]*["\'](\w[\w.-]+)["\']', text
        ))
        patterns.extend(re.findall(
            r'create_subprocess_\w+\(\s*\w*\s*,?\s*["\'](\w[\w.-]+)["\']', text
        ))

        # Environment variable names
        patterns.extend(re.findall(
            r'os\.environ\.get\(\s*["\'](\w{3,})["\']', text
        ))
        patterns.extend(re.findall(
            r'os\.environ\[\s*["\'](\w{3,})["\']', text
        ))

        # Constants (UPPER_CASE assignments)
        patterns.extend(re.findall(
            r'^[+]?\s*([A-Z][A-Z_]{2,})\s*=', text, re.MULTILINE
        ))

    elif mode == "plan":
        # File paths in plan text
        patterns.extend(re.findall(
            r'[\w/\\]+\.(?:py|js|ts|go|rs|java|rb|sh|c|cpp|h)', text
        ))

        # Backtick-quoted identifiers (common in markdown)
        patterns.extend(re.findall(r'`(\w{3,}(?:\.\w+)*)`', text))

        # Double-quoted identifiers
        patterns.extend(re.findall(r'"(\w{3,})"', text))

        # Snake_case/camelCase after action verbs
        patterns.extend(re.findall(
            r'(?:modify|change|update|rename|move|call|use|edit|fix|add)\s+'
            r'(\w{3,})',
            text, re.IGNORECASE,
        ))

    # Filter and deduplicate
    seen = set()
    result = []
    for p in patterns:
        p_lower = p.lower()
        if p_lower in _COMMON_NAMES:
            continue
        if len(p) < 3:
            continue
        if p_lower not in seen:
            seen.add(p_lower)
            result.append(p)

    # Cap at MAX_FINGERPRINTS, prefer longer (more specific) patterns
    result.sort(key=len, reverse=True)
    return result[:MAX_FINGERPRINTS]


def grep_codebase(
    project_root: Path,
    patterns: list[str],
    exclude_files: list[str] | None = None,
) -> dict[str, list[str]]:
    """Search codebase for patterns using git grep.

    Returns {pattern: [matching_file_1, matching_file_2, ...]}
    excluding files listed in exclude_files.

    Uses git grep for speed (only tracked files, respects .gitignore).
    Fails safe: returns empty dict on any error.

    Args:
        project_root: Root directory of the git repository.
        patterns: List of literal string patterns to search for.
        exclude_files: File paths to exclude from results (already in plan/diff).

    Returns:
        Dict mapping each pattern to a list of matching file paths.
        Empty dict if git grep is unavailable or fails.
    """
    import time

    exclude_set = set(exclude_files or [])
    # Normalize: strip leading slashes and ./ prefixes
    exclude_set = {f.lstrip("./\\") for f in exclude_set}

    results: dict[str, list[str]] = {}
    t0 = time.monotonic()

    for pattern in patterns[:MAX_FINGERPRINTS]:
        # Total timeout safety net
        if time.monotonic() - t0 > GREP_TOTAL_TIMEOUT:
            break

        try:
            proc = subprocess.run(
                ["git", "grep", "-l", "--fixed-strings", pattern],
                capture_output=True,
                text=True,
                timeout=GREP_TIMEOUT,
                cwd=str(project_root),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                files = [
                    f.strip() for f in proc.stdout.strip().split("\n")
                    if f.strip() and f.strip() not in exclude_set
                ]
                if files:
                    results[pattern] = files
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    return results


def read_file_identifiers(project_root: Path, file_path: str) -> list[str]:
    """Read a source file and extract exported identifiers.

    Finds function, class, and constant definitions in the file.
    Used to find callsites for files mentioned in a plan: if the plan
    says "modify foo.py", we read foo.py and extract what it defines,
    then grep for those definitions elsewhere.

    Args:
        project_root: Root directory of the project.
        file_path: Relative path to the file to read.

    Returns:
        List of identifier names defined in the file.
        Empty list if file doesn't exist or can't be read.
    """
    import re

    full_path = project_root / file_path
    if not full_path.exists():
        return []

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")[:MAX_FILE_LINES]
        text = "\n".join(lines)
    except OSError:
        return []

    identifiers = []

    # Function definitions
    identifiers.extend(re.findall(r'def\s+(\w{3,})\s*\(', text))
    identifiers.extend(re.findall(r'function\s+(\w{3,})\s*\(', text))
    identifiers.extend(re.findall(r'func\s+(\w{3,})\s*\(', text))

    # Class definitions
    identifiers.extend(re.findall(r'class\s+(\w{3,})', text))

    # Constants (UPPER_CASE)
    identifiers.extend(re.findall(
        r'^\s*([A-Z][A-Z_]{2,})\s*=', text, re.MULTILINE
    ))

    # Subprocess command names (the external commands this file wraps)
    identifiers.extend(re.findall(
        r'subprocess\.\w+\([^)]*["\'](\w[\w.-]+)["\']', text
    ))
    identifiers.extend(re.findall(
        r'subprocess\.\w+\(\s*\[\s*["\'](\w[\w.-]+)["\']', text
    ))
    identifiers.extend(re.findall(
        r'create_subprocess_\w+\([^)]*["\'](\w[\w.-]+)["\']', text
    ))

    # Filter common names and deduplicate
    seen = set()
    result = []
    for name in identifiers:
        if name.lower() in _COMMON_NAMES and name not in ("Path",):
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)

    return result


def format_impact_section(
    impact_results: dict[str, list[str]],
    plan_files: list[str],
) -> str:
    """Format grep results as a markdown section for the review prompt.

    Produces a human-readable summary of files that match patterns from
    the plan/diff but are NOT listed as files to modify.

    Args:
        impact_results: {pattern: [file1, file2, ...]} from grep_codebase.
        plan_files: Files already mentioned in the plan/diff.

    Returns:
        Markdown section string, or empty string if no results.
    """
    if not impact_results:
        return ""

    lines = [
        "## Impact Scan Results",
        "",
        "Files matching patterns from this plan/diff but NOT listed as modified:",
        "",
    ]

    plan_set = {f.lstrip("./\\") for f in plan_files}
    total_lines = 4

    for pattern, files in impact_results.items():
        # Filter out files that are in the plan (double-check)
        unmentioned = [f for f in files if f not in plan_set]
        if not unmentioned:
            continue

        file_list = ", ".join(unmentioned[:5])
        if len(unmentioned) > 5:
            file_list += f" (+{len(unmentioned) - 5} more)"

        line = f"- Pattern `{pattern}`: {file_list}"
        lines.append(line)
        total_lines += 1

        if total_lines >= MAX_IMPACT_LINES:
            lines.append("- ... (impact results truncated)")
            break

    if total_lines <= 4:
        # No unmentioned files found
        return ""

    lines.append("")
    lines.append(
        "These files MAY need changes for consistency. "
        "Evaluate whether each is affected by the planned modifications."
    )

    return "\n".join(lines)


FITNESS_ARCHITECTURE_TAGS = (
    "boundary_violation",
    "layer_violation",
    "cohesion_smell",
    "god_file_risk",
    "coupling_regression",
    "contract_erosion",
    "misplaced_logic",
    "responsibility_accretion",
)
_FITNESS_SEVERITY_ORDER = {"fail": 0, "warn": 1}
MAX_FITNESS_SIGNAL_LINES = 14


def _normalize_relpath(path_value) -> str:
    if not path_value:
        return ""
    return str(path_value).replace("\\", "/").lstrip("./")


def _load_fitness_module(project_root: Path):
    """Best-effort load of project-local fitness_check.py."""
    for candidate in (
        project_root / "tools" / "fitness_check.py",
        project_root / "scripts" / "fitness_check.py",
    ):
        if not candidate.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "_cc_fitness_check_runtime", candidate
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return None


def _module_name_from_relpath(file_ref: str) -> str:
    if not file_ref.endswith(".py"):
        return ""
    return ".".join(Path(file_ref).with_suffix("").parts)


def _append_fitness_signal(signals: list[dict], architecture_tag: str,
                           severity: str, summary: str, file_ref: str = "",
                           line: int | None = None, evidence: str = "",
                           modules: list[str] | None = None):
    if architecture_tag not in FITNESS_ARCHITECTURE_TAGS:
        return
    signals.append({
        "architecture_tag": architecture_tag,
        "severity": severity if severity in {"fail", "warn"} else "warn",
        "summary": summary.strip(),
        "file": _normalize_relpath(file_ref),
        "line": int(line) if isinstance(line, int) else 0,
        "evidence": evidence.strip(),
        "modules": [str(value).strip() for value in (modules or []) if str(value).strip()],
        "evidence_source": "fitness_check",
    })


def _signal_matches_focus(signal: dict, focus_files: set[str],
                          focus_modules: set[str]) -> bool:
    if not focus_files and not focus_modules:
        return False
    file_ref = signal.get("file", "")
    if file_ref and file_ref in focus_files:
        return True
    module_name = _module_name_from_relpath(file_ref)
    if module_name and module_name in focus_modules:
        return True
    for module_name in signal.get("modules", []):
        if module_name in focus_modules:
            return True
    return False


def _signal_sort_key(signal: dict):
    return (
        _FITNESS_SEVERITY_ORDER.get(signal.get("severity", "warn"), 9),
        0 if signal.get("file") else 1,
        signal.get("architecture_tag", ""),
        signal.get("file", ""),
        signal.get("summary", ""),
    )


def _architecture_tag_label(tag: str) -> str:
    return tag.replace("_", " ")


def _format_signal_line(signal: dict) -> str:
    tag_label = _architecture_tag_label(signal.get("architecture_tag", "architecture"))
    severity = str(signal.get("severity", "warn")).upper()
    location = ""
    if signal.get("file"):
        location = f"`{signal['file']}`"
        if signal.get("line"):
            location += f":{signal['line']}"
    summary = signal.get("summary", "")
    line = f"- [{tag_label}] [{severity}]"
    if location:
        line += f" {location} - {summary}"
    else:
        line += f" {summary}"
    if signal.get("evidence"):
        line += f" Evidence: {signal['evidence']}."
    return line


def _format_fitness_review_section(signals: list[dict], focus_files: set[str],
                                   focus_modules: set[str], title: str) -> str:
    if not signals:
        return ""

    ordered = sorted(signals, key=_signal_sort_key)
    focused = [signal for signal in ordered if _signal_matches_focus(signal, focus_files, focus_modules)]
    if focus_files or focus_modules:
        remaining = [signal for signal in ordered if signal not in focused]
        global_hotspots = [signal for signal in remaining if signal.get("severity") == "fail"]
    else:
        focused = ordered[:MAX_FITNESS_SIGNAL_LINES]
        global_hotspots = []

    lines = [
        f"## {title}",
        "",
        "Mechanical architecture signals from repo-side fitness analysis.",
        "Treat them as concrete evidence, not as automatic violations.",
        "Only escalate when the current plan/diff contributes to the drift, touches the hotspot, or leaves a documented contract unresolved.",
        "",
    ]

    if focused:
        heading = (
            "### Signals touching changed or planned files"
            if (focus_files or focus_modules)
            else "### Current architecture hotspots"
        )
        lines.append(heading)
        lines.append("")
        for signal in focused[:MAX_FITNESS_SIGNAL_LINES]:
            lines.append(_format_signal_line(signal))

    if global_hotspots:
        lines.append("")
        lines.append("### Related global hotspots")
        lines.append("")
        for signal in global_hotspots[:6]:
            lines.append(_format_signal_line(signal))

    return "\n".join(lines)


def load_fitness_review_context(project_root: Path,
                                focus_files: list[str] | None = None) -> dict:
    """Collect concise architectural fitness evidence for CodeWarden prompts."""
    result = {
        "signal_count": 0,
        "focus_signal_count": 0,
        "prompt_section": "",
        "report_section": "",
    }

    fitness = _load_fitness_module(project_root)
    if fitness is None:
        return result

    try:
        config = fitness.load_config(project_root)
        thresholds = config.get("thresholds", {})
        skip_dirs = config.get("skip_dirs", [])
        zones = fitness.detect_zones(project_root, config.get("zones", {}))
        zone_files = fitness.count_zone_files(project_root, zones, skip_dirs)
        git_data = fitness.analyze_git(
            project_root, zones, config.get("git_history_commits", 100)
        )
        import_data = fitness.analyze_python_imports(project_root, zones, skip_dirs)
        instability = fitness.compute_instability(import_data, zones, project_root, skip_dirs)

        structure_data: dict = {
            "god_file_risks": [],
            "god_function_risks": [],
            "god_class_risks": [],
            "fan_out_risks": [],
            "fan_in_risks": [],
            "dependency_cycles": [],
        }
        has_python = any(
            path.suffix == ".py"
            for path in fitness.source_files(project_root, skip_dirs)
        )
        if has_python:
            structure_data = fitness.analyze_python_structure(
                project_root, zones, skip_dirs, thresholds
            )
            structure_data.update(
                fitness.analyze_dependency_graph(import_data, thresholds)
            )

        gateway_rules = list(config.get("gateway_rules", [])) + fitness._load_gateway_rules_from_cc_config(project_root)
        structure_data["gateway_rule_violations"] = fitness.evaluate_gateway_rules(
            project_root, skip_dirs, gateway_rules
        )
        ownership_rules = list(config.get("ownership_rules", [])) + fitness._load_scoped_pattern_rules_from_cc_config(
            project_root, "ownership_rules", "ownership"
        )
        mutation_rules = list(config.get("mutation_rules", [])) + fitness._load_scoped_pattern_rules_from_cc_config(
            project_root, "mutation_rules", "mutation"
        )
        structure_data["ownership_rule_violations"] = fitness.evaluate_scoped_pattern_rules(
            project_root, zones, skip_dirs, ownership_rules, "ownership"
        )
        structure_data["mutation_rule_violations"] = fitness.evaluate_scoped_pattern_rules(
            project_root, zones, skip_dirs, mutation_rules, "mutation"
        )
        layer_rule_violations = fitness.evaluate_layer_rules(
            import_data, config.get("layer_rules", [])
        )

        snapshot = {
            "timestamp": "",
            "zone_files": zone_files,
            "direction_violations": len(import_data.get("violations", [])),
        }
        deltas = fitness.compute_deltas(
            snapshot, fitness.load_history(project_root), thresholds
        )

        signals: list[dict] = []

        for violation in import_data.get("violations", []):
            _append_fitness_signal(
                signals,
                "boundary_violation",
                "fail",
                f"Dependency direction violation: {violation.get('from_zone')} imports {violation.get('target_zone')} via `{violation.get('imports')}`",
                file_ref=violation.get("file", ""),
                line=violation.get("line"),
                evidence=f"Zone order breach at line {violation.get('line')}",
            )

        for violation in layer_rule_violations:
            _append_fitness_signal(
                signals,
                "layer_violation",
                violation.get("severity", "fail"),
                violation.get("message", "Layer rule violated"),
                file_ref=violation.get("file", ""),
                line=violation.get("line"),
                evidence=f"`{violation.get('imports')}` -> {violation.get('target_zone')}",
            )

        for violation in structure_data.get("gateway_rule_violations", []):
            _append_fitness_signal(
                signals,
                "boundary_violation",
                violation.get("severity", "fail"),
                violation.get("message", "Gateway rule violated"),
                file_ref=violation.get("file", ""),
                line=violation.get("line"),
                evidence=f"Gateway-only pattern `{violation.get('pattern', '')}` used outside approved files",
            )

        for violation in structure_data.get("ownership_rule_violations", []):
            _append_fitness_signal(
                signals,
                "responsibility_accretion",
                violation.get("severity", "fail"),
                violation.get("message", "Ownership rule violated"),
                file_ref=violation.get("file", ""),
                line=violation.get("line"),
                evidence=f"Zone `{violation.get('zone', '')}` matched `{violation.get('pattern', '')}`",
            )

        for violation in structure_data.get("mutation_rule_violations", []):
            _append_fitness_signal(
                signals,
                "misplaced_logic",
                violation.get("severity", "fail"),
                violation.get("message", "Mutation rule violated"),
                file_ref=violation.get("file", ""),
                line=violation.get("line"),
                evidence=f"Zone `{violation.get('zone', '')}` matched `{violation.get('pattern', '')}`",
            )

        for risk in structure_data.get("god_file_risks", []):
            _append_fitness_signal(
                signals,
                "god_file_risk",
                risk.get("severity", "warn"),
                f"God-file risk in `{risk.get('file', '')}`",
                file_ref=risk.get("file", ""),
                evidence=", ".join(risk.get("reasons", [])),
            )

        for risk in structure_data.get("god_function_risks", []):
            _append_fitness_signal(
                signals,
                "cohesion_smell",
                "warn",
                f"Oversized function `{risk.get('name', '')}` in `{risk.get('file', '')}`",
                file_ref=risk.get("file", ""),
                evidence=f"{risk.get('lines', 0)} lines",
            )

        for risk in structure_data.get("god_class_risks", []):
            _append_fitness_signal(
                signals,
                "cohesion_smell",
                "warn",
                f"God-object growth risk in `{risk.get('name', '')}`",
                file_ref=risk.get("file", ""),
                evidence=", ".join(risk.get("reasons", [])),
            )

        max_instability = thresholds.get("instability_shared_max", 0.3)
        for module_name, value in instability.items():
            if value <= max_instability:
                continue
            _append_fitness_signal(
                signals,
                "coupling_regression",
                "warn",
                f"Shared module `{module_name}` is unstable",
                file_ref=module_name,
                evidence=f"instability={value} exceeds {max_instability}",
            )

        for risk in structure_data.get("fan_out_risks", []):
            _append_fitness_signal(
                signals,
                "coupling_regression",
                risk.get("severity", "warn"),
                f"High fan-out from `{risk.get('file', '')}`",
                file_ref=risk.get("file", ""),
                evidence=f"{risk.get('count', 0)} internal dependencies",
            )

        for risk in structure_data.get("fan_in_risks", []):
            _append_fitness_signal(
                signals,
                "coupling_regression",
                risk.get("severity", "warn"),
                f"High fan-in into `{risk.get('file', '')}`",
                file_ref=risk.get("file", ""),
                evidence=f"{risk.get('count', 0)} internal dependents",
            )

        for cycle in structure_data.get("dependency_cycles", []):
            modules = [str(value) for value in cycle.get("modules", []) if str(value)]
            _append_fitness_signal(
                signals,
                "coupling_regression",
                cycle.get("severity", "warn"),
                f"Import cycle across {cycle.get('size', 0)} modules",
                evidence=", ".join(modules),
                modules=modules,
            )

        for delta in deltas:
            if delta.get("type") == "zone_growth":
                summary = (
                    f"{delta.get('zone')} zone grew {delta.get('growth_pct')}% "
                    f"({delta.get('previous')} -> {delta.get('current')} files)"
                )
            elif delta.get("type") == "violations_increase":
                summary = (
                    "Dependency direction violations increased "
                    f"({delta.get('previous')} -> {delta.get('current')})"
                )
            else:
                summary = "Architecture fitness trend worsened"
            _append_fitness_signal(
                signals,
                "contract_erosion",
                "warn",
                summary,
                evidence="Detected from fitness history delta",
            )

        normalized_focus_files = {
            _normalize_relpath(path_value)
            for path_value in (focus_files or [])
            if _normalize_relpath(path_value)
        }
        focus_modules = {
            _module_name_from_relpath(file_ref)
            for file_ref in normalized_focus_files
            if _module_name_from_relpath(file_ref)
        }

        focused = [
            signal for signal in signals
            if _signal_matches_focus(signal, normalized_focus_files, focus_modules)
        ]
        result["signal_count"] = len(signals)
        result["focus_signal_count"] = len(focused)
        result["prompt_section"] = _format_fitness_review_section(
            signals,
            normalized_focus_files,
            focus_modules,
            title="Architectural Fitness Signals",
        )
        result["report_section"] = _format_fitness_review_section(
            signals,
            normalized_focus_files,
            focus_modules,
            title="Architectural Fitness Evidence",
        )
        return result
    except Exception:
        return result


DOMAIN_CHECKLISTS = {
    "financial": (
        "Domain-specific checks (financial):\n"
        "- Decimal precision: monetary values must NOT use float/double\n"
        "- Transaction atomicity: state changes must be all-or-nothing\n"
        "- Audit trail: every state mutation must be logged\n"
        "- Race conditions: concurrent order processing must be serialized\n"
        "- Conservation of value: sum(debits) == sum(credits) at all times\n"
    ),
    "web": (
        "Domain-specific checks (web):\n"
        "- SQL injection: all queries must use parameterized statements\n"
        "- XSS: user input must be escaped before rendering\n"
        "- CSRF: state-changing endpoints need token protection\n"
        "- Input validation: all external input validated before use\n"
        "- Secrets: no hardcoded passwords, API keys, or tokens\n"
    ),
    "game": (
        "Domain-specific checks (game):\n"
        "- Frame independence: behavior must be identical at any frame rate\n"
        "- State serialization: save/load must preserve exact state\n"
        "- Resource lifecycle: every loaded resource must be freed\n"
        "- Determinism: same input must produce same output\n"
    ),
    "physics": (
        "Domain-specific checks (physics):\n"
        "- Conservation laws: energy/momentum must be conserved within tolerance\n"
        "- Numerical stability: no division by zero, no NaN propagation\n"
        "- Determinism: same seed + same input = identical output\n"
        "- Float comparison: use epsilon, never exact equality\n"
    ),
    "data": (
        "Domain-specific checks (data pipeline):\n"
        "- Idempotency: re-running must produce the same result\n"
        "- Ordering: message/event ordering must be preserved\n"
        "- Schema evolution: changes must be backward compatible\n"
        "- Backpressure: producers must handle slow consumers\n"
    ),
    "generic": "",
}
