# Chapter II rulings: the architect's decisions on the seven-slice audit

Branch `fast-loop-harness`, 2026-09-22. Seven auditors held the codebase to
Chapter II, one slice each plus a cross-cutting Class 1/2 sweep. Their reports are
in `docs/audits/chapter2/` (primitive, information, versioning, evaluations, charter,
time, smuggling).
They list about 150 findings. This document rules on them.

- Where an auditor misread the chapter, the ruling says so and cites the text.
- Where auditors disagree, the ruling picks one reading and gives the reason.
- Every change that follows must cite a ruling here, or a passage of Chapter II.

The authority is Chapter II (`docs/essay.md`, "THE DARK STACK"), as `AGENTS.md` now
states.

## 0. What the audit found, in one paragraph

The factory has most of Chapter II's organs, and several of them are wired
backwards:

- **Outside grading.** The only outside grading of judges that ever fired had its
  sign inverted; fixed in 50f136d.
- **Meta tier.** Every meta grade in 18 runs was a kernel zero for a form error.
- **Producers' "realized consequence".** Another model reads the charter under a
  rubric that excuses losses.
- **The router's "wake nobody" arm.** It earns an architect-set constant that no
  charter price touches, so the frontier drifts to abstention (learning death).
- **Kernel-written plans.** The kernel told seats which action class to take (my
  exploration draw), wrote their action menu, and patched behaviour through prompt
  sentences whenever a run looked wrong.
- **Governance.** Summoned per motion, never seated. The architect wrote the cards
  the factory runs on.
- **Clock.** One unjittered window drives about twelve loops, in four clock units,
  with outer loops faster than the loops they correct.
- **Channel between seats.** Direct messages and a public notebook sat beside the
  two prescribed channels. Neither was ever used, and both could contaminate the
  judges.

## 1. Rulings on conflicts and misreadings

**R1. Producers learn from verdicts. Realized consequence grades the judges.**
§III.b: "Productive agents … learn from evaluator agents through the coupling of
scoring and propensity." The sentence "this cannot be the only reward mechanism"
refers to *the evaluations layer*. The second signal is "a judgment of whether a
given verdict predicted real downstream outcomes."

- *Overruled.* Evaluations C6, which wanted verdict mode deleted, and the part of
  evaluations C5 that pays producers the kernel's PnL directly.
- *Upheld.* Evaluations C3, C4 and C5's deletion of the LLM "final judge"; smuggling
  A5, A6 and F1.
- *Target:*
  - A producer's reward is the verdict of its judge or judges.
  - A verdict in [0,1] is also a prediction.
  - When the world resolves the decision, the kernel scores that verdict by Brier
    against the **measured** outcome, and that score is part of the judge's own
    reward (not just a routing weight). The measured outcome depends on what the
    decision did:
    - executed operations: `return_paid_off` (realized or marked PnL net of cost);
    - a hold that named a declined trade: its opportunity-cost price (below);
    - a bare hold: no world outcome, so only the tier above grades that verdict.
  - Metas grade judges for compliance with the charter. Nothing replaces the world
    as the second signal.
  - Deleted:
    - the `realized` producer-feedback mode;
    - the grounded final-judge pipeline and its rubric;
    - `settlement/weights.py`, because the charter must not weight the outside
      signal ("sits outside the factory's input entirely");
    - the optional-payoff dance.

**R2. Opportunity cost stays, but only as a measurement of the world.**
It is a fact about the world: what the declined trade did, net of fees, priced ex ante
on the named trade. So it is a legitimate realized-consequence measurement, "prebaked
into the superdark factory at the Stackelberg move".
- *Overruled.* Smuggling A5's "rewrite as a producer forecast". A producer could still
  volunteer forecasts; the measurement stands without them.
- *Upheld.* It never *replaces* the judge's verdict as a producer's score (my wiring
  of 6f1c23b was wrong). The request text that announces the rule to producers goes
  (A4). The `counterfactual` field keeps a factual schema description only.

