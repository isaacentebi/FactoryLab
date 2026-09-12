# Factory Lab

A small, real-money implementation of the Class 3 factory described in *The Superdark Factory* (Poliks, Trillo, Dunn, Scott-Douglas, Springett). One wallet, one exchange account, and a population of short-lived language-model agents that buy their own thinking, trade, judge each other, and rewrite their own rules. The person who builds it makes one move, at launch, and then never touches it again.

The essay's claim is that a factory whose objectives are supplied from outside is a Class 2 factory no matter how clever its automation. A Class 3 factory derives what it is trying to do from norms and constraints, and its architect must not be able to predict what it will become. This repository is an attempt to build one honestly, small enough to read, with enough money at stake that the results mean something.

## How it works

**The world.** A world is a wallet in integer micro-dollars, an exchange (Hyperliquid, or a deterministic fake), a clock, and a sealed ledger. Every event the world produces (a tick, a price, a fill, a funding payment) is delivered to the population. Every state change is a ledger item first: encrypted, hash-chained, append-only. The ledger's key is released only when the world dies.

**The population.** An assembly is a model, a prompt, a contract saying which events it accepts and what it returns, and a budget. Assemblies are not processes; they exist only while answering an event. Each answer is a model call paid from the wallet at the vendor's price, so thinking is a cost like any other, and an assembly that spends more than it is worth to the factory is selected against. The seed population is nine assemblies of four kinds: producers, which act on the market or decline to; evaluators, which judge a producer's return; metas, which judge the judges; and an antagonist, paid for exposing judges who were wrong.

**One turn of the loop.** An event arrives. A router (a bandit learner over the assemblies that accept that event) samples one, records the probability with which it was chosen, and wakes it. The assembly returns a structured answer: an action, perhaps an order, perhaps tool calls, perhaps proposals. If it placed an order, the fill enters a lot book. Its return is routed to an evaluator, which answers with two numbers: a verdict on the return's quality, and a probability that the return will pay for itself. The verdict is graded later by a meta for conformity; the probability is graded by what actually happened in the wallet once the position closed. Every score flows back to the handle of the decision that earned it, and the routers update. That is the whole reward line: thin, delayed, and attributable.

**Learning.** Routers run no-regret learners (EXP3, Hedge) at the frontier and a no-swap-regret construction (Blum–Mansour) where decisions need to be correct per condition, not just on average. A share of every window's spend is reserved for assemblies with no history, so novelty is never starved out by incumbents. Any assembly may propose a new model, a new assembly, a replacement router, a tool written in Python that runs in an OS jail, a measurement of its own over the public window facts, or a connector: an https source the population votes in and the kernel fetches from on its behalf, metered per call. Proposals are validated by the kernel, and accepted ones join the population immediately.

**The charter.** Four norms are fixed at launch: truthful commitments, care with scarce resources, useful inquiry, and the capacity to revise inadequate practices. Everything else the population writes. A metric card names a measurable quantity, an acceptable region, and which role answers for it. A price controller turns violations into penalties on that role's rewards; the penalty ratchets while the violation lasts and decays when it stops. Cards, prices, the length of the tick, and the observations themselves are amended by proposal and a committee drawn by lot, whose votes are settled later against what the amendment actually did. The population can retire any assembly, including the seeds, and the roles themselves are contracts it registers: what an assembly accepts and what it emits, not a fixed pipeline.

**The kernel.** A short list of rules is enforced in code and never stated to the population, because a rule that is merely announced is read as advice. Money is conserved. Nothing returns before it is paid for. An unaffordable action is infeasible. Death at a zero balance is final. Every sampled action is addressable and its propensity is logged. The novelty reserve cannot be abolished. Nothing judges its own output. Population code has no network; the world is reached only through registered rails. Learner state is private; contracts, prices and the charter are public; no assembly sees the whole topology. Reports upward are buffered and jittered so the slow loops stay slower than the fast ones.

**Compute.** The factory buys inference from three rails: prepaid OpenRouter credit, Venice paid in USDC over x402, and any seller on the public x402 index. It holds its own reserve on Base, moves money between the exchange and the reserve through its own treasury tool, and tops up its Venice balance itself. When every rail is unaffordable the world ends; nobody refills it.

**Time.** The factory keeps its own clock. The tick is a charter parameter the population can change within physical bounds. Governance moves only as fast as the slowest measured consequence loop allows.

**What the architect does.** Writes one manifest: the seed assemblies, the prices, the norms, the bounds, and a charter whose cards the seed population drafted itself before launch. Its hash is the first ledger item. After launch there is one live view, a page of sealed aggregates and balances, and one control: kill. There is no refill, no restart with new settings, no reading the diary while the world lives.

**How it ends.** The balance reaches zero, compute becomes unaffordable, or the architect kills it. Then the seal is released and the diary can be read. A versioning library reads it by behaviour rather than configuration: a transfer operator over score profiles, its spectral gap, the four pathologies the essay names (stable failure, overfitting, learning death, thrash), and the early-warning signals that preceded them.

## Try it

Python 3.13 and `uv`. The scripted worlds need no network, no keys and no money.

```bash
uv sync
uv run factorylab run --world scripted --events 400 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
```

The first runs a deterministic world for 400 events and prints a summary. The second halves the price of BTC four times under a leveraged long; the world dies of `balance_zero` and releases its key. Then the tests:

```bash
uv run pytest
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
```

The second set kills and resumes real processes at every ledger write. `uv run factorylab --help` lists the rest: validating a manifest, resuming a world, publishing the wake page, reading a dead world's diary, versioning it.

Running against Hyperliquid testnet needs an exchange key and a model-provider key at the repository root (`hyperliquid.key`, `openrouter.key`, mode 0600, never committed). Running with real money additionally needs the reserve key and a manifest named `funded`; the code refuses mainnet under any other name.

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
- Never touch a running world. No refill, no new manifest, no restore, no upgrade, no reading the diary. The wake page is the only view.

## Status

Experimental. It has run on testnet with real fills, bought inference from all three rails with real USDC, and survived kill-and-resume at every ledger write. It has not yet run with real money on mainnet. Two rounds of cold audits against the essay are in `docs/audits/`; the fixes they produced are in the build log.

The essay is the experimenter's document and is not tracked here; `docs/essay.md` is where the code expects a copy. Its standard is the one this project holds itself to: if the architect can predict what the factory becomes, it is not the factory the essay describes.
