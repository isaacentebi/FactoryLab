"""Wave 7, over a scripted world: the context observations read the ledgered bytes.

The window's counters, the return samples and the reading rows carry the very
``sections`` counts each invocation's ledger row records, so there is one
measurement of a prompt's size and the published observations are that one.
"""

from collections import defaultdict

import pytest

from factorylab.runtime.worlds import load_manifest

EVENTS = 100  # the same scripted run test_e2_entitlement shares


@pytest.fixture(scope="module")
def world(scripted_runtime_run):
    manifest = load_manifest("scripted")
    record = scripted_runtime_run(manifest, EVENTS, 1)
    return record.runtime(manifest), record.entries


def test_each_return_sample_carries_its_invocations_ledgered_sections(world):
    rt, entries = world
    ledgered = defaultdict(list)
    for item in entries:
        if item["kind"] == "invocation":
            ledgered[item["handle"]].append(item["sections"])
    rows = [r for r in rt.card_samples.returns
            if not r.get("storage") and r["prompt_bytes"] is not None]
    assert rows, "the run retained no measured return sample"
    for row in rows:
        assert {"total": row["prompt_bytes"], "you": row["you_bytes"],
                "inputs": row["inputs_bytes"]} in [
            {key: s[key] for key in ("total", "you", "inputs")}
            for s in ledgered[row["handle"]]], row["handle"]


def test_the_closed_windows_counters_are_the_sums_of_its_samples(world):
    rt, _entries = world
    assert rt.card_samples.windows, "the run closed no window"
    for record in rt.card_samples.windows:
        rows = [r for r in rt.card_samples.returns if r["window"] == record["index"]
                and not r.get("storage") and r["prompt_bytes"] is not None]
        assert (record["invocations"] == record["prompts"] == record["read_measured"]
                == len(rows))
        for key in ("prompt_bytes", "you_bytes", "inputs_bytes"):
            assert record[key] == sum(r[key] for r in rows), key
        readings = [r for r in rt.card_samples.readings if r["window"] == record["index"]]
        assert record["downstream_read_bytes"] == sum(r["read_bytes"] for r in readings)


def test_every_reading_is_filed_under_the_author_of_the_return_read(world):
    rt, _entries = world
    readings = [r for r in rt.card_samples.readings]
    readings_total = sum(r["read_bytes"] for r in readings) + rt.window.downstream_read_bytes
    assert readings_total > 0, "no judge read a return in the run"
    for row in readings:
        assert row["assembly"] == rt.handle_to_assembly[row["handle"]]
        assert row["role"] == rt._decision_role(row["handle"])
        assert set(row) == {"handle", "assembly", "role", "window", "read_bytes", "reading"}
