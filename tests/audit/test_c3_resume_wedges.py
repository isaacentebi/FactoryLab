"""Cold audit round three, seat 3: recovery paths that refuse a resumable world, or
resume one that should be refused.

Each test reproduces one finding in docs/audits/v3/defects-fable.md and fails on the
audited commit. Nothing here touches a network.
"""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError, canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import RecoveryJournal, ResumeError, encode, resume_world
from factorylab.runtime.worlds import load_manifest


class Died(BaseException):
    pass


def _runtime(path, events):
    return Runtime(load_manifest("scripted"), events=events, seed=1, initial_balance_micro=None,
                   ledger_path=str(path), drip=True, router_gamma=.1)


@pytest.mark.parametrize("name", ["sandbox.run", "observation.run"])
def test_finding_2_a_jailed_run_interrupted_after_its_io_call_is_replayable(name):
    """A population tool or observation runs in the jail with no network and no side
    effects. The journal classifies it as an unacknowledged external write, so a process
    death between ``io.call`` and ``io.result`` makes every later resume refuse with
    ``replay_diverged``: the world is wedged by a pure computation."""
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = journal.recovering = True
    fingerprint = hashlib.sha256(canonical(encode(((), {})))).hexdigest()
    # The tail ends with the call's own evidence: the process died before io.result.
    journal.tail = [{"kind": "io.call", "name": name, "input_hash": fingerprint,
                     "seq": 0, "ts": 0}]
    try:
        result = journal.call(name, lambda: {"value": 1}, (), {})
    except BaseException as exc:  # _ReplayFault is a BaseException
        pytest.fail(f"an interrupted {name} cannot be resumed: {exc}")
    assert result == {"value": 1}
    assert recorded == [{"kind": "io.result", "call": 0, "result": encode({"value": 1})}]
    journal.tail = [
        {"kind": "io.call", "name": name, "input_hash": fingerprint, "seq": 0, "ts": 0},
        {**recorded[0], "seq": 1, "ts": 0},
    ]
    assert journal.call(name, lambda: pytest.fail("completed jail run repeated"), (), {}) == result


@pytest.mark.parametrize("name", ["sandbox.run", "observation.run"])
def test_finding_2_a_jailed_run_later_in_an_interrupted_event_fails_instead_of_running(name):
    """The other face of the same omission. While the interrupted event is re-executed
    past the journal tail, every call that is not read-only is refused as an external
    write that "was never dispatched". A population tool or observation reached after the
    crash point therefore fails with ``UnbilledFailure``, and the event completes on a
    path the uninterrupted world would not have taken (seen as one order fewer and one
    tool failure more in a scripted world interrupted at ``connector.call``)."""
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = journal.recovering = True
    journal.tail = []  # past the tail: the rest of the event runs for the first time
    try:
        result = journal.call(name, lambda: {"value": 1}, (), {})
    except Exception as exc:
        pytest.fail(f"{name} refused during re-execution of the interrupted event: {exc}")
    assert result == {"value": 1}
    assert [item["kind"] for item in recorded] == ["io.call", "io.result"]
    assert recorded[-1]["call"] == 0 and recorded[-1]["result"] == encode(result)


def test_finding_2_death_during_a_population_tool_run_wedges_the_scripted_world(tmp_path):
    from tests.cortex.test_jail import require_jail

    require_jail()
    path = tmp_path / "tool.jsonl"
    rt = _runtime(path, events=60)
    append = rt.ledger.append

    def interrupt(entry):
        seq = append(entry)
        if entry["kind"] == "io.call" and entry["name"] == "sandbox.run":
            raise Died
        return seq

    rt.ledger.append = interrupt
    with pytest.raises(Died):
        rt.run()
    rt._ledger_lock.close()
    try:
        summary = resume_world(load_manifest("scripted"), str(path))
    except ResumeError as exc:
        pytest.fail(f"resume refused a world interrupted inside the jail: {exc.code}: {exc}")
    assert summary["stats"]["resumes"] == 1 and summary["ledger_verify"]


def test_finding_5_a_ledger_shorter_than_its_own_head_is_a_rollback_not_a_resume(tmp_path):
    """The encrypted ``.head`` records the authenticated byte offset of the last checkpoint.
    A ledger file shorter than that offset (a restored backup, a copy truncated on a line
    boundary) is not a torn tail: whole acknowledged items are gone, and the head is the
    evidence. ``reopen`` ignores the head when ``offset > size`` and resumes the older
    state silently, re-running events whose external writes may already have happened."""
    m = load_manifest("scripted")
    path = tmp_path / "rollback.jsonl"
    rt = _runtime(path, events=3)
    rt.run()
    rt._ledger_lock.close()
    head = Path(str(path) + ".head")
    assert head.exists()
    lines = path.read_bytes().splitlines(keepends=True)
    items = Ledger.reopen(path, manifest=json.loads(m.canonical_json()))._recovery_items()
    snapshot = next(i for i in items if i["kind"] == "snapshot")
    cut = snapshot["seq"] + 1 + 20  # a complete line boundary well before the checkpoint
    path.write_bytes(b"".join(lines[:cut + 1]))
    with pytest.raises(LedgerIntegrityError):
        Ledger.reopen(path, manifest=json.loads(m.canonical_json()))


@pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")
def test_finding_9_a_class_transfer_confirmed_after_a_loss_does_not_kill_the_world():
    """The fake rail confirms a class transfer at the next tick by calling
    ``FakeExchange.class_transfer``, which re-checks availability and raises when a
    position lost value in between. The exception leaves ``treasury.tick`` and the event
    loop; the journaled poll replays it on every resume."""
    from factorylab.kernel.ledger import Ledger as MemoryLedger
    from factorylab.kernel.wallet import Wallet
    from factorylab.world.exchange import FakeExchange, Order
    from factorylab.world.treasury import FakeTreasury

    ledger = MemoryLedger(clock_ns=lambda: 0)
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    exchange = FakeExchange(coins=("BTC",), spot_pairs=("BTC/USDC",), start_cash_usd=Decimal(100),
                            start_prices={"BTC": Decimal(100)}, spread_bps=Decimal(0),
                            fee_bps=Decimal(0), step_bps=Decimal(0),
                            shocks={1: {"BTC": Decimal("0.5")}})
    treasury = FakeTreasury(ledger, wallet, exchange=exchange)
    wallet.bind_pots(treasury.pots)
    assert exchange.place(Order("BTC", True, Decimal(1))).status == "filled"
    exchange.drain_events()
    amount = int(exchange._perp_withdrawable())
    assert treasury.transfer("perps_to_spot", str(amount), handle="a", now_ns=1)[
        "status"] == "submitted"
    exchange.advance(1)  # the market halves before the confirming tick
    try:
        treasury.tick(2)
    except ValueError as exc:
        pytest.fail(f"treasury.tick raised out of the event loop: {exc}")
    assert treasury.state["status"] in ("confirmed", "failed", "submitted")
