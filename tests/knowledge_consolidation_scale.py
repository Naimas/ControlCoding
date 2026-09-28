"""Explicit consolidation experiment on a copied external 5k/50k/1k corpus.

The caller provides a disposable, already populated copy; this never configures
the real project or downloads models. Report timings are evidence, not tests of
semantic correctness of model prose. Uses production limits without overrides.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import database
from knowledge_combined_scale import memory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=20)
    args = parser.parse_args()
    root, output = args.project.resolve(), args.output.resolve()
    repo = Path(__file__).resolve().parents[1]
    for path in (root, output):
        if path == repo or repo in path.parents or path in repo.parents:
            raise ValueError('Use an external disposable workbench')
    if not 1 <= args.samples <= 20:
        raise ValueError('Sample count must be 1..20')
    output.mkdir()
    report = {'production_limits_unchanged': True, 'project': str(root),
              'timings': {}, 'completed': False, 'semantic_quality': 'not measured'}

    def save():
        report['memory'] = memory()
        (output / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def phase(name, call):
        start = time.perf_counter()
        result = call()
        report['timings'][name] = time.perf_counter() - start
        save()
        print(json.dumps({'phase': name, 'seconds': report['timings'][name]}), flush=True)
        return result

    with database(root) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        counts = {name: db.execute('SELECT count(*) FROM ' + table + where).fetchone()[0]
                  for name, table, where in (('sources', 'sources', ' WHERE deleted=0'),
                    ('chunks', 'chunks', ''), ('sessions', 'conversations', ''),
                    ('embedded', 'chunks', ' WHERE vector IS NOT NULL'))}
        assert counts == {'sources': 6000, 'chunks': 50000, 'sessions': 1000, 'embedded': 50000}, counts
    report['initial_counts'] = counts
    phase('migration_with_verified_backup', lambda: migrate(root, output / 'before.ccmemory'))
    phase('first_reconcile', lambda: service.reconcile(root))
    config = {'provider': 'ollama', 'model': '', 'generation': {
        'effort': 'default', 'thinking': None, 'reasoningMode': 'default',
        'verbosity': 'default', 'temperature': None, 'format': 'json', 'summary': False},
        'maxContextChars': 12000, 'maxOutputTokens': 2048, 'timeoutSeconds': 120,
        'maxRequests': 3, 'roleRevision': 'scale-fixture'}
    observed = set()
    for number in range(6):
        view = phase(f'create_{number}', lambda: service.dispatch(root, 'consolidation-create',
                                                                  {'title': 'Capacity fixture batch'}))
        packet = phase(f'prepare_{number}', lambda: service.dispatch(root, 'consolidation-prepare',
            {'job': view['job']['id'], 'prompt': 'Analyze only selected original passages.',
             'config': config, 'mode': 'manual'}))
        for item in packet['input_manifest']:
            key = tuple(item[k] for k in ('source', 'revision', 'line', 'end_line', 'excerpt'))
            assert key not in observed
            observed.add(key)
        n = len(packet['input_manifest'])
        response = json.dumps({'protocol': 1, 'job': packet['job'], 'request_id': packet['request_id'],
            'manifest_digest': packet['manifest_digest'], 'outcome': 'no_change', 'proposals': [],
            'used_evidence_ids': [], 'unresolved_questions': [],
            'coverage': {'inspected': n, 'analyzed': n, 'deferred': 0}})
        phase(f'import_{number}', lambda: service.dispatch(root, 'consolidation-import',
            {'job': packet['job'], 'request_id': packet['request_id'], 'text': response}))
    report['progress'] = service.dispatch(root, 'consolidation-view')['progress']
    assert report['progress']['analyzed_passages'] == len(observed)
    phase('context', lambda: service.dispatch(root, 'consolidation-context', {'query': 'requirement evidence'}))
    # The initial semantic call includes model activation; steady-state samples
    # are recorded separately instead of hiding warm-up in percentile claims.
    phase('semantic_warmup', lambda: service.query(root, 'documented requirement evidence', True))
    for number in range(args.samples):
        phase(f'refresh_{number}', lambda: service.reconcile(root))
        response = phase(f'query_{number}', lambda: service.query(root, 'documented requirement evidence', True))
        assert response['citations'] and response['mode'] == 'neural embeddings + lexical BM25', response.get('mode')
    for category in ('refresh', 'query'):
        samples = sorted(report['timings'][f'{category}_{i}'] for i in range(args.samples))
        report[category] = {'samples': len(samples), 'median_seconds': statistics.median(samples),
                            'p95_seconds': samples[max(0, int(.95 * len(samples) + .999) - 1)]}
    report['final_counts'] = service.status(root)['counts']
    report['completed'] = True
    report['performance_pass'] = report['refresh']['p95_seconds'] < 10 and report['query']['p95_seconds'] < 2
    save()
    if not report['performance_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
