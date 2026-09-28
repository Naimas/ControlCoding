"""Current approved memory cites only fresh original source passages."""
import pytest

from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_consolidation as mc
from cc_memory_lib import knowledge_service as service
from cc_memory_lib import knowledge_wiki_review as wiki_review
from cc_memory_lib.knowledge_consolidation_context import read
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import database, get


@pytest.fixture
def approved(project, tmp_path):
    migrate(project, tmp_path / 'pre-migration.ccmemory')
    view = mc.dispatch(project, 'consolidation-create', {'title': 'Review authentication'})
    citation = next(c for c in view['sources'] if c['path'] == 'docs/design.md')
    view = mc.dispatch(project, 'consolidation-propose', {
        'job': view['job']['id'], 'page_type': 'overview', 'key': 'authentication',
        'kind': 'summary', 'title': 'Authentication',
        'body': 'The source describes a passphrase [S1].',
        'reason': 'Cited summary', 'citations': [citation]})
    mc.dispatch(project, 'consolidation-decide', {
        'job': view['job']['id'], 'ids': [view['job']['proposals'][0]['id']],
        'decision': 'accept', 'request_id': 'accept-1'})
    return project, citation


def test_absent_and_v1_are_empty_without_migration(tmp_path, project):
    assert read(tmp_path)['schema'] == 0
    assert read(project)['schema'] == 1
    assert read(project)['claims'] == []


def test_current_hydrates_original_and_backlinks(approved):
    root, anchor = approved
    result = read(root)
    assert result['schema'] >= 2 and result['total'] == 1
    claim = result['claims'][0]
    assert claim['review_status'] == 'approved' and claim['current_status'] == 'current'
    assert claim['epistemic_status'] == 'unknown'  # v2 cannot infer authority from prose.
    assert claim['body'] != claim['evidence'][0]['excerpt']
    assert claim['evidence'][0]['origin'] == 'original_source'
    assert claim['evidence'][0]['source'] == anchor['source']
    assert result['backlinks'][0]['claim'] == claim['id']
    assert read(root, {'source': anchor['source']})['total'] == 1
    assert read(root, {'source': 'source:other'})['claims'] == []
    assert read(root, {'query': 'passphrase'})['total'] == 1
    assert read(root, {'query': 'Why does the authentication source mention a passphrase?'})['total'] == 1
    assert read(root, {'query': 'unrelated'})['total'] == 0
    assert read(root, {'history': claim['id']})['history'][0]['current_status'] == 'historical'


def test_epistemic_status_is_separate_from_review_status(approved):
    root, _ = approved
    claim_id = read(root)['claims'][0]['id']
    with database(root) as db:
        with db:
            db.execute('INSERT INTO consolidation_claim_metadata VALUES(?,?,?)',
                       (claim_id, 'disputed', 'branch/main'))
    claim = read(root)['claims'][0]
    assert claim['review_status'] == 'approved'
    assert claim['epistemic_status'] == 'disputed' and claim['scope'] == 'branch/main'


def test_disk_source_change_excludes_claim_without_explicit_refresh(approved):
    root, _ = approved
    (root / 'docs/design.md').write_text('# Authentication\nA token replaces the passphrase.\n', encoding='utf-8')
    assert read(root)['claims'] == []


def test_human_replacement_excludes_claim(approved):
    root, citation = approved
    with database(root) as db:
        with db:
            sections = get(db, wiki_review.STATE)
            sections['overview']['authentication'] = {
                'title': 'Human correction', 'body': 'Human replacement [S1].',
                'dependencies': [dict(citation, citation='S1')], 'approved': 'manual'}
            wiki_review.commit_sections(db, sections, [])
    assert read(root)['claims'] == []


def test_query_window_and_invalid_history(approved):
    root, _ = approved
    result = read(root, {'limit': 1, 'offset': 1})
    assert result['total'] == 1 and result['claims'] == [] and result['next_offset'] is None
    with pytest.raises(ValueError, match='invalid_consolidation_context_request'):
        read(root, {'history': 'not-a-claim'})


def test_forget_removes_original_and_derived_context(project, tmp_path):
    service.conversation(project, record())
    service.reconcile(project)
    migrate(project, tmp_path / 'before-conversation.ccmemory')
    view = mc.dispatch(project, 'consolidation-create', {'title': 'Review conversation'})
    citation = next(c for c in view['sources'] if c['source'] == 'conversation:chat-1')
    view = mc.dispatch(project, 'consolidation-propose', {
        'job': view['job']['id'], 'page_type': 'overview', 'key': 'conversation_note',
        'kind': 'summary', 'title': 'Conversation note',
        'body': 'The imported conversation mentions a private phrase [S1].',
        'reason': 'Review import provenance', 'citations': [citation]})
    mc.dispatch(project, 'consolidation-decide', {
        'job': view['job']['id'], 'ids': [view['job']['proposals'][0]['id']],
        'decision': 'accept', 'request_id': 'accept-conversation'})
    claim_id = read(project)['claims'][0]['id']
    service.dispatch(project, 'forget', 'chat-1')
    result = read(project, {'history': claim_id})
    assert result['claims'] == [] and result['history'] == []
    assert 'apricot' not in str(result).lower()


def test_long_line_chunk_hydrates_exact_indexed_original(project, tmp_path):
    (project / 'docs/design.md').write_text('A' * 1600 + 'B' * 199, encoding='utf-8')
    service.reconcile(project)
    migrate(project, tmp_path / 'long-line-before.ccmemory')
    view = mc.dispatch(project, 'consolidation-create', {'title': 'Review long source line'})
    citation = next(c for c in view['sources'] if c['path'] == 'docs/design.md')
    assert '\n' in citation['excerpt']  # The index bounds one physical line into pieces.
    view = mc.dispatch(project, 'consolidation-propose', {
        'job': view['job']['id'], 'page_type': 'overview', 'key': 'long_line',
        'kind': 'summary', 'title': 'Long line source',
        'body': 'The source contains a long line [S1].',
        'reason': 'Trace the indexed original', 'citations': [citation]})
    mc.dispatch(project, 'consolidation-decide', {
        'job': view['job']['id'], 'ids': [view['job']['proposals'][0]['id']],
        'decision': 'accept', 'request_id': 'accept-long-line'})
    hydrated = read(project)['claims'][0]['evidence'][0]
    assert hydrated['excerpt'] == citation['excerpt']
    assert hydrated['origin'] == 'original_source'
