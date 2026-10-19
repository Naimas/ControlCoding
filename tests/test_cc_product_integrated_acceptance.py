"""PW07 independent CLI journeys, also runnable against an extracted wheel.

CC_PW07_RUNTIME selects the directory containing cc.py (or a source root with
scripts/cc.py). Every journey gets a fresh process and physical project. Fixture
authors are reused, but their transitions execute the selected public CLI.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    # -I deliberately excludes user-site packages. Fixture authors use pytest
    # only as a test dependency; resolve its already installed parent explicitly,
    # then remove the path before any Core evidence context is captured.
    dependency_path = os.environ['CC_PW07_TEST_DEPENDENCIES']
    assert (Path(dependency_path) / 'pytest/__init__.py').is_file()
    sys.path.insert(0, dependency_path)
    import pytest
    sys.path.remove(dependency_path)
else:
    import pytest


SOURCE = Path(__file__).resolve().parents[1]
SCENARIOS = ('S01_creation', 'S02_continuation', 'S03_independent',
             'S04_requirement', 'S05_adoption', 'S06_incident')


def runtime_directory(value):
    runtime = Path(value).resolve()
    if (runtime / 'scripts/cc.py').is_file():
        runtime /= 'scripts'
    assert (runtime / 'cc.py').is_file(), runtime
    return runtime


@pytest.mark.parametrize('scenario', SCENARIOS, ids=SCENARIOS)
def test_pw07_independent_cli_journey(tmp_path, scenario):
    runtime = runtime_directory(os.environ.get('CC_PW07_RUNTIME', SOURCE))
    result = subprocess.run(
        [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
         '--journey', scenario, str(runtime), str(tmp_path / scenario)],
        cwd=runtime, capture_output=True, text=True, encoding='utf-8',
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
             'CC_PW07_TEST_DEPENDENCIES': str(Path(pytest.__file__).resolve().parent.parent)},
        # S06 also fills the actual storage boundary, checkpoints/replays the
        # retained history, then plans and verifies a fresh incident child.
        # Bound that cold-process journey separately from the shorter cases.
        timeout=900 if scenario == 'S06_incident' else 420)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['scenario'] == scenario and report['status'] == 'passed'
    assert report['runtime'] == str(runtime)
    assert report['cli_transitions'] > 0 and report['modules']
    assert all(Path(path).resolve().is_relative_to(runtime)
               for path in report['modules'].values())
    print(json.dumps(report, sort_keys=True))


def journey(scenario, runtime, root):
    import contextlib
    import copy
    import importlib.abc
    import importlib.machinery
    import types

    # Legacy fixture modules insert source/scripts into sys.path. Pin the actual
    # module resolver before importing them, then remove those discovery paths.
    # Missing wheel modules fail closed rather than falling back to the checkout.
    class OwnerFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != 'cc_product_progress_fixture' and (fullname == 'cc' or fullname.startswith('cc_')):
                spec = importlib.machinery.PathFinder.find_spec(
                    fullname, [str(runtime)] if path is None else path)
                if spec is None:
                    raise ImportError('Selected runtime lacks ' + fullname)
                if spec.origin and spec.origin != 'namespace':
                    assert Path(spec.origin).resolve().is_relative_to(runtime), spec.origin
                return spec
            return None

    sys.meta_path.insert(0, OwnerFinder())
    isolated_paths = list(sys.path)
    sys.path[:0] = [str(runtime), str(SOURCE / 'tests')]
    # selection() is a pure fixture constructor in a module that also owns
    # unrelated private-bridge tests. The public wheel deliberately omits that
    # adapter. Any attempt to use it in these CLI journeys must fail explicitly.
    unused_bridge = types.ModuleType('cc_panel_bridge')
    def private_bridge_unavailable(name):
        raise AssertionError('Private bridge is outside the public CLI journey: ' + name)
    unused_bridge.__getattr__ = private_bridge_unavailable
    sys.modules['cc_panel_bridge'] = unused_bridge
    import cc_development_lib as core
    import cc_product
    import cc_product_documents as documents
    import cc_development_jobs as jobs
    import test_cc_development_core as basic
    import test_cc_product_core as product_fixture
    import test_cc_product_evolution_acceptance as evolution
    import test_cc_product_evolution_transport as transport
    import cc_product_progress_fixture as fixture
    sys.path[:] = [str(runtime), *isolated_paths]
    root.mkdir(parents=True)
    command_root = root.parent / (root.name + '-commands')
    command_root.mkdir()
    transcript = []
    last_view = {'value': None}
    capacity_observation = None
    bootstrap = (
        "import runpy,sys\nfrom pathlib import Path\n"
        "r=Path(sys.argv[1]).resolve();sys.path.insert(0,str(r))\n"
        "sys.argv=[str(r/'cc.py'),*sys.argv[2:]]\n"
        "try:\n    runpy.run_path(str(r/'cc.py'),run_name='__main__')\n"
        "finally:\n"
        "    for n,m in list(sys.modules.items()):\n"
        "        if (n=='cc' or n.startswith('cc_')) and getattr(m,'__file__',None):\n"
        "            assert Path(m.__file__).resolve().is_relative_to(r),(n,m.__file__)\n")

    def cli(*args, code=0):
        command = [sys.executable, '-I', '-B', '-c', bootstrap, str(runtime), *map(str, args)]
        result = subprocess.run(command, cwd=runtime, capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
        transcript.append({'args': list(map(str, args)), 'exit': result.returncode})
        (command_root / 'transcript.json').write_text(json.dumps(transcript, indent=2), encoding='utf-8')
        assert (result.returncode in (0, 1) if code is None else result.returncode == code), (args, result.stdout, result.stderr)
        return json.loads(result.stdout)

    def read():
        last_view['value'] = cli('development', 'status', '--project-root', root, '--json')
        return last_view['value']

    def change(cid='C1'):
        # Fixture construction can inspect the selected owner in-process. User
        # transitions and milestone readback still execute fresh public CLIs.
        return next(row for row in last_view['value']['changes'] if row['id'] == cid)

    def preview(action, revision=None, code=0):
        path = command_root / ('action-%04d.json' % len(transcript))
        path.write_text(json.dumps(action), encoding='utf-8')
        revision = revision or last_view['value']['revision']
        domain = 'product' if action['kind'].startswith('product_') else 'development'
        value = cli(domain, 'preview', '--project-root', root, '--action-file', path,
                    '--revision', revision, '--json', code=code)
        return domain, path, revision, value

    def apply(prepared, code=0):
        domain, path, revision, value = prepared
        result = cli(domain, 'apply', '--project-root', root, '--action-file', path,
                     '--revision', revision, '--approval-id', value['approval_id'], '--json', code=code)
        if 'view' in result:
            last_view['value'] = result['view']
        return result

    def act(_root, action):
        assert Path(_root) == root
        if action.get('change_id') == 'C5':
            action = copy.deepcopy(action)
            if action['kind'] == 'product_specification_v2':
                action['content']['requirements'].append({
                    'id': 'operational-readiness', 'text': 'Operators can query local account readiness during incident follow-up'})
                action['content']['criteria'].append({
                    'id': 'operational-readiness', 'requirement_id': 'operational-readiness',
                    'text': 'The account_status function reports ready without creating an account',
                    'required': True, 'proof': 'automated', 'checks': [{'kind': 'verification', 'id': 'readiness'}]})
                action = product_fixture.detailed_action(root, {
                    'kind': 'product_specification', 'change_id': 'C5', 'content': action['content'],
                    'mappings': action['mappings'], 'actor': action['actor']})
                extra = next(row for row in action['details']['requirement_details'] if row['requirement_id'] == 'operational-readiness')
                extra['provenance'] = {'kind': 'explicit', 'source_refs': [], 'product_criterion_ids': [],
                    'rationale': 'Explicit bounded readiness diagnostic arising from the retained rejected-request incident'}
            elif action['kind'] == 'product_plan_v2':
                action['content']['criterion_check_mapping']['operational-readiness'] = ['readiness']
            elif action['kind'] == 'artifact' and action['stage'] == 'verify':
                action['content']['criterion_coverage']['operational-readiness'] = ['readiness']
        if scenario == 'S05_adoption' and action['kind'] in ('product_specification_v2', 'product_plan_v2'):
            action = copy.deepcopy(action)
            details = action['details']
            refs = [{'source_id': source['source_id'], 'item_id': item['id']}
                    for source in details['sources'] for item in documents.source_items(source)
                    if item['selected'] and item['disposition'] == 'mapped']
            for mapping in details.get('condition_mappings', []):
                mapping['source_refs'] = copy.deepcopy(refs)
            for row in details.get('requirement_details', []) + details.get('task_details', []):
                row['provenance'].update(kind='source', source_refs=copy.deepcopy(refs))
        prepared = preview(action)
        assert not prepared[-1]['blockers'], prepared[-1]
        return apply(prepared)['view']

    def blocked(action):
        before = read()
        # Product preview reports a successfully inspected blocked operation as
        # exit 0; Development preview uses exit 1. Both must expose blockers and
        # the real apply must fail without changing state.
        prepared = preview(action, code=None)
        assert prepared[-1].get('blockers') or prepared[-1].get('error'), prepared[-1]
        if 'approval_id' in prepared[-1]:
            apply(prepared, code=1)
        assert read() == before
        return prepared[-1]

    def receipts(_root):
        cli('verify', 'run', '--project-root', root, '--json')
        cli('invariants', 'run', '--project-root', root, '--json')

    def job(cid, kind, adapter):
        args = ['development', 'job-preview', cid, '--project-root', root,
                '--kind', kind, '--adapter-id', adapter, '--json']
        prepared = cli(*args)
        assert not prepared.get('blockers'), prepared
        args[1] = 'job-run'
        return cli(*args, '--approval-id', prepared['approval_id'])

    @contextlib.contextmanager
    def cli_authors():
        modules = [basic, product_fixture, evolution, transport, fixture]
        original = [(module, module.act) for module in modules]
        original_receipts = evolution.receipts, fixture.receipts
        try:
            for module in modules:
                module.act = act
            evolution.receipts = fixture.receipts = receipts
            yield
        finally:
            for module, previous in original:
                module.act = previous
            evolution.receipts, fixture.receipts = original_receipts

    def seed(doc=None, *, adoption=None):
        assert read()['product']['document'] is None
        assert not list(root.iterdir()), 'Opening a new target wrote files'
        fixture.git(root, 'init', '--quiet')
        fixture.git(root, 'config', 'user.name', 'PW07 local fixture')
        fixture.git(root, 'config', 'user.email', 'pw07@example.invalid')
        (root / '.gitignore').write_text('.controlcoding/\n__pycache__/\n', encoding='utf-8')
        initial_accounts = ('value = 0\ndef create_account(name, accounts):\n'
                            '    key = str(name) + "-" + str(len(accounts))\n'
                            '    accounts[key] = {"name": name}\n'
                            '    return accounts[key]\n' if scenario == 'S05_adoption' else 'value = 0\n')
        (root / 'accounts.py').write_bytes(initial_accounts.encode('utf-8'))
        fixture.git(root, 'add', '.gitignore', 'accounts.py')
        fixture.git(root, 'commit', '--quiet', '-m', 'Original local source')
        evolution.combined_contracts(root)
        (root / 'accounts.py').write_bytes(initial_accounts.encode('utf-8'))
        (root / 'check_combined.py').write_text(
            "import runpy\na=runpy.run_path('accounts.py');r=runpy.run_path('reports.py')\n"
            "assert a['value']==1 and r['value']==2\n"
            "accounts={};first=a['create_account']('member',accounts)\n"
            "assert a['create_account']('member',accounts) is first and len(accounts)==1\n"
            "try: a['create_account']('',accounts)\n"
            "except ValueError: pass\n"
            "else: raise AssertionError('Invalid details created an account')\n"
            "if 'account_status' in a: assert a['account_status']()=='ready'\n"
            "if 'report' in r: assert r['report']()==a['value']\n", encoding='utf-8')
        # Adapters read materialized local output and environment. The expected
        # candidate is passed only to the observation envelope; deployed identity
        # and metric come from independently written fixture files.
        (root / 'status.py').write_text(
            "import hashlib,json,runpy,sys,subprocess\nfrom pathlib import Path\n"
            "output=json.loads(Path('dist/deployment.json').read_text())\n"
            "for item in output.pop('files'):\n"
            "    actual=Path('dist')/item['path']\n"
            "    assert hashlib.sha256(actual.read_bytes()).hexdigest()==item['sha256']\n"
            "    assert actual.read_bytes()==Path(item['path']).read_bytes()\n"
            "app=runpy.run_path('dist/accounts.py');accounts={}\n"
            "first=app['create_account']('local-probe',accounts)\n"
            "assert app['create_account']('local-probe',accounts) is first and len(accounts)==1\n"
            "print(json.dumps({'candidateDigest':sys.argv[1],**output,"
            "'head':subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()}))\n",
            encoding='utf-8')
        (root / 'monitor.py').write_text(
            "import hashlib,json,runpy,sys\nfrom pathlib import Path\n"
            "output=json.loads(Path('dist/deployment.json').read_text())\n"
            "for item in output['files']:\n"
            "    assert hashlib.sha256((Path('dist')/item['path']).read_bytes()).hexdigest()==item['sha256']\n"
            "app=runpy.run_path('dist/accounts.py');traffic=json.loads(Path('dist/traffic.json').read_text())\n"
            "accounts={};rejections=0\n"
            "for request in traffic:\n"
            "    try: app['create_account'](request,accounts)\n"
            "    except ValueError: rejections+=1\n"
            "print(json.dumps({'candidateDigest':sys.argv[1],"
            "'deployedDigest':output['deployedDigest'],'window':'hour','metrics':"
            "{'invalid-rejections':{'value':rejections,'samples':len(traffic),"
            "'citation':'materialized-output:accounts.py:create_account:traffic.json'}}}))\n",
            encoding='utf-8')
        fixture.git(root, 'add', 'accounts.py', 'reports.py', 'check_combined.py',
                    'controlcoding.verification.json', 'controlcoding.invariants.json', 'status.py', 'monitor.py')
        fixture.git(root, 'commit', '--quiet', '-m', 'Observed local behavior fixture')
        if scenario == 'S05_adoption':
            probe = subprocess.run([sys.executable, '-I', '-B', '-c',
                "import runpy;app=runpy.run_path('accounts.py');accounts={};"
                "app['create_account']('member',accounts);app['create_account']('member',accounts);"
                "assert len(accounts)==2;print('Observed existing account creation and duplicate behavior')"],
                cwd=root, capture_output=True, text=True, timeout=30)
            assert probe.returncode == 0, probe.stdout + probe.stderr
            original_blob = subprocess.check_output(
                ['git', '-c', 'core.hooksPath=', 'show', 'HEAD^:accounts.py'], cwd=root)
            assert original_blob == initial_accounts.encode('utf-8') == (root / 'accounts.py').read_bytes()
        doc = doc or evolution.two_features()
        if adoption:
            adoption(doc)
        else:
            product_fixture.saved(root, doc)
        draft = read()
        assert not draft['enabled'] and not draft['changes']
        assert not (root / 'memory').exists()
        evolution.accept_document(root, doc) if not adoption else act(root, {
            'kind': 'product_decide', 'product_revision': draft['product']['current_revision'],
            'decision': 'accepted', 'actor': 'Owner', 'reason': 'Reviewed selected mature source conditions'})
        blocked(transport.selection(root))
        act(root, {'kind': 'activate', 'profile': 'routine'})
        if scenario == 'S04_requirement':
            missing_contributor = evolution.shared_selection(root)
            missing_contributor['changes'] = missing_contributor['changes'][:1]
            blocked(missing_contributor)
        selected = transport.selection(root)
        prepared = preview(selected)
        result = apply(prepared)
        assert apply(prepared)['receipt']['idempotent']
        assert len(result['view']['changes']) == 1
        assert change()['decisions'] == []

    def plan(cid='C1', filename='accounts.py'):
        fixture.fixture_plan(root, cid, filename)
        assert change(cid)['gates']['plan']['accepted']

    def implement(cid='C1', filename='accounts.py'):
        before = change(cid)
        original_decisions = copy.deepcopy(before['decisions'])
        raw = (root / filename).read_bytes()
        account_source = ('value = 1\ndef create_account(name, accounts):\n'
                          '    if not isinstance(name, str) or not name.strip():\n'
                          '        raise ValueError("Valid account details required")\n'
                          '    return accounts.setdefault(name.strip(), {"name": name.strip()})\n')
        if cid == 'C1':
            content = account_source
        elif cid == 'C5':
            content = account_source + 'def account_status():\n    return "ready"\n'
        else:
            content = 'value = 2\ndef report():\n    from accounts import value\n    return value\n'
        expected = content.encode('utf-8')
        if raw == expected:
            return  # Requirement adoption rebuilds proof for retained source.
        (root / filename).write_bytes(expected)
        read()
        current = change(cid)
        assert 'product_plan_source_changed' in current['gates']['plan']['reasons']
        # Preserve the accepted plan's tasks and specification. Only re-observe
        # the changed file scope and request the necessary new Owner approval.
        act(root, product_fixture.detailed_action(root, {
            'kind': 'product_plan', 'change_id': cid,
            'content': before['artifacts']['plan'][-1]['content'],
            'files': documents.observe_files(root, [filename]), 'actor': 'Writer'}))
        evolution.decision(root, cid, 'plan')
        assert change(cid)['decisions'][:len(original_decisions)] == original_decisions
        assert change(cid)['gates']['plan']['accepted']
        # The supported local release observer binds candidate bytes to the
        # observed Git HEAD. Commit only this intentional fixture implementation
        # after its renewed source review, before candidate capture and receipts.
        fixture.git(root, 'add', '--', filename)
        fixture.git(root, 'commit', '--quiet', '-m', 'Implement reviewed local fixture ' + cid)

    def build_verify(cid='C1', filename='accounts.py'):
        implement(cid, filename)
        evolution.build_change(root, cid, filename)
        receipts(root)
        evolution.verify_change(root, cid)
        assert change(cid)['outcomes']['verified']

    def observed_release(*, incident=False):
        candidate = change()['candidate']['digest']
        rejected = blocked({'kind': 'review', 'change_id': 'C1', 'findings': [],
                            'actor': 'Writer', 'reason': 'Same implementer cannot review their own candidate'})
        assert 'independent_actor_required' in json.dumps(rejected)
        act(root, {'kind': 'review', 'change_id': 'C1', 'findings': [],
                   'actor': 'Reviewer', 'reason': 'Independent review of exact local behavior'})
        evolution.stage(root, 'C1', 'review_release', {
            'candidate_identity': candidate, 'review_findings': [], 'finding_resolutions': [],
            'required_approvals': [], 'environment': 'local', 'release_artifact_identity': candidate,
            'rollback_readiness': 'Restore original local source and retain control history',
            'observation_provenance': 'self_reported'})
        evolution.decision(root, 'C1', 'review_release')
        blocked({'kind': 'release', 'change_id': 'C1', 'environment': 'local',
                 'artifact_digest': 'f' * 64, 'status': 'self_reported', 'actor': 'ReleaseOwner',
                 'reason': 'A different artifact is outside the reviewed release authorization'})
        release = {'kind': 'release', 'change_id': 'C1', 'environment': 'local',
                   'artifact_digest': candidate, 'status': 'self_reported', 'actor': 'ReleaseOwner',
                   'reason': 'Retain owner attestation separately from local output observation'}
        act(root, release)
        assert not change()['outcomes']['released']
        (root / 'dist').mkdir()
        (root / 'dist/accounts.py').write_bytes((root / 'accounts.py').read_bytes())
        (root / 'dist/deployment.json').write_text(json.dumps({
            'deployedDigest': candidate, 'environment': 'local', 'status': 'deployed',
            'files': copy.deepcopy(change()['candidate']['files']),
            'base': fixture.git(root, 'rev-parse', 'HEAD^')}), encoding='utf-8')
        (root / 'dist/traffic.json').write_text(json.dumps([''] * 12 if incident else ['member'] * 4), encoding='utf-8')
        adapters = []
        for adapter, kind, filename, configuration in [
                ('local-output', 'release_status', 'status.py', {'environment': 'local', 'artifactDigest': candidate}),
                ('local-metric', 'monitor', 'monitor.py', {'environment': 'local', 'deployedDigest': candidate,
                 'window': 'hour', 'metrics': [{'id': 'invalid-rejections', 'baseline': 0, 'maxDelta': 3, 'minimumSamples': 2}]})]:
            adapters.append({'id': adapter, 'kind': kind,
                'argv': [sys.executable, '-B', filename, '{candidateDigest}'],
                'timeoutSeconds': 10, 'outputLimitBytes': 8192,
                'files': [{'path': filename, 'sha256': hashlib.sha256((root / filename).read_bytes()).hexdigest()}],
                'configuration': configuration})
        (root / jobs.MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'adapters': adapters}), encoding='utf-8')
        # Adding the adapter manifest changes evidence inputs; refresh before
        # the actual release decision rather than pretending old proof is fresh.
        receipts(root)
        observation = job('C1', 'release_status', 'local-output')
        release.update(status='observed', adapter_kind='release_status', observation=observation,
                       reason='Observe actual materialized local output; no external deployment')
        act(root, release)
        assert change()['outcomes']['released']
        assert change()['stage'] == 'maintain'
        return job('C1', 'monitor', 'local-metric')

    def progress(**selectors):
        args = ['product', 'progress', '--project-root', root, '--json']
        for key, value in selectors.items():
            args.extend(['--' + key.replace('_', '-'), value])
        return cli(*args)

    def capacity_fixture():
        import cc_development_sessions as sessions
        import cc_development_store as store
        from cc_evidence_inputs import SafeRoot
        sessions.create(root, 'pw07-capacity', read()['revision'], 'Owner',
                        hashlib.sha256(b'pw07-capacity-create').hexdigest())
        session = sessions.read(root, 'pw07-capacity')
        sessions.append(root, 'pw07-capacity', session['revision'],
            hashlib.sha256(b'pw07-saved-capacity-turn').hexdigest(), 'Owner', 'user',
            'Retain this saved operational handoff text across quota failure and checkpoint replay.')
        session = sessions.read(root, 'pw07-capacity')
        paths = []
        with SafeRoot(root) as safe:
            total = store.ensure_planning_capacity(safe)
            index = 0
            while total < store.PLANNING_LIMIT:
                envelope = {'schema_version': 1, 'domain': 'planning', 'payload': {
                    'kind': 'pw07_retained_capacity_fixture', 'index': index, 'content': 'x' * (128 * 1024)}}
                raw = store._encoded(envelope)
                ref = hashlib.sha256(raw).hexdigest()
                relative = '.controlcoding/development/objects/' + ref + '.json'
                safe.write_atomic(relative, envelope, replace_existing=False)
                paths.append((root / relative, ref))
                total += len(raw)
                index += 1
            with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
                store.ensure_planning_capacity(safe)
        with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
            sessions.append(root, 'pw07-capacity', session['revision'],
                hashlib.sha256(b'pw07-full-capacity-append').hexdigest(), 'Owner', 'user', 'Preserve existing saved text')
        assert sessions.read(root, 'pw07-capacity') == session
        # A fresh current-method project has no genuine migration metadata to
        # grow. Exercise its physical JSON input boundary with legal whitespace,
        # preserving every parsed value and authenticated lineage field exactly.
        manifest = root / '.controlcoding/development.json'
        original = manifest.read_bytes()
        boundary_size = store.MANIFEST_LIMIT - store.PLANNING_RESERVE - 1024
        assert len(original) < boundary_size
        padded = original + b' ' * (boundary_size - len(original))
        manifest.write_bytes(padded)
        assert json.loads(original) == json.loads(padded)
        read()  # The exact new physical revision must be used by the next CLI action.
        assert read()['revision'] == hashlib.sha256(padded).hexdigest()
        boundary = {'original_bytes': len(original), 'padded_bytes': len(padded),
                    'planning_threshold': store.MANIFEST_LIMIT - store.PLANNING_RESERVE,
                    'parsed_semantics_preserved': True,
                    'scope': 'Physical input/read boundary; operational writes compact legal whitespace'}
        return session, paths, total, boundary

    def capacity_checkpoint(session, paths, total, boundary):
        import cc
        import cc_development_sessions as sessions
        import cc_development_store as store
        records = []
        for kind in ('verification', 'invariants'):
            prepared = cc._evidence_prepare(root, kind)
            observed = cc._evidence_history(root, kind, prepared)
            assert observed['assessment']['currentRequiredPass'], observed
            receipt = observed['latest']
            path = Path(receipt['path'])
            path = path if path.is_absolute() else root / path
            raw = path.read_bytes()
            identity = json.loads(raw)['id']
            records.append({'id': identity, 'kind': kind, 'receiptId': identity,
                            'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()})
        proof = preview({'kind': 'evidence', 'change_id': 'C1', 'records': records, 'actor': 'Verifier'})
        apply(proof)
        checkpoint = cli('development', 'checkpoint-preview', '--project-root', root, '--json')
        cli('development', 'checkpoint', '--project-root', root, '--revision', checkpoint['revision'],
            '--approval-id', checkpoint['approval_id'], '--actor', 'Owner', '--json')
        assert apply(proof)['receipt']['idempotent']
        assert sessions.read(root, 'pw07-capacity') == session
        # Retire only the explicitly seeded, unreachable capacity objects so the
        # subsequent new child can author planning. No session/history object is
        # trimmed, and each exact fixture path/hash is verified before deletion.
        for path, ref in paths:
            assert path.resolve().is_relative_to(root.resolve())
            assert path.name == ref + '.json' and hashlib.sha256(path.read_bytes()).hexdigest() == ref
            path.unlink()
        assert sessions.read(root, 'pw07-capacity') == session
        return {'planning_limit': store.PLANNING_LIMIT, 'occupied_bytes': total,
                'synthetic_objects': len(paths), 'session_preserved': True,
                'manifest_boundary': boundary,
                'full_quota_operations': ['incident_capture', 'incident_decide', 'evidence', 'checkpoint', 'exact_replay'],
                'synthetic_capacity_retired_before_new_child_planning': True}

    with cli_authors():
        if scenario == 'S05_adoption':
            import cc_product_adoption as adoption
            # A sanitized mature source has implemented code, future requests,
            # contradictory historical claims and unmanaged original bytes.
            legacy = {'scope': 'Valid details create exactly one account',
                'phases': [{'id': 'existing', 'status': 'COMPLETED', 'features': [
                    {'id': 'auth', 'description': 'Existing account implementation',
                     'acceptance': ['Valid details create exactly one account']}]}],
                'architecture': {'files': ['accounts.py']}, 'expansion_state': {'future': 'Reports'},
                'invariants_global': ['Never weaken authorization'],
                'contradiction': 'Historical claim: anonymous access is acceptable.'}
            originals = {'brief.json': json.dumps(legacy).encode(),
                         'operator-notes.txt': b'Unmanaged owner notes; retain exactly.\n'}

            def adopt(doc):
                for name, raw in originals.items():
                    (root / name).write_bytes(raw)
                source = adoption.observe(root, 'brief.json')
                selected = next(row for row in source['items'] if row['pointer'] == '/scope')
                selected.update(selected=True, disposition='mapped', targets=['signup-success'],
                                reason='Retain exact mandatory selected security wording')
                assert any(row['disposition'] == 'unresolved' for row in source['items'])
                doc['sources'] = [source]
                doc['features'][0]['source_references'] = [source['source_id']]
                doc['features'][0]['criteria'][0]['text'] = selected['text']
                act(root, {'kind': 'product_adoption_save', 'document': doc, 'base_revision': 'absent',
                           'sources': [source], 'resolutions': [], 'actor': 'Author'})

            seed(adoption=adopt)
            assert not change()['outcomes']['verified'], 'Observed code became verified capability'
            plan()
            build_verify()
            for name, raw in originals.items():
                assert (root / name).read_bytes() == raw
            assert 'key = str(name)' in fixture.git(root, 'show', 'HEAD^:accounts.py')
            source = read()['product']['document']['sources'][0]
            assert sum(row['selected'] for row in source['items']) == 1 < len(source['items'])
            (root / 'brief.json').write_bytes(originals['brief.json'] + b'\n')
            read()
            assert change()['gates']['plan']['outcome'] != 'allowed'
            (root / 'brief.json').write_bytes(originals['brief.json'])
        else:
            seed()
            blocked({'kind': 'decide', 'change_id': 'C1', 'stage': 'design',
                     'decision': 'accepted', 'actor': 'Owner', 'reason': 'An incomplete specification cannot pass'})
            plan()
            if scenario == 'S02_continuation':
                # Every read and transition is a cold CLI process. Preserve the
                # accepted draft identity and pending build across explicit reopen.
                accepted = read()
                assert read() == accepted
                implement()
                accepted = read()
                pending = preview({'kind': 'candidate', 'change_id': 'C1',
                                   'files': ['accounts.py'], 'actor': 'Writer'})
                assert read() == accepted
                built = apply(pending)
                assert apply(pending)['receipt']['idempotent']
                assert read()['revision'] == built['view']['revision']
                evolution.stage(root, 'C1', 'build', {
                    'approved_plan_revision': change()['artifacts']['plan'][-1]['revision'],
                    'source_identity': change()['candidate']['digest'],
                    'execution_provenance': {'mode': 'manual', 'description': 'Local CLI continuation'},
                    'deviations': [], 'changed_files': ['accounts.py']})
                evolution.decision(root, 'C1', 'build')
                receipts(root)
                evolution.verify_change(root, 'C1')
                before = read()
                raw = (root / 'accounts.py').read_bytes()
                (root / 'accounts.py').write_bytes(raw + b'# changed after restart\n')
                read()
                assert not change()['outcomes']['verified']
                assert read()['revision'] == before['revision']
                (root / 'accounts.py').write_bytes(raw)
                assert len(read()['changes']) == 1
            elif scenario == 'S04_requirement':
                build_verify()
                original = copy.deepcopy(read()['product']['evolution']['selections'][0])
                old_proof = copy.deepcopy(change()['decisions'])
                # Create the exact dependency while its target is current. New
                # edges to an already obsolete selection are rejected at intake.
                dependent = transport.selection(root)
                dependent.update(increment_id='I2', title='Reports after exact released accounts', features=['reports'],
                    changes=[{'change_id': 'C3', 'title': 'Dependent reports', 'owner': 'Owner', 'risk': 'routine', 'feature_ids': ['reports']}],
                    criteria=[{'criterion_id': 'report-success', 'owner_change_id': 'C3', 'contributor_change_ids': ['C3'], 'integration_check': None}],
                    dependencies=[{'consumer_change_id': 'C3', 'target_change_id': 'C1', 'target_feature_id': 'signup',
                        'target_feature_revision': original['features'][0]['feature_revision'],
                        'target_selection_revision': original['selection_revision'], 'required_result': 'released', 'gate': 'build'}])
                act(root, dependent)
                dependent_decisions = copy.deepcopy(change('C3')['decisions'])
                revised = evolution.two_features()
                revised['features'][0]['outcome'] = 'Members create accounts after explicit validation'
                evolution.accept_document(root, revised)
                assert any(row['required'] for row in read()['product']['evolution']['impacts'])
                act(root, {'kind': 'product_scope', 'actor': 'Owner', 'increment_id': 'I1',
                    'selection_revision': original['selection_revision'],
                    'product_revision': read()['product']['accepted_revision'], 'decision': 'adopt',
                    'reason': 'Adopt exact changed immutable account requirements'})
                assert not change()['gates']['intent']['accepted']
                assert not change()['outcomes']['verified']
                assert read()['product']['evolution']['selections'][0] == original
                assert change()['decisions'][:len(old_proof)] == old_proof
                plan()
                build_verify()
                assert change()['outcomes']['verified']
                # Adoption makes the existing exact target historical. Its old
                # revision cannot satisfy release. Old observed-release and multi-contributor
                # combinations remain in maintained evolution acceptance tests.
                assert 'product_dependency_target_stale' in change('C3')['gates']['build']['reasons']
                assert change('C3')['decisions'] == dependent_decisions
                rejected = blocked({'kind': 'candidate', 'change_id': 'C3', 'files': ['reports.py'], 'actor': 'Writer'})
                assert 'product_dependency_target_stale' in json.dumps(rejected)
            else:
                build_verify()
                observed = observed_release(incident=scenario == 'S06_incident')
                released = copy.deepcopy(change()['releases'])
                if scenario == 'S03_independent':
                    selected = transport.selection(root)
                    selected.update(increment_id='I2', title='Independent reports', features=['reports'],
                        changes=[{'change_id': 'C2', 'title': 'Reports', 'owner': 'Owner', 'risk': 'routine', 'feature_ids': ['reports']}],
                        criteria=[{'criterion_id': 'report-success', 'owner_change_id': 'C2',
                                   'contributor_change_ids': ['C2'], 'integration_check': None}])
                    act(root, selected)
                    assert change('C2')['decisions'] == []
                    assert change()['releases'] == released and change()['outcomes']['released']
                    plan('C2', 'reports.py')
                    build_verify('C2', 'reports.py')
                    assert change()['releases'] == released and change()['gates']['plan']['accepted']
                    # The new independent source commit advances HEAD. The old
                    # observed Git pair is retained history, not a new observation
                    # of this HEAD; it must not remain current delivery proof.
                    assert not change()['outcomes']['released_current_candidate']
                    assert progress(view='feature', entity_id='signup')['summary']['outcome'] != 'released'
                    assert progress(view='change', entity_id='C1', collection='releases')['rows']
                elif scenario == 'S06_incident':
                    session, capacity_paths, occupied, boundary = capacity_fixture()
                    capture = {'kind': 'incident_capture', 'change_id': 'C1', 'receipt_id': observed['receiptId'],
                        'signal_id': 'invalid-rejections', 'actor': 'Operator', 'reason': 'Investigate actual local rejected-request breach'}
                    act(root, capture)
                    boundary['after_first_operational_write_bytes'] = (root / '.controlcoding/development.json').stat().st_size
                    assert boundary['after_first_operational_write_bytes'] < boundary['padded_bytes']
                    incident = change()['incidents'][0]
                    act(root, capture)
                    assert len(change()['incidents']) == 1
                    act(root, {'kind': 'incident_decide', 'change_id': 'C1', 'incident_id': incident['id'],
                        'decision': 'deferred', 'owner': 'Operator', 'actor': 'Operator',
                        'reason': 'Plan bounded local correction with preserved operational origin'})
                    capacity_observation = capacity_checkpoint(session, capacity_paths, occupied, boundary)
                    followup = {'kind': 'incident_followup', 'change_id': 'C1', 'incident_id': incident['id'],
                        'child_id': 'C5', 'title': 'Correct local account observation', 'owner': 'Owner', 'risk': 'routine',
                        'actor': 'Operator', 'reason': 'Create fresh independently approved follow-up'}
                    act(root, followup)
                    assert change('C5')['decisions'] == []
                    blocked(followup)
                    act(root, {'kind': 'product_link_incident', 'change_id': 'C5',
                        'product_revision': read()['product']['accepted_revision'], 'feature_id': 'signup',
                        'actor': 'Owner', 'reason': 'Link retained operational origin to exact current account scope'})
                    (root / 'check_readiness.py').write_text(
                        "import runpy\napp=runpy.run_path('accounts.py');accounts={}\n"
                        "assert app['account_status']()=='ready' and accounts=={}\n", encoding='utf-8')
                    contract_path = root / 'controlcoding.verification.json'
                    contract = json.loads(contract_path.read_text())
                    contract['suites'].append({'id': 'readiness', 'kind': 'targeted', 'required': True,
                        'command': '"' + Path(sys.executable).as_posix() + '" -B check_readiness.py'})
                    contract_path.write_text(json.dumps(contract), encoding='utf-8')
                    plan('C5')
                    build_verify('C5')
                    assert change('C5')['origin']['incident_id'] == incident['id']
                    assert not change('C5')['outcomes']['released']
                    assert change()['releases'] == released and len(change()['incidents']) == 1
                    assert progress(view='change', entity_id='C1', collection='incidents')['rows']
                else:
                    assert scenario == 'S01_creation'
                    assert progress(view='feature', entity_id='signup')['summary']['outcome'] == 'released'

    modules = {name: str(Path(module.__file__).resolve()) for name, module in sys.modules.items()
               if name not in ('cc_panel_bridge', 'cc_product_progress_fixture')
               and (name == 'cc' or name.startswith('cc_'))
               and getattr(module, '__file__', None)}
    assert all(Path(path).is_relative_to(runtime) for path in modules.values()), modules
    final = read()
    semantic = {'enabled': final['enabled'], 'features': [row['id'] for row in final['product']['document']['features']],
        'changes': [{'id': row['id'], 'stage': row['stage'],
                     'gates': {key: {'outcome': gate['outcome'], 'accepted': gate['accepted']}
                               for key, gate in row['gates'].items()},
                     'decisions': [(decision['stage'], decision['decision'], decision['actor'])
                                   for decision in row['decisions']],
                     'releases': [(release['status'], release['environment']) for release in row['releases']],
                     'incident_count': len(row['incidents']),
                    'origin_kind': (row.get('origin') or {}).get('kind')}
                    for row in final['changes']]}
    (command_root / 'transcript.json').write_text(json.dumps(transcript, indent=2), encoding='utf-8')
    return {'scenario': scenario, 'status': 'passed', 'runtime': str(runtime),
            'cli_sha256': hashlib.sha256((runtime / 'cc.py').read_bytes()).hexdigest(),
            'cli_transitions': sum(row['args'][1] == 'apply' for row in transcript),
            'commands': len(transcript), 'modules': modules,
            'semantic_result': semantic,
            'capacity_observation': capacity_observation,
            'manual_implementation': 'Authored file change, stale file review, exact plan re-observation and fresh Owner approval',
            'private_bridge': 'Unused fixture import placeholder; no private adapter executed',
            'scope': 'Local materialized output and metric; no provider or external deployment'}


if __name__ == '__main__':
    assert len(sys.argv) == 5 and sys.argv[1] == '--journey'
    print(json.dumps(journey(sys.argv[2], runtime_directory(sys.argv[3]), Path(sys.argv[4])), sort_keys=True))
