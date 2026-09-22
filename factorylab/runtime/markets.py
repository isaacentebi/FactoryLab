"""The charter's markets, in a running world: posted λ and conditional forecasts on motions.

Essay II.IV.a. λ "reaches the committee as a speculative price posted by the
factory", and constraint determination "might be well served by conditional
prediction markets" in which "any representative error that misprices the marginal
worth of a constraint is penalized through the conventional reward channel".

Both markets are declared fields of any ordinary return (``shadow_prices`` and
``motion_forecasts``). Each post or forecast opens its own decision on the policy
channel, the channel a ballot settles on, so its score reaches the posting seat's
durable identity through the ordinary reward channel and nowhere else. The formulas
are published in ``world.mechanics.committee``; the arithmetic is in
``charter.market``.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from factorylab.charter.amendment import proposed_price
from factorylab.charter.controller import violation
from factorylab.charter.market import (
    BRANCHES,
    LAMBDA_POST_DEFINITION,
    expected_violation,
    post_score,
    standing,
    violation_sign,
    weighted_median,
)
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus

#: The two return fields the markets read, stated as facts (``a_return_may_include``).
MARKET_RETURN_FIELDS = {
    "shadow_prices": (
        "optional on any return: {card_id: lambda} for cards priced now (world.card_prices), "
        "each a number in [0, lambda_max]; one post per seat, card and reserve window. Each "
        "post opens its own policy decision; its score is in "
        "world.mechanics.committee.shadow_prices"
    ),
    "motion_forecasts": (
        "optional on any return: a list of {motion, branch, q}; motion is an id on "
        "world.governance.agenda, branch is enact or reject, q the probability in [0, 1] "
        "that the motion's predicted_effect holds on that branch. One per seat, motion and "
        "branch. Each opens its own policy decision; its score is in "
        "world.mechanics.committee.motion_forecasts"
    ),
}


class MarketsMixin:
    """Posted λ (charter audit M1) and conditional forecasts on motions (M2, P1)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Unsettled λ posts, in posting order, and each seat's settled-post record
        # (n, sum of scores): the standing its posts are weighted by.
        self.lambda_posts: list[dict[str, Any]] = []
        self.lambda_standing: dict[str, tuple[int, float]] = {}
        self.A_RETURN_MAY_INCLUDE = {**self.A_RETURN_MAY_INCLUDE, **MARKET_RETURN_FIELDS}

    # --- intake -------------------------------------------------------------------

    def _apply_registrations(self, handle: str, ret: Return) -> None:
        """Every ordinary return may also carry market posts, read after its registrations."""
        super()._apply_registrations(handle, ret)
        if ret.status != "ok":
            return
        if "shadow_prices" in ret.outputs:
            self._post_shadow_prices(handle, ret.outputs["shadow_prices"])
        if "motion_forecasts" in ret.outputs:
            self._post_motion_forecasts(handle, ret.outputs["motion_forecasts"])

    def _market_refused(self, handle: str, market: str, reason: str) -> None:
        """A refused post is ledgered and its reason reaches the poster's inbox."""
        self.ledger.append({"kind": f"{market}.refused", "handle": handle, "reason": reason,
                            "ts": self.clock.now_ns})
        self._refusal_to_owner(handle, f"{market}_refused", reason)

    def _open_market_decision(self, handle: str, assembly: str, event_id: str,
                              horizon_windows: int) -> str:
        """One post, one policy decision, owned by the posting seat's durable identity."""
        lid = f"assembly:{assembly}"
        deadline = self.clock.now_ns + (
            self.events_budget + self.ev.consequence_backstop_ticks
        ) * self.m.max_tick_ns + (horizon_windows + 1) * self.m.novelty.window_ns
        try:
            self.queue.get(handle)
            parent = handle
        except KeyError:
            parent = None
        post = self.queue.open(
            actor=lid, event_id=event_id,
            propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, lid,
                                        "direct-market-post"),
            channel="policy", deadline_ns=deadline, parent_handle=parent, cost_ceiling=0)
        self.handle_to_assembly[post] = assembly
        return post

    def _lambda_horizon(self) -> int:
        """Closed windows a post waits for its realized price: the price loop's settling ratio.

        Essay II.IV.c: an inner loop settles at least ``min_ratio`` times faster
        than the loop that commands it. The price law is commanded by the
        committee, so a posted price is resolved after ``min_ratio`` closes of the
        price loop, the shortest horizon on which the committee can act on it.
        """
        return max(1, int(self.m.timing.min_ratio))

    def _post_shadow_prices(self, handle: str, raw: Any) -> None:
        """Admit each valid ``{card_id: lambda}`` post; refuse each invalid one with its reason."""
        assembly = self._proposer_assembly(handle)
        if assembly is None or assembly not in self.assemblies:
            self._market_refused(handle, "lambda_post", "no posting seat for this return")
            return
        if not isinstance(raw, dict) or not raw:
            self._market_refused(handle, "lambda_post",
                                 "shadow_prices must map priced card ids to prices")
            return
        horizon = self._lambda_horizon()
        for card_id, value in raw.items():
            card_id = str(card_id)
            if card_id not in self.priced:
                self._market_refused(handle, "lambda_post", f"card {card_id} is not priced now")
                continue
            try:
                price = proposed_price(value, self.m.prices.lambda_max)
            except ValueError as exc:
                self._market_refused(handle, "lambda_post", str(exc))
                continue
            if any(p["assembly"] == assembly and p["card_id"] == card_id
                   and p["window"] == self.window.index for p in self.lambda_posts):
                self._market_refused(handle, "lambda_post", f"one post per seat on card "
                                     f"{card_id} per reserve window")
                continue
            post = self._open_market_decision(
                handle, assembly, f"lambda-post-{card_id}-{self.window.index}", horizon)
            row = {"handle": post, "parent": handle, "assembly": assembly, "card_id": card_id,
                   "lambda": price, "window": self.window.index,
                   "due_window": self.window.index + horizon - 1,
                   "controller_lambda": self.controller.price(card_id)}
            self.ledger.append({"kind": "lambda_post.posted", **row, "ts": self.clock.now_ns})
            self.lambda_posts.append(row)

    def _post_motion_forecasts(self, handle: str, raw: Any) -> None:
        """Admit each conditional forecast on an undecided charter motion's promise."""
        assembly = self._proposer_assembly(handle)
        if assembly is None or assembly not in self.assemblies:
            self._market_refused(handle, "motion_forecast", "no forecasting seat for this return")
            return
        if not isinstance(raw, list) or not raw:
            self._market_refused(handle, "motion_forecast",
                                 "motion_forecasts must be a list of {motion, branch, q}")
            return
        agenda = {am.id: am for am in self.charter_book.agenda()}
        for item in raw:
            if not isinstance(item, dict) or set(item) != {"motion", "branch", "q"}:
                self._market_refused(handle, "motion_forecast",
                                     "a forecast is exactly {motion, branch, q}")
                continue
            motion, branch, q = item["motion"], item["branch"], item["q"]
            if branch not in BRANCHES:
                self._market_refused(handle, "motion_forecast", "branch is enact or reject")
                continue
            if type(q) not in (int, float) or not isfinite(q) or not 0 <= q <= 1:
                self._market_refused(handle, "motion_forecast", "q is a probability in [0, 1]")
                continue
            proposal = agenda.get(motion) if isinstance(motion, str) else None
            if proposal is None:
                self._market_refused(handle, "motion_forecast",
                                     "motion must be undecided on the governance agenda")
                continue
            if any(v.get("forecast") and v["assembly"] == assembly
                   and v["amendment_id"] == motion and v["branch"] == branch
                   for v in self.pending_votes):
                self._market_refused(handle, "motion_forecast",
                                     "one forecast per seat, motion and branch")
                continue
            post = self._open_market_decision(
                handle, assembly, f"motion-forecast-{motion}-{branch}",
                proposal.predicted_effect.window)
            entry = {"handle": post, "assembly": assembly, "amendment_id": motion,
                     "vote": None, "forecast": True, "branch": branch, "q": float(q),
                     **self._promise_frame(proposal),
                     "activation_window": None, "baseline": None}
            self.ledger.append({"kind": "policy.forecast", **entry, "parent": handle,
                                "ts": self.clock.now_ns})
            self.pending_votes.append(entry)

    # --- the posted price ---------------------------------------------------------

    def _posted_lambda(self, card_id: str) -> dict | None:
        """The standing-weighted median of each seat's latest unsettled post on a card.

        What the committee reads beside the controller's λ (charter audit M1).
        """
        latest: dict[str, dict] = {}
        for post in self.lambda_posts:
            if post["card_id"] == card_id:
                latest[post["assembly"]] = post
        if not latest:
            return None
        value = weighted_median([(post["lambda"], standing(self.lambda_standing.get(seat)))
                                 for seat, post in latest.items()])
        windows = [post["window"] for post in latest.values()]
        return {"lambda": value, "posts": len(latest),
                "windows": [min(windows), max(windows)]}

    def _motion_market(self, motion_id: str) -> dict | None:
        """Each branch's conditional forecasts on a motion: their count and mean probability."""
        rows = [v for v in self.pending_votes
                if v.get("forecast") and v["amendment_id"] == motion_id]
        if not rows:
            return None
        market = {}
        for branch in BRANCHES:
            qs = [v["q"] for v in rows if v["branch"] == branch]
            if qs:
                market[branch] = {"forecasts": len(qs), "mean_q": sum(qs) / len(qs)}
        return market

    # --- settlement and the feed-forward ------------------------------------------

    def _close_price_window(self) -> None:
        """Ledger the posted aggregate, close the window, then settle posts that fell due."""
        index = self.window.index
        for card_id in sorted({p["card_id"] for p in self.lambda_posts}):
            posted = self._posted_lambda(card_id)
            if posted is not None:
                self.ledger.append({"kind": "lambda_post.aggregate", "card_id": card_id,
                                    "window": index, **posted,
                                    "controller_lambda": self.controller.price(card_id),
                                    "ts": self.clock.now_ns})
        super()._close_price_window()
        self._settle_lambda_posts(index)

    def _settle_lambda_posts(self, index: int) -> None:
        """Score every post due at this close against the price the law now holds.

        The realized price is the card's λ after this window's observation: what
        the published law, backward terms and feed-forward alike, charged per unit
        of violation once the window was measured. The score is
        ``charter.market.post_score``, strictly proper for the mean, and it settles
        the post's own policy decision. A card no longer priced leaves nothing to
        score: the post is censored.
        """
        remaining = []
        lambda_max = self.m.prices.lambda_max
        for post in self.lambda_posts:
            if post["due_window"] > index:
                remaining.append(post)
                continue
            if post["card_id"] in self.priced:
                realized = self.controller.price(post["card_id"])
                score = post_score(post["lambda"], realized, lambda_max)
                status = SettleStatus.SETTLED
                n, total = self.lambda_standing.get(post["assembly"], (0, 0.0))
                self.lambda_standing[post["assembly"]] = (n + 1, total + score)
            else:
                realized, score, status = None, 0.0, SettleStatus.CENSORED
            self.ledger.append({"kind": "lambda_post.settled", "handle": post["handle"],
                                "card_id": post["card_id"], "posted": post["lambda"],
                                "realized": realized, "score": score, "window": index,
                                "status": str(status), "ts": self.clock.now_ns})
            self.queue.settle(post["handle"], channel="policy", score=score, status=status,
                              definition_version=LAMBDA_POST_DEFINITION, sampling_ref=None)
        self.lambda_posts[:] = remaining

    def _anticipated_violation(self, card_id: str, value: float) -> float | None:
        """The change in a card's violation the conditional forecasts expect, or None.

        The forecasts read are those on the branch the world is on: for an
        undecided motion, the unchanged charter the controller is pricing now
        (``reject``); for a decided one, the branch taken, until its horizon
        settles. Each names the card through its motion's predicted effect. The
        expectation is ``charter.market.expected_violation``, in the controller's
        region-relative units, with one promise resolution as its step.
        """
        region = self.regions.get(card_id)
        if region is None:
            return None
        rows = [v for v in self.pending_votes if v.get("forecast")
                and v["prediction"].card_id == card_id
                and (v["activation_window"] is not None or v["branch"] == "reject")]
        if not rows:
            return None
        now = violation(region, value)
        observation = self.observations.get(next(
            c.observation for c in self.charter.cards if c.id == card_id))
        step = (self.m.committee.promise_resolution * observation.scale / region.scale
                if observation is not None else 0.0)
        forecasts = [(v["q"], violation_sign(region.kind, region.lo, region.hi, value,
                                             v["prediction"].direction)) for v in rows]
        return expected_violation(now, forecasts, step) - now

    # --- publication --------------------------------------------------------------

    def _world_block(self) -> dict[str, Any]:
        """``world.card_prices`` carries each card's posted price beside the controller's."""
        block = super()._world_block()
        for row in block.get("card_prices", ()):
            posted = self._posted_lambda(row["card_id"])
            if posted is not None:
                row["posted"] = posted
        return block

    def _mechanics_block(self) -> dict[str, Any]:
        """The markets' formulas, published beside the committee's and the controller's."""
        block = super()._mechanics_block()
        committee = block["committee"]
        lambda_max = self.m.prices.lambda_max
        committee["liability"] = (
            "a ballot and a conditional forecast are both bets on a motion's predicted_effect. "
            "The branch is decided at the boundary: enact when the motion takes effect, "
            "reject when it fails its vote. A forecast's q is its stated probability that "
            "the effect holds on its branch; a yes vote is q = 1 on enact and q = 0 on "
            "reject, a no vote the opposite. Score = 1 - (q - outcome)^2, outcome the "
            "promise measured at the declared window after the decision against the value "
            "at the decision, on the branch taken. A forecast on the branch not taken is "
            "void (censored). No decision, a refused activation or missing evidence is "
            "censored. Feedback returns to the betting assembly's durable identity. "
            "Retirements and connectors are voted when proposed, by a committee drawn the "
            "same way; a passed retirement takes effect at the next window boundary. With "
            "no predicted effect in a retire proposal, their ballots are unscored and "
            "censored.")
        committee["shadow_prices"] = (
            f"a seat may post a card's lambda p in [0, {lambda_max}]. It is scored after "
            f"{self._lambda_horizon()} closed windows (timing.min_ratio) against y, the "
            "card's lambda after that close: score = 1 - ((p - y) / lambda_max)^2, on the "
            "post's own policy decision; a card no longer priced censors it. The posted "
            "price of a card is the median of each seat's latest unsettled post weighted by "
            "the seat's (1/2 + sum of its settled post scores) / (1 + their count); it is "
            "published in world.card_prices and on the standing committee's agenda beside "
            "the controller's lambda. A lambda motion may name \"posted\" as a card's value: "
            "the posted price when the motion is admitted.")
        committee["motion_forecasts"] = (
            "a seat may forecast q, the probability that an agenda motion's predicted_effect "
            "holds on its enact or reject branch; scored as the liability states. Each "
            "motion's forecasts per branch (count, mean q) are on the committee's agenda.")
        controller = block["controller"]
        controller["recurrence"] += (
            ". A card's failed holdouts add a violation h = failed / named, and v = "
            "max(region violation, h). Feed-forward: for a card named by the predicted "
            "effect of forecasts on the branch in force (reject while undecided, the "
            "branch taken until its horizon), each forecast q whose direction relieves the "
            "violation reads (1 - q) * v and one that deepens it v + q * s, s = "
            "committee.promise_resolution * observation.scale / region scale; e = their mean "
            "(at least 0); F = kp * max(e - v, -v) is added to lambda' before clipping")
        block["measurement"] += (
            " A window may add interval {level, half_width}: a scope is measured only when "
            "z * s / sqrt(n) <= half_width, z the normal quantile at (1 + level) / 2, s the "
            "population standard deviation of its n samples (per closed window for a "
            "windows selector over whole windows). A registered observation may be measured "
            "per role or assembly: its code runs once per scope on that scope's share of the "
            "window facts (its own responses and forecasts; world series whole; counters no "
            "response attributes are null), with no identity in them.")
        return block
