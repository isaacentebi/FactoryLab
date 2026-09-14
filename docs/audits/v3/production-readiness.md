# Correction: session-added economic caps removed

The experimenter rejected the economic policy introduced in this session. The order-size,
gross-exposure and slippage cap fields, their runtime enforcement, and the mainnet launch
requirement have been deleted, not made optional. The capped diagnostic command and its
cap tests have been removed. Exact used diagnostic manifests are retained only as inert
`.txt` evidence under `evidence/retired-execution-drills/`; they are not runnable profiles.
No new daily inference budget is required or implemented. Allocation is the entire
Hyperliquid account, remaining OpenRouter credit, and Venice for additional compute.

Duplicate-submission prevention, order identities, finite transport timeouts, fill
reconciliation, accounting and the actual ratified charter remain. The SDK's pre-existing
order behavior and the pre-existing factory rules were not replaced with new policy.
Removing the capped quote path also removes its separate pre-submit-quote classification;
ambiguous SDK failures continue to be recorded as uncertain without blind resubmission.

**Everything below is historical evidence from before this correction, not current policy
or a current test result for the uncapped tree.** Verification of the removal follows in
`caps-removal.md`.

---

# Production readiness checkpoint — September 13, 2026

Status: execution repairs and bounded testnet mechanics verified; no funded launch approval.

Latest result: **2,647 regression tests passed**, plus **30 process-recovery tests**.
The final live drill completed in **24.95 seconds**, bought and closed **0.11 SOL**, recorded
both fills, and ended flat with no SOL resting orders and zero model calls. Production
money limits, account isolation and actual host/deployment checks remain open.

## What was repaired

- Every direct order output and venue tool shares fresh, conservative notional admission checks.
  Gross exposure includes perpetual positions, spot holdings even without a registered spot
  market, resting orders, and unresolved submissions. Missing marks or failed fresh reads
  refuse new exposure. Venue-enforced reductions remain possible.
- Optional immutable manifest bounds preserve historical hashes when absent. New mainnet
  launches through `run_world` require explicit slippage, per-order and gross bounds plus
  a client namespace before runtime construction.
- Configured market orders fetch a fresh quote before entering submission. A failed quote
  is a definite pre-submission refusal; submission timeouts remain uncertain. Completed
  order identities reuse their result without a new quote or submission.
- The prior lifecycle repair retains uncertainty, performs one read-only terminal order/fill
  reconciliation, and reports execution acknowledgements separately from ingested fills.
- A testnet-only CLI execution drill exercises leverage acknowledgement, resting limit,
  cancellation, runtime size refusal, opening fill and reduce-only close without model calls.
  It requires a flat selected market and a single-use manifest namespace. Its step log is
  diagnostic evidence; it is not a resumable population experiment.
- Historical `docs/launch-decisions.md` readiness and balance claims are explicitly superseded.

## Decisions made for the diagnostic fixture

`worlds/execution-drill.toml` uses SOL, an approximately $12 testnet order, a $20
per-order cap, $150 gross account admission cap and 50 basis points of price tolerance.
These are testnet fixture choices, not authorization for mainnet allocations. Other-market
exposure consumes the cap. The selected market must start flat and without resting orders.
The drill requests 2x cross leverage and, on success after closing, requests 1x; it does not
infer the account's previous setting. Acknowledgement is distinct from an independent read
of venue leverage. No paid model calls, reserve transfers or funding operations are included.

The exposure limit is conservative admission accounting, not a guarantee against future
price movement, concurrent account users or liquidation. Production needs a dedicated
account. Shutdown of an ordinary world does not automatically close positions.

## Evidence that must not be overstated

The previous ten-minute target produced 7 ticks in roughly 11 minutes 20 seconds and
65 successful model calls, costing $0.214380 in reported inference. Two ETH orders had
confirmed fills; one BTC submission remained uncertain. The original summary missed final
fills because the last tick had no subsequent poll. Existing account positions prevent
attributing account P&L to this run. The new terminal reconciliation addresses the reporting
path; a passing fixture does not retrospectively change that run's raw summary.

The population used its ratified 13-card charter. Only six cards had measured support by
the third window; an activity penalty influenced trading rationale. That is evidence of
incentive response, not an improved strategy. Seven ticks cannot establish sustained
charter revision or profitable trading. An 82-second model loop is not HFT; a lower requested
tick interval does not make serial model calls complete faster.

## Remaining launch gates

- Experimenter-selected total trading allocation, inference budget and price/exposure bounds.
  No new daily inference cap has been implemented or verified by this repair.
- Dedicated account baseline and resolution of all outstanding order identities; historical
  testnet account state must not become unexplained production inventory.
- Reviewed charter provenance and an experiment duration sufficient to observe the promised
  governance behavior. A mechanical drill cannot satisfy this.
