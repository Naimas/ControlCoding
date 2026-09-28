"""Original -> reviewed work -> retrieval -> synthesis -> mutation/recovery journeys."""
import json
from datetime import datetime, timezone

import pytest
from test_cc_knowledge import project
from test_cc_knowledge_ranking import seed
from test_cc_knowledge_wiki_review import proposal
from cc_memory_lib import knowledge_service as service, knowledge_wiki_review as review
from cc_memory_lib import knowledge_wiki_tools as wiki_tools, knowledge_backup
from cc_memory_lib.knowledge_store import database, get, put, KnowledgeError


def query(root, text='needle', **options):
    return service.dispatch(root, 'query', {'text': text, 'semantic': False, 'retrieval': options})


def test_two_hops_cycles_and_equal_budget_ablation(tmp_path):
    seed(tmp_path, {'a': 'needle', 'b': 'because of measured load', 'c': 'proof receipt', 'd': 'beyond two hops'},
         [('a', 'b'), ('b', 'c'), ('b', 'a'), ('c', 'd')])
    result = query(tmp_path, wiki=False)
    assert {c['source'] for c in result['citations']} == {'a', 'b', 'c'}
    assert all(len(s['path']) <= 2 for s in result['retrieval']['selected'])
    assert len({c['source'] for c in result['citations']}) == len(result['citations'])
    lexical = query(tmp_path, graph=False, wiki=False)
    assert [c['source'] for c in lexical['citations']] == ['a']
    assert result['retrieval']['budgets']['passages'] == lexical['retrieval']['budgets']['passages'] == 10


def test_filters_cannot_expand_or_disclose_outside_scope(tmp_path):
    seed(tmp_path, {'a': 'needle', 'private': 'secret'}, [('a', 'private')])
    result = query(tmp_path, paths=['docs/a.md'])
    assert [c['source'] for c in result['citations']] == ['a']
    assert 'secret' not in json.dumps(result) and 'private' not in json.dumps(result)
    assert not result['edges']


def test_pending_stale_and_approved_work_edges(tmp_path):
    seed(tmp_path, {'a': 'needle', 'b': 'supporting receipt'})
    state = service.dispatch(tmp_path, 'work-view')
    pending = service.dispatch(tmp_path, 'work-propose', dict(snapshot=state['snapshot'], source='b', target='a',
        source_role='evidence', target_role='activity', kind='evidences', reason='Receipt supports activity'))
    assert len(query(tmp_path, wiki=False)['citations']) == 1
    approved = service.dispatch(tmp_path, 'work-review', dict(snapshot=pending['snapshot'], id=pending['relations'][0]['id'], status='approved', reason='Reviewed exact revisions'))
    assert len(query(tmp_path, wiki=False)['citations']) == 2
    with database(tmp_path) as db, db:
        db.execute("UPDATE sources SET revision='changed' WHERE id='b'")
    result = query(tmp_path, wiki=False)
    assert len(result['citations']) == 1
    assert any(e['reason'] == 'stale_or_unapproved_relation' for e in result['retrieval']['excluded'])


def test_stale_explicit_edge_and_graph_budget(tmp_path):
    passages = {'a': 'needle'} | {f'n{i}': 'neighbor evidence' for i in range(80)}
    seed(tmp_path, passages, [('a', f'n{i}') for i in range(80)])
    result = query(tmp_path, wiki=False)
    assert len(result['citations']) <= 10 and result['retrieval']['truncated']
    assert len(result['retrieval']['paths']) <= 40
    with database(tmp_path) as db, db:
        db.execute("UPDATE edges SET revision='stale'")
    assert len(query(tmp_path, wiki=False)['citations']) == 1


def test_wiki_summary_hydrates_original_and_not_prose(project):
    value = proposal(project, body='The codename is nebula [S1].')
    pending = review.dispatch(project, 'wiki-review-propose', value)
    review.dispatch(project, 'wiki-review-decide', {'id': pending['id'], 'decision': 'accept'})
    result = query(project, 'nebula', graph=False)
    assert result['citations'] and result['retrieval']['wiki']
    assert all('nebula' not in c['excerpt'] and not c['source'].startswith('wiki:') for c in result['citations'])
    assert not query(project, 'nebula', graph=False, wiki=False)['citations']
    (project / 'docs/design.md').write_text('# Authentication\nReplaced source.\n', encoding='utf-8')
    service.reconcile(project)
    assert not query(project, 'nebula')['citations']


def test_recorded_history_cutoff_never_current_evidence(project):
    cutoff = datetime.now(timezone.utc).isoformat()
    result = query(project, 'passphrase', before=cutoff)
    assert result['historical'] and result['citations']
    assert all(c['historical'] for c in result['citations'])
    assert not query(project, 'passphrase', before='2000-01-01T00:00:00+00:00')['citations']
    with pytest.raises(KnowledgeError, match='history_cutoff_required'):
        query(project, intent='history')


@pytest.mark.parametrize('options', [{'paths':['../secret']}, {'paths':['C:/secret']}, {'graph':'yes'},
    {'intent':'invent'}, {'before':'2026-09-28'}, {'paths':'docs'}, {'unknown':True}])
def test_invalid_filters_rejected(project, options):
    with pytest.raises(KnowledgeError):
        query(project, **options)


