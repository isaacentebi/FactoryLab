# Edition 4 context rendering benchmark

This is a deterministic, offline measurement of request rendering. It constructs the
nine-seat `edition3-rehearsal-5` world with `events=0`, `FakeExchange`, and
`ScriptedProvider`. It performs no model call, venue call, treasury operation, or paid run.

The fixture gives `mechanism` a 30-item outcome queue. The real inbox carries its oldest
eight entries and reports the remaining count. Each body has a long retained rationale plus
deterministic `needed_claim_hash` and `evidence_hash` values. Two requests use the same
world, structural schema, outcome queue, and either a 2 KiB or 32 KiB state written through
`WorkingState.put` and read through `WorkingState.render`. Prompt mode is `compact` on both
sources. Explanatory `description` strings are removed from the schema before rendering so
an unrelated wording change cannot be counted as context savings; its structure is hashed
and must match.

Run a source tree explicitly; the helper inserts that tree at `sys.path[0]` before any
FactoryLab import and refuses a shadowed import:

```bash
UV_PROJECT_ENVIRONMENT=/Users/isaacentebi/Desktop/FactoryLab/.venv \
  uv run --no-sync python docs/audits/edition4-context/benchmark.py \
  --source /tmp/factorylab-feedback-repair-20260920 \
  --output docs/audits/edition4-context/baseline.json
```

After the implementation is ready, render the working tree to a temporary JSON and compare:

```bash
uv run python docs/audits/edition4-context/benchmark.py \
  --source /Users/isaacentebi/Desktop/FactoryLab --output /tmp/context-candidate.json
uv run python docs/audits/edition4-context/benchmark.py \
  --compare docs/audits/edition4-context/baseline.json /tmp/context-candidate.json \
  --output docs/audits/edition4-context/comparison.json
```

Comparison refuses different fixture or schema hashes. Discoverability records whether all
eight inline outcome IDs, state hash, state size, and unread count remain present. Exact
recoverability separately reads all 30 stored outcome bodies and verifies every claim and
evidence hash, then checks the working-state artifact against the bytes originally written.

Measured against `/tmp/factorylab-feedback-repair-20260920`:

| Scenario | Baseline | Candidate | Saved | Stable prefix | Inbox contribution | State contribution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 KiB state | 60,230 B | 41,520 B | 18,710 B (31.06%) | 25,425 -> 16,497 B | 14,622 -> 4,820 B | 2,173 -> 2,193 B |
| 32 KiB state | 90,949 B | 39,779 B | 51,170 B (56.26%) | 25,425 -> 16,497 B | 14,622 -> 4,820 B | 32,894 -> 454 B |

All eight inline IDs remain discoverable. All 30 outcome bodies, all exact
`needed_claim_hash` and `evidence_hash` values, and both stored working states recover
exactly. The hashes leave the inline index and remain in the exact body returned by
`outcome.get`; the large state leaves the inline prompt and remains in the artifact named by
its SHA. These byte savings are rendering evidence only, not evidence of better decisions or
behavior.

## Implementation and verification

The implementation borrows on-demand context and bounded memory patterns, using the
existing stores and tool contracts. It adds no dependency, database, background task,
or population objective. Ordinary requests retain their norms, task, budget and
small working state. Large state and outcome bodies remain exactly recoverable.

A same-decision retrieval loop can discover a contract, page an inbox, read a body,
and act within the existing ceiling. Intermediate results use an invocation-local
hash map, reachable through `artifact.get` only inside that invocation. Those
references explicitly expire at return; they never enter the durable artifact index.
The current tool result appears once. Final-answer reservation uses the current
model ceiling; actual metering still refuses unaffordable calls. Writes end retrieval,
unknown call prices do not extend it, and a five-round backstop bounds free readers.

Grounded evaluation and meta-review retain operational access without preloading
current charter/world facts, mutable working memory, inbox text, or population-written
tool descriptions. Their preserved norms and evidence remain the judging contract.
`world.read` is available in both modes so a grounded reference-mode review can also
recover contracts omitted from its initial context.

Changed implementation files in this iteration:

- `factorylab/cortex/request.py`
- `factorylab/cortex/schematics.py`
- `factorylab/runtime/bootstrap.py`
- `factorylab/runtime/compute.py`
- `factorylab/runtime/continuity.py`
- `factorylab/runtime/loop.py`

Changed checks: `tests/cortex/test_prompt_modes.py`,
`tests/runtime/test_continuity.py`, `test_context_retrieval.py`,
`test_discovery_continuation.py`, `test_grounded_feedback.py`,
`test_grounded_multi_router.py`, `test_world_read.py`, `test_child_requests.py`,
`test_connectors.py`, and `test_grounded_evidence_schema.py`.
Documentation: this directory, `docs/manifest.md`, and
`docs/build-spec-v0.8-round2.md`. Earlier uncommitted repairs were preserved.

Validation on the integrated working tree:

```text
uv run ruff check .
All checks passed!
uv run pytest
============================ 2186 passed in 33.40s =============================
```

Affected gate command:

```bash
uv run pytest -m gate -n 2 tests/runtime/test_compute_continuity.py tests/runtime/test_grounded_feedback.py tests/runtime/test_grounded_multi_router.py tests/runtime/test_discovery_continuation.py tests/runtime/test_context_retrieval.py tests/runtime/test_continuity.py tests/runtime/test_nonfinancial_consequence_gate.py tests/cortex/test_return_sections.py tests/scripts/test_edition4_nonfinancial_model_probe.py tests/scripts/test_edition4_report.py tests/runtime/test_child_requests.py tests/runtime/test_connectors.py tests/runtime/test_entitlement.py tests/runtime/test_resume.py
```

```text
============================== 21 passed in 9.22s ==============================
```

The scripted retrieval test reaches a later inbox item, uses its exact content in a
notebook write, and verifies total cost against the wallet delta. Another changes the
source after reading and recovers the original bytes through the temporary reference,
then verifies that reference is absent from persistent storage after return. Other
checks cover other-seat refusal, bounded hostile index fields, small-budget completion,
and outside-text restrictions on both financial and notebook writes.

These offline measurements establish functioning interfaces and smaller rendered
requests; they do not establish cheaper useful decisions, collaboration, income,
or Class 3 emergence. The next behavioral comparison must measure total inference
spend per completed useful decision, including retrieval calls, rather than initial
prompt size alone.

Release follow-up: [bounded testnet validation](live-smoke.md). PR review also repaired
result-size budget overruns and false delivery of omitted inbox entries. Model and
program calls reserve an affordable final-answer scaffold before tool/child spending.
Oversized unaffordable bodies remain unloaded; they do not confer acknowledgement
rights. An unaffordable continuation is a mechanical failure, not a voluntary decline.

Only the routed root call may use a routing-estimate bridge. Retrieval and child
calls spend the liable seat's own remaining cover; they cannot repeatedly draw
from the commons. Regression checks cover direct and chained bridge refusal.

Sparse outcome delivery is checkpointed separately. Acknowledgement advances only
through the contiguous delivered prefix for that seat; fetching a later item cannot
hide earlier unseen feedback, even across recovery or interleaved global IDs.
