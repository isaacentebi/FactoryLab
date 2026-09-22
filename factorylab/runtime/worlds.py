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
import re
import tomllib
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any

from factorylab.charter.charter import Charter, MetricCard, Norm, seed_charter
from factorylab.charter.provenance import (
    PROVENANCE_FIELDS,
    charter_content,
    charter_digest,
)
from factorylab.kernel.money import usd_to_micro
from factorylab.runtime.cards import parses
from factorylab.runtime.notes import NotesSpec
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
    client_namespace: str | None = None
    # Free collateral this world precommits to leaving unused at the venue, on top
    # of the margin an order needs. A buffer declared before the orders exist, so
    # it cannot be reasoned away by the order that wants it. Zero by default: a
    # world that wants a cushion says so.
    collateral_headroom_usd: str = "0"
    # DEPRECATED and inert (architect decision D1: a principal cap is a Class-2
    # imposition). Still read, validated and hashed exactly as declared, so the
    # manifests that carry it load and keep their historical manifest hashes; nothing
    # enforces it. The venue's own account is the only limit on the principal used.
    # Dropped from the canonical JSON at its ``None`` default, as it always was.
    principal_usd: str | None = None


@dataclass(frozen=True)
class ModelTier:
    id: str
    provider: str  # "fake" | "openrouter" | "anthropic"
    input_usd_per_mtok: str
    output_usd_per_mtok: str
    reasoning: tuple[tuple[str, Any], ...] = ()  # OpenRouter `reasoning` object, e.g. effort=low
    # web purchasable: engine, mode, max_results, usd_per_request
    web: tuple[tuple[str, Any], ...] = ()
    # Provider request keys sent verbatim per model (an OpenRouter ``provider`` routing
    # block, say); the request's own keys and its JSON contract are applied after it.
    extra_body: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class AssemblySeed:
    id: str
    model_id: str
    accepts: tuple[str, ...]
    max_tokens: int | None = 1024
    effort: str = "medium"
    memory_policy: str = "none"
    role: str = "producer"
    emits: tuple[str, ...] | None = None
    schemas: dict[str, dict] = field(default_factory=dict)
    #: Edition 3, C2: the minimum ticks between this seat's paid wakes. The seat owns it
    #: after launch; the manifest only says where it starts. 1 is "every tick it is drawn".
    cadence_floor: int = 1
    #: Edition 3, C1: the first head of this seat's working state, a JSON object. The
    #: manifest lens lives here (``{"lens": "..."}``); the seat may overwrite it.
    initial_state: dict[str, Any] = field(default_factory=dict)
    #: The seat's own system prompt. ``None`` keeps the population-wide seed prompt, which
    #: is what every world before edition 3 used and what their roster hashes recorded.
    system_prompt: str | None = None

    def __post_init__(self) -> None:
        from factorylab.cortex.registration import output_contracts, seed_emits

        if self.max_tokens is not None and (
                type(self.max_tokens) is not int or self.max_tokens <= 0):
            raise ValueError("assembly max_tokens must be positive or provider-native")
        if type(self.cadence_floor) is not int or self.cadence_floor < 1:
            raise ValueError("assembly cadence_floor must be a positive integer of ticks")
        if not isinstance(self.initial_state, dict):
            raise ValueError("assembly initial_state must be a JSON object")
        if self.system_prompt is not None and (
                not isinstance(self.system_prompt, str) or not self.system_prompt.strip()):
            raise ValueError("assembly system_prompt must be nonempty text when present")
        emits, schemas = output_contracts(
            self.emits if self.emits is not None else seed_emits(self.role), self.schemas)
        object.__setattr__(self, "emits", emits)
        object.__setattr__(self, "schemas", schemas)


@dataclass(frozen=True)
class ToolsSpec:
    population_tool_micro_per_call: int = 50
    # DEPRECATED and inert (architect decision D1): leverage is whatever the venue
    # allows. Kept only because every manifest hash was computed with it.
    max_leverage: int = 3
    max_routers_per_kind: int = 3
    max_depth: int = 4
    max_children: int = 3
    max_tool_calls: int = 4
    #: Whether this world publishes the voluntary addressing capability. It is off
    #: by default, so address is a factor a run turns on rather than something that
    #: arrives with a code change, and a world that predates the key is unchanged.
    #: It gates a capability; it schedules nothing and wakes nobody.
    address_enabled: bool = False


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


def online_id(model_id: str) -> str:
    """The id of a model's search-capable route: its own, when it already names one.

    A menu entry may be written either way — a base id whose ``web`` table buys the
    provider's search plugin, or the ``:online`` id itself — and both name the same
    route, so a price and a plugin configuration are registered once, under the id a
    request actually carries.
    """
    return model_id if model_id.endswith(":online") else f"{model_id}:online"


