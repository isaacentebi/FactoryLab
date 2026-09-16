# GPT-6 Pro, third reading: edition 3 at 4618f6f

Received 16 September 2026, in answer to `docs/audits/v6/brief-gpt6-edition3.md`. The package it
sent (its two test files, the 16-file unified diff, its evidence file, its test results and
README) is preserved beside this file under `review/`; the patch also as
`edition3-review.patch`. What follows is its answer, condensed only where it repeats the
package, and kept in its own words where the words matter.

## Verdict

Would not launch 4618f6f with real funds. Not because the constructor lost money or the
population deferred: "venue losses can consume fictitious compute resources; private working
state crosses the evaluation boundary; some consequences do not reach their owners; the router
manufactures work for judges when no seat acted; and shutdown does not establish the finality
its contract promises." Edition 3 is a meaningful improvement and the constructor's episode
contains real adaptation, but "the current implementation can teach its population false
things about its world, then reward the population for accommodating those falsehoods… your
factory can turn a bookkeeping defect into an institution." Preserve the openness, repair the
physics.

It read the essay, contracts, code map, calibration, rehearsal documents and the named runtime
boundaries; parsed all 517 returns; ran 33 characterization tests and 11 candidate-repair
checks (44 passed); `git apply --check` clean. Did not run the full suite or replay the diary.

## 1. What edition 3 is

A runtime of durable participants with the essay's real components. But C1 is not reliable end
to end, C2 does not consistently give the seat control over paid attention, C3 keeps privileged
payoff machinery and a circular fidelity score, C4 mixes self-knowledge with excessive and
sometimes misleading disclosure, C5 does not describe real custody or verified liquidation.

**A seat** is "a durable, bounded-authority principal hosting a resumable decision process."
Keep four identities apart: seat (authority, continuity, obligations), executor (the model or
program used for one invocation), project (a contingent assembly of commitments and
resources), lineage (provenance and inherited rights). One identifier doing all four jobs makes
retirement, child evaluators and delayed income awkward.

Code plainly violates the essay in three places: private working state forwarded to judges; a
challenged metric adjudicating its own fidelity; three arrivals treated as temporal
separation. The essay is not an engineering theorem elsewhere: memory is not a no-regret
guarantee; a model's retrospective probabilities are not the distribution that sampled its
action; encryption is not darkness and accurate private records are not Class 2; a temporary
scaffold is fine, a lens repeated forever in the system prompt weakens the permission to
replace it. Characterization: "a partly implemented Class-3-capable architecture, with an
observed episode of adaptive planning, not yet evidence that the population is successfully
revising the standards by which it chooses objectives."

## 2. Money: separate the pots, and more than two

Yes: venue P&L, fees and funding settle on venue accounts only. But three quantities must stay
distinct: learning score (evidence for a rule), seat entitlement (permission to spend within a
budget), actual assets and credits (held at a custodian or provider, changed only by verified
transactions). Rent does not consume OpenRouter credit either: it may reduce authority, never
reported inventory. The compute wallet may be a constitutional ceiling, labeled as authority,
not cash.

Confirmed: `runtime/venue.py` settles venue effects through the compute wallet (a venue loss
can exhaust it with no provider credit spent). Confirmed: `Treasury.earn()` accepts the same
settlement reference twice and the seller books both; that mints internal authority. Not
confirmed: the suspected double charge of paid computation inside `payoff.net`; the control
test shows the fields are distinct.

Do not repair by deleting one settlement: the bridge must change with the ledger (a confirmed
Venice purchase must increase the destination inventory and authority; Base USDC income is not
OpenRouter credit; principal converted is financing, not income; a pending bridge is a held
source plus a pending claim; Venice replenishment does not imply OpenRouter replenishment).

Request line and reward line stop learning from one number: the request line gets facts by
custody, authority, holds, liabilities, freshness, pending transfers; the reward line gets an
addressed assessment (handle, scoring-rule version, outcome, score, sampling record). A useful
consequence record: "this decision incurred 920 µUSD of provider cost, received 1,200 USDC
micro-units of funding at Hyperliquid, and still has an open position; its committed
hypothesis has not settled."

