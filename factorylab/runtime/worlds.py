"""World manifests.

A manifest is the architect's whole first move: initial balance, drip,
venue, priced model tiers, seed assemblies, novelty share, timing ratios,
termination conditions and the seed. It is loaded from TOML, validated, and
hashed into the ledger's genesis entry so a world can prove which manifest
it was born from. Nothing in a manifest changes after launch.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any

from factorylab.charter.charter import Charter, MetricCard, seed_charter
from factorylab.kernel.money import usd_to_micro
from factorylab.runtime.cards import parses
from factorylab.runtime.observations import observation_for
from factorylab.world.connector import DEFAULT_DENYLIST, validate_denylist
from factorylab.world.market import DISCOVERY_URL
from factorylab.world.models import PriceTable, TokenPrice

NS_PER_SECOND = 1_000_000_000
NS_PER_HOUR = 3_600 * NS_PER_SECOND
NS_PER_DAY = 24 * NS_PER_HOUR
WORLDS_DIR = Path(__file__).resolve().parents[2] / "worlds"


@dataclass(frozen=True)
class DripSpec:
    amount_micro: int
    period_ns: int
    start_ns: int
    end_ns: int


@dataclass(frozen=True)
class Shock:
    """A scripted price multiplier applied to one coin at one venue step (fake venue only)."""

    step: int
    coin: str
    multiplier: str


@dataclass(frozen=True)
class ExchangeSpec:
    kind: str  # "fake" | "hyperliquid"
    mainnet: bool = False
    coins: tuple[str, ...] = ("BTC", "ETH")
    spot_pairs: tuple[str, ...] = ()
    seed: int = 0
    start_cash_usd: str = "100"
    shocks: tuple[Shock, ...] = ()


@dataclass(frozen=True)
class ModelTier:
    id: str
    provider: str  # "fake" | "openrouter" | "anthropic"
    input_usd_per_mtok: str
    output_usd_per_mtok: str
    reasoning: tuple[tuple[str, Any], ...] = ()  # OpenRouter `reasoning` object, e.g. effort=low
    # web purchasable: engine, mode, max_results, usd_per_request
    web: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class AssemblySeed:
    id: str
    model_id: str
    accepts: tuple[str, ...]
    max_tokens: int = 1024
    effort: str = "medium"
    memory_policy: str = "none"
    role: str = "producer"
    emits: tuple[str, ...] | None = None
    schemas: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from factorylab.cortex.registration import output_contracts, seed_emits

        emits, schemas = output_contracts(
            self.emits if self.emits is not None else seed_emits(self.role), self.schemas)
        object.__setattr__(self, "emits", emits)
        object.__setattr__(self, "schemas", schemas)


@dataclass(frozen=True)
class ToolsSpec:
    population_tool_micro_per_call: int = 50
    max_leverage: int = 3
    max_routers_per_kind: int = 3
    max_depth: int = 4
    max_children: int = 3
    max_tool_calls: int = 4


@dataclass(frozen=True)
class ConnectorsSpec:
    """Connector reads share immutable size, time, flat-price and assembly-window bounds."""

    max_bytes: int = 262144
    timeout_s: int = 10
    call_price_micro: int = 1000
    max_calls_per_window: int = 60
    origin_denylist: tuple[str, ...] = DEFAULT_DENYLIST

    def __post_init__(self):
        for name in ("max_bytes", "timeout_s", "max_calls_per_window", "call_price_micro"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "call_price_micro" else 1):
                raise ValueError(f"connectors.{name} must be an integer within its bounds")
        validate_denylist(self.origin_denylist)
        object.__setattr__(self, "origin_denylist", tuple(self.origin_denylist))


@dataclass(frozen=True)
class TreasurySpec:
    """Compute insolvency and the public discovery index are fixed at launch."""

    insolvency_events: int = 20
    discovery_url: str = DISCOVERY_URL
    reserve_address: str | None = None
    hyperevm_gas_budget_wei: int = 0
    base_gas_budget_wei: int = 0
    max_transfer_fee_micro: int = 2_000_000
    withdrawal_fee_micro: int = 1_000_000
    cctp_max_fee_micro: int = 100_000
    fake_fee_micro: int = 10_000
    max_request_micro: int = 500_000
    reported_cost_multiple: int = 10
    max_venice_per_window: int = 10_000_000


@dataclass(frozen=True)
class PricesSpec:
    """Price controller parameters. Not money: bare rates and bounds."""

    eta: float = 0.5
    decay: float = 0.1
    lambda_max: float = 1.0
    min_window_events: int = 1
    kappa: float = 0.5
    penalty_cap: float = 0.5


@dataclass(frozen=True)
class EvaluationSpec:
    consequence_share: float = 0.3
    max_forecasts_per_verdict: int = 2
    verdict_timeout_events: int = 20
    min_coverage: float = 0.5
    trial_amount_micro: int = 100_000  # novelty trial paid per registration
    forecast_horizon_events: int = 10
    consequence_backstop_events: int = 200
    adversarial_share: float = 0.15  # cap on router mass over antagonist assemblies
    sibling_share: float = 0.5  # share of the representative's meta score a sibling settles at
    sampling_step: float = 0.1  # consequence-mix step per divergent window
    sampling_cap: float = 0.7  # ceiling of the raised consequence mix


@dataclass(frozen=True)
class NoveltySpec:
    share: float
    window_ns: int
    trials: int = 3  # settled consequences that end an assembly's protected trial
    max_lifetime_windows: int = 6  # windows after registration that end it regardless


@dataclass(frozen=True)
class CommitteeSpec:
    min_settled: int = 5
    seats: int = 5


@dataclass(frozen=True)
class ImmuneSpec:
    """Detection horizons and bounded interventions are immutable launch casts."""

    k: int = 3
    bins: int = 3
    tv_threshold: float = 0.2
    gap_threshold: float = 0.8
    gain_step: float = 0.05
    gamma_max: float = 0.5
    decay_step: float = 0.1
    registration_bins: tuple[float, ...] = (0.0, 2.0)
    revision_bins: tuple[float, ...] = (0.0,)


@dataclass(frozen=True)
class ClockSpec:
    min_tick_ns: int = 10 * NS_PER_SECOND


@dataclass(frozen=True)
class TimingSpec:
    min_ratio: int = 3
    jitter_fraction: float = 0.2
    cadence_sample: int = 200
    min_support: int = 30


@dataclass(frozen=True)
class TerminationSpec:
    balance_floor_micro: int = 0
    max_events: int | None = None


@dataclass(frozen=True)
class WorldManifest:
    name: str
    seed: int
    initial_balance_micro: int
    drip: DripSpec | None
    exchange: ExchangeSpec
    models: tuple[ModelTier, ...]
    assemblies: tuple[AssemblySeed, ...]
    novelty: NoveltySpec
    timing: TimingSpec
    termination: TerminationSpec
    evaluation: EvaluationSpec = EvaluationSpec()
    tools: ToolsSpec = ToolsSpec()
    connectors: ConnectorsSpec = ConnectorsSpec()
    prices: PricesSpec = PricesSpec()
    treasury: TreasurySpec = TreasurySpec()
    clock: ClockSpec = ClockSpec()
    committee: CommitteeSpec = CommitteeSpec()
    immune: ImmuneSpec = ImmuneSpec()
    tick_interval_ns: int = 10 * NS_PER_SECOND
    extra: dict[str, Any] = field(default_factory=dict)

    charter: Charter = field(default_factory=seed_charter)
    charter_prices: tuple[tuple[str, float], ...] = ()
    charter_explicit: bool = False

    # ---- derived

    @property
    def max_tick_ns(self) -> int:
        """Return the largest integer interval satisfying the reserve-window ratio."""
        return self.novelty.window_ns // self.timing.min_ratio

    def price_table(self) -> PriceTable:
        t = PriceTable()
        for m in self.models:
            per_in = Decimal(m.input_usd_per_mtok) / Decimal(1_000_000)
            per_out = Decimal(m.output_usd_per_mtok) / Decimal(1_000_000)
            base = TokenPrice.from_per_token(str(per_in), str(per_out))
            t.register(m.id, base)
            web = dict(m.web)
            if web:
                per_request = usd_to_micro(web.get("usd_per_request", "0"), rounding="exact")
                t.register(
                    f"{m.id}:online",
                    TokenPrice(base.input_micro, base.output_micro, per_request),
                )
        return t

    def web_config(self) -> dict[str, dict[str, Any]]:
        """OpenRouter web-plugin options per online variant (price key stripped)."""
        out: dict[str, dict[str, Any]] = {}
        for m in self.models:
            web = {k: v for k, v in dict(m.web).items() if k != "usd_per_request"}
            if dict(m.web):
                out[f"{m.id}:online"] = web
        return out

    def canonical_json(self) -> str:
        payload = asdict(self)
        # Admission provenance does not change the world defined by identical cards.
        payload.pop("charter_explicit")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def manifest_hash(self) -> str:
        """sha256 of the canonical JSON. Two manifests with equal hashes are the same world seed."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    # ---- validation

    def validate_venue_metadata(self) -> dict:
        """Name missing markets before launch; unreachable metadata is explicitly unverified.

        This read-only preflight is separate from deterministic manifest loading.
        It never constructs a trading adapter or reads account credentials.
        """
        if self.exchange.kind != "hyperliquid":
            return {"status": "not_applicable"}
        from urllib.request import Request, urlopen

        host = "api.hyperliquid.xyz" if self.exchange.mainnet else "api.hyperliquid-testnet.xyz"

        def metadata(kind):
            request = Request(f"https://{host}/info", data=json.dumps({"type": kind}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                return json.load(response)

        try:
            perps, spot = metadata("meta"), metadata("spotMeta")
            coins = {row["name"] for row in perps["universe"]}
            tokens = {row["index"]: row["name"] for row in spot["tokens"]}
            pairs = {f"{tokens[row['tokens'][0]]}/{tokens[row['tokens'][1]]}"
                     for row in spot["universe"]}
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return {"status": "unavailable", "coins": list(self.exchange.coins),
                    "spot_pairs": list(self.exchange.spot_pairs)}
        missing_coins = sorted(set(self.exchange.coins) - coins)
        missing_pairs = sorted(set(self.exchange.spot_pairs) - pairs)
        return {"status": "invalid" if missing_coins or missing_pairs else "valid",
                "missing_coins": missing_coins, "missing_spot_pairs": missing_pairs}

    def validate(self) -> None:
        if (self.exchange.kind == "hyperliquid" and self.exchange.mainnet
                and self.charter_explicit is not True):
            # Real money launches on the population's charter, never the seed cards.
            # Testnet rehearsals may run on the seed charter before edition 1 is drafted.
            raise ValueError("live_exchange_requires_explicit_charter: mainnet needs [charter]")
        if self.initial_balance_micro < 0:
            raise ValueError("initial balance must be non-negative")
        if (type(self.termination.balance_floor_micro) is not int
                or self.termination.balance_floor_micro < 0):
            raise ValueError("termination balance floor must be non-negative integer micro-USD")
        if type(self.treasury.insolvency_events) is not int or self.treasury.insolvency_events < 1:
            raise ValueError("treasury.insolvency_events must be a positive integer")
        if (type(self.treasury.reported_cost_multiple) is not int
                or self.treasury.reported_cost_multiple < 1):
            raise ValueError("treasury.reported_cost_multiple must be a positive integer")
        if self.treasury.reserve_address is not None:
            import re

            if (not isinstance(self.treasury.reserve_address, str)
                    or not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.treasury.reserve_address)
                    or int(self.treasury.reserve_address, 16) == 0):
                raise ValueError("treasury.reserve_address must be a nonzero EVM address")
        for budget_field in ("hyperevm_gas_budget_wei", "base_gas_budget_wei",
                      "max_transfer_fee_micro", "withdrawal_fee_micro", "cctp_max_fee_micro",
                      "fake_fee_micro", "max_request_micro", "max_venice_per_window"):
            value = getattr(self.treasury, budget_field)
            if type(value) is not int or value < 0:
                raise ValueError(f"treasury.{budget_field} must be nonnegative integer money")
        if (self.treasury.withdrawal_fee_micro + self.treasury.cctp_max_fee_micro
                > self.treasury.max_transfer_fee_micro):
            raise ValueError("withdrawal fee exceeds maximum transfer fee")
        from urllib.parse import urlsplit

        discovery = urlsplit(self.treasury.discovery_url)
        if (
            discovery.scheme not in {"http", "https"} or not discovery.hostname
            or discovery.username or discovery.password or discovery.query or discovery.fragment
        ):
            raise ValueError("treasury.discovery_url must be a public HTTP(S) endpoint")
        if not self.models:
            raise ValueError("a world needs at least one priced model tier")
        ids = {m.id for m in self.models}
        for a in self.assemblies:
            if a.model_id not in ids:
                raise ValueError(f"assembly {a.id} uses unpriced model {a.model_id}")
        if (type(self.novelty.share) not in (int, float)
                or not isfinite(self.novelty.share) or not 0 < self.novelty.share <= 1):
            raise ValueError("novelty share must be in (0, 1]")
        for name, value, minimum in (
            ("novelty.trials", self.novelty.trials, 1),
            ("novelty.max_lifetime_windows", self.novelty.max_lifetime_windows, 1),
            ("committee.min_settled", self.committee.min_settled, 1),
            ("committee.seats", self.committee.seats, 3),
            ("tools.max_depth", self.tools.max_depth, 0),
            ("tools.max_children", self.tools.max_children, 0),
            ("tools.max_tool_calls", self.tools.max_tool_calls, 0),
            ("immune.k", self.immune.k, 2), ("immune.bins", self.immune.bins, 2),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        for name in ("tv_threshold", "gap_threshold", "gain_step", "gamma_max", "decay_step"):
            value = getattr(self.immune, name)
            if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"immune.{name} must be finite and in (0, 1]")
        if self.immune.bins != 3:
            raise ValueError("immune.bins must be 3 for fixed region-relative cells")
        for name in ("registration_bins", "revision_bins"):
            cuts = getattr(self.immune, name)
            if (not isinstance(cuts, (tuple, list)) or not cuts
                    or any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in cuts)
                    or any(a >= b for a, b in zip(cuts, cuts[1:], strict=False))):
                raise ValueError(f"immune.{name} must contain increasing finite nonnegative cuts")
        if not 0 <= self.evaluation.consequence_share < 1:
            raise ValueError("consequence share must be in [0, 1)")
        for name in ("adversarial_share", "sibling_share", "sampling_step"):
            value = getattr(self.evaluation, name)
            if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"evaluation.{name} must be finite and in [0, 1]")
        cap = self.evaluation.sampling_cap
        if (type(cap) not in (int, float) or not isfinite(cap)
                or not self.evaluation.consequence_share <= cap < 1):
            raise ValueError("evaluation.sampling_cap must be in [consequence_share, 1)")
        backstop = self.evaluation.consequence_backstop_events
        if type(backstop) is not int or backstop < 1:
            raise ValueError("consequence_backstop_events must be a positive integer")
        for a in self.assemblies:
            if not isinstance(a.role, str) or not a.role.strip():
                raise ValueError(f"assembly {a.id} has an empty role label")
        if self.novelty.window_ns <= 0:
            raise ValueError("novelty window must be positive")
        if type(self.timing.cadence_sample) is not int or self.timing.cadence_sample < 1:
            raise ValueError("timing.cadence_sample must be a positive integer")
        if type(self.timing.min_support) is not int or self.timing.min_support < 1:
            raise ValueError("timing.min_support must be a positive integer")
        if self.timing.min_support > self.timing.cadence_sample:
            # The latency deque is capped at cadence_sample, so a larger support never arrives.
            raise ValueError("timing.min_support must be at most timing.cadence_sample")
        if type(self.timing.min_ratio) is not int or self.timing.min_ratio < 3:
            raise ValueError("timing min_ratio must be an integer at least 3")
        if type(self.clock.min_tick_ns) is not int or self.clock.min_tick_ns <= 0:
            raise ValueError("clock.min_tick must be positive integer nanoseconds")
        if (type(self.tick_interval_ns) is not int
                or not self.clock.min_tick_ns <= self.tick_interval_ns <= self.max_tick_ns):
            raise ValueError("tick_interval must lie within clock.min_tick and derived max_tick")
        if self.exchange.kind not in ("fake", "hyperliquid"):
            raise ValueError("unknown exchange kind")
        if self.exchange.mainnet and self.name != "funded":
            raise ValueError("mainnet is only allowed in the world named 'funded'")
        if self.exchange.shocks and self.exchange.kind != "fake":
            raise ValueError("price shocks exist only on the fake venue")
        for sh in self.exchange.shocks:
            if sh.step < 1 or Decimal(sh.multiplier) <= 0:
                raise ValueError("shock step must be >= 1 and multiplier positive")
        if self.drip is not None and (self.drip.period_ns <= 0 or self.drip.amount_micro < 0):
            raise ValueError("drip period must be positive and amount non-negative")
        from factorylab.charter.book import validate_observation_bindings

        validate_observation_bindings(self.charter.cards)
        # A launch card can only hold to account work this world can actually emit:
        # a seed role, every role at once, or a kind one of the seed assemblies emits.
        seed_kinds = frozenset(kind for a in self.assemblies for kind in a.emits)
        for card in self.charter.cards:
            card.validate_answers_for(seed_kinds)
            if observation_for(card.observation) is None:
                raise ValueError(f"card {card.id} observation: unknown catalogue id")
            if not parses(card):
                raise ValueError(f"card {card.id} acceptable_region: unparseable region")
            from factorylab.charter.measurement import preflight_card

            preflight_card(card)
        for card_id, value in self.charter_prices:
            if card_id not in {c.id for c in self.charter.cards}:
                raise ValueError(f"card {card_id} lambda: unknown card id")
            if (type(value) not in (int, float) or not isfinite(value)
                    or not 0 <= value <= self.prices.lambda_max):
                raise ValueError(f"card {card_id} lambda: must be in [0, prices.lambda_max]")
        p = self.prices
        if (type(p.penalty_cap) not in (int, float) or not isfinite(p.penalty_cap)
                or not 0 < p.penalty_cap < 1):
            raise ValueError("prices.penalty_cap must be finite and in (0, 1)")
        if type(p.kappa) not in (int, float) or not isfinite(p.kappa) or p.kappa < 0:
            raise ValueError("prices.kappa must be finite and nonnegative")
        if min(p.eta, p.decay, p.lambda_max) <= 0 or p.min_window_events < 1:
            raise ValueError("prices: eta, decay, lambda_max > 0 and min_window_events >= 1")


