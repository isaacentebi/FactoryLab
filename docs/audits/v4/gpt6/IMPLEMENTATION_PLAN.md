# Factory Lab vNext: implementation plan and funding contract

**Base commit:** `3a27fa4cefd2511c13c7a7d779a913249606e0bc` on `isaacentebi/FactoryLab/main`.
**Deliverable:** design and ordered implementation work, not an implemented release.
**Recommended experiment:** a new factory identity, $2,000 operating research envelope, eight weeks of prepaid observation, $1,200 of finite inference backing.
**Proposed physics version:** 2. **Initial charter edition:** 1.

## 1. Decision to implement

Build a factory with persistent executable capabilities, backed local resource responsibility, a finite learning endowment, and at least one authorized productive opportunity beyond trading.

Do not increase the current production endowment and assume that longer life implements this design. The existing nine-seat world is a useful foundation, but adding project responsibility, meaningful memory, non-model executors and independent effect enforcement changes the constitutional substrate.

The first funding change is **$300 initially unlocked and $900 subsequently unlocked on a schedule committed before genesis**, not an unlimited weekly refill. The full $1,200 must already have verified backing. The population sees which resources are spendable and which are locked; it cannot spend a calendar promise.

This new experiment explicitly replaces the current no-refill/no-new-budget preparation covenant. It does not amend a living factory. The old ratified charter is retained unchanged as historical evidence. [S03, S04, S47]

### Resource envelope

| Allocation | USD |
|---|---:|
| Finite live-population inference backing | 1,200 |
| Separate prelaunch calibration and small baselines | 200 |
| Trading / paid-work working capital | 200 |
| Experiment-scale infrastructure and backup allowance | 150 |
| External data, payments, bridge and gas allowance | 100 |
| Outside contingency, not a same-world rescue fund | 150 |
| **Operating research envelope** | **2,000** |

This is a decision budget, not an estimate of what emergence costs. Engineering labor, coding-agent implementation consumption and a broader production security review are excluded. The infrastructure line is a planning allowance, not a promise that a complete authority deployment has been priced.

Release $300 at launch; $150 on days 7, 14, 21 and 28; $100 on days 35, 42 and 49. Unspent unlocked backing carries forward. There is no automatic further contribution on day 56.

See [funding analysis](FUNDING_AND_COSTS.md) and [machine-readable funding proposal](FUNDING.json).

## 2. Ground truth in the current code

The code already provides important building blocks:
- ledger-first state changes, decision handles, receipt-aware external I/O and delayed feedback;
- registered accepts/emits contracts, population tools and observations, custom forecast predicates;
- an OS jail for population Python;
- registry versions, historical attribution, novelty protection, committee procedures;
- treasury conversion, provider selection and crash recovery surfaces.

Retain those. Do not introduce an unrelated agent orchestration framework or a second financial ledger that silently competes with the existing one.

The seams requiring deliberate changes are specific:

| Current seam | Consequence for the implementation |
|---|---|
| `AssemblySpec`, `AssemblySeed`, `_instantiate`, `_is_feasible` assume a model-backed assembly | A persistent program executor must be wired through instantiation, feasibility, child dispatch, schemas, retirement and recovery, not merely added as a tool. |
| `Meter.run` receives one `WalletLike`; local notes and external model calls debit that root | Separate real invoices from backed internal resource transfers through a compatible runtime facade. |
| `_manage_reserve_window` seeds novelty from `self.wallet.balance` | Locked endowment must not inflate active exploration entitlement or constrain ordinary work through an ineligible protected balance. |
| `Wallet.drip` adds book money on a schedule | Do not reuse it as a live provider-credit purchase or a proof of endowment funding. |
| `Runtime._check_termination` treats repeated unaffordability as terminal | A new, explicitly defined budget-dormant state must precede terminal death when legitimate future availability exists. |
| `NotesSpec` charges bytes per measurement window | Use elapsed storage exposure and economic classification; no synthetic financial burn tied to governance frequency. |
| `_policy_prediction` requires an existing measurable card | A new challenge route is required when the current measuring instrument is itself disputed. |
| `LotTable.fill` can credit opener and closer with the same P&L for local learning | Retain diagnostic credit only as such; financial distributions and project surplus need a separately conserved account. |
| `BootstrapMixin.live` follows the exchange kind | Independent paid work and paid inference need effect permissions separate from whether the trading exchange is fake. |
| `runtime_state` / `restore_runtime` and codec types enumerate fields explicitly | Every new budget, state, clock, artifact and project type needs explicit durable serialization and replay semantics. |
| `roster_hash` binds assembly/model configurations, not the complete offered world | New ratification must bind the funding, primitive and settlement context without rewriting legacy digests. |
| Mainnet is admitted only for a world named `funded`; initial charter edition must be 1 | Keep both current restrictions intact during development. A new physics version is not a charter starting at edition 2. |

