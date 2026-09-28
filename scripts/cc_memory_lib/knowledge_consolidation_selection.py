"""Incremental selection by original source revision, independent of scan order.

Only a successful analysis consumes its declared analyzed prefix. Concurrent
jobs selecting the same revisions are idempotent; unrelated source edits never
reset previous progress. Generated wiki or claim text is not eligible input.
"""
import json

from .knowledge_store import now
from .knowledge_consolidation import require

ORIGINALS = "s.deleted=0 AND s.id NOT LIKE 'wiki:%'"
TEXT = "length(trim(c.text,char(9)||char(10)||char(13)||' '))>0"
UNREAD = (TEXT + ' AND NOT EXISTS (SELECT 1 FROM consolidation_passage_progress p '
          'WHERE p.chunk=c.id AND p.source=c.source AND p.revision=c.revision)')
PENDING = (ORIGINALS + ' AND EXISTS (SELECT 1 FROM chunks c '
           'WHERE c.source=s.id AND c.revision=s.revision AND ' + UNREAD + ')')


def eligible_count(db):
    return db.execute('SELECT count(*) FROM sources s WHERE ' + PENDING).fetchone()[0]


def progress(db):
    inventory = db.execute('SELECT count(*) FROM sources s WHERE ' + ORIGINALS).fetchone()[0]
    nonempty = db.execute('SELECT count(*) FROM sources s WHERE ' + ORIGINALS +
                         ' AND EXISTS (SELECT 1 FROM chunks c WHERE c.source=s.id '
                         'AND c.revision=s.revision AND ' + TEXT + ')').fetchone()[0]
    eligible = eligible_count(db)
    passages = db.execute('SELECT count(*) FROM chunks c JOIN sources s ON s.id=c.source '
                          'WHERE ' + ORIGINALS + ' AND c.revision=s.revision AND ' + TEXT).fetchone()[0]
    pending = db.execute('SELECT count(*) FROM chunks c JOIN sources s ON s.id=c.source '
                        'WHERE ' + ORIGINALS + ' AND c.revision=s.revision AND ' + UNREAD).fetchone()[0]
    return {'inventory': inventory, 'eligible': eligible, 'analyzed': nonempty - eligible,
            'excluded': inventory - nonempty, 'passages': passages,
            'pending_passages': pending, 'analyzed_passages': passages - pending}


def choose(db, limit=8):
    require(type(limit) is int and 1 <= limit <= 20)
    rows = db.execute('SELECT s.id,s.path,s.title,s.revision FROM sources s WHERE ' + PENDING +
                      ' ORDER BY s.path LIMIT ?', (limit,)).fetchall()
    selected, size = [], 0
    for source in rows:
        chunk = db.execute('SELECT c.* FROM chunks c WHERE c.source=? AND c.revision=? AND ' +
                           UNREAD + ' ORDER BY c.line,c.end_line,c.id LIMIT 1',
                           (source['id'], source['revision'])).fetchone()
        item = {'source': source['id'], 'title': source['title'][:256], 'path': source['path'],
                'revision': source['revision'], 'line': chunk['line'], 'end_line': chunk['end_line'],
                'excerpt': chunk['text']}
        cost = len(json.dumps(item, ensure_ascii=False))
        if size + cost > 6000:
            break
        selected.append(item)
        size += cost
    require(selected or not rows, 'consolidation_passage_budget')
    return selected, eligible_count(db)


def mark_analyzed(db, packet, count):
    inputs = packet['input_manifest']
    require(type(count) is int and 0 <= count <= len(inputs), 'invalid_consolidation_coverage')
    # Bound storage to the current original inventory. Missing/removed sources
    # cannot become eligible through this maintenance operation.
    db.execute('DELETE FROM consolidation_progress WHERE source NOT IN '
               '(SELECT id FROM sources WHERE deleted=0)')
    db.execute('DELETE FROM consolidation_passage_progress WHERE chunk NOT IN (SELECT id FROM chunks)')
    for source in inputs[:count]:
        current = db.execute('SELECT revision,deleted FROM sources WHERE id=?', (source['source'],)).fetchone()
        require(current is not None and not current['deleted'] and current['revision'] == source['revision'],
                'consolidation_context_changed')
        chunks = db.execute('SELECT id FROM chunks WHERE source=? AND revision=? AND line=? '
                            'AND end_line=? AND text=?', (source['source'], source['revision'],
                            source['line'], source['end_line'], source['excerpt'])).fetchall()
        require(bool(chunks), 'consolidation_context_changed')
        for chunk in chunks:
            db.execute('INSERT OR REPLACE INTO consolidation_passage_progress VALUES(?,?,?,?)',
                       (chunk['id'], source['source'], source['revision'], now()))
        left = db.execute('SELECT 1 FROM chunks c WHERE c.source=? AND c.revision=? AND ' +
                          UNREAD + ' LIMIT 1', (source['source'], source['revision'])).fetchone()
        if left is None:
            db.execute('INSERT OR REPLACE INTO consolidation_progress VALUES(?,?,?)',
                       (source['source'], source['revision'], now()))
