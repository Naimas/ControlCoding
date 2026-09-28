"""Read-only canonical control projections; see docs/project-map-controls.md."""
from cc_layout import managed_relative, is_contained
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

import cc
import cc_feature
import cc_project_map_model as model
from cc_project_map_definition import observe_reviewed, digest, encoded
from cc_project_map_sources import _relative, _excluded, preview_project_map_scope, MapSourceError
from cc_setup_service import _Snapshots, _root, _ordinary, ReadPolicy, SetupServiceError

ADAPTER = 'cc-project-map-controls/v1'
REGISTRY = '.controlcoding/features/features.json'
FIXED = [REGISTRY, *[f'{plane}/{name}.json' for plane in ('.controlcoding', '.claude')
                    for name in ('cc_config', 'active_module', 'lift_request', 'hooks_lifted')]]
MODULE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


class ControlsError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code='invalid_controls'):
    if not condition:
        raise ControlsError(code)


def safe_text(value, limit=160):
    return type(value) is str and 0 < len(value) <= limit and not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value)


def _context():
    value = os.environ.get('CC_ACTIVE_MODULE', '').strip()
    return {'state': 'absent' if not value else 'present' if MODULE.fullmatch(value) else 'invalid',
            'module': value if value and MODULE.fullmatch(value) else None}


def preview_controls(request, preview_id, snapshot, revision):
    require(preview_project_map_scope(request)['preview_id'] == preview_id, 'preview_mismatch')
    require(type(snapshot) is str and re.fullmatch(r'snapshot:[0-9a-f]{64}', snapshot), 'invalid_request')
    require(revision == 'absent' or (type(revision) is str and re.fullmatch(r'[0-9a-f]{64}', revision)), 'invalid_request')
    root = _root(request['project_root'])
    prepared = {kind: cc._evidence_prepare(root, kind) for kind in ('verification', 'invariants')}
    # Preparation reads only the named contracts, not receipts/source identity.
    contracts = [d for kind in prepared.values() for d in kind['contracts']]
    extra = sorted({p for item in prepared.values() for p in item['policy'].get('extraPaths', [])})
    names = sorted({p for item in prepared.values() for p in item['policy'].get('environmentNames', [])})
    scope = {'schema_version': 1, 'adapter': ADAPTER, 'files': [managed_relative(root, p) for p in FIXED if not (is_contained(root) and p.startswith('.claude/'))],
             'contracts': contracts, 'extra_paths': extra, 'environment_names': names,
             'receipt_folders': [managed_relative(root, p) for p in ('.controlcoding/verification_receipts', '.controlcoding/invariant_receipts')],
             'source_identity': 'Canonical evidence inventory may inspect raw files beyond the code map; confined Git metadata inspection may use a private temporary projection.',
             'limits': {'control_file_bytes': 1048576, 'control_total_bytes': 8388608, 'control_entries': 256,
                        'lock_files': 64, 'discovery_entries': 5000, 'helper_seconds': 15},
             'evidence_limits': {'input_file_bytes': cc.cc_evidence_inputs.MAX_FILE,
                 'input_total_bytes': cc.cc_evidence_inputs.MAX_TOTAL, 'input_entries': cc.cc_evidence_inputs.MAX_ENTRIES,
                 'owner_seconds': cc.cc_evidence_inputs.MAX_SECONDS},
             'host_coverage': 'unknown', 'snapshot': snapshot, 'revision': revision}
    scope['scope_id'] = digest(encoded({'scope': scope, 'request': request, 'preview_id': preview_id, 'process_context': _context()}))
    return scope


