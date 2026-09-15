# Edition 2 build plan and frozen contracts

Base: `main` at `cff1e96`. Source of intent: `docs/audits/v4/gpt6-triage.md` and the reviewer's
packet under `docs/audits/v4/gpt6/`. This document is the contract every workstream builds
against. Change it only by editing this file first.

## Goal

A factory whose population can retain what it learns as machinery, whose mistakes cost their
maker, which can change its own standards without the old standard vetoing the change, which
receives its endowment on a committed schedule and pauses rather than dies between releases,
which can earn by selling a service as well as by trading, and whose hard casts (identity,
money, kill) are enforced by code rather than by trust.

## Principles that bind every workstream

- One conserved wallet remains the financial root. Every new budget, entitlement, lock or
  release is an accounting classification of that wallet's integer micro-USD, never new money.
- Every state change is ledgered first; every new durable field is serialised in
  `runtime_state` / `restore_runtime` explicitly and replays under the recovery journal.
- Population code runs only in the existing jail. Programs return typed requests for effects;
  they never hold credentials or open the network.
- Nothing population-authored reaches the system role.
- No production effect, mainnet call, fund transfer, or manifest named `funded` is created by
  any workstream. Testnet reads and the scripted world are fine. Never read, print or copy any
  `*.key` file.
- Do not run the full test suite in a workstream; run the tests you touch plus `ruff check`.
  The single full gate is run by the advisor before merge.

## Frozen contracts

### C1. Endowment: locked backing and scheduled release (`kernel/wallet.py`)

```python
@dataclass(frozen=True)
class ReleaseSchedule:
    releases: tuple[tuple[int, Money], ...]   # (at_ns offset from launch, amount_micro), ascending

Wallet(initial, ledger, *, locked_micro: Money = 0, release_schedule: ReleaseSchedule | None = None, ...)
Wallet.locked -> Money            # backing not yet released; excluded from available/unhistoried_available
Wallet.unlocked -> Money          # balance - locked
Wallet.release(now_ns) -> Money   # moves due tranches locked -> unlocked, once each, ledgered "release"
Wallet.next_release_ns -> int | None
```

`drip` stays as is and is not used for releases. `sum(releases) == locked_micro` is validated
at construction. `available` and `unhistoried_available` subtract `locked`.
`_manage_reserve_window` opens the novelty reserve on `wallet.unlocked`, not `wallet.balance`.

Manifest: `[endowment] locked_micro = N` and `releases = [{at = "7d", amount_micro = N}, ...]`.
Offsets are relative to the ledgered `Launch` timestamp. Validated in `runtime/worlds.py`.

### C2. Dormancy (`kernel/termination.py`, `runtime/loop.py`)

`Termination.check` returns `"budget_dormant"` (not terminal) when the wallet cannot afford the
cheapest feasible seat, `wallet.locked > 0`, and `next_release_ns` is not None. In that state the
loop skips paid cognition and continues mandatory maintenance: fills, settlement of due
forecasts, treasury pending, releases. Terminal death requires `locked == 0`. Dormancy entry and
exit are ledgered (`kind: "dormant"`, `{"entered"|"exited", ts}`) and shown in the wake.

### C3. Rent by byte-time (`runtime/notes.py`, `kernel/artifacts.py`)

Rent is `bytes × elapsed_ns × micro_per_byte_ns` with a retained remainder so frequent
collection cannot round up. Manifest field `notes.micro_per_byte_day` replaces
`byte_window_micro` (kept readable for old manifests, mapped at load). Default makes the 256 KiB
cap cost about one cent a day.

### C4. Release identity and witness (`runtime/bootstrap.py`, `runtime/resume.py`, `deploy/`)

`release_digest = sha256(git_head + sha256(uv.lock) + tree_hash(factorylab/))`, computed at
start by `factorylab/runtime/release.py::release_digest()`. Ledgered in the `Launch` event.
`restore_runtime` refuses a different digest with `failed_resume reason=release_mismatch`.
`deploy/backup.sh` records the digest beside the ledger. `deploy/witness.sh` appends
`{world, event, ts, release_digest, ledger_head}` for launch, dormant, kill and failed_resume
to a local append-only file and, when `FACTORYLAB_WITNESS_URL` is set, POSTs the same line.

### C5. Venice receipt (`world/treasury_rails.py`)

A purchase is confirmed on canonical debit evidence (the matching on-chain transfer). The credit
balance is advisory: record `observed`, `credit_before`, `amount`, and `metered_usage_since` in the
confirmation event; never refuse on the balance alone.

