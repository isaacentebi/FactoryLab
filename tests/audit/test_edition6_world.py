"""Edition 6: the edition 5 rehearsal with the architect's prompt text taken out.

Chapter II rulings R12 and smuggling audit B and C: the seats run on the kernel's
seed system prompt, each lens is a one-sentence prior, and the roster is honestly
unratified until the population ratifies it. The edition 5 file is not touched.
"""

from dataclasses import replace

import pytest

from factorylab.charter.provenance import roster_hash
from factorylab.runtime.worlds import load_manifest

EDITION5 = "worlds/edition5-testnet-rehearsal.toml"
EDITION6 = "worlds/edition6-testnet-rehearsal.toml"


def test_every_seat_runs_on_the_seed_system_prompt_with_a_one_sentence_prior():
    world = load_manifest(EDITION6)
    assert len(world.assemblies) == 9
    for seat in world.assemblies:
        assert seat.system_prompt is None, seat.id
        lens = seat.initial_state["lens"]
        assert lens.startswith("Your starting prior is ") and lens.count(".") == 1, seat.id
        assert "not" not in lens.lower().split(), seat.id


def test_the_charter_is_edition_5s_and_the_roster_claims_no_ratification():
    five, six = load_manifest(EDITION5), load_manifest(EDITION6)
    # Charter audit S2: the norm house keeps fidelity's value and drops its procedure;
    # every card and every other norm is edition 5's.
    assert six.charter.cards == five.charter.cards
    assert six.charter_prices == five.charter_prices
    assert [str(n) for n in six.charter.norms] == [str(n) for n in five.charter.norms]
    for old, new in zip(five.charter.norms, six.charter.norms, strict=True):
        if str(new) != "fidelity":
            assert new.definition == old.definition
    fidelity = next(n for n in six.charter.norms if str(n) == "fidelity")
    assert "defeasible evidence of the values" in fidelity.definition
    assert "A judge identifying such a conflict must" not in fidelity.definition
    assert six.charter_content_sha256 != five.charter_content_sha256
    assert six.charter_ratified_sha256 is None and six.charter_roster_sha256 is None
    assert roster_hash(six) != roster_hash(five)
    assert six.exchange.client_namespace is None and six.exchange.principal_usd is None
    funded = replace(six, name="funded", exchange=replace(
        six.exchange, mainnet=True, client_namespace="0" * 32))
    with pytest.raises(ValueError, match="ratified_sha256"):
        funded.validate()
