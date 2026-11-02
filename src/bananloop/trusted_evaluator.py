"""Core-owned, fail-closed evaluator execution for v2 protocols.

The runner accepts evaluator assets from a trusted resolver, not from a worker
path. It exposes one selected tier at a time and returns only content digests;
raw evaluator output and process logs never enter the public receipt.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import EvaluationResult, ProtocolManifest
from .evaluator_verification import EvaluatorProtocolV1

try:  # resource is POSIX-only; the execution backend itself is Linux-only.
    import resource as _resource
except ImportError:  # pragma: no cover - Windows
    _resource = None


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MAX_BUNDLE_BYTES = 50 * 1024 * 1024
_MAX_BUNDLE_FILES = 10_000
_MAX_BUNDLE_FILE_BYTES = 25 * 1024 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 100 * 1024 * 1024
_MAX_RESULT_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_BYTES = 25 * 1024 * 1024
_MAX_OUTPUT_FILES = 10_000
_MAX_EVALUATOR_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh", "cmd", "cmd.exe", "powershell", "pwsh"}
_PROTECTED_SEGMENTS = {".git", ".bananloop_eval", "evaluator", "validation", "holdout",
                       "secret", "secrets", "credentials"}


class TrustedEvaluatorError(RuntimeError):
    """Trusted evaluator input, execution, or result failed closed."""


class EvaluatorBudgetProofV1(BaseModel):
    """Core-validated durable evaluator queue settlement used by a run receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_id: str = Field(min_length=1, max_length=120)
    fence: int = Field(ge=1)
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource: Literal["evaluator_calls"] = "evaluator_calls"
    actual_amount: int = Field(ge=0)
    actual_usage: dict[str, int]

    @model_validator(mode="after")
    def usage_matches_call(self) -> "EvaluatorBudgetProofV1":
        if (self.actual_amount != 1 or self.actual_usage.get("evaluator_calls") != 1
                or set(self.actual_usage) - {"evaluator_calls", "evaluation_units"}
                or any(value < 0 for value in self.actual_usage.values())):
            raise ValueError("budget proof must settle exactly one evaluator call with valid usage")
        return self


