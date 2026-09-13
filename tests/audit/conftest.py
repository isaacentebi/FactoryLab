"""Offline W1 evidence from fake adapters; no item-key files are needed."""

from dataclasses import replace

import pytest

from factorylab.runtime.worlds import load_manifest


@pytest.fixture(scope="session")
def w1_scripted_diary(scripted_runtime_run):
    manifest = load_manifest("scripted")
    manifest = replace(manifest, immune=replace(manifest.immune, k=2, gap_threshold=.6))
    record = scripted_runtime_run(manifest, 800, 1, drip=False)
    return record.runtime(manifest), record.entries
