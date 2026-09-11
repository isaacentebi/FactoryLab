# Factory Lab — build spec v0.6, phase 3

11 September 2026 · Extends v0.5. Phase 2 is complete on main and three live testnet worlds have run; the third changed its own parts on model proposals (see `docs/build-log.md`). Phase 3 gives the population a fuller world and the charter.

Everything in v0.4 sections 1, 2, 7 and v0.5 section 0 still binds.

## 0. Decisions

| Topic | Decision |
|---|---|
| Memory | An assembly may keep private memory across its own invocations (its last few returns and the verdicts they earned). This is local state, private to that assembly, per v0.4 §1.6. |
| Price history | The world block carries a short recent-mid window per coin and an account statement. World facts, public. |
| Tool calling | A return may ask for tool calls; the runtime executes them (metered) and re-invokes the same assembly once with the results. One round per decision in this phase. |
| Venue tools | Reads (candles, order book, funding history, open orders, positions) and writes (market, limit, reduce-only, cancel, close, leverage) are registered `tool` contracts over the exchange. |
| Population tools | Any assembly may register a tool: sandboxed Python with a declared args schema, no network, bounded time and output, priced per call. Paid from the novelty reserve at registration like any proposal. |
| Web search | OpenRouter's web plugin is a purchasable: a model tier may declare a web variant priced per request (Exa fast: $0.007/request); the runtime registers `<model>:online` as a separate model capability. Assemblies on it get search results in their context. |
| Charter | Amendments to metric cards are population proposals. A committee of five seats drawn by lot across roles votes; three of five passes; the new edition activates at the next reserve-window boundary; every edition is preserved; evaluators judge against the current edition. Norms stay read-only. |
| Adversarial minority | Deferred to phase 3b. |
| Venue account | The experimenter funds a Hyperliquid testnet account and sets `HL_PRIVATE_KEY`; until then orders are rejected and P&L is zero. |

## 1. World block additions (runtime)

`world` in every request gains: `recent_mids: {coin: [{t_s, mid}, …]}` (last 20 ticks); `account: {equity_usd, cash_usd, positions, open_orders, realized_pnl_usd_to_date, fees_usd_to_date, funding_usd_to_date}`; `tools: [{id, description, args_schema, price_micro_per_call}]` (every tool this assembly may call); `charter_edition`. Each assembly's request additionally carries `your_recent_returns: [{outputs, verdict}]` (its own last 3, private).

## 2. Venue tools — `factorylab/world/venue_tools.py`

```python
class VenueTools:
    def __init__(self, exchange: Exchange, *, coins: tuple[str, ...])
    def contracts(self) -> list[ToolSpec]                      # one per tool below, with args_schema
    def call(self, tool_id: str, args: dict) -> dict            # validated; never raises on venue rejection
```

Tools (ids): `venue.candles(coin, interval ∈ {1m,5m,15m,1h}, n ≤ 200)`, `venue.order_book(coin, depth ≤ 20)`, `venue.funding_history(coin, n ≤ 100)`, `venue.open_orders()`, `venue.positions()`, `venue.place_market(coin, side, size, reduce_only=false)`, `venue.place_limit(coin, side, size, price, reduce_only=false)`, `venue.cancel(coin, order_id)`, `venue.close(coin, size=null)`, `venue.set_leverage(coin, leverage ≤ manifest max)`.

