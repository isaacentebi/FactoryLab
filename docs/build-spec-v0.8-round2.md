# Build spec v0.8: the round-two fidelity fixes

Binding for fix passes 2 and 3 after the round-two cold audit (`docs/audits/v2/triage.md`, section A). Pass 1 (section B) is merged. Every section names the triage id, the essay passage, the decision, the mechanism, and the acceptance check. Where the essay leaves a number open it is a manifest key with the seed value stated; nothing here is a new objective for the population.

The experimenter's decisions (12 September): A1 full composition with maximum freedom; A11 registrable observations; A16 verdict and payoff split, closers credited; A12 the population tops up Venice itself; A5, A10, A17 as recommended below.

Prerequisite: the mechanical split of `runtime/loop.py` (seat 7 item 8) lands first, so the sections below touch disjoint modules. Module names below assume it: `runtime/pricing.py`, `runtime/feedback.py`, `runtime/governance.py`, `runtime/routing.py`, `runtime/venue.py`, `cortex/schematics.py`.

## Pass 2, W1: time, immune organ, prices

### A2. Governance cadence measures the slowest loop

Essay II.IV.c: an inner loop must resolve several times faster than the outer loop.

Defect: internal events share one tick timestamp, so forecast latency is 0 ns and `slowest_period_ns` is 0; pending slow loops are excluded because they have not settled.

Mechanism (`runtime/cadence.py`):
- Latency is measured in **events** (`settled_event - opened_event`, already recorded) and converted to time with the current tick interval; nanoseconds are kept in the ledger item for the record but never drive the gate.
- The period estimate is the max of (a) the p90 of settled latencies and (b) the **age of the oldest outstanding forecast**, so a slow loop counts while it is running. `record_open(handle, opened_event)` and `record(...)` maintain the outstanding set.
- Insufficient support: fewer than `timing.min_support` settled latencies (seed 30) keeps the no-data cap (`backstop × tick`).
- After each activation, at least `min_ratio × period` of **fresh** elapsed events is required before the next; two amendments can never activate at the same boundary.
- The world block publishes `governance.slowest_period` in events and in time, and the count of outstanding forecasts.

Accept: seat 6's reproduction (200 zero-latency settlements) yields a period of at least the backstop cap; with one outstanding forecast opened 150 events ago the period is at least 150 events; the scripted run's world block never shows `0s`; two approved amendments never activate in one window.

### A3. The immune organ sees a frozen factory

Essay II.II.a: an incentive-based immune system that corrects each pathology live. II.IV.b: the duration of a failure state ratchets the gain.

Defects: quantile cells recomputed from the windows being classified; live and offline predicates differ; `violation()` duplicated; edition change resets history; `gap_threshold` inert; `versions` uses other thresholds.

Mechanism (`runtime/immune.py`, `versioning/`, `charter/controller.py`):
- Cells are formed against **fixed anchors**: for each priced card, the bin is {inside region, violating by less than one scale unit, violating by more}; for unpriced dimensions (registrations, revision), fixed absolute bins declared in `ImmuneSpec` (seed: registrations 0 / 1–2 / 3+). A frozen profile yields a constant cell by construction.
- One `violation(region, value)` in `charter/controller.py`; `versioning` and `immune` import it.
- Stable failure: `k` consecutive windows with the same cell **and** at least one priced card violated. Learning death: `k` consecutive same cells with no registration and no revision. Thrash: `k` consecutive **cell changes** (not distances) with no window inside all regions. Same predicates offline and live, one function.
- Edition change does not reset history; cards that disappear drop their dimension, cards that appear start one.
- Response: stable failure raises router gain (as now) **and** halves λ on the violated cards for one window (a failure state that has stopped teaching is priced down, then re-ratcheted, II.IV.b); thrash lowers gain (as now); learning death releases one extra novelty trial per assembly for the next window (A13).
- `factorylab versions` reads thresholds from the ledger's genesis manifest; the defaults in `versioning/__init__.py` are removed.

Accept: seat 6's two reproductions invert (the frozen run flags stable failure and learning death, the noisy-frozen synthetic does not flag thrash); seat 2's `[0,0,0,1,1,1,1,1]` sequence flags nothing; offline `versions` on the scripted diary agrees window by window with the live `pathology.*` items.

### A4. Prices keep the gradient

Essay II.II.b: price the duration of failure. II.I.a: a reward attributable to the decision that earned it.

Defects: Σ λ·violation unnormalised and unclipped; shared across a role; every settlement in a window gets the same number; two cards priced on one observation.

