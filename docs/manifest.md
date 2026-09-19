# Manifest keys

Every parameter the essay leaves open is a manifest key with a seed value; nothing
here is an objective for the population. A **hard cast** is fixed for the world's
life (it is hashed into genesis and no amendment reaches it); a **soft cast** can be
moved by the population through the charter. Keys are listed as their fix pass
introduces them; the polish pass will complete the table for the older keys.

The TOML manifest fixes the initial conditions. Money is stored as integer
micro-USD after exact decimal parsing. The entries below include the merged
round-three fixes to the existing contracts.

## Round-two W4: composition contracts (A1)

| Key | Type | Default / seed | Hard cast? |
| --- | --- | --- | --- |
| `assemblies[].accepts` | Nonempty array of event-kind strings | Required; shipped subscriptions unchanged | No: a registration may accept any kind, including return kinds |
| `assemblies[].emits` | Nonempty array of return-kind strings | Legacy labels expand once: producer → `ProducerReturn`, evaluator → `Verdict`, meta → `MetaVerdict`, antagonist → `Exposure`; explicit in `scripted.toml` | No: chosen by each registration; labels do not dispatch |
| `assemblies[].schemas` | Object mapping custom emits kinds to object schemas | `{}`; every custom kind requires a schema | No: population declares new kinds; an existing kind retains its meaning |
| `tools.max_depth` | Integer ≥ 0, never boolean or float | `4` | Yes: root depth is 0; zero disables children |
| `tools.max_children` | Integer ≥ 0, never boolean or float | `3` | Yes: per-request fan-out; zero disables children |
| `tools.max_tool_calls` | Integer ≥ 0, never boolean or float | `4` (the existing limit, now a manifest key) | Yes: per request; zero disables tool calls |

The assembly proposal uses the same accepts/emits/schemas contract. A custom
schema validates the returned payload, excluding the protocol fields `emits`,
`about_handle`, `register`, `requests`, `tool_calls`, `status` and `reason`. Custom returns receive
the feedback of the reward shape they declare, verdict feedback by default.
A changed schema requires a new kind name; built-in world or
kernel events cannot be impersonated. A producer may process its own event;
judgement against its own or an ancestor's output is refused. Public registrations
include ids and versions without identifying the author of a judged return.

`world.catalogue` lists each live assembly's `id`, `version`, `accepts` and
`emits`. `world.addressing` explains their use in `requests[].target`, retirement
and learner proposals. Every invocation receives its own assembly id in
`inputs.you`, including children. Ids and contracts are public; the earlier A8
statement that assembly ids are never disclosed is superseded. Models behind
ids, prompts, learner state, router menus and who judged whom remain sealed.

Venue and treasury write authority follows the decision chain. Every decision
in that chain must use a producing channel (`verdict` or `exposure`) and must
not emit `Verdict` or `MetaVerdict`. The writing decision must have an open
consequence account. Every decision in the writing chain, not only the writing
decision, must still hold an open consequence account.
A policy ballot binds no return kind and cannot write.
An author whose contract includes a judging kind is excluded from its own
subject's router, even if the contract also includes a producing kind.

Judging returns may include `about_handle`; omission selects the delivered
subject. A value absent from the decision queue falls back to an addressable
delivered subject with `about_handle.ignored` and `registration_feedback`.
An existing but forbidden handle is refused, not replaced. A requested judge
may address only its requesting decision or that decision's ancestors. The
ancestor self-judgement check still refuses those subjects, so this restriction
does not grant permission to judge the requesting chain. A payoff judgement on
a subject not chosen by the router also passes the hindsight check, including
a parent-selected subject. A fixed consequence, expired backstop or judgement
deadline beyond that backstop is refused. Judgement `return.refused` items
deliver their reasons in `registration_feedback`.

`tool.call.outcome` is `ok`, `failed` or `uncertain`. An unacknowledged venue
write is `uncertain` and retains its client id for reconciliation. An
acknowledged result with `error: null` is not a tool failure.

A multi-kind return must select `emits` on its first response, before any tools
or children run, and cannot change it on continuation. The kernel queue keeps
its original channel (`emits` for a sum of channels); `decision.contract` and
`decision.emits` record the alternatives and one-time selection. Runtime reads
and feedback expose the selected channel. Single-channel seed contracts keep
their original channels. Exposure also publishes the producer-shaped event used
by the shipped evaluator registrations. Judge cost accounts do not add novelty
trials to the forecast and meta-feedback trials they already receive.

`register: [{"kind":"retire","assembly_id":"eval-a","predicted_effect":{"card_id":"forecast_skill","direction":"increase","window":1}}]` proposes retirement
of the current version. It uses amendment eligibility, sortition, majority and
activation cadence, with the proposer and retirement target excluded.
`predicted_effect` is required and names a current measurable card.
Retirement ballots carry the delayed liability described under "Committee liability".
Retirement removes future routing and child admission, retains old handles,
accounts and feedback identity, and allows the id's next version to register.

Each depth has one continuation. It returns the final answer after its tool and
child results; additional requests at that continuation are refused. Descendant
costs accumulate against the original parent's remaining ceiling. A child's
ceiling is also capped by the parent's available compute. Children never spend
protected novelty compute. These are protocol semantics, not additional
population objectives.

`assemblies[].max_tokens` seeds `3000` for `eval-b` and `eval-c`, and `2500`
for `antagonist-a`, in testnet and the edition-one example. These are output
budgets, not guarantees of nonempty or well-formed model replies.

The deterministic scripted fixture reuses its existing call schedule: the third
registration slot installs a helper and a producer accepting `ProducerReturn`;
the fourth tool slot requests helper → grandchild with a catalogue tool; the
router-add slot also replaces the custom `Finding` router and proposes retiring
`eval-a`. The scripted provider also registers `scripted-fill-count`, later
names it in a card amendment, and registers a Blum–Mansour assembly learner.
Tick orders size from `world.wallet_balance_usd`, not venue equity.

## Round-two W3: disclosure, governance, the treasury

The W3 governance and charter keys are below; the W1
timing, pricing and immune keys follow under "Timing, pricing and immune
settings".

| Key | Type | Default / seed | Hard cast? |
| --- | --- | --- | --- |
| `treasury.max_venice_per_window` | Exact USD decimal string or integer, nonnegative | `"10"` (10,000,000 micro-USD) | Configured resource bound, fixed for a run; not amendable through metric cards |
| `treasury.cctp_forwarding` | `"never"`, `"on_empty_gas"` or `"always"` | `"on_empty_gas"` | Configured route rule, fixed for a run; absent or default keys leave the manifest hash unchanged |
| `treasury.max_forward_fee_usd` | Exact USD decimal string, nonnegative | `"0.30"` ($0.10 of headroom over the $0.20 quoted on both networks) | Hard bound on the on-chain forwarding fee quote per exit; a higher quote refuses before signing |
| `treasury.max_forward_fees_per_window` | Exact USD decimal string or integer, nonnegative | `"1"` | Per-reserve-window cap on forwarding fees quoted for submitted exits; a failed exit still counts |
| `treasury.forward_wait_windows` | Positive integer | `2` | Reserve windows a forwarded mint may stay unobserved before the exit strands recoverably; absent or default keys leave the manifest hash unchanged |
| `committee.seats` | Integer, at least 3 so the existing three core roles can be covered | `5` | Configured resource bound, fixed for a run |
| `committee.promise_resolution` | Finite positive number | `0.01` | Fraction of the frozen region's scale a promised move must clear to count; absent or default, it leaves the manifest hash unchanged |
| `charter.norms` | Nonempty array of names, or of `{ id, definition }` tables | Required for explicit charters; edition 3 carries definitions, editions before it carry bare names | Read-only for the edition. A bare name loads with an empty definition, so a charter surveyed before definitions existed keeps its content digest; `Charter.render` prints each definition under its norm |
| `charter.cards[].window.kind` | `"returns"`, `"forecasts"`, or `"windows"` | Required for explicit cards | Executable selector type; its value is population amendable |
| `charter.cards[].window.n` | Positive integer, never a boolean or float | Required; seed cost and well-formedness cards use `100`, forecast skill uses `50` | Population amendable sample horizon |
| `charter.cards[].window.per` | `"role"`, `"assembly"`, or null | Required in JSON; omitted in TOML means null. Seed cost and well-formedness use `"role"`; forecast skill uses `"assembly"` | Population amendable scope |
| `charter.cards[].answers_for` | `producer`, `evaluator`, `meta`, `antagonist`, `all`, or any registered emitted kind | Required | Population amendable pricing responsibility |
| Proposal `predicted_effect.card_id` | Current or proposed card id for amendments; current card id for connectors and retirements | Required; no default | Liability binds to a measurable card |
| Proposal `predicted_effect.direction` | `increase` or `decrease` | Required; no default | The promise graded against the baseline recorded at activation |
| Proposal `predicted_effect.window` | Positive integer count of closed reserve windows after activation | Required; no default | Population-authored liability horizon |

The existing `committee.min_settled`, `novelty.window`, `novelty.share`,
`novelty.trials`, prices, timing and clock parameters are disclosed in
every request's `world.mechanics`, at the values the manifest committed and an
amendment last activated. The two the runtime adapts live — the consequence mix
the sampling actuator raises and steps back, and the decay the immune controller
borrows on thrash — are published in `world.adaptive_scoring` instead, beside a
pointer back to their committed values; `mechanics` and `scoring` name that key
rather than quoting either number. The scoring block states the capped,
attributed formula recorded under "Observation units and attribution".

