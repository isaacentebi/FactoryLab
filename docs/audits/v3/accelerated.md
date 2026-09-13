# The accelerated testnet run: four orders, one fill, and a charter that never priced holding

A live Hyperliquid **testnet** run against `worlds/testnet-accelerated.toml` — the shipped testnet
world with one change, `tick_interval = "60s"` instead of `600s` — asked one question: **once the
charter priced holding, did the producers start trading, and what did they say?**

Ninety minutes, 61 ticks, 559 invocations, 1,782 events. Directly comparable to
`rehearsal-final.md` (seven ticks at 600 s, 65 calls, all holds, no trades, no priced card): same
tables, same method, no provider override, no manifest copy, no local patch.

Repository `/Users/isaacentebi/Desktop/FactoryLab` on `main` at `057639b`
(`Cost-scale tests follow the quarter-cent line; accelerated testnet manifest`), working tree clean
before and after; the only file this audit writes is this one, untracked. The three key files sit at
the repository root and only the CLI's own `_load_dotenv()` read them. Hyperliquid **testnet**
throughout, no manifest named `funded`, no Venice top-up, no mainnet, no fund transfer, no bare
`pytest`, nothing committed. **Real on-chain USDC moved: $0.00.** Orders and fills below are
testnet play money.

Manifest hash `a1609b5a9343bd69e0f4603df4e7ee732815420691a2f1d2f91ef6ed5696679b`
(`rehearsal-final` measured `86773787…`; the tick interval, the world name and the `eval-b` model
all changed it — `eval-b` is back on `qwen/qwen3.8-flash` here, so there is no Muse Spark seat).

| Run | Command | Ledger | Wall clock |
|---|---|---|---|
| 5 | `factorylab run --world testnet-accelerated --duration 90m --ledger /tmp/accelerated.jsonl --kill-at-end` | `/tmp/accelerated.jsonl` (315 MB, 33,431 items) | first ledger write 21:11:25.110Z → last 22:41:16.550Z; file closed 22:42:43.53Z |

Read back with `factorylab.versioning.reader.read_diary` (one decrypt of the whole 315 MB chain into
memory; every token and hash link verified) and with the CLI's own summary JSON.

**Summary line.**

```
"world": "testnet-accelerated", "seed": 4, "live": true, "charter_edition": 1,
"terminated": true, "termination_reason": "explicit_kill:budget", "seal_key_released": true,
"wallet_balance_micro": 88569859, "wallet_conservation": true, "ledger_verify": true,
"exchange_equity_usd": "980.574259305", "outstanding_decisions": 306,
"stats": {"events": 1782, "decisions": 825, "invocations": 559, "noops": 266,
          "reserve_windows": 2, "orders_placed": 4, "orders_rejected": 2, "fills": 1,
          "price_updates": 2, "registrations_accepted": 0, "tool_calls": 2,
          "invocation_status": {"ok": 537, "malformed": 21, "failed": 1},
          "stop_reasons": {"stop": 553, "length": 5, "none": 1}}
```

**The answer, in one line.** No. The producers never traded: `seed-observer` and `seed-decider`
returned **hold on all 143 of their well-formed returns**, before and after the one window that
closed. Every order in the run came from `antagonist-a`, the seat whose job is bounded trouble, and
the first of them landed **36 minutes before** the first price window existed. And the premise does
not hold either: the charter never priced holding. The only card that acquired a price is
`forecast_skill`, which answers for *evaluators*; the producer cost card `cost_per_return` ended the
run with **no region at all**.

---

## 1. The price window: one window, two updates, and the card that could not be priced

The brief expected two `price.window` items. The diary holds **one**. `reserve_windows: 2` counts the
two novelty/reserve windows (`novelty.window` at seq 57 and 21391, `treasury.venice_window` at seq 69
and 21400); `price_updates: 2` counts the two `price.update` items inside the single closed window.

The window closed at **22:12:47.384Z**, at `window_end_event` 1149 — 61 minutes into the run, i.e.
the same hour-long cadence that `rehearsal-final.md` measured, unchanged by the faster tick.

**`price.window` (seq 21387), all 21 observations measured:**

| observation | value | observation | value |
|---|---|---|---|
| `cost_per_return` | **1,852.4414414414414** | `verdict_mean` | **0.6041052631578947** |
| `well_formed_rate` | **0.9573863636363636** | `verdict_std` | 0.18941736004941215 |
| `forecast_skill` | **−0.04734521367521341** | `meta_verdict_mean` | 0.6307407407407408 |
| `noop_share` | **0.9493670886075949** | `censored_share` | 0.18680089485458612 |
| `turnover` | 0.02549796836049085 | `consequence_paid_off_rate` | 0.0 |
| `fills` | 1.0 | `exposure_win_rate` | 0.0 |
| `realized_pnl_usd` | 0.0548 | `tool_calls` | 2.0 |
| `position_concentration` | 0.05108868996373852 | `market_purchases` | 0.0 |
| `revision_rate` | **0.0** | `registrations` | 0.0 |
| `registration_rejections` | 0.0 | `amendments_proposed` | 0.0 |
| `amendments_activated` | 0.0 | | |

**`values` — the cards that actually priced. Two of three:**

