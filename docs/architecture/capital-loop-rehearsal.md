# The hybrid capital-loop rehearsal

*The Superdark Factory*, II.IV: "a continuous, reciprocal flow of capital is an
objective requirement." Edition 5 made a confirmed Venice conversion spending
authority (financing, never income). This mode rehearses that loop end to end with
real Venice credit while the population trades on testnet money:

    testnet profit -> real Venice credit -> spending authority -> Venice seats think with it

World: `worlds/edition6-capital-loop.toml` (edition 6's fourteen-seat roster, every seat on
OpenRouter at genesis; the seated families' Venice routes are on the menu, for seats to
move onto credit the factory buys). It replaced `worlds/edition5-capital-loop.toml`, which the kernel no
longer loads: since Wave 5a a world that seeds judging must hold at least as many
evaluator seats as producer seats, on at least three model families (docs/manifest.md,
"The evaluator population"). Code: `HybridRail` in
`factorylab/world/treasury_rails.py`, `FakeHybridRail` and the two-leg bookkeeping in
`factorylab/world/treasury.py`.

**Real money moves.** Every confirmed conversion spends $5 of real USDC from the Base
mainnet reserve. Nothing else is real: trading, the Base Sepolia reserve and the
shadow leg are testnet money.

## Two legs, shadow first

`treasury.transfer direction="to_venice" usd="5"` plans two steps. The transfer is
confirmed only when both are, and financing is booked once, then:

