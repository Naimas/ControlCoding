"""Real-source recorder: exact anchors, draft labels and review output integrity."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('real_evaluation', Path(__file__).with_name('knowledge_real_evaluation.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fixture():
    source = {'path': 'docs/a.md', 'body': '# Recovery\nRestore a backup.\n', 'revision': 'a' * 64}
    chunk = {'id': 'chunk1', 'source': 'source1', 'revision': source['revision'], 'path': source['path'],
             'line': 1, 'end_line': 2, 'text': source['body'].rstrip('\n')}
    draft = {'label_origin': 'agent-authored; pending human validation', 'questions': [
        {'id': 'q1', 'question': 'How is recovery performed?', 'category': 'lookup', 'answerable': True,
         'split': 'held_out', 'expected': [{'path': source['path'], 'quote': 'Restore a backup.'}]}]}
    return draft, source, chunk


def test_quote_freeze_binds_real_chunk_and_source():
    draft, source, chunk = fixture()
    corpus = runner.frozen_corpus(draft, [source], [chunk])
    assert corpus['questions'][0]['expected'] == ['chunk1']
    assert corpus['evidence'][0]['source_sha256'] == source['revision']
    assert 'pending human' in corpus['label_origin']


@pytest.mark.parametrize('body', ['No matching evidence.', 'Restore a backup.\nRestore a backup.'])
def test_missing_or_ambiguous_quote_rejected(body):
    draft, source, chunk = fixture()
    source['body'] = body
    with pytest.raises(ValueError, match='exactly once'):
        runner.frozen_corpus(draft, [source], [chunk])


def test_citation_requires_full_revision_range_and_excerpt():
    _, _, chunk = fixture()
    citation = {'id': 'S1', 'source': chunk['source'], 'revision': chunk['revision'], 'path': chunk['path'],
                'line': 1, 'end_line': 2, 'excerpt': chunk['text']}
    ids, citations = runner.match_citations({'citations': [citation]}, [chunk])
    assert ids == ['chunk1'] and citations[0]['valid']
    for key, wrong in [('revision', 'b' * 64), ('line', 5), ('excerpt', 'invented'), ('path', 'wrong.md')]:
        ids, citations = runner.match_citations({'citations': [{**citation, key: wrong}]}, [chunk])
        assert not ids and not citations[0]['valid']


def test_review_exports_escaped_text_and_empty_human_grades(tmp_path):
    draft, source, chunk = fixture()
    corpus = runner.frozen_corpus(draft, [source], [chunk])
    result = {'id': 'q1', 'answer': '<script>bad()</script>', 'citations': []}
    run = {'results': [result]}
    runner.review_export(corpus, {'test-method': run}, tmp_path)
    page = (tmp_path / 'review.html').read_text(encoding='utf-8')
    assert '<script>bad()' not in page and '&lt;script&gt;' in page
    assert 'test-method' not in page
    grades = json.loads((tmp_path / 'grades-test-method-PENDING.json').read_text(encoding='utf-8'))
    assert grades['reviewer'] == '' and grades['judgments'] == []
    assert grades['run_sha256'] == runner.fingerprint(run)


def test_external_existing_output_rejected_without_project_initialization(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    with pytest.raises(ValueError, match='new external'):
        runner.main(['--project', str(project), '--output', str(project), '--questions', 'unused'])
    assert not (project / '.controlcoding').exists()


def test_long_line_labels_only_passage_containing_quote():
    draft, source, _ = fixture()
    source.update(id='source1', body='z'*1600 + 'Restore a backup.' + 'y'*1600)
    from cc_memory_lib.knowledge_sources import passages
    chunks = [{**c, 'path': source['path']} for c in passages(source)]
    corpus = runner.frozen_corpus(draft, [source], chunks)
    expected = [c['id'] for c in chunks if 'Restore a backup.' in c['text']]
    assert len(expected) == 1
    assert corpus['questions'][0]['expected'] == expected


def test_hybrid_fallback_is_not_reported_as_neural(monkeypatch):
    draft, source, chunk = fixture()
    corpus = runner.frozen_corpus(draft, [source], [chunk])
    monkeypatch.setattr(runner.service, 'query', lambda *args: {'mode': 'lexical + explicit graph', 'warning': 'offline'})
    with pytest.raises(ValueError, match='fallback'):
        runner.record(Path('.'), corpus, [chunk], 'hybrid', {})
