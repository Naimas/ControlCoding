#!/usr/bin/env python3
"""cc_dashboard.py - ControlCoding Web UI Dashboard.

Read-only web dashboard for monitoring ControlCoding projects:
agents, bridge messages, session status, debug activity, and metrics.

Usage:
    python cc_dashboard.py --project-root /path/to/project
    python cc_dashboard.py --project-root . --bridge-dir .bridge --port 7860

Requirements:
    pip install gradio
"""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import gradio as gr

# --- Data Loading ---


def _control_plane_log_path(project_root: Path, filename: str) -> Path:
    canonical = project_root / ".controlcoding" / filename
    if canonical.exists():
        return canonical
    legacy = project_root / ".claude" / filename
    if legacy.exists():
        return legacy
    return canonical


def load_jsonl(path: Path) -> list:
    """Load a JSONL file, return list of dicts."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def load_json(path: Path) -> dict:
    """Load a JSON file."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_md(path: Path) -> str:
    """Load a markdown file."""
    if not path.exists():
        return "*File not found.*"
    return path.read_text(encoding="utf-8")


# --- Tab: Agents ---


def render_agents(bridge_dir: Path) -> str:
    agents = load_json(bridge_dir / "agents.json")
    if not agents:
        return "No agents registered. The bridge has not been used yet."

    lines = ["| Agent | Last Seen | Status |", "|---|---|---|"]
    for aid, info in agents.items():
        last_seen = info.get("last_seen", "?")
        status = info.get("status", "?")
        lines.append(f"| {aid} | {last_seen} | {status} |")
    return "\n".join(lines)


# --- Tab: Bridge Messages ---


def render_bridge(bridge_dir: Path, filter_agent: str = "") -> str:
    msg_dir = bridge_dir / "messages"
    if not msg_dir.exists():
        return "No bridge messages directory found."

    files = sorted(msg_dir.glob("*.json"))
    if not files:
        return "No messages yet."

    messages = []
    for f in files:
        msg = load_json(f)
        if msg:
            messages.append(msg)

    if filter_agent:
        messages = [
            m for m in messages
            if m.get("from") == filter_agent or m.get("to") == filter_agent
        ]

    if not messages:
        return "No messages matching filter."

    lines = ["| # | From | To | Status | Time | Content |", "|---|---|---|---|---|---|"]
    for msg in messages[-50:]:  # Last 50
        content = msg.get("content", "")[:80]
        reply = f" re:#{msg['reply_to']}" if msg.get("reply_to") else ""
        lines.append(
            f"| {msg.get('id', '?')} | {msg.get('from', '?')} | "
            f"{msg.get('to', '?')}{reply} | {msg.get('status', '?')} | "
            f"{msg.get('timestamp', '?')[:19]} | {content}... |"
        )
    return "\n".join(lines)


# --- Tab: Session ---


def render_session(project_root: Path) -> tuple:
    status = load_md(project_root / "STATUS.md")

    devlog_dir = project_root / "devlog"
    devlogs = ""
    if devlog_dir.exists():
        files = sorted(devlog_dir.glob("*.md"))[-5:]
        parts = []
        for f in files:
            parts.append(f"### {f.name}\n\n{f.read_text(encoding='utf-8')}")
        devlogs = "\n\n---\n\n".join(parts) if parts else "*No devlog entries.*"
    else:
        devlogs = "*No devlog directory.*"

    history = load_md(project_root / "STATUS_HISTORY.md")

    return status, devlogs, history


# --- Tab: Debug ---


def render_debug(project_root: Path) -> tuple:
    # Consultations
    consult_entries = load_jsonl(_control_plane_log_path(project_root, "consult_log.jsonl"))
    if consult_entries:
        lines = ["| Time | Role | Backend | Prompt Size | Summary |", "|---|---|---|---|---|"]
        for e in consult_entries[-20:]:
            lines.append(
                f"| {e.get('timestamp', '?')[:19]} | {e.get('role', '?')} | "
                f"{e.get('backend', '?')} | {e.get('prompt_chars', '?')} | "
                f"{e.get('problem_summary', '?')[:60]}... |"
            )
        consult_md = "\n".join(lines)
    else:
        consult_md = "*No consultations recorded.*"

    # Violations
    violations = load_jsonl(_control_plane_log_path(project_root, "codewarden_violations.jsonl"))
    if violations:
        lines = ["| Time | File | Rule | Severity |", "|---|---|---|---|"]
        for v in violations[-20:]:
            lines.append(
                f"| {v.get('timestamp', '?')[:19]} | {v.get('file', '?')} | "
                f"{v.get('rule', '?')[:40]} | {v.get('severity', '?')} |"
            )
        violations_md = "\n".join(lines)
    else:
        violations_md = "*No violations recorded.*"

    # Screenshots
    ss_dir = project_root / "screenshots"
    screenshots = []
    if ss_dir.exists():
        for f in sorted(ss_dir.glob("*.png"))[-10:]:
            screenshots.append(str(f))

    return consult_md, violations_md, screenshots


# --- Tab: Metrics ---


