# Defect audit, round two — seat 4 (Claude Fable): defects, reproduced

Cold read of main at `0ec4df4` (brief: `edd6efc`), essay first, then edition 1, the specs, the build log and round one's five reports, then every module and test. Gate reproduced: `uv run pytest` 1685 passed, 19 skipped (332 s); `scripted --events 500 --seed 1` and `scripted-crash --events 600 --seed 2` end as documented (conservation and verify true; the crash world dies `balance_zero`, seal released). Every finding has a failing test under `tests/audit/` on branch `audit/defects`; the output quoted is what the test prints on this commit. Nothing was fixed. No key file was read; nothing touched a network.

Beyond reading: a kill-at-every-ledger-write sweep (scripted world, 60 events, 17,185 appends, a kill at every 131st append in three modes — after the durable write, before it, with a torn half line — then resume and compare to the uninterrupted summary field by field); fuzzing of provider replies, the x402 quote parser and the EIP-3009 signer; a fake live venue failing mid-tick; wallet arithmetic at the boundaries; the tool jail on this host; clock steps on a resumed world.

Every finding is **will break** unless marked otherwise.

---

## 1. A journaled venue failure replays on every resume: one transient outage bricks the world — blocker

`factorylab/runtime/live.py:122` (`LiveVenue.on_tick`, `self.exchange.mids()` unguarded); `factorylab/world/exchange.py:651-659` (`mids` raises `VenueUnavailable` when nothing is cached, i.e. in every fresh process); `factorylab/runtime/resume.py:225-238` (replay re-raises a recorded `io.result` error) and `:293-303`; the same shape at `exchange.py:709-746` (`funding_payments` raises `ValueError` on a malformed row) and `:760-775` (`fills` parsing unguarded), reached from `live.py:91-118` and `loop.py:1441-1458`.

Essay, II.II.b: "a well-designed 'hard world' … is one in which irreversible harm is impossible."

Scenario: the process restarts (any crash, a reboot, a deploy) while Hyperliquid's API is slow; `meta()` succeeds, the first `all_mids` poll times out three times (3.5 s). `on_tick` raises; `_process_event` has no handler; the process exits. The journal has durably recorded `io.call exchange.mids` then `io.result {error}`. Every later `resume` replays the tick, hits the recorded error, re-raises it and dies — forever, whatever the venue does now. On the droplet: `resume` exits 1 in a `Restart=always` loop, the world neither alive nor terminated, positions unmanaged, the key never released. "Never re-dispatch a recorded write" is right for money; here it is applied to a read whose failure the runtime never catches. Any exception escaping `_process_event` whose cause was journaled has this property (findings 2 and 3 are further instances).

Tests: `tests/audit/test_audit_resume_wedges.py::test_transient_venue_outage_is_weather_not_death` — `RuntimeError: external call failed (VenueUnavailable)`; `::test_recorded_venue_failure_does_not_wedge_resume_forever` — the same error from a resume with a venue that never fails again.

Fix: treat every venue read in the tick as weather: `on_tick` (and `_producer_step:2517`) catch `RuntimeError` and skip the observation, as `funding`/`fills` already do; `fills`/`funding_payments` parse rows under `except (KeyError, ValueError, ArithmeticError)`. Independently, `_recorded_error` should preserve the contained class (`VenueUnavailable`, the metering classes) instead of widening to bare `RuntimeError`.

## 2. A lone UTF-16 surrogate in any reply crashes the ledger and wedges resume — blocker

`factorylab/kernel/ledger.py:77-80` (`_canonical`: `ensure_ascii=False` then `.encode("utf-8")`) reached from `loop.py:1361` (`Bus.publish`), `:1487/1493` (`runtime.input`), `:1432-1434` (the snapshot append, outside its `try`); the text enters through `cortex/assembly.py:168-186` (`raw_decode` accepts `"\ud83d"`), `world/openrouter.py:303`, `world/market.py:364`.

Essay, II.I.b: the contract space is the agent's "entire sensory apparatus"; a byte it accepts must not be lethal.

