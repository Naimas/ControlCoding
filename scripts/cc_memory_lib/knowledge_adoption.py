"""Read-only adoption observations; receipts are claims, never human acceptance."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

SCHEMA_VERSION = 1
MAX_FUTURE = timedelta(minutes=5)
MIN_SPAN = timedelta(days=7)
TABLES = ('sources', 'chunks', 'edges', 'wiki', 'conversations', 'turns')


def utc(value):
    if type(value) is not str or not value.endswith('Z'):
        raise ValueError('timestamp must be UTC with Z suffix')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise ValueError('invalid UTC timestamp') from exc
    if parsed.utcoffset() != timedelta(0):
        raise ValueError('timestamp must be UTC')
    return parsed


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _ordinary(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('unsupported archive path')


def _ordinary_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('unsupported archive directory')


def _project_key(value):
    # Historical receipt paths need not still exist. On Windows, normcase also
    # prevents capitalization aliases from counting as two adopted projects.
    return os.path.normcase(str(Path(value).resolve(strict=False)))


def observe(project, kind, note, *, now=None):
    if kind not in ('software', 'documents'):
        raise ValueError('kind must be software or documents')
    if type(note) is not str or not note.strip() or len(note) > 2000:
        raise ValueError('note must contain 1-2000 characters')
    root = Path(project).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('project must be a directory')
    instant = now or datetime.now(timezone.utc)
    archive = root / '.controlcoding' / 'knowledge' / 'knowledge.db'
    counts = {name: 0 for name in TABLES}
    flags = {'archive_present': False, 'schema_supported': False,
             'policy_present': False, 'needs_reconcile': None,
             'source_failures': None, 'quick_check_ok': None}
    digest = None
    if archive.exists() or archive.is_symlink():
        for directory in (root / '.controlcoding', root / '.controlcoding' / 'knowledge'):
            _ordinary_directory(directory)
        _ordinary(archive)
        # URI mode=ro prevents SQLite from creating or initializing an archive.
        with sqlite3.connect(archive.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.execute('BEGIN')
            flags['archive_present'] = True
            version = db.execute('PRAGMA user_version').fetchone()[0]
            flags['schema_supported'] = version == 1
            if flags['schema_supported']:
                present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not set(TABLES + ('meta',)).issubset(present):
                    flags['schema_supported'] = False
            if flags['schema_supported']:
                h = hashlib.sha256()
                for name in TABLES:
                    counts[name] = db.execute('SELECT count(*) FROM ' + name).fetchone()[0]
                    h.update((name + ':' + str(counts[name]) + '\n').encode())
                for statement in (
                    'SELECT id,revision,deleted FROM sources ORDER BY id',
                    'SELECT id,source,revision,model FROM chunks ORDER BY id',
                    'SELECT source,target,revision,kind FROM edges ORDER BY source,target,kind',
                    'SELECT id,revision FROM wiki ORDER BY id',
                    'SELECT id,updated FROM conversations ORDER BY id',
                    'SELECT id,conversation,sequence FROM turns ORDER BY id',
                ):
                    for row in db.execute(statement):
                        h.update(json.dumps(row, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
                        h.update(b'\n')
                digest = h.hexdigest()
                meta = {key: json.loads(value) for key, value in db.execute(
                    "SELECT key,value FROM meta WHERE key IN ('policy','needs_reconcile','source_diagnostics')")}
                flags['policy_present'] = 'policy' in meta
                flags['needs_reconcile'] = meta.get('needs_reconcile', True)
                diagnostics = meta.get('source_diagnostics', {})
                flags['source_failures'] = sum(row.get('state') == 'failed' for row in diagnostics.values())
                flags['quick_check_ok'] = db.execute('PRAGMA quick_check(1)').fetchone()[0] == 'ok'
            db.execute('ROLLBACK')
    healthy = (flags['archive_present'] and flags['schema_supported'] and flags['policy_present']
               and flags['needs_reconcile'] is False and flags['source_failures'] == 0
               and flags['quick_check_ok'] is True)
    return {'schema_version': SCHEMA_VERSION, 'id': str(uuid.uuid4()), 'observed_at': stamp(instant),
            'project': str(root), 'kind': kind, 'note': note.strip(),
            'archive': {'counts': counts, 'structural_sha256': digest, 'flags': flags,
                        'appears_consistent': bool(healthy)},
            'caveat': 'Read-only structural observation; this receipt cannot authenticate historical time, actual use, or absence of data loss.'}


def validate(receipt, *, now=None):
    if type(receipt) is not dict or set(receipt) != {'schema_version', 'id', 'observed_at', 'project', 'kind', 'note', 'archive', 'caveat'}:
        raise ValueError('invalid adoption receipt schema')
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != SCHEMA_VERSION or type(receipt['id']) is not str:
        raise ValueError('invalid adoption receipt version or ID')
    try:
        uuid.UUID(receipt['id'])
    except (ValueError, AttributeError) as exc:
        raise ValueError('invalid adoption receipt ID') from exc
    moment = utc(receipt['observed_at'])
    if moment > (now or datetime.now(timezone.utc)) + MAX_FUTURE:
        raise ValueError('future adoption receipt')
    if type(receipt['project']) is not str or not Path(receipt['project']).is_absolute():
        raise ValueError('invalid project identity')
    if receipt['kind'] not in ('software', 'documents') or type(receipt['note']) is not str or not receipt['note'].strip():
        raise ValueError('invalid kind or activity note')
    archive = receipt['archive']
    if type(archive) is not dict or set(archive) != {'counts', 'structural_sha256', 'flags', 'appears_consistent'}:
        raise ValueError('invalid archive observation')
    if type(archive['counts']) is not dict or set(archive['counts']) != set(TABLES) or any(type(n) is not int or n < 0 for n in archive['counts'].values()):
        raise ValueError('invalid archive counts')
    if archive['structural_sha256'] is not None and (type(archive['structural_sha256']) is not str or len(archive['structural_sha256']) != 64 or any(c not in '0123456789abcdef' for c in archive['structural_sha256'])):
        raise ValueError('invalid archive digest')
    if type(archive['flags']) is not dict or set(archive['flags']) != {'archive_present', 'schema_supported', 'policy_present', 'needs_reconcile', 'source_failures', 'quick_check_ok'}:
        raise ValueError('invalid archive flags')
    flags = archive['flags']
    if any(type(flags[key]) is not bool for key in ('archive_present', 'schema_supported', 'policy_present')):
        raise ValueError('invalid archive flags')
    if flags['needs_reconcile'] is not None and type(flags['needs_reconcile']) is not bool:
        raise ValueError('invalid reconciliation flag')
    if flags['source_failures'] is not None and (type(flags['source_failures']) is not int or flags['source_failures'] < 0):
        raise ValueError('invalid source failure count')
    if flags['quick_check_ok'] is not None and type(flags['quick_check_ok']) is not bool:
        raise ValueError('invalid integrity flag')
    if type(archive['appears_consistent']) is not bool or type(receipt['caveat']) is not str or not receipt['caveat']:
        raise ValueError('invalid receipt status')
    if flags['schema_supported'] != (archive['structural_sha256'] is not None):
        raise ValueError('archive digest and schema status disagree')
    if flags['schema_supported'] and not flags['archive_present']:
        raise ValueError('schema status requires an archive')
    consistent = (flags['archive_present'] and flags['schema_supported'] and flags['policy_present']
                  and flags['needs_reconcile'] is False and flags['source_failures'] == 0
                  and flags['quick_check_ok'] is True)
    if archive['appears_consistent'] is not bool(consistent):
        raise ValueError('inconsistent archive status')
    return moment


def summarize(receipts, *, now=None):
    if type(receipts) is not list or not receipts:
        raise ValueError('at least one receipt is required')
    seen = set()
    grouped = {}
    for receipt in receipts:
        moment = validate(receipt, now=now)
        if receipt['id'] in seen:
            raise ValueError('duplicate adoption receipt ID')
        seen.add(receipt['id'])
        grouped.setdefault((receipt['kind'], _project_key(receipt['project'])), []).append((moment, receipt))
    missing = []
    results = {}
    for kind in ('software', 'documents'):
        candidates = []
        for (candidate_kind, project), rows in grouped.items():
            if candidate_kind != kind:
                continue
            rows.sort(key=lambda item: item[0])
            start, end = rows[0][0], rows[-1][0]
            daily = [any(start + timedelta(days=day) <= moment < start + timedelta(days=day + 1)
                         for moment, _ in rows) for day in range(7)]
            issues = []
            if end - start < MIN_SPAN:
                issues.append('elapsed_span_under_7_days')
            if not all(daily):
                issues.append('missing_daily_observation')
            if not all(row['archive']['appears_consistent'] for _, row in rows):
                issues.append('archive_not_consistent_in_every_observation')
            candidates.append({'project': project, 'observations': len(rows), 'first': stamp(start),
                               'last': stamp(end), 'elapsed_hours': round((end-start).total_seconds()/3600, 3),
                               'seven_daily_windows': daily, 'activity_notes': [row['note'] for _, row in rows],
                               'missing': issues})
        if not candidates:
            missing.append(kind + '_project_missing')
        elif not any(not item['missing'] for item in candidates):
            missing.append(kind + '_seven_day_evidence_incomplete')
        results[kind] = candidates
    eligible = not missing and any(
        not software['missing'] and not documents['missing'] and software['project'] != documents['project']
        for software in results['software'] for documents in results['documents'])
    if not eligible and not missing:
        missing.append('distinct_projects_required')
    return {'schema_version': SCHEMA_VERSION, 'receipt_count': len(receipts), 'projects': results,
            'evidence_eligible_for_human_review': eligible, 'missing_requirements': missing,
            'human_adoption_acceptance': 'pending', 'completed': False,
            'caveat': 'Receipt contents and timestamps are unauthenticated; eligibility does not prove real use or no data loss.'}
