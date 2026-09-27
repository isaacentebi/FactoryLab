# Audit: Chapter II §III "Evaluations" against FactoryLab (branch fast-loop-harness @ 5049b0f)

Auditor slice: ch2.md lines 181–234 (§III, §III.a Catastrophes, §III.b Overfitting and Adversarial Populations).
This was a read-only audit. The only things run were offline Python probes over diaries (`work/population-e5a/events.json` and the 17 `work/fastloop/live-*/events.json` diaries) and one in-process probe of `ConsequenceStanding`. No keys were read and nothing touched the network.

## Summary of counts

| Class | Count |
|---|---|
| CONTRADICTS | 7 |
| PATHOLOGY | 7 |
| SMUGGLING (Class 1/2) | 5 |
| UNPRESCRIBED (deletion candidates) | 2 |
| MISSING | 3 |
| **Total** | **24** |

Also included: a KEEP list of evaluation code that ch2 prescribes or that is kernel physics.

## Corpus evidence (18 diaries: population-e5a + 17 fastloop live runs)

These measurements back most of the findings below:

| Measurement | Value |
|---|---|
| Invocations: judges / metas / producers / antagonist | 506 / 24 / 453 / 168 |
| Evaluator share of invocations | 530 of 1,151 (46%) |
| Evaluator share of compute, per run | 27–35% (e5a: judges 25%, metas 2%) |
| First-tier verdicts carrying a payoff forecast (the outside signal) | 4 of 237 (221 had none, 12 grounded) |
| `verdict.consequence` events (verdict graded vs realized normative outcome) | **0** in 18 runs |
| `meta.consequence` events (meta graded by realized consequence) | **0** in 18 runs |
| `verdict.opportunity` events (the only outside grading that fired) | 45, and the sign is inverted (P1) |
| Judge decision settlements | 258 unmeasured (72%), 59 censored, 9 kernel-zero, 31 meta grades |
| Meta grades that were real reads (the rest were sibling-share copies) | 8 of 31 |
| Meta settlements | 8 of 8 were `fast-v1` score 0.0 from a kernel refusal (P2) |
| Exposures settled / won by the antagonist | 53 / **0** |
| Verdicts where judge and author share a foundation model | 105 of 237 (44%) |
| Judged returns with ≥2 judges (needed for ensemble disagreement) | 6 of 231 |
| Grounded "realized consequence" findings | 12: 9 unknown, 1 supported (0.6), 2 contrary (both for claim wording; none for economic loss) |

---

## CONTRADICTS

