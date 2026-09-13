# Cold audit, round three — seat 3: defects, reproduced

Auditor: Fable 5.1. Branch `audit3/defects-fable`. Every finding below has a failing test under
`tests/audit/test_c3_*.py`; nothing was fixed. Line numbers are from this worktree. Labels:
**broken** (the intended thing is not there or falls over), **not Class 3** (it is there but is
not the factory the essay describes), **unclean** (works, wrongly). Method: read every module
and every test; then break the jail, the keys, the ledger and its `.head`, resume at every
ledger write, the connector proxy, observations in the jail, nested requests, retirement,
spot lots and the fake rails. Kill-and-resume trials were run on the scripted world at the
first append of 45 item kinds (harness kept in scratch, not committed); the results are
summarised at the end.

## Blockers

### 1. A parent's child request chooses the judge and the subject — broken

`factorylab/runtime/compute.py:859-864`. When a child request targets an evaluator,
`_invoke_child` builds `Event("child-input-…", REGISTERED, item.inputs)` from the parent's own
inputs and hands it to `_evaluator_step`. Whatever `about_handle` the parent put in those
inputs becomes the judged subject, and the child's verdict settles that return's verdict
channel. Nothing checks that the subject is the parent's own decision or an open account.

Failure: any producer with an open account hires `eval-a` to score a competitor's pending
return at 0.0 (`test_c3_child_judge.py::test_finding_1_a_parent_can_hire…`: the stranger's
`decision-0` is `SETTLED` at score 0.0, no `return.refused`). The router, its sampled
propensity, the adversarial share, the meta cascade and the "judged event" ancestry exclusion
are all bypassed: whoever pays for a child buys the judge.

Second face, `factorylab/runtime/loop.py:487`: the A9 hindsight guard only runs when
`about != subject`. On the child path the parent-supplied `about_handle` *is* the subject, so a
child judge seals a payoff forecast on a return whose consequence is already fixed
(`…::test_finding_1_the_child_judge_path_skips_the_hindsight_guard`: `rt.consequences.payoff`
is known before the call, the forecast is sealed, no refusal). Forecast skill, the currency of
standing and committee eligibility, is now buyable with hindsight.

Fix: in `_invoke_child`, refuse a Verdict/MetaVerdict child whose `about_handle` is not the
parent's own handle (or an ancestor within the request chain); route all other judging through
the router. Run `_judged_event`'s hindsight check on the resolved subject regardless of who
named it.

### 2. A jailed run counts as an external write: death inside it wedges the world — broken

`factorylab/runtime/resume.py:369-380`. `_read_only` lists the venue reads, treasury polls and
connector `fetch`; it omits `sandbox.run` (population tools) and `observation.run`. Both run in
the jail with no network and no side effects, but the journal treats them as writes.

Two consequences. (a) A process death between `io.call sandbox.run` and its `io.result` makes
every later resume fail at `resume.py:339-341` with `replay_diverged: unacknowledged external
write sandbox.run … refusing to submit it twice`. The world is dead for good, wedged by a pure
computation (`test_c3_resume_wedges.py::test_finding_2_death_during_a_population_tool_run…`
reproduces it end-to-end on the scripted world with the real jail; the two `…_is_replayable`
cases show the journal alone). (b) `resume.py:344-347`: while the interrupted event is
re-executed past the journal tail, every non-read-only call raises `UnbilledFailure`
"external write was never dispatched". A tool or observation reached after the crash point
fails, and the event completes on a path the uninterrupted world never takes
(`…::test_finding_2_a_jailed_run_later_in_an_interrupted_event_fails…`). The trials confirm
it: every crash inside the connector sequence (`connector.call/seated/vote/tally/registered`,
`io.call connector.fetch`) resumed with one more `tool_call_failures`, one fewer order marked
and a different equity, because the `connector-parser` tool that follows the fetch failed with
`UnbilledFailure` on replay.

Fix: add `run` for `sandbox.run`/`observation.run` to `_read_only` (they are deterministic
given their journaled inputs, or re-runnable at worst), and journal their result so a torn
pair is completed by re-execution.

## Serious

### 3. One refused amendment blocks every later retirement — broken

`factorylab/charter/book.py:186-190` refuses a passed amendment that an earlier activation made
redundant ("amendment leaves the charter unchanged"). `governance.py:809-822` closes its
ballots, but nothing calls `cadence.activated` (`cadence.py:143` is the only place the waiting
list is popped), so the refused id stays at the head of `waiting`. `governance.py:588` then
skips every passed retirement that is not at the head: `if waiting and waiting[0] !=
row["proposal"].id: continue`.

Failure (`test_c3_governance.py::test_finding_3…`): two identical amendments pass; the second is
refused at activation; a later retirement passes its committee and never activates across three
boundaries (`waiting=['same-two', 'retire:…']`). Two agreeing amendments in flight are the
ordinary case, so the population loses retirement for the life of the world, and
`charter.deferred` accumulates.

