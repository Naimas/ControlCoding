"""Semantic shape checks for version-two logical archives.

These checks run before restore creates a target. Source freshness is deliberately
left to reconciliation because a restored archive has a different project path.
"""
import hashlib
import json
import re

from .knowledge_store import KnowledgeError


JOB_STATES = frozenset(('ready', 'partial', 'published', 'rejected', 'stale', 'purged'))
V3_JOB_STATES = JOB_STATES | frozenset(('queued', 'waiting_manual', 'running', 'paused', 'canceled', 'failed', 'interrupted', 'analyzed'))
PROPOSAL_STATES = frozenset(('pending', 'accepted', 'rejected', 'stale'))
CLAIM_STATES = frozenset(('approved', 'superseded', 'stale'))
KINDS = frozenset(('summary', 'decision_summary', 'lesson', 'preference', 'open_question'))
PAGES = frozenset(('overview', 'workflow', 'timeline', 'glossary', 'open_questions'))
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
SECTION_KEY = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
SHA256 = re.compile(r'[a-f0-9]{64}\Z')
ANCHOR_FIELDS = frozenset(('source', 'path', 'revision', 'line', 'end_line', 'excerpt'))


def require(condition):
    if not condition:
        raise KnowledgeError('invalid_consolidation_storage')


def keys(value, expected, optional=()):
    require(type(value) is dict and set(expected) <= set(value)
            and set(value) <= set(expected) | set(optional))


def ident(value):
    return type(value) is str and IDENTIFIER.fullmatch(value) is not None


def text(value, limit=4096, empty=False):
    return type(value) is str and len(value) <= limit and (empty or bool(value.strip()))


def nonnegative(value):
    return type(value) is int and value >= 0


def target(value):
    if type(value) is not str or value.count('/') != 1:
        return False
    page, section = value.split('/')
    return page in PAGES and SECTION_KEY.fullmatch(section) is not None


def claim_id(value):
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return 'memory:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def parsed(value):
    require(type(value) is str)
    try:
        return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError) as exc:
        raise KnowledgeError('invalid_consolidation_storage') from exc


def anchor(value, citation=False, title=False):
    expected = ANCHOR_FIELDS | ({'citation'} if citation else set()) | ({'title'} if title else set())
    keys(value, expected)
    require(text(value['source'], 160) and not value['source'].startswith('wiki:'))
    require(text(value['path'], 1024) and text(value['revision'], 160)
            and nonnegative(value['line']) and value['line'] >= 1
            and nonnegative(value['end_line']) and value['end_line'] >= value['line']
            and text(value['excerpt'], 4096))
    if citation:
        require(type(value['citation']) is str and re.fullmatch(r'S[1-8]', value['citation']))
    if title:
        require(text(value['title'], 256))


def dependencies(value, require_items=True):
    require(type(value) is list and len(value) <= 8 and (value or not require_items))
    for number, item in enumerate(value, 1):
        anchor(item, citation=True)
        require(item['citation'] == 'S' + str(number))


def section(value, target_name, require_claim=False):
    keys(value, ('title', 'body', 'dependencies', 'approved'), ('claim_id',))
    require(text(value['title'], 120) and text(value['body'], 8000)
            and text(value['approved'], 80))
    dependencies(value['dependencies'])
    if require_claim:
        require(value.get('claim_id') == claim_id(target_name))
    elif 'claim_id' in value:
        require(value['claim_id'] == claim_id(target_name))


def stored_claim(value):
    keys(value, ('id', 'target', 'kind', 'title', 'body', 'revision',
                 'dependencies', 'state', 'updated'))
    require(target(value['target']) and value['id'] == claim_id(value['target'])
            and value['kind'] in KINDS and text(value['title'], 120)
            and text(value['body'], 8000) and text(value['revision'], 80)
            and value['state'] in CLAIM_STATES and text(value['updated'], 80))
    dependencies(parsed(value['dependencies']))


def receipt(value, proposals):
    keys(value, ('request_id', 'decision', 'patches', 'at', 'cumulative'), ('undone',))
    require(ident(value['request_id']) and value['decision'] == 'accept'
            and value['cumulative'] is True and text(value['at'], 80)
            and type(value['patches']) is list and 0 < len(value['patches']) <= 20)
    if 'undone' in value:
        require(type(value['undone']) is bool)
    seen = set()
    for patch in value['patches']:
        keys(patch, ('target', 'before', 'after', 'proposal', 'kind', 'before_claim'))
        require(target(patch['target']) and patch['target'] not in seen
                and ident(patch['proposal']) and patch['proposal'] in proposals
                and proposals[patch['proposal']]['target'] == patch['target']
                and patch['kind'] in KINDS)
        seen.add(patch['target'])
        require(patch['before'] is None or type(patch['before']) is dict)
        if patch['before'] is not None:
            section(patch['before'], patch['target'])
        section(patch['after'], patch['target'], require_claim=True)
        require(patch['before_claim'] is None or type(patch['before_claim']) is dict)
        if patch['before_claim'] is not None:
            stored_claim(patch['before_claim'])
            require(patch['before_claim']['target'] == patch['target'])


