#!/usr/bin/env python3
"""visual_test.py - Interactive visual testing with input control (L2+).
Builds, launches, interacts via simulated keyboard/mouse, captures screenshots.
Action vocabulary aligned with Claude Computer Use Tool.
Design: separate from visual_check.py (zero-dep). Shared code in utils.
No web support - AI uses Playwright directly. Inline --actions + --actions-file.
"""

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

from visual_check_utils import (
    take_screenshot, take_screenshot_window, compare_screenshots,
    run_output_test, run_http_test, run_file_test,
    write_json_report, kill_process, run_build,
    launch_process, focus_window, check_platform_warnings,
)

try:
    import pyautogui
    pyautogui.PAUSE = 0.1
    pyautogui.FAILSAFE = True
    _HAS_PYAUTOGUI = True
except ImportError:
    pyautogui = None
    _HAS_PYAUTOGUI = False

DEFAULT_OUTPUT_DIR = "screenshots"
DEFAULT_TIMEOUT = 300
ACTION_PAUSE = 0.3
KNOWN_ACTIONS = {
    "wait", "screenshot", "key", "keys", "type",
    "left_click", "left_click_relative", "right_click", "middle_click",
    "mouse_move", "mouse_move_relative", "left_click_drag",
    "scroll", "focus", "compare", "output_test", "http_test", "file_test",
}

def _r(action, param, msg, ok=True, **kw):
    d = {"action": action, "param": param, "result": msg, "success": ok}
    d.update(kw)
    return d


def parse_actions(actions_str: str) -> list[dict]:
    """Parse comma-separated action string: 'wait:2,screenshot:front.png'."""
    result = []
    for part in (p.strip() for p in actions_str.split(",")):
        if not part:
            continue
        if ":" in part:
            a, p = part.split(":", 1)
            result.append({"action": a.strip(), "param": p.strip()})
        else:
            result.append({"action": part, "param": ""})
    return result


def parse_actions_file(file_path: str) -> list[dict]:
    """Parse JSON actions file with optional expect field.
    Supports both direct action format and Verification Strategy format
    (entries with a 'method' field are auto-converted)."""
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("actions file must contain a JSON array")
    result = []
    for item in data:
        if "method" in item:
            entry = _convert_verification_entry(item)
        else:
            entry = {"action": item.get("action", ""), "param": str(item.get("param", ""))}
        if "expect" in item:
            entry["expect"] = item["expect"]
        result.append(entry)
    return result


def _convert_verification_entry(item: dict) -> dict:
    """Convert a Verification Strategy entry to action format.
    Supports methods: visual, command, file, test, http."""
    method = item.get("method", "")
    if method == "visual":
        return {"action": item.get("action", "screenshot"),
                "param": str(item.get("param", "screenshot.png"))}
    if method == "command":
        cmd = item.get("cmd", "")
        expected = item.get("expected", "")
        param = f"{cmd}|{expected}" if expected else cmd
        return {"action": "output_test", "param": param}
    if method == "http":
        url = item.get("url", "")
        status = item.get("expected_status", 200)
        contains = item.get("expected_contains", "")
        parts = [url, str(status)]
        if contains:
            parts.append(contains)
        return {"action": "http_test", "param": "|".join(parts)}
    if method == "file":
        fpath = item.get("path", "")
        contains = item.get("expected_contains", "")
        param = f"{fpath}|{contains}" if contains else fpath
        return {"action": "file_test", "param": param}
    if method == "test":
        return {"action": "output_test", "param": item.get("cmd", "")}
    return {"action": item.get("action", method), "param": str(item.get("param", ""))}


def _click(param, button="left"):
    """Execute click with optional coords and button."""
    if param:
        x, y = int(param.split(",")[0]), int(param.split(",")[1])
        pyautogui.click(x, y, button=button)
        return f"{button}-clicked at ({x},{y})"
    pyautogui.click(button=button)
    return f"{button}-clicked at current position"


