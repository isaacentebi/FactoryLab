# Factory Lab — build spec v0.7, phase 4

Binding for phase 4. Sections are independent workstreams; each names its files, its interfaces, and the decisions it settles. The gap list this closes is `docs/design-audit-v2.md` §7. Conventions of v0.4 §7 and `AGENTS.md` apply throughout: no new dependencies unless a section grants one, no global mutable state, money as integer micro-USD inside the kernel, Decimal only at venue boundaries, everything that changes state is a ledger item before it is a state change.

## 0. Decisions

| Topic | Decision |
|---|---|
| Versioning | An observer-side module that reads a diary and versions the factory by behaviour: per-window score profiles, cells, a transition operator, its contraction coefficient as the spectral-gap bound, version boundaries, the four pathologies, early-warning signals. Read-only; never coupled into the loop. |
| Verdict as forecast | Decision pending with the experimenter (§2). Proposed: every verdict is also a sealed forecast that the judged return pays off, settled at the horizon, and it feeds the evaluator's consequence standing. Producers are then priced by judges who are priced by money. |
| λ from the factory | An amendment may propose a starting price for a card; adopted on passage; the controller continues from it. |
| Meta recursion | One event kind `MetaVerdict`; metas may accept `Verdict` or `MetaVerdict`; a meta is judged only if someone accepts `MetaVerdict`; depth is the population's. |
| Treasury | Three pots; venue↔reserve moves are real and executed by the factory's tool; reserve→float top-ups are the operator's mechanical rule outside the factory's sight; compute insolvency is death. |
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

## 2. Verdict as a consequence forecast (workstream Q) — decision pending

Run 4 showed the flaw: producers returned `noop` on every invocation and evaluators graded that 0.8 to 1.0 against "care with scarce resources". The verdict channel prices charter conformity; the consequence channel prices the evaluators' separate predicate forecasts; nothing ties a verdict to money. The essay's consequence signal is "whether a given verdict predicted real downstream outcomes", so the verdict itself must be the prediction.

Proposed physics: when a verdict `v` on a producer return is delivered, the runtime also seals a forecast on the predicate `return_paid_off` with `q = v` and `horizon_events = evaluation.forecast_horizon_events`, addressed to the evaluator's consequence standing like any forecast. `return_paid_off` settles 1 if the judged return's own realized effect over the horizon is positive: the mark-to-market P&L at the horizon of the fills that return placed (plus what it realized by closing), minus that return's own compute cost; 0 otherwise. A `noop` return therefore pays off never (it cost compute and placed nothing); a hold of an existing position pays off with the position's owner, not with the holder. Evaluators who bless inaction are priced for it; evaluators who bless trades are priced by the market. Producers stay priced by verdicts. Nothing else changes.

Attribution needs a lot table: every fill carries the decision handle that placed it; a lot is (handle, coin, side, size, entry price); closes reduce lots first-in-first-out and realize P&L to the opening handle. Files: `settlement/vocabulary.py` (predicate), `settlement/forecast.py` (settle rule with a `lot_table` input), `runtime/loop.py` (seal on verdict delivery; maintain lots from `Fill` events), tests.

Deliberately not proposed: rewarding producers directly for wallet growth (that is the architect writing an objective), or a norm that says "grow". The pressure arrives only through judges answering to money.

## 3. Population-proposed λ (workstream C)

An amendment operation may carry `"lambda": number` in `[0, prices.lambda_max]` for the card it adds or replaces. On activation, the runtime calls `PriceController.set_price(card_id, value)` (new method: ledger item `price.proposed` with the amendment id, then the state change) before continuing the controller's own rule from that value. Removed cards drop their price. Files: `charter/amendment.py`, `charter/book.py`, `charter/controller.py`, `runtime/loop.py::_activate_charter_if_due`, tests. Cards without a proposed price keep the controller's price.

## 4. Recursive meta-evaluation (workstream R)

New `EventKind.META_VERDICT` (`"MetaVerdict"`, payload `about` = the judged decision handle, `tier` ≥ 2, `score`, `by`). Registration: role `meta` may accept exactly one of `Verdict` or `MetaVerdict`. Routing: `MetaVerdict` events route to metas that accept them; `_role_for_kind("MetaVerdict") == "meta"`. A meta judging a `Verdict` publishes `MetaVerdict` with `tier = 2`; a meta judging a `MetaVerdict` publishes `tier = n + 1`. A meta never judges its own return (skip in the universe for that event).

Channels: a meta decision opens on `conformity` if any registered assembly accepts `MetaVerdict` at the moment it opens, else on `fast` as today. A `conformity` meta decision is settled by the first higher-tier `MetaVerdict` about it within `verdict_timeout_events`, else censored like an unjudged producer. Seeds unchanged; the population adds depth by registering a meta that accepts `MetaVerdict`. Files: `kernel/events.py`, `cortex/registration.py`, `runtime/loop.py`, tests.

## 5. The treasury made real (workstream T)

### 5.1 Pots and the accounting identity

The kernel wallet stays one number. Three pots are a view of it: `venue` (Hyperliquid equity), `reserve` (USDC at a factory-owned address on Arbitrum), `float` (unspent OpenRouter credits). Identity, checked by the reconciler: `wallet == venue + reserve + float` within a tolerance of $0.50; drift is a `reconcile.drift` ledger item and a `Reconciled` event, never silently corrected.