def duration_ns(value: Any) -> int:
    """Return nanoseconds for an int of nanoseconds or a string like '1h', '30m', '10s', '7d'.

    The manifest and the CLI both state durations this way, so both read them here.
    """
    if isinstance(value, int):
        return value
    s = str(value).strip()
    units = {"s": NS_PER_SECOND, "m": 60 * NS_PER_SECOND, "h": NS_PER_HOUR, "d": NS_PER_DAY}
    if s[-1] in units:
        return int(Decimal(s[:-1]) * units[s[-1]])
    return int(s)


def _manifest_charter(raw: Any) -> tuple[Charter, tuple[tuple[str, float], ...]]:
    """Explicit charter tables yield edition 1 and card-specific errors for invalid fields."""
    if not isinstance(raw, dict):
        raise ValueError("charter must be a table")
    norms = raw.get("norms")
    if (not isinstance(norms, list) or not norms
            or any(not isinstance(n, str) or not n.strip() for n in norms)):
        raise ValueError("charter.norms must be a nonempty list of nonempty strings")
    if "edition" in raw and (type(raw["edition"]) is not int or raw["edition"] != 1):
        raise ValueError("charter.edition must be 1")
    rows = raw.get("cards", [])
    if not isinstance(rows, list):
        raise ValueError("charter.cards must be a list of tables")
    cards = []
    prices = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"card #{index} fields: expected a table")
        card_id = row.get("id", f"#{index}")
        for name in MetricCard.__dataclass_fields__:
            if name == "window":
                continue
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise ValueError(f"card {card_id} {name}: must be a nonempty string")
        window = row.get("window")
        if isinstance(window, dict) and "per" not in window:
            window = {**window, "per": None}  # TOML has no null literal.
        cards.append(MetricCard(**{name: row[name] for name in MetricCard.__dataclass_fields__
                                   if name != "window"}, window=window))
        if "lambda" in row:
            prices.append((card_id, row["lambda"]))
    return Charter(1, tuple(norms), tuple(cards)), tuple(prices)


