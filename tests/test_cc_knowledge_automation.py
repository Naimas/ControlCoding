"""Fresh event/restart matrix; no simulation of real human adoption time."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service


@pytest.mark.parametrize('event', ['manual', 'startup', 'timer', 'commit-ceremony',
                                  'source-change', 'worker', 'conversation'])
def test_event_refresh_retains_only_current_evidence(tmp_path, event):
    root = tmp_path / 'project'
    (root / 'docs').mkdir(parents=True)
    source = root / 'docs' / 'decision.md'
    source.write_text('# Decision\nOldtoken choice.', encoding='utf-8')
    service.configure(root, {**service.DEFAULT, 'embedding': '', 'automatic': False})
    service.reconcile(root)
    source.write_text('# Decision\nNewtoken choice.', encoding='utf-8')
    updated = service.reconcile(root, event)
    assert updated['generation'] == 2
    assert not updated['needs_reconcile']
    assert not service.query(root, 'Oldtoken', False)['citations']
    assert service.query(root, 'Newtoken', False)['citations']
    assert service.reconcile(root, event)['generation'] == 2
    cli = Path(__file__).resolve().parents[1] / 'scripts/cc_knowledge.py'
    proc = subprocess.run([sys.executable, '-I', '-B', str(cli), '--project-root',
                           str(root), 'status'], capture_output=True, text=True,
                          timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    restored = json.loads(proc.stdout)
    assert restored['generation'] == 2 and not restored['policy']['automatic']


def test_paused_policy_survives_process_restart_and_manual_retry(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'README.md').write_text('# Notes\nOne decision.', encoding='utf-8')
    config = {**service.DEFAULT, 'embedding': '', 'automatic': False, 'worker': False}
    service.configure(root, config)
    service.reconcile(root)
    cli = Path(__file__).resolve().parents[1] / 'scripts/cc_knowledge.py'
    result = subprocess.run([sys.executable, '-I', '-B', str(cli), '--project-root',
                             str(root), '--event', 'startup', 'sync'], capture_output=True,
                            text=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0
    state = json.loads(result.stdout)
    assert state['policy'] == config
    assert not state['needs_reconcile']
    assert state['jobs'][0]['state'] == 'done'
