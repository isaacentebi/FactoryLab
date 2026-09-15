"""Group H: scripted registrations, wallet sizing and demonstration cadence."""

import json
from decimal import Decimal

import pytest

from factorylab.runtime.propensity import action_label
from factorylab.runtime.resume import RecoveryJournal
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider


def _tick(wallet="10", venue="1000", mid="100", index=1):
    # Edition 3 (C4): the scripted producer sizes from the trading equity in its own
    # YOU block, not from a root wallet figure the world block no longer carries.
    return {"seat": {"world_resources": {"trading_equity_usd": wallet}},
            "payload": {"account": {"equity_usd": venue},
                        "mids": {"BTC": mid}, "index": index}}


@pytest.mark.parametrize("leverage", ["1", "3"])
def test_sizes_from_world_wallet(leverage):
    provider = ScriptedProvider(leverage=leverage)
    reply = provider._produce("event Tick", _tick())
    assert Decimal(reply["size"]) * 100 == Decimal("8") * Decimal(leverage)
    assert reply == ScriptedProvider(leverage=leverage)._produce(
        "event Tick", _tick(venue="999999"))


def test_missing_wallet_does_not_fall_back_to_venue_equity():
    inputs = _tick()
    del inputs["seat"]
    assert ScriptedProvider()._produce("event Tick", inputs)["action"] == "hold"


def test_registers_observation_then_names_it_in_amendment():
    provider = ScriptedProvider()
    proposals = [(n, p) for n in range(1, provider.late_amendment_call + 1)
                 for p in provider._produce("event MarketMid", {}).get("register", [])]
    observations = [(n, p) for n, p in proposals if p["kind"] == "observation"]
    assert len(observations) == 1
    call, observation = observations[0]
    assert any(n > call and p["kind"] == "amendment"
               and any(c["observation"] == observation["id"] for c in p["add"])
               for n, p in proposals)


@pytest.mark.parametrize("size", ["0.001", "0.01", "0.1", "1", "10"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_learner_covers_every_scripted_size_band(size, side):
    provider = ScriptedProvider()
    proposals = [p for _ in range(40)
                 for p in provider._produce("event MarketMid", {}).get("register", [])]
    learner = next((p for p in proposals if p["kind"] == "learner"), None)
    assert learner is not None
    assert learner["assembly_id"] == "seed-decider"
    assert "hold" in learner["actions"]
    reply = ScriptedProvider()._produce("event Tick", _tick(
        wallet=str(Decimal(size) * 100), mid="240", index=1 if side == "buy" else 3))
    assert reply["side"] == side and Decimal(reply["size"]) == Decimal(size)
    label = action_label("producer", reply, "ok")
    assert label in learner["actions"]


@pytest.mark.parametrize("world", ["scripted", "scripted-crash"])
def test_demo_backstop_allows_two_boundaries_inside_500(world):
    manifest = load_manifest(world)
    assert 2 * manifest.timing.min_ratio * manifest.evaluation.consequence_backstop_events < 500


def test_readme_500_event_acceptance(monkeypatch, capsys):
    from factorylab.runtime.loop import run_world

    entries = []
    append = RecoveryJournal.append

    def capture(journal, item):
        seq = append(journal, item)
        if item["kind"] in {
            "charter.activate", "charter.cadence", "assembly.retired",
            "propensity.learned", "propensity.unlearned", "observation.preflight",
            "registration.rejected",
        }:
            entries.append({**item, "seq": seq})
        return seq

    monkeypatch.setattr(RecoveryJournal, "append", capture)
    summary = run_world(load_manifest("scripted"), events=500, seed=1)
    activations = [i for i in entries if i["kind"] == "charter.activate"]
    retirements = [i for i in entries if i["kind"] == "assembly.retired"]
    with capsys.disabled():
        print(json.dumps({"stats": summary["stats"], "activations": activations,
                          "retirements": retirements,
                          "cadence": [i for i in entries if i["kind"] == "charter.cadence"]},
                         sort_keys=True))
    assert not summary["terminated"]
    assert summary["ledger_verify"] and summary["wallet_conservation"]
    # C10: the seeded trader spends its own entitlement on its calls and its trading
    # losses and runs it below one fake-opus call's ceiling before the run is out, so
    # the world makes fewer producer calls than before and the scripted population
    # observation, placed past the script's 1,600th producer call, is not reached
    # inside 500 events. The README says so; the provider's own schedule is pinned by
    # test_registers_observation_then_names_it_in_amendment.
    assert summary["stats"]["observations_registered"] == 0
    assert summary["stats"]["assembly_learners_registered"] >= 1
    assert activations
    assert any(i["assembly_id"] == "eval-a" for i in retirements)
    assert any(i["kind"] == "propensity.learned" for i in entries)
    assert not any(i["kind"] == "propensity.unlearned"
                   and i.get("assembly_id") == "seed-decider" for i in entries)
