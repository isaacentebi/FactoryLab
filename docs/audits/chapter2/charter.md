# Audit: Chapter II §IV "The Charter and the Loop" and §IV.a "Self-Writing"

Branch `fast-loop-harness` at 5049b0f, clean tree. Read-only audit. The one probe run was offline: `committee.draw` edge cases, plus a read of the diary `work/population-e5a/events.json` (7,490 items).

Files read: `factorylab/charter/*` (all eight modules); `runtime/governance.py`, `pricing.py`, `cards.py`, `cadence.py` and `immune.py` (price hooks); `bootstrap.py:744-767`; `worlds.py` (charter, prices, committee and provenance); `kernel/termination.py` and `kernel/wallet.py` (death); `scripts/adopt_charter.py`, `ratify_charter.py` and `draft_edition1.py` (header); `docs/charter/edition{2,3}-draft.toml` and `edition3-ratification.json`; `docs/architecture/edition5-roadmap.md`; and the `[charter]`, `[prices]` and `[committee]` sections of all 22 `worlds/*.toml`.

## Answers to the key questions

1. **Can the factory propose metrics for norms?** Yes, and this is the healthiest part of the slice. A seat can amend cards (add, replace or remove) with preflight (`governance.py:1049-1162`). It can register its own observation code, which is preflighted on the last closed window (`governance.py:294-347`). It can open a metric *challenge* with a side-by-side trial and a ballot (`governance.py:427-576`). **However**, observations the population writes are second class: they are window-global only, and can never be grouped per role or assembly (see C3).
   **Does the factory contribute to λ as a posted shadow price?** No. The controller posts λ *to* the factory in `world.card_prices`. The only way the factory can contribute is a literal `lambda` on an added or replaced card. That number is voted yes or no and then hard-set as the integrator's state (`amendment.py:21-32`, `governance.py:1508-1512`, `controller.py:235-249`). No price is posted by the factory, and none reaches the committee as information (M1).
2. **Standing sortition, or episodic?** Episodic. A committee is drawn only when a proposal arrives (`governance.py:1161`, `:574`). There is no seat on a cadence and no standing body. The measured `GovernanceCadence` only spaces activations; it does not seat anyone (C1).
3. **Who authored the norms and cards?**
   - **Norms.** Edition 2's four norms are "the four the reviewer proposed, in its words", where the reviewer is the GPT-6 auditor (`docs/charter/edition2-draft.toml` header). Edition 3 adds *fidelity*, also in GPT-6's words. That is an extrafactory authorship adopted by the architect, and ch2 permits it ("humans and/or extrafactory agents"). KEEP, except fidelity's procedural clause (S2).
   - **Cards.** Edition 1's cards were drafted by the population (`scripts/draft_edition1.py`), as ch2 prescribes. Editions 2 and 3, which every live world carries, are the **architect's draft** (the file header says so), with λ set by the architect. At ratification the population may only *select a subset, unchanged*. For edition 3 that was a one-card ballot, selected 5 of 5 (S1). This is Class 2 smuggling into the metrics layer that ch2 says should be ceded.
4. **Is there any futarchic or conditional-market mechanism?** None. `grep -i futarch|conditional market|prediction market|shadow price|holdout` over `factorylab/`, `scripts/` and `worlds/` finds nothing relevant. The nearest thing is the policy-promise ballot: every vote is graded against the predicted effect after activation (`governance.py:1584-1621`). That is a "bet on beliefs" embryo, but it resolves only the branch that was taken (P1), and the PID is purely backward-looking (M2).
5. **Do the ratification scripts or roster pins make humans intervene inside the factory?** No. `ratify_charter.py`, `adopt_charter.py` and `draft_edition1.py` run before launch, at charter time, and the population votes in them. The roster and charter digests (`provenance.py`, `worlds.py:711-734`) bind a funded launch to the roster that voted; they are a launch-time cast. No CLI path or operator path amends a live charter or sets a live price: the only mutation sites are `governance.py:1150` and `:553`, reached through factory amendments, and `bootstrap.py:766` at launch. The operator can only kill. That matches the ch2 quote "Governance never intervenes in the internal affairs". The converse problem is real, though. Because a world's charter must be edition 1 (`worlds.py:1061-1062`), a human or extrafactory house has **no** way to issue a new charter edition to a living factory, so every norm revision costs a death and a rebirth (M4).

