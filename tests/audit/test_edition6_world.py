"""Edition 6: the edition 5 rehearsal with the architect's prompt text taken out.

Chapter II rulings R12 and smuggling audit B and C: the seats run on the kernel's
seed system prompt, each lens is a one-sentence prior, and the roster is honestly
unratified until the population ratifies it. The edition 5 file is not touched.

Wave 5a grew the roster to the evaluator population the kernel now requires
(evaluations C1, M3, P6), and edition 5's own roster no longer loads (R8): it is read
here as history, past the one check it fails.
"""

import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from factorylab.charter.provenance import roster_hash
from factorylab.runtime.worlds import load_manifest

EDITION5 = "worlds/edition5-testnet-rehearsal.toml"
EDITION6 = "worlds/edition6-testnet-rehearsal.toml"


def _history(path, monkeypatch):
    """A world file the kernel refuses for its evaluator population (and, since wave 16,
    for its gains: SF-0), read as history."""
    from factorylab.runtime.worlds import WorldManifest

    monkeypatch.setattr(WorldManifest, "_validate_evaluator_population", lambda self: None)
    monkeypatch.setattr(WorldManifest, "gain_headroom",
                        lambda self: {"holds": True, "saturation_windows": 0,
                                      "diagnosis_windows": 0, "min_ratio": 0})
    return load_manifest(path)


def test_edition5_is_refused_for_its_evaluator_population():
    with pytest.raises(ValueError, match="evaluator population") as refused:
        load_manifest(EDITION5)
    reason = str(refused.value)
    assert "evaluator seats (4) do not outnumber producer seats (5)" in reason
    assert "2 model families serve the evaluator tier" in reason


def test_edition6_seeds_the_evaluator_population():
    from factorylab.runtime.families import model_family

    world = load_manifest(EDITION6)
    assert world.evaluator_population_problems() == []
    roles = [seat.role for seat in world.assemblies]
    evaluators = [s for s in world.assemblies if s.role in ("evaluator", "meta", "adversary")]
    assert len(evaluators) == 8 and roles.count("producer") + roles.count("antagonist") == 6
    assert len({model_family(s.model_id) for s in evaluators}) >= 3
    # Metas on three families that read MetaVerdicts too, the adversarial judge, and the
    # swap-based antagonist reading the one kind its Blum-Mansour router routes.
    metas = [s for s in world.assemblies if s.role == "meta"]
    assert len({model_family(s.model_id) for s in metas}) == 3
    assert all("MetaVerdict" in s.accepts for s in metas)
    assert any(s.role == "adversary" for s in world.assemblies)
    core = [s for s in world.assemblies if "Tick" in s.accepts]
    assert [s.role for s in core] == ["antagonist"]
    assert world.evaluation.no_swap_regret_kinds == ("Tick",)


def test_every_seat_runs_on_the_seed_system_prompt_with_a_one_sentence_prior():
    world = load_manifest(EDITION6)
    assert len(world.assemblies) == 14
    for seat in world.assemblies:
        assert seat.system_prompt is None, seat.id
        lens = seat.initial_state["lens"]
        assert lens.startswith("Your starting prior is ") and lens.count(".") == 1, seat.id
        assert "not" not in lens.lower().split(), seat.id


def test_the_charter_is_the_populations_own_and_claims_no_pin_for_a_changed_roster(
        monkeypatch):
    five, six = _history(EDITION5, monkeypatch), load_manifest(EDITION6)
    # Essay II.IV.a: the architect supplies norms only. The norms are edition 5's (the
    # norm house keeps fidelity's value and drops its procedure, charter audit S2); the
    # cards are the ones edition 6's population adopted on 23 September 2026
    # (docs/charter/edition6-ratified.toml).
    assert [str(n) for n in six.charter.norms] == [str(n) for n in five.charter.norms]
    for old, new in zip(five.charter.norms, six.charter.norms, strict=True):
        if str(new) != "fidelity":
            assert new.definition == old.definition
    fidelity = next(n for n in six.charter.norms if str(n) == "fidelity")
    assert "defeasible evidence of the values" in fidelity.definition
    assert "A judge identifying such a conflict must" not in fidelity.definition
    ratified = tomllib.loads(Path("docs/charter/edition6-ratified.toml").read_text())
    assert [c.id for c in six.charter.cards] == [c["id"] for c in ratified["charter"]["cards"]]
    # The roster changed after that session, so no digest is claimed, and a funded copy
    # is refused until a session adopts on the launch roster.
    assert six.charter_ratified_sha256 is None and six.charter_roster_sha256 is None
    assert six.exchange.client_namespace is None and six.exchange.principal_usd is None
    funded = replace(six, name="funded", exchange=replace(
        six.exchange, mainnet=True, client_namespace="0" * 32))
    with pytest.raises(ValueError, match="ratified_sha256"):
        funded._validate_funded_admission()
    # A pin, once claimed, binds the roster that voted: a stale one is refused.
    stale = replace(funded, charter_ratified_sha256=six.charter_content_sha256,
                    charter_roster_sha256="0" * 64)
    with pytest.raises(ValueError, match="roster differs"):
        stale._validate_funded_admission()
    assert roster_hash(six) == roster_hash(load_manifest(
        "worlds/edition6-capital-loop.toml"))

