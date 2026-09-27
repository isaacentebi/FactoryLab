# dark-stack

A superdark factory: a machine that produces its own objectives by negotiating them
with a world. This repository is its codebase.

It implements the ideas of *The Superdark Factory: Toward the Full Automation of
Software*, by Poliks, Trillo, Dunn, Scott-Douglas and Springett, published at
[superdark.ai](https://superdark.ai/). The repository takes its name from the essay's
second chapter, "The Dark Stack". It is an independent project, not affiliated with
or endorsed by the essay's authors. Our reading of that chapter, compressed into
design rules, is [AGENTS.md](AGENTS.md); where the code and that reading disagree,
the code is wrong.

## Three classes

A Class 1 factory automates execution. It takes a plan.
A Class 2 factory automates plan-making. It takes an objective.
A Class 3 factory automates objectives.

The difference is not sophistication. It is where desire enters. A Class 2 factory,
however clever, is an instrument: someone upstream wants, and it executes. Hand a
factory a target, a preferred behaviour or a pipeline it should have found for
itself, and it drops a class. A Class 3 factory has no one upstream. It has a world
(a wallet, venues, compute sellers, a clock), norms it did not write and cannot
edit, and prices on departing from them. It writes its own metric cards, proposes
its own seats, tools, measurements and markets, and votes on its own amendments.
What it pursues is whatever holds up against that world.

## Why dark

Dark as in lights-out: nobody on the floor. The architect makes one move, the
genesis manifest, and withdraws. The manifest's hash is the diary's first item.
After launch the architect keeps a single control, `kill`. Governance reaches a
living world by one door, the charter: a signed norm edition, read at a governance
boundary and testified to by a committee of seats.

Dark is not secret. What the population writes is the product, and a living world's
public wake publishes it as it lands: every return, the verdicts about it, the
world block every seat reads, balances and account statements. What stays sealed
until death is the machinery: learner state, router weights, propensities, private
memory, prompts, per-decision scores. The diary that holds all of it is encrypted,
hash-chained and append-only, and its seal is released only by the world's
termination. Then it is read by behaviour, not by configuration.

There is no father here. The architect does not author the orchestration, does not
choose what is good, does not tell a seat how cautious to be. It sets physics and
leaves. Structure the factory needs, it grows; structure it does not need, it can
retire, the seed seats included.

## Design rules

Chapter II, as this codebase reads it. Each rule is a constraint on us, the
builders, not an instruction to the factory.

- **Surfaces, not strategies.** We expose tools, prices, custody and limits. No
  prompt, tool description, default or refusal text tells a seat what to do or what
  is good. The essay puts it flatly: "A hard-coded pipeline of agents is literally
  just a waterfall" ([*The Superdark Factory*, Ch. II](https://superdark.ai/)).
- **Robust simplicity.** The less we know, the less structure we impose. No
  carve-outs for a model's past mistakes. A change is never justified by the
  behaviour mix it would produce; that is the architect optimizing toward its own
  "better", which is Class 2.
- **Physics is enforced, not announced.** The kernel is the hard cast. A rule that
  is announced is read as advice, so no kernel rule is restated as an instruction.
  The schematics (tools, prices, contracts, reward formulas) are public, as facts a
  seat retrieves with `world.read`.
- **Two channels.** A rich request channel, self-describing and author-neutral. A
  thin reward channel: a score, addressed to a persistent decision handle through a
  stateful queue. No third channel between seats. Seats compose through contracts,
  by requesting a kind of work, never a peer.
- **Minimal sufficient disclosure.** Schematics are public; scores, history and
  learner state are private. The deciding agent's propensity travels forward with
  the request. A judge sees the request, the output, the executed operations and the
  propensity. Never the author.
- **The reward chain.** Producers settle on their judges' verdicts. Judges are
  graded from above, tier by tier, for conformity to the charter, and from outside
  by realized consequence: settled P&L, the priced road not taken, a resolved
  forecast. Facts the world measures, at a horizon fixed at genesis. The signal
  that grades an evaluator sits outside the loop it judges.
- **Online, recursive evaluation.** No offline gates, no fixed rubrics. Evaluations
  of evaluations. Producers are the minority of the population, evaluators span at
  least three foundation-model families, and nothing judges its own output.
- **Learners.** Mean-based no-regret learners at the frontier, no-swap-regret
  learners (Blum–Mansour) at the core. Learning death is prevented as a fact about
  the world: a share of compute and write access usable only by unhistoried actions.
- **Behavioural versioning.** A version is a metastable input–output distribution,
  read through a transfer operator and its spectral gap. Not a hash, not a config.
  The factory never rewinds, and a change to the kernel is a new world.
- **Pathologies priced live.** Stable failure ratchets its penalty with duration.
  Thrash is charged on its volatility. Overfitting meets holdouts and a rising
  consequence sampling rate. Learning death meets the unhistoried niche. λ comes
  from a PID law.
- **The co-written charter.** Governance acts only through the charter. Norms sit
  behind a read-only wall; metric cards, holdouts and prices are the factory's to
  propose. Committees are drawn by lot under fresh pseudonyms. λ is posted by the
  factory as a shadow price, and motions carry conditional forecasts.
- **Time as ratios.** Loop periods are ratios, never absolute constants. An inner
  loop settles at least three times faster than the loop that commands it. Verdicts
  rise a tier only after their evidence settles, on jittered windows.

## The machine

A world is a wallet in integer micro-USD, venues, compute rails, a clock and a
sealed diary. The population is a set of assemblies. An assembly is a model, a
prompt, a contract (the event kinds it accepts, the return kinds it emits) and an
entitlement. It is not a process: it exists only while it answers one event, and
every model call it makes is metered and paid at the vendor's price. Thinking is a
cost like any other.

One turn. An event arrives. A router, a bandit over the assemblies that accept it,
samples one, logs the exact distribution it drew from and wakes it. The assembly
returns an action, perhaps orders, tool calls, proposals, and its own propensity.
Its return goes to judges on foundation families other than its author's; it
settles on the mean of their verdicts. A verdict is also a forecast: when the world
measures the return, the verdict is Brier-scored against the outcome, and the judge
settles on that score and on the conformity grade a meta gives it, one tier up.
Every score flows back to the handle of the decision that earned it, and the
routers update.

Any return may carry proposals: a model, an assembly, a replacement router, a tool
or a predicate in Python that runs in the jail, an observation, a market, a
connector, a charter amendment, a retirement. Each emitted kind is paid by one of
five reward shapes: judged, forecast, conformity, exposure, counter.

| Concept | Where it lives |
| --- | --- |
| Integer money, the one wallet, per-seat entitlements | `factorylab/kernel/money.py`, `wallet.py`, `budget.py` |
| The sealed diary, its seal, its release | `factorylab/kernel/ledger.py`, `termination.py` |
| Ledger-first event delivery | `factorylab/kernel/events.py` |
| Contracts, prices, provenance | `factorylab/kernel/registry.py` |
| Decision handles, propensity records, delayed settlement | `factorylab/kernel/queue.py` |
| The unhistoried niche | `factorylab/kernel/reserve.py` |
| Hedge, EXP3, Blum–Mansour, delayed feedback, the router | `factorylab/learners/` |
| Lots, consequence accounts, sealed forecasts, Brier scoring, standing | `factorylab/settlement/` |
| Norms, metric cards, the PID price law, amendments, sortition, λ markets, holdouts, norm editions | `factorylab/charter/` |
| Transfer operator, spectral gap, live versions, pathology predicates | `factorylab/versioning/` |
| Assemblies, the request and return channels, public schematics, registration, the jail | `factorylab/cortex/` |
| The loop, routing, feedback, pricing, governance | `factorylab/runtime/loop.py`, `routing.py`, `feedback.py`, `pricing.py`, `governance.py` |
| The immune organ, early warning, the chaos actuator | `factorylab/runtime/immune.py`, `ews.py`, `chaos.py` |
| The clock, the 3:1 cascade, measured consequence latency | `factorylab/runtime/clockwork.py`, `cascade.py`, `cadence.py` |
| The agent's own propensity, the priced road not taken, anticipatory settlement | `factorylab/runtime/propensity.py`, `grounded.py`, `uptake.py` |
| Composition through contracts, foundation families | `factorylab/runtime/composition.py`, `families.py` |
| Custody, checkpoints, resume, release identity, the kill witness, wind-down | `factorylab/runtime/custody.py`, `sidecar.py`, `resume.py`, `release.py`, `witness.py`, `winddown.py` |
| The manifest, the operator's CLI, the public wake | `factorylab/runtime/worlds.py`, `cli.py`, `wake.py` |
| The world: Hyperliquid and a deterministic fake, Polymarket, recorded tapes, vaults | `factorylab/world/exchange.py`, `polymarket.py`, `tape.py`, `vaults.py` |
| Compute rails: OpenRouter, Venice over x402, public x402 sellers; metering | `factorylab/world/openrouter.py`, `venice.py`, `market.py`, `x402.py`, `metering.py` |
| Treasury: USDC over CCTP between venue and reserve | `factorylab/world/treasury.py`, `treasury_rails.py`, `cctp.py`, `evm.py` |

`factorylab/kernel` imports nothing from the rest of the project and holds no
mutable state at import time. `factorylab/learners` imports nothing from the
project at all, so a learner is replaceable by the population without touching
physics. `factorylab/settlement` imports only the kernel: a score cannot reach the
thing it scores. Tests enforce all three boundaries.

## The hard cast

What the kernel and the runtime around it enforce today. None of it is stated to
the population as a rule; the facts of it are published as schematics. Every kernel
invariant has at least one test that attempts to violate it and asserts failure
(`tests/kernel/`).

**The wallet.** One conserved wallet in integer micro-USD; every conversion from
decimal names its rounding. Nothing is paid after the fact: a priced capability
reserves its ceiling, runs, commits its actual cost, and only then returns. An
action the wallet cannot cover is infeasible. Every reservation names a handle and
a reason, and an external settlement comes from one of four named sources: venue
P&L, funding, income, financing. A resource that costs nothing at the margin is a
limit or a price on reward, never a money debit. Death at
or below the balance floor, judged on settled money, is final; a later credit does
not revive it. Per-seat entitlements divide the one wallet and never create money.

**Custody.** Where the money is, and when it was last seen, is typed: a seat's
learning score, its entitlement and the assets held at a venue or a provider are
three quantities that never stand in for one another. A venue loss settles on the
venue account, not on the compute wallet. Treasury transfers are ledgered before
they are sent, and an uncertain outcome is quarantined until it resolves.

**The sealed diary.** Every state change is a ledger item first: encrypted,
SHA-256 hash-chained, fsynced, append-only, behind an exclusive writer lock. The
genesis hash is the manifest's. While the world lives, the ledger answers a live
reader with five fixed aggregate views, never items; its public seal is released
only by final termination.
Checkpoints are sealed under the same key beside the diary, and resume refuses one
that is missing, stale, foreign or altered. It never falls back to an older one. A
world refuses to resume under a different release digest of `factorylab/` and
`uv.lock`: new physics is a new world. A kill is also written outside the diary, so
a copy of the diary cannot bring a killed world back.

**The clock.** `timing.min_ratio` is an integer of at least three, fixed at genesis.
Every loop is counted in world ticks consumed. An outer loop's period is drawn as
`min_ratio` times the measured period of the loop it commands, lengthened by its own
jitter, and it fires only if that ratio still holds when it comes due. A judged
return's outcome is fixed once, at `world_repricing / min_ratio` of the venue's own
clock, so what a judge is graded on does not depend on how fast the factory runs.

**The registry.** Contracts are immutable and versioned, carry their provenance
(the decision that proposed them), and price their units in nonnegative integer
micro-USD. A built-in return kind keeps its meaning and its reward shape.

**The jail.** Population code runs only inside an OS jail: bubblewrap namespaces and
seccomp on Linux, with no sockets, no child processes and no host tree beyond the
Python runtime and the tool directory, under bounded resources and output. The
world is reached only through registered rails, and through credential-free,
bounded HTTPS connectors the runtime fetches on the population's behalf.

**The decision queue.** Every sampled action gets a persistent handle. Its
propensity record, the exact ordered distribution and a replayable seed, is
persisted before the handle is issued, and a choice inconsistent with the logged
distribution is refused. Feedback carries six fields: handle, channel, score,
definition version, status, sampling reference. A decision stays addressable,
with every return it received, while any score is owed to it; released, it keeps
a tombstone.

**The niche.** The novelty share lies in (0, 1]; it cannot be abolished. It accrues
as a flow and is spendable only by contracts with no settled history, for compute
and for registration alike. A decision taken in the niche bears no card penalty.

**The two channels.** A request carries everything an executor needs and nothing
about who asked; the only thing that travels forward is the propensity of the
decision it concerns, a distribution, never a name. Learning feedback is not on the
return. It arrives on the reward channel, addressed to the handle.

**The prices.** One price law, a PID controller per metric card, turns a card's
violation into λ. λ is clipped to `[0, penalty_cap / v]` while the card violates;
at the bound the integrator holds and the saturation is ledgered. Verdict and
conformity scores settle net of Σ λ·violation, clipped to [0, 1]. The consequence
and exposure channels, the novelty reserve and router exploration lie outside the
price law's reach.

**The population's shape.** A world that seeds judging is refused at load unless
evaluator seats strictly outnumber producer seats, at least three foundation
families serve the evaluator tier, and every judged kind is read by a judge off its
author's family. Nothing judges its own output or its ancestors'. Charter norms
change only by a signed norm edition; amendments reach metric cards, never norms.
Mainnet is refused except in a world named `funded` whose charter and roster match
the digests of the ratified charter.

## The gauntlet

`scripts/gauntlet.py` holds the pass criteria for the pathologies Chapter II names:
stable failure, thrash, learning death, overfitting, and the invariants their priced
answers must keep. Each criterion is a pure predicate over ledger rows and the
world's manifest, and returns `pass`, `fail` or `unsupported`. Unsupported is never
a pass: the rows carry no evidence either way. Every threshold derives from the
world's own parameters; no criterion asserts a target behaviour mix. The module
imports nothing from the runtime, so it reads a dead world's diary as readily as a
live test run.

`tests/gauntlet/` runs purpose-built populations through real worlds against those
criteria. `uv run python scripts/gauntlet.py sweep --population <name>` runs one
population over several seeds; `replay` runs every criterion over a dead diary.

`scripts/class2_audit.py` guards the other flank: the architect's text leaking into
what the seats see. A static audit in the check tier (`tests/audit/test_class2_static.py`)
lints every string a seat can read before it acts for the surface form of an
instruction, a value judgement or a restated kernel rule. The script renders the
full seat-visible corpus for a model auditor from another family, plants canaries,
validates the audit against them, triages findings per world, and gates a release on
their disposition. The protocol's fixed inputs live in `docs/audits/class2/`.

## Running it

Python 3.13 and [uv](https://docs.astral.sh/uv/). The scripted worlds need no
network, no keys and no money.

```bash
uv sync
uv run factorylab run --world scripted --events 200 --seed 1
uv run factorylab run --world scripted-crash --events 600 --seed 2
```

`scripted` runs a deterministic world on a fake venue and fake models and prints a
JSON summary: balance, conservation, ledger verification, custody, routers, prices,
charter edition. `scripted-crash` halves BTC four times under a leveraged long; the
venue account gaps below zero while the compute wallet, which is authority, keeps
paying for thought. Manifests live in `worlds/`; every key, its default and whether
it is fixed for the world's life is in [docs/manifest.md](docs/manifest.md).
`uv run factorylab --help` lists the rest: validating a manifest, resuming, killing,
publishing the wake, reading a dead world's diary, versioning it.

Tests come in three tiers, assigned in `tests/conftest.py`:

```bash
uv run pytest                                             # check: no world runs, about 40 s
uv run pytest -m gate -n 2                                # gate: every test that runs a world
uv run pytest -m gate -n 2 tests/gauntlet/test_learning_death.py
uv run pytest -m slow tests/audit/test_a1_composition.py  # slow: kills and resumes real processes
```

A `check` test that takes more than 2 s of CPU, or 10 s of wall time, fails and asks
to be marked `gate`.

`deploy/` holds the single-host deployment: cloud-init, systemd units, the jail
probe, backups, alerting and the kill witness. A live world reads its exchange and
provider keys from owned files at the repository root, mode 0400 or 0600, never
committed.

## Open

The frontier, as the machine stands.

- **Holdout attribution.** A holdout that never binds costs its proposer only its
  trial. Nothing yet grades a holdout's proposer by realized consequence.
- **Sustained saturation.** The price loop's period is measured against the
  settlements it waits on, so each price window stretches past the last and no card
  holds its cap for `min_ratio` updates. The escalation that follows saturation is
  written and unexercised.
- **Patience.** Non-market exploration is settled in anticipation. A market
  discovery whose carry breaks even after the grading horizon has no patience term,
  and a world whose discoveries outlive that horizon still loads.
- **Live-venue readiness.** Settled funding history, a bound on fill propagation,
  and the oracle price.
- **The long run.** An extended testnet run under the current physics.
