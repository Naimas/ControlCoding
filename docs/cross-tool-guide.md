# ControlCoding - Cross-Tool Compatibility Guide

> ControlCoding was designed and validated on Claude Code. The methodology
> itself (condominium architecture, maturity levels, domain invariants,
> gradual adoption) is tool-agnostic. Only L2 hook enforcement depends on
> the specific tool. This guide documents what works, what needs adaptation,
> and what's missing for each major AI coding tool.
>
> Adaptations for tools other than Claude Code are reasonable extrapolations,
> not battle-tested workflows. If you adopt CC on another tool and find gaps
> or improvements, contributions are welcome.

---

## 0 Authorized Interfaces Only

ControlCoding is tool-agnostic, not auth-agnostic.

Compatibility in this guide assumes one of these paths:

- the tool's own official CLI or desktop product
- the vendor's official API
- a local runtime such as Ollama
- a vendor-approved connector or MCP flow inside the vendor's own product

This guide does **not** imply that you may:

- embed Claude.ai or ChatGPT consumer login inside a ControlCoding-owned interface
- reuse consumer OAuth or session tokens across third-party products
- treat "works with tool X" as permission to bypass the vendor's supported auth model

For vendor models used through third-party tools (for example Cline or Aider),
use official API credentials or another officially supported auth path for that
tool. If the auth path is unclear, treat it as unsupported for public CC docs.

## 0.1 User Host vs CC UI

The tools in this guide are **User Hosts**, not ControlCoding-owned UI surfaces.

ControlCoding's canonical stack is:

```text
User Host -> CC Structure -> CC Agents -> CC UI
```

Where:

- **User Host** is the user's chosen tool or IDE
- **CC Structure** is the mandatory ControlCoding core
- **CC Agents** are optional helpers invoked by that core
- **CC UI** is optional

Operational rule:

- On official CLI or other consumer-auth host paths, the canonical chat stays
  in the User Host. Any CC UI must remain observability/control only.
- On those official-CLI paths, orchestration and primary coding also stay in the
  User Host; CC UI should not become the initiating chat surface.
- On official API or local-runtime paths, CC UI may provide the primary chat
  surface in future/internal work because ControlCoding owns the integration
  layer. That is not part of the current public release path.
- In `visualizer` mode, the Concierge thread is only a local routed transcript
  with explicit origin labels. It may mirror activity from the User Host, CC
  UI, approvals, and workers, but it must not masquerade as the vendor chat.

Current public release note:

- `Core` stays centered on the chosen official host
- if `Agents` are used in the release story, keep them explicit on that host
- API/local CC-owned primary chat paths belong to later Version II / `Studio`
  work, not to the current public release

## 0.2 Public-Safe Surface Rules

For public-facing usage, treat every project/session as having exactly one
**Primary Surface**.

Everything else is **Observer-only**.

| Situation | Allowed primary | Observer surfaces | Guidance |
|---|---|---|---|
| Official host only | Official host | None | Default path for consumer-auth workflows |
| CC UI with API/local runtime only | CC UI | None | Supported architecture for future/internal API-local workflows, not the current public release default |
| Official host + CC UI both open | One chosen explicitly | The other surface | Recommended only with explicit primary/observer handling |
| Two official hosts open | One chosen explicitly | All others | Do not let both dispatch work |
| Official host + CC UI + another host | One chosen explicitly | All others | Fail closed if ambiguous |

Public-safe advice:

- keep consumer auth inside the vendor's official host
- keep the current public release path centered on the chosen official host
- treat CC UI primary-chat mode as future/internal API/local work, not as the current public default
- if a user opens multiple surfaces, require an explicit choice of the primary one
- if a second CC-aware surface wants control, require explicit takeover approval instead of silent reassignment
- if the primary surface is unclear, block orchestration rather than guessing
- when a host or wrapper is CC-aware, have it register itself explicitly through the local surface lock (`cc surface claim|observe`) instead of trying to infer authority from open windows
- for launchers, prefer `cc surface run ... -- <command>` so claim, heartbeat, and release stay tied to the process lifecycle
- sequential host hopping is allowed if each session still has one explicit primary host

