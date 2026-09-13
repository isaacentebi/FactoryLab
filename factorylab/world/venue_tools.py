"""Validated, zero-priced venue contracts with a per-call runtime audit trail."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from typing import Any

from factorylab.world.exchange import Exchange, Order, OrderKind


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
            raise ValueError(f"{path}: must not be empty")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(f"{path}: invalid decimal string")


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


class VenueTools:
    """Only schema-valid requests reach the exchange; every attempt has an audit entry."""

    PUBLIC_READS = frozenset({"venue.instruments", "venue.mids", "venue.funding",
                              "venue.candles", "venue.order_book", "venue.funding_history"})

    def __init__(self, exchange: Exchange, *, coins: tuple[str, ...], max_leverage: int = 3,
                 spot_pairs: tuple[str, ...] = ()):
        if type(max_leverage) is not int or max_leverage < 1:
            raise ValueError("max_leverage must be a positive integer")
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
                "Place a market buy or sell; optionally reduce only.",
                trade,
                ["coin", "side", "size"],
            ),
            (
                "place_limit",
                "Place a good-until-cancelled limit order; optionally reduce only.",
                {**trade, "price": positive},
                ["coin", "side", "size", "price"],
            ),
            (
                "cancel",
                "Cancel a resting order on its coin.",
                {"coin": coin, "order_id": {"type": "string", "minLength": 1}},
                ["coin", "order_id"],
            ),
            (
                "close",
                "Reduce a position by size, or close it fully when size is omitted or null.",
                {
                    "coin": coin,
                    "market": market,
                    "size": {"anyOf": [*positive["anyOf"], {"type": "null"}], "default": None},
                },
                ["coin"],
            ),
            (
                "set_leverage",
                "Set cross-margin leverage for a coin.",
                {
                    "coin": coin,
                    "market": market,
                    "leverage": {"type": "integer", "minimum": 1, "maximum": max_leverage},
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
            "instruments": "Every market the venue lists, with its lot size, tick size and "
                           "minimum order value. world.venue carries these records for the "
                           "world's trading_markets only; this read returns the full listing.",
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
