"""The norm house's signed norm edition (charter audit M4, P5).

Essay II.IV.a: the norm layer is a "top congressional house", "'read-only' from the
perspective of the factory, though the factory is expected to testify within the
assembly"; and "the read/write permissions of the factory's input layer are part of
the factory's hard kernel". The write permission is the manifest's
``[norm_house] signer``. An edition applies only at a governance boundary, after the
seated committee's ledgered, non-binding testimony, as ``edition + 1``: new norms,
the factory's cards carried over, cards on removed norms refused.
"""

import json
import os
from dataclasses import replace

import pytest

from factorylab.charter.norm_edition import FORMAT, build, inbox_path, verify
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import NormHouseSpec, load_manifest, manifest_from_dict
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

HOUSE_KEY = "0x" + "11" * 32
OTHER_KEY = "0x" + "22" * 32
SEATS = {"seed-observer": "producer", "seed-decider": "producer", "eval-a": "evaluator",
         "eval-b": "evaluator", "antagonist-a": "antagonist", "meta-a": "meta"}
# The scripted charter's four norms, less "care with scarce resources" (which carries
# cost_per_return), plus one new norm with a definition.
NORMS = ["truthful commitments", "useful inquiry", "the capacity to revise inadequate practices",
         {"id": "legibility", "definition": "What the factory does can be read from outside."}]


def _address(key):
    from eth_account import Account

    return Account.from_key(key).address.lower()


def _manifest(signer=HOUSE_KEY):
    base = load_manifest("scripted")
    return replace(base, norm_house=NormHouseSpec(_address(signer) if signer else None))


def _runtime(tmp_path, monkeypatch, manifest):
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                 ledger_path=str(tmp_path / "world.jsonl"), router_gamma=.1,
                 exchange=FakeExchange(), provider=ScriptedProvider())
    rt._manage_reserve_window()
    monkeypatch.setattr(rt, "_committee_eligible", lambda: dict(SEATS))
    return rt


def _write(rt, body, sequence=1):
    path = inbox_path(rt.ledger.path, sequence)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))


def _edition(rt, key=HOUSE_KEY, sequence=1, norms=NORMS):
    return build(world=rt.m.name, manifest_sha256=rt.m.manifest_hash(), sequence=sequence,
                 norms=norms, private_key=key)


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _to_boundary(rt):
    rt.clock.now_ns = rt.cadence.earliest_ns(rt.tick_clock)
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)


# --- the signed file ----------------------------------------------------------------


def test_a_signed_edition_verifies_and_every_other_is_refused():
    m = _manifest()
    body = build(world=m.name, manifest_sha256=m.manifest_hash(), sequence=1, norms=NORMS,
                 private_key=HOUSE_KEY)
    assert body["format"] == FORMAT and body["signer"] == m.norm_house.signer
    norms, digest = verify(body, signer=m.norm_house.signer, world=m.name,
                           manifest_sha256=m.manifest_hash(), sequence=1)
    assert [str(n) for n in norms][-1] == "legibility"
    assert norms[-1].definition.startswith("What the factory does")
    ok = {"signer": m.norm_house.signer, "world": m.name,
          "manifest_sha256": m.manifest_hash(), "sequence": 1}
    cases = [
        ({**body}, {**ok, "signer": None}, "casts no norm_house.signer"),
        ({**body, "norms": [*body["norms"], "smuggled"]}, ok, "not the manifest's"),
        (build(world=m.name, manifest_sha256=m.manifest_hash(), sequence=1, norms=NORMS,
               private_key=OTHER_KEY), ok, "not the manifest's"),
        ({**build(world=m.name, manifest_sha256=m.manifest_hash(), sequence=1, norms=NORMS,
                  private_key=OTHER_KEY), "signer": m.norm_house.signer}, ok,
         "not the manifest's"),
        ({**body, "signature": "0x1234"}, ok, "signature is invalid"),
        ({**body, "lambda": {"cost_per_return": 0.0}}, ok, "exactly the fields"),
        ({**body, "cards": []}, ok, "exactly the fields"),
        (body, {**ok, "world": "another"}, "another world"),
        (body, {**ok, "sequence": 2}, "next norm edition is sequence 2"),
    ]
    for candidate, expected, reason in cases:
        with pytest.raises(ValueError, match=reason):
            verify(candidate, **expected)


# --- in a running world ---------------------------------------------------------------