def render_metrics(project_root: Path) -> str:
    ops = load_jsonl(_control_plane_log_path(project_root, "ops_log.jsonl"))
    if not ops:
        return "*No operations log found.*"

    parts = []

    # Total operations
    parts.append(f"**Total operations**: {len(ops)}\n")

    # Operations by tool
    tool_counts = Counter(e.get("tool_name", "?") for e in ops)
    parts.append("### Operations by Tool\n")
    parts.append("| Tool | Count |")
    parts.append("|---|---|")
    for tool, count in tool_counts.most_common():
        parts.append(f"| {tool} | {count} |")

    # Most edited files
    file_counts = Counter()
    for e in ops:
        fp = e.get("file", e.get("action", ""))
        if fp and fp != "?" and not fp.startswith("cd "):
            file_counts[fp] += 1

    if file_counts:
        parts.append("\n### Most Edited Files\n")
        parts.append("| File | Edits |")
        parts.append("|---|---|")
        for fp, count in file_counts.most_common(15):
            stuck = " **STUCK?**" if count >= 5 else ""
            parts.append(f"| {fp} | {count}{stuck} |")

    # Stuck loop detection
    sequences = []
    prev_file = None
    streak = 0
    for e in ops:
        fp = e.get("file", "")
        if fp == prev_file and fp:
            streak += 1
        else:
            if streak >= 5:
                sequences.append((prev_file, streak))
            streak = 1
            prev_file = fp
    if streak >= 5:
        sequences.append((prev_file, streak))

    if sequences:
        parts.append("\n### Potential Stuck Loops\n")
        parts.append("| File | Consecutive Edits |")
        parts.append("|---|---|")
        for fp, count in sequences:
            parts.append(f"| {fp} | {count} |")

    # Consultations summary
    consult = load_jsonl(_control_plane_log_path(project_root, "consult_log.jsonl"))
    if consult:
        role_counts = Counter(e.get("role", "?") for e in consult)
        parts.append(f"\n### Consultations: {len(consult)} total\n")
        parts.append("| Role | Count |")
        parts.append("|---|---|")
        for role, count in role_counts.most_common():
            parts.append(f"| {role} | {count} |")

    return "\n".join(parts)


# --- Dashboard Builder ---


def build_dashboard(project_root: Path, bridge_dir: Path) -> gr.Blocks:
    with gr.Blocks(title="ControlCoding Dashboard") as demo:
        gr.Markdown("# ControlCoding Dashboard")
        gr.Markdown(f"Project: `{project_root}`")

        with gr.Tabs():
            # Tab 1: Agents
            with gr.TabItem("Agents"):
                agents_output = gr.Markdown(render_agents(bridge_dir))
                gr.Button("Refresh").click(
                    fn=lambda: render_agents(bridge_dir),
                    outputs=agents_output,
                )

            # Tab 2: Bridge
            with gr.TabItem("Bridge"):
                filter_input = gr.Textbox(
                    label="Filter by agent (empty = all)",
                    placeholder="coder",
                )
                bridge_output = gr.Markdown(render_bridge(bridge_dir))
                gr.Button("Refresh").click(
                    fn=lambda f: render_bridge(bridge_dir, f),
                    inputs=filter_input,
                    outputs=bridge_output,
                )

            # Tab 3: Session
            with gr.TabItem("Session"):
                status_md, devlogs_md, history_md = render_session(project_root)
                with gr.Accordion("STATUS.md", open=True):
                    status_output = gr.Markdown(status_md)
                with gr.Accordion("Recent Devlogs", open=False):
                    devlog_output = gr.Markdown(devlogs_md)
                with gr.Accordion("Status History", open=False):
                    history_output = gr.Markdown(history_md)
                gr.Button("Refresh").click(
                    fn=lambda: render_session(project_root),
                    outputs=[status_output, devlog_output, history_output],
                )

            # Tab 4: Debug
            with gr.TabItem("Debug"):
                consult_md, violations_md, screenshots = render_debug(project_root)
                with gr.Accordion("Consultations", open=True):
                    consult_output = gr.Markdown(consult_md)
                with gr.Accordion("Violations", open=False):
                    violations_output = gr.Markdown(violations_md)
                with gr.Accordion("Screenshots", open=False):
                    if screenshots:
                        gr.Gallery(
                            value=screenshots,
                            label="Visual Check Screenshots",
                            columns=3,
                        )
                    else:
                        gr.Markdown("*No screenshots found.*")
                gr.Button("Refresh").click(
                    fn=lambda: render_debug(project_root)[:2],
                    outputs=[consult_output, violations_output],
                )

            # Tab 5: Metrics
            with gr.TabItem("Metrics"):
                metrics_output = gr.Markdown(render_metrics(project_root))
                gr.Button("Refresh").click(
                    fn=lambda: render_metrics(project_root),
                    outputs=metrics_output,
                )

    return demo


def main():
    parser = argparse.ArgumentParser(
        description="ControlCoding Dashboard - read-only web UI"
    )
    parser.add_argument(
        "--project-root", default=".",
        help="Path to the project root (default: current directory)",
    )
    parser.add_argument(
        "--bridge-dir", default=".bridge",
        help="Path to bridge directory (default: .bridge in project root)",
    )
    parser.add_argument(
        "--port", type=int, default=7860,
        help="Port to serve on (default: 7860)",
    )
    parser.add_argument(
        "--share", action="store_true",
        help="Create a public Gradio share link",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    bridge_dir = Path(args.bridge_dir)
    if not bridge_dir.is_absolute():
        bridge_dir = project_root / bridge_dir

    print(f"[Dashboard] Project root: {project_root}")
    print(f"[Dashboard] Bridge dir: {bridge_dir}")
    print(f"[Dashboard] Starting on port {args.port}...")

    demo = build_dashboard(project_root, bridge_dir)
    demo.launch(
        server_port=args.port,
        share=args.share,
        show_error=True,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
