# Seat 3 — the first move

Audited HEAD `0ec4df40eb573f546f69a111699e9df07320402e` (brief atop implementation `edd6efc`). Read the required documents and code, including tests; excluded the three specified internals. No other seat was consulted. Six findings follow, ranked by severity.

## 1. Blocker · will break — every advertised treasury transfer is malformed

**Locations:** `factorylab/runtime/loop.py:786,930`; `factorylab/cortex/assembly.py:214`.

**Essay:** II.IV, *The Charter and the Loop*: “a token budget of $0.” A solvent factory must be able to execute its chosen resource purchases.

**Scenario:** An assembly returns `{"tool_calls":[{"tool":"treasury.transfer","args":{"direction":"to_reserve","usd":"5"}}]}` with sufficient funds. The advertised schema uses `type: ["string", "integer"]`; `_validate_schema` performs dictionary membership with that list and raises `TypeError: unhashable type: 'list'`. `Assembly.invoke` converts the complete reply into a malformed return before any transfer dispatch. Integer `5` fails identically. Any accompanying valid actions/proposals are also discarded.

**Reproduction:** In-memory assembly, fixed one input/one output token, `TokenPrice(1,1)`, runtime validator: both inputs returned `status='malformed'`, cost `2`, and no tool calls. The required 500-tick scripted run recorded zero transfer intents despite its scripted transfer attempt.

**Fix:** Use supported `anyOf` alternatives or implement and validate union types consistently. Exercise the advertised schema through `Assembly.invoke` and runtime dispatch, including both exact money representations. Existing direct treasury tests bypass the failing boundary. This is a regression against round-one `fidelity-codex` finding 7's validation fix.

## 2. Serious · will break — Venice credit cannot be renewed by the population

**Locations:** `factorylab/world/treasury.py:101`; `factorylab/runtime/loop.py:778`; `factorylab/runtime/cli.py:238`; `factorylab/world/market.py:443`.

**Essay:** II.IV, *The Charter and the Loop*: “a continuous, reciprocal flow of capital.”

**Scenario:** OpenRouter credit is exhausted; a registered Venice assembly has spent its initial credit; Base reserve still holds 10,000,000 micro-USD. Even after fixing finding 1, `treasury.transfer("to_venice", "5", …)` is refused: `direction must be to_reserve or to_venue`. This was reproduced directly without network access. The only implemented top-up entry points are external CLI/proof operations. Venice completion does not top up, and namespace routing does not switch providers. A population that chose Venice cannot purchase its next call with its own available reserve. Without an already usable x402 assembly, compute insolvency can end the world while money remains.

**Fix:** Expose a bounded, journaled Venice top-up operation with source reservation, payment uncertainty handling, verified credit arrival and resume reconciliation. Publish it alongside the pot balances. An architect running `reserve topup` after launch is an intervention, not population autonomy. Independent x402 inference remains an alternative; this finding does not claim every trajectory must die.

## 3. Serious · not Class 3 — the initial assembly structure cannot be dismantled

**Locations:** `factorylab/cortex/registration.py:133,151`; `factorylab/runtime/loop.py:948,1024`; `factorylab/cortex/assembly.py:26`.

**Essay:** II.IV.c, *Clocking the Factory*: “orchestration armature that the factory is licensed to tear down.”

**Scenario:** The population discovers that one expensive seed is redundant and proposes retiring it, replacing its identity, or installing a combined producer/evaluator. There is no retirement proposal. Reusing an ID fails; an evaluator accepting anything except `ProducerReturn` fails; a producer accepting evaluation events fails. Every replacement router's universe still includes every compatible registered assembly. Reduced sampling probability is not retirement, and router retirement is not assembly retirement.

The FC child-request implementation addresses the formerly inert child path, but only permits two children that cannot call further children, followed by one parent continuation. It does not grant control over scheduled membership, event meanings or role dispatch. The fixed workflow survives every allowed rewrite.

