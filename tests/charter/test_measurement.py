"""Edition 2 (C6): the measurement vocabulary the charter loader reads.

``cost_per_attempt`` joins the catalogue beside ``cost_per_return``, which keeps
its meaning for every ratified charter; ``tool_calls`` is a mean per return.
"""


import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import (
    CardSamples,
    measure_card,
)
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.runtime.pricing import MeasureWindow

NORM = "care with scarce resources"


def _card(observation, region="at most 5000", *, kind="returns", n=10, per="role"):
    return MetricCard("card", NORM, "d", "u", MetricWindow(kind, n, per), region, observation,
                      "producer")


def _samples(rows, *, window=1):
    samples = CardSamples()
    for i, (cost, ok, calls) in enumerate(rows):
        samples.returned(handle=f"h{i}", assembly="asm", role="producer", window=window,
                         ret=Return(f"h{i}", {}, cost, "ok" if ok else "failed",
                                    tool_calls=tuple({"tool": "t", "args": {}}
                                                     for _ in range(calls))))
    return samples


def test_cost_per_attempt_counts_every_response_and_cost_per_return_only_successes():
    samples = _samples([(1_000, True, 0)] * 9 + [(100_000, False, 0)])
    assert measure_card(_card("cost_per_return"), samples) == {"producer": pytest.approx(1_000)}
    assert measure_card(_card("cost_per_attempt"), samples) == {
        "producer": pytest.approx(10_900)}
    # Over whole closed windows the same rows are read from the retained samples.
    window = MeasureWindow(1, 1, costs=[1_000] * 9, invocations=10, ok=9)
    samples.closed(window)
    assert measure_card(_card("cost_per_attempt", kind="windows", n=1, per=None), samples) == {
        "all": pytest.approx(10_900)}
    assert measure_card(_card("cost_per_return", kind="windows", n=1, per=None), samples) == {
        "all": pytest.approx(1_000)}


def test_global_windows_merge_flat_counters_and_nested_scores():
    # Edition 6's own charter (23 September 2026) was the first with a global windows
    # card over n > 1; merging calls_by_family, a flat {family: count}, crashed the world.
    samples = CardSamples()
    samples.closed(MeasureWindow(1, 1, calls_by_family={"gpt": 2, "xiaomi": 2}))
    samples.closed(MeasureWindow(2, 1, calls_by_family={"gpt": 4}))
    card = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at most 1",
                      "family_concentration", "all")
    assert measure_card(card, samples) == {"all": pytest.approx(6 / 8)}


def test_a_per_window_total_over_several_windows_is_their_mean():
    # The #139 review: burn_per_window over 25 windows compared their total with a
    # per-window ceiling. A total is averaged over the selected windows; a rate is not.
    samples = CardSamples()
    for index in (1, 2):
        samples.closed(MeasureWindow(index, 1, compute_spend_micro=1_000_000, invocations=4,
                                     ok=2 * index))
    burn = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at most 1",
                      "burn_per_window", "all")
    assert measure_card(burn, samples) == {"all": pytest.approx(1_000_000)}
    rate = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at least 0",
                      "well_formed_rate", "all")
    assert measure_card(rate, samples) == {"all": pytest.approx(6 / 8)}


def test_turnover_over_several_windows_is_the_mean_of_each_windows_own_ratio():
    # The #139 review: each window's turnover is over its own starting equity.
    samples = CardSamples()
    samples.closed(MeasureWindow(1, 100, notional_micro=100))
    samples.closed(MeasureWindow(2, 1_000, notional_micro=1_000))
    card = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at most 2",
                      "turnover", "all")
    assert measure_card(card, samples) == {"all": pytest.approx(1.0)}


def test_published_forecast_skill_states_its_score_and_which_sign_beats_the_base_rate():
    """Schematics are public (essay II.I.b): a seat reading the catalogue can tell which
    sign of ``forecast_skill`` beats the base rate. "Brier" alone reads both ways; the
    score here is 1 - (q - y)^2, higher is better."""
    from factorylab.charter.measurement import measurement_catalogue
    from factorylab.runtime.observations import CATALOGUE

    published = next(r for r in measurement_catalogue() if r["id"] == "forecast_skill")
    seed = next(o for o in CATALOGUE if o.id == "forecast_skill")
    for description in (published["description"], seed.description):
        assert "1 - (q - y)^2" in description
        assert "positive when" in description and "beat the base rate" in description


