"""B4: impossible bounds have no region and are refused before amendment admission."""

from dataclasses import asdict, replace

import pytest

from factorylab.cortex.request import Return
from factorylab.runtime.cards import region_for
from tests.conftest import make_runtime


@pytest.mark.parametrize("prose", ["at most 1e400", "at least -1e400", "between 0 and 1e400",
                                   "between 2 and 1"])
def test_unusable_region_gets_a_public_refusal(prose):
    rt = make_runtime()
    try:
        rt.reserve.open_window(rt.clock.now_ns, rt.wallet.balance)
        card = replace(rt.charter.cards[0], acceptable_region=prose)
        assert region_for(card, rolling={}) is None
        proposal = {"kind": "amendment", "id": "invalid-region", "add": [],
                    "replace": [asdict(card)],
                    "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1}}
        before = rt.reserve.remaining()
        rt._apply_registrations("proposal", Return("proposal", {"register": [proposal]}, 0, "ok"))
        assert rt.stats.amendments_proposed == 0 and rt.reserve.remaining() == before
        assert "finite usable bounds" in rt.registration_feedback[-1]["reason"]
    finally:
        rt._ledger_lock.close()
