"""Actual bounded reads, retained-base roundtrip and confined export effects."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_product_documents as documents
from cc_development_lib import read, prepare, commit, DevelopmentError
from cc_panel_bridge import dispatch


def document():
    return {'id': 'P1', 'title': 'Example', 'language': 'en', 'goals': ['Useful app'],
            'users': ['Owner'], 'constraints': ['Offline'], 'non_goals': ['Payments'], 'questions': [],
            'chapters': [{'id': 'CH1', 'title': 'Main', 'content': 'Overview', 'feature_ids': ['F1']}],
            'features': [{'id': 'F1', 'title': 'Accounts', 'problem': 'First paragraph.\n\nSecond paragraph.',
                          'outcome': 'Useful accounts', 'users': ['Owner'], 'priority': 'normal',
                          'criteria': [{'id': 'C1', 'text': 'Owner can create an account', 'required': True},
                                       {'id': 'C2', 'text': 'Owner can list accounts', 'required': True}],
                          'dependencies': [], 'risks': ['Personal data'], 'questions': [], 'source_references': []}],
            'sources': [], 'notes': 'Keep this note'}


def save(root, doc):
    snap = read(root)
    action = {'kind': 'product_save', 'document': doc, 'base_revision': snap['product']['current_revision'], 'actor': 'Owner'}
    preview = prepare(root, action, snap['revision'])
    assert not preview['blockers'], preview['blockers']
    return commit(root, action, snap['revision'], preview['approval_id'])['view']['product']


def test_source_actual_inventory_selection_and_drift(tmp_path):
    original = b'# Intro\nScope\n# Accounts\nCreate safely\n# Reports\nFuture\n'
    source = tmp_path / 'brief.md'; source.write_bytes(original)
    observed = documents.observe_source(tmp_path, 'brief.md')
    assert observed['sha256'] == hashlib.sha256(original).hexdigest()
    assert len(observed['sections']) == 3
    observed['sections'][1].update(selected=True, disposition='mapped', targets=['F1'])
    documents.validate_source(tmp_path, observed, reviewed=True)
    assert documents.source_coverage(observed) == {'total': 3, 'selected': 1, 'reviewed': 1, 'selected_complete': True, 'whole_complete': False}
    assert source.read_bytes() == original
    source.write_text('Changed', encoding='utf-8')
    with pytest.raises(DevelopmentError, match='source_drift'): documents.validate_source(tmp_path, observed, reviewed=True)


def test_source_unresolved_inventory_fabrication_and_unsupported(tmp_path):
    observed = documents.observe_source(tmp_path, text='User provided text')
    observed['sections'][0]['selected'] = True
    with pytest.raises(DevelopmentError, match='selected_source_unresolved'): documents.validate_source(tmp_path, observed, reviewed=True)
    observed['sections'][0]['text'] = 'Invented'
    with pytest.raises(DevelopmentError, match='invalid_source_inventory'): documents.validate_source(tmp_path, observed)
    with pytest.raises(DevelopmentError, match='unsupported_source_format'): documents.observe_source(tmp_path, 'brief.pdf')
    with pytest.raises(DevelopmentError): documents.observe_source(tmp_path, '../outside.md')


@pytest.mark.parametrize('contained', [False, True])
@pytest.mark.parametrize('ownership', ['managed', 'project_managed'])
def test_four_layout_export_conflict_retry_and_trusted_roundtrip(tmp_path, contained, ownership):
    if contained:
        (tmp_path / 'cc').mkdir(); (tmp_path / 'cc/layout.json').write_text('{"schema":1,"layout":"contained"}\n')
    product = save(tmp_path, document())
    path = 'docs/owner-product.md' if ownership == 'project_managed' else ''
    preview = documents.export_preview(tmp_path, product['document'], product['current_revision'], ownership=ownership, path=path)
    assert preview['path'] == ('docs/owner-product.md' if path else ('cc/' if contained else '') + 'dev/product/product.md')
    options = {k: preview[k] for k in ('ownership', 'path', 'expected_hash', 'preview_id')}
    receipt = documents.export_commit(tmp_path, product['document'], product['current_revision'], **options)
    assert receipt['state'] == 'exported'
    assert documents.export_commit(tmp_path, product['document'], product['current_revision'], **options)['idempotent']
    target = tmp_path / preview['path']; text = target.read_text(encoding='utf-8')
    target.write_text(text.replace('### Accounts', '### Renamed') + '\nUnrecognized owner note\n', encoding='utf-8')
    proposal = documents.roundtrip_preview(tmp_path, preview['path'], product['document'], product['current_revision'])
    assert proposal['document']['features'][0]['title'] == 'Renamed'
    assert proposal['document']['features'][0]['problem'] == document()['features'][0]['problem']
    assert 'Unrecognized owner note' in proposal['document']['notes']
    assert proposal['resolved']
    before = target.read_bytes()
    with pytest.raises(DevelopmentError, match='export_preview_stale'):
        documents.export_commit(tmp_path, product['document'], product['current_revision'], **options)
    assert target.read_bytes() == before
    lines = target.read_text().splitlines(); meta = json.loads(lines[0][len('<!-- cc-product-export '):-len(' -->')]); meta['base']['title'] = 'Forged'
    lines[0] = '<!-- cc-product-export ' + json.dumps(meta) + ' -->'; target.write_text('\n'.join(lines))
    with pytest.raises(DevelopmentError): documents.roundtrip_preview(tmp_path, preview['path'], product['document'], product['current_revision'])


def test_roundtrip_competing_edits_removal_marker_loss_and_resolution(tmp_path):
    original = save(tmp_path, document())
    text = documents.render_document(original['document'], original['current_revision'])
    target = tmp_path / 'document.md'; target.write_text(text.replace('### Accounts', '### File title'))
    revised = copy.deepcopy(original['document']); revised['features'][0]['title'] = 'Core title'
    current = save(tmp_path, revised)
    proposal = documents.roundtrip_preview(tmp_path, 'document.md', current['document'], current['current_revision'])
    assert {'id': 'competing:feature:F1', 'kind': 'competing_edit'} in proposal['conflicts']
    resolved = documents.roundtrip_preview(tmp_path, 'document.md', current['document'], current['current_revision'], resolutions={'competing:feature:F1': 'keep_core'})
    assert resolved['document']['features'][0]['title'] == 'Core title'
    target.write_text(text.replace('<!-- cc-product:feature:F1:begin -->', '<!-- broken -->'))
    assert not documents.roundtrip_preview(tmp_path, 'document.md', current['document'], current['current_revision'])['resolved']


def test_bridge_sources_reads_only_no_registry_or_memory(tmp_path):
    response = dispatch({'version': 1, 'id': 'source-read', 'operation': 'product_source_v1', 'project_root': str(tmp_path), 'path': '', 'text': '# Brief\nOwner scope'})
    assert response['status'] == 'ok'
    assert list(tmp_path.iterdir()) == []
    request = {'version': 1, 'id': 'bad', 'operation': 'product_source_v1', 'project_root': str(tmp_path), 'path': '', 'text': 'Brief', 'approval': True}
    assert dispatch(request)['status'] == 'error'


def test_export_receipt_failure_reviewed_retry_preserves_competing_bytes(tmp_path, monkeypatch):
    product = save(tmp_path, document())
    preview = documents.export_preview(tmp_path, product['document'], product['current_revision'])
    options = {k: preview[k] for k in ('ownership', 'path', 'expected_hash', 'preview_id')}
    from cc_evidence_inputs import SafeRoot
    original = SafeRoot.write_atomic
    failed = False
    def fail_after_file(self, relative, value, **kwargs):
        nonlocal failed
        if 'development_exports/' in relative and value.get('state') == 'exported' and not failed:
            failed = True
            raise OSError('injected receipt disk full')
        return original(self, relative, value, **kwargs)
    monkeypatch.setattr(SafeRoot, 'write_atomic', fail_after_file)
    with pytest.raises(OSError): documents.export_commit(tmp_path, product['document'], product['current_revision'], **options)
    assert (tmp_path / preview['path']).is_file()
    assert read(tmp_path)['product']['document'] == product['document']
    assert documents.export_commit(tmp_path, product['document'], product['current_revision'], **options)['idempotent']
    (tmp_path / preview['path']).write_text('Competing owner edit')
    with pytest.raises(DevelopmentError, match='export_preview_stale'): documents.export_commit(tmp_path, product['document'], product['current_revision'], **options)
    assert (tmp_path / preview['path']).read_text() == 'Competing owner edit'


def test_roundtrip_reorder_and_reviewed_criterion_removal(tmp_path):
    product = save(tmp_path, document())
    text = documents.render_document(product['document'], product['current_revision'])
    first = '<!-- cc-product:criterion:C1:begin -->\n- Owner can create an account\n<!-- cc-product:criterion:C1:end -->'
    second = '<!-- cc-product:criterion:C2:begin -->\n- Owner can list accounts\n<!-- cc-product:criterion:C2:end -->'
    target = tmp_path / 'document.md'; target.write_text(text.replace(first, '__first__').replace(second, first).replace('__first__', second))
    reordered = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'])
    assert [c['id'] for c in reordered['document']['features'][0]['criteria']] == ['C2', 'C1']
    target.write_text(text.replace(second, ''))
    removed = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'])
    assert removed['removals'] == ['criterion:C2'] and not removed['resolved']
    resolved = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'], resolutions={'remove:criterion:C2': 'adopt_file'})
    assert [c['id'] for c in resolved['document']['features'][0]['criteria']] == ['C1']
    assert read(tmp_path)['product']['document'] == product['document']


def test_roundtrip_unknown_block_retains_text(tmp_path):
    product = save(tmp_path, document())
    text = documents.render_document(product['document'], product['current_revision'])
    text += '\n<!-- cc-product:feature:UNKNOWN:begin -->\nNever lose these notes\n<!-- cc-product:feature:UNKNOWN:end -->\n'
    (tmp_path / 'document.md').write_text(text)
    result = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'])
    assert not result['resolved'] and 'Never lose these notes' in result['document']['notes']


@pytest.mark.parametrize('contained', [False, True])
@pytest.mark.parametrize('ownership', ['managed', 'project_managed'])
def test_change_stage_exports_layout_owner_native_cli_and_ipc(tmp_path, contained, ownership):
    from test_cc_product_core import handed,detailed_action, specification
    from test_cc_development_core import decide
    if contained:
        (tmp_path / 'cc').mkdir(); (tmp_path / 'cc/layout.json').write_text('{"schema":1,"layout":"contained"}\n')
    handed(tmp_path)
    path = 'docs/request.md' if ownership == 'project_managed' else ''
    preview = documents.change_export_preview(tmp_path, 'C1', 'intent', ownership=ownership, path=path)
    assert preview['path'] == ('docs/request.md' if path else ('cc/' if contained else '') + 'dev/changes/C1/intent.md')
    assert preview['status'] == 'draft' and 'Members need an account' in preview['content']
    options = {k: preview[k] for k in ('ownership', 'path', 'expected_hash', 'preview_id')}
    assert documents.change_export_commit(tmp_path, 'C1', 'intent', **options)['state'] == 'exported'
    decide(tmp_path, 'intent')
    # A new Core decision invalidates the earlier preview; historical export
    # cannot claim the new current acceptance without a new review.
    with pytest.raises(DevelopmentError, match='export_preview_stale'):
        documents.change_export_commit(tmp_path, 'C1', 'intent', **options)
    request = {'version': 1, 'id': 'stage-export', 'operation': 'product_change_export_preview_v1',
               'project_root': str(tmp_path), 'change_id': 'C1', 'stage': 'intent', 'path': path, 'ownership': ownership}
    response = dispatch(request)
    assert response['status'] == 'ok'
    assert response['result']['change_export_preview']['status'] == 'accepted_current'
    import subprocess
    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts/cc.py'),
                             'product', 'change-export-preview', '--project-root', str(tmp_path), '--change-id', 'C1',
                             '--stage', 'intent', '--ownership', ownership, '--path', path, '--json'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'accepted_current'


def test_product_export_cannot_label_draft_accepted(tmp_path):
    product = save(tmp_path, document())
    preview = documents.export_preview(tmp_path, product['document'], product['current_revision'], status='accepted')
    options = {k: preview[k] for k in ('ownership', 'path', 'expected_hash', 'preview_id')}
    with pytest.raises(DevelopmentError, match='export_status_stale'):
        documents.export_commit(tmp_path, product['document'], product['current_revision'], status='accepted', **options)
    assert not (tmp_path / preview['path']).exists()


def test_unpublished_stage_draft_has_readable_export_without_old_approval(tmp_path):
    from test_cc_product_core import handed, detailed_action
    from test_cc_development_core import decide, act
    import cc_product
    handed(tmp_path); decide(tmp_path, 'intent')
    draft = cc_product.template(tmp_path, 'C1', 'design')
    draft['content']['requirements'][0]['text'] = ''
    act(tmp_path, detailed_action(tmp_path, {'kind': 'product_specification', 'change_id': 'C1', 'content': draft['content'], 'mappings': [], 'actor': 'Owner'}))
    preview = documents.change_export_preview(tmp_path, 'C1', 'design')
    assert preview['status'] == 'draft' and 'Specification' in preview['content']
    assert preview['path'] == 'dev/changes/C1/spec.md'


def test_roundtrip_current_snapshot_must_be_actual_loaded_revision(tmp_path):
    old = save(tmp_path, document())
    (tmp_path / 'document.md').write_text(documents.render_document(old['document'], old['current_revision']))
    changed = document(); changed['title'] = 'New canonical title'; save(tmp_path, changed)
    with pytest.raises(DevelopmentError):
        documents.roundtrip_preview(tmp_path, 'document.md', old['document'], old['current_revision'])


def test_roundtrip_promotion_token_rejects_file_drift_and_manual_reinterpretation(tmp_path):
    product = save(tmp_path, document())
    target = tmp_path / 'document.md'; target.write_text(documents.render_document(product['document'], product['current_revision']).replace('### Accounts', '### File rename'))
    result = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'])
    snapshot = read(tmp_path)
    action = {'kind': 'product_save', 'document': result['document'], 'base_revision': result['current_revision'],
              'source_review': result['source_review'], 'actor': 'Owner'}
    preview = prepare(tmp_path, action, snapshot['revision'])
    assert not preview['blockers'], preview['blockers']
    target.write_text(target.read_text() + '\nCompeting file note\n')
    with pytest.raises(DevelopmentError): commit(tmp_path, action, snapshot['revision'], preview['approval_id'])
    assert read(tmp_path)['product']['document'] == product['document']
    result = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'])
    action.update(document=result['document'], source_review=result['source_review'])
    action['document']['title'] = 'Silently reinterpreted manual draft'
    with pytest.raises(DevelopmentError): prepare(tmp_path, action, snapshot['revision'])


def test_roundtrip_real_markdown_html_comments_cannot_terminate_metadata_header(tmp_path):
    doc = document()
    doc['sources'] = [documents.observe_source(tmp_path, text='# Brief\n<!-- Project owner note -->\nPreserve every row')]
    product = save(tmp_path, doc)
    (tmp_path / 'document.md').write_text(documents.render_document(product['document'], product['current_revision']))
    result = documents.roundtrip_preview(tmp_path, 'document.md', product['document'], product['current_revision'])
    assert result['resolved'] and result['document'] == doc


def test_dependencies_roundtrip_preserves_json_types_and_rejects_malformed_rows(tmp_path):
    doc = document()
    values = ['{"looks":"structured"}', {'feature_id': 'Reports', 'unknown': [True, 7, None]}, False, 4, None]
    doc['features'][0]['dependencies'] = values
    product = save(tmp_path, doc)
    text = documents.render_document(doc, product['current_revision'])
    target = tmp_path / 'document.md'; target.write_text(text)
    result = documents.roundtrip_preview(tmp_path, 'document.md', doc, product['current_revision'])
    assert result['document']['features'][0]['dependencies'] == values
    target.write_text(text.replace('- JSON: 4', '- JSON: 5'))
    result = documents.roundtrip_preview(tmp_path, 'document.md', doc, product['current_revision'])
    assert result['document']['features'][0]['dependencies'] == values[:3] + [5, None]
    target.write_text(text.replace('- JSON: 4', '- JSON: {broken'))
    with pytest.raises(DevelopmentError):
        documents.roundtrip_preview(tmp_path, 'document.md', doc, product['current_revision'])
