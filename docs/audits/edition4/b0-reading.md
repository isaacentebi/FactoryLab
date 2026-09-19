# B0 reading: repaired baseline, 30 minutes at a declared 10-second tick

Empiricist seat, read-only. Sources: `/tmp/factorylab-edition4-b0-01/report.json` and
`events.json` (10,106 items) only. No ledger key, no `*.key`, no live calls, no source reads
beyond what the run itself records. Frozen source `7dd212a`; world
`edition3-rehearsal-5.toml` with tick 120 s → 10 s, treasury rails denied, fresh client
namespace. Roster and charter hashes preserved.

## What the run was

55 ticks, 615 events, 322 routed decisions, 113 model invocations (109 ok, 4 malformed),
118 paid calls, **$0.6567** ($0.0056/call). Declared tick 10 s; **mean measured interval
32.7 s** across the run — the tick overran its declared period on average; I did not
verify the per-tick distribution, so the overrun may be uniform or concentrated.
Terminated `explicit_kill:budget`; seal released; wallet conservation and ledger verify
true. Venue custody booked −4,863 µUSD with `unattributed_micro: -4863` — the fee was
booked to venue custody but **not attributed to any seat's claim**. One confound: venue
equity was $966 against a declared $120 principal — the account carries more than the
world declares.

## The behavioural tally, corrected

60 producer-role returns. The headline "51 hold/defer, 3 investigate, 1 order, 5 missing"
needs its denominator and its missing five explained:

- The **antagonist is 34 of the 60** (27 hold, 7 defer). The four true producers answered
  26 times.
- The **5 "missing actions"**: four are *defers* — decision-166, -322, -365, -369 are
  `status: ok` returns whose `outputs` string is truncated mid-rationale **in the
  events.json export**; the returns themselves settled and received verdicts (0.70, 0.86,
  0.60, and a settled decision-369). They are not malformed, not `cannot`, not voided
  contracts — they are defers with truncated export text. The fifth, decision-159
  (mechanism), is a genuine malformed: `finish_reason: length`, cut off mid-rationale
  while emitting an `investigate` with two `venue.order_book` calls.
- Honest tally: **55/60 hold-or-defer (92%)**, 4 investigates (one malformed), 1 order.
- Among true producers: a strict count over cleanly parsed action fields gives
  **17/26 declines (65%)** — 13 defer + 4 hold. But the four export-truncated returns
  are all true-producer defers (mechanism ×2, constructor ×2), so the recovered
  classification is **21/26 declines (81%)**. 17/26 is the lower bound; 21/26 is the
  correct figure once the truncated outputs are read rather than dropped.

## What the repairs changed — and did not

Changed, visibly:

- **Malformed collapsed**: 4/113 vs 58/323 in run 5 — mechanism-159 (`length`),
  judge-fidelity-194 (`length`), meta-countercase-281 (`length`), judge-fidelity-295
  (`stop`, unparseable raw). GLM's reasoning-only empty-content failures are gone.
  Separately, three `return.sections_dropped` events (empirical-18, mechanism-26,
  mechanism-42 — `funding_history` missing `n`, `order_book` missing `depth`) show
  per-section validation doing its job on *ok* returns: decision-42's bad
  `order_book` call was dropped while the rest of the return stood, and the seat read
  the refusal and repaired the argument next wake. These dropped optional fields are
  not malformed answers and are not counted among the four.
- **Empty draws no longer manufacture work**: 19 `inapplicable` verdict-channel settles
  on handles with no invocation (decision-104, -110, …) — the 209 NOOP draws settle
  inapplicable instead of producing fake producer returns for judges to grade.
