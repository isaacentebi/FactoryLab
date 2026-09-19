# Astra audit: Class 3 fidelity and the remaining causal obstacles

19 September 2026. Read-only source audit of base `6c4c86455963f1b9f45edeff75cb99438ade050e`
plus the four production-file changes identified with release
`a4e022622894d9064fdc26e840c08be52344202b4569be24a2f52d8eec13bd2a`.
Only this report was written. No code, configuration, keys, funded manifests,
live diary/state, paid calls, network, deployment, tests, commits or branches were touched.
The running control was deliberately not inspected; not even its permitted observer was
needed for these conclusions. Its eventual results are outside this report's evidence.

## Judgment

The present design is a credible experiment in some necessary Class 3 mechanisms,
not demonstrated Class 3 behavior. The latest repairs remove real interface barriers;
they neither establish nor rule out endogenous objective formation. I would retain them.
I would not promote consequence mode or recommend a mainnet Class 3 launch yet.
The most immediate repair is an incomplete recursive check on the **final consequence
judge**, described in F1. More capital, more registrations, replacing judgment with profit,
or requiring a particular product would not repair it.

This does **not** require interrupting or altering the current control. The planned
same-source C comparison remains useful as a screen of the implementation that actually
exists. However, F1 means that a positive C result cannot demonstrate that its decisive
judges themselves learn from independently grounded recursive scrutiny. Preserve the pair's
source; investigate the finding offline before promoting the treatment.

## The essay's actual requirement, including norms

An objective is the standard by which a plan becomes preferable. Class 3 requires that
the factory participate in revising those standards in response to its world, not merely
generate new plans or unexpected trades (`docs/essay.md:80`, `:82`, `:114`). A new tool,
market or participant is an affordance; its existence alone is not this transition.

**Participants cannot edit the founding norms in this implementation, and that is
intentional and compatible with the essay.** The essay explicitly permits a read-only
normative constitution and distinguishes it from writable proxies and constraint prices
(`docs/essay.md:586-602`, especially `:592`). Wholesale norm self-creation is its undefined
Class 4 case. The current five substantive norms are in
`factorylab/charter/charter.py:165-194`; they are not a command to maximize profit.
`factorylab/charter/amendment.py:88-104` exposes card addition/replacement/removal,
prices and tick interval, with no norm operation. Admission rejects cards naming a new
norm (`factorylab/charter/book.py:83-108`); activation explicitly carries forward
`current.norms` (`:218`). Existing contracts retain their frozen norms
(`factorylab/runtime/grounded.py:105-130`).

This is a real distinction, not wordplay: participants can change what counts as evidence
of usefulness, which measurement is acceptable, who answers for it and how strongly it is
priced, while remaining answerable to the original values. The Class 3 question is whether
they actually make and sustain those revisions, and whether the revisions change later
costly choices. Editing a private lens or writing a proposal without activation does not
establish that. The separate Opus audit owns the detailed reachability of these contracts.

The essay also allows a temporary orchestration scaffold that the population can tear down
(`docs/essay.md:638`), costly recursive evaluation (`:537`, `:547-555`), and protected
patience for initially unsuccessful exploration (`:616`). Nine seed seats, a high judging
spend share and immutable norms are not individually disqualifying. A scaffold that never
becomes economically or temporally revisable is a different matter.

## Findings, in priority order

### F1 — P1, source defect: the final grounded judge is not fully inside the promised recursive consequence loop

**Observed in source.** `_complete_grounded_evaluation` emits `realized_finding`,
`grounded_contract` and `grounded_evidence` with its `Verdict`
(`factorylab/runtime/feedback.py:1609-1625`). The cascade retains the representative
payload (`factorylab/runtime/cascade.py:125-141`). But `_meta_step` constructs the actual
standard `Verdict` request from only verdict, rationale, producer outputs, current charter
and world; it never passes those three grounded fields
(`factorylab/runtime/loop.py:1416-1436`). Generic-event forwarding does not repair the
ordinary `Verdict` case. Thus the meta cannot directly inspect the supplied facts against
the final finding it is supposed to judge.

There is a second break in the same branch. `_evaluator_step` returns immediately after
grounded completion (`factorylab/runtime/loop.py:1245-1248`), before ordinary verdict
commitment and forecast processing (`:1354-1376`). Grounded completion does not create
the replacement independent commitment. `_commit_verdicts` only discovers existing
payoff forecasts (`factorylab/runtime/feedback.py:1174-1200`). A terminal meta instead
waits for `verdict_outcomes[final_judge_handle]`
(`factorylab/runtime/loop.py:1498-1508`), while that final judge has neither the ordinary
payoff forecast nor the no-payoff normative commitment that ordinarily creates this fact.
The expiration path eventually censors such a wait
(`factorylab/runtime/feedback.py:579-601`). Unknown final findings bypass recursive
emission altogether (`:1586-1589`), including an unwarranted unknown despite relevant facts.