@dataclass(frozen=True)
class WebSpec:
    """The search route, its flat call price and the ceiling on one search.

    ``search_model`` names a model on the menu; the tool calls its ``:online``
    route. With no model named there is no ``[web]`` block and no ``web.search``
    tool: a world that predates this keeps its manifest identity exactly.
    """

    search_model: str | None = None
    call_price_micro: int = 0
    max_call_micro: int = 0

    def __post_init__(self):
        if self.search_model is not None and not isinstance(self.search_model, str):
            raise ValueError("web.search_model must be a model id on the menu")
        for name in ("call_price_micro", "max_call_micro"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"web.{name} must be a non-negative integer")
        if self.search_model is not None and self.max_call_micro <= self.call_price_micro:
            raise ValueError("web.max_call_usd must leave room above the flat call price")


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
    # The exit route's Base mint: never forwarded, forwarded when the reserve has no
    # ETH (default), or always forwarded. The quote is read on-chain before signing.
    cctp_forwarding: str = "on_empty_gas"
    # $0.10 of headroom over the $0.20 the deployed CoreDepositWallet quotes on both networks.
    max_forward_fee_micro: int = 300_000
    max_forward_fees_per_window: int = 1_000_000
    # Reserve windows a forwarded mint may stay unobserved before the exit is stranded
    # (recoverably) and the treasury admits new transfers again.
    forward_wait_windows: int = 2


@dataclass(frozen=True)
class PricesSpec:
    """Price controller parameters. Not money: bare rates and bounds."""

    eta: float = 0.5
    decay: float = 0.1
    lambda_max: float = 1.0
    min_window_events: int = 1
    kappa: float = 0.5
    penalty_cap: float = 0.5
    # Floor on a decision's share of a generic (non-attributable) violation, so
    # splitting participation across many decisions cannot dilute it away.
    min_blame_share: float = 0.1
    # The flat price of one program seat call (C8), reserved and committed like a model call.
    program_micro_per_call: int = 50
    #: The price law (``charter.controller.PriceController``): ``integral`` is the
    #: shipped integrator every earlier world ran; ``pid`` adds the proportional
    #: gain ``kp`` and the derivative-on-measurement gain ``kd`` to the integral
    #: gain ``eta`` (essay II.II.b). All three keys are hash-neutral at their defaults.
    controller: str = "integral"
    kp: float = 0.0
    kd: float = 0.0


@dataclass(frozen=True)
class EvaluationSpec:
    consequence_share: float = 0.3
    max_forecasts_per_verdict: int = 2
    verdict_timeout_events: int = 20
    min_coverage: float = 0.5
    trial_amount_micro: int = 100_000  # novelty trial paid per registration
    forecast_horizon_events: int = 10
    grounded_horizon_ticks: int = 10
    consequence_backstop_events: int = 200
    adversarial_share: float = 0.15  # cap on router mass over antagonist assemblies
    sibling_share: float = 0.5  # share of the representative's meta score a sibling settles at
    sampling_step: float = 0.1  # consequence-mix step per divergent window
    sampling_cap: float = 0.7  # ceiling of the raised consequence mix
    #: What finally settles a producer decision. ``verdict`` is the shipped line: a
    #: judge opinion is the producer score. ``realized`` settles on observed
    #: consequence instead, so an approval that nothing bore out does not pay. The
    #: default is ``verdict``, so a world that predates the key is unchanged and a
    #: run turns the new line on deliberately.
    producer_feedback: str = "verdict"
    #: The exploration niche (essay II.II.b: learning death is prevented "as a fact
    #: about the world"). The share of producer decisions on which the kernel, not the
    #: seat, draws the action class, uniformly over the classes that act, and records
    #: that draw as the decision's propensity. Zero keeps every earlier world as it was.
    exploration_share: float = 0.0

    # Both horizons count world ticks consumed, not internal events (defect 1). The
    # field names predate that and are kept so every manifest keeps its meaning; the
    # manifest may also spell them ``verdict_timeout_ticks`` and
    # ``consequence_backstop_ticks``.
    @property
    def verdict_timeout_ticks(self) -> int:
        """World ticks a judgement waits for its judge before it is censored."""
        return self.verdict_timeout_events

    @property
    def consequence_backstop_ticks(self) -> int:
        """World ticks a return's consequence may stay open before it is marked."""
        return self.consequence_backstop_events


