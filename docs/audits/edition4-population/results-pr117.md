# PR117 population: interrupted at 26 of 240 ticks

The run on frozen merged source `30e0af8fb649e650b4a8fac88e5d1eff0f63b488`
terminated and sealed after 26 ticks, 184 sampled decisions and 79 provider calls.
It did not reach its preregistered coverage. The $5 cap was not exhausted: a
dispatched provider error without an authoritative bill caused the admission
guard to stop further calls. No retry, restart or follow-on world was launched.

## Why it stopped

At decision-184, judge-consequence called `openai/gpt-5.6-luna` for the final
assessment of constructor decision-109. Provider result seq 4791 records
`OpenRouterError`, HTTP status null and `unbilled=false`. The recorded fields do
not distinguish a connection failure, response-decoding failure or other
unclassified provider failure; calling this definitely a timeout would overclaim.

Admission retained 172,347 micro-USD in 78 authoritative call bills and a
9,706-micro ceiling for the failed call. Separately, the core meter inferred
2,253 micro from the provider balance delta and released 7,453 micro of its
wallet reservation (seq 4797–4799). This explains why the wallet's final spend
is 174,600 micro while the external budget conservatively reserves 182,053.
The balance reconciliation did not clear the external admission guard. The
report's `completed` status means normal runner finalization, not completion of
240 ticks; `unknown_bill_after_dispatch` is the stopping cause.

The practical next problem is a provider failure ending the observation window,
not insufficient capital. Before buying another run, evaluate the admission
guard's recovery contract offline: preserve uncertain liability and prevent
duplicate charges while deciding whether authoritative reconciliation can permit
later, distinct work. Do not silently retry the failed request or loosen the
guard in a running world. The transport cause remains unresolved in this diary.

## What the short run establishes

All 23 dispatched tools succeeded: 17 `outcome.get`, three `artifact.get` and
three `world.read`. No venue data tool dispatched. Constructor decision-109's
funding-history request omitted `n`; its tool section was rejected and its final
return malformed (seq 2884). Three other dropped sections were malformed
`working_state` on judge/meta returns, not three failed market investigations.
There were no orders, fills, admitted population artifacts, messages, funded
births, child invocations, proposals or governance activations.

There were 32 unique producer invocation handles, not 45 producer-return emissions
(13 were replays): 28 holds, two defers, one `verdict` and one malformed return.
The antagonist supplied 12 of those holds. Constructor's dropped batch contained
two requested calls: funding history and instruments; both were blocked. Thus 25
tool-call items were proposed and 23 executed. No later constructor wake occurred,
so this run cannot test correction after that rejection. Eleven subscription
changes were accepted and two refused; this is limited self-direction over wake
conditions, not new governance or collaboration.

Seven grounded findings closed: one supported and six unknown, with 13 grounded
contracts and 25 decisions still outstanding. No contrary finding closed. This
does not establish improved judgment: horizons are truncated and the required
sample is absent. A delivered inbox item, fetched body and subsequent decision
are separate observations; sequence alone does not prove learning. The canonical
trace remains mechanically inconclusive.

There is narrow evidence of feedback being read: empirical decision-136 fetched
outcomes 20/36/44 and explicitly distinguished an unknown finding from failure.
The sole supported chain was empirical decision-62 → judge decision-137 → finding
seq 3593 → delivery 3595 → settlement 3596. The judge awarded 0.6 for the hold
having occurred without a trade while leaving usefulness and whether holding was
right unresolved. A meta subsequently noted that the economic evidence did not
establish absence of an edge or optimality of holding. This is a concrete residual
judging concern: evidence of performing the stated action can receive credit even
when its value is unknown. It is not evidence of realized usefulness or successful
learning. Empirical's next wake, decision-175, deferred 110 measured ticks toward
the window close; the run ended before that deferral could resolve.

One malformed final judgment (decision-71, invalid enum) was followed by an accepted
unknown finding on its bounded retry (decision-73). Another final judgment
(decision-124, `verdict:null`) was malformed. The automatic retry here concerns a
fresh assessment of an invalid answer; the failed paid provider call was not retried.

There were 70 invocation records (continuations account for the larger provider
call count). Inclusive invocation costs were 91,497 micro for producer-role work,
73,634 for evaluators and 9,469 for metas. The producer bucket includes the
antagonist's 12 invocations and 33,809 micro; two exposures settled without a win.
Judges, metas and antagonist together used about 67% of inclusive invocation cost.
Do not add those costs to provider bills or wallet commits.

Final-call input-token median/max: producer 11,178.5/13,195;
evaluator 11,639/14,406; meta 5,423/5,476. These are not complete continuation
token totals. A shorter run with different decisions cannot establish context
improvement relative to the preceding 240-tick world.

## Finality, venue and accounting

The report confirms `terminated=true`, seal release, valid ledger and wallet
conservation. Terminal reconciliation succeeded. Both the report and an independent
read show no perpetual positions or open orders, equity 965.15385641 testnet USD.
Wind-down executed no orders; existing 0.00967 HYPE/USDC was within the precommitted
dust bound, valued at 321,866 micro testnet USD. This is not a new trading profit.

The $5 active reservation was released exactly once. Cumulative known inference
is $7.120146, total retained uncertainty $0.046446, and unreserved authorization
$42.833408. Prior uncertainty remains intact. No deployment, mainnet action,
transfer, top-up or additional trade was performed.

## Evidence and verification

Read terminal `report.json` first and confirmed termination before reading events.
Ran both existing analyzers successfully:

```
uv run python scripts/edition4_report.py --postmortem-report work/population-pr117/live/report.json --events work/population-pr117/live/events.json --out work/population-pr117/postmortem
uv run python docs/audits/edition4-experiments/analyze.py work/population-pr117/live/report.json work/population-pr117/live/events.json
```

`results-pr117.json` contains aggregates and SHA-256 identities for private report,
events, analyses and independent venue snapshot. Raw evidence remains under
`work/population-pr117/`. This run supplies neither a Class 3 result nor a valid
population-level comparison of the bundled repairs.
