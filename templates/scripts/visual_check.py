#!/usr/bin/env python3
"""visual_check.py - Visual feedback loop for ControlCoding debug (L2).

Builds the project, launches the executable, waits for rendering,
takes a screenshot, and terminates the process. The AI can then
read the screenshot file for multimodal visual analysis.

This script gives an AI the ability to SEE what it renders,
enabling iterative visual tuning without human feedback.

Usage:
    python visual_check.py --exe build/Release/app.exe
    python visual_check.py --exe build/Release/app.exe --build-cmd "cmake --build build --config Release"
    python visual_check.py --exe build/Release/app.exe --delay 5 --output screenshots/check_02.png

Requirements:
    - Windows: PowerShell (default on Windows 10+)
    - Linux: scrot (apt install scrot)
    - macOS: screencapture (built-in)
    - Python 3.8+

No additional Python packages required.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from visual_check_utils import (
    parse_multi_delays,
    generate_multi_paths,
    build_json_report,
    write_json_report,
    take_screenshot,
    take_screenshot_windows,
    take_screenshot_linux,
    take_screenshot_macos,
    kill_process,
)

# Default screenshot output location (relative to project root)
DEFAULT_OUTPUT = "screenshots/visual_check.png"
DEFAULT_DELAY = 3.0


def main():
    parser = argparse.ArgumentParser(
        description="Visual feedback loop: build, launch, screenshot, kill."
    )
    parser.add_argument(
        "--exe", required=True,
        help="Path to the executable to launch",
    )
    parser.add_argument(
        "--build-cmd", default=None,
        help="Optional build command to run before launching (e.g. 'cmake --build build --config Release')",
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Seconds to wait before taking screenshot (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help=f"Screenshot output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--kill-existing", default=None,
        help="Process name to kill before launching (e.g. 'DungeonCrawler3D.exe')",
    )
    parser.add_argument(
        "--multi", default=None,
        help="Comma-separated delays for multi-screenshot (e.g. '1,3,5')",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Write a structured JSON report alongside screenshots",
    )
    args = parser.parse_args()

    # Resolve multi-screenshot delays
    delays = None
    if args.multi:
        try:
            delays = parse_multi_delays(args.multi)
        except ValueError as e:
            parser.error(f"--multi: {e}")
        if args.delay != DEFAULT_DELAY:
            print("[L2] WARNING: --multi overrides --delay")
    if delays and len(delays) == 1:
        args.delay = delays[0]
        delays = None  # single value: behave like --delay

    # Kill existing process if requested
    if args.kill_existing:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/IM", args.kill_existing],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["pkill", "-f", args.kill_existing],
                capture_output=True,
            )
        time.sleep(0.5)

    # Build if requested (stderr merged to stdout so AI sees errors)
    build_success = None
    build_duration_ms = None
    if args.build_cmd:
        print(f"[L2] Building: {args.build_cmd}")
        t0 = time.monotonic()
        result = subprocess.run(args.build_cmd, shell=True, stderr=subprocess.STDOUT)
        build_duration_ms = int((time.monotonic() - t0) * 1000)
        build_success = result.returncode == 0
        if not build_success:
            print("[L2] BUILD FAILED - cannot proceed with visual check")
            if args.json:
                report = build_json_report(
                    exe=args.exe, build_cmd=args.build_cmd,
                    build_success=False, build_duration_ms=build_duration_ms,
                    screenshots=[], process_alive_at_end=False,
                    process_exit_code=None, errors=["build failed"])
                jp = write_json_report(report, args.output)
                print(f"[L2] JSON report: {jp}")
            sys.exit(1)
        print("[L2] Build succeeded")

    # Ensure output directory exists
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Launch executable
    exe_path = Path(args.exe)
    if not exe_path.exists():
        print(f"[L2] ERROR: executable not found: {exe_path}")
        sys.exit(1)

    print(f"[L2] Launching: {exe_path}")
    popen_kwargs = dict(cwd=str(exe_path.parent))
    if sys.platform != "win32":
        popen_kwargs["preexec_fn"] = os.setpgrp
    proc = subprocess.Popen(str(exe_path), **popen_kwargs)

    # Determine capture schedule
    errors = []
    screenshot_results = []
    tool_name = {"win32": "PowerShell", "darwin": "screencapture"}.get(sys.platform, "scrot")

    if delays:
        # Multi-screenshot mode
        output_paths = generate_multi_paths(args.output, delays)
        for out_dir in {str(Path(p).parent) for p in output_paths}:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        print(f"[L2] Multi-screenshot: delays={delays}")
        elapsed = 0.0
        for i, (d, out_p) in enumerate(zip(delays, output_paths)):
            wait = d - elapsed
            if wait > 0:
                time.sleep(wait)
            elapsed = d
            dead = proc.poll() is not None
            if dead and not any("process died" in e for e in errors):
                errors.append(f"process died at {elapsed:.1f}s")
            print(f"[L2] Screenshot {i+1}/{len(delays)} at {d}s -> {out_p}")
            out_resolved = str(Path(out_p).resolve())
            err, ok = take_screenshot(out_resolved)
            p_obj = Path(out_p)
            size = p_obj.stat().st_size if ok and p_obj.exists() else 0
            screenshot_results.append({
                "path": out_p, "delay_seconds": d,
                "size_bytes": size, "captured": ok,
            })
            if not ok:
                errors.append(f"screenshot at {d}s failed: {err}")
            elif dead:
                errors.append(f"screenshot at {d}s captured dead process")

        # Summary
        any_ok = any(s["captured"] for s in screenshot_results)
        print("[L2] Multi-screenshot summary:")
        for s in screenshot_results:
            status = f"{s['size_bytes']} bytes" if s["captured"] else "FAILED"
            print(f"  {s['path']}: {status}")
    else:
        # Single screenshot mode
        print(f"[L2] Waiting {args.delay}s for rendering...")
        time.sleep(args.delay)
        if proc.poll() is not None:
            print(f"[L2] ERROR: process exited early with code {proc.returncode}")
            if args.json:
                report = build_json_report(
                    exe=args.exe, build_cmd=args.build_cmd,
                    build_success=build_success, build_duration_ms=build_duration_ms,
                    screenshots=[], process_alive_at_end=False,
                    process_exit_code=proc.returncode, errors=["process exited early"])
                jp = write_json_report(report, args.output)
                print(f"[L2] JSON report: {jp}")
            sys.exit(1)
        print(f"[L2] Taking screenshot -> {output} (platform={sys.platform}, tool={tool_name})")
        err, ok = take_screenshot(str(output.resolve()))
        size = output.stat().st_size if ok and output.exists() else 0
        screenshot_results.append({
            "path": args.output, "delay_seconds": args.delay,
            "size_bytes": size, "captured": ok,
        })
        any_ok = ok
        if ok and output.exists():
            print(f"[L2] Screenshot saved: {output} ({size} bytes)")
            print(f"[L2] AI can now read this file for visual analysis")
        else:
            print(f"[L2] ERROR: screenshot failed (platform={sys.platform}, tool={tool_name})")
            if err:
                print(f"[L2] Detail: {err}")
                errors.append(f"screenshot failed: {err}")

    # Capture process state before termination
    process_alive = proc.poll() is None
    exit_code = None if process_alive else proc.returncode

    # Terminate the process (full tree cleanup)
    print("[L2] Terminating application...")
    kill_process(proc)

    # Write JSON report if requested
    if args.json:
        report = build_json_report(
            exe=args.exe, build_cmd=args.build_cmd,
            build_success=build_success, build_duration_ms=build_duration_ms,
            screenshots=screenshot_results, process_alive_at_end=process_alive,
            process_exit_code=exit_code, errors=errors)
        jp = write_json_report(report, args.output)
        print(f"[L2] JSON report: {jp}")

    print("[L2] Done")
    sys.exit(0 if any_ok else 1)


if __name__ == "__main__":
    main()
