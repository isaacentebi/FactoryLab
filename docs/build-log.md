# Build log

Each entry records what was built, who built it, how many attempts it took, and a fidelity check against `docs/build-spec-v0.4.md` and the source essay. The fidelity check is the point; the rest is bookkeeping.

## Phase 1 — deterministic substrate (11 September 2026)

Status: **complete**. All five completion checks in spec section 6 pass, run literally from the CLI.

| PR | Workstream | Implementer | Attempts | Reviewer changes |
|---|---|---|---|---|
| #1 | World adapters: fake and Hyperliquid exchanges, clock/drip, priced model providers, metering | Claude | 1 | — |
| #2 | Cortex: author-neutral requests, ephemeral assemblies, bounded sandbox | Claude | 1 | — |
| #3 | World manifests and CLI | Claude | 1 | — |
| #4 | Learners: Hedge, EXP3, Blum–Mansour, feasibility, router, reference games | Codex (`gpt-6-astra`) | 1 | none; accepted as delivered |
| #5 | Kernel: wallet, sealed ledger, registry, queue, events, reserve, timing, termination | Codex (`gpt-6-astra`) | 1 | ledger cost: full chain walk on every append was quadratic; replaced with an O(1) health check plus a periodic full walk |
| #6 | Runtime loop, venue liquidation and shocks, crash world | Claude | 3 iterations against the running loop (closure clock, drip, death physics) | — |

Test count on main: 229 (plus 3 network-marked tests that pass against Hyperliquid testnet).

### Completion checks, as run

1. `uv run pytest` — 229 passed.
2. `factorylab run --world scripted --events 200 --seed 1` — 476 invocations, 154 NOOPs, 198 delayed consequence settlements with maximum latency 10 events, every invocation metered (spend recorded per capability: 267,500 µUSD decider, 184,500 µUSD observer), a logged propensity that replays its sample, `wallet_conservation` and `ledger_verify` both true.
3. `factorylab run --world scripted-crash --events 2000 --seed 2` — terminated `balance_zero` at event 1622, balance −1,021,128 µUSD, seal key released, conservation true.
4. Reference games — Hedge swap regret per round 0.177, Blum–Mansour 0.014, on the same three-action game and seed at T = 5000; two-action game shows the notions coincide.
5. `factorylab probe --world testnet` — live mids and funding from Hyperliquid testnet.

### Fidelity check: spec section 1 invariants

| # | Invariant | Where enforced | Violating test |
|---|---|---|---|
| 1 | Conservation | `Wallet` books only reserve/commit, settle (`exchange_pnl`, `funding`), drip; `check_conservation()` | `tests/kernel/test_wallet.py` |
| 2 | Metering before return | `world/metering.py` reserve → execute → commit; `Wallet.reserve` refuses beyond `available` | `tests/world/test_models_and_metering.py`, `tests/kernel/test_wallet.py` |
| 3 | Death at zero | `Wallet.dead`, `Termination.check/kill`, `KeyStore._release` only by final Termination | `tests/kernel/test_termination.py`, `tests/runtime/test_loop.py` (crash world) |
| 4 | Addressability | `PropensityRecord.validate` replays the sample; `DecisionQueue.settle` addresses the original handle; successors or `historical` retention | `tests/kernel/test_queue.py`, `tests/learners/test_router.py` (20k draws) |
| 5 | Novelty reserve | `NoveltyReserve` per window, expiring, refuses contracts with settled history; `Registry.register` needs a live receipt for population writes | `tests/kernel/test_reserve.py`, `tests/kernel/test_registry.py` |
| 6 | Information boundaries | `Request.prompt_text` omits handle and channel; learner state private, hashed; executor sees no requester distribution | `tests/cortex/test_cortex.py` |
| 7 | Timing registration | `TimingRegistry`, `UpwardBuffer` with seeded min-ratio and jitter; exercised by the loop (569 releases in the 2000-event run) | `tests/kernel/test_timing.py` |
| 8 | Author neutrality | `Request.__post_init__` rejects author fields | `tests/cortex/test_cortex.py` |
| 9 | Sealed ledger | Fernet at rest, SHA-256 chain, key released only at termination, fixed aggregate views | `tests/kernel/test_ledger.py` (six tamper modes) |
| 10 | Non-intervention | Not enforceable in code beyond the kernel having no mid-run mutators; the covenant is written in the spec | — |

### Where the build taught us something the spec did not say

**A wallet starves; it does not die of thinking.** Invariant 2 forbids reserving beyond the balance, so compute spend approaches zero asymptotically and never crosses it. In the no-drip scripted run the balance ended at 3,329 µUSD with 730 logged feasibility exclusions. Death has to come from the world: trading losses, funding, liquidation. This is consistent with the essay (the factory prices survival against what it depends on) and is now pinned by `test_scripted_world_starves_but_cannot_die_from_compute_alone`. Spec condition 3 was reworded accordingly.

**Venues liquidate.** An unrealised loss never reaches the wallet, so a leveraged position under a crash would have left the world in limbo. The fake venue now force-closes below maintenance margin, which is what Hyperliquid does. Realised losses land in the wallet and can take it negative.

**The venue's cash must mirror the wallet.** Otherwise margin checks ignore compute spend and the factory can lever money it no longer has. `FakeExchange.sync_cash` is called after every settlement.

### Where phase 1 is deliberately less than the essay asks

These are known gaps to close in phase 2, not oversights.

- **Leaf rewards are architect-defined.** `fast-v1` (1 for a well-formed return) and `consequence-v1` (wallet delta over ten events, scaled to 1% of the initial balance) are supplied scoring rules. The essay wants scores to come from an evaluator population graded by consequence. Phase 2 replaces these with evaluator verdicts and settled forecasts; the channels and addressing already exist.
- **No swap-regret learner in operation.** Blum–Mansour is verified in the reference harness only. Its runtime adapter needs decision snapshots for delayed feedback (documented in its module docstring). Routers in operation are EXP3, so the "retentive core" the essay describes is not yet seeded.
- **The seed router is not yet replaceable by the population.** It is the arch-centering the essay permits; the registry supports `router` contracts, but no population action registers one yet.
- **No LLM in the loop.** `ScriptedProvider` stands in. `AnthropicProvider` is wired and priced but only the `testnet` world names real tiers, and the runtime refuses non-fake venues until the live path exists.
- **Sandbox is bounded, not isolated.** Wall-clock and CPU limits, empty environment, isolated interpreter. No network isolation and no memory cap on macOS. Population-written code is not yet executed anywhere.
- **Novelty reserve opens windows but nothing draws on it.** Population registration is untested end to end.
- **Ledger tamper detection is periodic, not per append.** Same-size in-place edits to earlier lines are caught by the full walk every 1024 items during runs (every 256 by default), by `verify()`, and by `aggregate()`. Tail tampers, truncation, reordering and header forgery are caught immediately. This was the price of a run that finishes.

### Implementer notes

Codex on `gpt-6-astra` delivered both logic workstreams in one attempt each with green gates it ran itself. Its decisions lists (23 for the kernel, 8 for the learners) were accurate and complete, which made review fast. The one substantive reviewer change was performance, not correctness. Both runs hit `uv` sandbox permission issues and worked around them with an offline cache; that is an environment problem, not a model one.

## Phase 2 — next

In order: evaluator assemblies with sealed forecasts and consequence settlement (`settlement/`); a live-venue runtime path on testnet with real model tiers; Blum–Mansour runtime adapter and a seeded retentive router; population registration through the reserve; charter metric cards and the λ controller (`charter/`). Then the funded world manifest, which is a launch decision, not a build task.
