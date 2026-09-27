# Audit: Chapter II §II Versioning (ch2.md 125–180) against FactoryLab `fast-loop-harness`

Auditor slice: what a version is, commitment and persistence, the first commit, pathology detection (the four convergence pathologies), priced penalties with self-adjusting λ, pricing the duration of stable failure, the kill switch and the hard world, the novelty reserve and guaranteed patience (ch2 line 290).

Scope read: `factorylab/versioning/*`, `runtime/immune.py`, `continuity.py`, `resume.py`, `release.py`, `pricing.py`, `cards.py`, `charter/controller.py`, `charter/windows.py`, `kernel/termination.py`, `kernel/reserve.py`, the novelty paths in `routing.py`/`feedback.py`/`compute.py`, retirement (`governance.py`, `routing.py`), and the hash shims in `runtime/worlds.py`.

Empirical probe: I ran the scripted fast harness (`edition5-testnet-rehearsal`, 120 ticks, seed 1) to `scratchpad/audit/runs-versioning/`. Then I decrypted that run's own diary offline with `versioning.reader` (probe scripts `scratchpad/audit/probe*.py`). I did not read any operator key.

## Verdict in one paragraph

The versioning core is a **behavioural** notion. Cells are built from judge and card profiles, versions are spans that do not increment, and a charter edition starts a new version. The kernel's commitment physics also holds: a manifest or release mismatch refuses resume, a killed identity cannot be resurrected from a backup, and there is no rewind path. So no software-release versioning was smuggled into the *version* concept itself. The problems sit in four places:

1. **Versioning is post-mortem only.** The transfer operator and spectral gap, which ch2 names as the detection instrument, drive no live decision.
2. **Two of the four pathology responses are inert or inverted.** The thrash response softens penalties instead of pricing volatility. The learning-death response is consumed by seed assemblies, which it cannot help.
3. **The learning-death detector is keyed to card compliance, not population dynamics.** Its "causal" access evidence is also wrong in every window because of an ordering bug.
4. **Class-1 identity engineering remains.** Twenty-eight hash-neutral manifest shims keep old identities alive across kernel-semantics changes, and git HEAD is part of the release identity.

The recent `router.learning_death` entry is observation-only, and nothing reads it. It disagreed with the immune flag in the probe run: 0 entries against 4. The PID λ is faithful to Stooke et al. and to ch2, but it is opt-in, and the unprescribed integral law is still the default.

## Probe evidence (scripted run, 19 immune windows)

- `pathology.learning_death` fired in windows 3–6. It fired only because the single card `censorship-bound` was still unmeasured (`None`), so `holding` was false. From window 9 to window 19 the tail had the same cell, 0 registrations, 0 revisions and a flat outcome, and no learning death was diagnosed, because the one card was compliant.
- Every one of the 19 `immune.window` entries reports `registration_route` lost with the reason "the novelty reserve window has nothing left". The matching `novelty.window` entries show about $12 of the ~$12 share **expired unspent** (for example `amount 11999997, expired 11999996`).
- There were 4 `novelty.grant` entries and 14 `novelty.grant_consumed` entries. All 14 consumers (`mechanism`, `empirical`, `antagonist`, `opportunity`) have `provenance: "seed"`, and for seeds `_unhistoried` ignores the grant.
- `router.learning_death`: 0 entries. `immune.gain`, `immune.price_ratchet`, `immune.decay`: 0 entries.
- The offline report gives 6 versions across 19 windows. Four of them (windows 6, 7, 8 and 9) last one window each, caused by the card moving from unsupported (-1) to measured. The global Dobrushin gap bound is 0.167 and `durable=False`, even though the last 9 windows are identical.
- The fast-loop scorecard reports none of this: no pathology or immune field.

---

## Findings

Severity: H = high, M = medium, L = low.

### CONTRADICTS

