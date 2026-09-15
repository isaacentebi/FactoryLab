"""Immutable, versioned capability contracts and provenance."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import Money, require_money
from factorylab.kernel.wallet import Reservation


def _freeze(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("schema keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("schemas must contain JSON-compatible values")


@dataclass(frozen=True)
class PriceSpec:
    """Named unit prices remain nonnegative integer micro-USD and deeply immutable."""

    units: Mapping[str, Money]

    def __post_init__(self) -> None:
        for unit, amount in self.units.items():
            if not isinstance(unit, str) or not unit:
                raise ValueError("price units must be named")
            require_money(amount, nonnegative=True)
        object.__setattr__(self, "units", MappingProxyType(dict(self.units)))


@dataclass(frozen=True)
class ResourceBounds:
    """Specified ceilings are nonnegative integers; None means no declared ceiling."""

    max_duration_ns: int | None = None
    max_memory_bytes: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for value in (
            self.max_duration_ns,
            self.max_memory_bytes,
            self.max_input_tokens,
            self.max_output_tokens,
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("resource bounds must be nonnegative integers or None")


@dataclass(frozen=True)
class Contract:
    """Contract fields and nested schemas cannot change after construction."""

    id: str
    version: int
    kind: Literal[
        "model", "tool", "assembly", "router", "purchase", "exchange",
            "observation", "connector", "service"
    ]
    description: str
    input_schema: dict
    output_schema: dict
    price: PriceSpec
    permissions: frozenset[str]
    resource_bounds: ResourceBounds
    provenance: str = "seed"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("contract id is required")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("contract version must be a positive integer")
        if self.kind not in (
            "model", "tool", "assembly", "router", "purchase", "exchange",
            "observation", "connector", "service"
        ):
            raise ValueError("unknown contract kind")
        if not isinstance(self.input_schema, Mapping) or not isinstance(
            self.output_schema, Mapping
        ):
            raise TypeError("schemas must be mappings")
        if not isinstance(self.price, PriceSpec) or not isinstance(
            self.resource_bounds, ResourceBounds
        ):
            raise TypeError("PriceSpec and ResourceBounds are required")
        if any(
            not isinstance(permission, str) or not permission for permission in self.permissions
        ):
            raise ValueError("permissions must be named")
        object.__setattr__(self, "input_schema", _freeze(self.input_schema))
        object.__setattr__(self, "output_schema", _freeze(self.output_schema))
        object.__setattr__(self, "permissions", frozenset(self.permissions))


class Registry:
    """Versions and registration provenance are persistent and cannot be overwritten."""

    def __init__(self, ledger: Ledger) -> None:
        self.__ledger = ledger
        self.__contracts: dict[str, dict[int, Contract]] = {}

    def register(
        self,
        contract: Contract,
        by_handle: str | None = None,
        reservation: Reservation | None = None,
    ) -> None:
        """Append the next version, requiring one live novelty receipt for population writes."""
        from factorylab.kernel.reserve import NoveltyReserve

        if not isinstance(contract, Contract):
            raise TypeError("Contract required")
        versions = self.__contracts.get(contract.id, {})
        expected = max(versions, default=0) + 1
        if contract.version != expected:
            raise ValueError(f"registration requires version {expected}")
        issuer = None
        if by_handle is not None:
            if not isinstance(by_handle, str) or not by_handle:
                raise ValueError("population registration requires a handle")
            if not isinstance(reservation, Reservation):
                raise PermissionError("population registration requires a novelty reservation")
            issuer = reservation._issuer
            if not isinstance(issuer, NoveltyReserve):
                raise PermissionError("reservation was not issued by NoveltyReserve")
            issuer._validate_registration(reservation, contract, self.__ledger)
        elif reservation is not None:
            raise ValueError("seed registration does not consume a novelty receipt")
        registered = replace(contract, provenance=by_handle if by_handle is not None else "seed")
        self.__ledger.append(
            {
                "kind": "registry.register",
                "contract": registered,
                "handle": by_handle,
                "reservation_id": reservation.id if reservation is not None else None,
            }
        )
        if issuer is not None:
            issuer._consume_registration(reservation)
        self.__contracts.setdefault(contract.id, {})[contract.version] = registered

    def get(self, id: str, version: int | None = None) -> Contract:
        """Return the immutable requested version, or the latest version when omitted."""
        versions = self.__contracts[id]
        return versions[max(versions) if version is None else version]

    def available(self, kind: str, event_kind: str | None = None) -> list[Contract]:
        """Return latest matching contracts, filtering input kind const/enum declarations."""
        matches = []
        for id in sorted(self.__contracts):
            contract = self.get(id)
            if contract.kind != kind:
                continue
            restriction = contract.input_schema.get("properties", {}).get("kind", {})
            if event_kind is not None:
                if "const" in restriction and restriction["const"] != event_kind:
                    continue
                if "enum" in restriction and event_kind not in restriction["enum"]:
                    continue
            matches.append(contract)
        return matches

    def state(self) -> dict:
        """Retain every immutable version and provenance, including superseded contracts."""
        return {"contracts": {k: dict(v) for k, v in self.__contracts.items()}}

    def _restore_state(self, state: dict) -> None:
        """Authenticated checkpoint contracts replace bootstrap state without new registrations."""
        if self.__ledger.final:
            raise RuntimeError("world is final")
        self.__contracts = {k: dict(v) for k, v in state["contracts"].items()}
