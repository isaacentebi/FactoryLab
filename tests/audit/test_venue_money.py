"""Regressions for the venue and money audit (branch ``fix/venue-money``).

Each test reproduces one verified defect and failed before its fix. They are
grouped by concern, one class per architect decision or numbered fix.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

import pytest

from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, HyperliquidExchange
from factorylab.world.venue_tools import VenueTools
from tests.helpers import place, venue_runtime
from tests.runtime.test_connectors import ledger_items

# Every signature here goes through the production chokepoint, with a real
# ReserveGuard in this test's temporary lock directory (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("write_ahead")



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
                     ledger_path=None, router_gamma=.1,
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
        import tomllib

        from factorylab.runtime.worlds import manifest_from_dict

        # Edition 3's own file names both keys, and its nine-seat roster no longer meets
        # the evaluator population the kernel requires, so it is refused whole (R8).
        with pytest.raises(ValueError, match="evaluator population"):
            load_manifest("edition3-testnet")
        # The keys themselves still load, read and hashed, on a roster that loads.
        with open("worlds/edition6-testnet-rehearsal.toml", "rb") as fh:
            raw = tomllib.load(fh)
        raw["venue"]["principal_usd"] = "120"
        raw.setdefault("tools", {})["max_leverage"] = 3
        m = manifest_from_dict(raw)
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


# ------------------------------------------------------------------------------ 1


def _lost_ack_runtime(monkeypatch):
    """A live-shaped runtime whose venue loses the first BTC submit and never reports it."""
    from factorylab.runtime.live import LiveClock
    from factorylab.world.exchange import OrderResult
    from tests.conftest import make_runtime

    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    exchange = rt.exchange.target
    real_place, real_lookup = exchange.place, exchange.lookup
    lost = set()

    def place(order):
        if not lost:
            lost.add(order.client_id)
            return OrderResult(None, "uncertain", Decimal(0), None, "submit timeout")
        return real_place(order)

    def lookup(client_id, **kw):
        if client_id in lost:
            return OrderResult(None, "uncertain", Decimal(0), None, "order not observed")
        return real_lookup(client_id, **kw)

    monkeypatch.setattr(exchange, "place", place)
    monkeypatch.setattr(exchange, "lookup", lookup)
    return rt


class TestFix1NoCoinFreeze:
    """One lost acknowledgement may not shut a coin for every seat."""

    def test_another_seats_uncertain_order_never_blocks_a_place_close_or_cancel(
            self, monkeypatch):
        from tests.audit.test_r3_b_authority import _producing_decision

        rt = _lost_ack_runtime(monkeypatch)
        lost = _producing_decision(rt, "seed-decider")
        order = {"coin": "BTC", "side": "buy", "size": "0.001"}
        assert rt._venue_write(lost, "venue.place_market", order,
                               slot="output")["status"] == "uncertain"
        other = _producing_decision(rt, "seed-decider")
        assert rt._venue_write(other, "venue.place_market", order,
                               slot="output")["status"] == "filled"
        closer = _producing_decision(rt, "seed-decider")
        closed = rt._venue_write(closer, "venue.close", {"coin": "BTC", "size": None},
                                 slot="tool0")
        assert closed["status"] == "filled"
        limit = _producing_decision(rt, "seed-decider")
        rest = rt._venue_write(limit, "venue.place_limit",
                               {**order, "price": "1000"}, slot="output")
        assert rest["status"] == "resting"
        canceller = _producing_decision(rt, "seed-decider")
        cancelled = rt._venue_write(canceller, "venue.cancel",
                                    {"coin": "BTC", "order_id": rest["order_id"]},
                                    slot="tool0")
        assert cancelled["status"] == "cancelled"
        assert not any("still uncertain" in i["reason"]
                       for i in ledger_items(rt, "order.refused"))

    def test_an_exhausted_intent_is_censored_and_blocks_nothing(self, monkeypatch):
        from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS
        from tests.audit.test_r3_b_authority import _producing_decision

        rt = _lost_ack_runtime(monkeypatch)
        lost = _producing_decision(rt)
        order = {"coin": "BTC", "side": "buy", "size": "0.001"}
        rt._venue_write(lost, "venue.place_market", order, slot="output")
        for _ in range(UNCERTAIN_ORDER_POLLS + 2):
            rt._reconcile_orders()
        assert rt.order_intents[lost]["unresolved"]
        again = _producing_decision(rt)
        assert rt._venue_write(again, "venue.place_market", order,
                               slot="output")["status"] == "filled"


# ------------------------------------------------------------------------------ 2


class TestFix2WindDownRetries:
    """A kill retries what the venue refused or half-did, and never repeats what it did."""

    def test_a_rejected_close_is_retried_by_the_next_kill_under_a_new_identity(self):
        from factorylab.runtime.venue import wind_down
        from factorylab.runtime.winddown import FLAT, operation_id
        from factorylab.world.exchange import OrderResult, Position
        from tests.audit.test_r3c_death import NONCE, Diary, Venue

        venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))],
                      close_result=OrderResult(None, "rejected", Decimal(0), None, "busy"))
        diary = Diary()
        wind_down(venue, diary, launch_nonce=NONCE)
        first = [call[3] for call in venue.sent("close")]
        assert first[0] == operation_id(NONCE, "BTC", "perp", "sell", 0)

        venue.close_result = OrderResult("o2", "filled", Decimal("1"), Decimal("100"))
        before = len(venue.sent("close"))
        wind_down(venue, diary, launch_nonce=NONCE)
        retried = [call[3] for call in venue.sent("close")][before:]
        assert len(retried) == 1 and retried[0] not in first
        venue.positions = []
        # A third kill after the venue is flat sends nothing and reads flat.
        before = len(venue.sent("close"))
        assert wind_down(venue, diary, launch_nonce=NONCE)["exposure_state"] == FLAT
        assert len(venue.sent("close")) == before

    def test_a_partial_ioc_close_is_retried_in_the_same_kill(self):
        from factorylab.runtime.venue import wind_down
        from factorylab.world.exchange import OrderResult, Position
        from tests.audit.test_r3c_death import NONCE, Diary, Venue

        venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))],
                      close_result=OrderResult("o1", "filled", Decimal("0.4"), Decimal("100")))
        wind_down(venue, Diary(), launch_nonce=NONCE)
        ids = [call[3] for call in venue.sent("close")]
        assert len(ids) >= 2 and len(set(ids)) == len(ids)

    def test_a_balance_below_the_venue_minimum_is_reported_dust_not_pending(self):
        from factorylab.runtime.venue import wind_down
        from factorylab.runtime.winddown import DUST
        from factorylab.world.exchange import SpotBalance
        from tests.audit.test_r3c_death import NONCE, Diary, Venue

        venue = Venue(balances=[SpotBalance("PURR", Decimal("1"), Decimal("1"))],
                      mids={"PURR/USDC": Decimal("5")})  # $5: above $1 dust, below $10
        venue.instruments = lambda: {"spot": [{"coin": "PURR/USDC",
                                               "min_order_value_usd": "10"}], "perp": []}
        report = wind_down(venue, Diary(), launch_nonce=NONCE)
        assert report["exposure_state"] == DUST
        assert not venue.sent("close")
        (row,) = report["residual"]["dust"]
        assert row["coin"] == "PURR/USDC" and row["reason"] == "below_venue_minimum"


# ------------------------------------------------------------------------------ 3


def _client_error(status: int):
    from hyperliquid.utils.error import ClientError

    return ClientError(status, None, "rate limited" if status == 429 else "bad", {})


class TestFix3ClientErrorsAreVenueWeather:
    """A 4xx from the SDK is a typed venue failure, never an exception that kills a tick."""

    def test_a_429_is_retried_with_backoff_then_unavailable(self, monkeypatch):
        from factorylab.world.exchange import VenueUnavailable

        sleeps = []
        monkeypatch.setattr("time.sleep", sleeps.append)
        ex = _live_stub()
        ex._info.user_state.side_effect = _client_error(429)
        try:
            ex.account()
        except VenueUnavailable as exc:
            assert "429" in str(exc) or "ClientError" in str(exc)
        else:
            raise AssertionError("a rate-limited read must be VenueUnavailable")
        assert ex._info.user_state.call_count > 1 and sleeps  # it backed off and asked again

    def test_other_4xx_are_unavailable_without_hammering(self, monkeypatch):
        from factorylab.world.exchange import VenueUnavailable

        monkeypatch.setattr("time.sleep", lambda s: None)
        ex = _live_stub()
        ex._info.user_state.side_effect = _client_error(400)
        try:
            ex.account()
        except VenueUnavailable:
            pass
        assert ex._info.user_state.call_count == 1

    def test_every_public_read_maps_a_client_error(self, monkeypatch):
        from factorylab.world.exchange import VenueUnavailable

        monkeypatch.setattr("time.sleep", lambda s: None)
        ex = _live_stub()
        for name in ("candles_snapshot", "l2_snapshot", "funding_history", "open_orders"):
            getattr(ex._info, name).side_effect = _client_error(429)
        for call in (lambda: ex.candles("BTC", "1m", 2), lambda: ex.order_book("BTC", 1),
                     lambda: ex.funding_history("BTC", 2), ex.open_orders,
                     lambda: ex.collateral_view("BTC")):
            try:
                call()
            except VenueUnavailable:
                continue
            raise AssertionError("expected VenueUnavailable")

    def test_a_rate_limited_collateral_read_refuses_the_order_and_the_tick_survives(
            self, monkeypatch):
        rt = venue_runtime(venue_usd="1000")

        def limited(*a, **kw):
            raise _client_error(429)

        monkeypatch.setattr("time.sleep", lambda s: None)
        live = _live_stub()
        live._info.open_orders.side_effect = limited
        monkeypatch.setattr(rt.exchange.target, "collateral_view", live.collateral_view)
        reason = rt._order_collateral("h", "BTC", Decimal("0.001"), True)
        assert reason == "order collateral unavailable: VenueUnavailable"


# ------------------------------------------------------------------------------ 4


class TestFix4StaleIsNeverLive:
    """A snapshot the venue did not just give is marked stale and never read as live."""

    def test_a_failed_mids_read_is_unavailable_not_the_last_prices(self, monkeypatch):
        from factorylab.world.exchange import VenueUnavailable

        monkeypatch.setattr("time.sleep", lambda s: None)
        ex = _live_stub()
        assert ex.mids()["BTC"] == Decimal(60000)
        ex._info.all_mids.side_effect = OSError("down")
        try:
            ex.mids()
        except VenueUnavailable:
            return
        raise AssertionError("an old price was served as a live one")

    def test_an_account_fallback_is_marked_stale_with_its_observation_time(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        ex = _live_stub()
        fresh = ex.account()
        assert fresh.stale is False and fresh.observed_at_ns is not None
        ex._info.user_state.side_effect = OSError("down")
        stale = ex.account()
        assert stale.stale is True and stale.observed_at_ns == fresh.observed_at_ns
        assert stale.positions == fresh.positions

    def test_a_stale_account_refuses_new_risk_even_with_this_tick_s_stamp(self, monkeypatch):
        """A read that succeeded, then an endpoint failure in the same tick.

        The fallback keeps the successful read's observation time, so an age check
        alone sees a fresh account. The snapshot is still one the venue declined to
        refresh, and new exposure cannot be authorised on it.
        """
        monkeypatch.setattr("time.sleep", lambda s: None)
        ex = _live_stub()
        fresh = ex.collateral_view("BTC")
        assert fresh["stale"] is False
        ex._info.user_state.side_effect = OSError("down")
        view = ex.collateral_view("BTC")
        assert view["stale"] is True
        assert view["observed_at_ns"] == fresh["observed_at_ns"]

        rt = venue_runtime(venue_usd="1000")
        rt.exchange.deterministic = False
        rt.exchange.target.collateral_view = lambda coin, market="perp": {
            **view, "observed_at_ns": rt.clock.now_ns}
        try:
            assert rt._order_collateral("h", "BTC", Decimal("0.001"), True) == (
                "order collateral is stale: the venue did not refresh the account")
            # It is still never a reason to block a reduction.
            assert rt._order_collateral("h", "BTC", Decimal("0.001"), False,
                                        reduce_only=True) is None
        finally:
            del rt.exchange.target.collateral_view

    def test_wind_down_never_reads_flat_from_a_stale_account(self):
        from dataclasses import replace as dc_replace

        from factorylab.runtime.venue import wind_down
        from factorylab.runtime.winddown import UNKNOWN
        from tests.audit.test_r3c_death import NONCE, Diary, Venue

        venue = Venue()
        live_account = venue.account
        venue.account = lambda: dc_replace(live_account(), stale=True)
        report = wind_down(venue, Diary(), launch_nonce=NONCE)
        assert report["exposure_state"] == UNKNOWN

    def test_a_watcher_never_settles_on_stale_equity_and_prompts_say_unavailable(self):
        from dataclasses import replace as dc_replace

        rt = venue_runtime(venue_usd="1000")
        live_account = rt.exchange.target.account
        rt.exchange.target.account = lambda: dc_replace(live_account(), stale=True)
        try:
            assert "equity_usd" not in rt._observed_world()
            rt.ticks_consumed += 1
            account, reason, _ = rt._tick_account_observation()
            assert account is None and reason == "StaleAccount"
            assert rt._equity_micro() is None
        finally:
            del rt.exchange.target.account


# ------------------------------------------------------------------------------ 5


class TestFix5UnpricedSpotToken:
    def test_one_unpriceable_spot_token_is_flagged_and_excluded_not_fatal(self):
        ex = _live_stub()
        ex.spot_pairs = ("PURR/USDC",)
        ex._configure_spot({"tokens": [{"index": 0, "name": "USDC", "szDecimals": 8},
                                       {"index": 1, "name": "PURR", "szDecimals": 0},
                                       {"index": 2, "name": "JUNK", "szDecimals": 0}],
                            "universe": [{"index": 1, "name": "PURR/USDC", "tokens": [1, 0]}]})
        ex._info.all_mids.return_value = {"BTC": "60000", "PURR/USDC": "4"}
        ex._info.spot_user_state.return_value = {"balances": [
            {"coin": "USDC", "total": "10", "hold": "0"},
            {"coin": "PURR", "total": "5", "hold": "0"},
            {"coin": "JUNK", "total": "1000", "hold": "0"}]}
        account = ex.account()
        assert account.equity_usd == Decimal(100) + Decimal(10) + Decimal(20)
        assert account.unpriced == ("JUNK",)
        assert any(b.coin == "JUNK" for b in account.spot_balances)
        assert account.stale is False


# ------------------------------------------------------------------------------ 6


ZERO_HASH = "0x" + "0" * 64


def _funding_row(ms, coin, usdc, szi="1", hash_=ZERO_HASH):
    return {"time": ms, "hash": hash_,
            "delta": {"type": "funding", "coin": coin, "usdc": usdc, "szi": szi,
                      "fundingRate": "0.0001", "nSamples": None}}


class TestFix6FundingIdentity:
    """Hyperliquid's userFunding rows carry an all-zero hash: it identifies nothing."""

    def test_zero_hash_payments_are_all_kept_and_distinct(self):
        from types import SimpleNamespace

        rows = [_funding_row(3_600_000, "ETH", "-0.25"),
                _funding_row(7_200_000, "ETH", "-0.30"),
                _funding_row(7_200_000, "BTC", "-0.10"),
                _funding_row(10_800_000, "ETH", "-0.25")]
        ex = HyperliquidExchange.__new__(HyperliquidExchange)
        ex._address = "0x" + "1" * 40
        ex._info = SimpleNamespace(user_funding_history=lambda user, start: rows)
        ex._guarded = lambda name, fn: fn()
        payments = ex.funding_payments(0)
        assert len(payments) == 4 and len({p.id for p in payments}) == 4
        assert sum(p.paid_usd for p in payments) == Decimal("0.90")
        # An empty hash is not a reason to drop a real payment either.
        rows.append(_funding_row(14_400_000, "ETH", "-0.05", hash_=""))
        assert len(ex.funding_payments(0)) == 5

    def test_the_live_cursor_books_each_zero_hash_payment_once(self):
        from types import SimpleNamespace

        from factorylab.runtime.live import LiveVenue

        rows = [_funding_row(3_600_000, "ETH", "-0.25")]
        ex = HyperliquidExchange.__new__(HyperliquidExchange)
        ex._address = "0x" + "1" * 40
        ex.name = "hyperliquid-testnet"
        ex._info = SimpleNamespace(user_funding_history=lambda user, start: [
            r for r in rows if r["time"] >= start])
        ex._guarded = lambda name, fn: fn()
        venue = LiveVenue(ex, ledger=SimpleNamespace(append=lambda item: None))
        assert len(venue.funding_payments(0)) == 1  # opens the cursor at 0 and books it
        rows.append(_funding_row(7_200_000, "ETH", "-0.25"))
        booked = venue.funding_payments(2)
        assert len(booked) == 1 and booked[0].payload["paid_usd"] == "0.25"
        assert venue.funding_payments(3) == []


