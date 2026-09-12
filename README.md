# Factory Lab

Factory Lab is the Class 3 factory of *The Superdark Factory* (Poliks, Trillo, Dunn, Scott-Douglas, Springett) built as one small world with one wallet. Ephemeral LLM assemblies are woken by that world's events, buy their own thinking from the wallet at vendor prices, trade on Hyperliquid, judge each other's returns, and propose new models, assemblies, routers, tools and amendments to their own charter. The experimenter plays the essay's architect: one committed move before launch — the manifest and the kernel — and after that nothing but reading the wake and, if needed, pressing kill.

## What it needs

Python 3.13 (`.python-version`, `deploy/cloud-init.yaml`) and `uv`. Nothing else for the scripted worlds: no network, no keys, no money.

## Run it in ten minutes

```bash
uv sync
uv run factorylab run --world scripted --events 400 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
```

`scripted` runs 400 events against a deterministic fake venue and model provider in about half a minute and prints a JSON summary. `scripted-crash` halves BTC four times under a leveraged long; that world dies of `balance_zero` and releases its seal key. Then the test gate:

```bash
uv run pytest
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
```

The second kills and resumes real subprocesses; it is deselected by default. `uv run factorylab --help` lists the other subcommands, and `uv run factorylab manifest --world scripted` prints a manifest's hash — the identity a launched world is sealed to.

## What the files are

- `factorylab/` — the package, below.
- `worlds/` — world manifests, one TOML each, hashed into the genesis ledger item.
- `tests/` — the suite, one directory per package.
- `deploy/` — cloud-init, systemd units, backup, alert and jail scripts, and the single-host runbook.
- `docs/` — specs, the build log, audit reports, charter drafts, research notes, run summaries.
- `scripts/` — one-shot scripts whose artefacts live under `docs/`.
- `outputs/` — earlier design documents, superseded by `docs/`.

Inside `factorylab/`:

- `kernel/` — the physics: the conserved wallet, the sealed hash-chained ledger and its five aggregate views, event delivery, capability contracts, the decision queue, the novelty reserve, timing registration, termination, integer micro-USD.
- `charter/` — the four read-only norms, metric cards, amendments, the sortition committee, the λ price controller.
- `cortex/` — the primitives: assemblies built from contracts, the rich request line and thin return line, registration proposals, the public schematics every assembly sees, population tools and their OS jail.
- `learners/` — EXP3 and Hedge, Blum–Mansour, handle-addressed delayed feedback, the sampling router and its feasibility filter.
- `runtime/` — the loop and what drives it. `loop.py` is the loop proper — launch, event processing, the producer, evaluator and meta steps, snapshots, termination — over method groups in `bootstrap`, `routing`, `governance`, `venue`, `pricing`, `feedback`, `compute` and `summary`, with the public world block in `cortex/schematics.py`. Also here: the CLI, manifests, resume, the wake page, the immune organ, the observations catalogue, governance cadence, the cascade gate.
- `settlement/` — FIFO lots, sealed forecasts and their scoring, consequence standing, the launch-declared predicate vocabulary.
- `versioning/` — a read-only library over a dead world's diary: transfer operator, spectral gap, behavioural versions, the four pathologies, early-warning signals.
- `world/` — everything outside: clock and drip, exchange adapters (a deterministic fake and Hyperliquid), model providers (OpenRouter, Venice, x402 sellers), metering, venue tools, the treasury rails over CCTP, the scripted provider.

## Never do this

- Never read, print or commit a `*.key`. `openrouter.key`, `hyperliquid.key`, `reserve.key` and a ledger's seal key belong to the experimenter; the CLI loads them into the environment and never emits a value.
- Never create `worlds/funded.toml` before the launch gates in `docs/handoff.md` and `deploy/README.md` are met. Mainnet is refused unless the world is named `funded`, and that manifest is written once.
- Never touch a running world: no refilled pot, no edited manifest, no restored ledger, no upgraded host, no reading the diary. `factorylab wake` is the only live view; the diary opens only after termination releases its key.

## The essay

The essay is the experimenter's document and is not tracked here; `docs/essay.md` is where the repository expects a copy. Its success criterion governs this build: a factory its architect can predict is not worth building, so green tests are not evidence of anything.

### How the code maps to it

