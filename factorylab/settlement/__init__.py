"""Sealed forecasts receive observable, addressable consequence scores.

Owns the forecast book and its seals, Brier scoring against a prevalence
baseline, the lot table that turns fills into realised consequence, the settlement
objects edition 3's third round separates (``receipts``: execution receipts,
learning receipts and commitments), and the standing each judge
accumulates from how its own forecasts settled.

Imports ``kernel`` and the standard library, and nothing else: a score must not
be able to reach the thing it is scoring.
``tests/settlement/test_settlement_boundaries.py`` enforces it.
"""

from factorylab.settlement.forecast import Forecast, ForecastBook, open_forecast_decision
from factorylab.settlement.receipts import (
    Commitment,
    ExecutionReceipt,
    LearningReceipt,
    ReceiptBook,
)
from factorylab.settlement.scoring import PrevalenceBaseline, brier
from factorylab.settlement.settle import Settled, Settler
from factorylab.settlement.standing import ConsequenceStanding
from factorylab.settlement.vocabulary import SEED_VOCABULARY, Observer, Predicate, WindowFacts

__all__ = (
    "SEED_VOCABULARY",
    "Commitment",
    "ConsequenceStanding",
    "ExecutionReceipt",
    "Forecast",
    "ForecastBook",
    "LearningReceipt",
    "Observer",
    "Predicate",
    "PrevalenceBaseline",
    "ReceiptBook",
    "Settled",
    "Settler",
    "WindowFacts",
    "brier",
    "open_forecast_decision",
)
