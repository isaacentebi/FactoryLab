# Verdict consequence review (phase 4 Q, revised for v0.8 A16/A5/A9)

The binding rules are v0.7 section 2 and v0.8 sections A16, A5 and A9. Producers
still receive the existing verdict channel (antagonists the existing exposure
channel); this change adds no direct producer reward for wallet growth. Every
valid delivered evaluation carries two numbers: the `verdict` (charter quality,
graded by meta conformity) and the `payoff`, committed raw as `q` on
`return_paid_off`, outside the proposed vocabulary. An antagonist may commit
its own `payoff` about its own return on the same predicate.

Consequence attribution (A16): a close credits the realised P&L of the closed
quantity to both sides. The opener's credit is net of its opening fee and
accrued funding; the closer's is net of its closing fee. `return_paid_off` is 1
when the realised result credited to a return, as opener or closer, exceeds its
own compute and tool cost. A return that opens and closes nothing settles 0.
Only a decision with an open consequence account can own an order or a lot
(A9); a fill whose order belongs to no account is refused with a
`consequence.refused` ledger item and never enters the shared FIFO.

## Attacks and defenses

| Attempt | Defense and verification | Remaining incentive or limitation |
| --- | --- | --- |
| Wash trades; tiny manufactured realized gains | Both execution fees, funding and the return's complete compute cost reduce proceeds. Same-price round trips and marketable opposing limit tools lose both fees in tests. | A venue paying net maker rebates could make churn profitable after costs. Those are real proceeds and are not discarded. |
| Self-crossing limit orders | Only actual fills enter the table; order submissions and model claims are not P&L. The fake venue's opposing marketable limits retain both fees. | Exchange matching and self-trade prevention remain venue responsibilities. An external counterparty can subsidize a return with a favorable real fill. |
| Close another return's profitable lot | Opposite-side fills consume the same coin's inventory FIFO. The closed quantity's P&L is credited to the opener (net of its opening fee and funding) and to the closer (net of its closing fee); the closer's credit is attribution, not a second cash flow. A reversal opens only its residual quantity for its caller. | An assembly can still determine when another return realizes gains or losses, and a return that closes a winning lot someone else opened pays off cheaply: the closer is credited the P&L it realised, as the spec decides, so a cheap close of a large winner is the most reliable payoff available. Closing a loser is credited the loss. |
| Split fills or positions across returns | Fees and signed funding are allocated exactly with rational arithmetic. Only the final per-return total is floored to integer micro-USD, so rounding cannot create a positive payoff or erase a fractional loss. Each return pays its own full compute bill. | Splitting can still change the number of binary observations. Numerous small winning returns can outweigh a rare catastrophic loss in standing; the specified predicate does not encode magnitude. Flooring can penalize split negative fractions more, never reward them. |
| Bless noops or judge only nontrading returns | No-fill returns, including free router NOOPs, settle 0 on `return_paid_off`, but a judge now says so in `payoff`, not in `verdict`: research, tool-building and other non-trading work can be graded high on charter quality without the judge being punished on payoff. Scripted Haiku judges give hold/noop payoff 0.9; scripted Opus judges 0.1. Tests assert Opus judge `eval-c` ends above Haiku judge `eval-a`. | A judge correctly predicting 0 can preserve baseline standing without proposing useful trades. That is consistent with calibration, not a growth objective. |
| Collude by returning the prevalence rate | Existing Brier-v1 scoring against the pre-observation prevalence baseline remains in force. Predicting the baseline earns no expected excess skill on the same outcome distribution. Standing is built from payoff forecasts only; optional public-predicate forecasts settle to their handles and the baseline but never enter selection weight. | Selective judging, collusive routing and variance remain possible. Baseline prevalence remains per forecast, so multiple judges on one return count multiple observations. |
| Hold losers open indefinitely or select a favorable mark | The fixed backstop marks remaining lots at the latest observed venue mid, net of actual opening fees and accumulated funding, even while orders remain live. Tests cover a partially realized winner with a losing remainder. | A producer can time exposure around the known event dynamics, manipulate a thin venue's mid, or leave a position/order to lose money after its outcome freezes. No speculative exit fee is charged at a mark, and no actual close is executed. |
| Hide trading behind tools or failed follow-up calls | Every accepted venue market/limit/close order is attributed before its fills are processed. Cost is fixed only after all invocation rounds and metered tool charges. A test sets proceeds above model-only cost but below the full cost and requires outcome 0. | Registration/trial payments are not compute costs of this predicate. Future capability types must preserve original-handle attribution. |
| Lose partial fills through repeated polling | The live runtime uses an inclusive timestamp cursor over all Fill fields and multiplicity, instead of the existing adapter's order-ID deduplication. Tests cover partial fills and identical same-timestamp executions. | The Exchange protocol exposes no execution ID. It must provide complete inclusive results; truncated history, missing fills, or late observations older than the cursor cannot be reconstructed here. |
| Liquidation or forged reported P&L | Liquidation consumes FIFO lots at its execution price; it credits no closer and cannot create a liquidator-owned position. Reported venue P&L is retained as evidence, but FIFO allocation uses actual opening and closing prices, because venue average-entry P&L can differ per close. | A fill that is neither a liquidation nor owned by an open account is refused and ledgered, so unknown inventory never enters the book; missing prior opening fills cannot be reconstructed from a liquidation alone. |
| Judge trades the account it forecasts | Venue and treasury write tools act only for a decision with an open consequence account; a judging decision is refused publicly (`tool.refused`). A fill for an order nobody with an account placed is refused by the lot table. Nothing routes to the assembly that authored the subject of an event, nor to the parent of a child's return (`route.excluded`). | A judge can still register a producer assembly and let that assembly trade; the trade is then a producer's, judged and priced as one. |
| Manufacture exposure with a wrong optional forecast | Exposure settles 1 only when the judge's mandatory payoff forecast about the antagonist's return scored below the baseline and the antagonist's own payoff forecast on that return scored above it. Optional forecasts never count. Exposure is priced through the antagonist's cards, and router mass on antagonists is capped at `evaluation.adversarial_share`. | An antagonist that knows its return will not pay off and forecasts so wins whenever a judge blesses it; that is the real failure the essay asks it to manufacture. Capped routing means exposure wins cannot buy more traffic than the cap. |

