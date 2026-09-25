# Factory Lab

A small, real-money implementation of the Class 3 factory described in *The Superdark Factory* (Poliks, Trillo, Dunn, Scott-Douglas, Springett). One wallet, one exchange account, and a population of short-lived language-model agents that buy their own thinking, trade, judge each other, and rewrite their own rules. The person who builds it makes one move, at launch, and then never touches it again.

The essay's claim is that a factory whose objectives are supplied from outside is a Class 2 factory no matter how clever its automation. A Class 3 factory derives what it is trying to do from norms and constraints, and its architect must not be able to predict what it will become. This repository is an attempt to build one honestly, small enough to read, with enough money at stake that the results mean something.

## How it works

**The world.** A world is a wallet in integer micro-dollars, an exchange (Hyperliquid, or a deterministic fake), a clock, and a sealed ledger. Every event the world produces (a tick, a price, a fill, a funding payment) is delivered to the population. Every state change is a ledger item first: encrypted, hash-chained, append-only. The ledger's key is released only when the world dies. The world sets no leverage ceiling and no principal cap of its own: the population uses whatever leverage and collateral the venue allows, and the venue's refusal is the only limit (the manifest keys `[tools] max_leverage` and `[venue] principal_usd` are deprecated and inert). Public data is readable for any coin the venue lists, and a connector may pay for data through x402 under a per-call cap.

**The population.** An assembly is a model, a prompt, a contract saying which events it accepts and what it returns, and a budget. Assemblies are not processes; they exist only while answering an event. Each answer is a model call paid from the wallet at the vendor's price, so thinking is a cost like any other, and an assembly that spends more than it is worth to the factory is selected against. The seed population (edition 6) is fourteen assemblies of five kinds: producers, which act on the market or decline to; evaluators, which judge a producer's return; metas, which judge the judges and each other, tier upon tier; adversarial judges, paid only when the world proves the judge they read wrong; and antagonists, paid for making judges miss worse than they usually do. The kernel refuses a world that seeds judging unless its evaluator seats outnumber its producer seats on at least three model families, and no seat ever judges a chain whose two nearest authors share its foundation family.

**One turn of the loop.** An event arrives. A router (a bandit learner over the assemblies that accept that event) samples one, records the probability with which it was chosen, and wakes it. The assembly returns a structured answer: an action, perhaps an order, perhaps tool calls, perhaps proposals. If it placed an order, the fill enters a lot book. Its return is routed to a judge, which answers with a verdict on the return against the charter; the return settles on the mean of its judges' verdicts. A verdict is also a prediction: when the world measures the return (whether it paid off, for one that traded; what the trade it declined would have made, for one that executed nothing, whose contract requires it to name that trade and a coin the world lists), the verdict is scored by Brier against that outcome. The judge settles on that score and on the conformity grade a meta gives it, the meta is scored against the judge's score in turn, and an antagonist is paid by how much worse than usual the judges were about its return. A share of returns is read by two judges on different families, so their disagreement is measured; variance, autocorrelation and that disagreement are computed at every window close and shown to the evaluators alone. A chaos actuator injects real, bounded faults into what seats are shown (a venue read unavailable, stale mids, a withheld tool result, a connector timeout), and none of them can move money. Every score flows back to the handle of the decision that earned it, and the routers update. That is the whole reward line: thin, delayed, and attributable.

**Learning.** Routers run the mean-based no-regret learner EXP3 at the frontier and a no-swap-regret construction (Blum–Mansour) where decisions need to be correct per condition, not just on average. A share of every window's spend is reserved for assemblies with no history, so novelty is never starved out by incumbents. Any assembly may propose a new model, a new assembly, a replacement router, a tool written in Python that runs in an OS jail, a measurement of its own over the public window facts, a market the venue lists, a forecast predicate that runs in the jail like an observation, or a connector: an https source the population votes in and the kernel fetches from on its behalf, free unless the source itself charges through x402. The wallet moves only when money moves: every debit is a real outflow to a named counterparty (a provider's bill, a seller's price, a venue or rail fee), and a resource that costs nothing at the margin, such as retained working state or a jailed tool call, is a limit, never a charge. A registration also declares which of four reward shapes pays the kind it emits. Registrations pass kernel validation; connectors also need a committee majority, while charter amendments and retirements wait for a governance boundary.

