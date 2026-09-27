"""A seat's local state stays its own (essay II.I.b; information audit C3, C4, C5).

"The local state of a given agent ... should be absolutely private." Each case
below attempts to read another seat's private state through a surface that used
to disclose it, and asserts the attempt sees nothing.
"""

import json

from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision


def test_artifact_list_and_the_seat_directory_show_only_the_callers_own_rows():
    rt = make_runtime()
    mine = rt.artifacts.put(b"my own bytes", owner="seed-decider", kind="working.state")
    theirs = rt.artifacts.put(b"someone else's bytes", owner="eval-a", kind="outcome.item")
    handle = decision(rt)

    page, cost = rt._run_tool("seed-decider", handle, {"tool": "artifact.list", "args": {}})
    assert cost == 0
    assert [row["sha"] for row in page["items"]] == [mine] and page["count"] == 1
    assert all("owner" not in row and "public" not in row for row in page["items"])
    # The removed owner filter cannot be used to name another seat.
    refused, _ = rt._run_tool("seed-decider", handle,
                              {"tool": "artifact.list", "args": {"owner": "eval-a"}})
    assert theirs not in json.dumps(refused)

    world = rt._world_block()
    row = next(r for r in world["seats"] if r["seat_id"] == "seed-decider")
    assert [a["sha"] for a in row["directory"]["artifacts"]["newest"]] == [mine]
    shared = {k: v for k, v in world.items() if k != "seats"}
    assert theirs not in json.dumps(shared, default=str)
    assert "shared_directory" not in json.dumps(world["world_update"], default=str)


def test_program_stdin_carries_only_the_programs_own_seat_row():
    from dataclasses import replace

    from tests.cortex.test_programs import program, req

    asm, _wallet, _recorded = program()
    world = {"seats": [{"seat_id": "prog-a", "open_commitments": "mine"},
                       {"seat_id": "eval-a", "open_commitments": {"sealed_forecasts": [
                           {"q": 0.83}]}}],
             "clock_now": {"tick_index": 3}}
    request = req()
    stdin = json.loads(asm.build_stdin(replace(request, inputs={**request.inputs,
                                                                   "world": world}), None))
    # C3: the partition the prompt applies, applied to what a program reads raw.
    assert stdin["inputs"]["world"]["seats"] == [{"seat_id": "prog-a",
                                                   "open_commitments": "mine"}]
    assert "0.83" not in json.dumps(stdin)
    assert stdin["inputs"]["world"]["clock_now"] == {"tick_index": 3}


def test_refusals_reach_only_their_owner_and_no_world_block_broadcasts_them():
    rt = make_runtime()
    handle = decision(rt)
    rt._refuse_judgement(handle, "judgement needs an addressable return handle")
    world = rt._world_block()
    # C5: no broadcast of other seats' refusals in the world every seat reads.
    assert "return_feedback" not in world and "registration_feedback" not in world
    told = {seat: [rt.outcomes.body(r["sha"])["outcome"] for r in records]
            for seat, records in rt.outcomes.items.items()}
    assert [item["kind"] for item in told["seed-decider"]] == ["judgement_refused"]
    assert set(told) == {"seed-decider"}


def test_pathology_labels_are_not_in_the_seat_facing_world_block():
    rt = make_runtime()
    rt.stats.pathologies = {"learning_death": True, "stable_failure": False, "thrash": False}
    world = rt._world_block()
    # U4: the architect's diagnosis goes to observers and the wake, not to seats.
    assert "learning_death" not in json.dumps(world, default=str)


def test_a_transfer_intent_event_names_the_handle_not_the_seat():
    rt = make_runtime()
    handle = decision(rt)
    rt.consequences.start(handle, rt.n)  # a producing decision may write
    rt._run_tool("seed-decider", handle, {"tool": "treasury.transfer",
                                          "args": {"direction": "to_reserve", "usd": "1"}})
    intents = [e for e in rt.internal if str(e.kind) == "TransferIntent"]
    # C8: the public event carries the handle; the ledger keeps the author.
    assert intents and all("by" not in e.payload for e in intents)
    assert intents[-1].payload["handle"] == handle
    ledgered = [i for i in rt.ledger._recovery_items() if i["kind"] == "treasury.intent"]
    assert ledgered[-1]["by"] == "seed-decider"
