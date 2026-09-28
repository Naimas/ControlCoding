"""Bounded read-only quality projection; see docs/project-map-analysis.md."""
from cc_layout import managed_path, managed_relative
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import cc_project_map_analyzers as analyzers
import cc_project_map_model as model
from cc_project_map_controls import observe_controls, preview_controls
from cc_project_map_definition import observe_reviewed
from cc_project_map_sources import _relative, _excluded, _json, _CODE, preview_project_map_scope, MapSourceError
from cc_setup_service import _root, _Snapshots, ReadPolicy, SetupServiceError

ADAPTER = 'cc-project-map-analysis/v1'
CONFIG = 'controlcoding.architecture.json'
LIMITS = {'file_bytes': 1048576, 'total_bytes': 8388608, 'retained_entries': 256,
          'files': 128, 'ast_nodes': 20000, 'findings': 256, 'dependencies': 1024,
          'expected_files': 128, 'rules': 128, 'reports': 8, 'helper_seconds': 15}
THRESHOLDS = {'file_lines': 500, 'function_lines': 80, 'function_branches': 12, 'duplicate_min_lines': 8}


class AnalysisError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code='invalid_analysis_configuration'):
    if not condition:
        raise AnalysisError(code)


def key(value):
    return analyzers.digest(analyzers.json_bytes(value))


def config_path(value):
    _relative(value)
    require(not _excluded(value))
    return value


def configuration(raw):
    defaults = {'pythonRoots': [''], 'expectedFiles': [], 'forbiddenDependencies': [], 'reports': []}
    if raw is None:
        return defaults
    try:
        value = _json(raw.decode('utf-8'))
        require(type(value) is dict and set(value) <= {'schemaVersion', *defaults} and type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1)
        result = {**defaults, **{k: v for k, v in value.items() if k != 'schemaVersion'}}
        for field, limit in [('pythonRoots', 8), ('expectedFiles', 128), ('forbiddenDependencies', 128), ('reports', 8)]:
            require(type(result[field]) is list and len(result[field]) <= limit)
        require(result['pythonRoots'])
        for path in result['pythonRoots']:
            if path != '': config_path(path)
        for path in result['expectedFiles']: config_path(path)
        for rule in result['forbiddenDependencies']:
            require(type(rule) is dict and set(rule) == {'id', 'from', 'to'} and type(rule['id']) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', rule['id']))
            config_path(rule['from']); config_path(rule['to'])
        require(len({r['id'] for r in result['forbiddenDependencies']}) == len(result['forbiddenDependencies']))
        for report in result['reports']:
            require(type(report) is dict and set(report) == {'kind', 'path'} and report['kind'] in ('junit', 'cobertura'))
            _relative(report['path'])
            require(report['path'].endswith('.xml'))
            # Only named report stores may override source inventory exclusions.
            stores = ('reports/', 'coverage/', '.controlcoding/verification_receipts/', '.controlcoding/invariant_receipts/')
            prefix = next((p for p in stores if report['path'].startswith(p)), '')
            require(not _excluded(report['path'][len(prefix):]))
        for field in ('pythonRoots', 'expectedFiles'):
            require(len(set(result[field])) == len(result[field]))
        require(len({r['path'] for r in result['reports']}) == len(result['reports']))
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, MapSourceError):
        raise AnalysisError('invalid_analysis_configuration') from None


def runtime_identity(runtime):
    if runtime is None:
        return {'state': 'unavailable', 'parser': 'babel-7.29.7', 'identity': None}
    require(type(runtime) is tuple and len(runtime) == 2 and all(type(p) is str and Path(p).is_absolute() for p in runtime), 'parser_unavailable')
    node, worker = map(Path, runtime)
    require(node.is_file() and worker.is_file() and worker.stat().st_size <= 2 * 1024 * 1024, 'parser_unavailable')
    return {'state': 'available', 'parser': 'babel-7.29.7', 'identity': key([runtime, analyzers.digest(worker.read_bytes())])}


