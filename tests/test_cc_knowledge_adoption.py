"""Adoption receipts describe observed evidence without granting acceptance."""
from datetime import datetime, timedelta, timezone
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib.knowledge_adoption import observe, summarize
from cc_memory_lib.knowledge_store import DDL
from cc_knowledge_adoption import main


BASE = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def archive(project):
    directory = project / '.controlcoding' / 'knowledge'
    directory.mkdir(parents=True)
    path = directory / 'knowledge.db'
    with sqlite3.connect(path) as db:
        db.executescript(DDL)
        db.execute('PRAGMA user_version=1')
        db.execute('INSERT INTO meta VALUES(?,?)', ('policy', '{}'))
        db.execute('INSERT INTO meta VALUES(?,?)', ('needs_reconcile', 'false'))
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                   ('s1', 'a.md', 'A', 'sha', 'body', 'project', 0, '2026-09-01'))
    return path


def receipts(project, kind, count=8, gap=None):
    output = []
    for day in range(count):
        if day == gap:
            continue
        output.append(observe(project, kind, 'Reviewed day ' + str(day),
                              now=BASE + timedelta(days=day)))
    return output


def test_absent_archive_is_explicit_and_observation_does_not_create_files(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    before = list(project.rglob('*'))
    result = observe(project, 'software', 'Initial inspection', now=BASE)
    assert result['archive']['flags']['archive_present'] is False
    assert result['archive']['appears_consistent'] is False
    assert result['archive']['structural_sha256'] is None
    assert list(project.rglob('*')) == before


def test_archive_observation_counts_digest_and_preserves_bytes(tmp_path):
    project = tmp_path / 'project'
    path = archive(project)
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    result = observe(project, 'documents', 'Reviewed source and work', now=BASE)
    assert result['archive']['counts']['sources'] == 1
    assert result['archive']['flags']['quick_check_ok'] is True
    assert result['archive']['appears_consistent'] is True
    assert len(result['archive']['structural_sha256']) == 64
    assert {p: p.read_bytes() for p in project.rglob('*') if p.is_file()} == before
    assert path.exists()


def test_seven_days_both_projects_eligible_but_never_accepted(tmp_path):
    software, documents = tmp_path / 'software', tmp_path / 'documents'
    archive(software)
    archive(documents)
    output = summarize(receipts(software, 'software') + receipts(documents, 'documents'),
                       now=BASE + timedelta(days=8))
    assert output['evidence_eligible_for_human_review'] is True
    assert output['missing_requirements'] == []
    assert output['human_adoption_acceptance'] == 'pending'
    assert output['completed'] is False


def test_missing_day_and_short_span_cannot_pass(tmp_path):
    software, documents = tmp_path / 'software', tmp_path / 'documents'
    archive(software)
    archive(documents)
    output = summarize(receipts(software, 'software', gap=3) + receipts(documents, 'documents'),
                       now=BASE + timedelta(days=8))
    assert not output['evidence_eligible_for_human_review']
    assert 'software_seven_day_evidence_incomplete' in output['missing_requirements']
    assert 'missing_daily_observation' in output['projects']['software'][0]['missing']
    short = summarize(receipts(software, 'software', count=7), now=BASE + timedelta(days=8))
    assert 'elapsed_span_under_7_days' in short['projects']['software'][0]['missing']


def test_future_and_duplicate_receipts_rejected(tmp_path):
    archive(tmp_path / 'project')
    row = observe(tmp_path / 'project', 'software', 'Work observed', now=BASE)
    with pytest.raises(ValueError, match='future'):
        summarize([row], now=BASE - timedelta(minutes=6))
    with pytest.raises(ValueError, match='duplicate'):
        summarize([row, copy.deepcopy(row)], now=BASE + timedelta(days=1))


def test_malformed_schema_and_cli_read_only(tmp_path, capsys):
    project = tmp_path / 'project'
    archive(project)
    row = observe(project, 'software', 'Inspected project', now=BASE)
    broken = copy.deepcopy(row)
    broken['archive']['counts']['sources'] = -1
    with pytest.raises(ValueError, match='counts'):
        summarize([broken], now=BASE + timedelta(days=1))
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps(row), encoding='utf-8-sig')
    assert main(['summarize', str(receipt)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['completed'] is False
    assert report['human_adoption_acceptance'] == 'pending'


def test_boolean_schema_and_digest_status_mismatch_rejected(tmp_path):
    project = tmp_path / 'project'
    archive(project)
    row = observe(project, 'software', 'Inspected source', now=BASE)
    boolean_version = copy.deepcopy(row)
    boolean_version['schema_version'] = True
    with pytest.raises(ValueError, match='version'):
        summarize([boolean_version], now=BASE + timedelta(days=1))
    missing_digest = copy.deepcopy(row)
    missing_digest['archive']['structural_sha256'] = None
    with pytest.raises(ValueError, match='digest'):
        summarize([missing_digest], now=BASE + timedelta(days=1))
    fake_digest = observe(tmp_path, 'documents', 'No archive', now=BASE)
    fake_digest['archive']['structural_sha256'] = 'a' * 64
    with pytest.raises(ValueError, match='digest'):
        summarize([fake_digest], now=BASE + timedelta(days=1))


@pytest.mark.skipif(os.name != 'nt', reason='Windows path aliases')
def test_case_alias_cannot_supply_distinct_project_kinds(tmp_path):
    project = tmp_path / 'AdoptionProject'
    archive(project)
    software = receipts(project, 'software')
    documents = receipts(project, 'documents')
    for row in documents:
        row['project'] = row['project'].swapcase()
    output = summarize(software + documents, now=BASE + timedelta(days=8))
    assert output['evidence_eligible_for_human_review'] is False
    assert 'distinct_projects_required' in output['missing_requirements']


def test_archive_directory_symlink_rejected_without_following(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    other = tmp_path / 'other'
    archive(other)
    try:
        (project / '.controlcoding').symlink_to(other / '.controlcoding', target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('directory symlink not available')
    with pytest.raises(ValueError, match='unsupported archive directory'):
        observe(project, 'software', 'Inspected linked archive', now=BASE)
