"""Edition 3, C5: the first world's manifest, the kill contract, and the calibration gate.

Three things are pinned here. The manifest (`worlds/edition3-testnet.toml`) is the roster,
the money and the kill contract of GPT-6 Pro's architect reading, and its hash is pinned so
a silent edit to the physics is a test failure rather than a surprise at ratification. The
kill contract is the promise that a dead factory carries no exposure: with
`[kill] wind_down = true` a kill empties the venue first and says so in the diary and in the
witness line, with it false nothing is touched, and in neither case may a venue delay
finality. The calibration gate is the screen every candidate route passes before the
population is seated: at least forty bounded cases, each with one determinate outcome.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.runtime import witness
from factorylab.runtime.loop import Runtime
from factorylab.runtime.venue import wind_down
from factorylab.runtime.worlds import KillSpec, load_manifest
from factorylab.world.exchange import Order


@pytest.fixture(autouse=True)
def _fresh_witness(monkeypatch):
    """No receiver, no inherited kill note: each test witnesses only its own kill."""
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, "_killed_here", set())
    witness.note_wind_down(wind_down=False, orders=0)


# --- the manifest -------------------------------------------------------------------------


def _exposed_runtime(*, wind: bool, ledger_path=None) -> Runtime:
    """A scripted world holding one resting order, one perp position and one spot balance."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, kill=KillSpec(wind_down=wind))
    rt = Runtime(manifest, events=40, seed=1, initial_balance_micro=None,
                 ledger_path=ledger_path, drip=False, router_gamma=0.1)
    exchange = rt.exchange
    mid = exchange.mids()["BTC"]
    # One resting limit order, far from the mid so it cannot cross into a fill.
    resting = exchange.place(Order("BTC", True, Decimal("0.001"), kind=_limit(),
                                   limit_px=(mid / 2).quantize(Decimal("0.01")),
                                   client_id="resting-1"))
    assert resting.status == "resting"
    # One open perp position.
    assert exchange.place(Order("BTC", True, Decimal("0.002"),
                                client_id="perp-1")).status == "filled"
    # One spot balance, bought with spot cash.
    rt.treasury.transfer("perps_to_spot", "20", handle="transfer", now_ns=1)
    rt.treasury.tick(2)
    exchange.sync_cash(rt.treasury.venue_balance_usd)
    assert exchange.place(Order("BTC/USDC", True, Decimal("0.0002"), market="spot",
                                client_id="spot-1")).status == "filled"
    exchange.drain_events()

    account = exchange.account()
    assert len(exchange.open_orders()) == 1
    assert [p.coin for p in account.positions if p.size] == ["BTC"]
    assert [b.coin for b in account.spot_balances if b.coin != "USDC" and b.total > 0]
    return rt


def _limit():
    from factorylab.world.exchange import OrderKind

    return OrderKind.LIMIT


def test_wind_down_empties_the_venue_and_ledgers_every_order_before_terminated():
    """C5: cancel, close, sell, each ledgered with its result, all of it before the end."""
    rt = _exposed_runtime(wind=True)
    report = rt.kill("explicit_kill:operator")

    assert report["attempted"] and report["failed"] == 0
    assert report["cancelled"] == 1 and report["closed"] == 1 and report["sold"] == 1
    assert report["orders"] == 3

    account = rt.exchange.account()
    assert rt.exchange.open_orders() == []
    assert [p for p in account.positions if p.size] == []
    assert [b for b in account.spot_balances if b.coin != "USDC" and b.total > 0] == []

    items = list(rt.ledger.items())
    # R3-C: each operation is its own pair of records, identified before it is sent
    # and answered after it, and ``kill.wind_down`` keeps the summary and the notes.
    ops = [i for i in items if i["kind"] == "winddown.op"]
    results = [i for i in items if i["kind"] == "winddown.op_result"]
    assert [o["op"] for o in ops] == ["cancel", "close", "sell"]
    assert [r["op"] for r in results] == ["cancel", "close", "sell"]
    assert all(r["result"]["status"] in ("cancelled", "filled") for r in results)
    assert [r["op_id"] for r in results] == [o["op_id"] for o in ops]
    assert all(o["op_id"].startswith("wd-") for o in ops)
    steps = [i for i in items if i["kind"] == "kill.wind_down"]
    assert [s["step"] for s in steps] == ["summary"]
    assert steps[-1]["orders"] == 3 and steps[-1]["operations"] == 3
    # The account was read once more at the end: an acknowledgement is not flat.
    reconciliation = [i for i in items if i["kind"] == "winddown.reconciliation"]
    assert len(reconciliation) == 1
    assert reconciliation[0]["exposure_state"] == "flat"
    assert report["exposure_state"] == "flat" and report["production_state"] == "killed"
    # Production died before the first operation and the seal came after the last.
    mark = [i for i in items if i["kind"] == "kill.production"]
    assert len(mark) == 1 and mark[0]["production_state"] == "killed"
    assert mark[0]["seq"] < min(o["seq"] for o in ops)
    # The whole wind-down is in the diary before the event that seals it.
    terminated = [i for i in items
                  if (i.get("event") or {}).get("kind") == "Terminated"]
    assert len(terminated) == 1
    last_wind_down = max(i["seq"] for i in items
                         if i["kind"].startswith(("kill.", "winddown.")))
    assert terminated[0]["seq"] > last_wind_down
    assert terminated[0]["seq"] == max(i["seq"] for i in items)
    assert rt.termination.final and rt.termination.reason == "explicit_kill:operator"


