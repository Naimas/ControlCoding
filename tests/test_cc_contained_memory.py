"""Contained-layout lifecycle coverage without live project state or providers."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cc_layout
from cc_documentation_observer import observe
from cc_document_reader import read_document
from cc_controlwork_observer import observe as observe_work
from cc_memory_lib import knowledge_service, work_features
from cc_memory_lib.commands import cmd_memory_work_capture, cmd_memory_work_init, cmd_memory_work_session
from cc_memory_lib.knowledge_catalog import read as catalog_read
from cc_memory_lib.knowledge_query import query as knowledge_query
from cc_memory_lib.knowledge_store import database_path


def contained_project(tmp_path):
    (tmp_path / "cc").mkdir()
    (tmp_path / "cc" / "layout.json").write_text(
        '{"schema":1,"layout":"contained"}\n', encoding="utf-8"
    )
    return tmp_path


def test_contained_work_and_memory_lifecycle_preserves_source_identity(tmp_path):
    project = contained_project(tmp_path)
    (project / "ROADMAP.md").write_text("# Application roadmap\n\nordinary source\n", encoding="utf-8")
    (project / "cc" / "ROADMAP.md").write_text("# ControlCoding roadmap\n\nmanaged source\n", encoding="utf-8")
    (project / "PROJECT.md").write_text("# User-authored project document\n", encoding="utf-8")
    (project / "notes.txt").write_text("User source remains at the project root.\n", encoding="utf-8")

    assert cc_layout.is_contained(project)
    assert cmd_memory_work_init(project, project_name="Fixture", json_output=True) == 0
    assert (project / "cc" / ".controlwork" / "config.json").is_file()
    assert (project / "cc" / "CONTROLWORK.md").is_file()
    assert (project / "cc" / "PROJECT.md").is_file()
    assert (project / "PROJECT.md").read_text(encoding="utf-8").startswith("# User-authored")
    assert (project / ".controlwork").exists() is False
    assert (project / "notes.txt").read_text(encoding="utf-8").startswith("User source")
    assert (project / "ROADMAP.md").read_text(encoding="utf-8").startswith("# Application")

    configured = knowledge_service.configure(project, {
        **knowledge_service.DEFAULT,
        "scopes": ["project", "work"],
        "embedding": "",
    })
    assert configured["archive_path"] == "cc/.controlcoding/knowledge/knowledge.db"
    assert database_path(project) == project / "cc" / ".controlcoding" / "knowledge" / "knowledge.db"
    knowledge_service.reconcile(project)

    rows = catalog_read(project, {"kind": "sources", "offset": 0, "snapshot": None})["rows"]
    by_path = {row["path"]: row for row in rows}
    assert by_path["ROADMAP.md"]["physicalPath"] == "ROADMAP.md"
    assert by_path["cc/ROADMAP.md"]["physicalPath"] == "cc/ROADMAP.md"
    assert by_path["cc/CONTROLWORK.md"]["physicalPath"] == "cc/CONTROLWORK.md"
    assert by_path["cc/PROJECT.md"]["physicalPath"] == "cc/PROJECT.md"
    identity = {path: row["id"] for path, row in by_path.items()}
    first = knowledge_query(project, "managed source", semantic=False)
    citation = next(item for item in first["citations"] if item["path"] == "cc/ROADMAP.md")
    assert citation["physicalPath"] == "cc/ROADMAP.md"

    (project / "notes.txt").write_text("User source remains at the project root. Updated.\n", encoding="utf-8")
    knowledge_service.reconcile(project, reason="source-change")
    refreshed = catalog_read(project, {"kind": "sources", "offset": 0, "snapshot": None})["rows"]
    refreshed_by_path = {row["path"]: row for row in refreshed}
    assert {path: refreshed_by_path[path]["id"] for path in identity} == identity


def test_contained_documentation_map_keeps_root_and_managed_docs_distinct(tmp_path):
    project = contained_project(tmp_path)
    for name, content in (
        ("ROADMAP.md", "# CC roadmap\n\n[Context](CONTROLWORK.md)\n"),
        ("CONTROLWORK.md", "# CC context\n"),
        ("PROJECT.md", "# CC project doc\n"),
    ):
        (project / "cc" / name).write_text(content, encoding="utf-8")
    (project / "ROADMAP.md").write_text("# Application roadmap\n", encoding="utf-8")
    result = observe(str(project), "project")["documentation"]
    docs = {item["path"]: item for item in result["documents"]}
    assert docs["ROADMAP.md"]["title"] == "Application roadmap"
    assert docs["cc/ROADMAP.md"]["title"] == "CC roadmap"
    assert docs["cc/ROADMAP.md"]["physicalPath"] == "cc/ROADMAP.md"
    assert "cc/CONTROLWORK.md" in docs
    assert "cc/PROJECT.md" in docs
    root_doc = read_document(str(project), "ROADMAP.md", docs["ROADMAP.md"]["sha256"], [])
    managed_doc = read_document(str(project), "cc/ROADMAP.md", docs["cc/ROADMAP.md"]["sha256"], [])
    assert root_doc["document"]["markdown"].startswith("# Application")
    assert managed_doc["document"]["markdown"].startswith("# CC roadmap")


def test_legacy_layout_keeps_existing_memory_and_work_roots(tmp_path):
    assert not cc_layout.is_contained(tmp_path)
    assert knowledge_service.status(tmp_path)["archive_path"] == ".controlcoding/knowledge/knowledge.db"
    cmd_memory_work_init(tmp_path, project_name="Legacy", json_output=True)
    assert (tmp_path / ".controlwork" / "config.json").is_file()
    assert not (tmp_path / "cc").exists()


def test_contained_work_capture_session_context_and_wiki_paths(tmp_path, capsys):
    project = contained_project(tmp_path)
    (project / ".obsidian").mkdir()
    (project / ".obsidian" / "app.json").write_text('{"userSetting":true}\n', encoding="utf-8")
    assert cmd_memory_work_init(project, project_name="Fixture", json_output=True) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert "cc/.controlwork/config.json" in initialized["created"]
    assert "cc/CONTROLWORK.md" in initialized["created"]
    assert "cc/PROJECT.md" in initialized["created"]

    assert cmd_memory_work_capture(project, "notes", "Contained note", "Captured Work content.", json_output=True) == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["path"].startswith("cc/.controlwork/memory/notes/")
    note = next((project / "cc" / ".controlwork" / "memory" / "notes").glob("*.md"))
    assert "Captured Work content." in note.read_text(encoding="utf-8")
    readable = work_features.controlwork_read_entry_payload(project, note.relative_to(project).as_posix())
    assert readable["ok"] is True
    assert readable["path"].startswith("cc/.controlwork/memory/notes/")

    assert cmd_memory_work_session(project, "start", session_id="contained-session", topic="Contained task", summary="Session note", json_output=True) == 0
    session = project / "cc" / ".controlwork" / "sessions" / "contained-session.json"
    assert session.is_file()

    packet = work_features.write_context_pack(project, "# Contained context packet\n", "general", "fixture")
    assert packet == project / "cc" / ".controlwork" / "context-packets" / packet.name

    settings_path = work_features.write_obsidian_settings(project)
    wiki_files = work_features.write_obsidian_wiki(project)
    assert settings_path == "cc/.obsidian/app.json"
    assert (project / ".obsidian" / "app.json").read_text(encoding="utf-8").startswith('{"userSetting"')
    assert json.loads((project / "cc" / ".obsidian" / "app.json").read_text(encoding="utf-8"))["userIgnoreFilters"]
    assert wiki_files and all(path.startswith("cc/wiki/") for path in wiki_files)
    assert (project / "cc" / "wiki" / "Home.md").is_file()
    assert not (project / "wiki").exists()
    assert "cc/CONTROLWORK.md" in (project / "cc" / "wiki" / "Home.md").read_text(encoding="utf-8")
    scope = observe_work(str(project), preview=True)["work_scope"]
    assert "cc/CONTROLWORK.md" in scope["paths"]
    observed = observe_work(str(project), scope_id=scope["scope_id"])["work"]
    assert observed["state"] == "present"
    assert any(item["physicalPath"].startswith("cc/.controlwork/memory/notes/") for item in observed["documents"])
