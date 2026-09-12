# Defect audit — Claude Fable (question C only)

Cold read of `factorylab/` and `tests/`, essay first. I answer only **C: will it
run** — bugs, races, crashes, money paths that lose or double-count, ledger seal
and chain, key handling, resume, the sandbox on Linux, x402, the reconciler. I do
not opine on Class-3 fidelity. Each finding is tagged **[BREAK]** (stops or
corrupts the world) or **[MONEY]** (real-dollar loss/misattribution). Where I ran
a script it says *reproduced*; otherwise *by reading* (often corroborated by the
build log). Scratch scripts exercised the sandbox, metering, the ledger chain and
the x402 parser against a live checkout.

Positive note first: the **x402 quote parser is robust**. I fuzzed non-402
bodies, missing/`accepts`-not-a-list, >2²⁵⁶ amounts, negative timeouts, wrong
network, base64 garbage and header-vs-body bignums; every adversarial input
raised `X402Error`, which `Assembly.invoke` turns into a `failed` return
(`cortex/assembly.py:94`). No uncaught exception escapes the parser. Not a defect.

---

## 1. [MONEY][BREAK] The tool "sandbox" is not an isolation boundary; a population tool can read the signing keys off disk and exfiltrate them

**Files:** `factorylab/cortex/sandbox.py:44-85` (`run_python`); the module-parse
blacklist `factorylab/cortex/registration.py:206-217` (`_tool`); keys placed at
the repo root by `factorylab/runtime/cli.py:29-42` (`_load_dotenv`).

Essay (II.IV.b): *"the type of security system endemic to a superdark factory is
based … on a constantly regenerated surface"* — but the kernel promises physics,
and this leaks the kernel itself.

`run_python` sets **only** `RLIMIT_CPU`; it does not set `RLIMIT_AS`,
`RLIMIT_NPROC`, `RLIMIT_NOFILE`, no network namespace, and no filesystem jail
(cwd is a tempdir, but absolute paths are reachable). The only content filter is a
substring blacklist at parse time (`"import socket"`, `"subprocess"`, …), which is
trivially bypassed with `__import__("so"+"cket")` or `importlib.import_module`.

*Reproduced:* a population tool whose `code` passes `_tool` validation opened a
`0600` key file owned by the same user and returned its contents, and obtained the
`socket` module in the same run:

```
rc 0 out {"stolen": "SECRET-KEY-MATERIAL-0xdeadbeef", "has_socket": true}
```

I also confirmed `RLIMIT_AS`/`RLIMIT_NPROC` are effectively unbounded inside the
sandbox and that `/etc/hosts` is readable.

**Failure scenario (funded droplet):** `reserve.key` and `hyperliquid.key` sit at
the repo root, `0600`, owned by the service user; the sandbox subprocess runs as
that same user. A hostile tool — exactly the "severity-0 bug at scale" and
adversarial minority the essay wants — reads both private keys and posts them over
a socket. The reserve (Base USDC) and the Hyperliquid account are then drained by
an outside party. This is not the factory's own doing; it is a design hole that
defeats every money invariant. Fork bombs / memory exhaustion (no NPROC/AS caps)
are a lesser version of the same defect.

**Fix (no factory demotion):** run the sandbox under an OS jail — a locked-down
namespace/seccomp profile or container with no network, a read-only bind of only
the tempdir, and `RLIMIT_AS`/`RLIMIT_NPROC`/`RLIMIT_NOFILE` set; stop relying on
the substring blacklist. Separately, never leave signing keys readable by the same
uid that executes population code (dedicated key-holder process / different uid).
The sandbox docstring already admits "no network isolation … on macOS"; the gap is
real on Linux too, where only CPU is limited.

---

## 2. [MONEY] The consequence fill cursor starts at epoch 0, so at launch every historical venue fill is booked as the factory's own P&L and lots

**Files:** `factorylab/settlement/consequence.py:149-151` (`FillCursor.__init__`,
`self.since_ns = 0`), poll at `:154-190`; wiring in `factorylab/runtime/loop.py:598`
(`self.consequence_fills = FillCursor(self.ledger)`) and the TICK branch
`loop.py:1205-1217`, where `LiveVenue.on_tick` fills are **filtered out**
(`if we.kind is not WorldEventKind.FILL`) and `consequence_fills.poll` becomes the
*sole* fill source that settles into the wallet via `_settle_exchange_effects`.

Essay (I.III): realized consequence must reflect *"what actually paid off in the
wallet"* — not another trader's history on the same address.

