# Seat 2 — pathologies, reward hacking, evolutionary pressure

Audited `0ec4df40eb573f546f69a111699e9df07320402e`. Required documents and code/tests were read in the requested order, excluding the three forbidden implementation internals. No other seat was consulted. Findings below distinguish new counterexamples from residual failures of round-one fixes.

## Actual fitness

The seed routers use EXP3: `Δlog(w_a)=γr/(N·p_logged(a))`, initially γ=0.1 (`factorylab/learners/exp3.py:44`). Censored returns provide no update; timeouts give zero. With clipping denoted `[x]₀¹`:

- Producer: `r=[judge_verdict−P_producer]₀¹`.
- Evaluator: `r=[meta_conformity−P_evaluator]₀¹`.
- Highest meta: `r=[1{valid conformity}−P_meta]₀¹`.
- Antagonist: `r=1{any associated forecast underperforms baseline}`; no card penalty.
- `P_role=Σ λ_j·violation_j(last closed window)`, shared across that role, including cards answering for `all`.

These are `runtime/loop.py:1789,2670,2810,3480,3591`. Evaluator execution probabilities additionally mix `0.7·p_router+0.3·W/ΣW` (`:1284`), where `W=[0.5+mean(Brier−baseline_Brier)]₀¹`, capped at 0.5 below 50% coverage (`settlement/standing.py:43`). Brier here means **reward** `1−(q−y)²`. All forecast predicates enter this standing pool.

`settlement/consequence.py:120` binds raw verdict to `return_paid_off`: an owned opening lot must exist and net proceeds, after fees/funding and marking if necessary, must strictly exceed attributable compute/tool cost (`settlement/lots.py`). No opening lot means y=0. Thus profitable outcomes influence producer selection indirectly through judges; portfolio growth is not the producer reward. Binary payoff, pure-closer treatment and optional-forecast dilution are already documented in `REWARD_HACKING.md`, not new findings here.

At 4,000 input/500 output tokens, manifest charges in integer micro-USD are: GLM 850; DeepSeek 4.1 1,100; older DeepSeek 350; Qwen 3.8 835; Qwen 3.7 185; Tencent 495; Luna 1,400; Muse 7,125. Online search adds its separate charge. These are controlled token counts, not measured live-model usage. Cheap models conserve survival budget; there is no direct reward for verbosity. Cost selection is weakened by the shared penalty. HOLD cheaply produces a predictable zero outcome; judges can remain calibrated without discovering anything useful.

Base seed cards price cost against the previous median, schema validity ≥0.9, and forecast skill ≥0. Edition 1 adds cost ≤500 and revision ≥0.05, and replaces forecast-skill pressure with mean verdict ≥0.8 (`worlds/edition1-example.toml:194`). This rewards favorable grading and observable activity more directly than truthful commitments, useful inquiry, or successful revision. Inquiry has no measurable edition-1 card.

## Findings, ranked

### 1. Serious — not Class 3 — Increasing prices erase the gradient needed to leave stable failure

**Location:** `runtime/loop.py:1789–1830`; `charter/controller.py:215–224`; `runtime/cards.py:64`; `runtime/immune.py:45`. Against round-one Codex §10; the new immune gain does not repair the reward floor.

**Essay:** II.II.b, *Versioning: Commitment and the First Commit*: “price the duration of failure”.

**Reproduction:** Close hourly edition-1 windows containing ten well-formed producer returns costing 1,100 each, verdicts 1, and revision fraction 0.1. Cost violation is `(1100−500)/500=1.2`. Seed parameters (`worlds/testnet.toml:173`) implement `λ′=clip(λ+0.5v−0.5max(0,v_previous−v),0,1)` when violating; satisfaction subtracts 0.1. The first two λ values are 0.6 and 1; penalties become 0.72 and 1.2. Even perfect returns receive 0.28, then **zero**. A newly cheap, successful action receives the same previous-window penalty as an expensive failure.

**Ledger/remedy:** `price.window → price.update → decision.settle(score=0) → price.penalty(effective=0)`; from window 3, closure also emits `pathology.stable_failure → immune.gain`. At five-minute ticks these closures occur at 65, 125, 185 minutes. γ rises by 0.05/window, reaching 0.5 at window 10, but multiplying zero rewards still gives zero learning. Offline versioning can flag a supported constant violating span after three windows; detection cannot restore contrast. Three constructed producer windows cost 33,000 micro before judging; the complete fixture below verifies survival through ten windows.

