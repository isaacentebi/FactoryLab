"""Typed custody: where this factory's money actually is, and when it was last seen.

Edition 3 C5, from GPT-6 Pro's third reading §2: three quantities must stay
distinct — the learning score (evidence for a rule), the seat entitlement
(permission to spend within the compute budget) and the **assets and credits**
held at a custodian or a provider, which change only by a verified transaction,
a provider charge, a refund or a purchase, and never by an internal
reclassification.

This module is the third of those. It answers one question, for the request
line and for an operator: what does this factory hold, in which account, and how
old is that observation. Six accounts, named for the custodian that actually
holds the balance:

``openrouter_credit``  prepaid model credit on the OpenRouter account
``venice_credit``      prepaid model credit on the Venice account
``venue_perps``        the perpetuals account: cash, margin used, positions, equity
``venue_spot``         the venue's spot balances
``base_reserve``       USDC at the reserve address on Base
``pending_conversions``a held source plus a claim on a destination, in flight

Every account carries ``observed_at_ns`` and a ``status`` of ``observed`` or
``unavailable`` with a ``reason``. The compute wallet is not here: it is
authority, the constitutional ceiling on what may be spent, not an asset, and
``Wallet.pots`` now labels it so.

Two rules this module exists to enforce:

* **No fabrication.** A failed venue read is ``unavailable`` with the reason.
  It is never filled in with the compute wallet's balance and an empty position
  set, which is what `_world_block` and `_producer_step` used to do — the
  reviewer's "the account-read fallback invents financial facts".
* **No network from the prompt path.** Everything here is read from the
  treasury's cached pots and from the runtime's one-read-a-tick account memo,
  whose failures are memoised too, so building a hundred prompts in a tick asks
  the venue once and a venue that refused is not asked again until the next tick.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

OBSERVED = "observed"
UNAVAILABLE = "unavailable"

#: Every account this factory can hold value in, in the order a reader wants them.
ACCOUNTS = ("openrouter_credit", "venice_credit", "venue_perps", "venue_spot",
            "base_reserve", "pending_conversions")


def unavailable(reason: str, observed_at_ns: int | None = None) -> dict[str, Any]:
    """An account nobody could read. It states why, and states no amount."""
    return {"status": UNAVAILABLE, "reason": str(reason)[:200],
            "observed_at_ns": observed_at_ns}


def observed(observed_at_ns: int | None, **fields: Any) -> dict[str, Any]:
    """An account read successfully, with the moment the read was made."""
    return {"status": OBSERVED, "reason": None, "observed_at_ns": observed_at_ns, **fields}


def _micro(value: Any) -> int | None:
    return value if type(value) is int else None


def _credit(pots: dict, key: str, observed_at_ns: int | None, *,
            seller: str | None = None) -> dict[str, Any]:
    """A provider credit pot: an integer is an observation, ``None`` is an outage."""
    value = pots.get("sellers", {}).get(seller) if seller else pots.get(key)
    if _micro(value) is None:
        return unavailable("provider balance unavailable", observed_at_ns)
    return observed(observed_at_ns, balance_micro=value)


def _venue_accounts(account: Any, reason: str | None,
                    observed_at_ns: int | None) -> tuple[dict, dict]:
    """Split one venue account read into the two custody accounts it describes."""
    if account is None:
        why = unavailable(f"venue account read failed: {reason or 'unavailable'}",
                          observed_at_ns)
        return why, dict(why)
    positions = [{"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                 for p in account.positions]
    spot = [{"coin": b.coin, "total": str(b.total), "available": str(b.available)}
            for b in account.spot_balances]
    # The perps account alone. A venue that does not state the split is taken at
    # its word for the quote balance only, which is the conservative reading.
    perps_equity = account.perps_equity_usd
    if perps_equity is None:
        perps_equity = account.equity_usd - sum(
            (Decimal(str(b["total"])) for b in spot if b["coin"] == "USDC"), Decimal(0))
    perps = observed(
        observed_at_ns,
        equity_usd=str(perps_equity),
        cash_usd=str(account.cash_usd),
        margin_used_usd=str(account.margin_used_usd),
        positions=positions,
    )
    unpriced = tuple(getattr(account, "unpriced", ()) or ())
    # A token the venue gives no USD mark is held, listed, and named as unpriced:
    # it is in no equity figure, and it is not hidden either.
    return perps, observed(observed_at_ns, balances=spot,
                           **({"unpriced": list(unpriced)} if unpriced else {}))


def _pending(treasury: Any, observed_at_ns: int | None) -> dict[str, Any]:
    """Transfers in flight: what is held at the source and what is claimed at the destination.

    A pending bridge is never a balance in two places and never a balance in
    neither. The source is held (the wallet's principal hold, and the pot it
    will leave), the destination carries a claim that is not yet an asset.
    """
    rows: list[dict[str, Any]] = []
    states = []
    state = getattr(treasury, "state", None)
    if state and state.get("status") == "submitted":
        states.append((state, "in_flight"))
    for entry in getattr(treasury, "stranded", []) or []:
        states.append((entry["state"], "stranded"))
    for item, status in states:
        rows.append({
            "transfer_id": item.get("id"),
            "direction": item.get("direction"),
            # A hybrid conversion's observed source is the venue: its shadow leg pays
            # the testnet pots while real mainnet USDC buys the credit (II.IV).
            "source": ("venue_perps" if "shadow_send" in (item.get("steps") or ())
                       else SOURCE_OF.get(item.get("direction"))),
            "destination": DESTINATION_OF.get(item.get("direction")),
            "held_micro": item.get("amount_micro"),
            "claim_micro": item.get("received_micro"),
            "class": "financing" if item.get("direction") == "to_venice" else "transfer",
            "status": status,
            "reason": item.get("reason"),
        })
    return observed(observed_at_ns, transfers=rows)


#: Where each treasury direction takes money from and where it lands. The Venice
#: route converts principal into compute credit: it replenishes Venice and nothing
#: else, and never implies an OpenRouter top-up.
SOURCE_OF = {"to_reserve": "venue_perps", "to_venue": "base_reserve",
             "to_venice": "base_reserve", "spot_to_perps": "venue_spot",
             "perps_to_spot": "venue_perps"}
DESTINATION_OF = {"to_reserve": "base_reserve", "to_venue": "venue_perps",
                  "to_venice": "venice_credit", "spot_to_perps": "venue_perps",
                  "perps_to_spot": "venue_spot"}


def custody_view(rt: Any) -> dict[str, Any]:
    """Return the six custody accounts for this runtime, fabricating none of them.

    R3-E renders this; the data is built here so the rendering cannot invent a
    number the runtime never observed. Nothing in this function performs a
    network call: the venue comes from the tick's memoised account read (failure
    memoised too) and the provider and reserve balances from the treasury's last
    persisted pot observation.
    """
    treasury = getattr(rt, "treasury", None)
    pots = treasury.pots() if treasury is not None else {}
    pots_ns = pots.get("observed_at_ns")
    account = reason = None
    account_ns = pots_ns
    if getattr(rt, "exchange", None) is not None:
        account, reason, account_ns = rt._tick_account_observation()
    else:
        reason = "world has no venue"
    perps, spot = _venue_accounts(account, reason, account_ns)
    reserve = (observed(pots_ns, balance_micro=pots["reserve"])
               if _micro(pots.get("reserve")) is not None
               else unavailable("reserve balance unavailable", pots_ns))
    view = {
        "openrouter_credit": _credit(pots, "seed", pots_ns),
        "venice_credit": _credit(pots, "sellers", pots_ns, seller="venice"),
        "venue_perps": perps,
        "venue_spot": spot,
        "base_reserve": reserve,
        "pending_conversions": _pending(treasury, pots_ns),
    }
    if "vaults" in pots:
        # Only a world with the vault surface has this account ([venue] vault_tools):
        # equity this venue account holds in vaults, which is not perps collateral.
        view["venue_vaults"] = (observed(pots_ns, balance_micro=pots["vaults"])
                                if _micro(pots.get("vaults")) is not None
                                else unavailable("vault equity unavailable", pots_ns))
    from factorylab.runtime.polymarket import custody as polymarket_custody

    polymarket = polymarket_custody(rt)
    if polymarket is not None:
        # The Polygon collateral pot of a world that enables event markets. It is
        # its own custodian and backs only its own orders (runtime/polymarket.py).
        view["polymarket"] = polymarket
    view["authority"] = {
        # Not an asset: the constitutional ceiling the assets above back.
        "kind": "authority", "unlocked_micro": rt.wallet.unlocked,
        "locked_micro": rt.wallet.locked, "balance_micro": rt.wallet.balance,
    }
    return view
