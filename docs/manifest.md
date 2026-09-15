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
| `charter.cards[].window.kind` | `"returns"`, `"forecasts"`, or `"windows"` | Required for explicit cards | Executable selector type; its value is population amendable |
| `charter.cards[].window.n` | Positive integer, never a boolean or float | Required; seed cost and well-formedness cards use `100`, forecast skill uses `50` | Population amendable sample horizon |
| `charter.cards[].window.per` | `"role"`, `"assembly"`, or null | Required in JSON; omitted in TOML means null. Seed cost and well-formedness use `"role"`; forecast skill uses `"assembly"` | Population amendable scope |
| `charter.cards[].answers_for` | `producer`, `evaluator`, `meta`, `antagonist`, `all`, or any registered emitted kind | Required | Population amendable pricing responsibility |
| Proposal `predicted_effect.card_id` | Current or proposed card id for amendments; current card id for connectors and retirements | Required; no default | Liability binds to a measurable card |
| Proposal `predicted_effect.direction` | `increase` or `decrease` | Required; no default | Recorded prediction; grading uses frozen-region compliance |
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

A rendered request puts the world facts that hold still first, in one contiguous
`WORLD` block, and everything that moves after it. The block carries the keys
named in `cortex/request.py:STABLE_WORLD_KEYS` — the charter text, mechanics,
scoring, tool, connector, work and observation catalogues, the assembly
catalogue and contracts, the trading markets and their instrument records — and
is byte-identical across consecutive calls to an assembly, so DeepSeek's and
OpenAI's automatic prefix caching hits it without any `cache_control` marker.
It is the head of the **first user message**, never the system message, and
that placement is a boundary, not a preference: the block publishes catalogues
the population writes — registered tool, observation, predicate and work
descriptions, metric cards, the charter text — and the system role is where one
member's prose would outrank every other assembly's own prompt. The system
message is exactly the assembly's world-supplied `system_prompt`; no
population-authored text ever enters it. The cache hit this keeps is the
per-assembly one, which is where the volume is: an assembly's system text is a
constant, so each of its calls opens with the identical `system` message
followed by the identical stable block, and a provider keys on nothing more
than that identical leading sequence. Handle-scoped memory, where a world
registers it, is the one thing that precedes the block and costs that assembly
the hit. The block changes when the charter edition, the mechanics or one of
those catalogues changes, and at nothing else; a live adaptation does not
change it. Everything that moves between calls — `inputs.you`, the event, the account, `recent_mids`, the pots,
note counts, pathologies, the reserve remaining, `governance`, `tick_intervals`,
`registration_feedback`, `adaptive_scoring` and `card_prices` — is rendered
after it, in the same user message inside `INPUTS`. The controller re-prices
every card at every closed window, so the charter disclosure names
`world.card_prices` instead of inlining each lambda; `card_prices` still
publishes every card's current price and region. Where the provider reports
it, `usage.prompt_tokens_details.cached_tokens` is recorded as
`usage.cached_tokens` on the `invocation` item, absent where it is not reported.
Cost metering is unchanged: OpenRouter's reported `usage.cost` already carries
the cache discount.

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
`novelty.grant_consumed` (A13).

A handle that opens and closes its own lot receives realised profit once,
net of its opening fees, funding, other charges and closing fees. Distinct
opener and closer handles retain their separate net attribution.

A verdict is also a prediction that the judged return will not be blamed by the
charter. It is scored against the share of its window's blame the pricing pass
attributed to that return, and the score joins payoff skill in the judge's
standing (`verdict.consequence`). A window that has not closed by the
consequence backstop, or whose attribution evidence was released before it could
be read, judged nothing: there is no fact either way, so the commitment is closed
out unscored (`verdict.unread`). It moves neither the judge's standing nor the
base rate of unblamed returns, and the metas that conformed to that verdict are
graded on the payoff fact alone. A missing fact is never performance.

## Exact measurement

`returns` selects the latest `n` completed invocation responses in each selected
scope. A continuation's cost belongs to its invocation, and a child invocation
is a separate response. For cost, only successful responses in those selected
rows contribute to the mean, and a retained-storage charge is selected beside
them as a cost row of the decision that holds it: it adds to what those
responses cost and is never divided into as one of them, so paying rent can only
raise a cost per response. The `n` are counted over responses alone, before any
charge joins them, and the charges that join a selected horizon are the ones
metered in the same measurement windows as its selected responses, so a charge
never fills a response slot, never displaces a response from a full horizon and
never supplies the support a short scope lacks. No other observation selects
one. Well-formedness uses all selected responses as its denominator. The other
supported return observations are `noop_share`, `revision_rate` and
`tool_calls`.

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
The outcome is whether the measured value satisfies the frozen acceptable
region. `direction` does not determine the outcome. Yes votes predict compliance;
no votes predict its negation. The score is `1 - (vote - outcome)^2`, recorded
as `policy-region-brier-v1`. Amendments, connectors and retirements use this
same liability. Abstentions, failed proposals and missing measurement or region
evidence are censored, with no fast reward.
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
canonical USDC debit for that nonce and observed Venice credit. Unknown evidence
keeps the principal held; it is not converted into a second payment or a claimed
arrival. The mainnet adapter requires wallet-bound Venice credit and Base USDC;
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
| `timing.cadence_sample` | positive integer | `200` | Yes: retained event-latency sample length. |
| `timing.min_ratio` | integer, at least 3 | `3` | Yes: cascade and governance separation. |
| `evaluation.consequence_backstop_events` | positive integer | `200`; scripted worlds `20`; testnet `60` | Yes: consequence horizon and conservative governance period floor. |
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

