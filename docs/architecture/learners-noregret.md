# Learners that are no-regret: design for the edition-8 redesign

Status: design, for review before the 72-hour run. Inputs: audit s06 findings #1 to #4
(GPT-Sol 6.1), the owner's rulings (propensities stay truthful everywhere; any
stabilisation lives inside the estimator), Chapter II §I.a, §I.b, §II.a, §II.b and §IV.b.
Sibling branches fix #1 (`fix-memory-bounds`: an issuance mark replaces tombstones) and
#3 (`fix-chapter2-p1`: no recommendation inference). This design builds on both.

## 1. What Chapter II requires, and what the code does today

| Requirement (passage) | Code today | What is wrong |
|---|---|---|
| Mean-based no-regret learners at the frontier (§I.a: "at least some naive, mean-based no-regret learning somewhere"; U\* > V needs them) | `EXP3` (`learners/exp3.py:35,42`) with lifetime-constant `gamma` (`router_gamma = 0.1`, `routing.py:686-688`) | `q >= gamma/K` forever, so on a fixed menu regret grows linearly (T/30 on the auditor's game): **not no-regret**. The constant floor also breaks the o(1) clause of the mean-based definition. |
| No-swap-regret learners at the core (§I.a, Blum and Mansour) | `BlumMansour` with constant-gamma EXP3 rows (`routing.py:686`) | Every row has the same floor, so the master puts `>= gamma/N` on a dominated arm: swap regret is linear too. |
| Learning death prevented as a fact about the world (§II.b: "some share of compute and write access is usable only in the context of unhistoried actions") | The novelty niche (`routing.py:742-868`) is a world fact. But exploration inside learners is raised by the organ (`immune.py:199-240`, up to `gamma_max`) and never falls below `seed_gamma` | The niche is right. The permanent `seed_gamma` floor is forced exploration inside learners, which rule 8 forbids, and the organ rewrites `gamma` while snapshots drawn at the old value are outstanding. |
| Propensity is the agent's own accounting (§I.b) | `MIN_DECLARED_MASS = 0.05` and `_floored` (`propensity.py:56,317,384`), stated in the schematic (`cortex/schematics.py:406`) | The declaration is rewritten, so importance weights are biased (finding #4: one reward moves the weight 2 instead of 10). |
| Reward "must find its way back to the exact decision" (§I.b) | Plain-EXP3 frontier routers keep no per-round record. Epochs carry weights (`EXP3.expand`, `BlumMansour.reshaped`) and successors retrain on predecessors' rounds (`update_carried`, `_apply_router_round` with an ad-hoc step rescale, `feedback.py:3153-3205`) | No theorem covers carried weights. The code says so (`exp3.py` docstring, `blum_mansour.py:259`). |
| Memory proportional to outstanding feedback | Tombstones per round (`delayed.py`, #1) | Fixed on `fix-memory-bounds`. This design must not reintroduce lifetime state. |

Seat-registered learners (`governance.py:417-420`, with a seat-chosen `gamma` from
`cortex/registration.py:678`) use the same classes and have the same defects, plus they
are trained off-policy on declared propensities.

## 2. The learners

One learner object per router and per seat learner. Its live state is the **current
phase**: a universe of N actions, a round counter `t`, and cumulative loss estimates.
Rewards arrive on the one map already in force (`(r + B - P)/(1 + B)` in [0, 1]); the
learner uses the loss `l = 1 - reward`.

**Estimator (both classes).** When round `s` settles with drawn action `k`, the learner
adds `l / pi_k` to `L[k]` (frontier) or `p_s,i * l / pi_k` to row `i` (core). Here
`pi_k` is the **truthful logged propensity**: the router's executed distribution, or the
seat's declaration exactly as declared. Nothing is floored. The stabilisation lives in
the estimator's form: a loss estimate enters as `exp(-eta * L)`, which lies in (0, 1],
so a tiny propensity can only push one arm down and never overflows the weights. The
frontier's own exploration (below) bounds the importance weight of every round it
draws itself. A non-finite estimate (a declaration below about 1e-308) trains nothing
and is ledgered `propensity.unlearned`, as today.

**Frontier: anytime EXP3 in follow-the-regularised-leader form, mean-based.**

    gamma_t = min(1, t^(-1/4)),  eta_t = gamma_t / N
    q_t = (1 - gamma_t) * softmax(-eta_t * L) + gamma_t / K    (K = feasible menu)

This is Braverman, Mao, Schneider and Weinberg (2018, *Selling to a No-Regret Buyer*),
Algorithm 5, whose **Theorem D.3** proves EXP3 with exploration `T^(-1/4)` is
mean-based, in anytime form. The exponent is not a free choice. The proof needs
exploration ε with `ε * sqrt(T)` growing, because Azuma's bound on the estimates
scales as `sqrt(T log T)/ε`. Per-round regret falls as `T^(-a)` while the mean-based
slack falls as `T^(a - 1/2)`, and `a = 1/4` is the one exponent at which both vanish at
the same rate.

> **Proposition 1** (frontier, fixed menu, on-policy, oblivious losses).
> `E[R_T] <= N ln N * T^(1/4) + (8/3) T^(3/4) + 16`, and the learner is mean-based.
> Proof sketch: the exponential-weights bound with a nonincreasing rate (Neu 2015, proof of
> Theorem 1) gives `ln N / eta_T + sum_t eta_t/2 * sum_i w_i * lhat_i^2`. On-policy,
> `E[sum_i w_i lhat_i^2] <= N/(1 - gamma_t)`. Mixing costs at most `gamma_t` per round.
> Substituting `eta_t = gamma_t / N` with `gamma_t <= 1/2` for `t >= 16` gives the bound.

The kernel's draw transforms (`_mix_with_standing` and `_cap_adversarial`) scale the
variance term by `rho = max_k q_k / pi_k`, a constant those transforms set.

Why not the auditor's doubling schedule, or the optimal-rate learners? Experiment E6
below shows the reason. Every `sqrt(T)`-rate learner we tried escapes the
Deng–Schneider–Sivan trap. That includes restarted EXP3 with
`gamma_H ~ H^(-1/2)`, EXP3 with no explicit exploration, and EXP3-IX. Escaping the trap
means the learner is **not mean-based**, which is the one property Chapter II asks of
the frontier. Doubling restarts also forget the history a mean-based learner is defined
by.

**Core: SR_MAB with loss-form exponential-weights rows, no exploration.**
Each row `i` holds `L_i`, with `q_i = softmax(-eta_t * L_i)` and `eta_t = sqrt(2 ln N / t)`.
The master `p = pQ` uses the exact solver kept from today. Row `i` learns `p_i * l / pi_k`.
The row proposal `q_ik` is no longer needed, so Lemma 10's observed-gain interface goes.

> **Proposition 2** (core, fixed menu, on-policy). `E[swap regret_T] <= (3/sqrt 2) N sqrt(T ln N)`.
> Proof sketch: Blum and Mansour (2007, **Theorem 11**'s decomposition) give swap regret
> = sum over rows of row regret. Row `i`'s estimate is unbiased for `p_i * l`. Because
> `sum_i p_i q_ik = p_k`, the second moments summed over all rows are at most N per
> round. The exponential-weights bound summed over N rows gives
> `N ln N / eta_T + (N/2) sum_t eta_t`. This matches Stoltz's `O(N sqrt(T log N))`
> (cited in Blum and Mansour 2007, p. 1309) and improves Theorem 11's gain-form
> `O(N sqrt(TN log N))`.

**Phases.** A phase is a new learner on the same identity: `t = 0`, `L = 0`, and the
menu in force. A phase opens on exactly these measured facts, each ledgered first as
`learner.phase {learner_id, phase, cause, universe, ordinal}`:

1. **menu**: the router's universe changed (a registration, a retirement, or a router
   proposal). This uses today's epoch trigger and speed limit (`_epoch_due`, §IV.b).
   A new seat is then present from round 1 of the phase. A strictly mean-based learner
   carrying old sums would draw it only at its exploration share.
2. **revision**: the charter edition or the terms digest changed. §II says the version
   has changed, so the learner is playing a different game.
3. **stable_failure**: an organ step that diagnoses stable failure (section 3).

A phase has no horizon, because the learners are anytime: nothing restarts on a schedule.

**Delayed and censored feedback.** Every router becomes keyed: each draw freezes a
snapshot `{phase, executed pi}` (core: plus the frozen `p`) under its handle, using the
sibling's ordinal mark. Settlement trains the learner only if the snapshot's phase is
current. Otherwise the round is **orphaned**: it settles in the kernel (money,
standing, history) and trains nothing, ledgered `learner.orphaned`. Censored, declined
and timed-out rounds keep today's neutral imputation and train their originating phase
like any other.

This departs from the auditor's proposal to retain completed phases while their
feedback is outstanding. A closed phase never samples again, so feedback to it cannot
change any decision. Rounds still outstanding when a phase closes are counted at full
loss: at most the outstanding count per boundary. Retaining
closed phases costs memory and buys nothing.

The same holds for a router the population replaces. It is kept only while its rounds
are outstanding or owed, as `_retain_router` does now, and trains nothing. Successor
chains and carried updates are deleted. With delays bounded by the decision cutoff
`d_max`, the usual decomposition adds `O(d_max * sum_t eta_t)` (Cesa-Bianchi, Gentile and
Mansour 2019, *Delay and cooperation in nonstochastic bandits*). A delay-tuned rate
`sqrt(ln N / (Nt + D_t))`, with `D_t` the cumulative outstanding count, was tried and
rejected: at delay 120 it multiplied a prototype core's regret by 8 at T = 64k (E2).

## 3. Non-stationarity

A mean-based learner is a "chaser of running averages" (§I.a). Within a phase it does
not track drift, by definition. E3 shows it never recovers from a late switch. That
memory is the property Deng et al.'s U\* is computed against. Doubling restarts do not
fix this. They forget on a schedule unrelated to the world: E3 shows a lag of 640
rounds when a boundary happened to fall just after the switch, and no recovery within
the run when it did not. They also cost the frontier its mean-basedness (E6).

So adaptivity comes from **phase boundaries at measured facts**, not from a rate:

* **Stable failure is the dead-history pathology.** §II.a describes the attractor
  "caused by ... its input changing in a way that the factory is not sufficiently
  incentivized to satisfy". §IV.b's answer is that its duration "needs to ratchet up
  the available gain that can be applied to the loop". §IV.b also defines gain as the
  strength with which a loop converts error into correction, which here is `eta`.
  A fresh phase is the learner at its maximum gain. Each organ step that diagnoses
  stable failure opens a phase on every router the organ steps today. Steps are
  rate-limited by the existing `gain:<kind>` loop (`min_ratio` times the router period,
  §IV.c), so a failure lasting `n` steps keeps the gain at its ceiling for its whole
  duration. Thrash and `cleared` do nothing: gain falls by itself as `t^(-1/4)` or
  `t^(-1/2)`. Learning death holds, as today. `gamma_max`, `gain_step`, `seed_gamma`,
  `router_gamma` and `learning_death_floor` are deleted.
* **Exogenous revisions** (charter edition, terms digest) open phases, as in section 2.
* **Drift that does not fail the charter** is not a pathology. The factory still
  satisfies its input, and §IV.b's requisite velocity binds the organ's loop period,
  which the kernel already checks against `world_repricing`.

No forgetting rate exists anywhere, so there is nothing to cede. With `S` phase
boundaries, regret against the best piecewise-fixed comparator with those breakpoints is
at most `(S+1)^(1/4) (8/3) T^(3/4) + ...` (frontier) and `2.12 N sqrt((S+1) T ln N)` (core),
by concavity. The sibling project's finding that tables which never forget let dead
history dominate is answered by the stable-failure phase. E3 measures the cost: with
the organ's three-window detection (about 1,080 Tick rounds in edition 8), per-round
post-switch regret is 0.079, against 0.385 with no phase and 0.380 today (switch at 24k
of 32k).

## 4. Persistence, ledger and replay

* **State.** Frontier: `{algorithm: "FrontierEXP3", id, phase: {index, cause, ordinal,
  universe, rounds, losses}}`. Core: the same with `rows` (N x N losses). The keyed
  wrapper adds `snapshots: {handle: {phase, executed, p?}}` and the sibling's
  `issued` and `at_issued`. The new algorithm names make every edition-7 learner state
  fail `restore_learner` loudly.
* **Phase boundary across a checkpoint.** A checkpoint holds only the current phase and
  the outstanding snapshots. A phase-1 round outstanding when phase 2 opened restores
  as a snapshot with `phase < current` and is orphaned when it settles, identically
  before and after a restore.
* **Determinism.** `gamma_t = 1/sqrt(sqrt(t))` and `eta_t = sqrt(2 ln N / t)` use only
  correctly rounded `sqrt` on integers, plus `log(N)`. Floats round-trip through
  `repr`, draws replay from logged seeds (`Router.route`), and phase opens are ledgered
  before they take effect. Platform dependence is unchanged from today (`math.exp`).
* **Ledger.** Added: `learner.phase` and `learner.orphaned`, and `phase` on
  `router.learned`. Removed: `immune.gain`, `router.carried`, `router.step_rescaled`,
  `propensity.floored`.

## 5. Memory: live state is bounded by outstanding feedback

Per learner, the live state is the current phase (`O(N)` frontier, `O(N^2)` core), plus
one `O(N)` snapshot per outstanding round, plus the sibling's mark (the handles opened
at one ordinal), plus `ObservedRewards` (`O(N)`).

No closed phase is kept. A snapshot is removed when its round settles, is discarded,
is withdrawn (a quiet draw that never opened a round, which also does not advance
`t`), or is orphaned. Every round has a cutoff (`queue.deadline_tick`), after which it
times out and is removed. So the number of snapshots is at most the draws in the last
`d_max` ticks, which is independent of lifetime.

A retired router lives only while `_retain_router`'s predicate (outstanding or owed
rounds) holds. Summed over learners, the bound is
`O(sum_learners (N^2 + N * outstanding))`.

