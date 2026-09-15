import pytest

from factorylab.kernel.events import Bus, Event
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.registry import Registry
from factorylab.kernel.termination import DORMANT, Termination
from factorylab.kernel.wallet import Infeasible, ReleaseSchedule, Wallet


def test_invariant_3_termination_is_final_and_releases_key(ledger, clock, contract_factory):
    wallet = Wallet(10, ledger, clock_ns=clock)
    bus = Bus(ledger)
    termination = Termination(ledger=ledger, bus=bus, clock_ns=clock)
    observed = []
    bus.subscribe(
        "Terminated",
        lambda event: observed.append(
            (event.payload["reason"], termination.final, ledger.seal_key_released())
        ),
    )
    assert termination.check(wallet, clock.now) is None
    with pytest.raises(PermissionError):
        _ = termination.seal_key
    wallet.commit(wallet.reserve(10, "h", "model"), 10)
    assert termination.check(wallet, clock.now) == "balance_zero"
    termination.kill(termination.check(wallet, clock.now))
    termination.kill("different")
    assert observed == [("balance_zero", True, True)]
    assert termination.check(wallet, clock.now) == "balance_zero"
    assert ledger.decrypt_item(0)["kind"] == "wallet.initial"
    assert ledger.verify()
    for operation in (
        lambda: ledger.append({"kind": "after-death"}),
        lambda: bus.publish(Event("a", "Tick", 100, {}, "world")),
        lambda: Registry(ledger).register(contract_factory()),
    ):
        with pytest.raises(RuntimeError, match="final"):
            operation()
    with pytest.raises(Infeasible):
        wallet.reserve(0, "h", "model")


def test_explicit_kill_locks_positive_wallet_and_finality_survives_callback_failure(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    bus = Bus(ledger)

    def fail(_):
        raise RuntimeError("broken observer")

    bus.subscribe("Terminated", fail)
    termination = Termination(ledger=ledger, bus=bus, clock_ns=clock)
    with pytest.raises(ExceptionGroup):
        termination.kill("explicit_kill")
    assert termination.final and ledger.seal_key_released() and ledger.verify()
    assert wallet.balance == 100 and not wallet.dead
    with pytest.raises(Infeasible):
        wallet.reserve(1, "h", "model")
    termination.kill("again")


def test_ledger_integrity_failure_still_releases_forensic_key(tmp_path, clock):
    path = tmp_path / "broken.jsonl"
    ledger = Ledger(path, clock_ns=clock)
    wallet = Wallet(10, ledger, clock_ns=clock)
    bus = Bus(ledger)
    termination = Termination(ledger=ledger, bus=bus, clock_ns=clock)
    ledger.append({"kind": "later"})
    lines = path.read_bytes().splitlines(keepends=True)
    lines[2] = b'{"item":"corrupt"}\n'
    path.write_bytes(b"".join(lines))
    assert termination.check(wallet, clock.now) == "ledger_failure"
    termination.kill("ledger_failure")
    assert ledger.seal_key_released() and termination.final
    assert not ledger.verify()
    assert ledger.decrypt_item(0)["kind"] == "wallet.initial"
    assert ledger.decrypt_item(2)["event"]["kind"] == "Terminated"


def test_cannot_replace_termination_authority_or_remove_conditions(ledger):
    bus = Bus(ledger)
    with pytest.raises(ValueError):
        Termination(("explicit_kill",), ledger=ledger, bus=bus)
    Termination(ledger=ledger, bus=bus)
    with pytest.raises(PermissionError):
        Termination(ledger=ledger, bus=bus)
    with pytest.raises(ValueError):
        Termination(ledger=Ledger(), bus=bus)


def test_reentrant_kill_freezes_writes_immediately_and_queues_terminal_event(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    bus = Bus(ledger)
    termination = Termination(ledger=ledger, bus=bus, clock_ns=clock)
    seen = []

    def stop(_):
        seen.append("tick-first")
        termination.kill("explicit_kill")
        assert ledger.final and ledger.seal_key_released()
        with pytest.raises(Infeasible):
            wallet.reserve(1, "h", "model")

    bus.subscribe("Tick", stop)
    bus.subscribe("Tick", lambda _: seen.append("tick-second"))
    bus.subscribe("Terminated", lambda _: seen.append("terminated"))
    bus.publish(Event("tick", "Tick", 100, {}, "world"))
    assert seen == ["tick-first", "tick-second", "terminated"]


def test_wallet_does_not_mutate_when_ledger_append_fails(ledger, monkeypatch):
    wallet = Wallet(100, ledger)
    hold = wallet.reserve(50, "h", "model")

    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "append", fail)
    with pytest.raises(OSError):
        wallet.commit(hold, 50)
    assert wallet.balance == 100 and wallet.available == 50
    assert wallet.check_conservation()


def test_final_seal_is_released_even_if_terminal_disk_write_fails(ledger, monkeypatch):
    termination = Termination(ledger=ledger, bus=Bus(ledger))

    def fail(_):
        raise OSError("terminal disk failure")

    monkeypatch.setattr(ledger, "_append", fail)
    with pytest.raises(OSError):
        termination.kill("explicit_kill")
    assert termination.final and ledger.final and ledger.seal_key_released()


def test_budget_dormant_is_a_pause_between_releases_never_a_death(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock, locked_micro=90,
                    release_schedule=ReleaseSchedule(((50, 90),)))
    termination = Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock)
    # Before the Launch anchors the schedule there is no release to wait for.
    assert termination.check(wallet, clock.now, cheapest_seat_micro=11) is None
    wallet.launch(clock.now)
    assert termination.check(wallet, clock.now) is None
    assert termination.check(wallet, clock.now, cheapest_seat_micro=10) is None
    assert termination.check(wallet, clock.now, cheapest_seat_micro=11) == DORMANT
    wallet.commit(wallet.reserve(10, "h", "model"), 10)
    assert wallet.unlocked == 0 and wallet.locked == 90 and not wallet.dead
    assert termination.check(wallet, clock.now) == DORMANT
    with pytest.raises(ValueError, match="pause"):
        termination.kill(DORMANT)
    assert not termination.final and not ledger.final
    assert wallet.release_due(clock.now + 50) == 90
    assert termination.check(wallet, clock.now + 50) is None
    wallet.commit(wallet.reserve(90, "h2", "model"), 90)
    # Terminal death by budget needs locked == 0: nothing remains to wait for.
    assert wallet.locked == 0 and wallet.dead
    assert termination.check(wallet, clock.now + 50) == "balance_zero"
    with pytest.raises(ValueError):
        termination.check(wallet, clock.now, cheapest_seat_micro=-1)
