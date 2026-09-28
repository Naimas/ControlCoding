"""Bounded, model-free continuity probe over the real knowledge service.

The seeded checkpoint notes are benchmark fixtures, not observed human handoffs or
approved decisions. Each probe runs in a new Python process against durable SQLite.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service


CHECKPOINTS = (
    ('scope', 'Keep the project source scope explicit.', 'Full corpus acceptance remains open.'),
    ('retention', 'Retain the reviewed conversation summary.', 'Human provenance review remains open.'),
    ('identity', 'Preserve source identity across a rename.', 'Old path reuse still needs observation.'),
    ('refresh', 'Refresh rich sources only after selection.', 'OCR coverage remains format dependent.'),
    ('picker', 'Use the native followed document picker.', 'Project switch isolation needs verification.'),
    ('ledger', 'Show a stored source status ledger.', 'Failed acquisition needs a visible retry.'),
    ('catalog', 'Use snapshot bound catalog windows.', 'Large corpus navigation remains open.'),
    ('backup', 'Stream archive export and restore.', 'Full capacity acceptance remains open.'),
    ('staging', 'Resume passage preparation after interruption.', 'Inventory capture remains open.'),
    ('acquisition', 'Batch durable acquisition checkpoints.', 'Crash and retry evidence remains open.'),
    ('graph', 'Keep one continuous document graph.', 'Cross source relation review remains open.'),
    ('reader', 'Open the formatted document reader.', 'Source revision binding needs verification.'),
    ('topics', 'Explain topic membership with evidence.', 'Ambiguous subjects need classification.'),
    ('history', 'Show linear document replacement history.', 'Filename version suggestions need review.'),
    ('roles', 'Save per role provider policies.', 'Autonomous execution remains separate work.'),
    ('manual', 'Correlate manual handoff replies.', 'Automatic archival remains separate work.'),
    ('wiki', 'Bind wiki pages to source revisions.', 'Contradiction review remains open.'),
    ('evaluation', 'Freeze quality evaluation inputs.', 'Blind human judgments remain open.'),
    ('release', 'Keep public claims tied to verified behavior.', 'Hosted acceptance remains open.'),
    ('adoption', 'Measure task resumption over twenty handoffs.', 'Human timing evidence remains open.'),
)


def cases():
    result = []
    for index, (topic, decision, blocker) in enumerate(CHECKPOINTS, 1):
        identifier = f'continuity_{index:02}_{topic}'
        marker = f'continuityprobe{index:02}{topic}'
        summary = (f'Checkpoint {marker}. Decision recorded for this benchmark: {decision} '
                   f'Open blocker: {blocker} Next action: inspect the blocker before resuming.')
        result.append({'id': identifier, 'marker': marker, 'title': f'{topic.title()} checkpoint',
                       'summary': summary, 'decision': decision, 'blocker': blocker})
    return result


def expected_body(case):
    return '# ' + case['title'] + '\n\nConversation evidence; not an approved decision.\n\n' + case['summary']


def validate(case, saved, result):
    """Return separate observable failures, including missing or changed blockers."""
    failures = []
    for field in ('id', 'title', 'summary'):
        if saved.get(field) != case[field]:
            failures.append('saved_' + field)
    if saved.get('retention') != 'summary' or saved.get('turns') != []:
        failures.append('retention_or_turns')
    if case['decision'] not in saved.get('summary', ''):
        failures.append('decision_missing_or_changed')
    if case['blocker'] not in saved.get('summary', ''):
        failures.append('blocker_missing_or_changed')
    citation = next((c for c in result['citations'] if c['source'] == 'conversation:' + case['id']), None)
    if citation is None:
        failures.append('source_identity_missing')
    else:
        if citation['path'] != 'conversation/' + case['id'] or citation['kind'] != 'conversation':
            failures.append('source_identity_changed')
        body = expected_body(case)
        if citation['revision'] != hashlib.sha256(body.encode()).hexdigest() or citation['excerpt'] != body:
            failures.append('source_content_changed')
        if case['decision'] not in citation['excerpt']:
            failures.append('indexed_decision_missing_or_changed')
        if case['blocker'] not in citation['excerpt']:
            failures.append('indexed_blocker_missing_or_changed')
    return failures


def external_root(path):
    root = path.resolve()
    checkout = Path(__file__).resolve().parents[1]
    if root == checkout or checkout in root.parents or root in checkout.parents:
        raise ValueError('fixture path must be outside the checkout')
    return root


def create(root):
    if root.exists():
        raise ValueError('new fixture directory required')
    root.mkdir(parents=True)
    project = root / 'project'
    project.mkdir()
    service.configure(project, {**service.DEFAULT, 'scopes': ['project'],
                                'automatic': False, 'embedding': '', 'retention': 'summary'})
    manifest = {'schema': 'cc-continuity-v1', 'cases': cases(),
                'provenance': 'curated benchmark checkpoints; no human timing observations'}
    for case in manifest['cases']:
        service.conversation(project, {'id': case['id'], 'title': case['title'],
            'summary': case['summary'], 'retention': 'summary', 'status': 'interrupted', 'turns': []})
    service.reconcile(project)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def probe(root, identifier):
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    case = next(c for c in manifest['cases'] if c['id'] == identifier)
    project = root / 'project'
    started = time.perf_counter()
    saved = service.read_conversation(project, identifier)
    result = service.query(project, case['marker'], False)
    elapsed_ms = (time.perf_counter() - started) * 1000
    failures = validate(case, saved, result)
    return {'id': identifier, 'passed': not failures, 'failures': failures,
            'automated_service_ms': round(elapsed_ms, 3),
            'cited_sources': [c['source'] for c in result['citations']]}


def run(root):
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema'] != 'cc-continuity-v1' or len(manifest['cases']) != 20:
        raise ValueError('unexpected continuity manifest')
    results = []
    for case in manifest['cases']:
        started = time.perf_counter()
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                    str(root), '--probe', case['id']], capture_output=True,
                                   text=True, timeout=30, check=False)
        wall_ms = (time.perf_counter() - started) * 1000
        if completed.returncode:
            results.append({'id': case['id'], 'passed': False,
                            'failures': ['probe_process_failed'], 'error': completed.stderr[-1000:],
                            'automated_wall_ms': round(wall_ms, 3)})
        else:
            result = json.loads(completed.stdout)
            result['automated_wall_ms'] = round(wall_ms, 3)
            results.append(result)
    wall = [item['automated_wall_ms'] for item in results]
    return {'schema': 'cc-continuity-report-v1', 'fixture': str(root),
            'cases': results, 'count': len(results),
            'passed': sum(item['passed'] for item in results),
            'all_passed': all(item['passed'] for item in results),
            'automated_wall_median_ms': round(statistics.median(wall), 3),
            'automated_wall_p95_ms': round(sorted(wall)[18], 3),
            'human_resume_times_ms': None, 'human_comparison': 'not measured',
            'human_time_improvement_percent': None,
            'scope': 'Durable summary and lexical source continuity only; no model calls or human resumption.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--check', action='store_true', help='check an existing fixture')
    parser.add_argument('--probe', metavar='IDENTIFIER', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = external_root(args.fixture)
    if args.probe:
        print(json.dumps(probe(root, args.probe)))
        return 0
    if not args.check:
        create(root)
    report = run(root)
    print(json.dumps(report, indent=2))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