**C1. Access evidence for learning death is false in every window (reserve read after it expires). Severity H.**
- Location: `factorylab/runtime/immune.py:110-117`, `factorylab/kernel/reserve.py:77-83,142-144`, `factorylab/runtime/pricing.py:226-235`.
- ch2 (156): "learning death is a property of the factory's population dynamics".
- Evidence: `close_window` runs inside `_close_price_window`. That happens only once `now >= reserve_window_start + window_ns`, before `reserve.open_window`. At that moment `_active()` is false, so `reserve.remaining()` returns 0 unconditionally. `registration_route` is therefore always "lost", and `revision_route` is lost by cascade. The probe confirms it: 19 of 19 windows report the loss while the reserve expires almost untouched. The wake and the diary publish a false causal diagnosis.
- Action: FIX. Sample `remaining` while the window is still active (for example at the last event before the boundary), or read the closing window's `expired` figure from the `novelty.window` accounting.

**C2. Thrash is not priced by duration; the response softens penalties. Severity H.**
- Location: `factorylab/runtime/immune.py:184-187`, `immune.py:55-58`, `ImmunePriceController.set_decay` `immune.py:11-26`, `pricing.py:457`, `worlds.py:404`.
- ch2 (175): "penalize the duration of spectral-gap volatility".
- Evidence: on thrash the organ does two things.
  - `_gain(rt,"thrash")` lowers γ, but only as far as `seed_gamma`: `min(old, max(seed, old-step))`. If stable failure never raised γ first, nothing changes.
  - `set_decay(decay + decay_step)` for one window. That makes non-violating cards' prices leak faster, which lowers penalties.

  No volatility cost, duration counter, λ or reward subtraction exists for thrash. The core is not "incentivized to stabilize"; it is let off.
- Action: BUILD thrash pricing. Make a spectral-gap-volatility cost (see C3) a priced term with its own PID λ whose integral accumulates the duration of volatility, subtracted from the rewards of the no-swap-regret core. Then DELETE `ImmunePriceController`, `set_decay`, `immune.decay`, `decay_step` and the `pricing.py:457` reset.
- Blast radius: all 22 worlds carry `decay_step`, plus `wake._on_immune_decay`, the tests in `tests/runtime/test_immune*`, and `docs/manifest.md`.

**C3. The transfer operator and spectral gap drive no live detection. Severity M-H.**
- Location: `factorylab/versioning/versions.py:93-140` (`diagnose`), `immune.py:169-171`.
- ch2 (158): "A thrashing factory is easy to detect, since its spectral gap is continuously unsettled." ch2 (152): stable failure is "a robust version with a wide spectral gap" that fails.
- Evidence:
  - The live predicates are proxies. Stable failure means *identical cells in all k=3 tail windows* plus a card violated in all of them. Thrash means a cell change in each of the last k windows plus a violation in every window. The docstring rejects the essay's instrument outright: "rather than volatility in a moving histogram partition".
  - `gap_bound` is computed over at most 3 cells and 2 transitions, which is degenerate. It is ledgered and read by nothing.
  - A failing attractor that cycles between two failing cells with a wide gap is never diagnosed as stable failure.
- Action: FIX. Maintain a rolling live operator over a horizon of several k. Diagnose stable failure as wide gap plus failing, and thrash as a volatile gap series. That gap-volatility series is the cost C2 needs.

### MISSING

**M1. Versioning exists only as a post-mortem of a dead diary. Severity H.**
- Location: `factorylab/versioning/__init__.py:1-7`, `runtime/cli.py:709-720` (`factorylab versions` requires the released key), `versioning/versions.py:300-343` (`settling`), `runtime/cadence.py`.
- ch2 (138): "Versioning the superdark factory is an active process". Also that version changes "are detected by observers" who are "part of the ... walls".
- Evidence:
  - No live object says which version the factory is in, how long it has lasted, or whether it is durable.
  - `versions()`, the global operator and `settling()` run only offline.
  - `immune.tv_threshold` and `immune.gap_threshold` are launch casts that no live code reads (`docs/manifest.md` says so).
  - Governance cadence times amendments from consequence latency, not from version settling time (ch2 IV.c, line 304, derives the charter-revision minimum from settling).
  - The package docstring "Nothing here runs while a world is alive" is also false: `diagnose`, `cell_series` and `slope` run live in `immune.py`, `wake.py:670` and `feedback.py:1568`.
