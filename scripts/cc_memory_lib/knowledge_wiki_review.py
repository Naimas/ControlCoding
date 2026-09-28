"""Bounded, source-anchored wiki pages with explicit human section review.

The compiler quotes and categorizes source lines. It never promotes a wiki page
to an original source and never rewrites accepted human prose.
"""
import json
import re
from uuid import uuid4

from .knowledge_store import KnowledgeError, database, get, now, put

PAGE_TYPES = {
    'overview': ('Project overview', None),
    'workflow': ('Workflows', r'workflow|process|procedure|runbook|\bsteps?\b'),
    'timeline': ('Timeline', r'\b20\d\d-\d\d-\d\d\b|milestone|release|decision'),
    'glossary': ('Glossary', r'glossary|definition|terminology|\bterms?\b'),
    'open_questions': ('Open questions', r'\?|\btodo\b|open question|unresolved|unknown'),
    'architecture': ('Architecture and as-built', r'architect|as.built|interface|module|service|design'),
    'concepts': ('Concepts', r'concept|definition|means|represents'),
    'requirements': ('Requirements', r'require|must|shall|acceptance|requisit'),
    'decisions': ('Decisions and rationale', r'decision|because|rationale|decis|because'),
    'sources': ('Source inventory', None),
    'outputs': ('Outputs and evidence', r'output|deliverable|evidence|receipt|result|proof'),
}
STATE = 'wiki_review:sections:v1'
PROPOSALS = 'wiki_review:proposals:v1'
ENABLED = 'wiki_review:enabled:v1'
EPOCH = 'wiki_review:epoch:v1'
MAX_SECTIONS = 20
MAX_PROPOSALS = 100
MAX_REVIEW_BYTES = 384 * 1024


def _check_budget(approved, proposals):
    """Bound persisted review content below the desktop helper's 1 MiB reply."""
    payload = json.dumps({'sections': approved, 'proposals': proposals},
                         ensure_ascii=True, separators=(',', ':'))
    if len(payload.encode('utf-8')) > MAX_REVIEW_BYTES:
        raise KnowledgeError('wiki_review_budget')


def validate_budget(db):
    """Validate retained review state before compilation or archive restore."""
    from .knowledge_wiki_tools import validate
    validate(db)
    approved = get(db, STATE, {})
    proposals = get(db, PROPOSALS, [])
    _check_budget(approved, proposals)
    return approved, proposals


def _page_id(page_type):
    if type(page_type) is not str or page_type not in PAGE_TYPES:
        raise KnowledgeError('invalid_wiki_page_type')
    return 'wiki:review:' + page_type


def _source_line(source, pattern):
    lines = source['body'].splitlines()
    if not lines:
        return None
    if pattern:
        index = next((i for i, line in enumerate(lines) if re.search(pattern, line, re.I)), None)
        if index is None:
            return None
    else:
        index = next((i for i, line in enumerate(lines) if line.strip() and not line.startswith('#')), None)
        if index is None:
            return None
    excerpt = lines[index][:500]
    if not excerpt.strip():
        return None
    return {'source': source['id'], 'path': source['path'], 'revision': source['revision'],
            'line': index + 1, 'end_line': index + 1, 'excerpt': excerpt}


def compile_pages(db, sources, stamp):
    """Refresh generated entries while retaining every approved section verbatim."""
    from .knowledge_wiki import freshness, store_page

    approved, _ = validate_budget(db)
    if not get(db, ENABLED, False):
        return
    originals = [s for s in sorted(sources, key=lambda s: s['path'])
                 if not s['id'].startswith('wiki:')]
    for page_type, (title, pattern) in PAGE_TYPES.items():
        identifier = _page_id(page_type)
        body = '# ' + title + '\n\n> Derived source index. Entries are quoted, not synthesized claims.\n'
        dependencies = []
        for source in originals:
            dep = _source_line(source, pattern)
            if dep is None:
                continue
            dep['citation'] = 'G' + str(len(dependencies) + 1)
            dependencies.append(dep)
            body += '\n## ' + source['title'].replace('\n', ' ')[:180] + '\n\n'
            body += '> ' + dep['excerpt'].replace('\n', '\n> ') + '\n\n'
            body += '[' + dep['citation'] + '] `' + dep['path'] + '` line ' + str(dep['line'])
            body += ' | revision `' + dep['revision'] + '`\n'
            if len(dependencies) == 32:
                break
        if not dependencies:
            body += '\nNo matching source lines in the current library.\n'
        sections = approved.get(page_type, {})
        if sections:
            body += '\n## Human-reviewed sections\n'
        for key, section in sorted(sections.items()):
            issues = freshness(db, section['dependencies'])
            body += '\n### ' + section['title'] + '\n\n'
            if issues:
                body += '> Sources changed or were removed. Review this section before relying on it.\n\n'
            body += section['body'] + '\n\n'
            for dep in section['dependencies']:
                body += '- [' + dep['citation'] + '] `' + dep['path'] + '` lines '
                body += str(dep['line']) + '-' + str(dep['end_line']) + ' | revision `' + dep['revision'] + '`\n'
            dependencies.extend(section['dependencies'])
        store_page(db, identifier, title, body, dependencies, stamp)


