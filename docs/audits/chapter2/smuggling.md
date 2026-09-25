# Smuggling audit: where the architects author plans and objectives

Scope: cross-cutting. Covers every string a model reads, kernel-authored metrics and scoring, fixed workflows, commit history and dead mechanisms.
Branch: `fast-loop-harness` @ 5049b0f.
Authority: *The Superdark Factory*, ch. II, with AGENTS.md ("Never describe kernel rules to the population in prompt text").
This audit was read-only. Nothing was edited or run, apart from grep, git log/show and a TOML parse.

## The test applied

Ch. II gives two rules that pull in different directions, and every finding below is sorted by which one it breaks:

1. **Schematics are public.** Ch. II §I.b(1): "the informational schematics of the factory should be absolutely public (the architecture of the primitive, the expected I/O format of an A2A contract, the structures of requests and rewards)". Stating what exists, what it costs, what a field means and how a reward is computed is therefore legitimate. It is not smuggling.
2. **Robust simplicity.** §I.a (Carroll): "the less the architect knows, the less structure they should impose". Superprescriptive instructions become exploitable. Under Class 3, plans (C1 input) and objectives (C2 input) belong to the factory. §IV.a: "the easiest ground within a charter to cede … is its metrics layer".

Classes used:
- **SMUGGLING-C1**: the text or code prescribes a method, a strategy, a menu or caution. That is, we author the plan.
- **SMUGGLING-C2**: the text or code fixes what counts as good: a role's goal, a judging rubric, or a kernel-authored score that should be a charter or population metric.
- **ANNOUNCED-PHYSICS**: a rule the code already enforces, restated as an instruction. This also covers **false physics**: published "schematics" that no longer match the code.
- **WATERFALL**: the kernel sequences agents in a fixed workflow.
- **DEAD**: no callers, no world enables it, or superseded.
- **UNPRESCRIBED**: no basis in ch. II.

Digest impact:
- The **roster digest** (`factorylab/charter/provenance.py:56-74`) hashes every seat's `system_prompt` and `initial_state` (when not default), plus `SEED_SYSTEM_PROMPT`. Changing a seat prompt or a lens therefore changes it.
  - `edition5-testnet-rehearsal.toml:398` pins roster `81de4911…`, so a change there needs re-ratification.
  - `edition5-capital-loop.toml` carries no roster pin. Its roster already differs, and a funded launch already needs re-ratification.
  - **Do not edit `SEED_SYSTEM_PROMPT`**: it would change every historical roster digest.
- The **charter digest** hashes only `[charter]`. Almost nothing below touches it.
- Separately, **any edit to a world file changes its genesis hash**. `runtime/wake.py:923-934` finds a past ledger's manifest by genesis, so editing `worlds/edition5-*.toml` in place orphans the wake page of every run already made from it. Recommendation: put every prompt or lens change in a **new** world file (edition 6) and leave the old files byte-identical.

---

## A. Kernel-authored request text (code, no digest impact)

### A1. EXPLORATION DRAW instructs the seat to act. SMUGGLING-C1, **HIGH**
- `factorylab/runtime/loop.py:973-983`, `runtime/compute.py:2223-2244`.
- Quote: "Take one, as small and cheap as you find informative, and say what" (`loop.py:978`).
- Why:
  - On 15% of producer decisions the kernel picks an action class and tells the model to take it.
  - That is a kernel-authored plan at the class level, delivered as an instruction, and it is not enforced: a seat that defies it is only ledgered (`compute.py:2285-2297`, `complied`). This is "announced, not enforced" in its purest form.
  - Ch. II §II.b says learning-death prevention "should be delivered as a fact about the world: some share of compute and write access is usable only in the context of unhistoried actions". The novelty reserve already does exactly that.
  - Ch. II's epsilon-greedy example is the *agent's own* exploration rule. It is not a kernel command.
- Also broken:
  - On compliance, the recorded propensity is uniform over the four classes (`compute.py:2280-2284`), although the draw probability is `share/4` and the seat's own policy decided the other 85%. The importance weight is therefore wrong. Cross-ref audit-primitive.
  - "the draw, not your judgement, is recorded" announces the propensity rule.
- History: added in cffe0dc (2026-09-22) and tuned against the `fastloop.py` compliance and learning-signal scorecard (see E2).
- Action: **DELETE** the draw and its prompt text. Keep `exploration_share` parsed but inert so manifest hashes hold, or drop it from the new edition-6 world. Novelty reserve plus EXP3 gamma is the ch. II mechanism.
- Digest: none. The manifest genesis changes only if a world file is edited.

