# PR121 native-completion population: terminal results

The run completed all 240 ticks and 619 paid calls for **$1.534603**, with no
uncertain bills, overruns or admission refusals. The terminal diary is sealed and
released, wallet conservation holds, and both terminal and subsequent read-only
venue snapshots show no perpetual positions or open orders. No recovery trade was
needed. This is a completed experiment, not evidence of a Class 3 factory.

## What changed, and what did not

The 4,096-token shared completion allowance was replaced by provider-advertised
limits. All 619 provider responses stopped normally: zero length-truncated
empty answers, compared with 42 in PR119. There were still 18 malformed final
answers, now schema errors: required fields, wrong types and invalid enum values.
The 580 final invocation records exclude intermediate continuation calls. Summing
all 619 journalled provider responses gives 7,178,351 input tokens and 216,384
output tokens, with the same $1.534603 authoritative bill.

The reward loop remains unresolved. There were 75 final grounded findings, all
unknown, zero supported or contrary findings, 49 censored contracts and eight
outstanding grounded contracts. These counts describe different stages and are not
necessarily disjoint. The inherited coverage screen fails its assessed/contrary
requirements despite meeting its tick target. More valid JSON alone did not supply
assessable consequences. Seventy-three findings cited economic outcomes; most
reasons concerned zero or unresolved value, which does not test broad declarations
such as holding or preserving runway.

A concrete interface failure affected the real trades. Decisions 264 and 273 each
executed two limit-order tools. Their final replies declared `action: order` without
the separate required `order` field and were marked malformed (seq 6688 and 7050).
Both initially resting orders later filled; the second pair received fill responses
(BTC reported 0.00116 filled against a requested 0.01). Four order identities
received fills, represented by 14 fragments. Final exposure before wind-down was
-0.01116 BTC and +0.6282 ETH. Mechanism recognized the larger BTC exposure, but
follow-up order/cancel attempts in decisions 282, 295 and 304 failed validation
before dispatch; no venue.cancel executed.
The final findings for both decisions explicitly cite malformed claims as a reason
for unknown, despite available execution evidence (seq 8845 and 9296). Both decisions still received observation contracts, final requests and findings:
evaluation eligibility was not skipped. The side effects survived; the declared
claim did not become a valid evaluable return.

This is not output starvation or evidence of a parser defect. It is a contract ambiguity between issuing an order
as an answer and reporting an order already executed during investigation. Any
repair must preserve executed effects and their attribution without interpreting
an after-the-fact report as an instruction to submit another order. Reproduce that
journey offline before another paid population run.

## Observed activity

All 103 dispatched tool calls returned `ok`: 63 outcome reads, 11 outcome listings,
six artifact reads, six calculations, four funding-history reads, four limit orders,
three world reads, two catalogue searches, two position reads, one open-order read
and one funding read. Successful dispatch does not count requests rejected before
execution, and reading an existing artifact does not prove a new cooperative product.

The six artifact reads retrieved invocation-scoped compact context, not new
population-authored work. There were zero addressed messages, notes, child requests,
funded births, population registrations, amendments, votes or charter activations.
Of four declared investigations, one funding-history request omitted n and one
outcome-list request exceeded its limit; one executed outcome/funding-history
reads, and one dispatched no tool. More credit was
not the immediate binding constraint: there were no budget-infeasible or unaffordable
route events, and every seed ended with over $10 of entitlement. Increasing the cap
alone therefore does not address the observed missing institutions. The antagonist
ran 125 times, with no recorded judge failure or successful exposed verdict. This
is use of an existing role, not evidence that adversarial evaluation improved it.

Twelve of the 75 final finding bodies were retrieved. Decision 84 fetched the
unknown finding for decision 14 (seq 2233) and retained it in working state
(invocation 2246) before deferring. Transport and retention are observable, but an
unknown finding supplies no directional correction. Metas returned 29 verdicts
with high conformity; agreement does not independently establish judgment quality.
Ten final-finding attempts were rejected for incompatible unknown/score shapes;
these are schema friction, not an explanation for every unknown finding.

Final-call contexts still commonly occupied roughly 40–49k characters, with
maxima of 94,701 for judge-fidelity and 80,808 for antagonist. Native output
headroom does not resolve that context growth. Treat context selection as a
separate measured problem, rather than attributing all inactivity to it.

## Cost, observation and scope

Authoritative inference bills are $1.534603. Wallet commits total $1.539705,
including tool/storage charges; inclusive invocation costs must not be added again.
The separate three-call prelaunch probe cost $0.003755 and was already reconciled.
The population's $5 reservation is released once. Cumulative authorized experiment
inference is now $9.917992 known plus $0.046446 retained prior uncertainty, leaving
$40.035562 unreserved under the user's $50 authorization.

Reported final-call cache usage is 728,064 cached tokens out of 6,705,894 input
tokens (10.86%). GLM reported cache hits on 236 of 251 final calls; Luna reported
zero on 329. That is observed provider metadata, not proof that a missing local
cache caused behavior. Continuation usage is excluded from these token counts.

The terminal wind-down recorded two filled closing orders and no residual perps
or resting orders; existing HYPE spot dust remained within its committed bound.
A later independent read also found no positions or open orders. Venue equity is
not independent income purchasing inference, and testnet gains are not spendable
model credit.

This comparison is observational: native completion allowances and the wall-clock
allowance changed, while market conditions, sampling and provider behavior also
changed. No claim that larger token allowances caused a behavioral improvement is
supported. The run proves that the previous truncation pattern need not recur
under this configuration; it does not prove unlimited completion is sufficient.

## Verification

Read terminal report first and confirmed `summary.terminated=true` before reading
any event export. Ran `scripts/edition4_report.py` and
`docs/audits/edition4-experiments/analyze.py` against the released diary. Raw requests,
responses and diary remain private under `work/population-pr121`; source hashes and
bounded aggregates are in `results-pr121.json`. No new world, transfer, top-up,
mainnet action or additional recovery order was performed.
