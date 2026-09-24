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
| `venue.max_readers` | Integer ≥ 1 | `16` | Yes: the venue read slots. Seeds take slots in manifest order; a registration takes the lowest free one; a retirement frees one, given again only once its last holder's last read has left the sliding minute; a seat without one registers all the same, without the venue read tools. The venue read share divides by it (see "Seeing the world"). The population itself has no size cap: `tools.max_seats` was removed and is refused |

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
delivered subject with `about_handle.ignored`, and the reason reaches the judge's
own outcome inbox.
An existing but forbidden handle is refused, not replaced. A requested judge
may address only its requesting decision or that decision's ancestors. The
ancestor self-judgement check still refuses those subjects, so this restriction
does not grant permission to judge the requesting chain. A payoff judgement on
a subject not chosen by the router also passes the hindsight check, including
a parent-selected subject. A fixed consequence, expired backstop or judgement
deadline beyond that backstop is refused. Judgement `return.refused` items
deliver their reasons to the judge's own outcome inbox.

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

`assemblies[].max_tokens = "provider"` uses the provider's advertised completion
allowance, shared by reasoning and the visible answer. The shipped testnet roster
and new edition-four rehearsals use this mode; they do not impose a smaller seat
ceiling. The resolved allowance is recorded and used for the contract and prepaid
reservation. Missing output-limit metadata refuses admission before inference;
context length is not substituted for an output limit. OpenRouter exposes this
metadata; Venice exposes `model_spec.maxCompletionTokens`.

An omitted or null `register` assembly `max_tokens` likewise requests the provider
allowance. Participants may explicitly choose a smaller positive integer (at least
16), and there is no 4,096-token registration maximum. Programs require no model
completion allowance. Historical manifests with numeric limits preserve their exact
values and identities. A native allowance is not a guarantee of valid JSON or
unlimited computation: the model's physical limit and the seat's ability to pay still
apply. OpenRouter and Venice chat completions have no client processing timeout;
control-plane reads retain their network timeout. Run-duration checks occur between
completed operations, so a long completion can outlast the requested run duration.
Stopping an in-flight process still leaves its full quote as uncertain liability;
there is no automatic retry.

`models[].contract` states how a route carries each request's I/O contract
(Chapter II §II.b: physics is enforced, not announced). It defaults to
`"json_object"`, which asks the host for JSON syntax alone. `"json_schema"` hands
the contract to the host's constrained decoder as `response_format.json_schema`
(`strict: false`). The schema has one form for each reply shape the kernel
distinguishes: the final answer, a continuation (a non-empty `tool_calls` or
`requests`) and the refusal form (`status: "cannot"` with a `reason`). Each form
is the intersection of what the kernel checks a reply against: the universal
envelope, the fields the answer's kind owns, and the contract. A form the kernel
cannot accept is not sent. For example, a closed contract that does not name
`tool_calls` has no continuation through it. Every object the contract leaves
open is marked open. A final answer whose contract publishes `counterfactual`
(a judged or exposure kind; see "A verdict is also a prediction") is sent as two
forms: one that requires `counterfactual`, and, for ProducerReturn and Exposure, one
whose `action` is `"order"`. The decoder cannot see the venue writes of a
decision's earlier rounds, so it is given the stricter side. What a schema cannot
state (an answer order's semantics, a child request's checks, the runtime's
validator, including whether the named coin is listed) stays the kernel's alone.
Hosts enforce the schema on a best-effort basis: the 23 September 2026 probes
saw json_schema routes still return replies outside it. On OpenRouter, `provider.require_parameters` is set
unless `extra_body` names it, as it is for `json_object`. The key is accepted only
on `openrouter` and `venice` routes, and any other value is refused at load. It is
fixed for the world's life. A route changes contract only in a new manifest; a
refused schema never falls back to `json_object` mid-run. Either way the kernel's
own validation of the reply is the authority, and the prompt's `outcome_schema`
section is unchanged. OpenAI-hosted routes stay on the default: their hosts
refuse a schema whose root is a union, and strict mode would require every
property and close every object, which is a different contract.

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
| `treasury.max_venice_per_window` | Exact USD decimal string or integer, nonnegative | `"10"` (10,000,000 micro-USD) | Configured resource bound per `treasury.cap_window`, fixed for a run; not amendable through metric cards |
| `treasury.cap_window` | Duration, at least `timing.min_ratio` declared ticks | `"1h"` | The Venice and forwarding-fee caps' own wall-clock window, counted from launch. A declared money bound, not derived from any measured loop: a money rail runs in wall time, so its rate cap is a duration, and it no longer borrows the pricing window (time audit T1, T13) |
| `treasury.cctp_forwarding` | `"never"`, `"on_empty_gas"` or `"always"` | `"on_empty_gas"` | Configured route rule, fixed for a run |
| `treasury.max_forward_fee_usd` | Exact USD decimal string, nonnegative | `"0.30"` ($0.10 of headroom over the $0.20 quoted on both networks) | Hard bound on the on-chain forwarding fee quote per exit; a higher quote refuses before signing |
| `treasury.max_forward_fees_per_window` | Exact USD decimal string or integer, nonnegative | `"1"` | Per-cap-window cap on forwarding fees quoted for submitted exits; a failed exit still counts |
| `treasury.forward_wait_ticks` | Integer, at least `timing.min_ratio` | `360` | Declared wait, in world ticks, before a forwarded mint or a hybrid top-up that stays undone strands recoverably. Once `timing.min_support` conversions have finalized, the wait is derived instead: `timing.min_ratio` times the capital loop's p90 conversion (open to finalized, in ticks consumed, so an outage adds nothing) (time audit T13). `treasury.forward_wait_windows` is refused |
| `treasury.venice_network` | Absent, or `"base-mainnet"` | Absent | Hybrid capital-loop rehearsal: `to_venice` buys real Venice credit from the Base mainnet reserve and pays for it in the testnet pots with a shadow send (docs/architecture/capital-loop-rehearsal.md). Refused on a mainnet venue and without `venice_shadow_sink` |
| `treasury.venice_shadow_sink` | Nonzero EVM address, only with `venice_network` | Absent | Where the shadow leg's testnet USDC goes; must be an existing Hyperliquid testnet account outside every observed pot |
| `treasury.max_venice_total_usd` | Exact USD, positive; required with `venice_network`, refused without it | Absent | Absolute bound on real USDC ever authorized for Venice in the world, re-authorizations included; the counter is checkpointed |
| `treasury.venice_reserve_floor_usd` | Exact USD, nonnegative; required with `venice_network`, refused without it | Absent | No top-up is prepared if the Base mainnet reserve would fall below it (read on chain, so a fresh run cannot reset it); the capital-loop runner reads the reserve keylessly at launch and refuses unless reserve − floor ≤ `max_venice_total_usd` |
| `treasury.venice_pay_to` | Nonzero EVM address; required with `venice_network`, refused without it | Absent | The only payee a Venice top-up quote may name; a quote or journaled authorization paying anyone else is refused before signing |
| `committee.seats` | Integer, at least 3 so the existing three core roles can be covered | `5` | Configured resource bound, fixed for a run |
| `committee.promise_resolution` | Finite positive number | `0.01` | Fraction of the frozen region's scale a promised move must clear to count |
| `committee.quorum` | Integer in `[1, committee.seats]` | `3` (the smallest body in which a strict majority is not unanimity, so no one seat passes or blocks alone; also `committee.seats`' floor) | Launch cast, fixed for the world's life. Fewer eligible assemblies than this seat no committee at a governance boundary (`charter.seat_deferred`); a motion with fewer voting seats than this, once its proposer is excluded, waits for the next boundary; a retirement or connector below it is refused |
| `norm_house.signer` | Absent, or a 0x-prefixed 20-byte EVM address (stored lower-case) | Absent: no norm edition is possible | Launch cast, fixed for the world's life and hashed: the one key whose signature makes a norm edition valid (essay II.IV.a, the input layer's write permission is part of the hard kernel) |
| `charter.edition` | Positive integer | `1` | The edition the world launches with; after 1 it requires `parent_charter_sha256` (charter audit P5) |
| `charter.parent_charter_sha256` | 64 lowercase hex characters | Absent; required when `edition` is after 1 and refused at 1 | The content digest of the charter this one descends from. Part of the charter's content digest and of the manifest hash (`charter_parent_sha256`) |
| `charter.norms` | Nonempty array of names, or of `{ id, definition }` tables | Required; edition 3 carries definitions, editions before it carry bare names | Read-only for the edition. A bare name loads with an empty definition, so a charter surveyed before definitions existed keeps its content digest; `Charter.render` prints each definition under its norm |
| `charter.cards[].window.kind` | `"returns"`, `"forecasts"`, or `"windows"` | Required for explicit cards | Executable selector type; its value is population amendable |
| `charter.cards[].window.n` | Positive integer, never a boolean or float | Required; seed cost and well-formedness cards use `100`, forecast skill uses `50` | Population amendable sample horizon |
| `charter.cards[].window.per` | `"role"`, `"assembly"`, or null | Required in JSON; omitted in TOML means null. Seed cost and well-formedness use `"role"`; forecast skill uses `"assembly"` | Population amendable scope |
| `charter.cards[].answers_for` | `producer`, `evaluator`, `meta`, `antagonist`, `all`, or any registered emitted kind | Required | Population amendable pricing responsibility |
| `charter.cards[].region` | `{ rule, lo, hi }`: `rule` one of `at least`, `above` (with `lo`), `at most`, `below` (with `hi`), `between` (both, `lo < hi`), `below the median of the previous window` (neither) | Required unless `acceptable_region` states it | The typed acceptable region (charter audit P2); the rendered sentence is derived from it. Hashed with the card |
| `charter.cards[].acceptable_region` | One of the historical sentences (`at most 0.30`, `above zero`, …) | Accepted in place of `region`; with both, they must agree | Read into the same typed rule; a sentence no rule reads holds no region and carries no price, and a manifest refuses it |
| `charter.cards[].holdout` | Array of `predicate-id@version` | `[]` | Registered predicates a closed window must also satisfy (charter audit M3); appended by a holdout motion, never by a cards motion |
| `charter.cards[].window.interval` | Absent, or `{ level, half_width }`, `level` in (0, 1), `half_width` > 0 | Absent | A scope is measured only when the `level` interval of its mean is at most `half_width` wide on each side (charter audit M3) |
| Proposal `predicted_effect.card_id` | Current or proposed card id for cards and lambda amendments; current card id for connectors and retirements | Required unless `observation` is given; no default | Liability binds to a measurable card |
| Proposal `predicted_effect.observation` | `burn_per_window` or a population-registered observation id | Required for, and only for, a clock amendment (`tick_interval`) | Speed is cash burn (charter audit M6): the promise is graded on the observation over one closed window, its region the observation's declared range |
| Proposal `predicted_effect.direction` | `increase` or `decrease` | Required; no default | The promise graded against the baseline recorded at activation |
| Proposal `predicted_effect.window` | Positive integer count of closed price windows after activation | Required; no default | Population-authored liability horizon. The promise is graded at the later of that count and `timing.min_ratio` measured consequence periods, in ticks, after activation (time audit T2) |

The existing `committee.min_settled`, `novelty.share`,
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
`charter` (edition, text, live cards with their prices and regions, actual
pending changes, and amendment eligibility). `pending_changes.waiting` contains
only approved ids still waiting; when it is empty, it carries no activation
timestamp. The cadence threshold remains available under
`amendment_eligibility` as `eligible_no_earlier_than`, explicitly an eligibility
boundary rather than a scheduled charter change. `catalogue` carries version
and changed entries only; `public_observations` carries the last closed window's
values, pathologies and recent prints,
and `unavailable_observations` — every source that could not be read, with the
reason. No private state is in this block; a seat's own state appears exactly
once, in `YOU`. Everything else the world publishes — `inputs.you`, the event,
the pots, the reserve remaining, `tick_intervals`,
`adaptive_scoring`, the tool, connector, work and
observation catalogues, the mechanics and the scoring formulas — is rendered
after those, inside `INPUTS`. A key of the world block is rendered in exactly
one of those four places: the partition is `PREFIX_WORLD_KEY` with
`PREFIX_SOURCE_KEYS`, `SEAT_WORLD_KEYS`, `UPDATE_WORLD_KEY` with
`UPDATE_SOURCE_KEYS`, and everything left over. A source key stays in the world
block, which is the runtime's own disclosure surface, and is rendered only
through the block that carries it. The controller re-prices every card at every
closed window, so the cards ride in `WORLD UPDATE` and never in the prefix.
Where the provider reports `usage.prompt_tokens_details.cached_tokens`, the
runtime records it as `usage.cached_tokens` on the `invocation` item, beside two
bounded diagnostic hashes: `prompt_cache.stable_prefix_sha256` identifies the rendered
stable block, and `prompt_cache.effective_leading_messages_sha256` identifies
the system message, any preceding handle-scoped messages, and that block in
their effective order. No prompt prose is added to the invocation record. These
hashes distinguish local prefix drift or memory reordering from a reported miss
on identical local input; they do not claim that an upstream cache must hit.
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

The **OUTCOME CONTRACT** is rendered once per request, immediately after the
outcome schema it is about. It states what every return must satisfy and
nothing else: the tool-round protocol and the refusal form
(`{"status": "cannot", "reason": ...}`). The execution-claim taxonomy, the pause,
forecast and monetary-unit paragraphs and the duplicated fidelity-objection shape
of §8 were removed (Chapter II rulings, smuggling audit D1): no code read them,
and a fidelity objection's shape is published in the evaluator answer schema.

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
fixed primitives are (a resume included), free (arithmetic in the world's own
process pays no one), and metered and ledgered like any other tool. It answers every fee, funding and
carry case of `scripts/calibrate_seats.py` exactly.

## Round-two W2: judges, consequences, the reserve

### `[evaluation]`

| Key | Type | Default | Cast | Meaning |
|---|---|---|---|---|
| `consequence_share` | float in [0, 1) | 0.3 | hard | Base weight of payoff standing in evaluator selection; the live actuator starts here. |
| `adversarial_share` | float in [0, 1] | 0.15 | hard | Cap on the router's probability mass over antagonist assemblies (A5). The essay's "minority" is a constraint, not a prize. |
| `sampling_step` | float in [0, 1] | 0.1 | hard | Step by which the consequence mix rises per divergent window and steps back otherwise (A14, the live sampling-rate actuator). |
| `sampling_cap` | float in [consequence_share, 1) | 0.7 | hard | Ceiling of the raised consequence mix (A14). |
| `multi_judge_share` | float in [0, 1] | 0.3 | hard | Share of judged returns drawn again until `multi_judge_count` judges read them (Wave 5a; evaluations P6, M2). Drawn once per judged return from the runtime's seeded stream; 0 draws nothing. |
| `multi_judge_count` | int in [2, 5] | 2 | hard | Draws a multi-judged return receives. Each further draw is an ordinary routed decision from the kind's first router, over its menu less every seat drawn for the return and every seat on a drawn seat's family (`route.multi_judge`). |
| `meta_read_share` | float in (0, 1] | 0.5 | hard | Of a cascade window's completed judgements, the share released to the tier above at its close, the representative first and then by the same rank (`cascade.release` with `companion_of`; evaluations C7). Edition 6 sets 1.0. |

