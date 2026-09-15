# Edition 2 testnet rehearsals at the ten-minute tick

Three live Hyperliquid-testnet runs of the edition 2 world on 15 September 2026, both from
`worlds/edition2-testnet.toml` (ratified charter, endowment schedule, one-hour windows) with a
fresh client namespace each, killed at the end. Code at `af103a0` (the reviewer zip) plus the
rehearsal-preflight fix `4afa4cb`. No mainnet, no fund movement. Ledgers are sealed; every number
below was read back through `factorylab.versioning.reader.read_diary` with the run's own seal key,
or from the run's summary line and `factorylab wake`.

## Run 1: 70 minutes, confounded by load

| | |
|---|---|
| Manifest | `worlds/edition2-rehearsal.toml`, namespace `73347490…`, hash `6d2dbb42…` |
| Wall clock | launch 02:23:37 local, kill 03:24:31 (61 min; `--duration 70m` ends at the cascade boundary) |
| Ledger | `/tmp/e2-rehearsal.jsonl`, 4,774 sealed items, verify true, wallet conservation true |
| Termination | `explicit_kill:budget`, seal key released |
| Events / decisions / invocations | 206 / 109 / 73 (36 noops) |
| Tick gaps (s) | 600.015, 600.006, 617.18, 630.24, 600.008, 600.009 |
| Invocation status | 55 ok, 11 failed, 7 malformed (well-formed 0.73) |
| Spend | 558,786 µUSD; successful calls 194,753 (3,541 per call); failed calls 348,884 |
| Wallet after | 89,420,468 µUSD; unlocked 29,464,172; locked 60,000,000; next release at launch + 7 d |
| Entitlements (wake) | producers 5.16 USD, evaluators 10.34, meta 5.33, antagonist 2.65, unallocated 5.98 |
| Orders | none placed; exchange equity 973.48 USD (testnet), funding 2,346 in / 23,092 out |
| Registrations, amendments, tool calls | 0 / 0 / 0 |

**What the 3× per-call cost was.** The 55 successful calls cost 3,541 µUSD each, the same as
edition 1's rehearsal (3,500). The other 62% of spend is eleven calls that returned no bill:
`billing uncertain (VeniceError | OpenRouterError)`, status none, which the meter charges at the
reserved ceiling (17,799 to 81,991 µUSD each). Nine more `X402Error`s hit the x402 seats. All
twenty errors have no HTTP status, which is a client-side timeout, and the two long tick gaps
(617 s, 630 s) fall in the same span. The full test gate (`pytest -n auto`, 12 minutes) was
running on this machine from launch until 02:36. Edition 1's clean rehearsal had zero such
errors. Run 2 below separates the two explanations.

**The malformed returns are one model.** Six of `seed-observer`'s seventeen returns
(`z-ai/glm-5.3-flash`) wrapped the whole reply in an `"answer": {...}` key; the raw text is a
sensible hold decision inside the wrong envelope. One `meta-a` reply was `{}` and one
`seed-observer` reply was truncated at the length limit. Edition 1's rehearsal had the same seat
on the same model at 1.00 well-formed, so the edition 2 prompt text (program seats, artifacts,
challenge, service) is the likely cause. This is exactly the question the seat calibration
(C13) exists to answer per model; its table is below.

**Edition 2 physics observed.** 147 `budget` ledger items: genesis grants of 2,666,666 per seat
and per-call debits through each seat's `SeatWallet`; cover and bridge ops present. Locked
backing untouched, next release correctly scheduled at launch + 7 d. No dormancy (30 USD
unlocked lasts days). Cost card measured both ways in the last window: `cost_per_return` 3,489,
`cost_per_attempt` 8,048, the difference being the uncertain bills, which is the point of the
new observation. `release_digest` in the Launch event. The witness append failed for this run:
the witness lives at `<parent of the ledger's directory>/.witness/`, which for a ledger in `/tmp`
is `/.witness` (unwritable). Run 2 keeps its ledger under `runs/`; on the droplet the ledger is
under `/srv/factorylab/runs/` and the witness lands beside it as designed.

