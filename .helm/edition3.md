# Helm: edition 3
Goal: build edition 3 per docs/plans/edition3.md (C1–C5) and leave main ready for calibration, ratification and a 24-hour testnet rehearsal.
Completion check: `uv run pytest` green on main after W1–W5 merged; slice test extended with C1/C2 steps passes; `worlds/edition3-testnet.toml` preflights; a 24 h testnet rehearsal report under docs/audits/v6/ shows a seat's state and an addressed outcome surviving three returns and a restore.
Out of scope: project funding contracts and commissioned judges (deferred, plan header); mainnet; fund transfers; a manifest named `funded`; reading any *.key.
## Workstreams
- [ ] 1. W1 continuity (state + inbox + artifact scoping) — implementer: opus — acceptance: C1 test
- [ ] 2. W2 thinking control (coalesced update, subscriptions, defer, watchers, vocabulary) — implementer: opus — acceptance: C2 test
- [ ] 3. W3 charter (norm definitions, one card, privileged predicate removed, fidelity objection) — implementer: opus — acceptance: C3 test, historical hashes hold
- [ ] 4. W4 world block (self, resources, continuity, directory, compact catalogue, toll removed) — implementer: opus — acceptance: golden block reconciles with ledger
- [ ] 5. W5 first world (roster, seeds, $300/$120 schedule, kill wind-down, calibration cases) — implementer: opus — acceptance: manifest pinned, wind-down test
## Log
- 2026-09-15 15:00 plan written from GPT-6's architect reading (docs/audits/v6/gpt6/); user locked the five contracts and "when we kill we liquidate open positions"; five Opus agents dispatched in worktrees.