Sources: [S05–S25, S31–S38](SOURCE_MAP.md). Some anchors use previously retrieved source at the identical pinned commit. See the source map's inspection labels.

### Two concrete budget facts

The repository's saved short paid probe records $0.028031 for 13 invocations over 119.10 seconds, with fake exchange/treasury and no independent provider billing reconciliation. It is useful starting evidence, not a long-run burn estimate. [S39, S40]

The current two-minute rehearsal window and one micro-dollar per byte-window notebook rent imply $47.18592/day of internal charges for a 64 KiB retained footprint at that cadence. This is not an actual storage invoice. Moving to a larger endowment before correcting this accounting interpretation can make a synthetic charge dominate the experiment. [S04, S09, S10, S11]

## 3. Non-negotiable implementation boundaries

1. No production effect, paid probe, provider top-up, mainnet read/write or deployment occurs as part of this plan. Later paid calibration requires a separate explicit budget and authorization.
2. No credential file is opened, printed or copied. No manifest named `funded` is generated.
3. Existing v1 worlds retain their original identity and semantics. New physics creates a fresh world; historical score meanings do not change in place.
4. Source packages preserve dependency boundaries. In particular, `world` can import `kernel.money`, not arbitrary kernel budget/project implementations. Runtime composes those through protocols.
5. All financial quantities use integer micro-USD or exact instrument units. Internal transfer, external expense, earned receipts, subsidy and synthetic scarcity prices remain distinct.
6. Population code stays in the existing jail. Typed output requests effects; it does not receive host credentials or unrestricted network access.
7. Judging authority cannot acquire trading/payment authority by creating a producer-shaped child.
8. A restore cannot invent money, revoke prior bills, replay paid effects as new ones, change code versions for recorded calls, or revive a terminal identity.
9. No unsupported measurement becomes zero; no acceptance becomes payment; no internal transfer becomes revenue.
10. Claims of independent survival exclude replenishment from unused founder subsidy and related-party demand.

## 4. Architecture contract before coding

Read [ARCHITECTURE_CONTRACTS.md](ARCHITECTURE_CONTRACTS.md) first. It defines:
- four externally supplied norms and a population-authored proxy layer;
- financial backing, local entitlements and evaluative judgments;
- endowment releases, banking, dormancy and death;
- projects, sponsorship, delegation and funded reproduction;
- persistent private/project/public state and content-addressed artifacts;
- model/program executor variants and state transactions;
- mandatory maintenance separated from optional paid cognition;
- explicit economic clocks and constitutional timing;
- independent paid work, acceptance and receipt-based income;
- conserved project consequences and independent evaluation;
- a process for challenging an inadequate metric;
- immutable substrate identity and an external authorization witness.

Freeze these interfaces in V01. Do not allow parallel implementers to invent competing `BudgetBook`, `ProjectOutcome`, `ExecutorSpec` or `DueSpec` definitions.

### Important intentional restraint

One shared cross-margined trading account initially belongs to one trading project. Internal project labels do not provide venue-side financial isolation. Other projects can contract with that project or operate on independently authorized work.

A known-feasible productive opportunity is an option, not a mandatory workflow. A scripted customer proves the payment plumbing; it does not establish independent demand.

