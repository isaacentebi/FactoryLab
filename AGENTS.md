# Factory Lab — implementer conventions

You are implementing part of a bounded experimental system described in
`docs/build-spec-v0.4.md`. Read that file first. `outputs/project-plan.md`
(v0.3) is the longer design rationale; the spec wins where they differ.

## Rules

- Python ≥ 3.12. `uv` manages the environment: `uv sync`, `uv run pytest`,
  `uv run ruff check .`. Do not add tooling.
- Allowed third-party packages: `hyperliquid-python-sdk`, `anthropic`,
  `cryptography`, `pytest`, `ruff`. Anything else needs a written reason.
- Money is integer micro-USD. Never a float.
- No global mutable state. No background threads in phase 1.
- `factorylab.kernel` imports nothing from `cortex`, `world`, or `runtime`.
- Every kernel invariant in spec section 1 gets at least one test that
  attempts to violate it and asserts failure.
- Docstrings state what a function guarantees, not what it does.
- Never describe kernel rules to the population in prompt text. Physics is
  enforced by code, not announced.
- Stay inside the files named in your task. Do not refactor neighbours, do
  not reformat unrelated files, do not commit.

## Verify gate

```
uv run ruff check . && uv run pytest
```

Run it before you return. Return the list of changed files, the gate output
verbatim, and any decision you made that the task did not specify.
