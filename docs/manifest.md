# Manifest keys

Every parameter the essay leaves open is a manifest key with a seed value; nothing
here is an objective for the population. A **hard cast** is fixed for the world's
life (it is hashed into genesis and no amendment reaches it); a **soft cast** can be
moved by the population through the charter. Keys are listed as their fix pass
introduces them; the polish pass will complete the table for the older keys.

The TOML manifest fixes the initial conditions. Money is stored as integer
micro-USD after exact decimal parsing. The entries below document the round-two
additions and the changed charter contract; other sections retain their existing
keys.

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
verdict feedback. A changed schema requires a new kind name; built-in world or
kernel events cannot be impersonated. A producer may process its own event;
judgement against its own or an ancestor's output is refused. Public registrations
include ids and versions without identifying the author of a judged return.

A multi-kind return must select `emits` on its first response, before any tools
or children run, and cannot change it on continuation. The kernel queue keeps
its original channel (`emits` for a sum of channels); `decision.contract` and
`decision.emits` record the alternatives and one-time selection. Runtime reads
and feedback expose the selected channel. Single-channel seed contracts keep
their original channels. Exposure also publishes the producer-shaped event used
by the shipped evaluator registrations. Judge cost accounts do not add novelty
trials to the forecast and meta-feedback trials they already receive.

`register: [{"kind":"retire","assembly_id":"eval-a"}]` proposes retirement
of the current version. It uses amendment eligibility, sortition, majority and
activation cadence, with the proposer excluded. Retirement ballots carry no
predicted-effect field, so they are censored without inventing a new reward.
Retirement removes future routing and child admission, retains old handles,
accounts and feedback identity, and allows the id's next version to register.

Each depth has one continuation. It returns the final answer after its tool and
child results; additional requests at that continuation are refused. Descendant
costs accumulate against the original parent's remaining ceiling. These are
protocol semantics from A1, not additional population objectives.

The deterministic scripted fixture reuses its existing call schedule: the third
registration slot installs a helper and a producer accepting `ProducerReturn`;
the fourth tool slot requests helper → grandchild with a catalogue tool; the
router-add slot also replaces the custom `Finding` router and proposes retiring
`eval-a`. The earlier scripted replies and seed probabilities are preserved.

## Round-two W3: disclosure, governance, the treasury
# Manifest parameters: round two

The TOML manifest fixes the initial conditions. Money is stored as integer
micro-USD after exact decimal parsing. These entries document the
round-two additions and the changed charter contract; other sections retain
their existing keys. The W3 governance and charter keys are below; the W1
timing, pricing and immune keys follow under "Timing, pricing and immune
settings".

| Key | Type | Default / seed | Hard cast? |
| --- | --- | --- | --- |
| `treasury.max_venice_per_window` | Exact USD decimal string or integer, nonnegative | `"10"` (10,000,000 micro-USD) | Configured resource bound, fixed for a run; not amendable through metric cards |
| `committee.seats` | Integer, at least 3 so the existing three core roles can be covered | `5` | Configured resource bound, fixed for a run |
| `charter.cards[].window.kind` | `"returns"`, `"forecasts"`, or `"windows"` | Required for explicit cards | Executable selector type; its value is population amendable |
| `charter.cards[].window.n` | Positive integer, never a boolean or float | Required; seed cost and well-formedness cards use `100`, forecast skill uses `50` | Population amendable sample horizon |
| `charter.cards[].window.per` | `"role"`, `"assembly"`, or null | Required in JSON; omitted in TOML means null. Seed cost and well-formedness use `"role"`; forecast skill uses `"assembly"` | Population amendable scope |
| `charter.cards[].answers_for` | `producer`, `evaluator`, `meta`, `antagonist`, or `all` | Required | Population amendable pricing responsibility |
| Amendment `predicted_effect.card_id` | Current or proposed card id | Required; no default | Forecast must bind to a measurable observation |
| Amendment `predicted_effect.direction` | `increase` or `decrease` | Required; no default | Forecast outcome type |
| Amendment `predicted_effect.window` | Positive integer count of closed reserve windows after activation | Required; no default | Population-authored liability horizon |