def _tick_horizon(ev: dict, name: str, default: int) -> Any:
    """Read one evaluation horizon under its tick name or its original name.

    ``<name>_ticks`` and ``<name>_events`` are one key in two spellings, both
    counted in world ticks; a manifest that gives both must give one number.
    """
    ticks, events = ev.get(f"{name}_ticks"), ev.get(f"{name}_events")
    if ticks is not None and events is not None and ticks != events:
        raise ValueError(f"evaluation.{name}_ticks and evaluation.{name}_events disagree")
    return ticks if ticks is not None else events if events is not None else default


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
    # A promised move counts once it clears this fraction of the frozen region's scale
    # (the observation's declared unit width); smaller moves are "did not move".
    promise_resolution: float = 0.01


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
class KillSpec:
    """The kill contract (edition 3, C5), precommitted in the manifest and nowhere else.

    ``wind_down = true``: every kill cancels the world's resting orders, closes its open
    perp positions and sells its spot balances at market before ``Terminated``, so a dead
    factory carries no exposure. ``false`` is the behaviour every world before edition 3
    had: the runtime stops and whatever is open at the venue stays open.
    """

    wind_down: bool = False
    #: Spot balances worth at most this are left alone: selling dust is a fee, not an exit.
    dust_micro: int = 1_000_000


@dataclass(frozen=True)
class ProvidersSpec:
    """Prepaid inference inventories, shown separately because they do not substitute.

    An OpenRouter balance cannot refill a Venice-only seat (GPT-6 §12.1). These are
    inventory facts for the world block and the accounting, never a spend authority.
    """

    openrouter_micro: int = 0
    venice_micro: int = 0


@dataclass(frozen=True)
class PromptSpec:
    """Which institutional facts every request carries inline, and which it retrieves.

    ``reference`` renders the whole institutional world inside the cached prefix,
    as every world before this key did. ``compact`` renders the norms, the
    capability index with its prices, and the facts a return cannot be well formed
    without; everything else is named in a directory and read back on demand from
    the same block the validators read.

    The default is ``reference``: a manifest that does not name a mode gets the
    prompt it always got, and hashes as it always did.
    """

    mode: str = "reference"


