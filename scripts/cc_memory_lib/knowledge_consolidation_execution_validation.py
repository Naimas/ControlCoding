"""Validate persisted execution records before backup, restore or commit.

These are archive integrity checks, not permission to execute a restored packet.
Source freshness and lease ownership remain execution-time checks.
"""
import json
import math
import re
from datetime import datetime

from .knowledge_store import KnowledgeError, get
from .knowledge_consolidation import digest
from .knowledge_consolidation_validation import (
    anchor, ident, keys, parsed, require, SHA256, target, text,
)

STATES = frozenset(('prepared', 'reserved', 'started', 'paused', 'canceled',
                    'interrupted', 'failed', 'imported'))
EPISTEMIC = frozenset(('observed', 'decided', 'planned', 'inferred', 'disputed', 'unknown'))
MODES = frozenset(('local', 'api', 'manual'))
OUTCOMES = frozenset(('proposals', 'no_change', 'insufficient_evidence'))
MAX_PACKET = 60 * 1024
MAX_QUEUE = 200


def clean(value, limit, empty=False):
    return text(value, limit, empty=empty) and not re.search(r'[\x00-\x1f\x7f]', value)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def sha(value):
    return type(value) is str and SHA256.fullmatch(value) is not None


def timestamp(value):
    require(type(value) is str and len(value) <= 80)
    try:
        require(datetime.fromisoformat(value).utcoffset() is not None)
    except ValueError as exc:
        raise KnowledgeError('invalid_consolidation_storage') from exc


