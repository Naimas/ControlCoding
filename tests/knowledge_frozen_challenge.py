"""Run a predeclared challenge against frozen sources and an exact candidate."""
import argparse
import json
from pathlib import Path

from knowledge_real_evaluation import frozen_corpus, record, review_export, save, candidate_identity
from cc_memory_lib.knowledge_sources import capture
from cc_memory_lib.knowledge_store import database
from cc_memory_lib.knowledge_evaluation import fingerprint, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--questions', type=Path, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if candidate_identity(repo) != args.candidate:
        raise ValueError('Candidate differs from predeclared freeze')
    output = args.output.resolve()
    if output.exists() or repo == output or repo in output.parents:
        raise ValueError('Use a new external output directory')
    frozen = args.frozen.resolve()
    root = frozen / 'frozen-project'
    chunks = json.loads((frozen / 'frozen-passages.json').read_text(encoding='utf-8'))
    with database(root) as db:
        actual = {r['id']: (r['revision'], r['text']) for r in db.execute('SELECT id,revision,text FROM chunks')}
        original_sources = {r['path']: r['revision'] for r in db.execute('SELECT path,revision FROM sources WHERE deleted=0')}
    if actual != {r['id']: (r['revision'], r['text']) for r in chunks}:
        raise ValueError('Frozen passages changed')
    draft = json.loads(args.questions.read_text(encoding='utf-8-sig'))
    sources = capture(root, ['project'])
    if original_sources != {s['path']: s['revision'] for s in sources}:
        raise ValueError('Frozen source bytes changed')
    corpus = frozen_corpus(draft, sources, chunks)
    # Record labels before any query or outcome. An interrupted run stays incomplete.
    output.mkdir(parents=True)
    save(output / 'corpus.json', corpus)
    save(output / 'freeze.json', {'candidate': args.candidate, 'corpus_sha256': fingerprint(corpus),
                                 'scope': 'Independent agent-authored challenge, not human acceptance'})
    runs, summary = {}, {}
    for mode in ('lexical', 'graph', 'hybrid'):
        reference = json.loads((frozen / ('run-'+mode+'.json')).read_text(encoding='utf-8'))
        run = record(root, corpus, chunks, mode, {**reference['metadata'], 'candidate': args.candidate})
        runs[mode] = run
        save(output / ('run-'+mode+'.json'), run)
        score = evaluate(corpus, run)
        save(output / ('score-'+mode+'.json'), score)
        summary[mode] = score['metrics']
    if candidate_identity(repo) != args.candidate:
        raise ValueError('Candidate changed during challenge')
    review_export(corpus, runs, output)
    save(output / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
