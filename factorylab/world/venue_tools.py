"""Validated, zero-priced venue contracts with a per-call runtime audit trail."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from typing import Any

from factorylab.world.exchange import Exchange, Order, OrderKind, VenueUnavailable
from factorylab.world.vaults import (
    ADDRESS_PATTERN,
    CREATE_FEE_USD,
    DESCRIPTION_LENGTH,
    MIN_CREATE_USD,
    NAME_LENGTH,
)

#: The vault surface's writes and reads (``[venue] vault_tools``).
VAULT_WRITES = frozenset({"venue.vault_create", "venue.vault_deposit", "venue.vault_withdraw"})
VAULT_READS = frozenset({"venue.vault_details", "venue.vault_positions"})


def seed_markets(exchange, spec) -> None:
    """The venue keeps its listing universe while the manifest seeds trading permission.

    The seed only ever adds, and only markets the venue lists: a manifest coin
    or pair the adapter does not list is dropped here for the same reason a
    ``market`` registration is refused, and an adapter never acquires a market
    class it was not built with. An adapter that publishes no listing at all
    keeps none: nothing is seeded onto it, and the manifest seed remains the
    trading permission the venue tools were built with.
    """
    from factorylab.world.exchange import FakeExchange

    target = getattr(exchange, "target", exchange)
    coins, pairs = spec.coins, spec.spot_pairs
    if isinstance(target, FakeExchange):
        target.listed_coins = tuple(dict.fromkeys((*target.coins, *target.listed_coins)))
        target.listed_spot_pairs = tuple(dict.fromkeys(
            (*target.spot_pairs, *target.listed_spot_pairs)))
        coins = tuple(c for c in coins if c in target.listed_coins)
        pairs = tuple(p for p in pairs if p in target.listed_spot_pairs)
        for coin in target.listed_coins:
            target._mids.setdefault(coin, Decimal(100))
            target._mid_history.setdefault(coin, [])
        for pair in target.listed_spot_pairs:
            base = pair.split("/")[0]
            target._mids.setdefault(base, Decimal(100))
            target._mids.setdefault(pair, target._mids[base])
            target._mid_history.setdefault(base, [])
            target._mid_history.setdefault(pair, [])
    for name, seeded in (("coins", coins), ("spot_pairs", pairs)):
        listed = getattr(target, name, None)
        if isinstance(listed, (list, tuple)):
            setattr(exchange, name, tuple(dict.fromkeys((*listed, *seeded))))


@dataclass(frozen=True)
class ToolSpec:
    """A tool's identity and price are fixed; schemas use JSON Schema keywords."""

    id: str
    description: str
    args_schema: dict
    price_micro_per_call: int
    kind: str = "venue"


def _validate(value: Any, schema: dict, path: str = "args") -> None:
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _validate(value, option, path)
                return
            except ValueError:
                pass
        raise ValueError(f"{path}: expected a finite positive decimal or allowed null")
    kind = schema["type"]
    matches = {
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float, Decimal),
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if not matches[kind]:
        raise ValueError(f"{path}: expected {kind}")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}: missing argument {key}")
        for key, item in value.items():
            if key not in schema["properties"]:
                raise ValueError(f"{path}: unexpected argument {key}")
            _validate(item, schema["properties"][key], f"{path}.{key}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: must be one of {schema['enum']}")
    if kind in ("integer", "number"):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError(f"{path}: must be finite")
        if "minimum" in schema and number < schema["minimum"]:
            raise ValueError(f"{path}: minimum is {schema['minimum']}")
        if "maximum" in schema and number > schema["maximum"]:
            raise ValueError(f"{path}: maximum is {schema['maximum']}")
        if "exclusiveMinimum" in schema and number <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path}: must exceed {schema['exclusiveMinimum']}")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: must not be empty" if schema.get("minLength", 0) <= 1
                             else f"{path}: at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path}: at most {schema['maxLength']} characters")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(f"{path}: invalid address" if schema["pattern"] == ADDRESS_PATTERN
                             else f"{path}: invalid decimal string")


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


