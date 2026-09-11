# Factory Lab — project proposal v0.2

10 September 2026 · Revised after the Chapter II fidelity audit · Before implementation

Build a bounded experimental world in which a population can compose its working arrangements, learn through different update mechanisms, evaluate its evaluators, and help revise the operational criteria governing its work. Paper trading is one starting activity. An owned running service supplies another, with observable consequences for the population's actual work.

**v0.1 is superseded as the factory design.** Its EMA/softmax population remains a useful comparison baseline. This revision restores omitted mechanisms and names unresolved questions. It specifies an experiment inspired by the dark stack; it does not certify a Class 3 factory. Nothing has been implemented or funded.

The companion `chapter2-audit.md` contains the detailed findings and passage links. The first implementation target remains a deterministic laboratory, now exercising the actual proposed interfaces and learning mechanisms.

## 1. What would count as evidence

Can the population encounter a problem with its operative criteria, propose a different interpretation or tradeoff, enact it through legitimate authority, and retain a useful behavioral change?

A candidate event must connect experience → proposed criterion or price → enacted change → altered learning/allocation → subsequent observations. Generating subgoals, selecting another trading strategy, rewriting a prompt or changing an unused charter is insufficient.

Test learning, retention, evaluator correction, self-assembly, timing and charter revision separately before interpreting their combined behavior. A broad fixed-objective account may still explain an apparent criterion change. The essay's bridge from repeated-game value to novelty beyond readable space remains a hypothesis. [Incentive Design](https://superdark.antikythera.org/chapter-ii-the-dark-stack#incentive-design-for-continuous-learning)

## 2. Four authorities and their connections

| Surface | Authority | Change rule |
|---|---|---|
| Constitutional kernel | Capabilities, isolation, resource ceilings, identity, decision addressing, access rights, novelty rights and relational timing enforcement | Fixed per world identity. A material intervention terminates that identity. |
| Consequence settlement | Fixed admissibility/provenance rules, sealed per-prediction event contracts, immutable observations and the protected consequence-to-evaluator feedback channel | New event contracts may use the existing rules. Changing the trusted scoring mechanism or feedback rights requires a declared world change. |
| Operative charter | Norm interpretations, conditional metrics, soft penalties, bounded update rules, allocation policies and delegated governance decisions | Revisable through the committed procedure; old definitions remain attached to old decisions. |
| Research audit | Comparisons, independent diagnostics, controls, uncertainty and interpretation | Analysis methods can improve under explicit versions. The dashboard is not a universal acceptance function. |

A thin broker may implement several services. The relevant separation is authority and information flow, not executable count. It validates and samples declared policies; it does not permanently own every local utility, project choice or assembly.

