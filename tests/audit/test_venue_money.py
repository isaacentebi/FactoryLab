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
