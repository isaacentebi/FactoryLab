import json
import shutil
import struct
import sys
from pathlib import Path

import pytest

from factorylab.cortex import sandbox
from factorylab.cortex.registration import Rejected, parse_proposals
from factorylab.cortex.tools import PopulationTool, ToolRunner


def require_jail():
    """Skip only where no jail is installed; a jail that is installed and cannot start fails."""
    if not sandbox.jail_installed():
        pytest.skip("no jail executable on this host; refusal is tested separately")
    reason = sandbox.jail_probe()
    if reason is not None:
        pytest.fail(f"this host claims a jail that cannot start: {reason}")


def proposal():
    return {"kind": "tool", "id": "test-tool", "description": "Synthetic probe",
            "args_schema": {"type": "object", "properties": {}}, "code": 'print("{}")',
            "timeout_s": 1}


def parse(item):
    return parse_proposals({"register": [item]}, event_kinds=frozenset(),
                           known_models=frozenset(), known_assemblies=frozenset())


@pytest.mark.parametrize("failure", ["missing", "unusable"])
def test_no_jail_refuses_registration_and_execution(monkeypatch, failure):
    if failure == "missing":
        monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    else:
        def fail(*args, **kwargs):
            raise OSError("jail unavailable")
        monkeypatch.setattr(sandbox.subprocess, "Popen", fail)
    assert parse(proposal()) == ([], [Rejected(0, "no jail on this host")])
    tool = PopulationTool("probe", "probe", {"type": "object", "properties": {}},
                          'print("{}")', 1, "synthetic")
    assert ToolRunner().run(tool, {}) == {"error": "no jail on this host"}


def test_no_jail_is_visible_in_the_world_block(monkeypatch):
    from tests.conftest import make_runtime

    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    rt = make_runtime()
    assert rt._world_block()["population_tools"] == {
        "available": False, "reason": "no jail on this host",
    }


@pytest.mark.parametrize("attack", ["file", "socket", "process"])
def test_tool_cannot_read_host_file_open_socket_or_spawn_process(tmp_path, attack):
    require_jail()
    outside = tmp_path / "outside-synthetic.txt"
    outside.write_text("synthetic fixture")
    code = {
        "file": f"open({str(outside)!r}).read()",
        # Linux seccomp refuses socket(); macOS seatbelt refuses connect(). Either is
        # confinement; both raise PermissionError before any byte leaves the jail.
        "socket": "__import__('so'+'cket').create_connection(('127.0.0.1', 9), timeout=1)",
        "process": "__import__('sub'+'process').run(['/usr/bin/true'], check=True)",
    }[attack]
    # A healthy computation proves that failure is caused by confinement.
    assert sandbox.run_python('print("ok")').stdout.strip() == "ok"
    result = sandbox.run_python(code)
    assert result.returncode != 0 and not result.timed_out
    assert "synthetic fixture" not in result.stdout


def test_tool_resource_limits_are_hard_and_finite():
    require_jail()
    result = sandbox.run_python(
        "import json, resource; "
        "print(json.dumps({name: resource.getrlimit(getattr(resource, name)) "
        "for name in ['RLIMIT_AS', 'RLIMIT_NPROC', 'RLIMIT_FSIZE', "
        "'RLIMIT_NOFILE', 'RLIMIT_CPU']}))"
    )
    assert result.returncode == 0
    limits = json.loads(result.stdout)
    if sys.platform == "darwin":
        # Darwin refuses every RLIMIT_AS value; the development jail documents that.
        assert limits.pop("RLIMIT_AS") == [2**63 - 1] * 2
    assert all(0 <= soft == hard < 2**40 for soft, hard in limits.values())
    assert limits["RLIMIT_NPROC"] == [0, 0]


def test_linux_command_mounts_base_python_and_work_only(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    prefix = Path(sys.base_prefix).resolve()
    python = Path(sys._base_executable).resolve()
    command = sandbox._command("/usr/bin/bwrap", tmp_path, prefix, python, 99)
    assert command[:3] == ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent"]
    assert "--new-session" in command and "--seccomp" in command
    mounts = [command[i + 1:i + 3] for i, arg in enumerate(command) if arg == "--ro-bind"]
    assert all(source == target for source, target in mounts)
    assert {source for source, _ in mounts} <= {"/usr", "/lib", "/lib64", str(prefix)}
    assert str(Path.cwd()) not in command and "/srv" not in command
    assert command[command.index("--bind") + 1:command.index("--bind") + 3] == [
        str(tmp_path), "/work",
    ]
    assert command[command.index("--setenv") + 1:command.index("--setenv") + 3] == [
        "PATH", "/usr/bin",
    ]


@pytest.mark.parametrize("machine,arch,socket,fork", [
    ("x86_64", 0xC000003E, 41, 56), ("aarch64", 0xC00000B7, 198, 220),
])
def test_seccomp_denies_network_and_process_syscalls_on_supported_linux_abis(
    monkeypatch, machine, arch, socket, fork,
):
    monkeypatch.setattr(sandbox.platform, "machine", lambda: machine)
    instructions = list(struct.iter_unpack("HBBI", sandbox._linux_seccomp()))

    def evaluate(number, architecture=arch):
        accumulator, pc = 0, 0
        while True:
            op, yes, no, value = instructions[pc]
            pc += 1
            if op == 0x20:
                accumulator = architecture if value == 4 else number
            elif op == 0x15:
                pc += yes if accumulator == value else no
            elif op == 0x45:
                pc += yes if accumulator & value else no
            elif op == 0x06:
                return value
            else:
                pytest.fail("unexpected BPF instruction")

    for number in (socket, fork, 435, 425, 426, 427):
        assert evaluate(number) == 0x50001  # EPERM
    assert evaluate(0) == 0x7FFF0000  # a permitted syscall
    assert evaluate(0, architecture=0) == 0x80000000
    assert evaluate(socket | 0x40000000) == 0x80000000


def test_macos_profile_does_not_grant_general_filesystem_network_or_fork(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("macOS profile")
    prefix = Path(sys.base_prefix).resolve()
    command = sandbox._command("/usr/bin/sandbox-exec", tmp_path, prefix,
                               Path(sys._base_executable).resolve(), 99)
    profile = command[2]
    assert "(deny default)" in profile
    assert "(allow network" not in profile and "process-fork" not in profile
    assert profile.count("subpath") == 3
    assert str(prefix) in profile and str(tmp_path) in profile
