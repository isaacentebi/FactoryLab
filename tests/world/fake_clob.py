"""An in-process stand-in for Polymarket's CLOB, Gamma and Data APIs, for the live venue.

It answers ``LivePolymarket``'s requests the way the published protocol does, verifying
what the real CLOB would verify (the L1 ``ClobAuth`` signature, the L2 HMAC over the
exact body bytes, the order's EIP-712 signature against its signer), and it matches,
fills, charges fees and resolves through the seeded ``FakePolymarket``. Nothing here
touches a network or a real key: every signer is generated in the test.
"""

from __future__ import annotations

import json
from decimal import Decimal
from urllib import parse

from eth_account import Account

from factorylab.world import polymarket_clob as clob
from factorylab.world.polymarket import FakePolymarket


def make_signer(seed: int = 1) -> clob.Signer:
    """A throwaway signer, derived from a test seed: never a funded key."""
    key = (seed.to_bytes(32, "big") if seed else b"\x01" * 32)
    return clob.Signer(Account.from_key(key))


class FakeClob:
    """The HTTP surface ``LivePolymarket.send`` talks to. ``calls`` records every request."""

    SECRET = "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LTAxMjM0NTY3OA=="

    def __init__(self, fake: FakePolymarket, *, confirm: bool = True) -> None:
        self.fake = fake
        self.confirm = confirm  # trades land CONFIRMED at once, else MATCHED until settle()
        self.calls: list[tuple[str, str]] = []
        self.orders: dict[str, dict] = {}  # hash -> {"pm": fake id, ...}
        self.pm_to_hash: dict[str, str] = {}
        self.trades: list[dict] = []
        self.fail_next: list = []  # exceptions (or callables) to raise on the next requests
        self.lose_answer = False  # the next POST /order is executed but its answer lost
        self.extra_fills: list[dict] = []  # trade rows to inject on the next /data/trades
        self.signer_address: str | None = None

    # ---- the transport

    def __call__(self, method: str, url: str, headers: dict, body: str | None):
        parts = parse.urlsplit(url)
        path, query = parts.path, dict(parse.parse_qsl(parts.query))
        self.calls.append((method, path))
        if self.fail_next:
            failure = self.fail_next.pop(0)
            raise failure
        host = parts.netloc
        if host.startswith("gamma"):
            return self._gamma(path, query)
        if host.startswith("data-api"):
            return self._positions(query)
        if path.startswith("/auth/"):
            return self._auth(headers)
        if path == "/book":
            return self._book(query["token_id"])
        self._check_l2(method, path, headers, body or "")
        if path == "/order" and method == "POST":
            answer = self._post(json.loads(body))
            if self.lose_answer:
                self.lose_answer = False
                raise clob.PolymarketUnavailable("transport: TimeoutError")
            return answer
        if path == "/order" and method == "DELETE":
            return self._cancel(json.loads(body)["orderID"])
        if path.startswith("/data/order/"):
            return self._order(path.rsplit("/", 1)[1])
        if path == "/data/orders":
            return {"data": [self._order(h) for h, o in self.orders.items()
                             if o["pm"] in self.fake._orders], "next_cursor": clob.END_CURSOR}
        if path == "/data/trades":
            rows = [t for t in self.trades + self.extra_fills
                    if int(t["match_time"]) > int(query.get("after", "0"))]
            self.extra_fills = []
            return {"data": rows, "next_cursor": clob.END_CURSOR}
        if path == "/balance-allowance":
            return {"balance": str(int(self.fake._cash * clob.UNIT)), "allowances": {}}
        raise AssertionError(f"unexpected request {method} {path}")

    # ---- auth

    def _auth(self, headers: dict) -> dict:
        digest = clob.auth_digest(headers["POLY_ADDRESS"], int(headers["POLY_TIMESTAMP"]),
                                  int(headers["POLY_NONCE"]))
        signer = Account._recover_hash(digest, signature=bytes.fromhex(
            headers["POLY_SIGNATURE"][2:]))
        assert signer.lower() == headers["POLY_ADDRESS"], "L1 signature does not recover"
        self.signer_address = signer.lower()
        return {"apiKey": "key-1", "secret": self.SECRET, "passphrase": "pass-1"}

    def _check_l2(self, method, path, headers, body):
        assert headers["POLY_API_KEY"] == "key-1" and headers["POLY_PASSPHRASE"] == "pass-1"
        expected = clob.hmac_signature(self.SECRET, int(headers["POLY_TIMESTAMP"]), method,
                                       path, body)
        assert headers["POLY_SIGNATURE"] == expected, "L2 HMAC does not verify"

    # ---- orders

    def _market_of(self, token_id: str) -> dict:
        return self.fake._markets[self.fake._tokens[token_id][0]]

    def _post(self, body: dict) -> dict:
        order = body["order"]
        assert body["orderType"] == "GTC" and body["owner"] == "key-1"
        signed = {**order, "side": 0 if order["side"] == "BUY" else 1}
        neg_risk = False
        digest = clob.order_hash(signed, neg_risk)
        if order["signatureType"] != 3:
            recovered = Account._recover_hash(bytes.fromhex(digest[2:]),
                                              signature=bytes.fromhex(order["signature"][2:]))
            assert recovered.lower() == order["signer"].lower(), "order signature"
        if digest in self.orders:
            return {"success": False, "errorMsg": f"order {digest} is invalid. Duplicated.",
                    "orderID": ""}
        price, size = clob.order_price_size(signed["side"], int(order["makerAmount"]),
                                            int(order["takerAmount"]))
        before = len(self.fake._events)
        result = self.fake.place(client_id=digest, token_id=order["tokenId"],
                                 is_buy=signed["side"] == 0, size=size, price=price)
        if result["status"] == "rejected":
            return {"success": False, "errorMsg": result["error"], "orderID": ""}
        self.orders[digest] = {"pm": result["order_id"]}
        self.pm_to_hash[result["order_id"]] = digest
        self._trades(self.fake._events[before:], taker=True)
        del self.fake._events[before:]
        return {"success": True, "errorMsg": "", "orderID": digest,
                "status": "matched" if result["status"] == "filled" else "live",
                "makingAmount": "0", "takingAmount": "0"}

    def _cancel(self, digest: str) -> dict:
        known = self.orders.get(digest)
        if known is None or known["pm"] not in self.fake._orders:
            return {"canceled": [], "not_canceled": {digest: "order can't be found"}}
        self.fake.cancel(client_id=f"cancel:{digest}", order_id=known["pm"])
        return {"canceled": [digest], "not_canceled": {}}

    def _order(self, digest: str) -> dict:
        known = self.orders.get(digest)
        if known is None:
            raise clob.ClobHttpError(404)
        order = self.fake._all_orders[known["pm"]]
        status = ("MATCHED" if order["remaining"] == 0 else
                  "CANCELED" if order.get("cancelled") else "LIVE")
        return {"id": digest, "status": status, "asset_id": order["token_id"],
                "side": "BUY" if order["is_buy"] else "SELL", "price": str(order["price"]),
                "original_size": str(order["size"]), "size_matched": str(order["filled"])}

    # ---- fills, time and the public reads

    def _trades(self, events, *, taker: bool) -> None:
        for event in events:
            if event["kind"] != "fill":
                continue
            digest = self.pm_to_hash[event["order_id"]]
            row = {"id": f"t-{len(self.trades) + 1}", "status":
                   "CONFIRMED" if self.confirm else "MATCHED",
                   "match_time": str(max(1, event["ts_ns"] // 1_000_000_000)),
                   "asset_id": event["token_id"], "maker_orders": []}
            if taker:
                row.update(taker_order_id=digest, size=event["size"], price=event["px"],
                           side="BUY" if event["is_buy"] else "SELL")
            else:
                row.update(taker_order_id="0xother", size=event["size"], price=event["px"],
                           maker_orders=[{"order_id": digest, "matched_amount": event["size"],
                                          "price": event["px"],
                                          "side": "BUY" if event["is_buy"] else "SELL"}])
            self.trades.append(row)

    def settle(self, status: str = "CONFIRMED") -> None:
        """Every trade not yet final moves to ``status``."""
        for trade in self.trades:
            if trade["status"] not in ("CONFIRMED", "FAILED"):
                trade["status"] = status

    def advance(self, now_ns: int) -> list:
        """Move the simulated book; what fills now fills resting (maker) orders."""
        events = self.fake.advance(now_ns)
        self._trades(events, taker=False)
        return events

    def _raw_market(self, market: dict) -> dict:
        public = self.fake._public(market, detail=True)
        return {"id": public["market_id"], "conditionId": public["condition_id"],
                "question": public["question"], "slug": public["slug"],
                "outcomes": json.dumps([o["outcome"] for o in public["outcomes"]]),
                "clobTokenIds": json.dumps([o["token_id"] for o in public["outcomes"]]),
                "outcomePrices": json.dumps([o["price"] for o in public["outcomes"]]),
                "active": public["active"], "closed": public["closed"],
                "acceptingOrders": public["accepting_orders"], "enableOrderBook": True,
                "orderPriceMinTickSize": public["tick_size"],
                "orderMinSize": public["min_order_size"], "negRisk": False,
                "feesEnabled": public["fees"]["enabled"],
                "feeSchedule": {"rate": public["fees"]["rate"], "exponent": "1",
                                "takerOnly": True},
                "umaResolutionStatus": public["uma_resolution_status"],
                "description": public["description"]}

    def _gamma(self, path: str, query: dict):
        if path.startswith("/markets/"):
            market = self.fake._markets.get(path.rsplit("/", 1)[1])
            if market is None:
                raise clob.ClobHttpError(404)
            return self._raw_market(market)
        token = query.get("clob_token_ids")
        if token not in self.fake._tokens:
            return []
        market = self._market_of(token)
        if (query.get("closed") == "true") != market["closed"]:
            return []
        return [self._raw_market(market)]

    def _book(self, token_id: str) -> dict:
        book = self.fake.order_book(token_id, 5)
        return {"asset_id": token_id, "market": book["condition_id"],
                "bids": book["bids"], "asks": book["asks"], "tick_size": book["tick_size"],
                "min_order_size": book["min_order_size"], "neg_risk": False}

    def _positions(self, query: dict) -> list:
        rows = []
        for token, position in sorted(self.fake._positions.items()):
            if position["size"] <= 0:
                continue
            market_id, side = self.fake._tokens[token]
            rows.append({"asset": token, "size": str(position["size"]),
                         "avgPrice": str(position["avg_px"]), "outcomeIndex": side,
                         "outcome": self.fake._markets[market_id]["outcomes"][side],
                         "conditionId": self.fake._markets[market_id]["condition_id"]})
        return rows


def live_venue(fake: FakePolymarket | None = None, *, signer=None, budget: int = 200,
               wall=None, **clob_args):
    """A ``LivePolymarket`` wired to a ``FakeClob``: (venue, fake clob)."""
    from tests.runtime.test_polymarket_surface import still_fake

    fake = fake if fake is not None else still_fake()
    server = FakeClob(fake, **clob_args)
    signer = signer or make_signer()
    clock = wall or _Wall()
    venue = clob.LivePolymarket(funder=signer.address, signature_type=0, budget=budget,
                                signer=signer, send=server, identity=lambda: ("ns", "nonce"),
                                wall=clock, nonce=lambda: 7)
    return venue, server


class _Wall:
    """A wall clock that moves one second a read."""

    def __init__(self, start: int = 1_790_000_000_000_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        self.now += 1_000_000_000
        return self.now


def decimal(value) -> Decimal:
    return Decimal(str(value))
