"""Semantic chunking for the Project Memory Engine."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .classifier import _keyword_list
from .store import (
    _content_hash_text,
    _ensure_schema,
    _json_dumps,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _relative_path,
    _require_initialized,
)

MAX_CHUNK_CHARS = 2800
LAYOUT_EXTRACTION_SIDECAR_SUFFIXES = (
    ".layout.json",
    ".ocr.json",
    ".pdf.layout.json",
    ".pdf.ocr.json",
)
DEEP_LAYOUT_NODE_TYPES = {
    "annotation",
    "caption",
    "figure",
    "footnote",
    "image",
    "margin_note",
    "paragraph",
    "schema",
    "table",
    "subtitle",
    "title",
}
DEEP_LAYOUT_BLOCK_ALIASES = {
    "fig": "figure",
    "footer_note": "footnote",
    "image_caption": "caption",
    "line": "paragraph",
    "margin-note": "margin_note",
    "marginnote": "margin_note",
    "note": "annotation",
    "ocr_line": "paragraph",
    "picture": "image",
    "section_heading": "subtitle",
    "text": "paragraph",
}
PAGE_LAYOUT_COLLECTIONS = (
    ("blocks", ""),
    ("items", ""),
    ("lines", "paragraph"),
    ("paragraphs", "paragraph"),
    ("tables", "table"),
    ("figures", "figure"),
    ("images", "image"),
    ("captions", "caption"),
    ("footnotes", "footnote"),
    ("annotations", "annotation"),
    ("notes", "annotation"),
    ("marginNotes", "margin_note"),
    ("margin_notes", "margin_note"),
)


def _chunk_id(document_id: str, ordinal: int, sequence_index: int, content: str) -> str:
    seed = f"{document_id}:{ordinal}:{sequence_index}:{_content_hash_text(content)}"
    return "CHUNK_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    seed = f"{source_id}:{target_id}:{edge_type}"
    return "EDGE_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _layout_node_id(document_id: str, node_type: str, ordinal: int, sequence_index: int, content: str) -> str:
    seed = f"{document_id}:{node_type}:{ordinal}:{sequence_index}:{_content_hash_text(content)}"
    return "LAYOUT_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _layout_edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    seed = f"{source_id}:{target_id}:{edge_type}"
    return "LAYOUT_EDGE_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _heading_title(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
    if not match:
        return None
    title = match.group(2).strip().strip("#").strip()
    if not title:
        return None
    return len(match.group(1)), title


def _split_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    stack: list[str] = []
    current_lines: list[str] = []
    current_heading_path = "Document"
    current_heading_level = 0
    current_heading_title = "Document"
    in_fence = False

    def flush() -> None:
        nonlocal current_lines
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(
                {
                    "heading_path": current_heading_path,
                    "heading_level": current_heading_level,
                    "heading_title": current_heading_title,
                    "content": content,
                }
            )
        current_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        fence_start = stripped.startswith("```") or stripped.startswith("~~~")
        heading = None if in_fence else _heading_title(line)
        if heading:
            flush()
            level, title = heading
            stack[:] = stack[: level - 1]
            stack.append(title)
            current_heading_path = " > ".join(stack)
            current_heading_level = level
            current_heading_title = title
        current_lines.append(line)
        if fence_start:
            in_fence = not in_fence
    flush()

    if sections:
        return sections
    stripped = text.strip()
    return [
        {
            "heading_path": "Document",
            "heading_level": 0,
            "heading_title": "Document",
            "content": stripped,
        }
    ] if stripped else []


def _split_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in content.splitlines():
        stripped = line.strip()
        fence_start = stripped.startswith("```") or stripped.startswith("~~~")
        if not in_fence and not stripped:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
        if fence_start:
            in_fence = not in_fence
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _continuation_parts(content: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    def safe_split_index(value: str) -> int:
        window_size = min(400, max(1, max_chars // 5))
        start = max(1, max_chars - window_size)
        end = min(max_chars, len(value))
        if end <= start:
            return max_chars

        double_newline = value.rfind("\n\n", start, end)
        if double_newline != -1:
            return double_newline + 2

        newline = value.rfind("\n", start, end)
        if newline != -1:
            return newline + 1

        sentence_match = None
        for match in re.finditer(r"[.!?](?=\s|$)", value[start:end]):
            sentence_match = match
        if sentence_match:
            return start + sentence_match.end()

        for index in range(end - 1, start - 1, -1):
            if value[index].isspace():
                return index + 1

        return max_chars

    def split_long_block(block: str) -> list[str]:
        pieces: list[str] = []
        remaining = block
        while len(remaining) > max_chars:
            split_at = safe_split_index(remaining)
            if split_at <= 0:
                split_at = max_chars
            piece = remaining[:split_at].strip()
            if piece:
                pieces.append(piece)
            remaining = remaining[split_at:].lstrip()
        if remaining.strip():
            pieces.append(remaining.strip())
        return pieces

    blocks = _split_blocks(content)
    if not blocks:
        return split_long_block(content)
    parts: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > max_chars:
            if current:
                parts.append(current.strip())
                current = ""
            parts.extend(split_long_block(block))
            continue
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) > max_chars and current:
            parts.append(current.strip())
            current = block
        else:
            current = candidate
    if current:
        parts.append(current.strip())
    return parts


def _summary_for_chunk(content: str, heading_path: str) -> str:
    for line in content.splitlines():
        stripped = line.strip().strip("#").strip()
        if stripped and not stripped.startswith("```") and not stripped.startswith("|"):
            return stripped[:220]
    return heading_path[:220]


def _has_table(content: str) -> bool:
    table_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("|") and line.strip().count("|") >= 2
    ]
    return len(table_lines) >= 2


def _has_schema(content: str) -> bool:
    lower = content.lower()
    schema_markers = (
        "```mermaid",
        "```json",
        "```yaml",
        "```yml",
        "classdiagram",
        "erdiagram",
        "flowchart",
        "sequenceDiagram".lower(),
        "schema",
        "interface",
        "data model",
    )
    return any(marker in lower for marker in schema_markers)


def _has_annotation(content: str) -> bool:
    lower = content.lower()
    return any(
        marker in lower
        for marker in (
            "> [!note]",
            "> [!warning]",
            "annotation:",
            "comment:",
            "review note:",
        )
    )


def _page_marker(content: str) -> str:
    match = re.search(r"\bpage\s+(\d+)\b", content, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _is_layout_extraction_sidecar(source_path: str) -> bool:
    name = Path(source_path).name.lower()
    return any(name.endswith(suffix) for suffix in LAYOUT_EXTRACTION_SIDECAR_SUFFIXES)


def _first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _canonical_deep_layout_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    raw = DEEP_LAYOUT_BLOCK_ALIASES.get(raw, raw)
    return raw if raw in DEEP_LAYOUT_NODE_TYPES else "paragraph"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_bbox(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        x0 = _float_or_none(value.get("x0", value.get("left", value.get("x"))))
        y0 = _float_or_none(value.get("y0", value.get("top", value.get("y"))))
        x1 = _float_or_none(value.get("x1", value.get("right")))
        y1 = _float_or_none(value.get("y1", value.get("bottom")))
        width = _float_or_none(value.get("width", value.get("w")))
        height = _float_or_none(value.get("height", value.get("h")))
        if x1 is None and x0 is not None and width is not None:
            x1 = x0 + width
        if y1 is None and y0 is not None and height is not None:
            y1 = y0 + height
    elif isinstance(value, (list, tuple)) and len(value) >= 4:
        x0 = _float_or_none(value[0])
        y0 = _float_or_none(value[1])
        x1 = _float_or_none(value[2])
        y1 = _float_or_none(value[3])
    else:
        return {}
    if x0 is None or y0 is None or x1 is None or y1 is None:
        return {}
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _normalized_bbox(bbox: dict[str, float], page_width: float | None, page_height: float | None) -> dict[str, float]:
    if not bbox or not page_width or not page_height:
        return {}
    if page_width <= 0 or page_height <= 0:
        return {}
    return {
        "x0": round(bbox["x0"] / page_width, 6),
        "y0": round(bbox["y0"] / page_height, 6),
        "x1": round(bbox["x1"] / page_width, 6),
        "y1": round(bbox["y1"] / page_height, 6),
    }


def _page_number(page: dict[str, Any], fallback: int) -> str:
    value = page.get("page", page.get("page_number", page.get("pageNumber", page.get("number", fallback))))
    return str(value).strip() or str(fallback)


def _page_size(page: dict[str, Any]) -> tuple[float | None, float | None]:
    width = _float_or_none(page.get("width"))
    height = _float_or_none(page.get("height"))
    size = page.get("size")
    if isinstance(size, dict):
        width = width if width is not None else _float_or_none(size.get("width", size.get("w")))
        height = height if height is not None else _float_or_none(size.get("height", size.get("h")))
    elif isinstance(size, (list, tuple)) and len(size) >= 2:
        width = width if width is not None else _float_or_none(size[0])
        height = height if height is not None else _float_or_none(size[1])
    return width, height


def _sidecar_table_cell_entries(block: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("cells", "tableCells", "table_cells"):
        value = block.get(key)
        if isinstance(value, list) and value:
            return [dict(item) if isinstance(item, dict) else {"text": str(item)} for item in value]
    entries: list[dict[str, Any]] = []
    headers = block.get("headers")
    if isinstance(headers, list):
        for column_index, value in enumerate(headers, start=1):
            entries.append(
                {
                    "text": str(value),
                    "rowIndex": 0,
                    "columnIndex": column_index,
                    "columnHeader": str(value),
                    "isHeader": True,
                }
            )
    rows = block.get("rows")
    if isinstance(rows, list):
        row_offset = 1 if entries else 0
        for row_index, row in enumerate(rows, start=row_offset):
            if isinstance(row, list):
                for column_index, value in enumerate(row, start=1):
                    header = str(headers[column_index - 1]) if isinstance(headers, list) and column_index <= len(headers) else ""
                    entries.append(
                        {
                            "text": str(value),
                            "rowIndex": row_index,
                            "columnIndex": column_index,
                            "columnHeader": header,
                            "isHeader": False,
                        }
                    )
            elif isinstance(row, dict):
                row_cells = row.get("cells")
                if isinstance(row_cells, list):
                    for column_index, value in enumerate(row_cells, start=1):
                        entries.append(
                            {
                                "text": str(value),
                                "rowIndex": _int_or_none(row.get("rowIndex", row.get("row_index"))) or row_index,
                                "columnIndex": column_index,
                                "isHeader": bool(row.get("isHeader", row.get("is_header", False))),
                            }
                        )
    return entries


def _sidecar_schema_field_entries(block: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("fields", "schemaFields", "schema_fields"):
        value = block.get(key)
        if isinstance(value, list) and value:
            return [dict(item) if isinstance(item, dict) else {"name": str(item), "value": ""} for item in value]
    return []


def _sidecar_item_text(item: dict[str, Any]) -> str:
    return _first_string(
        item.get("text"),
        item.get("content"),
        item.get("value"),
        item.get("label"),
        item.get("name"),
        item.get("summary"),
    )


def _block_text(block: dict[str, Any], node_type: str, page: str) -> str:
    text = _first_string(
        block.get("text"),
        block.get("content"),
        block.get("value"),
        block.get("ocr_text"),
        block.get("caption"),
        block.get("alt_text"),
        block.get("summary"),
    )
    if text:
        return text
    if node_type == "table":
        cell_text = [_sidecar_item_text(cell) for cell in _sidecar_table_cell_entries(block)]
        cell_text = [value for value in cell_text if value]
        if cell_text:
            return "\n".join(cell_text)
    if node_type == "schema":
        field_text = [_sidecar_item_text(field) for field in _sidecar_schema_field_entries(block)]
        field_text = [value for value in field_text if value]
        if field_text:
            return "\n".join(field_text)
    return f"[{node_type} block on page {page}]"


def _block_type_value(block: dict[str, Any]) -> Any:
    return block.get(
        "type",
        block.get(
            "node_type",
            block.get("nodeType", block.get("block_type", block.get("kind"))),
        ),
    )


def _sidecar_page_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[tuple[int, float, float, int, dict[str, Any]]] = []
    order = 0
    for key, default_type in PAGE_LAYOUT_COLLECTIONS:
        value = page.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            block = dict(item)
            if default_type and not _block_type_value(block):
                block["type"] = default_type
            bbox = _normalize_bbox(block.get("bbox", block.get("bounds", block.get("box"))))
            sort_group = 0 if bbox else 1
            collected.append((sort_group, bbox.get("y0", 0.0), bbox.get("x0", 0.0), order, block))
            order += 1
    return [item[-1] for item in sorted(collected, key=lambda entry: entry[:4])]


def _sidecar_child_source_location(
    item: dict[str, Any],
    page: str,
    page_width: float | None,
    page_height: float | None,
    coordinate_system: str,
) -> dict[str, Any]:
    bbox = _normalize_bbox(item.get("bbox", item.get("bounds", item.get("box"))))
    return {
        "page": page,
        "line_start": item.get("line_start", item.get("lineStart")),
        "line_end": item.get("line_end", item.get("lineEnd")),
        "bbox": bbox or None,
        "normalized_bbox": _normalized_bbox(bbox, page_width, page_height) or None,
        "page_width": page_width,
        "page_height": page_height,
        "coordinate_system": coordinate_system,
    }


def _sidecar_child_layout_specs(
    node_type: str,
    block: dict[str, Any],
    page: str,
    page_width: float | None,
    page_height: float | None,
    coordinate_system: str,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if node_type == "table":
        for index, cell in enumerate(_sidecar_table_cell_entries(block), start=1):
            text = _sidecar_item_text(cell)
            if not text:
                continue
            row_index = _int_or_none(cell.get("row_index", cell.get("rowIndex", cell.get("row"))))
            column_index = _int_or_none(cell.get("column_index", cell.get("columnIndex", cell.get("column"))))
            title = _first_string(cell.get("id"), cell.get("cellId"), cell.get("name"))
            if not title:
                title = f"R{row_index or '?'}C{column_index or index}"
            specs.append(
                {
                    "node_type": "table_cell",
                    "title": title,
                    "content": text,
                    "metadata": {
                        "layout_block_id": _first_string(cell.get("id"), cell.get("cellId"), cell.get("cell_id")),
                        "row_index": row_index,
                        "column_index": column_index,
                        "row_span": _int_or_none(cell.get("row_span", cell.get("rowSpan"))),
                        "column_span": _int_or_none(cell.get("column_span", cell.get("columnSpan"))),
                        "column_header": _first_string(cell.get("column_header"), cell.get("columnHeader")),
                        "is_header": bool(cell.get("is_header", cell.get("isHeader", False))),
                        "cell_text": text,
                        "source_location": _sidecar_child_source_location(cell, page, page_width, page_height, coordinate_system),
                    },
                }
            )
    elif node_type == "schema":
        for index, field in enumerate(_sidecar_schema_field_entries(block), start=1):
            field_name = _first_string(field.get("field_path"), field.get("fieldPath"), field.get("name"), field.get("key"))
            value_preview = _first_string(field.get("value_preview"), field.get("valuePreview"), field.get("value"), field.get("text"))
            if not field_name and not value_preview:
                continue
            content = f"{field_name}: {value_preview}" if field_name and value_preview else (field_name or value_preview)
            specs.append(
                {
                    "node_type": "schema_field",
                    "title": field_name or f"Field {index}",
                    "content": content,
                    "metadata": {
                        "layout_block_id": _first_string(field.get("id"), field.get("fieldId"), field.get("field_id")),
                        "field_path": field_name,
                        "field_name": _first_string(field.get("name"), field_name.rsplit(".", 1)[-1] if field_name else ""),
                        "value_type": _first_string(field.get("value_type"), field.get("valueType"), type(field.get("value")).__name__),
                        "value_preview": value_preview,
                        "source_location": _sidecar_child_source_location(field, page, page_width, page_height, coordinate_system),
                    },
                }
            )
    return specs


def _parse_layout_extraction_sidecar(source_path: str, text: str) -> list[dict[str, Any]] | None:
    if not _is_layout_extraction_sidecar(source_path):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return []
    title = _first_string(
        payload.get("title"),
        payload.get("document_title"),
        payload.get("documentTitle"),
        Path(source_path).stem,
    )
    source_document_path = _first_string(
        payload.get("source_path"),
        payload.get("sourcePath"),
        payload.get("document_path"),
        payload.get("documentPath"),
        payload.get("pdf_path"),
        payload.get("pdfPath"),
    )
    source_media_type = _first_string(
        payload.get("source_type"),
        payload.get("sourceType"),
        payload.get("media_type"),
    )
    extraction_method = _first_string(
        payload.get("extraction_method"),
        payload.get("extractionMethod"),
        payload.get("method"),
    )
    if not extraction_method:
        extraction_method = "ocr_sidecar" if "ocr" in Path(source_path).name.lower() else "pdf_layout_sidecar"
    coordinate_system = _first_string(
        payload.get("coordinate_system"),
        payload.get("coordinateSystem"),
        "page_coordinates",
    )
    engine = _first_string(payload.get("engine"), payload.get("provider"), payload.get("tool"))
    chunks: list[dict[str, Any]] = []
    ordinal = 0
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_value = _page_number(page, page_index)
        page_width, page_height = _page_size(page)
        blocks = _sidecar_page_blocks(page)
        for block_index, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                continue
            node_type = _canonical_deep_layout_type(_block_type_value(block))
            ordinal += 1
            content = _block_text(block, node_type, page_value)
            heading_path = f"{title} > Page {page_value}"
            heading_title = f"Page {page_value}"
            keywords = _keyword_list(source_path, heading_path, content)
            bbox = _normalize_bbox(block.get("bbox", block.get("bounds", block.get("box"))))
            source_location = {
                "page": page_value,
                "line_start": block.get("line_start", block.get("lineStart")),
                "line_end": block.get("line_end", block.get("lineEnd")),
                "bbox": bbox or None,
                "normalized_bbox": _normalized_bbox(bbox, page_width, page_height) or None,
                "page_width": page_width,
                "page_height": page_height,
                "coordinate_system": coordinate_system,
            }
            structural_types = {"page", node_type}
            if node_type in {"paragraph", "caption", "footnote", "margin_note"}:
                structural_types.add("paragraph")
            target_block_id = _first_string(
                block.get("target_block_id"),
                block.get("targetBlockId"),
                block.get("target_id"),
                block.get("targetId"),
                block.get("for"),
            )
            metadata = _chunk_metadata(
                source_path,
                heading_path,
                2,
                heading_title,
                content,
                keywords,
                False,
            )
            metadata.update(
                {
                    "source_title": title,
                    "source_document_path": source_document_path,
                    "source_media_type": source_media_type or "document",
                    "structural_level": "page",
                    "structural_types": sorted(
                        set(metadata.get("structural_types", [])) | structural_types
                    ),
                    "block_type": node_type,
                    "layout_node_type": node_type,
                    "layout_block_id": _first_string(
                        block.get("id"),
                        block.get("block_id"),
                        block.get("blockId"),
                    ),
                    "layout_block_index": block_index,
                    "target_block_id": target_block_id,
                    "layout_children": _sidecar_child_layout_specs(
                        node_type,
                        block,
                        page_value,
                        page_width,
                        page_height,
                        coordinate_system,
                    ),
                    "context_policy": "include_layout_page",
                    "source_location": source_location,
                    "extraction_confidence": "layout_sidecar",
                    "layout_extraction": {
                        "method": extraction_method,
                        "engine": engine,
                        "sidecar_path": source_path,
                        "schema_version": payload.get("schema_version", payload.get("schemaVersion")),
                    },
                    "ocr_confidence": block.get(
                        "ocr_confidence",
                        block.get("ocrConfidence", block.get("confidence")),
                    ),
                }
            )
            if node_type == "caption":
                metadata["context_policy"] = "include_caption_target"
            elif node_type == "footnote":
                metadata["context_policy"] = "include_footnote_target"
            chunks.append(
                {
                    "heading_path": heading_path,
                    "heading_level": 2,
                    "heading_title": heading_title,
                    "content": content,
                    "ordinal": ordinal,
                    "sequence_index": 1,
                    "metadata": metadata,
                    "keywords": keywords,
                    "summary": _summary_for_chunk(content, heading_path),
                }
            )
    return chunks


def _block_type(content: str) -> str:
    stripped = content.lstrip()
    if _has_table(content):
        return "table"
    if _has_annotation(content):
        return "annotation"
    if _has_schema(content):
        return "schema"
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return "code"
    first_line = stripped.splitlines()[0] if stripped else ""
    if re.match(r"^([-*+]|\d+[.)])\s+", first_line):
        return "list"
    if first_line.startswith(">"):
        return "quote"
    return "prose"


def _structural_level(heading_level: int) -> str:
    if heading_level <= 0:
        return "document"
    if heading_level == 1:
        return "chapter"
    if heading_level == 2:
        return "section"
    return "subsection"


def _semantic_role(heading_path: str, content: str) -> str:
    text = f"{heading_path}\n{content[:1200]}".lower()
    role_markers = (
        ("requirement", ("requirement", "must ", "shall ", "acceptance criteria")),
        ("decision", ("decision", "decide", "accepted choice")),
        ("evidence", ("evidence", "verified", "test output", "receipt", "benchmark")),
        ("rationale", ("rationale", "because", "reason")),
        ("open_question", ("open question", "question", "?")),
        ("warning", ("warning", "risk", "caution")),
        ("definition", ("definition", "means", "glossary")),
        ("example", ("example", "for example")),
        ("plan", ("plan", "roadmap", "next step", "todo")),
    )
    for role, markers in role_markers:
        if any(marker in text for marker in markers):
            return role
    return "general"


def _domains_for_terms(terms: list[str]) -> list[str]:
    term_set = {term.lower() for term in terms}
    domains: set[str] = set()
    domain_markers = {
        "software": {"api", "cli", "code", "db", "graph", "memory", "schema", "sqlite", "test", "vector"},
        "legal": {"law", "legal", "regulation", "statute"},
        "compliance": {"audit", "compliance", "control", "evidence", "policy"},
        "finance": {"billing", "finance", "invoice", "ledger", "payment", "settlement"},
        "research": {"analysis", "paper", "research", "source", "study"},
        "product": {"customer", "feature", "product", "roadmap", "user"},
        "operations": {"handoff", "maintenance", "ops", "process", "workflow"},
    }
    for domain, markers in domain_markers.items():
        if term_set & markers:
            domains.add(domain)
    return sorted(domains) or ["general"]


def _chunk_metadata(
    source_path: str,
    heading_path: str,
    heading_level: int,
    heading_title: str,
    content: str,
    keywords: list[str],
    is_continuation: bool,
) -> dict[str, Any]:
    block_type = _block_type(content)
    structural_level = _structural_level(heading_level)
    structural_types = {structural_level}
    if heading_level == 1:
        structural_types.add("title")
    elif heading_level == 2:
        structural_types.add("subtitle")
    if _split_blocks(content):
        structural_types.add("paragraph")
    if block_type in {"annotation", "schema", "table"}:
        structural_types.add(block_type)
    page = _page_marker(content)
    if page:
        structural_types.add("page")
    heading_terms = _keyword_list(heading_path, heading_title, limit=8)
    topics = _keyword_list(source_path, heading_path, " ".join(keywords), limit=8)
    subtopics = heading_terms[:6] or topics[:4]
    context_policy = "include_parent_heading" if heading_level else "self_only"
    if is_continuation:
        context_policy = "include_siblings"
    if block_type == "table":
        context_policy = "include_table_context"
    elif block_type == "schema":
        context_policy = "include_schema_caption"
    elif block_type == "annotation":
        context_policy = "include_annotation_target"
    return {
        "source_title": heading_path.split(" > ", 1)[0] if heading_path else Path(source_path).name,
        "heading_level": heading_level,
        "heading_title": heading_title,
        "structural_level": structural_level,
        "structural_types": sorted(structural_types),
        "block_type": block_type,
        "semantic_role": _semantic_role(heading_path, content),
        "topics": topics,
        "subtopics": subtopics,
        "domains": _domains_for_terms([*topics, *subtopics, *keywords]),
        "relation_strength": "continuation" if is_continuation else "same_section",
        "context_policy": context_policy,
        "source_location": {
            "page": page,
            "line_start": None,
            "line_end": None,
        },
        "extraction_confidence": "deterministic_structure",
    }


def _semantic_chunks_for_text(
    document_id: str,
    source_path: str,
    text: str,
    lifecycle: str,
) -> list[dict[str, Any]]:
    extracted_chunks = _parse_layout_extraction_sidecar(source_path, text)
    if extracted_chunks is not None:
        chunks: list[dict[str, Any]] = []
        for extracted in extracted_chunks:
            content = str(extracted["content"])
            ordinal = int(extracted.get("ordinal") or len(chunks) + 1)
            sequence_index = int(extracted.get("sequence_index") or 1)
            metadata = (
                extracted.get("metadata", {})
                if isinstance(extracted.get("metadata"), dict)
                else {}
            )
            keywords = extracted.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = _keyword_list(source_path, str(extracted.get("heading_path") or ""), content)
            heading_path = str(extracted.get("heading_path") or "Document")
            summary = str(extracted.get("summary") or _summary_for_chunk(content, heading_path))
            chunk = {
                "id": _chunk_id(document_id, ordinal, sequence_index, content),
                "document_id": document_id,
                "source_path": source_path,
                "heading_path": heading_path,
                "ordinal": ordinal,
                "sequence_index": sequence_index,
                "parent_chunk_id": "",
                "previous_chunk_id": "",
                "next_chunk_id": "",
                "lifecycle": lifecycle,
                "content_hash": _content_hash_text(content),
                "summary": summary,
                "keywords": [str(item) for item in keywords],
                "content": content,
                "metadata": metadata,
                "content_preview": content[:1000],
                "is_continuation": False,
            }
            chunks.append(chunk)
        for index, chunk in enumerate(chunks):
            if index:
                chunk["previous_chunk_id"] = chunks[index - 1]["id"]
            if index + 1 < len(chunks):
                chunk["next_chunk_id"] = chunks[index + 1]["id"]
        return chunks

    chunks: list[dict[str, Any]] = []
    ordinal = 0
    for section in _split_sections(text):
        ordinal += 1
        heading_path = section["heading_path"]
        heading_level = int(section.get("heading_level") or 0)
        heading_title = str(section.get("heading_title") or "Document")
        parts = _continuation_parts(section["content"])
        parent_id = ""
        for sequence_index, part in enumerate(parts, start=1):
            keywords = _keyword_list(source_path, heading_path, part)
            is_continuation = len(parts) > 1
            chunk = {
                "id": _chunk_id(document_id, ordinal, sequence_index, part),
                "document_id": document_id,
                "source_path": source_path,
                "heading_path": heading_path,
                "ordinal": ordinal,
                "sequence_index": sequence_index,
                "parent_chunk_id": parent_id,
                "previous_chunk_id": "",
                "next_chunk_id": "",
                "lifecycle": lifecycle,
                "content_hash": _content_hash_text(part),
                "summary": _summary_for_chunk(part, heading_path),
                "keywords": keywords,
                "content": part,
                "metadata": _chunk_metadata(
                    source_path,
                    heading_path,
                    heading_level,
                    heading_title,
                    part,
                    keywords,
                    is_continuation,
                ),
                "content_preview": part[:1000],
                "is_continuation": is_continuation,
            }
            if sequence_index == 1:
                parent_id = str(chunk["id"])
            chunks.append(chunk)
    for index, chunk in enumerate(chunks):
        if index:
            chunk["previous_chunk_id"] = chunks[index - 1]["id"]
        if index + 1 < len(chunks):
            chunk["next_chunk_id"] = chunks[index + 1]["id"]
    return chunks


def _insert_edge(conn: sqlite3.Connection, source_id: str, target_id: str, edge_type: str, data: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO edges(id, source_id, target_id, type, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _edge_id(source_id, target_id, edge_type),
            source_id,
            target_id,
            edge_type,
            _json_dumps(data),
            _now_iso(),
        ),
    )


def _insert_layout_node(conn: sqlite3.Connection, node: dict[str, Any], now: str) -> None:
    conn.execute(
        """
        INSERT INTO document_layout_nodes(
          id, document_id, source_path, node_type, parent_id, ordinal,
          sequence_index, heading_path, title, lifecycle, content_hash, summary,
          metadata, content_preview, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node["id"],
            node["document_id"],
            node["source_path"],
            node["node_type"],
            node.get("parent_id", ""),
            node["ordinal"],
            node.get("sequence_index", 0),
            node.get("heading_path", ""),
            node.get("title", ""),
            node["lifecycle"],
            node.get("content_hash", ""),
            node.get("summary", ""),
            _json_dumps(node.get("metadata", {})),
            node.get("content_preview", ""),
            now,
            now,
        ),
    )


