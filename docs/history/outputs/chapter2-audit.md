# Factory Lab: audit against Chapter II

10 September 2026 · Design audit, before implementation

**Verdict: v0.1 was a useful adaptive-agent baseline, but it omitted mechanisms central to the proposed dark stack. Building it unchanged would not test the architecture we intended to study.** Calling those omissions mere reductions in scale was too generous. No software has been implemented, so these are design failures caught before a build, not observed runtime catastrophes.

This audit covers the operative design claims throughout Chapter II, checked against the abstract and Chapters I and III, and checks the main learning distinction against the cited research. It separates fidelity to the essay from evidence that the essay's architecture works. A faithful experiment could still fail. The essay itself leaves important engineering and theoretical questions unresolved.

The original is preserved as `project-plan-v0.1.md`; the first correction is `project-plan-v0.2.md`. The current `project-plan.md` is v0.3, incorporating a subsequent independent Fable CLI review. See `fable-refinements.md` for that second audit's disposition. All runtime verification below is **required future evidence**, not completed testing.

## The most serious failures

### 1. We substituted tuning differences for different learning mechanisms

Our EMA estimates and softmax/epsilon sampler did not implement the two algorithmic populations we were discussing. Changing memory length, temperature or a persona prompt does not supply the missing conditional learning machinery. Worse, a permanent exploration floor over existing actions could accumulate linear regret while still being advertised as a no-regret learner.

The correction is to establish actual external-regret and swap-regret reference implementations in finite local games, then preserve their distinct update structures in the experimental population. Separately protect the invention and trial of new actions. Changing menus, delayed observations and revised reward meanings require declared adaptations; they do not inherit a global theorem by naming an algorithm. See [Incentive Design][incentive].

### 2. Our evaluators were mostly a testing service

Deterministic graders and occasional independent critics can validate accounting or catch mistakes. They do not supply the adaptive evaluator population, evaluator learning, or composable evaluations of evaluations that our experiment requires. A fixed two-level ladder would also make the seed topology permanent.

Retain deterministic settlement and kernel checks. Add persistent learning evaluators with their own decisions, scores, costs and delayed feedback; support evaluation and composition of those evaluators through the same contracts. Seed more evaluator than producer capacity, with a substantial exploratory evaluator population. A finite budget constrains actual depth; the initial depth must not define all possible future organization. See [Evaluations][evaluations] and [Catastrophes][catastrophes].

### 3. We turned accountability to the world into observation by a dashboard

The three-surface design prevented a fixed dashboard from vetoing every tradeoff. That was useful, but incomplete. It left a route for agents and judges to award one another excellent charter scores while nobody's learning or resource allocation suffered when those judgments failed downstream.

Add a separate consequence settlement channel. Each eligible evaluator prediction is committed before its outcome; an independently obtained event settles it; that result updates the evaluator's learning and consequential standing. The committee cannot revise away this channel or exchange charter points for a better historical consequence record. The research audit remains separate. See [Overfitting and Adversarial Populations][adversarial].

### 4. We postponed timing until after the system was built

“A slower governance interval” is not a sufficient timing contract. Feedback can arrive after the affected configuration disappears. A committee can revise again before its previous intervention has settled. Shared infrastructure can synchronize nominally independent agents.

The first kernel must support relational separation between nested feedback loops, buffered statistical reports upward, immediate authorized commands downward, and jitter. Their periods must respond to observed feedback and settling times; role names do not determine permanent bands. Detailed spectral identification can mature with data, but the queue semantics cannot be bolted on afterward without changing the first commitment. See [Time and Phase][time] and [Clocking the Factory][clocking].

### 5. Random trials were mistaken for a protected frontier

Sampling another known project does not give an unfamiliar project resources, writing rights, time to receive its first outcome, or access to mechanisms that can retain it. A colony could show plenty of random activity while rejecting every new capability at the retention boundary.

Reserve bounded compute **and capability-limited write access** for genuinely new proposals, preserve them through their declared feedback horizon, and provide a route into ordinary composition. Renaming an old project must not replenish its novelty allowance. This is distinct from exploration inside a finite learner. See [Versioning: Commitment and the First Commit][commitment] and [Time and Phase][time].

### 6. Diagnoses had no corrective effect

v0.1 could display stable failure, overfitting, learning death or thrash without changing what the population was paid to do. Its charter prices were optional, and adaptive penalties were deferred.