def preview_analysis(request, preview_id, snapshot, revision, controls_scope_id=None, *, runtime=None):
    require(preview_project_map_scope(request)['preview_id'] == preview_id, 'preview_mismatch')
    require(type(snapshot) is str and re.fullmatch(r'snapshot:[0-9a-f]{64}', snapshot), 'invalid_request')
    require(revision == 'absent' or type(revision) is str and re.fullmatch(r'[0-9a-f]{64}', revision), 'invalid_request')
    if controls_scope_id is not None:
        require(preview_controls(request, preview_id, snapshot, revision)['scope_id'] == controls_scope_id, 'preview_mismatch')
    root = _root(request['project_root'])
    try:
        with _Snapshots(root, {}, ReadPolicy()) as reader:
            item = reader.observe(managed_path(root, CONFIG), {})
            raw = item[-1] if item else None
            config = configuration(raw)
            reader.recheck()
        result = {'schema_version': 1, 'adapter': ADAPTER, 'config': managed_relative(root, CONFIG),
                  'config_identity': analyzers.digest(raw) if raw is not None else None,
                  'configuration': config, 'parser': runtime_identity(runtime), 'limits': LIMITS,
                  'python_parser': 'python-' + '.'.join(map(str, sys.version_info[:3])),
                  'thresholds': THRESHOLDS, 'snapshot': snapshot, 'revision': revision,
                  'includes_controls': controls_scope_id is not None,
                  'coverage': 'Static supported syntax only; no runtime resolution, whole-project health or acceptance claim'}
        result['scope_id'] = key([result, request, preview_id, controls_scope_id])
        return result
    except SetupServiceError as error:
        raise AnalysisError(error.code) from None


def javascript(files, runtime):
    if not files:
        return {}
    if runtime is None:
        return {f['path']: {'state': 'parser_unavailable', 'symbols': [], 'imports': []} for f in files}
    payload = analyzers.json_bytes({'files': files})
    require(len(payload) <= 12 * 1024 * 1024, 'scope_limit')
    env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
    env['ELECTRON_RUN_AS_NODE'] = '1'
    try:
        result = subprocess.run([runtime[0], '--max-old-space-size=192', runtime[1]],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6,
            cwd=str(Path(runtime[1]).parent), env=env, shell=False,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        require(result.returncode == 0 and not result.stderr and len(result.stdout) <= 1048576, 'parser_unavailable')
        output = _json(result.stdout.decode('utf-8'))
        require(type(output) is dict and 'error' not in output and output.get('schema_version') == 1 and output.get('parser') == 'babel-7.29.7', 'parser_unavailable')
        rows = output['files']
        require(type(rows) is list and len(rows) == len(files) and [r['path'] for r in rows] == [f['path'] for f in files], 'parser_unavailable')
        return {r['path']: r for r in rows}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, UnicodeError):
        raise AnalysisError('parser_unavailable') from None


