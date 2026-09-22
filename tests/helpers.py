"""Builders that several test files share.

Each builder returns an independent, network-free object; none holds state between
calls.
"""

from decimal import Decimal

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

# ---- spot inventory ------------------------------------------------------


def spot_venue():
    """A zero-fee, zero-spread fake venue with one spot pair and $100 of perps cash."""
    return FakeExchange(coins=("BTC", "ETH"), spot_pairs=("BTC/USDC",), start_cash_usd=Decimal(100),
                        start_prices={"BTC": Decimal(100), "ETH": Decimal(10)},
                        spread_bps=Decimal(0), fee_bps=Decimal(0), step_bps=Decimal(0))


def spot_runtime(exchange):
    """A scripted runtime with no ledger file, trading on ``exchange``."""
    return Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=.1, provider=ScriptedProvider(),
                   exchange=exchange)


def spot_producer(rt):
    """An open seed-decider verdict handle whose consequences have started."""
    from tests.runtime.test_loop import _consequence_decision

    handle = _consequence_decision(rt, "seed-decider", "verdict")
    rt.consequences.start(handle, 0)
    return handle


# ---- venue collateral ---------------------------------------------------


def venue_runtime(*, venue_usd="1000", wallet_micro=1_000_000) -> Runtime:
    """A scripted world whose venue is rich and whose compute wallet is not."""
    rt = Runtime(load_manifest("scripted"), events=0, seed=1,
                 initial_balance_micro=wallet_micro, ledger_path=None, router_gamma=.1,
                 provider=ScriptedProvider(),
                 exchange=FakeExchange(start_cash_usd=Decimal(venue_usd)))
    rt._manage_reserve_window()
    return rt


def collateral_decision(rt, owner="seed-decider"):
    """An open verdict handle owned by ``owner`` with its consequences started."""
    handle = rt.queue.open(
        actor=owner, event_id="venue-collateral", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=rt.clock.now_ns + 10**12, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = owner
    rt.consequences.start(handle, rt.n)
    return handle


def place(rt, size, *, side="buy", **args):
    """The venue's answer to one BTC market order placed by the seed decider."""
    return rt._run_tool("seed-decider", collateral_decision(rt), {
        "tool": "venue.place_market",
        "args": {"coin": "BTC", "side": side, "size": size, **args}})[0]


# ---- registration ------------------------------------------------------


def assembly(**changes):
    """A valid weather-desk assembly registration, with ``changes`` applied."""
    return {
        "kind": "assembly", "id": "weather-desk", "model_id": "model",
        "system_prompt": "Predict the weather.", "accepts": ["Tick"],
        "emits": ["WeatherForecast"],
        "schemas": {"WeatherForecast": {"type": "object", "properties": {}}},
        **changes,
    }