### C6. Grading (`settlement/settle.py`, `charter/measurement.py`, `runtime/pricing.py`, `settlement/lots.py`)

- Verdict baseline records the fractional target `1 - share`, the same quantity the judge is
  scored on.
- New observation `cost_per_attempt`: mean cost over every selected return, failed included,
  rent added. `cost_per_return` stays for old charters.
- `tool_calls` is the mean per return.
- Generic blame: per-decision share has a floor `min_share` (manifest `prices.min_blame_share`,
  default 0.1) so splitting participation cannot dilute below it.
- Lot credit is conserved: a lot's P&L is credited once, split between opener and closer by
  the notional each contributed, never both in full.

### C7. Metric challenge (`cortex/registration.py`, `runtime/governance.py`, `charter/book.py`)

New proposal kind `challenge`:

```json
{"kind": "challenge", "card_id": "cost-cap", "evidence": "text",
 "replacement": {"observation": "cost_per_attempt", "rule": "at most", "value": 5000,
                 "window": {"kind": "returns", "n": 10, "per": "role"}},
 "trial_windows": 6}
```

Admission costs one novelty trial. Both the incumbent card and the replacement are measured,
frozen, for `trial_windows` windows and both series are ledgered (`kind: "challenge.window"`).
At the end the existing committee ballot decides adoption as an amendment. The challenge is not
judged by the card it challenges: `_policy_prediction` accepts a challenge id as the promised
effect for proposals made during the trial. Commitments incurred under the incumbent settle
under the incumbent.

### C8. Programs as seats (`cortex/assembly.py`, `cortex/registration.py`, `runtime/compute.py`, `runtime/governance.py`, `runtime/resume.py`)

```python
@dataclass(frozen=True)
class ProgramAssemblySpec(AssemblySpec):
    code: str = ""              # Python, runs in the jail; model_id == "program"
    timeout_s: int = 10
    state_policy: str = "none"  # "none" | "private"  (private: one versioned artifact per spec id)
```

`ProgramAssembly.invoke(req)` runs the code in the jail with the rendered request JSON
(`req.prompt_text()` plus `inputs`) on stdin and expects the same Return JSON a model would
produce; it passes through the same `validator`. Cost is `prices.program_micro_per_call`
(manifest, default 50) reserved and committed through the meter, so every program call is a
wallet transaction. A program that fails or times out yields a `Return` with status `malformed`
like a model would. Programs are routed, judged, given standing and retired exactly like model
seats. Registration is `AssemblyProposal` with `model_id = "program"` and `code`; admission
costs the trial amount and requires the jail. Private state is loaded from and saved to the
artifact store (C9) around each call, by content hash, and the hash is ledgered with the return.

### C9. Artifact archive (`kernel/artifacts.py`)

```python
class ArtifactStore:
    def put(self, data: bytes, *, owner: str, kind: str) -> str      # sha256 hex; ledgered "artifact.put" {sha, owner, kind, bytes}
    def get(self, sha: str) -> bytes
    def list(self, *, owner: str | None = None) -> list[dict]
```

Bytes live under `runs/<world>.artifacts/<sha>`; the ledger holds hash, owner, kind, size, ts.
Rent by byte-time (C3) is charged to the owner's entitlement (C10) while the owner exists, and
to the commons rate once the owner retires. Retirement never deletes. Any seat may read any
artifact; the `artifact.get` tool is seeded and free.

### C10. Per-seat entitlement (`kernel/budget.py`, `world/metering.py`)

```python
class BudgetBook:
    def entitlement(self, assembly_id: str) -> Money
    def grant(self, assembly_id: str, amount: Money, reason: str) -> None    # from the unallocated unlocked pool
    def debit(self, assembly_id: str, amount: Money, reason: str) -> None
    def credit(self, assembly_id: str, amount: Money, reason: str) -> None
    def transfer(self, src: str, dst: str, amount: Money, reason: str) -> None
    def unallocated(self) -> Money   # wallet.unlocked - sum(entitlements) - holds
```

Invariant: `sum(entitlements) + unallocated == wallet.unlocked - holds`. All movements are
ledgered (`kind: "budget"`). `SeatWallet(wallet, book, assembly_id)` implements `WalletLike`:
`reserve` requires both the wallet and the seat's entitlement to cover the ceiling; `commit`
debits both. `Meter.run` receives the `SeatWallet` for that seat. Each release (C1) is split:
`base_share` equally across live seats, the remainder to `unallocated`. Settled `return_paid_off`
P&L credits the owning seat. Registering a child or a program moves the trial amount from the
proposer's entitlement to the child's. A seat with an empty entitlement is infeasible for
routing (not dead) until credited; retirement returns its entitlement to `unallocated`.

