# FactoryLab: first-principles and functional review

18 September 2026. Review target: `efa751b`, with the existing local edits to
`scripts/draft_edition1.py` and `tests/runtime/test_loop.py` preserved. Those edits are
included in the isolated verification snapshot. No runtime source was changed by this review.

The user's priority is an autonomous society that develops surprising goals and institutions;
earning its upkeep matters, but is secondary. That is the standard applied here.

**Judgment: there is useful infrastructure here, but the desired factory has not been
demonstrated. The next move should be a smaller, falsifiable experiment in collective
agency, preceded by performance repair and a few accounting fixes. Adding another trading
venue is unlikely to resolve the central problem.**

This conclusion is stronger than “needs more testing” and narrower than “throw it away.”
The population has started to remember, read consequences, and alter individual decisions.
It has not demonstrated that it can establish a shared undertaking, fund it, make an
institution around it, or replace that institution when evidence undermines it.

**What was examined**

The original local essay, README, binding v0.8 spec, Edition 3 and R3 plans, current runtime,
learning, settlement, budget, registration, prompt, service and deployment paths; the exported
Run 5 evidence; current test configuration and fixtures. Two Sol review assignments and four
Luna assignments covered ontology, mechanics, behavioral evidence, launch, world design, and
construction paths. The lead challenged and checked their conclusions rather than treating
agreement as proof. Original prediction-market research was consulted for mechanism design.

No keys, live sealed diaries, or live accounts were inspected. No paid model calls, money
movement, deployment, or mainnet execution was initiated. Historical testnet evidence is
identified as historical. This is a broad audit, not a claim to have proved every invariant.

**1. What has actually been achieved**

The strongest evidence is not the number of tests or architectural concepts. It is a seat
correcting a belief because an addressed execution receipt contradicted it. Run 5 includes
a constructor recognizing that its prior short was rejected, an empirical seat linking an
order to an earlier commitment, and a mechanism seat referring to accumulated funding prints.
Those are meaningful advances in continuity and evidence use.

The exported Run 5 returns and [rehearsal report](/Users/isaacentebi/Desktop/FactoryLab/docs/audits/v6/rehearsal.md:153) show:

| Observation | Evidence | What it establishes |
|---|---|---|
| 150 ticks, five hours, 323 model calls, $2.33 model/tool spend | Report and exported returns | A bounded live testnet episode, not a sustainable economy |
| 156 state writes, 290 addressed outcomes, 37 acknowledgements | Report | Individual continuity and some receipt use |
| Four orders, two fills, one refusal, one uncertain submission | Report | Some real testnet execution and uncertainty handling |
| 170 producer responses, 138 evaluator responses, 15 meta responses | `returns-all.json`, recounted | Evaluation consumed a substantial share of cognition |
| Eight tool calls, all producer-side; no child requests or registrations | Export, recounted | No demonstrated collective construction |
| No calculator, search, notes, proposals, programs or services | Report and export | Available vocabulary did not become an institution |
| 58 malformed GLM responses, including 40 reasoning-only responses | Report | A real reliability failure in the then-current route |
| 170 MB / 53,194 ledger items; repeated verdict close-outs dominated rows | Report | Serious historical recording amplification |

The malformed-response share was 58/323, about 18%. It is incorrect to treat every billed
call as productive thinking. It is equally incorrect to assume the current route still has
that failure rate: reasoning settings and other code have changed since this run.

Repairs for duplicate close-outs, empty-draw consequences, uncertain-order polling and prompt
prefix placement exist in current source. Run 5 does not validate those repairs; it predates
them. Nor does a five-hour episode establish a steady burn rate, long-run profitability,
or a week's institutional development.

**2. The ontology: where the concept and implementation separate**

The essay defines Class 3 by the automation of objective formation from a world of norms and
constraints, not by using many agents. It also explicitly distinguishes its repeated-game
performance argument from the Class 3 claim ([essay](/Users/isaacentebi/Desktop/FactoryLab/docs/essay.md:407)). That distinction
should become the organizing principle of this repository's claims.

