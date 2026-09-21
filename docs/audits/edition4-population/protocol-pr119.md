# PR119 population run: predeclared protocol

User authorized the next population experiment after PR119 merged. Freeze source
`a874f1e15901754ebaa5f4e6497612861f3ce1b0` at
`/tmp/factorylab-population-a874f1e15901`. Use the same R2 roster and base manifest
`work/coverage-60-r2/world.toml` as the previous 240-tick run. Output:
`work/population-pr119/live`. No population prompt or policy edits.

The operational launcher saves the effective manifest before any provider call and
requires a round-trip hash match. It returns the runner's original object unchanged.
The saved TOML is the original manifest for an emergency `factorylab kill`, not a
newly invented recovery world. The runner and all imported runtime code stay frozen.

Bounds: 240 ticks, requested 10-second interval, independent 120-minute deadline,
$5 admission cap, 1,000 model calls. Preserve reasoning settings, compact context,
realized producer feedback, addressing enabled. Source, settings and cap remain
unchanged after launch. Inference is serial; venue time is not artificially sped up.
Calls already in progress can finish after the deadline. No automatic paid retry. Unlike the interrupted PR117 run, isolated provider
exceptions keep their full quote reserved while later work may continue. Three
consecutive provider exceptions, cap/call exhaustion, successful responses without
authoritative bills and reported overruns still stop admission. Uncertainty remains
visible in the report; core balance reconciliation does not release the external reserve.

Only Hyperliquid testnet and prepaid inference are available. Deny all x402
purchases, top-ups and treasury transfers; unset the external income spool.
No live observer, diary, events or log reads. While running inspect only process
liveness and terminal-report existence. The configured terminal wind-down may
close testnet positions; verify its result rather than assuming success. No extra
recovery trades are authorized by this protocol.

Before launch require a read-only venue snapshot without errors and no open
perpetual positions or orders. Existing spot dust may remain under the same
precommitted $1 bound. Prior run's end snapshot is not current proof.

## Questions and predictions

1. **Recovery:** among seats producing invalid tool requests, how often does an
   exact error reach a later prompt, get read, and precede a valid corrected call?
   Predict fewer repeated identical argument failures per exposed seat than before.
   No attempted invalid request means this question is untested, not passed.
2. **Construction:** are predicate facts retrieved, admission attempted, and code
   accepted? Distinguish no attempt, not-ready window, schema/code failure, admitted
   artifact and independent use. Shape disclosure should remove the known list-versus-
   dictionary misunderstanding only when the participant obtains the disclosure.
3. **Consequences:** distinguish initial verdicts from final findings, score reasons,
   evidence references, unknowns/censoring and feedback read in subsequent decisions.
   Predict less use of zero income alone to justify contrary findings; do not equate
   fewer contrary findings with better learning or relax frozen norms to get activity.
4. **Population:** count valid messages, replies, artifact reuse, funded births,
   child invocations, proposals/activation and antagonist exposures separately.
   No prediction that these repairs alone produce cooperation or Class 3.
5. **Cost and action:** distinguish attempts, refusals, submitted orders and fills;
   report role costs and context/token distributions. No trade is not automatically
   failure; a trade is not automatically success. Testnet P&L is not inference income.

Coverage screen stays 240 ticks, 10 assessed grounded findings and one contrary
finding, with authoritative billing. This is the runner's inherited coverage screen,
not a behavioral target: an otherwise clean run with no contrary finding may fail
that screen and still be informative. Report outstanding horizons explicitly.

This is a before/after bundled-repair comparison, not a randomized causal estimate:
market conditions, stochastic model outputs, timing and the cap differ. Previous
$3 cap was not binding; this $5 cap preserves more headroom rather than predicting
additional activity. Sale rails are disabled, so independent income is untestable.

After process exit read report.json first, require `summary.terminated=true`, then
run the existing terminal report and experiment analyzers. If unsealed, use only
the supported final kill workflow with the original effective manifest; never
restart or reconstruct a different manifest. Reconcile the reserved
`pr119-population-240` cap exactly once, retaining all prior billing uncertainty.
Publish findings and the next warranted action. No follow-on world launches without
the user's instruction.

## Comparison boundary

The immediate predecessor stopped after 26 ticks due to one dispatched provider
error. This run tests the same population after only supervisor recovery changed.
Retain the original 240-tick PR116 run as the longer descriptive reference, not
a matched causal control. If another isolated provider exception occurs, report
whether later distinct work executes without retrying that call or erasing liability.