Fix: pop a refused amendment from the cadence (`self.cadence.activated(...)` or a dedicated
`refused()`), and let retirements skip refused/stale heads.

### 4. Spot inventory the runtime did not account for kills the loop — broken

`factorylab/runtime/venue.py:64` raises `ValueError("spot fill exceeds accounted inventory")`
inside `_settle_exchange_effects`, which `compute.py:527` calls after every venue tool. Two
ways in (`test_c3_spot.py`): an account that already holds spot BTC at launch (a rehearsal on
the same account, operator dust) and a producer calls `venue.close` on it; or a spot buy fill
whose order no open account owns is refused by the lot table (`consequence.refused`) yet still
credited to `spot_inventory`, so the next producer sell passes the runtime's check and dies in
`settlement/lots.py:176`. Either way the exception leaves the event loop, and because the fill is real a resume
re-reads it and raises again (reasoned from `resume.py`, not reproduced). The sell itself is
not the population's fault.

Fix: seed `spot_inventory` and the lot table from the venue's balances at launch (or refuse to
launch on a non-empty spot account), and never credit inventory a lot table refused.

### 5. A ledger shorter than its `.head` is a silent rollback — broken

`factorylab/kernel/ledger.py:323-334`: `_load_head` returns `None` unless `0 < offset <= size`.
An authenticated head whose offset is beyond the file is the strongest possible evidence that
acknowledged items are gone — a restored backup, a copy truncated on a line boundary — but
`reopen` treats it like a missing cache, verifies the shorter chain, and resumes from the older
state without a `ledger.repaired` item. Events whose external writes already happened are
re-run (`test_c3_resume_wedges.py::test_finding_5…`: a scripted world truncated 20 items after
its launch snapshot reopens cleanly). Corrupt and foreign heads fall back correctly; only the
"head ahead of file" case is wrong.

Fix: when the head decrypts and matches genesis but `offset > size`, raise
`LedgerIntegrityError`; require the operator to restore the file, not the runtime to guess.

### 6. There is no kill — broken

README line 25 (and its "How it ends", line 27): after launch "one control: kill". `cli.py:512-600`
registers `manifest probe market reserve run treasury resume wake report postmortem versions`.
`Termination` has `explicit_kill`, but the only caller is `loop.py:159-161` on `--kill-at-end`,
a launch-time flag for budgeted rehearsals. A funded world (budget 2**63) can only be stopped by
killing the process, which is a crash, not a kill: the seal is never released and the diary
stays unreadable until the balance reaches zero.
`test_c3_governance.py::test_finding_6_the_operator_has_a_kill_control`.

Fix: a `kill` subcommand that takes the ledger lock (or signals the runtime) and records
`explicit_kill` before releasing the seal.

### 7. A short connector body makes every return that mentions it malformed — broken