This guide describes a conservative integration model meant to reduce ambiguity.
It is not a claim that vendor policy enforcement can never change.

---

## 1 Compatibility Matrix

Hosts are grouped here by **capability class**, not by brand prestige.

ControlCoding keeps the same structural slots on every serious host:

- `context_gate`
- `permission_gate`
- `inline_boundary_gate`
- `repo_boundary_gate`
- `review_gate`
- `verification_gate`

What changes by host is where those gates run.

### 1.1 Normalized Gate Vocabulary

| Gate | Meaning |
|---|---|
| `context_gate` | The host reads a context file derived from `CONTROLCODING.md`. |
| `permission_gate` | Sandbox, approval, or execution policy limits what the host can run. |
| `inline_boundary_gate` | A native hook blocks writes/commands before they happen. |
| `repo_boundary_gate` | Git pre-commit or equivalent blocks violations at repository level. |
| `review_gate` | CodeWarden or equivalent review pass inspects the work after meaningful changes. |
| `verification_gate` | Criteria tracking and invariant tests close the loop before acceptance/release. |

### 1.2 Capability Classes

| Class | Meaning | Primary boundary strategy | Review strategy |
|---|---|---|---|
| **A. Native inline hook hosts** | Can block before a write/command happens | `inline_boundary_gate` + `repo_boundary_gate` backstop | Native stop/plan hooks |
| **B. Sandbox / approval hosts** | Can constrain execution but not reproduce Claude-style file hooks | `permission_gate` + `repo_boundary_gate` | Post-commit / manual / CI |
| **C. Instruction-first hosts** | Mainly follow instructions from the context file | `repo_boundary_gate` as primary | Post-commit / manual / CI, treated as mandatory |

### 1.3 Documented Hosts

#### Class A - Native Inline Hook Hosts

| Tool | Origin | Context File | Permission Gate | Boundary Gates | Review Gate | CC Levels | Free |
|---|---|---|---|---|---|---|---|
| **Claude Code** | Anthropic (US) | `CLAUDE.md` | Official host permissions | Native hooks + git pre-commit backstop | Native hooks | L1-L4 | Free tier + paid |
| **Cline** | Open-source | `.clinerules` | Host/editor permissions | Native hooks on macOS/Linux; repo-side fallback on Windows | Native hooks on supported platforms, otherwise post-commit/manual | L1-L4 with platform caveat | Free (open-source) |

#### Class B - Sandbox / Approval Hosts

| Tool | Origin | Context File | Permission Gate | Boundary Gates | Review Gate | CC Levels | Free |
|---|---|---|---|---|---|---|---|
| **Codex CLI** | OpenAI (US) | `AGENTS.md` | Sandbox isolation + approvals | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free (open-source) |

#### Class C - Instruction-First Hosts

| Tool | Origin | Context File | Permission Gate | Boundary Gates | Review Gate | CC Levels | Free |
|---|---|---|---|---|---|---|---|
| **Gemini CLI** | Google (US) | `GEMINI.md` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free tier + paid |
| **GitHub Copilot** | GitHub/Microsoft (US) | `.github/copilot-instructions.md` (also reads `AGENTS.md`) | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free tier + paid |
| **Cursor** | Anysphere (US) | `.cursor/rules/*.mdc` | None | `.cursorignore` is partial; git pre-commit remains the primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free tier + paid |
| **Windsurf** | Codeium (US) | `.windsurfrules` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free tier + paid |
| **Amp** | Sourcegraph (US) | `AGENTS.md` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free tier + paid |
| **Aider** | Open-source | `CONVENTIONS.md` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free (open-source) |
| **OpenCode** | Open-source | `AGENTS.md` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free (open-source) |
| **Goose** | Block (US) | `.goosehints` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free (open-source) |
| **Trae** | ByteDance (CN) | `.trae/rules/project_rules.md` | None | Git pre-commit as primary repo boundary gate | Post-commit / manual / CI | L1, L3, L4 plus repo-side L2 adaptation | Free |

