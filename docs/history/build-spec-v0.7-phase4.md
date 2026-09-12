# Factory Lab — build spec v0.7, phase 4

Binding for phase 4. Sections are independent workstreams; each names its files, its interfaces, and the decisions it settles. The gap list this closes is `docs/design-audit-v2.md` §7. Conventions of v0.4 §7 and `AGENTS.md` apply throughout: no new dependencies unless a section grants one, no global mutable state, money as integer micro-USD inside the kernel, Decimal only at venue boundaries, everything that changes state is a ledger item before it is a state change.

## 0. Decisions

| Topic | Decision |
|---|---|
| Versioning | An observer-side module that reads a diary and versions the factory by behaviour: per-window score profiles, cells, a transition operator, its contraction coefficient as the spectral-gap bound, version boundaries, the four pathologies, early-warning signals. Read-only; never coupled into the loop. |
| Verdict as forecast | Approved. Every verdict is also a sealed forecast that the judged return pays off, settled at the horizon, and it feeds the evaluator's consequence standing. Producers are then priced by judges who are priced by money. |
| λ from the factory | An amendment may propose a starting price for a card; adopted on passage; the controller continues from it. |
| Meta recursion | One event kind `MetaVerdict`; metas may accept `Verdict` or `MetaVerdict`; a meta is judged only if someone accepts `MetaVerdict`; depth is the population's. |
| Treasury | Compute is bought on the open market: any x402 seller is a primitive the factory can discover, register and pay per request from its own reserve; OpenRouter credits are a depleting seed nobody refills; venue↔reserve moves are real; compute insolvency is death. No human anywhere after launch. |
| Resume | Snapshot at reserve-window boundaries plus tail replay; a resumed scripted world's summary equals an uninterrupted run's. |

## 1. Versioning — `factorylab/versioning/` (workstream V)

Pure functions over a decrypted diary. Imports: stdlib, `cryptography` (already a dependency) for the reader, `factorylab.kernel.events` for event-kind names. Nothing in `factorylab.runtime` imports this package; the CLI subcommand is added separately after merge.

### 1.1 Reader — `reader.py`

`read_diary(ledger_path, key_path) -> list[dict]`: decrypt every item in seq order exactly as `runtime/cli.py::_cmd_postmortem` does (header line first, then one JSON record per line with an `item` Fernet token). Verify the hash chain (`seq`, `prev_hash`, `hash` over the canonical item as `kernel/ledger.py` computes it); raise `DiaryError` on any break. Items are returned as plain dicts.

### 1.2 Windows and profiles — `series.py`

Windows: if the diary contains `price.window` items, a window is the span between consecutive `window_end_event` values (the reserve windows the runtime already measures); otherwise consecutive blocks of `window_items` items (default 200). Every item belongs to exactly one window by seq. The first partial window is kept; the last partial window is dropped.

`Profile` per window (all floats or None when unsupported):

- `verdict`, `conformity`, `fast`, `consequence`, `exposure`: mean `return.score` over `decision.settle` items whose `return.channel` matches and `return.status == "settled"`.
- one entry per card in `price.window.values` (e.g. `cost_per_return`, `well_formed_rate`, `forecast_skill`, `turnover`).
- `noop_share`: share of `decision.open` items whose `propensity.chosen == "NOOP"`.
- `registrations`: count of `event` items whose `event.kind == "Registered"`.
- `disagreement`: mean over judged returns of the population standard deviation of raw verdicts among evaluators that judged the same return (group `event` items with `event.kind == "Verdict"` by `event.payload.about`; groups of size 1 contribute nothing).
- `balance`: last `balance_after` among `wallet.*` items in the window.

### 1.3 Cells and the operator — `operator.py`

Dimensions: the per-channel means that are supported in at least half the windows, plus every card value. Each dimension is cut into `bins` quantile bins (default 3) computed over the whole run; a window's cell is the tuple of bin indices (None dimensions map to bin −1). Transition counts between consecutive windows' cells form the operator `P`, row-normalised over occupied cells.

Spectral gap: the exact eigenvalues of a non-symmetric matrix are out of reach without numpy, so report the Dobrushin contraction coefficient `δ(P) = ½ · max_{i,j} Σ_k |P_ik − P_jk|`, which bounds the second eigenvalue modulus from above, and `gap_bound = 1 − δ(P)`. State this in the docstring. Also report the empirical `mixing`: the total-variation distance between the cell histograms of the first and second halves of the run.

