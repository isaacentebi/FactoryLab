# Handoff — where Factory Lab stands

Written 11 September 2026 so that any session (or a compacted one) can resume from the repository alone. Update this file whenever a phase lands.

## Where to look

- `docs/build-spec-v0.4.md`, `v0.5-phase2.md`, `v0.6-phase3.md` — the binding specs, in order. §8 of v0.6 is phase 3b.
- `docs/build-log.md` — what was built, by whom, attempts, the literal completion-check results, and the fidelity review against the essay after each phase.
- `docs/runs/*.summary.json` — the summaries of every live testnet run. Diaries and keys are never committed.
- `outputs/project-plan.md` v0.3 — the long design rationale; specs win where they conflict.
- The governing test is the essay's bewilderment criterion; it is completion condition 6 in v0.5 §9.

## State on main

12 September 2026, end of the fix cycle. Everything is merged (PRs #1–#37); no branch, worktree or background task is open. Everyday gate: `uv run pytest` (parallel by default) 1685 passed in about three minutes; the kill-and-resume tests are marked `slow` and run with `uv run pytest -m slow -p no:xdist tests/runtime/test_resume.py` before any merge to main (30 passed).

Phase 4 delivered: versioning, verdict-as-consequence-forecast, population λ, recursive evaluation with the cascade gate, the amendable clock, measured governance cadence, damping, the observations catalogue, the charter in the manifest, the x402 client, Venice and market compute, resume, hosting, the live wake. The reward line reaches every primitive; scoring physics is public in every request.

Cold audits (docs/audits/, five reports: fidelity by Codex, Fable, Opus; defects by Fable, Opus) found 30-odd issues; three fix passes closed every reproduced finding (docs/build-log.md "Cold audits"). Two fidelity arguments are recorded as dissent, not fixed: the public scoring block, and `return_paid_off` as the consequence predicate.

Proven with real money (docs/runs/compute-proof.md): the factory bought its own thinking on three rails from its reserve on Base ($5 Venice top-up, three paid completions; reserve now 4.989 USDC and 4.999768 Venice credit). Proven on testnet: Hyperliquid ↔ HyperEVM ↔ Base round trip through the factory's own treasury tool (PR #29).

Live runs 1–8 logged; run 8 was the first with population trades (docs/build-log.md).

## Secrets and money

- `openrouter.key` and `hyperliquid.key` at the repo root, 0600, gitignored, loaded by the CLI. Never read, print, or commit them. The experimenter creates them; the assistant does not handle key values.
- The Hyperliquid account is being funded with real money by the experimenter. The code refuses mainnet unless the world is named `funded`; that manifest does not exist yet and must not be created until: testnet fills verified, antagonists and price controller merged, the three cold audits (money paths, sealing, sandbox) done, hosting with restart and backup, the weekly rebalancing rule written down.

## How to run

```
uv run pytest
uv run factorylab run --world scripted --events 500 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
uv run python -m factorylab.runtime.cli run --world testnet --events 30 --seed 4 --tick-interval 10s --ledger runs/x.jsonl --kill-at-end
uv run python -m factorylab.runtime.cli report runs/x.summary.json
uv run python -m factorylab.runtime.cli postmortem runs/x.jsonl runs/x.jsonl.key --kinds event:Registered,invocation
```

The wake page (artifact) is rebuilt from summaries and a killed world's diary; see the build log for what it shows.

## Working agreement

The session model plans, specs, reviews diffs and merges. Codex on `gpt-6-astra` implements bounded logic workstreams from a spec section in its own worktree; Claude subagents implement runtime and design work. Every PR states the spec section, the gate output, and every decision the spec left open. After every build: run the completion checks literally, write the fidelity review, and check the bewilderment condition honestly.

## Next

1. A cold re-audit of this main, launched together with the experimenter (docs/audit-brief.md; Codex cannot take a defect seat).
2. The first-move review: seed prompts, the lineup, edition 1 of the charter (`worlds/edition1-example.toml`), the tick, the pots.
3. Operational checks on the launch machine: the Hyperliquid mainnet wallet (a dedicated experiment wallet holding only the experiment's money; withdrawals need its main key), Venice buying from the reserve, the wake page reachable.
4. Fund OpenRouter with the seed ($100), no auto top-up.
5. The `funded` manifest, hash recorded, launched. Then nothing changes, ever.

Previously listed before the `funded` manifest: merge resume and the clock; one real x402 purchase from the reserve for cents (experimenter funds `reserve.key`'s address with about $5 USDC on Base; `factorylab reserve init` prints the address) and one real $5 Venice tranche; venue↔reserve real moves with CCTP and funding payments (spec §5.3, §5.5); testnet faucet then run 6 with real fills; cold audits per `docs/audit-brief.md` (Codex, a Fable subagent and an Opus subagent, each given the essay); hosting (droplet, supervisor into resume, nightly encrypted backups, alert on death, and a `factorylab wake` command that publishes the five sealed aggregates plus venue and reserve balances hourly: the only live view, per the essay's control tower); build log, wake page.

The ordered list, with the gaps against the essay, is `docs/design-audit-v2.md` §7. In short: merge the controller wiring and `p3b-runtime`; run 5 with the Hyperliquid testnet key; make the treasury real (Hyperliquid withdrawals to a factory-owned reserve address, mechanical float top-ups from the reserve only); `resume` after a process crash; a versioning module (transfer operator, spectral gap, pathology flags, early-warning signals); population-proposed λ; recursive meta-evaluation; cold audits; hosting; then the `funded` manifest and no changes, ever.

Rebalancing finding (11 September): Hyperliquid withdrawals and deposits are scriptable through the SDK (main-wallet key, ~$1, ~5 min). OpenRouter credits cannot be bought by API (the crypto endpoint returns 410) and never flow back out. Hence the three-pot design in the audit §6.
