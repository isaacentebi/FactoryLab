# Seat 6 — wiring, by running

Audited commit `86b391c`, worktree `FactoryLab-a3-seat6`. Everything below was produced by
running the shipped commands against Hyperliquid **testnet**, real OpenRouter and Venice
credit, and Base mainnet USDC for one x402 purchase. Receipts, commands, balances and
per-rail spend are in `docs/runs/audit3-wiring.md` (gitignored; written anyway).

**Local patches**, permitted by my row, never committed — two lines in `worlds/testnet.toml`:

```diff
-reserve_address = "0x1111111111111111111111111111111111111111"
+reserve_address = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
-spot_pairs = ["BTC/USDC", "ETH/USDC"]
+spot_pairs = ["BTC/USDC"]
```

Without both, `uv run factorylab run --world testnet` exits 1 and no world exists. They are
findings 1 and 2.

**Caveat.** `hyperliquid.key` and `reserve.key` are shared with seat 5, which was using the
same testnet account and the same mainnet reserve concurrently. Every figure below comes from
my own call's receipt, never from a balance difference.

---

## Blockers

### 1. The world about to launch does not launch: its spot pairs do not exist — *broken*

`worlds/testnet.toml:208`; `factorylab/world/exchange.py:757`.

`venue.spot_pairs = ["BTC/USDC", "ETH/USDC"]`. Hyperliquid testnet spot metadata has no
`ETH/USDC` (the testnet token is `UETH`). `HyperliquidExchange._configure_spot` raises
`ValueError: spot pairs unavailable in venue metadata: ['ETH/USDC']` during `Runtime.__init__`,
before genesis. Repro: `uv run factorylab run --world testnet --duration 3m` → exit 1,
`factorylab run: adapter_unavailable`. `uv run factorylab manifest --world testnet` passes,
so nothing catches this until launch.

Worse forward: on **mainnet** neither `BTC/USDC` nor `ETH/USDC` exists either (the pairs are
`UBTC/USDC` and `UETH/USDC`), so copying this key into `funded.toml` fails the same way at the
one moment that cannot be retried. Verified against both nets' `spot_meta()`.

Fix: set testnet to `["BTC/USDC"]` and mainnet to the `U*` names, and make
`factorylab manifest` check `venue.spot_pairs` against live metadata so the failure is offline.

### 2. The world about to launch does not launch: the reserve address is a placeholder — *broken*

`worlds/testnet.toml:201`; `factorylab/world/treasury_rails.py:90`.

`reserve_address = "0x1111…"` with the comment "Rehearsal placeholder: tests inject the
matching reserve". With `reserve.key` at the repository root — where the README puts it —
`LiveRail.__init__` raises `RailError: reserve key does not match treasury.reserve_address`
and the world never starts. The shipped testnet world is runnable only by tests, not by the
documented command, which contradicts README's "It has run on testnet with real fills."

Compounding (*unclean*): `factorylab/runtime/cli.py:684-686` maps every launch exception to
`adapter_unavailable`, so neither cause is visible to the operator. I had to call `run_world`
from Python to see either. Printing the exception **class** would leak nothing.

Fix: omit `reserve_address` (the code then falls back to `UnconfiguredRail` and class
transfers still work) or carry the real address; and let the refusal name the failing subsystem.

### 3. A card can never name an observation the population registered — *broken*

`factorylab/charter/book.py:72`; `factorylab/charter/measurement.py:122,130`.

A11's whole promise is "a card may then name it". `CharterBook.validate` calls
`preflight_card(card)` with **no observation book**, so `preflight_card` falls back to
`seed_book()` and raises "card `<id>` observation: unregistered observation" for every card
naming a population-registered observation — after `governance.py:476-477` has already
preflighted the same card successfully against the live book.