# ------------------------------------------------------------------------------ 7


class TestFix7LimitPriceRounding:
    """≤5 significant figures (integers always allowed) and ≤ 6/8 − szDecimals decimals,
    rounded toward the passive side so rounding never makes an order more aggressive."""

    def _sent_price(self, coin, is_buy, px, *, sz_decimals, market="perp"):
        from factorylab.world.exchange import Order, OrderKind

        ex = _live_stub()
        ex.coins = (coin,) if market == "perp" else ()
        if market == "spot":
            ex.spot_pairs = (coin,)
            ex._spot_names = {coin: "@1"}
        ex._sz_decimals = {coin: sz_decimals}
        ex._exchange.order.return_value = {"status": "ok", "response": {"data": {
            "statuses": [{"resting": {"oid": 1}}]}}}
        ex.place(Order(coin, is_buy, Decimal("1"), OrderKind.LIMIT, Decimal(px),
                       client_id="c", market=market))
        return Decimal(str(ex._exchange.order.call_args.args[3]))

    def test_significant_figures_toward_the_passive_side(self):
        assert self._sent_price("BTC", True, "60123.456", sz_decimals=5) == Decimal("60123")
        assert self._sent_price("BTC", False, "60123.456", sz_decimals=5) == Decimal("60124")
        assert self._sent_price("ETH", True, "2500.123", sz_decimals=4) == Decimal("2500.1")
        assert self._sent_price("ETH", False, "2500.123", sz_decimals=4) == Decimal("2500.2")

    def test_integer_prices_are_always_allowed(self):
        assert self._sent_price("BTC", True, "123456.7", sz_decimals=5) == Decimal("123456")

    def test_decimal_cap_depends_on_market_and_size_decimals(self):
        assert self._sent_price("kPEPE", True, "0.0123456", sz_decimals=0) == Decimal("0.012345")
        assert self._sent_price("kPEPE", False, "0.0123456", sz_decimals=0) == Decimal("0.012346")
        # A perp with szDecimals 3 allows 3 decimals: 1.23456 -> 1.234 (buy), 1.235 (sell).
        assert self._sent_price("SOL", True, "1.23456", sz_decimals=3) == Decimal("1.234")
        assert self._sent_price("SOL", False, "1.23456", sz_decimals=3) == Decimal("1.235")
        # Spot allows 8 − szDecimals.
        assert self._sent_price("PURR/USDC", True, "0.000123456", sz_decimals=0,
                                market="spot") == Decimal("0.00012345")


