"""Source-bound wiki inspection, reviewable findings and guarded section recovery."""
import difflib
import json
import re

from .knowledge_store import database, get, put, now, KnowledgeError
from .knowledge_sources import digest
from .knowledge_wiki import freshness
from .knowledge_work import read_db

ACTIONS = ('wiki-tools-view', 'wiki-tools-decide', 'wiki-tools-compare',
           'wiki-tools-recover', 'wiki-tools-adopt', 'wiki-tools-propose-finding')
FINDINGS = 'wiki_tools:findings:v1'
HISTORY = 'wiki_tools:sections:v1'
RESOLUTIONS = ('conflict', 'different_scope', 'different_version', 'duplicate', 'dismissed')


def require(condition, error='invalid_wiki_tools_request'):
    if not condition:
        raise KnowledgeError(error)


def validate(db):
    for key in (FINDINGS, HISTORY):
        value = get(db, key, [])
        require(type(value) is list and len(value) <= 100 and
                len(json.dumps(value).encode('utf-8')) <= 512 * 1024, 'wiki_tools_budget')
        for item in value:
            require(type(item) is dict and type(item.get('id')) is str, 'invalid_wiki_tools_storage')
            if key == FINDINGS:
                require(type(item.get('dependencies')) is list and 1 <= len(item['dependencies']) <= 8
                        and item.get('status') in ('pending', *RESOLUTIONS)
                        and type(item.get('history')) is list and len(item['history']) <= 20,
                        'invalid_wiki_tools_storage')
                deps = item['dependencies']
            else:
                require(type(item.get('sections')) is dict, 'invalid_wiki_tools_storage')
                deps = []
                for sections in item['sections'].values():
                    require(type(sections) is dict, 'invalid_wiki_tools_storage')
                    for section in sections.values():
                        require(type(section) is dict and type(section.get('body')) is str
                                and type(section.get('title')) is str and type(section.get('dependencies')) is list,
                                'invalid_wiki_tools_storage')
                        deps.extend(section['dependencies'])
            for dep in deps:
                require(type(dep) is dict and all(type(dep.get(k)) is str for k in ('source', 'path', 'revision', 'excerpt'))
                        and type(dep.get('line')) is int and type(dep.get('end_line')) is int
                        and 0 < dep['line'] <= dep['end_line'], 'invalid_wiki_tools_storage')


def checkpoint(db, sections):
    """Keep bounded immutable section snapshots, including curator publications."""
    history = get(db, HISTORY, [])
    identifier = digest(json.dumps(sections, sort_keys=True))
    if sections and (not history or history[-1]['id'] != identifier):
        history.append({'id': identifier, 'created': now(), 'sections': sections})
        while len(history) > 20 or len(json.dumps(history).encode('utf-8')) > 512 * 1024:
            history.pop(0)
        put(db, HISTORY, history)


def purge(db, source):
    put(db, FINDINGS, [f for f in get(db, FINDINGS, []) if all(d['source'] != source for d in f['dependencies'])])
    put(db, HISTORY, [h for h in get(db, HISTORY, []) if all(d['source'] != source
                         for sections in h['sections'].values() for section in sections.values() for d in section['dependencies'])])


def candidate(kind, deps, explanation):
    return {'id': digest(json.dumps([kind, deps], sort_keys=True)), 'kind': kind,
            'dependencies': deps, 'explanation': explanation, 'status': 'pending',
            'reason': '', 'created': now(), 'history': []}