Each diagnosis needs an operative response, a resource cost, a feedback delay and a way to discover that the response made matters worse. A bounded price-update rule can operate under committee-approved authority without a vote on every update. PID is an optional controller, not an established cure. Escalating penalties indefinitely can create the oscillation we wanted to prevent. See [Pathology Detection][pathology], [Commitment][commitment] and [Time and Phase][time].

### 7. The broker could quietly own the entire choice architecture

A deterministic broker is useful for permissions, reservations and settlement. It becomes a substantive governor if its permanent scoring rule selects every project, fixes all compositions or chooses all reward tradeoffs. Splitting it into several programs would not solve that authority problem.

Keep the trusted mechanism small. The population must be able to propose and revise assemblies, routing, local choice policies, tools and operational criteria within its committed rights. The seed organization is disposable. Local algorithm state stays private; self-describing contracts carry enough information for composition. See [The Primitive][primitive] and [Information Design][information].

### 8. Paper trading could be asked to establish more than it can

A prediction committed before a future price arrives has an external outcome. Paper fills, paper losses and synthetic bounties do not demonstrate financial exposure, actual customer demand or effects on a live market. Paying for model calls is a real cost, but is not by itself a consequence score for a particular evaluator judgment.

Keep paper trading as one experimental activity. Add a clearly identified path from predictions about an owned running service to actual recorded operational outcomes. Controlled faults can affect that service, but remain a limited operationalization of the essay's much stronger production-catastrophe argument. No loss of real trading money is necessary to start this narrower work, and none is authorized. See [Adversarial Populations][adversarial].

## Detailed coverage ledger

“Break” means v0.1 omitted or replaced a central mechanism. “Partial” means it needed an enforceable specification. “Keep” means it was already a defensible choice. “Boundary” identifies a claim the experiment cannot establish. “Optional” identifies a source example that need not be copied. These are design judgments, not measured failure probabilities.

### I. The Primitive — composition and learning

Sources: [The Primitive][primitive], [Incentive Design][incentive], [Information Design][information].

| ID | v0.1 finding | Required correction or evidence |
|---|---|---|
| P01 | Partial: registry entries described projects, but not enough independently replaceable parts. | Register tools, prompts, memory policies, evaluators and assemblies with versioned contracts. Replace one provider without changing its consumers. |
| P02 | Partial: new projects were allowed, but the operating loop remained architect-owned. | Demonstrate a population-proposed rerouting, parallel composition, replacement and removal. No permanent agent needs the full topology. |
| P03 | Keep: requests specified inputs, costs, deadlines and outputs. | Test execution by a fresh consumer with no private conversational history. |
| P04 | Partial: anonymization was described mainly for judges. | Make execution requests author-neutral too; preserve authenticated lineage in the trusted ledger for rights and accounting. |
| P05 | Keep: temporary orchestration was allowed. | Record its dismantling or alteration as an experiment; do not harden its roles into the kernel. |
| P06 | Partial: rich schemas might freeze the designer's project taxonomy. | Permit capability composition and new schemas through declared interfaces; keep initial activity labels provisional. |
| P07 | Break: different tuning settings stood in for learner classes. | Validate distinct update algorithms. An LLM's fluency, confidence or persona is not a learning guarantee. |
| P08 | Partial: project menus changed freely. | Define local comparator sets and epochs. At least three actions are needed for the cited two-versus-three-action contrast; three are not sufficient for novelty. |
| P09 | Partial: memory persisted only within loosely defined generations. | Identify which state learns across rounds and what survives an amendment, replacement or retirement. |
| P10 | Keep the actual sampling record; partial algorithmic scope after feasibility filtering. | Verify normalization, zero support and selection frequencies. Log exclusions; a valid propensity does not restore a reference algorithm's invariant or comparator after arbitrary filtering. |
| P11 | Partial: counterfactual scoring remained generic. | Implement the chosen partial-feedback algorithm exactly; log its estimator and assumptions. Propensity does not reveal an unchosen outcome. |
| P12 | Break risk: retained full distributions could become shared agent context. | Enforce access by role and decision, including exports and tools. A private audit ledger must not become a global agent memory. |
| P13 | Partial: feedback payloads had no precise separation from explanation. | Use an addressable scalar learning return per declared score channel. Put diagnostic prose in separately authorized requests. |
| P14 | Partial: information permissions were described, not mapped. | Publish schema/protocol rules; keep local scores, memory and full distributions private; pass only the needed decision propensity along the relevant route. |
| P15 | Partial: population variety could collapse despite different settings. | Measure invocation, resource survival and realized update diversity; protect the frontier without mandating identical beliefs. |
| P16 | Boundary: no guarantee of emergence follows from assembling these parts. | Report local algorithm performance and observed novelty separately from claims about readable space. |

