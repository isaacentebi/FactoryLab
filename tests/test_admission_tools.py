"""Published tool boundaries track executable admission, without external calls."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.cortex.admission_tools import tool_admission_schematics
from factorylab.cortex.tools import PopulationTool, ToolRunner, _validate_args
from factorylab.runtime.compute import ComputeMixin
from factorylab.runtime.venue import VenueMixin
from factorylab.runtime.worlds import load_manifest
from factorylab.world import connector


@pytest.fixture
def facts():
    return tool_admission_schematics(load_manifest("scripted"))


def test_connector_published_bounds_are_enforced(facts):
    c = facts["connector"]
    connector.validate_path("/" + "x" * (c["max_path_chars"] - 1))
    with pytest.raises(connector.ConnectorRefused):
        connector.validate_path("/" + "x" * c["max_path_chars"])
    host = "a" * c["max_host_label_chars"] + ".example"
    assert connector.origin_host("https://" + host) == host
    with pytest.raises(connector.ConnectorRefused):
        connector.origin_host("https://a" + host)
    with pytest.raises(connector.ConnectorRefused):
        connector.origin_host("https://" + "a" * c["max_origin_chars"])


@pytest.mark.parametrize("path", ["//other", "/a#b", "/a\\b", "/a b", "/é", "/a\n"])
def test_connector_path_predicates_refuse(path):
    with pytest.raises(connector.ConnectorRefused):
        connector.validate_path(path)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://EXAMPLE.com",
        "https://example.com:443",
        "https://u@example.com",
        "https://example.com/",
        "https://-x.example",
        "https://x..example",
    ],
)
def test_connector_origin_predicates_refuse(origin):
    with pytest.raises(connector.ConnectorRefused):
        connector.origin_host(origin)


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "localhost", "x.local", "x.internal", "example.com", "sub.example.com"]
)
def test_connector_host_predicates_refuse(host):
    with pytest.raises(connector.ConnectorRefused):
        connector.check_host(host, ("example.com",))


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.1.1", "224.0.0.1", "::ffff:8.8.8.8", "8.8.8.8"]
)
def test_connector_address_predicates_refuse(address):
    with pytest.raises(connector.ConnectorRefused):
        connector.check_address(address, ("8.8.8.0/24",))


def test_population_cpu_and_output_caps_drive_runner(facts, monkeypatch):
    import factorylab.cortex.tools as tools

    seen = {}

    def run(_code, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(timed_out=False, returncode=0, stderr="", stdout="{}")

    monkeypatch.setattr(tools, "jail_available", lambda: True)
    monkeypatch.setattr(tools, "run_python", run)
    runner = ToolRunner()
    schema = {"type": "object", "properties": {}}
    tool = PopulationTool("x", "", schema, "", 20, "h")
    assert runner.run(tool, {}) == {}
    assert seen["cpu_s"] == facts["population"]["cpu_seconds"]
    assert runner.max_output_bytes == facts["population"]["default_max_output_bytes"]
    monkeypatch.setattr(
        tools,
        "run_python",
        lambda *a, **k: SimpleNamespace(
            timed_out=False, returncode=0, stderr="", stdout=" " * (runner.max_output_bytes + 1)
        ),
    )
    assert runner.run(tool, {}) == {"error": "output too large"}


@pytest.mark.parametrize("args", [{}, {"n": True}, {"n": 1, "extra": 2}, {1: 2}])
def test_population_schema_predicates_refuse(args):
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    assert _validate_args(schema, args) is not None
    assert _validate_args(schema, {"n": 1}) is None


def test_population_return_contract_and_jail_refuse(monkeypatch):
    import factorylab.cortex.tools as tools

    schema = {"type": "object", "properties": {}}
    tool = PopulationTool(
        "x", "", schema, "", 1, "h", {"type": "object", "properties": {}, "required": ["answer"]}
    )
    monkeypatch.setattr(tools, "jail_available", lambda: False)
    assert ToolRunner().run(tool, {}) == {"error": "no jail on this host"}
    monkeypatch.setattr(tools, "jail_available", lambda: True)
    monkeypatch.setattr(
        tools,
        "run_python",
        lambda *a, **k: SimpleNamespace(timed_out=False, returncode=0, stderr="", stdout="{}"),
    )
    assert "returns_schema" in ToolRunner().run(tool, {})["error"]


def test_write_authority_checks_each_ancestor(facts):
    channel = facts["dispatch"]["writing_channels"][0]
    decisions = {
        "child": SimpleNamespace(parent_handle="parent", channel=channel),
        "parent": SimpleNamespace(parent_handle=None, channel=channel),
    }
    rt = SimpleNamespace(
        queue=SimpleNamespace(get=decisions.__getitem__),
        WRITING_CHANNELS=ComputeMixin.WRITING_CHANNELS,
        return_kinds={},
        consequences=SimpleNamespace(account_open=lambda _: True),
    )
    assert ComputeMixin._may_write(rt, "child")
    rt.return_kinds["parent"] = "Verdict"
    assert not ComputeMixin._may_write(rt, "child")
    rt.return_kinds.clear()
    decisions["parent"].channel = "policy"
    assert not ComputeMixin._may_write(rt, "child")
    del decisions["parent"]
    assert not ComputeMixin._may_write(rt, "child")


def test_collateral_freshness_enforced_at_published_boundary(facts):
    age = facts["orders"]["collateral_max_age_ns"]
    rt = SimpleNamespace(
        exchange=SimpleNamespace(deterministic=False),
        clock=SimpleNamespace(now_ns=age + 1),
        m=SimpleNamespace(tick_interval_ns=age),
    )
    assert VenueMixin._collateral_stale(rt, {"observed_at_ns": 1}) is None
    assert VenueMixin._collateral_stale(rt, {"observed_at_ns": 0}) is not None
    assert VenueMixin._collateral_stale(rt, {"observed_at_ns": age, "stale": True})
    assert VenueMixin._collateral_stale(rt, {})


def test_perp_margin_includes_holds_and_batch_commitments():
    rt = SimpleNamespace(_order_leverage=lambda *a: Decimal(2))
    view = {
        "position_size": 0,
        "open_order_holds_usd": "1",
        "holds_included_in_margin_used": False,
        "eligible_equity_usd": Decimal(7),
        "margin_used_usd": Decimal(1),
    }
    assert (
        VenueMixin._perp_collateral(rt, view, "BTC", Decimal(1), True, Decimal(10), Decimal(0))
        is None
    )
    assert VenueMixin._perp_collateral(rt, view, "BTC", Decimal(1), True, Decimal(10), Decimal(1))


def test_spot_balance_and_inventory_refusals():
    view = {"spot_available": {"USDC": "10", "BTC": "1"}}
    assert (
        VenueMixin._spot_collateral(
            None, view, "BTC/USDC", Decimal(1), True, Decimal(10), Decimal(0)
        )
        is None
    )
    assert VenueMixin._spot_collateral(
        None, view, "BTC/USDC", Decimal(1), True, Decimal(10), Decimal(1)
    )
    assert VenueMixin._spot_collateral(
        None, view, "BTC/USDC", Decimal(2), False, Decimal(10), Decimal(0)
    )
    rt = SimpleNamespace(
        spot_inventory={"BTC/USDC": (Decimal(2), Decimal(0))},
        consequences=SimpleNamespace(
            table=SimpleNamespace(
                lots=[SimpleNamespace(coin="BTC/USDC", market="spot", size=Decimal(1))]
            )
        ),
    )
    assert VenueMixin._spot_shortfall(rt, "venue.close", {"market": "spot", "coin": "BTC/USDC"})


def test_venue_read_weight_boundary(facts):
    from factorylab.world.venue_tools import public_read_weight

    for tool, weight in facts["venue_reads"]["base_weights"].items():
        if tool not in facts["venue_reads"]["item_weights"]:
            assert public_read_weight(tool, {}) == weight
    rt = SimpleNamespace(
        venue_read_share=lambda: 2,
        _venue_read_used=lambda _: 0,
        PUBLIC_READ_REFUSAL=ComputeMixin.PUBLIC_READ_REFUSAL,
        m=SimpleNamespace(exchange=SimpleNamespace(coins=("BTC",))),
    )
    rt._venue_read_weight = lambda tool, args: ComputeMixin._venue_read_weight(rt, tool, args)
    assert ComputeMixin._venue_read_refusal(rt, "seat", "venue.mids", {}) is None
    rt._venue_read_used = lambda _: 1
    assert ComputeMixin._venue_read_refusal(rt, "seat", "venue.mids", {})


def test_polymarket_principal_and_order_budget_are_enforced_at_their_published_bounds():
    """The pot's principal cap and its request budget, as world.read publishes them."""
    from dataclasses import replace

    from factorylab.cortex.admission_tools import tool_admission_schematics
    from factorylab.runtime.polymarket import PRINCIPAL_REFUSAL, principal_excess
    from factorylab.runtime.worlds import PolymarketSpec
    from factorylab.world.polymarket_clob import BudgetSpent, RequestBudget

    manifest = replace(load_manifest("scripted"), polymarket=PolymarketSpec(
        enabled=True, principal_micro=40_000_000, order_requests_per_10s=7))
    facts = tool_admission_schematics(manifest)["polymarket_orders"]
    cap = facts["principal_micro"]
    surface = SimpleNamespace(spec=manifest.polymarket, settled=Decimal(0), live=False,
                              open_fees=[])

    def pot(usdc, tokens="0", avg="0.5"):
        return {"usdc": usdc, "positions": [{"size": tokens, "avg_px": avg}]}

    assert principal_excess(surface, pot(str(Decimal(cap) / 1_000_000))) is None
    assert principal_excess(surface, pot("39.999999", "0.000002", "1")) == PRINCIPAL_REFUSAL
    # What the pot earned itself never counts against its principal.
    surface.settled = Decimal(5)
    assert principal_excess(surface, pot("45")) is None
    budget = RequestBudget(facts["order_requests_per_10s"], wall=lambda: 10**18)
    for _ in range(facts["order_requests_per_10s"]):
        budget.take()
    with pytest.raises(BudgetSpent):
        budget.take()
    later = RequestBudget(1, wall=iter((0, 10_000_000_000)).__next__)
    later.take()
    later.take()  # a request 10 s later is outside the window
