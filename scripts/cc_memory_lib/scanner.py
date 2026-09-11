"""Project file scanner and path classifier for project memory."""

from __future__ import annotations

import os
import re
import zlib
from pathlib import Path

from .schema import HOST_CONTEXT_FILES, SKIP_DIRS, TEXT_EXTENSIONS


PDF_MAX_BYTES = 8 * 1024 * 1024
GOVERNED_ROOT_FILES = {
    ".clinerules",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTROLCODING.md",
    "CONTROLWORK.md",
    "GEMINI.md",
}
GOVERNED_ROOT_DIRS = (
    "project-definition",
    "design",
    "criteria",
    "contracts",
    "dev/project-definition",
    "dev/design",
    "dev/criteria",
    "dev/contracts",
    ".controlwork/memory",
)


def _looks_like_pdf(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    try:
        if path.stat().st_size > PDF_MAX_BYTES:
            return False
        return path.read_bytes()[:5] == b"%PDF-"
    except OSError:
        return False


def _decode_pdf_literal_string(value: bytes) -> str:
    output = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char != 0x5C:
            output.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        index += 1
        escapes = {
            ord("n"): ord("\n"),
            ord("r"): ord("\r"),
            ord("t"): ord("\t"),
            ord("b"): ord("\b"),
            ord("f"): ord("\f"),
            ord("("): ord("("),
            ord(")"): ord(")"),
            ord("\\"): ord("\\"),
        }
        if escaped in escapes:
            output.append(escapes[escaped])
            continue
        if escaped in b"01234567":
            octal = bytes([escaped])
            while index < len(value) and len(octal) < 3 and value[index] in b"01234567":
                octal += bytes([value[index]])
                index += 1
            try:
                output.append(int(octal, 8))
            except ValueError:
                pass
            continue
        if escaped in (ord("\r"), ord("\n")):
            if escaped == ord("\r") and index < len(value) and value[index] == ord("\n"):
                index += 1
            continue
        output.append(escaped)
    raw = bytes(output)
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be", errors="replace")
        except UnicodeDecodeError:
            return ""
    return raw.decode("latin-1", errors="replace")


def _decode_pdf_hex_string(value: bytes) -> str:
    cleaned = re.sub(rb"\s+", b"", value)
    if len(cleaned) % 2:
        cleaned += b"0"
    try:
        raw = bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return ""
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    return raw.decode("latin-1", errors="replace")


def _pdf_text_tokens(data: bytes) -> list[str]:
    tokens: list[str] = []
    string_pattern = rb"\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]+)>"
    for match in re.finditer(rb"\[(.*?)\]\s*TJ", data, flags=re.DOTALL):
        array_content = match.group(1)
        parts: list[str] = []
        for token in re.finditer(string_pattern, array_content, flags=re.DOTALL):
            raw = token.group(0)
            if raw.startswith(b"("):
                parts.append(_decode_pdf_literal_string(raw[1:-1]))
            elif raw.startswith(b"<") and not raw.startswith(b"<<"):
                parts.append(_decode_pdf_hex_string(raw[1:-1]))
        joined = "".join(parts).strip()
        if joined:
            tokens.append(joined)
    for match in re.finditer(rb"(\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]+)>)\s*(?:Tj|'|\")", data, flags=re.DOTALL):
        raw = match.group(1)
        if raw.startswith(b"("):
            text = _decode_pdf_literal_string(raw[1:-1]).strip()
        else:
            text = _decode_pdf_hex_string(raw[1:-1]).strip()
        if text:
            tokens.append(text)
    return tokens


def _pdf_streams(data: bytes) -> list[bytes]:
    streams: list[bytes] = []
    for match in re.finditer(rb"<<(?P<dict>.*?)>>\s*stream\r?\n(?P<stream>.*?)\r?\nendstream", data, flags=re.DOTALL):
        stream_data = match.group("stream")
        dictionary = match.group("dict")
        if b"/FlateDecode" in dictionary:
            try:
                stream_data = zlib.decompress(stream_data)
            except zlib.error:
                continue
        streams.append(stream_data)
    return streams


def _safe_read_pdf_text(path: Path, limit: int = 512 * 1024) -> str:
    try:
        data = path.read_bytes()[:PDF_MAX_BYTES]
    except OSError:
        return ""
    if not data.startswith(b"%PDF-"):
        return ""
    pages: list[str] = []
    for stream in _pdf_streams(data):
        tokens = _pdf_text_tokens(stream)
        if tokens:
            page_text = "\n".join(tokens).strip()
            if page_text:
                pages.append(page_text)
        if sum(len(page) for page in pages) >= limit:
            break
    if not pages:
        return ""
    title = path.stem.replace("_", " ").replace("-", " ").strip() or path.name
    lines = [f"# {title}"]
    for page_index, page_text in enumerate(pages, start=1):
        lines.extend(["", f"## Page {page_index}", "", page_text[:limit]])
    return "\n".join(lines)[:limit]


def _safe_read_head(path: Path, limit: int = 8192) -> str:
    if _looks_like_pdf(path):
        return _safe_read_pdf_text(path, limit=limit)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return handle.read(limit)
    except (OSError, UnicodeDecodeError):
        return ""

def _safe_read_text_with_metadata(path: Path, limit: int = 512 * 1024) -> tuple[str, dict[str, object]]:
    if _looks_like_pdf(path):
        return _safe_read_pdf_text(path, limit=limit), {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return handle.read(limit), {}
    except OSError:
        return "", {}
    except UnicodeDecodeError:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(limit)
        except OSError:
            return "", {}
        return text, {
            "encoding": "utf-8",
            "errors": "replace",
            "fallback_used": True,
            "replacement_character_present": "\ufffd" in text,
        }


def _safe_read_text(path: Path, limit: int = 512 * 1024) -> str:
    text, _metadata = _safe_read_text_with_metadata(path, limit=limit)
    return text

def _title_from_file(path: Path) -> str:
    text = _safe_read_head(path)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:160]
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name

def _is_text_file(path: Path) -> bool:
    if path.name in HOST_CONTEXT_FILES:
        return True
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    if _looks_like_pdf(path):
        return True
    if suffix == "" and path.stat().st_size <= 128 * 1024:
        head = path.read_bytes()[:4096]
        return b"\x00" not in head
    return False

def _is_governed_relpath(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    if normalized in GOVERNED_ROOT_FILES:
        return True
    for root in GOVERNED_ROOT_DIRS:
        if normalized == root or normalized.startswith(f"{root}/"):
            return True
    return False


def _iter_governed_scannable_files(project: Path) -> list[Path]:
    files: list[Path] = []
    project_root = project.resolve()

    def maybe_add(path: Path) -> None:
        try:
            rel = path.resolve().relative_to(project_root)
        except ValueError:
            return
        rel_label = rel.as_posix()
        if path.name == ".gitkeep" or not _is_governed_relpath(rel_label):
            return
        try:
            if path.is_file() and _is_text_file(path):
                files.append(path)
        except OSError:
            return

    for relative in GOVERNED_ROOT_FILES:
        maybe_add(project / relative)

    for relative in GOVERNED_ROOT_DIRS:
        root = project / relative
        if not root.exists() or not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                rel = path.resolve().relative_to(project_root)
            except ValueError:
                continue
            if any(part in SKIP_DIRS and part != ".controlwork" for part in rel.parts):
                continue
            maybe_add(path)

    return sorted({path.resolve(): path for path in files}.values(), key=lambda item: item.as_posix().lower())


def _iter_scannable_files(project: Path, scope: str = "full") -> list[Path]:
    if scope == "governed":
        return _iter_governed_scannable_files(project)
    files: list[Path] = []
    for root, dirs, names in os.walk(project):
        root_path = Path(root)
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in names:
            path = root_path / name
            try:
                rel = path.resolve().relative_to(project.resolve())
            except ValueError:
                continue
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            try:
                if path.is_file() and _is_text_file(path):
                    files.append(path)
            except OSError:
                continue
    return sorted(files, key=lambda item: item.as_posix().lower())

def _infer_lifecycle(rel_path: str) -> str:
    parts = {part.lower() for part in Path(rel_path).parts}
    if parts & {"deprecated", "legacy", "old"}:
        return "stale"
    if parts & {"archive", "archived"}:
        return "archived"
    return "active"

def _area_from_relpath(rel_path: str) -> str:
    parts = [part for part in Path(rel_path).parts if part and part != "."]
    if not parts:
        return "Root"
    if len(parts) == 1:
        return "Root"
    return parts[0]

def _classify_path(rel_path: str) -> tuple[str, str, str]:
    path = Path(rel_path)
    parts_lower = [part.lower() for part in path.parts]
    suffix = path.suffix.lower()
    lifecycle = _infer_lifecycle(rel_path)
    app_memory_markers = {
        "app_memory",
        "corpus",
        "domain_memory",
        "embeddings",
        "knowledge_graph",
        "rag",
        "runtime_memory",
        "vector_store",
        "vectors",
    }

    if path.name in HOST_CONTEXT_FILES:
        return "doc_node", "host_projection", lifecycle
    if set(parts_lower) & app_memory_markers:
        return "application_memory_component", "application_memory", lifecycle
    if "plans" in parts_lower and suffix == ".md":
        return "plan", "controlcoding_dev", lifecycle
    if "decisions" in parts_lower and suffix == ".md":
        return "decision", "controlcoding_dev", lifecycle
    if "research" in parts_lower and suffix == ".md":
        return "research_note", "controlcoding_dev", lifecycle
    if "handoffs" in parts_lower and suffix == ".md":
        return "handoff", "controlcoding_dev", lifecycle
    if path.parts and path.parts[0].lower() in {"tests", "test"}:
        return "test_node", "project_content", lifecycle
    if path.name.startswith("test_") and suffix == ".py":
        return "test_node", "project_content", lifecycle
    if path.parts and path.parts[0].lower() == "benchmarks":
        return "benchmark", "project_content", lifecycle
    if suffix in {".md", ".rst", ".txt", ".adoc", ".pdf"}:
        plane = "document_workspace" if path.parts and path.parts[0].lower() == "workdocs" else "project_content"
        return "doc_node", plane, lifecycle
    return "file_node", "project_content", lifecycle