# ------------------------------------------------------------------------------ 8


class TestFix8X402AfterSignature:
    """Once the signed authorization has left, the seller can settle it: never release."""

    def _metered(self, paid_response, *, record_fails_on=None):
        from factorylab.kernel.ledger import Ledger
        from factorylab.kernel.wallet import Wallet
        from factorylab.world.market import X402MeteredModel
        from factorylab.world.metering import Meter
        from factorylab.world.models import PriceTable, TokenPrice
        from tests.world.test_market import MODEL, SellerHTTP, provider

        fake = SellerHTTP()
        fake.paid_response = paid_response
        wallet = Wallet(10_000, Ledger())
        events = []

        def record(item):
            if item["kind"] == record_fails_on:
                raise OSError("diary refused")
            events.append(item)

        model = X402MeteredModel(provider(fake), PriceTable({MODEL: TokenPrice(0, 0, 2000)}),
                                 Meter(wallet), record=record, on_unaffordable=lambda h: None)
        return model, wallet, fake, events

    def _run(self, model):
        from factorylab.world.metering import BillingUncertain
        from factorylab.world.models import ModelRequest
        from tests.world.test_market import MODEL

        try:
            model.complete(ModelRequest(MODEL, "", ()), handle="decision-1")
        except BillingUncertain:
            return "uncertain"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
        return "ok"

    def test_a_402_after_payment_books_the_ceiling_as_uncertain(self):
        from factorylab.world.x402 import HTTPResponse

        model, wallet, fake, events = self._metered(HTTPResponse(402))
        assert self._run(model) == "uncertain"
        assert len(fake.payments) == 1
        assert wallet.balance == 10_000 - 1734  # the authorization is held, not released
        assert events[-1]["kind"] == "x402.unresolved"

    def test_a_failed_settlement_receipt_books_the_ceiling_as_uncertain(self):
        from factorylab.world.x402 import HTTPResponse
        from tests.world.test_market import encoded

        model, wallet, _, _ = self._metered(
            HTTPResponse(200, {}, {"PAYMENT-RESPONSE": encoded({"success": False})}))
        assert self._run(model) == "uncertain"
        assert wallet.balance == 10_000 - 1734

    def test_any_failure_after_the_send_is_uncertain_not_released(self):
        model, wallet, _, _ = self._metered(None, record_fails_on="x402.result")
        assert self._run(model) == "uncertain"
        assert wallet.balance == 10_000 - 1734


