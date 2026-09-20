# Coverage attempt on 55ea7d7

The run completed and the world terminated before its diary was inspected. Source
55ea7d78609a3f71d8d3d9b45c0584dd13dc2f8e is the code merged in PR112
(055b192ecdfc2d21f35a8b66db82094a8c1a86c5). This was an unsuccessful coverage
attempt, not a successful 60-tick behavioral experiment.

## Result

- 25 of 60 requested ticks, 188 sampled decisions, 84 provider attempts.
- Stop: `unknown_bill_after_dispatch`; 83 known bills total 261,357 micro-USD.
  The wrapper reserved an 8,197 micro-USD ceiling for the last attempt. Postmortem
  inspection established HTTP 402: the core meter marked it unbilled and released
  the entire reservation. Neither the $3 admission cap nor the 300-call cap was
  exhausted. The false uncertainty stopped the experiment nonetheless.
- The declared tick was 10 seconds; the measured mean gap was about 51.3 seconds.
  Model latency still limits delivered time. Shortening the tick alone does not
  create the requested experimental coverage.
- Five assessed grounded findings: three supported and two contrary. Two more
  findings were unknown. Fourteen decisions remained outstanding. The
  predeclared screen is inconclusive: insufficient ticks and assessed findings,
  and a wrapper-reported unresolved bill (later established unbilled).
- Ledger verification and wallet conservation passed. These checks do not prove
  that shutdown flattened venue exposure: the terminal venue snapshot contained
  a BTC short of 0.00013, entry 80,763, with no open orders. ETH was closed during wind-down. BTC close submission timed out, and order
  lookup did not observe it; the executor recorded `wind_down_pending` without
  blindly resubmitting. The runner reports `positions_closed_at_exit: false`. Do not start another world assuming
  a flat account.

## What the population actually did

Fourteen tools executed without tool-call failures: eight `world.read`, four
`outcome.get`, and two `venue.place_market`. Both order intents filled. No
registration, participant creation, amendment, or participant-to-participant
message was observed. No independent income buying inference was demonstrated.

There were 33 unique producer decisions: 24 classified holds, two defers, two
investigations and five malformed returns. Re-emitted contracts are excluded.
These descriptive counts are not a behavioral comparison: the run failed its
coverage contract, and reliability failures affected which seats could act.

Nine malformed answers generated addressed rejection feedback. One empirical
participant acknowledged a rejection and its next return was valid. An oversized
five-tool batch received the explicit four-tool limit; its next return was valid,
but that rejection was not acknowledged. Neither observation establishes causal
learning. Four provider faults exhausted the completion allowance in hidden
reasoning, with no visible answer. Reasoning settings were preserved.

Seven final findings were published and addressed to producer inboxes. None had
an observed acknowledgement. Four had a later valid return; three had no later
wake. Addressed delivery is therefore demonstrated, but comprehension and
adaptation to consequence feedback remain unproven.

## Context and economics

Reported input tokens for final invocations, including retrieved evidence, were:

| Role | Known token observations | Median | Maximum |
| --- | ---: | ---: | ---: |
| Producer | 33 | 13,526 | 16,272 |
| Evaluator | 38 | 13,108.5 | 17,657 |
| Meta | 5 | 6,064 | 12,402 |

These are provider token counts, not character estimates, and exclude intermediate
continuation calls as separate observations. They are not a matched comparison
against the prior prompt. Retrieval works, but this evidence does not justify
calling context optimized or small. Known cost per billed attempt was about
$0.00315; cost per useful decision is not established by that average.

Cumulative known experimental inference is $4.932683, with $0.030564 reserved for
prior uncertain bills. Remaining unreserved authorization is $45.036753 of $50. The
budget ledger retains the residual venue state and releases the unused $3 cap.

## Evidence and limits

Private evidence: `work/coverage-60-r1/live/report.json`, `events.json`, and the
postmortem report generated only after termination. Public results deliberately
omit raw participant prose and the diary. The run used testnet only, no observer,
no mainnet, no transfers or top-ups. PR112's tested code passed Ruff, 2,190 check
tests and four affected gates; the live result does not replace those checks.

The wrapper now uses the same `classify_provider_failure()` as the core meter.
A regression distinguishes confirmed unbilled HTTP 402 from an ambiguous dispatched
failure: the former continues; the latter retains its ceiling and stops admission.
No billing protection was relaxed. The next experiment must also resolve terminal
exposure and provider credit availability. Increasing capital, adding a forum, or interpreting silence
as an endogenous objective would not address these observed interruptions.

A subsequent read-only lookup of the original BTC wind-down identity still returned
`uncertain: order not observed`; the fresh account snapshot still showed the same
short and no open orders. No duplicate close was submitted. The sealed world was
not reopened and no new population was launched. Postmortem wrapper repair passed
Ruff, 2,191 check tests in 30.62 seconds, and the affected rehearsal gate in 0.70
seconds. These validate the repair offline; it has not been tested in another live
population.
