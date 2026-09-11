#!/usr/bin/env python3
"""consult.py - External consultation tool for ControlCoding debug (L3).

Sends a structured debug prompt to an external LLM WITHOUT project context.
The local AI prepares a focused data package; this script sends it to a
clean model instance for independent analysis.

The key property: the external model has NO access to the project codebase,
no history of previous attempts, and no bias from failed solutions. It
reasons purely from the data provided in the prompt.

This breaks tunnel vision: when the local AI is stuck in a loop, the
external consultant offers fresh hypotheses based on first principles.

Usage:
    # Interactive: pass problem description directly
    python consult.py --backend ollama --prompt "Floor geometry is being drawn but invisible. GL_CULL_FACE is enabled. Here are the vertex indices: [0,1,2,2,3,0]. Vertices are at y=0. Camera is above at y=1.6."

    # With data file: attach code snippets or logs
    python consult.py --backend openai --prompt "Lighting has no visible effect on floor" --data-file debug_data.txt

    # Select a backend explicitly
    python consult.py --prompt "..." --backend anthropic --model claude-haiku-4-5-20251001
    python consult.py --prompt "..." --backend ollama --model qwen2.5:14b

Backends (explicit selection required):
    - anthropic: requires ANTHROPIC_API_KEY environment variable
    - claude: optional adapter using the official Claude Code CLI
    - ollama: local adapter that requires Ollama running with a model
    - openai: OpenAI-compatible API (OpenAI, Azure, or a local endpoint)

The prompt should contain ONLY:
    - Problem description (what's expected vs what happens)
    - Relevant code snippets (not the full file, just the relevant section)
    - Specific data (vertex positions, shader output, log lines)
    - What has already been tried and failed

The prompt should NOT contain:
    - Full file contents
    - Project architecture description
    - Unrelated code
    - The AI's internal reasoning or previous attempts
"""

import argparse
import json
import os
import sys
from pathlib import Path

SYSTEM_PROMPT = """\
You are an external debugging consultant. You have NO access to the project \
codebase and NO history of previous debugging attempts.

You will receive a structured problem description with selected code snippets \
and data. Your job is to:

1. Analyze the data provided - do not assume anything not explicitly stated
2. Form hypotheses about the root cause, ranked by likelihood
3. For each hypothesis, suggest a specific diagnostic step to confirm or eliminate it
4. If the data is insufficient, state exactly what additional information you need

Rules:
- Focus on ROOT CAUSES, not workarounds or parameter tweaks
- Be specific: "check if X equals Y" not "look into the rendering"
- If you see a likely answer, state it directly with your reasoning
- Do not hedge excessively - give your best assessment with confidence levels
"""

# Output format for machine-readable results
RESULT_SEPARATOR = "--- EXTERNAL CONSULTATION ---"
RESULT_END = "--- END ---"
SUPPORTED_BACKENDS = ("anthropic", "claude", "ollama", "openai")

# Per-role tools for agent-mode (claude backend only).
# Non-claude backends ignore these (text-only, no agent loop).
ROLE_TOOLS = {
    "debug": ["WebSearch", "WebFetch"],
    "architect": ["WebSearch", "WebFetch"],
    "planner": ["WebSearch", "WebFetch"],
    "reviewer": [],  # pure code analysis
    "socratic": ["WebSearch", "WebFetch"],
}


def _project_root() -> Path:
    """Return the active project root for direct consult invocations."""
    return Path(os.environ.get("SESSION_PROJECT_ROOT", ".")).resolve()


