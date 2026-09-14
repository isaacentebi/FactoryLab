> Correction: the completed ten-minute run is historical. Its input is archived as
> `evidence/retired-execution-drills/testnet-10m.toml.txt`. The current drafting roster
> restores the original testnet treasury configuration and zero balance floor; the
> rehearsal helper no longer requires paid rails or Venice to be disabled. The historical
> restrictions and amounts described below are not current factory policy.

# Short experiments before the first move

This workflow separates live execution evidence from accelerated mechanism evidence.
It does not reward trade counts or treat a short profitable interval as successful learning.
Source: the experimenter's Downloads/Superdark Factory.md, Chapter II.IV, especially
Self-Writing and Clocking the Factory. Governance remains slower than the consequences it grades.

## Changes

- Exchange uncertainty retains submission/lookup stage diagnostics. Exception messages are not
  recorded because they may contain signing material. Uncertainty remains uncertainty; no timeout
  turns into a rejection, and another same-coin order remains blocked until resolution.
- Public `registration_feedback` contains actual registration refusals. `return_feedback` contains
  judgement, propensity, and order feedback. Both derive from the existing bounded checkpoint
  buffer, including its older prefix-only records. No checkpoint field is removed.
- Assembly completions request JSON objects on OpenRouter; schema and semantic validation still
  run locally. JSON syntax alone does not establish a valid action, probability, or judgement.
- Each prepared experiment gets a fresh persisted exchange client namespace. Venue IDs
  no longer collide across short experiments that reuse decision handle numbers. Legacy
  manifests retain their old hash and ID behavior. A `.used` marker prevents rerunning a
  prepared live manifest; prepare another manifest for the next experiment.
- The drafting script exports the exact passing charter with hashes of its roster and content.
  Historical drafts are retained, never silently overwritten. A changed roster requires a new survey.
- The rehearsal preflight requires that exported charter verbatim. An omitted charter cannot quietly
  substitute the seed cards. Measurement windows and voted thresholds are not shortened at adoption.

## Reproducible commands

Run from the repository root. Choose new artifact names and evidence directories for each experiment.
The calibration and survey buy real OpenRouter inference. Neither executes returned actions.

```sh
uv run python -m scripts.calibrate_rehearsal --world testnet-10m-roster --out /tmp/calibration.json
uv run python scripts/draft_edition1.py --world testnet-10m-roster --out docs/charter/edition1-short-draft.md --charter-out docs/charter/edition1-short.toml
uv run python -m scripts.ratify_charter --world testnet-10m-roster --candidate docs/charter/edition1-short.toml --out docs/charter/edition1-short-ratified.toml --report docs/charter/edition1-short-ratification.json
uv run python -m scripts.rehearsal prepare --base worlds/testnet-10m-roster.toml --charter docs/charter/edition1-short-ratified.toml --out worlds/testnet-10m.toml
uv run python -m scripts.rehearsal preflight --world worlds/testnet-10m.toml --charter docs/charter/edition1-short-ratified.toml
uv run python -m scripts.rehearsal live --world worlds/testnet-10m.toml --charter docs/charter/edition1-short-ratified.toml --out /tmp/factorylab-short-live
uv run python -m scripts.rehearsal mechanism --out /tmp/factorylab-short-mechanism
```

The drafting roster is a preparation artifact, not the live experiment. The live runner checks
provenance before the CLI loads credentials. The CLI owns credential and released-ledger-key reads.
No funded manifest, mainnet deployment, commit, or restart of an older world is part of this workflow.

## What ten minutes means

The candidate live profile requests 10-second ticks and two-minute price/reserve windows. Model
cascades determine achieved throughput; declaring 10 seconds does not guarantee 60 ticks. The CLI
checks its deadline between ticks, so a cascade already in progress may finish after ten minutes.

The profile retains the 60-event consequence backstop and 3:1 governance separation. Its declared
minimum governance delay is 30 minutes and grows with measured tick latency or older outstanding
consequences. This live check does not claim to test charter amendment activation.

The synthetic mechanism run uses the existing 500-event scripted world and simulated time. It
exercises the production runtime with predetermined actions and venue outcomes. Any amendments,
registrations, pathology changes, or profits there are mechanism fixtures, not emergent model behavior.
Its actual completed loops must be reported from the summary/diary, not inferred from configuration.

The two Qwen 3.8 seats were changed to the already-available Qwen 3.7 model after
upstream HTTP 429s persisted through a bounded recheck. The final two seats passed
two synthetic checks each; the other seven seats passed their earlier unchanged checks.
The meta-a output ceiling increased from 800 to 2000 tokens based on the historical
length stops, and its two new checks completed within budget.

The live profile starts with a $90 accounting wallet and an $88 termination floor. That limits net
accounting drawdown, not gross inference spend or venue equity loss. Venue funds are testnet assets.
Reserve transfers, paid x402 requests, and Venice top-ups are disabled in this execution profile.
The accelerated manifest and the older ten-minute-tick world are left unchanged.

