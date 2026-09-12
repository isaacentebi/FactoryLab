# Factory Lab

Factory Lab is an attempt to build the Class 3 factory of *The Superdark Factory* (Poliks, Trillo, Dunn, Scott-Douglas and Springett) as a small world. One wallet holds real money on Hyperliquid. Short-lived model assemblies are woken by the world's events, buy their own thinking from that wallet at vendor prices, trade, judge each other, and propose new parts of themselves. Every item-level record goes into a sealed diary nobody can read while the world lives. The experimenter plays the essay's architect: one committed move before launch (the manifest, the kernel, the seed charter), then nothing except reading the wake and, if it comes to it, pressing kill. No human supplies an objective. Four norms are the only input; the population writes the metrics, the prices and the clock.

## The governing test

The essay's success criterion is bewilderment. The factory must be "a bewilderingly complex alterity, continuously so, in order to be worth it" (*The Superdark Factory*, Chapter I). If the experimenter can predict what the world will do, the build has failed, however green the tests are. Spec v0.5 §9 condition 6 makes this a minimum check: one registration whose proposal came from a model's return rather than a script, and one router whose most frequent action is not what the seed predicts. Every live run is graded against it in `docs/build-log.md`; none has passed cleanly.

## How it maps to the essay

| Essay principle | Where it lives |
|---|---|
| Class 3: objectives derived from norms and constraints, never supplied | Four read-only norms in `charter/charter.py`; no goal text in any request; objectives emerge from cards, prices and routers |
| Darkness: interior information is useless from outside; read the wake | Sealed ledger in `kernel/ledger.py`; five aggregate views only; `runtime/wake.py` |
| The Stackelberg move: one committed first move, then no intervention | `worlds/<name>.toml` hashed into the genesis item; a changed manifest is a new world name; no mid-run mutators |
| Kernel vs charter: hard casts as physics, soft casts as priced penalties | `factorylab/kernel/` (ten invariants); `factorylab/charter/` (cards, λ controller, amendments) |
| Primitives with explicit contracts, no semantic coupling | Registry contracts for models, assemblies, routers and tools in `kernel/registry.py` and `cortex/` |
| Rich request line; thin, stateful reward line with a propensity score | `cortex/request.py`; `kernel/queue.py` (`DecisionQueue`, `PropensityRecord`, five score channels) |
| No-regret learners at the frontier, no-swap-regret learners at the core | `learners/exp3.py`, `learners/hedge.py`, `learners/blum_mansour.py`, `learners/delayed.py` |
| Learning death prevented as physics: an unhistoried share of spend | `kernel/reserve.py` (10 percent novelty reserve per window) |
| Versioning by behaviour: transfer operator, spectral gap, four pathologies, early warning | `factorylab/versioning/`, read-only over a diary |
| Evaluations online and recursive; more judges than producers | Evaluators and metas inside the loop; `MetaVerdict` tiers; cascade gate in `runtime/cascade.py` |
| Evaluations graded by realized consequence, outside the input | Kernel predicate `return_paid_off`; FIFO lots in `settlement/lots.py`; Brier against a prevalence baseline |
| Adversarial minority manufacturing real failures | `antagonist` role, paid on `exposure` (fooling a judge), trading the same account |
| Charter co-written by sortition; λ as a shadow price the factory posts | `charter/committee.py` (five seats by lot, three to pass); amendments may carry λ and the tick |
| The 3:1 cascade with jitter; upper loops starved of variety | `kernel/timing.py` (`TimingRegistry`, `UpwardBuffer`); metas see distributions, not returns |
| The $0 token budget as the last kill switch | `balance_floor_usd = "0"`; compute insolvency in `runtime/loop.py` |

Status per principle: `docs/design-audit-v2.md`. Binding specs: `docs/build-spec-v0.4.md` through `v0.7-phase4.md`.

## The physics

The kernel is ten invariants (spec v0.4 §1). Code enforces them; nothing ever states them to the population. The essay's reason: a restriction not promoted into physics is read as advice to route around, so each invariant must be met as a fact about the world, not a rule. Changing any of them kills the world; what follows is a new v0.

- Conservation. The wallet changes only through metered debits, exchange settlements and the declared drip.
- Metering before return. No model call, order or purchase returns until its cost is debited. An unaffordable action is infeasible and logged.
- Death at zero. Balance at or below the floor terminates the world. Termination is final. The diary key is released only then.
- Addressability. Every sampled action has a persistent handle and a logged propensity that replays its sample. Late rewards settle to the original handle.
- Novelty reserve. A share of each window's spend is usable only by actions with no performance record. The charter cannot abolish it.
- Information boundaries. Schematics, contracts, prices and the charter are public. Learner state is private. No component holds the full topology.
- Timing registration. Upward reports are buffered into distributions with a minimum 3:1 separation and jitter. Downward commands are prompt.
- Author neutrality. Requests carry no author identity. Lineage lives in the sealed ledger.
- Sealed ledger. Append-only, hash-chained, encrypted at rest. Aggregates are computed by kernel code, never by reading items.
- Non-intervention. After launch the experimenter has kill, and nothing else.