@dataclass(frozen=True)
class EndowmentSpec:
    """Locked backing and the tranches that release it, as offsets from the Launch (C1), and
    how each unlocked tranche is classified: ``base_share`` split equally across live seats,
    the remainder unallocated (C10)."""

    locked_micro: int = 0
    releases: tuple[tuple[int, int], ...] = ()  # (at_ns offset from launch, amount_micro)
    base_share: float = 0.8


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
    web: WebSpec = WebSpec()
    notes: NotesSpec = NotesSpec()
    prices: PricesSpec = PricesSpec()
    treasury: TreasurySpec = TreasurySpec()
    clock: ClockSpec = ClockSpec()
    committee: CommitteeSpec = CommitteeSpec()
    immune: ImmuneSpec = ImmuneSpec()
    endowment: EndowmentSpec = EndowmentSpec()
    kill: KillSpec = KillSpec()
    providers: ProvidersSpec = ProvidersSpec()
    prompt: PromptSpec = PromptSpec()
    tick_interval_ns: int = 10 * NS_PER_SECOND
    extra: dict[str, Any] = field(default_factory=dict)

    charter: Charter = field(default_factory=seed_charter)
    charter_prices: tuple[tuple[str, float], ...] = ()
    charter_explicit: bool = False
    # Admission provenance for a funded launch: the digest the ratification exported,
    # the roster it was surveyed against, and the digest of the cards actually loaded.
    charter_ratified_sha256: str | None = None
    charter_roster_sha256: str | None = None
    charter_content_sha256: str | None = None

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
                    online_id(m.id),
                    TokenPrice(base.input_micro, base.output_micro, per_request),
                )
        return t

    def web_config(self) -> dict[str, dict[str, Any]]:
        """OpenRouter web-plugin options per online variant (price key stripped)."""
        out: dict[str, dict[str, Any]] = {}
        for m in self.models:
            web = {k: v for k, v in dict(m.web).items() if k != "usd_per_request"}
            if dict(m.web):
                out[online_id(m.id)] = web
        return out

    def extra_body_config(self) -> dict[str, dict[str, Any]]:
        """Each model's verbatim provider request keys, by model id; absent when empty."""
        return {m.id: dict(m.extra_body) for m in self.models if m.extra_body}

    def canonical_json(self) -> str:
        payload = asdict(self)
        # Admission provenance does not change the world defined by identical cards.
        payload.pop("charter_explicit")
        # Preserve historical manifest identities for models without an extra body.
        for model in payload["models"]:
            if not model.get("extra_body"):
                model.pop("extra_body", None)
        for name in ("charter_ratified_sha256", "charter_roster_sha256",
                     "charter_content_sha256"):
            payload.pop(name)
        # Preserve historical manifest identities when the opt-in namespace is absent.
        if payload["exchange"]["client_namespace"] is None:
            payload["exchange"].pop("client_namespace")
        # Preserve historical manifest identities while the gas-route keys keep their defaults.
        for key, default in (("cctp_forwarding", "on_empty_gas"),
                             ("max_forward_fee_micro", 300_000),
                             ("max_forward_fees_per_window", 1_000_000),
                             ("forward_wait_windows", 2)):
            if payload["treasury"].get(key) == default:
                payload["treasury"].pop(key)
        # Edition 2 keys keep the identity of every manifest that predates them: a world
        # without locked backing, or at the default byte-day rent, hashes as it always did.
        if payload["endowment"] == asdict(EndowmentSpec()):
            payload.pop("endowment")
        # Edition 3's keys are hash-neutral at their defaults, so every world that predates
        # the kill contract, the provider inventories and the per-seat fields keeps both its
        # manifest identity and the roster hash its charter was ratified against.
        if payload["kill"] == asdict(KillSpec()):
            payload.pop("kill")
        if payload["providers"] == asdict(ProvidersSpec()):
            payload.pop("providers")
        # A world that names no prompt mode hashes exactly as it did before the key
        # existed: an added key may not rename a world that predates it, and every
        # roster digest a charter was ratified against stays what it was.
        if payload["prompt"] == asdict(PromptSpec()):
            payload.pop("prompt")
        # Likewise for the addressing switch: a world that does not publish address
        # hashes exactly as it did before the capability existed.
        if payload["tools"].get("address_enabled") is False:
            payload["tools"].pop("address_enabled")
        # And for the feedback line: a world settling producers on judge opinion is
        # the world every manifest already described.
        if payload["evaluation"].get("producer_feedback") == "verdict":
            payload["evaluation"].pop("producer_feedback")
        if payload["evaluation"].get("grounded_horizon_ticks") == 10:
            payload["evaluation"].pop("grounded_horizon_ticks")
        if payload["evaluation"].get("exploration_share") == 0.0:
            payload["evaluation"].pop("exploration_share")
        # An absent [web] block registers no search tool, so a world without one hashes
        # exactly as it did before web search existed.
        if payload["web"] == asdict(WebSpec()):
            payload.pop("web")
        for assembly in payload["assemblies"]:
            if assembly.get("cadence_floor") == 1:
                assembly.pop("cadence_floor", None)
            if not assembly.get("initial_state"):
                assembly.pop("initial_state", None)
            if assembly.get("system_prompt") is None:
                assembly.pop("system_prompt", None)
        if payload["notes"].get("micro_per_byte_day") == NotesSpec().micro_per_byte_day:
            payload["notes"].pop("micro_per_byte_day")
        # Preserve historical manifest identities while the blame floor keeps its default.
        if payload["prices"].get("min_blame_share") == 0.1:
            payload["prices"].pop("min_blame_share")
        # Preserve historical manifest identities while promise grading keeps its resolution.
        if payload["committee"].get("promise_resolution") == 0.01:
            payload["committee"].pop("promise_resolution")
        # Preserve historical manifest identities while the program call price is its default.
        if payload["prices"].get("program_micro_per_call") == 50:
            payload["prices"].pop("program_micro_per_call")
        # A world that names no price law runs the integrator and hashes as it did
        # before the PID existed: an added key may not rename a world that predates it.
        for key, default in (("controller", "integral"), ("kp", 0.0), ("kd", 0.0)):
            if payload["prices"].get(key) == default:
                payload["prices"].pop(key)
        # A world that precommits no collateral headroom hashes as it did before
        # the key existed: an added key may not rename a world that predates it.
        if payload["exchange"].get("collateral_headroom_usd") == "0":
            payload["exchange"].pop("collateral_headroom_usd")
        # A world that declares no trading principal hashes as it did before the key
        # existed: an added key may not rename a world that predates it.
        if payload["exchange"].get("principal_usd") is None:
            payload["exchange"].pop("principal_usd", None)
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

    def _validate_funded_admission(self) -> None:
        """Real money launches only on a fresh identity space and the ratified charter.

        The namespace keeps a funded world's client order IDs out of every other
        world's; the two digests bind the loaded cards and the roster that voted them
        to the exact artifact ``scripts/ratify_charter.py`` exported. The same digest
        functions serve both, so an existing ratified artifact verifies unchanged.
        """
        from factorylab.charter.provenance import roster_hash

        if self.exchange.client_namespace is None:
            raise ValueError(
                "mainnet requires exchange.client_namespace: a funded world needs its own "
                "venue identity space"
            )
        if self.charter_ratified_sha256 is None:
            raise ValueError("mainnet requires charter.ratified_sha256 from the ratified export")
        if self.charter_roster_sha256 is None:
            raise ValueError("mainnet requires charter.roster_sha256 from the ratified export")
        if self.charter_content_sha256 is None:
            raise ValueError("mainnet charter provenance needs the loaded charter table")
        if self.charter_content_sha256 != self.charter_ratified_sha256:
            raise ValueError("mainnet charter differs from the ratified charter digest")
        if roster_hash(self) != self.charter_roster_sha256:
            raise ValueError("mainnet roster differs from the roster the charter was ratified on")

    def _validate_endowment(self) -> None:
        """Locked backing is part of the initial balance and its tranches sum to it exactly."""
        e = self.endowment
        if type(e.locked_micro) is not int or e.locked_micro < 0:
            raise ValueError("endowment.locked_micro must be nonnegative integer money")
        if e.locked_micro > self.initial_balance_micro:
            raise ValueError("endowment.locked_micro cannot exceed the initial balance")
        previous = -1
        for item in e.releases:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("endowment.releases entries need at and amount_micro")
            at_ns, amount = item
            if type(at_ns) is not int or at_ns < 0:
                raise ValueError("endowment.releases at must be a nonnegative duration")
            if type(amount) is not int or amount < 1:
                raise ValueError("endowment.releases amount_micro must be positive integer money")
            if at_ns < previous:
                raise ValueError("endowment.releases must be in ascending order")
            previous = at_ns
        if sum(amount for _, amount in e.releases) != e.locked_micro:
            raise ValueError("endowment.releases must sum to endowment.locked_micro")

    def validate(self) -> None:
        namespace = self.exchange.client_namespace
        if self.prompt.mode not in ("reference", "compact"):
            raise ValueError("prompt.mode must be reference or compact")
        if self.evaluation.producer_feedback not in ("verdict", "realized"):
            raise ValueError("evaluation.producer_feedback must be verdict or realized")
        if not 0.0 <= self.evaluation.exploration_share <= 1.0:
            raise ValueError("evaluation.exploration_share must be within [0, 1]")
        if namespace is not None and (not isinstance(namespace, str) or len(namespace) != 32
                                      or any(c not in "0123456789abcdef" for c in namespace)):
            raise ValueError("exchange.client_namespace must be 32 lowercase hex characters")
        if (self.exchange.kind == "hyperliquid" and self.exchange.mainnet
                and self.charter_explicit is not True):
            # Real money launches on the population's charter, never the seed cards.
            # Testnet rehearsals may run on the seed charter before edition 1 is drafted.
            raise ValueError("live_exchange_requires_explicit_charter: mainnet needs [charter]")
        if self.initial_balance_micro < 0:
            raise ValueError("initial balance must be non-negative")
        share = self.endowment.base_share
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
            raise ValueError("endowment.base_share must be in (0, 1]")
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
                      "fake_fee_micro", "max_request_micro", "max_venice_per_window",
                      "max_forward_fee_micro", "max_forward_fees_per_window"):
            value = getattr(self.treasury, budget_field)
            if type(value) is not int or value < 0:
                raise ValueError(f"treasury.{budget_field} must be nonnegative integer money")
        if self.treasury.cctp_forwarding not in ("never", "on_empty_gas", "always"):
            raise ValueError("treasury.cctp_forwarding must be never, on_empty_gas or always")
        windows = self.treasury.forward_wait_windows
        if type(windows) is not int or windows < 1:
            raise ValueError("treasury.forward_wait_windows must be a positive integer")
        # A forwarded exit's burn carries maxFee up to the CCTP cap plus the forwarding cap;
        # the mint step must still fit the transfer fee cap after the principal burned.
        if (self.treasury.withdrawal_fee_micro + self.treasury.cctp_max_fee_micro
                + self.treasury.max_forward_fee_micro > self.treasury.max_transfer_fee_micro):
            raise ValueError("withdrawal, CCTP and forwarding fee caps exceed maximum transfer fee")
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
        if self.web.search_model is not None:
            # The tool calls the route, so the route must be priced: either the menu
            # names the ``:online`` id itself, or a base entry buys it with a ``web`` table.
            routes = ids | {online_id(m.id) for m in self.models if m.web}
            if online_id(self.web.search_model) not in routes:
                raise ValueError(
                    "web.search_model must name a search-capable model on the menu")
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
        grounded = self.evaluation.grounded_horizon_ticks
        if type(grounded) is not int or grounded < 1:
            raise ValueError("evaluation.grounded_horizon_ticks must be a positive integer")
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
        if self.exchange.mainnet and self.exchange.kind == "hyperliquid":
            self._validate_funded_admission()
        if self.exchange.shocks and self.exchange.kind != "fake":
            raise ValueError("price shocks exist only on the fake venue")
        for sh in self.exchange.shocks:
            if sh.step < 1 or Decimal(sh.multiplier) <= 0:
                raise ValueError("shock step must be >= 1 and multiplier positive")
        if self.drip is not None and (self.drip.period_ns <= 0 or self.drip.amount_micro < 0):
            raise ValueError("drip period must be positive and amount non-negative")
        self._validate_endowment()
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
        if (type(p.min_blame_share) not in (int, float) or not isfinite(p.min_blame_share)
                or not 0 <= p.min_blame_share <= 1):
            raise ValueError("prices.min_blame_share must be finite and in [0, 1]")
        if min(p.eta, p.decay, p.lambda_max) <= 0 or p.min_window_events < 1:
            raise ValueError("prices: eta, decay, lambda_max > 0 and min_window_events >= 1")
        if p.controller not in ("integral", "pid"):
            raise ValueError("prices.controller must be integral or pid")
        for name in ("kp", "kd"):
            value = getattr(p, name)
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ValueError(f"prices.{name} must be finite and nonnegative")
        if p.controller == "integral" and (p.kp or p.kd):
            raise ValueError("prices.kp and prices.kd need prices.controller = \"pid\"")


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
    for name in PROVENANCE_FIELDS:
        value = raw.get(name)
        if value is not None and (not isinstance(value, str)
                                  or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise ValueError(f"charter.{name} must be 64 lowercase hex characters")
    raw = charter_content(raw)
    norms = raw.get("norms")
    # Edition 3 carries each norm's definition in the charter object. The historical
    # bare-string form loads unchanged, with an empty definition, so every charter
    # written before this field existed keeps its content digest.
    if not isinstance(norms, list) or not norms:
        raise ValueError("charter.norms must be a nonempty list of nonempty strings")
    try:
        norms = [Norm.parse(n) for n in norms]
    except ValueError as exc:
        raise ValueError(f"charter.norms: {exc}") from None
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


def _committee(raw: dict) -> CommitteeSpec:
    spec = CommitteeSpec(**raw)
    resolution = spec.promise_resolution
    if (type(resolution) not in (int, float) or isinstance(resolution, bool)
            or not isfinite(resolution) or resolution <= 0):
        raise ValueError("committee.promise_resolution must be a finite positive number")
    return replace(spec, promise_resolution=float(resolution))


def manifest_from_dict(d: dict[str, Any]) -> WorldManifest:
    note = d.get("notes", {})
    if not isinstance(note, dict) or set(note) - {
        "max_keys", "max_bytes", "byte_window_micro", "micro_per_byte_day"
    }:
        raise ValueError("unknown notes manifest key")
    # ``byte_window_micro`` stays readable and maps to the per-byte call price; storage
    # rent is ``micro_per_byte_day`` (C3), at its default unless the manifest names one.
    notes = NotesSpec(**note)
    endowment = _manifest_endowment(d.get("endowment"))
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
    web_block = d.get("web", {})
    if not isinstance(web_block, dict) or set(web_block) - {
        "search_model", "call_price_micro", "max_call_usd"
    }:
        raise ValueError("unknown web manifest key")
    max_call = web_block.get("max_call_usd", "0")
    if type(max_call) not in (str, int):
        raise ValueError("web.max_call_usd must be exact USD text or integer")
    web = WebSpec(
        search_model=web_block.get("search_model"),
        call_price_micro=web_block.get("call_price_micro", 0),
        max_call_micro=usd_to_micro(max_call, rounding="exact"),
    )
    venice_cap = (d.get("treasury") or {}).get("max_venice_per_window", "10")
    if type(venice_cap) not in (str, int):
        raise ValueError("treasury.max_venice_per_window must be exact USD text or integer")
    forward_cap = (d.get("treasury") or {}).get("max_forward_fees_per_window", "1")
    if type(forward_cap) not in (str, int):
        raise ValueError("treasury.max_forward_fees_per_window must be exact USD text or integer")
    charter, charter_prices = (
        _manifest_charter(d["charter"]) if "charter" in d else (seed_charter(), ())
    )
    # The cards as written, digested exactly as the ratification export digested them.
    charter_content_sha256 = (charter_digest(charter_content(d["charter"]))
                              if isinstance(d.get("charter"), dict) else None)
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
    venue = d.get("venue", {})
    spot_pairs = venue.get("spot_pairs", [])
    headroom = str(venue.get("collateral_headroom_usd", "0"))
    try:
        if Decimal(headroom) < 0 or not Decimal(headroom).is_finite():
            raise ValueError
    except (ArithmeticError, ValueError):
        raise ValueError(
            "venue.collateral_headroom_usd must be a nonnegative exact decimal string"
        ) from None
    principal = venue.get("principal_usd")
    if principal is not None:
        principal = str(principal)
        try:
            if Decimal(principal) <= 0 or not Decimal(principal).is_finite():
                raise ValueError
        except (ArithmeticError, ValueError):
            raise ValueError(
                "venue.principal_usd must be a positive exact decimal string"
            ) from None
    if (not isinstance(spot_pairs, list) or any(
            not isinstance(p, str) or p.count("/") != 1 or not p.endswith("/USDC")
            or not p.split("/")[0] for p in spot_pairs)
            or len(set(spot_pairs)) != len(spot_pairs)):
        raise ValueError("venue.spot_pairs must be a unique list of BASE/USDC pairs")
    exchange = ExchangeSpec(
        kind=ex.get("kind", "fake"),
        client_namespace=ex.get("client_namespace"),
        mainnet=bool(ex.get("mainnet", False)),
        coins=tuple(ex.get("coins", ["BTC", "ETH"])),
        spot_pairs=tuple(spot_pairs),
        seed=int(ex.get("seed", d.get("seed", 0))),
        start_cash_usd=str(ex.get("start_cash_usd", "100")),
        collateral_headroom_usd=headroom,
        principal_usd=principal,
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
            extra_body=tuple(sorted((m.get("extra_body") or {}).items())),
        )
        for m in d.get("models", [])
    )
    assemblies = tuple(
        AssemblySeed(
            id=a["id"],
            model_id=a["model_id"],
            accepts=tuple(a.get("accepts", ["Tick"])),
            max_tokens=(None if a.get("max_tokens") == "provider"
                        or ("max_tokens" in a and a["max_tokens"] is None)
                        else a.get("max_tokens", 1024)),
            effort=a.get("effort", "medium"),
            memory_policy=a.get("memory_policy", "none"),
            role=a.get("role", "producer"),
            emits=tuple(a["emits"]) if "emits" in a else None,
            schemas=a.get("schemas", {}),
            cadence_floor=a.get("cadence_floor", 1),
            initial_state=a.get("initial_state", {}),
            system_prompt=a.get("system_prompt"),
        )
        for a in d.get("assemblies", [])
    )
    ev = d.get("evaluation", {})
    evaluation = EvaluationSpec(
        consequence_share=float(ev.get("consequence_share", 0.3)),
        max_forecasts_per_verdict=int(ev.get("max_forecasts_per_verdict", 2)),
        verdict_timeout_events=int(_tick_horizon(ev, "verdict_timeout", 20)),
        min_coverage=float(ev.get("min_coverage", 0.5)),
        trial_amount_micro=usd_to_micro(ev.get("trial_amount_usd", "0.10"), rounding="exact"),
        forecast_horizon_events=int(ev.get("forecast_horizon_events", 10)),
        grounded_horizon_ticks=ev.get("grounded_horizon_ticks", 10),
        consequence_backstop_events=_tick_horizon(ev, "consequence_backstop", 200),
        adversarial_share=ev.get("adversarial_share", 0.15),
        sibling_share=ev.get("sibling_share", 0.5),
        sampling_step=ev.get("sampling_step", 0.1),
        sampling_cap=ev.get("sampling_cap", 0.7),
        producer_feedback=_manifest_producer_feedback(ev.get("producer_feedback", "verdict")),
        exploration_share=float(ev.get("exploration_share", 0.0)),
    )
    pr = d.get("prices") or {}
    prices = PricesSpec(
        eta=float(pr.get("eta", 0.5)),
        kappa=pr.get("kappa", 0.5),
        decay=float(pr.get("decay", 0.1)),
        lambda_max=float(pr.get("lambda_max", 1.0)),
        min_window_events=int(pr.get("min_window_events", 1)),
        penalty_cap=pr.get("penalty_cap", 0.5),
        min_blame_share=pr.get("min_blame_share", 0.1),
        program_micro_per_call=int(pr.get("program_micro_per_call", 50)),
        controller=pr.get("controller", "integral"),
        kp=pr.get("kp", 0.0),
        kd=pr.get("kd", 0.0),
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
        committee=_committee(d.get("committee", {})),
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
        charter_ratified_sha256=(d.get("charter") or {}).get("ratified_sha256"),
        charter_roster_sha256=(d.get("charter") or {}).get("roster_sha256"),
        charter_content_sha256=charter_content_sha256,
        evaluation=evaluation,
        connectors=connectors,
        web=web,
        notes=notes,
        tools=ToolsSpec(
            int((d.get("tools") or {}).get("population_tool_micro_per_call", 50)),
            int((d.get("tools") or {}).get("max_leverage", 3)),
            int((d.get("tools") or {}).get("max_routers_per_kind", 3)),
            (d.get("tools") or {}).get("max_depth", 4),
            (d.get("tools") or {}).get("max_children", 3),
            (d.get("tools") or {}).get("max_tool_calls", 4),
            _manifest_address_enabled((d.get("tools") or {}).get("address_enabled", False)),
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
            cctp_forwarding=(d.get("treasury") or {}).get("cctp_forwarding", "on_empty_gas"),
            max_forward_fee_micro=usd_to_micro(
                (d.get("treasury") or {}).get("max_forward_fee_usd", "0.30"), rounding="exact"),
            max_forward_fees_per_window=usd_to_micro(forward_cap, rounding="exact"),
            forward_wait_windows=(d.get("treasury") or {}).get("forward_wait_windows", 2),
        ),
        clock=ClockSpec(duration_ns(clock.get("min_tick", default_min_tick))),
        tick_interval_ns=duration_ns(d.get("tick_interval", "10s")),
        endowment=endowment,
        kill=_manifest_kill(d.get("kill")),
        providers=_manifest_providers(d.get("providers")),
        prompt=_manifest_prompt(d.get("prompt")),
        extra={k: v for k, v in d.items() if k.startswith("x_")},
    )
    m.validate()
    return m


