"""Blind scorer input/output contracts, separate from arm accounting evidence.

The scorer contract exposes only anonymized outputs, frozen evaluator pins,
blinded pair digests, and outcome status. It never accepts action receipts,
resource summaries, arm identities, or an unblinding map.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Annotated, Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import EvidenceError, TrustPolicy, read_content_artifact, verify_output_anonymization
from .prereg import TrackManifest, canonical_bytes
from .report import (
    BlindedScorePackage, pair_score_digest, score_payload_digest,
)
from .worker import (
    ArmWorkerError, BubblewrapArmWorker, _WorkerCgroup, _check_worker_output,
    _kill_process_group, _safe_workspace, _validate_command,
)


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_OUTPUT_BYTES = 2_000_000
MAX_REQUEST_BYTES = 64_000_000
MAX_EVALUATOR_FILES = 10_000
MAX_EVALUATOR_BYTES = 500_000_000
MAX_EVALUATOR_FILE_BYTES = 50_000_000
MAX_SCORER_LOG_BYTES = 1_000_000
MAX_SCORER_PAIRS = 500
MAX_TOTAL_OUTPUT_BYTES = 45_000_000
MAX_RUNTIME_FILES = 1_000_000
MAX_RUNTIME_BYTES = 10_000_000_000
EVALUATOR_CONSTITUENT_FILES = {
    "protocol_sha256": "protocol.json",
    "rubric_sha256": "rubric.json",
    "validation_set_sha256": "validation-set.bin",
    "holdout_set_sha256": "holdout-set.bin",
}


class ScorerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate scorer JSON key")
        result[key] = value
    return result


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("scorer number must be finite")
    return value


class ScorerInputPair(ScorerModel):
    pair_id_sha256: Digest
    status: Literal["SCORED", "FAILED", "EXCLUDED", "MISSING"]
    arm_a_output_sha256: Digest | None
    arm_a_output_base64: str | None
    arm_b_output_sha256: Digest | None
    arm_b_output_base64: str | None

    @model_validator(mode="after")
    def output_bytes_are_bound(self) -> "ScorerInputPair":
        for label in ("arm_a", "arm_b"):
            digest = getattr(self, f"{label}_output_sha256")
            encoded = getattr(self, f"{label}_output_base64")
            if (digest is None) != (encoded is None):
                raise ValueError("scorer output digest and bytes must be provided together")
            if encoded is not None:
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error) as exc:
                    raise ValueError("scorer input has malformed base64 output") from exc
                if (not raw or len(raw) > MAX_OUTPUT_BYTES or _sha(raw) != digest
                        or base64.b64encode(raw).decode("ascii") != encoded):
                    raise ValueError("scorer input output bytes do not match their canonical digest")
        if self.status == "SCORED" and any(
                getattr(self, f"{label}_output_base64") is None for label in ("arm_a", "arm_b")):
            raise ValueError("scored pair requires both anonymized arm outputs")
        if self.status != "SCORED" and any(
                getattr(self, f"{label}_output_base64") is not None for label in ("arm_a", "arm_b")):
            raise ValueError("unscored pair inputs must not disclose partial arm outputs")
        return self


class BlindScorerInput(ScorerModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: Digest
    evaluator_pin_sha256: Digest
    evaluator_protocol_sha256: Digest
    evaluator_bundle_sha256: Digest
    rubric_sha256: Digest
    validation_set_sha256: Digest
    holdout_set_sha256: Digest
    pairs: tuple[ScorerInputPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sorted_unique_pairs(self) -> "BlindScorerInput":
        ids = tuple(pair.pair_id_sha256 for pair in self.pairs)
        if tuple(sorted(set(ids))) != ids:
            raise ValueError("scorer input pairs must be unique and sorted by digest")
        return self


class ScorerOutputPair(ScorerModel):
    pair_id_sha256: Digest
    status: Literal["SCORED", "FAILED", "EXCLUDED", "MISSING"]
    arm_a_output_sha256: Digest | None
    arm_b_output_sha256: Digest | None
    arm_a_score: float | None
    arm_b_score: float | None

    @model_validator(mode="after")
    def scores_match_status(self) -> "ScorerOutputPair":
        scores = (self.arm_a_score, self.arm_b_score)
        if self.status == "SCORED":
            if any(value is None or not math.isfinite(value) for value in scores):
                raise ValueError("scored pair requires two finite scores")
            if self.arm_a_output_sha256 is None or self.arm_b_output_sha256 is None:
                raise ValueError("scored result requires both output digests")
        elif any(value is not None for value in scores):
            raise ValueError("unscored pair cannot contain scores")
        return self


class BlindScorerOutput(ScorerModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: Digest
    evaluator_pin_sha256: Digest
    pairs: tuple[ScorerOutputPair, ...] = Field(min_length=1)
    score_payload_sha256: Digest

    @model_validator(mode="after")
    def output_is_sorted_and_bound(self) -> "BlindScorerOutput":
        ids = tuple(pair.pair_id_sha256 for pair in self.pairs)
        if tuple(sorted(set(ids))) != ids:
            raise ValueError("scorer output pairs must be unique and sorted by digest")
        if self.score_payload_sha256 != scorer_output_digest(self):
            raise ValueError("scorer output digest does not match its blind score projection")
        return self


class ScorerError(ValueError):
    """A blind scorer request or response is malformed or misbound."""


def evaluator_bundle_fingerprint(root: str | Path) -> str:
    """Hash a sealed evaluator tree using stable paths, modes, sizes, and bytes."""
    bundle = _safe_workspace(root)
    entries = []
    total = 0
    for current, dirs, files in os.walk(bundle, followlinks=False):
        dirs.sort()
        base = Path(current)
        for name in dirs:
            path = base / name
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ScorerError("evaluator bundle contains a non-directory path")
            entries.append({"path": path.relative_to(bundle).as_posix(),
                            "kind": "directory", "mode": stat.S_IMODE(info.st_mode)})
        for name in sorted(files):
            path = base / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EVALUATOR_FILE_BYTES:
                raise ScorerError("evaluator bundle contains a special or oversized file")
            total += info.st_size
            if total > MAX_EVALUATOR_BYTES or len(entries) >= MAX_EVALUATOR_FILES:
                raise ScorerError("evaluator bundle exceeds its file or byte limit")
            entries.append({"path": path.relative_to(bundle).as_posix(), "kind": "file",
                            "size": info.st_size, "mode": stat.S_IMODE(info.st_mode),
                            "sha256": _sha(path.read_bytes())})
    if len(entries) > MAX_EVALUATOR_FILES:
        raise ScorerError("evaluator bundle exceeds its file or byte limit")
    return _sha(canonical_bytes(entries))


def verify_evaluator_constituents(root: str | Path, pin) -> None:
    """Verify each named protocol, rubric, validation, and holdout artifact byte-for-byte."""
    bundle = _safe_workspace(root)
    for digest_field, relative_name in EVALUATOR_CONSTITUENT_FILES.items():
        path = bundle / relative_name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EVALUATOR_FILE_BYTES:
            raise ScorerError(f"pinned evaluator constituent is missing or unsafe: {relative_name}")
        if digest_field == "output_anonymizer_sha256" and not os.access(path, os.X_OK):
            raise ScorerError("pinned output anonymizer is not executable")
        if _sha(path.read_bytes()) != getattr(pin, digest_field):
            raise ScorerError(f"pinned evaluator constituent digest differs: {relative_name}")


def verify_no_pinned_arm_identifiers(content: bytes, manifest: TrackManifest) -> None:
    """Reject literal arm/model/provider fingerprints in scorer-visible output bytes."""
    lowered = content.lower()
    identifiers = {"BANANLOOP_V02_FULL", "CODEX_DIRECT", "BananLoop v0.2 Full", "Codex Direct"}
    for arm in manifest.arms:
        identifiers.update((arm.arm, arm.provider, arm.model, arm.model_version,
                            arm.worker_bundle_sha256, arm.tool_policy_sha256,
                            arm.reasoning_settings_sha256))
        if arm.role_roster_sha256 is not None:
            identifiers.add(arm.role_roster_sha256)
    for identifier in identifiers:
        needle = identifier.strip().lower().encode("utf-8")
        if needle and needle in lowered:
            raise ScorerError("output artifact contains a pinned arm/provider/model identifier")


def scorer_runtime_fingerprint(
    *, sandbox_binary: str | Path, interpreter: str | Path | None = None,
    launcher: str | Path | None = None,
    system_roots: tuple[str | Path, ...] = ("/usr", "/bin", "/lib", "/lib64"),
) -> tuple[str, str]:
    """Pin every read-only host tree exposed to bwrap and its launcher binaries."""
    sandbox = Path(sandbox_binary).absolute()
    if sandbox.is_symlink() or not sandbox.is_file() or not os.access(sandbox, os.X_OK):
        raise ScorerError("bubblewrap must be a trusted regular executable")
    interpreter_path = Path(interpreter or sys.executable).absolute()
    launcher_path = Path(launcher or Path(__file__).with_name("worker_launcher.py")).absolute()
    for path in (interpreter_path, launcher_path):
        if path.is_symlink() or not path.is_file():
            raise ScorerError("scorer launcher runtime file is not a trusted regular file")
    roots = []
    entries = []
    total_bytes = 0
    total_files = 0
    for raw_root in system_roots:
        root = Path(raw_root).absolute()
        if not root.exists():
            continue
        roots.append(str(root))
        root_info = root.lstat()
        if stat.S_ISLNK(root_info.st_mode):
            entries.append({"root": str(root), "path": ".", "kind": "symlink",
                            "target": os.readlink(root), "mode": stat.S_IMODE(root_info.st_mode)})
            continue
        if not stat.S_ISDIR(root_info.st_mode):
            raise ScorerError("scorer runtime root is not a regular directory")
        entries.append({"root": str(root), "path": ".", "kind": "directory",
                        "mode": stat.S_IMODE(root_info.st_mode)})
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            base = Path(current)
            for name in tuple(dirs):
                path = base / name
                info = path.lstat()
                relative = path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    entries.append({"root": str(root), "path": relative, "kind": "symlink",
                                    "target": os.readlink(path), "mode": stat.S_IMODE(info.st_mode)})
                    dirs.remove(name)
                elif stat.S_ISDIR(info.st_mode):
                    entries.append({"root": str(root), "path": relative, "kind": "directory",
                                    "mode": stat.S_IMODE(info.st_mode)})
                else:
                    raise ScorerError("scorer runtime contains a special filesystem entry")
            for name in sorted(files):
                path = base / name
                info = path.lstat()
                relative = path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    entries.append({"root": str(root), "path": relative, "kind": "symlink",
                                    "target": os.readlink(path), "mode": stat.S_IMODE(info.st_mode)})
                elif stat.S_ISREG(info.st_mode):
                    total_bytes += info.st_size
                    total_files += 1
                    if total_bytes > MAX_RUNTIME_BYTES or total_files > MAX_RUNTIME_FILES:
                        raise ScorerError("scorer host runtime exceeds its fingerprinting limits")
                    entries.append({"root": str(root), "path": relative, "kind": "file",
                                    "size": info.st_size, "mode": stat.S_IMODE(info.st_mode),
                                    "sha256": _sha(path.read_bytes())})
                else:
                    raise ScorerError("scorer runtime contains a special filesystem entry")
    sandbox_sha = _sha(sandbox.read_bytes())
    runtime_sha = _sha(canonical_bytes({
        "system_roots": roots,
        "entries": entries,
        "sandbox_path": str(sandbox),
        "sandbox_sha256": sandbox_sha,
        "interpreter_path": str(interpreter_path),
        "interpreter_sha256": _sha(interpreter_path.read_bytes()),
        "interpreter_version": sys.version,
        "launcher_path": str(launcher_path),
        "launcher_sha256": _sha(launcher_path.read_bytes()),
    }))
    return runtime_sha, sandbox_sha


def _snapshot_evaluator(source: Path, destination: Path) -> None:
    """Copy only regular tree entries into a private temporary snapshot."""
    directory_modes = []
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        target_dir = destination / relative_dir
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in dirs:
            candidate = current_path / name
            if candidate.is_symlink() or not candidate.is_dir():
                raise ScorerError("evaluator source changed or contains a non-directory path")
            target_child = target_dir / name
            target_child.mkdir(mode=0o700, exist_ok=True)
            directory_modes.append((target_child, stat.S_IMODE(candidate.stat().st_mode)))
        for name in files:
            candidate = current_path / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ScorerError("evaluator source changed or contains a non-regular file")
            info = candidate.stat()
            if info.st_size > MAX_EVALUATOR_FILE_BYTES:
                raise ScorerError("evaluator source contains an oversized file")
            content = candidate.read_bytes()
            target = target_dir / name
            target.write_bytes(content)
            target.chmod(stat.S_IMODE(info.st_mode))
    for path, mode in reversed(directory_modes):
        path.chmod(mode)


class BubblewrapBlindScorer:
    """Run the pinned evaluator in a fresh networkless process with no host accounting."""

    def __init__(self, *, cgroup_root: str | Path, bwrap_binary: str | None = None):
        self.cgroup_root = Path(cgroup_root).absolute()
        self.bwrap_binary = bwrap_binary or shutil.which("bwrap") or ""

    def available(self) -> bool:
        return BubblewrapArmWorker(
            bwrap_binary=self.bwrap_binary, cgroup_root=self.cgroup_root,
        ).available()

    def run(self, manifest: TrackManifest, request: BlindScorerInput,
            *, evaluator_root: str | Path) -> bytes:
        pin = manifest.evaluator
        if (manifest.manifest_sha256 is None or request.manifest_sha256 != manifest.manifest_sha256
                or request.track != manifest.track
                or request.evaluator_pin_sha256 != _sha(canonical_bytes(pin))
                or request.evaluator_protocol_sha256 != pin.protocol_sha256
                or request.evaluator_bundle_sha256 != pin.bundle_sha256
                or request.rubric_sha256 != pin.rubric_sha256
                or request.validation_set_sha256 != pin.validation_set_sha256
                or request.holdout_set_sha256 != pin.holdout_set_sha256):
            raise ScorerError("blind scorer request differs from the frozen evaluator pins")
        if not self.available():
            raise ScorerError("bubblewrap or delegated cgroup v2 is unavailable; refusing scorer fallback")
        runtime_sha, sandbox_sha = scorer_runtime_fingerprint(sandbox_binary=self.bwrap_binary)
        if (runtime_sha != pin.scorer_runtime_sha256
                or sandbox_sha != pin.scorer_sandbox_sha256):
            raise ScorerError("scorer host runtime or bubblewrap differs from the frozen pin")
        try:
            root = _safe_workspace(evaluator_root)
            if evaluator_bundle_fingerprint(root) != pin.bundle_sha256:
                raise ScorerError("evaluator bundle bytes differ from the frozen bundle digest")
            verify_evaluator_constituents(root, pin)
            argv = _validate_command(pin.scorer_entrypoint)
            executable = argv[0]
            if not executable.startswith("/evaluator/"):
                raise ScorerError("scorer entrypoint must be inside the read-only evaluator bundle")
            relative = Path(executable.removeprefix("/evaluator/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ScorerError("scorer entrypoint escapes the evaluator bundle")
            executable_path = root.joinpath(*relative.parts)
            if (executable_path.is_symlink() or not executable_path.is_file()
                    or not os.access(executable_path, os.X_OK)):
                raise ScorerError("pinned scorer entrypoint is not an executable bundle file")
        except ArmWorkerError as exc:
            raise ScorerError("evaluator bundle is unsafe") from exc

        request_bytes = canonical_bytes(request)
        if len(request_bytes) > MAX_REQUEST_BYTES:
            raise ScorerError("blind scorer request exceeds its byte limit")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="rpdi14-blind-scorer-") as temp:
            root_tmp = Path(temp)
            evaluator_snapshot = root_tmp / "evaluator"
            _snapshot_evaluator(root, evaluator_snapshot)
            if evaluator_bundle_fingerprint(evaluator_snapshot) != pin.bundle_sha256:
                raise ScorerError("private evaluator snapshot differs from the frozen bundle digest")
            input_dir, output_dir = root_tmp / "input", root_tmp / "output"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            input_path = input_dir / "request.json"
            fd = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(request_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            command = [self.bwrap_binary, "--die-with-parent", "--new-session", "--unshare-user",
                       "--unshare-pid", "--unshare-net", "--clearenv"]
            for system in ("/usr", "/bin", "/lib", "/lib64"):
                if Path(system).exists():
                    command.extend(["--ro-bind", system, system])
            command.extend(["--dir", "/evaluator", "--dir", "/input", "--dir", "/output",
                            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                            "--ro-bind", str(evaluator_snapshot.resolve()), "/evaluator",
                            "--ro-bind", str(input_dir.resolve()), "/input",
                            "--bind", str(output_dir.resolve()), "/output",
                            "--chdir", "/evaluator", "--setenv", "PATH", "/usr/bin:/bin",
                            "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
                            "--setenv", "LC_ALL", "C.UTF-8",
                            "--setenv", "BANANLOOP_SCORER_INPUT", "/input/request.json",
                            "--setenv", "BANANLOOP_SCORER_RESULT", "/output/result.json",
                            *argv])
            stdout_path, stderr_path = root_tmp / "stdout", root_tmp / "stderr"
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                try:
                    with _WorkerCgroup(
                        self.cgroup_root, memory_bytes=pin.scorer_memory_bytes,
                        process_limit=pin.scorer_process_limit,
                    ) as cgroup_procs:
                        launcher = Path(__file__).with_name("worker_launcher.py")
                        launch_command = [sys.executable, str(launcher), cgroup_procs,
                                          str(pin.scorer_timeout_seconds),
                                          str(pin.scorer_memory_bytes), "--", *command]
                        process = subprocess.Popen(
                            launch_command, cwd=root, stdin=subprocess.DEVNULL,
                            stdout=stdout_file, stderr=stderr_file,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                            shell=False, start_new_session=True,
                        )
                        deadline = started + pin.scorer_timeout_seconds
                        while process.poll() is None:
                            _check_worker_output(output_dir)
                            if (stdout_path.stat().st_size > MAX_SCORER_LOG_BYTES
                                    or stderr_path.stat().st_size > MAX_SCORER_LOG_BYTES):
                                raise ScorerError("blind scorer log exceeded its size limit")
                            if time.monotonic() >= deadline:
                                raise subprocess.TimeoutExpired(launch_command, pin.scorer_timeout_seconds)
                            time.sleep(0.05)
                except subprocess.TimeoutExpired as exc:
                    _kill_process_group(process)
                    raise ScorerError("isolated scorer exceeded its frozen timeout") from exc
                except (ScorerError, ArmWorkerError):
                    if "process" in locals() and process.poll() is None:
                        _kill_process_group(process)
                    raise
                except OSError as exc:
                    raise ScorerError("cannot start isolated blind scorer") from exc
            if process.returncode != 0:
                raise ScorerError("isolated blind scorer exited unsuccessfully")
            _check_worker_output(output_dir)
            result_path = output_dir / "result.json"
            if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size > MAX_REQUEST_BYTES:
                raise ScorerError("isolated scorer result is missing or oversized")
            raw_result = result_path.read_bytes()
            validated = validate_blind_scorer_output(raw_result, request=request)
            final_runtime_sha, final_sandbox_sha = scorer_runtime_fingerprint(
                sandbox_binary=self.bwrap_binary,
            )
            if (final_runtime_sha != runtime_sha or final_sandbox_sha != sandbox_sha):
                raise ScorerError("scorer host runtime changed during the isolated evaluator run")
            return canonical_bytes(validated)


def scorer_output_projection(output: BlindScorerOutput | dict) -> dict:
    value = output.model_dump(mode="json") if isinstance(output, BlindScorerOutput) else dict(output)
    return {
        "schema_version": value["schema_version"],
        "track": value["track"],
        "manifest_sha256": value["manifest_sha256"],
        "evaluator_pin_sha256": value["evaluator_pin_sha256"],
        "pairs": [
            {
                "pair_id_sha256": pair["pair_id_sha256"],
                "status": pair["status"],
                "arm_a_score": pair.get("arm_a_score"),
                "arm_b_score": pair.get("arm_b_score"),
                "arm_a_output_sha256": pair.get("arm_a_output_sha256"),
                "arm_b_output_sha256": pair.get("arm_b_output_sha256"),
            }
            for pair in value["pairs"]
        ],
    }


def scorer_output_digest(output: BlindScorerOutput | dict) -> str:
    return _sha(canonical_bytes(scorer_output_projection(output)))


def build_blind_scorer_input(
    manifest: TrackManifest,
    package: BlindedScorePackage,
    *,
    evidence_root: str,
    trust_policy: TrustPolicy,
) -> BlindScorerInput:
    """Read only anonymous output artifacts and frozen evaluator pins for one track."""
    if manifest.manifest_sha256 is None:
        raise ScorerError("manifest must be frozen before scorer input is prepared")
    if len(manifest.task_pins) > MAX_SCORER_PAIRS:
        raise ScorerError("frozen track exceeds the blind scorer pair-count limit")
    evaluator_sha = _sha(canonical_bytes(manifest.evaluator))
    if (package.manifest_sha256 != manifest.manifest_sha256 or package.track != manifest.track
            or package.evaluator_pin_sha256 != evaluator_sha):
        raise ScorerError("score package does not match the frozen track and evaluator")
    expected_pairs = {pin.blinded_pair_id_sha256 for pin in manifest.task_pins}
    if {pair.pair_id_sha256 for pair in package.pairs} != expected_pairs:
        raise ScorerError("score package pair set differs from the frozen manifest")
    input_pairs = []
    total_output_bytes = 0
    for pair in package.pairs:
        outputs: dict[str, bytes | None] = {"arm_a": None, "arm_b": None}
        if pair.status == "SCORED":
            for label in ("arm_a", "arm_b"):
                reference = getattr(pair, f"{label}_output_ref")
                digest = getattr(pair, f"{label}_output_sha256")
                raw_reference = getattr(pair, f"{label}_raw_output_ref")
                raw_digest = getattr(pair, f"{label}_raw_output_sha256")
                attestation = getattr(pair, f"{label}_anonymization_attestation")
                if any(value is None for value in (reference, digest, raw_reference,
                                                   raw_digest, attestation)):
                    raise ScorerError("scored pair lacks raw/sanitized output evidence")
                try:
                    verify_output_anonymization(
                        root=evidence_root, policy=trust_policy, reference=attestation,
                        pair_id_sha256=pair.pair_id_sha256,
                        arm_label="A" if label == "arm_a" else "B",
                        raw_reference=raw_reference, raw_sha256=raw_digest,
                        sanitized_reference=reference, sanitized_sha256=digest,
                        anonymizer_sha256=manifest.evaluator.output_anonymizer_sha256,
                    )
                except EvidenceError as exc:
                    raise ScorerError(f"output anonymization evidence is invalid: {exc}") from exc
                try:
                    outputs[label] = read_content_artifact(
                        root=evidence_root, reference=reference, expected_sha256=digest,
                        max_bytes=MAX_OUTPUT_BYTES,
                    )
                    total_output_bytes += len(outputs[label])
                    if total_output_bytes > MAX_TOTAL_OUTPUT_BYTES:
                        raise ScorerError("anonymous outputs exceed the aggregate scorer input limit")
                except EvidenceError as exc:
                    raise ScorerError(f"scorer output artifact failed verification: {exc}") from exc
                verify_no_pinned_arm_identifiers(outputs[label], manifest)
        input_pairs.append(ScorerInputPair(
            pair_id_sha256=pair.pair_id_sha256, status=pair.status,
            arm_a_output_sha256=_sha(outputs["arm_a"]) if outputs["arm_a"] is not None else None,
            arm_a_output_base64=(base64.b64encode(outputs["arm_a"]).decode("ascii")
                                 if outputs["arm_a"] is not None else None),
            arm_b_output_sha256=_sha(outputs["arm_b"]) if outputs["arm_b"] is not None else None,
            arm_b_output_base64=(base64.b64encode(outputs["arm_b"]).decode("ascii")
                                 if outputs["arm_b"] is not None else None),
        ))
    result = BlindScorerInput(
        track=manifest.track, manifest_sha256=manifest.manifest_sha256,
        evaluator_pin_sha256=evaluator_sha,
        evaluator_protocol_sha256=manifest.evaluator.protocol_sha256,
        evaluator_bundle_sha256=manifest.evaluator.bundle_sha256,
        rubric_sha256=manifest.evaluator.rubric_sha256,
        validation_set_sha256=manifest.evaluator.validation_set_sha256,
        holdout_set_sha256=manifest.evaluator.holdout_set_sha256,
        pairs=tuple(input_pairs),
    )
    if len(canonical_bytes(result)) > MAX_REQUEST_BYTES:
        raise ScorerError("blind scorer request exceeds its byte limit")
    return result


def validate_blind_scorer_output(
    raw: bytes,
    *,
    request: BlindScorerInput,
) -> BlindScorerOutput:
    """Reject malformed, changed, partial, or extra scorer rows before joining accounting."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_REQUEST_BYTES:
        raise ScorerError("scorer output is empty or exceeds its byte limit")
    try:
        value = json.loads(
            raw, object_pairs_hook=_unique_object, parse_float=_finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite scorer value")),
        )
        if isinstance(value, dict) and isinstance(value.get("pairs"), list):
            value["pairs"] = tuple(value["pairs"])
        output = BlindScorerOutput.model_validate(value, strict=True)
    except Exception as exc:
        raise ScorerError(f"scorer output is malformed: {type(exc).__name__}") from exc
    if canonical_bytes(output) != raw:
        raise ScorerError("scorer output must use canonical JSON")
    if (output.track != request.track or output.manifest_sha256 != request.manifest_sha256
            or output.evaluator_pin_sha256 != request.evaluator_pin_sha256):
        raise ScorerError("scorer output is for different frozen inputs")
    if len(output.pairs) != len(request.pairs):
        raise ScorerError("scorer output must preserve every blind pair")
    for source, scored in zip(request.pairs, output.pairs, strict=True):
        if (source.pair_id_sha256 != scored.pair_id_sha256 or source.status != scored.status
                or source.arm_a_output_sha256 != scored.arm_a_output_sha256
                or source.arm_b_output_sha256 != scored.arm_b_output_sha256):
            raise ScorerError("scorer output changed pair identity, status, or output binding")
        if source.status != "SCORED" and any(
                getattr(scored, field) is not None for field in ("arm_a_score", "arm_b_score")):
            raise ScorerError("scorer returned scores for an unscored arm pair")
    return output


