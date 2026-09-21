# Offline readiness after the 240-tick run

The next paid run must study behavior rather than discover basic interface failures.
Passing isolated validators is insufficient: a participant must be able to discover
the contract, recover from a rejected request, execute it, and receive its consequence.
No further paid calls, venue orders, transfers or deployments were made for this audit.

## Independent scopes and evidence

- **Opus:** compact tool discovery, argument validation and execution. Full schemas
  are retrievable; required fields being behind retrieval is not an absent capability.
  Direct calls to an internal dispatcher bypass model-return validation and are not,
  by themselves, reproductions of a participant-visible defect. Repeated copies of
  an error in the diary must not be counted as distinct rejected batches. Sixteen
  malformed-input cases exercised through `_invoke` were refused without a tool
  charge (inference remains billable). All 27 advertised tool schemas and 15 proposal
  shapes were retrievable. The initial claimed metering defect was withdrawn after
  reproduction through the real return validator; no production defect was confirmed.
- **Grok:** grounded finding validation and binding to its commission. Five focused
  tests passed. A numeric finding cannot cite only censored evidence, or references
  outside the supplied commission, on the production path. This does not establish
  that a model's explanation follows from the facts it cites. The first, broader
  Grok audit exceeded its provider's context limit; only the completed narrower
  review supports this conclusion.
- **GLM Flash:** governance, retirement, addressing, funded registration and artifact
  access. A 500-event scripted runtime exercised governance and retirement. Separate
  internal-path probes checked conserved chosen endowments, message delivery/read/ack,
  public cross-lineage artifact access and private-read refusal. Those probes do not
  prove that a model discovers the paths, or that a newborn buys an inference call.
  In-decision child requests are not participant births. `outcome.get` itself is free;
  processing its result consumes inference.
- **Sol:** existing coverage and a composed prompt-facing acceptance journey. The
  discovery/context baseline passed 39 tests, but did not require recovery after a
  rejected tool batch. Existing registration and feedback tests often invoke internal
  helpers, while existing world tests can assert receipt creation without requiring
  a subsequent participant to read and act on it.

## Composed acceptance and its limits

`tests/runtime/test_offline_capability_journey.py` deliberately omits a required
argument selected from the retrieved schema, then requires the exact rejection in
the next prompt and a body read before correction. A validated producer return
registers a tool; another lineage discovers and executes it in the OS jail. The
final assessment cites that execution receipt, settles the maker's decision and
reaches a later prompt which changes the scripted answer.

The policy, objective, actor selection and score are test-authored. This proves the
contract carries a correction, not that a real model discovers one. It uses ordinary
ticks with routing suppressed: the fixture selects the actors and does not test
organic routing or the full recursive judging population. Its two-tick consequence
horizon is shorter than the live run's and cannot establish live timing performance.
If the OS jail is unavailable, this gate fails before execution and settlement;
partial execution cannot pass as acceptance of the complete journey.

Verified on this host: `uv run pytest -m gate -n 2
tests/runtime/test_offline_capability_journey.py` completed **1 passed in 0.66s**,
with no skip. `uv run pytest` completed **2,224 passed in 32.56s** and
`uv run ruff check .` passed. Opus independently reviewed the journey assertions;
its reporting caveats are recorded above. No model-population behavior was measured
by these offline checks.

## What this means for the population evidence

The sealed run produced no orders. One participant repeatedly attempted prerequisite
market-data reads that failed argument validation; no order was submitted. It is
incorrect to attribute every hold to that same failure, or to claim a repaired read
will cause a profitable trade.

Producer rewards in realized mode come from the final consequence assessment, not
the initial verdict. Ordinary judges still have a separate optional forecast-calibration
path. Evidence-reference validation prevents some invalid scores; it cannot prove
semantic entailment. The grounded instruction repair constrains interpretation of
zero income and compute costs, but has not yet been validated in a live population.

Addressing and funded registration being callable is not evidence of collaboration
or autonomous objectives. A scripted policy demonstrates an affordance; a paid
population run tests whether real models use it. Neither alone establishes Class 3.
