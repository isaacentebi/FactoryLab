"""A live world's published schematics, rendered before launch without arming anything.

Chapter II §I.b: the informational schematics of the factory are public, "the
structures of requests and rewards" included (AGENTS.md rule 3). A charter is
ratified on them before a world launches (``scripts/charter_session.py``), so the
world block its voters read must be the one a live seat of that world reads at
launch: the same tools, contracts, custody accounts and sections. It must also be
true. A venue that has not been read is published as not read, never as a fake
venue's invented cash, and a rail that cannot move money publishes only the
directions the world's launcher admits.

This module is the one non-arming construction path of the runtime. It builds the
live runtime of a manifest over inert stand-ins that carry the real adapters'
published identity (the venue's name, endpoint and constants; the launcher's rail
name, admitted directions and gas table) and nothing else: no key, no signer, no
client, no transport, and the model catalogue it reads is the manifest's own
(``ManifestCatalogue``), never a provider's. Every observation they are asked for
raises ``NotYetRead``, which the world block already renders as an unavailable read;
every venue or x402 write raises ``InertRender`` and every rail step ``RailError``.
The runtime it builds is marked ``schematics_only``: ``run``,
launch and event processing refuse it (``refuse_to_run``), and ``render_schematics``
returns only the world block, never the runtime, so no world runs through this path.
"""

from __future__ import annotations

from typing import Any

from factorylab.world.evm import RailError
from factorylab.world.exchange import HyperliquidExchange


class NotYetRead(RuntimeError):
    """An observation the schematics render did not make: it reads no venue or rail."""


class InertRender(RuntimeError):
    """A schematics-only runtime was asked to act: it runs no world and moves nothing."""


#: Why a schematics-only runtime refuses to run, launch or process an event.
SCHEMATICS_ONLY_REFUSED = (
    "schematics_only: this runtime renders a world's published schematics before launch "
    "(factorylab/runtime/published.py); it runs no world")


#: The Hyperliquid adapter's writes: orders, cancels, closes, leverage and vault moves.
VENUE_WRITES = frozenset({"place", "cancel", "close", "set_leverage", "vault_create",
                          "vault_transfer"})


def _unread(name: str):
    def read(self, *_args, **_kwargs):
        raise NotYetRead(f"venue.{name}: not yet read; the schematics render reads no venue")

    read.__name__ = name
    return read


def _unsent(name: str):
    def write(self, *_args, **_kwargs):
        raise InertRender(SCHEMATICS_ONLY_REFUSED)

    write.__name__ = name
    return write


class ManifestCatalogue:
    """A provider that answers ``catalogue()`` from the manifest's own model table.

    A world whose seats size their completions natively ("provider" max_tokens,
    edition 5 and 6) needs each model's completion limit before its runtime can
    build a world block. The prices here are the manifest's, and the context and
    completion limits fixed ones, never a network read. It has no ``complete``: it
    answers no model call.
    """

    name = "manifest-catalogue"

    def __init__(self, manifest) -> None:
        self.manifest = manifest

    def catalogue(self) -> list:
        from decimal import Decimal

        from factorylab.world.models import CatalogueEntry

        # Exact decimal division: money is never a float (a float gave 1.0000000000000001e-07).
        rows = []
        for model in self.manifest.models:
            rows.append(CatalogueEntry(
                id=model.id, name=model.id,
                prompt_usd_per_token=str(Decimal(model.input_usd_per_mtok) / 1_000_000),
                completion_usd_per_token=str(Decimal(model.output_usd_per_mtok) / 1_000_000),
                context_length=200_000, max_completion_tokens=100_000))
        return rows


class InertVenue:
    """The Hyperliquid adapter's published identity, with no client behind it.

    Guarantees the name, endpoint, markets and class constants a live
    ``HyperliquidExchange`` of this spec publishes, and a method of every public name
    that adapter has: a read raises ``NotYetRead`` (an account, a listing, a mid or a
    fill is never answered, so nothing is invented) and a write (``VENUE_WRITES``)
    raises ``InertRender``, so nothing is sent. It holds no wallet and no SDK client,
    so it can sign nothing.
    """

    def __init__(self, spec: Any):
        from hyperliquid.utils import constants

        self.name = "hyperliquid-mainnet" if spec.mainnet else "hyperliquid-testnet"
        self.base_url = constants.MAINNET_API_URL if spec.mainnet else constants.TESTNET_API_URL
        self.coins, self.spot_pairs = spec.coins, spec.spot_pairs  # as live_exchange passes them


def _mirror_the_live_adapter() -> None:
    """Give ``InertVenue`` every public name of ``HyperliquidExchange``, once, at import.

    Constants are the adapter's published facts (the funding interval, the fee fields);
    every public method is a read or a write of the venue, and neither is made.
    """
    for name, value in vars(HyperliquidExchange).items():
        if not name.startswith("_") and not isinstance(value, property):
            setattr(InertVenue, name, value if not callable(value)
                    else _unsent(name) if name in VENUE_WRITES else _unread(name))


_mirror_the_live_adapter()


