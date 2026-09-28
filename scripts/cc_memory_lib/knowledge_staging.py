"""Private, rebuildable preparation checkpoints, never current evidence.

The canonical schema stays at version one. Old readers ignore this meta namespace;
manifest identity prevents reuse after an older reader changes the generation.
"""
import json
from pathlib import Path

from .knowledge_sources import digest, LIMITS
from .knowledge_store import get, put, KnowledgeError

PREFIX = 'ingest-stage/'
HEADER = PREFIX + 'header'
VERSION = 1


def clear(db):
    db.execute("DELETE FROM meta WHERE key GLOB 'ingest-stage/*'")


def identity(sources):
    return sorted((s['id'], s['path'], s['revision'], s['kind'], s['title'], digest(s['body']))
                  for s in sources)


def begin(db, root, config, head, sources, changed):
    manifest = digest(json.dumps({'version': VERSION, 'root': str(Path(root).resolve()),
                                 'policy': config, 'head': head,
                                 'generation': get(db, 'generation', 0),
                                 'sources': identity(sources)}, sort_keys=True))
    previous = get(db, HEADER)
    if previous and previous.get('manifest') == manifest:
        return previous
    with db:
        clear(db)
        header = {'version': VERSION, 'manifest': manifest, 'total': len(changed),
                  'prepared': 0, 'passages': 0}
        put(db, HEADER, header)
        if changed:
            put(db, 'needs_reconcile', True)
    return header


def prepare(db, header, sources, tokenizer):
    from .knowledge_checkpoints import Writer
    writer = Writer(db, HEADER, first=header['prepared'] == 0)
    for source in sources:
        key = PREFIX + digest(source['id'])
        cached = get(db, key)
        if cached is not None:
            if (cached.get('manifest') != header['manifest'] or
                    cached.get('digest') != digest(json.dumps(cached.get('chunks'), sort_keys=True))):
                raise KnowledgeError('invalid_ingest_checkpoint')
            continue
        chunks = []
        for chunk in tokenizer(source):
            chunks.append(chunk)
            if len(chunks) + header['passages'] > LIMITS['chunks']:
                raise KnowledgeError('chunk_budget')
        # First source is durable immediately, then bounded batches amortize fsync.
        # A killed batch rolls back; earlier batches survive a matching retry.
        updated = {**header, 'prepared': header['prepared'] + 1,
                   'passages': header['passages'] + len(chunks)}
        writer.add(key, {'manifest': header['manifest'], 'chunks': chunks,
                        'digest': digest(json.dumps(chunks, sort_keys=True))}, updated)
        header.update(updated)
    writer.flush()


def chunks(db, source):
    cached = get(db, PREFIX + digest(source['id']))
    if cached is None:
        raise KnowledgeError('missing_ingest_checkpoint')
    return cached['chunks']
