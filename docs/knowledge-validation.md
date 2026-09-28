# Knowledge validation workflows

These workflows record evidence, including failures. They do not grant release
acceptance or replace the required human review and elapsed adoption period.

## Real document retrieval comparison

Prepare a draft question JSON with `schema_version: 1`, an honest `label_origin`,
and `questions`. Each question has `id`, `question`, `split`, `category`,
`answerable` and `expected`. Each expected entry identifies a project-relative
`path` and a unique exact `quote`. Use the categories and split names in the
[evaluation schema](knowledge-evaluation.md). Include at least 120 distinct
questions, all seven categories, negatives and multi-source questions. Validate
the labels independently before treating them as acceptance evidence.

```powershell
python -B tests/knowledge_real_evaluation.py --project PROJECT --questions QUESTIONS.json --output NEW_EXTERNAL_DIRECTORY --embedding bge-m3:latest
```

Omit `--embedding` for offline lexical comparisons. Specifying it authorizes
loopback inference using the named already installed Ollama model; the script
does not install or download anything. It creates a new disjoint external copy
of authorized root/docs text sources and leaves live project memory unchanged.
It checks original byte hashes and the reconciled source manifest, then binds
labels to actual persisted passages. Ambiguous quotes fail instead of guessing.

The runner records a lexical top-eight reference, the shipped lexical retriever
with graph metadata and, when selected, the shipped semantic hybrid. Product modes
now use up to ten query-ranked passages; older receipts used six ranked passages
plus up to two neighbor chunks. This compares shipped behaviors,
not a controlled graph-only ablation. Hybrid fallback is an error, never silently
reported as neural retrieval. Emitted anchors are checked against exact frozen
path, source ID, revision, line interval and excerpt. The isolation observation
only detects citations outside the frozen scope; an adversarial leakage corpus
remains a separate requirement.

Outputs include frozen source copies, corpus/passages, per-method run/score JSON,
embedding identity/build timing and a `review.html` page with method names hidden.
The page contains formatted question, answer and source excerpts. A human selects
Supported/Not supported for each citation and downloads their entered judgments.
Unselected citations remain pending. Keep `review-key-private.json` separate from
the blinded reviewer; it maps opaque review identifiers to run/question IDs.

```powershell
python -B scripts/cc_knowledge_review.py --key review-key-private.json --review human-review.json --run run-hybrid.json
```

Save the resulting stdout JSON as the grades file for the evaluator. Conversion
requires an exact run fingerprint and never generates judgments. Human names and
blinding are process evidence, not authenticated identities. Agent-authored labels
and already inspected held-out results require a new independent holdout before
any tuning-dependent quality claim. Do not adjust thresholds to hide failures.

## Local-model answer evaluation

`ui/tests/knowledge-quality-local.cjs` evaluates the fixed question set with the
installed local Concierge model and its saved timeout/output settings. Supply
an isolated frozen runtime, a new copy of the frozen project, and a new output
directory. The evaluator disables answer archival only in its own process and
checks source, passage, edge and conversation identities after every question.
This prevents generated answers from becoming evidence for subsequent cases.
Do not reuse a project copy from an experiment that archived generated answers.

The runner preserves failures and the initial/follow-up evidence separately.
The first twelve abstentions or empty retrievals may receive one bounded deeper
search. This is a predeclared diagnostic sample, not full follow-up coverage.
`tests/knowledge_quality_report.py` produces response counts and a human review
sheet; it never substitutes citation presence for factual correctness. Exposed
development questions remain exposed even after a clean rerun. A new independent
holdout and actual human grades remain separate acceptance requirements.

## Cross-process conversation preservation

```powershell
python -B tests/knowledge_continuity_acceptance.py NEW_EXTERNAL_FIXTURE_DIRECTORY
```

The explicit benchmark writes twenty curated summary checkpoints through the
real service, closes the writer, and reopens each checkpoint from a fresh Python
process. It checks source identity, revision, exact summary, decision and blocker.
The JSON contains measured automated service/process timings and failure details.
`--check` reruns an existing fixture. No model calls are made. These are preservation
checks, not twenty observed human task resumptions or measured time savings.

For the human study, predeclare twenty real tasks, their accepted decisions and
blockers, the comparison baseline and the timing protocol. Record actual start/
end times, missed blockers and decision errors for both workflows. Counterbalance
order and use equivalent tasks to reduce learning effects. Missing measurements
stay blank; process startup milliseconds must never replace human resume times.

## Seven-day adoption observations

```powershell
python -B scripts/cc_knowledge_adoption.py observe --project PROJECT --kind software --note "Describe the actual activity and checks"
python -B scripts/cc_knowledge_adoption.py summarize RECEIPT_1.json RECEIPT_2.json
```

Use `--kind documents` for a distinct document-only adopter project. Redirect
stdout to unique receipt files outside the projects. Observe actual use daily
and after restarts, source changes and recovery exercises. The tool reads existing
SQLite archives in read-only mode and does not initialize or configure a project.
Missing archives are explicit, not healthy observations. Each receipt records
UTC time, project identity, counts, a structural digest and consistency flags.
The digest is a structural observation, not a complete backup or proof of no loss.

The summary requires at least 168 elapsed hours for each project and observations
in each of seven daily windows. It rejects duplicate IDs and future timestamps.
It reports only eligibility for human review; acceptance always remains pending.
Receipt contents and historical timestamps are not authenticated. Real activity,
preservation checks, backup/restore and issue disposition still need review.
No background schedule is installed by these commands, and no elapsed days are
simulated. An initial observation starts the evidence record, not the acceptance.
