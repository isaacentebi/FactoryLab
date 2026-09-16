import pytest

from factorylab.runtime.cli import main
from factorylab.runtime.worlds import NS_PER_HOUR, load_manifest, manifest_from_dict


def test_scripted_manifest_loads_and_hashes_stably() -> None:
    m = load_manifest("scripted")
    assert m.name == "scripted" and m.exchange.kind == "fake"
    assert m.initial_balance_micro == 100_000_000
    assert m.drip is None  # one starting balance, no drip in the seed worlds
    # short windows so charter editions can activate within a test run
    assert m.novelty.window_ns == 2 * 60 * 1_000_000_000
    assert m.price_table().cost("fake-opus", 1000, 100) == 1000 * 5 + 100 * 25
    assert m.manifest_hash() == load_manifest("scripted").manifest_hash()
    assert m.manifest_hash() != _testnet_with_charter().manifest_hash()


def test_testnet_manifest_is_not_mainnet() -> None:
    m = _testnet_with_charter()
    assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
    ids = {t.id for t in m.models}
    assert {"z-ai/glm-5.3-flash", "qwen/qwen3.8-flash", "openai/gpt-5.6-luna"} <= ids
    assert all(t.provider == "openrouter" for t in m.models)
    assert "z-ai/glm-5.3-flash:online" in m.price_table().prices


def _base() -> dict:
    return {
        "name": "x",
        "initial_balance_usd": "10",
        "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
        "assemblies": [{"id": "a", "model_id": "m"}],
        "novelty": {"share": 0.1, "window": "1h"},
    }


def test_validation_rejects_unpriced_assembly_and_mainnet_outside_funded() -> None:
    d = _base()
    d["assemblies"][0]["model_id"] = "ghost"
    with pytest.raises(ValueError):
        manifest_from_dict(d)
    d = _base()
    d["exchange"] = {"kind": "hyperliquid", "mainnet": True}
    with pytest.raises(ValueError):
        manifest_from_dict(d)
    d["name"] = "funded"
    d["charter"] = _with_charter()["charter"]
    # A funded world also needs its own venue identity space and its ratified
    # charter/roster hashes; those refusals are covered in tests/audit/test_r3_l_mainnet.py.
    with pytest.raises(ValueError, match="client_namespace"):
        manifest_from_dict(d)


def test_mainnet_requires_a_client_namespace_before_the_charter_hashes() -> None:
    d = _base()
    d["name"] = "funded"
    d["exchange"] = {"kind": "hyperliquid", "mainnet": True}
    d["charter"] = _with_charter()["charter"]
    with pytest.raises(ValueError, match="client_namespace"):
        manifest_from_dict(d)
    d["exchange"]["client_namespace"] = "b" * 32
    with pytest.raises(ValueError, match="ratified_sha256"):
        manifest_from_dict(d)


def test_duration_strings() -> None:
    d = _base()
    d["novelty"]["window"] = "36h"
    assert manifest_from_dict(d).novelty.window_ns == 36 * NS_PER_HOUR