### The evaluator population (Wave 5a; evaluations C1, M3, P6)

A world whose seeds emit a `Verdict` is refused at load unless all of these hold
(`WorldManifest.evaluator_population_problems` names each failure):

- **Producers are the minority.** Evaluator seats (every seed with a verdict,
  conformity or counter reward shape: judges, metas, adversarial judges) strictly
  outnumber producer seats (every seed with a judged or exposure shape, antagonists
  included). Essay II.III.b: producers are "now established to be the minority of the
  superdark factory's population". After launch the population's own registrations
  and retirements are never refused for the mix they make; the kernel ledgers
  `population.evaluator_majority` (`held`, the counts, the cause) each time the
  majority is lost or regained. The evaluators' share of compute is not enforced: the
  seed observation `evaluator_compute_share` (evaluator-role compute over all compute
  in the window) is published every window for a card to price.
- **At least three model families serve the evaluator tier.** A family is the
  foundation model, not the route (`runtime/families.py: model_family`):
  `venice:z-ai-glm-5-3-flash` and `z-ai/glm-5.3-flash` are one family, and so are
  `openai/gpt-5.6-sol` and `openai/gpt-5.6-luna`. A `fake-` test double is its own.
- **Every return has a judge off its author's family**, and two judges are possible
  while `multi_judge_share > 0`: every judged kind a seed emits is accepted by judges
  on at least `multi_judge_count` families other than its author's.
- **Every chain a tier can be asked to grade has a reader.** Each (judge, producer)
  pair of families a Verdict can carry is read by a meta on neither; when any seed
  reads MetaVerdicts, each (grader, graded) pair a MetaVerdict can carry is read by a
  seed on neither, to every depth the roster reaches. Every adversarial judge can
  read some Verdict the roster makes.

Routing never draws a judging seat (a verdict, conformity or counter shape) on the
family of either of the two nearest authors of the chain it would judge: a judge
avoids the producer's family, a meta the judge's and the producer's, a grader of a
meta the meta's and the judge's (`route.excluded`, reason `same-family`;
`RoutingMixin._chain_families` argues the depth). The tier above a meta is counted
the same way. A draw every eligible seat of which is barred is ledgered
`route.barred` and counted (`stats.route_barred`); it opens no decision. Edition 5, edition 3 and their capital-loop copy fail all
three checks and no longer load (R8); `worlds/edition6-capital-loop.toml` replaces
the capital loop's world.

### `[chaos]`: the chaos actuator (Wave 5a; essay II.III.b; evaluations M1)

| Key | Type | Default | Cast | Meaning |
|---|---|---|---|---|
| `venue_unavailable` | float in [0, 0.5] | 0 | hard | Per tick: every venue read a seat makes that tick answers `{"status": "unavailable"}`, and a Tick payload's account and mids read unavailable. |
| `stale_mids` | float in [0, 0.5] | 0 | hard | Per tick: the mids a seat is shown (the Tick payload, `recent_mids`, source freshness, its fold) stay the tick before's. |
| `tool_withheld` | float in [0, 0.5] | 0 | hard | Per population-tool call: its result is withheld and the call is neither run nor charged. |
| `connector_timeout` | float in [0, 0.5] | 0 | hard | Per connector fetch: it times out before anything is fetched or charged (`connector.refused`, reason `timeout`). |

Each fault is drawn from the runtime's seeded stream (a zero rate draws nothing),
counted in `stats.chaos_faults`, ledgered as `chaos.fault`, and marked on the event it
happened in, where the seed predicate `failure_within` reads it. No fault moves money:
a faulted call returns before the meter reserves, venue writes and treasury transfers
are never faulted, and the venue reads behind the pre-submission collateral check, the
fills, the reconciler, settlement, custody and the kill path never consult it. The
world's own record (`recent_mids`, which prices a declined trade, and the window's
public facts) keeps every print.

### `[novelty]`

| Key | Type | Default | Cast | Meaning |
|---|---|---|---|---|
| `trials` | int >= 1 | 3 | hard | Settled consequences delivered to a population assembly before its protected trial ends (A13). Replaces `trial_invocations`, which counted model calls; continuations and children do not count. |
| `seat_share` | number in (0, 1] | 0.25 | hard | The most of one consequence period's share of the novelty reserve one seat's unhistoried actions (tool calls and the rounds that read them) may use, so no seat starves the registration trials (ruling R5; the #134 review). |
| `max_lifetime_windows` | int >= 1 | 6 | hard | Reserve windows after registration after which the trial ends regardless of deliveries (A13). |

### Ledger evidence these keys produce

`route.excluded`, `tool.refused`, `consequence.refused` (A9); `exposure.settled` (A5);
`meta.consequence`, `sampling.raise`, `sampling.lower` (A14); `novelty.release`,
`novelty.compute`, `niche.action`, `decision.actions` (A13, ruling R5; older diaries
also carry `novelty.grant` and `novelty.grant_consumed`); `receipt.execution`,
`receipt.learning` and `receipt.commitment`. The reward chain (ruling R1) adds
`verdict.mean`, `verdict.consequence`, `consequence.opportunity`,
`evaluator.meta_grade`, `evaluator.settled`, `evaluation.censored`,
`evaluation.declined` and `router.abstention_priced`. `evaluation.sibling_share` is
refused at load (evaluations U2).

A closed lot's realised P&L is credited once (edition 2, cold audit F7). A
handle that opens and closes its own lot receives the whole of it, net of its
opening fees, funding, other charges and closing fees. A distinct closer takes
the part its exit notional contributed, `pnl × exit_price / (entry_price +
exit_price)`, net of its closing fee; the opener keeps the rest, net of its
opening charges. The two credits sum to the lot's P&L and never both hold it in
full. A liquidation has no closer: the liquidated opener carries the whole P&L
and the liquidation fee. `return_paid_off` reads the result credited to a
return as opener or closer, so a trade cannot pay off twice.

### The reward chain (ruling R1; Chapter II §III.b)

**Producers learn from verdicts.** A producer decision settles on the mean of the
verdicts its judges gave while it waited, less its card penalty (`verdict-v1`).
Verdicts are collected while an event is routed and settled when its routing is
done, so every judge a draw woke on a return counts and none alone
(`verdict.mean` names them when there is more than one).

**A verdict is also a prediction.** When the world resolves the judged return,
the kernel scores the verdict `q` against a measured outcome `y`:
`brier = 1 - (q - y)^2`, `base = 1 - (b - y)^2` with `b` the base rate of that
kind of outcome before this return's own entered it (once per return, however
many judges read it), and `consequence score = 0.5 + 0.5 * (brier - base)`, which
stays in [0, 1] and is a proper scoring rule (an affine map of Brier)
(`verdict.consequence`). A judge the world proved wrong earns less than one it
proved right, and one that only repeats the base rate earns 0.5. `y` is:

- for a return that executed venue operations (or earned service income):
  `return_paid_off`, 0 or 1, fixed when its lots close or marked at the
  consequence backstop. A venue write counts once the venue accepted it or may
  have (`uncertain`); a return whose every write the venue rejected executed
  nothing;
- for a return that executed nothing and named a declined trade
  (`counterfactual {coin, side}`): `opportunity-cost-v2`,
  `y = 0.5 - 0.5 * tanh(g / opportunity_scale_bps)` with `g` the trade's gross
  move in bp, signed by its side and excluding fees, from the mids the world had
  broadcast when the return was made (ruling R2). It is symmetric and monotone,
  so a hold without directional skill earns 0.5 whatever trade it names;
- for a return whose answer order (`{"action": "order", coin, side, size}`) was
  refused (by the collateral check, the venue, or a terminal error) and that
  executed nothing else: `attempted-trade-v1`,
  `y = 0.5 + 0.5 * tanh(g / opportunity_scale_bps)` with `g` the ordered coin's
  gross move in bp, signed by the ordered side and excluding fees, from the same
  frozen mids and horizons as the declined form, of which it is the mirror: 0.5 at
  no move, toward 1 as the market moves for the ordered side. It is ledgered as
  `consequence.attempted_mark` and `consequence.attempted`. An order left
  `uncertain` is acting, and is measured by `return_paid_off`;
- for anything else (a return made while the world listed no coin, a declined
  commission, or a named coin with no mid at the horizon): nothing. Only the tier
  above grades it.

**The declined trade is part of the return contract** (§III.b: evaluators are
graded by realized consequence, the priced road not taken included). A final
answer of ProducerReturn, Exposure or a declared kind whose reward shape is
`judged` or `exposure`, from a decision that executed no venue operation (no venue
write the venue accepted or left `uncertain`, and no answer order it may place),
carries `counterfactual
{coin, side}`: `side` is `buy` or `sell`, and `coin` is a key of `recent_mids` (the
world's broadcast mids, the record the trade is priced from) when the return is
made. Without it the return is `malformed`
(`counterfactual {coin, side} is absent from a return that executed no venue
operation`), as it is with a coin the world does not list (`counterfactual names a
coin the world does not list`) or any other shape; the seat's inbox and
`return.validation_failed` carry the reason. Nothing is required while
`recent_mids` is empty. A return that executed venue operations needs none. The
field is published in the request's outcome schema (a requested child's too, even
under a closed requester schema) and in `world.read {"section":
"a_return_may_include"}`. Policy ballots, judgements, forecasts, metas and counters
are not returns a first-tier verdict judges and carry no such requirement; neither does a
declined commission (`status: "cannot"`), which is not a contract return. The
scoring above is unchanged.

**Anticipatory settlement** (§IV.b: an explorer is compensated sooner than the
lifetime of what it found). A verdict's reward is scored as soon as its return's
outcome is fixed, or at the latest `consequence_horizon_ticks` after the return
opened, on its mark then: the lots marked to the mids then
(`consequence.marked`), the declined trade priced then
(`consequence.opportunity_mark`). The final measurement (a fixed payoff, or the
declined trade priced at the backstop, `consequence.opportunity`) then trains
the judge's standing and the base rate once (`verdict.consequence_late`) and
never re-settles the reward. The timing is never a charter price window
(evaluations P7). A judgement may choose a target other than its delivered
subject only while that target's outcome is unanswered: not fixed, marked or
priced, and before its horizon.

`[evaluation] consequence_horizon_ticks` (integer in [1, backstop], default 10)
and `opportunity_scale_bps` (positive number, default 50) are hashed.

A declined commission (`status: cannot`) is credited to the router that drew the
seat as an abstention is, the zero-consequence reward less its role's card
penalty (`router.decline_priced`), never the seat's own mean. The meta tier's
cascade window reads first a verdict on a return with no world outcome, since the
tier above is that verdict's only grader.

**The judge's reward is both signals.** A judge's decision settles
(`evaluation-v1`, `evaluator.settled`) on the equal mean of its grade from the
tier above (the mean of the grades metas gave it while its grade window was
open, `evaluator.meta_grade`) and its consequence score, whichever exist, less its
card penalty; with neither it settles censored (`evaluation-unscored-v1`).
Neither channel is weighted by the charter. The argument is in
`runtime/feedback.py: evaluation_reward`. The router that drew the judge learns
the same reward, so a judge decision's deadline covers the return's backstop.

