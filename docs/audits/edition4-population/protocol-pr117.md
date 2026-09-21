# PR117 population run: predeclared protocol

User authorized the next population experiment after PR117 merged. Freeze source
`30e0af8fb649e650b4a8fac88e5d1eff0f63b488` at
`/tmp/factorylab-population-30e0af8fb649`. Use the same R2 roster and base manifest
`work/coverage-60-r2/world.toml` as the previous 240-tick run. Output:
`work/population-pr117/live`. No population prompt or policy edits.

The operational launcher saves the effective manifest before any provider call and
requires a round-trip hash match. It returns the runner's original object unchanged.
The saved TOML is the original manifest for an emergency `factorylab kill`, not a
newly invented recovery world. The runner and all imported runtime code stay frozen.

Bounds: 240 ticks, requested 10-second interval, independent 120-minute deadline,
$5 admission cap, 1,000 model calls. Preserve reasoning settings, compact context,
realized producer feedback, addressing enabled. Source, settings and cap remain
unchanged after launch. Inference is serial; venue time is not artificially sped up.
Calls already in progress can finish after the deadline. No automatic paid retry.

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
`pr117-population-240` cap exactly once, retaining all prior billing uncertainty.
Publish findings and the next warranted action. No follow-on world launches without
the user's instruction.