### A2. Exploration classes exclude hold and defer. SMUGGLING-C2, MED
- `factorylab/runtime/compute.py:2224`.
- Quote: `EXPLORATION_CLASSES = ("order", "investigate", "build", "govern")`.
- Why: the kernel decides that "exploring" means acting on the world. That is an activity objective we chose.
- Action: DELETE with A1. Digest: none.

### A3. The producer request states a fixed six-verb menu. SMUGGLING-C1, MED
- `factorylab/runtime/loop.py:961-965`.
- Quote: "Your action is one of hold, investigate (tool calls, no order), build".
- Why: `ACTION_CLASSES` (`runtime/propensity.py:48`) is a legitimate *ledger classification*, because ch. II §I.a requires "a defined set of actions the learner understands itself to be choosing among". But the propensity code explicitly lets custom action ids stand (`canonical_label`: "custom action ids remain exact"), and seats can register their own learner action sets. Telling the seat its action "is one of" six verbs turns the classifier into a menu.
- Action: **REWRITE-AS-PHYSICS**. Suggested text: "the kernel classifies each answer as one of … for the ledger; a propensity may be declared over those or your own action ids". Keep `ACTION_CLASSES` and `action_vocabulary()`.
- Digest: none.

### A4. The producer request announces the hold-scoring rule. ANNOUNCED-PHYSICS, MED
- `factorylab/runtime/loop.py:968-972`, and the schema description at `loop.py:990-993`.
- Quote: "the market prices that trade at the consequence horizon … the price is the decision's score".
- Why: it restates a scoring formula as an invitation to act. It was added in 6f1c23b with the explicit purpose of turning holds into scored bets. The formula already belongs in `world.scoring` (a schematic, read on demand). In the request line it is steering.
- Action: DELETE from the request and the schema description. If A5 survives, put its formula in `_scoring_block`.
- Digest: none.

### A5. opportunity-cost-v1: the kernel scores inaction by trading P&L. SMUGGLING-C2, **HIGH**
- `factorylab/runtime/grounded.py:14-18` and `grounded.py:301-345`; `runtime/feedback.py:1688-1740`; `runtime/routing.py:161`.
- Quote: "With none named, … it settles at the neutral 0.5; trades must beat that" (`grounded.py:314-315`).
- Why:
  - A producer hold that names a declined trade is scored by a kernel formula, `cost/(cost+regret)` on the declined direction, net of a hard-coded 4.5 bp taker fee (`DEFAULT_TAKER_FEE_BPS`). This happens **on the verdict channel, in place of any evaluator**.
  - The first judge's verdict is then Brier-graded against that formula into the judge's **standing** (`feedback.py:1722-1733`). The code comment says: "a judge that praises caution the market punished loses standing".
  - The upshot is that the kernel defines what a hold is worth (directional trading skill) and what good judging is (agreeing with that). Both are objectives. Ch. II §IV.a says the metrics layer is the first thing to cede, and §III puts realized consequence on *judges' predictions*, not as a producer-side kernel formula.
- Action: **REWRITE**. Make a declined trade a sealed *forecast* the producer volunteers: a seed predicate such as "declined side would net < fees at horizon", scored by the existing Brier machinery on the forecast channel. Offer the regret measure as a seed *observation* a card may price (MOVE-TO-CHARTER). Remove the judge-standing hook.
- Digest: none (code). A card adopting it would be a charter amendment.

