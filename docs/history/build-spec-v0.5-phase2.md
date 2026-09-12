# Factory Lab — build spec v0.5, phase 2

11 September 2026 · Extends v0.4. Phase 1 (v0.4 section 6) is complete on main. This document adds evaluators, consequence settlement, population registration, the OpenRouter provider, the operational swap-regret adapter, a minimal charter, and the live testnet path.

Everything in v0.4 sections 1, 2 and 7 still binds. Where this document names a kernel change, it is deliberate and small.

## 0. Decisions since v0.4

**Governing test.** The essay's own criterion of success is bewilderment: if the architect is comfortable with what the factory becomes, the architect has failed. Every phase from here carries a literal version of that check (section 9, condition 6). Scaffolding that scripts the factory's behaviour (the scripted provider, seed prompts, seed routers) exists to test physics and must be replaceable by the population; none of it is the experiment.

| Topic | Decision |
|---|---|
| Drip | Off by default. One starting balance. The manifest may still declare a drip; the seed worlds do not. |
| Model roster | Non-Anthropic, through OpenRouter: GLM 5.3 Flash, DeepSeek V4.1 Flash, GPT-5.6 Luna, Muse Spark 1.3. Seeds run on the two flash tiers. |
| Catalogue agency | The whole OpenRouter catalogue is purchasable. The population may register any listed model as a capability at its listed price, paying a novelty trial from the reserve. |
| Metering truth | Model calls are charged at the cost OpenRouter reports for that request (`usage.cost`), rounded up to whole micro-USD. The price table is used for the reservation ceiling and as a fallback only. |
| Two pots | Real money lives in two places (venue account, OpenRouter credits). The kernel wallet is the single truth over both. A rebalancing rule declared at launch and executed mechanically by the experimenter moves realised venue profits to OpenRouter; it is physics if fixed before launch. Phase 2 implements the reconciliation, not the transfer. |
| Ledger key | Persisted to a 0600 file next to the ledger so a long-running world survives a process restart. Sealing is against the population and the wake view; the experimenter's non-intervention covenant now includes not reading that file. |
| Charter | Minimal and read-only in this phase: four norms and a fixed set of metric cards, rendered into evaluator prompts. Amendments are phase 3. |

## 1. New kernel event kinds

Add to `EventKind`: `ProducerReturn`, `Verdict` (exists), `ForecastSettled`, `Registered`, `RouterReplaced`, `Reconciled`. No other kernel change.

## 2. Settlement package — `factorylab/settlement/`

### 2.1 Event vocabulary (`vocabulary.py`)

A predicate is a launch-declared, trusted, observable fact. Seed vocabulary:

| id | params | y = 1 when |
|---|---|---|
| `wallet_up` | `horizon_events` | wallet balance at settlement > balance at forecast |
| `fill_within` | `horizon_events` | at least one fill event occurred in the window |
| `rejected_within` | `horizon_events` | at least one `OrderRejected` in the window |
| `liquidated_within` | `horizon_events` | at least one fill with `liquidation: true` in the window |
| `drawdown_exceeds` | `fraction`, `horizon_events` | min wallet balance in window < (1 − fraction) × balance at forecast |

`Observer` computes `y` from a `WindowFacts` record the runtime assembles from kernel-visible facts (wallet series and events between two event indices). Predicates never read the charter or any verdict.

### 2.2 Forecasts (`forecast.py`)

```python
@dataclass(frozen=True)
class Forecast:
    handle: str            # the decision handle this forecast is addressed to (its own decision)
    evaluator_id: str
    about_handle: str      # the producer decision being judged
    predicate_id: str
    params: dict
    q: float               # in [0, 1]
    made_at_event: int
    due_at_event: int
    seal: str              # sha256 of the canonical forecast, appended to the ledger at creation
```

`ForecastBook.seal(forecast)` appends `{"kind": "forecast.seal", ...}` to the ledger before any outcome can be known. `ForecastBook.due(n)` yields forecasts whose `due_at_event <= n`.

### 2.3 Scoring and standing (`scoring.py`, `standing.py`)

- `brier(q, y) = 1 − (q − y)²`, bounded in [0, 1].
- `PrevalenceBaseline` per predicate keeps the running base rate and the score a constant-prevalence forecaster would have earned; `skill = mean(brier) − mean(baseline_brier)`.
- `ConsequenceStanding` per evaluator id: `n`, `mean_brier`, `skill`, `coverage` (forecasts settled ÷ forecasts requested). Exposes `weight()` in [0, 1] = clipped `0.5 + skill`, defaulting to 0.5 with no history. Coverage below `min_coverage` (manifest) caps weight at 0.5: an evaluator cannot gain standing by forecasting only easy cases.

