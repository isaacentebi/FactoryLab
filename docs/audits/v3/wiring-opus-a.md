# Cold audit v3, seat 5: wiring, by running

Produced by running the shipped CLI or runtime in this worktree at commit `86b391c`. Paid
actions, balances and hashes: `docs/runs/audit3-wiring.md` (gitignored; written anyway).
Money: **$0.002 of real on-chain USDC** of the $0.50 cap, on two x402 buys; Venice from
prepaid credit ($0.000137), never topped up; testnet USDC only on the treasury; and $0.460899
of OpenRouter credit over 169 live invocations. No mainnet pot move, no `funded` manifest,
nothing committed.

---

## Findings

### 1. blocker — broken. The `testnet` world cannot be launched at all: two independent faults

**1a.** `worlds/testnet.toml:208` declares `spot_pairs = ["BTC/USDC", "ETH/USDC"]`, but of
Hyperliquid testnet's 1,321 spot pairs exactly **one** has a canonical name, `PURR/USDC`
(the rest are `@<index>`). `HyperliquidExchange._configure_spot` (`world/exchange.py:755-757`)
raises on the gap, from a constructor that runs before anything else
(`runtime/bootstrap.py:117`): `factorylab run --world testnet` exits 1 printing
`adapter_unavailable`, no world created, underneath
`ValueError: spot pairs unavailable in venue metadata: ['ETH/USDC']`.

**1b.** With `spot_pairs` fixed, construction fails again: `worlds/testnet.toml:202` is the
placeholder `reserve_address = "0x1111…1111"`, and `LiveRail.__init__`
(`world/treasury_rails.py:88-89`) refuses `reserve key does not match
treasury.reserve_address`. It fires whenever `reserve.key` is present, and `runtime/cli.py:74`
loads all three keys unconditionally. The README says testnet needs the exchange and model
keys; in fact the *third* key is what makes it unlaunchable.

Both faults are silent: `runtime/cli.py:679-686` turns every bootstrap exception into the
single word `adapter_unavailable`, so neither reason is obtainable without reading the source.
`worlds/edition1-example.toml:253` carries the same pair list, so the charter edition the
launch world is drafted from inherits 1a.

**Fix.** 1a: seed `venue.spot_pairs` from pairs that exist on the target network, and
validate at manifest load so the failure names itself. 1b: point `treasury.reserve_address`
at the real reserve, or build `LiveRail` lazily.

### 2. blocker — broken. A live class transfer can never be confirmed (`world/treasury.py:611`)

`class_poll` matches the venue's ledger row to the request by
`row.get("time") == ref["nonce"]`. The nonce is the client's millisecond timestamp at
*prepare*; `time` on a Hyperliquid `accountClassTransfer` row is the venue's *execution*
time. They are never equal. Reproduced on live testnet: the world sent one `perps_to_spot` of
$10 with nonce `1789272902090`, and `user_non_funding_ledger_updates` shows it executed at
`1789272917009` (+14,919 ms, hash `0x0a02f726d3b8`), with a second $10 at `1789273092998`.

