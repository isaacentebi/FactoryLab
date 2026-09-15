# Edition 2 testnet rehearsals at the ten-minute tick

Two live Hyperliquid-testnet runs of the edition 2 world on 15 September 2026, both from
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

_Pending: filled from `runs/e2-rehearsal-2.jsonl` when the run ends._

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
claim; no orders were placed in either run.
