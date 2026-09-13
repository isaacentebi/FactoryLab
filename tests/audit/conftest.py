"""Offline W1 evidence from fake adapters; no item-key files are needed."""

import json
from dataclasses import replace

import pytest

from factorylab.kernel.ledger import canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest


@pytest.fixture(scope="session")
def w1_scripted_diary():
    manifest = load_manifest("scripted")
    manifest = replace(manifest, immune=replace(manifest.immune, k=2, gap_threshold=.6))
    rt = Runtime(manifest, events=800, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=.1)
    entries = []
    append = rt.ledger.append

    def capture(item):
        seq = append(item)
        if item["kind"] != "snapshot":
            entries.append(json.loads(canonical(dict(item, seq=seq))))
        return seq

    rt.ledger.append = capture
    rt.run()
    return rt, entries
