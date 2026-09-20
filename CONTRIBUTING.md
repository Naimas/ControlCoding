# Contributing to ControlCoding

Thanks for your interest in contributing. This guide covers setup, testing, and PR guidelines.

## Prerequisites

- Python 3.11+ for Core; memory requires a working SQLite deserialize API
- Git
- Git Bash or WSL on Windows (Unix shell commands in scripts)

## Local Setup

```bash
git clone git@github.com:Naimas/ControlCoding.git
cd ControlCoding
```

Hooks need no pip dependencies (stdlib only). For development, use a dedicated
Python 3.11+ environment. The CI lock includes the test tools, MCP dependencies
and build tools:

```bash
python -m pip install --require-hashes --only-binary=:all: -r .github/requirements-ci.lock
python -m pip install --no-deps --no-build-isolation ".[mcp]"
```

## Running Tests

```bash
python -m pytest tests/ -v
```

Resolve failures before submitting a PR and document platform-dependent skips.
Test counts change with the source tree and interpreter; use the actual run's
summary rather than a historical total.

The default required checks are defined in `controlcoding.verification.json`:

```bash
python scripts/cc.py verify status --project-root .
python scripts/cc.py verify run --project-root . --json
```

Run these in an authorized development checkout: verification writes receipts
under `.controlcoding/`. Required checks cover docs/invariants, CLI/CI contracts,
core regressions, setup/runtime and optional-hook regressions. A default run
selects required suites only; `--all` also selects optional checks. This contract
is a maintained subset of `tests/`, not the full test suite. Generated contracts
include the setup/runtime and optional-hook targets only when those files exist.

CLI/repository-hygiene tests run in `cli-regression` with a 390-second limit.
Verification evidence, CI configuration, and public-example tests run separately
in required `verification-support-regression`, using the 300-second default.
Each group keeps its own JUnit report; both must pass for a complete required gate.
Generated contracts add the support group only for available supporting files
when selecting CLI-specific targets. A generic `tests` fallback does not add a
second support group. This split bounds each process independently; it is not a
guarantee of hosted timing or evidence that an interrupted run passed.

Core memory, hook, session and organization tests remain together in required
`core-governance-regression`, with a suite-specific 420-second limit in tracked
and generated contracts. Its targets, order and JUnit report are unchanged.
This finite allowance addresses repeated exhaustion of the previous 300-second
limit; it does not establish the cause or guarantee hosted completion. The global
default remains 300 seconds and the CLI limit remains 390. A core timeout still
produces incomplete evidence and fails the current-required gate.

Quick smoke test (fastest subset):

```bash
python -m pytest tests/test_check_boundaries.py tests/test_check_dangerous_commands.py tests/test_session_end_check.py -v
```

This smoke subset is useful during development; it does not satisfy the required
verification contract or replace full-suite review.

### CI configuration and diagnostics

The workflow is configured for GitHub-hosted Ubuntu and Windows with Python 3.11
and 3.13. Each job also provisions Python 3.10 and supplies its executable to the
unsupported-runtime rejection test through `CC_TEST_PYTHON310`. Configuration
alone is not evidence that these jobs or the rejection test have passed. A
maintainer must review the actual matrix results and every skip. Host-specific
hook delivery requires separate validation on a named host/version.