The shipped testnet manifest sets `tick_interval = "120s"` and
`evaluation.consequence_backstop_events = 60`. With `timing.min_ratio = 3`,
the conservative activation floor is 180 events, or six hours at the declared
tick interval. Both scripted manifests use a 20-event backstop so the
500-event demonstration can activate a card amendment and evaluator retirement
on separate boundaries.

All measured latencies are `settled_event - opened_event`. The ledger also retains
nanoseconds as provenance, but nanoseconds never determine the measured period.
The period is `max(backstop, supported_p90, oldest_outstanding_age)` in events;
unsupported p90 contributes nothing. Multiply by the current tick interval for
the corresponding duration. Both that duration and `min_ratio * period` fresh
events must pass after the previous activation. Activations at one boundary
therefore cannot chain.

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

Cost shares use the card's selected scopes and successful returns. Each selected
row contributes its cost divided by the count of successful responses in that
scope, so a retained-storage charge adds its own cost to the scope it is held in
and is never one of the responses that count is taken over; a scope with no
response of its own is measured nowhere and attributed nowhere. The
contributions are normalised across supported scopes. Evaluator and meta
cost cards therefore charge those roles. Global window cost retains the
producer-cost sufficient statistics. Tool attempts and turnover use the
decision's contribution divided by the window total.
A lower-bound well-formedness violation is allocated by malformed
invocations, so a correct return does not pay for someone else's malformed one;
an upper-bound violation uses well-formed invocations. A zero attributable total
contributes zero. Other observations use `1/n` decisions for the card's role
(or all roles for `answers_for = "all"`), counting the decisions that responded
in the window and not one whose only entry there is a retained-storage charge.
The final score is `clip(raw_score - penalty, 0, 1)`.

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
violated card. Learning death requires k same-cell windows with zero registrations
and revisions. Learning death's only response is that flag: the reserve reads it
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
code. The twenty-two seed observations are registered the same way at bootstrap
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
registered markets, inventory and lots survive a restart. An order refused before it reaches the venue is ledgered with its reason, `order.infeasible` when available collateral excludes it and `order.refused` for every other pre-submission refusal, and the reason also reaches `registration_feedback`. Every counted fill writes one `fill.counted` item at the moment it is counted, with the order id, coin, market, size, price, notional, realised P&L, fee and window; the `event:Fill` the population is delivered is a separate item written on delivery. A live tick broadcasts one `MarketMid` per trading market and one `Funding` per trading perpetual, the manifest seed plus every registered market, never the venue's whole listing, so a registered market enters the broadcast from the next tick and resume restores the set; fills and settled funding payments are never filtered, because they carry cash.

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

`note.put` and `note.get` are a public key-value notebook bounded in UTF-8 bytes
and charged rent per byte-window; unaffordable rent retains the text, an
overwrite cannot escape the debt, reads are journaled and priced, and the wake
publishes counts only. `[notes]` is a hard cast with exactly these keys.

| Key | Default | Meaning |
| --- | --- | --- |
| `max_keys` | `128` | Positive integer count of retained keys. |
| `max_bytes` | `262144` | Positive integer total of key and text bytes. |
| `byte_window_micro` | `1` | Positive integer micro-USD charged per byte per window. |

A key is 1–128 printable UTF-8 bytes. An entry's size is its key bytes plus its
text bytes, and its price is that size plus any unpaid retained bytes, times
`byte_window_micro`. A call above the caller's available compute or its request
ceiling is refused before any debit or overwrite. `note.get` on an unknown key is
an error. Each window boundary charges every retained note for the windows it has
not paid for: `note.rent` when the writer's compute affords it, `note.rent_due`
when it does not, in which case the text stays and the debt is still owed on the
next read or overwrite. The ledger items `note.put` and `note.get` carry the key,
handle, assembly id, cost, window, version and byte count, and `note.put` also
carries the text. The `note.read` journal call is replayable read-only work.
`world.notes` publishes the key and byte counts, the three bounds and the pricing
rule; the wake's `notes` section publishes counts only; the notebook survives
resume.

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

## Operator controls and recovery

The ledger writer lock and every ledger descriptor are close-on-exec, so no child can inherit one or keep a dead world locked; a jailed run that ends in a timeout, an error or an interrupt kills its confined process group before returning, but `sandbox-exec` has no `--die-with-parent`, so on macOS a confined process can still outlive a runtime that is killed outright.

`factorylab kill --world W --ledger L` takes the ledger writer lock, reopens
the original world, records `explicit_kill:operator`, releases the seal and
exits `3`. Stop the running process first so the lock is available. Stopping
the process alone does not terminate the world. Kill loads no credentials
and makes no network call. Failures from `run` and `kill` retain the reason
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