| Object | What it should mean | What the repo currently establishes |
|---|---|---|
| World | An environment with consequences and opportunities not exhausted by seed tasks | Primarily venue events, compute scarcity, and optional registered extensions |
| Agent | A continuing locus of memory, spending authority, and commitments | Substantially implemented through working state, inboxes, lineages and subscriptions |
| Learning | Better decisions as a result of attributable evidence | Router learning plus memory; optional assembly learners; no demonstrated objective-discovery advantage |
| Institution | A durable arrangement several participants depend on and can revise | Registries and governance provide ingredients, but collective projects are not demonstrated |
| Value | What is worth doing, under the norms, in response to the world | Qualitative judgments and a narrow measured charter; independent usefulness evidence remains sparse |
| Darkness | Descriptions of internals lose predictive utility | Encryption and restricted observation are implemented; useful unpredictability is not established |

Three traps deserve particular attention.

**A. A mutable menu is not the same as endogenous purpose.** The population can register
assemblies, tools, observations, predicates, models and routers. This is real freedom, not a
fake UI. But custom output kinds must map to four reward shapes, router learners are drawn
from a bounded family, and a live assembly identity is replaced through retirement and
registration. See [registration](/Users/isaacentebi/Desktop/FactoryLab/factorylab/cortex/registration.py:76) and
[governance](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:735). These may be reasonable physics;
the issue is whether this grammar supports useful, unexpected organizations before budgets
expire. The latest run does not answer that.

**B. Scarcity selects survival, not necessarily interesting collective life.** Charging each
thought is coherent. But if a seat pays immediately for investigation while benefits arrive
late or accrue to other seats, defer-and-wait may be individually rational. A novelty reserve
offers temporary patience; it does not itself solve collective financing, ownership, or the
credit for maintaining something others use. Equal endowment shares are protected by genesis
lineage ([budget](/Users/isaacentebi/Desktop/FactoryLab/factorylab/kernel/budget.py:19)); that prevents replication from
minting larger grants, but also makes genesis lineage economically consequential.

**C. The environment's affordances are an inductive bias.** The seed prompts explicitly allow
revising objectives; it would be inaccurate to call them rigid trade-only instructions.
Nevertheless, repeated venue prices and immediately executable orders make trading concrete.
An optional service registry without an established demand channel makes selling much more
abstract. “Do anything” does not neutralize that asymmetry. The proposed explanation for
nonconstruction is therefore a hypothesis about affordances, incentives, and discovery—not
a settled fact that the models are lazy or the kernel forbids building.

Do not fix this by rewarding a quota of proposals, new agents, trades, or surprising prose.
Those are easy proxies to manufacture. Also do not discard observability to obtain darkness:
random behavior and missing telemetry can both look mysterious while producing nothing.

**3. Functional findings that should be repaired**

These are distinct from the conceptual design recommendations.

| Priority | Finding | Evidence and consequence | Required repair/test |
|---|---|---|---|
| P1 for experimental validity | Unknown outcomes increment the novelty trial counter | `_settle_due_forecasts` counts every top-level payoff, including `censored`; probe changes counter 0→1; this can prematurely exhaust remaining protection for population assemblies with settled history | Count resolved evidence separately from attempted trials; unknown venue outcome must not silently exhaust evidence-based novelty protection |
| P1 financial correctness | One verified payment can mint spending authority twice | Two supported receipt forms resolve to one simulated on-chain USDC transfer; actual treasury/runtime code books both; conservation still passes | Canonicalize from a unique verified chain log before deduplication and credit, never from unnormalized claim fields |
| P1 for governance validity | Unknown outcomes qualify voters | Five independent but censored producer decisions qualify `seed-decider`; zero uncensored payoffs | Require the specific qualifying evidence to be observed; test censored, settled, and mixed cases |
| P2 defensive accounting | Lot book accepts execution beyond an order's bound | Bind 1 BTC; fill 2 BTC; result is a 2 BTC lot and zero remaining | Track original ordered and cumulative executed quantity, quarantine inconsistencies, preserve legitimate late fills after cancellation |
| P2 usability | Evaluator tool capability is poorly represented in its outcome schema | `tool_calls` absent from evaluator schema properties, yet accepted by generic validation and dispatch | Advertise valid read/tool continuation shapes explicitly; test a judge reading evidence and refusing unauthorized writes |
| P2 learning claims | Self-declared propensity is not a verified sampling probability | Code accepts the declared distribution, floors chosen mass at .05, and uses it for assembly-learner updates | Separate claimed deliberation from kernel-sampled action probability; do not claim unbiased off-policy learning from unverifiable declarations |
| P1 before exposing paid services | Seller has no production-death admission check | Separate `serve.py` process retains services; `Seller.handle` verifies payment and runs the tool without consulting world liveness | Refuse new payment/production after a durable kill; explicitly reconcile in-flight payments; fail closed on stale liveness |

