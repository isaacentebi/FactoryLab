"""Wave 7: context size is published as a fact the factory can propose a metric on.

Essay II.IV.a cedes the metrics layer: the factory proposes metrics for norms, and it
can do so only on a quantity the world publishes. These tests hold the four seed
context observations to their published definitions over known synthetic windows,
per role and per assembly on ``returns`` windows and globally on ``windows``, and
check that a preflighted charter card can bind each one.
"""

from types import SimpleNamespace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import (
    CardSamples,
    fresh_sample,
    measure_card,
    measurement_catalogue,
    preflight_card,
    preflight_measurement,
    scope_facts,
)
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.runtime.loop import Runtime
from factorylab.runtime.observations import observation_for
from factorylab.runtime.pricing import MeasureWindow

NORM = "care with scarce resources"
PROMPT = ("prompt_bytes", "you_bytes", "inputs_bytes")
CONTEXT = (*PROMPT, "downstream_read_bytes")


def _card(observation, *, kind="returns", n=2, per="assembly", answers_for="producer",
          region="at most 50000", interval=None):
    return MetricCard("card", NORM, "d", "u", MetricWindow(kind, n, per, interval), region,
                      observation, answers_for)


def _ret(handle, *, total, you, inputs, status="ok", cost=10):
    return Return(handle, {}, cost, status,
                  prompt_sections={"stable_prefix": total - you - inputs, "you": you,
                                   "inputs": inputs, "total": total})


def _samples():
    """Two producer assemblies and one judge, over windows 1 and 2.

    a: 1_000/100/300 in window 1 and 3_000/300/700 in window 2.
    b: 2_000/200/1_000 in window 2, and a ballot row the runtime rendered no prompt for.
    j: a judge, 5_000/500/4_000 in window 2, whose reading is filed under a's return.
    """
    s = CardSamples()
    s.returned(handle="a1", assembly="a", role="producer", window=1,
               ret=_ret("a1", total=1_000, you=100, inputs=300))
    s.returned(handle="a2", assembly="a", role="producer", window=2,
               ret=_ret("a2", total=3_000, you=300, inputs=700, status="malformed"))
    s.returned(handle="b1", assembly="b", role="producer", window=2,
               ret=_ret("b1", total=2_000, you=200, inputs=1_000))
    s.returned(handle="b2", assembly="b", role="producer", window=2,
               ret=Return("b2", {"reason": "assembly unavailable"}, 0, "failed"))
    s.returned(handle="j1", assembly="j", role="evaluator", window=2,
               ret=_ret("j1", total=5_000, you=500, inputs=4_000))
    # j1 read a1: the reading lands in window 2 under a1's author, never under j.
    s.read(handle="a1", assembly="a", role="producer", window=2, read_bytes=4_000)
    return s


@pytest.mark.parametrize(("observation", "a", "b"), [
    ("prompt_bytes", 2_000.0, 2_000.0), ("you_bytes", 200.0, 200.0),
    ("inputs_bytes", 500.0, 1_000.0),
])
def test_prompt_sizes_are_means_over_measured_prompts_per_assembly_and_role(observation, a, b):
    samples = _samples()
    # b2 was never rendered a prompt: it is not a zero-byte sample and not a response
    # of a prompt-size selection, so b has one measured response and a horizon of two
    # is not yet filled. A failed response's prompt counts.
    assert measure_card(_card(observation), samples) == {"a": pytest.approx(a)}
    assert measure_card(_card(observation, n=1), samples)["b"] == pytest.approx(b)
    per_role = measure_card(_card(observation, n=3, per="role"), samples)
    assert per_role == {"producer": pytest.approx((2 * a + b) / 3)}
    judges = measure_card(_card(observation, n=1, per="role", answers_for="evaluator"), samples)
    assert judges == {"evaluator": pytest.approx(
        {"prompt_bytes": 5_000, "you_bytes": 500, "inputs_bytes": 4_000}[observation])}


def test_prompt_size_of_a_scope_with_no_measured_prompt_is_unmeasured():
    samples = CardSamples()
    samples.returned(handle="x", assembly="x", role="producer", window=1,
                     ret=Return("x", {}, 0, "failed"))
    assert measure_card(_card("prompt_bytes", n=1), samples) == {}


