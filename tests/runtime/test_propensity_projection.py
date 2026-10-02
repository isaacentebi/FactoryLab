"""A propensity travels forward without carrying its author's words.

Chapter II §I.b: the request is "neutral with respect to its author", and the
propensity is the one piece of local state that travels forward with it. AGENTS rule
5: a judge never receives text that reveals who wrote what it judges. The distribution
and the action taken travel; an identifier outside the published action vocabulary
travels as an opaque alias, and the sealed record keeps the original for learning.
"""

import json

import pytest

from tests.audit.test_a10_propensity import Decider
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)

IDENTITY = "I am seed-decider"


class Recording(Decider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prompts = []

    def complete(self, req):
        self.prompts.append("\n".join(str(m.get("content", "")) for m in req.messages))
        return super().complete(req)


@pytest.mark.parametrize("hidden", [IDENTITY, f"verdict:{IDENTITY}", f"conformity:{IDENTITY}",
                                    f"hold+verdict:{IDENTITY}"])
def test_forwarded_propensity_withholds_identity_text(hidden):
    provider = Recording(propensity={"hold": 0.9, hidden: 0.1})
    runtime = _consequence_runtime(provider=provider)
    handle, event = _consequence_produce(runtime)
    declared = runtime.queue.declared_propensity(handle)
    # The sealed record keeps the seat's own identifiers: learning reads them.
    assert hidden in declared.action_ids
    before = len(provider.prompts)
    _consequence_judge(runtime, event, "eval-a")
    judged = [p for p in provider.prompts[before:] if "PROPENSITY" in p]
    assert judged, "the judge's request carries the subject's propensity"
    for prompt in judged:
        assert IDENTITY not in prompt and "seed-decider" not in prompt.split(
            "PROPENSITY", 1)[1].split("\n\n", 1)[0]
        section = prompt.split("PROPENSITY", 1)[1].split("\n\n", 1)[0]
        shown = json.loads(section.strip().splitlines()[-1])
        # Both masses survive, and the action taken is still the one named.
        assert sorted(shown.values()) == pytest.approx([0.1, 0.9])
        assert shown["hold"] == pytest.approx(0.9)
        assert "(hold)" in section


def test_projection_keeps_the_published_vocabulary_and_aliases_the_rest():
    from factorylab.runtime.propensity import neutral_projection, published_label

    markets = frozenset({"BTC", "ETH", "XYZ:TSLA"})
    over = {"hold": 0.4, "buy:BTC:m": 0.2, "sell:XYZ:TSLA:xs": 0.1,
            "buy:SEEDDECIDER:m": 0.1, "verdict:0.8": 0.05, "close:ETH+hold": 0.05,
            "my own plan": 0.1}
    shown, chosen = neutral_projection(over, "my own plan", markets)
    assert list(shown.values()) == list(over.values())
    assert {k for k in shown if k in over} == {
        "hold", "buy:BTC:m", "sell:XYZ:TSLA:xs", "verdict:0.8", "close:ETH+hold"}
    aliases = [k for k in shown if k not in over]
    assert len(aliases) == 2 and chosen in aliases
    assert shown[chosen] == pytest.approx(0.1)
    for text in ("SEEDDECIDER", "my own plan"):
        assert text not in json.dumps(shown) and text not in chosen
    # A literal alias declared by a seat is itself aliased, never confused with one.
    shown, chosen = neutral_projection({"other:1": 0.5, "hold": 0.5}, "hold", markets)
    assert chosen == "hold" and set(shown) == {"other:1", "hold"}
    assert not published_label("other:1", markets)
    assert not published_label("request:seed-decider", markets)
    # Every operand is checked against its published domain, never against its own
    # canonical form: text, non-finite and out-of-range scores are not labels.
    for label in (f"verdict:{IDENTITY}", f"conformity:{IDENTITY}", f"hold+verdict:{IDENTITY}",
                  "verdict:nan", "verdict:inf", "verdict:2.0", "verdict:-1.0", "verdict:0.80",
                  "verdict:", "conformity:1.1", "buy:BTC:m:x", "polymarket:buy:xs:x",
                  "transfer:sideways", "vault:steal", "close:SEEDDECIDER"):
        assert not published_label(label, markets), label
    for label in ("verdict:0.0", "verdict:1.0", "conformity:0.6", "hold+verdict:0.8",
                  "polymarket:cancel", "polymarket:sell:l", "vault:deposit"):
        assert published_label(label, markets), label
