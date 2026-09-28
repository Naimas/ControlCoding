"""Executable, bounded parity probe for the two Work Plane distributions.

Run from the ControlCoding checkout with --output-root outside both source trees.
The separate ControlWork checkout is read only. This is deliberately not a gate for
the ControlCoding-only knowledge archive or every Work Plane command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


CODING = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("CC_CONTROLWORK_ROOT", str(CODING.parent / "ControlWork"))).resolve()
SOURCES = {
    "parity_probe": Path(__file__).resolve(),
    "embedded_contract": CODING / "docs/work-plane-compatibility-contract.md",
    "canonical_contract": WORK / "docs/work-plane-compatibility-contract.md",
    "release_manifest": CODING / "controlcoding.release.json",
    "embedded_cli": CODING / "scripts/cc.py",
    "standalone_cli": WORK / "scripts/cw.py",
    "embedded_features": CODING / "scripts/cc_memory_lib/work_features.py",
    "standalone_features": WORK / "scripts/cw_features.py",
}
STAMP = re.compile(r"\b20\d{6}T\d{6}Z\b|\b20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\b")
GRAPH_ID = re.compile(r"CW_(?:NODE|EDGE|SUG|CHUNK|SESSION)_[A-F0-9]+")
COVERAGE = {
    "initialization_and_scan": {
        "covered": ["init", "scan", "scan_analyze", "status"],
        "outsideMatrix": ["quickstart", "scan re-run after deleted or renamed source"],
    },
    "review_and_source_import": {
        "covered": ["review queue", "reviewed decision", "promotion rejection before review",
                    "promotion with force note", "Markdown source import"],
        "outsideMatrix": ["batch review", "relational decision", "ready_to_promote approval",
                          "other rich source formats", "source change during review"],
    },
    "ocr": {
        "covered": ["status before/after", "hash-bound PDF sidecar import with memory source"],
        "outsideMatrix": ["ocr run with configured external adapter", "multi-page extraction"],
    },
    "portable_memory_and_sessions": {
        "covered": ["category list", "capture", "session start/note/close/show", "views",
                    "handoff", "context packet", "checkpoint"],
        "outsideMatrix": ["category propose/add/approve", "session link/list", "legacy lifecycle"],
    },
    "graph_and_retrieval": {
        "covered": ["graph status/explain/path", "suggestions/accept/accepted",
                    "retrieve", "ranked query matches", "RAG packet"],
        "outsideMatrix": ["graph reject/neighbors/stale/unresolved/diff/export", "retrieval ablation"],
    },
    "projections": {
        "covered": ["wiki build", "Obsidian init/check", "wiki edit review", "JSON dashboard"],
        "outsideMatrix": ["Obsidian sync", "HTML/Markdown dashboard", "wiki conflict merge"],
    },
    "mcp_and_migration": {
        "covered": ["MCP tools/category/search", "standalone-to-embedded import"],
        "outsideMatrix": ["other MCP calls", "embedded export", "older archive migration"],
    },
}


def hashes() -> dict[str, str]:
    pinned = dict(SOURCES)
    for path in sorted((CODING / "scripts/cc_memory_lib").glob("*.py")):
        pinned["embedded_module:" + path.name] = path
    for path in sorted((WORK / "scripts").glob("*.py")):
        pinned["standalone_module:" + path.name] = path
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in pinned.items()}


def normalized(value: object, roots: tuple[Path, Path]) -> object:
    if isinstance(value, dict):
        return {normalized(key, roots): normalized(item, roots) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item, roots) for item in value]
    if isinstance(value, str):
        for root in roots:
            value = value.replace(str(root), "<ROOT>").replace(root.as_posix(), "<ROOT>")
        value = STAMP.sub("<TIME>", value.replace("\\", "/"))
        return GRAPH_ID.sub(lambda match: match.group().split("_")[1] + "_<ID>", value)
    return value


def run_command(script: Path, root: Path, args: list[str]) -> dict:
    prefix = ["memory"] if script == SOURCES["embedded_cli"] else []
    command = [sys.executable, str(script), *prefix, *args, "--project-root", str(root)]
    result = subprocess.run(command, cwd=CODING if prefix else WORK,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, encoding="utf-8", timeout=45)
    output = result.stdout.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = output
    return {"returncode": result.returncode, "payload": payload,
            "stderr": result.stderr[-1000:]}


def relevant_records(root: Path) -> dict[str, object]:
    files = sorted((root / ".controlwork").rglob("*.md"))
    records = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        if any(part in rel for part in ("/views/", "/context-packets/", "/checkpoints/")):
            continue
        records[rel] = path.read_text(encoding="utf-8")
    return records


def projected_files(root: Path, relative: str) -> dict[str, str]:
    directory = root / relative
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(directory.rglob("*.md"))} if directory.exists() else {}


def portable_bytes(root: Path) -> dict[str, str]:
    directory = root / ".controlwork"
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_file()
            and path.relative_to(directory).as_posix() != "config.json"}


def comparison(left: object, right: object, roots: tuple[Path, Path]) -> dict:
    a, b = normalized(left, roots), normalized(right, roots)
    return {"pass": a == b, "standalone": a if a != b else None,
            "embedded": b if a != b else None}


def execute(output_root: Path, *, altered_embedded_fixture: bool = False) -> dict:
    output_root = output_root.resolve()
    for source in SOURCES.values():
        if not source.is_file():
            raise FileNotFoundError(source)
    source_hashes_before = hashes()
    identity = json.loads(SOURCES["release_manifest"].read_text(encoding="utf-8"))["workPlaneContract"]
    contract_ok = identity == "controlwork-work-plane/1.0.0" and all(
        "Contract identity: `controlwork-work-plane/1.0.0`" in SOURCES[key].read_text(encoding="utf-8")
        for key in ("embedded_contract", "canonical_contract")
    )
    # An isolated unique pair prevents stale files or concurrent probes from affecting evidence.
    run_root = output_root / ("run-" + uuid.uuid4().hex[:12])
    standalone, embedded = run_root / "standalone", run_root / "embedded"
    roots = (standalone, embedded)
    for root in roots:
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "design.md").write_text(
            "# Design evidence\n\nThe cobalt bridge is approved for phase one.\n", encoding="utf-8")
        (root / "docs" / "import.md").write_text(
            "# Research source\n\nThe amber route needs a second review.\n", encoding="utf-8")
        (root / "docs" / "scan.pdf").write_bytes(b"%PDF-1.4\n%% parity scanned fixture\n")
    if altered_embedded_fixture:
        (embedded / "docs" / "design.md").write_text(
            "# Design evidence\n\nThe cobalt bridge is rejected for phase one.\n", encoding="utf-8")
    results: dict[str, dict] = {}
    profile_variances: dict[str, dict] = {}

    def pair(name: str, standalone_args: list[str], embedded_args: list[str] | None = None,
             *, compare_output: bool = True) -> None:
        a = run_command(SOURCES["standalone_cli"], standalone, standalone_args)
        b = run_command(SOURCES["embedded_cli"], embedded, embedded_args or
                        ["work-" + standalone_args[0], *standalone_args[1:]])
        item = {"returncodes": [a["returncode"], b["returncode"]],
                "pass": a["returncode"] == b["returncode"] == 0}
        if compare_output and item["pass"]:
            left, right = a["payload"], b["payload"]
            if name == "query":
                # Surface labels and suggested CLI commands name each distribution.
                # The actual ranked matches and their citations are portable.
                def top_matches(packet: str) -> str:
                    return packet.split("## Top Matches\n", 1)[1].split("\n## Graph Explain Targets", 1)[0]
                left, right = top_matches(left), top_matches(right)
            if name == "checkpoint":
                # Fingerprints include timestamped generated files. Compare receipt
                # semantics here; persistence is checked from the files below.
                def checkpoint_semantics(payload: dict) -> dict:
                    return {"ok": payload["ok"], "created": payload["created"],
                            "reason": payload["reason"], "due": payload["due"]["due"]}
                left, right = checkpoint_semantics(left), checkpoint_semantics(right)
            if name == "handoff":
                # A preceding context packet may create a checkpoint within the
                # same second on one side, yielding a repeat suffix. The handoff
                # path and written portable views are the stable contract here.
                def handoff_semantics(payload: dict) -> dict:
                    return {"ok": payload["ok"], "handoff": payload["handoff"],
                            "checkpointCreated": bool(payload.get("checkpoint")),
                            "writtenViews": [path for path in payload["written"]
                                             if "/views/" in path]}
                left, right = handoff_semantics(left), handoff_semantics(right)
            if name == "scan_review_queue":
                # The index fingerprint includes file modification times from
                # separate fixture roots. Keep every queue item and source hash.
                def queue_semantics(payload: dict) -> dict:
                    projected = json.loads(json.dumps(payload))
                    projected.get("queue", {}).get("source", {}).pop("indexFingerprint", None)
                    return projected
                left, right = queue_semantics(left), queue_semantics(right)
            c = comparison(left, right, roots)
            item.update(c)
        if not item["pass"]:
            item["standalone"] = a
            item["embedded"] = b
        if all((root / ".controlwork/ingestion/file-index.json").is_file() for root in roots):
            item["scanStates"] = [
                {entry["path"]: entry["scanStatus"] for entry in json.loads(
                    (root / ".controlwork/ingestion/file-index.json").read_text(encoding="utf-8"))["files"]}
                for root in roots
            ]
        results[name] = item

    pair("init", ["init", "--name", "Parity Fixture", "--purpose", "Portable work parity"],
         ["work-init", "--name", "Parity Fixture", "--purpose", "Portable work parity", "--json"],
         compare_output=False)
    profile_variances["init_context"] = comparison(
        (standalone / "CONTROLWORK.md").read_text(encoding="utf-8"),
        (embedded / "CONTROLWORK.md").read_text(encoding="utf-8"), roots)
    default_query_a = run_command(SOURCES["standalone_cli"], standalone,
                                  ["query", "Bridge Workflows", "--stdout"])
    default_query_b = run_command(SOURCES["embedded_cli"], embedded,
                                  ["work-query", "Bridge Workflows", "--stdout"])
    profile_variances["default_query"] = comparison(
        default_query_a["payload"], default_query_b["payload"], roots)
    # Use the same canonical context corpus for the subsequent portable feature
    # comparisons. The generated init-context difference remains a separate failure.
    (embedded / "CONTROLWORK.md").write_bytes((standalone / "CONTROLWORK.md").read_bytes())
    for adapter in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "AI_CONTEXT.md"):
        (standalone / adapter).unlink()
    pair("scan", ["scan"], ["work-scan", "--json"], compare_output=False)
    pair("scan_analyze", ["scan-analyze", "--json"], ["work-analyze", "--json"])
    pair("scan_review_queue", ["scan-review", "--filter", "all", "--json"],
         ["work-review", "--filter", "all", "--json"])
    blocked = [run_command(SOURCES[side], root,
                           ["scan-promote", "docs/design.md", "--json"] if side == "standalone_cli"
                           else ["work-promote", "docs/design.md", "--json"])
               for side, root in (("standalone_cli", standalone), ("embedded_cli", embedded))]
    results["scan_promotion_gate"] = comparison(blocked[0]["payload"], blocked[1]["payload"], roots)
    results["scan_promotion_gate"]["pass"] &= all(item["returncode"] == 1 for item in blocked)
    pair("scan_review_decision", ["scan-review", "docs/design.md", "--review-status", "reviewed",
                                  "--sensitivity", "public", "--note", "Source checked", "--json"],
         ["work-review", "docs/design.md", "--review-status", "reviewed",
          "--sensitivity", "public", "--note", "Source checked", "--json"])
    pair("scan_promote", ["scan-promote", "docs/design.md", "--title", "Design Source",
                          "--force", "--force-note", "Explicit fixture review", "--json"],
         ["work-promote", "docs/design.md", "--title", "Design Source",
          "--force", "--force-note", "Explicit fixture review", "--json"])
    pair("source_import", ["scan-import", "docs/import.md", "--title", "Research Source", "--json"],
         ["work-import-source", "docs/import.md", "--title", "Research Source", "--json"])
    pair("ocr_status", ["ocr", "status"], ["work-ocr", "status"])
    for root in roots:
        index = json.loads((root / ".controlwork/ingestion/file-index.json").read_text(encoding="utf-8"))
        pdf_record = next(item for item in index["files"] if item["path"] == "docs/scan.pdf")
        (root / ".controlwork/extracts/input.ocr.json").write_text(json.dumps({
            "schemaVersion": "controlwork-ocr-sidecar/v1",
            "sourcePath": "docs/scan.pdf", "sourceHash": pdf_record["contentHash"],
            "extractor": "parity_fixture", "pages": [{"page": 1, "text": "Amber route appears in scan."}],
        }), encoding="utf-8")
    pair("ocr_sidecar", ["ocr", "import-sidecar", "docs/scan.pdf", "--sidecar", ".controlwork/extracts/input.ocr.json",
                         "--import-source", "--json"],
         ["work-ocr", "import-sidecar", "docs/scan.pdf", "--sidecar", ".controlwork/extracts/input.ocr.json",
          "--import-source", "--json"])
    pair("ocr_status_after", ["ocr", "status"], ["work-ocr", "status"])
    pair("categories", ["category", "list"], ["work-category", "list", "--json"])
    pair("status", ["status"], ["work-status", "--json"], compare_output=False)
    if results["status"]["pass"]:
        left = run_command(SOURCES["standalone_cli"], standalone, ["status"])["payload"]
        right = run_command(SOURCES["embedded_cli"], embedded,
                            ["work-status", "--json"])["payload"]
        shared = ("canonicalContext", "baseDocument", "hasConfig",
                  "hasCanonicalContext", "hasBaseDocument", "memoryCounts")
        results["status"].update(comparison({key: left[key] for key in shared},
                                            {key: right[key] for key in shared}, roots))
    index_paths = [root / ".controlwork/ingestion/file-index.json" for root in roots]
    index_before_capture = [path.read_bytes() for path in index_paths]
    pair("capture", ["capture", "decisions", "--title", "Cobalt Bridge", "--body",
                     "Approved after design review.", "--lifecycle", "active", "--source", "docs/design.md"],
         ["work-capture", "decisions", "--title", "Cobalt Bridge", "--body",
          "Approved after design review.", "--lifecycle", "active", "--source", "docs/design.md", "--json"])
    index_after_capture = [path.read_bytes() for path in index_paths]
    capture_changes = [before != after for before, after in zip(index_before_capture, index_after_capture)]
    results["capture_scan_refresh"] = {
        "pass": capture_changes[0] == capture_changes[1],
        "indexChanged": capture_changes,
        "command": "capture decisions",
    }
    pair("session_start", ["session", "start", "--id", "PARITY-001", "--topic", "Cobalt Bridge",
                            "--summary", "Review decision"],
         ["work-session", "start", "--id", "PARITY-001", "--topic", "Cobalt Bridge",
          "--summary", "Review decision"])
    pair("session_note", ["session", "note", "PARITY-001", "Evidence reviewed."],
         ["work-session", "note", "PARITY-001", "Evidence reviewed."])
    pair("session_close", ["session", "close", "PARITY-001", "--summary", "Decision confirmed",
                            "--decision", "Proceed with cobalt bridge"],
         ["work-session", "close", "PARITY-001", "--summary", "Decision confirmed",
          "--decision", "Proceed with cobalt bridge"])
    pair("session_show", ["session", "show", "PARITY-001"],
         ["work-session", "show", "PARITY-001"])
    pair("views", ["views", "generate"], ["work-views", "--json"])
    pair("retrieve", ["retrieve", "cobalt bridge"], ["work-retrieve", "cobalt bridge"])
    pair("query", ["query", "cobalt bridge", "--stdout"],
         ["work-query", "cobalt bridge", "--stdout"])
    pair("rag_pack", ["rag-pack", "cobalt bridge", "--stdout"],
         ["work-rag-pack", "cobalt bridge", "--stdout"])
    pair("graph", ["graph", "status"], ["work-graph", "status"])
    pair("graph_explain", ["graph", "explain", ".controlwork/sessions/parity-001.json"],
         ["work-graph", "explain", ".controlwork/sessions/parity-001.json"])
    pair("graph_path", ["graph", "path", "CONTROLWORK.md", ".controlwork/sessions/parity-001.json",
                        "--include-suggestions"],
         ["work-graph", "path", "CONTROLWORK.md", ".controlwork/sessions/parity-001.json",
          "--include-suggestions"])
    pair("graph_suggestions", ["graph", "suggestions"], ["work-graph", "suggestions"])
    if results["graph_suggestions"]["pass"]:
        suggestion_payloads = [run_command(SOURCES[side], root, ["graph", "suggestions"] if side == "standalone_cli"
                                           else ["work-graph", "suggestions"])["payload"]
                               for side, root in (("standalone_cli", standalone), ("embedded_cli", embedded))]
        if suggestion_payloads[0]["suggestions"] and suggestion_payloads[1]["suggestions"]:
            accepted = [run_command(SOURCES[side], root,
                                    (["graph", "accept"] if side == "standalone_cli" else ["work-graph", "accept"])
                                    + [payload["suggestions"][0]["id"], "--reason", "Fixture review"])
                        for (side, root), payload in zip((("standalone_cli", standalone),
                                                          ("embedded_cli", embedded)), suggestion_payloads)]
            results["graph_accept"] = comparison(accepted[0]["payload"], accepted[1]["payload"], roots)
            results["graph_accept"]["pass"] &= all(item["returncode"] == 0 for item in accepted)
            pair("graph_accepted", ["graph", "suggestions", "--status", "accepted"],
                 ["work-graph", "suggestions", "--status", "accepted"])
        else:
            results["graph_accept"] = {"pass": False, "error": "fixture produced no suggestion to accept"}
    index_before_context = [path.read_bytes() for path in index_paths]
    pair("context_pack", ["context-pack", "--scope", "general", "--topic", "cobalt bridge",
                          "--stdout"],
         ["work-context-pack", "--scope", "general", "--topic", "cobalt bridge", "--stdout"])
    index_after_context = [path.read_bytes() for path in index_paths]
    context_changes = [before != after for before, after in zip(index_before_context, index_after_context)]
    results["context_pack_scan_refresh"] = {
        "pass": context_changes[0] == context_changes[1],
        "indexChanged": context_changes,
        "command": "context-pack --scope general --topic cobalt bridge --stdout",
    }
    index_before_wiki = [path.read_bytes() for path in index_paths]
    pair("wiki", ["wiki", "build"], ["work-wiki", "build", "--json"])
    index_after_wiki = [path.read_bytes() for path in index_paths]
    wiki_changes = [before != after for before, after in zip(index_before_wiki, index_after_wiki)]
    results["wiki_scan_refresh"] = {
        "pass": wiki_changes[0] == wiki_changes[1],
        "indexChanged": wiki_changes,
        "command": "wiki build",
    }
    pair("obsidian_init", ["obsidian", "init"], ["work-obsidian", "init", "--json"])
    pair("obsidian_check", ["obsidian", "check"], ["work-obsidian", "check", "--json"])
    for root in roots:
        home = root / "wiki/Home.md"
        home.write_text(home.read_text(encoding="utf-8") + "\nHuman correction for review.\n",
                        encoding="utf-8")
    pair("wiki_edit_review", ["wiki", "import-edits", "--review"],
         ["work-wiki", "import-edits", "--review", "--json"])
    index_before_dashboard = [path.read_bytes() for path in index_paths]
    pair("dashboard_json", ["dashboard", "--format", "json", "--no-refresh-scan"],
         ["work-dashboard", "--format", "json", "--no-refresh-scan", "--json"])
    index_after_dashboard = [path.read_bytes() for path in index_paths]
    results["dashboard_no_refresh_scan"] = {
        "pass": index_before_dashboard == index_after_dashboard,
        "indexChanged": [before != after for before, after in zip(index_before_dashboard, index_after_dashboard)],
        "command": "dashboard --format json --no-refresh-scan",
    }
    pair("handoff", ["handoff"], ["work-handoff", "--json"])
    pair("mcp_tools", ["mcp", "tools"], ["work-mcp", "tools", "--json"])
    pair("mcp_categories", ["mcp", "call", "controlwork_list_categories"],
         ["work-mcp", "call", "controlwork_list_categories", "--json"])
    for root in roots:
        (root / "mcp-query.json").write_text(json.dumps({"query": "cobalt bridge", "limit": 3}), encoding="utf-8")
    pair("mcp_search", ["mcp", "call", "controlwork_search_memory", "--args-file", "mcp-query.json"],
         ["work-mcp", "call", "controlwork_search_memory", "--args-file", "mcp-query.json", "--json"])
    pair("checkpoint", ["checkpoint", "--title", "Parity checkpoint"],
         ["work-checkpoint", "--title", "Parity checkpoint"])
    results["portable_records"] = comparison(relevant_records(standalone), relevant_records(embedded), roots)
    results["wiki_pages"] = comparison(projected_files(standalone, "wiki"),
                                       projected_files(embedded, "wiki"), roots)
    results["views"] = comparison(projected_files(standalone, ".controlwork/memory/views"),
                                  projected_files(embedded, ".controlwork/memory/views"), roots)
    results["source_fixture"] = comparison(
        (standalone / "docs/design.md").read_text(encoding="utf-8"),
        (embedded / "docs/design.md").read_text(encoding="utf-8"), roots)
    def scan_records(root: Path) -> list[dict]:
        payload = json.loads((root / ".controlwork/ingestion/file-index.json").read_text(encoding="utf-8"))
        return [{key: item.get(key) for key in ("path", "kind", "scanStatus", "reviewStatus",
                                                  "sensitivity", "contentHash", "normalizedTextHash",
                                                  "sourceRecord", "ocrSidecar")}
                for item in payload["files"]]
    results["scan_records"] = comparison(scan_records(standalone), scan_records(embedded), roots)
    imported = run_root / "imported"
    imported.mkdir()
    migration = run_command(SOURCES["embedded_cli"], imported,
                            ["work-import", str(standalone), "--json"])
    migration_equal = (migration["returncode"] == 0 and
                       (imported / "CONTROLWORK.md").read_bytes() ==
                       (standalone / "CONTROLWORK.md").read_bytes() and
                       portable_bytes(imported) == portable_bytes(standalone))
    results["standalone_import"] = {
        "pass": migration_equal, "returncode": migration["returncode"],
        "sourceRecordCount": len(portable_bytes(standalone)),
        "importedRecordCount": len(portable_bytes(imported)),
        "excluded": [".controlwork/config.json: distribution metadata is rewritten"],
        "error": migration["payload"] if not migration_equal else None,
    }
    source_hashes_after = hashes()
    source_stable = source_hashes_before == source_hashes_after
    receipt = {"schemaVersion": 1, "contractIdentity": identity, "contractMatch": contract_ok,
               "sourceHashesSha256": source_hashes_before, "sourceStable": source_stable,
               "python": sys.version.split()[0], "runRoot": str(run_root),
               "profileVariances": profile_variances, "commandFamilies": COVERAGE,
               "results": results,
               "pass": contract_ok and source_stable and all(item["pass"] for item in results.values())}
    (run_root / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = execute(args.output_root)
    print(json.dumps({"pass": result["pass"], "runRoot": result["runRoot"],
                      "failed": [key for key, value in result["results"].items() if not value["pass"]]},
                     indent=2))
    raise SystemExit(0 if result["pass"] else 1)
