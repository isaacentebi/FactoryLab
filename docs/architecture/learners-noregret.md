# Learners that are no-regret: design for the edition-8 redesign (revision 2)

Status: revised after GPT-Sol 6.1 rejected revision 1 (`951d947b`). Inputs: audit s06
findings #1–#4; the owner's rulings (propensities stay truthful everywhere, and any
stabilisation lives inside the estimator); and Chapter II §I.a, §I.b, §II.a–b, §III.b
and §IV.b. Sibling branches fix #1 (`fix-memory-bounds`: an issuance mark replaces
tombstones) and #3 (`fix-chapter2-p1`: no recommendation inference).

**What changed from revision 1.** Revision 1 failed the review on these points:
- It treated "the estimate is finite" as stability.
- It ignored the draw transforms, which change what is actually played.
- It claimed a stronger core guarantee than it proved.
- It wrongly said that a T^(3/4) frontier was the necessary price of being mean-based.
- It said nothing about coverage for off-policy learning.
- It reset learners on every stable-failure diagnosis.
- Its lifecycle bound ignored owed credits and window closure.

All seven points are accepted. Revision 2 takes the smaller, citable route and says
plainly which parts are research.

## 1. Two learning situations, and an inventory of the learners

**(a) On-policy.** The learner's own draw picks the action. Here the classical
guarantees can apply. These are:
- every kernel router choosing which seat receives an event: the frontier routers for
  every kind, and the core routers for `evaluation.no_swap_regret_kinds` (`Tick` in
  edition 8);
- routers that the population adds or replaces (the `router` proposal);
- request routers (`shared.is_request_router`);
- the extra judge draws (`_draw_more_judges`).

Rounds that no router sampled, such as a `self` request carrying the parent-selected
propensity, are already excluded by `_router_sampled`.

**(b) Off-policy.** The kernel never chooses a seat's action (AGENTS rule 8). These
are seat-registered learners (`assembly_learners`, from the `learner` proposal),
trained on the propensity the seat declares. Their output is a recommendation the seat
may ignore (`_action_policy`). Here the learner does not control coverage, so
no-regret is not claimed (§2.4).

**What is wrong today** (measured in §6):

