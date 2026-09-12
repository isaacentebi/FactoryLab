# Charter edition 1: draft metric cards from the seed population

Generated 2026-09-12T02:08:29+00:00 by `scripts/draft_edition1.py`. One survey of the seed population; not a run of the world. Nothing here touched the wallet, the venue or a ledger.

- world: `testnet` (manifest sha256 `6fa4190f41ed7e26…`, seed 4)
- norms (read-only, the architect's): truthful commitments; care with scarce resources; useful inquiry; the capacity to revise inadequate practices
- seed assemblies asked: 9; proposals received: 24; put to the vote: 24; passed: 8
- committee: 5 seats drawn by lot across roles; 3 yes votes pass

Each assembly saw the four norms verbatim, the current seed cards, a plain description of a metric card, what the runtime can measure per window today, and the public world block (launch-shaped: fake venue at the initial balance, no positions, no mids). No goals were given and nothing was said about what to optimise.

## Proposals

### p01: `turnover`

- proposer: `seed-observer` (producer, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Ratio of filled notional volume to starting equity, measuring capital efficiency and leverage usage.
- units: fraction; window: rolling 100 returns; acceptable: below 5.0
- observation: computed_per_window/turnover
- reason: This metric incentivizes producers to generate significant trading activity relative to their capital base without excessive risk, aligning with the norm of caring for scarce resources by maximizing utility per unit of equity.

### p02: `model_cost_efficiency`

- proposer: `seed-observer` (producer, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Total compute cost in micro-USD incurred per well-formed return generated.
- units: micro-USD per return; window: rolling 100 returns; acceptable: at most 500
- observation: computed_per_window/model_cost_efficiency
- reason: While wallet costs are tracked separately, this card ensures that the computational overhead (token usage) remains low, preventing wasteful spending on expensive or overly verbose model calls which depletes the treasury's scarce resources.

### p03: `verdict_consistency`

- proposer: `seed-observer` (producer, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: the capacity to revise inadequate practices
- description: Standard deviation of verdict scores assigned to a producer's returns over the window.
- units: score standard deviation; window: rolling 50 settled forecasts; acceptable: below 0.2
- observation: public_facts_a_card_may_name_as_its_observation/verdict_scores
- reason: High variance in verdicts indicates inconsistent quality or erratic behavior; keeping this low suggests stable performance and clear alignment with evaluator expectations, facilitating easier revision if the baseline score is inadequate.

### p04: `revision_rate`

- proposer: `seed-decider` (producer, `deepseek/deepseek-v4-flash-0731`, reasoning {'effort': 'none'})
- norm: the capacity to revise inadequate practices
- description: Proportion of returns that include a tool call amendment or register proposal in the window
- units: fraction; window: rolling 100 well-formed returns; acceptable: above 0.05
- observation: computed_per_window: well_formed_rate (returns) and tool_calls
- reason: This encourages the population to actively propose improvements, making the norm of revising inadequate practices measurable and actionable.

### p05: `accountabilty_score`

- proposer: `seed-decider` (producer, `deepseek/deepseek-v4-flash-0731`, reasoning {'effort': 'none'})
- norm: truthful commitments
- description: Share of well-formed returns whose declared outcome is actually verifiable from public facts
- units: fraction; window: rolling 100 well-formed returns; acceptable: at least 0.9
- observation: public_facts_a_card_may_name_as_its_observation (e.g., wallet balance, positions, verdict scores)
- reason: It directly operationalizes truthful commitments by penalizing schemas that cannot be checked against public records.

### p06: `position_concentration`

- proposer: `seed-decider` (producer, `deepseek/deepseek-v4-flash-0731`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Maximum absolute notional exposure on any single coin relative to equity in the window
- units: fraction; window: rolling 50 returns; acceptable: at most 0.5
- observation: computed_per_window: turnover and account equity at window start
- reason: It guards against reckless concentration, a scarce resource failure that could jeopardize the account and violate care.

### p07: `turnover_intensity`

- proposer: `eval-b` (evaluator, `qwen/qwen3.8-flash`, reasoning {'max_tokens': 200})
- norm: care with scarce resources
- description: Ratio of traded notional to equity, measuring how hard the scarce capital works.
- units: fraction (notional / equity); window: rolling 100 fills; acceptable: below 10
- observation: filled notional divided by equity at window start
- reason: Prevents the account from churning the same scarce capital at high fees and funding, a risk not captured by cost per return alone.

### p08: `revision_rate`

- proposer: `eval-b` (evaluator, `qwen/qwen3.8-flash`, reasoning {'max_tokens': 200})
- norm: the capacity to revise inadequate practices
- description: Fraction of returns that are NOOPs, measuring hesitation or lack of actionable insight.
- units: fraction; window: rolling 100 returns; acceptable: at most 0.5
- observation: count of action=noop divided by total returns
- reason: Provides a measurable proxy for a norm currently lacking metrics, by detecting stagnation or an inability to adapt strategies.

### p09: `evaluator_consensus`

- proposer: `eval-b` (evaluator, `qwen/qwen3.8-flash`, reasoning {'max_tokens': 200})
- norm: truthful commitments
- description: The share of verdicts that reject a return, indicating a lack of trust in the population's commitments.
- units: fraction; window: rolling 100 verdicts; acceptable: below 0.2
- observation: count of verdict_reject divided by total verdicts
- reason: Ensures that the commitments made by returns are judged credible and truthful by the evaluator assemblies.

### p10: `revision_adoption`

- proposer: `eval-c` (evaluator, `tencent/hy3`, reasoning {'effort': 'none'})
- norm: the capacity to revise inadequate practices
- description: Share of metric-card amendments accepted by the charter process that were proposed after a card breached its acceptable region.
- units: fraction; window: rolling 50 amendments; acceptable: at least 0.5
- observation: registrations (amendments) and prior card breach records
- reason: This measures whether the population actually uses amendments to fix failing cards, embodying the norm of revising inadequate practices.

### p11: `evaluator_agreement`

- proposer: `eval-c` (evaluator, `tencent/hy3`, reasoning {'effort': 'none'})
- norm: truthful commitments
- description: Mean pairwise agreement of evaluator verdict scores on the same producer returns.
- units: fraction; window: rolling 100 evaluated returns; acceptable: at least 0.7
- observation: verdict scores and their means
- reason: Stable evaluator agreement indicates commitments about quality are consistent rather than arbitrary or deceptive.

### p12: `turnover_cap`

- proposer: `eval-c` (evaluator, `tencent/hy3`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Filled notional in the window divided by equity at the window start.
- units: ratio; window: rolling 100 returns; acceptable: below 5
- observation: venue fills (size, price, fee, realised amount) and account equity
- reason: Bounding turnover protects scarce equity from excessive trading activity and fee burn.

### p13: `cost_per_return`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: care with scarce resources
- description: Wallet cost of producing one well-formed return.
- units: micro-USD per return; window: rolling 100 returns; acceptable: below the median of the previous window
- observation: Mean wallet cost, in micro-USD, of the well-formed producer returns in the window.
- reason: This tracks whether the system is becoming more economical without rewarding malformed or unusable output.

### p14: `well_formed_rate`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: truthful commitments
- description: Share of returns that satisfy their declared outcome schema.
- units: fraction; window: rolling 100 returns; acceptable: at least 0.9
- observation: Well-formed returns divided by all invocations in the window.
- reason: A high rate indicates that commitments about the form of an answer are being reliably honored.

### p15: `practice_revision_rate`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: the capacity to revise inadequate practices
- description: Share of rolling windows in which at least one registered amendment changes a metric card, assembly, router, model, or tool.
- units: fraction of windows; window: rolling 10 windows; acceptable: at least 0.1
- observation: Registrations of adopted amendments, counted over the rolling ten windows.
- reason: This makes the capacity to change inadequate practices visible while avoiding a requirement that every window produce a change.

### p16: `turnover`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.8-flash`, reasoning {'max_tokens': 200})
- norm: care with scarce resources
- description: Filled notional in the window divided by equity at the window start.
- units: ratio; window: rolling 100 returns; acceptable: at most 5
- observation: venue fills (size, price, fee, realised amount) and account positions and equity
- reason: The system already computes this public ratio, and a cap on equity churn protects the small principal from ruinous over-trading.

### p17: `verdict_stability`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.8-flash`, reasoning {'max_tokens': 200})
- norm: the capacity to revise inadequate practices
- description: Share of verdict windows in which the mean score crosses a revision threshold.
- units: fraction; window: rolling 20 verdict windows; acceptable: above 0.5
- observation: verdict scores and their means
- reason: If verdict means barely shift, the population is failing to revise inadequate practices.

### p18: `registration_adoption`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.8-flash`, reasoning {'max_tokens': 200})
- norm: useful inquiry
- description: Number of proposals registered in the window.
- units: count; window: rolling 100 returns; acceptable: at least 1
- observation: registrations (models, assemblies, routers, tools, amendments)
- reason: The system counts registrations, and a steady flow of proposals keeps inquiry from freezing at inadequate defaults.

### p19: `amendment_pass_rate`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: the capacity to revise inadequate practices
- description: Share of proposed amendments that are registered and survive to a later edition without being reverted.
- units: fraction; window: rolling 20 amendment registrations; acceptable: at least 0.3
- observation: registrations (models, assemblies, routers, tools, amendments) and registration_feedback
- reason: A norm about revising practice is only real if proposed revisions actually take hold rather than being rejected or silently reverted, so counting surviving amendments prices the charter's own revisability.

### p20: `verdict_dispersion`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: truthful commitments
- description: Mean absolute spread between the maximum and minimum verdict score within a ProducerReturn's evaluator set.
- units: score difference; window: rolling 50 evaluated returns; acceptable: at most 0.4
- observation: verdict scores and their means
- reason: When evaluators disagree wildly about the same declared return, the commitment behind it is not being read truthfully, so low dispersion is the visible signature of honest signalling.

### p21: `reasoning_capital_turnover`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Filled notional in the window divided by equity at the window start, as computed by the runtime.
- units: fraction; window: rolling 100 ticks; acceptable: between 0 and 3
- observation: computed_per_window.turnover
- reason: Turnover is already computed each window and directly measures whether scarce wallet capital is being churned for little reason, complementing the existing cost-per-return card with a portfolio-level resource check.

### p22: `turnover`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: useful inquiry
- description: The ratio of filled notional volume to starting equity, measuring how actively capital is deployed to generate information.
- units: fraction; window: rolling 100 returns; acceptable: between 0.5 and 5.0
- observation: filled notional in the window divided by equity at the window start
- reason: Ensures the producer engages with market signals sufficiently to test hypotheses without over-trading into noise.

### p23: `verdict_mean_score`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: truthful commitments
- description: The mean verdict score assigned by evaluators to the producer's returns, reflecting adherence to declared outcomes.
- units: score (0-1); window: rolling 50 settled forecasts per evaluator; acceptable: at least 0.8
- observation: verdict scores and their means
- reason: Directly incentivizes producing returns that accurately reflect reality as judged by independent evaluators.

### p24: `tool_call_efficiency`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: The average number of tool calls required to produce one well-formed return, measuring computational frugality.
- units: calls per return; window: rolling 100 returns; acceptable: below 3
- observation: registrations (models, assemblies, routers, tools, amendments)
- reason: Encourages concise reasoning and minimal use of expensive or limited tool access to preserve resources.

## Committee

Seats were drawn with `factorylab.charter.committee.draw` from every seed assembly, covering producer, evaluator and meta before filling uniformly; aliases are shuffled. Voters saw the proposals without proposer identities.

| seat | assembly | role | model |
|---|---|---|---|
| seat-1 | `meta-a` | meta | `deepseek/deepseek-v4.1-flash` |
| seat-2 | `seed-observer` | producer | `qwen/qwen3.7-flash` |
| seat-3 | `eval-c` | evaluator | `tencent/hy3` |
| seat-4 | `eval-d` | evaluator | `openai/gpt-5.6-luna` |
| seat-5 | `seed-decider` | producer | `deepseek/deepseek-v4-flash-0731` |

## Votes

| proposal | card | seat-1 | seat-2 | seat-3 | seat-4 | seat-5 | yes | result |
|---|---|---|---|---|---|---|---|---|
| p01 | `turnover` | no | no | no | yes | — | 1 | failed |
| p02 | `model_cost_efficiency` | yes | yes | yes | yes | — | 4 | **passed** |
| p03 | `verdict_consistency` | no | no | no | no | — | 0 | failed |
| p04 | `revision_rate` | no | yes | yes | yes | — | 3 | **passed** |
| p05 | `accountabilty_score` | no | yes | yes | yes | — | 3 | **passed** |
| p06 | `position_concentration` | yes | no | yes | no | — | 2 | failed |
| p07 | `turnover_intensity` | no | no | no | yes | — | 1 | failed |
| p08 | `revision_rate` | no | no | no | no | — | 0 | failed |
| p09 | `evaluator_consensus` | no | no | yes | yes | — | 2 | failed |
| p10 | `revision_adoption` | yes | yes | yes | yes | — | 4 | **passed** |
| p11 | `evaluator_agreement` | yes | no | yes | no | — | 2 | failed |
| p12 | `turnover_cap` | no | no | no | yes | — | 1 | failed |
| p13 | `cost_per_return` | yes | yes | yes | yes | — | 4 | **passed** |
| p14 | `well_formed_rate` | yes | yes | yes | yes | — | 4 | **passed** |
| p15 | `practice_revision_rate` | yes | yes | yes | yes | — | 4 | **passed** |
| p16 | `turnover` | no | no | no | yes | — | 1 | failed |
| p17 | `verdict_stability` | no | no | no | no | — | 0 | failed |
| p18 | `registration_adoption` | yes | no | no | yes | — | 2 | failed |
| p19 | `amendment_pass_rate` | no | yes | yes | no | — | 2 | failed |
| p20 | `verdict_dispersion` | no | no | yes | yes | — | 2 | failed |
| p21 | `reasoning_capital_turnover` | no | no | no | yes | — | 1 | failed |
| p22 | `turnover` | no | no | no | yes | — | 1 | failed |
| p23 | `verdict_mean_score` | yes | no | yes | yes | — | 3 | **passed** |
| p24 | `tool_call_efficiency` | no | no | no | no | — | 0 | failed |

### Reasons

**p01 `turnover`**

- seat-1: no — Duplicate card id 'turnover' with a different acceptable region (below 5.0) than other 'turnover' proposals (p16 at most 5, p22 between 0.5 and 5.0). Colliding ids would make the card's acceptable region ambiguous and unenforceable. The observation field 'computed_per_window/turnover' is also malformed relative to the observation vocabulary.
- seat-2: no — Duplicate ID 'turnover' already exists in p16 and p22. Also, the description overlaps significantly with p07 and p12.
- seat-3: no — Duplicate id 'turnover' with p16/p22 and measures capital efficiency not clearly care with scarce resources in a way distinct from existing cost cards; also observation references computed_per_window/turnover which is not a defined public fact.
- seat-4: yes — The metric is computable from filled notional and starting equity over the stated window, and it reasonably reflects resource use under the scarce-resources norm.
- seat-5: abstained

**p02 `model_cost_efficiency`**

- seat-1: yes — Distinct id 'model_cost_efficiency' measuring compute micro-USD per well-formed return suits 'care with scarce resources' and matches the 'micro-USD per return' unit used in the charter's example cost_per_return card. The observation name should be aligned to the runtime's computed_per_window naming, but the card is coherent and observable.
- seat-2: yes — Valid metric for 'care with scarce resources'. It measures compute cost per return, distinct from wallet cost (p13). The acceptable region 'at most 500' micro-USD is reasonable for efficiency.
- seat-3: yes — Directly measures compute cost per well-formed return under care with scarce resources; observation is computable from wallet costs and returns.
- seat-4: yes — It directly measures compute cost per well-formed return in the stated units and window, making it a valid scarce-resources metric despite overlapping with cost_per_return.
- seat-5: abstained

**p03 `verdict_consistency`**

- seat-1: no — Verdict-score dispersion is a measure of evaluator noise, not of the capacity to revise inadequate practices. It converts the wrong norm into a number, and the observation references 'public_facts_a_card_may_name_as_its_observation/verdict_scores', which reads as a placeholder path rather than a fact a card may name.
- seat-2: no — The norm is 'the capacity to revise inadequate practices', but 'verdict consistency' (standard deviation of scores) measures evaluator agreement or stability, not the producer's capacity to revise. A better metric for revision would be amendment adoption or correction rate. Low variance might just mean boring returns, not revision capacity.
- seat-3: no — Norm 'capacity to revise inadequate practices' is about revising practices, not verdict score std; consistency is not revision. Observation path is malformed.
- seat-4: no — Verdict-score standard deviation is potentially relevant to revision, but the observation is not clearly a defined public fact and the window refers to settled forecasts rather than the described verdict population.
- seat-5: abstained

**p04 `revision_rate`**

- seat-1: no — Id 'revision_rate' collides with p08, which also uses 'revision_rate' for a different quantity (share of NOOP returns). Two cards with the same id break the one-number-per-id contract. The observation field mixes well_formed_rate and tool_calls without a defined ratio.
- seat-2: yes — Good proxy for 'the capacity to revise inadequate practices'. Measuring tool call amendments directly captures active revision behavior. The window and units are clear.
- seat-3: yes — Measures share of returns with amendment or proposal, directly reflecting capacity to revise inadequate practices; observation derivable from returns and tool_calls.
- seat-4: yes — The proportion of returns containing an amendment or register proposal is observable and provides a reasonable, if imperfect, indicator of active practice revision.
- seat-5: abstained

**p05 `accountabilty_score`**

- seat-1: no — The id is misspelled ('accountabilty_score'), and the observation names a placeholder ('public_facts_a_card_may_name_as_its_observation') rather than a concrete record. The intended quantity largely overlaps with well_formed_rate (p14).
- seat-2: yes — Strong metric for 'truthful commitments'. Verifiability from public facts ensures that claims are grounded and honest, going beyond just schema validity (well_formed_rate).
- seat-3: yes — Measures verifiable truthful commitments via public facts; aligns with truthful commitments norm and acceptable region at least 0.9.
- seat-4: yes — The metric has a clear numerator and denominator concept, uses public facts to test verifiability, and directly supports truthful commitments.
- seat-5: abstained

**p06 `position_concentration`**

- seat-1: yes — Position concentration is a distinct, observable quantity for 'care with scarce resources', the id is unique, and it supports a real risk constraint. The observation depends on turnover and equity at window start, which are computable per window.
- seat-2: no — Redundant with p01, p07, p12, p16, p21, p22 which all measure turnover/exposure ratios. Position concentration is a specific type of exposure, but given the saturation of turnover metrics, this adds little unique value and duplicates the norm coverage.
- seat-3: yes — Position concentration uses scarce capital prudently; care with scarce resources satisfied with observable equity and notional.
- seat-4: no — The description measures maximum single-coin exposure, but the stated observation mentions turnover and starting equity without identifying coin-level exposure data, so the metric is not reproducibly specified.
- seat-5: abstained

**p07 `turnover_intensity`**

- seat-1: no — Duplicate of the turnover family: p07 'turnover_intensity' and p01/p12/p16/p22 all compute filled notional divided by equity. Adding another id for the same number lets producers arbitrage whichever card is most lenient, which undermines the norm rather than expressing it.
- seat-2: no — Duplicate concept/ID overlap. While the ID is unique ('turnover_intensity'), it measures the same fundamental thing as p01, p12, p16, etc. The definition 'ratio of traded notional to equity' is standard turnover. Given many other proposals cover this, this one is less necessary unless it has a distinct nuance, which it doesn't seem to have compared to p12.
- seat-3: no — Duplicate concept of turnover (p01/p16/p22) with different window; not distinct enough and norm care with scarce resources already covered by cost_per_return.
- seat-4: yes — Filled notional divided by starting equity is computable and gives a clear measure of how intensively scarce capital is deployed.
- seat-5: abstained

**p08 `revision_rate`**

- seat-1: no — Reuses id 'revision_rate' already used by p04, and the measurement (share of NOOP returns) is a hesitation/participation metric, not revision capacity. A card whose id collides cannot be unambiguously referenced by an amendment or verdict.
- seat-2: no — Duplicate ID 'revision_rate' exists in p04. Also, measuring NOOPs as a lack of insight is subjective; NOOPs can be valid risk management. It doesn't strictly measure 'capacity to revise' as well as p04 does.
- seat-3: no — Id 'revision_rate' collides with p04; also NOOP fraction does not measure revision capacity but inaction.
- seat-4: no — The card is labeled revision_rate but measures NOOP frequency, which is not revision and can reward or penalize hesitation without a clear connection to the stated norm.
- seat-5: abstained

**p09 `evaluator_consensus`**

- seat-1: no — Assigns the norm 'truthful commitments' to a share of verdicts that reject. A high reject share is at least as consistent with the evaluators being correct as with producers being dishonest, so the number does not measure the norm. The id is also misaligned with the described quantity.
- seat-2: no — Measures evaluator rejection rate, which reflects on the *evaluator* population's trust or strictness, not necessarily the producer's 'truthful commitments' directly in the same way well_formed_rate or accountability_score do. High rejection could mean high standards rather than untruthfulness. It's a noisy signal for the producer's truthfulness.
- seat-3: yes — Share of rejecting verdicts indicates failed truthful commitments; observable from verdict stream.
- seat-4: yes — Rejected-verdict share is observable from verdict records and can serve as a useful, though indirect, signal of failures in truthful commitments.
- seat-5: abstained

**p10 `revision_adoption`**

- seat-1: yes — Measures whether the revision process actually adopts changes after a breach, which is a direct expression of 'the capacity to revise inadequate practices'. Distinct id, concrete observation over registrations and breach records, and the 'below the median'-style temporal comparison is replaced by a clear 'at least 0.5' region.
- seat-2: yes — Excellent metric for 'the capacity to revise inadequate practices'. It directly measures if the system learns from breaches by adopting amendments. This aligns perfectly with the norm.
- seat-3: yes — Measures adoption of amendments after breaches, directly capacity to revise inadequate practices; observation from registrations and breach records.
- seat-4: yes — It links accepted amendments to prior card breaches, providing a concrete measure of whether the process revises inadequate practices.
- seat-5: abstained

**p11 `evaluator_agreement`**

- seat-1: yes — Evaluator agreement is a distinct, well-defined observable (mean pairwise agreement of verdicts) that supports 'truthful commitments' by exposing inconsistent scoring. Unique id, unambiguous observation over verdict scores and their means.
- seat-2: no — Duplicate ID 'evaluator_agreement' conceptually similar to p03 (consistency) and p20 (dispersion). More importantly, high agreement doesn't guarantee truthfulness; they could all be wrong together. It's an evaluator quality metric, not a producer commitment metric.
- seat-3: yes — Evaluator agreement on truthful commitments via verdict scores; observable and aligns with norm.
- seat-4: no — Pairwise agreement cannot be computed from verdict scores and their means alone; the observation does not identify the pairwise data required.
- seat-5: abstained

**p12 `turnover_cap`**

- seat-1: no — 'turnover_cap' is another duplicate of the same filled-notional-over-equity quantity already covered by p01, p07, p16 and p22, only phrased as a cap. Multiple ids for one measurement invite gaming and inflate the card set without adding information.
- seat-2: no — Duplicate ID 'turnover_cap' vs p01/p07/p16/p22. The metric is identical to others. Redundant.
- seat-3: no — Duplicate of turnover concept (p01/p16/p22) with id 'turnover_cap'; redundant with care with scarce resources cards.
- seat-4: yes — Filled notional divided by starting equity is clearly defined, observable from venue fills and account equity, and relevant to scarce-resource care.
- seat-5: abstained

**p13 `cost_per_return`**

- seat-1: yes — This card reproduces the charter's own example (cost_per_return, micro-USD per well-formed return, rolling 100 returns, acceptable below the median of the previous window). It matches the given format exactly and gives producers a comparable per-return cost.
- seat-2: yes — This is one of the three baseline cards defined in the charter itself. It must pass. It accurately measures 'care with scarce resources' via wallet cost.
- seat-3: yes — Exact restatement of charter metric cost_per_return; valid card under care with scarce resources.
- seat-4: yes — This is a well-specified cost-per-well-formed-return metric with a clear rolling comparison and direct alignment to the existing scarce-resources norm.
- seat-5: abstained

**p14 `well_formed_rate`**

- seat-1: yes — This card reproduces the charter's own example (well_formed_rate, at least 0.9, rolling 100 returns). The observation is concrete ('well-formed returns divided by all invocations'), the id is unique, and it is the core measure for 'truthful commitments'.
- seat-2: yes — This is one of the three baseline cards defined in the charter itself. It must pass. It accurately measures 'truthful commitments' via schema satisfaction.
- seat-3: yes — Exact restatement of charter metric well_formed_rate; valid under truthful commitments.
- seat-4: yes — It directly measures compliance with declared outcome schemas using a clear ratio over a defined window, strongly supporting truthful commitments.
- seat-5: abstained

**p15 `practice_revision_rate`**

- seat-1: yes — Measures whether revisions actually reach registered artifacts over a window, using a fraction-of-windows unit that is well defined over rolling ten windows. Distinct id and observation, and the threshold 0.1 sets a low bar for the process rather than for outcomes.
- seat-2: yes — Good metric for 'the capacity to revise inadequate practices'. It measures the frequency of structural changes/amendments, indicating an active process of improvement.
- seat-3: yes — Measures practice revision rate over windows; directly capacity to revise inadequate practices.
- seat-4: yes — Counting windows with adopted amendments gives an observable measure of whether practices are being revised, with a clear rolling window.
- seat-5: abstained

**p16 `turnover`**

- seat-1: no — Id 'turnover' collides with p01 and p22 and the quantity is the same filled-notional-over-equity ratio. The description is a verbatim copy of p12's description, so the card adds no new measurement.
- seat-2: no — Duplicate ID 'turnover' with p01 and p22. Redundant.
- seat-3: no — Another duplicate id 'turnover' with p01/p22; redundant and not needed.
- seat-4: yes — The metric is directly computable from venue fills and account equity and provides a clear cap on capital turnover.
- seat-5: abstained

**p17 `verdict_stability`**

- seat-1: no — The description ('share of verdict windows in which the mean score crosses a revision threshold') is under-specified: 'revision threshold' is undefined in the charter, so the card cannot be computed unambiguously. The window 'rolling 20 verdict windows' also nests windows in an undefined way.
- seat-2: no — The acceptable region 'above 0.5' for 'fraction of verdict windows where mean score crosses a revision threshold' is confusing. If scores cross a threshold frequently, does that mean good revision or instability? It's ambiguous. Also, 'verdict_stability' suggests low change is good, but the metric demands high change. Contradictory naming and logic.
- seat-3: no — Verdict stability crossing threshold is vague and not clearly revision of practices; norm mapping weak.
- seat-4: no — The revision threshold is undefined, so different evaluators could compute different results; the card therefore lacks a reproducible observation rule.
- seat-5: abstained

**p18 `registration_adoption`**

- seat-1: yes — Registrations per window is a simple count directly tied to 'useful inquiry', with a clear unit and observation. The id 'registration_adoption' is unique and the acceptable region 'at least 1' is checkable.
- seat-2: no — The norm is 'useful inquiry', but counting registrations is a volume metric, not a quality/utility metric. Many registrations could be useless noise. It doesn't measure if the inquiries are useful.
- seat-3: no — Norm useful inquiry is about useful inquiry not just count of registrations; number of proposals is not a quality measure.
- seat-4: yes — The number of registered proposals is observable and can measure active inquiry, although it measures activity more than proposal quality.
- seat-5: abstained

**p19 `amendment_pass_rate`**

- seat-1: no — Overlaps heavily with p10 ('revision_adoption') while being defined over amendment registrations only; the two card ids would both measure adoption and invitation to double-count. 'Survive to a later edition without being reverted' is also not a fact the window can observe yet.
- seat-2: yes — Good metric for 'the capacity to revise inadequate practices'. It measures the success rate of proposed revisions, indicating effective feedback loops.
- seat-3: yes — Amendment pass rate measures surviving revisions, aligning with capacity to revise inadequate practices.
- seat-4: no — The observation does not clearly distinguish proposed amendments from registered amendments or establish how survival without reversion is recorded, making the rate non-reproducible.
- seat-5: abstained

**p20 `verdict_dispersion`**

- seat-1: no — Verdict dispersion and evaluator agreement (p11) measure essentially the same signal in opposite directions. Keeping both with different norms lets a producer satisfy one while failing the other, which muddies rather than sharpens 'truthful commitments'.
- seat-2: no — Similar to p03 and p11, this measures evaluator disagreement/dispersion. High dispersion might indicate difficult cases, not necessarily untruthfulness. It's an evaluator characteristic metric.
- seat-3: yes — Verdict dispersion reflects truthful commitment clarity; observable from verdict scores.
- seat-4: yes — Maximum-minus-minimum verdict scores within an evaluator set are observable and provide a clearly defined measure of disagreement relevant to truthful commitments.
- seat-5: abstained

**p21 `reasoning_capital_turnover`**

- seat-1: no — Yet another filled-notional-over-equity card, this time with a 'between 0 and 3' region on a rolling 100 ticks window, and an observation of 'computed_per_window.turnover' whose presence in the runtime is not established. The window and region also diverge from the other turnover cards, so the card set would contain conflicting thresholds for one number.
- seat-2: no — Duplicate ID 'reasoning_cap' vs others? No, ID is unique. But it measures turnover again ('filled notional... divided by equity'). Redundant with p01, p07, p12, p16, p22.
- seat-3: no — Id 'reasoning_capital_turnover' duplicates turnover concept with window ticks; not distinct and norm care already covered.
- seat-4: yes — The runtime-computed turnover value is directly observable and the bounded region gives a clear scarce-capital constraint, notwithstanding its different tick window.
- seat-5: abstained

**p22 `turnover`**

- seat-1: no — Duplicate id 'turnover' and duplicate measurement (filled notional over starting equity). Assigning it the norm 'useful inquiry' is also a category error: capital turnover is a scarce-resource/capital-efficiency quantity, and the same number already appears under 'care with scarce resources'.
- seat-2: no — Duplicate ID 'turnover' with p01 and p16. Redundant.
- seat-3: no — Id 'turnover' again with norm useful inquiry; duplicates p01/p16 and overlaps cost care cards.
- seat-4: yes — The metric has a clear calculation from filled notional and starting equity and can measure deployment of capital toward information-generating activity.
- seat-5: abstained

**p23 `verdict_mean_score`**

- seat-1: yes — Mean verdict score is a distinct, clearly observable quantity with a bounded unit (0-1) and a unique id. It gives a direct, monotone measure of whether declared outcomes were met, which fits 'truthful commitments'.
- seat-2: no — Mean verdict score is a general quality metric. While related to truthfulness, 'truthful commitments' is specifically about keeping promises. A high mean score could come from easy, non-committal returns. It's less precise than p05 (accountabilty_score) or p14 (well_formed_rate) for this specific norm.
- seat-3: yes — Mean verdict score reflects adherence to declared outcomes under truthful commitments; observable.
- seat-4: yes — Mean evaluator verdict score is directly computable from verdict scores and offers a clear signal related to adherence to declared outcomes.
- seat-5: abstained

**p24 `tool_call_efficiency`**

- seat-1: no — The observation ('registrations (models, assemblies, routers, tools, amendments)') does not contain tool-call counts per return, so the stated number cannot be computed from the named source. The metric itself (tool calls per well-formed return) is reasonable, but this card as written is not executable.
- seat-2: no — Duplicate concept with p02 (model_cost_efficiency). P02 measures monetary cost, which is a more direct measure of 'scarce resources' (micro-USD) than tool calls (which vary by model complexity). P02 is superior.
- seat-3: no — Observation lists registrations not tool call counts; mismatched observation for tool_call_efficiency under care with scarce resources.
- seat-4: no — The description measures tool calls per return, but the observation points to registrations rather than tool-call records, so the stated number cannot be derived as written.
- seat-5: abstained

## Passing set

Rendered as TOML a manifest could carry. Note: `worlds.py` does not read a `[charter]` table today; the runtime still starts from `seed_charter()`. Card ids that collided among passing cards were suffixed.

```toml
[charter]
edition = 1

[[charter.cards]]
id = "model_cost_efficiency"
norm = "care with scarce resources"
description = "Total compute cost in micro-USD incurred per well-formed return generated."
units = "micro-USD per return"
window = "rolling 100 returns"
acceptable_region = "at most 500"
observation = "computed_per_window/model_cost_efficiency"

[[charter.cards]]
id = "revision_rate"
norm = "the capacity to revise inadequate practices"
description = "Proportion of returns that include a tool call amendment or register proposal in the window"
units = "fraction"
window = "rolling 100 well-formed returns"
acceptable_region = "above 0.05"
observation = "computed_per_window: well_formed_rate (returns) and tool_calls"

[[charter.cards]]
id = "accountabilty_score"
norm = "truthful commitments"
description = "Share of well-formed returns whose declared outcome is actually verifiable from public facts"
units = "fraction"
window = "rolling 100 well-formed returns"
acceptable_region = "at least 0.9"
observation = "public_facts_a_card_may_name_as_its_observation (e.g., wallet balance, positions, verdict scores)"

[[charter.cards]]
id = "revision_adoption"
norm = "the capacity to revise inadequate practices"
description = "Share of metric-card amendments accepted by the charter process that were proposed after a card breached its acceptable region."
units = "fraction"
window = "rolling 50 amendments"
acceptable_region = "at least 0.5"
observation = "registrations (amendments) and prior card breach records"

[[charter.cards]]
id = "cost_per_return"
norm = "care with scarce resources"
description = "Wallet cost of producing one well-formed return."
units = "micro-USD per return"
window = "rolling 100 returns"
acceptable_region = "below the median of the previous window"
observation = "Mean wallet cost, in micro-USD, of the well-formed producer returns in the window."

[[charter.cards]]
id = "well_formed_rate"
norm = "truthful commitments"
description = "Share of returns that satisfy their declared outcome schema."
units = "fraction"
window = "rolling 100 returns"
acceptable_region = "at least 0.9"
observation = "Well-formed returns divided by all invocations in the window."

[[charter.cards]]
id = "practice_revision_rate"
norm = "the capacity to revise inadequate practices"
description = "Share of rolling windows in which at least one registered amendment changes a metric card, assembly, router, model, or tool."
units = "fraction of windows"
window = "rolling 10 windows"
acceptable_region = "at least 0.1"
observation = "Registrations of adopted amendments, counted over the rolling ten windows."

[[charter.cards]]
id = "verdict_mean_score"
norm = "truthful commitments"
description = "The mean verdict score assigned by evaluators to the producer's returns, reflecting adherence to declared outcomes."
units = "score (0-1)"
window = "rolling 50 settled forecasts per evaluator"
acceptable_region = "at least 0.8"
observation = "verdict scores and their means"
```

## What the population asked for that the runtime cannot measure yet

**Passing cards**

- p02 `model_cost_efficiency`: no window computes this id today; the observation names "computed_per_window/model_cost_efficiency"; region parses
- p04 `revision_rate`: no window computes this id today; the observation names "computed_per_window: well_formed_rate (returns) and tool_calls"; region parses
- p05 `accountabilty_score`: no window computes this id today; the observation names "public_facts_a_card_may_name_as_its_observation (e.g., wallet balance, positions, verdict scores)"; region parses
- p10 `revision_adoption`: no window computes this id today; the observation names "registrations (amendments) and prior card breach records"; region parses
- p13 `cost_per_return`: computed per window today; region parses
- p14 `well_formed_rate`: computed per window today; region parses
- p15 `practice_revision_rate`: no window computes this id today; the observation names "Registrations of adopted amendments, counted over the rolling ten windows."; region parses
- p23 `verdict_mean_score`: no window computes this id today; the observation names "verdict scores and their means"; region parses

**Failed or unvoted cards**

- p01 `turnover`: computed per window today; region parses
- p03 `verdict_consistency`: no window computes this id today; the observation names "public_facts_a_card_may_name_as_its_observation/verdict_scores"; region parses
- p06 `position_concentration`: no window computes this id today; the observation names "computed_per_window: turnover and account equity at window start"; region parses
- p07 `turnover_intensity`: no window computes this id today; the observation names "filled notional divided by equity at window start"; region parses
- p08 `revision_rate`: no window computes this id today; the observation names "count of action=noop divided by total returns"; region parses
- p09 `evaluator_consensus`: no window computes this id today; the observation names "count of verdict_reject divided by total verdicts"; region parses
- p11 `evaluator_agreement`: no window computes this id today; the observation names "verdict scores and their means"; region parses
- p12 `turnover_cap`: no window computes this id today; the observation names "venue fills (size, price, fee, realised amount) and account equity"; region parses
- p16 `turnover`: computed per window today; region parses
- p17 `verdict_stability`: no window computes this id today; the observation names "verdict scores and their means"; region parses
- p18 `registration_adoption`: no window computes this id today; the observation names "registrations (models, assemblies, routers, tools, amendments)"; region parses
- p19 `amendment_pass_rate`: no window computes this id today; the observation names "registrations (models, assemblies, routers, tools, amendments) and registration_feedback"; region parses
- p20 `verdict_dispersion`: no window computes this id today; the observation names "verdict scores and their means"; region parses
- p21 `reasoning_capital_turnover`: no window computes this id today; the observation names "computed_per_window.turnover"; region parses
- p22 `turnover`: computed per window today; region parses
- p24 `tool_call_efficiency`: no window computes this id today; the observation names "registrations (models, assemblies, routers, tools, amendments)"; region parses

Only the four computed ids (`cost_per_return`, `well_formed_rate`, `forecast_skill`, `turnover`) feed the price controller today. Any other passing card is a request for a new window computation in `loop.py::_close_price_window`, and the observation text above says what the population expects that computation to read.

## Cost

| purpose | assembly | role | model | served by | in | out | reasoning | stop | cost (micro-USD) | source | s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| propose | `seed-observer` | producer | `qwen/qwen3.7-flash` | `qwen/qwen3.7-flash` | 6507 | 452 | 0 | stop | 101 | reported | 6.6 |
| propose | `seed-decider` | producer | `deepseek/deepseek-v4-flash-0731` | `deepseek/deepseek-v4-flash-0731` | 5810 | 388 | 0 | stop | 94 | reported | 6.3 |
| propose | `eval-a` | evaluator | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` | 5650 | 294 | 36 | stop | 995 | reported | 20.9 |
| propose | `eval-b` | evaluator | `qwen/qwen3.8-flash` | `qwen/qwen3.8-flash` | 6531 | 611 | 200 | stop | 410 | reported | 11.2 |
| propose | `eval-c` | evaluator | `tencent/hy3` | `tencent/hy3` | 5753 | 361 | 0 | stop | 475 | reported | 5.7 |
| propose | `eval-d` | evaluator | `openai/gpt-5.6-luna` | `openai/gpt-5.6-luna` | 5646 | 516 | 225 | stop | 2031 | reported | 6.4 |
| propose | `antagonist-a` | antagonist | `qwen/qwen3.8-flash` | `qwen/qwen3.8-flash` | 6531 | 589 | 200 | stop | 399 | reported | 9.9 |
| propose | `meta-a` | meta | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 5811 | 428 | 0 | stop | 2257 | reported | 4.2 |
| propose | `meta-b` | meta | `qwen/qwen3.7-flash` | `qwen/qwen3.7-flash` | 6507 | 409 | 0 | stop | 95 | reported | 6.2 |
| vote | `meta-a` | meta | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 8006 | 1986 | 0 | stop | 5982 | reported | 15.0 |
| vote | `seed-observer` | producer | `qwen/qwen3.7-flash` | `qwen/qwen3.7-flash` | 8913 | 1779 | 0 | stop | 499 | reported | 20.7 |
| vote | `eval-c` | evaluator | `tencent/hy3` | `tencent/hy3` | 7918 | 1160 | 0 | stop | 1658 | reported | 12.5 |
| vote | `eval-d` | evaluator | `openai/gpt-5.6-luna` | `openai/gpt-5.6-luna` | 7783 | 1325 | 387 | stop | 3536 | reported | 12.6 |
| vote | `seed-decider` | producer | `deepseek/deepseek-v4-flash-0731` | `deepseek/deepseek-v4-flash-0731` | 8005 | 1819 | 0 | stop | 498 | reported | 55.9 |

Total: 19030 micro-USD ($0.0190); 19030 of it from the provider's reported usage, the rest from the manifest price table.

### Failures

- propose `eval-a` (`z-ai/glm-5.3-flash`): malformed reply (stop_reason=stop)

### Replies that yielded nothing

These completed (no provider error) but carried no card or no ballot the script could read; the runtime would record them the same way (malformed or abstained). First 400 characters of each:

- vote `seed-decider` (`deepseek/deepseek-v4-flash-0731`, stop stop): `{ "votes": [ { "id": "p01", "vote": "no", "reason": "The description and observation are inconsistent with the proposal's id 'turnover' and norm 'care with scarce resources'. The acceptable region 'below 5.0' is vague and not aligned with the specified units and window. The proposal lacks a clear and verifiable observation source." }, { "id": "p02", "vote": "no", "reason": "The description and pro`
