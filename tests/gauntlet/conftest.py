"""One gauntlet world per key per pytest invocation, shared by every xdist worker.

A world is 10-20 s; several tests read the same one. The first worker to ask runs it
and publishes its detached evidence (rows, manifest, instrument readings, recorded
requests); the others wait on the same POSIX lock and read it, as the root conftest's
``scripted_run`` does for plain scripted worlds.
"""

import fcntl
import pickle

import pytest


@pytest.fixture(scope="session")
def _gauntlet_cache(tmp_path_factory, request):
    base = tmp_path_factory.getbasetemp()
    directory = (base.parent if hasattr(request.config, "workerinput") else base) / "gauntlet"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture(scope="session")
def shared_run(_gauntlet_cache):
    """``shared_run(key, factory)``: the detached ``Run`` ``factory()`` returns, run once."""
    memo = {}

    def get(key, factory):
        if key in memo:
            return memo[key]
        path = _gauntlet_cache / f"{key}.pickle"
        with (_gauntlet_cache / f"{key}.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not path.exists():
                result = factory().detached()
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
                temporary.replace(path)
        memo[key] = pickle.loads(path.read_bytes())
        return memo[key]

    return get
