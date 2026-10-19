"""Negative controls for independently declared semantic-quality records."""
import copy
import json
from pathlib import Path

import pytest

from planning_quality_records import assess, corpus_identity, input_identity, packets

RAW = (Path(__file__).parent / 'fixtures' / 'planning-quality-corpus-v1.json').read_bytes()
CORPUS = json.loads(RAW)


def record():
    return {'schemaVersion': 1, 'corpus_sha256': corpus_identity(RAW), 'kind': 'fake_protocol', 'authorized_run': False,
        'configuration': {'provider': 'fake', 'model': 'deterministic', 'prompt_sha256': 'a' * 64,
                          'extraction_policy': 'pw02-selected-v1', 'context_builder_sha256': 'b' * 64},
        'implementation_actor': 'producer', 'grader_actor': 'independent-reviewer', 'tasks': [
            {'id': t['id'], 'input_sha256': input_identity(t), 'output_sha256': 'd' * 64,
             'mandatory': ['retained'] * len(t['mandatory']), 'security': ['retained'] * len(t['security']),
             'other': ['retained'] * len(t['other']), 'fabricated_source': False, 'fabricated_approval': False,
             'omission_claim': False, 'conflicts_preserved': True, 'assumptions_explicit': True,
             'questions_useful': True, 'plan_actionable': True, 'reason': 'Synthetic declared ratings exercise record protocol only.'}
            for t in CORPUS['tasks']]}


def test_model_packet_does_not_contain_grading_reference():
    model, grader = packets(CORPUS)
    assert len(model) >= 12
    assert all(set(t) == {'id', 'input', 'sources'} for t in model)
    assert all('mandatory' in t and 'security' in t for t in grader['tasks'])
    assert 'otherCoverageMinimum' not in json.dumps(model)


def test_frozen_identity_and_fake_success_cannot_qualify_live_assistance():
    assert corpus_identity(RAW) == 'f1cdfb4e70779973d87b4153d844539dddf639e2be942bdb9010019d0204427e'
    result = assess(CORPUS, RAW, record())
    assert result['record_passed'] and result['qualification'] == 'unqualified'


@pytest.mark.parametrize('flag', ['fabricated_source', 'fabricated_approval', 'omission_claim'])
def test_valid_record_with_semantic_fabrication_or_omission_fails(flag):
    grade = record()
    grade['tasks'][1][flag] = True
    assert assess(CORPUS, RAW, grade)['failed_tasks'] == [grade['tasks'][1]['id']]


@pytest.mark.parametrize('key,index', [('mandatory', 0), ('security', 5), ('other', 9)])
def test_missing_condition_fails_even_with_pinned_outputs(key, index):
    grade = record()
    grade['tasks'][index][key][0] = 'missing'
    assert not assess(CORPUS, RAW, grade)['record_passed']


def test_security_escalation_is_explicit_but_other_escalation_is_not_coverage():
    grade = record()
    grade['tasks'][5]['security'][0] = 'escalated'
    assert assess(CORPUS, RAW, grade)['record_passed']
    grade['tasks'][0]['other'][0] = 'escalated'
    assert not assess(CORPUS, RAW, grade)['record_passed']


@pytest.mark.parametrize('change,code', [
    ('same_actor', 'quality_grader_not_independent'), ('duplicate', 'quality_task_coverage'),
    ('changed_corpus', 'quality_corpus_changed'), ('unpinned', 'quality_output_unpinned')])
def test_invalid_review_evidence_rejected(change, code):
    grade = record()
    if change == 'same_actor': grade['grader_actor'] = grade['implementation_actor']
    if change == 'duplicate': grade['tasks'][1] = copy.deepcopy(grade['tasks'][0])
    if change == 'changed_corpus': grade['corpus_sha256'] = '0' * 64
    if change == 'unpinned': grade['tasks'][0]['output_sha256'] = ''
    with pytest.raises(ValueError, match=code): assess(CORPUS, RAW, grade)


def test_unauthorized_live_record_is_still_unqualified():
    grade = record()
    grade['kind'] = 'live_human'
    assert assess(CORPUS, RAW, grade)['qualification'] == 'unqualified'


def test_fake_configuration_cannot_be_relabelled_as_live_quality():
    grade = record()
    grade['kind'] = 'live_human'
    grade['authorized_run'] = True
    assert assess(CORPUS, RAW, grade)['qualification'] == 'unqualified'


def test_digest_from_another_task_cannot_pin_current_input():
    grade = record()
    grade['tasks'][0]['input_sha256'] = grade['tasks'][1]['input_sha256']
    with pytest.raises(ValueError, match='quality_input_changed'):
        assess(CORPUS, RAW, grade)


def test_parsed_grader_reference_cannot_change_under_original_corpus_pin():
    changed = copy.deepcopy(CORPUS)
    changed['rubric']['otherCoverageMinimum'] = 0
    with pytest.raises(ValueError, match='quality_corpus_changed'):
        assess(changed, RAW, record())


def test_retained_output_artifact_digest_is_checked_without_grading_its_text():
    grade = record()
    outputs = {t['id']: b'Independent retained output bytes' for t in CORPUS['tasks']}
    for task in grade['tasks']: task['output_sha256'] = corpus_identity(outputs[task['id']])
    assert assess(CORPUS, RAW, grade, output_artifacts=outputs)['output_hashes_verified']
    assert assess(CORPUS, RAW, grade, output_artifacts=outputs)['qualification'] == 'unqualified'
    outputs[grade['tasks'][0]['id']] += b' changed'
    with pytest.raises(ValueError, match='quality_output_changed'):
        assess(CORPUS, RAW, grade, output_artifacts=outputs)


def test_live_label_without_output_artifacts_remains_unqualified():
    grade = record()
    grade.update(kind='live_human', authorized_run=True)
    grade['configuration'].update(provider='named-provider', model='named-model')
    assert assess(CORPUS, RAW, grade)['qualification'] == 'unqualified'
