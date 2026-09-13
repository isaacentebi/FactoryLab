# Recheck 2: what PR #66 and the roster change did to the per-call bill

A second live measurement against the **shipped** `worlds/testnet.toml`, directly comparable to
`recheck.md`. No provider override, no manifest copy, no local patch.

Repository `/Users/isaacentebi/Desktop/FactoryLab` on `main` at `f59e7b0`
(`Merge pull request #66 from isaacentebi/fix/prompt-size`), working tree clean before and after;
the only file this audit writes is this one, untracked. The three key files sit at the repository
root and only the CLI's own `_load_dotenv()` read them. Hyperliquid **testnet** throughout, no
manifest named `funded`, no Venice top-up, no mainnet, no fund transfer, no bare `pytest`,
nothing committed. **Real on-chain USDC moved: $0.00.**

Manifest hash `9ad039b58e9c57f85b8b54ee7159bfa22d33a4de4f16a0e7b19ae3e69ba4e047`
(recheck.md measured `0c3d229a…`; both merges changed it).

| Run | Command | Ledger | Wall clock |
|---|---|---|---|
| 3 | `factorylab run --world testnet --duration 14m --ledger /tmp/recheck2.jsonl --kill-at-end` | `/tmp/recheck2.jsonl` | 18:41:41Z → 18:56:31Z (14m50s) |

Read back with `factorylab postmortem /tmp/recheck2.jsonl /tmp/recheck2.jsonl.key` and with
`factorylab.versioning.reader.read_diary`; the wake page came from
`factorylab wake --ledger /tmp/recheck2.jsonl`.

> **Tick timing in this run is contaminated.** A full `pytest` gate ran in this directory for the
> whole measurement window (load average 4.3–5.3 on a machine otherwise idle). The tick gaps below
> are reported for completeness only; **no tick-timing conclusion should be drawn from this run**,
> in either direction. Every token and money figure is unaffected — those are provider-reported
> counts, not wall-clock measurements.

**Summary line.**

```
"world": "testnet", "seed": 4, "live": true, "charter_edition": 1,
"terminated": true, "termination_reason": "explicit_kill:budget", "seal_key_released": true,
"wallet_balance_micro": 99779476, "wallet_conservation": true, "ledger_verify": true,
"exchange_equity_usd": "981.025753915", "outstanding_decisions": 1,
"stats": {"events": 226, "decisions": 93, "invocations": 63, "noops": 30,
          "reserve_windows": 1, "orders_placed": 0, "orders_rejected": 0, "fills": 0,
          "invocation_status": {"ok": 59, "malformed": 1, "failed": 3},
          "stop_reasons": {"stop": 60, "none": 3}}
```

---

## 1. The headline: input tokens fell 86%

| | recheck.md (run 2) | **this run** | change |
|---|---|---|---|
| mean input tokens / call | 159,846 | **21,825** | **−86.3%, 7.3× smaller** |
| median input tokens / call | — | 18,536 | |
| min / max input tokens | — | 16,346 / 47,661 | |
| mean output tokens / call | 354 | 343 | −3% |
| **cost / call** | **$0.0252** | **$0.003500** | **−86.1%, 7.2× cheaper** |
| well-formed rate | 176/202 = 0.871 | **59/63 = 0.937** | +0.066, now above the 0.9 region |
| calls / tick | 8.4 | 9.00 | |
| world events / tick | 6 | 6 | unchanged |

60 of the 63 invocations were billed (the 3 `failed` ones never reached a provider). Total input
1,309,508 tokens, total output 20,596, total metered **220,524 µUSD = $0.220524**.

The venue listing was the whole of it. `_traded_instruments` (`cortex/schematics.py:368-380`) now
filters the venue's instrument record to the markets in `trading_markets`, and the listing itself
is replaced by a one-sentence pointer in `world.venue_listing` — the docstring's own estimate,
"about 100k input tokens a call", matches the 138,021-token drop almost exactly once the second
change (the run-length difference in what else the block carries) is allowed for.

**Per assembly, whole run (63 invocations, 220,524 µUSD).**

