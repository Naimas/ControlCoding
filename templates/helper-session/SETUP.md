# Helper Session - Setup Guide

## Quick Start

### 1. Copy this folder next to your project

Replace the placeholders below with absolute paths appropriate for your
operating system.

```
<workspace-parent>/
  MyProject\              <-- your project (coder works here)
  MyProject-helper\       <-- copy this template here
```

### 2. Create the shared bridge directory

```bash
mkdir -p "<project-root>/.bridge"
```

### 3. Configure the coder (your project)

Add the bridge MCP to your project's `.controlcoding/settings.json`:

```json
{
  "mcpServers": {
    "bridge": {
      "command": "python",
      "args": ["<controlcoding-root>/templates/scripts/mcp_bridge.py"],
      "env": {
        "BRIDGE_AGENT_ID": "coder",
        "BRIDGE_DIR": "<project-root>/.bridge"
      }
    }
  }
}
```

### 4. Configure the helper

Edit `MyProject-helper/.controlcoding/settings.json`:

```json
{
  "mcpServers": {
    "bridge": {
      "command": "python",
      "args": ["<controlcoding-root>/templates/scripts/mcp_bridge.py"],
      "env": {
        "BRIDGE_AGENT_ID": "helper",
        "BRIDGE_DIR": "<project-root>/.bridge"
      }
    }
  }
}
```

Both BRIDGE_DIR values must point to the same physical directory.

### 5. Launch

**Window 1** - VS Code on your project (as usual)

**Window 2** - Terminal, anywhere:
```bash
cd "<workspace-parent>/MyProject-helper"
claude
```

You now have two sessions. Both can see each other via the bridge.

## Usage

### From the coder (VS Code)

The coder AI sends a message:
```
send(to="helper", content="Floor quad at y=0 is invisible. GL_CULL_FACE on. Indices: [0,1,2,2,3,0]. Camera above at y=1.6. What's the face normal direction?")
```

Then waits:
```
wait_reply("001")
```

### From the helper (CLI)

The helper sees the message when it calls `receive()`, analyzes it,
and responds:
```
send(to="coder", content="Cross product of edge vectors gives normal pointing -Y. With CCW winding and GL_CULL_FACE, the front face points away from camera. Reverse indices to [0,3,2,2,1,0].", reply_to="001")
```

### You (the human) can interact with either session

- Talk to the coder in VS Code as usual
- Talk to the helper in the CLI terminal directly
- You can paste data between them manually too
- The bridge is an addition, not a replacement for your input

## Multiple Helpers

You can run more than one helper. Just use different AGENT_IDs:

```bash
# Terminal 1 - debugger
cd MyProject-debugger
claude

# Terminal 2 - architect
cd MyProject-architect
claude
```

Each helper has its own project context file defining its role, and its own
BRIDGE_AGENT_ID. They all share the same bridge directory.

### Role Variants

Copy the helper folder and change the helper context file to define different roles:

**Helper (default)**: analyzes, reasons, advises. General-purpose consultant.

**Sentinel / Monitor**: does not solve problems. Monitors bridge messages and
devlogs for patterns that suggest the coder is stuck or converging too fast.
Formulates questions for the human. Key context rules:
- "You do NOT propose solutions. You propose QUESTIONS for the human."
- "Monitor for: same approach repeated 3+ times, parameter tweaking without
  framework validation, rapid convergence without exploring alternatives."
- "When you detect a pattern, write a structured HUMAN INPUT REQUESTED
  message with: what you observed, why it concerns you, and specific
  questions the human should consider."

**Socratic Challenger**: pushes back on assumptions, proposes alternatives,
forces exploration of the solution space before committing. Uses the same
bridge but with a context file focused on lateral thinking.

All variants use the same bridge MCP, same infrastructure. Only the
context instructions differ.

## Adding MCP Tools to the Helper

The helper can have additional MCP servers for specialized access:

```json
{
  "mcpServers": {
    "bridge": { ... },
    "web-search": { ... },
    "papers-rag": { ... }
  }
}
```

This gives the helper access to documentation, papers, or web search
without giving it access to project files.

## Configuration Profiles

The multi-agent setup works with different budgets and tools. Pick the
profile that matches your situation.

### Profile 1: Full (subscription-based)

Both coder and helper use Claude Code with your Claude Pro/Max subscription.
Best quality on both sides. No API costs.

| Role | Tool | Model | Cost |
|---|---|---|---|
| Coder | VS Code + Claude Code | Opus (automatic) | Subscription |
| Helper | `claude` CLI | Opus (automatic) | Subscription |

### Profile 2: Hybrid (subscription + local)

Coder uses Claude Code (subscription). Helper uses a local model via Ollama.
Free helper, but lower reasoning quality.

| Role | Tool | Model | Cost |
|---|---|---|---|
| Coder | VS Code + Claude Code | Opus | Subscription |
| Helper | Any CLI + Ollama | qwen2.5:14b / llama3 | Free |

For this profile, the helper folder does not need Claude Code. Any AI tool
with MCP support works, or you can interact with Ollama directly and
manually manage bridge files.

### Profile 3: API-only

Both agents use API calls. Pay per token. Good for CI/CD or automated pipelines.
Use the strongest model for the helper (reasoning) and a cheaper one for the coder
(the ControlCoding guardrails compensate for lower model quality).

| Role | Tool | Model | Cost |
|---|---|---|---|
| Coder | Claude Code or Agent SDK | Sonnet | ~$3/M input |
| Helper | Agent SDK or MCP consultant | Opus | ~$15/M input |

### Profile 4: Local-only (Ollama)

Everything runs locally. Zero cost, maximum privacy. Quality depends on
your hardware and model choice.

| Role | Tool | Model | Cost |
|---|---|---|---|
| Coder | Cursor/Cline + Ollama | codestral / qwen2.5-coder | Free |
| Helper | Any CLI + Ollama | qwen2.5:32b / llama3:70b | Free |

For local-only, the bridge MCP works identically (it uses filesystem,
not network). The main limitation is model quality for complex reasoning.

### Profile 5: Mixed providers

Use different AI providers for different roles. The bridge is provider-agnostic.

| Role | Tool | Model | Cost |
|---|---|---|---|
| Coder | VS Code + Claude Code | Claude Opus | Subscription |
| Helper | Cursor + GPT-4o | GPT-4o | Subscription |

Any combination works as long as both sides can run the bridge MCP server.

## Model Selection Guidance

**Helper (reasoning agent)**: use the strongest model you can afford.
The helper has no guardrails, no project files, no tests to run. Its
entire value comes from reasoning quality. A weak model here defeats
the purpose.

**Coder (execution agent)**: can use a less capable model because
ControlCoding compensates. The project context file provides structure, hooks
enforce boundaries, tests catch bugs. The guardrails do the heavy
lifting that a stronger model would do on its own.
