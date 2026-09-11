# Design audit v2 — the essay against the build

Written 11 September 2026 after a full re-read of *The Superdark Factory* (Poliks, Trillo, Dunn, Scott-Douglas, Springett), with phases 1–3 merged, phase 3b on a branch, and live testnet runs 1–4. Purpose: before real money, walk every principle of the dark stack and say honestly whether it is built, partly built, or missing, and who closes each gap. This supersedes the fidelity notes scattered through `docs/build-log.md` as the single checklist; the build log stays the record.

Legend: **built** = in main or on `p3b-runtime` with tests; **partial** = present but less than the essay asks; **missing** = not in the code at all.

## 1. Chapter I — darkness, the architect, the covenant

| Principle (essay) | Status | Evidence / gap |
|---|---|---|
| The architect makes one committed move and does not intervene afterwards; changing the kernel is the old world's death and a new v0. | built | Non-intervention covenant in `docs/handoff.md`; `--kill-at-end`; a killed world's diary is read post-mortem only; a changed manifest is a new world name. |
| Bewilderment is the success criterion: if the factory is immediately comprehensible to its architect, the architect failed. | built as a check, unmet as a result | Completion condition 6 in spec v0.5 §9. Run 3 met the minimum (unscripted registrations, routers whose menus the seed did not have). Nothing yet that we could not have predicted. This stays the governing test for every later run. |
| Exposure cap and covenant on S (Knightian ruin): bound the investment, tripwire on ruin. | built | The wallet is the whole exposure ($100); ruin = balance floor = death; the only kill button is the world's death. |
| The factory must have a self-model drawn from its wake, not from a privileged inner record; dark to itself as a whole. | built | Assemblies see the account statement, recent mids, their own last returns and verdicts (private), the roster and routers (public schematics). No part sees the ledger or the learners' state. |
| The human is not an occupant of any interior role. | built | No human channel exists in the loop. The one remaining human act, executing the treasury's transfer intents by hand, is removed by §5 below. |

## 2. Chapter II.I — the primitive

| Principle | Status | Evidence / gap |
|---|---|---|
| Composable primitives with explicit contracts; no semantic coupling; the contract read "at the last instance". | built | Assemblies, models, routers, tools are registry contracts; every request carries the public schematics and exact return shapes (v0.4 §1.6, added after run 2). |
| Seed both mean-based no-regret learners at the frontier and no-swap-regret learners at the core; the population can build the latter from the former (Blum–Mansour). | built | EXP3 routers seeded; `blum_mansour` routers registrable by proposal and used for comparator epochs; SnapshotLearner handles delayed feedback. Verified in `tests/learners/` and `tests/runtime/test_loop.py`. |
| Rich request line; thin, stateful reward line carrying a propensity score and a persistent decision handle. | built | `DecisionQueue`, `PropensityRecord` (replays its sample), score channels `verdict`/`conformity`/`fast`/`consequence`/`exposure`. |
| Minimal sufficient disclosure: schematics public, local state private, propensity forwarded. Dissensus seeded across the population. | built | Public block vs private memory; five model families in `worlds/testnet.toml`. |
| A share of compute and write access reserved for unhistoried actions (learning-death prevention as physics). | built | `NoveltyReserve` (10% share, windowed); every registration draws on it. |
| The population discovers primitives it was not given. | built | Model proposals may name any id in the OpenRouter catalogue (`Runtime.catalogue`), any reasoning level, the `:online` web variant; tool proposals register sandboxed code; router proposals add or replace routers. |
| Requests are author-neutral (author irrelevant, fungible or private). | to verify | Evaluator requests carry the producer's assembly id today. The essay wants the author irrelevant or private. Decision below (§6, D1). |

## 3. Chapter II.II — versioning