An activity card may be proposed by the population later under the same constitutional process. Removing the architect's seeded activity quotas is not a hidden permanent veto on those metrics.

## 5. Work order index

Each work order specifies existing files, proposed new files, entry points, changes, tests, dependency order and its acceptance contract. Test names are proposals, not completed verification.

| Work | Priority / size | Depends on | Deliverable |
|---|---|---|---|
| [V00](work-orders/V00.md) | P1 / small |  | Freeze baseline and establish non-spending verification |
| [V01](work-orders/V01.md) | P1 / cross-cutting | V00 | Commit versioned contracts and new-world identity |
| [V02](work-orders/V02.md) | P2 / medium | V01 | Correct measurement semantics without rewriting history |
| [V03](work-orders/V03.md) | P1 / cross-cutting | V01 | Separate real money from backed internal budgets |
| [V04](work-orders/V04.md) | P1 / cross-cutting | V03 | Implement finite releases, banking and budget dormancy |
| [V05](work-orders/V05.md) | P1 / large | V03, V04 | Give projects budgets and make reproduction funded |
| [V06](work-orders/V06.md) | P2 / large | V03, V05 | Add durable local state and a reusable artifact archive |
| [V07](work-orders/V07.md) | P2 / large | V05, V06 | Make programs first-class executors beside models |
| [V08](work-orders/V08.md) | P1 / cross-cutting | V04, V07 | Separate economic clocks from message traffic and paid cognition |
| [V09](work-orders/V09.md) | P1 / large | V03, V05, V06, V08 | Add a paid-work world adapter with receipt-based income |
| [V10](work-orders/V10.md) | P1 / large | V02, V05, V09 | Make project success and distributions economically conserved |
| [V11](work-orders/V11.md) | P2 / large | V02, V06, V09, V10 | Create the new constitution and a measurement-challenge route |
| [V12](work-orders/V12.md) | P2 / medium | V08, V10, V11 | Align evaluation and immune response with useful evidence |
| [V13](work-orders/V13.md) | P2 / medium | V07, V08, V09, V12 | Calibrate capability, context and full decision-tree cost |
| [V14](work-orders/V14.md) | P1 / medium | V00, V01 | Repair compute-renewal evidence at the payment boundary |
| [V15](work-orders/V15.md) | P1 / cross-cutting | V01, V03, V04, V14 | Enforce release identity and spending outside population execution |
| [V16](work-orders/V16.md) | P2 / medium | V10, V11, V12, V13, V15 | Publish economics and run discriminating experiments |
| [V17](work-orders/V17.md) | P1 / cross-cutting | all prior work | Ratify and release one bounded vertical slice |

## 6. Integration order and parallel ownership

### Critical sequence

`V00 -> V01 -> V03 -> V04 -> V05 -> V06 -> V07 -> V08 -> V09 -> V10 -> V11 -> V12 -> V13 -> V16 -> V17`

Parallel branches after interface freeze:
- `V01 -> V02` can develop new scoring/measurement semantics and local tests before the project-settlement integration in V10.
- `V01 -> V14` can reproduce and correct compute-renewal evidence independently of persistent executors.
- V15 starts after V03/V04/V14 and merges the authority protocol into the resource path.
- Artifact and pure executor code can be developed against the frozen types in parallel; their runtime integration still follows V06/V07.
- Public projection and experiment-report schemas can be designed early, but V16 cannot claim economic correctness before its dependencies settle it.

The dependency graph expresses gates, not a promise that every work item has identical effort.

### Shared-file rule

The most conflict-prone files are `runtime/worlds.py`, `runtime/bootstrap.py`, `runtime/resume.py`, `runtime/loop.py`, `runtime/compute.py`, `runtime/governance.py`, `cortex/schematics.py`, and the schema/roster code.

Assign one integration owner for each shared file. Other workers submit narrow additions against frozen interfaces, along with tests. Do not concurrently rewrite mixin dispatch or checkpoint layouts in separate branches.