- Reviewed pinned commit and clean deployment checkout (this work is intentionally uncommitted).
- Actual Ubuntu host jail probe, credentials provisioned through approved CLI paths, wake,
  backup upload and isolated restore rehearsal, plus measured peak resources under load.
  The current ledger reader materializes the diary. No bounded-memory redesign is claimed;
  capacity must be verified for the declared lifetime before launch.

No funded manifest was created and no mainnet execution was authorized.

## Verification

The completed code gates and all three bounded live drill outcomes are recorded below.

### Completed verification so far

- Focused execution and lifecycle regressions: `25 passed in 1.32s`.
- Explicit process-recovery suite (excluded from the ordinary gate by default):
  `30 passed in 185.01s (0:03:05)`.
- Fresh read-only lookup: both historical ETH order identities remain filled; BTC remains
  uncertain with `order not observed`. Evidence: `/tmp/factorylab-production-order-status.json`.

Initial required gate before the first drill: 2,639 passed in 704.98 seconds.
Full original output: `/tmp/factorylab-production-full-gate.log`.

### Process-recovery gate, verbatim

Command: `uv run pytest -m slow -o addopts='' tests/runtime/test_resume.py`

```text
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
plugins: xdist-3.8.0, anyio-4.15.1
collected 30 items

tests/runtime/test_resume.py ..............................              [100%]

======================== 30 passed in 185.01s (0:03:05) ========================
```

### First live drill: refused before order submission

`/tmp/factorylab-production-execution-drill/report.json` records a failed drill, not a
successful lifecycle test. Elapsed time was 12.35 seconds. The venue acknowledged SOL 2x
leverage, then the first order was refused because existing exposure plus the proposed
order exceeded the $150 gross cap. No order intent was submitted. The failure path at
that revision did not reset leverage; the selected market was flat at baseline.

A subsequent read-only baseline measured about $141.74 gross: $65.32 perpetuals and
$76.42 spot, with no resting orders. Thus the old fixture had insufficient headroom for
its proposed order; existing exposure alone did not exceed $150. The fixture is sharing
an account and does not establish an isolated population P&L baseline.

The drill now records a read-only baseline, supports `--preflight-only` without a ledger
or used marker, and performs exposure preflight before leverage changes. Known effects
receive bounded cleanup attempts on failure. Risk accounting additionally rejects zero,
negative and nonfinite marks for existing holdings instead of allowing them to create
capacity. Updated focused regressions: `30 passed in 1.43s`.

The fresh `worlds/execution-drill-recheck.toml` has a new namespace and a $175 gross cap.
The $20 per-order cap, approximately $12 order, and 50 bps price bound are unchanged.
This is a testnet diagnostic adjustment for the measured pre-existing holdings. It does
not raise or authorize any mainnet allocation, and the runtime still rechecks fresh risk
before each order. The final required gate below passed after these changes.

Required gate after exposure preflight repair: 2,644 passed in 699.92 seconds.
Full original output: `/tmp/factorylab-production-final-full-gate.log`.

### Second live drill: venue minimum caught a fixture rounding error

The $175-cap retry passed risk preflight, but the discounted passive price multiplied by
lot-rounded size was slightly below the venue's $10 minimum. The venue rejected it and
cleanup acknowledged a return to SOL 1x leverage. No order filled. Evidence is retained in
`/tmp/factorylab-production-execution-recheck/report.json` (14.68 seconds).

The fixture now computes its passive price at 98% of the baseline mid and verifies the
rounded size times that price meets the minimum before any leverage change. Regression
coverage includes both $100 and the observed $100.675 quote. Focused tests: 33 passed in
1.32 seconds. The order adapter and its risk limits were unchanged by this fixture repair.

### Final live drill: passed

`worlds/execution-drill-final.toml` is a fresh single-use testnet fixture. Manifest hash:
`2dcd7ab1889b4fff46a2a1be48e7cd1eea6e25282d40f59bef8f816567a092fb`.

Elapsed wall time: **24.95 seconds**. Paid model calls: **0**.

| Step | Observed result |
| --- | --- |
| Fresh exposure preflight | Passed |
| SOL cross-leverage request | 2x acknowledged |
| Passive limit | Rested, order 60069507496 |
| Cancel | Cancelled, same order 60069507496 |
| Oversized market order | Refused by runtime before submission |
| Market open | Filled 0.11 SOL at 100.65; order 60069513073 |
| Reduce-only close | Filled 0.11 SOL at 100.59; order 60069515085 |
| SOL leverage reset | 1x acknowledged |
| Fresh ending account read | SOL flat, no SOL resting orders |

This demonstrates a roughly $11.07 testnet round trip and the complete mechanical path.
It is not an isolated account P&L result or a population experiment. Other existing account
holdings were not closed. The earlier BTC identity remains unresolved in its original world.

