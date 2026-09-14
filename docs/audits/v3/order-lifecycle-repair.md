# Order lifecycle repair after the short rehearsal

The buying rail executed two ETH orders, but the prior run did not establish a reliable
end-to-end lifecycle. The BTC request was uncertain after a submission timeout, and the
summary counted no fill events despite two filled acknowledgements. This patch addresses
those execution, reporting and action-identity failures. It does not claim that ten-second
live ticks, profitable behavior, or account flattening have been demonstrated.

## Implemented changes

- The Hyperliquid trading SDK now receives the same finite timeout as its read client.
  A submission timeout remains uncertain; neither normal recovery nor final reconciliation
  resubmits its identity.
- A budgeted run performs one final reconciliation phase before releasing the seal:
  lookup each uncertain identity once, ingest unseen fills through the existing inclusive
  cursor, and terminate. There are no new model calls, orders, cancellations or closes.
  SDK read retries remain bounded. This can extend wall-clock duration beyond the trading
  deadline; it is a final receipt pass, not another trading tick.
- A durable `runtime.finish_budget` marker makes interruption within that phase replayable.
  Regression tests interrupt after the marker, cursor update, fill accounting, and final
  reconciliation report, then verify exactly one accounted fill after recovery.
- An exhausted fill read raises instead of silently returning an empty list. Routine
  polling retains its cursor; terminal reporting includes the safe failure class.
- The summary adds `execution`: intent count, terminal status counts, polled fill count,
  terminal reconciliation outcome, and an explicit indication that shutdown does not
  close positions. An acknowledged fill and an ingested execution are different evidence.
- `order-status --world ORIGINAL --client-id ID` queries original namespace-bound identities
  without launching a world or submitting an order. It does not mutate a dead ledger.
- Published order-label casing and numerical judgement bins are canonicalized consistently
  for declared probabilities and newly registered learners. Probabilities mapping into the
  same published bin are summed; genuinely custom action identifiers remain exact.
  Duplicate learner arms after canonicalization are refused before registration.
- An action label supplied as executable `action` now receives explicit feedback instead
  of silently doing nothing. It is not converted into an invented order size. An invalid
  order side can no longer silently become a sell.
- The live runtime no longer queries fills twice per tick: the existing inclusive cursor
  owns fill ingestion. Per-process exchange/provider operation counts and elapsed times
  are now in live summaries to identify remaining latency. They do not influence decisions
  and are not historical metrics restored from another process.

## Current external evidence

A read-only query on 2026-09-13, after implementation, returned:

| Original identity | Current venue answer |
|---|---|
| decision-172 | uncertain; order not observed |
| decision-173 | filled, 0.005 ETH, venue order 60062288236 |
| decision-175 | filled, 0.005 ETH, venue order 60062303272 |

The query confirms execution independently of the old diary acknowledgements. It does
not prove the BTC submission never executed. Its current uncertainty is retained.
No new paid model experiment or new trade was needed for this read-only check.
Evidence: `/tmp/factorylab-order-status.json` and `/tmp/factorylab-order-status.err`.

## What remains a measurement question

The previous experiment achieved seven ticks with roughly 82-second average spacing.
Removing duplicate reads is a concrete optimization; it does not establish a new achieved
rate. The next authorized live experiment should use the new IO timings and report actual
tick count, achieved interval and time spent reconciling. A 10-second configured interval
is not an acceptance result. Serial model cascades can still exceed it.

The population's inactivity penalty elicited trades whose stated rationale included avoiding
that penalty. This is evidence to submit to population charter review, not permission for
the architect to rewrite its voted objective. This patch preserves the ratified charter.
Existing testnet positions and the unresolved BTC identity remain external account state;
software termination does not cancel orders or liquidate positions.

## Validation

The focused regressions cover last-cascade fills, submission timeout without resubmission,
failed final fill reads, executable action validation, canonical probability bins, duplicate
poll removal, bounded SDK timeout, read-only status lookup, and interrupted reconciliation.
The full repository gate and final focused output are recorded below when complete.

No commit, mainnet action, or live-world source intervention was made. Existing changes
from the earlier charter/short-experiment work remain in the working tree.


## Files changed in this repair turn

- `README.md`
- `factorylab/runtime/cli.py`
- `factorylab/runtime/governance.py`
- `factorylab/runtime/live.py`
- `factorylab/runtime/loop.py`
- `factorylab/runtime/propensity.py`
- `factorylab/runtime/resume.py`
- `factorylab/runtime/summary.py`
- `factorylab/runtime/venue.py`
- `factorylab/settlement/consequence.py`
- `factorylab/world/exchange.py`
- `tests/audit/test_order_lifecycle_repair.py`
- `docs/audits/v3/order-lifecycle-repair.md`