class Projection:
    def __init__(self, observation, scope, root, reader, tick):
        self.map, self.scope, self.root, self.reader, self.tick = copy.deepcopy(observation), scope, root, reader, tick
        self.bundle = self.map['projection']['bundle']
        self.sources = {s['id']: s for s in self.bundle['sources']}
        self.files = {}
        for n in self.bundle['nodes']:
            if n['kind'] == 'file' and n['presence'] in ('observed', 'both'):
                for source in n['sources']:
                    s = self.sources[source]
                    if s['owner'] in ('code', 'design'):
                        self.files[s['locator']['path']] = (n, s)
        self.summary = {'schema_version': 1, 'adapter': ADAPTER, 'files': [], 'findings': [],
                        'dependencies': [], 'comparison': [], 'reports': [],
                        'analyzers': ['size/v1', analyzers.PYTHON, analyzers.JAVASCRIPT, 'duplicates/v1', 'imports/v1', 'architecture/v1', 'report-links/v1'],
                        'coverage': 'partial', 'limits': LIMITS, 'thresholds': THRESHOLDS,
                        'engines': {'python': scope['python_parser'], 'javascript': scope['parser']},
                        'notices': ['Static candidates are not runtime resolution; absence of findings does not establish health',
                                    'Findings do not change completion, current gate evidence or permissions']}

    def source(self, path, data, owner='design'):
        sid = 'analysis-source:' + key([path, owner])[:40]
        if sid not in self.sources:
            row = {'id': sid, 'owner': owner, 'locator': {'path': path, 'fragment': None},
                   'identity': 'sha256:' + analyzers.digest(data) if data is not None else None,
                   'adapter': 'cc-project-map-analysis-v1', 'resolution': 'resolved' if data is not None else 'unresolved',
                   'reason': 'Retained analysis declaration/report; no lifecycle or current test verdict inferred', 'synthetic': False}
            self.bundle['sources'].append(row); self.sources[sid] = row
        return sid

    def finding(self, rule, analyzer, subjects, locations, summary, *, classification='review', severity='warning', value=None, threshold=None, sources=None):
        require(len(self.summary['findings']) < 256, 'scope_limit')
        self.summary['findings'].append({'id': 'finding:' + key([rule, subjects, locations])[:40],
            'rule': rule, 'analyzer': analyzer, 'classification': classification, 'severity': severity,
            'lifecycle': 'open', 'subjects': subjects, 'sources': sources or sorted({l['source'] for l in locations}),
            'locations': locations, 'summary': summary, 'value': value, 'threshold': threshold,
            'limits': 'Current bounded static snapshot; not a verdict of runtime failure, acceptance or architectural intent beyond the named rule'})

    def location(self, path, line=1, end=None, symbol=None):
        n, s = self.files[path]
        return {'node': n['id'], 'symbol': symbol, 'path': path, 'source': s['id'], 'identity': s['identity'], 'line': line, 'end_line': end or line}

    def measure(self, runtime):
        parsed, jsfiles, duplicate_groups = {}, [], {}
        selected = [(p, n, s) for p, (n, s) in sorted(self.files.items()) if Path(p).suffix.lower() in _CODE and s['identity']]
        require(len(selected) <= 128, 'scope_limit')
        for path, node, source in selected:
            self.tick()
            item = self.reader.observe(self.root / path, {})
            require(item is not None and 'sha256:' + analyzers.digest(item[-1]) == source['identity'], 'changed_input')
            data = item[-1]
            row = {'path': path, 'node': node['id'], 'source': source['id'], 'identity': source['identity'],
                   'lines': len(data.splitlines()), 'bytes': len(data), 'state': 'unsupported_language', 'functions': 0}
            self.summary['files'].append(row)
            if row['lines'] > THRESHOLDS['file_lines']:
                self.finding('large_file', 'size/v1', [node['id']], [self.location(path, 1, row['lines'])],
                    'File size exceeds the review threshold; this does not establish a god file', value=row['lines'], threshold=THRESHOLDS['file_lines'])
            if path.lower().endswith(('.py', '.pyi')):
                parsed[path] = analyzers.python_ast(data, self.tick)
            elif Path(path).suffix.lower() in analyzers.CODE:
                try: jsfiles.append({'path': path, 'text': data.decode('utf-8')})
                except UnicodeError: row['state'] = 'encoding_unavailable'
        parsed.update(javascript(jsfiles, runtime))
        for row in self.summary['files']:
            path = row['path']
            if path not in parsed:
                continue
            result = parsed[path]
            row['state'] = result['state']
            language = analyzers.PYTHON if path.lower().endswith(('.py', '.pyi')) else analyzers.JAVASCRIPT
            if result['state'] != 'parsed':
                continue
            row['functions'] = sum(s['kind'] == 'function' for s in result['symbols'])
            parents = {}
            for symbol in result['symbols']:
                self.tick()
                symbol_id = None
                if language == analyzers.JAVASCRIPT:
                    sid = 'analysis-symbol:' + key([self.map['root_identity'], path, symbol['key']])[:40]
                    symbol_id = sid
                    self.bundle['nodes'].append({'id': sid, 'kind': 'symbol', 'title': symbol['title'], 'presence': 'observed',
                        'origin': 'code', 'mapping': 'proposed', 'sources': [row['source']], 'lifecycle': {'owner': 'adapter', 'state': 'unknown'},
                        'plan': 'unknown', 'criteria': [], 'reason': 'Static JavaScript/TypeScript syntax candidate; analysis-only symbol, file mapping is reviewed separately'})
                    self.bundle['edges'].append({'id': 'analysis-symbol-edge:' + key(sid)[:40], 'source': parents.get(symbol['parent'], row['node']),
                        'target': sid, 'relation': 'contains_structure', 'origin': 'adapter', 'mapping': 'proposed', 'sources': [row['source']]})
                    parents[symbol['key']] = sid
                elif symbol['kind'] == 'function' and not re.search(r'(?:^|\.)lambda:[0-9]+$', symbol['key']):
                    candidates = [s['node'] for o in self.map['observations'] if o['source'] == row['source']
                                  for s in o['details'].get('symbols', []) if s['line'] == symbol['line'] and s['kind'] == 'function']
                    if len(candidates) == 1:
                        symbol_id = candidates[0]
                if symbol['kind'] != 'function': continue
                location = self.location(path, symbol['line'], symbol['end_line'], symbol_id)
                for rule, value, threshold in [('long_function', symbol['end_line'] - symbol['line'] + 1, THRESHOLDS['function_lines']), ('branch_complexity', symbol['branches'], THRESHOLDS['function_branches'])]:
                    if value > threshold:
                        self.finding(rule, language, [row['node']] + ([symbol_id] if symbol_id else []), [location], 'Measured function metric exceeds the review threshold; nested function decisions are counted separately', value=value, threshold=threshold)
                if symbol['duplicate']:
                    duplicate_groups.setdefault((language, symbol['duplicate']), []).append(location)
            for item in result['imports']:
                candidates, resolution, reason = analyzers.resolve_import(path, item, set(self.files), self.scope['configuration']['pythonRoots'])
                require(len(self.summary['dependencies']) < 1024, 'scope_limit')
                target = self.files[candidates[0]][0]['id'] if resolution == 'local_candidate' else None
                dep = {'source': row['node'], 'target': target, 'path': path, 'line': item['line'],
                       'resolution': resolution, 'reason': reason, 'analyzer': language,
                       'candidates': candidates, 'source_ref': row['source'], 'identity': row['identity']}
                self.summary['dependencies'].append(dep)
                if target:
                    edge_id = 'analysis-dependency:' + key([row['node'], target])[:40]
                    if not any(e['id'] == edge_id for e in self.bundle['edges']):
                        self.bundle['edges'].append({'id': edge_id, 'source': row['node'], 'target': target, 'relation': 'depends_on',
                            'origin': 'adapter', 'mapping': 'proposed', 'sources': [row['source'], self.files[candidates[0]][1]['id']]})
        for (_, _), locations in sorted(duplicate_groups.items()):
            if len(locations) > 1:
                # Bound the whole finding instead of silently truncating members.
                require(len(locations) <= 32, 'scope_limit')
                self.finding('normalized_duplicate_function', 'duplicates/v1', sorted({n for l in locations for n in (l['node'], l['symbol']) if n}), locations,
                    'Functions have equal normalized syntax; identifiers and literals remain significant. Review for intentional repetition, not semantic redundancy', value=len(locations), threshold=1)

    def intent(self, raw):
        config = self.scope['configuration']
        sid = self.source(managed_relative(self.root, CONFIG), raw)
        root = next(n['id'] for n in self.bundle['nodes'] if n['kind'] == 'system')
        for path in config['expectedFiles']:
            if path in self.files:
                node, _ = self.files[path]
                state = 'observed'
                node['presence'] = 'both'
                node['sources'] = sorted(set(node['sources'] + [sid]))
            else:
                state = 'not_observed'
                node = {'id': 'analysis-intended:' + key([self.map['root_identity'], path])[:40], 'kind': 'file', 'title': Path(path).name[:160],
                    'presence': 'intended', 'origin': 'design', 'mapping': 'proposed', 'sources': [sid], 'lifecycle': {'owner': 'adapter', 'state': 'unknown'},
                    'plan': 'draft', 'criteria': [], 'reason': 'File explicitly expected by the coding architecture declaration; implementation not observed in this bounded inventory'}
                self.bundle['nodes'].append(node)
                self.bundle['edges'].append({'id': 'analysis-intended-edge:' + key(path)[:40], 'source': root, 'target': node['id'], 'relation': 'references', 'origin': 'adapter', 'mapping': 'proposed', 'sources': [sid]})
            self.summary['comparison'].append({'path': path, 'node': node['id'], 'state': state, 'source': sid})
        for rule in config['forbiddenDependencies']:
            for dep in self.summary['dependencies']:
                if dep['path'] == rule['from'] and dep['resolution'] == 'local_candidate' and dep['candidates'] == [rule['to']]:
                    self.finding('forbidden_dependency:' + rule['id'], 'architecture/v1', [dep['source'], dep['target']], [self.location(rule['from'], dep['line']), self.location(rule['to'])],
                        'A resolved static candidate pair violates this explicit architecture declaration; this is a static rule violation, not runtime proof',
                        classification='measured', sources=[sid, dep['source_ref'], self.files[rule['to']][1]['id']])

    def reports(self):
        for report in self.scope['configuration']['reports']:
            self.tick()
            item = self.reader.observe(self.root / report['path'], {})
            raw = item[-1] if item else None
            sid = self.source(report['path'], raw, 'evidence')
            result = {**report, 'source': sid, 'identity': self.sources[sid]['identity'], 'state': 'missing' if raw is None else 'unavailable', 'currentness': 'unassessed', 'counts': {}}
            if raw is not None:
                try:
                    text = raw.decode('utf-8')
                    require(not re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I), 'unsupported_report')
                    tree = ET.fromstring(text)
                    require(sum(1 for _ in tree.iter()) <= 20000, 'unsupported_report')
                    if report['kind'] == 'junit':
                        require(tree.tag in ('testsuite', 'testsuites'), 'unsupported_report')
                        rows = [tree] if tree.tag == 'testsuite' else [r for r in tree.iter('testsuite') if not r.findall('testsuite')]
                        require(rows and all(r.get('tests') is not None for r in rows), 'unsupported_report')
                        names = ('tests', 'failures', 'errors', 'skipped')
                    else:
                        require(tree.tag == 'coverage', 'unsupported_report')
                        rows, names = [tree], ('lines-valid', 'lines-covered', 'branches-valid', 'branches-covered')
                        require(tree.get('lines-valid') is not None and tree.get('lines-covered') is not None, 'unsupported_report')
                    counts = {k: sum(int(r.get(k)) for r in rows) for k in names if all(r.get(k) is not None for r in rows)}
                    require(all(0 <= v <= 100000000 for v in counts.values()), 'unsupported_report')
                    result.update(state='linked', counts=counts)
                except (ValueError, ET.ParseError, UnicodeError, RecursionError):
                    pass
            self.summary['reports'].append(result)

    def finish(self):
        projection = model.build_map_projection(self.bundle)
        for cycle in projection['diagnostics']:
            if cycle['code'] == 'dependency_cycle':
                members = set(cycle['members'])
                locations = [self.location(p) for p, (n, _) in self.files.items() if n['id'] in members]
                self.finding('static_dependency_cycle', 'imports/v1', sorted(members), locations,
                    'These files form a cycle of static local candidates; a cycle is a review signal, not proof of runtime failure')
        self.summary['findings'].sort(key=lambda f: f['id'])
        self.bundle['snapshot_id'] = 'analysis:' + key([self.bundle, self.summary])
        self.map['projection'] = model.build_map_projection(self.bundle)
        self.map['analysis'] = self.summary
        return model._bounded_copy(self.map, model.MapPolicy(), model.MapPolicy().max_output_bytes)