## Findings

Severity is H (high), M (medium) or L (low).

### CONTRADICTS

**C1. Governance is summoned per proposal, not seated on the cadence of charter revision** (H)
- **Where:** `factorylab/runtime/governance.py:1161` (`charter_book.seat` inside `_propose_amendment`), `:574` (challenges), `:1224` (retirements) and `:944` (connectors); `factorylab/charter/book.py:123-142` ("exactly one committee per proposal").
- **ch2:** "On the cadence of charter revision, a sample of the factory's population is seated"
- **Evidence:** A committee exists only as a reaction to a motion. There is no standing seat, no scheduled revision session, no agenda of λ or metric review, and no deliberation between seats. In e5a the only committee in the diary was seated at `seq 5326`, for a single proposal. The roadmap concedes this (edition5-roadmap.md:56-58). The cadence object (`runtime/cadence.py`) gates *activation* spacing and never seats anyone.
- **Action:** BUILD. Add a standing committee drawn at each governance-cadence boundary (reuse `GovernanceCadence.earliest_*`), rotated each boundary. The standing committee's agenda is every pending motion, plus a mandatory review of posted λ (M1) and challenged cards. Keep `CharterBook.seat` as the mechanism, but key it on the boundary instead of the amendment.

**C2. The sortition does not represent the population's variety, and a one-seat committee can pass** (H)
- **Where:** `factorylab/charter/committee.py:88-100` (the coverage groups are only `producer`, `evaluator` and `meta`; `count = min(size, len(candidates))`); `governance.py:1164-1193` (the `min_settled` experience filter).
- **ch2:** "sortition … needs to resemble to some degree the requisite variety of its stakeholders"
- **ch2 also names:** "no-regret learners, no-swap-regret learners, productive agents, evaluators, antagonists"
- **Evidence:**
  - `antagonist` is a role (`cortex/registration.py:25`), but it gets seated only by uniform fill.
  - Learner type (EXP3 vs Blum–Mansour) is not a stratum at all.
  - The offline probe `draw({"a": "producer"}, rng, 5)` returns one seat, whose single vote passes an amendment. `draw({}, …)` returns zero seats, which then auto-fail.
  - In the e5a diary the seated committee was `empirical`/producer, `constructor`/producer and `antagonist`/antagonist. It had no evaluator and no meta seat, because the experience filter left three eligible seats.
- **Action:** FIX. Stratify `draw` over every role in `ROLES` and over learner type, and set a minimum quorum (for example 3). Below quorum, defer the motion to the next cadence boundary instead of seating a rump. Report coverage in `charter.seat`.

**C3. Metrics written by the factory are structurally weaker than the architect's seed metrics** (M)
- **Where:** `factorylab/charter/measurement.py:74-79` (a registered observation gets `window_kinds=["windows"]` and `groupable=False`), `measurement.py:251-254` (a `per` role or assembly selector is refused for non-seed observations), and `runtime/observations.py:327` (`window_facts` strips identity).
- **ch2:** "The easiest ground within a charter to cede to the factory's contributory arm is its metrics layer."
- **Evidence:** Only seed observations, which are code the architect wrote, can be measured over returns or forecasts, or per role or assembly, and so carry attributable blame (`pricing.py:549-584`). A proxy proposed by the factory can only be a window-global number, priced through the generic share with the `min_blame_share` floor. The factory can therefore propose a proxy, but never one with the accountability of `avoidably_unresolved_share` or `cost_per_attempt`.
- **Action:** FIX. Run registered observation code per scope on scope-filtered, still anonymous facts. The kernel knows the scope and the code never sees it. Then per-role and per-assembly cards become available to population proxies. Anonymity (AGENTS.md) is preserved because the partition is the kernel's.

