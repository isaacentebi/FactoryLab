import random
from dataclasses import dataclass

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import DecisionQueue, PropensityRecord
from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds


@dataclass
class Clock:
    now: int = 100

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(clock):
    return Ledger(clock_ns=clock)


@pytest.fixture
def contract_factory():
    def make(id="new", version=1, kind="assembly", **kwargs):
        return Contract(
            id=id,
            version=version,
            kind=kind,
            description="test capability",
            input_schema=kwargs.pop("input_schema", {}),
            output_schema={},
            price=PriceSpec({"call": 10}),
            permissions=frozenset(),
            resource_bounds=ResourceBounds(max_duration_ns=100),
            **kwargs,
        )

    return make


@pytest.fixture
def propensity_factory():
    def make(actor="learner", seed=1, actions=("new", "NOOP"), probs=(0.75, 0.25)):
        chosen = random.Random(seed).choices(actions, weights=probs, k=1)[0]
        return PropensityRecord(actions, probs, chosen, seed, actor, "a" * 64)

    return make


@pytest.fixture
def queue(ledger, clock):
    return DecisionQueue(ledger, clock_ns=clock)


@pytest.fixture
def open_decision(queue, clock, propensity_factory):
    def open_(**kwargs):
        actor = kwargs.pop("actor", "learner")
        arguments = dict(
            actor=actor,
            event_id="tick-1",
            propensity=propensity_factory(actor=actor),
            channel="outcome",
            deadline_ns=clock.now + 10,
            parent_handle=None,
            cost_ceiling=100,
        )
        arguments.update(kwargs)
        return queue.open(**arguments)

    return open_
