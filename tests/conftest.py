"""Shared test fixtures for ControlCoding tests."""

import os
import sys
import pytest
from pathlib import Path

# Add templates/scripts to path so we can import MCP servers
SCRIPTS_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def tmp_path(tmp_path_factory):
    """Keep fixture paths short enough for Git on Windows, even in spaced roots."""
    return tmp_path_factory.mktemp("t")


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure in a temp directory."""
    # Set environment variables for the session manager
    os.environ["SESSION_PROJECT_ROOT"] = str(tmp_path)
    os.environ["SESSION_DEVLOG_DIR"] = "devlog"
    os.environ["SESSION_STATUS_FILE"] = "STATUS.md"
    os.environ["SESSION_HISTORY_FILE"] = "STATUS_HISTORY.md"

    # Create minimal structure
    (tmp_path / ".controlcoding").mkdir()
    (tmp_path / "CONTROLCODING.md").write_text(
        "# Test Project\n\n## Module Boundaries\n\n"
        "- src/core/engine.cpp\n- src/renderer/renderer.cpp\n",
        encoding="utf-8",
    )

    yield tmp_path

    # Cleanup env vars
    for key in ["SESSION_PROJECT_ROOT", "SESSION_DEVLOG_DIR",
                "SESSION_STATUS_FILE", "SESSION_HISTORY_FILE"]:
        os.environ.pop(key, None)
