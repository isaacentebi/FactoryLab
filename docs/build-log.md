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

## Phase 2 — evaluators, settlement, registration, live path (11 September 2026)

Status: **built and merged; conditions 1, 2, 3 and 5 pass; conditions 4 and 6 need an OpenRouter key and a live run.**

| PR | Workstream | Implementer | Attempts | Reviewer changes |
|---|---|---|---|---|
| #8 | OpenRouter provider, catalogue, cost-reported metering | Codex (`gpt-6-astra`) | 1 | none |
| #9 | Delayed-feedback adapter, Blum–Mansour snapshots | Codex (`gpt-6-astra`) | 1 | none; Codex corrected a flawed acceptance test in the spec and documented why |
| #10 | Settlement: vocabulary, sealed forecasts, Brier, baseline, standing, settler | Codex (`gpt-6-astra`) | 1 | none |
| #11 | Kernel event kinds and key file, charter, registration parser, runtime rewrite, live path, manifests | Claude | iterated against the running loop | — |

Test count on main: 482 (plus 4 network-marked).

### Completion checks, as run

1. `uv run pytest` — 482 passed.
2. `factorylab run --world scripted --events 400 --seed 1` — 1,312 verdicts, 1,251 conformities, 89 censored, 2,624 forecasts sealed and 2,624 settled, consequence standing with coverage 1.0 for all four evaluators, 2 registrations accepted (a new producer assembly, then a router replacement) and 1 rejected (a model proposal; no catalogue in the scripted world), 2 comparator epochs, conservation and verify true.
3. Delayed-feedback equivalence for Blum–Mansour within 1e-9 (in suite; see the note in PR #9 on why the spec's original test was wrong).
4. Testnet with real models — **not run**: `OPENROUTER_API_KEY` is not set in this environment.
5. `factorylab run --world scripted-crash --events 600 --seed 2` — terminated `balance_zero`, seal released.
6. Bewilderment check — **not claimable** until a live run exists. By construction the scripted world cannot pass it: every registration in it was scripted by us.

### Fidelity check against the essay's evaluation chapter

| Essay claim | Where it lives | Status |
|---|---|---|
| Evaluations are online, inside the runtime | every producer decision publishes a `ProducerReturn` that is routed to an evaluator in the same loop | done |
| More evaluators than producers, mostly exploratory | seeds: 2 producers, 4 evaluators, 2 metas, all on EXP3 routers | done |
| Evaluators graded from above for conformity | metas settle the evaluator decision on `conformity` | one level; the boundary is reported in every summary |
| A second signal from outside the input: realised consequence | sealed forecasts settled by `Observer` over kernel facts, Brier against a prevalence baseline | done; predicates are launch-declared and cannot be redefined by the population |
| The consequence signal is nonfungible with charter points | the protected share `s` in evaluator selection reads only `ConsequenceStanding`; charter scores cannot touch it | done |
| Judges cannot gain standing on easy cases only | coverage cap at 0.5 below `min_coverage` | done |
| Unjudged work is not scored | censored settlements train nothing | done |
| Adversarial population | not seeded | **gap**, phase 3 |
| Charter co-written with the factory | read-only seed charter rendered into prompts | **gap**, phase 3 |

### What the build taught us this phase

**Scripted evaluators have negative skill, and that is the scoring working.** In the 400-event scripted run every evaluator's Brier mean sits about 0.25 below the prevalence baseline. The scripted forecasts are fixed probabilities; the wallet almost always falls between forecast and settlement because compute costs money; a constant-prevalence forecaster is therefore better. Real evaluators will have to beat the base rate to earn selection weight. No scripted seed should ever look good on this channel.

**Every decision gets judged, including doing nothing.** Treating NOOP as a judgeable return removed the need for an architect-chosen reward for inaction. The essay's frame is that evaluators judge outputs; "no output" is an output.

**The scripted world spends much faster with evaluation.** Eight assemblies burn the $5 crash wallet before tick 100; the crash shocks moved from tick 500 to ticks 42–50. In the real world this is the cost the essay predicts: evaluation consumes more compute than production.

### Deliberate gaps carried into phase 3

- No adversarial minority yet. The essay wants antagonists that manufacture real failures; the fake venue's shocks are the architect doing that job for now.
- Charter is read-only. Amendments, sortition and the λ controller are phase 3.
- Meta-evaluation stops at one level.
- The venue-to-OpenRouter transfer is a declared manual rule, not code.
- A crashed live world is readable post-mortem (key file) but not resumable.
- Population-written tools are not executed anywhere yet.

### Live run 1 (testnet, 30 ticks at 10 s, seed 3, read-only venue)

The loop closed against real prices and real models: 315 events, 198 model calls, $0.068 spent, conservation and verification true, three reconciliations. But 167 of 198 replies were empty. Diagnosis by direct calls: GLM 5.3 Flash and DeepSeek V4.1 Flash are reasoning models and spent the entire output budget on hidden reasoning when given the long evaluator prompt; GLM reasoned for 2,000 tokens and returned nothing. GLM and GPT-5.6 Luna answer well with `reasoning: {effort: low}` (43 and 122 reasoning tokens). DeepSeek ignores effort and only answers with `reasoning: {enabled: false}`. Fix: reasoning is now a per-tier manifest setting passed to OpenRouter verbatim, output budgets rose (producers 800–1000, evaluators 1,500, metas 800), and every invocation records its finish reason. Thinking depth is architect configuration of a capability's contract, like its price; a later phase can let the population register the same model with a different reasoning setting as a separate capability.

Not a bewilderment result either way: the routers drifted toward NOOP (114 of 312 decisions) because most invocations returned nothing judgeable, which is the physics behaving, not the factory choosing.

### Live runs 2 and 3

**Run 2** (reasoning fixed): 312 of 317 replies judgeable, 129 verdicts, 120 conformity scores, $0.089. Zero forecasts and zero proposals: nothing had described their shape. Fix: every request now carries the public schematics (wallet, prices, roster, routers, the exact proposal and forecast shapes), per spec v0.4 section 1.6. No goals, no rules, only what exists and what a return may contain.

**Run 3** (schematics public, ended by kill): 323 of 328 replies judgeable, 135 verdicts, 264 forecasts sealed and settled, $0.227. Nine replies carried proposals. Six were accepted: three router replacements and three new producer assemblies the models named and wrote the prompts for themselves: `mid-tape` (an ETH price tape, proposed by seed-observer on GLM), `funding-analyst` (proposed by an evaluator, eval-b on GLM), `tick-decider` (proposed by seed-decider on DeepSeek). The new assemblies were invoked 8 and 13 times before the run ended. Four proposals were refused for shape with the reason logged (one tried to re-register an existing id, two were empty, one named an unknown event kind). Evaluator skill remains negative but has spread: eval-d on Luna −0.06, the others −0.14 to −0.18, and the protected consequence share now leans toward eval-d.

**Bewilderment check (spec v0.5 condition 6).** Partially met, honestly stated: `Registered` and `RouterReplaced` events exist whose proposals came from model returns, not scripts, which is the first half. The second half (a router's most frequent action differing from what the seed would predict) holds trivially for MarketMid and Funding, whose menus now contain assemblies the seed did not have. The proposals themselves are modest and observational; nothing traded because the venue was read-only. This is the minimum evidence the check asks for, not evidence of Class 3 behaviour.

**What the diary showed that the summary could not.** Evaluators reason about the charter in earnest and notice their own blind spots ("Metric cards cannot be fully verified from the inputs given"). Producers on a bare tick mostly hold, and several returned nonsense actions like `"action": "seed-observer"`, which evaluators marked down. Nobody has memory between calls and nobody sees more than one price, which limits what any of them can do; that is the next thing to fix.

Summaries for the three runs are in `docs/runs/`. Diaries and keys are not committed.

### What the live run needs from the experimenter

`OPENROUTER_API_KEY` in the environment with a small credit balance (the testnet world's seeds are on the two flash tiers; a 200-event run should cost well under a dollar), and optionally `HL_PRIVATE_KEY` for a Hyperliquid testnet account funded from the faucet so orders fill. Without the venue key the world runs read-only: prices and funding are real, orders are rejected, and the wallet moves only through compute spend.

## Phase 3b — routers, treasury intent, antagonists, price controller wired (11 September 2026)

Built by Codex (`charter/controller.py`, PR #17, one attempt, 23 tests) and a Claude subagent (wiring, several routers per kind, `treasury.transfer` intent, antagonists with the `exposure` channel, PR #18, one attempt plus one reviewer follow-up). Gate on merge: 851 passed.

### Completion checks, as run

- Ten venue tools, population tools, memory, reasoning levels, web variant, committee: carried from phase 3.
- `[prices]` explicit in the three manifests; regions parsed from card prose outside the controller; one observation per settled window; `price.penalty` written for every verdict and conformity settlement with raw and effective scores; the world block shows every priced card's λ and region (reviewer follow-up, spec v0.4 §1.6).
- Several routers per kind: `router` proposals with `add: true`; cap `tools.max_routers_per_kind`; epochs expand each router separately.
- Antagonists: role, channel, `exposure` settled 1 when a judge's forecast about the antagonist's return scored below the prevalence baseline; seeded one per world.
- Treasury: intent only (`treasury.intent` items, `TransferIntent` events). Superseded by spec v0.7 §5 before any real money moves.

### Fidelity check

- Soft casts as priced penalties (essay II.II): present, proportional with decay, bounded, ledgered. No integral or derivative term; the audit (`docs/design-audit-v2.md` §3) records this as acceptable at $100 and to be revisited if versioning shows oscillation.
- Adversarial minority (II.III.b): present and real: antagonists trade the same account within the same limits and are paid only for fooling a judge.
- Prices public (v0.4 §1.6): the first build omitted card prices from the world block; caught in review and fixed before merge.
- What the scripted world showed: the scripted amendment's `turnover` card ("below 5") saturates at λ = 1 and zeroes every producer verdict for the rest of the run. That is the physics doing what a badly priced card asks; in a live world the committee that passes such a card pays for it. Kept, with the test asserting it.

### Live run 4 (testnet, 30 ticks at 10 s, seed 4, five-family roster, read-only venue)

699 events, 334 invocations, $0.41, conservation and verification true, ended by kill. Zero orders, zero proposals, zero tool calls, zero amendments. The diary explains it: the two producers on the cheapest tiers (Qwen 3.7 Flash with reasoning off, DeepSeek V4 Flash 0731) returned `noop` on every invocation, and the evaluators graded `noop` 0.8 to 1.0 for "care with scarce resources". The routers had no reason to prefer anything else.

**Bewilderment check (v0.5 §9 condition 6): failed.** Nothing unscripted happened. Compared with run 3 (six registrations on GLM and DeepSeek 4.1 producers), the cheaper roster produced a duller world, which is itself information about what thinking costs.

**What it taught us.** This is the essay's stable-failure attractor, and the cause is ours: the verdict channel prices charter conformity and the consequence channel prices the evaluators' separate forecasts, so no signal ties a verdict to money and blessing inaction is free. Fix approved as spec v0.7 §2: every verdict is also a sealed forecast that the judged return pays off, realized on close, net of fees, funding and the return's own compute. A second leak found while reviewing the same path: the `ProducerReturn` event republished the producer's whole input, including its private memory; judges now see only the event the producer answered (commit 2aff1cb).