def test_an_edition_applies_only_at_a_boundary_after_ledgered_testimony(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, _manifest())
    priced = set(rt.priced)
    # A waiting motion whose card names the norm the edition removes is refused with it.
    rt._propose_amendment(_decision(rt), {
        "kind": "amendment", "id": "scarce-card", "add": [{
            "id": "scarce", "norm": "care with scarce resources", "description": "Cost.",
            "units": "micro-USD", "window": {"kind": "windows", "n": 1, "per": None},
            "acceptable_region": "at most 900", "observation": "burn_per_window",
            "answers_for": "all"}],
        "predicted_effect": {"card_id": "scarce", "direction": "decrease", "window": 1}})
    _write(rt, _edition(rt))
    rt._activate_charter_if_due()  # a window boundary, not a governance boundary
    assert rt.charter.edition == 1 and _items(rt, "charter.norm_edition") == []
    assert _items(rt, "norm_edition.testimony") == []
    _to_boundary(rt)
    rt._activate_charter_if_due()
    seating, = _items(rt, "charter.seat")
    testimony = _items(rt, "norm_edition.testimony")
    assert [t["alias"] for t in testimony] == [s["alias"] for s in seating["seats"]]
    assert all(t["assessment"] == "scripted testimony" and t["sequence"] == 1
               for t in testimony)
    # Non-binding: each testimony's decision closes unscored.
    assert all(rt.queue.history(t["handle"])[-1].definition_version
               == "norm-testimony-unscored-v1" for t in testimony)
    kinds = [i["kind"] for i in rt.ledger._recovery_items()]
    assert kinds.index("norm_edition.testimony") < kinds.index("charter.norm_edition")
    edition, = _items(rt, "charter.norm_edition")
    assert edition["edition"] == 2 and edition["base_edition"] == 1
    assert edition["removed"] == ["care with scarce resources"]
    assert edition["added"] == ["legibility"] and edition["signer"] == rt.m.norm_house.signer
    assert rt.charter.edition == 2
    assert [str(n) for n in rt.charter.norms] == [
        "truthful commitments", "useful inquiry", "the capacity to revise inadequate practices",
        "legibility"]
    # The factory's cards are carried over; the card on the removed norm is refused.
    assert [c.id for c in rt.charter.cards] == ["well_formed_rate", "forecast_skill"]
    refusals = _items(rt, "charter.refused")
    assert {r.get("card_id") for r in refusals} >= {"cost_per_return"}
    assert {r.get("amendment_id") for r in refusals} >= {"scarce-card"}
    assert rt.charter_book.pending() == []
    assert rt.priced <= priced and "cost_per_return" not in rt.priced
    assert rt.charter_book.norm_editions() == {2: {
        "sequence": 1, "digest": edition["digest"], "signer": rt.m.norm_house.signer}}
    # Applied once: the next boundary does not re-apply it; sequence 2 is next.
    _to_boundary(rt)
    rt._activate_charter_if_due()
    assert rt.charter.edition == 2 and len(_items(rt, "charter.norm_edition")) == 1


def test_without_the_manifests_signer_no_edition_is_possible(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, _manifest(signer=None))
    _write(rt, build(world=rt.m.name, manifest_sha256=rt.m.manifest_hash(), sequence=1,
                     norms=NORMS, private_key=HOUSE_KEY))
    _to_boundary(rt)
    rt._activate_charter_if_due()
    refused, = _items(rt, "norm_edition.refused")
    assert "casts no norm_house.signer" in refused["reason"]
    assert rt.charter.edition == 1 and _items(rt, "norm_edition.testimony") == []


def test_an_edition_with_an_invalid_signature_is_refused(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, _manifest())
    forged = {**_edition(rt, key=OTHER_KEY), "signer": rt.m.norm_house.signer}
    _write(rt, forged)
    _to_boundary(rt)
    rt._activate_charter_if_due()
    refused, = _items(rt, "norm_edition.refused")
    assert "not the manifest's norm_house.signer" in refused["reason"]
    assert rt.charter.edition == 1 and "cost_per_return" in {c.id for c in rt.charter.cards}
    # A tampered body fails the same way.
    _write(rt, {**_edition(rt), "norms": ["only this"]})
    _to_boundary(rt)
    rt._activate_charter_if_due()
    assert len(_items(rt, "norm_edition.refused")) == 2 and rt.charter.edition == 1


def test_below_quorum_the_edition_still_applies_and_the_absence_is_ledgered(tmp_path,
                                                                           monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, _manifest())
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {"eval-a": "evaluator"})
    _write(rt, _edition(rt))
    _to_boundary(rt)
    rt._activate_charter_if_due()
    absent, = _items(rt, "norm_edition.testimony_absent")
    assert absent["sequence"] == 1
    assert rt.charter.edition == 2


def _decision(rt, seat="seed-decider"):
    from factorylab.kernel.queue import PropensityRecord

    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id="proposal", propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    return handle


# --- the CLI ---------------------------------------------------------------------------


def _world_file(tmp_path, signer):
    text = open("worlds/scripted.toml").read()
    if signer:
        text += f'\n[norm_house]\nsigner = "{_address(signer)}"\n'
    # A manifest's name must match its file stem.
    folder = tmp_path / ("signed" if signer else "unsigned")
    folder.mkdir(exist_ok=True)
    path = folder / "scripted.toml"
    path.write_text(text)
    return path


