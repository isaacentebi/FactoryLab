# Removal of architect-added economic restrictions

The experimenter has already allocated the entire Hyperliquid account, remaining
OpenRouter credit, and Venice for additional compute. No new allocation decision or
architect-imposed daily inference budget is required.

## Deleted, not optional

- Order notional, gross-exposure and slippage fields from the manifest schema/parser.
- Runtime exposure/price admission enforcement and its supporting snapshot/quote path.
- The mainnet launch prerequisite requiring those limits.
- The capped execution-drill command, implementation and cap-enforcement tests.
- The short-rehearsal prerequisite that disabled paid rails/Venice.
- The added $88 balance floor in the current drafting roster; it now matches the original
  zero floor and original testnet treasury configuration.

Completed-run manifests and markers were preserved byte-for-byte only as `.txt` historical
inputs under `evidence/retired-execution-drills/`. They are not current launch profiles.
The population-authored charter was not rewritten or stripped of its own decisions.
Pre-existing factory rules and SDK behavior were not replaced with new economic policy.

## Retained repairs

Stable order identities, no blind resubmission, finite transport timeouts, safe uncertainty
reporting, final fill reconciliation, accurate accounting and actual charter loading remain.
The separate quote-stage classification introduced alongside the removed slippage option
was removed too; ambiguous SDK failures retain uncertainty instead of claiming no submission.

The earlier 0.11 SOL testnet round trip remains historical evidence of actual execution.
It is not a new live test of the revised tree. No new live run or commit was performed for
this removal. Production host/operation verification remains outstanding; additional trading
policy is not a prerequisite.

## Verification

Focused regressions: `31 passed in 1.43s`. These include absence of the deleted schema and
runtime policy, a $60 fixture order exceeding the removed $20 cap, and the original treasury
and balance floor in the drafting roster. A scan of `factorylab`, `scripts` and `worlds`
finds no removed cap fields, enforcement, forced paid-rail disablement or $88 floor.

Full required gate passed: 2,631 tests in 643.78 seconds. No new live run was performed.

## Changed files for this correction

- `factorylab/runtime/worlds.py`
- `factorylab/runtime/venue.py`
- `factorylab/runtime/loop.py`
- `factorylab/runtime/resume.py`
- `factorylab/runtime/cli.py`
- `factorylab/world/exchange.py`
- `factorylab/runtime/execution_drill.py` (deleted session-created file)
- `tests/audit/test_execution_limits.py` (deleted session-created file)
- `tests/audit/test_order_lifecycle_repair.py`
- `tests/audit/test_accelerated_repair.py`
- `scripts/rehearsal.py`
- `worlds/testnet-10m-roster.toml`
- `worlds/execution-drill*.toml`, corresponding `.used` markers, and `worlds/testnet-10m.toml`
  and its marker (moved to inert historical evidence)
- `docs/audits/v3/evidence/retired-execution-drills/*.txt` (exact historical inputs)
- `README.md`
- `docs/manifest.md`
- `docs/launch-decisions.md`
- `docs/audits/v3/short-experiments.md`
- `docs/audits/v3/production-readiness.md`
- `docs/audits/v3/caps-removal.md`

## Required gate output, verbatim

`uv run ruff check . && uv run pytest`

```text
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 14/14 workers
14 workers [2631 items]

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
........................................................................ [ 51%]
........................................................................ [ 54%]
........................................................................ [ 57%]
........................................................................ [ 60%]
........................................................................ [ 62%]
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
.......................................                                  [100%]
======================= 2631 passed in 643.78s (0:10:43) =======================
```
