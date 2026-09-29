"""Hyperliquid breadth on the live adapter: every perp, spot and named HIP-3 dexes.

The venue is the world (AGENTS.md). Each test attempts to violate an invariant of the
adapter and asserts it fails: reads stay batched by dex whatever the number of markets
(Chapter II §IV.c), a HIP-3 market's terms and fee are the venue's own and published
(§I.b, §II.b), and the wallet moves only on the venue's fill (AGENTS.md). No network:
the SDK's ``Info`` is replaced by a recorded stand-in.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from unittest.mock import Mock

import pytest

from factorylab.world.exchange import HyperliquidExchange, Order, OrderKind

USDC, USDH = 0, 7


def _meta(dex: str, n: int, *, collateral: int = USDC) -> dict:
    prefix = f"{dex}:" if dex else ""
    names = ["BTC", "ETH"] if not dex else ["TSLA", "GOLD"]
    names = (names + [f"C{i}" for i in range(n)])[:max(n, 2)]
    universe = []
    for i, name in enumerate(names):
        row = {"name": prefix + name, "szDecimals": 3 if i == 0 else 2, "maxLeverage": 20}
        if dex:
            row.update({"deployerFeeScale": "1.0", "marginMode": "noCross",
                        "growthMode": "enabled" if name == "GOLD" else None})
        universe.append(row)
    universe.append({"name": prefix + "DEAD", "szDecimals": 1, "maxLeverage": 3,
                     "isDelisted": True})
    return {"universe": universe, "collateralToken": collateral}


class FakeInfo:
    """The SDK ``Info`` a test controls: every read is counted by its type and dex."""

    n = 2
    dexes: dict[str, int] = {"xyz": USDC}

    def __init__(self, base_url=None, skip_ws=True, timeout=None, perp_dexs=None, **kw):
        self.calls: Counter = Counter()
        self.perp_dexs_arg = perp_dexs
        self.name_to_coin: dict = {}
        self.fail: set[str] = set()
        self.states: dict[str, dict] = {}

    def _count(self, kind, dex=""):
        self.calls[(kind, dex)] += 1
        if (kind, dex) in self.fail or kind in self.fail:
            raise OSError(f"{kind} {dex} unanswered")

    def meta(self, dex=""):
        self._count("meta", dex)
        return _meta(dex, self.n, collateral=self.dexes.get(dex, USDC))

    def spot_meta(self):
        self._count("spotMeta")
        return {"tokens": [{"index": USDC, "name": "USDC", "szDecimals": 8},
                           {"index": 1, "name": "PURR", "szDecimals": 0},
                           {"index": USDH, "name": "USDH", "szDecimals": 2}],
                "universe": [{"name": "PURR/USDC", "tokens": [1, USDC], "index": 0},
                             {"name": "@9", "tokens": [1, USDH], "index": 9}]}

    def all_mids(self, dex=""):
        self._count("allMids", dex)
        if dex:
            return {f"{dex}:{row['name'].split(':', 1)[1]}": "100"
                    for row in _meta(dex, self.n)["universe"] if not row.get("isDelisted")}
        return {**{row["name"]: "100" for row in _meta("", self.n)["universe"]
                   if not row.get("isDelisted")}, "PURR/USDC": "0.2", "#10": "0.5"}

    def meta_and_asset_ctxs(self):
        self._count("metaAndAssetCtxs", "")
        meta = _meta("", self.n)
        return [meta, [{"funding": "0.00001", "premium": "0"}] * len(meta["universe"])]

    def post(self, path, body):
        self._count(body["type"], body.get("dex", ""))
        meta = _meta(body["dex"], self.n)
        return [meta, [{"funding": "0.00002", "premium": "0"}] * len(meta["universe"])]

    def user_state(self, address, dex=""):
        self._count("clearinghouseState", dex)
        return self.states.get(dex, {"marginSummary": {"accountValue": "0", "totalMarginUsed": "0",
                                                       "totalRawUsd": "0"},
                                     "assetPositions": [], "withdrawable": "0"})

    def spot_user_state(self, address):
        self._count("spotClearinghouseState")
        return {"balances": [{"coin": "USDC", "total": "10", "hold": "0"}]}

    def open_orders(self, address, dex=""):
        self._count("openOrders", dex)
        return [{"oid": 77, "coin": "xyz:TSLA", "side": "B", "sz": "1", "limitPx": "90"}] if (
            dex == "xyz") else []

    def funding_history(self, name, start, end=None):
        self._count("fundingHistory", name)
        return []

    def user_funding_history(self, user, start, end=None):
        self._count("userFunding")
        return []

    def l2_snapshot(self, name):
        self._count("l2Book", name)
        return {"time": 0, "levels": [[], []]}

    def user_fees(self, address):
        self._count("userFees")
        return {"userCrossRate": "0.00045", "userAddRate": "0.00015",
                "userSpotCrossRate": "0.0007", "userSpotAddRate": "0.0004",
                "activeReferralDiscount": "0.0"}


@pytest.fixture
def venue(monkeypatch):
    def build(n=2, dexes=("xyz",), collateral=None):
        FakeInfo.n = n
        FakeInfo.dexes = {dex: USDC for dex in dexes} | (collateral or {})
        monkeypatch.setattr("hyperliquid.info.Info", FakeInfo)
        monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
        ex = HyperliquidExchange(address="0xabc", coins=("BTC",), spot_pairs=("PURR/USDC",),
                                 dexes=tuple(dexes))
        ex._info.calls.clear()
        return ex
    return build


def test_the_adapter_is_built_with_the_named_dexes_so_the_sdk_resolves_their_names(venue):
    ex = venue()
    assert ex._info.perp_dexs_arg == ["", "xyz"]
    assert "xyz:TSLA" in ex._listed_coins and "BTC" in ex._listed_coins


def test_a_dex_margined_in_another_token_is_refused_at_construction(venue):
    with pytest.raises(ValueError, match="not USDC"):
        venue(collateral={"xyz": USDH})


@pytest.mark.parametrize("n", [2, 200, 2000])
def test_reads_are_batched_by_dex_whatever_the_number_of_markets(venue, n):
    ex = venue(n=n)
    mids = ex.mids()
    ex.funding()
    ex.account()
    # One allMids, one metaAndAssetCtxs and one clearinghouseState per perp dex; one
    # spot state and one spot mids read: never a read per market.
    assert ex._info.calls == Counter({
        ("allMids", ""): 2, ("allMids", "xyz"): 1, ("metaAndAssetCtxs", ""): 1,
        ("metaAndAssetCtxs", "xyz"): 1, ("clearinghouseState", ""): 1,
        ("clearinghouseState", "xyz"): 1, ("spotClearinghouseState", ""): 1})
    assert "xyz:TSLA" in mids and "BTC" in mids and "PURR/USDC" in mids
    assert "#10" not in mids  # an outcome coin is not a listed market here
    assert len(mids) == len([c for c in ex._listed_coins if not c.endswith("DEAD")]) + 1


def test_a_dex_that_did_not_answer_prices_none_of_its_markets_never_an_old_price(venue):
    ex = venue()
    ex.mids()
    ex._info.fail.add(("allMids", "xyz"))
    mids = ex.mids()
    assert "BTC" in mids and not any(c.startswith("xyz:") for c in mids)


def test_every_hip3_market_publishes_its_dex_margin_leverage_and_scaled_fee(venue):
    ex = venue()
    rows = {row["coin"]: row for row in ex.instruments()["perp"]}
    tsla, gold, btc = rows["xyz:TSLA"], rows["xyz:GOLD"], rows["BTC"]
    assert (tsla["dex"], tsla["margin"], tsla["max_leverage"]) == ("xyz", "isolated", 20)
    # deployerFeeScale 1.0 doubles both rates (2 x scale); growth mode then x 0.1.
    assert (tsla["taker_fee_rate"], tsla["maker_fee_rate"]) == ("0.0009", "0.0003")
    assert (gold["taker_fee_rate"], gold["maker_fee_rate"]) == ("0.00009", "0.00003")
    # A first-dex perp states the account's own rates, byte for byte.
    assert (btc["taker_fee_rate"], btc["maker_fee_rate"]) == ("0.00045", "0.00015")
    assert "dex" not in btc and btc["margin"] == "cross"
    assert rows["xyz:DEAD"]["delisted"] is True
    # Lot and tick follow the perp rule for a HIP-3 market (6 - szDecimals), not spot's.
    assert (tsla["lot_size"], tsla["tick_size"]) == ("0.001", "0.001")
    # A spot pair quoted in another token is not a market of this world.
    assert [row["coin"] for row in ex.instruments()["spot"]] == ["PURR/USDC"]


def test_leverage_above_the_published_limit_is_refused_before_it_is_signed(venue):
    ex = venue()
    ex._exchange = Mock()
    ex._exchange.update_leverage.return_value = {"status": "ok"}
    refused = ex.set_leverage("xyz:TSLA", 21)
    assert refused["status"] == "rejected" and "max_leverage 20" in refused["error"]
    ex._exchange.update_leverage.assert_not_called()
    assert ex.set_leverage("xyz:TSLA", 20)["status"] == "ok"
    # An isolated-only market takes an isolated setting; a cross market a cross one.
    ex._exchange.update_leverage.assert_called_with(20, "xyz:TSLA", is_cross=False)
    ex.set_leverage("BTC", 5)
    ex._exchange.update_leverage.assert_called_with(5, "BTC", is_cross=True)


def test_a_hip3_market_order_is_priced_at_perp_precision_not_the_sdks_spot_rounding(venue):
    ex = venue()
    ex.coins = ("BTC", "xyz:TSLA")
    ex._exchange = Mock()
    ex._exchange._slippage_price.side_effect = AssertionError("rounds a HIP-3 id as spot")
    ex._exchange.order.return_value = {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"totalSz": "1", "avgPx": "100", "oid": 5}}]}}}
    ex._info.all_mids = lambda dex="": {"xyz:TSLA": "123.4567"}
    result = ex.place(Order("xyz:TSLA", True, Decimal("1"), OrderKind.MARKET, client_id="c"))
    assert result.status == "filled"
    wire, is_buy, size, price = ex._exchange.order.call_args.args[:4]
    # szDecimals 3 -> at most 6 - 3 = 3 decimals and five significant figures.
    assert (wire, is_buy, size) == ("xyz:TSLA", True, 1.0)
    assert Decimal(str(price)) == Decimal("129.62")


def test_an_order_on_a_market_outside_the_universe_is_refused_unsent(venue):
    ex = venue()
    ex._exchange = Mock()
    result = ex.place(Order("xyz:GOLD", True, Decimal("1"), OrderKind.MARKET))
    assert result.status == "rejected" and result.error == "unregistered perp coin"
    ex._exchange.order.assert_not_called()


def test_a_cancel_naming_no_coin_finds_its_order_never_walks_the_universe(venue):
    ex = venue(n=500)
    ex.coins = tuple(ex._listed_coins)  # a universe of hundreds of markets
    ex._exchange = Mock()
    ex._exchange.cancel.return_value = {"status": "ok",
                                        "response": {"data": {"statuses": ["success"]}}}
    assert ex.cancel("77", client_id="k")["status"] == "cancelled"
    ex._exchange.cancel.assert_called_once_with("xyz:TSLA", 77)


def test_hip3_positions_and_collateral_are_read_from_their_own_clearinghouse(venue):
    ex = venue()
    ex._info.states["xyz"] = {
        "marginSummary": {"accountValue": "40", "totalMarginUsed": "5", "totalRawUsd": "30"},
        "assetPositions": [{"position": {"coin": "xyz:TSLA", "szi": "2", "entryPx": "100",
                                         "leverage": {"type": "isolated", "value": 4}}}]}
    ex._info.states[""] = {
        "marginSummary": {"accountValue": "60", "totalMarginUsed": "1", "totalRawUsd": "50"},
        "assetPositions": [], "withdrawable": "59"}
    account = ex.account()
    assert [p.coin for p in account.positions] == ["xyz:TSLA"]
    assert account.perps_equity_usd == Decimal(100)
    assert account.reconciliation_cash_usd == Decimal(80)
    view = ex.collateral_view("xyz:TSLA")
    # A HIP-3 perp is margined against its dex alone, never the first dex's equity.
    assert view["eligible_equity_usd"] == Decimal(40)
    assert view["margin_used_usd"] == Decimal(5)
    assert view["account_mode"] == "isolated"
    assert view["leverage_for_instrument"] == Decimal(4)
    assert ex.collateral_view("BTC")["eligible_equity_usd"] == Decimal(60)


def test_half_an_account_is_not_an_account_a_silent_dex_falls_back_whole(venue):
    ex = venue()
    first = ex.account()
    ex._info.fail.add(("clearinghouseState", "xyz"))
    again = ex.account()
    assert again.stale and again.observed_at_ns == first.observed_at_ns


@pytest.mark.parametrize("n", [2, 2000])
def test_a_live_tick_sends_the_same_requests_whatever_the_universe(venue, n):
    """Chapter II §IV.c: the tick's venue cost is the dexes and the markets in play."""
    from factorylab.runtime.live import LiveVenue

    ex = venue(n=n)
    ex.coins = tuple(ex._listed_coins)  # every listed perp is tradeable
    tick = LiveVenue(ex, markets=lambda: ("BTC", "xyz:TSLA", "PURR/USDC"), bounded=True)
    tick.on_tick(3_600 * 10**9)
    sent = ex._info.calls
    assert sent[("allMids", "")] == 1 and sent[("allMids", "xyz")] == 1
    assert sent[("metaAndAssetCtxs", "")] == 1 and sent[("metaAndAssetCtxs", "xyz")] == 1
    # No book on the tick; settled funding only for the perps in play.
    assert not any(kind == "l2Book" for kind, _ in sent)
    assert {name for kind, name in sent if kind == "fundingHistory"} == {"BTC", "xyz:TSLA"}
    assert sum(sent.values()) == 7


@pytest.mark.parametrize("named,coin", [("buy:xyz:TSLA", "xyz:TSLA"), ("buy:BTC", "BTC"),
                                        ("sell:BTC:m", "BTC"), ("buy:XYZ:tsla", "xyz:TSLA")])
def test_a_named_trade_on_a_hip3_coin_keeps_its_dex(named, coin):
    from factorylab.runtime.grounded import declined_trade

    listed = ("BTC", "xyz:TSLA", "xyz:GOLD")
    assert declined_trade({"counterfactual": named}, listed)["coin"] == coin
    # The dex alone is not a market: it is never the coin a label names.
    assert declined_trade({"counterfactual": "buy:xyz"}, listed) is None


def test_a_hip3_label_canonicalizes_like_any_other():
    from factorylab.runtime.propensity import canonical_label, effect_label

    assert canonical_label("BUY:xyz:tsla:M") == "buy:XYZ:TSLA:m"
    assert canonical_label("buy:btc:xl") == "buy:BTC:xl"
    made = effect_label("venue.place_market", {"coin": "xyz:TSLA", "side": "buy",
                                               "size": "0.5"})
    assert canonical_label(made.lower()) == made
