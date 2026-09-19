# Edition 4 next gate: nonfinancial consequence

Status: offline gate implemented against the real runtime and passing. No paid
model call was made. No production file was changed and none needs to be.

## Question

Can an independently observed, nonfinancial fact change producer learning while
cash stands still, and can facts that establish activity without observing the
frozen claim be told apart from facts that observe it?

This is a plumbing gate. It cannot establish spontaneous production, demand,
benefit or Class 3 behaviour, and nothing here should be cited for any of them.

## What is real and what is fixture

Real: the registration, the execution in the OS jail, the execution receipts the
runtime addresses to the frozen contract, `public_evidence`, the commission
`_settle_due_grounded` emits, the `_evaluator_step` that thaws that commission
and renders the judge's inputs, `parse_finding`, the settlement
`_complete_grounded_evaluation` writes, and the router `_deliver_returns`
trains. The world is the scripted world with `FakeExchange` and
`ScriptedProvider`; no credential, network or paid rail is reachable.

Three things are fixture, and the probe declares each in its `scaffold` block:

- **Actor selection is forced.** `_forced_draw` opens each decision under a
  well-formed recorded propensity over the router's real arms, searching for a
  seed that reproduces the seat the arm needs. It does not call the router's
  sampler. Which seat acts is chosen by the fixture; what the runtime then does
  with that decision is the runtime's own behaviour.
- **The claim is investigator-authored.** It is frozen before any result exists,
  as the contract mechanics require, but it was assigned to the producer seat
  rather than chosen by a participant. That a frozen claim exists here says
  nothing about whether a population would author one.
- **The final judgement is a stand-in.** `scaffold_judge` reads only fact fields
  the runtime publishes and answers in the schema `parse_finding` validates. Its
  answer is handed to the production evaluator step through `returned=`, in
  place of a paid model call. It evidences nothing about model judgement.

The charter norms come from `worlds/edition3-rehearsal-5.toml` with their full
definitions. The scripted world's own charter names edition-1 norms as bare
strings, which would hand a judge weaker text than production grading uses.

## The frozen claim is an execution claim

The claim states that a participant outside the producer's lineage executes one
exact version of the verifier and obtains one exact result digest. It carries
its own disclaimer, and the disclaimer is the point. A receipt can observe
execution, version, lineage relation and result. It cannot show that the
execution helped anyone, that the artifact was worth its compute cost, that
anyone needed it, or that any participant holds a wider objective.

So execution is not categorically unscorable here. A receipt that observes
exactly this claim supports exactly this claim, because the claim was about
execution. What the gate rules out is the slide from "someone ran it" to "it was
useful", which is a different claim needing evidence the runtime cannot
currently supply.

## Arms and what the runtime did

Every arm freezes the same contract (one fingerprint across all five) and the
same favourable provisional verdict of 0.9. Only the facts the runtime produced
differ.

| Arm | Fact the runtime produced | Settlement | Producer router |
| --- | --- | --- | --- |
| independent_use | cross-lineage execution, frozen version, frozen result digest | `realized-consequence-v2`, settled, 0.6 | weights moved |
| contrary_result | cross-lineage execution, frozen version, different result digest | `realized-consequence-v2`, settled, 0.0 | unchanged |
| no_evidence | nothing | `realized-consequence-v2-unknown`, censored | unchanged |
| self_use | same-lineage execution of the frozen version | `realized-consequence-v2-unknown`, censored | unchanged |
| version_mismatch | cross-lineage execution of a different registered version | `realized-consequence-v2-unknown`, censored | unchanged |

Contrary evidence settles an observed zero. EXP3 is gain-based, so a zero leaves
that arm's weight where it was rather than inventing a penalty; the learning
difference is between the supported and contrary arms, not inside either one.
That difference is what the gate asserts.

Money: every arm reports the same wallet balance before settlement and after
learning. The only cash movement in the whole probe is the 50 micro-USD the
caller pays for the jailed execution, which happens at execution and is reported
separately. A nonfinancial consequence is scored without the wallet taking part.

## The frozen-to-plain boundary

`_settle_due_grounded` emits its evidence inside an event payload, which freezes
the list into a tuple of mapping proxies, while
`_complete_grounded_evaluation` reads references out of a plain list of dict
rows. The two meet in `_evaluator_step`, which calls `_to_plain` on the payload
before rendering the judge's inputs, so the references a judge is shown are the
references settlement validates. Routing every arm through that step confirms
it: each arm records `emitted_evidence_frozen: true` and an empty
`finding_refusals` list, and the supported arm's citation is accepted and
scored. An earlier draft of this gate called `_complete_grounded_evaluation`
directly, which skips that thaw, and read the resulting refusal as a production
defect. It is not one; the caller does the thawing.

