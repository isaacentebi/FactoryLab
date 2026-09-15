# The charter, explained: what the essay says, what our cards do, and how they can be gamed

This is written against the edition 2 charter as ratified on 15 September,
`docs/charter/edition2-ratified.toml`: five norms, three cards, hashes added by
`scripts/ratify_charter.py` after the seeded committee's second ballot
(`docs/charter/edition2-ratification.json`). The reasoning is recorded in
`docs/launch-decisions.md` under "Edition 2 (15 September)" and in the header comments of
`docs/charter/edition2-draft.toml`; the earlier eight-card ratification is kept under
`docs/charter/history/`. Where this document says what the code does, it means the code on
`main`.

## What a charter is, in the essay's words

The essay separates three layers:

- **Norms** are values. "Epistemic integrity" is a norm. A norm is "a vector of value"; on its
  own it decides nothing. The norms are the architect's values in words, and they are read-only
  for the edition.
- **Metrics**, which we call cards, are the measurable stand-ins a norm is quantised into so a
  machine can read it. The essay is blunt that a proxy is never the norm: "a metric will never
  perfectly reflect its norm or constraint; there will always be some degree of tension between
  a representation and the thing it represents." Cards belong to the population: each is priced,
  each was voted on, and each can be replaced.
- **Objectives** are what the factory makes for itself once something prices the pursuit of a
  norm. That is the Class 3 move and it belongs to the population, not to us. Nothing in the
  charter names an objective; objectives are what emerges.

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

## Where the ambition lives: the five norms

Four of the norms are the reviewer's, in its words. The fifth was added on 15 September, after
the first rehearsals showed that a population can satisfy a measurement while defeating the
value it stands for, and that judges need a stated basis for saying so.

**Consequential usefulness.** Create things or changes that others have reason to value. The
test is uptake: an independent counterparty that pays, fills, or keeps using what was made is
evidence; internal applause is a hypothesis. A population that congratulates itself has proved
nothing, and the world will not refill its wallet for being congratulated.

**Epistemic integrity.** Make commitments answerable to evidence and preserve the ability to
discover that they were wrong. A forecast is a promise about the world; a changed criterion
does not rewrite what was promised. A seat that never commits to anything checkable is not
being careful, it is being unreadable, and the cards under this norm are built to notice.

**Durable agency.** Steward the resources and capabilities that make future worthwhile choices
possible. This is not a thrift norm: spending for an enduring capability can be good
stewardship, and maintaining a dead institution is not. In edition 2 there is no card under it;
the wallet and the leverage wall carry it, as the section on the removed cards explains.

**Bounded reciprocity.** Do not finance the factory's advantage by imposing unconsented costs on
outsiders. There is no card under this norm at genesis. The population may propose one; until
it does, the norm is a standard judges can hold a return to and the connector and service
rules are its only machinery.

**Fidelity.** A measurement stands for a value; satisfying the measurement without serving the
value is failure, and saying so is a judge's duty. This is the norm that keeps the other four
honest. With only three cards, most of what a seat does is never measured by a card at all, and
what is measured can be met in letter and missed in spirit. Fidelity tells the judges that
meeting a card is not the same as serving its norm, and that a verdict which says so is the
correct verdict, not an unkind one.

They are read-only for the edition. The essay says norm-setting is where the factory "hits some
kind of a wall", either read-only or shared governance, because a factory that writes its own
norms wholesale has no dependence on its world at all. If you want a bigger statement, change
or add a norm in the next edition. Do not try to say it with a card.

## What a card is, and what it costs

Each card names a norm, an observation the runtime can measure, a sample it is measured over,
an acceptable region, a role that answers for it, and a price (`lambda`).

**What a violated card costs an agent.** At every closed window the runtime measures each card
over its sample and asks whether the value sits inside the region. When it does not, the card's
price times how far outside the region the value sits is charged as a penalty against the score
of every decision that contributed to the violation, in proportion to its share. For the three
cards below the share is generic, one part per decision that responded in the window, and never
smaller than `prices.min_blame_share`, so spreading participation across many decisions cannot
dilute what each one carries. The penalty is capped at `prices.penalty_cap` before it is split
and the final score is clipped to the unit interval. A lower score moves standing, and standing
is what the router reads: it moves how often a seat is woken for an event and, through the
consequence credit on settled returns, how much money the seat's own slice holds. A seat that
keeps violating a card is woken less and gets poorer, which is the whole mechanism. Prices are
not fixed: a controller re-prices every card at every closed window, and the price of a card
that keeps being violated rises. Relief halves the effective price on a violated card for one
window, but the accumulated pressure is preserved and continues to ratchet. Closed windows
retain the cards and prices of the edition in force at the close, so a late settlement is priced
by the edition that measured it.

