"""Shared utilities for visual_check.py and visual_test.py - screenshot
capture, process lifecycle, build, JSON reports, path generation."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Platform screenshot capture
# ---------------------------------------------------------------------------

def take_screenshot_windows(output_path: str) -> tuple[str, bool]:
    """Capture the primary screen on Windows via PowerShell."""
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bounds = $screen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save('{output_path.replace(chr(92), "/")}')
$graphics.Dispose()
$bitmap.Dispose()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        return ("", True)
    err = result.stderr.strip()
    if not err:
        err = "PowerShell screenshot failed. Check execution policy (Set-ExecutionPolicy)."
    return (err, False)


def take_screenshot_linux(output_path: str) -> tuple[str, bool]:
    """Capture screen on Linux using scrot."""
    result = subprocess.run(
        ["scrot", output_path],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        return ("", True)
    err = result.stderr.strip()
    if not err:
        err = "scrot failed. Install scrot: sudo apt install scrot"
    return (err, False)


def take_screenshot_macos(output_path: str) -> tuple[str, bool]:
    """Capture screen on macOS using screencapture."""
    result = subprocess.run(
        ["screencapture", "-x", output_path],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        return ("", True)
    err = result.stderr.strip()
    if not err:
        err = "screencapture failed"
    return (err, False)


def take_screenshot(output_path: str) -> tuple[str, bool]:
    """Platform-appropriate screenshot capture."""
    if sys.platform == "win32":
        return take_screenshot_windows(output_path)
    elif sys.platform == "darwin":
        return take_screenshot_macos(output_path)
    else:
        return take_screenshot_linux(output_path)


def parse_multi_delays(value: str) -> list[float]:
    """Parse comma-separated delay values into sorted list of positive floats.

    Raises ValueError on invalid input (negative, non-numeric).
    """
    parts = [s.strip() for s in value.split(",")]
    delays = []
    for part in parts:
        try:
            f = float(part)
        except ValueError:
            raise ValueError(f"invalid delay value: {part!r}")
        if f < 0:
            raise ValueError(f"delay must be non-negative: {f}")
        delays.append(f)
    delays.sort()
    return delays


def generate_multi_paths(base_output: str, delays: list[float]) -> list[str]:
    """Generate output paths with delay suffix for multi-screenshot mode.

    base_output="screenshots/check.png", delays=[1,3,5]
    -> ["screenshots/check_001s.png", "screenshots/check_003s.png", "screenshots/check_005s.png"]
    """
    p = Path(base_output)
    stem = p.stem
    ext = p.suffix
    parent = p.parent
    paths = []
    for d in delays:
        # Format: zero-padded to 3 chars (e.g., 001, 003, 010)
        int_part = int(d) if d == int(d) else d
        if isinstance(int_part, int):
            suffix = f"_{int_part:03d}s"
        else:
            suffix = f"_{d:06.2f}s"
        paths.append(str(parent / f"{stem}{suffix}{ext}"))
    return paths


def build_json_report(
    *,
    exe: str,
    build_cmd: str | None,
    build_success: bool | None,
    build_duration_ms: int | None,
    screenshots: list[dict],
    process_alive_at_end: bool,
    process_exit_code: int | None,
    errors: list[str],
) -> dict:
    """Build the structured JSON report dict."""
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "platform": sys.platform,
        "exe": exe,
    }
    if build_cmd is not None:
        report["build_cmd"] = build_cmd
        report["build_success"] = build_success
    if build_duration_ms is not None:
        report["build_duration_ms"] = build_duration_ms
    report["screenshots"] = screenshots
    report["process_alive_at_end"] = process_alive_at_end
    report["process_exit_code"] = process_exit_code
    report["errors"] = errors
    return report


def write_json_report(report: dict, base_output: str) -> str:
    """Write report to JSON file next to the base output path. Returns path."""
    p = Path(base_output).with_suffix(".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Process lifecycle (shared by visual_check.py and visual_test.py)
# ---------------------------------------------------------------------------

def run_build(build_cmd: str) -> tuple[bool, float]:
    """Run build command with shell=True. Returns (success, duration_ms)."""
    t0 = time.monotonic()
    result = subprocess.run(build_cmd, shell=True, stderr=subprocess.STDOUT)
    duration_ms = (time.monotonic() - t0) * 1000
    return (result.returncode == 0, duration_ms)


def launch_process(exe_path: str) -> subprocess.Popen:
    """Launch executable with cwd set to its parent. Uses process groups on Unix."""
    p = Path(exe_path)
    kwargs = dict(cwd=str(p.parent))
    if sys.platform != "win32":
        kwargs["preexec_fn"] = os.setpgrp
    return subprocess.Popen(str(p), **kwargs)


def kill_process(proc: subprocess.Popen) -> int | None:
    """Terminate process tree. Returns exit code or None.
    Uses taskkill /T on Windows, process groups on Unix."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        proc.wait()
    return proc.returncode


# ---------------------------------------------------------------------------
# Window and platform helpers (used by visual_test.py)
# ---------------------------------------------------------------------------

