"""Frozen evaluation must not turn incomplete or contaminated evidence into a pass."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib.knowledge_evaluation import evaluate, fingerprint
from cc_knowledge_evaluate import main, read_packet


def packets(count=120):
    categories = ['lookup', 'multi_source', 'history', 'contradiction',
                  'current_vs_planned', 'work', 'unanswerable']
    questions, results, judgments = [], [], []
    for i in range(count):
        category = categories[i % len(categories)]
        answerable = category != 'unanswerable'
        identifier = 'q' + str(i)
        questions.append({'id': identifier, 'question': 'Fixture question ' + identifier, 'split': 'calibration' if i < 14 else 'held_out',
                          'category': category, 'answerable': answerable,
                          'expected': ['e1', 'e2'] if answerable else []})
        results.append({'id': identifier, 'retrieved': ['e1', 'e2'] if answerable else [],
                        'abstained': not answerable, 'citations': [{'id': 'S1', 'valid': True}] if answerable else [],
                        'isolation_leaks': 0, 'latency_ms': 1.0})
        if answerable:
            judgments.append({'question_id': identifier, 'citation_id': 'S1', 'supported': True})
    corpus = {'schema_version': 1, 'questions': questions,
              'evidence': [{'id': name, 'source_sha256': 'a' * 64, 'locator': 'fixture.md:1-4'}
                           for name in ['e1', 'e2'] + ['noise' + str(i) for i in range(9)]]}
    run = {'schema_version': 1, 'corpus_sha256': fingerprint(corpus),
           'metadata': {key: 'fixture' for key in ('candidate', 'model', 'prompt', 'hardware', 'policy')},
           'results': results}
    grades = {'schema_version': 1, 'run_sha256': fingerprint(run),
              'reviewer': 'Test fixture only; not a human review', 'judgments': judgments}
    return corpus, run, grades


def test_complete_fixture_scores_but_does_not_certify_independence():
    corpus, run, grades = packets()
    result = evaluate(corpus, run, grades)
    assert result['quality_thresholds_met'] is True
    assert result['metrics']['recall_at_10'] == 1
    assert result['metrics']['citation_support'] == 1
    assert result['by_split']['held_out']['abstention_accuracy'] == 1
    assert 'c11_complete' not in result


def test_no_grades_and_small_corpus_cannot_pass():
    corpus, run, _ = packets(7)
    result = evaluate(corpus, run)
    assert not result['quality_thresholds_met']
    assert result['metrics']['citation_support'] is None
    assert result['metrics']['grade_coverage'] == 0
    assert result['issues']


def test_empty_corpus_has_no_successful_denominators():
    corpus, run, grades = packets(0)
    result = evaluate(corpus, run, grades)
    assert not result['quality_thresholds_met']
    for name in ('recall_at_10', 'abstention_accuracy', 'anchor_validity', 'citation_support', 'grade_coverage'):
        assert result['metrics'][name] is None


def test_recall_counts_only_first_ten_and_all_relevant_evidence():
    corpus, run, _ = packets(1)
    run['results'][0]['retrieved'] = ['e1'] + ['noise' + str(i) for i in range(9)] + ['e2']
    result = evaluate(corpus, run)
    assert result['metrics']['recall_at_10'] == .5


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unexpected', 'corpus_drift', 'grade_drift', 'duplicate_grade', 'unknown_citation'])
def test_inconsistent_packets_rejected(mutation):
    corpus, run, grades = packets()
    if mutation == 'missing':
        run['results'].pop()
    elif mutation == 'duplicate':
        run['results'].append(copy.deepcopy(run['results'][0]))
    elif mutation == 'unexpected':
        run['results'][0]['id'] = 'unknown'
    elif mutation == 'corpus_drift':
        corpus['questions'][0]['expected'] = ['different']
    elif mutation == 'grade_drift':
        grades['run_sha256'] = '0' * 64
    elif mutation == 'duplicate_grade':
        grades['judgments'].append(copy.deepcopy(grades['judgments'][0]))
    else:
        grades['judgments'][0]['citation_id'] = 'not-emitted'
    with pytest.raises(ValueError):
        evaluate(corpus, run, grades)


@pytest.mark.parametrize('field,value', [('latency_ms', float('nan')), ('latency_ms', -1),
                                        ('latency_ms', True), ('isolation_leaks', -1),
                                        ('isolation_leaks', True), ('abstained', 'false')])
def test_invalid_observations_rejected(field, value):
    corpus, run, _ = packets()
    run['results'][0][field] = value
    with pytest.raises(ValueError):
        evaluate(corpus, run)


@pytest.mark.parametrize('failure', ['leak', 'anchor', 'abstention', 'support', 'coverage', 'recall'])
def test_heldout_failures_remain_visible(failure):
    corpus, run, grades = packets()
    for question, result in zip(corpus['questions'], run['results']):
        if question['split'] != 'held_out':
            continue
        if failure == 'leak':
            result['isolation_leaks'] = 1
        elif failure == 'anchor' and result['citations']:
            result['citations'][0]['valid'] = False
        elif failure == 'abstention' and not question['answerable']:
            result['abstained'] = False
        elif failure == 'recall':
            result['retrieved'] = []
    if failure == 'support':
        for grade in grades['judgments']:
            grade['supported'] = False
    if failure == 'coverage':
        grades['judgments'].pop()
    grades['run_sha256'] = fingerprint(run)
    result = evaluate(corpus, run, grades)
    assert not result['quality_thresholds_met']
    assert result['issues']


def test_canonical_fingerprint_ignores_object_key_order():
    assert fingerprint({'b': 2, 'a': 1}) == fingerprint({'a': 1, 'b': 2})


def test_categories_cannot_be_hidden_in_calibration():
    corpus, run, grades = packets()
    for question in corpus['questions']:
        if question['category'] == 'contradiction':
            question['split'] = 'calibration'
    run['corpus_sha256'] = fingerprint(corpus)
    grades['run_sha256'] = fingerprint(run)
    assert not evaluate(corpus, run, grades)['quality_thresholds_met']


def test_unanswerable_citations_cannot_replace_answerable_support():
    corpus, run, grades = packets()
    grades['judgments'] = []
    for question, result in zip(corpus['questions'], run['results']):
        result['citations'] = [] if question['answerable'] else [{'id': 'S1', 'valid': True}]
        if result['citations']:
            grades['judgments'].append({'question_id': question['id'], 'citation_id': 'S1', 'supported': True})
    grades['run_sha256'] = fingerprint(run)
    report = evaluate(corpus, run, grades)
    assert report['metrics']['citation_support'] is None
    assert not report['quality_thresholds_met']


def test_abstaining_on_all_answerable_questions_cannot_pass():
    corpus, run, grades = packets()
    for result in run['results']:
        result['abstained'] = True
    grades['run_sha256'] = fingerprint(run)
    report = evaluate(corpus, run, grades)
    assert report['metrics']['answerable_response_coverage'] == 0
    assert not report['quality_thresholds_met']


def test_unanswerable_category_cannot_have_positive_evidence():
    corpus, run, _ = packets()
    corpus['questions'][0]['category'] = 'unanswerable'
    run['corpus_sha256'] = fingerprint(corpus)
    with pytest.raises(ValueError):
        evaluate(corpus, run)


@pytest.mark.parametrize('mutation', ['missing_text', 'bad_source_hash', 'unknown_evidence', 'huge_latency'])
def test_source_and_question_identity_are_required(mutation):
    corpus, run, _ = packets()
    if mutation == 'missing_text':
        del corpus['questions'][0]['question']
    elif mutation == 'bad_source_hash':
        corpus['evidence'][0]['source_sha256'] = 'unversioned'
    elif mutation == 'unknown_evidence':
        run['results'][0]['retrieved'].append('not-in-frozen-corpus')
    else:
        run['results'][0]['latency_ms'] = 10 ** 400
    run['corpus_sha256'] = fingerprint(corpus)
    with pytest.raises(ValueError):
        evaluate(corpus, run)


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"n": NaN}', '{"n": Infinity}'])
def test_cli_rejects_ambiguous_json(tmp_path, text):
    path = tmp_path / 'invalid.json'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        read_packet(path)


def test_cli_reports_pending_and_malformed_separately(tmp_path, capsys):
    corpus, run, _ = packets(7)
    a, b = tmp_path / 'corpus.json', tmp_path / 'run.json'
    a.write_text(json.dumps(corpus), encoding='utf-8')
    b.write_text(json.dumps(run), encoding='utf-8')
    assert main(['--corpus', str(a), '--run', str(b)]) == 1
    assert json.loads(capsys.readouterr().out)['quality_thresholds_met'] is False
    b.write_text('{', encoding='utf-8')
    assert main(['--corpus', str(a), '--run', str(b)]) == 2
    assert 'error' in json.loads(capsys.readouterr().err)


def test_real_cli_process_pass_and_changed_run_rejects_grades(tmp_path):
    corpus, run, grades = packets()
    paths = [tmp_path / name for name in ('corpus.json', 'run.json', 'grades.json')]
    for path, packet in zip(paths, (corpus, run, grades)):
        path.write_text(json.dumps(packet), encoding='utf-8')
    command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts/cc_knowledge_evaluate.py'),
               '--corpus', str(paths[0]), '--run', str(paths[1]), '--grades', str(paths[2])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['quality_thresholds_met']
    run['metadata']['candidate'] = 'changed candidate'
    paths[1].write_text(json.dumps(run), encoding='utf-8')
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert 'run_sha256' in json.loads(result.stderr)['error']
