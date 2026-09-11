"""Bounded subprocess execution for population-written code.

What this guarantees in phase 1: a wall-clock timeout, a CPU-time limit, an
empty environment, Python isolated mode (no site, no user site, no
PYTHONPATH), a private working directory, and captured output. What it does
*not* guarantee: network isolation or memory caps on macOS. The world manifest
must not grant this sandbox write rights outside its directory until the
runtime wraps it in an OS-level container; that is a phase 2 item and the
kernel treats sandbox output as untrusted regardless.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    cpu_limited: bool


def run_python(
    code: str,
    *,
    stdin: str = "",
    timeout_s: float = 5.0,
    cpu_s: int = 2,
    max_output_bytes: int = 64_000,
) -> SandboxResult:
    """Run ``code`` in a fresh isolated interpreter and return what it produced.

    Output beyond ``max_output_bytes`` is truncated. A wall-clock timeout kills
    the process and is reported; exceeding ``cpu_s`` is reported when the OS
    signals it.
    """
    if cpu_s <= 0 or timeout_s <= 0:
        raise ValueError("limits must be positive")

    def _limits() -> None:  # pragma: no cover - runs in the child
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="factorylab-sbx-") as d:
        path = os.path.join(d, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-B", path],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=d,
                env={},
                preexec_fn=_limits,
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = (exc.stderr or b"") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            if isinstance(err, bytes):
                err = err.decode(errors="replace")
            return SandboxResult(out[:max_output_bytes], err[:max_output_bytes], -1, True, False)
    cpu_limited = proc.returncode < 0 and -proc.returncode in (9, 24)  # SIGKILL/SIGXCPU
    return SandboxResult(
        proc.stdout[:max_output_bytes],
        proc.stderr[:max_output_bytes],
        proc.returncode,
        False,
        cpu_limited,
    )
