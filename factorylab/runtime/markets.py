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
    branch_violation,
    enactment_rate,
    margin,
    post_score,
    shadow_price,
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
        "each a number in [0, penalty_cap]; one post per seat, card and reserve window. Each "
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
        # Each closed window's decisions and per-scope card violations, kept until its
        # decisions' consequences are measured; those consequences; and the last
        # window's margins (charter audit M1, M5).
        self.margin_windows: dict[int, dict[str, Any]] = {}
        self.measured_consequences: dict[str, float] = {}
        self.lambda_dollars: dict[str, Any] = {}
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
        try:
            self.queue.get(handle)
            parent = handle
        except KeyError:
            parent = None
        post = self.queue.open(
            actor=lid, event_id=event_id,
            propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, lid,
                                        "direct-market-post"),
            channel="policy", horizon_ticks=self._policy_horizon(horizon_windows + 1),
            parent_handle=parent, cost_ceiling=0)
        self.handle_to_assembly[post] = assembly
        return post

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
        horizon = self._margin_horizon()
        for card_id, value in raw.items():
            card_id = str(card_id)
            if card_id not in self.priced:
                self._market_refused(handle, "lambda_post", f"card {card_id} is not priced now")
                continue
            try:
                price = proposed_price(value, self.m.prices.penalty_cap)
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
                   "due_window": self.window.index + horizon,
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
        """Ledger the posted aggregate, close the window, then settle what fell due."""
        index = self.window.index
        for card_id in sorted({p["card_id"] for p in self.lambda_posts}):
            posted = self._posted_lambda(card_id)
            if posted is not None:
                self.ledger.append({"kind": "lambda_post.aggregate", "card_id": card_id,
                                    "window": index, **posted,
                                    # A post may outlive its card: a removed card has
                                    # no controller price, only the posts made on it.
                                    "controller_lambda": (self.controller.price(card_id)
                                                          if card_id in self.priced
                                                          else None),
                                    "ts": self.clock.now_ns})
        super()._close_price_window()
        self._keep_margin_window(index)
        self._capture_consequences()
        for closed in sorted(w for w, row in self.margin_windows.items() if row["due"] <= index):
            self._close_margin(closed, index)

    def _margin_horizon(self) -> int:
        """Closed windows until a window's decisions have their world-measured consequences.

        A decision's consequence is fixed within its consequence patience at the
        latest (``_patience_ticks``: the horizon plus the verdict window, counted once;
        wave 16, D2 and ruling R10-k), converted to closed windows at the price loop's
        current period in ticks, and never sooner than ``timing.min_ratio`` windows.
        """
        ticks = self._patience_ticks()
        window = self.clockwork.period("price", default=self.m.timing.min_ratio)
        return max(int(self.m.timing.min_ratio), -(-ticks // window))

    def _keep_margin_window(self, index: int) -> None:
        """Keep the closed window's decisions and each card's per-scope violations.

        The window's marginal consequence is read once its decisions' consequences
        are measured (``_margin_horizon`` windows later). Nothing kept is published.
        """
        window = self.window
        cards = {}
        for card in window.closed_cards:
            region = window.closed_regions.get(card.id)
            scopes = window.closed_scopes.get(card.id) or {}
            cards[card.id] = {
                "per": card.window.per, "lambda": window.closed_prices.get(card.id, 0.0),
                "violations": ({str(scope): violation(region, value)
                                for scope, value in scopes.items()}
                               if region is not None and card.window.per else {})}
        self.margin_windows[index] = {
            "due": index + self._margin_horizon(),
            "decisions": {handle: {"assembly": self.handle_to_assembly.get(handle),
                                   "role": row["role"], "cost": row["cost"]}
                          for handle, row in window.decisions.items()},
            "cards": cards}

    def _capture_consequences(self) -> None:
        """Keep each tracked return's world-measured outcome once the world fixes it.

        A return's measured outcome (``return_paid_off``, a named trade's net-of-fee
        fact), in [0, 1]. The consequence score that grades an evaluator's judgement
        is never read here: realized consequence is not priced or traded through λ
        (wave 16, section 9; essay II.IV.a). The chain forgets outcomes after its
        horizon; the window that owns the decision keeps them until its margin is
        read.
        """
        tracked = {h for row in self.margin_windows.values() for h in row["decisions"]}
        for handle in tracked - set(self.measured_consequences):
            outcome = self.world_outcomes.get(handle)
            if outcome is not None and outcome.get("state") == "measured":
                self.measured_consequences[handle] = float(outcome["y"])

    def _margin_points(self, record: dict, card: dict) -> list[dict]:
        """One anonymous point per violating-or-not scope with a measured consequence."""
        per = card["per"]
        points = []
        for scope, v in sorted(card["violations"].items()):
            rows = [(self.measured_consequences.get(handle), row["cost"])
                    for handle, row in record["decisions"].items()
                    if str(row.get(per)) == scope]
            measured = [c for c, _cost in rows if c is not None]
            if not measured:
                continue
            points.append({"v": v, "consequence": sum(measured) / len(measured),
                           "micro_usd": sum(cost for _c, cost in rows) / len(rows),
                           "n": len(measured)})
        return points

    def _close_margin(self, window: int, index: int) -> None:
        """Read a window's marginal consequence per card and score the posts made in it.

        Essay II.IV.a (architect's ruling on the cold review): a posted λ is scored
        against the window's realized shadow price, ``charter.market.shadow_price``,
        the least-squares slope of the scopes' world-measured consequence on their
        violation, clipped to ``[0, penalty_cap]`` (the price at which a unit
        violation's penalty takes the whole cap: wave 16, R-E). The committee's λ is not the
        target, so posting it, or adopting a post by motion, cannot make a post
        come true. The score is ``charter.market.post_score``, strictly proper for
        the mean. With the slope unidentifiable the post is censored. The same
        margins are the window's λ-to-dollar statistic (``price.margin``).
        """
        record = self.margin_windows.pop(window)
        scale = self.m.prices.penalty_cap
        margins = {}
        for card_id, card in sorted(record["cards"].items()):
            points = self._margin_points(record, card)
            row = margin(points)
            target = shadow_price(points, scale)
            margins[card_id] = {"lambda": card["lambda"],
                                "marginal_consequence": row["slope"],
                                "micro_usd_per_violation": row["micro_usd_per_violation"],
                                "shadow_price": target, "scopes": row["scopes"]}
            self.ledger.append({"kind": "price.margin", "window": window, "card_id": card_id,
                                "lambda": card["lambda"], "points": points, **row,
                                "shadow_price": target, "ts": self.clock.now_ns})
        if margins:
            self.lambda_dollars = {"window": window, "cards": margins}
        remaining = []
        for post in self.lambda_posts:
            if post["window"] != window:
                remaining.append(post)
                continue
            target = (margins.get(post["card_id"]) or {}).get("shadow_price")
            if target is None:
                score, status = 0.0, SettleStatus.CENSORED
            else:
                score, status = post_score(post["lambda"], target, scale), \
                    SettleStatus.SETTLED
                n, total = self.lambda_standing.get(post["assembly"], (0, 0.0))
                self.lambda_standing[post["assembly"]] = (n + 1, total + score)
            self.ledger.append({"kind": "lambda_post.settled", "handle": post["handle"],
                                "card_id": post["card_id"], "posted": post["lambda"],
                                "realized": target, "score": score, "window": index,
                                "posted_window": window, "status": str(status),
                                "ts": self.clock.now_ns})
            self.queue.settle(post["handle"], channel="policy", score=score, status=status,
                              definition_version=LAMBDA_POST_DEFINITION, sampling_ref=None)
        self.lambda_posts[:] = remaining
        live = {h for row in self.margin_windows.values() for h in row["decisions"]}
        for handle in [h for h in self.measured_consequences if h not in live]:
            del self.measured_consequences[handle]

    def _anticipated_violation(self, card_id: str, value: float) -> float | None:
        """The change in a violating card's violation that liable forecasts expect, or None.

        Only forecasts that are scored whichever branch the committee takes enter:

        - on a decided motion (until its horizon settles), the forecasts on the branch
          taken, each reading ``charter.market.branch_violation``;
        - on an undecided motion, only a seat that forecast both branches, its pair
          read as ``p * e(enact) + (1 - p) * e(reject)``, ``p`` the factory's realized
          enactment rate (``charter.market.enactment_rate``). A forecast on one branch
          alone would be voided at no cost if the other branch were taken, so it
          moves nothing.

        The expectation is the mean over those contributions. A card inside its
        region takes no feed-forward (the controller checks it too).
        """
        region = self.regions.get(card_id)
        if region is None:
            return None
        now = violation(region, value)
        if now <= 0:
            return None
        step = self._resolution_step(card_id)
        p = enactment_rate(self.motion_tally["passed"], self.motion_tally["failed"])

        def expect(vote: dict) -> float:
            sign = violation_sign(region.kind, region.lo, region.hi, value,
                                  vote["prediction"].direction)
            return branch_violation(now, vote["q"], sign, step)

        contributions = []
        undecided: dict[tuple[str, str], dict[str, dict]] = {}
        for vote in self.pending_votes:
            if not vote.get("forecast") or vote["prediction"].card_id != card_id:
                continue
            if vote["activation_window"] is not None:
                contributions.append(expect(vote))
            else:
                undecided.setdefault((vote["amendment_id"], vote["assembly"]), {})[
                    vote["branch"]] = vote
        for pair in undecided.values():
            if set(pair) == {"enact", "reject"}:
                contributions.append(p * expect(pair["enact"])
                                     + (1 - p) * expect(pair["reject"]))
        if not contributions:
            return None
        return sum(contributions) / len(contributions) - now

    # --- publication --------------------------------------------------------------

    def _world_block(self) -> dict[str, Any]:
        """``world.card_prices`` carries each card's posted price beside the controller's."""
        block = super()._world_block()
        dollars = self.lambda_dollars.get("cards") or {}
        for row in block.get("card_prices", ()):
            posted = self._posted_lambda(row["card_id"])
            if posted is not None:
                row["posted"] = posted
            if row["card_id"] in dollars:
                # Charter audit M5: the last read window's margins beside its lambda.
                row["last_window_margin"] = {"window": self.lambda_dollars["window"],
                                             **dollars[row["card_id"]]}
        return block

    def _mechanics_block(self) -> dict[str, Any]:
        """The markets' formulas, published beside the committee's and the controller's."""
        block = super()._mechanics_block()
        committee = block["committee"]
        scale = self.m.prices.penalty_cap
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
            f"a seat may post a card's lambda p in [0, {scale}] for the reserve window it "
            "posts in. When that window's decisions have their world-measured consequences "
            "(the consequence patience, H = timing.world_repricing / timing.min_ratio plus "
            "verdict_timeout_ticks, converted to closed windows at the price loop's period "
            "in ticks, and at least timing.min_ratio windows; in force: "
            "world.adaptive_scoring.margin_horizon_windows) the window's shadow price y is "
            "read: the "
            "least-squares slope, across the card's scopes (per role or assembly, at least "
            "3, with variance in v), of the scope's mean measured outcome (a return's "
            "return_paid_off or priced named trade, in [0, 1]; never a judgement's "
            "consequence score) on its "
            "violation v, clipped to [0, prices.penalty_cap]. score = 1 - ((p - y) / "
            "prices.penalty_cap)^2 "
            "on the post's own policy decision; with y unidentified the post is censored. "
            "The posted price of a card is the median of each seat's latest unsettled post "
            "weighted by the seat's (1/2 + sum of its settled post scores) / (1 + their "
            "count); it is published in world.card_prices and on the standing committee's "
            "agenda beside the controller's lambda. A lambda motion may name \"posted\" as "
            "a card's value: the posted price when the motion is admitted.")
        committee["lambda_dollars"] = (
            "per window, once its consequences are measured, each card's margins across its "
            "scopes: marginal_consequence (the slope above, unclipped) and "
            "micro_usd_per_violation (the slope of the scopes' mean decision cost on v), "
            "beside the lambda the window closed at; None when not identifiable. Ledgered "
            "as price.margin with its points, published in world.card_prices as "
            "last_window_margin")
        committee["card_contract"] = (
            "a metric card: id; norm (one of the charter's norms); description; units; "
            "window {kind: returns | forecasts | windows, n: positive integer, per: role | "
            "assembly | null, optionally interval {level, half_width}}; region {rule, lo, "
            "hi}, rule one of at least, above (lo), at most, below (hi), between (lo < hi), "
            "below the median of the previous window (neither); observation (an id in "
            "world.observations); answers_for (producer, evaluator, meta, antagonist, all, "
            "or an emitted kind); optionally holdout [predicate@version]. A window selects "
            "the latest n samples; fewer is unmeasured. A card is preflighted through the "
            "measurement before any vote")
        committee["motion_forecasts"] = (
            "a seat may forecast q, the probability that an agenda motion's predicted_effect "
            "holds on its enact or reject branch; scored as the liability states. Each "
            "motion's forecasts per branch (count, mean q) are on the committee's agenda.")
        controller = block["controller"]
        controller["recurrence"] += (
            ". s = committee.promise_resolution * observation.scale / region scale. Each "
            "failed holdout adds s to v. Feed-forward, only while v > 0: each forecast q on "
            "a motion whose predicted effect names the card reads e = max(0, v + sign * q * "
            "s), sign +1 when the predicted direction deepens the violation and -1 when it "
            "relieves it. A decided motion's forecasts on the branch taken count one each; "
            "an undecided motion's count only as a seat's pair on both branches, as p * "
            "e(enact) + (1 - p) * e(reject), p = (passed + 1) / (passed + failed + 2) over "
            "the charter motions decided so far. With E their mean, F = kp * max(E - v, -v) "
            "is added to lambda' before clipping")
        block["measurement"] += (
            " A window may add interval {level, half_width}: a scope is measured only when "
            "z * s / sqrt(n) <= half_width, z the normal quantile at (1 + level) / 2, s the "
            "population standard deviation of its n samples (per closed window for a "
            "windows selector over whole windows). A registered observation may be measured "
            "per role or assembly: its code runs once per scope on that scope's share of the "
            "window facts (its own responses and forecasts; world series whole; counters no "
            "response attributes are null), with no identity in them.")
        return block
