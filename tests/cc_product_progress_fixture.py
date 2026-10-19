"""Real offline Product/Git/native fixture shared by current and authentic runtimes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import cc_product
import cc_product_documents as documents
import cc_development_lib as core
import cc_development_history as history
import cc_development_jobs as jobs
from test_cc_development_core import act
from test_cc_product_core import detailed_action
from test_cc_product_evolution_acceptance import (accept_document,two_features,shared_selection,latest_selection,change,decision,stage,build_change,receipts,verify_change,combined_contracts)

def inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts}

def git(root, *args):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=', *args],
                                   cwd=root, stderr=subprocess.STDOUT).decode().strip()

def fixture_plan(root, cid, filename, branch=False):
    decision(root, cid, 'intent')
    draft = cc_product.template(root, cid, 'design')
    for criterion in draft['content']['criteria']:
        criterion.update(proof='automated', checks=[{'kind': 'verification', 'id': 'combined'}])
    for mapping in draft['mappings']:
        mapping['reason'] = 'Keep the exact required condition and independently check its contribution.'
    act(root, detailed_action(root, {'kind': 'product_specification', 'change_id': cid,
        'content': draft['content'], 'mappings': draft['mappings'], 'actor': 'Writer'}))
    decision(root, cid, 'design')
    draft = cc_product.template(root, cid, 'plan')
    tasks = [{'id': 'implement', 'title': 'Implement selected behavior', 'owner': 'Writer', 'depends_on': []}]
    if branch:
        tasks = [{'id': 'design', 'title': 'Define behavior', 'owner': 'Writer', 'depends_on': []},
                 {'id': 'implement', 'title': 'Implement behavior', 'owner': 'Writer', 'depends_on': ['design']},
                 {'id': 'test', 'title': 'Check independent behavior', 'owner': 'Writer', 'depends_on': ['design']},
                 {'id': 'join', 'title': 'Review both contributions', 'owner': 'Writer', 'depends_on': ['implement', 'test']}]
    draft['content'].update(files=[filename], ownership_scopes=[{'owner': 'Writer', 'files': [filename]}],
        tasks=tasks, verification_strategy='Inspect combined actual source behavior.',
        rollback_assessment='Restore original source bytes and preserve records.')
    draft['content']['criterion_check_mapping'] = {key: ['combined'] for key in draft['content']['criterion_check_mapping']}
    act(root, detailed_action(root, {'kind': 'product_plan', 'change_id': cid, 'content': draft['content'],
        'files': documents.observe_files(root, [filename]), 'actor': 'Writer'}))
    decision(root, cid, 'plan')

def fixture_consumer(root, *, with_origins=False):
    original = next(s for s in core.read(root)['product']['evolution']['selections'] if s['id'] == 'I1')
    feature = original['features'][0]
    consumer = {'kind': 'product_select', 'product_revision': core.read(root)['product']['accepted_revision'],
        'increment_id': 'I2', 'title': 'Reports awaiting exact release', 'features': ['reports'],
        'changes': [{'change_id': 'C3', 'title': 'Dependent reports', 'owner': 'Writer', 'risk': 'routine', 'feature_ids': ['reports']}],
        'criteria': [{'criterion_id': 'report-success', 'owner_change_id': 'C3', 'contributor_change_ids': ['C3'], 'integration_check': None}],
        'dependencies': [{'consumer_change_id': 'C3', 'target_change_id': 'C1', 'target_feature_id': 'signup',
            'target_feature_revision': feature['feature_revision'], 'target_selection_revision': original['selection_revision'],
            'required_result': 'released', 'gate': 'build'}], 'actor': 'Owner'}
    if with_origins:
        consumer['changes'].insert(0, {'change_id': 'C2', 'title': 'Shared report contribution',
            'owner': 'Writer', 'risk': 'routine', 'feature_ids': ['reports']})
        consumer['criteria'][0].update(owner_change_id='C2', contributor_change_ids=['C2', 'C3'],
                                      integration_check={'kind': 'verification', 'id': 'combined'})
        consumer['dependencies'][0].update(consumer_change_id='C2', required_result='specification_accepted', gate='plan')
    return act(root, consumer)


def fixture_increment(root):
    return act(root, {'kind': 'product_increment', 'product_revision': core.read(root)['product']['accepted_revision'],
        'feature_id': 'audit', 'increment_id': 'I3', 'change_id': 'C4', 'title': 'Independent audit increment',
        'owner': 'Writer', 'risk': 'routine', 'actor': 'Owner'})


def fixture_finish_origins(root):
    """Retain unrelated genuine origins after qualifying the released candidate."""
    fixture_consumer(root, with_origins=True)
    fixture_increment(root)


def fixture_seed(root, *, with_origins=False, shared=True, defer_origins=False, include_consumer=True):
    """Shared native/test fixture; real Product decisions, exact blocked edge, no model."""
    root = Path(root)
    assert not defer_origins or with_origins
    assert include_consumer or not with_origins
    root.mkdir(parents=True, exist_ok=True)
    assert not list(root.iterdir()), 'Disposable fixture destination must be empty'
    git(root, 'init', '--quiet')
    git(root, 'config', 'user.name', 'Offline progress fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / '.gitignore').write_text('.controlcoding/\n.controlwork/\n', encoding='utf-8')
    (root / 'accounts.py').write_text('value = 0\n', encoding='utf-8')
    (root / 'reports.py').write_text('value = 0\n', encoding='utf-8')
    git(root, 'add', 'accounts.py', 'reports.py', '.gitignore')
    git(root, 'commit', '--quiet', '-m', 'Offline original source')
    base = git(root, 'rev-parse', 'HEAD')
    combined_contracts(root)
    (root / 'blocked.py').write_text('value = 0\n', encoding='utf-8')
    (root / 'status.py').write_text(
        "import json,sys,subprocess\nhead=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()\n"
        "print(json.dumps({'candidateDigest':sys.argv[1],'deployedDigest':sys.argv[2],"
        "'environment':'local','status':'deployed','base':sys.argv[3],'head':head}))\n", encoding='utf-8')
    (root / 'monitor.py').write_text(
        "import json,sys\nprint(json.dumps({'candidateDigest':sys.argv[1],'deployedDigest':sys.argv[2],"
        "'window':'hour','metrics':{'latency':{'value':12,'samples':4,'citation':'offline-progress:latency'}}}))\n", encoding='utf-8')
    git(root, 'add', 'accounts.py', 'reports.py', 'blocked.py', 'check_combined.py', 'status.py', 'monitor.py',
        'controlcoding.verification.json', 'controlcoding.invariants.json')
    git(root, 'commit', '--quiet', '-m', 'Offline observed candidate sources')
    doc = two_features()
    doc['features'][0]['title'] = ('Create and retain a local account with every required signup condition '
                                   'and the shared contributor result visible')
    if with_origins:
        audit = json.loads(json.dumps(doc['features'][0]))
        audit.update(id='audit', title='Audit history', problem='Operators need audit history',
                     outcome='Operators can inspect retained changes',
                     criteria=[{'id': 'audit-success', 'text': 'Retained history remains readable', 'required': True}])
        doc['features'].append(audit)
        doc['chapters'][0]['feature_ids'].append('audit')
    if with_origins:
        (root / 'source.txt').write_text('Authorization remains mandatory after restart.\n', encoding='utf-8')
        source = documents.observe_source(root, 'source.txt')
        doc['sources'] = [source]
        doc['features'][0]['source_references'] = [source['source_id']]
        adopted = act(root, {'kind': 'product_adoption_save', 'document': doc, 'base_revision': 'absent',
                            'sources': [source], 'resolutions': [], 'actor': 'Author'})
        act(root, {'kind': 'product_decide', 'product_revision': adopted['product']['current_revision'],
                   'decision': 'accepted', 'actor': 'Owner', 'reason': 'Review exact adopted source scope.'})
    else:
        accept_document(root, doc)
    act(root, {'kind': 'activate', 'profile': 'routine'})
    selected = shared_selection(root)
    if with_origins or not shared:
        # Authentic upgrade needs genuine combined-history retention, while
        # its released candidate can remain one independently owned change.
        # A separately planned shared reports condition retains both owners.
        selected['changes'] = selected['changes'][:1]
        selected['criteria'][0]['contributor_change_ids'] = ['C1']
    if not shared and not with_origins:
        selected['criteria'][0]['integration_check'] = None
    act(root, selected)
    if include_consumer and (shared or with_origins) and not defer_origins:
        fixture_consumer(root, with_origins=with_origins)
    for cid, filename in [('C1', 'accounts.py'), ('C2', 'reports.py'), ('C3', 'blocked.py')]:
        if cid == 'C3' and not include_consumer:
            continue
        if (with_origins or not shared) and cid != 'C1':
            continue
        fixture_plan(root, cid, filename, branch=cid == 'C1')
    if with_origins and not defer_origins:
        fixture_increment(root)
    return {'root': str(root), 'base': base, 'feature_id': 'signup', 'criterion_id': 'signup-success',
            'increment_id': 'I1', 'change_id': 'C1', 'blocked_change_id': 'C3',
            'scope': 'Real disposable local Git/Product decisions; no provider or real deployment'}

def fixture_build(root, *, change_ids=('C1', 'C2')):
    root = Path(root)
    files = {'C1': 'accounts.py', 'C2': 'reports.py'}
    for cid in change_ids: build_change(root, cid, files[cid])
    return {'built': list(change_ids), 'candidate_ids': {cid: change(root, cid)['candidate']['digest'] for cid in change_ids}}


def fixture_verify(root, *, integration=True, after_build=None, change_ids=('C1', 'C2'), build=True, retain_evidence=False):
    root = Path(root)
    if build:
        fixture_build(root, change_ids=change_ids)
    if after_build is not None:
        after_build(root)
    base = git(root, 'rev-parse', 'HEAD^')
    specs = []
    for cid in change_ids:
        candidate = change(root, cid)['candidate']['digest']
        specs.append({'id': 'status-' + cid, 'kind': 'release_status',
            'argv': [sys.executable, '-B', 'status.py', '{candidateDigest}', '{artifactDigest}', base],
            'timeoutSeconds': 10, 'outputLimitBytes': 8192,
            'files': [{'path': 'status.py', 'sha256': hashlib.sha256((root / 'status.py').read_bytes()).hexdigest()}],
            'configuration': {'environment': 'local', 'artifactDigest': candidate}})
        if cid == 'C1':
            specs.append({'id': 'monitor', 'kind': 'monitor',
                'argv': [sys.executable, '-B', 'monitor.py', '{candidateDigest}', candidate],
                'timeoutSeconds': 10, 'outputLimitBytes': 8192,
                'files': [{'path': 'monitor.py', 'sha256': hashlib.sha256((root / 'monitor.py').read_bytes()).hexdigest()}],
                'configuration': {'environment': 'local', 'deployedDigest': candidate, 'window': 'hour',
                    'metrics': [{'id': 'latency', 'baseline': 5, 'maxDelta': 3, 'minimumSamples': 2}]}})
    (root / jobs.MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'adapters': specs}), encoding='utf-8')
    receipts(root)
    retained = []
    if retain_evidence:
        import cc
        for kind in ('verification', 'invariants'):
            prepared = cc._evidence_prepare(root, kind)
            assert not prepared['issues']
            observed = cc._evidence_history(root, kind, prepared)
            assert observed['assessment'] == {'state':'current','currentRequiredPass':True,'reasons':[]}
            path = Path(observed['latest']['path'])
            path = path if path.is_absolute() else root / path
            assert path.resolve().is_relative_to(root.resolve())
            raw = path.read_bytes()
            receipt = json.loads(raw)
            assert receipt['status'] == 'passed' and receipt['executionState'] == 'completed'
            retained.append({'id':receipt['id'], 'kind':kind, 'receiptId':receipt['id'],
                'path':path.relative_to(root).as_posix(), 'sha256':hashlib.sha256(raw).hexdigest()})
    for cid in change_ids:
        if retain_evidence:
            act(root, {'kind':'evidence', 'change_id':cid, 'records':retained, 'actor':'Verifier'})
        verify_change(root, cid)
    if integration:
        selection = next(row for row in core.read(root)['product']['evolution']['selections'] if row['id'] == 'I1')
        if next(row for row in selection['ownership'] if row['criterion_id'] == 'signup-success')['integration_check'] is not None:
            integrated = act(root, {'kind': 'product_integration', 'actor': 'Verifier', 'increment_id': 'I1',
                'selection_revision': selection['selection_revision'], 'criterion_id': 'signup-success',
                'candidates': [{'change_id': cid, 'candidate_identity': change(root, cid)['candidate']['digest']} for cid in change_ids]})
            reviews = integrated['product']['evolution']['integration_reviews']
            assert any(row['selection_revision'] == selection['selection_revision']
                       and row['criterion_id'] == 'signup-success'
                       and {c['change_id'] for c in row['candidates']} == set(change_ids) for row in reviews)
            for cid in change_ids:
                completed = next(row for row in integrated['changes'] if row['id'] == cid)
                assert not any('integration' in reason for reason in completed['gates']['review_release']['reasons'])
    return {'verified': list(change_ids), 'feature_id': 'signup', 'criterion_id': 'signup-success'}

def fixture_job(root, cid, kind, adapter_id):
    current = change(root, cid)
    value = {'kind': kind, 'adapter_id': adapter_id}
    preview = jobs.prepare(root, value, current)
    result = jobs.execute(root, value, current, preview['approval_id'])
    assert result['state'] == 'allowed', result
    return result

def fixture_release(root, *, link_child=False, change_ids=('C1', 'C2'), checkpoint=True):
    root = Path(root)
    for cid in change_ids:
        candidate = change(root, cid)['candidate']['digest']
        act(root, {'kind': 'review', 'change_id': cid, 'findings': [], 'actor': 'Reviewer', 'reason': 'Review exact current fixture behavior.'})
        stage(root, cid, 'review_release', {'candidate_identity': candidate, 'review_findings': [], 'finding_resolutions': [],
            'required_approvals': [], 'environment': 'local', 'release_artifact_identity': candidate,
            'rollback_readiness': 'Restore retained original source.', 'observation_provenance': 'self_reported'})
        decision(root, cid, 'review_release')
        observed = fixture_job(root, cid, 'release_status', 'status-' + cid)
        act(root, {'kind': 'release', 'change_id': cid, 'environment': 'local', 'artifact_digest': candidate,
            'status': 'observed', 'adapter_kind': 'release_status', 'observation': observed,
            'actor': 'ReleaseOwner', 'reason': 'Observe actual local Git-backed fixture command; no external deployment.'})
    monitored = fixture_job(root, 'C1', 'monitor', 'monitor')
    view = act(root, {'kind': 'incident_capture', 'change_id': 'C1', 'receipt_id': monitored['receiptId'],
        'signal_id': 'latency', 'actor': 'Operator', 'reason': 'Retain actual local command breach for exact navigation.'})
    incident_id = next(c for c in view['changes'] if c['id'] == 'C1')['incidents'][0]['id']
    if link_child:
        act(root, {'kind': 'incident_followup', 'change_id': 'C1', 'incident_id': incident_id, 'child_id': 'C5',
            'title': 'Inspect retained latency breach', 'owner': 'Operator', 'risk': 'routine',
            'actor': 'Operator', 'reason': 'Create an explicitly unapproved linked follow-up request.'})
        act(root, {'kind': 'product_link_incident', 'change_id': 'C5',
            'product_revision': core.read(root)['product']['accepted_revision'], 'feature_id': 'signup',
            'actor': 'Owner', 'reason': 'Link the actual incident child to exact Product scope.'})
    if checkpoint:
        prepared = history.checkpoint_prepare(root)
        history.checkpoint_commit(root, prepared['revision'], prepared['approval_id'], 'Owner')
    return {'released': list(change_ids), 'incident_id': incident_id, 'change_id': 'C1'}

def fixture_drift(root):
    (Path(root) / 'accounts.py').write_text('value = 3\n', encoding='utf-8')
    return {'changed': 'accounts.py', 'scope': 'Raw source drift without a control-state mutation'}


def assert_transport_rows(root, selectors, *, private_isolated=True):
    """Compare populated facts in explicitly selected actual subprocess contexts."""
    import os
    import cc_product_progress as progress
    source = Path(__file__).resolve().parents[1]
    before = inventory(root)
    observations = []
    stable = lambda value: {key: item for key, item in value.items()
                            if key not in ('observation', 'pagination')}
    for view, entity, collection, kind in selectors:
        query = {'view': view, 'entity_id': entity, 'collection': collection,
                 'cursor': None, 'observation_id': None}
        expected = progress.read(root, **query)
        assert expected['rows'], (view, entity, collection)
        assert all(row['kind'] == kind and row['summary'] is None for row in expected['rows'])
        args = ['--view', view, '--entity-id', entity, '--collection', collection]
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
        owner_expected = expected
        owner_contexts = None
        if not private_isolated:
            # Natural -c startup at the owner directory has the same discovery
            # closure as direct CLI/private scripts. Pytest's extra test paths
            # remain part of its separate, correctly distinct read transcript.
            program = """