### C11. Service seller (`world/seller.py`, `deploy/serve.py`, `runtime/wake.py`)

Proposal kind `service`: `{program_id, price_micro, description}`. The wake host serves
`POST /service/<id>` under x402: the request is paid in USDC on Base to the reserve address,
the payment is verified with the same facilitator logic the x402 client uses, then the program
runs in the jail and returns its output. Each paid call is ledgered `income.earned {service,
micro, tx}` and credits the owning seat's entitlement. `pots` gains `earned_micro`,
`subsidy_micro`, `converted_from_principal_micro`, and the wake shows the three separately.

### C12. Charter, edition 2 (docs and manifests, after C6/C7 land)

Four read-only norms, the reviewer's: consequential usefulness; epistemic integrity; durable
agency; bounded reciprocity (text in `docs/charter/edition2-draft.toml`). Eight cards: tool discipline (mean per return),
well-formed floor, cost cap (cost_per_attempt), censorship bound, forecast skill, consequence
paid off, position concentration, turnover. The five quota cards are removed. Re-ratified by
a testnet committee vote with `scripts/ratify_charter.py`; hashes recorded.

### C13. Seat calibration (`scripts/calibrate_seats.py`)

Runs candidate models against the scripted contracts (produce, judge, meta, tool use, a failing
task, a continuation) under an explicit budget cap, offline by default, and reports per-model
completion rate, well-formed rate, p50/p95 cost per whole decision tree, and latency.

## Workstreams and ownership

| # | Workstream | Contracts | Owns | Wave |
|---|---|---|---|---|
| W1 | Endowment, dormancy, rent | C1 C2 C3 | `kernel/wallet.py`, `kernel/termination.py`, `runtime/notes.py`, `runtime/loop.py::_check_termination`, `runtime/pricing.py::_manage_reserve_window`, `runtime/worlds.py` (endowment, notes fields), resume fields | 1 |
| W2 | Hard casts | C4 C5 | `runtime/release.py` (new), `runtime/bootstrap.py`, `runtime/resume.py` (digest check only), `deploy/*.sh`, `world/treasury_rails.py` | 1 |
| W3 | Grading and challenge | C6 C7 | `settlement/settle.py`, `settlement/lots.py`, `charter/measurement.py`, `charter/book.py`, `runtime/pricing.py` (blame lines only), `runtime/governance.py::_policy_prediction` and new `_register_challenge`, `cortex/registration.py` (challenge proposal only) | 1 |
| W4 | Service seller | C11 | `world/seller.py`, `deploy/serve.py`, `runtime/wake.py`, `world/treasury.py` (pots view fields), `cortex/registration.py` (service proposal only) | 1 |
| W5 | Programs and archive | C8 C9 | `cortex/assembly.py`, `cortex/sandbox.py`, `kernel/artifacts.py`, `runtime/compute.py::_instantiate`, `runtime/governance.py::_register` (program branch), `cortex/registration.py` (AssemblyProposal program fields), `cortex/schematics.py` (prompt text), resume fields | 1 |
| W6 | Per-seat entitlement | C10 | `kernel/budget.py`, `world/metering.py`, `runtime/routing.py` feasibility, `runtime/feedback.py` consequence credit, governance child cost, resume fields | 2, after W1 |
| W7 | Charter, manifests, calibration, vertical slice | C12 C13 | `docs/charter/`, `docs/charter-explained.md`, `worlds/*.toml`, `scripts/`, `tests/audit/test_edition2_slice.py` | 3 |

Shared files touched by two workstreams (`runtime/governance.py`, `cortex/registration.py`,
`runtime/pricing.py`, `runtime/resume.py`) are edited only in the named functions or by adding
new functions; no reformatting, no moving code.

## Gates

- Each PR: touched tests green, `ruff check` clean, advisor diff review against this contract.
- Full gate before each merge: `uv run pytest` green on the merged tree.
- Wave 2 merged: zip of the tree to GPT-6 Pro for a cold review against this document.
- Wave 3: the twelve-step vertical slice from the reviewer's plan as one scripted test, a
  testnet rehearsal at the ten-minute tick, hashes recorded, funded manifest drafted (not named
  `funded` until the launch gates).