### 1.4 Versions, pathologies, early warning — `versions.py`

Version boundaries: with `k` (default 3) windows per block, compare the cell histogram of each trailing block with the block before it; a boundary is a window where their total-variation distance exceeds `tv_threshold` (default 0.5). A version is the span between boundaries: `start_window`, `end_window`, `duration`, `dominant_cells` (top 3 with shares), `mean_profile`, `charter_edition` at the start (from `amendment` items with an activation, else 1).

Pathologies, each reported with the windows it covers and the evidence numbers:

- `stable_failure`: a version of duration ≥ `k` whose windows' `gap_bound` over that span ≥ `gap_threshold` (default 0.5) and whose dominant cell has a card value outside its region (regions from the latest `price.region` item for the card before that window; violation computed as `charter/controller.py::PriceController.violation` does).
- `learning_death`: ≥ `k` consecutive windows occupying a single cell with `registrations == 0` throughout.
- `thrash`: ≥ `k` consecutive windows whose successive single-window cell change is a different cell every time and whose trailing-block TV stays above `tv_threshold`.
- `overfitting_divergence`: over a version, the least-squares slope of `verdict` is positive while the slope of `forecast_skill` (or, if absent, `consequence`) is negative. Reported as evidence, never as a verdict, with both slopes.

Early-warning signals per series (`verdict`, `conformity`, `consequence`, each card, `balance`, `disagreement`): variance and lag-1 autocorrelation over trailing spans of `k`, `2k`, `4k` windows, for the last window of the run and for every version end.

Settling time: for every activated amendment, the number of windows after activation until the trailing-block TV to the block before activation falls below `tv_threshold`; None if it never does.

### 1.5 Summary — `__init__.py`

`summary(items, *, window_items=200, bins=3, k=3, tv_threshold=0.5, gap_threshold=0.5) -> dict`: JSON-serialisable with `params`, `windows` (profiles and cells), `operator` (cells, matrix, `delta`, `gap_bound`, `mixing`), `versions`, `pathologies`, `ews`, `settling`. `render(summary) -> str`: a short plain-text report (versions, pathologies, gap bound, one EWS line per series). Deterministic: the same items give the same dict.

### 1.6 Tests — `tests/versioning/`

- Operator: identity transitions give `delta = 1`, `gap_bound = 0`; uniform rows give `delta = 0`, `gap_bound = 1`; a hand-built two-cell alternation is a `thrash` candidate.
- Each pathology detector on synthetic item lists that trigger exactly it.
- Reader: round-trip against a ledger written by `factorylab.kernel.ledger.Ledger` with a key file; a tampered line raises `DiaryError`.
- End to end: run `factorylab.runtime.loop.run_world` on the `scripted` manifest for 400 events with a ledger and key in a temp dir (see `tests/runtime/test_loop.py` for the call), read the diary, compute the summary, assert the shape, and assert two runs give equal summaries.

## 2. Verdict as a consequence forecast (workstream Q) — approved 11 September

Run 4 showed the flaw: producers returned `noop` on every invocation and evaluators graded that 0.8 to 1.0 against "care with scarce resources". The verdict channel prices charter conformity; the consequence channel prices the evaluators' separate predicate forecasts; nothing ties a verdict to money. The essay's consequence signal is "whether a given verdict predicted real downstream outcomes", so the verdict itself must be the prediction.

Physics: when a verdict `v` on a producer (or antagonist) return is delivered, the runtime also seals a forecast on the kernel predicate `return_paid_off` with `q = v`, addressed to the evaluator's consequence standing like any forecast. It settles when the return's realized consequence is known:

- Realized means realized. A return's lots are the fills its own orders produced (market, limit, reduce-only, close, all tools called from that return). Lots close first-in-first-out against later fills on the same coin and side, whoever placed them; the closing fill realizes P&L to the lot's opening handle at the fill price, net of the fees of both fills and the funding paid while the lot was open. The return pays off (1) when every lot it opened is closed and the sum of realized P&L exceeds the return's own compute cost (including tool-call charges); otherwise 0.
- Backstop: if any lot is still open after `evaluation.consequence_backstop_events` (default 200), the open remainder is marked to the venue mid at that event and the predicate settles on that value; the settlement item records `marked: true`. A liquidation closes lots like any fill and carries the liquidation loss.
- A return that placed no fills settles 0 as soon as its own cost is known (immediately). Doing nothing has a strictly negative realized consequence in a world where thinking costs money; this is arithmetic, not a rule against inaction. The question "would nothing have been better" belongs to the routers' propensity counterfactuals, not to this predicate.
- The predicate is kernel physics: not a charter card, not amendable, not in the vocabulary the population may propose against. It sits outside the factory's input, as the essay requires of realized consequence.

Reward-hacking review is part of the workstream. Before writing code, enumerate the ways an evaluator or producer could game `return_paid_off` and design against each, and list the residual ones in the PR: wash trades (opening and closing against oneself to manufacture tiny realized gains; fees make these negative, verify), self-crossing limit orders, closing another return's lot to realize its gain (FIFO attribution keeps the gain with the opener), splitting a position across many returns, judges blessing only returns that never place orders (settles 0 every time, so a judge who does this scores worse than the prevalence baseline), judges colluding with a producer by giving `v` near the base rate (the baseline comparison already prices this), gaming the backstop by holding losers open (marked to mid, loss realized), and any rounding edge in micro-USD. Where a hack survives, say so and price it if the physics allows.

Files: `settlement/vocabulary.py` (kernel predicate, flagged non-proposable), `settlement/forecast.py` or a new `settlement/lots.py` (lot table, FIFO, fees, funding, backstop marking), `runtime/loop.py` (seal on verdict delivery, maintain lots from `Fill` events including liquidation, settle the predicate, charge tool costs to the return), `runtime/worlds.py` (`consequence_backstop_events`), tests including a scripted world where a judge who blesses noops loses consequence standing to one that does not.

Deliberately not built: rewarding producers directly for wallet growth (that is the architect writing an objective), or a norm that says "grow". The pressure arrives only through judges answering to money.

## 3. Population-proposed λ (workstream C)

An amendment operation may carry `"lambda": number` in `[0, prices.lambda_max]` for the card it adds or replaces. On activation, the runtime calls `PriceController.set_price(card_id, value)` (new method: ledger item `price.proposed` with the amendment id, then the state change) before continuing the controller's own rule from that value. Removed cards drop their price. Files: `charter/amendment.py`, `charter/book.py`, `charter/controller.py`, `runtime/loop.py::_activate_charter_if_due`, tests. Cards without a proposed price keep the controller's price.

## 4. Recursive meta-evaluation (workstream R)

New `EventKind.META_VERDICT` (`"MetaVerdict"`, payload `about` = the judged decision handle, `tier` ≥ 2, `score`, `by`). Registration: role `meta` may accept exactly one of `Verdict` or `MetaVerdict`. Routing: `MetaVerdict` events route to metas that accept them; `_role_for_kind("MetaVerdict") == "meta"`. A meta judging a `Verdict` publishes `MetaVerdict` with `tier = 2`; a meta judging a `MetaVerdict` publishes `tier = n + 1`. A meta never judges its own return (skip in the universe for that event).

Channels: a meta decision opens on `conformity` if any registered assembly accepts `MetaVerdict` at the moment it opens, else on `fast` as today. A `conformity` meta decision is settled by the first higher-tier `MetaVerdict` about it within `verdict_timeout_events`, else censored like an unjudged producer. Seeds unchanged; the population adds depth by registering a meta that accepts `MetaVerdict`. Files: `kernel/events.py`, `cortex/registration.py`, `runtime/loop.py`, tests.

## 5. The treasury made real: compute bought on the open market (workstream T)

Revised 11 September after two research passes (`docs/research/venice.md`, and the x402 discovery index). The rule that decides everything: the factory may not depend on the experimenter for anything after launch, and compute is the dependency that mattered. OpenRouter cannot be refilled by a program. So compute must be bought by the factory, with its own money, from sellers it can pay on-chain, and there must be more than one seller so it is not captive.

### 5.1 Pots and the identity