Repro (`docs/runs/audit3-wiring.md`, obs2/obs4): run the scripted world 200 events, then push
`{"kind":"observation","id":"noop-count","unit":"count","range":[0,1000],"code":"def observe(facts): return float(facts.get('noop_returns',0))"}`
through `_apply_registrations`. The jail preflight returns 616.0 and `observation:noop-count`
v1 registers. Then propose an amendment adding a card with `"observation":"noop-count"` →
`registration.rejected`, traceback ending at `book.py:72`.

So the population may register a measurement and may never price one. The essay's
"continuously add holdout test criteria to a given charter" (II.IV.a) is not reachable.

Fix: `def validate(self, amendment, observations=None)` and pass `self.observations` from
`_propose_amendment`, exactly as `preflight_measurement` already does.

### 4. A class transfer settles on chain and is never confirmed — *broken*

`factorylab/world/treasury.py:612`.

`class_poll` requires `row.get("time") == ref["nonce"]`. Hyperliquid stamps the
`accountClassTransfer` ledger row with the block time. Measured: nonce `1789276841701`, row
time `1789276843536` — 1.835 s later. Exact equality can never match.

Repro: `Treasury.transfer("perps_to_spot", "20")` twice against testnet. Spot USDC moved
$0.9519 → $40.9519 (both landed: hashes `0x9f8b7bfa…`, `0xb5ac7498…`), while both journals sat
at `status: "submitted"` through twelve `reconcile` calls over 60 s, and `pots()` stayed
`pending: true, complete: false`.

`docs/manifest.md` states a pending transfer prevents another. So the population gets exactly
one class transfer, after which `treasury.transfer` is blocked in **every** direction for the
world's life, the wallet's pot view never completes again, and the spot leg — only reachable
through a class transfer — is one-shot.

Fix: match on `delta.type`, `toPerp` and amount within a bounded window,
`nonce <= row["time"] <= nonce + tolerance_ms`, and pin the matched hash.

---

## Serious

### 5. Governance cannot act for 600 world events, then only once per 600 — *broken*

`factorylab/runtime/cadence.py:104` (`earliest_event`), `:110` (`earliest_ns`).

`earliest = last_activation + min_ratio × slowest_period`, with `slowest_period` floored at
`evaluation.consequence_backstop_events` (200) and `min_ratio` 3. Measured on
`--world scripted --seed 1`: the `turnover` amendment passes 5-0 at event 20 and activates at
event **601**; the `eval-a` retirement passes at event 34 and activates at event **1201**
(`charter.cadence activation_ns 1201000000000`). At `--events 500` — the README's own command —
neither activates: `charter.deferred earliest_event: 601` repeats every window and `eval-a` is
still in `router:ProducerReturn`. I confirmed the shape by running 500, 700 and 1300 events.

Build-spec A1's acceptance ("a retire proposal removes a seed evaluator … the scripted world
exercises each once") is therefore not met by the documented run. On testnet/`funded`, with a
60 s tick, this is **10 hours to the first charter change of any kind** and ~19.5 hours from a
retirement vote to the retirement. Retirement — the essay's headline Class 3 freedom — is
gated behind the same serialized queue as price tweaks.

Fix: this may be intended cascade control, but it should be visible. Publish the next
activation time in the world block, and consider a separate, faster cadence for retirement,
which removes an actor rather than rewriting the standard.

### 6. A third of judgements are thrown away over an undocumented field — *broken*

`factorylab/runtime/loop.py:462-467`; `factorylab/cortex/schematics.py` `A_RETURN_MAY_INCLUDE`;
`factorylab/cortex/assembly.py:296-300`.

`about_handle` is published to the population as a free string in `reserved_return_fields`, but
it is the one reserved field with **no entry in `A_RETURN_MAY_INCLUDE`**, the block that
explains every other field. Judges fill it with prose. Measured in the 40-minute testnet run:
`meta-a` returned `"about_handle": "verdict"`, `eval-c` `"producer-return-eval"`, `eval-d`
`"current-return"`. The kernel cannot resolve any of them and discards the entire paid
judgement — **18 of ~57 judge returns, 32%**.

