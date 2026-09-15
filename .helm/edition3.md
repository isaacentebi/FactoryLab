# Helm: edition 3
Goal: build edition 3 per docs/plans/edition3.md (C1–C5) and leave main ready for calibration, ratification and a 24-hour testnet rehearsal.
Completion check: `uv run pytest` green on main after W1–W5 merged; slice test extended with C1/C2 steps passes; `worlds/edition3-testnet.toml` preflights; a 24 h testnet rehearsal report under docs/audits/v6/ shows a seat's state and an addressed outcome surviving three returns and a restore.
Out of scope: project funding contracts and commissioned judges (deferred, plan header); mainnet; fund transfers; a manifest named `funded`; reading any *.key.
## Workstreams
- [x] 1. W1 continuity (state + inbox + artifact scoping) — implementer: opus — acceptance: C1 test
- [x] 2. W2 thinking control (coalesced update, subscriptions, defer, watchers, vocabulary) — implementer: opus — acceptance: C2 test
- [x] 3. W3 charter (norm definitions, one card, privileged predicate removed, fidelity objection) — implementer: opus — acceptance: C3 test, historical hashes hold
- [x] 4. W4 world block (self, resources, continuity, directory, compact catalogue, toll removed) — implementer: opus — acceptance: golden block reconciles with ledger
- [x] 5. W5 first world (roster, seeds, $300/$120 schedule, kill wind-down, calibration cases) — implementer: opus — acceptance: manifest pinned, wind-down test
## Log
- 2026-09-15 15:00 plan written from GPT-6's architect reading (docs/audits/v6/gpt6/); user locked the five contracts and "when we kill we liquidate open positions"; five Opus agents dispatched in worktrees.
- W5 #90 merged (roster, seeds, $300/$120, kill wind-down, 45 calibration cases; manifest 805ada83…). Integration items: loop.py:486 insolvency death must call self.kill (one line, after W1/W2 merge); preflight needs edition 3 ratification on the new roster.
- W2 #92 merged (coalesced WorldUpdate, subscriptions/defer/cadence floor, watchers at program price, quiet tick, action vocabulary; also fixed an _open_epoch KeyError when a universe swaps an action).
- W1 #91 merged (working state, outcome inbox, artifact scoping, memory deque removed). Coordinator: loop.py insolvency death now goes through Runtime.kill (wind-down). Open: W3 charter, W4 world block; then full gate, extended slice, ratification on the edition 3 roster, calibration (paid), 24 h rehearsal.
- W3 #94 merged (norm definitions, one card on avoidably_unresolved_share, privileged predicate removed, fidelity objection, learning-death access reasons). Event-kinds pin extended for WorldUpdate/WatcherFired. Draft digest c25f5617…, roster 488c62ef… await ratification after W4.
- W4 #93 merged (YOU block: self/resources/world/continuity/accounting facts; directory tools; toll removed; compact catalogue: prompt 69,362 → 59,826 bytes). Gate before W4: 3049 passed. Final gate running.
