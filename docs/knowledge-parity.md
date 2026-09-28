# ControlWork Work Plane parity in ControlCoding

ControlCoding embeds the portable ControlWork Work Plane under `CONTROLWORK.md`
and `.controlwork/`. The canonical compatibility contract is
`controlwork-work-plane/1.0.0` in the separate ControlWork repository. The
ControlCoding profile pins that identity in `controlcoding.release.json`.

`tests/knowledge_controlwork_parity.py` runs the current standalone `cw.py` and
embedded `cc memory work-*` commands against separate disposable projects. It
records SHA-256 hashes for the contracts, release manifest, CLI entrypoints,
probe, and both products' Python modules before execution, then verifies those
sources did not change during the run. It compares the same portable source
corpus, command results, owned records, and generated projections. A standalone
import into an embedded project must preserve portable files byte for byte;
the embedded distribution intentionally rewrites `.controlwork/config.json`.

The current bounded matrix has 48 passing comparisons. It covers initialization,
scan, analysis, status, review queue and decision, a blocked and an overridden
promotion, Markdown source import, manual PDF OCR sidecar import, categories,
capture, session continuity, views, retrieval, ranked query matches, RAG packet,
graph status/explain/path/suggestion acceptance, context packet, wiki build and
edit review, Obsidian init/check, JSON dashboard, handoff, read-only MCP
inspection/search, checkpoint semantics, portable records, and standalone
import. The receipt is stored as `run-*/receipt.json` under the supplied output
directory, including exact source hashes and per-command results.

The expanded comparison found a capture side effect: standalone capture
refreshed the portable file index, while embedded capture did not. Embedded
`work-capture` now refreshes that index before writing its record. The fixture
checks that both distributions refresh it and retain the same review statuses,
source hashes, and imported record references.

Fresh `init` commands generate different `CONTROLWORK.md` prose for the two
distribution profiles. This changes default retrieval: in the measured fixture,
`Bridge Workflows` has no standalone match and one embedded match from the
embedded template. Both outputs, with timestamps and fixture paths normalized, are retained in the receipt under
`profileVariances`. For shared-operation comparison, the probe supplies the
same canonical context text to both disposable projects and rescans them. The
passing matrix therefore establishes parity for identical portable inputs, not
identity of freshly initialized default content.

The probe does not cover every Work Plane command. In particular, it does not
exercise batch or relational scan review, a configured OCR runtime, every graph
action, category mutations, every MCP call, migration of arbitrary older
archives, ControlCoding's separate knowledge archive, AI answer quality, or
host adapter behavior. Its receipt includes a `commandFamilies` inventory of
covered and untested actions. Run it against a readable standalone ControlWork checkout
beside ControlCoding, or set `CC_CONTROLWORK_ROOT` to its location. Use Python
3.11 or later and supply an output directory outside both source repositories.
For example, in PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$parityRoot = Join-Path $env:TEMP "controlcoding-parity"
python tests/knowledge_controlwork_parity.py --output-root $parityRoot
python -m pytest tests/test_cc_knowledge_controlwork_parity.py -q --basetemp (Join-Path $parityRoot "pytest-temp")
```

The probe exits nonzero if a shared operation or source-stability check fails.
The receipt names each failed comparison and preserves the source hashes needed
to reproduce its exact scope.