#: Hyperliquid's documented REST limits ("Rate limits and user limits",
#: hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits):
#: "REST requests share an aggregated weight limit of 1200 per minute" per IP;
#: l2Book, allMids, clearinghouseState and spotClearinghouseState weigh 2; every
#: other documented info request weighs 20; candleSnapshot adds weight per 60 items
#: returned, and fundingHistory, userFunding and userFills per 20. The added weight per
#: interval is not stated, so it is counted as 1.
VENUE_WEIGHT_PER_MINUTE = 1200
#: What the population's venue reads may use by default, all seats together: 40%,
#: leaving 720 a minute for the kernel's own calls (account and spot state, mids,
#: asset contexts, open orders, funding and fill pages each tick, and orders).
DEFAULT_PUBLIC_READ_WEIGHT_PER_MINUTE = 480
#: The venue requests behind each ``HyperliquidExchange._guarded`` name, by weight.
REQUEST_WEIGHT = {"all_mids": 2, "spot_mids": 2, "l2_snapshot": 2, "user_state": 2,
                  "spot_user_state": 2}
#: Requests whose weight grows with the items returned: one more per this many.
REQUEST_ITEMS_PER_WEIGHT = {"candles": 60, "funding_history": 20, "user_funding": 20,
                            "user_fills_by_time": 20, "non_funding_ledger": 20}


#: What a venue write costs besides its fill, as a fact (Chapter II §I.b): no gas.
NO_GAS = ("It pays no gas; a fill pays the venue's fee at the rates world.venue lists "
          "(taker_fee_rate, maker_fee_rate).")


def request_weight(what: str, result: Any = None) -> int:
    """The documented weight of one venue request named ``what``; items counted when known."""
    weight = REQUEST_WEIGHT.get(what, 20)
    per = REQUEST_ITEMS_PER_WEIGHT.get(what)
    if per is not None and isinstance(result, (list, tuple)):
        weight += len(result) // per
    return weight


#: Every venue read a seat can call, and the weight of one attempt of it: the
#: requests the live adapter sends for it, at their documented weights.
#: ``venue.instruments`` sends none: the adapter answers from the listing it loaded.
_BASE_WEIGHT = {"venue.instruments": 0, "venue.mids": 2, "venue.order_book": 2,
                "venue.funding": 20, "venue.candles": 20, "venue.funding_history": 20,
                "venue.open_orders": 20,
                # user state, spot user state and all mids (the last two with spot pairs)
                "venue.positions": 6,
                "venue.vault_details": 20,
                # userVaultEquities and leadingVaults
                "venue.vault_positions": 40}
_ITEMS_PER_WEIGHT = {"venue.candles": ("n", 60, 200), "venue.funding_history": ("n", 20, 100)}
#: The span a seat's venue read share is counted over: any sliding minute.
READ_WINDOW_NS = 60_000_000_000
#: Each seat read and the adapter method (and arguments, from the read's own) that
#: answers it: the key an identical read within one tick is answered under.
TICK_ANSWERED = {
    "venue.instruments": ("instruments", ()), "venue.mids": ("mids", ()),
    "venue.funding": ("funding", ()),
    "venue.candles": ("candles", ("coin", "interval", "n")),
    "venue.order_book": ("order_book", ("coin", "depth")),
    "venue.funding_history": ("funding_history", ("coin", "n")),
    "venue.open_orders": ("open_orders", ()), "venue.positions": ("account", ()),
    "venue.vault_details": ("vault_details", ("vault",)),
    "venue.vault_positions": ("vault_equities", ()),
}
TICK_ANSWER_FACT = (
    "Within one world tick, until a venue write, a read identical to a venue request "
    "already answered in that tick (the kernel's own included) is answered from that "
    "answer, and sends no request.")


