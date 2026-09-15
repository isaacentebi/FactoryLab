# GPT-6 Pro cold audit: what it said, what is verified, what we do

Audited commit: `3a27fa4` (main at the time; still main). Reviewer output is preserved verbatim
under `docs/audits/v4/gpt6/` (cold audit, implementation plan, funding note, test log). Its
reviewer did not get a runnable checkout: eleven isolated tests on pinned excerpts, no suite,
no testnet, no every-file pass. Verdict: **do not launch this commit**.

## Verified against our code today

| Finding | Verified | Where |
|---|---|---|
| F2 Venice receipt false negative: confirmation compares the credit *stock* to before + amount, so one micro-dollar of usage after a lost ack blocks confirmation forever | Yes | `factorylab/world/treasury_rails.py:449` |
| F3 verdict baseline mismatch: judges are scored on `1 - share` but the baseline learns `int(share == 0)`; a constant judge shows spurious skill | Yes | `factorylab/settlement/settle.py:242-253` |
| F4 cost card counts successful returns only; expensive failures hidden inside the 10% the well-formed floor tolerates | Yes, intentional (T12/T58) | `factorylab/charter/measurement.py:329` |
| F5 tool discipline sums calls over the ten-return window; the card's prose says per return | Yes | `factorylab/charter/measurement.py:340` |
| Rent trap: notes rent is 1 µUSD per retained byte per reserve window | Yes, default | `factorylab/runtime/notes.py:18` |
| F1 identity does not bind the executable: resume checks manifest hash, adapters, addresses, not the release | Read, agreed | `runtime/resume.py`, `deploy/start.sh`, `deploy/backup.sh` |
| F6 generic blame dilutes by `1/n` under a capped total | Read, agreed | `runtime/pricing.py:574-611` |
| F7 opener and closer both credited the same P&L for `return_paid_off` | Read, agreed; wallet not double-minted | `settlement/lots.py:218-245` |
| F8 `docs/charter-explained.md` describes the old four-card charter and says only profit can refill thinking | Yes | doc |

The rent trap depends on the manifest. `worlds/compute-continuity-testnet.toml`, which the launch
decisions name as the template to verify the funded manifest against, has a two-minute reserve
window: 720 windows a day, so 64 KiB of retained notes costs the wallet $47 a day and the 256 KiB
cap costs $189 a day, none of it paid to anyone. `worlds/edition1-example.toml` and
`worlds/testnet.toml` use a one-hour window: $1.57 a day at 64 KiB. The wallet balance is the
death clock, so with the two-minute window the population can die of rent while the OpenRouter
credit sits untouched. This is a manifest choice, not code.

## What the reviewer did not measure, and what we did

It never measured burn; its "$20 a day working case" is an assumption. Our final rehearsal at the
ten-minute tick measured the real thing:

| Basis | Calls/day | Cost/day | $90 lasts |
|---|---|---|---|
| 9 seats, 600 s tick, eval-b on Qwen 3.8 flash | 1,337 | $3.47 | ~26 days |
| Same with Muse Spark on eval-b | 1,337 | $7.20 | 12.5 days |

That is below the reviewer's own "lean" band ($5 a day). The cost is the call count, not the
price per call ($0.0026 average): nine seats, 144 ticks a day, plus judges and continuations.
So the answer to "do we need far more inference money" is no. What the reviewer is right
about is different and cheaper to act on:

1. **Runway is feedback cycles, not dollars.** At one-hour windows a six-window consequence
   horizon is six hours; 26 days is roughly a hundred horizons. Enough to learn from.
2. **Trading-only refill on $100 is brutal.** Covering $3.47 a day of thinking needs a 3.5%
   daily return before fees. No budget fixes that; only a cheaper population or a second way
   to earn (its vNext) does. Edition 1 should be read as a substrate test that buys a good
   death, not as a self-financing proof.
3. **A top-up is not profit.** The treasury moves principal into Venice credit; the wake must
   label credit bought from principal separately from anything earned.
4. **The activity quotas cause its two most likely failures.** Its 30% "institutional churn
   burns the budget" and 25% "cheap formatted consensus" trajectories are produced by the noop
   ceiling, revision-presence floor, activated-amendments floor, verdict-consistency and
   evaluator-disagreement cards. Those five turn instruments into obligations.

## What it proposes (vNext) and why we defer it

Its implementation packet is a kernel redesign: physics version 2, four new norms, a budget
book with scheduled unlocks and a dormant state before death, project-level budgets and funded
reproduction, persistent programs as first-class executors beside models, a content-addressed
artifact archive, a paid-work venue beyond trading, a metric-challenge route, an external
authority service for spend and identity, and a fresh ratification. Eighteen work packages,
112 proposed tests, none written. Budget $2,000 over eight weeks ($1,200 inference released on
a schedule), lean version $600 to $900 over four weeks.

Its own advice is not to put a larger endowment behind the current accounting. We have no
larger endowment. And edition 1's ledger is the only evidence that will say which of the
eighteen packages matter. So vNext is edition 2, decided after edition 1 dies.

## The plan: a milligram edition 1

Money already in hand: about $90 OpenRouter, the Hyperliquid account, the Venice and Base
residue. No new money. The covenant stays: no refill, no steering, kill only.

### A. Fixes before launch (code, one to two days)

| # | Fix | Size | Re-ratify? |
|---|---|---|---|
| 1 | F1: ledger a `release_digest` (commit + `uv.lock` hash) in the `Launch` event; resume refuses a different digest; backup records it | small | no |
| 2 | F2: confirm a Venice purchase on canonical debit evidence plus balance not below before-minus-usage; drop the stock-vs-flow test | small | no |
| 3 | F3: baseline records `1 - share`, the same target the judge is scored on | small | no |
| 4 | F7: consequence credit conserved across opener and closer | small | no |
| 5 | Rent: funded manifest uses a one-hour reserve window and a `byte_window_micro` that makes the notebook cap cost cents a day | manifest | no |
| 6 | F8: rewrite `charter-explained.md` against the thirteen ratified cards; delete "only profit refills" | doc | no |
| 7 | F4: cost card measures every attempt, not successful ones | small | yes |
| 8 | F5: tool discipline is mean calls per return, matching its prose | small | yes |
| 9 | Wake: pots view labels credit bought from principal vs earned | small | no |
| 10 | F6: blame dilution floor per decision | medium | no; defer unless cheap |

Not taken: the external authority service and off-host monotonic death witness (the reviewer's
full F1). The release digest plus the sealed ledger is the milligram version; the trusted host
remains the operator, and the wake says so.

### B. One decision for the architect

Re-ratify the charter with the five quota cards removed (noop ceiling, revision presence,
activated-amendments floor, verdict consistency, evaluator disagreement) and with the F4/F5
measurement corrections. Eight cards remain: tool discipline, well-formed floor, cost cap,
censorship bound, forecast skill, consequence paid off, position concentration, turnover.
Re-ratification is one testnet committee vote, about an hour, and is the architect's move
before launch, which the essay allows. Recommended: yes. It removes the cause of the reviewer's
two most likely deaths at zero dollars.

### C. Launch as planned

Fresh reconciliation, funded manifest from the one-hour-window template with the re-ratified
charter, gate green, droplet, first move, kill only. Success criteria stated in advance so we
do not move them afterwards: day one a full paid cycle with truthful accounting; day seven a
criterion or allocation changed by a settled consequence; day twenty resources held by earnings
rather than principal. Death after learning is a good death.

### D. Edition 2

After edition 1 ends, triage its ledger against the vNext packet and fund the lean version only
if the ledger shows the population was resource-limited rather than institution-limited.