Decisions not prescribed in the request: use one final read pass rather than indefinite
polling; retain unresolved identities as uncertain; keep final account reconciliation
separate from new model decisions; preserve the voted charter and the earlier run;
normalize published probability bins without inventing executable order arguments;
add latency instrumentation before claiming a faster achieved tick rate.

## Launch assessment and the Class 3 question

Current recommendation: do not make the irreversible unattended launch yet. The checks
here establish bounded software behavior and independently reconfirm old ETH execution;
they do not establish successful live operation of every repaired path.

The essay's Self-Writing section explicitly places write-access permissions in the fixed
initial design. Clocking the Factory requires temporal separation between operations and
judgement/governance. My interpretation is that an autonomous objective-setting population
still needs an execution substrate that can implement its decisions accurately. It cannot
infer capabilities into existence or change fixed kernel permissions through a charter vote.

This runtime submits real orders, and a resting venue limit order can fill while the model
is not thinking. The observed model-driven cadence is nevertheless about 82 seconds, not
high-frequency decision making. A future low-latency executor could implement population-
authored bounded policies while the population revises them more slowly; that would be a
new engineered capability and would need its own attribution, risk and recovery tests.

The current testnet manifest allows the `venue.set_leverage` tool to request up to 3x
cross-margin leverage on perpetuals. The adapter rejects spot leverage. This cap does not
prove the pre-existing account's current leverage settings or demonstrate that a live
leverage-change/close cycle has been exercised. No leverage tool was called in the prior
65-invocation experiment.

Before launch, require clean evidence for order acknowledgement and partial/full fills,
rejection and timeout recovery, cancellation and position reduction/close, bounded leverage
changes, and interruption recovery; match the chosen experiment horizon to the actual
latency and the charter's measurement support. Profit is an experiment outcome, not a
software correctness guarantee. Trading merely to avoid a hold penalty remains a specific
charter-proxy issue to expose to the population, not hide by increasing run duration.

At the measured mean spacing of 81.64 seconds, the configured 60-event backstop and
3:1 separation imply about 4.08 hours in the current governance time conversion
(60 × 3 × 81.64 seconds). That is a conservative floor, not a promise of activation.
The declared 10-second profile gives a 30-minute floor before achieved latency is known.
Neither fits inside a ten-minute governance demonstration.

External API references checked for this assessment:
- https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/exchange-endpoint
  documents limit-order time-in-force and leverage updates.
- https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint
  documents read-only status lookup by order id or client id.

Two additional interpretation limits from the installed SDK inspection:

- `market_open` obtains a midprice before submitting an IOC limit order. The historical
  outer `ReadTimeout` does not identify whether the quote read or signed submission timed
  out. It remains an uncertain attempt; diagnosing the exact failing stage needs finer
  instrumentation than the old diary retained.
- The adapter currently inherits the SDK's 0.05 market-order slippage argument. That is
  a tolerance, not measured slippage or guaranteed execution. Price protection should be
  an explicit reviewed launch setting rather than an overlooked dependency default.

The stricter-side check initially broke two existing tests because omission of `side`
was already a supported buy default. The repair preserves that default and gives it
consistent propensity labeling; an explicitly invalid side remains refused. The first
full gate had 2626 passes and those two failures. The corrected focused set passed 50
checks before the final full-gate rerun. A final action-label refinement also ensures
that writing a trade label in the action field is marked malformed, not recorded as an
executed trade.

Latency evidence from the old diary: 65 `provider.complete` calls, 131 `exchange.account`
calls, 49 `exchange.mids` calls, and 14 `exchange.fills` calls across seven ticks. The
621 journaled IO calls also include local operations such as 256 `provider.affordable`
and 85 instrument-catalogue reads, so that number must not be presented as 621 HTTP
requests. The new process timings distinguish operation count from elapsed cost.


## Final verify gate, verbatim

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
14 workers [2628 items]

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
........................................................................ [ 30%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 43%]
........................................................................ [ 46%]
........................................................................ [ 49%]
........................................................................ [ 52%]
........................................................................ [ 54%]
........................................................................ [ 57%]
........................................................................ [ 60%]
........................................................................ [ 63%]
........................................................................ [ 65%]
........................................................................ [ 68%]
........................................................................ [ 71%]
........................................................................ [ 73%]
........................................................................ [ 76%]
........................................................................ [ 79%]
........................................................................ [ 82%]
........................................................................ [ 84%]
........................................................................ [ 87%]
........................................................................ [ 90%]
........................................................................ [ 93%]
........................................................................ [ 95%]
........................................................................ [ 98%]
....................................                                     [100%]
======================= 2628 passed in 699.56s (0:11:39) =======================
```

Final focused execution/propensity/recovery checks: `50 passed in 2.14s`.
The final full gate includes the compatibility correction and malformed-action refinement.
