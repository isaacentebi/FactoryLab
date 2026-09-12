# Round-two triage

Seven seats audited main at `0ec4df4` (code identical to `edd6efc`). Reports: `class3-codex.md` (seat 1), `pathologies-codex.md` (2), `first-move-codex.md` (3), `defects-fable.md` (4, tests on branch `audit/defects`), `wiring-fable.md` (5, receipts in `docs/runs/audit-wiring.md`), `environment-opus.md` (6), `polish-opus.md` (7). Money spent by seat 5: $0.020 USDC and $0.023 Venice credit, receipted.

Every finding is listed once. "Seats" says who found it independently; a finding two or more seats reached without contact is the strongest signal this round produced. "Pass" is the fix pass it belongs to. "Decide" marks findings whose fix changes the design and needs the experimenter's call before anyone builds.

## A. Not Class 3

| ID | Finding | Seats | Sev | Pass | Decide |
|---|---|---|---|---|---|
| A1 | The population cannot dismantle the seed organisation: no assembly retirement, fixed role dispatch (`_role_for_kind`), producers cannot accept evaluation events, children cannot recurse or use tools. The essay's "armature the factory is licensed to tear down" is welded on. Round one #2/#6 partially fixed. | 1, 3 | blocker | 3 | yes |
| A2 | Governance cadence evaluates to zero: internal events share a timestamp, so forecast latency is 0 ns, p90 is 0, `ready` is always true; the world block publishes `slowest_period: 0s`. Amendments can activate every window. Round one #13 not closed by the new machinery. | 1, 2, 6 | blocker | 2 | no |
| A3 | The immune organ classifies against quantiles of its own sample, so stable failure and learning death are unreachable and a recovering or frozen factory reads as thrash; the response lowers exploration gain. It publishes the wrong flag to every request. Edition change resets its history; `gap_threshold` is inert; `violation()` exists twice and the organ imports the copy; `versions` uses different thresholds. | 2, 6, 7 | blocker | 2 | no |
| A4 | Priced penalties erase the learning gradient: Σ λ·violation is unnormalised and unclipped, shared across a role, so after two violating windows every producer settles at exactly 0 whatever it did. Reproduced in the scripted run (λ saturated on two cards, 1,660 settlements clipped). Edition 1 prices the same observation twice. | 2, 6 | serious | 2 | no |
| A5 | Exposure pays a near-constant (98.4% win rate) and rewards fake failures: a judge's wrong optional forecast counts as exposure though its verdict was right; 300 such wins drove the antagonist to 93% of routing. Round one #8 unfixed. | 2, 6 | serious | 2 | yes |
| A6 | Edition 1 as installed is not the edition voted: three of eight passed cards dropped, rolling windows are prose (every card measures the reserve window), the price controller learns from a charter other than the one published. The drafting script crashes on its first valid card since `answers_for`. Survey framing anchored the vote. | 1, 3, 6 | serious | 2 + first move | no |
| A7 | Producers and antagonists can amend a charter they cannot read: their requests carry the edition and card ids but not the norms or the cards; there is no reader tool. Committee size, threshold, reserve share and controller recurrence are also undisclosed. | 3 | serious | 2 | no |
| A8 | The full router topology (`menu` per router, `model_id` per assembly) is published to everyone. Round one #7, never fixed, not recorded as dissent. | 6 | serious | 2 | no |
| A9 | A judge can trade the account it forecasts and judge its own child's return; an evaluator's fills enter the shared FIFO lot book with no account, so a producer's later fills close against them and `return_paid_off` settles 0 by construction. | 6 | serious | 2 | no |
| A10 | Propensity is the router's distribution over executors, not the deciding agent's over its own actions, and it never rides on a request; no consumer can price a road not taken; Blum–Mansour forms one level above the decisions. | 6 | serious | 3 | yes |
| A11 | The population cannot register a measurement: pricing knows 22 catalogue observations; an amendment naming an unknown observation is accepted and then unpriced (`price.unparsed`). Launch validation rejects what amendment admission accepts. Round one #4 in narrower form. | 1, (6 open) | serious | 3 | yes |
| A12 | The population cannot renew Venice credit: `treasury.transfer` accepts only `to_reserve`/`to_venue`; the $5 tranche is a CLI action. Once OpenRouter is empty, a Venice assembly cannot buy its next call with money the reserve holds. | 3, 1 | serious | 2 | yes |
| A13 | The novelty reserve's three lifetime calls expire before the consequence arrives (continuations count; the backstop is 200 events); the learning-death flag has no responder; refused duplicate proposals consume the share. | 2, 4 | serious | 2 | no |
| A14 | Overfitting has no live actuator: one rejected registration per twenty returns satisfies the revision card; cascade siblings inherit the representative's meta score; the top meta earns 1 for any valid conformity; no sampling-rate remedy at runtime. | 2, 6 | serious | 2 | no |
| A15 | Committee votes carry no policy liability (fast score 1 for any valid ballot, counted toward eligibility); the proposer's assembly may sit on its own committee; an empty amendment bumps the edition and resets the immune history. | 2, 6 | serious | 2 | no |
| A16 | The charter verdict is forced to serve as the payoff probability: closing, research and tool-building returns open no lot so `y = 0` whatever their worth; judging them useful is punished. Seat 1 rejects the round-one dissent that a fixed consequence is forbidden, and keeps the narrower conflation. | 1 | serious | 3 | yes |
| A17 | The wake publishes positions with entry prices and per-assembly counts; whether that exceeds "the outcomes this factory produces" is open. | 5 | minor | 2 | yes |

