# Final rehearsal: the 600 s tick, the $90 wallet, and the Muse Spark seat

A live Hyperliquid **testnet** run against the **shipped** `worlds/testnet.toml` after the launch
decisions landed: `tick_interval = "600s"`, `initial_balance_usd = "90"`, `eval-b` on
`meta/muse-spark-1.3` (`max_tokens = 1500`). Directly comparable to `recheck2.md`: same tables,
same method, no provider override, no manifest copy, no local patch.

Repository `/Users/isaacentebi/Desktop/FactoryLab` on `main` at `c935d2d`
(`Edition 1 draft: forecast_skill card restored; decisions recorded`), working tree clean before
and after; the only file this audit writes is this one, untracked. The three key files sit at the
repository root and only the CLI's own `_load_dotenv()` read them. Hyperliquid **testnet**
throughout, no manifest named `funded`, no Venice top-up, no mainnet, no fund transfer, no bare
`pytest`, nothing committed. **Real on-chain USDC moved: $0.00.**

Manifest hash `8677378705db65c25bbf774eed2a830531039e6fa9a841a0a43541156e16de9c`
(recheck2 measured `9ad039b5…`; the tick, the wallet and the `eval-b` model all changed it).

| Run | Command | Ledger | Wall clock |
|---|---|---|---|
| 4 | `factorylab run --world testnet --duration 70m --ledger /tmp/final-rehearsal.jsonl --kill-at-end` | `/tmp/final-rehearsal.jsonl` | launch 19:40:33.07Z → last ledger write 20:41:52.10Z (61m19s) |

Read back with `factorylab postmortem` and with `factorylab.versioning.reader.read_diary`; the
wake page came from `factorylab wake --ledger /tmp/final-rehearsal.jsonl --out <dir>`.

> **Tick timing in this run is clean.** Codex had exited before the run and the test gate started
> only after it ended; the machine carried nothing else for the whole window. Unlike `recheck2.md`,
> the tick-timing conclusion below is load-bearing.

**Summary line.**

```
"world": "testnet", "seed": 4, "live": true, "charter_edition": 1,
"terminated": true, "termination_reason": "explicit_kill:budget", "seal_key_released": true,
"wallet_balance_micro": 89674692, "wallet_conservation": true, "ledger_verify": true,
"exchange_equity_usd": "980.956510305", "outstanding_decisions": 1,
"stats": {"events": 227, "decisions": 97, "invocations": 65, "noops": 32,
          "reserve_windows": 2, "orders_placed": 0, "orders_rejected": 0, "fills": 0,
          "invocation_status": {"ok": 65},
          "stop_reasons": {"stop": 65}}
```

---

## 1. Tick timing: the 600 s tick is delivered to the millisecond

Every `Tick` world event carries `source: "wallclock"` and its own `ts_ns`. Seven ticks, six gaps:

| gap | from → to (UTC) | seconds | drift vs 600 s |
|---|---|---|---|
| 1 | 19:40:38.203924 → 19:50:38.208319 | 600.004395 | +4.4 ms |
| 2 | 19:50:38.208319 → 20:00:38.213366 | 600.005047 | +5.0 ms |
| 3 | 20:00:38.213366 → 20:10:38.221001 | 600.007635 | +7.6 ms |
| 4 | 20:10:38.221001 → 20:20:38.229729 | 600.008728 | +8.7 ms |
| 5 | 20:20:38.229729 → 20:30:38.237855 | 600.008126 | +8.1 ms |
| 6 | 20:30:38.237855 → 20:40:38.247883 | 600.010028 | +10.0 ms |

**mean 600.0073 s, min 600.0044 s, max 600.0100 s.** Total accumulated drift over the whole run is
44 ms (`uptime_ns` 3,600.043959 s across six declared intervals of 600 s). The declared interval is
the delivered interval.

**No tick's work outran the interval.** The scheduler cannot deliver tick *N+1* while tick *N*'s
cascade is still running, so an overrun shows up as a late gap; the largest gap is 10 ms long.
A direct upper bound on cascade duration is also available: tick 7 was delivered at 20:40:38.25 and
the ledger's last byte — that cascade's 31 events, its 10 invocations, the termination record, the
seal and the head file — was written at 20:41:52.10, **73.9 s** later, shutdown included. Work per
tick is on the order of a minute against a ten-minute interval, roughly 12% duty cycle. Contrast
`recheck.md`'s 179 s delivered gap on a 120 s declared tick: at 600 s the runtime has more than an
order of magnitude of headroom.

