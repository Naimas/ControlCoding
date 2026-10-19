"""PW07 recovery controls in every layout/document-owner combination."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


SOURCE = Path(__file__).resolve().parents[1]


def exercise(runtime, root, contained, ownership):
    sys.path.insert(0, str(runtime))
    import cc_development_lib as core
    import cc_product_documents as documents
    from cc_evidence_inputs import SafeRoot

    # Load only the existing literal document factory, with no private bridge or
    # pytest discovery in the selected production interpreter.
    fixture = SOURCE / 'tests/test_cc_product_documents.py'
    tree = ast.parse(fixture.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'document')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(fixture), 'exec'), namespace)
    doc = namespace['document']()
    root.mkdir()
    outside = root.parent / 'outside-owner.txt'
    outside.write_bytes(b'Outside ownership sentinel\n')
    unmanaged = root / 'original-brief.md'
    unmanaged.write_bytes(b'# Original source\nKeep every row.\n')
    if contained:
        (root / 'cc').mkdir()
        (root / 'cc/layout.json').write_text('{"schema":1,"layout":"contained"}\n')
    preserved = {p: p.read_bytes() for p in (outside, unmanaged)}
    checks = []

    def check(name, result):
        assert result, name
        assert all(p.read_bytes() == raw for p, raw in preserved.items()), name + ' changed original bytes'
        checks.append(name)

    def save(value):
        before = core.read(root)
        action = {'kind': 'product_save', 'document': value,
                  'base_revision': before['product']['current_revision'], 'actor': 'Owner'}
        preview = core.prepare(root, action, before['revision'])
        assert not preview['blockers'], preview
        return core.commit(root, action, before['revision'], preview['approval_id'])['view']['product']

    product = save(doc)
    path = 'docs/owner-product.md' if ownership == 'project_managed' else ''
    preview = documents.export_preview(root, doc, product['current_revision'], ownership=ownership, path=path)
    options = {k: preview[k] for k in ('ownership', 'path', 'expected_hash', 'preview_id')}
    target = root / preview['path']
    import cc_development_gateway as gateway
    original_output = gateway._write_bytes

    def output_full(*args, **kwargs):
        raise OSError('PW07 injected output disk full')

    gateway._write_bytes = output_full
    try:
        partial = documents.export_commit(root, doc, product['current_revision'], **options)
    finally:
        gateway._write_bytes = original_output
    check('failed output reports recoverable partial export without source mutation',
          partial['state'] == 'needs_attention' and partial['reason'] == 'export_write_failed' and
          not target.exists() and core.read(root)['product']['document'] == doc)
    # Interrupt the receipt publication after actual output, then recover using
    # the original operation identity in this exact layout/ownership pair.
    original_write = SafeRoot.write_atomic
    failed = False

    def disk_full(safe, relative, value, **kwargs):
        nonlocal failed
        if 'development_exports/' in relative and value.get('state') == 'exported' and not failed:
            failed = True
            raise OSError('PW07 injected receipt disk full')
        return original_write(safe, relative, value, **kwargs)

    SafeRoot.write_atomic = disk_full
    try:
        try:
            documents.export_commit(root, doc, product['current_revision'], **options)
        except OSError as error:
            assert 'PW07 injected' in str(error)
        else:
            raise AssertionError('Receipt publication fault was not reached')
    finally:
        SafeRoot.write_atomic = original_write
    check('interrupted export retains canonical draft and actual output',
          failed and target.is_file() and core.read(root)['product']['document'] == doc)
    retry = documents.export_commit(root, doc, product['current_revision'], **options)
    check('reviewed retry recovers the original export', retry['state'] == 'exported' and retry['idempotent'])
    text = target.read_text(encoding='utf-8')
    before = core.read(root)

    def read_edited(content, current=product, **kwargs):
        target.write_text(content, encoding='utf-8')
        result = documents.roundtrip_preview(root, preview['path'], current['document'], current['current_revision'], **kwargs)
        assert core.read(root) == before, 'Roundtrip preview mutated canonical state'
        return result

    missing = read_edited(text.replace('<!-- cc-product:feature:F1:begin -->', '<!-- broken -->'))
    check('missing markers require explicit repair', not missing['resolved'])
    duplicate = read_edited(text + '\n<!-- cc-product:criterion:C1:begin -->\n- Duplicate\n<!-- cc-product:criterion:C1:end -->\n')
    check('duplicate markers cannot resolve as trusted content', not duplicate['resolved'] and
          any(c['kind'] == 'duplicate_marker' for c in duplicate['conflicts']))
    first = '<!-- cc-product:criterion:C1:begin -->\n- Owner can create an account\n<!-- cc-product:criterion:C1:end -->'
    second = '<!-- cc-product:criterion:C2:begin -->\n- Owner can list accounts\n<!-- cc-product:criterion:C2:end -->'
    assert first in text and second in text
    reordered = text.replace(first, '__pw07_first__').replace(second, first).replace('__pw07_first__', second)
    edited = read_edited(reordered.replace('### Accounts', '### Renamed accounts') + '\nOwner note retained verbatim\n')
    check('rename reorder and owner notes retain stable identity and text', edited['resolved'] and
          edited['document']['features'][0]['id'] == 'F1' and
          edited['document']['features'][0]['title'] == 'Renamed accounts' and
          [c['id'] for c in edited['document']['features'][0]['criteria']] == ['C2', 'C1'] and
          'Owner note retained verbatim' in edited['document']['notes'])
    removed = read_edited(text.replace(second, ''))
    check('criterion deletion cannot silently remove an obligation', removed['removals'] == ['criterion:C2'] and not removed['resolved'])
    reviewed = read_edited(text.replace(second, ''), resolutions={'remove:criterion:C2': 'adopt_file'})
    check('explicit removal review changes only the proposal', reviewed['resolved'] and
          [c['id'] for c in reviewed['document']['features'][0]['criteria']] == ['C1'])
    revised = copy.deepcopy(doc)
    revised['features'][0]['title'] = 'Core owner title'
    current = save(revised)
    before = core.read(root)
    conflict_text = text.replace('### Accounts', '### File owner title')
    conflicting = read_edited(conflict_text, current)
    check('simultaneous file and Core edits retain a conflict', not conflicting['resolved'] and
          {'id': 'competing:feature:F1', 'kind': 'competing_edit'} in conflicting['conflicts'])
    resolved = read_edited(conflict_text, current, resolutions={'competing:feature:F1': 'keep_core'})
    check('reviewed Core choice does not rewrite owner file', resolved['document']['features'][0]['title'] == 'Core owner title' and
          target.read_text(encoding='utf-8') == conflict_text)
    foreign = target.read_bytes()
    try:
        documents.export_commit(root, doc, product['current_revision'], **options)
    except core.DevelopmentError:
        pass
    else:
        raise AssertionError('Stale export replaced competing content')
    check('stale export retry preserves competing bytes', target.read_bytes() == foreign)
    fresh = documents.export_preview(root, current['document'], current['current_revision'], ownership=ownership, path=preview['path'])
    try:
        documents.export_commit(root, current['document'], current['current_revision'],
                                **{k: fresh[k] for k in options})
    except core.DevelopmentError as error:
        assert 'replacement_review_required' in str(error)
    else:
        raise AssertionError('Unreviewed replacement was accepted')
    check('existing output needs an explicit replacement decision', target.read_bytes() == foreign)
    modules = {n: str(Path(m.__file__).resolve()) for n, m in sys.modules.items()
               if (n == 'cc' or n.startswith('cc_')) and getattr(m, '__file__', None)}
    assert all(Path(p).is_relative_to(runtime) for p in modules.values()), modules
    return {'checks': checks, 'contained': contained, 'ownership': ownership, 'modules': modules,
            'factory_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()}


if __name__ == '__main__':
    runtime = Path(sys.argv[1]).resolve()
    print(json.dumps(exercise(runtime, Path(sys.argv[2]), sys.argv[3] == 'true', sys.argv[4])))
else:
    import pytest

    @pytest.mark.parametrize('contained', [False, True])
    @pytest.mark.parametrize('ownership', ['managed', 'project_managed'])
    def test_pw07_all_layout_owner_recovery(tmp_path, contained, ownership):
        runtime = Path(os.environ.get('CC_PW07_RUNTIME', SOURCE / 'scripts')).resolve()
        if (runtime / 'scripts/cc.py').is_file():
            runtime /= 'scripts'
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()), str(runtime),
                                 str(tmp_path / 'project'), str(contained).lower(), ownership],
                                cwd=runtime, capture_output=True, text=True, encoding='utf-8', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report['checks'] and report['contained'] == contained and report['ownership'] == ownership
        print(json.dumps(report, sort_keys=True))