Money is integer micro-USD. There are no floats in the wallet; decimals appear only at venue boundaries. Every state change is a ledger item before it is a state change.

## The loop in one heartbeat

The world's events drive a deterministic nervous system that is always awake. Model assemblies are woken selectively, and each waking costs money.

1. Tick. The clock emits `Tick`; the venue adapter emits `MarketMid`, `Funding` and `Fill`; the drip, if declared, emits `Drip`.
2. Routers. For each event kind, one or more no-regret routers choose an assembly or `NOOP` from the registered contracts after a feasibility filter against the wallet. The draw and its propensity are logged under a decision handle.
3. Producers. The chosen assembly is built from its contract (model, prompt, tools, memory), sees the public world block and its own private memory, and returns JSON: a hold, an order, tool calls, a proposal, an amendment, a forecast. Metering debits before the reply returns.
4. Evaluators. The `ProducerReturn` event (the event answered and the return, nothing private) routes to judges, who score it in [0, 1]. Each verdict is also a sealed forecast that the return will pay off.
5. Metas. Verdicts travel upward as distributions. A meta's score settles the judge's whole window on the `conformity` channel. Deeper tiers exist only if the population registers a meta that accepts `MetaVerdict`.
6. The cascade. Nothing moves upward until at least three inner closes have passed, jittered, so no governing loop corrects against unfinished transients.
7. Settlement. Lots close first-in-first-out against later fills. A return pays off when its realized P&L, net of fees, funding and its own compute, is positive. That settles the judge's forecast, updates `ConsequenceStanding`, and lands as a reward on the original handle.
8. Prices. At each window close the controller measures every card, prices violation as a per-card λ, and subtracts λ·cost from rewards. Passed amendments activate at the next boundary.

## What the population can do

- Proposals. Register an assembly with a prompt it wrote; a model from the OpenRouter catalogue or an `x402:` or `venice:` seller, at any reasoning level; a router, added or replacing; a sandboxed tool.
- Tools. Ten venue tools over Hyperliquid (`venue.place_market`, `venue.place_limit`, `venue.close`, `venue.positions` and the rest), `market.discover`, `catalogue.search`, `treasury.transfer`, and population-written tools at a flat per-call charge.
- Amendments. Add, replace or remove metric cards under the four norms. A committee of five seats drawn by lot across roles votes without seeing proposers; three yes votes pass; the new edition activates at the next window.
- λ. An amendment may carry a starting price for its card; the controller continues from it.
- The clock. An amendment may change the tick interval within kernel bounds: a venue floor and a ceiling derived from the cascade ratio.
- The market. The factory discovers x402 inference sellers on the public index and pays them per request from its own reserve.

Refused registrations carry a public reason. Norms are read-only.

## Money

Three pots, one number. The kernel wallet is the single accounting truth; the pots are a view the reconciler checks within $0.50.

| Pot | Holds | Moves |
|---|---|---|
| `venue` | Hyperliquid margin | Both ways by `treasury.transfer`, over native CCTP through HyperEVM to Base |
| `reserve` | USDC at the factory's own Base address (`reserve.key`) | Pays x402 sellers per request; funds Venice tranches |
| `seed` | OpenRouter credits bought before launch | Depleting; nobody refills it |

Treasury moves are two-phase and ledgered (`treasury.submitted`, then `confirmed` or `failed`); pots move on confirmation. Both directions were accepted end to end on testnet, with transaction references in the build log. Mainnet routing constants exist but no CLI can select them; mainnet behaviour is unverified.

Insolvency: if no affordable provider exists for twenty consecutive routed decisions, the world terminates with `insolvency:compute`. A factory that starves its compute with money still in the venue died of its own liquidity management.

After launch the experimenter never refills a pot, changes the drip, edits the manifest, restores an older ledger, upgrades the host, or reads the diary.

## What runs 1 to 7 taught us

Seven live testnet runs, each a few hundred to eleven hundred events and under $0.50 of compute. Summaries are in `docs/runs/`; diaries and keys are never committed.

- Run 1: 167 of 198 replies empty. Reasoning models spent their whole output budget thinking. Reasoning became a per-tier contract setting.
- Run 2: no forecasts, no proposals, because nothing had described their shape. Every request now carries the public schematics.
- Run 3: six registrations from model returns: three assemblies the models named and wrote, three router replacements. Bewilderment met at its minimum; the proposals were observational.
- Run 4: nothing. The cheapest producers returned `noop` every time and judges graded that 0.8 to 1.0 for "care with scarce resources". Failed.
- Run 5: two registrations; the antagonist fooled judges 44 times in 50; zero fills because the testnet account held no testnet USDC.
- Run 6: four registrations, sixteen refusals; all 202 producer returns settled `not_paid_off` under the verdict-as-forecast physics; still zero orders, because the order shape had never been public.
- Run 7: order shape public, and producers still held 112 times in 119. Failed.

