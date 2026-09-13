"""Everything outside the factory, behind one adapter each.

Owns the exchange adapters, the compute rails (OpenRouter, Venice, x402
sellers) and the wire parser they share, the x402 payment client, the treasury
rails and CCTP, HTTP connectors, metering, and the scripted stand-ins the
deterministic worlds run on. Every external fault arrives here as a typed
error carrying no transport body, so nothing that could hold a credential
crosses the boundary.

Imports exactly one thing from this project: ``kernel.money``, because a price
quoted outside must become integer micro-USD by the same rule everywhere.
``tests/test_package_boundaries.py`` enforces it.
"""
