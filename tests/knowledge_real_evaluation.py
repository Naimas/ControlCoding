"""Freeze real public documents and record reproducible retrieval comparisons.

Explicit benchmark only; creates a new external directory, never changes live memory.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import platform
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_sources import capture
from cc_memory_lib.knowledge_store import database
from cc_memory_lib.knowledge_evaluation import fingerprint, evaluate
from cc_memory_lib.knowledge_query import STOPWORDS


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def candidate_identity(repository):
    return fingerprint({p.relative_to(repository).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((repository / 'scripts').rglob('*.py'))})


def frozen_corpus(draft, sources, chunks):
    by_path = {s['path']: s for s in sources}
    evidence = [{'id': c['id'], 'source_sha256': c['revision'],
                 'locator': c['path'] + ':' + str(c['line']) + '-' + str(c['end_line'])}
                for c in chunks]
    questions = []
    for item in draft['questions']:
        expected = []
        for label in item['expected']:
            body = by_path[label['path']]['body'].replace('\r\n', '\n')
            quote = label['quote'].replace('\r\n', '\n')
            if not quote or body.count(quote) != 1:
                raise ValueError('Quote must occur exactly once: ' + item['id'] + ' ' + label['path'])
            position = body.index(quote)
            # Chunk end_line may include the next buffer's first line. Match
            # actual character intervals, including chunks splitting a long line.
            flat = body.replace('\n', '')
            quote_start = len(body[:position].replace('\n', ''))
            quote_end = quote_start + len(quote.replace('\n', ''))
            lines = body.splitlines(keepends=True)
            matches = []
            for c in chunks:
                if c['path'] != label['path']:
                    continue
                lower = len(''.join(lines[:c['line']-1]).replace('\n', ''))
                upper = len(''.join(lines[:c['end_line']]).replace('\n', ''))
                content = c['text'].replace('\r', '').replace('\n', '')
                offset = flat.find(content, lower, upper)
                if offset < 0:
                    raise ValueError('Passage cannot resolve against original text')
                if offset < quote_end and offset + len(content) > quote_start:
                    if flat.find(content, offset+1, upper) >= 0:
                        raise ValueError('Ambiguous repeated passage in source line range')
                    matches.append(c['id'])
            if not matches:
                raise ValueError('No passage matches ' + item['id'])
            expected.extend(matches)
        questions.append({**item, 'expected': list(dict.fromkeys(expected)),
                          'source_labels': item['expected']})
    return {'schema_version': 1, 'label_origin': draft['label_origin'],
            'evidence': evidence, 'questions': questions,
            'limits': 'Agent-authored draft labels; human validation and true blind holdout remain pending.'}


def lexical_baseline(chunks, question):
    terms = set(re.findall(r'\w{2,}', question.casefold())) - STOPWORDS
    scored = []
    for chunk in chunks:
        words = re.findall(r'\w{2,}', chunk['text'].casefold())
        score = sum(min(words.count(term), 3) for term in terms)
        if score:
            scored.append((score, chunk['id'], chunk))
    return [chunk for _, _, chunk in sorted(scored, key=lambda row: (-row[0], row[1]))[:8]]


def match_citations(result, chunks):
    by_anchor = {(c['source'], c['revision'], c['path'], c['line'], c['end_line'], c['text']): c for c in chunks}
    retrieved, citations = [], []
    for c in result['citations']:
        match = by_anchor.get((c['source'], c['revision'], c['path'], c['line'], c['end_line'], c['excerpt']))
        if match:
            retrieved.append(match['id'])
        citations.append({**c, 'valid': match is not None})
    return list(dict.fromkeys(retrieved)), citations


def record(root, corpus, chunks, mode, metadata):
    results = []
    allowed_paths = {c['path'] for c in chunks}
    for q in corpus['questions']:
        started = time.perf_counter()
        if mode == 'lexical':
            selected = lexical_baseline(chunks, q['question'])
            result = {'answer': '\n\n'.join(c['text'] for c in selected), 'warning': None,
                      'citations': [{'id': 'S'+str(i), 'source': c['source'], 'revision': c['revision'],
                                     'path': c['path'], 'line': c['line'], 'end_line': c['end_line'],
                                     'excerpt': c['text']} for i, c in enumerate(selected, 1)]}
        else:
            result = service.query(root, q['question'], mode == 'hybrid')
            if mode == 'hybrid' and (result.get('warning') or not result['mode'].startswith('neural embeddings')):
                raise ValueError('Hybrid run cannot accept lexical fallback: ' + str(result.get('warning')))
        elapsed = (time.perf_counter() - started) * 1000
        retrieved, citations = match_citations(result, chunks)
        results.append({'id': q['id'], 'retrieved': retrieved, 'citations': citations,
                        'answer': result['answer'], 'abstained': not result['citations'],
                        'warning': result.get('warning'), 'latency_ms': elapsed,
                        'isolation_leaks': sum(c['path'] not in allowed_paths for c in result['citations'])})
    return {'schema_version': 1, 'corpus_sha256': fingerprint(corpus),
            'metadata': {**metadata, 'retrieval': mode}, 'results': results,
            'isolation_method': 'Only emitted citation paths checked against frozen scope; not an adversarial leakage corpus.',
            'retrieval_budget': 'Lexical baseline top 8; current product modes up to 10 query-ranked passages with graph metadata. This compares shipped behavior, not a controlled graph-only ablation.'}


def review_export(corpus, runs, output):
    # Hide method names in the review page. The separate key is for the coordinator.
    key, panels = [], []
    for method, run in runs.items():
        digest = fingerprint(run)
        for question, result in zip(corpus['questions'], run['results']):
            token = hashlib.sha256((digest + result['id']).encode()).hexdigest()[:16]
            key.append({'review_id': token, 'method': method, 'question_id': result['id'], 'run_sha256': digest})
            citations = ''.join('<li><b>' + html.escape(c['id']) + '</b> ' +
                                html.escape(c['path']) + '<pre>' + html.escape(c['excerpt']) + '</pre>' +
                                '<label>Does this citation support the answer? <select data-review="'+token+
                                '" data-citation="'+html.escape(c['id'], quote=True)+'"><option value="">Not graded</option>'+
                                '<option value="yes">Supported</option><option value="no">Not supported</option></select></label></li>'
                                for c in result['citations'])
            panels.append((token, '<article><small>'+token+'</small><h2>'+html.escape(question['question'])+
                           '</h2><pre>'+html.escape(result['answer'])+'</pre><ol>'+citations+'</ol></article>'))
        save(output / ('grades-' + method + '-PENDING.json'), {'schema_version': 1,
             'run_sha256': digest, 'reviewer': '', 'judgments': []})
    save(output / 'review-key-private.json', key)
    page = '<!doctype html><meta charset="utf-8"><title>Evidence review</title><style>body{background:#eee;font:16px system-ui;max-width:1000px;margin:auto}article{background:white;padding:2rem;margin:2rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}header{position:sticky;top:0;background:white;padding:1rem}</style><header><h1>Human evidence review — pending</h1><p>Methods are hidden. Judge each citation against the answer. Ratings are not saved until downloaded.</p><label>Reviewer <input id="reviewer"></label> <button id="download">Download entered judgments</button><span id="status" role="status"></span></header>'
    script = '''<script>
document.getElementById('download').onclick=()=>{
 const reviewer=document.getElementById('reviewer').value.trim();
 if(!reviewer){document.getElementById('status').textContent=' Enter a reviewer identifier.';return;}
 const judgments=Array.from(document.querySelectorAll('select[data-review]')).filter(x=>x.value).map(x=>({review_id:x.dataset.review,citation_id:x.dataset.citation,supported:x.value==='yes'}));
 const blob=new Blob([JSON.stringify({schema_version:1,reviewer,judgments},null,2)],{type:'application/json'});
 const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='human-review.json';link.click();
 setTimeout(()=>URL.revokeObjectURL(link.href),1000);document.getElementById('status').textContent=' Downloaded '+judgments.length+' judgments; ungraded items remain pending.';
};</script>'''
    (output / 'review.html').write_text(page + ''.join(value for _, value in sorted(panels))+script, encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--questions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--embedding', default='', help='Installed local Ollama embedding model; opt in explicitly')
    args = parser.parse_args(argv)
    project, output = args.project.resolve(), args.output.resolve()
    repository = Path(__file__).resolve().parents[1]
    if output.exists() or any(output == p or p in output.parents or output in p.parents for p in (project, repository)):
        raise ValueError('Choose a new external output directory disjoint from the project/repository')
    draft = json.loads(args.questions.read_text(encoding='utf-8-sig'))
    sources = capture(project, ['project'])
    output.mkdir(parents=True)
    root = output / 'frozen-project'
    root.mkdir()
    for source in sources:
        raw = (project / source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['revision']:
            raise ValueError('Source changed during freeze')
        path = root / source['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    config = {**service.DEFAULT, 'scopes': ['project'], 'automatic': False, 'embedding': args.embedding}
    service.configure(root, config)
    service.reconcile(root)
    with database(root) as db:
        actual = {r['path']: r['revision'] for r in db.execute('SELECT path,revision FROM sources WHERE deleted=0')}
        if actual != {s['path']: s['revision'] for s in sources}:
            raise ValueError('Frozen source manifest differs from reconciled sources')
        chunks = [dict(row) for row in db.execute('SELECT c.*,s.path FROM chunks c JOIN sources s ON c.source=s.id ORDER BY c.id')]
    corpus = frozen_corpus(draft, sources, chunks)
    save(output / 'corpus.json', corpus)
    save(output / 'frozen-passages.json', chunks)
    metadata = {'candidate': candidate_identity(repository), 'model': 'lexical-only',
                'prompt': 'Extractive retrieval; no generation prompt', 'hardware': platform.platform()+' '+platform.processor(),
                'policy': json.dumps(config, sort_keys=True)}
    runs = {}
    for mode in ('lexical', 'graph'):
        runs[mode] = record(root, corpus, chunks, mode, metadata)
    if args.embedding:
        started = time.perf_counter()
        while True:
            state = service.index(root)
            if state['counts']['embedded'] == state['counts']['chunks']:
                break
            if time.perf_counter() - started > 1800:
                raise TimeoutError('Embedding build exceeded 30-minute benchmark budget')
        save(output / 'embedding-build.json', {'seconds': time.perf_counter()-started, 'identity': state['embedding_identity'], 'counts': state['counts']})
        runs['hybrid'] = record(root, corpus, chunks, 'hybrid', {**metadata, 'model': state['embedding_identity']})
    reports = {}
    for mode, run in runs.items():
        save(output / ('run-'+mode+'.json'), run)
        reports[mode] = evaluate(corpus, run)
        save(output / ('score-'+mode+'.json'), reports[mode])
    review_export(corpus, runs, output)
    print(json.dumps({'questions': len(corpus['questions']), 'sources': len(sources), 'passages': len(chunks),
                      'metrics': {mode: report['metrics'] for mode, report in reports.items()},
                      'human_review': 'pending', 'output': str(output)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