def _key_file(tmp_path, key, name="house.key"):
    path = tmp_path / name
    path.write_text(key)
    os.chmod(path, 0o600)
    return path


def test_the_cli_writes_a_signed_edition_beside_the_ledger(tmp_path, capsys):
    from factorylab.runtime.cli import ARGUMENT_EXIT, main

    world = _world_file(tmp_path, HOUSE_KEY)
    norms = tmp_path / "norms.toml"
    norms.write_text('norms = ["truthful commitments", { id = "legibility", '
                     'definition = "Readable from outside." }]\n')
    ledger = tmp_path / "w.jsonl"
    args = ["norm-edition", "--world", str(world), "--ledger", str(ledger),
            "--norms", str(norms), "--sequence", "1"]
    assert main([*args, "--key-file", str(_key_file(tmp_path, HOUSE_KEY))]) == 0
    written = json.loads(inbox_path(ledger, 1).read_text())
    m = load_manifest(world)
    norms_out, _ = verify(written, signer=m.norm_house.signer, world=m.name,
                          manifest_sha256=m.manifest_hash(), sequence=1)
    assert [str(n) for n in norms_out] == ["truthful commitments", "legibility"]
    out = capsys.readouterr()
    assert HOUSE_KEY.removeprefix("0x") not in out.out + out.err
    # The same sequence is never overwritten; another key is not the signer.
    assert main([*args, "--key-file", str(_key_file(tmp_path, HOUSE_KEY))]) == ARGUMENT_EXIT
    other = [*args[:-1], "2", "--key-file", str(_key_file(tmp_path, OTHER_KEY, "o.key"))]
    assert main(other) == ARGUMENT_EXIT and not inbox_path(ledger, 2).exists()
    # A norms file carrying anything but norms is refused.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"norms": ["x"], "lambda": {"cost_per_return": 0}}))
    assert main(["norm-edition", "--world", str(world), "--ledger", str(ledger),
                 "--norms", str(bad), "--sequence", "3",
                 "--key-file", str(_key_file(tmp_path, HOUSE_KEY))]) == ARGUMENT_EXIT
    # A manifest with no signer refuses every edition.
    unsigned = _world_file(tmp_path, None)
    assert main(["norm-edition", "--world", str(unsigned), "--ledger", str(ledger),
                 "--norms", str(norms), "--sequence", "3",
                 "--key-file", str(_key_file(tmp_path, HOUSE_KEY))]) == ARGUMENT_EXIT


# --- the manifest ------------------------------------------------------------------------


def _raw():
    import tomllib

    with open("worlds/scripted.toml", "rb") as f:
        return tomllib.load(f)


def test_the_signer_and_quorum_are_launch_casts_and_hashed():
    raw = _raw()
    base = manifest_from_dict(raw)
    assert base.norm_house.signer is None and base.committee.quorum == 3
    signed = manifest_from_dict({**raw, "norm_house": {"signer": _address(HOUSE_KEY)}})
    assert signed.manifest_hash() != base.manifest_hash()
    quorum = manifest_from_dict({**raw, "committee": {"quorum": 4}})
    assert quorum.committee.quorum == 4 and quorum.manifest_hash() != base.manifest_hash()
    for bad in ({"signer": "0x12"}, {"signer": 7}, {"key": "x"}, "0xabc"):
        with pytest.raises(ValueError, match="norm_house"):
            manifest_from_dict({**raw, "norm_house": bad})
    for quorum in (0, 6, True):
        with pytest.raises(ValueError, match="committee.quorum"):
            manifest_from_dict({**raw, "committee": {"quorum": quorum}})


def test_a_charter_after_edition_one_names_its_parent():
    """P5: a charter keeps its lineage across a rebirth, and the lineage is hashed."""
    raw = _raw()
    parent = "ab" * 32
    reborn = manifest_from_dict({**raw, "charter": {**raw["charter"], "edition": 4,
                                                     "parent_charter_sha256": parent}})
    assert reborn.charter.edition == 4 and reborn.charter_parent_sha256 == parent
    assert reborn.manifest_hash() != manifest_from_dict(raw).manifest_hash()
    with pytest.raises(ValueError, match="needs parent_charter_sha256"):
        manifest_from_dict({**raw, "charter": {**raw["charter"], "edition": 2}})
    with pytest.raises(ValueError, match="parent of an edition after 1"):
        manifest_from_dict({**raw, "charter": {**raw["charter"],
                                               "parent_charter_sha256": parent}})
    with pytest.raises(ValueError, match="positive integer"):
        manifest_from_dict({**raw, "charter": {**raw["charter"], "edition": 0}})