def config(value):
    keys(value, ('provider', 'model', 'generation', 'maxContextChars', 'maxOutputTokens',
                 'timeoutSeconds', 'maxRequests', 'roleRevision'))
    require(value['provider'] in ('ollama', 'openai') and clean(value['model'], 160, empty=True)
            and clean(value['roleRevision'], 160))
    for key, lo, hi in (('maxContextChars', 1, 12000), ('maxOutputTokens', 128, 4096),
                        ('timeoutSeconds', 5, 120), ('maxRequests', 1, 6)):
        require(integer(value[key], lo, hi))
    g = value['generation']
    keys(g, ('effort', 'thinking', 'reasoningMode', 'verbosity', 'temperature', 'format', 'summary'))
    require(g['effort'] in ('default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
            and g['reasoningMode'] in ('default', 'standard', 'pro')
            and g['verbosity'] in ('default', 'low', 'medium', 'high')
            and g['format'] in ('text', 'json') and type(g['summary']) is bool)
    require(g['thinking'] is None or type(g['thinking']) is bool
            or clean(g['thinking'], 40))
    require(g['temperature'] is None or type(g['temperature']) in (int, float)
            and math.isfinite(g['temperature']) and 0 <= g['temperature'] <= 2)


def packet(db, value, row, manifest, proposals):
    keys(value, ('job', 'request_id', 'mode', 'prompt', 'config', 'manifest_digest',
                 'input_manifest', 'approved_evidence', 'approved_context',
                 'protocol', 'existing_proposals', 'selection_offset'))
    require(value['job'] == row['job'] and value['request_id'] == row['request_id']
            and value['mode'] == row['mode'] and type(value['protocol']) is int
            and value['protocol'] == 1 and text(value['prompt'], 12000))
    require(integer(value['selection_offset'], 0, 1000000))
    config(value['config'])
    if row['mode'] != 'manual':
        require(value['config']['model'].strip() and value['config']['provider'] ==
                ('ollama' if row['mode'] == 'local' else 'openai'))
    inputs = value['input_manifest']
    require(type(inputs) is list and len(inputs) <= 8)
    pinned = {item['source']: item for item in manifest['sources']}
    seen = set()
    for number, item in enumerate(inputs, 1):
        require(type(item) is dict and item.get('evidence_id') == 'S' + str(number))
        original = {key: content for key, content in item.items() if key != 'evidence_id'}
        anchor(original, title=True)
        require(original['source'] not in seen and pinned.get(original['source']) == original)
        seen.add(original['source'])
    prior = value['approved_evidence']
    require(type(prior) is list and len(prior) <= 2 and len(inputs) + len(prior) <= 8)
    require(type(manifest.get('prior_sources', [])) is list
            and len(manifest.get('prior_sources', [])) == len(prior))
    for number, item in enumerate(prior, len(inputs) + 1):
        require(type(item) is dict and item.get('evidence_id') == 'S' + str(number))
        original = {key: content for key, content in item.items() if key != 'evidence_id'}
        anchor(original, title=True)
        require(original == manifest['prior_sources'][number - len(inputs) - 1])
        revision = db.execute('SELECT body FROM revisions WHERE source=? AND revision=?',
                              (original['source'], original['revision'])).fetchone()
        require(revision is not None)
        lines = revision['body'].splitlines()
        require(1 <= original['line'] <= original['end_line'] <= max(1, len(lines)))
        if original['excerpt'] not in '\n'.join(lines[original['line'] - 1:original['end_line']]):
            from .knowledge_sources import passages
            source = {'id': original['source'], 'revision': original['revision'], 'body': revision['body']}
            require(any(part['line'] == original['line'] and part['end_line'] == original['end_line']
                        and part['text'] == original['excerpt'] for part in passages(source)))
    evidence = inputs + prior
    require(len(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) <= 6000)
    evidence_by_id = {item['evidence_id']: item for item in evidence}
    require(len({tuple(item[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt'))
                 for item in evidence}) == len(evidence))
    claims = value['approved_context']
    require(type(claims) is list and len(claims) <= 2
            and len(json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) <= 2000)
    claim_ids = set()
    for claim in claims:
        keys(claim, ('id', 'target', 'revision', 'title', 'body', 'status',
                     'epistemic_status', 'scope', 'evidence_ids'))
        require(target(claim['target']) and claim['id'] == 'memory:' + digest(claim['target'])
                and claim['id'] not in claim_ids and sha(claim['revision'])
                and text(claim['title'], 120) and text(claim['body'], 8000)
                and claim['status'] == 'approved' and claim['epistemic_status'] in EPISTEMIC
                and text(claim['scope'], 160) and type(claim['evidence_ids']) is list
                and 0 < len(claim['evidence_ids']) <= 8
                and all(type(key) is str and key in evidence_by_id for key in claim['evidence_ids'])
                and len(set(claim['evidence_ids'])) == len(claim['evidence_ids']))
        claim_ids.add(claim['id'])
        stored = db.execute('SELECT title,body,dependencies FROM memory_claim_revisions WHERE id=? AND revision=?',
                            (claim['id'], claim['revision'])).fetchone()
        require(stored is not None and stored['title'] == claim['title'])
        mapping = {}
        for dependency in parsed(stored['dependencies']):
            match = next((item['evidence_id'] for item in evidence
                          if all(item[key] == dependency[key]
                                 for key in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt'))), None)
            require(match in claim['evidence_ids'])
            mapping[dependency['citation']] = match
        refs = set(re.findall(r'\[(S\d+)\]', stored['body']))
        require(refs <= set(mapping))
        expected_body = re.sub(r'\[(S\d+)\]', lambda found: '[' + mapping[found.group(1)] + ']', stored['body'])
        require(claim['body'] == expected_body)
    require({item['evidence_id'] for item in prior} <=
            {evidence_id for claim in claims for evidence_id in claim['evidence_ids']})
    require(value['manifest_digest'] == digest({'input_manifest': inputs,
            'approved_evidence': prior, 'approved_context': claims})
            and row['packet_digest'] == digest(value))
    prior = value['existing_proposals']
    require(type(prior) is list and len(prior) <= 20)
    seen = set()
    for item in prior:
        keys(item, ('id', 'status', 'target'))
        require(ident(item['id']) and item['id'] not in seen and item['id'] in proposals
                and proposals[item['id']]['job'] == row['job']
                and item['target'] == proposals[item['id']]['target']
                and item['status'] in ('pending', 'accepted', 'rejected', 'stale'))
        seen.add(item['id'])


def validate(db):
    """Reject malformed, orphaned or unbounded v3 runtime records."""
    jobs = {row['id']: dict(row) for row in db.execute('SELECT * FROM consolidation_jobs')}
    proposals = {row['id']: dict(row) for row in db.execute('SELECT * FROM consolidation_proposals')}
    ordinals = {}
    rows = list(db.execute('SELECT * FROM consolidation_attempts'))
    require(len(rows) <= 120)
    for row in rows:
        require(ident(row['request_id']) and row['job'] in jobs and row['mode'] in MODES
                and row['state'] in STATES and integer(row['ordinal'], 0, 5)
                and type(row['packet']) is str and len(row['packet'].encode('utf-8')) <= MAX_PACKET
                and sha(row['packet_digest']))
        ordinals.setdefault(row['job'], []).append(row['ordinal'])
        value = parsed(row['packet'])
        if value == {}:
            require(jobs[row['job']]['state'] in ('stale', 'purged')
                    and row['state'] == 'interrupted' and row['owner'] is None
                    and row['lease_until'] is None)
        else:
            packet(db, value, row, parsed(jobs[row['job']]['manifest']), proposals)
            require(row['ordinal'] < value['config']['maxRequests'])
        for column in ('lease_until', 'started', 'finished'):
            if row[column] is not None:
                timestamp(row[column])
        require(row['owner'] is None or ident(row['owner']))
        if row['state'] in ('reserved', 'started'):
            require(row['owner'] is not None and row['lease_until'] is not None)
        if row['state'] == 'started':
            require(row['started'] is not None)
        require(row['outcome'] is None or row['outcome'] in OUTCOMES)
        require(row['result_digest'] is None or sha(row['result_digest']))
        if row['state'] == 'imported':
            require(row['result_digest'] is not None and row['outcome'] is not None
                    and row['finished'] is not None)
        if row['error'] is not None:
            if row['error'] != 'restored':
                error = parsed(row['error'])
                keys(error, ('code', 'message'))
                require(ident(error['code']) and text(error['message'], 500, empty=True))
        if row['usage'] is not None:
            usage = parsed(row['usage'])
            keys(usage, ('input_tokens', 'output_tokens'))
            require(all(v is None or integer(v, 0, 1000000) for v in usage.values()))
    for values in ordinals.values():
        require(sorted(values) == list(range(len(values))))
    queue = list(db.execute('SELECT * FROM consolidation_queue'))
    require(len(queue) <= MAX_QUEUE)
    budgets = list(db.execute('SELECT * FROM consolidation_episode_budget'))
    require(len(budgets) <= 200)
    budget_by_prefix = {}
    for budget in budgets:
        require(type(budget['prefix']) is str and re.fullmatch(r'[a-f0-9]{62}', budget['prefix'])
                and integer(budget['used'], 0, 6) and integer(budget['closed'], 0, 1))
        timestamp(budget['updated'])
        budget_by_prefix[budget['prefix']] = budget
    seen_dedup = set()
    for row in queue:
        require(ident(row['id']) and row['trigger'] in ('source', 'conversation', 'commit', 'daily')
                and sha(row['dedup']) and row['state'] in ('queued', 'claimed', 'completed', 'canceled',
                                                         'failed', 'interrupted', 'paused', 'stale')
                and (row['job'] is None or row['job'] in jobs))
        prefix = row['dedup'][:62]
        budget = budget_by_prefix.get(prefix)
        require(row['dedup'] not in seen_dedup and budget is not None
                and int(row['dedup'][62:], 16) < budget['used'])
        seen_dedup.add(row['dedup'])
        timestamp(row['created'])
    # Chunk bodies are deliberately omitted from logical backups. Validate the
    # ledger's shape and original-source ownership here; restore clears progress
    # and rebuilds passages before any subsequent analysis can advance it.
    for table, cap in (('consolidation_progress', 6144),
                       ('consolidation_passage_progress', 50000)):
        require(db.execute('SELECT count(*) FROM ' + table).fetchone()[0] <= cap)
        for row in db.execute('SELECT * FROM ' + table):
            require(text(row['source'], 160) and not row['source'].startswith('wiki:')
                    and sha(row['revision']) and db.execute(
                        'SELECT 1 FROM sources WHERE id=?', (row['source'],)).fetchone() is not None)
            if table == 'consolidation_passage_progress':
                require(sha(row['chunk']))
            timestamp(row['analyzed'])
    prerequisites = {}
    for row in db.execute('SELECT * FROM consolidation_proposal_metadata'):
        require(row['proposal'] in proposals and row['epistemic_status'] in EPISTEMIC
                and text(row['scope'], 160))
        deps = parsed(proposals[row['proposal']]['dependencies'])
        conflicting = parsed(row['conflicting'])
        require(type(conflicting) is list and len(conflicting) <= 8)
        for item in conflicting:
            anchor(item, citation=True)
            # Imported batch IDs can be renumbered to proposal-local S1..S8.
            require(any(all(dep[key] == item[key] for key in item if key != 'citation') for dep in deps))
        if conflicting:
            require(row['epistemic_status'] == 'disputed')
        needed = parsed(row['prerequisites'])
        require(type(needed) is list and len(needed) <= 20
                and all(ident(key) for key in needed) and len(set(needed)) == len(needed))
        for key in needed:
            require(key != row['proposal'] and key in proposals
                    and proposals[key]['job'] == proposals[row['proposal']]['job'])
        prerequisites[row['proposal']] = needed
    checked = set()
    def walk(key, ancestors):
        require(key not in ancestors)
        if key in checked:
            return
        for other in prerequisites.get(key, []):
            walk(other, ancestors | {key})
        checked.add(key)
    for key in prerequisites:
        walk(key, set())
    for row in db.execute('SELECT * FROM consolidation_claim_metadata'):
        require(row['epistemic_status'] in EPISTEMIC and text(row['scope'], 160)
                and db.execute('SELECT 1 FROM memory_claims WHERE id=?', (row['claim_id'],)).fetchone() is not None)
    settings = get(db, 'consolidation_settings')
    if settings is not None:
        from .knowledge_consolidation_execution import _settings
        try:
            _settings(settings)
            if settings['config'] is not None:
                config(settings['config'])
        except (KeyError, TypeError, ValueError, KnowledgeError) as exc:
            raise KnowledgeError('invalid_consolidation_storage') from exc