The consequence channel must affect operation. Reserve a fixed share of evaluator-selection capacity for selection based on settled consequence performance, separate from charter-conformity performance and protected evaluator exploration. Charter points cannot improve a consequence record or eliminate this allocation channel. Its numerical share and scoring rule belong in the initial launch manifest. This is our concrete interpretation of the nonfungible signal, not a demonstrated cure for collusion. [Adversarial Populations](https://superdark.antikythera.org/chapter-ii-the-dark-stack#overfitting-and-adversarial-populations)

Keep external spending, internal credits and learning rewards in distinct units. No reward or committee vote can replenish the real budget.

## 3. The initial world

Starting opportunities are paper trading, sealed forecasting, building or repairing local tools, investigating failures, evaluating work and inactivity. These are seed opportunities, not a permanent taxonomy. Agents may register permitted capabilities and compose unfamiliar projects.

**Market observations.** Use historical replay for debugging and forward observation for commitments made before outcomes. Log availability, retrieval and event times separately. Historical replay may overlap model training data and does not establish unseen prediction skill. Define simulated paper orders, fills, fees and slippage before a run; an order cannot receive a retrospectively favorable fill from an already observable price.

**Owned operational service.** The population maintains a local service used by the experiment itself, such as forecast ingestion, artifact validation or reporting. Instrumentation outside worker write access records requests, errors, latency, rejected work and repair costs. These failures can consume the population's real reserved time or compute and alter its work.

Learning antagonists receive limited capabilities for reversible faults in that service: delayed responses, malformed permitted inputs, bounded resource contention or candidate code defects inside isolation. They cannot target third-party services, access host secrets or bypass the kernel. Ordinary traffic, no-fault intervals and unannounced bounded probes supply comparison exposure.

The service is an operational experiment, not an external customer business. Its incidents and sealed forecasts only partly instantiate the essay's stronger production-catastrophe argument. Paper losses do not demonstrate market reflexivity; paid tokens alone do not establish evaluator correctness. No live trading account or real-money order is included.

## 4. Contracts and information boundaries

Register tools, prompts, model calls, memory policies, learners, evaluators, projects and routing rules as capabilities or assemblies. Contracts specify inputs/outputs, permissions, side effects, resource bounds, timeout/cancellation and compatibility. A fresh consumer must execute from the contract without relying on another agent's conversational habits.

A request carries a persistent decision handle, semantic description, input references, capability versions, outcome schema, deadline and cost ceiling. The choosing component seals its actual sampling record at decision time. The reward route later pairs the result with that decision. Assemblies can generate further requests through the same protocol.

| Recipient | Receives | Private by default |
|---|---|---|
| Any primitive | Schemas, capability descriptions, current charter | All-agent memory and a compulsory global topology |
| Executor | Complete author-neutral request and necessary artifacts | Requester's deliberation, scores and full action distribution |
| Evaluator | Input, output, criteria and necessary provenance; selected propensity when its role needs it | Producer identity in the judgment payload, private state and future settlement |
| Local learner | Its settled score channel, own probabilities and algorithm state | Other learners' histories and invented unchosen outcomes |
| Retentive composition | Authorized information needed by its local counterfactual algorithm | Universal access to population state |
| Governance | Buffered distributions, amendments, price proposals and committee evidence | A live trace through which to direct every worker |
| Restricted ledger/audit | Authenticated lineage, complete decisions and immutable events | Authority to rewrite evidence or feed all private state back to agents |

A learning return is thin: decision handle, scalar score for one named channel, definition version, status and sampling reference. Diagnostic prose travels separately. Pending, settled, censored, timed out and inapplicable remain distinct. A timeout may earn an operational penalty without manufacturing an unknown outcome.

Seed information asymmetry and different local experience. Withholding a field does not erase a sampler's knowledge of its own probability or guarantee a learner class. Information permissions and actual update algorithms both need specification. [Information Design](https://superdark.antikythera.org/chapter-ii-the-dark-stack#information-design-for-continuous-learning)

## 5. Actual learning mechanisms

Validate the distinction in finite local tasks first. External regret compares accumulated reward with the best fixed action; swap regret compares it with the best fixed mapping from each selected action to an alternative. A live action log alone cannot compute their true values without the required outcome information.

For the reference retainer, implement the Blum–Mansour reduction: action-associated learners propose rows of `Q`; solve `p = pQ`; in the full-information case feed learner `i` the loss vector weighted by `p_i`. For bandit feedback, use its actual partial-information construction and required base-learner conditions. Generic IPS plus an arbitrary update is insufficient. [Blum and Mansour](https://www.jmlr.org/papers/volume8/blum07a/blum07a.pdf)

For the reference frontier, use multiplicative weights in the fully observed laboratory and an appropriately parameterized EXP3 reference in the bandit laboratory. Preserve the original update and exploration schedule. Constant-temperature EMA/softmax remains labeled as the heuristic baseline. [Auer et al.](https://cseweb.ucsd.edu/~yfreund/papers/bandits.pdf), [Deng et al.](https://arxiv.org/html/1909.13861v2)

Proposed seed: three productive learner instances and six evaluators, with four exploratory and two retentive evaluators. At least two evaluators initially judge other evaluators. A minority antagonist role crosses production and evaluation. These are initial instances, not nine concurrent paid calls, a permanent organization chart or a theoretical minimum. Invocation and compute shares matter more than headcounts.

Every learner declares its action identities, feasible set, bounded score/normalization, feedback model, allowed delay, estimator, exploration schedule, retained state and comparator. Validate fixed local menus of at least three actions first; three actions enable the cited distinction but do not guarantee novelty.

Then exercise delay, missingness and menu changes separately. New global projects may create new local problems without rewriting every existing learner. Changed action or reward meaning opens a new comparator epoch; compatible state can transfer under a recorded rule, but no theorem spans that change automatically.

The simplest operational seed allows one outstanding decision per learner until its declared score bundle resolves; other learners continue asynchronously. This preserves clear local update order at a throughput cost. If it cannot keep pace with the niche, validate a delayed-feedback variant before increasing local concurrency. Censoring, changed outcomes and endogenous resource feasibility limit the ordinary guarantees; expose those limits.

A finite benchmark verifies a substrate, not open-ended objective formation. An interacting population does not inherit a theorem merely because some components use named algorithms.

## 6. Protected novelty and retention

The kernel reserves bounded compute and write-capable trials for proposals without performance history. Write rights apply only to permitted artifact, capability or isolated service surfaces. Incumbents cannot consume the entire reserve, and charter amendments cannot abolish it.

Each admitted trial gets a stable identity, reserved resources and an initial feedback horizon. Preserve a viable recipient through that horizon. This is not indefinite funding or guaranteed success. If the budget cannot afford the horizon, report the niche as unsupported instead of repeatedly killing explorers before feedback.

“No prior history” means no inherited performance claim. The current decision still receives an address, provenance and actual selection record. Detect identical and materially equivalent resubmissions where possible; disclose remaining uncertainty in novelty classification.

Provide a path from surviving output to evaluation, registration and adoption by retentive learners or other assemblies. Measure the whole path. Random calls and new files are insufficient if discoveries never enter ordinary use. [Commitment](https://superdark.antikythera.org/chapter-ii-the-dark-stack#versioning-commitment-and-the-first-commit)

## 7. Learning evaluation and consequence settlement

Evaluators choose or compose judging methods, issue scored verdicts, commit predictions about downstream events and retain learning state. Other evaluators assess their judgments against the current charter. The independent consequence route settles eligible predictions against later observations.

A narrow initial example: an evaluator records probability `q` of a defined service failure in a specified window under a named action; trusted instrumentation later records binary outcome `y`; settlement returns a bounded proper score such as `1 − (q − y)²`. Fix the event, action, horizon, denominator and observation source before prediction. Score non-events too, compare against prevalence baselines and report coverage. This is an implementation choice, not a universal measure of good judgment.

Use a precommitted assignment/sampling rule for eligible exposures so judges cannot accumulate standing by selecting only easy claims. New event contracts can be registered within the fixed provenance and scoring rules; registration cannot alter already sealed predictions. A high score on trivial or selectively covered events is not evidence of broad evaluator competence. The trusted settlement path reads the sealed contract and observation, not the current charter's mutable scoring rubric or an admission verdict. Semantic interpretations produced by learning judges remain judgments to evaluate, not unchallengeable event facts.

If a warning changes the action and prevents failure, the counterfactual event remains unobserved. Do not treat successful prevention as a false alarm or automatically award causal credit. Use action-conditional targets, intervention records and separately bounded controlled comparisons where available.

Settlement updates the originating evaluator and its protected consequence standing. Incorrect verdicts must affect subsequent selection or resources even when they scored well against the charter. Past settlement feedback is necessary for learning; future outcomes before commitment are prohibited.

Evaluators can be replaced, composed and evaluated through the same registry and contracts. Seed a producer → evaluator → meta-evaluator path; additional depth or different topology is available within the budget. Report the active evaluation boundary. Finite recursion does not create an infallible top judge, and external events do not resolve every normative disagreement.

Record incident authorship and shared lineage privately. An antagonist cannot create a fault and collect independent prediction credit through its own evaluator clone. Bound rewards for repeated exploit copies. Most evaluators do ordinary judging. [Evaluations](https://superdark.antikythera.org/chapter-ii-the-dark-stack#iii-evaluations)

## 8. Timing in the first commitment

Each feedback relationship registers decisions, expected horizon, actual closure, dependent loops and response/settling estimates. Roles do not determine permanent clocks.

Leaf outcomes settle to their rightful learner when available. Reports moving upward to a loop governing lower loops are buffered into distributions with counts, delays, missingness and uncertainty. Enforce declared minimum separation relative to the governed response time, initially using the chapter's 3:1 lower-end recipe for laboratory calibration. Jitter upward windows without violating the minimum.

Ordinary charter amendments wait until the relevant response to the prior amendment can be assessed, considering the slowest consequential loop. Authorized downstream commands, cancellation and hard stops are prompt. Repeated or noisy interventions remain visible and costly under the charter.

A ratio is not a stability theorem. Estimate settling through bounded perturbations, delay distributions and uncertainty. Before estimates exist, use conservative seed horizons and label timing uncalibrated. A market horizon may make a short run incapable of multiple meaningful governance cycles.

If the world changes faster than any feasible hierarchy, shorten or combine lower loops, change the niche, or report timing failure. Updating governance more frequently on stale data does not solve it.

Soft subsystems may dissolve and reconstitute. A departure from their ordinary timing arrangement gets bounded resources, a predicted benefit and consequence review. Penalize repeated harmful disruption and retain useful restructuring. This route cannot erase queue rights, budget limits or identity rules. It is an explicit experimental interpretation of productive rupture, not a settled construction.

Track shared infrastructure and provider changes. Jitter does not prevent every correlated failure. [Time and Phase](https://superdark.antikythera.org/chapter-ii-the-dark-stack#time-and-phase), [Clocking](https://superdark.antikythera.org/chapter-ii-the-dark-stack#clocking-the-factory)

## 9. Behavioral identity, memory and repair

Keep four identifiers distinct: world identity, charter edition, learner generation and inferred behavioral regime. Observe distributions of outputs, outcomes and evaluator judgments conditional on recorded inputs. A charter change declares a boundary; an external change can create another without a code commit.

Start with explicit windows, conditional histograms, transition counts and uncertainty. Code hashes and prompts are forensic metadata. A later transfer-operator estimator must declare cells, lag, selected slow modes and data sufficiency and be checked on known processes. An action histogram or convenient scalar spectral gap is not the factory's identity. Version evaluator behavior too. [Versioning](https://superdark.antikythera.org/chapter-ii-the-dark-stack#ii-versioning)

Late outcomes settle to their original decision, probability and score definition. Retirement does not cancel liabilities, paper positions or feedback. A compatible persistent learner receives the update. Successor transfer requires a map of action meaning, reward meaning, normalization and state; a charter amendment alone does not wipe memory. Incompatible outcomes remain historical evidence rather than silent training for a different problem.

Soft rewards use `r_effective = r_local − Σ λ_j v_j`, with declared units and bounded prices. An initial controller can use `λ_next = clip(λ + η × violation_error, 0, λ_max)`, with rate limits, release behavior and timing specified. This is our prototype rule. If PID is used later, specify filtering, anti-windup and saturation. Neither is presumed stable.

Stamp each numerical update with a price-revision identifier and retain the coefficients effective at every decision. A price update is not automatically a new world, charter edition or learner reset. Adversarial online algorithms can accommodate varying rewards within their assumptions; the issue is preserving bounded scales, action comparability and valid counterfactual attribution. Specify any normalization/clipping before learning, retain unclipped costs for audit, and keep protected exploration rights and consequence settlement outside the price controller's authority.

| Diagnosis | Operative first response | Evidence to seek |
|---|---|---|
| Stable failure | Raise the bounded price of persistent unmet criteria; permit alternate composition | Escape with better consequences; record overshoot and cost escalation |
| Overfitting | Increase measurement variety/resolution and adversarial probes; use consequence discrepancies in metric proposals | Better downstream judgment, beyond improved amended scores |
| Learning death | Enforce trials, patience and access to retention; inspect blocked transitions | Trials survive feedback and can reach use; assess usefulness separately |
| Thrash | Correct stale attribution, damp repricing and adjust delay/refactoring relationships within authority | More retained benefit and fewer oscillatory replacements at comparable exposure |

Use multiscale variance, persistence/autocorrelation and judge disagreement when supported by data. Measure false alarms and misses. The committee can revise controller rules; the approved controller operates between meetings. Saturation or failed repair is a limit, not authority to expand external spending.

## 10. Operative charter revision

Proposed initial read-only norms are truthful commitments, care with scarce resources, useful inquiry and the capacity to revise inadequate practices. These await the eventual launch manifest. Operational interpretations are coauthored with the population; no global maximize-P&L instruction sits above them.

Every metric card specifies norm reference, observation schema, conditioning variables, units, window, denominator, minimum support, uncertainty rule, missingness, acceptable region, violation measure and reward/allocation effect. Name amendment authority and activation timing. Holdouts are versioned evidence, not an eternal definition of correctness.

Example: an investigation metric might concern recurrence per relevant service exposure and repair cost over a declared horizon. The population could discover that the existing metric rewards superficial closure and propose a durable-repair criterion. Actual numerical regions and sample requirements must be specified before operation; this proposal does not invent calibrated thresholds.

1. Any eligible primitive proposes a metric, bounded price, controller rule, allocation policy or permitted interpretation, with evidence and a predicted tradeoff.
2. Freeze the candidate and horizon. Separate development, adoption-validation and prospective evidence. Label reused data.
3. Seat a rotating committee by stratified sortition. Proposed seed: five distinct eligible lineages jointly covering producers, evaluators, antagonists and both learning types, using round-specific aliases. If coverage is unavailable, report it and use only fallback governance explicitly permitted by the launch constitution.
4. Trusted validation checks authority, executable definitions, resources, settlement integrity and timing. Proposed voting rule: three of five. Acceptance need not improve every research-audit dimension.
5. Activate at a future eligible boundary. Preserve old editions and decision mappings; verify an operational effect on measurement, rewards, prices or allocation.
6. After the horizon, assess predicted behavior and alternatives. Contradictory evidence may motivate a new amendment but cannot rewrite old facts.

Factory members propose prices from experience; governance need not possess a complete marginal-cost model. Delegation rights are constitutional. Changing them starts a new world. Blockchain and futarchy are optional mechanisms; votes are not weighted by token holdings. [Self-Writing](https://superdark.antikythera.org/chapter-ii-the-dark-stack#self-writing)

## 11. Build and evidence gates

**A. Deterministic substrate.** Build the broker, real isolation boundary, registry, sampler, ledger, delayed queue, paper accounting, consequence settlement and executable charter cards with scripted actors. Verify reservations, idempotence, future-data rejection, privacy, delayed survival, and accepted/rejected amendments. These establish integrity, not emergence.

Implement reference learners and check known finite games that distinguish external and swap regret. Verify the reduction's numerical invariant and bandit estimators against known outcomes. Exercise delay and menu changes separately. An easy stationary task that a heuristic also passes is insufficient evidence.

**B. Consequential runtime.** Add a serially scheduled model population, learning evaluators, meta-evaluation, protected trials, owned service and bounded antagonists. Demonstrate verdict → immutable event → evaluator update → consequential allocation. Demonstrate assembly changes and an operative amendment path without forcing the population to accept an amendment.

Exercise planted stable failure, score gaming, blocked retention, stale-feedback thrash and shared-infrastructure disturbance. Measure repair as well as detection, including costs and false alarms. Claims remain limited to the tested setting.

**C. Comparative experiment.** Begin with charter revision enabled/disabled × protected novelty enabled/disabled under matched opportunity and evaluation resources. Protected novelty is distinct from ordinary algorithmic exploration. Once the full loop works, add targeted matched ablations of evaluator learning, consequence influence and temporal buffering to distinguish their effects.

Predeclare hypotheses and analysis windows. Repeat seeds and time blocks as affordable; account for common prices and serial dependence. Use shuffled feedback and no-fault controls. Report actual resource use, effect sizes, uncertainty, absent events and negative findings. A successful novelty event is not required for a properly executed experiment to finish.

## 12. Before any launch

The next implementation target is concrete, but the open-world extension of the theory and calibrated operating parameters are not settled.

The launch manifest must specify total approved spend, verified provider/prices, feed, observation horizon, isolation technology, metric thresholds, score normalizations, novelty/evaluator resource shares, event predicates, governance eligibility and timing. Unknown prices or no paid budget prevent paid dispatch. Reserve all calls, retries, evaluations and proposal costs before execution; also bound concurrency, runtime, storage and population.

The earlier $10/week suggestion was never adopted. Specific model/provider availability has not been verified here. This planning document authorizes no live trading, funded run or production deployment.

Open risks are dynamic-menu/delay fidelity, novelty and consequence-credit gaming, evaluator diversity under scarce resources, productive timing exceptions, and distinguishing criterion revision from sophisticated fixed-objective adaptation. They remain explicit experimental questions. The detailed audit is complete; implementation and runtime validation have not begun.
