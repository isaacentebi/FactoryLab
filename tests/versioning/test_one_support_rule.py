"""One support rule for the failing attractor (R16b-10).

A card enters the failing attractor only when every window of its tail measured it
violating; a card already held stays held across an unmeasured window. The thrash
reading already demands a card's support in every window (``live.dimensions``), so
before this rule a period-2 oscillation sampled [violated, missing, violated] was read
as a persistent failure (stable failure on the producers' card in the gauntlet's i10
world) and as no oscillation: missing evidence is neither compliance nor violation
(essay II.a: a failing attractor is "a robust version"; IV.c: no diagnosis below
sampling noise).
"""

from factorylab.versioning.versions import diagnose

BINS = {"registration_bins": (0.0, 2.0), "revision_bins": (0.0,)}
REGION = {"card:c": {"kind": "min", "lo": 0.5, "hi": None, "scale": 0.5}}
WIDE = {"card_gap": 1.0, "volatility": None, "period": None}


def _window(index, value):
    """A closed window reading card ``c`` at ``value`` (None: not measured)."""
    return {"index": index, "regions": dict(REGION), "frontier_invocation": [],
            "lifespans": [], "profile": {"card:c": value, "registrations": 0,
                                         "revision": 0}}


def _diagnose(values, held=()):
    windows = [_window(i, v) for i, v in enumerate(values, start=1)]
    return diagnose(windows, dict(WIDE), k=3, tv_threshold=0.1, gap_threshold=0.5,
                    held=list(held), **BINS)


def test_violated_missing_violated_does_not_enter_the_attractor():
    result = _diagnose([0.1, None, 0.1])
    assert result["violated_cards"] == [] and not result["flags"]["stable_failure"]


def test_violated_in_every_window_enters_it():
    result = _diagnose([0.1, 0.1, 0.1])
    assert result["violated_cards"] == ["card:c"] and result["flags"]["stable_failure"]


def test_a_held_card_stays_held_across_a_gap():
    for values in ([0.1, None, 0.1], [None, None, 0.1], [None, None, None]):
        result = _diagnose(values, held=("card:c",))
        assert result["violated_cards"] == ["card:c"], values


def test_a_held_card_measured_compliant_leaves():
    result = _diagnose([0.1, None, 0.9], held=("card:c",))
    assert result["violated_cards"] == []
