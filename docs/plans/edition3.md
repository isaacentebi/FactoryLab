# Edition 3: a life that accumulates

Decided 15 September 2026 after GPT-6 Pro's architect reading (`docs/audits/v6/gpt6/`).
The one change: the unit of a seat's existence stops being one answer from one wake and
becomes something it keeps. Everything below follows from that. Class 3 is preserved: nothing
here tells a seat what to want; it gives a seat the means to remember what it wanted, test it,
and be answered.

Deferred on purpose: project funding contracts and commissioned judges (GPT-6 §8). A
population with memory can ask for contracts; we do not hand it a finance department.

## Contracts

### C1 Continuity: working state and the outcome inbox

- Every seat owns a **working state**: bytes it writes, content-addressed in the artifact
  store, with a head pointer the kernel keeps per seat. A seat may return `working_state`
  (a JSON object, soft allowance 8 KiB, hard 64 KiB) in any answer; the kernel stores it and
  advances the head. The next request of that seat renders the head verbatim under
  `your_state` with its sha and byte size. It survives retirement of the model behind the
  seat, restore, and any number of intervening returns. Rent by byte-time applies (C3 of
  edition 2); no transfer toll.
- Every seat owns an **outcome inbox**. When a consequence settles for a decision the seat
  made (payoff, verdict on its return, forecast score, late realization, a program result),
  the kernel appends an inbox item addressed to that seat: the original handle, what the seat
  said then (its rationale, its stated payoff and forecasts), the outcome, the observation
  time, the financial delta, and an evidence pointer. Items are delivered in the next request
  under `unread_outcomes` (bounded: newest 8 inline with the rest counted; `outcome.get` tool
  fetches older ones by handle). The seat's answer may carry `ack_through: <handle>` to
  advance its cursor; an unacknowledged item stays. Nothing is lost when the three-entry
  `memory` deque rolls over; that deque is removed. Checkpoints carry the head pointers and
  cursors; the item bodies are artifacts.
- Artifact access is scoped: a seat reads its own working state and any artifact it created;
  other seats' artifacts are readable only if published (a `public: true` flag on put) or
  listed in the directory (C4). `artifact.get` refuses otherwise with `artifact_private`.
- Acceptance: a seat forms a hypothesis in its state at wake 1, returns three more times, the
  runtime is checkpointed and restored from the diary, the consequence settles, and the
  seat's next request carries both the hypothesis (head unchanged) and the outcome addressed
  to that exact handle. The matched control (state disabled) shows neither.

### C2 Thinking control: the seat decides when to think

- A tick is one **coalesced world update** per seat, not one request per price print: all
  `MarketMid`, `Funding` and other world events since the seat's last wake are folded into one
  `since_you_last_woke` block (per coin: first, last, high, low, funding prints). Fills and
  safety events (liquidation warnings, order rejections) still wake the affected seat
  immediately.
- A seat owns its **subscription**: which event kinds and coins wake it, and a **cadence
  floor** (minimum ticks between paid wakes, default 1). Both are changeable by the seat's own
  answer (`subscribe: {...}`), ledgered, no ballot: it is the seat's own money. A seat may
  answer `defer: <n ticks>`; it is not woken by routine ticks for that long. Fills and safety
  events override defer.
- A **watcher** is a program seat registered by a seat with a trigger predicate over world
  state (price crossing, funding sign, equity threshold) evaluated by the kernel each tick
  without a model call (program price only, `prices.program_micro_per_call`); when it fires
  it wakes its owner with the trigger fact as an event. Registration uses the existing program
  seat route; the trigger is part of the spec.
- The router still samples among the seats that are awake and affordable; a deferred or
  unsubscribed seat is not a NOOP, it is absent from the draw, and the tick may route to
  nobody without that counting as anything.
- The action vocabulary distinguishes `hold`, `investigate` (tool calls only), `build`
  (registration), `govern` (proposal or challenge), `defer`, and orders; propensity is declared
  over these, not over hold versus trade only.