import json,sys
from pathlib import Path
import cc,cc_evidence_inputs as inputs,cc_product_progress as progress
root=Path(sys.argv[1]);query=json.loads(sys.argv[2]);contexts={}
if sys.argv[3]=='contexts':
    for kind in ('verification','invariants'):
        prepared=cc._evidence_prepare(root,kind);assert not prepared['issues']
        contexts[kind]={'runner':inputs.runner_context(Path(cc.__file__).parent),'executionContext':inputs.execution_context(root,prepared['policy'],prepared['executable'])}
        observed=cc._evidence_history(root,kind,prepared)
        assert observed['assessment']=={'state':'current','currentRequiredPass':True,'reasons':[]}
print(json.dumps({'progress':progress.read(root,**query),'contexts':contexts}))
"""
            owner = subprocess.run([sys.executable, '-B', '-c', program, str(root), json.dumps(query),
                'contexts' if not observations else 'rows'], cwd=source / 'scripts', env=env,
                capture_output=True, text=True, encoding='utf-8', timeout=60)
            assert owner.returncode == 0, owner.stdout + owner.stderr
            captured = json.loads(owner.stdout)
            owner_expected = progress.validate_response(captured['progress'])
            assert stable(owner_expected) == stable(expected)
            if captured['contexts']:
                import cc
                import cc_evidence_inputs as inputs
                owner_contexts = captured['contexts']
                for evidence_kind in ('verification','invariants'):
                    prepared = cc._evidence_prepare(root, evidence_kind)
                    assert not prepared['issues']
                    assert owner_contexts[evidence_kind] == {
                        'runner':inputs.runner_context(source / 'scripts'),
                        'executionContext':inputs.execution_context(root, prepared['policy'], prepared['executable'])}
                    assert cc._evidence_history(root, evidence_kind, prepared)['assessment'] == {
                        'state':'current','currentRequiredPass':True,'reasons':[]}
        public = subprocess.run([sys.executable, '-B', str(source / 'scripts/cc.py'),
            'product', 'progress', '--project-root', str(root), '--json', *args],
            cwd=None if private_isolated else source / 'scripts', capture_output=True, text=True, encoding='utf-8', timeout=60, env=env)
        assert public.returncode == 0, public.stdout + public.stderr
        public_value = progress.validate_response(json.loads(public.stdout))
        request = {'version': 1, 'id': 'actual-leaf-parity', 'operation': 'product_progress_v1',
                   'project_root': str(root), 'query': query}
        private_argv = [sys.executable, *(['-I'] if private_isolated else []), '-B', str(source / 'scripts/cc_panel_bridge.py')]
        private = subprocess.run(private_argv,
            input=json.dumps(request), cwd=None if private_isolated else source / 'scripts', capture_output=True, text=True, encoding='utf-8', timeout=60, env=env)
        assert private.returncode == 0, private.stderr
        wire = json.loads(private.stdout)
        assert wire['status'] == 'ok', wire
        assert set(wire['result']) == {'project_root', 'progress'}
        private_value = progress.validate_response(wire['result']['progress'])
        assert stable(public_value) == stable(private_value) == stable(expected)
        assert public_value['observation']['input_digest'] == private_value['observation']['input_digest'] == owner_expected['observation']['input_digest']
        assert public_value['observation']['observation_id'] == private_value['observation']['observation_id'] == owner_expected['observation']['observation_id']
        observations.append({'view': view, 'entity_id': entity, 'collection': collection,
                             'kind': kind, 'rows': len(expected['rows']),
                             'private_isolated': private_isolated,
                             'ordinary_owner_contexts': owner_contexts})
    assert inventory(root) == before, 'Populated transport reads changed project bytes'
    return observations


def assert_isolated_proof_context(root, *, release_progress=None):
    """Require exact owner context rejection or positive parity in actual -I mode."""
    import os
    import cc
    import cc_evidence_inputs as inputs
    import cc_product_progress as progress
    source = Path(__file__).resolve().parents[1]
    runtime = source / 'scripts'
    before = inventory(root)
    ordinary = {}
    for kind in ('verification', 'invariants'):
        prepared = cc._evidence_prepare(root, kind)
        assert not prepared['issues']
        history = cc._evidence_history(root, kind, prepared)
        assert history['assessment']['state'] == 'current' and history['assessment']['currentRequiredPass']
        ordinary[kind] = {'runner': inputs.runner_context(runtime),
                          'executionContext': inputs.execution_context(root, prepared['policy'], prepared['executable'])}
    selectors = {'view': 'increment', 'entity_id': 'I1', 'collection': 'changes',
                 'cursor': None, 'observation_id': None}
    ordinary_progress = progress.read(root, **selectors)
    ordinary_releases = release_progress or progress.read(root, view='change', entity_id='C1', collection='releases')
    from cc_development_lib import storage
    with inputs.SafeRoot(root) as safe:
        ordinary_state, _ = storage.load(safe)
        safe.validate()
    ordinary_change = next(row for row in ordinary_state['changes'] if row['id'] == 'C1')
    assert ordinary_progress['rows'] and ordinary_progress['summary']['outcome'] == 'released'
    env = {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
    for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'PATH'):
        if os.environ.get(key): env[key] = os.environ[key]
    program = r"""