def test_findings_persist_scope_classification_and_stale_review(project):
    (project / 'docs/design.md').write_text('# Architecture\nStorage: PostgreSQL\n', encoding='utf-8')
    (project / 'docs/alternative.md').write_text('# Alternative design\nStorage: Redis\n', encoding='utf-8')
    service.reconcile(project)
    review.dispatch(project, 'wiki-review-view')
    view = service.dispatch(project, 'wiki-tools-view', {'page':'wiki:review:overview'})
    finding = next(f for f in view['findings'] if f['kind'] == 'possible_disagreement')
    decided = service.dispatch(project, 'wiki-tools-decide', dict(page=view['page'],snapshot=view['snapshot'],id=finding['id'],status='different_scope',reason='Redis is a proposed cache, not the selected database'))
    assert decided['findings'][0]['status'] == 'different_scope'
    (project / 'docs/alternative.md').write_text('# Alternative design\nStorage: SQLite\n', encoding='utf-8')
    service.reconcile(project)
    current = service.dispatch(project, 'wiki-tools-view', {'page':view['page']})
    old = next(f for f in current['findings'] if f['id'] == finding['id'])
    assert old['issues'] and old['status'] == 'different_scope'
    with pytest.raises(KnowledgeError, match='source_changed'):
        service.dispatch(project, 'wiki-tools-decide', dict(page=view['page'],snapshot=current['snapshot'],id=finding['id'],status='conflict',reason='Recheck both sources'))


def test_synthesis_adoption_diff_recovery_and_reopen(project, tmp_path):
    review.dispatch(project, 'wiki-review-view')
    packet = query(project, 'passphrase', graph=False, wiki=False)
    draft = service.dispatch(project, 'wiki-draft', dict(title='Project synthesis', body='Entry requires a passphrase [S1].',citations=packet['citations'],generation=packet['generation'],provider='manual',model='external-chat'))
    page = service.page(project, 'wiki:review:overview')
    adopted = service.dispatch(project, 'wiki-tools-adopt', dict(page=page['id'],draft=draft['id'],key='entry',base_revision=page['revision']))
    assert adopted['proposal']['status'] == 'pending'
    review.dispatch(project, 'wiki-review-decide', {'id':adopted['proposal']['id'],'decision':'accept'})
    first = service.page(project, page['id'])
    replacement = review.dispatch(project, 'wiki-review-propose', proposal(project, key='entry',body='A secret is used to enter [S1].'))
    review.dispatch(project, 'wiki-review-decide', {'id':replacement['id'],'decision':'accept'})
    compared = service.dispatch(project, 'wiki-tools-compare', dict(page=page['id'],revision=first['revision']))
    assert '-Entry requires' in compared['comparison']['diff'] and '+A secret' in compared['comparison']['diff']
    saved = next(h for h in compared['history'] if h['key'] == 'entry')
    recovered = service.dispatch(project, 'wiki-tools-recover', dict(page=page['id'],snapshot=saved['snapshot'],key='entry',base_revision=compared['revision']))
    assert recovered['proposal']['body'] == 'Entry requires a passphrase [S1].'
    assert 'A secret' in service.page(project, page['id'])['body']
    review.dispatch(project, 'wiki-review-decide', {'id':recovered['proposal']['id'],'decision':'accept'})
    archive = tmp_path/'wiki.ccmemory'
    knowledge_backup.backup(project, archive)
    restored = tmp_path/'restored';restored.mkdir()
    knowledge_backup.restore(restored,archive)
    with database(restored) as db:
        assert get(db,wiki_tools.HISTORY)


def test_work_to_wiki_reverse_navigation_and_source_mutation(project):
    review.dispatch(project, 'wiki-review-view')
    packet = query(project, 'passphrase')
    sources = service.catalog(project)['sources']
    a,b=sources[:2]
    state=service.dispatch(project,'work-view')
    proposed=service.dispatch(project,'work-propose',dict(snapshot=state['snapshot'],source=a['id'],target=b['id'],source_role='objective',target_role='evidence',kind='evidences',reason='Reviewed work evidence'))
    service.dispatch(project,'work-review',dict(snapshot=proposed['snapshot'],id=proposed['relations'][0]['id'],status='approved',reason='Original revisions verified'))
    view=service.dispatch(project,'wiki-tools-view',{'page':'wiki:review:overview'})
    assert view['relations'][0]['effective'] and view['backlinks'] and view['sources']
    (project/'docs/design.md').unlink();service.reconcile(project)
    current=service.dispatch(project,'wiki-tools-view',{'page':'wiki:review:overview'})
    assert not current['relations'][0]['effective']


def test_privacy_purge_erases_findings_and_section_snapshots(project):
    value=proposal(project)
    for body in ('Private detail [S1].','Replacement detail [S1].'):
        pending=review.dispatch(project,'wiki-review-propose',{**value,'body':body,'base_revision':service.page(project,'wiki:review:overview')['revision']})
        review.dispatch(project,'wiki-review-decide',{'id':pending['id'],'decision':'accept'})
    source=value['citations'][0]['source']
    with database(project) as db,db:
        assert get(db,wiki_tools.HISTORY)
        review.purge_source(db,source)
        assert not get(db,wiki_tools.HISTORY)


def test_uncited_multiblock_synthesis_rejected(project):
    with pytest.raises(KnowledgeError,match='uncited_paragraph'):
        review.dispatch(project,'wiki-review-propose',proposal(project,body='Supported [S1].\n\nUnsupported conclusion.'))
