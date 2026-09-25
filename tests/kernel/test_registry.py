from dataclasses import FrozenInstanceError, replace

import pytest

from factorylab.kernel.registry import PriceSpec, Registry, ResourceBounds
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.wallet import Reservation, Wallet


def test_contracts_are_deeply_immutable(ledger, contract_factory):
    schema = {"properties": {"kind": {"enum": ["Tick", "Fill"]}}}
    contract = contract_factory(input_schema=schema)
    registry = Registry(ledger)
    registry.register(contract)
    schema["properties"]["kind"]["enum"].append("Drip")
    assert registry.available("assembly", "Tick") == [contract]
    assert registry.available("assembly", "Drip") == []
    for operation in (
        lambda: contract.input_schema["properties"].__setitem__("x", {}),
        lambda: contract.input_schema["properties"]["kind"]["enum"].append("Drip"),
        lambda: contract.price.units.__setitem__("call", 0),
    ):
        with pytest.raises((TypeError, AttributeError)):
            operation()
    with pytest.raises(FrozenInstanceError):
        contract.version = 2
    with pytest.raises(FrozenInstanceError):
        contract.resource_bounds.max_duration_ns = 0


def test_registry_versions_provenance_and_kind_filter(ledger, contract_factory):
    registry = Registry(ledger)
    first = contract_factory(provenance="forged-handle")
    registry.register(first)
    assert registry.get("new").provenance == "seed"
    for version in (1, 3):
        with pytest.raises(ValueError, match="version 2"):
            registry.register(replace(first, version=version))
    second = replace(first, version=2, description="revision")
    registry.register(second)
    assert registry.get("new", 1).description == first.description
    assert registry.get("new").version == 2
    registry.register(contract_factory(id="purchase", kind="purchase"))
    assert [item.id for item in registry.available("purchase")] == ["purchase"]
    assert len(registry.available("assembly")) == 1
    with pytest.raises(KeyError):
        registry.get("missing")


def test_invariant_5_population_registration_requires_authentic_novelty_receipt(
    ledger,
    clock,
    contract_factory,
):
    registry = Registry(ledger)
    contract = contract_factory()
    wallet = Wallet(100, ledger, clock_ns=clock)
    for receipt in (None, Reservation("fake", 10, "h", "novelty"), wallet.reserve(10, "h", "new")):
        with pytest.raises(PermissionError):
            registry.register(contract, by_handle="h", reservation=receipt)
    reserve = NoveltyReserve(0.2, has_history=lambda _: False, ledger=ledger, clock_ns=clock)
    reserve.open_window(clock.now, 100)
    receipt = reserve.reserve_for(contract, 10)
    with pytest.raises(PermissionError):
        registry.register(replace(contract, description="changed"), "h", receipt)
    registry.register(contract, by_handle="h", reservation=receipt)
    assert registry.get("new").provenance == "h"
    assert contract.provenance == "seed"
    # A consumed receipt cannot authorize a second registry, even on the same world.
    with pytest.raises(PermissionError):
        Registry(ledger).register(contract, "h", receipt)
    assert ledger.verify()


def test_event_const_and_unrestricted_schemas(ledger, contract_factory):
    registry = Registry(ledger)
    registry.register(contract_factory(id="all"))
    registry.register(
        contract_factory(id="tick", input_schema={"properties": {"kind": {"const": "Tick"}}})
    )
    assert [item.id for item in registry.available("assembly", "Tick")] == ["all", "tick"]
    assert [item.id for item in registry.available("assembly", "Fill")] == ["all"]


@pytest.mark.parametrize("amount", [-1, 1.0, True])
def test_price_specs_reject_invalid_money(amount):
    with pytest.raises((ValueError, TypeError)):
        PriceSpec({"token": amount})


def test_resource_bounds_and_contract_versions_validate(contract_factory):
    for bound in (-1, 1.0, True):
        with pytest.raises(ValueError):
            ResourceBounds(max_memory_bytes=bound)
    for version in (0, -1, True):
        with pytest.raises(ValueError):
            contract_factory(version=version)