def _manifest_kill(raw: Any) -> KillSpec:
    """``[kill] wind_down = true`` (C5): what a kill owes the venue, fixed before launch."""
    if raw is None:
        return KillSpec()
    if not isinstance(raw, dict) or set(raw) - {"wind_down", "dust_usd"}:
        raise ValueError("kill accepts only wind_down and dust_usd")
    wind_down = raw.get("wind_down", False)
    if type(wind_down) is not bool:
        raise ValueError("kill.wind_down must be true or false")
    dust = raw.get("dust_usd", "1")
    if type(dust) not in (str, int):
        raise ValueError("kill.dust_usd must be exact USD text or integer")
    return KillSpec(wind_down=wind_down, dust_micro=usd_to_micro(dust, rounding="exact"))


def _manifest_providers(raw: Any) -> ProvidersSpec:
    """``[providers]``: the prepaid inventory behind each route, stated separately (C5)."""
    if raw is None:
        return ProvidersSpec()
    if not isinstance(raw, dict) or set(raw) - {"openrouter_usd", "venice_usd"}:
        raise ValueError("providers accepts only openrouter_usd and venice_usd")
    amounts = []
    for key in ("openrouter_usd", "venice_usd"):
        value = raw.get(key, 0)
        if type(value) not in (str, int):
            raise ValueError(f"providers.{key} must be exact USD text or integer")
        micro = usd_to_micro(value, rounding="exact")
        if micro < 0:
            raise ValueError(f"providers.{key} must be nonnegative")
        amounts.append(micro)
    return ProvidersSpec(openrouter_micro=amounts[0], venice_micro=amounts[1])


