# Factory Lab — build spec v0.4

11 September 2026 · Supersedes the launch-world assumptions in `outputs/project-plan.md` v0.3 where they conflict. Everything else in v0.3 still binds.

This document records the decisions made after the v0.3 fidelity review and turns them into interfaces. It exists so that implementers (Codex, subagents) can build without access to the conversation, and so that every build can be checked against a written intent.

## 0. What changed since v0.3, and why

v0.3 sandboxed consequences: paper P&L, internal credits and real spend were three separate units, and the real budget could only fund a pre-approved compute pool. That keeps the factory alive regardless of what it does, which by the article's own definitions makes survival exogenous and the relation Class 2. v0.4 fixes this with one wallet.

| Decision | v0.3 | v0.4 |
|---|---|---|
| Money | three units, isolated | one wallet, real USD, every cost and every return settles into it |
| Venue | paper trading, historical replay | Hyperliquid (testnet for substrate and rehearsal, mainnet for the funded world) |
| Compute | pre-approved pool, allocated by policy | bought from the wallet at vendor price per invocation |
| Purchases | none | a registry of purchasable capabilities (model tiers, data, compute) at real prices; the factory decides |
| Survival | funding schedule set by experimenter | wallet at zero is death; a pre-committed drip is the only inflow besides P&L |
| Heartbeat | scheduler | the world's events (ticks, funding, drip, verdicts) drive a deterministic nervous system that is always awake; LLM assemblies are invoked selectively and cost money |
| Agents | seeded population with roles | no persistent agents; assemblies are ephemeral, invoked per request; learners, registry, queue and charter persist |
| Experimenter access | full ledger, live | wallet, behavioral distributions and outcomes live; item-level ledger sealed until death |
| Intervention | not specified | a written covenant: no changes between launch and death; any change is a new world |

Model choice is deliberately open. Model tiers are registered capabilities with prices, from more than one provider family. Which tiers are registered is a launch decision; which tier gets invoked is a factory decision.

## 1. Non-negotiables (kernel physics)

These are hard casts. They are enforced by code, never explained to the population as rules. A change to any of them is a new world.

1. **Conservation.** Wallet balance changes only through metered debits, exchange settlements, and the declared drip. No path creates money.
2. **Metering before return.** No model call, order, or purchase returns a result until its cost has been debited. If the wallet cannot cover a reservation, the action is infeasible and logged as such.
3. **Death at zero.** Balance at or below zero terminates the world. Termination is final. The item-level ledger key is released at termination.
4. **Addressability.** Every sampled action has a persistent handle, a logged propensity that matches the executed distribution, and a declared scoring channel. Late rewards settle to the original handle even if the choosing learner has been retired.
5. **Novelty reserve.** A declared share of each window's spend is usable only for actions with no inherited performance record. Unused reserve expires at window end. Incumbents cannot consume it. The charter cannot abolish it.
6. **Information boundaries.** Schematics, contracts, prices and the charter are public. A learner's state, history and full action distribution are private. The selected action's propensity travels only along the reward route. No component holds the full topology.
7. **Timing registration.** Reports moving upward to a loop that governs lower loops are buffered into distributions with a minimum separation (seed 3:1) and jitter. Downward commands, cancellation and hard stop are prompt.
8. **Author neutrality.** Requests carry no author identity in the payload. Lineage lives in the sealed ledger.
9. **Sealed ledger.** Item-level records are append-only, hash-chained, and encrypted with a key the kernel does not expose until termination. Aggregate views are derived by kernel code, not by reading items.
10. **Non-intervention.** After launch, the experimenter has two levers: kill, and nothing. The drip is declared at launch and cannot be changed.

## 2. Units and numbers

- Money is integer micro-USD (`int`). $1.00 = 1_000_000. No floats in the wallet.
- Model prices are stored per token in micro-USD (Opus 5: input 5, output 25; Sonnet 5: 2 and 10; Haiku 4.5: 1 and 5). Provider families other than Anthropic register their own table.
- Exchange amounts follow the venue's decimal conventions and are converted to micro-USD at the venue's reported mark for wallet accounting.
- Time is UTC, integer nanoseconds since epoch in the ledger; wall-clock helpers convert.
- Probabilities are floats in propensity records; the executed sample must be drawn from the logged distribution.

