# Repaired population: 240 ticks

The population completed the predeclared run on merged source
`53d13136921d690edc2498b8279e1e1214d22280`: 240 ticks, 623 provider calls,
$1.500198 in authoritative inference bills, no uncertain bills, no cap refusals.
The $3 ceiling was not binding. The frozen runtime and parameters were unchanged
throughout; analysis began only after `summary.terminated` was true.

The coverage screen passed: 41 assessed grounded findings (8 supported,
33 contrary), 25 unknown, 62 censored and 9 outstanding grounded decisions.
These are coverage categories, not proof of learning or independent purpose.
The canonical behavioral trace still marks mechanics inconclusive because invalid
returns and incomplete feedback exposure remain. A passed coverage screen and a
failed behavioral trace answer different questions.

## Attempts, execution and no trade

There were 264 distinct producer returns, including antagonist ProducerReturns:
219 valid and 45 malformed. Valid declared actions were 148 holds, 30 defers,
31 investigations and 10 builds. Declaring a build is not producing a working
artifact. Re-emitted contracts are excluded from these counts.

All 66 dispatched tools succeeded: 34 outcome.get, 12 catalogue.search,
10 world.read, 6 outcome.list, 3 artifact.get and 1 venue.funding. Another
75 tool sections were rejected before dispatch: 54 lacked order-book depth;
21 lacked the funding-history sample count n. These are rejected atomic batches,
not 75 individual calls. Forty-four malformed producer returns came from empirical.

The empirical participant tried to evaluate long ETH/short BTC funding capture,
conditional on spreads, fees and persistence. The prerequisite data requests
repeatedly failed validation. Its late decision-1684 still asked to establish
costs before trading. No order or fill occurred. This shows an interrupted
investigation, not that the trade would have been profitable or should be forced.
Opportunity mostly waited for observation-window evidence; mechanism retained
funding questions. Their inactivity cannot all be attributed to the same cause.

Constructor submitted three predicate programs. Decision-1010 was rejected
because no closed window existed for preflight; decision-1203 and the subsequent
attempt failed because the programs indexed price samples as dictionaries when
those samples were lists. No predicate was admitted or reused. No participant
birth, direct message, service income or governance activation was observed.

## Feedback and judging

The run produced addressed consequences, but delivery, retrieval and changed
behavior must be distinguished. Empirical fetched outcomes at decisions 50 and
66, then no later outcome.get is recorded. Its first malformed data request was
at decision-186 (seq 4813), long after those reads. Its inbox eventually held
215 addressed outcomes and its acknowledgement cursor remained at outcome:4.
The current prompt exposed only the oldest eight unread indices; detailed tool
errors stayed behind body retrieval. Repetition is therefore not evidence that
it read and ignored the exact argument error.

The antagonist ran 127 times and settled 98 exposures, with zero judge failures
or verdict exposures. Those costs do not establish adversarial value. The
judging-path audit accompanies the subsequent repair; assessed contrary findings
must not be treated as realized trading losses. No trade occurred.

## Cost, context and assets

Final-call input-token medians/maxima: producer 11,680.5/21,702; evaluator
13,527/15,586; meta 5,778/9,711. Context remains substantial. There were 582
invocation rows versus 623 provider calls; invocation usage represents its final
call, while invocation cost includes continuations and tools. Never sum these
costs with provider bills.

In the final-call telemetry, Luna reported zero cached tokens on all 320 rows;
GLM reported positive caching on 232 of 262 rows. This is not total-run cache
accounting and is not evidence by itself that the harness can force a cache hit.

Perpetual positions and open orders were empty at exit. Wind-down reported
`dust_within_precommitted_bound`: existing 0.00967 HYPE/USDC, valued at about
$0.353235 testnet USD, remained. The small equity change is not a trading profit:
no new orders occurred. No mainnet action, transfer, top-up or deployment occurred.