def test_downstream_read_bytes_is_filed_under_the_author_over_its_responses():
    samples = _samples()
    # a's two responses span windows 1-2, where 4_000 reading bytes of its returns were
    # metered: 2_000 per return. b has one rendered response (b2 was rendered no
    # prompt), so a horizon of two is not filled; of one, b1 was read by nobody: zero.
    card = _card("downstream_read_bytes")
    assert measure_card(card, samples) == {"a": pytest.approx(2_000.0)}
    assert measure_card(_card("downstream_read_bytes", n=1), samples) == {
        "a": pytest.approx(4_000.0), "b": 0.0}
    # Per role: the rendered producer responses span windows 1-2 (a1, a2, b1).
    assert measure_card(_card("downstream_read_bytes", n=3, per="role"), samples) == {
        "producer": pytest.approx(4_000 / 3)}
    # The judge that read is charged nothing for reading: no reading is filed under it.
    judges = _card("downstream_read_bytes", n=1, per="assembly", answers_for="evaluator")
    assert measure_card(judges, samples) == {"j": 0.0}


def test_a_reading_is_neither_a_response_nor_a_cost_to_any_other_observation():
    samples = _samples()
    before = {name: measure_card(_card(name, n=2), samples)
              for name in ("cost_per_attempt", "cost_per_return", "well_formed_rate",
                           "tool_calls", "noop_share", *PROMPT)}
    samples.read(handle="b1", assembly="b", role="producer", window=2, read_bytes=99_999)
    for name, value in before.items():
        assert measure_card(_card(name, n=2), samples) == value, name
    # And a reading never supplies the support a short horizon lacks.
    short = CardSamples()
    short.read(handle="z", assembly="z", role="producer", window=1, read_bytes=10)
    assert measure_card(_card("downstream_read_bytes", n=1), short) == {}


def test_a_reading_outside_the_horizons_windows_is_not_selected():
    samples = _samples()
    samples.read(handle="a1", assembly="a", role="producer", window=5, read_bytes=1_000_000)
    assert measure_card(_card("downstream_read_bytes"), samples)["a"] == pytest.approx(2_000.0)


def _closed(samples):
    samples.closed(MeasureWindow(1, 1, invocations=1, ok=1, prompt_bytes=1_000, you_bytes=100,
                                 inputs_bytes=300, downstream_read_bytes=0))
    samples.closed(MeasureWindow(2, 1, invocations=3, ok=2, prompt_bytes=10_000,
                                 you_bytes=1_000, inputs_bytes=5_700,
                                 downstream_read_bytes=4_000))
    return samples


@pytest.mark.parametrize(("observation", "expected"), [
    ("prompt_bytes", 11_000 / 4), ("you_bytes", 1_100 / 4), ("inputs_bytes", 6_000 / 4),
    ("downstream_read_bytes", 4_000 / 4),
])
def test_global_windows_divide_summed_bytes_by_summed_invocations(observation, expected):
    samples = _closed(CardSamples())
    card = _card(observation, kind="windows", n=2, per=None, answers_for="all")
    assert measure_card(card, samples) == {"all": pytest.approx(expected)}
    one = _card(observation, kind="windows", n=1, per=None, answers_for="all")
    latest = {"prompt_bytes": 10_000, "you_bytes": 1_000, "inputs_bytes": 5_700,
              "downstream_read_bytes": 4_000}[observation] / 3
    assert measure_card(one, samples) == {"all": pytest.approx(latest)}


@pytest.mark.parametrize("observation", CONTEXT)
def test_seed_measure_of_one_window_and_its_empty_window(observation):
    seed = observation_for(observation)
    assert seed is not None and not seed.per_window and seed.units.startswith("bytes per")
    window = MeasureWindow(1, 1, invocations=4, prompt_bytes=8, you_bytes=4, inputs_bytes=12,
                           downstream_read_bytes=20)
    assert seed.measure(window) == {"prompt_bytes": 2.0, "you_bytes": 1.0,
                                    "inputs_bytes": 3.0,
                                    "downstream_read_bytes": 5.0}[observation]
    assert seed.measure(MeasureWindow(1, 1)) is None
    # A record closed before these counters existed measures zero bytes, not a crash.
    assert seed.measure(SimpleNamespace(invocations=2)) == 0.0


def test_windows_per_scope_selects_rows_and_readings_of_the_selected_windows():
    samples = _closed(_samples())
    card = _card("downstream_read_bytes", kind="windows", n=1, per="assembly")
    # Window 2 only: a2 and 4_000 bytes read; b1, b2 and nothing read.
    assert measure_card(card, samples) == {"a": pytest.approx(4_000.0), "b": 0.0}
    card = _card("inputs_bytes", kind="windows", n=2, per="assembly")
    assert measure_card(card, samples) == {"a": pytest.approx(500.0), "b": pytest.approx(1_000.0)}


