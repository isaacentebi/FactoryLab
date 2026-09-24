"""The hosting pot and its two reads in a running world.

DigitalOcean is ``tests.digitalocean_fake`` at the HTTP boundary; nothing here reaches
the network.
"""

import tomllib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from factorylab.runtime import hosting
from factorylab.runtime.worlds import HostingSpec, load_manifest, manifest_from_dict
from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient
from factorylab.world.hosting import HostingRefused
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean
from tests.helpers import collateral_decision

ROOT = Path(__file__).parents[2]
#: A scripted world's launch clock is the wall clock (its own clock starts at zero), so
#: its fake host starts now.
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def manifest(fake):
    return replace(load_manifest("scripted"),
                   hosting=HostingSpec(enabled=True, droplet_id=fake.droplet_id))


def world(fake=None, **kwargs):
    from factorylab.runtime.loop import Runtime

    fake = fake or FakeDigitalOcean(start=NOW)
    rt = Runtime(manifest(fake), **{"events": 0, "seed": 1, "initial_balance_micro": None,
                                    "ledger_path": None, "router_gamma": 0.2, **kwargs},
                 hosting_client=DigitalOceanClient(http=fake))
    return rt, fake


def read(rt, handle, tool, seat="seed-decider"):
    return rt._run_tool(seat, handle, {"tool": tool, "args": {}})[0]


# --- the manifest ------------------------------------------------------------------------

def test_the_block_is_off_by_default_hashed_when_named_and_strict_about_its_keys():
    raw = tomllib.loads(ROOT.joinpath("worlds/scripted.toml").read_text())
    base = manifest_from_dict(raw)
    assert base.hosting == HostingSpec()
    off = manifest_from_dict({**raw, "hosting": {"enabled": False}})
    assert off.manifest_hash() == base.manifest_hash()
    named = manifest_from_dict({**raw, "hosting": {"enabled": False, "droplet_id": 7}})
    assert named.manifest_hash() != base.manifest_hash()
    on = manifest_from_dict({**raw, "hosting": {
        "enabled": True, "provider": "digitalocean", "droplet_id": 599960972}})
    assert on.hosting.droplet_id == 599960972
    for bad, match in (({"enabled": True, "max_monthly_usd": "24"}, "unknown hosting"),
                       ({"allow_disk_resize": False}, "unknown hosting"),
                       ({"enabled": True}, "needs hosting.droplet_id"),
                       ({"provider": "aws"}, "digitalocean"),
                       ({"droplet_id": -1}, "positive integer"),
                       ({"droplet_id": True}, "positive integer")):
        with pytest.raises(ValueError, match=match):
            manifest_from_dict({**raw, "hosting": bad})


def test_no_world_in_the_repository_enables_hosting():
    for path in sorted(ROOT.joinpath("worlds").glob("*.toml")):
        try:
            loaded = load_manifest(str(path))
        except ValueError as exc:
            assert "evaluator population" in str(exc), path.name
            continue
        assert loaded.hosting.enabled is False, path.name


def test_a_world_without_the_block_reads_nothing_and_publishes_nothing():
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import runtime_state

    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.2)
    assert rt.hosting is None and rt.treasury.hosting is None
    assert not any(tool.startswith("hosting.") for tool in rt.tool_specs)
    assert "hosting" not in rt.wallet.pots()
    assert "hosting" not in runtime_state(rt)["treasury"]


def test_a_launch_needs_a_token_and_its_droplet(monkeypatch):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.reasons import CredentialMissing

    fake = FakeDigitalOcean(start=NOW)
    fake.on_droplet = False   # a test, a laptop: the metadata service is not there
    with pytest.raises(HostingRefused) as refused:
        world(fake)
    assert refused.value.reason == HostingRefused.NOT_ON_DROPLET
    monkeypatch.delenv(TOKEN_ENV)
    with pytest.raises(CredentialMissing):
        Runtime(manifest(fake), events=0, seed=1, initial_balance_micro=None,
                ledger_path=None, router_gamma=0.2)


