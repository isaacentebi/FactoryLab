FactoryLab 4618f6f: offline cold-audit review package

Contents
- tests/audit/test_cold_4618f6f.py: 33 snapshot characterizations and controls.
- tests/audit/test_candidate_repairs_4618f6f.py: 11 in-memory candidate checks.
- edition3-review.patch: unified production diff, NOT APPLIED.
- edition3-audit-evidence.json: export counts, cost reconciliation, and source integrity.
- edition3-audit-test-results.txt: final local test result.

Run from the original repository root with its dependencies available:
  PYTHONDONTWRITEBYTECODE=1 python -m pytest -o addopts='' --noconftest \
    tests/audit/test_cold_4618f6f.py \
    tests/audit/test_candidate_repairs_4618f6f.py -q

Local result: 44 passed in 1.22s. All 549 original archive files remain byte-identical.
The characterization tests intentionally assert the snapshot's defects; passing does not
mean the system is safe. They are pinned to 4618f6f. After applying reviewed repairs,
convert the selected fixed expectations into permanent regression tests.

The candidate module constructs changed source strings in memory. Its unified_patch()
function reproduces the attached patch. It never writes production source. The patch
passed git apply --check against the upload but was never applied. Check source identity
and review each hunk before application. A changed release requires a new world identity.

Scope limitations
- No mainnet, no fund movement, no model/provider or venue calls, and no *.key reads.
- No authenticated replay of rehearsal 3: its full sealed diary is not in this package.
- Rehearsal analysis uses exported evidence, including 517 returned-answer records.
- Several optional SDKs and pytest-xdist were unavailable. Full integration tests did
  not run. Selected SDK-dependent functions were tested unchanged from their AST with
  explicit doubles; those checks are unit characterizations, not integration coverage.
- The patch is NOT a typed-treasury migration, durable wind-down executor, independently
  enforced death witness, or complete replacement of the evaluation architecture.
- In particular, legacy handle-based acknowledgement remains backward-compatible;
  exact outcome IDs must be used, and stricter acknowledgement semantics need review.
- Income receipt deduplication does not independently verify chain settlement. It is
  compatible with the current one-reference-per-payment protocol, not a general model
  for multiple transfer logs in one transaction. No old receipt-history migration exists.
- Private artifact directory entries still use a first-owner billing model. The patch
  adds read grants; it does not redesign ownership or lifecycle garbage collection.

This package is a review input, not authorization to launch or move funds.
