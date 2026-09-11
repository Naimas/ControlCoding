"""Unit tests for visual_test.py - L2+ interactive visual testing."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import visual_test
import visual_check_utils


# ===========================================================================
# Action parsing (C-S10)
# ===========================================================================

class TestActionParsing:
    def test_parse_inline_basic(self):
        result = visual_test.parse_actions("wait:2,screenshot:front.png,key:w")
        assert result == [{"action": "wait", "param": "2"},
                          {"action": "screenshot", "param": "front.png"},
                          {"action": "key", "param": "w"}]

    def test_parse_inline_no_param(self):
        result = visual_test.parse_actions("left_click")
        assert result == [{"action": "left_click", "param": ""}]

    def test_parse_inline_spaces(self):
        result = visual_test.parse_actions("wait:2, screenshot:test.png")
        assert len(result) == 2
        assert result[1]["action"] == "screenshot"

    def test_parse_inline_empty_parts_skipped(self):
        result = visual_test.parse_actions("wait:2,,key:w")
        assert len(result) == 2

    def test_parse_json_file(self, tmp_path):
        f = tmp_path / "actions.json"
        f.write_text(json.dumps([
            {"action": "wait", "param": "3"},
            {"action": "screenshot", "param": "test.png"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result == [{"action": "wait", "param": "3"},
                          {"action": "screenshot", "param": "test.png"}]

    def test_parse_json_file_param_coerced_to_str(self, tmp_path):
        f = tmp_path / "actions.json"
        f.write_text(json.dumps([{"action": "wait", "param": 3}]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["param"] == "3"

    def test_parse_json_file_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            visual_test.parse_actions_file(str(f))

    def test_parse_json_file_not_array(self, tmp_path):
        f = tmp_path / "obj.json"
        f.write_text('{"action": "wait"}')
        with pytest.raises(ValueError, match="array"):
            visual_test.parse_actions_file(str(f))

    def test_parse_json_file_with_expect(self, tmp_path):
        f = tmp_path / "actions.json"
        f.write_text(json.dumps([
            {"action": "screenshot", "param": "test.png",
             "expect": "A red button visible"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["expect"] == "A red button visible"

    def test_parse_json_file_without_expect(self, tmp_path):
        f = tmp_path / "actions.json"
        f.write_text(json.dumps([{"action": "wait", "param": "1"}]))
        result = visual_test.parse_actions_file(str(f))
        assert "expect" not in result[0]

    def test_unknown_action_warns(self, tmp_path):
        with pytest.warns(UserWarning, match="Unknown action"):
            result = visual_test.execute_action("fly", "up", Path(tmp_path))
        assert result["success"] is False
        assert "skipped" in result["result"]


# ===========================================================================
# Action execution - Computer Use Tool vocabulary (C-S11)
# ===========================================================================

class TestActionExecution:
    def test_wait(self):
        with patch("visual_test.time") as mock_time:
            result = visual_test.execute_action("wait", "2", Path("."))
            mock_time.sleep.assert_called_with(2.0)
            assert result["success"]
            assert "waited 2.0s" in result["result"]

    def test_wait_default(self):
        with patch("visual_test.time") as mock_time:
            result = visual_test.execute_action("wait", "", Path("."))
            mock_time.sleep.assert_called_with(1.0)

    def test_key(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("key", "w", Path("."))
            mock_pa.press.assert_called_with("w")
            assert result["success"]

    def test_keys(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("keys", "ctrl+s", Path("."))
            mock_pa.hotkey.assert_called_with("ctrl", "s")

    def test_type(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("type", "hello", Path("."))
            mock_pa.write.assert_called_with("hello", interval=0.02)

    def test_left_click_no_coords(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("left_click", "", Path("."))
            mock_pa.click.assert_called_once_with(button="left")
            assert "current position" in result["result"]

    def test_left_click_with_coords(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("left_click", "100,200", Path("."))
            mock_pa.click.assert_called_with(100, 200, button="left")
            assert "(100,200)" in result["result"]

    def test_right_click_no_coords(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("right_click", "", Path("."))
            mock_pa.click.assert_called_once_with(button="right")
            assert result["success"]
            assert "right" in result["result"]

    def test_right_click_with_coords(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("right_click", "50,60", Path("."))
            mock_pa.click.assert_called_with(50, 60, button="right")

    def test_middle_click(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("middle_click", "", Path("."))
            mock_pa.click.assert_called_once_with(button="middle")

    def test_left_click_relative(self):
        with patch("visual_test.pyautogui") as mock_pa:
            mock_pa.size.return_value = (1920, 1080)
            result = visual_test.execute_action("left_click_relative", "100,-50", Path("."))
            mock_pa.click.assert_called_with(1060, 490)

    def test_mouse_move(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("mouse_move", "500,300", Path("."))
            mock_pa.moveTo.assert_called_with(500, 300)
            assert result["success"]

    def test_mouse_move_relative(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("mouse_move_relative", "10,-20", Path("."))
            mock_pa.moveRel.assert_called_with(10, -20)

    def test_left_click_drag(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("left_click_drag", "400,300", Path("."))
            mock_pa.moveTo.assert_called_with(400, 300, duration=0.5)
            assert result["success"]

    def test_scroll(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("scroll", "3", Path("."))
            mock_pa.scroll.assert_called_with(3)

    def test_scroll_negative(self):
        with patch("visual_test.pyautogui") as mock_pa:
            result = visual_test.execute_action("scroll", "-5", Path("."))
            mock_pa.scroll.assert_called_with(-5)

    def test_screenshot_success(self, tmp_path):
        out = tmp_path / "test.png"
        out.write_bytes(b"x" * 100)
        with patch("visual_test.take_screenshot", return_value=("", True)):
            result = visual_test.execute_action("screenshot", "test.png", tmp_path)
        assert result["success"]
        assert result["_size"] == 100

    def test_screenshot_failure(self, tmp_path):
        with patch("visual_test.take_screenshot", return_value=("tool error", False)):
            result = visual_test.execute_action("screenshot", "fail.png", tmp_path)
        assert result["success"] is False

    def test_focus(self):
        with patch("visual_test.focus_window", return_value=True) as mock_fw:
            result = visual_test.execute_action("focus", "MyApp", Path("."))
            mock_fw.assert_called_with("MyApp")
            assert result["success"]

    def test_focus_not_found(self):
        with patch("visual_test.focus_window", return_value=False):
            result = visual_test.execute_action("focus", "Missing", Path("."))
            assert result["success"] is False


# ===========================================================================
# Shared utils: process lifecycle (C-S13)
# ===========================================================================

class TestSharedUtils:
    def test_run_build_success(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            success, ms = visual_check_utils.run_build("make")
            assert success is True
            assert ms >= 0

    def test_run_build_failure(self):
        with patch("visual_check_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            success, ms = visual_check_utils.run_build("bad_cmd")
            assert success is False

    def test_kill_process_windows(self):
        with patch("visual_check_utils.subprocess.run") as mock_run, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_proc = MagicMock(pid=12345, returncode=0)
            visual_check_utils.kill_process(mock_proc)
            args = mock_run.call_args[0][0]
            assert "taskkill" in args and "/T" in args

    def test_kill_process_unix(self):
        with patch("visual_check_utils.os") as mock_os, \
             patch("visual_check_utils.sys") as mock_sys, \
             patch("visual_check_utils.signal") as mock_signal:
            mock_sys.platform = "linux"
            mock_signal.SIGTERM = 15
            mock_proc = MagicMock(pid=12345, returncode=0)
            visual_check_utils.kill_process(mock_proc)
            mock_os.killpg.assert_called_with(12345, 15)

    def test_kill_process_timeout_fallback(self):
        with patch("visual_check_utils.subprocess.run"), \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_proc = MagicMock(pid=123, returncode=-1)
            mock_proc.wait.side_effect = [subprocess.TimeoutExpired("p", 5), None]
            visual_check_utils.kill_process(mock_proc)
            mock_proc.kill.assert_called_once()

    def test_launch_process_sets_cwd(self):
        with patch("visual_check_utils.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            visual_check_utils.launch_process("/some/dir/app.exe")
            assert "cwd" in mock_popen.call_args[1]

    def test_take_screenshot_windows_dispatch(self):
        with patch.object(visual_check_utils, "take_screenshot_windows",
                          return_value=("", True)) as m, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            visual_check_utils.take_screenshot("test.png")
            m.assert_called_once_with("test.png")

    def test_take_screenshot_linux_dispatch(self):
        with patch.object(visual_check_utils, "take_screenshot_linux",
                          return_value=("", True)) as m, \
             patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "linux"
            visual_check_utils.take_screenshot("test.png")
            m.assert_called_once_with("test.png")


# ===========================================================================
# Platform checks (C-S12)
# ===========================================================================

class TestPlatformChecks:
    def test_macos_permission_warning(self, capsys):
        with patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "darwin"
            visual_check_utils.check_platform_warnings()
        out = capsys.readouterr().out
        assert "Screen Recording" in out

    def test_windows_dpi_warning(self, capsys):
        with patch("visual_check_utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            import ctypes
            with patch.object(ctypes.windll.shcore, "GetScaleFactorForDevice",
                              return_value=150):
                visual_check_utils.check_platform_warnings()
        out = capsys.readouterr().out
        assert "DPI" in out or "150" in out

    def test_linux_wayland_warning(self, capsys):
        with patch("visual_check_utils.sys") as mock_sys, \
             patch("visual_check_utils.os") as mock_os:
            mock_sys.platform = "linux"
            mock_os.environ = {"XDG_SESSION_TYPE": "wayland"}
            visual_check_utils.check_platform_warnings()
        out = capsys.readouterr().out
        assert "Wayland" in out


# ===========================================================================
# Integration: mock context for main()
# ===========================================================================

class _TestMocks:
    def __init__(self, exe_exists=True, screenshot_ok=True,
                 poll_return=None, file_size=1000):
        self.exe_exists = exe_exists
        self.screenshot_ok = screenshot_ok
        self.poll_return = poll_return
        self.file_size = file_size

    def __enter__(self):
        self._stack = []
        self.mock_proc = MagicMock()
        self.mock_proc.poll.return_value = self.poll_return
        self.mock_proc.pid = 12345
        self.mock_proc.returncode = self.poll_return

        for name, val in [
            ("visual_test.launch_process", self.mock_proc),
            ("visual_test.kill_process", None),
            ("visual_test.run_build", (True, 100.0)),
        ]:
            p = patch(name, return_value=val)
            setattr(self, f"mock_{name.split('.')[-1]}", p.start())
            self._stack.append(p)

        p = patch("visual_test.time.sleep"); self.mock_sleep = p.start(); self._stack.append(p)

        self._mono = [0.0]
        def mono():
            self._mono[0] += 0.1; return self._mono[0]
        p = patch("visual_test.time.monotonic", side_effect=mono)
        p.start(); self._stack.append(p)

        err = "" if self.screenshot_ok else "tool error"
        p = patch("visual_test.take_screenshot", return_value=(err, self.screenshot_ok))
        self.mock_screenshot = p.start(); self._stack.append(p)
        p = patch("visual_test.take_screenshot_window", return_value=(err, self.screenshot_ok))
        self.mock_screenshot_window = p.start(); self._stack.append(p)

        p = patch("visual_test.focus_window", return_value=True)
        self.mock_focus = p.start(); self._stack.append(p)
        p = patch("visual_test.pyautogui"); self.mock_pa = p.start(); self._stack.append(p)
        p = patch("visual_test.check_platform_warnings"); p.start(); self._stack.append(p)

        outer = self
        class FakePath(type(Path())):
            def exists(self):
                s = str(self)
                if s.endswith(".exe") or "app" in s:
                    return outer.exe_exists
                return outer.screenshot_ok
            def stat(self):
                return MagicMock(st_size=outer.file_size)
            def mkdir(self, *a, **kw):
                os.makedirs(str(self), exist_ok=True)
        p = patch("visual_test.Path", FakePath)
        p.start(); self._stack.append(p)
        return self

    def __exit__(self, *args):
        for p in reversed(self._stack):
            p.stop()


# ===========================================================================
# Integration tests (C-S13)
# ===========================================================================

class TestIntegration:
    def test_full_sequence(self):
        with _TestMocks():
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "wait:1,key:w,screenshot:test.png"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0

    def test_json_output(self, tmp_path):
        with _TestMocks():
            out_dir = str(tmp_path / "shots")
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "wait:1,screenshot:test.png",
                "--output-dir", out_dir, "--json"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0
            json_path = Path(out_dir) / "visual_test.json"
            assert json_path.exists()
            data = json.loads(json_path.read_text())
            assert "actions_executed" in data
            assert "total_actions" in data
            assert data["total_actions"] == 2

    def test_actions_file(self, tmp_path):
        af = tmp_path / "seq.json"
        af.write_text(json.dumps([
            {"action": "wait", "param": "1"},
            {"action": "key", "param": "space"},
        ]))
        with _TestMocks():
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions-file", str(af)
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0

    def test_actions_file_with_expect(self, tmp_path):
        af = tmp_path / "seq.json"
        af.write_text(json.dumps([
            {"action": "screenshot", "param": "test.png",
             "expect": "Red button visible"},
        ]))
        with _TestMocks():
            out_dir = str(tmp_path / "shots")
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions-file", str(af), "--output-dir", out_dir, "--json"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0
            data = json.loads((Path(out_dir) / "visual_test.json").read_text())
            assert data["actions_executed"][0].get("expect") == "Red button visible"

    def test_no_actions_errors(self):
        with _TestMocks():
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code != 0

    def test_both_actions_errors(self, tmp_path):
        af = tmp_path / "seq.json"
        af.write_text("[]")
        with _TestMocks():
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe",
                "--actions", "wait:1", "--actions-file", str(af)
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code != 0

    def test_exe_not_found(self):
        with _TestMocks(exe_exists=False):
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "missing.exe", "--delay", "0",
                "--actions", "wait:1"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 1

    def test_process_died_early(self):
        with _TestMocks(poll_return=42):
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "wait:1"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 1

    def test_build_failure(self):
        with _TestMocks() as m:
            m.mock_run_build.return_value = (False, 50.0)
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe",
                "--build-cmd", "make", "--actions", "wait:1"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 1
            m.mock_launch_process.assert_not_called()


# ===========================================================================
# Auto-screenshot (C-S12)
# ===========================================================================

class TestAutoScreenshot:
    def test_auto_screenshot_after_action(self):
        with _TestMocks() as m:
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "key:w,key:s",
                "--auto-screenshot-after-action"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0
            # 2 auto-screenshots via take_screenshot_window (window capture)
            assert m.mock_screenshot_window.call_count == 2

    def test_auto_screenshot_not_after_screenshot_action(self):
        with _TestMocks() as m:
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "screenshot:test.png",
                "--auto-screenshot-after-action"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0
            # 1 explicit screenshot via window capture, 0 auto
            assert m.mock_screenshot_window.call_count == 1

    def test_auto_screenshot_in_json_report(self, tmp_path):
        with _TestMocks() as m:
            out_dir = str(tmp_path / "shots")
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "key:w",
                "--auto-screenshot-after-action",
                "--output-dir", out_dir, "--json"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0
            data = json.loads((Path(out_dir) / "visual_test.json").read_text())
            auto_actions = [a for a in data["actions_executed"]
                            if a["action"] == "auto_screenshot"]
            assert len(auto_actions) == 1
            assert "auto" in auto_actions[0]["result"]


# ===========================================================================
# Timeout (C-S12)
# ===========================================================================

class TestTimeout:
    def test_timeout_stops_actions(self, tmp_path):
        with _TestMocks() as m:
            # Make monotonic return values that exceed timeout
            call_count = [0]
            def fast_mono():
                call_count[0] += 1
                return call_count[0] * 100.0  # each call = 100s
            with patch("visual_test.time.monotonic", side_effect=fast_mono):
                out_dir = tmp_path / "test_timeout_dir"
                with patch("visual_test.sys.argv", [
                    "visual_test.py", "--exe", "app.exe", "--delay", "0",
                    "--actions", "wait:1,wait:2,wait:3",
                    "--timeout", "5", "--json", "--output-dir", str(out_dir)
                ]):
                    with pytest.raises(SystemExit) as exc:
                        visual_test.main()
                    assert exc.value.code == 0
                assert (out_dir / "visual_test.json").exists()

    def test_timeout_zero_unlimited(self):
        with _TestMocks() as m:
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions", "wait:1,key:w",
                "--timeout", "0"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0


# ===========================================================================
# Expect field (C-S12)
# ===========================================================================

class TestExpectField:
    def test_expect_in_json_report(self, tmp_path):
        af = tmp_path / "seq.json"
        af.write_text(json.dumps([
            {"action": "screenshot", "param": "check.png",
             "expect": "A visible red button"},
        ]))
        with _TestMocks() as m:
            out_dir = str(tmp_path / "shots")
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions-file", str(af), "--output-dir", out_dir, "--json"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
                assert exc.value.code == 0
            data = json.loads((Path(out_dir) / "visual_test.json").read_text())
            assert data["actions_executed"][0]["expect"] == "A visible red button"

    def test_no_expect_field_when_absent(self, tmp_path):
        af = tmp_path / "seq.json"
        af.write_text(json.dumps([{"action": "wait", "param": "1"}]))
        with _TestMocks() as m:
            out_dir = str(tmp_path / "shots")
            with patch("visual_test.sys.argv", [
                "visual_test.py", "--exe", "app.exe", "--delay", "0",
                "--actions-file", str(af), "--output-dir", out_dir, "--json"
            ]):
                with pytest.raises(SystemExit) as exc:
                    visual_test.main()
            data = json.loads((Path(out_dir) / "visual_test.json").read_text())
            assert "expect" not in data["actions_executed"][0]

    def test_expect_in_structured_output(self, tmp_path):
        """Expectations printed in summary alongside screenshots."""
        ss_info = [{"path": "test.png", "size": 100, "offset": 1.0,
                    "expect": "Red button"}]
        import io
        captured = io.StringIO()
        with patch("builtins.print",
                   side_effect=lambda *a, **kw: captured.write(
                       " ".join(str(x) for x in a) + "\n")):
            visual_test._print_summary(ss_info)
        assert "EXPECT: Red button" in captured.getvalue()


# ===========================================================================
# Verification Strategy parsing
# ===========================================================================

class TestVerificationStrategy:
    def test_visual_method(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "visual", "action": "screenshot", "param": "check.png"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "screenshot"
        assert result[0]["param"] == "check.png"

    def test_command_method(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "command", "cmd": "echo hello", "expected": "hello"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "output_test"
        assert "echo hello|hello" == result[0]["param"]

    def test_command_method_no_expected(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "command", "cmd": "echo ok"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "output_test"
        assert result[0]["param"] == "echo ok"

    def test_http_method(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "http", "url": "http://localhost:3000",
             "expected_status": 200, "expected_contains": "OK"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "http_test"
        assert "http://localhost:3000|200|OK" == result[0]["param"]

    def test_file_method(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "file", "path": "output.txt",
             "expected_contains": "result"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "file_test"
        assert result[0]["param"] == "output.txt|result"

    def test_test_method(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "test", "cmd": "pytest tests/"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "output_test"
        assert result[0]["param"] == "pytest tests/"

    def test_mixed_formats(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"action": "wait", "param": "1"},
            {"method": "visual", "action": "screenshot", "param": "test.png"},
            {"method": "command", "cmd": "echo done", "expected": "done"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert len(result) == 3
        assert result[0]["action"] == "wait"
        assert result[1]["action"] == "screenshot"
        assert result[2]["action"] == "output_test"

    def test_expect_preserved_from_verification(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "visual", "param": "shot.png",
             "expect": "Red button visible"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["expect"] == "Red button visible"

    def test_unknown_method_passthrough(self, tmp_path):
        f = tmp_path / "vs.json"
        f.write_text(json.dumps([
            {"method": "custom", "action": "custom_action", "param": "data"},
        ]))
        result = visual_test.parse_actions_file(str(f))
        assert result[0]["action"] == "custom_action"


# ===========================================================================
# New action types: compare, output_test, http_test, file_test
# ===========================================================================

class TestCompareAction:
    def test_compare_different_images(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"AAAA" * 100)
        (tmp_path / "b.bin").write_bytes(b"BBBB" * 100)
        with patch("visual_test.compare_screenshots", return_value=(True, 0.5)):
            result = visual_test.execute_action("compare", "a.bin,b.bin", tmp_path)
        assert result["success"] is True
        assert "differ" in result["result"]

    def test_compare_identical_images(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"same")
        (tmp_path / "b.bin").write_bytes(b"same")
        with patch("visual_test.compare_screenshots", return_value=(False, 0.0)):
            result = visual_test.execute_action("compare", "a.bin,b.bin", tmp_path)
        assert result["success"] is False
        assert "identical" in result["result"]

    def test_compare_bad_param(self):
        result = visual_test.execute_action("compare", "only_one", Path("."))
        assert result["success"] is False
        assert "expected param" in result["result"]

    def test_compare_missing_file(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"data")
        result = visual_test.execute_action("compare", "a.bin,missing.bin", tmp_path)
        assert result["success"] is False
        assert "not found" in result["result"]


class TestOutputTestAction:
    def test_output_test_with_expected(self):
        with patch("visual_test.run_output_test", return_value=(True, "hello world")):
            result = visual_test.execute_action(
                "output_test", "echo hello|hello", Path("."))
        assert result["success"] is True
        assert "pass" in result["result"]

    def test_output_test_no_expected(self):
        with patch("visual_test.run_output_test", return_value=(True, "output")):
            result = visual_test.execute_action(
                "output_test", "echo hi", Path("."))
        assert result["success"] is True

    def test_output_test_failure(self):
        with patch("visual_test.run_output_test", return_value=(False, "wrong")):
            result = visual_test.execute_action(
                "output_test", "echo wrong|expected", Path("."))
        assert result["success"] is False
        assert "fail" in result["result"]


class TestHttpTestAction:
    def test_http_test_success(self):
        with patch("visual_test.run_http_test", return_value=(True, "OK")):
            result = visual_test.execute_action(
                "http_test", "http://localhost:3000", Path("."))
        assert result["success"] is True

    def test_http_test_with_status_and_content(self):
        with patch("visual_test.run_http_test", return_value=(True, "body")) as mock:
            result = visual_test.execute_action(
                "http_test", "http://localhost|200|body", Path("."))
            mock.assert_called_with("http://localhost", 200, "body")
        assert result["success"] is True

    def test_http_test_failure(self):
        with patch("visual_test.run_http_test", return_value=(False, "HTTP 500")):
            result = visual_test.execute_action(
                "http_test", "http://localhost:3000|200", Path("."))
        assert result["success"] is False


class TestFileTestAction:
    def test_file_test_exists(self):
        with patch("visual_test.run_file_test",
                    return_value=(True, "file exists (42 bytes)")):
            result = visual_test.execute_action(
                "file_test", "/tmp/output.txt", Path("."))
        assert result["success"] is True

    def test_file_test_with_content(self):
        with patch("visual_test.run_file_test",
                    return_value=(True, "found: 'result'")):
            result = visual_test.execute_action(
                "file_test", "/tmp/output.txt|result", Path("."))
        assert result["success"] is True

    def test_file_test_not_found(self):
        with patch("visual_test.run_file_test",
                    return_value=(False, "file not found")):
            result = visual_test.execute_action(
                "file_test", "/tmp/missing.txt", Path("."))
        assert result["success"] is False


# ===========================================================================
# Window capture in screenshot action
# ===========================================================================

class TestWindowCapture:
    def test_screenshot_uses_window_capture_with_pid(self, tmp_path):
        out = tmp_path / "test.png"
        out.write_bytes(b"x" * 100)
        with patch("visual_test.take_screenshot_window",
                    return_value=("", True)) as mock_wc, \
             patch("visual_test.take_screenshot") as mock_full:
            result = visual_test.execute_action(
                "screenshot", "test.png", tmp_path, pid=12345)
            mock_wc.assert_called_once()
            mock_full.assert_not_called()
        assert result["success"]

    def test_screenshot_uses_full_capture_without_pid(self, tmp_path):
        out = tmp_path / "test.png"
        out.write_bytes(b"x" * 100)
        with patch("visual_test.take_screenshot_window") as mock_wc, \
             patch("visual_test.take_screenshot",
                   return_value=("", True)) as mock_full:
            result = visual_test.execute_action(
                "screenshot", "test.png", tmp_path, pid=None)
            mock_full.assert_called_once()
            mock_wc.assert_not_called()

    def test_pid_kwarg_backward_compatible(self, tmp_path):
        """Calling without pid kwarg still works (backward compat)."""
        out = tmp_path / "test.png"
        out.write_bytes(b"x" * 100)
        with patch("visual_test.take_screenshot", return_value=("", True)):
            result = visual_test.execute_action("screenshot", "test.png", tmp_path)
        assert result["success"]