Full receipt: `/tmp/factorylab-production-execution-final/report.json`.
Summary: `/tmp/factorylab-production-execution-final/summary.json`.

Verified final summary fields:

```json
{
  "execution": {
    "intents": 4,
    "statuses": {
      "filled": 2,
      "resting": 1,
      "cancelled": 1,
      "rejected": 0,
      "uncertain": 0
    },
    "polled_fills": 2,
    "terminal_reconciliation": {
      "attempted": true,
      "fill_read_error": null
    },
    "positions_closed_at_exit": false
  },
  "wallet_conservation": true,
  "ledger_verify": true
}
```

The execution counts are per-intent acknowledgements: the original limit intent rested,
and a separate cancellation intent cancelled it. They are not a count of current open
orders. `positions_closed_at_exit: false` means the runtime's shutdown phase did not
liquidate positions; this drill explicitly closed its SOL position before shutdown, as
the fresh ending account read confirms.

All three diagnostic worlds are terminated with `explicit_kill:budget`, released seals,
valid ledgers, wallet conservation, and zero model invocations. The two refused drills
recorded zero fills; the successful drill recorded two. No diagnostic world was left running.

## Working-tree handoff

No commits were made. This list includes the earlier short-experiment and lifecycle
repairs already present in this workstream, as well as the readiness changes above.

- `README.md` (modified).
- `docs/launch-decisions.md` (modified).
- `docs/manifest.md` (modified).
- `factorylab/cortex/assembly.py` (modified).
- `factorylab/cortex/schematics.py` (modified).
- `factorylab/runtime/cli.py` (modified).
- `factorylab/runtime/compute.py` (modified).
- `factorylab/runtime/governance.py` (modified).
- `factorylab/runtime/live.py` (modified).
- `factorylab/runtime/loop.py` (modified).
- `factorylab/runtime/propensity.py` (modified).
- `factorylab/runtime/resume.py` (modified).
- `factorylab/runtime/summary.py` (modified).
- `factorylab/runtime/venue.py` (modified).
- `factorylab/runtime/worlds.py` (modified).
- `factorylab/settlement/consequence.py` (modified).
- `factorylab/world/exchange.py` (modified).
- `factorylab/world/models.py` (modified).
- `factorylab/world/openrouter.py` (modified).
- `scripts/draft_edition1.py` (modified).
- `docs/audits/v3/order-lifecycle-repair.md` (new).
- `docs/audits/v3/production-readiness.md` (new).
- `docs/audits/v3/short-experiments.md` (new).
- `docs/charter/edition1-short-draft.md` (new).
- `docs/charter/edition1-short-ratification-v2.json` (new).
- `docs/charter/edition1-short-ratification.json` (new).
- `docs/charter/edition1-short-ratified.toml` (new).
- `docs/charter/edition1-short.toml` (new).
- `factorylab/runtime/execution_drill.py` (new).
- `scripts/calibrate_rehearsal.py` (new).
- `scripts/ratify_charter.py` (new).
- `scripts/rehearsal.py` (new).
- `tests/audit/test_accelerated_repair.py` (new).
- `tests/audit/test_execution_limits.py` (new).
- `tests/audit/test_order_lifecycle_repair.py` (new).
- `worlds/execution-drill-final.toml` (new).
- `worlds/execution-drill-final.used` (new).
- `worlds/execution-drill-recheck.toml` (new).
- `worlds/execution-drill-recheck.used` (new).
- `worlds/execution-drill.toml` (new).
- `worlds/execution-drill.used` (new).
- `worlds/testnet-10m-roster.toml` (new).
- `worlds/testnet-10m.toml` (new).
- `worlds/testnet-10m.used` (new).

Base commit before these uncommitted changes: `31ca950964b36b0555c32b0d5b4515fe225fa382`. This is not a reviewed deployment commit.

## Final required gate after all changes, verbatim

Command: `uv run ruff check . && uv run pytest`

```text
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 14/14 workers
14 workers [2647 items]

........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 10%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 19%]
........................................................................ [ 21%]
........................................................................ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 40%]
........................................................................ [ 43%]
........................................................................ [ 46%]
........................................................................ [ 48%]
........................................................................ [ 51%]
........................................................................ [ 54%]
........................................................................ [ 57%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 65%]
........................................................................ [ 68%]
........................................................................ [ 70%]
........................................................................ [ 73%]
........................................................................ [ 76%]
........................................................................ [ 78%]
........................................................................ [ 81%]
........................................................................ [ 84%]
........................................................................ [ 87%]
........................................................................ [ 89%]
........................................................................ [ 92%]
........................................................................ [ 95%]
........................................................................ [ 97%]
.......................................................                  [100%]
======================= 2647 passed in 708.99s (0:11:48) =======================
```
