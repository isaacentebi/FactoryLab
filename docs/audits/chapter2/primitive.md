# Audit: Chapter II §I "The Primitive" and §I.a "Incentive Design" (ch2.md lines 7–92)

Branch `fast-loop-harness` @ 5049b0f. Read-only audit. Scope: last-instance assembly, the waterfall test, Class 3 composability versus imperative spawning, primitive versus protocol, frontier versus core, mean-based versus no-swap-regret learners, U* > V, and what the architect must provision (memory, action set, attributable reward, counterfactuals).

Counts: CONTRADICTS 1 · UNPRESCRIBED 2 · MISSING 3 · PATHOLOGY 5 · SMUGGLING 2 (13 findings).

Kernel physics (money, custody, ledger, kill, resume) and world surfaces (venues, connectors, Polymarket, vaults) are out of scope as deletion targets and are not flagged here.

---

## Findings

### F1. PATHOLOGY, high: the router's NOOP arm has a fixed payoff that no charter price touches, so the frontier drifts to abstention (learning death)

- **Where:** `factorylab/runtime/routing.py:156-182` (the `ZERO_CONSEQUENCE` table: 0.5 for producer scales, 0.75 for Brier scales); `factorylab/runtime/feedback.py:2042-2047` and `:2145` (NOOP credited `RouterState.neutral()` with no penalty); `factorylab/runtime/pricing.py:768` (every seat's reward is `score - penalty`); `factorylab/learners/router.py:364-369` ("Learners must include NOOP"); `routing.py:866-904` (a watch that only observes).
- **Ch2:**
  - l.62: "should assume that the early superdark factory will be an absolute mess"
  - l.62: "not prematurely inhibit that mess from settling"
  - l.79: "needs at least some naive, mean-based no-regret learning somewhere"
  - l.77: "The world is ultimately responsible for setting prices to this game"
- **Evidence:**
  - Every router's universe includes `NOOP`. NOOP is credited an architect-chosen constant, and a woken seat has to beat that constant plus its card penalty.
  - A bare hold settles at exactly 0.5 (`OPPORTUNITY_DEFINITION`, `routing.py:161`). Any penalty from the `censorship-bound` card (`answers_for = "all"`) or from any other card therefore makes a woken seat strictly worse than NOOP for the same outcome.
  - NOOP pays no card price and no compute.
  - Commit 3ffb314 states the result: "a router whose seats score below its zero-consequence reward drifts to NOOP until every seat sits at the gamma floor". The fix was a ledger entry that "nothing reads".
  - Offline probe with this repo's `EXP3` (4 seats plus NOOP, gamma 0.1, 1,000 rounds, 20 seeds): seats scoring 0.05 below NOOP give NOOP a mean of 0.35 of the mass (maximum 0.65) against a uniform 0.20. After 2,000 rounds on one seed, NOOP holds 0.71.
  - The ProducerReturn (judge) router carries NOOP too, so judges can be abstained away as well. Unjudged returns are censored and stop teaching anyone.
- **Why it is the pathology:** the payoff of not acting is written by the architect (a constant), not priced by the world. That is exactly the cell of the payoff table ch2 says nobody holds. The frontier can then only be invoked through gamma exploration, which is ch2 II.II.a's learning death: a frontier "no longer being invoked".
- **Action: FIX.**
  - Give NOOP the same terms as a seat: the same card penalties, and ideally a world-priced counterfactual of inaction instead of a constant.
  - Or remove NOOP from routers whose seats already own their sleep (`defer` and `subscribe` exist in the producer schema, `loop.py:983-997`), so abstention is a seat's decision, priced like any other.
  - Either way, the learning-death watch should feed the novelty and immune path instead of being observation-only.
- **Blast radius:**
  - `feedback.py` abstention crediting (about 150 lines: `_defer_abstention`, `_credit_abstentions`, `noop_credits`)
  - `routing.py` (`ZERO_CONSEQUENCE`, `neutral()`, the watch)
  - resume state (`noop_credits`, `definitions`, `latency`)
  - `tests/runtime/test_router_learning.py`

### F2. SMUGGLING, high: the "exploration niche" has the kernel pick a plan class and tell the seat to carry it out

- **Where:**
  - `factorylab/runtime/compute.py:2223-2244` (`EXPLORATION_CLASSES = ("order","investigate","build","govern")`, `_exploration_draw`) and `:2281-2294`
  - `factorylab/runtime/loop.py:973-982`, which adds to the prompt: "EXPLORATION DRAW: the kernel sampled this decision … Take one, as small and cheap as you find informative, and say what you expect to learn"
  - `factorylab/runtime/worlds.py:340-344`
  - `exploration_share = 0.15` in `worlds/edition5-testnet-rehearsal.toml:307` and `worlds/edition5-capital-loop.toml:335`
- **Ch2:**
  - l.62: agents "will pass through exploration phases" (the phases belong to the learner)
  - l.77: the architect "can commit nothing better than a stationary strategy"
  - l.34: "an assembly anticipated at design time is one whose derivation terminates"
- **Evidence:**
  - The architect wrote the four-class taxonomy. The kernel samples one, and the instruction ("take one … say what you expect to learn") is Class 1 content injected into 15% of producer decisions.
  - The justification cites II.II.b ("learning death prevented as a fact about the world"). But ch2 l.175 prescribes something different: "Some share of compute and write access is usable only in the context of unhistoried actions". That is a reserved resource niche, not a kernel-directed action. The architect's own roadmap concedes the gap (`docs/architecture/edition5-roadmap.md` item 5: "The novelty reserve protects assemblies, not unhistoried actions").
  - The success metric was "the population used every action class (build 7, order 6, govern 5 …) where before it only held". That is the architect steering toward a behaviour it knew how to want, which is the readable space *c*.
  - The propensity recorded on a complied draw is uniform 1/4 (`compute.py:2286-2291`). The actual behaviour probability is a mixture of 0.15 × uniform and 0.85 × the seat's own policy, so the importance weights are biased too.
- **Action: DELETE** the exploration niche (the `exploration_share` key, `_exploration_draw`, the prompt clause, and the `exploration.draw` and `exploration.taken` ledger kinds). BUILD in its place the prescribed niche: compute and write access usable only by unhistoried actions. F1 is the root cause the niche was papering over.
- **Blast radius:**
  - about 60 lines across `compute.py`, `loop.py` and `worlds.py`
  - `tests/runtime/test_exploration.py`
  - `scripts/fastloop.py`
  - `tests/runtime/fixtures/polymarket-fastloop.toml`
  - two manifests (manifest hash changes; the default is 0, so older worlds are unaffected)

### F3. SMUGGLING, high: the kernel writes the learner's action space (six verbs plus base-unit size bands) and refuses declarations outside it

- **Where:**
  - `factorylab/runtime/propensity.py:48` (`ACTION_CLASSES`), `:68-73` (`SIZE_BANDS` in base units), `:152-260` (`action_label`, `action_class`), `:310-318` (a declaration must include the kernel's label or class, else it is refused)
  - `loop.py:962-967`, which tells producers "Your action is one of hold, investigate, build, govern, defer or order"
  - the `action_labels` institution section, 1,525 chars (`schematics.py:588`)
  - `compute.py` `_open_assembly_round`: a learner's declared actions outside the kernel labels are "unlearned"
- **Ch2:**
  - l.89: "a defined set of actions the learner understands itself to be choosing among"
  - l.60: "the less the architect knows, the less structure they should impose"
- **Evidence:**
  - A seat that declares a propensity over its own action ids (say `carry`, `market-make`) is refused unless the kernel's label or verb is among them. A `LearnerProposal` over self-defined actions trains nothing unless those actions match kernel labels. So the action set is not one the learner "understands itself" to choose among. The architect authored it, which is plan vocabulary.
  - The size bands ignore the coin: `0.01` BTC (about $600) is "xs", while 10 PURR is "l".
- **Action: FIX.**
  - Take the ground truth of what was done from executed effects only (the `effect_label` names of venue and treasury calls, and child requests).
  - Let the seat's or learner's declared action set be its own, with a seat-declared mapping from effects to its actions.
  - Drop the six-verb enum and the size bands from the prompt and from validation.
- **Blast radius:** `propensity.py` (about 120 lines), prompt text, `action_vocabulary()`, declared-propensity tests, and the fast-loop scorecard that counts classes.

### F4. PATHOLOGY, medium: over-specified instruction contracts (violating Carroll's robust simplicity), built from carve-outs

- **Where:**
  - the seed `system_prompt`: 2,620 chars, identical across all nine seats (`worlds/edition5-testnet-rehearsal.toml:192` and the other seat entries)
  - `factorylab/cortex/request.py:339-378` `OUTCOME_CONTRACT` (1,717 chars) on every request, including a mandated fidelity-objection JSON template and "For a pause, state the next relevant condition"
  - `action_vocabulary()` (1,525 chars)
  - `schematics.py:44-52` `ACCOUNTING_FACTS`
  - the producer request text at `loop.py:962-971`
- **Ch2:**
  - l.60: "A baroque incentive set filled with mazelike conditional clauses and carve-outs"
  - l.60: "robust designs are those that rely on weak claims"
- **Evidence:**
  - Many clauses are patches for observed model failures, for example "A refusal written inside an otherwise required answer field is not a valid refusal" and "never duration strings". Carroll's argument is that each such clause is exploitable structure imposed by an author who does not know the action space.
  - Several blocks are marked "verbatim" imports from an outside model's design reading ("GPT-6's third reading, §8"), which makes them design-time authorship by a third party.
  - Some lines announce kernel physics, for example "A venue profit does not refill provider credit without a confirmed conversion". The roadmap's item 6 already concedes this.
- **Action: FIX.**
  - Cut the system contract to what a return must satisfy plus the refusal form.
  - Move how-to material behind `catalogue.search` and `world.read`, where the compact mode already puts reference material.
  - Drop the carve-outs and let validation feedback, which is already returned to seats, teach the protocol.
  - This requires re-ratifying the roster digest, done once.

### F5. CONTRADICTS, medium: composition is imperative, addresses targets by id, and sits outside learning

- **Where:**
  - `factorylab/runtime/compute.py:2347-2404` (`_invoke_child`: `target` is a live assembly id; propensity `PropensityRecord((target,),(1.,),…,"parent-selected")` at `:2375`)
  - `feedback.py:1985-1995` (`_router_sampled` excludes parent-selected rounds, so no router ever learns composition)
  - `schematics.py:22-27`: "those ids are what requests[].target … name"
- **Ch2:**
  - l.36: "agent A decides to create agent B with specific instructions"
  - l.36: "without any single system or agent needing to hold the full topology"
  - l.36: "swapped, rerouted, parallelized, or removed without cascading failures"
- **Evidence:**
  - A parent must name a peer's id from the catalogue it holds. That peer runs with a propensity of 1.0.
  - A retired target returns "target assembly unavailable" (`:2402`), which is a cascading failure on removal.
  - The only learned composition is event routing over `accepts` (faithful, see below). Child requests, the one path by which agents compose one another's work within a turn, are the hierarchical spawning ch2 critiques, and nothing learns from them.
- **Action: BUILD** contract-addressed requests: `target` names a kind (an accepted event kind or an emitted kind). The kind's router draws the executor with a logged propensity, so composition is sampled, learned and survives retirement. Deprecate id targets, keeping `"self"` if needed.
- **Blast radius:** medium: `_invoke_child` in `compute.py` and its override in `loop.py:1124-1140`, `ChildRequest`, the addressing text, and child tests.

### F6. MISSING, medium: an assembly's public contract carries no self-description

- **Where:** `schematics.py:603-627`, which publishes `{id, version, accepts, emits}` only; `registration.py:132-154` `AssemblyProposal` has no description field; the system prompt is sealed.
- **Ch2:**
  - l.30: "the contract has to carry enough self-description"
  - l.38: "semantic coupling … not captured by the contract"
- **Evidence:** to use a peer as a primitive, which is required by F5's id-addressed children, a seat can only guess from the id's name ("mechanism", "empirical", "constructor"). That meaning lives outside the contract, which is semantic coupling by construction. Tools do carry a description (500 chars) and connectors carry one too. Assemblies are the one primitive without one.
- **Action: BUILD** a bounded public `description` and, optionally, a declared input expectation per accepted kind in assembly registration, published in the catalogue.

### F7. PATHOLOGY (semantic coupling), medium: the generic Return contract carries one venue's order semantics and the seed roles' fields

- **Where:** `factorylab/cortex/assembly.py:589-623` (`reserved_return_fields`: `coin`, `side`, `verdict`, `payoff`, `conformity` are reserved on every return) and `:843-869` (`_NOT_AN_ANSWER_ORDER`, which uses Hyperliquid SDK names, and the market-order validation applied to any return whose `action == "order"`).
- **Ch2:**
  - l.38: "You can easily limit the types of patterns … by overspecifying the primitive"
  - l.25: a contract "decorrelates the primitive's interior information from its consumers' utility"
- **Evidence:** every primitive, including population-declared kinds, validates against a Hyperliquid market-order shape and the four seed roles' numeric fields. A population kind cannot use `action: "order"` or `verdict` with its own meaning. The world surface (a venue) is compiled into the universal primitive contract instead of living in the producer kind's schema. The venue-tool path already exists (`venue.place_market`).
- **Action: FIX** by moving order semantics into the producer kind's outcome schema (or onto venue tools only) and keeping the universal reserved set to continuity, propensity, `register`, `requests`, `tool_calls` and `forecasts`.

### F8. MISSING, medium: the "core" retains nothing from the frontier, and by default there is no core

- **Where:** `routing.py:403-411` (`_seed_learner_kind`); `worlds.py:345-348` (`no_swap_regret_kinds` defaults to `()`); `worlds/edition5-testnet-rehearsal.toml:326` (`["ProducerReturn"]`, which is judge selection).
- **Ch2:**
  - l.87: "mean-based structures at the frontier … no-swap-regret structures at a kind of core"
  - l.87: "(where the surplus is retained)"
- **Evidence:**
  - Every world except the two edition 5 manifests seeds only EXP3.
  - Where a core exists, it is a Blum–Mansour learner choosing which judge to wake. No path carries a producer-frontier discovery into a no-swap-regret structure: no core over producer kinds conditioned on anything, and no promotion of a frontier arm's record into a core.
  - The placement is asserted in a manifest comment ("essay II.a") with no argument for why judge routing is where frontier surplus is retained.
- **Action: FIX/BUILD.** Either argue the placement explicitly, or seed a core that consumes frontier outcomes, for example a Blum–Mansour router over the same producer kind added beside the EXP3 router (`add = true` already exists). Keep the population's ability to register either class, which is faithful.

### F9. MISSING, low: population tools declare inputs but promise no output

- **Where:** `factorylab/runtime/governance.py:733` (`output_schema={"type": "object"}`), `registration.py:186-192` (`ToolProposal` has only `args_schema`), `cortex/tools.py:31-73` (the result is any JSON object).
- **Ch2:** l.27: "what it promises to return, and what conditions it guarantees"
- **Action: BUILD** an optional `returns_schema` on tool registration, validated by `ToolRunner.run` and published in the catalogue.

### F10. PATHOLOGY, low: self-declared LLM "propensities" serve as importance weights for training

- **Where:** `propensity.py:260-330` (`declared_record`; the floor `MIN_DECLARED_MASS = 0.05`, line 57), `compute.py` `_open_assembly_round`, and the scoring block text in `schematics.py:1785-1792` ("reward r on action a updates it with weight r / propensity(a)").
- **Ch2:** l.89: "a source of counterfactuals, some account of the roads-not-taken"
- **Evidence:** an LLM seat does not sample from the distribution it reports. Using the report as the behaviour policy for off-policy updates lets a seat inflate its own update by up to 20× by declaring low mass on what it did. The floor bounds the inflation but does not remove the bias. Disclosure is prescribed (I.b, forward to the recipient). Training on the report is not.
- **Action: FIX:** forward declarations to judges as belief. Train learners only on propensities the kernel actually sampled: router draws, or program seats drawing from kernel randomness.

### F11. UNPRESCRIBED, low: the Hedge learner and Blum–Mansour's full-information branch are dead in production

- **Where:**
  - `factorylab/learners/hedge.py` (the whole file)
  - `learners/base.py:150-152`
  - the `FullInfoFeedback` branches in `learners/blum_mansour.py:165-176`
  - `learners/delayed.py:7-14`
  - README l.15 claims "Routers run no-regret learners (EXP3, Hedge)"
- **Evidence:** no runtime path builds a Hedge router or supplies full-information losses (the factory never has them). Only tests use it (7 files under `tests/learners/`).
- **Depends on it:** only tests and `restore_learner`'s allowlist.
- **Action: DELETE**, or at least fix the README. Low risk.

### F12. PATHOLOGY (semantic coupling), low: an Exposure return is silently re-emitted as a ProducerReturn

- **Where:** `loop.py:1104-1108` and `compute.py:2459-2461` ("Exposure retains its producer-shaped judgment route for the shipped seeds").
- **Ch2:** l.38: "implicitly depend on each other's … output-formatting in ways that are *not* captured by the contract"
- **Action: FIX:** route Exposure only as its own kind. Subscribers that want to judge it declare `accepts = ["Exposure"]`.

### F13. UNPRESCRIBED (hygiene), low

- `registration.py:375-380`: `MarketProposal` appears twice in the `Proposal` union.
- `MAX_DECLARED_ACTIONS` and `MAX_ACTION_ID_CHARS` are defined twice (`registration.py:27-28`, `request.py:405-406`).
- **Action: FIX** (single definition).

---

## Considered and kept

- **The seed roster and the four seed kinds** (ProducerReturn, Verdict, MetaVerdict, Exposure), and the evaluation pipeline the runtime wires after each producer return. This is a Stackelberg seed (ch2 §III prescribes judges, metas and an adversarial minority). Roles are contracts (`accepts`/`emits`), seeds are retirable, and custom kinds route generically. KEEP. F7 and F12 are the parts that leak.
- **Four closed reward shapes** (`registration.py:42`). Incentive design at the first move is prescribed (l.58). A small, weak menu is consistent with robust simplicity. KEEP.
- **Seed lenses.** They live in editable `working_state`, not the system prompt (manifest l.126-135). This is seeded dissensus (I.b). KEEP.

## Faithful to the chapter

- **Class 3 composability through routers over contracts:** an event goes to any live assembly whose `accepts` names its kind. A learner picks the executor with a logged propensity (`routing.py:388-398, 780-866`). Routers form automatically for new kinds (`_open_epoch`). No agent holds the topology.
- **Last-instance assembly:** models, assemblies, program seats, tools, routers and learners register instantly against a novelty trial (`governance.py:700-900`), combined only at registration. There is no committee except for outside origins, which is a world boundary.
- **Mean-based frontier:** EXP3 is the default router (`routing.py:403-411, 428-437`). EXP3 is mean-based in Braverman et al.'s sense.
- **No-swap-regret by composition:** `BlumMansour` is built from N independent EXP3 rows solving p = pQ (SR_MAB, Lemma 10), exactly as l.89 describes. `SnapshotLearner` freezes each round so delayed rewards find their decision.
- **Primitives encode both classes:** `RouterProposal` and `LearnerProposal` accept `exp3 | blum_mansour`. Program seats with private state let the population seed learners of its own.
- **The three provisions of l.89:**
  - persistent memory: `working_state` continuity, checkpointed learner state
  - action sets: router universes and learner-declared sets (subject to F3)
  - attributable reward: handles, a logged propensity per draw, and a delayed settlement queue
- **Package independence:** `factorylab/learners` imports nothing from the project, so a learner is replaceable without touching physics (the contract hides internals, l.25).
- **System-role hygiene:** only the assembly's own prompt reaches the system role, and population-authored prose travels as material (`assembly.py:98-140`).
