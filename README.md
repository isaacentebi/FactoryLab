# Factory Lab

A bounded experimental project informed by *The Superdark Factory*.

**Current status:** phase 1 substrate complete and merged (229 tests). See the [build log](docs/build-log.md) for the fidelity review and phase 2 plan.

## Start here

- [Build spec v0.4](docs/build-spec-v0.4.md) — the interfaces implemented; supersedes v0.3 where they conflict.
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
uv run factorylab run --world scripted --events 200 --seed 1
uv run factorylab run --world scripted-crash --events 2000 --seed 2
uv run factorylab probe --world testnet
```

## Next implementation milestone

Phase 2: evaluator assemblies with sealed forecasts and consequence settlement, a live-venue runtime path on testnet with real model tiers, the Blum–Mansour runtime adapter, population registration through the novelty reserve, and the charter. The funded world is a launch decision, not a build task.

## Location

The project lives at `/Users/isaacentebi/Desktop/FactoryLab`.
The original Codex workspace path redirects here so existing conversation links
continue to work. Remote: github.com/isaacentebi/FactoryLab (private).