- **Outcomes are being read**: 161 `outcome.addressed`, 7 `outcome.ack`, and rationales
  that cite outcome ids verbatim ("outcome:31 … settled, cost 2555 micro,
  return_paid_off 0", "the prior return's venue.funding_history calls were dropped for a
  missing n argument (outcome:17)").
- **The wind-down works**: production killed, one close op with durable id, filled at
  2623.9, `dust_within_precommitted_bound`, witness summary written.

Unchanged or partially changed:

- **The verdict gradient, with one important exception.** Consequence-judge verdicts on
  producer actions: defer ≈ 0.72–0.86, hold ≈ 0.70, investigate 0.567 (n=3), order 0.78
  (n=1). Caution still outscores investigation there. **But judge-fidelity escalated
  against repeated passive holds**: 0.55 on decision-285 (antagonist hold on a flat
  PURR print), 0.50 on decision-300 (antagonist hold on an unchanged HYPE series), and
  **0.45 on decision-313 with a formal `fidelity_objection`** — the objection names
  the exact failure: across six judged wakes the same producer held while the 0.005 ETH
  long "went unmanaged, with no submitted read or position action," and "no card
  records this pattern — the settled forecasts only price fill_within/wallet_up
  outcomes, which do not distinguish an unmanaged open position from a flat account."
  So the claim "caution is always rewarded" is too strong: consequence verdicts still
  pay declines, but the fidelity judge detected position-neglect, priced it down
  0.55→0.50→0.45, and filed the mechanism's first observed objection that the metric
  cannot see the failure it found. That is the evaluation layer working as designed —
  and simultaneously documenting that the card set is blind to the behaviour.
- **The spend shape.** Antagonist $0.232 (35%), judges $0.262 (40%), metas $0.027 (4%):
  **~80% of spend on the evaluation layer**, producers 20%. The antagonist remains the
  most expensive seat and attacked zero times.
- **The drift.** Antagonist lens decayed to "Defer routine BTC MarketMid processing; no
  material BTC exposure or safety event is evidenced"; opportunity's to "Defer routine
  WorldUpdate processing while retaining the open ETH position." Same quiescence
  attractor, now written in scheduling language. The antagonist's returns are degenerate:
  `{"action":"hold","payoff":0.5,"propensity":{"hold":1}}`, no rationale — and it is
  these bare holds the fidelity judge began pricing down.
- **Timeouts: outstanding measurement, not a verified defect.** 58 `decision.timeout`
  settles, concentrated on antagonist (31) and judge (21) handles. On a 30-minute world
  whose consequence backstop is 60 ticks, some expiry is expected arithmetic; whether
  the rate is right is a causal question I did not verify against the settlement code.
  74 `forecast.refused`, correctly: "the return's outcome is already fixed" — you
  cannot forecast the P&L of a hold. `paid_off` 0, `not_paid_off` 112.

## The one thing that was new

The order was **empirical's decision-54: long 0.005 ETH perp at 2616.9** — the other leg
of the same funding-carry thesis (negative ETH funding → longs receive), not a new idea.
The process around it was the most complete hypothesis-test sequence in the records I
read: empirical reserved the test in working state at decision-13, pulled funding
history on both coins, derived the 3600 s settlement interval from timestamps, confirmed
four consecutive negative settlements, then placed the order. Filled; fee $0.0059; one
funding receipt +$0.001; closed by wind-down at 2623.9 for ~+$0.03 net. Mechanism ran a
parallel investigation of the same structure (long-ETH/short-BTC), repairing its own
dropped tool args between wakes. Constructor then tracked the open position across
wakes and priced the mark in its rationales — a seat reasoning about another seat's
position on the shared account, the closest thing to inter-seat awareness in the runs
I have read.

So: same thesis family, opposite direction, cleaner science. The population found the
second leg of the only cash flow it can see.

## Reading

Baseline fact, not Class 3: the repaired build produces **well-formed, evidence-citing,
self-correcting declines** — and, for the first time in the records I read, an
evaluation layer that noticed. The plumbing fixes are real and visible in the diary
(malformed 4 vs 58, inapplicable settles, readable outcomes, clean wind-down). The
gradient is mostly untouched: consequence verdicts still pay declines, nothing
forecasts a hold, and 80% of money still buys judging of a population that mostly
waits. The fidelity objection is the run's most interesting single artifact: a judge
found the exact pathology (passive neglect of an open position), priced it down across
three verdicts, and recorded that no metric in the charter can see it — an evaluation
organ reporting its own blind spot, which is the essay's fidelity norm operating
correctly and arriving too late to matter inside 55 ticks.

The numbers are consistent with the figure I gave the committee before this run
(≥85% hold/defer, ≤4 orders all funding-carry, zero registrations/searches) — stated
here as agreement with my earlier estimate, not as a sealed prediction. The one
surprise worth naming: the order was a *long*, the investigate rate among true
producers (3–4 of 26) is higher than run 5's, and the fidelity escalation is new —
all three plausibly the uncensored judge signal starting to differentiate, too early
to tell at these n.

## Numbers for the committee

| | |
|---|---|
| Producer-role returns | 60 (antagonist 34, producers 26) |
| hold/defer | 55/60 = 92%; producers only: 21/26 = 81% (17/26 = 65% counting cleanly parsed actions only) |
| investigate | 4 (1 malformed `length`) |
| orders | 1 filled (ETH long 0.005 @ 2616.9, wind-down close @ 2623.9) |
| registrations / governance / searches / notes | 0 / 0 / 0 / 0 |
| consequence verdict means | defer ≈ 0.75, hold 0.70, investigate 0.57, order 0.78 (n=1) |
| fidelity verdicts on repeated bare holds | 0.55 → 0.50 → 0.45 + fidelity_objection |
| eval-layer spend (incl. antagonist) | ~80% of $0.657 |
| decision.timeout | 58 (antagonist 31, judges 21) — causal status unverified |
| forecasts | 65 sealed, 62 settled, 74 refused (outcome already fixed) |
| malformed | 4/113 (vs 58/323 run 5); sections_dropped 3 (separate, ok returns) |
| measured tick | mean 32.7 s vs declared 10 s (per-tick distribution not verified) |
| venue custody | booked −4,863 µUSD, **unattributed** |
| judge standing | fidelity skill −0.014, consequence −0.018, coverage ~0.82 |