E5 measured a delay-120 core at 129 B at t = 0, 15.5 KB at 1k rounds, and 15.8 KB at
50k rounds (119 outstanding). The audit measured 199 KB at 10k rounds with tombstones.

## 6. Migration

Learners are kernel-adjacent and the release guard (`resume.py`, C4) refuses a
checkpoint from another release. **Edition 8 starts fresh from v0, and nothing is
migrated.** Two world-file lines change only if Q4 is accepted: edition 8's
`immune.gain_step` and `immune.gamma_max` keys (`worlds/edition8-launch.toml:603-604`
on `launch-world`) cease to exist and would be refused at load.

## 7. Implementation plan

Each PR gets a cold adversarial review and the full verify gate plus soak.

**PR 1: learners** (on top of `fix-memory-bounds`).
Files: `factorylab/learners/{base,exp3,blum_mansour,delayed,router,__init__}.py` and
`tests/learners/*`.
Changes: `FrontierEXP3` and swap rows with the loss estimator; `open_phase`; phase-tagged
snapshots; `withdraw_for`. Delete `expand`, `reshaped`, `update_carried`, `take_for`,
`update_observed_gain` and constant `gamma`.
Failing-first tests:
* `test_frontier_regret_exponent`: expected regret on the auditor's game at T = 2k and
  32k grows by at most `16^0.85`. Today it is 13.9x; the frontier is 8.0x.
