"""Forget copied private content across jobs, receipts and derived claim history."""
import json
from .knowledge_store import get, put, now
from .knowledge_consolidation import PRIVACY, encoded


def contains(value, source):
    if isinstance(value, dict):
        return value.get('source') == source or any(contains(v, source) for v in value.values())
    if isinstance(value, list):
        return any(contains(v, source) for v in value)
    # Some receipt fields keep canonical dependency JSON from SQLite records.
    if isinstance(value, str) and value.startswith(('[', '{')):
        try:
            return contains(json.loads(value), source)
        except (ValueError, RecursionError):
            return False
    return False


def purge(db, source):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (2, 3):
        return
    put(db, PRIVACY, get(db, PRIVACY, 0) + 1)
    if version == 3:
        db.execute('DELETE FROM consolidation_progress WHERE source=?', (source,))
        db.execute('DELETE FROM consolidation_passage_progress WHERE source=?', (source,))
    for job in db.execute('SELECT * FROM consolidation_jobs').fetchall():
        touched = db.execute('SELECT 1 FROM consolidation_inputs WHERE job=? AND source=?', (job['id'], source)).fetchone()
        if not touched and not any(contains(json.loads(job[k]), source) for k in ('manifest', 'receipt') if job[k]):
            continue
        if version == 3:
            db.execute('DELETE FROM consolidation_proposal_metadata WHERE proposal IN (SELECT id FROM consolidation_proposals WHERE job=?)', (job['id'],))
        for table in ('consolidation_inputs', 'consolidation_proposals', 'consolidation_events'):
            db.execute('DELETE FROM ' + table + ' WHERE job=?', (job['id'],))
        if version == 3:
            db.execute('DELETE FROM consolidation_attempts WHERE job=?', (job['id'],))
            db.execute('DELETE FROM consolidation_queue WHERE job=?', (job['id'],))
        empty = {'title': 'Forgotten source content removed', 'sources': [], 'before': {}, 'count': 0, 'total': 0, 'deferred': 0}
        db.execute("UPDATE consolidation_jobs SET state='purged',binding='{}',manifest=?,receipt=NULL,error=NULL,updated=? WHERE id=?",
                   (encoded(empty), now(), job['id']))
    ids = set()
    for table in ('memory_claims', 'memory_claim_revisions'):
        for row in db.execute('SELECT id,dependencies FROM ' + table):
            if contains(json.loads(row['dependencies']), source):
                ids.add(row['id'])
    for key in ids:
        db.execute('DELETE FROM memory_claims WHERE id=?', (key,))
        db.execute('DELETE FROM memory_claim_revisions WHERE id=?', (key,))
        if version == 3:
            db.execute('DELETE FROM consolidation_claim_metadata WHERE claim_id=?', (key,))
    # A newer revision can cite different evidence. Removing its lineage must
    # not leave a rendered section pointing at a deleted claim or retain copies
    # of an earlier private revision in the page history.
    from . import knowledge_wiki_review as review
    approved = get(db, review.STATE, {})
    affected_pages = set()
    for page, sections in approved.items():
        for key, section in list(sections.items()):
            if section.get('claim_id') in ids:
                del sections[key]
                affected_pages.add('wiki:review:' + page)
    if affected_pages:
        put(db, review.STATE, approved)
        put(db, review.EPOCH, get(db, review.EPOCH, 0) + 1)
        for page in affected_pages:
            for table in ('wiki', 'wiki_history'):
                db.execute('DELETE FROM ' + table + ' WHERE id=?', (page,))
