"""Provider-native completion limits become frozen runtime evidence."""

from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import CatalogueEntry, ModelRequest
from factorylab.world.scripted import ScriptedProvider

pytestmark = pytest.mark.gate


class CatalogueProvider(ScriptedProvider):
    catalogue_reads = 0

    def __init__(self, limit: int | None):
        super().__init__()
        self.limit = limit
        self.catalogue_calls = 0
        self.completion_calls = 0

    def catalogue(self):
        type(self).catalogue_reads += 1
        self.catalogue_calls += 1
        return [
            CatalogueEntry(
                id="fake-haiku",
                name="Fake Haiku",
                prompt_usd_per_token="0.000001",
                completion_usd_per_token="0.000005",
                context_length=1_000_000,
                max_completion_tokens=self.limit,
            )
        ]

    def complete(self, request):
        self.completion_calls += 1
        return super().complete(request)


def native_manifest():
    manifest = load_manifest("scripted")
    assemblies = list(manifest.assemblies)
    assemblies[0] = replace(assemblies[0], max_tokens=None)
    return replace(manifest, assemblies=tuple(assemblies))


def runtime(manifest, provider, *, ledger_path=None):
    return Runtime(
        manifest,
        events=0,
        seed=1,
        initial_balance_micro=None,
        ledger_path=ledger_path,
        drip=False,
        router_gamma=0.1,
        provider=provider,
    )


def test_native_seed_uses_provider_output_limit_for_spec_contract_and_prepaid_ceiling():
    provider = CatalogueProvider(16_384)
    rt = runtime(native_manifest(), provider)
    assembly = rt.assemblies["seed-observer"]

    assert assembly.spec.max_tokens == 16_384
    assert rt.registry.get("seed-observer").resource_bounds.max_output_tokens == 16_384
    request = ModelRequest("fake-haiku", "system", (), max_tokens=assembly.spec.max_tokens)
    assert assembly.model.ceiling(request) == rt.prices.price("fake-haiku").cost(
        73, 16_384
    )
    assert assembly.model.ceiling(request) > rt.prices.price("fake-haiku").cost(73, 4096)
    assert provider.completion_calls == 0


def test_missing_native_metadata_refuses_admission_before_trial_or_paid_call():
    provider = CatalogueProvider(None)
    rt = runtime(load_manifest("scripted"), provider)
    registry_before = rt.registry.state()
    budget_before = rt.budget.state()

    with pytest.raises(ValueError, match="provider-native max_tokens unavailable"):
        rt._register(
            "unknown-proposer",
            AssemblyProposal(
                id="native-child",
                role="producer",
                model_id="fake-haiku",
                system_prompt="Observe.",
                accepts=("Tick",),
                max_tokens=None,
                effort="low",
            ),
        )

    assert rt.registry.state() == registry_before
    assert rt.budget.state() == budget_before
    assert provider.completion_calls == 0


def test_native_population_assembly_resolves_before_admission():
    provider = CatalogueProvider(16_384)
    rt = runtime(load_manifest("scripted"), provider)
    rt._manage_reserve_window()
    rt.handle_to_assembly["founder-handle"] = "seed-observer"

    rt._register(
        "founder-handle",
        AssemblyProposal(
            id="native-child",
            role="producer",
            model_id="fake-haiku",
            system_prompt="Observe.",
            accepts=("Tick",),
            max_tokens=None,
            effort="low",
        ),
    )

    assert rt.assemblies["native-child"].spec.max_tokens == 16_384
    assert rt.registry.get("native-child").resource_bounds.max_output_tokens == 16_384
    assert provider.completion_calls == 0


def test_resume_uses_snapshotted_limit_without_reading_changed_catalogue(tmp_path):
    manifest = native_manifest()
    path = tmp_path / "native.jsonl"
    launched_provider = CatalogueProvider(16_384)
    rt = runtime(manifest, launched_provider, ledger_path=str(path))
    rt._launch()
    assert rt._snapshot("test")
    rt._ledger_lock.close()

    changed_provider = CatalogueProvider(65_536)
    CatalogueProvider.catalogue_reads = 0
    restored = resume_runtime(manifest, str(path), provider=changed_provider)

    assert CatalogueProvider.catalogue_reads == 0
    assert restored.catalogue_completion_limits["fake-haiku"] == 16_384
    assert restored.assemblies["seed-observer"].spec.max_tokens == 16_384
    assert restored.registry.get("seed-observer").resource_bounds.max_output_tokens == 16_384
