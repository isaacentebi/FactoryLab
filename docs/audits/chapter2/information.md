# Audit: Information Design (ch2 §I.b, lines 93–124) against FactoryLab `fast-loop-harness` @ 5049b0f

Auditor slice: information control, clean-context judges, contracts, protocol, the Stackelberg disclosure commitment, the rich request channel, the thin reward channel, public schematics and private state, minimal sufficient disclosure, the obedience constraint, and dissensus.

This audit was read-only. The only thing I ran was two offline probes in the scratchpad (`audit/probe/probe.py`, `probe2.py`). They use the scripted `PolicyProvider` on the fake venue with the default edition-4 rehearsal world (`address_enabled = true`, `exploration_share` forced to 0.5). No key files were read and no network was touched. Rendered prompts are in `audit/probe/prompts.json`.

---

## 0. Measurements

**Prompt size (live scorecards, 17 runs in `work/fastloop/live-*`, plus `work/population-e5a`)**
- Producer prompts: median 28–31 KB per run. Evaluators: 19.5–23 KB. Metas: 16–30 KB.
- e5a averages over 97 invocations, section by section:
  - stable_prefix 10.1 KB
  - YOU 6.9 KB
  - INPUTS 3.9 KB
  - WORLD UPDATE 2.9 KB
  - OUTCOME CONTRACT 1.7 KB, a fixed text sent on every request
  - OUTCOME SCHEMA 1.3 KB
  - total 27.7 KB
- One probed judge prompt came to 21.2 KB including the system prompt. The thing being judged (`inputs.producer`) is 1.3 KB of that, about 6%. 841 B of those 1.3 KB are the kernel's own boilerplate instructions to the producer. The actual input plus output under judgement is about 470 B.

**Behaviour (618 producer, 506 evaluator and 24 meta invocations across the live runs)**
- Producer actions are 85–100% `hold`/`defer` in every run.
- Assembly learners were registered in 0 runs, so `your_action_policy` is `null` in 100% of prompts.
- 126 propensity declarations were refused. Top reasons: non-finite numbers (26) and not summing to one (15). About 40 refusals quote a judge's verdict value, e.g. "(verdict:0.8)".
- Exploration draws were obeyed 10 times out of 23 (43%). Every `order` draw produced a `malformed` return.

**Channel use across all 18 ledgers**
- 0 `address.send` calls and 0 `note.put`/`note.get`/`note.list` calls.
- `outcome.get` was called about 500 times. It is the dominant tool, so the inbox is the live reward channel.
- `catalogue.search` and `world.read` are the other schematic reads in use.