class EvaluatorRunReceiptV1(BaseModel):
    """Content-addressed public receipt; contains no score, case ID, or log."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_id: str = Field(min_length=1, max_length=120)
    protocol_version: int = Field(ge=1)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=40, max_length=64)
    candidate_snapshot_id: str = Field(min_length=40, max_length=64)
    tier: Literal["dev", "validation", "holdout"]
    evaluator_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tier_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1, le=10000)
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    repeatability_runs: int = Field(ge=2, le=10)
    budget_proofs: tuple[EvaluatorBudgetProofV1, ...] = Field(min_length=2, max_length=10)
    output_artifact_digests: tuple[str, ...] = ()

    @model_validator(mode="after")
    def receipt_digest_matches(self) -> "EvaluatorRunReceiptV1":
        if not _GIT_OID.fullmatch(self.source_snapshot_id) or not _GIT_OID.fullmatch(self.candidate_snapshot_id):
            raise ValueError("evaluator run snapshots must be full immutable Git object IDs")
        if tuple(sorted(set(self.output_artifact_digests))) != self.output_artifact_digests:
            raise ValueError("output artifact digests must be sorted and unique")
        if any(not _SHA256.fullmatch(item) for item in self.output_artifact_digests):
            raise ValueError("output artifact digests must be SHA-256 values")
        if (len(self.budget_proofs) != self.repeatability_runs
                or len({item.job_id for item in self.budget_proofs}) != self.repeatability_runs
                or any(item.actual_amount != 1 or item.campaign_id != self.campaign_id
                       or item.spec_hash != self.spec_hash for item in self.budget_proofs)):
            raise ValueError("receipt requires one distinct settled evaluator-call proof per repeatability run")
        body = self.model_dump(mode="json")
        body.pop("receipt_digest")
        if self.receipt_digest != _digest(body):
            raise ValueError("evaluator run receipt digest does not match its content")
        return self

    def content_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("receipt_digest", None)
        return payload

    @classmethod
    def from_completed_runs(
        cls,
        executions: tuple["EvaluatorExecutionV1", ...],
        budget_proofs: tuple[EvaluatorBudgetProofV1, ...],
    ) -> "EvaluatorRunReceiptV1":
        """Mint the final receipt after Core has settled each queue job.

        Queue completion receipt digests depend on each execution result digest,
        so this final record must be formed only after all corresponding
        ``queue.complete`` calls have committed.
        """
        if not executions:
            raise TrustedEvaluatorError("final evaluator receipt requires completed execution evidence")
        first = executions[0]
        if len(executions) != first.repeatability_runs or len(budget_proofs) != len(executions):
            raise TrustedEvaluatorError("repeatability executions and settled queue proofs are incomplete")
        if (len({item.result_digest for item in executions}) != 1
                or len({item.output_artifact_digests for item in executions}) != 1):
            raise TrustedEvaluatorError("evaluator result or artifacts differ across repeatability runs")
        if any(item.scope_identity() != first.scope_identity() for item in executions):
            raise TrustedEvaluatorError("repeatability executions differ in protocol or campaign scope")
        if (len({item.job_id for item in budget_proofs}) != len(executions)
                or any(item.actual_amount != 1 or item.campaign_id != first.campaign_id
                       or item.spec_hash != first.spec_hash for item in budget_proofs)):
            raise TrustedEvaluatorError("queue settlements do not match every repeatability execution")
        payload = {
            "campaign_id": first.campaign_id, "spec_hash": first.spec_hash,
            "approval_digest": first.approval_digest, "protocol_id": first.protocol_id,
            "protocol_version": first.protocol_version, "protocol_digest": first.protocol_digest,
            "source_snapshot_id": first.source_snapshot_id,
            "candidate_snapshot_id": first.candidate_snapshot_id, "tier": first.tier,
            "evaluator_bundle_sha256": first.evaluator_bundle_sha256,
            "tier_manifest_sha256": first.tier_manifest_sha256,
            "case_count": first.case_count,
            "result_digest": first.result_digest, "repeatability_runs": len(executions),
            "budget_proofs": tuple(item.model_dump(mode="json") for item in budget_proofs),
            "output_artifact_digests": first.output_artifact_digests,
        }
        return cls(receipt_digest=_digest(payload), **payload)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_protocol_digest(protocol: EvaluatorProtocolV1) -> str:
    """Identity of the measurement contract before binding tier manifests.

    ProtocolManifest embeds this value; tier manifest digests are then included
    in ``content_digest``. Excluding only ``tiers`` avoids a recursive hash
    while keeping the manifest bound to the evaluator, metric and semantics.
    """
    payload = protocol.model_dump(mode="json")
    payload.pop("tiers", None)
    return _digest(payload)


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise TrustedEvaluatorError("asset path is not canonical")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise TrustedEvaluatorError("asset path must be relative")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise TrustedEvaluatorError("asset path contains a traversal component")
    normalized = posix.as_posix()
    if normalized != value or len(normalized) > 2048:
        raise TrustedEvaluatorError("asset path is not normalized or exceeds the limit")
    return normalized


def _manifest_from_bytes(data: bytes, *, expected_sha256: str, expected_cases: int) -> ProtocolManifest:
    if len(data) > _MAX_MANIFEST_BYTES:
        raise TrustedEvaluatorError("tier manifest exceeds the size limit")
    try:
        manifest = ProtocolManifest.model_validate_json(data)
    except Exception:
        raise TrustedEvaluatorError("tier manifest does not satisfy the frozen manifest schema") from None
    # Existing readiness receipts hash the canonical model dump with the
    # default JSON ASCII escaping. Match that established identity convention.
    canonical = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    if _sha256(canonical) != expected_sha256:
        raise TrustedEvaluatorError("tier manifest digest is not the canonical frozen manifest digest")
    if len(manifest.cases) != expected_cases:
        raise TrustedEvaluatorError("tier manifest case count differs from the approved protocol")
    return manifest


def _extract_bundle(data: bytes, destination: Path, *, expected_sha256: str) -> None:
    if len(data) > _MAX_BUNDLE_BYTES or _sha256(data) != expected_sha256:
        raise TrustedEvaluatorError("evaluator bundle bytes do not match the approved digest")
    destination.mkdir(mode=0o700)
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(data))
        infos = archive.infolist()
        if len(infos) > _MAX_BUNDLE_FILES:
            raise TrustedEvaluatorError("evaluator bundle contains too many entries")
        seen: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename.rstrip("/") if info.is_dir() else info.filename
            if not name:
                continue
            relative = _safe_relative(name)
            if relative in seen:
                raise TrustedEvaluatorError("evaluator bundle contains duplicate paths")
            seen.add(relative)
            if info.flag_bits & 0x1:
                raise TrustedEvaluatorError("encrypted evaluator bundle entries are unsupported")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise TrustedEvaluatorError("evaluator bundle contains a symlink or special file")
            if info.file_size > _MAX_BUNDLE_FILE_BYTES:
                raise TrustedEvaluatorError("evaluator bundle entry exceeds the per-file limit")
            total += info.file_size
            if total > _MAX_BUNDLE_BYTES:
                raise TrustedEvaluatorError("evaluator bundle exceeds the expanded-size limit")
            target = destination.joinpath(*PurePosixPath(relative).parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Read at most one byte over the declared size to detect malformed
            # or intentionally inflated zip entries before writing them.
            with archive.open(info) as source, target.open("xb") as output:
                remaining = info.file_size
                while remaining:
                    block = source.read(min(64 * 1024, remaining))
                    if not block:
                        raise TrustedEvaluatorError("evaluator bundle entry was truncated")
                    output.write(block)
                    remaining -= len(block)
                if source.read(1):
                    raise TrustedEvaluatorError("evaluator bundle entry exceeds its declared size")
            target.chmod(0o500 if mode & 0o111 else 0o400)
    except TrustedEvaluatorError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        raise TrustedEvaluatorError("evaluator bundle archive is malformed") from None


def _write_snapshot(files: Mapping[str, bytes], destination: Path) -> None:
    destination.mkdir(mode=0o700)
    total = 0
    seen: set[str] = set()
    for raw_path, contents in sorted(files.items()):
        path = _safe_relative(raw_path)
        if any(part.lower() in _PROTECTED_SEGMENTS for part in PurePosixPath(path).parts):
            raise TrustedEvaluatorError("candidate materialization includes a protected evaluator/data path")
        if path in seen or not isinstance(contents, bytes):
            raise TrustedEvaluatorError("candidate snapshot file map is ambiguous or malformed")
        seen.add(path)
        total += len(contents)
        if total > _MAX_CANDIDATE_BYTES:
            raise TrustedEvaluatorError("candidate snapshot exceeds the materialization limit")
        target = destination.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(contents)
        target.chmod(0o400)
    for directory, _, _ in os.walk(destination, topdown=False):
        Path(directory).chmod(0o500)


@dataclass(frozen=True, slots=True)
class EvaluatorArtifactBytesV1:
    """Verified bytes returned only to trusted Core for content-addressed storage."""

    sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class EvaluatorExecutionV1:
    """Private, unsettled Core result; contains no public score or case bytes.

    ``artifacts`` contains evaluator output bytes for Core's content-addressed
    artifact store. Keep this object out of worker contexts, event payloads and
    public projections.
    """

    campaign_id: str
    spec_hash: str
    approval_digest: str
    protocol_id: str
    protocol_version: int
    protocol_digest: str
    source_snapshot_id: str
    candidate_snapshot_id: str
    tier: str
    evaluator_bundle_sha256: str
    tier_manifest_sha256: str
    case_count: int
    result_digest: str
    repeatability_runs: int
    output_artifact_digests: tuple[str, ...]
    artifacts: tuple[EvaluatorArtifactBytesV1, ...] = field(default=(), repr=False)

    def scope_identity(self) -> tuple[Any, ...]:
        return (
            self.campaign_id, self.spec_hash, self.approval_digest, self.protocol_id,
            self.protocol_version, self.protocol_digest, self.source_snapshot_id,
            self.candidate_snapshot_id, self.tier, self.evaluator_bundle_sha256,
            self.tier_manifest_sha256, self.case_count, self.repeatability_runs,
        )


def _artifact_outputs(result: EvaluationResult, output_root: Path,
                      candidate_snapshot_id: str) -> tuple[tuple[str, ...], tuple[EvaluatorArtifactBytesV1, ...]]:
    if result.artifacts:
        raise TrustedEvaluatorError("legacy unverified artifact references are not accepted")
    declared: dict[str, str] = {}
    known_cases = {(case.case_id, case.repetition) for case in result.cases}
    for record in result.artifact_manifest:
        path = _safe_relative(record.path)
        if path in declared:
            raise TrustedEvaluatorError("evaluator result declares an output path more than once")
        if (record.case_id, record.repetition) not in known_cases:
            raise TrustedEvaluatorError("evaluator artifact is not bound to a validated case")
        if record.candidate_snapshot is not None and record.candidate_snapshot != candidate_snapshot_id:
            raise TrustedEvaluatorError("evaluator artifact names a different candidate snapshot")
        declared[path] = record.sha256
    actual: dict[str, str] = {}
    artifacts_by_digest: dict[str, bytes] = {}
    total = 0
    count = 0
    for path in output_root.rglob("*"):
        if path.is_symlink():
            raise TrustedEvaluatorError("evaluator output contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise TrustedEvaluatorError("evaluator output contains a special file")
        rel = _safe_relative(path.relative_to(output_root).as_posix())
        size = path.stat().st_size
        total += size
        count += 1
        if count > _MAX_OUTPUT_FILES or total > _MAX_OUTPUT_BYTES:
            raise TrustedEvaluatorError("evaluator output exceeds the artifact limit")
        content = path.read_bytes()
        digest = _sha256(content)
        actual[rel] = digest
        artifacts_by_digest[digest] = content
    if declared != actual:
        raise TrustedEvaluatorError("evaluator output artifacts do not match their declared digests")
    digests = tuple(sorted(set(actual.values())))
    artifacts = tuple(EvaluatorArtifactBytesV1(digest, artifacts_by_digest[digest]) for digest in sorted(artifacts_by_digest))
    return digests, artifacts


def _check_live_output_limits(output_root: Path) -> None:
    """Stop runaway output while the evaluator is still running.

    RLIMIT_FSIZE limits each individual file. This second guard bounds the
    combined directory before post-run artifact validation, rather than
    discovering an oversized output tree after it has filled the host disk.
    """
    total = 0
    count = 0
    for directory, dirnames, filenames in os.walk(output_root, followlinks=False):
        for name in dirnames:
            if (Path(directory) / name).is_symlink():
                raise TrustedEvaluatorError("evaluator output contains a symlink")
        for name in filenames:
            path = Path(directory) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise TrustedEvaluatorError("evaluator output contains a symlink or special file")
            total += info.st_size
            count += 1
            if count > _MAX_OUTPUT_FILES or total > _MAX_OUTPUT_BYTES:
                raise TrustedEvaluatorError("evaluator output exceeded its live artifact limit")


def _limit_evaluator_process(timeout_seconds: int) -> None:
    """Apply inherited Linux resource ceilings before bubblewrap starts."""
    if _resource is None:
        return
    try:
        _resource.setrlimit(_resource.RLIMIT_FSIZE, (_MAX_OUTPUT_BYTES, _MAX_OUTPUT_BYTES))
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (256, 256))
        cpu_limit = max(1, int(timeout_seconds) + 2)
        _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        if hasattr(_resource, "RLIMIT_NPROC"):
            _resource.setrlimit(_resource.RLIMIT_NPROC, (128, 128))
        # Evaluator bundles are executable code and can otherwise exhaust the
        # host before their wall-clock timeout fires. Linux provides RLIMIT_AS;
        # fail closed if the hard memory ceiling cannot be applied.
        if not hasattr(_resource, "RLIMIT_AS"):
            os._exit(125)  # pragma: no cover - supported Linux hosts expose this
        _resource.setrlimit(
            _resource.RLIMIT_AS,
            (_MAX_EVALUATOR_MEMORY_BYTES, _MAX_EVALUATOR_MEMORY_BYTES),
        )
    except (OSError, ValueError):
        # A failed hard ceiling is not safe to ignore.
        os._exit(125)


class TrustedEvaluatorRunner:
    """Run one frozen evaluator tier in a Linux bubblewrap sandbox.

    The resolver feeding this API must already validate the approved protocol
    receipt, candidate commit tree, campaign spec and budget queue settlement.
    This runner independently verifies every supplied byte digest and fails on
    hosts where Linux namespaces/bubblewrap are unavailable.
    """

    def __init__(self, *, bwrap_binary: str | None = None):
        self.bwrap_binary = bwrap_binary or shutil.which("bwrap") or ""

    def run(
        self,
        *,
        protocol: EvaluatorProtocolV1,
        approval_digest: str,
        campaign_id: str,
        spec_hash: str,
        candidate_snapshot_id: str,
        candidate_files: Mapping[str, bytes],
        evaluator_bundle: bytes,
        tier: Literal["dev", "validation", "holdout"],
        tier_manifest: bytes,
        case_inputs: Mapping[tuple[str, int], bytes],
    ) -> EvaluatorExecutionV1:
        if not _SHA256.fullmatch(approval_digest) or not _SHA256.fullmatch(spec_hash):
            raise TrustedEvaluatorError("approved protocol and spec digests are required")
        if not _GIT_OID.fullmatch(candidate_snapshot_id):
            raise TrustedEvaluatorError("candidate snapshot must be a full immutable Git object ID")
        tier_manifest_ref = next((item for item in protocol.tiers if item.tier == tier), None)
        if tier_manifest_ref is None:
            raise TrustedEvaluatorError("requested evaluator tier is not part of the frozen protocol")
        manifest = _manifest_from_bytes(
            tier_manifest, expected_sha256=tier_manifest_ref.manifest_sha256,
            expected_cases=tier_manifest_ref.case_count,
        )
        if manifest.protocol_sha256 != _case_protocol_digest(protocol):
            raise TrustedEvaluatorError("tier manifest is not bound to the approved evaluator protocol")
        expected_cases = {(case.case_id, case.repetition): case for case in manifest.cases}
        if set(case_inputs) != set(expected_cases):
            raise TrustedEvaluatorError("resolved tier inputs do not exactly match frozen case identities")
        for identity, payload_bytes in case_inputs.items():
            if not isinstance(payload_bytes, bytes) or _sha256(payload_bytes) != expected_cases[identity].input_sha256:
                raise TrustedEvaluatorError("resolved tier input bytes do not match the frozen case digest")
        if not self._sandbox_available():
            raise TrustedEvaluatorError("bubblewrap namespace isolation is unavailable; refusing evaluator fallback")

        with tempfile.TemporaryDirectory(prefix="bananloop-evaluator-") as temporary:
            root = Path(temporary)
            readonly = root / "readonly"
            readonly.mkdir(mode=0o700)
            evaluator_root = readonly / "evaluator"
            candidate_root = readonly / "candidate"
            tier_root = readonly / "tier"
            _extract_bundle(evaluator_bundle, evaluator_root,
                            expected_sha256=protocol.evaluator_bundle_sha256)
            argv = self._entrypoint(protocol.entrypoint)
            self._require_bundle_entrypoint(argv, evaluator_root)
            _write_snapshot(candidate_files, candidate_root)
            tier_root.mkdir(mode=0o700)
            (tier_root / "manifest.json").write_bytes(tier_manifest)
            (tier_root / "manifest.json").chmod(0o400)
            private_map = []
            input_files: dict[str, bytes] = {}
            for index, case in enumerate(manifest.cases):
                relative = f"case-{index:05d}.json"
                input_files[relative] = case_inputs[(case.case_id, case.repetition)]
                private_map.append({
                    "case_id": case.case_id,
                    "repetition": case.repetition,
                    "input_sha256": case.input_sha256,
                    "path": f"inputs/{relative}",
                })
            _write_snapshot(input_files, tier_root / "inputs")
            map_path = tier_root / "input-map.json"
            map_path.write_text(_canonical(private_map), encoding="utf-8")
            map_path.chmod(0o400)
            for directory, _, _ in os.walk(tier_root, topdown=False):
                Path(directory).chmod(0o500)
            output_root = root / "output"
            output_root.mkdir(mode=0o700)
            try:
                stdout = self._execute_isolated(
                    argv, readonly=readonly, output=output_root,
                    candidate_snapshot_id=candidate_snapshot_id,
                    tier=tier, timeout=protocol.timeout_seconds,
                )
            except TrustedEvaluatorError:
                raise
            except Exception:
                raise TrustedEvaluatorError("trusted evaluator process failed") from None
            if len(stdout) > _MAX_RESULT_BYTES:
                raise TrustedEvaluatorError("evaluator result exceeds the public parse limit")
            try:
                raw_result = json.loads(stdout)
                result = EvaluationResult.model_validate(raw_result)
            except Exception:
                raise TrustedEvaluatorError("evaluator result is invalid; details were withheld") from None
            self._validate_result(result, manifest, protocol)
            artifact_digests, verified_artifacts = _artifact_outputs(
                result, output_root, candidate_snapshot_id,
            )
            result_digest = _sha256(_canonical(result.model_dump(mode="json")).encode("utf-8"))

        return EvaluatorExecutionV1(
            campaign_id=campaign_id,
            spec_hash=spec_hash,
            approval_digest=approval_digest,
            protocol_id=protocol.protocol_id,
            protocol_version=protocol.version,
            protocol_digest=protocol.content_digest(),
            source_snapshot_id=protocol.source_snapshot_id,
            candidate_snapshot_id=candidate_snapshot_id,
            tier=tier,
            evaluator_bundle_sha256=protocol.evaluator_bundle_sha256,
            tier_manifest_sha256=tier_manifest_ref.manifest_sha256,
            case_count=len(manifest.cases),
            result_digest=result_digest,
            repeatability_runs=protocol.repeatability_runs,
            output_artifact_digests=artifact_digests,
            artifacts=verified_artifacts,
        )

    @staticmethod
    def _entrypoint(argv: tuple[str, ...]) -> list[str]:
        if not argv:
            raise TrustedEvaluatorError("frozen evaluator entrypoint is empty")
        basenames = {PurePosixPath(token.replace("\\", "/")).name.lower()
                     for token in argv if token and not token.startswith("-")}
        if basenames & _SHELLS or any(token in {"-c", "-e", "--eval"} for token in argv):
            raise TrustedEvaluatorError("evaluator entrypoint cannot invoke a shell or inline code")
        replacements = {
            "{evaluator}": "/workspace/evaluator",
            "{candidate}": "/workspace/candidate",
            "{tier}": "/workspace/tier",
            "{output}": "/output",
        }
        resolved = []
        for token in argv:
            for placeholder, path in replacements.items():
                token = token.replace(placeholder, path)
            resolved.append(token)
        return resolved

    @staticmethod
    def _require_bundle_entrypoint(argv: list[str], evaluator_root: Path) -> None:
        for token in argv:
            prefix = "/workspace/evaluator/"
            if token.startswith(prefix):
                relative = _safe_relative(token[len(prefix):])
                target = evaluator_root.joinpath(*PurePosixPath(relative).parts)
                if target.is_file():
                    return
        raise TrustedEvaluatorError("approved evaluator entrypoint does not execute a file from its pinned bundle")

    def _sandbox_available(self) -> bool:
        if os.name != "posix" or not self.bwrap_binary or not Path(self.bwrap_binary).is_file():
            return False
        # Runtime probing is performed in a separate namespace. No local
        # execution fallback is allowed when unshare is denied by the host.
        try:
            with tempfile.TemporaryDirectory(prefix="bananloop-bwrap-probe-") as directory:
                command = [self.bwrap_binary, "--die-with-parent", "--new-session", "--unshare-user",
                           "--unshare-pid", "--unshare-net", "--clearenv", "--ro-bind", "/usr", "/usr"]
                for system in ("/bin", "/lib", "/lib64"):
                    if Path(system).exists():
                        command.extend(["--ro-bind", system, system])
                command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                                "--chdir", "/tmp", "/usr/bin/true"])
                completed = subprocess.run(command, cwd=directory, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
                                            shell=False, timeout=5, check=False)
                return completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _execute_isolated(self, argv: list[str], *, readonly: Path, output: Path,
                          candidate_snapshot_id: str, tier: str, timeout: int) -> str:
        command = [self.bwrap_binary, "--die-with-parent", "--new-session", "--unshare-user",
                   "--unshare-pid", "--unshare-net", "--clearenv"]
        for system in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(system).exists():
                command.extend(["--ro-bind", system, system])
        command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--ro-bind", str(readonly.resolve()), "/workspace",
                        "--bind", str(output.resolve()), "/output",
                        "--chdir", "/workspace/candidate",
                        "--setenv", "PATH", "/usr/bin:/bin",
                        "--setenv", "HOME", "/tmp",
                        "--setenv", "LANG", "C.UTF-8",
                        "--setenv", "LC_ALL", "C.UTF-8",
                        "--setenv", "PYTHONPATH", "/workspace/evaluator",
                        "--setenv", "BANANLOOP_CANDIDATE", "/workspace/candidate",
                        "--setenv", "BANANLOOP_EVALUATOR", "/workspace/evaluator",
                        "--setenv", "BANANLOOP_TIER", "/workspace/tier",
                        "--setenv", "BANANLOOP_TIER_NAME", tier,
                        "--setenv", "BANANLOOP_CANDIDATE_SNAPSHOT", candidate_snapshot_id,
                        "--setenv", "BANANLOOP_OUTPUT", "/output", *argv])
        with tempfile.TemporaryDirectory(prefix="bananloop-evaluator-logs-") as log_dir:
            stdout_path, stderr_path = Path(log_dir) / "stdout", Path(log_dir) / "stderr"
            try:
                with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                    proc = subprocess.Popen(command, cwd=readonly, stdin=subprocess.DEVNULL,
                                            stdout=stdout_file, stderr=stderr_file,
                                            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp",
                                                 "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                                            shell=False, start_new_session=True,
                                            preexec_fn=lambda: _limit_evaluator_process(timeout))
                    try:
                        deadline = time.monotonic() + timeout
                        while proc.poll() is None:
                            _check_live_output_limits(output)
                            if time.monotonic() >= deadline:
                                raise subprocess.TimeoutExpired(command, timeout)
                            time.sleep(0.05)
                        return_code = proc.returncode
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, 9)
                        except OSError:
                            proc.kill()
                        proc.wait(timeout=5)
                        raise TrustedEvaluatorError("trusted evaluator timed out") from None
                    except TrustedEvaluatorError:
                        try:
                            os.killpg(proc.pid, 9)
                        except OSError:
                            proc.kill()
                        proc.wait(timeout=5)
                        raise
                if stdout_path.stat().st_size > _MAX_RESULT_BYTES:
                    raise TrustedEvaluatorError("evaluator result exceeds the public parse limit")
                if stderr_path.stat().st_size > _MAX_OUTPUT_BYTES:
                    raise TrustedEvaluatorError("evaluator log output exceeded the sandbox limit")
                if return_code != 0:
                    raise TrustedEvaluatorError("trusted evaluator returned a nonzero exit status")
                return stdout_path.read_text(encoding="utf-8")
            except TrustedEvaluatorError:
                raise
            except (OSError, UnicodeDecodeError, subprocess.SubprocessError):
                raise TrustedEvaluatorError("trusted evaluator process failed") from None

    @staticmethod
    def _validate_result(result: EvaluationResult, manifest: ProtocolManifest,
                         protocol: EvaluatorProtocolV1) -> None:
        if result.status != "ok":
            raise TrustedEvaluatorError("evaluator result status is not ok")
        if result.protocol_sha256 != manifest.protocol_sha256:
            raise TrustedEvaluatorError("evaluator returned a result for another frozen protocol")
        if protocol.metric_field not in result.metrics:
            raise TrustedEvaluatorError("evaluator omitted the approved primary metric")
        if not math.isfinite(result.metrics[protocol.metric_field].value):
            raise TrustedEvaluatorError("evaluator primary metric is not finite")
        expected = {(item.case_id, item.repetition): item.input_sha256 for item in manifest.cases}
        observed: dict[tuple[str, int], str] = {}
        for item in result.cases:
            key = (item.case_id, item.repetition)
            if key in observed or expected.get(key) != item.input_sha256:
                raise TrustedEvaluatorError("evaluator case identities do not match the frozen tier manifest")
            observed[key] = item.input_sha256
        if observed != expected:
            raise TrustedEvaluatorError("evaluator omitted or changed frozen tier cases")

