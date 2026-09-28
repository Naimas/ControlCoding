"""Static analysis on external disposable projects, with installed parser fixtures."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import tracemalloc

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_project_map_analysis as analysis
import cc_project_map_analyzers as analyzers
import cc_project_map_definition as definition
import cc_project_map_sources as source
import cc_project_map_controls as controls


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
    return path


def args(root):
    request = {'project_root': str(root), 'project_id': 'analysis-fixture', 'observed_at': '2026-09-21T12:00:00Z', 'design_paths': []}
    preview = source.preview_project_map_scope(request)['preview_id']
    review = definition.observe_reviewed(request, preview)['review']
    return request, preview, review['source_snapshot'], review['revision']


def observe(root, runtime=None, with_controls=False):
    bound = args(root)
    control = controls.preview_controls(*bound)['scope_id'] if with_controls else None
    preview = analysis.preview_analysis(*bound, control, runtime=runtime)
    return analysis.observe_analysis(*bound, control, preview['scope_id'], runtime=runtime)


def rules(result):
    return [f['rule'] for f in result['analysis']['findings']]


@pytest.fixture
def runtime():
    node, worker = os.environ.get('CC_MAP_TEST_NODE'), os.environ.get('CC_MAP_TEST_WORKER')
    if not node or not worker:
        pytest.skip('Set trusted CC_MAP_TEST_NODE and CC_MAP_TEST_WORKER to the external built parser')
    assert Path(node).is_absolute() and Path(worker).is_file()
    return node, worker


def test_empty_no_write_and_no_false_health(tmp_path):
    result = observe(tmp_path)
    assert result['analysis']['coverage'] == 'partial'
    assert not result['analysis']['files'] and not result['analysis']['findings']
    assert result['projection']['summary']['verified_units'] == 0
    assert not list(tmp_path.iterdir())


def test_python_metrics_duplicates_cycles_intent_rules_and_canaries(tmp_path):
    body = '\n'.join(['    a = value', '    b = a + 1', '    c = b + 1', '    d = c + 1', '    e = d + 1', '    f = e + 1', '    return f'])
    write(tmp_path, 'a.py', 'import b\nSECRET="CANARY"\ndef first(value):\n' + body + '\n')
    write(tmp_path, 'b.py', 'import a\ndef second(value):\n' + body + '\n')
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'expectedFiles': ['a.py', 'future/service.py'],
          'forbiddenDependencies': [{'id': 'no-a-to-b', 'from': 'a.py', 'to': 'b.py'}]})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = observe(tmp_path)
    assert set(rules(result)) == {'normalized_duplicate_function', 'static_dependency_cycle', 'forbidden_dependency:no-a-to-b'}
    assert len([d for d in result['analysis']['dependencies'] if d['target']]) == 2
    assert {c['state'] for c in result['analysis']['comparison']} == {'observed', 'not_observed'}
    assert any(n['presence'] == 'intended' for n in result['projection']['bundle']['nodes'])
    assert result['projection']['summary']['verified_units'] == 0
    assert 'CANARY' not in json.dumps(result)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    for finding in result['analysis']['findings']:
        assert finding['analyzer'] and finding['lifecycle'] == 'open'
        assert all(l['identity'].startswith('sha256:') and l['line'] > 0 for l in finding['locations'])


def test_thresholds_and_nested_function_branch_scope(tmp_path):
    code = 'def large(value):\n' + ''.join(f'    if value == {i}: return {i}\n' for i in range(15))
    code += '    def inner(x):\n        if x: return 1\n    return inner(value)\n'
    code += '# padding\n' * 505
    write(tmp_path, 'large.py', code)
    parsed = analyzers.python_ast(code.encode(), lambda: None)
    assert [s['branches'] for s in parsed['symbols']] == [16, 2]
    result = observe(tmp_path)
    assert set(rules(result)) == {'large_file', 'branch_complexity'}
    assert all(f['classification'] == 'review' for f in result['analysis']['findings'])
    finding = next(f for f in result['analysis']['findings'] if f['rule'] == 'branch_complexity')
    assert len(finding['subjects']) == 2
    assert any(n['kind'] == 'symbol' and n['id'] in finding['subjects'] for n in result['projection']['bundle']['nodes'])


def test_long_function_threshold_positive_and_exact_boundary_negative(tmp_path):
    write(tmp_path, 'a.py', 'def run():\n' + '    pass\n' * 80)
    assert 'long_function' in rules(observe(tmp_path))
    write(tmp_path, 'a.py', 'def run():\n' + '    pass\n' * 79)
    assert 'long_function' not in rules(observe(tmp_path))


@pytest.mark.parametrize('variant', ['literal', 'identifier', 'small'])
def test_duplicate_negatives_preserve_literals_identifiers_and_minimum(tmp_path, variant):
    body = '\n'.join(['    a = 1', '    b = a + 1', '    c = b + 1', '    d = c + 1', '    e = d + 1', '    f = e + 1', '    return f'])
    other = body.replace('a = 1', 'a = 2') if variant == 'literal' else body.replace('f', 'other')
    if variant == 'small': body = other = '    return 1'
    write(tmp_path, 'a.py', 'def one():\n' + body + '\ndef two():\n' + other + '\n')
    assert 'normalized_duplicate_function' not in rules(observe(tmp_path))


@pytest.mark.parametrize('case', ['dynamic', 'syntax', 'unsupported', 'alias', 'ambiguous'])
def test_unknowns_are_not_reported_as_broken_dependencies(tmp_path, case):
    write(tmp_path, 'a.py', 'import missing\n')
    if case == 'dynamic': write(tmp_path, 'a.py', '__import__("CANARY")\n')
    if case == 'syntax': write(tmp_path, 'a.py', 'def bad( CANARY')
    if case == 'unsupported': write(tmp_path, 'a.rs', 'fn main() {}')
    if case == 'alias': write(tmp_path, 'a.py', 'import hidden_alias\n')
    if case == 'ambiguous':
        write(tmp_path, 'a.py', 'import dep\n')
        write(tmp_path, 'dep.py', 'pass\n'); write(tmp_path, 'dep/__init__.py', 'pass\n')
    result = observe(tmp_path)
    assert not rules(result) and not any(d['target'] for d in result['analysis']['dependencies'])
    assert result['analysis']['coverage'] == 'partial'
    assert 'CANARY' not in json.dumps(result)
    if case == 'ambiguous': assert result['analysis']['dependencies'][0]['resolution'] == 'ambiguous'


def test_python_relative_imports_and_explicit_src_root(tmp_path):
    write(tmp_path, 'src/pkg/sub/a.py', 'from . import b, c\nfrom .. import outside\nfrom .... import unsupported\n')
    write(tmp_path, 'src/pkg/sub/b.py', 'pass\n'); write(tmp_path, 'src/pkg/sub/c.py', 'pass\n')
    write(tmp_path, 'src/pkg/outside.py', 'pass\n')
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'pythonRoots': ['src']})
    deps = observe(tmp_path)['analysis']['dependencies']
    assert sorted(d['candidates'][0] for d in deps if d['target']) == ['src/pkg/outside.py', 'src/pkg/sub/b.py', 'src/pkg/sub/c.py']
    assert sum(d['resolution'] == 'unresolved' for d in deps) == 1


@pytest.mark.parametrize('extension', ['js', 'ts', 'jsx', 'tsx', 'mjs', 'cjs'])
def test_real_javascript_typescript_symbols_and_static_cycle(tmp_path, runtime, extension):
    annotation = ': number' if extension in ('ts', 'tsx') else ''
    jsx = 'return <div/>;' if extension in ('jsx', 'tsx') else 'return value;'
    write(tmp_path, 'a.' + extension, f'import "./b.{extension}";\nexport function run(value{annotation}) {{ if(value) {{ {jsx} }} }}\n')
    write(tmp_path, 'b.' + extension, f'import "./a.{extension}";\nexport class Store {{ read() {{ return 1; }} }}')
    result = observe(tmp_path, runtime)
    assert 'static_dependency_cycle' in rules(result)
    assert all(f['state'] == 'parsed' for f in result['analysis']['files'])
    titles = [n['title'] for n in result['projection']['bundle']['nodes'] if n['kind'] == 'symbol']
    assert 'run' in titles and 'Store' in titles and 'Store.read' in titles
    assert result['projection']['summary']['verified_units'] == 0


def test_javascript_comments_strings_dynamic_imports_and_aliases(tmp_path, runtime):
    write(tmp_path, 'a.ts', '// import "./fake";\nconst s = "import CANARY";\nimport("./dynamic");\nimport thing from "@alias/missing";\nrequire("./other");\n')
    result = observe(tmp_path, runtime)
    assert len(result['analysis']['dependencies']) == 3
    assert all(d['target'] is None for d in result['analysis']['dependencies'])
    assert not rules(result) and 'CANARY' not in json.dumps(result)


def test_javascript_duplicates_and_ambiguous_extension_candidates(tmp_path, runtime):
    body = '\n'.join([' const a=1;', ' const b=a+1;', ' const c=b+1;', ' const d=c+1;', ' const e=d+1;', ' return e;'])
    write(tmp_path, 'main.ts', 'import "./dep";\nfunction a(){\n' + body + '\n}\nfunction b(){\n' + body + '\n}\n')
    write(tmp_path, 'dep.ts', 'export const a=1;'); write(tmp_path, 'dep.js', 'export const a=1;')
    result = observe(tmp_path, runtime)
    assert 'normalized_duplicate_function' in rules(result)
    assert result['analysis']['dependencies'][0]['resolution'] == 'ambiguous'


def test_missing_parser_and_bad_syntax_are_explicit(tmp_path, runtime):
    write(tmp_path, 'a.ts', 'function bad( CANARY')
    assert observe(tmp_path)['analysis']['files'][0]['state'] == 'parser_unavailable'
    result = observe(tmp_path, runtime)
    assert result['analysis']['files'][0]['state'] == 'parse_unavailable' and 'CANARY' not in json.dumps(result)


def test_report_links_do_not_promote_currentness_or_leak_bodies(tmp_path):
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'reports': [{'kind': 'junit', 'path': 'reports/tests.xml'}, {'kind': 'cobertura', 'path': 'coverage/coverage.xml'}]})
    write(tmp_path, 'reports/tests.xml', '<testsuites><testsuite tests="4" failures="1"><testcase name="CANARY"><failure>CANARY</failure></testcase></testsuite></testsuites>')
    write(tmp_path, 'coverage/coverage.xml', '<coverage lines-valid="10" lines-covered="8"/>')
    result = observe(tmp_path)
    assert [r['state'] for r in result['analysis']['reports']] == ['linked', 'linked']
    assert all(r['currentness'] == 'unassessed' for r in result['analysis']['reports'])
    assert result['analysis']['reports'][0]['counts']['failures'] == 1
    assert result['projection']['summary']['verified_units'] == 0 and 'CANARY' not in json.dumps(result)


@pytest.mark.parametrize('xml', ['<!DOCTYPE a [<!ENTITY x "CANARY">]><testsuite tests="1"/>', '<CANARY', '<testsuite tests="-1"/>', '<html/>'], ids=['entities', 'malformed', 'negative', 'unsupported'])
def test_unsafe_or_invalid_reports_are_unavailable(tmp_path, xml):
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'reports': [{'kind': 'junit', 'path': 'reports/a.xml'}]})
    write(tmp_path, 'reports/a.xml', xml)
    result = observe(tmp_path)
    assert result['analysis']['reports'][0]['state'] == 'unavailable'
    assert 'CANARY' not in json.dumps(result)


@pytest.mark.parametrize('value', [ {'schemaVersion': 2}, {'schemaVersion': 1, 'expectedFiles': ['../CANARY']},
    {'schemaVersion': 1, 'expectedFiles': ['.env']}, {'schemaVersion': 1, 'reports': [{'kind': 'junit', 'path': 'secrets/private.xml'}]},
    {'schemaVersion': 1, 'command': 'CANARY'}, {'schemaVersion': 1, 'pythonRoots': []}], ids=['version', 'escape', 'excluded', 'sensitive', 'command', 'roots'])
def test_invalid_configuration_is_rejected_without_echo(tmp_path, value):
    write(tmp_path, analysis.CONFIG, value)
    with pytest.raises(analysis.AnalysisError, match='invalid_analysis_configuration'):
        observe(tmp_path)


def test_source_scope_and_definition_binding(tmp_path):
    write(tmp_path, 'a.py', 'pass\n')
    bound = args(tmp_path); scope = analysis.preview_analysis(*bound)
    with pytest.raises(analysis.AnalysisError, match='preview_mismatch'):
        analysis.observe_analysis(*bound, None, '0' * 64)
    write(tmp_path, 'a.py', 'pass # changed\n')
    with pytest.raises(analysis.AnalysisError, match='stale_snapshot'):
        analysis.observe_analysis(*bound, None, scope['scope_id'])


def test_config_change_after_preview_is_rejected(tmp_path):
    bound = args(tmp_path); scope = analysis.preview_analysis(*bound)
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'expectedFiles': ['future.py']})
    with pytest.raises(analysis.AnalysisError, match='preview_mismatch'):
        analysis.observe_analysis(*bound, None, scope['scope_id'])


def test_retained_source_change_and_new_file_are_rejected(tmp_path, monkeypatch):
    write(tmp_path, 'a.py', 'pass\n')
    original = analysis.Projection.reports
    def change(self):
        original(self); write(tmp_path, 'new.py', 'pass\n')
    monkeypatch.setattr(analysis.Projection, 'reports', change)
    with pytest.raises(analysis.AnalysisError, match='changed_input'):
        observe(tmp_path)


@pytest.mark.parametrize('kind', ['hardlink', 'oversize'])
def test_named_report_safety_and_budget(tmp_path, kind):
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'reports': [{'kind': 'junit', 'path': 'reports/a.xml'}]})
    path = write(tmp_path, 'reports/a.xml', '<testsuite tests="1"/>')
    if kind == 'hardlink': os.link(path, path.with_name('alias.xml'))
    else: path.write_bytes(b' ' * (1048576 + 1))
    with pytest.raises(analysis.AnalysisError): observe(tmp_path)


def test_file_count_limit_and_owned_cooperative_timeout(tmp_path, monkeypatch):
    for i in range(129): write(tmp_path, f'f{i}.py', 'pass\n')
    with pytest.raises(analysis.AnalysisError, match='scope_limit'): observe(tmp_path)


def test_controls_composition_preserves_independent_dimensions(tmp_path):
    write(tmp_path, 'a.py', 'pass\n' * 501)
    write(tmp_path, '.controlcoding/cc_config.json', {'protected_zones': {'deny': ['a.py']}})
    result = observe(tmp_path, with_controls=True)
    assert result['controls']['host_coverage'] == 'unknown' and 'large_file' in rules(result)
    assert any(c['decision'] == 'deny' for c in result['projection']['bundle']['constraints'])
    assert result['projection']['summary']['verified_units'] == 0


def test_nested_lambda_has_its_own_branch_metric():
    result = analyzers.python_ast(b'def outer():\n    return lambda x: 1 if x else 0\n', lambda: None)
    assert [s['branches'] for s in result['symbols']] == [1, 2]


def test_runtime_identity_change_invalidates_preview(tmp_path, runtime):
    worker = tmp_path.parent / 'owned-worker.cjs'
    worker.write_bytes(Path(runtime[1]).read_bytes())
    local_runtime = runtime[0], str(worker)
    bound = args(tmp_path); preview = analysis.preview_analysis(*bound, runtime=local_runtime)
    worker.write_bytes(worker.read_bytes() + b'\n// fixture parser identity change\n')
    with pytest.raises(analysis.AnalysisError, match='preview_mismatch'):
        analysis.observe_analysis(*bound, None, preview['scope_id'], runtime=local_runtime)


def test_report_store_override_does_not_open_hidden_or_private_subtrees(tmp_path):
    for path in ('reports/.private/x.xml', 'coverage/secrets/x.xml', '.controlcoding/verification_receipts/private/x.xml'):
        write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'reports': [{'kind': 'junit', 'path': path}]})
        with pytest.raises(analysis.AnalysisError, match='invalid_analysis_configuration'): observe(tmp_path)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows junction fixture')
def test_report_parent_junction_is_rejected(tmp_path):
    external = tmp_path.parent / 'report-target'
    external.mkdir(); write(external, 'a.xml', '<testsuite tests="1"/>')
    link = tmp_path / 'reports'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external)], capture_output=True)
    assert result.returncode == 0
    write(tmp_path, analysis.CONFIG, {'schemaVersion': 1, 'reports': [{'kind': 'junit', 'path': 'reports/a.xml'}]})
    with pytest.raises(analysis.AnalysisError): observe(tmp_path)
    assert (external / 'a.xml').read_text() == '<testsuite tests="1"/>'


def test_cooperative_timeout_clears_entire_analysis(tmp_path, monkeypatch):
    write(tmp_path, 'a.py', 'pass\n')
    original = analysis.Projection.reports
    def expire(self):
        original(self)
        # Change only the analyzer's elapsed check, not the source owner's clock.
        monkeypatch.setattr(analysis, 'time', type('Clock', (), {'monotonic': staticmethod(lambda: 10**20)}))
    monkeypatch.setattr(analysis.Projection, 'reports', expire)
    with pytest.raises(analysis.AnalysisError, match='time_limit'): observe(tmp_path)


def test_named_bounded_python_fixture_metrics(tmp_path, record_property):
    for i in range(24): write(tmp_path, f'module_{i}.py', f'def run(value):\n    return value + {i}\n')
    tracemalloc.start(); started = time.monotonic()
    result = observe(tmp_path)
    elapsed = time.monotonic() - started; _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    record_property('fixture', '24-small-python-files')
    record_property('elapsed_seconds', round(elapsed, 4)); record_property('python_peak_bytes', peak)
    record_property('reply_bytes', len(analyzers.json_bytes(result))); record_property('nodes', len(result['projection']['bundle']['nodes']))
    assert len(result['analysis']['files']) == 24 and len(analyzers.json_bytes(result)) < 768 * 1024
