# Cold audit, round three: does it work, and is it the factory the essay describes?

Six seats read main as it is, fresh, with no contact between seats and no build history. The first question for every seat is the plain one: **is the intended functionality actually there, and what is broken?** The second is the essay's.

## Required reading, in this order

1. `README.md`. It says what the factory is and how to run it.
2. The essay in full: `docs/essay.md`.
3. `docs/manifest.md` (every manifest key), `docs/build-spec-v0.8-round2.md` (the decisions behind the current design), `worlds/testnet.toml` and `worlds/edition1-example.toml` (the world about to launch, and its charter).
4. The code, all of it: `factorylab/`, `tests/`, `scripts/`, `deploy/`, `worlds/`. Run `uv run pytest` and the scripted worlds.

Not required: `docs/history/`, `docs/audits/` and `docs/build-log.md`. They are records of how main got here. A seat may consult them but should not take their findings as its own.

## The seats

| Seat | Auditor | Aimed at | Report |
|---|---|---|---|
| 1 | Claude Fable | **Class 3.** Chapter I and II against the code as it stands. Where does an objective still come from the architect? What is still welded on? Specifically: roles as contracts, retirement, nested requests, population-written observations and connectors: are they real freedoms or paper ones? Is the jail plus registrable connectors the right primitive, or does the set of things the factory can know still belong to the architect? | `docs/audits/v3/class3-fable.md` |
| 2 | Codex high | **Pathologies and pressure.** Write down the fitness function the population actually faces from the reward line backwards. The four pathologies. Reward hacking and collusion including the new surfaces: connectors, observations, retirement, spot, the split verdict and payoff, the size-banded propensities. Does the pressure point where the norms point? | `docs/audits/v3/pathologies-codex.md` |
| 3 | Claude Fable | **Defects, reproduced.** Break it, with a failing test under `tests/audit/` per finding on a branch: the jail and its probe, key handling, the streaming ledger and its head file, resume at every ledger write with the new item kinds, the connector proxy on adversarial responses, observation code in the jail, nested requests at the depth cap, retirement with pending feedback, spot lots and class transfers, the treasury's Venice leg. | `docs/audits/v3/defects-fable.md` + branch `audit3/defects-fable` |
| 4 | Codex high | **Defects, reproduced**, on everything except the sandbox and key-handling code (`cortex/sandbox.py`, `kernel/reserve.py`, `world/x402.py`): money paths, lots and consequences, prices and penalties, the cadence and immune organ, governance and ballots, composition dispatch, the wake. Failing tests on branch `audit3/defects-codex`. | `docs/audits/v3/defects-codex.md` + branch |
| 5 | Claude Opus | **Wiring, by running.** Everything the population is told it can do, tried once, with receipts: the scripted worlds; a testnet world with fills on perps and on spot and a class transfer; a registration, a retirement vote, a nested request with a tool, an observation registered and priced, a connector registered by vote and fetched, a Venice completion from prepaid credit, an x402 seller for cents, a treasury move, kill and resume, the observatory wake. Spend cap $0.50 on-chain. | `docs/audits/v3/wiring-opus-a.md` + receipts |
| 6 | Claude Opus | The same checklist as seat 5, independently, without contact. Two runs of the same list is how we learn what is flaky rather than broken. | `docs/audits/v3/wiring-opus-b.md` + receipts |

## What every report contains

Findings first, ranked by severity: blocker, serious, minor. Each with file and line, the concrete failure (inputs and state, then the wrong outcome), a reproduction where one is possible, and a proposed fix. Three labels, never mixed in one finding: **broken** (the intended functionality is not there or fails), **not Class 3** (violates the essay), **unclean**. For essay findings, the passage (section, quote under 15 words). Then, in one paragraph: does it work, and is it the factory the essay describes? Under 3,000 words. Nothing applied by any seat except the defect seats' tests on their branches.

## Rules

Never read, print or copy any `*.key`. Never create a manifest named `funded`. Hyperliquid testnet only. The wiring seats may spend at most $0.50 of on-chain USDC each and may consume Venice's prepaid credit; never a Venice top-up; never a mainnet pot move. Read-only otherwise.

## Afterwards

One triage table, `docs/audits/v3/triage.md`; fixes behind the gate; the edition 1 re-draft; the first-move review; the droplet; `funded`.