*Caveat on method.* Only `Tick` events and the `runtime.input` that ingests them carry a fresh
wall clock; every other diary item is stamped with the frozen world clock of the event being
processed, so per-cascade durations are not recorded item by item. The two independent measures
above — inter-tick drift, and the last cascade against the file's mtime — are what the ledger
supports.

---

## 2. Cost

**Total metered 350,144 µUSD = $0.350144** over 65 invocations and 7 ticks. Every invocation
reached a provider and was billed (recheck2 had 3 unbilled `failed` calls).

| | recheck2 (run 3) | **this run** |
|---|---|---|
| invocations | 63 | **65** |
| calls / tick | 9.00 | **9.29** |
| mean input tokens / call | 21,825 | **20,636** |
| median / min / max input | 18,536 / 16,346 / 47,661 | 18,391 / 16,350 / 45,985 |
| mean output tokens / call | 343 | 320 |
| **cost / call** | **$0.003500** | **$0.005387** |
| cost / call, excluding `eval-b` | — | **$0.002560** |
| cost / tick | — | **$0.050021** (50,020.6 µUSD) |

The prompt did not grow. Mean input tokens fell slightly and the median is flat; the bill per call
rose 54% purely because one seat moved to a model priced 8× the roster median.

**Per assembly, whole run (65 invocations, 350,144 µUSD).**

| assembly | model | ok | malformed | failed | total | cost µUSD | µUSD / call | share |
|---|---|---|---|---|---|---|---|---|
| antagonist-a | qwen/qwen3.8-flash | 4 | 0 | 0 | 4 | 12,224 | 3,056 | 3.5% |
| eval-a | z-ai/glm-5.3-flash | 9 | 0 | 0 | 9 | 18,585 | 2,065 | 5.3% |
| eval-b | **meta/muse-spark-1.3** | 8 | 0 | 0 | 8 | **204,251** | **25,531** | **58.3%** |
| eval-c | deepseek/deepseek-v4.1-flash | 6 | 0 | 0 | 6 | 12,297 | 2,050 | 3.5% |
| eval-d | openai/gpt-5.6-luna | 9 | 0 | 0 | 9 | 60,207 | 6,690 | 17.2% |
| meta-a | deepseek/deepseek-v4.1-flash | 4 | 0 | 0 | 4 | 5,430 | 1,358 | 1.6% |
| meta-b | qwen/qwen3.7-flash | 2 | 0 | 0 | 2 | 882 | 441 | 0.3% |
| seed-decider | deepseek/deepseek-v4.1-flash | 3 | 0 | 0 | 3 | 10,231 | 3,410 | 2.9% |
| seed-observer | z-ai/glm-5.3-flash | 20 | 0 | 0 | 20 | 26,037 | 1,302 | 7.4% |
| **total** | | **65** | **0** | **0** | **65** | **350,144** | **5,387** | |

**Input tokens per assembly (all 65 calls billed).**

| assembly | model | calls | mean in | min | max | mean out | mean cached | cache hits |
|---|---|---|---|---|---|---|---|---|
| antagonist-a | qwen/qwen3.8-flash | 4 | 25,860 | 18,511 | 45,985 | 656 | 8,448 | 2/4 |
| eval-a | z-ai/glm-5.3-flash | 9 | 21,022 | 17,838 | 39,350 | 280 | 10,240 | 8/9 |
| eval-b | meta/muse-spark-1.3 | 8 | 18,182 | 17,258 | 18,636 | 660 | **0** | **0/8** |
| eval-c | deepseek/deepseek-v4.1-flash | 6 | 19,167 | 17,670 | 20,139 | 424 | 10,581 | 4/6 |
| eval-d | openai/gpt-5.6-luna | 9 | 25,225 | 17,264 | 39,414 | 319 | **0** | **0/9** |
| meta-a | deepseek/deepseek-v4.1-flash | 4 | 17,481 | 17,203 | 17,634 | 371 | 11,584 | 3/4 |
| meta-b | qwen/qwen3.7-flash | 2 | 20,079 | 19,799 | 20,359 | 230 | 8,000 | 1/2 |
| seed-decider | deepseek/deepseek-v4.1-flash | 3 | 36,838 | 36,516 | 37,221 | 205 | 15,232 | 3/3 |
| seed-observer | z-ai/glm-5.3-flash | 20 | 17,029 | 16,350 | 17,336 | 121 | 10,944 | 19/20 |

