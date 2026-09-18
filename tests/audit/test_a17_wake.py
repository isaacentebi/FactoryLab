"""A17: the wake is a read-only observatory of everything already public.

Essay I.III: the control tower reads the outcomes the factory produces, and
nothing else. Whatever the population can see is public to the experimenter,
and so is everything the population wrote: the ``returns`` view publishes every
answer live and unredacted, because darkness is not secrecy. Learner state,
router weights and sampling propensities, private memories, prompts and
per-decision scores stay sealed until death, and every section other than
``returns`` still folds to role totals without an id or a handle.
"""

import json
import shutil

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.wake import (
    collect_wake,
    render_wake,
)
from factorylab.runtime.worlds import load_manifest

# One window must close for the standing sections to exist; the scripted world
# closes its first at event 121 and exercises registration, retirement, an
# amendment and a refused transfer before then.
EVENTS = 150

# Keys the essay keeps private. They are asserted absent as *keys*: a public
# observation may legitimately be named "verdict_mean" in a charter card.
SEALED_KEYS = frozenset({
    "propensity", "propensities", "chosen", "weights", "gamma", "learner", "learner_id",
    "state", "memory", "memories", "prompt", "system_prompt", "prompt_text", "raw",
    "outputs", "output", "rationale", "text", "verdict", "payoff", "score", "scores",
    "skill", "handle", "assembly_id", "actor", "id_", "entry_px", "px", "size", "notional",
})
MARKER = "SEALED_ONLY_PRIVATE_TEXT"


@pytest.fixture(scope="module")
def scripted(scripted_run):
    """A shared scripted ledger; consumers must copy its directory before writing."""
    return scripted_run("scripted", EVENTS, 1).ledger_path


@pytest.fixture(scope="module")
def wake(scripted):
    return collect_wake(scripted)


def _keys(value):
    """Every key at every depth of the published document."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_a17_no_sealed_field_appears_anywhere_in_the_output(scripted, tmp_path):
    """Private items exist in the diary; none of them, and no sealed key, is published.

    A return's outputs are the one exception, by the experimenter's decision: they
    are published in ``returns`` and nowhere else. The router's sampling
    propensity, learner state, memories and prompts stay sealed.
    """
    directory = tmp_path / "private-world"
    shutil.copytree(scripted.parent, directory)
    scripted = directory / scripted.name
    manifest = load_manifest("scripted")
    writer = Ledger.reopen(scripted, manifest=json.loads(manifest.canonical_json()))
    written = "WHAT_THE_SEAT_WROTE"
    writer.append({"kind": "invocation", "assembly_id": "eval-a", "role": "evaluator",
                   "handle": "decision-1", "outputs": written, "cost": 1, "status": "ok"})
    writer.append({"kind": "decision.open", "handle": "decision-1", "score": 0.9,
                   "propensity": {"chosen": MARKER, "weights": [MARKER], "p": 0.5}})
    writer.append({"kind": "router.state", "learner_id": MARKER, "weights": [1.0, 2.0],
                   "memory": MARKER, "prompt": MARKER, "verdict": MARKER})
    data = collect_wake(scripted)
    page = render_wake(data)
    document = json.dumps(data)
    assert MARKER not in document and MARKER not in page
    folded = {k: v for k, v in data.items() if k != "returns"}
    assert not _keys(folded) & SEALED_KEYS
    assert written not in json.dumps(folded)
    # Restated for the GPT-6 third reading (§3): an unparseable diary text is no longer
    # republished verbatim as ``truncated_text``. The seat's own words are exactly what
    # a public projection must not leak when it cannot tell which fields it is holding,
    # so the wake says the outputs are unavailable and the written text appears nowhere.
    assert data["returns"]["rows"][-1]["outputs"] == {
        "outputs_unavailable": "unparseable_or_truncated"}
    assert written not in document and written not in page
    assert not _keys(data["returns"]) & {"propensities", "chosen", "weights", "gamma",
                                         "learner", "learner_id", "memory", "memories",
                                         "prompt", "system_prompt", "prompt_text"}