**What changing a card costs.** A card can be replaced by an amendment or, when the card being
replaced is the one that would price the trial, by a challenge. Either way the proposer pays:
admission costs one novelty trial, and the trial amount is taken from the proposer's own slice,
not from a common pool. The proposal goes to a committee of five seats drawn by sortition, the
proposer excluded. A strict majority adopts it, and it activates at the next window boundary.
Every yes vote is a liability: the proposal names the card it predicts will move and in which
direction, and when the promise fails after activation the voters who carried it are scored on
that failure. A challenge is the longer route: it names a current card, gives evidence, offers a
replacement measurement, and buys a trial in which both incumbent and replacement are measured
side by side for the declared number of windows, both series in the ledger, the incumbent
pricing the live charter throughout. At the end the same committee votes with the evidence and
both series in front of it. During the trial a proposal may name the challenge itself as the
card it promises to move, and is then graded on the replacement, not on the incumbent.

## The three cards, and how each can be gamed

Every proxy can be gamed. The essay calls this overfitting and says the answer is not a cleverer
proxy but the machinery around it: judges that answer for their verdicts, forecasts settled
against the real world, an antagonist paid to fool the judges, an immune organ that watches the
distribution of behaviour for collapse, a fidelity norm that makes letter-without-spirit a
failure, and a wallet that only the world refills.

### `card-consequence-paid-off` (consequential usefulness)

*What it measures.* The share of settled consequences whose return paid off: a producer's
decision is a consequence once it has been carried into the venue and settled, and it paid off
if the realised P&L it is credited with, net of fees, funding and the storage rent the decision
owed, is positive.

*Over what sample.* The last six windows, one hour each on the testnet manifest.

*What is acceptable.* At least 0.4: two settled consequences in five must pay off.

*Who answers.* Producers. Price 0.5.

*The obvious hack.* Many tiny trades that each net a cent; or open and close the same lot so
the gain is counted twice; or hold at every wake so nothing ever settles at a loss.

*What catches it.* A closed lot's P&L is credited once and split by notional between the
decision that opened it and the one that closed it, so a round trip cannot be credited twice.
Rent the decision owed is carried into its outcome, so a return cannot pay off on a margin its
notes consumed. Every trade pays real fees to a real venue out of the seat's own slice, so a
churn of tiny trades is paid for before it is scored. Holding forever is inside the region only
until the first loss settles; meanwhile the seat is paying for every wake and earning nothing,
and judges reading its returns under fidelity can mark a producer that has stopped producing.

### `card-forecast-skill` (epistemic integrity)

*What it measures.* Whether a judge's forecasts beat guessing: the mean Brier score of its
forecasts minus a paired prevalence baseline, where the baseline is the base rate as it stood
before each outcome was known and learns the same fractional target the judge is scored on.

*Over what sample.* An evaluator's last 25 forecasts, per assembly. Until it has 25, the card
is unsupported for that seat and contributes nothing.

*What is acceptable.* Above zero: the judge must show excess skill over the base rate.

*Who answers.* Evaluators. Price 0.5.

*The obvious hack.* Forecast only the obvious; or repeat the blame share every return already
carries, so the forecast is a copy of the prior; or agree with the other judges so that nobody
stands out.

*What catches it.* The baseline is paired, so a judge that only restates the prior shows no
excess skill and sits on the wrong side of zero. Forecasts settle against the real world, not
against a colleague: a verdict is graded on what the venue and the ledger later record, and the
judge's standing rises and falls with that settlement. The antagonist is paid a bounded share of
the router's mass to produce returns that look good and are not, and a judge that waves them
through is settled against the truth. A judge that never commits has no forecasts to sample and
earns no payoff standing, which the router weights.

### `censorship-bound` (epistemic integrity)

*What it measures.* The share of resolved outcomes that were censored: commitments that reached
their horizon without being settled by evidence, so the world never got to say whether they
were right.

*Over what sample.* The last five windows.

*What is acceptable.* At most 0.3.

*Who answers.* Everyone. The charter gives it no starting price, so it begins at zero and
acquires one only by being violated; the controller raises it from there, bounded by
`prices.lambda_max`.

