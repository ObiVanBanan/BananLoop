"""Inert Linux-container probe; it neither imports nor runs candidate code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys


def _mount_options(mountpoint: str) -> list[str]:
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        left, _separator, _right = line.partition(" - ")
        fields = left.split()
        if len(fields) > 5 and fields[4].replace("\\040", " ") == mountpoint:
            return fields[5].split(",")
    return []


def _mount_targets() -> list[str]:
    targets = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.partition(" - ")[0].split()
        if len(fields) > 4:
            targets.append(fields[4].replace("\\040", " "))
    return targets


def _status() -> dict[str, str]:
    result = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith(("CapEff:", "NoNewPrivs:", "Seccomp:")):
            key, value = line.split(":", 1)
            result[key] = value.strip()
    return result


def _limit_file(root: Path, filename: str) -> Path:
    matches = sorted(root.rglob(filename))
    if not matches:
        raise RuntimeError(f"cgroup resource limit is unreadable: {filename}")
    return matches[0]


def _resource_limits() -> tuple[float, int, int]:
    root = Path("/sys/fs/cgroup")
    cpu_max = root / "cpu.max"
    if cpu_max.is_file():
        quota, period = cpu_max.read_text(encoding="ascii").split()
        if quota == "max":
            raise RuntimeError("CPU quota is absent")
        cpus = int(quota) / int(period)
    else:
        quota_path = _limit_file(root, "cpu.cfs_quota_us")
        period_path = _limit_file(root, "cpu.cfs_period_us")
        quota = int(quota_path.read_text(encoding="ascii").strip())
        period = int(period_path.read_text(encoding="ascii").strip())
        if quota <= 0:
            raise RuntimeError("CPU quota is absent")
        cpus = quota / period

    memory_v2 = root / "memory.max"
    memory_path = memory_v2 if memory_v2.is_file() else _limit_file(root, "memory.limit_in_bytes")
    memory = memory_path.read_text(encoding="ascii").strip()
    if memory == "max":
        raise RuntimeError("memory limit is absent")

    pids_v2 = root / "pids.max"
    pids_path = pids_v2 if pids_v2.is_file() else _limit_file(root, "pids.max")
    pids = pids_path.read_text(encoding="ascii").strip()
    if pids == "max":
        raise RuntimeError("PID limit is absent")
    return cpus, int(memory), int(pids)


def probe() -> dict[str, object]:
    if os.name != "posix" or not Path("/proc/self/status").is_file():
        raise RuntimeError("probe requires a Linux container with procfs")
    status = _status()
    if os.geteuid() == 0:
        raise RuntimeError("candidate process is running as root")
    if status.get("CapEff", "").lstrip("0"):
        raise RuntimeError("effective capabilities are not empty")
    if status.get("NoNewPrivs") != "1":
        raise RuntimeError("no_new_privs is not enabled")
    if status.get("Seccomp") != "2":
        raise RuntimeError("default seccomp filter is not active")
    if "ro" not in _mount_options("/"):
        raise RuntimeError("container root mount is not read-only")
    if "ro" not in _mount_options("/candidate"):
        raise RuntimeError("candidate artifact mount is not read-only")
    if "rw" not in _mount_options("/tmp"):
        raise RuntimeError("bounded /tmp tmpfs is not writable")
    if "rw" not in _mount_options("/dev/shm"):
        raise RuntimeError("bounded /dev/shm tmpfs is not writable")
    tmpfs_stat = os.statvfs("/tmp")
    shm_stat = os.statvfs("/dev/shm")
    tmpfs_capacity = tmpfs_stat.f_blocks * tmpfs_stat.f_frsize
    shm_capacity = shm_stat.f_blocks * shm_stat.f_frsize
    if not 0 < tmpfs_capacity <= 256 * 1024**2:
        raise RuntimeError("/tmp tmpfs exceeds the 256 MiB pilot cap")
    if not 0 < shm_capacity <= 64 * 1024**2:
        raise RuntimeError("/dev/shm exceeds the 64 MiB cap")
    tmp_probe = Path("/tmp/bananloop-write-probe")
    tmp_probe.write_text("ok", encoding="ascii")
    tmp_probe.unlink()
    try:
        Path("/candidate/.bananloop-write-probe").write_text("must fail", encoding="ascii")
    except OSError:
        pass
    else:
        raise RuntimeError("candidate artifact mount unexpectedly allows writes")
    try:
        socket.create_connection(("1.1.1.1", 80), timeout=0.5)
    except OSError:
        network_blocked = True
    else:
        network_blocked = False
    if not network_blocked:
        raise RuntimeError("candidate container reached an external network address")
    cpus, memory_bytes, pids = _resource_limits()
    if cpus > 1.0001:
        raise RuntimeError("CPU quota exceeds the one-core pilot cap")
    if memory_bytes > 8 * 1024**3:
        raise RuntimeError("memory limit exceeds the 8 GiB pilot cap")
    if pids > 64:
        raise RuntimeError("PID limit exceeds the 64-process pilot cap")
    targets = _mount_targets()
    forbidden_targets = (
        "/root/.ssh",
        "/home",
        "/host",
        "/run/desktop/mnt/host",
        "/var/run/docker.sock",
        "/mnt/c/Users",
        "/Users",
    )
    if any(
        target == forbidden or target.startswith(forbidden + "/")
        for target in targets
        for forbidden in forbidden_targets
    ):
        raise RuntimeError("a host profile, credential directory, or Docker socket appears mounted")
    return {
        "status": "PASS",
        "uid": os.geteuid(),
        "cap_eff": status["CapEff"],
        "no_new_privs": status["NoNewPrivs"],
        "seccomp": status["Seccomp"],
        "root_read_only": True,
        "candidate_read_only": True,
        "tmpfs_writable": True,
        "tmpfs_capacity_bytes": tmpfs_capacity,
        "shm_capacity_bytes": shm_capacity,
        "external_network_blocked": network_blocked,
        "cpu_quota": cpus,
        "memory_limit_bytes": memory_bytes,
        "pids_limit": pids,
        "mount_target_count": len(targets),
        "python": sys.version.split()[0],
    }


if __name__ == "__main__":
    try:
        print(json.dumps(probe(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "reason": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
