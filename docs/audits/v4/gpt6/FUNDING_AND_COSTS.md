# Funding and operating-cost decision

Base: `isaacentebi/FactoryLab@3a27fa4cefd2511c13c7a7d779a913249606e0bc`.
Planning basis: September 14, 2026, Mexico City (the pinned commit is timestamped September 15 UTC).
Status: proposed NEW experiment. No funding, provider purchase, account change, paid probe, or live run was performed for this plan.

## Recommendation

Authorize a **$2,000, eight-week research envelope**, not a $2,000 trading account.
This is a proposed amount the experimenter can choose to risk; it is not an assessment of personal affordability and not an estimate of software-development labor.

| Allocation | USD | Treatment |
|---|---:|---|
| Live population inference endowment | 1,200 | Finite, prefunded; released by the genesis schedule below |
| Pre-genesis calibration and small baseline comparisons | 200 | Separate experiments with separate identities; never an undeclared rescue fund |
| Trading / productive-work working capital | 200 | Maximum founder contribution to the initial work account; receipts and obligations remain separate |
| Runtime, authority service, backups and storage for the study | 150 | Planning allowance, not a vendor quote; report as prepaid infrastructure/subsidy |
| External data, payment, bridge and gas charges | 100 | Explicit allowance; actual expense only when charged, no double count with inference |
| Uncommitted contingency | 150 | Outside the living factory; usable for development or a NEW identity, not a discretionary rescue |
| **Total** | **2,000** | Excludes hired engineering, coding-agent implementation tokens, taxes and material egress overruns |

A lean four-week experiment could use roughly $600–$900, with fewer paid invocations and a much smaller external-action surface. A $5,000–$10,000 research program should primarily purchase repeated worlds and controlled comparisons, not place all of the money in one population. These are design allocations, not quoted market prices or minimum capital proven to cause emergence.

Do not fund the current implementation more heavily before separating its financial accounting from synthetic resource charges. More money does not repair an unusable feedback loop, an untrusted receipt, or a poor unit of selection.

## Genesis funding contract

Use a **prefunded endowment with scheduled unlocking**, not manual weekly top-ups.

| Offset from actual launch | New compute entitlement | Cumulative entitlement |
|---|---:|---:|
| Day 0 | $300 | $300 |
| Day 7 | $150 | $450 |
| Day 14 | $150 | $600 |
| Day 21 | $150 | $750 |
| Day 28 | $150 | $900 |
| Day 35 | $100 | $1,000 |
| Day 42 | $100 | $1,100 |
| Day 49 | $100 | $1,200 |
| Day 56 and later | $0 | $1,200 |

Rules:
1. Commit the complete schedule, backing, permitted uses and time anchor before genesis.
2. Unspent unlocked entitlement carries forward. There is no weekly expiration or reward for using the allocation.
3. The population cannot borrow against an unreleased tranche.
4. Unlocking is an internal reclassification of existing backing. It does not call `Wallet.drip`, credit a provider twice, or recognize revenue.
5. Realized, cleared external earnings can finance additional work under the committed permissions. The $1,200 cap is on founder subsidy, not on future use of genuine earned resources.
6. A terminally dead identity cannot receive a grant or resume. A live identity temporarily without a usable allowance may enter the new `budget_dormant` state only when a valid future release or bounded pending settlement exists.
7. Deterministic reconciliation and previously committed risk/wind-down obligations continue during dormancy. A dormant trading population is not permission to abandon positions.
8. After the final release, continued life can still be funded by unspent subsidy. Merely surviving past day 56 is not proof of self-financing.
9. An explicit experimental end or exhaustion of prepaid hosting is reported as an administrative/infrastructure end, not mislabeled learning death.
10. No debit card auto-top-up, discretionary release override, or operator “looks promising” exception is supplied to the population.

Weekly transfers made manually under an intended schedule are weaker than a prefunded release policy: the founder retains discretion, payment can fail, and the population may learn to bargain for continuation. A fixed daily stream is another valid experimental treatment, but comparing daily and weekly release is a later experiment, not a runtime tuning decision.

### Backing implementation choice

For the first implementation, prefund isolated provider credit and/or an isolated compute-purchase reserve under the authority service. Represent the full owned backing once in the financial records, with its nonfungible resource class. Hold the unreleased portion as encumbered.

A provider's $1,200 balance does not mean a project may spend $1,200 on day 0. The authority service checks the unlocked budget as well as actual provider liquidity. A provider-credit claim cannot finance venue margin. Multiple pools or accounts are reconciled separately.

Where some endowment remains as reserve cash, a later reserve-to-provider purchase is a conversion of the same assets, with any actual fee separately recognized. A calendar entry alone never proves the conversion occurred.

Do not presume today's existing account balances are $90 or $100. The committed rehearsal wallet is fake-USD accounting, and the launch decisions explicitly require a fresh resource reconciliation. Existing credit can count toward the proposed $1,200 only after identity, ownership, amount, and existing obligations are verified.

## What current repository evidence says about cost

