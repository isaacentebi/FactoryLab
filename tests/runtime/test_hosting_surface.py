"""The hosting pot and its two reads in a running world.

DigitalOcean is ``tests.digitalocean_fake`` at the HTTP boundary; nothing here reaches
the network.
"""

import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from factorylab.runtime import hosting
from factorylab.runtime.worlds import HostingSpec, load_manifest, manifest_from_dict
from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient
from factorylab.world.hosting import HostingRefused
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean
from tests.helpers import collateral_decision

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def manifest(fake):
    return replace(load_manifest("scripted"),
                   hosting=HostingSpec(enabled=True, droplet_id=fake.droplet_id))


def client(fake):
    return DigitalOceanClient(http=fake, attempts=1, timeout_s=hosting.READ_TIMEOUT_S)


def world(fake=None, **kwargs):
    from factorylab.runtime.loop import Runtime

    fake = fake or FakeDigitalOcean()
    rt = Runtime(manifest(fake), **{"events": 0, "seed": 1, "initial_balance_micro": None,
                                    "ledger_path": None, "router_gamma": 0.2, **kwargs},
                 hosting_client=client(fake))
    return rt, fake


def read(rt, handle, tool):
    return rt._run_tool("seed-decider", handle, {"tool": tool, "args": {}})[0]


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


def test_an_enabled_world_does_not_start_without_a_token_or_off_a_droplet(monkeypatch):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.reasons import CredentialMissing

    fake = FakeDigitalOcean()
    fake.on_droplet = False  # a test, a laptop: the metadata service is not there
    with pytest.raises(HostingRefused) as refused:
        world(fake)
    assert refused.value.reason == HostingRefused.NOT_ON_DROPLET
    monkeypatch.delenv(TOKEN_ENV)
    with pytest.raises(CredentialMissing):
        Runtime(manifest(fake), events=0, seed=1, initial_balance_micro=None,
                ledger_path=None, router_gamma=0.2)


def test_an_account_paying_for_anything_else_does_not_start():
    fake = FakeDigitalOcean()
    fake.snapshots = 1
    with pytest.raises(HostingRefused) as refused:
        world(fake)
    assert refused.value.reason == HostingRefused.NOT_DEDICATED


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


def test_the_reads_publish_structured_facts_and_no_provider_prose():
    rt, fake = world()
    handle = collateral_decision(rt)
    sizes = read(rt, handle, "hosting.sizes")
    assert sizes["current"] == "s-1vcpu-2gb" and sizes["region"] == "nyc3"
    assert "g-2vcpu-8gb" not in [row["slug"] for row in sizes["sizes"]]  # not sold here
    assert {"slug": "s-4vcpu-8gb", "vcpus": 4, "memory_mb": 8192, "disk_gb": 160,
            "price_monthly_usd": "48", "price_hourly_usd": "0.07143"} in sizes["sizes"]
    droplet = read(rt, handle, "hosting.droplet")
    assert droplet["droplet"]["size_slug"] == "s-1vcpu-2gb"
    assert droplet["pot"]["custodian"] == "digitalocean"
    # The fake's descriptions carry an instruction; no read passes any of it on.
    assert "Ignore" not in str(sizes) + str(droplet)
    fake.down = True
    assert read(rt, handle, "hosting.sizes") == {"error": hosting.READ_FAILED}


def test_the_pot_is_its_own_custody_account():
    from factorylab.runtime.custody import custody_view

    rt, fake = world()
    rt.treasury.observe_hosting()
    view = custody_view(rt)["hosting_credit"]
    assert view["status"] == "observed" and view["balance_micro"] == 200_000_000
    assert view["generated_at"] == "2026-09-23T12:00:00Z" and view["discrepancy_micro"] == 0
    fake.down = True
    rt.treasury.observe_hosting()
    unread = custody_view(rt)["hosting_credit"]
    assert unread["status"] == "unavailable" and unread["reason"] == "billing read failed"


def test_the_pot_survives_a_checkpoint_and_another_account_cannot_restore_it():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt, fake = world()
    rt.treasury.observe_hosting()
    fake.bill("0.25")
    rt.treasury.observe_hosting()
    state = runtime_state(rt)
    twin, _ = world(fake)
    restore_runtime(twin, state)
    assert twin.hosting.state() == rt.hosting.state()
    assert TOKEN not in str(state)
    stranger, _ = world(FakeDigitalOcean(team_uuid="aaaaaaaa-0000-4000-8000-000000000001"))
    with pytest.raises(HostingRefused) as refused:
        restore_runtime(stranger, runtime_state(rt))
    assert refused.value.reason == HostingRefused.ACCOUNT_MISMATCH


# --- a running world ---------------------------------------------------------------------------

def _billing_reads(fake):
    return sum(c["url"].endswith("/v2/customers/my/balance") for c in fake.calls)


@pytest.mark.gate
def test_billing_is_read_once_a_reserve_window_and_a_replayed_burn_is_booked_once(tmp_path):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import resume_runtime
    from tests.runtime.test_resume import stop_after

    fake = FakeDigitalOcean()
    fake.accrue_per_read = "0.10"
    path = tmp_path / "hosting.jsonl"
    rt = Runtime(manifest(fake), events=200, seed=1, initial_balance_micro=None,
                 ledger_path=str(path), router_gamma=0.1, hosting_client=client(fake))
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
    assert reads == len(windows) >= 3                  # once a window, never per tick
    assert rt.ticks_consumed > reads
    booked = rt.hosting.burned_micro
    burns = [i for i in rt.ledger.ledger._recovery_items()
             if i["kind"] == "treasury.hosting_burn"]
    assert booked == sum(i["micro"] for i in burns) > 0
    # The host's burn never moved the model wallet.
    assert not any(i["kind"] == "wallet.settle" for i in rt.ledger.ledger._recovery_items())
    restored = resume_runtime(rt.m, str(path), hosting_client=client(fake))
    # The replay answered from the record: DigitalOcean was not asked again, and the
    # burn it books is the burn the crashed process booked, once.
    assert _billing_reads(fake) == reads
    assert restored.hosting.burned_micro == booked
    replayed = [i for i in restored.ledger.ledger._recovery_items()
                if i["kind"] == "treasury.hosting_burn"]
    assert sum(i["micro"] for i in replayed) == booked
    # And it is what DigitalOcean took after the endowment reading, which itself
    # followed one accrual.
    assert booked == (reads - 1) * 100_000 == int(fake.taken * 1_000_000) - 100_000
