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

   Run it inside `tmux` (or `screen`), not under `nohup`. A closed terminal then stops
   nothing, and you can reattach to see the end. Under `nohup` SIGHUP is ignored, and
   the runner leaves an ignored signal alone, so the run goes on without a terminal.
   That works, but the loud end-of-run warning then goes only to `nohup.out`.

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
      exactly at a line boundary cannot be told from a shorter one by its file alone:
      the write-ahead authorization record (step 5) is the defence there;
   5. resolves every authorization in the reserve's write-ahead authorization record
      against finalized Base, whatever any diary now holds (see "The write-ahead
      authorization record"), and refuses while one may still settle, or while one
      settled that no diary booked (a recovery: exit 3);
   6. scans the last settlement window before the finalized head, and every block
      after it up to the latest, for the reserve's own authorizations and transfers,
      and refuses on any the record does not know (the cooling-off scan, same section).

### How long a run must be

A conversion settles, or provably dies, only once a *finalized* Base block is past its
debit or past its `validBefore`, and the rail sees that on its next tick. The signer
stamps `validBefore` from this host's clock, at most 600 s after it prepares the
authorization. So one conversion's settlement horizon, in Base's own time, is:

- **600 s**, the validity cap the signer always applies. Never the quote read at
  launch: the signer obeys whatever quote it is handed later, so a launch-time quote
  would bound nothing.
- **plus twice how far the finalized block is behind the present**: the later of this
  host's clock and the latest block's timestamp, less the finalized block's timestamp.
  The host clock carries Base's finality lag, any lead of the host over the chain (a
  `validBefore` stamped by a fast clock lies that much later in chain time) and any
  staleness of the node that answered (an old finalized block only lengthens it). The
  latest block's time stands in when the host clock is the slow one, so a slow host
  cannot shrink it; and since it only ever raises the maximum, a stale `latest` (two
  reads a load balancer sent to two nodes) cannot shrink it either. Doubling is the
  allowance for the lag growing during the run (16 minutes measured, 32 allowed).
  The latest block must also be above the finalized one, or the launch is refused
  (`finalized_tag_not_behind_latest`): some providers answer the `finalized` tag with
  their latest block, which would make the lag vanish and the rail's finalized reads
  mean nothing. A block that cannot be read refuses too (`finality_lag_unreadable`).
- **plus one tick** for the rail to look.

The run commands the conversion, and AGENTS.md rule 12 (essay II, IV.c) asks an inner
loop to settle at least 3× faster than the outer loop that commands it, so the runner
refuses a run *planned* shorter than **three** horizons. The clock delivers
floor(`--duration` / tick) ticks, or `--ticks` when that is fewer, and its first tick
comes at once: N ticks span N − 1 intervals, and that is the planned length credited
(a 7,599 s duration at a 10 s tick is 759 ticks, credited 7,580 s). The signer stamps
`validBefore` with the wall clock, the same clock the bound's host time is read from, so
the two cannot disagree; a capital-loop launch refuses an injected clock
(`capital_loop_requires_the_wall_clock`), since a virtual clock would stamp a real
authorization. Injected clocks remain for testnet-only runs. The admission cap, a
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

- Beside them, `<reserve>.authorizations.jsonl` is the write-ahead authorization record
  (next section), created with the lock file. A lock file without it refuses every
  launch (`capital_loop_authorization_record_missing`).

Reading only the sibling directories of `--out` is **not enough** on its own, even
with the lock: the lock ends with its run, and a run can end (or die) with an
authorization still live for up to its validity window plus finality. A later run
launched with `--out` in another directory would not see it, and the $5 it could still
settle is invisible to the floor. The last-run record closes that: every earlier run
was proven settled or dead by the launch after it (a dead EIP-3009 authorization never
revives), so the last holder is the only earlier run that can still be live, and it is
always read. Siblings and `--previous-run` are still read too.

Every EIP-3009 signer in this code base takes the same lock. The one function that
signs a `TransferWithAuthorization` with a reserve key
(`x402.sign_transfer_authorization`) signs only after its guard wrote the authorization
to the reserve's write-ahead record under the reserve's lock (next section). The signers
are the capital-loop rail, the ordinary treasury rail, x402 purchases (a world's market
and `factorylab probe`), `factorylab reserve topup` and `scripts/compute_proof.py`. While
a capital-loop run is alive every one of them refuses (`capital_loop_reserve_locked`);
otherwise each takes the lock for its one write and records its authorization, which
the next capital-loop launch resolves like its own. A signer with no guard signs
nothing.

Plain reserve-key transactions take the same guard. Every one this code base signs goes
through `EVM.prepare` or `EVM.replace` (`factorylab/world/evm.py`), which records it
(transaction hash, chain, nonce, destination, the chain head as `start_block`, origin,
run directory) to the same record under the same lock before returning it, and
`EVM.broadcast`, which asks the guard first: while a capital-loop run holds the reserve
nothing is prepared, replaced or broadcast. They are the ordinary rail's approvals and
HyperCore deposit, CCTP's `depositForBurn` and `receiveMessage`, and the acceptance CLI's
withdrawal. A chain with no guard prepares and sends nothing. The hybrid rail's Base
chain has a zero gas budget and never signs one.

