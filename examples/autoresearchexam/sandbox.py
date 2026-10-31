"""Fail-closed Docker boundary for executing an untrusted candidate artifact.

The trusted evaluator must call this runner with only the input for one case.
The evaluator/grader directory and host environment are deliberately not
mounted or forwarded to the candidate container.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid


class SandboxError(RuntimeError):
    pass


_PINNED_IMAGE = re.compile(r"^[^\s,]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class CandidateSandbox:
    image: str
    artifact_dir: Path
    command: tuple[str, ...]
    timeout_seconds: int = 30
    cpu_count: float = 1.0
    memory_bytes: int = 2 * 1024**3
    pids_limit: int = 64
    tmpfs_bytes: int = 256 * 1024**2
    output_limit_bytes: int = 1024 * 1024
    input_limit_bytes: int = 16 * 1024**2
    docker_executable: str = "docker"


@dataclass(frozen=True)
class CandidateResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def stage_candidate_artifact(
    source_root: Path,
    destination: Path,
    allowed_files: tuple[str, ...],
) -> Path:
    """Copy allowlisted files from a frozen snapshot into a detached artifact.

    The caller must stop all writers to ``source_root`` before staging. This
    detached copy is not an OS access-control boundary from another process
    running as the same host user.
    """
    source = source_root.resolve(strict=True)
    target = destination.absolute()
    if not source.is_dir() or source_root.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(source_root)
    ):
        raise SandboxError("candidate source must be a real directory")
    try:
        target.resolve(strict=False).relative_to(source)
    except ValueError:
        pass
    else:
        raise SandboxError("candidate artifact destination cannot be inside its source")
    if target.exists():
        raise SandboxError("candidate artifact destination must not already exist")
    if not allowed_files:
        raise SandboxError("candidate artifact allowlist cannot be empty")
    if len(allowed_files) > 128:
        raise SandboxError("candidate artifact allowlist exceeds 128 files")
    normalized: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    total_bytes = 0
    for raw in allowed_files:
        if not raw or "\\" in raw or "\0" in raw:
            raise SandboxError("candidate allowlist paths must use canonical forward slashes")
        relative = Path(raw)
        posix_parts = raw.split("/")
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in posix_parts):
            raise SandboxError("candidate allowlist path is absolute or traverses a directory")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in posix_parts):
            raise SandboxError("candidate allowlist path contains unsupported characters")
        normalized_path = "/".join(posix_parts)
        if normalized_path in seen:
            raise SandboxError("candidate allowlist contains a duplicate path")
        seen.add(normalized_path)
        source_file = source.joinpath(*posix_parts)
        cursor = source
        for part in posix_parts:
            cursor = cursor / part
            if cursor.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(cursor)):
                raise SandboxError("candidate artifact cannot contain symlinks or junctions")
        try:
            mode = source_file.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise SandboxError(f"candidate artifact file is unavailable: {raw}") from exc
        if not stat.S_ISREG(mode):
            raise SandboxError(f"candidate artifact path is not a regular file: {raw}")
        file_size = source_file.stat(follow_symlinks=False).st_size
        total_bytes += file_size
        if total_bytes > 64 * 1024**2:
            raise SandboxError("candidate artifact exceeds 64 MiB total")
        normalized.append((normalized_path, source_file, _sha256_path(source_file)))

    target.mkdir(parents=True)
    for relative, source_file, source_digest in normalized:
        output = target.joinpath(*relative.split("/"))
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, output, follow_symlinks=False)
        try:
            output_mode = output.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise SandboxError(f"staged candidate artifact is unavailable: {relative}") from exc
        if not stat.S_ISREG(output_mode) or output.is_symlink():
            raise SandboxError(f"staged candidate artifact is not a regular file: {relative}")
        if _sha256_path(source_file) != source_digest or _sha256_path(output) != source_digest:
            raise SandboxError(f"candidate source changed while staging: {relative}")
        try:
            output.chmod(0o444)
        except OSError:
            # Docker mounts the artifact read-only as a second, enforceable
            # layer even on filesystems without POSIX mode support.
            pass
    for directory, _children, _files in os.walk(target, topdown=False):
        try:
            Path(directory).chmod(0o555)
        except OSError:
            pass
    return target.resolve(strict=True)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(spec: CandidateSandbox) -> Path:
    if not _PINNED_IMAGE.fullmatch(spec.image):
        raise SandboxError("sandbox image must be pinned by sha256 digest")
    root = spec.artifact_dir.resolve(strict=True)
    if not root.is_dir() or spec.artifact_dir.is_symlink():
        raise SandboxError("candidate artifact must be a real directory")
    if (
        not spec.command
        or len(spec.command) > 128
        or any(not isinstance(part, str) or "\0" in part or len(part) > 4096 for part in spec.command)
    ):
        raise SandboxError("candidate command must be a bounded argv without NUL bytes")
    if spec.timeout_seconds < 1 or spec.timeout_seconds > 2400:
        raise SandboxError("candidate timeout must be between 1 and 2400 seconds")
    if spec.cpu_count <= 0 or spec.cpu_count > 2:
        raise SandboxError("candidate CPU limit must be in (0, 2]")
    if spec.memory_bytes < 64 * 1024**2 or spec.memory_bytes > 8 * 1024**3:
        raise SandboxError("candidate memory limit must be between 64 MiB and 8 GiB")
    if spec.pids_limit < 1 or spec.pids_limit > 64:
        raise SandboxError("candidate pids limit must be between 1 and 64")
    if spec.tmpfs_bytes < 1024**2 or spec.tmpfs_bytes > 24 * 1024**3:
        raise SandboxError("candidate tmpfs must be between 1 MiB and 24 GiB")
    if spec.output_limit_bytes < 1 or spec.output_limit_bytes > 16 * 1024**2:
        raise SandboxError("candidate output limit must be between 1 byte and 16 MiB")
    if spec.input_limit_bytes < 1 or spec.input_limit_bytes > 16 * 1024**2:
        raise SandboxError("per-case input limit must be between 1 byte and 16 MiB")
    # Docker's --mount CSV syntax has no portable escaping for commas.
    if "," in str(root):
        raise SandboxError("candidate artifact path cannot contain a comma")
    return root


def docker_argv(spec: CandidateSandbox, *, name: str) -> list[str]:
    """Build the complete container command; no environment or grader mount."""
    artifact_dir = _validate(spec)
    if not re.fullmatch(r"bananloop-candidate-[0-9a-f]{32}", name):
        raise SandboxError("invalid temporary container name")
    return [
        spec.docker_executable,
        "run",
        "--rm",
        "--pull=never",
        "--name", name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--log-driver=local",
        "--log-opt=max-size=1m",
        "--log-opt=max-file=1",
        "--log-opt=compress=false",
        "--user=65532:65532",
        "--pids-limit", str(spec.pids_limit),
        "--memory", str(spec.memory_bytes),
        "--cpus", str(spec.cpu_count),
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={spec.tmpfs_bytes}",
        "--shm-size=64m",
        "--mount", f"type=bind,source={artifact_dir},target=/candidate,readonly",
        "--workdir", "/candidate",
        "--entrypoint", spec.command[0],
        spec.image,
        *spec.command[1:],
    ]


def _capture_bounded(stream, buffer: bytearray, limit: int, overflow: threading.Event) -> None:
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        remaining = limit - len(buffer)
        if remaining > 0:
            buffer.extend(chunk[:remaining])
        if len(chunk) > remaining:
            overflow.set()
            return


def _kill_container(executable: str, name: str) -> None:
    # A timeout may kill the Docker CLI while the daemon-owned container keeps
    # running. Address only this invocation's unpredictable name.
    for operation in ("kill", "rm"):
        args = [executable, operation]
        if operation == "rm":
            args.append("--force")
        args.append(name)
        try:
            subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                env=_docker_cli_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _docker_cli_env() -> dict[str, str]:
    # Keep process lookup/platform essentials only. Docker context must be
    # configured in trusted Docker CLI settings; campaign env is never passed.
    names = {"PATH", "SYSTEMROOT", "WINDIR"}
    return {key: value for key, value in os.environ.items() if key.upper() in names}


def _validate_image(spec: CandidateSandbox) -> None:
    """Reject image metadata that would add writable volumes or secret env."""
    try:
        inspected = subprocess.run(
            [
                spec.docker_executable,
                "image",
                "inspect",
                "--format",
                "{{json .Config.Volumes}}|{{json .Config.Env}}",
                spec.image,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env=_docker_cli_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("cannot verify local sandbox image metadata") from exc
    if inspected.returncode != 0:
        raise SandboxError("digest-pinned sandbox image is not available locally")
    try:
        volumes_json, env_json = inspected.stdout.strip().split("|", 1)
        volumes = json.loads(volumes_json)
        image_env = json.loads(env_json)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SandboxError("sandbox image metadata could not be parsed") from exc
    if volumes:
        raise SandboxError("sandbox image declares Docker volumes; refusing implicit writable mounts")
    if not isinstance(image_env, list):
        raise SandboxError("sandbox image environment metadata is invalid")
    sensitive = re.compile(r"(?i)(api[_-]?key|token|secret|password|credential|auth)")
    if any(sensitive.search(str(value).partition("=")[0]) for value in image_env):
        raise SandboxError("sandbox image declares a credential-like environment variable")


def run_candidate(spec: CandidateSandbox, *, case_input: bytes) -> CandidateResult:
    """Run one candidate case without host env, grader mounts, or network."""
    if not isinstance(case_input, bytes):
        raise SandboxError("case input must be bytes")
    if len(case_input) > spec.input_limit_bytes:
        raise SandboxError("per-case input exceeds its configured limit")
    name = "bananloop-candidate-" + uuid.uuid4().hex
    argv = docker_argv(spec, name=name)
    _validate_image(spec)
    # Do not expose the campaign's .env, Codex credentials, or arbitrary
    # project variables to the Docker CLI. It uses the trusted local Docker
    # context; no host environment is forwarded into the container.
    docker_env = _docker_cli_env()
    output = bytearray()
    errors = bytearray()
    overflow = threading.Event()
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path(tempfile.gettempdir()),
            env=docker_env,
            shell=False,
        )
    except OSError as exc:
        raise SandboxError(f"cannot start Docker CLI: {exc}") from exc
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    out_thread = threading.Thread(target=_capture_bounded, args=(proc.stdout, output, spec.output_limit_bytes, overflow), daemon=True)
    err_thread = threading.Thread(target=_capture_bounded, args=(proc.stderr, errors, spec.output_limit_bytes, overflow), daemon=True)
    out_thread.start()
    err_thread.start()
    deadline = time.monotonic() + spec.timeout_seconds
    completed = False

    def send_input() -> None:
        try:
            proc.stdin.write(case_input)
            proc.stdin.close()
        except OSError:
            pass

    input_thread = threading.Thread(target=send_input, daemon=True)
    input_thread.start()
    try:
        while proc.poll() is None:
            if overflow.is_set():
                raise SandboxError("candidate exceeded the bounded output limit")
            if time.monotonic() >= deadline:
                raise SandboxError(f"candidate timed out after {spec.timeout_seconds}s")
            time.sleep(0.025)
        proc.wait()
        input_thread.join(timeout=2)
        out_thread.join(timeout=2)
        err_thread.join(timeout=2)
        if overflow.is_set():
            raise SandboxError("candidate exceeded the bounded output limit")
        completed = True
    finally:
        if not completed:
            _kill_container(spec.docker_executable, name)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    return CandidateResult(proc.returncode, bytes(output), bytes(errors))
