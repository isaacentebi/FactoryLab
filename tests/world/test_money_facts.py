"""The money facts a seat is shown are true, scoped and in units it can read.

Chapter II §I.b (prices, custody and limits are public schematics) and §II.b (the
published contract is the enforced one). Wave 15 (investigator 8 on longrun1): a gas
blocker read "HyperCore transfer gas budget is exhausted" for a budget the manifest set to
zero, on every seat's pots, without saying it gates only the exit to the reserve; a seat
read it as the venue being unable to move. No fee rate was published anywhere. The
published treasury.transfer listed directions the rehearsal refused and described its
Venice source twice, differently. The seat view published a manifest's "$80 of Venice at
launch" while the account held $0.098. A seat read -77,814 micro-USD as -$77.8.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.continuity import OutcomeInbox
from factorylab.world.evm import RailError
from factorylab.world.exchange import FakeExchange, HyperliquidExchange
from factorylab.world.treasury import (
    GAS_BUDGET_KEYS,
    TRANSFER_DIRECTIONS,
    FakeHybridRail,
    FakeRail,
    Treasury,
    UnconfiguredRail,
    admitted_directions,
    gas_gates,
    transfer_tool_spec,
    venice_conversion_text,
)
from factorylab.world.treasury_rails import HybridRail
from factorylab.world.venue_tools import NO_GAS, VenueTools
from tests.world.test_treasury_rails import setup as live_rail


class Denying:
    """A rehearsal-style wrapper: it admits ``allowed`` and reads through otherwise."""

    def __init__(self, rail, allowed):
        self.rail, self.ALLOWED = rail, allowed

    def __getattr__(self, name):
        return getattr(self.rail, name)


# --- gas: what it gates, and a zero that reads as zero ------------------------------


def test_a_zero_gas_budget_reads_as_zero_never_as_exhausted():
    rail = live_rail()
    rail.hyper.gas_budget_wei = 0
    with pytest.raises(RailError) as refused:
        rail._core_gas_bound({})
    assert "treasury.hyperevm_gas_budget_wei is 0" in str(refused.value)
    assert "exhausted" not in str(refused.value) and "to_reserve" in str(refused.value)
    spent = live_rail()
    with pytest.raises(RailError, match="exhausted: 0 wei left"):
        spent._core_gas_bound({"hyper": spent.hyper.gas_budget_wei})


class Consulted(dict):
    """A gas_spent map that records every budget key a preflight reads from it: the one
    way a rail consults a native gas budget (``LiveRail.remaining``)."""

    def __init__(self):
        super().__init__()
        self.read: list[str] = []

    def get(self, key, default=None):
        self.read.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.append(key)
        return super().__getitem__(key)


def _live():
    from factorylab.runtime.worlds import TreasurySpec

    rail = live_rail()
    # The self-mint branch reaches every budget check the exit makes.
    rail.spec = TreasurySpec(reserve_address=rail.reserve_address, cctp_forwarding="never")
    rail.exchange._exchange = None  # no signer: a class move is refused before signing
    return rail


def _hybrid():
    rail = HybridRail.__new__(HybridRail)
    rail.__dict__.update(_live().__dict__)
    return rail


def _rails():
    wallet = Wallet(100_000_000, Ledger(clock_ns=lambda: 0), clock_ns=lambda: 0)
    exchange = FakeExchange(start_cash_usd=Decimal("50"))
    return {"live": _live(), "hybrid": _hybrid(),
            "unconfigured": UnconfiguredRail(SimpleNamespace(_exchange=None)),
            "fake": FakeRail(wallet, exchange=exchange),
            "fake-hybrid": FakeHybridRail(wallet, sink="0x" + "d" * 40, exchange=exchange)}


@pytest.mark.parametrize("name", ["live", "hybrid", "unconfigured", "fake", "fake-hybrid"])
def test_the_published_gas_gates_are_the_directions_whose_preflight_consults_a_budget(name):
    """For every rail type, pots.gas.gates names exactly the admitted directions whose
    preflight reads a native gas budget, and exactly the budgets it reads."""
    rail = _rails()[name]
    consulted = {}
    for direction in admitted_directions(rail):
        spent = Consulted()
        try:
            rail.preflight(direction, 10_000_000, spent)
        except (RailError, ValueError):
            pass  # a refusal after the budget check still consulted it
        if spent.read:
            consulted[direction] = sorted({GAS_BUDGET_KEYS[k] for k in spent.read})
    assert {d: sorted(keys) for d, keys in gas_gates(rail).items()} == consulted
    if name in ("live", "hybrid"):
        assert set(consulted) == {"to_reserve", "to_venue"}


def test_the_gas_position_covers_every_gated_direction_and_no_other():
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    rail = _live()
    treasury = Treasury(ledger, wallet, rail, clock_ns=lambda: 0)
    view = treasury._gas_view()
    assert set(view["gates"]) == {"to_reserve", "to_venue"}
    assert "no gas" in view["does_not_gate"] and "closes" in view["does_not_gate"]
    for direction in view["gates"]:
        assert {"refill_ready", "blocked_by"} <= set(view[direction])
    # The to_venue blocker is the one its own preflight names.
    rail.hyper.gas_budget_wei = 0
    view = treasury._gas_view()
    with pytest.raises(RailError) as refused:
        rail.preflight("to_venue", 10_000_000, {})
    assert view["to_venue"]["blocked_by"] == str(refused.value)
    assert view["to_venue"]["refill_ready"] is False
    # A direction this world does not admit is neither gated nor reported.
    treasury.rail = Denying(_live(), ("to_venue", "to_venice"))
    view = treasury._gas_view()
    assert set(view["gates"]) == {"to_venue"} and "to_reserve" not in view
    treasury.rail = Denying(_live(), ("to_venice",))
    view = treasury._gas_view()
    assert view["gates"] == {} and "to_reserve" not in view and "to_venue" not in view


def test_venue_writes_state_that_they_pay_no_gas():
    tools = VenueTools(FakeExchange(coins=("BTC",)), coins=("BTC",))
    for tool in ("venue.place_market", "venue.place_limit", "venue.close", "venue.cancel",
                 "venue.set_leverage"):
        assert NO_GAS in tools._specs[tool].description
    assert "fee rates" in tools._specs["venue.instruments"].description


# --- fees: the venue's own, beside lot, tick and minimum order ----------------------


def test_the_fake_venue_publishes_the_fee_it_charges():
    exchange = FakeExchange(coins=("BTC",), fee_bps=Decimal("4.5"))
    (row,) = exchange.instruments()["perp"]
    assert row["taker_fee_rate"] == row["maker_fee_rate"] == "0.00045"
    assert {"lot_size", "tick_size", "min_order_value_usd"} <= set(row)


def _hyperliquid(answer):
    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    exchange._address = "0x" + "1" * 40

    def user_fees(address):
        if isinstance(answer, Exception):
            raise answer
        return answer

    exchange._info = SimpleNamespace(user_fees=user_fees)
    exchange._sz_decimals = {"BTC": 5}
    exchange._listed_coins = ("BTC",)
    exchange._spot_names = {}
    exchange.coins = ("BTC",)
    exchange._fee_rates = exchange._read_fee_rates()
    return exchange


def test_hyperliquid_publishes_this_accounts_rates_as_the_venue_states_them():
    exchange = _hyperliquid({"userCrossRate": "0.00045", "userAddRate": "0.00015"})
    (row,) = exchange.instruments()["perp"]
    assert row["taker_fee_rate"] == "0.00045" and row["maker_fee_rate"] == "0.00015"
    assert row["min_order_value_usd"] == "10"
    # A market whose rates the venue did not state is unavailable, never a number.
    assert exchange._fee_rates["spot"]["fee_rates"] == "unavailable"


@pytest.mark.parametrize("answer", [ConnectionError("down"), {}, {"userCrossRate": "x"}])
def test_an_unread_fee_rate_is_unavailable_never_invented(answer):
    exchange = _hyperliquid(answer)
    (row,) = exchange.instruments()["perp"]
    assert row["fee_rates"] == "unavailable" and row["reason"]
    assert "taker_fee_rate" not in row and "maker_fee_rate" not in row


# --- treasury.transfer: only what this world admits, described once ----------------


def test_the_published_directions_are_the_ones_the_rail_admits():
    assert admitted_directions(SimpleNamespace()) == TRANSFER_DIRECTIONS
    assert admitted_directions(UnconfiguredRail(None)) == ("spot_to_perps", "perps_to_spot")
    assert "to_venice" in HybridRail.ALLOWED
    capital_loop = Denying(object(), ("to_venice",))
    spec = transfer_tool_spec(admitted_directions(capital_loop), hybrid=True)
    assert spec["args_schema"]["properties"]["direction"]["enum"] == ["to_venice"]
    assert [e["direction"] for e in spec["args_schema"]["examples"]] == ["to_venice"]
    for denied in ("to_reserve", "to_venue", "spot_to_perps", "perps_to_spot"):
        assert denied not in spec["description"]
    assert transfer_tool_spec((), hybrid=False) is None


def test_the_venice_source_is_one_true_statement_per_world():
    hybrid, plain = venice_conversion_text(True), venice_conversion_text(False)
    assert "perps withdrawable" in hybrid and "Base mainnet USDC" in hybrid
    assert "no reserve USDC is needed" not in hybrid
    assert "reserve USDC" in plain and "perps" not in plain
    spec = transfer_tool_spec(TRANSFER_DIRECTIONS, hybrid=True)
    assert spec["description"].count("to_venice converts") == 1
    assert hybrid in spec["description"]


def test_a_runtime_publishes_no_direction_its_rail_refuses():
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest

    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    assert rt.tool_specs["treasury.transfer"]["args_schema"]["properties"]["direction"][
        "enum"] == list(TRANSFER_DIRECTIONS)
    rt.treasury.rail.target = Denying(rt.treasury.rail.target, ("to_venice",))
    block = rt._world_block()
    spec = rt.tool_specs["treasury.transfer"]
    assert spec["args_schema"]["properties"]["direction"]["enum"] == ["to_venice"]
    assert "to_venice" in block["compute_supply"]["venice"]
    rt.treasury.rail.target = Denying(rt.treasury.rail.target, ())
    block = rt._world_block()
    assert "treasury.transfer" not in rt.tool_specs
    assert "No treasury.transfer direction converts" in block["compute_supply"]["venice"]


# --- what a seat is shown of its own money ------------------------------------------


def test_the_seat_view_publishes_no_manifest_commitment_as_a_balance():
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest

    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    inventory = rt._provider_inventory()
    assert "committed_at_launch" not in inventory
    assert "[providers]" not in inventory["as_of"]


def test_outcome_amounts_carry_their_dollars_beside_the_micro_usd():
    from factorylab.kernel.artifacts import ArtifactStore

    ledger = Ledger(clock_ns=lambda: 0)
    inbox = OutcomeInbox(ArtifactStore(ledger, root=None, clock_ns=lambda: 1), ledger,
                         lambda: 0)
    record = inbox.append("seat", handle="decision-1", delta_micro=-77_814,
                          outcome={"kind": "consequence", "net_micro": -77_814,
                                   "venue_delta_micro": {"fee_micro": 38_100}})
    entry = inbox.index_of("seat", record)
    assert entry["delta_micro"] == -77_814 and entry["delta_usd"] == "-0.077814"
    view = inbox.get("seat", "outcome:1")
    assert view["outcome"]["net_usd"] == "-0.077814"
    assert view["outcome"]["venue_delta_micro"]["fee_usd"] == "0.038100"
    assert view["delta_usd"] == "-0.077814"
