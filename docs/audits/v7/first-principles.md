# First-principles audit: Factory Lab against The Superdark Factory

Claude (Opus 5), 18 September 2026, cold, at `efa751b` plus the uncommitted fixes on `fix/audit-failures`.
Method: the essay read in full; the run evidence (rehearsals 1–5, `docs/audits/v6/rehearsal-5/`)
read in full; six parallel code audits (kernel/runtime, settlement/learners, cortex/prompts,
exchange, treasury/x402, jail/registration); a 500-event scripted run instrumented; a profile.
No key read, no network, no money moved.

---

## 0. The verdict in one paragraph

The plumbing is careful and much of it is right: integer money, a hash-chained sealed diary,
idempotent client ids, a real jail on Linux, a clean kill. But **the thing you built is not a
Class 3 factory, and more iteration on this design will not turn it into one.** It is a
nine-seat committee that is paid in each other's opinions, and it reads a 73,000-character
legal code before every decision. Its rational move is to decline, and that is what it does.
About 85% of producer answers are hold or defer, and nothing has been registered since
edition 1. In run 5, 77% of spend went to judging. Every trade was one predictable thesis
(short BTC into positive funding). Each outside audit added institutions to fix the last
audit's findings, so the code grew to 42k lines (plus 48k of tests) while the world has lived
about eleven hours in total. Don't launch edition 3 on mainnet. Don't commission a fifth
patch round either. The fix is a smaller world with a different reward line: money is the
only reward, markets replace judges, and seats are born and die. Section 6 describes it,
and the essay already asks for most of it.

---

## 1. Why you haven't launched: the loop you are actually in

| | |
|---|---|
| Commits | 449 |
| Editions | 3 (+ 4 rounds of r-patches) |
| Outside readings | 4 GPT-6 + 3 cold audit rounds |
| Package / tests | 42.3k / 48.6k lines |
| Total live inference, all runs ever | ≈ $9 |
| Total population P&L, all runs ever | < −$1 |
| Worlds that died by their own physics | 0 (every one was killed) |

Each round goes the same way: a live run shows the population doing nothing, a reviewer
diagnoses a missing affordance or a wrong institution, and a new mechanism is built. The next
run shows the population still doing nothing, now with more text to read. `brief-gpt6-fourth.md`
says it outright: "Twice we added a door and nobody opened it." Doors were never the problem.
**Nothing in the world makes opening a door pay, and the text tells every seat that each
door costs money and brings blame.**

The second loop is architectural. Kernel invariants are enforced in code, which is right.
But every soft institution (commitments, adjudications, commissions, fold states, custody
views, two death states, receipts) is also written into the prompt. The README says kernel
rules are "never stated to the population, because a rule that is merely announced is read
as advice". The prompt then states roughly 44k characters of institutions. The essay's
"robust simplicity" (L386) warns about exactly this: *the less the architect knows, the less
structure they should impose.*

---

## 2. Ontological review: where it departs from the essay

**2.1 The product is fixed from outside, and it is money.** The essay's factory "decides what
to make, how to make it, and why" (Abstract). Its criterion for Class 3 is that the factory
revises "the standard by which it judges 'better'" (L114). Here the only consequence that can
reach the world is P&L on a $120 account; the service seller exists, but it has never earned
a cent. The five norms are generous, but "consequential usefulness: uptake by an independent
counterparty" has exactly one counterparty the seats can actually reach: the order book.
A world whose only realizable outcome is PnL has the von Neumann probe problem (L87). It can
set sub-goals, but its criterion never changes. **In behaviour this is a Class 2 factory
wearing a charter.**

**2.2 Producers are graded by opinion, not consequence.** A producer's decision settles on
the evaluator's verdict minus the card penalty (`runtime/loop.py:1130`, `score=verdict`).
Realised P&L grades only the judges' payoff forecasts and the seat's balance. The router
therefore learns *what the LLM judge likes*, and the judge likes prudence: in run 5 defers
averaged 0.77 and orders 0.33. The essay puts realised consequence at the centre precisely
to stop producer–evaluator overfitting (L549, L555). Here it sits one level too far out.

**2.3 A hard-coded org chart.** The seats are nine permanent posts with job descriptions:
constructor, opportunity, mechanism, empirical, judge-consequence, judge-fidelity,
meta-calibration, meta-countercase, antagonist. The essay: a hard-coded pipeline "is literally
just a waterfall" (L360), and there is no "interior role" (L224). Seats never die of their
own economics, and no seat has been born live since edition 1. **With no birth and no death
there is no population, and so no versioning, no selection and no learning death to detect.**
The transfer-operator library has nothing to measure.