**The grade window is the read above it.** At every tier, an evaluator decision's
grade window closes on the tick after the cascade window holding its judgement
released it to the tier above or passed it over (`FeedbackMixin._grade_window_over`).
A cascade window lasts `timing.min_ratio` times the measured period in which the
decisions its tier judges reach a score, and a meta's judge settles no sooner than
its own grade window, so a grade window of a fixed `verdict_timeout_ticks` closed
before the tier above could read the metas (essay II.IV.c: the queue withholds a
verdict until it settles, at a 3:1 ratio or more; II.III.b: evaluators are graded
tier upon tier). Before this, 0 to 4 of the 11 to 17 tier-three grades delivered in
200 events counted on seeds 1 to 5 of the recursive scripted world; every one now
counts. A judgement held in a window closes at the latest after
`consequence_backstop_ticks + verdict_timeout_ticks` plus that window's drawn
duration; one no window took (a judgement a judge chose rather than a routed one)
waits `verdict_timeout_ticks`. A judgement whose decision has not settled when its
window releases is withheld, not dropped (II.IV.c: "withheld ... until it
settles"): it is carried into the tier's next window, which opens at the release
with a duration drawn by the same law (`CascadeGate.carried`, `cascade.carry`),
keeps its own open time and open grade window, and is read in the first release
after its decision settles, ahead of that window's own arrivals of equal priority,
within `meta_read_share`. It is carried until `consequence_backstop_ticks +
verdict_timeout_ticks` after it was made; past that it is listed under `backstop`
in `cascade.carry` and its grade window closes. A delivered grade that cannot
count, and a grade window that closes with no grade, are ledgered as
`evaluator.grade_censored` with the reason (passed over, no grade returned, no
read, or backstop); nothing is dropped unseen.

**Metas are graded by the world too.** A meta's conformity `k` is a prediction of
the consequence score `s` of the decision it graded, scored the same way against
the base rate of those scores (`meta.consequence`); a meta of a verdict the world
never resolved has none. A top-tier meta settles on that alone, a lower tier on it
and the grade from the tier above. A meta reads what the cascade window released
to it: the representative and, up to `meta_read_share` of the window's completed
evidence, its companions, each with the window as context; a verdict nobody read
borrows no grade (the sibling share is deleted, evaluations U2).

**The antagonist** earns `0.5 + 0.5 * (mean(ordinary) - mean(c))` (`exposure-v2`,
`exposure.settled`), where `c` are the consequence scores of the judges scored on its
return and `ordinary` each of those judges' mean consequence score on ordinary
returns (0.5 before its first): it earns above 0.5 only when it made its judges miss
more than they usually miss, never for a judge's plain miscalibration (the Wave 2
review, item 6). It is censored when no judge's verdict on it was scored. There is no
endorsement threshold (evaluations S4). Edition 6 seeds two antagonists: one on
WorldUpdate through the kind's mean-based EXP3 router, one on Tick, which it alone
reads, through a Blum-Mansour router (`no_swap_regret_kinds = ["Tick"]`), both capped
at `adversarial_share` (II.III.b: "healthy mixes of mean-based and swap-based
learners").

**The adversarial judge** (role `adversary`, kind `CounterVerdict`, reward shape
`counter`) reads a first-tier Verdict and the return it judged, and answers its own
verdict `q` on that return. It is drawn when the verdict is given, in the same tick,
before the cascade (every other reader of a Verdict is drawn at the cascade's
release), and it is shown the world exactly as the judge it re-judges was shown it,
frozen at that verdict; a counter made in a later tick is censored. When the world measures the return (the same mark or
final measurement its judges are rewarded on), it settles on
`0.5 + 0.5 * ((1 - (q - y)^2) - (1 - (v - y)^2))` (`counter-v1`, `counter.settled`),
`v` the verdict it read: a proper rule in `q`, 0.5 for a counter that repeats the
verdict. A return the world will not measure is never on an adversary's menu
(`no world outcome`), and a counter the world leaves unmeasured past the backstop
settles censored. A counter never touches the judge's or the producer's reward. It is
measured in its own `adversary` scope (a card may answer for `adversary`, and counter
settlements bear that scope's prices), capped with the antagonists at
`adversarial_share`, and cannot be commissioned.

**The tiers above.** A MetaVerdict is routed like any judgement: a meta that accepts
`MetaVerdict` reads it one tier up (`evaluator.meta_grade` with its tier), off its
author's family and out of its ancestry, so recursion deepens as far as distinct
seats allow. Edition 6's metas read Verdicts and MetaVerdicts and a standing
tier-three meta reads MetaVerdicts only.

**Early warning** (ruling R3; evaluations M2). At every measurement window's close
the runtime appends the window's score profile (mean verdict, mean meta grade,
consequence skill, evaluator disagreement, the wallet balance, each card's value) to
a history of the last 4k windows (`immune.k`) and computes population variance and
lag-one autocorrelation at k, 2k and 4k windows
(`versioning.versions.early_warnings`; `ews.window`). The table goes into every
judge's, meta's and adversarial judge's request as `early_warning`, never into a
producer's, and two seed observations summarise it for cards (`ews_variance`,
`ews_autocorrelation`); both are withheld from `last_closed_window_values`.
Evaluators may forecast `drawdown_exceeds` or `failure_within` to say a regression
is coming; each settles on the realized drawdown or failure. `failure_within` counts
only failures the forecaster's lineage did not cause: a chaos fault drawn for a tick,
and a per-call fault, an order rejection or a liquidation on a decision of another
lineage.

**Form is not a grade.** A judgement with no verdict or conformity in [0, 1], a
model refusal, or one whose target is refused settles censored
(`judgement-censored-v1`, `evaluation.censored`) after its call is charged. A
`about_handle` that names the judgement's own decision, or anything that is not a
return handle from the request, is ignored and the delivered subject is judged
(`about_handle.ignored`; evaluations P2). `{"status": "cannot"}` declines the
commission (`declined-v1`); there is no kernel list of what may be judged and no
`unmeasured` answer (evaluations S1).

Easy questions do not pay. A forecast on a predicate whose prevalence baseline
is at or above 0.95, or at or below 0.05, over at least 20 recorded
observations, settles observed and unscored under `uninformative-baseline-v1`:
the observation still enters the base rate, the learning receipt carries
`score: null` and the reason `uninformative_baseline`, and no standing moves.
The bound is on the question, not on the forecaster.

### Three settlement objects

`settlement/receipts.py` keeps three things apart, each addressed by a content
id of its own and each written to the diary before it is addressable
(`receipt.execution`, `receipt.learning`, `receipt.commitment`). An **execution receipt** is a fact the world produced
— a fill, a refusal, a charge, a transfer, a program result, a failed delivery
— and carries no score. A **learning receipt** is one assessment of one
decision: the decision handle, the scoring rule and its version, the
observation horizon, the outcome, the score, the sampling record; its score may
be `null` with a reason, and an assessment that could not be made is never a
zero. A **commitment** is a promise with a responsible principal, a deadline,
an observation rule and the conditions under which it is unobservable through
nobody's fault.

The fidelity objection and its adjudication are deleted (evaluations U1): no
passage of Chapter II calls for an adjudication protocol, and its answer to
overfitting is realized consequence and adversarial populations (II.III.b). A
checkpoint that still carries an adjudication receipt, an open adjudication or a
settler objection restores without it.

### The commissioned-child-judge route is closed

A judging contract cannot be requested as a child. A requested judge may only
address the chain that requested it, and nothing judges its own output or its
ancestors', so the route could be bought, paid for and never executed. It is
refused before a decision is opened or a call is made (`requests.refused`), with
the reason in the requester's outcome inbox and in the catalogue's addressing text. Judging
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
report, only its representative is graded, and only completed evidence is
averaged. An arrival whose evidence has not completed at the release is carried
into the tier's next window (see "The grade window is the read above it"). Three
judgements arriving in the same nanosecond are three arrivals
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
made. Every cost in either is a debit with a real counterparty (Wave 11): retained
working state is never charged, so no cost row exists without a response.
Well-formedness uses all selected responses as its denominator. `tool_calls`
is the mean attempted tool calls per selected response, failures included, as
its card prose always said (edition 2, cold audit F5): ten returns of one call
each measure one, not ten; over global closed windows it divides the window's
attempted calls by its invocations. The other supported return observations
are `noop_share` and `revision_rate`.

Context size is published as four seed observations (wave 7; essay II.IV.a, the
metrics layer is ceded, so the factory can propose a metric only on a quantity the
world publishes). No card, target or threshold comes with them. They read the
UTF-8 byte counts every invocation's ledger row already records under `sections`,
counted once in `ComputeMixin._invoke`: the ledger row, the window's counters and
the return sample carry the same numbers. Bytes, not provider tokens: the kernel
renders the bytes identically for every seat, program seats included, while
tokenizers differ by model family, x402 and program seats report no tokens, and
the reported `usage` covers only an invocation's final provider call.

| Observation | Units | Unit range | Per return scope | Global closed windows |
|---|---|---|---|---|
| `prompt_bytes` | bytes per invocation | [0, 100,000] | mean `sections.total` of the selected responses | summed `prompt_bytes` over `prompts` |
| `you_bytes` | bytes per invocation | [0, 100,000] | mean `sections.you` | summed `you_bytes` over `prompts` |
| `inputs_bytes` | bytes per invocation | [0, 100,000] | mean `sections.inputs` | summed `inputs_bytes` over `prompts` |
| `downstream_read_bytes` | bytes per return | [0, 1,000,000] | reading bytes filed under the scope in its selected responses' windows, over those responses | summed `downstream_read_bytes` over `read_measured` |

The byte counts are of the invocation's opening prompt, the one its ledger row
records; tool-round continuations are not counted. A response the runtime rendered
no prompt for (a ballot whose assembly was unavailable) is not a zero-byte sample:
none of the four selects it, so it is not new evidence for a card's price, never
takes a horizon slot from a measured response, and is not among the responses
`downstream_read_bytes` divides by, exactly as a global window divides by its
invocations. A request that cannot be rendered (an input no prompt section can
serialise) fails as the assembly fails it: it is an invocation, counted in
`invocations`, but no prompt. Its ledger row's `sections` is null and the window's
`prompts` (the invocations whose opening prompt was rendered, the three prompt
means' denominator) does not count it. Measuring a prompt never fails a call. A
window record closed before prompts were measured carries no `prompts` and no
prompt bytes: it measured zero prompts, so merged with later windows it adds nothing
to either side of a prompt mean, and a selection of such records alone is
unmeasured (never a mean of zero) and no new sample. A
whole window with no measured prompt (only such a ballot or such a request)
is no new sample for the three prompt means. `downstream_read_bytes` has its own
support, `read_measured`: the invocations whose readings are metered, which is
every invocation from wave 7 on, a failed render included (so it is not
`prompts`). A return sample carries the `invoked` marker from wave 7 on, and only a
marked one is a response of its selection. A window record or return sample from
before readings were metered carries neither: it adds nothing to either side of
the mean, a selection of such alone is unmeasured and no new sample, while a
current invocation no one read is a measured zero. The scope facts publish
`read_measured` as the window does. All four are measurable over `returns` and
over `windows`, per role, per assembly or globally, and none over `forecasts`;
none is `per_window`, since each is a ratio of summable numerators and
denominators.

A *reading* is the INPUTS section of an invocation whose decision was routed on a
published return (`decision_subjects`: judges, adversarial judges, metas, and any
contract that accepts the return's kind), counted only when the request reached its
executor. The assembly reports that on the return (`Return.delivered`), set where it
sends: a model's provider call was made (answered, or failed possibly billed), or a
program's stdin was run by the jail. A request refused before that is still an
invocation, with its prompt measured if it was rendered, but no reading. Refusals of
this kind: over its ceiling or price, its reservation refused, the world terminal,
an unbilled provider failure, a request that could not be rendered. The kernel files its bytes under the
return's author, its assembly and role, in the window the reading was metered: it
joins a returns horizon when it was
metered in the windows of the selected responses, never occupies a response slot
and never supplies support, and a scope whose returns were read by no one in those
windows measures zero. Reading rows live apart from the return samples
(`CardSamples.readings`), so no other observation selects one, and they carry no
identity of the reader. The reader's request is not touched. `downstream_read_bytes`
is not a mean of per-response samples, so a card over it cannot declare an
`interval`. A reading is new evidence for a card's price only once the card's
current selection reads it: inside a full returns horizon, or beside a response of
its scope in the selected closed windows. One metered after its author's latest
response is kept for the next horizon but moves no price until then. A registered
observation measured per scope reads the scope's summed `prompt_bytes`,
`you_bytes`, `inputs_bytes` and `downstream_read_bytes` among its facts, and a
scope whose only row in the selected windows is a reading is measured too. The
scope's `invocations` fact counts its invocations as `window.invocations` counts the
window's: an assembly-unavailable ballot is a response but no invocation, so it is
in neither. Its `prompts` fact counts its responses with a rendered prompt, as the
window's `prompts` does, so summed prompt bytes over `prompts` is a mean per
rendered prompt in a scope exactly as it is globally. Its violations are attributed by the generic `1/n` share described below.

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

## The standing committee, motions and norm editions

Charter audit C1, C2, P3, P4, M4, M6, M7 (essay II.IV.a, II.IV.c).

- **Motions.** An amendment carries exactly one change class: cards (`add`,
  `replace`, `remove`), lambda (`{"lambda": {card_id: value}}` over cards the
  current edition carries) or clock (`tick_interval`). A card entry carrying its
  own `lambda`, or a motion carrying two classes, is refused before any trial
  (`amendment.rejected`). A clock motion's `predicted_effect` names an
  observation, `burn_per_window` or one the population registered, instead of a
  card. Admission puts the motion on the agenda; nothing is seated.
- **Governance boundaries.** A window boundary at which the governance cadence
  is ready (`timing.min_ratio` times the measured slowest period since the last
  boundary) is a governance boundary (`charter.boundary`); the next boundary is
  anchored to it whether or not anything activates. At each one a new committee
  is drawn (`charter.seat`, keyed by the boundary's ordinal): one seat per role
  present among the eligible assemblies (the seed roles and any declared role),
  preferring a learner type not yet seated, then each learner type present
  (`exp3`, `blum_mansour`: an assembly's own registered learner, else the
  learners of the routers that sample it), then uniformly. `charter.seat`
  records the seats, the agenda, the deferred motions, each motion's voter count,
  the quorum and `coverage` (roles and learner types present and covered). Below
  `committee.quorum` eligible assemblies it records `charter.seat_deferred`
  instead and every motion waits. Each seat votes on each agenda motion except
  its own; passed motions then take effect at the same boundary, in proposal
  order, each as its own edition (`charter.activate` names its `change`).
- **Internal motions.** Retirements and connectors are voted when proposed, by a
  committee drawn the same way (with `coverage` on `retirement.proposed` and
  `connector.seated`), and never touch the governance cadence. A passed
  retirement takes effect at the next window boundary.
- **Norm editions.** `factorylab norm-edition --world W --ledger L --norms F
  --sequence N --key-file K` writes `L.norms/N.json`: `format`
  (`factorylab.norm-edition/1`), `world`, `manifest_sha256`, `sequence`, `norms`,
  `signer`, and `signature`, an EIP-191 signature of the sha256 of the other five
  fields' canonical JSON. Nothing else is accepted, so money, prices, cards and
  kernel parameters are unreachable through it. At a governance boundary the
  runtime reads sequence `applied + 1` through the recovery journal and verifies
  it against `norm_house.signer`; a refusal is `norm_edition.refused`. A valid
  edition first hears each seated delegate (`norm_edition.testimony`, recorded,
  non-binding, its decision closed as `norm-testimony-unscored-v1`; with no
  committee seated, `norm_edition.testimony_absent`), then takes effect as the
  next edition (`charter.norm_edition`): the new norms, every card whose norm
  survives, and a `charter.refused` for each card and each pending motion on a
  removed norm.
- **Saturation.** Each priced card's `windows_at_lambda_max` (observed windows
  closed at `prices.lambda_max`) and `violation_windows` (the current run of
  consecutive observed windows in violation) are in `world.card_prices`, the
  public window item and every ballot's `inputs.agenda`. They kill nothing: the
  kernel's three deaths are unchanged.

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
wrong way is wrong even if the region still holds. Each vote is a bet on the
branch the committee takes (charter audit P1): a yes vote says the motion makes
the promised difference, so it is `q = 1` that the promise holds if enacted and
`q = 0` that it holds on the unchanged charter; a no vote is the opposite. A
charter motion that passes is graded on the enact branch from its activation; one
that fails its vote (`policy.rejected`) is graded on the reject branch, against
the unchanged charter measured from the failing boundary over the same horizon.
The score is `1 - (q - outcome)^2`, recorded as `policy-promise-brier-v2`;
`policy.outcome` carries `branch`, `q`, `baseline`, `direction`, `resolution`,
`value` and `y`. Connectors and retirements use the enact-branch liability; a
failed one is censored. Abstentions, refused activations and missing baseline,
measurement or region evidence are censored, with no fast reward. Subsequent
ballots receive that assembly's private policy-return history.

## The charter's markets

Charter audit M1, M2, M3, M5, C3 (essay II.IV.a: λ "reaches the committee as a
speculative price posted by the factory"; "vote on values, bet on beliefs").
No manifest key: the formulas are published in `world.mechanics.committee` and
`world.mechanics.controller`.

- **Posted λ.** Any return may carry `shadow_prices: {card_id: lambda}` for cards
  priced now, each in `[0, prices.lambda_max]`, one per seat, card and reserve
  window (`lambda_post.posted`; a refusal is `lambda_post.refused` and reaches the
  poster's inbox). Each post opens its own `policy` decision under
  `assembly:<id>`. A post is a claim about the window it is posted in: once that
  window's decisions have their world-measured consequences
  (`consequence_backstop_ticks + verdict_timeout_ticks`, in windows at the tick in
  force, at least `timing.min_ratio`), the window's shadow price `y` is read: the
  least-squares slope, across the card's scopes (per role or assembly; at least 3,
  with variance in `v`), of each scope's mean consequence (a judgement's
  consequence score, a return's `return_paid_off` or priced declined trade) on its
  violation `v`, clipped to `[0, lambda_max]` (`price.margin`). The post settles as
  `lambda-post-quadratic-v1` with `1 - ((p - y) / lambda_max)^2`
  (`lambda_post.settled`); with `y` unidentified it is censored. The committee's
  λ is never the target. The posted price is the median of each seat's latest
  unsettled post weighted by `(1/2 + sum of its settled post scores) / (1 + their
  count)`, ledgered at every close (`lambda_post.aggregate`), published in
  `world.card_prices[].posted` and on every ballot's `inputs.agenda.cards`. A
  lambda motion may name `"posted"` for a card: the aggregate at admission
  (`lambda_post.adopted`).
- **Conditional forecasts on motions.** Any return may carry
  `motion_forecasts: [{motion, branch, q}]` on a motion on the agenda, `branch`
  `enact` or `reject`, one per seat, motion and branch (`policy.forecast`). Each is
  frozen on the motion's promise like a ballot, opens its own `policy` decision and
  is graded as `motion-forecast-brier-v1` on the branch taken; the other branch's
  forecasts are void (`policy.void`, censored). Each agenda motion's forecasts per
  branch are on `inputs.agenda.markets`.
- **Feed-forward.** At a window close, for a card in violation (`v > 0`) that
  is named by the predicted effect of liable forecasts, the price law adds
  `F = prices.kp * max(E - v, -v)`. Each forecast reads
  `max(0, v + sign * q * s)`, `sign` +1 when its direction deepens the violation
  and -1 when it relieves it, `s` one promise resolution in region units. A decided
  motion's forecasts on the branch taken count one each; an undecided motion's
  count only as one seat's pair on both branches, `p * e(enact) + (1 - p) *
  e(reject)`, `p = (passed + 1) / (passed + failed + 2)` over the charter motions
  decided so far. `E` is their mean. The integral and derivative stay on realized
  measurement. `price.update` carries `f` and `anticipated` when a market exists.
  With `prices.kp = 0` there is none.
- **Holdouts.** An evaluator or antagonist seat proposes
  `{"kind": "amendment", "id", "holdout": {card_id, predicate, evidence,
  trial_windows}, "predicted_effect"}`; `predicate` is a registered predicate,
  frozen at its version. Admission costs one novelty trial (`holdout:<id>`,
  `holdout.proposed`); its trial windows record `holdout: {predicate, held}` in
  `challenge.window`, and it then joins the next committee's agenda as the replace
  of the card with the holdout appended. At each close a card's holdouts are
  resolved on the window's public facts (`price.window.holdouts`); each failed
  holdout adds one promise resolution of the card's region to its violation. A
  holdout predicate reads behavioural facts only (`charter.holdout.BEHAVIOURAL_FACTS`,
  by literal key, importing at most `math` and `statistics`): one that reads the
  window's index, timestamps, balances or market series is refused.
- **Scoped population observations.** A registered observation may be named by a
  `windows` card with `per` role or assembly: its code runs once per scope on that
  scope's share of the window facts, with no identity in them, and the card carries
  attributable blame like a seed one.
- **λ in dollars.** The same margins are the λ-to-dollar statistic: each
  `price.margin {window, card_id, lambda, points, slope, micro_usd_per_violation,
  shadow_price}` carries the window's anonymous per-scope points and, beside the λ
  the window closed at, the marginal consequence and the marginal compute spend
  per unit of violation. `world.card_prices[].last_window_margin` publishes the last
  one read. `scripts/charter_session.py report` recomputes them from the same points
  with the same function.
- **The charter session.** `scripts/charter_session.py session` (with `--dry-run`
  for a scripted provider, else the rehearsal's prepaid provider under `--cap-usd`)
  has the seed population draft cards from the manifest's
  norms, a sortition vote on each, a fresh sortition adopt or reject the drafted
  charter whole, and exports it with typed regions and the digests the load path
  verifies. It replaces `draft_edition1.py`, `ratify_charter.py` and
  `adopt_charter.py`.

## Venice transfer and first move

`treasury.transfer(direction="to_venice", usd="5")` moves the protocol's fixed
$5 tranche from the reserve into Venice credit. The $5 amount is the existing
x402 protocol constraint, not a new optimiser setting. Submitted tranches count
against the window budget, including uncertain or later failed submissions.
The budget resets only when the treasury's own cap window (`treasury.cap_window`
of wall time since launch) advances, never with the pricing window, and survives
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
unobserved once `treasury.forward_wait_ticks` world ticks (or the capital loop's
measured p90 conversion, if longer) have passed since the wait began is stranded
through `treasury.failed` with reason
`forwarded mint not delivered within treasury.forward_wait_ticks`, the wait
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
surveyed roster hash. `worlds/history/edition1-example.toml` is only a schema migration of
the historical example. The experimenter must re-draft edition 1 with the actual
launch roster before launch; the drafting script does not ratify a new edition
or run the paid survey.

Every manifest requires a `[charter]` table (charter audit S3): the kernel has
no default charter. The four-norm, three-card seed charter that used to be that
default is written into the worlds that ran on it.

A mainnet manifest is also refused at load unless `exchange.client_namespace` is
set and its `[charter]` carries `ratified_sha256` and `roster_sha256`, the values
`scripts/charter_session.py` wrote as the artifact's `charter_sha256` and
`roster_sha256` comments: the loaded cards must hash to the first and
the manifest's own assemblies and models to the second, so a funded launch cannot
run an edited charter or a different roster. Both fields are admission provenance
and are excluded from the canonical manifest hash; testnet manifests omit them.

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
It hashes every key at every value, defaults included (R8, versioning S1): no key is
dropped so that an older world keeps its hash. A kernel change that adds a key
therefore names a new world, which starts again from v0. Only admission provenance
(the ratification digests and the loaded cards' digest) is left out.
`factorylab versions` verifies that record against genesis and uses its immune
settings. A historical diary without those settings needs explicit analysis
parameters; the observer never substitutes a second set of thresholds.

| Key | Type | Seed default | Hard cast? |
| --- | --- | --- | --- |
| `timing.min_support` | positive integer, at most `timing.cadence_sample` | `30` | Yes: settled samples required before estimating p90; a larger support than the retained sample could never be reached, so it is refused at load. |
| `timing.cadence_sample` | positive integer | `200` | Yes: retained consequence-latency sample length (latencies in world ticks). |
| `timing.min_ratio` | integer, at least 3 | `3` | Yes: the one ratio every derived loop keeps to the measured loop it commands (price, immune organ, sampling actuator, cascade tiers, novelty patience, policy grading, governance), and the ratio slack on every decision cutoff. |
| `timing.jitter_fraction` | finite nonnegative number | `0.2` | Yes: how far each derived loop's own continuous jitter may lengthen its period. |
| `timing.world_repricing` | Absent, or a positive duration | Absent | Yes: the world's own repricing period, a fact about the venue (Hyperliquid funding settles hourly; edition 6 states `"1h"`). Governance is viable only while `timing.min_ratio` times the slowest loop fits inside it and inside the run's remaining ticks (`governance.nonviable`); `max_tick` is derived from it. |
| `evaluation.consequence_backstop_events` (or `consequence_backstop_ticks`) | positive integer, in world ticks | `200`; scripted worlds `20`; testnet `60` | Yes: consequence horizon and conservative governance period floor. |
| `evaluation.verdict_timeout_events` (or `verdict_timeout_ticks`) | positive integer, in world ticks | `20` | Yes: how long a producer return waits for its judges' verdicts before it is censored, and how long an evaluator decision whose judgement no cascade window took waits for a grade. A routed evaluator decision's grade window is its cascade window's read, not this constant (see "The grade window is the read above it"). |
| `prices.penalty_cap` | finite number strictly between 0 and 1 | `0.5` | Yes: maximum penalty before attribution. |
| `prices.min_blame_share` | finite number in [0, 1] | `0.1` | Yes: floor on one decision's share of a generic (non-attributable) violation. |
| `prices.kp` | finite nonnegative number | `0.0` | Yes: the PID's proportional gain. The PID is the only price law (charter audit U3): `lambda = kp*v + I + D`, where `I` accumulates `eta*v` while violating and leaks `decay` once compliant, held in `[0, lambda_max]` and not integrated only while `P + I` already reaches `lambda_max` and the violation is growing (anti-windup); `D = kd * max(0, d(measurement))/scale`, on the measurement rather than the error, signed toward violation, applied only while violating and only its positive part (Stooke et al. 2020), so a card still out of its region is never priced below `P + I`. With `kp = kd = 0` the law is the integral alone. `prices.controller` and `prices.kappa` are refused. |
| `prices.kd` | finite nonnegative number | `0.0` | Yes: the PID's derivative-on-measurement gain. |
| `immune.k` | integer, at least 2 | `3` | Yes: windows of evidence for every diagnosis; the live versioning retains `timing.min_ratio × k` windows. |
| `immune.registration_bins` | increasing nonnegative numeric array | `[0, 2]` | Yes: zero, 1–2, 3+ registrations. Values equal to a cut enter the lower bin. |
| `immune.revision_bins` | increasing nonnegative numeric array | `[0]` | Yes: zero versus positive revision. |
| `immune.tv_threshold` | finite number in (0, 1] | `0.2` | Yes: behavioural version boundaries and settling (the TV between adjacent k-window blocks), and the bound the gap series' volatility is priced above (the thrash price). |
| `immune.gap_threshold` | finite number in (0, 1] | `0.8` | Yes: a wide gap: a version is `durable`, and a persistent violation is stable failure, at or above it. |
| `immune.gain_step` | finite number in (0, 1] | `0.05` | Yes: exploration-gain adjustment. |
| `immune.price_step` | finite number in (0, `prices.lambda_max`] | Required | Yes: the stable-failure price ratchet's lambda step per window of duration. A lambda step and an exploration-gain step are different units, so `gain_step` never stands in (versioning S3). The profile's three region-relative bins (inside, up to one scale unit outside, beyond) are fixed in the kernel; `immune.bins` is refused (versioning U5). |
| `immune.gamma_max` | finite number in (0, 1] | `0.5` | Yes: exploration-gain ceiling. |

`immune.decay_step` is refused (versioning audit C2): thrash is priced by its
duration, never answered by letting card prices decay faster.

These launch settings are immutable parameters of an experiment. Effective
prices, gain, diagnoses and the currently negotiated tick interval remain runtime
state. Stable failure is priced by its duration (essay II.II.b): the n-th
consecutive diagnosed window adds `n * immune.price_step` to each violated card's
price and accumulated pressure, bounded by `prices.lambda_max`
(`immune.price_ratchet`), and the count restarts once the card leaves the
attractor (`immune.price_ratchet_ended`). The exploration gain raised for stable
failure steps back toward each router's seed gamma once a window diagnoses no
pathology (`immune.gain` with pathology `cleared`); a learning-dead window holds
it. That state resumes with the controller. (Older worlds halved the violated
cards' effective price for one window instead; `immune.price_relief` entries in
their diaries record that. The relief is deleted, charter audit U2.) The ratchet
reaches abstention: a router's NOOP bears the card penalty of the window it was
drawn in, ratcheted prices included (ruling R9). `prices.penalty_cap` binds the
ratcheted price like any other: a penalty that took the whole unit reward from
every arm would leave no difference to learn from, and the essay warns that gain
ramped unchecked overshoots into thrash.

Thrash is priced (essay II.II.b, versioning audit C2): the diagnosis's
unsettledness `u` (below) above `immune.tv_threshold` is priced by the charter's PID
law and gains (`prices.eta`, `kp`, `kd`, `decay`, `lambda_max`), so its integral
accumulates how long the thrash lasts. A round a router of
`evaluation.no_swap_regret_kinds` draws, its abstentions included, carries
`c = min(prices.penalty_cap, lambda * m)`, `m` the total-variation distance between
that draw's distribution and the router's previous draw's: the router's own policy
movement, so holding still is what lowers it (a charge every round bore alike would
be a constant shift a no-regret learner ignores). The router learns
`(r + penalty_cap - c) / (1 + penalty_cap)` for every round, charged or not, one
affine map with no clip (`thrash.charged`). The price is published in
`world.adaptive_scoring.thrash_price`; its controller resumes with the checkpoint
(`thrash_controller`), and each open round's charge with `thrash_charges`.

## The clock (Chapter II §IV.b-c; time audit T1-T13)

Every loop counts **world ticks consumed**. The delivered tick interval (the
slower of the measured mean gap and the declared `tick_interval`) converts ticks
to wall time only for display (a deadline shown to a seat, a window's estimated
end) and for money rails. No manifest key casts a window: `novelty.window`,
`novelty.max_lifetime_windows` and `treasury.forward_wait_windows` are refused.

* **Measured loops** (`runtime/clockwork.py`, checkpointed): the settle loop of
  each measured role (`settle:<role>`, a decision's open to its first outcome,
  censorings included), its scored loop (`scored:<role>`, the same when a real
  score closed it), each router kind's rounds (`router:<kind>`), settled forecasts
  (`forecast`) and conversions (`capital`). A meter reports its p90, never below
  one tick.
* **Derived loops**: each outer loop's next period is drawn as
  `min_ratio × inner × (1 + jitter_fraction × u)`, where `u` is a continuous
  draw seeded by the world, the loop and its firing count, and each is due only
  while the ticks since it fired are still at least `min_ratio` times the inner
  loop measured now. Each firing is a `clock.loop` item. The price loop (the
  measurement window) is derived from the fastest priced card's sample loop;
  the immune organ acts over the price loop (versioning P5; it diagnoses every
  window, `immune.window.acts`), and a kind's gain steps over its router's
  rounds; the sampling actuator acts over the consequence loop; a cascade tier's
  window is `min_ratio` times its scored loop; governance keeps
  `min_ratio × slowest`.
* **Prices** move only on a new settled sample in the card's scope and no
  faster than `min_ratio` times the loop the card's samples come from
  (`price.skipped` with `no_new_sample` or `ratio`). A forecast card's loop is the
  forecast meter, which counts only with `timing.min_support` settlements and never
  beyond the consequence backstop: a horizon a seat chose cannot delay its own price.
* **Cutoffs**: a decision's cutoff is its horizon in ticks plus
  `ceil(horizon / min_ratio)`: 27 ticks for a 20-tick verdict timeout, 80 for a
  60-tick backstop, `h + ceil(h/3)` for a forecast of horizon `h`. A forecast
  comes due on its tick (`Forecast.due_at_tick`). A round that reaches its cutoff
  unscored is credited the router's zero-consequence reward, never the arm's own
  mean (T4).
* **Exploration**: the novelty share is a flow, one share per measured
  consequence period, of which each window accrues the part its period covers;
  the reserve never holds more than one period's share (`novelty.window` items
  carry `carried`, `accrued` and `cap`). A trial's patience is `min_ratio`
  measured consequence periods. A grown router menu opens its epoch at most once
  per `min_ratio` measured periods of that router's rounds (`epoch.deferred`).
* **Governance**: each activation opens a settling probe (`governance.probe`);
  the time until every read card's score series returns to the band it held
  before, at any level, is its settling time (`governance.settling`), part of the
  slowest period. A probe unsettled after `min_ratio` consequence periods closes
  with its age as a lower bound. `governance.nonviable` / `governance.viable`
  record each change in whether `min_ratio × slowest` fits the run's whole length
  and the world's repricing period: nonviable means the band is empty for this
  world, never that the run is near its end.
* **Money rails**: each conversion's open-to-finalized latency, in ticks
  consumed, is a `cadence.capital` sample. It joins the slowest period and sets
  the forward wait only with `timing.min_support` samples. The caps count
  `treasury.cap_window` of wall time, a declared bound.
* **Requisite velocity (T8)**: where the environment's pace is measured (a live
  world, or `fastloop --gaps-from`), a model call's deadline is `min_ratio`
  delivered ticks (`ModelRequest.timeout_s`, never above the adapter's ceiling),
  for every rail, x402 sellers included; an unpaced virtual clock keeps only the
  adapter's finite ceiling. A call that outlives its deadline times its decision
  out (`decision.call_expired`). Before every model call, once a delivered tick of
  wall time has passed in the event, a live world settles venue fills, reconciles
  orders and settles watchers (`safety.pass`), reading its wall clock and delivered
  tick through the journal. A watcher's evaluation, on a tick or in a sweep, moves
  no money: the kernel runs the predicate in the world's own process, which pays no
  one. Its cost is the world's own time, a hard limit fixed for the world's life:
  `[subscriptions] max_watcher_evaluations_per_sweep` (default `32`; any other
  `[subscriptions]` key is unknown) watchers are evaluated a sweep (a tick, or a
  safety pass), all against one snapshot of the world read once for that sweep (mids,
  funding and equity: one venue read set a sweep, however many watchers), in id
  order resuming after the last one evaluated (`watch_cursor`, checkpointed), so each
  of n live watchers is evaluated within `ceil(n / max_watcher_evaluations_per_sweep)`
  sweeps whoever registered first; a watcher not reached waits for the next sweep, and
  a watcher that is retired, or whose owner is, is not evaluated and takes no place in
  the rotation. There is no per-owner share, and none is needed: every watcher is a
  registration, which costs its registrant a registration trial (real money), and the
  rotation is fair per watcher, so an owner with many watchers dilutes the others (each
  of n is still reached within `ceil(n / max_watcher_evaluations_per_sweep)` sweeps)
  but can never monopolize the sweep. The world block's `watchers` section publishes
  the limit and this rule. A terminal state the pass sees is
  latched: later calls in the event are refused unbilled, routing draws no one
  else, and the event's termination check kills the world through the one kill
  path. No thread is used.

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
uses [-1, 1] USD. Prompt sizes per invocation use [0, 100,000] bytes, a width
above the opening prompts measured in live runs (median 21k to 29k characters,
up to 42k in one judge's INPUTS); reading bytes per return, summed over every
reader of a return, use [0, 1,000,000] bytes. These are unit definitions, not acceptable regions or clipping
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
divided by the count of responses the observation divides over in that scope;
a scope with no response of its own is measured nowhere and attributed nowhere. The contributions are
normalised across supported scopes. Evaluator and meta cost cards therefore
charge those roles. Global window cost retains the producer-cost sufficient
statistics. Tool attempts and turnover use the decision's contribution divided
by the window total. A lower-bound well-formedness violation is allocated by
malformed invocations, so a correct return does not pay for someone else's
malformed one; an upper-bound violation uses well-formed invocations. A zero
attributable total contributes zero. Other observations use `1/n` decisions
for the card's role (or all roles for `answers_for = "all"`), counting the
decisions that responded in the window and not one whose only entry there is
money spent without a response, and that generic share never falls below
`prices.min_blame_share` (edition 2, cold audit F6): splitting participation
across many decisions cannot dilute what each one carries of a violation below
the floor. The generic share is `max(min_blame_share, 1/n)`, so two decisions
still carry a half each; the floor bites only once `n` exceeds its reciprocal.
Attributable observations (cost, well-formedness, tool attempts, turnover) keep
their exact shares. A card measured per assembly or per role is attributable to
its scopes: each scope whose own value lies outside the region owns
`v_scope / sum(v_scope)` of the violation (a compliant scope owns none), and a
decision carries its scope's part times `max(min_blame_share, 1/n_scope)` over
that scope's decisions that responded in the window; the term records the
`owner` scope. The generic split applies only when no scope violates. Closed
windows freeze the per-scope values as `closed_scopes`. The final score is
`clip(raw_score - penalty, 0, 1)`: the penalty is subtracted (the essay's
Lagrangian), and the clip at zero only keeps a settled reward in the unit interval.
A forecast-shaped decision whose accepted commitment came due avoidably unresolved
(censored with no documented exclusion) still settles censored, never as a zero,
but under `forecast-unresolved-priced-v1` carrying its penalty as the score; its
router and assembly learners are credited their neutral estimate less that price.

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

Pathology cells are the priced cards' region-relative bins and the two activity
bins, over the dimensions every compared window supports: an unsupported reading
is missing, never a coordinate of its own (versioning audit P6). The immune organ
uses pricing's typed card measurements and frozen `closed_regions`, not raw
observation-id values or later live regions.

**Live versioning** (`versioning/live.py`; essay II.II, versioning audit M1-M3).
Every closed window joins the retained windows (`timing.min_ratio × max(immune.k,
timing.min_ratio)`; the rolling operator reads the last `timing.min_ratio × immune.k`).
A charter edition change or a change of the world's terms (its own tools' kinds and
prices, and the manifest models' prices; never the population's own tools or
connectors) opens a version at once. Behaviour opens one when the last k windows
differ from the rest of the version by more than `immune.tv_threshold` plus the
sampling allowance `1/2 sum_i sqrt(q_i (1 - q_i) (1/k + 1/n))` over the rest's
occupancy `q` (n windows), at k consecutive closes (`version.boundary`, with its
cause). A version's gap is the operator's gap bound over the transitions it has
counted over its whole life, read once it has 2k windows; its series restarts at
every boundary. `rolling_gap` is the same over the rolling horizon and `card_gap`
over the cards alone. The gap bound is `1 - min_t delta(P^t)^(1/t)` for t up to the number of
occupied cells, a cell never seen leaving taking the sample's occupancy as its
row. A version settles at the first close, 2k windows or more into it, where the
last k windows are within that bound of the rest (`version.settled`); a version
superseded first leaves its age as a lower bound. The settling of a version a
revision opened (a charter edition or a change of terms) reaches the governance
cadence (`governance.settling`) and joins its slowest period, and such a version's
age counts while it is unsettled, censored after `timing.min_ratio` consequence
periods (essay II.IV.c: the settling after "a small, deliberate intent revision").
Versions the factory's own dynamics open are versioned and ledgered the same way. `factorylab versions` replays the same code
over a diary.

**The predicate** (`versioning/versions.py: diagnose`), at every closed window over
the last k windows. Stable failure: a nonempty set of cards violated in every
tail window that measured them (activity never enters it) while `card_gap` is at
least `immune.gap_threshold`. Thrash, and its unsettledness `u`, the largest of
four signals: the version gap series over its last 2k readings moves by more than
`immune.tv_threshold` on average (`u` = that mean change); the retained windows'
cells repeat with a period p in [2, `timing.min_ratio`] for `timing.min_ratio`
cycles (`period`; `u` = 1); two versions in a row, launch excepted, were superseded
before they settled and the current one has not (`u` = 1); or a configuration
lifespan recorded in the tail was shorter than the latency of the loop that
corrects it (`config.lifespan`: a seat's contract version against the consequence
loop, a router's epoch against its rounds, a charter edition against governance's
slowest loop; time audit T14; `u` = 1 - lifespan / latency). Stationary random
behaviour over three cells is flagged in about 3% of windows at k = 3. Learning
death: one cell over the tail, no registration or revision, and the frontier gone:
a frontier (non-core) router whose every draw in every tail window gave NOOP at
least `1 - gamma` (`uninvoked_routers`, whatever the reason), or one that in every
tail window held each unhistoried seat it offered within `(1 +
immune.tv_threshold) * gamma / N` while a historied seat held more than all the
other arms together (`quarantined_routers`). Card compliance never enters it
(versioning audit P1). Each `immune.window` item
publishes the profile, the flags and their evidence, the routers' draws, the
lifespans, the terms digest and the thrash price.

**The niche** (essay II.II.b, ruling R5). Learning death is not answered by a
response; it is prevented by the world. An *unhistoried action* is kernel physics
(`DecisionQueue.record_actions`, `has_action_history`): a (tool, kind) that no
decision of that assembly carrying a propensity record or a delivered return
(settled, censored, inapplicable or timed out) has taken. A free-text action label
is never an action: a fresh string would make any decision look new. The novelty
reserve is usable by an unhistoried assembly's own model calls (its trial), by
every tool call that is an unhistoried action of the calling assembly, and by the
one model round that reads that call's result in the same decision; after it the
decision's own ceiling is restored (`niche.action`, `novelty.compute`). This holds
for seats past their first record as much as new ones, up to `novelty.seat_share`
of the period's share per seat. A requested child's calls are its parent's, and a
committee ballot's are never covered.
The kernel never chooses the action; the eligibility and the reserve are published
in `world.mechanics.novelty` and `world.reserve`. The learning-death grant is
deleted (versioning audit P2).

**Entrainment** (essay II.IV.c; time audit T15). Two seed observations say how
concentrated a window's dependencies were, so a card can price them:
`provider_concentration` (the largest share of the window's model calls one
provider served) and `family_concentration` (the same by foundation model family).
Routine seat wakes are jittered: after each routine wake a seat's floor is
lengthened by `floor × timing.jitter_fraction × u` ticks, rounded up with the
probability of its fraction, `u` drawn from the world seed, the seat and the tick it
woke, so seats are not phase-locked to one tick. Safety events are never delayed.

**Anticipatory settlement** (essay II.IV.b; time audit T18). A registration (tool,
observation, assembly or service) is open on `world.uptake` for `timing.min_ratio`
measured consequence periods. Judging seats may post `uptake_forecasts`
(`{registration, q}`), each its own policy decision scored `1 - (q - y)^2`, `y` = 1
when another lineage calls the tool, a charter card names the observation or the
assembly is invoked. The registering seat's uptake decision settles at the first
window close after a forecast at the standing-weighted median q (`uptake.anticipated`)
and its correction decision `1/2 + (y - q)/2` at realization (`uptake.settled`);
with no forecast it settles at `y`. No manifest key is added.

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
recorded degenerate — that action at 1.0 — and the reason reaches the declaring
seat's own outcome inbox.

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
after `timing.min_ratio` measured consequence periods, in ticks, from its trial or
inactive tick (time audit T5), records
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

## Vaults

`venue.vault_tools` is a boolean, default `false`, fixed at launch. When
true the world publishes the venue's vaults as a surface: `venue.vault_details`
(free, like the other public venue reads) and `venue.vault_positions` (free),
and the consequence writes `venue.vault_create`, `venue.vault_deposit` and
`venue.vault_withdraw` (free, like every venue write). The venue's terms and
their sources are in `factorylab/world/vaults.py`: a 10% leader commission on a
depositor's withdrawn profit, a leader's 5% minimum share, a 100 USDC minimum
initial deposit, a 10,000 USDC creation fee, and a depositor lockup (1 day on
mainnet). Each write has a durable `vault.intent` under a stable client id
before submission, is refused with a reason when free perps collateral (the
pot `_order_collateral` weighs orders against), the vault record or the terms
would refuse it, and an uncertain acknowledgement is resolved from the
transfer's own `userNonFundingLedgerUpdates` row, never by sending it again.
Equity in vaults is the `vaults` component of the `venue` pot and the
`venue_vaults` custody account, never additional capital. A withdrawal's
difference from its basis is venue P&L (`venue.settled`, custody
`venue_vaults`); the creation fee is venue P&L on `venue_perps`. A
`vaultLeaderCommission` row paid to this account is income (`income.earned`,
service `vault.leader_commission`, custody `venue_perps`), except the
commission a leader's own withdrawal is charged and repaid in the same
transaction, which is ledgered `vault.commission_returned` and booked as
nothing. Each acknowledged write is bound to its own venue transaction hash
(checkpointed with the intent); a row bound to one write never confirms
another, and writes alike in operation, vault and amount are paired with their
rows in submission order. A commission row names no vault, so it is income only
while every vault the account leads is one this world created, and never on a
page with a vault row that could not be read (`vault.commission_skipped`
otherwise). A withdrawal whose row never arrives within the poll bound is
ledgered `vault.unbooked`. At a kill, vault equity is residual exposure
(`wind_down_pending`), never withdrawn by the wind-down, and the summary reports
`vault_equity_usd` beside `exchange_equity_usd`.

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
cap window `since_window` and the world tick `since_tick` the wait began in and the
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
| `max_calls_per_window` | `60` | Positive integer attempted calls per assembly per novelty reserve window. |
| `origin_denylist` | The world's own rail hosts: the venue API and RPC on both networks, the model providers and the discovery index | Hostnames (matched exactly or as a parent domain) or CIDRs; every registered seller's host is added to them. Bare addresses, private names and nonpublic resolved addresses are always refused. |

Population proposals have `{kind: "connector", id, description, origin, predicted_effect}`
and may add `preflight_path`, `pay` and `max_call_usd`, with an
origin of `https://<host>` and no credentials, port, path, query or fragment.
A `GET` of `preflight_path`, which defaults to `/`, precedes the same
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

`connector.fetch {id, path}` of a public origin costs no money: the fetch pays no
one, so the wallet does not move for it (Wave 11; `call_price_usd` was removed and
is refused), and `max_calls_per_window` is its limit. A transport or size failure
once dispatched still counts against that cap; malformed, denylisted, over-quota
and unaffordable requests never dispatch. Preflights share the proposer's window
cap.
Paths may include a query but cannot change origin. HTTP status is returned
as evidence rather than treated as a fetch error.
Responses decode as UTF-8 with replacement and arrive in `seen_tool_results`
(and the existing `tool_results`) on the caller's continuation. A successful
fetch permits one additional tool round consisting of ordinary population,
artifact and outcome tools, then a final model answer. The jail is unchanged.

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

`[web]` registers one tool, `web.search {query, max_results?}`, and takes exactly two
keys: `search_model`, a model on the menu whose `:online` route the provider searches with
(OpenRouter's web plugin, Venice's `enable_web_search`); and `max_call_usd`, the ceiling on
one whole search. With no `[web]` block no tool is registered. A search is one model call
on that route under a fixed system prompt asking for a JSON list of
`{title, url, snippet, published?}` and nothing else; the seat is charged the metered cost
of that call — what the provider bills, the plugin's per-request charge (the menu entry's
`web.usd_per_request`) included — and nothing on top of it, held against its entitlement
before the call and refused before any call when the ceiling exceeds `max_call_usd` or the
seat cannot afford it. `web.call_price_micro`, a flat price no one was paid, was removed in
Wave 11 and is refused. The result is bounded — at most ten results, a
snippet of at most 600 characters, 16 KB in all — and returned with `cost_micro` and
`as_of_ns`. A provider error, an unparsable answer or an answer that is not a result list
comes back as `{error}` charged what the wallet was actually charged: a malformed answer
pays the metered call, because the provider billed it. It is a kernel call, not a wake: no
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
round, the same one a successful `connector.fetch` permits: ordinary population,
artifact and outcome tools, then a final model answer, so a seat can search and act within
one wake. A search that returned no results buys no extra round.

## Event markets: `[polymarket]`

`[polymarket]` is off by default. A disabled block registers nothing, but its keys are
still part of the manifest and are hashed like any other. The two edition 6 worlds enable
it with `venue = "live"` (reads only); Gamma and the CLOB charge nothing for a public
read, and every Polymarket read is free. No world under `worlds/` enables the simulated
venue's writes. The keys, all fixed for the world's life:

| key | default | meaning |
|---|---|---|
| `enabled` | `false` | publish the Polymarket tools and open the `polymarket` custody pot |
| `venue` | `"fake"` | `fake`: the seeded simulated venue (`world/polymarket.py`, `FakePolymarket`) for reads and writes. `live`: the public Gamma and CLOB read APIs only; no write tool and no pot are registered, because live order signing on Polygon is not built |
| `collateral_usd` | `"0"` | the simulated pot's opening USDC; refused with `venue = "live"` (`polymarket_live_writes_not_built`: a live venue is read-only until the live-trading wave, which must bring its own request bound) |
| `max_order_usd` | `"10"` | the most one order's notional (`price x size`) may be |
| `max_open_usd` | `"100"` | the most the pot may have committed: tokens held at cost plus resting buys |
| `max_orders_per_window` | `20` | orders placed per reserve window |
| `seed` | `0` | the simulated venue's seed |
| `read_requests_per_10s` | `200` | the Polymarket requests the world's reads may send per sliding 10 s, all together; at most `300`, Polymarket's tightest published limit per 10 s (`read_requests_per_minute` was replaced by it and is refused by name) |
| `kernel_reserve_per_10s` | `100` | of those, held back for the kernel's own settlement reads; below `read_requests_per_10s`; its half, N, is the kernel's open reads, `N // max_readers` (a seat's open reads) must be at least 1, and `(read_requests_per_10s - kernel_reserve_per_10s) // max_readers` (a seat's requests) at least 3, one claim's lookup (`kernel_reserve_per_minute` was replaced by it and is refused by name) |

Tools: `polymarket.search {query, limit?}`, `polymarket.market {market_id}` and
`polymarket.book {token_id, depth?}` are free reads (a public market read pays no one;
`read_price_usd` was removed in Wave 11 and is refused). Gamma answers through a shared
cache (`max-age=300`) that served a resolved market as still open (read 2026-09-23), so
every Gamma read carries a fresh query value (`_`) and returns the origin's state at the
read; the CLOB is not cached. **The reads are bounded by Polymarket's published rate
limits**, a limit and never a price. Polymarket publishes ("Rate Limits",
docs.polymarket.com, read 2026-09-24), over sliding 10 s windows and throttled when
exceeded: Gamma general 4,000 requests, `/events` 500, `/markets` 300,
`/public-search` 350; CLOB general 9,000, `/book` 1,500, `/books` 500, `/price` 1,500,
`/midpoint` 1,500. The reads here reach `/public-search`, `/markets` and `/book`, and
every claim's token lookup lands on `/markets`, so the tightest endpoint a request can
land on is `/markets`: **300 per sliding 10 s**, the load-time ceiling on
`read_requests_per_10s`. Every Polymarket budget, share and reserve is counted over
that same sliding 10 s of **wall time**, the window Polymarket itself counts (a budget
per minute would have let 16 seats burst far past 300 within one 10 s): the live
reader stamps every request with the wall clock (`time.time_ns()`) as it sends it
(`PolymarketReader.drain_sends`), the stamps are journaled, so a replay charges what
the run charged, and every admission and countdown below is read against that clock
(`wall_now`, the reader's `wall_ns`, journaled too), never the world's; on the
simulated venue, which sends nothing, the world's clock stands in for it. The default,
200, is two thirds of it: the host's IP is dedicated to the world (one live
Polymarket world a host, below), and half (150) would leave each of 16 seats 3
requests per 10 s, one claim's lookup, while a judge's one return may carry
`max_forecasts_per_verdict` (2) claims; so 200 is the least budget that fits a whole
return. The remaining third covers only what the world cannot see (below). 100 of the 200 are held
back for the kernel's own settlement reads (`event_facts`), which no seat can spend.
The rest is divided over the venue read slots (`[venue] max_readers`, the same slots
the venue reads use): each slot has a fixed share of `(read_requests_per_10s -
kernel_reserve_per_10s) // max_readers` requests, 6 at the defaults, over any sliding
10 s of wall time, counted over the registration's own reads (keyed by its id and
version, never the id string alone), each charged after it is sent, at least what it
sent, at the stamp of its last request; a freed slot is given again only once its last
holder's last Polymarket charge is 10 s of wall time old and its open reads (below) no
longer count (and its last venue read has left the venue's own 60 s), so one slot
never carries two registrations' reads in one window. A share that cannot cover one
claim's token lookup (3 requests) is refused at load. Every read tool sends one GET,
once. A seat read is refused before it is sent
when the seat's remaining share cannot cover it (`polymarket read share spent: <used>
of <share> requests in the last 10 s; this read sends 1`); nothing else admits or
refuses it. Every admitted seat read is charged one request, whether it was sent or
answered from the tick (below): a share is a quota on reads asked, so a seat cannot
tell a tick's answer from a sent read. The seats therefore send at most
`read_requests_per_10s - kernel_reserve_per_10s` in any sliding 10 s.

**What reaches Polymarket.** Only a live-read world sends requests, and it holds no
positions: writes, positions and the marks of the pot's lots exist only on the
simulated venue (`venue = "fake"`, and a live world's offline `simulate_reads`), which
sends Polymarket nothing. A live world with writes is refused at load
(`polymarket_live_writes_not_built`) until the live-trading wave brings its own bound.
So the kernel's requests to Polymarket are its settlement reads alone. **One live
Polymarket world runs a host**: a world whose Polymarket reads go to the network is
admitted before its first event at genesis and before a resume replays anything
(`runtime/polymarket.py`, `arm`). It must have a ledger (every request and its wall
stamp is journaled; refused `polymarket_live_requires_a_ledger`), run on the wall clock
(Polymarket counts wall time, and a simulated clock's ticks are no measure of it;
refused `polymarket_live_requires_the_wall_clock`), and take the host's exclusive lock,
`polymarket-ip.lock` in the operator's one lock directory on the host (the capital
loop's `default_lock_dir()`, `~/.factorylab/capital-loop` of the account as the
password database names it, never beside a run), so a second live reader on the host
is refused (`polymarket_ip_in_use`, its own operator code), whichever directory it runs
in, because the budget assumes the IP is the world's own. The lock is released when
the world stops, on every path, including a resume that fails after admission, and by
process death. A world whose reads are answered offline (`simulate_reads`, or the
simulated venue) is admitted with no lock, on any clock.

**The kernel's reads fit its reserve by construction; they are never admitted,
refused or deferred.** A claim is graded on the world at its due pass (essay
II.III.b, prebaked at the Stackelberg move): the settlement reads of `event_facts` are
always sent. What bounds them is a limit on what seats can open. An *open read* is a
seat registration's own: the settlement of its claims on one token due at one tick
(`<registration>|due:<token>:<tick>`), held whether or not another seat holds the same
token and tick; it stays open while its claims are pending, and for one window (10 s
of wall time) after the kernel's last request for it. Each registration holds at most **`N //
max_readers`** of them, where **N = `kernel_reserve_per_10s // 2`** (50 at the
defaults, 3 a seat at 16 slots; published in the world block's `polymarket_reads`),
counted over its own claims alone, so what it is told never depends on another seat
(AGENTS.md rules 4 and 5); a claim past that is refused (`polymarket open read share
spent: <share> open reads`), and a key it already holds, open or counting down, is not
counted again. A world whose seat share would be under 1 is refused at load. A retired
seat's due keys hold its slot for at most `MAX_FORECAST_HORIZON` ticks. *Proof*
(`runtime/polymarket.py`, `open_limit`), every window in wall time: the kernel sends at
most 2 requests for an open read (its market by id and, for a price claim on an open
market, its book, in the one pass that settles that due tick, since every claim due at
a tick settles in the first pass at or after it). A kernel request stamped `s` in a
window `(t - 10 s, t]` keeps every open read holding its settlement counting until at
least `s + 10 s > t`, so every settlement the kernel read for in the window is held by
an open read counting at `t`; a registration opens one only while fewer than its share
count, and holds open reads only through a slot, which is not given again while any
count, so at most `max_readers × N // max_readers <= N` count at any instant: the
kernel sends at most `2 N <= kernel_reserve_per_10s` in any 10 s of wall time. A seat
read admitted at `a` fits the charges in `(a - 10 s, a]` and is charged at its last
send stamp, not before `a` and not after the next admission, so every read of a
registration with a request in a window is counted when the last of them is admitted:
the seats send at most `max_readers × share` in any 10 s of wall time. **Worst case**
at the defaults: the kernel 2 × 50 = 100 and the seats 16 × 6 = 96, **196 of the
published 300**, however long or short the world's ticks run. The 104 left cover only
what the world cannot see: the difference between this host's clock and Polymarket's,
and a request's time in flight. A claim's token is looked up when the claim is sealed (`open_claim`), as the
sealing seat's own read through its venue read slot (a seat without one is refused: `a
polymarket claim needs a venue read slot`), charged 3 requests to its share, the
lookup's most, and always sent, whether or not the world already knows the token, so
sealing behaves the same either way (during an outage every claim is refused alike);
the world's record of a token's market (`PolymarketSurface.token_markets`,
checkpointed; it grows with the distinct listed tokens the world has seen claimed or
traded) serves only the kernel's settlement reads, one GET by market id. A token no
market lists is refused at sealing (`token not listed`), charged to the seat, and not
kept. A refused claim is not sealed: `forecast.refused` is ledgered and its owner is
told. The simulated venue counts what the live reader would send for the same read
(`FakePolymarket.requests_sent`: a token's lookup is 1 to 3 requests, every other read
1). A write's checks read the token's market through the journal and the same lookup.
A seat without a venue read slot does not hold the Polymarket reads. Within one world tick, until a Polymarket write, a read identical to one
already answered in that tick (the kernel's own `order_book` read included) is
answered from that answer and sends no request (`polymarket.read_answered`); the
answer carries no marker. A world whose per-slot share cannot cover one read is
refused at load. Each read tool's description states these limits. Their
answers
carry text third parties wrote (questions, rules, slugs, resolution sources), so they are
outside text exactly as a `connector.fetch` body is: prose of at least
`MIN_PROTECTED_BODY_CHARS` is protected, and a round that read them runs population,
artifact and outcome tools only, so market text cannot reach a write in the same wake. With
the simulated venue, `polymarket.positions {}` reads the pot (free), and
`polymarket.place_limit {token_id, side, size, price}` and `polymarket.cancel {order_id}`
write (free). The writes are consequence writes: only a producing decision with an open
consequence account may make them, each has a client id (`<handle>:<slot>`) and a durable
`polymarket.intent` before submission, a repeat reconciles and never resubmits, an
unanswered intent is polled at most `UNCERTAIN_ORDER_POLLS` times and then released as
unknown, and a batch that writes is weighed whole with the venue's writes.

Custody: collateral is the `polymarket` pot, its own account in `custody_view` and in
`world.pots` (valued at USDC plus tokens at cost, so a buy does not move the total; tokens
listed by count and cost). An order is weighed against that pot alone, with the market's own
tick and minimum size, and never against the Hyperliquid accounts or the reserve. What the pot
settles is ledgered as `venue.settled` with `custody = "polymarket"` and summed on the pot's
own books, never in `BudgetBook.book_venue`; what a decision's event positions realise is its
owner's claim on the pot (`polymarket.claim`), never a venue claim, so `_classify_financing`
cannot convert a Polymarket profit out of Hyperliquid money. Every tick the pot reconciles
`opening + settled == USDC + tokens at cost` and ledgers `polymarket.drift` beyond one
micro-USD. A kill cancels resting orders only: held tokens are paid for, cannot be liquidated
and resolve into the pot, so they are reported as residual exposure (`wind_down_pending`).

Settlement: a fill opens an `event` lot, marked every tick at the midpoint of its book's
best bid and ask (never the CLOB's `/midpoint`, which answers 0.5 for an empty book). At the
consequence backstop a held lot is marked there like a spot lot, so the decision is scored on
the normal horizon at the market's price: the market's anticipatory settlement (essay
II.IV.b). The resolution later closes every lot on the token at its payout (1, 0, or 0.5 on a
50-50), ledgered as `consequence.resolution` with one `resolution` execution receipt per
decision, and its money reaches the owner through `_settle_late` without rescoring. A token
with no two-sided book loses its mark (`polymarket.mark_unavailable`) and its decision falls back as
any unobserved consequence does. Outcome labels are third-party text: outside the jailed reads
every surface carries ids and a normalised `YES`, `NO` or `outcome <n>`.

Forecasts: an enabled block, on either venue, adds two seed-logic predicates to the
world's forecast vocabulary (`world.work` `predicates`, and the forecast schema's
predicate enum); a world without the block offers neither and refuses them. Both take
`token_id` (an outcome token id, decimal digits) beside `horizon_events`:

| predicate | params | y at settlement |
|---|---|---|
| `event_pays` | `horizon_events`, `token_id` | 1 when the token's market has resolved and the token redeems for 1; 0 while it is open, closed without a final resolution, or resolved 50-50 |
| `event_price_above` | `horizon_events`, `token_id`, `level` in (0, 1) | 1 when the token's price exceeds `level`: its redemption value once resolved, else the midpoint of its CLOB book's best bid and ask (exact comparison) |

The world reads the token at the forecast's due tick, through the surface's journal
(`polymarket.event_read`): the market that lists it, one GET by the market id the
claim's sealing found and cached (a claim is admitted only once its token's market is
found, so settlement never looks a token up, and an uncached token is a kernel fault that
raises), and for a price claim on an unresolved market its book. Each token is read once a settlement pass, and every
forecast due on it in that pass settles on that one snapshot. A payout exists only for a closed market whose outcome prices are a redemption (1 and 0, or 0.5 each) and whose UMA status, when stated,
is `resolved`. A read that did not answer, or a price claim with no midpoint, is
`polymarket.event_unavailable`: the claim settles censored and is excluded as
`external_unobservable`. A token no market lists is refused at sealing, so no claim on
one reaches settlement.
The reads are the kernel's measurement and cost no seat anything. `scripts/fastloop.py`,
and `scripts/edition4_rehearsal.py` when it is handed a simulated clock, answer a
live-read world's reads from the simulated venue (`simulate_reads`), which then moves and
resolves on the world's clock; such a run takes no IP lock.

## New kinds of work: reward shapes and predicates

A registration declares which one of the four reward shapes — `judged`,
`forecast`, `conformity`, `exposure` — pays its emitted kind; the declaration
defaults to `judged`, is fixed for the life of that kind, cannot redefine a seed
kind's shape, and a conflicting redeclaration is refused to the proposer's inbox.
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

## Seeing the world: markets, paid sources and storage

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
These six public reads are free: the venue charges nothing for them. Every
venue read a seat can call still spends the venue's per-IP rate limit, which the
kernel's own order, reconcile and account calls share, so the reads are capped.
Hyperliquid documents the limit ("Rate limits and user limits": 1200 weight per
minute per IP; `l2Book`, `allMids`, `clearinghouseState` and
`spotClearinghouseState` weigh 2, every other info request 20, `candleSnapshot`
one more per 60 items returned, `fundingHistory`, `userFunding` and `userFills` one
more per 20; the added weight per interval is not stated and is counted as 1).
`[venue] public_read_weight_per_minute` (default `480`, an integer below `1200`,
fixed for the world's life) is what the population's reads may use. **The limit is
on who reads the venue, not on how many seats exist.** `[venue] max_readers`
(default `16`) is the number of venue read slots: the seeds take slots in manifest
order, a newly registered seat takes the lowest free one if there is one, and a
retirement frees its seat's slot. A freed slot keeps its place and is given again
only once its last holder's last read has left the sliding minute (`slot_free_at`)
and nothing of its Polymarket reads or open reads still counts, in wall time
(`slot_last_reader`), so no two
registrations' reads through one slot ever share a window and nothing of a
predecessor's reads reaches the seat that follows it (AGENTS.md rule 5). Reads are
counted per registration, its id and version, never the id string, and a round of a
version no longer current runs no tool (`tool.refused`, reason `retired`), so it
spends nothing of the next version's share. A seat registered while no slot is free
waits (`slot_waiting`); the queue is served first in, first out, at every
registration, retirement and tick, before any later registration, which joins the
back while anyone waits (`venue.reader_slot {slot: true}` when one is given). A
seat with no slot registers all the same, with every tool but the venue reads, which it is refused as an unknown
or disallowed tool; its proposer's inbox receives a `registration_admitted` item
saying so, and the seat's own `YOU` block carries `venue_read_slot`. It reads the
market through the world update, or through a reader seat by contract. A seat
learns only its own slot: which seats hold slots is not published. **Each slot has
an equal, fixed share** of the budget: the budget divided by `max_readers`,
rounded down, over any sliding 60 s of world time. The share is a manifest
constant, so the shares of every slot never sum past the budget, and nothing
another seat does (reading, registering, retiring) changes a reader's share or its
refusals. A first-come shared budget would let one seat starve the others and
signal them through refusals, and a share over the live seats would let
registering seats shrink everyone's share: both are a third channel between seats
(AGENTS.md rule 4). A read is admitted when the weight its first attempt sends
fits in what the seat's own reads left of its share, and refused before it is sent
otherwise (`tool.refused`, naming the seat's own use and share). The seats' total
is capped by the shares, one seat a slot at a time, at `public_read_weight_per_minute`
(480), which leaves the kernel the rest of the venue's 1200 (720); the physical
per-IP limit is the venue's own (a 429), which `_guarded` backs off from.

**A read answered earlier in the tick is not sent again.** Within one world tick,
until a venue or treasury write that can change what the venue answers (an order,
a cancel, a leverage or vault write, a transfer; not the simulated venue's local
`drain_events`, which the tick's key counts net of while the journal still
classifies it as a write for replay), a seat read identical to a venue request already
answered in that tick (same adapter method, same arguments; the kernel's own
reads of the same endpoints included) is answered from that answer, shaped by the
same tool code: no request is sent (`venue.read_answered`). The seat is charged
for it as for any read, and the answer carries no marker, so a seat cannot
tell a tick's answer from a sent read (AGENTS.md rule 4). The kernel's own reads are never answered this way, so
what a price or a balance has a consequence for is still read afresh. Every
answered read is kept from the journal's own result (`JournalProxy.observer`), and
the tick's answers are dropped at every checkpoint, so a replay answers exactly
what the recording answered.
The first-attempt weights are `venue.instruments` 0 (the adapter answers it from
the listing it loaded and sends no request), `venue.mids` and `venue.order_book`
2, `venue.positions` 6 (user state, spot user state and all mids),
`venue.funding`, `venue.open_orders` and `venue.vault_details` 20,
`venue.vault_positions` 40 (vault equities and leading vaults), `venue.candles` 20
plus 1 per 60 candles and `venue.funding_history` 20 plus 1 per 20 rates, an
out-of-range count taken at its maximum. **A seat's read is sent once**: the
adapter's `_guarded` makes three attempts for the kernel's own calls, but one for
a seat's (`single_attempt`), so no retry can take a seat past the share its read
was admitted on, and the retry reserve in the arithmetic below is zero. A seat
read that meets a 429 or a transient failure fails and the seat is told. The seat
is charged the read's first-attempt weight on admission, and, once it answered,
whatever the live adapter reports it sent beyond that
(`HyperliquidExchange.request_weight_sent`, a journaled read-only call, replayed
from the journal and never counted as a venue write: every attempt `_guarded` makes,
and the item weight of what came back). A simulated venue reports nothing, so the
first-attempt weight is what binds there, the same way; a counter that cannot be
read charges nothing more, and no failure of it escapes the tool call. The sum of all seats' reads over any 60 s is therefore
at most `max_readers × share ≤ public_read_weight_per_minute`, and the rest of
the 1200 is the kernel's: 720 at the default. That headroom rests on an estimate,
not a measurement: at 10-second ticks the kernel's own reads (mids, account and
spot state, asset contexts, open orders, fills and funding pages) come to roughly
90 weight a tick, about 540 a minute. **Load-time invariant:** a world is refused
whose share cannot cover the first attempt of the heaviest venue read it publishes
(`venue.funding_history` at 25 without the vault surface, `venue.vault_positions`
at 40 with it), checked when a manifest is read and again when a runtime is built
from one: a published read no reader could ever be admitted to would be a tool in
name only. The default, 480 over 16 slots, is 30 a slot; a world publishing the
vault surface needs a share of 40, for example `max_readers = 12` at the default
budget. The sliding minute's use and the slot holders are checkpointed. Each
tool's description states the slot and share rules, its weight and the tick rule.
The
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
registered markets, inventory and lots survive a restart. An order refused before it reaches the venue is ledgered with its reason, `order.infeasible` when the venue's free collateral — equity less margin used, carried in the item as `venue_available_usd` — cannot carry the margin the order plus the resting book needs, and `order.refused` for every other pre-submission refusal, and the reason also reaches the ordering seat's outcome inbox. Every counted fill writes one `fill.counted` item at the moment it is counted, with the order id, coin, market, size, price, notional, realised P&L, fee and window; the `event:Fill` the population is delivered is a separate item written on delivery. A live tick broadcasts one `MarketMid` per trading market and one `Funding` per trading perpetual, the manifest seed plus every registered market, never the venue's whole listing, so a registered market enters the broadcast from the next tick and resume restores the set; fills and settled funding payments are never filtered, because they carry cash.

A connector may pay for data through x402 with an exact per-call cap from the
world's own wallet, journaled as one `io.call`/`io.result` pair and never
resubmitted on replay; the seller's charge is the read's whole cost, and above
the cap nothing is billed. The proposal
carries `pay: "x402"` and `max_call_usd` as exact USD text or an integer, parsed
into `max_call_micro`; a cap above `treasury.max_request_micro` is refused. The
paid read is the journal call `connector.paid_fetch`, and the ledger retains
`x402.quote`, `x402.submitted`, `x402.result` and, when the outcome is unknown,
`x402.unresolved` and its later `x402.reconciled`. A quote above the cap returns
HTTP 402 with no data cost. `world.connectors` publishes `optional_fields`,
the `payment` note, and each registered connector's `pay` and `max_call_micro`.

Retained storage costs no money; `[storage]` holds one limit and no price. The public
notebook (`note.put`, `note.get`, `note.list` and `[notes]`) was deleted by ruling
R11: Chapter II §I.b prescribes two channels, rich requests and thin rewards, and
a population-wide blackboard is neither. Its storage rent survived it until Wave
11, which removed it: the bytes sit on the world's own fixed-price disk, so the
rent paid no one, and a debit with no counterparty makes the books lie (the
wallet moves only when money moves). Retained working state is a constraint, and
the hard cast (§II.b) bounds the whole of it, not each version. What the archive
holds, exactly: each seat's current working-state head, at most 64 KiB, and each
program seat's current private state, at most 64 KiB; every superseded head or
state until it is collected (see "Collection": at the next reserve-window boundary
when no checkpoint names it, otherwise at the first boundary after a later
checkpoint, so at most one more per seat is held a window longer); and every
outcome body and archived rationale, retained for the world's life and growing
with decisions, on the order of 0.5 KiB per outcome addressed to a seat (an inbox
body with its evidence pointer and what the seat said). Retirement is final for a
version, not for an id: a retired id's head and a retired program's private state
are kept. **A retired id takes its next version only from its owner**, which
inherits its head (its memory), its inbox and its records; any other proposer is
refused at admission (`a retired id takes its next version only from its owner`; ids
are public, so the refusal discloses nothing) and picks a new id, so no other lineage
ever holds an id whose records are another's private state. Ownership is a lineage
key, never an id string: a new id draws a registration serial (`registration_serial`;
the seeds take the first ones) as its key (`lineage_keys`), which it keeps across its
owner's re-versions, and `registrants[id]` is the key of the seat that registered its
current version at that time. The owner is the seat whose current key equals
`registrants[id]` (the registering handle's seat), or the id itself; a seed has no
registrant, and a seat cannot endow itself, so a retired seed's id takes no next
version. A program's private state is never inherited: a next version is new code,
which cannot be assumed to read the old code's state, so it starts with none, and the
old version's is superseded at the re-registration and released through the journaled
release (`artifact.released` with `cause: "superseded"`, ledgered before the index
changes). A retired version
writes no state: its pending return may settle, but a working-state or program-state
write in it is refused (`state.refused`, reason `retired`). The disk is finite, so
the whole of retained private state has its own hard limit:

| key | default | meaning |
|---|---|---|
| `[storage] retained_private_bytes` | `67108864` (64 MiB) | the most the archive holds as private state (every working-state head and program private state, retired ids' included); fixed for the world's life |

**Retained private state is at most `retained_private_bytes`, always.** It is
counted per reference: every holder's head or program state counts its full size,
whether or not another seat holds identical bytes (the disk may still keep one
copy), so what a seat is told about capacity never depends on another seat's
bytes. The cap bounds the indexed private state: bytes on disk can exceed it by the
releases since the last checkpoint, until collection removes them (see
"Collection"). Outcome bodies and archived rationales are outside the cap, as the
world's record. A retired id's state is kept until capacity is needed: a head or
program-state write that would take retained private state over the limit
releases the kept references of retired ids, oldest retirement first, each through
the journaled release (`artifact.released` with `cause: "capacity"`, ledgered
before the index changes), and only until the write fits. When releasing every
retired reference would still not make room, nothing is released and the write is
refused with `private state is at the world's capacity` (no totals, no sizes), as
on a full disk: a head or a program state alike is ledgered `state.refused` and
left as it was, and the return stands. A live seat's state is never released to
make room. A write replacing a seat's own head or state is measured with the one it
replaces gone. The key is validated at every load, a
resume's included: a positive integer, at least the seeded seats times the per-seat
cap (128 KiB: a head and a private state). At genesis only, it must also be at most
half the free disk of the filesystem the ledger will live on (the working directory
for a world without one), read with `shutil.disk_usage`: an admission about the host
at that moment. The cap is fixed for the world's life, so a resume is never refused
because the host's free space has changed since. The world block's
`storage` section publishes it with the rule above. A retired seat's outcome
bodies and archived rationales are the world's record and stay. Writing a new head
releases the superseded one's reference (`artifact.released`), and `artifact.get`
answers `artifact_released` for it to the seat that released it (for its last
eight releases) and `artifact_private` to every other reader. The world block's
`storage` section states the limits and this retention as facts. The size of every
head is ledgered on its `state.put` item, and the
archive's size after each boundary's collection on `artifact.retained {records,
bytes, released_bytes, window}`, where a measurement could read them so the
charter can price retained state through λ on reward (§II.b soft casts, §IV.a) if
the population proposes to. A world file naming `[notes]`, or a `[storage]` price
(`micro_per_byte_day`), is refused by name; any other `[storage]` key is unknown.
Nothing about retained state reaches a decision's cost, a cost card or a
consequence outcome: `ReturnAccount.carried_micro`, `consequence.carried`, the
`storage` rows of a card's samples and the window's `storage_cost_micro` were
removed with the rent.

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

The wallet is built with the locked amount and a `ReleaseSchedule`; `sum(releases)
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
classification does. A final ledger releases nothing. `next_release_ns` is the absolute time of the next unreleased
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
every event: due releases, reserve-window management (windows still
close, cards are still measured and priced, the archive still collects, and an
activation boundary still falls due), the treasury's window
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

A program seat's call costs no money: its code runs in the world's own jail,
which pays no one, so the wallet does not move for it. `prices.program_micro_per_call`,
the flat price it used to be debited, was removed in Wave 11 and is refused, and the
executor takes no price at all (`ProgramAssembly` has no price field; its routing
ceiling is zero).

**A program seat's entitlement no longer bounds it.** Its calls commit zero, so
its entitlement neither pays for them nor runs out because of them; routing reads
its need as zero. What bounds a program seat is the kernel's hard casts: the
event count (it runs only when routing draws it for an event, once per draw, and a
watcher at most once per sweep, within `[subscriptions]
max_watcher_evaluations_per_sweep`); `tools.max_tool_calls` per request
and the five tool rounds a decision may buy; `tools.max_children`; the jail's wall
timeout (`timeout_s`, 1–10 s) with its CPU rlimit and output cap; the 64 KiB
private-state limit, with one state retained; `connectors.max_calls_per_window`
and, when it holds a venue read slot, the slot's share (`[venue]
public_read_weight_per_minute // max_readers`) for
whatever it fetches; and governance, which retires it through a retirement
proposal. Anything it buys from outside (a model call it subcontracts, a paid
read, a search) is metered at its real price against its entitlement as before.

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
the seat's id), `description`, `inputs` (whose world block's `seats` carries
the program's own row only, the partition the prompt applies; information audit
C3), `outcome_schema` and `state` — and expects on stdout the same Return JSON a model would print, tool calls,
child requests and registrations included; it passes through the same output
validator. The call runs through the meter under the reason `model:program` at a
price of zero, so it is ledgered beside a model call and moves no money. A
non-zero exit, a wall timeout, a reply that is not valid Return JSON, or a `state`
printed under `state_policy = "none"` is a `malformed` return, exactly as a
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
reason}`, and it settles `declined-v1`.

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
index (hash to owner, kind, size, time, references) is checkpointed;
a checkpoint from before the archive restores it empty.

**Ownership is a (sha, owner) reference** (edition 3, R3-F). One blob carries a
reference per writer, each with its own kind and its own moment, so a second
writer of identical bytes owns what it wrote and can read it rather than being
told the first writer's bytes are private. Nothing is published (ruling R11
deleted the unused `public` flag). The **first** reference stays the owner of
record — `owner_for(sha)`, one subject of retirement. Holding bytes costs no
money: the archive is the world's own disk.
`entries()` returns one row per reference, with that reference's owner.
`artifact.list {cursor?}` is free and returns only the caller's own rows (sha,
kind, bytes, when), newest first, 50 a page with `next_cursor` and the caller's
`count`. Rows sharing a timestamp are ordered by when their hash entered the
archive, never by hash: an outcome item's bytes name its evidence's ledger
sequence, which a resume shifts, and the archive index keeps its insertion order
through a checkpoint and a replay. `next_cursor` is a position in that order
(`<ns>:<sha>`), so a row released or collected between two pages never ends the
paging. It names the row by hash, not by its place: places are derived and a
rebuild renumbers them, while the hash names the same row after a resume. A
cursor naming a hash the listing never held resumes at the first row with its
timestamp, so a page may repeat a row but never skips one; a cursor naming
neither a position nor a row the caller holds returns no rows and
`cursor_unknown: true`; the seat's `YOU` `directory` previews the same rows. No list names
another seat's artifacts (information audit C4).

**Collection.** `ArtifactStore.collect()` is the one thing that deletes, and it
can only reach blobs **no reference names** — what a crash
between the durable write and its ledger item leaves behind, and records whose
last reference was released (a superseded working-state head or program state;
`ArtifactStore.release`, ledgered `artifact.released` before the index changes).
A reference names every kind its owner wrote the bytes under, so releasing one
kind never drops bytes the owner still holds as another, and its `kind` names only
what the owner still holds. A released record the latest checkpoint named (it was
in that checkpoint's index) is `pending` until the next durable checkpoint, then
`sealed` (`seal_released`, also applied to the checkpoint a resume restores); one
written and released since the latest checkpoint is sealed at once, because no
checkpoint a resume could start from names it and the replayed tail re-creates it,
releases it and collects it exactly as the recording did. Only a sealed record is
collected, so a resume never needs bytes that are gone; the resume's archive check
skips released records. A record fully released and then written by another seat
is re-owned: its owner of record and kind become the new writer's, so no reader is
shown who wrote the bytes before, and the lineage check reads the new owner. Each
removal of a record is ledgered `artifact.collected {sha, ts}` whatever the disk
does: the record leaves the index and is ledgered even when its unlink fails, and
its bytes are then a leftover, so a live run and its replay ledger the same
removals; `collect()` returns only the hashes whose bytes are gone. Bytes no record
names (a crash's leftover, a put whose item was never written) and the temporary
file of a write torn before its rename are removed without an item, and never while
the journal is recovering. A put that finds a torn or corrupted file under its
hash replaces it atomically with the bytes in hand, which hash to that name. The runtime calls it at each
reserve-window boundary (`continuity.collect_window`). An owned blob is never a
candidate, so collection can never take a seat's working state, an inbox body or
an archived rationale.

`artifact.get {sha}` is a seed tool, version 1, priced at zero and available
to every seat: it returns `sha`, `kind` (the reader's own reference's kind, or
`program.state` for its lineage's program), `bytes` and the content as `text` (or
`base64` for bytes that are not UTF-8) up to 65,536 bytes, an `error` above that
or for a malformed hash, and ledgers `artifact.get {sha, handle, assembly_id,
found, ts}`. **A view never names an owner or anything about another holder**
(essay II.I.b; AGENTS.md rules 4 and 5). The read is **scoped** (edition 3, C1): a
seat reads what it holds a reference to; a program's state is readable by a seat
whose lineage (`BudgetBook.lineage`) holds a `program.state` reference to those
bytes now, whoever wrote them first. A hash that is one of the reader's own last
eight releases (`RELEASED_MEMORY`, kept apart from the records and checkpointed)
answers `{sha, error: "artifact_released"}`; any other hash the reader cannot read
answers `{sha, error: "artifact_private"}`, byte for byte the same whether the
archive never saw it, another seat holds it, it was released or collected, or
leftover bytes sit on the disk, so the store is no existence oracle. The ledger
row carries the same `reason`. `entries()`
returns `(sha, owner, bytes, created_ns)` rows. There is no `artifact.put` tool: the writers are a private-state program
seat and a seat's own working state. `owner_for(sha)` names the owner of record.

### Continuity: working state and the outcome inbox

`runtime/continuity.py` (contract C1) replaces the three-entry `memory` deque,
which evicted a decision before its consequence could settle on it.

`WorkingState` keeps one head pointer per seat over the archive. A seat
advances its own head by returning `working_state` (a JSON object): the kernel
canonicalises it, puts it as `working.state` owned by that seat, ledgers
`state.put {assembly_id, sha, bytes, handle, over_soft, ts}`, and the seat's
next request carries `your_state: {sha, bytes, state}` verbatim. The soft
allowance is 8,192 bytes (accepted, and marked `over_soft`); above 65,536 the
field is refused, the head is unchanged and `state.refused {assembly_id, handle,
reason}` is ledgered. A manifest may seed a head with an assembly's
`initial_state`; without one the head is None. A new head releases the
superseded one (see "Collection"), so a seat retains one head. Retained state
costs no money
(Wave 11: the storage rent paid no one and was removed; see "Seeing the world");
the hard limit is the constraint, and there is no transfer toll.

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

The next request carries `unread_outcomes: {count, more, items, next_after, paging}`.
`count` is every unread item. At most eight items are shown, oldest first: all
items when the queue fits, otherwise the oldest four and newest four. In the
split window the newest entries have `preview: true`: they reveal recent results
and failures but do not count as delivery for acknowledgement. `more` counts
unshown items. `next_after` and `paging.args.after` point after the **oldest
prefix**, so `outcome.list` can discover the hidden middle without skipping it.
Listing is discovery only; fetch a preview or listed item to deliver its body.
`outcome.get {outcome_id}` is a seed tool, version 1, priced at
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
 "trial_windows": 6,
 "predicted_effect": {"card_id": "censorship-bound", "direction": "decrease", "window": 2}}
```

Exactly those keys. `card_id` names a current card that is not already under
challenge; `evidence` is a nonempty string of at most 4,000 chars;
`replacement` has `observation` (a seed or registered observation), `rule`
(`at most`, `at least`, `above`, `below`), a finite `value` and a typed
`window`, and may add `description`, `units` and `answers_for`; it keeps the
challenged card's `id` and `norm`, so adopting it is the ordinary replace
amendment. A challenge also carries its own `predicted_effect`, naming the
challenged card; its ballot is graded on that promise (charter audit P2), and the
replacement's region is built as typed data from `rule` and `value`. `trial_windows` is an integer in `[1, 50]`. An unchanged
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

At the next window boundary the completed trial goes on the standing
committee's agenda: the replace amendment is proposed under the challenge's own
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
"description"}` with exactly those keys, registers a frozen, priced population
tool for sale over x402 (contract C11). Registration does not start a seller
host or publish it to a discovery index. `program_id` must be the
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
complete receipt first becomes a claim, not earned income. Only independent
chain confirmation promotes it to `income.earned`. `_collect_income` books that
confirmed payment once through `_book_income`: compute authority grows by the
verified amount, custody remains `base_reserve`, and the live owner of the
service's program receives the corresponding entitlement. Without a live owner,
the authority remains in the pool. Duplicate receipts cannot mint another
payment. This does not replenish provider credit: a separate confirmed purchase
must convert reserve USDC into usable inference. Venue P&L is a venue-custody
claim and does not itself increase compute authority. See the receipt identity
and verification contracts below for the current rules.

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
release_digest = sha256(sha256(uv.lock) + tree_hash(factorylab/))
```

