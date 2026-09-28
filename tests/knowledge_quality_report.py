"""Summarize actual local-model observations without inventing human grades."""
import argparse
import hashlib
import json
from pathlib import Path

from knowledge_answer_review import render
from knowledge_real_evaluation import match_citations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--responses', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--passages', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    repo = Path(__file__).resolve().parents[1]
    if output.exists() or output == repo or repo in output.parents:
        raise ValueError('Use a new external output directory')
    raw = args.responses.read_bytes()
    packet = json.loads(raw)
    if (args.responses.parent / 'INVALID.json').exists():
        raise ValueError('Experiment marked invalid; do not create an acceptance review')
    if not packet.get('frozenIdentity') or packet.get('archive_policy') != 'Disabled in evaluator only; product retention unchanged':
        raise ValueError('Missing frozen-corpus isolation evidence')
    corpus_raw = args.corpus.read_bytes()
    if hashlib.sha256(corpus_raw).hexdigest() != packet['corpus_sha256']:
        raise ValueError('Corpus hash changed')
    corpus = json.loads(corpus_raw)
    passages_raw = args.passages.read_bytes()
    passages = json.loads(passages_raw)
    identities = {p['id']: (p['revision'], f"{p['path']}:{p['line']}-{p['end_line']}") for p in passages}
    if identities != {e['id']: (e['source_sha256'], e['locator']) for e in corpus['evidence']}:
        raise ValueError('Frozen passages do not match corpus evidence')
    questions = {q['id']: q for q in corpus['questions']}
    rows = packet['results']
    if len({r['id'] for r in rows}) != len(rows) or any(r['id'] not in questions for r in rows):
        raise ValueError('Duplicate or unknown result')
    initial_errors = sum(bool(r['initialError']) for r in rows)
    final_errors = sum(bool(r['error']) for r in rows)
    answerable = [r for r in rows if questions[r['id']]['answerable']]
    negative = [r for r in rows if not questions[r['id']]['answerable']]
    def retrieval_metrics(stage):
        recalls, valid, total, warnings = [], 0, 0, 0
        for row in rows:
            evidence = (row.get('followup') if stage == 'final' else None) or row['initial'] or {'citations': []}
            retrieved, citations = match_citations(evidence, passages)
            valid += sum(c['valid'] for c in citations)
            total += len(citations)
            warnings += bool(evidence.get('warning'))
            expected = questions[row['id']]['expected']
            if questions[row['id']]['answerable']:
                recalls.append(len(set(expected) & set(retrieved[:10])) / len(expected))
        return {'recall_at_10': sum(recalls) / len(recalls) if recalls else None,
                'valid_anchors': valid, 'emitted_anchors': total,
                'warnings': warnings, 'answerable_cases': len(recalls)}
    summary = {
        'scope': packet['scope'], 'completed': len(rows), 'required': len(questions),
        'complete': len(rows) == len(questions), 'initial_errors': initial_errors,
        'final_errors': final_errors,
        'answerable_cases': len(answerable),
        'answerable_cited_drafts': sum(bool(r['finalAnswer']) and not r['finalAnswer']['abstained'] and not r['error'] for r in answerable),
        'negative_cases': len(negative),
        'negative_refusals': sum(bool(r['finalAnswer']) and r['finalAnswer']['abstained'] and not r['error'] for r in negative),
        'followup_rounds': packet['followups'],
        'human_support_grades': 0, 'acceptance': 'pending',
        'notice': 'Response coverage and refusal counts are not factual correctness. Labels are exposed agent-authored development labels. Errors remain failures.',
        'response_sha256': hashlib.sha256(raw).hexdigest(),
        'runtime_manifest_sha256': packet['runtime_manifest_sha256'],
        'frozen_identity': packet['frozenIdentity'],
        'archive_policy': packet['archive_policy'],
        'passages_sha256': hashlib.sha256(passages_raw).hexdigest(),
        'initial_retrieval': retrieval_metrics('initial'),
        'final_retrieval': retrieval_metrics('final'),
    }
    if args.baseline:
        from cc_memory_lib.knowledge_evaluation import fingerprint
        baseline_raw = args.baseline.read_bytes()
        baseline = json.loads(baseline_raw)
        if baseline['corpus_sha256'] != fingerprint(corpus):
            raise ValueError('Baseline corpus differs')
        selected = {r['id'] for r in answerable}
        scores = [len(set(questions[r['id']]['expected']) & set(r['retrieved'][:10])) / len(questions[r['id']]['expected'])
                  for r in baseline['results'] if r['id'] in selected]
        if len(scores) != len(selected):
            raise ValueError('Baseline does not cover observed answerable cases')
        summary['reference_retrieval'] = {'sha256': hashlib.sha256(baseline_raw).hexdigest(),
                                         'recall_at_10': sum(scores) / len(scores) if scores else None,
                                         'scope': 'Recorded reference on identical frozen passages; differing shipped result limits, not controlled graph ablation'}
    displayed_questions, evidence, answers = [], [], []
    for row in rows:
        stages = [('initial', row['initial'], row['initialAnswer'], row['initialError'])]
        if row['followup'] is not None:
            stages.append(('followup', row['followup'], row['finalAnswer'], row['error']))
        for stage, source, answer, error in stages:
            identifier = row['id'] + '-' + stage
            displayed_questions.append({'id': identifier, 'question': row['question'] + ' (' + stage + ')'})
            evidence.append({'id': identifier, 'citations': (source or {}).get('citations', [])})
            answers.append({'id': identifier, 'text': (answer or {}).get('text'), 'error': error or 'No generated response'})
    output.mkdir(parents=True)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    page = render({'questions': displayed_questions}, {'results': evidence}, {'results': answers}, summary['response_sha256'])
    page = page.replace('Questa scheda riguarda otto risposte.', f'Questa scheda riguarda {len(answers)} osservazioni, incluse le fasi iniziali e gli eventuali approfondimenti.')
    (output / 'valutazione-risposte.html').write_text(page, encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
