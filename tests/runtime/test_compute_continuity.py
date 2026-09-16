"""A funded Venice population survives loss of its OpenRouter endowment."""

from dataclasses import replace
from pathlib import Path

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.market import MultiProvider
from factorylab.world.openrouter import OpenRouterError
from factorylab.world.scripted import ScriptedProvider, _inputs_from_prompt
from scripts.rehearsal import voted_charter

ROSTER = "worlds/compute-continuity-roster.toml"


class CreditProvider:
    """Expose a controlled provider-credit loss without spending real account funds."""

    def __init__(self, name, calls, *, available=True, exhaust_after=None, race=False):
        self.name, self.calls = name, calls
        self.available, self.exhaust_after, self.race = available, exhaust_after, race
        self.completed = 0
        self.scripted = ScriptedProvider(register_at_calls=(), tool_at_calls=(),
                                         treasury_at_call=-1, router_add_at_call=-1)

    def balance_micro(self):
        return 1_000_000 if self.available else 0

    def complete(self, request):
        assert self.available, "an exhausted provider was dispatched"
        if self.race:
            self.available = False
            self.calls.append((self.name, "402", None))
            raise OpenRouterError(402, "credit exhausted")
        inputs = _inputs_from_prompt(str(request.messages[0]["content"]))
        self.calls.append((self.name, request.model_id, inputs.get("you")))
        result = replace(self.scripted.complete(request), cost_micro=1)
        self.completed += 1
        if self.completed == self.exhaust_after:
            self.available = False
        return result


def runtime(openrouter, venice, *, path=None, roster=ROSTER, events=12):
    m = load_manifest(roster)
    m = replace(m, exchange=replace(m.exchange, kind="fake"),
                treasury=replace(m.treasury, insolvency_events=3))
    provider = MultiProvider(openrouter=openrouter, venice=venice)
    return Runtime(m, events=events, seed=4, initial_balance_micro=None,
                   ledger_path=str(path) if path else None, drip=False,
                   router_gamma=.1, provider=provider)


@pytest.mark.parametrize("mode", ["empty_at_launch", "exhaust_after_two", "402_race"])
def test_population_continues_on_venice_after_openrouter_loss(mode, monkeypatch):
    from urllib.request import OpenerDirector

    monkeypatch.setattr(OpenerDirector, "open", lambda *a, **k: pytest.fail("network"))
    calls = []
    first = CreditProvider("openrouter", calls, available=mode != "empty_at_launch",
                           exhaust_after=2 if mode == "exhaust_after_two" else None,
                           race=mode == "402_race")
    second = CreditProvider("venice", calls)
    # Restated for R3-D: a tier's separation is a duration rather than three arrivals
    # (GPT-6 third reading §6.C), so the world has to run long enough for one of those
    # durations to pass before a meta is asked anything at all. Twenty-four ticks is
    # the same short world, given the time the cascade now takes. What this asserts is
    # unchanged: after the first provider is lost the population goes on doing most of
    # its work on the second, and the verdicts, conformities and settled forecasts
    # below say the work was real.
    rt = runtime(first, second, events=24)
    result = rt.run()
    assert not result["terminated"]
    assert result["ledger_verify"] and result["wallet_conservation"]
    assert rt.ticks_consumed == 24
    assert second.completed > 10
    assert not first.available
    if mode == "empty_at_launch":
        assert first.completed == 0
        assert all(name == "venice" for name, _, _ in calls)
    else:
        last = max(i for i, call in enumerate(calls) if call[0] == "openrouter")
        assert len(calls[last + 1:]) > 10
        assert all(call[0] == "venice" for call in calls[last + 1:])
    assert result["stats"]["verdicts"] > 0
    # Restated for R3-D: this counted conformity settlements, and in this short world
    # they came from judgements of returns that had committed to nothing — which now
    # conclude "unmeasured" instead of being scored (GPT-6 third reading §6.B). What
    # matters here is that evaluation ran on the second provider and closed what it
    # opened, by either answer.
    closed = [i for i in rt.ledger._recovery_items() if i["kind"] == "evaluation.unmeasured"]
    assert result["stats"]["conformities"] > 0 or closed
    assert result["stats"]["forecasts_settled"] > 0


def test_unseeded_venice_cannot_rescue_an_empty_openrouter_population():
    calls = []
    rt = runtime(CreditProvider("openrouter", calls, available=False),
                 CreditProvider("venice", calls), roster="worlds/testnet-10m-roster.toml")
    result = rt.run()
    assert result["terminated"]
    assert calls == []


def test_both_providers_empty_ends_without_dispatch_or_billing():
    calls = []
    rt = runtime(CreditProvider("openrouter", calls, available=False),
                 CreditProvider("venice", calls, available=False))
    result = rt.run()
    assert result["terminated"] and result["seal_key_released"]
    assert result["ledger_verify"] and result["wallet_conservation"]
    assert calls == []


def test_resume_after_openrouter_loss_keeps_venice_available(tmp_path):
    calls = []
    first = CreditProvider("openrouter", calls, exhaust_after=2)
    second = CreditProvider("venice", calls)
    path = tmp_path / "continuity.jsonl"
    rt = runtime(first, second, path=path)
    original = rt._process_event

    class Cut(BaseException):
        pass

    def cut(event):
        result = original(event)
        if not first.available and rt.ticks_consumed >= 4:
            raise Cut
        return result

    rt._process_event = cut
    with pytest.raises(Cut):
        rt.run()
    assert not first.available
    before = len(calls)
    result = resume_world(rt.m, str(path), provider=MultiProvider(openrouter=first, venice=second))
    assert not result["terminated"]
    assert result["stats"]["resumes"] == 1
    assert result["ledger_verify"] and result["wallet_conservation"]
    assert len(calls) > before
    assert all(call[0] == "venice" for call in calls[before:])


def test_candidate_covers_all_seed_roles_and_cannot_reuse_old_ratification():
    m = load_manifest(ROSTER)
    assert {a.role for a in m.assemblies if a.model_id.startswith("venice:")} == {
        "producer", "evaluator", "meta", "antagonist",
    }
    assert len([a for a in m.assemblies if a.role == "evaluator"
                and a.model_id.startswith("venice:")]) >= 2
    with pytest.raises(ValueError, match="roster differs"):
        voted_charter(Path("docs/charter/edition1-short-ratified.toml"), m)


def test_compute_supply_is_public_interface_information_not_assembly_model_identity():
    calls = []
    rt = runtime(CreditProvider("openrouter", calls), CreditProvider("venice", calls))
    world = rt._world_block()
    assert "to_venice" in world["compute_supply"]["venice"]
    assert "catalogue.search" in world["compute_supply"]["discovery"]
    assert all("model_id" not in a for a in world["catalogue"])
