# PR121 population run: predeclared protocol

User authorized the next population experiment after PR121 merged. Freeze source
`b29f33745c38d117ad69e229e1c786d374ee21c1` at
`/tmp/factorylab-population-b29f33745c38`. Use the same R2 roster and base manifest
`work/coverage-60-r2/world.toml` as the previous 240-tick run. Output:
`work/population-pr121/live`. No population prompt or policy edits.

The operational launcher saves the effective manifest before any provider call and
requires a round-trip hash match. It returns the runner's original object unchanged.
The saved TOML is the original manifest for an emergency `factorylab kill`, not a
newly invented recovery world. The runner and all imported runtime code stay frozen.

Bounds: 240 ticks, requested 10-second interval, independent 240-minute deadline,
$5 admission cap, 1,000 model calls. Preserve reasoning settings, compact context,
realized producer feedback, addressing enabled. Source, settings and cap remain
unchanged after launch. Inference is serial; venue time is not artificially sped up.
Calls already in progress can finish after the deadline. No automatic paid retry. As in PR119, isolated provider
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
market conditions, stochastic model outputs, timing and the cap differ. The previous $5 cap was not binding; this run keeps it unchanged. Sale rails are disabled, so independent income is untestable.

After process exit read report.json first, require `summary.terminated=true`, then
run the existing terminal report and experiment analyzers. If unsealed, use only
the supported final kill workflow with the original effective manifest; never
restart or reconstruct a different manifest. Reconcile the reserved
`pr121-population-240` cap exactly once, retaining all prior billing uncertainty.
Publish findings and the next warranted action. No follow-on world launches without
the user's instruction.

## Native completion preflight and comparison boundary

The predecessor delivered 200 ticks, with 42 empty answers exhausting a 4,096-token
shared reasoning/output allowance. This run uses the provider-advertised completion
limits, frozen at bootstrap; no numeric reasoning cap is added. Other reasoning
settings stay unchanged. Missing advertised limits refuse launch. Longer native
allowances increase reservation ceilings; affordable actual bills alone do not
prove that a request can be admitted.

Launch is conditional on offline affordability and feedback-exposure checks and
three representative large judge calls passing production validation. Reserve $1
for that separate no-trading probe within the existing $50 total authorization;
its costs are not population costs and it has no automatic retries. Persist each
request and schema before dispatch and each response/bill before validation.

Predict fewer length-truncated empty answers. Do not predict that the 25 unknown
findings in the previous run automatically become assessable: they were largely
unverifiable claims about holding or deferring. Report valid final findings and
feedback use separately from parsing success. The doubled wall deadline aims to
allow 240 delivered ticks; model latency and synchronous processing still apply.
An in-progress completion may outlast that deadline. No outcome is guaranteed.

The comparison is descriptive, not randomized: native allowances and the wall
deadline changed, alongside market conditions and stochastic responses. No new
population objective, roster change, or activity reward is introduced.

## Prelaunch results

Offline investigation plus continuation succeeded, as did a founder-funded $2 Sol
child (native reservation about $1.51). Five focused feedback checks passed.
The late PR121 review exposed missing native metadata in the fake rehearsal
provider; only that test fixture was repaired. Two rehearsal gates, four native
completion gates, Ruff and 2,258 ordinary tests passed. Production source remains
the merged commit named above.

Three representative real-commission probes rendered 41,337, 22,047 and 20,486
characters. All used GLM with its advertised 131,072-token allowance and stopped
normally with authoritative bills totalling 3,755 micro USD. Both final assessment
and meta-review passed production validation. The initial judge returned a valid
catalogue request, not a completed judgment; no tool was executed in this probe.
These are representative reconstructions, not exact historical replays. Their
success does not establish that every future request will complete or that judgments
are good. Raw requests and responses remain private under work/population-pr121.

Fresh venue read: no perpetual positions or open orders. The preceding report was
also corrected: contrary feedback was fetched and retained in later working state,
but its acknowledgement cursor did not advance through that item. Read-and-revision
is observed; durable learning and causal improvement remain unproven.