`class_poll` returns `None` for both, so the transfer stays `submitted` for ever: the diary
runs `treasury.submitted` → `broadcast` → `retry` → `broadcast` → `pending {"reason":
"submission requires reconciliation"}`, once per reconcile tick for the rest of the world's
life, `principal_moved: false` throughout. Per `docs/manifest.md` ("A pending or stranded
transfer prevents another transfer") the treasury then jams — no further transfer, in any
direction, ever — and the pots view stays `pending: true, complete: false`, so the
population's `pots` block never accounts for money that did move. Nothing is lost and the
retries are idempotent (the venue rejects the duplicate nonce), but the factory never learns
its own balance sheet. Invisible in every scripted world, because the fake rail fakes the
poll; it bites only the live rail, the one `testnet` and `funded` use.

**Fix.** Do not compare `time` to `nonce`: match the unique `accountClassTransfer` row with
the right `toPerp` and amount whose `time` lies in `[nonce, nonce + maxTimeout]`, pinned by
`hash`; or record the hash the venue returns at send time and match on that.

### 3. blocker — broken. Governance cannot activate in the world the README tells you to run

`CadenceGate.slowest_period_events` (`runtime/cadence.py:80-92`) floors the period at the
`consequence_backstop_events` backstop (seed 200) *permanently* — the docstring says so — so
`earliest_event` (`:98-101`) is `0 + 3 × 200 = 600`, published at t=0 as
`governance.earliest_activation_event: 600`. The README's command is `--events 500`.

So in the shipped demonstration, everything the population is told about governance passes its
vote and does nothing: `"amendments_passed": 1, "amendments_activated": 0, "clock_changes": 0,
"votes_cast": 15`. The amendment and the retirement are each deferred once per reserve window,
five times, to an event the world never reaches — `charter.approved` and
`retirement.tally {"outcome": "passed"}`, then ten `charter.deferred {…, "earliest_event":
601}`. `eval-a`, voted out at world-second 34, is still routed for the remaining 466 s, 509
invocations. The only `actor.retire` items in the diary are `router:MarketMid` and
`router:Finding`: router replacement, not retirement. The build spec's A1 acceptance ("a
retire proposal removes a seed evaluator … the scripted world exercises each once") is not met
at the documented event count.

Re-running at 700 events separates "the gate is wrong" from "the demo is too short". The gate
is right: at event 601 the amendment activates (`charter.activate`,
`price.register {"card_id": "turnover"}`). The **retirement still does not**, because
activation is one per boundary and the next is 1201. Governance therefore acts at events 601,
1201, 1801: in `scripted` (1 s tick) at 10 and 20 minutes; in `testnet` and anything built
from it (60 s tick, same backstop) at **10 and 20 hours of world time**, and given finding 6,
10–24 and 20–48 hours of wall clock. A seed assembly voted out in its first minute keeps
judging for most of a day. The ballots work; nothing that ships reaches the gate.

**Fix.** Raise the README's `--events` past 1200 (or lower the scripted manifests'
`consequence_backstop_events`) so the demonstration demonstrates governance, and print the
activation schedule the world block already computes.


### 4. blocker — broken. A registered observation can never be named by a card (`charter/book.py:72`)

`CharterBook` holds no observation book, and `CharterBook.validate` calls `preflight_card(card)`
with no `observations` argument, so `preflight_card` (`charter/measurement.py:127`) falls back
to `seed_book()` — the 22 seed observations and nothing else. Every amendment whose card names
a population-registered observation is therefore refused, whatever the runtime registered.
`runtime/governance.py:477` *does* pass the live book, so the earlier check passes and the
later one refuses.

Reproduced by running. A harness subclassing `ScriptedProvider` had a producer register an
observation, then thirty world-seconds later propose a card naming it:

```
[162834] observation.preflight {"observation": "fills-seen", "value": 93.0, "error": null}
[163054] event:Registered      {"kind": "observation", "id": "fills-seen", "version": 1}
[186498] registration.rejected {"reason": "ValueError: card fills-seen-card observation:
                                unregistered observation"}
[250109] observation.out_of_range {"observation": "fills-seen", "version": 1, "value": 101.0}
```

The registry says registered, the pricing pass measures it every window by name and version,
and the amendment validator in the same process says it does not exist. It reproduces with no
runtime at all, because the book has no way of being told. A11's whole point is "a card may
then name it", and the world block says exactly that; as shipped, a registered observation can
be made and can never be priced. **Fix:** give `CharterBook` the runtime's `ObservationBook`
and pass it at `book.py:72` (`runtime/worlds.py:402`'s seed-only call is correct, because only
seed cards exist at manifest load).

### 5. serious — broken. `factorylab wake` publishes the architect's real accounts for a world that owns neither