# ------------------------------------------------------------------------------ 9


class TestFix9SellerIsHttpsAndPublic:
    """A seller registered without a vote cannot point the kernel at a private host."""

    def test_plaintext_and_private_seller_urls_are_refused(self):
        import pytest

        from factorylab.world.market import split_model_id
        from factorylab.world.x402 import X402Error

        for url in ("http://seller.test", "https://localhost", "https://127.0.0.1",
                    "https://10.0.0.5", "https://[::1]", "https://169.254.169.254",
                    "https://metadata.internal", "https://printer.local",
                    "https://seller.test:8443"):
            with pytest.raises(X402Error):
                split_model_id(f"x402:{url}#model")
        assert split_model_id("x402:https://seller.test/v1#model")[0] == "https://seller.test"

    def test_a_public_name_that_resolves_privately_is_refused_before_any_request(self):
        import pytest

        from factorylab.world.market import X402Provider
        from factorylab.world.x402 import X402Error

        requests = []
        provider = X402Provider(transport=lambda *a: requests.append(a),
                                resolver=lambda host: ["10.0.0.5"])
        with pytest.raises(X402Error, match="public"):
            provider.registration_price("x402:https://rebind.example#model")
        assert requests == []
        # A public answer passes the check (and then reaches the seller's catalogue).
        ok = X402Provider(transport=lambda *a: (_ for _ in ()).throw(RuntimeError("io")),
                          resolver=lambda host: ["93.184.216.34"])
        with pytest.raises(X402Error, match="transport"):
            ok.registration_price("x402:https://seller.example#model")


