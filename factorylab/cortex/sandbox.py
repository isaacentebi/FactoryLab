"""Population Python runs only inside an OS jail, with bounded resources and output.

Linux uses bubblewrap namespaces and seccomp: no host tree outside the Python
runtime/system libraries and the tool directory, no sockets or child processes.
macOS sandbox-exec is development-only: a deny-default profile grants access
only to the Python prefix and the tool directory, and the kernel refuses every
RLIMIT_AS/RLIMIT_DATA value (EINVAL, confined or not), so on macOS the memory
bound is only the wall and CPU timeouts. Hosts unable to launch the jail cannot
execute population code. No source-text filtering is a boundary.
"""

from __future__ import annotations

import errno
import json
import os
import platform
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class NoJail(RuntimeError):
    """Population tools are infeasible on this host."""


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool


# The jail binary is resolved on the system path only: the caller's PATH can
# neither hide it nor substitute an impostor from a user-writable directory.
_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _jail_executable() -> str:
    name = {"linux": "bwrap", "darwin": "sandbox-exec"}.get(sys.platform)
    executable = shutil.which(name, path=_SYSTEM_PATH) if name else None
    if executable is None:
        raise NoJail("no jail on this host")
    return executable


def _linux_seccomp() -> bytes:
    """Deny process creation and sockets even for uid 0; foreign syscall ABIs fail closed."""
    # Linux UAPI audit architecture and syscall numbers. No Python package or
    # helper executable is needed to construct classic BPF for bwrap --seccomp.
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = 0xC000003E
        denied = (41, 53, 56, 57, 58, 101, 272, 308, 425, 426, 427, 435)
    elif machine in ("aarch64", "arm64"):
        arch = 0xC00000B7
        denied = (97, 117, 198, 199, 220, 268, 425, 426, 427, 435)
    else:
        raise NoJail("no jail on this host")
    # ptrace, unshare/setns and io_uring are also denied: they are unnecessary
    # for a tool and must not provide alternate routes around the socket check.
    instructions = [
        (0x20, 0, 0, 4),                 # load seccomp_data.arch
        (0x15, 1, 0, arch),              # architecture must match
        (0x06, 0, 0, 0x80000000),        # SECCOMP_RET_KILL_PROCESS
        (0x20, 0, 0, 0),                 # load seccomp_data.nr
        (0x45, 0, 1, 0x40000000),        # reject x32 ABI syscall bit
        (0x06, 0, 0, 0x80000000),
    ]
    for syscall in denied:
        instructions.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, 0x00050000 | errno.EPERM)])
    instructions.append((0x06, 0, 0, 0x7FFF0000))  # SECCOMP_RET_ALLOW
    return b"".join(struct.pack("HBBI", *instruction) for instruction in instructions)


def _command(jail: str, work: Path, prefix: Path, python: Path, seccomp_fd: int) -> list[str]:
    if sys.platform == "linux":
        command = [jail, "--unshare-all", "--die-with-parent", "--cap-drop", "ALL"]
        for path in dict.fromkeys((Path("/usr"), Path("/lib"), Path("/lib64"), prefix)):
            if path.exists():  # /lib64 is absent on some aarch64 distributions
                command.extend(["--ro-bind", str(path), str(path)])
        command.extend([
            "--tmpfs", "/tmp", "--bind", str(work), "/work", "--chdir", "/work",
            "--new-session", "--clearenv", "--setenv", "PATH", "/usr/bin",
            "--seccomp", str(seccomp_fd), "--", str(python), "-I", "-S", "-B",
            "/work/runner.py",
        ])
        return command
    # No process-fork, network, or general filesystem grant. The executable
    # grant is limited to this interpreter; dylibs must come from its prefix
    # or the OS shared cache. If the installed runtime needs more, the probe
    # fails closed instead of broadening the filesystem grant.

    # dyld's shared-cache lookup (dyld4::CacheFinder) opens the root directory
    # itself before any image loads; without that one read the interpreter is
    # aborted (SIGABRT from ignition_halt) before its first instruction. The
    # grant is the literal "/" only: no subpath, so no file under it opens.
    profile = (
        '(version 1)(deny default)'
        f'(allow process-exec (literal {json.dumps(str(python))}))'
        '(allow file-read-data (literal "/"))'
        f'(allow file-read* (subpath {json.dumps(str(prefix))})'
        f' (subpath {json.dumps(str(work))}))'
        f'(allow file-write* (subpath {json.dumps(str(work))}))'
        '(allow sysctl-read)'
    )
    return [jail, "-p", profile, str(python), "-I", "-S", "-B", str(work / "runner.py")]


