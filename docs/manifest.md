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

## Round-two W3: disclosure, governance, the treasury

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
`sampling.lower` (A14); `novelty.release` (A13).

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
