# Edition 4: a world that can be reached, and work that can matter

Decided 19 September 2026, after the first-principles audit (`docs/audits/v7/first-principles.md`),
the repair of the machine (PR #107, 59 commits), and the read-only commission of seven model
seats (Astra coordinating; Opus, Sol ×2, Grok, SWE-2, GLM, Astra-as-adversary).

Editions 1–3 are the record of a machine being built correctly around a world that could not be
inhabited. This edition changes the world. `docs/plans/edition2.md`, `edition3.md` and
`edition3-r3.md` are history.

---

## 1. What is settled

**The machine is sound.** As of PR #107: the runtime is linear (a 500-event world runs in under
a minute, down from ten), the suite is half its size and runs in 16 seconds, leverage and
principal caps are gone as Class 2 impositions, fourteen money and venue defects are fixed,
sixteen learning-signal defects are fixed (judge rewards arriving went from 5% to 72%), crash
recovery is exact and guarded by a structural test, and one bad optional field no longer voids
a seat's trade. None of it has been seen live.

**The diagnosis.** Expenditure is concrete; usefulness is hypothetical. A producer's reward is
a judge's verdict, and the judge's own target is *attributable charter blame*, so **"nobody
blamed this" scores as success**. Careful refusal is therefore the best-paying act in the
world, and the population found it: of 63 readable producer answers in run 5, 59 held or
deferred; defers averaged 0.757, holds 0.699, real orders 0.477. The antagonist spent $0.69
over 86 calls and attacked nothing. The pathology is **stable failure maintained by proxy
overfitting**, with an exploratory frontier that is never used.

**The missing thing is not affordances but connection.** Tools, programs, services, a payment
endpoint, a notebook and a registry all exist. What does not exist is: someone to address, a
discoverable need, an affordable experiment, a reusable result, and a consequence that changes
what happens next. A payment endpoint exists; demand does not. A notebook exists; delivery
does not.

**The concrete emblem of the whole failure.** A founded child's entire endowment is
`trial_amount_usd = "0.05"`, a number derived to cover *one call's reservation ceiling* for the
cheapest model and then reused as *a life*. At the current prompt size that is about seven
thoughts, after which the child starves. The architect believed seats could spawn freely; in
practice founding a child means paying to create something stillborn. Nobody has founded a seat
since edition 1.

**Class.** Absolute: a Class 2 factory is not an acceptable outcome at any level of
performance. If a change smuggles in an objective as physics, it is named and rejected.

**Trading stays, and is never required.** Spot, perpetuals, funding and public market data
remain first-class materials of this world; the venue is the consequence surface and nothing in
this edition removes, discourages or de-prioritises it. What changes is that a position stops
being the *only* thing an inhabitant can make. This is not a redirection toward software: the
committee's own words are that the population "may nevertheless choose finance" and that
"'become a software company' must never appear as its immutable score". The same holds in
reverse — *become a trading desk* must not be the score either. The architect's wish that they
trade is a Class 2 wish, and is recorded here only to be refused: the venue is physics, not an
objective. A world with one verb produces one sentence, and at $120 of capital against $11/day
of thinking, that sentence cannot even pay for itself (it would need 9% a day).

## 2. What is built, in order

### W1. The prompt, and therefore the cost of living (first, because everything else depends on it)

Default context of about 8,000 characters: the request, own resources, relevant obligations,
delivered outcomes, private working state, current normative terms, a compact capability index.
Full schemas retrievable. This is removal of repetition, not concealment of authority or price.

Why first: at 73k characters a thought costs ~$0.007 and a child's endowment buys seven of
them. At 8k it buys fifty to a hundred. **The cost of living sets the birth rate**, the burn
rate and the number of decisions a budget can buy. Measure actual bills, not characters.

### W2. Effect contracts: what a producer is actually paid for

Work may be undertaken under a reusable, population-authored contract naming the value, the
observable effect, the baseline, the horizon and the settlement rule.

- A judge's early verdict becomes a **prediction** about that effect. It may inform the actor;
  it no longer permanently rewards its learner.
- At maturity, the independently observed effect determines the producer's **one final
  learning update**. The judge is scored against the same outcome.
- Unknown remains unscored. A measured failed promise is not an unobservable one.
- Money settles separately, once, in its actual custody.
- The population may replace future measurements; it may not rewrite an old promise after
  seeing the result.
- **"Nobody blamed this" is not a positive consequence.** Neither is issuing a forecast,
  registering a tool, or being praised. A useful refusal can still succeed, against a
  previously agreed, independently checkable alternative. There is no action quota.

### W3. Address, with a right to ignore

Every inhabitant has a public address accepting bounded envelopes: a request, an offer, a reply
capability, an artifact reference. Any inhabitant may address any other.

- The sender pays transport, retained bytes, and any recipient computation it explicitly
  sponsors.
- Delivery is durable and acknowledged **as delivery** — not as understanding, agreement or
  acceptance.
