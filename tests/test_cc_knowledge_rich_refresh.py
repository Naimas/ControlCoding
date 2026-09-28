"""Opt-in rich sources share atomic generations, history and deletion semantics."""
import json
import pytest
from test_cc_knowledge import project
from test_cc_rich_import import office
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_sources import digest
from cc_memory_lib.knowledge_store import database, KnowledgeError


def enable(root):
    service.configure(root, {**service.DEFAULT, 'scopes': ['project', 'rich-documents'], 'embedding': ''})


def docx(text):
    return office('word/document.xml', '<d xmlns="urn:word"><p><t>' + text + '</t></p></d>')


def source(root, path):
    return next(r for r in service.catalog(root)['sources'] if r['path'] == path)


def test_opt_in_edit_history_notes_and_deletion(project):
    file = project / 'docs/design.docx'
    file.write_bytes(docx('Original scope'))
    service.reconcile(project)
    assert all(r['path'] != 'docs/design.docx' for r in service.catalog(project)['sources'])
    enable(project)
    service.reconcile(project, 'timer')
    first = source(project, 'docs/design.docx')
    service.notes(project, first['id'], 'Keep human note')
    generation = service.status(project)['generation']
    service.reconcile(project, 'timer')
    assert service.status(project)['generation'] == generation
    raw = docx('Changed scope')
    file.write_bytes(raw)
    service.reconcile(project, 'commit-ceremony')
    second = source(project, 'docs/design.docx')
    assert first['id'] == second['id'] and first['revision'] != second['revision']
    page = service.page(project, first['id'])
    assert 'Changed scope' in page['body'] and page['notes'] == 'Keep human note'
    assert len(page['history']) == 2 and file.read_bytes() == raw
    with database(project) as db:
        assert not db.execute('SELECT 1 FROM chunks WHERE source=? AND revision=?', (first['id'], first['revision'])).fetchone()
    file.unlink()
    service.reconcile(project)
    assert all(r['id'] != first['id'] for r in service.catalog(project)['sources'])
    with database(project) as db:
        assert not db.execute('SELECT 1 FROM chunks WHERE source=?', (first['id'],)).fetchone()
        assert db.execute('SELECT count(*) FROM revisions WHERE source=?', (first['id'],)).fetchone()[0] == 2


def test_sidecar_only_changes_and_missing_or_mismatched_ocr(project):
    raw = b'%PDF-1.4\nno text streams'
    (project / 'docs/scan.pdf').write_bytes(raw)
    sidecar = project / 'docs/scan.pdf.ocr.json'
    def write(text, hash_value=digest(raw)):
        sidecar.write_text(json.dumps({'sourcePath': 'docs/scan.pdf', 'sourceHash': hash_value,
                                      'pages': [{'page': 1, 'text': text}]}), encoding='utf-8')
    write('First transcription')
    enable(project)
    service.reconcile(project)
    first = source(project, 'docs/scan.pdf')
    write('Corrected transcription')
    service.reconcile(project, 'source-change')
    second = source(project, 'docs/scan.pdf')
    assert first['revision'] != second['revision']
    assert 'Corrected transcription' in service.page(project, first['id'])['body']
    for bad_hash in ('0'*64, None):
        if bad_hash:
            write('Invalid', bad_hash)
        else:
            sidecar.unlink()
        with pytest.raises(KnowledgeError, match='ocr_source_mismatch|ocr_required'):
            service.reconcile(project)
        assert service.status(project)['needs_reconcile']
        assert source(project, 'docs/scan.pdf')['revision'] == second['revision']


def test_scope_removal_preserves_original_and_removes_projection(project):
    file = project / 'brief.docx'
    raw = docx('Private brief')
    file.write_bytes(raw)
    enable(project)
    service.reconcile(project)
    assert source(project, 'brief.docx')
    service.configure(project, service.DEFAULT.copy())
    service.reconcile(project)
    assert all(r['path'] != 'brief.docx' for r in service.catalog(project)['sources'])
    assert file.read_bytes() == raw


def test_invalid_rich_source_retains_coherent_generation(project):
    file = project / 'brief.docx'
    file.write_bytes(docx('Initial'))
    enable(project)
    service.reconcile(project)
    previous = service.catalog(project)
    file.write_bytes(b'x'*(1024*1024+1))
    with pytest.raises(KnowledgeError, match='source_budget'):
        service.reconcile(project)
    assert service.catalog(project) == previous


def test_publication_failure_and_retry_preserve_one_generation(project, monkeypatch):
    file = project / 'brief.docx'
    file.write_bytes(docx('Initial'))
    enable(project)
    service.reconcile(project)
    previous = service.catalog(project)
    file.write_bytes(docx('Revised'))
    original = service.compile_wiki
    def fail(*args):
        raise RuntimeError('simulated interruption')
    monkeypatch.setattr(service, 'compile_wiki', fail)
    with pytest.raises(RuntimeError):
        service.reconcile(project)
    assert service.catalog(project) == previous
    monkeypatch.setattr(service, 'compile_wiki', original)
    service.reconcile(project)
    assert 'Revised' in service.page(project, source(project, 'brief.docx')['id'])['body']


def test_xlsx_and_pdf_use_same_scope(project):
    (project / 'docs/table.xlsx').write_bytes(office('xl/worksheets/sheet1.xml',
        '<worksheet xmlns="urn:sheet"><sheetData><row><c t="inlineStr"><is><t>Budget</t></is></c></row></sheetData></worksheet>'))
    (project / 'docs/text.pdf').write_bytes(b'%PDF-1.4\n<< /Length 22 >>\nstream\nBT (Visible PDF) Tj ET\nendstream\n%%EOF')
    enable(project)
    service.reconcile(project)
    for path, text in [('docs/table.xlsx', 'Budget'), ('docs/text.pdf', 'Visible PDF')]:
        assert text in service.page(project, source(project, path)['id'])['body']
