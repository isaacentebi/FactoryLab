# Live rehearsal on Hyperliquid testnet

Round-three fix plan, Step 3. Not an audit: an attempt to make each checklist line
true on a live testnet world and to paste the diary items that prove it.

Worktree `/Users/isaacentebi/Desktop/FactoryLab-rehearsal` at `3955daf`, clean.
The three key files (`openrouter.key`, `hyperliquid.key`, `reserve.key`) sit at the
repository root at mode 0600; nothing but the CLI's own `_load_dotenv()` read them.
Hyperliquid **testnet** throughout, no manifest named `funded`, no Venice top-up,
no `scripts/compute_proof.py`, no bare `pytest`, nothing committed.
**Real on-chain USDC moved: $0.00.** Paid actions, balances and per-rail spend are in
`docs/runs/rehearsal.md` (gitignored).

## The rehearsal world

`/tmp/fl-rh/rehearsal.toml` is `worlds/testnet.toml` with four changes:
`name = "rehearsal"`, `tick_interval = "30s"`, `evaluation.consequence_backstop_events = 10`,
and — a deviation from the brief — `novelty.window = "5m"` (from `1h`). The brief asked for
the first two so that activations land inside the run; they are not sufficient on their own.
`_activate_charter_if_due()` and `_close_price_window()` are only reached from
`_manage_reserve_window()`, which fires when the world clock has advanced one
`novelty.window`. With the shipped hour, the first window closes an hour into the run, the
first activation no earlier than the second hour, and the "next price window measures it"
line no earlier than the third. Manifest hash `23619f768af229b4aef521033620300012139cf69bd9c7d5bf66d158765a232c`.

**A scripted provider override was used for the rehearsal world.** The shipped OpenRouter
seeds cannot be made to register a connector, a market, an observation, an amendment and a
retirement, and to place three particular orders, inside a run of this length. Everything
except the model answers was real: the live Hyperliquid testnet exchange, the live treasury
rail, real HTTPS to `api.kraken.com`, the real macOS jail. The harness is
`tests/audit/test_rehearsal_drive.py`; its provider class is deliberately *not* a
`ScriptedProvider` subclass, so the runtime treated its calls as non-deterministic and
replayed them from the journal exactly as it would replay OpenRouter.

## The finding that shaped everything else

`LiveVenue.on_tick` emits one `MarketMid` world event per coin returned by
`HyperliquidExchange.mids()`, and `mids()` iterates
`(*self._listed_coins, *self._spot_names)` — every listed perpetual **and every
USDC-quoted spot pair on the venue**, not the manifest's `exchange.coins` and
`venue.spot_pairs`. It also emits one `Funding` event per perpetual, from
`meta_and_asset_ctxs()`, which is likewise the whole universe.

On Hyperliquid testnet today that is 212 perpetuals and 1,263 USDC-quoted spot pairs:

> **one tick delivers ≈ 1,687 world events**, each routed to a producer, judged by an
> evaluator, assessed by a meta, metered, and written to the diary.

World A ran 1,431 events across 33 minutes of wall clock and four resumes and **never
finished its first tick**: its world clock stayed at the launch timestamp
`1789302921721656000` throughout. The shipped testnet world behaves the same way — the
`--events 3` run in line 1 below was still inside tick 1 after 17 minutes.

Two consequences worth the fix plan's attention:

- **Cost.** One tick is ~1,687 producer decisions and ~2,500 further judge and meta
  invocations. The decision to "launch at a two-minute tick … compute is roughly $12 to
  $15 a day" assumed something like 30 events per tick. The measured OpenRouter spend for
  the shipped manifest was $1.35 for a run that did not complete one tick.
- **Diary size.** 622,719,871 bytes for 1,431 events — about 435 KiB per event. Every
  invocation record embeds the whole world block, which itself re-reads
  `exchange.instruments()` and `exchange.account()` per invocation.

## The checklist

### 1. `factorylab run --world testnet --events 3` constructs from the shipped manifest, all three keys present, no local patch — **worked differently**