A rendered request opens with a stable prefix and puts everything that moves
after it. Round three (R3-E, GPT-6's third reading §8) makes that prefix exactly
two things: the **WORLD CONTRACT** wrapper — its text verbatim, with the five
fixed norms rendered from the charter object, so a ratified edition renders its
own definitions — and a **compact base capability index**, one line and one
price for every tool and one line for every proposal kind. Nothing else. It is
serialised once per runtime by `cortex/schematics.py:_stable_prefix_text`,
carried on the world block under `stable_prefix`, and reused byte-for-byte:
every request in a world renders the same string object, and a runtime restored
from a checkpoint recomputes the same bytes from the same restored state, which
is what DeepSeek's and OpenAI's automatic prefix caching keys on with no
`cache_control` marker. It is a function of the charter's norms, the tool set
with its prices and the proposal kinds, and of nothing else — a seat registered,
a card repriced or a charter edition bumped no longer breaks every cached prefix
in the world. The reviewer's own instruction: byte stability "does not require
copying every institutional description into that prefix". Measured on the
scripted world, the prefix falls from 52,447 rendered bytes to 8,239.

It is the head of the **first user message**, never the system message, and
that placement is a boundary, not a preference: the capability index publishes
one-line descriptions the population wrote for its own registered tools, and
everything after it publishes the rest — observation, predicate and work
descriptions, metric cards, the charter text — and the system role is where one
member's prose would outrank every other assembly's own prompt. The system
message is exactly the assembly's world-supplied `system_prompt`; no
population-authored text ever enters it. The cache hit this keeps is the
per-assembly one, which is where the volume is: an assembly's system text is a
constant, so each of its calls opens with the identical `system` message
followed by the identical prefix, and a provider keys on nothing more than that
identical leading sequence. Handle-scoped memory, where a world registers it, is
the one thing that precedes the prefix and costs that assembly the hit.

After the prefix come the `YOU` block, the `WORLD UPDATE` block, and then the
work. `WORLD UPDATE` is the world's moving facts in §8's order:
`observation_window` (the measurement window, the tick, and how fresh each price
source is), `changes_since_last_successful_delivery` (C2's coalesced fold, or a
statement that this request carries none), `execution_receipts` (the receipts
newly addressed to this seat, or that no addressed-receipt source is carried),
`charter` (edition, text, live cards with their prices and regions, pending
changes), `catalogue` (version and changed entries only), `public_observations`
(last closed window's values, pathologies, recent prints, the shared directory)
and `unavailable_observations` — every source that could not be read, with the
reason. No private state is in this block; a seat's own state appears exactly
once, in `YOU`. Everything else the world publishes — `inputs.you`, the event,
the pots, note counts, the reserve remaining, `tick_intervals`,
`registration_feedback`, `adaptive_scoring`, the tool, connector, work and
observation catalogues, the mechanics and the scoring formulas — is rendered
after those, inside `INPUTS`. A key of the world block is rendered in exactly
one of those four places: the partition is `PREFIX_WORLD_KEY` with
`PREFIX_SOURCE_KEYS`, `SEAT_WORLD_KEYS`, `UPDATE_WORLD_KEY` with
`UPDATE_SOURCE_KEYS`, and everything left over. A source key stays in the world
block, which is the runtime's own disclosure surface, and is rendered only
through the block that carries it. The controller re-prices every card at every
closed window, so the cards ride in `WORLD UPDATE` and never in the prefix.
Where the provider reports
it, `usage.prompt_tokens_details.cached_tokens` is recorded as
`usage.cached_tokens` on the `invocation` item, absent where it is not reported.
Cost metering is unchanged: OpenRouter's reported `usage.cost` already carries
the cache discount.

Edition 3 (contract C4) adds a `YOU` block between the stable prefix and the
work, and makes the institutional catalogue an index rather than a copy. R3-E
gives it §8's template, every slot serialised by the kernel and never by a
model: `seat`, `lineage`, `request` (this decision's own handle, deadline, cost
ceiling and liable budget, which the decider previously never saw), `clock`
(now, tick index, **tick interval in seconds** — a tick count is not a duration
until it is multiplied by this — and this seat's last successful delivery),
`working_state` (C1's head, exactly), `spending_authority` (entitlement, held,
available, unsettled bills and the next release with this seat's own share),
`provider_inventory` with its freshness, `venue_accounts` and
`pending_conversions` by custody, `runway`, `subscription` with its next
eligible tick, `open_commitments`, `outcomes` (`unread_count`, the inline window
oldest-first with each item's exact `outcome_id`, and `more`) and `directory`.

Where a source is missing a slot renders the string `unavailable`, never a
number: a venue read that failed is `{"status": "unavailable", "reason": …}` and
never an equity of zero or an empty position set, an unobserved provider balance
is `null`, and a burn rate observed over less than six hours is `insufficient
history` rather than extrapolated. `venue_accounts` and `pending_conversions`
defer to `runtime/custody.py:custody_view` where R3-B's typed custody is
present, and otherwise render the account read the runtime already performs,
marked `observed` or `unavailable`. Nothing in the block performs I/O.
The accounting facts are constant text and are published, once, with the rest of
the institutional disclosure under `world.accounting_facts`.
`world.seats` carries one entry per live
seat because one world block serves every request built in a tick;
`Request.prompt_text` renders the acting seat's entry and no other, so no seat
reads another's account. The provider inventory is the last treasury
observation the runtime already holds.

The capability index in the prefix is the one published copy: every tool's id,
one-line description and price, and one line per proposal kind. The full
`args_schema` of any tool and the full shape of any proposal kind are retrieved
by `catalogue.search`, whose result carries `models`, `tools` and
`proposal_shapes`. Nothing became undiscoverable — every id and description is
in the prefix — and the schemas a decision never reads no longer ride in front
of every decision.

The **OUTCOME CONTRACT** (§8, verbatim) is rendered once per request,
immediately after the outcome schema it is about: what an execution claim must
distinguish (`intended`, `submitted`, `settled`, `rejected`, `unknown`), what a
forecast and a fidelity objection must carry, what a pause must state, and that
a monetary quantity names its asset, its custody account and its unit.

The change is measured, not assumed: every `invocation` item carries `sections`,
the UTF-8 bytes rendered per prompt section (`stable_prefix`, `you`,
`world_update`, `request`, `inputs`, `propensity`, `outcome_schema`,
`outcome_contract`, `completion_criterion`, `total`), and the wake publishes
`prompt_sections` with the mean and total per section over every recorded
invocation. On the scripted world, before R3-E and after: `stable_prefix`
52,983 → 8,990, `you` 3,195 → 3,663, `world_update` 0 → 3,798, `inputs` 5,433 →
47,821, `outcome_contract` 0 → 1,315, total 61,826 → 65,802. The prefix that a
provider caches falls by 84%; the institutional disclosure it used to carry is
rendered once with the work, where a seat reads it at the moment it matters, and
the 6.4% the total rises is that disclosure plus what §8 added — the outcome
contract, the custody and subscription slots, the observation window and the
sources that could not be read.

### `calc`

A deterministic, unit-explicit arithmetic tool (`cortex/calc.py`), GPT-6's third
reading §7: the final roster did not meet "every critical arithmetic case", and
the repair it named is a capability rather than a better prompt. Five
operations — `notional(size, price)`, `fee(fee_bps` with either `notional` or
`size` and `price`), `funding(size, mark, rate)`, `carry(size, mark,
hourly_rate, hours, round_trip_fee)` and `margin(size, mark, leverage)` — all in
exact `Decimal` arithmetic quantised to six decimal places, with the unit in
every field name. Funding and carry state the venue's sign convention and
settlement period in the result: a positive `funding_usd` is what the position
pays. It prescribes no objective: `carry` reports `net_usd_positive`, a fact
about a subtraction, and never a recommendation. It is published wherever the
fixed primitives are (a resume included), priced at
`prices.tool_micro_per_call` where a world commits one and free otherwise, and
metered and ledgered like any other tool. It answers every fee, funding and
carry case of `scripts/calibrate_seats.py` exactly.

## Round-two W2: judges, consequences, the reserve

### `[evaluation]`

| Key | Type | Default | Cast | Meaning |
|---|---|---|---|---|
| `consequence_share` | float in [0, 1) | 0.3 | hard | Base weight of payoff standing in evaluator selection; the live actuator starts here. |
| `adversarial_share` | float in [0, 1] | 0.15 | hard | Cap on the router's probability mass over antagonist assemblies (A5). The essay's "minority" is a constraint, not a prize. |
| `sibling_share` | float in [0, 1] | 0.5 | hard | Share of the representative's meta score at which an unread cascade sibling settles (A14). |
| `sampling_step` | float in [0, 1] | 0.1 | hard | Step by which the consequence mix rises per divergent window and steps back otherwise (A14, the live sampling-rate actuator). |
| `sampling_cap` | float in [consequence_share, 1) | 0.7 | hard | Ceiling of the raised consequence mix (A14). |

### `[novelty]`

| Key | Type | Default | Cast | Meaning |
|---|---|---|---|---|
| `trials` | int >= 1 | 3 | hard | Settled consequences delivered to a population assembly before its protected trial ends (A13). Replaces `trial_invocations`, which counted model calls; continuations and children do not count. A learning-death window grants one more. |
| `max_lifetime_windows` | int >= 1 | 6 | hard | Reserve windows after registration after which the trial ends regardless of deliveries (A13). |

### Ledger evidence these keys produce

`route.excluded`, `tool.refused`, `consequence.refused` (A9); `exposure.settled` (A5);
`cascade.sibling`, `meta.consequence`, `meta.awaiting_consequence`, `sampling.raise`,
`sampling.lower` (A14); `novelty.release`, `novelty.grant`,
`novelty.grant_consumed` (A13). Edition 3's third round adds
`evaluation.unmeasured`, `verdict.unmeasured`,
`verdict.committed_without_payoff`, `receipt.execution`, `receipt.learning`,
`receipt.commitment`, `receipt.adjudication`,
`fidelity.adjudication_queued`, `fidelity.adjudicated`,
`fidelity.finding_refused`, `fidelity.challenge_opened` and
`fidelity.challenge_skipped`.

A closed lot's realised P&L is credited once (edition 2, cold audit F7). A
handle that opens and closes its own lot receives the whole of it, net of its
opening fees, funding, other charges and closing fees. A distinct closer takes
the part its exit notional contributed, `pnl × exit_price / (entry_price +
exit_price)`, net of its closing fee; the opener keeps the rest, net of its
opening charges. The two credits sum to the lot's P&L and never both hold it in
full. A liquidation has no closer: the liquidated opener carries the whole P&L
and the liquidation fee. `return_paid_off` reads the result credited to a
return as opener or closer, so a trade cannot pay off twice.

A verdict is also a prediction that the judged return will not be blamed by the
charter. It is scored against the share of its window's blame the pricing pass
attributed to that return, and the score joins payoff skill in the judge's
standing (`verdict.consequence`). The outcome is fractional, `1 - share`, and
the prevalence baseline the judge is scored against learns that same fraction
(`record_fraction`, once per judged return however many judges it has), never a
rounded `share == 0` (edition 2, cold audit F3): a judge that only repeats the
constant share of blame every return carries shows no excess skill. Every
verdict about one return is scored against the base rate as it stood before
that return's outcome entered it. A window that has not closed by the
consequence backstop, or whose attribution evidence was released before it could
be read, judged nothing: there is no fact either way, so the commitment is closed
out unscored (`verdict.unread`). It moves neither the judge's standing nor the
base rate of unblamed returns. A missing fact is never performance.

### Evaluation is a commission (edition 3, third round)

Every evaluator request carries a `commission`: the subject, the observation
scope, the evidence horizon in events, and the budget in micro-USD. What comes
back may be a verdict, or one of two other complete answers.

- **Unmeasured.** `{"status": "unmeasured", "reason": ...}` settles the
  commission `INAPPLICABLE` under `unmeasured-v1` (`evaluation.unmeasured`). No
  score, no price, no standing, no base rate: the work was done and the finding
  is that there was nothing here this evidence could measure. It is not a low
  score and not a censored decision somebody failed to answer.
- **Declined.** `{"status": "cannot", "reason": ...}` settles under
  `declined-v1`. The seat is charged the call it made and nothing else. There
  is no activity quota and no profit quota anywhere in the runtime.

The runtime reaches `unmeasured` on its own in three places. A judged return
that committed to nothing — no stated claim, counterfactual, observation rule,
resource decision or accepted promise, in its return or in its inbox-visible
commitments — cannot be judged against anything, so the commission concludes
unmeasured rather than scoring how prudent the return looked. Unfamiliar work
keeps its exploratory allowance: while a seat is inside the novelty share the
population granted it, its returns stay evaluable whatever they say. A verdict
whose normative window closed unread settles unmeasured instead of falling back
to its payoff forecast, and every meta that conformed to it settles unmeasured
with it rather than timing out at zero for a fact the runtime owed it and never
delivered.

`payoff` is an optional field. A judge with nothing to say about the kernel's
consequence predicate is not forced to invent a number for it and is not
penalised for leaving it out; its verdict is still committed as a normative
claim (`verdict.committed_without_payoff`) and decided by whatever facts the
world produced about it — its normative outcome, its payoff forecast, or both.
When neither exists there is nothing to be right about
(`verdict.unmeasured`).

Easy questions do not pay. A forecast on a predicate whose prevalence baseline
is at or above 0.95, or at or below 0.05, over at least 20 recorded
observations, settles observed and unscored under `uninformative-baseline-v1`:
the observation still enters the base rate, the learning receipt carries
`score: null` and the reason `uninformative_baseline`, and no standing moves.
The bound is on the question, not on the forecaster.

### Four settlement objects

`settlement/receipts.py` keeps four things apart, each addressed by a content
id of its own and each written to the diary before it is addressable
(`receipt.execution`, `receipt.learning`, `receipt.commitment`,
`receipt.adjudication`). An **execution receipt** is a fact the world produced
— a fill, a refusal, a charge, a transfer, a program result, a failed delivery
— and carries no score. A **learning receipt** is one assessment of one
decision: the decision handle, the scoring rule and its version, the
observation horizon, the outcome, the score, the sampling record; its score may
be `null` with a reason, and an assessment that could not be made is never a
zero. A **commitment** is a promise with a responsible principal, a deadline,
an observation rule and the conditions under which it is unobservable through
nobody's fault. An **adjudication** is a contestable interpretation: a value, a
measurement, evidence, a finding and the adjudicator who made it.

### A fidelity objection is an adjudication

An accepted objection becomes an open `Adjudication` the moment it is made, and
it is queued (`fidelity.adjudication_queued`) for an adjudicator drawn from the
seats that judge — never the judge that wrote the verdict, and never a seat the
challenged card answers for. With nobody independent available the claim stays
open: an interested finding is worse than none. The adjudicator answers with
`fidelity_finding: {upheld, reason}` on its own judging return. The finding
produces a learning receipt for the objector, scoring the uncertainty it stated
against the finding by the same proper score as anything else
(`fidelity.adjudicated`), and, when the objection is upheld, opens a
`challenge` proposal for the card through the population's ordinary
registration route (`fidelity.challenge_opened`). Nothing here reprices a card:
the committee does that, or nobody does.

### The commissioned-child-judge route is closed

A judging contract cannot be requested as a child. A requested judge may only
address the chain that requested it, and nothing judges its own output or its
ancestors', so the route could be bought, paid for and never executed. It is
refused before a decision is opened or a call is made (`requests.refused`), with
the reason in `return_feedback` and in the catalogue's addressing text. Judging
work reaches a seat the three ways it always did: the router's sampling, the
adversarial share and the cascade.

### Cascade separation is time and completed evidence

A tier's window covers a **duration**, not a number of arrivals: the jittered
`timing.min_ratio` the manifest already precommits, counted in observation
windows (the tick interval) rather than in messages, drawn once when the window
opens and never redrawn inside it (`cascade.arrival` carries `window_ns`,
`opened_ns` and `elapsed_ns`). The window releases when its duration has
elapsed and some of the evidence inside it has completed — for a verdict, that
the return it judged has an outcome. Every arrival is named in the released
report, so the sibling share still reaches it, and only completed evidence is
averaged. Three judgements arriving in the same nanosecond are three arrivals
in an empty window and trigger nothing. Execution facts and safety actions never
enter the cascade and are never slowed by it.

## Exact measurement

`returns` selects the latest `n` completed invocation responses in each selected
scope. A continuation's cost belongs to its invocation, and a child invocation
is a separate response. There are two cost observations. `cost_per_return`
keeps its old meaning for old charters: only successful responses in the
selected rows contribute to the mean. `cost_per_attempt` (edition 2, cold audit
F4) is the mean over every selected response, failed and malformed ones
included, so an expensive failure cannot hide inside the tenth the well-formed
floor tolerates; over global closed windows it uses every return the window
made. For both, a retained-storage charge is selected beside the responses as
a cost row of the decision that holds it: it adds to what those responses cost
and is never divided into as one of them, so paying rent can only raise a cost
per response. The `n` are counted over responses alone, before any charge joins
them, and the charges that join a selected horizon are the ones metered in the
same measurement windows as its selected responses, so a charge never fills a
response slot, never displaces a response from a full horizon and never
supplies the support a short scope lacks. No other observation selects one.
Well-formedness uses all selected responses as its denominator. `tool_calls`
is the mean attempted tool calls per selected response, failures included, as
its card prose always said (edition 2, cold audit F5): ten returns of one call
each measure one, not ten; over global closed windows it divides the window's
attempted calls by its invocations. The other supported return observations
are `noop_share` and `revision_rate`.

`forecasts` selects the latest `n` resolved forecast records in each scope.
`forecast_skill` uses paired Brier skill against the baseline as it stood before
each outcome, not lifetime standing. The other supported forecast observations
are `verdict_mean`, `verdict_std`, `consequence_paid_off_rate` and `censored_share`.
Censored records count toward the selector but not a scored outcome mean.
`verdict_mean` and `verdict_std` select by the judged subject's assembly or
role. `forecast_skill` selects by the forecaster. Preflight uses the same
subject-aware forecast row construction as runtime measurement.

`windows` selects exactly the latest `n` closed reserve windows. Global rates
are recomputed from their combined sufficient statistics, rather than averaging
window rates with unequal denominators. Per-role and per-assembly window scopes
require an observation supported by the corresponding response/forecast rows.
Other catalogue observations, such as turnover, are measurable over global
closed windows only. Unsupported combinations are refused before a vote.

Null scope pools the factory. Role or assembly scope groups each entity's own
samples and, unless `answers_for=all`, selects entities with that role. A scope
with fewer than `n` responses or forecasts is unavailable. A windows selector
requires `n` closed windows. The controller receives the equal mean of supported
scope measurements; private entity values never enter the public topology view.
Previous-cost-median bounds use the same selector's per-response cost samples.
New horizons may need to warm up when retained history is shorter than a newly
adopted card. The buffers and their active measurements survive resume.

## Committee liability

Eligibility counts distinct router-chosen decision handles with settled
consequences, including terminal meta consequences and conformity settlements.
Fast or verdict scores alone do not qualify. Child requests and policy ballots
cannot manufacture eligibility. Proposers cannot vote on their own proposals.
Retirement targets cannot vote on their own retirement. Identical patches,
including unchanged prices and clock intervals, are refused before spending
the proposal reservation or seating a
committee. A proposed observation cannot overlap another live card's pricing
role, including an `all` binding.

Charter validation uses the runtime observation book, including population
registrations. `charter.propose.observation_bindings` freezes observation ids
and versions and survives resume. If an added or replaced card's observation
is superseded or withdrawn before activation, `charter.refused` names the card
and voted and current versions. Its ballots are censored. Refused amendments
and stale retirements leave the cadence queue with `charter.cadence_refused`
without consuming an activation boundary.

A valid vote opens a policy decision under `assembly:<id>`, retaining that
identity across proposals. `policy.promised` freezes the named card's selector,
observation version and implementation, and acceptable region. An unavailable
region is resolved at activation. Its baseline is measured at activation as
evidence. If activation opens window `i`, horizon `k` settles at the close
of `i+k-1`.
The outcome is whether the promise held against the recorded baseline, not
whether the region is satisfied. A move counts once it clears
`committee.promise_resolution` of the frozen region's scale. A card outside its
region at the baseline kept the promise only by moving in the promised
`direction` that far; a card already inside kept it by staying inside without
moving against the promise. A vote that backed a change whose value went the
wrong way is wrong even if the region still holds. Yes votes predict a kept
promise; no votes predict its negation. The score is `1 - (vote - outcome)^2`,
recorded as `policy-promise-brier-v2`; `policy.outcome` carries `baseline`,
`direction`, `resolution`, `value` and `y`. Amendments, connectors and
retirements use this same liability. Abstentions, failed proposals and missing
baseline, measurement or region evidence are censored, with no fast reward.
Subsequent ballots receive that assembly's private policy-return history.

## Venice transfer and first move

`treasury.transfer(direction="to_venice", usd="5")` moves the protocol's fixed
$5 tranche from the reserve into Venice credit. The $5 amount is the existing
x402 protocol constraint, not a new optimiser setting. Submitted tranches count
against the window budget, including uncertain or later failed submissions.
The budget resets only when the reserve-window index advances, and survives
resume. A pending or stranded transfer prevents another transfer.

The journal records the quote, unsigned authorization, nonce and expiry before
submission. Retries sign that same authorization. Confirmation requires the
canonical USDC debit for that nonce: the unique successful `AuthorizationUsed`
whose receipt transfers exactly the tranche from the reserve to Venice's payee.
The Venice credit balance is advisory (edition 2, cold audit F2): a balance is
a stock, and usage between purchase and confirmation lowers it without
contradicting the purchase, so the observed credit, `credit_before_micro`, the
amount, the balance's source and the diary's own `metered_usage_since_micro`
are recorded in the confirmation's evidence and never decide it. Unknown
evidence keeps the principal held; it is not converted into a second payment
or a claimed arrival. The mainnet adapter requires wallet-bound Venice credit and Base USDC;
the testnet rail refuses that live route. The offline fake rail exercises the
same journal, principal hold, credit view and budget.

The exit route `to_reserve` burns USDC on HyperCore and mints it on Base. Its
Core gas is spot HYPE in the venue account, which the population buys itself on
`HYPE/USDC` (seeded in both the funded draft and testnet as the physics of the
exit route); HYPE charged as `nativeTokenFee` is not a fill, so runtime spot
inventory may exceed the venue balance and an oversized sell is refused. At
`prepare("withdraw_burn")` the world reads the reserve's own Base ETH balance:
with ETH and Base gas budget for one mint it self-mints (`data = "0x00"`),
otherwise it sends empty data so Circle's forwarder mints on Base and deducts the
fee that `CoreDepositWallet.calculateCrossChainWithdrawalFee` quotes on-chain
before signing. That quote is the burn message's `maxFee`, bounded by
`treasury.max_forward_fee_usd` and by `treasury.max_forward_fees_per_window`,
so an executed fee above it can never confirm; the amount minimum
(`withdrawal_fee` plus the signed branch's `maxFee`) is applied again at
`prepare` against the route actually signed, so a gas-price or balance flicker
between preflight and signing refuses with a ledgered reason rather than burning
less than the fee cap. Loading refuses a manifest whose `withdrawal_fee_usd`,
`cctp_max_fee_usd` and `max_forward_fee_usd` together exceed
`max_transfer_fee_usd`, so a forwarded exit's mint step always fits the transfer
fee cap after the principal burned. `treasury.cctp_forwarding`
pins the rule (`never` keeps the old ETH requirement; `always` forwards). The
choice, the quote and the balances read are public in `treasury.gas_route`
before anything is signed; a zero or over-cap quote refuses with a ledgered
reason. The forwarded mint step observes Circle's finalized `MessageReceived`
for the burn's nonce and the exact USDC credit, sends nothing and books the fee
as USDC, never as native gas; while it waits, a reserve that later holds ETH may
deliver the unclaimed message itself (`destinationCaller` is zero), which is
what `factorylab treasury advance` re-evaluates each tick. If that self-mint
reverts because Circle delivered first ("Nonce already used"), the step
re-checks the transmitter's consumed-nonce record and confirms the forwarder's
finalized credit, booking only the reverted transaction's gas, instead of
stranding money that arrived. The wait is bounded: a forwarded mint still
unobserved once `treasury.forward_wait_windows` reserve windows have opened
since the wait began is stranded through `treasury.failed` with reason
`forwarded mint not delivered within treasury.forward_wait_windows`, the wait
record (`waited`) and `recoverable: true`; such a strand keeps its principal
hold but leaves the transfer slot, so new transfers are admitted, and whenever
no transfer is in flight a tick re-checks it exactly as during the wait (the
forwarder's delivery, or the reserve's own self-mint of the still-unclaimed
message) and, on a reference, ledgers `treasury.recovered` and completes it
through the same steps; parked strands are listed in the pots view as
`stranded` (`transfer_id`, `stranded_micro`, `reason`, `since_ns`) and are
checkpointed with their holds. While that mint waits,
the observer keeps the last finalized Base block it scanned in the transfer's
pending reference (`scanned_to`, checkpointed and replayed on resume) and pages
`MessageReceived` logs only from the block after it, at most `FORWARD_SCAN_PAGES`
(40) fifty-block pages per tick, so a long wait costs a bounded number of RPC
calls per tick instead of a rescan from the burn. The pots view the
population reads carries a `gas` block: `core_hype`, `core_hype_required`,
`base_eth_wei`, `base_gas_remaining_wei`, the quoted `forward_fee_micro`, the
`route` the next exit would take, `minimum_micro`, and `refill_ready` with the
exact `blocked_by` reason; while a transfer is pending the money pots stay the
cached observation but the `gas` block is re-read every refresh (journaled as
`treasury.pots` with `pending: true`) and names the transfer in flight as
`blocked_by`, beside `pending_reason` and `pending_since`. A CCTP message whose
nonce is zero is never treated as consumed: `CCTP.consumed` refuses it. The
reverse direction `to_venue` needs reserve Base
ETH and HyperEVM HYPE and is refused with a public reason without them; nothing
acquires that gas.

`factorylab reserve topup --usd 5` is for the pre-launch seed. It refuses before
loading credentials or constructing a payment client when a world journal exists
under the working directory, or at an explicit `--ledger` path. Run it from the
world workspace; this is a workspace journal check, not a machine-wide search.

The draft survey accepts any number of cards returned within the existing model
output budget, with no card-count cap. It withholds existing cards from both
proposals and ballots, preflights each candidate through the pricing measurement,
includes `answers_for` and typed windows in the exported TOML, and records the
surveyed roster hash. `worlds/edition1-example.toml` is only a schema migration of
the historical example. The experimenter must re-draft edition 1 with the actual
launch roster before launch; the drafting script does not ratify a new edition
or run the paid survey.

A mainnet Hyperliquid manifest requires an explicit `[charter]`; testnet may
use the seed charter. `charter_explicit` records admission provenance and is
excluded from the canonical manifest hash.

A mainnet manifest is also refused at load unless `exchange.client_namespace` is
set and its `[charter]` carries `ratified_sha256` and `roster_sha256`, the values
`scripts/ratify_charter.py` wrote as the artifact's `charter_sha256` and
`roster_sha256` comments: the loaded cards must hash to the first and
the manifest's own assemblies and models to the second, so a funded launch cannot
run an edited charter or a different roster. Both fields are admission provenance
and, like `charter_explicit`, are excluded from the canonical manifest hash;
testnet manifests omit them.

Testnet `treasury.reserve_address` is the public checksummed address
`0x1228e5620944a79D268Afc7522E00891526EdEBb`, not a placeholder.

`treasury.insolvency` records entry into or exit from an unaffordable-compute
streak and the count reaching `treasury.insolvency_events`. Intermediate
unaffordable events advance the count without another item.

OpenRouter and Venice failures retain their provider class in invocation and
replay records. A provider failure is unbilled only when `sent` is false or
the provider returns a 4xx rejection. Missing credentials, failed name
resolution, refused connections and certificate verification failures establish
non-dispatch. A timeout or dropped connection after possible dispatch remains
billing-uncertain and commits the reserved ceiling. `io.result` retains
`status` and `unbilled` so replay preserves the same accounting.

## Timing, pricing and immune settings

The canonical manifest is recorded with its hash in the ledger's `Launch` event.
`factorylab versions` verifies that record against genesis and uses its immune
settings. A historical diary without those settings needs explicit analysis
parameters; the observer never substitutes a second set of thresholds.

| Key | Type | Seed default | Hard cast? |
| --- | --- | --- | --- |
| `timing.min_support` | positive integer, at most `timing.cadence_sample` | `30` | Yes: settled samples required before estimating p90; a larger support than the retained sample could never be reached, so it is refused at load. |
| `timing.cadence_sample` | positive integer | `200` | Yes: retained consequence-latency sample length (latencies in world ticks). |
| `timing.min_ratio` | integer, at least 3 | `3` | Yes: cascade and governance separation. |
| `evaluation.consequence_backstop_events` (or `consequence_backstop_ticks`) | positive integer, in world ticks | `200`; scripted worlds `20`; testnet `60` | Yes: consequence horizon and conservative governance period floor. |
| `evaluation.verdict_timeout_events` (or `verdict_timeout_ticks`) | positive integer, in world ticks | `20` | Yes: how long a judgement waits for its judge (a verdict for a producer return, a meta verdict for a verdict) before it is censored. |
| `prices.penalty_cap` | finite number strictly between 0 and 1 | `0.5` | Yes: maximum penalty before attribution. |
| `prices.min_blame_share` | finite number in [0, 1] | `0.1` | Yes: floor on one decision's share of a generic (non-attributable) violation; absent from the manifest hash at its default. |
| `immune.k` | integer, at least 2 | `3` | Yes: consecutive windows or changes required for diagnosis. |
| `immune.bins` | integer, exactly 3 | `3` | Yes: inside, up to one scale unit outside, more than one unit outside. |
| `immune.registration_bins` | increasing nonnegative numeric array | `[0, 2]` | Yes: zero, 1–2, 3+ registrations. Values equal to a cut enter the lower bin. |
| `immune.revision_bins` | increasing nonnegative numeric array | `[0]` | Yes: zero versus positive revision. |
| `immune.tv_threshold` | finite number in (0, 1] | `0.2` | Yes: behavioral version boundaries, not the thrash predicate. |
| `immune.gap_threshold` | finite number in (0, 1] | `0.8` | Yes: the operator's `durable` readout, not an additional pathology gate. |
| `immune.gain_step` | finite number in (0, 1] | `0.05` | Yes: exploration-gain adjustment. |
| `immune.gamma_max` | finite number in (0, 1] | `0.5` | Yes: exploration-gain ceiling. |
| `immune.decay_step` | finite number in (0, 1] | `0.1` | Yes: extra price decay for the window following thrash. |

These launch settings are immutable parameters of an experiment. Effective
prices, gain, diagnoses and the currently negotiated tick interval remain runtime
state. Relief halves the effective lambda on violated cards for one window; it
preserves the controller's accumulated price and previous violation. That state
resumes with the controller. Repeated failure can renew relief, while underlying
pressure continues to ratchet.

## Timing interpretation

The two evaluation horizons, `verdict_timeout_events` and
`consequence_backstop_events`, count **world ticks consumed**, not internal events.
The runtime's internal event counter advances for every fill, verdict, meta verdict,
watcher firing and world update, about twenty times per tick in the scripted world,
so a horizon counted in it lasted a fraction of what its number said: a 20-event
verdict timeout was about one tick, shorter than the cascade window that releases
verdicts to the metas, and nearly every evaluator decision was censored before a
meta could read it. The keys keep their names and their numbers, now read as
ticks; `verdict_timeout_ticks` and `consequence_backstop_ticks` are the same keys
spelled for their unit (a manifest that gives both spellings must give one
number). The judgement deadlines the decision queue enforces were already
computed from these numbers times the tick interval, so the two now agree.

The shipped testnet manifest sets `tick_interval = "120s"` and
`evaluation.consequence_backstop_events = 60`. With `timing.min_ratio = 3`,
the conservative activation floor is 180 ticks, or six hours at the declared
tick interval. Both scripted manifests use a 20-tick backstop so the
500-event demonstration can activate a card amendment and evaluator retirement
on separate boundaries.

All measured latencies are `settled_event - opened_event` on the cadence's clock,
which is world ticks consumed (the ledger's `cadence.*` items keep their field
names). The ledger also retains nanoseconds as provenance, but nanoseconds never
determine the measured period. The period is
`max(backstop, supported_p90, oldest_outstanding_age)` in ticks; unsupported p90
contributes nothing. Multiply by the current tick interval for the corresponding
duration. Both that duration and `min_ratio * period` fresh ticks must pass after
the previous activation. Activations at one boundary therefore cannot chain.

`LiveClock` retains the latest 64 delivered tick gaps and exposes their integer
mean through `measured_interval_ns()`. Before two ticks it returns the declared
interval. `intervals()` returns `declared_ns`, `measured_ns` and `samples`.
Governance conversion uses the delivered gap mean from `measured_interval_ns()`
once two ticks have been sampled, so an overrunning loop lengthens the published
period and the activation gate instead of understating them; the declared
interval prices a simulated clock, an unsampled one, and any period whose
declared interval has since changed. `world.tick_intervals` publishes that pair
and its sample count; `world.clock.tick_interval` remains the declared interval.
Resume preserves
the event budget but starts a fresh gap sample and drops the wall-clock deadline.

The spec's p90-only estimate and its zero-timestamp reproduction pull in different
directions: many same-tick settlements can have small positive event latencies.
Keeping the already declared backstop as a floor satisfies the reproduction and
the essay's requirement that governance relate to “the periodicity of the
factory’s slowest loops” (Chapter II, IV.c). Fast completions cannot establish that
an unobserved slow process has become fast.

## Observation units and attribution

The catalogue publishes `unit_range` and its width, `scale`; card prose cannot
change either. Bounded fractions and scores use [0, 1], score differences use
[-1, 1], and standard deviations of unit scores use [0, 0.5]. Unbounded counts
and ratios use one count or one base quantity as their unit interval [0, 1];
cost per return uses one dollar [0, 1,000,000] in micro-USD; signed dollar P&L
uses [-1, 1] USD. These are unit definitions, not acceptable regions or clipping
bounds for seed observations: larger and negative observations remain measurable.
Registered observations must return within their declared range.

Pricing normalises a card by its own bound magnitude for a one-sided region,
or by its width for a band. A zero one-sided bound falls back to the
observation's declared unit width. The resulting scale is frozen with the
card's region. Doubling a positive 500-micro-USD cap therefore has violation 1.

For card j, `v_j = distance_outside_region / card_region.scale`, and
`S = sum(lambda_j * v_j)`. A settlement receives
`min(S, prices.penalty_cap) * share`. When cards measure different quantities,
`share = sum(lambda_j * v_j * share_j) / S`, or zero when S is zero.

Cost shares use the card's selected scopes. For `cost_per_return` only
successful returns own cost; for `cost_per_attempt` every invocation's cost is
spent and owned, failed ones included. Each selected row contributes its cost
divided by the count of responses the observation divides over in that scope,
so a retained-storage charge adds its own cost to the scope it is held in and is
never one of the responses that count is taken over; a scope with no response
of its own is measured nowhere and attributed nowhere. The contributions are
normalised across supported scopes. Evaluator and meta cost cards therefore
charge those roles. Global window cost retains the producer-cost sufficient
statistics. Tool attempts and turnover use the decision's contribution divided
by the window total. A lower-bound well-formedness violation is allocated by
malformed invocations, so a correct return does not pay for someone else's
malformed one; an upper-bound violation uses well-formed invocations. A zero
attributable total contributes zero. Other observations use `1/n` decisions
for the card's role (or all roles for `answers_for = "all"`), counting the
decisions that responded in the window and not one whose only entry there is a
retained-storage charge, and that generic share never falls below
`prices.min_blame_share` (edition 2, cold audit F6): splitting participation
across many decisions cannot dilute what each one carries of a violation below
the floor. The generic share is `max(min_blame_share, 1/n)`, so two decisions
still carry a half each; the floor bites only once `n` exceeds its reciprocal.
Attributable observations (cost, well-formedness, tool attempts, turnover) keep
their exact shares. The final score is `clip(raw_score - penalty, 0, 1)`.

Closed windows retain their observations, regions, contributions, and the cards
and prices of the edition in force at the close, for delayed settlements. A
closed window is therefore priced by the edition that measured it: an amendment
activated at a window boundary, which takes effect after the close, cannot
remove or restate a card out of what that window already attributed, so a
verdict or a late settlement from it keeps the blame the window assigned.
Before a window closes, the most recent closed observations supply
pressure and the current window's observed contribution totals supply shares.
A cost settlement inside its own window reads the selected returns as they
stand, falling back to observed contributions when no shares are available.
Only delayed settlements use that window's frozen `closed_shares`.
A settlement cannot depend on future returns. Each penalty item records the
terms, window identifiers and shares actually used. Historical windows are
released when no unresolved decision needs them.

Fixed pathology cells include the priced card dimensions and the two activity
dimensions. The immune organ uses pricing's typed card measurements, including
unavailable support, and frozen `closed_regions`, not raw observation-id values
or later live regions. Raw reward channels remain available for retrospective analysis.
Adding a card starts its support history; removing one drops that dimension
without clearing surviving evidence. Thrash requires k consecutive changes with
no compliant window. Stable failure requires k same-cell windows with a common
violated card. Learning death is the frontier gone, not a count of edits (the
essay: the surplus-generating frontier extinguished or quarantined). It requires
k same-cell windows with zero registrations and revisions, no improvement in
consequence outcomes over those windows (the least-squares slope of the
paid-off rate and of realized P&L both flat or falling; an unmeasured series
never improves) and a compliance the cards cannot vouch for (in some window of
the tail a card is violated, unmeasured or without a region; a charter with no
measured card cannot show compliance). A stable, compliant organisation is not
learning-dead, nor is a stable one whose outcomes are improving. The window
profile carries `paid_off` and `realized_pnl` for this, and each `immune.window`
item publishes the `frontier` evidence (`quiet`, `improving`, `holding` and
the two slopes). Learning death's only response is that flag: the reserve reads it
at the next window boundary and grants one extra novelty trial per assembly for
the window that opens. A grant is spent by the first consequence delivered to an
assembly beyond `novelty.trials`, and whatever is unspent expires at the next
boundary, where the flag must be raised again to re-issue it. That single grant
is part of the novelty lifetime policy (A13): ledger evidence `novelty.grant` and
`novelty.grant_consumed`.

## Round-two W5: propensity and measurement (A10, A11)

Neither section adds a manifest key: the spec supplies no number for either, and
nothing here tells the population what to optimise. What they add are kernel
resource bounds of the same class as the existing registration caps (prompt
length, tool source length, tool timeout), stated here so they are readable in
one place. All are fixed in code for the world's life.

| Bound | Where | Value | What it bounds |
| --- | --- | --- | --- |
| `MAX_DECLARED_ACTIONS` | `cortex/request.py`, `cortex/registration.py` | `32` | Actions in one declared propensity, and in one registered assembly action set |
| `MAX_ACTION_ID_CHARS` | same | `64` | Length of one action id; the ids themselves are the population's |
| `PROPENSITY_TOLERANCE` | `cortex/request.py` | `1e-6` | How far a declared distribution may sum from one before it is refused |
| `MIN_DECLARED_MASS` | `runtime/propensity.py` | `0.05` | Minimum recorded mass on the chosen action after normalisation |
| `MAX_WORLD_SAMPLES` | `runtime/observations.py` | `1024` | Retained samples per public world series |
| `MAX_OBSERVATION_CODE_CHARS` | `runtime/observations.py` | `8000` | Source length of a registered observation (the tool source bound) |
| `MAX_OBSERVATION_DESCRIPTION_CHARS` | `runtime/observations.py` | `500` | Description length (the tool description bound) |
| `OBSERVATION_TIMEOUT_S` / `OBSERVATION_CPU_S` | `runtime/observations.py` | `5` / `2` | Wall and CPU seconds for one observation run: the tool jail's own ceilings |

### A10: the deciding agent's propensity

Every decision now carries two propensities. The first is unchanged: the
router's distribution over which assembly to wake, sampled by the kernel and
replayable from its seed (`source = "sampled"`). The second is the woken
assembly's own distribution over its own actions (`source = "declared"`), logged
on the same handle as `decision.propensity` evidence. Nothing in the kernel
sampled it, so it carries no reproducible seed; what is checked is that it is a
distribution and that the action actually taken carries positive mass in it.

The action taken is named by the kernel, in the vocabulary published as
`world.action_labels`: `hold` or `<side>:<COIN>:<size band>` for a producer or
antagonist — the size band buckets the declared size in base units into `xs`,
`s`, `m`, `l`, `xl`, so that sizing is a decision the declaration can name —
`verdict:<q>` and `conformity:<c>` rounded to one decimal for a judge, and
`malformed` for a return that did not parse. A return that declares nothing, or
declares something that is not a distribution or omits the action it took, is
recorded degenerate — that action at 1.0 — and the reason reaches the population
in `registration_feedback`.

Producing action labels include accepted or uncertain venue and treasury tool
effects and successful child requests, followed by the final answer's action.
Rejected writes add no effect label. Orders use the same banded label through
tools or final answers. Other effects use `close:<COIN>`, `cancel:<COIN>`,
`leverage:<COIN>`, `transfer:<direction>` and `request:<assembly id>`.
Multiple parts join with `+` in execution order, truncated to 64 characters.
A trade through a tool followed by `hold` therefore retains its trade label.
Positive declared mass below `MIN_DECLARED_MASS` is raised to `0.05` after
normalisation, with other probabilities rescaled and `propensity.floored`
recorded. Zero mass remains a refused declaration.

The declared propensity travels forward on the request about that decision: the
`ProducerReturn`, `Verdict` and `MetaVerdict` events carry it, and the judge's
`Request` carries it as a first-class field rendered in its own `PROPENSITY`
block. This is the essay's single exception to privacy (II.I.b), so nothing else
of an agent's local state moves with it.

A `{"kind": "learner", "assembly_id", "learner", "actions", "gamma"}`
registration gives one assembly a learner over the action set it declares.
Blum--Mansour needs one copy per action before the first round, which is why the
set is declared at registration rather than inferred. The learner proposes; the
assembly decides; the reward that settles the decision trains the learner
off-policy through the declared propensity, via the same snapshot machinery the
routers use. Ledger evidence: `propensity.refused`, `propensity.unlearned`,
`propensity.learned`.

A learner's action set is fixed at registration: include `hold` and every
`<side>:<COIN>:<size band>` label the assembly may take, because a decision
outside that set produces `propensity.unlearned`.

Children open under the parent's router actor and their settled scores train
that router when it holds the target. A target outside its universe routes
feedback to a router whose universe holds the target, found through the
target's accepted kinds. If none exists, feedback goes to the requesting
assembly's learner on `request:<target>`. `request.settled` identifies the
fallback learner trained; `propensity.unlearned` records unavailable or failed
learning instead of silently dropping the score.

### A11: registrable observations

A `{"kind": "observation", "id", "description", "unit", "range": [lo, hi],
"code"}` registration adds a measurement. `code` defines `observe(facts)` over
the public per-window facts as JSON — the same facts the seed observations
compute from, with the per-decision attribution (`decisions`, `closed_values`,
`closed_regions`, `closed_shares`) removed and sets rendered as sorted lists.
It runs in the tool jail under the tool limits above, and is admitted only after a preflight run on
the last closed window returns a finite number; the preflight and its reason are
ledgered as `observation.preflight`. Nothing is registrable before a window has
closed, and nothing is registrable on a host without a jail.

The declared `range` bounds supported outputs and supplies the fallback unit
width for zero-bound cards. Pricing otherwise uses the card's own bound or
band width as its scale.

`window_facts.mids` and `window_facts.funding` map coin symbols to
`[timestamp_ns, value]` pairs. Mids are integer micro-USD; funding rates are
dimensionless. `wallet_balance_micro` holds `[timestamp_ns, balance]` pairs
sampled at delivered ticks. `tick_timestamps_ns` holds those tick timestamps.
Each series retains at most `MAX_WORLD_SAMPLES` (`1024`) samples, across coins,
including combined multi-window measurements. These samples survive checkpoints.

Registration is versioned in the registry as `observation:<id>`: re-registering
an id supersedes it with the next version and cards then measure with the new
code. The twenty-three seed observations are registered the same way at bootstrap
(`observation:<id>`, version 1, provenance `seed`) and cannot be redefined: the
charter's own cards are measured by them, and their ids are not even slug-shaped,
so a proposal cannot name one. A registered observation is measured over closed
windows only; the `returns` and `forecasts` selectors remain the seed row
vocabulary, and `preflight_card` refuses any other binding. A card naming an
unregistered observation is refused before the vote, with the reason.

At window close, registered observations run only when named by a live card
or covered by an open registration trial. Seed observations remain available.
Delivery of the registration starts `observation.trial`. An undelivered
registration records `observation.inactive`. An unused observation retires
after `novelty.max_lifetime_windows` from its trial or inactive window, records
`observation.retired`, and leaves the observation book.

## Spot venue

`venue.spot_pairs` is a list of unique `BASE/USDC` pairs, default `[]`, fixed at
launch. Testnet seeds `["PURR/USDC", "HYPE/USDC"]` (`HYPE/USDC` is `@1035`
there), the funded draft `edition1-example` seeds `["HYPE/USDC"]`, and scripted
seeds `["BTC/USDC"]`; `HYPE/USDC` is seeded as the physics of the exit route
(docs/launch-decisions.md, "Self-serve gas"), one spot market and one
`MarketMid` per tick.
The mainnet re-draft uses `UBTC/USDC` and `UETH/USDC` for those base assets.
Live pairs must exist verbatim in SDK spot metadata; unavailable pairs fail launch.
Orders and closes accept `market: "perp" | "spot"` (default `perp`); spot uses pair
names and long-only inventory. Spot has no leverage, funding or liquidation.
The world venue block publishes lot sizes, price decimal increments and
`min_order_value_usd` (`"10"` on Hyperliquid; `"0"` by default on the fake).
Live prices also obey the venue's five-significant-figure rule (integer prices
are allowed).
`treasury.transfer` accepts `perps_to_spot` and `spot_to_perps`, moving available
USDC through the same intent, submission and receipt journal. Venue pots show
`perps` and `spot` as components of `venue`, never additional capital.

Class transfer confirmation requires a unique hashed `accountClassTransfer`
row matching the signed direction and exact amount, executed within the
inclusive interval from the nonce to nonce plus `CLASS_EXECUTION_TOLERANCE_MS`
(`60000` ms). Execution time need not equal the nonce. Two matching rows
confirm nothing. A fake settlement refused by the venue becomes
`treasury.failed` with a reason and releases the unmoved principal instead of
raising out of the treasury tick.

A poll or step preparation that cannot complete is ledgered as `treasury.pending`
with the transfer id, `step`, `phase` (`poll` or `prepare`), a bounded `reason`
(the rail's own constant message or, for any other exception, its class name,
never RPC text), the monotone per-step `attempts` count, `since_ns`, the
reserve window `since_window` the wait began in and the
rail's carried `reference`, written on the first attempt, on every change of
reason and on every tenth attempt (`PENDING_JOURNAL_EVERY`), and the pots view
publishes the current stall as `pending_reason` and `pending_since` until the
step gets evidence or a reference.

Live fills are classified against the venue's full spot metadata, not the
manifest's traded subset. Non-USDC launch holdings seed unowned lots at the
launch mark with `consequence.spot_seed` and `spot.inventory` evidence. A
holding without a launch price refuses launch. Refused fills never credit
spot inventory. Sells cannot exceed the smaller of runtime inventory and
accounted lots. Deferred fills update inventory only after order acknowledgement.

Gap liquidation realises the full observed loss and may overshoot zero;
`scripted-crash` demonstrates this. `termination.balance_floor_usd` is parsed
into `balance_floor_micro` and is the death condition: a balance at or below it
is death, irreversibly and at launch as well as in flight, and a gap liquidation
may still overshoot it. It defaults to `"0"`, where the termination reason is
`balance_zero`; a configured positive floor terminates with `balance_floor`.
The floor is not a guaranteed liquidation price.

## Registrable connectors (W8)

`[connectors]` is a hard cast with exactly these keys; it contains no seed origins.

| Key | Default | Meaning |
| --- | --- | --- |
| `max_bytes` | `262144` | Positive integer response-body cap; an extra detection byte causes refusal. |
| `timeout_s` | `10` | Positive integer wall-time bound for DNS, TLS and reading. |
| `call_price_usd` | `"0.001"` | Exact USD text or integer, converted to nonnegative integer micro-USD. |
| `max_calls_per_window` | `60` | Positive integer attempted calls per assembly per novelty reserve window. |
| `origin_denylist` | The world's own rail hosts: the venue API and RPC on both networks, the model providers and the discovery index | Hostnames (matched exactly or as a parent domain) or CIDRs; every registered seller's host is added to them. Bare addresses, private names and nonpublic resolved addresses are always refused. |

Population proposals have `{kind: "connector", id, description, origin, predicted_effect}`
and may add `preflight_path`, `pay` and `max_call_usd`, with an
origin of `https://<host>` and no credentials, port, path, query or fragment.
A priced `GET` of `preflight_path`, which defaults to `/`, precedes the same
experienced, proposer-excluding sortition ballot path as amendments. A strict majority admits the next
`connector:<id>` registry version. `predicted_effect` names a current measurable
card and carries `direction` and `window`. Admission starts the same delayed
ballot liability as an amendment.
The usual novelty registration trial is charged on admission.

Preflight tests reachability within the byte and time bounds. Any HTTP status,
including 3xx, 403 or 404, is admissible. Redirects are never followed.
The response body remains bounded by `max_bytes`; headers and body together
are bounded by `max_bytes + HEADER_ALLOWANCE_BYTES`, with a fixed `65536`-byte
allowance in `world/connector.py`.

`connector.fetch {id, path}` costs the flat price even for transport or
size failures once dispatched; malformed, denylisted, over-quota and unaffordable
requests never dispatch. Preflights share the proposer's price and window cap.
Paths may include a query but cannot change origin. HTTP status is returned
as evidence rather than treated as a fetch error.
Responses decode as UTF-8 with replacement and arrive in `seen_tool_results`
(and the existing `tool_results`) on the caller's continuation. A successful
fetch permits one additional tool round consisting of ordinary population tools
and the note tools, then a final model answer. The jail is unchanged.

`MIN_PROTECTED_BODY_CHARS` is `32`, fixed in `runtime/compute.py`.
Bodies at least that long and copies in parser arguments/model journal
responses are transient and redacted from the public ledger surfaces.
Final outputs containing the protected raw body
are refused instead of rewriting their action fields. Counters and registry
versions survive checkpoints. Shorter bodies are repeatable facts and are
neither protected nor grounds for refusing a final return.
A dispatched fetch is journalled as one
`io.call`/`io.result` pair, like a paid model call or an x402 purchase, so
recovery replays a call made after the last checkpoint from its recorded
outcome: it does not re-fetch potentially changed information and does not
repeat the debit.

The observatory's `connectors` section publishes latest registered versions and
attempt counts per UTC date at each public window close, including preflights.
Scripted manifests use an offline fake transport; live manifests use bounded HTTPS.

## Reading the web (edition 3)

`[web]` registers one tool, `web.search {query, max_results?}`, and takes exactly three
keys: `search_model`, a model on the menu whose `:online` route the provider searches with
(OpenRouter's web plugin, Venice's `enable_web_search`); `call_price_micro`, the tool's own
flat price; and `max_call_usd`, the ceiling on one whole search. With no `[web]` block no
tool is registered and the manifest hashes exactly as it did before web search existed, so
edition 2 is untouched. A search is one model call on that route under a fixed system
prompt asking for a JSON list of `{title, url, snippet, published?}` and nothing else; the
seat is charged the flat price plus the metered cost of that call, held against its
entitlement before the call and refused before any call when the ceiling exceeds
`max_call_usd` or the seat cannot afford it. The result is bounded — at most ten results, a
snippet of at most 600 characters, 16 KB in all — and returned with `cost_micro` and
`as_of_ns`. A provider error, an unparsable answer or an answer that is not a result list
comes back as `{error}` charged what the wallet was actually charged, and a malformed answer
pays the metered call but not the tool's flat price. It is a kernel call, not a wake: no
propensity, no judgement, no return, and its cost lands on the calling seat's consequence
account the way a connector read's does. The completion runs through the provider journal
proxy, so a resumed diary replays the same results from its `io.call`/`io.result` pair
instead of searching again, and every call and refusal is ledgered as `web.call` and
`web.refused` beside the `tool.call` row.

Searched text carries the connector's posture. Every title and snippet of at least
`MIN_PROTECTED_BODY_CHARS` (`32`, fixed in `runtime/compute.py`) is protected exactly as a
fetched body is: verbatim and JSON-escaped copies are redacted from the public ledger
surfaces and a final output carrying one is refused. Urls and shorter strings are
repeatable facts and stay readable, and the protection is transient — it lasts the
invocation, like a fetch's. A successful `web.search` also permits one additional tool
round, the same one a successful `connector.fetch` permits: ordinary population, note,
artifact and outcome tools, then a final model answer, so a seat can search and act within
one wake. A search that returned no results buys no extra round.

## New kinds of work: reward shapes and predicates

A registration declares which one of the four reward shapes — `judged`,
`forecast`, `conformity`, `exposure` — pays its emitted kind; the declaration
defaults to `judged`, is fixed for the life of that kind, cannot redefine a seed
kind's shape, and a conflicting redeclaration reaches `registration_feedback`.
The declaration is `reward_shapes`, an object on the assembly proposal mapping
each of its own `emits` kinds to a shape. A declaration naming a kind the
proposal does not emit is refused. The seed shapes are `ProducerReturn`
→ `judged`, `Verdict` → `forecast`, `MetaVerdict` → `conformity` and
`Exposure` → `exposure`. A refusal is ledgered as `registration.rejected` with
its reason. `world.work` publishes `reward_shapes`, `default_reward_shape`,
`kind_rewards`, `predicates`, `predicate_registration` and `predicate_contract`.
That block names the four shapes and the default; it states no reason for the
catalogue being closed.

A shape selects an existing reward channel. `judged` settles on the verdict
channel. `forecast` settles on the consequence channel, except that the seed
`Verdict` keeps its conformity channel because its predictions already have
their own consequence decisions. `conformity` settles on the conformity channel
when a higher tier exists to judge it, and on the fast channel otherwise.
`exposure` settles on the
exposure channel. Cascade admission follows the declared shape too: the seed
`Verdict` is a tier-one arrival and every `conformity`-shaped kind, seed or
population, is buffered with the others at the tier its own payload declares, so
a judgement cannot reach the tier above it sooner by being registered under a new
name. A forecast-shaped return earns the mean of its own resolved
predictions once, as `forecast-mean-v1`; a return with any unresolved prediction
is censored rather than scored. Admitted shapes survive resume in
`kind_reward_shapes`, so a kind keeps its meaning after the assembly that
declared it is retired.

A card's `answers_for` may name any registered emitted kind, and that kind is
measured in its own scope rather than as a producer. A launch manifest's cards
are narrower: `answers_for` there must be a seed role, `all`, or a kind one of
the manifest's own assemblies emits. An amendment naming an unregistered kind is
refused before the vote. The role aliases and `all` are reserved spellings: a
registration emitting `Producer`, `ALL` or any other capitalisation of one is
refused with feedback, and a card's emitted-kind scope keeps the kind's exact
spelling instead of being folded into an alias, so an admitted kind's card can
never silently measure a different population.

A forecast predicate registers like an observation: a jailed
`resolve(facts) -> bool` preflighted against the last closed window, versioned
per id so a sealed claim keeps the meaning it was sealed with, and resolved only
over facts that followed the claim; a resolution that fails is unscored, never
false. The proposal is `{"kind": "predicate", "id", "description", "code"}` with
exactly those fields. `id` is a slug of 2–48 chars; seed and kernel predicate
ids, including `return_paid_off`, cannot be redefined. `code` is bounded by
`MAX_PREDICATE_CODE_CHARS` (`8000`) and must define `resolve`; `description` is
bounded by `MAX_PREDICATE_DESCRIPTION_CHARS` (`500`). The resolver runs in the
tool jail under the observation ceilings, `OBSERVATION_TIMEOUT_S` and
`OBSERVATION_CPU_S`, and only a JSON boolean is an outcome. Nothing is
registrable before a window has closed, and nothing is registrable on a host
without a jail.

The preflight and its value or reason are ledgered as `predicate.preflight`.
Admission registers `predicate:<id>` with one novelty trial and emits a
`REGISTERED` payload `{"kind": "predicate", "id", "version"}`. Re-registering an
id appends the next version; an outstanding forecast binds the version it sealed,
and the base rate it is scored against is keyed `<id>@<version>`, so a
replacement definition starts its own prevalence history. A sealed claim also
carries `window_cursor`, the position of every public window counter and series
at the moment it was made, and resolution reads `window_facts_since` that mark:
a fact already true when the claim was sealed resolves nothing. Unavailable
facts, a timeout, a nonboolean result or a failed run leave the forecast
censored. `world.work.predicates` publishes each predicate's id, description,
parameter names, `horizon_param`, `version` and `provenance`, and `predicate`
is one of the kinds the `register` field accepts.

## Seeing the world: markets, paid sources and notes

A connector proposal may carry a `preflight_path` within its own origin, and
admission judges whether the origin answered within the manifest's bounds rather
than whether its root returned 2xx. The path defaults to `/`, starts with one
`/`, carries no fragment or whitespace, and cannot change origin. Any HTTP
status, 404 and 403 included, is an answer; only an unanswered, oversize or
out-of-time read refuses admission. The preflight's body is stripped before the
proposer sees the result. `connector.registered` records `preflight_path`, `pay`
and `max_call_micro` beside the origin and version.

`exchange.coins` and `venue.spot_pairs` are the launch seed of trading
permission, not the limit of what may be read: public venue data is readable for
any coin the venue lists, and a `market` registration adds a pair the venue
lists, under a novelty trial, surviving resume. The venue's listing is not in the
prompt: `world.venue` carries the instrument record of each market in
`world.trading_markets` and nothing else, and `world.venue_listing` says that
`venue.instruments` returns the whole listing. A listing of a few thousand
instruments therefore costs the world block nothing, and the published schema of
the three per-coin public reads names the listing rather than enumerating it;
dispatch still checks the coin against the venue and refuses an unlisted one.
`venue.instruments`,
`venue.mids` and `venue.funding` cover every listed market;
`venue.candles`, `venue.order_book` and `venue.funding_history` accept any coin
or pair the venue lists, and `venue.funding_history` refuses a spot pair.
`venue.place_market`, `venue.place_limit`, `venue.close`, `venue.cancel` and
`venue.set_leverage` still refuse a market that is not registered for trading.
These six public reads are priced at `connectors.call_price_usd` per call. The
launch seed only ever adds to the adapter's own listing; on the deterministic
venue a seeded market the adapter does not list is dropped, and on a live one an
unlisted spot pair fails launch. An adapter that publishes no listing keeps the
manifest seed it was built with.

The proposal is `{"kind": "market", "coin"}` for a perpetual or
`{"kind": "market", "pair"}` for a `BASE/USDC` spot pair, with exactly one of
them. A coin the venue does not list, or one already registered for trading, is
refused. Admission costs one novelty trial, registers the contract
`market:<market>:<coin>`, ledgers `market.registered` and emits a `REGISTERED`
payload `{"kind": "market", "coin", "market", "version"}`. `world.trading_markets`
publishes the `perp` and `spot` lists the population may trade. Resume rebuilds
the venue tools from the launch seed and replays every `market:` contract, so
registered markets, inventory and lots survive a restart. An order refused before it reaches the venue is ledgered with its reason, `order.infeasible` when the venue's free collateral — equity less margin used, carried in the item as `venue_available_usd` — cannot carry the margin the order plus the resting book needs, and `order.refused` for every other pre-submission refusal, and the reason also reaches `registration_feedback`. Every counted fill writes one `fill.counted` item at the moment it is counted, with the order id, coin, market, size, price, notional, realised P&L, fee and window; the `event:Fill` the population is delivered is a separate item written on delivery. A live tick broadcasts one `MarketMid` per trading market and one `Funding` per trading perpetual, the manifest seed plus every registered market, never the venue's whole listing, so a registered market enters the broadcast from the next tick and resume restores the set; fills and settled funding payments are never filtered, because they carry cash.

A connector may pay for data through x402 with an exact per-call cap from the
world's own wallet, journaled as one `io.call`/`io.result` pair and never
resubmitted on replay; above the cap only the flat read is billed. The proposal
carries `pay: "x402"` and `max_call_usd` as exact USD text or an integer, parsed
into `max_call_micro`; a cap above `treasury.max_request_micro` is refused. The
paid read is the journal call `connector.paid_fetch`, and the ledger retains
`x402.quote`, `x402.submitted`, `x402.result` and, when the outcome is unknown,
`x402.unresolved` and its later `x402.reconciled`. A quote above the cap returns
HTTP 402 with no data cost. `world.connectors` publishes `optional_fields`,
the `payment` note, and each registered connector's `pay` and `max_call_micro`.

`note.put`, `note.get` and `note.list` are a public key-value notebook bounded in
UTF-8 bytes and charged rent by byte-time; unaffordable rent retains the text, an
overwrite cannot escape the debt, reads are journaled and priced, and the wake
publishes counts only. `[notes]` is a hard cast with exactly these keys.

| Key | Default | Meaning |
| --- | --- | --- |
| `max_keys` | `128` | Positive integer count of retained keys. |
| `max_bytes` | `262144` | Positive integer total of key and text bytes. |
| `byte_window_micro` | `1` | Positive integer micro-USD charged as the flat price of one `note.put` or `note.get` call. It prices neither storage nor bytes moved; the name is kept so old manifests still load. |
| `micro_per_byte_day` | `"0.04"` | Exact positive decimal text (or integer) micro-USD per retained byte per day: the storage rent (edition 2, contract C3). At its default the whole 256 KiB cap costs 10,485 micro-USD, about a cent, a day. Absent or default, it leaves the manifest hash unchanged. |

A key is 1–128 printable UTF-8 bytes. An entry's size is its key bytes plus its
text bytes, and a call's price is the flat `byte_window_micro` plus any rent the
entry still owes. Edition 3 (contract C4) removed the per-byte transfer toll:
charging a micro-USD for every byte moved made a 4 KiB read cost about $0.0041
before the model had consumed one character of it — more than a cheap model call
— for a resource the factory does not actually pay for, which made remembering
dearer than producing another unsupported paragraph. Byte-time rent is
unchanged: storage is a real resource, and a note nobody will pay to keep should
go. `note.list` is free, like `artifact.get`, and returns up to 50 rows of key,
title, type, bytes, version, owner seat, the window last written and `public`
(always true for the notebook), newest first, with `next_cursor` for the next
page and `count` for the whole index; it carries no text, so a reader need not
already know a key. `artifact.list` is its counterpart over the archive, with
`sha` in place of the key and an optional `owner` filter. A call above the
caller's available compute or its
request ceiling is refused before any debit or overwrite. `note.get` on an
unknown key is an error. Rent is `bytes × elapsed_ns × rate`, accrued from the
moment a key is written (an overwrite inherits the open interval, so rewriting
forgives nothing) and collected at each reserve-window boundary for the time
elapsed since the last boundary, not per window counted: the rate is an exact
ratio of micro-USD per byte-nanosecond, and whatever fraction of a micro-USD an
interval leaves over is carried on the entry (`rent_carry`), so collecting
hourly charges exactly what collecting daily charges and a two-minute window
cannot round a small note up to a micro-USD (the reviewer's rent trap). The
boundary writes `note.rent` when the holding decision's compute affords it,
`note.rent_due` when it does not, in which case the text stays and the debt is
owed on the next read or overwrite. The ledger items `note.put` and `note.get`
carry the key, handle, assembly id, cost, window, version and byte count, and
`note.put` also carries the text. The `note.read` journal call is replayable
read-only work. `world.notes` publishes the key and byte counts, the bounds and
the pricing rule; the wake's `notes` section publishes counts only; the
notebook, its accrual marks and its carried remainders survive resume.

Retained storage is an explicit, resumable liability of the decision that holds
the note, not only a wallet debit. Every paid charge is added to that
decision's cost contribution for the window the charge landed in and enters
that window's measured rows — a producer's charge its cost statistics too, as
cost the window spent and never as a return it received — as a cost of the same
decision and never as a response, so a cost card sees it whether it selects
returns or whole closed windows, and so do the penalty shares it attributes,
and while the decision's own consequence outcome is still open it is also
carried into that outcome's cost, so a return cannot resolve
`return_paid_off = 1` on a margin its storage has already consumed. An outcome
is fixed once and never reopened, so rent falling due afterwards stays with the
note's current owner decision as a cost contribution alone, and the note is
kept rather than released: public text other decisions may already have read is
not deleted because one account closed. The carried amount is
`ReturnAccount.carried_micro`, resumes with the consequence table, and appears
as `consequence.carried`; the matching `price.contribution` item carries
`storage` and `carried`.

Window facts carry the market, funding, wallet and tick series of the closed
window, retained to `MAX_WORLD_SAMPLES`, so a registered observation can measure
the world and not only the factory. `window_facts.books` maps each coin to
`ts_ns`, `bids` and `asks` levels, with prices in micro-USD and sizes in base
units. Paid reads through `venue.mids`, `venue.funding`, `venue.funding_history`
and `venue.order_book` are what fill `mids`, `funding` and `books`;
`wallet_balance_micro` and `tick_timestamps_ns` are sampled at delivered ticks.
Malformed or unavailable venue data contributes no sample. No series carries an
account, an author or a handle.

A predicate forecast seals a cursor into the open window, and that cursor marks
monotonic sample positions: each bounded series counts the samples it has already
discarded, so the mark does not slide when the series rolls and evidence that
arrived after the claim is still found. When the retained prefix no longer
reaches back to the mark, the required interval has been discarded: the window
supplies no facts at all and the forecast is closed unscored rather than resolved
false, with `forecast.evidence_discarded` in the diary. A claim that comes due
after a window boundary is read against a window that opened after the mark, so
every sample in it counts and any sample it has already discarded censors the
claim the same way.

## Edition 2: endowment, machinery, challenge, income, release identity

The edition 2 contracts (`docs/plans/edition2.md`, from the cold audit in
`docs/audits/v4/gpt6-triage.md`) add no objective for the population. They
add a locked endowment released on a schedule and a pause instead of a death
between releases, seats that are programs, an archive of what a seat keeps, a
route for changing a card's measurement without the old card vetoing it, a
second way to earn beside trading, and a release identity bound into the
diary. Each is a classification of the one conserved wallet or a ledgered
fact; none is new money. Everything below is what the code on `main` does.

### `[endowment]`

| Key | Type | Default | Hard cast? |
| --- | --- | --- | --- |
| `endowment.locked_micro` | nonnegative integer micro-USD, at most `initial_balance_micro` | `0` | Yes: backing booked in the balance at launch that nobody can spend until released. |
| `endowment.releases` | array of tables `{at = "7d", amount_micro = N}`, ascending `at`, positive amounts summing exactly to `locked_micro` | `[]` | Yes: the tranches, as durations after the ledgered `Launch`, never absolute times. |

An absent or default `[endowment]` leaves the manifest hash unchanged. The
wallet is built with the locked amount and a `ReleaseSchedule`; `sum(releases)
== locked_micro` is validated at load and again at construction. `wallet.locked`
is the backing not yet released and `wallet.unlocked` is `balance - locked`;
`available` and `unhistoried_available` are taken from the unlocked part, so
locked money can never be reserved, committed or counted as novelty budget. A
venue loss can carry the unlocked part below zero until a release lands. The
novelty reserve window opens on `wallet.unlocked`, not on the balance.

The schedule is anchored once, at the ledgered `Launch` timestamp
(`wallet.anchor`, carrying `launch_ns` and the locked amount). Every delivered
event calls `wallet.release_due(now_ns)` before anything else spends: each
tranche whose `launch_ns + at` has passed moves from locked to unlocked once,
in order, ledgered as `release` with `tranche`, `amount`, `due_ns`,
`locked_after` and `balance_after`. The balance does not change; only its
classification does. `drip` is unrelated to releases and a final ledger
releases nothing. `next_release_ns` is the absolute time of the next unreleased
tranche, or null. The locked amount, the schedule, the anchor and the count of
released tranches are checkpointed and checked on restore: a checkpoint whose
locked backing disagrees with its released tranches is refused. The wake's
`pots.current` carries `locked_micro`, `unlocked_micro`, `next_release_ns` and
`dormant`. The wake's `money.in_by_class` counts every tranche under its
`release` class: the wallet's own `release` item carries the tranche's amount
(a cancelled hold is a `wallet.release` item and moves nothing). The loop then
classifies each released tranche with `Budget.on_release` (C10): `base_share`
of what the pool actually holds is split equally across the live seats and the
remainder stays unallocated, ledgered as `budget {op: "release", amount,
backed, grants, to_unallocated}`; a tranche the pool does not fully hold
(shared spending ran it down) refills the pool before any seat is endowed.
On `main` that split counts seat ids, so a lineage that registered more
children takes more of every tranche (reviewer P2-07). The R2-B economy change
(`docs/audits/v5/gpt6-second-reading-triage.md`) splits a release per lineage
and pays each lineage's share to its root seat; landing in the R2-B economy PR,
not merged at the time of writing.

### Dormancy

`Termination.check` returns `budget_dormant` when the unlocked, unheld money
cannot buy the cheapest live seat (the smallest reserve ceiling routing would
probe, doubled for a non-x402 seat, or one micro-USD when no seat prices),
`wallet.locked > 0` and `next_release_ns` is not null. It is not a terminal
reason: `Termination.kill` refuses it. Terminal death by budget requires
`locked == 0`; a world with backing and a release still due never dies of
budget (`balance_zero`, `balance_floor`), though it still dies of a ledger
failure or an operator kill.

The loop enters dormancy on that trigger (`trigger: "wallet"`) or, while
backing and a release remain, when the compute-insolvency streak reaches
`treasury.insolvency_events` (`trigger: "insolvency"`); without backing that
streak is still `insolvency:compute` death. Entry and exit are ledgered before
the state changes: `{"kind": "dormant", "state": "entered", ts, trigger,
locked, unlocked, next_release_ns, n}` and `{"kind": "dormant", "state":
"exited", ts, since_ns, locked, unlocked, n}`. A wallet entry is left as soon
as the check no longer reports dormancy; an insolvency entry is left only once
a release has landed since it began, so a provider shortfall is not retried on
the same money, and leaving resets the insolvency count.

While dormant the event is not routed: no seat is woken for it, so no model or
program call, no return, no registration and no tool call comes of it, and the
compute-insolvency streak is not advanced. Everything mandatory continues on
every event: drips and due releases, reserve-window management (windows still
close, cards are still measured and priced, note rent still accrues and is
collected, and an activation boundary still falls due), the treasury's window
cap and its tick, order reconciliation, fills and settled funding from the
venue, x402 reconciliation, the reconciler's snapshot, settlement of due
forecasts, censoring of stale judgements, queue expiry and return delivery,
and the checkpoint at each window boundary. One paid path is not paused by
dormancy on `main`: a metric challenge whose trial completed is balloted at
the next activation boundary from inside window management, and those ballots
are metered requests to the committee's seats; while the wallet cannot afford
them each such ballot is a failed return and an abstention. The dormancy record
(`since_ns`, `trigger`, released-tranche count, `next_release_ns`) is
checkpointed. The wake shows `liveness.status` as `alive`, `dormant` or
`terminated` with `dormant_since_ns` and every `dormant_periods` pair, and
`pots.dormancy` lists each episode (`state`, `ts_ns`, `locked_micro`,
`next_release_ns`). The wake host witnesses each transition (deploy/README.md,
"Witness").

### Program seats

| Key | Type | Default | Hard cast? |
| --- | --- | --- | --- |
| `prices.program_micro_per_call` | nonnegative integer micro-USD | `50` | Yes: the flat price of one program-seat call. Absent or default, it leaves the manifest hash unchanged. |

An assembly proposal whose `model_id` is `program` registers a seat whose
executor is population Python in the tool jail rather than a model
(`ProgramAssemblySpec`, contract C8). The proposal carries `code` (nonempty,
at most 16,000 chars), `timeout_s` (integer 1–10 wall seconds, default 10) and
`state_policy` (`none` or `private`, default `none`) beside the ordinary
assembly fields; `accepts`, `emits`, `schemas`, `reward_shapes` and `role` mean
what they mean for a model seat. Admission costs the same novelty trial as a
model seat, registers the same `assembly:<id>` contract at the next version,
and is refused before the trial is spent on a host without the jail. The
`REGISTERED` payload adds `program: true` and the `state_policy`.

Each call runs the code once with one JSON object on stdin — `prompt` (the
rendered request, exactly what a model would read, with `inputs.you` set to
the seat's id), `description`, `inputs`, `outcome_schema` and `state` — and
expects on stdout the same Return JSON a model would print, tool calls,
child requests and registrations included; it passes through the same output
validator. The price is reserved and committed through the meter under the
reason `model:program`, so every call is a wallet transaction and the novelty
reserve treats it as the seat's own compute; a call whose price exceeds the
request's cost ceiling is a `failed` return that ran nothing. A non-zero exit,
a wall timeout, a reply that is not valid Return JSON, or a `state` printed
under `state_policy = "none"` is a billed `malformed` return, exactly as a
model's malformed reply would be. Programs are routed, judged, given standing,
priced by the cards and retired exactly like model seats; the wake's roster
counts them under the model id `program`.

With `state_policy = "private"` the object the program prints under `state`
is its private memory: it never reaches the outcome schema, the judges or the
return's outputs. It is serialised as canonical JSON (at most 65,536 bytes,
else `malformed`), archived as an artifact owned by the seat with kind
`program.state`, and handed back as `state` on the next call; the hash is
carried on the return's provider metadata (`state_sha`) and every metered call
(one the meter admitted, whatever its status) is ledgered as `program.call
{assembly_id, handle, status, cost, state_in, state_out}`, so the diary names
the machinery's memory as well as its answer.
A state the archive cannot read arrives as null with `state_error` on the
return. The spec (code included) and the current `state_sha` are checkpointed;
resume restores the state by hash from the archive.

### Attention: what wakes a seat, and what it may decline

`runtime/subscriptions.py` (contract C2) owns what wakes a seat. A seat's own
answer carries `subscribe {kinds, coins, cadence_floor}` and `defer: <n ticks>`;
both are the seat's own money and neither needs a ballot. `subscribe` narrows and
never widens — a kind outside the seat's registered `accepts` is refused with a
reason (`subscription.refused`), and an adopted change is ledgered
`subscription.changed`.

**Deferral's contract, exactly** (edition 3, R3-F; the seat prompt's common
contract states the same words). `defer` and `cadence_floor` silence **routine
world wakes** and nothing else: `Tick`, `Drip`, `MarketMid`, `Funding` and the
coalesced `WorldUpdate`. They do **not** silence a `Fill`, an `OrderRejected` or
a `WatcherFired`, which reach the affected seat whatever it deferred. They also
do not silence a **judge or meta commission**: that is somebody else's paid
request arriving, and a seat that stopped reading the market has not resigned
from the cascade. A commission is declined the only way paid work can be — by
answering `{"status": "cannot", "reason": ...}`. That costs the call and nothing
beyond it, is **not** malformed (its propensity label is `declined`, an arm a
learner can hold), is ledgered `commission.declined {assembly_id, handle,
reason}`, and R3-D settles it `unmeasured`.

**The fold has three durable states, per seat.** *Offered*: world events folded
in — first, last, high, low, the funding prints, the counts — and not yet
delivered. *Delivered*: rendered into a request that was invoked, and held.
*Acknowledged*: that invocation returned `ok`, and it is dropped
(`fold.acknowledged`). An invocation that **fails or comes back malformed showed
the seat nothing**, so its fold returns to offered — merged under whatever
accumulated meanwhile, oldest values first — and the next wake sees it
(`fold.offered`). All three states are checkpointed inside `subscriptions`, so a
restore between a request and its answer still owes the seat that world.

**Coin filters apply to the delivered fold**, not only to admission: a seat
subscribed to BTC reads BTC prints and BTC funding, and the counts it is shown
are the counts of what it is shown. A watcher's `equity_below`/`equity_above`
trigger is settled against the **venue's** equity; a failed account read leaves
equity simply absent and the watcher keeps the last value it actually saw, rather
than firing on the compute wallet's balance, which is spending authority and not
venue equity. For the same reason a failed account read on a tick renders
`account_unavailable: <reason>` and no `account` block at all: no fabricated
equity, no empty position set.

### The artifact archive

`kernel/artifacts.py` is a content-addressed store (contract C9): an
artifact is a byte string named by its SHA-256. `put(data, owner, kind)`
ledgers `artifact.put {sha, owner, artifact_kind, bytes, ts}` before the bytes
exist, so a crash between the two leaves a record without bytes rather than
bytes without a record; the bytes live beside the ledger under
`runs/<world>.artifacts/<sha>` (mode 0600, written through a temporary file
and an atomic replace), or in memory for a world without a ledger path. A put
is idempotent by content, `get` verifies the hash it was asked for and refuses
a tampered file, and retirement of an owner leaves its artifacts readable. The
index (hash to owner, kind, size, time, published, references) is checkpointed;
a checkpoint from before the archive restores it empty.

**Ownership is a (sha, owner) reference** (edition 3, R3-F). One blob carries a
reference per writer, each with its own kind, its own moment and its own
published flag, so a second writer of identical bytes owns what it wrote and can
read it rather than being told the first writer's bytes are private; putting an
existing sha with `public: true` publishes the blob. The **first** reference
stays the owner of record — `owner_for(sha)`, one payer of rent and one subject
of retirement. `entries()` and therefore `artifact.list` return one row per
reference, with that reference's owner and published flag.

**Collection.** `ArtifactStore.collect()` is the one thing that deletes, and it
can only reach blobs **no reference names and nothing published** — what a crash
between the durable write and its ledger item leaves behind. Each removal is
ledgered `artifact.collected {sha, ts}`. The runtime calls it at each
reserve-window boundary (`continuity.charge_window`). An owned blob and a public
blob are never candidates, so collection can never take a seat's working state,
an inbox body, an archived rationale or anything the population published.

`artifact.get {sha}` is a seed tool, version 1, priced at zero and available
to every seat: it returns `sha`, `owner`, `kind` (the reader's own reference's
kind when it has one), `public`, `bytes` and the content as
`text` (or `base64` for bytes that are not UTF-8) up to 65,536 bytes, an
`error` above that or for an unknown or malformed hash, and ledgers
`artifact.get {sha, handle, assembly_id, found, ts}`. The read is **scoped**
(edition 3, C1): a seat reads what it owns and anything put with `public: true`;
a program's `program.state` is readable within the program's own lineage
(`BudgetBook.lineage`); anything else answers `{sha, error: "artifact_private"}`
and nothing about the bytes, and the ledger row carries `reason`. `entries()`
returns `(sha, owner, public, bytes, created_ns)` rows for the directory W4
builds. There is no `artifact.put` tool: the writers are a private-state program
seat and a seat's own working state. `owner_for(sha)` names who pays rent.

### Continuity: working state and the outcome inbox

`runtime/continuity.py` (contract C1) replaces the three-entry `memory` deque,
which evicted a decision before its consequence could settle on it.

`WorkingState` keeps one head pointer per seat over the archive. A seat
advances its own head by returning `working_state` (a JSON object): the kernel
canonicalises it, puts it as `working.state` owned by that seat, ledgers
`state.put {assembly_id, sha, bytes, handle, over_soft, ts}`, and the seat's
next request carries `your_state: {sha, bytes, state}` verbatim. The soft
allowance is 8,192 bytes (accepted, and the rent is what it is); above 65,536
the field is refused, the head is unchanged and `state.refused {assembly_id,
handle, reason}` is ledgered. A manifest may seed a head with an assembly's
`initial_state`; without one the head is None. Rent is byte-time at
`notes.micro_per_byte_day` collected at each reserve-window boundary through the
seat's own meter (`state.rent`, or `state.rent_due` when unaffordable), on the
same accrual arithmetic the notebook uses; there is no transfer toll.

`OutcomeInbox` addresses every settled consequence to the seat that decided it:
`{handle, said: {rationale, payoff, forecasts}, outcome, observed_at_ns,
delta_micro, evidence}`, the body an artifact owned by that seat, ledgered as
`outcome.addressed {assembly_id, handle, sha, item, delta_micro, evidence}`.
**Every consequence reaches its owner** (edition 3, R3-F), each as its own item
with an exact `outcome_id`: the verdict on a return; its payoff, with the money;
a **fill**, addressed through the lot table to the seat whose order it was; a
**refusal**, addressed to the seat whose order was refused; a settled forecast on
**any** predicate, not only `return_paid_off`; a **program result**, addressed to
the lineage that registered the program, since a program has no model to read an
inbox; a **late realisation**; and a judge's verdict consequence. A consequence
with no owner to address is a **failed delivery** and is ledgered
`outcome.undeliverable {consequence, handle, reason}` rather than dropped.

The next request carries `unread_outcomes: {count, more, items}` — `count` is
every unread item, `items` is the **oldest** eight, oldest first, and `more` is
how many unread items the window did not carry, so the window is never mistaken
for the queue. `outcome.get {outcome_id}` is a seed tool, version 1, priced at
zero: a kernel read of the seat's own inbox, never another seat's, ledgered as
`outcome.get`. `handle` is a **fallback** and returns the oldest item of that
decision the seat has not read, with a `note` saying so, because one decision can
settle into several outcomes; the view carries `related_outcomes`, the ids of the
rest. An answer's `ack_through: <outcome_id>` advances that seat's cursor and is
ledgered `outcome.ack {through, handle, cursor}`; it acknowledges only items
**delivered** at or before it — an id the seat learned from `related_outcomes`
but was never shown acknowledges only as far as its last delivery — and
everything after stays unread.

What a seat **said** is retained until that decision's last consequence settles
or the seat retires. Only then, and only over `MAX_SAID`, is the oldest such
record archived as an artifact (`said.archived {assembly_id, handle, sha}`) and
dropped from the table; `outcome.get` and the settler read it back from the
archive, so no decision with open consequences can lose its rationale. Heads,
item indexes, cursors, how far each seat was delivered, the archived-rationale
index and what each handle said are checkpointed; the bodies are artifacts, and
`_verify_artifacts` refuses to continue a world whose head or inbox body is
missing.

### The metric challenge

A `challenge` proposal (contract C7) is the route for replacing what a card
measures without the card it challenges judging the change:

```json
{"kind": "challenge", "card_id": "censorship-bound", "evidence": "text",
 "replacement": {"observation": "censored_share", "rule": "at most", "value": 0.2,
                 "window": {"kind": "windows", "n": 5}},
 "trial_windows": 6}
```

Exactly those keys. `card_id` names a current card that is not already under
challenge; `evidence` is a nonempty string of at most 4,000 chars;
`replacement` has `observation` (a seed or registered observation), `rule`
(`at most`, `at least`, `above`, `below`), a finite `value` and a typed
`window`, and may add `description`, `units` and `answers_for`; it keeps the
challenged card's `id` and `norm`, so adopting it is the ordinary replace
amendment. `trial_windows` is an integer in `[1, 50]`. An unchanged
replacement, an unmeasurable window, an unparsable region, a duplicate
observation binding or a refused preflight is refused with the reason before
anything is spent.

Admission costs one novelty trial, registered as the contract
`challenge:<challenge id>` (`challenge-<n>-<card slug>`), and ledgers
`challenge.proposed` with the frozen incumbent and replacement cards, the
evidence, the trial length, the start window and the frozen definitions of
any registered observations either card reads. The incumbent keeps pricing the
live charter throughout: nothing in the charter changes at admission, and a
window closed during the trial is priced by the edition that measured it, so
commitments incurred under the incumbent settle under the incumbent. At every
window close of the trial both cards are measured, frozen, over the same
samples with the definitions frozen at admission, and one `challenge.window
{challenge_id, window, incumbent: {card_id, observation, value, scopes},
replacement: {...}}` item is ledgered; `value` is the equal mean of the
supported scopes, or null. When the series holds `trial_windows` rows the
trial is complete (`challenge.trial_complete`, status `due`) and no further
window is measured.

At the next activation boundary the completed trial goes to the existing
committee ballot: the replace amendment is proposed under the challenge's own
id with the observation bindings frozen at admission (so a definition that
drifted during the trial refuses activation exactly as any amendment would,
ledgered `challenge.refused`), its promise is the replacement holding inside
its region one window after activation, the proposer is excluded and the
committee is seated by the usual sortition (`challenge.balloted`). Each
voter's request carries the ordinary amendment inputs and, for a
challenge-originated amendment only, `inputs.challenge`: the challenge id and
card, the evidence (cut to 2,000 chars, with `evidence_truncated`), the frozen
incumbent and replacement cards, and both measured series side by side, one
row per trial window with each side's observation, value and scopes, bounded to
the last 24 windows and 8 scopes per side; the request's description names the
challenge. The ledger keeps the whole series. Votes, tally, activation and
ballot liability are the amendment's. While a challenge is in trial, due or
balloted, a connector or retire proposal may name the challenge id as its
`predicted_effect.card_id`, and that promise is frozen on the replacement card
and graded on it, never on the incumbent. Challenges, their series and their
status survive checkpoints.

### The service seller

A `service` proposal, `{"kind": "service", "program_id", "price_micro",
"description"}` with exactly those keys, sells a registered population tool's
output to outside buyers over x402 (contract C11). `program_id` must be the
slug of a tool the population registered (a `population_tools` entry, not a
program seat), `price_micro` an integer in `[1, 10,000,000]`, and the host
must have the jail. Admission costs one novelty trial, registers
`service:<program_id>` at the next version, and ledgers `service.registered`
with the id, version, price, description, the proposing handle and owner,
and the tool's exact `code`, `args_schema` and `timeout_s`: the program is
frozen at registration, so a later tool version never changes what a buyer
already paid for. The `REGISTERED` payload carries the price.

The runtime holds the ledger's only writer lock, so the endpoint is served
beside it by `deploy/serve.py`, which reads the sealed ledger the way the wake
does (re-read every `--refresh` seconds; the latest version of each service
wins) and takes the reserve address from the manifest the genesis names,
exiting 2 without one. `GET /services` lists the catalogue: id, description,
price, version and argument schema, never source. `POST /service/<id>` without
a payment header returns 402 with the v2 quote: `exact` canonical Base USDC,
the price as the amount, `treasury.reserve_address` as `payTo`, a 300-second
timeout. With a payment header (`payment-signature`, `x-payment` or
`x-402-payment`), `runtime/seller.py` verifies it by rebuilding exactly the
EIP-3009 typed data `world/x402.py`'s buyer signs and recovering its signer,
refuses a reused nonce (the last 4,096 are remembered), submits it once to the
facilitator (`FACTORYLAB_FACILITATOR_URL`, default
`https://x402.org/facilitator`) and accepts only an explicit, matching
settlement naming the recovered payer and a transaction; any refusal is a
fresh 402 with a local reason and the header is never echoed. Request bodies
above 65,536 bytes are refused. Only then does the program run, in the same
jail population tools use, and its output returns with a `PAYMENT-RESPONSE`
header. The receipt is appended to the spool (`--spool`,
`world/income.py`: one JSON line `{service, micro, tx, payer, program,
version, ts}`, fsynced, 0600) before the program runs, so a program that fails
still leaves the receipt it was paid for. Nothing in the server reads a key or
signs.

The runtime, started with `FACTORYLAB_INCOME_SPOOL` naming that file, reads
the spool on every treasury tick through the recovery journal
(`treasury.income.lookup`, replayed byte-for-byte on resume), from the offset
its snapshot carries: only newline-terminated lines are read, a spool shorter
than the offset is treated as replaced and read from nowhere, and the consumed
offset is part of the treasury snapshot, so no receipt is booked twice. Each
complete receipt becomes `income.earned {service, micro, tx, payer, program,
version, served_ns}` through `Treasury.earn`, which a paid call served
in-process (tests, or a future loop hook) reaches directly. `earn` ledgers the
item and adds to `earned_micro`; it does not credit the integer wallet
balance. The USDC itself arrived at the reserve address and appears in the
reserve pot when the rail is next observed. On `main` the loop books the same
receipts through `_collect_income` and credits the seat that owns the
service's program (`_book_income`, reason `income.earned:<service>`) as a
pool-to-seat reclassification bounded by the pool, like a paid-off credit: the
root wallet does not grow, and a service whose program has no live owner
leaves the income in the pool (reviewer P2-05). The R2-B economy change
(`docs/audits/v5/gpt6-second-reading-triage.md`) books earned income into the
root wallet as new money, like venue P&L, credits the seller from that new
money, and treats a settled receipt as a paid-off consequence; landing in the
R2-B economy PR, not merged at the time of writing.

### The three income classes

The treasury keeps, beside the pots, where money that entered them came from
(`INCOME_CLASSES`), and the wake publishes the three separately as
`pots.income` and counts them in `money.in_by_class`:

| Class | Meaning | Ledger evidence |
| --- | --- | --- |
| `earned_micro` | x402 income from sold service calls. The only earned line. | `income.earned` |
| `subsidy_micro` | The architect's compute credit: the first complete observation of the seed provider credit plus every seller credit, recorded once. Null until observed. | `treasury.subsidy {micro, seed_micro, sellers}` |
| `converted_from_principal_micro` | Venice credit bought from trading capital: the `received_micro` of every confirmed `to_venice` transfer. Conversion, not profit. | `treasury.confirmed` with `direction = "to_venice"` |

All three, and the spool offset, survive resume with the treasury snapshot.

### Release identity and witness

Beside its manifest hash and its venue account, a diary binds the release that
executes it (cold audit F1, contract C4). `factorylab/runtime/release.py`
computes once per process

```
release_digest = sha256(git_head + sha256(uv.lock) + tree_hash(factorylab/))
```

where the tree hash covers every regular file under the package by relative
path and content, byte-compiled caches excluded, so an uncommitted edit is a
different release exactly as a new commit is; the head comes from git, else
from the `RELEASE` record `deploy/install.sh` wrote, and `release_info()`
says which (`git`, `release_file`, `none`). The digest is drawn at
construction and carried in the `Launch` event's payload and in every
checkpoint. `restore_runtime` compares the saved digest with the running one
and refuses a different release with `failed_resume reason=release_mismatch`:
the CLI exits 1 with that reason code, the supervisor's webhook and witness
lines carry it, and the diary gets a `failed_resume` item naming both digests.
There is no override; a changed release is a new world. A checkpoint written
before release identity carries no digest: it restores, keeps its historical
`Launch`, and, once launched, adopts the running release so every later resume
is bound. `deploy/backup.sh` writes the same identity, plus the archived
ledger's byte length and SHA-256, to `runs/funded.release.json` in every
archive. `deploy/witness.sh` appends `{world, event, ts, release_digest,
ledger_head[, reason]}` for `launch`, `dormant`, `kill` and `failed_resume` to
an append-only file outside the diary and, when `FACTORYLAB_WITNESS_URL` is
set, POSTs the same line; `deploy/README.md` says who calls it when.

The witness requirement is part of the launch identity too (edition 3, R3-C).
Whether a receiver was configured at launch, and which one (the SHA-256 of its
URL; the address itself never enters the diary), is ledgered in `Launch` beside
the release digest and carried in every checkpoint. Unsetting
`FACTORYLAB_WITNESS_URL` afterwards therefore removes no veto: a resume of a
world that launched under a receiver refuses with `witness_required` when none is
configured and `witness_mismatch` when a different one is. The runtime's own kill
lines are written both to `.witness/<stem>.jsonl` and to
`.witness/<world>-<launch_nonce[:16]>.jsonl`, and the second takes nothing from
the diary's filename, so renaming a restored copy of a diary does not move it out
of reach of the record of its death.

### Death and exposure are two states

`production_state ∈ {alive, killed}` and `exposure_state ∈ {flat,
dust_within_precommitted_bound, wind_down_pending, unknown}` (edition 3, R3-C,
GPT-6's third reading §6.D). A kill sets production killed first and
irrevocably — `kill.production` in the diary, a witness line outside it — and
only then runs the wind-down executor (`factorylab/runtime/winddown.py`), whose
whole authority is to cancel, reduce, close and reconcile: it cannot open risk
and cannot resume the population. Every external operation has a durable
identity derived from (launch nonce, coin, market, side, target), ledgered
`winddown.op` before submission and `winddown.op_result` after it, so a repeated
kill or a kill after a restart reconciles by identity and repeats nothing it
completed. What the venue definitively refused or only partly did (rejected, an
IOC that cancelled, a partial fill) is retried under the target's next attempt
identity: up to three rounds in one kill, each re-reading the venue, and a later
kill of the same diary continues the numbering. An ambiguous answer (a timeout, an
exception, a resting order) is read again, never resent. A residual below the
venue's minimum order value (Hyperliquid's $10, above the default `[kill]
dust_usd = "1"`) can never be sold; it is reported in the residual's dust with
`reason = "below_venue_minimum"` and reads `dust_within_precommitted_bound`, not
`wind_down_pending`. A final account read is ledgered as `winddown.reconciliation` with the residual and
the `exposure_state` it implies: an acknowledgement is not a flat account, and a
failed read is `unknown`. Neither a venue nor the diary can prevent death; a
diary failure during the wind-down is counted, printed on stderr and carried to
the witness line.

## Operator controls and recovery

The ledger writer lock and every ledger descriptor are close-on-exec, so no child can inherit one or keep a dead world locked; a jailed run that ends in a timeout, an error or an interrupt kills its confined process group before returning, but `sandbox-exec` has no `--die-with-parent`, so on macOS a confined process can still outlive a runtime that is killed outright.

`factorylab kill --world W --ledger L` takes the ledger writer lock, reopens
the original world, records `explicit_kill:operator`, releases the seal and
exits `3`. Stop the running process first so the lock is available. Stopping
the process alone does not terminate the world. Kill loads no credentials and
makes no network call, unless the manifest precommitted `[kill] wind_down =
true`: then, and only then, it loads the venue credential and runs the wind-down
executor after production is already dead (above). Failures from `run` and `kill` retain the reason
code on the first stderr line and may add the exception class and originating
`factorylab` module on a second line, without provider exception text.

`probe --max-tokens` defaults to `256` for model probes. A paid x402 completion
with empty text is a failed probe. Live `run --duration` sets a wall-clock
deadline as well as the event ceiling derived from the declared interval.
The clock stops before delivering a tick at or after the deadline; it does
not interrupt an in-progress tick. Fake worlds convert duration to event count.

The wake reads the venue only for a world's live Hyperliquid exchange and
the reserve only when the manifest configures a reserve address, with the
respective credentials present. Host credentials alone do not attach live
accounts to a fake world. Public portfolio and window items expose no open positions.

The wake's `returns` view publishes every agent's answer live and unredacted:
one row per `invocation` item as soon as it is in the ledger, with the window,
timestamp, seat id, role, model served, status, cost and the `outputs` exactly
as written (action, rationale, forecasts, register proposals, notes, ballots
with their reasons), the `tool.call` items under the same handle, and the
`Verdict` and `MetaVerdict` events about that handle once they land. Darkness
is not secrecy: what the population wrote is the experiment's product. The page
carries the latest `--returns` rows (default 500, `factorylab wake --returns N`);
every return is also written to `returns-<window>.json` beside `wake.json`.
Every other section still folds to role totals, and the machinery stays sealed
until death: learner state, router weights and sampling propensities, private
memories, prompts and per-decision scores.

Interrupted `sandbox.run` and `observation.run` journal calls are replayable
read-only work and may re-execute after a crash. An authenticated ledger head
whose offset exceeds the file length raises `LedgerIntegrityError`; reopen
does not silently roll back to the shorter file.

## Short rehearsal order identities

`exchange.client_namespace` is an optional 32-character lowercase hexadecimal string,
fixed for a world's life. When supplied, Hyperliquid client order IDs hash the namespace
and decision identity together. Independent preparations use fresh UUID namespaces;
resume retains the original namespace. Absence preserves legacy client IDs and canonical
manifest hashes. Never change it on a living or resumable world.

Because decision handles restart at `decision-1` on a fresh ledger, the namespace alone
cannot separate two runs of one manifest: each launch also draws a `launch_nonce`,
records it in the `Launch` event and folds it into the client order ID, so a rerun can
never reproduce a previous run's identities while a resumed world restores its nonce
from its checkpoint and keeps the identities it already submitted. A venue status answer
whose client order ID belongs to another launch is not this world's order: it is reported
uncertain with that reason rather than booked. Checkpoints written before launch nonces
existed restore none and keep their historical identities.

`scripts/rehearsal.py prepare` creates this namespace and binds the exact voted charter
to its roster hash. `live` requires that charter and a namespace, refuses mainnet, and
marks a prepared manifest used, once its evidence directory is known to be creatable,
before starting the CLI. It does not disable paid treasury routes or Venice; that
prerequisite was removed with the economic caps.
Repetition requires a new preparation, not reuse of old client order IDs.

The public world exposes actual proposal refusals in `registration_feedback` and
judgement, propensity and order refusals in `return_feedback`. Existing checkpoint
buffers remain readable; legacy prefix-only entries are classified on disclosure.

## Edition 3 R3-B: typed custody, and what may move the compute wallet

From GPT-6 Pro's third reading §2 and §3 (`docs/audits/v6/gpt6-third/reading.md`)
and `docs/plans/edition3-r3.md`. Three quantities are kept apart and never
conflated: the **learning score** (evidence for a rule), the **seat entitlement**
(permission to spend inside the compute budget) and the **assets and credits**
held by a custodian, which change only by a verified transaction, a provider
charge, a refund or a purchase — never by an internal reclassification.

### The custody accounts

`factorylab/runtime/custody.py` builds one view, `custody_view(rt)`, with six
accounts, each carrying `status` (`observed` or `unavailable`), a `reason` when
unavailable, and `observed_at_ns`:

| Account | What it holds | Read from |
| --- | --- | --- |
| `openrouter_credit` | prepaid model credit at OpenRouter | treasury pots (`seed`) |
| `venice_credit` | prepaid model credit at Venice | treasury pots (`sellers.venice`) |
| `venue_perps` | perps equity, cash, margin used, positions | the tick's account read |
| `venue_spot` | the venue's spot balances | the tick's account read |
| `base_reserve` | USDC at the reserve address on Base | treasury pots (`reserve`) |
| `pending_conversions` | transfers in flight: a held source and a claim at the destination | `treasury.state` and its strands |

Beside them, `authority` — the compute wallet — labelled as what it is: the
constitutional ceiling on spending, not an asset, and not a seventh pot to add
to the others. `Wallet.pots()` and `Treasury.pots()` carry the same label.

Nothing in the view is invented. A venue read that fails renders
`venue_perps` and `venue_spot` `unavailable` with the exception that caused it;
the old fallback, which answered an unreachable venue with the compute wallet's
balance and an empty position list, is gone from `_world_block`, from the tick
payload in `_producer_step`, from `_equity_micro` (which now returns `None`, so
every ratio measured against window equity is honestly unmeasured) and from
`_world_resources` (`trading_equity_usd` is `null`, never the reserve pot).
`_tick_account_observation` memoises the failure as well as the answer, so a
tick's hundred prompts ask an unreachable venue once.

### What moves the compute wallet

Model, tool and program charges; rent, as authority; releases; transfers between
seats; verified income; and confirmed conversions into provider credit. That is
the whole list.

Venue P&L, fees and funding are not on it. They settle on the venue accounts,
which are the record of them, and the diary carries one `venue.settled {custody,
amount, reference, reason, handle, event}` item per effect — `custody` being
`venue_perps` or `venue_spot`. The wake reports the same figures it always did
in `money.in_by_class` / `out_by_class` and now says where each class moved:
`money.custody_of_class` names `venue` for `exchange_pnl` and `funding` and
`authority` for the rest, and `money.venue_by_custody` totals the venue effects
by account. The scripted rail's venue pot is read from the venue rather than
derived from the wallet, and a scripted transfer moves the venue's own cash.

### The bridge

A confirmed `to_venice` transfer decreases `base_reserve` by the principal and
increases `venice_credit` by what arrived, and does nothing else: it implies no
OpenRouter replenishment, and the ledger says so in
`treasury.financing {class: "financing", source, destination, principal_micro,
credit_micro, implies_openrouter_replenishment: false}`. Principal converted
into compute is financing and is counted in `converted_from_principal_micro`,
never in `earned_micro`. While the transfer is in flight it is a held source and
a pending claim in `pending_conversions`, never a balance in two places.

### Income receipts

A receipt's identity is chain, transaction hash, log index, asset and recipient
(defaults: `base`, `USDC`, the reserve). `Treasury.earn` is idempotent on that
identity: the same payment twice books once, and a *different* payment presented
under one identity fails closed with `income.conflict` and books nothing. The log
index is normalised to an integer and a missing recipient is the reserve, and a
transfer is also deduplicated across spellings: the same transaction and recipient
with the same log index, or the same amount where either side has no log index, is
the same transfer (`income.duplicate`, nothing booked). A confirmed claim is booked
under the chain's own identity (the log the transfer is at, the recipient it
reached); two equal transfers to the reserve in one transaction and a claim that
names no log index are ambiguous and the claim stays unresolved. A receipt a claim
became is handed to the runtime's credit exactly once, even when `Treasury.tick`
verified it; the hosted seller (`deploy/serve.py`) spools the recipient.

The seller's spool is the wake host's word, not a payment. `collect_income`
books each spool row as a **claim** (`income.claimed`, counted in
`pots.claimed_micro`), and `Treasury.verify_receipt` promotes it to income only
when the rail's chain read confirms the transfer: `LiveRail.verify_receipt`
reads the Base transaction and looks for a USDC transfer to the reserve of
exactly the claimed amount, at the claimed log index when one is given. It
answers confirmed, contradicted (`income.conflict`, nothing booked) or unknown,
and an unknown leaves the claim standing. A rail with no chain read confirms
nothing. Verified income lands in `base_reserve` (`income.custody`) and raises
the authority it backs. A paid call settled in process, through the facilitator,
on an authorization the runtime verified itself, books directly.

### Collateral

Both adapters expose `collateral_view(coin, market)`: `account_mode`,
`collateral_asset`, `eligible_equity_usd` (the perps account alone — spot marks
are not collateral for a perp), `margin_used_usd`, `open_order_holds_usd`,
`holds_included_in_margin_used`, `leverage_for_instrument`, `position_size`,
`spot_available` and `observed_at_ns`. `AccountState` now states
`perps_equity_usd` beside `equity_usd` so the split is read, not derived.

`_order_collateral` checks incremental margin, plus holds not already reflected
in margin used, plus the manifest's precommitted headroom, against eligible
equity minus margin used. Spot is checked separately and against its own
balances: a buy needs the USDC (`spot buy exceeds venue USDC balance`), a sell
needs the base coin (`spot sell exceeds venue base balance`). Unknown collateral
(`order collateral unavailable: <exception>`) and stale collateral (`order
collateral is stale: venue account older than one tick`, which is how
Hyperliquid's fallback to its last complete snapshot reads) block new risk, and
neither ever blocks a cancellation or a `reduce_only` reduction. A failed
Hyperliquid mids read raises `VenueUnavailable` and is never answered with the last
prices; an account fallback to the last complete snapshot is returned with
`stale = true` and its original `observed_at_ns`, and the prompts (`StaleAccount`),
the watchers, a window's opening equity and the wind-down's final reconciliation
(`unknown`, never `flat`) all refuse it.

`[venue] collateral_headroom_usd` is an exact nonnegative decimal string,
default `"0"`: free collateral the world precommits to leaving unused, declared
before the orders that would want it. It is not `[kill] dust_micro`, which is a
different setting for a different thing. At its default the key is dropped from
the canonical manifest JSON, so no world that predates it changes hash.

`[venue] principal_usd` and `[tools] max_leverage` are **deprecated and inert**
(architect decision D1: a cap on the principal or the leverage the population may use
is an objective supplied from outside, a Class-2 imposition). Both keys are still
read and validated, and both still enter the canonical manifest JSON exactly as
before, so every manifest that declares them loads and keeps its historical hash;
nothing enforces either. `_collateral_view` is the venue's own view, unchanged, and
`venue.set_leverage` takes any positive integer and lets the venue accept or refuse
it. The first launch gate is met by holding only the proposed principal at the venue.

The margin an order needs is charged at the leverage the venue has in effect for the
instrument: `leverage_for_instrument` is what Hyperliquid's `clearinghouseState`
reports for an open position on the coin, or failing that the venue's acknowledgement
of this account's `set_leverage` (the fake reports its own per-coin setting). When the
venue has not said — a coin with no position and no acknowledged `set_leverage`, or a
resting order on such a coin (`open_order_holds_usd` is then `null`) — the local check
does not guess a 1x requirement: it admits the order and the venue's acceptance or
rejection is the answer.

### The reward line

`_credit_consequence` books the consequence in parts rather than as one number:
`provider_cost_micro`, `venue_delta_micro` (a map by custody), `position_open`,
`commitment_settled`, alongside the entitlement movement `net_micro`. The
inbox item carries them.

## Edition 4 factors: prompt, address, feedback

Three keys turn on one edition 4 change each. All three are elided from the
canonical manifest JSON at their defaults, so every manifest that predates them
keeps its hash, and none of them changes a roster digest: a charter ratified on a
roster is still ratified on it when a factor is switched on.

`[prompt] mode` is `"reference"` (the default) or `"compact"`. Under `reference` a
request carries the whole institutional world inside the cached prefix, which is
what every world did before this key existed. Under `compact` the prefix keeps the
charter norms, the capability index with one line and one price per capability, and
the sections a return is validated against (`a_return_may_include`,
`reserved_return_fields`, `action_labels`, `accounting_facts`, `meta_input`). The
rest of the reference — the registries, the catalogues, the settlement rules — is
replaced by `sections_not_carried`, a directory naming every held-out section with
its exact handle, its entry count, its byte size and the route that reads it. A
compacted section is never moved into `INPUTS` and never dropped: it is retrieved
through the world-reading tool, which returns the same object the world block
publishes and the action validators read. On the scripted world the rendered
producer prompt measured 65,436 versus 36,293 bytes in the integration fixture, and every section other than the
prefix is byte-identical between the two modes. No byte ceiling is enforced
anywhere; `Request.section_bytes` measures what was actually sent.

`[tools] address_enabled` is exactly `true` or `false`, default `false` (a truthy
string or `1` is refused). It gates whether the world publishes the voluntary
addressing capability. It schedules nothing and wakes nobody. When a return records
a call to `address.send`, the projection that crosses a contract boundary
(`public_return`, `public_tool_calls`) keeps the capability, the recipient, the
price and the size of what was said, and drops the body under any of the names
`text`, `body`, `message`, `content` or `payload`, at whatever nesting the return
wrote it. A judge prices an act it can see the shape of; it does not read the
message. The sender keeps its own copy in its working state, which no projection
touches.

`[evaluation] producer_feedback` is `"verdict"` (the default) or `"realized"`. Under
`verdict` a producer decision settles on the judge opinion it drew, which is the
shipped line. Under `realized`, initial opinion is provisional. Before the producer
acts, the runtime freezes all charter norm definitions, the separate pricing cards,
existing predicate versions, evidence baseline and tick horizon. A fresh independent
evaluator later interprets attributable economic outcomes, execution receipts and
resolved forecasts against the producer's claim under those frozen norms. Windowed
pricing cards are not the sole criteria for valuing an individual decision. Historical
contracts without frozen norms retain that absence on restore; current norms are not
silently substituted. The current feedback definition is `realized-consequence-v2`. This is
consequence-grounded evaluation, not an objective utility oracle or a pure-P&L score.
Supported and contrary findings must cite supplied evidence. Unknown findings have no
numeric score and produce no learner update; a timed-out pending assessment also cannot
train early. One malformed final finding can be retried by a different evaluator,
within the close horizon. Both top-level and child producer decisions use this path.

`grounded_horizon_ticks` is an exact positive integer, default `10`. It controls when
the first final consequence-grounded commission becomes due and is independent of
`forecast_horizon_events`, which continues to govern ordinary forecasts. Closure is
bounded by a further `max(horizon + 1, verdict_timeout_ticks)` ticks. A final unknown
finding may close earlier. Late adoption is not retroactively scored. Card
penalties retain the existing originating-measurement-window rule, including the lambda
at that window's close; the numeric lambda is not frozen at decision time.

In realized mode, paid population-tool executions generate version-bound receipts for
caller and maker. These distinguish same-lineage and cross-lineage use. They contain
result hashes, not private argument or result bodies, and execution alone earns no score.
An unknown configuration value is refused at load. All factors are fixed at launch.

Assembly proposals may include `endowment_micro`, an exact positive integer transferred
from the founder's available entitlement. Omission retains `trial_amount_usd` as the
default transfer. A chosen endowment does not consume that entire amount from shared
novelty: admission still uses the fixed trial amount. Unaffordable founding is refused
before registration; children retain their founder's lineage for commons allocation.
The amount is recorded in the private budget transfer ledger, not public registration.
This funds a continuing participant; it is distinct from a `requests[]` child invocation,
whose actual cost is charged to the requester. It does not introduce individual bankruptcy
or a fixed lifetime: later commons allocation remains possible.
