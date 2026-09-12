# Factory Lab — project proposal v0.1

10 September 2026 · Working name · Design proposal, before implementation

Build a small population of agents in a world anchored in paper trading. Give it scarce resources, access to changing external conditions, and room to choose projects, build tools and revise the criteria behind its choices. Observe whether those revisions change what it actually does.

The ambition is a serious, bounded attempt at the kind of system described in *The Superdark Factory*. The first experiment can produce evidence about adaptation, criterion revision and governance. It cannot certify that a “Class 3” factory exists.

This proposal was developed through three rounds with the existing [Claude conversation](https://claude.ai/chat/7c13eaa8-ccb4-4877-b6c0-aef552ba9a02), after reading the [abstract and Chapters I–III](https://superdark.antikythera.org/#abstract). The companion reading record distinguishes the essay’s claims from our design decisions.

## What we are trying to discover

Can a population change what it considers worth doing in response to experience, and can it carry that change into its measurements, resource allocation and actions?

A useful observation would be a population that discovers a problem with its own criterion, proposes a replacement, adopts an explicit tradeoff, and subsequently behaves differently. The evidence must survive comparisons with a version that cannot revise its charter, and with explanations such as changing prices, depleted budgets, chance or a hidden instruction from a central agent.

Profit, elaborate conversations, novel agent names, rewritten code and surprise are insufficient evidence by themselves. Choosing a less profitable action could also be risk management, a mistake or an attempt to increase longer-term profit. These alternatives belong in the analysis.

## The first useful world

Paper trading is present in the first LLM experiment. Its initial neighbors are forecasting, building or repairing local tools, investigating failures, and inactivity. These are starting opportunities. Agents can register new projects and recombine available capabilities within the same permissions.

Use two data modes, labeled throughout:

- **Historical replay** for repeatable debugging and controlled interventions. Known historical data may already occur in a model’s training; replay performance is not evidence of an unseen forecasting capability.
- **Forward observation** for forecasts and paper decisions committed before later outcomes arrive. Record when data was available, not merely the timestamp printed on it.

The paper treasury, fills and transaction costs are simulated. Model usage and elapsed time are real costs. Tool bounties and internal credits, if used, remain explicitly synthetic. Tool reuse is an activity measure; it is not automatically economic value.

The initial fill model must specify timing, fees, slippage, position accounting and unavailable data. An order cannot be retrospectively filled using a price or bar that was already observable when it was submitted. Fill assumptions remain visible and subject to sensitivity analysis.

No live account or order execution is part of this version. The earlier $10/week allowance is an unadopted suggestion from Claude. Venue, funding and any later funded experiment remain separate decisions.

## Three separate surfaces

| Surface | Purpose | Authority |
|---|---|---|
| Kernel | Resource limits, capabilities, isolation, identity, settlement integrity and governance rights | Fixed for an experimental run; a material change starts a new run identity |
| Charter | Norm interpretations, operative metrics, allowed prices, allocation rules and committee decisions | Revisable through a recorded process involving the population |
| Audit panel | Stable observations that let us compare runs and inspect consequences | Written by trusted measurement code; the population cannot rewrite historical observations |

The audit panel is not a universal reward function. Its measurements do not impose a rule that every amendment must improve every dimension. Such a rule would prevent ordinary tradeoffs and could reject all costly exploration.

The charter can make particular costs or constraints consequential through explicit, versioned allocation rules and bounded prices. Agents may maintain their own priorities. We must be able to trace how an adopted change reaches later decisions; merely updating a text file is insufficient.

Suggested starting norms for Isaac’s review are truthful commitments, care with scarce resources, useful inquiry, and the ability to revise inadequate practices. These are qualitative commitments. The population participates in interpreting them and proposing operational criteria. No fixed global “maximize profit” instruction sits above that process.

## Smallest operating loop

1. A trusted broker publishes the current charter, available capabilities, permitted observations, resources and outstanding commitments.
2. Agents propose a small menu of projects. Each has a stable versioned identity, capability requirements, cost bound, completion conditions and outcome schema. Repeating a project reuses its identity; materially changing it creates a new version.
3. The broker checks permissions and affordability, including the cost of generating the menu itself. It reserves resources atomically and samples a feasible project using an explicit probability distribution.
4. The project runs in an isolated worker. Network and model access pass through the broker. Forecasts and paper orders are sealed before their target outcomes become available.
5. Outcomes arrive asynchronously. The broker records costs and observations against the originating decision. Applicable learning updates use the reward definitions and policy generation associated with that decision.
6. At a slower governance interval, a temporary committee can consider charter amendments. The observation panel continues to record the resulting behavior.

There is no permanent chief agent choosing all projects. An initial scheduler and seed configuration make the first run possible, but neither supplies an eternal organization chart. Removing or replacing the initial arrangement is an experimental event, recorded explicitly.

The first implementation needs a broker, isolated workers, a project/capability registry, a durable event ledger, a paper environment, deterministic graders, sampled independent critics and a readable observation panel. It does not need a blockchain, an internal prediction market, unbounded evaluator recursion or a live exchange integration.

## Contracts and learning

Keep request content sufficient to execute without private conversational assumptions: input references, capability and schema versions, deadline, cost ceiling and expected outputs. The broker knows authenticated ownership and lineage even when a judge receives an anonymized artifact.

Every decision record includes the run, agent lineage, policy generation, charter version, project menu, feasible set, normalized sampling distribution, selected project, selected probability, exploration-branch flag, timestamps and resource reservation. The full distribution is restricted evidence; judges do not automatically receive another agent’s private state.

For the initial learner, use an explicit mixture:

`p(a) = (1 − ε) × softmax(u(a) / T) + ε / K`

Here `K` counts the feasible projects after filtering. `u(a)` is the agent’s local, versioned valuation. `ε` and `T` are recorded policy parameters. The minimum probability contributed by the exploration mixture is `ε / K`, not `ε` for every action. A random-number generator makes the actual selection. Verbal model confidence and token likelihood are not substituted for that probability.

For each project arm and applicable outcome component, an initial update can be:

`Q_j(a) ← Q_j(a) + α × (observed_reward_j − Q_j(a))`

Agents combine these estimates using their own recorded priorities and normalization rules. Different memory/update settings provide an initial population contrast. Unobserved components retain explicit uncertainty or a declared prior; they are not silently treated as successes or zeros. New outcome dimensions and policy revisions start a recorded generation with an explicit state-transfer rule.

This is a concrete adaptive baseline, not a claimed implementation of the essay’s no-regret/no-swap-regret populations. Arbitrarily changing project sets, reward definitions and contexts does not preserve standard regret guarantees. The actual algorithmic distinction is established in work such as [Blum and Mansour](https://www.jmlr.org/papers/v8/blum07a.html); the broader bridge to novelty remains a separate hypothesis, beyond the repeated-game results of [Deng, Schneider and Sivan](https://arxiv.org/abs/1909.13861).

Counterfactual estimates are limited to decisions with the required logged support and comparable outcome definitions. Report overlap, weight concentration and uncertainty; suppress estimates when support is inadequate. Menu generation itself is not assigned an invented propensity. The causal setting matters as well as the probability log. [Bottou et al.](https://www.jmlr.org/papers/v14/bottou13a.html)

## Feedback, cost and containment

Use distinct statuses: pending, settled, censored and timed out. Missing market data, an inapplicable score and an observed failure are different events. A timeout may have an explicit operational penalty, but it does not manufacture an unknown outcome.

Late observations stay attached to their original decisions. Retired policies retain their records; they do not silently train successors. An agent’s retirement does not erase reserved costs, simulated positions, debts or pending outcomes. Imported experience requires a recorded compatibility decision.

All paid calls require a conservative pre-dispatch cost reservation based on bounded inputs and outputs and verified provider accounting. Retries and evaluators share the same cap. Unknown prices or an absent budget prevent paid execution. Reconcile actual usage after calls; do not let a committee increase the external spending ceiling.

Also bound concurrency, worker population, tool runtime, storage and proposal volume. Initial numeric values belong in a launch manifest, not in assumptions about an already approved spend. A practical starting proposal is four producer slots, two concurrent workers, at most four candidates per menu and one charter candidate under active evaluation at a time. These are tuning defaults to review, not theoretical requirements.

The broker holds credentials outside worker access. Generated code runs behind an actual isolation boundary with controlled mounts and broker-mediated communication. Ordinary same-user subprocesses and an instruction saying “do not edit the kernel” are insufficient. Verify the chosen boundary before exposing generated code to real credentials or sensitive host files.

## A charter amendment is a transaction

1. Any eligible agent submits a concrete proposed change, the experience motivating it, affected norms, expected costs and benefits, and a prediction about later behavior.
2. Freeze the proposal and its evaluation horizon. Separate development evidence, adoption-validation evidence and a fresh prospective audit period. Data used for repeated proposal selection is not called untouched holdout data.
3. A three-member rotating committee reviews the proposal. Committee selection is broker-controlled, uses distinct eligible lineages, and prevents self-spawning from multiplying votes. Record conflicts and dissent; disclose shared model dependencies.
4. Deterministic validation checks schema, permissions, resource limits, measurement integrity and effective timing. It does not require improvement on every audit measure. The committee weighs the tradeoff and adopts with two of three votes under its predeclared rights.
5. The broker activates the amendment at a future boundary. Old records retain their original definitions. Any changed allocation rule, metric or price has an explicit operational effect and version. Proposals outside committee authority stay proposals.
6. After the declared horizon, compare behavior with the prediction and the relevant control. Record contradictory evidence and alternative explanations. A later revision changes future operation; it cannot relabel past results.

For example, agents may discover that frequent low-confidence paper trades consume attention and generate avoidable turnover. They could propose spending more of the next interval on a calibration tool and accepting less near-term simulated profit. The proposal names the cost, expected decision changes and future observations that would support or undermine it. It may pass despite worsening short-term P&L or increasing compute use, provided it remains within hard limits and the committee accepts the tradeoff.

That event is not proof of new ultimate values. It is inspectable evidence of a proposed criterion change, adopted authority and a subsequent behavioral response. A rejected proposal or unchanged charter is also a valid result.

## What the observation panel shows

- **Activity and resources:** project allocation, spend, remaining resources, idle time and pending liabilities.
- **Learning and retention:** sampled exploratory branches, new project trials, repeated useful discoveries, survival time and population concentration.
- **Consequences:** sealed forecast quality and coverage; paper returns, exposure, drawdown and costs; independently measured tool benefit; investigation recurrence per relevant exposure.
- **Governance:** proposals, votes, dissent, operative amendments and whether predicted behavioral changes followed.
- **Measurement health:** missing outcomes, delayed settlements, outcome-definition changes, scorer disagreement and separation of training from prospective evaluation.

Behavioral changes can be summarized with allocation distributions, change magnitudes and supporting sample counts. A code commit, an agent’s self-description and a behavioral change remain separate records.

Treat the essay’s pathology names as diagnostic hypotheses. Stable failure needs persistent poor outcomes as well as stable behavior. Thrash needs costly churn with little retained benefit. Learning death needs evidence that new useful work has stopped reaching practice. Metric gaming needs divergence between rewarded measurements and independently observed consequences. No single threshold establishes one of these diagnoses.

Start with deterministic scoring and use independent model critics selectively. Grade probabilistic judge forecasts against later observations where that is meaningful. Some normative disagreements will not have a numerical ground truth; preserve them rather than fabricating one. Spectral analysis, adaptive PID penalties and automatic temporal-band control can wait for sufficient data and a demonstrated need.

## Build in three milestones

**1. A trustworthy laboratory.** Implement the broker, registry, sampler, event ledger, paper accounting and worker boundary with scripted agents. Demonstrate correct resource reservation, distributions after feasibility filtering, idempotent settlement, late feedback after retirement, future-data rejection and blocked capability escapes. Exercise accepted and rejected amendments with fixtures. If counterfactual estimators are included, check them against a synthetic process with known ground truth and demonstrate refusal under inadequate support.

**2. One small, bounded population.** Add inexpensive model routing and run paper trading, forecasting and tool work through the same loop. Record both adaptation and failures. Exercise the full amendment path without requiring the real population to accept one. Use deterministic fixtures to check detection of planted failures. A short run validates operation and estimates cost; it does not establish statistical learning or long-term stability.

**3. A comparative experiment.** Compare charter revision enabled/disabled × protected exploration enabled/disabled. “Disabled” refers to the explicit protected exploration mechanism, not a claim that all other stochasticity disappears. Hold data feeds, total compute opportunity, candidate-generation opportunity and evaluation budgets comparable, and report actual resource use.

Predeclare hypotheses and analysis windows. Repeat across seeds and time blocks as the budget allows; account for shared prices and serial dependence rather than treating every tick as independent. Use shuffled feedback and planted failures as diagnostic controls. Report effect sizes, uncertainty, absent events and negative findings. No successful amendment or novelty event is required for the experiment to be complete.

## Decisions left for launch

The conceptual design is ready to discuss and the deterministic laboratory is the first implementation target. Before a paid LLM run, choose the total compute budget, verified model/provider, data feed, observation horizon and concrete isolation setup. Claude recovered a preference for inexpensive models; specific model names and availability have not been verified.

The hardest open question is identifying a meaningful change of criteria separately from ordinary adaptation under a sufficiently broad fixed objective. This proposal makes that question observable in narrower pieces. It does not resolve it by declaring all bounded systems Class 2, or by declaring any self-revision Class 3.

Nothing has been implemented, connected to a trading account, or run as a funded experiment as part of this planning session.