| assembly | model | ok | malformed | failed | total | cost µUSD |
|---|---|---|---|---|---|---|
| antagonist-a | qwen/qwen3.8-flash | 4 | 0 | 2 | 6 | 12,436 |
| eval-a | z-ai/glm-5.3-flash | 10 | 0 | 0 | 10 | 26,398 |
| eval-b | qwen/qwen3.8-flash | 4 | 1 | 1 | 6 | 14,256 |
| eval-c | **deepseek/deepseek-v4.1-flash** | 6 | 0 | 0 | 6 | 27,603 |
| eval-d | openai/gpt-5.6-luna | 14 | 0 | 0 | 14 | 84,606 |
| meta-a | deepseek/deepseek-v4.1-flash | 5 | 0 | 0 | 5 | 14,418 |
| meta-b | qwen/qwen3.7-flash | 1 | 0 | 0 | 1 | 627 |
| seed-decider | deepseek/deepseek-v4.1-flash | 1 | 0 | 0 | 1 | 7,268 |
| seed-observer | z-ai/glm-5.3-flash | 14 | 0 | 0 | 14 | 32,912 |
| **total** | | **59** | **1** | **3** | **63** | **220,524** |

`tencent/hy3` is gone from the roster and from the diary; `eval-c` ran on
`deepseek/deepseek-v4.1-flash` for all 6 of its calls, all `ok`. In recheck.md `eval-c` on
`tencent/hy3` was 16 ok / 4 malformed.

**Input tokens per assembly (billed calls only).**

| assembly | model | billed | mean in | min | max | mean cached | cache hits |
|---|---|---|---|---|---|---|---|
| antagonist-a | qwen/qwen3.8-flash | 4 | 32,996 | 19,306 | 47,084 | 15,808 | 4/4 |
| eval-a | z-ai/glm-5.3-flash | 10 | 20,833 | 17,774 | 39,356 | 2,304 | 2/10 |
| eval-b | qwen/qwen3.8-flash | 5 | 26,440 | 19,685 | 47,661 | 10,598 | 3/5 |
| eval-c | deepseek/deepseek-v4.1-flash | 6 | 22,530 | 17,670 | 40,214 | 7,040 | 3/6 |
| eval-d | openai/gpt-5.6-luna | 14 | 22,825 | 17,262 | 39,724 | 0 | 0/14 |
| meta-a | deepseek/deepseek-v4.1-flash | 5 | 17,540 | 17,318 | 17,783 | 11,264 | 4/5 |
| meta-b | qwen/qwen3.7-flash | 1 | 20,165 | 20,165 | 20,165 | 0 | 0/1 |
| seed-decider | deepseek/deepseek-v4.1-flash | 1 | 36,712 | 36,712 | 36,712 | 14,080 | 1/1 |
| seed-observer | z-ai/glm-5.3-flash | 14 | 16,978 | 16,346 | 17,385 | 494 | 1/14 |

`seed-observer`'s 14 calls span 16,346–17,385 input tokens — a 1,039-token spread across the run,
which is the moving part of the block. That floor is the stable prefix, and it is the same size
every time.

---

## 2. `cached_tokens` is recorded, and the cache does hit

**The field is present on every call that reached a provider: 60 of 63.** The three absentees are
exactly the three `failed` invocations, whose `usage` is all-`None` because no request completed
(`outputs {"reason": "OpenRouterError"}`). The additive shape in `runtime/compute.py:783-785` — the
key is written only when the provider reports an `int` — behaved as designed.

| | value |
|---|---|
| calls with `cached_tokens` present | 60 / 63 (100% of billed calls) |
| calls with `cached_tokens` > 0 | **18 / 60 = 30.0%** |
| mean `cached_tokens` per billed call | 4,314 |
| max `cached_tokens` on one call | 17,664 |
| total cached tokens | 258,816 |
| **cached share of input tokens** | **258,816 / 1,309,508 = 19.76%** |

**Which models reported it, and which hit.**

| model | calls | reported `cached_tokens` | hits > 0 |
|---|---|---|---|
| z-ai/glm-5.3-flash | 24 | 24 | 3 |
| openai/gpt-5.6-luna | 14 | 14 | **0** |
| deepseek/deepseek-v4.1-flash | 12 | 12 | **8** |
| qwen/qwen3.8-flash | 9 | 9 | **7** |
| qwen/qwen3.7-flash | 1 | 1 | 0 |
| *(no provider — `failed`)* | 3 | 0 | 0 |

