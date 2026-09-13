# Seat 4 — correctness defects

Audited commit: `86b391c112dccb485b22d0287cf038ce7dcb7ecb`.
Every finding below is independently reproduced by a new intended-behavior assertion in
`tests/audit/test_v3_seat4_boundaries.py`. No implementation changes.

## 1. Blocker — broken: a single decision receives its own trading profit twice

**Location:** `factorylab/settlement/lots.py:191–211`.

**Input and state:** Start one consequence account, place both opening and closing
orders under that handle, buy one unit at 100 and sell it at 100.60, with zero fees
and 1,000,000 micro-USD of compute cost. This is permitted by the ordinary multiple
tool-call path. The reproduction covers both BTC perpetuals and BTC/USDC spot.

**Wrong outcome:** `Payoff.net_micro == 1_200_000` and `y == 1`; the correct values
are 600,000 and 0. The trade earned 0.60 USD against 1 USD of compute. The opening
account receives its realized profit in the lot loop, then the same account receives
the closer's profit again. This corrupts the payoff target and subsequent forecast
grading; it does not create a corresponding wallet deposit. The existing separate
opener/closer attribution can remain intentional without doubling one handle's
own realized profit.

**Reproduction:** `test_one_decision_round_trip_counts_its_profit_once` (two cases).

**Proposed fix:** Distinguish a close of the same handle's lot from a close of another
handle's lot. Credit gross profit once per affected handle while retaining its own
opening fees, funding, and closing fees. Cover partial closes, mixed ownership,
reversals, and both market classes when implementing the repair.

## 2. Blocker — broken: a policy ballot can issue a venue write

**Location:** `factorylab/runtime/compute.py:429–435,552–561`;
`factorylab/runtime/governance.py:647–657,700–713`.

**Input and state:** Seat `seed-decider`, whose contract emits `ProducerReturn`, on
a connector committee. Its fake provider returns a valid Boolean ballot and a
`venue.place_market` tool request to buy 0.0001 BTC, then returns its final ballot.
The test uses the existing fake exchange and does not fetch the connector.

**Wrong outcome:** The exchange records a fill. `_hold_vote` opens a `policy`
decision, but `_invoke` records the assembly's usual producing kind in
`return_kinds`. `_may_write` checks the queue's policy restriction only when that
mapping is absent. A normal contract binding therefore disables the policy refusal,
despite the explicit guarantee that judging decisions and their children cannot
write. The common ballot invocation path also serves amendment and retirement
votes; the executable reproduction is a connector vote.

**Reproduction:** `test_policy_ballot_has_no_venue_write_authority`.

**Proposed fix:** Make a policy decision and any descendant ineligible for venue or
treasury writes independently of its selected output kind. Preserve paid read tools
for ballots. Keep ballot-schema validation independent of the assembly's ordinary
producing schema, including assemblies with several declared output kinds.

## 3. Serious — broken: registered observations cannot enter a charter amendment

**Location:** `factorylab/charter/book.py:72`;
`factorylab/runtime/governance.py:473–494`;
`factorylab/charter/measurement.py:130`.

**Input and state:** A runtime has the registered observation `fresh-measure`, a
fraction with range [0,1], and a deterministic measurement runner returning 0.5.
Propose a card naming it, selecting one closed window and requiring at least 0.4.
The fixture starts the ordinary reserve window and an addressable proposing handle.

**Wrong outcome:** Runtime preflight accepts the live observation, then
`CharterBook.validate` calls `preflight_card(card)` without the live observation
book. The default seed vocabulary rejects the same card with
`ValueError: card fresh-card observation: unregistered observation`.
The proposal cannot reach sortition. This leaves population-written measurement
available in the runtime vocabulary but unavailable for this essential pricing path.
The reproduction isolates admission after registration; it makes no claim about
executing population code on this host.

**Reproduction:** `test_registered_observation_can_enter_a_charter_proposal`.

**Proposed fix:** Give charter validation the same explicit observation vocabulary
as runtime preflight, including the repeated validation inside `propose`. Preserve
the observation identity/version needed by frozen proposals, and test the complete
registration → proposal → vote → activation → priced-window path.

## 4. Serious — broken: evaluator cost cards measure a violation but allocate no penalty

**Location:** `factorylab/runtime/pricing.py:348–368`, especially `354–355`.

**Input and state:** A `cost_per_return` card answers for evaluators, selects their
latest one return, and requires cost at most 500 micro-USD. The sole evaluator
returns successfully at cost 2,000. `measure_card` correctly produces
`{"evaluator": 2000.0}`.

**Wrong outcome:** `_decision_share` returns 0 for that evaluator instead of 1.
Its cost-specific branch includes only contributions whose role is `producer`,
irrespective of the card's `answers_for`. Multiplying a real violation by this share
removes the evaluator's cost pressure. The same exclusion applies to meta cost
cards; the reproduction exercises the evaluator case.

**Reproduction:** `test_evaluator_cost_card_attributes_its_measured_cost`.