Collateral: the repaired check is better but too broad if `equity_usd` includes spot marks that
are not eligible collateral. The adapter should expose the actual pool (account mode,
collateral asset, eligible equity, margin used, open-order holds and whether they are already
in margin used, leverage for this instrument, observed at) and the check is incremental margin
plus holds not already reflected plus precommitted headroom against eligible equity minus
margin used. Unknown or stale collateral blocks new risk, never cancellation or bounded
reduction.

## 3. Cold findings (paths under `factorylab/`)

| Finding | Evidence | Disposition |
|---|---|---|
| Venue effects change compute authority | `runtime/venue.py` | launch blocker; typed-ledger migration |
| Duplicate income receipts booked twice | `world/treasury.py`, `runtime/seller.py` | dedup patch; independent payment verification still required |
| Private state reaches evaluators | `runtime/loop.py`, `runtime/compute.py` forward `ret.outputs` with continuity fields | public-return projection patch |
| A minimal world bypasses privacy partitioning | `cortex/request.py` conditions separation on stable fields existing | patch |
| Router abstention manufactures a producer return | `runtime/loop.py::_assembly_step` sends an unselected draw into `_producer_step` | patch; no authored decision means no return to grade |
| Fidelity objections scored against the proxy they challenge | `settlement/settle.py` | remove circular scoring now; independent adjudication remains required |
| Owner-directed feedback incomplete | refusals broadcast, fills not consistently addressed, non-payoff forecasts miss the inbox | refusal patch; general causal delivery to complete |
| Inbox and state failure semantics lose continuity | failed journal writes advance memory; missing blobs become "no state"; a handle retrieves only its latest outcome | write-ahead / fail-closed / item-address patches; ack semantics to tighten |
| Working-state rent reprices the past | rewriting a head uses the new byte count for an earlier interval | byte-time accrual patch |
| Artifact ownership and durability inconsistent | second writer of identical bytes cannot read its own; republishing does not publish; unindexed blob readable; references can precede durable bytes | candidate repairs; lifecycle not redesigned |
| Subscriptions do not mean what a seat infers | non-routine judge work bypasses deferral; coin filters affect admission not the fold; missing watcher observations erase the last value | watcher patch; paid-work admission needs a clear contract |
| Cascade separation counts arrivals, not time | three messages arriving together satisfy the separation | launch blocker |
| Retirement not final at the budget layer | a late credit recreates entitlement for a retired seat | keep such resources in the commons |
| Kill can fail before termination, repeat external actions, or report success early | ledger failure escapes wind-down; repeated kills close again; "resting" counts as closed; no final reconciliation | defensive patches; durable wind-down missing |
| Death witness enforcement removable | unsetting the remote URL removes its veto; renaming a diary changes the local witness location | identity-bound, fail-closed enforcement outside mutable naming |
| Restore validates some identity constraints after mutation | `runtime/resume.py` assigns saved fields before checking release/facilitator | early-validation patch; transactional restore still needed |

Further, not disguised as fixes: the account-read fallback invents financial facts (a failed
venue read fills the request with wallet equity and empty positions); the fold can be consumed
before successful delivery (offered / delivered / acknowledged need a durable distinction);
`MAX_SAID` can evict decision-linked material before a delayed consequence (patch stops it;
long term archive-backed retention); income-spool trust is not payment verification (receipt
identity needs chain, transaction, log, asset, recipient); evaluator machinery still
privileges payoff (required payoff field, payoff-dependent meta logic, fallback to payoff when
normative consequence is unreadable); forecast-shaped returns can reward easy questions; the
commissioned-child-judge path has incompatible exclusions (remove the suggestion it is usable);
the seal does not close every disclosure path.

## 4. What the rehearsal shows