### 2.4 Settler (`settle.py`)

`Settler.settle_due(n, facts_for(window)) -> list[Settled]`: for each due forecast, compute `y`, `brier`, update baseline and standing, call `queue.settle(forecast.handle, channel="consequence", score=brier, status=SETTLED, definition_version="brier-v1", sampling_ref=None)`, and return records the runtime publishes as `ForecastSettled` events. Missing facts (world ended early) settle as `CENSORED` with no score.

## 3. Evaluators and meta-evaluators — cortex and runtime

### 3.1 Assembly kinds

`AssemblySpec.role` ∈ {`producer`, `evaluator`, `meta`}. Evaluators accept `ProducerReturn`; metas accept `Verdict`.

### 3.2 Evaluator request and return

Inputs: the producer request's description and inputs, its outputs, its cost, the charter rendering (norms and metric cards), and the seed predicate vocabulary. The producer's identity is not included.

Outcome schema:

```json
{"verdict": 0.0-1.0, "rationale": "string",
 "forecasts": [{"predicate": "wallet_up", "params": {"horizon_events": 10}, "q": 0.6}]}
```

At most `max_forecasts_per_verdict` (manifest, seed 2). Each forecast becomes its own decision: actor = the evaluator assembly id, propensity = one-hot on the bucket `round(q, 1)` (so the record replays), channel `consequence`, opened by the runtime, then sealed in the ForecastBook.

The evaluator router's own decision (which evaluator to wake for this ProducerReturn) uses channel `conformity`.

### 3.3 Verdict delivery

