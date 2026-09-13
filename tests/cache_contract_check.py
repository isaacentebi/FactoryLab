"""Explicitly invoked cache checks; excluded from default test-file discovery."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import conftest as subject


def builder(monkeypatch, directory, *, fail=False):
    def run(manifest, *, events, seed, ledger_path):
        with (directory / "computations").open("a") as stream:
            stream.write("run\n")
        Path(ledger_path).write_text("original ledger")
        Path(ledger_path + ".key").write_text("synthetic sidecar")
        if fail:
            raise RuntimeError("interrupted writer")
        return {"events": events, "seed": seed, "nested": {1: [2, 3]}}

    monkeypatch.setattr(subject, "run_world", run)
    monkeypatch.setattr(subject, "jail_available", lambda: True)


@pytest.mark.parametrize("consumer", range(4))
def test_cross_worker_single_writer(tmp_path_factory, request, monkeypatch, tmp_path, consumer):
    base = tmp_path_factory.getbasetemp()
    directory = (base.parent if hasattr(request.config, "workerinput") else base) / "contract"
    directory.mkdir(exist_ok=True)
    builder(monkeypatch, directory)
    manifest = subject.load_manifest("scripted")
    result = subject._cached_scripted_run(directory, manifest, 17, 2, mode="run_world")
    assert (directory / "computations").read_text().splitlines() == ["run"]
    assert result.summary["nested"] == {1: [2, 3]}
    changed = result.summary
    changed["nested"][1].append(4)
    assert result.summary["nested"] == {1: [2, 3]}
    with pytest.raises(FrozenInstanceError):
        result.ledger_path = tmp_path / "other"
    copy = result.copy_to(tmp_path / "copy")
    copy.write_text("damaged")
    Path(str(copy) + ".key").unlink()
    assert result.ledger_path.read_text() == "original ledger"
    assert Path(str(result.ledger_path) + ".key").exists()


def test_keys_include_all_inputs_and_normalize_manifests(tmp_path, monkeypatch):
    builder(monkeypatch, tmp_path)
    manifest = subject.load_manifest("scripted")
    run = subject.scripted_run.__wrapped__(tmp_path)
    original = run("scripted", 17, 2)
    assert run(manifest, 17, 2).ledger_path == original.ledger_path
    changed = subject._manifest_with_changes(manifest, {"immune": {"k": 2}})
    assert manifest.immune.k != changed.immune.k
    overridden = run("scripted", 17, 2, manifest_changes={"immune": {"k": 2}})
    assert run(changed, 17, 2).ledger_path == overridden.ledger_path
    assert overridden.ledger_path != original.ledger_path
    assert run(manifest, 18, 2).ledger_path != original.ledger_path
    assert run(manifest, 17, 3).ledger_path != original.ledger_path
    monkeypatch.setattr(subject, "jail_available", lambda: False)
    assert run(manifest, 17, 2).ledger_path != original.ledger_path
    assert len((tmp_path / "computations").read_text().splitlines()) == 5


def test_failed_writer_is_never_published_and_can_retry(tmp_path, monkeypatch):
    builder(monkeypatch, tmp_path, fail=True)
    manifest = subject.load_manifest("scripted")
    with pytest.raises(RuntimeError, match="interrupted writer"):
        subject._cached_scripted_run(tmp_path, manifest, 17, 2, mode="run_world")
    assert not list(tmp_path.glob("*/result.pickle"))
    builder(monkeypatch, tmp_path)
    result = subject._cached_scripted_run(tmp_path, manifest, 17, 2, mode="run_world")
    assert result.summary["events"] == 17
    assert len((tmp_path / "computations").read_text().splitlines()) == 2


def test_marker_partition_includes_transitive_fixtures(tmp_path):
    direct = tmp_path / "direct.py"
    direct.write_text("from factorylab.runtime.loop import run_world as run\n")
    unit = tmp_path / "unit.py"
    unit.write_text("pass\n")
    marked = []
    items = [
        SimpleNamespace(path=direct, fixturenames=[], add_marker=marked.append,
                        get_closest_marker=lambda name: None),
        SimpleNamespace(path=unit, fixturenames=["wake", "scripted_run"],
                        add_marker=marked.append, get_closest_marker=lambda name: None),
        SimpleNamespace(path=unit, fixturenames=[], add_marker=marked.append,
                        get_closest_marker=lambda name: None),
    ]
    subject.pytest_collection_modifyitems(items)
    assert [marker.name for marker in marked] == ["world", "world", "fast"]


def test_runtime_capture_preserves_live_evidence(tmp_path):
    from factorylab.world.scripted import ScriptedProvider, _inputs_from_prompt

    requests = []

    class RecordingProvider(ScriptedProvider):
        def complete(self, request):
            text = "\n".join(str(m.get("content", "")) for m in request.messages)
            requests.append(_inputs_from_prompt(text))
            return super().complete(request)

    manifest = subject.load_manifest("scripted")
    reference = subject.Runtime(manifest, events=3, seed=3, initial_balance_micro=None,
                                ledger_path=None, drip=True, router_gamma=.1,
                                provider=RecordingProvider())
    expected = reference.run()
    result = subject._cached_scripted_run(tmp_path, manifest, 3, 3, mode="recorded_runtime")
    assert result.summary == expected
    assert result.requests == requests
    assert result.ledger_path is None
    assert result.runtime(manifest)._world_block() == reference._world_block()
    assert result.runtime(manifest).stats == reference.stats
    assert result.entries and all(item["kind"] != "snapshot" for item in result.entries)
    with pytest.raises(ValueError, match="in-memory ledger"):
        result.copy_to(tmp_path / "copy")


def test_public_factory_preserves_real_ledger_and_summary(tmp_path, scripted_run):
    import json

    from factorylab.kernel.ledger import Ledger

    # A tool-free manifest is valid even on hosts where the OS jail cannot start.
    changes = {"tools": {"population_tool_micro_per_call": 0}}
    manifest = subject._manifest_with_changes(subject.load_manifest("scripted"), changes)
    result = scripted_run("scripted", 3, 4, manifest_changes=changes)
    assert result.summary == subject.run_world(manifest, events=3, seed=4)
    assert scripted_run(manifest, 3, 4).ledger_path == result.ledger_path
    original = result.ledger_path.read_bytes()
    copy = result.copy_to(tmp_path / "private-world")
    ledger = Ledger.reopen(copy, manifest=json.loads(manifest.canonical_json()))
    ledger.append({"kind": "private-copy-only"})
    assert result.ledger_path.read_bytes() == original
    assert copy.read_bytes().startswith(original) and copy.read_bytes() != original
    assert Ledger.reopen(result.ledger_path, manifest=json.loads(manifest.canonical_json()),
                         read_only=True).verify()