* `test_core_swap_regret_exponent`: T = 500 to 8k, at most `16^0.6`. Today 6.4x; the core is 1.04x.
* `test_frontier_is_mean_based_core_is_not_trapped`: on the bandit investment trap, the
  frontier's swap/T is at least 0.12 and the core's at most 0.01 at T = 16k.
* `test_checkpoint_continues_across_phase_boundary`: open phase 2 with phase-1 rounds
  in flight, checkpoint, restore, continue, and compare bit for bit with the
  uninterrupted run, including orphaned settlements.
* `test_state_bounded_by_outstanding`.
* `test_truthful_rare_propensity_is_learned_unfloored`.
Size: about +450 / -350 production lines, +450 test lines.

**PR 2: runtime wiring** (on top of PR 1 and `fix-chapter2-p1`).
Files: `runtime/{routing,feedback,propensity,compute,governance,bootstrap,loop,published,resume,summary}.py`,
`cortex/{schematics,registration}.py`, `docs/manifest.md` and `README.md`.
Changes:
* Every router is keyed.
* Epochs become `menu` phases on a stable identity.
* Orphan path in place of the successor, carry and rescale code (`_hand_over`,
  `_successor_state`, `_apply_router_round`).
* `noop_credits` hold no `p`/`executed`.
* Delete `_floored`, `MIN_DECLARED_MASS`, the floor refusal and its schematic text.
* `gamma` is refused in router and learner proposals.
* `router_gamma` and `seed_gamma` are gone.

