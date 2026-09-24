import pytest

from factorylab.runtime.worlds import NS_PER_HOUR, load_manifest, manifest_from_dict


def test_testnet_manifest_is_not_mainnet() -> None:
    m = _testnet_with_charter()
    assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
    ids = {t.id for t in m.models}
    assert {"z-ai/glm-5.3-flash", "qwen/qwen3.8-flash", "openai/gpt-5.6-luna"} <= ids
    assert all(t.provider == "openrouter" for t in m.models)
    assert "z-ai/glm-5.3-flash:online" in m.price_table().prices


def _base() -> dict:
    from tests.seed_charter import seed_charter_table

    return {
        "name": "x",
        "initial_balance_usd": "10",
        "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
        "assemblies": [{"id": "a", "model_id": "m"}],
        "novelty": {"share": 0.1},
        "charter": seed_charter_table(),
        "immune": {"price_step": 0.05},
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
    d["treasury"] = {"cap_window": "36h"}
    d["timing"] = {"world_repricing": "36h"}
    m = manifest_from_dict(d)
    assert m.treasury.cap_window_ns == 36 * NS_PER_HOUR
    assert m.timing.world_repricing_ns == 36 * NS_PER_HOUR


@pytest.mark.parametrize("section,key", [("novelty", "window"),
                                         ("novelty", "max_lifetime_windows"),
                                         ("treasury", "forward_wait_windows")])
def test_a_cast_window_is_refused_because_windows_are_derived(section, key):
    """Time audit T1, T5, T11, T13: no loop period is cast in a manifest (ruling R8)."""
    d = _base()
    d.setdefault(section, {})[key] = "1m" if key == "window" else 2
    with pytest.raises(ValueError, match=rf"{section}\.{key} was"):
        manifest_from_dict(d)


def test_the_load_half_of_the_ratio_rule_refuses_a_horizon_inside_min_ratio_ticks():
    """Time audit T2: a horizon a decision waits on is an outer loop over the tick."""
    for key in ("verdict_timeout_events", "consequence_backstop_events"):
        d = _base()
        d["evaluation"] = {key: 2, "consequence_horizon_ticks": 1}
        with pytest.raises(ValueError, match="at least timing.min_ratio ticks"):
            manifest_from_dict(d)
    d = _base()
    d["treasury"] = {"forward_wait_ticks": 2}
    with pytest.raises(ValueError, match="forward_wait_ticks"):
        manifest_from_dict(d)
    d = _base()
    d["tick_interval"] = "10s"
    d["clock"] = {"min_tick": "10s"}
    d["treasury"] = {"cap_window": "20s"}
    with pytest.raises(ValueError, match="cap_window"):
        manifest_from_dict(d)


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


def test_a_manifest_hashes_what_it_says_and_a_default_is_no_exception():
    """R8 / versioning S1: no key leaves the hash at its default, so the pinned identity
    of the scripted world moved when the shims went, again when the standing committee
    added committee.quorum, norm_house and charter_parent_sha256, again when a card's
    region became typed data beside its holdouts and interval (charter audit P2, M3),
    and again when W4's judges came to accept Exposure (primitive audit F12), and again
    when Wave 5a added the evaluation keys and [chaos] and gave the roster a third
    model family (evaluations C1, M1, P6), and again when a fourth gave a meta a family
    no chain holds (the #132 review), and again when the clock stopped casting windows
    (time audit T1, T5, T13: the novelty window and lifetime left, the treasury caps
    gained their own duration and the forward wait its ticks), and again when thrash
    came to be priced and immune.decay_step left (versioning audit C2), and again when
    novelty.seat_share capped one seat's share of the niche (ruling R5), and again when
    each model came to state how its route carries the contract (models.contract,
    §II.b), and again when the prices that paid no one left (Wave 11: [storage]
    micro_per_byte_day, connectors.call_price_usd, web.call_price_micro,
    polymarket.read_price_usd, tools.population_tool_micro_per_call and
    prices.program_micro_per_call; the wallet moves only when money moves, §II.b,
    §IV.a), and again when the public venue reads gained their per-minute weight
    budget ([venue] public_read_weight_per_minute), and again when that budget came to
    be divided over venue read slots ([venue] max_readers; the population itself has no
    size cap); each time it is a new v0."""
    scripted = load_manifest("scripted")
    assert '"forecast_horizon_events":10' in scripted.canonical_json()
    assert '"chaos":{"connector_timeout":0.0' in scripted.canonical_json()
    assert '"contract":"json_object"' in scripted.canonical_json()
    assert scripted.manifest_hash() == (
        "73195f9247fe2b2f00488d84d49d71cb5f86d4ee35f20ba620f63ec7645230c0"
    )

    implicit = manifest_from_dict(_base())
    explicit_raw = _base()
    explicit_raw["evaluation"] = {"forecast_horizon_events": 10}
    explicit = manifest_from_dict(explicit_raw)
    assert explicit.manifest_hash() == implicit.manifest_hash()

    changed_raw = _base()
    changed_raw["evaluation"] = {"forecast_horizon_events": 11}
    assert manifest_from_dict(changed_raw).manifest_hash() != implicit.manifest_hash()


@pytest.mark.parametrize("key,value", [("producer_feedback", "realized"),
                                       ("producer_feedback", "verdict"),
                                       ("grounded_horizon_ticks", 10),
                                       ("sibling_share", 0.5)])
def test_the_deleted_reward_chain_keys_are_refused_not_ignored(key, value):
    """Ruling R1 deleted the realized feedback mode and the grounded final judge, and
    evaluations U2 the sibling share; R8 refuses a manifest that names physics this
    kernel does not run."""
    raw = _base()
    raw["evaluation"] = {key: value}
    with pytest.raises(ValueError, match=f"evaluation.{key} was removed"):
        manifest_from_dict(raw)


def test_clock_bounds_seed_validation_and_hash():
    """max_tick is derived from the world's repricing period (time audit T7).

    A governance period is at least min_ratio consequence backstops, so a tick is
    admissible while that many ticks fit inside the world's repricing period. A
    world that states no repricing period has no upper bound.
    """
    d = _base()
    d["clock"] = {"min_tick": "10s"}
    d["tick_interval"] = "10s"
    assert manifest_from_dict(d).max_tick_ns is None
    d["timing"] = {"world_repricing": "10h"}  # 36,000 s over 3 x 200 backstop ticks
    m = manifest_from_dict(d)
    assert m.clock.min_tick_ns == 10_000_000_000
    assert m.max_tick_ns == 60_000_000_000
    assert "max_tick" not in m.canonical_json()
    d["tick_interval"] = "1m"
    assert manifest_from_dict(d).tick_interval_ns == m.max_tick_ns
    for invalid in ["9s", "61s"]:
        d["tick_interval"] = invalid
        with pytest.raises(ValueError, match="tick_interval"):
            manifest_from_dict(d)
    d["tick_interval"] = "10s"
    d["clock"]["min_tick"] = "5s"
    assert manifest_from_dict(d).manifest_hash() != m.manifest_hash()
    d["clock"]["max_tick"] = "20m"
    with pytest.raises(ValueError, match="derived"):
        manifest_from_dict(d)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "200", None])