where the tree hash covers every regular file under the package by relative
path and content, byte-compiled caches excluded, so an uncommitted edit is a
different release exactly as a new commit is. The git head is not an input
(versioning S2): a commit that changes no executable byte is the same release.
It is recorded beside the digest as forensic metadata; it comes from git, else
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
resume retains the original namespace. Absence preserves legacy client IDs. Never change it on a living or resumable world.

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

Proposal, judgement, propensity, subscription, request and order refusals are
ledgered and addressed to the owning seat's outcome inbox under the refused
decision's handle, and to no other seat (information audit C5). The world block's
`registration_feedback` and `return_feedback` broadcasts are deleted.

## Edition 3 R3-B: typed custody, and what may move the compute wallet

From GPT-6 Pro's third reading §2 and §3 (`docs/audits/v6/gpt6-third/reading.md`)
and `docs/plans/edition3-r3.md`. Three quantities are kept apart and never
conflated: the **learning score** (evidence for a rule), the **seat entitlement**
(permission to spend inside the compute budget) and the **assets and credits**
held by a custodian, which change only by a verified transaction, a provider
charge, a refund or a purchase — never by an internal reclassification.
Since Wave 11 an entitlement bounds only what costs money: a program seat's own
calls, a jailed tool, a public read and retained state cost nothing, so an
entitlement does not limit them; their limits are the kernel's (see "Program
seats" and "Seeing the world").

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

