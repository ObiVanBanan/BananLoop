"""Evidence-only RPDI-05 adapter for fenced v2 exploration jobs.

Workers receive a fresh source snapshot and an artifact directory. They never
receive the registry or campaign database handle; trusted Core validates and
projects their evidence after the durable queue result has committed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import (
    BubblewrapBackend, CapabilityAuthority, CapabilityDenied, CapabilityGrantV1,
    LocalBackend, PermissionEngine, SandboxUnavailable, ToolGateway,
)
from .contracts import ExplorationCampaignSpecV2
from .graph_store import ExplorationCompletionV2, GraphRepository, GraphStoreError
from .worker_queue import DurableWorkerQueue, WorkerQueueError


class EvidenceAdapterError(RuntimeError):
    """Evidence failed trusted-Core validation or publication."""


class ArtifactDigestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceOnlyOutputV2(BaseModel):
    """Worker output contains evidence only; score/reward fields are rejected."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: str = Field(min_length=1, max_length=200)
    evidence_refs: tuple[str, ...] = ()
    artifacts: tuple[ArtifactDigestV2, ...] = ()
    limitations: tuple[str, ...] = ()
    next_experiment: str = Field(min_length=1, max_length=4000)

    @field_validator("evidence_refs", "limitations")
    @classmethod
    def valid_text_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not x.strip() or len(x) > 2000 for x in values):
            raise ValueError("evidence text items must be nonempty and bounded")
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def unique_artifact_paths(self) -> "EvidenceOnlyOutputV2":
        paths = [item.path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        return self


_FORBIDDEN_PARTS = {".git", ".bananloop_eval", "holdout", "validation", "secret", "secrets", "credential", "credentials"}
_MAX_SNAPSHOT_BYTES = 250_000_000
_MAX_ARTIFACT_BYTES = 25_000_000
_GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise EvidenceAdapterError("paths must be nonempty POSIX relative paths")
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or path.as_posix() in {"", "."}
            or re.match(r"^[A-Za-z]:", value)
            or any(part in {"", ".", ".."} for part in path.parts)):
        raise EvidenceAdapterError("path is absolute or traverses outside its root")
    lower = [part.lower() for part in path.parts]
    if any(part in _FORBIDDEN_PARTS or part.startswith(".env") for part in lower):
        raise EvidenceAdapterError("path is excluded by the evidence-only boundary")
    if any(re.search(r"(secret|credential|holdout|validation|evaluator)", part) for part in lower):
        raise EvidenceAdapterError("path resembles protected evaluation or secret material")
    return path.as_posix()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_state_dir(root: Path, *parts: str) -> Path:
    """Create state directories one component at a time, refusing symlinks."""
    trusted_root = root.resolve()
    trusted_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if trusted_root.is_symlink() or not trusted_root.is_dir():
        raise EvidenceAdapterError("configured state root is not a real directory")
    current = trusted_root
    for part in parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir():
            raise EvidenceAdapterError("state path contains a symlink or non-directory")
        if not current.resolve().is_relative_to(trusted_root):
            raise EvidenceAdapterError("state path escapes the configured root")
    return current


