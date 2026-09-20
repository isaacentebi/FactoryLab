# Bounded working context

## Change and prediction, before dispatch

PR115 retained exact retrieval routes and accepted intermediate private notes,
but its corrected commissioned screen completed only one of four candidate
chains. The six-fact task still forced a model to copy evidence into notes or
oscillate between batches. This repair keeps up to 16 KiB of canonical older
result entries alongside the latest batch. Older and oversized entries remain
exactly retrievable through invocation-private references. The whole request is
quoted; unaffordable bodies become references. No new institution, durable
memory store, reward, model, or subsidy is introduced.

The response contract also explicitly says to submit one JSON object, end the
turn after requesting tools, and await actual results. It does not execute
simulated future turns or fabricated receipts.

Offline proof must derive its answers only from each current prompt, without
writing working_state: visibility should be 0 -> 4 -> 6 facts, and both models'
scripted task paths should finish. A forced-eviction regression must recover the
original exact body after the source changes. Quote fallback must preserve the
same decision ceiling.

Run the existing four-case investigation screen once per arm with unchanged
runner, tasks, models, reasoning, output allowances and fake/denied action rails.
Baseline is merged PR115 (8e57719); candidate changes the working context and turn
instruction together, so this is a repair-bundle screen, not an attribution of
individual effects. Freeze actual dispatch requests before paid calls. Reserve
$0.48 per arm inside the existing cumulative $50 authorization. Stop on any
uncertain bill; never retry an unchanged arm for a favorable answer.

Prediction: six-fact completion should improve because the final prompt can hold
all six facts without model-authored memory. Calculation should not regress.
All four candidate cases must succeed before a population pilot; any failure is
reported and traced against the actual prompt and receipts. This screen measures
commissioned tool use, not cooperation, autonomous objectives or Class 3.

## Verification

Ruff passed. The check tier passed 2,212 tests in 35.36 seconds. The three
relevant gate tests passed in 3.40 seconds. Independent review found no actionable
issues in billing fallback, invocation privacy, exact recovery or frozen inputs.

The prompt-only replay completed all four cases in twelve scripted calls, with
no state writes. The scripted bill of 1,200 micro-USD is fixture accounting, not
paid inference. Its retained source is
`work/tool-turn-working-context/prompt_only_canned.py`, SHA-256 `fbac1462a57589fffd97a7e98fa75cd3a8604b62e36936706736128a127f0a9e`.
Its report is `work/tool-turn-working-context/prompt-only-canned-report.json`,
SHA-256 `3cce1d33579ef15d0107ce59e298a7a77cc5382d952f7fba8aca1f457f5e5702`.

## First real-model result

Baseline completed 2/4 chains in 8 calls, costing $0.016655. Candidate c831600
completed 3/4 in 9 calls, costing $0.017898. Both calculation tasks succeeded;
Luna's six-fact task changed from malformed to exact success. GLM's six-fact
request failed before executing any tool in both arms. All bills are known.

The candidate GLM reply contained four valid outcome.get requests and an empty
`facts` array beside them. The partial answer validator skipped missing required
fields, but still enforced the final array's minimum length, so it discarded the
valid investigation. This is an observed contract problem, not evidence that the
participant declined. The six exact facts were available to Luna's final prompt
and it used them; no private working_state was necessary.

## Correct the continuation boundary, before the next dispatch

Keep reserved action and effect validation strict. An unfinished task-specific
answer field must not prevent a valid tool continuation: omit its invalid value
from that intermediate answer and record the omission. The eventual final
answer still must satisfy the entire schema. A malformed tool batch must not
be rescued or partially executed, nor may this relaxation hide an invalid trade,
verdict, declared event kind or other reserved field.

After an offline replay of the exact observed GLM reply, run the revised candidate
once on the same four tasks with a $0.48 cap. Compare descriptively with the
already-run baseline; do not rerun an unchanged baseline. Prediction: the valid
four-read request executes, later two reads remain visible with the first four,
and final completion improves. Report every attempt, including failures; the
same 4/4 progression requirement applies. This amendment follows a new source
change and is not an unchanged paid retry.
