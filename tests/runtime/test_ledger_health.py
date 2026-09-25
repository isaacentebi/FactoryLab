"""An on-disk edit made while a world runs is refused at the next resume.

The running world's health check walks only the items appended since its last
walk (``Ledger.healthy``), so a same-size edit to a line it has already walked
does not stop the run. What it cannot do is survive a restart: open and resume
authenticate every byte of the diary before the world continues.
"""

from __future__ import annotations

import pytest

from factorylab.kernel.ledger import LedgerIntegrityError
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import ResumeError, resume_world
from factorylab.runtime.worlds import load_manifest


class ProcessDeath(BaseException):
    """Stands in for a killed process: nothing below it gets to clean up."""


def flip_a_byte_in_line(path, index):
    lines = path.read_bytes().splitlines(keepends=True)
    raw = bytearray(lines[index])
    pos = raw.index(b'"item":"') + len(b'"item":"') + 20
    raw[pos] = ord("A") if raw[pos] != ord("A") else ord("B")
    lines[index] = bytes(raw)
    path.write_bytes(b"".join(lines))


def test_an_edit_mid_run_is_caught_at_the_next_resume(tmp_path):
    manifest = load_manifest("scripted")
    path = tmp_path / "world.jsonl"
    rt = Runtime(manifest, events=200, seed=1, initial_balance_micro=None,
                 ledger_path=str(path), router_gamma=0.1)
    original = rt._process_event
    seen = {"edited_at": None, "count_at_edit": None}
    cadence = rt.ledger.ledger._Ledger__full_every

    def process(event):
        result = original(event)
        ledger = rt.ledger.ledger
        if seen["edited_at"] is None and ledger._Ledger__verified_count >= cadence:
            # A periodic walk has covered line 2 of the file (its second item).
            flip_a_byte_in_line(path, 2)
            seen["edited_at"] = rt.ledger.healthy() and len(path.read_bytes())
            seen["count_at_edit"] = rt.ledger.ledger._Ledger__count
        # Die only once a periodic walk has run past the edit.
        if (seen["count_at_edit"] is not None
                and rt.ledger.ledger._Ledger__count // cadence
                > seen["count_at_edit"] // cadence):
            raise ProcessDeath
        return result

    rt._process_event = process
    with pytest.raises(ProcessDeath):
        rt.run()
    # The run did not notice, through at least one periodic walk after the edit: the
    # walk reads what was appended since the last one, not the lines before it.
    assert seen["edited_at"] and seen["count_at_edit"] > cadence
    assert len(path.read_bytes()) > seen["edited_at"]
    before = path.read_bytes()
    with pytest.raises((ResumeError, LedgerIntegrityError)):
        resume_world(manifest, str(path))
    assert path.read_bytes() == before  # refused before writing anything
