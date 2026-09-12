# Cold audit — defects (Claude Opus)

Question **C** only. I have not read any other file in `docs/audits/`. Every finding is
marked **[reproduced]** (a script under `/tmp/fl` exercised it against this worktree) or
**[by reading]**. Gate is green here: `ruff` clean, 1497 tests pass in 7m32s; `scripted`
runs 120 world events with conservation and chain verification true. Nothing below is a
Class 3 opinion; everything is *this will break* unless labelled *unclean*.

---

## 1. A population tool reads every key the factory user owns, and has the network [reproduced]

`factorylab/cortex/sandbox.py:55-69`; `factorylab/cortex/registration.py:206-217`;
`deploy/factorylab.service:9,32-34`.

Essay II.I: "the kernel enforces them" — physics, not advice (Ch. II, The Primitive).

`run_python` passes `env={}`, `-I`, a private cwd and `RLIMIT_CPU`, and nothing else. The
child keeps the parent's uid, mount namespace and network. A registered tool whose code is
`open('/srv/factorylab/reserve.key').read()` is accepted by `_tool` (the blocklist is a
substring test on six literals, defeated by `__import__('so'+'cket')`) and returns up to
8 KB of key material as `tool_results`. That result is then (a) written into the ledger as
`tool.call` args, and (b) placed verbatim in the follow-up prompt sent to a third-party
model vendor. I reproduced the read of a decoy 0600 key file and `import socket` inside the
sandbox. On the droplet `hyperliquid.key`, `openrouter.key`, `reserve.key` and
`runs/funded.jsonl.key` are all `factory:factory 0600` — the sandbox runs as `factory`.

**Fix**: run the sandbox under a distinct uid with `unshare -n`, a read-only bind of an
empty root, and `seccomp`; or, minimally, move the key files out of the service's reachable
filesystem (`LoadCredential=` / `systemd-creds`) so that nothing the process can open is a
key. The blocklist should be deleted, not extended — it implies a guarantee it cannot give.

## 2. One non-finite number in a model reply wedges the world forever [reproduced]

`factorylab/runtime/resume.py:86`; reached from `loop.py:2093-2095` (`memory[...] = ret.outputs`)
via `loop.py:1257` `_snapshot`.

Essay II.II: versions "only move forward" — a world must not need a rewind.

`json.loads` maps `1e309` to `inf`. The value is stored in the assembly's private memory,
which is a `_RUNTIME_FIELDS` member, so the next reserve-window snapshot calls
`encode(float('inf'))` and raises `ValueError: nonfinite checkpoint number`. Nothing catches
it: `run()` → `_process_event` → `_snapshot` → process exit. Because the reply is journaled,
`resume` replays it and raises again. I ran it against a file ledger: run crashed, then two
consecutive `resume_world` calls crashed identically. On the droplet this is `Restart=always`
with a 30 s → 15 min backoff, a `failed_resume` webhook every cycle, a world that is neither
alive nor terminated, the diary key never released, and the venue position unmanaged.

**Fix**: reject non-finite numbers where they enter — in `Assembly._parse_json_object`,
`json.loads(..., parse_constant=)` plus a finite-number walk over `parsed` — and make
`encode` coerce rather than raise. A snapshot must never be the first thing that validates
population output.

## 3. Any second writer on the ledger kills the world and releases the seal [reproduced]

`factorylab/kernel/ledger.py:213-224`, `factorylab/kernel/termination.py:389`; no lock
anywhere in `runtime/cli.py` or `deploy/start.sh`.

Essay II.II.b: the kernel is "inviolable from both the inside … and its outside".