`cached_tokens` is **not** absent on every call. Every model that answered reported the field;
two of them, `openai/gpt-5.6-luna` (14 calls) and `qwen/qwen3.7-flash` (1 call), reported it as
zero every time. The two clear beneficiaries are `deepseek/deepseek-v4.1-flash` (8 of 12) and
`qwen/qwen3.8-flash` (7 of 9) — precisely the automatic-prefix-cache providers the change was
aimed at. `z-ai/glm-5.3-flash` hit on only 3 of 24, and 14 of those 24 are `seed-observer`, whose
calls are the cheapest in the run anyway.

The 19.76% cached share is a real but second-order saving. **It is not what moved the bill.** The
prompt getting 7.3× smaller is what moved the bill; caching is worth roughly a further fifth of
what remains, and only on two of the five models actually serving.

---

## 3. The rendered prompt is **not** recorded in the ledger

Asked for, and it is not there. An `invocation` item carries exactly:

```
assembly_id, role, handle, cost, status, stop_reason, served_by,
finish_reason, usage, outputs, ts   (+ seq, hash, prev_hash)
```

There is no `messages`, `system`, `prompt` or `prompt_text` field on any item of any kind, and the
`WORLD` header string (`"What holds for every call in this world…"`) appears nowhere in the
decrypted diary. The `io.call` items — 63 of them named `provider.complete`, matching the 63
invocations — record only `name` and an `input_hash`, never a body. **The wire shape therefore
cannot be confirmed from this run's ledger.** It is confirmed from source instead:

- `cortex/assembly.py:92` `build_model_request` returns `system=self.spec.system_prompt` and a
  single `{"role": "user", "content": req.prompt_text()}` — the docstring guarantees "no
  population-authored text ever reaches the system role".
- `cortex/request.py:189` `prompt_text` "opens with `stable_prefix()` and … nothing which moves
  between calls precedes that block".
- `cortex/request.py:171` `stable_prefix` renders `WORLD_HEADER` plus
  `json.dumps(stable, sort_keys=True, indent=2)` over `STABLE_WORLD_KEYS` — sorted keys, so
  byte-stable per assembly.
- `world/openrouter.py:116` puts `{"role": "system", "content": req.system}` first, then the
  messages, so the system message is the assembly's own prompt alone.

The token evidence is consistent with all four: a tight per-assembly input-token floor, and cache
hits on exactly the providers that key on an identical leading token sequence.

*If a future audit needs the wire shape from the diary itself, the ledger would have to record a
prompt digest — the `io.call` `input_hash` is the natural place, but it is not decomposed.*

---

## 4. Ticks and pace — reported, not concluded

| | recheck.md (run 2) | this run |
|---|---|---|
| ticks delivered | 24 | 7 |
| `stats.events` | 714 | 226 |
| decisions | 319 | 93 |
| invocations | 202 | 63 |
| noops | 117 | 30 |
| invocations per tick | 8.4 | 9.00 |
| tick gap mean / min / max | 179.4 s / 120.0 s / 493.1 s | 126.4 s / 120.0 s / 157.2 s |

Six of the seven gaps were 120.0–121.1 s; one was 157.2 s. **Draw no conclusion from this.** The
concurrent `pytest` gate loaded the CPU for the whole window, so this run can neither confirm nor
refute that the smaller prompt fixed the 179 s delivered tick. A clean-machine rerun is the only
way to settle the tick question.

The world-event structure is unchanged and still correct: every `Tick` is followed by three
`MarketMid` (`BTC`, `ETH`, `PURR/USDC`) and two `Funding` (`BTC`, `ETH`) — six world events per
tick, in all seven ticks, with no funding-payment excursion this time.

**No price window closed.** `reserve_windows: 1`, `price_updates: 0`, and there is no `price.window`
item in the diary: 14 minutes does not reach the 100-return card window, so this run produces no
T47 figure. The `well_formed_rate` **observation** over the whole run is 59/63 = **0.9365**, above
the charter's "at least 0.9" region, but it was never priced.