- Acceptance: with two producers, one deferring 6 ticks, the ledger shows no invocation for
  that seat in that span, one on the seventh, and an immediate wake on a fill in between.

### C3 Charter: one card, and the hidden objective removed

- The ratified edition 3 charter carries five norms with their **full definitions in the
  charter object** (not TOML comments): consequential usefulness, epistemic integrity, durable
  agency, bounded reciprocity, fidelity, the last in GPT-6's wording: "Measurements are
  defeasible evidence of the values, not substitutes for them. A favorable measurement is
  insufficient when supported consequences contradict the value it represents. A judge
  identifying such a conflict must name the value, the measurement, the evidence and the
  uncertainty, and make the claim open to challenge. Missing measurement alone is not evidence
  of failure."
- **One card**: `censorship-bound`, narrowed: observation `avoidably_unresolved_share` =
  attributable, avoidably unresolved accepted commitments over eligible commitments due, window
  last 25 eligible due commitments per responsible scope, at most 0.30, lambda 0.10, answers-for
  the commitment owner. Not-yet-due promises, documented external unobservability, and events
  the seat never committed to observe are excluded; no eligible sample means unmeasured, never
  zero.
- `card-consequence-paid-off` and `card-forecast-skill` are removed, **and** the privileged
  path is removed with them: `Settler` no longer trains consequence standing only from
  `return_paid_off`; judge standing is trained from every registered predicate a judge
  forecast, weighted by the charter's cards' `answers_for` when a card names one and equally
  otherwise. `return_paid_off` remains a kernel fact (cash settlement is immutable) and a
  predicate anyone may forecast; it is no longer the unique anchor.
- A **fidelity objection** is a structured verdict field: `{value, measurement, evidence,
  uncertainty}`; it is scored like any verdict, it is contestable through the existing
  challenge route, and it is never a kernel verdict on its own.
- The learning-death diagnosis is "loss of affordable access to investigation and revision",
  never "unchanged behaviour" (edition 2 #87 already requires a gone frontier; keep it so and
  make the wake say why access is lost when it fires).
- Acceptance: a seat whose returns never own a lot but whose forecasts and commitments resolve
  keeps standing; removing a card from the charter removes its effect on standing entirely
  (pinned by a test that flips the card and diffs standing updates).

### C4 The seat sees itself: the world block

Rendered from state, never from a model. Replaces the root-wallet-only impression. Sections and
fields as GPT-6 §9, with these exact keys:

```
self: seat_id, lineage_id, capability_version, request_handle, request_deadline_utc,
      request_cost_ceiling_usd, liable_budget
your_resources: spendable_entitlement_usd, reserved_for_open_work_usd, unsettled_bills_usd,
      provider_credit_available_for_this_route_usd, spend_last_24h_usd,
      next_endowment_release {at_utc, root_amount_usd, your_share_usd},
      runway_at_observed_burn {days_low, days_high, observed_over}, next_release_reachable
world_resources: root_unlocked_usd, root_locked_usd, trading_equity_usd,
      external_net_income_to_date {trading_usd, services_usd},
      principal_converted_to_compute_usd, provider_inventory {openrouter_usd, venice_usd},
      hosting_paid_through_utc
continuity: working_state_head {sha, bytes}, unread_outcomes {count, items}, open_commitments,
      shared_directory_changes, market_data_as_of
accounting_facts: the eight sentences of GPT-6 §9 verbatim
```

- The **shared directory**: `note.list` and `artifact.list` return bounded indexes (key or sha,
  title, type, bytes, owner seat, updated, public) so a reader need not already know a key.
  The one-micro-per-byte notebook transfer toll is removed; byte-time rent stays.
- The institutional catalogue (tool schemas, proposal shapes) becomes a compact index in the
  stable prefix with full schemas fetched by `catalogue.search`; measured, not assumed: the
  request builder logs rendered bytes per section.
- `Request` fields the seat never saw (handle, deadline, cost ceiling, liable budget) are in
  `self`.
