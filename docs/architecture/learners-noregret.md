# Learners that are no-regret: design for the edition-8 redesign (revision 4)

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

**Revision 3** applies the round-2 review of `9b52245e` (APPROVE WITH CHANGES) and the
owner's ruling on the core:
- a fixed coverage bound `kappa` for each phase, and the swap-regret cost of the
  transforms (§2.3);
- the IX bias stated with coverage, with off-policy guarantees moved to research
  (§2.4, R5);
- an operational delivery bound (§2.6) and a settle gate (§2.5);
- the startup horizon of Proposition 2 (§2.2);
- what the run does not demonstrate (§2.8).

**Revision 4** applies GPT-Sol 6.1's cold review of #189/#190 (BLOCK, five P1s) and the
senior advisor's memo on menu growth, whose recommendation the integrator adopted:
- a menu grows in place; only a population router replacement, or a seat that would
  raise a core menu's `kappa`, opens a phase (§2.5). Under revision 3 a founded seat
  waited the settle gate, 2,400 ticks on edition 8, while its novelty trial expired
  after about 360 (P1 1);
- a replacement waits the settle gate too, kept across a checkpoint (P1 2);
- the delivery bound is the queue's own cutoff stretched by the deadline formula,
  through one shared function (P1 3, §2.6);
- withdrawing a quiet draw that rolled a core epoch restores the epoch (P1 4, §2.2);
- a population `blum_mansour` router needs a per-tick kind, as the core key does (P1 5).

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

**Proposition 1** (fixed menu, synchronous on-policy feedback, oblivious losses, the
learner's own policy `q`, with the estimate `l/q_k`; with the transforms of §2.3, the
variance term is multiplied by the fixed `kappa`):

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

**A growing menu** (§2.5). A new arm's cumulative loss is set so that its weight at the
pre-growth rate, `exp(−eta·L_new)`, is the mean weight of the arms already on the menu.
`N` then grows, so `eta_t = gamma_t/N_t` stays nonincreasing (all the proof above
needs), and at the next, lower rate the new arm's weight is no longer exactly the mean.
The round count is not reset.

**Measured.**
- At T = 26k: regret 114, against 911 for the quarter exponent.
- Growth per 4x horizon: x1.54 (deterministic game) and x1.24 (Bernoulli game).
- In the trap the learner stays trapped (swap regret / T = 0.170), as a mean-based
  learner should.
- Delay of 120 rounds: 292, against 269 without delay.

### 2.2 Core: the published Blum–Mansour bandit reduction, with doubling epochs

**Owner ruling:** the core keeps doubling epochs, with the first epoch at least as long
as the delay. A single fixed-horizon epoch would need a hard cap on draws, which is an
architect constant that Chapter II argues against. The measured cost of about 3.4x
regret (§6) is accepted for this existence run.

The core is SR_MAB as it is today (Blum and Mansour 2007, §5), with Auer EXP3 rows that
satisfy Lemma 10. The only change is the schedule:
- epoch `k` has horizon `H_k = H_0·2^k` draws;
- within an epoch, `gamma_k = min(1, sqrt(N ln N/((e − 1)·H_k)))` is constant;
- every row restarts at each epoch boundary.

`H_0` is the larger of `⌈N ln N/(e − 1)⌉` and the router's delivery bound converted
into draws (§2.6):

    H_0 ≥ r · L,   L = (1 + min_ratio) · c(h),   c(h) = h + ⌈h/min_ratio⌉

