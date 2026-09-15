# Helm: edition 2
Goal: build edition 2 per docs/plans/edition2.md (contracts C1–C13) and leave main ready for the funded manifest.
Completion check: `uv run pytest` green on main after W1–W7 merged; `tests/audit/test_edition2_slice.py` passes; a testnet rehearsal report under docs/audits/v5/ with achieved tick gaps; charter edition 2 ratified with hashes in docs/charter/.
Out of scope: mainnet, fund transfers, a manifest named `funded`, publishing the website, reading any *.key.
## Workstreams
- [ ] 1. W1 endowment/dormancy/rent — implementer: fable — acceptance: tests/kernel + tests/runtime touched files green; release/dormant ledgered; reserve opens on unlocked
- [ ] 2. W2 hard casts — implementer: fable — acceptance: resume refuses release mismatch; Venice confirm on debit evidence; witness script
- [ ] 3. W3 grading + challenge — implementer: fable — acceptance: GPT-6 reproductions inverted (F3/F4/F5/F6/F7 tests); challenge trial ledgered and adoptable
- [ ] 4. W4 service seller — implementer: fable — acceptance: paid call verified, ledgered income.earned, pots split three ways
- [ ] 5. W5 programs + archive — implementer: fable — acceptance: program seat registered, routed, judged, resumed; artifact survives retirement
- [ ] 6. W6 per-seat entitlement — implementer: fable — acceptance: invariant holds across release, debit, credit, child, retirement, resume
- [ ] 7. W7 charter/manifests/calibration/slice — implementer: fable + advisor — acceptance: ratification hashes; slice test; rehearsal report
## Log
- 2026-09-14 plan written; wave 1 dispatched (W1–W5) in worktrees.