class _Reads:
    """A window that records every field a calculator reads from it."""

    def __init__(self, window):
        object.__setattr__(self, "_window", window)
        object.__setattr__(self, "read", set())

    def __getattr__(self, name):
        self.read.add(name)
        return getattr(self._window, name)


class _Row(dict):
    """A sample row that records every key a calculator reads from it."""

    def __init__(self, row, read):
        super().__init__(row)
        self.read = read

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)


def _full_window():
    """A closed window in which every counter is nonzero, so every branch reads on."""
    return MeasureWindow(
        index=1, equity_start_micro=1_000_000, costs=[10, 20], invocations=3, ok=2,
        notional_micro=500, forecast_skills=[0.1, -0.2], producer_returns=2,
        noop_returns=1, revision_returns=1, registrations=1, registration_rejections=1,
        amendments_proposed=1, amendments_activated=1,
        verdicts={"h": {"a": [0.4], "b": [0.8]}}, consequences_settled=2,
        consequences_paid_off=1, non_acting_outcomes=2, non_acting_informative=1,
        non_acting_paid_off=1, fills=1, realized_pnl_micro=5,
        max_position_notional_micro=100, exposures_settled=2, exposures_won=1,
        meta_verdicts=[0.5], outcomes=2, censored=1, tool_calls=4, market_purchases=1,
        decisions={"h": {"cost": 5}}, compute_spend_micro=30, evaluator_spend_micro=10,
        ews_variance=0.1, ews_autocorrelation=0.2, calls_by_provider={"x": 2},
        calls_by_family={"y": 2}, prompts=2, prompt_bytes=100, you_bytes=10,
        inputs_bytes=20, downstream_read_bytes=40, read_measured=2)


def test_every_published_input_is_exactly_what_its_calculator_reads():
    """Codex on #152 (rule 3): the catalogue published ``forecast_skill`` as averaging
    scored verdicts its calculator never read. Each seed now declares its inputs in code
    (``WINDOW_INPUTS`` for its window calculator, ``ROW_INPUTS`` for its row
    calculator), the catalogue renders its input clause from them alone, and here each
    calculator is run on a window and on rows that record what it reads: the declaration
    is exactly that, so the published text cannot drift from the code."""
    from factorylab.charter.measurement import (
        FORECAST_OBSERVATIONS,
        NOT_A_MEAN,
        RETURN_OBSERVATIONS,
        ROW_INPUTS,
        ROW_KEY_MEANINGS,
        _measure_rows,
        _sample_values,
        measurement_catalogue,
    )
    from factorylab.runtime.observations import (
        CATALOGUE,
        SEED_IDS,
        WINDOW_FIELD_MEANINGS,
        WINDOW_INPUTS,
    )

    assert set(WINDOW_INPUTS) == SEED_IDS
    assert set(ROW_INPUTS) == RETURN_OBSERVATIONS | FORECAST_OBSERVATIONS
    for seed in CATALOGUE:
        window = _Reads(_full_window())
        seed.measure(window)
        assert window.read == set(WINDOW_INPUTS[seed.id]), seed.id
    response = {"cost": 5, "ok": True, "noop": False, "revision": True, "tool_calls": 2,
                "prompt_bytes": 10, "you_bytes": 3, "inputs_bytes": 4, "verdict": 0.5,
                "skill": 0.1, "predicate": "return_paid_off", "status": "settled",
                "subject_acted": True, "y": 1, "excluded": None}
    for observation, declared in ROW_INPUTS.items():
        read = set()
        rows = [_Row(response, read)]
        if observation == "downstream_read_bytes":
            rows.append(_Row({"reading": True, "read_bytes": 7}, read))
        assert _measure_rows(observation, rows) is not None, observation
        assert read == set(declared), observation
        if observation not in NOT_A_MEAN:
            spread = set()
            _sample_values(observation, [_Row(response, spread)])
            assert spread <= set(declared), observation
    published = {row["id"]: row for row in measurement_catalogue()}
    for seed in SEED_IDS:
        row = published[seed]
        assert row["inputs"] == {"windows": list(WINDOW_INPUTS[seed]),
                                 "rows": list(ROW_INPUTS.get(seed, ()))}
        for name in WINDOW_INPUTS[seed]:
            assert f"{name} ({WINDOW_FIELD_MEANINGS[name]})" in row["description"]
        for key in ROW_INPUTS.get(seed, ()):
            assert f"{key} ({ROW_KEY_MEANINGS[key]})" in row["description"]
    skill = published["forecast_skill"]["description"]
    assert "scored verdicts" not in skill and "never included" in skill
    censored = published["censored_share"]["description"]
    assert "censored / outcomes" in censored and "UNRESOLVED_PRICED" in censored