def test_endowment_section_parses_validates_and_keeps_the_identity_of_worlds_without_it():
    from factorylab.runtime.worlds import NS_PER_DAY, EndowmentSpec

    default = manifest_from_dict(_base())
    assert default.endowment == EndowmentSpec() and "endowment" not in default.canonical_json()
    raw = _base()
    raw["endowment"] = {"locked_micro": 0, "releases": []}
    assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
    raw["endowment"] = {"locked_micro": 6_000_000, "releases": [
        {"at": "7d", "amount_micro": 2_000_000}, {"at": "14d", "amount_micro": 4_000_000}]}
    m = manifest_from_dict(raw)
    assert m.endowment == EndowmentSpec(6_000_000, ((7 * NS_PER_DAY, 2_000_000),
                                                    (14 * NS_PER_DAY, 4_000_000)))
    assert m.manifest_hash() != default.manifest_hash()
    for bad, message in (
        ({"locked_micro": 6_000_000, "releases": [{"at": "7d", "amount_micro": 1}]}, "sum"),
        ({"locked_micro": 20_000_000, "releases": [{"at": "7d", "amount_micro": 20_000_000}]},
         "exceed"),
        ({"locked_micro": 2, "releases": [{"at": "7d", "amount_micro": 1},
                                          {"at": "6d", "amount_micro": 1}]}, "ascending"),
        ({"locked_micro": 1, "releases": [{"at": -1, "amount_micro": 1}]}, "nonnegative"),
        ({"locked_micro": 0, "releases": [{"at": "1d", "amount_micro": 0}]}, "positive"),
        ({"locked_micro": 1, "releases": [{"at": "1d", "amount_micro": 1.0}]}, "integer"),
        ({"locked_micro": 1, "releases": [{"at": "1d", "amount_micro": 1, "x": 1}]}, "exactly"),
        ({"locked_micro": "1", "releases": [{"at": "1d", "amount_micro": 1}]}, "integer"),
        ({"locked_micro": 1, "releases": [{"at": "1d", "amount_micro": 1}], "extra": 1}, "only"),
    ):
        raw = _base()
        raw["endowment"] = bad
        with pytest.raises(ValueError, match=message):
            manifest_from_dict(raw)


def test_notes_rent_rate_defaults_keeps_the_legacy_key_readable_and_hashes_stably():
    from factorylab.runtime.notes import NS_PER_DAY, NotesSpec, accrue

    default = manifest_from_dict(_base())
    assert default.notes.micro_per_byte_day == "0.04" and default.notes.byte_window_micro == 1
    assert "micro_per_byte_day" not in default.canonical_json()
    # The default rate makes the 256 KiB cap cost about one cent a day.
    micro, carry = accrue({"bytes": 262144, "rent_ns": 0, "rent_carry": 0}, NS_PER_DAY,
                          default.notes)
    assert micro == 10485 and carry > 0
    for section in ({"byte_window_micro": 1}, {"micro_per_byte_day": "0.04"},
                    {"micro_per_byte_day": "0.040"}):
        raw = _base()
        raw["notes"] = section
        assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
    raw = _base()
    raw["notes"] = {"byte_window_micro": 3, "micro_per_byte_day": "1"}
    m = manifest_from_dict(raw)
    assert m.notes == NotesSpec(byte_window_micro=3, micro_per_byte_day="1")
    assert m.manifest_hash() != default.manifest_hash()
    assert NotesSpec(micro_per_byte_day=2).micro_per_byte_day == "2"
    for value in (0, "0", "-1", 0.04, "abc", "NaN", "Infinity", None):
        raw = _base()
        raw["notes"] = {"micro_per_byte_day": value}
        with pytest.raises(ValueError, match="notes"):
            manifest_from_dict(raw)


def test_cli_manifest_command(capsys) -> None:
    assert main(["manifest", "--world", "scripted"]) == 0
    out = capsys.readouterr().out
    assert '"name": "scripted"' in out and '"hash"' in out


def test_cli_probe_refuses_world_without_live_venue(capsys) -> None:
    assert main(["probe", "--world", "scripted"]) == 2


@pytest.mark.network
def test_cli_probe_testnet(capsys) -> None:
    assert main(["probe", "--world", "testnet"]) == 0
    assert "hyperliquid-testnet" in capsys.readouterr().out


def test_prices_section_defaults_and_validation() -> None:
    m = manifest_from_dict(_base())
    assert (m.prices.eta, m.prices.decay, m.prices.lambda_max, m.prices.min_window_events) == (
        0.5,
        0.1,
        1.0,
        1,
    )
    d = _base()
    d["prices"] = {"eta": 0.25, "decay": 0.05, "lambda_max": 2, "min_window_events": 3}
    m2 = manifest_from_dict(d)
    assert m2.prices.lambda_max == 2.0 and m2.prices.min_window_events == 3
    assert (
        m2.manifest_hash() != m.manifest_hash()
    )  # the controller's parameters are part of the seed
    for bad in ({"eta": 0}, {"decay": -1}, {"lambda_max": 0}, {"min_window_events": 0}):
        d = _base()
        d["prices"] = bad
        with pytest.raises(ValueError):
            manifest_from_dict(d)