Run totals: input 1,341,312 tokens, output 20,816, reasoning 6,279.

### `cached_tokens`: present everywhere, and hitting twice as often as in recheck2

| | recheck2 | this run |
|---|---|---|
| calls with `cached_tokens` present | 60 / 63 (100% of billed) | **65 / 65 (100%)** |
| calls with `cached_tokens` > 0 | 18 / 60 = 30.0% | **40 / 65 = 61.5%** |
| mean cached per call | 4,314 | **7,944** |
| max cached on one call | 17,664 | 16,896 |
| total cached tokens | 258,816 | **516,352** |
| **cached share of input tokens** | 19.76% | **38.50%** |

| model | calls | reported `cached_tokens` | hits > 0 |
|---|---|---|---|
| z-ai/glm-5.3-flash | 29 | 29 | **27** |
| openai/gpt-5.6-luna | 9 | 9 | **0** |
| deepseek/deepseek-v4.1-flash | 13 | 13 | 10 |
| qwen/qwen3.8-flash | 4 | 4 | 2 |
| qwen/qwen3.7-flash | 2 | 2 | 1 |
| **meta/muse-spark-1.3** | 8 | 8 | **0** |

The longer tick is what moved the cache: `z-ai/glm-5.3-flash` went from 3 hits in 24 to 27 in 29,
because a 600 s tick keeps the same assemblies re-entering the same stable prefix instead of
spreading calls thinly. The two models that never hit are `openai/gpt-5.6-luna` (consistent with
recheck2) and the new `meta/muse-spark-1.3`.

The discount is real and visible in the metered figure: the one `qwen/qwen3.8-flash` call with
16,896 cached tokens was billed 4,772 µUSD against a list-price 7,035 µUSD — cached input priced at
about a tenth. Calls with no cached tokens are billed at the manifest's list price to the µUSD.

### The Muse Spark seat

| | value |
|---|---|
| invocations | 8 of 65 (12.3% of calls) |
| **share of total spend** | **204,251 / 350,144 = 58.3%** |
| **cost per call** | **25,531 µUSD = $0.02553** |
| input tokens | 145,457 total, mean 18,182 (min 17,258, max 18,636) |
| output tokens | 5,277 total, mean 660 (min 401, max 878), of which 3,865 reasoning |
| `cached_tokens` | **reported on all 8 calls, zero on all 8** |
| ok / malformed / failed | **8 / 0 / 0**, every `finish_reason` `stop` |
| headroom | largest completion 878 of `max_tokens` 1500 |

Eight calls — one seat out of nine, 12% of the traffic — are 58% of the bill. It is not the prompt:
`eval-b`'s inputs are the *tightest* in the run (a 1,378-token spread, narrower than any other
assembly). It is the price sheet, $1.25/$4.25 per Mtok against a roster median of $0.15/$0.50, plus
a doubled output length (660 mean against 320 run-wide) because the model actually thinks: 3,865
reasoning tokens, where `deepseek-v4.1-flash` and `qwen3.7-flash` emit none. On quality the seat
paid: 8/8 well-formed, no truncation, nothing malformed.

### $/day and how long $90 lasts

At a 600 s tick a day is **144 ticks**. This run's cost per tick is 50,020.6 µUSD.

| Basis | Calls/day | Cost/day | Days a $90 wallet lasts |
|---|---|---|---|
| **144 ticks/day × this run's cost per tick (with Muse Spark)** | 1,337 | **$7.20** | **12.5** |
| straight wall-clock extrapolation ($0.350144 / 3,600 s) | — | $8.40 | 10.7 |
| 144 ticks/day, `eval-b` back on `qwen/qwen3.8-flash` at recheck2's per-call cost | 1,337 | **$3.47** | **25.9** |