Construction is clean. `git status` was empty before and after; the run loaded all three
keys through `_load_dotenv()` and launched against the live venue. The world's own wake
names the shipped manifest:

```
"world": "testnet",
"manifest_hash": "0c3d229a734b8338f96416ec14b8f77a250491f39b3f1ea9637249333538fd63",
"venue": {"equity_micro": 979805478, "realized_to_date_micro": -17250},
"reserve": {"usdc_micro": 4966000, "venice_micro": 4976619}
```

`factorylab manifest --world /tmp/fl-rh/rehearsal.toml` also returned
`"venue_validation": {"status": "valid", "missing_coins": [], "missing_spot_pairs": []}`,
so the shipped spot pair resolves against live testnet metadata.

What did not work is *finishing*. Two attempts were made. The first was stopped by a
15-minute timeout, the second was stopped by hand after 17 minutes; neither reached its
third event, for the reason above. The run is not wedged — it is doing ~1,700 events of
work per tick.

### 2. A perp fill, a spot fill (PURR/USDC) and a `perps_to_spot` class transfer reaching `treasury.confirmed`, with `pots()` complete — **worked as told**

Both orders reached the venue and were acknowledged:

```
[1935] order.intent {"args": {"coin": "BTC", "market": "perp", "side": "buy", "size": "0.0002"},
       "client_id": "decision-7", "operation": "venue.place_market", "result": {"status": "uncertain"}}
[1941] consequence.acknowledged {"client_id": "decision-7"}
[2049] order.intent {"args": {"coin": "PURR/USDC", "market": "spot", "side": "buy", "size": "4"},
       "client_id": "decision-12:tool:0", "operation": "venue.place_market", "result": {"status": "uncertain"}}
[2055] consequence.acknowledged {"client_id": "decision-12:tool:0"}
```

The spot fill is itemised, and the lot book took it:

```
[21957] consequence.fill_cursor {"seen": [[[1789302962558000000, "60023419540", "PURR/USDC", true,
        "4.0", "4.6252", "0.01295056", "0.0", false, "spot", "3.9972"], 1]], ...}
[21961] spot.inventory {"coin": "PURR/USDC", "entry_px": "4.608443333333333333333333333",
        "order_id": "60023419540", "size": "14.9895"}
```

(the launch seeded 10.9923 PURR at 4.60235 through `consequence.spot_seed`, so 14.9895 is
that plus the 3.9972 net fill). `stats.fills = 2` and window 1's `fills` observation is
`2.0`, so the perp fill was counted too.

The class transfer:

```
[2091]  treasury.intent {"direction": "perps_to_spot", "usd": "30", "by": "seed-observer"}
[2100]  treasury.submitted {... "nonce": 1789302921721, "reference": {"action": {"amount": "30.000000",
        "nonce": 1789302921721, "toPerp": false, "type": "usdClassTransfer"}} ...}
[21979] treasury.confirmed {... "principal_moved": true, "receipts": [{"delta": {"toPerp": false,
        "type": "accountClassTransfer", "usdc": "30.0"},
        "hash": "0x7504de8ce587eb6d767e0429332bbb010900f672808b0a3f18cd89dfa48bc558", ...}] ...}
```

and `pots()` is complete in the wake:

```
"pots": {"current": {"complete": true, "reserve": 11000000, "seed": 0,
                     "venice": null, "venue": 979801135},
         "transfers": [{"status": "submitted", "direction": "perps_to_spot", "amount_micro": 30000000},
                       {"status": "confirmed", "direction": "perps_to_spot", "amount_micro": 30000000}]}
```

One evidence gap worth a look: `stats.fills` and the window observation both read 2, and the
fill cursor carries the spot row, but **no `event:Fill` item appears anywhere in the diary**.
A full-file `factorylab postmortem --kinds event:Fill` over all 40,651 items returned nothing,
while `event:Terminated` from the same pass matched. Fills are settled and counted but the
published event does not survive into the ledger under that kind.

### 3. An amendment passes and activates at the published earliest event; a retirement passes and the retired assembly stops being routed — **worked as told**

Retirement, proposed by `decision-30`, voted, deferred with its published earliest, then
activated:

```
[2560]  retirement.tally {"outcome": "passed", "proposal_id": "retire:decision-30:1"}
[2561]  charter.approved {"amendment_id": "retire:decision-30:1"}
[2562]  charter.deferred {"amendment_id": "retire:decision-30:1", "earliest_event": 31,
        "earliest_ns": 1789303821721656000, "window": 1}
[22037] assembly.retired {"assembly_id": "eval-c", "proposal_id": "retire:decision-30:1", "version": 1}
[22039] actor.retire {"actor": "router:ProducerReturn"}
[22041] charter.cadence {"amendment_id": "retire:decision-30:1", "activation_event": 746,
        "earliest_ns": 1789303821721656000, "slowest_period_events": 10, "outstanding_forecasts": 0}
```

Amendment `rehearsal-fill-card`, the same shape:

```
[22537] charter.approved {"amendment_id": "rehearsal-fill-card"}
[22538] charter.deferred {"amendment_id": "rehearsal-fill-card", "earliest_event": 776,
        "earliest_ns": 1789305805503854000, "window": 2}
[32703] charter.activate {"amendment_id": "rehearsal-fill-card", "edition": 2, "round": 1}
[32707] charter.cadence {"activation_event": 1144, "earliest_ns": 1789305805503854000,
        "previous_activation_event": 746, "slowest_period_events": 10}
```

Both activated at the first reserve-window boundary at or after their published
`earliest_ns`/`earliest_event`, and the two activations did not chain (events 746 and 1144).
The retired seat left the router: the final summary's ProducerReturn router is at epoch 2
with universe `["eval-a", "eval-b", "eval-d", "NOOP"]` — `eval-c` is gone, and
`invocations_by_assembly` names no `eval-c` at all.

A caveat about this run rather than about retirement: the ProducerReturn router drew `NOOP`
almost every time (`noops: 600`, `verdicts: 0`, `censored: 1153`, and
`invocations_by_assembly` lists only `antagonist-a` 238, `seed-observer` 614 and
`rehearsal-child-judge` 3). So no evaluator was sampled in this run, retired or not, and the
retirement's effect on routing is evidenced by the router's universe and epoch rather than by
a fall in eval-c's traffic.

### 4. A registered observation is named by a card; the card passes; the next price window measures it — **worked as told**

```
[22469] observation.preflight {"observation": "rehearsal-fill-count", "window": 1,
        "value": 2.0, "error": null, "handle": "decision-894"}
[32708] price.region {"card_id": "rehearsal_fills", "edition": 2,
        "region": {"hi": 1000.0, "kind": "max", "lo": null, "scale": 1000.0}}
[36998] price.window {"charter_edition": 2, "window": 3, "window_end_event": 1297,
        "observations": {..., "rehearsal-fill-count": 0.0, ...},
        "regions": {..., "rehearsal_fills": {"card_id": "rehearsal_fills", "hi": 1000.0,
                    "kind": "max", "lo": null, "scale": 1000.0}, ...},
        "values": {"cost_per_return": 67.34693877551021, "rehearsal_fills": 0.0,
                   "well_formed_rate": 0.99}}
```

The card `rehearsal_fills` names the population's own `rehearsal-fill-count`
(`def observe(facts): return facts['fills']`), passed a committee, activated as edition 2,
and the **next** price window carries both the raw observation and the card's value.

One thing the population is not told: an observation is registrable only once a window has
closed (`no closed window to preflight the observation against`), and nothing in the world
block says when that will be. The rehearsal's first eight proposals were refused for that
reason alone; I reproduced it offline in `tests/audit/test_rehearsal_probe.py`
(120 scripted events → `card_samples.windows: 0` → refusal; 400 events → 1 closed window →
registration accepted).

### 5. A connector whose root answers 404 (Kraken) is registered by vote with a `preflight_path` and fetched; a numeric body does not malform later returns — **worked differently**

`https://api.kraken.com/` answers 404 and `/0/public/Time` answers 200 (both checked with
curl before the run). The first ballot could not seat anybody:

```
[2288] connector.tally {"passed": false, "seats": 0, "yes": 0, "vote_id": "connector:kraken:v1:decision-20"}
[2289] connector.refused {"handle": "decision-20", "reason": "connector sortition vote did not reach a majority"}
```

The second, once assemblies had settled consequences, passed:

```
[3702] connector.tally {"passed": true, "seats": 1, "yes": 1, "vote_id": "connector:kraken:v1:decision-79"}
[3705] connector.registered {"id": "kraken", "origin": "https://api.kraken.com",
       "preflight_path": "/0/public/Time", "version": 1, "max_call_micro": 0, "pay": null,
       "predicted_effect": {"card_id": "forecast_skill", "direction": "increase", "window": 1}}
```

and the priced preflight and fetches all answered:

```
[2285] connector.call {"id": "kraken", "path": "/0/public/Time", "status": 200, "bytes": 87, "cost": 1000}
[3680] connector.call {... status 200, bytes 87, cost 1000}
[3738] connector.call {... status 200, bytes 87, cost 1000}
[3749] tool.call {"tool": "kraken-parse", "args": "[connector continuation]", "cost": 50, "ok": true}
```

So: registered by vote with a `preflight_path` against a 404 root, and fetched. The second
half was **not** demonstrated, through a fault in my harness rather than the factory. The
87-byte body is above `MIN_PROTECTED_BODY_CHARS`, so it is protected and the jailed
`kraken-parse` tool extracted the number correctly — but my provider scanned the
continuation's `seen_tool_results` in order, matched the `connector.fetch` row again and
re-issued the same tool call. The kernel refused the extra round rather than mangling
anything:

```
[3755] tool.calls_ignored {"handle": "decision-81", "reason": "continuation already consumed"}
```

and the decision's return stayed well-formed (that window's `well_formed_rate` is 0.995).
The number therefore never reached a final return. Worth re-running with a fixed harness;
nothing here suggests the factory would malform it.

### 6. A ballot invocation that tries `venue.place_market` is refused; a child request naming another assembly's return as its subject is refused — **worked as told**

```
[2546] tool.refused {"assembly_id": "antagonist-a", "handle": "decision-31", "tool": "venue.place_market",
       "reason": "venue and treasury writes require a producing return kind and an open consequence
                  account; judging decisions and their children cannot write"}
[2547] tool.call {"tool": "venue.place_market", "args": "{\"coin\": \"BTC\", \"side\": \"buy\",
       \"size\": \"0.0002\"}", "ok": false, "outcome": "failed"}
```

The ballot voted yes and its vote was counted; only the write was refused, publicly.

```
[24567] return.refused {"handle": "decision-987", "about_handle": "decision-961",
        "reason": "a requested judgement may only address the requesting decision or its ancestors;
                   judging anyone else's return is the router's"}
```

`decision-961` is a live, addressable return belonging to another assembly, obtained from an
earlier prompt's `your_recent_returns`. An earlier attempt that named a handle which does not
exist produced the other, correct refusal — `judgement needs an addressable return handle`
(seqs 2489, 21891) — so the two paths are distinguishable in the diary.

### 7. A market is registered (a venue-listed coin not in the seed) and an order is placed on it; a note is written, priced, and read back after a resume — **worked differently**

The market registered:

```
[2209] market.registered {"coin": "SOL", "market": "perp", "handle": "decision-17"}
```

and the world block's `trading_markets.perp` then carried SOL, which is how the harness knew
to order. The order was **not** placed: `stats.orders_placed = 2, orders_rejected = 1` with
`order.intent` items for BTC and PURR only. The SOL order (0.2 SOL, ~$19.9 notional) was
refused by `_order_exclusion` before submission — the $100 world wallet was already carrying
the BTC exposure, the seeded spot inventory and the protected novelty reserve. That is the
kernel working, but note that **a rejected order leaves no ledger item at all**: only
`stats.orders_rejected` moves, so the population is told nothing it can read back and an
operator cannot see from the diary which order was refused or why.

The note half worked as told, including the read-back after a real resume:

```
[2160]  note.put {"assembly_id": "antagonist-a", "key": "rehearsal-plan", "bytes": 67, "cost": 67,
        "version": 1, "window": 1, "text": "Rehearsal note written before the process was killed."}
[21954] resume.begin {"n": 745, "now_ns": 1789304905503854000}
[22031] note.rent {"key": "rehearsal-plan", "cost": 67, "window": 2}
[22069] note.get {"assembly_id": "antagonist-a", "key": "rehearsal-plan", "bytes": 67, "cost": 67,
        "version": 1, "window": 2}
```

with further `note.rent` charges at windows 3 and 4. Written, priced per byte-window, and
read back after the process was killed and resumed.

### 8. Kill the process inside a population tool run (SIGKILL); `factorylab resume` comes back with the same summary fields; then `factorylab kill` releases the seal and the diary decrypts — **worked differently**

Five SIGKILLs, each fired only once a jailed tool was confirmed running:

```
SIGKILL 25480 during a jailed population tool run at 2026-09-13T13:07:37Z
  31494 .../python3.13 -I -S -B /private/var/folders/.../factorylab-sbx-6ne7gtrj/runner.py
SIGKILL 31516 ... 13:26:21Z   factorylab-sbx-4by4r8ho
SIGKILL 33408 ... 13:33:53Z   factorylab-sbx-34jiox_1
SIGKILL 34261 ... 13:40:23Z   factorylab-sbx-rice96bw
SIGKILL 34834 ... 13:53:40Z   factorylab-sbx-uygq_w57   (the fake twin)
```

The live world came back four times through `resume_world`, the function
`factorylab resume` calls (`resume.begin` at seqs 21954, 32641, 36971, 40631;
`stats.resumes = 4`), each time with the same summary fields —
`world "rehearsal"`, `manifest_hash 23619f76…`, `seed 4`, `live true` — and with
`wallet_conservation true` and `ledger_verify true`.

Two problems on the CLI path:

- **`ledger_busy` after a jail kill.** The first `factorylab resume` returned
  `factorylab resume: ledger_busy` (exit 4). The jailed child survives a SIGKILL of its
  parent on macOS — `bwrap --die-with-parent` is the Linux path; `sandbox-exec` has no
  equivalent — and it holds the inherited ledger writer lock. `pkill -9 -f factorylab-sbx`
  released it. On a supervised host this would look like a world that cannot be restarted.
- **`replay_diverged` when the provider changes.** With the lock free,
  `factorylab resume --world fakerh --ledger …` returned
  `factorylab resume: replay_diverged` (exit 1), twice. A resume of a byte-copy of the same
  ledger through the same code path, with the provider that wrote the journal, returned a
  full summary (`wallet_conservation true`, `ledger_verify true`, `charter_edition 1`).
  The CLI rebuilds the provider from the manifest, so this is the seam between my scripted
  override and the manifest's OpenRouter provider, not a defect a CLI-only world would hit —
  but it is why no CLI-printed resume summary appears in this report.

The operator control worked exactly as documented:

```
$ factorylab kill --world rehearsal --ledger /tmp/fl-rh/rehearsal.ledger
{"world": "rehearsal", "terminated": true, "termination_reason": "explicit_kill:operator",
 "seal_key_released": true}
exit 3
[40651] event:Terminated {"reason": "explicit_kill:operator"}
```

and the diary decrypts: every quotation in this report is `factorylab postmortem` output
against the released key.

### 9. `factorylab wake` shows the world's own venue, no reserve section on a world without one, no positions — **worked as told**

World A (live venue, reserve configured):

```
"venue": {"equity_micro": 979842135, "realized_to_date_micro": -17250},
"reserve": {"usdc_micro": 4966000, "venice_micro": 4976619},
"portfolio": {"equity_micro": 979801135, "realized_to_date_micro": 0}
```

`portfolio` is equity and realised P&L only — no coin, no side, no size, no entry price —
while the world held a BTC perpetual and 14.9895 PURR. The `norsv` world is the same
manifest with `treasury.reserve_address` deleted:

```
"world": "norsv",
"venue": {"equity_micro": 979809758, "realized_to_date_micro": -17250},
"reserve": {"usdc_micro": "unavailable", "venice_micro": "unavailable"}
```