`runtime/wake.py:502-510` gates the `venue` and `reserve` sections on nothing but the presence
of `HL_PRIVATE_KEY` / `RESERVE_PRIVATE_KEY` in the environment, and
`world/exchange.py:1290 live_exchange` ignores `spec.kind`. Run on the `scripted` world's
ledger — the world the README says "needs no network, no keys and no money" — `wake.json`
carried `"venue": {"equity_micro": 991567873}` and `"reserve": {"usdc_micro": 4967000,
"venice_micro": 4976714}`: the live Hyperliquid testnet account and the live **Base mainnet**
reserve, in the published page of a fake world whose own equity the same file gives as
`portfolio.equity_micro: 79175730`. Two contradictory equities in one page. Not only cosmetic:
`runtime/worlds.py:383` refuses `mainnet = true` only when `exchange.kind == "hyperliquid"`,
so a manifest with `kind = "fake"`, `mainnet = true` and any name validates, and `wake` on it
reads the **mainnet** venue account.

**Fix.** Pass `exchange.kind` through: read the venue only when the world's own exchange is
live and the reserve only when its manifest configures one; move the mainnet-name check off
`kind`.

### 6. serious — broken. The live tick is not the manifest tick, and the cadence gate is priced in manifest ticks

`LiveClock._events` (`runtime/live.py:52-63`) sleeps until `last_ns + interval_ns` and
otherwise fires as soon as it can, so an overrunning loop just produces a late tick. From the
shipped `testnet` manifest's own sealed diary (`tick_interval = "60s"`), tick to tick:
**60.0, 145.1, 119.8, 104.4, 118.0, 75.7 s** — 1.0× to 2.4× the declared interval, because
one tick's work does not fit in 60 s at real model latency (7 ticks, 627 s, 54 invocations).
`CadenceGate.slowest_period_ns`
(`runtime/cadence.py:94-96`) converts its event estimate to real time by multiplying by the
*declared* interval, and `docs/manifest.md` states that rule explicitly, so the published
`governance.slowest_period` understates the slowest loop by the same factor — the exact
quantity the essay's cascade-ratio argument (Ch. II, IV.c, "an inner loop must resolve itself
several times faster") rests on.

**Fix.** Use the *measured* tick interval in `slowest_period_ns`, or publish both and say
which the gate used.

### 7. minor — broken. `probe --provider x402` pays for an empty answer and says nothing

`runtime/cli.py:132-137` hard-codes `max_tokens=32`. On a reasoning model that budget goes to
`reasoning_content` and `content` comes back empty; `world/openai_wire.py:41` reads absent
content as the empty string by design, so the probe prints a successful-looking result with
no text and charges the wallet. At 32 tokens FarOuter's `minimax-m3` printed
`{"text": "", "cost_micro": 1000, "settlement": {"success": true}}` (tx `0x9b8efe0c…`); at 64
tokens the same model returned `'OK'`, 45 output tokens of which 41 were reasoning
(`0xf5b00a1e…`). Both $0.001. The sellers' own resource descriptions warn about exactly this.
**Fix:** raise the probe's budget and flag an empty completion with `finish_reason: length`.

### 8–10. minor — unclean, three together

**Ledger volume.** The 500-event scripted world produced a **351 MB** ledger and an 831 KB
`.head`: mostly `io.call`/`io.result` (59,137 pairs) and `treasury.insolvency`, appended once
per event 6,784 times, all `{"unaffordable": false}`. Write it on a transition.

**The wake publishes a position.** `wake.json` carries
`portfolio.open_positions: [{"coin": "BTC", "side": "buy"}]`, against A17's "no positions, no
entry prices, no assembly ids".

**Two registrable kinds ship untested end to end.** `observations_registered: 0` and
`assembly_learners_registered: 0` in both 500-event scripted runs: `ScriptedProvider._produce`
(`world/scripted.py:64-229`) proposes no `observation` and no `learner`, though the world
block advertises both. That is why finding 4 survived two earlier audits.

---

## What I tried, once each