**R3. The early-warning statistics are prescribed; they stay and go live.**
§III.a: "variance, autocorrelation, and ensemble disagreement … cycle at every level
of the dark stack."
- *Overruled.* Versioning U4, which wanted them deleted.
- *Upheld.* Evaluations M2: compute them online and publish them to the evaluators.

**R4. A declared propensity is the agent's own accounting, and learners may train
on it.** §I.b: the propensity is "an agent's own accounting of the statistical field
it drew from … the model's own probability assessment of the tokens it emitted".
- *Overruled.* Primitive F10 ("train only on kernel-sampled propensities").
- *Kept.* The floor (`MIN_DECLARED_MASS`).
- *Upheld.* Information P3: when a registered learner exists, the kernel samples it
  and discloses `{recommended, p}` rather than the distribution.

**R5. The kernel never chooses a seat's action.** The exploration draw is deleted
(primitive F2, information S2, smuggling A1/A2). I built it. It misread §II.b, which
asks for "some share of compute and write access … usable only in the context of
unhistoried actions", a niche in the *world*. The novelty reserve is that niche for
assemblies. It is rebuilt so that unhistoried *actions* by existing seats can draw
on it too.

**R6. "A decision acts once" and batch atomicity are physics. The cross-decision
duplicate-order refusal is a guardrail.**
- *Kept (kernel physics):*
  - idempotent client identities;
  - an answer never executes a refused or dropped write;
  - a batch of writes is weighed whole.
- *Deleted:* the refusal of an identical resting order placed by a *later* decision
  (smuggling G1). The venue allows it, and fees price it. §III calls such rules
  "unit tests written before production".

**R7. World surfaces are the world.** Hyperliquid, its vaults, Polymarket and the
connectors stay as surfaces with physics-only descriptions. Unused off-by-default
surfaces are not deletion targets for being unused.

**R8. History is forensic, never constitutive (§II).** Old world files move to
`worlds/history/`, and the wake lookup follows them.
- The 28 hash-neutral manifest shims go (versioning S1). A manifest names the physics
  it runs under. When the physics changes, the world is new: "a new factory begins
  from a new v0".
- `git HEAD` leaves the release digest (versioning S2). The executable tree and lock
  stay, and a real kernel change is still lethal.

**R9. NOOP is not free.** A router may still draw "wake nobody", because not spending
is a real choice. But its reward is not an architect constant. It bears the same
charter prices a woken decision bears (versioning P4, primitive F1). Where the world
can price inaction, it uses that price. The separate `router.learning_death`
observation folds into the single learning-death diagnosis (versioning U1, time T16).

