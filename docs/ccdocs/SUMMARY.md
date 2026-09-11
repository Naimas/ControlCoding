# ControlCoding v2 - Documentation Index

This directory contains the ControlCoding v2 methodology split into focused, self-contained documents. Each document can be read independently, with cross-references to related material.

Each document can be read independently, with cross-references to related material.

---

## Core Documentation (docs/ccdocs/)

| Document | Contents | Audience |
|---|---|---|
| [methodology.md](methodology.md) | Architecture, principles, enforcement model, invariants, promotion path, adoption strategy, conclusions, comparative analysis | Architects, team leads, anyone evaluating CC |
| [hooks-reference.md](hooks-reference.md) | Hook scripts, configuration, exit codes, resilience, cross-tool adaptation | Developers implementing enforcement |
| [tools-reference.md](tools-reference.md) | CLAUDE.md constitution, feature.lock, CLI tool, MCP servers, debug escalation, external connection control | Developers setting up and using CC tools |
| [cookbook.md](cookbook.md) | Worked examples by domain (Django, React, C++, data pipeline, financial), complete Payments retry example with realistic failures | Anyone starting a new CC project |

## Getting Started (docs/)

| Document | Contents | Audience |
|---|---|---|
| [Install ControlCoding On Your Project](../install-controlcoding-on-your-project.md) | Primary install path: run setup, engagement setup, and doctor on your project | New adopters |
| [Quick Start](../quick-start.md) | Compact setup summary after the primary install page | New adopters |
| [Adoption Guide](../adoption-guide.md) | Detailed walkthrough of CC adoption for existing projects | Teams adopting CC incrementally |
| [Cross-Tool Guide](../cross-tool-guide.md) | Using CC with Claude Code, Cursor, Aider, Kiro, and generic tools | Multi-tool teams |
| [Evidence Report](../evidence.md) | Validation data from 2 real projects: metrics, adoption patterns, costs, limitations | Evaluators, skeptics |

## Reading Paths

**"I want to evaluate ControlCoding"**
1. [methodology.md](methodology.md) - Sections 1-3 (intro, condominium, principles)
2. [Evidence Report](../evidence.md) - Real project data, adoption costs, known limitations
3. [cookbook.md](cookbook.md) - Section 6 (complete Payments example)
4. [methodology.md](methodology.md) - Section 10 (conclusions, when NOT needed)

**"I want to set up ControlCoding on my project"**
1. [Install ControlCoding On Your Project](../install-controlcoding-on-your-project.md) - primary onboarding path
2. [Quick Start](../quick-start.md) - compact setup summary
2. [cookbook.md](cookbook.md) - Find your domain, copy the snippets
3. [tools-reference.md](tools-reference.md) - CLAUDE.md template, cc CLI
4. [hooks-reference.md](hooks-reference.md) - Hook configuration

**"I want to understand the enforcement model"**
1. [methodology.md](methodology.md) - Section 5 (DENY/WARN, compliance matrix)
2. [hooks-reference.md](hooks-reference.md) - Full hook reference
3. [tools-reference.md](tools-reference.md) - feature.lock format

**"I want to protect requirement documents from AI compression"**
1. [methodology.md](methodology.md) - Section 9.14 (Semantic Fidelity)
2. [cookbook.md](cookbook.md) - Section 7.3 item 6 (common mistake: letting AI rewrite specs)
3. [tools-reference.md](tools-reference.md) - CLAUDE.md template Semantic Fidelity section

**"I want to verify my project matches its design document"**
1. [tools-reference.md](tools-reference.md) - Section 10d (verification_agent.py), 10f (mcp_vision.py)
2. [cookbook.md](cookbook.md) - Section 8 (Verification Agent worked example)
3. [tools-reference.md](tools-reference.md) - Sections 10, 10c (visual tools for screenshot verification)

**"I want to adapt CC to my tool"**
1. [Cross-Tool Guide](../cross-tool-guide.md) - Overview
2. [tools-reference.md](tools-reference.md) - Tool adaptation section
3. [hooks-reference.md](hooks-reference.md) - Hook configuration for each tool
