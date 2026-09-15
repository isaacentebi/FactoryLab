# The charter, explained: what the essay says, what our cards do, and how they can be gamed

This is written against the edition 3 charter, `docs/charter/edition3-draft.toml`: five
norms **with their definitions in the charter object**, and one card. It is a draft; the
hashes are added by `scripts/ratify_charter.py` after the committee's ballot on testnet.
Edition 2 (`docs/charter/edition2-ratified.toml`, five norms, three cards) is kept for
comparison, and the earlier eight-card ratification under `docs/charter/history/`. The
reasoning for edition 3 is in `docs/plans/edition3.md` (C3) and in GPT-6 Pro's architect
reading, `docs/audits/v6/gpt6/architect-review.md` sections 5 and 13. Where this document
says what the code does, it means the code on `main`.

**What changed from edition 2, in one paragraph.** The norms now carry their definitions
where the population can read them, instead of in TOML comments the loader dropped. Two of
the three cards are gone: `card-consequence-paid-off`, which asked returns to own profitable
lots, and `card-forecast-skill`, which required strictly positive excess skill. The third,
`censorship-bound`, is narrowed from every censored outcome to accountable resolution
failures. And the privilege that survived the last round of card removals is gone with them:
the settler no longer trains judge standing from `return_paid_off` alone. A judge may also
now file a **fidelity objection**, a structured claim that a favourable measurement is not
serving its value.

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

In edition 3 the definitions below are **in the charter**, not in this document and not in the
manifest's comments. `Charter.norms` is a list of `{id, definition}`; `Charter.render` prints
each definition under its norm, so every seat that is shown the charter is shown the words it
is judged against. A charter written before this field existed loads unchanged, with empty
definitions, and its content digest is untouched: the bare-string form is still valid TOML for
the loader and is still what gets written back out where no definition exists.

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

**Fidelity**, in edition 3 restated in the architect's words: *"Measurements are defeasible
evidence of the values, not substitutes for them. A favorable measurement is insufficient when
supported consequences contradict the value it represents. A judge identifying such a conflict
must name the value, the measurement, the evidence and the uncertainty, and make the claim open
to challenge. Missing measurement alone is not evidence of failure."* This is the norm that
keeps the other four honest. With one card, most of what a seat does is never measured by a
card at all, and what is measured can be met in letter and missed in spirit. The last two
sentences are the load-bearing ones: fidelity is the basis of an appeal, not a truth oracle,
and the absence of a measurement is not a finding against anybody.

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
of every decision that contributed to the violation, in proportion to its share. For the card
below the share is generic, one part per decision that responded in the window, and never
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

## The one card, and how it can be gamed

Every proxy can be gamed. The essay calls this overfitting and says the answer is not a cleverer
proxy but the machinery around it: judges that answer for their verdicts, forecasts settled
against the real world, an antagonist paid to fool the judges, an immune organ that watches the
distribution of behaviour for collapse, a fidelity norm that makes letter-without-spirit a
failure, and a wallet that only the world refills.

### `censorship-bound` (epistemic integrity)

*What it measures.* `avoidably_unresolved_share`: attributable, avoidably unresolved accepted
commitments over the eligible commitments that came due in the responsible scope. A commitment
is unresolved when it reached its horizon and no evidence settled it, so the world never got to
say whether it was right.

*What is not in the sample.* A promise that is not yet due is not measured — its horizon has
not arrived, and nothing about it is anybody's failure yet. A commitment whose fact the owner
documented as externally unobservable, without fault of its own, is excluded and recorded with
its reason (`external_unobservable` in the settlement, carried into the sample). An event the
seat never committed to observe is not in the sample at all, because there is no commitment.
And **no eligible sample means unmeasured, never zero**: a scope with nothing due is not
thereby compliant, it is simply not measured, and the card contributes nothing for it.

*Over what sample.* The last 25 eligible due commitments, per assembly — the responsible
scope. An excluded commitment never occupies one of the 25 slots, so documented unobservability
cannot push an accountable commitment out of the window that answers for it.

*What is acceptable.* At most 0.30, explicitly provisional.

*Who answers.* The commitment owner, including responsibility delegated at acceptance. Price
`lambda` 0.10, population-adjustable.

*The obvious hack.* Commit to nothing that could be left unresolved; promise nothing checkable;
or claim external unobservability for everything.

*What catches it.* Nothing to settle is nothing to be scored on: standing is built from settled
commitments, and a seat with none is woken less and less. The exclusion is a documented reason
in the ledger, not a flag a seat can assert on its own answer: the runtime records it where the
fact was sought and not found. And the judges, under fidelity, are asked to mark a return made
unfalsifiable on purpose — which is exactly the kind of claim a fidelity objection is for.