**Causal consequence.** The producer can learn from the final judge, and the final judge
can receive a meta's opinion, while the decisive evidence interpretation is hidden from
that meta and its terminal outside signal is absent. The strongest anti-overfitting
claim of C therefore exceeds this implementation. The nonfinancial gate checks through
producer learning; it does not establish closure of this additional loop.

**Strongest counterargument.** Normative assessment must remain contestable, and the final
judge's rationale plus producer claim can support a coarse conformity review. Agreed:
there is still recursive activity. It does not establish a review of the actual evidence
or the separate external anchor required by `docs/essay.md:537-549`. Treating the final
judge's own numeric answer as that external anchor would be circular.

**Cheapest diagnostic / minimal repair.** One offline production-route witness: mature a
contract, let the real evaluator step emit its final `Verdict`, release it through the
real cascade, capture the actual meta request and follow its terminal settlement. Compare
a supported fact with a contradicted fact while holding superficial verdict/rationale
constant. Prediction: the current meta request lacks the changed fact, and no independent
final-judge outcome arrives. Then carry the representative's bounded frozen evidence into
the existing meta request and define an independently resolved commitment for the final
judge's actual claim, with explicit unknown behavior. Do not create a second court or
calibrate judges against agreement with their own final score. Scrutiny of an evidence-
present unknown must remain possible without awarding the producer an invented score.
This is a source-traced finding, not a newly executed failing test; no production caller
was bypassed to manufacture it.

### F2 — P1, measurement limitation: independently observed execution does not yet expose the useful dependency

**Observed.** `public_evidence` admits the subject's economic outcome, executions addressed
to its handle and its attached resolved forecasts
(`factorylab/runtime/grounded.py:134-179`). Execution receipts are eligible to ground a
number (`:182-207`), but relevance and value are interpreted by the judge, not guaranteed
by citation validity (`:211-245`). Population predicates see anonymous numeric window
facts, deliberately without per-decision identities
(`factorylab/runtime/observations.py:327-378`). That useful privacy boundary also prevents
them from observing an arbitrary chain such as “this artifact changed this independent
participant's later result.”

The new nonfinancial gate freezes an **execution/version/result-digest claim**, scripts
the caller and scripts the final judgment. Its own design explicitly disclaims benefit
(`docs/audits/edition4-next/nonfinancial-design.md:15-60`). The paid recheck establishes
usable cited findings on these supplied cases, not discovery of useful cooperation
(`docs/audits/edition4-next/results.md:89-104`). The stronger execution-plan requirement
of useful nonfinancial consequence remains open.

**Causal consequence / hypothesis.** Exact but trivial undertakings are easier to observe
than useful long chains. If judges reward the former under epistemic integrity, the system
can select for provable activity; if they insist on downstream benefit that the evidence
surface cannot show, it can select for almost nothing outside finance. Neither outcome is
forced by the code, but both are plausible under the available information. This is a
measurement bottleneck, not proof that models refuse to cooperate.

**Strongest counterargument.** Successful execution can itself fulfill a useful commitment,
and nonfinancial inquiry can be assessed against independently resolved forecasts. Correct.
Do not categorically ban self-use or prescribe sales as the only value. The missing witness
is a relevant downstream effect, not an automatic rule that every internal act is worthless.

**Cheapest diagnostic / minimal addition.** Fix one participant-authored undertaking before
the fact, with an observable consequence in a recipient's later work. Compare actual result
consumption, receipt-only invocation, contrary result and unavailable result. Let judges see
the same bounded provenance and no investigator-provided utility label. Reuse receipts and
commitments to link a consented downstream observation to its producer; expose only the
facts needed for that claim, not the population's entire topology. If no existing contract
can express that witness, name the exact missing observation before designing another API.
Do not rename the current execution witness “useful production.”

### F3 — P2, structural selection pressure: honest unknowns still disadvantage slow or hard-to-measure exploration

**Observed.** C unknowns deliberately produce no router or assembly update
(`factorylab/runtime/feedback.py:1673-1684`, `:1780-1790`). EXP3 increases the observed
arm's log weight by `gamma/N * reward/propensity`
(`factorylab/learners/exp3.py:44-54`). The other feasible arms' relative shares consequently
fall when one arm receives supported scores. No explicit zero need be recorded for this
selection effect. For a fixed menu, the expected increment reflects the probability of
getting an assessable outcome as well as its score; unequal observability is not corrected
by logging the action propensity.