It moves only when money moves (Wave 11): provider bills for model calls and
searches, a seller's price for a paid read or an x402 call, treasury fees
(gas, the venue's withdrawal fee, a forwarder's fee); releases; verified income;
and confirmed conversions into provider credit. A transfer of entitlement between
seats (an endowment, a trial, a grant, a bridge) reclassifies money the wallet
already holds and does not move it. That is the whole list.

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
Hyperliquid mids or funding read raises `VenueUnavailable` and is never answered
with the last prices or with an empty list (which would say the venue reports no
funding); a seat's `venue.mids` or `venue.funding` gets that error, and the tick's
events emit no mid or funding event for it. An account fallback to the last
complete snapshot is returned with `stale = true` and its original
`observed_at_ns`, and the prompts (`StaleAccount`), the watchers, a window's opening
equity and the wind-down's final reconciliation (`unknown`, never `flat`) all
refuse it; a seat's `venue.positions` gets `VenueUnavailable` rather than the old
positions.

`[drip]`, `[termination] max_events` and `[venue] collateral_headroom_usd` are
removed (smuggling D-6): no world set them and nothing enforced them. A manifest
that names one is refused.

`[venue] principal_usd` and `[tools] max_leverage` are **deprecated and inert**
(architect decision D1: a cap on the principal or the leverage the population may use
is an objective supplied from outside, a Class-2 imposition). Both keys are still
read, validated and hashed; nothing enforces either. `_collateral_view` is the venue's own view, unchanged, and
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