Suggested development partitions:
- resource/lifecycle owner: V03–V05 and integration with V15;
- pure executor/archive owner: V06–V08;
- measurement/institution owner: V02, V10–V12;
- external-adapter owner: V09, V14, authority transport;
- integration/evidence owner: V00/V01, shared codecs and V13/V16/V17.

This is a development plan for the human/coding agents building the substrate, not a fixed organization imposed on the living factory.

### What can be quick, and what cannot be shortcut

V02 and V14 are localized enough to isolate with reproductions and control cases. Budgeted releases require new lifecycle semantics. Model-to-program execution and project-level resource ownership are larger cross-cutting changes. External authority is also a real boundary change.

The route to faster implementation is frozen contracts, narrow work ownership, explicit regression obligations and a complete vertical slice. It is not parallel wholesale edits to a shared runtime.

## 7. Milestone gates

### Gate A: stable development base (V00/V01)
A pinned, non-spending test environment exists. Legacy hashes and fixtures are preserved. Proposed v2 types and code owners are agreed. No new financial authority exists yet.

### Gate B: truthful resources and continuity (V02–V05, V14/V15)
Unlocking, allocation, internal rents, external invoices and project delegation conserve the right quantities. Dormancy and terminal death are separate. Renewal evidence is either authoritative or explicitly blocked. A fresh authorization service enforces production effects and rejects stale identity state.

**Do not place a larger live endowment behind the system before this gate.** Scripted implementation and separately authorized development calibration can proceed earlier.

### Gate C: retained productive capability (V06–V10)
A model can produce a bounded program, the program can reuse state/artifacts, a project can consume an opportunity, and unique receipts finance subsequent work. The test proves the real runtime path with scripted external adapters.

One successful transformed data artifact is more useful for this gate than a synthetic flood of agent registrations.

### Gate D: revisable standards, affordable cognition (V11–V13)
A population can challenge a faulty metric without the faulty metric automatically vetoing the challenge. Meaningful decisions survive settlement delay. Calibration measures total decision-tree cost and tests stronger reasoning where it contributes.

### Gate E: honest experimental launch readiness (V16/V17)
The actual roster ratifies a charter against the actual new funding and capability context. Public evidence distinguishes subsidy from independent earnings. Crash and replay cases have been executed with exact output recorded. Real counterparties and deployment prerequisites, when relevant, are verified separately.

No gate automatically starts a production service or creates a funded manifest.

## 8. Minimum complete scripted vertical slice

The first complete test story is intentionally small:

1. An external scripted work venue offers several optional bounded file/data tasks.
2. A sponsor funds one project from unlocked endowment rights.
3. A model-backed assembly creates a normalization program and a versioned artifact.
4. A registered program executor handles later compatible inputs without another LLM call.
5. A distinct evaluator judges the finished output using the committed input/output contract, not the producer's private trace.
6. The recipient accepts a precise artifact version. That alone credits no money.
7. A unique payment receipt arrives; finances increase once, and the project's full external expense is subtracted before any surplus distribution.
8. The original author retires. The useful artifact remains retrievable and usable by an authorized descendant.
9. A proposed metric challenge tests whether an incumbent cost-per-answer proxy misses repeated-use value. Both measurement definitions remain frozen through the test.
10. A budget-dormant period spans a scheduled release. No model is paid merely to poll for the grant; release happens once at the original launch-relative time.
11. A crash is inserted before and after each new durable/effect transition. Resume preserves the same resource positions, code/state versions, outcome commitments and authorization IDs.
12. Terminal kill revokes new work. Restoration of an earlier snapshot cannot purchase another effect under the killed identity.

Scripted choices can deliberately exercise every path. This is plumbing evidence, not evidence that unscripted agents chose the worthwhile work or revised their objectives autonomously.

## 9. Verification commands and artifacts

These are commands for a prepared implementation checkout after the corresponding tests exist. They were **not executed during this planning pass**.