def _control_plane_read_path(filename: str) -> Path:
    """Return canonical control-plane path with legacy fallback."""
    project_root = _project_root()
    canonical = project_root / ".controlcoding" / filename
    legacy = project_root / ".claude" / filename
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def _check_runtime_specialist_gate(role_ref: str, backend: str, model: str) -> dict:
    """Apply the same specialist runtime gate used by routed CC paths."""
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from control_plane_utils import check_specialist_runtime
    except ImportError:
        return {
            "allowed": False,
            "enforced": True,
            "reason": "control_plane_utils_unavailable",
            "detail": (
                "Control plane governance is unavailable; refusing "
                "specialist runtime path."
            ),
            "component": "consultant",
            "role_ref": role_ref,
            "backend": backend,
            "model": model,
        }

    try:
        return check_specialist_runtime(
            role_ref=role_ref,
            requested_backend=backend,
            requested_model=model,
            component="consultant",
            config_path=str(_control_plane_read_path("cc_engagement.json")),
            gateway_config_path=str(_control_plane_read_path("gateway_config.json")),
            event_log_path=str(_control_plane_read_path("event_log.jsonl")),
        )
    except Exception as exc:
        return {
            "allowed": False,
            "enforced": True,
            "reason": "governance_check_failed",
            "detail": f"Specialist runtime governance check failed: {exc}",
            "component": "consultant",
            "role_ref": role_ref,
            "backend": backend,
            "model": model,
        }


def _format_gate_detail(gate: dict) -> str:
    """Return stable user-facing gate detail with reason when available."""
    reason = str(gate.get("reason") or "").strip()
    detail = str(gate.get("detail") or "").strip()
    if reason and detail:
        return f"{reason}: {detail}"
    return reason or detail or "governance_denied"


def _load_image_b64(image_path: str) -> str | None:
    """Load an image file and return base64-encoded string."""
    import base64
    p = Path(image_path)
    if not p.exists():
        print(f"[L3] WARNING: image not found: {image_path}")
        return None
    try:
        return base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError as e:
        print(f"[L3] WARNING: could not read image: {e}")
        return None


def call_ollama(prompt: str, model: str = "qwen2.5:14b",
                image_path: str = "") -> str:
    """Send prompt to local Ollama instance. Supports vision models."""
    try:
        import urllib.request
        import urllib.error

        payload = {
            "model": model,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
        }

        # Add image for vision models
        if image_path:
            img_b64 = _load_image_b64(image_path)
            if img_b64:
                payload["images"] = [img_b64]

        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "")
    except urllib.error.URLError:
        print("[L3] ERROR: cannot connect to Ollama at localhost:11434")
        print("[L3] Make sure Ollama is running: ollama serve")
        sys.exit(1)
    except Exception as e:
        print(f"[L3] ERROR calling Ollama: {e}")
        sys.exit(1)


