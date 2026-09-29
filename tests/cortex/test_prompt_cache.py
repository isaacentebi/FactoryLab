"""§IV.a, speed is cash burn: what holds still leads the prompt, what moves trails it.

A seat's consecutive requests differ in their account, update, request and inputs,
and in nothing else a provider's prefix cache reads first. These tests render two
real requests of one seat whose inputs differ and prove the leading run is the
same bytes, and that the reorder that made it so moved sections without changing
a character of any of them.
"""

from __future__ import annotations

import json
from dataclasses import replace

from factorylab.cortex.request import CACHE_PREFIX_SECTIONS, YOU_HEADER
from tests.conftest import make_runtime

SEAT = "seed-decider"


def _requests(rt):
    return [
        rt._request(
            f"cache-lead-{index}", f"Respond to supplied tick {index}.",
            {"kind": "Tick", "payload": {"index": index, "note": "x" * index},
             "world": rt._world_block()},
            {}, 10**15, "policy",
        )
        for index in (1, 2)
    ]


def test_a_seats_cacheable_lead_is_byte_identical_while_its_inputs_differ():
    rt = make_runtime()
    assembly = rt.assemblies[SEAT]
    first, second = (assembly.build_model_request(req) for req in _requests(rt))
    one, two = first.messages[-1]["content"], second.messages[-1]["content"]
    assert one != two  # the two calls really differ ...
    lead = first.cache_prefix_chars
    assert lead > 0 and second.cache_prefix_chars == lead
    # ... and not in the system message or in any byte of the leading run.
    assert first.system == second.system
    assert one[:lead] == two[:lead]
    stamped = replace(_requests(rt)[0], inputs={**_requests(rt)[0].inputs, "you": SEAT})
    assert one[:lead] == stamped.cache_prefix()
    assert one.startswith(stamped.stable_prefix())
    # The lead is the stable block and the reply contract, and it stops at YOU:
    # nothing of this call's own account, event or inputs is inside it.
    assert "OUTCOME SCHEMA\n" in one[:lead] and "OUTCOME CONTRACT" in one[:lead]
    assert one[lead:].startswith(YOU_HEADER) and two[lead:].startswith(YOU_HEADER)
    assert "cache-lead-1" not in one[:lead] and "cache-lead-1" in one[lead:]
    assert "Respond to supplied tick 1." in one[lead:]


def test_the_reorder_moves_whole_sections_and_changes_none_of_them():
    rt = make_runtime()
    req = replace(_requests(rt)[0], inputs={**_requests(rt)[0].inputs, "you": SEAT})
    sections = req.sections()
    names = [name for name, _text in sections]
    assert tuple(names[:len(CACHE_PREFIX_SECTIONS)]) == CACHE_PREFIX_SECTIONS
    assert names[len(CACHE_PREFIX_SECTIONS):len(CACHE_PREFIX_SECTIONS) + 2] == [
        "you", "world_update"]
    assert names[-1] == "completion_criterion"
    assert len(names) == len(set(names))
    text = dict(sections)
    # Every section is still headed and whole; only the last carries no separator.
    assert text["outcome_schema"].startswith("OUTCOME SCHEMA\n")
    assert text["outcome_schema"].endswith("\n\n")
    json.loads(text["outcome_schema"].removeprefix("OUTCOME SCHEMA\n").split("\n")[0])
    assert text["outcome_contract"].endswith("\n\n")
    assert not text["completion_criterion"].endswith("\n")
    assert "".join(text for _name, text in sections) == req.prompt_text()
    assert req.section_bytes()["total"] == len(req.prompt_text().encode("utf-8"))


def test_the_ledger_hashes_the_lead_the_provider_was_sent():
    """The diary's prefix identity covers the same run the request states as cacheable."""
    rt = make_runtime()
    captured = []
    complete = rt.provider.target.complete

    def capture(request):
        captured.append(request)
        return complete(request)

    rt.provider.target.complete = capture
    for req in _requests(rt):
        rt._invoke(SEAT, req, "producer")
    rows = [row for row in rt.ledger._recovery_items()
            if row["kind"] == "invocation" and str(row["handle"]).startswith("cache-lead-")]
    assert len(rows) == 2 and len(captured) == 2
    assert rows[0]["prompt_cache"] == rows[1]["prompt_cache"]
    assert captured[0].cache_prefix_chars == captured[1].cache_prefix_chars > 0


def test_a_block_that_runs_into_the_next_section_keeps_the_contract_after_the_work():
    """The contract moves only where its header still opens a line; otherwise the
    request renders in its old order and only the block itself is the cacheable run."""
    from factorylab.cortex.request import Request

    def render(stable):
        return Request("h-1", "Do the work.", {"kind": "Tick", "payload": {},
                                                "world": {"stable_prefix": stable}},
                       {}, {"type": "object", "required": ["action"]}, 0, 0, "parent-1",
                       "a JSON object satisfying the outcome schema", "verdict", "h-1")

    runs_on = render('OPERATING ACCESS\n{"tools":[]}')
    names = [name for name, _text in runs_on.sections()]
    assert names.index("outcome_schema") > names.index("inputs")
    assert names[-3:] == ["outcome_schema", "outcome_contract", "completion_criterion"]
    assert runs_on.cache_prefix() == 'OPERATING ACCESS\n{"tools":[]}'
    ends_line = render("WORLD CONTRACT\nfixed\n\n")
    assert [name for name, _text in ends_line.sections()][:3] == list(CACHE_PREFIX_SECTIONS)
    assert ends_line.prompt_text().startswith("WORLD CONTRACT\nfixed\n\nOUTCOME SCHEMA\n")
    # Either way every section's own text is the same; only positions differ.
    assert dict(render("WORLD CONTRACT\nfixed\n\n").sections())["outcome_contract"] == \
        dict(runs_on.sections())["outcome_contract"]
    # No stable block, no cacheable run.
    assert render("").cache_prefix() == ""