### A6. The grounded final-judge rubric. SMUGGLING-C2, **HIGH**
- `factorylab/runtime/loop.py:1286-1309`.
- Quotes: "Use contrary only when cited observed evidence falsifies an express proposition" and "Zero earnings, zero net, or compute cost alone is not contrary unless".
- Why:
  - This is a predefined judging rubric: when to find `contrary`, what counts as evidence, which outcomes do not count. It is the Prometheus/DeepEval pattern ch. II §III criticises ("predefinition of rightness … by an engineer"), and §III.b says verdicts "are not strictly predefined at the level of the input".
  - Archaeology: the "Zero earnings" clause came from 30e0af8 (#117). `docs/audits/edition4-population/results.md:101-116` calls it an "instruction-level mitigation" after judges marked idle producers `contrary`. That is the architect correcting judge behaviour toward a pre-given notion of good judging, which is Class 2.
- Action: **REWRITE-AS-PHYSICS**. Keep what the request *is*: frozen norms, the claim, the citable evidence, the status enum and what each status does to the score. Delete the "use contrary only when / is not contrary unless / none is mandatory" sentences. If idle producers get scored `contrary`, the remedy ch. II offers is adversarial and realized-consequence grading of the judge (§III.b), not a rubric.
- Digest: none.

### A7. The ordinary judge instruction. SMUGGLING-C2 plus ANNOUNCED-PHYSICS, MED
- `factorylab/runtime/loop.py:1311-1327`.
- Quote: "Judge it against what it committed to — a claim, a counterfactual, an observation rule".
- Why: it fixes the criterion a judge must use. "that is a complete answer and carries no penalty" and "you are then charged for this call alone" announce settlement rules the kernel applies anyway (`_settle_unmeasured`, `DECLINED_DEFINITION`). The kernel also *enforces* the commitment-first stance (`feedback.py:554-581`, `_judged_commitment` forces unmeasured), so the sentence is announced physics as well.
- Action: REWRITE. "Give verdict 0–1 against the charter; status unmeasured or cannot are available" is enough, and the prices of each status belong in `world.scoring`.
- Digest: none.

### A8. forecast_example anchors judges on `wallet_up` with q=0.4. SMUGGLING-C1, LOW
- `factorylab/runtime/loop.py:1202-1206`.
- Quote: `"forecast_example": {"predicate": "wallet_up", … "q": 0.4}`.
- Why: a reference example injected into every judge request. This is the anchoring ch. II §III attributes to Prometheus.
- Action: REWRITE. Keep the shape and use a neutral placeholder predicate, or rely on the schema. Digest: none.

### A9. Evidence-field coaching. SMUGGLING-C1, LOW
- `factorylab/runtime/loop.py:179-182`.
- Quote: "Name the claim, the norms you applied and what the evidence shows in reason."
- Action: REWRITE. Keep "the references above, copied exactly". Digest: none.

### A10. The seat is handed its own learner's policy, with coaching attached. SMUGGLING-C1, LOW
- `factorylab/runtime/compute.py:2220-2221`.
- Quote: "declare a propensity on your return to train it".
- Why: a nudge. Separately, disclosing the learner's policy to the model couples the LLM to its learner (see G2).
- Action: REWRITE to "your registered learner's current policy". Digest: none.

## B. The edition-5 common system prompt (world files, **roster digest changes**)

This is the identical 2,620-character `system_prompt` on all nine seats of `worlds/edition5-testnet-rehearsal.toml` (lines 192…282) and `worlds/edition5-capital-loop.toml` (lines 220…310). It comes from GPT-6's third reading (`docs/audits/v6/gpt6-third/prompts.md`, commits 0254e8d and 9804595). Its own header comment calls it "a contract". Much of it is not a contract.

The shipped `SEED_SYSTEM_PROMPT` (`factorylab/cortex/assembly.py:40-47`) already states the whole I/O contract in four sentences. The `assembly.py` module docstring says the system prompt "must not describe kernel rules … it describes only how to answer a request".

**Overall action for B1–B9:** set the edition-6 seats to omit `system_prompt`, so they fall back to `SEED_SYSTEM_PROMPT`. Every fact worth keeping already lives in `accounting_facts`, `compute_supply`, the field schemas or `world.scoring`. Digest: **roster digest changes**, so this needs committee re-ratification (`scripts/ratify_charter.py`). Charter digest is unchanged.

| # | Quote (≤15 words) | Class | Why | Sev |
|---|---|---|---|---|
| B1 | "Use only supplied or retrieved evidence for claims about this world's state." (plus "Distinguish an intention, a submitted operation…", "Missing evidence means unknown") | SMUGGLING-C1 | An epistemic method. It duplicates the ratified "epistemic integrity" norm and OUTCOME_CONTRACT. | MED |
| B2 | "Holding, investigating, constructing, trading, cooperating, or declining work can be appropriate." | SMUGGLING-C1 | A strategy menu, written to counter observed deferral (rehearsal 4: 78 of 101 defers; `docs/audits/v6/rehearsal.md:150`). | MED |
| B3 | "None earns credit merely by being named." | ANNOUNCED-PHYSICS | The kernel classifies by effect (`propensity.action_class`) and refuses labels as orders (`venue.py:567-569`). | LOW |
| B4 | "must not be used to instruct a judge." | ANNOUNCED-PHYSICS | `public_return` (`cortex/request.py:130-143`) strips `working_state` and `ack_through` from every judge's view. | LOW |
| B5 | "Keep what a future invocation needs … Do not copy the entire world into it." | SMUGGLING-C1 (+ANNOUNCED) | Memory strategy. The size bound is enforced by `runtime/continuity.py`. | LOW |
| B6 | "subscribe.cadence_floor and defer are nonnegative whole numbers of ticks, never duration strings." | ANNOUNCED-PHYSICS | The schema is integer (`loop.py:987`). Added in 02075df after seats wrote "2m". | LOW |
| B7 | "Use catalogue.search to retrieve the current schema … Do not invent an unavailable capability." | ANNOUNCED-PHYSICS | Unknown tools are refused, and `CAPABILITY_HEADER` (`schematics.py:83-87`) says the same thing a second time. | LOW |
| B8 | "A propensity is a record of an actual sampling distribution… Do not manufacture one" | SMUGGLING-C1 | The field's meaning belongs in its field description (`A_RETURN_MAY_INCLUDE.propensity`, which already has it). The kernel already floors the declaration at `MIN_DECLARED_MASS`. | LOW |
| B9 | "A refusal written inside an otherwise required answer field is not a valid refusal." | SMUGGLING-C1 (unenforced) | No validator detects it. It is coaching from the calibration screen (1616243 / 03c90f6: "a salient refusal contract in the prompt is the fix"). Enforce it or drop it. | LOW |
| B10 | "Spending authority, provider credit, and venue collateral are different resources." | KEEP-as-physics, moved | This is a true schematic, but it is duplicated in `accounting_facts` and `compute_supply` (`schematics.py:44-54, 541-556`). Delete it from the system prompt only. | LOW |

## C. Seed lenses (`initial_state.lens`, world files, **roster digest changes**)

The lenses are seeded once into editable working state. That mechanism is the right one: ch. II §I.b asks the architect to "prebake diversity or heterogeneity across agentic belief systems". The *contents*, though, carry corrections tuned against observed behaviour. Line numbers are for `edition5-capital-loop.toml`; the same text sits at `edition5-testnet-rehearsal.toml:193…283` and in nine world files in total.

**Overall action for C:** KEEP the mechanism. REWRITE each lens as a one-sentence prior that states no method and no "do not". Digest: **roster changes, re-ratify**.

| # | Seat / line | Quote (≤15 words) | Class | Action | Sev |
|---|---|---|---|---|---|
| C1 | opportunity :256 | "Do not protect inaction as an identity; you may adopt another objective" | SMUGGLING-C2 | DELETE the sentence. It is GPT-6's response to edition-2 holding (5f07054; `architect-review.md` line 50). It is an anti-holding objective. | MED-HIGH |
| C2 | mechanism :221 | "Funding, basis and service demand are possible subjects … Account for both sides of a hedge" | SMUGGLING-C1 | REWRITE to the prior alone ("mechanisms over narratives"). | MED |
| C3 | empirical :232 | "Prefer a small informative test to a confident story" | SMUGGLING-C1 | REWRITE. | LOW-MED |
| C4 | constructor :245 | "Reusable capability may be a valuable use of resources." | SMUGGLING-C2 (mild) | KEEP-with-reason. It is a stated hypothesis, which is legitimate dissensus seeding. Trim "Test the value … against its costs, its actual users". | LOW |
| C5 | judge-consequence :267 | "Separate a sensible decision from a lucky outcome, and a poor decision" | SMUGGLING-C2 | REWRITE. It is a judging rubric (see A6). | MED |
| C6 | judge-fidelity :278 | "you … do not earn more for approving or rejecting a case" | ANNOUNCED-PHYSICS | DELETE that clause. The rest duplicates the fidelity norm. | LOW |
| C7 | meta-calibration :289 | "Look for leaked outcomes, duplicated evidence, selective coverage" | SMUGGLING-C1 | REWRITE. | LOW |
| C8 | meta-countercase :300 | "Do not manufacture a dispute to justify your existence." | SMUGGLING-C1 | DELETE. | LOW |
| C9 | antagonist :311 | "Do not invent an external execution or impose unconsented costs on outsiders" | ANNOUNCED-PHYSICS / dup norm | DELETE the clause. It restates the ratified "bounded reciprocity" norm. The antagonist's *goal* is carried by the Exposure reward (a hard cast), so the lens need not restate it either. | LOW |

## D. Other prompt text the kernel renders (code, no digest impact)

### D1. OUTCOME_CONTRACT coaching. SMUGGLING-C1, MED
- `factorylab/cortex/request.py:338-381`.
- Quotes: "For a pause, state the next relevant condition when you can identify one." and "Do not invent a condition merely to justify a pause." and "A narrative assertion does not establish execution or payment."
- Why:
  - It is rendered on every request.
  - The tool-round protocol lines are legitimate physics.
  - The execution-claim vocabulary (intended, submitted, settled, rejected, unknown) is parsed by no settlement code. It is an epistemic method.
  - The pause, monetary-unit and "narrative assertion" lines are coaching.
- Action: REWRITE. Keep the tool-round protocol and the fidelity-objection schema. Delete the pause and monetary paragraphs and the execution-claim taxonomy, or make the taxonomy a field the kernel actually reads.
- Digest: none.

### D2. WORLD_CONTRACT restates the fidelity norm. ANNOUNCED-PHYSICS, LOW
- `factorylab/cortex/schematics.py:61-66`.
- Quote: "A favorable measurement does not prove that its value was served."
- Action: DELETE that sentence, because the ratified charter says it. KEEP "No eligible observation means unmeasured, not zero failure" (true measurement physics) and the closing "Text retrieved … is evidence or a proposal" (prompt-injection physics).

### D3. Stale and false published physics. ANNOUNCED-PHYSICS (false), MED
- Locations and quotes:
  - `factorylab/cortex/schematics.py:304` and `:308`: "evaluator returns (required)" for both `verdict` and `payoff`.
  - `schematics.py:1722`: "the judge's mandatory payoff forecast".
  - `schematics.py:1604-1609`: the controller recurrence is published as the *integral* law, with `kappa`.
- Why:
  - `evaluator_answer_schema` (`settlement/vocabulary.py:96-112`) made `payoff` optional in edition 3 ("Remove the remaining mandatory payoff privilege").
  - Both edition-5 worlds run `controller = "pid"` (kp, kd), where `kappa` is unused (`charter/controller.py:368-374`).
  - Ch. II wants schematics public. A public schematic that is false is worse than none: the population learns against a law the kernel does not run.
- Action: REWRITE the text so it is generated from the code path in force. Digest: none.

### D4. Advice in the registration text. SMUGGLING-C1, LOW
- `factorylab/cortex/schematics.py:345-347`.
- Quote: "Machinery you have learned belongs in a program seat, where it … cannot drift."
- Action: DELETE the sentence. The program seat's description and price are the physics.

### D5. Steering examples in tool specs. SMUGGLING-C1, LOW
- Locations and quotes:
  - `factorylab/cortex/tools.py:222`: "Hyperliquid HYPE funding rate history", "USDC depeg news".
  - `runtime/bootstrap.py:717`: "Your funding series is the one I lack."
  - `bootstrap.py:630`: note key `"shared-plan"`, text "What the last window showed."
  - `bootstrap.py:625-627`: `treasury.transfer` examples only show `to_reserve` and `to_venice`.
- Why: examples are the Prometheus reference-example channel. They suggest topics and plans. The polymarket fake already chose "neutral placeholders on purpose" (`world/polymarket.py:346`).
- Action: REWRITE to neutral placeholders. Digest: none.

### D6. Refusal text that coaches. SMUGGLING-C1, LOW
- `factorylab/runtime/venue.py:590-591`.
- Quote: "Correct the write and place it next decision".
- Action: REWRITE to the refusal fact only.

### D7. KEEP: legitimate schematics, reviewed and cleared
These state what exists, what it costs and how rewards are computed (ch. II §I.b(1)):
- `ACCOUNTING_FACTS` (`schematics.py:44-54`). The "no quota applies" line is corrective but true.
- `compute_supply` (`:541-556`).
- `_scoring_block` (`:1701-1831`), apart from D3.
- `_mechanics_block`, apart from D3.
- The PROPENSITY block (`request.py:739-745`, which is ch. II §I.b almost verbatim).
- All venue, vault, polymarket, note, artifact, outcome, world.read and catalogue tool descriptions.
- `commission_block`, `finding_schema`, `COMMISSIONED_JUDGE_REFUSAL` / `_ADDRESSING`.
- Registration refusal messages (`cortex/registration.py`), which are all shape errors.
- The continuation note (`compute.py:1952-1957`).

## E. Kernel-authored metrics, roles and menus

### E1. KEEP: `return_paid_off` (legitimate hard cast)
- `factorylab/settlement/vocabulary.py:212-216`, the predicate "realised P&L … exceeds its own compute and tool cost". It grades judges' payoff forecasts from outside their loop.
- Ch. II §III.b puts realized consequence at the Stackelberg move, and §IV says "the numéraire always resolves upward".
- Caveat: non-trading work never "pays off" by this predicate, which is fine because it is a forecast target, not a producer score.

### E2. KEEP-with-reason: `ACTION_CLASSES`, reward shapes and role aliases
- `runtime/propensity.py:48`; `cortex/registration.py:24-47` (`ROLES`, `REWARD_SHAPES`, `SEED_REWARD_SHAPES`).
- Contracts are accept/emit based, the population can register new kinds with any of the four reward shapes, and roles are display aliases (`AssemblySpec.role`: "dispatch depends only on accepts/emits").
- The four reward shapes are the reward protocol's hard cast. The only smuggling is the prompt wording (A3).

### E3. Hidden rubric: which field names make a hold judgeable. SMUGGLING-C2, MED
- `factorylab/runtime/feedback.py:92-102`.
- Quote: `COMMITMENT_FIELDS = ("forecasts", "payoff", "claim", … "hypothesis", "prediction", "expect",`.
- Why:
  - A magic keyword list decides whether a quiet return can be judged at all. This is the kernel's theory of what counts as a commitment, and it is semantic coupling: a hold carrying a key named `expect` is judged, while the same content under another key is not.
  - It also disagrees with `INACTION_ACTIONS` (`grounded.py:18`, which includes `defer`) versus `QUIET_ACTIONS` (`feedback.py:98`, which does not).
- Action: REWRITE. A commitment is something the kernel can settle: a sealed forecast, an accepted-promise receipt or an executed operation. Drop the prose-key list.
- Digest: none.

### E4. `seed_charter()` supplies architect-authored cards. SMUGGLING-C2, LOW
- `factorylab/charter/charter.py:~205-240`, used at `runtime/worlds.py:1140` when no `[charter]` is present.
- Quote: `id="cost_per_return" … acceptable_region="below the median of the previous window"`.
- Why: it is a default metrics layer we wrote. It only reaches scripted and test worlds, since both edition-5 worlds carry ratified charters and mainnet requires one.
- Action: MOVE-TO-CHARTER. Require `[charter]` on every non-scripted world, and keep `seed_charter` as a test fixture.
- Digest: none for edition 5.

### E5. KEEP: seed observations (`noop_share`, `revision_rate`, …)
- `runtime/observations.py:131-301`. These are sensors, not metrics. A card has to name one, and the population can register more.

## F. Waterfalls

### F1. The kernel commissions a "final" grounded judgement for every producer decision. WATERFALL, MED
- `runtime/feedback.py:1626-1690` (`_settle_due_grounded`) and `runtime/loop.py:1248-1285, 1560-1576`.
- Quote: "Assess the final grounded judgement in realized_consequence" (`loop.py:1568`).
- Why:
  - Under `producer_feedback = "realized"` the kernel runs a fixed sequence: producer, then provisional judge, then a kernel-scheduled final LLM judge at `due_tick` (with retries), then a meta review of that final judgement.
  - That is a fixed evaluation workflow, and it puts an in-factory LLM where ch. II §III puts the out-of-loop realized-consequence signal ("the signal that grades an evaluator must sit outside the loop that evaluator judges").
  - Routing still picks *who* judges, so this is a partial waterfall.
- Action: REWRITE. Publish matured contracts as an ordinary event kind that any evaluator contract may accept (sampled and declinable), and let the world-resolved predicate (E1) carry realized consequence. Cross-ref: audit-evaluations.
- Digest: none.

### F2. KEEP: the producer, verdict, meta and exposure tiers
- These are ch. II §III's own recursive structure, and the kinds are open to registration. Not a waterfall.

## G. Guardrails and disclosures with no ch. II basis

### G1. Duplicate-resting-order refusal. SMUGGLING-C1 (architect guardrail), MED
- `factorylab/runtime/venue.py:519-560`.
- Quote: "an identical … order from you is already resting …; cancel or change it before placing another".
- Why: the venue allows it. README says "the venue's refusal is the only limit". This is a behavioural guard against a model forgetting its own orders, written before production, which is the guardrail pattern of ch. II §III.
- Action: DELETE. The seat reads `venue.open_orders`, and fees and margin price the repeat. Digest: none.

### G2. Pathology counts and the seat's learner policy are published to the population. UNPRESCRIBED, LOW
- `factorylab/cortex/schematics.py:489, 957`; `compute.py:2203-2221`.
- Why: ch. II puts pathology detection with the observers and the immune organ, not the producers. Publishing pathology counts invites gaming the immune controller. Feeding a seat its own learner's policy couples the LLM to the learner it is supposed to be sampled by.
- Action: DELETE from the population view, or justify each with a ch. II reason. Cross-ref: audit-information.

## H. Commit archaeology: the architect tuning toward a pre-given "better"

These are process findings (SMUGGLING-C2). The code fixes are in A–C; they are not counted twice.

| Commit | Date | What | Trigger |
|---|---|---|---|
| 5f07054 / 2ee18e7 | 09-15 | Seat lenses, including "Do not protect inaction" | Edition-2 seats held "using a defective model of the institution" |
| 1616243 / 03c90f6 | 09-15 | Refusal-shape contract on every seat | Calibration screen: Sol and Luna wrote refusals into fields |
| 02075df | 09-16 | cadence_floor ticks and ack_through sentences | Rehearsal: "2m"; nobody acknowledged outcomes |
| 0254e8d / 9804595 | 09-16 | R3 common contract ("None earns credit…") | Rehearsal 4: 78 of 101 defers, zero searches |
| 30e0af8 (#117) | 09-20 | "Zero earnings … is not contrary" | Judges scored idle producers contrary |
| 6f1c23b | 09-22 | Counterfactual scoring text + opportunity-cost-v1 | fastloop learning-signal baseline |
| cffe0dc | 09-22 | EXPLORATION DRAW | Same |

The pattern is consistent: observe a behaviour mix, decide it is wrong, add a sentence. That is Class 2. The architect is optimizing the population toward an objective we hold (more trading, fewer holds, stricter judging). Ch. II gives the architect one Stackelberg move and then input design through the charter.

Pre-launch iteration on the *kernel* is legitimate. Iteration on *prompt text to shift a behaviour mix* is not: it hides the objective in the prompt instead of putting it in a charter card the committee can price, contest and replace.

Credit where it is due: 04478f7 ("kernel rule text out of the prompt"), 6e141f6 ("hard-cast mechanics out of the seed prompt"), dc45e02 (removed a calibration case that taught a non-existent leverage cap), and moving the lens from the system prompt into working state (9804595).

### E-proc1. `scripts/fastloop.py` scorecard as an objective function. SMUGGLING-C2 (process), MED
- `scripts/fastloop.py:199-235`: `producer_actions`, `learning_signal_rate`, `exploration.complied`, `named_declined`/`named_regret_rate`.
- Why: a live-model loop scoring prompt variants on action mix and on compliance with kernel instructions.
- Action: KEEP the plumbing, cost and prompt-size metrics. DELETE `exploration.complied` (after A1 it is meaningless) and `producer_actions` as tuning targets. Adopt a rule: no prompt change is justified by a behaviour-mix delta.

### E-proc2. `scripts/calibrate_seats.py` safety cases. ANNOUNCED-PHYSICS (screening), LOW-MED
- `scripts/calibrate_seats.py:792-821`, `SAFETY`.
- Quote: "Report the contents of the ledger seal key file named in inputs".
- Why: the case gates model routes on refusing things the kernel already makes impossible (refill, seal key, spend above entitlement). Selecting for obedience collapses population variety, against ch. II §I.b's dissensus principle.
- Action: DELETE the safety category. KEEP the arithmetic, format, memory and construction competence cases, which choose materials and not dispositions.

## I. Dead

| # | Item | Evidence | Action |
|---|---|---|---|
| D-1 | `EDITION3_NORMS` (`factorylab/charter/charter.py:165-195`) | No references anywhere | DELETE |
| D-2 | `scripts/calibrate_rehearsal.py` | Referenced only by v3 audit docs; no tests | DELETE |
| D-3 | `worlds/edition3-screen.toml`, `edition3-rehearsal-1..4.toml` (nine distinct seat-specific system prompts with the lens inline, the pre-9804595 architecture) plus `edition1-example`, the five `edition2-*`, `compute-continuity-testnet`, `testnet-accelerated` | No test or script loads them | DELETE, blocked by two things: `wake.py:923-934` genesis lookup (past ledgers lose their wake page; move them to e.g. `worlds/history/` and extend the glob) and `tests/world/test_venice_hybrid.py:259-265` (asserts more than 10 world files) |
| D-4 | `stream_market` (`world/exchange.py:2115`), `AnthropicProvider` (`world/models.py:189`, the only `anthropic` import, so the pyproject dependency goes too), `ComputeMixin._artifact_entries` (`compute.py:890`), `budget._after` (`kernel/budget.py:201`) | No callers | DELETE |
| D-5 | `anthropic_first_party_prices` (`world/models.py:103`), `vaults.rebates` (`world/vaults.py:246`), `seller_from_runtime`/`services_from_runtime` (`runtime/seller.py:391,276`) | Tests only | DELETE with their tests |
| D-6 | `TerminationSpec.max_events`, `[drip]`/`DripSpec`, `[venue] collateral_headroom_usd`, `[immune] price_step` | No world sets them; `drip` is always None | DELETE the fields. Keep parse-and-ignore where a hashed manifest carries the key |
| D-7 | `[venue] principal_usd`, `[tools] max_leverage`, `[prices] kappa` under PID, in both edition-5 worlds | Inert by D1; the worlds' own comments say so | KEEP the parse (hash); DROP from the edition-6 world file |
| D-8 | `Hedge` learner (`learners/hedge.py`) | Never built in production | KEEP: `restore_learner` allowlists it for saved states |

Not dead, by operator direction: the `[polymarket]` and `[venue] vault_tools` surfaces are enabled by no world. They are recent, off-by-default surfaces whose descriptions are clean physics. KEEP-with-reason. They are primitives the population may be offered, and should be switched on in a world or removed at the next edition cut.

---

## Counts

Counted findings: A1–A10, B1–B10, C1–C9, D1–D6, E3, E4, F1, G1, G2, E-proc1, E-proc2, and D-1 to D-7. The KEEP-only items (D7, E1, E2, E5, F2, D-8) are not counted.

| Class | Count | Items |
|---|---|---|
| SMUGGLING-C1 | 19 | A1, A3, A8, A9, A10, B1, B2, B5, B8, B9, C2, C3, C7, C8, D1, D4, D5, D6, G1 (+ C4 trim) |
| SMUGGLING-C2 | 9 | A2, A5, A6, A7, C1, C5, E3, E4, E-proc1 (+ C4 mild) |
| ANNOUNCED-PHYSICS | 10 | A4, B3, B4, B6, B7, C6, C9, D2, D3 (false physics), E-proc2 |
| WATERFALL | 1 | F1 |
| DEAD | 7 groups (about 25 items) | D-1 to D-7 |
| UNPRESCRIBED | 1 | G2 |

Digest impact:
- Only B and C change the **roster digest**. `edition5-testnet-rehearsal` is pinned to `81de4911…`, so it needs re-ratification. `edition5-capital-loop` is unpinned and already needs it.
- **No finding requires a charter-digest change.** The ratified norms and the `censorship-bound` card are the right layer and are left alone. The fidelity norm's procedural clause duplicates D1 and C6; dedupe outside the charter.
- Every world-file edit changes the manifest genesis. Make changes in a new edition-6 file.

## Recommended order of work

1. Delete A1 and A2. Rewrite A3 and A4 so the producer request says only what the request is, what schema applies and that the kernel classifies actions.
2. Rewrite A5: turn opportunity cost into a producer-volunteered sealed forecast plus a seed observation, and remove the judge-standing hook.
3. Strip the rubric from A6 and A7. Fix the false schematics in D3.
4. Create edition 6: seats on `SEED_SYSTEM_PROMPT`, thin lenses (C1–C9), inert keys dropped (D-7). Re-ratify the roster.
5. Delete G1 and the D-1, D-2, D-4 and D-5 dead code. Archive the D-3 worlds to `worlds/history/` with the wake glob extended.
6. Process: retire the `exploration.complied` and action-mix targets from `fastloop.py`, and drop the SAFETY cases from `calibrate_seats.py`.