## Acceptance evidence

For each completed live check, retain its manifest and roster/charter hashes, summary, stderr, ledger,
and released key. Report exact attempt, acknowledgement, rejection, uncertainty and fill counts,
per-seat JSON/semantic failures, measured ticks, priced and unsupported cards, settled versus censored
consequences, registrations/amendments, and real inference costs. Read the diary only after death.

A card needing 100 returns or 50 forecasts per assembly remains unsupported until those samples
exist. A baseline-relative region needs a prior measured baseline. Closing a two-minute window does
not manufacture either. A pathology flag with insufficient history is not a clean bill of health.

## Historical BTC evidence

The dead accelerated diary's CLI postmortem returned `order not observed` for the first BTC
submission (io.result 8352) and its lookup (8354), while ETH io.result 10387 acknowledged order
60052668055 filled at 2501.6 for 0.01 ETH. The older adapter discarded its initial submission error;
that missing evidence cannot now be reconstructed. Neither absence of a readable acknowledgement
nor absence from a lookup proves that the BTC trade never executed.

## Validation and experiment results

Results are recorded below after completion. A running check is not acceptance evidence.

### Completed preparation and mechanism evidence

The first survey produced 52 proposals; 49 were individually measurable and 27 won a
majority. Its passing set was correctly refused at whole-manifest preflight because
of observation/accountability overlaps. The report and original export remain intact.
One proposal and one ballot completion hit the original survey output ceiling.

The same seeded five-seat committee then selected compatible subsets of the already
approved cards. Every ballot is composition-validated and a card still requires three
of the original five votes. The first local ratification helper mishandled TOML's
omitted null scope; that attempt is retained in `edition1-short-ratification.json`.
After the parser fix and a regression test, all five ballots were valid; the compatible
majority set has 13 cards (`edition1-short-ratification-v2.json`). No thresholds or
measurement windows were rewritten by the operator.

The ratified charter includes a population-proposed `noop-ceiling`: at most 0.6 over
ten producer returns, and an absolute 5000 micro-USD cost cap over ten returns per role.
Cards requiring five or six windows remain unavailable until supported. The
`amendments_activated_floor` cannot be assessed as satisfied merely because the run ends;
this profile's governance floor exceeds ten minutes.

The 500-tick scripted mechanism run completed 17,365 internal events and 5,891 invocations.
It accepted 15 registrations, activated two charter amendments (ending at edition 3),
settled 8,403 forecasts, and closed four immune windows. Thrash was flagged. Ledger
verification, wallet conservation and seal release were all true. These are controlled
fixture outcomes, not spontaneous behavior or live-market evidence. The evidence is
`/tmp/factorylab-short-mechanism/summary.json` and its adjacent terminated ledger.

The initial full gate found one privacy regression: public refusal summaries must not
expose originating handles. The category split was corrected to retain anonymity;
the original test and the new checks passed before the final gate rerun.

### Verify gate (verbatim)

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
14 workers [2612 items]

