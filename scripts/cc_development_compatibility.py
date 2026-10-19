"""Read-only known predecessor inspection and exact reviewed compatibility steps.

Target identities must come from the installed owner, never project/request JSON.
Inspection is not historical validation or permission to migrate. The writer must
validate the complete original registry and reobserve inputs under its lock.
Only intent/design/plan decisions retain applicability. Post-plan acceptance must
be made under the current method; historical execution receipts need fresh checks.
"""
import argparse
import hashlib
import json
import re
import stat
from pathlib import Path

# Filled only after the reviewed PW00 semantic owner bytes are frozen. An empty
# table deliberately denies transitions; versions and caller flags are not proof.
SEMANTIC_PINS = {'cc_development_lib/method.py': 'e4286c6928624a12757c7b348945f81588266a52c68c0e5864dc24ed8ca909f3', 'cc_development_lib/schema.py': '40c4aee48a41895609dc70aa567a6ad33fdc27c4156229852f5292ad49ab4a77', 'cc_development_lib/evaluator.py': '860fda1dd1af2916b00e5b06ec53563b495a201a4d66880c8e2d5c48b55a24b1', 'cc_development_lib/policy.py': '62e9c3bd970b13df712b6b0d39148cf00ac7df1f817053748695c44aa6ef39b7', 'cc_development_lib/storage.py': 'f2d822a5b469cb69e6d9ae6d4e1d35cba59d661069138117288eeb102560081d', 'cc_development_lib/__init__.py': 'c13132d2ba3fa863a888c05efb14b68fca4ef9050bb46ee4397c3e08bff1486b', 'cc_development_execution.py': '48c95f38fdaec11eac490913db82d85ba718d1703e8cec1b73b303654815c1d6', 'cc_development_adapters.py': 'fdd081f92673019bc4cb162d068609ba4b0aa809450c01178f0fdf2eb20a5d9d', 'cc_development_gateway.py': '71b9b4814d8f21ce91f52db7f011ea233ab2b8e3c37f8507e59232406edea67d', 'cc_development_eval.py': 'e4b7e0cad6800136f5e9b4d672da1cf47b22a1471fc87e248bee09eed693123e', 'cc_development_codex.py': 'ff829877b92ce1a0c13f894bf2f8156862e7ba9b669e371ee5ef0a8ecf1ae827', 'cc_development_jobs.py': 'bf6a23e53272f473dcd3299436b375dfb6cfcfc9f938346e6561af1791123192', 'cc_development_release.py': '6cf42655b26043cec449c6ddf34fa4adb5757229611badbec7579934fe640f77', 'cc_development_store.py': '0fe3920844b3499fccf7847beef6a69d9697dc29091cbb41601b3054b8052d5d', 'cc_development_migration.py': '6fa3b1d37a793104c17ea6c7701bca0c7d1141eacd2e2582b70bfe5dc227a4c8', 'cc_development_bundle.py': '5e82875381e87aad9836a5492e8ce8eeed3511e5fdf2248b13b8eed3229c99a7', 'cc_development_sessions.py': '3e363bd2df06b66f98f259a55e830d0dc05dc52adaf16da7ee5456a862ac4571', 'cc_development_archive.py': '8338c9f978dbcfd33d7977435a662a36b97d45d315f60b0d56605cf556a9f4d1', 'cc_development_history.py': 'f81860fc88a41dbffbae50e9d47e6f0d4bfbb4596df558614d550dbe1bb3f410', 'cc_product.py': '346f92f3dc1f3c9ab5862bdf998f6340597d9db0beccc6ffbea8e3c3c5f7440e', 'cc_product_evolution.py': '73b987607ee580dc10081666e0113774172c8513013e4314f475c9427e10a57c', 'cc_product_documents.py': '763636ca7b0d430581426b726bf1657eedcd4e67790738ed04c8c824413cc144', 'cc_product_adoption.py': 'e00a31ec1884c3ec70d8abd4d9e0ed38f047b3d097788e330ef8db99c9ffe384', 'cc_product_planning.py': '5459c214b00f5aec62627e631ba7ef2416e06d1ef005a05b784a88541d7823a9', 'cc_product_progress.py': '4b76fed7d1a8b7ebadedfd87ade3022db5b25ceef733eebd027418b0063876f2', 'cc_product_progress_contract.py': 'c7028bf6488814cded02dc0430f81b5b3cb2928ad1965bac042820f88af79a90', 'cc_product_progress_observation.py': '818d9e8228592edbd773a5720948150a4884f04a74d3c7478119dcc094e36321', 'cc_evidence_inputs.py': '5fe68e6127de1a8013763718257f1c66080a652828def6ebdfc08e2a219f07da', 'cc_evidence_process.py': '7196bc963f1d60fb6c8b5eeb9ea725516d4da204d5b7ded480356511556f40e6', 'cc_product_cli.py': '634f997d3efece73a1309f60b2a7adfc017c2c56bcf0eda4317c276d09e5caec', 'cc_planning.py': 'b91e27df5bc0354dc02e67bd9487e2350d981056f040efa99f44885236a9839b', 'cc_planning_cli.py': 'fcf974b43d37d074fa35c51397c24f3a084bcf0ef83d36fe0a19a83e8813f161', 'cc.py': '6bf05c016f45a7841fb573e18a68b7a15278fce7bb1a5ae9c500ccd4301bc28a'}
SEMANTIC_FILES = ('cc_development_lib/method.py', 'cc_development_lib/schema.py', 'cc_development_lib/evaluator.py',
                  'cc_development_lib/policy.py', 'cc_development_lib/storage.py',
                  'cc_development_lib/__init__.py', 'cc_development_execution.py',
                  'cc_development_adapters.py', 'cc_development_gateway.py', 'cc_development_eval.py',
                  'cc_development_codex.py',
                  'cc_development_jobs.py', 'cc_development_release.py',
                  'cc_development_store.py', 'cc_development_migration.py', 'cc_development_bundle.py',
                  'cc_development_sessions.py', 'cc_development_archive.py', 'cc_development_history.py',
                  'cc_product.py','cc_product_evolution.py','cc_product_documents.py','cc_product_adoption.py',
                  'cc_product_planning.py','cc_product_progress.py','cc_product_progress_contract.py','cc_product_progress_observation.py','cc_evidence_inputs.py','cc_evidence_process.py','cc_product_cli.py', 'cc_planning.py', 'cc_planning_cli.py', 'cc.py')