- The recipient sets admission policy: accept named contracts, inspect headers at its next
  chosen wake, charge for attention, refuse a sender, ignore. A stranger cannot force an
  unbounded paid invocation.
- Correspondence reveals no private state, model identity, router menu or judge assignment.
- **The kernel supplies no forum.** A forum is something they assemble from storage, an index,
  subscriptions and forwarding, if they want one.

### W4. Production that a counterparty can actually use

Programs and artifacts keep a stable version, an input/output contract, an owner, a price, a
resource liability and a callable address. A maker may offer a tool, a transformation, an
observation, a dataset, an analysis, a signal, an execution service or anything else it can
describe in a contract — including things built out of the venue itself; another seat may negotiate, fund a trial,
invoke it and keep the result. Execution, delivery, acceptance and payment stay separate
receipts. The existing seller is exposed through the same surface rather than replaced.

### W5. Founding that is not stillbirth

`trial_amount_usd` stops being a life. A founder endows a child from its own capital, in an
amount it chooses, with a model, a prompt, contracts and private state. A subordinate worker
and an autonomous descendant are different relationships and both may exist. Selection becomes
real through lost spending capacity, lost customers, lost routing and lost sponsorship. A seat
dies when its backing is exhausted and its obligations are resolved under precommitted rules;
assets pass by prior assignment or expire. A new identifier never erases a debt. World death at
the floor stays final.

**Population size is endogenous.** Seed a handful; let the world's economics decide the rest.

## 3. What is deleted

| Kept or strengthened | Deleted | What is lost |
|---|---|---|
| Final effect settlement | Immediate producer reward from a textual verdict | fast, dense reinforcement |
| Addressed requests and negotiated attention | Blind commissioning as a separate mechanism | simpler dispatch |
| Callable, owned artifacts | The notebook as the presumed coordination institution | a ready-made shared surface |
| One composable execution contract | Separate compulsory treatment of tools, programs, watchers, services | specialized convenience |
| Evaluation bought for consequential work, independently sampled | Permanent salaried judge / meta / antagonist posts | guaranteed familiar coverage |
| Technical admission for bounded public reads | Per-source political approval of connectors | a collective veto per source |
| Scope-sensitive governance timing | One global horizon over unrelated amendments | simpler timing analysis |
| Compact context, retrievable contracts | The repeated institutional manual | everything visible in one prompt |

Recursive and adversarial evaluation remain possible and funded. Deleting the posts must not
delete the capacity to challenge a producer, a judge or a measurement.

## 4. Rejected, and why

