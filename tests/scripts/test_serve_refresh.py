"""The hosted seller refreshes its catalogue on the serving thread, never on another.

AGENTS engineering rules: "No global mutable state. No background threads." (s09 #3)
"""

import importlib.util
import threading
from pathlib import Path

import pytest


def _serve_module():
    spec = importlib.util.spec_from_file_location(
        "serve", Path(__file__).resolve().parents[2] / "deploy" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Server:
    """A finite fake server: each request advances the clock; the last one stops it."""

    def __init__(self, clock, requests):
        self.clock, self.left, self.threads, self.timeouts = clock, requests, [], []

    def handle_request(self):
        self.threads.append(threading.get_ident())
        self.timeouts.append(self.timeout)
        self.clock[0] += 30.0
        self.left -= 1
        if self.left == 0:
            raise KeyboardInterrupt


def test_service_refresh_runs_on_the_serving_thread():
    serve = _serve_module()
    clock = [0.0]
    server = _Server(clock, requests=5)
    refreshed = []
    started = threading.active_count()

    def refresh():
        refreshed.append((threading.get_ident(), clock[0]))
        if len(refreshed) == 1:
            raise OSError("ledger unreadable")  # the previous catalogue stands

    with pytest.raises(KeyboardInterrupt):
        serve.serve_and_refresh(server, refresh, 60.0, clock=lambda: clock[0])
    here = threading.get_ident()
    assert set(server.threads) == {here}
    # Five 30-second requests: refreshes fall due at 60 and 120, both on this thread.
    assert refreshed == [(here, 60.0), (here, 120.0)]
    assert server.timeouts[0] == 60.0 and all(t >= 0 for t in server.timeouts)
    assert threading.active_count() == started
    assert "threading" not in serve.__dict__