def diagnose(db, deps):
    """Conservative assertion candidates, never automatic semantic adjudication."""
    fields, bodies, findings = {}, {}, []
    sources = list(dict.fromkeys(d['source'] for d in deps))[:64]
    for identifier in sources:
        row = db.execute('SELECT * FROM sources WHERE id=? AND deleted=0', (identifier,)).fetchone()
        if row is None:
            continue
        def anchor(line, excerpt):
            return dict(source=identifier, path=row['path'], revision=row['revision'], line=line,
                        end_line=line, excerpt=excerpt, citation='S1')
        for number, line in enumerate(row['body'].splitlines()[:200], 1):
            if not 8 <= len(line) <= 500 or line.lstrip().startswith(('#', '>', '|', '```', '[')):
                continue
            norm = ' '.join(line.casefold().split())
            dep = anchor(number, line)
            if norm in bodies and bodies[norm]['source'] != identifier:
                findings.append(candidate('possible_duplicate', [bodies[norm], dep], 'Identical assertion text in different originals; shared wording does not establish duplicate authority.'))
            else:
                bodies[norm] = dep
            match = re.match(r'^\s*([\w][\w .-]{2,60})\s*(?::|\s+is\s+|\s+è\s+)\s*(.{1,200})$', line)
            if match:
                key, value = match[1].strip().casefold(), match[2].strip().casefold()
                previous = fields.get(key)
                if previous and previous[0] != value and previous[1]['source'] != identifier:
                    findings.append(candidate('possible_disagreement', [previous[1], dep],
                        'Same assertion key with different values. Review scope, dates and versions before deciding whether this is a contradiction.'))
                else:
                    fields[key] = value, dep
            if len(findings) >= 40:
                return findings, True
    return findings, len(sources) >= 64


def inspect(db, root, identifier, scan=False):
    validate(db)
    row = db.execute('SELECT * FROM wiki WHERE id=?', (identifier,)).fetchone()
    require(row is not None, 'page_unavailable')
    deps = json.loads(row['dependencies'])
    source_ids = {d['source'] for d in deps}
    findings = get(db, FINDINGS, [])
    limited = False
    if scan:
        proposed, limited = diagnose(db, deps)
        for finding in proposed:
            if not any(f['id'] == finding['id'] for f in findings):
                if len(findings) == 100:
                    limited = True
                    break
                findings.append(finding)
        put(db, FINDINGS, findings)
        validate(db)
    backlinks = []
    for page in db.execute('SELECT id,title,revision,dependencies FROM wiki ORDER BY id'):
        anchors = json.loads(page['dependencies'])
        shared = source_ids & {d['source'] for d in anchors}
        if shared and page['id'] != identifier and len(backlinks) < 80:
            backlinks.append({'id': page['id'], 'title': page['title'], 'revision': page['revision'],
                              'sources': sorted(shared), 'stale': bool(freshness(db, anchors))})
    relations = [r for r in read_db(db, root)['relations'] if source_ids & {r['source'], r['target']}]
    history = [{'snapshot': h['id'], 'created': h['created'], 'page_type': page_type,
                'key': key, 'title': section['title'], 'body': section['body'],
                'issues': freshness(db, section['dependencies'])}
               for h in get(db, HISTORY, []) for page_type, sections in h['sections'].items()
               if identifier == 'wiki:review:' + page_type for key, section in sections.items()]
    entries = [{**f, 'issues': freshness(db, f['dependencies'])} for f in findings
               if any(d['source'] in source_ids for d in f['dependencies'])]
    snapshot = digest(json.dumps([row['revision'], get(db, 'generation'), get(db, 'wiki_review:epoch:v1', 0), findings], sort_keys=True))
    result = {'page': identifier, 'revision': row['revision'], 'snapshot': snapshot,
              'sources': [dict(d, issues=freshness(db, [d])) for d in deps[:160]],
              'backlinks': backlinks, 'relations': relations, 'findings': entries,
              'history': history[-40:], 'truncated': limited or len(deps) > 160 or len(history) > 40,
              'notice': 'Candidates require review. Different versions or scopes are not automatically conflicts. Reviewed prose remains protected; recovery creates a new proposal.'}
    require(len(json.dumps(result).encode('utf-8')) < 900000, 'wiki_tools_budget')
    return result