- `ToolSpec(id, description, args_schema: dict, price_micro_per_call: int, kind="venue")`.
- `FakeExchange` gains what it lacks: candles built from its own mid history, a synthetic order book (mid ± spread, sizes decaying), leverage per coin used by the margin check, `close`. Existing behaviour and tests unchanged.
- `HyperliquidExchange` implements reads through `Info` (`candles_snapshot`, `l2_snapshot`, `funding_history`, `open_orders`, `user_state`) and writes through `Exchange` (`market_open`, `order` with `{"limit": {"tif": "Gtc"}}`, `cancel`, `market_close`, `update_leverage`). Without a key every write returns `{"status": "rejected", "error": "no signing key"}`.
- Reads cost 0 per call; writes cost 0 per call (the venue's own fees are charged through fills). Every call is logged with its args.

## 3. Tool calling — cortex and runtime

A return may include `tool_calls: [{"tool": id, "args": {…}}]` (max 4). The runtime validates each against the tool's args schema and the assembly's allowed set, executes them in order through metering (reserve `price_micro_per_call`, commit), and re-invokes the same assembly with the original request plus `tool_results: [{tool, args, result}]` appended to inputs. The second return is the decision's final return. If the second return also asks for tools, it is treated as final with `tool_calls` ignored and a note in the ledger. Cost of both invocations is the decision's cost.

## 4. Population tools — `factorylab/cortex/tools.py`

Proposal kind `tool`: `{"kind":"tool","id":slug,"description":str,"args_schema":{…},"code":python source ≤ 8000 chars,"timeout_s":≤5}`. The code is a script that reads a JSON object from stdin and prints a JSON object to stdout. Registered as a `tool` contract with provenance; executed through `cortex.sandbox.run_python` (isolated, no network, CPU-limited); result truncated to 8 KB; failures return `{"error": …}`. Price per call: `manifest.tools.population_tool_micro_per_call` (seed 50). A tool may be called by any assembly that names it in its `tool_ids` or by its proposer.

## 5. Web search purchasable

Manifest tier option: `web = { engine = "exa", mode = "fast", max_results = 5, usd_per_request = "0.007" }`. The runtime registers a second model capability `<id>:online` whose contract carries `per_request_micro`. `OpenRouterProvider` sends `plugins: [{"id":"web","engine":…,"max_results":…}]` when the model id ends with `:online` (strip the suffix for the `model` field? No: OpenRouter accepts the `:online` slug directly; send it as-is and the plugin config only when extra options are set). `MeteredModel.ceiling` adds `per_request_micro`; reported cost already includes search.

## 6. Charter committee — `factorylab/charter/`

```python
@dataclass(frozen=True)
class Amendment:
    id: str; proposer_handle: str; edition_base: int
    add: tuple[MetricCard, ...]; replace: tuple[MetricCard, ...]; remove: tuple[str, ...]
    predicted_effect: str

class CharterBook:
    def __init__(self, ledger, seed_charter: Charter)
    def current(self) -> Charter
    def propose(self, amendment: Amendment) -> None            # validated against current edition; ledger entry
    def pending(self) -> list[Amendment]
    def seat(self, amendment_id: str, eligible: dict[str, str], rng) -> Committee   # eligible: assembly_id -> role
    def vote(self, committee: Committee, alias: str, vote: bool, reason: str) -> None
    def tally(self, committee: Committee) -> str | None       # "passed" | "failed" | None while incomplete
    def activate_due(self, now_ns: int) -> Charter | None     # applies passed amendments at a window boundary
```

- `Committee`: five seats (fewer only if fewer than five eligible lineages exist; then all), drawn without replacement with at least one producer, one evaluator and one meta when available; each seat gets a round-specific alias; votes are recorded under the alias with the assembly id held in the sealed ledger only.
- Three of five passes (majority of seats when fewer). Ties fail.
- Editions are immutable; `Charter.edition` increments; `render()` of any past edition remains available.
- Norms cannot be amended; an amendment that touches norms is rejected at `propose`.
- The runtime (not this package) invokes each seat's assembly with a `Vote on an amendment` request carrying the current charter, the amendment, and the proposer's predicted effect, and returns `{"vote": true|false, "reason": str}`; malformed votes count as abstentions (not yes).

## 7. Completion condition

1. `uv run pytest` exits 0.
2. `factorylab run --world scripted --events 500 --seed 1` with the scripted provider extended to exercise the new paths shows: at least 10 venue tool calls with results returned to the calling assembly; one population tool registered and called; one web-variant model registered as a purchasable (fake provider); one amendment proposed, seated, voted and activated, with evaluators receiving edition 2 afterwards; conservation and verify true.
3. `factorylab run --world testnet --events 30 --seed 4 --tick-interval 10s --kill-at-end` with real models shows tool calls in the diary and, if `HL_PRIVATE_KEY` is set, at least one fill.
4. Bewilderment check as in v0.5 condition 6, plus: at least one tool call or amendment proposal that came from a model's return.

Out of scope: adversarial minority, λ controller, sortition of humans, real money.
