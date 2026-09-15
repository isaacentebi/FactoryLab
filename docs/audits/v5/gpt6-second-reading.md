# GPT-6 Pro second reading (reviewed zip: FactoryLab-edition2-dbc0c2e)

Verbatim as returned to the experimenter on 15 September 2026. Triaged against main e5a8f70 in
`gpt6-second-reading-triage.md`.

---

# Judgment: do not launch this snapshot unchanged

**The strongest reason to wait is that the experiment can lose continuity without reliably declaring death, or remain nominally alive after its population cannot act.** Those are failures of the object you intend to study. A trading loss would be an admissible result; an undocumented change of memory, an ambiguous resurrection, or an unrecognized inability to think would contaminate the result.

The repository does give the population meaningful constitutional authority. Its most consequential weakness is elsewhere: **the links between useful behavior, individual survival, collective resources, and irrevocable identity are less complete than the narrative suggests.**

## The four launch criteria

1. Money and identity are hard casts: fails on identity. An earlier valid diary can receive no death objection after relocation or loss of its external witness. P1.
2. Class 3 without architect steering: Class-3-capable constitution; kill-only commitment insufficiently binding (deployment environment, facilitator URL, connectors, paid demand). P1/P2.
3. Its death would teach why: not ready. Recovery can lose program state; resource admission and death detection disagree; service income and delayed losses have incomplete individual credit assignment. P1.
4. Survival possible in principle: structural trading path exists; service-income branch does not replenish root spending authority; program-seat route has a reproduced failure. P1/P2.

## Ontological

Darkness is "measurable decorrelation of intrasystemic information from external utility", not secrecy. Factory Lab: a sealed, constitutionally adaptive agent economy with genuine Class 3 affordances, not yet a demonstrated superdark factory. What the code adds: an individual entitlement economy inside the collective wallet (changes the unit of selection; a lineage can increase its future subsidy by multiplying seat IDs); scheduled endowment and dormancy; a programmable service economy; pathology detection as fixed machinery.

## Teleological and economic