def _manifest_producer_feedback(raw: Any) -> str:
    """``[evaluation] producer_feedback``: a judge opinion or an observed consequence."""
    if raw not in ("verdict", "realized"):
        raise ValueError("evaluation.producer_feedback must be verdict or realized")
    return raw


def _manifest_address_enabled(raw: Any) -> bool:
    """``[tools] address_enabled``: exactly a boolean, never a truthy string or 1."""
    if type(raw) is not bool:
        raise ValueError("tools.address_enabled must be true or false")
    return raw


def _manifest_prompt(raw: Any) -> PromptSpec:
    """``[prompt] mode = "reference" | "compact"``: how much institution rides inline.

    An absent block is ``reference``, which is what every world rendered before
    this key existed. An unknown key or an unknown mode is refused here rather than
    quietly ignored, so a manifest cannot ask for a compaction it does not get.
    """
    if raw is None:
        return PromptSpec()
    if not isinstance(raw, dict) or set(raw) - {"mode"}:
        raise ValueError("prompt accepts only mode")
    mode = raw.get("mode", "reference")
    if mode not in ("reference", "compact"):
        raise ValueError("prompt.mode must be reference or compact")
    return PromptSpec(mode=mode)


def _manifest_endowment(raw: Any) -> EndowmentSpec:
    """``[endowment] locked_micro = N``, ``releases = [{at = "7d", amount_micro = N}]`` (C1)
    and ``base_share = 0.8`` (C10)."""
    if raw is None:
        return EndowmentSpec()
    if not isinstance(raw, dict) or set(raw) - {"locked_micro", "releases", "base_share"}:
        raise ValueError("endowment accepts only locked_micro, releases and base_share")
    locked = raw.get("locked_micro", 0)
    if type(locked) is not int:
        raise ValueError("endowment.locked_micro must be integer micro-USD")
    releases = raw.get("releases", [])
    if not isinstance(releases, list):
        raise ValueError("endowment.releases must be a list of tables")
    tranches = []
    for item in releases:
        if not isinstance(item, dict) or set(item) != {"at", "amount_micro"}:
            raise ValueError("endowment.releases entries need exactly at and amount_micro")
        amount = item["amount_micro"]
        if type(amount) is not int:
            raise ValueError("endowment.releases amount_micro must be integer micro-USD")
        at = item["at"]
        if type(at) not in (int, str):
            raise ValueError("endowment.releases at must be a duration such as '7d'")
        tranches.append((duration_ns(at), amount))
    return EndowmentSpec(locked_micro=locked, releases=tuple(tranches),
                         base_share=raw.get("base_share", 0.8))


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
