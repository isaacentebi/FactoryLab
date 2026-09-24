"""The host in a running world: its own pot, verified at start, and two structured reads.

A world that enables ``[hosting]`` runs on a DigitalOcean droplet whose prepaid
credit is its own pot (``Treasury.observe_hosting``, read once a reserve window;
world/hosting.py books what DigitalOcean reports it took, on that pot alone). The
population sees two free reads: the droplet, and DigitalOcean's size list with its
prices. Both publish structured facts only (slugs, counts, prices, times), never
DigitalOcean-authored prose, so no outside text reaches a wake that can write.
There is no write: a resize powers the droplet off, nothing here could power it
back on, and the factory runs on it.
"""

from __future__ import annotations

import os
from typing import Any

from factorylab.world.hosting import HostingAccount, verify

KIND = "hosting"
READS = ("hosting.droplet", "hosting.sizes")
#: A world's own DigitalOcean reads: one attempt, a short wait. A slow answer costs a
#: bounded pause, never a retry loop (AGENTS.md rule 12).
READ_TIMEOUT_S = 5
READ_FAILED = "hosting read unavailable"


def tool_specs() -> dict[str, dict[str, Any]]:
    """The two reads a ``[hosting]`` world publishes, with examples their schemas accept."""
    tools = {
        "hosting.droplet": (
            "This world's DigitalOcean droplet: its size slug, vCPUs, memory, disk, "
            "status, region and monthly and hourly price, and the hosting pot: "
            "DigitalOcean's credit remaining and month-to-date usage with their "
            "generated_at, and what the books took from them. Free."),
        "hosting.sizes": (
            "DigitalOcean's published droplet sizes available in this droplet's region: "
            "slug, vCPUs, memory, disk, monthly and hourly price. Free."),
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


def install(rt: Any, client: Any | None = None) -> None:
    """Verify the host and open its pot; a world without ``[hosting]`` is untouched.

    Guarantees an enabled world starts only on a verified, dedicated droplet
    (``world.hosting.verify``: raises ``HostingRefused`` with a named reason
    otherwise). ``client`` is the DigitalOcean adapter at its HTTP boundary;
    without one the live adapter is built from ``DIGITALOCEAN_TOKEN``. The
    verification reads are a precondition, like a credential check, and are not
    journaled; every later read is, under ``hosting.<name>``, so a replay answers
    from the record and never asks DigitalOcean twice.
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
        client = DigitalOceanClient(timeout_s=READ_TIMEOUT_S, attempts=1)
    identity = verify(client, spec.droplet_id)
    account = HostingAccount(JournalProxy(client, rt.ledger, "hosting"),
                             droplet_id=spec.droplet_id, identity=identity)
    rt.treasury.hosting = account
    rt.hosting = account
    rt.tool_specs.update(tool_specs())


def execute(rt: Any, action_id: str, handle: str, tool_id: str, args: dict,
            slot: str) -> dict[str, Any]:
    """Answer one hosting read with structured facts only, or a fixed refusal."""
    if args:
        return {"error": "hosting reads take no arguments"}
    account: HostingAccount = rt.hosting
    try:
        droplet = account.client.droplet(account.droplet_id)
        sizes = account.client.sizes() if tool_id == "hosting.sizes" else None
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
