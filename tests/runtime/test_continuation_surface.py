"""A tool continuation publishes its limits in its schema; it carries no instructions.

Chapter II §I.a: "the less the architect knows, the less structure they should
impose". AGENTS rules 1 and 3: the continuation schema is the contract the kernel
enforces (``cap_continuation``); prose telling the seat what to do next is not.
"""

from tests.audit import class2_lexicon as lexicon
from tests.runtime.test_discovery_continuation import ANSWER, request, runtime, scripted
from tests.runtime.test_loop import lists_nothing


def _tool_bound(schema, key):
    shapes = schema.get("anyOf", [schema])
    return {s.get("properties", {}).get(key, {}).get("maxItems") for s in shapes}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_tool_continuations_publish_limits_without_coaching(monkeypatch):
    rt = lists_nothing(runtime())
    req = request(rt, ANSWER)
    seen = []
    invoke = rt._invoke_compute

    def capture(seat, current):
        seen.append(current)
        return invoke(seat, current)

    monkeypatch.setattr(rt, "_invoke_compute", capture)
    prompts: list[str] = []
    read = {"tool": "world.read", "args": {"section": "composition"}}
    scripted(rt, monkeypatch, [
        {"tool_calls": [read]},  # an affordable, unanswered read: a round is granted
        {"tool_calls": [read]},  # the same read again buys nothing: the next is closing
        {"action": "hold"},
    ], prompts)
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and len(seen) == 3
    granted, closing = seen[1], seen[2]
    # The limits stand in the schemas: a granted round admits tools and no children,
    # a closing round admits neither.
    assert _tool_bound(granted.outcome_schema, "tool_calls") == {None}
    assert _tool_bound(granted.outcome_schema, "requests") == {0}
    assert _tool_bound(closing.outcome_schema, "tool_calls") == {0}
    assert _tool_bound(closing.outcome_schema, "requests") == {0}
    # And nothing in the continuation's inputs tells the seat what to do next.
    for follow in (granted, closing):
        assert "continuation" not in follow.inputs
        added = {k: v for k, v in follow.inputs.items() if k not in req.inputs}
        for key, value in added.items():
            if key in ("tool_results", "seen_tool_results"):
                continue
            for text in _strings(value):
                assert lexicon.lint_text(f"*/continuation/{key}", text) == [], (key, text)
    for prompt in prompts[1:]:
        assert "final answer" not in prompt and "call tools again" not in prompt
    # A continuation never claims a routing bridge, whatever its inputs say.
    assert granted.continued and closing.continued and not seen[0].continued