## Run 2: 40 minutes, quiet machine

| | |
|---|---|
| Manifest | `worlds/edition2-rehearsal-2.toml`, namespace `f98a5239…`, hash `18c9e98c…` |
| Wall clock | launch 03:31 local, 30.1 min to kill (`--duration 40m` ends at the cascade boundary) |
| Ledger | `runs/e2-rehearsal-2.jsonl`, verify true, wallet conservation true, 0 outstanding decisions |
| Termination | `explicit_kill:budget`, seal key released; witness line written to `.witness/e2-rehearsal-2.jsonl` |
| Events / decisions / invocations | 142 / 65 / 42 (23 noops) |
| Tick gaps (s) | 600.006, 600.000, 600.003 |
| Invocation status | 38 ok, 2 malformed, 2 failed (well-formed 0.905) |
| Spend | 238,573 µUSD; successful calls 141,037 (3,712 per call); the two uncertain bills 81,296 |
| Wallet after | 89,736,725 µUSD; locked 60,000,000 untouched |
| Orders | none placed; exchange equity 973.54 USD (testnet) |

Nothing else on the machine: the gate had ended and only the paid calibration ran beside it,
network-bound. Tick timing is load-bearing here, and it is the declared tick to the
millisecond. The successful-call cost is edition 1's. The two malformed replies are an
evaluator reply truncated at its length limit (`eval-a`, Venice GLM) and a meta reply whose
propensity named `conformity:0.55` instead of the declared key (`meta-a`, Venice DeepSeek); no
`"answer"` wrapper appeared because `seed-observer` was routed less in a shorter run. The two
provider errors with no HTTP status (`eval-c` Venice, `eval-d` OpenRouter) recurred without
load, billed at the ceiling and 34% of the run's spend, so run 1's excess was partly load and
partly a transport timeout of 60 s that a long reply can exceed; after both runs the model HTTP
timeout was raised to 180 s (`e6bb48b`), which the ten-minute tick absorbs. The next rehearsal
measures its effect.

Burn from the clean run: 42 calls in 30 minutes is about 2,000 calls a day; at 3,712 per
successful call that is roughly $7.5 a day before uncertain bills, against edition 1's $3.47 at
144 ticks a day. The difference is calls per tick (14 here against about 9), not price per
call: the judges and meta seats were woken more. The endowment schedule in
`worlds/edition2-testnet.toml` was sized to $3.47 a day; at $7.5 a day the $30 genesis tranche
lasts four days and a weekly $10 tranche about a day and a third, so the population would spend
most of each week dormant. Either the schedule or the roster's cadence should change before the
funded manifest; both are manifest edits.

## Run 3: the ratified edition, flat account, quiet machine

After the decisions of the morning (`docs/launch-decisions.md`, "Edition 2"): three cards and
five norms ratified, observer on DeepSeek 4.1 flash, bills settled from the provider's balance,
OpenRouter pinned to hosts that honour JSON mode, the wake publishing every answer, and the
inherited testnet positions closed at market beforehand.

| | |
|---|---|
| Manifest | `worlds/edition2-rehearsal-3.toml`, namespace `b2ee65d7…`; code `9c4dcb8` |
| Wall clock | launch 10:25 local, 30 min to kill |
| Ledger | `runs/e2-rehearsal-3.jsonl`, verify true, conservation true |
| Tick gaps (s) | 600.010, 600.009, 600.010 |
| Events / decisions / invocations | 124 / 60 / 38 (22 noops) |
| Invocation status | 36 ok, 1 malformed, 1 failed (well-formed 0.947) |
| Spend | successful calls 177,953 µUSD (4,943 per call); the one uncertain bill settled at 3,097 |
| Orders / tool calls / registrations | 0 / 0 / 0 |