def public_read_weight(tool_id: str, args: Any) -> int | None:
    """The documented weight of one attempt of a seat's venue read, or None for any other tool.

    Guarantees the weight never undercounts a well-formed call's first attempt: an
    item count that is missing or out of its schema's range is counted at the
    schema's maximum. Retries are not in it; they are charged as the adapter sends
    them.
    """
    base = _BASE_WEIGHT.get(tool_id)
    if base is None:
        return None
    extra = _ITEMS_PER_WEIGHT.get(tool_id)
    if extra is None:
        return base
    key, per, most = extra
    n = args.get(key) if isinstance(args, dict) else None
    if type(n) is not int or not 1 <= n <= most:
        n = most
    return base + -(-n // per)


class VenueTools:
    """Only schema-valid requests reach the exchange; every attempt has an audit entry."""

    PUBLIC_READS = frozenset({"venue.instruments", "venue.mids", "venue.funding",
                              "venue.candles", "venue.order_book", "venue.funding_history"})

    def __init__(self, exchange: Exchange, *, coins: tuple[str, ...],
                 max_leverage: int | None = None, spot_pairs: tuple[str, ...] = ()):
        # ``max_leverage`` is accepted and ignored (architect decision D1): leverage is
        # whatever the venue allows, and the venue's refusal is the only ceiling.
        # Callers that still pass the deprecated ``[tools] max_leverage`` keep working.
        del max_leverage
        if not coins or any(not isinstance(coin, str) or not coin for coin in coins):
            raise ValueError("coins must contain nonempty coin names")
        self.exchange = exchange
        self.coins, self.spot_pairs = tuple(coins), tuple(spot_pairs)
        public = list((*coins, *spot_pairs))
        for name in ("coins", "spot_pairs", "listed_coins", "listed_spot_pairs", "_listed_coins"):
            values = getattr(exchange, name, ())
            if isinstance(values, (list, tuple)):
                public.extend(values)
        spot_names = getattr(exchange, "_spot_names", {})
        if isinstance(spot_names, dict):
            public.extend(spot_names)
        self.public_coins = tuple(dict.fromkeys(public))
        self.log: list[tuple[str, dict, bool]] = []
        coin = {"type": "string", "enum": list(dict.fromkeys((*coins, *spot_pairs)))}
        positive = {
            "anyOf": [
                {"type": "number", "exclusiveMinimum": 0},
                {
                    "type": "string",
                    "pattern": r"^(?=[0-9.]*[1-9])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
                    r"(?:[eE][+-]?[0-9]+)?$",
                },
            ]
        }
        market = {"type": "string", "enum": ["perp", "spot"], "default": "perp"}
        trade = {
            "market": market,
            "coin": coin,
            "side": {"type": "string", "enum": ["buy", "sell"]},
            "size": positive,
            "reduce_only": {"type": "boolean", "default": False},
        }
        definitions = [
            (
                "candles",
                "Recent OHLCV candles, oldest first; timestamps in nanoseconds.",
                {
                    "coin": coin,
                    "interval": {"type": "string", "enum": ["1m", "5m", "15m", "1h"]},
                    "n": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                ["coin", "interval", "n"],
            ),
            (
                "order_book",
                "Best-first bid and ask price/size levels.",
                {"coin": coin, "depth": {"type": "integer", "minimum": 1, "maximum": 20}},
                ["coin", "depth"],
            ),
            (
                "funding_history",
                "Recent funding rates, oldest first; timestamps in nanoseconds.",
                {"coin": coin, "n": {"type": "integer", "minimum": 1, "maximum": 100}},
                ["coin", "n"],
            ),
            ("open_orders", "Currently resting orders for the account.", {}, []),
            ("positions", "Open signed positions and entry prices for the account.", {}, []),
            (
                "place_market",
                "Place a market buy or sell; optionally reduce only. " + NO_GAS,
                trade,
                ["coin", "side", "size"],
            ),
            (
                "place_limit",
                "Place a good-until-cancelled limit order; optionally reduce only. " + NO_GAS,
                {**trade, "price": positive},
                ["coin", "side", "size", "price"],
            ),
            (
                "cancel",
                "Cancel a resting order on its coin. " + NO_GAS,
                {"coin": coin, "order_id": {"type": "string", "minLength": 1}},
                ["coin", "order_id"],
            ),
            (
                "close",
                "Reduce a position by size, or close it fully when size is omitted or null. "
                + NO_GAS,
                {
                    "coin": coin,
                    "market": market,
                    "size": {"anyOf": [*positive["anyOf"], {"type": "null"}], "default": None},
                },
                ["coin"],
            ),
            (
                "set_leverage",
                "Set cross-margin leverage for a coin. " + NO_GAS,
                {
                    "coin": coin,
                    "market": market,
                    "leverage": {"type": "integer", "minimum": 1},
                },
                ["coin", "leverage"],
            ),
        ]
        self._specs = {
            f"venue.{name}": ToolSpec(
                f"venue.{name}",
                description,
                deepcopy(
                    {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    }
                ),
                0,
            )
            for name, description, properties, required in definitions
        }
        # ``instruments`` is where the venue's whole listing lives. The world block
        # carries only the trading markets' records, so this description is what
        # tells an assembly the rest of the listing is one call away.
        listings = {
            "instruments": "Every market the venue lists, with its lot size, tick size, "
                           "minimum order value and this account's taker and maker fee "
                           "rates. world.venue carries these records for the world's "
                           "trading_markets only; this read returns the full listing.",
            "mids": "Public venue mids for all listed markets.",
            "funding": "Public venue funding for all listed markets.",
        }
        for name, description in listings.items():
            self._specs[f"venue.{name}"] = ToolSpec(
                f"venue.{name}", description,
                {"type": "object", "properties": {}, "required": [],
                 "additionalProperties": False}, 0)
        # Read identities are checked against the venue at dispatch, not the seed.
        for name in ("candles", "order_book", "funding_history"):
            self._specs[f"venue.{name}"].args_schema["properties"]["coin"] = {
                "type": "string", "enum": list(self.public_coins)}

    def admit_market(self, coin: str, market: str) -> None:
        """A validated registration expands only the world's trading schemas."""
        attr = "spot_pairs" if market == "spot" else "coins"
        setattr(self, attr, tuple(dict.fromkeys((*getattr(self, attr), coin))))
        self.public_coins = tuple(dict.fromkeys((*self.public_coins, coin)))
        for name in ("candles", "order_book", "funding_history"):
            self._specs[f"venue.{name}"].args_schema["properties"]["coin"]["enum"] = list(
                self.public_coins)
        for name in ("place_market", "place_limit", "close", "cancel", "set_leverage"):
            self._specs[f"venue.{name}"].args_schema["properties"]["coin"]["enum"] = list(
                dict.fromkeys((*self.coins, *self.spot_pairs)))

    def contracts(self) -> list[ToolSpec]:
        """Return detached contracts; editing their schemas cannot alter validation."""
        return deepcopy(list(self._specs.values()))

    def call(self, tool_id: str, args: dict) -> dict:
        """Return JSON-ready results or errors, logging success, rejection, and invalid input."""
        recorded_args = deepcopy(args)
        ok = False
        write = False
        try:
            spec = self._specs.get(tool_id)
            if spec is None:
                return {"error": f"unknown tool: {tool_id}"}
            try:
                _validate(args, spec.args_schema)
            except ValueError as exc:
                return {"error": str(exc)}
            write = tool_id in (
                "venue.place_market",
                "venue.place_limit",
                "venue.cancel",
                "venue.close",
                "venue.set_leverage",
            )
            result = _json_value(self._dispatch(tool_id, args))
            if result.get("status") == "rejected":
                return {"status": "rejected", "error": result.get("error") or "venue rejection"}
            ok = result.get("error") is None
            return result
        except Exception as exc:
            error = {"error": f"{type(exc).__name__}: {exc}"}
            return {"status": "rejected", **error} if write else error
        finally:
            self.log.append((tool_id, recorded_args, ok))

    def _dispatch(self, tool_id: str, args: dict) -> Any:
        ex = self.exchange
        if tool_id == "venue.instruments":
            return ex.instruments()
        if tool_id == "venue.mids":
            return {"mids": ex.mids()}
        if tool_id == "venue.funding":
            return {"funding": ex.funding()}
        if tool_id in ("venue.candles", "venue.order_book", "venue.funding_history"):
            if (args["coin"] not in self.public_coins
                    or tool_id == "venue.funding_history" and "/" in args["coin"]):
                return {"error": "coin is not listed for this public read"}
        if tool_id in ("venue.place_market", "venue.place_limit", "venue.close",
                       "venue.set_leverage"):
            allowed = self.spot_pairs if args.get("market", "perp") == "spot" else self.coins
            if args["coin"] not in allowed:
                return {"error": "market is not registered for trading"}
        if tool_id == "venue.candles":
            return {"candles": ex.candles(args["coin"], args["interval"], args["n"])}
        if tool_id == "venue.order_book":
            return ex.order_book(args["coin"], args["depth"])
        if tool_id == "venue.funding_history":
            return {"funding_history": ex.funding_history(args["coin"], args["n"])}
        if tool_id == "venue.open_orders":
            return {"open_orders": ex.open_orders()}
        if tool_id == "venue.positions":
            account = ex.account()
            if getattr(account, "stale", False):
                # The adapter fell back to its last complete snapshot because the venue
                # did not answer: the kernel reads that as stale, and a seat is told
                # the venue did not answer, never shown old positions as this read's.
                raise VenueUnavailable("account: the venue did not answer this read")
            return {"positions": account.positions, **({"spot_balances": account.spot_balances}
                    if getattr(ex, "spot_pairs", ()) else {})}
        if tool_id in ("venue.place_market", "venue.place_limit"):
            limit = tool_id == "venue.place_limit"
            return ex.place(
                Order(
                    args["coin"],
                    args["side"] == "buy",
                    Decimal(str(args["size"])),
                    OrderKind.LIMIT if limit else OrderKind.MARKET,
                    Decimal(str(args["price"])) if limit else None,
                    reduce_only=args.get("reduce_only", False),
                    market=args.get("market", "perp"),
                )
            )
        if tool_id == "venue.cancel":
            return ex.cancel(args["order_id"], coin=args["coin"])
        if tool_id == "venue.close":
            size = args.get("size")
            return ex.close(args["coin"], None if size is None else Decimal(str(size)),
                            market=args.get("market", "perp"))
        return ex.set_leverage(args["coin"], args["leverage"], market=args.get("market", "perp"))


def vault_specs() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Guarantees the vault surface's contracts and one schema-valid example for each.

    Descriptions state what a call does and what the venue charges or refuses, and
    nothing about what a vault is for. Every call is free: the venue charges
    nothing for a read, and what it charges for a write (the creation fee) lands
    on the venue account, where it happens.
    """
    address = {"type": "string", "pattern": ADDRESS_PATTERN}
    usd = {
        "anyOf": [
            {"type": "number", "exclusiveMinimum": 0},
            {"type": "string",
             "pattern": r"^(?=[0-9.]*[1-9])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"},
        ]
    }
    definitions = [
        ("venue.vault_details",
         "A vault's venue record: name, leader, total equity, depositor count, leader "
         "fraction and commission, whether it takes deposits or is closed, and this "
         "account's own equity, lockup end and withdrawable amount in it.",
         {"vault": address}, ["vault"], 0),
        ("venue.vault_positions",
         "This account's equity in each vault it holds, with each lockup end, and the "
         "vaults it leads.", {}, [], 0),
        ("venue.vault_create",
         "Create a vault led by this account, moving usd from perps collateral into it. "
         f"The venue also charges a {CREATE_FEE_USD} USDC creation fee from perps "
         f"collateral. usd is at least {MIN_CREATE_USD}; name ({NAME_LENGTH[0]}-"
         f"{NAME_LENGTH[1]} characters) and description ({DESCRIPTION_LENGTH[0]}-"
         f"{DESCRIPTION_LENGTH[1]}) cannot be changed later. Returns the vault address.",
         {"name": {"type": "string", "minLength": NAME_LENGTH[0],
                   "maxLength": NAME_LENGTH[1]},
          "description": {"type": "string", "minLength": DESCRIPTION_LENGTH[0],
                          "maxLength": DESCRIPTION_LENGTH[1]},
          "usd": usd}, ["name", "description", "usd"], 0),
        ("venue.vault_deposit",
         "Move usd from perps collateral into a vault. The deposit is locked until the "
         "lockup end venue.vault_details reports.",
         {"vault": address, "usd": usd}, ["vault", "usd"], 0),
        ("venue.vault_withdraw",
         "Move usd of this account's equity in a vault back to perps collateral. Refused "
         "before the lockup end, and in a vault this account leads when its share would "
         "fall below 5%. The venue pays a withdrawal net of the leader's commission on "
         "the profit of the part withdrawn.",
         {"vault": address, "usd": usd}, ["vault", "usd"], 0),
    ]
    specs = {
        tool_id: {"id": tool_id, "description": description,
                  "args_schema": {"type": "object", "properties": deepcopy(properties),
                                  "required": required, "additionalProperties": False},
                  "price_micro_per_call": price, "kind": "venue"}
        for tool_id, description, properties, required, price in definitions
    }
    vault = "0x" + "0" * 40
    examples = {
        "venue.vault_details": [{"vault": vault}],
        "venue.vault_positions": [{}],
        "venue.vault_create": [{"name": "example", "description": "Example description.",
                                "usd": "100"}],
        "venue.vault_deposit": [{"vault": vault, "usd": "10"}],
        "venue.vault_withdraw": [{"vault": vault, "usd": "10"}],
    }
    return specs, examples
