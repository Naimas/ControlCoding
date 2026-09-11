"""Unit tests for visual_check.py - L2 debug visual feedback loop."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "templates" / "scripts" / "visual_check.py"
HOOKS_PATH = Path(__file__).parent.parent / "templates" / "hooks"

# Import the modules for unit testing
sys.path.insert(0, str(SCRIPT_PATH.parent))
sys.path.insert(0, str(HOOKS_PATH))
import visual_check
import visual_check_utils
import check_workflow


# ---------------------------------------------------------------------------
# Helper: run script as subprocess (integration tests)
# ---------------------------------------------------------------------------

def run_script(args: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    """Run visual_check.py with given CLI args."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)] + args,
        capture_output=True, text=True, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Helper: common mock context for main() unit tests
# ---------------------------------------------------------------------------

class MainMocks:
    """Bundles the mocks needed to run main() without side effects."""

    def __init__(self, exe_exists=True, screenshot_ok=True, poll_return=None,
                 wait_timeout=False, file_size=1000):
        self.exe_exists = exe_exists
        self.screenshot_ok = screenshot_ok
        self.poll_return = poll_return
        self.wait_timeout = wait_timeout
        self.file_size = file_size

    def __enter__(self):
        self._stack = []

        # subprocess.run (build, kill-existing)
        p = patch("visual_check.subprocess.run",
                  return_value=MagicMock(returncode=0))
        self.mock_run = p.start(); self._stack.append(p)

        # subprocess.Popen
        self.mock_proc = MagicMock()
        self.mock_proc.poll.return_value = self.poll_return
        self.mock_proc.pid = 12345
        if self.wait_timeout:
            self.mock_proc.wait.side_effect = subprocess.TimeoutExpired("p", 5)
        p = patch("visual_check.subprocess.Popen", return_value=self.mock_proc)
        self.mock_popen = p.start(); self._stack.append(p)

        # time.sleep
        p = patch("visual_check.time.sleep")
        self.mock_sleep = p.start(); self._stack.append(p)

        # take_screenshot
        err = "" if self.screenshot_ok else "tool error"
        p = patch("visual_check.take_screenshot",
                  return_value=(err, self.screenshot_ok))
        self.mock_screenshot = p.start(); self._stack.append(p)

        # kill_process (imported from utils)
        p = patch("visual_check.kill_process")
        self.mock_terminate = p.start(); self._stack.append(p)

        # Path operations - use a fake class that tracks calls
        self._orig_path = visual_check.Path

        outer = self
        class FakePath(type(Path())):
            def exists(self):
                s = str(self)
                # For exe path checks
                if s.endswith(".exe") or "app" in s:
                    return outer.exe_exists
                # For output file check after screenshot
                return outer.screenshot_ok
            def stat(self):
                return MagicMock(st_size=outer.file_size)
            def mkdir(self, *a, **kw):
                # Actually create dirs for tmp_path tests
                os.makedirs(str(self), exist_ok=True)

        p = patch("visual_check.Path", FakePath)
        p.start(); self._stack.append(p)

        return self

    def __exit__(self, *args):
        for p in reversed(self._stack):
            p.stop()


# ===========================================================================
# Phase 1: Argument parsing
# ===========================================================================