Here `r` is the kind's per-tick draw bound, `h` is the longest decision horizon at
genesis, and `c(h)` is the cutoff the queue actually sets for it. On `Tick`, `r = 1`:
one Tick event per tick, and Tick rounds are not judged again. At load, the kernel
refuses a `no_swap_regret_kinds` entry that has no per-tick draw bound, and at
admission it refuses a population `blum_mansour` router for such a kind (revision 4;
Sol's probe opened two rounds of a `ProducerReturn` core in one tick). These epochs
belong to the doubling trick; they are not the phases of §2.5.

**A growing menu** (§2.5; revision 4, after Sol's review of #191). Each row holds
cumulative gain estimates `Ĝ_i`, adds `X_ik = p_i·r/(kappa·pi_k)` per round, and
proposes `q_i ∝ exp(eta_t·Ĝ_i)` mixed with `gamma_k/K` at the rate
`eta_t = gamma_k/N_t`, where `N_t` is the menu's size at the draw. `gamma_k`, the epoch's
exploration mass, stays frozen at the `N` the epoch opened with. The rate reads the
menu now, so it is **nonincreasing within the epoch**, and every update keeps Auer's
premise: `q_ik ≥ gamma_k/K_t` gives `X_ik ≤ K_t/gamma_k`, so
`eta_t·X_ik ≤ K_t/N_t ≤ 1`. (At `ca655ca4` the step was frozen at `gamma_k/N_0`
while the floor used the grown `K`; Sol's probe reached an exponent of 1.614.)
- A new action gets a uniform new row and, in every old row, a gain estimate at that
  row's mean weight at the pre-growth rate.
- Growth from a singleton epoch (`N = 1`, `gamma = 0`, every draw NOOP, nothing
  learned) restarts that epoch at the grown `N`, under the same epoch index. Sol's
  probe had frozen `gamma = 0` into a snapshot no restore accepted.
- The next epoch retunes `gamma` to the grown `N`.

Within a grown epoch, each row is EXP3 in FTRL form with a nonincreasing rate. Its regret
against any arm `j` from `j`'s arrival is at most
`ln N_T/eta_T + (e − 2)·Σ_t eta_t·E[Σ_a q_a·X_a²] ≤ N_T ln N_T/gamma_k + (e − 2)·gamma_k·H_k`,
which is Auer's bound with `N_T` in place of `N`. The time-varying rate is published in
loss form (Cesa-Bianchi and Lugosi 2006, Theorem 2.3). **The gain form under
`eta_t·X ≤ 1` and the mean-weight entry term `ln(N_T/N_0) ≤ ln N_T` are our own
argument.** Lemma 10 and Theorem 11 then apply per epoch unchanged, and Lemma 10's
constant loosens by at most `sqrt(N_T ln N_T/(N_0 ln N_0))`. The price is the step: a
grown epoch learns at `N_0/N_T` of its pre-growth rate until the next boundary, which
is 1.5x slower for one Tick seat on edition 8. Without growth the policy is the one
before this change (to 1e-12).

**A quiet draw** is withdrawn exactly. When the draw that rolled an epoch is withdrawn
while it is the new epoch's only round, the closed epoch's position and rows are
restored, so its outstanding rounds still train (Sol's `H_0 = 3` probe moved the
position from `(0, 3)` to `(1, 0)` and orphaned them).

**Compressed snapshots.** The Lemma-10 row update uses
`X_ik = g_ik/q_ik = p_i · r/pi_k`; the row proposal `q_ik` cancels. A core snapshot
therefore stores only `{epoch, p, pi}`, which is `O(N)`. Today's snapshot stores every
row, which is `O(N²)`.

**Proposition 2** (fixed menu, synchronous feedback, the learner's own master policy
`p`, fixed coverage `kappa` from §2.3).
- **For T ≥ H_0:** apply Theorem 11 within each epoch, with row gains divided by
  `kappa`, and Auer's `R^MAB(H) ≤ 2.63·sqrt(H·N·ln N)`. Summing over the doubling
  epochs gives

      max_F E[swap regret_F] ≤ 8.98·kappa·N·sqrt(T·N·ln N)

- **For T < H_0:** the first epoch is cut short. Lemma 10 then gives each row a regret
  of at most `2·gamma_0·T + N ln N/gamma_0`. The bound is
  `kappa·N·(2·gamma_0·T + N ln N/gamma_0)`, which can exceed `T`. **No sublinear claim
  is made before `H_0`.**

This is the published form. `B_{SR_MAB,F}` is an expectation, and the maximum over `F`
sits outside it (B&M p. 1317). `E[max_F]` is **not claimed** (R1). Rounds orphaned at
epoch boundaries are an accounting cost, not a delayed-feedback theorem (R2).

Two properties hold by construction:
- Every row keeps `q_ik ≥ gamma_k/N`. The matrix `Q` is therefore strictly positive and
  the stationary solve is unique.
- Every master propensity is at least `gamma_k/N` before any transform, so the
  review's 1e-6 lock cannot occur on-policy.

**Measured** (Bernoulli game, N = 3, 64k draws): 2541 synchronous and 2791 at delay
120, against 745 for today's constant gamma. That is 3.41x.

### 2.3 The draw transforms: world-level limits outside the learner

`_cap_adversarial` keeps the adversarial minority within its share. §III.b calls it "a
constraint on routing", and rule 7 treats the adversarial layer as a population
constraint. `_mix_with_standing` is the consequence-sampling actuator: §IV.b answers
overfitting by raising the sampling rate (rule 10), and rule 6 grades evaluators by
realized consequence. Both are world facts about how a draw is executed. **Both stay,
explicitly outside the learner.** The learner learns its own policy (`q`, or the master
`p` for the core). Importance weights use the truthfully logged executed policy `pi`.

**A fixed, phase-wide coverage bound.** For a menu in force through a phase:

    kappa = 1 / ( (1 − s_cap if a forecast-shaped evaluator is on the menu, else 1)
                · (share if an adversary is on the menu, else 1) )

Here `s_cap = evaluation.sampling_cap` and `share = evaluation.adversarial_share`, both
fixed for the world's life. The transforms guarantee `pi_k ≥ q_k/kappa` on every draw.
Each step multiplies an arm by at least `(1 − s)` or `share`. `kappa` is constant within
a phase, so dividing Lemma-10 gains by it scales the game itself, not time-weighted
rewards. A per-round `kappa_t` is not used.

**When `adversarial_share = 0`:** adversarial seats can never be woken, so they are
infeasible on every router. They sit outside the learner's support and outside its
comparator class.

**The executed policy pi:**
- External regret is the learner's regret plus `Σ_t (pi_t − q_t)·l_t`, which is at
  most `Σ_t TV(pi_t, q_t)`.
- For a fixed swap mapping `F`, swap regret is the learner's regret plus
  `Σ_t Σ_a (pi_t,a − p_t,a)·(l_t,a − l_t,F(a))`, which is at most
  `2·Σ_t TV(pi_t, p_t)`. On two actions with `q = (.5, .5)`, `pi = (.75, .25)`, losses
  `(1, 0)` and the exchange mapping, this term is 0.5.
- These terms are the cost of the world constraint. They are ledgered per draw as
  `TV(pi, q)` and **not claimed small**.
- The executed policy is not mean-based where `s > 0`.

**Edition 8:** `antagonist-core` accepts `Tick` on `launch-world`. The core's menu
therefore holds an adversary from genesis, with `kappa = 1/0.15 ≈ 6.67`, and the cap
holds the executed antagonist mass at 0.15 or less. **Recorded for the run:** dividing
every row gain by `kappa` means the core learns its menu at about `1/kappa` (one
seventh) of the speed it would without the adversary. The advisor's measurements put
nearly all of the core's 5.3x regret over the old constant-gamma core on this factor
(4.5x of it), not on doubling. A tighter fixed constant does not exist, since
`pi_adv ≥ min(p_adv, share)` leaves the worst case at `1/share`. This is accepted for
the run and not retuned; a core that needs no transform is research (R3).

### 2.4 Estimator and rare propensities

Logged propensities stay truthful in the request, the reward channel and the ledger.
`_floored`, `MIN_DECLARED_MASS`, the floor refusal and its schematic text are deleted.

**On-policy.** The estimate is `l/pi_k`. Its size is bounded by the learner's own
exploration and `kappa`:
- frontier: one update moves a logit by at most `kappa`;
- core: `X_ik` is at most `kappa·N/gamma_k`, before the `1/kappa` gain scaling.

**Off-policy (seat learners).** The update is IX inside the learner (Neu 2015,
Equation 3).
- **Frontier rule:** `lhat = l/(pi_k + beta_t)`, with `beta_t = eta_t/2`.
- **Core rule:** Auer rows in doubling epochs, with `Xtilde_ik = p_i·r/(pi_k + beta_k)`
  and `beta_k = gamma_k/(2N)`.
- **Stability:** one update moves a logit by at most `eta/beta = 2`.
- **Bias:** the expected frontier loss estimate is `l_a·pi_a/(pi_a + beta)`. That is
  optimistic for actions the seat rarely takes. The weighted bias
  `Σ_a q_a·l_a·beta/(pi_a + beta)` is at most `kappa·N·beta` under coverage `kappa`,
  and is unbounded without coverage. The core rule's gain estimate is pessimistic by
  the same factor `pi/(pi + beta)`.
- **Coverage:** an action the seat never takes is never observed, and its estimate
  stays at its prior. In the review's example, the recommendation was `bad` 96% of the
  time.
- **No regret guarantee is claimed for off-policy learners** (R5). The core rule in
  particular does not inherit Auer's gain-form theorem. The schematic states this as a
  fact.

### 2.5 Phases: only on replacement or a raised core kappa; no reset on stable failure

- **Growth is in place.** A registration that adds a seat to a router's menu adds an arm
  to the live learner, with the initialisation of §2.1 (frontier) or §2.2 (core). It
  opens no phase, orphans nothing, changes no identity and waits for no gate. It is
  ledgered `router.grown`, with the router, the seats added, the grown menu, its
  `kappa` and the event ordinal. A founded seat is therefore drawable on the next event
  of its kind, inside its novelty trial: §IV.b requires the compensation period to be
  shorter than the lifetime of what it compensates. In-flight rounds are safe because a
  snapshot's support is a subset of the grown menu, the frontier's update touches only
  the drawn arm, and a core round gives the new row zero gain (`p.get(action, 0)`).
- **Why it is no-regret.** In full information this is Hedge with experts that arrive
  over time: Mourtada and Maillard (2017), "Efficient tracking of a growing number of
  experts" (ALT 2017, PMLR 76:517–539; arXiv 1708.09811). An expert that enters at the
  mean weight raises the potential by at most `ln(1 + 1/N)`, so regret against each
  expert counts from its arrival, by the same potential argument. **The bandit step is
  our own argument, measured and not published**, as §2.1's anytime step is ours over
  Braverman et al. Regret against an arm `j` over `[s_j, T]` is Proposition 1's bound
  with `N_T` in place of `N`, plus the variance term, which exploration `gamma_t/K` over
  the *current* menu already covers. For the core, the potential step holds within
  each row at the nonincreasing rate `gamma_k/N_t`, and the loosening of §2.2 applies.
  These are statements for an oblivious adversary, with the new arm's regret counted
  from its arrival and the growth times fixed in advance. The frontier's mean-based
  property (§2.1) is argued for a fixed menu; with arms arriving, it holds against
  each arm from its arrival, under the same slack.
- **Measured** (advisor's memo; the repo's learners; arms arriving at rounds 100, 3,000
  and 10,000; 4 seeds; T = 32k). The frontier's regret was 952 when it grew in place,
  1,629 when it restarted at once, and 2,093 when it restarted behind revision 3's gate.
  The gated restart put no mass at all on a new arm in its first 200 rounds. For the
  core (`H_0 = 800`, an arm arriving at round 2,000, T = 16k), regret from arrival was
  668 in place against 797 for a restart. For both classes, the test
  `test_regret_with_an_arm_added_mid_run_is_sublinear_from_its_arrival` checks that
  regret from an arm's arrival grows like `sqrt` and stays inside the class's
  fixed-menu bound at the grown `N`.
- **What opens a phase.** A phase is a fresh learner of the same class under a new
  identity, with no weight carried. Two changes open one:
  - a population router replacement (`add = false` for a kind that has routers);
  - a seat whose arrival would raise a core menu's `kappa`, since the core divides every
    gain by a `kappa` fixed for its life (§2.3). On edition 8 only a forecast-shaped
    seat accepting `Tick` can do that. A seat that keeps `kappa` still grows the core in
    place while such a phase waits. A frontier's `kappa` is the grown menu's.

  The old identity is retained while it has outstanding or owed rounds, as
  `_retain_router` does, but those rounds train nothing: they are `learner.orphaned`.
  A **retirement** opens no phase: the retired arm becomes infeasible within the
  current phase, and nothing is orphaned. An added router (`add = true`) opens no phase
  either, since it orphans nothing.
- **The settle gate.** `_epoch_due` uses a measured period, so it does **not**
  guarantee that feedback settles between changes. A phase-opening change is therefore
  deferred until `min_ratio·L` ticks have passed since the kind's last phase opened.
  Genesis opens the first phase. Here `L` is the delivery bound of §2.6, and this is
  §IV.c's cascade ratio, with the router loop as the outer loop over delivery. A waiting
  replacement is ledgered `router.deferred` and kept across a checkpoint
  (`pending_routers`). A later replacement of the same kind supersedes it. The phase it
  opens is stamped with the tick it is built (Sol's probe: replacements at ticks 1, 2
  and 3 all took effect and orphaned every round). A waiting coverage phase is
  `epoch.deferred`. Under the most churn the gate permits, the rounds drawn in the first
  `1 − 1/min_ratio` (at least 2/3) of each phase are delivered inside it, provided the
  draw rate is uniform and nothing expires.
- **Carry is deleted:** `expand`, `reshaped`, `update_carried`, the carry branch of
  `_apply_router_round`, and `step_rescaled`. Growth in place is not carry. Carry moved
  weights across a change no theorem spanned. Growth keeps one learner whose potential
  argument spans the arrival.
- **No learner reset on stable failure.** The exploration ratchet is deleted:
  `immune._gain`, `gamma()`, `seed_gamma`, `router_gamma`, `immune.gain_step` and
  `immune.gamma_max`. Stable failure keeps the duration price ratchet
  (`immune.py:443-453`); §IV.b defines a loop's gain as "nothing less than exactly λ".
  Thrash keeps its price.
- **Charter and terms revisions** open no phase during the run (R4).

### 2.6 Delay and lifecycle: an operational delivery bound

The runtime enforces a deadline for every router snapshot. A round opened at tick `o`
with horizon `h` is cut off by the queue at `o + c(h)`, `c(h) = h + ⌈h/min_ratio⌉`, and
must be learned, discarded, withdrawn, orphaned or expired by

    o + (1 + min_ratio)·c(h)

The router-wide **delivery bound** `L` is the same formula at the longest horizon any
routed decision can carry. Both come from one function (`clockwork.delivery_ticks`
over the queue's `clockwork.deadline_ticks`), so `L` is never below a round's own
delivery. Revision 3 read `L = (1 + min_ratio)·h` without the cutoff's slack. Sol's
edition-8 probes measured an Exposure round with horizon 160, cutoff 214 and delivery
856, and a Forecast round with horizon 200, cutoff 267 and delivery 1,068, while the
bound and `H_0` said 800. The bound is still router-wide; bounds per kind can wait,
since with growth in place a longer bound no longer delays a founded seat.

Two code paths do not meet this today, and PR B changes both:
- **Owed abstention credits.** `_defer_abstention` can set a due tick beyond the
  round's cutoff. The review's probe had open tick 100, cutoff 110 and mean latency
  100, which gave due tick 200. The due tick is clamped to the round's own cutoff:
  `min(o + ⌈mean latency⌉, o + h)`.
- **Window closure.** A price window can outlast its period: when its measured inner
  period grows, closure is postponed (the review's probe closed at tick 300). A round
  still awaiting its origin window at `o + L` **expires untrained**, ledgered
  `learner.expired` with the window's state. §IV.c's ratio is the justification: a
  window still open after `min_ratio` of the round's own horizon has broken the
  cascade ratio the kernel declares.

**From ticks to draws.** With a per-tick draw bound `r` (§2.2), live snapshots per
router are at most `r·(L + 1)`, each `O(N)` (core included, after compression). Live
state per learner is that, plus the current epoch (`O(N)` frontier, `O(N²)` core rows),
plus the sibling's issuance mark. No closed epoch and no lifetime record is kept.

**Tests written to fail first** (PR B):
- `test_owed_credit_due_never_exceeds_cutoff`, using the probe values;
- `test_window_outliving_bound_expires_round`, using the probe values;
- `test_permitted_menu_churn_keeps_learning`: a seat joins every tick; revision 4
  asserts no phase opens, nothing is orphaned or expired, and the learned fraction is 1
  (revision 3 asserted at least `1 − 1/min_ratio − 0.05` across gated phases);
- `test_snapshot_count_bounded_by_r_times_L`;
- checkpoint and resume across a phase opening, covering owed credits, an unclosed
  window, simultaneous causes, duplicate returns and a quiet-draw withdrawal.

**A delayed-feedback regret guarantee is not proven** for either class (R2). Measured:
the frontier is about 9% higher at delay 120.

### 2.7 Diagnostics computed at draw time

`_watch_abstention` records each draw's own threshold: the floor `gamma_t/K` and the
phase or epoch.
- `uninvoked` means every draw gave NOOP at least `1 − gamma_t` of that draw.
- `fresh_ratio_max` is computed against the same draw-time floor.
- Core routers are excluded from the frontier-floor diagnostics.

### 2.8 What the 72-hour run does and does not demonstrate

Propositions 1 and 2 are synchronous, fixed-menu guarantees **for the learners' own
policies**. A grown menu has the growing-experts bound of §2.5, whose bandit step is our
own measured argument. On edition 8 the `Tick` core learns at about `1/kappa` speed,
with `kappa ≈ 6.67`, because the adversary sits on its menu (§2.3). This is accepted for
the run. The run's routers are delayed, and on the edition-8 `Tick` core and the
evaluator routers they are transformed. **The run does not demonstrate no-regret for
delayed or transformed policies**, and it demonstrates none for off-policy seat
learners.

The run removes the known false guarantees: linear regret by construction, rewritten
propensities, and an exploration ratchet that rewrote rounds in flight. It also bounds
delivery and memory operationally, and ledgers the transform cost and every orphaned
or expired round.

## 3. Research items (they do not block the run)

- **R1.** `E[max_F swap regret]` for the core, which needs high-probability row bounds
  under Lemma-10-style off-proposal feedback.
- **R2.** Delayed-feedback guarantees for both classes. The frozen stationarity
  `p_s·Q_s = p_s` remains true. What is missing is a result for **delayed row
  learning**: rows that learn round `s` after later rounds have moved them.
- **R3.** Executed-policy guarantees under the standing mix and the cap, or moving the
  consequence mix into the reward channel (rule 6).
- **R4.** Phases on charter and terms revisions, for the affected games only.
- **R5.** Off-policy guarantees: frontier IX under measured coverage (bias at most
  `kappa·N·beta`), and a cited algorithm for the off-policy core.
- **R6.** A horizon-free core with better constants. We have not established a
  compatible cited replacement.
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
  - core: doubling epochs, with `H_0` supplied by the caller and Lemma-10 gains divided
    by a fixed `kappa`;
  - compressed core snapshots `{epoch, p, pi}`;
  - snapshots tagged with their epoch or phase, and orphaning;
  - `withdraw_for`;
  - the off-policy IX rules for the frontier and the core (§2.4);
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
  - `test_withdrawn_draw_does_not_advance_t`;
  - `test_core_snapshot_is_order_n_and_trains_identically`.

**PR B: runtime** (on PR A and `fix-chapter2-p1`). About +250/−550.
- Files: `runtime/{routing,feedback,immune,propensity,compute,governance,bootstrap,loop,published,resume,worlds}.py`,
  `cortex/{schematics,registration}.py`, `docs/manifest.md` and `README.md`.
- Changes:
  - every router is keyed;
  - `H_0 = max(⌈N ln N/(e − 1)⌉, r·(1 + min_ratio)·h)`;
  - the load refuses a core kind that has no per-tick draw bound;
  - the delivery deadline `o + L`: the owed-credit due tick is clamped to the cutoff,
    and a round past the deadline expires as `learner.expired`;
  - the settle gate for phase-opening changes;
  - a retirement becomes infeasibility within the phase;
  - adversarial seats are infeasible when `adversarial_share = 0`;
  - the phase-wide `kappa`;
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
    credits, an unclosed origin window, simultaneous causes, duplicate returns and
    quiet withdrawal;
  - `test_owed_credit_due_never_exceeds_cutoff`;
  - `test_window_outliving_bound_expires_round`;
  - `test_permitted_menu_churn_keeps_learning`;
  - `test_snapshot_count_bounded_by_r_times_L`;
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

**Off-policy** (half-exponent frontier, 20 seeds). The seat declares hold 0.01 / order
0.99, and order pays 0.5. The table gives the recommendation's median p(hold) at 50k
rounds, with the value at 1k in brackets:

| Hold pays | Floored (today) | IX | Unbiased |
|---|---|---|---|
| 0.9 | 0.10 [0.10] | 0.998 [0.983] | 0.998 [0.982] |
| 0.6 | 0.10 [0.10] | 0.998 [0.979] | 0.998 [0.841] |
| 0.3 | 0.10 [0.10] | 0.002 [0.945] | 0.002 [0.059] |

The review measured 0.00453 for IX on the hold-0.3 case, against 0.9666 under the
quarter exponent of revision 1. With the half exponent, IX's optimism toward the rare
action is transient: it is wrong at 1k (0.945) and right by 50k. That is the bias
stated in §2.4. IX is kept for its bounded step, as the owner ruled. Every row covers
the same declared behaviour, so these figures say nothing about uncovered actions.

**Memory:** live state is bounded by outstanding rounds, at 15.8 KB with 119
outstanding after 50k rounds.

## 7. Resolved, and remaining questions for the owner

**Resolved:**
- the half-exponent frontier, which the reviewer approved;
- the doubling core, accepted at about 3.4x by the owner's ruling;
- the transforms kept as world limits, with their cost ledgered rather than bounded;
- refusing the `gamma` proposal fields and the two immune keys, which the reviewer
  agreed to.

**Remaining:**
1. **Should a core kind be required to have a per-tick draw bound at load?** This is
   needed to convert the delivery bound from ticks into draws. Recommended: yes. Only
   `Tick` is a core kind today, and it has `r = 1`.
2. **Should a round still awaiting its origin price window at `o + L` expire
   untrained, rather than wait unbounded?** Recommended: yes, ledgered and counted. An
   unbounded wait is the memory defect of #1 in another form.
3. **Should a retirement remove the arm from the feasible menu, with no new phase?**
   Recommended: yes. Nothing is orphaned, and the retired arm is never drawn again.