# ------------------------------------------------------------------------------ 10


RESERVE = "0x" + "ab" * 20
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


def _canonical(tx, log_index=5, micro=2500):
    return {"confirmed": True, "evidence": {"chain": 8453, "tx": tx, "log_index": log_index,
                                            "token": USDC_BASE, "recipient": RESERVE,
                                            "micro": micro}}


class TestFix10IncomeBooksOnce:
    """One on-chain transfer is income once, whichever path reports it and how."""

    def _runtime(self, monkeypatch, outcome=None):
        from tests.runtime.test_fidelity import runtime as scripted_runtime

        rt = scripted_runtime()
        if outcome is not None:
            monkeypatch.setattr(rt.treasury.rail.target, "verify_receipt", outcome)
        return rt

    def test_a_direct_booking_and_a_later_verified_claim_of_it_book_once(self, monkeypatch):
        rt = self._runtime(monkeypatch, lambda receipt: _canonical(receipt["tx"]))
        treasury = rt.treasury
        assert treasury.earn("oracle", 2500, "0xFEED", payer="0xb", chain="base",
                             asset="USDC", recipient=RESERVE) is not None
        # The host's spool reports the same payment without a log index or recipient.
        treasury.earn("oracle", 2500, "0xfeed", claim=True, payer="0xb")
        assert treasury.verify_receipts() == []
        assert treasury.income["earned_micro"] == 2500
        assert treasury.pots()["claimed_micro"] == 0

    def test_two_spellings_of_one_claim_verify_to_one_booking(self, monkeypatch):
        rt = self._runtime(monkeypatch, lambda receipt: _canonical(receipt["tx"]))
        treasury = rt.treasury
        treasury.earn("oracle", 2500, "0xabc", claim=True, payer="0xb")
        treasury.earn("oracle", 2500, "0xabc", claim=True, payer="0xb", log_index=5,
                      recipient=RESERVE)
        assert len(treasury.verify_receipts()) == 1
        assert treasury.income["earned_micro"] == 2500

    def test_a_claim_verified_inside_the_treasury_tick_still_credits_once(self, monkeypatch):
        rt = self._runtime(monkeypatch, lambda receipt: _canonical(receipt["tx"]))
        treasury = rt.treasury
        treasury.earn("oracle", 2500, "0xabc", claim=True, payer="0xb")
        before = rt.wallet.balance
        treasury.tick(1)  # verifies the claim; the runtime must still credit it
        rt._collect_income()
        rt._collect_income()
        assert rt.wallet.balance == before + 2500
        assert len(ledger_items(rt, "income.custody")) == 1

    def test_the_live_rail_leaves_an_ambiguous_transfer_unresolved(self):
        from types import SimpleNamespace

        from factorylab.world.evm import event_topic, word_address
        from factorylab.world.treasury_rails import LiveRail

        topic = event_topic("Transfer(address,address,uint256)")
        to_word = "0x" + word_address(RESERVE).hex()

        def log(index, value):
            return {"address": USDC_BASE, "topics": [topic, "0x" + "00" * 32, to_word],
                    "data": hex(value), "logIndex": hex(index)}

        rail = LiveRail.__new__(LiveRail)
        rail.reserve_address = RESERVE
        receipt = {"status": "0x1", "logs": [log(2, 2500), log(7, 2500)]}
        rail.base = SimpleNamespace(chain=SimpleNamespace(usdc=USDC_BASE, id=8453),
                                    proof=lambda tx: receipt)
        claim = {"tx": "0xabc", "micro": 2500}
        assert rail.verify_receipt(claim) is None  # two equal transfers: which one?
        confirmed = rail.verify_receipt({**claim, "log_index": 7})
        assert confirmed["confirmed"] and confirmed["evidence"]["log_index"] == 7
        receipt["logs"] = [log(3, 2500)]
        only = rail.verify_receipt(claim)
        assert only["evidence"]["log_index"] == 3 and only["evidence"]["token"] == USDC_BASE

    def test_the_hosted_seller_spools_the_recipient(self, tmp_path):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "serve", Path(__file__).resolve().parents[2] / "deploy" / "serve.py")
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        spool = tmp_path / "income.jsonl"
        seller = serve.build_seller({}, RESERVE, "https://facilitator.example", spool)
        seller.earn(SimpleService(), 2500, "0xabc", "0xb", 1)
        row = json.loads(spool.read_text().splitlines()[-1])
        assert row["recipient"] == RESERVE


