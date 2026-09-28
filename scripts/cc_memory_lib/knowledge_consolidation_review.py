"""Atomic selected publication and guarded undo; caller owns the transaction."""
import json
from .knowledge_store import get, put, now
from . import knowledge_wiki_review as review
from .knowledge_consolidation import (shape, identifier, require, job_row, current,
                                     digest, encoded, page_revision, binding)


def replay(db, job, request_id, operation):
    require(identifier(request_id))
    for row in db.execute('SELECT payload FROM consolidation_events WHERE job=?', (job,)):
        event = json.loads(row[0])
        if event.get('request_id') == request_id:
            require(event['operation'] == operation, 'consolidation_request_conflict')
            return True
    return False


def event(db, job, request_id, operation, receipt=None):
    payload = {'request_id': request_id, 'operation': operation}
    if receipt is not None:
        payload['receipt'] = receipt
    db.execute('INSERT INTO consolidation_events(job,kind,payload,created) VALUES(?,?,?,?)',
               (job, operation['action'], encoded(payload), now()))


def save_claim(db, target, kind, section):
    key = 'memory:' + digest(target)
    old = db.execute('SELECT * FROM memory_claims WHERE id=?', (key,)).fetchone()
    if old:
        db.execute('INSERT OR IGNORE INTO memory_claim_revisions VALUES(?,?,?,?,?,?,?,?,?)',
                   tuple(old[k] for k in ('id', 'revision', 'target', 'kind', 'title', 'body', 'dependencies', 'state', 'updated')))
    claim = {'id': key, 'target': target, 'kind': kind, 'title': section['title'], 'body': section['body'],
             'revision': digest(section), 'dependencies': encoded(section['dependencies']),
             'state': 'approved', 'updated': now()}
    db.execute('INSERT OR REPLACE INTO memory_claims VALUES(?,?,?,?,?,?,?,?,?)', tuple(claim.values()))
    db.execute('INSERT OR IGNORE INTO memory_claim_revisions VALUES(?,?,?,?,?,?,?,?,?)',
               tuple(claim[k] for k in ('id', 'revision', 'target', 'kind', 'title', 'body', 'dependencies', 'state', 'updated')))
    return dict(old) if old else None


