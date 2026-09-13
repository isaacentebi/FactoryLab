# Recheck: does the shipped testnet world tick, and what is T47?

Two questions, answered live against the **shipped** `worlds/testnet.toml` and the shipped
OpenRouter seeds. No provider override, no manifest copy, no local patch.

Worktree `/Users/isaacentebi/Desktop/FactoryLab-rehearsal` at `8d7a1e1`, clean apart from the
previous rehearsal's two untracked harness files, neither of which was used. The three key
files sit at the repository root at mode 0600; only the CLI's own `_load_dotenv()` read them.
Hyperliquid **testnet** throughout, no manifest named `funded`, no Venice top-up, no mainnet,
no bare `pytest`, nothing committed. **Real on-chain USDC moved: $0.00.** Paid actions and
per-rail spend are in `docs/runs/recheck.md` (gitignored).

Manifest hash `0c3d229a734b8338f96416ec14b8f77a250491f39b3f1ea9637249333538fd63` in both runs.

| Run | Command | Ledger | Wall clock |
|---|---|---|---|
| 1 | `factorylab run --world testnet --duration 12m --ledger /tmp/recheck.jsonl --kill-at-end` | `/tmp/recheck.jsonl` | 14:37:04Z → 14:49:52Z (12m48s) |
| 2 | `factorylab run --world testnet --duration 70m --ledger /tmp/recheck-t47.jsonl --kill-at-end` | `/tmp/recheck-t47.jsonl` | 14:50:17Z → 16:02:20Z (72m03s) |

**Why not `--events 30`.** `--events N` is not N world events: `_cmd_run` passes it straight to
`LiveClock(interval_ns, events)` as the **tick** budget (`bootstrap.py:83`, `loop.py:170`), so
`--events 30` is thirty ticks — an hour of wall clock at the shipped 120 s tick, not a few
minutes. The brief's fallback was taken instead.

---

## 1. The shipped testnet world completes ticks — yes

**It finished.** Run 1 ran to its deadline and ended `explicit_kill:budget` with the seal
released; `wallet_conservation true`, `ledger_verify true`. Its summary line:

```
"world": "testnet", "seed": 4, "live": true, "charter_edition": 1,
"terminated": true, "termination_reason": "explicit_kill:budget", "seal_key_released": true,
"wallet_balance_micro": 99406610, "wallet_conservation": true, "ledger_verify": true,
"exchange_equity_usd": "980.427563570", "outstanding_decisions": 1,
"stats": {"events": 92, "decisions": 39, "invocations": 26, "noops": 13,
          "reserve_windows": 1, "orders_placed": 0, "fills": 0,
          "invocation_status": {"ok": 22, "malformed": 3, "failed": 1},
          "stop_reasons": {"stop": 25, "none": 1}}
```

**Six world events per tick, exactly as expected.** Every `Tick` in both runs is followed by
three `MarketMid` (the manifest's `BTC`, `ETH` and the venue's `PURR/USDC`) and two `Funding`
(`BTC`, `ETH`) — the world's own trading markets, not the venue's 212 perpetuals and 1,263 spot
pairs. Run 1's first tick, from the diary:

```
[54]  Launch
[56]  Tick index=0
[113] MarketMid BTC
[145] MarketMid ETH
[169] MarketMid PURR/USDC
[192] Funding BTC
[209] Funding ETH
[226] ProducerReturn …
[575] Tick index=1
```

Run 2 repeated it 24 times without exception:

```
tick 0  seq=56     venue events after it: {'MarketMid': 3, 'Funding': 2}
tick 1  seq=495    (+120.0s)  {'MarketMid': 3, 'Funding': 2}
…
tick 4  seq=2270   (+192.9s)  {'MarketMid': 3, 'Funding': 4}   <- two settled funding *payments*
…
tick 23 seq=12412  (+139.4s)  {'MarketMid': 3, 'Funding': 2}
ticks: 24  span_s 4126.3
```

Tick 4's two extra `Funding` events are settled funding **payments** from
`LiveVenue.funding_payments`, which the broadcast bound deliberately does not filter: a payment
is cash that moved. The rate broadcast itself never exceeded five events.

That is the finding that dominated the last rehearsal, closed: **≈1,687 events per tick → 6.**

**Invocations and pace.**

| | run 1 | run 2 |
|---|---|---|
| ticks delivered | 3 | 24 |
| world + internal events (`stats.events`) | 92 | 714 |
| decisions | 39 | 319 |
| invocations | 26 | 202 |
| noops | 117 of 319 decisions in run 2 | |
| invocations per tick | 8.7 | 8.4 |
| tick gap, mean / min / max | 178 s, 452 s (2 gaps) | 179.4 s / 120.0 s / 493.1 s |