class SimpleService:
    id = "oracle"
    program_id = "prog"
    version = 1


# ------------------------------------------------------------------------------ 11


class TestFix11NoSalesAfterDeath:
    """A dead world sells nothing: no quote, no settlement, no program run."""

    def test_the_hosted_seller_fails_closed_on_death_and_on_stale_liveness(self):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "serve", Path(__file__).resolve().parents[2] / "deploy" / "serve.py")
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        clock = [100.0]
        liveness = serve.Liveness(max_age_s=60, clock=lambda: clock[0])
        liveness.observe([{"kind": "event", "event": {"kind": "Launch"}}])
        assert liveness()
        clock[0] += 61  # the ledger has not been re-read for longer than the bound
        assert not liveness()
        liveness.observe([{"kind": "kill.production", "production_state": "killed"}])
        assert not liveness()
        liveness.observe([{"kind": "event", "event": {"kind": "Launch"}}])
        assert not liveness()  # death is final: a later read cannot revive it


# ------------------------------------------------------------------------------ 12


class _NeverRail:
    """A rail whose one step is submitted and then never evidenced."""

    def __init__(self, wallet, *, expires_at_ns=None):
        from factorylab.world.treasury import FakeRail

        self.inner = FakeRail(wallet)
        self.expires_at_ns = expires_at_ns
        self.sent = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def send(self, step, reference):
        self.sent.append(reference)

    def poll(self, step, state):
        return None

    def expired(self, step, state, now_ns):
        if self.expires_at_ns is not None and now_ns >= self.expires_at_ns:
            return "authorization expired unused"
        return None