**The charter.** Four norms are fixed at launch: truthful commitments, care with scarce resources, useful inquiry, and the capacity to revise inadequate practices. Everything else the population writes within the manifest's fixed bounds. A metric card names a measurable quantity, an acceptable region, and which role answers for it. A card may answer for any registered kind, not only the four seed roles. A price controller turns violations into penalties on that role's rewards; the penalty ratchets while the violation lasts and decays when it stops. Cards, prices and the length of the tick are amended by proposal and a committee drawn by lot. Observations register through validation and can then be named by cards. Amendment, connector and retirement votes settle later against compliance with the promised card's frozen acceptable region and measurement. The population can retire any assembly, including the seeds, and the roles themselves are contracts it registers: what an assembly accepts and what it emits, not a fixed pipeline.

**The kernel.** A short list of rules is enforced in code and never stated to the population, because a rule that is merely announced is read as advice. Money is conserved. Nothing returns before it is paid for. An unaffordable action is infeasible. Death at or below the balance floor is final. Every sampled action is addressable and its propensity is logged. The novelty reserve cannot be abolished. Nothing judges its own output. Population code has no network; the world is reached only through registered rails. Assembly ids and contracts, prices and the charter are public. Each request names its own assembly; models behind ids, prompts, learner state, router menus and who judged whom remain sealed. Reports upward are buffered and jittered so the slow loops stay slower than the fast ones.

**Compute.** The factory buys inference from three rails: prepaid OpenRouter credit, Venice paid in USDC over x402, and any seller on the public x402 index. It holds its own reserve on Base, moves money between the exchange and the reserve through its own treasury tool, and tops up its Venice balance itself. When every rail is unaffordable the world ends; nobody refills it.

**Time.** The factory keeps its own clock. The tick is a charter parameter the population can change within physical bounds. Governance uses measured consequence ages in world ticks with a configured backstop floor, and every evaluation horizon (a judgement's timeout, a consequence's backstop) counts world ticks, never the runtime's internal events. The live clock measures delivered tick gaps, and governance converts tick periods with the interval the loop actually achieved.

**Storage.** The diary is the append-only record; a checkpoint is a continuation, not history. The world keeps one rolling checkpoint beside its diary (`runs/<world>.checkpoint/`), compressed and sealed under the diary's key, written and flushed to stable storage before the diary's `snapshot` item names its SHA-256; older checkpoint files are removed once the newer one is named. A recorded answer (`io.result`) larger than 1 KiB is kept the same way, once per distinct answer, in `runs/<world>.io/`. Resume refuses a checkpoint or answer that is missing, stale, foreign or altered, and writes nothing to the diary when it does; it never falls back to an older checkpoint, since the factory never rewinds. Every diary item a scripted world writes is at most 64 KiB (`ITEM_CAP_BYTES`), so the diary grows with events, not with the world's state. At each checkpoint the world drops retained state no reader can reach (event history before the oldest open forecast, the payload of a return no judgement can accept any more, feedback a router has already read) and ledgers what the checkpoint cost against its period; one that costs more than `1/min_ratio` of its period is recorded as `checkpoint.slow`, a fact and never a kill.

**What the architect does.** Writes one manifest: the seed assemblies, the prices, the norms, the bounds, and a charter whose cards the seed population drafted itself before launch. Its hash is the first ledger item. After launch there is one live view, a page of sealed aggregates and balances, and one control: kill. There is no refill, no restart with new settings, no reading the diary while the world lives.

**How it ends.** The balance reaches zero, compute becomes unaffordable, or the architect kills it. Then the seal is released and the diary can be read. A versioning library reads it by behaviour rather than configuration: a transfer operator over score profiles, its spectral gap, the four pathologies the essay names (stable failure, overfitting, learning death, thrash), and the early-warning signals that preceded them.

## Try it

Python 3.13 and `uv`. The scripted worlds need no network, no keys and no money.

```bash
uv sync
uv run factorylab run --world scripted --events 500 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
```

The first runs a deterministic world for 500 events, registers an assembly learner, activates a card amendment and a seed evaluator retirement on separate governance boundaries, and prints a summary. Its orders size from the world wallet, and its consequence backstop is 20 ticks. Every seat spends from its own entitlement (edition 2, C10), and what a seat's trades realise at the venue is its claim on the venue's custody, never compute money drawn from or paid into the other seats' pool: the summary's `venue_custody` reconciles the claims with what the venue settled. A router that draws nobody now settles that draw as inapplicable instead of manufacturing a producer return for the judges to grade, so evaluation stops buying work nobody authored and the run reaches the scripted population observation, which the script places past its 1,600th producer call, inside these 500 events. The second halves the price of BTC four times under a leveraged long; gap liquidation can take the wallet below zero, and the world dies of `balance_zero` and releases its key. Then the tests:

