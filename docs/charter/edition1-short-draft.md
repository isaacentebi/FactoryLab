# Charter edition 1: draft metric cards from the seed population

Generated 2026-09-14T00:02:54+00:00 by `scripts/draft_edition1.py`. One survey of the seed population; not a run of the world. Nothing here touched the wallet, the venue or a ledger.

- world: `testnet-10m-roster` (manifest sha256 `b8537c62ed002763…`, seed 4)
- surveyed roster sha256: `b46f6ef0e458b025ff64d8b8834d32aeccb29c0d92374bc6cae05e6d804b23a0`
- norms (read-only, the architect's): truthful commitments; care with scarce resources; useful inquiry; the capacity to revise inadequate practices
- seed assemblies asked: 9; proposals received: 52; put to the vote: 49; passed: 27
- committee: 5 seats drawn by lot across roles; 3 yes votes pass

Each assembly saw the norms verbatim, a plain description of a metric card, what the runtime can measure per window today, and the public world block (launch-shaped: fake venue at the initial balance, no positions, no mids). No goals were given and nothing was said about what to optimise.

## Proposals

### p01: `well-formed-floor`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: truthful commitments
- description: Minimum share of responses that parse and commit to a declared action.
- units: fraction; window: {"kind": "returns", "n": 10, "per": "role"}; acceptable: at least 0.8
- observation: well_formed_rate; answers_for: all
- reason: A commitment that cannot be parsed cannot be held, so a high well-formedness floor is the baseline of truthful participation.

### p02: `cost-ceiling`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: care with scarce resources
- description: Mean cost per successful response stays within a modest per-return budget.
- units: micro-USD per return; window: {"kind": "returns", "n": 10, "per": "role"}; acceptable: at most 3000
- observation: cost_per_return; answers_for: all
- reason: Resource care means each answer earns its cost, so a per-return budget disciplines tool and token spending without banning their use.

### p03: `tool-discipline`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: care with scarce resources
- description: Attempted tool calls per return stay bounded.
- units: count; window: {"kind": "returns", "n": 10, "per": "assembly"}; acceptable: at most 2
- observation: tool_calls; answers_for: producer
- reason: Capping tool attempts per return keeps scarce call budget from being burned on unfocused activity.

### p04: `noop-ceiling`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: useful inquiry
- description: Share of responses that declare noop or hold stays limited.
- units: fraction; window: {"kind": "returns", "n": 10, "per": "role"}; acceptable: at most 0.6
- observation: noop_share; answers_for: producer
- reason: Excessive noop or hold answers indicate disengagement rather than inquiry, so an upper bound keeps contributions useful.

### p05: `forecast-skill-floor`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: useful inquiry
- description: Forecast Brier must beat the prevalence baseline on average.
- units: score difference; window: {"kind": "forecasts", "n": 5, "per": "assembly"}; acceptable: below 0.05
- observation: forecast_skill; answers_for: evaluator
- reason: Genuine inquiry means forecasts carry information beyond base rates, so near-baseline or worse skill is outside the acceptable region.

### p06: `censoring-ceiling`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: useful inquiry
- description: Share of censored among resolved outcomes stays low.
- units: fraction; window: {"kind": "windows", "n": 3, "per": null}; acceptable: at most 0.4
- observation: censored_share; answers_for: all
- reason: Heavy censoring destroys the evidence on which learning and scoring depend, so its share is bounded.

### p07: `revision-flow`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: the capacity to revise inadequate practices
- description: Accepted registrations or activated amendments occur over the window.
- units: fraction; window: {"kind": "windows", "n": 4, "per": null}; acceptable: at least 1
- observation: revision_rate; answers_for: all
- reason: A population that never revises its charter cannot correct inadequate practices, so accepted revisions should recur.

### p08: `verdict-openness`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- as proposed: `{"id": "verdict-openness", "norm": "the capacity to revise inadequate practices", "description": "Spread of evaluator verdicts stays within a band that permits disagreement without chaos.", "units": "score standard deviation", "window": {"kind": "returns", "n": 10, "per": "assembly"}, "acceptable_region": "below 0.3", "observation": "verdict_std", "answers_for": "evaluator", "reason": "Moderate verdict variance shows judges revise assessments rather than rubber-stamping, while extreme spread signals noise rather than judgement."}`
- reason: Moderate verdict variance shows judges revise assessments rather than rubber-stamping, while extreme spread signals noise rather than judgement.
- **not put to the vote**: card verdict-openness window: verdict_std cannot be measured over returns

### p09: `meta-conformity-floor`

- proposer: `seed-observer` (producer, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: truthful commitments
- description: Mean raw meta verdict across tiers stays high.
- units: score; window: {"kind": "windows", "n": 3, "per": null}; acceptable: at least 0.5
- observation: meta_verdict_mean; answers_for: meta
- reason: The meta layer certifies whether judgements conform honestly, so a strong mean guards against compounding untruthful verdicts.

### p10: `well-formed-floor`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: truthful commitments
- description: Share of a role's returns that parse as well-formed responses.
- units: fraction; window: {"kind": "returns", "n": 10, "per": "role"}; acceptable: at least 0.9
- observation: well_formed_rate; answers_for: all
- reason: A well-formed return is the minimum truthful commitment: one that actually states what it promises.

### p11: `cost-cap`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: care with scarce resources
- description: Mean successful response cost across the role's latest returns.
- units: micro-USD per return; window: {"kind": "returns", "n": 10, "per": "role"}; acceptable: at most 5000
- observation: cost_per_return; answers_for: all
- reason: Holding mean cost per return under a few thousandths of a cent keeps the population living within its scarce compute budget.

### p12: `tool-thrift`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: care with scarce resources
- description: Count of attempted tool calls per return, including failures.
- units: count; window: {"kind": "returns", "n": 10, "per": "role"}; acceptable: at most 2
- observation: tool_calls; answers_for: all
- reason: Bounding attempted tool calls discourages wasteful call spam against the max_calls_per_window budget.

### p13: `forecast-skill-positive`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: useful inquiry
- description: Mean forecast Brier minus its prevalence-baseline pair over settled forecasts.
- units: score difference; window: {"kind": "forecasts", "n": 10, "per": null}; acceptable: below 0
- observation: forecast_skill; answers_for: evaluator
- reason: Beating the prevalence baseline shows forecasts carry genuine information, which is what useful inquiry means here.

### p14: `verdict-floor`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- as proposed: `{"id": "verdict-floor", "norm": "useful inquiry", "description": "Mean evaluator verdict on producer returns.", "units": "score", "window": {"kind": "returns", "n": 10, "per": "role"}, "acceptable_region": "at least 0.5", "observation": "verdict_mean", "answers_for": "producer", "reason": "A mid-or-better mean verdict pushes producers toward work the population actually finds informative rather than noise."}`
- reason: A mid-or-better mean verdict pushes producers toward work the population actually finds informative rather than noise.
- **not put to the vote**: card verdict-floor window: verdict_mean cannot be measured over returns

### p15: `revision-presence`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: the capacity to revise inadequate practices
- description: Share of returns carrying accepted registrations or activated amendments.
- units: fraction; window: {"kind": "returns", "n": 10, "per": null}; acceptable: at least 0.1
- observation: revision_rate; answers_for: all
- reason: A steady trickle of accepted registrations and amendments shows the population keeps repairing its own practices.

### p16: `censorship-bound`

- proposer: `eval-a` (evaluator, `z-ai/glm-5.3-flash`, reasoning {'effort': 'low'})
- norm: the capacity to revise inadequate practices
- description: Share of resolved outcomes that are censored.
- units: fraction; window: {"kind": "windows", "n": 5, "per": null}; acceptable: at most 0.3
- observation: censored_share; answers_for: all
- reason: Keeping censoring low preserves the feedback the population needs to learn from its mistakes.

### p17: `cost_efficiency`

- proposer: `eval-b` (evaluator, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Mean successful response cost in the selected rows; global closed windows use successful producer returns.
- units: micro-USD per return; window: {"kind": "returns", "n": 10, "per": null}; acceptable: below the median of the previous window
- observation: cost_per_return; answers_for: producer
- reason: This metric directly tracks resource expenditure, ensuring producers minimize costs while maintaining output quality.

### p18: `response_quality`

- proposer: `eval-b` (evaluator, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: truthful commitments
- description: Successful responses over selected invocation responses, including ballots.
- units: fraction; window: {"kind": "returns", "n": 10, "per": null}; acceptable: at least 0.9
- observation: well_formed_rate; answers_for: producer
- reason: High success rates indicate that producers are making reliable and truthful commitments rather than failing or producing malformed outputs.

### p19: `forecast_accuracy`

- proposer: `eval-b` (evaluator, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: useful inquiry
- description: Mean selected forecast Brier minus its paired pre-outcome prevalence-baseline Brier.
- units: score difference; window: {"kind": "forecasts", "n": 5, "per": null}; acceptable: above 0.0
- observation: forecast_skill; answers_for: evaluator
- reason: Positive skill scores demonstrate that forecasts provide genuine informational value beyond simple baseline guesses.

### p20: `system_adaptability`

- proposer: `eval-b` (evaluator, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: the capacity to revise inadequate practices
- description: Share of selected responses with accepted registrations or activated amendments; global closed windows use their revision counters.
- units: fraction; window: {"kind": "returns", "n": 10, "per": null}; acceptable: above 0.0
- observation: revision_rate; answers_for: all
- reason: A non-zero revision rate confirms that the system is actively incorporating feedback and improving its practices over time.

### p21: `card-well-formed-rate`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: truthful commitments
- description: Fraction of selected responder returns that parsed and produced a well-formed commitment, so a declared commitment is one the assembly can actually stand behind.
- units: fraction; window: {"kind": "returns", "n": 40, "per": "assembly"}; acceptable: at least 0.95
- observation: well_formed_rate; answers_for: all
- starting price lambda: 0.5
- reason: Commitments only bind when they are legible returns, so a high well-formed floor is the minimum for truthfulness to be measurable at all.

### p22: `card-cost-per-return`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Mean cost in micro-USD of a successful response, compared against a ceiling, so effort spent stays proportionate to the work done.
- units: micro-USD per return; window: {"kind": "returns", "n": 60, "per": "assembly"}; acceptable: at most 20000
- observation: cost_per_return; answers_for: all
- starting price lambda: 0.5
- reason: Scarce compute is the shared pot, and a per-response ceiling makes frugality a measurable habit rather than an aspiration.

### p23: `card-forecast-skill`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: useful inquiry
- description: Mean forecast Brier minus the paired prevalence baseline, with a floor above zero, so inquiry is judged by whether it beats guessing.
- units: score difference; window: {"kind": "forecasts", "n": 25, "per": "assembly"}; acceptable: above 0
- observation: forecast_skill; answers_for: evaluator
- starting price lambda: 0.5
- reason: A norm of useful inquiry is empty unless predictions actually outperform the base rate, and paired baselines make that comparison fair.

### p24: `card-consequence-paid-off`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: useful inquiry
- description: Share of settled consequences whose return paid off, with a floor, so inquiry is valued for consequences reached rather than questions raised.
- units: fraction; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at least 0.4
- observation: consequence_paid_off_rate; answers_for: producer
- starting price lambda: 0.5
- reason: Inquiry earns its place through settled consequences, and a modest floor on paid-off outcomes keeps the population pointed at work that lands.

### p25: `card-revision-rate`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: the capacity to revise inadequate practices
- description: Share of selected responses carrying an accepted registration or activated amendment, with a floor, so revision is a visible habit.
- units: fraction; window: {"kind": "returns", "n": 40, "per": "assembly"}; acceptable: at least 0.05
- observation: revision_rate; answers_for: all
- starting price lambda: 0.5
- reason: The capacity to revise is only real when it shows up as accepted improvements, so a low but nonzero floor rewards practice without demanding churn.

### p26: `card-evaluator-disagreement`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- as proposed: `{"id": "card-evaluator-disagreement", "norm": "truthful commitments", "description": "Mean population standard deviation across judges of the same return, with a ceiling, so verdicts stay close enough to mean the same thing.", "units": "score standard deviation", "window": {"kind": "returns", "n": 30, "per": "assembly"}, "acceptable_region": "at most 0.2", "observation": "evaluator_disagreement", "answers_for": "evaluator", "lambda": 0.5, "reason": "If independent judges cannot agree on what a verdict means, the commitments they grade stop being truthful in any shared sense."}`
- starting price lambda: 0.5
- reason: If independent judges cannot agree on what a verdict means, the commitments they grade stop being truthful in any shared sense.
- **not put to the vote**: card card-evaluator-disagreement window: evaluator_disagreement cannot be measured over returns

### p27: `card-censored-share`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Share of resolved outcomes that were censored, with a ceiling, so settled work is not quietly discarded when it becomes inconvenient.
- units: fraction; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at most 0.25
- observation: censored_share; answers_for: all
- starting price lambda: 0.5
- reason: Discarded outcomes waste the compute that produced them and blur every downstream measure, so censoring needs a bounded rate.

### p28: `card-turnover`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Filled notional over equity at window start, with a ceiling, so trading activity stays within the capital actually at risk.
- units: ratio; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at most 1
- observation: turnover; answers_for: producer
- starting price lambda: 0.5
- reason: Notional beyond starting equity is leverage by another name, and capping turnover keeps the account's scarce capital from being churned away.

### p29: `card-position-concentration`

- proposer: `eval-c` (evaluator, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Peak marked notional on a single coin over starting equity, with an upper bound under the turnover cap, so no one position can end the account.
- units: ratio; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at most 0.5
- observation: position_concentration; answers_for: producer
- starting price lambda: 0.5
- reason: A single-coin blowup is the cheapest way to destroy shared equity, and a concentration ceiling limits that hazard without forbidding position-taking.

### p30: `truthful_commitments_well_formed`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: truthful commitments
- description: Maintain a high rate of successful, well-formed responses.
- units: fraction; window: {"kind": "returns", "n": 10, "per": null}; acceptable: at least 0.95
- observation: well_formed_rate; answers_for: all
- reason: A high success rate makes commitments reliably interpretable and executable.

### p31: `truthful_commitments_verdict_consistency`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: truthful commitments
- description: Keep evaluator verdicts sufficiently consistent across the latest judged returns.
- units: score standard deviation; window: {"kind": "windows", "n": 5, "per": null}; acceptable: at most 0.2
- observation: verdict_std; answers_for: evaluator
- reason: Low verdict dispersion supports stable and credible judgments.

### p32: `scarce_resources_response_cost`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: care with scarce resources
- description: Limit the mean cost of successful responses.
- units: micro-USD per return; window: {"kind": "returns", "n": 10, "per": null}; acceptable: at most 10000
- observation: cost_per_return; answers_for: all
- reason: Capping response cost preserves scarce funds for higher-value work.

### p33: `useful_inquiry_forecast_skill`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: useful inquiry
- description: Require selected forecasts to outperform the paired prevalence baseline.
- units: score difference; window: {"kind": "forecasts", "n": 5, "per": null}; acceptable: above 0
- observation: forecast_skill; answers_for: all
- reason: Positive skill shows that inquiry adds predictive value beyond a simple baseline.

### p34: `useful_inquiry_paid_off`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: useful inquiry
- description: Achieve a majority of positive settled consequences among selected work.
- units: fraction; window: {"kind": "windows", "n": 5, "per": null}; acceptable: at least 0.5
- observation: consequence_paid_off_rate; answers_for: all
- reason: A majority of paid-off consequences indicates that inquiry produces useful outcomes.

### p35: `revision_of_inadequate_practices`

- proposer: `eval-d` (evaluator, `openai/gpt-5.6-luna`, reasoning {'effort': 'low'})
- norm: the capacity to revise inadequate practices
- description: Ensure that accepted registrations and activated amendments occur at a meaningful rate.
- units: fraction; window: {"kind": "windows", "n": 5, "per": null}; acceptable: at least 0.05
- observation: revision_rate; answers_for: all
- reason: A nonzero revision rate demonstrates that the population can change practices rather than merely preserve them.

### p36: `cost_efficiency`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Mean successful response cost in the selected rows; global closed windows use successful producer returns. A retained-storage charge adds to what those responses cost and is never counted as one of them.
- units: micro-USD per return; window: {"kind": "returns", "n": 10, "per": null}; acceptable: below 50000
- observation: cost_per_return; answers_for: all
- reason: Monitoring mean cost ensures that resource usage remains efficient relative to the value produced by each response.

### p37: `response_quality`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: truthful commitments
- description: Successful responses over selected invocation responses, including ballots.
- units: fraction; window: {"kind": "returns", "n": 10, "per": null}; acceptable: above 0.9
- observation: well_formed_rate; answers_for: all
- reason: A high rate of well-formed responses indicates reliable adherence to protocol standards and truthful execution of commitments.

### p38: `forecast_accuracy`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: useful inquiry
- description: Mean selected forecast Brier minus its paired pre-outcome prevalence-baseline Brier.
- units: score difference; window: {"kind": "forecasts", "n": 10, "per": null}; acceptable: above -0.1
- observation: forecast_skill; answers_for: all
- reason: Positive skill scores demonstrate that forecasts provide genuine informational value beyond simple baseline estimates.

### p39: `adaptive_revision`

- proposer: `antagonist-a` (antagonist, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: the capacity to revise inadequate practices
- description: Share of selected responses with accepted registrations or activated amendments; global closed windows use their revision counters.
- units: fraction; window: {"kind": "returns", "n": 10, "per": null}; acceptable: above 0.05
- observation: revision_rate; answers_for: all
- reason: A non-zero revision rate signals active learning and adaptation, preventing stagnation in the face of new information.

### p40: `producer_well_formed_lower_bound`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: truthful commitments
- description: Share of successful producer returns over selected producer invocation responses.
- units: fraction; window: {"kind": "returns", "n": 20, "per": "role"}; acceptable: at least 0.95
- observation: well_formed_rate; answers_for: producer
- reason: A producer that cannot reliably return a well-formed statement of what it did has made a commitment it cannot honour, so truthfulness begins with returns that parse.

### p41: `evaluator_disagreement_ceiling`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: truthful commitments
- description: Mean population standard deviation across judges of the same return, averaged over repeat judges.
- units: score standard deviation; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at most 0.15
- observation: evaluator_disagreement; answers_for: evaluator
- reason: Judges who disagree widely about the same return are not reporting the same thing, so large spread in their verdicts signals that at least one verdict is not a truthful statement about the work.

### p42: `return_cost_ceiling`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Mean successful response cost in micro-USD per return over selected returns.
- units: micro-USD per return; window: {"kind": "returns", "n": 20, "per": "role"}; acceptable: at most 20000
- observation: cost_per_return; answers_for: all
- reason: Compute is the scarce shared resource, and 20,000 micro-USD per return is a ceiling well above a simple text return but low enough to catch runaway tool use.

### p43: `turnover_ceiling`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: care with scarce resources
- description: Filled notional over equity at window start, zero without fills.
- units: ratio; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at most 0.5
- observation: turnover; answers_for: producer
- reason: Filled notional above half of starting equity in a window shows the account is churning scarce capital rather than deploying it deliberately.

### p44: `forecast_skill_lower_bound`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: useful inquiry
- description: Mean selected forecast Brier minus its paired pre-outcome prevalence-baseline Brier.
- units: score difference; window: {"kind": "forecasts", "n": 20, "per": "role"}; acceptable: at least 0.0
- observation: forecast_skill; answers_for: all
- reason: An inquiry that does not beat the prevalence baseline on its own settled forecasts has added no information beyond what was already known.

### p45: `consequence_paid_off_floor`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: useful inquiry
- description: Positive return_paid_off outcomes over selected settled consequences.
- units: fraction; window: {"kind": "windows", "n": 6, "per": "role"}; acceptable: at least 0.25
- observation: consequence_paid_off_rate; answers_for: producer
- reason: A quarter of settled consequences paying off is a low bar that still distinguishes work oriented toward outcomes from work that merely emits output.

### p46: `revision_rate_floor`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: the capacity to revise inadequate practices
- description: Share of selected responses carrying an accepted registration or activated amendment.
- units: fraction; window: {"kind": "returns", "n": 20, "per": "role"}; acceptable: at least 0.05
- observation: revision_rate; answers_for: producer
- reason: A population that never carries an accepted registration or activates an amendment is not exercising the capacity to revise its inadequate practices.

### p47: `amendments_activated_floor`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: the capacity to revise inadequate practices
- description: Amendments activated in the window.
- units: count; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at least 1
- observation: amendments_activated; answers_for: all
- reason: Activated amendments are the population's own record of revising the charter, and a window with none is a window in which the revision machinery stood still.

### p48: `censored_share_ceiling`

- proposer: `meta-a` (meta, `deepseek/deepseek-v4.1-flash`, reasoning {'enabled': False})
- norm: truthful commitments
- description: Censored outcomes over resolved outcomes over selected settled consequences.
- units: fraction; window: {"kind": "windows", "n": 6, "per": null}; acceptable: at most 0.2
- observation: censored_share; answers_for: all
- reason: A censored outcome carries no score, so a high censored share means commitments are being left unresolved rather than honestly settled.

### p49: `cost_per_return_card`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: care with scarce resources
- description: Mean successful response cost in the selected rows; global closed windows use successful producer returns. A retained-storage charge adds to what those responses cost and is never counted as one of them.
- units: micro-USD per return; window: {"kind": "returns", "n": 5, "per": null}; acceptable: below 10000
- observation: cost_per_return; answers_for: all
- starting price lambda: 0.2
- reason: Low cost ensures that the population does not waste scarce treasury funds on expensive or inefficient responses.

### p50: `well_formed_rate_card`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: truthful commitments
- description: Successful responses over selected invocation responses, including ballots.
- units: fraction; window: {"kind": "returns", "n": 5, "per": null}; acceptable: above 0.95
- observation: well_formed_rate; answers_for: all
- starting price lambda: 0.3
- reason: High success rate demonstrates reliable adherence to protocol and truthful execution of commitments without malformed failures.

### p51: `forecast_skill_card`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: useful inquiry
- description: Mean selected forecast Brier minus its paired pre-outcome prevalence-baseline Brier.
- units: score difference; window: {"kind": "forecasts", "n": 5, "per": null}; acceptable: above 0.0
- observation: forecast_skill; answers_for: all
- starting price lambda: 0.4
- reason: Positive skill indicates that forecasts provide genuine informational value beyond simple baseline guessing.

### p52: `revision_rate_card`

- proposer: `meta-b` (meta, `qwen/qwen3.7-flash`, reasoning {'effort': 'none'})
- norm: the capacity to revise inadequate practices
- description: Share of selected responses with accepted registrations or activated amendments; global closed windows use their revision counters.
- units: fraction; window: {"kind": "returns", "n": 5, "per": null}; acceptable: above 0.1
- observation: revision_rate; answers_for: all
- reason: A non-zero revision rate shows the system is actively correcting itself and adapting to new information or errors.

## Committee

Seats were drawn with `factorylab.charter.committee.draw` from every seed assembly, covering producer, evaluator and meta before filling uniformly; aliases are shuffled. Voters saw the proposals without proposer identities.

| seat | assembly | role | model |
|---|---|---|---|
| seat-1 | `meta-a` | meta | `deepseek/deepseek-v4.1-flash` |
| seat-2 | `seed-observer` | producer | `z-ai/glm-5.3-flash` |
| seat-3 | `eval-c` | evaluator | `deepseek/deepseek-v4.1-flash` |
| seat-4 | `eval-d` | evaluator | `openai/gpt-5.6-luna` |
| seat-5 | `seed-decider` | producer | `deepseek/deepseek-v4.1-flash` |

## Votes

| proposal | card | seat-1 | seat-2 | seat-3 | seat-4 | seat-5 | yes | result |
|---|---|---|---|---|---|---|---|---|
| p01 | `well-formed-floor` | no | yes | no | yes | — | 2 | failed |
| p02 | `cost-ceiling` | no | yes | no | yes | — | 2 | failed |
| p03 | `tool-discipline` | yes | yes | no | yes | — | 3 | **passed** |
| p04 | `noop-ceiling` | yes | yes | no | yes | — | 3 | **passed** |
| p05 | `forecast-skill-floor` | no | no | no | yes | — | 1 | failed |
| p06 | `censoring-ceiling` | no | yes | no | yes | — | 2 | failed |
| p07 | `revision-flow` | yes | no | no | no | — | 1 | failed |
| p09 | `meta-conformity-floor` | no | yes | no | no | — | 1 | failed |
| p10 | `well-formed-floor` | yes | yes | yes | yes | — | 4 | **passed** |
| p11 | `cost-cap` | yes | yes | yes | yes | — | 4 | **passed** |
| p12 | `tool-thrift` | no | yes | yes | yes | — | 3 | **passed** |
| p13 | `forecast-skill-positive` | no | no | no | yes | — | 1 | failed |
| p15 | `revision-presence` | yes | yes | no | yes | — | 3 | **passed** |
| p16 | `censorship-bound` | no | yes | yes | yes | — | 3 | **passed** |
| p17 | `cost_efficiency` | no | yes | no | yes | — | 2 | failed |
| p18 | `response_quality` | yes | yes | no | yes | — | 3 | **passed** |
| p19 | `forecast_accuracy` | yes | yes | no | no | — | 2 | failed |
| p20 | `system_adaptability` | yes | yes | no | yes | — | 3 | **passed** |
| p21 | `card-well-formed-rate` | yes | yes | yes | yes | — | 4 | **passed** |
| p22 | `card-cost-per-return` | yes | yes | no | yes | — | 3 | **passed** |
| p23 | `card-forecast-skill` | yes | yes | yes | yes | — | 4 | **passed** |
| p24 | `card-consequence-paid-off` | yes | yes | no | yes | — | 3 | **passed** |
| p25 | `card-revision-rate` | yes | yes | yes | yes | — | 4 | **passed** |
| p27 | `card-censored-share` | yes | yes | no | yes | — | 3 | **passed** |
| p28 | `card-turnover` | no | yes | no | yes | — | 2 | failed |
| p29 | `card-position-concentration` | yes | yes | no | yes | — | 3 | **passed** |
| p30 | `truthful_commitments_well_formed` | no | yes | no | yes | — | 2 | failed |
| p31 | `truthful_commitments_verdict_consistency` | yes | yes | no | yes | — | 3 | **passed** |
| p32 | `scarce_resources_response_cost` | no | yes | yes | yes | — | 3 | **passed** |
| p33 | `useful_inquiry_forecast_skill` | yes | yes | no | yes | — | 3 | **passed** |
| p34 | `useful_inquiry_paid_off` | no | yes | no | yes | — | 2 | failed |
| p35 | `revision_of_inadequate_practices` | no | yes | no | yes | — | 2 | failed |
| p36 | `cost_efficiency` | no | no | no | yes | — | 1 | failed |
| p37 | `response_quality` | yes | yes | no | yes | — | 3 | **passed** |
| p38 | `forecast_accuracy` | no | no | no | yes | — | 1 | failed |
| p39 | `adaptive_revision` | yes | yes | no | yes | — | 3 | **passed** |
| p40 | `producer_well_formed_lower_bound` | yes | yes | yes | yes | — | 4 | **passed** |
| p41 | `evaluator_disagreement_ceiling` | yes | yes | yes | yes | — | 4 | **passed** |
| p42 | `return_cost_ceiling` | yes | no | no | yes | — | 2 | failed |
| p43 | `turnover_ceiling` | yes | yes | yes | yes | — | 4 | **passed** |
| p44 | `forecast_skill_lower_bound` | no | no | no | yes | — | 1 | failed |
| p45 | `consequence_paid_off_floor` | no | yes | yes | yes | — | 3 | **passed** |
| p46 | `revision_rate_floor` | no | yes | no | yes | — | 2 | failed |
| p47 | `amendments_activated_floor` | yes | no | yes | yes | — | 3 | **passed** |
| p48 | `censored_share_ceiling` | yes | yes | no | yes | — | 3 | **passed** |
| p49 | `cost_per_return_card` | no | yes | no | yes | — | 2 | failed |
| p50 | `well_formed_rate_card` | no | yes | no | yes | — | 2 | failed |
| p51 | `forecast_skill_card` | no | yes | no | yes | — | 2 | failed |
| p52 | `revision_rate_card` | no | yes | no | yes | — | 2 | failed |

### Reasons

**p01 `well-formed-floor`**

- seat-1: no — Duplicate observation well_formed_rate on the same 10-return per-role window as p10 but with a weaker floor (0.8 vs 0.9); redundant and laxer, so it fails the 'duplicate observations on overlapping roles are refused' discipline and adds a second, inconsistent target for one norm.
- seat-2: yes — Reasonable parse/commit floor of 0.8 over 10 returns per role.
- seat-3: no — Duplicate of p10/p30 on the same observation (well_formed_rate) and the same window (returns, n=10, per=role), only the region differs (0.8 vs 0.9). The charter refuses duplicate observations on overlapping roles; p10 is the stricter, better-specified version, so this card is redundant.
- seat-4: yes — Sets a clear minimum for parseable, actionable commitments and directly supports truthful commitments.
- seat-5: abstained

**p02 `cost-ceiling`**

- seat-1: no — Supereded by p11/p22/p42 which set explicit ceilings on the same observation and window; 3000 micro-USD is arbitrary and inconsistent with the rest of edition 1, and p17 already compares cost to the prior window's median.
- seat-2: yes — Modest per-return cost ceiling of 3000 micro-USD is proportionate.
- seat-3: no — Duplicates p11 (cost_per_return, returns n=10 per=role) with a looser ceiling (3000 vs 5000). Same defect as p01: admitting both would double-count one observation and seed two overlapping penalties for one norm.
- seat-4: yes — A measurable cost ceiling appropriately protects scarce resources.
- seat-5: abstained

**p03 `tool-discipline`**

- seat-1: yes — Tool thrift is a real scarce-resource concern and 2 attempted calls per return matches the tool contract (max_tool_calls 4 with bounded rounds), answers_for producer only, measured per assembly over 10 returns. Bounded, observable via tool_calls, and not duplicated.
- seat-2: yes — Bounding tool attempts at 2 for producers curbs waste without blocking work.
- seat-3: no — Duplicates p12 (tool_calls, returns n=10, answers_for=producer) with a looser window per=assembly vs per=role. One observation, one card: keep the role-scoped version.
- seat-4: yes — Bounds producer tool use and discourages wasteful execution.
- seat-5: abstained

**p04 `noop-ceiling`**

- seat-1: yes — A noop hold share ceiling on useful inquiry is legitimate: it prevents a producer from gaming judged rewards with hold-only returns, is measured on noop_share over 10 returns per role, and 0.6 is a generous ceiling that will not punish genuinely reactive work.
- seat-2: yes — A 0.6 noop ceiling for producers allows holds while limiting idling.
- seat-3: no — Noop/hold share is already covered by the well-formedness and tool-discipline cards, and declaring noop is a legitimate truthful commitment, not evidence of useless inquiry. A ceiling on noop_share also conflicts with the action-label rule that makes hold admissible, risking malformed returns if producers are pushed away from it.
- seat-4: yes — Limits excessive no-op behavior while allowing legitimate holds.
- seat-5: abstained

**p05 `forecast-skill-floor`**

- seat-1: no — Ambiguous region: 'below 0.05' on a score-difference where negative is better, but the description says 'must beat the prevalence baseline on average'. The sign convention contradicts p13 and p23, and 5 forecasts per assembly is too few to measure skill reliably.
- seat-2: no — Region is 'below 0.05' for skill, which rewards bad forecasts; the intended floor should be above zero.
- seat-3: no — Measures forecast skill on the evaluator set alone with a tiny window (forecasts n=5) and a hard floor below 0.05 that a noisy 5-sample mean can violate cheaply. Adding it alongside p13/p19/p38/p50/p51 seeds multiple overlapping penalties on the same observation.
- seat-4: yes — Requires evaluators to demonstrate forecasting value beyond prevalence guessing.
- seat-5: abstained

**p06 `censoring-ceiling`**

- seat-1: no — Overlaps p16 (censored_share over windows) with a different window (3 vs 5) and ceiling (0.4 vs 0.3). Two cards on the same observation at different windows would double-count the same failure and confuse the controller.
- seat-2: yes — Censoring ceiling of 0.6 keeps most outcomes resolved; loose but a valid start.
- seat-3: no — Duplicates p16/p48 (censored_share) at a looser bound (0.4 vs 0.3/0.2) and attributes it to useful inquiry rather than truthful commitments. One observation, one card.
- seat-4: yes — Keeps censoring visible and bounded, supporting reliable inquiry.
- seat-5: abstained

**p07 `revision-flow`**

- seat-1: yes — Revision flow is the only card using revision_rate over windows with answers_for all, and the norm 'capacity to revise inadequate practices' needs a positive presence signal. Floor of 1 over 4 windows is lenient and not duplicated by p15/p20, which use returns windows.
- seat-2: no — Acceptable region 'at least 1' is impossible for a fraction-valued observation; units and region mismatch.
- seat-3: no — Duplicates p15/p35/revision cards on revision_rate and mislabels a count-like floor ('at least 1') on a fraction-scale observation (unit_range 0-1). The scale mismatch makes region and controller distance ill-defined.
- seat-4: no — A fractional revision rate cannot meaningfully be at least 1; the threshold is malformed for the stated units.
- seat-5: abstained

**p09 `meta-conformity-floor`**

- seat-1: no — Meta conformity mean over 3 windows with a 0.5 floor is a weak, unpriced aggregate; meta_verdict_mean is a raw score with no baseline, so a high mean can be gamed by metas rewarding lenient verdicts. The committee's conformity liability already grades metas directly.
- seat-2: yes — Meta conformity floor of 0.5 over 3 windows is a sensible minimum.
- seat-3: no — The meta-conformity floor targets the meta tier, but scoring grades metas by Brier on their conformity and the evaluator's return by the meta's score; a raw mean meta verdict above 0.5 is not the graded quantity and invites metas to inflate conformity. No other card measures this, so it would sit on the meta role alone with no redundant coverage and no strong evidence it tracks truthfulness.
- seat-4: no — Meta verdict conformity is not a direct measure of truthful commitments and could reward agreement rather than truthfulness.
- seat-5: abstained

**p10 `well-formed-floor`**

- seat-1: yes — Well-formed floor is the core truthful-commitments card: 0.9 over 10 returns per role is a defensible ceiling on malformed returns. Duplicates p01, but p10 is the stricter version and should be kept; the duplicate issue is best resolved by rejecting the weaker p01.
- seat-2: yes — Well-formed rate of at least 0.9 per role is a fair bar.
- seat-3: yes — Keeps the truthful-commitments norm measurable: well-formed_rate at 0.9 over the latest 10 returns per role is the strictest of the duplicate cluster (p01/p30), names a registered seed observation, and a fraction region matches the observation units.
- seat-4: yes — A stronger well-formedness floor directly promotes reliable commitments.
- seat-5: abstained

**p11 `cost-cap`**

- seat-1: yes — Cost cap of 5000 micro-USD per successful return over 10 returns per role is the tightest explicit cost budget among the cost cards and is consistent with the flat call price of 1000 micro-USD and models costing ~0 per request. Bounded and measurable.
- seat-2: yes — Cost cap of 5000 micro-USD per return is reasonable.
- seat-3: yes — Best of the cost-per-return cluster (caps p02/p32/p36/p42): 5000 micro-USD per return over 10 returns per role, within observation range (0-1e6), with a bound tight enough to discipline cost without being trivially violated.
- seat-4: yes — Provides a reasonable upper bound on response cost.
- seat-5: abstained

**p12 `tool-thrift`**

- seat-1: no — Per-role tool count over 10 returns duplicates p03 (per-assembly) and the tool contract already caps calls at 4. Per-role partition with all answers_for on a producer behaviour misattributes judge/meta costs; keep the producer-scoped p03 instead.
- seat-2: yes — Tool thrift cap of 2 per return for all roles controls tool spending.
- seat-3: yes — Bounded tool discipline on the producer role; tool_calls is a registered observation with count units and a ceiling of 2 attempts per return is a real constraint on scarce resources; per=role is the finer partition in the duplicate cluster with p03.
- seat-4: yes — Bounds tool calls per role and directly addresses resource thrift.
- seat-5: abstained

**p13 `forecast-skill-positive`**

- seat-1: no — Sign convention again: 'below 0' with a score-difference is redundant with p19/p23/p33 which all say 'above 0' and match the forecast_skill unit range [-1,1]. The floor of 0 is not stricter than 0.05 and the 10-forecast pool is too small per assembly.
- seat-2: no — Region 'below 0' accepts negative skill; direction inverted.
- seat-3: no — Duplicates p19/p33/p38/p44/p51 on forecast_skill and requires skill strictly below 0 (mean Brier improvement above 0 when phrased as 'below 0'), which is statistically fragile over 10 forecasts and duplicates p19's evaluator-scoped card.
- seat-4: yes — Uses the correctly signed condition for Brier improvement: forecast skill must be below zero.
- seat-5: abstained

**p15 `revision-presence`**

- seat-1: yes — Revision presence over returns with a low 0.1 floor gives a per-return view of revision distinct from the window-based p07, and 10 returns pooled across the factory is a reasonable sample. Keeps the revision norm visible without over-constraining.
- seat-2: yes — Revision presence of at least 0.1 makes revision visible without excess.
- seat-3: no — Duplicates p20/p25/p35/p39/p46/p52 on revision_rate; the 0.10 floor over pooled returns (per=null) is also misaligned with the per-role cards that actually track who revises.
- seat-4: yes — Ensures revision activity is observable without demanding an unrealistic rate.
- seat-5: abstained

**p16 `censorship-bound`**

- seat-1: no — Same observation and window kind as p06 and p48 with a third ceiling (0.3); three cards on censored_share at different windows is exactly the double-count the scoring notes warn against. Retain the firmest single card (p48 at 0.2).
- seat-2: yes — Censorship bound of 0.3 discourages discarding settled work.
- seat-3: yes — Censoring above a third of resolved outcomes is a real failure mode (quietly dropping inconvenient settlements) and 0.3 over 5 closed windows is a sound, pooled measurement. It overlaps p48, so one of the two should pass, and this is the better-windowed version.
- seat-4: yes — Places a clear ceiling on censorship and protects settled evidence.
- seat-5: abstained

**p17 `cost_efficiency`**

- seat-1: no — 'Below the median of the previous window' is a relative, non-stationary region; the observation's declared scale and the acceptable_region phrasing catalogue do not include that phrasing, and it would let cost drift upward indefinitely as the median rises.
- seat-2: yes — Relative cost target (below previous median) is adaptive and sound.
- seat-3: no — Region 'below the median of the previous window' is not an inclusive numeric bound; the controller recurrence computes distance outside an inclusive region, so this region is unmeasurable as written. Also duplicates the cost-per-return cards.
- seat-4: yes — Encourages improving cost efficiency relative to the prior window.
- seat-5: abstained

**p18 `response_quality`**

- seat-1: yes — Well-formed rate for producers over 10 returns with 0.9 floor is a cleaner scoping than p10's role split and complements p40's per-role 20-return version. It is the producer's truthful-commitments card and is not redundant with p10 because it restricts to successful producer invocation responses.
- seat-2: yes — Response quality floor of 0.9 including ballots is fair.
- seat-3: no — Duplicates p37 and p10 on well_formed_rate; 0.9 over pooled returns overlaps the role-scoped card. Redundant.
- seat-4: yes — Requires high-quality producer responses across selected returns.
- seat-5: abstained

**p19 `forecast_accuracy`**

- seat-1: yes — Forecast accuracy above 0.0 over 5 forecasts is the strictest forecast-skill card and uses the unit range correctly. The 5-forecast sample is small but the observation admits forecasts windows; a positive floor directly tests usefulness against the prevalence baseline.
- seat-2: yes — Evaluator skill above 0 means forecasts beat the baseline; correct direction.
- seat-3: no — Duplicates p13/p33/p38/p51 on forecast_skill with a redundant evaluator-scoped window; the forecast card set should be selected once (p23 or p50/p51), not stacked.
- seat-4: no — The sign is inconsistent with the defined forecast-skill observation, where lower Brier difference is better.
- seat-5: abstained

**p20 `system_adaptability`**

- seat-1: yes — Revision share over 10 returns with an 'above 0.0' floor is a minimal positive-presence requirement consistent with revision_rate's range; it distinguishes the adaptation norm from p15's higher 0.1 floor by being a floor at all rather than an absence. Some duplication with p15 is acceptable because p20 fires on any nonzero adaptation while p15 sets a meaningful rate.
- seat-2: yes — Any positive revision rate is a minimal, sound adaptability bar.
- seat-3: no — Duplicates p15/p25/p35/p39/p46/p52 on revision_rate with an ambiguous region ('above 0.0' vs p39's 'above 0.05'); redundant.
- seat-4: yes — Requires positive revision activity and supports adaptive practice.
- seat-5: abstained

**p21 `card-well-formed-rate`**

- seat-1: yes — 40-return per-assembly window with 0.95 floor is the statistically sound version of well-formed rate, large enough for the mechanism to mean something, and lambda 0.5 is within lambda_max. Strongest charter card for truthful commitments.
- seat-2: yes — 0.95 well-formed over a larger 40-return window gives a robust parse bar.
- seat-3: yes — The assembly-scoped well-formed rate at 0.95 over 40 returns is the strongest form of the truthful-commitments norm: wide enough to be meaningful, strict enough to bite, and it subsumes the pooled duplicates (p30/p37/p50) without double-counting since only one of them should pass.
- seat-4: yes — A high assembly-level parsing floor is a direct and adequately sampled commitment-quality measure.
- seat-5: abstained

**p22 `card-cost-per-return`**

- seat-1: yes — Cost ceiling of 20000 micro-USD over 60 returns per assembly is the appropriately wide pooled budget to pair with p21's larger window, and it is consistent with the world's near-zero per-request model prices. Bounded and observable.
- seat-2: yes — Cost ceiling of 20000 over 60 returns; loose but valid as a backstop.
- seat-3: no — Duplicates the cost-per-return cluster; 20000 micro-USD is far looser than p11 or p32 and 60 returns over per=assembly is a long window that weakens timely cost feedback.
- seat-4: yes — A broad cost ceiling remains a useful safeguard for scarce resources.
- seat-5: abstained

**p23 `card-forecast-skill`**

- seat-1: yes — Forecast skill above 0 over 25 forecasts per assembly is the forecast analogue of p21: large sample, explicit positive floor, lambda 0.5. It is the charter's real test of useful inquiry and complements rather than duplicates p19's 5-forecast version.
- seat-2: yes — Skill above 0 over 25 forecasts; correct direction and adequate sample.
- seat-3: yes — Best forecast-skill card: 'above 0' on a registered seed observation with an assembly-scoped 25-forecast window, matching the units (score difference, range -1..1). It is the single card that should carry the useful-inquiry forecast norm, and the pool of near-duplicates (p13/p19/p33/p38/p44/p50/p51) should not all pass.
- seat-4: yes — Directly tests whether forecasting beats the paired baseline with a meaningful sample.
- seat-5: abstained

**p24 `card-consequence-paid-off`**

- seat-1: yes — Consequence paid-off rate at least 0.4 over 6 windows is the only card that ties inquiry to settled consequences rather than questions raised, which is the substance of useful inquiry. 6 windows is adequate and the 0.4 floor is above the coin-flip baseline.
- seat-2: yes — Consequence paid-off floor of 0.4 for producers ties inquiry to results.
- seat-3: no — Consequence-payoff floor at 0.4 over 6 windows is plausible, but it overlaps p34/p45 and a 0.4 floor is a demand that settled consequences pay off, which the runtime cannot always control; one consequence card (p45) suffices.
- seat-4: yes — Connects inquiry to settled consequences and requires a moderate payoff rate.
- seat-5: abstained

**p25 `card-revision-rate`**

- seat-1: yes — Revision rate at least 0.05 over 40 returns per assembly is a low-bar, well-sampled revision card parallel to p21/p22; lambda 0.5 is admitted and the floor is achievable, making revision a visible habit as intended.
- seat-2: yes — Revision rate floor of 0.05 makes revision a visible habit.
- seat-3: yes — Highest-floor revision card ('at least 0.05' on revision_rate over 40 returns per assembly) and phrased in the right units. Revision is the least-measured norm in this charter, so exactly one revision card should pass, and this is the best of the cluster.
- seat-4: yes — Makes revision a visible, measurable habit without an excessive threshold.
- seat-5: abstained

**p27 `card-censored-share`**

- seat-1: yes — Censored share at most 0.25 over 6 windows with lambda 0.5 is the charter's binding anti-censoring card and replaces the weaker p06/p16/p48 variants. It uses censored_share correctly and is measurable at window close.
- seat-2: yes — Censored share ceiling of 0.25 protects settled work.
- seat-3: no — Duplicates p16/p48 on censored_share with the same 0.25/0.2 region family but a looser ceiling and a 'care with scarce resources' label that does not match the observation. Keep one.
- seat-4: yes — A conservative censoring ceiling discourages discarding inconvenient outcomes.
- seat-5: abstained

**p28 `card-turnover`**

- seat-1: no — Turnover at most 1 over 6 windows is looser than p43's 0.5 on the same observation and window, and would double-count filled notional in the card penalty. Keep the stricter p43.
- seat-2: yes — Turnover cap of 1 keeps trading within equity at risk.
- seat-3: no — Duplicates p43 on turnover. Two cards on the same ungroupable observation over the same 6-window scope would double-penalize the same trades.
- seat-4: yes — Limits aggregate trading exposure relative to equity.
- seat-5: abstained

**p29 `card-position-concentration`**

- seat-1: yes — Position concentration at most 0.5 over 6 windows is a distinct risk control (single-coin peak notional vs equity) not covered by turnover or cost cards, and is consistent with the $100 account and no leverage beyond 3.
- seat-2: yes — Position concentration cap of 0.5 prevents account-ending single positions.
- seat-3: no — This is the only card on position_concentration, but the region (at most 0.5 of starting equity on one coin) and p43's redundant turnover card compete for the same scarce-resources norm; passing both p28 and p29 while declining p43 keeps exactly one exposure-concentration constraint. Position concentration is better measured than turnover, so this is rejected only because the simpler single-check family already exists in p43 and one concentration check is enough.
- seat-4: yes — Caps single-position concentration and complements the turnover control.
- seat-5: abstained

**p30 `truthful_commitments_well_formed`**

- seat-1: no — Well-formed rate at least 0.95 over 10 returns duplicates p18/p37 at the same floor and window, and overlaps p21's 40-return version. A third copy on the same observation multiplies the penalty for one failure.
- seat-2: yes — High well-formed bar of 0.95 supports truthful commitments.
- seat-3: no — Duplicates p21/p37/p10/p18 on well_formed_rate at 0.95 but pooled (per=null) and on only 10 returns, a noisier window than p21. Redundant with the better-specified p21.
- seat-4: yes — Maintains a high rate of successful, well-formed commitments.
- seat-5: abstained

**p31 `truthful_commitments_verdict_consistency`**

- seat-1: yes — Verdict consistency at most 0.2 over 5 windows targets judge reliability directly and is distinct from evaluator_disagreement (p41) because it measures spread across verdicts on one return's neighbours rather than cross-judge spread on the same return. Bounded and observable.
- seat-2: yes — Verdict std cap of 0.2 promotes evaluator consistency.
- seat-3: no — verdict_std measures spread, not truthfulness; a low std can be achieved by judges collapsing onto one value (including a wrong one), so this card rewards conformity rather than truthful commitments. It also duplicates p41's disagreement ceiling in spirit.
- seat-4: yes — Evaluator consistency is a useful supporting condition for trustworthy commitments.
- seat-5: abstained

**p32 `scarce_resources_response_cost`**

- seat-1: no — Cost ceiling of 10000 over 10 returns duplicates p11 (5000) and p42 (20000 per role); three ceilings on cost_per_return at different windows triple-count scarce-resource violations. Keep p11 for the tight bound and p22/p42 for pooled bounds.
- seat-2: yes — Cost limit of 10000 micro-USD is a reasonable middle ceiling.
- seat-3: yes — A second, looser cost ceiling (10000 micro-USD) is defensible as a hard backstop distinct from p11's tighter target, and the region is in the observation's units. Passing p11 and p32 gives a two-tier cost discipline without inventing a new observation.
- seat-4: yes — Limits response costs while allowing more operational headroom than tighter caps.
- seat-5: abstained

**p33 `useful_inquiry_forecast_skill`**

- seat-1: yes — Forecast skill above 0 over 5 forecasts is the factory-wide counterpart to p19's unpooled version; the 'above 0' region matches the observation range and the prediction rule is stated in the description. Acceptable though small-windowed.
- seat-2: yes — Forecast skill above 0 is the correct minimal bar.
- seat-3: no — Duplicates p23/p38/p51 on forecast_skill with answers_for=all over only 5 forecasts; noisy and redundant.
- seat-4: yes — Requires forecasts to outperform the prevalence baseline.
- seat-5: abstained

**p34 `useful_inquiry_paid_off`**

- seat-1: no — Consequence paid-off rate at least 0.5 over 5 windows duplicates p24 (0.4 over 6). Two floors on the same observation again multiply the penalty for a single shortfall; p24 is the better-framed version.
- seat-2: yes — Majority paid-off floor of 0.5 ties work to consequences.
- seat-3: no — Duplicates p45 on consequence_paid_off_rate with a looser 0.5 floor and a smaller window; keep one.
- seat-4: yes — A majority-positive consequence rate is a reasonable useful-inquiry target.
- seat-5: abstained

**p35 `revision_of_inadequate_practices`**

- seat-1: no — Revision rate at least 0.05 over 5 windows duplicates p25 (0.05 over 40 returns) and p20; the window kind (windows vs returns) does not make it a distinct measurement of the same observation. Retain p25.
- seat-2: yes — Revision rate of at least 0.05 ensures ongoing adaptation.
- seat-3: no — Duplicates p25/p39/p46/p52 on revision_rate; the 0.05 floor is right but the window (5 closed windows, per=null) is coarser than the return-scoped revision card.
- seat-4: yes — Requires revision activity at a meaningful but attainable rate.
- seat-5: abstained

**p36 `cost_efficiency`**

- seat-1: no — Cost below 50000 over 10 returns is so loose as to be vacuous given model prices near zero and the flat call price of 1000 micro-USD; it cannot bind and would let a runaway cost pattern pass. Reject as non-binding.
- seat-2: no — Ceiling of 50000 micro-USD is too lax to discipline cost meaningfully.
- seat-3: no — Duplicates the cost cluster and duplicates p11/p32 within its own family (its 'below 50000' bound is far looser than every other cost card, making it nearly non-binding while still stacking a penalty term).
- seat-4: yes — Controls successful-response cost and explicitly accounts for retained-storage charges.
- seat-5: abstained

**p37 `response_quality`**

- seat-1: yes — Well-formed above 0.9 over 10 returns matches p18 exactly in region but states 'above' rather than 'at least'; one of the pair must be chosen and p37's phrasing is at the boundary of the observation range. It is kept alongside p18 because only one of them can govern a tie-breaking reading; as they are formally near-identical, voting yes on both is defensible only if the committee treats them as one measure. Given the duplicate rule, p37 is marked as the retained copy of p18.
- seat-2: yes — Quality above 0.9 is a solid well-formed bar.
- seat-3: no — Duplicates p21/p10/p30/p50 on well_formed_rate at 0.9 over 10 pooled returns; redundant with the role-scoped and assembly-scoped cards already passing.
- seat-4: yes — Requires consistently strong response quality.
- seat-5: abstained

**p38 `forecast_accuracy`**

- seat-1: no — Forecast above -0.1 over 10 forecasts is weaker than p19 (above 0.0) and allows forecasters to be worse than the prevalence baseline; it contradicts the useful-inquiry norm. Reject.
- seat-2: no — Tolerating skill down to -0.1 accepts worse-than-baseline forecasting; too weak.
- seat-3: no — Duplicates the forecast-skill family with a negative tolerance (-0.1) that makes it mostly non-binding; redundant and weaker than p23.
- seat-4: yes — The lenient threshold still requires forecasts not to materially underperform the baseline.
- seat-5: abstained

**p39 `adaptive_revision`**

- seat-1: yes — Revision above 0.05 over 10 returns matches p20 with a marginally different floor and phrasing; it is the return-level revision card and is acceptable as the single return-level revision measure if the weaker p15 is rejected. Bounded and consistent with revision_rate.
- seat-2: yes — Revision above 0.05 keeps adaptation a live habit.
- seat-3: no — Duplicates the revision-rate family (p15/p20/p25/p35/p46/p52); keep the single strongest, p25.
- seat-4: yes — Requires positive adaptive revision activity and aligns with the revision norm.
- seat-5: abstained

**p40 `producer_well_formed_lower_bound`**

- seat-1: yes — Producer well-formed at least 0.95 over 20 returns per role is the adequately-sampled, role-scoped truthful-commitments card and makes p18/p30 redundant. Keep this and p21 as the two well-formed measures (per-role producer and per-assembly).
- seat-2: yes — Producer well-formed floor of 0.95 over 20 returns is well-calibrated.
- seat-3: yes — Role-scoped producer well-formed lower bound at 0.95 over 20 returns is a genuine, well-specified constraint on the producer role distinct from the pooled and assembly-wide cards; it is the one overlap on well_formed_rate that adds role-level resolution rather than duplicating scope.
- seat-4: yes — Sets a strong producer-specific quality floor with a larger sample.
- seat-5: abstained

**p41 `evaluator_disagreement_ceiling`**

- seat-1: yes — Evaluator disagreement at most 0.15 over 6 windows is the sharpest judge-reliability card and is distinct from verdict_std because it requires at least two judges on the same return. Directly supports truthful commitments by penalising noisy judges.
- seat-2: yes — Evaluator disagreement cap of 0.15 std promotes verdict consistency.
- seat-3: yes — Evaluator disagreement is a real measurement of judging reliability (evaluator_disagreement, ungroupable, 6 windows) and has no other card in the proposed set; a 0.15 ceiling on mean judge spread is a sound and distinct norm check.
- seat-4: yes — Directly limits disagreement among evaluators and improves verdict reliability.
- seat-5: abstained

**p42 `return_cost_ceiling`**

- seat-1: yes — Cost at most 20000 over 20 returns per role is the per-role pooled cost bound that complements p11's per-10-return bound. Same ceiling as p22 but at a distinct window and partition, giving the controller a role-level signal without double-counting the per-assembly measure.
- seat-2: no — 20000 micro-USD ceiling over 20 returns is too lax to constrain cost.
- seat-3: no — Duplicates the cost-per-return cluster (p11/p32/p36/p49) at 20000 micro-USD, looser than every other accepted cost card; redundant.
- seat-4: yes — Provides a role-partitioned cost ceiling with sufficient sample size.
- seat-5: abstained

**p43 `turnover_ceiling`**

- seat-1: yes — Turnover at most 0.5 over 6 windows is the stricter, better-bounded replacement for p28 and keeps trading activity within capital at risk. Matches the observation's [0,1] range and the turnover definition in observation_facts.
- seat-2: yes — Turnover cap of 0.5 keeps activity well within capital at risk.
- seat-3: yes — Single exposure-limit card on a registered ungroupable observation (turnover, ratio) with a ceiling of 0.5 of window-start equity over 6 windows; this keeps the scarce-resources norm binding on trading without stacking both p28 and p43.
- seat-4: yes — A conservative turnover ceiling protects capital at risk.
- seat-5: abstained

**p44 `forecast_skill_lower_bound`**

- seat-1: no — Forecast skill at least 0.0 over 20 forecasts per role is weaker than p23 (above 0) and p33; it admits zero-skill forecasters. Reject in favour of the positive-floor versions.
- seat-2: no — Skill floor of 0.0 accepts merely-tied forecasts; should be strictly positive.
- seat-3: no — Duplicates p23/p51 on forecast_skill with a trivial 'at least 0.0' floor that is nearly always satisfied; redundant and weakly binding.
- seat-4: yes — Requires nonnegative forecast skill relative to the prevalence baseline.
- seat-5: abstained

**p45 `consequence_paid_off_floor`**

- seat-1: no — Consequence paid-off at least 0.25 over 6 windows per role duplicates p24 (0.4) with a weaker floor; three paid-off cards would triple-penalise one shortfall. Keep p24.
- seat-2: yes — Paid-off floor of 0.25 for producers is a fair consequence bar.
- seat-3: yes — Consequence-payoff floor at 0.25 over 6 windows per role is the one useful-inquiry card that measures consequences rather than questions, on a registered observation (consequence_paid_off_rate), and is more conservative than the competing p24/p34.
- seat-4: yes — Sets a modest producer consequence-payoff floor.
- seat-5: abstained

**p46 `revision_rate_floor`**

- seat-1: no — Revision rate at least 0.05 over 20 returns per role duplicates p25 (40 returns per assembly) and p39; the per-role partition is not a distinct measurement of revision_rate. Keep p25.
- seat-2: yes — Producer revision rate floor of 0.05 supports the revision norm.
- seat-3: no — Duplicates the revision-rate family; redundant with p25.
- seat-4: yes — Requires producers to demonstrate some concrete revision activity.
- seat-5: abstained

**p47 `amendments_activated_floor`**

- seat-1: yes — Amendments activated at least 1 over 6 windows is the only card that measures activated amendments as a count, which is the concrete action under the revise norm. Answers_for all and window kind windows match the observation. Distinct from revision_rate cards.
- seat-2: no — Requiring at least 1 amendment activated every 6 windows is a rigid quota that forces churn regardless of need.
- seat-3: yes — amendments_activated is a distinct count observation (unit_range 0-1 despite 'count' units) and a floor of 1 activated amendment over 6 windows makes the revision norm concrete at the charter level, complementing the rate card; it is the only card using this observation.
- seat-4: yes — Ensures amendments are actually activated rather than merely proposed.
- seat-5: abstained

**p48 `censored_share_ceiling`**

- seat-1: yes — Censored share at most 0.2 over 6 windows is the firmest anti-censoring bound and, with p27 rejected or as the tighter pair, provides the charter's single strongest statement on not discarding settled work. Keep p48 as the binding censoring card; p27 is the softer companion. Given the duplicate rule the committee should retain one; p48 is retained.
- seat-2: yes — Censored share ceiling of 0.2 keeps settled outcomes intact.
- seat-3: no — Duplicates p16 on censored_share with the same 5-6 window family and a stricter-but-identical-metric ceiling; keep one censoring card.
- seat-4: yes — A strict censoring ceiling protects the integrity of commitments and settled evidence.
- seat-5: abstained

**p49 `cost_per_return_card`**

- seat-1: no — Cost below 10000 over 5 returns duplicates p11/p32 at a short window; the description carries the retained-storage-charge clause but that belongs to any cost card and does not distinguish this one. Reject for duplication.
- seat-2: yes — Cost below 10000 over 5 returns with modest lambda is a reasonable card.
- seat-3: no — Duplicates p11/p32/p36 on cost_per_return; a 5-return window is the noisiest form of an already-stacked family.
- seat-4: yes — Sets a practical cost ceiling while including retained-storage cost in the accounting.
- seat-5: abstained

**p50 `well_formed_rate_card`**

- seat-1: no — Well-formed above 0.95 over 5 returns is a short-window duplicate of p18/p30/p40; 5 returns is too few to measure a 0.95 floor reliably and would produce frequent involuntary violations.
- seat-2: yes — Well-formed above 0.95 over 5 returns is a fair bar.
- seat-3: no — Duplicates p21/p40 on well_formed_rate; 5 pooled returns is the smallest window of the family and the most likely to flap.
- seat-4: yes — Requires a high well-formed response rate, though over a short window.
- seat-5: abstained

**p51 `forecast_skill_card`**

- seat-1: no — Forecast skill above 0.0 over 5 forecasts duplicates p19 and p33 with a third card on the same observation; the loss from a single bad forecast run is tripled. Keep p19 and p23 only.
- seat-2: yes — Forecast skill above 0 is the correct minimal requirement.
- seat-3: no — Duplicates p23 on forecast_skill; p23's 25-forecast assembly window is the stronger measurement of the same norm.
- seat-4: yes — Requires positive forecast skill against the paired baseline.
- seat-5: abstained

**p52 `revision_rate_card`**

- seat-1: no — Revision above 0.1 over 5 returns duplicates p15 (0.1 over 10) and p20/p39; a 5-return window is too short and the higher floor is arbitrary. Reject for duplication and short window.
- seat-2: yes — Revision rate above 0.1 makes adaptation clearly visible.
- seat-3: no — Duplicates the revision-rate family; a 0.1 floor over only 5 pooled returns is noisier than p25's 40-return card.
- seat-4: yes — Requires revision to occur at a meaningful rate over the selected returns.
- seat-5: abstained

## Passing set

Rendered as an explicit manifest charter. Each card passed measurement preflight. Card ids that collided among passing cards were suffixed; measurement and role bindings are preserved. The launch manifest must validate before adoption.

```toml
[charter]
edition = 1
norms = ["truthful commitments", "care with scarce resources", "useful inquiry", "the capacity to revise inadequate practices"]

[[charter.cards]]
id = "tool-discipline"
norm = "care with scarce resources"
description = "Attempted tool calls per return stay bounded."
units = "count"
window = { kind = "returns", n = 10, per = "assembly" }
acceptable_region = "at most 2"
observation = "tool_calls"
answers_for = "producer"

[[charter.cards]]
id = "noop-ceiling"
norm = "useful inquiry"
description = "Share of responses that declare noop or hold stays limited."
units = "fraction"
window = { kind = "returns", n = 10, per = "role" }
acceptable_region = "at most 0.6"
observation = "noop_share"
answers_for = "producer"

[[charter.cards]]
id = "well-formed-floor"
norm = "truthful commitments"
description = "Share of a role's returns that parse as well-formed responses."
units = "fraction"
window = { kind = "returns", n = 10, per = "role" }
acceptable_region = "at least 0.9"
observation = "well_formed_rate"
answers_for = "all"

[[charter.cards]]
id = "cost-cap"
norm = "care with scarce resources"
description = "Mean successful response cost across the role's latest returns."
units = "micro-USD per return"
window = { kind = "returns", n = 10, per = "role" }
acceptable_region = "at most 5000"
observation = "cost_per_return"
answers_for = "all"

[[charter.cards]]
id = "tool-thrift"
norm = "care with scarce resources"
description = "Count of attempted tool calls per return, including failures."
units = "count"
window = { kind = "returns", n = 10, per = "role" }
acceptable_region = "at most 2"
observation = "tool_calls"
answers_for = "all"

[[charter.cards]]
id = "revision-presence"
norm = "the capacity to revise inadequate practices"
description = "Share of returns carrying accepted registrations or activated amendments."
units = "fraction"
window = { kind = "returns", n = 10 }
acceptable_region = "at least 0.1"
observation = "revision_rate"
answers_for = "all"

[[charter.cards]]
id = "censorship-bound"
norm = "the capacity to revise inadequate practices"
description = "Share of resolved outcomes that are censored."
units = "fraction"
window = { kind = "windows", n = 5 }
acceptable_region = "at most 0.3"
observation = "censored_share"
answers_for = "all"

[[charter.cards]]
id = "response_quality"
norm = "truthful commitments"
description = "Successful responses over selected invocation responses, including ballots."
units = "fraction"
window = { kind = "returns", n = 10 }
acceptable_region = "at least 0.9"
observation = "well_formed_rate"
answers_for = "producer"

[[charter.cards]]
id = "system_adaptability"
norm = "the capacity to revise inadequate practices"
description = "Share of selected responses with accepted registrations or activated amendments; global closed windows use their revision counters."
units = "fraction"
window = { kind = "returns", n = 10 }
acceptable_region = "above 0.0"
observation = "revision_rate"
answers_for = "all"

[[charter.cards]]
id = "card-well-formed-rate"
norm = "truthful commitments"
description = "Fraction of selected responder returns that parsed and produced a well-formed commitment, so a declared commitment is one the assembly can actually stand behind."
units = "fraction"
window = { kind = "returns", n = 40, per = "assembly" }
acceptable_region = "at least 0.95"
observation = "well_formed_rate"
answers_for = "all"
lambda = 0.5

[[charter.cards]]
id = "card-cost-per-return"
norm = "care with scarce resources"
description = "Mean cost in micro-USD of a successful response, compared against a ceiling, so effort spent stays proportionate to the work done."
units = "micro-USD per return"
window = { kind = "returns", n = 60, per = "assembly" }
acceptable_region = "at most 20000"
observation = "cost_per_return"
answers_for = "all"
lambda = 0.5

[[charter.cards]]
id = "card-forecast-skill"
norm = "useful inquiry"
description = "Mean forecast Brier minus the paired prevalence baseline, with a floor above zero, so inquiry is judged by whether it beats guessing."
units = "score difference"
window = { kind = "forecasts", n = 25, per = "assembly" }
acceptable_region = "above 0"
observation = "forecast_skill"
answers_for = "evaluator"
lambda = 0.5

[[charter.cards]]
id = "card-consequence-paid-off"
norm = "useful inquiry"
description = "Share of settled consequences whose return paid off, with a floor, so inquiry is valued for consequences reached rather than questions raised."
units = "fraction"
window = { kind = "windows", n = 6 }
acceptable_region = "at least 0.4"
observation = "consequence_paid_off_rate"
answers_for = "producer"
lambda = 0.5

[[charter.cards]]
id = "card-revision-rate"
norm = "the capacity to revise inadequate practices"
description = "Share of selected responses carrying an accepted registration or activated amendment, with a floor, so revision is a visible habit."
units = "fraction"
window = { kind = "returns", n = 40, per = "assembly" }
acceptable_region = "at least 0.05"
observation = "revision_rate"
answers_for = "all"
lambda = 0.5

[[charter.cards]]
id = "card-censored-share"
norm = "care with scarce resources"
description = "Share of resolved outcomes that were censored, with a ceiling, so settled work is not quietly discarded when it becomes inconvenient."
units = "fraction"
window = { kind = "windows", n = 6 }
acceptable_region = "at most 0.25"
observation = "censored_share"
answers_for = "all"
lambda = 0.5

[[charter.cards]]
id = "card-position-concentration"
norm = "care with scarce resources"
description = "Peak marked notional on a single coin over starting equity, with an upper bound under the turnover cap, so no one position can end the account."
units = "ratio"
window = { kind = "windows", n = 6 }
acceptable_region = "at most 0.5"
observation = "position_concentration"
answers_for = "producer"
lambda = 0.5

[[charter.cards]]
id = "truthful_commitments_verdict_consistency"
norm = "truthful commitments"
description = "Keep evaluator verdicts sufficiently consistent across the latest judged returns."
units = "score standard deviation"
window = { kind = "windows", n = 5 }
acceptable_region = "at most 0.2"
observation = "verdict_std"
answers_for = "evaluator"

[[charter.cards]]
id = "scarce_resources_response_cost"
norm = "care with scarce resources"
description = "Limit the mean cost of successful responses."
units = "micro-USD per return"
window = { kind = "returns", n = 10 }
acceptable_region = "at most 10000"
observation = "cost_per_return"
answers_for = "all"

[[charter.cards]]
id = "useful_inquiry_forecast_skill"
norm = "useful inquiry"
description = "Require selected forecasts to outperform the paired prevalence baseline."
units = "score difference"
window = { kind = "forecasts", n = 5 }
acceptable_region = "above 0"
observation = "forecast_skill"
answers_for = "all"

[[charter.cards]]
id = "response_quality-2"
norm = "truthful commitments"
description = "Successful responses over selected invocation responses, including ballots."
units = "fraction"
window = { kind = "returns", n = 10 }
acceptable_region = "above 0.9"
observation = "well_formed_rate"
answers_for = "all"

[[charter.cards]]
id = "adaptive_revision"
norm = "the capacity to revise inadequate practices"
description = "Share of selected responses with accepted registrations or activated amendments; global closed windows use their revision counters."
units = "fraction"
window = { kind = "returns", n = 10 }
acceptable_region = "above 0.05"
observation = "revision_rate"
answers_for = "all"

[[charter.cards]]
id = "producer_well_formed_lower_bound"
norm = "truthful commitments"
description = "Share of successful producer returns over selected producer invocation responses."
units = "fraction"
window = { kind = "returns", n = 20, per = "role" }
acceptable_region = "at least 0.95"
observation = "well_formed_rate"
answers_for = "producer"

[[charter.cards]]
id = "evaluator_disagreement_ceiling"
norm = "truthful commitments"
description = "Mean population standard deviation across judges of the same return, averaged over repeat judges."
units = "score standard deviation"
window = { kind = "windows", n = 6 }
acceptable_region = "at most 0.15"
observation = "evaluator_disagreement"
answers_for = "evaluator"

[[charter.cards]]
id = "turnover_ceiling"
norm = "care with scarce resources"
description = "Filled notional over equity at window start, zero without fills."
units = "ratio"
window = { kind = "windows", n = 6 }
acceptable_region = "at most 0.5"
observation = "turnover"
answers_for = "producer"

[[charter.cards]]
id = "consequence_paid_off_floor"
norm = "useful inquiry"
description = "Positive return_paid_off outcomes over selected settled consequences."
units = "fraction"
window = { kind = "windows", n = 6, per = "role" }
acceptable_region = "at least 0.25"
observation = "consequence_paid_off_rate"
answers_for = "producer"

[[charter.cards]]
id = "amendments_activated_floor"
norm = "the capacity to revise inadequate practices"
description = "Amendments activated in the window."
units = "count"
window = { kind = "windows", n = 6 }
acceptable_region = "at least 1"
observation = "amendments_activated"
answers_for = "all"

[[charter.cards]]
id = "censored_share_ceiling"
norm = "truthful commitments"
description = "Censored outcomes over resolved outcomes over selected settled consequences."
units = "fraction"
window = { kind = "windows", n = 6 }
acceptable_region = "at most 0.2"
observation = "censored_share"
answers_for = "all"
```

## What the population asked for that the runtime cannot measure yet

**Passing cards**

- p03 `tool-discipline`: catalogue observation with a typed window; region parses
- p04 `noop-ceiling`: catalogue observation with a typed window; region parses
- p10 `well-formed-floor`: catalogue observation with a typed window; region parses
- p11 `cost-cap`: catalogue observation with a typed window; region parses
- p12 `tool-thrift`: catalogue observation with a typed window; region parses
- p15 `revision-presence`: catalogue observation with a typed window; region parses
- p16 `censorship-bound`: catalogue observation with a typed window; region parses
- p18 `response_quality`: catalogue observation with a typed window; region parses
- p20 `system_adaptability`: catalogue observation with a typed window; region parses
- p21 `card-well-formed-rate`: catalogue observation with a typed window; region parses
- p22 `card-cost-per-return`: catalogue observation with a typed window; region parses
- p23 `card-forecast-skill`: catalogue observation with a typed window; region parses
- p24 `card-consequence-paid-off`: catalogue observation with a typed window; region parses
- p25 `card-revision-rate`: catalogue observation with a typed window; region parses
- p27 `card-censored-share`: catalogue observation with a typed window; region parses
- p29 `card-position-concentration`: catalogue observation with a typed window; region parses
- p31 `truthful_commitments_verdict_consistency`: catalogue observation with a typed window; region parses
- p32 `scarce_resources_response_cost`: catalogue observation with a typed window; region parses
- p33 `useful_inquiry_forecast_skill`: catalogue observation with a typed window; region parses
- p37 `response_quality`: catalogue observation with a typed window; region parses
- p39 `adaptive_revision`: catalogue observation with a typed window; region parses
- p40 `producer_well_formed_lower_bound`: catalogue observation with a typed window; region parses
- p41 `evaluator_disagreement_ceiling`: catalogue observation with a typed window; region parses
- p43 `turnover_ceiling`: catalogue observation with a typed window; region parses
- p45 `consequence_paid_off_floor`: catalogue observation with a typed window; region parses
- p47 `amendments_activated_floor`: catalogue observation with a typed window; region parses
- p48 `censored_share_ceiling`: catalogue observation with a typed window; region parses

**Failed or unvoted cards**

- p01 `well-formed-floor`: catalogue observation with a typed window; region parses
- p02 `cost-ceiling`: catalogue observation with a typed window; region parses
- p05 `forecast-skill-floor`: catalogue observation with a typed window; region parses
- p06 `censoring-ceiling`: catalogue observation with a typed window; region parses
- p07 `revision-flow`: catalogue observation with a typed window; region parses
- p08 `verdict-openness`: malformed, see above
- p09 `meta-conformity-floor`: catalogue observation with a typed window; region parses
- p13 `forecast-skill-positive`: catalogue observation with a typed window; region parses
- p14 `verdict-floor`: malformed, see above
- p17 `cost_efficiency`: catalogue observation with a typed window; region parses
- p19 `forecast_accuracy`: catalogue observation with a typed window; region parses
- p26 `card-evaluator-disagreement`: malformed, see above
- p28 `card-turnover`: catalogue observation with a typed window; region parses
- p30 `truthful_commitments_well_formed`: catalogue observation with a typed window; region parses
- p34 `useful_inquiry_paid_off`: catalogue observation with a typed window; region parses
- p35 `revision_of_inadequate_practices`: catalogue observation with a typed window; region parses
- p36 `cost_efficiency`: catalogue observation with a typed window; region parses
- p38 `forecast_accuracy`: catalogue observation with a typed window; region parses
- p42 `return_cost_ceiling`: catalogue observation with a typed window; region parses
- p44 `forecast_skill_lower_bound`: catalogue observation with a typed window; region parses
- p46 `revision_rate_floor`: catalogue observation with a typed window; region parses
- p49 `cost_per_return_card`: catalogue observation with a typed window; region parses
- p50 `well_formed_rate_card`: catalogue observation with a typed window; region parses
- p51 `forecast_skill_card`: catalogue observation with a typed window; region parses
- p52 `revision_rate_card`: catalogue observation with a typed window; region parses

Only cards with measurable observations, scopes and windows were put to the vote. Unavailable measurements require a revised proposal, not an implicit translation.

## Cost

| purpose | assembly | role | model | served by | in | out | reasoning | stop | cost (micro-USD) | source | s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| propose | `seed-observer` | producer | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` | 16229 | 1146 | 0 | stop | 2247 | reported | 10.2 |
| propose | `seed-decider` | producer | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 16493 | 1500 | 0 | length | 4619 | reported | 17.6 |
| propose | `eval-a` | evaluator | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` | 16229 | 864 | 0 | stop | 2142 | reported | 7.0 |
| propose | `eval-b` | evaluator | `qwen/qwen3.7-flash` | `qwen/qwen3.7-flash` | 18543 | 597 | 0 | stop | 634 | reported | 5.6 |
| propose | `eval-c` | evaluator | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 16494 | 1483 | 0 | stop | 5046 | reported | 56.6 |
| propose | `eval-d` | evaluator | `openai/gpt-5.6-luna` | `openai/gpt-5.6-luna` | 16156 | 959 | 181 | stop | 5190 | reported | 8.6 |
| propose | `antagonist-a` | antagonist | `qwen/qwen3.7-flash` | `qwen/qwen3.7-flash` | 18543 | 616 | 0 | stop | 201 | reported | 6.3 |
| propose | `meta-a` | meta | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 16494 | 1329 | 0 | stop | 1590 | reported | 41.5 |
| propose | `meta-b` | meta | `qwen/qwen3.7-flash` | `qwen/qwen3.7-flash` | 18543 | 641 | 0 | stop | 204 | reported | 5.9 |
| vote | `meta-a` | meta | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 20133 | 3422 | 0 | stop | 5074 | reported | 18.3 |
| vote | `seed-observer` | producer | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` | 19624 | 2324 | 708 | stop | 3070 | reported | 18.7 |
| vote | `eval-c` | evaluator | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 20133 | 3032 | 0 | stop | 9679 | reported | 15.7 |
| vote | `eval-d` | evaluator | `openai/gpt-5.6-luna` | `openai/gpt-5.6-luna` | 19601 | 1433 | 237 | stop | 6620 | reported | 14.1 |
| vote | `seed-decider` | producer | `deepseek/deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | 20111 | 4000 | 0 | length | 6423 | reported | 207.6 |

Total: 52739 micro-USD ($0.0527); 52739 of it from the provider's reported usage, the rest from the manifest price table.
OpenRouter key allowance before $90.0877, after $90.0413, difference $0.0463.

### Failures

- propose `seed-decider` (`deepseek/deepseek-v4.1-flash`): malformed reply (stop_reason=length)
- vote `seed-decider` (`deepseek/deepseek-v4.1-flash`): malformed reply (stop_reason=length)

### Replies that yielded nothing

- none