- Existing broad static/default gate: `uv run ruff check . && uv run pytest`.
- New offline-focused gate: `uv run pytest tests/audit/vnext -m "not network and not slow" -o addopts="" -n 0`.
- Explicit slow recovery gate, only after its files are implemented: `uv run pytest tests/audit/vnext -m slow -o addopts="" -n 0`.
- Re-run the current relevant package-boundary tests and existing runtime/recovery regressions.

The repository default pytest configuration excludes `network` and `slow`; a green default run is not evidence the slow interruption suite ran. The proposed vNext fixture must enforce network/effect denial rather than merely trusting markers.

For each completed work item retain:
- base and resulting commit or exact patch identity;
- files changed and test names implemented;
- commands and verbatim output;
- dependency and jail state;
- any price/receipt evidence with its date and provenance;
- any unresolved failure, deviation or unavailable external prerequisite.

Never attach credential bytes or private live-factory traces. Source implementation is future work; this packet contains planning documents and structured data only.

## 10. Fresh ratification and historical compatibility

Do not set a new world's initial `charter.edition` to 2: the current loader requires 1. Add a separate physics/version discriminator.

Do not alter legacy source hashes by silently adding default-valued fields to canonical v1 serialization. Historical snapshots, manifests and baseline score definitions remain v1. If supporting v1 and v2 in one checkout becomes too invasive, retain a frozen v1 release reader and introduce an explicit v2 format; do not pretend changing definitions is backward-compatible.

Extend provenance with a new context digest containing offered resource types, schedule, executor contracts, settlement definitions and permissions. Preserve `roster_hash`'s historical semantics.

The final v2 charter vote must happen after these inputs exist. It cannot be borrowed from the old thirteen-card adoption or fabricated by a deterministic test.

## 11. Research protocol and economic claims

A fixed endowment buys an experiment in adaptation. It does not certify a business.

Report external earnings, founder contributions, related-party demand, internal conversions, actual invoices, liabilities and infrastructure subsidy separately. Include remaining unspent backing at the study end.

Compare a small number of independently identified runs at matched spend:
- a capable fixed-objective baseline;
- the vNext population;
- selected ablations, such as no persistent program/archive or common-pool budgets.

The $200 calibration/baseline line covers small developmental comparisons, not a statistically powerful multi-arm eight-week study. A larger research allocation should buy replication and better evidence before it buys one much larger population.

No “live longer” aggregate substitutes for explanations of how the population obtained its resources. Principal conversion and unused grants are not independent earnings. A good death can be informative, and a profitable episode can still be institutional overfitting.

## 12. Open prerequisites, not hidden implementation assumptions

1. Real paid-work demand: select an authorized buyer/interface, acceptance contract, and actual payment/refund evidence. No adapter implementation can manufacture independent demand.
2. Venice receipt semantics: verify what the provider can prove. If it exposes no adequate purchase-credit evidence, retain uncertainty and block the claimed renewal guarantee; do not invent an API.
3. Authority witness: choose an independent persistence/trust arrangement and test stale-state handling. Same-host backup and an all-powerful administrator are not solved by a hash.
4. Model and cost selection: verify the actual served configurations and invoices in metered development tests. The dated manifest prices and short historical probe are not sufficient.
5. Infrastructure and support horizon: measure archive growth, CPU/memory needs and the cost of a bounded wind-down. Prepaid external hosting remains a material dependency.

Everything else in the core design has a proposed default or interface in the contracts/work orders. A prerequisite can block production use without blocking scripted implementation.

## 13. How to use this packet

Start with this plan and `ARCHITECTURE_CONTRACTS.md`.
Read `AGENT_HANDOFF.md` before editing.
Execute one acceptance contract at a time from `work-orders/`.
Use `WORK_ITEMS.json` as the machine-readable dependency/work inventory.
Use `ACCEPTANCE_MATRIX.md` to track planned versus executed verification.
Use `SOURCE_MAP.md` to reopen exact source locations at the pinned baseline.

All work orders are currently **planned**. This document narrows implementation decisions; it does not eliminate the need to read complete target functions, test the integration or obtain approval for external effects.