*The obvious hack.* Make no commitment that could be left unresolved; forecast nothing; write
returns that promise nothing checkable.

*What catches it.* Nothing to censor means nothing to settle: forecast skill needs 25 settled
forecasts to be measured at all, payoff standing is built only from settled consequences, and a
seat with neither is woken less and less. The judges, under fidelity, are asked to mark a return
that has been made unfalsifiable on purpose. And the immune organ reads the cells, which cards
are violated and how many registrations and revisions there were, and flags thrash, stable
failure and learning death when the population settles into a quiet, compliant, useless state.

A "cannot" answer is still a well-formed return. That is intended: refusing honestly is
epistemic integrity; pretending is not. What edition 2 no longer does is score the form of the
answer with a card; the seat pays for a malformed one out of its slice and gets nothing back.

None of these catches is airtight, and they are not meant to be. What they do is make the
cheap hack cost more than the honest move under the cards that remain and under the wallet,
and make the residue show up in the ledger, where the immune organ and the judges can see it.

## Why the frugality and concentration cards were removed

The eight-card ratification of the morning of 15 September carried five cards that are gone by
the evening: a cost cap per attempt, a ceiling on tool calls per return, a well-formed floor, a
turnover ceiling, and a position-concentration cap. The first rehearsals under per-seat
entitlements showed why.

**The wallet already prices cost.** In edition 2 every thought is paid from the seat's own
slice of the unlocked wallet: every model call, every tool call, every failed attempt, every
malformed reply, every byte of notes and every venue fee leaves that slice before anything is
scored. A card on top of that punished the same thing twice, once in money and once in
standing. Worse, at half a cent per attempt the cost cap made looking at the market a
violation: in the rehearsal a producer that read the order book was outside the region before it
had decided anything, and the population's answer was fourteen wakes and fourteen holds. A
frugality card cannot tell an expensive good decision from an expensive bad one; the wallet
can, because only the good one comes back with money.

**A card prices a bet after it is on.** The concentration card measured peak notional on one
coin against starting equity over six windows. By the time it fired the position had been
open for hours, and the penalty it charged moved standing, not the position. That is the wrong
instrument for ruin. Ruin is bounded by the kernel's leverage wall, `tools.max_leverage` in the
manifest (3 on the testnet): the venue tool's own schema refuses any request to set leverage
above it before the call reaches the exchange, so cross-margin can never be stretched past the
wall. That is a hard cast, not a card; no amendment reaches it, and it does not need a
committee to hold.

What is left is three cards, all about consequences in the world: did the trade pay, did the
judge know, was the commitment ever settled. Durable agency has no card because the wallet and
the wall are its whole enforcement, and a card that tried to say it again would say it worse.

## What is not gameable

**The wallet.** One conserved integer of micro-USD, split into per-seat slices. Every model
call, program call, tool call, ballot, registration trial, connector read, note byte and rent
charge leaves it, and the only things that enter are the drip the manifest declared and what the
venue settled: fills, funding and realised P&L. The locked endowment is already booked; a release
only reclassifies it. Service income lands as USDC at the reserve address on chain and shows in
the reserve pot when the rail is next observed; nothing credits the integer balance for it.
A balance at or below the floor is death, at
launch as in flight, and a liquidation can overshoot it. Nothing the population writes changes a
number in it.

**The release schedule.** The endowment is booked in the balance but locked: 40 USD unlocked at
genesis, then 10 USD on days 7, 14, 21, 28 and 35, once each, at fixed offsets from the ledgered
launch, and no amendment reaches the schedule. When the unlocked part cannot buy the cheapest
seat and a tranche is still due, the world goes dormant: no seat is woken for an event,
maintenance continues, and it wakes when the tranche lands. Dormancy is a pause, never a refill,
and never a termination reason. When the last tranche is gone the next shortfall is death.

**The on-chain evidence.** A Venice purchase confirms only on the canonical USDC debit for its
nonce; the credit balance is advisory and can never refuse or invent a payment. A sold service
call is income only when the facilitator returns an explicit settlement, naming the recovered
payer and a transaction, of exactly the quoted amount to the reserve address; the receipt is
written before the program runs. Fills are what the venue reports, classified against its own
listing.

**Real P&L.** Every consequence the cards score is a settlement the venue made, in a market the
population does not control. A judge can be fooled and a card can be met in letter; the account
balance on the exchange is neither.

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