## 3. Package layout

```
factorylab/
  kernel/
    money.py        Money type, conversions
    wallet.py       Wallet, reservations, drip schedule, death
    ledger.py       append-only hash chain, sealed item store, aggregate views
    registry.py     capability contracts, versions, prices, purchasables
    queue.py        DecisionQueue: handles, propensity, channels, delayed settlement
    events.py       event types and the ordered in-process bus
    reserve.py      novelty reserve per window
    timing.py       timing registry, upward buffering, jitter
    termination.py  termination conditions, kill switch
  learners/
    base.py         Learner protocol
    hedge.py        multiplicative weights (full information)
    exp3.py         EXP3 (bandit)
    blum_mansour.py swap-regret reduction (full information and bandit)
    router.py       event -> action selection over registered assemblies + NOOP
    feasibility.py  filter with logged exclusions
    reference_games.py finite games for external vs swap regret checks
  world/
    exchange.py     Exchange protocol, FakeExchange, HyperliquidExchange
    clock.py        clock and drip event source
    models.py       ModelProvider protocol, FakeModel, AnthropicProvider, price tables
    metering.py     debit-before-return wrapper for any priced capability
  cortex/
    request.py      Request and Return dataclasses
    assembly.py     Assembly = model capability + prompt + tools + memory policy
    sandbox.py      subprocess sandbox for population-written code
  settlement/       (phase 2) forecasts, event vocabulary, scoring, standing
  charter/          (phase 2) metric cards, controller, amendments, sortition
  runtime/
    loop.py         event loop: world -> nervous system -> cortex -> settlement
    worlds.py       world definitions (scripted, testnet, funded)
    cli.py          factorylab run / status / kill
tests/
  kernel/ learners/ world/ cortex/ runtime/
```

## 4. Interfaces (phase 1)

### 4.1 Money and wallet

```python
Money = int  # micro-USD

class Wallet:
    balance: Money
    def reserve(self, amount: Money, handle: str, reason: str) -> Reservation  # raises Infeasible
    def commit(self, reservation: Reservation, actual: Money) -> None           # actual <= reserved; remainder released
    def release(self, reservation: Reservation) -> None
    def settle(self, delta: Money, handle: str, reason: str) -> None            # exchange P&L, funding; may be negative
    def drip(self, now_ns: int) -> Money                                        # applies any due scheduled deposits, idempotent
    @property
    def dead(self) -> bool                                                      # balance <= 0
```

- `DripSchedule(amount: Money, period_ns: int, start_ns: int, end_ns: int)` is set at construction and immutable.
- Every mutation appends a ledger entry `{kind, amount, balance_after, handle, reason, ts}`.
- Invariant tested: `balance == initial + sum(drips) + sum(settlements) - sum(commits)`.

### 4.2 Ledger

```python
class Ledger:
    def append(self, entry: dict) -> int            # returns seq; entry gets ts, seq, prev_hash, hash
    def aggregate(self, view: str, **params) -> dict  # kernel-defined views only
    def seal_key_released(self) -> bool
```