## B. Will break

| ID | Finding | Seats | Sev | Pass |
|---|---|---|---|---|
| B1 | `treasury.transfer` is unreachable from any return: the schema's union type crashes the validator, the whole return is discarded. Testnet has no `[treasury]`, so rehearsals never exercised it. Regression against round one #7. | 3, 5 | blocker | 1 |
| B2 | A journaled venue read failure replays on every resume: one transient outage during a restart bricks the funded world permanently (`Restart=always` loop, key never released). Same shape for malformed fill/funding rows. | 4 | blocker | 1 |
| B3 | A lone UTF-16 surrogate in any model or seller reply crashes canonicalisation and wedges every resume. | 4 | blocker | 1 |
| B4 | A passed amendment whose region parses to infinity crashes activation and every resume after it. | 4 | blocker | 1 |
| B5 | Replay of an interrupted live event charges the full ceiling for calls the journal refuses to dispatch (205,725 µUSD per call reproduced). | 4 | serious | 1 |
| B6 | An x402 payment with unknown outcome leaks a wallet hold nothing closes; two hundred such calls end the world `insolvency:compute`. | 4 | serious | 1 |
| B7 | A vendor's reported cost is trusted without bound; one bad number kills the wallet and releases the seal. | 4 | serious | 1 |
| B8 | Manifest validation accepts `timing.min_ratio` 1–2 that the cascade refuses at the first verdict. Round one #11 unfixed. | 4 | serious | 1 |
| B9 | Resume materialises the whole diary (9.2× RSS); a funded diary becomes unresumable on 4 GiB in about a month; the hourly wake stops within weeks. Round one #13 recorded as fixed, not on this path. | 4, 5 | serious | 1 |
| B10 | Resume does not pin the venue account: a credential swap after a crash runs B's account with A's lots and learning. Round one #15 unfixed. | 1, 4 | serious | 1 |
| B11 | A world run with `--tick-interval` cannot be resumed or woken (the override changes the hashed genesis); the handoff's own rehearsal command hits this and the CLI prints one fixed line. | 5 | serious | 1 |
| B12 | One mistyped field in one proposal silently voids the whole return: no rejection, no feedback, the valid proposals and any order in the same reply lost. Thirteen return field names are reserved and typed globally but never declared. | 5, 6 | serious | 1 |
| B13 | No code has ever run in the jail on macOS: the `sandbox-exec` profile aborts the interpreter; every tool proposal is refused; the four jail tests skip so the gate is green. Linux (bubblewrap under the unit's restrictions, AppArmor on 24.04) unproven. | 5, 4 | serious | 1 |
| B14 | The Venice rail is selected and paid and returns nothing on world-sized prompts: the thinking switch is ignored above some prompt size, hidden reasoning eats the budget, the diary records no reasoning tokens. | 5 | serious | 1 |
| B15 | The mainnet guard tests the manifest's declared name, not the file; `edition1-example.toml` declares `name = "testnet"`; two manifests, one identity. | 7 | serious | 1 |
| B16 | A death before the launch snapshot leaves a ledger neither `run` nor `resume` accepts; the supervisor loops forever. | 4 | minor | 1 |
| B17 | A backward wall-clock step stalls a resumed world for the whole gap. | 4 | minor | 1 |
| B18 | `market.discover` never returns on a repeating index, and takes 45 s on the real one inside the tick; synchronous calls outrun the 10 s floor (4 ticks in 156 s). | 4, 5 | minor | 1 |
| B19 | The pots identity check has never run: OpenRouter `limit_remaining` is null for an unlimited key; reserve reported 0 without `[treasury]`. | 5 | minor | 1 |
| B20 | Two documented commands are wrong: the slow-test gate exits 4 against `addopts`; killing the `uv run` wrapper leaves the world running. | 5 | minor | 1 |

## C. Unclean

Seat 7's twenty ordered items are the polish pass, unchanged (`polish-opus.md`). The ones that touch behaviour are folded above: `violation()` twice and the `versions` thresholds (A3), the duplicate card (A4), the manifest name check (B15). Seat 6's minors: `capability_versions` always `{}`, `UpwardBuffer` dead, `registration_feedback` broadcast with its handle, cascade attribution.

## Passes

1. **Defects** (B1–B20): seat 4's fifteen failing tests are the acceptance check, plus new tests for B1, B11–B15, B19. No design decisions needed. Codex cannot take the sandbox and key paths (B3, B13 the macOS profile) — those go to a Claude subagent.
2. **Fidelity mechanics** (A2–A9, A12–A15, A17): each fix is local and the essay's remedy is stated in the report. A6 includes repairing the drafting script and re-running edition 1 with the launch roster.
3. **Architecture** (A1, A10, A11, A16, A5's redefinition): decided with the experimenter first, then specified, then built. These change what the population can do, not how well the code does it.
4. **Polish** (C): last, after the gate is green on everything above, so no auditor's line numbers move under a fix.

Each pass merges to main behind the everyday gate and the slow resume tests before the next starts. Round three is a cold re-audit of the same seven seats on the fixed main.