### MISSING

**M1. The factory posts no shadow price λ** (H)
- **Where:** `factorylab/charter/amendment.py:21-32` and `:137-146`; `runtime/governance.py:1078-1087` and `:1508-1512`; `charter/controller.py:235-249`.
- **ch2:** "It reaches the committee as a speculative price posted by the factory."
- **ch2 also says:** "λ cannot be determined without collaboration with the factory."
- **Evidence:** λ is set in exactly three ways:
  - the architect's literal in the world TOML (`bootstrap.py:763-766`, ledgered as `amendment_id="manifest:edition1"`; e5a `seq 71`, λ=0.1);
  - the backward-looking controller;
  - a literal on an amendment card, which becomes a yes-or-no vote and then overwrites the integrator.

  No seat can post "my marginal worth of constraint X at my operating point", and no aggregate of such posts is shown to the committee. The flow is controller → factory only (`world.card_prices`, `cortex/schematics.py:502`).
- **Action:** BUILD a λ-posting return: a seat declares a per-card price and stakes it through the ordinary reward channel. Aggregate the posts, ledger them, and put them in the standing committee's agenda (C1) beside the controller's λ. A seat's error is penalized when realized violation cost diverges from its posted price.

**M2. There is no futarchic or conditional-market mechanism, and the λ controller is purely backward-looking** (M)
- **Where:** `factorylab/charter/controller.py:341-457` (both laws react only to realized violation); the absence of any market construct anywhere in the repo.
- **ch2:** "constraint determination (λ and metrics) might be well served by conditional prediction markets"
- **ch2 also says:** "A futarchic λ, however, is necessarily forward-looking"
- **Evidence:** The PID prices only windows that have already violated. Adversarial seats have no instrument with which to arbitrage a mispriced λ. The policy-promise ballot (`governance.py:1536-1621`) is the closest analogue, but it is a boolean vote graded after the fact, and it resolves only the enacted branch (P1).
- **Action:** BUILD, after M1. Add conditional forecasts on a motion's predicted effect, over both the enact and reject branches, using the existing predicate and forecast settlement (the reject branch resolves as a counterfactual on the unchanged charter). Feed the expected violation into the PID as a feed-forward term. Norms stay out of it: they sit behind the read-only wall, as ch2 recommends.

**M3. The evaluatory layer cannot add holdout criteria, and cards are not probabilistic** (M)
- **Where:** `factorylab/charter/charter.py:72-106` (`MetricCard`: a threshold on a mean over `n` samples); `charter/windows.py`.
- **ch2:** "permitting its evaluatory layer to continuously add holdout test criteria to a given charter"
- **ch2 also says:** "with holdouts, confidence intervals, and sample size requirements all subject to change"
- **Evidence:** A card has a sample size (`window.n`) but no confidence interval and no holdout set. There is also no path by which an evaluator's adversarially found case becomes a charter criterion. Predicates are registered claims, not charter entries, and challenges replace a card whole.
- **Action:** BUILD. Add an optional `holdout` field on a card: a named set of predicate or scenario ids that the card's observation must also satisfy. Let evaluator and antagonist seats append to it through the ordinary amendment path, with a trial like the challenge mechanism's. Add an optional interval requirement to `MetricWindow`.

**M4. No human or extrafactory house can revise a living factory's charter** (M/H)
- **Where:** `factorylab/runtime/worlds.py:1061-1062` ("charter.edition must be 1"); `amendment.py` (norms immutable); the absence of any CLI or cadence channel.
- **ch2:** "governance occurs in the rewriting of a charter and a subsequent permission"
- **ch2 also says:** "the factory is expected to testify within the assembly"
- **Evidence:** In-run governance consists entirely of factory seats, and it edits cards only. The read-only norm wall is correct. But the house that owns the norms has no channel except writing a new world, which means a new ledger and a death. Ch2 reserves death for kernel intervention, and treats charter rewriting as a soft cast. The population also has no channel to testify on norms, apart from the whole-charter reject in `adopt_charter.py`.
- **Action:** BUILD a signed, ledgered "norm edition" input, applied only at a cadence boundary. It produces `edition+1` with the new norms, the factory's cards carried over, and cards on removed norms refused as book refusals. Before it applies, the standing committee (C1) returns ledgered testimony. The write permission for this input is a launch-time hard cast in the manifest.