Mechanism (`runtime/pricing.py`, `charter/controller.py`):
- Violation is normalised by the observation's scale (`CardRegion.scale`, set from the observation's declared unit range in the catalogue, not 1.0).
- The penalty applied to a settlement is `min(Σ λ·violation, penalty_cap) × share`, where `share` is the decision's own contribution to the observation in that window when the observation is decision-attributable (cost, well-formedness, tool calls, turnover: the decision's value over the window total) and 1/n otherwise. Seed `penalty_cap = 0.5` (`PricesSpec.penalty_cap`), so a perfect return under a saturated card still earns at least half its reward.
- An amendment whose card names an observation another live card of the same role already names is refused with a public reason.
- The scoring block states the formula as implemented.

Accept: seat 2's ten-window reproduction (cost 1,100 against a 500 ceiling) leaves a cheap successful return with a strictly higher settlement than an expensive failed one in the same window; the scripted run never clips 100% of a window's settlements to zero.

## Pass 2, W2: judges, consequences, the reserve

### A16. Verdict and payoff are two numbers; closers are credited

Essay II.III.b: whether a verdict predicted real downstream outcomes; a consequence metric fixed at the Stackelberg move is permitted.

Mechanism (`settlement/`, `runtime/feedback.py`, `cortex/schematics.py`):
- The evaluator return carries `verdict` (charter quality, unit interval, graded by meta conformity as now) and `payoff` (probability that the return's consequence predicate resolves true, graded by Brier against the realised predicate as the forecast is now). `payoff` is mandatory; the old single-number path is removed.
- Consequence attribution (`settlement/lots.py`): a decision's realised result is the sum over fills it caused of: for an opening fill, nothing until close; for a closing fill, the realised P&L of the closed quantity **credited to the closer**, and, on the same close, the opener's account receives the same realised P&L attributed to its opening decision. Both are net of their own attributable costs. `return_paid_off` is true when the decision's net realised result exceeds its cost by the time of settlement (or the backstop marks it).
- Returns that open or close nothing resolve `y = 0` still; but judges now carry that in `payoff`, not in `verdict`, so useful non-trading work is graded on quality without being punished on payoff.
- Evaluator standing (`_mix_with_standing`) uses payoff Brier only.

Accept: seat 1's A/B/C reproduction: B (the closer) resolves `y = 1` on a profitable close; C (research) judged `verdict 0.9, payoff 0.1` earns a good Brier; the scoring block and `A_RETURN_MAY_INCLUDE` document both fields.

### A5. Exposure pays only for a real, attributable failure, and is priced

Essay II.III.b: the failures have to be real.

Mechanism (`runtime/feedback.py` `_settle_exposures`):
- An antagonist's exposure on a handle settles 1 only when the **evaluated verdict's mandatory payoff** on that same handle scored a Brier worse than the baseline **and** the antagonist's own forecast on that handle beat the baseline. Optional forecasts of the judge never count.
- Exposure settlements pass through `_settle_priced` with the antagonist's role cards (`answers_for` may name `antagonist`), so exposure is priced like every other channel.
- Antagonist routing share is bounded by `EvaluationSpec.adversarial_share` (seed 0.15): the router's probability mass on antagonist assemblies is renormalised to at most that share. The essay's "minority" is a constraint, not a prize.

Accept: seat 2's three-event reproduction awards exposure 0; 300 fake wins move the antagonist's share by nothing beyond the cap; the scripted run's exposure win rate is below 60%.

### A9. No judge trades what it judges, and nothing judges its own output

Essay II.III: the signal that grades an evaluator sits outside the loop it judges.

Mechanism (`runtime/venue.py`, `runtime/routing.py`):
- Venue and treasury write tools are available only to a decision whose handle has an open consequence account (producers and any assembly acting as one); a judge invoking them gets a public refusal.
- An evaluator's fills, if any reach the book (they should not), never enter the shared FIFO; the lot table refuses a fill without an account.
- `_universe_for` excludes, for every event kind, the assembly that authored the event's subject (`handle_to_assembly[about]`), and for a child's return, the parent.

Accept: seat 6's two scenarios (a judge closing against a producer's lot; a judge routed to its own child's return) are refused with ledger items.

### A13. The novelty reserve lasts as long as the experiment

Essay II.IV.b: the compensation period of an exploratory learner must be shorter than its lifetime.

Mechanism: an unhistoried assembly stays unhistoried until `novelty.trials` **settled consequences** (not invocations) have been delivered to it, or `novelty.max_lifetime_windows` (seed 6) have passed; continuations and children do not count as trials; `pathology.learning_death` grants one extra trial per assembly for the next window (A3).

Accept: seat 2's scenario (two invocations plus a continuation, consequence pending) keeps the assembly unhistoried; after three settled consequences it is not.

### A14. Revisions are real and cascades keep attribution

Essay II.IV.b: increase the sampling rate; II.I.a: attribution.

Mechanism: `revision` counts only **accepted** registrations and activated amendments; every verdict in a cascade batch keeps its own meta score when one is later drawn (the representative's score settles the representative; siblings settle at the `EvaluationSpec.sibling_share` (seed 0.5) of it until sampled); the top meta's fast reward for a valid conformity is replaced by its conformity Brier against the eventual consequence; when a role's verdict mean and payoff Brier diverge for `k` windows (the offline overfitting signal), the runtime raises `consequence_mix` (the 0.3 weight) by 0.1 for the next window, capped at 0.7. That is the live sampling-rate actuator.

Accept: one rejected registration per twenty returns leaves `revision` at 0; the divergence signal in the scripted run produces a `sampling.raise` item.

## Pass 2, W3: disclosure, governance, the treasury

### A7. Everyone can read the charter and the mechanics

Essay II.I.b: underdisclosure starves an agent of what it needs to coordinate; schematics are public.

Mechanism (`cortex/schematics.py`): the world block carries `charter` (the full render: norms, cards with observation, region, λ, `answers_for`, window) for every role; and `mechanics`: committee seats and threshold, eligibility rule, novelty share and window, trials, the controller recurrence (η, κ, decay, λ max, penalty cap), the cascade ratio and jitter, the consequence mix, the tick bounds. A `charter.read` tool is unnecessary once this is in every request.

### A8. No topology

Essay II.I.b: transparency collapse. No assembly sees the whole topology.

Mechanism: the world block publishes per event kind the **number** of routers and of assemblies, the contracts, prices and catalogue; `menu` and per-assembly `model_id` are removed. An assembly learns of another only through the exposure and consequence channels and public registrations (`Registered` events remain public: id, kind, accepts; not model, not prompt).

### A15. Committee seats carry liability

Essay II.IV.a: any representative error that misprices a constraint is penalised through the conventional reward channel.

Mechanism (`runtime/governance.py`, `charter/`):
- A ballot's fast settlement is replaced by a **delayed** settlement: each vote records the amendment's `predicted_effect` as a forecast over the named card's observation (direction and window), settled against the measured observation `k` windows after activation (yes-votes score on the prediction; no-votes score on its negation). Votes settle through the voter's durable learner identity, not a per-amendment alias.
- Eligibility counts settled consequences on decisions the assembly did not request itself.
- The proposer's assembly never sits on the committee for its own amendment.
- An amendment whose activation leaves the charter unchanged is refused before the vote.

### A6. Windows are executable; edition 1 is re-drafted with the launch roster

Essay II.IV.a: every contribution converts a world-finding into authored record.

Mechanism: `MetricCard.window` is a typed field `{kind: "returns"|"forecasts"|"windows", n: int, per: "role"|"assembly"|null}` and the pricing pass measures exactly that; the free-text window is gone; an amendment with an unmeasurable window is refused with a reason. `scripts/draft_edition1.py` is repaired (`answers_for`, typed window), preflights every proposed card through the pricing pass before the vote, shows no existing cards and imposes no per-assembly proposal cap (the survey's anchoring, seat 3), and records the roster hash it surveyed. The re-draft is run by the experimenter as part of the first move, with the launch roster, after this pass merges.

### A12. The population tops up Venice

Essay II.IV: a continuous, reciprocal flow of capital.

Mechanism (`world/treasury.py`, `world/treasury_rails.py`): `treasury.transfer` gains `direction: "to_venice"` in the fixed $5 tranche, journaled two-phase like the other legs, bounded by `TreasurySpec.max_venice_per_window` (seed $10); the pots view shows Venice credit; the CLI top-up remains for the pre-launch seed only and refuses when a world ledger exists.

### A17. The wake shows outcomes

Essay I.III: the control tower reads the outcomes the factory produces.

Mechanism (`runtime/wake.py`): venue equity, realised P&L to date, reserve and Venice balances, the five sealed aggregates with per-assembly names replaced by role counts; no positions, no entry prices, no assembly ids.

## Pass 3, W4: composition is the population's

### A1. Full composition, maximum freedom

Essay II.I: a hard-coded pipeline of agents is a waterfall. II.IV.c: an orchestration armature the factory is licensed to tear down.

Decision: the kernel keeps exactly three hard casts about organisation: (1) nothing judges its own output or its ancestors' (A9); (2) every decision has a wallet reservation and a consequence account; (3) resource bounds (depth, fan-out, budget per event, tool calls) are manifest constraints. Everything else is registrable.

Mechanism (`cortex/registration.py`, `runtime/routing.py`, the step methods):
- **Roles become contracts.** A registration declares `accepts` (any event kinds, including `ProducerReturn`, `Verdict`, `MetaVerdict`, `Tick`, market events) and `emits` (the return kinds it may produce: `ProducerReturn`, `Verdict`, `MetaVerdict`, `Exposure`, or a population-declared kind with a declared schema). The four seed roles are four such contracts, shipped as registrations, not as code. `_role_for_kind` is replaced by dispatch on `accepts`; reward channel is chosen by the `emits` kind of the return (verdict-shaped returns settle on conformity and payoff; producer-shaped on verdict; exposure-shaped on exposure).
- **Retirement.** A proposal `{"kind": "retire", "assembly_id"}` from any assembly, subject to the same sortition vote as an amendment, removes the assembly from every router's universe; its learner history and open handles are retained, delayed feedback still settles to its identity, and it may be re-registered under a new version. The seed population is retirable.
- **Recursive children with tools.** `requests` may nest to `tools.max_depth` (seed 4) and `tools.max_children` per request (seed 3), each child metered against the parent's reservation; children may call tools and make their own requests; a parent may continue once per depth level.
- **Routers are population objects.** A registration may replace the router for any event kind (already possible) and may register a router for a population-declared event kind.
- The world block and the seed prompts say all of this in one paragraph: what an assembly may accept and emit, that any of them may be retired, that requests nest.

Accept: seat 1's reproduction (parent → helper → grandchild) executes all three; a registered producer accepting `ProducerReturn` is admitted; a retire proposal removes a seed evaluator and its pending feedback still settles; the scripted world exercises each once.

## Pass 3, W5: propensity and measurement

### A10. The deciding agent's propensity rides on the request

Essay II.I.b: the propensity score is directed forward within a request and stored within the reward queue.

Mechanism: an assembly's structured return may declare `propensity: {"<action>": p, ...}` over its own declared action set (order/hold/coin/size buckets, or verdict buckets); the kernel logs it as a second `PropensityRecord` on the same handle; the `ProducerReturn` forwarded to judges carries it; Blum–Mansour may be registered at the assembly level over the declared action set. Absent propensity is recorded as degenerate (the chosen action at 1.0).

### A11. Registrable observations

Essay II.IV.b: increase the rate, resolution and variety of measurement. II.IV.a: continuously add holdout criteria.

Mechanism: a registration `{"kind": "observation", "id", "description", "unit", "range": [lo, hi], "code"}` where `code` is a pure function `observe(facts) -> float` over the public per-window facts (the same facts the catalogue computes from, passed as JSON), run in the tool jail under the tool limits, versioned in the registry, and admitted only after a preflight run on the last closed window succeeds. A card may then name it. The catalogue's 22 become seed observations registered the same way. An amendment naming an unregistered observation is refused with a reason.

## After pass 3

Edition 1 re-draft with the launch roster (A6). Pass 4: seat 7's polish list. Round three: the same seven seats on the result.

## Pass 3, W8: registrable connectors (information sources are the population's)

Essay II.I: composability means the factory can discover primitives, assess their contracts and assemble them. I.I: what a factory can know must not be decided for it.

Defect: population code runs in a jail with no network, and the only things that reach the world are rails the architect chose. The set of information sources is fixed at the first move.

Decision: the jail is unchanged. A **connector** is the simplest possible read-only rail: an https origin the population registers by vote, which the kernel fetches from on the population's behalf.

Mechanism, deliberately minimal:
- Registration kind `connector`: `{id, description, origin}`. `origin` is `https://<host>` only. Nothing else is configurable.
- Admission: the same sortition vote as an amendment, after a kernel preflight `GET origin/` that must answer within the bounds. Public reason on refusal. Versioned in the registry as `connector:<id>`.
- Use: a tool call `connector.fetch {id, path}` from any return. The runtime fetches `origin + path` with GET, no headers beyond `Accept` and `User-Agent`, no credentials, https only, response cut at `connectors.max_bytes`, timeout `connectors.timeout_s`, hosts in `connectors.origin_denylist` (the venue's and providers' hosts, private ranges) refused. Metered at the flat `connectors.call_price_usd` per call from the caller's reservation; at most `connectors.max_calls_per_window` per assembly. The body reaches the caller as text in `seen_tool_results` on its continuation; a population tool may parse it in the jail via the ordinary tool path.
- Ledger: `connector.registered`, `connector.call` (id, path, status, bytes, cost; never the body), `connector.refused`. The observatory lists connectors and calls per day.
- Manifest keys (seed values): `connectors.max_bytes = 262144`, `connectors.timeout_s = 10`, `connectors.call_price_usd = "0.001"`, `connectors.max_calls_per_window = 60`, `connectors.origin_denylist`.
- No cache, no byte pricing, no params schema, no path prefix. Seed: none; the architect registers no connector.

Accept: the scripted world (fake transport) registers a connector by vote, calls it from a producer, parses the body in a jailed tool, and the ledger shows the chain; a non-https origin, a denylisted host, or a body over the cap is refused with a reason; the jail test proving population code has no network still passes.
