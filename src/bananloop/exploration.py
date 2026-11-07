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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .budget_ledger import BudgetLedger
from .capabilities import (
    BubblewrapBackend, CapabilityAuthority, CapabilityDenied, CapabilityGrantV1,
    LocalBackend, PermissionEngine, SandboxUnavailable, ToolGateway,
)
from .contracts import ExplorationCampaignSpecV2
from .graph_store import ExplorationCompletionV2, GraphRepository, GraphStoreError
from .human_decision import HumanDecisionRequestV1
from .roles import (
    PinnedRoleExecutorV1, RoleContextSourceV1,
    RoleContractError, RoleContextV1, RoleRegistryV1, TeamFactoryV1,
    TeamManifestAuthority, TeamRequestV1, build_role_context,
)
from .evaluator_verification import EvaluatorProtocolAuthority, EvaluatorProtocolDraftV1
from .research_ledger import ResearchFindingV1, ResearchLedger, ResearchLedgerError
from .worker_queue import DurableWorkerQueue, WorkerQueueError
from .scheduler import HybridScheduler


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
    research_findings: tuple[ResearchFindingV1, ...] = Field(default=(), max_length=64)
    protocol_draft: EvaluatorProtocolDraftV1 | None = None
    human_decision_request: HumanDecisionRequestV1 | None = None

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
        claims = [item.claim_key for item in self.research_findings]
        if len(claims) != len(set(claims)):
            raise ValueError("research finding claim keys must be unique within one result")
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
                 campaign_id: str, state_root: str | Path, role_registry: RoleRegistryV1):
        self.graph = graph
        self.queue = queue
        self.campaign_id = campaign_id
        self.graph.bind_campaign_id(campaign_id)
        self.state_root = Path(state_root).resolve()
        self.role_registry = role_registry

    def enqueue(self, *, node_id: str, visible_paths: tuple[str, ...] | list[str]) -> dict[str, Any]:
        spec = self.graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2):
            raise EvidenceAdapterError("v2 Optimization remains unsupported until its evaluator adapter is approved")
        spec_hash = spec.approved_hash()
        if self.role_registry.digest() != spec.role_registry_digest:
            raise EvidenceAdapterError("role registry does not match the frozen campaign spec")
        try:
            self.role_registry.role("researcher")
        except RoleContractError as exc:
            raise EvidenceAdapterError("pinned role registry has no canonical researcher role") from exc
        snapshot = self.graph.scheduling_snapshot()
        target_node = next((item for item in snapshot["nodes"] if item["node_id"] == node_id), None)
        if target_node is None or target_node["phase"] != "EXPLORATION":
            raise EvidenceAdapterError("evidence-only adapter accepts only existing EXPLORATION nodes")
        if target_node["status"] not in {"PENDING", "READY"} or not target_node["ready"]:
            raise EvidenceAdapterError("exploration node is blocked or is not schedulable")
        node_revision = int(target_node["revision"]) + (1 if target_node["status"] == "PENDING" else 0)
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
        action_id = f"exploration:{node_id}"
        existing = self.queue.get_job_for_action(self.campaign_id, action_id)
        if existing is not None:
            request = existing["request"]
            capability_manifest = request.get("capability_manifest")
            if (request.get("schema") != "evidence-only-attempt-v1"
                    or request.get("node_id") != node_id
                    or request.get("spec_hash") != spec_hash
                    or request.get("base_commit") != resolved_commit
                    or request.get("role_registry_digest") != spec.role_registry_digest
                    or not isinstance(capability_manifest, dict)
                    or capability_manifest.get("visible_paths") != list(paths)
                    or int(request.get("node_revision", -1)) != node_revision):
                raise EvidenceAdapterError("persisted Exploration action differs from this replay request")
            return existing
        budget_ledger = BudgetLedger(self.queue.path)
        try:
            budget_snapshot = budget_ledger.snapshot(self.campaign_id)
        finally:
            budget_ledger.close()
        budget_remaining = {
            name: max(0, int(item["cap"]) - int(item["spent"])
                      - int(item["reserved"]) - int(item["unknown"]))
            for name, item in budget_snapshot.items()
        }
        model_token_cap = spec.budget.max_model_tokens
        team_request = TeamRequestV1(
            campaign_id=self.campaign_id,
            spec_hash=spec_hash,
            requested_instances={"researcher": 1},
            available_agent_calls=spec.budget.max_agent_calls,
            available_model_tokens=model_token_cap,
        )
        try:
            built_manifest = TeamFactoryV1.build(
                self.role_registry, team_request, expected_registry_digest=spec.role_registry_digest,
            )
            manifest_authority = TeamManifestAuthority(self.queue.path)
            try:
                try:
                    team_manifest = manifest_authority.load(
                        self.campaign_id, spec_hash=spec_hash,
                        registry_digest=spec.role_registry_digest,
                    )
                except RoleContractError as exc:
                    if "has no pinned" not in str(exc):
                        raise
                    team_manifest = manifest_authority.pin(built_manifest)
                if team_manifest.manifest_digest != built_manifest.manifest_digest:
                    raise RoleContractError("rebuilt team does not match the durable campaign pin")
            finally:
                manifest_authority.close()
        except RoleContractError as exc:
            raise EvidenceAdapterError(str(exc)) from exc
        researcher = next((member for member in team_manifest.members if member.role.role_id == "researcher"), None)
        if researcher is None:
            raise EvidenceAdapterError("pinned team manifest has no researcher instance")
        node_hypothesis = target_node["payload"].get("hypothesis")
        if not isinstance(node_hypothesis, str) or not node_hypothesis.strip():
            node_hypothesis = None
        try:
            context_fields = {
                    "node_id": {"value": node_id, "classification": "PUBLIC_NODE",
                                "source_ref": f"graph://nodes/{node_id}"},
                    "node_phase": {"value": target_node["phase"], "classification": "PUBLIC_NODE",
                                   "source_ref": f"graph://nodes/{node_id}"},
                    "public_task": {
                        "value": (node_hypothesis or spec.goal)[:4000],
                        "classification": "PUBLIC_NODE" if node_hypothesis else "PUBLIC_SPEC",
                        "source_ref": f"graph://nodes/{node_id}" if node_hypothesis
                                      else "campaign://spec/goal",
                    },
                    "evidence_refs": {"value": (), "classification": "PUBLIC_NODE",
                                      "source_ref": f"graph://nodes/{node_id}"},
                    "budget_remaining": {"value": budget_remaining,
                                         "classification": "CORE_BUDGET",
                                         "source_ref": f"budget://campaign/{self.campaign_id}"},
            }
            role_context_source = RoleContextSourceV1(
                campaign_goal={"value": spec.goal, "classification": "PUBLIC_SPEC",
                               "source_ref": "campaign://spec/goal"},
                fields=context_fields,
            )
            role_context = build_role_context(researcher, role_context_source)
            protocol_digest = ResearchLedger.protocol_digest_for_binding(
                team_manifest.job_binding(
                    researcher.instance_id, action_id=action_id,
                    context_digest=role_context.digest(),
                )
            )
            projection = ResearchLedger(self.graph.conn).project(
                graph=self.graph, queue=self.queue,
                campaign_ids=(self.campaign_id,), source_snapshot_id=resolved_commit,
                protocol_digest=protocol_digest,
            )
            if projection.evidence:
                source_ref = f"research://ledger/{projection.projection_digest}"
                context_updated = False
                if "evidence_refs" in researcher.role.context_fields:
                    context_fields["evidence_refs"] = {
                        "value": tuple(item.evidence_id for item in projection.evidence),
                        "classification": "DEV_AGGREGATE", "source_ref": source_ref,
                    }
                    context_updated = True
                if "dev_summary" in researcher.role.context_fields:
                    summary_lines = [
                        f"{item.claim_key} [{item.stance}] evidence_ids={item.evidence_id}: {item.statement}"
                        for item in projection.evidence
                    ]
                    summary_lines.extend([
                        f"{item.claim_key} [{item.status}] evidence_ids="
                        f"{','.join(item.evidence_ids)}: " + " | ".join(item.statements)
                        for item in projection.causal_memory
                    ])
                    summary_lines.extend(
                        f"{item.signature} [{item.status}]: {item.intervention_type}/"
                        f"{item.algorithmic_layer}; paths={', '.join(item.affected_paths)}; "
                        f"support={item.support_count}; refutation={item.refutation_count}; "
                        f"evidence_ids={','.join(item.evidence_ids)}"
                        for item in projection.structural_memory
                    )
                    if projection.contradictions:
                        summary_lines.append(
                            f"{len(projection.contradictions)} conflicting public DEV finding(s) are retained separately."
                        )
                    context_fields["dev_summary"] = {
                        "value": "\n".join(summary_lines)[:12000],
                        "classification": "DEV_AGGREGATE", "source_ref": source_ref,
                    }
                    context_updated = True
                if context_updated:
                    role_context = build_role_context(
                        researcher, RoleContextSourceV1(campaign_goal=role_context_source.campaign_goal,
                                                        fields=context_fields),
                    )
        except Exception as exc:
            raise EvidenceAdapterError(f"cannot build isolated researcher context: {type(exc).__name__}") from exc
        role_binding = team_manifest.job_binding(
            researcher.instance_id, action_id=action_id, context_digest=role_context.digest(),
        )
        role_policy = researcher.role.capability_policy
        task_policy = policy.model_copy(update={"read_paths": paths})
        scope_digest = _sha(_canonical({
            "campaign_scope": spec.autonomy.scope_digest,
            "team_manifest_digest": team_manifest.manifest_digest,
            "role_id": researcher.role.role_id,
            "role_policy_digest": _sha(_canonical(role_policy.model_dump(mode="json")).encode()),
        }).encode())
        policy_digest = _sha(_canonical({
            "campaign": policy.model_dump(mode="json"),
            "role": role_policy.model_dump(mode="json"),
            "task": task_policy.model_dump(mode="json"),
        }).encode())
        manifest = {"policy": "rpdi06-capability-gateway-v1", "scope_digest": spec.autonomy.scope_digest,
                    "policy_digest": policy_digest, "visible_paths": list(paths), "base_commit": resolved_commit,
                    "node_revision": node_revision, "team_manifest_digest": team_manifest.manifest_digest,
                    "role_binding_digest": _sha(_canonical(role_binding).encode()),
                    "context_digest": role_context.digest()}
        capability_hash = _sha(_canonical(manifest).encode())
        identity = _sha(_canonical({"campaign_id": self.campaign_id, "node_id": node_id,
                                    "spec_hash": spec.approved_hash(), "node_revision": node_revision,
                                    "capability_hash": capability_hash}).encode())
        job_id = "explore-" + identity[:40]
        grant = PermissionEngine.derive(
            campaign_id=self.campaign_id, job_id=job_id, spec_hash=spec_hash,
            scope_digest=scope_digest, campaign=policy, role=role_policy, task=task_policy,
        )
        if any(not grant.allows_path("read", path) for path in paths):
            raise EvidenceAdapterError("source allowlist exceeds the approved campaign capability policy")
        if target_node["status"] == "PENDING":
            try:
                ready = HybridScheduler(self.graph).mark_ready(
                    node_id, expected_node_revision=int(target_node["revision"]), expected_phase="EXPLORATION"
                )
            except GraphStoreError as exc:
                raise EvidenceAdapterError(str(exc)) from exc
            if ready["node_revision"] != node_revision:
                raise EvidenceAdapterError("node changed while the exploration job was being prepared")
        authority = CapabilityAuthority(self.queue.path)
        try:
            authority.issue(grant)
        finally:
            authority.close()
        manifest["grant_digest"] = grant.grant_digest
        request = {"schema": "evidence-only-attempt-v1", "campaign_id": self.campaign_id,
                   "node_id": node_id, "spec_hash": spec_hash, "base_commit": resolved_commit,
                   "approved_base_ref": spec.base_commit,
                   "source_repo": str(repo), "capability_manifest": manifest,
                   "capability_manifest_digest": capability_hash, "capability_digest": grant.grant_digest,
                   "capability_grant": grant.model_dump(mode="json"),
                   "node_revision": node_revision,
                   "role_binding": role_binding,
                   "role_context": role_context.model_dump(mode="json", exclude_unset=True),
                   "team_manifest_digest": team_manifest.manifest_digest,
                   "role_registry_digest": team_manifest.registry_digest}
        return self.queue.enqueue(
            job_id=job_id, enqueue_key=job_id, campaign_id=self.campaign_id,
            action_id=action_id, resource="agent_calls", upper_bound=1,
            request=request,
            role_allocation={
                "manifest_digest": team_manifest.manifest_digest,
                "role_instance_id": researcher.instance_id,
                "role_id": researcher.role.role_id,
                "calls_limit": researcher.role.calls_per_instance,
                "tokens_limit": researcher.role.tokens_per_instance,
                "token_upper_bound": researcher.role.tokens_per_instance // researcher.role.calls_per_instance,
            },
        )

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
                executor: PinnedRoleExecutorV1,
                lease_seconds: int = 300) -> dict[str, Any]:
        before_dispatch = self.queue.get_job(job_id)
        if before_dispatch["state"] != "READY":
            raise EvidenceAdapterError("role-bound worker job is not READY")
        spec = self.graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2):
            raise EvidenceAdapterError("campaign no longer resolves to an Exploration spec")
        try:
            manifest_authority = TeamManifestAuthority(self.queue.path)
            try:
                team_manifest = manifest_authority.load(
                    self.campaign_id, spec_hash=spec.approved_hash(),
                    registry_digest=spec.role_registry_digest,
                )
            finally:
                manifest_authority.close()
            bound_member = team_manifest.member(before_dispatch["request"]["role_binding"]["role_instance_id"])
            if executor.manifest_digest != team_manifest.manifest_digest or executor.member != bound_member:
                raise RoleContractError("executor was not built from the pinned role manifest member")
        except (RoleContractError, KeyError, TypeError) as exc:
            raise EvidenceAdapterError("executor does not match the pinned team manifest") from exc
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
                raw = executor.dispatch(dict(lease["request"]), gateway)
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
        # Hash the exact durable worker payload. Re-serializing through the
        # current model would add fields introduced after an older job was
        # committed and break crash-safe replay across schema additions.
        encoded = _canonical(durable["result"])
        if _sha(encoded.encode()) != durable["output_digest"]:
            raise EvidenceAdapterError("durable worker output digest failed verification")
        request = job["request"]
        if int(durable["fence"]) != int(job["fence"]):
            raise EvidenceAdapterError("durable result belongs to a stale worker fence")
        spec = self.graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2) or spec.approved_hash() != request["spec_hash"]:
            raise EvidenceAdapterError("campaign spec changed since the attempt was enqueued")
        try:
            authority = TeamManifestAuthority(self.queue.path)
            try:
                team_manifest = authority.load(
                    self.campaign_id,
                    spec_hash=spec.approved_hash(),
                    registry_digest=spec.role_registry_digest,
                )
            finally:
                authority.close()
            if team_manifest.manifest_digest != request.get("team_manifest_digest"):
                raise RoleContractError("worker request changed its team manifest binding")
            role_context = RoleContextV1.model_validate(request["role_context"])
            member = team_manifest.member(request["role_binding"]["role_instance_id"])
            if role_context.role_id != member.role.role_id or not set(role_context.model_fields_set) <= (
                    set(member.role.context_fields) | {"role_id"}):
                raise RoleContractError("worker role context exceeds its pinned field allowlist")
            expected_binding = team_manifest.job_binding(
                request["role_binding"]["role_instance_id"],
                action_id=request["role_binding"]["action_id"],
                context_digest=role_context.digest(),
            )
            if expected_binding != request["role_binding"]:
                raise RoleContractError("worker request role binding failed verification")
        except (RoleContractError, KeyError, TypeError, ValueError) as exc:
            raise EvidenceAdapterError("worker request is not bound to the pinned team manifest") from exc
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
            "node_id": result.node_id, "node_revision": int(request["node_revision"]),
            "job_id": job_id, "worker_fence": int(durable["fence"]),
            "source_request_hash": job["request_hash"], "output_digest": durable["output_digest"],
            "spec_hash": request["spec_hash"],
            "evidence_refs": tuple(sorted((*result.evidence_refs, *artifact_refs))),
            "artifact_digests": tuple(sorted(artifact_digests)), "limitations": result.limitations,
            "next_experiment": result.next_experiment,
            "human_decision_request": result.human_decision_request,
        }
        try:
            completion = ExplorationCompletionV2.model_validate(payload)
            if self.graph.command_response(payload["idempotency_key"]) is not None:
                replay = self.graph.complete_exploration(completion)
                ResearchLedger(self.graph.conn).ingest_completed_job(self.graph, self.queue, job_id)
                if result.protocol_draft is not None:
                    EvaluatorProtocolAuthority(self.graph.conn).record_completed_proposal(
                        self.graph, self.queue, job_id,
                    )
                return replay
            grant = CapabilityGrantV1.model_validate(request["capability_grant"])
            authority = CapabilityAuthority(self.queue.path)
            try:
                backend = LocalBackend() if grant.backend == "LOCAL" else BubblewrapBackend()
                workspace = self.state_root / "rpdi05" / "workspaces" / f"{job_id}-f{durable['fence']}"
                gateway = ToolGateway(queue=self.queue, authority=authority, grant=grant,
                                      fence=int(durable["fence"]), workspace=workspace,
                                      artifacts=artifact_directory, backend=backend)
                def commit_completion_and_ledger():
                    response = self.graph.complete_exploration(completion)
                    ResearchLedger(self.graph.conn).ingest_completed_job(self.graph, self.queue, job_id)
                    if result.protocol_draft is not None:
                        EvaluatorProtocolAuthority(self.graph.conn).record_completed_proposal(
                            self.graph, self.queue, job_id,
                        )
                    return response

                return gateway.commit_evidence(commit_completion_and_ledger)
            finally:
                authority.close()
        except (GraphStoreError, CapabilityDenied, SandboxUnavailable, ResearchLedgerError) as exc:
            raise EvidenceAdapterError(str(exc)) from exc