class TestFix12NoTransferBlocksForever:
    def _treasury(self, rail_factory):
        from factorylab.kernel.ledger import Ledger
        from factorylab.kernel.wallet import Wallet
        from factorylab.world.treasury import Treasury

        ledger = Ledger(clock_ns=lambda: 0)
        wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
        rail = rail_factory(wallet)
        return Treasury(ledger, wallet, rail, fee_ceiling_micro=10_000), wallet, rail

    def test_an_expired_unused_authorization_is_abandoned_and_frees_the_slot(self):
        hour = 3_600 * 10**9
        treasury, wallet, rail = self._treasury(
            lambda w: _NeverRail(w, expires_at_ns=5 * hour))
        assert treasury.transfer("to_reserve", "10", handle="a", now_ns=1)["status"] == (
            "submitted")
        assert treasury.tick(hour) == []
        assert treasury.transfer("to_reserve", "10", handle="b", now_ns=hour)["status"] == (
            "refused")
        treasury.tick(6 * hour)
        assert treasury.state["status"] == "failed"
        assert wallet.available == wallet.balance == 100_000_000  # principal released
        assert treasury.transfer("to_reserve", "10", handle="c",
                                 now_ns=6 * hour)["status"] == "submitted"

    def test_the_live_rail_knows_when_a_top_up_or_a_withdrawal_can_no_longer_execute(self):
        from factorylab.world.treasury_rails import LiveRail

        rail = LiveRail.__new__(LiveRail)
        top_up = {"reference": {"authorization": {"validBefore": 1_000}}, "nonce": 0}
        assert rail.expired("venice_top_up", top_up, 1_000 * 10**9) is None
        # A top-up is judged on finalized Base, never on the runtime clock: however far
        # past validBefore the clock runs, an unread chain proves nothing expired. The
        # chain proof itself is tests/world/test_venice_hybrid.py's reviewer probe.
        assert rail.expired("venice_top_up", top_up, (1_000 + 7_200) * 10**9) is None
        day_ms = 86_400_000
        withdraw = {"reference": {"nonce": 10 * day_ms}, "nonce": 10 * day_ms}
        assert rail.expired("withdraw_burn", withdraw, 11 * day_ms * 10**6) is None
        assert rail.expired("withdraw_burn", withdraw, 14 * day_ms * 10**6)
        assert rail.expired("mint_base", withdraw, 99 * day_ms * 10**6) is None

    def test_prepared_evm_transactions_carry_gas_price_headroom(self):
        from tests.world.test_evm import setup as evm_setup

        _, _, ref = evm_setup()
        assert ref["tx"]["gasPrice"] >= 125  # the node quoted 100

    def test_a_stuck_evm_transaction_is_replaced_at_the_same_nonce_with_a_bump(self):
        from tests.world.test_evm import receipt as evm_receipt
        from tests.world.test_evm import setup as evm_setup

        rpc, chain, ref = evm_setup()
        bumped = chain.replace(ref, gas_remaining_wei=10**15)
        assert bumped["tx"]["nonce"] == ref["tx"]["nonce"]
        assert bumped["tx"]["gasPrice"] * 8 >= ref["tx"]["gasPrice"] * 9  # at least +12.5%
        assert bumped["tx_hash"] != ref["tx_hash"] and ref["tx_hash"] in bumped["replaces"]
        # Whichever of the two the chain mined is found; an unmined hash has no receipt.
        mined = evm_receipt(ref)
        answer = rpc.__call__

        def realistic(method, url, body, headers):
            if (body["method"] == "eth_getTransactionReceipt"
                    and body["params"][0] != mined["transactionHash"]):
                from factorylab.world.x402 import HTTPResponse

                return HTTPResponse(200, {"result": None}, {})
            return answer(method, url, body, headers)

        rpc.receipt = mined
        chain.transport = realistic
        assert chain.receipt(bumped)["transactionHash"] == ref["tx_hash"]

    def test_the_treasury_replaces_a_step_the_chain_will_not_mine(self):
        minute = 60 * 10**9

        class Replacing(_NeverRail):
            def prepare(self, step, state, gas_spent):
                return {"network": "scripted", "tx_hash": "0x1", "tx": {"nonce": 7},
                        "chain_key": "base", "fee_ceiling_micro": 0}

            def replace(self, step, reference, gas_spent):
                return {**reference, "tx_hash": reference["tx_hash"] + "1",
                        "replaces": [reference["tx_hash"], *reference.get("replaces", [])]}

        treasury, _, rail = self._treasury(lambda w: Replacing(w))
        treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
        for k in range(1, 8):
            treasury.tick(k * 2 * minute)
        hashes = {r["tx_hash"] for r in rail.sent}
        assert len(hashes) > 1  # it did not resend the one stuck transaction forever
        assert all(r["tx"]["nonce"] == 7 for r in rail.sent)


# ------------------------------------------------------------------------------ 13


class TestFix13DeathIsSettledReality:
    """A provisional ceiling is not a cost: it cannot kill the wallet on its own."""

    def _wallet(self, balance=1_000, floor=0):
        from factorylab.kernel.ledger import Ledger
        from factorylab.kernel.wallet import Wallet

        return Wallet(balance, Ledger(clock_ns=lambda: 0), clock_ns=lambda: 0,
                      balance_floor_micro=floor)

    def test_an_uncertain_bill_at_the_whole_balance_does_not_kill_and_settling_revives(self):
        wallet = self._wallet()
        hold = wallet.reserve(1_000, "h", "model:x")
        wallet.commit_uncertain(hold)
        assert not wallet.dead  # its true cost may be anything from 0 to 1,000
        wallet.settle_uncertain(hold.id, 10)
        assert not wallet.dead and wallet.balance == 990
        assert wallet.check_conservation()

    def test_a_settled_cost_that_reaches_the_floor_is_death(self):
        wallet = self._wallet()
        hold = wallet.reserve(1_000, "h", "model:x")
        wallet.commit_uncertain(hold)
        wallet.settle_uncertain(hold.id, 1_000)
        assert wallet.dead

    def test_death_is_still_final_once_settled_reality_reaches_the_floor(self):
        wallet = self._wallet()
        hold = wallet.reserve(400, "h", "model:x")
        wallet.commit_uncertain(hold)
        wallet.settle(-600, "fill:1", "exchange_pnl")  # 1000 - 400 - 600 = 0, best case 400
        assert not wallet.dead
        wallet.settle_uncertain(hold.id, 400)
        assert wallet.dead
        # The floor was reached on settled money: a later gain does not revive it.
        import pytest

        from factorylab.kernel.wallet import Infeasible

        with pytest.raises(Infeasible):
            wallet.settle(500, "fill:2", "exchange_pnl")

    def test_a_checkpoint_in_limbo_restores_alive(self):
        wallet = self._wallet()
        hold = wallet.reserve(1_000, "h", "model:x")
        wallet.commit_uncertain(hold)
        state = wallet.state()
        restored = self._wallet()
        restored._restore_state(state)
        assert not restored.dead
        restored.settle_uncertain(hold.id, 1)
        assert restored.balance == 999 and not restored.dead