def validate(db):
    """Reject inconsistent v2 rows before logical backup or target restore."""
    jobs = {row['id']: dict(row) for row in db.execute('SELECT * FROM consolidation_jobs')}
    require(len(jobs) <= 20)
    inputs = {}
    for row in db.execute('SELECT * FROM consolidation_inputs'):
        require(row['job'] in jobs and text(row['source'], 160) and text(row['revision'], 160))
        inputs.setdefault(row['job'], {})[row['source']] = row['revision']
    proposals = {}
    for row in db.execute('SELECT * FROM consolidation_proposals'):
        require(ident(row['id']) and row['job'] in jobs and target(row['target'])
                and row['kind'] in KINDS and row['status'] in PROPOSAL_STATES
                and nonnegative(row['ordinal']) and text(row['title'], 120)
                and text(row['body'], 3000) and text(row['reason'], 1000)
                and text(row['created'], 80)
                and (row['decided'] is None or text(row['decided'], 80))
                and (row['base_revision'] is None or text(row['base_revision'], 160)))
        deps = parsed(row['dependencies'])
        dependencies(deps)
        require(all(inputs.get(row['job'], {}).get(dep['source']) == dep['revision'] for dep in deps))
        require(set(re.findall(r'\[(S\d+)\]', row['body'])) <= {dep['citation'] for dep in deps}
                and re.search(r'\[S\d+\]', row['body']) is not None)
        proposals[row['id']] = dict(row)
    for job_id, row in jobs.items():
        states = V3_JOB_STATES if db.execute('PRAGMA user_version').fetchone()[0] == 3 else JOB_STATES
        require(ident(job_id) and row['state'] in states
                and text(row['created'], 80) and text(row['updated'], 80))
        binding, manifest = parsed(row['binding']), parsed(row['manifest'])
        if row['state'] == 'purged':
            require(binding == {})
        else:
            keys(binding, ('project', 'generation', 'head', 'policy', 'privacy_epoch', 'review_epoch'))
            require(text(binding['project'], 2048) and nonnegative(binding['generation'])
                    and (binding['head'] is None or text(binding['head'], 160))
                    and type(binding['policy']) is str and SHA256.fullmatch(binding['policy'])
                    and nonnegative(binding['privacy_epoch']) and nonnegative(binding['review_epoch']))
        version = db.execute('PRAGMA user_version').fetchone()[0]
        keys(manifest, ('title', 'sources', 'count', 'total', 'deferred', 'before'),
             ('prior_sources',) if version == 3 else ())
        require(text(manifest['title'], 120) and type(manifest['sources']) is list
                and len(manifest['sources']) <= 20 and nonnegative(manifest['count'])
                and nonnegative(manifest['total']) and nonnegative(manifest['deferred'])
                and manifest['count'] == len(manifest['sources'])
                and manifest['deferred'] == manifest['total'] - manifest['count']
                and type(manifest['before']) is dict)
        manifest_sources = {}
        for item in manifest['sources']:
            anchor(item, title=True)
            require(item['source'] not in manifest_sources)
            manifest_sources[item['source']] = item['revision']
            require(db.execute('SELECT 1 FROM sources WHERE id=?', (item['source'],)).fetchone() is not None)
        if version == 3:
            prior_sources = manifest.get('prior_sources', [])
            require(type(prior_sources) is list and len(prior_sources) <= 2
                    and len(manifest['sources']) + len(prior_sources) <= 8)
            for item in prior_sources:
                anchor(item, title=True)
                require(manifest_sources.get(item['source'], item['revision']) == item['revision'])
                manifest_sources[item['source']] = item['revision']
                require(db.execute('SELECT 1 FROM sources WHERE id=?', (item['source'],)).fetchone() is not None)
        require(inputs.get(job_id, {}) == manifest_sources)
        own = {key: item for key, item in proposals.items() if item['job'] == job_id}
        require(len(own) <= 20 and {item['ordinal'] for item in own.values()} == set(range(len(own))))
        require(set(manifest['before']) == set(own))
        for proposal_id, before in manifest['before'].items():
            if before is not None:
                section(before, own[proposal_id]['target'])
        size = sum(len(str(v).encode('utf-8')) for v in row.values() if v is not None)
        for item in own.values():
            size += sum(len(str(v).encode('utf-8')) for v in item.values() if v is not None)
        require(size <= 128 * 1024)
        if row['receipt'] is not None:
            receipt(parsed(row['receipt']), own)
        if row['error'] is not None:
            require(type(parsed(row['error'])) is dict)
        if row['state'] == 'purged':
            require(not own and not inputs.get(job_id) and row['receipt'] is None)
    for table in ('memory_claims', 'memory_claim_revisions'):
        for row in db.execute('SELECT * FROM ' + table):
            stored_claim(dict(row))
    request_ids = set()
    for row in db.execute('SELECT * FROM consolidation_events'):
        require(row['job'] in jobs and row['kind'] in ('accept', 'reject', 'undo')
                and text(row['created'], 80))
        event = parsed(row['payload'])
        keys(event, ('request_id', 'operation'), ('receipt',))
        require(ident(event['request_id']) and (row['job'], event['request_id']) not in request_ids)
        request_ids.add((row['job'], event['request_id']))
        operation = event['operation']
        if row['kind'] == 'undo':
            keys(operation, ('action',))
        else:
            keys(operation, ('action', 'ids'))
            require(type(operation['ids']) is list and 0 < len(operation['ids']) <= 20
                    and len(set(operation['ids'])) == len(operation['ids'])
                    and all(ident(key) and key in proposals and proposals[key]['job'] == row['job']
                            for key in operation['ids']))
        require(operation['action'] == row['kind'])
        if 'receipt' in event:
            receipt(event['receipt'], {key: proposal for key, proposal in proposals.items()
                                       if proposal['job'] == row['job']})