**Fix:** Attribute controllable costs/violations to the responsible decision, and use bounded normalization preserving action differences under aggregate distress. Apply duration pressure without clipping every candidate to the same reward. This is possible within Class 3.

### 2. Serious — not Class 3 — Exposure pays for an unrelated deliberate forecast error

**Location:** `runtime/loop.py:3480–3507,2099`; `settlement/standing.py:25`. Against Fable F7/Opus §8, with a stronger counterexample than the original collusion report.

**Essay:** II.III.b, *Overfitting and Adversarial Populations*: “they have to be real”.

**Reproduction:** Event 1: antagonist returns `{"action":"hold"}`; its 39-micro cost yields y=0. Event 2: judge correctly returns verdict 0, plus two one-event `wallet_up` forecasts, q=1 and q=0. Event 3: wallet has declined. The mandatory verdict scores Brier 1; the optional forecasts score 0 and 1. The wrong optional forecast alone awards antagonist **exposure=1**, although the actual verdict was correct and no failure was exposed. All settle at the same simulated nanosecond. Judge standing remains 1/3 after this first trial, coverage 1; conformity can still support the other 70% of its selection mixture.

**Ledger/remedy:** Correct `forecast.consequence(q=0,y=0)` precedes the wrong optional consequence settlement and full exposure settlement. No exposure price is applied, including `answers_for=all`. In a controlled three-action EXP3 sequence, 300 such wins drive antagonist probability to 0.93332 versus 0.03334 each for producer and NOOP. One seeded antagonist identity does not constrain its traffic share. Offline exposure counts report success; there is no counterfeit-exposure detector.

**Fix:** Require an attributable, independently settled failure of the evaluated verdict, separate optional prediction accuracy, and give persistent collusion consequential liability. Specify adversarial participation bounds if a minority of activity is intended.

### 3. Serious — will break — The live detector treats recovery as thrash and misses frozen behavior

**Location:** `runtime/immune.py:98–136`; `versioning/operator.py:97` (`cell_series`); `versioning/versions.py:191`. New defect in the live replacement for round-one's offline immune system.

**Essay:** II.IV.c, *Clocking the Factory*: “iatrogenic thrash”.

**Reproduction:** Feed supported windows with verdict means `[0,0,0,1,1,1,1,1]`, constant cost/skill/validity, no priced violations, and one registration/window. Start γ=0.2. This is one transition followed by five stable windows. At window 8, the three block distances are 1, 2/3, 1/3; runtime declares thrash, lowers γ to 0.15, and raises decay from 0.1 to 0.2. The same synthetic diary produces **no offline pathology**: offline code requires consecutive cell changes; runtime does not.

Conversely, a complete five-minute fixture produced only HOLD/NOOP behavior, verdict 0, meta conformity 1, and zero registrations throughout ten closed windows. Stable-failure/learning-death flags never fired; thrash fired at windows 8–10. Changing model-cost mixtures and tiny cumulative-skill drift changed quantile cells despite unchanged behavior. At 605 minutes it remained alive with 99,381,240 micro-USD.

**Ledger/remedy:** `pathology.thrash → immune.window → immune.gain`; the evidence shows unchanged final cells in the first probe. Earliest supported thrash intervention is window 8, after five windows at the improved level. Recomputed quantiles provide no fixed material-change threshold.

**Fix:** Share the live/offline predicate, require persistent behavioral changes, freeze/calibrate observation scales, and separate estimator warm-up from population change. Preserve evidence of successful settling before damping it.

### 4. Serious — not Class 3 — Overfitting has no live sampling remedy; conformity remains cheap to manufacture

**Location:** `runtime/loop.py:2274,2754–2880`; `runtime/cascade.py`; `runtime/immune.py:111`; `versioning/versions.py:149`. Against Codex §§12/16 and the partial live-immune fix.

**Essay:** II.IV.b, *Time and Phase*: “simply increase the sampling rate”.