def test_invalid_cadence_sample_is_rejected(value):
    raw = _base()
    raw["timing"] = {"cadence_sample": value}
    with pytest.raises(ValueError, match="cadence_sample"):
        manifest_from_dict(raw)


def _with_charter():
    from dataclasses import asdict

    from tests.seed_charter import seed_charter

    raw = _base()
    raw["charter"] = asdict(seed_charter())
    raw["charter"]["norms"] = list(raw["charter"]["norms"])
    raw["charter"]["cards"] = list(raw["charter"]["cards"])
    return raw


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
    ("immune", "price_step", 0), ("immune", "price_step", 2.0),
    ("immune", "tv_threshold", -1),
    ("immune", "gamma_max", 1.1), ("immune", "gap_threshold", float("nan")),
    ("immune", "gain_step", True), ("immune", "decay_step", 0),
])
def test_fidelity_casts_reject_invalid_values(section, field, value):
    raw = _base()
    raw.setdefault(section, {})[field] = value
    with pytest.raises(ValueError, match=rf"{section}\.{field}"):
        manifest_from_dict(raw)


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


def test_provider_native_completion_mode_preserves_explicit_historical_allowance():
    d = _base()
    d["assemblies"][0]["max_tokens"] = "provider"
    native = manifest_from_dict(d)
    assert native.assemblies[0].max_tokens is None
    d["assemblies"][0]["max_tokens"] = None
    assert manifest_from_dict(d).assemblies[0].max_tokens is None
    d["assemblies"][0]["max_tokens"] = 4096
    assert manifest_from_dict(d).assemblies[0].max_tokens == 4096