| Item | Outcome | Evidence |
|---|---|---|
| scripted world, 500 events | **worked as told** | `wallet_conservation: true`, `ledger_verify: true`, 17,856 events, 356 fills, 12 registrations |
| scripted-crash world, 600 events | **worked differently** | dies `balance_zero` and releases the seal, but at wallet **−$4.144312**, below `termination.balance_floor_usd = "0"`: the liquidation loss lands after the affordability check. Conservation holds. |
| `versions`, `postmortem`, `report`, `manifest` | worked as told | 17 versions over 80 windows, Dobrushin gap 0.9605, `learning_death` and `overfitting_divergence` evidence, EWS table; the diary decrypts with the released key and stores returns, never prompts |
| testnet world, live | **did not work** | finding 1; ran only after two local patches |
| observatory wake on a living world | **worked differently** | finding 5 |
| perp fill on testnet | **worked as told**, at the third attempt | order `59997700051` buy 0.001 BTC @ 77039.0 (fee $0.034667), then `venue.close` → `59997705229`. Earlier attempts drew `order.infeasible {"reason": "order collateral exceeds available wallet balance"}`: `ScriptedProvider` sizes from the *venue's* equity while the kernel enforces the *world's* wallet, and `_order_leverage` (`venue.py:262-274`) gives no discount for leverage the world never set. The refusal is right; the seed sizing rule is not. |
| spot fill on testnet | **could not try as shipped**; worked once patched | no `BTC/USDC` or `ETH/USDC` on testnet (finding 1a). With `PURR/USDC`: `venue.place_market {market: "spot"}` → order `59998054535`, buy 8.0 @ 4.6252, `spot.inventory {size: "7.9944"}` net of fee; a repeat was correctly refused `Insufficient spot balance`. |
| class transfer (`perps_to_spot`) | **worked differently** | executes on the venue; never confirms (finding 2) |
| registration (assembly, model, tool, router) | worked as told | 12 accepted, 1 rejected: `no catalogue entry for that model` |
| retirement vote; amendment | **did not work** | both ballots pass, neither activates (finding 3) |
| nested request with a tool | worked as told | `request.child` parent→`composition-helper`→`funding-watcher`, grandchild calling `catalogue.search`, cost ceilings 89,806,181 → 89,805,681 µ |
| observation registered / **priced by a card** | **worked as told** / **did not work** (finding 4) | `observation.preflight {"value": 93.0}` then `event:Registered`; measured every window after, `observation.out_of_range` when it left its declared `[0, 100]` |
| assembly learner (Blum–Mansour) | **worked as told** | `event:Registered {"kind": "learner", "learner": "blum_mansour"}` then 333 `propensity.learned`. A trap: 334 decisions were `propensity.unlearned {"reason": "declared actions outside the registered action set"}` — the set is frozen at registration and the kernel's label carries a size band, so an assembly declaring `buy:BTC:m` that trades an `l` size learns nothing that round. |
| connector registered by vote and fetched | worked as told | `connector.registered {"id": "scripted-source", "vote_id": "connector:scripted-source:v1:…"}`, then two `connector.call` at 1,000 µ, body parsed by a jailed population tool |
| Venice from prepaid credit; x402 seller for cents | worked as told (x402 with finding 7) | Venice $0.000137, credit 4,976,850 → 4,976,714 µ, no on-chain spend; two x402 purchases at $0.001, settled on Base mainnet |
| treasury move venue→reserve | **worked as told** | testnet USDC; `confirmed`, 5,000,000 µ received, 3,406 µ fees, after 13 min and 10 `advance` polls — the Base Sepolia mint is accepted only at L1 finality (`evm.py:290-292`). Leg 1 proved by canonical system call plus Circle attestation; the journal never invented a second payment for the unconfirmed leg. |
| kill and resume | worked as told | a live testnet world's process killed mid-run; `factorylab resume` replayed and continued (`resume.begin/reconcile/timeouts`, `event:Reconciled`) |
| jail probe; `compute_proof.py --dry-run` | worked as told | `jail-check: ok (/usr/bin/sandbox-exec confines …)`; three seller steps, `payment_outcome: "not_sent"` |

