"""A request says what it is, shows nothing hollow, and its stable prefix holds still.

Chapter II §I ("the contract has to carry enough self-description") and §I.b (a rich,
self-describing request; the schematics are public). Wave 15 (investigators 3 and 5 on
longrun1): INPUTS showed a WorldUpdate as ``{"tick": 7}`` because its fold had moved to
WORLD UPDATE under another name with nothing pointing to it, while the header claimed
INPUTS carried the request; the wake stated no contract of its own; and the "stable"
prefix carried byte sizes and measured loop periods that moved.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.cortex.request import COALESCED_UPDATE_POINTER, Request
from factorylab.cortex.schematics import (
    INSTITUTIONS_COMPACT_HEADER,
    INSTITUTIONS_HEADER,
    MOVING_INSTITUTION_KEYS,
)
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import PromptSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_reward_chain import Population, _unsettled_produce


def runtime(mode):
    manifest = replace(load_manifest("worlds/scripted.toml"), prompt=PromptSpec(mode=mode))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                   router_gamma=0.1, provider=ScriptedProvider(),
                   exchange=FakeExchange(coins=manifest.exchange.coins))


def test_a_lifted_fold_leaves_a_pointer_in_inputs():
    fold = {"fills": [1], "events": 3}
    req = Request("h", "Respond to event WorldUpdate on runtime.",
                  {"kind": "WorldUpdate", "payload": {"tick": 7, "since_you_last_woke": fold},
                   "world": {}}, {}, {"type": "object"}, 1, 1, None, "c", "x", "h")
    sections = dict(req.sections())
    inputs = json.loads(sections["inputs"].removeprefix("INPUTS\n"))
    assert inputs["payload"] == {"tick": 7, "since_you_last_woke": COALESCED_UPDATE_POINTER}
    assert "changes_since_last_successful_delivery" in COALESCED_UPDATE_POINTER
    update = json.loads(sections["world_update"].removeprefix("WORLD UPDATE\n"))
    assert update["changes_since_last_successful_delivery"] == fold


def test_the_institutions_header_says_what_request_and_inputs_carry():
    for header in (INSTITUTIONS_HEADER, INSTITUTIONS_COMPACT_HEADER):
        assert "INPUTS carries this request" not in header
        assert "REQUEST states this request and INPUTS carries its inputs" in header
        assert "for the life of this runtime" not in header


@pytest.mark.parametrize("mode", ["reference", "compact"])
def test_the_stable_prefix_holds_still_while_the_loops_move(mode):
    """A prefix that moved with every loop was not stable: its stated guarantee (it
    changes only when a registration, a retirement or a charter change takes effect) is
    now true, and the moving loop table is rendered in INPUTS instead."""
    rt = runtime(mode)
    before = rt._stable_prefix_text()
    clock = json.dumps(rt._institutional_block()["clock"])
    rt.clockwork.latencies["price"] = [3, 5, 8]
    rt.clockwork.fire("price", 4, 2)
    rt.clockwork.fire("price", 9, 3)
    rt.stats.last_window_values = {"x": 1.0}
    after = rt._stable_prefix_text()
    assert json.dumps(rt._institutional_block()["clock"]) != clock  # the loops did move
    assert after == before
    world = rt._world_block()
    req = Request("h", "d", {"world": world}, {}, {"type": "object"}, 1, 1, None, "c", "x",
                  "h")
    inputs = json.loads(dict(req.sections())["inputs"].removeprefix("INPUTS\n"))
    for key in MOVING_INSTITUTION_KEYS:
        # The section is named (world.read can fetch it); its moving value is in INPUTS.
        assert key in inputs["world"]
    assert '"loops"' not in req.stable_prefix() and '"loops"' in json.dumps(inputs["world"])


def test_the_compact_directory_names_sections_and_no_moving_sizes():
    rt = runtime("compact")
    prefix = rt._stable_prefix_text()
    directory = rt._institutional_directory(rt._institutional_block())
    assert isinstance(directory["sections"], list)
    assert not re.search(r'"sections":\{', prefix)
    assert "retrieved whole" in directory["authority"]


def test_the_wake_request_describes_itself_as_facts(monkeypatch):
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.2, provider=Population())
    captured = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda *a, **k: captured.append(request(*a, **k))
                        or captured[-1])
    _unsettled_produce(rt)
    (req,) = captured
    text = req.description
    assert text.startswith("Respond to event Tick on test.")
    assert "A return here is this seat's decision about this event" in text
    named = set(re.findall(r"world\.scoring\.([a-z_]+)", text))
    assert named and named <= set(rt._scoring_block())
    for advice in ("should", "consider", "try", "best", "better", "recommend", "task"):
        assert advice not in text.lower()


def test_a_judge_sees_the_judged_decisions_fold_withheld_not_hollow(monkeypatch):
    from factorylab.runtime.loop import FOLD_WITHHELD
    from factorylab.runtime.shared import CH_CONFORMITY
    from tests.runtime.test_loop import _consequence_decision

    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.2, provider=Population(verdicts=(0.5,)))
    _producer, event = _unsettled_produce(rt)
    payload = json.loads(json.dumps(dict(event.payload), default=dict))
    payload["inputs"]["payload"]["since_you_last_woke"] = {"fills": []}
    event = replace(event, payload=payload)
    captured = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda *a, **k: captured.append(request(*a, **k))
                        or captured[-1])
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns)
    (req,) = captured
    assert req.inputs["producer"]["inputs"]["payload"]["since_you_last_woke"] == FOLD_WITHHELD
