"""Closed, disjoint sequence windows retain unsupported observations as None."""

from copy import deepcopy
from math import isfinite
from statistics import fmean, pstdev

from factorylab.kernel.events import EventKind

type Profile = dict[str, float | None]

CHANNELS = ("verdict", "conformity", "fast", "consequence", "exposure")


def number(value: object) -> float | None:
    """Return finite numeric observations as floats, excluding booleans."""
    if type(value) not in (int, float):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if isfinite(value) else None


def mean(values: list[float]) -> float | None:
    """Unsupported means remain None rather than becoming artificial zeroes."""
    return fmean(values) if values else None


def ordered(items: list[dict]) -> list[dict]:
    """Return sequence-sorted references, rejecting missing or duplicate sequence numbers."""
    if any(type(item.get("seq")) is not int or item["seq"] < 0 for item in items):
        raise ValueError("items require nonnegative integer seq values")
    result = sorted(items, key=lambda item: item["seq"])
    if any(a["seq"] == b["seq"] for a, b in zip(result, result[1:], strict=False)):
        raise ValueError("duplicate item sequence")
    return result


def card_names(items: list[dict]) -> list[str]:
    """Card dimensions include every price.window value name, in lexical order."""
    immune = [item for item in items if item.get("kind") == "immune.window"]
    if immune:
        return sorted({name for item in immune for name in item.get("regions", {})})
    return sorted(
        {
            name
            for item in items
            if item.get("kind") == "price.window"
            for name in item.get("values", {})
        }
    )


def profile(items: list[dict], cards: list[str]) -> Profile:
    """Scores use settled returns only; disagreement uses within-window raw Verdicts.

    Each judged return contributes equally, only when at least two evaluator
    handles judge it. Repeated events from one handle retain its latest verdict.
    Counts are floats for the observer profile; wallet accounting is untouched.
    Card/profile name collisions are rejected instead of overwriting evidence.
    """
    names = (*CHANNELS, "noop_share", "registrations", "revision", "disagreement", "balance",
             "paid_off", "realized_pnl")
    if set(cards).intersection(names):
        raise ValueError("card name collides with a profile field")
    result = dict.fromkeys((*CHANNELS, *cards))
    scores = {channel: [] for channel in CHANNELS}
    decisions = []
    judged = {}
    registrations = 0
    revision = 0.0
    paid_off = realized_pnl = None
    balance = None
    for item in items:
        kind = item.get("kind", "")
        if kind == "decision.settle":
            ret = item.get("return", {})
            score = number(ret.get("score"))
            if ret.get("status") == "settled" and ret.get("channel") in scores:
                if score is not None:
                    scores[ret["channel"]].append(score)
        elif kind == "decision.open":
            decisions.append(item["propensity"]["chosen"] == "NOOP")
        elif kind == "price.window":
            observations = item.get("observations", {})
            revision = number(observations.get("revision_rate", 0.0))
            paid_off = number(observations.get("consequence_paid_off_rate"))
            realized_pnl = number(observations.get("realized_pnl_usd"))
            for card in cards:
                result[card] = number(item.get("values", {}).get(card))
        elif kind == "event":
            event = item.get("event", {})
            if event.get("kind") == EventKind.REGISTERED:
                registrations += 1
            elif event.get("kind") == EventKind.VERDICT:
                payload = event.get("payload", {})
                score = number(payload.get("verdict"))
                about, evaluator = payload.get("about_handle"), payload.get("evaluator_handle")
                if about is not None and evaluator is not None and score is not None:
                    judged.setdefault(about, {})[evaluator] = score
        elif kind.startswith("wallet.") and "balance_after" in item:
            balance = number(item["balance_after"])
    result.update({channel: mean(values) for channel, values in scores.items()})
    result.update(
        noop_share=mean(decisions),
        registrations=float(registrations),
        revision=revision,
        paid_off=paid_off,
        realized_pnl=realized_pnl,
        disagreement=mean([pstdev(group.values()) for group in judged.values() if len(group) > 1]),
        balance=balance,
    )
    return result


def windows(items: list[dict], *, window_items: int = 200) -> list[dict]:
    """Every retained item belongs to one closed window; only the final tail is dropped.

    A price.window marker closes its own window inclusively by seq, and its
    increasing window_end_event labels the endpoint. The first prefix is kept.
    Otherwise full item-count blocks are kept. Bounds and window indices are
    inclusive and zero-based. Regions reflect the latest declarations before
    closure, including setup inside the window, as in the runtime. Charter
    edition is strictly the edition at the window's first seq.
    """
    if type(window_items) is not int or window_items < 1:
        raise ValueError("window_items must be a positive integer")
    items = ordered(items)
    cards = card_names(items)
    immune = {i["window"]: i for i in items if i.get("kind") == "immune.window"}
    closings = []
    previous_event = None
    for index, item in enumerate(items):
        if item.get("kind") == "price.window":
            end = item.get("window_end_event")
            if (
                type(end) is not int
                or end < 0
                or (previous_event is not None and end <= previous_event)
            ):
                raise ValueError("price.window endpoints must strictly increase")
            closings.append((index + 1, end))
            previous_event = end
    if not closings:
        closings = [(end, None) for end in range(window_items, len(items) + 1, window_items)]
    result = []
    start = 0
    regions = {}
    edition = 1
    previous_event = None
    for end, end_event in closings:
        group = items[start:end]
        start_edition = edition
        for item in group:
            if item.get("kind") == "charter.activate":
                edition = item["edition"]
                if item["seq"] == group[0]["seq"]:
                    start_edition = edition
            elif item.get("kind") == "price.region":
                regions[item["card_id"]] = deepcopy(item["region"])
            elif item.get("kind") in ("price.removed", "price.region_cleared"):
                regions.pop(item["card_id"], None)
        measured = profile(group, cards)
        marker = group[-1]
        evidence = (immune.get(marker.get("window"))
                    if marker.get("kind") == "price.window" else None)
        live = {}
        if evidence is not None:
            measured.update(deepcopy(evidence["profile"]))
            regions = deepcopy(evidence["regions"])
            start_edition = evidence["charter_edition"]
            # What the live organ recorded beside the profile: the routers' draws, the
            # configuration lifespans, the world's terms and the tick it closed at.
            live = {name: deepcopy(evidence[name]) for name in (
                "frontier_invocation", "lifespans", "terms", "tick") if name in evidence}
        elif "regions" in marker:
            regions = deepcopy(marker["regions"])
            start_edition = marker.get("charter_edition", start_edition)
        result.append(
            {
                "index": len(result),
                "start_seq": group[0]["seq"],
                "end_seq": group[-1]["seq"],
                "start_event": previous_event,
                "end_event": end_event,
                "profile": measured,
                "regions": deepcopy(regions),
                "charter_edition": start_edition,
                **live,
            }
        )
        previous_event = end_event
        start = end
    return result