def test_scope_facts_publish_each_scopes_own_bytes_without_any_identity():
    samples = _samples()
    rows = [r for r in samples.returns if r["assembly"] == "a"]
    readings = [r for r in samples.readings if r["assembly"] == "a"]
    facts = scope_facts([{"index": 1}, {"index": 2}], rows, [], readings)
    assert (facts["prompt_bytes"], facts["you_bytes"], facts["inputs_bytes"],
            facts["downstream_read_bytes"], facts["invocations"]) == (4_000, 400, 1_000,
                                                                       4_000, 2)
    assert "a1" not in repr(facts) and "'a'" not in repr(facts)


@pytest.mark.parametrize("observation", CONTEXT)
@pytest.mark.parametrize(("kind", "per", "answers_for"), [
    ("returns", "assembly", "producer"), ("returns", "role", "evaluator"),
    ("returns", "assembly", "meta"), ("windows", None, "all"), ("windows", "role", "producer"),
    ("windows", "assembly", "all"),
])
def test_a_preflighted_card_binds_each_context_observation(observation, kind, per, answers_for):
    card = _card(observation, kind=kind, n=3, per=per, answers_for=answers_for)
    preflight_measurement(card)


def test_an_interval_states_the_error_of_a_mean_so_not_of_reading_bytes_per_return():
    interval = {"level": 0.9, "half_width": 100.0}
    for observation in PROMPT:
        preflight_card(_card(observation, interval=interval))
    with pytest.raises(ValueError, match="not a mean"):
        preflight_card(_card("downstream_read_bytes", interval=interval))


def test_forecast_windows_cannot_measure_prompt_sizes():
    for observation in CONTEXT:
        with pytest.raises(ValueError, match="cannot be measured over forecasts"):
            preflight_measurement(_card(observation, kind="forecasts"))


def test_published_rows_state_what_is_measured_and_prescribe_nothing():
    rows = {row["id"]: row for row in measurement_catalogue()}
    for observation in CONTEXT:
        row = rows[observation]
        assert row["window_kinds"] == ["windows", "returns"] and row["groupable"]
        assert "bytes" in row["description"] and "bytes" in row["units"]
        # A fact, never a strategy (AGENTS.md rule 1): no advice about size.
        for word in ("should", "keep", "avoid", "good", "better", "prefer", "limit",
                     "target", "cheap"):
            assert word not in row["description"].lower(), (observation, word)
            assert word not in observation_for(observation).description.lower()


def test_prune_keeps_the_readings_a_returns_horizon_reads():
    samples = _samples()
    samples.read(handle="a1", assembly="a", role="producer", window=0, read_bytes=7)
    samples.closed(MeasureWindow(2, 1))
    card = _card("downstream_read_bytes")
    samples.prune((card,))
    # The window-2 reading is inside a's horizon (windows 1-2); the window-0 one is not.
    assert [(r["window"], r["read_bytes"]) for r in samples.readings] == [(2, 4_000)]
    samples.prune(())
    assert [r["window"] for r in samples.readings] == [2]  # the retained closed window


class _Reader:
    """The attribute surface ``Runtime._record_reading`` reads, and nothing else."""

    _decision_role = Runtime._decision_role

    def __init__(self):
        self.decision_subjects = {"judge-h": "prod-h", "self-h": "self-h"}
        self.handle_to_assembly = {"prod-h": "producer-1", "judge-h": "judge-1",
                                   "self-h": "producer-1"}
        self.return_kinds = {"prod-h": "ProducerReturn"}
        self.assemblies = {}
        self.window = MeasureWindow(3, 1)
        self.card_samples = CardSamples()


def test_the_runtime_files_a_readers_inputs_under_the_subjects_author():
    rt = _Reader()
    sections = {"stable_prefix": 10, "you": 20, "inputs": 700, "total": 730}
    Runtime._record_reading(rt, "judge-h", Return("judge-h", {}, 1, "ok",
                                                   prompt_sections=sections))
    assert rt.window.downstream_read_bytes == 700
    assert rt.card_samples.readings == [{"handle": "prod-h", "assembly": "producer-1",
                                         "role": "producer", "window": 3, "read_bytes": 700,
                                         "reading": True}]
    # No subject, a subject nobody authored, no rendered prompt, or a decision that is
    # its own subject: nothing is filed.
    for handle, ret in (("other-h", Return("other-h", {}, 1, "ok", prompt_sections=sections)),
                        ("judge-h", Return("judge-h", {}, 1, "ok")),
                        ("self-h", Return("self-h", {}, 1, "ok", prompt_sections=sections))):
        Runtime._record_reading(rt, handle, ret)
    rt.decision_subjects["orphan-h"] = "noop-h"
    Runtime._record_reading(rt, "orphan-h", Return("orphan-h", {}, 1, "ok",
                                                    prompt_sections=sections))
    assert rt.window.downstream_read_bytes == 700 and len(rt.card_samples.readings) == 1


