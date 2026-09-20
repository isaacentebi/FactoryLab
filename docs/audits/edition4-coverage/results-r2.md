# R2: provider substitution completed 60 ticks

The user authorized resolving the terminal exposure and provider-credit blockers.
A separately journaled reduce-only recovery closed R1's remaining testnet BTC
short before R2; account and open-order reads were flat. No mainnet, top-ups,
transfers, or access to a living diary occurred.

## Fixed protocol and source

Frozen code: 284c8c2f1fec1759a060255f5255b90435a397d3, including PR113's
confirmed-unbilled rejection classification. Target: 60 ticks, independent
60-minute deadline, $3 cap, 300 calls. Compact context, realized feedback,
addressing enabled, reasoning preserved, no observer. No settings changed live.

Venice had only $0.098171 left; OpenRouter had $76.782606. Five seed seats were
routed from Venice GLM 5.3 Flash to OpenRouter's `z-ai/glm-5.3-flash`, with low
reasoning and output allowances preserved. The unused Qwen model-menu route also
moved to OpenRouter. Norms, charter content, other assembly fields and endowments
were preserved. This is a provider-substitution arm, not a matched repeat.

Base roster digest: `81de4911c5ba27eeeca65435cba461c31862bcc2a3f29c16ebb5e75c1005f434`.
Arm roster digest: `9dbee18d502253e0908c81763c5a9cbe681b380578e6f1a2a5ae10a3628ed19a`.
Arm manifest digest: `60140b08d9cd657ff3a2ba0aeed70ab95048cd9c56c567a9dec34c8e50bd8d94`.
The runner's preservation flags compare this new base with its effective manifest;
they do not imply preservation relative to R1. Exact preflight and route deltas
are retained privately in `work/coverage-60-r2/preflight.json`.

## Terminal result

- All 60 ticks delivered; measured mean gap 19.34 seconds, despite a declared
  ten-second interval. There were 663 events and 338 sampled decisions.
- 154 provider calls, all bills known: 414,816 micro-USD ($0.414816). No unknown
  bills, overruns, cap refusals or provider-fault events occurred.
- Ledger integrity and wallet conservation passed.
- Venue before and after: no positions, no open orders; equity unchanged at
  965.14998841 testnet USD. No orders were submitted during this run.
- Nine assessed grounded findings: eight supported, one contrary. Eleven further
  findings were unknown, with thirteen grounded decisions still outstanding.
- The predeclared behavioral screen remains **inconclusive**: the ten-assessed-
  sample threshold was missed by one. Tick coverage, contrary evidence and
  authoritative billing thresholds passed. No run extension or threshold change
  was made to obtain a pass.

The generic termination label is `explicit_kill:budget`, but admission stop reason
is null and neither spend nor call limit was exhausted: this run reached its
60-tick endpoint. The summary's `positions_closed_at_exit: false` is not evidence
of residual exposure; fresh venue snapshots are flat and no positions were opened.

## Observed actions and feedback

There were 143 completed invocations: 140 valid and three malformed. The 64 unique
producer decisions comprise 34 holds, 21 defers, seven classified investigations
and two malformed returns. Re-emitted contracts are excluded from these counts.
The third malformed invocation was a judge. These are descriptive counts, not
proof of a behavioral effect from the route substitution.

Twenty-one tools executed successfully: seventeen `outcome.get` and four
`outcome.list`. No order, message, registration, participant birth, proposal or
amendment was observed. No independent income bought further operation.

Three malformed-answer rejections were addressed to their owners, and each had a
later valid return. None had an acknowledgement. The two malformed producers
attempted more than four tool calls; their batches were rejected atomically.
Twenty final findings were published and addressed. The report observes no
acknowledged final finding, which by itself does not prove that a finding was
unread; exact retrievals must be checked separately before inferring exposure.

## Context and interpretation

Final invocation input-token medians/maxima were: producer 13,983/17,904;
evaluator 15,166/17,547; meta 5,298.5/5,604. Retrieval is functional, but context
remains substantial. R1's separate section analysis identified stable preamble
and current world payload as the largest baseline contributors. No prompt change
was mixed into this run.

This establishes that the fixed harness can complete a bounded 60-tick population
run with affordable, fully billed inference and a flat terminal venue. It does
not establish cooperation, autonomous objective formation, useful production,
or consequence-driven learning. More capital alone is not supported as the next
remedy by this result. Provider-route differences prevent a causal comparison
with R1's behavior or output reliability.

## Accounting and evidence

Cumulative known experimental inference: $5.347499. Prior uncertain bills remain
reserved at $0.030564. Remaining unreserved authorization: $44.621937 of $50.
There is no active rehearsal reservation after this run.

Private evidence: `work/coverage-60-r2/live/report.json`, `events.json`,
`postmortem/report.json`, and `sanitized-summary.json`. These were read only after
confirmed termination. Raw diaries and participant prose are not published.
The exact runtime used for R2 was already verified by PR113: Ruff, 2,191 check tests and the affected rehearsal gate.
The arm's manifest and effective manifest were validated offline before dispatch.

## Exact rejection and exposure follow-up

All nine rejected tool sections exceeded the four-call batch limit: opportunity
five times, mechanism twice, constructor and empirical once each. Seven retained
a valid investigation action while losing the entire oversized tool section;
two were fully malformed. Thus every one of the seven valid investigation answers
lost its requested batch. Eight affected handles executed no tools; one malformed
handle had separately executed an `outcome.list` during continuation. The 55 valid
hold/defer choices were actual declared choices, not merely stripped tool batches.

Mechanism later explicitly fetched both rejection records (`outcome:68` and
`outcome:87`), then made bounded individual lookups without another oversized
batch. Opportunity repeated the fault five times without explicitly fetching its
rejection records. These are observed sequences, not causal proof of learning.

Four of twenty final findings were explicitly retrieved despite zero
acknowledgements: `outcome:26` (empirical decision-33, unknown), `outcome:37`
(mechanism decision-45, supported), `outcome:104` (mechanism decision-156,
unknown), and `outcome:106` (mechanism decision-160, unknown). The report's
acknowledgement-based exposure field therefore understates direct retrieval;
zero acknowledgements must not be summarized as zero exposure.

The eleven unknown findings concerned five holds, four defers and two stripped
investigations. Observed inference cost, zero realized income and no liquidation
did not determine whether restraint was useful or merely idle, nor establish
avoided losses or information value. For the two investigations, requested tools
had not executed. Eight separate consequence-unknown events were censored cases
without assessable evidence at the frozen horizon. Longer runs alone cannot make
those absent counterfactuals observable.

The batch limit was not missing: the frozen request builder emitted
`tool_calls.maxItems = 4`, and compact mechanics also exposed the limit. Raw
provider request bodies were not retained; this conclusion uses the frozen source,
manifest and deterministic schema builder rather than claiming a byte-for-byte
payload capture. A follow-up wording change states the actual schema-derived cap
beside the response schema. It changes salience only; schema validation, atomic
rejection, costs and retry policy remain intact. R2 predates that wording change,
so improved compliance is a hypothesis, not a demonstrated result.

Post-run prompt clarification validation: Ruff passed; 2,191 check tests passed
in 32.73 seconds; three affected world gates passed in 3.27 seconds. The initial
prompt/context-only gate selection contained no gate-tier cases, so the relevant
return-section, grounded-feedback and rehearsal world files were selected instead.
The existing prompt test now checks the configured cap and avoids claiming one
common cap when a schema alternative is uncapped. No extra test file was added.