**R10. The judge tier is mostly mean-based (§III: "a significantly higher population
of mean-based no-regret judges than … swap-based").** The Blum–Mansour core moves out
of judge selection. It is seeded where it retains frontier surplus, beside the
frontier routers of producer kinds (evaluations C2, primitive F8). That placement
must be argued in the code, not asserted in a comment.

**R11. No direct messages, no public notebook.** §I.b prescribes two channels: rich
requests and thin rewards. `address.send`, `note.*` and artifact publication are
deleted (information U1–U3, C6). Collaboration is composition through requests.
Its credit flows back through the reward channel (see W4 below). Working-state rent
survives as a storage-rent parameter.

**R12. Behaviour-mix deltas never justify a change (§I.a, Carroll).**
- The seven prompt-tuning commits the smuggling audit found are reverted in edition 6.
- `scripts/fastloop.py` keeps its plumbing, cost and prompt-size metrics, and loses
  its action-mix and compliance targets.
- `calibrate_seats.py` loses its obedience ("SAFETY") cases.

## 2. Target architecture (what Chapter II asks for, stated as the build)

- **Primitives.**
  - An assembly publishes a self-description with its contract (`id`, `accepts`,
    `emits`, `description`). Tools promise a `returns_schema`.
  - The universal return contract carries continuity, propensity, registrations,
    requests, tool calls and forecasts. Venue order semantics live in the producer
    kind's schema, not in every primitive.
- **Composition.**
  - A request names a *kind* of work, not an assembly id. That kind's router draws
    the executor with a logged propensity, so composition is sampled, learned and
    survives retirement.
  - The request forwards its author's propensity.
  - When the requesting decision is scored, the reward flows back to each executor it
    composed. That is the collaboration credit, carried by the ordinary reward channel.
- **Information.**
  - Judges see a machine view: request, output, executed operations, propensity. No
    author identity and no role clause. The same projection applies to metas.
  - Each seat's private state stays private, including program stdin and artifact
    listings.
  - Refusals go to the owner's inbox. Pathology labels go to observers, not seats.
- **Evaluations.**
  - Evaluators are the majority of seats and compute, which is a manifest invariant.
  - A share of returns goes to two or more judges on different model families (never
    the author's), which makes ensemble disagreement possible.
  - The meta tier reads a meaningful share of verdicts, and recursion can deepen.
  - A malformed or refused judgement is censored at its cost, never graded 0.
  - There is no kernel evaluability list: judges may decline.
  - The adversarial layer is plural: a chaos actuator for real, bounded faults;
    adversarial judges; antagonists of both learner types.
  - Early-warning statistics are live.
- **Versioning and pathologies.**
  - A live transfer operator gives the current version, a gap per version, and
    settling time.
  - Stable failure is a wide gap while failing, and its duration ratchets the gain,
    charged to abstention too.
  - Thrash is volatility of the gap, priced by its own PID term.
  - Learning death is read from frontier invocation, and answered by the niche for
    unhistoried actions.
  - The immune organ acts at least 3:1 slower than the price loop.
  - One price law: PID.
- **Charter.**
  - A standing sortition, stratified over roles and learner types with a quorum, is
    seated at each governance-cadence boundary.
  - Seats post λ as a shadow price, staked through the reward channel.
  - Conditional forecasts on motions cover both branches (futarchy), feeding forward
    into the PID.
  - Evaluators can add holdout criteria to cards.
  - A signed norm edition from the human house applies at a cadence boundary, after
    testimony.
  - Motions are unbundled, and speed motions carry a burn prediction.
  - The next charter's cards are drafted by the population.
- **Time.**
  - Every loop is counted in ticks, with the measured interval used only to convert.
  - Every outer loop's period is derived as at least `min_ratio` times the measured
    period of the loop it commands, each with its own jitter.
  - Money caps have their own durations.
  - A model call's deadline is a ratio of the tick.
  - Safety processing (fills, watchers, wind-down) never waits behind a model call.

## 3. Waves

Each wave works on its own branch and passes the full suite, the gate and slow tiers,
and a scripted harness run. It gets a cold review before merging. Money-path waves
also get a second review.

- **W1: delete and stop leaking.**
  - Channels and leaks: R11, and information C1–C5, C7, C8, U4, P5, P8.
  - Kernel-written plans and rubrics: R5, R6, R12, and smuggling A3, A4, A6–A10,
    B/C (edition-6 world), D1–D6, E3.
  - Dead code: charter U1–U4, versioning U2, U3, U5, S1–S3, time T9, smuggling D-1
    to D-7.
- **W2: the reward chain.** R1, R2, R9, R10; evaluations C3, C4, P2, P3, S2–S4, U1, U2;
  primitive F1.
- **W3: the clock.** Time T1–T8, T10–T13; versioning P5.
- **W4: composition and collaboration.** Primitive F3, F5–F7, F9; information M1–M3.
- **W5: evaluations and pathologies built out.** Evaluations C1, M1–M3, P6;
  versioning C1–C3, M1–M4, P1–P3; time T14, T15, T18.
- **W6: the charter built out.** Charter C1–C3, M1–M7, P1–P5, S1–S4.
- **Then:** edition 6 is ratified by the population, the capital-loop rehearsal runs,
  and long testnet runs follow.