The constructor: persistent, observation-conditioned adaptation (retained and revised a plan,
obtained funding history, changed size after refusals, retained an exit condition before the
trigger, closed after the cited change). The lens explains where attention began; observations
explain part of how behaviour changed. But: "a probe sized to pass under both collateral
hypotheses cannot distinguish those hypotheses"; the constructor treated the small fill as
resolving the question, and repeatedly described a refused order as having "vanished" despite
a recorded refusal (the missing owner-addressed refusal is one part; the confident narration
another). Arithmetic inconsistencies in its carry estimates. "A continuing process capable of
using evidence, retaining rules, and retaining mistakes." To isolate memory versus lens, run
matched sequences with empty-state and neutral-lens controls.

Not delta-neutral carry: BTC short and ETH long with unequal notionals is a cross-asset funding
trade with directional and basis exposure; closing ETH left the BTC short. The observation
contract must say whether a quoted rate is the settlement-period rate or an eight-hour
equivalent. "Minus nine cents" is too narrow: pre-shutdown settled net was −$0.154 (price
−$0.095, fees −$0.088, funding +$0.029); the shutdown BTC close adds +$0.171 gross; the
shutdown fee is missing. Shutdown transactions are inside the accounting boundary.

Defers: 116 producer invocations, 15 malformed, 101 valid: 78 defers, 18 holds, 5 orders. Mixed:
some restraint (the opportunity seat changed subscription and cadence; the constructor
understood ten two-minute ticks as twenty minutes), some router oversupply, some plausible
prompt/evaluation adaptation (polished restraint is cheap and judges keep scoring it). Do not
introduce an activity quota. 482 quiet draws are not 482 decisions.

Evaluator spend: 74% is not disqualifying in itself (the essay expects evaluation to cost more
than production); the defect is the synthetic `{"action":"noop"}` producer return the runtime
creates when the router selects nobody and then grades: 403 producer observations versus 116
invocations; 272 evaluator returns mentioning "noop", $1.48 of reported cost; 267
`verdict.unread` versus 7 `verdict.consequence`. "No authored decision means no producer
return to grade." Even then, a hold should be evaluated only against something it commits to.

Acks: three `outcome.get` attempts (two failed reads by the fidelity judge, one successful by
the empirical seat); the constructor never called it despite saying it would reconcile.
Mechanical causes first: one decision can receive several outcomes, `outcome.get` returns the
latest, the inline window is a subset, acknowledging a handle can acknowledge unseen items,
some facts never enter the inbox. "Acknowledgement is queue maintenance, not a learning
reward."

## 5. Economics

Booked model $2.780 + tools $0.003 = $2.783; pre-shutdown trading loss $0.154; wallet decline
$2.937 under mixed accounting; exports sum $2.788 (525 calls reported vs 517 returns). Per
exported return: GLM $0.00402 (249), Luna $0.00667 (268). Per tick $0.0189: $2.73/day at ten
minutes, $13.63/day at two, linear rescaling only. Hosting $0.80/day; about $3.53/day; use
$4/day of external net receipts as the continuity target. Keep the $500 boundary; provider
split $195 OpenRouter / $105 Venice fits the measured mix better than $220/$80. Keep
120/60/60/60 for the next controlled test: per seat genesis is $10.67 and weekly $5.33, and
the busiest judge projects to $8.30/week, so the budget problem is partly stranded authority,
not runway; any later allocation mechanism should be a population-usable contract. Earned
continuity at $4/day needs 3.3% of $120 a day from trading, implausible; a service would need
80 requests a day at $0.05 contribution; a unit-consistent funding calculator or a checked
data transformation is more plausible than "sell opinions", and there is no demand evidence.

## 6. Architectural changes

A. Separate execution receipts, learning receipts, commitments and adjudications as
independently addressable objects. B. Make evaluation a resource-consuming commission with a
subject, scope, horizon and budget that may conclude "unmeasured"; no synthetic returns; keep an
exploratory allowance. C. Temporal separation by time and completed evidence, not arrivals;
aggregate upward with precommitted jitter; keep execution facts and safety actions off the slow
path. D. Split death from liquidation completion: `production_state = killed`,
`exposure_state ∈ {flat, dust_within_precommitted_bound, wind_down_pending, unknown}`; a
narrowly authorized wind-down executor with durable operation ids may only cancel, reduce,
reconcile, finalize; "resting" is not flat, partial fill is not flat. E. Do not promise
primitives a future request cannot create: either seed a small generic budget/commitment
capability now or accept that some requests need a new world.