### II. Versioning — identity and correction

Sources: [Versioning][versioning], [Pathology Detection][pathology], [Commitment][commitment].

| ID | v0.1 finding | Required correction or evidence |
|---|---|---|
| V01 | Partial: behavioral summaries emphasized action allocation. | Include outcome and evaluator distributions conditional on inputs, charter and environmental regime. Allocation alone is insufficient. |
| V02 | Partial: charter editions could be mistaken for ordinary code versions. | Distinguish world identity, charter edition, learner generation and inferred behavioral regime. A charter change is a declared regime boundary even before enough new data accumulates. |
| V03 | Keep: code and behavioral changes were separate. | Retain forensic hashes without describing a replay as restoration of the same living factory. |
| V04 | Missing: evaluator behavior had no versioning of its own. | Detect changes in scoring relationships and settlement calibration; a changed ruler can mimic a changed subject. |
| V05 | Partial: advanced estimators were deferred without a replacement. | Start with explicit conditional windows, uncertainty and transition counts; output “insufficient evidence” where required. |
| V06 | Boundary: an arbitrary spectral-gap statistic could mislabel stability. | If using a transfer operator, declare cells, lag and retained modes. Distinguish separation of slow modes from the ordinary mixing gap; validate on known processes. |
| V07 | Partial: stable failure was a dashboard diagnosis. | Track persistent unmet criteria conditional on comparable inputs; attach a duration-sensitive corrective price and measure escape or overshoot. |
| V08 | Partial: good scores could mask failure against intent. | Compare charter scoring with independent consequences and adversarial discoveries; a stable or improving regime is not proof against gaming. |
| V09 | Break: new-project exploration had no resource/write guarantee. | Verify a protected new proposal can run, write permitted artifacts, receive feedback and reach a retention decision. |
| V10 | Partial: thrash detection lacked attribution and response. | Track replacement relative to feedback age, retained benefit and costs; test misapplied late feedback and corrective damping. |
| V11 | Keep: material kernel changes started a new run. | Apply this also to charter write rights, accounting authority and foundational timing rules. Record termination rather than transparent rollback. |
| V12 | Keep: real isolation and broker-held credentials were required. | Verify enforcement against generated code; instructions alone are not a boundary. |
| V13 | Break: prices were nonoperative or postponed. | Give every enabled soft penalty units, bounds, update rule and a traceable effect on learning/allocation. Keep real expenditure separate. |
| V14 | Optional: a full PID controller is not essential to the first experiment. | A simpler bounded rule is acceptable if its delay, saturation, recovery and failure behavior are explicit. |

### III. Evaluations — consequence and adversarial learning

Sources: [Evaluations][evaluations], [Catastrophes][catastrophes], [Adversarial Populations][adversarial].

| ID | v0.1 finding | Required correction or evidence |
|---|---|---|
| E01 | Break: model critics were sampled adjuncts. | Make evaluation a continuously scheduled participant in actual runtime, with persistent adaptive state. |
| E02 | Keep: deterministic graders suit objective arithmetic. | Keep accounting and settlement checks, while distinguishing them from learning judgment. |
| E03 | Break: no substantial exploratory evaluator population. | Seed both learning types, more exploratory than retentive evaluator capacity, and verify both are actually invoked. Labels and headcounts alone do not suffice. |
| E04 | Break: evaluator composition was absent. | Use the same contracts for judging artifacts, judging verdicts and recombining judges. Seed recursion; allow topology changes within the resource envelope. |
| E05 | Break: evaluation could be the first budget item cut. | Reserve meaningful evaluator and settlement capacity; reduce producer throughput when the budget cannot support feedback. |
| E06 | Partial: judge correctness was mostly conformity to criteria. | Add delayed consequence settlement outside the judged loop and the committee's editable metrics. Show that incorrect judges lose consequential standing. |
| E07 | Missing: a universal scalar could erase that distinction. | Preserve separate consequence records and an operative allocation channel that charter points cannot cancel. |
| E08 | Partial: “independent critic” did not specify leakage prevention. | Hide producer private state and future outcome; allow judges to learn from their own past settled results. |
| E09 | Partial: catastrophe detection was reduced to planted fixtures. | Track multiscale variance, persistence and disagreement in operation. Measure false alarms and missed events; no catastrophe guarantee. |
| E10 | Break: adversarial activity was mostly offline testing. | Keep learning antagonists inside the owned runtime, able to generate bounded stress and producer/evaluator failures with real service effects. |
| E11 | Missing: adversaries could create easy incidents and collect rewards forever. | Separate incident authorship from forecast credit, authenticate lineage, cap repeat rewards and score control/no-fault periods as well as incidents. |
| E12 | Keep with clarification: most judges should perform ordinary evaluation. | Use a minority adversarial cohort spanning production and evaluation; do not turn every judge into an attacker. |
| E13 | Boundary: owned-service failures and paper markets are limited worlds. | Label actual operational effects, external observations and simulations separately. Do not claim full-scale catastrophe exposure or market reflexivity. |

