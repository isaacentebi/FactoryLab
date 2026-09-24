"""The hosting pot and the droplet surface in a running world.

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
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean
from tests.helpers import collateral_decision
from tests.runtime.test_loop import _consequence_diary

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def world(fake=None, **spec):
    from factorylab.runtime.loop import Runtime

    fake = fake or FakeDigitalOcean()
    manifest = replace(load_manifest("scripted"), hosting=HostingSpec(
        enabled=True, droplet_id=fake.droplet_id,
        **{"max_monthly_micro": 24_000_000, **spec}))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.2, hosting_client=DigitalOceanClient(http=fake))
    rt._manage_reserve_window()
    return rt, fake


def resize(rt, handle, size, **args):
    return rt._run_tool("seed-decider", handle,
                        {"tool": "hosting.resize", "args": {"size": size, **args}})[0]


# --- the manifest ------------------------------------------------------------------------

def test_the_block_is_off_by_default_hashed_when_named_and_strict_about_its_keys():
    raw = tomllib.loads(ROOT.joinpath("worlds/scripted.toml").read_text())
    base = manifest_from_dict(raw)
    assert base.hosting == HostingSpec()
    off = manifest_from_dict({**raw, "hosting": {"enabled": False}})
    assert off.manifest_hash() == base.manifest_hash()
    capped = manifest_from_dict({**raw, "hosting": {"enabled": False, "max_monthly_usd": "24"}})
    assert capped.manifest_hash() != base.manifest_hash()
    on = manifest_from_dict({**raw, "hosting": {
        "enabled": True, "provider": "digitalocean", "droplet_id": 599960972,
        "max_monthly_usd": "24", "allow_disk_resize": False}})
    assert on.hosting.max_monthly_micro == 24_000_000 and on.hosting.droplet_id == 599960972
    for bad, match in (({"enabled": True, "token": "x"}, "unknown hosting"),
                       ({"enabled": True}, "needs hosting.droplet_id"),
                       ({"provider": "aws"}, "digitalocean"),
                       ({"max_monthly_usd": 24.5}, "exact USD"),
                       ({"max_monthly_usd": "24.0000001"}, "micro"),
                       ({"allow_disk_resize": "yes"}, "true or false"),
                       ({"droplet_id": -1}, "positive integer")):
        with pytest.raises(ValueError, match=match):
            manifest_from_dict({**raw, "hosting": bad})


def test_no_world_in_the_repository_enables_hosting():
    for path in sorted(ROOT.joinpath("worlds").glob("*.toml")):
        try:
            manifest = load_manifest(str(path))
        except ValueError as exc:
            assert "evaluator population" in str(exc), path.name
            continue
        assert manifest.hosting.enabled is False, path.name


def test_a_world_without_the_block_reads_nothing_and_publishes_nothing():
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import runtime_state

    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.2)
    assert rt.hosting is None and rt.treasury.hosting is None
    assert not any(tool.startswith("hosting.") for tool in rt.tool_specs)
    assert "hosting" not in rt.wallet.pots()
    assert "hosting" not in runtime_state(rt)["treasury"]


def test_an_enabled_world_without_a_token_does_not_start(monkeypatch):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.reasons import CredentialMissing

    monkeypatch.delenv(TOKEN_ENV)
    manifest = replace(load_manifest("scripted"),
                       hosting=HostingSpec(enabled=True, droplet_id=1))
    with pytest.raises(CredentialMissing):
        Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                router_gamma=0.2)


# --- the surface ---------------------------------------------------------------------------

def test_published_tools_state_what_they_do_and_carry_valid_examples():
    from factorylab.cortex.assembly import validate_schema

    rt, _ = world()
    specs = {k: v for k, v in rt.tool_specs.items() if k.startswith("hosting.")}
    assert set(specs) == {*hosting.READS, hosting.WRITE}
    for spec in specs.values():
        for example in spec["args_schema"]["examples"]:
            validate_schema(example, spec["args_schema"])
        text = spec["description"].lower()
        assert "free" in text and spec["price_micro_per_call"] == 0
        # A surface, never a suggestion (AGENTS.md: physics is enforced, not announced).
        assert not any(word in text for word in ("should", "profit", "opportunit", "edge",
                                                 "recommend", "consider", "worth", "better",
                                                 "cheap", "save"))
    assert "hosting.resize" in rt.CONSEQUENCE_WRITES


def test_the_size_list_is_published_as_facts_with_the_limits():
    rt, fake = world()
    handle = collateral_decision(rt)
    answer = rt._run_tool("seed-decider", handle, {"tool": "hosting.sizes", "args": {}})[0]
    assert answer["current"] == "s-1vcpu-2gb" and answer["region"] == "nyc3"
    assert answer["limits"] == {"max_monthly_usd": "24", "disk_resize_allowed": False}
    slugs = [row["slug"] for row in answer["sizes"]]
    assert "g-2vcpu-8gb" not in slugs  # not sold in this droplet's region
    assert {"slug": "s-4vcpu-8gb", "vcpus": 4, "memory_mb": 8192, "disk_gb": 160,
            "price_monthly_usd": "48", "price_hourly_usd": "0.07143",
            "description": "Basic"} in answer["sizes"]
    droplet = rt._run_tool("seed-decider", handle, {"tool": "hosting.droplet", "args": {}})[0]
    assert droplet["droplet"]["size_slug"] == "s-1vcpu-2gb"
    assert droplet["pot"]["custodian"] == "digitalocean"


def test_a_decisions_resize_is_its_act_journaled_once_and_the_bill_arrives_by_reading():
    rt, fake = world()
    hosting.tick(rt)  # the endowment reading
    handle = collateral_decision(rt)
    balance = rt.wallet.balance
    first = resize(rt, handle, "s-2vcpu-4gb")
    again = resize(rt, handle, "s-2vcpu-4gb")
    assert first["status"] == again["status"] == "in-progress"
    assert len(fake.posts) == 1
    diary_intents = [i for i in rt.hosting.intents.values()]
    assert diary_intents[0]["client_id"] == f"{handle}:tool:0"
    assert rt.wallet.balance == balance  # no synthetic debit from the price list
    fake.finish(first["action_id"])
    fake.bill("0.04")
    hosting.tick(rt)
    assert rt.hosting.intents[f"{handle}:tool:0"]["status"] == "completed"
    assert rt.wallet.balance == balance - 40_000
    kinds = [i["kind"] for i in _consequence_diary(rt)]
    assert kinds.index("hosting.intent") < kinds.index("hosting.acknowledged")
    assert "treasury.hosting_burn" in kinds and "hosting.resized" in kinds


@pytest.mark.parametrize(("size", "args", "reason"), [
    ("s-4vcpu-8gb", {}, "max_monthly_usd"),
    ("s-9vcpu-99gb", {}, "published size list"),
    ("s-2vcpu-4gb", {"disk": True}, "allow_disk_resize"),
])
def test_resizes_the_limits_do_not_admit_are_refused_before_any_intent(size, args, reason):
    rt, fake = world()
    result = resize(rt, collateral_decision(rt), size, **args)
    assert reason in result["error"] and fake.posts == [] and rt.hosting.intents == {}


def test_a_judge_cannot_resize_the_host():
    rt, fake = world()
    handle = collateral_decision(rt)
    rt.return_kinds[handle] = "Verdict"
    assert resize(rt, handle, "s-2vcpu-4gb")["error"] == rt.WRITE_REFUSAL
    assert fake.posts == [] and rt.hosting.intents == {}


def test_the_pot_is_a_custody_account_and_counted_in_the_pots():
    from factorylab.runtime.custody import custody_view

    rt, fake = world()
    hosting.tick(rt)
    view = custody_view(rt)["hosting_credit"]
    assert view["status"] == "observed" and view["balance_micro"] == 200_000_000
    assert view["generated_at"] == "2026-09-23T12:00:00Z"
    assert rt.wallet.pots()["hosting"] == 200_000_000
    fake.down = True
    hosting.tick(rt)
    assert custody_view(rt)["hosting_credit"]["status"] == "unavailable"


def test_the_pot_and_an_open_resize_survive_a_checkpoint():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt, fake = world()
    hosting.tick(rt)
    fake.bill("0.25")
    hosting.tick(rt)
    handle = collateral_decision(rt)
    resize(rt, handle, "s-2vcpu-4gb")
    state = runtime_state(rt)
    twin, _ = world(fake)
    restore_runtime(twin, state)
    assert twin.hosting.state() == rt.hosting.state()
    assert twin.treasury.hosting is twin.hosting
    assert TOKEN not in str(state)
