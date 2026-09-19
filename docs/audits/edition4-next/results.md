# Discovery and consequence gates — September 19

This follow-up repairs the discovery interface, tests consequence plumbing, and
prepares a versioned deployment. The architect authorized deployment separately
from the earlier experiment plan. Mainnet trading, funding, transfers, top-ups,
and launching a funded world remain outside this work. The cumulative inference
authorization remains USD 50; `../edition4/budget.json` is the accounting record.

## Production change

The compact capability index now includes a valid `catalogue.search` call taken
from that tool's own validated schema example. Previously the only way to read
the catalogue's required argument was to call the catalogue successfully.

A successful catalogue or institutional lookup now permits a second, bounded
tool round followed by the final answer. Previously a participant received the
schema after its opportunity to invoke the capability had expired. Repeated
lookups cannot extend the two-round bound. Existing affordability, venue-write
validation, and restrictions after reading outside text remain in force.

These are two interface barriers. Invalid arguments were observed in the earlier
rehearsal; the continuation barrier is an additional mechanical finding, not a
retrospective explanation for every declined decision.

## Paid discovery diagnostic

The fixed task asked two existing seats to obtain a notional calculation from a
world capability and report the returned value. It supplied no tool argument
names. The compact prompt, full norms, task and initial rendered request hashes
were frozen before dispatch. This was a commissioned diagnostic on a fake
exchange with a restricted tool allowance, not a spontaneous population run.

| Model | Observed chain | Result |
| --- | --- | --- |
| GLM 5.3 Flash through Venice | direct `calc` invocation, then answer | actual receipt matched |
| Luna through OpenRouter | `catalogue.search`, then `calc`, then answer | full discovery chain completed |

Both returned `4321.625000`, matching the tool receipt. Neither produced dropped
sections or refused calls. Five model calls cost **12,794 micro-USD**, with no
unknown bills or overruns. GLM did not perform a lookup, so only Luna demonstrates
the full discovery chain. One task per model establishes feasibility, not a
reliability rate or general unfamiliar-tool competence.

Evidence: `discovery-preflight.json`, `discovery-paid.jsonl`, and its per-call
journal. The fixed clock and masking of the observation timestamp are declared
in the preflight. All other initial prompt fields were checked before dispatch.

## Offline nonfinancial gate

`nonfinancial-results.json` records real registration, jailed execution,
runtime-generated receipts, emitted commissions, the production evaluator step,
settlement, and producer-router feedback. Supporting and contrary facts lead to
different learning outcomes; missing, self-use and off-version evidence censor
without training. Cash does not move during settlement or learning; execution
itself costs 50 micro-USD.

Actor selection, claim authorship and final judgment are explicitly scripted
fixtures. The claim concerns execution/version/result, not benefit. This proves
plumbing, not model discrimination, useful cooperation, income or autonomy.
See `nonfinancial-design.md` for the contract and limitations.

## Paid nonfinancial screening

Ten frozen calls cost **20,398 micro-USD**, all authoritatively billed. The
preregistered exact-label count was **4/10**; it is retained in
`nonfinancial-model/report.json`, not revised after seeing the answers.

That count mixes different findings. Luna returned five valid findings: support
for the matching receipt, contrary for the different result, unknown for absent
evidence, and contrary for self-use and the wrong version. GLM returned two valid
findings, one otherwise substantive answer with invalid citation references,
one reasoning-only empty completion, and one truncated completion. Neither model
credited self-use or the wrong version as supporting the independent-use claim.

The original fixture labels were internally inconsistent. The claim was
existential (a matching execution would occur by the horizon), so a different
execution does not alone disprove it. This applies to the changed digest as well
as self-use and a changed version. The raw exact-label count is therefore not a
model accuracy estimate. The fixture remains preserved as evidence of that
diagnostic failure; later comparisons must declare different hypotheses before
dispatch.