Evidence anchors: [novelty count](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/feedback.py:1029),
[counter contract](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/feedback.py:814),
[committee eligibility](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:1084),
[fill accounting](/Users/isaacentebi/Desktop/FactoryLab/factorylab/settlement/lots.py:374),
[evaluator schema](/Users/isaacentebi/Desktop/FactoryLab/factorylab/settlement/vocabulary.py:114),
[declared propensity](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/propensity.py:275),
[assembly training](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/compute.py:1302).

The censored-outcome findings were reproduced using actual runtime methods, not merely inferred from
comments. The novelty probe uses a seed and directly establishes the incorrect counter increment;
seed protection follows a separate rule, so it does not demonstrate that this seed loses protection.
The premature-protection consequence follows the population-assembly branch of
[routing](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/routing.py:358), once that assembly has settled history.
The overfill finding is a local invariant gap; this review did not establish that
the exchange actually sends overfills. Exact duplicate-fill deduplication is a separate defense.
Blindly rejecting every fill above *remaining* would itself be wrong after a cancel, because a
late venue observation can be legitimate. The repair must preserve original order quantity.

**The payment duplication is the strongest financial finding.** An offline proof supplied one
Base transaction containing one USDC transfer of 1,000,000 micro-units. It submitted two claims
with identical transaction, service and amount: `(recipient=None, log_index=None)` and
`(recipient=reserve, log_index=0)`. The actual `LiveRail.verify_receipt` confirmed both. The
actual treasury booked two income items; `_book_income` increased wallet spending authority
by 2,000,000 micro-units. `wallet.check_conservation()` returned `True`.

This does not create USDC on-chain; it creates falsely backed internal authority. The root
cause is a mismatch between [claim identity](/Users/isaacentebi/Desktop/FactoryLab/factorylab/world/treasury.py:210),
[verification defaults](/Users/isaacentebi/Desktop/FactoryLab/factorylab/world/treasury_rails.py:154), and
[booking](/Users/isaacentebi/Desktop/FactoryLab/factorylab/world/treasury.py:294): omitted fields are resolved permissively
by verification, but the verified identity is not used to deduplicate. The deployed seller's
`spool_earn` call actually omits `pay_to`, despite having the reserve address in scope.

The fix must select a unique actual transfer log and canonical chain id, transaction hash,
log index, token address and recipient; reject ambiguous matches or keep them unresolved;
and use that identity for exactly-once credit, across every ingestion path. Merely adding
`pay_to` to one caller leaves other representations and missing log indices unresolved.
Regression tests should cover omitted/explicit identity, spelling normalization, reordered
delivery, restart, two genuinely distinct logs, and conflicting amounts. No live exploit or
actual duplicated funds were observed. Reproduction scripts and exact output are included
with this review.

The propensity issue is bounded and partly acknowledged by the existing code. It does not
invalidate kernel-recorded router propensities. In a primitive demonstration, identical
`hold` reward 1 with declared probabilities .05, .50 and .95 produces next EXP3 hold
probabilities .7080, .5225 and .5118. The declaration can change learning intensity without
changing the action or its outcome. This is an illustration of the update rule, not evidence
that a live seat exploited it. If actual action-level importance weighting matters, let the
model propose an admissible menu and let the kernel sample and record the selected action.
Otherwise label this learner heuristic and measure it as such.