PRIOR = {'id': 'controlcoding-controlled-development', 'version': '0.5.0',
         'digest': '2d16b55e0040fff17fa0b64a5748ea908d26cf7f665c4b63f5e099830842501d',
         'evaluator_version': '1',
         'implementation_digest': '658ab4a8e122ede2854048033a1f94962837a8d9eee582fcaadedd77c8679260'}
RETAINED = {'id': 'controlcoding-controlled-development', 'version': '0.6.0',
            'digest': '1f0ec1fdfc17fe4d685140a38386424dc34e3437de4c7d58c14b4fe934c75235',
            'evaluator_version': '1',
            'implementation_digest': 'bfda53058e5a8ddd971a830020189aa463db16ebc892cb8c711c9dfd7d93740a'}
_FIELDS = frozenset(PRIOR)
RETENTION = {'id':'controlcoding-controlled-development','version':'0.6.1',
             'digest':'a7bb6521c582e504773644b4b492d983daabf201a8b55c9006e59513b1e7c33d',
             'evaluator_version':'1','implementation_digest':'9fe7a8fce4ca261354cc9dd7bedd76ab6c9c8e9aadef2246517348bf27cbf5a0'}
_POLICY = 'cd10_storage_only_v1'
PRODUCT = {'id':'controlcoding-controlled-development','version':'0.7.0',
           'digest':'1552705656ff9108631994f12575a1102d5750db3ae18a335fa57f28d1e17880',
           'evaluator_version':'1','implementation_digest':'39f46ae686174a15b05ece6eead7494ae75dffc7dcf4e52601c0861e9a369a44'}
DISCUSSION = {'id':'controlcoding-controlled-development','version':'0.8.0',
              'digest':'1b14d5372a44956abe0e75febce041da0f87323bcdc9d8313513832f497da798',
              'evaluator_version':'1','implementation_digest':'23903b6a1091c91542d350a4699f6844f628d2e4383cadb57dc11326ba56d46c'}