def test_an_account_with_other_resources_launches_and_books_only_its_droplet():
    fake = FakeDigitalOcean(start=NOW)
    fake.add("888", "Droplets", "0.07143", "another-host", since=NOW - timedelta(days=1))
    rt, _ = world(fake)
    for _ in range(3):
        hosting.observe(rt)
        fake.advance(24)
    # Two days of this droplet, and none of the other's.
    assert 0 < rt.hosting.burned_micro() <= 3 * 24 * 17_860


# --- the surface ---------------------------------------------------------------------------

def test_two_free_reads_and_no_write():
    from factorylab.cortex.assembly import validate_schema

    rt, _ = world()
    specs = {k: v for k, v in rt.tool_specs.items() if k.startswith("hosting.")}
    assert set(specs) == set(hosting.READS) == {"hosting.droplet", "hosting.sizes"}
    for spec in specs.values():
        for example in spec["args_schema"]["examples"]:
            validate_schema(example, spec["args_schema"])
        text = spec["description"].lower()
        assert "free" in text and spec["price_micro_per_call"] == 0
        # A surface, never a suggestion (AGENTS.md: physics is enforced, not announced).
        assert not any(word in text for word in ("should", "profit", "opportunit", "edge",
                                                 "recommend", "consider", "worth", "better",
                                                 "cheap", "save"))
    assert not any(tool.startswith("hosting.") for tool in rt.CONSEQUENCE_WRITES)
    assert all(rt._read_only_call(tool) for tool in specs)


def test_seat_reads_answer_from_the_windows_billing_read_and_never_the_network():
    rt, fake = world()
    handle = collateral_decision(rt)
    assert read(rt, handle, "hosting.droplet") == {"error": hosting.NOT_YET_READ}
    hosting.observe(rt)
    asked = len(fake.calls)
    sizes = read(rt, handle, "hosting.sizes")
    droplet = read(rt, handle, "hosting.droplet")
    for _ in range(20):
        read(rt, handle, "hosting.droplet", seat="seed-observer")
    assert len(fake.calls) == asked                  # no seat read reached DigitalOcean
    assert sizes["current"] == "s-1vcpu-2gb" and sizes["region"] == "nyc3"
    assert "g-2vcpu-8gb" not in [row["slug"] for row in sizes["sizes"]]  # not sold here
    assert {"slug": "s-4vcpu-8gb", "vcpus": 4, "memory_mb": 8192, "disk_gb": 160,
            "price_monthly_usd": "48", "price_hourly_usd": "0.07143"} in sizes["sizes"]
    assert droplet["droplet"]["size_slug"] == "s-1vcpu-2gb"
    assert droplet["pot"]["balance"] == "unknown"
    # The fake's descriptions carry an instruction; no read passes any of it on, and
    # nothing about the rest of the account reaches a seat.
    assert "Ignore" not in str(sizes) + str(droplet)
    assert "account_entries" not in str(droplet)


def test_the_billing_read_has_a_third_of_a_tick():
    rt, fake = world()
    tick = rt.tick_clock.interval_ns / 1e9
    assert hosting.budget_s(rt) == tick / rt.m.timing.min_ratio
    hosting.observe(rt)
    assert rt.hosting.budget_s == tick / rt.m.timing.min_ratio
    assert max(c["timeout"] for c in fake.calls) <= tick / rt.m.timing.min_ratio


def test_a_host_without_a_bounded_lookup_refuses_to_start(monkeypatch):
    from factorylab.runtime.loop import Runtime
    from factorylab.world import digitalocean

    fake = FakeDigitalOcean(start=NOW)
    monkeypatch.setattr(digitalocean, "lookup_available", lambda: False)
    with pytest.raises(HostingRefused) as refused:
        Runtime(manifest(fake), events=0, seed=1, initial_balance_micro=None,
                ledger_path=None, router_gamma=0.2)   # the live adapter, no fake injected
    assert refused.value.reason == HostingRefused.NO_LOOKUP