**The bill fix works live.** The one provider error (`antagonist-a`, Venice) was charged at its
ceiling and then settled from the provider's balance read to 3,097 µUSD, ledgered as
`wallet.settle_uncertain`. In runs 1 and 2 the same event cost 17,000 to 82,000 each.

**The rationales changed character.** Thirteen producer wakes, thirteen holds, as before, but no
rationale quotes a card any more. They read the market: "a 0.16% move, inside noise", "the fourth
print in the series, a slow drift of 0.26% over 1800 s with no acceleration", "a perp entry
would need to clear the $10 minimum notional", "no edge identified in this window". The decider
carries small buy and sell probabilities on BTC instead of a flat hold. On a flat account with
prices drifting a quarter of a percent in half an hour, that is judgement, not compliance.
Still no tool calls: nobody paid to look at candles or the book. Whether that changes over days
is the experiment's question, not a rehearsal's.

**Cost per successful call rose to 4,943** from 3,700: the observer's new model is dearer per
token than GLM and the rationales are longer. About $7 a day at this cadence.

## Run 4: after the second reading's repairs, 20 minutes

With the three repair pull requests merged (#86 economy, #87 diagnostics, #88 identity and
lifecycle) on top of run 3's edition. Same charter, same roster, same schedule; only the code
under them changed. `--duration 30m` buys three ticks at the ten-minute interval, so the run
ends after the third (the CLI's documented rule), twenty minutes after launch.

| | |
|---|---|
| Manifest | `worlds/edition2-rehearsal-4.toml`, namespace `f86b4da2…`; code `4d5ae0e` |
| Wall clock | launch 13:07 local, killed 13:28 (`explicit_kill:budget`, the duration) |
| Ledger | `runs/edition2-rehearsal-4.jsonl`, verify true, conservation true, 2,258 items |
| Tick gaps (s) | 600.010, 600.002 |
| Events / decisions / invocations | 115 / 100 / 31 |
| Invocation status | 30 ok, 1 failed (`antagonist-a`, Venice provider error, charged 0) |
| Spend | successful calls 117,745 µUSD (3,924 per call); OpenRouter 60,826, Venice 56,919 |
| Producer wakes | 11: observer 7, antagonist 3, decider 1; 10 holds, 1 failed |
| Orders / tool calls / registrations | 0 / 0 / 0 |
| Witness | `.witness/edition2-rehearsal-4.jsonl`, one kill line naming the diary, nonce, head and release digest |

**Nothing regressed.** Ticks exact, accounting conserved, the diary verifies, the kill line
written. The Venice failure cost nothing this time (the error came before any charge, so there
was no uncertain bill to settle). Rationales still read the market: "a single mid drift of
18 bp on a 10-minute tick", "identical to the prior print, zero drift", "positive rate plus
positive premium". One decider rationale still names a card ("keeps the card penalty…"), the
only one of eleven.

**Two things the run showed that are not regressions but matter for launch.**

1. *The diary is mostly the venue's listing.* 21 of 29 MB are 80 recorded reads of
   `exchange.instruments`, about 260 KB each: the prompt builder reads the venue's full listing
   every time it builds a request and the recorded-I/O layer keeps every copy. Run 3 had the
   same (105 reads, 27 MB). At the funded cadence that is one to two gigabytes of diary a day,
   all one listing, and the backup copies it. Fixed in #89: one read per tick for prompt
   building, dropped at each checkpoint so a replayed tail asks the venue where the recorded
   one did (the population's own paid `venue.instruments` call is untouched).
2. *The wake's pots and entitlements views are empty in a short run.* They come from the public
   snapshot the runtime writes at each price-window close, and the window is one hour. Runs 2
   and 3 had the same. In the funded world they fill hourly.

The commons release, the entitlement dormancy and the artifact and facilitator checks from #88
did not fire: no seat ran out of entitlement, no restore happened. They are exercised by
`tests/audit/test_r2a_lifecycle.py` and the slice test, both green on this commit.

## Seat calibration under the edition 2 prompt

`scripts/calibrate_seats.py --world worlds/edition2-testnet.toml --all-menu --paid --budget-usd 3
--repeats 2`, run at 03:30 on a quiet machine: every model on the menu through the seven
scenarios twice (produce, judge, meta, tool use, a task that must be refused, a continuation,
a long context), each a real rendered request through `Runtime._invoke` with the scripted
exchange, costs from the meter. Spent 667,183 µUSD of the 3,000,000 cap; no call refused by
the cap. Full rows in `docs/audits/v5/calibration/edition2-menu.json`.

| Candidate | Seats using it | Well-formed | Task met | Refusal ok | Cost p50 µ$ | p95 | Cached |
|---|---|---|---|---|---|---|---|
| deepseek/deepseek-v4.1-flash | none | 100% | 100% | 100% | 2,182 | 12,117 | 62% |
| venice:z-ai-glm-5-3-flash | eval-a | 93% | 93% | 100% | 3,669 | 5,560 | 67% |
| qwen/qwen3.8-flash | eval-b | 86% | 79% | 100% | 935 | 3,995 | 83% |
| venice:qwen-3-8-flash | antagonist-a, meta-b | 86% | 86% | 100% | 1,562 | 3,902 | 71% |
| z-ai/glm-5.3-flash | seed-observer | 79% | 79% | 100% | 2,951 | 7,121 | 0% |
| openai/gpt-5.6-luna | eval-d | 79% | 79% | 100% | 6,729 | 27,038 | 0% |
| venice:deepseek-v4-1-flash | seed-decider, eval-c, meta-a | 71% | 71% | 100% | 1,614 | 5,992 | 86% |
| qwen/qwen3.7-flash | none | 71% | 64% | 0% | 339 | 4,659 | 62% |
| deepseek/deepseek-v4-flash-0731 | none | 57% | 14% | 50% | 372 | 530 | 86% |
| meta/muse-spark-1.3 | none | not measured | | | | | |

Reading the failures, not the percentages: every "refused" row is the deliberate failing task
answered correctly (the order named does not exist), so "refusal ok" is the column that carries
it. The well-formed losses are of three kinds. Malformed replies: GLM 5.3 flash on OpenRouter 3,
DeepSeek 0731 4, Qwen 3.7 2, Qwen 3.8 1 (Venice's GLM had none). Provider errors with no HTTP
status, billed at the ceiling as `billing uncertain`: eleven across OpenRouter and Venice, on a
quiet machine, so they are not only the load that confounded run 1. And harness refusals: the
harness endows each candidate seat with 85,714 µUSD, below the long-context ceiling of the
dearest models, so 17 trees were refused by the seat's own entitlement before any call (all 14
of Muse Spark's, which is why it is unmeasured, plus one GPT 5.6 and two Venice DeepSeek); a
rerun with a larger per-seat grant would measure those.

What it says about the roster: `seed-observer` on GLM 5.3 flash through OpenRouter is the worst
seat on the roster for shape (79%, and the wrapper failure seen in run 1); the same model through
Venice is at 93% with a cache hit, and DeepSeek 4.1 flash on OpenRouter is the only candidate at
100% on every column. The three Venice DeepSeek seats are at 71% mostly from provider errors, not
malformed text. Changing a seat's model changes the roster hash and needs a re-ratification (one
ballot, a third of a cent).

## What this does and does not show

Execution and feedback at the declared tick, with the edition 2 kernel live on a real venue and
two real providers: the endowment split, per-seat metering, both cost observations, the release
schedule, the ratified eight-card charter. It is not a profitability, convergence or survival
claim; no orders were placed in any run. The three decisions the first runs raised were taken
and run 3 rehearses them; what remains before the funded manifest is the outside reviewer's
second reading and the operator steps in `docs/handoff.md`.