def execute_action(action: str, param: str, output_dir: Path,
                    *, pid: int | None = None) -> dict:
    """Execute a single action. Returns result dict.
    pid: process PID for window-only screenshot capture."""
    if action not in KNOWN_ACTIONS:
        warnings.warn(f"Unknown action: {action}")
        return _r(action, param, "skipped (unknown)", ok=False)
    if action == "wait":
        secs = float(param) if param else 1.0
        time.sleep(secs)
        return _r(action, param, f"waited {secs}s")
    if action == "screenshot":
        filename = param if param else "screenshot.png"
        out_path = str((output_dir / filename).resolve())
        if pid is not None:
            err, ok = take_screenshot_window(pid, out_path)
        else:
            err, ok = take_screenshot(out_path)
        p = Path(out_path)
        if ok and p.exists():
            size = p.stat().st_size
            return _r(action, param, f"saved ({size} bytes)",
                      _path=str(output_dir / filename), _size=size)
        return _r(action, param, f"failed: {err}", ok=False)
    if action == "key":
        pyautogui.press(param)
        return _r(action, param, f"pressed key: {param}")
    if action == "keys":
        pyautogui.hotkey(*param.split("+"))
        return _r(action, param, f"pressed keys: {param}")
    if action == "type":
        pyautogui.write(param, interval=0.02)
        return _r(action, param, f"typed text ({len(param)} chars)")
    if action in ("left_click", "right_click", "middle_click"):
        btn = action.replace("_click", "")
        if btn == "left":
            btn = "left"
        return _r(action, param, _click(param, btn))
    if action == "left_click_relative":
        dx, dy = map(int, param.split(","))
        sw, sh = pyautogui.size()
        x, y = sw // 2 + dx, sh // 2 + dy
        pyautogui.click(x, y)
        return _r(action, param, f"clicked at ({x},{y})")
    if action == "mouse_move":
        x, y = map(int, param.split(","))
        pyautogui.moveTo(x, y)
        return _r(action, param, f"moved to ({x},{y})")
    if action == "mouse_move_relative":
        dx, dy = map(int, param.split(","))
        pyautogui.moveRel(dx, dy)
        return _r(action, param, f"moved by ({dx},{dy})")
    if action == "left_click_drag":
        x, y = map(int, param.split(","))
        pyautogui.moveTo(x, y, duration=0.5)
        return _r(action, param, f"dragged to ({x},{y})")
    if action == "scroll":
        pyautogui.scroll(int(param))
        return _r(action, param, f"scrolled {param}")
    if action == "compare":
        parts = param.split(",")
        if len(parts) != 2:
            return _r(action, param, "expected param: before.png,after.png", ok=False)
        p1 = str((output_dir / parts[0].strip()).resolve())
        p2 = str((output_dir / parts[1].strip()).resolve())
        if not Path(p1).exists() or not Path(p2).exists():
            return _r(action, param, "one or both files not found", ok=False)
        changed, ratio = compare_screenshots(p1, p2)
        if changed:
            return _r(action, param, f"images differ (ratio={ratio:.4f})")
        return _r(action, param,
                  f"images identical (ratio={ratio:.4f}) - action had no effect",
                  ok=False)
    if action == "output_test":
        if "|" in param:
            cmd, expected = param.split("|", 1)
        else:
            cmd, expected = param, None
        ok, output = run_output_test(
            cmd.strip(), expected.strip() if expected else None)
        return _r(action, param, f"{'pass' if ok else 'fail'}: {output[:200]}", ok=ok)
    if action == "http_test":
        parts = param.split("|")
        url = parts[0].strip()
        status = int(parts[1].strip()) if len(parts) > 1 else 200
        contains = parts[2].strip() if len(parts) > 2 else None
        ok, body = run_http_test(url, status, contains)
        return _r(action, param, f"{'pass' if ok else 'fail'}: {body[:200]}", ok=ok)
    if action == "file_test":
        if "|" in param:
            fpath, expected = param.split("|", 1)
        else:
            fpath, expected = param, None
        ok, detail = run_file_test(
            fpath.strip(), True, expected.strip() if expected else None)
        return _r(action, param, f"{'pass' if ok else 'fail'}: {detail}", ok=ok)
    if action == "focus":
        ok = focus_window(param)
        return _r(action, param, f"focus {'found' if ok else 'not found'}", ok=ok)
    return _r(action, param, "unhandled", ok=False)


def _print_summary(ss_info: list[dict]) -> None:
    print("[L2+] === VISUAL TEST COMPLETE ===")
    if ss_info:
        print("[L2+] Screenshots captured:")
        for i, s in enumerate(ss_info, 1):
            line = f"[L2+]   {i}. {s['path']} ({s['size']}B) at {s.get('offset',0):.1f}s"
            if s.get("expect"):
                line += f"\n[L2+]      EXPECT: {s['expect']}"
            print(line)
    else:
        print("[L2+] No screenshots captured.")
    print("[L2+]\n[L2+] ANALYSIS INSTRUCTIONS:\n[L2+] Read each screenshot and "
          "describe what you see.\n[L2+] For sequential screenshots, identify "
          "what CHANGED between them.")


def _build_report(args, build_ok, build_ms, results, ss_info, errors,
                  alive=False, code=None) -> dict:
    from datetime import datetime
    rpt = {"timestamp": datetime.now().isoformat(timespec="seconds"),
           "platform": sys.platform, "exe": args.exe}
    if args.build_cmd:
        rpt.update(build_cmd=args.build_cmd, build_success=build_ok)
    if build_ms is not None:
        rpt["build_duration_ms"] = build_ms
    rpt.update(process_alive_at_end=alive, process_exit_code=code,
               actions_executed=results, total_actions=len(results),
               screenshots_captured=len(ss_info),
               actions_file=args.actions_file, errors=errors)
    return rpt


