"""Convert blinded citation judgments to one exact knowledge evaluation run.

This only maps human-supplied labels. It does not score or create judgments.
"""
import argparse
import json
import re
import sys

from cc_knowledge_evaluate import read_packet
from cc_memory_lib.knowledge_evaluation import fingerprint


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value, name):
    require(type(value) is str and bool(value.strip()), name + ' must be a nonempty string')
    return value


def convert(key, review, run):
    """Bind review IDs through the private key and validate target-run citations."""
    require(type(key) is list, 'key must be an array')
    require(type(review) is dict and set(review) == {'schema_version', 'reviewer', 'judgments'},
            'invalid review packet')
    require(type(review['schema_version']) is int and review['schema_version'] == 1,
            'review schema_version must be 1')
    reviewer = nonempty(review['reviewer'], 'reviewer')
    require(type(review['judgments']) is list, 'judgments must be an array')
    require(type(run) is dict and type(run.get('schema_version')) is int
            and run['schema_version'] == 1 and type(run.get('results')) is list,
            'invalid run packet')
    run_hash = fingerprint(run)
    citations = {}
    for result in run['results']:
        require(type(result) is dict, 'invalid run result')
        question_id = nonempty(result.get('id'), 'run question id')
        require(question_id not in citations, 'duplicate run question id: ' + question_id)
        require(type(result.get('citations')) is list, 'invalid run citations')
        ids = set()
        for citation in result['citations']:
            require(type(citation) is dict, 'invalid run citation')
            citation_id = nonempty(citation.get('id'), 'run citation id')
            require(citation_id not in ids, 'duplicate run citation id: ' + question_id)
            ids.add(citation_id)
        citations[question_id] = ids

    by_review_id, target_questions = {}, set()
    for entry in key:
        require(type(entry) is dict and set(entry) ==
                {'review_id', 'method', 'question_id', 'run_sha256'}, 'invalid key entry')
        review_id = nonempty(entry['review_id'], 'review_id')
        nonempty(entry['method'], 'method')
        question_id = nonempty(entry['question_id'], 'question_id')
        digest = entry['run_sha256']
        require(type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest),
                'invalid key run_sha256')
        require(review_id not in by_review_id, 'duplicate review_id: ' + review_id)
        by_review_id[review_id] = entry
        if digest == run_hash:
            require(question_id in citations, 'key references unknown run question: ' + question_id)
            require(question_id not in target_questions, 'duplicate key question: ' + question_id)
            target_questions.add(question_id)
    require(target_questions == set(citations), 'key does not cover exact run questions')

    seen, judgments = set(), []
    for item in review['judgments']:
        require(type(item) is dict and set(item) == {'review_id', 'citation_id', 'supported'},
                'invalid review judgment')
        review_id = nonempty(item['review_id'], 'judgment review_id')
        citation_id = nonempty(item['citation_id'], 'judgment citation_id')
        require(type(item['supported']) is bool, 'supported must be boolean')
        require(review_id in by_review_id, 'unknown review_id: ' + review_id)
        pair = review_id, citation_id
        require(pair not in seen, 'duplicate judgment: ' + review_id + '/' + citation_id)
        seen.add(pair)
        entry = by_review_id[review_id]
        if entry['run_sha256'] != run_hash:
            continue
        question_id = entry['question_id']
        require(citation_id in citations[question_id],
                'unknown citation for run question: ' + question_id + '/' + citation_id)
        judgments.append({'question_id': question_id, 'citation_id': citation_id,
                          'supported': item['supported']})
    return {'schema_version': 1, 'run_sha256': run_hash, 'reviewer': reviewer,
            'judgments': judgments}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True, help='Private review ID map JSON')
    parser.add_argument('--review', required=True, help='Blinded human judgments JSON')
    parser.add_argument('--run', required=True, help='Exact recorded run JSON')
    args = parser.parse_args(argv)
    try:
        grades = convert(read_packet(args.key), read_packet(args.review), read_packet(args.run))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(grades, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
