# Round three fix plan

Goal: close every row of `triage.md` once, each behind a test that was red and is green, then one gate, one live rehearsal against a written checklist, and no further audit rounds. Audited commit `86b391c`.

## Why round two took two days, and what changes

| Round two | This round |
|---|---|
| The 20-minute gate was run by every agent, in parallel, many times. | Step 0 makes the gate fast. Agents never run it. I run it once, at the end. |
| Branches overlapped on `loop.py`, `governance.py`, `pricing.py`; merge agents resolved conflicts. | Every file has exactly one owning group. No two groups touch the same file. `docs/manifest.md` is updated by me after the merges, not by groups. |
| Findings without reproductions were argued about in prose. | 48 failing tests already exist (seats 3 and 4). A row without one gets its test written before its fix. A row that cannot be reproduced comes back as "not reproducible" with the attempt, and is closed as known. |
| Rules in the middle of long prompts were ignored (Codex ran the full suite twice this round). | The first line of every dispatch is the command allow-list. Codex never commits; I commit. |
| Reviews waited on the wrong bots. | One Codex review per PR, exits on first review, Opus fixes comments, merge. Under 20 minutes each. |

## Step 0: test speed, alone, before any fix

Owner: Codex medium. One PR, test code only, no production change. Measured on main with `--durations` over `tests/runtime tests/audit tests/world` (1,156 tests, 9:19 wall under `-n auto`):

| Test | Seconds | What it does |
|---|---|---|
| `test_a3_immune.py` fixture `w1_scripted_diary` | 551 (setup) | its own 500-event scripted run through the real jail |
| `test_a4_prices.py` fixture `w1_scripted_diary` | 549 (setup) | the same run again |
| `test_audit_b13_jail.py::...population_tool_in_the_jail` | 295 | the same run again |
| `test_loop.py::test_scripted_world_phase3_spec_condition_2` | 185 | short-cadence manifest, 500 events |
| `test_a5_exposure.py::...win_rate_is_below_sixty_percent` | 146 | scripted, 400 events |
| `test_loop.py::test_scripted_world_phase2_spec_condition_2` | 145 | |
| `test_loop.py::test_scripted_clock_amendment_...` | 139 | |
| `test_loop.py::test_scripted_amendment_lambda_...` | 62 | |
| `test_a17_wake.py` module fixture | 43 + 38 | |
| `test_connectors.py::...real_sortition...` (2 cases) | 23 + 23 | |

Everything else is under 8 seconds. The same seed gives the same run (a test asserts it), so the same world is recomputed about eight times, and under xdist a 550-second test pins one worker while the rest idle. The work: one session-wide cache of scripted runs keyed by (manifest, events, seed), shared across xdist workers through a lock file, computed once, read by every consumer; tests that mutate a ledger copy it first; `fast` and `world` markers auto-applied; no assertion weakened, no test removed, same collection count. Acceptance: the migrated files pass alone and together under `-n 4`, the heaviest world runs once per session, and the default gate on merged main, which I run once, is under six minutes. The remaining cost inside a run (a 351 MB ledger for 500 events, from an insolvency item written every event and a journal pair per connector call) is production code and belongs to groups E and B, not here.

## Step 1: fix groups, in parallel, file-disjoint

