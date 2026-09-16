# Edition 3, third round: make its costs, consequences, privacy and death true

Decided 16 September 2026 from GPT-6 Pro's third reading (`docs/audits/v6/gpt6-third/`),
followed in full by the architect's instruction. The reviewer's own words for the goal: "The
next move is not to force this population to be more entrepreneurial or more active. It is to
make its costs, consequences, privacy, and death true. Then let it decide what is worth doing."

Nothing here changes what a seat may want. Everything here changes whether what a seat is
told is true, whether what it does reaches it, whether what it keeps is private, and whether
death is death.

## Order

R3-A lands first (the reviewer's 16-file patch, its tests converted, the suite repaired). The
five below start from main after R3-A merges and run in parallel with the file ownership
stated; R3-E and R3-F change prompt text and so end with one re-ratification and one
re-screen.

## Contracts

### R3-B Money: three quantities, typed custody

Reading §2 and §3 (venue effects, duplicate income, collateral scope, account-read fallback).

- Three ledgers, never conflated: **learning score** (evidence for a rule), **seat
  entitlement** (spending authority within the compute budget), **assets and credits** by
  custody: `openrouter_credit`, `venice_credit`, `venue_perps` (cash, margin used, positions),
  `venue_spot` (balances), `base_reserve` (USDC), `pending_conversions` (source hold plus
  destination claim). Each asset changes only by a verified transaction, provider charge,
  refund or purchase; never by an internal reclassification.
- Venue P&L, fees and funding settle on the venue accounts only. The compute wallet is the
  constitutional spending ceiling and is labeled `authority`, not cash; its balance moves only
  for model, tool and program charges, rent (as authority), releases, transfers between seats
  and confirmed conversions into provider credit. Rent never reduces reported provider
  inventory.
- The bridge: a confirmed Venice purchase increases `venice_credit` and the authority it
  backs, and only that; Base USDC income increases `base_reserve`; principal converted is
  `financing`, shown as such and never as income; a pending bridge is a held source plus a
  pending claim; no OpenRouter replenishment is implied by the Venice route.
- Income receipts: idempotent on a receipt identity of chain, transaction, log index, asset,
  recipient; conflicting facts for one identity fail closed; the spool is a claim until
  verified against the chain read the treasury already performs.
- Collateral: the exchange adapter exposes `collateral_view` (account mode, collateral asset,
  eligible equity, margin used, open-order holds and whether already included, leverage for
  this instrument, observed at); the check is incremental margin plus holds not already
  reflected plus precommitted headroom against eligible equity minus margin used; unknown or
  stale collateral blocks new risk and never blocks cancellation or bounded reduction; spot
  buys and sells have their own checks.
- A failed venue account read renders `venue_accounts: unavailable` with the reason; it never
  fabricates equity or an empty position set.
- The request line carries the facts by custody with freshness; the reward line carries an
  addressed assessment (handle, scoring-rule version, outcome, score, sampling record). A
  consequence record reads like: "incurred 920 µUSD of provider cost, received 1,200 µUSDC of
  funding at Hyperliquid, position still open, committed hypothesis not settled."
- Acceptance: a venue loss leaves the compute authority untouched and provider inventory
  unchanged; a duplicate receipt books once and a conflicting one refuses; a confirmed Venice
  purchase moves reserve to venice_credit exactly; the collateral check admits an order the
  venue can carry and refuses one it cannot, from the collateral view, with spot and perp
  separate; the `you` block shows every custody account and never a fabricated one.

### R3-C Death and identity

Reading §3 (kill, witness, restore, retirement) and §6.D.

- `production_state` and `exposure_state` are separate. Kill sets production dead,
  irrevocably, first. Then a narrowly authorized **wind-down executor** with durable
  operation ids may only cancel, reduce, reconcile and finalize: it records
  `exposure_state ∈ {flat, dust_within_precommitted_bound, wind_down_pending, unknown}`, is
  restart-safe (a repeated kill or a restart reconciles by operation id and never repeats a
  close), treats "resting" and partial fills as not flat, performs a final account
  reconciliation, and reports residual exposure honestly. It cannot resume the population or
  open new risk. A ledger failure during wind-down never prevents death.
- Witness enforcement is identity-bound and fail-closed: the receiver requirement is part of
  the launch identity (ledgered in `Launch`), so unsetting the environment cannot remove the
  veto; the local witness file is keyed by launch identity, not by diary filename, so renaming
  a diary cannot revive it; a resume with a configured receiver and no verdict refuses (kept).
- Restore is transactional: every identity constraint (release digest, facilitator, killed
  identity, artifact presence, witness verdict) is validated before any saved field is
  assigned; a refusal leaves the runtime as it was.
- Retirement is final at the budget layer: a late credit to a retired seat goes to the commons
  and is ledgered as such; a retired seat never regains headship.
- Acceptance: partial fills, resting closes, unavailable mids, ledger failures, dropped acks,
  repeated kills and restarts: production stays dead, residual exposure is reported under the
  executor's authority, and neither renaming files nor unsetting a variable revives an identity.

### R3-D Evaluation and time

Reading §3 (privileged payoff, easy forecasts, commissioned-child route, cascade), §6.A–C, §7.

- Four objects, independently addressable: **execution receipt** (fill, refusal, charge,
  transfer, program result, failed delivery), **learning receipt** (assessment of one decision
  under a scoring version and horizon), **commitment** (promise, responsible principal,
  deadline, observation rule, unobservability conditions), **adjudication** (a contestable
  interpretation of whether evidence supports a norm or falsifies a proxy).
- Evaluation is a commission: subject, observation scope, evidence horizon, budget; it may
  conclude `unmeasured`; no synthetic producer returns (kept from the patch); a hold is judged
  only against something it committed to (a claim, a counterfactual, an observation rule, a
  resource decision, an accepted promise), never "looks prudent"; an exploratory allowance
  keeps unfamiliar work evaluable; the population can decline or reshape evaluation spend
  through its own answers, with no activity or profit quota anywhere.
- The remaining payoff privilege goes: the `payoff` field is optional; meta logic does not
  depend on it; when normative consequence is unreadable the verdict is `unmeasured`, not
  scored on payoff.
- Forecast returns are scored against a matched baseline of question difficulty (the existing
  prevalence baseline per predicate) and a forecast on a predicate whose base rate is at or
  above 0.95 or at or below 0.05 earns no standing; easy questions do not pay.
- The commissioned-child-judge route is removed from the catalogue and the prompt until it can
  execute without self-judgement.
- Fidelity objections stay unscored (patch) and become adjudications: an independent
  adjudicator (a judge that did not write the verdict and does not own the measurement; the
  antagonist's evidence route may supply the counter-case) resolves them into a learning
  receipt for the objector and a proposed reprice of the card for the population.
- Cascade separation is time and completed evidence: a tier's upward report aggregates the
  completed evidence of its window (the scope's observation window and settlement horizon),
  with precommitted jitter, and never fires on arrival count; execution facts and safety
  actions stay on the immediate path.
- Acceptance: no producer return exists for an empty draw; a judged hold with no commitment
  settles `unmeasured`; a forecast at a 0.99 base rate moves no standing; three simultaneous
  arrivals do not trigger a tier; an objection resolves through a different judge and reprices
  the card only through the population's route.

### R3-E Prompts and arithmetic

Reading §7 (lens out of the system prompt; repeated exposition; deterministic arithmetic) and
§8 (the literal replacements, `docs/audits/v6/gpt6-third/prompts.md`).

- The seat system prompt is the **common system contract** verbatim; the lens is seeded once
  into working state as `{lens, open_questions, active_commitments}` and never appears in the
  system prompt; the stable prefix is the common contract, the WORLD CONTRACT wrapper with the
  five norms verbatim, and the compact base capability index, serialized once and reused
  byte-for-byte; the `YOU` block follows the template with kernel-serialized slots (clock with
  tick duration; spending authority; provider inventory with freshness; venue accounts by
  custody; pending conversions; outcomes with exact ids, oldest first, with `more`); the
  moving block follows WORLD UPDATE (observation window, changes since last successful
  delivery, execution receipts, charter, catalogue changes, public and unavailable
  observations); the outcome-schema text is the OUTCOME CONTRACT (intended / submitted /
  settled / rejected / unknown; forecast fields; objection shape; pause condition; money with
  asset, custody and unit).
- A deterministic tool `calc` with unit-explicit operations: `notional`, `fee`, `funding`
  (with the venue's convention and the settlement period stated), `carry` (window, hourly
  rate, fees), `margin` (size, mark, leverage). Free or at the flat tool price; recorded like
  any tool; prescribes no objective.
- Acceptance: a golden prefix byte-identical across two requests and across a restore; the
  lens absent from every system message and present in every genesis head; `calc` answers the
  45 calibration cases exactly; the roster re-ratified (prompt change) and the final roster
  re-screened with `calc` available (the reviewer's point: the gate is not met without it).

### R3-F Attention and continuity, completed

Reading §3 (subscriptions, fold, inbox, artifacts) and §4 (acks).

- Deferral's contract is explicit and true: `defer` and `cadence_floor` cover routine world
  wakes; judge and meta commissions are paid work the seat may also decline by answering
  `cannot` at no cost beyond the call; the prompt says exactly this.
- The fold has three durable states, offered / delivered / acknowledged-by-execution; an
  invocation failure returns the fold to offered; coin filters apply to the delivered fold,
  not only to admission.
- Every consequence reaches its owner's inbox: fills, refusals, non-payoff forecast
  settlements, program results, failed deliveries; each item has an exact `outcome_id`;
  `outcome.get` takes an id; `ack_through` takes an id and acknowledges only items delivered
  at or before it; the inline window is oldest-first with `more`.
- Artifact lifecycle: ownership by (sha, owner) pairs so a second writer of the same bytes
  owns its own reference; publishing an existing sha publishes it; an unindexed blob is
  unreadable; a reference is written only after the bytes are durable; garbage collection
  only of unreferenced, unowned blobs, ledgered.
- Acceptance: a refusal lands in the deciding seat's inbox with an id; an invocation failure
  leaves the fold offered and the next wake sees it; a second writer reads its own artifact;
  `ack_through` an old id leaves newer items unread.

## Gates before the funded manifest

The reviewer's four, as tests and one run:

1. Financial reality: a testnet rehearsal whose venue account holds the real proposed $120
   (the experimenter withdraws the rest of the testnet balance first or the manifest declares
   the principal and the runtime refuses to use more), with custody reconciliation, exact-once
   income, collateral from the view, and a confirmed bridge.
2. Continuity and information boundaries: fault-injection tests around state writes, inbox
   delivery, acks and restore; no judge or wake output ever receives private state.
3. Finality: the R3-C acceptance, as tests.
4. Selection and time: re-screen final prompts and routes; no synthetic work; real evidence
   windows; the population can decline evaluation spend.

Then one rehearsal of at least 150 ticks on the final prompts, read for the reviewer's "week"
signals: fewer manufactured subjects, reliable receipts, accurate custody, decisions that change
because of evidence.