def observe_analysis(request, preview_id, snapshot, revision, controls_scope_id, scope_id, *, runtime=None):
    scope = preview_analysis(request, preview_id, snapshot, revision, controls_scope_id, runtime=runtime)
    require(scope['scope_id'] == scope_id, 'preview_mismatch')
    observation = observe_controls(request, preview_id, snapshot, revision, controls_scope_id) if controls_scope_id else observe_reviewed(request, preview_id)
    require(observation['review']['source_snapshot'] == snapshot, 'stale_snapshot')
    require(observation['review']['revision'] == revision, 'definition_conflict')
    root = _root(request['project_root'])
    started = time.monotonic()
    def tick(): require(time.monotonic() - started < 10, 'time_limit')
    try:
        with _Snapshots(root, {}, ReadPolicy()) as reader:
            identity = reader.observe(root, {}, directory=True)
            require(analyzers.digest((os.path.normcase(str(root)) + '\0' + repr(identity)).encode()) == observation['root_identity'], 'changed_input')
            item = reader.observe(managed_path(root, CONFIG), {})
            raw = item[-1] if item else None
            require((analyzers.digest(raw) if raw is not None else None) == scope['config_identity'], 'changed_input')
            projection = Projection(observation, scope, root, reader, tick)
            projection.measure(runtime)
            projection.intent(raw)
            projection.reports()
            reader.recheck(); tick()
            latest = observe_reviewed(request, preview_id)
            require(latest['review']['source_snapshot'] == snapshot and latest['review']['revision'] == revision and latest['root_identity'] == observation['root_identity'], 'changed_input')
            require(preview_analysis(request, preview_id, snapshot, revision, controls_scope_id, runtime=runtime)['scope_id'] == scope_id, 'changed_input')
            result = projection.finish(); tick()
            return result
    except SetupServiceError as error:
        raise AnalysisError({'too_large': 'scope_limit', 'input_limit': 'scope_limit'}.get(error.code, error.code)) from None
    except model.MapValidationError:
        raise AnalysisError('scope_limit') from None
    except (OSError, ValueError, RecursionError, MemoryError) as error:
        if isinstance(error, AnalysisError): raise
        raise AnalysisError('analysis_unavailable') from None