**A further design risk: learning to satisfy judges can outrun learning to satisfy the world.**
The producer's reward is a judge verdict less charter penalties
([loop](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/loop.py:1121),
[pricing](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/pricing.py:623)). Judge standing affects which judge
is selected ([routing](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/routing.py:527)); it is not a retrospective
correction of the producer reward. The router then consumes the settled score. This is not
automatically a bug: delegated evaluation can work. But it requires evidence that improved
judge approval predicts independent consequences. The existing record is too thin to establish
that bridge.

The single `censorship-bound` card is an intentional attempt to avoid a disguised profit or
activity objective. Adding many cards is not the recommendation. Any registered resolvable
predicate can contribute to forecast standing. Still, normative verdict calibration targets
one minus attributed charter blame ([settler](/Users/isaacentebi/Desktop/FactoryLab/factorylab/settlement/settle.py:447));
with one narrow card, “not blamed” is not a complete test of usefulness, reciprocity, or sound
judgment. Preserve qualitative contestability and add independent consequence evidence for
the actual undertakings the population chooses.

**The build-and-sell path is fragmented.** An ordinary registered tool can be published as a
service, and fake-facilitator tests exercise that route. A stateful program assembly or watcher
cannot be sold by the same mechanism: service validation requires a `known_tools` identity,
and the seller reconstructs a `PopulationTool`, not a continuing assembly. See
[service validation](/Users/isaacentebi/Desktop/FactoryLab/factorylab/cortex/registration.py:313),
[registration](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:900), and
[execution](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/seller.py:243). A program can be requested internally;
making its behavior externally available currently requires a separate tool implementation.

The public program proposal shape also omits the accepted `trigger` field
([schema](/Users/isaacentebi/Desktop/FactoryLab/factorylab/cortex/schematics.py:173)), making watcher construction harder
to discover. Publishing a tool authored by someone else gives the author monetary entitlement
while the service-registration decision receives consequence attribution. That can be a
reasonable split, but it is not an explicit partnership or revenue-sharing agreement.

External serving requires separately starting `deploy/serve.py` and supplying network exposure
described in the runbook. The default runtime unit does not establish a public marketplace.
More seriously, [the refresh loop](/Users/isaacentebi/Desktop/FactoryLab/deploy/serve.py:79) leaves the last catalogue in
place even when reads fail, and neither catalogue reconstruction nor
[payment admission](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/seller.py:328) checks terminal production
state. This source-level failure path matters if services are deployed; this review did not
send a post-kill payment or establish that an exposed live seller is running. Death must cover
every production surface, including the independently hosted seller.

**4. Why it heats the laptop and slows iteration**

There are two separate issues: the test workflow buys too much simulation for routine
feedback, and the runtime repeatedly constructs information it already has.

The default [pytest configuration](/Users/isaacentebi/Desktop/FactoryLab/pyproject.toml:29) runs every available CPU via
`-n auto`. This machine reports 14 logical CPUs. It excludes network and marked slow tests,
but still includes expensive world tests. Concurrent full gates multiply that load. During
this audit other Python work and macOS background indexing were also using CPU; the fan noise
cannot be attributed entirely to FactoryLab from a process snapshot.

The central measured runtime path is:

`routing feasibility → required request size → world block → all seat views → each seat's
directory → enumerate and sort the entire artifact archive`.

Only a bounded preview is displayed, but the implementation builds the complete directory
first, repeatedly, for different seats. See
[feasibility](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/routing.py:494),
[seat views](/Users/isaacentebi/Desktop/FactoryLab/factorylab/cortex/schematics.py:1175),
[per-seat directory](/Users/isaacentebi/Desktop/FactoryLab/factorylab/cortex/schematics.py:926), and
[archive enumeration](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/compute.py:542).

Small offline baselines, with scripted provider and in-memory ledger:

| Tick budget | Delivered internal events | Invocations | Artifact references | Full archive enumerations | Wall time |
|---:|---:|---:|---:|---:|---:|
| 10 | 121 | 38 | 93 | 935 | 0.643 s |
| 20 | 238 | 73 | 165 | 1,913 | 1.371 s |
| 40 | 544 | 175 | 366 | 4,865 | 3.935 s |