Compute spend reduces `float` and the wallet by the metered cost (as today). Trading changes `venue` and the wallet (as today via the reconciler). `treasury.transfer(to_reserve, usd)`: a real withdrawal from the venue to the reserve address; `venue −usd`, `reserve +usd − fee`, wallet `−fee`. `treasury.transfer(to_venue, usd)`: a real deposit from the reserve to the bridge; `reserve −usd − gas`, `venue +usd`, wallet `−gas`. Both are two-phase: `treasury.submitted` (tx reference) then `treasury.confirmed` or `treasury.failed` on the next reconcile; the pots move on confirmation. Amounts below the venue's minimum, or above the pot, are refused with the reason in the tool result and a `treasury.refused` item.

The float top-up is not a factory action: when `float < treasury.float_floor_usd`, the operator converts `min(reserve, treasury.float_target_usd − float)` from the reserve into OpenRouter credits and records it with `factorylab treasury topup --usd X --tx ...` (a CLI command that appends `treasury.topup` and adjusts the pots; nothing else). The world block shows the three pots and the floor.

### 5.2 Compute insolvency

If the provider refuses calls for lack of credit (OpenRouter 402, or the credits endpoint reporting a balance below one request's ceiling) for `treasury.insolvency_events` consecutive events (default 20), the world terminates with `termination_reason = "insolvency:compute"`. The factory that lets its float run dry with money in the venue has died of its own liquidity management, which is the essay's "$0 token budget" made exact.

### 5.3 Interfaces

`world/treasury.py`:

```python
class Treasury(Protocol):
    def withdraw_to_reserve(self, usd: Decimal) -> TxRef
    def deposit_to_venue(self, usd: Decimal) -> TxRef
    def status(self, tx: TxRef) -> Literal["pending", "confirmed", "failed"]
    def reserve_balance_usd(self) -> Decimal
    def float_balance_usd(self) -> Decimal            # OpenRouter GET /api/v1/credits: total_credits − total_usage
```

`FakeTreasury` for scripted worlds: instant confirmation on the next tick, fixed fee $1.00, gas $0.05, float tracked from metered spend. `HyperliquidTreasury`: withdrawals through the SDK's bridge withdrawal signed by the main wallet key (`HL_PRIVATE_KEY`); deposits as an ERC-20 USDC transfer from the reserve key (`RESERVE_PRIVATE_KEY`, loaded from `reserve.key` like the others) to the Hyperliquid bridge on Arbitrum via raw JSON-RPC with `eth_account` signing (already present transitively); `web3` may be added as a dependency only if raw signing of the transfer proves impractical, and the PR must say so. Testnet: Hyperliquid testnet and Arbitrum Sepolia; mainnet addresses only when the manifest is named `funded`.

Manifest: `[treasury] float_floor_usd, float_target_usd, insolvency_events, reserve_address`. Wallet: `pots()` view derived from ledger items; the venue's `sync_cash` and the reconciler use the `venue` pot only.

### 5.4 Acceptance

Scripted world with `FakeTreasury`: the scripted transfer at call 120 moves pots, fees land in the wallet, conservation holds, the identity holds at every reconcile; a scripted insolvency (float forced to zero) terminates with `insolvency:compute`. Testnet: one real withdrawal and one real deposit on Hyperliquid testnet, both confirmed, recorded in the build log with tx references. Keys are never read, printed or committed by anyone but the process.

## 6. Resume (workstream H)

`factorylab resume --world W --ledger L` continues a world whose process died. The key file next to the ledger is read by the process (not a person; the covenant is about people). At every reserve-window boundary the runtime writes a `snapshot` item: learner states (`state()`/`restore()` on every learner and router, to be added), queue (open decisions, pending judgements, exposures), registry, assemblies and memory, charter book and controller, stats, `n`, clock. Resume loads the last snapshot, replays every later item through the same code paths that wrote them (settlements into learners and the queue, registrations into the registry, price updates into the controller), then reconciles the venue (positions, equity) and marks decisions whose deadlines passed during the outage as timeouts with a `resume.timeouts` item. Acceptance: a scripted run killed at event 250 (`SIGKILL`) and resumed produces a final summary equal to an uninterrupted run's summary field by field, except `stats.resumes`. Live: one testnet world killed and resumed with an open position, reconciled, logged.

## 7. Phase 4 completion condition

1. `uv run ruff check . && uv run pytest` exits 0 on main after V, C, R, T, H (and Q if approved) are merged.
2. `factorylab versions runs/testnet-fourth.jsonl runs/testnet-fourth.jsonl.key` prints versions and flags `learning_death` or `stable_failure` for run 4's noop attractor.
3. A scripted world killed and resumed matches its uninterrupted twin.
4. A Hyperliquid testnet world executes one withdrawal and one deposit through the factory's own tool call, confirmed on chain.
5. A live run after Q (if approved) shows evaluators' consequence standing diverging between judges who bless `noop` and judges who do not.
6. Bewilderment (v0.5 §9 condition 6) re-checked on that run and written up honestly.