- Acceptance: a golden-file test of the rendered block for a scripted world; every number
  reconciles with the ledger (`entitlements`, `wallet.pots`, releases).

### C5 The first world: roster, seeds, money, kill

- Roster of nine as GPT-6 §10.3, with lens prompts from `docs/audits/v6/gpt6/seed-prompts.md`
  verbatim after the common paragraph: mechanism (deepseek/deepseek-v4.1-flash), empirical
  (venice:z-ai-glm-5-3-flash), constructor (openai/gpt-5.6-sol, 4,096 output tokens, cadence
  floor 9 ticks ≈ 16 wakes/day), opportunity (deepseek), judge-consequence (deepseek),
  judge-fidelity (venice GLM), meta-calibration (deepseek), meta-countercase
  (venice:qwen-3-8-flash), antagonist (venice:qwen-3-8-flash). Lenses are population text,
  replaceable by the seat (C1 state may override its lens; the manifest lens is the initial
  head).
- All four producers accept the coalesced update; the population changes subscriptions after.
- Money: $300 inference backing (`locked` schedule: 120 at genesis, 60 at 7d, 14d, 21d),
  `base_share` 0.8 across nine genesis lineages, 0.2 commons; $120 trading capital; $20
  prelaunch calibration outside the world; $24 hosting; $36 fees and contingency. Provider
  inventories shown separately ($220 OpenRouter, $80 Venice).
- **Kill contract**: kill cancels every resting order and closes every open position at
  market, ledgered as `kill.wind_down` before `Terminated`; a dead factory carries no
  exposure. This is precommitted in the manifest (`[kill] wind_down = true`) and stated in
  the README. (Decision: the alternative, accepting residual exposure, leaves real money
  moving with nobody alive to answer for it.)
- Calibration gate before ratification: at least 40 bounded cases per route including known
  opportunities, known no-ops, fee and funding arithmetic, an exact refusal, state retrieval
  after other turns, a restart, and a construction task; every safety and accounting case
  passes; at least 95% valid returns. Run by the experimenter (paid).
- Acceptance: `worlds/edition3-testnet.toml` loads, its hash is pinned, preflight passes;
  ratification on testnet after all prompt text is final.

## Workstreams (parallel, Opus, worktrees)

| | Contract | Files owned | Must not touch |
|---|---|---|---|
| W1 | C1 | `runtime/continuity.py` (new), `kernel/artifacts.py` (scoping), `runtime/feedback.py` (memory delivery only), `runtime/loop.py` (memory deque and request inputs only), `runtime/resume.py` fields, tools `outcome.get`, `working_state` return field in `cortex/request.py` schema | routing, settlement, schematics world block |
| W2 | C2 | `runtime/routing.py`, `runtime/subscriptions.py` (new), `kernel/events.py` (coalesced update), `cortex/assembly.py` (watcher trigger), `runtime/loop.py` (event folding and defer only), `runtime/propensity.py` (vocabulary) | feedback, settlement, schematics |
| W3 | C3 | `settlement/`, `charter/`, `runtime/cards.py`, `runtime/immune.py`, `docs/charter/edition3-draft.toml`, `runtime/worlds.py` (norm definitions field) | loop, routing, schematics |
| W4 | C4 | `cortex/schematics.py`, `cortex/request.py` (rendering), `runtime/notes.py`, `world/venue_tools.py` (list tools), `runtime/wake.py` (section bytes) | loop, routing, settlement |
| W5 | C5 | `worlds/edition3-testnet.toml`, `runtime/venue.py` (wind-down), `runtime/worlds.py` (`[kill]`), `deploy/README.md`, `docs/launch-decisions.md`, `scripts/calibrate_seats.py` cases | everything else |

Gates: each PR runs its targeted tests and ruff; the experimenter runs the full gate on the
integration branch, the slice test extended with C1 and C2 steps, then a testnet rehearsal of
at least 24 hours, then calibration and ratification.