`healthy()` compares the file's size and tail to what this process wrote. A second process
appending one item makes it `False`, `Termination.check` returns `ledger_failure`, and
`kill()` publishes `Terminated` **and releases the sealed diary key**. I reproduced the full
sequence: resume a world, let another `Ledger.reopen` append one record, and the first
process's next step returns `terminated: True, reason: ledger_failure,
seal_key_released: True`. The only defence is prose in `deploy/README.md`. An operator
running the documented `factorylab resume` command twice, or starting a restored copy beside
the original, destroys the world irreversibly and un-seals its interior.

**Fix**: take an `fcntl.flock(LOCK_EX|LOCK_NB)` on the ledger path in `Ledger.__init__`/
`reopen` and hold it for the process's life; refuse to start otherwise. Separately, a failed
`healthy()` should be distinguishable from a hash-chain break — an external append is not
evidence that this world's own evidence is corrupt.

## 4. A trading loss during an in-flight treasury transfer crashes the world [reproduced]

`factorylab/world/treasury.py:254`, called from `loop.py:1208` with no handler.

Essay II.IV: "Conservation" is a hard cast; a priced fee is not.

`Wallet.settle` (exchange P&L, funding) changes the balance with no availability check, so
`available` can go negative while the treasury holds principal + fee ceiling. On the next
confirmed step, `reconcile` commits the observed fee and re-reserves the remaining ceiling —
`wallet.reserve` raises `Infeasible`, which propagates through `Treasury.reconcile`,
`Treasury.tick` and `Runtime._process_event` to process exit. I reproduced it with a
two-step rail: `$6.40` wallet, `$5` transfer, `$1` fee ceiling, one `-$0.90` `exchange_pnl`
settlement → `Infeasible: reservation exceeds available balance @ treasury.py:254`. The
`treasury.step_confirmed` item is already durable, so resume replays the step and dies
again — wedge, as in §2. The live CCTP plan is 2–5 steps over minutes; a fill in that window
is the normal case, not the edge case.

**Fix**: `Treasury.reconcile` should reduce the re-reserved ceiling to
`min(remaining, wallet.available)` and mark the transfer fee-starved rather than raise; and
`Runtime._process_event` should treat any `Infeasible` from the treasury as a refusal, not a
crash.

## 5. A torn final record bricks the live world; the backup of the same file repairs it [reproduced]

`factorylab/kernel/ledger.py:268` vs `deploy/backup.sh:23-31`.

Essay I.II: a governor's only remaining move must not be a restore.

`_append` writes one line through a `BufferedWriter`; lines reach 72 KB in the scripted world,
so a kill mid-flush can leave a record without its newline. `_tokens()` then raises
`incomplete ledger` and `Ledger.reopen` refuses forever (exit 1, `failed_resume`, restart
loop). I truncated the last line of a real 6.2 MB ledger: `reopen` refused; the same file
with the incomplete record dropped — exactly what `backup.sh` does — reopened cleanly. So the
authors already know partial records occur, repair them in the backup path, and treat them as
fatal corruption in the live path. The documented recovery (restore a backup) is the one act
the covenant forbids.

**Fix**: in `Ledger.reopen` only, drop a single trailing record that lacks its newline,
ledger the truncation as a `recovery.truncated` item, and keep the strict rule for every
earlier line.

## 6. A vendor's bad usage number leaks a wallet hold on every call, until insolvency [reproduced]

`factorylab/world/metering.py:78` — `actual = cost_of(result)` sits *outside* the
`try/except` that releases the reservation.

Essay II.I.b: the reward line is "thin but stateful" — a hold is state that must close.

If `cost_of` raises (a provider reporting `prompt_tokens: -5`, a string, or an unpriced
substituted `model_id`), the reservation is never committed and never released.
`Assembly.invoke` catches the exception and returns `status: failed`, so the loop continues
while `available` drops by the full ceiling each call and `balance` never moves.
`Wallet.check_conservation()` still returns `True` — it only inspects `balance`. Reproduced:
three calls, `available` 9 997 336 → 9 994 672 → 9 992 008 at constant balance. The end state
is every action infeasible, twenty consecutive unaffordable events, and termination with
`insolvency:compute` — "a factory that starves its compute" for a reason that is a vendor's
JSON, not its own liquidity management.

**Fix**: move `cost_of` inside the `try`, release on failure, and validate
`input_tokens`/`output_tokens` in `OpenRouterProvider.complete` before they reach pricing.

## 7. A `}` inside any string makes the whole return malformed [reproduced]

`factorylab/cortex/assembly.py:152-174`.

Essay II.III.b: producers "learn from evaluator agents through … scoring and propensity".

`_parse_json_object` counts braces without string awareness. `{"verdict":0.9,"rationale":
"cost > 0 }"}` returns `None`; so does `{"action":"hold","note":"a}b"}`. A producer's return
becomes `malformed` (no registrations, no order, `well_formed_rate` down); an evaluator's
becomes a zero conformity **and leaves its producer unjudged, hence censored**. Judges write
prose about JSON and costs constantly. In my 120-event scripted run `censored == verdicts`
(290 each) even with a well-behaved provider; this defect adds a silent, prose-correlated
censorship channel on top.

**Fix**: try `json.loads(s)` first, then use `json.JSONDecoder().raw_decode` from the first
`{` — both are string-aware. Delete the brace counter.

## 8. x402: unbounded authorization lifetime, and no cap on a seller's per-request ceiling [reproduced / by reading]

`factorylab/world/x402.py:205-206` and `:268,301`; `factorylab/world/market.py:258-281`.

Essay II.IV: "The only kill switch greater than the teardown of a factory kernel is $0."

`_requirements` accepts any `maxTimeoutSeconds` below `2**256`; `authorization_typed_data`
sets `validBefore = now + maxTimeoutSeconds`. I fed the parser a quote with
`maxTimeoutSeconds = 10**30` and got an accepted quote whose signed EIP-3009 authorization
expires in the year 10^22. Each request signs a fresh nonce, so a seller can bank N
authorizations and settle them all later; the wallet books one payment per request, and the
divergence surfaces only as a `reconcile.drift` line nobody acts on (`live.py:216`).

Worse, `registration_price` takes the seller's own unpaid quote as the immutable per-request
ceiling when the seller publishes no `/v1/models`. No manifest field bounds it. An assembly
on such a model is feasible whenever `ceiling <= wallet.available`, so a single call to a
seller the population found on the public index can commit ~the whole wallet — the $0 kill
switch, pulled by a stranger, in one request. **[by reading]**

**Fix**: clamp `validBefore` to a few minutes regardless of the quote; add
`treasury.max_per_request_micro` to the manifest and reject any registration or quote above
it in `X402Provider.register`/`authorize`.

## 9. The three-proposal cap does not apply to amendments; each costs five unbudgeted model calls [reproduced]

`factorylab/runtime/loop.py:2452,2494` and `:2740` (`_hold_vote`).

Essay II.IV.a: the charter is "a limited, privileged site of governance".

Amendments are filtered out of `raw` before `parse_proposals`, so `MAX_PROPOSALS_PER_RETURN`
never sees them, and the loop over `amendments` has no cap. Each accepted amendment seats a
committee and invokes up to five assemblies **synchronously, inside the same event**, priced
to the wallet and outside the novelty reserve's accounting. One return carrying 30 amendments
produced 30 proposals and **150 vote invocations in a single event** (reproduced). The only
brake is the novelty reserve's remaining balance at `trial_amount_micro` each — with the
default 10 % share that is dozens per window. The public `proposal_shapes` contract tells the
population the cap is three.

**Fix**: count amendments against `MAX_PROPOSALS_PER_RETURN`, and queue committee votes as
ordinary routed decisions instead of a synchronous inner loop.

## 10. Two unguarded venue calls in the hot path [reproduced]

`factorylab/runtime/loop.py:2069` (`self.exchange.mids()` in `_producer_step`) and
`:1686` (`self.exchange.place(order)` in `_execute_outputs`); also `live.py:287`.

Essay I: the factory must not die for "a reason that is not the factory's own doing".

With a venue that raises on `mids()` or `place()`, the world crashes at these two lines
(reproduced with a fault-injecting `FakeExchange`; tracebacks end exactly there). Everywhere
else the code is careful — `_world_block`, `_observe_positions`, `_equity_micro`,
`Reconciler.snapshot` and `_run_tool` all guard. `HyperliquidExchange.place` catches
internally, so on the live venue only the `mids()` sites bite, and only before the first
successful read — i.e. precisely when `resume` runs during a venue outage.

Related, **[by reading]**: `exchange.py:613-615,647-649` return `_last_mids`/`_last_account`
indefinitely, with no staleness bound and no signal. During a long outage the world trades on
a frozen mid and, worse, `LotTable.resolve` marks open lots to it, manufacturing
`return_paid_off` outcomes that never happened. Bound the age and censor consequences that
depend on a stale mark.

**Fix**: wrap both call sites in `except RuntimeError` with the same fallbacks used elsewhere.

## 11. The manifest validator accepts a `min_ratio` the cascade rejects at runtime [reproduced]

`factorylab/runtime/worlds.py:271` (`min_ratio < 1`) vs `factorylab/runtime/cascade.py:15`
(`min_ratio >= 3`).

Essay II.IV.c: the literature's ratio "start[s] from 3:1 at a minimum".

`worlds/funded.toml` with `timing.min_ratio = 2` passes `factorylab manifest --world funded`
— the documented pre-launch check — and then raises `ValueError: cascade min_ratio must be an
integer >= 3` on the first `Verdict` event. Reproduced for 1 and 2; 3 runs. The manifest is
the architect's one committed move; its validator must be total.

**Fix**: require `min_ratio >= 3` in `WorldManifest.validate`.

## 12. The sandbox has no memory, file-size or process limits [reproduced]

`factorylab/cortex/sandbox.py:47-52` sets only `RLIMIT_CPU`; the docstring claims memory caps
exist off macOS.

A tool allocated 1.5 GB and wrote a 200 MB file outside its temp dir (reproduced). On a
4 GiB droplet that OOMs the host and may take the factory with it; the factory's own
`ReadWritePaths=/srv/factorylab/runs` means a tool can fill the disk holding the ledger,
after which `Ledger.append` fails and the world dies of `ledger_failure`. A forked,
*sleeping* grandchild survives the wall-clock timeout indefinitely (reproduced: alive 8 s
after a 2 s timeout) because `subprocess.run` kills only the direct child; it also holds
stdout open, so every such call burns the full timeout and returns `{"error": "timeout"}`.

**Fix**: set `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NPROC` in `_limits`, `os.setsid()` in the
child and `os.killpg` on timeout.

## 13. Resume and wake load the entire diary into memory, every time [reproduced, measured]

`factorylab/kernel/ledger.py:181-185` (`_recovery_items`), `:349` (`aggregate`),
`runtime/wake.py:44,62,163`.

Essay I.II: darkness is read from the wake — so the wake must survive the world.

`Ledger.reopen` + `_recovery_items` decrypts every item into a list; each of the five wake
views decrypts the file again, and `timing()` a seventh time. Measured: a 6.2 MB ledger costs
0.37 s and **56 MB of RSS** — a ~9× amplification. Run 5's real shape (896 ledger events for
~200 world events, ~625 B/item) gives ~40 KB of diary per world event; at the manifest's 10 s
tick that is ~350 MB/day, so a 4 GiB droplet stops being able to `resume` after roughly
a fortnight, and the hourly wake (`TimeoutStartSec=15min`) goes dark before that. There is no
windowing, compaction or incremental verification anywhere, and `deploy/README.md` budgets
disk but not RAM or CPU for a launch intended to be unbounded.

**Fix**: stream `_tokens()` and `aggregate` instead of materialising lists; keep a running
aggregate in the snapshot so the wake reads the tail since the last snapshot only; verify
incrementally from the last verified head.

## 14. Metering computes an overrun and throws it away [by reading]

`factorylab/world/metering.py:87` — the comment says the runtime "settles the remainder as a
debt and lowers future ceilings". `grep -rn overrun factorylab` returns only this file.

Essay II.I: metering before return is a hard cast, not an estimate.

Whenever a vendor bills above the reserved ceiling, the wallet is charged the ceiling and the
excess is silently forgotten. Conservation still reports `True` because the wallet's identity
never sees the unbooked amount. Note also that `OpenRouterProvider.complete` never requests
`usage.include`, so `cost_micro` is almost always `None` in live runs and every cost is
table-derived — the overrun path is the only place a real vendor price could correct the
table, and it is inert.

**Fix**: either book the overrun as a debt at the next affordable moment, or raise rather than
under-charge. Do not leave a comment describing a mechanism that does not exist.

## 15. Resume never checks which venue account it reattached to [by reading]

`factorylab/runtime/resume.py:384-388` compares only the adapter's `name`
(`"hyperliquid"`); the venue address comes from `hyperliquid.key` and appears in no manifest
field and no snapshot check. `LiveRail` checks the *reserve* address against the manifest —
the venue account has no equivalent.

Essay II.II.b: a changed world "is a new factory from a new v0".

Restoring a backup onto a host that still holds a rehearsal key, or copying the wrong key
during the documented `scp` step, silently points a resumed world at a different account. The
reconciler's $0.50 drift check is the only signal and it merely logs.

**Fix**: pin the expected venue address in the manifest (it is already hashed into genesis)
and refuse resume on mismatch, as `LiveRail` already does for the reserve.

---

## Unclean, not broken

- `settlement/scoring.py:21` names `1 - (q - y)^2` "brier". The direction is right everywhere
  it is used, but `charter/charter.py:102` renders "Mean Brier score … above zero" to the
  population, which is false under the standard definition; an evaluator that optimises the
  charter text literally optimises backwards.
- `runtime/cards.py:82`: a previous-window median of 0 yields `scale = 1.0` with `hi = 0`,
  so a 300 µUSD cost becomes violation 300 and every producer verdict clips to 0.
- `wake.py:236-243`: a crash between `mkstemp` and `os.replace` leaves a `.wake-*` file in the
  published directory.
- `runtime/loop.py:2781`: committee votes are counted in `stats.invocations` but not in
  `window.invocations`, so `well_formed_rate` ignores a whole class of model calls.

*(2 650 words.)*
