# Frozen knowledge quality evaluation

For real source freezing, blinded review export, continuity and adoption receipts,
see [validation workflows](knowledge-validation.md).

`scripts/cc_knowledge_evaluate.py` scores recorded results against a frozen corpus.
It is an offline, standard-library tool. It does not open project memory, call a
model, change sources or manufacture human review evidence.

```powershell
python -B scripts/cc_knowledge_evaluate.py --corpus CORPUS.json --run RUN.json --grades GRADES.json
```

The JSON report goes to stdout. Exit codes: `0` for satisfied numerical quality
thresholds, `1` for pending/failed thresholds, `2` for invalid inputs. Each input
is limited to 8 MiB. Duplicate JSON keys and non-finite numbers are rejected.
Keep private corpora, recorded answers and reports in an external workbench.

## Freeze before testing

Prepare questions and relevant evidence labels before running the candidate.
Separate calibration questions from held-out questions that are not used for
implementation tuning. Freeze original source bytes, locators, candidate version,
model/quantization, prompt, policies and hardware. A content hash binds packets;
it does not authenticate their author or prove they were frozen before a run.

The corpus packet uses this shape (the abbreviated example cannot pass the gate):

```json
{
  "schema_version": 1,
  "evidence": [
    {"id": "recovery-v1-lines-8-12", "source_sha256": "<64 lowercase hexadecimal characters>", "locator": "docs/recovery.md:8-12"}
  ],
  "questions": [
    {"id": "q001", "question": "How is a lost archive restored?", "split": "held_out", "category": "lookup", "answerable": true, "expected": ["recovery-v1-lines-8-12"]}
  ]
}
```

Evidence identifiers must identify frozen passages, not just a document title.
Include distractor passages in the evidence manifest too. All expected and
retrieved IDs must resolve to that manifest. Unanswerable questions have an empty
`expected` list; answerable questions require at least one relevant passage.

Required categories are `lookup`, `multi_source`, `history`, `contradiction`,
`current_vs_planned`, `work` and `unanswerable`. Both `calibration` and `held_out`
splits are required for readiness, with all categories present in held-out data.
At least 120 questions are required. This minimum alone does not establish a
representative or independently authored benchmark.

## Record a run

```json
{
  "schema_version": 1,
  "corpus_sha256": "<fingerprint of the complete corpus packet>",
  "metadata": {
    "candidate": "source tree digest and build identity",
    "model": "model, quantization and embedding identity, or lexical-only",
    "prompt": "prompt text or digest and token budget",
    "hardware": "CPU, RAM, GPU, operating system and runtime",
    "policy": "scope, retention and retrieval configuration"
  },
  "results": [
    {"id": "q001", "retrieved": ["recovery-v1-lines-8-12"], "abstained": false,
     "citations": [{"id": "S1", "valid": true}], "isolation_leaks": 0, "latency_ms": 25.0}
  ]
}
```

Record exactly one result per question. Retain answer text and the original
citations as additional result fields for review; these fields are also covered
by the run fingerprint. `valid` must come from resolving the emitted citation
against the frozen source revision and passage, not from the model's assertion.
`isolation_leaks` records observed excluded-scope disclosures. The scorer validates
these fields' types but does not independently inspect files or answers.

The current retrieval service emits up to ten query-ranked passages and keeps
graph links as metadata. It returns excerpts rather than an AI synthesis. Preserve that order
and count. Do not pad results to ten or equate returned excerpts with an evaluated
generated answer. Record separate runs for each actual baseline or ablation;
this tool does not implement new retrieval modes.

The configured Concierge can separately generate a cited draft or explicitly
abstain. Its exact `INSUFFICIENT_EVIDENCE` response is accepted without invented
citations; mixed markers, unknown citation IDs and truncated responses are rejected.
This protocol check does not prove entailment or a measured abstention rate.

Fingerprints use `knowledge_evaluation.fingerprint(packet)`: SHA-256 over JSON
with sorted keys, compact separators, UTF-8, `ensure_ascii=False` and no NaN.
Whitespace and object key order do not change the digest; array order does.

## Supply citation judgments separately

```json
{
  "schema_version": 1,
  "run_sha256": "<fingerprint of the complete run packet>",
  "reviewer": "reviewer identifier",
  "judgments": [
    {"question_id": "q001", "citation_id": "S1", "supported": true}
  ]
}
```

A human reviews whether each citation supports the associated answer or claim.
Use a blinded review process externally. A supplied reviewer name is not identity
verification, and agent-authored judgments must not be described as independent
human review. Any run edit invalidates its previous grade packet. Missing grades
remain pending; duplicate or unknown citation references are rejected.

## Read the result

Reports include overall, split and category metrics, numerator/denominator counts,
packet fingerprints and explicit pending/failing reasons. Recall@10 is the mean
fraction of expected evidence found among each answerable question's first ten
returned identifiers. It is not top-one accuracy. Empty denominators produce
`null`, never an automatic pass.

`quality_thresholds_met` requires complete held-out citation grading. As a
conservative evidence-completeness guard, every held-out answerable question must
also have a non-abstained response with citations; `answerable_response_coverage`
reports that fraction. Otherwise the packet stays incomplete even if its retrieval
recall is high. Required numerical thresholds are:

- evidence recall@10 at least 90%;
- correct abstention on unanswerable cases at least 95%;
- citation support among reviewed citations on answerable questions at least 95%;
- 100% reported valid citation anchors and zero reported isolation leaks.

Calibration metrics are reported separately and do not inflate held-out scores.
Latency is retained in each run observation; this scorer does not certify the
separate capacity/performance gate.

A passing report is only a check of supplied numerical evidence. It is not C11
completion, independent review, proof of source freshness, comparative advantage,
twenty measured handoffs, seven-day adoption evidence or release approval. The
five-question `tests/knowledge_ollama_evaluation.py` remains a small local-model
regression exercise, separate from the required frozen quality benchmark.