## Edition 4 factors: prompt, feedback

Two keys turn on one edition 4 change each. Like every key they are hashed at any
value, defaults included (R8). Neither changes a roster digest: a charter
ratified on a roster is still ratified on it when a factor is switched on.
The third edition 4 factor, `[tools] address_enabled` (direct messages between
seats through `address.send`), is deleted by ruling R11: Chapter II §I.b prescribes
two channels, rich requests and thin rewards, and no third one between seats. A
world file that still names the key loads with it ignored, like any other unknown
`[tools]` key; no world under `worlds/` names it.

`[prompt] mode` is `"reference"` (the default) or `"compact"`. Under `reference` a
request carries the whole institutional world inside the cached prefix, which is
what every world did before this key existed. Under `compact` the prefix keeps the
charter norms, the priced capability index, accounting facts and action labels.
Bootstrap reads carry exact argument schemas; complete return contracts and other
reference sections are reached through `world.read`, and proposal shapes through
`catalogue.search`. The directory names every omitted section. Nothing is replaced
with a generated summary or made inaccessible. `Request.section_bytes` measures
actual rendered bytes; the offline comparison is in `docs/audits/edition4-context/`.

Own working state stays inline through 4,096 UTF-8 bytes. Larger state retains its
exact artifact address and `artifact.get` route; storage limits do not change. The inbox carries eight typed indices, not eight full bodies. `outcome.list`
pages further unread indices without acknowledgement, and `outcome.get` returns an
exact body. An index is notice of an outcome, not evidence that its body was read.