The CI verification step isolates fixture Git configuration from the runner's
system/global settings (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL` set to the
platform's null device, and `GIT_CONFIG_COUNT=0`). Local repository settings and
attributes remain effective, including filters configured by regression tests.
Use the same process-local isolation when reproducing CI locally. Ambient EOL
normalization can otherwise make a fixture's raw worktree bytes differ from its
index before a hook runs. Do not change your global Git configuration for this.

Pytest fixtures use pytest's external temporary root (or an explicitly supplied
`--basetemp`) and compact per-test names to keep Windows Git paths bounded.
Keep `TEMP`/`TMP` or an explicit test base outside the source checkout. Pytest
owns fixture retention and cleanup; the verifier's bounded attempt directory
remains separate from the fixture tree. Do not place fixture bases inside the
project when running the public-example checks.

Each pytest suite writes JUnit XML, including failed-test captured output, to
`.controlcoding/verification_receipts/<suite-id>.xml`. These reports survive the
verifier's temporary-directory cleanup; schema-v2 JSON receipts retain output
metadata and empty compatibility tail fields. A rerun overwrites the XML for that suite, so match reports to the
current run rather than treating an older file as fresh evidence. These XML files
are diagnostics, not source attestations or replacements for JSON receipts.

Receipt outcomes distinguish complete required runs from explicit subsets.
`verify status --require-current` and `invariants status --require-current` require
a complete pass matching current source, contract and execution context. Ordinary
status and existing doctor strict flags retain configuration-only semantics.
See [verification evidence](docs/verification-evidence.md) for input exclusions,
bounds, legacy receipts, output privacy and platform limitations.

CI uploads verification JSON/XML, invariant JSON and `ci-environment.json` using
an explicit path allowlist, including on failure, with 14-day retention. The
environment record includes the checked-out commit, runtime and SQLite versions,
installed distributions and hashes of the workflow, lock and verification
contract. It does not attest all source bytes. Only fixture output intended for
public CI logs belongs in test diagnostics. An early setup failure may have no
reports; inspect the job log in that case.

### Updating CI pins

CI installs `.github/requirements-ci.lock` with hashes and binary distributions,
then installs this checkout without dependency resolution or build isolation.
Direct dependency choices are in `.github/requirements-ci.in`. Review changes to
those roots and the resolved transitive versions; no pin is a claim of permanent
compatibility or security. With an existing uv installation and Python 3.11,
regenerate into a temporary directory outside the repository:

```bash
uv pip compile .github/requirements-ci.in --python-version 3.11 --universal --generate-hashes --no-python-downloads --no-config --no-build --default-index https://pypi.org/simple --no-emit-index-url --output-file /path/to/workbench/requirements-ci.lock
```

Keep the uv cache in the same external workbench. Use the system certificate
store if your environment requires it (`--system-certs`); do not disable TLS
verification. Before importing the lock, replace machine-specific command header
paths with the two portable header comments used in the checked-in lock, recording
the uv version actually used. Review the version/hash diff and validate installation
and tests in all four clean CI jobs. Local tests in an existing environment do not
verify installation of the lock. See the [uv compilation documentation](https://docs.astral.sh/uv/pip/compile/).

Action references use full commit SHAs. When updating one, verify the commit
against the official Action release and review its runtime requirements and
changes. See GitHub's [secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use).
Required-check enforcement is a separate repository ruleset or branch-protection
setting; adding the workflow does not enable it.

## Project Structure

| Directory | Status | Description |
|-----------|--------|-------------|
| `templates/hooks/` | Stable | Hook infrastructure - do not modify without approval |
| `templates/CLAUDE.md.template` | Shared | Adopter-facing template |
| `docs/` | Shared | User documentation |
| `scripts/` | Features | CLI tool, MCP servers, utilities |

See `CONTROLCODING.md` for the public architecture, behavior boundaries, and
release contract.

## Testing Hooks Manually

Hooks receive JSON on stdin. To test a hook outside Claude Code:

```bash
# Test boundary enforcement - should block (exit 2)
echo '{"tool_name":"Edit","tool_input":{"file_path":"check_boundaries.py"}}' | python templates/hooks/check_boundaries.py

# Test boundary enforcement - should allow (exit 0)
echo '{"tool_name":"Edit","tool_input":{"file_path":"README.md"}}' | python templates/hooks/check_boundaries.py

# Test dangerous command blocking
echo '{"tool_name":"Bash","tool_input":{"command":"git push --force"}}' | python templates/hooks/check_dangerous_commands.py
```

Exit code 0 = allow, 2 = block. Hooks must never exit 1.

## Protected Zones

`templates/hooks/` is stable infrastructure and its self-protection is
hardcoded. Changes require explicit maintainer approval and targeted hook
regression tests.

## Areas Needing Contributions

- Bug fixes in hooks or CLI tool
- New test cases for edge cases in boundary enforcement
- Cross-tool compatibility testing (Cursor, Windsurf, Codex, etc.)
- Cookbook examples for new frameworks/languages (`docs/ccdocs/cookbook.md`)
- Cross-platform fixes (Windows, macOS, Linux compatibility)
- Documentation improvements (clarity, examples, typos)

## Pull Request Format

1. One concern per PR
2. Include a short description of what and why
3. Reference any related issue
4. Ensure all tests pass (`python -m pytest tests/ -v`)

## Code Conventions

- Hooks: Python stdlib only, no pip dependencies
- Exit codes: 0 (allow) or 2 (block), never 1
- All user-facing files in English
- No em dashes - use " - " or restructure the sentence

## Questions?

Open an issue on [GitHub](https://github.com/Naimas/ControlCoding/issues).
