import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError


def preview(root):
    return service.dispatch(root, 'ocr-preview', {'path': 'docs/scan.pdf', 'text': 'Reviewed scan evidence for the project.'})


def test_reviewed_ocr_is_source_bound_and_original_preserved(project):
    path = project/'docs/scan.pdf'
    raw = b'%PDF-1.4\n% Scanned document without extractable text\n%%EOF'
    path.write_bytes(raw)
    service.configure(project, {**service.DEFAULT, 'embedding': '', 'scopes': ['project', 'rich-documents']})
    result = preview(project)
    assert not (project/'docs/scan.pdf.ocr.json').exists()
    service.dispatch(project, 'ocr-save', {'path': result['path'], 'text': result['text'], 'approval': result['approval']})
    service.reconcile(project)
    assert service.query(project, 'Reviewed scan evidence', False)['citations']
    assert path.read_bytes() == raw
    path.write_bytes(raw+b'\n% new revision')
    with pytest.raises(KnowledgeError, match='ocr_source_mismatch'):
        service.reconcile(project)
    with pytest.raises(KnowledgeError, match='ocr_preview_changed'):
        service.dispatch(project, 'ocr-save', {'path': result['path'], 'text': result['text'], 'approval': result['approval']})


def test_ocr_cannot_read_an_unselected_source(project):
    with pytest.raises(KnowledgeError):
        service.dispatch(project, 'ocr-preview', {'path': '../private.pdf', 'text': 'arbitrary text'})