## 7. Delete or reverse

Delete the synthetic noop producer path. Delete the fidelity objection's same-proxy score (keep
the objection as an unresolved claim until independent adjudication). Remove the remaining
mandatory payoff privilege. Remove the permanent lens from the system prompt (seed it once in
working state). Remove repeated institutional exposition from each request. Remove promises of
commissioned judges and funding routes that cannot execute. Do not delete memory, protected
exploration, norm definitions or all meta-evaluation. Removing DeepSeek and Qwen is justified;
excluding Sol on cost is reasonable; but GLM 45/45 and Luna 43/45 with a funding miss means
"every critical arithmetic case" is not met by the final roster: give seats a deterministic,
unit-explicit arithmetic capability (it prescribes no objective).

## 8. Prompts (verbatim in its answer; transcribed to `prompts.md` beside this file)

A common system contract replacing the repeated text; the constructor lens seeded once in
working state as `{lens, open_questions, active_commitments}`; a short WORLD CONTRACT wrapper
around the verbatim norms; a `YOU` template with kernel-serialized slots (seat, lineage, clock
with tick duration, working_state, spending_authority, provider_inventory, venue_accounts,
pending_conversions, runway, subscription, open_commitments, outcomes with exact ids,
directory); a WORLD UPDATE block (observation window, changes since last successful delivery,
execution receipts, charter, catalogue changes, public and unavailable observations); an
OUTCOME CONTRACT (intended / submitted / settled / rejected / unknown for execution claims;
forecast fields; fidelity objection shape; pause condition; money with asset, custody and
unit). Private state appears once, in `YOU`; mutable things stay out of the prefix; the prefix
is serialized once and its bytes reused. Measured: mean request 98.5 KB (54.2 KB prefix,
20.5 KB `YOU`, 21.2 KB inputs).

## 9. The patch

16 production files (`review/edition3-review.patch`): stop grading the router's empty draw;
stop scoring the objection against its proxy; public-return projection; privacy partition
unconditional; refusals addressed to the owner; write-ahead and fail-closed state and inbox
with exact outcome ids; byte-time rent accrual; artifact read grants; watcher last-value
retention; retired seats' late credits to the commons; defensive kill; early restore
validation; income receipt dedup. Not in it: the typed treasury, the durable wind-down
executor, identity-bound witness enforcement, transactional restore, artifact lifecycle,
independent fidelity adjudication, chain-verified receipts.

## 10. The four pathologies, applied

Stable failure: buying explanations of inactivity without changing its conditions.
Overfitting: judges approve synthetic noops, favorable metrics suppress objections, easy
forecasts dominate. Learning death: seats lose affordable investigation or feedback despite
nominal resources elsewhere. Thrash: arrival-count cascades apply feedback at the wrong scale.

## 11. Launch gates, stop conditions, a week

Four gates: financial reality (test with the real $120 principal; separate custody
reconciliation, exact-once income, correct collateral scope, a bridge that replenishes only
the destination); continuity and information boundaries (inject failures around state, inbox,
acks, restore; every owner receives fills and refusals; missing data stays unavailable; no
judge or wake gets private state); finality (partial fills, resting closes, unavailable mids,
ledger failures, dropped acks, repeated kills, restarts; production stays dead; residual
exposure reported under restricted authority; renaming files or unsetting a variable cannot
revive an identity); selection and temporal behaviour (re-screen final prompts and routes;
no synthetic work; real evidence windows; a usable way to decline or reshape evaluation
spend without a quota).

Stop: a verified duplicate credit, unauthorized disclosure, witness bypass, or unowned residual
exposure. Not: a week without profit.

"The next move is not to force this population to be more entrepreneurial or more active. It
is to make its costs, consequences, privacy, and death true. Then let it decide what is worth
doing."