# ------------------------------------------------------------------------------ 14


class TestFix14Jail:
    def test_the_macos_profile_grants_only_named_sysctls(self, monkeypatch, tmp_path):
        import sys

        from factorylab.cortex import sandbox

        monkeypatch.setattr(sys, "platform", "darwin")
        command = sandbox._command("/usr/bin/sandbox-exec", tmp_path, tmp_path,
                                   tmp_path / "python", 3)
        profile = command[2]
        assert "(allow sysctl-read)" not in profile  # no unfiltered grant
        assert "(sysctl-name" in profile and "kern.proc" not in profile

    def test_the_jailed_interpreter_starts_and_cannot_read_the_process_table(self):
        import sys

        import pytest

        from factorylab.cortex import sandbox

        if sys.platform != "darwin" or not sandbox.jail_available():
            pytest.skip("the macOS sandbox profile is exercised on macOS only")
        code = ("import ctypes, os, platform\n"
                "libc = ctypes.CDLL(None)\n"
                "size = ctypes.c_size_t(0)\n"
                "mib = (ctypes.c_int * 3)(1, 14, 0)  # CTL_KERN, KERN_PROC, KERN_PROC_ALL\n"
                "print(libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0),"
                " os.cpu_count() > 0, platform.machine() != '')\n")
        result = sandbox.run_python(code, timeout_s=5)
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["-1", "True", "True"]

    def test_linux_tmp_is_a_bounded_tmpfs(self, monkeypatch, tmp_path):
        import sys

        from factorylab.cortex import sandbox

        monkeypatch.setattr(sys, "platform", "linux")
        command = sandbox._command("/usr/bin/bwrap", tmp_path, tmp_path, tmp_path / "python", 3)
        at = command.index("--tmpfs")
        assert command[at + 1] == "/tmp" and command[at - 2] == "--size"
        assert 0 < int(command[at - 1]) <= 64 * 1024 * 1024

    def test_the_world_unit_bounds_memory_and_tasks(self):
        from pathlib import Path

        unit = (Path(__file__).resolve().parents[2] / "deploy" / "factorylab.service")
        text = unit.read_text()
        assert "\nMemoryMax=" in text and "\nTasksMax=" in text

    def test_a_key_file_wins_and_an_exported_conflicting_key_is_refused_under_a_jail(
            self, tmp_path, monkeypatch, capsys):
        import os

        import pytest

        from factorylab.cortex import sandbox
        from factorylab.runtime import cli

        monkeypatch.chdir(tmp_path)
        for var in ("OPENROUTER_API_KEY", "HL_PRIVATE_KEY", "RESERVE_PRIVATE_KEY"):
            monkeypatch.delenv(var, raising=False)
        key = tmp_path / "openrouter.key"
        key.write_text("sk-from-file\n")
        key.chmod(0o600)
        monkeypatch.setattr(sandbox, "jail_installed", lambda: False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-exported")
        cli._load_dotenv()
        assert os.environ["OPENROUTER_API_KEY"] == "sk-from-file"  # the file wins
        assert "sk-" not in capsys.readouterr().err  # a warning never prints a value
        monkeypatch.setattr(sandbox, "jail_installed", lambda: True)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-exported")
        with pytest.raises(cli.KeyFileModeError, match="exported"):
            cli._load_dotenv()

    def test_a_symlinked_key_file_is_never_followed(self, tmp_path, monkeypatch):
        import os

        import pytest

        from factorylab.runtime import cli

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)
        target = tmp_path / "elsewhere"
        target.write_text("0xsecret\n")
        target.chmod(0o600)
        os.symlink(target, tmp_path / "reserve.key")
        with pytest.raises(cli.KeyFileModeError):
            cli._load_dotenv()
        assert "RESERVE_PRIVATE_KEY" not in os.environ

    def test_the_acceptance_cli_refuses_mainnet_even_under_python_O(self, monkeypatch):
        from types import SimpleNamespace

        import pytest

        from factorylab.runtime import treasury_cli
        from factorylab.world import exchange as exchange_module
        from factorylab.world import treasury_rails

        monkeypatch.setenv("RESERVE_PRIVATE_KEY", "0x" + "11" * 32)
        monkeypatch.setattr(exchange_module, "HyperliquidExchange", lambda **kw: object())
        mainnet = SimpleNamespace(testnet=False, hyper=SimpleNamespace(chain=SimpleNamespace(
            id=999)), base=SimpleNamespace(chain=SimpleNamespace(id=8453)), venue_address="0x")
        monkeypatch.setattr(treasury_rails, "LiveRail", lambda exchange, spec: mainnet)
        source = __import__("inspect").getsource(treasury_cli.command)
        assert "assert rail.testnet" not in source  # stripped by python -O
        with pytest.raises(treasury_rails.RailError, match="testnet"):
            treasury_cli.command(SimpleNamespace(treasury_command="probe", ledger="x", usd=None))