The kernel wallet stays one number. Pots are a view: `venue` (Hyperliquid equity), `reserve` (USDC at the factory's own address on Base, `reserve.key`), `seed` (the OpenRouter credits the experimenter bought before launch: a depleting endowment that nobody ever refills), and any wallet-bound seller balances the factory chose to hold (Venice tranches). Identity checked by the reconciler within $0.50: `wallet == venue + reserve + seed + Σ seller balances`; drift is a `reconcile.drift` item and a `Reconciled` event, never corrected silently.

### 5.2 Sellers are primitives

An x402 seller is any OpenAI-compatible chat-completions endpoint that answers a request with HTTP 402 and an `exact`-scheme quote on `eip155:8453` (USDC). The factory pays per request: the reserve key signs the quoted authorization, the facilitator settles, the response comes back; the kernel meters exactly the amount paid, and the reservation ceiling is the quote. No float, no tranche, no human.

- World tool `market.discover(url_substring | query, limit)`: reads the x402 discovery index (`https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources`, public, paginated) and returns sellers with their listed prices and networks. Public schematic; priced like any world read.
- Model proposal namespace `x402:<seller_url>#<model_id>`. On registration the runtime reads the seller's `/v1/models` for per-token prices when it has one, else takes the 402 quote as the per-request price; the capability is registered with that price and the seller's network. Anything else about the seller is for the factory to learn: a seller that fails, stalls or returns garbage produces a malformed return, gets judged, and its routers learn.
- Venice stays a seller with a wallet-bound balance: `venice:<id>` models are paid from the balance, and `treasury.transfer(to_venice, 5)` is a real $5 tranche from the reserve (the x402 client already built). The per-request path is the default; the tranche path exists because Venice's catalogue is wider than most sellers'.
- The OpenRouter `seed` pot pays for `openrouter` models until it is gone. When it is gone, those capabilities become infeasible (cost ceiling unmet), exactly like any other unaffordable primitive.

### 5.3 Venue ↔ reserve, for real

Route decided 11 September on the implementer's finding that Hyperliquid now recommends native CCTP and marks the Arbitrum bridge deprecated: Hyperliquid ↔ HyperEVM ↔ Base through Circle's CCTP, no Arbitrum hop. The route lives behind one module with a narrow interface; the ledger, pots and reconciler never know which bridge is underneath.

`treasury.transfer(to_reserve, usd)`: a real Hyperliquid withdrawal (SDK, main-wallet key) to the reserve address on HyperEVM, then a CCTP burn on HyperEVM and mint on Base to the same address (contract calls through raw JSON-RPC with `eth_account`; the reserve holds a small gas budget on HyperEVM and Base, booked as a fee when spent). `treasury.transfer(to_venue, usd)`: the reverse. Two-phase and ledgered: `treasury.submitted` with tx references, then `treasury.confirmed` or `treasury.failed` on reconcile; pots move on confirmation. Amounts below the venue minimum or above the pot are refused with the reason in the tool result. Live acceptance on Hyperliquid testnet, HyperEVM testnet and Base Sepolia, tx references in the build log; mainnet only under the `funded` manifest.

### 5.4 Insolvency

If no affordable provider exists for a routed decision (reserve below every quote, seed empty, seller balances empty) for `treasury.insolvency_events` consecutive events (default 20), the world terminates with `insolvency:compute`. A factory that lets its compute money run dry with money in the venue died of its own liquidity management. This is the essay's "$0 token budget" made exact.

### 5.5 Live funding payments

The Hyperliquid adapter reports funding rates but not funding paid. The lot table (§2) needs actual payments: add `funding_payments(since_ns)` to the exchange protocol from the venue's user-funding history and have the reconciler emit Funding events with real `paid_usd`.

### 5.6 Workstreams and acceptance

- **T1** (now): x402 per-request provider, `market.discover`, the `x402:` proposal namespace, metering at the paid amount, the insolvency rule, `factorylab probe --provider x402 --seller URL --model M` paying one real request from the reserve. Acceptance: HTTP-faked tests for the 402 loop, metering equality with the quote, discovery parsing, proposal registration and feasibility; then one real request bought from the reserve for cents and recorded in the build log.
- **T2** (after resume): venue↔reserve real moves with CCTP, pots view, identity reconcile, funding payments. Acceptance: on Hyperliquid testnet and Arbitrum/Base Sepolia end to end, then one real $10 round trip, both logged with tx references.
- The `funded` manifest waits for both, plus resume, hosting and the cold audits.

## 6. Resume (workstream H)

`factorylab resume --world W --ledger L` continues a world whose process died. The key file next to the ledger is read by the process (not a person; the covenant is about people). At every reserve-window boundary the runtime writes a `snapshot` item: learner states (`state()`/`restore()` on every learner and router, to be added), queue (open decisions, pending judgements, exposures), registry, assemblies and memory, charter book and controller, stats, `n`, clock. Resume loads the last snapshot, replays every later item through the same code paths that wrote them (settlements into learners and the queue, registrations into the registry, price updates into the controller), then reconciles the venue (positions, equity) and marks decisions whose deadlines passed during the outage as timeouts with a `resume.timeouts` item. Acceptance: a scripted run killed at event 250 (`SIGKILL`) and resumed produces a final summary equal to an uninterrupted run's summary field by field, except `stats.resumes`. Live: one testnet world killed and resumed with an open position, reconciled, logged.

## 7. Phase 4 completion condition

1. `uv run ruff check . && uv run pytest` exits 0 on main after V, C, R, T, H (and Q if approved) are merged.
2. `factorylab versions runs/testnet-fourth.jsonl runs/testnet-fourth.jsonl.key` prints versions and flags `learning_death` or `stable_failure` for run 4's noop attractor.
3. A scripted world killed and resumed matches its uninterrupted twin.
4. A Hyperliquid testnet world executes one withdrawal and one deposit through the factory's own tool call, confirmed on chain.
5. A live run after Q (if approved) shows evaluators' consequence standing diverging between judges who bless `noop` and judges who do not.
6. Bewilderment (v0.5 §9 condition 6) re-checked on that run and written up honestly.

## 8. The clock is the factory's (workstream K)

Fidelity note. The essay puts only relational timing in the kernel (the cascade ratio and jitter) and says the architect should not encode "the actual frequency bands themselves"; it also says speed is indistinguishable from cash burn, hence a constraint, hence the charter's. A tick interval only the architect can change is a frequency band fixed by fiat. So the tick is a charter setting the committee amends, and the kernel holds only bounds that are physics, not cost caps.

- Bounds (kernel): `min_tick` is what the venue and the runtime can physically sustain, seed `10s`; `max_tick` is derived, never chosen: the tick must close at least `timing.min_ratio` times per reserve window, so `max_tick = novelty.window / timing.min_ratio` (20 minutes for a one-hour window).
- Charter: an amendment may carry `tick_interval` (duration string within the bounds); the committee votes as for any amendment; on activation the live clock adopts it from the next tick; ledger item `clock.changed` with edition, old and new intervals; the world block shows the current interval and the bounds.
- Launch value for the funded world: five minutes, the architect's cast for edition 1, recorded as such in the manifest. Scripted and testnet manifests unchanged unless amended.
- Files: `charter/amendment.py`, `book.py`, `runtime/worlds.py` (`[clock]` with `min_tick`; validation of the seed interval against both bounds), `runtime/live.py` (LiveClock with a mutable interval), `runtime/loop.py` (activation), tests including a scripted amendment that changes the clock and a rejected one outside the bounds.

## 9. The charter in the manifest (workstream M)

Edition 1 of the funded world is the population's draft (`docs/charter/edition1-draft.md`), not the phase 2 seed cards, so the manifest must carry a charter. `[charter]` table: `norms` (list of strings; the architect's, read-only for the population) and `[[charter.cards]]` entries with the `MetricCard` fields (`id`, `norm`, `description`, `units`, `window`, `acceptable_region`, `observation`), where `observation` must name an id in the observations catalogue and `acceptable_region` must be a phrasing `runtime/cards.py` parses; validation fails the manifest load otherwise, naming the card and field. Optional `lambda` per card seeds the controller's starting price. When the table is absent, the seed charter applies unchanged (scripted and testnet manifests stay as they are). The manifest hash covers the charter. `factorylab manifest --world W` prints the resolved charter. Tests: a manifest with a valid charter loads and the runtime renders it as edition 1; an unknown observation or an unparsable region fails load with the reason; hash changes with the charter.