These are local baselines under concurrent load, not rigorous isolated benchmark comparisons.
The 40-tick case enumerated the full archive about 28 times per model invocation. A separate
profile of 20 ticks took 3.26 seconds with profiling overhead: world-block work accumulated
1.95 seconds and ledger appends 1.62 seconds; those nested cumulative times must not be summed.
It produced 14,241 ledger appends. The path gets more expensive as history grows.

The test fixture adds another avoidable cost:
[ScriptedRun](/Users/isaacentebi/Desktop/FactoryLab/tests/conftest.py:32) unpickles the entire result for *each* `.summary`,
`.entries`, `.requests`, or `.runtime()` access. Cross-worker fixture caching already exists,
so “add a cache” is not a sufficient diagnosis. The cached object's representation and repeated
whole-result decoding are the problem. The cache is also session-local: separate pytest
invocations do not share the expensive simulation.

Persistent [ledger appends](/Users/isaacentebi/Desktop/FactoryLab/factorylab/kernel/ledger.py:681) canonicalize, hash,
encrypt, open, write, fsync and close per row. This provides a durability boundary; simply
turning off fsync would buy speed by changing correctness. The in-memory path still serializes,
hashes and encrypts. Whole-state snapshots and historical state add further work; quantify
their contribution before redesigning persistence.

Recommended order, with no claimed speedup until measured:

1. Cap default laptop workers at two; run one integration gate at a time. Preserve a full
   release gate, but label a fast developer check separately. Do not rename slow failures
   “fast passing tests,” remove assertions, or exclude scenarios to claim an optimization.
2. Compute an artifact directory snapshot once per archive mutation generation and derive
   private/public seat views from it. Start with reuse within one world-block render, then
   consider broader caching with explicit invalidation on writes, publication, ownership
   changes, garbage collection and restore. Preserve privacy and deterministic ordering.
3. Avoid constructing every seat's complete world view just to estimate affordability for
   one seat. Reuse immutable sections and generation-bound dynamic facts while maintaining
   a conservative reservation ceiling. An underestimate would become a money bug.
4. Split cached scripted-run fields so reading a summary does not decode requests, entries
   and state. Preserve detached mutable values for tests that intentionally mutate copies.
5. Use small deterministic scenarios for narrow assertions; reserve long trajectories for
   explicit integration properties. Keep process-kill/durability testing intact.
6. Benchmark 10/40/150/500 ticks with the same manifest/seed and semantic output digest.
   Measure wall time, CPU, peak RSS, rows, archive scans and snapshot bytes. Only then set
   performance budgets and consider deeper event/index compaction.

Exact rational Blum–Mansour solves deserve scale limits, but the measured small-world
bottleneck was rendering/archive work, not that algorithm. Optimize the observed path first.

**5. The world I would build next**

I would make the next experiment a small society with persistent, jointly funded projects.
Keep trading available as one optional livelihood. Keep the existing memory, budgets,
artifact store, program runner and registry. Add the missing social contract with the smallest
possible enforceable representation.

A project would have an identity independent of its founding seat, a population-written
purpose, participants, an escrowed budget, artifact/version references, milestones, a
resolution rule, an expiry/exit rule, and explicit authority to spend its resources. Participants
would opt in. Kernel code would enforce authorized transfers and collateral, not judge the
project's purpose. The participants could amend the agreement through its declared procedure.

This is a proposal, not an existing capability or an excuse to build an entire DAO framework.
Start with one escrow, one deliverable, one acceptance rule and a refund-on-expiry path. Allow
a second seat to commission work and reuse the output. A project with no takers may expire.
There should be no reward merely for founding one.

The current plan explicitly deferred project funding contracts
([Edition 3](/Users/isaacentebi/Desktop/FactoryLab/docs/plans/edition3.md:8)). For a society-first goal, that deferral now deserves
reversal. Persistent *individual* state solved one problem; persistent *shared* commitments
solve a different one. New names or chat messages alone will not create institutions.