import hashlib,json,sys
from pathlib import Path
runtime=Path(sys.argv[1]).resolve();root=Path(sys.argv[2]).resolve();sys.path.insert(0,str(runtime))
import cc,cc_evidence as evidence,cc_evidence_inputs as inputs,cc_product_progress as progress
from cc_development_lib import storage
contexts={};assessments={};receipt_checks={}
for kind in ('verification','invariants'):
    prepared=cc._evidence_prepare(root,kind);assert not prepared['issues']
    contexts[kind]={'runner':inputs.runner_context(runtime),'executionContext':inputs.execution_context(root,prepared['policy'],prepared['executable'])}
    observed=cc._evidence_history(root,kind,prepared);assessments[kind]=observed['assessment']
    path=Path(observed['latest']['path']);path=path if path.is_absolute() else root/path
    assert path.resolve().is_relative_to(root)
    raw=path.read_bytes();receipt=json.loads(raw)
    assert receipt['status']=='passed' and receipt['executionState']=='completed'
    assert evidence.validate_receipt(receipt,kind,entries=prepared['executable'],contracts=prepared['contracts'])==[]
    assert receipt['inputs']['after']==inputs.capture_inputs(root,prepared['policy'])
    assert receipt['contracts']==prepared['contracts']
    assert receipt['plan']==evidence.make_plan(prepared['executable'],prepared['required'])
    receipt_checks[kind]={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'status':receipt['status'],'executionState':receipt['executionState'],'structure_inputs_contracts_plan_current':True}
