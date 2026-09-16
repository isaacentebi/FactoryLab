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