`FillCursor` polls `exchange.fills(0)`. `HyperliquidExchange.fills`
(`world/exchange.py:706-733`) calls `user_fills_by_time(address, 0)` — **all** fills
the venue retains for that account. On the first tick these are emitted as `Fill`
world events; `_settle_exchange_effects` (`loop.py:1644-1657`) settles their
`realized_usd`/`fee_usd` into the wallet and `ReturnConsequences.observe` opens
FIFO lots for them. Note the sibling `LiveVenue` was fixed to start at launch
(`loop.py:624`, `last_fill_ns=self.clock.now_ns`), but the source actually used
(`consequence_fills`) was not.

**By reading, corroborated by the build log:** the T2 acceptance entry and the run-6
entry both record "Two fills at the start were the experimenter's manual test order,
picked up because the fill cursor starts at zero rather than at launch (to fix in
the reconciler)." It is still `= 0`. The funded main wallet already held real USDC
and prior test orders, so at launch the factory will ingest that history as its own
realized P&L and open phantom lots — corrupting the wallet balance, every
`return_paid_off` settlement, and the drawdown/wallet_up predicates.

**Fix (no demotion):** initialize `FillCursor.since_ns` to the launch timestamp
(as `LiveVenue.last_fill_ns` already is), persisted in the genesis/first snapshot,
so only post-launch executions are attributed.

---

## 3. [BREAK][MONEY] `resume` takes no lock; two concurrent resumes are two writers on one wallet and fork the hash chain into an unrecoverable ledger