**Fix:** Add population-controlled assembly replacement/retirement and composable scheduling contracts, retaining liability and delayed feedback for retired identities. Preserve resource bounds and temporal separation without fixing the permanent organizational graph. This is against the incomplete fix for round-one `fidelity-codex` 2 / `fidelity-opus` 6, not a rediscovery of the old missing child dispatcher.

## 4. Serious · not Class 3 — producers can amend a charter they cannot read

**Locations:** `factorylab/runtime/loop.py:1059,1126,2522,2604`; `factorylab/charter/charter.py:59`.

**Essay:** II.I.b, *Information Design for Continuous Learning*: “underdisclosure starves an agent of what it needs to coordinate.”

**Scenario:** On its first event, a producer wants to replace a bad metric. Its prompt contains `charter_edition`, priced card IDs, regions and a proposal template requiring “one of the charter norms.” It contains neither those norms nor the full cards and their observation/accountability bindings. There is no charter-reading tool. The evaluator, meta and committee paths separately receive `charter.render()`; ordinary producers and antagonists do not. Guessing an unknown norm fails validation.

**Reproduction:** With the edition-one example installed, `_world_block()` contained no `charter`, no `norms`, and none of the four norm strings. Reading the producer request construction confirms no separate charter injection. An occasional committee seat is not a launch-time public contract.

**Fix:** Include the effective charter and its checked measurement bindings in every actor's public input, or provide an advertised versioned reader. Also publish the missing fixed mechanics needed to interpret it: reserve share/window, committee size/threshold and price-controller recurrence. These are schematics, not private learner state. This extends the unresolved disclosure problem in round-one `fidelity-codex` 15; rendering observation fields for evaluators alone did not close it.

## 5. Serious · will break — installed edition one changes the voted measurements

**Locations:** `worlds/edition1-example.toml:183`; `factorylab/runtime/loop.py:1749,1781,2274`; `factorylab/runtime/observations.py:50`.

**Essay:** II.IV.a, *Self-Writing*: “Every such contribution converts a world-finding into authored record.”

The example explicitly omits three passed cards and installs five. Its comments acknowledge that rolling-window labels are unenforced, but the population-facing charter still promises them. The exact eight-card audit is:

| Passed card | Can the catalogue measure the voted quantity? |
|---|---|
| `model_cost_efficiency` | No exact binding. Mapped to wallet cost per well-formed producer return, including tools/continuations, rather than the stated compute-cost quantity and rolling 100 returns. |
| `revision_rate` | Activity proxy exists; counts tool calls and proposal objects, including rejected ones, over producer returns. Neither the rolling 100 well-formed denominator nor effective revision is established. |
| `accountabilty_score` | No verifiability-of-commitment observation; omitted. |
| `revision_adoption` | No amendment-to-prior-breach linkage; omitted. |
| `cost_per_return` | Base quantity exists; rolling 100 returns and its prior-window median are replaced by reserve-window samples. |
| `well_formed_rate` | Schema-validity quantity exists; rolling 100 returns is not implemented. |
| `practice_revision_rate` | No fraction of revision-active windows over ten windows; omitted. |
| `verdict_mean_score` | `verdict_mean` exists, but pools verdicts in the reserve window; no rolling 50 settled forecasts per evaluator. |

**Scenario/reproduction:** Put 100 well-formed returns costing 100 micro-USD each, followed by 100 costing 900 each, in one reserve window. `_close_price_window()` reports `500.0`; the `model_cost_efficiency` violation is `0.0`. The promised last-100 mean is 900 and violates the 500 ceiling. The price controller therefore learns from a different charter. A rejected `{"kind":"unknown"}` proposal separately reproduced `cosmetic_revision=True`.

**Fix:** Make observation, scope and window executable contracts; preflight every proposed card before voting. Implement unavailable measurements or return them to the population for revision. Ratify any reduced/translated edition explicitly. Do not present five architect-translated cards as the enacted eight-card vote. This is against remaining defects from round-one `fidelity-codex` 15/16; effective role binding fixed finding 4's decorative-price bug, not these semantics.