def test_a_reading_after_the_authors_latest_response_survives_the_window_floor():
    # PR #143 review: a reading metered after its author's latest response lies outside
    # today's horizon; once the retained-window floor passed it, prune dropped it, and
    # the author's next response opened a horizon spanning a window whose reading was
    # gone, so downstream_read_bytes undercounted.
    samples = CardSamples()
    card = _card("downstream_read_bytes")

    def respond(handle, window):
        samples.returned(handle=handle, assembly="a", role="producer", window=window,
                         ret=_ret(handle, total=10, you=1, inputs=1))

    respond("a1", 1)
    respond("a2", 2)
    samples.read(handle="a2", assembly="a", role="producer", window=4, read_bytes=600)
    for index in (3, 4, 5):
        samples.closed(MeasureWindow(index, 1))
        samples.prune((card,))
    assert [row["window"] for row in samples.readings] == [4]
    respond("a3", 6)
    # The horizon is a2 (window 2) and a3 (window 6): the window-4 reading is in it.
    assert measure_card(card, samples) == {"a": pytest.approx(300.0)}


def test_readings_no_future_horizon_can_select_are_not_retained():
    samples = CardSamples()
    card = _card("downstream_read_bytes")
    for handle, window in (("a1", 1), ("a2", 2)):
        samples.returned(handle=handle, assembly="a", role="producer", window=window,
                         ret=_ret(handle, total=10, you=1, inputs=1))
    for _ in range(1_000):
        samples.read(handle="a1", assembly="a", role="producer", window=3, read_bytes=1)
    samples.read(handle="a2", assembly="a", role="producer", window=8, read_bytes=5)
    for handle, window in (("a3", 6), ("a4", 7)):
        samples.returned(handle=handle, assembly="a", role="producer", window=window,
                         ret=_ret(handle, total=10, you=1, inputs=1))
    samples.closed(MeasureWindow(8, 1))
    samples.prune((card,))
    # The horizon now opens at window 6 and only moves forward: nothing metered before
    # it can be selected again, however many readings there were. The window-8 one
    # stays, both inside the floor and after the latest response.
    assert [(row["window"], row["read_bytes"]) for row in samples.readings] == [(8, 5)]


@pytest.mark.parametrize("observation", CONTEXT)
def test_a_row_no_prompt_was_rendered_for_neither_reprices_nor_evicts(observation):
    # PR #143 review: an assembly-unavailable ballot row carries no prompt bytes. For
    # an answers_for="all" card it was a fresh sample (the PID repriced on the same
    # measurement) and it took a horizon slot (evicting a measured response).
    samples = CardSamples()
    for handle, total in (("p1", 1_000), ("p2", 3_000)):
        samples.returned(handle=handle, assembly="p", role="producer", window=1,
                         ret=_ret(handle, total=total, you=total // 10, inputs=total // 2))
    samples.read(handle="p1", assembly="p", role="producer", window=1, read_bytes=800)
    card = _card(observation, n=2, per=None, answers_for="all")
    before = measure_card(card, samples)
    assert before
    samples.returned(handle="ballot", assembly="gone", role="other", window=2,
                     ret=Return("ballot", {"reason": "assembly unavailable"}, 0, "failed"))
    assert not fresh_sample(card, samples, MeasureWindow(2, 1))
    assert measure_card(card, samples) == before
    # Over whole windows: a window whose only activity is not an invocation (rent, a
    # ballot no assembly answered) rendered no prompt, so it is no new sample either.
    whole = _card(observation, kind="windows", n=1, per=None, answers_for="all")
    idle = MeasureWindow(3, 1, decisions={"rent-h": {"role": "producer", "cost": 5}})
    assert not fresh_sample(whole, samples, idle)
    assert fresh_sample(whole, samples, MeasureWindow(4, 1, invocations=1))
    # A rendered response in the window is new evidence.
    samples.returned(handle="p3", assembly="p", role="producer", window=5,
                     ret=_ret("p3", total=500, you=50, inputs=250))
    assert fresh_sample(card, samples, MeasureWindow(5, 1))