........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 11%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 19%]
........................................................................ [ 22%]
........................................................................ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 30%]
........................................................................ [ 33%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 44%]
........................................................................ [ 46%]
........................................................................ [ 49%]
........................................................................ [ 52%]
........................................................................ [ 55%]
........................................................................ [ 57%]
........................................................................ [ 60%]
........................................................................ [ 63%]
........................................................................ [ 66%]
........................................................................ [ 68%]
........................................................................ [ 71%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 79%]
........................................................................ [ 82%]
........................................................................ [ 85%]
........................................................................ [ 88%]
........................................................................ [ 90%]
........................................................................ [ 93%]
........................................................................ [ 96%]
........................................................................ [ 99%]
....................                                                     [100%]
======================= 2612 passed in 682.93s (0:11:22) =======================
```

The two final ratification-specific regressions were added after that full run began.
The complete focused repair file then passed: `14 passed in 0.40s`.


### Completed live experiment, 2026-09-13

The runner exited successfully and the world terminated with `explicit_kill:budget`.
Ledger verification, wallet conservation and seal release are true. The ten-minute
budget took approximately 11m20s from ledger creation to summary completion, including
setup and the final in-progress cascade. There were seven ticks (indices 0–6),
65 invocations, and 226 internal events. Mean spacing between tick starts was
81.6 seconds; a declared 10-second interval did not achieve high throughput.

All 65 model calls returned status `ok` and stop reason `stop`; reported inference
cost was 214,380 micro-USD ($0.214380). This is the live run alone, excluding calibration
and charter ballots. JSON success is not semantic success: eight propensity declarations
were refused and four judgement returns were refused. Per-seat calls were seed-observer
15, antagonist-a 7, eval-a 11, eval-b 5, eval-c 11, eval-d 10, meta-a 5, meta-b 1;
seed-decider was never selected. Every invoked seat had zero parse/length/provider failures.

**Trading evidence:** seed-observer produced 11 holds, one malformed-for-execution
`buy:ETH:xs` action, and three `order` actions. Antagonist-a held all seven times.
The early ETH proposal's propensity was refused because the runtime expected the
lowercased identity `buy:eth:xs`; it did not become an order intent. The final three
orders were all from seed-observer:

| Decision | Request | Exchange evidence |
|---|---|---|
| decision-172 | Buy 0.001 BTC | Uncertain: submit ReadTimeout, lookup order not observed |
| decision-173 | Buy 0.005 ETH | Filled acknowledgement, order 60062288236, price 2468.6 |
| decision-175 | Buy 0.005 ETH | Filled acknowledgement, order 60062303272, price 2467.6 |

The raw summary reports three orders placed and **zero polled fills**. That is not
proof of no trading: the two filled acknowledgements are present at diary entries
3734 and 3795. All three submissions occurred in the final tick; no later tick ingested
fill events. Eight decisions and twelve consequences remained outstanding at death.
BTC is unresolved, not rejected; there was no duplicate submission. This experiment
reused a testnet account already holding positions, so account equity is not isolated
experiment P&L. Terminating the software is not evidence that venue positions were closed.

The charter was exactly the ratified 13-card edition. Three price windows completed;
six cards had measured values by the third window, seven remained unsupported.
Revision pressure reached 1.0; noop pressure first rose to 0.25 in the final tick.
One EXP3 learner registration was accepted, and a duplicate was correctly refused.
The next producer output explicitly recognized that refusal and stopped reproposing it.
No charter amendments were proposed or activated. 102 of 106 sealed forecasts settled;
21 consequences were censored. Three immune windows are insufficient to treat false
pathology flags as a health certificate.

The producer's final rationales explicitly say the buys avoid a hold under noop pressure
and give the consequence predicate a chance to pay off. This is evidence that the
loaded charter reached behavior. It is also a warning about the objective: activity
pressure can buy activity without creating a stronger thesis. There is no demonstrated
profitability, durable learning, or causal isolation of the charter change here.

### Next repair priorities established by this run

1. Reconcile terminal orders without buying more model decisions: bounded read-only
   lookups/fill ingestion, with an explicit unresolved count and separate acknowledged
   versus polled fills in the summary. Preserve the diary; never resubmit on uncertainty.
2. Make action identity consistent across schematics, propensity validation, learner
   actions and executable order syntax. The observed uppercase/lowercase rejection
   and rounded judgement bins should be tested end to end.
3. Profile and reduce cascade/API overhead before promising 60 ticks in ten minutes.
   This run made 621 recorded IO calls for seven ticks, including repeated market reads.
   Measure where time goes; do not change event timing or governance semantics silently.
4. Put the inactivity card back to population review with the observed activity-only
   rationales as evidence. Keep the actual charter, but do not equate fewer holds with
   useful inquiry. Longer card windows need a separate experiment with sufficient support.

These are new findings from the bounded run, not fixes claimed complete in this patch.
A further paid/live run was not started. No mainnet transfer or commit was made.

Evidence: `/tmp/factorylab-short-live/summary.json`, `preflight.json`, `postmortem.txt`,
`stderr.log` (empty), and the adjacent terminated ledger. Calibration and survey artifacts
are separately retained at the paths above; the successful ratification report is the
`-v2.json` file. The full verify gate is printed verbatim above, and the final ruff and
diff-whitespace checks were clean.

### Decisions made within the implementation

Used JSON object mode with existing semantic validation; replaced two capacity-blocked
Qwen seats before surveying; raised meta-a's output ceiling; adopted a second population
ballot for composition compatibility; retained voted sample windows; used fresh venue
identity namespaces and single-use prepared manifests. Used the existing scripted fixture
for the fast mechanism check, and finished the live cascade at the deadline rather than
interrupting an in-flight submission. No runtime source was changed during the live run.

### Changed files

- `docs/manifest.md`
- `factorylab/cortex/assembly.py`
- `factorylab/cortex/schematics.py`
- `factorylab/runtime/compute.py`
- `factorylab/runtime/governance.py`
- `factorylab/runtime/loop.py`
- `factorylab/runtime/venue.py`
- `factorylab/runtime/worlds.py`
- `factorylab/world/exchange.py`
- `factorylab/world/models.py`
- `factorylab/world/openrouter.py`
- `scripts/draft_edition1.py`
- `docs/audits/v3/short-experiments.md`
- `docs/charter/edition1-short-draft.md`
- `docs/charter/edition1-short-ratification-v2.json`
- `docs/charter/edition1-short-ratification.json`
- `docs/charter/edition1-short-ratified.toml`
- `docs/charter/edition1-short.toml`
- `scripts/calibrate_rehearsal.py`
- `scripts/ratify_charter.py`
- `scripts/rehearsal.py`
- `tests/audit/test_accelerated_repair.py`
- `worlds/testnet-10m-roster.toml`
- `worlds/testnet-10m.toml`
- `worlds/testnet-10m.used`