def apply_blind_scorer_output(
    package: BlindedScorePackage,
    output: BlindScorerOutput,
) -> BlindedScorePackage:
    """Join blind scores to host-held accounting only after scorer output is frozen."""
    if (package.track != output.track or package.manifest_sha256 != output.manifest_sha256
            or package.evaluator_pin_sha256 != output.evaluator_pin_sha256
            or len(package.pairs) != len(output.pairs)):
        raise ScorerError("scorer output and host package do not share a frozen identity")
    score_rows = {item.pair_id_sha256: item for item in output.pairs}
    if set(score_rows) != {item.pair_id_sha256 for item in package.pairs}:
        raise ScorerError("scorer output pair set differs from the host package")
    merged = []
    for pair in package.pairs:
        row = score_rows[pair.pair_id_sha256]
        if (pair.status != row.status or pair.arm_a_output_sha256 != row.arm_a_output_sha256
                or pair.arm_b_output_sha256 != row.arm_b_output_sha256):
            raise ScorerError("scorer output changed host-pinned outcome or output bytes")
        payload = pair.model_dump(mode="python")
        payload.update(arm_a_score=row.arm_a_score, arm_b_score=row.arm_b_score)
        payload.pop("pair_result_sha256")
        merged.append({**payload, "pair_result_sha256": pair_score_digest(payload)})
    package_payload = package.model_dump(mode="python")
    package_payload["pairs"] = tuple(merged)
    package_payload["score_payload_sha256"] = scorer_output_digest(output)
    package_payload.pop("package_sha256")
    from .report import score_package_digest
    package_payload["package_sha256"] = score_package_digest(package_payload)
    try:
        return BlindedScorePackage.model_validate(package_payload, strict=True)
    except Exception as exc:
        raise ScorerError("cannot merge scorer output with host accounting package") from exc