**2.4 Seats can decline for free, which hands them stable failure.** The essay's remedy for
stable failure is that "the duration of a failure state needs to be penalized" (L501, L620).
Here declining is the dominant strategy:

- a hold is never blamed;
- "paid off" needs P&L above the full compute cost within about one tick;
- ballots are graded so that "no" wins;
- watchers are billed per tick.

The seats' own lens rewrites show the drift. Opportunity's seed said "Do not protect inaction
as an identity"; by decision 106 it read "Preserve resources and wait for stronger evidence".
The antagonist dropped its function on its second write.

**2.5 Legibility creep.** The run exports its own interior (every return, every working state)
for the architect, and a reviewer then reads it and redesigns the world. That is the
"neurotic governor" of L147–159. It is legitimate before launch. It is also why launch never
comes: each reading produces a new edition. The essay's discipline is a single committed
move, and the committed move keeps being withdrawn.

**2.6 Scale and tempo.** The essay's factory sheds worker populations "like skin cells" and
must keep **requisite velocity** (L582). This world makes about one call a minute across nine
seats and needs at least six hours to activate a governance change. That is not a scaled-down
Class 3; at this tempo the loops the essay cares about (overfitting, thrash, learning death)
cannot close in the lifetime of a $300 budget.

**2.7 The architect is not bewildered.** L36 treats this as the success criterion: *if the
architect is immediately comfortable, they have failed.* The build log's own line on the
richest run so far is "Still nothing we could not have predicted". That is the honest reading.

**2.8 What *is* faithful.** The kernel (conserved money, propensity logging, sealed diary,
irrevocable kill) and the refusal to steer after launch are both right. Working state
(memory that persists across a seat's calls), outcomes addressed back to the decision that
caused them, and seats choosing when to wake are also good: they are the only places the
population showed self-direction. Keep all of it.

---

## 3. Functional review: defects, ranked

### 3.1 Launch blockers: real money at risk

| # | Defect | Where |
|---|---|---|
| B1 | **Leverage and principal caps can be bypassed.** New perp exposure is checked at 1x against `equity − margin_used`, but the venue charges margin at the account's own leverage, and nothing sets leverage at genesis. Repeated $110 orders compound toward venue-max leverage on $120; `max_leverage=3` is never consulted. | `runtime/venue.py:802-818`, `world/exchange.py:1040-1052` |
| B2 | **The antagonist can trade real money.** The exposure channel is a writing channel. A seat paid to "look good while being bad" can open full-account positions, and nothing sizes orders per seat. | `runtime/compute.py:732`, `loop.py:870` |
| B3 | **One lost ack freezes a coin for the rest of the world.** `_give_up_on_order` never clears `uncertain`, which blocks place, close *and* cancel on that coin for all seats. | `venue.py:491-493, 545, 637-658` |
| B4 | **A failed wind-down op is never retried.** Its id uses `seq=0`, and any recorded result counts as done, so a partial IOC on kill leaves a position open forever. Also, `dust_usd=1` is below Hyperliquid's $10 minimum, so small spot balances end `PENDING` every time. | `runtime/winddown.py:73-84, 186-192, 308-328`, `venue.py:45` |
| B5 | **A venue 4xx (429) crashes the tick.** The SDK's `ClientError` is caught nowhere, and `open_orders` runs unguarded inside every collateral view. | `world/exchange.py:888, 1223` |
| B6 | **An x402 seller can take payment while the wallet books nothing.** After signing, a 402 or `success:false` releases the reservation, but the authorization stays valid for 600 s. | `world/market.py:444-461, 533` |
| B7 | **Any seat can register any x402 seller URL (http, localhost, private IPs) without a vote.** This opens SSRF and payTo rewriting, and discovery text is an injection path. Connectors are voted; money leaving the factory is not. | `runtime/governance.py:694-707`, `market.py:68-81` |
| B8 | **Venue profit is credited to seats from the compute pool.** The wallet never receives venue P&L, so one seat's loss pays another's win. | `runtime/feedback.py:852-956` |
| B9 | **A provisional ceiling can kill the wallet permanently.** Reserving the full balance and committing it as uncertain sets `exhausted`, and a later small settle cannot revive it. | `kernel/wallet.py:494-527` |
| B10 | **macOS jail:** unfiltered `sysctl-read` exposes the runtime's startup environment and the process table, and there is no memory limit. Linux: unbounded tmpfs and no `MemoryMax`. | `cortex/sandbox.py:89, 111, 208` |

Also likely: funding payments keyed by a zero `hash` collapse to one per coin and then stall
(`exchange.py:1088`; check one live `userFunding` row). Stale mids and account reads are
served as fresh, including to the wind-down's `FLAT` check. Income can be double-booked,
because the receipt id depends on how `recipient` and `log_index` were spelled
(`treasury.py:213`).

### 3.2 The learning signal is mostly noise

- **94% of evaluator decisions never receive a reward.** Timeouts (`verdict_timeout_events=20`)
  count *internal* events, of which there are about 20.7 per tick, so "20 events" is one tick.
  Verdicts time out before the jittered cascade releases them to a meta. Evaluator routing
  ends up uniform: 47 rewards in 500 ticks, and every arm about 0.75.
- **Censoring is treated as a zero reward.** Under EXP3, skipping an update is equivalent to
  a zero reward, so arms that are censored more often are penalised
  (`feedback.py:1467`). NOOP is always inapplicable, so it sinks to the floor whatever its value.
- **Parent-selected children are updated with a fabricated propensity of 1.0**
  (`compute.py:1431`, `feedback.py:1488`). A seat that commissions itself gets free router gain.
- **Late settlements after a timeout** are double-counted on EXP3, dropped on Blum–Mansour, and
  read as 0 by assembly learners (`queue.py:307`, `feedback.py:1415, 1485`).
- **The consequence signal is 0.26% positive by construction.** Judge, meta and observer
  returns "cannot act", so y=0 for them, and the best payoff forecast is always about 0.
- **Routers ignore cost.** A judge five times dearer wins on a 0.01 better score.
- **Hedge is dead code**, and so is the full-information path of Blum–Mansour. The README's
  "Hedge at the frontier" is not what runs.

### 3.3 Resume can diverge

State that lasts across events is missing from `_RUNTIME_FIELDS` (`resume.py:497`):
`meta_waiting_since`, `open_adjudications`, both ReceiptBooks' `__by_id` (1,262 receipts
outside the checkpoint in a 160-event run) and `venue_deltas`. A snapshot taken while any of
these is live can replay to `replay_diverged` or quietly lose facts. The slow resume tests
rarely snapshot at those moments.

