"""Reviewed plan succession affects only current, authorized evidence."""
import pytest

from test_cc_knowledge import project
from test_cc_document_codes import configure, listing, proposed, decide
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError


def row(root, path):
    return next(item for item in listing(root)['rows'] if item['path'] == path)


def classify(root, path, quote, phase):
    source = row(root, path)
    pending = proposed(root, source, quote=quote, phase=phase)
    return decide(root, {**source, 'code': pending['code']}, phase=phase)['code']


def link(root, successor, predecessor, reason='Reviewed: the newer plan replaces the previous plan.'):
    source, target = row(root, successor), row(root, predecessor)
    return service.dispatch(root, 'code-link-save', {
        'source': source['id'], 'revision': source['revision'], 'expected': source['code']['token'],
        'link_expected': source['code']['lineage']['token'], 'target': target['id'],
        'target_revision': target['revision'], 'target_expected': target['code']['token'], 'reason': reason})


def test_reviewed_succession_is_bidirectional_bounded_and_invalidated(project):
    original = (project / 'docs/design.md').read_bytes()
    (project / 'docs/old-plan.md').write_text('# Old plan\nLegacy payment adapter is scheduled for retirement.\n', encoding='utf-8')
    (project / 'docs/new-plan.md').write_text('# New plan\nModern payment adapter replaces the legacy work.\n', encoding='utf-8')
    (project / 'docs/third-plan.md').write_text('# Third plan\nFuture payment adapter remains proposed.\n', encoding='utf-8')
    service.dispatch(project, 'sync', 'manual')
    configure(project)
    old = classify(project, 'docs/old-plan.md', 'Legacy payment adapter is scheduled for retirement.', 'F01')
    new = classify(project, 'docs/new-plan.md', 'Modern payment adapter replaces the legacy work.', 'F02')
    third = classify(project, 'docs/third-plan.md', 'Future payment adapter remains proposed.', 'F03')
    assert len({old['assignment']['id'], new['assignment']['id'], third['assignment']['id']}) == 3
    candidates = service.dispatch(project, 'code-link-candidates', {'source': row(project, 'docs/new-plan.md')['id'], 'query': old['assignment']['display'], 'offset': 0})
    assert candidates['total'] == 1 and candidates['rows'][0]['path'] == 'docs/old-plan.md'
    saved = link(project, 'docs/new-plan.md', 'docs/old-plan.md')
    assert saved['code']['lineage']['supersedes']['effective']
    assert row(project, 'docs/old-plan.md')['code']['lineage']['superseded_by']['effective']
    with pytest.raises(KnowledgeError, match='document_code_duplicate_successor'):
        link(project, 'docs/third-plan.md', 'docs/old-plan.md')
    with pytest.raises(KnowledgeError, match='document_code_link_cycle'):
        link(project, 'docs/old-plan.md', 'docs/new-plan.md')
    with pytest.raises(KnowledgeError, match='invalid_document_code_link'):
        link(project, 'docs/new-plan.md', 'docs/new-plan.md')
    packet = service.query(project, 'modern payment adapter', semantic=False, retrieval={'wiki': False, 'graph': True})
    assert {'docs/new-plan.md', 'docs/old-plan.md'} <= {c['path'] for c in packet['citations']}
    assert any(e['kind'] == 'human_reviewed:document_supersedes' for e in packet['retrieval']['edges'])
    assert (project / 'docs/design.md').read_bytes() == original
    (project / 'docs/old-plan.md').write_text('# Old plan\nLegacy payment adapter changed after review.\n', encoding='utf-8')
    stale = row(project, 'docs/new-plan.md')['code']['lineage']['supersedes']
    assert not stale['effective']
    packet = service.query(project, 'modern payment adapter', semantic=False, retrieval={'wiki': False, 'graph': True})
    assert not any(e['kind'] == 'human_reviewed:document_supersedes' for e in packet['retrieval']['edges'])
    fresh = row(project, 'docs/new-plan.md')
    removed = service.dispatch(project, 'code-link-remove', {'source': fresh['id'], 'revision': fresh['revision'],
        'expected': fresh['code']['token'], 'link_expected': fresh['code']['lineage']['token']})
    assert removed['code']['lineage']['supersedes'] is None


def test_exact_code_filter_is_current_only_and_combines_with_paths(project):
    configure(project)
    design = classify(project, 'docs/design.md', 'People enter with a secret passphrase.', 'F03')
    readme = classify(project, 'README.md', 'See [design](docs/design.md).', 'F04')
    code = design['assignment']['display']
    packet = service.query(project, 'design passphrase', semantic=False,
                           retrieval={'document_codes': [code], 'wiki': True, 'graph': True})
    assert packet['citations'] and {c['path'] for c in packet['citations']} == {'docs/design.md'}
    assert packet['retrieval']['options']['document_codes'] == [code]
    none = service.query(project, 'design passphrase', semantic=False,
                         retrieval={'document_codes': [code], 'paths': ['README.md']})
    assert not none['citations']
    with pytest.raises(KnowledgeError, match='invalid_retrieval_options'):
        service.query(project, 'design', semantic=False, retrieval={'document_codes': [code, code]})
    with pytest.raises(KnowledgeError, match='historical_document_code_filter_unavailable'):
        service.query(project, 'design', semantic=False,
                      retrieval={'intent': 'history', 'before': '2026-09-30T12:00:00+02:00', 'document_codes': [code]})
    assert readme['assignment']['display'] != code
    (project / 'docs/design.md').write_text('# Authentication\nA token replaces the passphrase.\n', encoding='utf-8')
    service.dispatch(project, 'sync', 'manual')
    changed = service.query(project, 'token passphrase', semantic=False, retrieval={'document_codes': [code]})
    assert changed['citations'] == []
