"""Offline, reproducible scoring for a frozen knowledge retrieval evaluation.

This module scores recorded runs and separately supplied human judgments. It does
not call a model, inspect a project, or establish a reviewer's identity.
"""

import hashlib
import json
import math
import re


REQUIRED_CATEGORIES = frozenset({
    'lookup', 'multi_source', 'history', 'contradiction',
    'current_vs_planned', 'work', 'unanswerable',
})


def fingerprint(value):
    """SHA-256 of canonical sorted, compact UTF-8 JSON (without NaN)."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError('value is not canonical JSON') from exc
    return hashlib.sha256(encoded).hexdigest()


def _object(value, name):
    if type(value) is not dict:
        raise ValueError(name + ' must be an object')
    return value


def _array(value, name):
    if type(value) is not list:
        raise ValueError(name + ' must be an array')
    return value


def _string(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(name + ' must be a nonempty string')
    return value


def _unique_strings(value, name):
    values = _array(value, name)
    for item in values:
        _string(item, name + ' item')
    if len(values) != len(set(values)):
        raise ValueError(name + ' contains duplicates')
    return values


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _metrics(rows, judgments):
    answerable = [(q, r) for q, r in rows if q['answerable']]
    unanswerable = [(q, r) for q, r in rows if not q['answerable']]
    recall = sum(len(set(q['expected']) & set(r['retrieved'][:10])) / len(q['expected'])
                 for q, r in rows if q['answerable'])
    citations = [(q['id'], c) for q, r in rows for c in r['citations']]
    graded = [(question_id, c, judgments[(question_id, c['id'])])
              for question_id, c in citations if (question_id, c['id']) in judgments]
    answerable_ids = {q['id'] for q, _ in answerable}
    answerable_citations = [(question_id, c) for question_id, c in citations
                            if question_id in answerable_ids]
    answerable_graded = [(question_id, c, supported) for question_id, c, supported in graded
                         if question_id in answerable_ids]
    answered_with_citations = sum(not r['abstained'] and bool(r['citations'])
                                  for _, r in answerable)
    return {
        'recall_at_10': _ratio(recall, len(answerable)),
        'abstention_accuracy': _ratio(sum(r['abstained'] for _, r in unanswerable),
                                      len(unanswerable)),
        'answerable_response_coverage': _ratio(answered_with_citations, len(answerable)),
        'anchor_validity': _ratio(sum(c['valid'] for _, c in citations), len(citations)),
        'isolation_leaks': sum(r['isolation_leaks'] for _, r in rows),
        'citation_support': _ratio(sum(supported for _, _, supported in answerable_graded),
                                   len(answerable_graded)),
        'grade_coverage': _ratio(len(graded), len(citations)),
        'question_count': len(rows),
        'answerable_count': len(answerable),
        'unanswerable_count': len(unanswerable),
        'citation_count': len(citations),
        'answerable_citation_count': len(answerable_citations),
        'answered_with_citations_count': answered_with_citations,
        'graded_citation_count': len(graded),
    }


def evaluate(corpus: dict, run: dict, grades: dict | None = None) -> dict:
    """Validate frozen inputs and score retrieval, abstention and cited answers.

    Invalid or inconsistent input raises ValueError. Quality thresholds are checked
    on held-out questions only; the result is not C11 or independent-review signoff.
    """
    corpus = _object(corpus, 'corpus')
    run = _object(run, 'run')
    if corpus.get('schema_version') != 1 or type(corpus.get('schema_version')) is not int:
        raise ValueError('corpus schema_version must be 1')
    if run.get('schema_version') != 1 or type(run.get('schema_version')) is not int:
        raise ValueError('run schema_version must be 1')
    corpus_hash = fingerprint(corpus)
    if run.get('corpus_sha256') != corpus_hash:
        raise ValueError('run corpus_sha256 does not match corpus')
    metadata = _object(run.get('metadata'), 'run metadata')
    for field in ('candidate', 'model', 'prompt', 'hardware', 'policy'):
        _string(metadata.get(field), 'run metadata ' + field)

    evidence_ids = set()
    for entry in _array(corpus.get('evidence'), 'corpus evidence'):
        evidence = _object(entry, 'evidence')
        identifier = _string(evidence.get('id'), 'evidence id')
        if identifier in evidence_ids:
            raise ValueError('duplicate evidence id: ' + identifier)
        evidence_ids.add(identifier)
        digest = evidence.get('source_sha256')
        if type(digest) is not str or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise ValueError('evidence source_sha256 must be lowercase SHA-256: ' + identifier)
        _string(evidence.get('locator'), 'evidence locator')

    questions = _array(corpus.get('questions'), 'corpus questions')
    by_id = {}
    for question in questions:
        q = _object(question, 'question')
        identifier = _string(q.get('id'), 'question id')
        if identifier in by_id:
            raise ValueError('duplicate question id: ' + identifier)
        _string(q.get('question'), 'question text')
        if q.get('split') not in ('calibration', 'held_out'):
            raise ValueError('invalid question split: ' + identifier)
        _string(q.get('category'), 'question category')
        if type(q.get('answerable')) is not bool:
            raise ValueError('question answerable must be boolean: ' + identifier)
        if q['category'] == 'unanswerable' and q['answerable']:
            raise ValueError('unanswerable category cannot be answerable: ' + identifier)
        expected = _unique_strings(q.get('expected'), 'question expected')
        if bool(expected) != q['answerable']:
            raise ValueError('question expected disagrees with answerable: ' + identifier)
        if not set(expected) <= evidence_ids:
            raise ValueError('question expected references unknown evidence: ' + identifier)
        by_id[identifier] = q

    results = _array(run.get('results'), 'run results')
    run_by_id = {}
    for result in results:
        r = _object(result, 'result')
        identifier = _string(r.get('id'), 'result id')
        if identifier not in by_id or identifier in run_by_id:
            raise ValueError('unknown or duplicate result id: ' + identifier)
        retrieved = _unique_strings(r.get('retrieved'), 'result retrieved')
        if not set(retrieved) <= evidence_ids:
            raise ValueError('result retrieved references unknown evidence: ' + identifier)
        if type(r.get('abstained')) is not bool:
            raise ValueError('result abstained must be boolean: ' + identifier)
        citations = _array(r.get('citations'), 'result citations')
        citation_ids = set()
        for citation in citations:
            c = _object(citation, 'citation')
            citation_id = _string(c.get('id'), 'citation id')
            if citation_id in citation_ids:
                raise ValueError('duplicate citation id: ' + identifier)
            citation_ids.add(citation_id)
            if type(c.get('valid')) is not bool:
                raise ValueError('citation valid must be boolean: ' + identifier)
        leaks = r.get('isolation_leaks')
        if type(leaks) is not int or leaks < 0:
            raise ValueError('isolation_leaks must be a nonnegative integer: ' + identifier)
        latency = r.get('latency_ms')
        try:
            valid_latency = type(latency) in (int, float) and math.isfinite(latency) and latency >= 0
        except OverflowError:
            valid_latency = False
        if not valid_latency:
            raise ValueError('latency_ms must be finite and nonnegative: ' + identifier)
        run_by_id[identifier] = r
    if set(run_by_id) != set(by_id):
        raise ValueError('run must have exactly one result per question')

    run_hash = fingerprint(run)
    judgments = {}
    if grades is not None:
        grades = _object(grades, 'grades')
        if grades.get('schema_version') != 1 or type(grades.get('schema_version')) is not int:
            raise ValueError('grades schema_version must be 1')
        if grades.get('run_sha256') != run_hash:
            raise ValueError('grades run_sha256 does not match run')
        _string(grades.get('reviewer'), 'grades reviewer')
        for judgment in _array(grades.get('judgments'), 'grades judgments'):
            j = _object(judgment, 'judgment')
            question_id = _string(j.get('question_id'), 'judgment question_id')
            citation_id = _string(j.get('citation_id'), 'judgment citation_id')
            key = question_id, citation_id
            if question_id not in run_by_id or key in judgments or not any(
                    c['id'] == citation_id for c in run_by_id[question_id]['citations']):
                raise ValueError('unknown or duplicate judgment citation reference')
            if type(j.get('supported')) is not bool:
                raise ValueError('judgment supported must be boolean')
            judgments[key] = j['supported']

    pairs = [(q, run_by_id[q['id']]) for q in questions]
    metrics = _metrics(pairs, judgments)
    by_split = {split: _metrics([(q, r) for q, r in pairs if q['split'] == split], judgments)
                for split in ('calibration', 'held_out')}
    by_category = {category: _metrics([(q, r) for q, r in pairs if q['category'] == category], judgments)
                   for category in sorted({q['category'] for q in questions})}
    held = by_split['held_out']
    issues = []
    if len(questions) < 120:
        issues.append('corpus_below_120_questions')
    if not by_split['calibration']['question_count'] or not held['question_count']:
        issues.append('both_splits_required')
    if missing := REQUIRED_CATEGORIES - set(by_category):
        issues.append('missing_categories:' + ','.join(sorted(missing)))
    held_categories = {q['category'] for q, _ in pairs if q['split'] == 'held_out'}
    if missing := REQUIRED_CATEGORIES - held_categories:
        issues.append('held_out_missing_categories:' + ','.join(sorted(missing)))
    if not held['answerable_count'] or not held['unanswerable_count']:
        issues.append('held_out_needs_answerable_and_unanswerable')
    if not held['citation_count'] or held['grade_coverage'] != 1:
        issues.append('held_out_citations_need_complete_human_grades')
    if held['answerable_response_coverage'] != 1:
        issues.append('held_out_answerable_response_evidence_incomplete')
    for field, minimum in (('recall_at_10', .90), ('abstention_accuracy', .95),
                           ('citation_support', .95), ('anchor_validity', 1.0)):
        if held[field] is None or held[field] < minimum:
            issues.append('held_out_' + field + '_below_threshold')
    if held['isolation_leaks']:
        issues.append('held_out_isolation_leaks')
    return {'schema_version': 1, 'corpus_sha256': corpus_hash, 'run_sha256': run_hash,
            'metrics': metrics, 'by_split': by_split, 'by_category': by_category,
            'quality_thresholds_met': not issues, 'issues': issues,
            'limits': ('Anchor validity and isolation leaks are supplied observations; '
                       'human citation labels and reviewer identity are not independently verified. '
                       'Quality thresholds do not establish C11 completion or release signoff.')}