Scenario: a reply cut by `max_tokens` inside an emoji, or any x402 seller the population registered, returns a JSON string with a lone surrogate. It passes parsing and validation, lands in `outputs`, private memory and `registration_feedback`, and the first ledger write carrying it (the `ProducerReturn` or `Verdict` event, or the next snapshot) raises `UnicodeEncodeError` out of `_process_event`. The reply is journaled, so every resume replays it and dies identically. Non-finite numbers were closed in round one (`_finite_json`); strings were not.

Tests: `test_audit_resume_wedges.py::test_lone_surrogate_in_a_reply_does_not_crash_the_ledger` and `::test_lone_surrogate_does_not_wedge_resume` — `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud83d' in position 390: surrogates not allowed`, twice.

Fix: reject at the boundary (walk parsed strings in `_parse_json_object`, `malformed` on a failed strict UTF-8 encode) and make `_canonical` total (`ensure_ascii=True` or `errors="surrogatepass"`) so no population string reaches a raise inside the kernel.

## 3. A passed amendment whose region overflows crashes activation, and every resume after it — blocker

`factorylab/runtime/cards.py:18,31-32,35-56` (`_NUMBER` admits exponents; `float("1e400")` is `inf`), `:64-83` (`region_for`), `factorylab/charter/controller.py:12-22,39-55` (`_number` raises), reached from `loop.py:1686` (`_derive_regions`) at the boundary that activates the edition (`loop.py:3309`). Nothing validates the region at proposal (`loop.py:3082-3167`, `charter/book.py:143-162`).

Essay, II.II.b: the kernel "is inviolable from both the inside of the factory (immutable) and its outside"; cards.py's own contract: "an unparsed card simply carries no price."

Scenario: an assembly proposes `acceptable_region: "at most 1e400"` (or four hundred digits); the committee passes it; at activation `region_for` raises out of `_manage_reserve_window`. Amendment and activation are durable, so the replay reaches the same boundary and dies. The population can brick the kernel with a card.

Test: `test_audit_resume_wedges.py::test_overflowing_card_region_is_refused_or_unpriced_not_fatal` — `ValueError: hi must be a finite number` on the run and both resumes.

Fix: `_parse` returns `None` for non-finite bounds; `_propose_amendment` refuses a card whose region the controller would reject, reason in `amendment_feedback`.

## 4. Replaying an interrupted event charges model calls the journal knows were never dispatched — serious

`factorylab/runtime/resume.py:257-258` (raises `RuntimeError("… never dispatched")`), `:261-275` (recorded and re-raised as bare `RuntimeError`); `factorylab/world/metering.py:90-99` (any non-`UnbilledFailure` → `commit_uncertain`), `kernel/wallet.py:227-234`.

Essay, I.IV: "Nothing registers as a cost except against what the factory depends on"; a bill nobody issued is not a cost.

Scenario (live world, any crash): the process dies after `decision.open`, before the provider was contacted. On resume the journal correctly refuses to dispatch (no `io.call`) but raises a class metering reads as "may have been billed": the full ceiling is booked `metering.uncertain`, the return is `failed` with `cost = ceiling`. No money left the factory; the wallet now under-reports itself, drift accumulates, and every restart costs the ceiling of every undispatched call in the interrupted event (a committee vote is five in one event). The resumed-equals-uninterrupted guarantee holds only for deterministic adapters.

Test: `tests/audit/test_audit_money_paths.py::test_replay_of_an_interrupted_event_does_not_charge_undispatched_model_calls` — `provider.calls == 0`, yet `metering.uncertain` for 205,725 µUSD; balance 99,794,275 against 100,000,000 at the cut.

Fix: raise `UnbilledFailure` (metering releases) for the never-dispatched case, or dispatch it live — the journal has proven no request left the process.

## 5. An x402 payment with an unknown outcome leaks a wallet hold nothing closes — serious