1. **`shadow_send`**: a Hyperliquid testnet `usdSend` of exactly $5 from the venue's main
   account to `treasury.venice_shadow_sink`. It is signed like the class transfer
   (EIP-712 user-signed action at the transfer's persisted millisecond nonce) and
   confirmed by the venue's own ledger row for that sender, sink, amount and nonce.
   This is what makes the observed pots pay for the credit.
2. **`venice_top_up`**: the fixed $5 x402 top-up from the mainnet reserve, proven by
   the canonical `AuthorizationUsed` debit exactly as an ordinary conversion is, but
   read on Base mainnet.

The shadow leg goes first because it is cheap and a failure there spends nothing real:
the top-up's authorization is not even prepared until the shadow receipt is ledgered.
The other order could leave real USDC spent against a testnet leg that never pays.

## Failure matrix

| Where it fails | What the ledger holds | What happens next |
| --- | --- | --- |
| Preflight (tranche not $5, window cap spent, absolute cap would be exceeded, a hybrid strand still owed, venue withdrawable short, mainnet reserve short or below its floor, sink `missing`, agent key, `VENICE_API_KEY` set) | `treasury.refused` with the reason | Nothing held, nothing signed |
| Venue rejects the shadow send (first attempt) | `treasury.failed`, `stranded_micro` 0 | Hold released; no authorization exists; nothing real spent |
| Shadow send outcome unknown | `treasury.pending` | Same signed action at the same nonce is resent (a venue nonce executes once); confirms on the ledger row, even a late one, or fails as `shadow send nonce expired unexecuted` after the nonce window |
| Shadow row charges a fee | `treasury.pending` "shadow send charged a fee; the conversion needs an operator" | Stalls publicly; the preflight's `missing`-sink refusal exists to prevent it |
| Top-up cannot be prepared after the shadow paid (no quote, reserve at its floor, payee differs, a cap reached) | `treasury.pending` each attempt, then `treasury.failed` status `stranded`, `recoverable`, reason `Venice top-up not prepared within treasury.forward_wait_windows` | Hold stays; no new `to_venice` may start beside it; when the slot is free a tick re-prepares the top-up (`treasury.recovered`), within the absolute and window caps. The shadow leg is never re-sent |
| Top-up submission outcome unknown | `treasury.pending` "submission outcome unknown" | **Never resubmitted.** The step is poll-only: it confirms on the `AuthorizationUsed` debit, or strands recoverably only when a **finalized** Base block is past its `validBefore` and the receipt scan covered every block up to it with no debit. The runtime clock is never consulted. Recovery first polls the superseded authorization (a late debit is booked and nothing new is signed), then prepares a *new* authorization, counted against the caps. A kill during the submission resumes as unknown too (the journal refuses to replay a poll-only send) |
| Debit found, credit read | `treasury.confirmed` and `treasury.financing`, the credit reads and `credit_shortfall_micro` in the evidence | Financing is booked on the canonical debit, as the ordinary rail books it. Metered spend is an estimate, the balance rounds down and seats spend the credit through the finality wait, so a shortfall is recorded, not held |
| Debit found, but the credit rose by less than $5 − metered spend − $0.25 (`CREDIT_TOLERANCE_MICRO`) | `treasury.pending` "Venice credit short of the tranche; financing held unresolved", with the numbers in its carry | Only a missing credit, not spend or rounding, is held: no financing is booked, the principal stays held, the step keeps polling and is never tested for expiry or re-authorized. It confirms if the credit shows up; otherwise it needs an operator |
| Kill anywhere | Checkpoint + journal on disk; the live world is **not** resumed | `factorylab resume` refuses a live hybrid world (the opt-in is not checkpointed) and the rehearsal runner has no resume. Follow "After a crash": read what is outstanding, wait, and launch a fresh run; its launch check refuses while any earlier authorization can still settle. Journal replay of both legs is exact (`tests/runtime/test_resume.py`, hybrid cut test, on the scripted world), but a live hybrid world is never replayed in place |

Every accepted authorization, re-authorizations included, is counted in
`venice_authorized_micro` and ledgered as `treasury.venice_authorized` before it can be
signed. A new conversion is admitted only while authorized + $5 × (in flight + stranded)
+ $5 stays within `max_venice_total_usd`; an authorization is prepared only while
authorized + $5 does, and a recovery or a late-window authorization is charged to the
window it is accepted in. Expired authorizations stay counted: the bound is on what was
ever authorized, not on what the chain has shown.

## Accounting

A hybrid conversion books exactly what an ordinary one does: the principal hold is
released, `wallet.settle(+5, "financing")` adds spending authority, and
`converted_from_principal_micro` grows by 5. The observed pots move by venue −$5 and
Venice +$5, so their total is unchanged, as with an ordinary conversion (reserve −$5,
Venice +$5); the reconciler's discrepancy moves by the financing alone in both modes.
`treasury.financing` names the venue as the source (`source: venue_perps`) and the real
payer beside it (`paid_from: base_mainnet_reserve`, `shadow_sink`). The real mainnet
reserve appears in the pots view as `venice_reserve`, never summed into the total.

## Manifest

```toml
[treasury]
reserve_address = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
venice_network = "base-mainnet"          # refused on a mainnet venue
venice_shadow_sink = "0x...dEaD"         # required; PLACEHOLDER, confirm before a live run
max_venice_per_window = "10"             # two conversions per reserve window
max_venice_total_usd = "10"              # required: every authorization ever, counted
venice_reserve_floor_usd = "0"           # required; PLACEHOLDER, checked at launch
venice_pay_to = "0x2670b922ef37c7df47158725c0cc407b5382293f"  # required: Venice's payee
```

All five hybrid keys are absent from the canonical hash when unset, so every other
world keeps its manifest hash; any of them without `venice_network` is refused. The
world's roster differs from the one its charter was ratified on, so it carries no
`roster_sha256`; testnet admission does not check it, and a funded launch of this
roster would need re-ratification (`scripts/ratify_charter.py`).

Three bounds stack. `max_venice_total_usd` is the absolute cap within one world, kept
across kills. `venice_reserve_floor_usd` is read on chain before every authorization,
so it also bounds a fresh run whose counter starts at zero. The mainnet reserve's
balance is the last bound: fund it with only what the rehearsal may spend. The
per-window cap is a rate, not a bound: the reserve window is the novelty window (`1m`
here), and the one-transfer slot and Base finality (a top-up confirms on a *finalized*
debit, typically 15–20 minutes) serialize conversions far below it anyway.

The payee is pinned: a Venice quote naming any other `payTo` is refused before an
authorization is built, and `top_up` refuses to sign a journaled reference whose
payee differs. The pinned value was recorded from Venice's live unpaid quote on 11
September 2026 (docs/research/venice.md) and cannot be verified offline.

The manifest alone never switches the mode on. `factorylab run` and `factorylab resume`
refuse a live hybrid world; only `scripts/edition4_rehearsal.py --capital-loop` passes
the runtime opt-in, and the opt-in is not checkpointed. A hybrid world interrupted
mid-run is therefore never resumed: see "After a crash".

The floor is only a cross-run bound if it sits close to the balance, so the runner
checks it against the chain at launch: it reads the reserve's USDC keylessly (a public
`eth_call` on the manifest's `reserve_address`) and refuses unless
reserve − floor ≤ `max_venice_total_usd`, printing both numbers
(`capital_loop_launch_check`). A $1 floor on a $15 reserve with a $10 cap is refused.

Expiry is the same rule on both rails, the ordinary mainnet `LiveRail` included: a
top-up authorization is abandoned only when a finalized Base block is past its
`validBefore`, the USDC contract's `authorizationState(reserve, nonce)` is false at
that block (so a lagging RPC returning no logs cannot fake it), and the log scan
covered every block up to it. The runtime clock is never consulted.

## Before a live run

1. **Rehearse for free first.** The fast harness runs both legs on scripted custodians:

       uv run python scripts/fastloop.py run --provider scripted --ticks 20 --seeds 1 \
         --world worlds/edition6-capital-loop.toml

2. **Confirm the sink.** Replace the `0x...dEaD` placeholder or accept it. It must be
   outside every pot this world observes and must already exist on Hyperliquid testnet
   (send it $1 of testnet USDC once by hand). Read-only check; the answer must not be
   `missing`:

       curl -s https://api.hyperliquid-testnet.xyz/info -H 'Content-Type: application/json' \
         -d '{"type":"userRole","user":"0x000000000000000000000000000000000000dEaD"}'

3. **Read the mainnet reserve without a key** (a public `eth_call`, no signature):

       uv run python -c "from factorylab.world.x402 import usdc_balance, eth_balance; \
       a='0x1228e5620944a79D268Afc7522E00891526EdEBb'; \
       print('usdc_micro', usdc_balance(a), 'eth_wei', eth_balance(a))"

   With the reserve key loaded, `uv run factorylab reserve status` also reads the
   Venice credit (a SIWE sign-in, which moves nothing). Record all three numbers.
   Fund the mainnet reserve with the most you are willing to spend and no more.
4. **Set the floor.** Put `venice_reserve_floor_usd` at the reserve's USDC (step 3)
   minus the total this rehearsal may spend, and `max_venice_total_usd` at or above
   that difference; the runner re-reads the reserve at launch and refuses unless
   reserve − floor ≤ `max_venice_total_usd`.
5. **Confirm the payee.** An unpaid quote signs nothing and moves nothing; its Base
   `payTo` must equal `venice_pay_to`:

       curl -s -X POST https://api.venice.ai/api/v1/x402/top-up | \
         python -c "import json,sys; print([a['payTo'] for a in json.load(sys.stdin)['accepts'] if a['network']=='eip155:8453'])"

   If Venice changed its payee, verify the new one out of band before updating the key.
6. **Check the venue.** The venue's perps `withdrawable` on testnet must cover $5 per
   conversion; the venue key must be the main wallet (not an agent) and
   `VENICE_API_KEY` must be unset (top-ups credit the reserve wallet). The venue and the
   reserve must be different keys. Never send exactly $5 to the sink by hand: a shadow
   send's ledger row without a nonce is matched on sender, sink and amount.
7. **Launch only from a checkout that contains this runbook's code, and never while
   any other checkout, worktree or agent session on this host could launch one.** The
   guards below (the reserve lock, the last-run record, the settlement bound, the
   strict diary read) live in the runner itself. An older checkout's runner takes no
   lock and writes no record: it would run beside a guarded run on the same reserve,
   each authorizing against a floor that cannot see the other's unsettled
   authorizations, and the next guarded launch would not know it had run. The lock
   excludes only runs that take it.

   (The `--capital-loop` flag is the runtime opt-in `factorylab run` never passes; it
   admits `to_venice` only; every other treasury route and every x402 purchase stays
   denied, and the reserve key is cleared from the environment once the rail has
   captured its signer):

       uv run python scripts/edition4_rehearsal.py --world worlds/edition6-capital-loop.toml \
         --capital-loop --source-root "$PWD" --out work/capital-loop/<run> \
         --duration 150m --cap-usd 5

   (`--source-root` must name the checkout whose `factorylab` is imported; `--cap-usd`
   bounds model spend only, not conversions. A longer run spends more model money
   under the same cap.)

   `report.json` carries a `capital_loop` section naming the network, sink, window cap,
   total cap, floor, payee and reserve, and the launch check's numbers. Before building
   anything the runner, in this order:

   1. takes the reserve's lock (see "One run per reserve") and refuses
      `capital_loop_reserve_locked` while another capital-loop run holds it;
   2. refuses a run too short for a conversion to settle inside it (see "How long a run
      must be"), `capital_loop_duration_below_settlement_bound`;
   3. reads the reserve and refuses unless reserve − floor ≤ `max_venice_total_usd`;
   4. reads every sibling run directory of `--out` that kept a diary, each
      `--previous-run DIR`, and the reserve's last recorded run wherever it is, and
      refuses while any of their top-up authorizations could still settle. A run whose
      ledger key file is missing, or whose diary does not read whole under its own key
      (`run_ledger_unreadable`: another run's key in a copied or restored folder, a
      corrupted, removed or reordered line), is refused too, since what cannot be read
      may be a live authorization. A last line missing its newline still counts when
      it decrypts and chains; only one that does not (a crash mid-append) is skipped.
      The reserve's recorded last run must not read empty (header only, or torn at its
      first record): a run records itself only after its launch items exist, so an
      empty diary there is truncation (`recorded_run_ledger_empty`). Any other diary cut
      exactly at a line boundary cannot be told from a shorter one by its file alone;
      the last-run record, which makes every launch read the last run, and the chain
      reads of every authorization found are the defence there.

### How long a run must be

A conversion settles, or provably dies, only once a *finalized* Base block is past its
debit or past its `validBefore`, and the rail sees that on its next tick. The signer
stamps `validBefore` from this host's clock, at most 600 s after it prepares the
authorization. So one conversion's settlement horizon, in Base's own time, is:

- **600 s**, the validity cap the signer always applies. Never the quote read at
  launch: the signer obeys whatever quote it is handed later, so a launch-time quote
  would bound nothing.
- **plus twice how far the finalized block is behind this host's clock**: the host's
  time now, less the timestamp of the one finalized block read at launch. That single
  difference already holds Base's finality lag, any lead of the host clock over the
  chain (a `validBefore` stamped by a fast clock lies that much later in chain time),
  and any staleness of the node that answered (an old finalized block only lengthens
  it). It deliberately reads no `latest` block: a "latest less finalized" lag pairs two
  reads that a load balancer can send to two nodes, and a stale `latest` would shrink
  the lag toward zero and the bound with it. Doubling is the allowance for the lag
  growing during the run (16 minutes measured, 32 allowed). A finalized block that
  cannot be read, or that is not behind the host's clock, refuses the launch
  (`finality_lag_unreadable`).
- **plus one tick** for the rail to look.

The run commands the conversion, and AGENTS.md rule 12 (essay II, IV.c) asks an inner
loop to settle at least 3× faster than the outer loop that commands it, so the runner
refuses a run *planned* shorter than **three** horizons. The planned length is
`--duration`, or `--ticks` × the tick when that is shorter; the admission cap, a
failure, a signal or a kill can still end a run sooner, and the bound says nothing of
those. With the numbers of 23 September 2026 (no clock lead, a lag of about 16 minutes, the
rehearsal's 10 s tick) the bound is 3 × (600 + 1,920 + 10) s = 7,590 s, about 127
minutes; `--duration 150m` leaves room. The numbers are printed in
`capital_loop_launch_check.settlement` and kept in the report.

No length makes a late conversion impossible: a top-up submitted in roughly the last
horizon of a run still ends `submitted`. The bound makes that the tail, not the rule,
and the end of the run says so (see "After the run").

### One run per reserve

The floor check reads the chain, and the chain cannot see an authorization that was
signed but has not settled; each run also counts only its own authorizations. Two runs
on one reserve started close together could therefore each authorize past the floor.
So a capital-loop run holds a lock on its reserve from before its launch check until
it returns:

- The lock is `~/.factorylab/capital-loop/<reserve address, lowercase>.lock`, an
  exclusive `flock` on a close-on-exec descriptor (the kernel ledger's own writer-lock
  pattern). The OS releases it when the run returns or its process dies, however it
  dies, so a crash never wedges it and there is no pid file to clear by hand. It is
  keyed by the reserve and the operator account, not by `--out` or the checkout, so
  every worktree resolves the same lock. `~` here is the account's home directory as
  the password database names it, not `$HOME`: a launch with another `HOME` still
  finds the same lock and record.
- Beside it, `<reserve>.last-run.json` names the one run that last held the reserve. A
  run records itself once its checks passed and its diary exists, before anything can
  sign (the record and then its directory are flushed with `F_FULLFSYNC` on macOS,
  `fsync` elsewhere, so the record survives a power loss); the next launch reads that
  run wherever its directory is. The first launch that ever creates a reserve's lock
  file writes a record naming no run, before it takes the lock. From then on a lock
  file with no record beside it refuses every launch (`capital_loop_last_run_missing`):
  deleting the record cannot switch the last-run check off. A record with no lock file
  beside it refuses too (`capital_loop_lock_file_missing`), and the launch does not
  recreate the lock file.
- A lock is only the lock while its file is the one the path names: after `flock`
  succeeds the launch compares the descriptor's inode and device with the path's, and
  refuses on a mismatch (`capital_loop_lock_replaced`). An `flock` belongs to an inode,
  not a name. If the lock file were removed while a run held it, a new launch would
  create and lock a fresh file at the same path, and the two runs would each hold "the"
  lock.

Reading only the sibling directories of `--out` is **not enough** on its own, even
with the lock: the lock ends with its run, and a run can end (or die) with an
authorization still live for up to its validity window plus finality. A later run
launched with `--out` in another directory would not see it, and the $5 it could still
settle is invisible to the floor. The last-run record closes that: every earlier run
was proven settled or dead by the launch after it (a dead EIP-3009 authorization never
revives), so the last holder is the only earlier run that can still be live, and it is
always read. Siblings and `--previous-run` are still read too.

What the lock does not cover: another machine, or another operator account (another
home directory), on the same reserve; `factorylab reserve` top-ups made by hand; and
runs launched before this lock existed. The on-chain floor is the only bound across
those. Do not delete `~/.factorylab/capital-loop`: the record is what finds the last
run.

**The deliberate manual reset.** If the recorded run's directory is gone (every launch
refuses `run_ledger_missing`, naming it), its diary reads empty
(`recorded_run_ledger_empty`), or the record or lock file was removed or damaged
(`capital_loop_last_run_missing`, `capital_loop_lock_file_missing`,
`capital_loop_last_run_unreadable`), the check can only be reset by hand, and only
this way:

1. make sure **no capital-loop run is alive on this host** (no runner process, in any
   checkout or session). This is the one thing the files cannot check for you: a run
   still alive holds its lock on the file you are about to remove, and removing it lets
   the next launch create and lock a fresh file beside the live run, so two runs would
   authorize against one reserve at once;
2. wait until at least 600 s plus twice the finality lag have passed since the last run
   died (its authorizations are then settled or dead), and read the reserve's balance
   (step 3 of "Before a live run");
3. remove **both** `<reserve>.lock` and `<reserve>.last-run.json`. The next launch
   creates them afresh with a record naming no run.

Removing only one of the two refuses every launch until both are gone.

## After the run

- **If the run printed `CAPITAL LOOP OUTSTANDING`** (on stderr, and as
  `capital_loop_outstanding` on stdout, in the final summary and in `report.json`), it
  ended with a conversion unbooked: a top-up still `submitted` (its authorization may
  settle after the world died, or settled with its credit short), or a shadow send
  still pending. Its `top_ups_submitted` names each transfer, nonce and `validBefore`.
  The runner exits 3 when a top-up was left submitted or the diary could not be read
  (1 for any other failure, 0 otherwise; a pending shadow send alone is testnet money
  and exits 0). From the world's construction on, SIGINT (Ctrl-C), SIGTERM and SIGHUP
  stop the run in order (`status: stopped`, `stopped_by`): the report is written, the
  warning printed and the exit code chosen exactly as at a normal end. `report.json` is
  written before the warning is printed, and the warning is printed even when that
  write fails. **Treat exit 130, a kill (137), any other unexpected status, or a missing
  report as possibly outstanding**, and run the script below before anything else.
  Do not touch the reserve or relaunch; run its `next_step`,

      uv run python scripts/capital_loop_outstanding.py work/capital-loop/<run>

  and follow "After a crash" from step 2. A settlement after the world died is never
  booked by it; the next launch refuses until that authorization settled or died.
- `events.json`: every `treasury.confirmed` with `direction: to_venice` has a matching
  `treasury.financing` (`source: venue_perps`, `paid_from: base_mainnet_reserve`) and a
  `financing.classified` naming the seat and what went to its entitlement.
- The last `treasury.venice_authorized` shows `authorized_micro` at or below
  `cap_micro`; the number of these items is the number of authorizations ever built.
- No transfer is left `submitted` or in `stranded`; if one is, its `reason` says which
  leg is waiting. A hybrid strand's shadow already paid; the world is never resumed,
  so settle it by reading the chain ("After a crash") before any manual action.
- Each confirmation's evidence carries `credit_shortfall_micro`: spend and rounding
  the tolerance absorbed. A `treasury.pending` "Venice credit short of the tranche"
  means real USDC left but the credit did not show it: compare `observed_micro`,
  `credit_before_micro` and `metered_usage_since_micro` in its carry with Venice's
  transaction list (`/x402/transactions/{wallet}`) before anything else.
- Re-read the mainnet reserve (step 3): it fell by exactly $5 × confirmed conversions,
  and it is not below the floor. Each confirmation's `tx_hash` is a Base mainnet
  transaction to check on a block explorer; its `nonce` is the authorization's.
- The Venice credit rose by $5 per conversion, less what Venice seats spent
  (`metered_usage_since_micro` in the evidence explains the difference).
- The venue's testnet ledger shows one `usdSend` of $5 to the sink per conversion (the
  evidence's `venue_ledger_hash`), and nothing else left the venue for the sink.
- Any `treasury.pending` whose reason is "submission outcome unknown" on a top-up
  means an authorization may still be live: do not top up by hand until it confirmed,
  or a finalized Base block is past its `validBefore` with no `AuthorizationUsed` for
  its nonce.

## After a crash

A killed or crashed capital-loop world is not resumed. Instead:

1. **Wait the validity window plus Base's finality lag** (about 26 minutes at most
   with the numbers of 23 September 2026). A crashed world's last top-up authorization
   stays valid on chain until its `validBefore` (at most the quote's
   `maxTimeoutSeconds`, capped at 600 seconds after it was prepared), and a finalized
   block past it comes about 16 minutes later. The reserve lock was released when the
   process died; nothing needs clearing.