class InertRail:
    """A treasury rail's published contract, from the rail a world's launcher builds.

    Guarantees ``name``, ``ALLOWED`` (the directions ``treasury.transfer`` publishes,
    ``admitted_directions``) and ``GAS_BUDGETS`` (``gas_gates``) are the ones given,
    and that every other call raises: a balance is ``NotYetRead``, and a plan,
    preflight, prepare, send or poll is a ``RailError``. It holds no key.
    """

    def __init__(self, name: str, allowed: tuple[str, ...],
                 gas_budgets: dict[str, tuple[str, ...]] | None = None):
        self.name, self.ALLOWED = name, tuple(allowed)
        self.GAS_BUDGETS = dict(gas_budgets or {})

    @classmethod
    def of(cls, rail_class: type, *, testnet: bool) -> InertRail:
        """The published contract of ``rail_class`` (``bootstrap.rail_class``'s choice)
        on a testnet or mainnet venue: its name, ``ALLOWED`` and gas table.

        ``ALLOWED`` is the rail's own code (``LiveRail``'s depends on its venue), read on
        an instance its ``__init__`` never ran: it holds no exchange, no key and no
        signer, carries only the venue's ``testnet`` fact, and is dropped here.
        """
        probe = object.__new__(rail_class)
        probe.testnet = testnet
        return cls(rail_class.name, probe.ALLOWED, getattr(rail_class, "GAS_BUDGETS", None))

    @classmethod
    def published_by(cls, rail: Any) -> InertRail:
        """The contract a launcher's wrapper publishes over an ``InertRail``: its name,
        its ``admitted_directions`` and the gas table it reads through, exactly as the
        runtime reads them from the wrapped rail it installs."""
        from factorylab.world.treasury import admitted_directions

        return cls(rail.name, admitted_directions(rail), getattr(rail, "GAS_BUDGETS", None))

    def balances(self) -> dict:
        raise NotYetRead("treasury pots: not yet read; the schematics render reads no rail")

    def gas_view(self, *_args) -> dict:
        raise NotYetRead("treasury gas: not yet read; the schematics render reads no rail")

    def _refuse(self, *_args, **_kwargs):
        raise RailError(SCHEMATICS_ONLY_REFUSED)

    plan = preflight = prepare = send = poll = verify_receipt = bind_guard = _refuse


class InertMarket:
    """An x402 market that quotes, registers and completes nothing (the rehearsal
    runner's ``DeniedMarket`` contract): no paid call is ever reachable."""

    max_request_micro = 0

    def affordable(self, _model_id: str, _ceiling_micro: int) -> tuple[bool, str]:
        return False, "x402: schematics render"

    def quote(self, *_args, **_kwargs):
        raise InertRender(SCHEMATICS_ONLY_REFUSED)

    register = complete = quote


def check_inert(manifest: Any, *, exchange: Any, rail: Any, provider: Any, market: Any,
                ledger_path: Any, journal: Any, lock: Any, capital_loop: Any,
                clock_source: Any) -> None:
    """Refuse a schematics-only construction unless every adapter is inert.

    Guarantees the path builds a live manifest's runtime only over ``InertVenue``,
    ``InertRail``, ``InertMarket`` and ``ManifestCatalogue`` (so no provider is built
    from the environment or read over the network), with no diary, no journal, no
    lock, no supplied clock and no capital-loop opt-in: nothing it constructs can
    sign, send or write a ledger file.
    """
    problems = [
        what for what, bad in (
            ("a live manifest", manifest.exchange.kind == "fake"),
            # Exactly these classes: a subclass could carry a client or a key.
            ("an InertVenue exchange", type(exchange) is not InertVenue),
            ("an InertRail", type(rail) is not InertRail),
            ("an InertMarket", type(market) is not InertMarket),
            ("a ManifestCatalogue provider", type(provider) is not ManifestCatalogue),
            ("no ledger path", ledger_path is not None),
            ("no journal", journal is not None),
            ("no ledger lock", lock is not None),
            ("no capital-loop opt-in", capital_loop is not False),
            ("no supplied clock", clock_source is not None),
        ) if bad]
    if problems:
        raise ValueError("schematics_only requires " + ", ".join(problems))


def refuse_to_run(rt: Any) -> None:
    """Raise ``InertRender`` when ``rt`` was built by this path: it runs no world."""
    if getattr(rt, "schematics_only", False):
        raise InertRender(SCHEMATICS_ONLY_REFUSED)


def render_schematics(manifest: Any, *, rail: InertRail) -> dict[str, Any]:
    """The world block a live seat of ``manifest`` reads at launch, as published facts.

    Guarantees the runtime is the one the launcher builds for this manifest, on a
    clock of the live kind and the manifest's own model catalogue, with ``rail``'s
    published contract in place of the rail it signs with, and every venue and rail
    observation reported as not yet read. The
    runtime is dropped here: only the block leaves, so it can never be run.
    """
    from factorylab.runtime.loop import Runtime

    rt = Runtime(manifest, events=1, seed=None, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1, provider=ManifestCatalogue(manifest), market=InertMarket(),
                 exchange=InertVenue(manifest.exchange), _schematics_rail=rail)
    return rt._world_block()