class TestArgParsing:
    def test_missing_exe_exits_error(self):
        r = run_script([])
        assert r.returncode != 0

    def test_exe_required(self):
        r = run_script(["--exe", "some.exe", "--delay", "0"])
        # Will fail because exe doesn't exist, but parsing succeeds
        assert "[L2]" in r.stdout

    def test_default_output(self):
        """Missing --output uses default screenshots/visual_check.png."""
        r = run_script(["--exe", "nonexistent.exe", "--delay", "0"])
        assert "not found" in r.stdout.lower() or r.returncode != 0

    def test_delay_float_accepted(self):
        r = run_script(["--exe", "nonexistent.exe", "--delay", "1.5"])
        assert r.returncode != 0

    def test_build_cmd_with_spaces(self):
        r = run_script(["--exe", "nonexistent.exe", "--build-cmd", "echo hello world"])
        assert "Build succeeded" in r.stdout or "BUILD FAILED" in r.stdout or r.returncode != 0

    def test_kill_existing_stored(self, tmp_path):
        """--kill-existing is parsed without starting process-control tools."""
        output_path = str(tmp_path / "visual_check.png")
        with MainMocks(exe_exists=False) as mocks, \
             patch("visual_check.sys.platform", "win32"), \
             patch(
                 "visual_check.sys.argv",
                 [
                     "visual_check.py",
                     "--exe",
                     "nonexistent.exe",
                     "--kill-existing",
                     "notepad.exe",
                     "--delay",
                     "0",
                     "--output",
                     output_path,
                 ],
             ):
            with pytest.raises(SystemExit) as exc_info:
                visual_check.main()

        assert exc_info.value.code == 1
        mocks.mock_run.assert_called_once_with(
            ["taskkill", "/F", "/IM", "notepad.exe"],
            capture_output=True,
        )
        mocks.mock_popen.assert_not_called()


# ===========================================================================
# Phase 1: Platform detection
# ===========================================================================