| Essay principle | Where it lives |
|---|---|
| Class 3: objectives derived from norms and constraints, never supplied | Four read-only norms in `charter/charter.py`; the population writes the metric cards, the prices and the clock |
| Darkness: interior information is useless from outside; read the wake | Sealed ledger in `kernel/ledger.py`; five aggregate views only; `runtime/wake.py` publishes nothing else |
| The Stackelberg move: one committed first move, then no intervention | `worlds/<name>.toml` hashed into the genesis item; a changed manifest is a different world; no mid-run mutator exists |
| Kernel and charter: hard casts as physics, soft casts as priced penalties | `factorylab/kernel/` enforces; `factorylab/charter/` prices, through cards, λ and amendments |
| Primitives with explicit contracts, no semantic coupling | Registry contracts for models, assemblies, routers and tools in `kernel/registry.py` and `cortex/` |
| Rich request line; thin, stateful reward line with a propensity score | `cortex/request.py`; `kernel/queue.py` (`DecisionQueue`, `PropensityRecord`, five score channels) |
| No-regret learners at the frontier, no-swap-regret learners at the core | `learners/exp3.py`, `learners/hedge.py`, `learners/blum_mansour.py`, `learners/delayed.py` |
| Learning death prevented as physics: an unhistoried share of spend | `kernel/reserve.py`; each manifest declares a tenth of the window |
| Versioning by behaviour: transfer operator, spectral gap, pathologies, early warning | `factorylab/versioning/`, read-only over a diary; `factorylab versions` |
| Evaluations online and recursive; more judges than producers | Evaluator and meta steps inside the loop; `MetaVerdict` tiers; `runtime/cascade.py` |
| Evaluations graded by realized consequence, outside the input | Predicate `return_paid_off`; FIFO lots in `settlement/lots.py`; Brier against a prevalence baseline |
| Adversarial minority manufacturing real failures | The `antagonist` role, paid on the `exposure` channel for fooling a judge, trading the same account |
| Charter co-written by sortition; λ as a shadow price the factory posts | `charter/committee.py`, five seats by lot and three votes to pass; an amendment may carry a starting λ and the tick |
| The 3:1 cascade with jitter; upper loops starved of variety | `kernel/timing.py` (`TimingRegistry`, `UpwardBuffer`); metas see distributions, not returns |
| The $0 token budget as the last kill switch | `balance_floor_usd = "0"` in every manifest; compute insolvency in `runtime/loop.py` |

### The physics

Code enforces the kernel; nothing states it to the population, because a restriction not promoted into physics is read as advice to route around. Changing any of it kills the world.

- **Conservation.** The wallet changes only through metered debits, exchange settlements and the declared drip.
- **Metering before return.** No model call, order or purchase returns before its cost is debited. An unaffordable action is infeasible.
- **Death at zero.** Balance at or below the floor terminates the world, finally. The diary key is released only then.
- **Addressability.** Every sampled action carries a persistent handle and a logged propensity that replays its draw; late rewards settle to that handle.
- **Novelty reserve.** A declared share of each window's spend is usable only by contracts with no settled history. The charter cannot abolish it.
- **Information boundaries.** Schematics, contracts, prices and the charter are public; learner state is private; no component holds the full topology.
- **Timing registration.** Upward reports are buffered into distributions with a minimum 3:1 separation and jitter. Downward commands are prompt.
- **Author neutrality.** Requests carry no author identity. Lineage lives in the sealed ledger.
- **Sealed ledger.** Append-only, hash-chained, encrypted at rest; aggregates come from kernel code, never from reading items.
- **Non-intervention.** After launch the experimenter has kill, and nothing else.

Money is integer micro-USD; decimals appear only at venue boundaries. Every state change is a ledger item first.

### Glossary

- **Class 1, 2, 3.** Factories that automate execution, planning and objectives. Only Class 3 sets its own standard of better.
- **Darkness.** Interior information is disclosed but useless for prediction, audit or steering; read the wake instead.
- **Stackelberg move.** The architect's single committed move before launch; here, the manifest and the kernel.
- **Kernel, charter.** Hard casts enforced as physics; soft casts, editioned, priced, amendable by the population.
- **Primitive, contract.** A composable unit, and the input/output shape it exposes.
- **Request line, reward line.** Rich self-describing requests; thin scores returning to the decision handle with a propensity.
- **No-regret, no-swap-regret.** Learners converging to the best single action, and to the best action per condition.
- **Version.** A near-invariant region of behaviour, read from score profiles rather than configuration.
- **The four pathologies.** Stable failure, overfitting, learning death, thrash.
- **Realized consequence.** Whether a verdict predicted what actually paid off in the wallet.
- **Sortition.** Committee seats drawn by lot from the population.
