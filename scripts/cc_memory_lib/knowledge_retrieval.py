"""Bounded retrieval routes over original evidence, never generated self-citations."""
import json
import re
from datetime import datetime

from .knowledge_store import KnowledgeError, get
from .knowledge_ranking import tokenize
from .knowledge_wiki import freshness
from .knowledge_work import read_db

INTENTS = ('auto', 'lookup', 'decision', 'work', 'architecture', 'synthesis', 'history')


def options(value, text):
    defaults = dict(graph=True, wiki=True, intent='auto', paths=[], kinds=[], lifecycle='all', before=None)
    if value is None:
        value = {}
    if type(value) is not dict or set(value) - set(defaults):
        raise KnowledgeError('invalid_retrieval_options')
    result = defaults | value
    if (type(result['graph']) is not bool or type(result['wiki']) is not bool
            or result['intent'] not in INTENTS or result['lifecycle'] not in ('all', 'planned', 'current')):
        raise KnowledgeError('invalid_retrieval_options')
    for field in ('paths', 'kinds'):
        entries = result[field]
        if (type(entries) is not list or len(entries) > 20
                or any(type(s) is not str or not s or len(s) > 200 or '\\' in s
                       or s.startswith('/') or ':' in s and field == 'paths'
                       or '..' in s.split('/') for s in entries)):
            raise KnowledgeError('invalid_retrieval_options')
    if result['before'] is not None:
        try:
            date = datetime.fromisoformat(result['before'])
            if date.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise KnowledgeError('invalid_history_cutoff') from None
        result['intent'] = 'history'
        if result['lifecycle'] != 'all':
            raise KnowledgeError('historical_lifecycle_filter_unavailable')
    elif result['intent'] == 'history':
        raise KnowledgeError('history_cutoff_required')
    if result['intent'] == 'auto':
        routes = [('decision', r'why|decision|rationale|perch[eé]|decisione'),
                  ('work', r'block|task|phase|work|blocc|attivit|fase'),
                  ('architecture', r'architecture|as.built|interface|architettura'),
                  ('synthesis', r'summar|synth|overview|sintes|riepilog')]
        result['intent'] = next((name for name, pattern in routes if re.search(pattern, text, re.I)), 'lookup')
    return result


def allowed(source, config):
    if source['deleted'] or source['id'].startswith('wiki:'):
        return False
    if config['paths'] and not any(source['path'] == p or source['path'].startswith(p.rstrip('/') + '/') for p in config['paths']):
        return False
    if config['kinds'] and source['kind'] not in config['kinds']:
        return False
    if config['lifecycle'] != 'all':
        # Only canonical structured states may classify current/planned work.
        try:
            payload = json.loads(source['body'].rsplit('\n```json\n', 1)[1].rsplit('\n```', 1)[0])
            node = payload.get('node', payload)
            state = node.get('lifecycle', node.get('state'))
            state = state.get('state') if isinstance(state, dict) else state
        except (ValueError, IndexError, TypeError, AttributeError):
            return False
        states = ('planned', 'proposed', 'draft', 'backlog') if config['lifecycle'] == 'planned' else ('active', 'in_progress', 'in-progress', 'blocked', 'review', 'accepted', 'completed', 'done')
        if not source['kind'].startswith('dev-') or state not in states:
            return False
    return True


