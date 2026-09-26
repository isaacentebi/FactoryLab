"""Every executed write keys its verdicts' base rate by its own instrument (wave 16, D3).

A placeholder instrument pools distinct instruments into one prevalence: every
Polymarket token once keyed as ``-``, so twenty similar resolutions anywhere would
have marked every event market uninformative. Each write kind names what it acted on.
"""

from types import SimpleNamespace

import pytest

from factorylab.runtime.compute import ComputeMixin
from factorylab.runtime.polymarket import tool_specs as polymarket_specs
from factorylab.world.venue_tools import vault_specs
from tests.conftest import make_runtime

PLACEHOLDERS = {"", "-", "None", "PM:None", "VAULT:None", "TREASURY:None"}


def _example(rt, operation):
    """The write's first published example: this world's tool, or the vault and
    Polymarket surfaces' own when the scripted world does not publish them."""
    published = {**{k: v["args_schema"].get("examples")
                    for k, v in polymarket_specs(SimpleNamespace(), writes=True).items()},
                 **vault_specs()[1],
                 **{k: v["args_schema"].get("examples") for k, v in rt.tool_specs.items()}}
    return dict(published[operation][0])


def test_no_executed_write_kind_keys_by_a_placeholder():
    """Every consequence write the kernel admits, including any added later: one with
    no identity raises (below) and fails here."""
    rt = make_runtime()
    for operation in sorted(ComputeMixin.CONSEQUENCE_WRITES):
        row = {"operation": operation, "args": _example(rt, operation), "status": "ok"}
        instrument = rt._instrument(row)
        assert instrument not in PLACEHOLDERS and "None" not in instrument, operation


def test_two_polymarket_tokens_key_apart():
    rt = make_runtime()
    first = {"operation": "polymarket.place_limit", "status": "ok",
             "args": {"token_id": "111", "side": "buy", "size": "1", "price": "0.5"}}
    second = {**first, "args": {**first["args"], "token_id": "222"}}
    assert rt._instrument(first) == "PM:111" and rt._instrument(second) == "PM:222"
    rt.world_outcomes["a"] = {"subject": {"coin": rt._instrument(first), "side": "buy"}}
    rt.world_outcomes["b"] = {"subject": {"coin": rt._instrument(second), "side": "buy"}}
    assert rt._verdict_key("a", "return_paid_off") != rt._verdict_key("b", "return_paid_off")


def test_a_write_kind_with_no_identity_is_refused_not_pooled():
    rt = make_runtime()
    with pytest.raises(ValueError, match="no instrument identity"):
        rt._instrument({"operation": "venue.teleport", "args": {}, "status": "ok"})
