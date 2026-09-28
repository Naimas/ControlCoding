"""Bounded rich-document preview, original preservation and OCR hash binding."""
import io
import json
import os
import zipfile
import zlib
import pytest
from test_cc_controlwork_manage import apply, plan, inventory, ID, TIME
from cc_memory_lib.knowledge_import import extract
from cc_panel_configuration import ConfigurationError, digest
import cc_controlwork_manage as manage


def office(name, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
    return stream.getvalue()


def test_docx_xlsx_pdf_coverage_is_visible():
    docx = office('word/document.xml', '<w:document xmlns:w="urn:word"><w:p><w:r><w:t>Visible design</w:t></w:r></w:p></w:document>')
    text = extract('design.docx', docx)
    assert 'Visible design' in text and digest(docx) in text and 'formatting is not preserved' in text
    xlsx = office('xl/worksheets/sheet1.xml', '<worksheet xmlns="urn:sheet"><sheetData><row><c t="inlineStr"><is><t>Budget</t></is></c></row></sheetData></worksheet>')
    assert 'Budget' in extract('budget.xlsx', xlsx)
    pdf = b'%PDF-1.4\n<< /Length 22 >>\nstream\nBT (Visible PDF) Tj ET\nendstream\n%%EOF'
    assert 'Visible PDF' in extract('design.pdf', pdf)


def test_scanned_pdf_requires_hash_bound_sidecar():
    raw = b'%PDF-1.4\nno text streams'
    with pytest.raises(ConfigurationError, match='ocr_required'):
        extract('scan.pdf', raw)
    value = {'sourcePath': 'scan.pdf', 'sourceHash': digest(raw), 'pages': [{'page': 1, 'text': 'Reviewed scan text'}]}
    # Use the canonical accepted hash key.
    value['contentHash'] = digest(raw)
    assert 'Reviewed scan text' in extract('scan.pdf', raw, json.dumps(value).encode())
    value['sourceHash'] = value['contentHash'] = '0' * 64
    with pytest.raises(ConfigurationError, match='ocr_source_mismatch'):
        extract('scan.pdf', raw, json.dumps(value).encode())


def test_expansion_and_encrypted_sources_are_rejected():
    raw = office('word/document.xml', 'x' * (4 * 1024 * 1024 + 1))
    with pytest.raises(ConfigurationError, match='document_expansion_limit'):
        extract('bomb.docx', raw)
    pdf = b'%PDF-1.4\n<< /Filter /FlateDecode >>\nstream\n' + zlib.compress(b'x' * (4 * 1024 * 1024 + 1)) + b'\nendstream'
    with pytest.raises(ConfigurationError, match='document_expansion_limit'):
        extract('bomb.pdf', pdf)
    with pytest.raises(ConfigurationError, match='encrypted_document'):
        extract('encrypted.pdf', b'%PDF-1.4 /Encrypt')


@pytest.mark.skipif(os.name != 'nt', reason='Windows transaction writer')
def test_reviewed_docx_import_binds_preview_and_preserves_original(tmp_path):
    apply(tmp_path)
    source = tmp_path / 'brief.docx'
    source.write_bytes(office('word/document.xml', '<d xmlns="urn:word"><p><t>Original design</t></p></d>'))
    raw = source.read_bytes()
    value = {'action': 'import', 'area': 'sources'}
    preview = plan(tmp_path, value, ['brief.docx'])
    assert any('Original design' in f.get('preview', '') for f in preview['files'])
    manage.save(str(tmp_path), value, ['brief.docx'], ID, TIME, preview['approval_id'])
    assert source.read_bytes() == raw
    assert not apply(tmp_path, value, ['brief.docx'])['files']
    preview = plan(tmp_path, value, ['brief.docx'])
    source.write_bytes(office('word/document.xml', '<d xmlns="urn:word"><p><t>Changed design</t></p></d>'))
    before = inventory(tmp_path)
    with pytest.raises(ConfigurationError, match='preview_mismatch'):
        manage.save(str(tmp_path), value, ['brief.docx'], ID, TIME, preview['approval_id'])
    assert inventory(tmp_path) == before