All grounded work receives one launch-fixed horizon
(`factorylab/runtime/grounded.py:119-125`); a valid unknown closes it irrevocably
(`factorylab/runtime/feedback.py:1586-1600`). Population trial protection also ends at a
fixed lifetime even if no consequence arrived
(`factorylab/runtime/routing.py:375-399`). The model bill has already been paid.

**Causal prediction.** All else equal, cheap short-horizon measurable work gains routing
share over equally valuable work whose evidence appears later or cannot be represented.
This can manufacture a financial/activity niche without anyone explicitly installing
profit maximization. It is the essay's learning-death problem (`docs/essay.md:616`),
not a reason to reinstate optimistic credit for ignorance.

**Strongest counterargument.** Scarce compute should favor evidenced work, and the kernel
already protects novelty. Both are true. The question is whether protected access and
funded lifetime cover at least one complete exploratory loop at delivered tempo. A
nominal reserve fraction or chosen child endowment alone does not demonstrate that.

**Cheapest diagnostic / minimal decision.** A matched offline delayed-feedback witness,
varying only evidence arrival and collection while preserving genuine outcomes, should
report exposure, mature outcomes, routing share and remaining affordable calls. Do this
before changing the learner. If structural censoring dominates, allow bounded prospective
horizon choice or observation schedules and price the required patience explicitly;
preserve frozen obligations and once-only settlement. Do not add a reward for being new,
being active or claiming that evidence will eventually appear.

### F4 — P1 for a Class 3 claim, experimental mismatch: current short screens cannot test revisable collective objectives

**Observed.** Governance activation requires both elapsed time and fresh ticks at
`min_ratio * slowest_period`, with the backstop as a floor
(`factorylab/runtime/cadence.py:101-148`,
`factorylab/runtime/governance.py:1451-1472`). The rehearsal base sets a 60-tick backstop
and ratio 3 (`worlds/edition3-rehearsal-5.toml:269-285`), hence at least 180 ticks for the
first eligible activation, before considering longer outstanding consequences. The
current plan gives each arm 30 minutes at a nominal ten-second tick but explicitly
acknowledges serial dispatch (`docs/audits/edition4-next/live-comparison-plan.md:9-15`).
Earlier screening arms delivered only 19-23 ticks in 15 minutes
(`docs/audits/edition4/results.md:64-87`).

Even the ideal ten-second schedule uses almost the whole 30 minutes for the first
180-tick floor, leaving no meaningful post-amendment observation. The observed slower
tempo makes the gap larger. A C screen can still test final evidence feedback; it cannot
establish or falsify endogenous collective objective revision.

**Strongest counterargument.** An agent may revise its local priorities without amending
a charter. Yes; that is useful early evidence. The stronger claim that the factory has
changed its shared standard, allocation and ensuing behavior requires the slower loop.

**Cheapest diagnostic / minimal change.** Before a future governance experiment, calculate
the affordable calls and delivered ticks needed for one proposal, one eligible activation
and one full consequence window after it. Use existing cadence, reservation and receipt
data. Choose a fresh experiment whose stopping contract can cover that sequence, or report
that it cannot be afforded. Do not shorten a running world's horizons, erase cascade
separation, or read continued inactivity as a verdict on Class 3 under inadequate exposure.

### F5 — P1 for self-support, unproved economic closure: learning scores do not purchase the next inference bill

**Observed.** Numeric producer settlement trains selection; it is not a cash receipt
(`factorylab/runtime/feedback.py:1590-1600`). Venue profit remains a claim on venue
custody rather than a refill of compute entitlement (`:885-927`). Seller income is
credited after collection/verification through treasury (`:1034-1067`), a distinct rail.
The current testnet screen deliberately disables the relevant real-money conversion
paths and cannot test real income buying thought
(`docs/audits/edition4-next/live-comparison-plan.md:9-12`, `:35-37`). The prior results
report public seller discovery and income-to-inference evidence as unfinished
(`docs/audits/edition4/results.md:179-188`).

**Causal implication.** More sophisticated internal cooperation can redistribute or conserve
the launch endowment; it cannot replenish aggregate external purchasing power by itself.
The factory can legitimately value inquiry or preservation over revenue, but persistent
operation still requires some independently supplied resource flow. This can come from
machine counterparties; it need not be a human consumer or an architect-assigned business.

**Strongest counterargument.** Profitable venue activity could fund everything, and a
bounded experiment need not already be self-supporting. Agreed. Neither mechanism has
been established by the current prepaid testnet comparison. A forecast of sufficient
trading profit or a discoverable seller URL is not a closed resource loop.