def focus_window(title_fragment: str) -> bool:
    """Bring window with matching title to front (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes, ctypes.wintypes
        user32 = ctypes.windll.user32
        found = [None]
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if title_fragment.lower() in buf.value.lower():
                    found[0] = hwnd
                    return False
            return True
        user32.EnumWindows(callback, 0)
        if found[0]:
            user32.SetForegroundWindow(found[0])
            time.sleep(0.3)
            return True
    except Exception:
        pass
    return False


def check_platform_warnings():
    """Detect platform-specific issues and print warnings for visual testing."""
    if sys.platform == "darwin":
        print("[L2+] NOTE: macOS requires Screen Recording permission.")
        print("[L2+] Go to: System Preferences > Privacy & Security > Screen Recording")
        print("[L2+] Also check: Privacy & Security > Accessibility for keyboard/mouse.")
    elif sys.platform == "win32":
        try:
            import ctypes
            dpi = ctypes.windll.shcore.GetScaleFactorForDevice(0)
            if dpi > 100:
                print(f"[L2+] WARNING: Windows DPI scaling detected ({dpi}%).")
                print("[L2+] Mouse coordinates may be offset. Try: right-click python.exe >")
                print('[L2+] Properties > Compatibility > "Override high DPI scaling" > "Application"')
        except Exception:
            pass
    else:
        session = os.environ.get("XDG_SESSION_TYPE", "")
        if session == "wayland":
            print("[L2+] WARNING: Wayland detected. pyautogui requires X11.")
            print("[L2+] Run with: XDG_SESSION_TYPE=x11 python visual_test.py ...")


# ---------------------------------------------------------------------------
# Window-only capture (Windows via ctypes, fallback to full screen)
# ---------------------------------------------------------------------------

def find_window_by_pid(pid: int) -> int | None:
    """Find the main visible window handle for a process PID (Windows only).
    Returns HWND as int, or None if not found or not on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        result = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            proc_id = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    result.append(int(hwnd))
                    return False
            return True

        user32.EnumWindows(callback, 0)
        return result[0] if result else None
    except Exception:
        return None


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Get window bounding rectangle (left, top, right, bottom).
    Returns None on failure or non-Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def _take_screenshot_region_windows(
    x: int, y: int, w: int, h: int, output_path: str
) -> tuple[str, bool]:
    """Capture a specific screen region on Windows via PowerShell."""
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap({w}, {h})
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen({x}, {y}, 0, 0, (New-Object System.Drawing.Size({w}, {h})))
$bitmap.Save('{output_path.replace(chr(92), "/")}')
$graphics.Dispose()
$bitmap.Dispose()
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return ("", True)
        return (result.stderr.strip() or "PowerShell region capture failed", False)
    except Exception as e:
        return (str(e), False)


def take_screenshot_window(pid: int, output_path: str) -> tuple[str, bool]:
    """Capture screenshot of a specific window identified by PID.
    Falls back to full-screen capture if window not found or not on Windows.
    Uses Pillow ImageGrab if available, otherwise PowerShell region capture."""
    hwnd = find_window_by_pid(pid)
    if hwnd is None:
        return take_screenshot(output_path)

    rect = get_window_rect(hwnd)
    if rect is None:
        return take_screenshot(output_path)

    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return take_screenshot(output_path)

    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        img.save(output_path)
        return ("", True)
    except ImportError:
        pass
    except Exception as e:
        return (str(e), False)

    # Fallback: PowerShell region capture
    return _take_screenshot_region_windows(left, top, right - left, bottom - top, output_path)


# ---------------------------------------------------------------------------
# Screenshot comparison
# ---------------------------------------------------------------------------

def compare_screenshots(
    path1: str, path2: str, threshold: float = 0.02
) -> tuple[bool, float]:
    """Compare two screenshots for visual difference.
    Returns (changed, difference_ratio).
    changed is True when images differ more than threshold.
    Uses Pillow if available, falls back to byte comparison."""
    try:
        from PIL import Image
        img1 = Image.open(path1).convert("L").resize((64, 64))
        img2 = Image.open(path2).convert("L").resize((64, 64))
        pixels1 = list(img1.getdata())
        pixels2 = list(img2.getdata())
        diff = sum(abs(p1 - p2) for p1, p2 in zip(pixels1, pixels2))
        max_diff = 255 * len(pixels1)
        ratio = diff / max_diff if max_diff > 0 else 0.0
        return (ratio > threshold, ratio)
    except ImportError:
        b1 = Path(path1).read_bytes()
        b2 = Path(path2).read_bytes()
        changed = b1 != b2
        return (changed, 1.0 if changed else 0.0)
    except Exception:
        return (True, -1.0)


# ---------------------------------------------------------------------------
# Verification strategy: output, HTTP, file tests
# ---------------------------------------------------------------------------

def run_output_test(
    cmd: str, expected: str | None = None, timeout: float = 30
) -> tuple[bool, str]:
    """Run a command and optionally check stdout/stderr for expected string.
    Returns (success, actual_output)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
        if expected is None:
            return (result.returncode == 0, output)
        return (expected in output, output)
    except subprocess.TimeoutExpired:
        return (False, f"timeout after {timeout}s")
    except Exception as e:
        return (False, str(e))


def run_http_test(
    url: str,
    expected_status: int = 200,
    expected_contains: str | None = None,
    timeout: float = 10,
) -> tuple[bool, str]:
    """HTTP GET test. Returns (success, response_body_or_error)."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status_ok = resp.status == expected_status
            content_ok = expected_contains in body if expected_contains else True
            return (status_ok and content_ok, body)
    except urllib.error.HTTPError as e:
        return (e.code == expected_status, f"HTTP {e.code}")
    except Exception as e:
        return (False, str(e))


def run_file_test(
    path: str,
    expected_exists: bool = True,
    expected_contains: str | None = None,
) -> tuple[bool, str]:
    """Check file existence and optional content match.
    Returns (success, detail_message)."""
    p = Path(path)
    exists = p.exists()
    if not expected_exists:
        return (not exists, "file absent" if not exists else "file exists unexpectedly")
    if not exists:
        return (False, "file not found")
    if expected_contains is None:
        return (True, f"file exists ({p.stat().st_size} bytes)")
    try:
        content = p.read_text(encoding="utf-8")
        found = expected_contains in content
        return (found, f"{'found' if found else 'not found'}: {expected_contains!r}")
    except Exception as e:
        return (False, str(e))
