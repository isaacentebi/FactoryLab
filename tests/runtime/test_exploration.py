"""The exploration niche: the kernel, not the seat, draws some decisions' action class.

Essay II.II.b: learning death is prevented "as a fact about the world" — a share
of the factory's decisions is reserved for exploration. A seat that declares
"order: 0.1" and then holds every time never explores; a kernel draw does, and
records its own distribution as the decision's propensity, so the learners are
trained on a probability that was actually sampled.
"""

from dataclasses import replace

import pytest

from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_loop import _consequence_produce, _consequence_runtime
from tests.runtime.test_order_journey import Scripted, _exchange


def _manifest(share):
    m = load_manifest("scripted")
    return replace(m, evaluation=replace(m.evaluation, exploration_share=share))


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i.get("kind") == kind]


def test_no_share_no_draw_and_the_manifest_hash_is_unchanged():
    base = load_manifest("scripted")
    assert base.evaluation.exploration_share == 0.0
    assert _manifest(0.0).manifest_hash() == base.manifest_hash()
    provider = Scripted({"action": "hold"})
    rt = _consequence_runtime(provider=provider, exchange=_exchange(), manifest=_manifest(0.0))
    _consequence_produce(rt)
    assert not _items(rt, "exploration.draw")
    assert "EXPLORATION DRAW" not in provider.requests[0].messages[-1]["content"]


class _Rigged:
    """The runtime's generator, pinned: every decision is drawn, always ``order``."""

    def random(self):
        return 0.0

    def randrange(self, n):
        return 0


def test_a_complied_draw_records_the_kernels_propensity():
    provider = Scripted({"action": "order", "coin": "BTC", "side": "buy", "size": "0.001",
                         "rationale": "the draw asked for an order; testing fill quality"})
    rt = _consequence_runtime(provider=provider, exchange=_exchange(), manifest=_manifest(1.0))
    rt.rng = _Rigged()
    handle, _ = _consequence_produce(rt)
    assert _items(rt, "exploration.taken")[0]["complied"] is True
    declared = rt.queue.declared_propensity(handle)
    mass = dict(zip(declared.action_ids, declared.probs, strict=True))
    assert mass == pytest.approx({"order": 0.25, "investigate": 0.25, "build": 0.25,
                                  "govern": 0.25})


def test_a_defied_draw_keeps_the_seats_own_declaration():
    provider = Scripted({"action": "hold", "propensity": {"hold": 0.9, "order": 0.1}})
    rt = _consequence_runtime(provider=provider, exchange=_exchange(), manifest=_manifest(1.0))
    rt.rng = _Rigged()
    handle, _ = _consequence_produce(rt)
    assert _items(rt, "exploration.taken")[0]["complied"] is False
    declared = rt.queue.declared_propensity(handle)
    assert dict(zip(declared.action_ids, declared.probs, strict=True))["hold"] == \
        pytest.approx(0.9)


def test_a_drawn_decision_is_told_its_class():
    provider = Scripted({"action": "investigate", "tool_calls": [
        {"tool": "venue.positions", "args": {}}]}, {"action": "hold", "rationale": "read"})
    rt = _consequence_runtime(provider=provider, exchange=_exchange(), manifest=_manifest(1.0))
    handle, _ = _consequence_produce(rt)
    draw = _items(rt, "exploration.draw")[0]
    assert draw["handle"] == handle and draw["p"] == 0.25
    assert "EXPLORATION DRAW" in provider.requests[0].messages[-1]["content"]
    assert f"{draw['class']!r}" in provider.requests[0].messages[-1]["content"]
    taken = _items(rt, "exploration.taken")[0]
    assert taken["drawn"] == draw["class"] and taken["complied"] == (
        taken["taken"] == draw["class"])


def test_the_draw_is_deterministic_for_a_seed():
    def run():
        provider = Scripted(*[{"action": "hold"}] * 6)
        rt = _consequence_runtime(provider=provider, exchange=_exchange(),
                                  manifest=_manifest(0.5))
        for _ in range(6):
            _consequence_produce(rt)
        return [(d["handle"], d["class"]) for d in _items(rt, "exploration.draw")]
    assert run() == run()


def test_share_outside_the_unit_interval_is_refused():
    with pytest.raises(ValueError, match="exploration_share"):
        _manifest(1.5).validate()


def test_a_hold_can_name_its_declined_trade_in_the_published_schema():
    provider = Scripted({"action": "hold", "counterfactual": {"coin": "BTC", "side": "buy"}})
    rt = _consequence_runtime(provider=provider, exchange=_exchange())
    _, event = _consequence_produce(rt)
    assert '"counterfactual"' in provider.requests[0].messages[-1]["content"]
    assert event.payload["outputs"]["counterfactual"] == {"coin": "BTC", "side": "buy"}


def test_a_tool_trade_reported_as_order_is_named_by_the_trade():
    from factorylab.runtime.propensity import action_label

    report = {"action": "order", "rationale": "submitted a tiny market buy"}
    assert action_label("producer", report, "ok", ("buy:BTC:xs",)) == "buy:BTC:xs"
    assert action_label("producer", {**report, "coin": "BTC", "side": "buy",
                                     "size": "0.001"}, "ok", ("buy:BTC:xs",)) == "buy:BTC:xs"
    assert action_label("producer", report, "ok") == "malformed"  # nothing traded
