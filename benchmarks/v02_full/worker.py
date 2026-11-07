"""Bubblewrap boundary for one blinded RPDI-14 arm task.

The trusted coordinator creates one task envelope and invokes a frozen command
inside a fresh networkless process. The worker receives no opposite-track
manifest, evaluator bundle, validation/holdout data, host environment, or API
credentials. Provider calls must use the optional per-worker broker socket.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import secrets
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Literal

from .evidence import (EvidenceArtifactRef, EvidenceError, load_trust_policy,
                       verify_evidence_artifact, verify_protected_directory)
from .execution import ArmAssignmentProjection
from .prereg import ArmConfig, TrackManifest, canonical_bytes


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_INPUT_BYTES = 2_000_000
_MAX_RESULT_BYTES = 2_000_000
_MAX_LOG_BYTES = 25_000_000
_MAX_OUTPUT_FILES = 128
_MAX_OUTPUT_BYTES = 25_000_000
_MAX_RUNTIME_FILES = 10_000
_MAX_RUNTIME_BYTES = 250_000_000
_MAX_RUNTIME_FILE_BYTES = 25_000_000
_SHELLS = {"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"}


class ArmWorkerError(RuntimeError):
    """One arm task cannot be run in the required isolated worker process."""


_WORKER_PERMIT_PAYLOAD_KEYS = frozenset({
    "authorization_sha256", "assignment_sha256", "manifest_sha256", "track",
    "task_id", "pair_id_sha256", "arm_label", "arm_configuration_sha256",
    "sequence", "action_id_sha256", "action_kind", "bounds_json_sha256",
    "bound_proof_sha256", "meter_database_path_sha256",
    "broker_socket_path_sha256", "broker_socket_device", "broker_socket_inode",
})


def _broker_socket_identity(socket_path: str | Path | None) -> dict[str, str]:
    if socket_path is None:
        raise ArmWorkerError("metered worker launch requires its one-shot provider broker socket")
    broker_directory, socket_name = _validate_broker_socket(socket_path)
    path = broker_directory / socket_name
    info = path.lstat()
    return {
        "broker_socket_path_sha256": hashlib.sha256(
            str(path.absolute()).encode("utf-8")
        ).hexdigest(),
        "broker_socket_device": str(info.st_dev),
        "broker_socket_inode": str(info.st_ino),
    }


def _verify_metered_arm_launch_permit(
    permit: EvidenceArtifactRef | None, *, projection: ArmAssignmentProjection,
    manifest: TrackManifest, arm_config: ArmConfig, arm_label: str, action_id: str,
    evidence_root: str | Path, trust_policy_path: str | Path,
    provider_broker_socket: str | Path | None,
) -> dict[str, str]:
    if not isinstance(permit, EvidenceArtifactRef):
        raise ArmWorkerError("a signed metered-dispatch permit is required")
    try:
        verify_protected_directory(evidence_root)
        policy, _ = load_trust_policy(trust_policy_path)
        expected = {
            "authorization_sha256": projection.authorization_sha256,
            "assignment_sha256": projection.assignment_evidence.artifact_sha256,
            "manifest_sha256": manifest.manifest_sha256,
            "track": manifest.track,
            "task_id": projection.task.task_id,
            "pair_id_sha256": projection.task.blinded_pair_id_sha256,
            "arm_label": arm_label,
            "arm_configuration_sha256": hashlib.sha256(canonical_bytes(arm_config)).hexdigest(),
            "action_id_sha256": action_id,
            "action_kind": "MODEL_CALL",
            "meter_database_path_sha256": hashlib.sha256(str(
                (Path(evidence_root) / "rpdi14-action-meter.sqlite3").resolve(strict=True)
            ).encode("utf-8")).hexdigest(),
            **_broker_socket_identity(provider_broker_socket),
        }
        verified = verify_evidence_artifact(
            root=evidence_root, reference=permit, policy=policy,
            evidence_type="WORKER_LAUNCH", subject_sha=action_id,
            conclusion="ACCEPTED", issuer_role="ACTION_METER_ATTESTOR",
            expected_payload=expected,
        ).document
        if set(verified.payload) != _WORKER_PERMIT_PAYLOAD_KEYS:
            raise EvidenceError("worker launch permit has an invalid payload shape")
        return verified.payload
    except (ArmWorkerError, EvidenceError, OSError, ValueError) as exc:
        raise ArmWorkerError(f"worker launch permit does not verify: {exc}") from exc


def _consume_metered_arm_launch_permit(
    payload: dict[str, str], *, evidence_root: str | Path,
) -> None:
    """Atomically consume the sole launch for a still-pending meter intent."""
    root = Path(evidence_root)
    database = root / "rpdi14-action-meter.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise ArmWorkerError("worker launch permit has no protected meter database")
    info = database.stat()
    if info.st_mode & 0o077:
        raise ArmWorkerError("meter database permissions are too broad for worker launch")
    database_path_sha256 = hashlib.sha256(
        str(database.resolve(strict=True)).encode("utf-8")
    ).hexdigest()
    if payload["meter_database_path_sha256"] != database_path_sha256:
        raise ArmWorkerError("worker launch permit is bound to a different meter database")
    if payload.get("action_kind") != "MODEL_CALL":
        raise ArmWorkerError("only a metered MODEL_CALL intent may launch an arm worker")
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM meter_actions WHERE pair_id=? AND arm_label=? AND sequence=?",
            (payload["pair_id_sha256"], payload["arm_label"], int(payload["sequence"])),
        ).fetchone()
        if (row is None or row["state"] != "PENDING"
                or row["action_id"] != payload["action_id_sha256"]
                or payload["action_kind"] != "MODEL_CALL"
                or row["action_kind"] != "MODEL_CALL"
                or row["arm_configuration_sha256"] != payload["arm_configuration_sha256"]
                or row["bound_proof_sha256"] != payload["bound_proof_sha256"]
                or hashlib.sha256(row["bounds_json"].encode("utf-8")).hexdigest()
                != payload["bounds_json_sha256"]):
            raise ArmWorkerError("worker launch permit does not match a pending metered action")
        changed = connection.execute(
            "UPDATE meter_actions SET worker_launch_state='LAUNCHED' "
            "WHERE pair_id=? AND arm_label=? AND sequence=? AND action_id=? "
            "AND state='PENDING' AND worker_launch_state='NOT_LAUNCHED'",
            (payload["pair_id_sha256"], payload["arm_label"], int(payload["sequence"]),
             payload["action_id_sha256"]),
        )
        if changed.rowcount != 1:
            raise ArmWorkerError("worker launch permit has already been consumed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _cgroup_controllers_available(root: Path) -> bool:
    if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
            or any(part.is_symlink() for part in (root, *root.parents))):
        return False
    try:
        info = root.stat()
        if (not _is_cgroup2_path(root) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or not os.access(root, os.W_OK)):
            return False
        controllers = set((root / "cgroup.controllers").read_text(encoding="ascii").split())
        enabled = set((root / "cgroup.subtree_control").read_text(encoding="ascii").split())
    except OSError:
        return False
    return {"memory", "pids"}.issubset(controllers & enabled)


def _is_cgroup2_path(root: Path, *, mountinfo: Path = Path("/proc/self/mountinfo")) -> bool:
    """Check the effective deepest mount is cgroup v2, not marker files."""
    try:
        resolved = root.resolve(strict=True)
        matches: list[tuple[int, int, str]] = []
        for index, line in enumerate(mountinfo.read_text(encoding="utf-8").splitlines()):
            before, separator, after = line.partition(" - ")
            if not separator:
                continue
            fields, post_fields = before.split(), after.split()
            if len(fields) < 5 or not post_fields:
                continue
            mount_text = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
            mountpoint = Path(mount_text).resolve(strict=True)
            if resolved.is_relative_to(mountpoint):
                matches.append((len(mountpoint.parts), index, post_fields[0]))
    except (OSError, RuntimeError):
        return False
    return bool(matches) and max(matches)[2] == "cgroup2"


@dataclass(frozen=True)
class ArmWorkerResult:
    output: bytes
    task_id: str
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    pair_id_sha256: str
    elapsed_seconds: float


def _safe_workspace(root: str | Path) -> Path:
    path = Path(root).absolute()
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ArmWorkerError("worker workspace path contains a symlink")
    if not path.is_dir():
        raise ArmWorkerError("worker workspace must be a real directory")
    for current, dirs, files in os.walk(path, followlinks=False):
        base = Path(current)
        for name in (*dirs, *files):
            candidate = base / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ArmWorkerError("worker workspace contains a symlink")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ArmWorkerError("worker workspace contains a special file")
    return path.resolve()


def worker_bundle_fingerprint(
    workspace: str | Path,
    argv: list[str] | tuple[str, ...],
    *,
    sandbox_binary: str | Path,
) -> str:
    """Bind command, workspace files, and bubblewrap executable to one SHA-256."""
    root = _safe_workspace(workspace)
    command = _validate_command(argv)
    sandbox = Path(sandbox_binary).absolute()
    if sandbox.is_symlink() or not sandbox.is_file() or not os.access(sandbox, os.X_OK):
        raise ArmWorkerError("bubblewrap executable must be a trusted regular executable")
    virtual_executable = PurePosixPath(command[0])
    if (not virtual_executable.is_absolute() or ".." in virtual_executable.parts
            or "\\" in command[0]):
        raise ArmWorkerError("worker executable must use an absolute pinned path")
    if command[0].startswith("/workspace/"):
        executable_path = root.joinpath(*virtual_executable.parts[2:])
        if not executable_path.is_relative_to(root) or executable_path.is_symlink():
            raise ArmWorkerError("worker executable escapes its frozen bundle")
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            raise ArmWorkerError("worker executable is missing from its frozen bundle")
        executable_identity = {"path": command[0], "bundle_file": executable_path.relative_to(root).as_posix()}
    else:
        executable = Path(command[0])
        executable_path = executable.resolve()
        allowed_system_roots = tuple(Path(item).resolve() for item in ("/usr", "/bin", "/lib", "/lib64")
                                     if Path(item).exists())
        if (not any(executable_path.is_relative_to(item) for item in allowed_system_roots)
                or not executable_path.is_file() or not os.access(executable_path, os.X_OK)):
            raise ArmWorkerError("worker executable is outside the read-only sandbox mounts")
        executable_identity = {
            "path": command[0], "resolved_path": str(executable_path),
            "sha256": hashlib.sha256(executable_path.read_bytes()).hexdigest(),
            "mode": stat.S_IMODE(executable_path.stat().st_mode),
        }
    entries = []
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        base = Path(current)
        for name in dirs:
            path = base / name
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ArmWorkerError("worker runtime contains a non-directory path")
            entries.append({"path": path.relative_to(root).as_posix(),
                            "kind": "directory", "mode": stat.S_IMODE(info.st_mode)})
        for name in sorted(files):
            path = base / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_RUNTIME_FILE_BYTES:
                raise ArmWorkerError("worker runtime contains a non-regular or oversized file")
            total += info.st_size
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "kind": "file",
                "size": info.st_size,
                "mode": stat.S_IMODE(info.st_mode),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        if total > _MAX_RUNTIME_BYTES or len(entries) > _MAX_RUNTIME_FILES:
            raise ArmWorkerError("worker runtime exceeds its file or byte limit")
    launcher_interpreter = Path(sys.executable).resolve()
    if not launcher_interpreter.is_file():
        raise ArmWorkerError("trusted worker launcher interpreter is unavailable")
    return hashlib.sha256(canonical_bytes({
        "argv": list(command), "executable": executable_identity, "files": entries,
        "sandbox_sha256": hashlib.sha256(sandbox.read_bytes()).hexdigest(),
        "worker_harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "worker_launcher_sha256": hashlib.sha256(
            Path(__file__).with_name("worker_launcher.py").read_bytes()).hexdigest(),
        "launcher_interpreter": {
            "path": str(launcher_interpreter),
            "sha256": hashlib.sha256(launcher_interpreter.read_bytes()).hexdigest(),
            "python_version": sys.version,
        },
    })).hexdigest()


def _arm_envelope(
    projection: ArmAssignmentProjection,
    manifest: TrackManifest,
    arm_config: ArmConfig,
    *,
    arm_label: Literal["A", "B"],
    action_id: str,
) -> bytes:
    task = projection.task
    if (projection.track != manifest.track or projection.manifest_sha256 != manifest.manifest_sha256
            or arm_config not in manifest.arms or arm_label not in {"A", "B"}):
        raise ArmWorkerError("worker input is not bound to the frozen arm manifest")
    pin = next((item for item in manifest.task_pins if item.task_id == task.task_id), None)
    if (pin is None or pin.blinded_pair_id_sha256 != task.blinded_pair_id_sha256
            or pin.task_data_sha256 != task.task_data_sha256
            or hashlib.sha256(task.task_payload).hexdigest() != pin.task_data_sha256):
        raise ArmWorkerError("worker task payload differs from the frozen task pin")
    payload = {
        "schema_version": 1,
        "action_id": action_id,
        "track": manifest.track,
        "arm_label": arm_label,
        "pair_id_sha256": pin.blinded_pair_id_sha256,
        "task_id": pin.task_id,
        "task_data_sha256": pin.task_data_sha256,
        "initial_tree_sha256": pin.initial_tree_sha256,
        "task_payload_base64": base64.b64encode(task.task_payload).decode("ascii"),
        "approved_dev_information_sha256": manifest.approved_dev_information_sha256,
        "worker_bundle_sha256": arm_config.worker_bundle_sha256,
        "arm_config": arm_config.model_dump(mode="json"),
        "resource_caps": manifest.common_caps.model_dump(mode="json"),
    }
    raw = canonical_bytes(payload)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ArmWorkerError("single-task worker envelope exceeds its byte limit")
    return raw


def _validate_command(argv: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)):
        raise ArmWorkerError("worker command must be a nonempty frozen argv vector")
    basenames = {Path(token.replace("\\", "/")).name.lower()
                 for token in argv if not token.startswith("-")}
    if basenames & _SHELLS or any(token in {"-c", "-e", "--eval"} for token in argv):
        raise ArmWorkerError("worker command cannot use a shell or inline code")
    return tuple(argv)


def _validate_broker_socket(socket_path: str | Path | None) -> tuple[Path, str] | None:
    if socket_path is None:
        return None
    path = Path(socket_path).absolute()
    for component in reversed(path.parents):
        if component.is_symlink():
            raise ArmWorkerError("broker socket path may not contain symlinks")
    if path.is_symlink():
        raise ArmWorkerError("broker socket path may not contain symlinks")
    try:
        parent = path.parent.stat()
        info = path.lstat()
    except OSError as exc:
        raise ArmWorkerError("provider broker socket is unavailable") from exc
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077
            or not stat.S_ISSOCK(info.st_mode) or info.st_mode & 0o077
            or (hasattr(os, "geteuid") and parent.st_uid not in {0, os.geteuid()})):
        raise ArmWorkerError("broker socket must be private inside a mode-0700 directory")
    try:
        contents = list(path.parent.iterdir())
    except OSError as exc:
        raise ArmWorkerError("cannot inspect private broker directory") from exc
    if contents != [path]:
        raise ArmWorkerError("private broker directory must contain only the worker socket")
    return path.parent, path.name


def _configure_worker_cgroup(child: Path, *, memory_bytes: int, process_limit: int) -> None:
    for name, value in (
        ("memory.max", str(memory_bytes)),
        ("memory.oom.group", "1"),
        ("pids.max", str(process_limit)),
    ):
        target = child / name
        if target.is_symlink() or not target.is_file():
            raise ArmWorkerError(f"delegated cgroup is missing {name}")
        target.write_text(value, encoding="ascii")
        if target.read_text(encoding="ascii").strip() != value:
            raise ArmWorkerError(f"delegated cgroup did not apply {name}")
    if not (child / "cgroup.procs").is_file():
        raise ArmWorkerError("delegated cgroup is missing cgroup.procs")
    if not (child / "cgroup.kill").is_file() or not (child / "cgroup.events").is_file():
        raise ArmWorkerError("delegated cgroup lacks process-tree kill/events controls")


class _WorkerCgroup:
    """Delegated cgroup v2 limits covering the launcher and its full process tree."""

    def __init__(self, root: Path, *, memory_bytes: int, process_limit: int):
        self.root = root
        self.memory_bytes = memory_bytes
        self.process_limit = process_limit
        self.path: Path | None = None

    def __enter__(self) -> str:
        root = self.root
        if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
                or any(part.is_symlink() for part in (root, *root.parents))):
            raise ArmWorkerError("worker cgroup root must be an absolute, non-symlink delegated directory")
        if not _cgroup_controllers_available(root):
            raise ArmWorkerError("delegated cgroup v2 must enable memory and pids controllers")
        child = root / f"bananloop-worker-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            child.mkdir(mode=0o700)
            _configure_worker_cgroup(child, memory_bytes=self.memory_bytes,
                                     process_limit=self.process_limit)
        except Exception as exc:
            self.path = child if child.exists() else None
            self._remove_empty_child()
            if isinstance(exc, ArmWorkerError):
                raise
            raise ArmWorkerError("cannot create bounded worker cgroup") from exc
        self.path = child
        return str(child / "cgroup.procs")

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self.path is None:
            return False
        try:
            (self.path / "cgroup.kill").write_text("1", encoding="ascii")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                events = (self.path / "cgroup.events").read_text(encoding="ascii").splitlines()
                if "populated 0" in events:
                    break
                time.sleep(0.02)
            else:
                raise ArmWorkerError("worker process tree did not leave its cgroup")
            self.path.rmdir()
            self.path = None
        except OSError as cleanup_error:
            raise ArmWorkerError("cannot kill and remove worker process cgroup") from cleanup_error
        return False

    def _remove_empty_child(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            self.path.rmdir()
        except OSError:
            # A partially configured cgroup stays behind and makes this worker
            # fail closed; never weaken controls to recover automatically.
            pass


class BubblewrapArmWorker:
    """Run a single frozen task in a networkless, bounded Linux process."""

    def __init__(self, *, bwrap_binary: str | None = None,
                 cgroup_root: str | Path | None = None):
        self.bwrap_binary = bwrap_binary or shutil.which("bwrap") or ""
        self.cgroup_root = Path(cgroup_root).absolute() if cgroup_root is not None else None

    def available(self) -> bool:
        if (os.name != "posix" or not self.bwrap_binary or not Path(self.bwrap_binary).is_file()
                or self.cgroup_root is None):
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="rpdi14-worker-probe-") as temp:
                command = [self.bwrap_binary, "--die-with-parent", "--new-session", "--unshare-user",
                           "--unshare-pid", "--unshare-net", "--clearenv", "--ro-bind", "/usr", "/usr"]
                for system in ("/bin", "/lib", "/lib64"):
                    if Path(system).exists():
                        command.extend(["--ro-bind", system, system])
                command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                                "--chdir", "/tmp", "/usr/bin/true"])
                result = subprocess.run(
                    command, cwd=temp, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"},
                    shell=False, timeout=5, check=False,
                )
                return result.returncode == 0 and _cgroup_controllers_available(self.cgroup_root)
        except (OSError, subprocess.SubprocessError):
            return False

    def run(
        self,
        *,
        argv: list[str] | tuple[str, ...],
        workspace: str | Path,
        projection: ArmAssignmentProjection,
        manifest: TrackManifest,
        arm_config: ArmConfig,
        arm_label: Literal["A", "B"],
        action_id: str,
        timeout_seconds: int,
        memory_bytes: int,
        provider_broker_socket: str | Path | None = None,
        _metered_permit: EvidenceArtifactRef | None = None,
        evidence_root: str | Path | None = None,
        trust_policy_path: str | Path | None = None,
    ) -> ArmWorkerResult:
        if evidence_root is None or trust_policy_path is None:
            raise ArmWorkerError("protected evidence and trust-policy roots are required")
        permit_payload = _verify_metered_arm_launch_permit(
            _metered_permit, projection=projection, manifest=manifest,
            arm_config=arm_config, arm_label=arm_label, action_id=action_id,
            evidence_root=evidence_root, trust_policy_path=trust_policy_path,
            provider_broker_socket=provider_broker_socket,
        )
        command_argv = _validate_command(argv)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= manifest.common_caps.wall_seconds:
            raise ArmWorkerError("worker timeout is outside the frozen wall-time cap")
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ArmWorkerError("worker memory ceiling must be a positive integer")
        if memory_bytes != arm_config.worker_memory_bytes:
            raise ArmWorkerError("worker memory ceiling differs from the frozen arm configuration")
        runtime_root = _safe_workspace(workspace)
        runtime_sha256 = worker_bundle_fingerprint(
            runtime_root, command_argv, sandbox_binary=self.bwrap_binary,
        )
        if runtime_sha256 != arm_config.worker_bundle_sha256:
            raise ArmWorkerError("worker bundle does not match the frozen arm bundle digest")
        broker = _validate_broker_socket(provider_broker_socket)
        if _broker_socket_identity(provider_broker_socket) != {
            key: permit_payload[key] for key in (
                "broker_socket_path_sha256", "broker_socket_device", "broker_socket_inode",
            )
        }:
            raise ArmWorkerError("provider broker socket differs from the signed worker permit")
        if not self.available():
            raise ArmWorkerError("bubblewrap namespaces are unavailable; refusing local execution fallback")
        if not _DIGEST.fullmatch(action_id):
            raise ArmWorkerError("worker action ID must be a lowercase SHA-256 digest")
        envelope = _arm_envelope(projection, manifest, arm_config,
                                 arm_label=arm_label, action_id=action_id)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="rpdi14-arm-worker-") as temp:
            root = Path(temp)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            input_path = input_dir / "task.json"
            fd = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            command = [self.bwrap_binary, "--die-with-parent", "--new-session", "--unshare-user",
                       "--unshare-pid", "--unshare-net", "--clearenv"]
            for system in ("/usr", "/bin", "/lib", "/lib64"):
                if Path(system).exists():
                    command.extend(["--ro-bind", system, system])
            command.extend(["--dir", "/workspace", "--dir", "/input", "--dir", "/output",
                            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                            "--ro-bind", str(runtime_root), "/workspace",
                            "--ro-bind", str(input_dir.resolve()), "/input",
                            "--bind", str(output_dir.resolve()), "/output"])
            if broker:
                broker_dir, socket_name = broker
                command.extend(["--dir", "/run", "--dir", "/run/bananloop-broker"])
                command.extend(["--bind", str(broker_dir), "/run/bananloop-broker"])
            command.extend(["--chdir", "/workspace", "--setenv", "PATH", "/usr/bin:/bin",
                            "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
                            "--setenv", "LC_ALL", "C.UTF-8",
                            "--setenv", "BANANLOOP_TASK_INPUT", "/input/task.json",
                            "--setenv", "BANANLOOP_RESULT", "/output/result.json"])
            if broker:
                command.extend(["--setenv", "BANANLOOP_PROVIDER_BROKER",
                                f"/run/bananloop-broker/{socket_name}"])
            command.extend([*command_argv])
            stdout_path, stderr_path = root / "stdout", root / "stderr"
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                if self.cgroup_root is None:
                    raise ArmWorkerError("a delegated cgroup v2 root is required for worker launch")
                with _WorkerCgroup(self.cgroup_root, memory_bytes=memory_bytes,
                                   process_limit=arm_config.worker_process_limit) as cgroup_procs:
                    _consume_metered_arm_launch_permit(permit_payload, evidence_root=evidence_root)
                    if _broker_socket_identity(provider_broker_socket) != {
                        key: permit_payload[key] for key in (
                            "broker_socket_path_sha256", "broker_socket_device", "broker_socket_inode",
                        )
                    }:
                        raise ArmWorkerError("provider broker socket changed before worker launch")
                    launcher = Path(__file__).with_name("worker_launcher.py")
                    launch_command = [sys.executable, str(launcher), cgroup_procs,
                                      str(timeout_seconds), str(memory_bytes), "--", *command]
                    try:
                        process = subprocess.Popen(
                            launch_command, cwd=runtime_root, stdin=subprocess.DEVNULL,
                            stdout=stdout_file, stderr=stderr_file,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                            shell=False, start_new_session=True,
                        )
                    except OSError as exc:
                        raise ArmWorkerError("cannot start isolated arm worker") from exc
                    deadline = started + timeout_seconds
                    try:
                        while process.poll() is None:
                            _check_worker_output(output_dir)
                            if stdout_path.stat().st_size > _MAX_LOG_BYTES or stderr_path.stat().st_size > _MAX_LOG_BYTES:
                                raise ArmWorkerError("arm worker log exceeded its size limit")
                            if time.monotonic() >= deadline:
                                raise subprocess.TimeoutExpired(launch_command, timeout_seconds)
                            time.sleep(0.05)
                    except (ArmWorkerError, subprocess.TimeoutExpired):
                        _kill_process_group(process)
                        raise
                if process.returncode != 0:
                    raise ArmWorkerError("isolated arm worker exited unsuccessfully")
                _check_worker_output(output_dir)
                if stdout_path.stat().st_size > _MAX_LOG_BYTES or stderr_path.stat().st_size > _MAX_LOG_BYTES:
                    raise ArmWorkerError("arm worker log exceeded its size limit")
            result_path = output_dir / "result.json"
            if (result_path.is_symlink() or not result_path.is_file()
                    or result_path.stat().st_size > _MAX_RESULT_BYTES):
                raise ArmWorkerError("arm worker result is missing or exceeds its size limit")
            result = result_path.read_bytes()
            try:
                decoded = json.loads(result)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArmWorkerError("arm worker result is not valid JSON") from exc
            if not isinstance(decoded, dict) or decoded.get("schema_version") != 1:
                raise ArmWorkerError("arm worker result has an invalid envelope")
            return ArmWorkerResult(
                output=result, task_id=projection.task.task_id, track=manifest.track,
                pair_id_sha256=projection.task.blinded_pair_id_sha256,
                elapsed_seconds=time.monotonic() - started,
            )


def _check_worker_output(root: Path) -> None:
    total = count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        count += len(dirs) + len(files)
        if count > _MAX_OUTPUT_FILES:
            raise ArmWorkerError("arm worker created too many output files")
        for name in (*dirs, *files):
            path = base / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ArmWorkerError("arm worker output contains a symlink")
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > _MAX_OUTPUT_BYTES:
                    raise ArmWorkerError("arm worker output exceeded its size limit")
            elif not stat.S_ISDIR(info.st_mode):
                raise ArmWorkerError("arm worker output contains a special file")


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