*Why this card and not the others.* It protects the evidentiary process rather than prescribing
a kind of economic activity. Zero cards is a legitimate reachable constitution; this is the one
card worth starting with, and the population may replace, reprice or remove it.

## The fidelity objection

A verdict may carry one structured extra field:

```
fidelity_objection: { value, measurement, evidence, uncertainty }
```

The `value` must be one of the charter's norms and the `measurement` must name something the
charter actually measures — a live card or a named observation — so the claim is about a real
proxy and can be answered. `evidence` is the judge's reason; `uncertainty` is how unsure it is,
in [0, 1].

Four things are true of it, and they are the point:

- **It is validated.** A malformed or unplaceable objection is refused, and the refusal is
  ledgered with its reason. It cannot be a gesture.
- **It is ledgered** (`fidelity.objection`) with the judge, the return it judged, and its four
  fields, before anything scores it.
- **It is scored like any verdict.** Its stated confidence (`1 - uncertainty`) is a claim that
  the charter's blame will land on that return, scored by the same proper score, against the
  same realised blame share, into the same judge standing. A judge that cries foul on returns
  the charter does not blame loses standing for it.
- **It is never a kernel verdict on its own.** It settles no decision, moves no money and
  blames no card. The measurement it names is contestable through the existing challenge
  route: a challenge names a live card, gives evidence, offers a replacement, and buys a trial
  in which both are measured side by side before the committee votes.

## Why the paid-off card and the forecast floor went

`card-consequence-paid-off` asked at least 40% of settled consequences to pay off, where paying
off means the return's own handle owns a lot with positive net proceeds. That is not what money
says. Nine gains of a dollar and one loss of twenty is a 90% hit rate and an eleven-dollar loss;
three gains of ten and seven losses of one is a 30% hit rate and a twenty-three dollar profit.
The card approves the first and rejects the second, and nothing in accounting requires that
preference. Worse, the unit of credit is one return handle: a successful investigation, a useful
program, and an informed decision not to trade own no lot at all, and scored zero for it. It was
a substantive model of usefulness wearing a measurement's clothes.

`card-forecast-skill` required strictly positive excess skill over a matched baseline. On a
stationary uninformative task an honest, well-calibrated forecaster can only equal its baseline,
and that should not be a constitutional failure. The scoring machinery stays — forecasts are
still settled against the world and still trained against paired baselines — but beating the
base rate is no longer an obligation the charter imposes from the start.

**And the privilege went with them.** Removing a card from a TOML file did not, in edition 2,
remove the thing the card was about: `Settler` trained consequence standing from the kernel's
`return_paid_off` commitment and from nothing else, so whatever else a judge forecast settled to
its handle and to the prevalence baseline but never reached its selection weight. That is an
architectural choice about what a judge is *for*, made in code rather than by the population.
In edition 3 every registered predicate a judge forecast trains its standing, weighted by the
charter's cards: a card's `answers_for` names the scope the population holds accountable, a
claim about a return in that scope carries that card's share of the weight, and a charter whose
cards name no particular scope weights every claim equally. Remove a card and its effect on
standing goes with it, entirely — which is pinned by a test that flips a card in and out and
diffs the standing updates. Coverage is now every forecast a judge was asked for, not one
predicate's.

`return_paid_off` is untouched as a fact: cash settlement is immutable, the venue still says
what a lot realised, and anyone may forecast it. It is simply no longer the unique enduring
anchor of judge quality.

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

What is left, after edition 3, is one card, about whether a commitment was ever settled.
Durable agency has no card because the wallet and the wall are its whole enforcement, and a card
that tried to say it again would say it worse; consequential usefulness has none because the
money already says it better than a hit rate can.

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

**Learning death is a lost access, not a quiet week.** The immune organ's diagnosis of
"learning death" is loss of affordable, usable access to investigation and revision, never
unchanged behaviour. The gone-frontier rule is unchanged — stable cells, no registrations or
revisions, no improvement in consequence outcomes, and a compliance the cards cannot vouch for
— and the record now says which access is gone and why: no affordable seat (with the cheapest
seat's price against the richest seat's entitlement), no route to registration (the novelty
reserve is empty, or no seat is affordable), or no route to revision (no committee can be
drawn, or registration itself is gone). Each rides in the organ's own window profile as
`access:<name>`. A population that can still afford to look and chooses not to has lost
nothing: protected exploration buys an option, it does not require its exercise.

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
