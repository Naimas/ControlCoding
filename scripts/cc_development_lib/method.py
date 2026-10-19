"""Pinned, executable local development method (no optional dependencies)."""
import hashlib
import json
from pathlib import Path

STAGES = ('intent', 'design', 'plan', 'build', 'verify', 'review_release', 'maintain')
FIELDS = {
    'intent': ('problem', 'outcome', 'affected_users_and_systems', 'constraints', 'non_goals', 'open_questions', 'success_measures'),
    'design': ('upstream_intent_revision', 'requirements', 'criteria', 'decisions', 'risks', 'policy_concerns', 'source_references'),
    'plan': ('upstream_specification_revision', 'files', 'ownership_scopes', 'tasks', 'dependencies', 'criterion_check_mapping', 'verification_strategy', 'rollback_assessment'),
    'build': ('approved_plan_revision', 'source_identity', 'execution_provenance', 'deviations', 'changed_files'),
    'verify': ('candidate_identity', 'criterion_coverage', 'required_contract_results', 'freshness', 'unresolved_failures'),
    'review_release': ('candidate_identity', 'review_findings', 'finding_resolutions', 'required_approvals', 'environment', 'release_artifact_identity', 'rollback_readiness', 'observation_provenance'),
    'maintain': ('deployed_artifact_identity', 'observation_scope', 'observation_window', 'signals', 'findings', 'dispositions', 'followup_owners'),
}
PROFILES = ('routine', 'standard', 'critical')
METHOD = {'schema_version': 1, 'id': 'controlcoding-controlled-development', 'version': '0.11.0',
          'evaluator_version': '1', 'stages': list(STAGES), 'artifact_fields': FIELDS,
          'profiles': list(PROFILES), 'provenance': 'local_revision',
          'evidence_policy': 'owner_assessed_current_complete', 'invalidation': 'all_downstream',
          'identity_policy': 'local_actor_labels_are_not_authenticated',
          'agent_evaluation_policy': 'versioned_tasks_trusted_file_grading_current_configuration',
          'execution_policy': 'qualified_proposal_worker_separate_reviewed_scoped_gateway',
          'release_policy': 'opt_in_current_pr_snapshot_reviewed_packet_bound_target',
          'operations_policy': 'canonical_durable_incidents_reviewed_linked_intent_bounded_diagnosis',
          'history_policy': 'reviewed_checkpoint_bounded_hot_replay_exact_retained_history'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def implementation_identity():
    """Pin trusted installed owner bytes independently of the project cwd."""
    from cc_evidence_inputs import SafeRoot, Budget
    owner = Path(__file__).absolute().parent.parent
    package = ('method.py', 'schema.py', 'evaluator.py', 'storage.py', 'policy.py', '__init__.py')
    dependencies = ('cc_development_adapters.py', 'cc_development_jobs.py', 'cc_development_eval.py', 'cc_layout.py',
                    'cc_development_execution.py', 'cc_development_gateway.py', 'cc_development_codex.py',
                    'cc_development_release.py', 'cc_development_github.py',
                    'cc_development_operations.py', 'cc_development_diagnosis.py',
                    'cc_development.py', 'cc_development_archive.py',
                    'cc_development_store.py', 'cc_development_compatibility.py',
                    'cc_development_migration.py', 'cc_development_sessions.py', 'cc_development_bundle.py',
                    'cc_development_history.py', 'cc_product.py', 'cc_product_evolution.py', 'cc_product_documents.py', 'cc_product_adoption.py', 'cc_product_planning.py', 'cc_product_cli.py',
                    'cc_planning.py', 'cc_planning_cli.py',
                    'cc_evidence.py', 'cc_evidence_inputs.py', 'cc_evidence_process.py',
                    'cc_product_progress.py', 'cc_product_progress_contract.py', 'cc_product_progress_observation.py', 'cc.py')
    files = ['cc_development_lib/' + name for name in package] + list(dependencies)
    with SafeRoot(owner) as root:
        budget = Budget(total=4 * 1024 * 1024, per_file=2 * 1024 * 1024, entries=48)
        values = []
        for name in files:
            budget.entry()
            raw, info = root.read(name, budget)
            if info.st_nlink != 1:
                raise ValueError('unsafe_method_implementation')
            values.append([name, hashlib.sha256(raw).hexdigest()])
        root.validate()
    return digest(values)


IMPLEMENTATION_DIGEST = implementation_identity()
IDENTITY = {'id': METHOD['id'], 'version': METHOD['version'], 'digest': digest([METHOD, IMPLEMENTATION_DIGEST]),
            'evaluator_version': '1', 'implementation_digest': IMPLEMENTATION_DIGEST}