The existing `committee.min_settled`, `novelty.window`, `novelty.share`,
`novelty.trials`, prices, timing and clock parameters are disclosed in
every request's `world.mechanics`. Their runtime values, including temporary
controller decay, are used in that disclosure. W1 owns the penalty-cap change;
until that parameter exists, the mechanics block reports it as null and the
scoring block retains the actual uncapped formula.
controller decay, are used in that disclosure. The scoring block states the
capped, attributed formula recorded under "Observation units and attribution".

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

## Exact measurement

`returns` selects the latest `n` completed invocation responses in each selected
scope. A continuation's cost belongs to its invocation, and a child invocation
is a separate response. For cost, only successful responses in those selected
rows contribute to the mean. Well-formedness uses all selected responses as its
denominator. The other supported return observations are `noop_share`,
`revision_rate` and `tool_calls`.

`forecasts` selects the latest `n` resolved forecast records in each scope.
`forecast_skill` uses paired Brier skill against the baseline as it stood before
each outcome, not lifetime standing. The other supported forecast observations
are `verdict_mean`, `verdict_std`, `consequence_paid_off_rate` and `censored_share`.
Censored records count toward the selector but not a scored outcome mean.

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
Previous-cost-median bounds use the same selector's cost samples. New horizons
may need to warm up when retained history is shorter than a newly adopted card.
The buffers and their active measurements survive resume.

## Committee liability

Eligibility counts distinct decision handles with settled consequences, not
fast or verdict settlements. A decision requested by its own assembly anywhere
in its ancestry cannot qualify that assembly. Proposers cannot vote on their
own amendments. Identical patches, including unchanged prices and clock
intervals, are refused before spending the proposal reservation or seating a
committee. A proposed observation cannot overlap another live card's pricing
role, including an `all` binding.

A valid vote opens a policy decision under `assembly:<id>`, retaining that
identity across amendments. The prediction freezes the named card's selector
and observation. Its baseline is measured immediately before activation. If
activation opens window `i`, horizon `k` settles at the close of `i+k-1`.
The directional outcome is strict: an unchanged value does not satisfy an
increase or decrease prediction. Yes votes predict that outcome; no votes
predict its negation. The score is `1 - (vote - outcome)^2`. Abstentions, failed
amendments and missing measurement evidence are censored, with no fast reward.
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
launch roster after the fix passes merge; this workstream does not ratify a new
edition or run the paid survey.

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
| `evaluation.consequence_backstop_events` | positive integer | `200` | Yes: consequence horizon and conservative governance period floor. |
| `prices.penalty_cap` | finite number strictly between 0 and 1 | `0.5` | Yes: maximum penalty before attribution. |
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

All measured latencies are `settled_event - opened_event`. The ledger also retains
nanoseconds as provenance, but nanoseconds never determine the measured period.
The period is `max(backstop, supported_p90, oldest_outstanding_age)` in events;
unsupported p90 contributes nothing. Multiply by the current tick interval for
the corresponding duration. Both that duration and `min_ratio * period` fresh
events must pass after the previous activation. Activations at one boundary
therefore cannot chain.

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
bounds: larger and negative observations remain measurable. The spec supplies
no further calibration for unbounded observations, so none is fitted from live
samples or inferred from the charter's requested bound.

For card j, `v_j = distance_outside_region / observation.scale`, and
`S = sum(lambda_j * v_j)`. A settlement receives
`min(S, prices.penalty_cap) * share`. When cards measure different quantities,
`share = sum(lambda_j * v_j * share_j) / S`, or zero when S is zero.

Cost, tool attempts and turnover use the decision's contribution divided by the
window total. A lower-bound well-formedness violation is allocated by malformed
invocations, so a correct return does not pay for someone else's malformed one;
an upper-bound violation uses well-formed invocations. A zero attributable total
contributes zero. Other observations use `1/n` decisions for the card's role
(or all roles for `answers_for = "all"`). The final score is
`clip(raw_score - penalty, 0, 1)`.

Closed windows retain their observations, regions and contributions for delayed
settlements. Before a window closes, the most recent closed observations supply
pressure and the current window's observed contribution totals supply shares.
A settlement cannot depend on future returns. Each penalty item records the
terms, window identifiers and shares actually used. Historical windows are
released when no unresolved decision needs them.

Fixed pathology cells include the priced card dimensions and the two activity
dimensions. Raw reward channels remain available for retrospective analysis.
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