A finite endowed society can be a successful first experiment even if it does not yet fund
itself. Report its subsidy and remaining lifetime plainly, and test self-sustainability as a
separate later property. Otherwise the initial society may spend its entire life defending
runway before it has developed anything worth sustaining.

For example, two seats might fund a shared watcher, disagree about its reliability, commission
an independent test, change their payment agreement after it fails, and appoint a new maintainer
who inherits the service rather than the founder's private thoughts. That sequence would be
interesting because ownership, knowledge, trust and resources actually change. It is an example
of what the world should permit, not a plot to insert into the agents' prompts.

Three candidate worlds, as experimental designs rather than promises:

| World | What participants can do | What would make it interesting | Main failure mode |
|---|---|---|---|
| Research commons | Commission observations, data checks, watchers and reusable analyses; share or sell outputs | A group chooses an uncertainty, divides work, revises a method and is reused by someone else | Expensive mutually flattering reports |
| Infrastructure cooperative | Pool funds for tools or monitoring; agree maintenance and access terms; replace a failing maintainer | A useful institution survives its founder and changes its rules after a failure | Private costs with shared benefits make nobody maintain it |
| Charter laboratory | Propose competing measurements and governance procedures; forecast effects; run reversible trials | The population discovers that its own proxy is harmful and adopts a better arrangement | Self-certifying metrics and control-loop oscillation |

My first choice is the research commons with the ability to become the cooperative. It starts
from working primitives already in this repo and allows specialization, reciprocity, dependency,
conflict and institutional revision without prespecifying the institution. A society might
choose to remain simple; complexity is not itself success.

External demand should eventually enter through a stable, precommitted interface—real users
or other independent systems able to discover and buy outputs—not ad hoc gifts from the
architect when the population disappoints. A private service registry is not a customer base.
Synthetic consumers can test mechanics, but cannot establish real market demand.

**6. Prediction markets: yes as an experiment, after choosing their job**

The repo's forecasts are immutable scored commitments. Its `market` registration adds venue
instruments. Neither is a prediction market with positions, collateral and a trading price.

The essay specifically discusses conditional markets for constraints and their prices
([essay](/Users/isaacentebi/Desktop/FactoryLab/docs/essay.md:598)). That is a closer conceptual fit than adding another external
speculation venue. But your society-first goal suggests beginning with a smaller use:
participants can disagree about project outcomes and price information that would resolve
that disagreement.

For example, a project offers a watcher that claims to detect a condition cheaply. Other
participants can forecast whether the frozen version will meet a declared accuracy/cost test
on a held-out stream. A funder can use those beliefs when deciding whether to commission it.
The contract funds work; the market aggregates beliefs. They are separate mechanisms.

The minimal experiment needs a frozen question/predicate/version, horizon, source and
resolution policy, collateral, maximum subsidy/loss, positions bound to identities, and
true/false/unknown settlement. Unknown must have a precommitted unwind/refund treatment;
it must not silently become false. All transfers stay in integer micro-units and cannot
increase aggregate spending authority. Internal trading profit is redistribution, not external
income. Exploration funding and survival reserves should not be available as unlimited market
maker subsidy.