| Mechanism | Location | Defect |
|---|---|---|
| Constant `gamma` in frontier EXP3 | `exp3.py:35,42`; `routing.py:686-688` | Linear regret: about T/30 on the auditor's game. |
| Constant `gamma` in the core rows | Same places | Linear swap regret. |
| Exploration ratchet | `immune.py:199-240`, with `seed_gamma` as its floor | Exploration forced inside the learners, which rule 8 forbids. It also rewrites `gamma` while snapshots taken at the old value are outstanding. |
| Declared propensities floored | `propensity.py:56,317,384`; `cortex/schematics.py:406` | The declaration is rewritten, so the estimate is biased (#4). |
| Epochs carry weights to successors | `EXP3.expand`, `BlumMansour.reshaped`, `update_carried`, `_apply_router_round` | No theorem covers the carried weights, which the code's own comments admit. |
| Gain diagnostics | `routing.py:1382-1391,1447-1478` | They compare against `seed_gamma`. |

## 2. What must change before the 72-hour run

### 2.1 Frontier: unrestarted, anytime EXP3 with a half-exponent schedule

The learner keeps a cumulative loss estimate `L` and counts its opened rounds `t` (a
quiet draw is withdrawn and does not count). Losses are `l = 1 − reward`, on the one
map already in force. At each draw:

    gamma_t = min(1, t^(-1/2)),   eta_t = gamma_t / N,
    q_t = (1 − gamma_t) · softmax(−eta_t · L) + gamma_t / K,   K = size of the feasible menu

The schedule is set at the draw and depends only on `t`, so no organ step can rewrite
it.

**Proposition 1** (fixed menu, synchronous on-policy feedback, oblivious losses, with
the estimate `l/q_k`):

    E[R_T] ≤ (N ln N + 4)·sqrt(T) + 3

*Proof sketch.* The exponential-weights bound with a nonincreasing rate (Neu 2015,
proof of Theorem 1) gives `ln N/eta_T + Σ_t (eta_t/2)·E[Σ_i w_i·lhat_i²]`. On-policy,
the expectation term is at most `N/(1 − gamma_t)`. Mixing costs at most `gamma_t` per
round. For `t ≥ 4`, `gamma_t ≤ 1/2` and `Σ t^(-1/2) ≤ 2·sqrt(T)`, which gives the bound.

**Mean-based** (anytime; this is our own argument, adapting Braverman et al. 2018,
Theorem D.3, which is fixed-horizon).
- *Concentration.* The estimation error `Lhat_i − L_i` is a martingale. Each increment
  is at most `K/gamma_t ≤ K·sqrt(T)`, and the predictable variance is at most
  `Σ_t K/gamma_t ≤ (2/3)·K·T^(3/2)`.
- *Freedman's inequality,* with a union bound over arms and rounds, then bounds the
  error uniformly by `O(K·T^(3/4)·sqrt(log T) + K·sqrt(T)·log T)` with probability
  `1 − 1/T`.
- *The mean-based condition.* Take slack `delta_T = C·T^(-1/4)·sqrt(log T)`. If arm i
  trails arm j by `delta_T·T` in true cumulative reward, then `t ≥ delta_T·T` and the
  estimated gap is at least `delta_T·T/2`. The arm's softmax mass is then at most
  `exp(−delta_T·T/(2N·sqrt(T)))`, which is `o(1)`, and its exploration mass is at most
  `(delta_T·T)^(-1/2)`, also `o(1)`.

So the learner is mean-based and has `O(sqrt(T))` regret. Revision 1's claim that
mean-basedness forces `T^(3/4)` regret was wrong: Braverman et al. chose the exponent
1/4 "for convenience of analysis". The quarter-exponent schedule is dropped.

**Measured.**
- At T = 26k: regret 114, against 911 for the quarter exponent.
- Growth per 4x horizon: x1.54 (deterministic game) and x1.24 (Bernoulli game).
- In the trap the learner stays trapped (swap regret / T = 0.170), as a mean-based
  learner should.
- Delay of 120 rounds: 292, against 269 without delay.

### 2.2 Core: the published Blum–Mansour bandit reduction, with doubling epochs

The core is SR_MAB as it is today (Blum and Mansour 2007, §5). Its rows are Auer
EXP3, which satisfies Lemma 10 (`blum_mansour.py` is kept, including the row-gain and
Lemma-10 interfaces). The only change is the schedule:
- epoch `k` has horizon `H_k = H_0·2^k`;
- within an epoch, `gamma_k = min(1, sqrt(N ln N/((e − 1)·H_k)))` is constant;
- all rows restart at each epoch boundary;
- `H_0` is the larger of `⌈N ln N/(e − 1)⌉` and the router's learning-delivery bound
  `D` (§2.6), so each epoch is at least as long as its feedback takes to arrive.

These are epochs of the doubling trick, internal to the algorithm. They are not the
phases of §2.5.

**Proposition 2** (fixed menu, synchronous on-policy feedback). Apply Blum–Mansour
Theorem 11 within each epoch, with Auer et al. 2002, Corollary 3.2, giving
`R^MAB(H_k) ≤ 2.63·sqrt(H_k·N·ln N)`. Summing over doubling epochs:

    max_F E[swap regret_F] ≤ 8.98·N·sqrt(T·N·ln N) + (number of orphaned rounds)

The orphan term is at most `D·(log2(T/H_0) + 1)`.

This is exactly the published form. In Theorem 11, the quantity `B_{SR_MAB,F}` is an
expectation and the maximum over `F` is taken outside it (B&M p. 1317). The stronger
statement `E[max_F]` is **not claimed** (research item R1).

Two properties hold by construction:
- Every row keeps `q_ik ≥ gamma_k/N`. The matrix `Q` is therefore strictly positive and
  the stationary solve is unique; the reducible branch of `stationary_distribution`
  never fires on this path.
- Every master propensity is at least `gamma_k/N`. That bounds every importance weight,
  so the review's P0 example (propensity 1e-6 locking the master at (1, 0)) cannot
  happen on-policy.

**Measured** (Bernoulli game, N = 3, T = 64k): regret 2541 synchronous and 2791 with
delay 120, growing x2.5 per 4x horizon. That is worse than today's constant gamma at
this horizon (745). The constants of the doubling trick are poor; this is the price of
a cited guarantee (open question Q2).

### 2.3 The draw transforms: world-level limits outside the learner

`_cap_adversarial` keeps the adversarial minority within its share. §III.b calls it
"a constraint on routing", and rule 7 treats the adversarial layer as a population
constraint. `_mix_with_standing` is the consequence-sampling actuator. §IV.b says
overfitting is answered by raising the sampling rate (rule 10), and rule 6 says
evaluators are graded by realized consequence. Both are world facts about how a draw
is executed. Neither is part of the learner's strategy.

**Decision:** keep both transforms, and place them explicitly outside the learner.
- The learner learns its own policy `q`. Importance weights use the executed policy
  `pi`, logged truthfully.
- Coverage is guaranteed by the transforms themselves: `pi_k ≥ (1 − s)·share·q_k`. So
  the coverage ratio `kappa = max_k q_k/pi_k ≤ 1/((1 − s)·share)`, where the share
  factor applies only when an adversary is on the menu.
- **For the learner's policy q:** Proposition 1 holds with its variance term multiplied
  by `kappa`. Proposition 2 holds with row gains divided by `kappa`, which keeps Lemma
  10's `g ≤ 1`, and its regret multiplied by `kappa`.
- **For the executed policy pi:** regret equals the learner's regret plus
  `Σ_t (pi_t − q_t)·l_t`. That second term is the cost of the world constraint, and it
  is **not claimed small**.
- The executed policy is **not** mean-based on routers where `s > 0`.
- When no transform binds (`kappa = 1`, `pi = q`), both guarantees hold for the
  executed policy.

The runtime will ledger the per-draw total variation `TV(pi, q)` as a new field on
the draw's existing record, so the cost is measured rather than assumed. A PR-2 test asserts whether the edition-8
`Tick` core menu holds a forecast or adversarial seat, which decides whether `kappa = 1`
there. Moving the consequence mix out of the draw and into the reward channel is
research item R3.

### 2.4 Estimator and rare propensities

Logged propensities stay truthful in the request, the reward channel and the ledger.
`_floored` and `MIN_DECLARED_MASS` are deleted, together with their refusal and their
schematic text.

- **On-policy** uses the unbiased estimate `l/pi_k`. Its size is bounded by the
  learner's own exploration and the coverage above:
  - frontier: one update moves a logit by at most `eta_t·K·kappa/gamma_t ≤ kappa`;
  - core: Auer's estimate is at most `N/gamma_k`.
- **Off-policy (seat learners)** uses an IX estimate inside the learner (Neu 2015,
  Equation 3): `lhat = l/(pi_k + beta_t)`, with `beta_t = eta_t/2`. For a seat-learner
  core, the row update is `p_i·l/(pi_k + beta_t)`.
  - *Stability:* one update moves a logit by at most `eta_t/beta_t = 2`. The review's
    P0 example becomes a step of 2, not a lock.
  - *Bias, stated:* the expected estimate is `l_a·pi_a/(pi_a + beta_t)`. It is
    optimistic for actions the seat rarely takes, and the bias shrinks as `beta_t`
    falls.
  - *Coverage, stated:* an action the seat never takes (`pi_a = 0`) is never observed.
    Its estimate stays at zero, so the recommendation favours it. In the review's
    example the recommendation was `bad` 96% of the time. This is not a defect to
    hide: the learner has no evidence about such an action.
  - *What is claimed:* when the seat's behaviour covers the learner's policy
    (`max_a q_a/pi_a ≤ kappa` on every round), the recommendation's regret is the
    on-policy bound with variance multiplied by `kappa`, plus the IX bias
    `Σ_t beta_t·N`. **No regret is claimed without coverage.** The schematic states
    this as a fact.

### 2.5 Phases: only on menu changes; no reset on stable failure

**Menu phases.** A router's menu changes through a registration, a retirement or a
router proposal. These go through today's epoch path, which is already gated so that
feedback settles between control changes: `_epoch_due` allows at most one change per
`min_ratio` measured router periods (§IV.c). The new learner starts fresh: `t = 0` and
`L = 0`, or epoch 0 for the core. The old identity stays retained while its rounds are
outstanding or owed, as `_retain_router` does today, but **its rounds train nothing**.
They are orphaned and ledgered `learner.orphaned`, at most the outstanding count per
change. The carry path (`expand`, `reshaped`, `update_carried`, the carry branch of
`_apply_router_round`, and `step_rescaled`) is deleted.

**No learner reset on stable failure.** The immune organ's exploration ratchet
(`immune._gain`, `gamma()`, `seed_gamma`, `router_gamma`, `immune.gain_step` and
`immune.gamma_max`) is deleted. Stable failure keeps the existing duration price
ratchet (`immune.py:443-453`). §IV.b defines a loop's gain as "nothing less than
exactly λ", so that ratchet is the gain ratchet Chapter II asks for. Thrash keeps its
price.

**Charter and terms revisions** do not open phases in the run. Deciding which games a
revision affects is research item R4.

### 2.6 Delay and lifecycle

A router round's **learning delivery** ends at the latest of three points:
- its cutoff (`queue.deadline_tick`);
- for an owed credit, its due tick (`_defer_abstention`). That is the open tick plus
  the router's mean learned latency, and so no later than the cutoff;
- for an abstention or unscored round, the close of its origin price window
  (`_abstention_awaits_close`).

So `D ≤ cutoff + W_price`, in ticks, where `W_price` is the price loop's window. Both
are published in `world.clock.loops`.

A snapshot is removed when its round is learned, discarded, withdrawn (a quiet draw)
or orphaned. Live snapshots are therefore at most `(draws per tick)·(D + 1)` per
router. Live state per learner is that, times `O(N)` per snapshot, plus the current
epoch (`O(N)` for the frontier, `O(N²)` for the core), plus the sibling's issuance mark.
No closed epoch and no lifetime record is kept.

A delayed-feedback regret guarantee is **not proven here** for either class (research
item R2). The measured effect is small: about +9% for the frontier at delay 120.

### 2.7 Diagnostics computed at draw time

`_watch_abstention` records each draw's own threshold: the floor `gamma_t/K` and its
epoch.
- `uninvoked` means every draw gave NOOP at least `1 − gamma_t` of that same draw.
- `fresh_ratio_max` is computed against that draw's floor.
- Core routers are excluded from the frontier-floor diagnostics.

## 3. Research items (they do not block the run)

- **R1.** `E[max_F swap regret]` for the core: high-probability row bounds under
  Lemma-10-style off-proposal feedback.
- **R2.** Delayed-feedback guarantees for both classes. Under delay, the stationarity
  identity of the synchronous proof no longer holds between the current `Q` and the
  frozen `p_s`.
- **R3.** Executed-policy guarantees under the standing mix and the cap, or moving the
  consequence mix into the reward channel (rule 6).
- **R4.** Phases on charter and terms revisions for the affected games only.
- **R5.** Off-policy bounds expressed through measured coverage ratios.
- **R6.** An anytime core (decaying `gamma_t` rows without restarts). It is likely
  better in practice but has no published swap theorem yet.
- **R7.** Deleting `Hedge` and the full-information Blum–Mansour branch.

## 4. Migration

The release guard (`resume.py`, C4) refuses a checkpoint from another release, and the
renamed learner states also fail `restore_learner`. **Edition 8 starts fresh from v0.**
The only world-file edit is deleting `immune.gain_step` and `immune.gamma_max`
(`worlds/edition8-launch.toml:603-604` on `launch-world`), which the manifest will
refuse.

## 5. PR plan (two bounded PRs)

Each PR gets a cold review, the verify gate and soak.

**PR A: learners** (on `fix-memory-bounds`). About +300/−250 production lines and +350
test lines.
- Files: `factorylab/learners/{exp3,blum_mansour,delayed,base}.py` and
  `tests/learners/*`.
- Changes:
  - frontier: FTRL form with the half-exponent schedule;
  - core: doubling epochs, with `H_0` supplied by the caller;
  - snapshots tagged with their epoch, and orphaning;
  - `withdraw_for`;
  - an off-policy flag for the IX estimate;
  - delete `expand`, `reshaped` and `update_carried`.
- Tests written to fail first:
  - `test_frontier_regret_sqrt_growth`: from T = 2k to 32k, growth is at most 16^0.6.
    Today it is x13.9.
  - `test_core_epochs_swap_regret_within_theorem_11_bound`;
  - `test_frontier_is_mean_based_core_is_not_trapped`: at 16k, frontier swap/T is at
    least 0.12 and core swap/T is at most 0.02;
  - `test_off_policy_rare_propensity_step_bounded`: with propensity 1e-6, the logit
    moves by at most 2 and the master stays interior;
  - `test_checkpoint_continues_across_epoch_boundary`: bit-identical continuation,
    including orphans;
  - `test_withdrawn_draw_does_not_advance_t`.

**PR B: runtime** (on PR A and `fix-chapter2-p1`). About +250/−550.
- Files: `runtime/{routing,feedback,immune,propensity,compute,governance,bootstrap,loop,published,resume,worlds}.py`,
  `cortex/{schematics,registration}.py`, `docs/manifest.md` and `README.md`.
- Changes:
  - every router is keyed;
  - `H_0` is taken from `D`;
  - orphan in place of carry;
  - delete the floor;
  - delete `_gain` and `gamma()` (the price ratchet stays);
  - `gamma` is refused in proposals and `router_gamma` is removed;
  - draw-time diagnostics;
  - `TV(pi, q)` is ledgered;
  - the coverage statement goes into the schematic.
- Tests written to fail first:
  - the auditor's `test_runtime_learners_have_sublinear_fixed_menu_regret` (gate);
  - `test_truthful_rare_propensity_survives_learning_and_restore`;
  - `test_epoch_change_orphans_in_flight_rounds_across_resume` (gate), covering owed
    credits, an unclosed origin window, duplicate returns and quiet withdrawal;
  - `test_stable_failure_ratchets_price_not_learner`;
  - `test_manifest_refuses_gamma_keys`.

**This is the smallest safe set.** Each item fixes a false guarantee or a biased
record: linear regret, floored propensities, an exploration ratchet that rewrites
in-flight rounds, and weight carry without a theorem. Nothing optional is in it.

If the run must start before PR B, **do not launch with PR A alone**: the runtime
would still floor declarations and ratchet `gamma`. The fallback is to launch on the
current code with the known defects written into the run's record.

## 6. Experiments

Stdlib only; repo learners against prototypes; expected regret computed from the
policy; 4 seeds.

**Fixed menu, the auditor's game (`a = 1`, `b = 0`, `NOOP = 1`):**

| Learner | T = 1k | 4k | 16k | 26k | 64k |
|---|---|---|---|---|---|
| today, EXP3 (gamma = 0.1) | 44 | 144 | 544 | — | 2144 |
| today, SR_MAB | 65 | 167 | 567 | — | 2167 |
| **frontier, half exponent** | 28 | 49 | 91 | 114 | 175 |
| quarter exponent (revision 1) | 80 | 224 | 633 | 911 | 1789 |
| **core, doubling epochs** | 157 | 369 | 793 | — | 1659 |

**Bernoulli game (0.6 / 0.4 / 0.5), T = 64k:**

| Learner | Synchronous | Delay 120 |
|---|---|---|
| frontier | 269 | 292 |
| core, doubling | 2541 | 2791 (`H_0 = D`) |
| today, EXP3 | 689 | 714 |
| today, SR_MAB | 745 | 760 |

**Trap** (Deng, Schneider and Sivan; a mean-based learner gets about 3/16), swap
regret / T at 64k:

| Learner | Swap regret / T |
|---|---|
| frontier, half exponent | 0.170 |
| today's EXP3 | 0.172 |
| core, doubling SR_MAB | 0.005 (0.003 at 16k) |
| today's SR_MAB | 0.024 |

**Off-policy.** The seat declares hold 0.01 / order 0.99, and order pays 0.5. The
table gives the recommendation's p(hold) at 50k rounds:

| Hold pays | Floored (today) | IX | Unbiased |
|---|---|---|---|
| 0.9 | 0.10 | 0.97 | 0.97 |
| 0.6 | 0.10 | 0.97 | 0.97 |
| 0.3 | 0.10 | 0.97 | 0.03 |

IX's optimism toward the rare action is the bias stated in §2.4. It is kept for its
bounded step, which matches the owner's ruling.

**Memory:** live state is bounded by outstanding rounds, at 15.8 KB with 119
outstanding after 50k rounds.

## 7. Open questions for the owner

1. **Ship the half-exponent frontier?** Recommended: yes. It is mean-based, with
   `O(sqrt(T))` regret and the best measured numbers.
2. **Is the doubling core's poor constant acceptable?** It costs 2541 against today's
   745 at 64k, but carries a cited theorem. Recommended: yes for the run. The anytime
   core (R6) comes after, once proven.
3. **Keep the consequence mix and the adversarial cap as world limits, with the
   executed-policy cost ledgered rather than bounded?** Recommended: yes for the run.
   R3 comes after.
4. **Refuse the `gamma` proposal fields and the two immune keys?** Recommended: yes.
