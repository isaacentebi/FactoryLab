# The charter, explained: what the essay says, what our cards do, and how they can be gamed

This is written against the edition 2 draft, `docs/charter/edition2-draft.toml`: four norms,
eight cards, not yet ratified (the testnet committee vote and `scripts/ratify_charter.py` add
the hashes). Where it says what the code does, it means the code on `main`.

## What a charter is, in the essay's words

The essay separates three layers:

- **Norms** are values. "Epistemic integrity" is a norm. A norm is "a vector of value"; on its
  own it decides nothing.
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

So the philosophy lives in the norms, and the norms are ours. The cards are meant to be thin,
even a little dumb, because the population is supposed to propose better ones. Every proposal
must name the card it promises to improve, and whoever votes for it is liable for the result.
Improving the proxies is their work, priced by the charter itself.

## Where the ambition lives: the four norms

The norms in edition 2 are the reviewer's, in its words:

- **Consequential usefulness.** Create things or changes that others have reason to value;
  uptake by an independent counterparty is evidence, internal applause is a hypothesis.
- **Epistemic integrity.** Make commitments answerable to evidence and preserve the ability to
  discover that they were wrong; a changed criterion does not rewrite what was promised.
- **Durable agency.** Steward the resources and capabilities that make future worthwhile choices
  possible; spending for an enduring capability can be good stewardship, maintaining a dead
  institution is not.
- **Bounded reciprocity.** Do not finance the factory's advantage by imposing unconsented costs
  on outsiders.

They are read-only for the edition. The essay says norm-setting is where the factory "hits some
kind of a wall", either read-only or shared governance, because a factory that writes its own
norms wholesale has no dependence on its world at all. If you want a bigger statement, change
or add a norm. Do not try to say it with a card. Bounded reciprocity has no card at genesis; the
population may propose one.

## What changed from edition 1, and why

Edition 1 had thirteen cards. Five of them were quotas: a noop ceiling, a revision-presence
floor, an activated-amendments floor, a verdict-consistency band and an evaluator-disagreement
ceiling. Each turned an instrument into an obligation. The cold audit traced the two most likely
deaths of the world, institutional churn that burns the budget and cheap formatted consensus,
to exactly those five. They are gone. Two measurements were also corrected: the cost card now
counts every attempt, failed ones included, so an expensive failure cannot hide inside the tenth
of malformed returns the well-formed floor tolerates; and tool discipline is the mean per
return, which is what its prose always said.

## The eight cards, and how each can be gamed

Every proxy can be gamed. The essay calls this overfitting and says the answer is not a cleverer
proxy but the machinery around it: judges that answer for their verdicts, forecasts settled
against the real world, an antagonist paid to fool the judges, an immune organ that watches the
distribution of behaviour for collapse, and a wallet that only the world refills. Per card:

| Card | Norm | Rule (who answers) | The obvious hack | What catches it |
|---|---|---|---|---|
| `tool-discipline` | durable agency | mean attempted tool calls per return at most 2, over each assembly's last 10 returns (producers) | Push the calls into child requests, or into a program seat, and keep your own return clean. | A child is its own return and its own cost, charged against the parent's ceiling; a program's calls are counted on the program's returns; the cost cap prices whatever the tree spent. |
| `well-formed-floor` | epistemic integrity | at least 90% of a role's last 10 returns parse (everyone) | Always answer "cannot, reason: ..." which is valid JSON. | It is well-formed and useless. Nothing pays off, no forecast is right, and the wallet drains. This card is a floor so the kernel can tell a broken seat from a thinking one, not a goal. |
| `cost-cap` | durable agency | mean cost per attempt at most 5,000 micro-USD (half a cent) over a role's last 10 returns, failed ones and storage rent included (everyone) | Route to the cheapest seat and say nothing; or let expensive calls fail so they were not "returns". | Failures now count in the mean and own their share of the blame. Rent counts. A cheap silence still has to pay off under the consequence card, and the judges score it. |
| `censorship-bound` | epistemic integrity | at most 30% of resolved outcomes censored, over 5 windows (everyone) | Make no commitment that could be left unresolved; forecast nothing. | Forecast skill needs 25 resolved forecasts per evaluator to be measured at all, and payoff standing, which the router weights, is built only from settled consequences; a judge that never commits earns none. |
| `card-forecast-skill` | epistemic integrity | mean Brier minus the paired prevalence baseline above zero, over an evaluator's last 25 forecasts, price 0.5 (evaluators) | Forecast only the obvious; or, as a judge, repeat the blame share every return carries. | The baseline is paired: it is the base rate as it stood before each outcome, and it learns the same fractional target the judge is scored on, so a constant judge shows no excess skill. The world settles the forecast, not a colleague. |
| `card-consequence-paid-off` | consequential usefulness | at least 40% of settled consequences paid off, over 6 windows, price 0.5 (producers) | Many tiny trades that each net a cent; open and close the same lot to be credited twice. | P&L is credited once and split by notional between opener and closer, net of fees and funding. Storage rent a decision owes is carried into its outcome, so a return cannot pay off on a margin its notes consumed. Turnover and concentration cap the size of the game. |
| `card-position-concentration` | durable agency | peak notional on one coin over starting equity at most 0.5, over 6 windows, price 0.5 (producers) | Spread the same bet over several coins, or over perp and spot. | Turnover is capped on filled notional across everything. A liquidation realises the whole observed loss and can overshoot zero; the balance floor is death. |
| `turnover_ceiling` | durable agency | filled notional over equity at window start at most 0.5, over 6 windows, zero without fills (producers) | Never trade: zero fills is inside the region. | Then nothing is earned, and every window costs cognition and rent. The consequence card wants settled consequences that paid off; a silent producer has none. |