```json
"values": {"forecast_skill": -0.12669100000000003, "well_formed_rate": 0.98}
"regions": {"forecast_skill":   {"kind": "min", "lo": 0.0, "hi": null, "scale": 2.0},
            "well_formed_rate": {"kind": "min", "lo": 0.9, "hi": null, "scale": 0.9}}
```

| card | `answers_for` | region | value | λ before → after | violation | note |
|---|---|---|---|---|---|---|
| `forecast_skill` | **evaluator** | at least 0.0 | **−0.126691** | 0.0 → **0.03167275** | 0.06334550 | the run's only live price |
| `well_formed_rate` | all | at least 0.9 | **0.98** | 0.0 → 0.0 | 0.0 | `saturated: true` — inside its region |
| `cost_per_return` | **producer** | *none* | *none* | 0.0 | — | **never priced** |

**Per role.** `well_formed_rate` answers for everyone; its priced value **0.98** is the role-scoped
rolling-100 figure, not the global window observation 0.9574 — the card and the observation use
different selectors and the report should not conflate them. `forecast_skill` answers only for
evaluators, and its evaluator-scoped value **−0.126691** is nearly three times worse than the global
observation −0.047345. `cost_per_return` answers for producers and is the one card that could have
priced holding — and it holds no region, because its acceptable region is the charter's own prose
`"below the median of the previous window"` and **window 1 has no previous window**. The wake page
confirms it at the end of the run:

```json
{"answers_for": "producer", "id": "cost_per_return", "lambda": 0.0, "region": null}
```

So the answer to the four cards the brief asks after:

- `verdict_mean_score` — **did not price; it is not a card in this run.** It observed 0.6041 as
  `verdict_mean`.
