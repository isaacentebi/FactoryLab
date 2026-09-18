# Factory Lab — implementer conventions

You are implementing part of a bounded experimental system. Read `README.md`
first: it states what the factory is and what the kernel enforces. The binding
rules are `docs/build-spec-v0.8-round2.md`; `docs/manifest.md` documents every
manifest key, its default, and whether it is fixed for the world's life. The
code wins where any document disagrees with it.

## Rules

- Python 3.13. `uv` manages the environment: `uv sync`, `uv run pytest`,
  `uv run ruff check .`. Do not add tooling.
- Allowed third-party packages: `hyperliquid-python-sdk`, `anthropic`,
  `cryptography`, `pytest`, `ruff`. Anything else needs a written reason.
- Money is integer micro-USD. Never a float.
- No global mutable state. No background threads.
- `factorylab.kernel` imports nothing from `cortex`, `world`, or `runtime`.
- Every kernel invariant gets at least one test that attempts to violate
  it and asserts failure.
- Docstrings state what a function guarantees, not what it does.
- Never describe kernel rules to the population in prompt text. Physics is
  enforced by code, not announced.
- Stay inside the files named in your task. Do not refactor neighbours, do
  not reformat unrelated files, do not commit.

## Verify gate

```
uv run ruff check . && uv run pytest
uv run pytest -m gate -n 2 <the gate test files you touched or that cover your change>
```

A bare `uv run pytest` runs the `check` tier (no world runs, 2 workers). Run it, plus
only the specific `gate` files for what you changed; do not run the whole gate
(`uv run pytest -m "check or gate" -n 4`) unless your task says so, and never with
`-n auto`. A new test that runs a world is `gate`: the conftest usually detects it,
and a `check` test over 2 s fails with a message telling you to mark it
`@pytest.mark.gate`. Run it before you return. Return the list of changed files, the gate output
verbatim, and any decision you made that the task did not specify.