And `return.refused` goes only to the sealed diary; unlike `propensity.refused`
(`runtime/compute.py:768`) it is never appended to `registration_feedback`, so the population
cannot learn. The factory's sensory organ loses a third of its output, silently, forever.

Fix: document the field; on an unresolvable value fall back to the router's own subject (the
default already) and put the reason in `registration_feedback`.

### 7. Every provider failure is billed at the full ceiling — *broken*

`factorylab/runtime/resume.py:389-397` (`_recorded_error`); `factorylab/world/metering.py:91-99`.

`_recorded_error`'s class list does not contain `OpenRouterError`, so every provider failure is
re-raised as a bare `RuntimeError`. `Metered.run` commits the whole reservation for anything
that is not `UnbilledFailure`, and `UnbilledFailure` is raised nowhere except `resume.py:347`.
So a connection failure, a missing key or an HTTP 4xx before generation all charge the maximum.

Measured: 13 invocations failed `billing uncertain (RuntimeError)`, costing **199,592 of
1,174,279 micro-USD — 17.0% of the testnet run's entire compute spend — for nothing**. The
diary's `io.result` knows the class was `OpenRouterError`; the invocation record and the
summary say only "RuntimeError", so neither the population nor the operator can see what broke.

Fix: add `OpenRouterError` and the Venice error to `_recorded_error`'s classes; classify
transport-level failures as `UnbilledFailure` so the hold is released.

### 8. The wake page of a fake world publishes the real account — *broken*

`factorylab/runtime/wake.py:452-469, 504-509`.

`collect_wake` reads the live venue and reserve whenever `HL_PRIVATE_KEY` /
`RESERVE_PRIVATE_KEY` are in the environment, with no check that the world is live, and
`_venue` calls `live_exchange(manifest.exchange)`, which ignores `kind == "fake"`.

Repro: `uv run factorylab wake --ledger scripted.jsonl --out wake/` on the offline scripted
diary published `"venue": {"equity_micro": 986556225}` — the real testnet account — beside
`"portfolio": {"equity_micro": 60863397}`, the world's own, plus the real Base-mainnet reserve
`{"usdc_micro": 4967000, "venice_micro": 4976714}`. Two contradictory equity figures on the one
page the architect is allowed to read.

Fix: gate both on `manifest.exchange.kind == "hyperliquid"` and report `unavailable` otherwise.

### 9. The launch roster fails its own first charter card — *broken*

`worlds/testnet.toml` seed roster vs the `well_formed_rate` card (`answers_for = "all"`, >= 0.9).

Measured over 365 live invocations: 308 ok, 44 malformed, 13 failed = **84.4% well-formed**.
`eval-c` (tencent/hy3) 7/15 malformed, `antagonist-a` (qwen3.8-flash) 6/10, and 17 of the
malformed stopped on `length` with `{"raw": ""}` — reasoning models exhausting `max_tokens`
before emitting content. The world therefore violates its own first card from its first window
and the controller ratchets λ against every role for the architect's model choice.

Fix: raise `max_tokens` for the reasoning seeds or reseat the roster; this is the architect's
first move, not the population's fault.

---

## Minor

10. **Spot cannot be rehearsed with the shipped pair** (*unclean*). Testnet `BTC/USDC` has
    `szDecimals 0` and a mid of 6100, so `instruments()` reports `lot_size: "1"` and one lot
    costs ~$6,100 against an account holding single-digit spot USDC. I got a spot fill only by
    deriving `spot_pairs=("PURR/USDC",)`: order `60000565418`, 3.0 @ 4.6252, `market: "spot"`,
    `inventory_size` correctly net of the PURR-denominated fee.
11. **A spot fill on an unconfigured pair is booked as a perp** (*broken*).
    `factorylab/world/exchange.py:980-984` sets `market` by membership in `spot_pairs`, and the
    spot fee-token correction above it is skipped for the same reason. Observed live: a
    `PURR/USDC` fill entered the diary as `"market": "perp"` and settled -5600 micro against the
    wallet. Classify by the venue's own metadata, not by the manifest's subset.