def manifest_from_dict(d: dict[str, Any]) -> WorldManifest:
    conn = d.get("connectors", {})
    if not isinstance(conn, dict) or set(conn) - {
        "max_bytes", "timeout_s", "call_price_usd", "max_calls_per_window", "origin_denylist"
    }:
        raise ValueError("unknown connectors manifest key")
    connector_price = conn.get("call_price_usd", "0.001")
    if type(connector_price) not in (str, int):
        raise ValueError("connectors.call_price_usd must be exact USD text or integer")
    connectors = ConnectorsSpec(
        max_bytes=conn.get("max_bytes", 262144), timeout_s=conn.get("timeout_s", 10),
        call_price_micro=usd_to_micro(connector_price, rounding="exact"),
        max_calls_per_window=conn.get("max_calls_per_window", 60),
        origin_denylist=conn.get("origin_denylist", DEFAULT_DENYLIST),
    )
    venice_cap = (d.get("treasury") or {}).get("max_venice_per_window", "10")
    if type(venice_cap) not in (str, int):
        raise ValueError("treasury.max_venice_per_window must be exact USD text or integer")
    charter, charter_prices = (
        _manifest_charter(d["charter"]) if "charter" in d else (seed_charter(), ())
    )
    clock = d.get("clock", {})
    if set(clock) - {"min_tick"}:
        raise ValueError("clock accepts only min_tick; max_tick is derived")
    drip = None
    if "drip" in d:
        dd = d["drip"]
        drip = DripSpec(
            amount_micro=usd_to_micro(dd["amount_usd"], rounding="exact"),
            period_ns=duration_ns(dd["period"]),
            start_ns=duration_ns(dd.get("start", 0)),
            end_ns=duration_ns(dd["end"]),
        )
    ex = d.get("exchange", {})
    spot_pairs = d.get("venue", {}).get("spot_pairs", [])
    if (not isinstance(spot_pairs, list) or any(
            not isinstance(p, str) or p.count("/") != 1 or not p.endswith("/USDC")
            or not p.split("/")[0] for p in spot_pairs)
            or len(set(spot_pairs)) != len(spot_pairs)):
        raise ValueError("venue.spot_pairs must be a unique list of BASE/USDC pairs")
    exchange = ExchangeSpec(
        kind=ex.get("kind", "fake"),
        mainnet=bool(ex.get("mainnet", False)),
        coins=tuple(ex.get("coins", ["BTC", "ETH"])),
        spot_pairs=tuple(spot_pairs),
        seed=int(ex.get("seed", d.get("seed", 0))),
        start_cash_usd=str(ex.get("start_cash_usd", "100")),
        shocks=tuple(
            Shock(int(sh["step"]), str(sh["coin"]), str(sh["multiplier"]))
            for sh in ex.get("shocks", [])
        ),
    )
    models = tuple(
        ModelTier(
            id=m["id"],
            provider=m.get("provider", "fake"),
            input_usd_per_mtok=str(m["input_usd_per_mtok"]),
            output_usd_per_mtok=str(m["output_usd_per_mtok"]),
            reasoning=tuple(sorted((m.get("reasoning") or {}).items())),
            web=tuple(sorted((m.get("web") or {}).items())),
        )
        for m in d.get("models", [])
    )
    assemblies = tuple(
        AssemblySeed(
            id=a["id"],
            model_id=a["model_id"],
            accepts=tuple(a.get("accepts", ["Tick"])),
            max_tokens=int(a.get("max_tokens", 1024)),
            effort=a.get("effort", "medium"),
            memory_policy=a.get("memory_policy", "none"),
            role=a.get("role", "producer"),
            emits=tuple(a["emits"]) if "emits" in a else None,
            schemas=a.get("schemas", {}),
        )
        for a in d.get("assemblies", [])
    )
    ev = d.get("evaluation", {})
    evaluation = EvaluationSpec(
        consequence_share=float(ev.get("consequence_share", 0.3)),
        max_forecasts_per_verdict=int(ev.get("max_forecasts_per_verdict", 2)),
        verdict_timeout_events=int(ev.get("verdict_timeout_events", 20)),
        min_coverage=float(ev.get("min_coverage", 0.5)),
        trial_amount_micro=usd_to_micro(ev.get("trial_amount_usd", "0.10"), rounding="exact"),
        forecast_horizon_events=int(ev.get("forecast_horizon_events", 10)),
        consequence_backstop_events=ev.get("consequence_backstop_events", 200),
        adversarial_share=ev.get("adversarial_share", 0.15),
        sibling_share=ev.get("sibling_share", 0.5),
        sampling_step=ev.get("sampling_step", 0.1),
        sampling_cap=ev.get("sampling_cap", 0.7),
    )
    pr = d.get("prices") or {}
    prices = PricesSpec(
        eta=float(pr.get("eta", 0.5)),
        kappa=pr.get("kappa", 0.5),
        decay=float(pr.get("decay", 0.1)),
        lambda_max=float(pr.get("lambda_max", 1.0)),
        min_window_events=int(pr.get("min_window_events", 1)),
        penalty_cap=pr.get("penalty_cap", 0.5),
    )
    # Scripted providers run in virtual time, including live-shaped test fixtures.
    default_min_tick = "1s" if all(m.provider == "fake" for m in models) else "10s"
    nov = d.get("novelty", {})
    if "trial_invocations" in nov:
        raise ValueError("novelty.trial_invocations was replaced by novelty.trials "
                         "(settled consequences, not invocations)")
    tim = d.get("timing", {})
    term = d.get("termination", {})
    m = WorldManifest(
        name=d["name"],
        seed=int(d.get("seed", 0)),
        initial_balance_micro=usd_to_micro(d["initial_balance_usd"], rounding="exact"),
        drip=drip,
        exchange=exchange,
        models=models,
        assemblies=assemblies,
        novelty=NoveltySpec(nov.get("share", 0.1), duration_ns(nov.get("window", "1d")),
                            nov.get("trials", 3), nov.get("max_lifetime_windows", 6)),
        committee=CommitteeSpec(**d.get("committee", {})),
        immune=ImmuneSpec(**d.get("immune", {})),
        timing=TimingSpec(
            int(tim.get("min_ratio", 3)), float(tim.get("jitter_fraction", 0.2)),
            tim.get("cadence_sample", 200),
            tim.get("min_support", 30),
        ),
        termination=TerminationSpec(
            usd_to_micro(term.get("balance_floor_usd", 0), rounding="exact"),
            term.get("max_events"),
        ),
        charter=charter,
        charter_prices=charter_prices,
        charter_explicit="charter" in d,
        evaluation=evaluation,
        connectors=connectors,
        tools=ToolsSpec(
            int((d.get("tools") or {}).get("population_tool_micro_per_call", 50)),
            int((d.get("tools") or {}).get("max_leverage", 3)),
            int((d.get("tools") or {}).get("max_routers_per_kind", 3)),
            (d.get("tools") or {}).get("max_depth", 4),
            (d.get("tools") or {}).get("max_children", 3),
            (d.get("tools") or {}).get("max_tool_calls", 4),
        ),
        prices=prices,
        treasury=TreasurySpec(
            (d.get("treasury") or {}).get("insolvency_events", 20),
            (d.get("treasury") or {}).get("discovery_url", DISCOVERY_URL),
            reserve_address=(d.get("treasury") or {}).get("reserve_address"),
            hyperevm_gas_budget_wei=(d.get("treasury") or {}).get("hyperevm_gas_budget_wei", 0),
            base_gas_budget_wei=(d.get("treasury") or {}).get("base_gas_budget_wei", 0),
            max_transfer_fee_micro=usd_to_micro(
                (d.get("treasury") or {}).get("max_transfer_fee_usd", "2"), rounding="exact"),
            withdrawal_fee_micro=usd_to_micro(
                (d.get("treasury") or {}).get("withdrawal_fee_usd", "1"), rounding="exact"),
            cctp_max_fee_micro=usd_to_micro(
                (d.get("treasury") or {}).get("cctp_max_fee_usd", "0.10"), rounding="exact"),
            fake_fee_micro=usd_to_micro(
                (d.get("treasury") or {}).get("fake_fee_usd", "0.01"), rounding="exact"),
            max_request_micro=(d.get("treasury") or {}).get("max_request_micro", 500_000),
            reported_cost_multiple=(d.get("treasury") or {}).get("reported_cost_multiple", 10),
            max_venice_per_window=usd_to_micro(venice_cap, rounding="exact"),
        ),
        clock=ClockSpec(duration_ns(clock.get("min_tick", default_min_tick))),
        tick_interval_ns=duration_ns(d.get("tick_interval", "10s")),
        extra={k: v for k, v in d.items() if k.startswith("x_")},
    )
    m.validate()
    return m


def load_manifest(name_or_path: str) -> WorldManifest:
    """Load ``worlds/<name>.toml`` or an explicit path. Guarantees the result is validated."""
    p = Path(name_or_path)
    if not p.exists():
        p = WORLDS_DIR / f"{name_or_path}.toml"
    with open(p, "rb") as f:
        manifest = manifest_from_dict(tomllib.load(f))
    if manifest.name != p.stem:
        raise ValueError("manifest name must match its file stem")
    return manifest