**Scenario:** Producer submits one rejected registration object per twenty well-formed returns. `_carries_revision` counts it: revision=0.05 satisfies the inclusive bound without changing a practice. Honest all-zero verdicts instead create edition-1 verdict violation 0.8, λ=0.4/0.8/1 over three windows, and penalties 0.32/0.64/0.8. These pressures favor cosmetic activity and flattering evaluation.

The 3:1 cascade and jitter exist: three or four arrivals release one representative. All sibling verdicts receive the representative's meta score although only its full rationale is judged. A valid highest-meta conformity receives raw fast reward 1 regardless of its correctness. Repeated favorable representative returns therefore reinforce siblings without individual inspection; the consequence mixture limits but does not eliminate this route.

**Ledger/remedy:** `registration.rejected` coexists with positive revision observations; buffered verdicts receive equal conformity settlements; top-meta fast scores stay 1. Runtime has **no overfitting flag or sampling-rate actuator**, so remedy latency is unbounded, even while solvent. Offline divergence requires positive verdict slope and negative consequence-skill slope; constant overfit, saturation at zero, or warmed-up stationary miscalibration can remain invisible indefinitely. Two supported points can establish slopes, but only retrospective analysis acts on them.

**Fix:** Measure effective revisions, preserve each verdict's adjudication/accountability through batching, and connect persistent norm/consequence disagreement to funded additional evaluation. Bind meta credit to downstream judgment quality.

### 5. Serious — not Class 3 — Three model calls do not provide exploratory patience

**Location:** `runtime/loop.py:1227–1244,2281–2291`; `worlds/testnet.toml:151`; `runtime/immune.py:117–136`. Against Fable F8/Codex §9; protected model funding now exists, but its lifetime ends too early.

**Essay:** II.IV.b, *Time and Phase*: “the compensation period of any exploratory learner must be shorter than the lifetime”.

**Scenario:** Register an assembly, invoke it with a tool continuation (two calls), receive its first judged settlement, then invoke once more. Three lifetime invocations are consumed; `_unhistoried` becomes false although the opened economic experiment may still await its 200-event backstop. A fourth needed follow-up loses protected funding. Entitlement does not guarantee selection or bridge the time until useful consequence becomes known. At the DeepSeek fixture price, three calls cost only 3,300 micro-USD; the problem is the lifetime contract, not wallet insolvency.

**Ledger/remedy:** `novelty.invocation(count=1,2,3)` and an early settled history precede pending consequence completion. A genuinely constant, registration-free population can emit `pathology.learning_death` at window 3/185 minutes, but **no branch responds to that flag**. Offline detects three same-cell, registration-free windows; cheap registrations reset that proxy without restoring an exploratory frontier. The ten-window fixture in finding 3 remained alive without detection or repair.

**Fix:** Reserve selectable exploratory work through a declared consequence horizon, accounting for continuations separately; tie learning-death response to frontier access and realized opportunity, not registration counts alone.

### 6. Serious — not Class 3 — Completed traffic can collapse governance's supposedly slow clock

**Location:** `runtime/cadence.py:29–56`; `runtime/loop.py:3286,3517`. Against Codex §13/Opus §9; nanosecond recording fixes units, not the statistic.

**Essay:** II.IV.c, *Clocking the Factory*: “the factory’s slowest loops”.

**Reproduction:** At a five-minute tick, no-data cadence is `3×200×5min=50h`. Record just one terminal one-event forecast whose open/settle timestamps coincide: the measured period and waiting interval become **zero**. Pending long experiments are absent from the estimator. Enough quick completions similarly keep p90 zero despite some slower completions. Even mandatory no-opening-lot verdicts generate immediate completions; optional forecast spam is unnecessary.

**Ledger/remedy:** `cadence.settlement(latency_ns=0)` permits `charter.cadence(slowest_period_ns=0)` at the next hourly activation boundary while slower work remains pending. Internal events consume event horizons without advancing the external tick. A numeric 3 multiplier provides no time separation here. Versioning's retrospective settling analysis does not feed this gate; no eventual window repairs it automatically.

**Fix:** Estimate the controlled loops' response/settling times, include pending age and insufficient-support bounds, and separate endogenous bookkeeping from environmental observations. Until enough evidence exists, retain a conservative positive floor.