def expand(db, root, text, seeds, config):
    """At most 2 hops, 40 visited originals, 200 edges and 4 wiki summaries."""
    cache, excluded, paths, edges, wiki = {}, [], {}, [], []
    terms = set(tokenize(text))
    def source(identifier):
        if identifier not in cache:
            row = db.execute('SELECT * FROM sources WHERE id=?', (identifier,)).fetchone()
            cache[identifier] = dict(row) if row else None
        return cache[identifier]
    def eligible(identifier):
        row = source(identifier)
        return row is not None and allowed(row, config)
    def reject(identifier, reason):
        if len(excluded) < 40:
            excluded.append({'source': identifier, 'reason': reason})
    frontier = []
    for seed in seeds:
        if eligible(seed) and seed not in paths:
            paths[seed] = []
            frontier.append(seed)
    additions, truncated = [], False
    if config['wiki']:
        matches = []
        # Stream summaries, retain only the four best; never index their prose as originals.
        for row in db.execute("SELECT id,title,body,dependencies FROM wiki WHERE id LIKE 'wiki:%' AND id NOT LIKE 'wiki:draft:%' ORDER BY id"):
            score = len(terms & set(tokenize(row['title'] + ' ' + row['body'])))
            if not score:
                continue
            deps = json.loads(row['dependencies'])
            if freshness(db, deps):
                reject(row['id'], 'stale_wiki')
                continue
            # A summary crossing an explicit filter is excluded as a whole.
            if not deps or any(not eligible(d['source']) for d in deps):
                continue
            matches.append((score, row['id'], deps))
            matches.sort(key=lambda item: (-item[0], item[1]))
            del matches[4:]
        for _, identifier, deps in matches:
            wiki.append(identifier)
            for dep in deps[:8]:
                target = dep['source']
                if target not in paths and len(paths) < 40:
                    paths[target] = [{'kind': 'wiki_original_anchor', 'page': identifier,
                                      'target': target, 'revision': dep['revision']}]
                    additions.append((target, dep, 'wiki'))
                    frontier.append(target)
    if config['graph']:
        reviewed = read_db(db, root)['relations']
        finding_edges = []
        for finding in get(db, 'wiki_tools:findings:v1', []):
            if finding['status'] not in ('conflict', 'different_scope', 'different_version', 'duplicate') or freshness(db, finding['dependencies']):
                continue
            for dep in finding['dependencies'][1:]:
                finding_edges.append({'source': finding['dependencies'][0]['source'], 'target': dep['source'],
                                      'revision': finding['dependencies'][0]['revision'],
                                      'kind': 'wiki_reviewed:' + finding['status']})
        inspected = 0
        for depth in range(2):
            following = []
            for node in frontier:
                candidates = [dict(r) for r in db.execute(
                    'SELECT * FROM edges WHERE source=? OR target=? ORDER BY source,target,kind LIMIT 201', (node, node))]
                candidates += [{'source': r['source'], 'target': r['target'], 'kind': 'human_reviewed:' + r['kind'],
                                'revision': r['source_revision'], 'effective': r['effective']}
                               for r in reviewed if node in (r['source'], r['target'])]
                candidates += [e for e in finding_edges if node in (e['source'], e['target'])]
                if config['intent'] in ('work', 'decision'):
                    candidates.sort(key=lambda e: (not e['kind'].startswith(('human_reviewed:', 'canonical_work:', 'recorded_dev:')), e['kind']))
                for edge in candidates:
                    if inspected >= 200 or len(paths) >= 40:
                        truncated = True
                        break
                    inspected += 1
                    origin, target = source(edge['source']), source(edge['target'])
                    other = edge['target'] if edge['source'] == node else edge['source']
                    if not eligible(edge['source']) or not eligible(edge['target']):
                        reject('(filtered endpoint)', 'unavailable_or_filtered_endpoint')
                        continue
                    if edge['revision'] != origin['revision'] or edge.get('effective') is False:
                        reject(other, 'stale_or_unapproved_relation')
                        continue
                    if not edge['kind'].startswith(('explicit_markdown_reference', 'original_document_link', 'canonical_work:', 'recorded_dev:', 'human_reviewed:', 'wiki_reviewed:')):
                        continue
                    clean = {k: edge[k] for k in ('source', 'target', 'kind', 'revision')}
                    if clean not in edges:
                        edges.append(clean)
                    if other not in paths:
                        paths[other] = paths[node] + [clean]
                        additions.append((other, None, 'graph'))
                        following.append(other)
            frontier = following
    selected = []
    for identifier, dep, signal in additions:
        # Pick one bounded original passage per reached source, favoring query overlap.
        best = None
        for row in db.execute('SELECT id,text,line,end_line FROM chunks WHERE source=? AND revision=? ORDER BY line,id', (identifier, source(identifier)['revision'])):
            score = len(terms & set(tokenize(row['text'])))
            if dep and row['line'] <= dep.get('line', 1) <= row['end_line']:
                score += 1
            candidate = (score, -row['line'], row['id'])
            if best is None or candidate > best:
                best = candidate
        if best:
            selected.append({'id': best[2], 'source': identifier, 'signal': signal, 'path': paths[identifier]})
    return selected, {'intent': config['intent'], 'paths': paths, 'edges': edges, 'wiki': wiki,
                      'excluded': excluded, 'truncated': truncated,
                      'budgets': {'hops': 2, 'sources': 40, 'edges': 200, 'wiki': 4, 'passages': 10}}


def history(db, text, config):
    """Recorded original versions before cutoff; not reconstructed Git membership."""
    terms, results, scanned, truncated = set(tokenize(text)), [], 0, False
    for source in db.execute('SELECT * FROM sources ORDER BY id'):
        if not allowed(source, config):
            continue
        row = db.execute('SELECT * FROM revisions WHERE source=? AND julianday(observed)<=julianday(?) '
                         'ORDER BY julianday(observed) DESC, revision LIMIT 1', (source['id'], config['before'])).fetchone()
        if row is None:
            continue
        scanned += len(row['body'].encode('utf-8'))
        if scanned > 16 * 1024 * 1024:
            truncated = True
            break
        lines = row['body'].splitlines()
        for i, line in enumerate(lines):
            score = len(terms & set(tokenize(line)))
            if score:
                results.append((score, {'source': source['id'], 'path': source['path'], 'title': source['title'],
                    'revision': row['revision'], 'line': i + 1, 'end_line': i + 1,
                    'excerpt': line[:2000], 'kind': source['kind'], 'historical': True,
                    'observed': row['observed']}))
                results.sort(key=lambda r: (-r[0], r[1]['path'], r[1]['line']))
                del results[10:]
    return [dict(item, id='S' + str(i)) for i, (_, item) in enumerate(results, 1)], truncated