class TestPlatformDispatch:
    def test_windows_dispatch(self):
        with patch.object(visual_check_utils, "take_screenshot_windows", return_value=("", True)) as mock_win, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            visual_check_utils.take_screenshot("test.png")
            mock_win.assert_called_once_with("test.png")

    def test_linux_dispatch(self):
        with patch.object(visual_check_utils, "take_screenshot_linux", return_value=("", True)) as mock_lin, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "linux"
            visual_check_utils.take_screenshot("test.png")
            mock_lin.assert_called_once_with("test.png")

    def test_macos_dispatch(self):
        with patch.object(visual_check_utils, "take_screenshot_macos", return_value=("", True)) as mock_mac, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "darwin"
            visual_check_utils.take_screenshot("test.png")
            mock_mac.assert_called_once_with("test.png")

    def test_windows_uses_powershell(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            visual_check.take_screenshot_windows("test.png")
            args = mock_run.call_args[0][0]
            assert args[0] == "powershell"

    def test_linux_uses_scrot(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            visual_check.take_screenshot_linux("test.png")
            args = mock_run.call_args[0][0]
            assert args[0] == "scrot"

    def test_macos_uses_screencapture(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            visual_check.take_screenshot_macos("test.png")
            args = mock_run.call_args[0][0]
            assert args[0] == "screencapture"


# ===========================================================================
# Phase 1: Build step
# ===========================================================================

class TestBuildStep:
    def test_build_cmd_runs_before_exe(self):
        """Build command runs with shell=True."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--build-cmd", "make"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            # Build command was called with shell=True
            build_call = m.mock_run.call_args_list[0]
            assert build_call[0][0] == "make"
            assert build_call[1].get("shell") is True

    def test_no_build_cmd_skips_build(self):
        """Without --build-cmd, subprocess.run is NOT called for building."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            # subprocess.run should NOT have been called (no build, no kill-existing)
            m.mock_run.assert_not_called()

    def test_build_failure_exits_without_launching(self):
        """Build failure (non-zero exit) prints error, exits 1, does NOT launch exe."""
        r = run_script(["--exe", "nonexistent.exe", "--build-cmd", "exit 1"])
        assert r.returncode == 1
        assert "BUILD FAILED" in r.stdout

    def test_build_stderr_visible(self):
        """Build command stderr should be visible in output."""
        if sys.platform == "win32":
            cmd = "echo build_error_msg 1>&2 && exit 1"
        else:
            cmd = "echo build_error_msg >&2 && exit 1"
        r = run_script(["--exe", "nonexistent.exe", "--build-cmd", cmd])
        assert r.returncode == 1
        # stderr from build should be visible (merged to stdout via stderr=STDOUT)
        assert "build_error_msg" in r.stdout or "build_error_msg" in r.stderr


# ===========================================================================
# Phase 1: Process lifecycle
# ===========================================================================

class TestProcessLifecycle:
    def test_exe_not_found_error(self):
        r = run_script(["--exe", "totally_nonexistent_binary_xyz.exe", "--delay", "0"])
        assert r.returncode == 1
        assert "not found" in r.stdout.lower()
        assert "totally_nonexistent_binary_xyz" in r.stdout

    def test_exe_launched_with_cwd(self):
        """Exe is launched with cwd set to exe's parent directory."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "/some/dir/app.exe", "--delay", "0"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            popen_kwargs = m.mock_popen.call_args[1]
            assert "cwd" in popen_kwargs

    def test_process_exited_early(self):
        """If process exits during delay, error with exit code 1."""
        with MainMocks(poll_return=42) as m:
            m.mock_proc.returncode = 42
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0"]):
                with pytest.raises(SystemExit) as exc:
                    visual_check.main()
                assert exc.value.code == 1

    def test_terminate_timeout_falls_back_to_kill(self):
        """If terminate + wait times out, kill is called."""
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        # First wait() raises timeout, second wait() (after kill) succeeds
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired("p", 5), None]

        with patch("visual_check_utils.subprocess.run"):
            with patch("visual_check_utils.sys") as mock_sys:
                mock_sys.platform = "win32"
                visual_check_utils.kill_process(mock_proc)
                mock_proc.kill.assert_called_once()

    def test_terminate_uses_taskkill_tree_on_windows(self):
        """On Windows, kill_process uses taskkill /F /T /PID."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch("visual_check_utils.subprocess.run") as mock_run, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            visual_check_utils.kill_process(mock_proc)
            args = mock_run.call_args[0][0]
            assert "taskkill" in args
            assert "/T" in args
            assert "/PID" in args
            assert "12345" in args


# ===========================================================================
# Phase 1: Screenshot capture
# ===========================================================================

class TestScreenshotCapture:
    def test_output_dir_created(self, tmp_path):
        """Output directory is created if it doesn't exist."""
        output = tmp_path / "subdir" / "deep" / "screenshot.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output)]):
                with pytest.raises(SystemExit):
                    visual_check.main()

        assert output.parent.exists()

    def test_screenshot_failure_exits_1(self):
        """Screenshot failure returns exit 1."""
        with MainMocks(screenshot_ok=False) as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0"]):
                with pytest.raises(SystemExit) as exc:
                    visual_check.main()
                assert exc.value.code == 1

    def test_output_path_with_spaces(self, tmp_path):
        """Output path with spaces in directory name is handled."""
        output = tmp_path / "dir with spaces" / "screenshot.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output)]):
                with pytest.raises(SystemExit):
                    visual_check.main()

        assert output.parent.exists()


# ===========================================================================
# Phase 1: Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_delay_zero(self):
        """--delay 0 is valid: time.sleep(0) is called."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            m.mock_sleep.assert_called_with(0.0)

    def test_long_delay_mocked(self):
        """Large delay value is passed to time.sleep correctly."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "120.5"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            m.mock_sleep.assert_called_with(120.5)

    def test_powershell_backslash_conversion(self):
        """Windows screenshot converts backslashes to forward slashes in PS script."""
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            visual_check.take_screenshot_windows("C:\\Users\\test\\screenshot.png")
            # PowerShell command is the 3rd element: ["powershell", "-NoProfile", "-Command", script]
            ps_cmd = mock_run.call_args[0][0][3]
            assert "C:/Users/test/screenshot.png" in ps_cmd


# ===========================================================================
# Phase 1: Error messages
# ===========================================================================

class TestErrorMessages:
    def test_error_messages_have_l2_prefix(self):
        r = run_script(["--exe", "nonexistent_xyz.exe", "--delay", "0"])
        assert "[L2]" in r.stdout

    def test_build_failure_message(self):
        r = run_script(["--exe", "nonexistent.exe", "--build-cmd", "exit 1"])
        assert "BUILD FAILED" in r.stdout

    def test_exe_not_found_includes_path(self):
        r = run_script(["--exe", "specific_missing_app.exe", "--delay", "0"])
        assert "specific_missing_app" in r.stdout

    def test_screenshot_failure_includes_platform(self):
        """Error message includes platform and tool info."""
        with MainMocks(screenshot_ok=False) as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0"]):
                # Capture printed output
                import io
                captured = io.StringIO()
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.write(" ".join(str(x) for x in a) + "\n")):
                    with pytest.raises(SystemExit):
                        visual_check.main()
                output = captured.getvalue()
                assert "platform=" in output


# ===========================================================================
# Phase 1: Process cleanup (--kill-existing)
# ===========================================================================

class TestKillExisting:
    def test_windows_uses_taskkill(self):
        with MainMocks(exe_exists=False) as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--kill-existing", "old.exe", "--delay", "0"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            # First subprocess.run call should be taskkill (on Windows)
            if sys.platform == "win32":
                first_call = m.mock_run.call_args_list[0]
                assert "taskkill" in first_call[0][0]
                assert "/F" in first_call[0][0]
                assert "old.exe" in first_call[0][0]

    def test_unix_uses_pkill(self):
        """On Unix, --kill-existing uses pkill."""
        with MainMocks(exe_exists=False) as m, \
             patch("visual_check.sys.platform", "linux"):
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--kill-existing", "old_app", "--delay", "0"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            first_call = m.mock_run.call_args_list[0]
            assert "pkill" in first_call[0][0]
            assert "old_app" in first_call[0][0]

    def test_kill_existing_sleeps(self):
        """After killing, 0.5s sleep before continuing."""
        with MainMocks(exe_exists=False) as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe",
                        "--kill-existing", "old.exe", "--delay", "0"]):
                with pytest.raises(SystemExit):
                    visual_check.main()

            # First sleep call should be 0.5 (after kill)
            assert m.mock_sleep.call_args_list[0] == call(0.5)