## Choices where the spec left details open

- A closer with no open lots and no accepted unfilled orders resolves as soon as
  its cost is final, with the P&L it realised already credited; the opener whose
  lot it closed resolves when its own lots and orders are done.
- Backstop time starts at the producer/antagonist invocation's runtime event,
  with settlement at `opened_at_event + consequence_backstop_events`. Events
  include internal events, matching the loop's existing event-index horizons.
- A return without accepted pending orders or open lots resolves as soon as its
  total cost is final. Accepted unfilled or partially filled orders defer early
  settlement until fills, acknowledged cancellation/rejection, or the backstop.
  This prevents asynchronous orders from being prematurely classified as noops.
- A backstop fixes an outcome, not inventory or order state. Later fills still
  consume the original lots and never revise or transfer the fixed outcome.
- A return with several coins/lots waits for all of them. Actual closes include
  both fees and signed funding; marks include incurred fees/funding only. No
  additional hypothetical liquidation or exit fee is invented.
- Fractional micro-USD stays exact until the final return total is floored once.
  Payoff requires at least one opened or closed lot and that integer net strictly
  exceed the full return cost. Fees/funding receipts remain signed actual cash flows.
- A missing mark leaves an outcome pending instead of inventing a price or
  treating missing evidence as failure. Market observations are cached from the
  normal venue event stream; internal events do not trigger fresh network reads.
- Each delivered evaluation gets its own zero-cost consequence decision for its
  payoff, including multiple judges of the same return and payoffs about
  antagonists. An evaluation missing either number is malformed: it is not
  delivered and creates no commitment. The original evaluator identity and
  parent handle remain the reward address even after retirement.
- The forecast retains the existing positive-horizon schema (at least one event)
  but its economic outcome may settle immediately after sealing, or before the
  backstop on a real close. The unmodified raw payoff is used before card penalties.
- Antagonist exposure remains pending while forecasts about the return remain
  outstanding; a late consequence can settle a decision with an earlier
  operational timeout. The two exposure facts (judge failed, antagonist beat the
  baseline) may arrive in different settlement passes and are held until both
  are known or nothing is pending.
- Summary `paid_off`, `not_paid_off`, `marked`, and `consequences_pending` count
  returns once, including unjudged returns. Existing forecast counters count each
  evaluator commitment. `lots_opened` and `lots_closed` expose scripted coverage.
- No manifest explicitly overrides the default 200. The field participates in
  the canonical manifest/hash and rejects nonpositive or noninteger values.

## Source and scope limits

Funding allocation is verified with signed `paid_usd` observations, including
receipts and partial closes. The current `runtime/live.py` adapter supplies rates
with `paid_usd = "0"`, not actual account funding payments. This work does not
infer payments from rates or claim live net-of-funding correctness without those
observations. The adapter is outside Q's file scope. No live venue or real-money
run is required or claimed by these tests.

The wallet can terminate while a venue batch contains several liquidation fills.
Lot accounting consumes all observed fills before wallet termination, so their
economic evidence is retained even if later wallet actions stop at death.
