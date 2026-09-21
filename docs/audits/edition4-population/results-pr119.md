# PR119: trading worked, evaluation remained impaired

The world on frozen source `a874f1e15901754ebaa5f4e6497612861f3ce1b0`
terminated and sealed after 200 of 240 planned ticks, 1,282 sampled decisions and
460 provider calls. Known inference was $1.259488, with no new uncertain bills.
The wall-clock limit ended the run; neither the $5 cap nor 1,000-call cap bound it.
The generic terminal reason `explicit_kill:budget` is not evidence of depleted money.
There was no provider exception, so isolated-error recovery was not exercised live.

## Action and remaining exposure

Mechanism decision-84 submitted a 0.005 BTC short and a 0.15 ETH long through
`venue.place_market` (tool receipts seq 2267 and 2281). Both filled. These were two
legs of one decision, not two independently discovered strategies. The summary's
legacy `orders_placed=0` counter does not count these tool-submitted orders;
the execution report and attributable tool receipts do.

Shutdown attempted both closes. ETH closed at 2657.8, venue order 60631897860.
BTC close `wd-56915012a68e293958030f08` returned `uncertain`: a ReadTimeout on
submission followed by "order not observed" on lookup (seq 33742). Wind-down
reported `wind_down_pending`, not flat. The world nevertheless sealed, as finality
does not depend on venue success.

An independent read after the run confirmed the remaining BTC position was -0.005
at entry 81732.0, with no resting orders. Equity was 964.975590865 testnet USD at
terminal reporting and 942.760013865 at the later independent read; these are
different observation times and are not a clean realized P&L comparison. Existing
0.00967 HYPE/USDC remained within the precommitted dust bound. Under separate explicit
user authorization, the original BTC close was reconciled (still not observed), then
one reduce-only recovery order closed 0.005 BTC at 86533.0, order 60677048880.
A fresh snapshot confirmed no perpetual positions or open orders, equity 942.352314865.
This operator action is outside the sealed experiment and is not population behavior.
The population's two opening fills must not be mixed with the shutdown close.

## What still prevents a behavioral conclusion

Only two grounded findings were scored: one supported and one contrary. There
were 25 unknown findings, 42 censored grounded contracts and nine outstanding;
19 decisions of all kinds remained outstanding. These categories are not a single
disjoint partition. Coverage failed both the tick target and the ten-assessment
minimum despite authoritative bills. This is not evidence of Class 3.

Forty-two model responses stopped at their output limit and failed JSON parsing:
35 from judge-fidelity and seven from meta-countercase. For example, decision-6
used all 4,096 output tokens as reported reasoning tokens, returned empty text,
and failed with "answer is not a JSON object". This is answer starvation, not
a judgment that the producer's action was bad. There were 46 malformed invocations
in total; the other four had enum/type/required-field faults. Passing offline
schema tests cannot prove that a live model has enough answer allowance. These failures
damaged judging throughput; they do not directly explain the 25 delivered unknown
findings. Most evaluated hold/defer claims lacked a falsifiable consequence: settled
zero net income neither demonstrated usefulness nor contradicted a specific promise.
The supported finding concerned decision-84 execution receipts; the contrary finding
concerned decision-232 claimed carry versus zero settled earnings. Only two of 27
final chains were acknowledged. The two scored findings reached inboxes without
acknowledgement; later actions cannot be causally attributed to their exposure.

All 74 dispatched tools succeeded: 62 outcome reads, four world reads, two funding-
history reads, two order-book reads, two market orders, one funding read and one
outcome listing. This is a material difference from the earlier run's blocked
market investigations, but market conditions, sampled actions and run length differ.
It is not a controlled causal estimate of any individual repair. Before dispatch,
decision-35 omitted order-book depth and decision-42 omitted funding-history n;
decision-84 later supplied both correctly. This demonstrates operational recovery,
not that error feedback caused it. There were 192 distinct valid producer decisions
(169 declared hold, 21 defer, two investigate); 112 return replays are not new actions.
Decision-84 declared hold despite trading, illustrating why executed tools matter
more than the action label. No messages, notes, artifacts, searches, funded births or
cross-seat artifact reuse occurred. Subscription changes reused existing capabilities.

No population registrations, observations, amendments, votes or charter activations
were recorded. The antagonist settled 91 exposures without a win. At the first
immune-window snapshot the registration route was unavailable because the novelty
reserve window had nothing left; revision also depended on that route. That is a
time-local affordability observation, not proof that registration was impossible
throughout the run. Lack of production must not be attributed solely to unwillingness.

## Next warranted work

The separately authorized residual-position recovery is complete.
Next reproduce evaluator answer starvation offline using the recorded limits and
provider-response shape. Preserve reasoning rather than returning to the old patch
of disabling it. Any allowance change must still reserve a payable ceiling and be
tested as an isolated model-interface change before another population run.

The economic interpretation also needs the reserve-exhaustion timeline and actual
feedback exposure. Neither more trading nor more model calls alone resolves those
questions. Do not add institutions merely to produce visible activity.

## Accounting and verification

The $5 active reservation was released exactly once. Cumulative known inference is
$8.379634; prior uncertainty remains $0.046446; unreserved authorization is
$41.573920. No mainnet action, top-up, transfer or deployment occurred.

The terminal report was read first and termination confirmed before diary access.
Both existing terminal analyzers completed successfully. `results-pr119.json`
records aggregate evidence and hashes of the private report, diary, analyses and
independent venue snapshot. Raw data remain under `work/population-pr119/`.
