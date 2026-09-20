# Context release: bounded testnet validation

The user authorized pushing, reviewing, cleaning and running this release. This is
a smoke experiment on the repaired interfaces, not a matched demonstration of
learning or Class 3 behavior.

Predeclared bounds: fresh world; Hyperliquid testnet; ten minutes with ten-second
declared ticks; at most 120 provider calls and $1.50 additional inference. Preserve
reasoning, roster, norms and economic endowments. Use compact context, realized
feedback and voluntary addressing. No treasury transfers, x402 purchases, mainnet,
top-ups, live diary inspection or parameter changes. The source is archived from
the pushed commit before launch. The earlier $0.022544 uncertain bill remains
reserved independently; the cumulative authorization is $50.

Mechanical questions: do completed requests stay affordable, can schemas and inbox
bodies be retrieved, do optional failures remain distinguishable from choices, and
are feedback outcomes addressed correctly? Behavioral questions remain separate:
does anyone voluntarily communicate, build, use another participant's output, or
change a costly decision after evidence? No reward for producing those events is
added. The existing coverage threshold remains 60 delivered ticks, ten assessed
grounded outcomes and at least one contrary outcome. Missing coverage means
inconclusive, regardless of how many calls succeeded.

Inspect the diary only after termination. Retain all billed attempts, reserve any
new unknown ceiling, verify terminal wallet conservation and venue positions/orders,
and publish only a sanitized summary. Raw ledgers, credentials and exported events
remain local.

## First smoke: 6061107

The frozen source completed with 13 delivered ticks and 39 provider attempts.
Known inference was 118,898 micro-USD; one Venice transport error left a dispatched
bill uncertain, so 8,020 micro-USD remains reserved and the harness terminated.
No reported overrun occurred. Ledger verification and wallet conservation passed.
The testnet venue was flat before and after: no positions, open orders, or intents.

The population completed 24 tool calls with no recorded tool failures: six
`world.read`, eleven `outcome.get`, one `venue.funding`, two `catalogue.search`, two
`venue.funding_history`, and two `artifact.get`. Of 28 completed invocation records,
26 were OK, one malformed (reasoning-only), and one failed (uncertain provider bill).
These records contain eleven continuation calls across five tool-using decisions;
four decisions used multi-tool chains. One twelve-tool retrieval chain ended in the
reasoning-only malformed answer. Do not divide spend by invocation count and call it
cost per model call. Seven subscription changes, seven deferrals, 24 working-state
updates and two outcome acknowledgments occurred. There were no accepted child
requests, addressed messages, notes, registrations or order intents. A final
`unknown` feedback item was delivered but remained unacknowledged with no valid
subsequent return. Three evaluations were unmeasured. The refused finding shape was downstream of
the Venice failure (no finding arrived), not a malformed judge decision. The absence of cooperation is not conclusive at this coverage.

Final-request sizes ranged up to 45,198 bytes for ordinary seats and 55,079 for
judges. Context is smaller and retrieval is exercised, but meaningful request cost
still includes every retrieval continuation. There were zero assessed grounded
samples; 13/60 required ticks and 0/10 assessed samples do not meet the predeclared
coverage contract. This is an inconclusive behavioral experiment.

A Codex PR review during this frozen run found repeated routing-bridge eligibility
on continuations. Commit `1f601bd` restricts the bridge to the routed root call and
caps the remaining chain to the liable seat's cover. The first smoke therefore
cannot validate that repaired spending boundary; it recorded zero bridges, so that
latent defect did not explain its observed behavior. A fresh smoke on `1f601bd` uses
the same ten-minute, $1.50, 120-call limits and unchanged coverage thresholds.
Its diary remains sealed until termination.

## Corrected spending boundary: 1f601bd

The second ten-minute smoke completed 14 ticks and 36 provider calls, costing
109,293 micro-USD, all authoritative. No overrun or unknown bill occurred. All
11 dispatched tools succeeded (eight `world.read`, three `outcome.get`). Wallet
conservation and ledger verification passed; no order intents, positions or open
orders remained. The harness stopped at its duration bound.

There were 31 invocation records: 27 OK and four malformed. Two failures were
length/reasoning-only; one tool-only answer omitted the required order-book
`depth`, and another requested five tools against a four-tool limit. No valid core
answer accompanied those rejected batches.
The models' attempted operations are not counted as executed actions. One optional
working-state section was rejected without invalidating the answer.

Of 15 unique producer decisions, 13 were valid; 19 emissions included four
re-emissions. A supported final finding settled and was addressed to its producer,
which later returned a valid defer. It was not acknowledged, and actual model
exposure was unknown; this is not evidence of learning. A second finding was
unknown. Coverage remains inconclusive: 14/60 ticks, 1/10 assessed samples and no
contrary sample. Measured actual tick interval was about 43.5 seconds despite the
ten-second requested cadence; serial inference dominated the critical path.

A further review found that fetching a high-numbered inbox item could skip earlier
unseen feedback at acknowledgement. Commit `3450176` preserves sparse delivery gaps
and checkpoints them. Both gap and recovery regressions pass. A final three-minute
interface smoke on that exact source is capped at $0.50/40 calls. Its declared tick
ceiling is 18, not a replacement for the ten-minute arm's 60-tick coverage contract;
it is a release smoke only and cannot establish behavior.

## Delivery-gap repair smoke: 3450176

The short smoke finished with six ticks, 17 authoritative provider bills and
50,560 micro-USD of inference. Four tools executed successfully (three `world.read`,
one `outcome.get`). Fourteen invocation records were OK; one was malformed after a
reasoning-only response. Ledger verification and wallet conservation passed. There
were no order intents, and the venue remained flat. No grounded finding matured;
behavior remains inconclusive.

Across these three smokes: 92 provider attempts, 39 successful dispatched tools,
278,751 micro-USD known spend plus 8,020 micro-USD newly reserved uncertainty. The
cumulative inference ledger also retains the earlier 22,544 micro-USD unknown bill.
No mainnet operation, treasury transfer or top-up was made. All worlds terminated;
no background rehearsal remains. Raw diaries and reports remain local under `work/`.

The final PR review also identified a caller input named `actor_context` being
silently omitted when an ordinary request already had `world`. That preservation
fix and removal of a redundant grading instruction from the operating-context header
followed the last live snapshot. They are verified by focused regressions and the
check/gate suite, not by these earlier paid calls. They do not change the economic
or delivery contracts exercised above.

## Release interpretation

The interfaces exercised here work; this is not a claim that every capability was
exercised live. Offline tests cover sparse inbox recovery, child funding, context
recovery and affordability boundaries. Live runs establish actual schema discovery,
retrieval and safe termination. They do not establish reliable model compliance,
collaboration, independent income or autonomous objective formation.

Before a behavioral launch, close the observed coverage gap: ten-second configured
ticks produced roughly 33–47-second measured intervals, largely from serial model
calls. A meaningful comparison must deliver enough ticks to close consequences,
then measure useful completed decisions including all retrieval bills. More runtime
features would not make these short samples conclusive.