def _insert_layout_edge(
    conn: sqlite3.Connection,
    document_id: str,
    source_id: str,
    target_id: str,
    edge_type: str,
    data: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO document_layout_edges(
          id, document_id, source_id, target_id, type, data, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _layout_edge_id(source_id, target_id, edge_type),
            document_id,
            source_id,
            target_id,
            edge_type,
            _json_dumps(data),
            _now_iso(),
        ),
    )


def _first_heading_title(chunks: list[dict[str, Any]], source_path: str) -> str:
    for chunk in chunks:
        metadata = chunk.get("metadata", {}) if isinstance(chunk.get("metadata"), dict) else {}
        if metadata.get("source_title") and isinstance(metadata.get("layout_extraction"), dict):
            return str(metadata["source_title"])
        if int(metadata.get("heading_level") or 0) == 1 and metadata.get("heading_title"):
            return str(metadata["heading_title"])
    return Path(source_path).stem or source_path


def _layout_node(
    document_id: str,
    source_path: str,
    node_type: str,
    lifecycle: str,
    ordinal: int,
    sequence_index: int,
    title: str,
    summary: str,
    content: str,
    *,
    parent_id: str = "",
    heading_path: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": _layout_node_id(document_id, node_type, ordinal, sequence_index, f"{title}\n{content}"),
        "document_id": document_id,
        "source_path": source_path,
        "node_type": node_type,
        "parent_id": parent_id,
        "ordinal": ordinal,
        "sequence_index": sequence_index,
        "heading_path": heading_path,
        "title": title[:220],
        "lifecycle": lifecycle,
        "content_hash": _content_hash_text(content),
        "summary": summary[:300],
        "metadata": metadata or {},
        "content_preview": content[:1000],
    }