### Other Tools (not tested with CC)

The following tools have limited or undocumented context file support. CC has not been tested with them. Listed for awareness, not as a compatibility claim. For these tools, use their models through a documented Class A-C host (Cline, OpenCode, Aider, or similar) instead.

| Tool | Origin | Context Support | Notes |
|---|---|---|---|
| **MarsCode** | ByteDance (CN) | Project-level rules (undocumented format) | Use models via Cline/Aider |
| **CodeGeeX** | Zhipu AI (CN) | .codegeex/ config | Limited documentation |
| **Tongyi Lingma** | Alibaba (CN) | Limited | IDE plugin, primarily autocomplete |
| **GigaCode** | Sber (RU) | None documented | IDE plugin, primarily autocomplete |
| **YandexGPT Code** | Yandex (RU) | None documented | IDE plugin, limited agentic capability |

### Models (use through a documented host)

These are models, not tools. Use them through a documented host (Cline, OpenCode, Aider, or similar) to get CC support, using official API credentials or another clearly authorized path.

| Model | Origin | Best Agent For It | Notes |
|---|---|---|---|
| **DeepSeek V3/R1** | DeepSeek (CN) | Cline, Aider, OpenCode | Strong coding, free API tier |
| **Qwen3-Coder** | Alibaba (CN) | OpenCode, Aider | Good for local deployment |
| **Codestral** | Mistral (FR/EU) | Cline, Aider | European alternative, 32K context |
| **Llama 4** | Meta (US) | Cline, OpenCode | Open weights, local or cloud |
| **Gemma 3** | Google (US) | Aider, OpenCode | Smaller, fast, local-friendly |

---

## 2 CC Levels in the Normalized Gate Model

| Level | What it requires | How it maps across hosts |
|---|---|---|
| **L1 - Documented** | `context_gate` | Portable: derive the right host-native file from `CONTROLCODING.md` |
| **L2 - Enforced** | Strongest available boundary gate | Native inline hooks on Class A; repo-side boundary gate on Classes B/C |
| **L3 - Verified** | `verification_gate` | Portable: criteria tracking + invariant tests are tool-independent |
| **L4 - Isolated** | Feature locks, zone isolation, disciplined workflow | Portable as structure/convention; not dependent on one vendor host |

**Key insight**: the structure is portable even when the protection location changes. On Class A hosts, L2 is inline-first. On Class B/C hosts, L2 is repo-side and review-driven. `cc host status` and `cc doctor` are the canonical way to inspect the active protection model for a project.

---

## 3 Quick Setup per Tool

### 3.1 Claude Code (reference implementation)

This is the tool CC was designed for. Full support, no adaptation needed.

```
1. Create `CONTROLCODING.md` as the canonical project rules file
2. Sync `CLAUDE.md` from it for Claude Code
3. Copy templates/hooks/ to your project
4. Configure `.controlcoding/settings.json` (see `templates/hooks/settings.json.example`)
5. `inline_boundary_gate` and native `review_gate` run through Claude hooks
6. Keep git pre-commit as the repo-side backstop
```

Context file: `CLAUDE.md` at project root (also reads `CLAUDE.md` in subdirectories). In ControlCoding, `CLAUDE.md` is derived from canonical `CONTROLCODING.md`.
Auth note: use Claude Code as shipped by Anthropic. CC compatibility does not
include embedding Claude.ai login or reusing Claude consumer tokens in a
third-party product.

### 3.2 Codex CLI (OpenAI)

```
1. Run `cc setup` and choose `Codex CLI` as the user host
2. ControlCoding keeps `CONTROLCODING.md` as the canonical rules file and generates `AGENTS.md`
3. Codex reads `AGENTS.md` automatically (`context_gate`)
4. Codex sandbox/approval flow acts as the `permission_gate`
5. Use git pre-commit as the primary `repo_boundary_gate`
6. Use CodeWarden post-commit/manual/CI as the `review_gate`
7. Keep criteria/invariants active as the `verification_gate`
```