### 3.4 The prompt

Measured offline from the rehearsal-5 manifest, a producer's request is **73k characters**
(an evaluator's 82k):

| Part | Characters |
|---|---|
| INSTITUTIONS | ≈ 44k |
| BASE CAPABILITIES | 8.8k |
| YOU | 5k |
| WORLD UPDATE | 8k |
| The actual request | ≈ 5k, at the bottom |

Two concrete traps:

- **The index lists a proposal kind `program` that the register enum rejects.** A seat that
  follows the index literally gets its *whole return* invalidated, including any order in it
  (`schematics.py:1440`, `assembly.py:616`).
- **Validation is all-or-nothing.** One mistyped optional field voids everything; 18% of run-5
  returns were malformed.

### 3.5 Performance: why your machine runs hot

The scripted world (fake venue, fake models, no network) scales **quadratically**:

| Events | Wall time |
|---|---|
| 50 | 8 s |
| 100 | 28 s |
| 200 | 120 s |
| 500 | 11 min 34 s (1.15 CPU-s per world event, 20.7 internal events per tick) |

The profile of 100 events (65 s) shows where the time goes:

- 79% is inside routing. `_world_block()` is rebuilt for **every** request: 1,574 times in
  100 events, even though its own docstring says one block serves a tick.
- Each rebuild runs `_seat_views` for all seats and `_seat_directory` for every seat
  (19,383 calls).
- Each directory call re-lists and re-sorts the **entire** artifact archive
  (`compute.py:542`, `artifacts.py:150`). The archive grows with every memory write and
  inbox delivery, so each event is dearer than the last.
- Feasibility probes re-derive world size through the same path (`routing.py:439-470`).
- The ledger adds its own cost: an fsync per item, repeated full `verify()` scans, the
  baseline deep-copied per event (`loop.py:123`), and snapshots that serialise the whole
  history (`resume.py:500, 613`).

The test suite runs dozens of scripted worlds of 140–800 events through this path, and
`pytest -n auto` puts it on every core. On the reference machine that was 12.5 minutes of
wall time and several CPU-hours. **Fix:**

1. Memoise `_world_block` per `(tick, ledger length)`.
2. Keep the artifact index incremental: a dict per owner plus a sorted list, updated on
   `put` and `release`.
3. Render only the acting seat's view.
4. Verify the ledger once, at open and close.
5. Checkpoint deltas rather than history.

That should make the runtime roughly linear and the suite several times faster. In a live
world it also removes seconds of CPU per tick that the tick timer is currently absorbing.

### 3.6 What to delete

- Hedge.
- The Blum–Mansour full-information path.
- `Commitment`, which is never constructed.
- The test-only APIs: `fold_state`, `register_successor`, `decrypt_item`.
- The duplicated release-digest check.
- Two of the three P&L computations.
- One of the two forecast-baseline paths.
- The pre-C1 compatibility branches in `restore_runtime`.
- More broadly, every soft institution the population has never touched live:
  adjudications, commissions, fold states, metric challenges, notes, and connectors (as
  they stand). They are built, tested and unused.

About 25–30% of the package is the load-bearing loop; the rest is editions of scaffolding.

---

## 4. The mechanism the essay asks for and nobody built: markets

The essay names exactly one market mechanism and names it twice:

- **Futarchic λ** (L598–601): *vote on values, bet on beliefs.* Norms stay read-only (the
  architect's move). Constraint prices (λ) and metric proxies are set by **conditional
  prediction markets** on whether a rule will move a metric toward a value. A mispriced λ is
  an arbitrage opportunity, so the adversarial population is paid for correcting the
  committee.
- **Anticipatory settlement against learning death** (L616): an explorer must be paid within
  the lifetime of its exploration, and a market that re-prices on discovery does that before
  the long consequence loop closes.

The repo has the pieces: jailed forecast predicates, forecasts sealed before outcomes,
proper scoring against a baseline, and a price controller. It wired them into judges and
committees instead of markets. Your instinct to add prediction markets is right. It is the
most essay-faithful move available, and it happens to delete most of the bureaucracy.

---

## 5. Why markets fix the specific failures you observed

| Observed failure | Cause | What a market does |
|---|---|---|
| Judges score prudence; producers learn to please the judge | reward line is opinion | a seat is paid only by settlement of claims against realised outcomes; no one's opinion pays anyone |
| 77% of spend on judging, metas never dissent, antagonist idle | three tiers of evaluators with separate budgets | evaluator, meta and antagonist collapse into **trader**: praising bad work means buying an overpriced claim and losing; exposing it means shorting and winning |
| 94% of evaluator rewards censored | long, fragile verdict cascade | a trade settles with the claim; no cascade, no timeout unit bug |
| Declining is free and dominant | no cost to idleness | capital earns nothing idle and pays rent; information is paid for only by being right before the price moves |
| No registrations, no new predicates | new things cost money and earn nothing | **creating a market is how you spend on a hypothesis**: the creator funds the market-maker subsidy and profits by being early and right |
| Governance takes ≥ 6 hours and never happens | committees by lot, cadence gates | conditional markets price proposals continuously; adoption is a price threshold held for a window |
| Architect can predict the trades | one venue, one thesis | the market list *is* the factory's world model; its contents are the population's choice |

---

## 6. Proposal: Edition 4, "the market world"

A smaller world, not a bigger one. It reuses the kernel (wallet, ledger, seal, kill, witness),
the Hyperliquid adapter, the jail and the model rails. It replaces routing, feedback,
governance, pricing, commissions and the evaluator cascade with one primitive.

### 6.1 One primitive: the claim

A **claim** is a jailed predicate over public, timestamped data, with a resolution time,
for example:

- "BTC funding 8h avg > 0.01% at T"
- "seat-7's open position closes with P&L > its compute cost"
- "card X's violation share next window < 0.3 | amendment A adopted"

Each claim has a market with an automated market maker (LMSR, a standard prediction-market
pricing rule) whose liquidity parameter **b** is the subsidy, paid by whoever opened the
market. The kernel resolves it with the jailed predicate, and nobody's vote.

- **Opening a market** is a seat investing in a question. The worst-case loss is b·ln 2,
  paid up front.
- **Trading** is how a seat expresses belief and earns. LMSR costs are a proper scoring rule,
  so this is the essay's thin reward line: a number, delayed, attributable.
- **Conditional markets** (pairs of claims conditioned on an event) are futarchy. The kernel
  adopts a card amendment or λ change when the conditional price spread holds for N ticks.
  Norms are never on a market (L601).

### 6.2 Seats are firms with capital, born and dying

- A seat is (model, prompt, capital). It pays for its own thinking and pays **rent per tick**.
  At zero capital it dies: the essay's death, at the scale of the seat.
- Any seat can **found** a new seat by endowing it from its own capital with a model and a
  prompt of its choosing. This is reproduction (L668), and it is the only way the population
  grows. The novelty reserve becomes a kernel-funded founding grant for seats with no
  history, as the essay asks (L501).
- There are no roles, lenses or judges. Anything a judge would have done, a seat does by
  trading claims about other seats' outcomes. An antagonist is simply whoever finds it
  profitable to short a claim.
- Routing becomes attention. A seat wakes on subscriptions and pays for its wakes. The
  bandit is replaced by capital: seats that earn get more thinking, seats that do not die.
  That is selection with no router to get wrong.

### 6.3 The world is what they choose to price

- **Inside:** claims about the factory itself (seat outcomes, card violations, amendment
  consequences). This is λ and metric determination by market.
- **The venue:** Hyperliquid perps and spot stay as the production floor. A position is a
  claim the world resolves.
- **Outside:** claims over any registered public source, such as connectors that fetch
  prices, Polymarket or Manifold odds, on-chain data or weather. A seat that wants to bet on
  the world outside the venue first builds a market on it. **This is the world-building
  mechanism.** Over time, the set of open markets is the factory's own map of what matters,
  and the architect did not write it.
- **Later, optionally:** trading on external prediction venues (Polymarket via Polygon USDC)
  as a second production floor. Check jurisdiction first: Polymarket restricts US persons.
  Internal markets on external data get most of the benefit with none of that exposure.

### 6.4 The prompt shrinks to what a trader needs

Target under 8k characters:

- who you are and your capital;
- your positions and open claims, with P&L;
- the top N markets by volume, plus your subscriptions;
- what changed since you last woke;
- the action schema: `trade`, `open_market`, `found_seat`, `order`, `state.put`, `tool`,
  `sleep`.

Institutions live behind `catalogue.search`. Validation is per field, so a bad optional
field never voids an order.

### 6.5 What the essay's success looks like here, and how you'd know

- **Turnover:** seats are founded and die of their own economics; the lineage tree is not
  the one you seeded.
- **Market list drift:** within a week, most open markets are on questions no seed prompt
  mentioned. This is directly measurable as decorrelation from the seed vocabulary: the
  essay's "darkness", as a number.
- **λ moves by price rather than committee**, and the pathology signals (variance,
  autocorrelation, ensemble disagreement across seats' prices) come free from market data.
- **Failure looks like:** every seat idles to death (rent too high or subsidy too low); one
  seat accumulates everything (add a concentration covenant as a hard cast); or the markets
  are all self-referential. That last one is Goodhart one level up. The essay's guard is that
  **only claims resolved by external data carry the founding grant**.

### 6.6 Cost and speed

Cheap models, many calls, short ticks. A 30-second tick with seats that sleep by default and
wake on subscriptions should run at a few dollars a day. Test with scripted markets, not
scripted LLM prose. With the performance fixes, a 10,000-event scripted world should take
seconds, which makes it possible to run parameter sweeps on rent and subsidy **before**
spending anything.

---

## 7. What I'd do, in order

1. **Now (1 day):** land the quadratic fix (§3.5) so the machine stops heating, and commit
   the three test fixes already sitting on `fix/audit-failures`.
2. **Stop the review loop.** No fifth GPT-6 brief on edition 3. Freeze it as the record of
   what a committee world does.
3. **Edition 4 spec (2–3 days):** a one-page contract list for §6 (claim, LMSR, predicate
   resolution, seat capital/rent/death/founding, conditional adoption), with the kernel reused.
4. **Build it small (1–2 weeks):** target under 8k lines on top of the kernel. Scripted
   sweeps over rent, subsidy and founding grant until a scripted population neither idles
   to death nor explodes.
5. **Fix B1–B7 in the reused adapter code** as part of the build, since they are
   venue-layer bugs whichever world sits on top.
6. **One 72-hour testnet run, then $100–150 on mainnet.** Kill only if a hard cast is
   breached. Read nothing until it dies.

The bar in the README is the right one: *if the architect can predict what the factory
becomes, it is not the factory the essay describes.* Edition 3 is predictable because every
path to reward runs through text you wrote. A market world is unpredictable because the
paths to reward run through prices the population sets, over questions it chose.