**Files:** `factorylab/runtime/resume.py:432` (`resume_runtime`) and
`factorylab/kernel/ledger.py:145-179` (`Ledger.reopen`) — neither takes an OS lock;
contrast `factorylab/runtime/treasury_cli.py:158-160`, which *does* `fcntl.flock`.
Only operator discipline guards it (`deploy/README.md:253-254`: "Never run a
restored funded copy alongside the original").

Essay (III): the flash-crash lesson is that a hard cast, not vigilance, halts the
cascade — yet single-writer safety here is left to a prose warning.

*Reproduced:* two independent `Ledger.reopen` handles on one file both succeed and
both append; the chain forks and a later reopen fails verification:

```
two concurrent reopen handles obtained: True
file now has 4 items; both processes appended to one ledger
reopen after interleave raised: LedgerIntegrityError ledger chain or manifest hash differs
```

**Failure scenario:** a hung/slow process not yet reaped plus a manual `resume`, or
the README's own backup-restore/rehearsal path, yields two live runtimes. Before the
chain even corrupts, both submit venue orders and treasury withdrawals against the
one Hyperliquid wallet (double-spend / conflicting nonces). The interleaved appends
then make the ledger permanently fail `verify()` — the world is bricked and only a
human editing the ledger (a covenant breach) could revive it. `run` is protected
(ledger created `O_EXCL`, `ledger.py:117`); `resume` is not.

**Fix (no demotion):** take an exclusive `flock` on the ledger (or a sidecar
`.lock`, as `treasury_cli` does) for the life of `run`/`resume`, refusing to start
if held. This is physics-neutral single-writer enforcement, not intervention.

---

## 4. [MONEY] Metered vendor overruns are silently swallowed, so the wallet under-books real spend and "metering before return" / "death at zero" stop being real-money guarantees

**Files:** `factorylab/world/metering.py:82-89` (on `actual > ceiling`, commit the
*ceiling* and return `overrun`); `MeteredModel.complete:111-129`;
`cortex/assembly.py:96-97,130-139` and `runtime/loop.py:1977,1996` use
`metered.cost` (= ceiling). Nothing anywhere reads `.overrun` (grep confirms only
`metering.py` mentions it).

Essay (I.II): the token budget is *"the only kill switch greater than the teardown
of a factory kernel"* — but only if the wallet tracks money actually spent.

*Reproduced:* a provider reporting `$0.90` against a `78 µUSD` ceiling committed
only `78`, leaving `overrun = 899922` uncharged:

```
committed cost = 78 overrun = 899922
wallet believes it spent 78 micro, vendor billed 900000
Assembly.invoke references .overrun: False
```

The comment at `metering.py:84-86` promises to "settle the remainder as a debt and
lower future ceilings" — no code does. `Wallet.check_conservation()` stays true
(internally consistent), but the wallet over-reports its balance versus the real
credit account, so the factory keeps "affording" compute it has already overspent,
and the reconciler will log `reconcile.drift` once the gap exceeds `$0.50`
(`runtime/live.py:213-218`). Trigger in practice: any vendor whose reported
per-request cost exceeds the table-derived ceiling (reasoning-token surcharges,
live-price drift on a population-registered model, Venice reported cost above the
registered estimate — the OpenRouter/Venice path uses `MeteredModel`, not the
quote-pinned `X402MeteredModel`).

**Fix (no demotion):** debit the true `actual` (reserve a larger ceiling, or book
`overrun` as an immediate additional debit/settlement) so the wallet cannot claim
money the vendor already took; or refuse to return a result whose cost exceeds the
reservation rather than clamping it.

---

## 5. [BREAK] A torn final ledger append is unrecoverable; a crash or `ENOSPC` during the last write bricks the world and loops `resume` forever

**Files:** `factorylab/kernel/ledger.py:268-269` (`_tokens` raises
`LedgerIntegrityError("incomplete ledger")` on any line lacking a trailing
newline), reached by `Ledger.reopen:157-179`; append fsyncs one line at a time
(`ledger.py:249-256`); systemd maps failed resume to exit 1 → restart-with-backoff
(`deploy/README.md:181-192`).

Essay (II.II): a well-designed hard world is one where *"irreversible harm is
impossible"* — a torn tail is exactly irreversible harm from outside the factory.

*Reproduced:* truncating the final line of a healthy scripted ledger makes resume
fail permanently while a clean ledger resumes fine:

```
resume torn exit 1  ->  factorylab resume: recovery unavailable
resume clean exit 0
```

**Failure scenario:** power loss, an OOM-kill, or `ENOSPC` on a year-long droplet
leaves a short final line (a partial write on a full disk is the realistic case).
`_tokens` then rejects the whole diary; `resume` returns 1; systemd restarts and
resume fails identically, forever. Recovery requires a human to trim the byte
tail — a covenant breach — defeating "a droplet reboot must not kill the factory."

**Fix (no demotion):** make `_tokens`/`reopen` tolerate a single trailing partial
line by discarding it after confirming it is the last record and not a complete
authenticated item (write to a temp segment + atomic rename, or a length-prefixed
framing), so a torn tail loses at most the last un-fsynced item instead of the
world.

---

## Other observations (lower severity, by reading, not individually reproduced)

- **[MONEY] Router replacement re-uses the learner id, so post-replace late
  rewards train the wrong learner.** `_build_router` (`loop.py:903`) reuses
  `lid = f"router:{kind}"` on `replace=True`; `_deliver_returns`
  (`loop.py:3110-3133`) keys delivered feedback by learner id and feeds the fresh
  learner outcomes from decisions the *old* learner made. Mis-attribution, not a
  crash.
- **Committee vote invocations bypass the insolvency accounting.** `_hold_vote`
  (`loop.py:2736-2799`) invokes up to five seat assemblies (metered) but does not
  set `_compute_routed`/`_compute_unaffordable`, so a wallet drained by voting is
  invisible to the `insolvency:compute` streak (`loop.py:1329,1394-1407`). Spend
  path outside the death detector; low.
- **[MONEY] Fills after death in one batch are dropped from wallet accounting.**
  `_settle_exchange_effects` (`loop.py:1642-1643`) returns on the first
  `wallet.dead`, skipping later fills/funding in the same event batch. World is
  terminating, so mostly benign, but realized loss can be under-booked at the
  exact death boundary.
- **Key-file mode check can trap resume.** `_load_dotenv` (`cli.py:36-41`) raises
  if `hyperliquid.key`/`reserve.key` are not exactly regular `0600`; on resume this
  becomes exit 1 and a systemd restart loop with no diagnosis (`cli.py:544-546`).

## What I could not fault (checked, clean)

- x402 quote parser under adversarial headers (reproduced above).
- Wallet integer arithmetic and conservation: `reserve`/`commit`/`settle` reject
  floats/negatives and over-reservation (`kernel/wallet.py`), death is `<= 0` and
  final.
- Ledger hash chain and seal: Fernet at rest, key released only by final
  `Termination`, six tamper modes covered; the verified-prefix cache
  (`ledger.py:280-311`) is authentication-preserving.
- FIFO lot accounting uses exact `Fraction` and floors once (`settlement/lots.py`);
  the reward-hacking review in `settlement/REWARD_HACKING.md` matches the code.

Scope note: this is a $100 world; nothing above threatens liveness of the scripted
world (it ran 120 events clean: conservation and verify true). Findings 1–3 are the
gating risks for the *funded* world specifically.
