"""Small retained human-grade record adapter; never grades semantics automatically."""
import hashlib
import json


def corpus_identity(raw):
    return hashlib.sha256(raw).hexdigest()


def input_identity(task):
    packet = {k: task[k] for k in ('id', 'input', 'sources')}
    raw = json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def packets(corpus):
    """Separate model evidence from reference answers before any named run."""
    model_inputs = [{'id': t['id'], 'input': t['input'], 'sources': t['sources']} for t in corpus['tasks']]
    grader_reference = {'rubric': corpus['rubric'], 'tasks': [
        {k: v for k, v in t.items() if k not in ('input', 'sources')} for t in corpus['tasks']]}
    return model_inputs, grader_reference


def assess(corpus, raw_corpus, record, *, output_artifacts=None):
    """Validate declared independent human ratings and aggregate rubric thresholds.

    A passing fake record establishes this adapter's protocol only. This function
    cannot authenticate the grader, determine semantic equivalence or authorize
    a live configuration run.
    """
    def require(ok, code):
        if not ok:
            raise ValueError(code)
    require(type(record) is dict and set(record) == {
        'schemaVersion', 'corpus_sha256', 'kind', 'authorized_run', 'configuration',
        'implementation_actor', 'grader_actor', 'tasks'}, 'quality_record_invalid')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 1
            and record['corpus_sha256'] == corpus_identity(raw_corpus), 'quality_corpus_changed')
    require(json.loads(raw_corpus) == corpus, 'quality_corpus_changed')
    require(record['kind'] in ('fake_protocol', 'live_human')
            and type(record['authorized_run']) is bool, 'quality_run_invalid')
    require(all(type(record[k]) is str and record[k].strip() for k in ('implementation_actor', 'grader_actor'))
            and record['implementation_actor'] != record['grader_actor'], 'quality_grader_not_independent')
    config = record['configuration']
    require(type(config) is dict and set(config) == {'provider', 'model', 'prompt_sha256', 'extraction_policy', 'context_builder_sha256'}
            and all(type(v) is str and v.strip() for v in config.values()), 'quality_configuration_invalid')
    require(all(len(config[k]) == 64 and all(c in '0123456789abcdef' for c in config[k])
                for k in ('prompt_sha256', 'context_builder_sha256')), 'quality_configuration_unpinned')
    require(type(record['tasks']) is list and len(record['tasks']) == len(corpus['tasks']), 'quality_task_coverage')
    by_id = {t['id']: t for t in corpus['tasks']}
    if output_artifacts is not None:
        require(type(output_artifacts) is dict and set(output_artifacts) == set(by_id)
                and all(type(raw) is bytes for raw in output_artifacts.values()), 'quality_output_artifacts_invalid')
    seen, failed = set(), []
    for grade in record['tasks']:
        require(type(grade) is dict and set(grade) == {'id', 'input_sha256', 'output_sha256', 'mandatory',
            'security', 'other', 'fabricated_source', 'fabricated_approval', 'omission_claim',
            'conflicts_preserved', 'assumptions_explicit', 'questions_useful', 'plan_actionable', 'reason'}, 'quality_grade_invalid')
        require(grade['id'] in by_id and grade['id'] not in seen, 'quality_task_coverage')
        seen.add(grade['id'])
        task = by_id[grade['id']]
        require(all(type(grade[k]) is str and len(grade[k]) == 64
                    and all(c in '0123456789abcdef' for c in grade[k]) for k in ('input_sha256', 'output_sha256')), 'quality_output_unpinned')
        require(grade['input_sha256'] == input_identity(task), 'quality_input_changed')
        if output_artifacts is not None:
            require(grade['output_sha256'] == corpus_identity(output_artifacts[grade['id']]), 'quality_output_changed')
        for key in ('mandatory', 'security', 'other'):
            require(type(grade[key]) is list and len(grade[key]) == len(task[key]), 'quality_requirement_coverage')
            require(all(x in ('retained', 'escalated', 'missing') for x in grade[key]), 'quality_rating_invalid')
        flags = ('fabricated_source', 'fabricated_approval', 'omission_claim', 'conflicts_preserved',
                 'assumptions_explicit', 'questions_useful', 'plan_actionable')
        require(all(type(grade[k]) is bool for k in flags)
                and type(grade['reason']) is str and bool(grade['reason'].strip()), 'quality_grade_reason_required')
        other_fraction = sum(x == 'retained' for x in grade['other']) / len(task['other']) if task['other'] else 1
        if ('missing' in grade['mandatory'] or 'missing' in grade['security']
                or other_fraction < corpus['rubric']['otherCoverageMinimum']
                or any(grade[k] for k in ('fabricated_source', 'fabricated_approval', 'omission_claim'))
                or not all(grade[k] for k in ('conflicts_preserved', 'assumptions_explicit', 'questions_useful', 'plan_actionable'))):
            failed.append(grade['id'])
    passed = not failed
    named_live = all(config[k].strip().casefold() not in {'fake', 'deterministic', 'unknown', 'unidentified', 'latest', 'auto', 'default'}
                     for k in ('provider', 'model'))
    return {'record_passed': passed, 'failed_tasks': failed,
            'qualification': 'declared_live_human_assessment' if passed and record['kind'] == 'live_human'
                and record['authorized_run'] and named_live and output_artifacts is not None else 'unqualified',
            'output_hashes_verified': output_artifacts is not None,
            'provenance': 'Unauthenticated local actor declarations; retained semantic ratings require independent review.'}