The $1.500198 bill was reconciled exactly once in the cumulative $50 ledger.
Total known experimental inference is $6.947799, prior uncertainty remains
$0.036740, and unreserved authorization is $43.015461. No new run was launched.

## Reproduction

`results.json` contains selected aggregates and SHA-256 identities for the private
terminal report, events export and both analyses. Raw bodies remain under
`work/population-120-repaired/`. Run the existing terminal analyzers only after
checking the completed report and termination flag:

```
uv run python scripts/edition4_report.py --postmortem-report work/population-120-repaired/live/report.json --events work/population-120-repaired/live/events.json --out work/population-120-repaired/postmortem
uv run python docs/audits/edition4-experiments/analyze.py work/population-120-repaired/live/report.json work/population-120-repaired/live/events.json
```

## Judging audit and repair boundary

Initial verdicts do not settle producers in realized mode. A fresh assessment
uses evidence after the observation horizon and its realized_consequence score
updates the producer router. Ordinary evaluators still optionally forecast
return_paid_off; that calibration affects their selection alongside recursive
meta-evaluation. Seventy-three forecast settlements occurred. Forecasts are not
the producer's direct reward in this mode.

However, most final evidence was an EconomicOutcome with no earnings and a
positive compute bill. Some judges scored this as failed usefulness, others as
prudent restraint. The 33 contrary findings are zero producer rewards, not
33 losing trades or falsified market forecasts. For example constructor
decision-29 settled at zero (seq 2548), while opportunity decision-83's defer
settled at 0.4 (seq 4010). These are interpretive differences over thin evidence.
Evaluators, metas and antagonist together consumed about 79% of inclusive
invocation spend. Antagonist's 127 holds and zero successful exposures do not show
that adversarial evaluation helped.

The small repair keeps norms unchanged, requires a grounded final finding, removes
unused payoff/forecast output fields from final assessments, and instructs the
judge to name the express commitment or applicable frozen criterion that observed
evidence falsifies. Zero earnings/cost alone cannot establish contrary unless
that criterion makes them relevant. Lack of assessable evidence means unknown,
which is not trained as zero. This is instruction-level mitigation: the parser
verifies citations exist, not that their semantics prove falsification. A generic
hard guarantee would require explicit participant-authored executable commitments;
we have not silently replaced the norms with profit or an architect's action rubric.

The implemented interface repair preserves the eight-item inbox bound but shows
oldest and newest unread items, preserving pagination and sparse-acknowledgement
rules. Exact rejection labels are indexed; full receipts remain private and
retrievable. Predicate disclosure now explains readiness and the actual
[timestamp_ns,value] input pairs, including micro-USD units, with a direct
world.read route. Strict tool batches and jailed predicate preflight remain.
No analytical defaults, automatic trades or automatically repaired participant
programs were introduced.

## Repair verification

Ruff passed. The final check tier passed 2,224 tests in 33.42 seconds. Relevant
gates cover return-section delivery and grounded evaluation. Independent review
caught and resolved two issues before release: newest previews must not accumulate
an unbounded delivered-sparse checkpoint, and frozen norms must remain an
independent basis for contrary findings. Recent preview indices therefore do not
acknowledge or durably deliver bodies; explicit outcome.get retains that behavior.

A metering-overrun fixture was updated to stake its full $1 wallet so prompt-size
changes cannot prevent the first ballot from reaching the deliberately excessive
$1.10 bill. No runtime money limit was changed. The predicate example test derives
the published shape from the actual window serializer. The feedback tests verify
bounded backlog display, exact error labels, private bodies, middle pagination and
acknowledgement behavior through restore. The judging tests inspect the actual
rendered commission and retain unscored unknown outcomes and independent norms.

These repairs are verified offline. No post-repair paid population was run in
this change. A subsequent controlled run should test whether rejected requests
are corrected after recent feedback becomes visible and whether final judges
stop treating zero earnings as automatic falsification. A successful code test
is not evidence that either behavior has changed. A fresh independent read also
confirmed no perpetual positions or open orders after the completed experiment.