`compute.py:654-658` refuses any final output containing a fetched body ("connector body in
durable output"). For a numeric or one-word body — a price feed, a health endpoint — the
substring check refuses every later return whose rationale or size contains that text
(`test_c3_connectors_observations.py::test_finding_7…`: bodies `65000.5`, `OK`, `1`; the
producer's order return is `malformed`). The one thing the population fetched is the one thing
it may not say; the connector feature is unusable for the feeds it was built for.

Fix: apply the durable-output rule only above a minimum body length (say 32 bytes), or refuse on
the parsed value's provenance rather than by substring.

### 8. The assembly being retired sits on its own committee — not Class 3

Essay §"Charter": "that seat is consistently rotated, preserving a kind of mutual anonymity".
`governance.py:570-576` pops only the proposer from the eligible draw; the target is not
excluded. `test_c3_governance.py::test_finding_10…`: `eval-a` is seated on
`retire:…` for `eval-a` (`['eval-b', 'seed-observer', 'meta-a', 'eval-a']`). With `seats = 5`
and a small population one seat is a fifth of the vote on its own retirement.

Fix: `eligible.pop(target, None)`.

### 9. Child requests manufacture committee eligibility — not Class 3

`governance.py:529-547` counts settled consequences by assembly, including
`parent_handle` decisions, and `_independent_decision` (606-617) only excludes decisions the
assembly itself requested. A confederate that has never been routed earns
`committee.min_settled` from five child requests by another assembly
(`test_c3_child_judge.py::test_finding_12…`: `invocations_by_assembly['confederate'] == 5`,
zero router picks, `_committee_eligible()` returns it). Combined with 8, any paying assembly can
stack the sortition pool with copies of itself; the essay's requisite variety becomes whoever
had the most child budget.

Fix: count only router-chosen decisions (propensity from the actor's learner) toward eligibility.

## Minor

### 10. Registered observations run at every window close whether or not a card names them — unclean

`pricing.py:281` runs `book.value(o, w)` for every registered observation at each window
close, in the jail, under the tool limits (5 s wall each). Admission costs one novelty trial
once; the kernel then pays up to 5 s per registration per window for the life of the world, and
the preflight accepts `time.sleep(4)` (`test_c3_connectors_observations.py::test_finding_8…`:
five observations no card references all run). One hundred admitted observations is eight
minutes per window close, on the critical path of the loop.

Fix: value only observations a live card names (plus those under an open trial); retire the
rest at `max_lifetime_windows`.

### 11. An incumbent with no money buys compute through a fresh child — unclean

A broke incumbent that requests a never-invoked assembly as a child has that child's model call
paid from the protected novelty share, with the parent choosing the task
(`test_c3_child_judge.py::test_finding_13…`: available 0, the child's call spends 6 micro of
protected compute). Small per call, but it inverts the reserve's purpose: novelty compute is
for unhistoried *actions*, not for incumbents' subcontracting.

Fix: a child's cost ceiling should be bounded by the parent's own `available_for`, never by the
reserve.

### 12. Response headers are not bounded by `max_bytes` — unclean

`world/connector.py:215-221`: `max_bytes` bounds the body read; `http.client.getresponse`
buffers every header first (its own limits allow ~100 lines of 64 KiB). A hostile origin makes
the proxy read 5.4 MB per call at the connector price
(`test_c3_transport.py`). A slow body correctly hits the deadline (2.04 s in a probe).

Fix: cap total bytes on the socket wrapper (`_DeadlineSocket`) at `max_bytes + header allowance`.

### 13. A fake class transfer confirmed after a loss raises out of `treasury.tick` — unclean

`world/treasury.py:530-532` confirms a pending class transfer by calling
`FakeExchange.class_transfer`, which re-checks availability and raises
`ValueError("insufficient available class cash")` (`exchange.py:634`) when a position lost
value between submission and the confirming tick. Fake rail only, but a rehearsal world dies
(`test_c3_resume_wedges.py::test_finding_9…`).

Fix: catch it in `confirm` and mark the transfer `failed` with a reason.

## What held

The jail probe passes on macOS (`sandbox-exec`): `/etc/passwd` denied, no network; threads, a
3 GB allocation and `listdir /` are allowed, which is harmless there but worth a Linux check.
Keys were not read. Corrupt, foreign and stale `.head` files all fall back correctly; a torn
tail repairs with `ledger.repaired`. The depth and fan-out caps on nested requests held in the
existing tests and my probes. The connector body-in-output guard and the slowloris deadline
hold. Of the
kill-and-resume trials at the first append of each item kind, every non-connector kind reached
(`wake.public`; retirement proposed/ballot/tally and `assembly.retired`;
`charter.approved/activate`; `policy.activated/outcome`; `decision.propensity/contract/emits`;
`spot.inventory`; `order.intent/acknowledged`; `treasury.submitted/confirmed/venice_window`;
`request.child`; `router.retained/drained`; `actor.retire`; `registry.register`; `tool.call`;
`snapshot`; `committee.decision`; `charter.deferred`; `price.proposed`; `router.created`;
`epoch`; `immune.window`; `price.window`; `novelty.window`; `exposure.settled`;
`meta.consequence`; `cascade.sibling`; `upward.release`) resumed to an identical summary; the
six connector-sequence kinds diverged as described in finding 2. `novelty.grant` and the
observation items (`observation.preflight`, `io.call observation.run`) were not reached in the
scripted run; the observation case is covered by the journal-level tests of finding 2.

## Answers

Is the intended functionality there and what is broken? Most of it is: the ledger, the jail,
the router, the lot table, the treasury legs, the connector proxy and the resume machinery do
what the spec says, and thirty kill-and-resume points come back identical. What is broken is
concentrated at the seams round two added: composition lets a parent buy a judge and its
subject (1), the recovery journal does not know that the jail is not the world (2), one refused
amendment silently ends retirement (3), and a live world cannot be killed (6); the spot books,
the head file and the numeric-body rule each have one path that ends the world or the feature
(4, 5, 7). Is it the factory the essay describes? Not yet on the point the essay cares most
about: the sensory organs are supposed to be adversarial and outcome-only, and the delegation
is supposed to be drawn by sortition with mutual anonymity; today a paying assembly can appoint
its judge, back-date its skill, seat confederates and vote on its own retirement (1, 8, 9).
Fix those four and the seams in 2–7, and the machine on this branch is a credible Class 3
rehearsal; as it stands it is a Class 2 factory with a governance layer its own population can
route around.

## Test run

`uv run pytest tests/audit/ -o addopts=""`: `21 failed, 262 passed in 1103.71s (0:18:23)` — the 21 failures are the `test_c3_*` reproductions above; every pre-existing audit test passes.