`factorylab/world/market.py:421-423` (`except PaymentOutcomeUnknown: record("x402.unresolved"); raise` — the reservation is neither committed nor released), `:336-337,354-360`; no reader of `x402.unresolved` in `factorylab/runtime/`; only the treasury uses `Wallet._reservation_for_resume`.

Essay, II.I.b: the reward line is "thin but stateful … a managed queue of outstanding decisions"; a hold is state that must close.

Scenario: a registered seller (the population may register any seller on the public index) drops the connection after the signed authorization is sent, or returns an unreadable receipt. Each call leaves `quote.amount_micro` held: `available` falls, `balance` does not, `check_conservation()` stays true, the return says `cost 0`. Holds survive snapshots and resume. At `max_request_micro` $0.50, two hundred such calls make every action infeasible and the world dies `insolvency:compute` — a seller's fault, not the factory's liquidity.

Test: `test_audit_money_paths.py::test_unknown_x402_payment_outcome_does_not_leak_a_wallet_hold` — `3 holds of 300000 micro never close`.

Fix: `commit_uncertain` (as the OpenRouter path does) and reconcile `x402.unresolved` against the reserve's USDC balance on the next tick — the payment either left the reserve or it did not.

## 6. A single reported vendor cost, unbounded, kills the wallet — serious