def test_clock_bounds_seed_validation_and_hash():
    d = _base()
    d["clock"] = {"min_tick": "10s"}
    d["tick_interval"] = "10s"
    m = manifest_from_dict(d)
    assert m.clock.min_tick_ns == 10_000_000_000
    assert m.max_tick_ns == 1200_000_000_000
    assert "max_tick" not in m.canonical_json()
    d["tick_interval"] = "20m"
    assert manifest_from_dict(d).tick_interval_ns == m.max_tick_ns
    for invalid in ["9s", "1201s"]:
        d["tick_interval"] = invalid
        with pytest.raises(ValueError, match="tick_interval"):
            manifest_from_dict(d)
    d["tick_interval"] = "10s"
    d["clock"]["min_tick"] = "5s"
    assert manifest_from_dict(d).manifest_hash() != m.manifest_hash()
    d["clock"]["max_tick"] = "20m"
    with pytest.raises(ValueError, match="derived"):
        manifest_from_dict(d)


def test_clock_seed_intervals_remain_unchanged():
    assert load_manifest("scripted").tick_interval_ns == 1_000_000_000
    assert load_manifest("scripted-crash").tick_interval_ns == 1_000_000_000
    # Ten minutes (launch decision, docs/launch-decisions.md): the same number of
    # thoughts per dollar as a faster tick, each worth more, and a $90 wallet lasts
    # about three weeks before any earnings.
    assert load_manifest("testnet").tick_interval_ns == 600_000_000_000
    assert load_manifest("testnet").evaluation.consequence_backstop_events == 60
    assert load_manifest("testnet").clock.min_tick_ns == 10_000_000_000


@pytest.mark.parametrize("world", ["scripted", "scripted-crash", "testnet"])
def test_damping_and_cadence_are_explicit_manifest_parameters(world):
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    with (WORLDS_DIR / f"{world}.toml").open("rb") as source:
        raw = tomllib.load(source)
    assert raw["prices"]["kappa"] == 0.5
    assert raw["timing"]["cadence_sample"] == 200
    m = _testnet_with_charter() if world == "testnet" else load_manifest(world)
    assert m.prices.kappa == 0.5 and m.timing.cadence_sample == 200


def test_damping_and_cadence_defaults_overrides_and_hashes():
    default = manifest_from_dict(_base())
    assert default.prices.kappa == 0.5 and default.timing.cadence_sample == 200
    for section, key, value in (("prices", "kappa", 0), ("timing", "cadence_sample", 100)):
        raw = _base()
        raw[section] = {key: value}
        changed = manifest_from_dict(raw)
        assert getattr(getattr(changed, section), key) == value
        assert changed.manifest_hash() != default.manifest_hash()


@pytest.mark.parametrize("value", [-1, True, "0.5", float("nan"), float("inf")])
def test_invalid_kappa_is_rejected(value):
    raw = _base()
    raw["prices"] = {"kappa": value}
    with pytest.raises(ValueError, match="kappa"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "200", None])
def test_invalid_cadence_sample_is_rejected(value):
    raw = _base()
    raw["timing"] = {"cadence_sample": value}
    with pytest.raises(ValueError, match="cadence_sample"):
        manifest_from_dict(raw)


def _with_charter():
    from dataclasses import asdict

    from factorylab.charter.charter import seed_charter

    raw = _base()
    raw["charter"] = asdict(seed_charter())
    raw["charter"]["norms"] = list(raw["charter"]["norms"])
    raw["charter"]["cards"] = list(raw["charter"]["cards"])
    return raw


def test_manifest_charter_defaults_and_explicit_hash():
    from factorylab.charter.charter import seed_charter

    default = manifest_from_dict(_base())
    assert default.charter == seed_charter()
    raw = _with_charter()
    assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
    for field, value in (("description", "Population draft"), ("lambda", 0.4)):
        changed = _with_charter()
        changed["charter"]["cards"][0][field] = value
        assert manifest_from_dict(changed).manifest_hash() != default.manifest_hash()


