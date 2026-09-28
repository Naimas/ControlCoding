"""Migrate real archives and continue using source-bound human work records."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_layout_cli as layout
from cc_memory_lib import commands, sessions, knowledge_service as service
from cc_memory_lib.knowledge_graph import read as graph_read, source as graph_source
from cc_memory_lib.knowledge_store import database, get


def test_real_archives_notes_relations_schedule_and_sessions_survive_migration(tmp_path):
    project = tmp_path / 'adopter'; project.mkdir()
    (project / 'docs').mkdir()
    (project / 'docs/design.md').write_text('# Design\nAuthentication uses tokens.\n')
    (project / 'README.md').write_text('# Application\n[Design](docs/design.md)\n')
    app = project / 'app.py'; app.write_text('print("untouched")\n')
    assert commands.cmd_memory_init(project, json_output=True) == 0
    assert commands.cmd_memory_decision_add(project, 'Keep tokens', body='Token design is approved.', json_output=True) == 0
    assert sessions.cmd_memory_session_start(project, 'Implementation', session_id='session-kept', json_output=True) == 0
    assert commands.cmd_memory_work_init(project, project_name='Migration fixture', json_output=True) == 0
    assert commands.cmd_memory_work_capture(project, 'plans', 'Implementation plan', 'Build token validation.', json_output=True) == 0
    service.configure(project, {**service.DEFAULT, 'scopes': ['project', 'work', 'dev-memory'], 'embedding': ''})
    chat = dict(id='chat-kept', title='Design decision', retention='transcript', summary='Keep tokens.', status='active',
                turns=[dict(id='turn-kept', sequence=0, role='user', content='Keep the token decision.',
                            provenance={'origin': 'manual', 'model': 'fixture'})])
    service.conversation(project, chat)
    saved_chat = service.read_conversation(project, 'chat-kept')
    service.reconcile(project)
    before = {s['path']: s for s in service.catalog(project)['sources']}
    design, readme = before['docs/design.md'], before['README.md']
    service.notes(project, design['id'], 'Human note must survive.')
    state = service.dispatch(project, 'work-view', None)
    state = service.dispatch(project, 'work-propose', dict(snapshot=state['snapshot'], source=design['id'],
        target=readme['id'], source_role='activity', target_role='phase', kind='part_of', reason='Reviewed ownership'))
    relation = state['relations'][0]
    approved = service.dispatch(project, 'work-review', dict(snapshot=state['snapshot'], id=relation['id'],
        status='approved', reason='Approved against source evidence'))['relations'][0]
    schedule = service.dispatch(project, 'work-schedule-view', None)
    service.dispatch(project, 'work-schedule-save', dict(snapshot=schedule['snapshot'], start_date='2026-10-01',
        task=dict(id=design['id'], revision=design['revision'], duration_days=2, buffer_days=1,
                  earliest_start=None, deadline=None), reason='Reviewed estimate'))
    with database(project) as db:
        saved_schedule = get(db, 'work_schedule_v1')
    selected = [p for p in ('.controlcoding', '.controlwork', 'CONTROLWORK.md', 'PROJECT.md', 'dev', 'devlog', 'knowledge')
                if (project / p).exists()]
    plan = layout.migration_plan(project, selected, tmp_path / 'backup')[0]
    assert not plan['blockers'], plan['blockers']
    result = layout.migrate(project, selected, tmp_path / 'backup', plan['approval_id'])
    assert result['layout'] == 'contained'
    assert app.read_text() == 'print("untouched")\n'
    assert service.read_conversation(project, 'chat-kept') == saved_chat
    with database(project) as db:
        assert get(db, 'work_schedule_v1') == saved_schedule
    service.reconcile(project)
    after = {s['path']: s for s in service.catalog(project)['sources']}
    for path, record in before.items():
        actual = 'cc/' + path if path in ('CONTROLWORK.md', 'PROJECT.md') else path
        assert after[actual]['id'] == record['id'], path
    assert service.page(project, design['id'])['notes'] == 'Human note must survive.'
    relation_after = service.dispatch(project, 'work-view', None)['relations'][0]
    assert relation_after['history'] == approved['history'] and relation_after['effective']
    assert sessions.cmd_memory_session_show(project, 'session-kept', json_output=True) == 0
    assert commands.cmd_memory_work_capture(project, 'notes', 'After migration', 'Continue here.', json_output=True) == 0
    service.reconcile(project)
    assert service.query(project, 'token', False)['citations']
    assert not any((project / p).exists() for p in selected)
    assert (project / 'cc/.controlcoding/knowledge/knowledge.db').exists()


def test_contained_graph_window_and_lookup_expose_real_document_paths(tmp_path):
    layout.initialize(tmp_path)
    assert commands.cmd_memory_work_init(tmp_path, project_name='Graph fixture', json_output=True) == 0
    service.configure(tmp_path, {**service.DEFAULT, 'scopes': ['project', 'work'], 'embedding': ''})
    service.reconcile(tmp_path)
    graph = graph_read(tmp_path, dict(topic=None, query='', focus=None, offset=0, snapshot=None))
    context = next(s for s in graph['sources'] if s['path'] == 'cc/CONTROLWORK.md')
    assert context['physicalPath'] == 'cc/CONTROLWORK.md'
    assert graph_source(tmp_path, context['id'])['physicalPath'] == 'cc/CONTROLWORK.md'


def test_migrated_git_and_host_adapters_point_at_contained_artifacts(tmp_path):
    import cc
    project = tmp_path / 'project'; project.mkdir()
    subprocess.run(['git', 'init', str(project)], check=True, capture_output=True)
    assert cc.cmd_init(project, quiet=True) == 0
    cc._write_host_integration_assets(project, 'codex_cli')
    context = project / 'CONTROLCODING.md'
    text, spec = cc._render_expected_host_context(project, 'codex_cli', context, context.read_text(), 'CONTROLCODING.md')
    (project / spec['path']).write_text(text, encoding='utf-8')
    selected = [p for p in ('.controlcoding', 'CONTROLCODING.md', 'STATUS.md', 'ROADMAP.md', 'BUGS.md',
                            'devlog', 'hooks', 'tools/fitness_check.py', 'tools/cc_layout.py',
                            'tools/control_plane_utils.py') if (project / p).exists()]
    plan = layout.migration_plan(project, selected, tmp_path / 'backup')[0]
    assert not plan['blockers'], plan['blockers']
    layout.migrate(project, selected, tmp_path / 'backup', plan['approval_id'])
    assert 'cc/CONTROLCODING.md' in (project / 'AGENTS.md').read_text()
    hook = (project / '.git/hooks/pre-commit').read_text().replace('\\', '/')
    assert '/cc/hooks/' in hook and '/cc/tools/' in hook
    manifest = json.loads((project / 'cc/.controlcoding/launchers/manifest.json').read_text())
    assert manifest['primaryEntryPoint']['shell'].startswith('cc/.controlcoding/')
    assert all((project / path).exists() for path in manifest['generatedFiles'])
    assert (tmp_path / 'backup/original/AGENTS.md').read_text() == text
