# Factory Lab

A bounded experimental project informed by *The Superdark Factory*.

**Current status:** phase 2 built and merged (482 tests): evaluators, sealed forecasts and consequence settlement, population registration, OpenRouter provider, live testnet path. The live run and the bewilderment check wait on an OpenRouter key. See the [build log](docs/build-log.md).

## Start here

- [Build spec v0.4](docs/build-spec-v0.4.md) — phase 1 interfaces; supersedes v0.3 where they conflict.
- [Build spec v0.5](docs/build-spec-v0.5-phase2.md) — phase 2: evaluators, settlement, registration, live path, and the bewilderment check.
- [Build log](docs/build-log.md) — what was built, by whom, and how faithfully.
- [Working plan v0.3](outputs/project-plan.md) — the consolidated specification.
- [Fable refinements](outputs/fable-refinements.md) — accepted, qualified and rejected recommendations.
- [Independent Fable 5.1 CLI review](outputs/fable-cli-review.md).
- [Chapter II audit](outputs/chapter2-audit.md) — 64 source-linked design checks.
- [Source reading record](outputs/source-reading.md).

Prior plan versions are retained in `outputs/` for comparison. Source snapshots,
review inputs and intermediate records are preserved in `work/`, which is excluded
from Git.

## Running it

```bash
uv sync
uv run pytest
uv run factorylab run --world scripted --events 400 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
uv run factorylab probe --world testnet
OPENROUTER_API_KEY=... uv run factorylab run --world testnet --events 30 --seed 3
```

## Next implementation milestone

A live testnet run with real models, then phase 3: an adversarial minority, charter amendments with sortition and the λ controller, deeper meta-evaluation, population-written tools. The funded world is a launch decision, not a build task.

## Location

The project lives at `/Users/isaacentebi/Desktop/FactoryLab`.
The original Codex workspace path redirects here so existing conversation links
continue to work. Remote: github.com/isaacentebi/FactoryLab (private).
