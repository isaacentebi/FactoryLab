"""The market universe a world may trade: the manifest's names and selectors, resolved once.

A venue, a market or a data source is the world, not architecture (AGENTS.md): which of
the venue's markets a world lists is the world's own fact, and which of them the
population uses is its own business (Chapter II §I, surfaces not strategies). The
manifest names markets explicitly (``BTC``, ``xyz:TSLA``, ``PURR/USDC``) or by selector:

- ``*`` in ``[exchange] coins``: every live perp of the venue's first perp dex;
- ``<dex>:*`` in ``[exchange] coins``: every live perp of the named builder-deployed
  (HIP-3) dex;
- ``*/USDC`` in ``[venue] spot_pairs``: every USDC-quoted spot pair.

A selector is resolved against the venue's instrument listing at launch, and the
resolved lists are pinned in the world (the Launch ledger and every checkpoint), so a
world is fixed for its life (Chapter II §II: the factory never rewinds, and a world is
one world): a market the venue lists later is not added, and a resume never resolves
again. Explicit names are kept as written; the venue refuses what it does not list.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: Every live perp of the venue's first perp dex.
PERPS_ALL = "*"
#: Every USDC-quoted spot pair the venue lists.
SPOT_ALL = "*/USDC"
#: A builder-deployed perp dex's name, as Hyperliquid's ``perpDexs`` states them.
DEX_NAME = re.compile(r"[A-Za-z0-9]{1,16}")


def is_selector(name: Any) -> bool:
    """Whether ``name`` is a selector (``*``, ``<dex>:*``, ``*/USDC``) rather than a market."""
    return isinstance(name, str) and (name in (PERPS_ALL, SPOT_ALL) or name.endswith(":*"))


def explicit_markets(names: Iterable[str]) -> tuple[str, ...]:
    """The names in ``names`` that are markets, in order: never a selector."""
    return tuple(name for name in names if not is_selector(name))


def selectors(names: Iterable[str]) -> tuple[str, ...]:
    """The selectors in ``names``, in order."""
    return tuple(name for name in names if is_selector(name))


def named_dexes(coins: Iterable[str]) -> tuple[str, ...]:
    """Every HIP-3 dex a coin list names, by selector (``xyz:*``) or by a market on it
    (``xyz:TSLA``), in first-named order and once each."""
    out: dict[str, None] = {}
    for name in coins:
        if isinstance(name, str) and ":" in name:
            out.setdefault(name.split(":", 1)[0])
    return tuple(out)


def validate(coins: Iterable[Any], spot_pairs: Iterable[Any]) -> None:
    """Raise ``ValueError`` unless every selector is well formed.

    Guarantees a perp selector is ``*`` or ``<dex>:*`` with a dex name of 1-16 letters
    and digits, a spot selector is ``*/USDC``, a market named on a dex names a dex of
    that form, and no selector sits in the other list.
    """
    for name in coins:
        if not isinstance(name, str) or not name:
            raise ValueError("exchange.coins must be nonempty coin names or selectors")
        if name == SPOT_ALL:
            raise ValueError("*/USDC selects spot pairs: it belongs in venue.spot_pairs")
        if ":" in name:
            dex, rest = name.split(":", 1)
            if not DEX_NAME.fullmatch(dex) or not rest or ":" in rest and rest != "*":
                raise ValueError(f"exchange.coins entry {name!r} does not name a perp dex "
                                 "market as <dex>:<coin> or select one as <dex>:*")
        elif "*" in name and name != PERPS_ALL:
            raise ValueError(f"exchange.coins selector {name!r} is not * or <dex>:*")
    for name in spot_pairs:
        if isinstance(name, str) and "*" in name and name != SPOT_ALL:
            raise ValueError(f"venue.spot_pairs selector {name!r} is not */USDC")


def resolve(coins: Iterable[str], spot_pairs: Iterable[str],
            listing: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The markets ``coins`` and ``spot_pairs`` name against the venue's ``listing``.

    ``listing`` is an ``instruments()`` answer: ``{"perp": [rows], "spot": [rows]}``,
    each row naming its ``coin`` and, when the venue delisted it, ``delisted``.
    Guarantees explicit names are kept as written and in order, each selector adds the
    live markets it selects in the listing's own order, nothing appears twice, and a
    selector that selects nothing raises ``ValueError`` (a world whose universe is
    empty where the manifest asked for one would be an invented world).
    """
    def live(market: str) -> list[str]:
        return [str(row["coin"]) for row in listing.get(market) or ()
                if isinstance(row, Mapping) and row.get("coin") is not None
                and not row.get("delisted")]

    perps, pairs = live("perp"), live("spot")
    out_coins: list[str] = []
    for name in coins:
        if name == PERPS_ALL:
            chosen = [coin for coin in perps if ":" not in coin]
        elif is_selector(name):
            dex = name[:-2]
            chosen = [coin for coin in perps if coin.startswith(f"{dex}:")]
        else:
            out_coins.append(name)
            continue
        if not chosen:
            raise ValueError(f"the venue lists no live market for selector {name!r}")
        out_coins.extend(chosen)
    out_pairs: list[str] = []
    for name in spot_pairs:
        if name == SPOT_ALL:
            chosen = [pair for pair in pairs if pair.endswith("/USDC")]
            if not chosen:
                raise ValueError(f"the venue lists no live market for selector {name!r}")
            out_pairs.extend(chosen)
        else:
            out_pairs.append(name)
    return tuple(dict.fromkeys(out_coins)), tuple(dict.fromkeys(out_pairs))
