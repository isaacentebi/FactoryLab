"""The host in a running world: its pot observed each tick, its droplet a surface.

A world that enables ``[hosting]`` gets the prepaid DigitalOcean credit it runs
on as a pot (``Treasury.observe_hosting``: the wallet moves only when
DigitalOcean's own billing shows money left) and three tools: two reads, of the
droplet and of DigitalOcean's published size list with its prices, and one
write, ``hosting.resize``. The tools state what they do and what they cost;
nothing here says whether a size is worth its price (AGENTS.md rules 1 and 3).
A resize's price reaches the books only through the observed billing that
follows it, never as a debit computed from the list.
"""

from __future__ import annotations

import os
from typing import Any

from factorylab.world.hosting import READ_FAILED, HostingAccount

KIND = "hosting"
READS = ("hosting.droplet", "hosting.sizes")
WRITE = "hosting.resize"


def tool_specs() -> dict[str, dict[str, Any]]:
    """The three tools a ``[hosting]`` world publishes, with examples their schemas accept."""
    tools = {
        "hosting.droplet": (
            "This world's DigitalOcean droplet: its size slug, vCPUs, memory, disk, "
            "status, region and monthly and hourly price; the hosting pot "
            "(DigitalOcean's credit remaining, month-to-date usage, generated_at, and "
            "what the books took from it); and every resize this world submitted. Free.",
            {}, [], [{}]),
        "hosting.sizes": (
            "DigitalOcean's published droplet sizes available in this droplet's region: "
            "slug, vCPUs, memory, disk, monthly and hourly price. Also states this "
            "world's hosting limits: the highest monthly price a resize may target and "
            "whether a resize may grow the disk. Free.",
            {}, [], [{}]),
        "hosting.resize": (
            "Resize this world's droplet to a size slug from hosting.sizes. disk false "
            "(the default) changes vCPUs and memory and keeps the disk, so a later "
            "resize can go back down; disk true also grows the disk, which DigitalOcean "
            "cannot shrink again. DigitalOcean powers the droplet off to resize it. The "
            "new price is charged by DigitalOcean and reaches the hosting pot through "
            "its billing. Free to call.",
            {"size": {"type": "string", "minLength": 1, "maxLength": 64},
             "disk": {"type": "boolean"}},
            ["size"], [{"size": "s-2vcpu-4gb", "disk": False}]),
    }
    return {
        tool_id: {
            "id": tool_id, "kind": KIND, "description": description,
            "args_schema": {"type": "object", "properties": properties,
                            "required": required, "additionalProperties": False,
                            "examples": examples},
            "price_micro_per_call": 0,
        }
        for tool_id, (description, properties, required, examples) in tools.items()
    }


def install(rt: Any, client: Any | None = None) -> None:
    """Open the hosting pot and publish its tools; a world without ``[hosting]`` is untouched.

    ``client`` is the DigitalOcean adapter at its HTTP boundary; without one the
    live adapter is built, and it needs ``DIGITALOCEAN_TOKEN`` (the CLI loads it
    from ``digitalocean.key``). Every call is journaled under ``hosting.<name>``,
    so a replay answers from the record and never asks DigitalOcean twice.
    """
    spec = rt.m.hosting
    if not spec.enabled:
        return
    from factorylab.runtime.resume import JournalProxy
    from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient

    if client is None:
        if not os.environ.get(TOKEN_ENV):
            from factorylab.runtime.reasons import CredentialMissing

            raise CredentialMissing(f"[hosting] needs {TOKEN_ENV}")
        client = DigitalOceanClient()
    account = HostingAccount(JournalProxy(client, rt.ledger, "hosting"),
                             droplet_id=spec.droplet_id,
                             max_monthly_micro=spec.max_monthly_micro,
                             allow_disk_resize=spec.allow_disk_resize)
    rt.treasury.hosting = account
    rt.hosting = account
    rt.tool_specs.update(tool_specs())


def tick(rt: Any) -> None:
    """Observe the pot once and follow any open resize, by reading only."""
    rt.treasury.observe_hosting()
    rt.hosting.tick(rt.ledger)


def _limits(account: HostingAccount) -> dict[str, Any]:
    from decimal import Decimal

    return {"max_monthly_usd": format(Decimal(account.max_monthly_micro) / 1_000_000, "f"),
            "disk_resize_allowed": account.allow_disk_resize}


def execute(rt: Any, action_id: str, handle: str, tool_id: str, args: dict,
            slot: str) -> dict[str, Any]:
    """Run one validated hosting tool; a write goes through ``HostingAccount.resize`` only."""
    from factorylab.cortex.assembly import validate_schema

    account: HostingAccount = rt.hosting
    try:
        validate_schema(args, rt.tool_specs[tool_id]["args_schema"])
    except (ValueError, TypeError, RecursionError) as exc:
        return {"error": f"invalid hosting arguments: {exc}"}
    if tool_id == WRITE:
        result = account.resize(rt.ledger, client_id=f"{handle}:{slot}", handle=handle,
                                size=args.get("size"), disk=args.get("disk", False))
        rt.ledger.append({"kind": "hosting.answer", "handle": handle, "assembly_id": action_id,
                          "status": result["status"], "ts": rt.clock.now_ns})
        return result
    try:
        droplet = account.client.droplet(account.droplet_id)
        sizes = account.client.sizes() if tool_id == "hosting.sizes" else None
    except Exception:  # noqa: BLE001 - a read failure is a fact, not a crash
        return {"error": READ_FAILED}
    if tool_id == "hosting.sizes":
        return {"region": droplet["region"], "current": droplet["size_slug"],
                "limits": _limits(account),
                "sizes": [{k: row[k] for k in ("slug", "vcpus", "memory_mb", "disk_gb",
                                               "price_monthly_usd", "price_hourly_usd",
                                               "description")}
                          for row in sizes
                          if row["available"] and droplet["region"] in row["regions"]],
                "as_of_ns": rt.clock.now_ns}
    return {"droplet": droplet, "pot": account.view(), "limits": _limits(account),
            "resizes": [{k: intent[k] for k in ("size", "disk", "from_size", "status",
                                                "action_id")}
                        for intent in account.intents.values()],
            "as_of_ns": rt.clock.now_ns}
