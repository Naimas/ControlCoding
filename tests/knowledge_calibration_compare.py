"""Compare current ranking only on calibration questions of an existing frozen corpus."""
import argparse
import json
from pathlib import Path
import sys

from knowledge_real_evaluation import record, save, candidate_identity
from cc_memory_lib.knowledge_store import database
from cc_memory_lib.knowledge_evaluation import evaluate, fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, help='Previous calibration directory; compare its after-MODE packets')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists() or repo == output or repo in output.parents:
        raise ValueError('Use a new external output directory')
    frozen = args.frozen.resolve()
    original = json.loads((frozen / 'corpus.json').read_text(encoding='utf-8'))
    corpus = {**original, 'questions': [q for q in original['questions'] if q['split'] == 'calibration']}
    if not corpus['questions']:
        raise ValueError('No calibration questions')
    chunks = json.loads((frozen / 'frozen-passages.json').read_text(encoding='utf-8'))
    with database(frozen / 'frozen-project') as db:
        current = {row['id']: (row['revision'], row['text']) for row in db.execute('SELECT id,revision,text FROM chunks')}
    if current != {row['id']: (row['revision'], row['text']) for row in chunks}:
        raise ValueError('Frozen passages changed')
    output.mkdir(parents=True)
    save(output / 'corpus.json', corpus)
    ids = {q['id'] for q in corpus['questions']}
    summary = {}
    for mode in ('graph', 'hybrid'):
        baseline_file = (args.baseline / ('after-'+mode+'.json')) if args.baseline else (frozen / ('run-'+mode+'.json'))
        old = json.loads(baseline_file.read_text(encoding='utf-8'))
        if old['corpus_sha256'] != fingerprint(corpus if args.baseline else original):
            raise ValueError('Baseline corpus changed')
        before = {**old, 'corpus_sha256': fingerprint(corpus), 'results': [r for r in old['results'] if r['id'] in ids]}
        after = record(frozen / 'frozen-project', corpus, chunks, mode,
                       {**old['metadata'], 'candidate': candidate_identity(repo), 'selection_policy': 'BM25/semantic RRF top10; graph metadata only'})
        # The shared recorder preserves historical comparison description; this
        # candidate explicitly changes the budget and makes no equal-budget claim.
        after['retrieval_budget'] = 'Up to ten query-ranked passages; no arbitrary graph neighbors.'
        for label, run in (('before', before), ('after', after)):
            save(output / (label+'-'+mode+'.json'), run)
            score = evaluate(corpus, run)
            save(output / ('score-'+label+'-'+mode+'.json'), score)
            summary[label+'-'+mode] = score['metrics']
    save(output / 'summary.json', {'scope': 'Calibration only, no fresh held-out or human acceptance', 'metrics': summary})
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
