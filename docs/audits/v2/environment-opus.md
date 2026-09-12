# Cold audit v2 — seat 6: environment, primitives and contracts (Opus)

Read in order: `docs/essay.md` in full; `worlds/edition1-example.toml`, `docs/charter/edition1-draft.md`,
`worlds/testnet.toml`; `docs/design-audit-v2.md`, specs v0.4–v0.7, `docs/build-log.md`,
`docs/handoff.md`; round one's five reports; then `factorylab/`, `tests/`, `scripts/`, `deploy/`,
`worlds/`. Gate reproduced green on this worktree: `1685 passed, 19 skipped` in 295 s. Ran
`scripted --events 500 --seed 1` and `scripted-crash --events 600 --seed 2`; both terminate clean,
conservation and hash chain true. Numbers marked *[run]* come from the 500-event scripted run.
No other seat consulted. No file changed but this one.

Findings ranked by severity. Labels: **not Class 3**, **will break**, **unclean**.

---

## 1. The immune organ diagnoses a frozen factory as thrash and damps its exploration — blocker, [not Class 3]

`factorylab/runtime/immune.py:98-136`, `factorylab/versioning/operator.py:79-123`.

Essay II.II.a: "an incentive-based immune system that corrects each of these pathologies live, in
runtime." II.IV.b: "the duration of a failure state needs to ratchet up the available gain."

`close_window` classifies each window into a cell via `cell_series(windows, cards, bins=3)`. The
quantile cuts are recomputed **from the same windows being classified** (`operator.py:110-115`).
With three retained windows and three distinct values on a dimension, `quantile_cuts` places each
value in its own bin, so the cells are necessarily different. `stable_failure` and `learning_death`
both require `same = len(set(tail)) == 1` (`immune.py:102, 112-119`), so both are unreachable
whenever any measured quantity has measurement noise. `thrash` requires the opposite — three
consecutive block TV distances above `tv_threshold` — and maximally-spread cells give TV = 1.0.

Reproduced twice. *[run]*: windows 2, 3 and 4 all violate all three priced cards
(`cost_per_return` 983/919/921 against a max of 500, `forecast_skill` −0.303/−0.302/−0.302 against
"above zero", `turnover` 229/258/229 against a max of 5) with `registrations = 0.0` in all three —
textbook stable failure *and* learning death — and the summary reports
`{"stable_failure": false, "thrash": false, "learning_death": false}`; recomputing `cell_series`
over those three windows gives cells `(0,0,0,2,2,0,1,0)`, `(1,0,1,0,0,1,2,0)`, `(2,0,2,1,1,2,0,0)`
and `gap_bound = 0.0`. Synthetically, eight windows of a factory frozen at verdict 0.60,
consequence −0.30, cost 920, turnover 230 with 0.1 % noise produce eight distinct cells and
`block TV tail [1.0, 1.0, 1.0]` → `thrash = True`.

Failure scenario: the funded world settles into the attractor every live run so far has shown
(judges bless inaction, nothing pays off). The organ fires `thrash`, which calls
`_gain(rt, "thrash")` and *lowers* every router's EXP3 `gamma` back toward `seed_gamma`
(`immune.py:44-64, 131-134`) and raises the price decay, weakening the penalties. The one live
lever the essay asks for is applied with the sign reversed, and the population is told
`pathologies: {"thrash": true}` in every request (`loop.py:1139`) — a false fact about the world.

Fix: classify against the cards' own absolute regions and fixed scale-anchored bins, not
quantiles of the live history; or, minimally, bin each dimension by its `CardRegion` bounds so an
unchanging profile yields an unchanging cell. Also drop the `previous = []` reset on charter
edition change (`immune.py:76-78`), which discards the history a detector needs after every
amendment. No demotion: this is the essay's detector, implemented against a moving partition.

## 2. The measured governance cadence is zero — blocker, [not Class 3]

`factorylab/runtime/cadence.py:46-56`; `factorylab/runtime/loop.py:1512-1516`, `3517-3531`.

Essay II.IV.c: "an inner loop must resolve itself several times faster than the outer loop", and
the failure it names, "the outer controller will correct against the unfinished transients of the
controlled loop."