## 6. Serious · will break — the next charter-drafting run crashes on its first valid card

**Locations:** `scripts/draft_edition1.py:46,276,303`; `factorylab/charter/charter.py:26`.

**Essay:** II.IV.a, *Self-Writing*: “if you hand it a norm, it will propose a metric.”

**Scenario/reproduction:** Pass `_card_from` a valid card containing all seven advertised `CARD_FIELDS`. It invokes the current eight-argument `MetricCard` constructor and raises `TypeError: MetricCard.__init__() missing 1 required positional argument: 'answers_for'`. The exception escapes `collect_proposals` after the model completion has been paid for, preventing the committee/report stage. Even supplying an extra `answers_for` cannot help: the constructor only forwards `CARD_FIELDS`.

**Fix:** Update drafting schema, parsing and report/collision rendering together. Have the population specify the accountable role, validate before voting, and preserve it into the installed charter. Test a complete synthetic survey/vote/report path. This is a new regression introduced by the round-one role-binding fix; the historical successful draft does not demonstrate that today's script works.

## First-move accounting: norms, defaults and freedoms

I.I, *On Factories and Darkness*, says a metric becomes an objective “only when it is prioritized as such.” The four norms are genuinely broad values: none specifies a trade, model or expenditure target. Their immutability is permitted by II.IV.a's “read-only wall.”

Seed cards do specify instrumental preferences: perpetual cost reduction, 90% well-formedness and positive forecast skill. The passed 500-micro ceiling, activity floor and 0.8 verdict target are also objectives once priced. None follows uniquely from its norm: scarce-resource care can justify an expensive useful call; truthful commitments can warrant low verdicts; revisability can justify changing nothing. They are revisable operational hypotheses, not proof that these objectives are required by the essay. Findings 3–5 restrict how fully that negotiation can happen.

| Element | Mechanism actually available | Disclosed at launch? / interpretation |
|---|---|---|
| Tick | Amendment `tick_interval`; testnet starts 60s, bounded 10s–20m; delayed activation. | Yes, shape and bounds. Cadence constraints, not a prescribed trading frequency. |
| Cards | Amendment add/replace/remove, observation ID and `answers_for`. No observation-registration mechanism. | Shape/catalogue yes; charter absent for producers. See 4–5. |
| Prices | Optional amendment `lambda`; controller reprices within `lambda_max=1`. | Lambda/bound yes. Fixed `eta=.5`, `kappa=.5`, decay `.1` recurrence absent. Controller parameters cannot be amended. |
| Models/reasoning | `catalogue.search`, model registration, new assembly; namespaced reasoning variants. | Yes. Model choice changes through a new assembly, not an in-place rewrite. |
| Compute market | `market.discover`, `x402:<seller>#<model>` or `venice:<id>` registration. | Yes. Discovery index, payment network and request cap remain constraints. |
| Committee | Sortition, at most five seats, majority; five settled decisions required. No amendment of committee rules. | Eligibility yes; size/threshold not in ordinary world input. Constitutional permissions, permitted by II.IV.a. |
| Pots | Venue/reserve transfer, seller inference purchases; no Venice top-up. | Balances and transfer contract yes; execution fails in 1, missing operation in 2. Initial allocation remains architect-selected. |
| Membership | Add assemblies; replace/add routers. No assembly retirement or role-algebra replacement. | Additions advertised; no missing retirement instruction could supply the absent mechanism. See 3. |
| Novelty/immune | Protected 10%, one-hour windows, three trials; automatic gain/decay responses. No amendment of these settings. | Protection/trials/pathology flags partly disclosed; full parameters absent. Architectural constraints with exploratory incentives. |

II.IV.a calls write-access rules “hard casts”; their immutability alone is not a defect. II.IV.c requires relational timing rather than “the actual frequency bands themselves.” I found no additional tick defect after tracing amendment activation and reading the deterministic clock/resume tests. Exact immune gains, price gains, seat count and novelty fractions remain engineering choices requiring experimental calibration.