Failing-first tests:
* the auditor's `test_runtime_learners_have_sublinear_fixed_menu_regret` (gate): runtime
  routers on the constant-gap game at three horizons, asserting Propositions 1 and 2
  and the growth exponents;
* `test_truthful_rare_propensity_survives_learning_and_restore` (auditor #4);
* `test_epoch_phase_survives_resume` (gate).

Size: about +500 / -900.

**PR 3: organ and world facts.**
Files: `runtime/{immune,routing,worlds,markets or schematics}.py` and `docs/manifest.md`,
plus edition 8's two lines on `launch-world`.
Changes:
* Stable failure opens phases. Thrash and `cleared` do nothing.
* Revision phases (charter edition, terms digest).
* `frontier_invocation`'s floors read the phase's `gamma_t`; the core has no floor.
* `gain_step` and `gamma_max` are refused.
* Publish `world.mechanics.learning` as facts: the schedules, the estimator and the
  phase causes (§I.b, "the structures of requests and rewards").

Tests:
* `test_stable_failure_step_opens_one_phase_per_router`;
* `test_thrash_and_cleared_open_none`;
* `test_charter_revision_opens_phase`;
* `test_manifest_refuses_gamma_keys`;
* `test_learning_schematic_is_published_without_advice`.

Size: about +300 / -250.

## 8. Experiments (stdlib, repo learners vs prototypes; expected regret computed from the policy; 4 seeds unless noted)

**E1. Fixed menu, auditor's game** (`a=1, b=0, NOOP=1`).

| T | today EXP3 | today SR_MAB | doubling EXP3 | doubling SR_MAB | **frontier** | **core** |
|---|---|---|---|---|---|---|
| 1k | 44 | 65 | 86 | 157 | 80 | 6.0 |
| 4k | 144 | 167 | 187 | 369 | 224 | 6.5 |
| 16k | 544 | 567 | 389 | 793 | 633 | 7.0 |
| 64k | 2144 | 2167 | 800 | 1659 | 1789 | 7.4 |
| x per 4x | 3.94 | 3.82 | 2.06 | 2.09 | 2.83 = 4^(3/4) | 1.05 |

Bernoulli version (0.6 / 0.4 / 0.5) at 64k: today 689 / 745; doubling 1365 / 2541;
frontier 544; core 165.

**E2. Delay 120 rounds** (Bernoulli, 64k): frontier 556, core 237; today 714 / 760. A
prototype core with the delay-tuned rate gave 3,904, against 487 untuned.

**E3. Switch** (`a` 0.7 to 0.3, `b` 0.3 to 0.7; 20 seeds).

| Switch at | today | doubling | frontier | frontier, phase at +360 | frontier, phase at +1080 |
|---|---|---|---|---|---|
| 8k of 16k (median lag) | never | 640 | never | 393 | 1114 |
| 24k of 32k (median lag) | never | never | never | 418 | 1119 |
| 24k of 32k (post-switch regret per round) | 0.380 | 0.308 | 0.385 | 0.046 | 0.079 |

**E4. Off-policy, seat declares hold 0.01 / order 0.99.** Target learner's `p(hold)` at
T = 50k, 20 seeds, for hold paying 0.9 / 0.6 / 0.3 (order pays 0.5):

| Estimator | hold 0.9 | hold 0.6 | hold 0.3 | hold estimate / truth |
|---|---|---|---|---|
| floored (today) | 0.10 | 0.10 | 0.10 | 0.20 |
| IX, `beta = eta/2` | 0.97 | 0.97 | 0.97 | 0.32 |
| **truthful** | 0.97 | 0.97 | 0.03 | 1.00 |

The floor ranks the rare arm last whatever it pays. IX ranks it first whatever it pays.
Only the truthful estimator follows the world. At T = 1k it is noisier: the minimum of
`p(hold)` was 0.09 when hold paid 0.6.

**E5. Memory.** See section 5.

**E6. Bandit investment trap** (Deng, Schneider and Sivan 2019; theoretical 3/16 =
0.1875 for a mean-based learner). Swap regret / T at 64k:

| Learner | Swap regret / T |
|---|---|
| frontier | 0.170 (rising: 0.122, 0.162, 0.170) |
| today's EXP3 | 0.172 |
| today's SR_MAB | 0.024 |
| core | 0.002 |
| doubling EXP3 | 0.004 |
| EXP3, no exploration | 0.000 |
| EXP3-IX | 0.000 |

## 9. Open questions for the owner

1. **Accept T^(3/4) frontier regret as the price of being mean-based?**
   *Recommend yes.* Chapter II asks for mean-based first, and E6 shows every
   `sqrt(T)`-rate learner we tried escapes the trap. Over a 26k-round 72-hour router the mean exploration is
   about 0.105, close to today's 0.1, starting at 0.18 at t = 1k and falling to 0.079.
2. **A full phase restart on each stable-failure organ step?**
   *Recommend yes,* rate-limited by the existing gain loop. The alternative, a partial
   decay, needs an architect constant.
3. **Do terms-digest changes open phases, or only charter editions?**
   *Recommend both,* behind the epoch speed limit, since §II names both as version
   changes.
4. **Refuse `gamma` in router and learner proposals, and the manifest keys
   `immune.gain_step` and `immune.gamma_max`?**
   *Recommend yes.* The schedule is part of the algorithm and is published, so a
   parameter with no meaning should not be accepted silently.
5. **Truthful estimator with no IX term, departing from the ruling's example?**
   *Recommend yes,* per E4. IX remains a one-line option inside the estimator if the
   owner prefers bounded single-round steps to unbiasedness.
6. **Delete `Hedge` and Blum–Mansour's full-information branch?** No runtime caller
   uses them. *Recommend a separate small PR after PR 3*, porting `reference_games.py`
   to bandit form.
