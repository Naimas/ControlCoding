"""Current local Git/hosted PR bindings and reviewed preparation packets.

Snapshots are observations, never remote-state guarantees or deployments.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

import cc_evidence_inputs as inputs
from cc_layout import managed_relative
from cc_development_adapters import require, read_json, candidate_current
from cc_development_lib.method import digest

POLICY = '.controlcoding/development-release.json'
PACKETS = '.controlcoding/development_release_packets'
SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')


def _time(value):
    require(type(value) is str, 'pr_timestamp_invalid')
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.tzinfo is not None, 'pr_timestamp_invalid')
    return stamp.astimezone(dt.timezone.utc)


def _result(state='allowed', reasons=(), **extra):
    return {'state': state, 'reasons': list(reasons), **extra}


def policy(root, change):
    relative = managed_relative(root, POLICY)
    with inputs.SafeRoot(root) as reader:
        item = reader.read(relative, inputs.Budget(), missing=True)
        reader.validate()
    if item is None:
        return _result(required=False, policyDigest=None)
    value, sha = read_json(root, relative)
    require(type(value) is dict and set(value) == {'schemaVersion', 'repository', 'baseRef', 'requiredChecks',
            'requiredReviewers', 'maxAgeSeconds', 'pullRequests'} and type(value['schemaVersion']) is int
            and value['schemaVersion'] == 1 and type(value['repository']) is str and REPO.fullmatch(value['repository'])
            and type(value['baseRef']) is str and 0 < len(value['baseRef']) <= 255
            and type(value['maxAgeSeconds']) is int and 1 <= value['maxAgeSeconds'] <= 3600, 'pr_policy_invalid')
    require(type(value['requiredChecks']) is list and 1 <= len(value['requiredChecks']) <= 64
            and type(value['requiredReviewers']) is list and len(value['requiredReviewers']) <= 64
            and type(value['pullRequests']) is list and len(value['pullRequests']) <= 256, 'pr_policy_invalid')
    seen = set()
    for check in value['requiredChecks']:
        require(type(check) is dict and check.get('kind') in {'check_run', 'commit_status'}, 'pr_policy_check_invalid')
        fields = {'kind', 'name', 'appId'} if check['kind'] == 'check_run' else {'kind', 'name', 'creator'}
        require(set(check) == fields and type(check['name']) is str and 0 < len(check['name']) <= 255,
                'pr_policy_check_invalid')
        require(type(check['appId']) is int and check['appId'] > 0 if check['kind'] == 'check_run'
                else type(check['creator']) is str and bool(check['creator']), 'pr_policy_check_invalid')
        key = digest(check)
        require(key not in seen, 'pr_policy_check_duplicate')
        seen.add(key)
    reviewers = value['requiredReviewers']
    require(all(type(x) is str and re.fullmatch(r'[A-Za-z0-9-]{1,100}', x) for x in reviewers)
            and len({x.casefold() for x in reviewers}) == len(reviewers), 'pr_policy_reviewer_invalid')
    selections = set()
    for selection in value['pullRequests']:
        require(type(selection) is dict and set(selection) == {'changeId', 'number', 'base', 'headRepository'}
                and type(selection['changeId']) is str and selection['changeId'] not in selections
                and type(selection['number']) is int and selection['number'] > 0
                and type(selection['base']) is str and SHA.fullmatch(selection['base'])
                and type(selection['headRepository']) is str and REPO.fullmatch(selection['headRepository']), 'pr_policy_selection_invalid')
        selections.add(selection['changeId'])
    change_id = change.get('id') if type(change) is dict else change
    matches = [x for x in value['pullRequests'] if x['changeId'] == change_id]
    return _result('allowed' if len(matches) == 1 else 'blocked', [] if matches else ['pr_selection_missing'],
                   required=True, policyDigest=sha, policy=value, selection=matches[0] if matches else None)


def local_git(root, change, base):
    """Raw blobs and complete committed scope; no fetch, staging or checkout."""
    from cc_development_lib.evaluator import latest, accepted, candidate_identity, upstream
    candidate = change.get('candidate')
    require(candidate and candidate_current(root, candidate)['state'] == 'allowed', 'pr_candidate_not_current')
    require(candidate['digest'] == candidate_identity(change, candidate['files'])
            and candidate['upstream'] == upstream(change, 'build'), 'pr_candidate_identity_stale')
    plan = latest(change, 'plan')
    require(plan and accepted(change, 'plan'), 'pr_plan_not_approved')
    allowed = set(plan['content']['files'])
    indexed = {r['path']: r for r in candidate['files']}
    require(type(base) is str and SHA.fullmatch(base), 'pr_base_invalid')
    with inputs.SafeRoot(root) as reader:
        budget = inputs.Budget(total=64 * 1024 * 1024, per_file=8 * 1024 * 1024, entries=1024)
        with reader.git_projection(budget) as git:
            def call(args):
                code, raw = git.git(args, budget=budget)
                require(code == 0, 'pr_git_unavailable')
                return raw
            head = call(['rev-parse', '--verify', 'HEAD^{commit}']).decode('ascii').strip()
            require(SHA.fullmatch(head), 'pr_head_invalid')
            require(call(['rev-parse', '--verify', base + '^{commit}']).decode('ascii').strip() == base, 'pr_base_missing')
            tree = call(['rev-parse', '--verify', head + '^{tree}']).decode('ascii').strip()
            call(['merge-base', '--is-ancestor', base, head])
            statuses = call(['diff-tree', '--no-commit-id', '--name-status', '-r', '-z', '--no-renames', base, head]).split(b'\0')
            require(statuses[-1] == b'' and (len(statuses) - 1) % 2 == 0, 'pr_git_scope_invalid')
            changed = []
            for index in range(0, len(statuses) - 1, 2):
                status, rawpath = statuses[index:index + 2]
                require(status in {b'A', b'M'}, 'pr_unsupported_changed_file')
                path = rawpath.decode('utf-8', errors='strict')
                inputs.parts(path)
                require(path in allowed and path in indexed, 'pr_changed_file_outside_candidate')
                changed.append(path)
            require(changed and len(set(changed)) == len(changed), 'pr_changed_scope_empty_or_duplicate')
            for path, row in indexed.items():
                inputs.parts(path)
                entry = call(['ls-tree', '-z', head, '--', path]).split(b'\0')
                require(len(entry) == 2 and entry[1] == b'' and entry[0].split(b'\t', 1)[-1] == path.encode(), 'pr_tree_entry_invalid')
                require(entry[0].split(b' ', 1)[0] in {b'100644', b'100755'}, 'pr_tree_file_mode_unsupported')
                raw = call(['cat-file', 'blob', head + ':' + path])
                require(len(raw) == row['size'] and hashlib.sha256(raw).hexdigest() == row['sha256'], 'pr_candidate_not_committed')
            require(call(['rev-parse', '--verify', 'HEAD^{commit}']).decode('ascii').strip() == head, 'pr_head_changed_during_read')
        reader.validate()
    return {'base': base, 'head': head, 'tree': tree, 'changedFiles': sorted(changed), 'candidateDigest': candidate['digest']}


def prepare_observation(root, change):
    selected = policy(root, change)
    require(selected['required'] and selected['state'] == 'allowed', 'pr_policy_selection_missing')
    local = local_git(root, change, selected['selection']['base'])
    p = selected['policy']
    request = {'schemaVersion': 1, 'candidateDigest': change['candidate']['digest'], 'repository': p['repository'], 'number': selected['selection']['number'],
               'baseRef': p['baseRef'], 'base': local['base'], 'head': local['head'],
               'headRepository': selected['selection']['headRepository'], 'requiredChecks': p['requiredChecks'],
               'requiredReviewers': p['requiredReviewers']}
    import cc_development_github
    try:
        cc_development_github.request_value(request)
    except cc_development_github.WorkerError:
        require(False, 'pr_provider_request_invalid')
    from cc_development_lib.storage import identity
    from cc_development_lib.evaluator import latest
    with inputs.SafeRoot(root) as reader:
        project = identity(reader)
    return {'schemaVersion': 1, 'request': request, 'policyDigest': selected['policyDigest'], 'localGit': local,
            'changeId': change['id'], 'candidateDigest': change['candidate']['digest'], 'method': change['method'],
            'projectIdentity': project, 'planDigest': latest(change, 'plan')['digest']}


def assess_observation(root, change, observation_plan, observation, finished_at=None):
    try:
        current = prepare_observation(root, change)
        require(observation_plan == current, 'pr_observation_inputs_changed')
        require(type(observation) is dict and set(observation) == {'schemaVersion', 'provider', 'candidateDigest',
                'repository', 'number', 'baseRef', 'base', 'head', 'headRepository', 'state', 'draft', 'merged',
                'mergeCommitSha', 'complete', 'checks', 'reviews'}, 'pr_observation_invalid')
        request = current['request']
        require(type(observation.get('schemaVersion')) is int and observation['schemaVersion'] == 1
                and type(observation.get('number')) is int and observation.get('provider') == 'github'
                and observation.get('complete') is True, 'pr_observation_incomplete')
        for field in ('candidateDigest', 'repository', 'number', 'baseRef', 'base', 'head', 'headRepository'):
            require(observation.get(field) == request[field], 'pr_identity_changed')
        require(observation.get('state') == 'open' and observation.get('draft') is False
                and observation.get('merged') is False, 'pr_not_open_ready')
        stamp = _time(finished_at or observation.get('observedAt'))
        age = (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds()
        maximum_age = policy(root, change)['policy']['maxAgeSeconds']
        observer = inputs.progress_observation()
        if observer is not None:
            deadline = stamp + dt.timedelta(seconds=maximum_age)
            earliest = stamp - dt.timedelta(seconds=5)
            allowed_now = -5 <= age <= maximum_age
            key = 'pr_time:' + inputs.digest([change['id'], finished_at, maximum_age])
            observer.fact(key, allowed_now, lambda: earliest <= dt.datetime.now(dt.timezone.utc) <= deadline)
            if allowed_now:
                observer.expires(deadline.isoformat())
            elif age < -5:
                observer.expires(earliest.isoformat())
        require(-5 <= age <= maximum_age, 'pr_observation_expired')
        checks = observation.get('checks')
        reviews = observation.get('reviews')
        require(type(checks) is list and len(checks) <= 4096 and type(reviews) is list and len(reviews) <= 4096,
                'pr_collections_incomplete')
        for row in checks:
            require(type(row) is dict and row.get('kind') in {'check_run', 'commit_status'}, 'pr_check_invalid')
            fields = {'kind', 'id', 'name', 'appId', 'head', 'status', 'conclusion', 'startedAt', 'completedAt', 'url'} \
                if row['kind'] == 'check_run' else {'kind', 'id', 'name', 'creator', 'head', 'status', 'createdAt', 'url'}
            require(set(row) == fields, 'pr_check_invalid')
            require(type(row['id']) is int and row['id'] > 0 and type(row['name']) is str and 0 < len(row['name']) <= 255
                    and type(row['head']) is str and SHA.fullmatch(row['head'])
                    and (row['url'] is None or type(row['url']) is str and len(row['url']) <= 4096), 'pr_check_invalid')
            timestamps = ('startedAt', 'completedAt') if row['kind'] == 'check_run' else ('createdAt',)
            require(all(row[k] is None or type(row[k]) is str and len(row[k]) <= 100 for k in timestamps), 'pr_check_timestamp_invalid')
            if row['kind'] == 'check_run':
                require(type(row['appId']) is int and row['appId'] > 0 and type(row['status']) is str
                        and row['status'] in {'queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending'}
                        and (row['conclusion'] is None or type(row['conclusion']) is str and len(row['conclusion']) <= 100), 'pr_check_invalid')
            else:
                require(type(row['creator']) is str and 0 < len(row['creator']) <= 100
                        and row['status'] in {'pending', 'error', 'failure', 'success'}, 'pr_check_invalid')
        for row in reviews:
            require(type(row) is dict and set(row) == {'id', 'login', 'commit', 'state', 'submittedAt'}, 'pr_review_invalid')
            require(type(row['id']) is int and row['id'] > 0 and type(row['login']) is str and 0 < len(row['login']) <= 100
                    and type(row['commit']) is str and SHA.fullmatch(row['commit'])
                    and row['state'] in {'APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'}
                    and (row['submittedAt'] is None or type(row['submittedAt']) is str and len(row['submittedAt']) <= 100),
                    'pr_review_invalid')
        for required in request['requiredChecks']:
            identity = 'appId' if required['kind'] == 'check_run' else 'creator'
            matches = [r for r in checks if type(r) is dict and r.get('kind') == required['kind']
                       and r.get('name') == required['name'] and r.get(identity) == required[identity]]
            require(matches and all(type(r.get('id')) is int and r['id'] > 0 for r in matches), 'pr_required_check_missing')
            require(len({r['id'] for r in matches}) == len(matches), 'pr_check_ambiguous')
            require(all(r.get('head') == request['head'] for r in matches), 'pr_check_wrong_head')
            if required['kind'] == 'check_run':
                require(all(r.get('status') == 'completed' for r in matches), 'pr_required_check_unpassed')
                ordered = [(_time(r.get('completedAt')), r) for r in matches]
                require(all(when <= stamp + dt.timedelta(seconds=5) for when, _ in ordered), 'pr_check_timestamp_invalid')
                require(len({when for when, _ in ordered}) == len(ordered), 'pr_check_ambiguous')
                latest_check = max(ordered, key=lambda pair: pair[0])[1]
                require(latest_check.get('status') == 'completed' and latest_check.get('conclusion') == 'success', 'pr_required_check_unpassed')
            else:
                ordered = [(_time(r.get('createdAt')), r) for r in matches]
                require(all(when <= stamp + dt.timedelta(seconds=5) for when, _ in ordered), 'pr_check_timestamp_invalid')
                require(len({when for when, _ in ordered}) == len(ordered), 'pr_check_ambiguous')
                latest_check = max(ordered, key=lambda pair: pair[0])[1]
                require(latest_check.get('status') == 'success', 'pr_required_check_unpassed')
        for reviewer in request['requiredReviewers']:
            matches = [r for r in reviews if type(r) is dict and str(r.get('login', '')).casefold() == reviewer.casefold()]
            require(matches and all(type(r.get('id')) is int and r['id'] > 0 for r in matches), 'pr_required_review_missing')
            require(len({r['id'] for r in matches}) == len(matches), 'pr_review_ambiguous')
            effective = [r for r in matches if r.get('state') not in {'COMMENTED', 'PENDING'}]
            require(effective and all(r.get('state') in {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'} for r in effective),
                    'pr_required_review_unapproved')
            ordered = [(_time(r.get('submittedAt')), r) for r in effective]
            require(all(when <= stamp + dt.timedelta(seconds=5) for when, _ in ordered), 'pr_review_timestamp_invalid')
            require(len({when for when, _ in ordered}) == len(ordered), 'pr_review_ambiguous')
            review = max(ordered, key=lambda pair: pair[0])[1]
            require(review.get('commit') == request['head'] and review.get('state') == 'APPROVED', 'pr_required_review_unapproved')
        return _result(observationPlan=current, observationDigest=digest(observation), localGit=current['localGit'],
                       observedAt=stamp.isoformat(), maxAgeSeconds=policy(root, change)['policy']['maxAgeSeconds'],
                       provenance='observed_command_snapshot')
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as error:
        return _result('blocked', [getattr(error, 'code', str(error) if isinstance(error, inputs.EvidenceError) else 'pr_observation_invalid')])


def _canonical_observation(root, change, receipt_id):
    from cc_development_jobs import RECEIPTS, assess_job_observation
    require(type(receipt_id) is str and re.fullmatch(r'development_job_[0-9a-f]{32}', receipt_id), 'pr_receipt_invalid')
    receipt, sha = read_json(root, managed_relative(root, RECEIPTS) + '/' + receipt_id + '.json')
    record = {'receiptId': receipt_id, 'observation': receipt.get('observation')}
    result = assess_job_observation(root, record, change, 'pull_request')
    require(result['state'] == 'allowed', 'pr_job_unobserved')
    plan = receipt['plan'].get('pullRequestPlan', receipt['plan'].get('observationPlan'))
    assessment = assess_observation(root, change, plan, receipt['observation'], receipt.get('finishedAt'))
    require(assessment['state'] == 'allowed', assessment['reasons'][0] if assessment['reasons'] else 'pr_observation_blocked')
    # Runner additionally enforces latest-attempt ordering, including running/failures.
    return {'receiptId': receipt_id, 'receiptDigest': sha, 'assessment': assessment,
            'observation': receipt['observation'], 'plan': plan}


def assessment(root, change):
    try:
        selected = policy(root, change)
        if not selected['required'] or selected['state'] != 'allowed':
            return selected
        from cc_development_lib.evaluator import latest
        artifact = latest(change, 'review_release')
        choice = (artifact or {}).get('content', {}).get('observation_provenance')
        require(type(choice) is dict and set(choice) == {'mode', 'receiptId'} and choice['mode'] == 'github_pr', 'pr_observation_selection_missing')
        proof = _canonical_observation(root, change, choice['receiptId'])
        return _result(required=True, policyDigest=selected['policyDigest'], **proof)
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as error:
        return _result('blocked', [getattr(error, 'code', str(error) if isinstance(error, inputs.EvidenceError) else 'pr_assessment_unavailable')], required=True)


def _packet_binding(root, change, receipt_id, environment, artifact_digest):
    from cc_development_lib.evaluator import latest, accepted
    from cc_development_lib.evaluator import assess_receipts, evaluate
    require(type(environment) is str and 0 < len(environment) <= 200
            and type(artifact_digest) is str and re.fullmatch(r'[0-9a-f]{64}', artifact_digest), 'release_packet_target_invalid')
    artifact = latest(change, 'review_release')
    require(artifact and accepted(change, 'review_release') and accepted(change, 'verify'), 'release_packet_approval_missing')
    require(artifact['content'].get('environment') == environment
            and artifact['content'].get('release_artifact_identity') == artifact_digest, 'release_packet_target_unapproved')
    choice = artifact['content'].get('observation_provenance')
    require(choice == {'mode': 'github_pr', 'receiptId': receipt_id}, 'release_packet_observation_unselected')
    with inputs.SafeRoot(root) as reader:
        evidence = assess_receipts(reader, change)
        gate = evaluate(reader, change, through_stage='review_release')['review_release']
        reader.validate()
    require(evidence['state'] == 'allowed', 'release_packet_verification_unproved')
    require(gate['outcome'] == 'allowed', 'release_packet_review_gate_blocked')
    proof = _canonical_observation(root, change, receipt_id)
    decisions = [r for r in change['decisions'] if r['stage'] in {'verify', 'review_release'}]
    reviews = [r for r in change['reviews'] if r['candidate_identity'] == change['candidate']['digest']]
    require(reviews and reviews[-1]['actor'] != change['candidate']['actor'], 'release_packet_review_missing')
    require(not any(f['severity'] in {'major', 'critical'} and f['disposition'] != 'resolved' for f in reviews[-1]['findings']), 'release_packet_review_unresolved')
    return {'schemaVersion': 1, 'projectIdentity': proof['plan']['projectIdentity'], 'changeId': change['id'],
            'method': change['method'], 'candidateDigest': change['candidate']['digest'], 'environment': environment,
            'artifactDigest': artifact_digest, 'releaseRevision': artifact['revision'], 'releaseArtifactDigest': artifact['digest'],
            'decisionDigest': digest(decisions), 'reviewDigest': digest(reviews[-1]), 'evidenceDigest': digest(evidence),
            'policyDigest': proof['plan']['policyDigest'], 'localGit': proof['assessment']['localGit'],
            'receiptId': receipt_id, 'receiptDigest': proof['receiptDigest']}


def packet_preview(root, change, receipt_id, environment, artifact_digest):
    binding = _packet_binding(root, change, receipt_id, environment, artifact_digest)
    result = {'binding': binding, 'effect': 'Retain the reviewed release packet. No deployment or publication occurs.'}
    result['approval_id'] = digest(result)
    result['packetId'] = 'release_packet_' + digest(binding)[:32]
    return result


def packet_prepare(root, change, receipt_id, environment, artifact_digest, approval_id):
    import cc_development_lib as core
    from cc_development_lib.storage import load, transaction
    core.check_root(root)
    with inputs.SafeRoot(root) as reader:
        with transaction(reader, operation_id='release_packet'):
            state, _ = load(reader, locked=True)
            matches = [r for r in state['changes'] if r['id'] == change['id']]
            require(len(matches) == 1, 'release_packet_change_missing')
            preview = packet_preview(root, matches[0], receipt_id, environment, artifact_digest)
            require(preview['approval_id'] == approval_id, 'preview_mismatch')
            relative = managed_relative(root, PACKETS) + '/' + preview['packetId'] + '.json'
            item = reader.read(relative, inputs.Budget(), missing=True)
            if item:
                return packet_inspect(root, preview['packetId']) | {'replayed': True}
            packet = {'schemaVersion': 1, 'id': preview['packetId'], 'binding': preview['binding'],
                      'approvalId': approval_id, 'createdAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'status': 'prepared'}
            packet['integrity'] = digest(packet)
            reader.write_atomic(relative, packet, replace_existing=False)
            reader.validate()
            return packet


def packet_inspect(root, packet_id):
    require(type(packet_id) is str and re.fullmatch(r'release_packet_[0-9a-f]{32}', packet_id), 'release_packet_id_invalid')
    value, _ = read_json(root, managed_relative(root, PACKETS) + '/' + packet_id + '.json')
    require(value.get('id') == packet_id and value.get('schemaVersion') == 1
            and value.get('integrity') == digest({k: v for k, v in value.items() if k != 'integrity'}), 'release_packet_changed')
    require(packet_id == 'release_packet_' + digest(value['binding'])[:32]
            and value['approvalId'] == digest({'binding': value['binding'],
                'effect': 'Retain the reviewed release packet. No deployment or publication occurs.'}), 'release_packet_approval_changed')
    from cc_development_lib.storage import identity
    with inputs.SafeRoot(root) as reader:
        require(value['binding']['projectIdentity'] == identity(reader), 'release_packet_wrong_project')
    return value


def packet_assess(root, change, packet_id):
    try:
        packet = packet_inspect(root, packet_id)
        before = packet['binding']
        now = _packet_binding(root, change, before['receiptId'], before['environment'], before['artifactDigest'])
        require(now == before, 'release_packet_stale')
        return _result(packetId=packet_id, binding=now, status='prepared', deploymentObserved=False)
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as error:
        return _result('blocked', [getattr(error, 'code', str(error) if isinstance(error, inputs.EvidenceError) else 'release_packet_unavailable')], packetId=packet_id)


def packet_reports(root, change):
    """Bounded historical packets with separately calculated current assessment."""
    relative = managed_relative(root, PACKETS)
    try:
        with inputs.SafeRoot(root) as reader:
            try:
                entries = reader.listing(relative, inputs.Budget(entries=256))
            except FileNotFoundError:
                return []
            names = sorted(name for name, _ in entries if re.fullmatch(r'release_packet_[0-9a-f]{32}\.json', name))
            reader.validate()
        reports = []
        for name in names:
            packet_id = name[:-5]
            try:
                packet = packet_inspect(root, packet_id)
                if packet['binding']['changeId'] != change['id']:
                    continue
                current = packet_assess(root, change, packet_id)
                reports.append({'packetId': packet_id, 'status': 'prepared', 'deploymentObserved': False,
                                'binding': packet['binding'], 'state': current['state'], 'reasons': current['reasons']})
            except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as error:
                reports.append({'packetId': packet_id, 'status': 'unknown', 'deploymentObserved': False,
                                'state': 'blocked', 'reasons': [getattr(error, 'code', 'release_packet_unreadable')]})
        return reports
    except (OSError, ValueError, TypeError, inputs.EvidenceError):
        return [{'packetId': None, 'status': 'unknown', 'deploymentObserved': False,
                 'state': 'blocked', 'reasons': ['release_packet_collection_unavailable']}]
