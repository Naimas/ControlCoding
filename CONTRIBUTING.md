# Contributing to ControlCoding

Thanks for your interest in contributing. This guide covers setup, testing, and PR guidelines.

## Prerequisites

- Python 3.10+
- Git
- Git Bash or WSL on Windows (Unix shell commands in scripts)

## Local Setup

```bash
git clone git@github.com:Naimas/ControlCoding.git
cd ControlCoding
```

No pip dependencies are needed for hooks (stdlib only). MCP servers require `fastmcp`:

```bash
pip install fastmcp
```

## Running Tests

```bash
python -m pytest tests/ -v
```

All tests must pass before submitting a PR. There are 33 test files with 977 tests.

Quick smoke test (fastest subset):

```bash
python -m pytest tests/test_check_boundaries.py tests/test_check_dangerous_commands.py tests/test_session_end_check.py -v
```

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
