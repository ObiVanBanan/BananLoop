"""Single-threaded host launcher for cgroup attachment and worker rlimits."""
from __future__ import annotations

import os
import re
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[3] != "--":
        return 125
    cgroup_procs, timeout_raw, memory_raw = argv[:3]
    command = argv[4:]
    if (not command or any(not item or "\x00" in item for item in command)
            or not re.fullmatch(r"[1-9][0-9]{0,6}", timeout_raw)
            or not re.fullmatch(r"[1-9][0-9]{0,12}", memory_raw)):
        return 125
    try:
        attach_to_cgroup(cgroup_procs)
        import resource
        timeout_seconds, memory_bytes = int(timeout_raw), int(memory_raw)
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds + 1, timeout_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (25_000_000, 25_000_000))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.execvpe(command[0], command, {
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        })
    except (ImportError, OSError, ValueError):
        return 125
    return 125


def attach_to_cgroup(cgroup_procs: str) -> None:
    fd = os.open(cgroup_procs, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        payload = str(os.getpid()).encode("ascii")
        if os.write(fd, payload) != len(payload):
            raise OSError("short cgroup.procs write")
    finally:
        os.close(fd)
    read_fd = os.open(cgroup_procs, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(read_fd, "r", encoding="ascii") as stream:
        if str(os.getpid()) not in stream.read().split():
            raise OSError("launcher was not attached to its worker cgroup")


if __name__ == "__main__":  # pragma: no cover - exercised as subprocess entrypoint
    raise SystemExit(main(sys.argv[1:]))