@pytest.mark.parametrize(("field", "value"), [
    ("observation", "missing"), ("acceptable_region", "roughly adequate"),
    ("norm", "unknown"), ("description", None), ("observation", 12),
    ("lambda", -0.1), ("lambda", 1.1), ("lambda", True), ("lambda", "0.5"),
    ("lambda", float("nan")), ("lambda", float("inf")),
])
def test_manifest_charter_rejects_card_field_with_identity(field, value):
    raw = _with_charter()
    raw["charter"]["cards"][0][field] = value
    with pytest.raises(ValueError, match=f"cost_per_return.*{field}"):
        manifest_from_dict(raw)


def test_manifest_charter_duplicate_ids_and_lambda_bounds():
    raw = _with_charter()
    raw["charter"]["cards"].append(raw["charter"]["cards"][0])
    with pytest.raises(ValueError, match="cost_per_return.*id"):
        manifest_from_dict(raw)
    for value in (0, 2):
        raw = _with_charter()
        raw["prices"] = {"lambda_max": 2}
        raw["charter"]["cards"][0]["lambda"] = value
        assert manifest_from_dict(raw).charter_prices == (("cost_per_return", value),)


@pytest.mark.parametrize("norms", [None, [], "a norm", [1], [""]])
def test_manifest_charter_requires_norm_list(norms):
    raw = _with_charter()
    raw["charter"]["norms"] = norms
    with pytest.raises(ValueError, match="norms"):
        manifest_from_dict(raw)


