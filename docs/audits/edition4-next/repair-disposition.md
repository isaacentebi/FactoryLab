# Astra and Opus repair pass

This pass changes existing paths rather than adding institutions. It does not
change norms, impose an activity or profit target, lower registration prices,
alter learners, or launch a funded world. Existing uncommitted work is retained.

## Disposition

| Finding | Action and limit |
| --- | --- |
| Astra F1: final judge's evidence and recursive commitment | Repair and production-route regression in the existing feedback/meta path; validation recorded below. The independent normative fact is not an oracle for interpreting every claim. |
| Astra F2: execution mistaken for useful dependency | The existing nonfinancial probe now explicitly reports downstream consumption as `not_expressible`. Its receipt has no observation binding the result to a later independent outcome. No utility flag, self-reported benefit, or hash match is substituted for that missing fact. |
| Astra F3: delayed evidence loses exposure | The existing probe now compares evidence before the maturity snapshot, after it, and an unanswered timeout. Late observable evidence is absent from the frozen commission; an unknown produces no learner update. This exposes the pressure; it does not justify automatically rewarding delay or changing the learner. |
| Astra F4: insufficient governance runway | The existing observer reports the activation floor and one subsequent consequence window, distinguishing declared and measured tempo. Missing measurements remain unknown; elapsed ticks do not guarantee a proposal or activation. |
| Astra F5: income-to-inference unproved | One existing monetary test file now covers unconfirmed claim, one-time confirmed owner credit, reserve custody, and purchase of provider credit from earned funds with zero initial reserve. This uses the scripted rail, not an external buyer or real provider bill. |
| Opus: disabled address appears as unused | Existing report/observer distinguish disabled, enabled with no observed use in the supplied window, and unknown configuration. Zero alone carries no availability claim. |
| Opus: registration promises a sale | Both compact and reference descriptions now say that registration freezes a priced program; it supplies neither hosting nor discovery. Seller documentation now accurately describes claim verification, ownership credit and custody. |
| Opus: operator needed to publish every product | The existing seller refreshes registrations automatically. Its host, route and spool can be configured before launch. That removes the alleged need for a discretionary operator action per product; public index listing and real demand remain unverified. |
| Opus: expensive registration | No price change. A tool debits personal entitlement; a service consumes shared novelty capacity. The absent attempts do not identify price as the cause. |
| Opus: jail or immutable norms block Class 3 | The completed control's jail was available. Immutable founding norms are intentional; operational cards, prices and tempo are revisable. No bypass is added. |

## Experimental boundary

The completed control ran for 48 ticks at about 37.9 seconds per tick. The
manifest's governance floor is 180 ticks, and one further 60-tick consequence
window takes the simple lower-bound plan to 240 ticks: about 2.53 hours at that
observed pace. At the control's cost rate, that is about USD 2.70 of inference,
before extra governance or consequence evaluation. Neither number is a budget
guarantee; slower outstanding loops can raise the cadence floor.

After source changes, a feedback comparison needs a fresh control on identical
source. The earlier control is preserved as historical evidence. A communication
experiment enables address explicitly at launch. A self-support experiment
requires a preconfigured public seller and independently paid uptake followed
by a verified inference purchase. Testnet profit and scripted buyers cannot
establish that claim.

F2's missing downstream observation and F5's real economic closure remain open
research/launch conditions. Declaring those repaired by adding a success label
would repeat the audit's central mistake. No new model calls, treasury transfers,
external payments or mainnet activity are part of this repair pass.

## Validation

Final local package release:
`7050cec67be5e8ce64e817ee1aac44cf88f39bc9eaa02133b7f9aa248d414db6`,
on base `6c4c864` with uncommitted changes. This repair has not been committed,
merged, deployed or exercised in a paid population. The previously installed
release remains a separate deployment record.

F1's repair reuses the ordinary normative commitment. It preserves the finding,
frozen norms, original horizon and evidence snapshot through recursive review.
All cited evidence survives; uncited context is bounded with omissions counted.
An evidence-present unknown keeps the judge's conformity open for correction,
without scoring the producer. Tests exercise both an independently blamed and
unblamed return, as well as a genuinely missing normative fact. The independent
anchor judges the original return's normative consequence; it does not certify
that every interpretation of a receipt is correct.

The checkpoint coverage gate exposed a preexisting lazy-state mismatch:
snapshotting initialized an absent `_grounded_pending` field. Runtime construction
now initializes both grounded collections explicitly. The existing coverage test
passes without weakening its assertions or exempting state from checkpointing.

Commands:

```text
uv run ruff check .
All checks passed!

uv run pytest
============================ 2123 passed in 29.00s =============================

uv run pytest -m gate -n 2 tests/runtime/test_grounded_feedback.py tests/runtime/test_grounded_evidence_snapshot.py tests/runtime/test_grounded_multi_router.py tests/runtime/test_grounded_commission_scope.py tests/runtime/test_cascade.py tests/runtime/test_checkpoint_coverage.py tests/audit/test_r3_g_verdict_consequence.py tests/runtime/test_nonfinancial_consequence_gate.py tests/scripts/test_edition4_nonfinancial_probe.py tests/scripts/test_edition4_observer.py tests/scripts/test_edition4_report.py
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
plugins: xdist-3.8.0, anyio-4.15.1
created: 2/2 workers
2 workers [17 items]

.................                                                        [100%]
============================= 17 passed in 14.75s ==============================
```

`git diff --check` passed. The offline probe completed without model calls or
network activity; its new output is `nonfinancial-repair-results.json`. Earlier
probe outputs and paid evidence are preserved. No new test module, dependency,
runtime subsystem, public endpoint or account was added.

Files changed in this pass (excluding earlier retained work):

- `factorylab/runtime/feedback.py`, `loop.py`, `bootstrap.py`
- `factorylab/cortex/schematics.py`
- `scripts/edition4_observer.py`, `edition4_report.py`, `edition4_nonfinancial_probe.py`
- `tests/runtime/test_grounded_feedback.py`, `test_nonfinancial_consequence_gate.py`
- `tests/scripts/test_edition4_observer.py`, `test_edition4_report.py`, `test_edition4_nonfinancial_probe.py`
- `tests/audit/test_r3b_money.py`
- `docs/manifest.md`, `deploy/README.md`
- This disposition and `nonfinancial-repair-results.json`