The tick **completes**, but it does not complete inside its declared 120 s interval: the mean
delivered gap in run 2 was 179 s and the slowest was 493 s. Run 1's 12 minutes therefore bought
3 ticks rather than 6, and run 2's 70 minutes bought 24 rather than 35. Nothing is wedged — the
clock simply fires late when a tick's work outruns the interval, and `LiveClock` records that as
`measured_interval_ns`. The dominant cost is not the event count any more but the size of each
invocation: **mean input 159,846 tokens per call** (run 2, 195 billed calls, mean output 354),
because every invocation embeds the whole world block.

---

## 2. T47 — well-formed rate over the first closed window

Run 2 closed one price window. `reserve_windows: 2`, `price_updates: 1`, and the first
`price.window` item, at seq 10617 immediately after tick 20:

```
[10617] price.window {"window": 1, "window_end_event": 586, "charter_edition": 1,
  "observations": {"well_formed_rate": 0.8703703703703703, "cost_per_return": 28231.1875,
                   "forecast_skill": -0.24324996083959896, "noop_share": 0.9672131147540983,
                   "censored_share": 0.1970649895178197, "verdict_mean": 0.745,
                   "turnover": 0.0, "fills": 0.0, …},
  "regions": {"well_formed_rate": {"kind": "min", "lo": 0.9, "scale": 0.9},
              "forecast_skill":  {"kind": "min", "lo": 0.0, "scale": 2.0}},
  "values": {"forecast_skill": -0.068996}}
```

### **`well_formed_rate` = 0.8704** — below the charter's acceptable region of at least 0.9.

That is 141 `ok` out of 162 invocations closed inside window 1 (`ok`/invocations, per
`pricing.py:_close_price_window`). Over the whole 202-invocation run the rate is 176/202 = 0.871.

**Per assembly.** Window 1 (the 162 invocations the rate is computed from):

| assembly | ok | malformed | failed | total |
|---|---|---|---|---|
| antagonist-a | 6 | 2 | 1 | 9 |
| eval-a | 21 | 3 | 0 | 24 |
| eval-b | 24 | 4 | 4 | 32 |
| eval-c | 16 | 4 | 0 | 20 |
| eval-d | 19 | 0 | 0 | 19 |
| meta-a | 10 | 2 | 0 | 12 |
| meta-b | 3 | 0 | 0 | 3 |
| seed-decider | 7 | 0 | 0 | 7 |
| seed-observer | 35 | 1 | 0 | 36 |
| **total** | **141** | **16** | **5** | **162** |

Whole run (202 invocations, 5,097,508 µUSD metered):

| assembly | ok | malformed | failed | total | cost µUSD |
|---|---|---|---|---|---|
| antagonist-a | 7 | 3 | 1 | 11 | 278,369 |
| eval-a | 26 | 4 | 0 | 30 | 685,471 |
| eval-b | 29 | 5 | 6 | 40 | 930,977 |
| eval-c | 16 | 4 | 0 | 20 | 240,843 |
| eval-d | 28 | 0 | 0 | 28 | 1,077,159 |
| meta-a | 14 | 2 | 0 | 16 | 365,179 |
| meta-b | 3 | 0 | 0 | 3 | 52,318 |
| seed-decider | 9 | 0 | 0 | 9 | 353,569 |
| seed-observer | 44 | 1 | 0 | 45 | 1,113,623 |
| **total** | **176** | **19** | **7** | **202** | **5,097,508** |

Run 1's 26 invocations, for comparison: `eval-c` 5 ok / 2 malformed, `meta-a` 2 ok / 1 malformed,
`eval-b` 1 ok / 1 failed, everyone else clean.

### `length` versus `stop`

```
stop reasons by status (run 2, whole run): ok {'stop': 176}
                                           malformed {'stop': 15, 'length': 4}
                                           failed {'none': 7}
window 1 only:                             malformed {'stop': 12, 'length': 4}
```

**Only 4 of 19 malformed returns (21%; 4 of 16 = 25% inside window 1) stopped on `length`.**
The other 15 stopped on `stop` — a complete, parseable JSON body that fails its outcome schema.
`length` is the minority cause, and it is not evenly spread: one each from `antagonist-a`,
`eval-a`, `meta-a` and `seed-observer`; the whole `eval-*` malformed mass except one is `stop`.
Three of the four `length` returns are the reasoning-budget fault the manifest's `eval-b` comment
already anticipates, and the diary names it:

