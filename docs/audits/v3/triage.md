# Round three triage (draft, seats 1/2/3/4/5 in; 6 pending)

Legend: B blocker, S serious, M minor. Seats: 1 class3-fable, 2 pathologies-codex, 3 defects-fable, 4 defects-codex, 5 wiring-a, 6 wiring-b.
Decision: fix | design (needs Isaac) | known | doc.

| # | Finding | Seats | Sev | Label | Verified on main | Decision |
|---|---|---|---|---|---|---|
| T1 | Same handle opening and closing its own lot is credited twice (lots.py opener net + closer net) | 2,4 | B | broken | yes, lots.py:191-211 | fix |
| T2 | A policy ballot invocation can issue a venue write (return_kinds recorded, _may_write only checks policy when mapping absent) | 4 | B | broken | yes, compute.py:429-435 | fix |
| T3 | Registered observations cannot be named by a card: CharterBook.validate preflights without the observation book | 2,4,5 | B | broken | yes, book.py:72 | fix |
| T4 | testnet.toml cannot construct: spot_pairs not on testnet (only PURR/USDC) and placeholder reserve_address refused by LiveRail; CLI hides reason as adapter_unavailable | 5 | B | broken | yes, testnet.toml:202,208 | fix (manifest + validate at load + name the reason) |
| T5 | Live class transfer never confirms: class_poll compares venue execution time to request nonce; treasury jams for life | 5 | B | broken | code read; live repro by seat 5 | fix |
| T6 | Governance activation unreachable in shipped demo (earliest 600 vs README 500); in testnet first activations at 10h/20h world time | 5 | B | broken | yes, cadence.py:98-101, README:35 | fix (README events >1200 or lower backstop in scripted manifests; publish schedule) + design: testnet backstop |
| T7 | Population cannot name assemblies: ids never disclosed, no self id in requests; retire/learner/requests keyed by id | 1 | S | broken | seat 1 probe | fix (contract catalogue with ids; inputs.you) |
| T8 | No kill: no CLI kill, service Restart=always, seal released only at balance zero | 1,3 | S | broken | cli/service read; test | design (Isaac: $0 kill switch vs kill command) |
| T9 | testnet runs on seed charter (no [charter]); validate should refuse launch without explicit charter | 1 | S | not C3 | yes | known (edition 1 re-draft pending) + fix: validate refuses mainnet without [charter] |
| T10 | Connector preflight demands 2xx at "/" so data APIs (Kraken, CoinGecko, Coinbase, Binance) are refused | 1 | S | broken | seat 1 receipts | fix (preflight on answered-within-bounds or proposal preflight_path) |
| T11 | Metas never committee-eligible: terminal meta consequences and conformity settlements not counted | 1,4 | S | broken | yes | fix |
| T12 | Evaluator/meta cost cards allocate zero penalty: _decision_share hardcodes producer | 2,4 | S | broken | yes | fix |
| T13 | Immune organ diagnoses raw reserve-window values, pricing uses typed card samples | 2,4 | S | broken | yes | fix |
| T14 | Edition 1 verdict_mean card (forecasts, per producer assembly) never measures: verdict rows belong to evaluators; preflight hides it | 2 | S | broken | to verify | fix (select by subject) + re-draft |
| T15 | Tool trades labelled hold for propensity (final JSON only); declared tiny action mass explodes EXP3 update | 2 | S | broken | to verify | fix (bind action label to executed tools; floor/verify declared mass) |
| T16 | Verdict/payoff split: evaluator verdict=1,payoff=0 on lazy producer scores perfectly at every tier; endorsement unaccountable | 2 | S | not C3 | argument | design |
| T17 | Committee liability scores against proposer's direction, ignores acceptable region; connector/retirement ballots have no liability | 2 | S | not C3 | to verify | design + fix (settle against region) |
| T18 | Edition 1 cost pressure negligible: cost normalised by 1e6 vs 500 target | 2 | S | not C3 | to verify | fix (normalise by card scale) + re-draft |
| T19 | wake publishes real venue/reserve accounts gated on env only, on any world incl. fake; mainnet check keyed on kind | 5 | S | broken | yes, wake.py:503-509 | fix |
| T20 | Live tick overruns 1.0-2.4x; slowest_period_ns uses declared interval | 5 | S | broken | seat 5 diary | fix (measured interval) |
| T21 | Custom contract kinds judged/priced/forecast only as producers; predicates fixed to seed vocabulary | 1 | M | not C3 | yes | design (post-launch?) |
| T22 | Registered observations see only factory activity, no mids/funding/wallet series | 1 | M | not C3 | yes | fix (add series to window facts) |
| T23 | Child request reward trains nobody's router | 1 | M | unclean | to verify | fix |
| T24 | Mixed contract author woken to judge itself then refused | 1 | M | unclean | to verify | fix |
| T25 | Live venue writes return uncertain so tool.call ok:false on filled orders; misprices tool_calls/well_formed | 5 | M | broken | seat 5 diary | fix |
| T26 | probe --provider x402 max_tokens=32 pays for empty answer | 5 | M | broken | cli.py:132 | fix |
| T27 | Ledger volume: treasury.insolvency every event; 351 MB for 500 events | 5 | M | unclean | seat 5 | fix (write on transition) |
| T28 | wake publishes open_positions coin/side against A17 | 5 | M | unclean | to verify | fix |
| T29 | Scripted world never registers observation or learner; two kinds untested end to end | 5 | M | unclean | yes | fix (scripted provider) |
| T30 | scripted-crash dies at wallet -$4.14 below floor 0: liquidation loss lands after affordability check | 5 | M | broken | seat 5 | fix or doc |
| T31 | Seed sizing from venue equity vs kernel enforcing world wallet; order.infeasible on testnet | 5 | M | broken | seat 5 | fix (scripted sizing) |
| T32 | Learner action set frozen at registration; size band mismatch -> propensity.unlearned | 5 | M | unclean | seat 5 | doc or fix |
| T33 | A parent's child request to an evaluator judges whatever about_handle the parent supplies: buy a judge for a competitor's return; hindsight guard skipped on the child path | 3 | B | broken | test (21 failing) | fix |
| T34 | sandbox.run / observation.run treated as external writes by the recovery journal: death inside a jailed run wedges resume for good; replay past the tail raises UnbilledFailure | 3 | B | broken | test, end-to-end with real jail | fix |
| T35 | A refused (redundant) amendment stays at the head of the cadence waiting list and blocks every later retirement | 3 | S | broken | test | fix |
| T36 | Spot inventory the runtime did not account for (pre-existing holdings, refused fill still credited) raises out of the loop and again on resume | 3 | S | broken | test | fix (seed inventory from venue or refuse launch on non-empty spot) |
| T37 | Ledger shorter than its authenticated .head is a silent rollback; external writes re-run | 3 | S | broken | test | fix (raise LedgerIntegrityError) |
| T38 | Short connector body (a number, "OK") makes every later return that contains it malformed | 3 | S | broken | test | fix (min length or provenance) |
| T39 | The retirement target sits on its own committee | 3 | S | not C3 | test | fix (one line) |
| T40 | Child requests manufacture committee eligibility for never-routed confederates | 3 | S | not C3 | test | fix (count router-chosen decisions only) |
| T41 | Every registered observation runs at every window close under 5 s each, card or no card | 3 | M | unclean | test | fix |
| T42 | A broke incumbent buys compute via a fresh child from the novelty reserve | 3 | M | unclean | test | fix (child ceiling bounded by parent) |
| T43 | Response headers not bounded by max_bytes (~5 MB per call) | 3 | M | unclean | test | fix |
| T44 | Fake class transfer confirmed after a loss raises out of treasury.tick (fake rail only) | 3 | M | unclean | test | fix |

Perimeter (seat 1 ruling b), for Isaac: venue view fixed to manifest coins; only free GET text; nothing retained beyond one request; measurement closed to own activity. Proposed: reachability preflight (T10), GET on venue public info host, window facts series (T22), a bounded metered note primitive.
