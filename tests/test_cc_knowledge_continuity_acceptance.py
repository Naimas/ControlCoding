"""Continuity acceptance uses durable service records and separate processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import knowledge_continuity_acceptance as benchmark
from cc_memory_lib import knowledge_service as service


SCRIPT = Path(benchmark.__file__).resolve()


def invoke(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=180, check=False,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def test_twenty_durable_resumptions_in_fresh_processes(tmp_path):
    fixture = tmp_path / 'continuity'
    completed = invoke(fixture)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert report['count'] == report['passed'] == 20
    assert report['all_passed']
    assert len({row['id'] for row in report['cases']}) == 20
    assert all(row['automated_wall_ms'] > 0 and row['automated_service_ms'] >= 0
               and 'conversation:' + row['id'] in row['cited_sources'] for row in report['cases'])
    assert report['human_resume_times_ms'] is None
    assert report['human_time_improvement_percent'] is None
    assert (fixture / 'project' / '.controlcoding' / 'knowledge' / 'knowledge.db').is_file()
    assert invoke(fixture, '--check').returncode == 0


@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_missing_or_changed_blocker_fails_after_persisted_update(tmp_path, mutation):
    fixture = tmp_path / 'continuity'
    benchmark.create(benchmark.external_root(fixture))
    case = benchmark.cases()[0]
    changed_summary = case['summary'].replace(
        case['blocker'], '' if mutation == 'missing' else 'A different blocker is open.')
    service.conversation(fixture / 'project', {'id': case['id'], 'title': case['title'],
        'summary': changed_summary, 'retention': 'summary', 'status': 'interrupted', 'turns': []})
    service.reconcile(fixture / 'project')
    completed = invoke(fixture, '--probe', case['id'])
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert not result['passed']
    assert 'blocker_missing_or_changed' in result['failures']
    assert 'indexed_blocker_missing_or_changed' in result['failures']
    assert 'source_content_changed' in result['failures']