The saved paid-inference result and accompanying report describe **13 invocations costing $0.028031 over 119.10 seconds**. The exchange and treasury were fake. The report explicitly says the costs were adapter/meter observations and were not independently reconciled with provider billing. This is historical evidence, not a run performed for this plan. [S39, S40]

Arithmetic from that small sample:
- Mean recorded invocation expense: $0.028031 / 13 = approximately **$0.002156**.
- At exactly that mean, $90 buys approximately **41,740** invocations.
- $1,200 buys approximately **556,527** invocations.
- Extrapolating the entire 119.10-second trace continuously gives approximately **$20.33/day**.
- This is NOT an expected daily cost: the trace used an accelerated four-tick test, a specific roster, a particular prompt size, mostly simple responses and no demonstrated commercial work.

Illustrative 56-day sensitivities, after all model invocations across producers, judges, continuations and children are counted:

| Scenario | Paid model calls/day | Mean cost/call | Daily inference | 56-day inference |
|---|---:|---:|---:|---:|
| Lean | 500 | $0.010 | $5 | $280 |
| Working case | 2,000 | $0.010 | $20 | $1,120 |
| Expensive / expanding context | 4,000 | $0.015 | $60 | $3,360 |

Those average prices are assumptions, not live model quotations. A $1,200 allowance supports 240, 60 or 20 active days at the respective daily burns before revenue. Scheduled access may extend calendar duration through dormancy; it does not purchase additional active computation.

Use a blended population: cheap routine handling and deterministic execution, plus materially stronger paid reasoning for difficult work. Do not force every assembly into a cheap model simply to maximize invocation count. Model families and efforts should be selected from actual contract-completion evidence, then allowed to change through the registry.

## The current rent trap

`NotesSpec.byte_window_micro = 1`, and `charge_window()` applies retained bytes multiplied by elapsed measurement windows through `Meter(self.wallet)`. The mixed rehearsal manifest has a **two-minute** novelty window. `PricingMixin._manage_reserve_window()` calls the rent collector at that same boundary. [S04, S09, S10, S11]

At the declared cadence, a retained 64 KiB total entry footprint accrues:
`65,536 bytes × 1 micro-USD × 720 windows/day = 47,185,920 micro-USD/day`, or **$47.18592/day**.
At the 256 KiB notebook cap, it is **$188.74368/day**. Charges are only paid when affordable; unpaid amounts remain liabilities. Overrunning/skipped window boundaries can alter the actually realized window count.

These are **internal wallet charges**, not verified payments to a storage vendor. Their present interpretation can both dominate the experiment's virtual burn and separate the wallet from the external pots. Merely raising the starting balance is the wrong response.

The proposed change:
- actual external invoices decrease financial assets once;
- internal scarcity rents move budget entitlement to a declared internal service/commons account, never invent an external cash loss;
- storage exposure is measured in elapsed byte-time, independent of governance cadence;
- fractional micro-unit accrual retains a remainder so frequent collection cannot create rounding-based charges;
- deletion/archival and prepaid leases are possible with explicit liabilities and retained provenance.

## Calibration needed before fixing the roster and start

Extend the existing two-case script, do not run it unchanged. It currently invokes `provider.complete` directly and loads local credentials. [S37]

The new metered calibration mode needs:
- an explicit total approved paid budget, with separate request ceilings and no automatic account refill;
- scripted exchange and treasury, production effects disabled at the authority boundary;
- whole decision trees (including continuations), ordinary tasks, failing tasks, long contexts, archive retrieval and complex judgment;
- exact served model/version when disclosed, usage, cache and hidden reasoning, cost provenance, failure/uncertainty classification, completed work and latency;
- a time-at-real-cadence run spanning several complete consequence horizons, rather than just short synthetic responses;
- reports of p50/p95 per-work-item total external cost, total governance/evaluation overhead, external invoices and synthetic charges separately;
- holdout tasks and multiple fixed seeds. A sample is not independent merely because it has another handle.

Calibration is development before genesis. It must not turn into an operator judging and rescuing the living population.

## Public pricing checked for this plan

1. Venice API pricing, accessed September 14: https://docs.venice.ai/overview/pricing .
   The page lists token-based API charges and separate feature charges. As an illustration, its listed web-search charge is $10/1,000 requests, additional to tokens. Do not substitute these public rows for a successfully verified live catalogue and account-specific bill.
2. OpenRouter FAQ, accessed September 14: https://openrouter.ai/docs/faq .
   Usage is deducted from prepaid credits; token, request and reasoning costs vary by model. The fetched page acknowledges credit-purchase fees but did not render a usable numeric fee. No guessed fee percentage is used in this budget.
3. DigitalOcean Droplet pricing, accessed September 14: https://www.digitalocean.com/pricing/droplets .
   Regular Basic 4 GiB/2-vCPU is listed at $24/month; 2 GiB/1-vCPU is $12/month. These are base VM prices, not the full infrastructure quote, and do not establish that either VM can run this workload safely.

The inference budget buys APIs, not a GPU cluster. Engineering labor, automated coding-agent consumption, and a production-grade independent authority deployment are separate scopes.