What the lock does not cover: another machine, or another operator account (another
home directory), on the same reserve; and code older than this runbook. The on-chain
floor and the cooling-off scan (next section) are the only bounds across those.

**Never restore, copy or migrate `~/.factorylab/capital-loop`.** A restored or copied
record has forgotten every authorization written since the copy, and no file beside it
can tell. The cooling-off scan catches one that already settled; one still unsettled
could settle after the next run's floor check. Deleting the directory is the manual
reset below, and it requires every recorded authorization to be settled and booked, or
dead.

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
3. copy `<reserve>.last-run.json` aside (it names the last run, which you may still
   need to read), then remove **all three**: `<reserve>.lock`, `<reserve>.last-run.json`
   and `<reserve>.authorizations.jsonl`. The next launch creates them afresh, with a
   record naming no run and an empty authorization record. The authorization record is
   the one file whose loss can hide real money: remove it only once every authorization
   in it is settled and booked, or dead (step 2, and `--acknowledge` below). A lock
   directory made before the authorization record existed refuses with
   `capital_loop_authorization_record_missing`, and the refusal names these exact files.

Removing some of them and not the others refuses every launch until all are gone.

### The write-ahead authorization record

A diary can be truncated at a line boundary, restored from an older copy, or deleted,
and a file alone cannot tell a cut diary from a shorter one. So an authorization's
existence does not rest on any diary:

- **Before any signer signs** an EIP-3009 authorization (see "One run per reserve") it
  reads Base's latest block number, then appends one line (nonce, value, payer, payee,
  `validAfter`, `validBefore`, that block as `start_block`, origin, run directory) to
  `<reserve>.authorizations.jsonl` beside the lock and flushes it to stable storage
  (`F_FULLFSYNC` on macOS). If the head cannot be read or the write fails, nothing is
  signed; a signer with no guard refuses to sign at all. The signature does not exist
  before the line, so it cannot be used before `start_block`. A crash between the
  append and the signature leaves an authorization recorded and never signed: it can
  never be used, and it resolves as soon as it expires. A plain reserve-key transaction
  is recorded the same way, as a `transaction` line (previous section).
- **An append never lands on a torn line.** The writer checks the record ends in a
  newline first. A last line that is a whole entry missing only its newline is counted,
  and the newline is written (and flushed) before anything else. A torn fragment is not
  appended to: every launch refuses `authorization_record_torn` until it is repaired:

      uv run python scripts/capital_loop_outstanding.py --repair-torn

  which takes the lock, moves the fragment to `<record>.torn-<seconds>` (nothing is
  deleted), and records a `torn` entry carrying its bytes. Any nonce-like value in the
  fragment becomes an open authorization, resolved against the chain like any other
  (a torn append was never signed, but the record does not assume it). A torn nonce
  always resolves as a recovery if it was used: nothing shows where it was booked. Its
  `validBefore` is the fragment's own only when the value is terminated (a closing
  quote, or a delimiter after a bare number): `"validBefore": "13` cut mid-digits is
  unknown, never 13. Known or not, it is capped at the repair time + 600 s, since a
  signer capped at 600 s wrote it before the repair. Its `start_block` is the
  fragment's own when terminated; otherwise its scan starts by the legacy rule below,
  from that capped `validBefore`, so it never starts at the genesis block.

  The repair is atomic. It writes and flushes the sidecar, writes the new record (the
  good prefix and the `torn` entry) whole to a temporary file beside it and flushes
  that, swaps it in with `os.replace`, and flushes the directory (`F_FULLFSYNC`). A
  crash at any step leaves either the old record (still refused as torn: run the
  repair again) or the new one, never neither, and never a half-written record.