EVOLUTION = {'id':'controlcoding-controlled-development','version':'0.9.0',
             'digest':'48b3f0609b0fabe0cf62520a477adaff15db170cee266bbee2a781e108ca4eab',
             'evaluator_version':'1','implementation_digest':'7ab9261cc5d721805afa2944bde522712e9af6d84a9cf66e3f8aaf58323870b5'}
PLANNING = {'id':'controlcoding-controlled-development','version':'0.10.0',
            'digest':'decce80ae8ffb0ac4efa86855a7af8d7da760b368f5bc51a45c971ea751530d1',
            'evaluator_version':'1','implementation_digest':'2c20fd0e25307d52a135c4513e75031387df916ffac14021eecda5a7381c4622'}
_RECEIPTS = ('.controlcoding/development_job_receipts',
             '.controlcoding/development_execution_receipts',
             '.controlcoding/development_apply_receipts')


class CompatibilityError(ValueError):
    """Stable error code; no source contents in diagnostics."""


def _identity(value):
    return (type(value) is dict and set(value) == _FIELDS
            and all(type(v) is str for v in value.values())
            and value['id'] == PRIOR['id'] and value['evaluator_version'] == '1'
            and all(re.fullmatch('[0-9a-f]{64}', value[k]) is not None
                    for k in ('digest', 'implementation_digest')))


def trusted_pair(source, target):
    """Return one exact reviewed edge and its explicit applicability policy.

    The seven supported edges retain their own rules. Version strings and caller
    equivalence claims never substitute for fixed predecessor/target identities.
    """
    if not _identity(source) or not _identity(target):
        raise CompatibilityError('unsupported_compatibility_pair')
    if source == PRIOR and target == RETAINED:
        return _policy(source, target, _POLICY)
    if source == RETAINED and target == RETENTION:
        return _policy(source,target,'pw00_retention_storage_only_v1')
    if source == RETENTION and target == PRODUCT:
        return _policy(source,target,'pw01_optional_product_unlinked_gates_unchanged_v1')
    if source == PRODUCT and target==DISCUSSION:
        return _policy(source,target,'pw02_optional_discussion_pw01_gates_unchanged_v1')
    if source == DISCUSSION and target == EVOLUTION:
        return _policy(source, target, 'pw03_revision_scope_dependencies_unaffected_decisions_v1')
    if source == EVOLUTION and target == PLANNING:
        return _policy(source, target, 'pw04_explicit_detail_upgrade_unaffected_decisions_v1')
    if source != PLANNING or target['version'] != '0.11.0':
        raise CompatibilityError('unsupported_compatibility_pair')
    _trusted_target(target)
    return _policy(source, target, 'pw05_observed_progress_unaffected_planning_decisions_v1')


def _policy(source, target, name):
    return {'policy': name, 'source': dict(source), 'target': dict(target),
            'applicable_decision_stages': ['intent', 'design', 'plan'],
            'execution_evidence': 'historical_requires_current_checks'}


def trusted_chain(source, target):
    """Only the ordered exact reviewed predecessor steps, never skipped policies."""
    chain=[PRIOR,RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING]
    if source not in chain or source==target:raise CompatibilityError('unsupported_compatibility_pair')
    start=chain.index(source)
    if target in chain:
        stop=chain.index(target)
        if stop<=start:raise CompatibilityError('unsupported_compatibility_pair')
        return [trusted_pair(a,b) for a,b in zip(chain[start:stop],chain[start+1:stop+1])]
    return [trusted_pair(a,b) for a,b in zip(chain[start:],chain[start+1:]+[target])]