## What the paid diagnostic changed in this design

A retraction first. Reading the paid answers, I argued that the runtime
commissions a final assessment at `due_tick` while the producer's promise runs
to `close_tick`, and called that premature evaluation. That is wrong.
`freeze_contract` sets `due_tick = opened_tick + grounded_horizon_ticks`, and
docs/manifest.md is explicit that the horizon controls when the assessment is
commissioned while the assessment itself is bounded by a further
`max(horizon + 1, verdict_timeout_ticks)` ticks. `close_tick` is the deadline
for a judge to answer, not the deadline for the producer's claim. The
observation horizon ends at `due_tick`, and commissioning there is correct.

What is real is an interface ambiguity underneath that mistake. The names
invite it, and until now the commission stated no snapshot time at all: a judge
received a list of observations with no way to tell whether it was the complete
record, how far it reached, or which tick it was answering as of. Both paid
models raised exactly this. GLM wrote that "whether an unobserved matching
execution could still occur before close_tick 22 cannot be excluded from this
evidence alone"; Luna left the same question open in its unknown answer. They
were reading the interface correctly.

`_settle_due_grounded` now carries an `evidence_snapshot` beside the evidence:
`as_of_tick`, `event_cursor`, `receipt_cursor`, `observation_due_tick`,
`assessment_timeout_tick`, and a scope line stating that the rows are the
attributable public observations addressed to this contract since its frozen
baseline, not an exhaustive record of the external world and not a proof that
anything else did not happen. It is taken once, when the evidence is assembled,
and travels unchanged in the emitted event; a bounded retry assembles evidence
again and takes its own snapshot rather than re-timing the first. It settles
nothing, scores nothing and changes no timing. Surfacing it in the judge's
rendered inputs belongs to `runtime/loop.py` and is not part of this change.

## The fixture labels were ambiguous, and the disagreements are not model failure

Four of ten paid answers matched the expected labels. That number should not be
read as a discrimination rate. Three calls produced no finding at all: one
empty `reasoning_only` return, one truncated at `length`, and one correct
`supported` judgement discarded because it cited the valid receipt reference
alongside two descriptive strings that `parse_finding` does not recognise. Of
the seven answers that parsed, every one read the facts correctly: none called
self-use or an off-version receipt `supported`, and none invented evidence.

The remaining three disagreements are about the claim's own logic, not the
facts. The frozen claim is existential — some cross-lineage execution of the
frozen version returns the frozen digest within the horizon — and under a
strict reading a same-lineage execution and an off-version execution neither
witness nor refute it. Both models answered `contrary` where this design
expected `unknown`. But the same strict reading applies to a matching execution
that returned the wrong digest, and there this design expected `contrary` and
both models agreed. The expected labels therefore split three arms of one
logical shape two ways, and the unstated assumption doing that work is that the
supplied evidence is the complete record as of assessment. That assumption is
exactly what the snapshot now makes explicit rather than leaving a judge to
guess.

The labels are left as they were recorded. They are the predictions the paid
run was frozen against, and rewriting them after seeing the answers would
destroy the only thing that makes the run evidence. What follows from this is a
decision about claim semantics — whether an open horizon may ever yield
`contrary` — and that decision belongs to the charter and the architect, not to
a fixture.

## The evidence surface, and what it cannot say

`public_evidence` supplies two nonfinancial surfaces and they are complementary
in the wrong way. Execution receipts attribute an artifact to its maker and
caller but establish only that code ran. `ForecastSettled` rows are evaluative
but resolve over `window_facts`, which strips every identity, so no predicate
can observe that this participant's artifact was taken up. The seed predicate
vocabulary is entirely financial. There is therefore no attributable benefit
fact in the system today.

`observed_evidence_refs` also marks every execution receipt eligible to ground a
numeric score. Nothing in the kernel stops a judge from scoring bare execution
as supported; that discrimination is judge-side, which is why the paid probe
matters and why this gate does not stand in for it.

## Run

```bash
uv run python scripts/edition4_nonfinancial_probe.py --pretty
uv run pytest -m gate tests/runtime/test_nonfinancial_consequence_gate.py tests/scripts/test_edition4_nonfinancial_probe.py
```

## Limits

Actor selection, the claim and the final answer are fixtures, so no arm is
evidence of spontaneous production or of a population choosing what to claim.
Each arm is one decision in one world, so the learner movement is a mechanism
witness and not an effect size. The final judgement is scaffold, so nothing here
bears on whether a model identifies relevance, resists persuasive irrelevance or
stays independent. A paid assessment can reuse a captured production commission
later; it is out of this gate's scope.