def _validated_dependencies(db, citations):
    if type(citations) is not list or not 0 < len(citations) <= 8:
        raise KnowledgeError('invalid_wiki_review_citation')
    deps = []
    for number, item in enumerate(citations, 1):
        if type(item) is not dict or not {'source', 'path', 'revision', 'line', 'end_line', 'excerpt'} <= set(item):
            raise KnowledgeError('invalid_wiki_review_citation')
        if (not isinstance(item['source'], str) or item['source'].startswith('wiki:')
                or not isinstance(item['path'], str) or not isinstance(item['revision'], str)
                or type(item['line']) is not int or type(item['end_line']) is not int
                or not isinstance(item['excerpt'], str) or not item['excerpt']
                or item['line'] < 1 or item['end_line'] < item['line']):
            raise KnowledgeError('invalid_wiki_review_citation')
        dep = {k: item[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')}
        dep['citation'] = 'S' + str(number)
        deps.append(dep)
    from .knowledge_wiki import freshness
    if freshness(db, deps):
        raise KnowledgeError('wiki_review_source_changed')
    return deps


def _view(db):
    from .knowledge_wiki import freshness
    approved, stored_proposals = validate_budget(db)
    pages = []
    for page_type, (title, _) in PAGE_TYPES.items():
        row = db.execute('SELECT revision FROM wiki WHERE id=?', (_page_id(page_type),)).fetchone()
        pages.append({'type': page_type, 'id': _page_id(page_type), 'title': title,
                      'revision': row['revision'] if row else None,
                      'sections': [{**section, 'key': key,
                                    'issues': freshness(db, section['dependencies'])}
                                   for key, section in sorted(approved.get(page_type, {}).items())]})
    revisions = {item['type']: item['revision'] for item in pages}
    proposals = []
    for proposal in stored_proposals[-MAX_PROPOSALS:]:
        issues = freshness(db, proposal['dependencies'])
        if (proposal['base_revision'] != revisions[proposal['page_type']]
                or proposal.get('base_review_epoch', 0) != get(db, EPOCH, 0)):
            issues.append({'code': 'page_changed'})
        proposals.append({**proposal, 'issues': issues,
                          'eligible': proposal['status'] == 'pending' and not issues
                          and not get(db, 'needs_reconcile', True)})
    result = {'pages': pages, 'proposals': proposals,
              'notice': 'Generated entries are deterministic source excerpts. Human-approved prose requires review when its sources change.'}
    if len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > 900 * 1024:
        raise KnowledgeError('wiki_review_budget')
    return result


def commit_sections(db, approved, proposals):
    """Publish validated section changes inside the caller's transaction."""
    _check_budget(approved, proposals)
    if any(key not in PAGE_TYPES or len(sections) > MAX_SECTIONS
           for key, sections in approved.items()):
        raise KnowledgeError('wiki_review_section_limit')
    from .knowledge_wiki_tools import checkpoint
    checkpoint(db, get(db, STATE, {}))
    put(db, STATE, approved)
    put(db, EPOCH, get(db, EPOCH, 0) + 1)
    put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    current = [dict(r) for r in db.execute('SELECT * FROM sources WHERE deleted=0 ORDER BY path')]
    compile_pages(db, current, now())


def dispatch(root, action, value=None):
    if action not in ('wiki-review-view', 'wiki-review-propose', 'wiki-review-decide'):
        raise KnowledgeError('invalid_wiki_review_action')
    with database(root) as db:
        if db is None:
            raise KnowledgeError('wiki_review_context_changed')
        if action == 'wiki-review-view':
            if value is not None:
                raise KnowledgeError('invalid_wiki_review_request')
            if not get(db, ENABLED, False):
                if get(db, 'needs_reconcile', True):
                    raise KnowledgeError('wiki_review_context_changed')
                with db:
                    put(db, ENABLED, True)
                    put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
                    current = [dict(r) for r in db.execute('SELECT * FROM sources WHERE deleted=0 ORDER BY path')]
                    compile_pages(db, current, now())
            return _view(db)
        if get(db, 'needs_reconcile', True):
            raise KnowledgeError('wiki_review_context_changed')
        with db:
            proposals = get(db, PROPOSALS, [])
            if action == 'wiki-review-propose':
                if type(value) is not dict or set(value) != {'page_type', 'key', 'title', 'body', 'citations', 'base_revision'}:
                    raise KnowledgeError('invalid_wiki_review_request')
                page_type = value['page_type']
                identifier = _page_id(page_type)
                row = db.execute('SELECT revision FROM wiki WHERE id=?', (identifier,)).fetchone()
                if row is None or value['base_revision'] != row['revision']:
                    raise KnowledgeError('wiki_review_page_changed')
                key, title, body = value['key'], value['title'], value['body']
                if (type(key) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', key)
                        or type(title) is not str or not 0 < len(title) <= 120 or '\n' in title
                        or type(body) is not str or not 0 < len(body) <= 8000
                        or len(json.dumps(value)) > 20000):
                    raise KnowledgeError('invalid_wiki_review_request')
                approved = get(db, STATE, {})
                if key not in approved.get(page_type, {}) and len(approved.get(page_type, {})) >= MAX_SECTIONS:
                    raise KnowledgeError('wiki_review_section_limit')
                deps = _validated_dependencies(db, value['citations'])
                used = set(re.findall(r'\[(S\d+)\]', body))
                if not used or used - {d['citation'] for d in deps}:
                    raise KnowledgeError('invalid_wiki_review_citation')
                for paragraph in re.split(r'\n\s*\n', body):
                    prose = '\n'.join(line for line in paragraph.splitlines() if line.strip() and not line.startswith('#'))
                    if prose and not re.search(r'\[S\d+\]', prose):
                        raise KnowledgeError('wiki_uncited_paragraph')
                proposal = {'id': uuid4().hex, 'page_type': page_type, 'key': key,
                            'title': title, 'body': body, 'dependencies': deps,
                            'base_revision': row['revision'], 'base_review_epoch': get(db, EPOCH, 0),
                            'status': 'pending',
                            'created': now(), 'decided': None}
                if len(proposals) >= MAX_PROPOSALS:
                    # Keep pending reviews and the newest terminal decisions.
                    terminal = next((i for i, p in enumerate(proposals) if p['status'] != 'pending'), None)
                    if terminal is None:
                        raise KnowledgeError('wiki_review_proposal_limit')
                    proposals.pop(terminal)
                proposals.append(proposal)
                _check_budget(approved, proposals)
                put(db, PROPOSALS, proposals)
                return proposal
            if type(value) is not dict or set(value) != {'id', 'decision'} or value['decision'] not in ('accept', 'reject'):
                raise KnowledgeError('invalid_wiki_review_request')
            proposal = next((p for p in proposals if p['id'] == value['id']), None)
            if proposal is None or proposal['status'] != 'pending':
                raise KnowledgeError('wiki_review_proposal_unavailable')
            if value['decision'] == 'accept':
                row = db.execute('SELECT revision FROM wiki WHERE id=?', (_page_id(proposal['page_type']),)).fetchone()
                if (row is None or row['revision'] != proposal['base_revision']
                        or get(db, EPOCH, 0) != proposal['base_review_epoch']):
                    raise KnowledgeError('wiki_review_page_changed')
                from .knowledge_wiki import freshness
                if freshness(db, proposal['dependencies']):
                    raise KnowledgeError('wiki_review_source_changed')
                approved = get(db, STATE, {})
                approved.setdefault(proposal['page_type'], {})[proposal['key']] = {
                    k: proposal[k] for k in ('title', 'body', 'dependencies')}
                approved[proposal['page_type']][proposal['key']]['approved'] = now()
                accepted = [{**item, 'status': 'accepted', 'decided': now()} if item['id'] == proposal['id'] else item
                            for item in proposals]
                commit_sections(db, approved, accepted)
            proposal['status'] = 'accepted' if value['decision'] == 'accept' else 'rejected'
            proposal['decided'] = now()
            put(db, PROPOSALS, proposals)
            return proposal


def purge_source(db, source):
    """Erase retained derived text when a private source is forgotten."""
    from .knowledge_wiki_tools import purge
    purge(db, source)
    approved = get(db, STATE, {})
    for sections in approved.values():
        for key, section in list(sections.items()):
            if any(d['source'] == source for d in section['dependencies']):
                del sections[key]
    put(db, STATE, approved)
    put(db, PROPOSALS, [p for p in get(db, PROPOSALS, [])
                        if not any(d['source'] == source for d in p['dependencies'])])