`factorylab/world/metering.py:100-102`, `kernel/wallet.py:191-208` (`commit_reported` debits any `actual`); `world/openrouter.py:134-140`, `world/venice.py:181-183` (the vendor's number is trusted as is).

Essay, II.IV: "The only kill switch greater than the teardown of a factory kernel is a token budget of $0" — the world's, not a vendor's typo.

Scenario: OpenRouter or Venice reports `cost: 1000000` (a unit error, an outage page, a compromised seller) for a request reserved at 167 µUSD. The wallet books $1,000,000, `dead` is true, the world terminates `balance_zero` and releases the seal. Round one found overruns swallowed; the fix removed every bound.

Test: `test_audit_money_paths.py::test_a_single_reported_vendor_cost_cannot_kill_the_wallet` — `charged 1000000000000, balance -999900000000, dead True`.

Fix: bound a reported overrun (`actual ≤ k·ceiling`, `k` in the manifest); above it book the ceiling, record `metering.disputed`, let the reconciler carry the drift. Death must come from the world.

## 7. The manifest validator accepts a `timing.min_ratio` the cascade refuses — serious (round one, Opus #11, not fixed)

`factorylab/runtime/worlds.py:305-306` (`min_ratio < 1`) vs `factorylab/runtime/cascade.py:14-15` (`< 3` raises), reached at the first `Verdict` (`loop.py:2060-2066`). Not in the build log's fix list; no commit touched it.

Essay, I.IV: "this very important first and only move needs to be deeply considered" — the validator is the architect's only check of that move.

Scenario: `funded.toml` with `min_ratio = 2` passes `factorylab manifest`, launches, dies `ValueError: cascade min_ratio must be an integer >= 3` at its first judgement.

Test: `tests/audit/test_audit_manifest_clock.py::test_manifest_validation_rejects_a_min_ratio_the_cascade_will_refuse` — `DID NOT RAISE`.

Fix: `min_ratio >= 3` in `WorldManifest.validate`.

## 8. Resume materialises the whole diary: a year-long world stops being resumable in weeks — serious (round one, Opus #13, recorded as fixed by "diary streaming", not on this path)

`factorylab/kernel/ledger.py:198-203` (`read_bytes`, every token), `:236-240` (`_recovery_items` decrypts every item into a list), `factorylab/runtime/resume.py:492,506-507` (the last snapshot is found only after that). Commit `dcebbd1` changed `resume.py` and tests, not this.

Essay, I.IV: after the first move "the architect commits to no further intervention" — a world that needs a bigger machine to restart needs the architect.

Measured: a 24.9 MB scripted diary costs 229 MB net RSS to resume (9.2×). At run 5's ~40 KB per world event and a 5-minute tick the funded diary grows ~12 MB/day; a 4 GiB droplet cannot resume after roughly a month, and every crash from then on is finding 1's restart loop.

Test: `tests/audit/test_audit_liveness.py::test_resume_memory_does_not_scale_with_the_whole_diary` — `resume used 89 MB for a 10.7 MB diary`.

Fix: verify incrementally from a persisted head, find the last snapshot scanning backwards, decrypt only the tail.

## 9. A refused duplicate model proposal consumes the window's novelty share — minor

`factorylab/runtime/loop.py:3016-3017` (`reserve_for` before `registry.register`; same order at `:2962-2963`, `:3024-3025`, `:3062-3063`, `:3160-3162`); `kernel/reserve.py:85-119,144-160` (debited at issue, never refunded).

Essay, II.II.b: "some share of compute and write access is usable only in the context of unhistoried actions."

Scenario: an assembly re-proposes a seeded model (run 6: eleven re-proposals). `reserve_for` debits $0.10 of the share, `registry.register` refuses the version, the proposal is rejected — the share is gone until the window ends. Enough refusals starve the window's real registrations.

Test: `test_audit_money_paths.py::test_a_refused_duplicate_model_proposal_does_not_consume_the_novelty_share` — `assert 9886000 == 9986000`.

Fix: check the registry version before drawing the receipt, or refund an unconsumed receipt on rejection.

## 10. A backward wall-clock step stalls a resumed live world for the whole gap — minor

`factorylab/runtime/live.py:51-62` (`wait = last_ns + interval − now`, unbounded), `:69-72` (`restore` keeps the diary's `last_ns`).

Essay, II.IV: "a factory cannot move slower than its environment."

Scenario: the droplet is restored from a snapshot, or NTP steps the clock back by Δ; the resumed clock sleeps Δ before its next tick — no ticks, no reconciles, positions unmanaged.

Test: `test_audit_manifest_clock.py::test_a_resumed_live_clock_does_not_sleep_through_a_backward_wall_clock_step` — `21660.0 <= 60` (six hours).

Fix: `wait = min(wait, interval_ns)`; `max(now, last + 1)` already keeps timestamps monotonic.

## 11. `market.discover` never returns on a repeating index — minor

`factorylab/world/market.py:96-147`: the loop ends only on `offset >= total` or a short page; a paginator that omits `total` and repeats resources satisfies neither.

Essay, II.IV: requisite velocity — a call that never returns stops every loop above it.

Scenario: Coinbase's index misbehaves; a producer's `market.discover` hangs the event inside `Meter.run`; the process is alive, so nothing restarts it, and the wake goes stale.

Test: `test_audit_liveness.py::test_discover_terminates_on_a_repeating_index` — `discover() requested 26 pages and never returned`.

Fix: a page budget, and stop when a page adds no unseen resource.

## 12. A resume never checks which venue account it reattached to — minor (round one, Opus #15, not fixed)

`factorylab/runtime/resume.py:384-386,415-419` (adapters compared by `name` and `deterministic`); the venue address comes from `hyperliquid.key`, appears in no manifest field and no snapshot. `LiveRail` pins the reserve address; the venue has no equivalent.

Essay, II.II.b: a changed world "is a new factory from a new v0" — here silently.

Test: `test_audit_manifest_clock.py::test_a_resume_snapshot_pins_the_venue_account_it_will_reattach_to` — `{'name': 'fake', 'deterministic': False}`.

Fix: record the venue address in the launch snapshot; refuse resume on mismatch.

## 13. A death before the launch snapshot leaves a world nobody can launch — minor

`factorylab/runtime/loop.py:580-589` (the ledger file is created) before `:636-641` (`HyperliquidExchange`, a network call in its constructor, `exchange.py:619`) and `:1346` (the launch snapshot); `deploy/start.sh` runs `run` only when the file is absent, then `exec resume`; `resume.py:493-495` refuses a snapshot-less ledger; `ledger.py:157` refuses an existing file.

Essay, I.IV: the first move "is the architect's only move" — it must be able to happen.

Scenario: on launch day the venue's `meta()` times out once during construction. The header and a handful of items are on disk; `run` exits; `start.sh` execs `resume` — "recovery unavailable" (exit 1); every restart skips `run` because the file exists. Found by the sweep (the kill at append 7); confirmed by the test.

Test: `test_audit_liveness.py::test_a_death_before_the_launch_snapshot_leaves_a_launchable_world` — `{'resume': 'refused: ledger has no recoverable snapshot', 'run': 'refused: FileExistsError'}`.

Fix: construct the adapters before creating the ledger file, and let `resume` of a ledger that holds no `Launch` event re-run the launch.

---

## Round-one fixes, re-verified on main

Fixed (by reading, and by the tests named): population cards priced through `answers_for` (`loop.py:1789-1799`); the tool jail replaces the blacklist (bubblewrap + seccomp on Linux, sandbox-exec on macOS, refusal elsewhere — on this macOS host the jail refuses to start, every probe exits −6, and the runtime reports "no jail on this host"; the Linux path was read, not run); fill cursors start at launch (`loop.py:633,659-661`); the ledger lock; torn-append repair (my sweep's torn mode); string-aware JSON extraction; the novelty reserve's sign; the immune organ in the loop; order intents with stable client ids; Blum–Mansour under the standing mix; child requests wired; non-finite numbers refused; the treasury fee shortfall fails closed; `cost_of` inside the release guard; the amendment cap; the x402 lifetime (`min(…, 600)`) and per-request cap; router id retirement; committee votes in the insolvency streak; the whole fatal fill batch booked; key-file mode on resume. Overruns are now debited — without bound (finding 6). Not fixed: 7, 8, 12. Of the two unguarded venue calls, `place` is behind `_venue_write`; `_producer_step`'s `mids()` (`loop.py:2517`) is bare but shielded by the adapter's cache after the first read — finding 1 is the surviving instance.

## Checked, clean (what I tried)

- Kill at every ledger write: 3 × 131 kill points across 17,185 appends; every resume after the launch snapshot reproduced the uninterrupted summary exactly (all fields but `resumes`), including torn half-lines, kills between the two writes of `queue.open`, reserve-window snapshots and the scripted amendment's activation. The scripted world's resume is sound; the live path's defects are 1, 2, 4, 13.
- Wallet arithmetic at zero, one micro-dollar, a settle below zero, a commit after a loss: conservation holds, death is final, a dead wallet refuses settles and reservations; a negative `available` is handled.
- The x402 quote parser and EIP-3009 typed data: sixteen adversarial `accepts` variants (2²⁵⁶, leading zeros, booleans, zero recipient, wrong asset/network/domain, unicode digits, non-dict metadata) refused; `maxTimeoutSeconds = 10³⁰` is accepted but signs `validBefore = now + 600`; the header outranks the body; SIWE signs a fresh nonce and the exact URI.
- Seal and chain: the key is released only by final `Termination`; the verified-prefix cache re-compares bytes; a different manifest hash refuses reopen.
- Sortition with fewer eligible than seats: all eligible are seated and a majority of seats passes; with none eligible the committee is empty and the amendment fails (its trial share consumed, finding 9). The amendable tick is bounded at proposal and activation.
- Controller damping, the immune ratchet (gamma raised on stable failure, lowered with borrowed decay on thrash, restored through the learner codec), the cascade gate's tier separation, the observations catalogue (zero denominators yield "unsupported", never a division): no defect found.
- The treasury's two-phase journal: every rail call is contained; a lost acknowledgement replays as `Pending`, never resubmitted; restored holds are re-bound by id and purpose.

## Open (the essay leaves these)

Whether a vendor's bill or the table price is the "real" cost when they disagree (6 needs a policy, not only a bound); how long an unknown payment may stay unresolved before it counts as paid (5); whether a world that cannot be resumed on its machine should die rather than wait (8).

## Test summary

`uv run pytest tests/audit/` on this commit: `15 failed in 8.69s`.
