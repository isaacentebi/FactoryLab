#!/usr/bin/env python3
"""Judge the funded world's health from evidence, fail closed, and witness dormancy.

Runs hourly as ``factorylab-health.service``:

    witness_liveness.py --wake /srv/factorylab/www/wake.json \\
        --state /srv/factorylab/runs/funded.liveness \\
        --health /srv/factorylab/runs/funded.health

**Health.** The verdict is one of ``healthy``, ``dormant``, ``terminated``,
``unhealthy`` or ``unknown``, and only the first two are success. "Alive" is never
inferred from the absence of a problem: a missing, unreadable or stale wake, a field
the wake could not compute, or a clock that runs backwards is ``unknown``. A world the
wake calls alive is ``healthy`` only when every one of these holds, from data the
wake already publishes:

* durable progress: the ledger's last event (``last_event_time_ns``) is recent at the
  moment the wake was published (the file's mtime), and that publication is recent;
* successful work: while not dormant, at least one invocation the provider answered
  ``ok`` in the recent ``returns`` rows (failed-only evidence is ``unhealthy``);
* venue reads: when the wake reads the venue account (``venue``), the read succeeded;
* settlements: no open decision (``commitments``) is overdue by more than its own
  span or the grace, whichever is longer, past its deadline;
* disk: the data filesystem is below the alert threshold.

Recorded, intentional dormancy (``liveness.status == "dormant"``, ledgered by the
runtime, C2) waives only the successful-work check: a dormant world routes no paid
cognition but still ticks, settles and reads its venue.

Exit status: 0 healthy or dormant; 10 unhealthy; 11 unknown; 12 terminated. On 10 or
11 the verdict is sent through ``deploy/alert.sh --event <status> <first reason>``
every hour it persists; the unit treats 10-12 as handled, so any other failure of
this script (a crash, exit 1) fails the unit and its ``OnFailure=`` alerts instead.
The verdict is written to the ``--health`` record (``<status> [reasons...]``), which
the six-hourly heartbeat reports. A ``healthy`` verdict, and only that, also pings the
optional dead-man's switch (``alert.sh --ping``, ``FACTORY_HEARTBEAT_URL``), whose
service alerts the owner when the hourly pings stop: the one alarm that still fires
when this host is gone.

**Dormancy witness.** Compares ``liveness.status`` with the status last witnessed
(the ``--state`` file). A change into ``dormant`` runs ``witness.sh dormant entered``;
from ``dormant`` back to ``alive``, ``witness.sh dormant exited``. ``launch`` and
``failed_resume`` belong to ``start.sh``; ``kill`` is written by the runtime itself.
The state file is rewritten only after the line was appended, so a failed append is
retried on the next run. Nothing here reads the ledger or a key.

Chapter II §III ("an online evaluation runs continuously in production") and §IV.c
(neither the factory nor its control apparatus may be slower than its environment).
The verdict goes to the owner only; nothing here enters the world or a seat's request.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

STATUSES = ("alive", "dormant", "terminated")
HERE = Path(__file__).resolve().parent
WITNESS_SH = HERE / "witness.sh"
ALERT_SH = HERE / "alert.sh"
EXIT = {"healthy": 0, "dormant": 0, "unhealthy": 10, "unknown": 11, "terminated": 12}
#: Every reason a verdict can carry, in the order they are reported (the first is the
#: alert's reason code). Each matches alert.sh's ``^[a-z_]{1,40}$``.
REASONS = (
    "disk_high", "disk_unknown",
    "wake_missing", "wake_unavailable", "wake_stale", "clock_skew", "ledger_unknown",
    "ledger_stale", "settlement_overdue", "commitments_unknown",
    "provider_failing", "no_provider_success", "returns_unknown",
    "venue_unreadable",
)
UNKNOWN_REASONS = frozenset({
    "disk_unknown", "wake_missing", "wake_unavailable", "wake_stale", "clock_skew",
    "ledger_unknown", "commitments_unknown", "returns_unknown",
})
S = 1_000_000_000


@dataclass(frozen=True)
class Limits:
    """How old each piece of evidence may be. The wake publishes hourly, ticks are
    seconds apart, and the restart backoff caps at 15 minutes."""

    max_wake_age_s: int = 2 * 3600
    max_ledger_age_s: int = 20 * 60
    provider_window_s: int = 2 * 3600
    settlement_grace_s: int = 3600
    clock_skew_s: int = 5 * 60
    disk_alert_percent: float = 80.0


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Verdict:
    """``status`` is one of EXIT's keys; ``reasons`` are drawn from REASONS, in order."""

    status: str
    reasons: tuple[str, ...] = ()