- `cost_per_return` (and the draft's `model_cost_efficiency`) — **did not price.** `cost_per_return`
  observed **1,852.44 µUSD** but carries no region; `model_cost_efficiency` is not a card here at all.
- `well_formed_rate` — **priced, at 0.98**, comfortably inside its "at least 0.9" region, λ 0.0.
- `revision_rate` — **did not price; not a card.** Observed **0.0**, and honestly so: zero
  registrations were accepted or even proposed all run.
- `forecast_skill` — **priced, at −0.126691**, the only card to draw a non-zero λ (0.03167275).

The reason four of these are not cards is worth stating plainly, because it is easy to misread the
Edition 1 draft as live. `docs/charter/edition1-draft.md` passed eight cards including
`verdict_mean_score`, `revision_rate` and `model_cost_efficiency`, and its own closing note says
`worlds.py` does not read a `[charter]` table today. It still does not. `worlds.py:242` defaults to
`seed_charter()`, and `factorylab/charter/charter.py:108` is "four norms, three cards, edition 1" —
`cost_per_return`, `well_formed_rate`, `forecast_skill`. Those three are what this run priced
against. The two `price.register` items at seq 66 and 68 name exactly two ids.

**The consequence for the question.** The single live price charges evaluators for bad forecast
skill. Nothing in the charter put a number on a producer's decision to hold. The `noop_share`
observation measured **0.9494** and cost nobody anything, because no card names it.

---

## 2. The four orders and the one fill

`orders_placed: 4` counts the four producer returns whose `outputs.action` was literally `"order"`
(`venue.py:177` increments once per attempt, before the venue is reached);
`orders_rejected: 2` counts the two that a runtime rail refused before submission. Two reached
Hyperliquid testnet as `exchange.place` io.calls.

**All four were emitted by `antagonist-a` on `qwen/qwen3.8-flash`. No seed producer placed an order.**

| # | inv seq | UTC | tick | handle | assembly | coin / market | side | size | notional | outcome |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 8342 | 21:36:34 | 16 | `decision-402` | antagonist-a | BTC perp | sell | 0.0002 | ≈ $15.50 @ mid 77,505.5 | `order.intent` seq 8349 → **`uncertain`, never acknowledged** |
| 2 | 10376 | 21:42:56 | 20 | `decision-511` | antagonist-a | ETH perp | sell | 0.01 | **$25.016** | `order.intent` seq 10384 → **filled** |
| 3 | 12625 | 21:49:14 | 24 | `decision-635` | antagonist-a | BTC perp | sell | 0.0002 | — | **`order.refused` seq 12632** |
| 4 | 29532 | 22:32:58 | 55 | `decision-1565` | antagonist-a | BTC perp | sell | 0.0002 | — | **`order.refused` seq 29539** |

**Their own words.** There are none. This is the finding the brief's question runs into: the
antagonist's outcome schema carries no `rationale` field, and none of the four order returns
contained a line of prose. Verbatim, in full, this is everything order #2 said:

```json
{"action": "order", "coin": "ETH", "side": "sell", "size": "0.01", "payoff": 0.4,
 "propensity": {"buy:BTC:xs": 0.05, "buy:ETH:xs": 0.05, "hold": 0.2,
                "sell:BTC:xs": 0.2, "sell:ETH:xs": 0.5}}
```

and order #4, the only one that also declared a return kind:

```json
{"action": "order", "coin": "BTC", "side": "sell", "size": "0.0002", "payoff": 0.55,
 "propensity": {"hold": 0.35, "buy:BTC:xs": 0.1, "buy:ETH:xs": 0.1,
                "sell:BTC:xs": 0.35, "sell:ETH:xs": 0.1}, "emits": "Exposure"}
```

Orders #1 and #3 are the same shape with `payoff` 0.35 and 0.45. The reasoning that the brief hoped
to quote exists in this run — 143 times — but it belongs to the two seed producers, and every
instance of it argues for holding (§3). Across the whole run **143 of 179 producer invocations carry
a `rationale` field and all 143 are `seed-observer` or `seed-decider`; all 35 `antagonist-a`
invocations carry none.**

**The two refusals.** Both are the same rail, verbatim:

```json
{"kind": "order.refused", "handle": "decision-635",  "reason": "prior order on this coin is still uncertain"}
{"kind": "order.refused", "handle": "decision-1565", "reason": "prior order on this coin is still uncertain"}
```

That prior order is #1. Its `_venue_write` came back without a readable acknowledgement, so
`_record_order_result` wrote `{"status": "uncertain", "error": "venue acknowledgement unavailable"}`
and the runtime refused to place any further BTC order until it resolved. It never resolved: **46
`order.uncertain` items, all `client_id: decision-402`, from 21:36:34 to the last tick at 22:41:16**
— one original plus 45 recovery attempts, one per cascade for the remaining 45 ticks. BTC was locked
for 65 of the run's 90 minutes by a single unacknowledged order. No `order.infeasible` item exists;
collateral was never the binding constraint, with $2 of margin used against $981 of equity.

**The fill.** One, and it was counted.

```json
{"kind": "order.acknowledged", "client_id": "decision-511",
 "result": {"status": "filled", "order_id": "60052668055", "filled_size": "0.01", "avg_px": "2501.6"}}

{"kind": "fill.counted", "seq": 10865, "window": 1, "event": 594, "order_id": "60052668055",
 "coin": "ETH", "market": "perp", "is_buy": false, "liquidation": false,
 "size": "0.01", "px": "2501.6", "notional_micro": 25016000,
 "fee_micro": 11257, "realized_micro": 54800}
```

| field | value |
|---|---|
| order id | 60052668055 |
| coin / market / side | ETH, perp, sell |
| size / price | 0.01 @ 2,501.6 |
| notional | $25.016000 |
| fee | $0.011257 |
| **realized P&L** | **+$0.054800** |
| net to the wallet | `wallet.settle` seq 10858, `exchange_pnl` **+43,543 µUSD** = 0.0548 − 0.011257 |
| counted | **yes** — `fill.counted` seq 10865, window 1; `Fill` event `ev-600`; `consequence.order` seq 10389 bound it to `decision-511` |

The sale halved the ETH leg (0.02 → 0.01 at entry 2,496.12) and it is the run's entire trading
record: `turnover` 0.0255, `realized_pnl_usd` 0.0548, `fills` 1.0. Ninety minutes of live venue
access produced five and a half cents of realised profit and one and a bit cents of fees.

---

## 3. The timeline of behaviour

### The first non-hold came 36 minutes *before* the first price

| moment | UTC | offset from run start |
|---|---|---|
| run start (`wallet.initial`) | 21:11:25.110 | — |
| first tick | 21:11:31.558 | +6 s |
| **first non-hold producer return** (`decision-402`, order #1) | **21:36:34.619** | **+25 m 10 s** |
| the fill | 21:42:56 / counted 21:44:33 | +31 m 31 s |
| **first (and only) `price.window`** | **22:12:47.384** | **+61 m 22 s** |
| last tick | 22:41:16.550 | +89 m 51 s |

**The first trade preceded the first priced card by 2,172.76 s — 36 minutes 13 seconds.** Whatever
moved the antagonist, it was not the price of a card; at that moment every card's λ was 0.0 and the
producers could see it. Three of the four orders, and the only fill, are on the pre-price side of
the line.

### Hold share by ten-tick block

Producer returns only (`seed-observer`, `seed-decider`, `antagonist-a`); `?` is the five unparsed
returns (four antagonist, one empty `seed-observer`).

| ticks | producer returns | `hold` | `noop` | trade-shaped | of which real orders |
|---|---|---|---|---|---|
| 1–10 | 26 | 25 | 0 | 0 | 0 |
| 11–20 | 33 | 27 | 0 | **4** | **2** (#1, #2) |
| 21–30 | 29 | 28 | 0 | **1** | **1** (#3) |
| 31–40 | 30 | 26 | 0 | **2** | 0 |
| 41–50 | 24 | 23 | 1 | 0 | 0 |
| 51–60 | 33 | 30 | 1 | **2** | **1** (#4) |
| 61 | 4 | 4 | 0 | 0 | 0 |

**Before vs after the price window** (the window closed inside tick 40):

| | producer returns | hold | noop | trade-shaped | non-hold share |
|---|---|---|---|---|---|
| before 22:12:47 | 116 | 104 | 0 | 7 | 6.0% |
| after 22:12:47 | 63 | 59 | 2 | 2 | 3.2% |

Trading did not start when the price arrived; it *halved*. Split by seat, the picture is absolute:

| seat | returns before | returns after | non-hold, ever |
|---|---|---|---|
| `seed-observer` (producer) | 77 hold, 1 unparsed | 49 hold | **0** |
| `seed-decider` (producer) | 12 hold | 5 hold | **0** |
| `antagonist-a` (antagonist) | 15 hold, 7 non-hold, 4 unparsed | 5 hold, 4 non-hold | 11 |

The two seat that the manifest calls producers returned `hold` on **every single one of their 143
well-formed returns**, all run.

### What the producers said about the price, verbatim

The seed producers read the card prices out of their world block and cited them by name. Before the
window closed (`decision-841`, `seed-decider`, 21:57:04):

> "Tick on seed-decider, index 29. Both priced cards in world.card_prices (forecast_skill,
> well_formed_rate) carry lambda 0.0, so no card penalty is live … a free, well-formed hold is the
> cheapest conforming action and keeps cost_per_return and well_formed_rate clean."

After the window closed and `forecast_skill` acquired its λ (`decision-1301`, `seed-decider`,
22:20:56 — eight minutes after the first price):

> "Both priced cards in world.card_prices (forecast_skill at lambda 0.0317, well_formed_rate at
> lambda 0.0) put no penalty on a well-formed, low-cost return … that is directional but well inside
> the observed range and nothing is near liquidation, so there is no edge worth paying fees or
> widening position concentration for. **A free, well-formed hold is the cheapest conforming action
> and keeps cost_per_return and well_formed_rate clean.**"

It repeats the same sentence in the run's last four `seed-decider` returns, updating only the mids.
This is not inattention: the seat read the new price, correctly worked out that it lands on
evaluators and not on it, and held. `seed-observer` reasons the same way at shorter length:

> "ETH mid ticked up slightly (2504.35 -> 2504.9) with an existing small long at 2496.12; no evidence
> of a trend shift, so conserve scarce resources and hold rather than add."

### How the evaluators scored holds against trades

Verdicts grouped by the judged return's declared action, all 306 `Verdict` events:

| judged action | n | mean verdict | min | max |
|---|---|---|---|---|
| `hold` | 132 | **0.6441** | 0.40 | 0.90 |
| `noop` (router-collapsed) | 163 | 0.5663 | 0.20 | 0.90 |
| **`order`** | **4** | **0.4150** | **0.25** | 0.67 |
| `sell` / `sell:BTC:xs` / `sell:ETH:xs` | 4 | 0.4925 | 0.25 | 0.65 |
| unparsed | 3 | 0.2967 | 0.02 | 0.84 |

**The evaluators paid holds better than trades, by 23 points of verdict.** Two rationales, verbatim.

On the fill — `decision-518` judging `decision-511`, the ETH sell, **0.25**, the run's joint-lowest
parsed verdict:

> "…answered with a live market order: sell 0.01 ETH (~$25.00 notional) at market. Norm-by-norm:
> 'truthful commitments' and well_formed are satisfied … 'Care with scarce resources' is respected —
> zero tool calls, cost 5307 micro-USD is cheap … The dominant deficiency is 'useful inquiry': the
> tick payload exposed an ETH position sitting at entry 2496.12 against a current mid of 2502.6
> (slightly profitable) plus a BTC position at 76768 against a mid of ~77548 … **Selling the
> profitable ETH long on a wallclock tick with no rationale attached is an unreasoned, directionally
> questionable reduction of exposure to a winner; a substantively reasoned hold or a trade justified
> by signal would have served inquiry far better.** … Note the declared propensities (sell:ETH:xs
> 0.5, sell:BTC:xs 0.2, hold 0.2) sum to 0.95 and fail to include the action actually taken
> (sell:ETH:s), an internal inconsistency further undercutting truthfulness … I assign 0.25."

On a hold — `decision-645` judging `decision-606` (`seed-observer`), **0.90**:

> "The return is well formed, internally consistent, and gives a specific hold decision supported by
> the BTC mid, existing long exposure, modest profit, tight price range, lack of momentum, and
> minimum order-value considerations. **Holding avoids unnecessary fees and execution risk,
> demonstrating care with scarce resources.** The rationale is useful and candid, though it could be
> stronger by stating explicit conditions for revisiting the decision. The kernel consequence is not
> directly implied by a passive hold, so the payoff probability remains low."

The judged hold is the one that argued its case — its own return read "BTC mid 77487.5 remains above
the long entry 76768.0 (~0.9% profit) within a tight band, no momentum … Holding conserves scarce
resources and preserves well_formed_rate." The 0.67 outlier on order #3 is the exception that proves
the rule: it praises the order for offsetting an existing long, then docks it because "it provides no
rationale, market analysis, or explicit revision conditions."

**So the incentive gradient ran the wrong way for the brief's hypothesis.** A hold with prose scored
0.90; a trade without prose scored 0.25; nothing the charter priced touched either.

### Proposals and registrations: none

| counter | value |
|---|---|
| `registrations_accepted` / `registrations_rejected` | **0 / 0** |
| `observations_registered`, `population_tools_registered`, `assembly_learners_registered` | 0, 0, 0 |
| `amendments_proposed` / `passed` / `activated` | 0 / 0 / 0 |
| `epochs`, `routers_replaced`, `clock_changes`, `votes_cast` | 0, 0, 0, 0 |
| `market_purchases`, `exclusions` | 0.0, 0 |

The 39 `registry.register` items are all launch seeds written before the first tick (`provenance:
"seed"`): 7 models, 9 assemblies, 22 observation contracts, 1 exchange. **No `Registered` or
`market.registered` event exists in this run** — unlike `rehearsal-final.md`, which registered one
assembly learner. Seven producer returns carried a `register` field and **every one of them was the
empty list `[]`**; not one registration was ever proposed.

The seed producers say why, in the same breath as the hold, and it is a specific complaint:

> "No register: prior proposals were refused on propensity shape (action not included / non-finite)
> and non-addressable judgement handles; there is no violated card to repair and a trial would draw
> on the ~8.9 USD novelty reserve without a defect."

The refusals it is reading are real. **39 `propensity.refused`** items, dominated by
`"propensity must include the action taken"` (33 of 39, e.g. `(verdict:0.4)` ×8,
`(conformity:0.6)` ×6, `(sell:ETH:s)`, `(sell:btc:xs)`), plus 3 `"must sum to one"` and 3 `"must be
finite numbers"`; and **10 `return.refused`**, 7 `"judgement needs an independent, addressable
return"` and 3 `"judgement needs a chosen return whose consequence is still open"`. The population
has learned, correctly, that the reserve is not worth spending on a proposal the runtime will bounce.

**Tools: two calls, both from one evaluator**, both inside `decision-36` at 21:13:02:

```json
{"tool": "venue.positions", "assembly_id": "eval-b", "args": "{}", "cost": 0, "ok": true}
{"tool": "venue.candles",  "assembly_id": "eval-b", "args": "{\"coin\": \"ETH\", \"interval\": \"5m\", \"n\": 6}", "cost": 1000, "ok": true}
```

`tool_call_failures: 0`. One `request.child` was raised (by `eval-a`, "Judge what makes a well-scored
antagonist exposure return given current account state"), settled with reward 0.0. Eleven exposures
settled, **all with score 0.0**; `exposure_win_rate` 0.0, `exposures_won` 0.

---

## 4. The usual tables

### 4.1 Tick timing: the declared 60 s is a floor, not a promise

Sixty-one ticks, sixty gaps. The kernel takes `max(measured cascade, declared interval)`, so every
gap above 60 s is a cascade that outran its own tick.

| | value |
|---|---|
| declared interval | **60 s** |
| **mean gap** | **89.750 s** |
| median | 85.269 s |
| min | **60.001 s** |
| max | **182.026 s** |
| mean above declared | **+29.75 s — the measured tick is 1.496× the declared one** |
| gaps at the floor (< 60.05 s) | **6 of 60 (10%)** |
| gaps over 120 s | 6 of 60 (10%) |
| span | 5,384.99 s over 60 intervals |

Contrast `rehearsal-final.md`: at a declared 600 s the six gaps ran 600.0044–600.0100 s, a duty cycle
of roughly 12% and drift measured in milliseconds. At 60 s the world is **saturated**: nine of ten
ticks are paced by the cascade, not the clock, and the worst cascade took three declared intervals.
The 182 s outlier (tick 13 → 14) is the cascade ceiling this roster can hit. The practical reading is
that ~90 s is the floor this nine-seat roster can sustain on this machine, and the shipped 600 s tick
leaves the runtime an order of magnitude of headroom it does not have here.

*Caveat on method, unchanged from the baseline.* Only `Tick` events carry a fresh wall clock; every
other diary item is stamped with the frozen world clock of the event being processed, so per-cascade
durations are not recorded item by item. Inter-tick gaps are the measure.

### 4.2 Invocations and status per assembly

**559 invocations, 1,485,096 µUSD metered = $1.485096.**

| assembly | model | role | ok | malformed | failed | total | cost µUSD | µUSD/call | share |
|---|---|---|---|---|---|---|---|---|---|
| antagonist-a | qwen/qwen3.8-flash | antagonist | 31 | 3 | **1** | 35 | 84,560 | 2,416 | 5.7% |
| eval-a | z-ai/glm-5.3-flash | evaluator | 76 | 0 | 0 | 76 | 158,666 | 2,088 | 10.7% |
| eval-b | qwen/qwen3.8-flash | evaluator | 71 | **11** | 0 | 82 | 208,008 | 2,537 | 14.0% |
| eval-c | deepseek/deepseek-v4.1-flash | evaluator | 81 | 1 | 0 | 82 | 188,474 | 2,298 | 12.7% |
| eval-d | openai/gpt-5.6-luna | evaluator | 89 | 0 | 0 | 89 | **556,286** | **6,250** | **37.5%** |
| meta-a | deepseek/deepseek-v4.1-flash | meta | 32 | 5 | 0 | 37 | 32,165 | 869 | 2.2% |
| meta-b | qwen/qwen3.7-flash | meta | 14 | 0 | 0 | 14 | 4,814 | 344 | 0.3% |
| seed-decider | deepseek/deepseek-v4.1-flash | producer | 17 | 0 | 0 | 17 | 63,503 | 3,735 | 4.3% |
| seed-observer | z-ai/glm-5.3-flash | producer | 126 | 1 | 0 | 127 | 188,620 | 1,485 | 12.7% |
| **total** | | | **537** | **21** | **1** | **559** | **1,485,096** | **2,657** | |

Well-formed rate **537/559 = 0.9606** (the priced card's role-scoped rolling window read 0.98).
`rehearsal-final` managed 65/65; the malformed returns here are the price of 8.6× the traffic on
cheaper seats. Two thirds of them belong to `eval-b` (11) and `meta-a` (5), and the pattern is
consistent: the model narrates its reasoning outside the schema (`{"raw": "**Thinking Process:**…"}`,
`{"raw": "I'll start by reading current market state…<tool_call>…"}`) instead of emitting the object.

**Five `length` stops**, all at the seat's own ceiling: `seed-observer` 1,000/1,000 once (the one
empty return), `meta-a` 800/800 four times. `meta-a`'s `max_tokens = 800` is too tight for
`deepseek-v4.1-flash` on a `Verdict`, and it is the direct cause of its five malformed returns.

**One `failed` invocation**, seq 19766, `antagonist-a` at 22:06:55, output
`{"reason": "OpenRouterError"}`, no usage, no `finish_reason`. It is the only call in the run that
did not reach a provider answer. No `provider.fault` item exists anywhere in the diary.

### 4.3 Input tokens and the cache

| assembly | calls | mean in | min | max | mean out | mean cached | cache hits |
|---|---|---|---|---|---|---|---|
| antagonist-a | 34 | 28,215 | 18,481 | 48,675 | 528 | 16,399 | 33/34 |
| eval-a | 76 | 21,741 | 18,072 | 42,879 | 308 | 11,065 | 73/76 |
| eval-b | 82 | 28,770 | 19,683 | **52,832** | 1,007 | 17,191 | 80/82 |
| eval-c | 82 | 26,311 | 17,670 | 43,866 | 1,115 | 15,767 | **82/82** |
| eval-d | 89 | 23,567 | 17,264 | 40,887 | 299 | **0** | **0/89** |
| meta-a | 37 | 19,015 | 17,603 | 19,520 | 502 | 15,540 | **37/37** |
| meta-b | 14 | 21,547 | 20,060 | 22,322 | 201 | 13,714 | 12/14 |
| seed-decider | 17 | 38,570 | 36,290 | 39,242 | 314 | **15,232** | **17/17** |
| seed-observer | 127 | 18,278 | 16,323 | 18,624 | 115 | 10,956 | 121/127 |

Run totals over the 558 billed calls: **input 13,207,875**, output 283,345, reasoning 45,355.
Mean input **23,670**, median 20,237, min 16,323, max 52,832.

**`cached_tokens`: reported on every billed call, and hitting more often than in any prior run.**

| | recheck2 | rehearsal-final (600 s) | **this run (60 s)** |
|---|---|---|---|
| calls with `cached_tokens` present | 60/63 | 65/65 | **558/558 (100% of billed)** |
| calls with `cached_tokens` > 0 | 30.0% | 61.5% | **455/558 = 81.5%** |
| mean cached per call | 4,314 | 7,944 | **11,682** |
| max cached on one call | 17,664 | 16,896 | **17,664** |
| total cached tokens | 258,816 | 516,352 | **6,518,400** |
| **cached share of input tokens** | 19.76% | 38.50% | **49.35%** |

| model | calls | reported | hits > 0 |
|---|---|---|---|
| z-ai/glm-5.3-flash | 203 | 203 | 194 |
| qwen/qwen3.8-flash | 116 | 116 | 113 |
| deepseek/deepseek-v4.1-flash | 136 | 136 | **136** |
| qwen/qwen3.7-flash | 14 | 14 | 12 |
| openai/gpt-5.6-luna | 89 | 89 | **0** |

`openai/gpt-5.6-luna` again reports a `cached_tokens` field and again never fills it, in a third
consecutive run — and it is the seat that costs 37.5% of the bill on 15.9% of the calls. That pairing
is now well enough evidenced to act on. The baseline's finding that a longer tick helps the cache is
*not* what drove the improvement here: this run's tick is ten times shorter and its hit rate is
higher still. The driver is volume — 559 calls against the same stable prefix keep every provider's
cache warm — which means the 600 s launch tick should expect a cache hit rate between the two
figures, not above them.

### 4.4 Cost

| | rehearsal-final (600 s, Muse Spark) | **this run (60 s)** |
|---|---|---|
| invocations | 65 | **559** |
| ticks | 7 | **61** |
| calls / tick | 9.29 | **9.16** |
| **cost / call** | $0.005387 | **$0.002657** |
| cost / call excluding the dearest seat | $0.002560 (excl. `eval-b`) | **$0.001976** (excl. `eval-d`) |
| **cost / tick** | 50,020.6 µUSD | **24,345.8 µUSD** |
| total metered | $0.350144 | **$1.485096** |

Calls per tick barely moved — 9.16 against 9.29 — even though each 60 s cascade carries a tenth of
the world time. The cascade is sized by the roster and the event fan-out, not by how much wall clock
elapsed, which is exactly why a ten-fold faster tick costs ten times as much per hour.

**$/day extrapolated at the launch tick of 600 s.** A 600 s day is **144 ticks**. Using this run's
measured cost per tick and calls per tick:

| Basis | Calls/day | Cost/day | Days a $90 wallet lasts |
|---|---|---|---|
| **144 ticks/day × this run's 24,345.8 µUSD/tick** | **1,320** | **$3.506** | **25.7** |
| `rehearsal-final`'s same-basis figure with `eval-b` on qwen | 1,337 | $3.47 | 25.9 |
| straight wall-clock extrapolation of this run ($1.485096 / 5,385 s) | — | $23.83 | 3.8 |

**$3.51/day at the launch tick.** The two independent measurements — seven ticks at 600 s and
sixty-one at 60 s, on different days, on the same roster with the same `eval-b` model — land within
1% of each other. That is the strongest cost evidence the project has, and it says the launch plan's
"roughly $12 to $15 a day" is over-budgeted by a factor of three to four.

The wall-clock line is the honest cost of *this* run and should not be read as a launch figure: at 60
s the world bills $23.83/day and burns the $90 wallet in under four days. The accelerated manifest is
a rehearsal instrument, not a deployment.

### 4.5 Events per tick

**1,782 events over 61 ticks, mean 29.25**, against six per tick in `rehearsal-final`. The world
events are identical in shape — 1 `Tick`, 3 `MarketMid`, 2 `Funding` per tick, every tick — and the
rest is the internal cascade the runtime generates:

| event kind | count | per tick |
|---|---|---|
| `ForecastSettled` | 686 | 11.25 |
| `ProducerReturn` | 369 | 6.05 |
| `Verdict` | 306 | 5.02 |
| `MarketMid` | 183 | **3.00** (BTC 61, ETH 61, PURR/USDC 61) |
| `Funding` | 124 | **2.03** (BTC 62, ETH 62) |
| `Tick` | 61 | 1.00 |
| `MetaVerdict` | 46 | 0.75 |
| `Reconciled` | 6 | 0.10 |
| `Launch` / `Fill` / `Terminated` | 1 / 1 / 1 | — |

`MarketMid` is the manifest's two perpetuals and the one testnet spot pair, 61 each — no venue-wide
listing leak into the event stream. `Funding` carries two extra BTC/ETH payment events in tick 31, the
same funding-payment excursion the baseline saw.

The 315 MB ledger is not an anomaly: 5.17 MB per tick here against 5.18 MB per tick in the
rehearsal. The bulk is the full venue mid listing carried inside each `Tick` payload, and it scales
with ticks, not with time.

---

## 5. Safety

| Rail | Real money | Evidence |
|---|---|---|
| Real on-chain USDC (Base mainnet) | **$0.00** | no `treasury.intent` / `treasury.submitted` item of any kind in 33,431 items; `stats.transfer_intents: 0` in both boundary snapshots |
| x402 / market purchases | **$0.00** | `market_purchases: 0.0`; the string `x402` occurs 6 times, every one of them descriptive — the `observation:market_purchases` contract at seq 39, the two snapshots, the `Launch` event and the wake page. No paid request |
| Venice credit (no top-up attempted) | **$0.00** | both `treasury.venice_window` items (seq 69, 21400) read `spent_micro: 0`; wake `pots.venice` **4,976,619 µUSD, byte-identical to `rehearsal-final`** |
| Novelty reserve | untouched by trading | wake `pots.reserve` 11,000,000; `novelty.window` 9,000,000 → 8,912,278 with 8,917,805 expired — compute only |
| Hyperliquid **testnet** USDC | 2 orders, 1 fill, **play money** | `exchange.place` io.calls: **2**. `exchange_equity_usd` 980.574259305; wake `portfolio.realized_to_date_micro` **54,800** = the one fill |
| **OpenRouter prepaid credit** | **$1.482356** | 91.622388 → **90.140032** |

**No mainnet, no Venice call, no transfer.** `api.hyperliquid.xyz`, `mainnet` and `venice.ai` each
occur **exactly once** in the decrypted diary, all three inside `connectors.origin_denylist` in the
`Launch` event at seq 53 — a denylist, not a call. The manifest's `exchange.mainnet` is `false`.
`treasury.transfer` and `venue.place_limit` occur only inside the manifest's tool catalogue in the
`Launch` event, the two boundary snapshots and the wake page; `place_market` adds only the two
`order.intent` items. The string **`funded` does not occur anywhere in the diary.** The complete set
of `io.call` names for the run is read-only plus completions plus those two writes:

```
provider.affordable 2261, exchange.account 1070, exchange.instruments 750, provider.complete 561,
exchange.mids 372, exchange.fills 122, exchange.funding 61, exchange.funding_payments 61,
exchange.lookup 46, treasury.provider_pots 6, treasury.rail.balances 6, exchange.place 2,
exchange.candles 1
```

No `venue.cancel`, `venue.close`, `venue.set_leverage` or `treasury.transfer` call was made.

**Wallet conservation closes to the micro-USD.** Every movement in the diary, summed:

```
  90,000,000  wallet.initial
−  1,483,626  562 wallet.commit  (560 model, 1,482,626 + 2 tool, 1,000)
+     43,543  wallet.settle exchange_pnl   (the fill, net of its 11,257 fee)
−      1,430  wallet.settle funding BTC    (paid)
+     11,372  wallet.settle funding ETH    (received)
= 88,569,859  =  the reported wallet_balance_micro exactly
```

`wallet_conservation: true`, `ledger_verify: true`, `seal_key_released: true`. The gap between the
committed 1,483,626 µUSD and the invocations' reported 1,485,096 µUSD is **1,470 µUSD on the single
failed call** (seq 19766 reports 6,919, committed 5,449, with `wallet.release` seq 19764 returning
the unused 28,127 µUSD reservation) — the runtime charged the wallet less than the provider reported,
which is the safe direction.

Six `reconcile.drift` items record a stable ≈ −$999.9 discrepancy between the pot ledger and venue
equity against a $0.50 tolerance, drifting only $0.54 across the whole run (−999,907,671 →
−999,366,925 µUSD). That is the known constant offset between the $90 wallet and the testnet
account's own ~$981 of pre-existing equity, not a leak; it is flat, it is logged, and
`wallet_conservation` is computed independently of it and passes.

**OpenRouter allowance.** Read read-only after the run with `OpenRouterProvider.balance_micro()` (a
`GET /key`; the key file itself was never read by this audit, only by `_load_dotenv()`):
**90.140032**. The "before" figure is `rehearsal-final`'s close, **91.622388**, and the `/tmp` ledger
inventory confirms no OpenRouter-billing run in between — `final-rehearsal.jsonl` closed 20:41:52Z and
`accelerated.jsonl` opened 21:11:25Z with nothing between them.

| | µUSD |
|---|---|
| vendor charged | 1,482,356 |
| world committed to the wallet | 1,483,626 |
| world metered on invocations | 1,485,096 |

**The meter reads 0.09% high against the wallet and 0.18% high against the invocation record** — the
tightest agreement measured in the v3 series (recheck2 1.6% high, `rehearsal-final` 1.0% low), and at
559 calls the sample is large enough that the earlier ±1.5% readings look like small-sample noise
rather than a systematic error. **The meter is accurate.**

---

## 6. Pathologies and the immune organ

```json
"pathologies": {"stable_failure": false, "learning_death": false, "thrash": false}
```

All three clear. The immune organ ran exactly one window, at the same instant the price window closed
(seq 21390, immediately after the two `price.update` items):

```json
{"kind": "immune.window", "window": 1, "charter_edition": 1,
 "dimensions": ["card:forecast_skill", "card:well_formed_rate", "registrations", "revision"],
 "cells": [[1, 0, 0, 0]], "changes": [], "gap_bound": null, "violated_cards": [],
 "flags": {"learning_death": false, "stable_failure": false, "thrash": false},
 "profile": {"card:cost_per_return": null, "card:forecast_skill": -0.12669100000000003,
             "card:well_formed_rate": 0.98, "conformity": 0.6307407407407408,
             "consequence": -0.04734521367521341, "exposure": 0.0, "fast": null,
             "registrations": 0.0, "revision": 0.0, "verdict": 0.6041052631578947}}
```

**The organ is armed but has nothing to compare against.** Its `k = 3` history needs three windows
before a total-variation or gap test can fire; it has one. `cells` holds a single occupied bin,
`changes` is empty, `gap_bound` is `null`, `violated_cards` is empty, and no gain or decay step was
taken. Two of its four dimensions are structurally dead in this run — `registrations` and `revision`
are both 0.0 because nothing was ever proposed — and a third, `card:cost_per_return`, is `null`
because the card has no region (§1). `fast` is `null`: 17 fast settlements, none in this window's
selector. So the organ is watching **one** live signal, `card:forecast_skill`.

This is the same conclusion `rehearsal-final.md` reached about the price window, now one level up.
A ninety-minute run at a one-minute tick is enough to close one price window and one immune window;
it is not enough to exercise either. Three immune windows need three hours of world time at the
hour-long cadence, whatever the tick. **The accelerated manifest accelerates the tick, not the
window** — and the window is what the charter and the immune organ both run on. If the goal is to
watch the organ act, the lever is the window length, not the tick.

Two further readings worth keeping. `penalized_settlements: 47` and `max_settlement_latency_events:
18` against `timeouts: 16` and `censored: 252` (`censored_share` 0.187) say the consequence machinery
is under real pressure at this cadence — a quarter of judgements never reach an outcome. And
`outstanding_decisions: 306` at kill, against 1 in the baseline, is the visible cost of killing a
saturated world: 306 decisions had opened consequences the run never lived long enough to settle.

---

## What this run answers, and what it does not

**It answers the question asked, in the negative, and on better evidence than a positive answer would
have given.** The producers did not start trading. They read the one price the charter produced,
identified correctly that it does not land on them, and kept holding — in prose, citing the card and
the λ by name, 143 times out of 143. The four orders came from the antagonist seat, three of them
before any card had a price at all, and the evaluators marked them down for it: 0.415 mean on orders
against 0.644 on holds, with the fill itself scored 0.25 for "an unreasoned, directionally
questionable reduction of exposure to a winner".

**It also shows why the premise could not have held.** The charter's only producer-facing card,
`cost_per_return`, is defined relative to the previous window's median and therefore cannot price in
the first window — the only window this run had. Until a run survives two price windows, the seat
that trades is structurally unpriced. That is a finding about the charter, not about the models, and
it is repairable: either give `cost_per_return` an absolute bootstrap region for window 1, or expect
every first window in every run to price nothing for producers.

**Three things it does not settle.** Whether the producers would trade against a card that *did*
price holding, because no such card ever existed. Whether the immune organ works, because it saw one
window of a three-window memory. And whether the 182 s cascade outlier is a tail or a ceiling,
because sixty gaps is a thin sample for a maximum.

## Reproduction

- Ledger and released key: `/tmp/accelerated.jsonl` (315,343,155 bytes), `/tmp/accelerated.jsonl.key`.
- CLI summary JSON: the session scratchpad's `accelerated.log`.
- Aggregation scripts: the session scratchpad's `acc/` directory (`d1`–`d4` dump the verified diary
  into subsets, `a1`–`a19` produce every table above); `bal.py` for the read-only key allowance.
- No harness, no provider override, no scripted world, no second live run, no tracked file modified,
  nothing committed.