A verdict is published as a `Verdict` event carrying `about_handle`, `evaluator_handle`, `verdict`. The producer decision it judges is settled on its channel with the verdict as the score when that channel is `verdict` (producers' router decisions move from `fast` to `verdict` in this phase; `fast` remains for `NOOP` and for returns no evaluator judged within `verdict_timeout_events`, scored 0).

### 3.4 Meta-evaluation

A meta assembly receives a Verdict event's payload (the verdict, its rationale, the producer output, the charter) and returns `{"conformity": 0.0-1.0, "rationale": ...}`. The runtime settles the evaluator router decision on channel `conformity` with that score. Meta decisions themselves settle `fast` (well-formed = 1). Depth stops there in this phase; the boundary is reported in the run summary.

### 3.5 Protected consequence share in evaluator selection

When routing a `ProducerReturn`, the executed distribution is `(1 − s) · router_probs + s · standing_probs`, where `standing_probs` is proportional to `ConsequenceStanding.weight()` over the feasible evaluators, and `s = manifest.evaluation.consequence_share` (seed 0.3). The logged propensity is the mixed distribution. Charter-conformity scores cannot change `s`.

### 3.6 Population balance

Seed manifests carry more evaluator than producer capacity: 2 producers, 4 evaluators, 2 metas. Two of the evaluators are seeded on a different model family from the producers.

## 4. Population registration — `cortex/registration.py` and runtime

Any assembly's return may include a `register` list. Each proposal is validated, paid from the novelty reserve (`trial_amount` from the manifest), registered with provenance = the proposing decision handle, and announced as a `Registered` event. Rejections are logged with a deterministic reason. Proposal kinds:

| kind | fields | effect |
|---|---|---|
| `model` | `openrouter_id` | looks up the catalogue; registers a `model` contract at the listed price; the model becomes usable by new assemblies |
| `assembly` | `id`, `role`, `model_id`, `system_prompt`, `accepts`, `max_tokens`, `effort` | registers an `assembly` contract and instantiates it; every router for the accepted kinds opens a new comparator epoch (below) |
| `router` | `event_kind`, `learner` ∈ {`exp3`, `blum_mansour`}, `gamma` | replaces the router for that event kind; announced as `RouterReplaced` |

**Comparator epochs.** Learners have fixed universes. When an action is added, the runtime builds a new learner over the expanded universe, carries over the log-weights of existing actions, starts the new action at the mean carried weight, records `{"kind": "epoch", ...}` in the ledger, and continues. This is the recorded transfer rule the v0.3 plan asks for; no theorem is claimed across the epoch.

Registrations cannot touch kernel physics, prices, the consequence share, the vocabulary, or the charter.

## 5. OpenRouter provider — `world/openrouter.py`

```python
class OpenRouterProvider:
    name = "openrouter"
    def __init__(self, *, key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1", transport=None, app_name="factorylab")
    def complete(self, req: ModelRequest) -> ModelResponse     # POST /chat/completions
    def catalogue(self) -> list[CatalogueEntry]                 # GET /models: id, prompt and completion USD-per-token as str
    def balance_micro(self) -> int | None                       # GET /key: limit_remaining, else None
```

- `ModelResponse` gains `cost_micro: int | None`. OpenRouter's `usage.cost` (USD float) is converted through `Decimal(str(cost))` and rounded **up** to micro-USD. `MeteredModel` uses `cost_micro` when present, otherwise the price table.
- Reasoning effort maps to OpenRouter's `reasoning: {"effort": ...}` field when the manifest tier says the model supports it.
- `transport` is an injectable callable `(method, path, json) -> dict` so tests run without network; a `network`-marked test hits the real API when `OPENROUTER_API_KEY` is set.
- `CatalogueEntry.price()` returns a `TokenPrice`; catalogue prices are USD per token as decimal strings and may not divide into whole micro-USD per token. `TokenPrice.from_per_token(str, str)` stores exact `Fraction` micro-USD and `cost()` rounds up. (Extend `world/models.py`.)
- Never log the key. Never retry a request that may have been billed.

## 6. Operational swap-regret — `learners/delayed.py`

`SnapshotLearner(inner)` wraps any learner for delayed feedback: `distribution()` records a snapshot keyed by a caller-supplied handle (for Blum–Mansour: the Q rows and solved p; for EXP3: nothing beyond the propensity); `update_for(handle, feedback)` applies the update using that snapshot, so out-of-order and delayed returns train the round they belong to. Blum–Mansour's pending-round restriction is lifted through this wrapper only; the inner learner is never called with a stale round. Reference test: a delayed-feedback replay of the investment-trap game reaches the same regret numbers as the synchronous run when feedback arrives in shuffled order.

## 7. Charter (minimal) — `charter/`

`Charter(norms: tuple[str, ...], cards: tuple[MetricCard, ...])`, immutable in this phase. Seed norms: truthful commitments, care with scarce resources, useful inquiry, the capacity to revise inadequate practices. Seed cards: `cost_per_return`, `well_formed_rate`, `forecast_skill`, each with units, window and acceptable region as text. `Charter.render()` is what evaluators see. No λ controller yet.

## 8. Live testnet path — runtime

- `LiveWorld` event source: wall-clock ticks at `tick_interval`; polls Hyperliquid testnet mids and funding each tick; polls fills when a key is present; emits `Reconciled` every `reconcile_every` ticks with wallet balance, OpenRouter `limit_remaining` and venue account value, logging any discrepancy (informational; the wallet stays authoritative).
- `Ledger(key_path=...)`: key persisted 0600; on construction with an existing ledger file and key file, the ledger resumes (verifies the chain first).
- `factorylab run --world testnet --duration 30m` runs the live loop; `--events` still bounds it.
- The testnet manifest names the four OpenRouter tiers with their current prices, seeds on the flash tiers, no drip, `initial_balance_usd = "100"` (fake USD; the wallet is not funded in this world).

## 9. Phase 2 completion condition

1. `uv run pytest` exits 0.
2. `factorylab run --world scripted --events 400 --seed 1` (scripted world now seeds evaluators and metas on the scripted provider) shows: verdict-settled producer decisions; at least 20 sealed forecasts settled with Brier scores; consequence standing with nonzero coverage for at least two evaluators; at least one `Registered` event from a scripted proposal (a new assembly) followed by an epoch record and an invocation of the new assembly; conservation and verify true.
3. Delayed-feedback replay of the reference game matches the synchronous regret numbers (seed 0, T = 5000) within 1e-9.
4. With `OPENROUTER_API_KEY` set, `factorylab run --world testnet --events 30 --seed 3` completes 30 live events with at least one real model invocation whose ledger cost equals OpenRouter's reported cost rounded up, and one `Reconciled` event.
5. `factorylab run --world scripted-crash --events 600 --seed 2` still terminates by death with the seal released (shocks moved to ticks 42–50 because the phase-2 population spends the $5 wallet down long before tick 500).
6. **Bewilderment check.** In a testnet run of at least 200 live events with real models, the ledger contains at least one `Registered` or `RouterReplaced` event whose proposal came from a model's return, not from a script, and at least one router's most-frequent action differs from what the seed manifest would predict. This is the minimum evidence that the world did something we did not write; it is not proof of Class 3 behaviour, and a run that passes conditions 1 through 5 but fails this one is reported as a failure of the experiment, not a success of the build.

Out of scope for phase 2: charter amendments, sortition, λ controller, population-written tools, real money, the venue-to-OpenRouter transfer.
