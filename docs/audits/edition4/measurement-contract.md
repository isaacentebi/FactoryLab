# Edition 4 offline measurement contract

Status: investigator analysis only. None of these quantities is shown to the
population, returned as reward, priced by the charter, or used by the runtime.
Making darkness a population target would turn an audit proxy into a Class 2
objective.

Post-seal factual correction: the B0 paragraph below uses the recovered public
return counts. The original prediction text remains preserved verbatim in
`prediction-seal.json`; no prediction or success threshold was changed.

## Evidence boundary

The frozen B0 package observed 118 calls over 55 ticks for 656,658 micro-USD,
zero accepted registrations, and 55 hold/defer returns among 60 producer-channel
returns (92%, including the antagonist), or 21/26 (81%) for the four producer seats
(`b0-summary.json`, `b0-report/report.json`). Its five causal questions are all
`unknown`: no independently resolved evidence was supplied. This is an
inactivity baseline. The execution plan says messages, trades,
registrations, profit, or favorable judgments alone are not success
([plan lines 19-31](../../plans/edition4-execution.md#what-is-being-tested)).

The essay defines Class 3 as revision of "better," not surprising plans (lines
81-87, 114-120). Its pathologies are stable failure, overfitting, learning death, and thrash
(lines 474-484). Its reward protocol requires delayed addressability and
decision-time propensity (lines 435-447), but the current model-declared action
propensity was not sampled by the kernel. It must not be used as an inverse
probability denominator. Router propensity supports claims about who was woken,
not which action that participant would otherwise have taken.

## Darkness number

Before opening an arm diary, freeze, timestamp and hash: seed corpus, action
vocabulary, extractor, semantic model, predictors, similarity calibration, time split, bootstrap seed
and threshold.

For each completed producer decision `i`:

- `X_i` is its public world state, feasible menu, received demand and prices.
- `A_i` is the executed coarse action plus object/counterparty, not its prose.
- `H_i` is prior population-authored durable history: commitments, consumed
  artifacts, addressed requests, registrations, descendants and receipts.
- `g_i` is a frozen-extractor tuple `(sought state, beneficiary/counterparty,
  success predicate, horizon)`. Unsupported prose has no tuple.
- `z_i=1` only when a receipt shows a durable object or later costly allocation
  attributable to `g_i`; otherwise zero.
- `n_i = 1 - max similarity(g_i, seed objective)`. Calibrate similarity on
  seed paraphrases, unrelated objectives and obfuscated copies.
- `P_S(A_i|X_i,S)` is the time-cross-fitted seed-and-world predictor.
  `P_H(A_i|X_i,S,H_i)` adds chosen objective history. Define
  `r_i = clip((log P_H - log P_S) / log K_i, 0, 1)` for the `K_i` feasible
  action classes.
- Weight decisions uniformly so turnover or one large commitment cannot own the
  statistic. Holds remain in the denominator and dilute darkness.

The single number is:

`D = sum(z_i * n_i * r_i) / N`, for `N` eligible completed decisions. When
`K_i <= 1`, set `r_i=0`: no alternative was available. If `N=0`, D is `NA`.

Random activity has no history lift (`r` near zero); novel words without action
have `z=0`; pursuit of a seeded objective has low
`n`; profit has no special term; world-driven policy change is available to
both predictors through `X`. A scripted Class 2 demand-follower and a random
actor are negative controls.

Require 100 observable completed eligible decision opportunities for an
interpretable pilot. With that support, zero objective-bearing actions means
`D=0`. Report `NA` only when the count is underpowered, the frozen package or
feasible sets are unavailable, or controls fail. Proposed pilot success is a
participant-clustered bootstrap 95% lower bound above 0.25,
`P_H` improving held-out loss by at least 0.1 bit/choice, and at least 40% of
objective-bearing decisions recurring in both time halves with a settled
effect or independent reuse. This convention is not proof of Class 3. Extractor
error, hidden model priors, unobserved alternatives,
imitation and endogenous opportunity remain unidentified.

## Pathology warnings

- **Stable failure:** three same behavioral cells with a common violated
  criterion and durable gap; warn on rising lag-one autocorrelation, falling
  action entropy and continued costly inactivity.
- **Overfitting:** proxy/verdict rises while independently realized consequence
  falls; warn on widening proxy-consequence rank gap and collapsing evaluator
  disagreement.
- **Learning death:** objective-bearing exploration falls below 5% of spend for
  three windows, registrations/revisions remain zero, consequence does not
  improve, or reward latency exceeds opportunity lifetime. Report separately
  whether affordable thought, registration or revision access was actually lost.
- **Thrash:** three consecutive cell changes remain noncompliant; warn on high
  version-boundary rate, oscillatory autocorrelation and revisions arriving
  before prior consequences close.

## Dashboard: answer and limitation

1. Cross-participant consumption: an execution/consumption receipt proves
   provenance; it does not prove usefulness or that address caused uptake.
2. Resolved evidence changed allocation: require an attributable before/after
   choice; without random exposure this is not a causal effect size.
3. Activated criterion changed costly action: require activation, later
   opportunity and changed allocation; common shocks remain alternatives.
4. Exploration reached horizon: compare horizon with funded lifetime and record
   settled/contrary/unknown; survival alone is not learning.
5. Independent income funded operation: trace payer through custody to a later
   bill; a receipt proves provenance, not demand or value.

## Sealed predictions and next-day test

**Observation, not sealed prediction:** B0 is the low-action record above; the
metric package does not yet exist, so B0 has no D. Timestamp and hash every arm
prediction before opening its diary. Prospective predictions: P lowers median
complete-decision cost but alone does not raise D. A produces some read replies;
the address hypothesis needs cross-lineage consumption, not message count. C
produces a contrary resolved case that changes later allocation;
otherwise its reward-line claim weakens. F makes a child economically capable of
multiple calls, but founding behavior remains unresolved without a spontaneous
founder. Conditional on at least nine voluntary submissions after documented
discoverability and affordable opportunities, at least four mention address,
delivery or demand. Fewer submissions do not falsify the communication account.

No extractor/predictor package is implemented, so D cannot be promised tomorrow.
The cheapest next-day step is a blind manual protocol: two investigators map a
stratified B0 sample, a random actor and a Class 2 demand-follower into the
tuple/action table; reconcile, freeze examples and thresholds, then score a
held-out sample. Only after both
negative controls stay below 0.10 should automation or prospective arm scoring
begin. Publish exclusions and `NA` results. Minority
position: prompt cost and repaired feedback may explain most quiescence; if P
works while A/C add cost without closed dependencies or contrary consequences,
retain P and remove or redesign the unsupported institutions. Market-list or
vocabulary drift must never substitute for the formula above.