def decide(db, root, value):
    shape(value, 'job ids decision request_id')
    require(type(value['decision']) is str and value['decision'] in ('accept', 'reject'))
    require(type(value['ids']) is list and 0 < len(value['ids']) <= 20
            and all(identifier(v) for v in value['ids']) and len(set(value['ids'])) == len(value['ids']))
    job = job_row(db, value['job'])
    operation = {'action': value['decision'], 'ids': sorted(value['ids'])}
    if replay(db, job['id'], value['request_id'], operation):
        return job['id']
    require(job['state'] != 'purged', 'consolidation_job_unavailable')
    candidates = []
    for key in value['ids']:
        row = db.execute('SELECT * FROM consolidation_proposals WHERE id=? AND job=?', (key, job['id'])).fetchone()
        require(row is not None and row['status'] == 'pending', 'consolidation_proposal_unavailable')
        candidates.append(dict(row))
    stamp = now()
    receipt = None
    if value['decision'] == 'accept':
        current(db, root, job)
        require(len({r['target'] for r in candidates}) == len(candidates), 'consolidation_target_conflict')
        if db.execute('PRAGMA user_version').fetchone()[0] == 3:
            selected_ids = {r['id'] for r in candidates}
            for row in candidates:
                metadata = db.execute('SELECT * FROM consolidation_proposal_metadata WHERE proposal=?',
                                      (row['id'],)).fetchone()
                if metadata:
                    for prerequisite in json.loads(metadata['prerequisites']):
                        prior = db.execute('SELECT status,job FROM consolidation_proposals WHERE id=?',
                                           (prerequisite,)).fetchone()
                        require(prior is not None and prior['job'] == job['id']
                                and (prior['status'] == 'accepted' or prerequisite in selected_ids),
                                'consolidation_prerequisite_unavailable')
        approved = get(db, review.STATE, {})
        patches = []
        # Validate every member against the same original generation/page state.
        for row in candidates:
            ptype, key = row['target'].split('/', 1)
            require(row['base_revision'] == page_revision(db, ptype), 'consolidation_target_changed')
            review._validated_dependencies(db, json.loads(row['dependencies']))
            before = approved.get(ptype, {}).get(key)
            after = {k: row[k] for k in ('title', 'body')}
            after.update(dependencies=json.loads(row['dependencies']), approved=stamp,
                         claim_id='memory:' + digest(row['target']))
            patches.append({'target': row['target'], 'before': before, 'after': after,
                            'proposal': row['id'], 'kind': row['kind']})
        for patch in patches:
            ptype, key = patch['target'].split('/', 1)
            approved.setdefault(ptype, {})[key] = patch['after']
        review.commit_sections(db, approved, get(db, review.PROPOSALS, []))
        for patch in patches:
            patch['before_claim'] = save_claim(db, patch['target'], patch['kind'], patch['after'])
            if db.execute('PRAGMA user_version').fetchone()[0] == 3:
                metadata = db.execute('SELECT epistemic_status,scope FROM consolidation_proposal_metadata WHERE proposal=?',
                                      (patch['proposal'],)).fetchone()
                if metadata:
                    db.execute('INSERT OR REPLACE INTO consolidation_claim_metadata VALUES(?,?,?)',
                               ('memory:' + digest(patch['target']), metadata['epistemic_status'], metadata['scope']))
        previous = json.loads(job['receipt']) if job['receipt'] else None
        accumulated = {p['target']: p for p in previous['patches']} if previous and not previous.get('undone') else {}
        for patch in patches:
            old = accumulated.get(patch['target'])
            if old:
                patch['before'], patch['before_claim'] = old['before'], old['before_claim']
            accumulated[patch['target']] = patch
        receipt = {'request_id': value['request_id'], 'decision': 'accept',
                   'patches': list(accumulated.values()), 'at': stamp, 'cumulative': True}
        db.execute('UPDATE consolidation_jobs SET receipt=?,binding=? WHERE id=?',
                   (encoded(receipt), encoded(binding(db, root)), job['id']))
        put(db, 'consolidation_epoch', get(db, 'consolidation_epoch', 0) + 1)
    for row in candidates:
        db.execute('UPDATE consolidation_proposals SET status=?,decided=? WHERE id=?',
                   ('accepted' if value['decision'] == 'accept' else 'rejected', stamp, row['id']))
    pending = db.execute("SELECT count(*) FROM consolidation_proposals WHERE job=? AND status='pending'", (job['id'],)).fetchone()[0]
    accepted = db.execute("SELECT count(*) FROM consolidation_proposals WHERE job=? AND status='accepted'", (job['id'],)).fetchone()[0]
    state = ('partial' if accepted else 'ready') if pending else ('published' if accepted else 'rejected')
    db.execute('UPDATE consolidation_jobs SET state=?,updated=? WHERE id=?', (state, stamp, job['id']))
    event(db, job['id'], value['request_id'], operation, receipt)
    return job['id']


def undo(db, root, value):
    shape(value, 'job request_id')
    job = job_row(db, value['job'])
    operation = {'action': 'undo'}
    if replay(db, job['id'], value['request_id'], operation):
        return job['id']
    current(db, root, job)
    receipt = json.loads(job['receipt']) if job['receipt'] else None
    require(receipt and not receipt.get('undone'), 'consolidation_undo_unavailable')
    approved = get(db, review.STATE, {})
    for patch in receipt['patches']:
        ptype, key = patch['target'].split('/', 1)
        require(approved.get(ptype, {}).get(key) == patch['after'], 'consolidation_target_changed')
        if patch['before']:
            review._validated_dependencies(db, patch['before']['dependencies'])
            approved[ptype][key] = patch['before']
        else:
            approved.get(ptype, {}).pop(key, None)
    review.commit_sections(db, approved, get(db, review.PROPOSALS, []))
    for patch in receipt['patches']:
        key = patch['after']['claim_id']
        db.execute("UPDATE memory_claim_revisions SET state='superseded' WHERE id=? AND revision=?", (key, digest(patch['after'])))
        if patch['before_claim']:
            claim = patch['before_claim']
            db.execute('INSERT OR REPLACE INTO memory_claims VALUES(?,?,?,?,?,?,?,?,?)',
                       tuple(claim[k] for k in ('id', 'target', 'kind', 'title', 'body', 'revision', 'dependencies', 'state', 'updated')))
        else:
            db.execute('DELETE FROM memory_claims WHERE id=?', (key,))
            if db.execute('PRAGMA user_version').fetchone()[0] == 3:
                db.execute('DELETE FROM consolidation_claim_metadata WHERE claim_id=?', (key,))
    db.execute("UPDATE consolidation_proposals SET status='stale' WHERE job=? AND status='accepted'", (job['id'],))
    receipt['undone'] = True
    db.execute("UPDATE consolidation_jobs SET receipt=?,state='stale',updated=? WHERE id=?", (encoded(receipt), now(), job['id']))
    put(db, 'consolidation_epoch', get(db, 'consolidation_epoch', 0) + 1)
    event(db, job['id'], value['request_id'], operation, receipt)
    return job['id']
