"""The one closed vocabulary a failing command is allowed to tell an operator.

A launched world runs unattended and logs nothing: the unit discards both
streams, because an exception message can carry a key, an address or a provider
response body. Silence is the right instinct and the wrong implementation. So
every command that fails prints exactly one of these codes and nothing
interpolated, the supervisor's webhook carries the same code, and an operator
at three in the morning can act on it without reading the interior of a living
world.

Adding a code is a deliberate act: it widens what the outside can learn.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path


class Reason(StrEnum):
    """Every refusal an operator may be told about, and no other text."""

    # Evidence and recovery.
    LEDGER_INTEGRITY = "ledger_integrity"
    LEDGER_BUSY = "ledger_busy"
    INVALID_SNAPSHOT = "invalid_snapshot"
    REPLAY_DIVERGED = "replay_diverged"
    NO_LAUNCH = "no_launch"
    TERMINATED = "terminated"
    EVIDENCE_UNREADABLE = "evidence_unreadable"  # A read-back file is absent, malformed or sealed.

    # The committed first move.
    MANIFEST_UNAVAILABLE = "manifest_unavailable"
    MANIFEST_MISMATCH = "manifest_mismatch"
    RELEASE_MISMATCH = "release_mismatch"  # The executing release differs from the launched one.
    IDENTITY_KILLED = "identity_killed"  # The witness records this identity's kill: it stays dead.
    WITNESS_UNAVAILABLE = "witness_unavailable"  # A receiver is configured and gave no verdict.
    WITNESS_REQUIRED = "witness_required"  # This world launched under a receiver; none is set.
    WITNESS_MISMATCH = "witness_mismatch"  # The receiver set is not the one it launched under.
    ARTIFACT_MISSING = "artifact_missing"  # The archive index names bytes that are not there.
    ARTIFACT_PRIVATE = "artifact_private"  # The archive holds it; this reader is not scoped to it.
    FACILITATOR_MISMATCH = "facilitator_mismatch"  # The x402 facilitator is not the launched one.
    TICK_OVERRIDE_REFUSED = "tick_override_refused"
    NO_LIVE_VENUE = "no_live_venue"

    # Credentials and everything outside the factory.
    CREDENTIAL_MISSING = "credential_missing"  # The environment has no such key: supply it.
    CREDENTIAL_UNSAFE = "credential_unsafe"  # A key file's mode or owner is wrong: fix it.
    VENUE_UNREACHABLE = "venue_unreachable"
    VENUE_ACCOUNT_MISMATCH = "venue_account_mismatch"
    ADAPTER_MISMATCH = "adapter_mismatch"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    EMPTY_COMPLETION = "empty_completion"
    RESERVE_UNAVAILABLE = "reserve_unavailable"
    MARKET_UNAVAILABLE = "market_unavailable"
    TREASURY_UNAVAILABLE = "treasury_unavailable"

    # The host.
    JAIL_UNAVAILABLE = "jail_unavailable"
    WAKE_UNAVAILABLE = "wake_unavailable"

    # Refusals that protect a world or a key from the operator.
    WORLD_EXISTS = "world_exists"
    RESERVE_KEY_EXISTS = "reserve_key_exists"
    TOPUP_AMOUNT_REFUSED = "topup_amount_refused"
    QUOTE_ABOVE_CAP = "quote_above_cap"
    ARGUMENTS_INCOMPLETE = "arguments_incomplete"


class CredentialMissing(RuntimeError):
    """A credential a command needs is absent from the environment.

    A ``RuntimeError`` so that every handler that already catches one still
    does; a distinct class so the operator is told ``credential_missing``
    rather than a generic unavailability. The variable's name stays inside the
    process: the code alone says which action to take.
    """


#: Constant advice for the two paths where a payment may already have settled.
PAYMENT_MAY_HAVE_SETTLED = (
    "A submitted payment may have settled. Check balances and references before retrying."
)


def record(reason: Reason) -> None:
    """Write the code where a supervisor can read it, mode 0600, or do nothing.

    ``RuntimeDirectory`` is the unit's own tmpfs. Outside it — a developer's
    shell, a test — there is nowhere to write and nothing is written.
    """
    directory = os.environ.get("RUNTIME_DIRECTORY")
    if not directory:
        return
    path = Path(directory.split(":")[0]) / "reason"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(reason.value + "\n")
    except OSError:
        pass  # A missing runtime directory must not turn a refusal into a crash.
