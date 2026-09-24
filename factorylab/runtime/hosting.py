"""The host in a running world: its own pot, booked from its invoices, and two reads.

A world that enables ``[hosting]`` runs on a DigitalOcean droplet. What the droplet
costs is booked from DigitalOcean's own invoice lines for it, once a reserve window
(``Treasury.observe_hosting``; world/hosting.py). That one read also fetches the
droplet and DigitalOcean's size list, and the population's two free reads,
``hosting.droplet`` and ``hosting.sizes``, answer from it: a seat's read never
reaches the network. Both publish structured facts only (slugs, counts, prices,
times), never DigitalOcean-authored prose, and nothing about the rest of the account.
There is no write: a resize powers the droplet off, nothing here could power it back
on, and the factory runs on it.

The billing read runs inside event processing, at a reserve window's boundary, under
one monotonic deadline of a tick divided by ``[timing] min_ratio`` (AGENTS.md rule
12: an inner loop settles at least that many times faster than the loop that
commands it), name lookup included. It can delay the event it runs in, and so the
world's next event, by at most that; a slow DigitalOcean leaves the pot where it was
until the next window.
"""

from __future__ import annotations

import os
import time
from typing import Any

from factorylab.world.hosting import HostingAccount, HostingRefused, verify_launch

KIND = "hosting"
READS = ("hosting.droplet", "hosting.sizes")
NOT_YET_READ = "hosting not read yet"
SIZES_UNREAD = "the size catalogue could not be read at the last billing read"


def budget_s(rt: Any) -> float:
    """The billing read's whole budget: the tick the clock now declares, over min_ratio."""
    interval = getattr(rt.tick_clock, "interval_ns", None) or rt.m.tick_interval_ns
    return interval / 1_000_000_000 / max(1, rt.m.timing.min_ratio)


def observe(rt: Any) -> None:
    """Book this reserve window's billing reading, within its budget."""
    rt.hosting.budget_s = budget_s(rt)
    rt.treasury.observe_hosting()


def tool_specs() -> dict[str, dict[str, Any]]:
    """The two reads a ``[hosting]`` world publishes, with examples their schemas accept."""
    tools = {
        "hosting.droplet": (
            "This world's DigitalOcean droplet as of the last billing read: its size "
            "slug, vCPUs, memory, disk, status, region and monthly and hourly price; and "
            "the hosting pot: this droplet's booked burn by month, from DigitalOcean's "
            "invoice lines for it. Free."),
        "hosting.sizes": (
            "DigitalOcean's published droplet sizes available in this droplet's region, "
            "as of the last billing read: slug, vCPUs, memory, disk, monthly and hourly "
            "price. Free."),
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
    otherwise), binds that account, and anchors the launch month to the world's
    launch clock. A resume asks DigitalOcean nothing to start: the binding and the
    launch time come from the checkpoint, and the next billing reading checks the
    binding live. Without an injected ``client`` the live adapter is built from
    ``DIGITALOCEAN_TOKEN``, and a host with no bounded name lookup (``getent``)
    refuses to start, launch or resume.
    """
    spec = rt.m.hosting
    if not spec.enabled:
        return
    from factorylab.runtime.resume import JournalProxy
    from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient, lookup_available

    if client is None:
        if not os.environ.get(TOKEN_ENV):
            from factorylab.runtime.reasons import CredentialMissing

            raise CredentialMissing(f"[hosting] needs {TOKEN_ENV}")
        if not lookup_available():
            raise HostingRefused(HostingRefused.NO_LOOKUP)
        client = DigitalOceanClient()
    bound = launch_ns = price = None
    if not resuming:
        bound, price = verify_launch(client, spec.droplet_id, budget_s(rt))
        # The launch clock: a live world's clock is the wall clock; a scripted world's
        # starts at zero, and its host bills in real time.
        launch_ns = rt.clock.now_ns if rt.live else time.time_ns()
    account = HostingAccount(JournalProxy(client, rt.ledger, "hosting"),
                             droplet_id=spec.droplet_id, bound=bound, launch_ns=launch_ns,
                             budget_s=budget_s(rt), launch_price=price)
    rt.treasury.hosting = account
    rt.hosting = account
    rt.tool_specs.update(tool_specs())


def execute(rt: Any, action_id: str, handle: str, tool_id: str, args: dict,
            slot: str) -> dict[str, Any]:
    """Answer one hosting read from the last billing read; never from the network."""
    if args:
        return {"error": "hosting reads take no arguments"}
    account: HostingAccount = rt.hosting
    droplet = account.snapshot.get("droplet")
    if droplet is None:
        return {"error": NOT_YET_READ}
    if tool_id == "hosting.sizes":
        if account.snapshot.get("sizes") is None:
            # The catalogue could not be read at the last billing read: an older one is
            # not served as current.
            return {"error": SIZES_UNREAD}
        return {"region": droplet["region"], "current": droplet["size_slug"],
                "sizes": [{k: row[k] for k in ("slug", "vcpus", "memory_mb", "disk_gb",
                                               "price_monthly_usd", "price_hourly_usd")}
                          for row in account.snapshot.get("sizes", [])
                          if row["available"] and droplet["region"] in row["regions"]]}
    return {"droplet": droplet, "pot": account.view()}