```bash
uv run pytest                                     # check: the inner loop, 2 workers, about a minute
uv run pytest -m "check or gate" -n 4             # the merge gate: adds every test that runs a world
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
uv run pytest -m gate tests/audit/test_r2a_lifecycle.py   # one gate file you touched
```

Tests are in three tiers, assigned in `tests/conftest.py`. `check` runs no world, and a
`check` test whose call takes over 2 s fails and asks to be marked `@pytest.mark.gate`
(`FACTORYLAB_CHECK_LIMIT_S=5` raises the limit on a slow machine, `=off` disables it).
`gate` is every test that runs a world or reads a shared scripted run. `slow` kills and
resumes real processes at every ledger write. More workers: `-n 8`, or `-n auto` for
every core (hot on a laptop); the last `-m` and `-n` given win over the defaults.
`uv run python scripts/bench_scripted.py 50 100 200` times the scripted world and
prints digests of its diary, so a performance change can show it changed nothing else. `uv run factorylab --help` lists the rest: validating a manifest, resuming a world, publishing the wake page, reading a dead world's diary, versioning it.

Running against Hyperliquid testnet needs an exchange key and a model-provider key at the repository root (`hyperliquid.key`, `openrouter.key`, mode 0600, never committed). The shipped testnet manifest uses `PURR/USDC` spot, a configured reserve address, a 120-second tick and a 60-tick consequence backstop. Running with real money additionally needs the reserve key and a manifest named `funded` with an explicit `[charter]`; the code refuses mainnet without them. The mainnet spot names for the re-draft are `UBTC/USDC` and `UETH/USDC`.

To end a persistent world, stop its running process to release the writer lock, then run `uv run factorylab kill --world W --ledger L` with its original manifest and ledger. This records `explicit_kill:operator`, releases the seal and exits `3`. Stopping the process alone leaves the world resumable. Failures from `run` and `kill` may print a second stderr line naming the exception class and factory module beneath the reason code.

On live worlds, `run --duration 30m` uses a wall-clock deadline checked between ticks and an event ceiling; work already in progress may finish after the deadline. A budgeted live run finishes with one read-only order/fill reconciliation pass before sealing;
its summary distinguishes acknowledged order statuses from ingested fills. Shutdown does not
close venue positions. `order-status --world W --client-id ID` reads an original order identity
using that manifest’s namespace, without starting or resuming a world. Client order IDs are
also bound to the run that made them, so add `--launch-nonce` with the `launch_nonce` from that
run’s `Launch` event; worlds that launched before launch-bound identities omit it.

`probe --max-tokens` sets the model probe budget, default `256`; a paid but empty x402 completion fails the probe. The wake reads only the world's configured live accounts and publishes no open positions.

## Layout

```
factorylab/
  kernel/      wallet, ledger, events, registry, decision queue, novelty reserve, timing, termination
  cortex/      assemblies, requests and returns, registration, tools and the jail, the public world block
  learners/    EXP3, Hedge, Blum–Mansour, delayed feedback, routers
  settlement/  lots, forecasts, scoring, consequence attribution
  charter/     norms, cards, amendments, committee, price controller
  runtime/     the loop and its parts: routing, governance, pricing, feedback, venue, resume, wake, immune organ
  versioning/  reading a dead world's diary by behaviour
  world/       exchange adapters, model providers, x402 client, treasury rails, metering
worlds/        manifests: scripted, scripted-crash, testnet, an example charter edition
tests/         one directory per package; tests/audit holds the reproductions from the cold audits
deploy/        cloud-init, systemd units, backups, alerting, the jail probe, the single-host runbook
docs/          the current spec, build log, audit reports, manifest reference, research notes; docs/history holds superseded plans
scripts/       one-shot scripts (compute proof, charter drafting)
```

`docs/manifest.md` documents every manifest key, its default, and whether it is a hard cast.

## Rules

- Never read, print or commit a `*.key`. The CLI loads them; nothing else should.
- Never create `worlds/funded.toml` before the launch checks in `deploy/README.md` are met.
- Never steer a running world. No refill, no new manifest, no restore, no upgrade, no reading the diary. The wake page is the only view; `factorylab kill` is the final operator control.

## Status

Experimental. It has run on testnet with real fills, bought inference from all three rails with real USDC, and survived process interruption and resume at every ledger write. It has not yet run with real money on mainnet. Three rounds of cold audits against the essay are in `docs/audits/`.

The essay is the experimenter's document and is not tracked here; `docs/essay.md` is where the code expects a copy. Its standard is the one this project holds itself to: if the architect can predict what the factory becomes, it is not the factory the essay describes.