`effective = min(1.0, max(0.0, score - penalty))` (`runtime/pricing.py:634`): scores are not money. Gaming a card does not incur a monetary fine; favorable judgments do not finance another thought. Quantities: learning score, seat entitlement, root spending authority, external assets and provider credit. The money loop closes for trading P&L (root wallet) but not for service income (pool reclassification; with an empty pool a $100 receipt gives the seller nothing). A treasury top-up is not proof of profit. Expected behaviour: cheap competence or cheap formal compliance; reliable formatting; rewards cheap to obtain and hard to overturn; replication rewarded more directly than discovery (nine child seats: one lineage's next-tranche share 888,888 → 4,444,440 micro-USD). Public-goods problem: judges and voters pay for collective benefit from their own entitlement. Current eight-card candidate better than thirteen; residual tensions on forecast skill in uninformative environments, paid-off fraction vs expectancy, no card for sustainable net earnings, no card for bounded reciprocity. No immutable card; the fixed elements are measurement grammar and immune logic (P2). The paper does not establish that charging for computation suffices for cooperation; sortition is not spatial assortment; the missing measurements are who pays, who benefits, who reproduces, whether benefit survives removal of subsidy. Success: day one a complete attributable cycle; day seven release separated from earned revenue, lower cost per useful outcome; day twenty useful behaviour viable, external net earnings contribute. A good death: finds no worthwhile use of resources, stops paying for appearances of progress.

## Mechanistic and adversarial

Insider strategies and catchers (all P2 unless noted): cost/well-formed cards (cheap valid responses); paid-off card (many small winners); judges (easy judgments); meta-judges; policy votes (region likely to pass anyway, scorer checks compliance not promised direction); antagonist (cheap attributable failures); immune organ (learning-death proxy false); propensity self-report; notes; program memory readable by any seat given the hash; paid connectors; registration multiplying seat IDs for endowment (release calculation reproduced); paid service seller settles payment before execution, failure is not a refund (P1 external obligation); Venice top-up masquerading as self-financing; gas route (potential P1, unverified); late trading losses after marked payoff not charged to opener (run); collective starvation: all seats entitlement-excluded while pool positive, router NOOPs, no insolvency (P1, run); crash/recovery artifact and witness seams (P1). Release mechanism identity-counted: `return self._split("release", amount, self.seats(), reason)` (`kernel/budget.py:293`). Outside steering: wake (observation not steering), manifest/package (caught), deployment environment incl. `FACTORYLAB_FACILITATOR_URL` (`runtime/seller.py:273`, P1 under kill-only), backup/restore (P1), connectors, models, paid demand.

## Prognostic (first twenty days, subjective)

30% cheap procedural equilibrium (stable failure/overfitting; do not kill early). 20% entitlement stall and release dependence (apparent learning death, potentially a lifecycle defect). 15% provider/reservation pressure (external forcing). 12% promising programmatic adaptation then machinery failure (not a pathology; kill right, verdict wrong). 8% lineage captures subsidy. 7% earnings with normative failure (the worrying success). 5% honest contraction / good death. 3% genuine adaptive niche. Cost calibration weak: ~$7.5/day extrapolated from the rehearsal report; $30 buys ~4 active days, a $10 tranche ~1.3; dormancy predicts intermittent life.

## Forensic

- P1-01 death evidence optional at restore: `runtime/witness.py:194–228` returns None when the receiver is unreachable; `runtime/resume.py:773–790` refuses only when `seen is not None`; `deploy/factorylab.service:32–34` permits writes only under `/srv/factorylab/runs`, the witness path is outside it. Run + read.
- P1-02 backup omits the artifact sidecar: `deploy/backup.sh:43–83`; `runtime/resume.py:532–534,795–798`; `kernel/artifacts.py:80–94`; `cortex/assembly.py:325–340`. A program runs with no state and reports ok, old state hash retained. Run.
- P1-03 flat-fee program seat fails on world growth: `runtime/routing.py:437–455` `self.prices.price(asm.spec.model_id)` raises KeyError for "program". Run (100 → 101 chars).
- P1-04 entitlement exclusion vs death: `runtime/routing.py:479–490,569–584`, `runtime/loop.py:431–478`, `kernel/termination.py:72–103`. NOOP with `unaffordable=False`, no termination reason. Run.
- P2-05 service receipts do not replenish root authority: `runtime/feedback.py:447–458`, `world/treasury.py:169–208`, `kernel/budget.py:156–172`. Run. A non-trading return has a permanently zero economic outcome absent a trading consequence.
- P2-06 marked outcome freezes opener's later loss: `runtime/feedback.py:430–445` `if payoff.marked or owner is None ...: return`. Run.
- P2-07 replication buys subsidy: `kernel/budget.py:99–101,254–293`; `runtime/governance.py:524–541,685–725`. Run.
- P2-08 profitable compliant stability labelled learning death: `versioning/versions.py:125–130`, `runtime/immune.py:79–101`. Run.
- P2-09 amendment vote graded on region compliance not promised effect: `runtime/governance.py:1439–1465`. Read.
- P2-10 agent-reported propensity not authenticated: `runtime/propensity.py:215–254`; `runtime/compute.py:925–966`. Read.
- P2-11 `docs/manifest.md:1030–1035` and `1255–1259` describe integrated mechanisms as absent; `worlds/edition2-testnet.toml:296–297` says the charter is not ratified while `312–315` records it. Read.

Verification: 76 existing offline tests plus 12 new, 88 passed, two deselected; one test file under `tests/audit/test_cold_dbc0c2e.py`. Not executed: full suite, runtime, testnet, mainnet, transfers, top-up, gas, droplet, real restore.

## What you did not ask

1. Which outcomes falsify the world design rather than indict the population: inconsistent resource authority, missing recovered memory, defective routing, ambiguous identity continuity.
2. What entity's successful behaviour is supposed to survive: seat, parent, lineage, collective wallet, router, provider model are not interchangeable; the subsidy and late-loss cases show the alignment is incomplete.
3. Would you accept a correct conclusion that this factory should do almost nothing: you need to, or the experiment carries a concealed objective, remain visibly interesting.

Final decision: do not launch unchanged. Priority: identity finality across restore; continuity of program memory; agreement between the ability to act and the definition of death; program-seat pricing; economic attribution separating earned resources from subsidy and assigning realized consequences to the capabilities responsible.
