"""An in-process stand-in for Polymarket's CLOB, Gamma and Data APIs, for the live venue.

It answers ``LivePolymarket``'s requests the way the published protocol does, verifying
what the real CLOB would verify (the L1 ``ClobAuth`` signature, the L2 HMAC over the
exact body bytes, the order's EIP-712 signature against its signer), and it matches,
fills, charges fees and resolves through the seeded ``FakePolymarket``. Nothing here
touches a network or a real key: every signer is generated in the test.
"""

from __future__ import annotations

import base64
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


#: The V2 order's EIP-712 type string, as the exchange contract states it (written here
#: from ctf-exchange-v2 ``Structs.sol``, never taken from the code under test).
ORDER_TYPE_STRING = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,"
    "uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,"
    "bytes32 builder)")


class FakeClob:
    """The HTTP surface ``LivePolymarket.send`` talks to. ``calls`` records every request."""

    SECRET = "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LTAxMjM0NTY3OA=="

    def __init__(self, fake: FakePolymarket, *, confirm: bool = True,
                 owners: dict[str, str] | None = None) -> None:
        self.fake = fake
        self.confirm = confirm  # trades land CONFIRMED at once, else MATCHED until settle()
        # A MATCHED trade moves nothing until it is CONFIRMED; a FAILED one never does:
        # the effect of each trade not yet final, undone in the simulated pot until then.
        self.effects: dict[str, dict] = {}
        self.owners = {k.lower(): v.lower() for k, v in (owners or {}).items()}  # wallet -> EOA
        self.neg_risk_markets: set[str] = set()  # markets whose orders the neg-risk exchange takes
        self.on_post = None  # called just before an order executes (a fee change, say)
        self.hidden_positions: set[str] = set()  # tokens the Data API does not list yet
        self.positions_page: int | None = None  # rows a /positions page carries; None: all
        self.calls: list[tuple[str, str]] = []
        self.orders: dict[str, dict] = {}  # hash -> {"pm": fake id, ...}
        self.pm_to_hash: dict[str, str] = {}
        self.trades: list[dict] = []
        self.fail_next: list = []  # exceptions (or callables) to raise on the next requests
        self.lose_answer = False  # the next POST /order is executed but its answer lost
        self.extra_fills: list[dict] = []  # trade rows to inject on the next /data/trades
        self.signer_address: str | None = None
        self.fail_lookups = 0  # order lookups that fail before one answers
        self.fail_balance = 0  # balance reads that fail before one answers
        self.lose_cancel_answer = False  # the next DELETE /order executes, its answer lost
        self.orders_lag = False  # /data/orders does not list resting orders yet
        self.page_size: int | None = None  # rows a /data/trades page carries; None: all

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
            answer = self._cancel(json.loads(body)["orderID"])
            if self.lose_cancel_answer:
                self.lose_cancel_answer = False
                raise clob.PolymarketUnavailable("transport: TimeoutError")
            return answer
        if path.startswith("/data/order/"):
            if self.fail_lookups:
                self.fail_lookups -= 1
                raise clob.PolymarketUnavailable("transport: TimeoutError")
            return self._order(path.rsplit("/", 1)[1])
        if path == "/data/orders":
            return {"data": [self._order(h) for h, o in self.orders.items()
                             if o["pm"] in self.fake._orders and not self.orders_lag],
                    "next_cursor": clob.END_CURSOR}
        if path == "/data/trades":
            rows = [t for t in self.trades + self.extra_fills
                    if int(t["match_time"]) > int(query.get("after", "0"))]
            self.extra_fills = []
            if self.page_size is None:
                return {"data": rows, "next_cursor": clob.END_CURSOR}
            # The CLOB's cursor is a base64 offset ("MA==" is 0, "LTE=" is -1, the end).
            start = int(base64.b64decode(query.get("next_cursor", clob.FIRST_CURSOR)))
            end = start + self.page_size
            following = (base64.b64encode(str(end).encode()).decode() if end < len(rows)
                         else clob.END_CURSOR)
            return {"data": rows[start:end], "next_cursor": following}
        if path == "/positions":
            return self._positions(query)
        if path == "/balance-allowance" and self.fail_balance:
            self.fail_balance -= 1
            raise clob.PolymarketUnavailable("transport: TimeoutError")
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

    def _typed(self, order: dict, neg_risk: bool) -> dict:
        """The order as EIP-712 typed data, built here from the published struct and
        domain, never from the code under test."""
        exchange = ("0xe2222d279d744050d28e00520010520000310F59" if neg_risk
                    else "0xE111180000d2663C0091e4f400237545B87B996B")
        return {"domain": {"name": "Polymarket CTF Exchange", "version": "2", "chainId": 137,
                           "verifyingContract": exchange},
                "message": {"salt": int(order["salt"]), "maker": order["maker"],
                            "signer": order["signer"], "tokenId": int(order["tokenId"]),
                            "makerAmount": int(order["makerAmount"]),
                            "takerAmount": int(order["takerAmount"]),
                            "side": 0 if order["side"] == "BUY" else 1,
                            "signatureType": int(order["signatureType"]),
                            "timestamp": int(order["timestamp"]),
                            "metadata": bytes.fromhex(order["metadata"][2:]),
                            "builder": bytes.fromhex(order["builder"][2:])}}

    def _verify(self, order: dict, neg_risk: bool) -> str:
        """The order's hash when its signature is valid for the exchange its market
        routes to, else a 400 as the CLOB answers an invalid signature."""
        from eth_account.messages import _hash_eip191_message, encode_typed_data

        typed = self._typed(order, neg_risk)
        order_type = [{"name": n, "type": t} for n, t in (
            ("salt", "uint256"), ("maker", "address"), ("signer", "address"),
            ("tokenId", "uint256"), ("makerAmount", "uint256"), ("takerAmount", "uint256"),
            ("side", "uint8"), ("signatureType", "uint8"), ("timestamp", "uint256"),
            ("metadata", "bytes32"), ("builder", "bytes32"))]
        digest = _hash_eip191_message(encode_typed_data(
            domain_data=typed["domain"], message_types={"Order": order_type},
            message_data=typed["message"]))
        raw = bytes.fromhex(order["signature"][2:])
        try:
            if int(order["signatureType"]) == 3:
                # ERC-7739: the wallet's owner signs a TypedDataSign wrapping the order
                # under the exchange's domain, the wallet as verifying contract.
                wrapper = _hash_eip191_message(encode_typed_data(
                    domain_data=typed["domain"],
                    message_types={"TypedDataSign": [
                        {"name": "contents", "type": "Order"}, {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                        {"name": "verifyingContract", "type": "address"},
                        {"name": "salt", "type": "bytes32"}], "Order": order_type},
                    message_data={"contents": typed["message"], "name": "DepositWallet",
                                  "version": "1", "chainId": 137,
                                  "verifyingContract": order["signer"], "salt": bytes(32)}))
                recovered = Account._recover_hash(wrapper, signature=raw[:65])
                plain = encode_typed_data(domain_data=typed["domain"],
                                          message_types={"Order": order_type},
                                          message_data=typed["message"])
                # The whole ERC-7739 suffix: the app domain, the contents hash, the
                # contents type string exactly, and its length.
                valid = (recovered.lower() == self.owners.get(order["signer"].lower())
                         and order["maker"].lower() == order["signer"].lower()
                         and raw[65:97] == bytes(plain.header)
                         and raw[97:129] == bytes(plain.body)
                         and raw[129:-2] == ORDER_TYPE_STRING.encode()
                         and raw[-2:] == len(ORDER_TYPE_STRING).to_bytes(2, "big"))
            else:
                recovered = Account._recover_hash(digest, signature=raw)
                valid = recovered.lower() == order["signer"].lower() and len(raw) == 65
                if int(order["signatureType"]) in (1, 2):
                    # A proxy or a safe trades only for the EOA that owns it.
                    valid = valid and self.owners.get(
                        order["maker"].lower()) == order["signer"].lower()
                elif int(order["signatureType"]) == 0:
                    valid = valid and order["maker"].lower() == order["signer"].lower()
        except Exception:  # noqa: BLE001 - a signature that does not parse is invalid
            valid = False
        if not valid:
            raise clob.ClobHttpError(400, "invalid signature")
        return "0x" + bytes(digest).hex()

    def _post(self, body: dict) -> dict:
        order = body["order"]
        assert body["orderType"] == "GTC" and body["owner"] == "key-1"
        signed = {**order, "side": 0 if order["side"] == "BUY" else 1}
        market = self.fake._tokens.get(order["tokenId"], (None,))[0]
        digest = self._verify(order, market in self.neg_risk_markets)
        if self.on_post is not None:
            self.on_post()
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
        # A trade of an order is matched no earlier than the order was signed.
        self.orders[digest] = {"pm": result["order_id"],
                               "signed_s": int(order["timestamp"]) // 1000}
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
            rate = self._market_of(event["token_id"])["fee_rate"]
            row = {"id": f"t-{len(self.trades) + 1}", "status":
                   "CONFIRMED" if self.confirm else "MATCHED",
                   "match_time": str(max(1, event["ts_ns"] // 1_000_000_000,
                                         self.orders[digest]["signed_s"])),
                   "asset_id": event["token_id"], "maker_orders": []}
            if taker:
                # The execution's own fee rate, in basis points (get-trades).
                row.update(taker_order_id=digest, size=event["size"], price=event["px"],
                           side="BUY" if event["is_buy"] else "SELL",
                           fee_rate_bps=str(int(rate * 10_000)))
            else:
                row.update(taker_order_id="0xother", size=event["size"], price=event["px"],
                           fee_rate_bps="0",
                           maker_orders=[{"order_id": digest, "matched_amount": event["size"],
                                          "price": event["px"], "fee_rate_bps": "0",
                                          "side": "BUY" if event["is_buy"] else "SELL"}])
            self.trades.append(row)
            if not self.confirm:
                self.effects[row["id"]] = event
                self._move(event, undo=True)

    def _move(self, event: dict, *, undo: bool) -> None:
        """Apply (or undo) one fill's cash and inventory in the simulated pot."""
        size, px, fee = (Decimal(event[k]) for k in ("size", "px", "fee_usd"))
        sign = -1 if undo else 1
        position = self.fake._positions.setdefault(
            event["token_id"], {"size": Decimal(0), "avg_px": Decimal(0)})
        self.fake._cash -= sign * (px * size + fee)  # every fill is a buy
        total = position["size"] + sign * size
        cost = position["size"] * position["avg_px"] + sign * size * px
        position["size"], position["avg_px"] = total, (cost / total if total else Decimal(0))

    def settle(self, status: str = "CONFIRMED") -> None:
        """Every trade not yet final moves to ``status``: CONFIRMED moves its cash and
        inventory, FAILED moves nothing, ever."""
        for trade in self.trades:
            if trade["status"] not in ("CONFIRMED", "FAILED"):
                trade["status"] = status
                event = self.effects.pop(trade["id"], None)
                if event is not None and status == "CONFIRMED":
                    self._move(event, undo=False)

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
                "orderMinSize": public["min_order_size"],
                "negRisk": public["market_id"] in self.neg_risk_markets,
                "feesEnabled": public["fees"]["enabled"],
                "feeSchedule": {"rate": public["fees"]["rate"], "exponent": "1",
                                "takerOnly": True},
                "umaResolutionStatus": public["uma_resolution_status"],
                "description": public["description"]}

    def _gamma(self, path: str, query: dict):
        if path == "/public-search":
            return {"events": [{"markets": [self._raw_market(m)
                                            for m in self.fake._markets.values()]}]}
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
                "min_order_size": book["min_order_size"],
                "neg_risk": self.fake._tokens[token_id][0] in self.neg_risk_markets}

    def _positions(self, query: dict) -> list:
        rows = []
        for token, position in sorted(self.fake._positions.items()):
            if position["size"] <= 0 or token in self.hidden_positions:
                continue
            market_id, side = self.fake._tokens[token]
            rows.append({"asset": token, "size": str(position["size"]),
                         "avgPrice": str(position["avg_px"]), "outcomeIndex": side,
                         "outcome": self.fake._markets[market_id]["outcomes"][side],
                         "conditionId": self.fake._markets[market_id]["condition_id"]})
        offset, limit = int(query.get("offset", "0")), int(query.get("limit", "500"))
        if self.positions_page is not None:
            limit = min(limit, self.positions_page)  # a server may cap a page
        return rows[offset:offset + limit]


def live_venue(fake: FakePolymarket | None = None, *, signer=None, budget: int = 200,
               wall=None, signature_type: int = 0, funder: str | None = None, **clob_args):
    """A ``LivePolymarket`` wired to a ``FakeClob``: (venue, fake clob)."""
    from tests.runtime.test_polymarket_surface import still_fake

    fake = fake if fake is not None else still_fake()
    signer = signer or make_signer()
    funder = funder or signer.address
    clob_args.setdefault("owners", {funder: signer.address})
    server = FakeClob(fake, **clob_args)
    clock = wall or _Wall()
    venue = clob.LivePolymarket(funder=funder, signature_type=signature_type, budget=budget,
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
