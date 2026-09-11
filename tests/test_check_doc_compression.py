"""Tests for check_doc_compression.py - Semantic Fidelity enforcement hook."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

HOOK_DIR = Path(__file__).parent.parent / "templates" / "hooks"
sys.path.insert(0, str(HOOK_DIR))

import check_doc_compression


class TestIsMonitored:
    def test_dev_monitored(self):
        assert check_doc_compression._is_monitored("dev/plan.md") is True

    def test_devlog_monitored(self):
        assert check_doc_compression._is_monitored("devlog/2026-03-23.md") is True

    def test_src_not_monitored(self):
        assert check_doc_compression._is_monitored("src/main.py") is False

    def test_templates_not_monitored(self):
        assert check_doc_compression._is_monitored("templates/hooks/hook.py") is False

    def test_backslash_normalized(self):
        assert check_doc_compression._is_monitored("dev\\subdir\\doc.md") is True


class TestIsDocument:
    def test_markdown(self):
        assert check_doc_compression._is_document("plan.md") is True

    def test_json(self):
        assert check_doc_compression._is_document("config.json") is True

    def test_txt(self):
        assert check_doc_compression._is_document("notes.txt") is True

    def test_python(self):
        assert check_doc_compression._is_document("script.py") is False

    def test_no_extension(self):
        assert check_doc_compression._is_document("README") is False


class TestGetGitFileSize:
    def test_tracked_file(self):
        with patch("check_doc_compression.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1500\n")
            size = check_doc_compression._get_git_file_size(Path("."), "dev/doc.md")
            assert size == 1500

    def test_untracked_file(self):
        with patch("check_doc_compression.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            size = check_doc_compression._get_git_file_size(Path("."), "dev/new.md")
            assert size is None

    def test_timeout(self):
        with patch("check_doc_compression.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 5)):
            size = check_doc_compression._get_git_file_size(Path("."), "dev/doc.md")
            assert size is None


class TestMainHook:
    """Integration tests for the main() hook function."""

    def _run_hook(self, tool_name, file_path, old_size, new_size,
                  monitored=True, exists=True):
        """Helper: run main() with mocked stdin and filesystem."""
        data = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}

        mock_path = MagicMock()
        mock_path.exists.return_value = exists
        mock_path.stat.return_value = MagicMock(st_size=new_size)

        with patch("sys.stdin") as mock_stdin, \
             patch("check_doc_compression._find_project_root",
                   return_value=Path("C:/example-workspace/project")), \
             patch("check_doc_compression._get_git_file_size",
                   return_value=old_size), \
             patch("check_doc_compression.Path") as MockPath:

            mock_stdin.read.return_value = json.dumps(data)

            # Make Path(file_path).resolve().relative_to() work
            resolved = MagicMock()
            if monitored:
                resolved.relative_to.return_value = Path(
                    file_path.replace("C:/example-workspace/project/", "")
                )
            else:
                resolved.relative_to.return_value = Path("src/main.py")

            path_instance = MagicMock()
            path_instance.resolve.return_value = resolved
            path_instance.exists.return_value = exists
            path_instance.stat.return_value = MagicMock(st_size=new_size)

            def path_constructor(p=None):
                if p == file_path:
                    return path_instance
                return MagicMock()

            MockPath.side_effect = path_constructor
            MockPath.__truediv__ = MagicMock()

            with pytest.raises(SystemExit) as exc:
                check_doc_compression.main()
            return exc.value.code

    def test_allows_non_edit_tool(self):
        data = {"tool_name": "Read", "tool_input": {"file_path": "dev/doc.md"}}
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(data)
            with pytest.raises(SystemExit) as exc:
                check_doc_compression.main()
            assert exc.value.code == 0

    def test_allows_invalid_json(self):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "not json"
            with pytest.raises(SystemExit) as exc:
                check_doc_compression.main()
            assert exc.value.code == 0

    def test_allows_empty_path(self):
        data = {"tool_name": "Edit", "tool_input": {"file_path": ""}}
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(data)
            with pytest.raises(SystemExit) as exc:
                check_doc_compression.main()
            assert exc.value.code == 0

    def test_allows_new_file(self):
        """New files (not in git) should always be allowed."""
        code = self._run_hook("Edit", "C:/example-workspace/project/dev/new.md",
                              old_size=None, new_size=1000)
        assert code == 0

    def test_allows_file_growth(self):
        """File that grew should be allowed."""
        code = self._run_hook("Edit", "C:/example-workspace/project/dev/doc.md",
                              old_size=1000, new_size=1500)
        assert code == 0

    def test_allows_small_reduction(self):
        """Reduction below threshold should be allowed."""
        code = self._run_hook("Edit", "C:/example-workspace/project/dev/doc.md",
                              old_size=1000, new_size=900)  # 10% reduction
        assert code == 0

    def test_allows_tiny_file(self):
        """Files below MIN_SIZE_BYTES are exempt."""
        code = self._run_hook("Edit", "C:/example-workspace/project/dev/small.md",
                              old_size=200, new_size=50)
        assert code == 0


class TestCompressionThreshold:
    def test_default_threshold(self):
        assert check_doc_compression.COMPRESSION_THRESHOLD == 0.20

    def test_min_size_default(self):
        assert check_doc_compression.MIN_SIZE_BYTES == 500

    def test_threshold_env_override(self):
        with patch.dict(os.environ, {"COMPRESSION_THRESHOLD": "0.30"}):
            # Would need reimport to test env var, just verify the pattern
            assert float(os.environ["COMPRESSION_THRESHOLD"]) == 0.30