**M5. λ is unit-free and has no link to material cost; the λ↔cost correlation is never computed at charter time** (M)
- **Where:** `factorylab/runtime/pricing.py:728-786` (the penalty is subtracted from a score clipped to [0, 1]); `runtime/worlds.py:297-317` (`lambda_max = 1.0`, `penalty_cap = 0.5`, no units).
- **ch2:** "correlation between the pricing λ … and the material costs … profoundly understood at charter time"
- **Evidence:** λ scales a unit-interval reward penalty. Nothing relates a card's λ to USD, tokens, or the capital loop, and no report at charter time estimates what a λ costs the factory in dollars. `lambda_max` and `penalty_cap` are the same arbitrary constants in all 22 worlds.
- **Action:** BUILD a charter-time report, added to `ratify_charter.py` and `draft_edition1.py` or their successor. From the rehearsal diaries it computes each card's realized penalty × decisions × the mean dollar value of a unit of reward. At runtime, publish the same statistic per window in `world.card_prices`.

**M6. Speed is amendable, but it is not priced as cash burn** (M/L)
- **Where:** `factorylab/charter/amendment.py:43-58` and `governance.py:1513-1531` (a `tick_interval` amendment); no burn card in any live world.
- **ch2:** "speed is categorically indistinguishable from a specific approach to cash burn"
- **Evidence:** A seat can make the clock faster or slower through a yes-or-no vote. In e5a (`seq 5325`), `tick_interval "30s"` rode on a card-removal motion. No card, prediction or price attaches the change to burn, and edition 2 deliberately removed every cost card. The roadmap names this gap (item 2).
- **Action:** FIX. A `tick_interval` motion must carry its own predicted effect on a burn observation, such as a population-registered one over `wallet_balance_micro`, or a seed `burn_per_window`. It must also be a separate motion (P3).

**M7. The charter's live-or-die threat has no measured signal** (L)
- **Where:** `factorylab/kernel/termination.py:32-42` (death conditions fixed to `balance_zero`, `explicit_kill` and `ledger_failure`); `controller.py:387-392` (the `saturations` counter is kept but surfaced nowhere).
- **ch2:** "if it cannot be satisfied beyond what is priced as acceptable, then the factory needs to be scrapped"
- **Evidence:** Correctly, the kernel does not auto-kill on the charter. But governance never sees an "unsatisfiable at λ_max" statistic, so the threat cannot be invoked on evidence.
- **Action:** BUILD a per-card saturation and duration statistic in the public window block and in the standing committee agenda. KEEP the kernel's three deaths unchanged, and KEEP the $0 budget as the ultimate kill (`wallet.dead` → `balance_zero`, `termination.py:92-93`).

### SMUGGLING