The straight wall-clock line is the pessimistic one: the run's uptime spans six intervals but seven
tick cascades, so it books an eighth of a tick too much. The 144-tick line is the like-for-like
figure.

**Plainly, on the swap.** recheck2's `eval-b` on `qwen/qwen3.8-flash` cost 14,256 µUSD over 5
billed calls = 2,851 µUSD per call. Substituting that price for this run's eight `eval-b` calls
gives 168,703 µUSD per 7 ticks, 24,100 µUSD per tick, **$3.47/day** — the wallet lasts about 26
days instead of 12.5. Using recheck2's run-wide per-call cost ($0.003500) instead gives $3.58/day;
using its per-invocation `eval-b` cost (14,256/6) gives $3.39/day. Every basis lands between $3.39
and $3.58. **The Muse Spark seat roughly doubles the daily bill: about $3.50/day without it, $7.20
with it, on the same traffic.**

Two further readings worth keeping. This run's cost per call excluding `eval-b` is **$0.002560**,
27% *below* recheck2's run-wide $0.003500 — the longer tick's cache hits paid for themselves. And
at $7.20/day the launch plan's "roughly $12 to $15 a day" is now comfortably over-budgeted rather
than under.

---

## 3. Well-formed: 65 of 65

| | recheck2 | this run |
|---|---|---|
| ok | 59 | **65** |
| malformed | 1 | **0** |
| failed | 3 | **0** |
| **well-formed rate** | 0.9365 | **65/65 = 1.0000** |

`invocation_status` is `{"ok": 65}` and `stop_reasons` is `{"stop": 65}`; `finish_reason` is `stop`
on all 65. **Zero `length` stops** (recheck2 also zero; `recheck.md` had 4 of 19). **Zero
`provider.fault` items** — no item of any kind in the diary carries `fault` or `error` in its name.
No `OpenRouterError` output, so no call failed to reach a provider. The tightest completion headroom
on any call was 384 tokens (`antagonist-a`, 1,826 output against `max_tokens` 2,500).

The charter's `well_formed_rate` card is a "at least 0.9" region; the one closed price window
observed **1.0**. The `cost_per_return` card (at most 2,500 µUSD) observed **1,751.6** — note this
card measures *producer* returns only, so the `eval-b` seat's price does not enter it; the run's
producer seats cost 1,796 µUSD per call on average, and `eval-b`'s 25,531 is invisible to that card.
That is worth knowing before the card is trusted as a cost alarm.

---

## 4. Events per tick

Six world events per tick in all seven ticks, exactly as expected:

| tick | UTC | Tick | MarketMid | Funding | diary events | invocations | cost µUSD |
|---|---|---|---|---|---|---|---|
| 1 | 19:40:38 | 1 | 3 | 2 | 26 | 6 | 48,066 |
| 2 | 19:50:38 | 1 | 3 | 2 | 44 | 12 | 60,955 |
| 3 | 20:00:38 | 1 | 3 | 2 (+2) | 44 | 13 | 75,437 |
| 4 | 20:10:38 | 1 | 3 | 2 | 28 | 9 | 76,193 |
| 5 | 20:20:38 | 1 | 3 | 2 | 29 | 10 | 38,494 |
| 6 | 20:30:38 | 1 | 3 | 2 | 24 | 5 | 31,207 |
| 7 | 20:40:38 | 1 | 3 | 2 | 31 | 10 | 19,792 |

`MarketMid` coins are `BTC`, `ETH`, `PURR/USDC` — 7 each, the manifest's two perpetuals and the one
testnet spot pair, no venue-wide listing leak. `Funding` is `BTC`, `ETH` on every tick.

**One funding-payment excursion.** Two extra `Funding` events (`ev-77`, `ev-78`) arrived inside tick
3 with their own exchange timestamp 20:00:00.100 and non-zero `paid_usd`: BTC `0.000694` paid, ETH
`-0.02553` received. Net +24,836 µUSD to the wallet, which closes the conservation arithmetic
exactly: 90,000,000 − 350,144 + 24,836 = **89,674,692**, the reported `wallet_balance_micro`.
`wallet_conservation: true`, `ledger_verify: true`.

