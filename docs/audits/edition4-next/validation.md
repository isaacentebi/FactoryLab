# Final source validation

Release: `a4e022622894d9064fdc26e840c08be52344202b4569be24a2f52d8eec13bd2a`.
Opus implemented the changes; the lead reviewed the diffs, required corrections
to the diagnostic claims and schema, and ran the consolidated checks.

`uv run ruff check .`:

```
All checks passed!
```

`uv run pytest`:

```
============================ 2114 passed in 28.75s =============================
```

Relevant gate command:

```sh
uv run pytest -m gate -n 2 tests/runtime/test_discovery_continuation.py tests/runtime/test_world_read.py tests/runtime/test_child_requests.py tests/runtime/test_connectors.py tests/runtime/test_grounded_feedback.py tests/runtime/test_grounded_evidence_schema.py tests/runtime/test_grounded_evidence_snapshot.py tests/runtime/test_nonfinancial_consequence_gate.py tests/scripts/test_edition4_nonfinancial_probe.py tests/scripts/test_edition4_nonfinancial_model_probe.py tests/audit/test_r3_p_prompt.py tests/audit/test_e3_world.py
```

Gate output:

```
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
plugins: xdist-3.8.0, anyio-4.15.1
created: 2/2 workers
2 workers [20 items]

....................                                                     [100%]
============================== 20 passed in 8.16s ==============================
```

Production files changed:

- `factorylab/cortex/schematics.py`: callable catalogue bootstrap example.
- `factorylab/runtime/compute.py`: bounded discovery continuation.
- `factorylab/runtime/feedback.py`: frozen evidence collection metadata.
- `factorylab/runtime/loop.py`: explicit citation choices and snapshot rendering.

Diagnostic files added:

- `scripts/edition4_discovery_probe.py`.
- `scripts/edition4_nonfinancial_probe.py`.
- `scripts/edition4_nonfinancial_model_probe.py`.
- `tests/runtime/test_discovery_continuation.py`.
- `tests/runtime/test_nonfinancial_consequence_gate.py`.
- `tests/runtime/test_grounded_evidence_schema.py`.
- `tests/runtime/test_grounded_evidence_snapshot.py`.
- `tests/scripts/test_edition4_discovery_probe.py`.
- `tests/scripts/test_edition4_nonfinancial_probe.py`.
- `tests/scripts/test_edition4_nonfinancial_model_probe.py`.

Documentation/evidence: this directory and `../edition4/budget.json`.
The unrelated `.claude/` directory was left untouched. No commits were made.

Implementation decisions: retain existing continuation argument privacy and
client identity naming; preserve duplicate valid citations; report old snapshots
as unknown rather than assigning current time; use actual runtime receipts and
settlement in the offline gate; explicitly label forced actor selection, fixture
claims and scripted judgments. No normative score, role ratio, model reasoning
setting, economic objective or kernel invariant was changed.