def jail_installed() -> bool:
    """Report whether this host claims a jail: the platform's jail executable exists."""
    try:
        _jail_executable()
    except NoJail:
        return False
    return True


def jail_probe() -> str | None:
    """Return None when population code can run confined here, else why it cannot.

    The reason names the failing stage (no executable, an interpreter outside its
    prefix, a launch error, or the confined interpreter's exit status and streams)
    so an operator can act on it; it never contains population code.
    """
    try:
        result = run_python('print("factorylab-jail-ready")', timeout_s=2)
    except NoJail as exc:
        return str(exc)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"jail launch failed: {type(exc).__name__}: {exc}"
    if result.returncode == 0 and result.stdout.strip() == "factorylab-jail-ready":
        return None
    if result.timed_out:
        return "jailed interpreter did not answer within 2 s"
    return (
        f"jailed interpreter exited {result.returncode}"
        f" (stdout {result.stdout.strip()[:200]!r}, stderr {result.stderr.strip()[:500]!r})"
    )


def jail_available() -> bool:
    """Report usable isolation, including hosts where an installed jail cannot start."""
    return jail_probe() is None


def run_python(
    code: str,
    *,
    stdin: str = "",
    timeout_s: float = 5.0,
    cpu_s: int = 2,
    max_output_bytes: int = 64_000,
) -> SandboxResult:
    """Bound population execution by an OS jail, rlimits and a process-tree wall timeout.

    Captured streams use size-limited files, never unbounded host-memory pipes.
    Limit setup fails closed. The interpreter is the base runtime, so the repo
    virtualenv, its packages and all host credentials are outside the jail.
    """
    if cpu_s <= 0 or timeout_s <= 0 or max_output_bytes <= 0:
        raise ValueError("limits must be positive")
    jail = _jail_executable()
    prefix = Path(sys.base_prefix).resolve()
    python = Path(sys._base_executable).resolve()
    if not python.is_relative_to(prefix) or prefix == Path("/"):
        raise NoJail("no jail on this host")
    with (
        tempfile.TemporaryDirectory(prefix="factorylab-sbx-") as directory,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
        tempfile.TemporaryFile() as seccomp,
    ):
        work = Path(directory).resolve()
        (work / "main.py").write_text(code, encoding="utf-8")
        # Set NPROC after the jail has created its init/interpreter processes.
        # These hard limits cannot be raised by population code. Linux seccomp
        # enforces the process ban even where RLIMIT_NPROC exempts uid 0.
        # Darwin returns EINVAL for any RLIMIT_AS value (the shared cache alone
        # exceeds what a tool may map), so the development jail sets no memory
        # rlimit; Linux, the deployed jail, bounds the address space.
        address_space = (
            "" if sys.platform == "darwin"
            else "resource.setrlimit(resource.RLIMIT_AS, (536870912, 536870912))\n"
        )
        (work / "runner.py").write_text(
            "import resource, runpy\n"
            f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_s}, {cpu_s}))\n"
            f"{address_space}"
            "resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))\n"
            "resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))\n"
            "resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))\n"
            "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
            "runpy.run_path('main.py', run_name='__main__')\n",
            encoding="utf-8",
        )
        if sys.platform == "linux":
            seccomp.write(_linux_seccomp())
            seccomp.seek(0)
        command = _command(jail, work, prefix, python, seccomp.fileno())
        timed_out = False
        with subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
            cwd=work, env={}, start_new_session=True,
            pass_fds=(seccomp.fileno(),) if sys.platform == "linux" else (),
        ) as proc:
            try:
                proc.communicate(stdin.encode("utf-8"), timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                # bwrap's --die-with-parent tears down its PID namespace too.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
        stdout.seek(0)
        stderr.seek(0)
        return SandboxResult(
            stdout.read(max_output_bytes).decode("utf-8", errors="replace"),
            stderr.read(max_output_bytes).decode("utf-8", errors="replace"),
            -1 if timed_out else proc.returncode,
            timed_out,
        )


def main() -> int:
    """Exit 0 only when a confined interpreter runs here; print the reason otherwise.

    ``python -m factorylab.cortex.sandbox`` is the provisioning probe: run it as
    the service user under the unit's own restrictions, and fail provisioning
    on a non-zero exit instead of launching a world whose tools cannot exist.
    """
    reason = jail_probe()
    if reason is None:
        print(f"factorylab jail-check: ok ({_jail_executable()} confines {sys._base_executable})")
        return 0
    print(f"factorylab jail-check: FAILED: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