`slowest_period_ns` is the p90 of settled forecast latencies **in nanoseconds**. `_emit` stamps
every internal event with `self.clock.now_ns`, which only advances on a tick, and a forecast's
horizon is counted in *events*. The scripted run produces 14,081 events for 500 world events, so a
horizon-10 forecast opens and settles inside one tick and its latency is 0 ns. Measured directly on
the 500-event run: 200 recorded latencies, `min = median = p90 = 0`, `slowest_period_ns = 0`,
`earliest_ns = last_activation + 3 × 0`. `GovernanceCadence.ready` therefore always returns True.
The world block announces it: `"governance": {"slowest_period": "0s", "earliest_activation":
"1970-01-01T00:00:00.000000000Z"}`.

This is not a scripted artefact: the same structure holds live (tick 60 s, roughly 28 internal
events per world event), and `consequence_backstop_events = 200` means the loop the cadence is
meant to track is two orders of magnitude slower than the gate it produces.

Failure scenario: three amendments per return are admissible (`loop.py:2905`); each passes or
fails synchronously inside the proposing producer's own event, and each passed one may activate at
the very next reserve-window boundary. The charter can change edition every window, which (with
finding 1) also resets the pathology detector each time. Governance runs at the speed of the fastest
loop in the factory — exactly iatrogenic thrash.

Fix: measure latency in events, not nanoseconds, or clock internal events; include outstanding
(unsettled) decisions in the estimate so a slow loop is not excluded by being slow; floor the
period at the backstop horizon when the sample is degenerate.

## 3. Propensity is the router's, and it never travels on the request — serious, [not Class 3]

`factorylab/cortex/request.py:19-60`; `factorylab/runtime/loop.py:2103-2180`, `2474-2495`.

Essay II.I.b names one exception to privacy: the propensity score, "directed forward within a
request and stored within the reward queue", which lets "its recipient reconstruct the potential
value of an alternative choice."

Only the second half is built. `PropensityRecord` (`kernel/queue.py:23-65`) is exact, replayable
and correctly sealed. But `Request` has no propensity field, `ProducerReturn` carries
`{kind, payload, outputs, cost, status}` (`loop.py:2566-2578`), and `LearningReturn` carries a
`sampling_ref` handle and no distribution. No consumer of any request can see any road not taken.

Worse, the propensity that exists is the wrong distribution. It is the router's distribution over
*which assembly to wake* — `Sample.action_ids` are assembly ids (`learners/router.py:62-80`). The
essay's propensity is "an agent's own accounting of the statistical field it drew from when making
its decision". The decision an assembly actually makes — order or hold, which coin, which size,
which verdict — carries no propensity at all. Consequence: the Blum–Mansour construction
(`learners/blum_mansour.py`, correct as written) can only build a no-swap-regret learner over
executor selection. The surplus-retaining core the essay wants is one level above where the
decisions are.

Fix: have assemblies declare a decision-time distribution over their own declared action set in the
return (the seed already asks for structured output), log it as a second propensity on the same
handle, and forward it on the `ProducerReturn` as the essay's exception (3). Not a demotion: it is
the primitive as defined.

## 4. The whole topology is published to everyone — serious, [not Class 3] (round-one finding, unfixed)

`factorylab/runtime/loop.py:1145-1162`.

Essay II.I.b, on the weakest possible informatic setting: a transparency collapse where every agent
reveals "the entire decisional set available to it", which "limits the opportunity for
self-organization from U* to V". Spec v0.4 invariant 6: "No component holds the full topology."

`_world_block` still publishes `{"event_kind", "learner", "menu": st.universe}` for every router —
the complete action set of every learner in the factory — and `model_id` for every assembly.
Verified live: `routers = [... {"event_kind": "ProducerReturn", "learner": "EXP3", "menu":
["eval-a","eval-b","eval-c","eval-d","NOOP"]} ...]`. This was finding 7 of `fidelity-opus.md` in
round one; it is not among the two arguments the build log records as dissent
(`build-log.md:395`), and no fix pass touched it.

Fix, unchanged from round one: publish contracts, prices and the *count* of routers per kind; drop
`menu` and per-assembly `model_id`. Cost nil.

## 5. A judge can trade the account it forecasts, and judge its own output — serious, [not Class 3]

`factorylab/runtime/loop.py:2189-2191`, `948-958`, `2421-2460`; `factorylab/settlement/lots.py:126-181`.

Essay II.III (fourth design principle): "the signal that grades an evaluator must sit outside the
loop that evaluator judges."

`_allowed_tools` returns every registered tool to every assembly, including `venue.place_market`,
`venue.close` and `treasury.transfer`; `_run_tool` takes no role. Two consequences the round-one
reports named separately and neither fix pass closed:

(a) `_universe_for` excludes the judged assembly only for `Verdict`/`MetaVerdict` kinds
(`loop.py:950-951`). A child request (`_invoke_child`) may target *any* assembly id, emits a
`ProducerReturn` for the child's answer, and that return is routed to the full evaluator roster —
including the evaluator that produced it. It can then seal a forecast about its own return.

(b) An evaluator's fills are attributed to a `LotOrder` whose handle never had
`consequences.start` called, so `owner in accounts` is false: no account is credited, but the lot
**enters the shared FIFO book**. Every later opposite-side producer fill is consumed against that
lot instead of opening its own (`lots.py:151-177`), so the producer's `opened_lots` stays 0 and
`return_paid_off` settles 0 by construction (`lots.py:239`). A judge that wants its q ≈ 0 forecast
to come true need only hold an opposite position in the same coin.

Fix: gate venue and treasury writes on the acting decision having an open consequence account, and
extend the self-judgement exclusion to `ProducerReturn` by comparing `handle_to_assembly[about]`.

## 6. The return contract has reserved field names it never declares — serious, [unclean]

`factorylab/cortex/assembly.py:249-282`.

Essay II.I: a contract must be explicit enough that consumers do not "implicitly depend on each
other's reasoning patterns, identities, or output-formatting"; II.I.b: a consumer is "never expected
to infer based on contextual metadata."

`_validate_return` types thirteen names globally before the request's own `outcome_schema` is
applied: `action`, `rationale`, `reason`, `status`, `coin`, `side` as strings, `verdict` and
`conformity` as unit numbers, plus `vote`, `register`, `tool_calls`, `requests`, `forecasts`.
Verified: with `outcome_schema = {"properties": {"status": {"type": "object"}}}`, the reply
`{"action": "noop", "status": {"done": true}}` raises "wrong field type" and becomes `malformed`.
None of this appears in `A_RETURN_MAY_INCLUDE`, in `proposal_shapes`, or in any rendered schema.

Failure scenario: a producer composes a child request whose declared outcome schema uses `status`
or `reason` as an object — both natural names. Every such child is censored, the parent's
continuation is marked `"incomplete continuation answer"`, `well_formed_rate` (a priced card,
`answers_for = "all"`) falls, and nothing tells anyone why. Fix: publish the reserved names and
their types in the world block, or namespace them (`_action`, `_verdict`) so the declared schema is
the whole contract.

## 7. One card can zero every settlement in a role — serious, [not Class 3]

`factorylab/runtime/loop.py:1789-1817`; `factorylab/charter/controller.py:258-267`.

Essay II.I.a: the contract must afford "a reward that can be attributed back to the decision that
earned it."

`_penalty_for` takes the latest window's value for every card the *role* answers for and returns
Σ λ·violation, unclipped; `_settle_priced` subtracts it from every settlement of that role in the
window. Violations are not normalised to the observation's range: a `turnover` card with region
"at most 5", scale 1.0, against an observation that measures cumulative filled notional over
starting equity, gives violation ≈ 224. *[run]*: λ saturates at 1.0 on both `turnover` and
`cost_per_return`, 1,660 settlements are penalised, and every producer verdict clips to 0. The
verdict channel carries a constant, and a constant reward teaches nothing.

This compounds with edition 1: `model_cost_efficiency` and `cost_per_return`
(`worlds/edition1-example.toml:195-222`) both name the observation `cost_per_return`, so the same
number is priced twice against two regions on the same role.

Fix: normalise violation by the observation's own scale before pricing (the `CardRegion.scale`
field exists and is set to 1.0 for every non-USD card, `runtime/cards.py:81-82`), cap Σ λ·violation
at `lambda_max`, and refuse an amendment whose card names an observation another live card already
names.

## 8. Smaller, all confirmed on this main — minor

