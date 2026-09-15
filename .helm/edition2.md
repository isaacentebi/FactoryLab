# Helm: edition 2
Goal: build edition 2 per docs/plans/edition2.md (contracts C1–C13) and leave main ready for the funded manifest.
Completion check: `uv run pytest` green on main after W1–W7 merged; `tests/audit/test_edition2_slice.py` passes; a testnet rehearsal report under docs/audits/v5/ with achieved tick gaps; charter edition 2 ratified with hashes in docs/charter/.
Out of scope: mainnet, fund transfers, a manifest named `funded`, publishing the website, reading any *.key.
## Workstreams
- [x] 1. W1 endowment/dormancy/rent — implementer: fable — acceptance: tests/kernel + tests/runtime touched files green; release/dormant ledgered; reserve opens on unlocked
- [x] 2. W2 hard casts — implementer: fable — acceptance: resume refuses release mismatch; Venice confirm on debit evidence; witness script
- [x] 3. W3 grading + challenge — implementer: fable — acceptance: GPT-6 reproductions inverted (F3/F4/F5/F6/F7 tests); challenge trial ledgered and adoptable
- [x] 4. W4 service seller — implementer: fable — acceptance: paid call verified, ledgered income.earned, pots split three ways
- [x] 5. W5 programs + archive — implementer: fable — acceptance: program seat registered, routed, judged, resumed; artifact survives retirement
- [ ] 6. W6 per-seat entitlement — implementer: fable — acceptance: invariant holds across release, debit, credit, child, retirement, resume
- [ ] 7. W7 charter/manifests/calibration/slice — implementer: fable + advisor — acceptance: ratification hashes; slice test; rehearsal report
## Log
- 2026-09-14 plan written; wave 1 dispatched (W1–W5) in worktrees.
- W1 #69 reviewed, gate 2745/10 → pins restated, green. W2 #70, W3 #71, W4 #73, W5 #74, W7-calibration #72 reviewed and merged onto e2/integration with hand-resolved conflicts (bootstrap imports, worlds fields, wake handlers, schematics string, README). Targeted e2 set 143 passed. Full gate running on e2/integration. W6 still building.
- Follow-up for integration pass: challenge ballots should show voters the evidence and both trial series (W3 caveat); x402 income must credit the seat entitlement once W6 lands (W4 caveat); witness.sh dormant needs a caller (W2 caveat).
- Second integration gate 2868 passed. main fast-forwarded to 58b9223 (W1–W5 + calibration). PRs 69–74 merged. W6 #75 open: rebasing onto integration with symmetric consequence debit (floor 0), release hook, x402 income → seat credit. W8 integration pass dispatched (challenge ballot inputs, witness dormant, docs/manifest.md, charter-explained rewrite).
- Remaining after W6+W8: wave 3 = trial_amount/base_share/release schedule sizing, funded-template manifest (one-hour window, endowment), charter re-ratification on testnet (last, after all prompt text is final), twelve-step vertical slice test, testnet rehearsal at 600 s, GPT-6 second-reading zip.