**The four non-`ok` returns.** Three `failed` (`antagonist-a` ×2, `eval-b` ×1) carry
`stop_reason "none"`, `cost 0` and `outputs {"reason": "OpenRouterError"}` — provider errors, not
model output, and they sit in the `well_formed_rate` denominator. The single `malformed` is
`eval-b` on `qwen/qwen3.8-flash` at seq 3218: `stop_reason stop`, complete parseable JSON that
failed its outcome schema. **Zero `length` stops in the whole run**, against 4 of 19 in recheck.md,
and **zero `provider.fault` items** — the reasoning-budget fault that recheck.md documented three
times did not recur. A prompt a seventh the size leaves the completion budget alone.

---

## Spend per rail

| Rail | Real money | Evidence |
|---|---|---|
| Real on-chain USDC (Base mainnet) | **$0.00** | no `treasury.intent`/`treasury.submitted` in the diary; `stats.transfer_intents: 0`; wake `compute.spend_by_rail_per_day.x402 = 0` |
| Venice credit (no top-up attempted) | **$0.00** | wake `.venice = 0`; `treasury.venice_window` `spent_micro: 0`; `venice_micro` 4,976,619 unchanged from recheck.md |
| Base Sepolia testnet USDC (the reserve) | $0.00 | wake `reserve.usdc_micro` 4,966,000, unchanged from recheck.md |
| Hyperliquid **testnet** USDC | no activity at all | `orders_placed 0`, `orders_rejected 0`, `fills 0`; venue `realized_to_date_micro` −17,250, unchanged from recheck.md |
| **OpenRouter prepaid credit** | **$0.217052** | key allowance 92.192998 → 91.975946 |

The world's meter read 220,524 µUSD against the vendor's 217,052 µUSD — the meter is 1.6% high,
the same rounding-up bias recheck.md measured at 1.5%.

**No mainnet, no Venice call, no transfer.** The strings `api.hyperliquid.xyz`, `mainnet` and
`venice.ai` each occur exactly once in the decrypted diary, all inside the manifest's
`connectors.origin_denylist` in the `Launch` event — a denylist, not a call. The complete set of
`io.call` names for the run is read-only plus completions:

```
exchange.account 121, exchange.instruments 84, exchange.mids 43, exchange.fills 14,
exchange.funding 7, exchange.funding_payments 7, provider.affordable 256, provider.complete 63
```

No order, cancel, close, leverage or transfer call was made.

## Daily cost at the 120 s tick

Cost per call **$0.0035004** (220,524 µUSD / 63).

| Basis | Calls/day | Cost/day | recheck.md |
|---|---|---|---|
| declared 120 s tick, 8.4 calls/tick (recheck's basis) | 6,048 | **$21.17** | $152 |
| declared 120 s tick, this run's 9.00 calls/tick | 6,480 | $22.68 | — |
| measured 126.4 s tick, 8.4 calls/tick *(contaminated)* | 5,742 | $20.10 | $102 |
| straight extrapolation of this run's wall clock ($0.2205 / 14m50s) | — | $21.41 | $102 |

**On the like-for-like basis — 8.4 calls per tick, 120 s tick — the bill falls from $152/day to
$21/day, a 7.2× reduction.** Against the measured-tick basis it falls from $102/day to about
$20–21/day.

The launch plan's "roughly $12 to $15 a day" is no longer an order of magnitude away; it is within
a factor of about 1.5. The remaining lever is no longer the venue listing. At ~21,800 input tokens
a call the block still dominates the bill, and the next largest reducible piece is whatever the
moving `INPUTS` section carries — note that the max observed call, 47,661 tokens, is 2.7× the
16,346-token floor, so the moving part still varies by ~30k tokens between calls.

## Reproduction

- Ledger and released key: `/tmp/recheck2.jsonl`, `/tmp/recheck2.jsonl.key`.
- Wake page and the aggregation scripts: the session scratchpad (`bal.py` for the read-only key
  allowance via `OpenRouterProvider.balance_micro()`, `an.py`, `t2.py`, `t3.py`, `t4.py`).
- No harness, no provider override, no scripted world, no tracked file modified, nothing committed.
