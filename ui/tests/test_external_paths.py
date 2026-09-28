"""Launcher preflight must reject a source/profile overlap before Electron runs."""
import base64
import json
from pathlib import Path
import subprocess
import sys

import pytest

ENTRY = Path(__file__).resolve().parents[1] / 'observer_paths.py'


@pytest.mark.parametrize('case', ['ordinary', 'source_profile', 'source_workspace', 'workspace_profile',
                                  'descriptor_profile', 'descriptor_mismatch'])
def test_external_launcher_binding_before_profile_creation(tmp_path, case):
    source, workspace, package = [tmp_path / p for p in ('source', 'workspace', 'package')]
    for p in (source, workspace, package):
        p.mkdir()
    sentinel = source / 'keep.txt'
    sentinel.write_bytes(b'original')
    original_stamp = sentinel.stat().st_mtime_ns
    profile = tmp_path / 'profile'
    bound = source
    if case == 'source_profile':
        profile = source / 'profile'
    if case == 'source_workspace':
        workspace = source / 'workspace'
    if case == 'workspace_profile':
        profile = workspace / 'profile'
    if case == 'descriptor_profile':
        profile = source / 'profile'
        source_arg = ''
    else:
        source_arg = str(source)
    if case == 'descriptor_mismatch':
        bound = tmp_path / 'other'
        bound.mkdir()
    if case in ('descriptor_profile', 'descriptor_mismatch'):
        (workspace / 'external.json').write_text(json.dumps({'source': str(bound)}), encoding='utf-8')
    request = {'profile': str(profile), 'project': '', 'package': str(package),
               'external': True, 'source': source_arg, 'workspace': str(workspace)}
    payload = base64.b64encode(json.dumps(request).encode()).decode()
    result = subprocess.run([sys.executable, '-I', '-B', str(ENTRY), payload], capture_output=True, timeout=10)
    assert (result.returncode == 0) == (case == 'ordinary')
    assert not profile.exists()
    assert sentinel.read_bytes() == b'original' and sentinel.stat().st_mtime_ns == original_stamp
    assert sorted(p.name for p in source.iterdir()) == ['keep.txt']
    if case == 'ordinary':
        assert json.loads(result.stdout)['source'] == str(source.resolve())
