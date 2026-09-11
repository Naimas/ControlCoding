# SPDX-License-Identifier: PolyForm-Shield-1.0.0
# Copyright (c) 2026 Stefano Tonello.

"""Stdlib fallback extractors for ControlWork source import.

These extractors intentionally avoid optional parser dependencies. Future
extra-based extractors should use a separate family/dependency marker while
preserving the public extractor names chosen by the import orchestration.
"""

from __future__ import annotations

import re
import zipfile
import zlib
from pathlib import Path
import xml.etree.ElementTree as ET


PDF_MAX_BYTES = 8 * 1024 * 1024
STDLIB_FALLBACK_METADATA = {
    "extractorFamily": "stdlib_fallback",
    "extractorDependency": "stdlib",
}


def stdlib_fallback_metadata(metadata: dict | None = None) -> dict:
    result = dict(metadata or {})
    result.update(STDLIB_FALLBACK_METADATA)
    return result


def xml_text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def docx_text_extract(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": f"DOCX text extraction failed: {exc}"}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {"ok": False, "error": f"DOCX XML parse failed: {exc}"}
    paragraphs = []
    for paragraph in root.iter():
        if paragraph.tag.endswith("}p"):
            parts = [xml_text(child) for child in paragraph.iter() if child.tag.endswith("}t")]
            line = "".join(parts).strip()
            if line:
                paragraphs.append(line)
    if not paragraphs:
        return {"ok": False, "error": "DOCX contains no extractable text"}
    return {
        "ok": True,
        "text": "\n\n".join(paragraphs),
        "metadata": stdlib_fallback_metadata({"paragraphCount": len(paragraphs)}),
        "warnings": ["DOCX extraction is best-effort text only; formatting is not preserved."],
    }


def xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    values = []
    for item in root.iter():
        if not item.tag.endswith("}si"):
            continue
        text = "".join(part.text or "" for part in item.iter() if part.tag.endswith("}t"))
        values.append(text)
    return values


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(part.text or "" for part in cell.iter() if part.tag.endswith("}t")).strip()
    value = ""
    for child in cell:
        if child.tag.endswith("}v") and child.text is not None:
            value = child.text.strip()
            break
    if cell_type == "s" and value:
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return value
    return value


def xlsx_text_extract(path: Path, max_rows: int = 40, max_sheets: int = 5) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            sheet_names = sorted(name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name))
            shared_strings = xlsx_shared_strings(archive)
            lines = ["XLSX workbook preview"]
            total_rows = 0
            max_columns = 0
            sheets_seen = 0
            for sheet_name in sheet_names[:max_sheets]:
                sheets_seen += 1
                try:
                    root = ET.fromstring(archive.read(sheet_name))
                except (KeyError, ET.ParseError):
                    continue
                rows = []
                for row in root.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    values = [xlsx_cell_value(cell, shared_strings) for cell in row if cell.tag.endswith("}c")]
                    if any(values):
                        total_rows += 1
                        max_columns = max(max_columns, len(values))
                        if len(rows) < max_rows:
                            rows.append(values)
                lines.extend(["", f"## {Path(sheet_name).stem}", ""])
                if rows:
                    lines.extend(", ".join(value for value in row) for row in rows)
                else:
                    lines.append("[No rows extracted.]")
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": f"XLSX table extraction failed: {exc}"}
    if sheets_seen == 0:
        return {"ok": False, "error": "XLSX contains no extractable worksheets"}
    warnings = ["XLSX extraction is a bounded table summary; formulas and formatting are not preserved."]
    if len(sheet_names) > max_sheets:
        warnings.append("XLSX preview truncated by sheet limit.")
    return {
        "ok": True,
        "text": "\n".join(lines),
        "metadata": stdlib_fallback_metadata(
            {"sheetCount": len(sheet_names), "rowCount": total_rows, "columnCount": max_columns}
        ),
        "warnings": warnings,
    }


def decode_pdf_literal_string(value: bytes) -> str:
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
        return raw[2:].decode("utf-16-be", errors="replace")
    return raw.decode("latin-1", errors="replace")


def decode_pdf_hex_string(value: bytes) -> str:
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


def pdf_text_tokens(data: bytes) -> list[str]:
    tokens: list[str] = []
    string_pattern = rb"\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]+)>"
    for match in re.finditer(rb"\[(.*?)\]\s*TJ", data, flags=re.DOTALL):
        parts: list[str] = []
        for token in re.finditer(string_pattern, match.group(1), flags=re.DOTALL):
            raw = token.group(0)
            if raw.startswith(b"("):
                parts.append(decode_pdf_literal_string(raw[1:-1]))
            elif raw.startswith(b"<") and not raw.startswith(b"<<"):
                parts.append(decode_pdf_hex_string(raw[1:-1]))
        joined = "".join(parts).strip()
        if joined:
            tokens.append(joined)
    for match in re.finditer(rb"(\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]+)>)\s*(?:Tj|'|\")", data, flags=re.DOTALL):
        raw = match.group(1)
        text = decode_pdf_literal_string(raw[1:-1]).strip() if raw.startswith(b"(") else decode_pdf_hex_string(raw[1:-1]).strip()
        if text:
            tokens.append(text)
    return tokens


def pdf_streams(data: bytes) -> list[bytes]:
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


def pdf_text_extract(path: Path, limit: int) -> dict:
    try:
        data = path.read_bytes()[:PDF_MAX_BYTES]
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if not data.startswith(b"%PDF-"):
        return {"ok": False, "error": "file does not look like a PDF"}
    pages: list[str] = []
    for stream in pdf_streams(data):
        tokens = pdf_text_tokens(stream)
        if tokens:
            pages.append("\n".join(tokens).strip())
        if sum(len(page) for page in pages) >= limit:
            break
    if not pages:
        return {"ok": False, "error": "PDF contains no extractable text; use --as-reference or OCR sidecar"}
    lines = []
    for page_index, page_text in enumerate(pages, start=1):
        lines.extend([f"## Page {page_index}", "", page_text])
    return {
        "ok": True,
        "text": "\n\n".join(lines),
        "metadata": stdlib_fallback_metadata({"pageCount": len(pages)}),
        "warnings": ["PDF text extraction is best-effort and does not perform OCR or layout analysis."],
    }
