"""Blinded reviews become grades only for the bound, validated run."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_knowledge_review import convert
from cc_memory_lib.knowledge_evaluation import fingerprint


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'cc_knowledge_review.py'


def packets():
    run = {'schema_version': 1, 'metadata': {'candidate': 'test'}, 'results': [
        {'id': 'q1', 'citations': [{'id': 'S1'}, {'id': 'S2'}]},
        {'id': 'q2', 'citations': [{'id': 'S1'}]},
    ]}
    digest = fingerprint(run)
    other = '0' * 64 if digest != '0' * 64 else '1' * 64
    key = [{'review_id': 'blind-a', 'method': 'graph', 'question_id': 'q1', 'run_sha256': digest},
           {'review_id': 'blind-b', 'method': 'graph', 'question_id': 'q2', 'run_sha256': digest},
           {'review_id': 'blind-other', 'method': 'lexical', 'question_id': 'q1', 'run_sha256': other}]
    review = {'schema_version': 1, 'reviewer': 'Reviewer One', 'judgments': [
        {'review_id': 'blind-other', 'citation_id': 'S9', 'supported': False},
        {'review_id': 'blind-b', 'citation_id': 'S1', 'supported': False},
        {'review_id': 'blind-a', 'citation_id': 'S2', 'supported': True},
    ]}
    return key, review, run


def test_convert_filters_other_run_and_preserves_human_labels():
    key, review, run = packets()
    grades = convert(key, review, run)
    assert grades == {'schema_version': 1, 'run_sha256': fingerprint(run),
                      'reviewer': 'Reviewer One', 'judgments': [
                          {'question_id': 'q2', 'citation_id': 'S1', 'supported': False},
                          {'question_id': 'q1', 'citation_id': 'S2', 'supported': True}]}
    assert convert(key, {**review, 'judgments': []}, run)['judgments'] == []


@pytest.mark.parametrize('mutation', [
    'changed_run', 'duplicate_review_id', 'missing_key_question', 'unknown_review_id',
    'duplicate_judgment', 'conflicting_judgment', 'wrong_citation', 'wrong_type',
    'blank_reviewer', 'duplicate_run_citation',
])
def test_invalid_or_ambiguous_mapping_rejected(mutation):
    key, review, run = packets()
    if mutation == 'changed_run':
        run['metadata']['candidate'] = 'changed'
    elif mutation == 'duplicate_review_id':
        key[2]['review_id'] = key[0]['review_id']
    elif mutation == 'missing_key_question':
        key.pop(1)
    elif mutation == 'unknown_review_id':
        review['judgments'][0]['review_id'] = 'absent'
    elif mutation in ('duplicate_judgment', 'conflicting_judgment'):
        row = dict(review['judgments'][1])
        if mutation == 'conflicting_judgment':
            row['supported'] = not row['supported']
        review['judgments'].append(row)
    elif mutation == 'wrong_citation':
        review['judgments'][1]['citation_id'] = 'S9'
    elif mutation == 'wrong_type':
        review['judgments'][1]['supported'] = 'true'
    elif mutation == 'blank_reviewer':
        review['reviewer'] = ' '
    elif mutation == 'duplicate_run_citation':
        run['results'][0]['citations'].append({'id': 'S1'})
    with pytest.raises(ValueError):
        convert(key, review, run)


def test_cli_emits_evaluator_schema_and_rejects_ambiguous_json(tmp_path):
    key, review, run = packets()
    paths = {name: tmp_path / (name + '.json') for name in ('key', 'review', 'run')}
    for name, value in [('key', key), ('review', review), ('run', run)]:
        paths[name].write_text(json.dumps(value), encoding='utf-8')
    command = [sys.executable, '-B', str(SCRIPT), '--key', str(paths['key']),
               '--review', str(paths['review']), '--run', str(paths['run'])]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == convert(key, review, run)
    paths['review'].write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 2
    assert 'Duplicate JSON key' in json.loads(completed.stderr)['error']