def _trusted_target(target):
    """Installed identity plus independently frozen semantic owner bytes."""
    from cc_development_lib.method import IDENTITY
    from cc_evidence_inputs import SafeRoot, Budget
    if target != IDENTITY or set(SEMANTIC_PINS) != set(SEMANTIC_FILES):
        raise CompatibilityError('unsupported_compatibility_pair')
    with SafeRoot(Path(__file__).absolute().parent) as root:
        budget = Budget(total=4 * 1024 * 1024, per_file=2 * 1024 * 1024, entries=48)
        for name in SEMANTIC_FILES:
            raw, info = root.read(name, budget)
            if info.st_nlink != 1 or hashlib.sha256(raw).hexdigest() != SEMANTIC_PINS[name]:
                raise CompatibilityError('compatibility_semantics_changed')
        root.validate()


def change_identity_allowed(original, current, compatibility):
    """Permit retained original identities, never rewriting historical records."""
    return identity_checker(current, compatibility)(original)


def identity_checker(current, compatibility):
    """Qualify once per validation boundary; retain no cross-call file cache."""
    if not _identity(current):
        return lambda original: False
    if compatibility is None or compatibility == []:
        allowed = (dict(current),)
        return lambda original: _identity(original) and original in allowed
    policies = compatibility if type(compatibility) is list else [compatibility]
    if not _valid_chain(policies, current):
        return lambda original: False
    allowed = tuple([dict(current)] + [dict(row['source']) for row in policies])
    return lambda original: _identity(original) and original in allowed


def _valid_chain(policies, current):
    if type(policies) is not list or not 1 <= len(policies) <= 7:
        return False
    try:
        if any(type(row) is not dict or row != trusted_pair(row.get('source'), row.get('target')) for row in policies):
            return False
        return (policies[-1]['target'] == current
                and all(a['target'] == b['source'] for a, b in zip(policies, policies[1:])))
    except CompatibilityError:
        return False


def _valid_policy(value, current):
    return _valid_chain([value], current)


def _json(raw):
    def pairs(rows):
        value = {}
        for k, v in rows:
            if k in value:
                raise CompatibilityError('duplicate_json_key')
            value[k] = v
        return value
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(CompatibilityError('invalid_state')))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, CompatibilityError):
            raise
        raise CompatibilityError('invalid_state') from None