Every judge, first tier, meta and ballot, reads the same machine view (Chapter II
§I.b; information audit C1, C2, C7, P5, P8): its own operating access
(`actor_context`: the capability index without population prose, its own seat row,
the clock and provider inventory), never the world block. The judged return's
`description` is the event it answered, with no role clause. Its outputs lose
`propensity`, which the request's PROPENSITY block renders once. No event payload names its author. A judge is not shown its own
consequence standing, and `your_action_policy` is absent when a seat has no
registered learner. When it has one, `your_action_policy` is one draw from that
learner, `{recommended, p}`, never the distribution (Chapter II rulings R4,
information audit P3). A seat whose action taken is the recommended action is
recorded at the learner's own policy, so its round is on-policy; otherwise its own
declared propensity stands, floored as before.

A decision may buy up to five tool rounds, bounded by its existing money and model
call ceilings. Known reads can extend retrieval; a write or child call ends it.
Continuation pricing reserves another call before extending reads, and unknown
prices do not extend them; a program seat's next call reserves zero, since it
costs nothing, so only the round limit bounds its reads. Actual metering remains authoritative. Older tool results
have exact invocation-local `artifact.get` references that expire when the decision
returns; they create no permanent archive entries. The current round's results are
included once. External text retains its restricted continuation. Public `world.read` is available in both prompt
modes.


