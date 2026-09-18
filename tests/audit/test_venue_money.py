"""Regressions for the venue and money audit (branch ``fix/venue-money``).

Each test reproduces one verified defect and failed before its fix. They are
grouped by concern, one class per architect decision or numbered fix.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, HyperliquidExchange
from factorylab.world.venue_tools import VenueTools
from tests.audit.test_r1_venue_collateral import place, venue_runtime
from tests.runtime.test_connectors import ledger_items


def _live_stub(**state) -> HyperliquidExchange:
    """A Hyperliquid adapter with every SDK call stubbed; no network is reachable."""
    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex.coins = ("BTC", "ETH")
    ex.spot_pairs = ()
    ex._address = "0xabc"
    ex._info = Mock()
    ex._exchange = Mock()
    ex._sz_decimals = {"BTC": 5, "ETH": 4}
    ex._spot_names, ex._spot_tokens, ex._spot_marks, ex._spot_universe = {}, {}, {}, {}
    ex.transient_failures = 0
    ex.account_fallbacks = 0
    ex._last_mids = None
    ex._last_account = None
    ex._last_account_ns = None
    ex._leverage = {}
    ex._account_mode = "cross"
    ex._info.open_orders.return_value = []
    ex._info.all_mids.return_value = {"BTC": "60000", "ETH": "2500"}
    ex._info.user_state.return_value = state or {
        "marginSummary": {"accountValue": "100", "totalMarginUsed": "0"},
        "assetPositions": []}
    return ex


# ------------------------------------------------------------------------------ D1


class TestD1NoLeverageOrPrincipalCap:
    """The population may use whatever leverage and principal the venue allows."""

    def test_a_declared_principal_no_longer_caps_the_collateral_view(self):
        manifest = load_manifest("scripted")
        manifest = replace(manifest, exchange=replace(manifest.exchange, principal_usd="120"))
        from factorylab.runtime.loop import Runtime
        from factorylab.world.scripted import ScriptedProvider

        rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=50_000_000,
                     ledger_path=None, drip=False, router_gamma=.1,
                     provider=ScriptedProvider(),
                     exchange=FakeExchange(start_cash_usd=Decimal(966)))
        rt._manage_reserve_window()
        view = rt._collateral_view("BTC")
        assert view["eligible_equity_usd"] == Decimal(966)
        assert "principal_cap_usd" not in view
        # $900 of notional at the venue's 3x needs $300: more than the old $120 cap.
        beyond = (Decimal(900) / rt.exchange.mids()["BTC"]).quantize(Decimal("0.00001"))
        assert place(rt, str(beyond))["status"] == "filled"
        assert not ledger_items(rt, "order.infeasible")

    def test_old_manifests_with_the_deprecated_keys_still_load_and_keep_their_hash(self):
        m = load_manifest("edition3-testnet")
        assert m.exchange.principal_usd == "120"  # read, and inert
        payload = json.loads(m.canonical_json())
        assert payload["exchange"]["principal_usd"] == "120"
        assert payload["tools"]["max_leverage"] == 3

    def test_set_leverage_has_no_manifest_ceiling_the_venue_decides(self):
        exchange = FakeExchange(max_leverage=Decimal(20))
        venue = VenueTools(exchange, coins=("BTC",), max_leverage=2)
        spec = next(s for s in venue.contracts() if s.id == "venue.set_leverage")
        assert "maximum" not in spec.args_schema["properties"]["leverage"]
        assert venue.call(spec.id, {"coin": "BTC", "leverage": 10})["status"] == "ok"
        # Above the venue's own rule the venue refuses; that refusal is the answer.
        assert venue.call(spec.id, {"coin": "BTC", "leverage": 50})["status"] == "rejected"

    def test_the_live_view_reports_the_leverage_the_venue_has_in_effect(self):
        ex = _live_stub(**{
            "marginSummary": {"accountValue": "100", "totalMarginUsed": "30"},
            "assetPositions": [{"position": {"coin": "BTC", "szi": "0.01", "entryPx": "60000",
                                             "leverage": {"type": "cross", "value": 20}}}]})
        view = ex.collateral_view("BTC")
        assert view["leverage_for_instrument"] == Decimal(20)
        # A coin whose leverage the venue has not told us is unknown, not 1x.
        assert ex.collateral_view("ETH")["leverage_for_instrument"] is None

    def test_unknown_live_leverage_leaves_the_venue_as_the_authority(self):
        rt = venue_runtime(venue_usd="1000")
        view = {"eligible_equity_usd": Decimal(100), "margin_used_usd": Decimal(0),
                "open_order_holds_usd": Decimal(0), "holds_included_in_margin_used": False,
                "leverage_for_instrument": None, "position_size": Decimal(0),
                "spot_available": {"USDC": Decimal(0)}, "observed_at_ns": rt.clock.now_ns}
        # $600 of notional against $100 of equity: refused at an assumed 1x, but the
        # venue's leverage is unknown here, so the venue's own answer decides.
        assert rt._perp_collateral(view, "BTC", Decimal("0.01"), True,
                                   Decimal(60000), Decimal(0)) is None
