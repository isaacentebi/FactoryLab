"""Edition 3 C1: the acceptance for working state and the outcome inbox.

The plan's acceptance, verbatim: a seat forms a hypothesis in its state at wake
one, returns three more times, the runtime is checkpointed and restored from the
diary in a fresh process-equivalent, the consequence settles, and the seat's next
request carries both the hypothesis (head unchanged) and the outcome addressed to
that exact handle. The matched control, with state disabled, shows neither.

Three returns is the number that mattered: the deque this replaces held exactly
three, so the fourth return used to evict the first and the settlement had
nowhere to land.

Around it: the size limits, the acknowledgement cursor, the refusal, the
scoping of ``artifact.get`` and ``outcome.get``. Nothing here reaches a network,
a credential or a venue beyond the fake.
"""

import json

from factorylab.kernel.artifacts import PRIVATE_REFUSAL
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_connectors import decision
from tests.runtime.test_loop import (
    _consequence_runtime,
)

SEAT = "seed-decider"


# ---- the inbox: cursor, bound, outcome.get ----------------------------------------------


def test_one_seat_cannot_read_another_seats_outcome():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    rt.outcomes.append(SEAT, handle="h0", outcome={"verdict": 1.0})
    handle = decision(rt, "eval-a")
    result, _ = rt._run_tool("eval-a", handle, {"tool": "outcome.get", "args": {"handle": "h0"}})
    assert "error" in result and "handle" not in result


# ---- scoping ----------------------------------------------------------------------------

def test_a_seat_reads_its_own_state_and_a_stranger_is_refused_artifact_private():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    sha = rt.working_state.put(SEAT, {"mine": True})["sha"]
    own = rt._run_tool(SEAT, decision(rt, SEAT), {"tool": "artifact.get", "args": {"sha": sha}})[0]
    assert json.loads(own["text"]) == {"mine": True}
    other = rt._run_tool("eval-a", decision(rt, "eval-a"),
                         {"tool": "artifact.get", "args": {"sha": sha}})[0]
    assert other == {"sha": sha, "error": PRIVATE_REFUSAL}
    row = [i for i in rt.ledger._recovery_items() if i["kind"] == "artifact.get"][-1]
    assert row["found"] is False and row["reason"] == PRIVATE_REFUSAL


# ---- the deque is gone ------------------------------------------------------------------
