"""The capability index describes what can be looked up; it prescribes no strategy.

Chapter II §I.b: a request carries a "complete, semantically rich, but also neutral
self-description". AGENTS rules 1 and 3: publishing the discovery tool and its schema
is the surface; when to call it is the seat's.
"""

from dataclasses import replace

from factorylab.cortex.schematics import CAPABILITY_HEADER
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import PromptSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.audit import class2_lexicon as lexicon


def _runtime(mode: str) -> Runtime:
    manifest = replace(load_manifest("worlds/scripted.toml"), prompt=PromptSpec(mode=mode))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                   provider=ScriptedProvider(),
                   exchange=FakeExchange(coins=manifest.exchange.coins))


def test_capability_prefix_contains_no_action_instructions():
    collocations = [c["text"] for c in lexicon.load_allowlist()["collocation"]]
    assert lexicon.lint_text("*/capability_header", CAPABILITY_HEADER,
                             collocations=collocations) == []
    for mode in ("reference", "compact"):
        rt = _runtime(mode)
        returns = rt._capability_index()["returns"]
        assert lexicon.lint_text("*/capability_index/returns", returns,
                                 collocations=collocations) == []
        # The facts stay: the discovery tool and what it returns are still published.
        assert "catalogue.search" in CAPABILITY_HEADER and "catalogue.search" in returns
        assert "catalogue.search" in rt._stable_prefix_text()
