"""Bounded approved-memory snapshot with hydrated original source anchors."""
import json
import re

from .knowledge_consolidation import digest
from .knowledge_store import get
from .knowledge_wiki import freshness
from . import knowledge_wiki_review as review

STOP = frozenset(('about', 'after', 'before', 'claim', 'claims', 'current',
                  'derived', 'evidence', 'original', 'project', 'recorded',
                  'review', 'selected', 'source', 'sources', 'summary', 'this',
                  'with', 'wiki', 'from', 'that', 'have', 'will', 'would'))
MAX_ANCHORS = 8
MAX_ANCHOR_CHARS = 6000
MAX_CONTEXT_CHARS = 2000


def serialized_chars(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _words(value):
    return {word for word in re.findall(r'[\w-]{4,}', value.casefold()) if word not in STOP}


def _key(value):
    return tuple(value[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt'))


def _current_claims(db, selected):
    approved = get(db, review.STATE, {})
    topic = _words(' '.join(item['path'] + ' ' + item['title'] + ' ' + item['excerpt']
                            for item in selected))
    selected_sources = {item['source'] for item in selected}
    ranked = []
    for page, sections in sorted(approved.items()):
        if type(sections) is not dict:
            continue
        for section_key, section in sorted(sections.items()):
            if type(section) is not dict or type(section.get('claim_id')) is not str:
                continue
            row = db.execute('SELECT * FROM memory_claims WHERE id=? AND state=?',
                             (section['claim_id'], 'approved')).fetchone()
            if row is None or row['target'] != page + '/' + section_key:
                continue
            try:
                deps = json.loads(row['dependencies'])
            except (ValueError, TypeError):
                continue
            if (section.get('claim_id') != row['id']
                or digest(section) != row['revision']
                or section.get('title') != row['title'] or section.get('body') != row['body']
                or section.get('dependencies') != deps or freshness(db, deps)):
                continue
            if db.execute('SELECT 1 FROM memory_claim_revisions WHERE id=? AND revision=?',
                          (row['id'], row['revision'])).fetchone() is None:
                continue
            overlap = len(topic & _words(row['target'] + ' ' + row['title'] + ' ' + row['body']))
            shared = sum(dep['source'] in selected_sources for dep in deps)
            if not overlap and not shared:
                continue
            ranked.append((shared * 10 + overlap, row['target'], dict(row), deps))
    return sorted(ranked, key=lambda item: (-item[0], item[1]))


def _hydrate(db, dep):
    row = db.execute('SELECT id,path,title,revision,body,deleted FROM sources WHERE id=?',
                     (dep['source'],)).fetchone()
    if (row is None or row['deleted'] or row['path'] != dep['path']
            or row['revision'] != dep['revision'] or row['id'].startswith('wiki:')):
        return None
    lines = row['body'].splitlines()
    if not 1 <= dep['line'] <= dep['end_line'] <= max(1, len(lines)):
        return None
    content = '\n'.join(lines[dep['line'] - 1:dep['end_line']])
    if dep['excerpt'] not in content:
        from .knowledge_sources import passages
        original = {'id': dep['source'], 'revision': dep['revision'], 'body': row['body']}
        if not any(part['line'] == dep['line'] and part['end_line'] == dep['end_line']
                   and part['text'] == dep['excerpt'] for part in passages(original)):
            return None
    return {**{k: dep[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')},
            'title': row['title'][:256]}


def select(db, selected):
    """Return complete current claims and any additional original anchors."""
    inputs = [{'evidence_id': 'S' + str(i), **item} for i, item in enumerate(selected, 1)]
    evidence = list(inputs)
    indexed = {_key(item): item['evidence_id'] for item in evidence}
    prior, claims = [], []
    for _, _, row, deps in _current_claims(db, selected):
        if len(claims) >= 2:
            break
        additions, ids, mapping = [], [], {}
        valid = True
        for dep in deps:
            anchor = _hydrate(db, dep)
            if anchor is None:
                valid = False
                break
            key = _key(anchor)
            found = indexed.get(key)
            if found is None:
                found = next((item['evidence_id'] for item in additions if _key(item) == key), None)
            if found is None:
                if len(evidence) + len(additions) >= MAX_ANCHORS:
                    valid = False
                    break
                found = 'S' + str(len(evidence) + len(additions) + 1)
                additions.append({'evidence_id': found, **anchor})
            ids.append(found)
            mapping[dep['citation']] = found
        if not valid:
            continue
        if len(prior) + len(additions) > 2:
            continue
        combined = evidence + additions
        if serialized_chars(combined) > MAX_ANCHOR_CHARS:
            continue
        refs = set(re.findall(r'\[(S\d+)\]', row['body']))
        if not refs <= set(mapping):
            continue
        body = re.sub(r'\[(S\d+)\]', lambda match: '[' + mapping[match.group(1)] + ']', row['body'])
        metadata = db.execute('SELECT epistemic_status,scope FROM consolidation_claim_metadata WHERE claim_id=?',
                              (row['id'],)).fetchone()
        ids = list(dict.fromkeys(ids))
        claim = {'id': row['id'], 'target': row['target'], 'revision': row['revision'],
                 'title': row['title'], 'body': body, 'status': 'approved',
                 'epistemic_status': metadata['epistemic_status'] if metadata else 'unknown',
                 'scope': metadata['scope'] if metadata else 'project', 'evidence_ids': ids}
        if serialized_chars(claims + [claim]) > MAX_CONTEXT_CHARS:
            continue
        for item in additions:
            indexed[_key(item)] = item['evidence_id']
        evidence.extend(additions)
        prior.extend(additions)
        claims.append(claim)
    return inputs, prior, claims