def main():
    pa = argparse.ArgumentParser(description="Interactive visual testing (L2+).")
    aa = pa.add_argument
    aa("--exe", required=True); aa("--build-cmd", default=None)
    aa("--actions", default=None); aa("--actions-file", default=None)
    aa("--output-dir", default=DEFAULT_OUTPUT_DIR)
    aa("--delay", type=float, default=3.0); aa("--kill-existing", default=None)
    aa("--window-title", default=None); aa("--json", action="store_true")
    aa("--auto-screenshot-after-action", action="store_true")
    aa("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = pa.parse_args()

    if not _HAS_PYAUTOGUI:
        print("[L2+] ERROR: pyautogui not installed. Run: pip install pyautogui")
        sys.exit(1)
    check_platform_warnings()

    if args.actions and args.actions_file:
        pa.error("use --actions or --actions-file, not both")
    if args.actions: actions = parse_actions(args.actions)
    elif args.actions_file:
        try: actions = parse_actions_file(args.actions_file)
        except (json.JSONDecodeError, ValueError, OSError) as e:
            pa.error(f"--actions-file: {e}")
    else: pa.error("provide --actions or --actions-file")
    if not actions: pa.error("action list is empty")

    errors, json_out = [], str(Path(args.output_dir) / "visual_test.json")
    if args.kill_existing:
        import subprocess as _sp
        cmd = (["taskkill", "/F", "/IM", args.kill_existing] if sys.platform == "win32"
               else ["pkill", "-f", args.kill_existing])
        _sp.run(cmd, capture_output=True); time.sleep(0.5)
    build_ok, build_ms = None, None
    if args.build_cmd:
        print(f"[L2+] Building: {args.build_cmd}")
        build_ok, dur = run_build(args.build_cmd)
        build_ms = int(dur)
        if not build_ok:
            print("[L2+] BUILD FAILED")
            if args.json:
                rpt = _build_report(args, False, build_ms, [], [], ["build failed"])
                print(f"[L2+] JSON report: {write_json_report(rpt, json_out)}")
            sys.exit(1)
        print("[L2+] Build succeeded")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exe_path = Path(args.exe)
    if not exe_path.exists():
        print(f"[L2+] ERROR: executable not found: {exe_path}")
        sys.exit(1)

    print(f"[L2+] Launching: {exe_path}")
    proc = launch_process(str(exe_path))
    t0 = time.monotonic()
    print(f"[L2+] Waiting {args.delay}s for initial render...")
    time.sleep(args.delay)
    if proc.poll() is not None:
        print(f"[L2+] ERROR: process exited early with code {proc.returncode}")
        sys.exit(1)

    title = args.window_title or exe_path.stem
    print(f"[L2+] Focusing window: {title}")
    focus_window(title)
    time.sleep(0.3)

    print(f"[L2+] Executing {len(actions)} actions...")
    results, ss_info, auto_idx = [], [], 0
    timeout_limit = args.timeout if args.timeout > 0 else float("inf")
    timed_out = False

    for i, act in enumerate(actions):
        if time.monotonic() - t0 > timeout_limit:
            timed_out = True
            take_screenshot(str((output_dir / "timeout_final.png").resolve()))
            for j in range(i, len(actions)):
                results.append({"action": actions[j]["action"],
                    "param": actions[j]["param"], "result": "skipped: timeout",
                    "success": False})
            errors.append(f"timeout at {time.monotonic() - t0:.1f}s")
            break
        action, param = act["action"], act["param"]
        result = execute_action(action, param, output_dir, pid=proc.pid)
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        if act.get("expect"):
            clean["expect"] = act["expect"]
        results.append(clean)
        print(f"[L2+]   {i+1}. {action}({param}) -> {result['result']}")
        if result.get("_path"):
            ss = {"path": result["_path"], "size": result["_size"],
                  "offset": time.monotonic() - t0}
            if act.get("expect"):
                ss["expect"] = act["expect"]
            ss_info.append(ss)
        if args.auto_screenshot_after_action and action != "screenshot":
            auto_idx += 1
            af = f"auto_{auto_idx:03d}_{action[:20]}.png"
            auto_path = str((output_dir / af).resolve())
            err, ok = take_screenshot_window(proc.pid, auto_path)
            if ok:
                p = Path(output_dir / af)
                sz = p.stat().st_size if p.exists() else 0
                results.append({"action": "auto_screenshot", "param": af,
                                "result": f"auto ({sz} bytes)", "success": True})
                ss_info.append({"path": str(p), "size": sz,
                                "offset": time.monotonic() - t0})
        time.sleep(ACTION_PAUSE)

    alive = proc.poll() is None
    exit_code = None if alive else proc.returncode
    print("[L2+] Terminating application...")
    kill_process(proc)
    if timed_out:
        print(f"[L2+] WARNING: timeout reached ({args.timeout}s)")
    _print_summary(ss_info)
    if args.json:
        rpt = _build_report(args, build_ok, build_ms, results, ss_info, errors,
                            alive, exit_code)
        print(f"[L2+] JSON report: {write_json_report(rpt, json_out)}")
    print("[L2+] Done")
    sys.exit(0)


if __name__ == "__main__":
    main()