Start with a few batch-cleared collateralized questions, or a bounded market maker only if
sparse participation requires it. Market scoring rules are a principled way to support
information aggregation without waiting for a matching counterparty
([Hanson](https://hanson.gmu.edu/mktscore.pdf)); that mathematical machinery does not create
independent information or reliable resolution. The connection between cost-function markets
and no-regret learning is real ([Chen and Vaughan](https://arxiv.org/abs/1003.0034)), but it
does not establish that nine correlated model seats will form a useful society.

Keep market prices advisory initially. Do not directly bind them to the spending or violation
metric that resolves their own bets. Separate independent forecasting from a participant
promising to cause an outcome: the latter is closer to a performance contract. Avoid letting
a trader act as its own oracle. Preserve immutable evidence and a bounded dispute process.

Conditional governance markets have an additional limit: after adopting policy A, policy B's
outcome is unobserved. Refunding the unchosen branch is honest; treating an observed outcome
as a causal comparison is not. A frozen historical baseline is a comparator, not randomization.
Use paired sandbox trials or precommitted randomized choices where feasible if the experiment
needs a causal claim. Do not call this full futarchy merely because ballots contain forecasts.

The market earns its continued existence only if it improves forecast quality or funding
decisions relative to cheaper independent forecasts, after computation and subsidy costs.
If it only adds transactions and prompts, remove it.

**7. A finite path to launch, instead of another indefinite rewrite**

Separate three decisions: whether mechanics are trustworthy, whether collective agency appears,
and whether mainnet operation is justified. One does not substitute for another.

| Stage | Deliverable | Evidence needed to leave the stage |
|---|---|---|
| Repair iteration speed | Archive/view reuse and fixture decode improvements; bounded worker default | Same scenario outcomes and assertions; measured before/after CPU, wall time and memory; no weakened durability |
| Repair evidence semantics | Censored-trial and voter-eligibility fixes; explicit evaluator tool contract; accounting anomaly handling | Adversarial reproductions fail before and pass after; current full and crash/resume gates accounted for |
| Prove one collective loop offline | Minimal shared-project contract and reliable capability discovery | Commission→escrow→artifact→independent reuse→addressed result→revision or closure, including failed delivery and refund |
| Run a bounded genesis experiment | A frozen build, roster, budget, interfaces and duration | No architect steering; reported actual use, costs, failures and institutional changes |
| Decide expansion | Mainnet and/or broader external interfaces | Current launch checks and explicit deployment/funding authorization; an experiment worth continuing |

Judge a genesis run by evidence such as: a self-chosen shared undertaking persists over wakes;
a second lineage voluntarily funds or depends on it; an adverse result changes its method or
agreement; a reusable artifact reduces later work; commitments settle honestly; and its cost
fits the precommitted budget. These are observer criteria, not instructions to force a quota
of institutions into the population.

Use matched baselines: current Edition 3; a simpler fixed-role society with the same tools and
budget; and the project-enabled variant. Add markets only after that comparison is interpretable.
Use several bounded runs rather than declaring success from one picturesque transcript. Exact
budget and sample size should be set after measuring the repaired runtime, not invented here.

If there is no collective loop, inspect where it failed: discovery, capability, financing,
coordination, execution, resolution, or actual value. Fix that seam rather than responding with
more roles and more charter language. If a fixed-role baseline performs just as well at lower
cost, accept that result.

For model selection, the useful unit is cost per completed reliable undertaking. Arithmetic
screens and JSON validity alone missed the long-context failures in Run 5. Screen the exact
prompt, route settings, tools, inbox and multi-turn construction path. Stronger reasoning
may belong in rare construction or adjudication work, but that is a measured design choice,
not something a price table alone can settle. This review does not claim a researched current
provider ranking or prescribe an unverified roster.

**8. What I would keep, simplify and stop claiming**

Keep integer money, bounded authority, custody separation, irreversible production death,
addressed receipts, private working state, independent judgment, versioned evidence, and
explicit unknowns. These are useful even if the factory hypothesis fails.

Simplify repeated world descriptions, duplicate historical views, whole-result test decoding,
and the expectation that routine edits require everyone to run the full simulation suite.
Consider shallower evaluation by default if an ablation shows deeper tiers buy no predictive
value. Do not delete a judgment layer on aesthetics alone.

Stop using “many tests,” “no-regret,” “sealed,” “real fills,” or “agents can propose changes”
as substitutes for demonstrated autonomous institution-building. Stop describing an old
rehearsal as validation of a newer code/roster combination. Reconcile the README's older
four-norm and shutdown descriptions with Edition 3's five norms and configured wind-down;
the distinction between ordinary shutdown and an explicit kill should be stated precisely.

The work so far is not worthless. It has built much of the machinery required to run a serious
experiment. But the next success should be a small society doing one consequential thing
together that survives contact with evidence—not another architecture diagram or another
unbounded audit-and-patch cycle.

**Verification record**

See `verification.md` in this directory for commands, exact gate status, reproduced findings,
and the limits of the isolated snapshot. Review artifacts are the only files created here;
existing implementation edits belong to the other ongoing work.
