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
