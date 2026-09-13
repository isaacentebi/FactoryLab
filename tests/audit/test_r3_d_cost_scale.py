"""Round three T18: resource pressure follows the edition's own cap."""

from dataclasses import replace

import pytest

from factorylab.charter.controller import CardRegion, PriceController, relative_region, violation
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.cards import region_for
from factorylab.runtime.worlds import load_manifest


def test_edition_one_cost_cap_has_fraction_card_relative_weight():
    manifest = load_manifest("worlds/edition1-example.toml")
    card = next(c for c in manifest.charter.cards if c.id == "model_cost_efficiency")
    region = relative_region(region_for(card, rolling={}))
    fraction = relative_region(region_for(
        replace(card, observation="well_formed_rate", units="fraction",
                acceptable_region="at most 0.25"), rolling={}))
    assert region.hi == 500
    assert violation(region, 1_000) == pytest.approx(violation(fraction, 0.5))
    assert violation(region, 1_000) == pytest.approx(1.0)


def test_ten_edition_one_cost_violations_survive_one_compliant_window():
    manifest = load_manifest("worlds/edition1-example.toml")
    card = next(c for c in manifest.charter.cards if c.id == "model_cost_efficiency")
    controller = PriceController(Ledger(), eta=manifest.prices.eta,
                                 decay=manifest.prices.decay,
                                 lambda_max=manifest.prices.lambda_max,
                                 min_window_events=manifest.prices.min_window_events,
                                 kappa=manifest.prices.kappa)
    controller.register(relative_region(region_for(card, rolling={})))
    for event in range(10):
        controller.observe(card.id, 1_000, window_end_event=event)
    assert controller.penalty({card.id: 1_000}) == pytest.approx(1.0)
    controller.observe(card.id, 500, window_end_event=10)
    assert controller.price(card.id) == pytest.approx(0.9)


@pytest.mark.parametrize("region,scale", [
    (CardRegion("zero", "max", None, 0, 2), 2),
    (CardRegion("negative", "min", -4, None, 2), 4),
    (CardRegion("band", "band", -2, 4, 1), 6),
    (CardRegion("wide", "band", -1e308, 1e308, 1), 1),
])
def test_relative_regions_use_declared_units_when_no_finite_bound_scale_exists(region, scale):
    normalized = relative_region(region)
    assert normalized.scale == scale
    assert (normalized.kind, normalized.lo, normalized.hi) == (region.kind, region.lo, region.hi)
