# Factory Lab

A bounded experimental project informed by *The Superdark Factory*.

**Current status:** build spec v0.4 written; phase 1 substrate under construction.

## Start here

- [Build spec v0.4](docs/build-spec-v0.4.md) — the interfaces being implemented; supersedes v0.3 where they conflict.
- [Working plan v0.3](outputs/project-plan.md) — the consolidated specification.
- [Fable refinements](outputs/fable-refinements.md) — accepted, qualified and rejected recommendations.
- [Independent Fable 5.1 CLI review](outputs/fable-cli-review.md).
- [Chapter II audit](outputs/chapter2-audit.md) — 64 source-linked design checks.
- [Source reading record](outputs/source-reading.md).

Prior plan versions are retained in `outputs/` for comparison. Source snapshots,
review inputs and intermediate records are preserved in `work/`, which is excluded
from Git.

## Next implementation milestone

Phase 1 of the build spec: the deterministic substrate (wallet, sealed ledger, registry, decision queue, novelty reserve), the reference learners and router, world adapters with a scripted exchange and model, and a runtime loop that closes on the `scripted` world. Completion condition is in spec section 6.

## Location

The project lives at `/Users/isaacentebi/Desktop/FactoryLab`.
The original Codex workspace path redirects here so existing conversation links
continue to work. Remote: github.com/isaacentebi/FactoryLab (private).