Two interface clarifications follow from review: enumerate exact permitted
citations, and expose the evidence collection time with clearly distinguished
observation maturity and assessment timeout. They do not change norms, scores,
or the actual timing of settlement. Empty/truncated completions remain a measured
reliability limitation; reasoning has not been disabled to conceal it.

## Recheck after citation and evidence-time clarification

`nonfinancial-recheck-plan.md` records the narrower hypotheses before dispatch.
The unchanged two models returned **10 usable, validly cited findings in 10
calls**, costing **17,431 micro-USD**, with no empty/truncated completions or
uncertain bills. Both supported the matching independent execution, found the
different result contrary, and treated absent evidence as unknown. Neither
supported self-use or the wrong version. GLM used unknown for those latter
cases; Luna used contrary. The ambiguous exact-label distinction remains visible
and is not presented as an accuracy rate.

This is a small screen, not a causal effect estimate or a guarantee of output
reliability. No norms, reasoning settings, token budgets, scoring rules or
participant objectives were changed between the two screens. These findings
establish neither usefulness nor a live population's learning.

## Verification and deployment

The final source passed Ruff, **2,114 check tests and 20 relevant gate tests**;
commands and output are in `validation.md`. Release `a4e022622894` is deployed
to the existing host. The Linux OS-jail check and a 50-event offline smoke run
passed under the service user and protection settings. The prior installation
is preserved, and the factory service remains **inactive and disabled**. See
`deployment.md` for the complete digest, archive and rollback record. No funded
world or mainnet activity was launched.

The fresh control/consequence screening is governed by `live-comparison-plan.md`;
no new live-population learning result is claimed until those runs close.
Useful cooperation, independent income buying further operation and autonomous
objective formation remain unproved.

## Completed control and independent audits

The new bounded control ended and released its seal before the diary was read.
It made 98 provider calls for **540,781 micro-USD**, with no uncertain bills,
over 48 ticks (measured interval about 37.9 seconds). The 53 producer returns
were 36 defer, 15 hold, one order and one investigation: 51/53 declined action.
Router NOOP draws are separate and are not included in that denominator.
Two `venue.funding_history` calls executed successfully. One earlier optional
tool-call section was dropped for a missing `n` argument. No registrations were
attempted or rejected. The diary's `sandbox.availability` was true, ruling out
missing jail support as the explanation for this run's absent tool creation.
Address was intentionally disabled and external selling was outside this arm;
their zero counts do not test demand for those capabilities.

One population order filled. The bounded runner's existing wind-down closed the
position; final read-only venue reconciliation showed no positions or open
orders. That close is shutdown activity, not a second autonomous trading choice.
Wallet conservation and ledger verification passed. The runner reported 18
outstanding decisions at termination, so this is not evidence that every
consequence horizon completed. Cumulative known inference spend is now
**3,101,702 micro-USD of the authorized 50,000,000**, with no active reservation
or uncertain bills. See `live-control/audit-summary.json` and the full report.

Exactly one Astra and one Opus performed distinct independent audits. The lead
checked their findings against caller paths and corrected Opus's initial
conflation of personal tool-registration entitlement with shared service
novelty reservations. Reports: `astra-class3-audit.md` and
`opus-capability-audit.md`.

Astra identified a source-confirmed gap: final grounded findings emit evidence
which the standard meta request omits; grounded completion also skips the
ordinary verdict commitment. A production-route regression has not yet been
run for this finding. The paid consequence arm is deferred pending that focused
reproduction and repair. It has not been run; this control is not a matched
causal comparison. Changing the source requires a new control for a future pair.

The next concrete sequence is: reproduce and repair that recursive path; prove
one relevant downstream nonfinancial dependency; then run a fresh bounded world
with the intended capabilities explicitly enabled and enough measured lifetime
for governance to activate and its consequences to resolve. No profit objective,
activity quota or automatic norm rewriting is implied by these findings.