## Survey framing, roster and launch prompts

The survey is materially framed by the architect: `proposal_prompt` supplies existing cards, limits each assembly to three proposals and supplies measurement/region vocabulary. The record even has a voter declaring two baseline cards “It must pass” (`edition1-draft.md:372,380`). This is evidence of anchoring, not independent rediscovery of the seed metrics. The fixed norms are legitimate; describing unsupported rolling windows is not (5). The one survey, role-covering sortition and three-of-five vote do not independently violate II.IV.a's “randomized selection of delegates.” I examined author stripping, seat construction, abstentions and threshold calculation; no additional drafting-governance finding beyond 5–6.

A different reasonable seed would materially change proposals, votes, spend and eventual membership. That is permitted first-move influence: II.I.a says “The population you seed is the only instrument that can actually reach past V.” It does not permit permanent architectural barriers to later counterplay. The draft's two producer models also differ from today's launch producers (`edition1-draft.md:584` versus `testnet.toml:78`); it is a historical population draft, not a vote by the current exact roster.

The eight listed models span families and prices, but **all eight use OpenRouter** in this manifest. Six distinct models serve the nine seed assemblies. There is no cheapest-model-only selector or favourite-ID reward in the runtime; the two producer substitutions explicitly favour observed initiative over the cheapest calls. Cost pressure comes from resource constraints and cards, not that roster alone.

The repository's Venice research records five available counterparts, three missing models, and higher prices for several counterparts; its blanket parity comment in `testnet.toml:77` is unsupported even by that record. Qwen 3.7 and HY3 are active seed dependencies; Muse is only listed. The x402 research does not establish exact replacements for all three. These are historical research observations, not freshly verified seller availability.

When OpenRouter credit reaches zero, bare-ID seeds do not become Venice/x402 agents automatically. Survival requires prior registration of affordable alternative models **and assemblies**, plus usable balances; finding 2 then limits renewal. This is conditional survivability, not proof of it. A namespace switch should remain an explicit population choice.

I read the launch system/request strings, schemas, charter rendering, scoring disclosure and continuations. The system prompt prescribes JSON/composition, not a trading strategy; producer requests say “Respond to event”; evaluator/meta requests prescribe their scaffold roles. Public consequence/scoring descriptions explain payoff contracts, which II.I.b expressly makes public as “the structures of requests and rewards.” I do not repeat round one's disputed claim that disclosure itself necessarily installs an objective. The concrete prompt problems are missing charter/semantics and false measurement promises, above.

## Verification and audit decisions

Only this report was added. No implementation changes, commits, live purchases or mainnet calls. Used the existing Python environment with this worktree on `PYTHONPATH`, disabled bytecode/test caches, and kept command-output scratch files outside the worktree. No credential-file contents were inspected. No new dependency was installed.

Required worlds, both exit 0: `scripted --events 500 --seed 1` ended alive at 59,644,062 micro-USD; `scripted-crash --events 600 --seed 2` terminated `balance_zero` at −3,481,919. Both reported `wallet_conservation=true` and `ledger_verify=true`. Synthetic execution establishes no live treasury or provider proof.

Gate output (verbatim):

```text
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab-audit-firstmove
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 14/14 workers
14 workers [1704 items]

........................................................................ [  4%]
.....................................................s.................. [  8%]
................................s....................................... [ 12%]
........................................................................ [ 16%]
......................s................................................s [ 21%]
........................................................................ [ 25%]
.......................................................................s [ 29%]
s..........................s.........s........s.s..........s............ [ 33%]
....................s..........s..................................s..... [ 38%]
..........................s.............s..................s............ [ 42%]
...........................s......................................s..... [ 46%]
........................................................................ [ 50%]
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
================= 1685 passed, 19 skipped in 331.73s (0:05:31) =================
```

**Open:** The essay leaves primitive richness, seed mix, constitutional governance form, exact control gains and viable economic niche underdetermined. No single alternative default proves fidelity or survival. The findings concern unavailable counterplay and broken contracts, not those open choices.