def test_the_pot_is_its_own_custody_account_with_no_balance():
    from factorylab.runtime.custody import custody_view

    rt, fake = world()
    for _ in range(3):
        hosting.observe(rt)
        fake.advance(24)
    view = custody_view(rt)["hosting_credit"]
    assert view["status"] == "unavailable" and "account-wide" in view["reason"]
    assert view["burned_micro"] == rt.hosting.burned_micro() > 0


# --- a running world ---------------------------------------------------------------------------

def _billing_reads(fake):
    return sum(c["url"].endswith("/v2/customers/my/invoices/preview?per_page=200&page=1")
               for c in fake.calls)


@pytest.mark.gate
def test_billing_is_read_once_a_window_and_a_replayed_burn_is_booked_once(tmp_path):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import resume_runtime
    from tests.runtime.test_resume import stop_after

    fake = FakeDigitalOcean(start=NOW)
    fake.stall = lambda url, timeout: (fake.advance(8) if "invoices/preview" in url
                                       else None)   # the host accrues while the world runs
    path = tmp_path / "hosting.jsonl"
    rt = Runtime(manifest(fake), events=200, seed=1, initial_balance_micro=None,
                 ledger_path=str(path), router_gamma=0.1,
                 hosting_client=DigitalOceanClient(http=fake))
    windows = []
    open_window = rt._manage_reserve_window

    def counted():
        before = rt.reserve_window_start
        open_window()
        if rt.reserve_window_start != before:
            windows.append(rt.reserve_window_start)

    rt._manage_reserve_window = counted
    stop_after(rt, lambda r, e: len(windows) >= 3 and str(e.kind) == "Tick")
    reads = _billing_reads(fake)
    assert reads == len(windows) >= 3 and rt.ticks_consumed > reads   # never per tick
    booked = rt.hosting.burn_by_month()
    assert rt.hosting.burned_micro() > 0
    items = rt.ledger.ledger._recovery_items()
    assert not any(i["kind"] == "wallet.settle" for i in items)
    # A resume never waits on DigitalOcean: it is down, and the world comes back anyway.
    fake.down = True
    restored = resume_runtime(rt.m, str(path), hosting_client=DigitalOceanClient(http=fake))
    assert _billing_reads(fake) == reads           # the replay answered from the record
    assert restored.hosting.burn_by_month() == booked
    replayed = [i for i in restored.ledger.ledger._recovery_items()
                if i["kind"] == "treasury.hosting_burn"]
    assert sum(i["micro"] for i in replayed) == sum(booked.values())
    assert restored.hosting.bound == rt.hosting.bound
    # The next live reading checks the binding: another account's token books nothing.
    fake.down = False
    fake.user_uuid = "00000000-0000-4000-8000-000000000bad"
    assert restored.treasury.observe_hosting() is None
    assert restored.hosting.unread == HostingRefused.ACCOUNT_MISMATCH
    assert restored.hosting.burn_by_month() == booked


def test_the_deploy_covers_the_digitalocean_key():
    deploy = ROOT / "deploy"
    static = (deploy / "factorylab-static.service").read_text()
    assert "-/srv/factorylab/digitalocean.key" in static.split("InaccessiblePaths=")[1]
    backup = (deploy / "backup.sh").read_text()
    assert "'digitalocean.key'" in backup and "members+=(digitalocean.key)" in backup
    readme = (deploy / "README.md").read_text()
    assert "digitalocean.key" in readme
    assert all(scope in readme for scope in ("billing:read", "droplet:read", "account:read",
                                             "sizes:read"))
    assert "read-only" in (ROOT / "docs/manifest.md").read_text().split("## The host")[1]
