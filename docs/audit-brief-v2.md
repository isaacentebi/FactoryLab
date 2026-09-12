# Cold audit, round two: the full edit of main

Round one (`docs/audit-brief.md`, five reports in `docs/audits/`) found 30-odd issues; three fix passes closed every reproduced one. Round two audits the fixed main at commit `edd6efc` from seven seats, each pointed at a different part of the factory, with no contact between seats. This is the last strict gate before the first move.

Every seat reads the essay in full first (`docs/essay.md` in the worktree), then edition 1 of the charter (`worlds/edition1-example.toml`, `docs/charter/edition1-draft.md`), then the specs and the build log. An audit that has not read the essay cannot do this job. All seats are read-only against the audited commit: findings, reproductions and proposed fixes, never applied fixes. Fixes come afterwards, in a separate cycle, so every auditor sees the same code.

## The seats

| Seat | Auditor | Aimed at | Deliverable |
|---|---|---|---|
| 1 | Codex `gpt-6-astra`, xhigh | **Is this a Class 3 factory?** Chapter I and II of the essay against the code. Where does an objective still come from the architect? Where is the architect still inside the loop after launch? What demotes it to Class 2 the moment it is switched on? What is missing for superdark rather than automated? | `docs/audits/v2/class3-codex.md` |
| 2 | Codex `gpt-6-astra`, xhigh | **The four pathologies and the pressure.** Stable failure, overfitting, learning death, thrash: which reward, price, timing or disclosure incentivises each; the first sign in the ledger; whether versioning would see it. Then reward hacking and collusion beyond `settlement/REWARD_HACKING.md`. Then: what is the evolutionary pressure of this factory, and does it point where the norms point? | `docs/audits/v2/pathologies-codex.md` |
| 3 | Codex `gpt-6-astra`, high | **The first move.** Everything the architect commits once: seed prompts, the lineup and model selection, the seed cards, edition 1 of the charter, the prices, the tick, the pots, the norms. Is any of it an objective in disguise? Is any of it a rule dressed as a schematic? Would a different seed population reach a different factory, and is that fine? Does the seed have the autonomy the essay requires (can it change the tick, the cards, the models, the market)? | `docs/audits/v2/first-move-codex.md` |
| 4 | Claude Fable | **Defects, reproduced.** Kernel, settlement, learners, charter, runtime: bugs, races, money paths that lose or double-count a micro-dollar, the seal and hash chain, key handling, resume, the tool jail, the x402 signing path, the reconciler. Every finding comes with a failing test under `tests/audit/` on a branch. | `docs/audits/v2/defects-fable.md` + branch `audit/defects-fable` |
| 5 | Claude Fable | **Does it actually work, end to end?** Not by reading: by running. The scripted worlds; a testnet world with real fills; the population registering a new assembly; a producer writing a Python file and running an algorithm inside the jail; catalogue search; a Venice completion paid from the factory's own credit; an x402 seller quoted and paid for cents; treasury pots; a kill and resume; the wake page; the deploy scripts read against a fresh Linux box. Everything the population is told it can do, tried once. | `docs/audits/v2/wiring-fable.md` + `docs/runs/audit-wiring.md` receipts |
| 6 | Claude Opus | **Environment, primitives and contracts.** Chapter II: are the primitives the essay names (request line, reward line, propensity, contracts, versioning by behaviour, evaluations graded by consequence, sortition, cascade, the market) realised as the essay defines them, or renamed approximations? Is the environment corrupted anywhere (a leak from the architect's world into the population's, a hidden channel, a disclosure that is more or less than minimally sufficient)? Every contract the population sees: thin enough to compose, rich enough to act on? | `docs/audits/v2/environment-opus.md` |
| 7 | Claude Opus | **The repository as a product.** Dead code, dead config, dead docs; self-referential README; names that describe history instead of function; duplication; module boundaries; what a stranger needs to run it in ten minutes. A concrete, ordered list of changes, none applied. This seat may skip the specs but not the essay: it must know what the code is for. | `docs/audits/v2/polish-opus.md` |

Seats 1–3 are Codex because those questions are argument, not sandbox internals. Codex has been blocked twice by its provider's classifier when reading the sandbox and key-handling code, so the defect seat and the wiring seat are Claude; the Codex prompts steer away from `cortex/sandbox.py`, `kernel/reserve.py` and `world/x402.py` except as the essay's questions require.

## Required reading, in this order

1. The essay in full: `docs/essay.md`.
2. Edition 1: `worlds/edition1-example.toml`, `docs/charter/edition1-draft.md`, `worlds/testnet.toml`.
3. `docs/design-audit-v2.md`, the specs `docs/build-spec-v0.4.md` through `v0.7-phase4.md`, `docs/build-log.md`, `docs/handoff.md`, and round one's reports in `docs/audits/` (so nothing is re-found; anything re-found is a finding against the fix).
4. The code, all of it: `factorylab/`, `tests/`, `scripts/`, `deploy/`, `worlds/`. Run `uv run pytest` and the scripted worlds. Read a dead world's diary if a key is present.

## What every report contains

Findings ranked by severity, each with: file and line; the essay passage it answers to (section, quote under 15 words); the concrete failure scenario; and a proposed fix, or the statement that no fix is possible without demoting the factory. Three labels, never mixed in one finding: **not Class 3**, **will break**, **unclean**. Round-one findings that are not fixed on main are reported as such. Under 3,000 words per report; seat 5 adds receipts.

## Out of scope

Style and formatting (seat 7 excepted), performance unless it threatens liveness, and whatever the essay leaves open (note it as open). No fixes applied by any seat.

## Afterwards

All seven reports are consolidated into one triage table (`docs/audits/v2/triage.md`) by the session model, with the experimenter. Fix passes follow round one's pattern: defects first, fidelity second, polish last, each behind the everyday gate and the slow resume tests, each merged to main before the next. Then the first-move review, then the operational checks, then `funded`.
