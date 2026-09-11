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
from pathlib import Path
from typing import Any

from factorylab.world.models import PriceTable, TokenPrice

NS_PER_SECOND = 1_000_000_000
NS_PER_HOUR = 3_600 * NS_PER_SECOND
NS_PER_DAY = 24 * NS_PER_HOUR
WORLDS_DIR = Path(__file__).resolve().parents[2] / "worlds"


def usd_to_micro(value: str | int | float | Decimal) -> int:
    """Exact micro-USD for a dollar amount given as a string or Decimal-friendly number."""
    d = Decimal(str(value)) * Decimal(1_000_000)
    if d != d.to_integral_value():
        raise ValueError(f"{value!r} is not representable in micro-USD")
    return int(d)


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


@dataclass(frozen=True)
class ToolsSpec:
    population_tool_micro_per_call: int = 50
    max_leverage: int = 3


@dataclass(frozen=True)
class EvaluationSpec:
    consequence_share: float = 0.3
    max_forecasts_per_verdict: int = 2
    verdict_timeout_events: int = 20
    min_coverage: float = 0.5
    trial_amount_micro: int = 100_000  # novelty trial paid per registration
    forecast_horizon_events: int = 10


@dataclass(frozen=True)
class NoveltySpec:
    share: float
    window_ns: int


@dataclass(frozen=True)
class TimingSpec:
    min_ratio: int = 3
    jitter_fraction: float = 0.2


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
    tick_interval_ns: int = NS_PER_SECOND
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- derived

    def price_table(self) -> PriceTable:
        t = PriceTable()
        for m in self.models:
            per_in = Decimal(m.input_usd_per_mtok) / Decimal(1_000_000)
            per_out = Decimal(m.output_usd_per_mtok) / Decimal(1_000_000)
            base = TokenPrice.from_per_token(str(per_in), str(per_out))
            t.register(m.id, base)
            web = dict(m.web)
            if web:
                per_request = usd_to_micro(web.get("usd_per_request", "0"))
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
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def manifest_hash(self) -> str:
        """sha256 of the canonical JSON. Two manifests with equal hashes are the same world seed."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    # ---- validation

    def validate(self) -> None:
        if self.initial_balance_micro < 0:
            raise ValueError("initial balance must be non-negative")
        if not self.models:
            raise ValueError("a world needs at least one priced model tier")
        ids = {m.id for m in self.models}
        for a in self.assemblies:
            if a.model_id not in ids:
                raise ValueError(f"assembly {a.id} uses unpriced model {a.model_id}")
        if not 0 <= self.novelty.share <= 1:
            raise ValueError("novelty share must be in [0, 1]")
        if not 0 <= self.evaluation.consequence_share < 1:
            raise ValueError("consequence share must be in [0, 1)")
        for a in self.assemblies:
            if a.role not in ("producer", "evaluator", "meta"):
                raise ValueError(f"assembly {a.id} has unknown role {a.role}")
        if self.novelty.window_ns <= 0:
            raise ValueError("novelty window must be positive")
        if self.timing.min_ratio < 1:
            raise ValueError("timing min_ratio must be at least 1")
        if self.exchange.kind not in ("fake", "hyperliquid"):
            raise ValueError("unknown exchange kind")
        if self.exchange.kind == "hyperliquid" and self.exchange.mainnet and self.name != "funded":
            raise ValueError("mainnet is only allowed in the world named 'funded'")
        if self.exchange.shocks and self.exchange.kind != "fake":
            raise ValueError("price shocks exist only on the fake venue")
        for sh in self.exchange.shocks:
            if sh.step < 1 or Decimal(sh.multiplier) <= 0:
                raise ValueError("shock step must be >= 1 and multiplier positive")
        if self.drip is not None and (self.drip.period_ns <= 0 or self.drip.amount_micro < 0):
            raise ValueError("drip period must be positive and amount non-negative")


def _ns(value: Any) -> int:
    """Accept an int of ns, or a string like '1h', '30m', '10s', '7d'."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    units = {"s": NS_PER_SECOND, "m": 60 * NS_PER_SECOND, "h": NS_PER_HOUR, "d": NS_PER_DAY}
    if s[-1] in units:
        return int(Decimal(s[:-1]) * units[s[-1]])
    return int(s)


def manifest_from_dict(d: dict[str, Any]) -> WorldManifest:
    drip = None
    if "drip" in d:
        dd = d["drip"]
        drip = DripSpec(
            amount_micro=usd_to_micro(dd["amount_usd"]),
            period_ns=_ns(dd["period"]),
            start_ns=_ns(dd.get("start", 0)),
            end_ns=_ns(dd["end"]),
        )
    ex = d.get("exchange", {})
    exchange = ExchangeSpec(
        kind=ex.get("kind", "fake"),
        mainnet=bool(ex.get("mainnet", False)),
        coins=tuple(ex.get("coins", ["BTC", "ETH"])),
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
        )
        for a in d.get("assemblies", [])
    )
    ev = d.get("evaluation", {})
    evaluation = EvaluationSpec(
        consequence_share=float(ev.get("consequence_share", 0.3)),
        max_forecasts_per_verdict=int(ev.get("max_forecasts_per_verdict", 2)),
        verdict_timeout_events=int(ev.get("verdict_timeout_events", 20)),
        min_coverage=float(ev.get("min_coverage", 0.5)),
        trial_amount_micro=usd_to_micro(ev.get("trial_amount_usd", "0.10")),
        forecast_horizon_events=int(ev.get("forecast_horizon_events", 10)),
    )
    nov = d.get("novelty", {})
    tim = d.get("timing", {})
    term = d.get("termination", {})
    m = WorldManifest(
        name=d["name"],
        seed=int(d.get("seed", 0)),
        initial_balance_micro=usd_to_micro(d["initial_balance_usd"]),
        drip=drip,
        exchange=exchange,
        models=models,
        assemblies=assemblies,
        novelty=NoveltySpec(float(nov.get("share", 0.1)), _ns(nov.get("window", "1d"))),
        timing=TimingSpec(int(tim.get("min_ratio", 3)), float(tim.get("jitter_fraction", 0.2))),
        termination=TerminationSpec(
            usd_to_micro(term.get("balance_floor_usd", 0)),
            term.get("max_events"),
        ),
        evaluation=evaluation,
        tools=ToolsSpec(
            int((d.get("tools") or {}).get("population_tool_micro_per_call", 50)),
            int((d.get("tools") or {}).get("max_leverage", 3)),
        ),
        tick_interval_ns=_ns(d.get("tick_interval", "1s")),
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
        return manifest_from_dict(tomllib.load(f))