def test_an_unreachable_venue_is_ledgered_and_the_world_still_dies():
    """A kill a venue could block is not a kill. Every read and every order is guarded."""

    class Unreachable:
        """Answers nothing: reads raise, and so would any write that got that far."""

        name = "unreachable"

        def open_orders(self):
            raise OSError("venue unreachable")

        def account(self):
            raise OSError("venue unreachable")

        def mids(self):
            raise OSError("venue unreachable")

    rt = _exposed_runtime(wind=True)
    rt.exchange = Unreachable()
    report = rt.kill("explicit_kill:operator")

    assert rt.termination.final and rt.termination.reason == "explicit_kill:operator"
    # Three reads, each guarded on its own (R3-C separates the price read from the
    # account read, so an unpriceable balance is not mistaken for an absent one).
    assert report["failed"] == 3 and report["orders"] == 0
    assert {e["step"] for e in report["errors"]} == {"open_orders", "account", "mids"}
    assert {e["error"] for e in report["errors"]} == {"OSError"}
    failures = [i for i in rt.ledger.items()
                if i["kind"] == "kill.wind_down" and i.get("step") == "read_failed"]
    assert {f["read"] for f in failures} == {"open_orders", "account", "mids"}
    assert all(f["error"] == "OSError" for f in failures)
    # A venue that answers nothing leaves an exposure nobody can describe.
    assert report["exposure_state"] == "unknown"


def test_a_refusing_venue_is_recorded_order_by_order_and_never_raises():
    """A venue that rejects every write leaves a legible partial wind-down, not an exception."""

    class Refusing:
        name = "refusing"

        def open_orders(self):
            return [{"order_id": "1", "coin": "BTC", "side": "buy", "size": Decimal("1"),
                     "price": Decimal("1")}]

        def account(self):
            from factorylab.world.exchange import AccountState, Position, SpotBalance

            return AccountState(
                equity_usd=Decimal(0), cash_usd=Decimal(0),
                positions=(Position("BTC", Decimal("0.5"), Decimal("60000")),),
                margin_used_usd=Decimal(0),
                spot_balances=(SpotBalance("PURR", Decimal("100"), Decimal("100")),))

        def mids(self):
            return {"BTC": Decimal("60000"), "PURR/USDC": Decimal("4.6")}

        def cancel(self, order_id, *, coin=None, client_id=None):
            raise RuntimeError("venue refused the cancel")

        def close(self, coin, size=None, *, client_id=None, market="perp"):
            return {"status": "rejected", "error": "venue refused the close"}

    ledger = _MemoryLedger()
    from factorylab.runtime.winddown import ROUNDS_PER_KILL

    report = wind_down(Refusing(), ledger)
    # The cancel raised (ambiguous: read, never resent); the two definitive
    # rejections are retried under new identities, a bounded number of rounds.
    sent = 1 + 2 * ROUNDS_PER_KILL
    assert report["orders"] == sent and report["failed"] == sent
    assert report["cancelled"] == report["closed"] == report["sold"] == 0
    ops = [i for i in ledger.rows if i["kind"] == "winddown.op"]
    assert [i["op"] for i in ops[:3]] == ["cancel", "close", "sell"]
    assert [i["op"] for i in ops[3:]] == ["close", "sell"] * (ROUNDS_PER_KILL - 1)
    assert len({i["op_id"] for i in ops}) == len(ops)
    results = [i for i in ledger.rows if i["kind"] == "winddown.op_result"]
    assert results[0]["result"] == {"status": "failed", "error": "RuntimeError"}
    # Nothing was left flat and the executor says so, from the account and not from
    # the three refusals: the resting order, the position and the balance are all there.
    assert report["exposure_state"] == "wind_down_pending"
    assert [i["exposure_state"] for i in ledger.rows
            if i["kind"] == "winddown.reconciliation"] == ["wind_down_pending"]


class _MemoryLedger:
    """Just enough ledger for the wind-down: it appends, and it keeps what it appended."""

    def __init__(self):
        self.rows: list[dict] = []

    def append(self, item: dict) -> int:
        self.rows.append(item)
        return len(self.rows)


# --- the calibration gate ------------------------------------------------------------------