def inspect_state(state, target=None):
    """Report original history boundaries without promoting any decision/proof."""
    record_limit = 32768 if type(state) is dict and state.get('method') in (RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING) else 2048
    fields = {'schema_version', 'enabled', 'profile', 'method', 'project_identity', 'changes', 'operations'}
    allowed = (fields,fields|{'retention'},fields|{'product'},fields|{'product','retention'}) if type(state) is dict and state.get('method') in (PRODUCT,DISCUSSION,EVOLUTION,PLANNING) else (fields,fields|{'retention'})
    if (type(state) is not dict or set(state) not in allowed or type(state['schema_version']) is not int
            or state['schema_version'] != 1 or type(state['enabled']) is not bool
            or state['method'] not in (PRIOR, RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING) or not _identity(state['method'])
            or state['profile'] not in ('routine', 'standard', 'critical')
            or type(state['changes']) is not list or len(state['changes']) > 256
            or type(state['operations']) is not list or len(state['operations']) > record_limit
            or type(state['project_identity']) is not str
            or re.fullmatch('[0-9a-f]{64}', state['project_identity']) is None):
        raise CompatibilityError('unsupported_historical_state')
    changes = []
    seen = set()
    for change in state['changes']:
        if (type(change) is not dict or change.get('method') not in (PRIOR, RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING)
                or type(change.get('id')) is not str or change['id'] in seen
                or type(change.get('closed')) is not bool or type(change.get('history')) is not list
                or len(change['history']) > record_limit):
            raise CompatibilityError('invalid_historical_change')
        seen.add(change['id'])
        previous = change.get('history_base',{}).get('head','absent')
        for record in change['history']:
            if (type(record) is not dict
                    or set(record) != {'action', 'result', 'timestamp', 'previous', 'digest'}
                    or record['previous'] != previous):
                raise CompatibilityError('invalid_history')
            raw = json.dumps({k: v for k, v in record.items() if k != 'digest'}, sort_keys=True,
                             ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()
            if record['digest'] != hashlib.sha256(raw).hexdigest():
                raise CompatibilityError('invalid_history')
            previous = record['digest']
        changes.append({'id': change['id'], 'closed': change['closed'],
                        'originalMethod': dict(change['method']), 'historyRecords': len(change['history']),
                        'historyHead': previous})
    return {'schemaVersion': 1, 'readOnly': True, 'sourceSchema': 1,
            'sourceMethod': dict(state['method']), 'enabled': state['enabled'],
            'changes': changes, 'operationCount': len(state['operations']),
            'strictHistoricalValidationRequired': True, 'migrationPerformed': False,
            'compatibility': trusted_chain(state['method'], target) if target is not None else None}


def _effects(root, budget):
    from cc_layout import managed_relative
    effects = []
    observed = []
    for logical in _RECEIPTS:
        relative = managed_relative(root.path, logical)
        try:
            entries = root.listing(relative, budget)
        except FileNotFoundError:
            continue
        for name, entry in sorted(entries):
            # Windows directory enumeration can report zero link count;
            # the confined handle read below supplies the authoritative one.
            if not stat.S_ISREG(entry.st_mode) or not name.endswith('.json'):
                raise CompatibilityError('unsupported_receipt_entry')
            data, receipt_info = root.read(relative + '/' + name, budget)
            if receipt_info.st_nlink != 1:
                raise CompatibilityError('unsafe_receipt_file')
            value = _json(data)
            allowed = {'running', 'allowed', 'blocked'} if logical != _RECEIPTS[2] else {'running', 'partial', 'applied'}
            if type(value) is not dict or value.get('state') not in allowed:
                raise CompatibilityError('invalid_receipt_state')
            row = {'path': relative + '/' + name, 'sha256': hashlib.sha256(data).hexdigest()}
            observed.append(row)
            if value['state'] in {'running', 'partial'} or (value['state'] == 'blocked' and value.get('kind') in {'deploy', 'rollback'}):
                effects.append({**row, 'state': value['state'], 'reason': 'effect_reconciliation_required'})
    return {'observedReceipts': observed, 'blockingEffects': effects, 'migrationBlocked': bool(effects)}


def inspect_effects(project):
    """Read current effects without treating a current registry as a predecessor."""
    from cc_evidence_inputs import SafeRoot, Budget
    with SafeRoot(project) as root:
        result = _effects(root, Budget(total=16 * 1024 * 1024, per_file=2 * 1024 * 1024, entries=2048))
        root.validate()
        return result


def inspect_project(project, target=None, *, locked=False):
    """Bounded confined inspection, including interrupted external-effect records."""
    from cc_evidence_inputs import SafeRoot, Budget
    from cc_layout import managed_relative
    with SafeRoot(project) as root:
        budget = Budget(total=16 * 1024 * 1024, per_file=2 * 1024 * 1024, entries=2048)
        lock = managed_relative(root.path, '.controlcoding/development.lock')
        if not locked:
            try:
                with root.directory(lock):
                    raise CompatibilityError('recovery_required')
            except FileNotFoundError:
                pass
        item = root.read(managed_relative(root.path, '.controlcoding/development.json'), budget, missing=True)
        if item is None:
            raise CompatibilityError('historical_registry_absent')
        raw, info = item
        if info.st_nlink != 1:
            raise CompatibilityError('unsafe_state_file')
        decoded = _json(raw)
        if type(decoded) is dict and decoded.get('schema_version') == 2:
            from cc_development_store import read_state
            logical, _, _ = read_state(root)
            result = inspect_state(logical, target)
            result['sourceSchema'] = 2
        else:
            result = inspect_state(decoded, target)
        result['sourceSha256'] = hashlib.sha256(raw).hexdigest()
        result.update(_effects(root, budget))
        root.validate()
        return result


def main(argv=None):
    from cc_evidence_inputs import EvidenceError
    from cc_layout import LayoutError
    parser = argparse.ArgumentParser(description='Read-only inspection of exact retained CD10/PW00 registries.')
    parser.add_argument('--project-root', required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect_project(args.project_root)
    except (CompatibilityError, EvidenceError, LayoutError, OSError, ValueError) as error:
        print(json.dumps({'readOnly': True, 'migrationPerformed': False,
                          'error': str(error) if isinstance(error, CompatibilityError) else 'inspection_failed'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 2 if result['migrationBlocked'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
