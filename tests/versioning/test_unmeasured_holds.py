"""An unmeasured card holds its state (wave 16, second addendum, M-6).

Essay II.II.b prices stable failure by its duration. In longrun1 the paid-off card
went unmeasured for a whole diagnosis tail (windows 21-24), left the failing set while
the stable failure persisted, and its duration price restarted from one at window 28.
Missing evidence is neither compliance nor failure: a failing card that goes
unmeasured stays failing, and a card never measured never fails by default.
"""

import json
from pathlib import Path

from factorylab.versioning.versions import diagnose

ROOT = Path(__file__).resolve().parents[2]
SLICE = ROOT / "tests/fixtures/longrun1_gauntlet_slice.json"
BINS = {"registration_bins": (0.0, 2.0), "revision_bins": (0.0,)}
K = 3


def _windows():
    """Each closed window of the slice as the organ reads it: card readings and regions
    from ``price.window``, the frontier from ``immune.window``, the recorded card gap."""
    events = json.loads(SLICE.read_text())
    prices = {e["window"]: e for e in events if e["kind"] == "price.window"}
    immune = {e["window"]: e for e in events if e["kind"] == "immune.window"}
    flagged = {e["window"]: e for e in events if e["kind"] == "pathology.stable_failure"}
    windows = []
    for index in sorted(prices):
        row = prices[index]
        windows.append({
            "index": index,
            "profile": {f"card:{cid}": value for cid, value in row["values"].items()},
            "regions": {f"card:{cid}": region for cid, region in row["regions"].items()},
            "frontier_invocation": immune[index].get("frontier_invocation", []),
            "lifespans": [], "acts": immune[index]["acts"],
            "card_gap": flagged.get(index, {}).get("card_gap"),
            "recorded": flagged.get(index, {}).get("violated_cards"),
        })
    return windows


def _resets(*, hold: bool) -> list[tuple[int, str]]:
    """Diagnose the slice window by window and ratchet as the organ does at its acting
    windows; return each (window, card) whose duration restarted while the attractor
    held and the card had not been measured compliant."""
    windows = _windows()
    held = windows[1]["recorded"]  # the failing set the organ held after window 19
    failing_at_last_act: set[str] = set()
    resets = []
    for i in range(2, len(windows)):
        retained = windows[: i + 1]
        state = {"card_gap": windows[i]["card_gap"]}
        diagnosis = diagnose(retained, state, k=K, tv_threshold=0.2, gap_threshold=0.8,
                             held=held if hold else (), **BINS)
        held = diagnosis["violated_cards"]
        if not windows[i]["acts"] or not diagnosis["flags"]["stable_failure"]:
            continue
        now = set(diagnosis["violated_cards"])
        resets += [(windows[i]["index"], card) for card in sorted(failing_at_last_act - now)
                   if all((w["profile"].get(card) is None) for w in retained[-K:])]
        failing_at_last_act = now
    return resets


def test_the_longrun1_slice_shows_no_duration_reset_while_the_attractor_held():
    assert _resets(hold=True) == []


def test_without_holding_the_unmeasured_card_resets_at_window_24():
    """The negative control is the rule it replaces: left unmeasured, the card falls out."""
    assert _resets(hold=False) == [(24, "card:independent-consequence")]


def test_an_unmeasured_card_never_fails_by_default():
    """I-5: a card no window ever measured never enters the failing set, held or not."""
    regions = {"card:c": {"kind": "min", "lo": 0.5, "hi": None, "scale": 0.5}}
    windows = [{"index": i, "profile": {}, "regions": regions, "frontier_invocation": [],
                "lifespans": []} for i in range(K)]
    diagnosis = diagnose(windows, {"card_gap": None}, k=K, tv_threshold=0.2,
                         gap_threshold=0.8, **BINS)
    assert diagnosis["violated_cards"] == [] and not diagnosis["flags"]["stable_failure"]


def test_a_held_card_measured_compliant_leaves_the_failing_set():
    """Holding is for missing evidence only: a measurement inside the region relieves."""
    regions = {"card:c": {"kind": "min", "lo": 0.5, "hi": None, "scale": 0.5}}
    windows = [{"index": i, "profile": {"card:c": 0.9 if i == K - 1 else None},
                "regions": regions, "frontier_invocation": [], "lifespans": []}
               for i in range(K)]
    diagnosis = diagnose(windows, {"card_gap": None}, k=K, tv_threshold=0.2,
                         gap_threshold=0.8, held=["card:c"], **BINS)
    assert diagnosis["violated_cards"] == []
    unmeasured = [dict(w, profile={}) for w in windows]
    kept = diagnose(unmeasured, {"card_gap": None}, k=K, tv_threshold=0.2,
                    gap_threshold=0.8, held=["card:c"], **BINS)
    assert kept["violated_cards"] == ["card:c"] and kept["unmeasured_held"] == ["card:c"]
    assert kept["flags"]["stable_failure"]  # the attractor holds on held evidence alone


def test_a_card_the_charter_dropped_is_not_held():
    windows = [{"index": i, "profile": {}, "regions": {}, "frontier_invocation": [],
                "lifespans": []} for i in range(K)]
    diagnosis = diagnose(windows, {"card_gap": None}, k=K, tv_threshold=0.2,
                         gap_threshold=0.8, held=["card:gone"], **BINS)
    assert diagnosis["violated_cards"] == [] and not diagnosis["flags"]["stable_failure"]