Context file: `AGENTS.md` at project root. Also reads `AGENTS.md` in subdirectories.
Manual preview path: `python scripts/cc.py export host-context --host codex_cli --preview-only`.
After review, `--force` may update only an already valid-owned adapter. Use the
explicit adoption flow below for an eligible historical unmarked or foreign file.
Codex runs commands in a sandboxed environment with network disabled by default.
Optional advanced path: `python scripts/cc.py write-path enable --mode patch_gateway --project-root .`
if you want an explicit CC-owned patch gateway for sensitive writes. This is
opt-in only and does not imply native inline parity.
Auth note: use official Codex CLI authentication or official OpenAI API
credentials as documented by OpenAI. Do not treat CC compatibility as permission
to reuse consumer credentials in unrelated third-party tools.

### 3.3 Gemini CLI (Google)

```
1. Run `cc setup` and choose `Gemini CLI` as the user host
2. ControlCoding keeps `CONTROLCODING.md` canonical and generates `GEMINI.md`
3. Gemini CLI reads it automatically (`context_gate`)
4. There is no native `inline_boundary_gate`
5. Use git pre-commit as the primary `repo_boundary_gate`
6. Use CodeWarden post-commit/manual/CI as the `review_gate`
7. Keep criteria/invariants active as the `verification_gate`
```

Context file: `GEMINI.md` at project root.
Manual preview path: `python scripts/cc.py export host-context --host gemini_cli --preview-only`.
After review, `--force` may update only an already valid-owned adapter. Use the
explicit adoption flow below for an eligible historical unmarked or foreign file.

### 3.4 GitHub Copilot

```
1. Create .github/copilot-instructions.md with your CC rules
2. Copilot reads it as custom instructions for the repository
3. This gives you the `context_gate`, not an `inline_boundary_gate`
4. Use git pre-commit as the primary `repo_boundary_gate`
5. Use CodeWarden post-commit/manual/CI as the `review_gate`
```

Context file: `.github/copilot-instructions.md`. Copilot coding agent also reads `AGENTS.md` (since August 2025) and `.github/instructions/*.instructions.md` for scoped rules.
Note: Copilot's instruction-following is less reliable than dedicated coding agents. Keep rules short and explicit.

### 3.5 Cursor

```
1. Create .cursor/rules/ directory
2. Add one .mdc file per concern (e.g., architecture.mdc, invariants.mdc)
3. Use .cursorignore to exclude protected files from AI edits
4. Treat `.cursorignore` as partial help only; git pre-commit remains the primary `repo_boundary_gate`
5. Use CodeWarden post-commit/manual/CI as the `review_gate`
```

Context file: `.cursor/rules/*.mdc` files. Cursor also reads `.cursorrules` at project root (legacy).
The `.cursorignore` file works like `.gitignore` syntax and prevents Cursor from reading or modifying listed files.

### 3.6 Cline (open-source)

```
1. Run `cc setup` and choose `Cline` as the user host
2. ControlCoding keeps `CONTROLCODING.md` canonical and generates `.clinerules`
3. On macOS/Linux, Cline v3.36+ gives you a native `inline_boundary_gate` and native `review_gate`
4. Copy templates/hooks/ and configure them in Cline's settings
5. On Windows, fall back to git pre-commit + post-commit/manual review
```

Context file: `.clinerules` at project root.
Manual preview path: `python scripts/cc.py export host-context --host cline --preview-only`.
After review, `--force` may update only an already valid-owned adapter. Use the
explicit adoption flow below for an eligible historical unmarked or foreign file.
Cline runs on VS Code and supports multiple backend models (Claude, GPT, DeepSeek, local models via Ollama).
**Note**: Cline hooks are currently supported on macOS and Linux only. On Windows, use git pre-commit hooks for L2 enforcement.

### 3.7 Aider (open-source)