2. **Read what is outstanding**, read-only and keyless (the run's diary is opened with
   its own `ledger.jsonl.key`, as `factorylab postmortem` opens it; the reserve key is
   never loaded, nothing is signed):

       uv run python scripts/capital_loop_outstanding.py work/capital-loop/<run>

   For every top-up authorization the run journaled, current and superseded, it prints
   the nonce, `validBefore`, the finalized head's timestamp, the USDC contract's
   `authorizationState` and any `AuthorizationUsed` debit, and a verdict: `settled`,
   `expired unused`, or `LIVE: may still settle`. It also lists shadow sends left
   unconfirmed. It exits 1 while anything is live or pending.
3. **Settle the books by hand from that output.** A `settled` authorization with no
   `treasury.financing` in the dead diary bought real credit the dead world never
   booked; the next run counts it in its first credit observation. A pending shadow
   send is testnet money only.
4. **Relaunch** as in "Before a live run". The launch check re-reads every sibling run
   directory and the reserve's last recorded run, and refuses while any authorization
   could still settle.

## What is not proven live

Both legs' success path runs end to end in `tests/world/test_venice_hybrid.py` through
the real `HybridRail`, `Treasury`, x402 client, `EVM` scans and Hyperliquid SDK, faked
only where bytes leave the process (the HTTP/JSON-RPC transport and the SDK's HTTP
post). The fake Base is honest (logs only for the asked contract, topics and blocks;
finality trailing the head) and the fake Venice and venue check every signature they
receive; a debit to another payee, of another amount or from another account is never
booked, and ours is booked once. `tests/scripts/test_capital_loop_live_run.py` runs the
runner itself on the same wires: the real `run_rehearsal` and `runtime.run()`, a seat's
own `treasury.transfer to_venice`, both legs signed, the run ending with the top-up
submitted (exit 3, or stopped by SIGINT, SIGTERM or SIGHUP with the same report), and
the next launch refused until the fake chain shows the authorization settled or dead.
Those fakes are ours, not Venice's or Hyperliquid's.

The testnet `usdSend` signing and its ledger-row shape were exercised only against
fakes: the signature recovers the venue address through the SDK's own
`recover_user_from_user_signed_action`, and both row shapes Hyperliquid has used
(`internalTransfer`, and `send` with a nonce) are accepted. The first live conversion
should be watched: if its shadow never confirms, compare the venue's
`userNonFundingLedgerUpdates` row with `HybridRail._shadow_receipt`.

The shadow leg's own expiry (`shadow send nonce expired unexecuted`) is still decided
on the runtime clock against the venue's nonce window. A clock far ahead could fail a
shadow send that later executes: testnet money would leave the pots unbooked, but no
real money moves, because the top-up is never prepared for a failed shadow. The pinned
payee and the credit check rely on Venice's recorded quote shape and balance endpoint.
