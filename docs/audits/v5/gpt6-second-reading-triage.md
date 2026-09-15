# GPT-6 Pro second reading: triage

Reviewed zip: `FactoryLab-edition2-dbc0c2e`. Triaged against main `e5a8f70`. Verdict: do not
launch unchanged. Its full report is `docs/audits/v5/gpt6-second-reading.md`.

The four decisions of 15 September (three cards and five norms; observer on DeepSeek 4.1 flash;
bills settled from the provider's balance; the wake publishing every answer) postdate the zip
and are not reflected in its charter section, which nevertheless argued for the same direction:
it praised removing the five quotas and named the cost, well-formed, tool and concentration
cards as the residual tensions; all four are now gone. Everything it says about the kernel,
the entitlement economy, the witness and recovery still applies, verified below.

| Finding | Verified on e5a8f70 | Disposition |
|---|---|---|
| P1-01 death evidence optional at restore: unreachable receiver resumes; witness path unwritable by the service | Yes: `runtime/witness.py:222`; `deploy/factorylab.service:34` | Fix, R2-A: a configured receiver with no verdict refuses; service may write `.witness/`; local-only is documented as the weaker guarantee |
| P1-02 backup omits the artifact directory; program runs with no state and reports ok | Yes: `deploy/backup.sh`; `kernel/artifacts.py`; `cortex/assembly.py` | Fix, R2-A: backup includes artifacts; resume refuses a missing referenced sha |
| P1-03 program seat pricing KeyError on world growth | Yes: `runtime/routing.py:453` | Fix, R2-B: flat-fee seats have no growth term |
| P1-04 all seats exhausted, pool positive, router NOOPs forever, no death signal | Yes: `runtime/routing.py:480`, `loop.py::_check_termination` | Fix, R2-A: commons release when nobody can act; then dormancy or `insolvency:entitlement` |
| P2-05 service income is a pool reclassification, root wallet never grows | Yes: `runtime/feedback.py:447–458` | Fix, R2-B: income books into the root wallet like venue P&L; seller credited from new money; a settled receipt is a paid-off consequence |
| P2-06 marked payoff freezes later loss attribution | Yes: `runtime/feedback.py:440` | Fix, R2-B: late realization charges the opener |
| P2-07 releases split per seat id: nine children take five times the share | Yes: `kernel/budget.py:293` | Fix, R2-B: releases split per lineage, paid to the root |
| P2-08 profitable compliant stability labelled learning death | Yes: `versioning/versions.py:127–130` | Fix, R2-C: requires a gone frontier, not zero edits |
| P2-09 amendment vote scored on region compliance, not the promise vs baseline | Yes: `runtime/governance.py` vote scoring | Fix, R2-C |
| P2-10 agent-reported propensity is a self-report | Known since round one; floored at 0.05 | Leave; documented |
| P2-11 `docs/manifest.md` and the manifest comment out of sync | Yes | Fix, R2-C |
| Facilitator URL read from the environment after launch | Yes: `runtime/seller.py:273` | Fix, R2-A: ledgered at Launch, resume refuses a change |

Not taken: its request for a full every-file certification (out of scope for one more pass);
its "paid demand as steering" point is a covenant, not code.

Its three closing questions are the ones the post-mortem must answer: which layer failed;
which entity's success is supposed to survive (the lineage split and late-loss fix are the
code's answer); and whether an almost-idle factory would be accepted as a result (yes; the
learning-death redefinition removes the last fixed pressure against it).