```
1. Create CONVENTIONS.md at project root with your CC rules
2. Aider reads it automatically as project conventions
3. This gives you the `context_gate`, not an `inline_boundary_gate`
4. Use git pre-commit as the primary `repo_boundary_gate`
5. Use CodeWarden post-commit/manual/CI as the `review_gate`
```

Context file: `CONVENTIONS.md` at project root.
Aider works with many models (Claude, GPT, DeepSeek, Codestral, local models). Good option for using CC with non-standard models.

### 3.8 OpenCode (open-source)

```
1. Create AGENTS.md at project root with your CC rules
2. OpenCode reads it natively
3. This gives you the `context_gate`, not an `inline_boundary_gate`
4. Use git pre-commit as the primary `repo_boundary_gate`
5. Use CodeWarden post-commit/manual/CI as the `review_gate`
```

Context file: `AGENTS.md` at project root.
OpenCode is a terminal-based agent that supports multiple providers. Lightweight alternative for AGENTS.md-native workflows.

### 3.9 Self-Hosted Stack (fully free, fully local)

For a completely free and private setup with no cloud dependency:

```
1. Install Ollama (ollama.com) - local model runtime
2. Pull a coding model: ollama pull qwen3-coder (or codestral, deepseek-coder-v2)
3. Install an agent: Aider, OpenCode, or Cline (with local backend)
4. Create the appropriate context file (CONVENTIONS.md, AGENTS.md, or .clinerules)
5. Copy your CC rules into it
6. Use git pre-commit as the primary `repo_boundary_gate`
7. Use CodeWarden post-commit/manual/CI as the `review_gate`
```

**Recommended combo on supported platforms**: Cline + Ollama + Qwen3-Coder.
This gives you native hook support where Cline exposes hooks, with a local
model at zero cost. On platforms without Cline hook support, treat it as a
repo-side path.

**Limitations of local models**: smaller context windows (8K-32K vs 200K for cloud models), weaker instruction-following on long context files, slower on consumer hardware. Keep `CONTROLCODING.md` and the derived host files under roughly 200 lines for local models. Domain invariant tests (L3) work regardless of model quality since they are standard test suites.

---

## 4 AGENTS.md - The Cross-Tool Standard

AGENTS.md is a convention proposed by the Linux Foundation (December 2025) for providing project instructions to AI coding agents. It uses plain markdown, the same format as CLAUDE.md.

**Current adoption**: Codex CLI, OpenCode, Amp, and several other tools read AGENTS.md natively. The convention supports hierarchical instructions (AGENTS.md files in subdirectories override or extend the root file).

**Recommendation for CC adopters**:

- Keep `CONTROLCODING.md` as the canonical source of truth inside ControlCoding
- Generate host-native files such as `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, or `.clinerules` from that source
- Preview with `python scripts/cc.py export host-context --host <host> --preview-only` before a manual resync; `--force` is only for an already valid-owned adapter
- Use `python scripts/cc.py host switch <host>` when a project moves from one official host to another across sessions
- If you use multiple tools on the same project, maintain one source of truth and derive the other file names from it
- Do not maintain divergent instructions in multiple files - this defeats the purpose of a single source of truth
- Do not treat host hopping as permission to run multiple simultaneous primary chats on the same session

### Historical Adapter Migration

Historical host adapters must pass an ownership preflight before any write:

1. Preview the current state with
   `python scripts/cc.py context sync --host <host> --preview-only`.
2. If the target is `unmarked` or explicitly `foreign` and the preflight says it
   is safe to adopt, preview adoption with
   `python scripts/cc.py context adopt --host <host>`.
3. Apply that reviewed adoption explicitly with
   `python scripts/cc.py context adopt --host <host> --apply`.
4. Run `python scripts/cc.py context sync --host <host>` only after ownership is
   valid.

`context adopt` without `--apply` is preview-only. Invalid, ambiguous, and
unreadable or unsafe targets cannot be adopted. Normal sync and `--force` do
not overwrite unmarked or foreign files, and `--force` applies only to adapters
that are already valid-owned. This preserves the D3 ownership contract.

---

## 5 Repo-Side Boundary and Review Gates Without Native Hooks

For hosts that lack native inline hooks, ControlCoding does not disappear. The
boundary gate simply moves from inline execution to the repository/review layer.

Default strategy by capability class:

- **Class A**: native `inline_boundary_gate` first, git pre-commit as backstop
- **Class B**: `permission_gate` + git pre-commit as the primary boundary path
- **Class C**: git pre-commit as primary boundary path and CodeWarden review treated as mandatory

For tools that lack native pre-tool hooks, the most important substitute is git
pre-commit.

### Git Pre-Commit Hook Example

Create `.git/hooks/pre-commit` (or use a framework like pre-commit or husky):

```bash
#!/bin/bash
# Block commits that modify protected zones

