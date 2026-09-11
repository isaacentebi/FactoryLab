import pytest

from factorylab.kernel.events import Bus, Event
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.registry import Registry
from factorylab.kernel.termination import Termination
from factorylab.kernel.wallet import Infeasible, Wallet


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