value=progress.read(root,view='increment',entity_id='I1',collection='changes')
retained_releases=progress.read(root,view='change',entity_id='C1',collection='releases')
with inputs.SafeRoot(root) as safe:
    state,_=storage.load(safe);safe.validate()
change=next(row for row in state['changes'] if row['id']=='C1')
change_identity={key:change[key] for key in ('candidate','artifacts','decisions','method')}
from cc_development_lib.method import digest
state_identity=digest(state)
modules={}
for name,module in tuple(sys.modules.items()):
    filename=getattr(module,'__file__',None)
    if (name=='cc' or name.startswith('cc_')) and filename:
        path=Path(filename).resolve();assert path.is_relative_to(runtime),(name,path)
        modules[name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
assert sys.flags.isolated==1 and modules
print(json.dumps({'contexts':contexts,'assessments':assessments,'receipt_checks':receipt_checks,'change_identity':change_identity,'state_identity':state_identity,'retained_releases':retained_releases,'progress':value,'modules':modules,'python':sys.executable,'isolated':sys.flags.isolated}))
"""
    probe = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(runtime), str(root)],
        cwd=runtime, env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    isolated = json.loads(probe.stdout)
    for entry in isolated['modules'].values():
        path = Path(entry['path']).resolve()
        assert path.is_relative_to(runtime) and hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
    mismatched = []
    for kind in ('verification', 'invariants'):
        changed = isolated['contexts'][kind] != ordinary[kind]
        assessed = isolated['assessments'][kind]
        if changed:
            assert assessed['state'] == 'context_changed'
            assert assessed['reasons'] == ['runner_or_execution_context_changed']
            assert assessed['currentRequiredPass'] is False
            mismatched.append(kind)
        else:
            assert assessed == {'state': 'current', 'currentRequiredPass': True, 'reasons': []}
    expected = progress.validate_response(isolated['progress'])
    from cc_development_lib.method import digest
    assert isolated['state_identity'] == digest(ordinary_state)
    assert isolated['change_identity'] == {key:ordinary_change[key] for key in ('candidate','artifacts','decisions','method')}
    for row in expected['rows']:
        original = next(item for item in ordinary_progress['rows'] if item['id'] == row['id'])
        assert row['scope'] == original['scope']
        for key in ('candidate_identity','candidate_current','artifact_revisions','detail_revisions','profile_revision'):
            assert row['data'][key] == original['data'][key]
        assert row['data']['candidate_current'] is True
        assert row['data']['gates']['build']['accepted'] is True
        assert row['data']['gates']['build']['outcome'] == 'allowed'
        checks = row['data']['gates']['verify']['evidence']['checks']
        for kind in ('verification','invariants'):
            assert checks[kind]['assessment'] == isolated['assessments'][kind]
    immutable_release = lambda row: {**{key:item for key,item in row.items() if key != 'data'},
        'data':{key:item for key,item in row['data'].items() if key not in ('historical','assessment')}}
    assert [immutable_release(row) for row in isolated['retained_releases']['rows']] == [
        immutable_release(row) for row in ordinary_releases['rows']]
    if mismatched:
        assert expected['summary']['outcome'] == 'implemented'
        assert expected['summary']['coverage']['required']['implemented'] == ordinary_progress['summary']['coverage']['required']['total']
        assert expected['summary']['coverage']['required']['verified'] == expected['summary']['coverage']['required']['released'] == 0
        assert all(row['data']['historical'] for row in isolated['retained_releases']['rows'] if row['data']['status'] == 'observed')
    else:
        assert {k:v for k,v in expected.items() if k not in ('observation','pagination')} == {
            k:v for k,v in ordinary_progress.items() if k not in ('observation','pagination')}
    # Source CLI imports its trusted owner directory explicitly under -I.
    # Its actual __main__ parser/dispatch runs unchanged; ordinary CLI entry is
    # independently exercised by assert_transport_rows and public narratives.
    bootstrap = "import runpy,sys;from pathlib import Path;r=Path(sys.argv[1]).resolve();sys.path.insert(0,str(r));sys.argv=[str(r/'cc.py'),*sys.argv[2:]];runpy.run_path(str(r/'cc.py'),run_name='__main__')"
    public = subprocess.run([sys.executable, '-I', '-B', '-c', bootstrap, str(runtime),
        'product', 'progress', '--project-root', str(root), '--json', '--view', 'increment',
        '--entity-id', 'I1', '--collection', 'changes'], cwd=runtime, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert public.returncode == 0, public.stdout + public.stderr
    request = {'version':1,'id':'production-isolated-evidence','operation':'product_progress_v1',
               'project_root':str(root),'query':selectors}
    private = subprocess.run([sys.executable, '-I', '-B', str(runtime / 'cc_panel_bridge.py')],
        input=json.dumps(request), cwd=runtime, env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert private.returncode == 0, private.stderr
    wire = json.loads(private.stdout)
    assert wire['status'] == 'ok', wire
    public_value = progress.validate_response(json.loads(public.stdout))
    private_value = progress.validate_response(wire['result']['progress'])
    stable = lambda value:{key:item for key,item in value.items() if key not in ('observation','pagination')}
    assert stable(expected) == stable(public_value) == stable(private_value)
    assert expected['observation']['input_digest'] == public_value['observation']['input_digest'] == private_value['observation']['input_digest']
    assert expected['observation']['observation_id'] == public_value['observation']['observation_id'] == private_value['observation']['observation_id']
    assert inventory(root) == before, 'Actual isolation/context reads changed project bytes'
    return {'ordinary_contexts':ordinary,'isolated_contexts':isolated['contexts'],
            'isolated_assessments':isolated['assessments'],'receipt_checks':isolated['receipt_checks'],'mismatched_receipt_kinds':mismatched,
            'isolated_core_modules':isolated['modules'],'isolated_python':isolated['python'],
            'private_entrypoint_sha256':hashlib.sha256((runtime/'cc_panel_bridge.py').read_bytes()).hexdigest(),
            'cli_entrypoint_sha256':hashlib.sha256((runtime/'cc.py').read_bytes()).hexdigest(),
            'python_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            'production_isolated_parity':True}