| Principle | Status | Evidence / gap |
|---|---|---|
| Version the factory by what it does, not what it is: partition judge-score profiles into cells, count transitions (the transfer operator), read the spectral gap; a version is a near-invariant region. | **missing** | Nothing in the code computes versions. `report` prints counts and means. This is one of the four pillars and it is absent. Workstream V (§7). |
| Detect the four pathologies live: stable failure (wide gap, failing input), overfitting, learning death (one state, no variety), thrash (unsettled gap). | **missing** | No detector. The price controller prices card violations only. Workstream V adds detection; workstream P prices duration of stable failure and duration of gap volatility, as the essay asks. |
| Early-warning signals: multiscale variance, autocorrelation, ensemble disagreement across heterogeneous judges. | **missing** | Not computed. Ensemble disagreement is cheap (several evaluators judge the same return already). Workstream V. |
| Kernel = thin set of hard casts, inviolable from inside and outside, understood by the population as a fact about the world, not a rule. | built | Kernel package; nothing in a request states a rule; manifests are physics. |
| Soft casts as priced penalties: subtract λ·cost from rewards, λ set by a controller (PID suggested: P raises with violation, I accumulates sustained violation, D dampens overshoot). | partial | `PriceController` (PR #17, wiring in flight): proportional step plus decay, clipped, one update per settled window, ledgered. No integral memory, no derivative damping. Acceptable for a $100 world; note it. Workstream P may add I and D if versioning shows oscillation. |
| A kill switch that nukes identity must always be on the table. | built | Death by balance floor; death by kill; no resurrection under the same name. |

## 4. Chapter II.III — evaluations

| Principle | Status | Evidence / gap |
|---|---|---|
| Online, continuous, in production; no preproduction gate. | built | Evaluators run inside the loop on real returns. |
| Requisite variety: more mean-based than swap-based judges; evaluators outnumber producers and consume more compute. | built | 4 evaluators + 2 metas vs 2 producers + 1 antagonist; evaluator selection is a no-regret router with a protected consequence share. |
| Evaluations of evaluations, recursively, "to some arbitrary level decided by the aggregate apparatus". | partial | Meta-evaluation stops at one tier: metas accept exactly `Verdict`, and nothing judges a meta. Workstream R lets the population register judges of any verdict tier (`MetaVerdict` event, role `meta` accepting `Verdict` or `MetaVerdict`), so depth is the factory's choice. |
| The signal that grades an evaluator sits outside the loop it judges (realized consequence). | built | Forecasts settle against the wallet (Brier vs prevalence baseline); `ConsequenceStanding` with coverage cap. |
| Adversarial minority, internal, producing real failures to farm consequence scoring; not staged. | built (on branch) | `antagonist` role, judged like a producer, scored on `exposure` (fooled a judge). Trades the same real account within the same limits. |
| Producers and evaluators must not be able to collude toward overfitting. | built | Consequence channel + antagonists + protected share; population balance rule in v0.5 §3.6. |
| Writing the first eval is the architect's most direct act of governance. | built | Seed charter: four norms, three cards. |

## 5. Chapter II.IV — the charter and the loop

| Principle | Status | Evidence / gap |
|---|---|---|
| Charter as editioned input; every edition preserved; the factory co-writes it; norms read-only, metrics ceded. | built | `CharterBook`, amendments to cards only, editions, sortition committee (5 seats by lot, 3 of 5), activation at the next reserve-window boundary. |
| λ is a shadow price the factory helps set; "λ cannot be determined without collaboration with the factory". | **missing** | Only the controller sets λ; amendments cannot propose a price. Workstream C: an amendment may carry a proposed λ for a card; if it passes, the controller adopts it as the new starting price and continues from there. Cheap, and it is the essay's explicit ask. |
| Governance cadence: the charter loop must run slower than the slowest inner loop (3:1 to 10:1), measured by settling time after a small deliberate revision. | partial | Charter activation waits for the reserve window (1 h) and the timing registry enforces 3:1 with jitter upward. Nobody measures settling time. Workstream V reports it; the manifest window stays the architect's cast. |
| Cascade control: verdicts buffered upward with jitter; downstream commands rich and stateless; upper layers starved of variety. | built | `TimingRegistry`/`UpwardBuffer`; metas see verdict distributions, not raw returns. |
| Requisite velocity: fast enough that the environment cannot outrun the factory, slow enough that counterfactuals are worth keeping. | partial | Tick is the world clock (60 s live). Routers decide per event; assemblies may accept any event kind; positions may persist across ticks. The clock itself is a cast. Fine for a $100 world on Hyperliquid perps. |
| Token budget of $0 is the only kill switch greater than kernel teardown. | built | `balance_floor_usd = "0"`. |
| Futarchy for constraints, authoritarian for norms. | deliberately not built | The essay reports mixed results and a $100 world has no market depth for it. Norms are read-only, constraints go through the committee. Revisit only if a later world is large. |
| Entrainment: avoid a single foundation model as a global forcing function; diversify dependencies. | built | Five families; OpenRouter catalogue open to proposals. Single-vendor risk remains OpenRouter itself and Hyperliquid itself; accepted for this scale. |

## 6. Our own physics — money, which the essay leaves to us

| Element | Status | Gap |
|---|---|---|
| One wallet, integer micro-USD, conservation, reserve/commit/settle, drip, death from the world not from thinking. | built | — |
| Compute metered at the provider's reported cost; reasoning depth purchasable per capability. | built | — |
| Venue: ten tool contracts over Hyperliquid; fills, funding, liquidation mirrored to the wallet; reconciler on restart. | built | Run 5 with the testnet key still owed: no run has had real fills yet. |
| Money can leave the account in true reality on the factory's decision. | **missing** | Today `treasury.transfer` writes an intent and a human executes it. That is a human occupant of an interior role and a fake lever. Workstream T (below) makes it real. |
| A process crash on a hosted world is not the world's death. | **missing** | No `resume`. The reconciler reads venue state but the runtime cannot rebuild learners, queues and the charter book from the ledger. For a funded world this is mandatory: a droplet reboot must not kill the factory or, worse, leave positions open with no one watching. Workstream H. |
| Cold audits of money paths, sealing and the sandbox by a fresh reader. | pending | Codex, after T and H land. |

### The treasury, made real (workstream T)

Three pots. (1) Hyperliquid margin. (2) A reserve address on Arbitrum owned by the factory (a second key on the server). (3) The OpenRouter prepaid float. The kernel wallet remains the single accounting truth and meters every model call, so the float is a payment rail, not a balance the factory sees.

- `treasury.transfer(to_reserve, usd)` executes a real Hyperliquid withdrawal to the reserve address through the SDK (main-wallet signature, ~$1 fee, ~5 min); the wallet books the fee and moves the amount from `venue` to `reserve`. `treasury.transfer(to_venue, usd)` executes a real deposit from the reserve (USDC on Arbitrum to the bridge). Both are ledgered with tx hashes and reconciled on confirmation.
- The float rule is mechanical and outside the factory's sight: when the OpenRouter balance falls below a threshold, the experimenter converts reserve USDC into credits, only ever from the reserve, and records it. No timing choice, no amount choice beyond "what is in the reserve". Credits are one-way, so the reserve is the only pot the factory can move both ways; that asymmetry is real and is exposed as such.
- Testnet first: Hyperliquid testnet withdrawals to an Arbitrum Sepolia address, verified end to end, before the `funded` manifest exists.
- A subagent checks whether any model provider now sells inference for on-chain payment programmatically; if one exists with a usable catalogue, the float step disappears and the factory holds compute money directly.

## 7. Workstreams to close the gaps, in order

| # | Workstream | Implementer | Closes | Notes |
|---|---|---|---|---|
| 0 | Merge controller wiring (in flight) and `p3b-runtime`. | Fable subagent; review by the session | §3 soft casts, §4 antagonists, treasury intent (interim) | Full gate, fidelity note in the build log. |
| 1 | Run 5 with the Hyperliquid testnet key: real fills, real P&L; bewilderment check. | session | §6 venue | Read-only runs are over. |
| T | Real treasury (above), testnet first. | Codex, effort xhigh (money path) | §6 | Touches `world/live.py`, `runtime/loop.py` treasury tool, wallet pots, manifest `[treasury]`. |
| H | `resume`: rebuild runtime state from the ledger + venue reconcile; supervisor-safe. | Codex, effort xhigh (money path) | §6 | Sequential with T (both touch the runtime). |
| V | Versioning module: transfer operator over per-card score cells, spectral gap, version boundaries, four-pathology flags, EWS (variance, autocorrelation at several scales, ensemble disagreement), slowest-loop settling time. Feeds `report` and the summary; read-only on the world. | Codex, effort high | §3 versioning, EWS, §5 cadence | Pure function of the ledger; no runtime coupling. |
| P | Price duration terms: stable-failure duration and gap-volatility duration priced through the controller (optionally I and D terms). | Codex, effort medium | §3 pathologies as penalties | After V, since it consumes V's signals. |
| C | Population-proposed λ: amendments may carry a price; adopted on passage. | Codex, effort medium | §5 shadow price | Small change in `charter/amendment.py`, `book.py`, runtime `_activate_charter_if_due`. |
| R | Recursive meta-evaluation: `MetaVerdict` events; metas may accept `Verdict` or `MetaVerdict`; population chooses depth. | Codex, effort medium | §4 recursion | Registration rules + one runtime step. |
| A | Cold audits: money paths, ledger sealing, sandbox on Linux. | Codex, fresh worktree, effort xhigh | §6 | After T and H. |
| D | Hosting: DigitalOcean droplet, systemd unit with restart, nightly encrypted backup of ledger + keys, alert on death. | Fable subagent | §6 | After H. |
| F | `funded` manifest, only after 0–D. Then no changes, ever. | session + experimenter | — | — |

Parallelisable: V and C and R touch disjoint files and can run in three worktrees at once. T then H are sequential. A and D wait.

## 8. Decisions for the experimenter

- **D1. Author neutrality.** Strip the producer's assembly id from evaluator requests (evaluators judge the return, not the author)? The essay says the author should be irrelevant or private. Recommendation: yes, replace the id with the return's decision handle; standing is tracked by handle already. Cost: nil. Risk: evaluators lose the ability to punish a repeat offender by name, which the essay would call semantic coupling anyway.
- **D2. Open catalogue spend.** The population may already register any OpenRouter model, including expensive ones, paid from the same wallet. Keep that open (recommended: it is the wallet's money and the physics price it) or cap by a manifest ceiling on $/Mtok? Recommendation: keep open; the reservation ceiling and the wallet are the cap.
- **D3. Float threshold.** How low may the OpenRouter float fall before you convert from the reserve? Recommendation: $10, checked daily; recorded each time.

## 9. What is deliberately not built, and why

- Futarchy (§5): no market depth at $100.
- Reproduction of child factories (Chapter III): out of scope for a lab.
- Population changes to the world clock: the clock is a cast; the population can already choose which events to wake on.
- PID derivative term: only if versioning shows controller-driven oscillation.