- Item-level entries are encrypted at rest (symmetric key generated at world creation, held in the kernel's key store, exposed only by `Termination`).
- Aggregate views for phase 1: `wallet_series`, `spend_by_capability`, `invocations_by_assembly`, `action_frequencies`, `settlement_latency`.
- Hash chain verified by `Ledger.verify() -> bool`.

### 4.3 Registry

```python
@dataclass(frozen=True)
class Contract:
    id: str; version: int; kind: Literal["model","tool","assembly","router","purchase","exchange"]
    description: str
    input_schema: dict; output_schema: dict
    price: PriceSpec               # per-unit prices in micro-USD; units named
    permissions: frozenset[str]
    resource_bounds: ResourceBounds
    provenance: str                # "seed" or the handle of the decision that registered it

class Registry:
    def register(self, contract: Contract, by_handle: str | None) -> None
    def get(self, id: str, version: int | None = None) -> Contract
    def available(self, kind: str, event_kind: str | None = None) -> list[Contract]
    def purchasables(self) -> list[Contract]
```

- Registration by a population decision goes through the novelty reserve.
- Contracts are immutable; a change is a new version.

### 4.4 Decision queue

```python
@dataclass
class PropensityRecord:
    action_ids: tuple[str, ...]; probs: tuple[float, ...]; chosen: str; rng_seed: int; learner_id: str; learner_state_hash: str

class DecisionQueue:
    def open(self, *, actor: str, event_id: str, propensity: PropensityRecord, channel: str,
             deadline_ns: int, parent_handle: str | None, cost_ceiling: Money) -> str   # returns handle
    def settle(self, handle: str, *, channel: str, score: float, status: SettleStatus, definition_version: str, sampling_ref: str | None) -> None
    def outstanding(self, actor: str | None = None) -> list[Decision]
    def expire(self, now_ns: int) -> list[str]   # marks timed_out; operational penalty channel, no manufactured outcome
```

- `SettleStatus = pending | settled | censored | timed_out | inapplicable`.
- A settlement for a retired actor is delivered to its declared successor if a compatibility map exists, otherwise retained as historical evidence. Never dropped.
- The learning return is thin: handle, channel, scalar score, definition version, status, sampling ref. Nothing else.

### 4.5 Events and bus

Event kinds for phase 1: `Launch`, `Tick`, `Drip`, `MarketMid`, `Funding`, `Fill`, `OrderRejected`, `Verdict`, `WalletChanged`, `ReserveWindowOpened`, `Terminated`.

```python
class Bus:
    def publish(self, event: Event) -> None       # appends to ledger, then delivers in order
    def subscribe(self, kind: str, handler) -> None
```

Every event has `id`, `kind`, `ts_ns`, `payload`, `source` (world adapter or kernel). The bus is single-threaded and deterministic given the same event sequence.

### 4.6 Novelty reserve

```python
class NoveltyReserve:
    def __init__(self, share: float, window_ns: int)
    def open_window(self, now_ns: int, window_spend_budget: Money) -> None
    def reserve_for(self, contract: Contract, amount: Money) -> Reservation  # only if contract has no settled history
    def remaining(self) -> Money
```

### 4.7 Learners

```python
class Learner(Protocol):
    id: str
    def distribution(self, feasible: Sequence[str]) -> dict[str, float]
    def update(self, feedback: Feedback) -> None          # full-info: loss vector; bandit: (action, reward, propensity)
    def state(self) -> bytes                              # private; hashed for propensity records
```

- `Hedge` and `EXP3` are the frontier references. `BlumMansour` composes N base learners into a swap-regret learner: rows of Q from base learners, solve `p = pQ`, feed learner i the loss weighted by `p_i` in the full-information case; use the paper's partial-information construction for the bandit case.
- `reference_games.py` must include at least one finite game where Hedge accumulates swap regret and BlumMansour does not, with a test that checks both numerically over a fixed horizon and seed.
- Feasibility filtering logs every exclusion with a deterministic reason. The logged propensity is over the feasible set actually sampled from; no post-hoc clipping.

### 4.8 Router

A router is a learner whose action set, for a given event kind, is the registered assemblies that accept that event kind plus `NOOP`. Routers are registered contracts of kind `router`; more than one router may exist per event kind, and a router assignment is itself a routable decision in later phases. Phase 1 ships one seed router per event kind.

### 4.9 World adapters

```python
class Exchange(Protocol):
    def mids(self) -> dict[str, Decimal]
    def funding(self) -> list[FundingEvent]
    def account(self) -> AccountState
    def place(self, order: Order) -> OrderResult
    def cancel(self, order_id: str) -> None
    def fills(self, since_ns: int) -> list[Fill]

class ModelProvider(Protocol):
    def complete(self, req: ModelRequest) -> ModelResponse   # response carries usage (input_tokens, output_tokens)
```

- `FakeExchange` is deterministic from a seed and a scripted price path; it simulates fills at mid with a configurable spread and hourly funding.
- `HyperliquidExchange` wraps `hyperliquid-python-sdk`, testnet by default, mainnet only when the world manifest says so. Keys come from environment or a key file path in the manifest, never from code.
- `FakeModel` returns scripted responses with declared token usage.
- `AnthropicProvider` uses the official SDK with adaptive thinking and the server-side fallback beta. Other families are added as separate providers behind the same protocol.
- `metering.py` wraps any priced capability: reserve at the cost ceiling, execute, commit actual, return. Failure releases the reservation and logs it.

### 4.10 Requests and assemblies

```python
@dataclass(frozen=True)
class Request:
    handle: str; description: str; inputs: dict; capability_versions: dict[str, int]
    outcome_schema: dict; deadline_ns: int; cost_ceiling: Money
    parent_handle: str | None; completion_criterion: str; scoring_channel: str; resource_liability: str

@dataclass(frozen=True)
class Return:
    handle: str; outputs: dict; cost: Money; status: str
```

An assembly is a contract of kind `assembly` binding a model capability id, a prompt template, a tool list (registered tool ids), and a memory policy. Invoking it builds a `ModelRequest`, runs it through metering, and produces a `Return`. Assemblies may emit further `Request`s (child decisions) through the same router path.

### 4.11 Runtime loop

```
for event in world.events():
    bus.publish(event)                      # ledger first
    wallet.drip(event.ts_ns)
    if wallet.dead: terminate(); break
    for router in routers_for(event.kind):
        feasible = feasibility.filter(router.actions(event), wallet, registry)
        dist = router.distribution(feasible)
        action = sample(dist, rng)
        handle = queue.open(...propensity...)
        if action != NOOP:
            ret = metering.run(assembly=action, request=build_request(event, handle))
            settle_fast_channels(handle, ret)  # e.g. cost channel closes now
    queue.expire(event.ts_ns)
    deliver_due_settlements()               # slow channels: fills, funding, verdicts address original handles
```

## 5. Worlds

| World | Exchange | Models | Wallet | Purpose |
|---|---|---|---|---|
| `scripted` | FakeExchange | FakeModel | fake USD | substrate tests, deterministic, no network |
| `testnet` | Hyperliquid testnet | real providers, real prices, debited from a fake wallet | fake USD | rehearsal: the loop closes against a live venue |
| `funded` | Hyperliquid mainnet | real providers | real USD | the experiment |

A world is defined by a manifest (`worlds/<name>.toml`): initial balance, drip schedule, registered contracts and prices, novelty share and window, timing ratios, termination conditions, seed. The manifest is hashed into the ledger's genesis entry.

## 6. Phase 1 completion condition

Phase 1 is the substrate on the `scripted` world plus a live read against testnet. It is complete when all of the following hold:

1. `uv run pytest` exits 0.
2. `uv run factorylab run --world scripted --events 200 --seed 1` runs to completion and its final status output shows: at least one non-NOOP invocation with a logged propensity; every invocation metered before return (ledger shows reserve→commit pairs); at least one delayed settlement delivered to a handle opened at least 10 events earlier; wallet balance equal to the conservation formula; `Ledger.verify()` true.
3. `uv run factorylab run --world scripted --events 2000 --seed 2 --initial-balance 5000000` terminates by death (balance ≤ 0) with a `Terminated` event and releases the seal key.
4. The reference-games test demonstrates Hedge accumulating positive swap regret and BlumMansour driving it toward zero on the same game and seed.
5. `uv run factorylab probe --world testnet` fetches live mids and funding from Hyperliquid testnet and prints them (network required; skipped in CI).

Out of scope for phase 1: settlement of forecasts, charter, sortition, λ controller, meta-evaluation buffering beyond the timing registry's data structures, real model calls in the loop, real money.

## 7. Conventions for implementers

- Python ≥ 3.12, `uv` for environments, `pytest` for tests, `ruff` for lint. No other tooling.
- Standard library first. Allowed third-party: `hyperliquid-python-sdk`, `anthropic`, `cryptography`, `tomli`/`tomllib`, `pytest`, `ruff`. Adding anything else requires a stated reason in the PR.
- No floats for money. No global mutable state. No background threads in phase 1.
- Every kernel invariant in section 1 has at least one test that tries to violate it and fails.
- Public functions have docstrings that state what they guarantee, not what they do.
- Nothing in the kernel imports from `cortex`, `world` or `runtime`.
- Do not describe the kernel's rules to the population in prompts. Physics is enforced, not announced.