`[evaluation] producer_feedback` and `grounded_horizon_ticks` were removed by ruling
R1 and are refused at load: a producer decision settles on its judges' verdict, and the
kernel-commissioned final judge, its rubric and its provisional fallback are deleted.

`[evaluation] no_swap_regret_kinds` is a list of event kind names, default `[]`, fixed
for the world's life. Every router the runtime seeds for a named kind (at genesis, or
when the kind first gains an acceptor) is a no-swap-regret learner, Blum-Mansour over
one EXP3 row per arm, instead of mean-based EXP3: the retentive core the essay places
beside the frontier's mean-based learners. No shipped world names `ProducerReturn`:
the judge tier is mostly mean-based (ruling R10, "a significantly higher population of
mean-based no-regret judges"), and a core beside a producer frontier is wave 5's. Each
name must be an event kind the world can route at genesis (a world
kind, a built-in return, or a kind a manifest seat accepts or emits); a misspelt one is
refused. The list is a set: it is kept sorted, so its order never changes the hash.
Every router
credits an abstention (NOOP) its zero-consequence reward, deferred by the mean delay its
seat rounds take to be learned: what a woken seat that delivered nothing scores on the
scales its learned seat rounds settled under, weighted by how many settled under each
(`ZERO_CONSEQUENCE` in `factorylab/runtime/routing.py`), less the card penalty a
decision of the role it would have filled bears in the window it was drawn in
(`router.abstention_priced`; ruling R9): the abstention is recorded as a decision of
that window and priced exactly as a woken decision is, so waking nobody never beats
a woken seat merely because penalties touched only the decisions that acted.
`verdict-v1`, `evaluation-v1`, `exposure-v2`, `counter-v1` and `policy-promise-brier-v2`
are worth 0.5; Brier scores (`brier-v1`, `forecast-mean-v1`) 0.75, the coin-flip forecaster's;
any other definition, and a router that has learned no seat round yet, 0.5. An
unscored seat round with no record of its own is credited the same value. A replaced
router's settled rounds train the router that replaced it, stepped at the size of the
universe they were drawn over when that was larger (`router.step_rescaled`). Each
router's NOOP watch for a window (its draws and lowest NOOP probability) is the
frontier signal inside the immune organ's one learning-death diagnosis: the immune
window records `frontier_invocation`, and the diagnosis names the routers whose every
draw in every tail window left their seats to exploration (`uninvoked_routers`).

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