def call_anthropic(prompt: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Send prompt to Anthropic API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[L3] ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    try:
        import urllib.request

        data = json.dumps({
            "model": model,
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]
    except Exception as e:
        print(f"[L3] ERROR calling Anthropic API: {e}")
        sys.exit(1)


def call_openai(prompt: str, model: str = "gpt-4o") -> str:
    """Send prompt to OpenAI-compatible API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[L3] ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    base_url = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")

    try:
        import urllib.request

        data = json.dumps({
            "model": model,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return "ERROR: Empty response from OpenAI-compatible API."
    except Exception as e:
        print(f"[L3] ERROR calling OpenAI API: {e}")
        sys.exit(1)


def call_claude(
    prompt: str, model: str = "", tools: list[str] | None = None
) -> str:
    """Send prompt through the optional official Claude Code CLI adapter."""
    import shutil
    import subprocess

    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        print("[L3] ERROR: Claude CLI not found in PATH")
        sys.exit(1)

    full_message = f"{SYSTEM_PROMPT}\n\n---\n\n{prompt}"
    cmd = [exe, "-p", "-", "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])
    if tools:
        cmd.extend(["--allowedTools", ",".join(tools)])

    try:
        result = subprocess.run(
            cmd,
            input=full_message,
            capture_output=True,
            text=True,
            timeout=300,
            shell=(os.name == "nt"),
            cwd=os.environ.get("TEMP", "/tmp"),
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            print(f"[L3] ERROR: Claude CLI failed: {stderr or 'non-zero exit'}")
            sys.exit(1)
        return result.stdout.strip()
    except Exception as e:
        print(f"[L3] ERROR calling Claude CLI: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="External debug consultation (L3): send problem to isolated LLM."
    )
    parser.add_argument(
        "--prompt", required=True,
        help="Problem description with relevant data",
    )
    parser.add_argument(
        "--data-file", default=None,
        help="Optional file with additional code snippets or logs to include",
    )
    parser.add_argument(
        "--backend", required=True,
        choices=SUPPORTED_BACKENDS,
        help="Consultation backend to use (explicit selection required)",
    )
    parser.add_argument(
        "--model", default=None,
        help="Override the default model name",
    )
    parser.add_argument(
        "--role", default="debug",
        choices=["debug", "architect", "planner", "reviewer", "socratic"],
        help="Consultation role (default: debug). With claude backend, "
             "non-reviewer roles get WebSearch/WebFetch tools.",
    )
    parser.add_argument(
        "--image", default=None,
        help="Path to screenshot/image for visual analysis (vision models)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Save consultation result to file (in addition to stdout)",
    )
    args = parser.parse_args()

    # Build the full prompt
    full_prompt = args.prompt
    role_tools = list(ROLE_TOOLS.get(args.role, []))
    if args.data_file:
        data_path = Path(args.data_file)
        if not data_path.exists():
            print(f"[L3] ERROR: data file not found: {data_path}")
            sys.exit(1)
        full_prompt += "\n\n--- ATTACHED DATA ---\n" + data_path.read_text(
            encoding="utf-8", errors="replace"
        )

    # Image support: add to prompt for text-only backends, pass directly for vision
    image_path = args.image or ""
    if image_path:
        abs_image = str(Path(image_path).resolve())
        print(f"[L3] Image: {abs_image}")
        # For claude backend: tell it to Read the image file
        if args.backend == "claude":
            full_prompt += (
                f"\n\n## Screenshot\n"
                f"Read and analyze the screenshot at: {abs_image}\n"
                f"Compare what you see against the expected visual criteria."
            )
            if "Read" not in role_tools:
                role_tools = list(role_tools) + ["Read"]

    # Call the selected backend
    print(f"[L3] Consulting external model ({args.backend}, role={args.role})...")
    print(f"[L3] Prompt length: {len(full_prompt)} chars")
    if role_tools and args.backend == "claude":
        print(f"[L3] Agent mode: tools={role_tools}")
    requested_model = (
        args.model or ""
        if args.backend == "claude"
        else args.model or "gpt-4o"
        if args.backend == "openai"
        else args.model or "claude-haiku-4-5-20251001"
        if args.backend == "anthropic"
        else args.model or "qwen2.5:14b"
    )
    gate = _check_runtime_specialist_gate(args.role, args.backend, requested_model)
    if not gate.get("allowed", True):
        print(f"\n{RESULT_SEPARATOR}")
        print(f"[GATED] {_format_gate_detail(gate)}")
        print(RESULT_END)
        return

    backend = str(gate.get("backend") or "").strip().lower() or args.backend
    if backend not in SUPPORTED_BACKENDS:
        print("[L3] ERROR: runtime gate returned an unsupported consultation backend.")
        return 1

    model = gate.get("model") or requested_model
    if backend == "claude":
        result = call_claude(full_prompt, model, tools=role_tools)
    elif backend == "openai":
        result = call_openai(full_prompt, model)
    elif backend == "anthropic":
        result = call_anthropic(full_prompt, model)
    elif backend == "ollama":
        result = call_ollama(full_prompt, model, image_path=image_path)

    # Output
    print(f"\n{RESULT_SEPARATOR}")
    print(result)
    print("[costStatus: unknown]")
    print(RESULT_END)

    # Optionally save to file
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding="utf-8")
        print(f"\n[L3] Result saved to: {output_path}")


if __name__ == "__main__":
    raise SystemExit(main())