the venue is published, the reserve carries no figures. One operational caveat:
`collect_wake` matches the ledger's genesis hash against the manifests **installed in
`worlds/`**, so a world launched from a manifest path outside that directory cannot be woken
at all — `factorylab wake` prints `wake_unavailable` and writes a page of `"unavailable"`.
I had to copy the rehearsal manifests into `worlds/` to publish their pages; they are not
committed and have been removed again.

### 10. `wallet_conservation` and `ledger_verify` true; total spend per rail — **worked as told**

World A restored after the last SIGKILL and read without advancing:

```
"world": "rehearsal",
"manifest_hash": "23619f768af229b4aef521033620300012139cf69bd9c7d5bf66d158765a232c",
"seed": 4, "live": true, "charter_edition": 2,
"wallet_balance_micro": 99947595,
"wallet_conservation": true,
"ledger_verify": true,
"exchange_equity_usd": "979.840335920",
"stats": {"resumes": 4, "events": 1431, "fills": 2, "reserve_windows": 4,
          "amendments_proposed": 1, "amendments_passed": 1, "amendments_activated": 1,
          "observations_registered": 1, "population_tools_registered": 2,
          "orders_placed": 2, "orders_rejected": 1, "transfer_intents": 1, "votes_cast": 3}
```

`ledger_verify` walked the world's own 622,719,871-byte hash chain.

| Rail | Real money |
|---|---|
| Real on-chain USDC (Base mainnet) | **$0.00** |
| Venice credit (no top-up attempted) | $0.00 |
| Base Sepolia testnet USDC (the reserve) | $0.00 — 11.000000 before and after |
| Hyperliquid testnet USDC | testnet only: $30.00 moved perps→spot, ~$15.3 + ~$18.5 of orders, $0.017250 of fees |
| OpenRouter prepaid credit | $1.35 measured for the shipped-manifest run, plus an earlier aborted run of similar length whose ledger was deleted (est. $1.3–1.5): **≈ $2.7–2.9 total** |

The rehearsal world's own metered compute (53,524 micro-USD) is notional: its provider was
scripted, so no provider call was billed. Full per-action detail is in
`docs/runs/rehearsal.md`.

## Other things the run showed

- **Committee size.** `committee.seats = 5`, but the connector ballot that passed seated
  **one**: `{"passed": true, "seats": 1, "yes": 1}`. Eligibility (`committee.min_settled = 5`
  distinct router-chosen decisions with settled consequences) takes long enough that early
  governance is decided by whoever qualifies first. A one-seat majority is a majority.
- **Launch spot seeding works.** Non-USDC holdings already in the account were seeded as
  unowned lots at the launch mark: `consequence.spot_seed {"coin": "PURR/USDC", "size":
  "10.9923", "entry_px": "4.60235"}` and the same for `HYPE/USDC` 0.19973 @ 33.612.
- **The class transfer blocks venue writes while pending**, as documented
  (`_venue_write` returns `class transfer awaiting receipt`); nothing was lost, and the
  transfer confirmed on the next reconciliation after a resume.
- **`novelty.window` is the governance clock on a live world.** Because the world clock only
  advances when a tick is delivered (internal events carry the current clock value) and
  because a resume sets the clock to wall time, the four window closes in this run were
  driven by the four resumes, not by ticks. That is correct behaviour, but it means a world
  whose ticks are as expensive as this one's governs itself almost never.

## Reproduction

- Harness: `tests/audit/test_rehearsal_drive.py` (untracked, left in place).
  `FL_MANIFEST`, `FL_LEDGER`, `FL_EVENTS`, `FL_DURATION`, `FL_STATE`, `FL_MODE`
  (`run` | `resume` | `summary`), `FL_SLOWTOOL` (flag-file path), `FL_PASSIVE`.
- Offline reproduction of the closed-window precondition:
  `tests/audit/test_rehearsal_probe.py`.
- Manifests, ledgers, released keys, wake pages and the extracted diary items:
  `/tmp/fl-rh/`.