12. **The venue's minimum order value is never published** (*unclean*). `instruments()` gives
    lot size, tick size and significant figures; a size 2 PURR order ($9.25) was rejected with
    "Order must have minimum value of 10 USDC". The population is told a size is legal and pays
    to find out it is not.
13. **`treasury.insolvency` is a per-event heartbeat** (*unclean*): 6,784 of a 500-event
    scripted run's ledger items, almost all `unaffordable: false`.
14. **`scripted-crash` dies below its floor** (*unclean*): terminal wallet **-$4.144312** with
    `termination.balance_floor_usd = "0"`. A gap liquidation on the fake venue overshoots; the
    essay's "token budget of $0" reads as a floor, not an overshoot.
15. **`--duration 40m` ran 67 minutes** (*unclean*): `cli.py:313-314` converts a duration into
    an event count, and each tick's internal cascade can outlast the tick.

---

## What worked, tried once each

Both scripted worlds exactly as the README says, including `balance_zero` and seal release.
Ledgered run, `postmortem`, `versions` (1 version / 4 windows, no pathologies, agreeing window
by window with the live `immune.window` items), `wake`. `kill -9` mid-run then
`factorylab resume` — `resume.begin` / `resume.reconcile` / `resume.timeouts` / `resume`, then
on to 13,782 events with conservation and verify both true. A connector registered by a 5-0
sortition ballot and fetched twice, its body parsed by a jailed population tool. A nested
request parent → helper → grandchild with tool calls. An assembly, a tool and a router
registered; a model proposal correctly rejected. An observation registered through the jail. A
Venice completion from prepaid credit (96 micro-USD, `chatcmpl-fdbdbba7…`). An x402 purchase
from a public-index seller for $0.001 (`0xec6a8d98…`). A perp fill (`60000400486`) and a spot
fill (`60000565418`) on testnet. Two class transfers that moved money. A treasury `to_reserve`
leg whose HyperEVM burn confirmed with a Circle attestation and whose Base Sepolia mint was
still in flight, with the principal correctly held and a second transfer correctly refused. A
$1 withdrawal correctly refused as below the fee-covered minimum.

**Spend:** $0.001 of Base mainnet USDC (cap $0.50), $0.000096 of Venice prepaid credit, $1.174
of OpenRouter prepaid credit, testnet-only venue and treasury moves. No Venice top-up, no
mainnet pot move, no manifest named `funded`.

---

## Does it work, and is it the factory the essay describes?

Most of it works, and the parts that work are better than they need to be: the sealed
hash-chained diary survives a `kill -9` at an arbitrary write and replays into a world that
still conserves money; the jail runs population code; the connector, the nested request, the
lot book, the x402 and Venice rails and the two venue classes all do what the world block
promises. But the world that is about to be launched cannot be launched from its own manifest
— two dead values stop it before genesis, and one of them (`BTC/USDC` on mainnet) is waiting to
stop `funded` at the single moment the architect is forbidden to retry — and three of the
freedoms the population is *told* it has are paper: a registered observation can never reach a
card, a class transfer can never confirm and so can happen only once, and a retirement takes
600 world events to arrive because it queues behind price tweaks. Against the essay, the
shortfall is specific and it is the same shortfall each time: the factory is allowed to
*propose* new objectives and not to *install* them. Chapter II.IV.a asks that every
contribution "converts a world-finding into authored record"; here the conversion step is
broken in code, so the charter remains, in practice, the architect's four norms and three
cards, measured by the architect's twenty-two observations, on the architect's clock. Add the
32% of judgements deleted over an undocumented field and the 17% of compute spent on calls that
returned nothing, and the reward line the essay calls the factory's sensory organ is running at
roughly two-thirds signal. None of this is structural: every finding above is a wrong default,
a missing argument or an unthreaded book, and all four blockers are a handful of lines. Fix
them and this is, as far as running it can show, the factory the essay describes. Launch it as
it stands and it will not start.