def _hooks():
    """Import installed trusted definitions only; never call a hook entry point."""
    folder = Path(__file__).resolve().parent.parent / 'templates/hooks'
    require(folder.is_dir(), 'policy_owner_unavailable')
    saved = list(sys.path)
    sys.path.insert(0, str(folder))
    try:
        result = []
        for name in ('feature_lock', 'check_boundaries'):
            spec = importlib.util.spec_from_file_location('_cc_map_' + name, folder / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result.append(module)
        return (*result, folder)
    finally:
        sys.path[:] = saved


class Inputs:
    def __init__(self, root, reader):
        self.root, self.reader = root, reader
        self.raw = {}
        self.listings = {}
        self.started = time.monotonic()

    def tick(self):
        require(time.monotonic() - self.started <= 12, 'time_limit')

    def physical(self, relative):
        return managed_relative(self.root, relative) if relative.startswith(('.controlcoding/', '.claude/')) else relative

    def read(self, relative):
        self.tick()
        if is_contained(self.root) and relative.startswith('.claude/'):
            return None
        if relative not in self.raw:
            snapshot = self.reader.observe(self.root / self.physical(relative), {})
            self.raw[relative] = snapshot[-1] if snapshot is not None else None
        return self.raw[relative]

    def data(self, relative):
        data = self.read(relative)
        if data is None:
            return None
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result)
                result[key] = value
            return result
        try:
            value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs)
            require(type(value) is dict)
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ControlsError('invalid_controls') from None

    def fallback(self, name):
        canonical, legacy = '.controlcoding/' + name + '.json', '.claude/' + name + '.json'
        chosen = canonical if is_contained(self.root) or self.read(canonical) is not None else legacy
        return self.data(chosen), chosen

    def names(self, relative, limit):
        self.tick()
        path = self.root / self.physical(relative) if relative else self.root
        snapshot = self.reader.observe(path, {}, directory=True)
        if snapshot is None:
            self.listings[relative] = None
            return []
        fd = self.reader.entries[path][2]
        names = []
        with os.scandir(path if os.name == 'nt' else fd) as listing:
            for item in listing:
                require(len(names) < limit, 'scope_limit')
                names.append(item.name)
        self.listings[relative] = sorted(names)
        return names

    def locks(self):
        contained = is_contained(self.root)
        start = managed_relative(self.root, '.controlcoding/module-locks') if contained else ''
        pending, count, result = [(start, 0)], 0, []
        while pending:
            relative, depth = pending.pop()
            require(depth <= 24, 'scope_limit')
            for name in self.names(relative, 5000 - count):
                count += 1
                child = (relative + '/' if relative else '') + name
                if name == '.feature-lock.json':
                    require(len(result) < 64, 'scope_limit')
                    result.append((child, self.data(child)))
                elif contained or not _excluded(child):
                    try:
                        _relative(child)
                        path = self.root / child
                        info = path.lstat() if os.name == 'nt' else os.stat(name, dir_fd=self.reader.entries[path.parent][2], follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            _ordinary(info, True, 'control_directory')
                            pending.append((child, depth + 1))
                    except (MapSourceError, SetupServiceError):
                        # Unsupported ordinary-code paths are already omissions in the base map.
                        # A missing discovery boundary prevents a complete lock interpretation.
                        raise ControlsError('unsupported_path') from None
        return result

    def recheck(self):
        self.reader.recheck()
        for relative, expected in list(self.listings.items()):
            if expected is not None:
                self.names(relative, max(5000, len(expected) + 1))
                require(self.listings[relative] == expected, 'changed_input')
        self.tick()


class Projection:
    def __init__(self, observation, inputs):
        self.map, self.inputs = copy.deepcopy(observation), inputs
        self.bundle = self.map['projection']['bundle']
        self.root = next(n['id'] for n in self.bundle['nodes'] if n['kind'] == 'system')
        self.summary = {'schema_version': 1, 'adapter': ADAPTER, 'host_coverage': 'unknown',
                        'features': {'state': 'absent', 'count': 0, 'issues': []},
                        'active_module': {}, 'gates': [], 'notices': []}

    def source(self, path, owner, fragment=None, raw=None, reason='Confined canonical source; configuration is not host execution evidence'):
        data = self.inputs.read(path) if raw is None else raw
        sid = 'controls-source:' + digest(encoded([path, owner, fragment]))[:40]
        if not any(s['id'] == sid for s in self.bundle['sources']):
            self.bundle['sources'].append({'id': sid, 'owner': owner, 'locator': {'path': self.inputs.physical(path), 'fragment': fragment},
                'identity': 'sha256:' + digest(data) if data is not None else None,
                'adapter': 'cc-project-map-controls-v1', 'resolution': 'resolved' if data is not None else 'unresolved',
                'reason': reason, 'synthetic': False})
        return sid

    def constraint(self, subject, kind, scope, rule, sources, *, decision='unknown', stage='configured', coverage='unknown', context='Selected panel process; editor context and hook delivery unverified', reason, condition, operation='write'):
        identifier = 'control:' + digest(encoded([subject, kind, scope, rule]))[:40]
        self.bundle['constraints'].append({'id': identifier, 'subject': subject, 'kind': kind,
            'operation': operation, 'scope': scope, 'rule': rule, 'sources': sources,
            'applicability': 'applicable' if subject != self.root else 'unknown', 'coverage': coverage,
            'stage': stage, 'decision': decision, 'host': 'unverified', 'context': context,
            'reason': reason, 'responsible': 'Project owner / configured host', 'condition': condition})

    def features(self):
        try:
            registry = self.inputs.data(REGISTRY)
            if registry is None:
                return
            require(registry.get('schemaVersion') == cc_feature.FEATURE_STATE_SCHEMA_VERSION)
            features = registry.get('features')
            require(type(features) is list and len(features) <= 128)
            require(type(registry.get('wipLimit', 1)) is int and 1 <= registry.get('wipLimit', 1) <= 128)
            for feature in features:
                require(type(feature) is dict and safe_text(feature.get('id'), 80) and MODULE.fullmatch(feature['id']))
                require(safe_text(feature.get('title')) and feature.get('state') in cc_feature.FEATURE_STATES)
                criteria = feature.get('acceptanceCriteria', [])
                require(type(criteria) is list and len(criteria) <= 16 and all(safe_text(c, 4096) for c in criteria))
            require(len({f['id'] for f in features}) == len(features))
            issues = cc_feature._validate_registry(registry)
            self.summary['features'] = {'state': 'observed', 'count': len(features), 'issues': ['registry_conflict'] if issues else []}
            for feature in features:
                fid = 'feature:' + digest(feature['id'].encode())[:40]
                sid = self.source(REGISTRY, 'feature', feature['id'])
                criteria = []
                for i, criterion in enumerate(feature.get('acceptanceCriteria', [])):
                    cid = 'criterion:' + digest(encoded([feature['id'], i, criterion]))[:40]
                    criteria.append(cid)
                    self.bundle['nodes'].append({'id': cid, 'kind': 'criterion', 'title': f'Acceptance criterion {i + 1}',
                        'presence': 'intended', 'origin': 'adapter', 'mapping': 'confirmed', 'sources': [sid],
                        'lifecycle': {'owner': 'adapter', 'state': 'unknown'}, 'plan': 'draft', 'criteria': [],
                        'reason': 'Owner criterion declaration; text omitted, no criterion-specific source-bound verdict'})
                    self.bundle['edges'].append({'id': 'criterion-edge:' + digest(cid.encode())[:40], 'source': fid,
                        'target': cid, 'relation': 'contains_work', 'origin': 'adapter', 'mapping': 'confirmed', 'sources': [sid]})
                self.bundle['nodes'].append({'id': fid, 'kind': 'feature', 'title': feature['title'],
                    'presence': 'intended', 'origin': 'adapter', 'mapping': 'confirmed', 'sources': [sid],
                    'lifecycle': {'owner': 'feature', 'state': feature['state']}, 'plan': 'draft' if criteria else 'unknown',
                    'criteria': criteria, 'reason': 'Canonical reported lifecycle. Free-text scope does not establish a code mapping; feature-local receipts do not establish current v2 evidence'})
                self.bundle['edges'].append({'id': 'feature-edge:' + digest(fid.encode())[:40], 'source': self.root,
                    'target': fid, 'relation': 'references', 'origin': 'adapter', 'mapping': 'confirmed', 'sources': [sid]})
        except ControlsError:
            self.summary['features'] = {'state': 'invalid', 'count': 0, 'issues': ['invalid_registry']}

    def policies(self):
        lock_owner, boundary, folder = _hooks()
        core_source = self.source('templates/hooks/check_boundaries.py', 'core', raw=(folder / 'check_boundaries.py').read_bytes(),
                                  reason='Installed trusted Core policy template, not an observed adopter hook or proof of host delivery')
        try:
            config, config_path = self.inputs.fallback('cc_config')
            zones = boundary.normalize_protected_zones(config.get('protected_zones', [])) if config else []
            require(len(zones) <= 128)
            for zone in zones:
                require(type(zone['path']) is str and zone.get('level') in ('warn', 'deny'))
                _relative(zone['path'].rstrip('/'))
            config_source = self.source(config_path, 'policy') if config is not None else core_source
        except (ControlsError, MapSourceError, TypeError):
            zones, config_source = [], core_source
            self.summary['notices'].append('invalid_protected_zones')
        zones = [{'path': z[0], 'level': z[2]} for z in boundary.MANDATORY_DENY_ZONES] + zones
        active, active_path = None, '.controlcoding/active_module.json'
        lock_files = []
        conflict = False
        try:
            active, active_path = self.inputs.fallback('active_module')
            if active is not None:
                require(type(active.get('module')) is str and MODULE.fullmatch(active['module']) and active.get('mode') in lock_owner._VALID_MODES)
            lock_files = self.inputs.locks()
            seen = set()
            for path, lock in lock_files:
                require(type(lock) is dict and type(lock.get('version')) is int and lock['version'] == 1 and type(lock.get('module')) is str and MODULE.fullmatch(lock['module']))
                require(lock['module'] not in seen)
                seen.add(lock['module'])
                for field in ('owns', 'shared_write', 'may_read'):
                    values = lock.get(field, [])
                    require(type(values) is list and len(values) <= 128 and all(safe_text(v, 1024) for v in values))
                    if field == 'shared_write':
                        require(all(not any(c in v for c in '*?[') for v in values))
                require('owns' in lock)
            context = _context()
            conflict = context['state'] == 'invalid' or bool(context['module'] and active and context['module'] != active['module'])
            module_name = context['module'] or (active['module'] if active else None)
            mode = active['mode'] if active else 'enforce' if module_name else None
            selected = next(((path, lock) for path, lock in lock_files if lock['module'] == module_name), None)
            if module_name and selected is None:
                conflict = True
            self.summary['active_module'] = {'module': module_name, 'mode': mode, 'source': 'conflict' if conflict else 'file' if active else 'process' if module_name else 'absent',
                'file': active_path if active else None, 'coverage': 'conflicting' if conflict else 'unknown',
                'may_read': 'declarative_only', 'locks': len(lock_files)}
        except (ControlsError, MapSourceError):
            selected, module_name, mode, conflict = None, None, None, True
            self.summary['active_module'] = {'module': None, 'mode': None, 'source': 'invalid', 'file': active_path,
                'coverage': 'conflicting', 'may_read': 'declarative_only', 'locks': 0}
        policy_refs = [self.source(path, 'policy') for path, _ in lock_files]
        if active is not None:
            policy_refs.append(self.source(active_path, 'policy'))
        if conflict:
            self.constraint(self.root, 'feature_perimeter', 'project', 'active_module_conflict', policy_refs or [core_source],
                coverage='conflicting', reason='Active module inputs are invalid, conflicting, duplicated or refer to an undiscovered lock. No global denial is inferred.',
                condition='Resolve the active context and validate it in the actual editing host')
        by_source = {s['id']: s for s in self.bundle['sources']}
        for node in self.bundle['nodes']:
            if node['kind'] not in ('file', 'symbol') or node['presence'] not in ('observed', 'both'):
                continue
            paths = [by_source[s]['locator']['path'] for s in node['sources'] if by_source[s]['owner'] in ('code', 'design')]
            for path in paths:
                protected = any(boundary._parts_match_pattern(path.split('/'), p) for p in boundary.PROTECTED_HOOK_PATTERNS)
                matches = [{'path': p, 'level': 'deny'} for p in boundary.PROTECTED_HOOK_PATTERNS if boundary._parts_match_pattern(path.split('/'), p)] if protected else [z for z in zones if boundary._parts_match_zone(path.split('/'), z['path'])]
                if matches:
                    zone = matches[0]
                    self.constraint(node['id'], 'policy', path, 'self_protection' if protected else 'protected_zone', [core_source, config_source] if core_source != config_source else [core_source],
                        decision='deny' if zone['level'] == 'deny' else 'unknown', stage='predicted',
                        reason='Self-protected by the installed Core template; no generic bypass' if protected else f"Configured {zone['level']} zone for this existing path; lifts and host enforcement are not evaluated",
                        condition='Follow the authoritative scoped owner workflow; verify actual host delivery')
                if selected and not conflict:
                    lock_path, lock = selected
                    owned = lock_owner._match_owns(path, lock['owns'])
                    shared = lock_owner._match_shared_write(path, lock.get('shared_write', []))
                    rule = 'owns_match' if owned else 'shared_write_match' if shared else 'outside_perimeter'
                    self.constraint(node['id'], 'feature_perimeter', path, rule, [self.source(lock_path, 'policy')],
                        decision='allow' if owned or shared else 'deny' if mode == 'enforce' else 'unknown', stage='predicted',
                        reason=f'Active module {module_name}; mode {mode}; {rule}. may_read is declarative, not a write grant.',
                        condition='Use the correct module context in the actual host; ownership cannot override protected zones')
        try:
            pending, pending_path = self.inputs.fallback('lift_request')
            if pending is not None and pending.get('status') == 'PENDING':
                target = pending.get('file')
                _relative(target)
                sid = self.source(pending_path, 'policy')
                for node in self.bundle['nodes']:
                    if any(by_source.get(s, {}).get('locator', {}).get('path') == target for s in node['sources']):
                        self.constraint(node['id'], 'approval', target, 'pending_lift', [sid], decision='approval_required',
                            reason='A pending request names this operation scope. No token, reason body or grant is exposed.',
                            condition='The owner must review the exact request through the established lift workflow')
        except (ControlsError, MapSourceError):
            self.summary['notices'].append('invalid_lift_request')
        self.summary['notices'].append('Host execution and active lifts are unassessed; predictions never authorize writes')

    def gates(self):
        for kind in ('verification', 'invariants'):
            prepared = cc._evidence_prepare(self.inputs.root, kind)
            refs = []
            for descriptor in prepared['contracts']:
                data = self.inputs.read(descriptor['path'])
                require((digest(data) if data is not None else None) == descriptor['sha256'], 'changed_input')
                refs.append(self.source(descriptor['path'], 'evidence'))
            folder = '.controlcoding/' + ('verification_receipts' if kind == 'verification' else 'invariant_receipts')
            # Pin the exact bounded history before using the existing owner's evaluator.
            for name in self.inputs.names(folder, 128):
                if name.endswith('.json'):
                    _relative(name)
                    self.inputs.read(folder + '/' + name)
            history = cc._evidence_history(self.inputs.root, kind, prepared)
            assessment, latest = history['assessment'], history['latest']
            if latest:
                refs.append(self.source(latest['path'], 'evidence'))
            source_refs = refs or [self.source('controlcoding.' + kind + '.json', 'evidence')]
            outcome = (latest or {}).get('status', 'unknown')
            current_pass = assessment['currentRequiredPass'] is True
            value = {'kind': kind, 'state': assessment['state'], 'current_required_pass': current_pass,
                     'outcome': outcome, 'reasons': assessment['reasons'], 'required_count': len(prepared['required']),
                     'contract_valid': not prepared['issues'], 'scope': 'project', 'receipt': latest['path'] if latest else None}
            self.summary['gates'].append(value)
            mapped_outcome = {'passed_subset': 'partial', 'running': 'incomplete'}.get(outcome, outcome)
            if mapped_outcome not in ('passed', 'partial', 'failed', 'incomplete'):
                mapped_outcome = 'unknown'
            self.bundle['assessments'].append({'id': 'gate-assessment:' + kind, 'subject': self.root, 'sources': source_refs,
                'outcome': mapped_outcome, 'freshness': 'current' if assessment['state'] == 'current' else 'stale' if assessment['state'] in ('stale', 'context_changed') else 'unknown',
                'applicability': 'applicable', 'sufficiency': 'complete' if current_pass else 'subset' if outcome == 'passed_subset' else 'unknown',
                'coverage': 'complete' if current_pass else 'unknown',
                'reason': 'Canonical project-wide receipt assessment; never a per-file or feature-criterion acceptance verdict'})
            self.constraint(self.root, 'gate', 'project', kind + '_required_gate', source_refs,
                decision='allow' if current_pass else 'unknown',
                reason=f"{kind} gate: {assessment['state']}; {outcome}; project-wide scope only",
                condition='Obtain a complete current required pass through the canonical command; this view does not run checks', operation='verify_required')


def observe_controls(request, preview_id, snapshot, revision, scope_id):
    scope = preview_controls(request, preview_id, snapshot, revision)
    require(scope['scope_id'] == scope_id, 'preview_mismatch')
    observation = observe_reviewed(request, preview_id)
    require(observation['review']['source_snapshot'] == snapshot, 'stale_snapshot')
    require(observation['review']['revision'] == revision, 'definition_conflict')
    root = _root(request['project_root'])
    try:
        with _Snapshots(root, {}, ReadPolicy()) as reader:
            identity = reader.observe(root, {}, directory=True)
            require(digest((os.path.normcase(str(root)) + '\0' + repr(identity)).encode()) == observation['root_identity'], 'changed_input')
            inputs = Inputs(root, reader)
            projection = Projection(observation, inputs)
            projection.features()
            projection.policies()
            projection.gates()
            inputs.recheck()
            require(preview_controls(request, preview_id, snapshot, revision)['scope_id'] == scope_id, 'changed_input')
            projection.map['controls'] = projection.summary
            projection.bundle['snapshot_id'] = 'controls:' + digest(encoded([projection.bundle, projection.summary]))
            projection.map['projection'] = model.build_map_projection(projection.bundle)
            return model._bounded_copy(projection.map, model.MapPolicy(), model.MapPolicy().max_output_bytes)
    except SetupServiceError as error:
        raise ControlsError({'too_large': 'scope_limit', 'input_limit': 'scope_limit'}.get(error.code, error.code)) from None
    except model.MapValidationError:
        raise ControlsError('scope_limit') from None
    except OSError:
        raise ControlsError('source_unavailable') from None