The noop attractor is the essay's stable failure, and its diagnosis (build log, run 7) is four links in our own design, not a bug. Nobody was told how they were scored. The reward line stopped at the router and never reached the judge. Every seed card favours inaction. The seed producers were the two cheapest tiers with reasoning off. Merged since: a public `scoring` block, rewards landing in the primitive's private memory, producers reseeded on GLM 5.3 Flash and DeepSeek 4.1 Flash. In flight: an observations catalogue so the population's own passing cards (`docs/charter/edition1-draft.md`: 24 proposed, 8 passed, $0.019) can be priced. Run 8 is the first fair test.

Plainly: nothing has yet happened that we could not have predicted. The gate passes; the experiment does not.

## How to run it

Requires Python 3.12 or later and `uv`.

```bash
uv sync
uv run ruff check . && uv run pytest
```

Scripted worlds need no network or keys:

```bash
uv run factorylab run --world scripted --events 400 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
```

Testnet needs three keys at the repo root, mode 0600, gitignored: `openrouter.key` (an OpenRouter API key), `hyperliquid.key` (a Hyperliquid testnet private key funded from the faucet) and `reserve.key` (created once by `reserve init`, which prints only the address). The experimenter creates them; the CLI loads them into the environment. The code never prints, logs or commits a key value, and neither should you.

```bash
uv run factorylab reserve init
uv run factorylab probe --world testnet
uv run factorylab run --world testnet --events 200 --seed 8 --ledger runs/r8.jsonl --kill-at-end
uv run factorylab report runs/r8.summary.json
```

Killing the `uv run` wrapper does not kill the world: the child process can keep running. Use the world's explicit kill mechanism to end it; for a crash rehearsal, target the actual world process.

`--kill-at-end` ends a budgeted rehearsal by explicit kill so the seal key is released. Only then can the diary be read:

```bash
uv run factorylab postmortem runs/r8.jsonl runs/r8.jsonl.key --kinds event:Registered,invocation
uv run python -c "from factorylab.versioning.reader import read_diary; from factorylab.versioning import summary, render; print(render(summary(read_diary('runs/r8.jsonl', 'runs/r8.jsonl.key'))))"
```

Versions, pathologies and early-warning signals are a library over the diary; there is no `versions` subcommand yet.

Resume and wake:

```bash
uv run factorylab resume --world testnet --ledger runs/r8.jsonl
uv run factorylab wake --ledger runs/r8.jsonl --out www
```

`resume` continues a world whose process died, from the last window snapshot plus replay, then reconciles the venue; a resumed scripted world's summary equals an uninterrupted run's. `wake` publishes the five sealed aggregates plus venue and reserve balances, the only live view of a running world. Mainnet is refused unless the world is named `funded`; that manifest does not exist.

## Cold audit and launch checklist

`docs/audit-brief.md` calls for five independent auditors in separate worktrees, each having read the essay first. Three answer: is this Class 3, which pathologies does the design invite, will it run, are the contracts thin and rich enough, how can it be gamed. Two hunt defects only. Findings go to `docs/audits/`. None exists yet.

Then, in order: one real x402 purchase from the reserve for cents (so far only unsigned quotes, $0 spent, no seller's reliability known); run 8 with the observations catalogue; the cold audits; a rehearsal of process death, restart, backup restore and the death alert on a disposable world, per `deploy/README.md`; a pinned commit and `worlds/funded.toml`; one launch; then no changes, ever.

## Deliberately not built

- Futarchy for constraints. The essay reports mixed results, and a $100 world has no market depth.
- Reproduction of child factories (Chapter III). Out of scope for a lab.
- Integral and derivative terms in the price controller; revisit if versioning shows oscillation.
- Rewarding producers for wallet growth, or a norm that says "grow". That would be the architect writing an objective. Pressure reaches producers only through judges who answer to money.
- Any human channel after launch, including the treasury.

## Glossary

- Class 1, 2, 3. Factories that automate execution, planning and objectives respectively. Only Class 3 sets its own standard of "better".
- Darkness. Interior information is disclosed but useless for prediction, audit or steering. Read the wake instead.
- Stackelberg move. The architect's single committed move before launch; here, the manifest and kernel.
- Kernel, charter. Hard casts enforced as physics; soft casts, editioned, priced, amendable by the population.
- Primitive, contract. A composable unit and the input/output shape it exposes.
- Request line, reward line. Rich self-describing requests; thin scores returning to the decision handle with a propensity.
- No-regret, no-swap-regret. Learners converging to the best single action (EXP3, Hedge), and to the best action per condition (Blum-Mansour).
- Version. A near-invariant region of behaviour, read from score profiles, not configuration.
- The four pathologies. Stable failure, overfitting, learning death, thrash.
- Realized consequence. Whether a verdict predicted what actually paid off in the wallet.
- Sortition. Committee seats drawn by lot from the population.
- The 3:1 cascade. An inner loop closes at least three times before the outer loop sees its distribution.

## Location

The project lives at `/Users/isaacentebi/Desktop/FactoryLab`. Remote: github.com/isaacentebi/FactoryLab (private).
