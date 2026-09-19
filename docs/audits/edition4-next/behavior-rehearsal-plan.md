# Bounded behavior rehearsal protocol

This protocol screens launch-time factors. It does not establish autonomous goals,
institution formation, profitability, or production readiness.

Each arm freezes `prompt_mode` (`reference` or `compact`), `producer_feedback`
(`verdict` or `realized`), `address_enabled`, and the reasoning override
(`preserve`, `off`, or `on`) before runtime construction. Omitted factor flags preserve
the supplied manifest. Norms and assemblies remain unchanged. A reasoning override is
the one exception to roster-digest preservation;
the report records every tier's before/after reasoning object and both roster digests.
`on` requires every tier to declare a reasoning configuration. It retains an active
effort and turns an explicitly disabled boolean into an explicitly enabled boolean,
using the existing OpenRouter and Venice adapter paths. A missing setting causes a
preflight refusal. This makes enablement the factor under test; it is not evidence that
the upstream model generated or exposed reasoning. The report records requested
reasoning and the provider-effective request configuration. Actual hidden reasoning
remains unknown unless the vendor reports it.

The report's `protocol` records the wall duration, spend cap, call cap, planned tick
ceiling, minimum delivered ticks, and grounded sample targets before a run. These are
immutable during a run. The runner never lengthens a duration or evaluation horizon to
meet a target.

The default delivered-tick target is the manifest's 60-tick consequence backstop. For
context, the conservative governance activation floor is 180 further/fresh ticks and a
screen that first observes one full backstop then waits that floor needs 240 ticks. A
30-minute arm has a nominal ceiling of 180 ten-second ticks, but the prior arm delivered
only 48 because paid calls dominated the wall clock. Therefore a clean shutdown is not
enough: unmet tick coverage yields `behavioral_screen.status = "inconclusive"`.

In `realized` mode the default preregistered target is at least ten completed final
findings, including at least one contrary finding. A completed assessed sample must be
`supported` or `contrary` and cite at least one supplied evidence reference. Unknown,
malformed, and uncited answers do not count. The report separately records supported,
contrary, unknown, censored, and outstanding contracts. The contrary minimum is a screen
for interpretability, not a quota for model behavior; failure to observe one is
inconclusive and never grounds a forced relabel.

The runner also reports outstanding decisions, calls, decisions, exact cost/call rates
per delivered tick, and the existing process-local `provider.complete`, `exchange.account`
and `exchange.mids` call counts and duration aggregates. These measurements expose latency and cost without adding a second
benchmark harness. Testnet-only, prepaid-provider, denied-x402, denied-transfer, hard
wall, hard call, and hard spend boundaries remain in force.