PROTECTED_ZONES="src/stable/ src/core/ src/models/"

for zone in $PROTECTED_ZONES; do
    if git diff --cached --name-only | grep -q "^$zone"; then
        echo "CONTROL CODING: Blocked commit modifying protected zone: $zone"
        echo "If this is intentional, use --no-verify to bypass."
        exit 1
    fi
done
```

**Difference from native hooks**: git pre-commit catches violations at commit time, not at edit time. The AI can still modify protected files during a session; the repository gate just prevents those changes from becoming accepted history. Native hooks (Claude Code, Cline on supported platforms) are still stronger because they stop the edit before it happens.

### CI/CD as L2 Backup

For team projects, add a CI check that verifies no protected zones were modified without approval:

```yaml
# GitHub Actions example
- name: Check protected zones
  run: |
    CHANGED=$(git diff --name-only origin/main...HEAD)
    for zone in src/stable src/core; do
      if echo "$CHANGED" | grep -q "^$zone/"; then
        echo "::error::Protected zone modified: $zone"
        exit 1
      fi
    done
```

### CodeWarden Across Tools

CodeWarden (the guardian agent review hook) is tool-agnostic. The Python script
(`codewarden_review.py`) runs `git diff`, reads your context file, and calls an
LLM. Only the trigger mechanism changes per tool:

| Tool | Trigger | How to set up |
|---|---|---|
| Claude Code | Stop hook (native) | Already configured in `settings.json.example` |
| Cline | Stop hook (native, v3.36+) | Same config as Claude Code, in Cline settings |
| All other tools | Git post-commit hook | Copy `templates/hooks/codewarden_postcommit` to `.git/hooks/post-commit` |
| CI/CD | Pipeline step | Add `python hooks/codewarden_review.py` as a step after tests |
| Manual | Command line | Run `python hooks/codewarden_review.py` after any session |

**Plan review** (Claude Code, Cline only): CodeWarden can also review AI-generated
plans before code is written, via a PreToolUse hook on ExitPlanMode. This is
specific to tools with plan mode. See `settings.json.example` for configuration.

The git post-commit hook runs CodeWarden in the background after each commit, so
it doesn't slow down your workflow. The report is saved to
`.controlcoding/codewarden_report.md` and ready before your next session.

For the LLM backend, CodeWarden supports Ollama (free, local), Anthropic API,
and OpenAI-compatible APIs. See the script header for environment variable
configuration.

---

## 6 CC vs Codex Sandbox: Different Isolation Models

ControlCoding and Codex CLI approach code isolation differently. They are complementary, not competing.

**ControlCoding: semantic granularity.** CC uses path-based zones (stable/, shared/, features/) with selective boundary and review gates. On native-hook hosts that means inline enforcement; on non-inline hosts it means repo-side boundaries plus review/verification. Either way, the structure stays fine-grained: one module can write to `shared/scoring_utils.py` while another cannot.

**Codex: OS-level sandbox.** Codex runs code in an isolated container with network disabled by default. The AI has full write access inside the sandbox but cannot reach outside. This is coarse-grained: everything inside is allowed, everything outside is blocked.

| Dimension | ControlCoding | Codex Sandbox |
|---|---|---|
| Granularity | Per-file, per-zone, per-module | All-or-nothing container |
| Enforcement | Boundary + review gates (inline on Class A, repo-side on Classes B/C) | OS-level isolation (Docker/VM) |
| Read access | Unrestricted (AI reads full codebase) | Limited to sandbox contents |
| Write access | Selective (zone + module rules) | Full inside sandbox |
| Network | Controlled per connection (Section 9.13) | Disabled by default |
| Multi-agent | Per-agent module perimeter (feature lock) | Per-container isolation |
| Trade-off | Relies on hook protocol (bypassable via Bash) | No bypass, but no fine-grained control |

**Using both together:** CC inside a Codex sandbox gives defense in depth. The sandbox prevents escape, while CC provides the repo boundary gate, review gate, and verification gate for intra-project structure. Put the project in the Codex sandbox, keep `AGENTS.md` synced from `CONTROLCODING.md`, and rely on pre-commit + CodeWarden + verification instead of pretending Codex has Claude-style inline hooks.

---

## 7 Representative Validation Matrix

This is the representative smoke matrix CC should keep green as host support evolves.

| Capability class | Representative host | Expected context file | Expected protection model | Expected primary boundary gate | Expected review gate |
|---|---|---|---|---|---|
| Class A - native inline hook | `claude_code` | `CLAUDE.md` | `inline_first` | `inline_boundary_gate` via native hooks | Native stop/plan hooks |
| Class B - sandbox / approval | `codex_cli` | `AGENTS.md` | `repo_side` | `permission_gate` + git pre-commit | Post-commit / manual / CI |
| Class C - instruction-first | `gemini_cli` | `GEMINI.md` | `review_driven` | Git pre-commit | Post-commit / manual / CI |

The practical smoke flow per representative host is:

1. `cc setup` or `cc host switch <host>` writes the right `hostProfile` and host-native context file.
2. `cc host status` reports the expected capability class and protection model.
3. `cc doctor` reports the real `inline gate`, `repo boundary gate`, `review gate`, and `verification gate` for that host without implying false hook parity.
4. For Class B/C hosts, `cc doctor` should fail if the repo boundary gate is not really wired repo-side.

---

## 8 Tool Landscape Notes

**US tools** (Claude Code, Codex, Gemini CLI, Copilot, Cursor) have the most mature context file support. They actively compete on "context engineering" features.

**European tools**: Mistral's Codestral is a strong coding model available through documented hosts such as Cline or Aider. No European-origin coding agent with native context file support exists yet.

**Chinese tools** (Trae, Tongyi Lingma, MarsCode, CodeGeeX): rapidly evolving. Most have some form of project-level rules but documentation is primarily in Chinese. Context file conventions are not yet standardized across Chinese tools. Best approach: use a Chinese model (DeepSeek, Qwen3-Coder) through a documented host with stronger CC support such as Cline.

**Russian tools** (GigaCode, YandexGPT): primarily IDE plugins focused on autocomplete. Limited agentic capabilities and no documented context file support. Use Russian-accessible models through Cline or Aider.

**Open-source tools** (Cline, Aider, OpenCode, Goose): the most flexible option. They support multiple backend models and have active communities. Cline stands out as the only open-source tool with native hook support equivalent to Claude Code.

---

## 9 Summary

1. ControlCoding keeps one structure across hosts: context, boundary, review, and verification gates always exist.
2. The gate location changes by capability class: inline-first on Claude/Cline, sandbox/repo-side on Codex, repo-side/review-driven on instruction-first hosts.
3. `CONTROLCODING.md` is the canonical source of truth. Generate `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, or `.clinerules` from it.
4. `cc host status` and `cc doctor` are the canonical runtime view of the active protection model.
5. For a fully free setup: Cline + Ollama + Qwen3-Coder gives you native hooks with a local model.
6. The methodology was validated on Claude Code. Other tool setups are documented as serious adaptations to evaluate, not as fake parity claims.
7. "AI-agnostic" means multi-vendor and cross-tool compatible. It does not mean consumer login reuse across third-party products.