def read_wake(wake: Path) -> tuple[dict | None, int | None]:
    """The published wake and its mtime in ns, or (None, None) when it cannot be read."""
    try:
        mtime = wake.stat().st_mtime_ns
        data = json.loads(wake.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    return (data, mtime) if isinstance(data, dict) else (None, None)


def read_status(wake: Path) -> str | None:
    """The wake's published liveness status, or None when it cannot be read."""
    return _status(read_wake(wake)[0])


def _status(data: dict | None) -> str | None:
    liveness = data.get("liveness") if isinstance(data, dict) else None
    status = liveness.get("status") if isinstance(liveness, dict) else None
    return status if status in STATUSES else None


def disk_percent(path: Path) -> float | None:
    """Used share of the filesystem holding ``path``, as ``df`` reports it, or None."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    denominator = usage.used + usage.free
    return 100.0 * usage.used / denominator if denominator else None


def _int(value) -> int | None:
    return value if type(value) is int else None


def assess(wake: dict | None, *, wake_mtime_ns: int | None, now_ns: int,
           disk: float | None, limits: Limits = DEFAULT_LIMITS) -> Verdict:
    """A verdict that is ``healthy`` or ``dormant`` only on positive, fresh evidence.

    Guarantees: missing, unreadable, stale or uncomputable evidence never yields
    ``healthy`` or ``dormant``; a published ``terminated`` is final whatever its age;
    failed-only work while alive is ``unhealthy``.
    """
    found: set[str] = set()
    if disk is None:
        found.add("disk_unknown")
    elif disk >= limits.disk_alert_percent:
        found.add("disk_high")
    status = _status(wake)
    if status == "terminated":
        return Verdict("terminated")
    if wake is None:
        found.add("wake_missing")
    elif status is None:
        found.add("wake_unavailable")
    else:
        _evidence(wake, status, wake_mtime_ns, now_ns, limits, found)
    reasons = tuple(reason for reason in REASONS if reason in found)
    if any(reason not in UNKNOWN_REASONS for reason in reasons):
        return Verdict("unhealthy", reasons)
    if reasons:
        return Verdict("unknown", reasons)
    return Verdict("dormant" if status == "dormant" else "healthy")


def _evidence(wake: dict, status: str, published: int | None, now: int, limits: Limits,
              found: set[str]) -> None:
    skew = limits.clock_skew_s * S
    # The ledger's age is read at the check, not at the publication (§IV.c): an event
    # 19 minutes old when the hourly wake published is 49 minutes old half an hour
    # later. A stale publication says nothing of the ledger now, so it is aged at the
    # publication, as it always was.
    aged_at = now
    if published is None or now - published > limits.max_wake_age_s * S:
        found.add("wake_stale")
        aged_at = published
    elif published - now > skew:
        found.add("clock_skew")
        published = None  # a publication from the future measures no age
    last = _int(wake.get("last_event_time_ns"))
    if published is None:
        pass  # no trustworthy publication time: the ledger's age is not measurable
    elif last is None:
        found.add("ledger_unknown")
    elif last - published > skew:
        found.add("clock_skew")
    elif aged_at - last > limits.max_ledger_age_s * S:
        found.add("ledger_stale")
    _settlements(wake.get("commitments"), limits, found)
    if status == "alive":
        _provider(wake, last, limits, found)
    venue = wake.get("venue")
    if venue is not None and not (isinstance(venue, dict)
                                  and _int(venue.get("equity_micro")) is not None):
        found.add("venue_unreadable")


def _settlements(commitments, limits: Limits, found: set[str]) -> None:
    handles = commitments.get("handles") if isinstance(commitments, dict) else None
    rows = handles.get("rows") if isinstance(handles, dict) else None
    as_of = _int(commitments.get("as_of_ns")) if isinstance(commitments, dict) else None
    if not isinstance(rows, list) or as_of is None:
        found.add("commitments_unknown")
        return
    grace = limits.settlement_grace_s * S
    for row in rows:
        if not isinstance(row, dict):
            found.add("commitments_unknown")
            continue
        age, deadline = _int(row.get("age_ns")), _int(row.get("deadline_ns"))
        if age is None or deadline is None:
            continue  # a decision without a wall deadline: its cutoff counts ticks
        # The runtime counts cutoffs in ticks; the wall deadline is their projection,
        # so a decision may run past it by its own span before it is overdue.
        span = max(0, deadline - (as_of - age))
        if as_of - deadline > max(grace, span):
            found.add("settlement_overdue")


def _provider(wake: dict, last: int | None, limits: Limits, found: set[str]) -> None:
    returns = wake.get("returns")
    rows = returns.get("rows") if isinstance(returns, dict) else None
    if not isinstance(rows, list) or last is None:
        found.add("returns_unknown")
        return
    window = limits.provider_window_s * S
    recent = [row for row in rows if isinstance(row, dict)
              and _int(row.get("ts_ns")) is not None and last - row["ts_ns"] <= window]
    if any(row.get("status") == "ok" for row in recent):
        return
    if recent:
        found.add("provider_failing")
        return
    liveness = wake.get("liveness") or {}
    launched = _int(liveness.get("launched_ns"))
    # A world younger than the window may not have called anything yet; one that has
    # existed for a whole window and answered nothing has done no work.
    if launched is None or last - launched > window:
        found.add("no_provider_success")


def read_state(state: Path) -> str:
    """The status last witnessed; a world with no record is taken to have been alive."""
    try:
        recorded = state.read_text(encoding="utf-8").strip()
    except OSError:
        return "alive"
    return recorded if recorded in STATUSES else "alive"


def write_state(state: Path, status: str) -> None:
    """Replace the record atomically, mode 0600, beside the ledger it describes."""
    state.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{state.name}-", dir=state.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(status + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, state)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def transition(previous: str, current: str) -> str | None:
    """The witness reason for a change, or None when nothing is to be witnessed."""
    if previous == current:
        return None
    if current == "dormant":
        return "entered"
    if previous == "dormant" and current == "alive":
        return "exited"
    return None


def witness(script: Path, reason: str) -> bool:
    """Run ``witness.sh dormant <reason>``; True when it appended its line."""
    completed = subprocess.run(["bash", str(script), "dormant", reason],
                               capture_output=True, timeout=120, check=False)
    return completed.returncode == 0


def alert(script: Path, verdict: Verdict) -> bool:
    """Send ``alert.sh --event <status> <first reason>``; True when it was delivered."""
    reason = verdict.reasons[0] if verdict.reasons else "none"
    completed = subprocess.run(["bash", str(script), "--event", verdict.status, reason],
                               capture_output=True, timeout=150, check=False)
    return completed.returncode == 0


def ping(script: Path) -> bool:
    """Send ``alert.sh --ping`` (the optional dead-man's switch); True when delivered or
    when no ``FACTORY_HEARTBEAT_URL`` is configured."""
    completed = subprocess.run(["bash", str(script), "--ping"],
                               capture_output=True, timeout=150, check=False)
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wake", required=True, type=Path, help="the published wake.json")
    parser.add_argument("--state", required=True, type=Path,
                        help="file remembering the last witnessed status (under runs/)")
    parser.add_argument("--health", type=Path,
                        help="where the verdict is recorded for the heartbeat (under runs/)")
    parser.add_argument("--disk", type=Path,
                        help="a path on the data filesystem (default: the state file's directory)")
    parser.add_argument("--witness", type=Path, default=WITNESS_SH,
                        help="witness script (default: deploy/witness.sh beside this file)")
    parser.add_argument("--alert", type=Path, default=ALERT_SH,
                        help="alert script (default: deploy/alert.sh beside this file)")
    args = parser.parse_args(argv)
    data, mtime = read_wake(args.wake)
    verdict = assess(data, wake_mtime_ns=mtime, now_ns=time.time_ns(),
                     disk=disk_percent(args.disk or args.state.parent))
    if args.health is not None:
        write_state(args.health, " ".join((verdict.status, *verdict.reasons)))
    witnessed = True
    current = _status(data)
    if current is not None:
        previous = read_state(args.state)
        reason = transition(previous, current)
        witnessed = reason is None or witness(args.witness, reason)
        if witnessed and previous != current:
            write_state(args.state, current)
    if verdict.status in ("unhealthy", "unknown"):
        alert(args.alert, verdict)
    elif verdict.status == "healthy":
        # Only a healthy verdict pings: an unhealthy factory, a dead host and a dead
        # check all stop the pings alike, and the outside service raises the alarm.
        ping(args.alert)
    if not witnessed and EXIT[verdict.status] == 0:
        return 1  # the record stays behind so the next run tries again
    return EXIT[verdict.status]


if __name__ == "__main__":
    sys.exit(main())
