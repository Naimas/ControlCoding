"""Canonical desktop workflows use only disposable external projects."""
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_panel_jobs as jobs
from cc_panel_configuration import ConfigurationError


def install(memory='deferred'):
    return {'kind': 'install', 'answers': {'name': 'Example panel project', 'host': 'codex_cli',
        'documentation': 'managed', 'memory': memory, 'manual': False, 'guidance': 'recommended',
        'notes': '', 'stable': [], 'shared': [], 'features': [], 'rules': ''}, 'suites': []}


def run(root, value, answers):
    plan = jobs.prepare(str(root), value)
    assert not plan['blockers']
    return jobs.execute(str(root), value, plan['approval_id'], str(answers))


def test_preview_and_stale_confirmation(tmp_path):
    root=tmp_path/'project';root.mkdir()
    value=install();p=jobs.prepare(str(root),value)
    assert p['handoff']['setup']['hooks_location']=='local' and not list(root.iterdir())
    (root/'AGENTS.md').write_text('Foreign context',encoding='utf-8')
    with pytest.raises(ConfigurationError,match='preview_mismatch'):
        jobs.execute(str(root),value,p['approval_id'],str(tmp_path/'answers.json'))
    assert not (tmp_path/'answers.json').exists()


def test_real_install_engagement_memory_and_project(tmp_path):
    root=tmp_path/'project';root.mkdir()
    assert run(root,install(),tmp_path/'install.json')==0
    assert (root/'CONTROLCODING.md').is_file() and (root/'.controlcoding/cc_config.json').is_file()
    assert run(root,{'kind':'engagement','answers':{'manual':True},'suites':[]},tmp_path/'engagement.json')==0
    engagement=json.loads((root/'.controlcoding/cc_engagement.json').read_text(encoding='utf-8'))
    assert engagement['manual_consultation_allowed']
    assert run(root,{'kind':'memory','answers':{},'suites':[]},tmp_path/'unused.json')==0
    receipt=json.loads((root/'.controlcoding/memory_bootstrap_receipt.json').read_text(encoding='utf-8'))
    assert receipt['scanScope']=='governed' and receipt['status']=='completed' and not receipt['fullRepoScan']
    value={'kind':'project','suites':[],'answers':{'mode':'guided','summary':'A reviewed project purpose','users':'Maintainers',
        'scope':'Inspect local sources','out_of_scope':'Remote deployment','acceptance':'Never silently discard user data',
        'references':'','stack':'Python','architecture':'Core and presentation'}}
    assert run(root,value,tmp_path/'project.json')==0
    docs='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in root.rglob('*.md'))
    assert 'A reviewed project purpose' in docs and 'Never silently discard user data' in docs


@pytest.mark.parametrize('kind',['doctor','verify_status','memory','invariants'])
def test_fixed_operations_reject_extra_arguments(tmp_path,kind):
    with pytest.raises(ConfigurationError):jobs.prepare(str(tmp_path),{'kind':kind,'answers':{'command':'echo dangerous'},'suites':[]})


def test_verify_preview_exposes_selected_commands(tmp_path):
    (tmp_path/'controlcoding.verification.json').write_text(json.dumps({'schemaVersion':1,'suites':[
        {'id':'selected','command':'python -c "print(1)"','required':True},
        {'id':'optional','command':'python -c "print(2)"','required':False}]}),encoding='utf-8')
    v={'kind':'verify_run','answers':{},'suites':[]}
    assert [s['id'] for s in jobs.prepare(str(tmp_path),v)['suites']]==['selected']
    with pytest.raises(ConfigurationError,match='invalid_suite'):jobs.prepare(str(tmp_path),{**v,'suites':['missing']})


def test_conversation_is_read_by_core(tmp_path):
    import cc_controlwork_manage as manage
    from cc_controlwork_observer import observe
    def save(value,identity):
        args=(str(tmp_path),value,[],identity,'2026-09-22T20:00:00Z');p=manage.prepare(*args)[0];return manage.save(*args,p['approval_id'])
    save({'action':'init','name':'Conversations','purpose':'Reviewed knowledge'},'a'*32)
    result=save({'action':'conversation','title':'UI exchange','provider':'test/model','messages':[
        {'role':'user','content':'Question'}, {'role':'assistant','content':'<script>not executable</script>'}]},'b'*32)
    scope=observe(str(tmp_path),preview=True)['work_scope'];work=observe(str(tmp_path),scope_id=scope['scope_id'])['work']
    assert result['saved'] and len(work['sessions'])==1
    assert work['sessions'][0]['notes'][1]['text']=='<script>not executable</script>'


@pytest.mark.parametrize('program,expected', [('print(42)', 0), ('raise SystemExit(7)', 1)])
def test_real_verification_preserves_exit_and_receipts(tmp_path,program,expected):
    root=tmp_path/'project';root.mkdir()
    command=f'"{Path(sys.executable).as_posix()}" -B -c "{program}"'
    (root/'controlcoding.verification.json').write_text(json.dumps({'schemaVersion':1,
        'requiredKinds':['targeted'],'suites':[{'id':'fixture','kind':'targeted','required':True,'command':command}]}),encoding='utf-8')
    value={'kind':'verify_run','answers':{},'suites':[]}
    assert run(root,value,tmp_path/'unused.json')==expected
    assert list((root/'.controlcoding/verification_receipts').glob('*.json'))


def test_setup_analysis_only_includes_selected_design_excerpts(tmp_path):
    from cc_panel_configuration import default_draft, analysis_packet
    (tmp_path/'design.md').write_text('# Reviewed design\nEvidence for a proposal.',encoding='utf-8')
    (tmp_path/'.env').write_text('PRIVATE_CANARY',encoding='utf-8')
    draft=default_draft(tmp_path);draft['design_paths']=['design.md']
    first=analysis_packet(str(tmp_path),draft)
    assert first['design_excerpts'][0]['text'].startswith('# Reviewed design')
    assert 'PRIVATE_CANARY' not in json.dumps(first)
    (tmp_path/'design.md').write_text('Changed design',encoding='utf-8')
    assert analysis_packet(str(tmp_path),draft)['request_id']!=first['request_id']