class EvidenceOnlyAttemptAdapter:
    """Build, execute, verify and replay an isolated exploration attempt."""

    def __init__(self, *, graph: GraphRepository, queue: DurableWorkerQueue,
                 campaign_id: str, state_root: str | Path):
        self.graph = graph
        self.queue = queue
        self.campaign_id = campaign_id
        self.state_root = Path(state_root).resolve()

    def enqueue(self, *, node_id: str, visible_paths: tuple[str, ...] | list[str]) -> dict[str, Any]:
        spec = self.graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2):
            raise EvidenceAdapterError("v2 Optimization remains unsupported until its evaluator adapter is approved")
        policy = spec.autonomy.capability_policy
        if policy is None:
            raise EvidenceAdapterError("v2 campaign has no approved capability policy; failing closed")
        paths = tuple(sorted({_safe_relative(p) for p in visible_paths}))
        if not paths:
            raise EvidenceAdapterError("an explicit nonempty source visibility allowlist is required")
        repo = Path(spec.source_repo).resolve()
        if not repo.is_dir():
            raise EvidenceAdapterError("campaign source_repo is unavailable")
        try:
            resolved_commit = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options",
                 f"{spec.base_commit}^{{commit}}"], text=True, stderr=subprocess.PIPE, timeout=15,
            ).strip().lower()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise EvidenceAdapterError("approved base does not resolve to a Git commit") from exc
        if not _GIT_OID.fullmatch(resolved_commit):
            raise EvidenceAdapterError("Git returned an invalid full commit object ID")
        policy_digest = _sha(_canonical(policy.model_dump(mode="json")).encode())
        manifest = {"policy": "rpdi06-capability-gateway-v1", "scope_digest": spec.autonomy.scope_digest,
                    "policy_digest": policy_digest, "visible_paths": list(paths), "base_commit": resolved_commit}
        capability_hash = _sha(_canonical(manifest).encode())
        identity = _sha(_canonical({"campaign_id": self.campaign_id, "node_id": node_id,
                                    "spec_hash": spec.approved_hash(), "graph_revision": self.graph.graph_revision(),
                                    "capability_hash": capability_hash}).encode())
        job_id = "explore-" + identity[:40]
        grant = PermissionEngine.derive(
            campaign_id=self.campaign_id, job_id=job_id, spec_hash=spec.approved_hash(),
            scope_digest=spec.autonomy.scope_digest, campaign=policy, role=policy, task=policy,
        )
        if any(not grant.allows_path("read", path) for path in paths):
            raise EvidenceAdapterError("source allowlist exceeds the approved campaign capability policy")
        authority = CapabilityAuthority(self.queue.path)
        try:
            authority.issue(grant)
        finally:
            authority.close()
        manifest["grant_digest"] = grant.grant_digest
        request = {"schema": "evidence-only-attempt-v1", "campaign_id": self.campaign_id,
                   "node_id": node_id, "spec_hash": spec.approved_hash(), "base_commit": resolved_commit,
                   "approved_base_ref": spec.base_commit,
                   "source_repo": str(repo), "capability_manifest": manifest,
                   "capability_manifest_digest": capability_hash, "capability_digest": grant.grant_digest,
                   "capability_grant": grant.model_dump(mode="json"),
                   "graph_revision": self.graph.graph_revision()}
        return self.queue.enqueue(job_id=job_id, enqueue_key=job_id, campaign_id=self.campaign_id,
                                  action_id=f"exploration:{node_id}", resource="agent_calls",
                                  upper_bound=1, request=request)

    def _workspace(self, job: dict[str, Any], fence: int) -> tuple[Path, Path]:
        request = job["request"]
        repo = Path(request["source_repo"])
        base = request["base_commit"]
        paths = tuple(_safe_relative(p) for p in request["capability_manifest"]["visible_paths"])
        command = ["git", "-C", str(repo), "archive", "--format=tar", base, "--", *paths]
        archive_file = tempfile.TemporaryFile()
        try:
            subprocess.run(command, check=True, stdout=archive_file, stderr=subprocess.PIPE, timeout=60)
            if archive_file.tell() > _MAX_SNAPSHOT_BYTES:
                raise EvidenceAdapterError("approved source snapshot exceeds the 250 MB limit")
            archive_file.seek(0)
        except Exception:
            archive_file.close()
            raise
        workspaces = _ensure_state_dir(self.state_root, "rpdi05", "workspaces")
        artifacts_root = _ensure_state_dir(self.state_root, "rpdi05", "artifacts")
        workspace = workspaces / f"{job['job_id']}-f{fence}"
        artifacts = artifacts_root / f"{job['job_id']}-f{fence}"
        workspace.mkdir(mode=0o700)
        artifacts.mkdir(mode=0o700)
        try:
            with tarfile.open(fileobj=archive_file, mode="r:") as archive:
                for member in archive.getmembers():
                    name = _safe_relative(member.name)
                    if not (member.isfile() or member.isdir()):
                        raise EvidenceAdapterError("source archive contains a link or special file")
                    target = workspace.joinpath(*PurePosixPath(name).parts)
                    if not target.resolve().is_relative_to(workspace.resolve()):
                        raise EvidenceAdapterError("archive member escapes the workspace")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        source = archive.extractfile(member)
                        if source is None:
                            raise EvidenceAdapterError("archive member could not be read")
                        target.write_bytes(source.read())
                        target.chmod(0o600)
        except Exception:
            # Leave failed materialization quarantined for forensic inspection.
            raise
        finally:
            archive_file.close()
        return workspace, artifacts

    def run_one(self, *, job_id: str, worker_id: str,
                execute: Callable[[dict[str, Any], ToolGateway], dict[str, Any]],
                lease_seconds: int = 300) -> dict[str, Any]:
        lease = self.queue.claim(job_id, worker_id, lease_seconds=lease_seconds)
        fence = int(lease["fence"])
        try:
            workspace, artifacts = self._workspace(lease, fence)
            self.queue.mark_dispatched(job_id, worker_id, fence)
            grant = CapabilityGrantV1.model_validate(lease["request"]["capability_grant"])
            authority = CapabilityAuthority(self.queue.path)
            try:
                backend = LocalBackend() if grant.backend == "LOCAL" else BubblewrapBackend()
                gateway = ToolGateway(queue=self.queue, authority=authority, grant=grant, fence=fence,
                                      workspace=workspace, artifacts=artifacts, backend=backend)
                raw = execute(dict(lease["request"]), gateway)
            finally:
                authority.close()
            output = EvidenceOnlyOutputV2.model_validate(raw)
            if output.node_id != lease["request"]["node_id"]:
                raise EvidenceAdapterError("worker output targets a different graph node")
            declared = {item.path: item.sha256 for item in output.artifacts}
            actual: dict[str, str] = {}
            if any(_safe_relative(path) != path for path in declared):
                raise EvidenceAdapterError("artifact paths must be canonical relative paths")
            aggregate_size = 0
            for path in artifacts.rglob("*"):
                if path.is_symlink():
                    raise EvidenceAdapterError("artifact symlinks are forbidden")
                if path.is_file():
                    relative = _safe_relative(path.relative_to(artifacts).as_posix())
                    if path.stat().st_size > _MAX_ARTIFACT_BYTES:
                        raise EvidenceAdapterError("an artifact exceeds the 25 MB limit")
                    aggregate_size += path.stat().st_size
                    if aggregate_size > _MAX_ARTIFACT_BYTES:
                        raise EvidenceAdapterError("artifact set exceeds the 25 MB aggregate limit")
                    actual[relative] = _sha(path.read_bytes())
            if declared != actual:
                raise EvidenceAdapterError("artifact manifest does not exactly match produced artifact files")
            result = output.model_dump(mode="json")
            output_digest = _sha(_canonical(result).encode())
            receipt_digest = _sha(_canonical({"job_id": job_id, "fence": fence,
                                             "request_hash": lease["request_hash"],
                                             "output_digest": output_digest}).encode())
            return self.queue.complete(job_id, worker_id, fence, output_digest=output_digest,
                                       actual_amount=1, receipt_digest=receipt_digest,
                                       result_payload=result)
        except Exception as exc:
            try:
                self.queue.cancel(job_id, reason=f"evidence attempt failed: {type(exc).__name__}")
            except WorkerQueueError:
                pass
            raise

    def publish_completed(self, job_id: str) -> dict[str, Any]:
        job = self.queue.get_job(job_id)
        durable = self.queue.get_result(job_id)
        if job["state"] != "COMPLETED" or durable["result"] is None:
            raise EvidenceAdapterError("job has no durable evidence result")
        result = EvidenceOnlyOutputV2.model_validate(durable["result"])
        encoded = _canonical(result.model_dump(mode="json"))
        if _sha(encoded.encode()) != durable["output_digest"]:
            raise EvidenceAdapterError("durable worker output digest failed verification")
        request = job["request"]
        if int(durable["fence"]) != int(job["fence"]):
            raise EvidenceAdapterError("durable result belongs to a stale worker fence")
        spec = self.graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2) or spec.approved_hash() != request["spec_hash"]:
            raise EvidenceAdapterError("campaign spec changed since the attempt was enqueued")
        artifacts_parent = _ensure_state_dir(self.state_root, "rpdi05", "artifacts")
        artifact_directory = artifacts_parent / f"{job_id}-f{durable['fence']}"
        if artifact_directory.is_symlink() or not artifact_directory.is_dir() or not artifact_directory.resolve().is_relative_to(artifacts_parent.resolve()):
            raise EvidenceAdapterError("job artifact directory is missing or escaped its state root")
        published_root = _ensure_state_dir(self.state_root, "rpdi05", "published")
        artifact_refs: list[str] = []
        artifact_digests: list[str] = []
        aggregate_size = 0
        for item in result.artifacts:
            relative = _safe_relative(item.path)
            source = artifact_directory.joinpath(*PurePosixPath(relative).parts)
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(artifact_directory.resolve()):
                raise EvidenceAdapterError("verified artifact is missing or escaped its job directory")
            if source.stat().st_size > _MAX_ARTIFACT_BYTES:
                raise EvidenceAdapterError("an artifact exceeds the 25 MB limit")
            content = source.read_bytes()
            aggregate_size += len(content)
            if aggregate_size > _MAX_ARTIFACT_BYTES:
                raise EvidenceAdapterError("artifact set exceeds the 25 MB aggregate limit")
            digest = _sha(content)
            if digest != item.sha256:
                raise EvidenceAdapterError("artifact bytes changed after worker completion")
            target = published_root / digest
            if target.is_symlink():
                raise EvidenceAdapterError("content-addressed artifact target is a symlink")
            if target.exists():
                if not target.is_file() or _sha(target.read_bytes()) != digest:
                    raise EvidenceAdapterError("content-addressed artifact store contains conflicting bytes")
            else:
                temporary = published_root / f".{digest}.{os.getpid()}.tmp"
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
                try:
                    with os.fdopen(descriptor, "wb") as output_file:
                        output_file.write(content)
                        output_file.flush()
                        os.fsync(output_file.fileno())
                    os.replace(temporary, target)
                    target.chmod(0o400)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            artifact_digests.append(digest)
            artifact_refs.append(f"artifact://sha256/{digest}")
        payload = {
            "command_id": f"complete-{job_id}-f{durable['fence']}",
            "idempotency_key": f"completion:{job_id}:f{durable['fence']}",
            "expected_graph_revision": self.graph.graph_revision(),
            "node_id": result.node_id, "job_id": job_id, "worker_fence": int(durable["fence"]),
            "source_request_hash": job["request_hash"], "output_digest": durable["output_digest"],
            "spec_hash": request["spec_hash"],
            "evidence_refs": tuple(sorted((*result.evidence_refs, *artifact_refs))),
            "artifact_digests": tuple(sorted(artifact_digests)), "limitations": result.limitations,
            "next_experiment": result.next_experiment,
        }
        try:
            completion = ExplorationCompletionV2.model_validate(payload)
            if self.graph.command_response(payload["idempotency_key"]) is not None:
                return self.graph.complete_exploration(completion)
            grant = CapabilityGrantV1.model_validate(request["capability_grant"])
            authority = CapabilityAuthority(self.queue.path)
            try:
                backend = LocalBackend() if grant.backend == "LOCAL" else BubblewrapBackend()
                workspace = self.state_root / "rpdi05" / "workspaces" / f"{job_id}-f{durable['fence']}"
                gateway = ToolGateway(queue=self.queue, authority=authority, grant=grant,
                                      fence=int(durable["fence"]), workspace=workspace,
                                      artifacts=artifact_directory, backend=backend)
                return gateway.commit_evidence(lambda: self.graph.complete_exploration(completion))
            finally:
                authority.close()
        except (GraphStoreError, CapabilityDenied, SandboxUnavailable) as exc:
            raise EvidenceAdapterError(str(exc)) from exc