- **Exposure still pays the base rate** [not Class 3]. `loop.py:3480-3495`. *[run]*
  `exposures_won 1431 / exposures_settled 1454` = 98.4 %. A reward that is almost always 1 gives the
  adversarial minority no reason to manufacture the real failure the essay demands ("those events
  cannot be staged in an artificial environment", II.III.b). Round-one finding 8, unfixed.
- **The cascade launders attribution** [unclean]. `loop.py:2867-2880`: the representative meta's
  score settles every sibling handle in the window, against II.I.a's attribution requirement. The
  3:1 ratio and jitter themselves are correct (`runtime/cascade.py:12-21`), though jitter draws from
  only `{3, 4}` at the seeded `jitter_fraction = 0.2`.
- **A card's `window` text is decorative** [unclean]. `charter/charter.py:16-27` vs
  `loop.py:1734-1788`: every card is measured over the reserve window whatever its prose says, so
  edition 1's "rolling 100 returns" and "rolling 50 settled forecasts per evaluator" are not what is
  measured. A rule dressed as a schematic, in the direction of under-disclosure.
- **`capability_versions` is always `{}`** [unclean]. `request.py:21-27` documents it as pinning
  every capability to an exact registry version; `loop.py:2487` passes an empty dict. A contract
  field that lies.
- **Dead physics** [unclean]. `immune.gap_threshold` cannot bind: when `same` is true the matrix is
  1×1 and `contraction` returns `gap_bound = 1.0` by convention (`operator.py:33-45`), so the
  manifest's `gap_threshold = 0.8` is inert. `UpwardBuffer` (`loop.py:622`, `3557-3567`) released
  1,968 summaries *[run]* that reach only the ledger; the cascade gate does the real work.
- **`registration_feedback` is broadcast with its handle** [unclean]. `loop.py:1170`, `2944`. A
  refusal addressed to one decision is that decision's local state, and II.I.b wants local state
  private; "universal read access is the transparency collapse just described."
- **Committee self-dealing survives** [unclean]. `loop.py:3169-3178` does not exclude the
  proposer's own assembly from the committee voting its amendment. Votes settle `CH_FAST = 1` for
  any well-formed reply (`loop.py:3252-3256`) — a positive reward for voting, not for voting well,
  against II.IV.a: "any representative error that misprices the marginal worth of a constraint is
  penalized." Those settlements also count toward `_committee_eligible`, so seating is
  self-reinforcing.

## Where I found nothing

The reward queue is the essay's thin stateful line done properly: addressable handles, retirement
with successor channel maps, censored/timeout statuses that never manufacture a score
(`kernel/queue.py`). The Blum–Mansour reduction matches the cited construction including the
executed-propensity denominator, and `SnapshotLearner` handles delay correctly; I probed the
standing-mix path that crashed in round one and could not reproduce it. The market for compute is
real and plural (`world/market.py`, `venice.py`, `openrouter.py`: discovery, x402 per-request
quotes, three rails), and the compute price list, seller list and catalogue in the world block are
facts, not rules. Author neutrality is enforced at the type (`request.py:41-43`) and the
`ProducerReturn` carries no producer identity. The tick is genuinely the factory's own within
`[min_tick, novelty.window / min_ratio]` and amendable by the population
(`worlds.py:206-208`, `loop.py:3082-3102`); I found no wall-clock dependency that matters beyond
the venue's own.

## Open (the essay leaves these open)

Futarchic λ; the depth of the evaluator recursion; the fixed observation catalogue
(`runtime/observations.py:49-184`) — the essay's only stated anti-overfitting remedy is to "simply
increase the sampling rate (the rate, resolution, and variety of measurement)" (II.IV.b), and no
party can add an observation or change the window, but whether a fixed measurement vocabulary is a
hard cast or a demotion is not settled by the text.

## The seat's question

**Are Chapter II's primitives realised as defined?** Four of them are: the rich, author-neutral
request line; the thin, stateful, addressable reward line with a replayable propensity record; the
kernel's hard casts against the charter's soft casts; and the market for compute, which is a real
multi-rail market the population can shop in. Three are renamed approximations, and the rename
costs something that matters for Class 3. Propensity is the *router's* choice of executor, not the
deciding agent's account of its own field, and it never rides on a request — so no consumer can
price a road not taken, and the no-swap-regret core can only form over executor selection.
Versioning by behaviour exists as a transfer operator but its cells are quantiles of the window
sample itself, so near-invariance is measured against a partition that moves with the data: the
live organ cannot see the two pathologies it was built to correct, and answers the third with the
wrong sign. And the 3:1 cascade, correct for verdicts, evaluates to a separation of exactly zero
seconds for governance, so the outer loop is not slower than the inner loop at all — it is
unbounded. Evaluations are genuinely online, recursive and graded partly by realised consequence,
but the consequence channel runs through an account a judge can trade against, and the adversarial
minority is paid a near-constant. Sortition, the charter's soft casts and the factory's own clock
are the parts I would call faithful without qualification. The environment is not corrupted by a
leak from the architect's world — I read every string that reaches a model and found no build
history, no debug field and no doc — but it is corrupted in two other ways the essay names: it
discloses the full topology, which is more than minimally sufficient, and it now publishes a
pathology flag that is systematically wrong, which is worse than disclosing nothing.