def dispatch(root, action, value):
    require(action in ACTIONS and type(value) is dict and type(value.get('page')) is str and len(value['page']) <= 180)
    proposal = None
    with database(root) as db:
        require(db is not None and not get(db, 'needs_reconcile', True), 'wiki_context_changed')
        with db:
            view = inspect(db, root, value['page'], action == 'wiki-tools-view')
            if action == 'wiki-tools-view':
                require(set(value) == {'page'})
                return view
            if action == 'wiki-tools-compare':
                require(set(value) == {'page', 'revision'})
                previous = db.execute('SELECT body,dependencies FROM wiki_history WHERE id=? AND revision=?', (value['page'], value['revision'])).fetchone()
                current = db.execute('SELECT body FROM wiki WHERE id=?', (value['page'],)).fetchone()
                require(previous is not None, 'page_revision_unavailable')
                diff = list(difflib.unified_diff(previous['body'].splitlines(), current['body'].splitlines(), fromfile='selected revision', tofile='current revision', lineterm=''))
                return {**view, 'comparison': {'from': value['revision'], 'to': view['revision'],
                        'diff': '\n'.join(diff)[:60000], 'truncated': len('\n'.join(diff)) > 60000,
                        'issues': freshness(db, json.loads(previous['dependencies']))}}
            if action == 'wiki-tools-decide':
                require(set(value) == {'page', 'snapshot', 'id', 'status', 'reason'} and value['snapshot'] == view['snapshot'], 'wiki_inspection_changed')
                require(value['status'] in RESOLUTIONS and type(value['reason']) is str and 5 <= len(value['reason']) <= 1000)
                findings = get(db, FINDINGS, [])
                item = next((f for f in findings if f['id'] == value['id']), None)
                require(item is not None and item['id'] in {f['id'] for f in view['findings']}, 'finding_unavailable')
                require(not freshness(db, item['dependencies']), 'wiki_review_source_changed')
                require(len(item['history']) < 20, 'wiki_tools_budget')
                item['history'].append({k: item[k] for k in ('status', 'reason')})
                item.update(status=value['status'], reason=value['reason'], reviewed=now())
                put(db, FINDINGS, findings)
                return inspect(db, root, value['page'])
            if action == 'wiki-tools-propose-finding':
                require(set(value) == {'page', 'citations', 'reason'})
                from .knowledge_wiki_review import _validated_dependencies
                deps = _validated_dependencies(db, value['citations'])
                require(2 <= len(deps) <= 8 and type(value['reason']) is str and 5 <= len(value['reason']) <= 1000)
                source_ids = {d['source'] for d in view['sources']}
                require(any(d['source'] in source_ids for d in deps))
                findings = get(db, FINDINGS, [])
                item = candidate('reviewer_suggestion', deps, value['reason'])
                require(len(findings) < 100, 'wiki_tools_budget')
                if not any(f['id'] == item['id'] for f in findings):
                    put(db, FINDINGS, findings + [item])
                return inspect(db, root, value['page'])
            if action == 'wiki-tools-recover':
                require(set(value) == {'page', 'snapshot', 'key', 'base_revision'} and value['base_revision'] == view['revision'], 'wiki_review_page_changed')
                item = next((h for h in get(db, HISTORY, []) if h['id'] == value['snapshot']), None)
                page_type = value['page'].removeprefix('wiki:review:')
                section = item['sections'].get(page_type, {}).get(value['key']) if item else None
                require(section is not None, 'section_history_unavailable')
                proposal = {'page_type': page_type, 'key': value['key'], 'title': section['title'], 'body': section['body'],
                            'citations': section['dependencies'], 'base_revision': view['revision']}
            if action == 'wiki-tools-adopt':
                require(set(value) == {'page', 'draft', 'key', 'base_revision'} and value['base_revision'] == view['revision'], 'wiki_review_page_changed')
                require(type(value['draft']) is str and value['draft'].startswith('wiki:draft:'))
                draft = db.execute('SELECT * FROM wiki WHERE id=?', (value['draft'],)).fetchone()
                require(draft is not None, 'page_unavailable')
                body = draft['body'].split('> Unreviewed AI synthesis; never independent evidence.\n\n', 1)[-1].rsplit('\n\n## Generator\n\n', 1)[0]
                deps = json.loads(draft['dependencies'])
                used = set(re.findall(r'\[(S\d+)\]', body))
                deps = [d for d in deps if d['citation'] in used]
                mapping = {d['citation']: 'S' + str(i) for i, d in enumerate(deps, 1)}
                require(used <= set(mapping), 'invalid_wiki_review_citation')
                body = re.sub(r'\[(S\d+)\]', lambda m: '[' + mapping[m[1]] + ']', body)
                proposal = {'page_type': value['page'].removeprefix('wiki:review:'), 'key': value['key'],
                            'title': draft['title'][:120], 'body': body, 'citations': deps, 'base_revision': view['revision']}
    from .knowledge_wiki_review import dispatch as review
    # Fresh page/review epoch and source checks are repeated under the writer lock.
    result = review(root, 'wiki-review-propose', proposal)
    with database(root) as db:
        return {**inspect(db, root, value['page']), 'proposal': result}