### IV. Charter — operative governance

Sources: [The Charter and the Loop][charter], [Self-Writing][selfwriting].

| ID | v0.1 finding | Required correction or evidence |
|---|---|---|
| C01 | Partial: the charter began as qualitative norms plus a vote. | Require conditional metrics with units, windows, sample requirements, uncertainty, missingness and operational consequences. |
| C02 | Keep: factory participation in amendments was explicit. | Verify participation changes enacted metrics, prices or criteria; rewritten prose alone does not qualify. |
| C03 | Partial: allocation authority could remain outside every amendment. | Trace at least one amendment through measurement, reward or pricing into subsequent choices. |
| C04 | Partial: a three-person committee did not ensure representation. | Use stratified sortition across learning types, producers, evaluators and antagonists; rotate seats and prevent lineage multiplication from creating votes. |
| C05 | Missing: delegate anonymity was not carried through deliberation. | Use round-specific delegate aliases; keep trusted identity records while avoiding reputation-based privileges in committee payloads. |
| C06 | Keep: fixed norms did not automatically rule out the experiment. | Specify a read-only normative layer and revisable operational interpretations. Do not confuse bounded rights with proof of Class 2 or Class 3. |
| C07 | Partial: prices were posted as optional policy fields. | Require factory-originated evidence and price proposals; a human need not know the factory's complete marginal-cost landscape. |
| C08 | Keep: amendments need not improve every audit dimension. | Record accepted tradeoffs; hard resource limits and immutable settlement remain enforceable. |
| C09 | Keep: outcome definitions stayed versioned. | Retain old evidence, label reused validation data, and preserve a fresh prospective period. Missing data cannot become success. |
| C10 | Optional: DAO machinery and futarchy were omitted; token-weighted voting remains excluded. | A blockchain or internal market is not necessary to implement the first governance mechanism. |
| C11 | Partial: launch parameters were unresolved. | Require budget, feed, horizon, numeric charter and isolation manifest before paid execution; none has been approved here. |

### IV. Timing — the easily missed constitutional layer

Sources: [Time and Phase][time], [Clocking the Factory][clocking].

| ID | v0.1 finding | Required correction or evidence |
|---|---|---|
| T01 | Break: fixed governance intervals lacked settling evidence. | Register action-to-reward closure and distinguish it from dispatch rate; estimate the slowest relevant response before regular amendments. |
| T02 | Break: queue lacked upward temporal aggregation. | Release distributions with counts, age, missingness and uncertainty at the required relation to child loops. Do not delay ordinary leaf settlement unnecessarily. |
| T03 | Missing: jitter and common forcing were unmodeled. | Jitter upward windows while respecting minimum separation; test shared provider latency or behavioral changes. Jitter is not immunity. |
| T04 | Partial: emergency and ordinary governance timing were conflated. | Apply authorized downstream commands promptly; prevent routine amendments from chasing unfinished transients. |
| T05 | Break risk: protecting against stale feedback could discard all late learning. | Settle every decision to its owner; transfer only compatible state to successors. Keep a viable recipient through declared feedback horizons. |
| T06 | Missing: lifespan of exploration was not tied to reward delay. | Provide patience through the first eligible settlement; if the horizon is unaffordable, record the niche as unsupported. |
| T07 | Missing: fixed subsystem boundaries would forbid productive disruption. | Permit accountable restructuring of soft subsystems. Penalize repeated harmful boundary noise and assess productive breaches after consequences; no right to rewrite the kernel. |
| T08 | Boundary: a cadence ratio could be advertised as a stability proof. | Treat 3:1–10:1 as the chapter's engineering starting range. Measure settling, gain, delays and response to disturbances in this system. |
| T09 | Missing: the world could move faster than the whole hierarchy. | Define a viability check; shorten or combine lower loops, change the niche, or report failure when no feasible governance interval exists. |
| T10 | Boundary: one colony cannot establish ecosystem stability. | Inventory shared dependencies; test correlated disturbances locally. Interfactory entanglement and planetary cascades remain outside the experiment. |