| Group | Rows | Files owned (nobody else touches them) | Model | Existing failing tests |
|---|---|---|---|---|
| A settlement | T1 double credit; T14 verdict card by subject; T36 spot inventory at launch | `settlement/lots.py`, `settlement/settle.py`, `charter/measurement.py`, `runtime/venue.py`, `runtime/bootstrap.py` | Codex medium | seat 4 round-trip (2), seat 3 `test_c3_spot` |
| B composition and authority | T2 ballot write; T33 child judge and hindsight; T42 child ceiling; T24 self-judging; T38 short body; T45 `about_handle` documented, unresolvable values fall back to the router's subject with feedback; then T15 action labels from tools and declared-mass floor; T23 child reward; T7 contract catalogue with ids and `inputs.you` | `runtime/compute.py`, `runtime/loop.py`, `runtime/propensity.py`, `cortex/schematics.py`, `cortex/request.py` | Fable, two PRs in sequence (authority first, then labels and disclosure) | seat 4 policy ballot, seat 3 `test_c3_child_judge` (3), `test_c3_connectors_observations` short body |
| C governance | T3 observation book into `CharterBook`; T11 meta eligibility; T35 refused amendment at head; T39 target off its own committee; T40 eligibility from router-chosen decisions only; T17 liability settled against the acceptable region; T6 publish the activation schedule | `runtime/governance.py`, `runtime/cadence.py`, `charter/book.py`, `charter/committee.py`, `runtime/worlds.py` (CharterBook construction only) | Codex high | seat 4 observation and meta eligibility, seat 3 `test_c3_governance` (3), eligibility case in `test_c3_child_judge` |
| D pricing and immune | T12 evaluator and meta cost share; T13 immune on typed card samples; T18 cost normalised by card scale; T41 value only named observations; T22 mids, funding and wallet series in window facts | `runtime/pricing.py`, `runtime/immune.py`, `runtime/observations.py`, `charter/controller.py` | Codex medium | seat 4 evaluator cost, immune sample |
| E ledger and resume | T34 jailed runs are read-only in the journal; T37 head ahead of file raises; T27 insolvency item on transition | `kernel/ledger.py`, `runtime/resume.py` (T27: the append site, coordinated with B if it is in `loop.py`) | Codex medium | seat 3 `test_c3_resume_wedges` (4) |
| F live rails and manifests | T4 testnet manifest constructs, validation names the fault, CLI prints the reason; T5 class transfer matched by hash and time window; T19 wake reads only the world's own accounts, mainnet check off `kind`; T20 measured tick in the cadence conversion; T25 uncertain venue writes not counted as failures; T26 probe budget; T10 connector preflight on answered-within-bounds; T43 header bytes capped; T28 no positions in the wake; T44 fake transfer failure caught; T30 liquidation loss before the floor check; T46 provider failures release the hold; T48 spot fills classified by venue metadata; T49 minimum order value published; T50 duration honoured; T8 `factorylab kill`: takes the ledger lock, writes `explicit_kill`, releases the seal, nothing else | `world/treasury.py`, `world/treasury_rails.py`, `world/exchange.py`, `world/connector.py`, `world/venue_tools.py`, `runtime/live.py`, `runtime/wake.py`, `runtime/cli.py`, `worlds/testnet.toml`, `worlds/edition1-example.toml` (pairs only), `deploy/` | Opus, keys in its worktree, testnet only, $0.50 cap, proves T4, T5, T20 and T25 by running; seat 6's rows included | seat 3 `test_c3_transport` |
| H scripted world and README | T29 scripted provider registers an observation and a learner; T31 seed sizing from the world wallet; T47 `max_tokens` for the reasoning seeds in `testnet.toml` (with F, pairs and tokens only); T6 README event count past the first activation; T32 documented | `world/scripted.py`, `worlds/scripted*.toml`, `README.md` | Codex medium | none; writes its own |

Then three design groups, decided with Isaac (see Decisions), each dispatched the moment the group that owns its files has merged:

| Group | What | Files | Model | After |
|---|---|---|---|---|
| G judge accountability | A verdict is a prediction that the judged return will not be blamed by the charter. Settle it, like the payoff forecast, when the window's cards attribute penalties: an evaluator whose high verdicts land on blamed returns loses standing. Antagonists expose a judge whose high verdict sits on a return that violated a card. Producer reward unchanged. | `runtime/feedback.py`, `settlement/standing.py`, `settlement/settle.py`, the exposure path in `runtime/feedback.py`, the attribution surface of `runtime/pricing.py` | Fable | A and D |
| J new kinds of work | Cards may name any registered kind (`answers_for`). Forecast predicates registrable as jailed `resolve(facts) -> bool`, versioned like observations. At registration a kind declares which of the four reward shapes pays it (judged, forecast, conformity, exposure); nothing else is welded to the seed roles. No fifth shape: reward stays outside the loop of the thing rewarded. | `cortex/registration.py`, `settlement/vocabulary.py`, `charter/charter.py` (`answers_for`), `runtime/routing.py` (`_return_channels`), `runtime/shared.py` | Codex high | B and C |
| K world access | Preflight by reachability (T10). GET on the venue's public info host for any coin. Markets registrable: `exchange.coins` and `venue.spot_pairs` become the seed, a `market` registration adds one the venue lists. A connector may pay for data through x402 with a per-call cap, from the same wallet. A metered, bounded, public key-value note (`note.put/get`, priced per byte-window, in the diary). Mids, funding and wallet series in window facts (T22). | `world/connector.py` (preflight, x402 leg), `world/venue_tools.py`, `world/market.py`, a new `runtime/notes.py`, `runtime/observations.py` (facts; D lands first) | Codex high | D and F |