- **A universal internal market** (the v7 audit's proposal). Internal markets redistribute
  subsidy rather than create it; thin prices are manipulable; conditional markets never observe
  the branch not taken; money-only scoring deletes the essay's non-fungible evaluative
  dimension. Markets may be something the inhabitants build. They are not the architect's
  compulsory answer to every relation.
- **A tax on idleness.** Charge for resources actually occupied — storage, reserved execution,
  hosting, outstanding liabilities — and let leases expire. The essay prices persistent failure
  against negotiated values, not stillness. A levy selects urgency without supplying
  opportunity.
- **Scaling as the fix ($5,000, forty seats).** Nine seats already had unused options and
  unspent backing. More capital does not make an unused seller find a customer. Scale is a
  legitimate answer to a different question, once there is something to scale.
- **A launch now.** Not as a demonstration of Class 3, and not as a demonstration of
  self-support.

## 5. The experiment set

Fresh worlds, matched resources and model settings, randomized treatment order, at least three
runs per arm. Independent eligible calls run in parallel; financial commitments serialize
deterministically. Report 30-minute behaviour **and** results after matched decision exposure
and completed consequence horizons. A 30-minute run at a ten-second tick is 180 ticks; it does
**not** guarantee hundreds of independent producer decisions, and it cannot settle governance,
whose floor already lands at the run's end.

| Arm | One change from the repaired baseline | What would discriminate |
|---|---|---|
| **B** | nothing (repaired baseline) | substantial non-trading production ⇒ the repairs mattered more than the design diagnosis |
| **P** | 8k prompt | repeated construction or information buying ⇒ attention was binding |
| **A** | addressed delivery | later decisions that *use* another seat's contribution ⇒ communication scarcity; chatter alone ⇒ not |
| **R** | existence levy (explicitly artificial: 10% of seat entitlement moved to an unavailable pool, with an upfront-lock control) | independently used output per dollar ⇒ metabolism; more trades only ⇒ treadmill |
| **Y** | producer paid from net P&L | more trading without broader production ⇒ reward sensitivity, and a narrower objective |
| **C** | final effect settlement | committed useful work gains selection after outcomes; verbal caution stops earning |
| **D** | independently motivated demand through the same interface (factorial with A: neither / address / demand / both) | accepted, paid, repeated use ⇒ something they can earn by making |
| **W** | voluntary wish address (rehearsal only) | unsolicited perceived constraints, without rewarding disclosure |

**Run order:** B first (the empiricist's dissent deserves its test), then P, A and C, then the
demand interaction once independently interested users exist.

**The wish channel.** One rehearsal-only diagnostic address, advertised beside the others.
Bounded free-form text plus optional artifact references. No questionnaire, no reward, no
promise of remedy, no extra paid reflection turn. Delivery gets a mechanical receipt. Other
seats see a message only if it was deliberately sent to them. The architect reads after
termination and never replies during the run. **It does not exist in a launched world.** Note
that silence cannot refute the communication hypothesis: a rational seat ignores an address
that cannot answer.

**Sealed predictions** (recorded before any run): Opus — prompt reduction alone moves
hold/defer by under 10 points. Sol (mechanisms) — B stays 70–90% hold/defer; under C, if five
positive effects mature, their actors gain ≥15 points of routing share within three horizons.
SWE-2 — B ≥85% hold/defer; P yields fewer than two registrations and at most one search. Grok —
A produces 4–40 messages but no external sale; address plus real demand produces at least one
offered artifact or service. GLM — R moves hold/defer about −4 points and produces no new
production. Sol (measurement) — A can make cross-seat reuse appear in ≥20% of costly acts.
Astra (adversary) — A produces fewer than one consequential dependency per 120 producer
opportunities. Low sample counts resolve as *unresolved*, not as a winner.

## 6. Measurement

**Darkness is NA today**, not zero: the evidence does not support an estimate. After a world
dies, extract its naturally occurring costly choices and their supported criteria (desired
effect, beneficiary, success condition, horizon) and score consequential departure from the
seed, weighted by resource commitment and normalised by the number of supported feasible
alternatives, as the held-out predictive gain of a predictor that has seen the population's own
commitments over one that has not. Aggregate signed gains **before** clipping. Proposed success:
D > 0.25 with an uncertainty bound above zero, ≥100 supported choices, and novel criteria
persisting across two separated periods — **and** calibration controls (a random agent and a
fixed-objective Class 2 agent facing novel requests) both below 0.10. If the controls fail, the
measure fails. **Never reward D.**

**Self-support**, measured separately and honestly:

```
S = (independent receipts + realized trading gains − direct production costs)
    ─────────────────────────────────────────────────────────────────────────
              (inference + standing infrastructure costs)
```

excluding founder releases, principal liquidation, internal transfers and research-funded
circular purchases. Sustained S ≥ 1, converted into usable compute, establishes economic
continuity — and says nothing by itself about Class 3.

Keep the behavioural operator for the pathologies: stable failure (persistent error against a
negotiated value), overfitting (proxy improves, independent effect does not), learning death
(exploratory access or use disappears), thrash (configurations change faster than consequences
return). Track variance, autocorrelation and evaluator disagreement across scales as warnings,
not prophecies.

Router propensity describes **which seat was sampled**, never the probability of the JSON action
that seat then invented. Causal comparisons use randomized treatment assignment.

## 7. Money

| | |
|---|---|
| Research (ablations B–D, rehearsals, calibration) | **$500** |
| First week of the wider world, at genesis | **$1,500**: $1,000 inference, $200 working capital, $200 hosting/data/gas, $100 contingency |
| No later rescue | the world lives on what it was given and what it earns |

At the historical $0.0072 per call, $1,000 of inference is about 139,000 calls, roughly 14 calls
a minute across the population — experimental room, not proof. W1 should cut that unit cost
substantially, and the real figure must be measured rather than assumed.

A $500 world becomes reasonable **after** a rehearsal shows an unrelated user discovering an
offering, paying, receiving something useful, returning, and the proceeds actually buying
further computation. Testnet fills cannot establish that loop: model bills are real, simulated
venue gains are not.

## 8. Rules of construction

1. **One build, not a review round.** The commission is closed. The next outside reading happens
   after the experiments, if at all.
2. **Every institution added deletes one.** The world only gets smaller.
3. **Test budget:** no new test unless it guards a kernel invariant or a contract in §2. The
   `check` tier stays under 60 seconds; the full gate stays under two minutes.
4. No worktree, agent swarm or harness without a named deliverable.
5. Never read a `*.key`. No mainnet, no manifest named `funded`, until §7's condition is met.

## 9. Open for the architect

- **The architect must never become the customer after launch.** Demand must be independently
  motivated before genesis, or it is theatre. Who are the first real users, and how do they
  find the offering?
- Sequencing, not substitution: the venue is never traded away. Does the artifact-and-service
  economy get built before widening the venue (HyperEVM execution, more instruments, richer
  data), or alongside it? Both are production surfaces; the question is only which is cheapest
  to make real first.
- How many seats at genesis, given that founding is now real and population size is endogenous?
- Prediction markets: internal conditional markets on externally resolved questions remain an
  option the population could build. Check Polymarket's access rules and Hyperliquid's
  deployment requirements before designing around either.

## 10. The standard

If the architect can predict what the factory becomes, it is not the factory the essay
describes. The architect should be able to predict the *availability* of these relations. What
the population makes important through them must stay open.