def test_a_world_without_a_charter_is_refused():
    """Charter audit S3: the kernel supplies no default charter."""
    raw = _base()
    del raw["charter"]
    with pytest.raises(ValueError, match=r"\[charter\]"):
        manifest_from_dict(raw)


def test_the_ratchet_step_is_stated_and_the_bin_count_is_not_a_key():
    """Versioning S3 and U5: price_step is required, and immune.bins is refused."""
    raw = _base()
    del raw["immune"]["price_step"]
    with pytest.raises(ValueError, match="immune.price_step is required"):
        manifest_from_dict(raw)
    raw = _base()
    raw["immune"]["bins"] = 3
    with pytest.raises(ValueError, match="immune.bins was removed"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("section,table", [
    ("drip", {"amount_usd": "1", "period": "1d", "end": "7d"}),
    ("termination", {"max_events": 100}),
    ("venue", {"collateral_headroom_usd": "0"}),
])
def test_keys_no_world_set_are_refused_not_ignored(section, table):
    """Smuggling D-6: a manifest naming a removed key would describe unrun physics."""
    raw = _base()
    raw[section] = table
    with pytest.raises(ValueError, match="was removed"):
        manifest_from_dict(raw)


def test_a_models_contract_defaults_to_json_object_and_names_its_schema_routes():
    """Chapter II §II.b: how a route carries the contract is a load-time fact."""
    raw = _base()
    assert manifest_from_dict(raw).models[0].contract == "json_object"
    assert manifest_from_dict(raw).schema_contract_models() == frozenset()
    raw["models"][0].update(provider="openrouter", contract="json_schema")
    manifest = manifest_from_dict(raw)
    assert manifest.schema_contract_models() == frozenset({"m"})
    assert manifest.manifest_hash() != manifest_from_dict(_base()).manifest_hash()


@pytest.mark.parametrize("provider,contract", [
    ("openrouter", "json"), ("openrouter", "strict"), ("openrouter", True),
    ("openrouter", None), ("x402", "json_schema"), ("fake", "json_schema"),
])
def test_a_contract_no_route_can_keep_is_refused_at_load(provider, contract):
    raw = _base()
    raw["models"][0].update(provider=provider, contract=contract)
    with pytest.raises(ValueError, match="models.contract"):
        manifest_from_dict(raw)


def test_the_edition6_worlds_carry_the_schema_on_the_probed_routes_alone():
    # DeepSeek left json_schema after the first live capital-loop rehearsal (9 of 11 real
    # contracts malformed under it); Qwen and MiniMax keep it.
    expected = frozenset({"qwen/qwen3.8-flash", "minimax/minimax-m3"})
    for world in ("edition6-testnet-rehearsal", "edition6-capital-loop"):
        assert load_manifest(world).schema_contract_models() == expected