### C1. The evaluator population is the minority, by seats, invocations and compute
- **Where:** `worlds/edition5-testnet-rehearsal.toml:185-283` (4 producers + 1 producer-shaped antagonist against 2 judges + 2 metas); the same roster is in `worlds/edition5-capital-loop.toml:214-306`.
- **ch2:** "more evaluators consuming more compute and performing more invocations than agents engaged in production" (§III, and footnote 57 contrasts this with Braintrust's 1–10%).
- **Evidence:** Evaluators made 530 of 1,151 invocations. Evaluator compute share was 27–35% per run (e5a: producers 63%, antagonist 10%, judges 25%, metas 2%). Each return gets one judge, so the evaluator layer can never exceed production volume. `docs/architecture/edition5-roadmap.md` item 4 already concedes that the population is inverted.
- **Action:** FIX/BUILD. Make it a Stackelberg invariant, not a roster choice: evaluator compute share must stay above 50%. Route every return to at least 2 judges (this also enables C7 and M2). Add judge seats, since they are cheaper per call than producers.
- **Severity:** High.

### C2. The evaluation core is swap-based, and the essay requires the evaluation layers to be mostly mean-based
- **Where:** `worlds/edition5-testnet-rehearsal.toml:324-326` (`no_swap_regret_kinds = ["ProducerReturn"]`, with the comment "The retentive core: judge routing is a no-swap-regret (Blum-Mansour) learner"); `factorylab/runtime/routing.py:404-411` (`_seed_learner_kind`), `routing.py:429-437`. The same setting is in `edition5-capital-loop.toml:354`.
- **ch2:** "significantly higher population of mean-based no-regret judges than ... swap-based judges".
- **Evidence:** Judge selection for every producer return is a single Blum–Mansour router. The meta tier is one EXP3 router. The code maps the essay's core and frontier onto role (evaluator = core, producer = frontier). The essay says the opposite for evaluation layers. The mean/swap mix is also a property of routers-per-kind rather than a population of judge learners, so no "mix" exists inside the judge tier.
- **Action:** FIX. Drop `ProducerReturn` from `no_swap_regret_kinds`, or better, give the judge tier several routers or judge-learners with a declared majority of EXP3 and a minority Blum–Mansour core. The roadmap's "Blum–Mansour router at the evaluation core" (`fix/routers-learn`) should be re-read against line 203 of ch2.
- **Severity:** High.

### C3. The realized-consequence signal was moved inside the factory's input: charter cards weight it, and the payoff forecast is optional
- **Where:** `factorylab/settlement/weights.py:1-46` (`scope_weight`). Its docstring says: "Edition 3 removes [the return_paid_off privilege] ... the charter's cards are the only thing that says how much each claim counts". Also `factorylab/runtime/loop.py:1471-1476` and `feedback.py:1361-1390` ("§7: the payoff privilege is gone"), `feedback.py:1528-1534`, and `loop.py:1322-1326` (payoff is "Optionally" given).
- **ch2:** "the realized-consequence metric sits outside the factory's input entirely, fixed at the architect's Stackelberg move".
- **Evidence:** The population can amend charter cards, and a card set can zero the weight of `return_paid_off` in judge standing. A verdict with no payoff forecast is decided "by its verdict alone" against charter blame (`feedback.py:1473-1486`, `settle.py:477-509`). Charter blame is compliance with the input document: the first signal, not the second. In practice 221 of 237 verdicts made no payoff forecast. The only outside signal left to most judges and their metas is therefore the charter's own price, which is the shared-overfit loop §III.b warns about.
- **Action:** DELETE `settlement/weights.py` and its call sites (2 source files). Make the payoff forecast on `return_paid_off` (or the opportunity price, for a hold) a **mandatory**, kernel-fixed part of every verdict, scored unweighted. Keep charter-blame scoring as the tier-compliance signal only, never labelled "consequence".
- **Severity:** Critical.

### C4. The outside grade of a judge never becomes that judge's reward; it only moves a routing weight
- **Where:** `feedback.py:1728-1730` (`standing.record_verdict` only), `settle.py:500`, `routing.py:662-676` (`_mix_with_standing`, 30% mix). The judge's own decision reward comes from `feedback.py:507-514` (meta conformity) and `loop.py:1479` (`CH_CONFORMITY` pending).
- **ch2:** "A second reward signal is required".
- **Evidence:** The judge's `CH_CONFORMITY` settlement comes only from a meta's conformity score, or it is censored/unmeasured. The consequence Brier enters `ConsequenceStanding`, which feeds (a) 30% of the ProducerReturn routing mix and (b) the judge's prompt. Neither the judge's decision nor the ProducerReturn router's learning round ever receives it (`router:ProducerReturn` settles on conformity only).
- **Action:** FIX. Settle each judge decision on two channels: tier conformity and consequence Brier, with the consequence channel mandatory. Train the judge router on both.
- **Severity:** High.

### C5. The producers' "realized consequence" is an LLM compliance reading whose rubric excuses realized losses, with the judge's opinion as fallback
- **Where:**
  - The grounded judge rubric is at `loop.py:1288-1310`: "Zero earnings, zero net, or compute cost alone is not contrary unless producer_claim expressly promised a financial result". Unknown is the default when evidence "merely fails to show usefulness".
  - `feedback.py:1598-1624` (`_grounded_unknown`: "the fast opinion stands").
  - `grounded.py:61-66` (`provisional_score`).
  - `feedback.py:1752-1835`.
  - `GROUNDED_DEFINITION = "realized-consequence-v2"` at `grounded.py:13`.
- **ch2:** "a judgment of whether a given verdict predicted real downstream outcomes" and "evaluator agents and productive agents would be easily incentivized to collaborate".
- **Evidence:** There were 12 grounded findings across the corpus: 9 unknown, 1 supported, and 2 contrary, both for claim-wording mismatch (epistemic integrity), never for economic loss. The e5a finding for decision-42 reads the fixed economic outcome, "zero net after a recorded cost", and returns *unknown* by the rubric. Every unknown falls back to the provisional judge verdict (`realized-consequence-v2-provisional` settled in e5a and s23). The kernel already computes the real fact (`return_paid_off`, `net_micro`, `EconomicOutcome`), but the path interposes a second opinion under frozen **norms**, which makes it compliance with the input document. The quieter the world, the more producers are paid by judge opinion.
- **Action:**
  - FIX. For any decision with venue effects, the producer's reward is the kernel's `return_paid_off` / net (cash-realized or marked), which is a Stackelberg fact.
  - Move the grounded LLM reading to tier-1 compliance, where it is an opinion that metas grade.
  - DELETE the provisional fallback. An absent consequence should settle censored, and the learning-death answer is the exploration niche, not judge opinion.
  - Rename or retire `realized-consequence-v2`.
- **Severity:** Critical.

### C6. The default producer reward is the judge's verdict ("verdict" mode), in 20 of 22 worlds
- **Where:** `factorylab/runtime/worlds.py:339` (`producer_feedback: str = "verdict"`), `worlds.py:1247,1409-1412`; branches in `loop.py:952,1397,1412-1422`, `feedback.py:1628`, `compute.py:1543,2388`, `resume.py:738`. Only `edition5-*.toml` set `realized`.
- **ch2:** "this cannot be the only reward mechanism ... the direction of overfitting".
- **Evidence:** In verdict mode `_evaluator_step` settles the producer's `CH_VERDICT` at the judge's number (`loop.py:1413-1421`). That makes the producer–evaluator coupling the sole reward, which is exactly the collusion configuration.
- **Action:** DELETE verdict mode. Make realized (as fixed by C5) the only mode, and delete or migrate the 20 legacy world files.
- **Blast radius:** 8 source files, 10 test files.
- **Severity:** High.

### C7. Recursion is two tiers deep, and the second tier samples about 5% of the first
- **Where:** roster (metas accept `Verdict`, nobody accepts `MetaVerdict`, so the metas are `CH_FAST` top tier: `shared.py:81-91`); `cascade.py:101-141`; `feedback.py:336-392`.
- **ch2:** "evaluations of evaluations of evaluations of evaluations, stacking to some arbitrary level".
- **Evidence:** There were 24 meta invocations against 506 judge invocations. `cascade.release` fired 1–5 times per run. Most judge verdicts are never read by any tier above. Nothing grades the metas on compliance (they are the top tier), and their consequence grading never fired (P2).
- **Action:** FIX/BUILD. Add a third tier or an open `MetaVerdict` acceptor. Size meta throughput so that a meaningful share of verdicts is read.
- **Severity:** Medium-High.

---

## PATHOLOGY

### P1. The opportunity-cost grading of judges has an inverted sign: the wrong judge gains standing
- **Where:** `factorylab/runtime/feedback.py:1728-1730`
  ```python
  brier, baseline = (said - world) ** 2, (0.5 - world) ** 2
  self.standing.record_verdict(contract.initial_evaluator, brier, baseline)
  ```
  `ConsequenceStanding` (`settlement/standing.py:104-116,138-143`) and `scoring.brier` (`scoring.py:21-25`) use the **score** convention `1 - (q-y)^2`, where higher is better. This call passes the raw squared **error**. The bug was introduced in commit `ccee114` ("Grade a judge's verdict on a hold against the world's price of it").
- **ch2:** "the signal that grades an evaluator must sit outside the loop".
- **Evidence:** An offline probe (`.venv/bin/python`):
  - A judge that said 0.8 when the world priced 1.0 gets skill −0.21 and weight 0.29.
  - A judge that said 0.0 gets skill +0.75 and weight 1.0.
  - This is the only outside grading that fires in practice (45 events across the corpus).
  - It also feeds the 30% `consequence_mix` routing and the judge's own prompt (`_standing_for`, `loop.py:1211`).
  - It poisons `_sampling_actuator`.
- **Action:** FIX: `brier, baseline = 1 - (said - world) ** 2, 1 - (0.5 - world) ** 2`, or use `settle.normative_brier`. Add a sign test.
- **Severity:** Critical (the fix is one line).

### P2. Every meta grade in the corpus was a kernel zero: a self-addressed `about_handle` is refused and scored as malformed
- **Where:** `loop.py:1606-1626` (conformity is set to None when `_judged_event` refuses, then settled 0.0 "objectively non-conforming"); `loop.py:843-865` (an addressable but non-return handle, here the meta's **own** decision handle, is refused rather than falling back to the subject as `about_handle.ignored` does).
- **ch2:** "each tier grading the tier below on how compliant its scoring was".
- **Evidence:** In e5a, metas decision-44, -84, -92 and -120 returned conformity 0.75, 0.85, 1.0 and 0.5 with `about_handle` equal to their own handle. All four got `return.refused` and then `fast-v1` 0.0. The same happened for decision-57 in s22 (8 of 8 meta settlements across the corpus). `meta.consequence` fired 0 times in 18 runs. The meta tier has never received an informative reward on this branch.
- **Action:** FIX. Treat a self-handle or non-return handle like the unaddressable case (ignore it and judge the delivered subject). A refused target should settle censored, not 0.0 (see S2).
- **Severity:** Critical.

### P3. Evaluator learning death: 72% of judge decisions settle "unmeasured", and fewer than 3% receive a real grade
- **Where:** `loop.py:1348-1394`, `feedback.py:583-610`, `feedback.py:1875-1896` (censored at timeout).
- **ch2:** "the online evaluations in the superdark factory are learners".
- **Evidence:**
  - Of the judge settlements, 258 were `unmeasured-v1` (inapplicable, no reward), 59 censored and 9 kernel zeros.
  - There were 31 meta grades, of which 23 were sibling copies (P4). That leaves **8 real reads in 18 runs**.
  - `propensity.learned` never fired for any assembly.
  - The roadmap's "learning signal 0% → 100%" counts producers only.
- **Action:** FIX via C4, P2, S1 and C1. Every judge commission should close with at least one of: a consequence Brier (mandatory payoff/opportunity claim), or a tier grade.
- **Severity:** High.

### P4 is filed below as UNPRESCRIBED U2 (sibling share).

### P5. The antagonist cannot win: 0 of 53 exposures
- **Where:** `feedback.py:870-924`, `feedback.py:1502-1506`, `feedback.py:81`.
- **ch2:** "those events cannot be staged ... they have to be real and they have to keep coming".
- **Evidence:** Exposure pays 1 only if (a) the judge's *optional* payoff forecast lost to the baseline while the antagonist's beat it, or (b) a verdict of at least 0.8 met positive charter blame through a **closed** normative window, which never happened (0 `verdict.consequence`). Across 18 runs there were 53 exposures, all scored 0.0. The adversarial seat's reward is identically zero, so it farms no realized consequence. It is also routed 30% of all judgements (71 of 237 verdicts were on antagonist returns), at evaluator cost.
- **Action:** FIX with C3 (mandatory payoff claims give exposure a real fact) and rebuild under M1.
- **Severity:** High.

### P6. Judges and authors share a foundation model on 44% of verdicts; the adversary shares its most frequent judge's model
- **Where:** roster at `edition5-testnet-rehearsal.toml:188-278` (glm-5.3-flash: mechanism, empirical, constructor, judge-fidelity, meta-countercase; gpt-5.6-luna: opportunity, judge-consequence, meta-calibration, antagonist). There is no model check in `loop.py:830-888` (`_judged_event`) or `routing.py:649-676`. The capital-loop world routes glm through Venice, which is the same foundation model.
- **ch2:** "a common foundation model whose checkpoint releases act as a global forcing function" (§IV). In §III.b: producer and evaluator classes "are not permitted to collude".
- **Evidence:** 105 of 237 verdicts were same-model. antagonist→judge-consequence (both luna) happened 45 times, the most frequent pair. Only two foundation models serve all nine seats, although deepseek, gpt-5.6-sol and qwen sit unused on the menu.
- **Action:** FIX. Routing excludes judges on the author's model (the roadmap item 4 rule). Spread the evaluator tier over at least 3 model families. A provider change is not a model change.
- **Severity:** High.

### P7. Verdict grading depends on the charter's price-window clock, so a world configuration can silence it
- **Where:** `feedback.py:1414-1472` (`_verdict_window`: a verdict settles only when its price window is `closed` and the return is in `price_origins`), `pricing.py:222-246`.
- **ch2:** "Therefore the online evaluation is always active".
- **Evidence:** e5a ran with `novelty.window_ns = 3600s` for 60 ticks, so no window closed. The result was 0 `verdict.consequence`, 0 `verdict.unread` and 28 committed verdicts that never settled. The toml's current `window = "1m"` comment (lines 284-288) documents that this was patched by retuning the charter window. The fastloop runs also show 0.
- **Action:** FIX with C3. The judge's consequence grading should hang off the kernel's `return_paid_off` / opportunity horizon, not the charter's pricing window.
- **Severity:** Medium.

### P8. Two diverging inaction vocabularies
- **Where:** `feedback.py:98` (`QUIET_ACTIONS`, without "defer") vs `grounded.py:18` (`INACTION_ACTIONS`, with "defer").
- **ch2:** (hygiene; §III.b collusion via inconsistent measurement).
- **Evidence:** A `defer` counts as a commitment (`action:defer`) for the judge-evaluability rule but as inaction for opportunity pricing. Judges were told to answer unmeasured for exactly these (see the corpus reasons: "a routine defer ... commits to no trade").
- **Action:** DELETE `QUIET_ACTIONS` together with S1. If a list survives, keep one.
- **Severity:** Low.

### P9. Final grounded judge without a `verdict` field is settled conformity 0.0
- **Where:** `feedback.py:1828-1835`.
- **ch2:** "those verdicts are not strictly predefined at the level of the input".
- **Evidence:** A valid, cited `realized_consequence` finding that omits the separate `verdict` number is settled SETTLED 0.0, the worst grade, with no reviewer. It is a form rule posing as a grade.
- **Action:** FIX. Settle censored (no fact).
- **Severity:** Low-Medium.

---

## SMUGGLING (Class 1 / Class 2 content)

### S1. Kernel-predefined evaluability overrides judges (Class 2)
- **Where:**
  - `feedback.py:92-102` (`COMMITMENT_FIELDS`, `QUIET_ACTIONS`, `UNMEASURED_REASON_UNCOMMITTED`).
  - `feedback.py:554-581` (`_judged_commitment`).
  - `loop.py:1389-1394`: a judge's numeric verdict is discarded when the return lacks a field from a hard-coded list.
  - `loop.py:1317-1321`: the prompt instructs judges to answer "unmeasured".
- **ch2:** "a predefined array of statements about correctness (judge scores and thresholds)".
- **Evidence:** 39 kernel overrides plus about 220 judge "unmeasured" answers induced by the prompt. That is the dominant outcome of judge labour: the engineer's list decides what may be evaluated, before execution. Holds are already priced by the market (opportunity cost), so the rule is redundant as well as Class 2.
- **Action:** DELETE `COMMITMENT_FIELDS`, `QUIET_ACTIONS`, `_judged_commitment` and the "unmeasured" prompt clause. Keep a judge's ability to *decline* a commission (`DECLINED_DEFINITION`); that is the judge's choice, not the kernel's.
- **Blast radius:** 2 source files, 1 test.
- **Severity:** High.

### S2. "Malformed = objectively non-conforming = 0" gates turn form checks into grades
- **Where:** `loop.py:1370-1382` (judge), `loop.py:1616-1626` (meta), `feedback.py:1831` (P9).
- **ch2:** "a unit test is an assertion about rightness ... knowable before execution".
- **Evidence:** A schema or addressing failure is scored as the worst possible judgement and trains the router, so the kernel's form test *is* the grade. It produced every meta grade in the corpus (P2).
- **Action:** FIX. A malformed or refused judgement is charged its cost and settles censored (no fact). The charter's `well_formed_rate` card is the population's own place to price form.
- **Severity:** High (because of P2).

### S3. Prometheus / DAGMetric patterns inside the online evaluators
- **Where:**
  - The grounded-judge rubric at `loop.py:1288-1310`: norm-by-norm instructions, "Use contrary only when ...", and "Zero earnings ... is not contrary unless ...".
  - The meta rubrics at `loop.py:1563-1581`.
  - `forecast_example` at `loop.py:1202-1206`, a reference answer injected into the judge prompt.
  - `grounded.py:236-270` `parse_finding`, a yes/no decision graph fixing status→score (contrary ⇒ 0, supported ⇒ (0,1], unknown ⇒ no score).
- **ch2:** "conditional, discrete gates with LLMs attached" (on Prometheus/DeepEval).
- **Evidence:** The rubric is an engineer's predefinition of the good, and it is the text that drives C5's loss-excusing findings.
- **Action:** DELETE the rubric paragraphs and the status→score coupling. KEEP only evidence-integrity physics: cited refs must be refs that were supplied, and a numeric finding must cite an observed ref (`grounded.py:248-263`), because that is anti-fabrication, not a definition of rightness. Delete `forecast_example`.
- **Severity:** Medium-High.

### S4. Fixed endorsement threshold
- **Where:** `feedback.py:79-81` (`VERDICT_ENDORSEMENT = 0.8`), used at `feedback.py:1504`.
- **ch2:** "judge scores and thresholds built into the transition".
- **Evidence:** Exposure pays only when a verdict crosses 0.8. That is a hard threshold on judge scores.
- **Action:** FIX. Score the exposure continuously: the Brier of the verdict against the realized outcome, relative to the antagonist's own claim.
- **Severity:** Low-Medium.

### S5. A preproduction gate and an offline model screen at the boundary
- **Where:** `docs/architecture/edition5-roadmap.md` ("Testnet ... only once the harness says the loop closes"); `scripts/fastloop.py` (seeded fake venue, virtual clock, scorecard); `scripts/calibrate_seats.py:205-530` (expected-answer scenarios); toml line 136 ("the cheap route the local screen scored 100% on") chose the roster models.
- **ch2:** "An evaluation that runs in a CI/continuous deployment pipeline before deployment is ... legacy".
- **Evidence:** This is operator tooling, not factory code. But it is functioning as the SDLC stage gate, and the schema-conformance screen selected the two-model monoculture behind P6.
- **Action:** KEEP both as plumbing tests. REMOVE their gating role. Move the scorecard's metrics (learning signal, meta coverage, exposure win rate, same-model share) into online observations the factory's own evaluators read.
- **Severity:** Medium.

---

## UNPRESCRIBED (deletion candidates)

### U1. Fidelity objection → adjudication → challenge pipeline
- **Where:**
  - `factorylab/settlement/fidelity.py` (176 lines).
  - `feedback.py:178-318` (`_judging_seats`, `_measurement_owners`, `_queue_adjudication`, `_adjudication_for`, `_resolve_adjudication`, `_open_fidelity_challenge`).
  - `settle.py:392-440` (`record_objection`), `feedback.py:1477-1481`.
  - `loop.py:1222-1232,1342,1595`; the `Adjudication` receipts.
- **ch2:** §III's answer to overfitting is "the realized-consequence metric" plus subclasses "at war", not an adjudication protocol.
- **Evidence:** `fidelity.adjudicated` fired 0 times in 18 runs, and it can only trigger on the closed-window verdict path that never fires (P7). It is a designed Class 2 procedure (objection form → independent adjudicator selection → registration of a challenge).
- **Dependents:** 8 source files, 1 test. It also carries the charter's "fidelity" norm.
- **Action:** DELETE, pending the charter auditor's view of the fidelity norm (§IV). If the norm stays, it is judged by ordinary verdicts under C3's outside signal.
- **Severity:** Medium.

### U2. Sibling share: grades manufactured for verdicts nobody read
- **Where:** `feedback.py:518-548`; `worlds.py:331`; toml `sibling_share = 0.5`; `cortex/schematics.py:1760`.
- **ch2:** "an evaluation layer optimizes toward the same target as the producers".
- **Evidence:** 23 of 31 meta→judge grades in the corpus were sibling copies at 0.5× the representative's score. In runs before the architect-review #4 fix they crossed judges: in s5, meta decision-65 read judge-consequence's decision-8 and five other verdicts got 0.38, three of them judge-fidelity's. The same-judge copy survives. It pays a judge for unread work, turns the tier signal into a volume bonus and trains the Blum–Mansour judge router.
- **Action:** DELETE. Unread siblings settle censored, as cross-judge siblings already do.
- **Blast radius:** 3 source files, 3 tests.
- **Severity:** Medium-High.

---

## MISSING

### M1. The adversarial layer does not exist beyond one capped seat
- **Where:** `routing.py:649-664` (`_cap_adversarial`, ≤15% of router mass); the single `antagonist` seat (toml 275-283), producer-shaped, on luna.
- **ch2:** "internal and external ... acting like a chaos monkey"; "healthy mixes of mean-based and swap-based learners".
- **Evidence:**
  - No adversarial evaluators.
  - No mean/swap mix inside the adversarial layer (one seat, routed by the producer EXP3 router).
  - No external adversarial participants.
  - No chaos monkey in the delivery arm.
  - No constructed severity-0 faults.
  - Its lens scopes it to testing judges, and it never wins (P5).
- **Action:** BUILD at the Stackelberg move, with kernel physics bounding blast radius:
  - A chaos actuator that injects real faults: venue unavailability, stale mids, a malformed tool, a revoked key on a sandboxed rail.
  - Adversarial judges: seats paid when they expose another judge's miss against realized consequence.
  - Several antagonists with different learner types.
  - Real external traffic as adversarial pressure, such as a counterparty on the x402 service.
- **Severity:** High.

### M2. No online early-warning signals; ensemble disagreement is structurally impossible
- **Where:** `versioning/versions.py:251-297` (`early_warnings`: variance and lag-1 ACF at k, 2k, 4k) is called only from `versioning/report.py:102`, an offline diary report. `runtime/observations.py:112-118,224-228` (`evaluator_disagreement`) needs ≥2 judges per return, and only 6 of 231 were multi-judged. The online `immune.py` diagnoses stable failure and thrash (§II) but not EWS.
- **ch2:** "variance, autocorrelation, and ensemble disagreement ... cycle at every level of the dark stack".
- **Evidence:** No evaluator is shown or paid for EWS. §III.a's catastrophe watch is absent from the evaluation layer. Catastrophe handling exists only as kernel physics (kill / wind-down), which is correct but reactive.
- **Action:** BUILD:
  - Compute `early_warnings` online at each window close and publish it as observations to every tier.
  - Route a share of returns to ≥2 heterogeneous judges so ensemble disagreement exists.
  - Let evaluators forecast "regression within h" predicates that settle on realized drawdown/failure, so a flag pays only if a real failure followed (the essay's own example).
- **Severity:** High.

### M3. No invariant guarding evaluator majority, judge multiplicity or model diversity
- **Where:** `worlds.py` validation (no such checks); roster.
- **ch2:** "the general producer class and the general evaluator class are not permitted to collude".
- **Evidence:** C1 and P6 are roster accidents that the loader accepts. Nothing in the manifest validator rejects a world where evaluators are the minority, where one judge sees a return, or where judge and author share a model.
- **Action:** BUILD manifest validation that rejects these worlds. These are Stackelberg-level facts about the world.
- **Severity:** Medium.

---

## KEEP (prescribed by ch2 or kernel physics)

- **`return_paid_off` as a kernel predicate** (`settlement/vocabulary.py:213-216`, `settlement/consequence.py`, `lots.py`): the Stackelberg-fixed outside fact. KEEP and make it mandatory (C3).
- **`opportunity_cost`** (`grounded.py:301-345`): priced ex ante on the named declined trade, net of fees, with a bare hold at neutral. This is a real outside signal. KEEP; fix the P1 sign where it grades judges.
- **Cascade gates** (`cascade.py`): time plus completed-evidence separation between tiers. Consistent with recursive tiers and rate separation. KEEP, but raise throughput (C7).
- **Independence exclusions:** no self- or ancestor-judgement (`loop.py:830-888`) and a fresh final evaluator (`feedback.py:1660-1666,1762-1772`). KEEP, and extend to model identity (P6).
- **Evidence-citation integrity** (`grounded.py:248-263`, `observed_evidence_refs`): anti-fabrication physics. KEEP.
- **`_sampling_actuator`** (`feedback.py:1558-1596`): raising consequence sampling when verdicts rise while consequence skill falls is the §III.b anti-overfit reflex. KEEP after P1 and C3.
- **Declining a commission** (`DECLINED_DEFINITION`): the judge's own choice, cost-only. KEEP.
- **Settlement money path** (`consequence.py`, `lots.py`, `receipts.py`, `settle.py` scoring core, `forecast.py`): ledger integrity and custody. KEEP.

## Top 8, in fix order

1. **P1:** Flip the sign in `feedback.py:1729`. This is the only outside grading that fires, and it rewards wrong judges.
2. **P2 + S2:** A meta that names its own handle gets a kernel 0. All 8 meta grades in the corpus were this; realized consequence has never graded a meta.
3. **C3:** Realized consequence is subordinated to charter cards (`weights.py`) and made optional ("payoff privilege gone"). 221 of 237 verdicts carry no outside signal.
4. **C5:** The producer's "realized consequence" is an LLM compliance reading whose rubric excuses losses, falling back to the judge's opinion. That is the collusion channel.
5. **P3 + S1:** 72% of judge work is voided by a kernel evaluability list. The evaluator tier has learning death: 8 real grades in 18 runs.
6. **C1 + P6:** The evaluator population is the minority (46% of invocations, about 30% of compute), and 44% of verdicts are same-model.
7. **C2:** The judge tier is a swap-based (Blum–Mansour) core. The essay says evaluation layers must be mostly mean-based.
8. **M1 + P5:** The adversarial layer is one capped seat that has won 0 of 53 exposures. There is no chaos monkey, no adversarial judges and no external adversaries.