Every proposal carries a promised effect (decision 4): connector and retirement proposals name the card they promise to improve and their yes-voters settle like amendment voters. That is one rule inside group C, not a new group.

Group E's T27 append site is in `runtime/loop.py`, which B owns: E hands B a one-line request and B lands it. Everything else is disjoint by construction.

## Step 2: the PR flow, per group

1. Fresh worktree from main after Step 0 merges. Dispatch prompt, first line: "You may run only: `uv run ruff check .`, `uv run pytest <your test files> tests/audit -o addopts=""`. Never `uv run pytest` bare. Never commit."
2. The agent turns the group's failing tests green, writes a test for every row that lacks one, and reports: rows closed, rows not reproducible with the attempt, the targeted test summary line.
3. I read the diff against the triage rows, commit, open the PR.
4. One Codex review. Opus fixes the comments on the same branch. Merge.
5. Groups A, D, E, H are small enough to land within an hour of each other. B and F are the long ones and start first.

## Step 3: close, once

1. **Gate.** I run the full gate once on merged main, and the slow resume tests once. Green or the group that broke it fixes it.
2. **Edition 1 re-draft** at Isaac's keyboard with the launch roster, after A, C, D land, so the verdict card selects by subject and the cost card is scaled correctly. The seat charter stays out of `testnet.toml` and validation refuses a live launch without an explicit `[charter]` (T9).
3. **Live rehearsal**, the check that replaces a fourth audit. An Opus agent with keys, a rehearsal manifest (short tick, low backstop so activation lands within the run), testnet, $0.50 cap. It is not asked to audit; it is asked to make each line true and paste the diary items that prove it:
   - `factorylab run --world testnet` constructs from the shipped manifest, all three keys present, no local patch.
   - A perp fill, a spot fill, a `perps_to_spot` transfer confirmed within the horizon, pots complete.
   - An amendment passes and activates at the published earliest event; a retirement passes and the assembly stops being routed.
   - A registered observation is named by a card, the card passes, the next price window measures it.
   - A connector whose root answers 404 is registered by vote and fetched; a numeric body does not malform later returns.
   - A ballot invocation that tries `venue.place_market` is refused; a child request naming another assembly's return as its subject is refused.
   - The process is killed inside a population tool run; resume comes back identical. `factorylab kill` releases the seal and the diary decrypts.
   - `factorylab wake` shows the world's own venue, no reserve section on a world without one, no positions.
   - Conservation and chain verification true at the end.
4. **Closure review.** Opus, cold: reads the merged diffs and the rehearsal diary against every triage row and states, row by row, closed, known, or open. Open rows go back to their group. When the table has no open row, `handoff.md` records it and we go to the droplet.

## Decisions, taken

1. **Kill.** Build `factorylab kill`. Simple: lock, one item, seal released. Group F.
2. **Cadence.** Launch at a two-minute tick and a 60-tick consequence horizon: a trade has two hours to show its result, governance can act every six hours, compute is roughly $12 to $15 a day at seat 5's measured call rate. The horizon only bounds when a trade is judged; the population may hold positions for any length and may vote to change its own tick. The rehearsal world runs shorter.
3. **Judges answer for verdicts.** Group G, as designed above. Chosen over paying producers by money only (loses the charter) and over grading verdicts by a higher judge (moves the collusion up a level).
4. **Every vote is liable.** Every proposal names the card it promises to improve. Group C.
5. **New kinds of work.** All three parts, with pick-from-four as the third. Group J.
6. **See the world.** Everything that is not physics comes down. Group K. What stays: no network inside the jail, every read through a journaled rail, every byte paid for.

## Models, by budget

Fable was used for groups B and G only, and no further Fable seats are dispatched (usage). Codex high took C, J and K; Codex reached its usage limit during wave two, so the closure review at the end and every remaining fix go to Opus (a reviewer that wrote none of the code it reviews). Codex medium takes Step 0, A, D, E and H. Opus takes F1, F2 (long, live, need keys) and every review-comment fix.

## Timeline, wall clock

Step 0 two hours. Wave one (A, B, C, D, E, F, H) three to four hours in parallel, B and F the long poles. Wave two (G, J, K) three to four hours. Reviews and merges one to two hours per wave. Gate under ten minutes, twice. Re-draft one hour at the keyboard. Rehearsal three hours including the world's own time. Closure review one hour. Two sessions.