- Action: BUILD live versioning: a rolling operator, version boundaries, a per-version gap and a settling time. Feed it to the immune organ (C3) and to governance cadence. Keep `factorylab versions` as the forensic reader.

**M2. No per-version spectral gap. Severity L-M.**
- Location: `versioning/report.py:423-426`.
- ch2 (142): "If near-invariant regions are cleanly partitioned ... a given version is durable".
- Evidence: `durable` is one boolean over the whole life. In the probe the last 9 identical windows still report `durable=False`, because the global matrix mixes earlier versions.
- Action: FIX. Compute the operator and gap per version span.

**M3. External change to the terms of satisfaction is not a version trigger. Severity L.**
- Location: `versioning/versions.py:40-45`.
- ch2 (136): "If an external force changes the terms ... the version has changed."
- Evidence: boundaries come only from a charter edition change or a TV jump. A venue or world-surface change (fees, a surface being enabled, a regime shift) that does not move cells within k windows is invisible.
- Action: BUILD. Treat world-surface and terms events as boundaries alongside `charter.activate`.

**M4. The fast harness is blind to pathologies. Severity L-M.**
- Location: `scripts/fastloop.py` scorecard (no `patholog`, `immune` or `versioning` reference in `scripts/`).
- ch2 (162): the goal is "an incentive-based immune system that corrects each of these pathologies live".
- Evidence: the operator's main feedback loop cannot see whether the organ fired, whether its responses bit, or whether the two learning-death detectors agree. This probe needed hand-written decryption scripts.
- Action: BUILD. Add per-seed flag counts, response counts and a detector-agreement field to the scorecard.

### PATHOLOGY (code that produces or masks a ch2 convergence pathology)

**P1. The learning-death predicate is keyed to card compliance and measurability, not population dynamics. Severity H.**
- Location: `factorylab/versioning/versions.py:127-133,143-151,186-214`.
- ch2 (156): learning death "can leave a clear signal behind, e.g., a single state with no variety".
- Evidence: the flag is `same and quiet and not improving and not holding`. `holding` requires every card to be measured and compliant, so:
  - an unmeasured card makes the flag fire (probe windows 3–6);
  - one satisfied card suppresses it forever (probe windows 9–19: same cell, zero registrations, zero revisions, flat outcomes).

  `lost_access` is computed and never used by the flag (the docstring says "the flag still fires on the gone-frontier rule").
