# PR 108 review disposition

The initial published head was `b458f6e`. Cursor Bugbot and Codex Review both
reviewed that head. Independent local reviews used Grok and Sol; Sol implemented
bounded fixes and the lead inspected and integrated them. This record describes
the resulting revision, not a new live experiment.

## Accepted and repaired

- Cursor 4052289350: grounded final commissions omit live charter, predicates,
  forecast example, world snapshot and current consequence standing. Frozen norms,
  the claim and supplied observations remain. A captured-request regression checks
  the actual prompt. This does not claim perfect blinding: the seat retains its
  own continuity state and unread outcomes.
- Cursor 4052289357: `evaluation.grounded_horizon_ticks` is a separate positive
  integer setting. Ordinary forecasts still count internal events. Default manifest
  identity is preserved; tests vary both settings independently.
- Codex 4052298080: removed evaluator instructions spelling out evidence acceptance
  rules. The requested judgment and output schema remain; acceptance stays in code.
- Codex 4052298084 and local review: redact root-level address returns and delegated
  address-shaped inputs, including nested bare recipient/body mappings. Ordinary
  task text remains available. A child-evaluation event regression checks delivery.
  A child request's description is public task documentation.
- Codex 4052298086: founder endowment transfer depends on free founder entitlement,
  not spendable wallet cash. A fully reserved novelty-wallet regression checks the
  exact transfer and conservation.
- Codex 4052298088: new rehearsal reports express mean micro-USD as an exact rational
  string, or null when no bill is known. Historic evidence files are unchanged.
- Local Grok review: assembly learners now wait through a wall timeout while a
  grounded contract remains pending, matching router behavior. Supported/contrary
  outcomes train once, including after restore; unknown discards without imputation.

## Findings not adopted

- Cursor 4052289347: a NOOP final-router draw remains voluntary nonparticipation.
  The commission is bounded and closes unknown; the kernel does not resample until
  it purchases a judge. A regression proves no invocation and eventual closure.
  The limited retry for an actual unusable finding remains a different case.
- Grok proposed excluding all marked economic outcomes from observed evidence.
  Not adopted: a mark is an observed valuation, although it is not cash realized.
  Evidence explicitly distinguishes `marked` from `cash_realized`. Whether it bears
  out a claim belongs to the independent interpretation against that frozen claim;
  universal exclusion would also reject claims specifically about marked value.
  No automatic profit/usefulness reward follows from evidence eligibility.

## Integrated verification

After all review fixes, the lead ran:

```text
uv run ruff check .
All checks passed!

uv run pytest
2025 passed in 21.85s

uv run pytest -m gate -n 2 tests/runtime/test_grounded_feedback.py tests/runtime/test_address_integration.py tests/runtime/test_tool_use_evidence.py tests/audit/test_edition4_rehearsal.py tests/scripts/test_edition4_report.py tests/scripts/test_edition4_observer.py tests/runtime/test_loop.py tests/audit/test_r2a_lifecycle.py tests/audit/test_r3d_evaluation.py
23 passed in 13.93s
```

`git diff --check` passed. The full gate tier was not run. No new paid calls,
venue actions or deployments accompanied this revision. Earlier paid probes and
rehearsals retain their recorded source identity; they do not validate this new
prompt or clock split live. Fresh corrected-C and nonfinancial tests remain gates.