def test_a_card_over_one_closed_window_computes_the_window_published_value():
    """Codex on #152 (rule 3: one metric name, one formula): for every seed, the value a
    card over closed windows computes from one window equals the value ``price.window``
    publishes for that window (``book.value`` on the runtime's window, as
    ``_close_price_window`` computes it), unless the catalogue declares the two differ,
    with the reason (``CLOSED_WINDOW_DIFFERS``). ``forecast_skill`` and
    ``cost_per_attempt`` read the window's sample rows on the card side, so the rows here
    carry what a runtime records beside its counters: a censored forecast (no skill) and
    a ballot whose assembly was unavailable (no invocation)."""
    from dataclasses import replace

    from factorylab.charter.measurement import (
        CLOSED_WINDOW_DIFFERS,
        CardSamples,
        measure_cards,
        window_forecast_skills,
        window_resolved_verdicts,
    )
    from factorylab.runtime.observations import SEED_IDS, SEEDS, seed_book

    samples = CardSamples()
    for skill, verdict in ((0.1, 0.9), (None, None), (-0.2, 0.4)):
        samples.forecasts.append({"handle": "f", "assembly": "e", "role": "evaluator",
                                  "window": 1, "skill": skill, "verdict": verdict,
                                  "predicate": "return_paid_off", "y": 1,
                                  "status": "settled" if skill is not None else "censored",
                                  "excluded": None})
    for cost, invoked in ((5, True), (0, True), (0, True), (0, False)):
        samples.returns.append({"handle": "h", "assembly": "a", "role": "producer",
                                "window": 1, "cost": cost, "ok": invoked, "invoked": invoked,
                                "noop": False, "revision": False, "tool_calls": 0})
    # The window the runtime closes over those rows: its forecast skills are the window's
    # own rows, and its three invocations spent what their responses cost.
    window = replace(_full_window(), compute_spend_micro=5,
                     forecast_skills=window_forecast_skills(samples, 1),
                     resolved_verdicts=window_resolved_verdicts(samples, 1))
    book = seed_book()
    published = {seed: book.value(SEEDS[seed], window) for seed in SEED_IDS}
    cards = [MetricCard(id=f"card-{seed}", norm="n", description="d",
                        units=SEEDS[seed].units, window=MetricWindow("windows", 1, None),
                        acceptable_region="at most 1", observation=seed, answers_for="all")
             for seed in sorted(SEED_IDS)]
    carded = measure_cards(cards, samples, window, observations=book)
    assert published["forecast_skill"] == carded["card-forecast_skill"] == pytest.approx(-0.05)
    assert published["cost_per_attempt"] == carded["card-cost_per_attempt"] == 5 / 3
    assert (published["resolved_verdict_mean"] == carded["card-resolved_verdict_mean"]
            == pytest.approx(0.65))
    for seed in SEED_IDS:
        if seed in CLOSED_WINDOW_DIFFERS:
            continue
        assert carded.get(f"card-{seed}") == published[seed], seed