- Action: FIX. Drop the `holding` gate. Diagnose from frontier invocation (P4's router signal: the NOOP share, or the draw mass on unhistoried seats), plus quiet, plus a single cell. Keep access evidence as the causal annotation once C1 is fixed.

**P2. The learning-death response does nothing for the population that is actually there. Severity H.**
- Location: `factorylab/runtime/pricing.py:259-268`, `routing.py:496-529`, `feedback.py:926-940`.
- ch2 (175): "The prevention of learning death should be delivered as a fact about the world".
- Evidence:
  - `_issue_novelty_grant` gives "one extra novelty trial per assembly".
  - `_unhistoried` returns `not has_history` for any `provenance == "seed"` assembly before the grant is consulted. The grant can only reach population-registered assemblies, and a learning-dead world has registered none.
  - `_count_consequence` still ledgers `novelty.grant_consumed` for seeds. That is 14 of 14 in the probe, a record of a response that conferred nothing.
- Action: FIX or DELETE. Either make the grant widen what a dead frontier can actually use (a larger novelty share for the next window, or protected compute and registration for seeds past their first record), or delete `novelty_grant`, `novelty.grant` and `novelty.grant_consumed` and rely on the structural reserve alone, which is what ch2 prescribes as "a fact about the world".
- Blast radius: `pricing.py`, `routing.py`, `feedback.py`, `bootstrap.py:449`, `resume.py:552`, `wake._on_novelty_grant`, tests.

**P3. The stable-failure duration counter resets on exploration noise. Severity M.**
- Location: `versioning/versions.py:106-117`, `versioning/operator.py:103-121`, `immune.py:188-206`, `controller.py:277-288`.
- ch2 (175): "ratcheting up penalties the longer the factory spends in a wide-spectral-gap attractor".
- Evidence:
  - `same` compares whole cells, which include the `registrations` and `revision` bins and a violation-magnitude bin split at 1.0.
  - One registration, which is exactly what the organ's raised γ encourages, or a failing card crossing violation 1.0, breaks `same`. `end_failure` then zeroes `failing_windows` and the ratchet restarts at one step.
  - The remedy therefore resets its own duration clock.
- Action: FIX. Define the failing attractor on the persistent violated-card set (and, per C3, a wide gap), not on activity dimensions.

**P4. Pricing stable failure through acting rewards makes abstention the escape, turning stable failure into learning death. Severity M.**
- Location: `runtime/feedback.py:2010-2049` (NOOP is credited the neutral whatever the prices), `pricing.py:728-735`, `controller.py:251-275`.
- ch2 (175): "price the duration of failure". ch2 (156): the frontier "is no longer being invoked".
- Evidence:
  - The ratchet raises λ only on decisions that act. An abstaining router draw is credited `RouterState.neutral()` with no card term.
  - The longer the failure lasts, the more NOOP dominates. Commit 3ffb314 describes exactly this drift: "a router whose seats score below its zero-consequence reward drifts to NOOP".
  - Separately, `prices.penalty_cap = 0.5` and `lambda_max` saturate the ratchet after about 6 windows at `step = 0.05`, so duration stops being priced.
- Action: FIX. Charge the failing attractor's λ-cost against the abstention's neutral too, so that *staying* is what costs. Re-examine whether the cap should bind duration pricing.

**P5. The immune organ runs 1:1 with the controller it revises (cascade ratio violated). Severity M.**
- Location: `runtime/pricing.py:443-471` (PID `observe` at :446, then `close_window` at :471 in the same boundary), `immune.py:184-206`.
- ch2 (300): "an inner loop must resolve itself several times faster than the outer loop".
- Evidence: every window close first updates the PID λ and then lets the organ ratchet λ, change decay and step γ. The outer corrector acts at the inner loop's own frequency, which is the setup for iatrogenic thrash (ch2 IV.c).
- Action: FIX. The organ acts once every ≥3 price windows, jittered, while it still diagnoses every window.

**P6. Offline versions split on measurement artefacts. Severity M-L.**
- Location: `versioning/operator.py:111-119`, `versioning/versions.py:31-45`.
- ch2 (136): versions "describe periods of behavioral consistency".
- Evidence: `-1` (unsupported) is treated as a behaviour value, and boundaries have no debounce ("without debounce"). The probe shows 4 one-window versions produced only by a card becoming measurable.
- Action: FIX. Treat unsupported as missing, not as a cell coordinate, and require a minimum version duration (≥ k windows).

### UNPRESCRIBED (deletion candidates)

**U1. `router.learning_death`: a second detector that only observes. Severity M.**
- Location: `runtime/routing.py:185-192,868-903`, `RouterState.watch`, the 3 lines in `feedback.py`, `tests/runtime/test_router_learning.py`, `docs/manifest.md`.
- ch2 (162): "an incentive-based immune system that corrects each of these pathologies live".
- Evidence: the commit says "Observation only: nothing reads the watch or the entry back". It disagrees with the immune flag (probe: 0 against 4), and it adds checkpointed state (`watch`) that resume must carry. It is nonetheless the more faithful signal ("frontier no longer invoked").
- Action: FIX by making it the input to the single learning-death diagnosis (P1), then DELETE the separate ledger kind. If it is not folded in, DELETE it (about 60 lines, one test file and one resume field).

**U2. Price relief: dead code. Severity L-M.**
- Location: `charter/controller.py:112,294-311,464,510`, `pricing.py:458-470` (the comments and the `expire_relief` call), `wake.py:652-654`, `tests/charter/test_price_ratchet.py`, `tests/runtime/test_immune_ratchet.py:60`.
- ch2 (175): "ratcheting up penalties the longer". Halving is the opposite.
- Evidence: `relieve()` is called nowhere in `factorylab/` (checked by grep). Commit c239e4e replaced relief with the ratchet. No old world can be resumed under this code anyway (release digest), so the path cannot execute.
- Action: DELETE `relieve`, `expire_relief`, `relief_window` and `effective_lambda`, plus the relief tests. `wake` may keep a reader for historical diaries.

**U3. The integral price law and `kappa`, still the default. Severity M.**
- Location: `charter/controller.py:123,132-137,166,179-181,371-386`, `worlds.py` defaults, and the hash shim `worlds.py:653-655`.
- ch2 (172): λ is determined by "a proportional–integral–derivative (PID) controller".
- Evidence:
  - PID is opt-in. Only the two edition5 worlds set `controller = "pid"`; 20 worlds run the pre-PID integrator with a one-sided `kappa` damper that ch2 does not prescribe.
  - A PID with Kp = Kd = 0 already gives an integral-only law.
- Action: DELETE the integral branch and `kappa`, and make PID the only law, with default gains stated in every manifest.
- Blast radius: pricing semantics of every non-edition5 world (each becomes a new v0, which is correct per ch2 170), `tests/charter/test_controller.py`, and the manifest docs.

**U4. Early-warning signals (variance and lag-1 ACF). Severity L.**
- Location: `versioning/versions.py:251-297`, `report.py:445,476-485`.
- ch2: nothing in §II; the four pathologies are detected by operator and gap.
- Evidence: critical-slowing-down statistics from the resilience literature. They are offline only, drive no response, and add series (`balance`, `disagreement`) to the profile just for themselves.
- Action: DELETE, along with their render lines and tests.

**U5. `immune.bins`, a manifest key with one legal value. Severity L.**
- Location: `worlds.py:398,928-929`, `report.py:410-411`.
- ch2: n/a.
- Evidence: the value must be 3 (`raise ValueError("... must be 3")`). A cast that cannot vary is not a cast.
- Action: DELETE the key; hardcode the three region-relative bins.

### SMUGGLING (Class 1 or Class 2 content)

**S1. 28 hash-neutral manifest shims keep identities stable across kernel-semantics changes. Severity M.**
- Location: `factorylab/runtime/worlds.py:562-670` (`canonical_json`), for example :641-669.
- ch2 (170): the architect's kernel change "has to be understood as lethal", and "a new factory begins from a new v0".
- Evidence: every new key is popped from the hash at its default "so an added key may not rename a world that predates it". For example, the stable-failure response changed from relief to ratchet (c239e4e) while every edition 1–3 manifest kept its hash. The manifest hash, which is the identity the charter's roster digest is ratified against, now names worlds whose physics differ. That is Class-1 backward-compatibility engineering: it treats the manifest as a software artefact to keep stable across releases.
- Action: FIX. Bind manifest identity to the kernel semantics it runs under (for example a kernel-semantics digest in the genesis), and delete the 28 pops, accepting that old manifests hash anew (each is a new v0).
- Blast radius: hash-pinning tests, runbooks, charter roster digests.

**S2. git HEAD is part of the release identity. Severity M-L.**
- Location: `factorylab/runtime/release.py:141-153`, enforced at `resume.py:830-832`.
- ch2 (132): "it cannot be Git", and configuration state "cannot be understood as constitutive" of a version.
- Evidence: `release_digest = sha256(git_head + uv.lock + tree(factorylab/))`. A docs-only or tests-only commit changes `git_head`, so a crash-resume is refused (`release_mismatch`) and the world dies from a change that altered no kernel physics. The tree hash already captures every executable byte.
- Action: FIX. Set `release_digest = sha256(uv.lock + tree)` and keep `git_head` as forensic metadata. KEEP the refusal itself: a kernel change *is* lethal.

**S3. `price_step` falls back to `gain_step` (a unit-conflation compatibility shim). Severity L.**
- Location: `worlds.py:405-408,657-658`, `immune.py:194`.
- ch2 (172): λ is "the weight of the penalty".
- Evidence: the γ step and the λ step are different units, as the code comment admits. The fallback exists only so that older worlds hash as they did.
- Action: FIX. Make `price_step` required, and delete the fallback and its hash shim.

---

## KEEP (checked; conforms, with reason)

- **What a version is: the behavioural cells and the non-incrementing spans** (`versioning/operator.py`, `versions.py:31-65`). Versions come from judge and card profiles, not configuration, and a charter edition opens a version. This matches ch2 134-136 ("version it by what it does"). Fix P6 and M2; keep the concept.
- **Novelty reserve** (`kernel/reserve.py`, `routing.py:496-560`, `compute.py:413-470`). A protected share of spend, and of registration (write access), that only contracts without settled history can use, re-opened every window. This is literally ch2 175: "some share of compute and write access is usable only in the context of unhistoried actions". It is kernel physics.
- **Guaranteed patience** (`routing.py:496-521`: `novelty.trials`, `max_lifetime_windows`). A registered assembly keeps protected compute through N delivered consequences within a lifetime bound: "allowed to live longer than justified by its own current scoring" (ch2 290).
- **Stable-failure duration ratchet** (`controller.ratchet`, `immune.py:188-199`). Prescribed (ch2 175); it is ledgered first and resumes with the controller. Fix P3 and P4 around it.
- **PID λ** (`controller.py:419-457`). Kp·e + I(η, anti-windup, decay leak) + Kd·max(0, Δmeasurement), taken on the measurement. This follows Stooke et al. 2020. Kd escalates early rather than damping λ; ch2's "dampen price escalation before it overshoots" is satisfied because the integral does not have to wind up. Make it the only law (U3).
- **Kill switch** (`kernel/termination.py`, `runtime/witness.py`). `explicit_kill` is final and releases the seal. Resume refuses a killed identity even from a backup diary (`resume.py:1092-1115,838-857`). "A brutal kill switch that nukes the identity" (ch2 172). Kernel physics.
- **Snapshot and resume** (`resume.py`). Forward-only crash recovery of the same identity. It refuses a manifest-hash mismatch, a release mismatch, a facilitator mismatch or missing artefacts. `runtime_state`/`restore_runtime` have exactly one producer (`loop.py:467`) and one consumer (`resume.py:1140`), so there is no rewind or branch path. That matches ch2 132, "does not move backwards". Kernel physics.
- **Overfitting is not priced; the sampling rate is raised instead** (`feedback.py:1558-1597`). This is consistent with ch2 177 (a price is "yet another metric") and IV.b (raise the sampling rate).
- **Retirement** (`governance.py:1195-1260`, `routing.py:1006-1017`). Population governance by committee vote, retaining accounts and learners. It is not versioning and has no rewind. Out of this slice.
- **`continuity.py`, `charter/windows.py`, `runtime/cards.py`.** These cover seat memory (kernel "memory" physics, ch2 170), card sample selectors and region parsing. None of them carries a software-release notion of version.

## Counts

| Class | Count | IDs |
|---|---|---|
| CONTRADICTS | 3 | C1, C2, C3 |
| MISSING | 4 | M1, M2, M3, M4 |
| PATHOLOGY | 6 | P1–P6 |
| UNPRESCRIBED | 5 | U1–U5 |
| SMUGGLING | 3 | S1, S2, S3 |
| KEEP | 10 | as listed above |