**Proposed fix:** Use the card's measured scope and selected supporting returns for
cost attribution. Carry the measured emitted-contract role through contribution
recording instead of assuming the caller is a producer. Preserve the same support
when delayed settlements refer to a closed window.

## 5. Serious — broken: the immune organ diagnoses a different sample from the card

**Location:** `factorylab/runtime/immune.py:76–78`;
`factorylab/runtime/pricing.py:282–287,313–316`.

**Input and state:** A well-formed-rate card selects the last 100 returns and requires
at least 0.9. Its selected sample contains 99 successful returns and one malformed
return: measured value 0.99. The latest raw reserve-window rate is 0. Repeat that
raw observation for the immune organ's configured `k` windows while the card's
selected measurement remains compliant.

**Wrong outcome:** `stable_failure` becomes true. `_close_price_window` computes and
freezes typed card measurements for the controller, but passes raw observation
values to the immune organ. The latter labels those raw values `card:<id>` and
compares them with the card's acceptable region. Its resulting diagnosis can change
router exploration and relieve card prices despite the actual card passing. This
also treats unavailable card support as available whenever the raw channel has a
value. Agreement between live and offline diagnostics would not establish that
either used the correct sample.

**Reproduction:** `test_immune_uses_the_cards_configured_sample`.

**Proposed fix:** Supply the immutable measured card values, support, and regions
from the closed price window to both diagnostic paths. Keep independently defined
raw activity/channel measurements separate from card dimensions.

## 6. Serious — broken: settled terminal meta consequences never qualify their author for committees

**Location:** `factorylab/runtime/governance.py:532–551`;
`factorylab/runtime/feedback.py:310–332`.

**Input and state:** Give `meta-a` the configured minimum of five independent top
meta requests. Finish their compute and settle each through the real
`_settle_meta_consequence` path against a resolved outcome. The fixture verifies
that `consequences_by_assembly["meta-a"] == 5`.

**Wrong outcome:** `_committee_eligible()` still excludes `meta-a`. It gathers lot
accounts only from `verdict`/`exposure` decisions and parent handles of separate
settled `consequence` decisions. A terminal meta's Brier consequence instead settles
its existing `fast` handle and increments consequence experience, so neither
eligibility source includes it. Terminal seed metas cannot earn eligibility through
their intended work. Other available roles can still form a committee; this is
exclusion of qualified participants, not a claim that all governance stops.

**Reproduction:** `test_settled_terminal_meta_consequences_qualify_for_committee`.

**Proposed fix:** Include settled terminal meta consequence evidence keyed by the
original decision handle. Retain the independent-request/ancestry checks and count
each original decision once, consistently with novelty experience.

## Verification and limits

The final new-test run produces `7 failed in 0.24s`: all seven failures are the expected
wrong outcomes above, with no setup errors. A separate deterministic corpus of 432
return/proposal shape mutations completed with accepted-or-malformed results,
accounted compute, and no leaked reservations. All providers and exchanges used
for these checks were fake; no credentials or external funds were involved.

Ruff passed with `All checks passed!`. The full gate was interrupted after reaching
roughly 95%; its verbatim summary was:

```text
====== 55 failed, 1958 passed, 17 skipped, 22 errors in 228.25s (0:03:48) ======
```

The requested `uv run pytest tests/audit/ -o addopts=""` produced this partial
summary on its first interrupted attempt:

```text
============ 13 failed, 125 passed, 11 errors in 189.14s (0:03:09) =============
```

A final repeat, with tracebacks suppressed, again reached the scripted fixture and
was interrupted; that repeat exited 130 during cleanup without a numeric summary.
Complete-suite verification is not established. Existing
tests encounter this host's unavailable jail, and scripted-runtime tests stopped
making timely progress. Direct attempts to run both `scripted` and `scripted-crash`
were refused by the runtime with `NoJail` (`sandbox_apply: Operation not permitted`).
These host failures are not counted as findings about the excluded implementation.
The uv commands used an alternate temporary cache plus offline/no-sync environment
settings because the default cache was not writable here.

Both new files remain uncommitted. The requested `git add` was refused by the
filesystem sandbox: Git cannot create
`/Users/isaacentebi/Desktop/FactoryLab/.git/worktrees/FactoryLab-a3-seat4/index.lock`
(`Operation not permitted`). No other files were staged or changed by this audit.

Required documents, the essay, allowed production modules, manifests, scripts, and
deployment files were read. Review of the existing test files was partial; this is
not a claim to have completed the brief's requested read of every test. The three
excluded source files were not opened. No build-history or earlier audit report
supplied a finding, and no other audit seat was consulted.

The intended system is substantially implemented, but it does not yet work
reliably enough for this launch: these reproductions show incorrect economic
feedback, ballot authority, penalties, diagnosis, eligibility, and measurement
admission. Its editable compositions, routing, charter cards, observations, and
connectors provide real mechanisms in the direction of the essay's Class 3 factory;
the blocked observation-to-charter path and inconsistent pressure prevent certifying
those mechanisms as a working whole. These are demonstrated implementation defects,
not evidence that endogenous objectives or Class 3 emergence have been achieved.