def _heading_layout_type(heading_level: int) -> str:
    if heading_level <= 0:
        return "document"
    if heading_level == 1:
        return "chapter"
    if heading_level == 2:
        return "section"
    return "subsection"


def _block_layout_type(block: str, metadata: dict[str, Any] | None = None) -> str:
    if metadata:
        layout_type = _canonical_deep_layout_type(metadata.get("layout_node_type") or metadata.get("block_type"))
        if layout_type in DEEP_LAYOUT_NODE_TYPES:
            return layout_type
    block_type = _block_type(block)
    if block_type in {"annotation", "schema", "table"}:
        return block_type
    return "paragraph"


def _layout_blocks_for_chunk(chunk_content: str, metadata: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(metadata.get("layout_extraction"), dict):
        return [(chunk_content, metadata)]
    return [(block, {}) for block in _split_blocks(chunk_content)]


def _markdown_table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_markdown_table_delimiter(cells: list[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        normalized = cell.replace(" ", "")
        if not re.fullmatch(r":?-{3,}:?", normalized):
            return False
    return True


def _table_cell_specs(block: str) -> list[dict[str, Any]]:
    lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("|") and line.strip().count("|") >= 2
    ]
    if len(lines) < 2:
        return []
    headers = _markdown_table_cells(lines[0])
    delimiter = _markdown_table_cells(lines[1])
    if not _is_markdown_table_delimiter(delimiter):
        return []
    specs: list[dict[str, Any]] = []
    for column_index, value in enumerate(headers, start=1):
        if not value:
            continue
        specs.append(
            {
                "node_type": "table_cell",
                "title": f"Header {column_index}",
                "content": value,
                "metadata": {
                    "row_index": 0,
                    "column_index": column_index,
                    "column_header": value,
                    "is_header": True,
                },
            }
        )
    for row_index, line in enumerate(lines[2:], start=1):
        cells = _markdown_table_cells(line)
        for column_index, value in enumerate(cells, start=1):
            if not value:
                continue
            header = headers[column_index - 1] if column_index - 1 < len(headers) else ""
            title = f"R{row_index}C{column_index}"
            content = f"{header}: {value}" if header else value
            specs.append(
                {
                    "node_type": "table_cell",
                    "title": title,
                    "content": content,
                    "metadata": {
                        "row_index": row_index,
                        "column_index": column_index,
                        "column_header": header,
                        "is_header": False,
                        "cell_text": value,
                    },
                }
            )
    return specs


def _strip_code_fence(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith(("```", "~~~")):
        closing = lines[-1].strip()
        if closing.startswith(("```", "~~~")):
            return "\n".join(lines[1:-1]).strip()
    return block.strip()


def _schema_value_preview(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)[:160]
    return str(value)[:160]


def _walk_schema_fields(value: Any, prefix: str = "", limit: int = 80) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    if len(fields) >= limit:
        return fields
    if isinstance(value, dict):
        for key, child in value.items():
            if len(fields) >= limit:
                break
            field_path = f"{prefix}.{key}" if prefix else str(key)
            fields.append(
                {
                    "field_path": field_path,
                    "field_name": str(key),
                    "value_type": type(child).__name__,
                    "value_preview": _schema_value_preview(child),
                }
            )
            if isinstance(child, (dict, list)):
                fields.extend(_walk_schema_fields(child, field_path, limit - len(fields)))
    elif isinstance(value, list):
        for index, child in enumerate(value[:10]):
            if len(fields) >= limit:
                break
            field_path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            fields.append(
                {
                    "field_path": field_path,
                    "field_name": f"[{index}]",
                    "value_type": type(child).__name__,
                    "value_preview": _schema_value_preview(child),
                }
            )
            if isinstance(child, (dict, list)):
                fields.extend(_walk_schema_fields(child, field_path, limit - len(fields)))
    return fields[:limit]


def _schema_field_specs(block: str) -> list[dict[str, Any]]:
    raw = _strip_code_fence(block)
    fields: list[dict[str, Any]] = []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        fields = _walk_schema_fields(parsed)
    if not fields:
        for line in raw.splitlines():
            match = re.match(r"^\s*[\"']?([A-Za-z_][A-Za-z0-9_.-]*)[\"']?\s*[:=]\s*(.+?)\s*$", line)
            if not match:
                continue
            fields.append(
                {
                    "field_path": match.group(1),
                    "field_name": match.group(1).rsplit(".", 1)[-1],
                    "value_type": "text",
                    "value_preview": match.group(2)[:160],
                }
            )
            if len(fields) >= 80:
                break
    return [
        {
            "node_type": "schema_field",
            "title": field["field_path"],
            "content": f"{field['field_path']}: {field['value_preview']}",
            "metadata": field,
        }
        for field in fields
    ]


def _child_layout_specs(node_type: str, block: str, metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    layout_children = metadata.get("layout_children") if isinstance(metadata, dict) else None
    if isinstance(layout_children, list) and layout_children:
        return [child for child in layout_children if isinstance(child, dict)]
    if node_type == "table":
        return _table_cell_specs(block)
    if node_type == "schema":
        return _schema_field_specs(block)
    return []


def _bbox_from_source_location(source_location: dict[str, Any]) -> dict[str, float]:
    bbox = source_location.get("bbox")
    return bbox if isinstance(bbox, dict) else {}


def _bbox_center(bbox: dict[str, float]) -> tuple[float, float]:
    return ((bbox["x0"] + bbox["x1"]) / 2.0, (bbox["y0"] + bbox["y1"]) / 2.0)


def _bbox_overlap_ratio(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> float:
    overlap = max(0.0, min(first_end, second_end) - max(first_start, second_start))
    smallest = max(0.0, min(first_end - first_start, second_end - second_start))
    if smallest <= 0:
        return 0.0
    return overlap / smallest


def _bbox_area(bbox: dict[str, float]) -> float:
    return max(0.0, bbox["x1"] - bbox["x0"]) * max(0.0, bbox["y1"] - bbox["y0"])


def _bbox_intersection_area(first: dict[str, float], second: dict[str, float]) -> float:
    width = max(0.0, min(first["x1"], second["x1"]) - max(first["x0"], second["x0"]))
    height = max(0.0, min(first["y1"], second["y1"]) - max(first["y0"], second["y0"]))
    return width * height


def _nearest_visual_edges(page_items: list[dict[str, Any]], source_path: str) -> list[tuple[str, str, str, dict[str, Any]]]:
    edges: list[tuple[str, str, str, dict[str, Any]]] = []
    for item in page_items:
        source_id = str(item["id"])
        source_bbox = item["bbox"]
        source_center_x, source_center_y = _bbox_center(source_bbox)
        candidates: dict[str, tuple[float, str, dict[str, Any]]] = {}
        for other in page_items:
            target_id = str(other["id"])
            if target_id == source_id:
                continue
            target_bbox = other["bbox"]
            target_center_x, target_center_y = _bbox_center(target_bbox)
            x_overlap = _bbox_overlap_ratio(
                source_bbox["x0"],
                source_bbox["x1"],
                target_bbox["x0"],
                target_bbox["x1"],
            )
            y_overlap = _bbox_overlap_ratio(
                source_bbox["y0"],
                source_bbox["y1"],
                target_bbox["y0"],
                target_bbox["y1"],
            )
            intersection = _bbox_intersection_area(source_bbox, target_bbox)
            if intersection:
                area = min(_bbox_area(source_bbox), _bbox_area(target_bbox))
                overlap_ratio = intersection / area if area else 0.0
                score = 1.0 - overlap_ratio
                existing = candidates.get("visual_overlaps")
                if existing is None or score < existing[0]:
                    candidates["visual_overlaps"] = (
                        score,
                        target_id,
                        {"overlap_ratio": round(overlap_ratio, 6)},
                    )
            if x_overlap >= 0.25:
                if target_center_y > source_center_y:
                    distance = target_bbox["y0"] - source_bbox["y1"]
                    if distance >= 0:
                        existing = candidates.get("visual_below")
                        if existing is None or distance < existing[0]:
                            candidates["visual_below"] = (
                                distance,
                                target_id,
                                {"distance": round(distance, 6), "axis_overlap": round(x_overlap, 6)},
                            )
                elif target_center_y < source_center_y:
                    distance = source_bbox["y0"] - target_bbox["y1"]
                    if distance >= 0:
                        existing = candidates.get("visual_above")
                        if existing is None or distance < existing[0]:
                            candidates["visual_above"] = (
                                distance,
                                target_id,
                                {"distance": round(distance, 6), "axis_overlap": round(x_overlap, 6)},
                            )
            if y_overlap >= 0.25:
                if target_center_x > source_center_x:
                    distance = target_bbox["x0"] - source_bbox["x1"]
                    if distance >= 0:
                        existing = candidates.get("visual_right_of")
                        if existing is None or distance < existing[0]:
                            candidates["visual_right_of"] = (
                                distance,
                                target_id,
                                {"distance": round(distance, 6), "axis_overlap": round(y_overlap, 6)},
                            )
                elif target_center_x < source_center_x:
                    distance = source_bbox["x0"] - target_bbox["x1"]
                    if distance >= 0:
                        existing = candidates.get("visual_left_of")
                        if existing is None or distance < existing[0]:
                            candidates["visual_left_of"] = (
                                distance,
                                target_id,
                                {"distance": round(distance, 6), "axis_overlap": round(y_overlap, 6)},
                            )
        for edge_type, (_score, target_id, data) in candidates.items():
            edges.append((source_id, target_id, edge_type, {"source_path": source_path, **data}))
    return edges


def _sync_document_layout(
    conn: sqlite3.Connection,
    document_id: str,
    source_path: str,
    lifecycle: str,
    chunks: list[dict[str, Any]],
) -> int:
    old_rows = conn.execute(
        "SELECT id FROM document_layout_nodes WHERE document_id = ?",
        (document_id,),
    ).fetchall()
    for row in old_rows:
        layout_id = str(row["id"])
        conn.execute(
            "DELETE FROM document_layout_edges WHERE source_id = ? OR target_id = ?",
            (layout_id, layout_id),
        )
    conn.execute("DELETE FROM document_layout_nodes WHERE document_id = ?", (document_id,))

    if not chunks:
        return 0

    now = _now_iso()
    nodes: list[dict[str, Any]] = []
    edges: list[tuple[str, str, str, dict[str, Any]]] = []
    heading_nodes: dict[str, str] = {}
    page_nodes: dict[str, str] = {}
    page_reading_nodes: dict[str, list[str]] = {}
    page_visual_items: dict[str, list[dict[str, Any]]] = {}
    layout_block_nodes: dict[str, str] = {}
    pending_target_edges: list[tuple[str, str, str, dict[str, Any]]] = []
    previous_reading_node = ""

    document_title = _first_heading_title(chunks, source_path)
    document_node = _layout_node(
        document_id,
        source_path,
        "document",
        lifecycle,
        0,
        0,
        document_title,
        f"Document layout for {source_path}",
        document_title,
        metadata={"source_path": source_path, "layout_version": 1},
    )
    nodes.append(document_node)
    document_node_id = str(document_node["id"])

    title_node_created = False
    for chunk in chunks:
        metadata = chunk.get("metadata", {}) if isinstance(chunk.get("metadata"), dict) else {}
        heading_path = str(chunk.get("heading_path") or "Document")
        heading_level = int(metadata.get("heading_level") or 0)
        heading_title = str(metadata.get("heading_title") or heading_path.rsplit(" > ", 1)[-1])
        ordinal = int(chunk.get("ordinal") or 0)
        sequence_index = int(chunk.get("sequence_index") or 1)
        chunk_id = str(chunk.get("id") or "")
        chunk_content = str(chunk.get("content") or chunk.get("content_preview") or "")

        if heading_path and heading_path != "Document" and sequence_index == 1 and heading_path not in heading_nodes:
            parent_heading = " > ".join(heading_path.split(" > ")[:-1])
            parent_id = heading_nodes.get(parent_heading, document_node_id)
            heading_type = _heading_layout_type(heading_level)
            heading_node = _layout_node(
                document_id,
                source_path,
                heading_type,
                lifecycle,
                ordinal,
                0,
                heading_title,
                chunk.get("summary", heading_title),
                heading_title,
                parent_id=parent_id,
                heading_path=heading_path,
                metadata={
                    "chunk_id": chunk_id,
                    "heading_level": heading_level,
                    "structural_types": metadata.get("structural_types", []),
                    "context_policy": metadata.get("context_policy", ""),
                },
            )
            nodes.append(heading_node)
            heading_node_id = str(heading_node["id"])
            heading_nodes[heading_path] = heading_node_id
            edges.append((parent_id, heading_node_id, "contains", {"source_path": source_path}))
            edges.append((heading_node_id, chunk_id, "represented_by_chunk", {"source_path": source_path}))
            edges.append((chunk_id, heading_node_id, "has_layout_node", {"source_path": source_path}))

            if heading_level == 1 and not title_node_created:
                title_node = _layout_node(
                    document_id,
                    source_path,
                    "title",
                    lifecycle,
                    ordinal,
                    -1,
                    heading_title,
                    heading_title,
                    heading_title,
                    parent_id=document_node_id,
                    heading_path=heading_path,
                    metadata={"chunk_id": chunk_id, "heading_level": heading_level},
                )
                nodes.append(title_node)
                edges.append((document_node_id, str(title_node["id"]), "contains", {"source_path": source_path}))
                edges.append((str(title_node["id"]), chunk_id, "represented_by_chunk", {"source_path": source_path}))
                title_node_created = True

        parent_id = heading_nodes.get(heading_path, document_node_id)
        source_location = metadata.get("source_location", {}) if isinstance(metadata.get("source_location"), dict) else {}
        page = str(source_location.get("page") or "")
        if page and page not in page_nodes:
            page_node = _layout_node(
                document_id,
                source_path,
                "page",
                lifecycle,
                ordinal,
                0,
                f"Page {page}",
                f"Page {page}",
                page,
                parent_id=document_node_id,
                heading_path=heading_path,
                metadata={
                    "page": page,
                    "chunk_id": chunk_id,
                    "page_width": source_location.get("page_width"),
                    "page_height": source_location.get("page_height"),
                    "coordinate_system": source_location.get("coordinate_system"),
                    "source_document_path": metadata.get("source_document_path", ""),
                    "layout_extraction": metadata.get("layout_extraction", {}),
                },
            )
            nodes.append(page_node)
            page_nodes[page] = str(page_node["id"])
            edges.append((document_node_id, str(page_node["id"]), "contains", {"source_path": source_path}))

        block_parent_id = page_nodes.get(page, parent_id) if isinstance(metadata.get("layout_extraction"), dict) else parent_id
        for block_index, (block, block_metadata) in enumerate(_layout_blocks_for_chunk(chunk_content, metadata), start=1):
            node_type = _block_layout_type(block, block_metadata)
            summary = _summary_for_chunk(block, heading_path)
            block_source_location = (
                block_metadata.get("source_location", {})
                if isinstance(block_metadata.get("source_location"), dict)
                else source_location
            )
            block_id = str(block_metadata.get("layout_block_id") or "")
            target_block_id = str(block_metadata.get("target_block_id") or "")
            node = _layout_node(
                document_id,
                source_path,
                node_type,
                lifecycle,
                ordinal,
                sequence_index * 1000 + block_index,
                summary,
                summary,
                block,
                parent_id=block_parent_id,
                heading_path=heading_path,
                metadata={
                    "chunk_id": chunk_id,
                    "block_type": block_metadata.get("block_type") or _block_type(block),
                    "layout_node_type": node_type,
                    "layout_block_id": block_id,
                    "target_block_id": target_block_id,
                    "semantic_role": _semantic_role(heading_path, block),
                    "context_policy": metadata.get("context_policy", ""),
                    "topics": metadata.get("topics", []),
                    "subtopics": metadata.get("subtopics", []),
                    "domains": metadata.get("domains", []),
                    "source_location": block_source_location,
                    "source_document_path": metadata.get("source_document_path", ""),
                    "source_media_type": metadata.get("source_media_type", ""),
                    "layout_extraction": metadata.get("layout_extraction", {}),
                    "ocr_confidence": metadata.get("ocr_confidence"),
                },
            )
            nodes.append(node)
            node_id = str(node["id"])
            edges.append((block_parent_id, node_id, "contains", {"source_path": source_path}))
            edges.append((node_id, chunk_id, "represented_by_chunk", {"source_path": source_path}))
            edges.append((chunk_id, node_id, "has_layout_node", {"source_path": source_path}))
            if page:
                edges.append((page_nodes[page], node_id, "in_page", {"page": page, "source_path": source_path}))
                page_reading_nodes.setdefault(page, []).append(node_id)
                bbox = _bbox_from_source_location(block_source_location)
                if bbox:
                    page_visual_items.setdefault(page, []).append({"id": node_id, "bbox": bbox})
            if block_id:
                layout_block_nodes[block_id] = node_id
            if target_block_id and node_type in {"caption", "footnote"}:
                edge_type = "caption_for" if node_type == "caption" else "footnote_for"
                pending_target_edges.append(
                    (
                        node_id,
                        target_block_id,
                        edge_type,
                        {"page": page, "source_path": source_path, "target_block_id": target_block_id},
                    )
                )
            for child_index, child_spec in enumerate(_child_layout_specs(node_type, block, block_metadata), start=1):
                child_type = str(child_spec.get("node_type") or "")
                child_content = str(child_spec.get("content") or "")
                if not child_type or not child_content:
                    continue
                child_metadata = child_spec.get("metadata", {})
                if not isinstance(child_metadata, dict):
                    child_metadata = {}
                child_edge_type = "table_cell_of" if child_type == "table_cell" else "schema_field_of"
                child_node = _layout_node(
                    document_id,
                    source_path,
                    child_type,
                    lifecycle,
                    ordinal,
                    sequence_index * 1000000 + block_index * 1000 + child_index,
                    str(child_spec.get("title") or child_type),
                    child_content,
                    child_content,
                    parent_id=node_id,
                    heading_path=heading_path,
                    metadata={
                        "chunk_id": chunk_id,
                        "parent_layout_node_id": node_id,
                        "parent_layout_node_type": node_type,
                        "source_location": block_source_location,
                        "source_document_path": metadata.get("source_document_path", ""),
                        **child_metadata,
                    },
                )
                nodes.append(child_node)
                child_node_id = str(child_node["id"])
                edges.append((node_id, child_node_id, "contains", {"source_path": source_path}))
                edges.append((child_node_id, node_id, child_edge_type, {"source_path": source_path}))
                edges.append((child_node_id, chunk_id, "represented_by_chunk", {"source_path": source_path}))
                edges.append((chunk_id, child_node_id, "has_layout_node", {"source_path": source_path}))
                if page:
                    edges.append((page_nodes[page], child_node_id, "in_page", {"page": page, "source_path": source_path}))
            if previous_reading_node:
                edges.append((previous_reading_node, node_id, "next_layout", {"source_path": source_path}))
                edges.append((node_id, previous_reading_node, "previous_layout", {"source_path": source_path}))
            previous_reading_node = node_id

    for page, node_ids in page_reading_nodes.items():
        for left, right in zip(node_ids, node_ids[1:]):
            edges.append((left, right, "visual_adjacent", {"page": page, "source_path": source_path}))
    for page, page_items in page_visual_items.items():
        for source_id, target_id, edge_type, data in _nearest_visual_edges(page_items, source_path):
            edges.append((source_id, target_id, edge_type, {"page": page, **data}))
    for source_id, target_block_id, edge_type, data in pending_target_edges:
        target_node_id = layout_block_nodes.get(target_block_id)
        if target_node_id:
            edges.append((source_id, target_node_id, edge_type, data))

    for node in nodes:
        _insert_layout_node(conn, node, now)
    for source_id, target_id, edge_type, data in edges:
        _insert_layout_edge(conn, document_id, source_id, target_id, edge_type, data)
    return len(nodes)


def _sync_semantic_chunks(
    conn: sqlite3.Connection,
    document_id: str,
    source_path: str,
    text: str,
    lifecycle: str,
    extra_metadata: dict[str, Any] | None = None,
) -> int:
    old_rows = conn.execute(
        "SELECT id FROM semantic_chunks WHERE document_id = ?",
        (document_id,),
    ).fetchall()
    old_ids = [str(row["id"]) for row in old_rows]
    for chunk_id in old_ids:
        conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (chunk_id, chunk_id))
    conn.execute("DELETE FROM semantic_chunks WHERE document_id = ?", (document_id,))

    chunks = _semantic_chunks_for_text(document_id, source_path, text, lifecycle)
    if extra_metadata:
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            chunk["metadata"] = {**metadata, **extra_metadata}
    now = _now_iso()
    for chunk in chunks:
        conn.execute(
            """
            INSERT INTO semantic_chunks(
              id, document_id, source_path, heading_path, ordinal, sequence_index,
              parent_chunk_id, previous_chunk_id, next_chunk_id, lifecycle,
              content_hash, summary, keywords, metadata, content_preview, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk["id"],
                chunk["document_id"],
                chunk["source_path"],
                chunk["heading_path"],
                chunk["ordinal"],
                chunk["sequence_index"],
                chunk["parent_chunk_id"],
                chunk["previous_chunk_id"],
                chunk["next_chunk_id"],
                chunk["lifecycle"],
                chunk["content_hash"],
                chunk["summary"],
                _json_dumps(chunk["keywords"]),
                _json_dumps(chunk["metadata"]),
                chunk["content_preview"],
                now,
                now,
            ),
        )
        _insert_edge(conn, document_id, chunk["id"], "contains", {"source_path": source_path})
        if chunk["previous_chunk_id"]:
            _insert_edge(
                conn,
                chunk["previous_chunk_id"],
                chunk["id"],
                "next_chunk",
                {"source_path": source_path},
            )
        if chunk["previous_chunk_id"]:
            _insert_edge(
                conn,
                chunk["id"],
                chunk["previous_chunk_id"],
                "previous_chunk",
                {"source_path": source_path},
            )
        if chunk["is_continuation"] and chunk["parent_chunk_id"] and chunk["parent_chunk_id"] != chunk["id"]:
            _insert_edge(
                conn,
                chunk["parent_chunk_id"],
                chunk["id"],
                "continues_to",
                {"heading_path": chunk["heading_path"]},
            )
            _insert_edge(
                conn,
                chunk["id"],
                chunk["parent_chunk_id"],
                "continues_from",
                {"heading_path": chunk["heading_path"]},
            )
            _insert_edge(
                conn,
                chunk["parent_chunk_id"],
                chunk["id"],
                "same_section_as",
                {"heading_path": chunk["heading_path"]},
            )
    _sync_document_layout(conn, document_id, source_path, lifecycle, chunks)
    return len(chunks)


def _chunk_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["keywords"] = json.loads(str(data.get("keywords") or "[]"))
    except json.JSONDecodeError:
        data["keywords"] = []
    try:
        data["metadata"] = json.loads(str(data.get("metadata") or "{}"))
    except json.JSONDecodeError:
        data["metadata"] = {}
    return data


def _chunks_for_path(conn: sqlite3.Connection, rel_path: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM semantic_chunks
        WHERE source_path = ?
        ORDER BY ordinal, sequence_index
        """,
        (rel_path,),
    ).fetchall()
    return [_chunk_row_to_dict(row) for row in rows]


def _layout_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["metadata"] = json.loads(str(data.get("metadata") or "{}"))
    except json.JSONDecodeError:
        data["metadata"] = {}
    return data


def _layout_nodes_for_path(conn: sqlite3.Connection, rel_path: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM document_layout_nodes
        WHERE source_path = ?
        ORDER BY ordinal, sequence_index, node_type, id
        """,
        (rel_path,),
    ).fetchall()
    return [_layout_row_to_dict(row) for row in rows]


def _layout_edges_for_path(conn: sqlite3.Connection, rel_path: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.*
        FROM document_layout_edges e
        JOIN document_layout_nodes n ON n.id = e.source_id OR n.id = e.target_id
        WHERE n.source_path = ?
        GROUP BY e.id
        ORDER BY e.type, e.source_id, e.target_id
        """,
        (rel_path,),
    ).fetchall()
    return [_layout_row_to_dict(row) for row in rows]


def cmd_memory_chunks(project: Path, path: str, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_chunks_memory_not_initialized", message)
        return 1
    rel_path = _relative_path(project, path)
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        chunks = _chunks_for_path(conn, rel_path)
    if json_output:
        print(json.dumps({"ok": True, "path": rel_path, "chunks": chunks}, indent=2))
        return 0
    print(f"Memory chunks: {rel_path}")
    if not chunks:
        print("No semantic chunks found. Run `cc memory scan` after adding the document.")
        return 0
    for chunk in chunks:
        continuation = " continuation" if int(chunk.get("sequence_index") or 1) > 1 else ""
        print(
            f"- {chunk['id']} - {chunk['heading_path']}"
            f" [{chunk['ordinal']}.{chunk['sequence_index']}]{continuation}"
        )
        print(f"  Summary: {chunk['summary']}")
    return 0


def cmd_memory_layout(project: Path, path: str, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_layout_memory_not_initialized", message)
        return 1
    rel_path = _relative_path(project, path)
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        nodes = _layout_nodes_for_path(conn, rel_path)
        edges = _layout_edges_for_path(conn, rel_path)
    if json_output:
        print(json.dumps({"ok": True, "path": rel_path, "nodes": nodes, "edges": edges}, indent=2))
        return 0
    print(f"Document layout: {rel_path}")
    if not nodes:
        print("No document layout nodes found. Run `cc memory scan` after adding the document.")
        return 0
    counts: dict[str, int] = {}
    for node in nodes:
        node_type = str(node.get("node_type") or "")
        counts[node_type] = counts.get(node_type, 0) + 1
    print("Node types: " + ", ".join(f"{key}: {counts[key]}" for key in sorted(counts)))
    for node in nodes[:80]:
        parent = f" parent={node['parent_id']}" if node.get("parent_id") else ""
        heading = f" heading={node['heading_path']}" if node.get("heading_path") else ""
        print(
            f"- {node['node_type']} `{node['id']}`{parent}{heading}\n"
            f"  {node['summary']}"
        )
    if len(nodes) > 80:
        print(f"... {len(nodes) - 80} more node(s)")
    return 0


__all__ = [
    "cmd_memory_chunks",
    "cmd_memory_layout",
    "_chunks_for_path",
    "_layout_nodes_for_path",
    "_semantic_chunks_for_text",
    "_sync_semantic_chunks",
]