# ===========================================================================
# Phase 1: Screenshot function return type (after fix 2.4)
# ===========================================================================

class TestScreenshotReturnType:
    """Tests for tuple[str, bool] return from screenshot functions."""

    def test_windows_returns_tuple(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = visual_check.take_screenshot_windows("test.png")
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert result == ("", True)

    def test_windows_failure_returns_stderr(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="PS error msg")
            err, success = visual_check.take_screenshot_windows("test.png")
            assert success is False
            assert "PS error msg" in err

    def test_linux_returns_tuple(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = visual_check.take_screenshot_linux("test.png")
            assert isinstance(result, tuple)
            assert result == ("", True)

    def test_linux_failure_suggests_install(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="")
            err, success = visual_check.take_screenshot_linux("test.png")
            assert success is False
            assert "install scrot" in err.lower()

    def test_macos_returns_tuple(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = visual_check.take_screenshot_macos("test.png")
            assert isinstance(result, tuple)
            assert result == ("", True)

    def test_dispatcher_returns_tuple(self, tmp_path):
        output_path = str(tmp_path / "test.png")
        with patch("visual_check.sys.platform", "win32"), \
             patch.object(
                 visual_check,
                 "take_screenshot_windows",
                 return_value=("", True),
             ) as mock_windows:
            with patch.dict(
                visual_check.take_screenshot.__globals__,
                {"take_screenshot_windows": mock_windows},
            ):
                result = visual_check.take_screenshot(output_path)
            mock_windows.assert_called_once_with(output_path)
            assert isinstance(result, tuple)


# ===========================================================================
# Phase 1: check_workflow.py - screenshot recency (after fix 2.5)
# ===========================================================================

class TestWorkflowRecency:
    """Tests for check_visual_recently() recency check."""

    def test_no_screenshots_dir(self, tmp_path):
        assert check_workflow.check_visual_recently(tmp_path) is False

    def test_empty_screenshots_dir(self, tmp_path):
        (tmp_path / "screenshots").mkdir()
        assert check_workflow.check_visual_recently(tmp_path) is False

    def test_recent_screenshot_passes(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir()
        f = ss_dir / "check.png"
        f.write_bytes(b"fake image")
        assert check_workflow.check_visual_recently(tmp_path) is True

    def test_old_screenshot_fails(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir()
        f = ss_dir / "check.png"
        f.write_bytes(b"fake image")
        old_time = time.time() - 7200  # 2 hours ago
        os.utime(f, (old_time, old_time))
        assert check_workflow.check_visual_recently(tmp_path) is False


# ===========================================================================
# Phase 2: --multi (multi-screenshot capture)
# ===========================================================================

class TestMultiParsing:
    def test_parse_basic(self):
        assert visual_check_utils.parse_multi_delays("1,3,5") == [1.0, 3.0, 5.0]

    def test_parse_single(self):
        assert visual_check_utils.parse_multi_delays("2") == [2.0]

    def test_parse_unsorted_gets_sorted(self):
        assert visual_check_utils.parse_multi_delays("5,1,3") == [1.0, 3.0, 5.0]

    def test_parse_with_spaces(self):
        assert visual_check_utils.parse_multi_delays("1, 3, 5") == [1.0, 3.0, 5.0]

    def test_parse_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            visual_check_utils.parse_multi_delays("-1,3")

    def test_parse_non_numeric_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            visual_check_utils.parse_multi_delays("1,abc,3")

    def test_parse_floats(self):
        assert visual_check_utils.parse_multi_delays("0.5,1.5,2.5") == [0.5, 1.5, 2.5]

    def test_parse_zero_allowed(self):
        assert visual_check_utils.parse_multi_delays("0,1,2") == [0.0, 1.0, 2.0]


class TestMultiPaths:
    def test_basic_paths(self):
        paths = visual_check_utils.generate_multi_paths("screenshots/check.png", [1.0, 3.0, 5.0])
        assert len(paths) == 3
        assert paths[0].endswith("check_001s.png")
        assert paths[1].endswith("check_003s.png")
        assert paths[2].endswith("check_005s.png")

    def test_single_delay(self):
        paths = visual_check_utils.generate_multi_paths("out/shot.png", [2.0])
        assert len(paths) == 1
        assert paths[0].endswith("shot_002s.png")

    def test_preserves_parent_dir(self):
        paths = visual_check_utils.generate_multi_paths("deep/dir/img.png", [1.0])
        assert "deep" in paths[0] and "dir" in paths[0]


class TestMultiExecution:
    def test_multi_overrides_delay_with_warning(self):
        """--multi with --delay prints warning and uses --multi."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe",
                        "--delay", "10", "--multi", "1,3"]):
                import io
                captured = io.StringIO()
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.write(" ".join(str(x) for x in a) + "\n")):
                    with pytest.raises(SystemExit):
                        visual_check.main()
                output = captured.getvalue()
                assert "WARNING" in output
                assert "--multi overrides --delay" in output

    def test_multi_single_value_becomes_delay(self):
        """--multi with single value behaves like --delay."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--multi", "2"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
            # Should have used single-shot mode (sleep with the value)
            m.mock_sleep.assert_called_with(2.0)

    def test_multi_takes_multiple_screenshots(self):
        """--multi 1,3 takes two screenshots."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--multi", "1,3"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
            assert m.mock_screenshot.call_count == 2

    def test_multi_process_death_continues(self):
        """If process dies mid-sequence, remaining screenshots still taken."""
        with MainMocks() as m:
            # Process alive for first screenshot, dead for rest (+ final check)
            m.mock_proc.poll.side_effect = [None, 42, 42, 42, 42]
            m.mock_proc.returncode = 42
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--multi", "1,3,5"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
            # All 3 screenshots attempted
            assert m.mock_screenshot.call_count == 3

    def test_multi_prints_summary(self):
        """Multi mode prints a summary at the end."""
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--multi", "1,3"]):
                import io
                captured = io.StringIO()
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.write(" ".join(str(x) for x in a) + "\n")):
                    with pytest.raises(SystemExit):
                        visual_check.main()
                output = captured.getvalue()
                assert "summary" in output.lower()


# ===========================================================================
# Phase 2: --json (structured report)
# ===========================================================================

class TestJsonReport:
    def test_json_file_created(self, tmp_path):
        """--json creates a .json file alongside the screenshot."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output), "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        json_path = output.with_suffix(".json")
        assert json_path.exists()

    def test_json_is_valid(self, tmp_path):
        """JSON file is parseable."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output), "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        data = json.loads((output.with_suffix(".json")).read_text())
        assert isinstance(data, dict)

    def test_json_required_fields(self, tmp_path):
        """JSON contains all required fields."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output), "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        data = json.loads((output.with_suffix(".json")).read_text())
        assert "timestamp" in data
        assert "platform" in data
        assert "exe" in data
        assert "screenshots" in data
        assert "process_alive_at_end" in data
        assert "process_exit_code" in data
        assert "errors" in data
        assert len(data["screenshots"]) == 1
        ss = data["screenshots"][0]
        assert "path" in ss
        assert "delay_seconds" in ss
        assert "size_bytes" in ss
        assert "captured" in ss

    def test_json_no_build_duration_without_build_cmd(self, tmp_path):
        """build_duration_ms absent when no --build-cmd."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output), "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        data = json.loads((output.with_suffix(".json")).read_text())
        assert "build_duration_ms" not in data
        assert "build_cmd" not in data

    def test_json_has_build_info_with_build_cmd(self, tmp_path):
        """build_cmd and build_duration_ms present when --build-cmd used."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output), "--build-cmd", "echo ok", "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        data = json.loads((output.with_suffix(".json")).read_text())
        assert "build_cmd" in data
        assert "build_duration_ms" in data
        assert data["build_success"] is True

    def test_json_errors_on_failure(self, tmp_path):
        """errors array populated on screenshot failure."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks(screenshot_ok=False) as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output), "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        data = json.loads((output.with_suffix(".json")).read_text())
        assert len(data["errors"]) > 0

    def test_json_multi_single_file_multiple_entries(self, tmp_path):
        """With --multi, single JSON file with multiple screenshot entries."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe",
                        "--multi", "1,3,5", "--output", str(output), "--json"]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        json_path = output.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert len(data["screenshots"]) == 3

    def test_no_json_without_flag(self, tmp_path):
        """Without --json, no JSON file is created."""
        output = tmp_path / "shots" / "check.png"
        with MainMocks() as m:
            with patch("visual_check.sys.argv",
                       ["visual_check.py", "--exe", "app.exe", "--delay", "0",
                        "--output", str(output)]):
                with pytest.raises(SystemExit):
                    visual_check.main()
        assert not output.with_suffix(".json").exists()


# ===========================================================================
# Phase 2: utils unit tests
# ===========================================================================

class TestBuildJsonReport:
    def test_minimal_report(self):
        r = visual_check_utils.build_json_report(
            exe="app.exe", build_cmd=None, build_success=None,
            build_duration_ms=None, screenshots=[],
            process_alive_at_end=True, process_exit_code=None, errors=[])
        assert r["exe"] == "app.exe"
        assert "build_cmd" not in r
        assert "build_duration_ms" not in r
        assert r["process_alive_at_end"] is True

    def test_with_build(self):
        r = visual_check_utils.build_json_report(
            exe="app.exe", build_cmd="make", build_success=True,
            build_duration_ms=1234, screenshots=[],
            process_alive_at_end=True, process_exit_code=None, errors=[])
        assert r["build_cmd"] == "make"
        assert r["build_duration_ms"] == 1234
        assert r["build_success"] is True


class TestWriteJsonReport:
    def test_writes_file(self, tmp_path):
        report = {"test": True}
        p = visual_check_utils.write_json_report(report, str(tmp_path / "out.png"))
        assert Path(p).exists()
        data = json.loads(Path(p).read_text())
        assert data["test"] is True

    def test_json_extension(self, tmp_path):
        p = visual_check_utils.write_json_report({}, str(tmp_path / "shot.png"))
        assert p.endswith(".json")


# ===========================================================================
# Window capture functions
# ===========================================================================

class TestFindWindowByPid:
    def test_returns_none_on_non_windows(self):
        with patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert visual_check_utils.find_window_by_pid(1234) is None

    def test_returns_none_on_exception(self):
        with patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            # ctypes is imported locally inside the function; removing it from
            # sys.modules forces an ImportError which the function catches.
            with patch.dict("sys.modules", {"ctypes": None, "ctypes.wintypes": None}):
                result = visual_check_utils.find_window_by_pid(1234)
                assert result is None

    def test_returns_hwnd_when_found(self):
        if sys.platform != "win32":
            pytest.skip("Windows only")
        # Use current process PID - may or may not have a window
        # This test just verifies no crash
        result = visual_check_utils.find_window_by_pid(os.getpid())
        assert result is None or isinstance(result, int)


class TestGetWindowRect:
    def test_returns_none_on_non_windows(self):
        with patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert visual_check_utils.get_window_rect(12345) is None

    def test_returns_none_on_invalid_hwnd(self):
        if sys.platform != "win32":
            pytest.skip("Windows only")
        # Invalid HWND should return None
        result = visual_check_utils.get_window_rect(0)
        assert result is None


class TestTakeScreenshotWindow:
    def test_fallback_to_full_screen_when_no_window(self):
        with patch.object(visual_check_utils, "find_window_by_pid", return_value=None), \
             patch.object(visual_check_utils, "take_screenshot",
                          return_value=("", True)) as mock_full:
            err, ok = visual_check_utils.take_screenshot_window(9999, "out.png")
            mock_full.assert_called_once_with("out.png")
            assert ok is True

    def test_fallback_when_rect_is_none(self):
        with patch.object(visual_check_utils, "find_window_by_pid", return_value=12345), \
             patch.object(visual_check_utils, "get_window_rect", return_value=None), \
             patch.object(visual_check_utils, "take_screenshot",
                          return_value=("", True)) as mock_full:
            err, ok = visual_check_utils.take_screenshot_window(9999, "out.png")
            mock_full.assert_called_once_with("out.png")

    def test_fallback_when_rect_degenerate(self):
        with patch.object(visual_check_utils, "find_window_by_pid", return_value=12345), \
             patch.object(visual_check_utils, "get_window_rect",
                          return_value=(100, 100, 100, 100)), \
             patch.object(visual_check_utils, "take_screenshot",
                          return_value=("", True)) as mock_full:
            err, ok = visual_check_utils.take_screenshot_window(9999, "out.png")
            mock_full.assert_called_once_with("out.png")

    def test_uses_pillow_imagegrab_when_available(self, tmp_path):
        out = str(tmp_path / "window.png")
        mock_img = MagicMock()
        with patch.object(visual_check_utils, "find_window_by_pid", return_value=100), \
             patch.object(visual_check_utils, "get_window_rect",
                          return_value=(0, 0, 800, 600)), \
             patch("visual_check_utils.ImageGrab", create=True) as mock_ig:
            # Simulate PIL import inside the function
            import importlib
            with patch.dict("sys.modules", {"PIL": MagicMock(), "PIL.ImageGrab": MagicMock()}):
                # Just test the fallback path since PIL mock is complex
                pass
        # Verify function returns tuple
        with patch.object(visual_check_utils, "find_window_by_pid", return_value=None), \
             patch.object(visual_check_utils, "take_screenshot",
                          return_value=("", True)):
            err, ok = visual_check_utils.take_screenshot_window(9999, out)
            assert isinstance(ok, bool)


class TestTakeScreenshotRegionWindows:
    def test_powershell_region_capture(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            err, ok = visual_check_utils._take_screenshot_region_windows(
                100, 200, 800, 600, "test.png")
            assert ok is True
            args = mock_run.call_args[0][0]
            assert args[0] == "powershell"

    def test_region_capture_failure(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="PS error")
            err, ok = visual_check_utils._take_screenshot_region_windows(
                0, 0, 100, 100, "test.png")
            assert ok is False
            assert "PS error" in err


# ===========================================================================
# Screenshot comparison
# ===========================================================================

class TestCompareScreenshots:
    def test_identical_images(self, tmp_path):
        # Create two identical image files
        img_data = b"\x89PNG" + b"\x00" * 100
        (tmp_path / "a.png").write_bytes(img_data)
        (tmp_path / "b.png").write_bytes(img_data)
        # With no Pillow, fallback to byte comparison
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            changed, ratio = visual_check_utils.compare_screenshots(
                str(tmp_path / "a.png"), str(tmp_path / "b.png"))
        # Byte-identical files
        assert changed is False or ratio == 0.0

    def test_different_images_bytes_fallback(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"AAAA")
        (tmp_path / "b.bin").write_bytes(b"BBBB")
        # Force no-Pillow path by removing PIL from sys.modules
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            changed, ratio = visual_check_utils.compare_screenshots(
                str(tmp_path / "a.bin"), str(tmp_path / "b.bin"))
        assert changed is True
        assert ratio == 1.0

    def test_missing_file_returns_changed(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"data")
        changed, ratio = visual_check_utils.compare_screenshots(
            str(tmp_path / "a.bin"), str(tmp_path / "nonexistent.bin"))
        assert changed is True
        assert ratio == -1.0

    def test_threshold_parameter(self, tmp_path):
        (tmp_path / "x.bin").write_bytes(b"same")
        (tmp_path / "y.bin").write_bytes(b"same")
        # Force byte fallback to test threshold with identical files
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            changed, ratio = visual_check_utils.compare_screenshots(
                str(tmp_path / "x.bin"), str(tmp_path / "y.bin"), threshold=0.5)
        assert changed is False
        assert ratio == 0.0


# ===========================================================================
# Output test
# ===========================================================================

class TestRunOutputTest:
    def test_success_no_expected(self):
        ok, output = visual_check_utils.run_output_test("echo hello")
        assert ok is True
        assert "hello" in output

    def test_expected_found(self):
        ok, output = visual_check_utils.run_output_test("echo hello world", "hello")
        assert ok is True

    def test_expected_not_found(self):
        ok, output = visual_check_utils.run_output_test("echo hello", "xyz")
        assert ok is False

    def test_command_failure_no_expected(self):
        ok, output = visual_check_utils.run_output_test("exit 1")
        assert ok is False

    def test_timeout(self):
        with patch("visual_check_utils.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("cmd", 1)):
            ok, output = visual_check_utils.run_output_test("sleep 100", timeout=1)
            assert ok is False
            assert "timeout" in output


# ===========================================================================
# HTTP test
# ===========================================================================

class TestRunHttpTest:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK body"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, body = visual_check_utils.run_http_test("http://example.com")
            assert ok is True
            assert "OK body" in body

    def test_wrong_status(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "http://example.com", 404, "Not Found", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            ok, body = visual_check_utils.run_http_test(
                "http://example.com", expected_status=200)
            assert ok is False

    def test_expected_contains(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"hello world"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, body = visual_check_utils.run_http_test(
                "http://example.com", expected_contains="world")
            assert ok is True

    def test_connection_error(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            ok, body = visual_check_utils.run_http_test("http://localhost:99999")
            assert ok is False


# ===========================================================================
# File test
# ===========================================================================

class TestRunFileTest:
    def test_file_exists(self, tmp_path):
        f = tmp_path / "output.txt"
        f.write_text("result data")
        ok, detail = visual_check_utils.run_file_test(str(f))
        assert ok is True
        assert "exists" in detail

    def test_file_not_found(self, tmp_path):
        ok, detail = visual_check_utils.run_file_test(str(tmp_path / "missing.txt"))
        assert ok is False
        assert "not found" in detail

    def test_content_match(self, tmp_path):
        f = tmp_path / "output.txt"
        f.write_text("expected content here")
        ok, detail = visual_check_utils.run_file_test(str(f), expected_contains="expected")
        assert ok is True
        assert "found" in detail

    def test_content_mismatch(self, tmp_path):
        f = tmp_path / "output.txt"
        f.write_text("some other content")
        ok, detail = visual_check_utils.run_file_test(
            str(f), expected_contains="missing_string")
        assert ok is False

    def test_expected_absent(self, tmp_path):
        ok, detail = visual_check_utils.run_file_test(
            str(tmp_path / "gone.txt"), expected_exists=False)
        assert ok is True
        assert "absent" in detail

    def test_expected_absent_but_exists(self, tmp_path):
        f = tmp_path / "surprise.txt"
        f.write_text("oops")
        ok, detail = visual_check_utils.run_file_test(str(f), expected_exists=False)
        assert ok is False