### 7. Serious — not Class 3 — Sortition's new paid votes still carry no policy liability

**Location:** `runtime/loop.py:3169–3265,3591`; `charter/book.py:113–138`; `runtime/immune.py:77`. Against Fable F6/Codex §14; paid voting and five-settlement eligibility are insufficient.

**Essay:** II.IV.a, *Self-Writing*: “any representative error” is “penalized through the conventional reward channel”.

**Scenario:** Coalition assemblies obtain five settled decisions each, including self-requested composition trials; eligibility counts completion, not independent policy evidence. When seated, they approve a harmful λ/card amendment. Each valid ballot receives fast score 1 under a unique `committee:<amendment>:<alias>` actor. `_deliver_returns` only trains router actors; no future policy outcome grades the ballot or its `predicted_effect` text. Voting now costs compute, but agreeing with bad policy has no distinct liability.

An accepted empty patch with an id and predicted-effect text increments the charter edition without changing cards; a CharterBook probe confirmed edition 1→2 with identical cards. Repeating approved cosmetic editions faster than three windows clears immune comparison history each time. This is a conditional attack requiring committee approval, not an observed spontaneous coalition.

**Ledger/remedy:** `committee.decision → fast=1 → charter.activate`; no later policy-dependent voter reward. `immune.window` restarts its horizon. The shared three-amendment cap per return is enforced, but one successful amendment/hour suffices for this reset. Offline retains the diary; it cannot supply missing accountability.

**Fix:** Attach falsifiable policy forecasts and delayed credit to delegates' durable learner identities; preserve unchanged observation histories across cosmetic edits, and assess eligibility using independent experience. Keep λ self-writing available.

## Execution and coverage

Both required CLI runs completed with verified ledgers and wallet conservation. Scripted, 500 events/seed 1: 59,644,062 micro-USD remaining, alive, one amendment activated. Scripted-crash, 600/seed 2: balance-zero termination, −3,481,919 micro-USD, no closed price window. Its one-second ticks cannot exercise hourly remedies before death; it is not the five-minute experiment.

Scratch reproductions are `/tmp/audit2-seat2/audit_probes.py`, `probes.json`, and `five-minute-evidence.json`. The additional 121-tick experiment used edition-1 prices, a fake exchange, no drip, deterministic HOLD/judge/meta providers and in-memory ledgers. This measures mechanism behavior, not live-model strategy or funded execution. No keys were inspected and no mainnet request was made.

No additional distinct finding in compute-market/treasury pots: inspected affordability, metering, transfers, and associated tests; simulated providers do not establish live rail solvency. No additional amendment-cap bypass found. Traced request/scoring disclosures without finding a separate new disclosure exploit. Numerical thresholds, exact protected share and the external consequence predicate remain **open** architect choices; choosing profit alone is not treated here as automatically demoting the factory.

Verification decision: the worktree lacked an environment and `uv sync` panicked. Reused the existing FactoryLab environment with explicit worktree `PYTHONPATH`, disabled sync/bytecode and redirected caches to scratch. An initial gate had a neighboring-test import mismatch; the corrected complete gate passed. Only this report changed; no implementation or test fixes were made.

`uv run ruff check . && uv run pytest` — corrected gate output verbatim:

```text
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab-audit-pathologies
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 14/14 workers
14 workers [1704 items]

........................................................................ [  4%]
........................................................................ [  8%]
........................................................................ [ 12%]
......s......................................................s.s........ [ 16%]
...............................................s........................ [ 21%]
.....s................s................................................. [ 25%]
............s.....s..................................................... [ 29%]
...........................s..................s......................... [ 33%]
.......s.......................s....s................................... [ 38%]
..........................s............................................. [ 42%]
...........s....................................s.......s.........s..... [ 46%]
................................s....................................... [ 50%]
........................................................................ [ 54%]
........................................................................ [ 59%]
........................................................................ [ 63%]
........................................................................ [ 67%]
........................................................................ [ 71%]
........................................................................ [ 76%]
........................................................................ [ 80%]
........................................................................ [ 84%]
........................................................................ [ 88%]
........................................................................ [ 92%]
........................................................................ [ 97%]
................................................                         [100%]
================= 1685 passed, 19 skipped in 168.96s (0:02:48) =================
```
