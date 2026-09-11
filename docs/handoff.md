# Handoff — where Factory Lab stands

Written 11 September 2026 so that any session (or a compacted one) can resume from the repository alone. Update this file whenever a phase lands.

## Where to look

- `docs/build-spec-v0.4.md`, `v0.5-phase2.md`, `v0.6-phase3.md` — the binding specs, in order. §8 of v0.6 is phase 3b.
- `docs/build-log.md` — what was built, by whom, attempts, the literal completion-check results, and the fidelity review against the essay after each phase.
- `docs/runs/*.summary.json` — the summaries of every live testnet run. Diaries and keys are never committed.
- `outputs/project-plan.md` v0.3 — the long design rationale; specs win where they conflict.
- The governing test is the essay's bewilderment criterion; it is completion condition 6 in v0.5 §9.

## State on main

Phases 1–3b and most of phase 4 are merged (PRs #1–#24). Gate: 1170 tests. Merged on 11 September in phase 4: versioning (behaviour-based versions, pathologies, early warnings), population-proposed λ, recursive meta-evaluation behind a per-tier cascade gate, verdict-as-consequence-forecast with FIFO lots and a reward-hacking review, the x402 client and Venice provider, and compute bought on the open market (`market.discover`, `x402:` sellers, insolvency). Judges see only the event a producer answered. The Hyperliquid adapter survives transient API failures.

In flight: resume after a process death (spec v0.7 §6) and the committee-amendable clock (§8), both with Codex in worktrees `../FactoryLab-p4H` and `../FactoryLab-p4K`.

Live runs: 1–5 logged (run 5 summary in `runs/`, to be copied to `docs/runs/`). Run 5 had two unscripted registrations and an antagonist that fooled judges 44 times in 50, but zero fills: the venue key's testnet account has no testnet USDC (faucet claim owed by the experimenter).

Decisions taken 11 September: compute is bought by the factory itself on the x402 market with Venice as the credible renewable seller and OpenRouter credits as a depleting seed nobody refills (no human anywhere after launch; card and OpenRouter API routes verified dead, see `docs/research/`); the funded world ticks every five minutes at launch and the committee may amend the tick within physics bounds; verdicts are forecasts settled on realized consequence.

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

Remaining before the `funded` manifest: merge resume and the clock; one real x402 purchase from the reserve for cents (experimenter funds `reserve.key`'s address with about $5 USDC on Base; `factorylab reserve init` prints the address) and one real $5 Venice tranche; venue↔reserve real moves with CCTP and funding payments (spec §5.3, §5.5); testnet faucet then run 6 with real fills; cold audits; hosting; build log, wake page.

The ordered list, with the gaps against the essay, is `docs/design-audit-v2.md` §7. In short: merge the controller wiring and `p3b-runtime`; run 5 with the Hyperliquid testnet key; make the treasury real (Hyperliquid withdrawals to a factory-owned reserve address, mechanical float top-ups from the reserve only); `resume` after a process crash; a versioning module (transfer operator, spectral gap, pathology flags, early-warning signals); population-proposed λ; recursive meta-evaluation; cold audits; hosting; then the `funded` manifest and no changes, ever.

Rebalancing finding (11 September): Hyperliquid withdrawals and deposits are scriptable through the SDK (main-wallet key, ~$1, ~5 min). OpenRouter credits cannot be bought by API (the crypto endpoint returns 410) and never flow back out. Hence the three-pot design in the audit §6.
