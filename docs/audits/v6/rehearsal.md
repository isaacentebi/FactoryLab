# Edition 3 testnet rehearsals

## Run 1: 40 minutes on the ratified edition 3, flat account

The first live run of edition 3 (`docs/plans/edition3.md`): all five workstreams merged
(#90 to #94), the charter ratified on this roster, GPT-6's nine seats and seed lenses, kill
wind-down on. Four ticks; the constructor (Sol, cadence floor 9) could not wake yet by design.

| | |
|---|---|
| Manifest | `worlds/edition3-rehearsal-1.toml`, namespace `08ae9358…`; code `3d352da` |
| Wall clock | launch 18:01 local, killed 18:33 (`explicit_kill:budget`, the duration) |
| Ledger | `runs/edition3-rehearsal-1.jsonl`, verify true, conservation true, 2,235 items |
| Tick gaps (s) | 600.008, 600.010, 600.006 |
| Invocations | 27: judge-fidelity 13 (2 malformed), judge-consequence 7, antagonist 3, mechanism 2, opportunity 1, meta-calibration 1 |
| Spend | 133,681 µUSD in 32 minutes, about $6 a day at this cadence; wallet 299.87 of 300 |
| Producer wakes | mechanism 2 (hold, hold), opportunity 1 (defer 1 tick), antagonist 3 (hold) |
| Tool calls | 7, all by mechanism: funding history ×4, candles ×2, open orders ×1 |
| Orders | 0 by the population; 2 by the kill wind-down |
| Prompt bytes | stable prefix 54,048; `you` block 8,596; inputs 4,188 (producer); total 68,229 |

**What worked, item by item.**

- *Continuity (C1).* Nine `state.put` at genesis: every seat's lens became its first working
  state head (about 1.1 KB each). 27 outcomes were addressed to the seats that decided them
  (`outcome.addressed`), bodies as artifacts. No seat wrote a new `working_state` and none
  acknowledged an outcome in four ticks; the fields are there and were rendered.
- *Thinking control (C2).* One `WorldUpdate` per tick, four in all. The opportunity seat
  answered `defer: 1` on tick 1 ("everything I am handed is one print per market … insufficient
  history") and was absent from the next draw (`seat.deferred`, `until_tick 2`). No
  subscription changes, no watchers registered.
- *Charter (C3).* One card. The wake carries the five norm definitions. The fidelity judge
  wrote structured verdicts; two of its thirteen returns were cut off mid-JSON (GLM at its
  output limit on a long rationale), which the kernel counted as malformed and billed.
- *The seat sees itself (C4).* Every request carried the `you` block: entitlement, holds,
  release schedule, runway ("insufficient history" under six hours), provider inventory, open
  commitments, unread outcomes. The mechanism seat's second rationale reads the world in those
  terms: "flat account, zero open commitments, one prior decision handle still open".
- *Kill (C5).* The wind-down ran before `Terminated`: it sold the 17.99 PURR spot balance the
  testnet account carried (filled 17 at 4.5795) and tried to sell the HYPE balance, which the
  testnet book rejected ("could not immediately match against any resting orders"); both
  ledgered as `kill.wind_down`, the summary counted 2 orders, 1 sold, 1 failed, and the witness
  line carries `wind_down: true`. The world died anyway, as the contract says.

**The first purchase of information in five rehearsals.** The mechanism seat (DeepSeek 4.1
flash, lens: cash-flow mechanisms) pulled 48 hours of funding history on BTC and ETH and
candles on both, and its second wake began from what it had pulled: "BTC perp funding has run
persistently positive … ETH persistently negative … the funding series I pulled last wake
shows a legible …". It still held: the carry it saw needs a hedge it did not yet size. That is
the shape GPT-6 asked for (retained reason, bought evidence, a threshold), reached in two wakes.

**What did not happen yet.** No seat wrote its own state, so "the funding series I pulled last
wake" lived only in the addressed outcome and the seat's rationale; if the seat had written it
to `working_state` it would be in the next request verbatim. No acks. The constructor never
woke. Four ticks is not a test of any of that; the 24-hour run is.

**Costs.** The `you` block is 8.6 KB per request, a fifth of the changing part; the stable
prefix is still 54 KB. About $6 a day at nine seats and this cadence, before the constructor's
wakes (about $1.30 a day more at sixteen wakes).

## Run 2: stopped after ten minutes

A 24-hour run at the ten-minute tick was started at 20:26 and stopped ten minutes later by
decision: a day buys 144 wakes, and what the run has to show is per wake, not per day. Killed
with the operator's kill; the wind-down tried to sell the HYPE dust and the testnet book rejected
it; the identity is witnessed dead (`.witness/edition3-rehearsal-2.jsonl`).

## Run 3: five hours at a two-minute tick, 147 ticks, the final roster

`worlds/edition3-rehearsal-3.toml`: the ratified final roster (GLM 5.3 flash on Venice for five
seats, GPT-5.6 Luna on OpenRouter for four), every seat prompt opening with the JSON and refusal
contract, tick 120 s so that the governance floor falls from 30 hours to 6 and a day's wakes
fit in five hours. Launched 20:30, killed 01:32 (`explicit_kill:budget`, the duration).

| | |
|---|---|
| Ticks | 147; gaps min 120.000 s, median 120.009 s, max 162.5 s (one late tick under load) |
| Ledger | `runs/edition3-rehearsal-3.jsonl`, 194 MB, 46,608 items, verify true, conservation true |
| Model calls | 525: Luna 268 ok / 0 malformed; GLM 221 ok / 28 malformed, every one `finish_reason: length` |
| Spend | $2.94 in five hours ($0.0056 per call, $0.020 per tick); wallet 297.06 of 300 |
| Producer answers | 101 ok: defer 78, hold 18, order 5; 15 malformed |
| Quiet ticks | 482 draws reached nobody because every candidate was asleep |
| Working state | 21 writes by seats themselves (constructor 8, mechanism 8, empirical 5) beyond the 9 seeded lenses; 10 rent charges |
| Outcomes | 559 addressed; 0 acknowledged; `outcome.get` called 3 times |
| Subscriptions | 2 accepted (opportunity: BTC only, cadence floor 1 then 3); 9 refused |
| Defers | 80: antagonist 32, opportunity 17, mechanism 16, empirical 9, constructor 6 |
| Orders | 5 sent: 2 refused by the kernel for collateral, 3 filled (BTC short 0.001, ETH long 0.025, ETH close); realized -$0.095 plus $0.088 fees; funding received $0.029 |
| Tool calls | 16: positions 3, open orders 3, outcome.get 3, funding history 2, artifact.get 2, funding 1, set_leverage 1, place_market 1 |
| Registrations, proposals | none by the population |
| Wind-down | closed the 0.001 BTC short at 76,023 and sold 0.19 HYPE at 33.327; 2 orders, 0 failed; witness carries `wind_down: true` |

**The thing this edition was built for happened.** The constructor seat (GLM, lens: reusable
capability) kept a plan in its working state across wakes and executed it over four hours:

1. Pulled twenty hours of funding history, wrote `plan: funding-carry leg 1 of 2`, observed
   BTC funding persistently positive and ETH persistently negative, sent a 0.005 BTC short.
2. The kernel refused it ("order collateral exceeds available wallet balance"). Next wake its
   state carried `rejection_diagnosis`: two hypotheses about what margin the world actually
   allows, and a probe: 0.001 BTC, which passes under both.
3. The probe filled at 76,194 with margin 38.09. Next wake: "confirms margin is charged at
   leveraged notional from perps cash and the strict spot-USDC hypothesis is wrong". Sent the
   ETH long, 0.025 at 2,407.8, "the sign-flip exit rule I set" written into state.
4. Two ticks after ETH funding flipped positive, it closed the ETH leg at 2,404.0 by that rule,
   citing the two prints that triggered it.

A hypothesis, a test, a diagnosis of a refusal, a revised test, an exit rule, and the exit,
each wake reading the previous one's state. That is the Tuesday-to-Thursday acceptance test
from GPT-6's reading, passed on testnet by a $0.0035 model, with a net loss of nine cents.

**The rest of the population.** The mechanism seat wrote eight states about funding
persistence and sized nothing ("checking rate persistence and level distribution over a longer
window"). The empirical seat kept hypotheses about the fills it saw ("tiny test-sized trades
opened by other seats; no evidence yet they signal anything") and deferred. The opportunity
seat narrowed its subscription to BTC and raised its cadence floor to three ticks. The
antagonist deferred 32 times. Judges and meta ran 387 calls, 74% of all spend, grading
mostly defers and holds.

**Three defects the run exposed, all fixable before launch.**

1. *Collateral is checked against the wrong pot.* `_order_collateral` compares the venue
   margin an order needs with `wallet.available`, the thinking pot net of the protected
   reserve. That is why a $383 short was refused on an account with $851 of perps cash. In the
   funded world the thinking pot and the trading principal are separate by design (C5), so the
   check must be against the venue's free collateral, with the leverage wall as the hard cast.
2. *The diary is mostly mids.* 860 `exchange.mids` reads recorded in full, 58 KB each (the
   whole venue), 50 MB of the 194; instruments, once a tick as #89 made it, are 39 MB more. At
   the ten-minute tick that is about 200 MB a day. Same fix as #89: one read a tick for prompt
   building.
3. *GLM runs out of output.* All 28 malformed returns are `length`: mechanism and empirical
   at 1,500 tokens, meta-countercase at 2,000, cut mid-rationale. 4,096 for every GLM seat.

And two frictions worth a sentence in the prompt rather than a code change: seats wrote
`cadence_floor: "2m"` and `"120s"` (the field is ticks, an integer) and asked to subscribe to
kinds outside their contract; both refusals were ledgered with the reason and fed back.
Nobody acknowledged an outcome: `ack_through` is documented but not salient.

**Costs.** $0.020 per tick. At the ten-minute tick that is about $2.90 a day, half of edition
2's burn, because 78 of 101 producer answers were defers and 482 draws reached nobody; the
seats are already spending less than the router offers them.

## Run 4: forty minutes with web search available

`worlds/edition3-rehearsal-4.toml`, two-minute tick, the roster and prompts of run 3 plus the
`web.search` tool (PR #96: a cent a query, results recorded and protected like fetched text, a
continuation round after a search). Twenty ticks, 94 invocations, $0.48. Eight tool calls, all
venue reads (funding history, funding, positions, order book). **Zero searches.** Nobody opened
the door the run was built to test. The same population that reads funding history every
wake did not look outside the venue once, at a cent a look. That is the design fact GPT-6's
third reading names: the tools are an index entry, the lenses point at the market, and
deferring is the cheapest competent-looking answer. The R3 workstreams (prompts and the
evaluation commission in particular) are the response; run 5 tests it again.

