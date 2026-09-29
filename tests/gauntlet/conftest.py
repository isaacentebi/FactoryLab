"""One gauntlet world per key per pytest invocation, shared by every xdist worker.

A world is 10-20 s; several tests read the same one. The first worker to ask runs it
and publishes its detached evidence (rows, manifest, instrument readings, recorded
requests); the others wait on the same POSIX lock and read it, through the root
conftest's ``shared_result``.
"""

import pytest


@pytest.fixture(scope="session")
def shared_run(shared_result):
    """``shared_run(key, factory)``: the detached ``Run`` ``factory()`` returns, run once."""
    return lambda key, factory: shared_result(f"gauntlet-{key}",
                                              lambda: factory().detached())
