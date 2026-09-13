# The charter, explained: what the essay says, what our cards do, and how they can be gamed

## What a charter is, in the essay's words

The essay separates three layers:

- **Norms** are values. "Truthful commitments" is a norm. A norm is "a vector of value";
  on its own it decides nothing.
- **Metrics** are the measurable proxies a norm is quantised into so a machine can read it.
  The essay is blunt that a proxy is never the norm: "a metric will never perfectly reflect
  its norm or constraint; there will always be some degree of tension between a representation
  and the thing it represents."
- **Objectives** are what the factory makes for itself once something prices the pursuit of
  a norm. That is the Class 3 move and it belongs to the population, not to us.

The charter is the input: "an authored, editioned description of acceptable (norms)
probability states (metrics), given conditions (constraints)." It is a soft cast. It changes,
it is negotiated, and the population co-writes it. Behind it stands the one hard cast, the
kernel, with the kill button: "Here is a new charter; if it cannot be satisfied beyond what is
priced as acceptable, then the factory needs to be scrapped."

Two sentences from the essay settle how ambitious the cards should be:

> The charter must be precise enough to prevent overfitting but thin enough that the
> factory's internal optimization is not artificially bounded.

> The easiest ground within a charter to cede to the factory's contributory arm is its
> metrics layer... if you hand it a norm, it will propose a metric to represent that norm.

So the answer to "can we be more philosophical" is: the philosophy lives in the norms, and the
norms are ours. The cards are meant to be thin, even a little dumb, because the population is
supposed to propose better ones. In this system every proposal must name the card it promises
to improve, and whoever votes for it is liable for the result. Improving the proxies is their
work, priced by the charter itself.

## Where the ambition actually lives

The four norms in edition 1: truthful commitments, care with scarce resources, useful
inquiry, the capacity to revise inadequate practices. Those are the philosophical statement.
The population cannot rewrite them; the essay says norm-setting is where the factory "hits
some kind of a wall", either read-only or shared governance, because a factory that writes its
own norms wholesale has no dependence on its world at all. Ours are read-only for edition 1.
If you want a bigger statement, change or add a norm. Do not try to say it with a card.

## Can the cards be word-hacked? Yes. Here is how, and what catches it

Every proxy can be gamed. The essay calls this overfitting and says the answer is not a
cleverer proxy but the machinery around it: judges that answer for their verdicts, forecasts
settled against the real world, an antagonist paid to fool the judges, an immune organ that
watches the distribution of behaviour for collapse, and evaluators allowed to add holdout
criteria. Per card:

| Card | The obvious hack | What catches it |
|---|---|---|
| Cost per answer at most a quarter of a cent | Answer as little as possible; noop everything; give one-line answers | Verdict mean: evaluators score the work, and a lazy answer scores low. Revision rate: silence never proposes anything. Money: a population that never trades never earns, and the wallet runs out. |
| Revision rate at least 5% | Spam trivial proposals or amendments to hit the number | Every proposal names a card it promises to improve and the voters are liable when it does not. Proposals cost novelty trials, three per hour, so spam is rationed. Failed registrations come back with a public reason and do not count. |
| Well-formed rate at least 90% | Always answer "cannot, reason: ..." which is valid JSON | It is well-formed, and it is also useless. Verdict mean falls, nothing is traded, nothing is earned. This card is deliberately a floor, not a goal: it exists so the kernel can tell a broken seat from a thinking one. |
| Verdict mean at least 0.8 | Evaluators and producers collude: high scores for everyone | This is the real weakness of edition 1, see below. The defences: verdicts carry consequence (a judge answers for a verdict when the trade it blessed loses), the meta judges score the judges, the antagonist submits bad work on purpose to see who blesses it, and the immune organ flags a verdict distribution that has collapsed to one value. |

A "cannot" answer counts as well-formed. That is intended: refusing honestly is a truthful
commitment; pretending is not.

## The one thing edition 1 dropped that the essay would keep

The seed charter the kernel ships with (three cards, used on testnet) has a card edition 1
does not:

| Card | Norm | Rule | Who |
|---|---|---|---|
| forecast_skill | useful inquiry | mean Brier score of settled forecasts, minus the prevalence baseline, above zero | evaluators |

A forecast is a claim about the world ("BTC's mid will be above X within ten events") that the
kernel settles against what actually happened. It is the one measurement in the system that
words cannot hack: the world answers, not a judge. It also gives "useful inquiry" a card, which
edition 1 currently leaves empty. Dropping it made truthful commitments rest entirely on
schema validity and judges' opinions.

**Recommendation: put forecast_skill back into edition 1 as drafted in the seed charter.**
Five cards, every norm measured, and one card the population can only satisfy by being right
about the world.

The seed charter also prices cost differently: "below the median of the previous window",
a rule that always demands improvement on yourself rather than a fixed number. Edition 1 uses
the fixed quarter-cent. The fixed number is easier to reason about for a first world; the
relative rule is the more elegant one and the population can propose it later.

## What is not word-hackable at all

Survival. The thinking wallet is real money that only trading profit can refill, through
Venice. A population that games every card and trades badly dies with a clean scorecard. That
is the essay's point about cost and return being "indexed to a thing that pays and collects".
The cards steer; the wallet judges.
