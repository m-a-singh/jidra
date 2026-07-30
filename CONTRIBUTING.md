# Contributing to JIDRA

## Getting Started

```bash
git clone https://github.com/akhilsinghcodes/jidra.git
cd jidra
pip install -e ".[dev]"
```

## Development Setup

- Python 3.11+
- Run `ruff check` and `ruff format` before committing
- Run `pytest` to verify tests pass

## How to Contribute

1. Fork the repo and create a branch from `main`
2. Make your changes with tests where applicable
3. Run `ruff check src/ evals/ experiments/` — fix any lint errors
4. Open a pull request with a clear description of what and why

## What We Welcome

- Bug fixes with reproduction steps
- New language extractor support (Go resolution improvements, C# etc.)
- MCP tool enhancements
- Documentation improvements
- Eval harness additions (new test cases against public codebases)

## What to Avoid

- Breaking changes to MCP tool schemas without discussion
- Adding runtime dependencies without justification
- Large refactors without an issue/discussion first

## Running the Eval Harness

```bash
# Java eval (requires thingsboard indexed)
python -m evals.harness.java.agent_eval

# TypeScript/Python evals
python -m evals.harness.ts_python.agent_eval
```

## Code Style

- `ruff` for lint and format (config in `pyproject.toml`)
- Type hints on public functions
- No docstrings unless the behavior is genuinely non-obvious

## Questions

Open an issue or start a discussion on GitHub.
