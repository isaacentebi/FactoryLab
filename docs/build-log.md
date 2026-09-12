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

## Phase 4 — versioning, verdict as forecast, population λ, recursive evaluation, market compute, clock (11 September 2026)

Merged PRs #19–#25, all Codex on `gpt-6-astra` in separate worktrees, one attempt each except recursive meta-evaluation (two passes: the cascade gate was a review finding) and resume (in flight: a semantic rebase against the clock change). Gate on main after #25: 1189 passed.

- Versioning (`factorylab/versioning/`): windows, cells, transition operator with the Dobrushin bound as the spectral-gap bound, versions, the four pathologies, early-warning signals, settling time. Read-only over a diary.
- Verdict as consequence forecast (spec v0.7 §2, approved by the experimenter after the fidelity argument in this log's run 4 entry): kernel predicate `return_paid_off`, FIFO lots, realized on close, backstop marking; reward-hacking review in `factorylab/settlement/REWARD_HACKING.md`.
- Population-proposed λ; recursive meta-evaluation with a per-tier cascade gate (3:1 with jitter; the meta's score settles its whole window, a reviewer change from the free 1.0 the first pass gave siblings); the clock amendable by the committee within physics bounds.
- Compute bought on the open market: x402 client (built for Venice, generalised), `market.discover`, `x402:` and `venice:` namespaces, metering at the paid quote, compute insolvency. Research in `docs/research/venice.md`; the card and OpenRouter routes verified dead.
- Judges see only the event a producer answered; the Hyperliquid adapter retries and falls back to last-good values.

### Fidelity check

Every principle in `docs/design-audit-v2.md` marked missing is now built, with two remaining partials: the price controller has no integral or derivative term, and governance cadence is enforced by window rather than measured settling time (the versioning module now reports it). The one deliberate new deviation is the launch tick of five minutes, the architect's cast for edition 1, healed by the committee's power to amend it.

### Live run 5 (testnet, seed 5, venue key, unfunded testnet account)

896 events, $0.46, ended by kill. Two unscripted registrations (`eth-mid-analyzer`, invoked 23 times), the antagonist fooled a judge 44 times in 50, evaluator skill near baseline. Zero orders: the testnet account had no testnet USDC. Bewilderment partial.

### Live run 6 (testnet, seed 6, 999 testnet USDC, new physics)

1,117 events, 344 invocations, $0.40. Four registrations accepted (`btc-mid-analyzer`, `funding-analyzer`, `eth-mid-analyzer`, one more), sixteen refused: eleven re-proposals of ids that already existed, four malformed router proposals, one model id in the wrong shape. Antagonist fooled judges 27 of 39. Meta-verdicts at tier 2: 62 (the cascade gate held meta wakes to a third of verdicts). Twelve failed invocations were OpenRouter 429s on Qwen 3.8 Flash, weather the world now survives. Two fills at the start were the experimenter's manual test order, picked up because the fill cursor starts at zero rather than at launch (to fix in the reconciler).

**The new physics bit.** Every one of 202 producer returns settled `not_paid_off`, so evaluator standing fell to −0.18 to −0.47 for blessing returns that placed nothing. Within one run nobody adapted; judges have no memory across runs and little within. That is the pressure the design intends and it will only resolve when something pays off.

**Still zero orders, and the cause is ours again.** The producer contract said `action` is a string and nothing more; the only direct order shape, `{"action": "order", coin, side, size}`, was never in the public schematics, and the venue tools need a tool call the cheap models rarely attempt. Producers invented labels (`observe_market_mid`, `register`). Fixed the same way run 3 was fixed: the shape and an example are now public. No rule added.

**Bewilderment (v0.5 §9 condition 6).** Partial: four assemblies the seed did not have, all observational. Nothing surprising yet. The honest reading is that the population cannot yet act on the world because we had not told it the shape of acting; run 7 is the first fair test.

**Follow-ups:** fill cursor from launch time; registration rejection reasons public in the world block (the population re-proposes the same id because it cannot see why it was refused); the proposal parser should learn the model namespaces instead of the alias workaround.

### Live run 7 (testnet, seed 7, order shape public) and the diagnosis of the noop attractor

1,151 events, 349 invocations, $0.42. Producers returned `noop` or `hold` 112 times in 119 with the order shape in front of them; evaluators graded that 0.95 to 1.0; evaluator standing fell to −0.11 to −0.41; the antagonist fooled judges 36 of 43. Four registrations refused (malformed ids), none accepted. Bewilderment: failed. The equity drop and three fills in this run were the treasury build's live tests on the same testnet account, not the population's.

**Diagnosis, as a system, not a bug.** Four links, each traced in the code:

1. *Nobody is told how they are scored.* The seed prompt asks for JSON satisfying a schema; a judge's request says "evaluate against the charter". Nothing tells a judge that its verdict is also a forecast that the return pays off, nor tells a producer that its score is the verdict. The physics existed only in the kernel. Fixed by publishing a `scoring` block in every world block: what settles on which channel and how, as facts, no goals (v0.4 §1.6).
2. *The reward line stopped at the router.* Producers saw verdicts (1.0 for noop, reinforcing it); judges never saw the consequence of their verdicts or their standing. The essay: reward must reach the decision that earned it. Fixed: realized payoff, the judge's Brier and its standing now land in the primitive's private memory.
3. *The seed cards all favour inaction* (cost, well-formedness, forecast skill: a noop wins each), and metas grade judges on the same cards. The population's own edition-1 draft (`docs/charter/edition1-draft.md`, branch p4-E, 24 proposals, 8 passed for $0.019) proposed the antidote: a revision rate, a cap on inaction, registration flow. The runtime could measure only four quantities, so their cards would sit unpriced. Fix in flight: an observations catalogue (workstream O) so a card names what it measures and proposers see what can be measured.
4. *The seed producers were the two cheapest tiers with reasoning off.* Reseeded on GLM 5.3 Flash and DeepSeek 4.1 Flash, at parity on Venice, an architect's cast before the button.

Run 8 follows the catalogue merge, with the population's passing cards where measurable.

## T2 live acceptance

11 September 2026, America/Mexico_City. Worktree `FactoryLab-p4T2`, branch `p4-T2`;
integrated main through `ff941fb` (including H/resume, governance cadence and run-7 fixes). This is infrastructure
acceptance, not a population run or evidence of Class 3 behaviour.

**Wallet identity.** The CLI derives
`0xB3b3E605D9fdbAa4D23c048A2749A9164d16270f` from `hyperliquid.key`; it is the main
wallet, not an agent key. This is the wallet previously observed holding the real
100-USDC Hyperliquid deposit. The apparent zero came from looking at perps equity
while the mainnet UI used a unified balance. No Hyperliquid mainnet transaction was
performed for T2. A funded world should use a dedicated experiment main wallet
holding only experiment funds, with withdrawal signing confined to its server.

**Route decision.** Section 5.3 was amended by the experimenter to use native CCTP
through HyperEVM, replacing the deprecated Arbitrum bridge. The outbound adapter
uses SDK-signed `sendToEvmWithData`: HyperCore debits perps, the linked
CoreDepositWallet burns on HyperEVM, and the reserve submits its own mint on Base.
The return path approves and burns on Base, mints to the same reserve address on
HyperEVM, then approves and calls `depositFor(main_wallet, amount, 0)` to credit
perps. The manager, wallet and reconciler see only opaque steps and evidence.

Primary references, checked 11 September:

- [Hyperliquid USDC routing](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/usdc).
- [Circle native withdrawal procedure](https://developers.circle.com/cctp/howtos/withdraw-usdc-from-hypercore-to-evm),
  [CoreDepositWallet interface](https://developers.circle.com/cctp/references/coredepositwallet-contract-interface),
  [HyperCore contract addresses](https://developers.circle.com/cctp/references/hypercore-contract-addresses),
  [CCTP addresses](https://developers.circle.com/cctp/references/contract-addresses),
  and [message format](https://developers.circle.com/cctp/references/technical-guide).
- [Hyperliquid RPC](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/json-rpc)
  and [nanoreth system transaction encoding, commit 93cf469](https://github.com/hl-archive-node/nanoreth/blob/93cf469bbbe6f44708f5d646bfaacfe869955a0a/src/node/types/reth_compat.rs).
- [Circle hook construction](https://github.com/circlefin/hyperevm-circle-contracts/blob/master/src/messages/CrossChainWithdrawalHookData.sol).

Live inspection corrected two assumptions from the high-level guide. The contract
wraps custom hook data with a version, sender and nonce and requests CCTP finality
2000. Hyperliquid's official RPC exposes the system call through a custom method
but hides its receipt/logs. Circle indexes the nanoreth synthetic transaction hash.
The adapter now matches the canonical system call and exact calldata, derives that
hash, validates every immutable CCTP field, and requires the pinned destination
transmitter to accept the signed attestation in a read-only `eth_call`. That proves
the burn; a successful, finalized actual mint receipt is still required to move the
pot. System-call inclusion alone never confirms a burn.

**Testnet preparation.** Reserve address:
`0x1228e5620944a79D268Afc7522E00891526EdEBb`. The acceptance CLI pins Hyperliquid
testnet, HyperEVM chain 998/domain 19 and Base Sepolia chain 84532/domain 6. No real
funds were used to acquire test gas. Google supplied 0.05 Sepolia ETH in
`0x80535c23512e1bfff94216647923cb60c34720f4ff745561dc66fb7ce30d284e`;
the canonical Base Sepolia bridge transferred 0.001 ETH in
`0x4a2f3fc8394ff84c043139cbc477c84fab312e412f213f6ab8e760431c7e8148`.
The venue wallet bought 0.30 test HYPE with test USDC (order 59915855722), moved
0.10 HYPE to HyperEVM via Core hash
`0xad1fdce1fd456339ae99042918311b010500f4c79848820b50e88834bc493d24`,
and sent 0.05 HYPE to the reserve in
`0x3e9fe1784a516afded473625d2e861fca6605a8cce1851babe3917a4176bdf5e`.
These bootstrap actions preceded the acceptance ledger's initial balance.

**Acceptance completed at 03:04:29 UTC on 12 September.** The encrypted, restartable acceptance journal is
outside the repository at the projectless task's `work/t2-live-acceptance.jsonl`.
Its adjacent key and lock must be preserved. Initial USDC pots totalled 986.930257
(venue 986.930257, Base reserve 0). Native gas balances were 0.05 HYPE at the
HyperEVM reserve and 0.001 ETH at the Base Sepolia reserve.

| Leg | Observed evidence | Status |
|---|---|---|
| Withdraw and burn 10 test USDC | HyperCore nonce `1789177432547`, ledger hash `0xbfdb1262d524792ac1540429185eb20000162a48702797fc63a3bdb594285315`; HyperEVM system hash `0xfbb27bf157f4be3d02672aeee924fd6e58827f544c9ed090acfc437f73a2542a`; Circle/archive hash `0xfeb0a857f06578edef908512901e94b8ce202f8f69a06393c4d828d71ad4149f` | Validated against the canonical system call and destination-verified Circle attestation. Burned 10 USDC; CCTP fee 0. |
| Mint on Base Sepolia | `0x5356f36e164358944d76400810a730289423095dded19dfca30f876fa965da77`; CCTP nonce `0x26bdeda379b24e773dcb4f384025490efe053877039169a61928a6d15bef9b5b` | Finalized canonical receipt reconciled at 02:17:21 UTC on 12 September; reserve arrival confirmed at 10 USDC. Gas 1,056,152,931,299 wei ETH, valued at 2,631 micro-USD. |
| Return burn on Base Sepolia | Approval `0x8b9f025ecceb56be57a7c1b71993b6527296e08ff023ab9933edd86c26a4e31e`; burn `0x91a29aaf838932fd230e3c2c0bec711a0a650810db01f2c03a3071d40475e095`, block 46706391 | Both approval and burn finalized; reconciled at 03:02:24 UTC. Combined native gas valued at 2,422 micro-USD, with no USDC fee. |
| Mint on HyperEVM testnet | `0x4ac371e8f2a7dc90f9576d4afce0aa6280eb1b4568d9f4ec2bbd7b2e7f5247fc`; CCTP nonce `0x16519b1ceb8584ed99f6a3e52435ffc557b2969b7378439ead4800fac880b704` | Finalized mint of 10 USDC reconciled at 03:03:04 UTC; CCTP fee 0. |
| Approve CoreDepositWallet | `0xef57aae3bf639b8cfb378d53f84e134f44efd2cc4561502cae8906ba7e1685a9` | Finalized at 03:03:46 UTC. |
| HyperCore credit | Deposit `0xda7a02447339640f80ff8be1c8116b93d9eecbe04ff3257914b454b4e82ae0e3`; HyperCore ledger hash `0x47c57f8308ec6a35493f042919649e0000009768a3ef8907eb8e2ad5c7e0441f`, nonce 475810 | Finalized receipt plus unique forwarding event and matching 10-USDC perps credit confirmed at 03:04:29 UTC. |

The native HyperCore charge was 0.00002 HYPE, valued at the observed $36 HYPE mid
as 720 micro-USD. Review of that first live receipt caught an accounting defect:
the initial version also debited 720 micro-USDC even though no USDC had been spent
on gas. The corrected implementation books native gas in wei and USD fee evidence,
consumes its separate gas budget and transfer fee ceiling, and debits the kernel
only for actual USDC fees. Prefunded native inventory was never credited to the
USDC/provider-credit wallet. The existing acceptance journal retains its original
720-micro-USDC debit as historical evidence; it is not rewritten or silently
corrected. Subsequent receipts use the corrected accounting. Tests exercise both
normal settlement and process death while booking a native gas fee.

Other implementation decisions: serialize one treasury transfer per world;
reserve principal plus a $2 total economic fee ceiling before submission; use
exact-amount allowances and finalized canonical settlement receipts; allow only pinned
HyperEVM/Base native USDC contracts; bound each chain's gas separately; request
standard CCTP finality 2000 and self-mint without paid automatic forwarding.
The native route has no documented fixed minimum, so the adapter refuses amounts
at or below its conservative $1 withdrawal plus $0.10 CCTP fee coverage, as well
as amounts above the spendable source pot. The scripted rail charges a configurable
$0.01 and settles on the following tick. Mainnet routing constants exist for a
future authorized funded world; this acceptance CLI cannot select them.

The acceptance process was restarted between submission and reconciliation and
continued the same encrypted journal, transfer ID, nonce and transaction references.
It did not issue a replacement withdrawal. Both 10-USDC directions are confirmed.
Final observed pots: venue 986.930257 USDC, Base reserve 0, total 986.930257;
no transfer remains pending. Kernel balance 986.929537 retains the historical
0.000720-USDC debit described above, so its discrepancy is -720 micro-USDC,
inside the 500,000-micro-USDC reconciliation tolerance. Outbound economic gas
cost was 3,351 micro-USD and return cost 3,593 micro-USD: 6,944 micro-USD total.
These are USD valuations of test HYPE/ETH, not real-money spending. All bridge and
withdrawal USDC fees observed on this round trip were zero. The full gate is green on code commit `e24f960`:

```text
$ uv run ruff check . && uv run pytest
All checks passed!
================ 1358 passed, 5 deselected in 475.70s (0:07:55) ================
```

Five opt-in network tests are deselected by the repository default; the live
acceptance above is separate. Subsequent changes only finalize this evidence log.

**Latency corrections.** A profiled 120-event scripted workload spent 44.896 of
56.066 seconds repeatedly decrypting unchanged ledger records. The ledger now
keeps an authenticated immutable ciphertext prefix: every verification still
checks the header, length and complete prefix bytes, authenticates every new item,
and checks the final hash. Reopen authenticates from the beginning. The same
profiled workload took 13.136 seconds after this change (4.27 times faster).
Tests attempt changed, reordered and truncated cached ciphertexts and changed
on-disk bytes/header. Verification frequency and its integrity contract are unchanged.

Base approvals no longer wait for L1 finality before preparing the burn. A
canonical successful approval permits preparation of the next fixed nonce; both
references are persisted before burn broadcast. The original approval/burn pair
is rebroadcast on uncertain results. Gas remains unbooked until both receipts are
finalized, then is charged once against the native budget. Crash tests cover
approval bookkeeping, next-step preparation, submission and a lost burn reply.
The transfer's principal stays held until destination arrival. This removes one
serial Base finality wait; it cannot remove the chain's remaining finality delay.

The first live return also exposed a version-specific fee API error:
`getMinFeeAmount` reverts on Circle's Base deployments, which have no fee switch.
The adapter now omits that accessor only for the pinned Base chains; supported
fee-switch chains still require their fee check. The burn retains its explicit
maximum fee and standard finality, and attestation validation enforces the cap.
See [Circle fee/version documentation](https://developers.circle.com/cctp/concepts/fees),
checked against the live Base Sepolia response on 11 September local time.

## Compute rail proof

11 September 2026. The reserve already exists with a 0600, gitignored key. Its
verified Base mainnet address is
`0x1228e5620944a79D268Afc7522E00891526EdEBb`, chain 8453. Read-only status reported
0 native USDC, 0 ETH, 0 Venice balance, and `topup_5_affordable: false`
on the final read at about 03:03 UTC on 12 September (11 September local).
No payment authorization or mainnet transfer was submitted. Total proof spend: $0.

| Requested proof | Actual observation | Settlement / answer |
|---|---|---|
| $5 Venice tranche and `venice:z-ai-glm-5-3-flash` | Unfunded reserve; top-up and completion not submitted | None; not proved |
| FarOuter `glm-5.3-flash` | Public models list succeeded; unsigned completion request returned HTTP 402 with an `exact` Base-USDC quote of 1,000 micro-USDC ($0.001), payable to `0x8e3c3e9c91cc0161b5e1cf138180ef3641d2371e` | No signature, settlement, paid latency or real completion |
| NetIntel `glm-5.3-flash` | Public models list succeeded; unsigned request returned HTTP 400 `model_unknown` | No supported quote, settlement or completion for the requested model |
| NetIntel `gpt-4.1-mini` | Public catalogue and unsigned HTTP 402 quote: 5,000 micro-USDC ($0.005) on Base | Not purchased; the experimenter rejected the proposed OpenAI model substitution |
| FarOuter `deepseek-v4-flash` | Unsigned HTTP 402 quote: 1,000 micro-USDC ($0.001), exact Base-USDC option | No payment or completion; prepared as a different Chinese model family for the proof |
| AIspace `qwen-3-8-flash` | `GET /api/v1/models` succeeded; unsigned completion returned HTTP 402 with a 10,000 micro-USDC ($0.01) exact Base quote | No payment or completion; AIspace explicitly resells Venice and is not an independent inference backend |

These are current seller availability/quote checks, not inference proofs. The
experimenter must fund the reserve and execute the payment commands in
`docs/runs/venice-proof.md`; ambiguous payment attempts must be reconciled before
any retry. Both sellers' existing probe commands retain the default $0.10 cap.
The experimenter subsequently rejected OpenAI models for this proof and requested
more Chinese model diversity. The prepared lineup is Venice GLM 5.3 Flash,
FarOuter DeepSeek V4 Flash and AIspace Qwen 3.8 Flash. This replaces NetIntel's
proposed paid request. A fresh Venice API catalogue also listed Kimi K3, MiniMax
M3 Preview, Xiaomi MiMo V2.5 and ByteDance Seed 2.1 Turbo. These are advertised
model choices, not verified serving identities or comparative quality results.
The AIspace quote documents that reasoning can exhaust a small output cap;
the paid proof must record finish reason and nonempty answer separately from
successful settlement. It cannot count HTTP 200 with an empty answer as success.
The authorized ceiling remains $6 total; no Hyperliquid mainnet activity is part
of this compute proof.

### Live run 8 (testnet, seed 8, scoring public, reward line delivered, GLM and DeepSeek 4.1 producers)

1,110 events, 333 invocations, $0.70. **The population traded for the first time.** The antagonist sold 0.005 BTC short at 77,038; the observer bought ETH twice (one rejected, one filled at 2,491.6); both positions were open at the kill, so no return paid off yet and one lot was marked. A producer registered `trend-decider`, accepting every event kind, and it was woken 28 times. Three router proposals were refused because the proposal shape described `add` in prose and the models sent the string "true"; the shape now shows a boolean. Antagonist 31 of 40. Evaluator standing −0.20 to −0.44: judges still bless inaction while nothing pays off, as the audits predicted for this phase. The early fills are again the experimenter's pre-launch test orders, caught by the consequence cursor that still starts at zero (audit finding, fix in workstream FA).

**Bewilderment (v0.5 §9 condition 6).** Partial, and the first time with skin in the game: an unscripted assembly with a broader event appetite than any seed, and trades nobody scripted. Still nothing we could not have predicted. The honest next test is a run long enough for a position to close.

### Cold audits (12 September)

Five seats: fidelity by Codex, Fable and Opus; defects by Fable and Opus (Codex was blocked twice by its provider's classifier on this code). Reports in `docs/audits/`. Convergent findings: population cards priced but never applied; the tool sandbox is not isolation; the consequence fill cursor starts at zero; no OS lock on a ledger; vendor overruns swallowed; a torn final append bricks resume; the JSON extractor censors returns with a `}` in a rationale; the novelty reserve's sign reversed; the immune system post-mortem only; lost order acknowledgements recorded as rejections; Blum–Mansour routers crash under the standing mix; the seed prompt advertises child requests nothing handles. Two fidelity arguments recorded as dissent rather than fixed: that the public scoring block over-discloses (the essay lists reward structures among public schematics) and that `return_paid_off` is an architect's objective (the essay makes money the numéraire; run 8 is the first evidence either way). Fix workstreams FA, FB, FC.