**Breadth (offline probe 2)**
- The world block carries 55 keys, including a `seats` list with 9 rows, one per seat. Each row holds `open_commitments` (the seat's open handles and sealed forecasts with `q`), `spending_authority`, `subscription` and `directory`.
- `_allowed_tools(judge) == set(tool_specs)` is True: a judge has every tool, including `note.*` and `address.send`.

---

## 1. Per-channel verdicts

| # | Communication path | Where | Does ch2 prescribe it? | Verdict |
|---|---|---|---|---|
| 1 | `address.send` (direct messages) | `runtime/address.py`, `compute.py:1161-1235`, `request.py:27-160` | No. It is a third channel beside request and reward: author-bearing, unscored, not a contract. | **DELETE** |
| 2 | Public notebook `note.put/get/list` | `runtime/notes.py`, world `notes`, directory | No. It is a population-wide blackboard, neither a contract nor private memory. | **DELETE** (keep the byte-day rent rate for working state) |
| 3 | `artifact.get` | `compute.py:1364-1376`, `kernel/artifacts.py:245-301` | Yes as private memory and addressable evidence (persistent handles) | **KEEP** |
| 4 | `artifact.list` and WORLD UPDATE `shared_directory` | `compute.py:858-957`, `schematics.py:1530-1546` | No, and it leaks private-state metadata | **RESHAPE**: own rows only (public ones too, if any exist) |
| 5 | Artifact `public` flag / publication | `artifacts.py:120-150` | Nothing ever sets `public=True` | **DELETE** (dead code) |
| 6 | Stable prefix, INSTITUTIONS, capability index, `world.read`, `catalogue.search` | `schematics.py:690-886`, `request.py:689-707` | Yes: "informational schematics of the factory should be absolutely public" | **KEEP**, and shrink (P2, S3) |
| 7 | WORLD UPDATE (charter, catalogue diff, observations, freshness) | `schematics.py:1048-1067`, `request.py:641-668` | Mostly yes: world facts and public schematics | **RESHAPE**: drop `pathologies` and the private directory rows |
| 8 | `since_you_last_woke` (per-seat fold) | `subscriptions.py:436`, `loop.py:917-922` | Yes: a coalesced, seat-controlled sensory update, i.e. minimal disclosure | **KEEP** |
| 9 | `your_state` / `working_state` | `continuity.py:148-221` | Yes: "memory that persists across rounds", private | **KEEP** |
| 10 | `unread_outcomes` / outcome inbox / `outcome.get` / `ack_through` | `continuity.py:223-600`, `feedback.py:699-743` | Yes: persistent handle plus stateful queue | **KEEP**, reshaped thin: no messages, no prose reasons |
| 11 | `your_action_policy` (the seat's own learner policy) | `compute.py:2201-2223`, `loop.py:942,1212,1529` | Not a privacy breach, but it discloses a distribution rather than a draw, which invites fabricated propensity | **RESHAPE** (disclose a sampled recommendation plus its p) or **DELETE** |
| 12 | Propensity declaration and forward travel | `request.py:410-455,752-758`, `propensity.py`, `compute.py:2247-2311` | Yes: the sole prescribed exception to privacy | **KEEP**, with fixes (S1, M1, M2, P3, P8) |
| 13 | Exploration draw disclosed as a directive | `loop.py:973-983`, `compute.py:2226-2289` | No: the kernel picks the action class for the agent | **DELETE** (from the kernel) |
| 14 | Requests / children (`_check_child`, `_invoke_child`) | `assembly.py:832-838`, `compute.py:2347-2466`, `loop.py:1123-1169` | Yes: the rich, author-neutral A2A request | **KEEP**; **BUILD** the propensity forward (M1) |
| 15 | Judge payloads (first tier) | `loop.py:1060-1104,1171-1300` | Yes: a clean-context input/output view | **RESHAPE** (C1, C2, C6, P5, P8) |
| 16 | Meta-judge payloads | `loop.py:1495-1560` | Yes as judges, but they get the full world block | **RESHAPE** (C7) |
| 17 | `registration_feedback` / `return_feedback` (global deque of 8) | `schematics.py:494-497`, `bootstrap.py:792` | No: broadcasts per-decision feedback to everyone | **RESHAPE**: address to the owner's inbox; delete the broadcast |
| 18 | `custody_view` / `account` / `pots` | `runtime/custody.py`, `schematics.py:404-515` | Yes: the shared fund is the world, not a seat's local state | **KEEP** |
| 19 | `catalogue` (every live id with accepts/emits) | `schematics.py:964-1007` | Yes: the public schematics of contracts | **KEEP** |
| 20 | Program-seat stdin | `assembly.py:332-341` | No: it passes every seat's private row | **RESHAPE** (C3) |
| 21 | `your_consequence_standing` to judges | `loop.py:1211`, `feedback.py:684-697` | No: gaming signal, and it includes the router's weight | **DELETE** from the prompt |
| 22 | `TransferIntent` event `by: action_id` | `compute.py:1491-1501` | No: an author name on a public event | **RESHAPE** (handle only) |

---

## 2. Findings

Severity scale: HIGH, MED, LOW.

### CONTRADICTS: the code does the opposite of ch2

**C1. Judges can identify the antagonist. Severity: HIGH.**
- Where: `runtime/loop.py:928-937` (adversarial clause added to `description`), `loop.py:1088-1104` (`description` copied into the judged payload), `loop.py:1188-1196` (the evaluator receives `producer.description`), and `cortex/request.py:130-143` (`public_return` keeps `payoff`).
- ch2: "the author here should be either irrelevant, or fungible, or private"; the judge should "simply look at it like a machine: input, output."
- Evidence: in probe prompt #2 the evaluator reads `producer.description = "…You may include payoff: … it is sealed as your forecast about your own return. …"`. Only Exposure/antagonist seats get this clause. Their `outputs.payoff` (the self-forecast) is also visible to the judge, as an anchor and a second tell. The antagonist exists to test whether judges approve unsupported work, and this leak defeats that test.
- Action: FIX. Give the judge an event-neutral description (the event plus the outcome schema, with no role clauses), and strip `payoff` from judged outputs, since it is already sealed separately.

**C2. Grounded judges and metas see the producer's name. Severity: HIGH.**
- Where:
  - `loop.py:1264-1268` and `loop.py:122-126`: `observation_contract` includes `producer_id`.
  - `feedback.py:1673-1686`: the grounded commission payload carries `contract` (with `producer_id`, `initial_evaluators` and `final_evaluators`) and `excluded_evaluators`.
  - For generic kinds, that payload is forwarded whole as `inputs.event`: `loop.py:1286-1288`.
- ch2: the judge "should never have full context on the internal operations of the assembly".
- Evidence: the assembly id travels in `realized_consequence.observation_contract.producer_id`. The docstring at `compute.py:1700-1703` claims "the judge of this return never sees the name".
- Action: FIX. Drop `producer_id`, the evaluator lists and `excluded_evaluators` from every judge-facing projection. Keep them on the ledger.

**C3. Program seats read every seat's private account. Severity: HIGH.**
- Where: `cortex/assembly.py:332-341`. `build_stdin` passes `req.inputs` whole. `compute.py:1693-1697` says so on purpose: "Keep their inputs whole".
- ch2: "the local state of a given agent … should be absolutely private"; "universal read access is the transparency collapse just described".
- Evidence (probe 2): the world block has 9 `seats` rows. Prompt rendering selects only the seat's own row (`request.py:559-561`), but program seats get all 9. That includes other seats' `open_commitments.sealed_forecasts[].q` (judges' unresolved forecasts), spend, runway, subscription and the seat directory. A population-registered program can therefore read judges' pending forecasts and producers' open handles.
- Action: FIX. Apply the prompt's partition (own row only, plus `SEAT_WORLD_KEYS` filtering) before building stdin. Better still, build the world block per seat.

**C4. Private artifacts are listed by owner, kind and size. Severity: MED.**
- Where: `compute.py:858-957` (`artifact.list`, unscoped `_artifact_entries`), `schematics.py:1530-1546` (`shared_directory`, inside every WORLD UPDATE), `schematics.py:1146-1157`.
- ch2: local state, including "running scores, history", private. The archive's own contract, `artifacts.py:278-281`, says a scoped-out reader "is told … nothing about its bytes, its size or its owner".
- Evidence: the probe's WORLD UPDATE lists `{"kind":"outcome.item","owner":"antagonist","public":false,…}` and `{"kind":"working.state","owner":"empirical","bytes":480}`. Every seat therefore sees:
  - when each other seat is receiving rewards (the `outcome.item` rows);
  - how large each seat's memory is;
  - each row's sha. For small, low-entropy bodies, the sha is at least a theoretical guess-and-check oracle.
- Action: FIX. List only the caller's own rows plus public ones. Remove private rows from `shared_directory`.

**C5. `return_feedback` broadcasts other seats' decisions and verdicts. Severity: MED.**
- Where: `bootstrap.py:792` (a global `deque(maxlen=8)`), rendered to every seat by `schematics.py:494-497`. Appended from:
  - `compute.py:2308` (propensity refusals)
  - `loop.py:827,855` (judgements)
  - `governance.py:133,219` (commissioned-judge refusals and registrations)
  - `venue.py:254` (orders)
  - `subscriptions.py:729` (subscriptions)
- ch2: reward "must find its way back to the exact decision"; local state private.
- Evidence: across the runs, the refusal reasons include "propensity must include the action taken (verdict:0.8)" and similar verdict values (about 40 of them), plus action labels such as `buy:BTC:xs` and order-refusal texts. Each goes to all seats, author-stripped but timing-correlatable.
- Action: FIX. Deliver each reason to the owner's inbox by handle (`venue.py:257` already does this for orders). Delete the world-block broadcast. The validator's rules belong in the public schematics, not in replays of other seats' mistakes.

**C6. The clean-context judge can be contaminated through population channels. Severity: HIGH while addressing is on.**
- Where:
  - `compute.py:1263-1265`: every seat gets every tool, judges included (probe 2: True).
  - The evaluator's `unread_outcomes` comes from `outcomes.unread` (`loop.py:1209-1210`).
  - Message items index `from` (`continuity.py:75`).
  - `address.prepare` has no restriction on the recipient's role (`address.py:124-134`).
- ch2: the judge "should never have full context"; Yan's reviewer shares "no context whatsoever".
- Evidence: a producer can `address.send` its judge ("I'm X; my return is sound"), or `note.put` a key the judge's `note.get` reads. About 160 lines of redaction in `request.py` hide message bodies from the judge's payload, while the judge's own inbox delivers them. Addressing is enabled in `edition5-capital-loop.toml:359`, `edition5-testnet-rehearsal.toml:331` and the pr121 rehearsal world.
- Action: FIX. Deleting U1 and U2 removes both vectors. Otherwise, judges' allowed tools must exclude population-authored channels.

**C7. Meta judges get the full world block, while first-tier judges get the machine view. Severity: MED/LOW.**
- Where: `loop.py:1525-1527` versus `loop.py:1239-1249` (`_operating_context`).
- ch2: a judge should "simply look at it like a machine: input, output."
- Evidence: in live runs meta prompts reach 29–30 KB. The whole population world, custody and directory arrive alongside the verdict under review.
- Action: FIX. Use the same `_operating_context` projection for metas and ballots.

**C8. `TransferIntent` events carry the author's seat id. Severity: LOW.**
- Where: `compute.py:1491-1501` (`"by": action_id`, emitted to any subscriber).
- ch2: the author should be "irrelevant, or fungible, or private".
- Action: FIX. Carry the handle only.

### UNPRESCRIBED: deletion candidates

**U1. `address.send` (direct messages). Severity: HIGH. Action: DELETE.**
- ch2: the protocol has "two major communication channels—a request channel and a reward channel".
- Argument:
  - ch2 prescribes two channels. The rich one is author-neutral and answered in kind. The thin one is a score plus propensity on a persistent handle.
  - `address.send` is neither. It names its sender (`"from"` in the item, `address.py:189-191`, indexed by `continuity.py:75`), is free-form rather than a contract, scores nothing, and rides on the reward inbox.
  - It is exactly the implicit dependency that the contract does not capture, which is semantic coupling, plus a bilateral side channel that enables collusion and C6.
  - It has been used 0 times in 18 ledgers.
- Dependents and blast radius:
  - `runtime/address.py` (191 lines)
  - `compute.py:_address_send` (lines 1161-1235) and its dispatch in `_run_tool`, plus the `ADDRESS_TOOL` redaction at `compute.py:1858-1859`
  - `cortex/request.py:27-160`: `ADDRESS_TOOL`, `ADDRESS_BODY_FIELDS`, `_project`, `_redact_projected_body`, `public_tool_calls`, `public_child_inputs`. `public_return` shrinks to its field filter.
  - `bootstrap.py:708-716`
  - `worlds.py:150-154` and `604-606` (`address_enabled`)
  - `continuity.py:75` (`"from"`) and the `outcome.list` description ("outcome and message index")
  - Scripts: `edition4_observer.py`, `edition4_nonfinancial_probe.py`, `edition4_rehearsal.py`, `edition4_report.py`
  - Tests: `test_address.py` (230), `test_address_integration.py` (221), `test_address_privacy.py` (211), plus parts of `test_prompt_modes.py`, `test_edition4_rehearsal.py` and `test_edition4_report.py`
  - World TOMLs that set `address_enabled = true`: capital-loop, testnet-rehearsal and pr121. Removing the key changes those manifests' hashes, so check any charter or roster digest ratified against them.

**U2. Public notebook `note.put/get/list`. Severity: MED. Action: DELETE.**
- ch2: "universal read access is the transparency collapse just described"; the judge shares no context.
- Argument:
  - Private memory is already provided by `working_state`.
  - Public, self-describing contribution is already provided by registrations (tool/program/observation/service). These are the prescribed public schematics: contracts, not prose.
  - The notebook is a population-wide blackboard with owner attribution (`notes.py:118-123`, `schematics.py:1532-1535`). It is broadcast shared context, not co-writer context (Yan's co-writers are one stream).
  - It is a judge-contamination vector (C6).
  - It has been used 0 times in 18 ledgers.
- Blast radius:
  - `runtime/notes.py` (261 lines)
  - Rent collection in `compute.py`, `pricing.py` and `world/treasury.py`
  - World keys `notes`, `directory.notes` and `shared_directory.notes`
  - `wake.py` counts
  - `NotesSpec` in `worlds.py:30,504,639,1097-1104`
  - Tests: `test_r3_k_notes.py`, `test_discovery_continuation.py`, `test_context_retrieval.py`
- Keep: `NotesSpec.micro_per_byte_day` also prices working-state rent (`continuity.py:49-52`). Rename it to a storage-rent parameter rather than delete it.

**U3. Artifact publication and the unscoped `artifact.list`. Severity: LOW. Action: DELETE the public path; RESHAPE the list.**
- ch2: "private state… with the sole exception of … the propensity score".
- Evidence: no caller passes `public=True`. `artifact.list` exists to make "shared memory findable", but no shared memory is ever published; all it does is C4.

**U4. `pathologies` labels shown to seats. Severity: LOW. Action: DELETE from seat views; keep for the observer.**
- Where: `schematics.py:489,957`.
- ch2: "Overdisclosure hands a given agent signals that it will either overfit to or game".
- Evidence: the architect's own diagnosis of the population (`learning_death`, `stable_failure`, `thrash`) is broadcast into every WORLD UPDATE.

**U5. `your_action_policy: null` rendered in every prompt. Severity: LOW. Action: DELETE the key when there is no learner.**
- Where: `loop.py:942,1212,1529`.
- Evidence: null in 100% of prompts across all runs. It is dead bytes, and a standing hint to register a learner. See P3 for the substantive problem.

### MISSING: prescribed but absent or stubbed

**M1. Propensity is not forwarded on A2A (child) requests. Severity: MED. Action: BUILD.**
- Where: `ChildRequest` has no propensity field (`request.py:810-822`). Children are opened with a degenerate `PropensityRecord((target,),(1.,)…,"parent-selected")` (`compute.py:2378-2384`, `loop.py:1145-1149`).
- ch2: "By producing propensity as a public part of a request".
- Evidence: the one A2A request the population can author carries no account of the alternatives: which other targets, or doing the work itself. The delegation choice can never be priced counterfactually.
- Action: add an optional `propensity` (with `chosen`) to `ChildRequest`, validate it with `validate_propensity`, and record it on the child handle in place of `parent-selected`.

**M2. Forwarded propensity has no consumer that prices the alternatives. Severity: HIGH. Action: BUILD / FIX.**
- ch2: the propensity lets a recipient "reconstruct the potential value of an alternative choice".
- Evidence:
  - The judge receives the propensity three times (P8), but its answer schema (`evaluator_answer_schema`) has no slot for valuing roads not taken.
  - The declared propensity trains only assembly learners (`feedback.py:1940-1983`), and 0 runs register one.
  - The only counterfactual that is ever priced is the kernel's single "trade you declined" (`counterfactual {coin, side}`), which is world-specific (see S4).
  - So the reward channel is structurally "score plus propensity", but the swap-regret path never fires. The no-swap-regret core (Blum–Mansour) is unreachable in practice.
- Action: give the reward line a counterfactual consumer. Either the judge prices the listed alternatives (a `{action: value}` slot bounded by the declared support), or the kernel always attaches an importance-weighted reward to the declaring seat's own inbox and queue, so that an in-context learner (the seat) or a registered learner can use it.

**M3. "Respond in kind" is only partial. Severity: LOW. Action: BUILD (optional).**
- ch2: "Any agent who receives a request should respond in kind".
- Evidence: a child's answer returns to its parent as a bare output (`compute.py:2464-2466`). It is not a self-describing request, and there is no convention for a child to carry a follow-on request (with propensity) back.

### PATHOLOGY: failure modes ch2 names

**P1. Homogenized disclosure consistent with transparency collapse. Severity: MED.**
- ch2: "actively spread dissensus … across the agent population"; "everyone converges, and the factory is worth V".
- Evidence:
  - All 9 seats receive a byte-identical stable prefix (5.4–13.7 KB), the same OUTCOME CONTRACT (1.7 KB), the same 2620-character system prompt (`edition5-capital-loop.toml`) and the same WORLD UPDATE.
  - Heterogeneity comes only from a 4–5 sentence private `initial_state.lens`, which is good and prescribed, and from the model mix.
  - Outcome: 85–100% `hold`/`defer` in every live run.
  - This is consistent with convergence, not proof of it.
- Action: FIX. Shrink the shared text (S3). Seed dissensus through disclosure itself, e.g. differing observation subsets or lenses carried as private state, not a common prefix.

**P2. Overdisclosure: the judged content is about 6% of a judge's prompt. Severity: MED.**
- ch2: "rule of minimal sufficient disclosure".
- Evidence:
  - The judge prompt is 21 KB, of which `inputs.producer` is 1.3 KB.
  - The judge's YOU block (3.7 KB of spending authority, provider inventory, subscription and runway) and a 5.4 KB capability prefix dominate.
  - Producers receive about 30 capabilities and use about 10; `outcome.get`, `catalogue.search` and `world.read` account for most calls.
- Action: FIX. The judge view should be commission plus input plus output plus propensity plus evidence. Everything else stays behind `world.read`/`catalogue.search`.

**P3. `your_action_policy` invites fabricated propensity, and the propensity is advertised to the judge. Severity: MED (latent).**
- Where: `compute.py:157-176` (`_publishable`, engineered "so an agent that copies a published policy verbatim into its return" validates) and `compute.py:2217-2223` ("declare a propensity on your return to train it").
- ch2: propensity is "an agent's own accounting of the statistical field it drew from".
- Evidence:
  - The seat is handed a distribution, not a sample. An LLM that copies it and then picks by judgement declares a behaviour policy it did not follow, so the importance ratio is 1 and the off-policy correction in `_open_assembly_round` is silently wrong.
  - This contradicts the seed prompt's own "Do not manufacture one retrospectively".
  - Separately, `A_RETURN_MAY_INCLUDE.propensity` (`schematics.py:320-329`) tells the seat "the judges of this return read it". That turns the accounting into a persuasion signal aimed at the judge.
- Action: FIX. When a learner exists, the kernel samples from it and discloses `{recommended, p}`. If the seat obeys, record the learner's p; otherwise record the seat's own. Remove the "judges read it" sentence.

**P4. The exploration draw fails the obedience constraint. Severity: MED.**
- ch2: "otherwise, an autonomous agent may simply ignore it".
- Evidence:
  - Compliance was 10 of 23 (43%).
  - `order` draws were 100% `malformed`, and `investigate` draws resolved to `hold`.
  - The draw text also reaches the judge through `description`, telling the judge that the decision was kernel-forced.
- Action: see S2 (DELETE).

**P5. Judges see their own standing and the router's selection weight. Severity: LOW/MED.**
- Where: `loop.py:1211`, `feedback.py:684-697`.
- ch2: "Overdisclosure hands a given agent signals that it will either overfit to or game".
- Evidence: `selection_weight` is the judge router's local state, not the judge's. The probe shows `{"payoff_skill":0.09,"selection_weight":0.59,…}` in the judge's INPUTS.
- Action: DELETE from the prompt. The thin per-handle rewards in the inbox are the prescribed feedback.

**P6. Rich reward. Severity: LOW/MED.**
- Where: `feedback.py:730-743`. The grounded final finding delivered to the producer's inbox carries free-text `reason` and `evidence`.
- ch2: "In this sense, the reward should always be a score"; "a learner given too much context will form dependencies on arbitrary signals".
- Action: RESHAPE to `{status, score}` on the handle. The reason stays on the ledger and in the Verdict event for metas.

**P7. Persistent judge memory erodes clean context over rounds. Severity: LOW.**
- Where: `loop.py:1208`. Judges carry `working_state` across commissions. Combined with C1 and C2, a judge can accumulate per-producer history.
- ch2: the reviewer is "freed from the coder's accumulated history".
- Action: consider stateless judging commissions, or forbid producer identifiers in judge state. The fix for C1 and C2 makes this mostly moot.

**P8. The propensity is shown three times to the judge. Severity: LOW.**
- Where: `outputs.propensity` (from `public_return`), `producer.propensity` (`loop.py:1195`) and the PROPENSITY block (`request.py:752-758`).
- ch2: "minimal sufficient disclosure".
- Action: FIX. Keep only the PROPENSITY block, which is the prescribed "directed forward within a request", and strip `propensity` from judged outputs.

**P9. The outcome inbox multiplexes reward with control errors, messages and program results. Severity: LOW/MED.**
- Where: `continuity.py:342-381` plus its callers.
- ch2: "a thin but stateful reward line".
- Evidence: verdicts, grounded findings with prose, `return_rejected`, `order_refused`, messages and program deliveries all share one index. The thin reward line is diluted.
- Action: RESHAPE. Resolved largely by U1 and P6. Consider a typed `reward` versus `receipt` split.

### SMUGGLING: we author the plan or the objective

**S1. The kernel authors the action space in every producer request. Severity: MED.**
- Where: `loop.py:962-967` ("Your action is one of hold, investigate, build, govern, defer or order; your propensity is declared over those"), `propensity.py:44-50` (`ACTION_CLASSES`) and `action_class()`.
- ch2: "If the factory can organize itself only in ways that were anticipated at design time" (§I, line 34).
- Evidence:
  - This contradicts `validate_propensity`'s own contract, "The action ids are the agent's" (`request.py:431-434`).
  - The architect's six verbs, plus trading-specific labels (`buy:BTC:xs` size bands), fix the option set inside c.
- Action: FIX. Seats declare over their own ids. `action_class` can stay as ledger telemetry, but it is not the declared space and is not in the prompt.

**S2. The exploration draw has the kernel choose the agent's action class. Severity: MED/HIGH.**
- Where: `loop.py:973-983` and `compute.py:2226-2245`. The draw also overwrites the agent's propensity with the kernel's (`compute.py:2281-2288`).
- ch2: propensity is "an agent's own accounting". Epsilon-greedy is named as the agent's rule, not the architect's.
- Evidence:
  - The kernel samples "take an action of class 'build'" and records its own uniform-over-4 propensity.
  - This is live in capital-loop (`exploration_share = 0.15`).
  - Compliance is 43% (P4).
- Action: DELETE from the kernel. Exploration belongs in a seat's own registered learner or program, where the propensity is genuinely the agent's.

**S3. Instruction payloads fatten the request channel. Severity: LOW/MED.**
- Where:
  - `OUTCOME_CONTRACT` (`request.py:348-391`, 1719 B on every request, sourced from "GPT-6's third reading, §8 verbatim")
  - `ACCOUNTING_FACTS` (`schematics.py:43-54`)
  - The 2620-character seed system prompt, with procedural rules such as "Acknowledge through an item only after you have processed…"
  - Judge instructions (`loop.py:1291-1330`)
- ch2: "You can easily limit the types of patterns … by overspecifying the primitive" (line 38). Also Carroll's "robust simplicity" (§I.a).
- Evidence: advice on how to reason (forecast shape, pause etiquette, how to write objections) rides in every contract. The design authority cited in the code is an outside review, not ch2.
- Action: RESHAPE. Keep the schema and the verifiable contract. Move advice to `world.read` sections, or delete it.

**S4. World-specific scoring rules are baked into the kernel's universal producer request. Severity: LOW.**
- Where: `loop.py:964-967` (counterfactual `{coin, side}` "the market prices that trade…") and the trading labels in `propensity.py`.
- ch2: the world surface is the world, not the architecture.
- Action: FIX. The surface (the venue) should publish its counterfactual schema as a capability, not as kernel prose. Hand off to the evaluation and primitive auditors.

### KEEP, with reasons (positive findings)

- **Private `working_state` head**, readable only by its owner (`artifacts.py:245-264`). This is the prescribed memory, kept private.
- **Private lenses in `initial_state`**. Dissensus is seeded as private state rather than as prompt text, which is prescribed.
- **Thin verdict delivery** (`feedback.py:710-712`): `{verdict: round(score,4)}` addressed to the handle, with the judge referenced by handle only.
- **Stateful queue with persistent handles** (`kernel/queue.py`, `PropensityRecord` on the handle, `declared_propensity`), and the inbox cursor with `ack_through`.
- **Author stripping on requests** (`request.py:478-492`, `assembly.py:832-838`) and the id stamped at render time (`assembly.py:98-140`).
- **First-tier judge `actor_context` projection** (`loop.py:1239-1249`) and removal of `since_you_last_woke` from judged inputs.
- **`catalogue.search` / `world.read` / capability index**: self-describing contracts read at plan time, i.e. public schematics.
- **`custody_view`**: the shared fund is world state. One seat's positions being visible to all is inherent to a single venue account, and is not a seat-privacy breach.
- **`since_you_last_woke`**: a coalesced, seat-owned subscription. This is minimal disclosure the seat itself controls.

---

## 3. Counts

| Class | Count | IDs |
|---|---|---|
| CONTRADICTS | 8 | C1–C8 |
| UNPRESCRIBED | 5 | U1–U5 |
| MISSING | 3 | M1–M3 |
| PATHOLOGY | 9 | P1–P9 |
| SMUGGLING | 4 | S1–S4 |
| **Total** | **29** | |

HIGH-severity findings: C1, C2, C3, C6, U1, M2 (6 in total). S2 is rated MED/HIGH.

## 4. Suggested order of work

1. **Close the judge leaks.** C1 (description and payoff), C2 (`producer_id` and evaluator lists), C7 (metas get `_operating_context`), P8 (dedupe the propensity), P5 (drop standing). These are small edits with a large effect on the adversarial test.
2. **DELETE `address.send` (U1) and the notebook (U2).** This removes C6 and about 160 lines of redaction in `request.py`. Keep the rent rate for working state.
3. **Fix private-state leaks.** C3 (program stdin), C4 (artifact listing), C5 (`return_feedback` to the owner's inbox).
4. **Remove the kernel's authorship of choices.** S2 (delete the exploration draw), S1 (the agent's own action ids), P3 (a learner draw, not a distribution).
5. **BUILD the missing reward half.** M2 (a counterfactual consumer of the propensity), then M1 (propensity on child requests).
6. **Trim disclosure.** P2 and S3 (a judge's view as input and output; advice behind `world.read`), then measure the change in `sections` bytes.