**S1. The architect authors the live metrics layer, and ratification is a rubber stamp** (H)
- **Where:** `docs/charter/edition3-draft.toml` (header: "architect's draft"); `docs/charter/edition2-draft.toml` (also "architect's draft", with cards pruned by an audit); `scripts/ratify_charter.py:201-207` ("Keep every selected definition unchanged"); `docs/charter/edition3-ratification.json` (one card, 5 of 5 selected it); the `[charter]` section of every `worlds/edition{2,3,5}-*.toml`.
- **ch2:** "if you hand it a norm, it will propose a metric to represent that norm"
- **Evidence:** Edition 1 had the population draft cards under the norms (`scripts/draft_edition1.py`), with a committee vote. Editions 2 and 3 regressed:
  - the architect, following GPT-6 audits, wrote, removed and priced the cards (`censorship-bound`, λ=0.10, its observation `avoidably_unresolved_share`, and that observation's exclusion semantics coded into `measurement.py:326` and `:389`, `feedback.py:1222-1228` and `settle.py:73`);
  - the population could only select the cards.

  That is Class 2 content: a plan about how the norm should be measured. The in-run amendment path mitigates it (and in e5a a seat did try to remove the card), but the constitution the factory starts with is not its own.
- **Action:** FIX. The next charter is produced by `draft_edition1.py`'s method: the architect supplies norms only, the population proposes cards and λ, and a sortition votes. Retire the "select a subset, unchanged" ratification mode. Its only surviving use would be a repair vote over population-drafted cards. The special settlement path for `avoidably_unresolved_share` stays as world plumbing, but it should be one observation among many, not the charter's reason to exist.

**S2. The *fidelity* norm encodes a judge procedure** (L/M)
- **Where:** `factorylab/charter/charter.py:186-193`; the `fidelity` definition in every edition-2, 3 and 5 world.
- **ch2:** "vote on a more abstract and primary array of norms"
- **Evidence:** Its phrasing, "A judge identifying such a conflict must name the value, the measurement, the evidence and the uncertainty…", is a Class 2 plan for evaluators placed in the read-only norm layer, where the factory cannot amend it. In e5a, voters parrot its clauses as rules (`charter.vote` reasons, `seq 5381`).
- **Action:** FIX at the next norm edition. Keep the value ("measurements are defeasible evidence of the values"). Drop the procedure, and let the factory propose a card or holdout for it (M3).

**S3. A default charter is hidden in code** (M/L)
- **Where:** `factorylab/charter/charter.py:197-242` (`SEED_NORMS`, `seed_charter()`); `runtime/worlds.py:517` and `:1140`.
- **ch2:** "The input is delivered in the form of a charter"
- **Evidence:** A world with no `[charter]` table silently runs on four norms and three cards the architect wrote into the code. Six worlds do so: `scripted`, `scripted-crash`, `testnet`, `testnet-accelerated`, `testnet-10m-roster` and `compute-continuity-roster`. The charter is then not an authored, editioned input: it is code.
- **Action:** FIX. Require an explicit `[charter]` table. Move the seed charter text into `worlds/scripted.toml` and the other five. The `seed_charter()` builder can stay as a test fixture under `tests/`.
- **Blast radius:** 6 worlds; about 5 test files use `seed_charter` or `SEED_NORMS`. The manifest hash changes for those worlds.

**S4. A charter ballot carries world-specific facts written by us** (L)
- **Where:** `scripts/adopt_charter.py:66-69`, where `compute_supply` is prose about the OpenRouter and Venice supply and `treasury.transfer`.
- **ch2:** "the first charter is almost certainly coauthored with several agentic systems"
- **Evidence:** This is coauthorship by the architect's prose inside the vote prompt, specific to the compute-continuity world.
- **Action:** FIX. Derive the text from the manifest (providers and treasury), or drop it.

### UNPRESCRIBED (deletion candidates)

**U1. `EDITION3_NORMS` is a dead constant** (L)
- **Where:** `factorylab/charter/charter.py:165-194`.
- **Evidence:** There are zero references in `factorylab/`, `scripts/`, `tests/`, `worlds/` or `docs/`. The world files carry the norms inline.
- **Action:** DELETE. It has no dependents.

**U2. Price relief is dead code: `relieve`, `expire_relief`, `relief_window` and the halved `effective_lambda`** (L)
- **Where:** `factorylab/charter/controller.py:112`, `:294-311`, `:464` and `:509-510`; `runtime/pricing.py:470`.
- **Evidence:** `relieve()` has no callers. The roadmap replaced relief with the ratchet ("instead of halving it"). `expire_relief` runs every window over a field that is always `None`.
- **Action:** DELETE all four.
- **Dependents:** `tests/charter/test_controller.py`, `tests/charter/test_price_ratchet.py` and `tests/runtime/test_immune_ratchet.py`; the controller snapshot keys `relief_window` and `effective_lambda`. Resume must tolerate them being absent from old checkpoints: read them with `.get` and ignore them.

**U3. There are two price laws: the "integral" law with `kappa` damping, and the PID** (M/L)
- **Where:** `factorylab/charter/controller.py:123`, `:157-192` and `:368-386`; `runtime/worlds.py:304` and `:311-317`; `kappa` in `bootstrap.py`, `resume.py` and `cortex/schematics.py`.
- **ch2:** "the PID controller through which λ is progressively determined"
- **Evidence:** Ch2 prescribes one controller. The integral law is the PID with kp=kd=0, plus the ad hoc κ damping. Every world except `edition5-*` still defaults to it.
- **Action:** FIX, collapsing the two into one. Make `pid` the only law, so that the old default becomes `pid` with kp=kd=0, and delete `kappa` and `CONTROLLERS`.
- **Blast radius:** the default price trajectories of 20 worlds change, because κ is gone; about 5 tests; resuming a pre-change diary (gate it on the recorded parameters, or refuse to resume).

**U4. `adopt_charter.py` is a third, near-duplicate pre-launch ballot** (L)
- **Where:** `scripts/adopt_charter.py`, used once for `compute-continuity-testnet.toml`; it duplicates `ratify_charter.py` and `draft_edition1.py` (each has its own `draw`, prompt and digest export).
- **Evidence:** Negotiation at charter time is prescribed. Three divergent scripts are not.
- **Action:** DELETE after consolidating all three into one charter-time sortition script: population drafting (S1), a whole-charter vote, and digest export.
- **Dependents:** the provenance comments in `worlds/compute-continuity-testnet.toml` (historical; keep the JSON evidence in `docs/charter/`).

### PATHOLOGY

**P1. Beliefs are graded only on the enacted branch** (M)
- **Where:** `factorylab/runtime/governance.py:1434`, `:1455` and `:1488-1494` (`_censor_ballots` on a failed motion); `:1584-1621`.
- **ch2:** "bet on beliefs"
- **Evidence:** When a motion fails, every ballot is censored. A "no" vote is therefore scored only when the voter is outvoted, and a "yes" vote only when it wins. A reflexive "no" carries almost no liability. In e5a, all three votes on `remove-censorship-bound` were "no" and the motion failed, so no ballot will ever be graded. This is the one-branch resolution problem that futarchy's conditional markets exist to solve.
- **Action:** FIX, through M2. Grade a failed motion's ballots against the unchanged charter's realized value over the same horizon. The rejected branch is observable.

**P2. Card regions are parsed from prose, and the runtime writes the challenger's promise** (L/M)
- **Where:** `factorylab/runtime/cards.py:19-60` (regex over `acceptable_region`); `governance.py:453` (a challenge builds prose from `rule` and `value`, then re-parses it); `governance.py:544-546` (the direction is inferred from the prefix `"at least"` or `"above"`, so a `between` band always predicts "decrease").
- **Evidence:** The region is stringly typed, and the challenger's predicted effect is invented by the runtime, not declared by the challenger.
- **Action:** FIX. Make the region typed data (`{rule, lo, hi}`) on `MetricCard`, with the prose derived from it. A challenge declares its own `predicted_effect`.

**P3. Motions are bundled** (M/L)
- **Where:** `factorylab/charter/amendment.py:57-58` and `governance.py:1119-1129` (card changes, λ and `tick_interval` in one yes-or-no vote with a single `predicted_effect`).
- **Evidence:** In e5a (`seq 5325`), a speed change rode on a card removal, and the only prediction was on the card. Bundling hides the speed and burn decision (M6) and makes the ballot grade meaningless for the part that was not predicted.
- **Action:** FIX. Allow one change class per motion (cards, λ, or clock), each with its own prediction.

**P4. Retirement motions block charter activation** (L)
- **Where:** `factorylab/runtime/governance.py:1231-1240`, `:1460` and `:1472`.
- **Evidence:** Retirements and connectors are the factory's internal self-organization, not charter governance, but they share the charter's cadence queue. A passed retirement at the head of the queue stalls every approved amendment behind it.
- **Action:** FIX. Give internal motions their own queue, or keep them out of `GovernanceCadence`. KEEP sortition for them: it is factory-internal and ch2 does not forbid it.

**P5. Edition numbering resets per world** (L)
- **Where:** `factorylab/runtime/worlds.py:1061-1062`.
- **Evidence:** The "Edition 3 charter" loads as `edition = 1` (`worlds/edition5-capital-loop.toml`), so the charter's own record does not carry its lineage across a rebirth. This is also the mechanism behind M4.
- **Action:** FIX alongside M4. Allow `edition = n`, with `parent_charter_sha256` as provenance.

## KEEP (checked against ch2 and found prescribed)

- **K1. Norms are read-only in-run, and write permissions are a launch cast.** No operation edits a norm (`amendment.py:1`, `:137-139`; `book.py:94-99`), and the committee parameters are immutable (`worlds.py:386-391`). Ch2 describes this as "a read-only wall"; the input layer's read and write permissions belong to the hard kernel.
- **K2. Self-writing of metrics.** Amendments, observation registration with preflight, and metric challenges with a side-by-side trial and frozen observation bindings (`book.py:65-81`, `:234-256`) are ch2 §IV.a, and the best-built part of the slice. Extend them (C3, M3); do not delete them.
- **K3. The sortition mechanics.** One seat has one vote (not token-weighted), aliases are shuffled, the proposer is excluded, the committee's ancestry is checked (`governance.py:1271-1288`), and a seat qualifies by experience (`committee.py:60-69`). Ch2: "not delivered through the medium of token-weighted vote."
- **K4. No operator path mutates a live charter or price.** The operator can only kill (`cli.py:507-564`). Ch2: "Governance never intervenes in the internal affairs of the superdark factory."
- **K5. A $0 budget kills.** `wallet.dead` leads to `balance_zero` or `balance_floor` (`termination.py:92-93`). Dormancy is a pause only while locked backing remains (`termination.py:98-103`), so the budget is not $0. That is the treasury's slice. KEEP.
- **K6. The PID as the baseline controller.** It uses derivative on measurement, the positive part of D only, and anti-windup (`controller.py:419-457`). Ch2 treats the backward-looking PID as the pre-futarchic baseline. The duration ratchet (`controller.py:251-288`) belongs to §II.b.
- **K7. The charter and roster digests** (`provenance.py`; `worlds.py:711-734`). These are a launch-time legitimacy binding, not an intervention inside the factory. Note that they are enforced only on mainnet, and that the `edition5-*` worlds deliberately drop the roster pin (`edition5-capital-loop.toml`, comment above `ratified_sha256`). A funded edition-5 launch therefore needs re-ratification.
- **K8. Policy-promise grading of ballots.** Each ballot is graded against the predicted effect after activation (`governance.py:1536-1621`). This is the "bet on beliefs" seed. KEEP, and extend it (P1, M2).
- **K9. The `tick_interval` amendment.** Ch2: "the factory keeps its own time". KEEP; fix its pricing (M6, P3).
- **K10. `measurement.py`, `windows.py` and `book.py`.** These are measurement and edition-book plumbing: sealed, ledger-first, with refusals kept as evidence. KEEP.

## Counts

| Class | Count | IDs |
|---|---|---|
| CONTRADICTS | 3 | C1, C2, C3 |
| MISSING | 7 | M1–M7 |
| SMUGGLING | 4 | S1–S4 |
| UNPRESCRIBED | 4 | U1–U4 |
| PATHOLOGY | 5 | P1–P5 |
| **Total** | **23** | (plus 10 KEEP verdicts) |