def test_example_manifest_and_cli_resolve_population_charter(capsys):
    import json
    from dataclasses import replace

    example = load_manifest("edition1-example")
    base = load_manifest("testnet")
    # The launch world's cadence decision is the launch world's: edition 1 is
    # re-drafted with the actual roster and takes a cadence then. Everything else
    # about the two files is still the same file.
    # The funded draft seeds BTC and ETH perps and the HYPE/USDC spot pair; testnet
    # keeps PURR/USDC from its rehearsals beside it. HYPE/USDC is the physics of the
    # exit route (docs/launch-decisions.md, "Self-serve gas"); other pairs the
    # population registers itself.
    assert replace(example, name=base.name, charter=base.charter, treasury=base.treasury,
                   charter_explicit=base.charter_explicit, exchange=base.exchange,
                   charter_content_sha256=base.charter_content_sha256,
                   tick_interval_ns=base.tick_interval_ns, evaluation=base.evaluation) == base
    assert example.tick_interval_ns == 600_000_000_000
    assert example.evaluation.consequence_backstop_events == 60
    assert {c.id: c.observation for c in example.charter.cards} == {
        "model_cost_efficiency": "cost_per_return", "revision_rate": "revision_rate",
        "well_formed_rate": "well_formed_rate",
        "verdict_mean_score": "verdict_mean", "forecast_skill": "forecast_skill",
    }
    assert main(["manifest", "--world", "edition1-example"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["charter"] == json.loads(example.canonical_json())["charter"]
    assert out["charter"]["edition"] == 1


@pytest.mark.parametrize("field", ["answers_for"])
def test_manifest_card_requires_explicit_role(field):
    raw = _with_charter()
    del raw["charter"]["cards"][0][field]
    with pytest.raises(ValueError, match="cost_per_return.*answers_for"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("value", [True, None, "", "judge", "unknown"])
def test_manifest_card_rejects_unknown_role(value):
    raw = _with_charter()
    raw["charter"]["cards"][0]["answers_for"] = value
    with pytest.raises(ValueError, match="cost_per_return.*answers_for"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("section,field,value", [
    ("novelty", "trials", True), ("novelty", "trials", 0),
    ("novelty", "trials", 1.5), ("novelty", "max_lifetime_windows", 0),
    ("novelty", "trial_invocations", 3), ("committee", "min_settled", 0),
    ("evaluation", "adversarial_share", 1.5), ("evaluation", "sibling_share", -0.1),
    ("evaluation", "sampling_step", 2), ("evaluation", "sampling_cap", 0.2),
    ("committee", "min_settled", False), ("immune", "k", 1), ("immune", "k", 3.0),
    ("immune", "bins", 1), ("immune", "tv_threshold", -1),
    ("immune", "gamma_max", 1.1), ("immune", "gap_threshold", float("nan")),
    ("immune", "gain_step", True), ("immune", "decay_step", 0),
])
def test_fidelity_casts_reject_invalid_values(section, field, value):
    raw = _base()
    raw.setdefault(section, {})[field] = value
    with pytest.raises(ValueError, match=rf"{section}\.{field}"):
        manifest_from_dict(raw)


def test_fidelity_casts_are_explicit_in_all_worlds_and_hashed():
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    for path in WORLDS_DIR.glob("*.toml"):
        raw = tomllib.loads(path.read_text())
        if raw.get("exchange", {}).get("kind") == "hyperliquid" and "charter" not in raw:
            raw["charter"] = _with_charter()["charter"]
        manifest = manifest_from_dict(raw)
        assert raw["novelty"]["trials"] == manifest.novelty.trials == 3
        assert raw["novelty"]["max_lifetime_windows"] == manifest.novelty.max_lifetime_windows
        for key in ("adversarial_share", "sibling_share", "sampling_step", "sampling_cap"):
            assert raw["evaluation"][key] == getattr(manifest.evaluation, key)
        assert raw["committee"]["min_settled"] == manifest.committee.min_settled == 5
        for key in ("k", "bins", "tv_threshold", "gap_threshold", "gain_step", "gamma_max",
                    "decay_step"):
            assert raw["immune"][key] == getattr(manifest.immune, key)
        raw["immune"]["k"] += 1
        assert manifest.manifest_hash() != manifest_from_dict(raw).manifest_hash()


def test_forward_fee_headroom_and_the_wait_bound_leave_manifest_identities_unchanged():
    """Hashes recorded at 25d2750 on feat/self-serve-gas, before ``max_forward_fee_usd``
    defaulted to "0.30" and ``treasury.forward_wait_windows`` existed. Both defaults are
    dropped from the canonical JSON, so an unchanged manifest keeps its identity. Testnet's
    one deliberate identity change in that pass is the seeded HYPE/USDC pair (the physics
    of the exit route, docs/launch-decisions.md), so it is compared with that seed removed."""
    import json
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    assert load_manifest("scripted").manifest_hash() == (
        "f3bf34acc6aa2e9a530bd176453c1968e526b4083f7f3dedbea59636bdad2dd8")
    raw = tomllib.loads((WORLDS_DIR / "testnet.toml").read_text())
    assert raw["venue"]["spot_pairs"] == ["PURR/USDC", "HYPE/USDC"]
    raw["venue"]["spot_pairs"] = ["PURR/USDC"]
    assert manifest_from_dict(raw).manifest_hash() == (
        "25d4e21e7161d2075bd8fa66b640842fba102970cad7511d14d2e8a697ac9a62")
    for world in ("scripted", "testnet"):
        raw = tomllib.loads((WORLDS_DIR / f"{world}.toml").read_text())
        default = manifest_from_dict(raw)
        treasury = json.loads(default.canonical_json())["treasury"]
        assert not {"max_forward_fee_micro", "forward_wait_windows"} & set(treasury)
        assert default.treasury.max_forward_fee_micro == 300_000
        assert default.treasury.forward_wait_windows == 2
        raw.setdefault("treasury", {}).update(max_forward_fee_usd="0.30", forward_wait_windows=2)
        assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
        raw["treasury"]["max_forward_fee_usd"] = "0.20"
        assert manifest_from_dict(raw).manifest_hash() != default.manifest_hash()


def _testnet_with_charter():
    """Inspect live settings with an explicit test-only charter, without changing any manifest."""
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    raw = tomllib.loads((WORLDS_DIR / "testnet.toml").read_text())
    raw["charter"] = _with_charter()["charter"]
    return manifest_from_dict(raw)


def test_manifest_card_scope_must_name_a_kind_a_seed_assembly_emits():
    """A launch card cannot hold to account work no seed assembly ever produces."""
    raw = _with_charter()
    raw["charter"]["cards"][0]["answers_for"] = "WeatherForcast"  # a typo for the kind below
    with pytest.raises(ValueError, match="cost_per_return answers_for: unregistered emitted kind"):
        manifest_from_dict(raw)
    raw["assemblies"] = [{"id": "a", "model_id": "m", "emits": ["WeatherForecast"],
                          "schemas": {"WeatherForecast": {"type": "object"}}}]
    with pytest.raises(ValueError, match="unregistered emitted kind"):
        manifest_from_dict(raw)
    raw["charter"]["cards"][0]["answers_for"] = "WeatherForecast"
    assert manifest_from_dict(raw).charter.cards[0].answers_for == "WeatherForecast"


def test_edition2_testnet_manifest_carries_the_edition2_physics_and_hashes_stably():
    """Edition 2 (W7): the draft the funded manifest is derived from after ratification.

    30 USD unlocked at genesis, 60 USD released as six weekly 10 USD tranches (C1), the
    one-hour novelty window (never the two-minute rent trap of the compute-continuity
    rehearsal), the eight draft cards verbatim, and a pinned identity so a silent edit
    to the physics is a test failure, not a surprise at ratification.
    """
    import tomllib

    from factorylab.runtime.worlds import NS_PER_DAY, WORLDS_DIR

    m = load_manifest("edition2-testnet")
    assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
    assert m.exchange.coins == ("BTC", "ETH")
    assert m.exchange.spot_pairs == ("PURR/USDC", "HYPE/USDC")
    assert m.exchange.client_namespace is None  # drawn by `scripts/rehearsal.py prepare`
    assert m.tick_interval_ns == 600_000_000_000
    # C1: the endowment sums. 90 USD in, 30 unlocked at genesis, the rest on days 7..42.
    assert m.initial_balance_micro == 90_000_000
    assert m.endowment.locked_micro == 50_000_000
    assert m.initial_balance_micro - m.endowment.locked_micro == 40_000_000
    assert m.endowment.releases == tuple(
        (day * NS_PER_DAY, 10_000_000) for day in (7, 14, 21, 28, 35))
    assert sum(amount for _, amount in m.endowment.releases) == m.endowment.locked_micro
    assert m.endowment.base_share == 0.8
    # The one-hour window, and the rent, blame and program prices at their stated values.
    assert m.novelty.window_ns == NS_PER_HOUR
    assert m.notes.micro_per_byte_day == "0.04"
    assert m.prices.min_blame_share == 0.1 and m.prices.program_micro_per_call == 50
    # The trial: three calls of the cheapest seat (eval-b, qwen/qwen3.7-flash at 3000
    # tokens) at the meter's ceiling for the largest measured request, 3 * 5,811 = 17,433.
    assert m.evaluation.trial_amount_micro == 50_000
    cheapest = next(a for a in m.assemblies if a.id == "eval-b")
    assert cheapest.model_id == "qwen/qwen3.7-flash" and cheapest.max_tokens == 3000
    ceiling = m.price_table().cost(cheapest.model_id, int((419 + 120_000) * 1.5) + 64, 3000)
    assert ceiling == 5_811 and 3 * ceiling <= m.evaluation.trial_amount_micro
    # The same nine seats and ten models as the compute-continuity roster.
    continuity = load_manifest("compute-continuity-testnet")
    # Same roster as the compute-continuity manifest except the observer, moved to
    # DeepSeek 4.1 flash after the calibration (docs/audits/v5/rehearsal.md).
    changed = {(a.id, a.model_id) for a in m.assemblies} ^ {
        (a.id, a.model_id) for a in continuity.assemblies}
    assert changed == {("seed-observer", "deepseek/deepseek-v4.1-flash"),
                       ("seed-observer", "z-ai/glm-5.3-flash")}
    assert m.models == continuity.models
    assert m.treasury.reserve_address == continuity.treasury.reserve_address
    # The three draft cards and five norms, verbatim: every frugality card is gone because
    # the wallet prices cost; fidelity gives judges the basis to mark down hollow compliance.
    assert [c.id for c in m.charter.cards] == [
        "card-consequence-paid-off", "card-forecast-skill", "censorship-bound"]
    assert m.charter.norms == ("consequential usefulness", "epistemic integrity",
                               "durable agency", "bounded reciprocity", "fidelity")
    draft = tomllib.loads((WORLDS_DIR.parent / "docs/charter/edition2-draft.toml").read_text())
    raw = tomllib.loads((WORLDS_DIR / "edition2-testnet.toml").read_text())
    # The manifest carries the ratification's provenance digests beside the cards it
    # loads; the cards and norms themselves are the draft verbatim.
    provenance = {k: raw["charter"].pop(k) for k in ("ratified_sha256", "roster_sha256")}
    assert raw["charter"] == draft["charter"]
    assert provenance == {
        "ratified_sha256": "a7f105eefd65ac70904b04b3389840841e6751b18bde3c5cc2262622b2b6be39",
        "roster_sha256": "e0d1d9fbb937952845a967b8fa5d1978e6882b940f685ad4b0016085cbb90f2a"}
    # Ratified 15 September: the loader carries the digests and they equal the loaded cards.
    assert m.charter_ratified_sha256 == provenance["ratified_sha256"]
    assert m.charter_roster_sha256 == provenance["roster_sha256"]
    assert m.charter_content_sha256 == m.charter_ratified_sha256
    assert m.manifest_hash() == (
        "b184b1d8dc55daf56978ea51181be0d06e59493bef2727d97b2f67717efdbf8b")


def test_edition3_testnet_manifest_identity_is_pinned_beside_edition_2():
    """Edition 3 (C5): the first world's manifest, pinned the way edition 2's is.

    The roster, the money and the kill contract are checked in `tests/audit/test_e3_world.py`;
    this is the identity itself, beside the edition 2 pin, so a silent edit to either world's
    physics is a test failure rather than a surprise at ratification.
    """
    m = load_manifest("edition3-testnet")
    assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
    assert len(m.assemblies) == 9 and m.kill.wind_down is True
    assert m.manifest_hash() == (
        "776d2ecf38aa2cb762c761a894c72c3e2a75d22e7cbcbc01839e664c97c258ea")
    # Edition 3's new keys are hash-neutral at their defaults: edition 2 is untouched.
    assert load_manifest("edition2-testnet").manifest_hash() == (
        "b184b1d8dc55daf56978ea51181be0d06e59493bef2727d97b2f67717efdbf8b")


def test_prices_min_blame_share_is_optional_bounded_and_absent_from_the_hash_at_default():
    """Edition 2 (C6): the generic blame floor is a manifest price with a default of 0.1."""
    import json
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    default = manifest_from_dict(raw)
    assert default.prices.min_blame_share == 0.1
    assert "min_blame_share" not in json.loads(default.canonical_json())["prices"]
    assert default.manifest_hash() == (
        "f3bf34acc6aa2e9a530bd176453c1968e526b4083f7f3dedbea59636bdad2dd8")
    raw.setdefault("prices", {})["min_blame_share"] = 0.1
    assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
    raw["prices"]["min_blame_share"] = 0.25
    explicit = manifest_from_dict(raw)
    assert explicit.prices.min_blame_share == 0.25
    assert explicit.manifest_hash() != default.manifest_hash()
    for bad in (-0.1, 1.5, "0.1", True):
        raw["prices"]["min_blame_share"] = bad
        with pytest.raises(ValueError, match="min_blame_share"):
            manifest_from_dict(raw)


def test_promise_resolution_is_explicit_and_hash_neutral_at_its_default():
    default = manifest_from_dict(_base())
    assert default.committee.promise_resolution == 0.01
    assert "promise_resolution" not in default.canonical_json()
    raw = _base()
    raw["committee"] = {"promise_resolution": 0.01}
    assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
    raw["committee"] = {"promise_resolution": 0.05}
    assert manifest_from_dict(raw).manifest_hash() != default.manifest_hash()
    for bad in (0, -0.1, "0.01", True, float("inf")):
        raw["committee"] = {"promise_resolution": bad}
        with pytest.raises((ValueError, TypeError)):
            manifest_from_dict(raw)