## Where copying the essay literally would also go wrong

The cited repeated-game results do not establish that a naive learner creates objectives outside the architect's conception, that a careful learner cannot, or that full information universally prevents novelty. These are substantive extensions made by the essay. We can follow its population hypothesis without describing those extensions as proven engineering laws. [Deng, Schneider and Sivan](https://arxiv.org/html/1909.13861v2)

The cited paper also rules out beating its Stackelberg benchmark in constant-sum games; having three actions is not enough. A trading-themed environment cannot simply borrow the theorem's surplus conclusion. Our reference games must exercise its actual assumptions, and our broader world must be assessed on its own evidence.

Swap regret concerns mappings from selected actions to alternative actions; it is not synonymous with context sensitivity, “thinking about the weather,” or a cautious persona. Blum–Mansour's full-information reduction uses probability-weighted losses and a stationary-distribution calculation. Its bandit construction adds requirements; a retrospective spreadsheet over only sampled subsets is not an exact substitute. [Blum and Mansour](https://www.jmlr.org/papers/volume8/blum07a/blum07a.pdf)

Several interfaces therefore require an explicit engineering interpretation:

- **Unhistoried versus unlogged.** A new action has no inherited reward trail; its current selection must still receive an address, provenance and actual sampling record. Erasing evidence would defeat delayed attribution.
- **Privacy versus learning.** A bandit implementation may need its own sampling probabilities internally. Withholding a message field does not erase mathematical knowledge already held by its sampler, nor guarantee a particular learner class.
- **Fixed consequence channel versus revisable aims.** The narrow channel supplies durable correction, while the charter decides operational interpretations and tradeoffs. The channel does not magically define every normative outcome objectively.
- **Timing discipline versus productive rupture.** Preserve enforceable reporting rules and permit reconstitution of soft subsystems. An exemption that simply lets any agent ignore timing would empty the commitment; a permanent hierarchy would suppress the restructuring hypothesis.
- **Real failure versus manufactured reward.** An agent causing a fault and predicting its own fault is not evidence of improved detection. Incident provenance and counterfactual/control exposure matter.
- **Behavioral identity versus estimator output.** Histograms are useful measurements. A threshold crossing, especially with few observations or changed inputs, does not automatically establish a durable new version.

These tensions are not fixed by renaming components. They are explicit targets for the laboratory and the subsequent experiment.

## Claude cross-check and remaining limits

The existing Claude conversation received this audit and a second source-specific challenge. Its review helped sharpen the separation between settlement and evaluation, and between simple timing enforcement and advanced estimators. I rejected its suggestions that EMA plus epsilon was enough to count as the chapter's algorithmic distinction, that every price update must await committee repair, or that sharing broker code automatically creates a hidden governor. Those claims were too strong or unsupported by the passages.

In the follow-up Claude withdrew the broad shared-broker objection and accepted bounded runtime price updates. We retained its useful request for a per-decision price revision and explicit consequence dataflow. Changing prices need not invalidate adversarial online-learning results by itself, but reward scales, comparisons and retrospective attribution must remain defined. We also added coverage controls: rewarding judges only for self-selected easy predictions would leave the consequence channel formally intact but substantively weak.

This audit checks design fidelity, not the historical accuracy of every cited vendor example or the validity of every analogy in the essay. Chapter I limits what our evidence can establish about autonomy; Chapter III limits claims about scale and interdependence. The corrected plan is a bounded research specification. It is not a certification that a faithful or viable factory has been built.

[primitive]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#i-the-primitive
[incentive]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#incentive-design-for-continuous-learning
[information]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#information-design-for-continuous-learning
[versioning]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#ii-versioning
[pathology]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#pathology-detection
[commitment]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#versioning-commitment-and-the-first-commit
[evaluations]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#iii-evaluations
[catastrophes]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#catastrophes
[adversarial]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#overfitting-and-adversarial-populations
[charter]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#iv-the-charter-and-the-loop
[selfwriting]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#self-writing
[time]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#time-and-phase
[clocking]: https://superdark.antikythera.org/chapter-ii-the-dark-stack#clocking-the-factory