**Cheapest next diagnostic.** Trace the existing seller's publication, unrelated discovery,
payment, delivery, durable receipt, custody movement and a later provider bill as distinct
steps. A scripted/testnet customer proves plumbing only; unrelated paid uptake proves
demand only for what was bought. Keep this separate from Class 3 evidence. No new venue,
internal prediction market or larger principal is presently justified by this gap alone.

## Independent review of the latest patch

The catalogue bootstrap example is obtained from the tool's own schema rather than a
second handwritten contract (`factorylab/cortex/schematics.py:707-744`). Discovery now
grants one additional tool round, and the bound stays two regardless of repeated lookups
(`factorylab/runtime/compute.py:1310-1360`). Existing total-cost accounting and restricted
continuation after fetched outside text remain in the caller (`:1322-1334`, `:1400-1463`).
The claim is bounded discovery feasibility, not arbitrary multi-step tool competence.

Evidence timestamps are captured at evidence assembly and carried with the commission
(`factorylab/runtime/feedback.py:1490-1534`); older missing timestamps remain unknown.
The citation schema enumerates supplied references without deciding which facts establish
value (`factorylab/runtime/loop.py:91-144`, `:1194-1197`). This is a useful interface fix.
The real evaluator caller thaws event payloads before settlement (`:1100`, `:1245-1247`),
so the previously alleged mapping-proxy refusal is not a production defect.

I found no reason in these four diffs to roll back the fixes. F1 is an existing gap in the
larger C path, not a defect newly introduced by the snapshot/citation changes. The
nonfinancial fixture's ambiguous existential labels remain a diagnostic limitation, as
the current report correctly admits; its exact-label count is not a model accuracy rate.

## Minimal sequence and explicit dissent

1. Keep the running world's bytes and interpretation unchanged. Read its result only
   after death; the lead owns this and the paired consequence run.
2. Reproduce and repair F1 through the existing evaluator/cascade path before promoting C.
3. Close one relevant nonfinancial dependency with the F2 witness and quantify F3's actual
   exposure/runway. Keep context reduction; do not commission a new institution first.
4. Give one future experiment enough funded lifetime to exercise a revisable criterion
   and observe its consequences. Then distinguish independently earned operation from
   endowment consumption with F5's custody-to-provider evidence.

Delete the claim that a successful execution witness completes the useful-nonfinancial
gate. Delete redundant institutional prose when authoritative discovery supplies it.
Review duplicate mechanisms only after they have had feasible, discoverable opportunities.
Do not delete protected novelty, independent evaluators or private state because short
runs did not yet use them. Do not add inactivity rent merely to force visible behavior.

**Dissent from v7:** the strong diagnosis of opinion-following deserved investigation,
but the conclusion that money should be the only reward and markets should replace judges
does not follow from the essay. Its futarchy discussion is a possible governance design,
not a requirement to collapse values into cash; it expressly retains a nonfungible
realized-consequence standard (`docs/essay.md:578`, `:598-602`). A factory can choose
finance. An architect-installed profit objective would answer a different question.

**Dissent from an overly reassuring reading of the current gate:** cited runtime evidence
and an independent model are necessary checks, not sufficient causal grounding. Producer
weight movement is not useful cooperation; useful cooperation is not self-support;
self-support is not endogenous objective formation. The current evidence supports none
of those substitutions. Conversely, no finite list of prelaunch fixtures can certify
Class 3 in advance. After the concrete broken loop is repaired and a viable experimental
lifetime is specified, the next meaningful test is a bounded autonomous world, not an
unbounded sequence of architect-written acceptance scenarios.

## Verification and uncertainty

Read the essay including Chapter II, references and notes; README and AGENTS; the manifest
contract sections; v7 first-principles audit; edition4 results and execution plan; current
results, nonfinancial design, deployment and validation reports; the complete four-file
production diff and the relevant source callers above. Historical memory was used only to
locate the distinction between mechanisms and observed institution-building; all substantive
findings here are grounded in the current local files. External papers, the host and live
services were not independently queried. The Opus audit is separate and was not read into
these conclusions.

No new test ran in this read-only pass. The existing release validation reports Ruff and
2,114 checks passing; its relevant-gate output is reproduced verbatim below, not claimed
as an execution by this auditor:

```text
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
plugins: xdist-3.8.0, anyio-4.15.1
created: 2/2 workers
2 workers [20 items]

....................                                                     [100%]
============================== 20 passed in 8.16s ==============================
```

The deployment report records the release installed with the factory service inactive and
disabled. That is reported installation evidence, not a fresh host check or a statement
that no separate rehearsal process is running. No new live-population learning, demand,
governance or autonomy result is claimed here.