```
provider.fault {"assembly_id": "eval-a", "served_by": "z-ai/glm-5.3-flash", "cost": 15480,
  "reasoning_tokens": 1500, "max_tokens": 1500,
  "reason": "hidden reasoning consumed the whole completion budget; no visible reply"}
provider.fault {"assembly_id": "antagonist-a", "served_by": "qwen/qwen3.8-flash", "cost": 26934,
  "reasoning_tokens": 2532, "max_tokens": 2500, "reason": … }
provider.fault {"assembly_id": "seed-observer", "served_by": "z-ai/glm-5.3-flash", "cost": 15194,
  "reasoning_tokens": 1000, "max_tokens": 1000, "reason": … }
```

A representative `stop` malformation — complete JSON, wrong shape — is `eval-c` at seq 951:

```
[951] invocation eval-c (evaluator, malformed, stop=stop, finish=stop, cost=10782,
      served_by=tencent/hy3, usage={"input_tokens": 153380, "output_tokens": 298})
      outputs: {"raw": "{ \"about_handle\": null, \"verdict\": 0.85, \"payoff\": 0.4, … }"}
```

`raw` with no `reason` means the body parsed as JSON and then failed `validate_schema`. Raising
`max_tokens` fixes at most a fifth of the malformed mass; the rest is schema discipline.

The 7 `failed` returns carry `stop_reason "none"`, `cost 0` and `outputs {"reason":
"OpenRouterError"}` — six on `eval-b` (`qwen/qwen3.8-flash`), one on `antagonist-a`. They are
provider errors, not model output, and they sit in the `well_formed_rate` denominator.

### The card was not priced, only the observation

`values` carries `forecast_skill` alone. The `well_formed_rate` **card** declares
`window = {kind = "returns", n = 100, per = "role"}`, and `measure_card` skips any scope with
fewer than `n` rows (`charter/measurement.py:333-338`). With 162 returns split across producer,
evaluator and meta, no role had 100, so neither `well_formed_rate` nor `cost_per_return` produced
a card value and neither got a price update — `stats.price_updates: 1`. At the observed
8.4 invocations per tick and 179 s tick, the evaluator role reaches 100 returns in roughly three
hours and the meta role in roughly six. **The observation is measured every window; the card that
prices it is hours behind.**

---

## Spend per rail

| Rail | Real money | Evidence |
|---|---|---|
| Real on-chain USDC (Base mainnet) | **$0.00** | no `treasury.intent`/`treasury.submitted` in either diary; wake `spend_by_rail_per_day.x402 = 0` |
| Venice credit (no top-up attempted) | **$0.00** | wake `.venice = 0`; `venice_micro` 4,976,619 before and after |
| Base Sepolia testnet USDC (the reserve) | $0.00 | wake `reserve.usdc_micro` 4,966,000 in both wakes |
| Hyperliquid **testnet** USDC | no activity at all | `orders_placed 0`, `orders_rejected 0`, `fills 0` in both runs; `realized_to_date_micro` −17,250 unchanged from the last rehearsal |
| **OpenRouter prepaid credit** | **$5.607121** | key allowance 97.883790 → 97.357976 → 92.276669 |

Per run: run 1 **$0.525814** (metered 593,390 µUSD), run 2 **$5.081307** (metered 5,097,508 µUSD).
The world's own meter and the vendor's credit agree to 1.5%; the meter reads slightly high because
each reported cost is rounded up.

### Daily cost at the 120 s tick

From run 2, the only run long enough to be a rate:

- **calls per tick: 8.4** (202 invocations / 24 ticks)
- **cost per call: $0.0252** (5,081,307 µUSD / 202) — driven by ~160 k input tokens per call
- **cost per tick: $0.212**

| Basis | Ticks/day | Cost/day |
|---|---|---|
| declared 120 s tick | 720 | **$152** |
| measured 179 s tick (what the loop actually delivers) | 482 | **$102** |
| straight extrapolation of run 2's wall clock ($5.08 / 72 min) | — | **$102** |

The launch plan's "roughly $12 to $15 a day" assumed ~30 events per tick. The event count is now
six, but the per-call token count is not: **~$100–150/day**, an order of magnitude over plan. The
remaining lever is the size of the world block, not the number of events.

## Reproduction

- Ledgers, released keys, wake pages and the extracted diary items: `/tmp/recheck.jsonl`,
  `/tmp/recheck-t47.jsonl`, `/private/tmp/fl-rc/`.
- Everything quoted above is `factorylab postmortem`-equivalent output against the released keys;
  the aggregation scripts are in `/private/tmp/fl-rc/` (`analyze.py`, `win.py`, `tok.py`,
  `ticks.py`, `inv.py`). No harness, no provider override, no scripted world.
