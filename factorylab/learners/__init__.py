"""No-regret learning over a declared action set, and nothing else.

Owns Hedge, EXP3, the Blum-Mansour swap-regret reduction, the delayed-feedback
wrappers and the router that samples among them. A learner is a pure function
of its own state and the feedback it is handed: no wallet, no ledger, no clock,
no knowledge of what its actions mean.

Imports nothing from this project at all, which is what makes a learner
replaceable by the population without touching physics.
``tests/test_package_boundaries.py`` enforces it.
"""
