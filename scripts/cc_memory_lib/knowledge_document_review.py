"""Private, revision-bound human document categories; originals stay untouched."""
import json
import re

from .knowledge_document_types import classify, collection, result, TYPES
from .knowledge_sources import digest
from .knowledge_store import database, get, put, now, KnowledgeError

PREFIX = 'document_type_review:'
MAX_REVIEWS = 6000


def validate(value, identifier):
    if (type(value) is not dict or set(value) != {'source', 'path', 'revision', 'type', 'reason', 'updated'}
            or value['source'] != identifier or type(identifier) is not str or not 0 < len(identifier) <= 180
            or type(value['path']) is not str or len(value['path']) > 4096
            or type(value['revision']) is not str or not re.fullmatch('[a-f0-9]{64}', value['revision'])
            or type(value['type']) is not str or value['type'] not in TYPES
            or type(value['reason']) is not str or not 5 <= len(value['reason'].strip()) <= 600
            or type(value['updated']) is not str or len(value['updated']) > 80):
        raise KnowledgeError('invalid_document_classification_state')
    return value


def reviews(db):
    values = {}
    for row in db.execute('SELECT key,value FROM meta WHERE substr(key,1,?)=? LIMIT ?', (len(PREFIX), PREFIX, MAX_REVIEWS + 1)):
        if len(values) >= MAX_REVIEWS or len(row['value']) > 12000:
            raise KnowledgeError('document_classification_limit')
        identifier = row['key'][len(PREFIX):]
        try:
            values[identifier] = validate(json.loads(row['value']), identifier)
        except (ValueError, TypeError) as error:
            raise KnowledgeError('invalid_document_classification_state') from error
    return values


def effective(source, saved=None):
    automatic = classify(source)
    token = digest(json.dumps([source['id'], source['path'], source['revision'], saved], sort_keys=True))
    same = saved and saved['revision'] == source['revision'] and saved['path'] == source['path']
    current = result(saved['type'], 'confirmed', saved['reason']) if same else automatic
    return {**current, 'collection': collection(source), 'review_token': token,
            'review_stale': bool(saved and not same), 'automatic_key': automatic['key'],
            'previous_label': TYPES[saved['type']][0] if saved and not same else None,
            'reviewed_at': saved['updated'] if saved else None}


def read_type(db, source):
    saved = get(db, PREFIX + source['id'])
    if saved is not None:
        validate(saved, source['id'])
    from .knowledge_organization import effective as organization
    from .knowledge_tags import public_tags
    return {**effective(source, saved), 'organization': organization(db, source), 'tags': public_tags(db, source)}


def save(root, value):
    if (type(value) is not dict or set(value) != {'source', 'revision', 'type', 'reason', 'expected'}
            or type(value['source']) is not str or not 0 < len(value['source']) <= 180
            or type(value['revision']) is not str or not re.fullmatch('[a-f0-9]{64}', value['revision'])
            or type(value['expected']) is not str or not re.fullmatch('[a-f0-9]{64}', value['expected'])
            or value['type'] is not None and (type(value['type']) is not str or value['type'] not in TYPES)
            or type(value['reason']) is not str or not 5 <= len(value['reason'].strip()) <= 600):
        raise KnowledgeError('invalid_document_classification')
    with database(root) as db:
        if db is None or get(db, 'needs_reconcile', True):
            raise KnowledgeError('document_classification_refresh_required')
        row = db.execute('SELECT id,path,title,revision,kind FROM sources WHERE id=? AND deleted=0', (value['source'],)).fetchone()
        if row is None:
            raise KnowledgeError('source_unavailable')
        source = dict(row)
        current = read_type(db, source)
        if source['revision'] != value['revision'] or current['review_token'] != value['expected']:
            raise KnowledgeError('document_classification_changed')
        if source['kind'] in ('conversation', 'session') and value['type'] not in (None, classify(source)['key']):
            raise KnowledgeError('document_classification_record_kind')
        saved = reviews(db)
        if value['type'] is not None and source['id'] not in saved and len(saved) >= MAX_REVIEWS:
            raise KnowledgeError('document_classification_limit')
        with db:
            if value['type'] is None:
                db.execute('DELETE FROM meta WHERE key=?', (PREFIX + source['id'],))
            else:
                put(db, PREFIX + source['id'], dict(source=source['id'], path=source['path'], revision=source['revision'],
                                                   type=value['type'], reason=value['reason'].strip(), updated=now()))
            put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
        return dict(saved=True, source=source['id'], document_type=read_type(db, source))
