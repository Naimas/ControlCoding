# Feature State Machine

ControlCoding can track the current feature as a local work contract under
`.controlcoding/features/features.json`.

This is separate from module feature locks, mini-design slices, verification
contracts, invariants, and promotion manifests:

- Mini-design slices explain what should be built and why.
- `cc feature` tracks the current bounded feature and WIP state.
- `cc verify` and `cc invariants` define project-wide verification suites.
- `cc promote` moves mature work through workspace, features, shared, and stable
  zones.

## Commands

Initialize the registry:

```bash
python scripts/cc.py feature init --project-root .
```

Start one active feature:

```bash
python scripts/cc.py feature start payment-memory --project-root . \
  --title "Payment memory contract" \
  --objective "Keep payment memory work bounded and verifiable" \
  --acceptance "Feature has a local work contract" \
  --check "python -m pytest tests/test_cc_cli.py"
```

`feature start` enforces WIP=1 across `active`, `verifying`, `passing`, and
`blocked` features. Complete or abort the current feature before starting a
different one.

Record verification:

```bash
python scripts/cc.py feature verify payment-memory --project-root . \
  --run "python -m pytest tests/test_cc_cli.py" \
  --evidence "targeted tests passed"
```

When all `--run` commands pass, the feature moves to `passing` and the command
writes a verification receipt. Manual evidence without a command moves the
feature to `verifying`; it does not allow completion.

Complete a passing feature:

```bash
python scripts/cc.py feature complete payment-memory --project-root . \
  --summary "Implemented and verified the feature contract"
```

Abort a feature:

```bash
python scripts/cc.py feature abort payment-memory --project-root . \
  --reason "Superseded by a narrower plan"
```

Inspect state:

```bash
python scripts/cc.py feature status --project-root .
python scripts/cc.py feature show payment-memory --project-root .
```

The feature state is also visible in operational surfaces:

```bash
python scripts/cc.py doctor --project-root . --json
python scripts/cc.py memory op-index --project-root . --topic "payment memory"
python scripts/cc.py memory startup --project-root . --topic "payment memory"
```

These commands read the registry only. They do not start, verify, complete, or
abort feature work.

## State Model

| State | Meaning |
|---|---|
| `active` | The feature is the current WIP item. |
| `verifying` | Manual evidence exists, but no passing command receipt exists yet. |
| `passing` | At least one verification command ran successfully for this feature. |
| `blocked` | A verification command failed. |
| `completed` | The feature was completed after reaching `passing`. |
| `aborted` | The feature was intentionally stopped. |

The current implementation is ControlCoding-local. The same contract shape is
portable enough for ControlWork later, but ControlWork parity should be added as
a separate slice so Project Plane behavior stays deliberate.
