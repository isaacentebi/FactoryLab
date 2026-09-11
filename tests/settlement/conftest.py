from dataclasses import dataclass

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import DecisionQueue
from factorylab.settlement import (
    ConsequenceStanding,
    Forecast,
    ForecastBook,
    Observer,
    PrevalenceBaseline,
    Settler,
    open_forecast_decision,
)


@dataclass
class SimClock:
    now: int = 100

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def clock():
    return SimClock()


@pytest.fixture
def ledger(clock):
    return Ledger(clock_ns=clock)


@pytest.fixture
def queue(ledger, clock):
    return DecisionQueue(ledger, clock_ns=clock)


@pytest.fixture
def book(ledger):
    return ForecastBook(ledger)


@pytest.fixture
def baseline():
    return PrevalenceBaseline()


@pytest.fixture
def standing():
    return ConsequenceStanding(min_coverage=0.75)


@pytest.fixture
def settler(book, queue, standing, baseline):
    return Settler(book, queue, standing, baseline, Observer())


@pytest.fixture
def forecast_factory():
    def make(**changes):
        arguments = dict(
            handle="forecast-1",
            evaluator_id="judge-a",
            about_handle="producer-1",
            predicate_id="wallet_up",
            params={"horizon_events": 10},
            q=0.75,
            made_at_event=0,
            due_at_event=10,
        )
        arguments.update(changes)
        return Forecast(**arguments)

    return make


@pytest.fixture
def seal_forecast(book, queue, clock, forecast_factory):
    def seal(*, parent_handle=None, **changes):
        forecast = forecast_factory(**changes)
        handle = open_forecast_decision(
            queue,
            evaluator_id=forecast.evaluator_id,
            event_id=f"event-{forecast.made_at_event}",
            q=forecast.q,
            deadline_ns=clock.now + 1_000,
            parent_handle=parent_handle,
            now_event=forecast.made_at_event,
            horizon=forecast.params["horizon_events"],
        )
        return book.seal(forecast_factory(**{**changes, "handle": handle}))

    return seal