- **At every launch**, every recorded authorization not yet resolved is read twice,
  and the two reads must agree: USDC's `authorizationState` at the finalized block, and
  a scan of the finalized blocks it could have been used in for its `AuthorizationUsed`.
  The `finalized` tag is read once per check. Its block number is then passed
  explicitly to both reads (`EVM.scan` takes the end block), so a provider whose tag
  moves between two calls cannot split them. The scan runs from the entry's
  `start_block` to the first block whose timestamp is past its `validBefore`, or to the
  finalized block while no finalized block is past it yet. It costs the same whatever
  the entry's age: at most about 600 s of blocks, plus a binary search for the end
  block. An entry written before `start_block` existed (legacy), or a torn one without
  it, starts at the block at `validBefore` − 600 s − the host clock's lead over the
  latest block now − 300 s (`LEGACY_SKEW_CUSHION_S`) − the finality lag. The 300 s
  covers a host clock that led the chain by up to 5 minutes more at signing than it
  does now. A clock that has been corrected by more than that is the case the recorded
  `start_block` covers. The reads must agree, or the launch refuses
  (`recorded_authorization_reads_disagree`), as it also does when the scan fell short
  of its end block. A finalized tag that is not behind `latest` refuses too
  (`finalized_tag_not_behind_latest`). Nothing is resolved from one read, and nothing
  is read at `latest`. Then, used entries resolve by **origin**:
  - used by a world (`capital_loop`, or `treasury` from the ordinary rail), and the
    diary of the run directory it names holds the `treasury.financing` of the
    transfer whose confirmed receipt carries that nonce: resolved. An entry with no
    origin was written before origins existed, by a capital-loop run, and is held to
    the same rule;
  - used by a signer with no diary (`reserve_topup`, `x402_purchase`, `x402_probe`,
    `compute_proof`, `treasury_cli`): spent in the open, resolved;
  - used by a world, a legacy entry or a torn fragment, and no diary shows it
    booked: **recovery**. The launch is refused
    (`recorded_authorization_settled_unbooked`), `CAPITAL LOOP RECOVERY` is printed and
    the runner exits 3. Real USDC left the reserve and bought Venice credit no world
    booked. Settle the books by hand ("After a crash", step 3), then acknowledge it:

        uv run python scripts/capital_loop_outstanding.py --acknowledge 0x<nonce>

    which takes the reserve's lock, refuses unless finalized Base shows that recorded
    authorization used, and appends its resolution;
  - unused, and the finalized block is past its `validBefore`: dead for good, resolved;
  - unused and not yet past it: the launch is refused
    (`recorded_authorization_may_still_settle`) until it settles or expires.
- A resolution is appended to the record too, so a diary deleted after its financing
  was proven is never needed again. Any unreadable line other than a torn last one
  refuses (`authorization_record_unreadable`).
- **The cooling-off scan.** A record rolled back with its directory cannot be caught by
  any local file. So each launch also scans for the reserve's own `AuthorizationUsed`
  and `Transfer` events. The scan covers every block from the block at the finalized
  head's timestamp − (600 s + 2 × the finality lag) up to the latest block. That
  includes settlements not yet final: a reorg can only remove what refuses here, so
  the scan detects and never resolves. An authorization used there whose nonce the
  record does not know (a torn fragment's nonces count as known) refuses the launch as
  a recovery (`unrecorded_reserve_authorization`, exit 3). USDC leaving the reserve
  there refuses too (`unrecorded_reserve_transfer`), unless it shares its transaction
  with such an authorization or is a transaction the record holds. Both pass once the
  window has moved past them. This catches a rollback whose authorization already
  settled. One not yet settled is not on chain to find, which is why the directory is
  never restored or copied.
- **A transfer made by hand is not on the record.** Sending USDC out of the reserve
  from a wallet, or from any code older than this runbook, refuses every capital-loop
  launch (`unrecorded_reserve_transfer`) while it lies inside the scan. The scan
  window is about 42 minutes (600 s + 2 × a 16-minute lag). So after the transfer
  finalizes the refusal lasts about 42 minutes more, about 58 minutes after it was
  sent. It then clears by itself, with nothing to acknowledge. Do not move reserve
  USDC by hand within an hour before a launch.

## After the run

- **If the run printed `CAPITAL LOOP OUTSTANDING`** (on stderr, and as
  `capital_loop_outstanding` on stdout, in the final summary and in `report.json`), it
  ended with a conversion unbooked: a top-up still `submitted` (its authorization may
  settle after the world died, or settled with its credit short), or a shadow send
  still pending. Its `top_ups_submitted` names each transfer, nonce and `validBefore`.
  A conversion counts as unbooked until its `treasury.financing` exists, whatever its
  last status: a stop between `treasury.confirmed` and `treasury.financing` leaves a
  confirmed transfer with no booking, and it is reported (and exits 3) like a top-up
  still submitted.
  The runner exits 3 when a top-up was left unbooked or the diary could not be read
  (1 for any other failure, 0 otherwise; a pending shadow send alone is testnet money
  and exits 0). From the world's construction on, SIGINT (Ctrl-C), SIGTERM and SIGHUP
  (unless it is ignored, as under `nohup`) stop the run in order (`status: stopped`,
  `stopped_by`): the report is written, the warning printed and the exit code chosen
  exactly as at a normal end. From the moment the run's end begins, all three are held
  back (blocked) until the report is written and the warning printed; one that arrived
  meanwhile is then recorded and changes nothing. `report.json` is written before the
  warning is printed; if that write fails, the warning is printed anyway and the exit
  code is still 3 for an outstanding top-up (`report_write_failed` in what the runner
  returns). A lost terminal at the final print changes no exit code. A stop skips the
  end-of-run wind-down (`kill_at_end`), so the testnet venue may be left with open
  positions: testnet money only, and acceptable. **Treat exit 130, a kill (137), any other unexpected status, or a missing
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
   booked; the next run counts it in its first credit observation. Then acknowledge it
   (`scripts/capital_loop_outstanding.py --acknowledge 0x<nonce>`): until then every
   launch refuses it as a recovery and exits 3. A pending shadow send is testnet money
   only.
4. **Relaunch** as in "Before a live run". The launch check re-reads every sibling run
   directory, the reserve's last recorded run and the write-ahead authorization record,
   and refuses while any authorization could still settle or settled unbooked.

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
