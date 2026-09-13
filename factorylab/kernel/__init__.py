"""The physics: what the factory can and cannot do, enforced in code rather than announced.

Owns conserved integer money, the sealed append-only diary and its seal, event
delivery, the contract registry, the decision queue, the novelty reserve, loop
timing and termination. Every invariant here has at least one test that
attempts to violate it and asserts failure.

Imports nothing from this project but ``kernel`` itself, and no threading: a
layer that everything rests on cannot depend on what is built above it.
``tests/kernel/test_boundaries.py`` enforces both, and that no module here
holds a mutable container at import time.
"""
