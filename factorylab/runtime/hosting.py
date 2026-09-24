"""The host in a running world: its own pot, booked from its invoices, and two reads.

A world that enables ``[hosting]`` runs on a DigitalOcean droplet. What the droplet
costs is booked from DigitalOcean's own invoice lines for it, once a reserve
window (``Treasury.observe_hosting``; world/hosting.py). The population sees two
free reads, the droplet and DigitalOcean's size list with its prices, capped per
seat per reserve window. Both publish structured facts only (slugs, counts,
prices, times), never DigitalOcean-authored prose, so no outside text reaches a
wake that can write. There is no write: a resize powers the droplet off, nothing
here could power it back on, and the factory runs on it.

Every DigitalOcean read runs under one monotonic deadline of at most one tick,
name lookup included (AGENTS.md rule 12): a slow DigitalOcean leaves the pot
unknown until the next window, and never stalls the world longer than a tick.
"""

from __future__ import annotations

import os
from typing import Any

from factorylab.world.hosting import SEAT_READS_PER_WINDOW, HostingAccount, verify

KIND = "hosting"
READS = ("hosting.droplet", "hosting.sizes")
READ_FAILED = "hosting read unavailable"
READ_LIMITED = f"hosting reads are limited to {SEAT_READS_PER_WINDOW} a seat a reserve window"


def budget_s(rt: Any) -> float:
    """One tick, in seconds, as the clock now declares it: the most any DigitalOcean
    read may take (rule 12). A clock amendment that shortens the tick shortens it."""
    interval = getattr(rt.tick_clock, "interval_ns", None) or rt.m.tick_interval_ns
    return interval / 1_000_000_000


def observe(rt: Any) -> None:
    """Book this reserve window's billing reading, within one tick."""
    rt.hosting.budget_s = budget_s(rt)
    rt.treasury.observe_hosting()


def tool_specs() -> dict[str, dict[str, Any]]:
    """The two reads a ``[hosting]`` world publishes, with examples their schemas accept."""
    limit = (f" At most {SEAT_READS_PER_WINDOW} hosting reads a seat a reserve window. "
             "Free.")
    tools = {
        "hosting.droplet": (
            "This world's DigitalOcean droplet: its size slug, vCPUs, memory, disk, "
            "status, region and monthly and hourly price; and the hosting pot: this "
            "droplet's booked burn by month, from DigitalOcean's invoice lines for it."
            + limit),
        "hosting.sizes": (
            "DigitalOcean's published droplet sizes available in this droplet's region: "
            "slug, vCPUs, memory, disk, monthly and hourly price." + limit),
    }
    return {
        tool_id: {
            "id": tool_id, "kind": KIND, "description": description,
            "args_schema": {"type": "object", "properties": {}, "required": [],
                            "additionalProperties": False, "examples": [{}]},
            "price_micro_per_call": 0,
        }
        for tool_id, description in tools.items()
    }


def install(rt: Any, client: Any | None = None, *, resuming: bool = False) -> None:
    """Open the hosting pot and publish its reads; a world without ``[hosting]`` is untouched.

    Guarantees a launch starts only on a verified droplet of the token's account
    (``world.hosting.verify``: raises ``HostingRefused`` with a named reason
    otherwise), and binds that account. A resume never asks DigitalOcean anything
    to start: the binding comes from the checkpoint, and the next billing reading
    checks it live and refuses to book on a mismatch. ``client`` is the
    DigitalOcean adapter at its HTTP boundary; without one the live adapter is
    built from ``DIGITALOCEAN_TOKEN``. Every read after start is journaled under
    ``hosting.<name>``, so a replay answers from the record.
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
    bound = None if resuming else verify(client, spec.droplet_id, budget_s(rt))
    account = HostingAccount(JournalProxy(client, rt.ledger, "hosting"),
                             droplet_id=spec.droplet_id, bound=bound, budget_s=budget_s(rt))
    rt.treasury.hosting = account
    rt.hosting = account
    rt.tool_specs.update(tool_specs())


def execute(rt: Any, action_id: str, handle: str, tool_id: str, args: dict,
            slot: str) -> dict[str, Any]:
    """Answer one hosting read with structured facts only, or a fixed refusal."""
    if args:
        return {"error": "hosting reads take no arguments"}
    account: HostingAccount = rt.hosting
    if not account.admit_read(action_id, rt.window.index):
        return {"error": READ_LIMITED}
    account.budget_s = budget_s(rt)
    try:
        # One journaled call, with its budget in seconds, under one deadline.
        if tool_id == "hosting.sizes":
            found = account.client.catalogue(account.droplet_id, account.budget_s)
            droplet, sizes = found["droplet"], found["sizes"]
        else:
            droplet = account.client.droplet(account.droplet_id, account.budget_s)
    except Exception:  # noqa: BLE001 - a read failure is a fact, not a crash
        return {"error": READ_FAILED}
    rt.ledger.append({"kind": "hosting.read", "handle": handle, "assembly_id": action_id,
                      "tool": tool_id, "ts": rt.clock.now_ns})
    if tool_id == "hosting.sizes":
        return {"region": droplet["region"], "current": droplet["size_slug"],
                "sizes": [{k: row[k] for k in ("slug", "vcpus", "memory_mb", "disk_gb",
                                               "price_monthly_usd", "price_hourly_usd")}
                          for row in sizes
                          if row["available"] and droplet["region"] in row["regions"]],
                "as_of_ns": rt.clock.now_ns}
    return {"droplet": droplet, "pot": account.view(), "as_of_ns": rt.clock.now_ns}