**Was any market registered? No.** `registrations_accepted: 1`, and the single `Registered` event is
an assembly learner, not a market:

```
{"kind": "Registered", "payload": {"actions": ["hold","sell:BTC","buy:BTC"],
 "id": "antagonist-a", "kind": "learner", "learner": "blum_mansour"}}
```

`market_purchases 0.0`, `observations_registered 0`, `population_tools_registered 0`,
`tool_calls 0`, `tool_call_failures 0`. The 40 `registry.register` items are all launch seeds
(`provenance: "seed"` — models and tools), written before the first tick.

One price window closed (`reserve_windows: 2`, one `price.window` item at seq 3542, `window_end_event
198`), but its `values` are empty and `price_updates: 0`: the window produced observations, priced
no card. Sixty-one minutes still does not reach a priced card.

---

## 5. Safety

| Rail | Real money | Evidence |
|---|---|---|
| Real on-chain USDC (Base mainnet) | **$0.00** | no `treasury.intent`/`treasury.submitted` in the diary; `stats.transfer_intents: 0`; wake `compute.spend_by_rail_per_day.x402 = 0` |
| Venice credit (no top-up attempted) | **$0.00** | wake `.venice = 0`; both `treasury.venice_window` items `spent_micro: 0`; `venice_micro` 4,976,619, unchanged from recheck2 |
| Base Sepolia testnet USDC (the reserve) | $0.00 | wake `reserve.usdc_micro` 4,966,000, unchanged from recheck2 |
| Hyperliquid **testnet** USDC | no activity at all | `orders_placed 0`, `orders_rejected 0`, `fills 0`; venue `realized_to_date_micro` −17,250, unchanged from recheck2. Only the funding payment above moved testnet value |
| **OpenRouter prepaid credit** | **$0.353558** | key allowance 91.975946 → **91.622388** |

**Orders, fills, transfers, x402: zero.** The complete set of `io.call` names for the run is
read-only plus completions:

```
provider.affordable 266, exchange.account 121, exchange.instruments 82, provider.complete 65,
exchange.mids 44, exchange.fills 14, exchange.funding 7, exchange.funding_payments 7
```

No `venue.place_limit`, `venue.place_market`, `venue.cancel`, `venue.close`, `venue.set_leverage`
or `treasury.transfer` call was made; those names occur in the diary only inside the manifest's
tool catalogue in the `Launch` event and the two boundary snapshots.

**No mainnet, no Venice call.** `api.hyperliquid.xyz`, `mainnet` and `venice.ai` each occur
**exactly once** in the decrypted diary, all three inside `connectors.origin_denylist` in the
`Launch` event (seq 53) — a denylist, not a call. The manifest's `exchange.mainnet` is `false`.
The string `funded` does not occur anywhere in the diary.

**OpenRouter allowance.** Read read-only after the run with `OpenRouterProvider.balance_micro()`
(a `GET /key`; the key file itself was never read by this audit, only by `_load_dotenv()`):
**91.622388**. recheck2 closed at 91.975946 and no OpenRouter-billing run happened in between —
the `/tmp` ledger set shows nothing between 18:56:29Z (recheck2) and 19:40Z (this run), and the
`rehearsal.md` world used a scripted provider. The vendor therefore charged **$0.353558** against
the world's meter of **$0.350144**: the meter reads **1.0% low** this time, where recheck2 measured
it 1.6% high and `recheck.md` 1.5% high. The sign flip is worth a note but not an alarm at this
magnitude — per-call costs are provider-reported, and a handful of calls (two `deepseek` and two
`luna` calls checked) are billed above their manifest list price, which the meter records faithfully
but the reservation ceiling is priced from.

## Reproduction

- Ledger and released key: `/tmp/final-rehearsal.jsonl`, `/tmp/final-rehearsal.jsonl.key`.
- CLI summary JSON: the session scratchpad's `final-rehearsal.log`.
- Wake page and aggregation scripts: the session scratchpad (`bal.py` for the read-only key
  allowance, `dump*.py`, `an.py`, `safety.py`, `den.py`).
- No harness, no provider override, no scripted world, no tracked file modified, nothing committed.