**One more, minor — broken: a live venue write that fills is counted as a failure.** Every
`venue.*` write returns `{"status": "uncertain"}` and is resolved later by the reconciler, so
`tool.call` records `ok: false` even for the orders above that filled. The run whose only
activity was the PURR spot buy summarises as `"orders_placed": 0, "fills": 1,
"lots_opened": 1, "tool_calls": 2, "tool_call_failures": 2`: a real fill in the lot book, no
orders placed, both tool calls failures. `tool_calls` and `well_formed_rate` are priced
observations, so this misprices the population for succeeding.

**Jail, probed directly** (`cortex.sandbox.run_python`): refused `urllib` over https, raw
`socket.create_connection`, opening `reserve.key`, writing to `$HOME`, and
`subprocess.run(['/bin/echo'])`; `glob('*.key')` and `os.environ` "KEY" entries came back
empty; ordinary JSON in and out worked.

**Connector proxy, probed directly** against live hosts with the world's own denylist:
`https://example.org/` (200, 559 bytes) and `https://api.github.com/zen` (200, 19 bytes) went
through; every adversarial case was refused with a reason — `http://`, `api.venice.ai`
(denylisted), `localhost` and `169.254.169.254` (private or nonpublic), a path of
`https://evil.test/x`, a 301 (redirects disabled), an oversize body (reporting only
`max_bytes + 1` bytes).

---

## Local patches

Two lines in `worlds/testnet.toml`, this worktree only, never committed, solely to get past
finding 1:

```diff
-reserve_address = "0x1111111111111111111111111111111111111111"   # [treasury], line 202
+reserve_address = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
-spot_pairs = ["BTC/USDC", "ETH/USDC"]                            # [venue], line 208
+spot_pairs = ["PURR/USDC"]
```

For the live-venue runs I also used an untracked copy at `/tmp/fl5/testnet-fast.toml`
(`name = "testnet-fast"`, `tick_interval = "5s"`, `[clock] min_tick = "1s"`) so ticks were not
gated by the 60 s interval, plus harnesses subclassing `ScriptedProvider` to register an
observation and a learner and place small orders. Neither touches the repository.

---

## Does it work, and is it the factory the essay describes?

Most of the machinery works, and the parts that work are better than they need to be. The
ledger verified and conserved across 28,198 events; kill-and-resume replayed a live world's
authenticated tail and reconciled it against the venue mid-flight; the jail refused network,
keys, environment, filesystem and subprocess on every probe I could invent; the connector
proxy refused http, denylisted hosts, loopback, cloud metadata, origin-bearing paths,
redirects and oversize bodies while still fetching real public https; three compute rails
each paid from a different purse; the population placed, closed and spot-bought real fills on
a live venue; and the treasury journal held its principal for thirteen minutes rather than
claim an arrival it could not prove, then confirmed both legs from finalized receipts. But
the factory about to launch cannot be started: its only live rehearsal world dies at
construction on two unrelated manifest faults, and neither reason is obtainable without
reading the source, because the CLI answers `adapter_unavailable` and nothing else. Past that,
three of the freedoms the population is told it has do not reach the world. A class transfer
executes on the venue and can never be confirmed, because the confirmation compares an
execution time to a request nonce, so the treasury jams for the life of the world. A
registered observation can never be named by a card, because the charter book validates
against the seed vocabulary and cannot be told otherwise — the runtime measures `fills-seen`
every window and refuses, in the same process, to admit it exists. And no world shorter than
600 events reaches its first activation boundary, so in the run the README prints, the
amendment passes and does nothing and the evaluator the population voted out keeps judging to
the end. On the essay's question the shape is right and unusually honest about it: what the
factory may know, may measure, may be and may stop being are registrations rather than code,
the kernel's three hard casts about organisation are the only ones enforced, and nothing I
reached announced a rule instead of enforcing it. But the essay's test is the wake, not the
design — "redirect their gaze from the self-description of the factory to the outcomes" — and
the outcomes I could observe are Class 2: a population trading and judging against exactly the
three cards the architect wrote, whose every attempt to change that ends in a ballot that
passes, a measurement that cannot be priced, or a boundary it never reaches. Fix 2, 3 and 4
and I think the claim holds; until then the freedoms are real in the code and paper in the
world.
