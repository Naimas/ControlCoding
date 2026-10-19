"""Document roles are explainable suggestions, not approval or retrieval scope."""
import pytest
from test_cc_knowledge import project
from cc_memory_lib.knowledge_document_types import classify
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError


@pytest.mark.parametrize('path,title,kind,expected,basis', [
    ('docs/architecture.md', 'System architecture', 'document', 'design', 'suggested'),
    ('PIANO_IMPLEMENTAZIONE.md', 'Design implementation plan', 'document', 'implementation_plan', 'suggested'),
    ('docs/requisiti.md', 'Requisiti', 'document', 'requirements', 'suggested'),
    ('.controlwork/memory/sources/input.md', 'Architecture report', 'sources', 'design', 'suggested'),
    ('chat:one', 'Architecture plan discussion', 'conversation', 'conversation', 'recorded'),
    ('.controlwork/sessions/one.json', 'Development', 'session', 'session_record', 'recorded'),
    ('AGENTS.md', 'Rules', 'document', 'agent_instructions', 'convention'),
    ('.github/copilot-instructions.md', 'Instructions', 'document', 'agent_instructions', 'convention'),
    ('docs/rules.md', 'Istruzioni per gli agenti', 'document', 'agent_instructions', 'suggested'),
    ('prototipe 2/contracts/DESKTOP_P2.md', 'Contratti desktop P2 — 010', 'document', 'technical_contract', 'suggested'),
    ('docs/test-plan.md', 'Testing plan', 'document', 'verification', 'suggested'),
    ('docs/ADR-001.md', 'Choose service', 'document', 'decision', 'suggested'),
    ('README.md', 'Architecture project', 'document', 'usage_guide', 'convention'),
    ('docs/note.md', 'Meeting notes', 'document', 'unclassified', 'uncertain'),
    ('docs/plan-design.md', 'Architecture plan', 'document', 'unclassified', 'uncertain'),
    ('docs/plant.md', 'Plant biology', 'document', 'unclassified', 'uncertain'),
])
def test_roles_use_record_type_or_explained_names(path, title, kind, expected, basis):
    value = classify(dict(path=path, title=title, kind=kind, body='Design plan contract conversation'))
    assert value['key'] == expected and value['basis'] == basis
    assert value['reason'] and value['purpose'] and value['label']


def read(project, role=None, offset=0, snapshot=None, kind='sources'):
    return service.dispatch(project, 'library', dict(kind=kind, query='', document_type=role, offset=offset, snapshot=snapshot))


def test_role_filter_counts_whole_library_and_binds_pagination(project):
    for n in range(65):
        (project / 'docs' / f'plan-{n}.md').write_text('# Implementation plan\nWork steps.', encoding='utf-8')
    service.reconcile(project)
    first = read(project, 'implementation_plan')
    counts = {t['key']: t['count'] for t in first['document_types']}
    assert first['total'] == counts['implementation_plan'] == 65
    assert counts['design'] == 1 and counts['usage_guide'] == 1
    assert len(first['rows']) == 50
    second = read(project, 'implementation_plan', first['next_offset'], first['snapshot'])
    assert len(second['rows']) == 15 and second['next_offset'] is None
    assert not {r['id'] for r in first['rows']} & {r['id'] for r in second['rows']}
    assert all(r['document_type']['key'] == 'implementation_plan' for r in first['rows'] + second['rows'])
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        read(project, 'design', snapshot=first['snapshot'])
    assert read(project, 'unclassified')['total'] == 0
    with pytest.raises(KnowledgeError, match='invalid_library_request'):
        read(project, 'design', kind='wiki')
    with pytest.raises(KnowledgeError, match='invalid_library_request'):
        read(project, ['design'])


def test_historical_type_does_not_borrow_new_title_and_instructions_stay_excluded(project):
    old = service.page(project, 'wiki:topic:architecture')
    assert old['reading']['sources'][0]['document_type']['key'] == 'design'
    (project / 'docs/design.md').write_text('# Implementation plan\nChanged function.', encoding='utf-8')
    (project / 'AGENTS.md').write_text('# Instructions\nDo not ingest this sentinel.', encoding='utf-8')
    service.reconcile(project)
    source = old['dependencies'][0]['source']
    historical = service.page(project, source, service.page(project, source)['history'][-1]['revision'])
    assert historical['reading']['sources'][0]['document_type']['key'] == 'unclassified'
    assert all(r['path'] != 'AGENTS.md' for r in read(project)['rows'])
    assert read(project, 'agent_instructions')['total'] == 0


def test_source_lookup_and_wiki_cards_share_classification(project):
    page = service.page(project, 'wiki:topic:architecture')
    card = page['reading']['sources'][0]
    lookup = service.dispatch(project, 'graph-source', card['id'])
    row = next(r for r in read(project)['rows'] if r['id'] == card['id'])
    assert card['document_type'] == lookup['document_type'] == row['document_type']
