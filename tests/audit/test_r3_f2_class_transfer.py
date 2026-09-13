"""Round three, group F2, triage T5: a live class transfer must be able to confirm.

The venue stamps its ``accountClassTransfer`` ledger row with its own execution time,
which is later than the millisecond nonce this client signed at prepare. Matching the
two by equality can never succeed, so the transfer stays ``submitted`` for ever and,
because a pending transfer prevents another, the treasury jams for the world's life.

Offline throughout: the venue is a recorded ledger row, no socket is opened. The live
proof of the same path is the last test, skipped unless FL_LIVE_T5 names the direction.
"""

import os
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.exchange import AccountState, SpotBalance
from factorylab.world.treasury import (
    CLASS_EXECUTION_TOLERANCE_MS,
    Treasury,
    UnconfiguredRail,
)

VENUE_LATENCY_MS = 1_835  # measured by seat 6: nonce 1789276841701, row time 1789276843536


class Venue:
    """Hyperliquid's read surface for a class transfer, with its own execution clock."""

    name = "hyperliquid-testnet"
    spot_pairs = ("PURR/USDC",)

    def __init__(self):
        self._address = "0x1228e5620944a79d268afc7522e00891526edebb"
        self._exchange = SimpleNamespace(
            wallet=SimpleNamespace(address=self._address.upper()), vault_address=None
        )
        self.rows: list[dict] = []
        self._info = SimpleNamespace(
            user_non_funding_ledger_updates=lambda user, since: [
                dict(row) for row in self.rows if row["time"] >= since
            ],
            user_state=lambda _user: {"marginSummary": {"accountValue": "60"}},
        )

    def account(self) -> AccountState:
        return AccountState(
            Decimal(100),
            Decimal(60),
            (),
            Decimal(0),
            (SpotBalance("USDC", Decimal(40), Decimal(40)),),
        )


class Rail(UnconfiguredRail):
    """The venue executes the signed action a measured moment after it is sent."""

    def send(self, step: str, reference: dict) -> None:
        self.exchange.rows.append(
            {
                "time": reference["nonce"] + VENUE_LATENCY_MS,
                "hash": "0x9f8b7bfa" + str(len(self.exchange.rows)),
                "delta": {
                    "type": "accountClassTransfer",
                    "usdc": reference["action"]["amount"],
                    "toPerp": reference["action"]["toPerp"],
                },
            }
        )


def _treasury():
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    venue = Venue()
    treasury = Treasury(ledger, wallet, Rail(venue), fee_ceiling_micro=0)
    wallet.bind_pots(treasury.pots)
    return treasury, venue, ledger


def test_t5_a_class_transfer_the_venue_executed_reaches_confirmed():
    """The row arrives 1.8 s after the nonce, which is the whole of the defect."""
    treasury, _venue, _ledger = _treasury()
    assert (
        treasury.transfer("perps_to_spot", "20", handle="a", now_ns=1_789_276_841_701_000_000)[
            "status"
        ]
        == "submitted"
    )
    results = treasury.tick(1_789_276_845_000_000_000)
    assert [r["status"] for r in results] == ["confirmed"], (
        "the venue executed the transfer and the treasury never confirmed it"
    )
    assert treasury.state["principal_moved"] and treasury.state["fees_micro"] == 0


def test_t5_a_confirmed_transfer_leaves_the_pots_view_complete_and_unblocked():
    """A jammed transfer keeps ``pots`` incomplete and refuses every later transfer."""
    treasury, _venue, _ledger = _treasury()
    treasury.transfer("perps_to_spot", "20", handle="a", now_ns=1_789_276_841_701_000_000)
    treasury.tick(1_789_276_845_000_000_000)
    pots = treasury.pots()
    assert pots["pending"] is False and pots["complete"] is True
    assert (
        treasury.transfer("spot_to_perps", "5", handle="b", now_ns=1_789_276_900_000_000_000)[
            "status"
        ]
        == "submitted"
    )


def test_t5_a_row_outside_the_execution_window_confirms_nothing():
    """The window is the only thing that makes the match unique; it still binds."""
    treasury, venue, _ledger = _treasury()
    treasury.transfer("perps_to_spot", "20", handle="a", now_ns=1_789_276_841_701_000_000)
    venue.rows[0]["time"] += CLASS_EXECUTION_TOLERANCE_MS
    assert treasury.tick(1_789_276_845_000_000_000) == []
    assert treasury.state["status"] == "submitted"


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("FL_LIVE_T5") != "perps_to_spot",
    reason="live testnet class transfer runs only when explicitly asked",
)
def test_t5_live_testnet_perps_to_spot_confirms():
    """One real testnet class transfer of a few dollars, confirmed from venue evidence.

    Testnet only, and never a mainnet venue: the exchange is constructed with
    ``mainnet=False`` in this module and nowhere else.
    """
    from factorylab.runtime.cli import _load_dotenv
    from factorylab.world.exchange import HyperliquidExchange

    # The signing key reaches the process the way the CLI puts it there, and no
    # other way: it is never read, printed or copied by this test.
    _load_dotenv()
    amount = os.environ.get("FL_LIVE_T5_USD", "3")
    exchange = HyperliquidExchange(mainnet=False, coins=("BTC",), spot_pairs=("PURR/USDC",))
    assert exchange.name == "hyperliquid-testnet"
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(50_000_000, ledger, clock_ns=lambda: 0)
    treasury = Treasury(ledger, wallet, UnconfiguredRail(exchange), fee_ceiling_micro=0)
    wallet.bind_pots(treasury.pots)
    import time

    started = time.time_ns()
    assert (
        treasury.transfer("perps_to_spot", amount, handle="live", now_ns=started)["status"]
        == "submitted"
    )
    results = []
    for _ in range(30):
        time.sleep(2)
        results = treasury.tick(time.time_ns())
        if results or treasury.state["status"] != "submitted":
            break
    print(
        [i for i in ledger._recovery_items() if str(i.get("kind", "")).startswith("treasury.")][-4:]
    )
    print(treasury.pots())
    assert results and results[0]["status"] == "confirmed"
    assert treasury.pots()["complete"] is True