def test_a_card_over_returns_or_forecasts_computes_what_a_window_of_the_same_responses_does():
    """Codex on #152: the same set of responses gives the same value by every path. A
    card over returns or forecasts (the row calculators) and the window calculator on
    those rows' window facts (``scope_facts``, the kernel's own statement of what a
    window of them counts) agree for every row-measured seed, unless ``ROWS_DIFFER``
    declares why not. The rows carry what a runtime records: a response whose prompt
    could not be rendered, a ballot whose assembly was unavailable (no invocation, so no
    response), a reading, a censored forecast, a consequence of a return that did not
    act, and a commitment excluded from its owner's sample."""
    from types import SimpleNamespace

    from factorylab.charter.measurement import (
        RETURN_OBSERVATIONS,
        ROW_INPUTS,
        ROWS_DIFFER,
        CardSamples,
        _rows,
        _selected,
        measure_card,
        scope_facts,
    )
    from factorylab.runtime.observations import SEEDS, seed_book

    samples = CardSamples()
    for i, (cost, ok, noop, revision, calls, prompt, you, inputs, invoked) in enumerate((
            (7, True, True, False, 2, 100, 10, 30, True),
            (3, False, False, True, 0, None, None, None, True),  # could not be rendered
            (5, True, False, False, 1, 80, 0, 20, True),
            (0, False, False, False, 0, None, None, None, False))):  # assembly unavailable
        samples.returns.append({
            "handle": f"h{i}", "assembly": "a", "role": "producer", "window": 1,
            "cost": cost, "ok": ok, "noop": noop, "revision": revision, "tool_calls": calls,
            "prompt_bytes": prompt, "you_bytes": you, "inputs_bytes": inputs,
            "verdict": None, "invoked": invoked})
    samples.readings.append({"handle": "h0", "assembly": "a", "role": "producer",
                             "window": 1, "read_bytes": 40, "reading": True})
    for i, (skill, status, y, acted, excluded) in enumerate((
            (0.2, "settled", 1, True, None), (-0.4, "settled", 0, False, None),
            (None, "censored", None, True, None), (None, "censored", None, True, "external"))):
        samples.forecasts.append({
            "handle": f"f{i}", "assembly": "a", "role": "evaluator", "window": 1,
            "subject_handle": "s", "subject_assembly": "a", "subject_role": "producer",
            "skill": skill, "status": status, "y": y, "subject_acted": acted,
            "predicate": "return_paid_off", "verdict": 0.5 + i / 10, "excluded": excluded})
    window = SimpleNamespace(**scope_facts([{"index": 1, "equity_start_micro": None}],
                                           samples.returns, samples.forecasts,
                                           samples.readings))
    book = seed_book()
    compared = 0
    for observation in sorted(ROW_INPUTS):
        kind = "returns" if observation in RETURN_OBSERVATIONS else "forecasts"
        responses = [row for row in _selected(observation, _rows(samples, kind, observation))
                     if not row.get("reading")]
        card = MetricCard(id=f"card-{observation}", norm="n", description="d",
                          units=SEEDS[observation].units,
                          window=MetricWindow(kind, len(responses), None),
                          acceptable_region="at most 1", observation=observation,
                          answers_for="all")
        carded = measure_card(card, samples, book).get("all")
        if observation in ROWS_DIFFER:
            continue
        assert carded is not None and carded == SEEDS[observation].measure(window), observation
        compared += 1
    assert compared == len(ROW_INPUTS) - len(ROWS_DIFFER)


@pytest.mark.parametrize("window", [MetricWindow("forecasts", 5, "assembly"),
                                    MetricWindow("returns", 5, "role"),
                                    MetricWindow("windows", 5, "role")])
@pytest.mark.parametrize("name", ["verdict_mean", "verdict_std"])
def test_a_card_naming_delivered_verdicts_over_a_scope_is_refused_and_pointed_to_its_name(
        name, window):
    """Codex on #152: ``verdict_mean`` and ``verdict_std`` are the verdicts delivered in
    whole closed windows. A card over forecasts, returns or a scope asks for the verdict
    attached to each resolved forecast, which is ``resolved_verdict_mean`` or
    ``resolved_verdict_std``: it is refused at load, and the refusal names the new name.
    The same card under the new name loads, and the whole-window card still does."""
    from dataclasses import replace

    from factorylab.charter.measurement import preflight_card

    card = MetricCard(id="c", norm="n", description="d", units="score", window=window,
                      acceptable_region="at least 0.5", observation=name, answers_for="all")
    with pytest.raises(ValueError, match=f"is resolved_{name}"):
        preflight_card(card)
    if window.kind != "returns":
        preflight_card(replace(card, observation=f"resolved_{name}"))
    preflight_card(replace(card, window=MetricWindow("windows", 5, None)))