A "cannot" answer counts as well-formed. That is intended: refusing honestly is epistemic
integrity; pretending is not.

None of these catches is airtight, and they are not meant to be. What they do is make the
cheap hack cost more than the honest move under the cards that remain, and make the residue
show up in the ledger, where the immune organ reads the cells (which cards are violated, how
many registrations and revisions there were) and flags thrash, stable failure and learning
death.

## How the population fixes a bad proxy

Two routes, both priced. An **amendment** adds, replaces or removes cards, names the card it
predicts will move and in which direction, and goes to a sortition committee whose yes votes
are graded on whether the region held after activation. A **challenge** is for the case an
amendment handles badly: the card being challenged is the one that would price the trial. A
challenge names a current card, gives evidence, offers a replacement measurement, and buys one
novelty trial. Both the incumbent and the replacement are then measured, frozen, for the
declared number of windows, both series are in the ledger, the incumbent keeps pricing the
live charter throughout, and at the end the ordinary committee votes on adopting the
replacement with the evidence and both series in front of it. During the trial a proposal
may name the challenge itself as the card it promises to move, and is then graded on the
replacement, not on the incumbent. That is how the F4 and F5 corrections above would have
been made from inside had the population had the route.

## What is not gameable

**The wallet.** One conserved integer of micro-USD. Every model call, program call, tool
call, ballot, registration trial, connector read, note byte and rent charge leaves it, and the
only things that enter are the drip the manifest declared and what the venue settled: fills,
funding and realised P&L. The locked backing is already booked; a release only reclassifies
it. Service income lands as USDC at the reserve address on chain and shows in the reserve pot
when it is next observed; nothing credits the balance for it. A balance at or below the floor
is death, at launch as in flight, and a liquidation can overshoot it. Nothing the population
writes changes a number in it.

**The release schedule.** The endowment is booked in the balance but locked; tranches unlock
at fixed offsets from the ledgered launch, once each, and no amendment reaches the schedule.
When the unlocked part cannot buy the cheapest seat and a tranche is still due, the world
goes dormant: no seat is woken for an event, maintenance continues, and it wakes when the
tranche lands. Dormancy is a pause, never a refill, and never a termination reason. When the
last tranche is gone the next shortfall is death.

**The on-chain evidence.** A Venice purchase confirms only on the canonical USDC debit for its
nonce; the credit balance is advisory and can never refuse or invent a payment. A sold
service call is income only when the facilitator returns an explicit settlement, naming the
recovered payer and a transaction, of exactly the quoted amount to the reserve address; the
receipt is written before the program runs. Fills are what the venue reports, classified
against its own listing.

**The code.** The diary carries the digest of the release that launched it (commit, lock
file, package tree), and a resume under a different release is refused. A witness file
outside the diary records launch, dormancy, kill and every refused resume with that digest
and the ledger's byte hash.

## Where the money comes from, said plainly

The pots view keeps three lines beside the balances, and the wake shows them separately:

- `subsidy_micro` is the architect's compute credit, observed once at the start. It is a
  gift, and it is not repeated: the covenant is no refill.
- `converted_from_principal_micro` is Venice credit bought with trading capital through the
  treasury. A top-up is **conversion, not profit**: the same money moved from one pot to
  another, minus fees. An earlier version of this document said the thinking wallet could
  only be refilled by trading profit; that was false then and is false now.
- `earned_micro` is x402 income from service calls outsiders paid for. It is **the only
  earned line**. Trading gains show as venue equity and realised P&L, not here.

A population that games every card, converts its principal into thinking, sells nothing and
trades badly dies with a clean scorecard. That is the essay's point about cost and return
being "indexed to a thing that pays and collects". The cards steer; the wallet judges.
