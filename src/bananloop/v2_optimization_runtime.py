"""Pinned, capability-gated v2 candidate creation for Optimization nodes.

The optimizer can propose bounded file contents only. Core writes those files
through ToolGateway, commits the candidate in a private snapshot repository,
and publishes the commit into the research graph after the durable queue result
has been verified. Evaluator execution remains a separate Core-owned lane.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import fnmatch
import sqlite3
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .budget_ledger import BudgetLedger
from .capabilities import (
    BubblewrapBackend, CapabilityAuthority, CapabilityDenied, CapabilityGrantV1,
    LocalBackend, PermissionEngine, SandboxUnavailable, ToolGateway,
)
from .contracts import OptimizationCampaignSpecV2, RuntimePaths, utc_now
from .evaluator_verification import EvaluatorProtocolAuthority
from .graph_store import AddEdge, AddNode, MutateGraph, TransitionNodeStatus
from .optimization_lifecycle import OptimizationAttemptCoordinator
from .roles import (
    PinnedRoleExecutorV1, RoleContextSourceV1, RoleContractError,
    RoleRegistryV1, TeamFactoryV1, TeamManifestAuthority, TeamRequestV1,
    build_role_context,
)
from .scheduler import HybridScheduler, SchedulerPlan
from .worker_queue import DurableWorkerQueue, WorkerBudgetError, WorkerQueueError


class V2OptimizationError(RuntimeError):
    """A v2 candidate cannot safely be proposed, executed, or published."""


def _format_report_metric(metric: dict[str, Any] | None) -> str:
    if not isinstance(metric, dict) or metric.get("value") is None:
        return "UNKNOWN"
    value = metric["value"]
    unit = metric.get("unit")
    return f"{value} {unit}" if unit else str(value)


def _provider_call_summaries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    calls = []
    for row in conn.execute(
        "SELECT payload_json FROM events WHERE event_type='V2_PROVIDER_CALL' ORDER BY seq"
    ):
        receipt = json.loads(row[0])
        raw_usage = receipt.get("usage")
        usage = ({key: raw_usage.get(key) for key in
                  ("input_tokens", "output_tokens", "total_tokens")}
                 if isinstance(raw_usage, dict) else "UNKNOWN")
        calls.append({
            "role_id": receipt.get("role_id"),
            "request_id": receipt.get("request_id"),
            "status": receipt.get("status", "UNKNOWN"),
            "dispatched": receipt.get("dispatched") is True,
            "usage": usage,
        })
    return calls


def _render_final_report_markdown(report: dict[str, Any]) -> str:
    """Render a public, owner-readable campaign summary beside the JSON report."""
    optimization = report.get("optimization_report", report)
    comparison = optimization.get("baseline_candidate_delta", {})
    lines = [
        f"# BananLoop campaign {report.get('campaign_id', optimization.get('campaign_id', 'unknown'))}",
        "",
        f"- Status: `{report.get('status', optimization.get('status', 'UNKNOWN'))}`",
        f"- Goal: {optimization.get('goal', 'Not recorded.')}",
        "",
        "## Validation baseline / candidate / delta",
        "",
    ]
    rows = comparison.get("comparison_table", []) if isinstance(comparison, dict) else []
    if comparison.get("status") == "VERIFIED" and rows:
        lines.extend([
            "| Tier | Metric | Baseline | Candidate | Delta | Directional improvement |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ])
        for row in rows:
            baseline = row.get("baseline")
            candidate = row.get("candidate")
            delta = row.get("delta")
            directional = row.get("directional_improvement")
            lines.append(
                f"| {row.get('tier', 'VALIDATION')} | {row.get('metric_id', 'unknown')} "
                f"| {baseline} | {candidate} | {delta:+} | {directional:+} |"
            )
    else:
        lines.append(
            f"Comparison `{comparison.get('status', 'INCOMPLETE')}`: "
            f"{comparison.get('reason', 'validation receipts are unavailable.')}"
        )
    holdout = optimization.get("holdout")
    if isinstance(holdout, dict):
        lines.extend([
            "", "## Trusted HOLDOUT", "",
            f"- Status: `{holdout.get('status', 'UNKNOWN')}`",
            f"- Metric: {_format_report_metric(holdout.get('metric'))}",
            f"- Cases: {holdout.get('case_count', 'UNKNOWN')}",
            f"- Repeatability runs: {holdout.get('repeatability_runs', 'UNKNOWN')}",
        ])
    provider_calls = report.get("provider_calls", optimization.get("provider_calls", []))
    if provider_calls:
        lines.extend([
            "", "## Provider calls", "",
            "| Role | Request ID | Status | Input tokens | Output tokens | Total tokens |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ])
        for call in provider_calls:
            usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
            lines.append(
                f"| {call.get('role_id', 'unknown')} | `{call.get('request_id') or 'UNKNOWN'}` "
                f"| {call.get('status', 'UNKNOWN')} | {usage.get('input_tokens', 'UNKNOWN')} "
                f"| {usage.get('output_tokens', 'UNKNOWN')} "
                f"| {usage.get('total_tokens', 'UNKNOWN')} |"
            )
    limitations = report.get("limitations", optimization.get("limitations", []))
    if limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in limitations)
    lines.append("")
    return "\n".join(lines)


def _atomic_write_report(path: Path, raw: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OptimizationCandidateProposalV1(BaseModel):
    """Untrusted optimizer output: complete file replacements, never commands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=4000)
    changes: dict[str, str] = Field(min_length=1, max_length=16)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("limitations", mode="before")
    @classmethod
    def normalize_single_limitation(cls, value: Any) -> Any:
        # LLMs occasionally serialize one optional limitation as a scalar
        # string. This has an unambiguous lossless interpretation; malformed
        # non-string values remain rejected by the typed contract.
        return (value,) if isinstance(value, str) else value

    @field_validator("limitations")
    @classmethod
    def bounded_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 2000 for item in value):
            raise ValueError("candidate limitations must be nonempty bounded text")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def bounded_changes(self) -> "OptimizationCandidateProposalV1":
        if sum(len(text.encode("utf-8")) for text in self.changes.values()) > 4_000_000:
            raise ValueError("candidate changes exceed the 4 MB response limit")
        if any(not text for text in self.changes.values()):
            raise ValueError("candidate file replacements cannot be empty")
        return self


OptimizerModelReply = Callable[..., dict[str, Any]]
def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise V2OptimizationError("candidate paths must be nonempty POSIX relative paths")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    parts = tuple(part.lower() for part in path.parts)
    protected = {".git", ".bananloop_eval"}
    if (path.is_absolute() or windows.drive or path.as_posix() in {"", "."}
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(part in protected or part.startswith(".env")
                   or any(marker in part for marker in (
                       "evaluator", "evaluation", "validation", "holdout",
                       "secret", "credential",
                   )) for part in parts)):
        raise V2OptimizationError("candidate path is absolute, protected, or traverses its workspace")
    return path.as_posix()


def make_optimizer_invoke(model_reply: OptimizerModelReply) -> Callable[..., dict[str, Any]]:
    """Create a one-call adapter that exposes only allowlisted public source.

    ``model_reply`` is expected to be the official provider broker. It receives
    no queue request, capability grant, filesystem path, evaluator asset, or
    credential. Every write is performed by Core through ToolGateway.
    """
    if not callable(model_reply):
        raise TypeError("a trusted one-shot optimizer transport is required")

    def invoke(request: dict[str, Any], gateway: Any, *, adapter: str, provider: str,
               model: str, model_version: str, prompt_digest: str,
               max_model_tokens: int) -> dict[str, Any]:
        if adapter != "codex" or provider != "openai":
            raise V2OptimizationError("v2 Optimization accepts only its pinned OpenAI role")
        binding = request.get("role_binding")
        if (not isinstance(binding, dict) or binding.get("role_id") != "optimizer"
                or any(binding.get(key) != value for key, value in (
                    ("adapter", adapter), ("provider", provider), ("model", model),
                    ("model_version", model_version), ("prompt_digest", prompt_digest)))):
            raise V2OptimizationError("model request differs from its pinned optimizer role")
        manifest = request.get("capability_manifest")
        paths = manifest.get("visible_paths") if isinstance(manifest, dict) else None
        write_paths = (manifest.get("write_paths", paths)
                       if isinstance(manifest, dict) else None)
        if (not isinstance(paths, list) or not paths or len(paths) > 16
                or any(not isinstance(path, str) for path in paths)
                or not isinstance(write_paths, list) or not write_paths
                or len(write_paths) > 16 or any(not isinstance(path, str) for path in write_paths)):
            raise V2OptimizationError("optimizer has no bounded source visibility allowlist")
        paths = [_safe_path(path) for path in paths]
        write_paths = [_safe_path(path) for path in write_paths]
        if len(paths) != len(set(paths)) or len(write_paths) != len(set(write_paths)):
            raise V2OptimizationError("optimizer source path allowlists must be unique")
        if not set(write_paths) <= set(paths):
            raise V2OptimizationError("candidate write paths must also be visible to the optimizer")
        documents: dict[str, str] = {}
        total = 0
        for raw_path in paths:
            path = _safe_path(raw_path)
            content = gateway.read_file(path)
            if len(content) > 64_000:
                raise V2OptimizationError("optimizer source file exceeds the 64 KB prompt limit")
            total += len(content)
            if total > 160_000:
                raise V2OptimizationError("optimizer source context exceeds the 160 KB prompt limit")
            documents[path] = content
        context = request.get("role_context")
        if not isinstance(context, dict):
            raise V2OptimizationError("pinned optimizer context is missing")
        public_input = {
            "protocol": "v2-optimizer-one-shot-v2",
            "role_context": context,
            "selected_hypothesis_id": request["hypothesis_id"],
            "visible_documents": documents,
            "writable_paths": write_paths,
            "instruction": (
                "Return complete replacement text for only writable_paths. "
                "Do not return shell commands, scores, evaluator claims, or private test data. "
                "The trusted Core will run the frozen DEV evaluator after committing this candidate."
            ),
        }
        raw = model_reply(
            public_input, provider=provider, model=model,
            model_version=model_version, prompt_digest=prompt_digest,
            max_model_tokens=max_model_tokens,
        )
        provider_usage = getattr(model_reply, "last_usage", None)
        model_tokens = (provider_usage.get("total_tokens")
                        if isinstance(provider_usage, dict) else None)
        if (isinstance(model_tokens, bool) or not isinstance(model_tokens, int)
                or model_tokens < 0):
            model_tokens = None
        proposal = OptimizationCandidateProposalV1.model_validate(raw)
        if proposal.hypothesis_id != request.get("hypothesis_id"):
            raise V2OptimizationError("optimizer changed the selected hypothesis identity")
        changed: list[str] = []
        for raw_path, content in sorted(proposal.changes.items()):
            path = _safe_path(raw_path)
            if path not in write_paths:
                raise V2OptimizationError("candidate change is outside the task's exact write allowlist")
            gateway.write_file(path, content)
            changed.append(path)
        return {
            "schema": "v2-optimization-candidate-v1",
            "hypothesis_id": proposal.hypothesis_id,
            "rationale": proposal.rationale,
            "changed_paths": changed,
            "limitations": list(proposal.limitations),
            "model_tokens": model_tokens,
        }

    return invoke


def _run_git(args: list[str], cwd: Path, *, timeout: int = 60) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(cwd), "-c", "core.longpaths=true", *args],
            stderr=subprocess.PIPE, timeout=timeout,
        ).decode("utf-8").strip()
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        detail = getattr(exc, "stderr", None)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        if not isinstance(detail, str) or not detail.strip():
            detail = str(exc)
        detail = " ".join(detail.split())[:500]
        raise V2OptimizationError(
            f"trusted Core Git snapshot operation failed "
            f"(git {args[0] if args else 'operation'}; target_is_directory={cwd.is_dir()}; "
            f"{type(exc).__name__}: {detail})"
        ) from None


def _safe_mkdir(root: Path, *parts: str) -> Path:
    trusted = root.resolve()
    trusted.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = trusted
    for part in parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir() or not current.resolve().is_relative_to(trusted):
            raise V2OptimizationError("Optimization state path is unsafe")
    return current


class V2OptimizationCandidateAdapter:
    """Durable optimizer dispatch and graph projection for one candidate."""

    def __init__(self, *, graph: Any, queue: DurableWorkerQueue, campaign_id: str,
                 state_root: str | Path, role_registry: RoleRegistryV1):
        self.graph, self.queue, self.campaign_id = graph, queue, campaign_id
        self.graph.bind_campaign_id(campaign_id)
        self.state_root = Path(state_root).resolve()
        self.role_registry = role_registry

    def _pinned(self, node_id: str, visible_paths: list[str] | tuple[str, ...],
                hypothesis_id: str,
                write_paths: list[str] | tuple[str, ...] | None = None
                ) -> tuple[dict[str, Any], dict[str, Any]]:
        spec = self.graph.campaign_spec()
        if not isinstance(spec, OptimizationCampaignSpecV2):
            raise V2OptimizationError("v2 optimizer requires a persisted Optimization campaign")
        if spec.feature_flags.profile != "FULL" or self.role_registry.digest() != spec.role_registry_digest:
            raise V2OptimizationError("campaign is not bound to this complete pinned role registry")
        try:
            role = self.role_registry.role("optimizer")
        except RoleContractError as exc:
            raise V2OptimizationError("pinned role registry has no optimizer role") from exc
        protocol_authority = EvaluatorProtocolAuthority(self.graph.conn)
        try:
            protocol_proposal, _ = protocol_authority.approved_protocol(spec.evaluation.approval_digest)
            protocol_authority.require_frozen_reference(
                spec.evaluation.protocol_id, spec.evaluation.version,
                spec.evaluation.content_sha256, spec.evaluation.approval_digest,
                campaign_id=self.campaign_id,
            )
        except Exception as exc:
            raise V2OptimizationError("campaign evaluator protocol is not independently frozen") from exc
        source_snapshot_id = protocol_proposal.protocol.source_snapshot_id
        snapshot = self.graph.scheduling_snapshot()
        node = next((item for item in snapshot["nodes"] if item["node_id"] == node_id), None)
        if (node is None or node["phase"] != "OPTIMIZATION"
                or node["status"] not in {"PENDING", "READY"} or not node["ready"]
                or (node["payload"].get("candidate_snapshot_id") is not None
                    and node["payload"].get("last_phase_transition", {}).get("guard") != "REGRESSION_FAILED")):
            raise V2OptimizationError("optimizer job requires an unblocked candidate-proposal node")
        if not hypothesis_id or len(hypothesis_id) > 120:
            raise V2OptimizationError("selected hypothesis identity is invalid")
        paths = tuple(sorted({_safe_path(item) for item in visible_paths}))
        if not paths or len(paths) > 16:
            raise V2OptimizationError("optimizer requires 1 to 16 exact visible source paths")
        writes = tuple(sorted({_safe_path(item) for item in
                               (visible_paths if write_paths is None else write_paths)}))
        if (not writes or len(writes) > 16 or not set(writes) <= set(paths)):
            raise V2OptimizationError("optimizer write paths must be a nonempty subset of visible paths")
        repo = Path(spec.source_repo).resolve()
        if not repo.is_dir() or repo.is_symlink():
            raise V2OptimizationError("approved source repository is unavailable")
        if _run_git(["rev-parse", "--verify", "--end-of-options",
                     f"{source_snapshot_id}^{{commit}}"], repo).lower() != source_snapshot_id:
            raise V2OptimizationError("approved evaluator source snapshot is unavailable")
        for path in paths:
            if not any(fnmatch.fnmatchcase(path, pattern)
                       for pattern in spec.autonomy.capability_policy.read_paths):
                raise V2OptimizationError("source visibility exceeds the campaign read policy")
            scope = getattr(spec.autonomy, "scope", None)
            if scope is not None and not any(
                path == prefix or path.startswith(prefix.rstrip("/") + "/")
                for prefix in scope.allowed_paths
            ):
                raise V2OptimizationError("source visibility exceeds the approved autonomy scope")
        for path in writes:
            if not any(fnmatch.fnmatchcase(path, pattern)
                       for pattern in spec.autonomy.capability_policy.write_paths):
                raise V2OptimizationError("candidate writes exceed the campaign write policy")

        spec_hash = spec.approved_hash()
        budget = BudgetLedger(self.queue.path)
        try:
            budget_snapshot = budget.snapshot(self.campaign_id)
        finally:
            budget.close()
        scheduler = HybridScheduler(self.graph)
        plan = scheduler.plan(budget=budget_snapshot)
        item = next((candidate for candidate in plan.candidates
                     if candidate.node_id == node_id and candidate.phase == "OPTIMIZATION"
                     and candidate.lane == "OPTIMIZATION_WORKER"), None)
        if item is None:
            raise V2OptimizationError(f"candidate is not schedulable: {plan.reason}")
        if node["status"] == "PENDING":
            ready = scheduler.mark_ready(
                node_id, expected_node_revision=int(node["revision"]), expected_phase="OPTIMIZATION",
            )
            node_revision = int(ready["node_revision"])
        else:
            node_revision = int(node["revision"])

        team_request = TeamRequestV1(
            campaign_id=self.campaign_id, spec_hash=spec_hash,
            requested_instances={"optimizer": 1, "integration_verifier": 1},
            available_agent_calls=spec.budget.max_agent_calls,
            available_model_tokens=spec.budget.max_model_tokens,
        )
        try:
            built = TeamFactoryV1.build(
                self.role_registry, team_request,
                expected_registry_digest=spec.role_registry_digest,
            )
            authority = TeamManifestAuthority(self.queue.path)
            try:
                try:
                    manifest = authority.load(
                        self.campaign_id, spec_hash=spec_hash,
                        registry_digest=spec.role_registry_digest,
                    )
                except RoleContractError as exc:
                    if "has no pinned" not in str(exc):
                        raise
                    manifest = authority.pin(built)
                if manifest.manifest_digest != built.manifest_digest:
                    raise RoleContractError("pinned optimizer manifest changed")
            finally:
                authority.close()
        except RoleContractError as exc:
            raise V2OptimizationError(str(exc)) from exc
        member = next((item for item in manifest.members if item.role.role_id == "optimizer"), None)
        if member is None:
            raise V2OptimizationError("pinned team manifest has no optimizer instance")
        if any(getattr(member.role, field) != getattr(role, field) for field in (
            "role_id", "adapter", "provider", "model", "model_version",
            "prompt_digest", "capability_policy", "context_fields",
        )):
            raise V2OptimizationError("resolved optimizer role differs from the pinned registry")

        payload = node["payload"]
        context_values = {
            "node_id": {"value": node_id, "classification": "PUBLIC_NODE",
                        "source_ref": f"graph://nodes/{node_id}"},
            "node_phase": {"value": "OPTIMIZATION", "classification": "PUBLIC_NODE",
                           "source_ref": f"graph://nodes/{node_id}"},
            "public_task": {"value": spec.goal, "classification": "PUBLIC_SPEC",
                            "source_ref": "campaign://spec/goal"},
            "constraints": {"value": tuple(
                                item[:2000] for item in self._optimizer_constraints(payload)
                            )[:128],
                            "classification": "PUBLIC_NODE", "source_ref": f"graph://nodes/{node_id}"},
            "evidence_refs": {"value": tuple(str(ref) for ref in (
                                  *payload.get("evidence_refs", ()),
                                  *payload.get("last_phase_transition", {}).get("evidence_refs", ()),
                              ))[:64],
                              "classification": "PUBLIC_NODE", "source_ref": f"graph://nodes/{node_id}"},
            "dev_summary": {"value": str(payload.get("dev_summary", ""))[:12000],
                            "classification": "PUBLIC_NODE", "source_ref": f"graph://nodes/{node_id}"},
        }
        role_context = build_role_context(
            member,
            RoleContextSourceV1(
                campaign_goal={"value": spec.goal, "classification": "PUBLIC_SPEC",
                               "source_ref": "campaign://spec/goal"},
                fields=context_values,
            ),
        )
        binding = manifest.job_binding(
            member.instance_id,
            action_id="optimization:" + _sha256(f"{node_id}\0{hypothesis_id}".encode())[:32],
            context_digest=role_context.digest(),
        )
        task_policy = spec.autonomy.capability_policy.model_copy(
            update={"read_paths": paths, "write_paths": writes},
        )
        role_policy = role.capability_policy
        role_binding_digest = _sha256(_canonical(binding).encode())
        capability_manifest = {
            "visible_paths": list(paths), "write_paths": list(writes),
            "source_snapshot_id": source_snapshot_id,
            "node_revision": node_revision,
            "team_manifest_digest": manifest.manifest_digest,
            "role_binding_digest": role_binding_digest,
            "context_digest": role_context.digest(),
        }
        identity = _sha256(_canonical({
            "campaign_id": self.campaign_id, "node_id": node_id,
            "hypothesis_id": hypothesis_id, "source_snapshot_id": source_snapshot_id,
            "spec_hash": spec_hash, "team_manifest_digest": manifest.manifest_digest,
            "node_revision": node_revision, "visible_paths": paths,
            "write_paths": writes,
        }).encode())
        job_id = f"opt-{identity[:40]}"
        final_grant = PermissionEngine.derive(
            campaign_id=self.campaign_id, job_id=job_id, spec_hash=spec_hash,
            scope_digest=spec.autonomy.scope_digest,
            campaign=spec.autonomy.capability_policy, role=role_policy, task=task_policy,
        )
        if (any(not final_grant.allows_path("read", path) for path in paths)
                or any(not final_grant.allows_path("write", path) for path in writes)):
            raise V2OptimizationError("the intersected capability grant rejects the requested file scope")
        authority = CapabilityAuthority(self.queue.path)
        try:
            authority.issue(final_grant)
        finally:
            authority.close()
        capability_manifest["grant_digest"] = final_grant.grant_digest
        # Do not put the candidate repo path in any model-visible field. This
        # request is trusted Core queue state and never forwarded wholesale.
        request = {
            "schema": "v2-optimization-attempt-v1", "campaign_id": self.campaign_id,
            "node_id": node_id, "hypothesis_id": hypothesis_id,
            "source_snapshot_id": source_snapshot_id, "source_repo": str(repo),
            "spec_hash": spec_hash, "node_revision": node_revision,
            "capability_manifest": capability_manifest,
            "capability_digest": final_grant.grant_digest,
            "capability_grant": final_grant.model_dump(mode="json"),
            "role_binding": binding,
            "role_context": role_context.model_dump(mode="json", exclude_unset=True),
            "team_manifest_digest": manifest.manifest_digest,
            "role_registry_digest": manifest.registry_digest,
        }
        return request, {
            "member": member, "manifest": manifest, "role": role,
            "spec": spec, "paths": paths, "write_paths": writes,
            "source_snapshot_id": source_snapshot_id,
            "grant": final_grant,
        }

    @staticmethod
    def _optimizer_constraints(payload: dict[str, Any]) -> tuple[str, ...]:
        parts = [str(payload.get("hypothesis", ""))]
        prior_guard = payload.get("last_phase_transition", {}).get("guard")
        if prior_guard == "REGRESSION_FAILED":
            parts.append(
                "The previous candidate did not pass independent verification. "
                "Use the persisted research evidence to form a distinct next candidate."
            )
        return tuple(item for item in parts if item)

    def enqueue(self, *, node_id: str, visible_paths: list[str] | tuple[str, ...],
                hypothesis_id: str,
                write_paths: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        request, pinned = self._pinned(node_id, visible_paths, hypothesis_id, write_paths)
        member = pinned["member"]
        return self.queue.enqueue(
            job_id=self._job_id(request), enqueue_key=self._job_id(request),
            campaign_id=self.campaign_id,
            action_id=request["role_binding"]["action_id"],
            resource="agent_calls", upper_bound=1,
            resource_bounds=self._resource_bounds(pinned),
            request=request,
            role_allocation={
                "manifest_digest": pinned["manifest"].manifest_digest,
                "role_instance_id": member.instance_id, "role_id": "optimizer",
                "calls_limit": member.role.calls_per_instance,
                "tokens_limit": member.role.tokens_per_instance,
                "token_upper_bound": max(1, member.role.tokens_per_instance // member.role.calls_per_instance),
            },
        )

    @staticmethod
    def _resource_bounds(pinned: dict[str, Any]) -> dict[str, int]:
        bounds = {"agent_calls": 1, "experiments": 1, "attempts": 1}
        spec = pinned["spec"]
        if spec.budget.max_model_tokens is not None:
            role = pinned["role"]
            bounds["model_tokens"] = max(
                1, role.tokens_per_instance // role.calls_per_instance,
            )
        return bounds

    @staticmethod
    def _job_id(request: dict[str, Any]) -> str:
        digest = _sha256(_canonical({
            "campaign_id": request["campaign_id"], "node_id": request["node_id"],
            "hypothesis_id": request["hypothesis_id"], "source_snapshot_id": request["source_snapshot_id"],
            "spec_hash": request["spec_hash"], "team_manifest_digest": request["team_manifest_digest"],
            "node_revision": request["node_revision"],
            "visible_paths": request["capability_manifest"]["visible_paths"],
            "write_paths": request["capability_manifest"]["write_paths"],
        }).encode())
        return f"opt-{digest[:40]}"

    def run_one(self, *, node_id: str, visible_paths: list[str] | tuple[str, ...],
                hypothesis_id: str, model_reply: OptimizerModelReply,
                write_paths: list[str] | tuple[str, ...] | None = None,
                worker_id: str = "v2-optimizer", lease_seconds: int = 300) -> dict[str, Any]:
        job = self.enqueue(node_id=node_id, visible_paths=visible_paths,
                           hypothesis_id=hypothesis_id, write_paths=write_paths)
        if job["state"] == "COMPLETED":
            return self.publish_completed(job["job_id"])
        if job["state"] != "READY":
            raise V2OptimizationError(
                f"optimizer job is {job['state']}; uncertain work is never dispatched twice"
            )
        spec = self.graph.campaign_spec()
        manifest_authority = TeamManifestAuthority(self.queue.path)
        try:
            manifest = manifest_authority.load(
                self.campaign_id, spec_hash=spec.approved_hash(),
                registry_digest=spec.role_registry_digest,
            )
        finally:
            manifest_authority.close()
        member = manifest.member(job["request"]["role_binding"]["role_instance_id"])
        invoke = make_optimizer_invoke(model_reply)
        executor = PinnedRoleExecutorV1.from_manifest(manifest, member.instance_id, invoke)
        lease = self.queue.claim(job["job_id"], worker_id, lease_seconds=lease_seconds)
        fence = int(lease["fence"])
        try:
            workspace, artifacts = self._workspace(lease)
            self.queue.mark_dispatched(job["job_id"], worker_id, fence)
            grant = CapabilityGrantV1.model_validate(lease["request"]["capability_grant"])
            authority = CapabilityAuthority(self.queue.path)
            try:
                backend = LocalBackend() if grant.backend == "LOCAL" else BubblewrapBackend()
                gateway = ToolGateway(
                    queue=self.queue, authority=authority, grant=grant, fence=fence,
                    workspace=workspace, artifacts=artifacts, backend=backend,
                )
                output = executor.dispatch(dict(lease["request"]), gateway)
                proposal = self._validate_output(output, job["request"], workspace)
                candidate_id = gateway.commit_candidate(
                    proposal["changed_paths"],
                    lambda paths: self._commit_candidate(
                        job["request"], workspace, paths,
                        build_id=f"{job['job_id']}-f{fence}",
                    ),
                )
            finally:
                authority.close()
            result = {
                "schema": "v2-optimization-result-v1", "node_id": node_id,
                "hypothesis_id": proposal["hypothesis_id"],
                "rationale": proposal["rationale"],
                "limitations": proposal["limitations"],
                "source_snapshot_id": job["request"]["source_snapshot_id"],
                "candidate_snapshot_id": candidate_id,
                "changed_paths": proposal["changed_paths"],
                "model_tokens": proposal["model_tokens"],
                "model_token_usage_quality": "actual" if proposal["model_tokens"] is not None else "UNKNOWN",
            }
            output_digest = _sha256(_canonical(result).encode())
            receipt_digest = _sha256(_canonical({
                "job_id": job["job_id"], "fence": fence,
                "request_hash": lease["request_hash"], "output_digest": output_digest,
            }).encode())
            actual_usage: dict[str, int | None] = {
                "agent_calls": 1, "experiments": 1, "attempts": 1,
            }
            if "model_tokens" in lease["resource_bounds"]:
                actual_usage["model_tokens"] = proposal["model_tokens"]
            self.queue.complete(
                job["job_id"], worker_id, fence, output_digest=output_digest,
                receipt_digest=receipt_digest, actual_amount=1,
                actual_usage=actual_usage,
                result_payload=result,
            )
        except Exception as exc:
            # Once dispatched, preserve the reservation as UNKNOWN. In
            # particular, never replay a possibly billed provider request.
            try:
                self.queue.cancel(job["job_id"], reason=f"optimizer failed: {type(exc).__name__}")
            except WorkerQueueError:
                pass
            raise
        return self.publish_completed(job["job_id"])

    @staticmethod
    def _validate_output(raw: Any, request: dict[str, Any], workspace: Path) -> dict[str, Any]:
        if (not isinstance(raw, dict) or raw.get("schema") != "v2-optimization-candidate-v1"
                or raw.get("hypothesis_id") != request["hypothesis_id"]):
            raise V2OptimizationError("optimizer result does not match its durable request")
        paths = raw.get("changed_paths")
        if (not isinstance(paths, list) or not paths or len(paths) > 16
                or len(paths) != len(set(paths))):
            raise V2OptimizationError("optimizer result has no unique changed path set")
        allowed = set(request["capability_manifest"]["write_paths"])
        for path in paths:
            normalized = _safe_path(path)
            if normalized not in allowed:
                raise V2OptimizationError("optimizer changed a path outside its pinned task scope")
            file = workspace.joinpath(*PurePosixPath(normalized).parts)
            if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(workspace.resolve()):
                raise V2OptimizationError("candidate change is missing or escaped the worker workspace")
        limitations = raw.get("limitations", [])
        if (not isinstance(raw.get("rationale"), str) or not raw["rationale"].strip()
                or len(raw["rationale"]) > 4000 or not isinstance(limitations, list)
                or len(limitations) > 32 or any(not isinstance(item, str) or not item.strip()
                                                or len(item) > 2000 for item in limitations)):
            raise V2OptimizationError("optimizer narrative fields are malformed or oversized")
        model_tokens = raw.get("model_tokens")
        if (model_tokens is not None and
                (isinstance(model_tokens, bool) or not isinstance(model_tokens, int) or model_tokens < 0)):
            raise V2OptimizationError("provider usage receipt is malformed")
        return {
            "hypothesis_id": request["hypothesis_id"],
            "rationale": raw["rationale"], "limitations": sorted(set(limitations)),
            "changed_paths": sorted(paths),
            "model_tokens": model_tokens,
        }

    def _commit_candidate(self, request: dict[str, Any], workspace: Path,
                          paths: tuple[str, ...], *, build_id: str) -> str:
        source_repo = Path(request["source_repo"]).resolve()
        builds = _safe_mkdir(self.state_root, "v2o", "b")
        # The durable state root can be deeply nested (notably under Windows
        # pytest temp roots). Keep transient clone path components short so
        # subprocess cwd and Git's internal hook paths remain below MAX_PATH.
        build = builds / hashlib.sha256(build_id.encode("utf-8")).hexdigest()[:24]
        if build.exists():
            raise V2OptimizationError("candidate build workspace already exists")
        # Keep the durable repository path short too: publish_completed later
        # asks Git to open it, and Git for Windows rejects deep paths even
        # when `core.longpaths` is enabled for the transient clone.
        snapshots = _safe_mkdir(self.state_root, "s", self.campaign_id)
        try:
            # pytest and user campaign state can be nested deeply on Windows;
            # Git for Windows otherwise fails while materializing template
            # hook files below .git/hooks once the full path exceeds MAX_PATH.
            subprocess.run(["git", "-c", "core.longpaths=true", "clone",
                            "--no-hardlinks", "--quiet",
                            str(source_repo), str(build)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            _run_git(["checkout", "--detach", request["source_snapshot_id"]], build)
            for relative in paths:
                safe = _safe_path(relative)
                source = workspace.joinpath(*PurePosixPath(safe).parts)
                target = build.joinpath(*PurePosixPath(safe).parts)
                if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(workspace.resolve()):
                    raise V2OptimizationError("candidate file is not a regular worker-produced file")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise V2OptimizationError("candidate target is not a regular file")
                shutil.copyfile(source, target)
            _run_git(["add", "--", *paths], build)
            staged = tuple(sorted(filter(None, _run_git(
                ["diff", "--cached", "--name-only", "--"], build,
            ).splitlines())))
            if staged != tuple(sorted(paths)):
                raise V2OptimizationError("staged candidate paths differ from the authorized change set")
            _run_git(["-c", "user.name=BananLoop v2 Core",
                      "-c", "user.email=bananloop-v2@example.invalid",
                      "commit", "--quiet", "-m", f"v2 candidate {request['hypothesis_id'][:60]}"], build)
            candidate_id = _run_git(["rev-parse", "--verify", "HEAD^{commit}"], build).lower()
            if len(candidate_id) not in {40, 64} or any(c not in "0123456789abcdef" for c in candidate_id):
                raise V2OptimizationError("Git returned a noncanonical candidate commit ID")
            _run_git(["merge-base", "--is-ancestor", request["source_snapshot_id"], candidate_id], build)
            final = snapshots / hashlib.sha256(candidate_id.encode("ascii")).hexdigest()[:16]
            if final.exists():
                raise V2OptimizationError("candidate snapshot ID is already registered")
            os.replace(build, final)
            return candidate_id
        except V2OptimizationError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None)
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            if not isinstance(detail, str) or not detail.strip():
                detail = str(exc)
            detail = " ".join(detail.split())[:500]
            raise V2OptimizationError(
                f"trusted Core could not commit the candidate snapshot "
                f"({type(exc).__name__}: {detail})"
            ) from exc

    def publish_completed(self, job_id: str) -> dict[str, Any]:
        job = self.queue.get_job(job_id)
        durable = self.queue.get_result(job_id)
        result = durable.get("result")
        if job["state"] != "COMPLETED" or not isinstance(result, dict):
            raise V2OptimizationError("optimizer job has no durable completed result")
        if (_sha256(_canonical(result).encode()) != durable["output_digest"]
                or int(durable["fence"]) != int(job["fence"])
                or result.get("schema") != "v2-optimization-result-v1"
                or result.get("node_id") != job["request"].get("node_id")
                or result.get("source_snapshot_id") != job["request"].get("source_snapshot_id")):
            raise V2OptimizationError("durable optimizer result failed integrity or scope validation")
        candidate_id = result.get("candidate_snapshot_id")
        snapshot_key = hashlib.sha256(str(candidate_id).encode("ascii")).hexdigest()[:16]
        snapshot_repo = (self.state_root / "s" / self.campaign_id / snapshot_key).resolve()
        expected_root = (self.state_root / "s" / self.campaign_id).resolve()
        if (not snapshot_repo.is_relative_to(expected_root) or snapshot_repo.is_symlink()
                or not snapshot_repo.is_dir()):
            raise V2OptimizationError("durable candidate repository is unavailable")
        if _run_git(["rev-parse", "HEAD^{commit}"], snapshot_repo).lower() != candidate_id:
            raise V2OptimizationError("candidate repository HEAD differs from its durable result")

        node_id = result["node_id"]
        child_id = f"{node_id}-candidate-{job_id.removeprefix('opt-')[:12]}"
        key = f"publish-optimization:{job_id}:{durable['output_digest']}"
        replay = self.graph.command_response(key)
        if replay is not None:
            return {"status": "CANDIDATE_CREATED", "job_id": job_id,
                    "candidate_node_id": child_id, "candidate_snapshot_id": candidate_id,
                    "replayed": True}
        snapshot = self.graph.scheduling_snapshot()
        parent = next((item for item in snapshot["nodes"] if item["node_id"] == node_id), None)
        if (parent is None or parent["phase"] != "OPTIMIZATION" or parent["status"] != "READY"
                or int(parent["revision"]) != int(job["request"]["node_revision"])):
            raise V2OptimizationError("optimizer parent node changed before candidate publication")
        candidate_payload = {
            "source_snapshot_id": result["source_snapshot_id"],
            "candidate_snapshot_id": candidate_id,
            "hypothesis_id": result["hypothesis_id"],
            "candidate_rationale": result["rationale"],
            "parent_node_id": node_id,
            "candidate_limitations": result["limitations"],
            "scheduler": {"priority": int(parent["payload"].get("scheduler", {}).get("priority", 0)),
                          "upper_bound": 1},
        }
        self.graph.mutate(MutateGraph(
            command_id=key, idempotency_key=key,
            expected_graph_revision=self.graph.graph_revision(),
            changes=(
                AddNode(type="AddNode", node_id=child_id, phase="OPTIMIZATION",
                        payload=candidate_payload),
                AddEdge(type="AddEdge", source_id=child_id, target_id=node_id,
                        edge_type="DERIVED_FROM"),
                TransitionNodeStatus(
                    type="TransitionNodeStatus", node_id=node_id,
                    expected_node_revision=int(parent["revision"]),
                    expected_status="READY", to_status="COMPLETED",
                    guard="VERIFIED_COMPLETION", actor="CORE",
                    evidence_refs=(f"git://candidate/{candidate_id}",),
                ),
            ),
        ))
        return {"status": "CANDIDATE_CREATED", "job_id": job_id,
                "candidate_node_id": child_id, "candidate_snapshot_id": candidate_id,
                "changed_paths": result["changed_paths"], "replayed": False}

    def _workspace(self, job: dict[str, Any]) -> tuple[Path, Path]:
        request = job["request"]
        roots = _safe_mkdir(self.state_root, "v2_optimization", "workers")
        artifacts_root = _safe_mkdir(self.state_root, "v2_optimization", "artifacts")
        workspace = roots / f"{job['job_id']}-f{job['fence']}"
        artifacts = artifacts_root / f"{job['job_id']}-f{job['fence']}"
        workspace.mkdir(mode=0o700)
        artifacts.mkdir(mode=0o700)
        archive_file = tempfile.TemporaryFile()
        try:
            subprocess.run(
                ["git", "-C", request["source_repo"], "archive", "--format=tar",
                 request["source_snapshot_id"], "--", *request["capability_manifest"]["visible_paths"]],
                check=True, stdout=archive_file, stderr=subprocess.PIPE, timeout=60,
            )
            archive_file.seek(0)
            with tarfile.open(fileobj=archive_file, mode="r:") as archive:
                for item in archive.getmembers():
                    relative = _safe_path(item.name)
                    if not (item.isfile() or item.isdir()):
                        raise V2OptimizationError("approved source archive contains a link or special file")
                    target = workspace.joinpath(*PurePosixPath(relative).parts)
                    if not target.resolve().is_relative_to(workspace.resolve()):
                        raise V2OptimizationError("source archive member escapes the workspace")
                    if item.isdir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        source = archive.extractfile(item)
                        if source is None:
                            raise V2OptimizationError("source archive file could not be read")
                        target.write_bytes(source.read())
                        target.chmod(0o600)
        except V2OptimizationError:
            raise
        except (OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
            raise V2OptimizationError("approved source workspace could not be materialized") from exc
        finally:
            archive_file.close()
        return workspace, artifacts


class V2OptimizationRuntime:
    """Drive candidate creation, trusted DEV evaluation and independent verification.

    The caller supplies the already-approved one-shot optimizer transport and
    the separately pinned tool-free integration-verifier executor. This class
    never invokes the legacy Engine and never dispatches a model during DEV or
    VALIDATION evaluation.
    """

    def __init__(self, *, graph: Any, queue: DurableWorkerQueue, campaign_id: str,
                 state_root: str | Path, role_registry: RoleRegistryV1,
                 trusted_evaluator: Any, integration_verifier: Any):
        self.graph, self.queue, self.campaign_id = graph, queue, campaign_id
        self.graph.bind_campaign_id(campaign_id)
        self.state_root = Path(state_root).resolve()
        self.role_registry = role_registry
        self.trusted_evaluator = trusted_evaluator
        self.candidate_adapter = V2OptimizationCandidateAdapter(
            graph=graph, queue=queue, campaign_id=campaign_id,
            state_root=state_root, role_registry=role_registry,
        )
        self.lifecycle = OptimizationAttemptCoordinator(
            graph=graph, trusted_evaluator=trusted_evaluator,
            integration_verifier=integration_verifier,
        )

    def plan(self):
        spec = self.graph.campaign_spec()
        if not isinstance(spec, OptimizationCampaignSpecV2):
            raise V2OptimizationError("v2 Optimization runtime requires a persisted Optimization campaign")
        if (spec.feature_flags.profile != "FULL"
                or spec.role_registry_digest != self.role_registry.digest()):
            raise V2OptimizationError("campaign is not bound to the full pinned Optimization role registry")
        control = self.queue.campaign_control(self.campaign_id)
        if control["state"] == "PAUSED":
            return SchedulerPlan((), "PAUSED", self.graph.graph_revision())
        if control["state"] == "STOPPED":
            return SchedulerPlan((), "STOP_REQUESTED", self.graph.graph_revision())
        budget = BudgetLedger(self.queue.path)
        try:
            snapshot = budget.snapshot(self.campaign_id)
        finally:
            budget.close()
        return HybridScheduler(self.graph).plan(budget=snapshot)

    def pause(self, *, reason: str = "owner requested pause") -> dict[str, Any]:
        return self.queue.set_campaign_control(self.campaign_id, "PAUSED", reason=reason)

    def resume(self) -> dict[str, Any]:
        return self.queue.set_campaign_control(self.campaign_id, "RUNNING")

    def stop(self, *, reason: str = "owner requested stop") -> dict[str, Any]:
        return self.queue.set_campaign_control(self.campaign_id, "STOPPED", reason=reason)

    def run_one(
        self, *, visible_paths: list[str] | tuple[str, ...],
        write_paths: list[str] | tuple[str, ...] | None = None,
        model_reply: OptimizerModelReply | None = None,
        integration_verifier_executor: Any | None = None,
        hypothesis_id: str | None = None,
        worker_id: str = "v2-optimization",
    ) -> dict[str, Any]:
        """Advance one scheduler-selected v2 Optimization or Verification node."""
        finalized = self.graph.conn.execute(
            "SELECT receipt_json FROM evaluator_run_receipts WHERE campaign_id=?",
            (self.campaign_id,),
        ).fetchall()
        if any(json.loads(row[0]).get("tier") == "holdout" for row in finalized):
            raise V2OptimizationError("campaign is finalized; no work may follow HOLDOUT")
        plan = self.plan()
        if not plan.candidates:
            return {"status": plan.reason, "campaign_id": self.campaign_id}
        selected = plan.candidates[0]
        scheduler = HybridScheduler(self.graph)
        node = next(item for item in self.graph.scheduling_snapshot()["nodes"]
                    if item["node_id"] == selected.node_id)
        if selected.lane == "OPTIMIZATION_WORKER":
            candidate_id = node["payload"].get("candidate_snapshot_id")
            last_guard = node["payload"].get("last_phase_transition", {}).get("guard")
            if candidate_id is None or last_guard == "REGRESSION_FAILED":
                if model_reply is None:
                    raise V2OptimizationError("a one-shot pinned optimizer transport is required for candidate creation")
                next_hypothesis = hypothesis_id or node["payload"].get("hypothesis_id")
                if not isinstance(next_hypothesis, str) or not next_hypothesis:
                    next_hypothesis = "hyp-" + _sha256(
                        f"{selected.node_id}\0{selected.node_revision}".encode(),
                    )[:24]
                return self.candidate_adapter.run_one(
                    node_id=selected.node_id, visible_paths=visible_paths,
                    write_paths=write_paths,
                    hypothesis_id=next_hypothesis, model_reply=model_reply,
                    worker_id=worker_id,
                )
            if node["status"] == "PENDING":
                scheduler.mark_ready(
                    selected.node_id, expected_node_revision=int(node["revision"]),
                    expected_phase="OPTIMIZATION",
                )
            receipt = self.lifecycle.evaluate_candidate(
                node_id=selected.node_id, worker_id=worker_id,
            )
            return {"status": "DEV_EVALUATED", "campaign_id": self.campaign_id,
                    "node_id": selected.node_id, "candidate_snapshot_id": candidate_id,
                    "evaluator_receipt_digest": receipt.receipt_digest,
                    "tier": receipt.tier}

        if selected.lane != "INDEPENDENT_VERIFIER" or selected.phase != "VERIFICATION":
            raise V2OptimizationError(f"scheduler selected unsupported v2 lane {selected.lane!r}")
        if integration_verifier_executor is None:
            raise V2OptimizationError("a separate pinned integration-verifier executor is required")
        if node["status"] == "PENDING":
            scheduler.mark_ready(
                selected.node_id, expected_node_revision=int(node["revision"]),
                expected_phase="VERIFICATION",
            )
        baseline = self._baseline_validation_receipt(worker_id=worker_id)
        receipt = self.lifecycle.verify_candidate(
            node_id=selected.node_id,
            baseline_run_receipt_digest=baseline.receipt_digest,
            executor=integration_verifier_executor, worker_id=worker_id,
        )
        promotion = None
        if receipt.decision == "PASS":
            spec = self.graph.campaign_spec()
            from .champion_promotion import ChampionPromotionService

            champion = ChampionPromotionService(self.graph.conn, self.graph.evaluator_protocols)
            current = champion.initialize(
                campaign_id=self.campaign_id,
                source_snapshot_id=receipt.source_snapshot_id,
            )
            promotion = self.lifecycle.promote_candidate(
                node_id=selected.node_id,
                expected_champion_snapshot_id=current["snapshot_id"],
                expected_champion_revision=current["revision"],
            )
        return {"status": "INDEPENDENT_VERIFICATION_" + receipt.decision,
                "campaign_id": self.campaign_id, "node_id": selected.node_id,
                "integration_receipt_digest": receipt.receipt_digest,
                "champion": promotion}

    def run_campaign(
        self, *, visible_paths: list[str] | tuple[str, ...],
        write_paths: list[str] | tuple[str, ...] | None = None,
        model_reply: OptimizerModelReply | None = None,
        integration_verifier_executor: Any | None = None,
        max_steps: int = 20,
        worker_id: str = "v2-optimization",
    ) -> dict[str, Any]:
        """Run scheduler-selected Optimization/evaluation/verification steps.

        Every step is committed through the durable queue before the scheduler
        is consulted again. The caller supplies pinned role transports but
        cannot select a lane or bypass the trusted evaluator.
        """
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 100:
            raise V2OptimizationError("max_steps must be an integer from 1 to 100")
        steps: list[dict[str, Any]] = []
        for _ in range(max_steps):
            plan = self.plan()
            if not plan.candidates:
                if plan.reason == "ALL_NODES_COMPLETE":
                    result = {
                        "status": plan.reason, "campaign_id": self.campaign_id,
                        "steps_this_call": len(steps), "steps": steps,
                        "graph_revision": plan.graph_revision,
                    }
                    result["holdout"] = self._finalize_holdout(worker_id=worker_id)
                    return self._finish_campaign_run(result)
                return self._finish_campaign_run({
                    "status": plan.reason, "campaign_id": self.campaign_id,
                    "steps_this_call": len(steps), "steps": steps,
                    "graph_revision": plan.graph_revision,
                })
            try:
                result = self.run_one(
                    visible_paths=visible_paths, write_paths=write_paths,
                    model_reply=model_reply,
                    integration_verifier_executor=integration_verifier_executor,
                    worker_id=worker_id,
                )
            except WorkerBudgetError:
                current = self.plan()
                return self._finish_campaign_run({
                    "status": "BUDGET_EXHAUSTED", "campaign_id": self.campaign_id,
                    "steps_this_call": len(steps), "steps": steps,
                    "graph_revision": current.graph_revision,
                })
            except WorkerQueueError as exc:
                current = self.plan()
                if current.reason in {"PAUSED", "STOP_REQUESTED"}:
                    return self._finish_campaign_run({
                        "status": current.reason, "campaign_id": self.campaign_id,
                        "steps_this_call": len(steps), "steps": steps,
                        "graph_revision": current.graph_revision,
                    })
                raise V2OptimizationError("durable Optimization worker failed") from exc
            steps.append(result)
        current = self.plan()
        if current.reason == "ALL_NODES_COMPLETE":
            result = {
                "status": current.reason, "campaign_id": self.campaign_id,
                "steps_this_call": len(steps), "steps": steps,
                "graph_revision": current.graph_revision,
            }
            result["holdout"] = self._finalize_holdout(worker_id=worker_id)
            return self._finish_campaign_run(result)
        return self._finish_campaign_run({
            "status": current.reason if not current.candidates else "STEP_LIMIT",
            "campaign_id": self.campaign_id,
            "steps_this_call": len(steps), "steps": steps,
            "graph_revision": current.graph_revision,
        })

    def _finalize_holdout(self, *, worker_id: str) -> dict[str, Any]:
        """Run trusted HOLDOUT once the scheduler has completed every node.

        HOLDOUT receipts are returned only in the owner report. They never
        become graph evidence or feed a scheduler, verifier, or agent context.
        Deterministic evaluator job IDs make a retry after a process restart
        reuse completed work; an indeterminate job fails closed.
        """
        from .champion_promotion import ChampionPromotionError, ChampionPromotionService
        from .trusted_evaluator import EvaluatorRunReceiptV1, TrustedEvaluatorError

        spec = self.graph.campaign_spec()
        authority = EvaluatorProtocolAuthority(self.graph.conn)
        proposal, _ = authority.approved_protocol(spec.evaluation.approval_digest)
        if not any(item.tier == "holdout" for item in proposal.protocol.tiers):
            # Public-only evaluators (for example a Harbor validation MCP
            # service) must never be relabeled as HOLDOUT. Record the explicit
            # absence in the owner report without dispatching another evaluator
            # job or touching Harbor's private verifier.
            return {
                "status": "NOT_CONFIGURED", "tier": "holdout",
                "protocol_digest": proposal.protocol.content_digest(),
                "reason": "The independently approved evaluator protocol has no HOLDOUT tier; none was run.",
            }
        source_snapshot_id = proposal.protocol.source_snapshot_id
        champion_service = ChampionPromotionService(self.graph.conn, authority)
        try:
            champion = champion_service.current()
        except ChampionPromotionError:
            champion = champion_service.initialize(
                campaign_id=self.campaign_id, source_snapshot_id=source_snapshot_id,
            )
        if champion["campaign_id"] != self.campaign_id:
            raise V2OptimizationError("HOLDOUT champion belongs to a different campaign")
        try:
            existing = self.graph.conn.execute(
                "SELECT receipt_json FROM evaluator_run_receipts "
                "WHERE campaign_id=? AND candidate_snapshot_id=? ORDER BY rowid DESC",
                (self.campaign_id, champion["snapshot_id"]),
            ).fetchall()
            receipt = next((json.loads(row[0]) for row in existing
                            if json.loads(row[0]).get("tier") == "holdout"), None)
            if receipt is not None:
                receipt_model = EvaluatorRunReceiptV1.model_validate(receipt)
                return {
                    "status": "VERIFIED", "tier": "holdout",
                    "candidate_snapshot_id": receipt["candidate_snapshot_id"],
                    "receipt_digest": receipt["receipt_digest"],
                    "case_count": receipt["case_count"],
                    "repeatability_runs": receipt["repeatability_runs"],
                    "tier_manifest_sha256": receipt["tier_manifest_sha256"],
                    "metric": self.trusted_evaluator.owner_metric_summary(receipt_model),
                    "replayed": True,
                }
            job_ids = self.trusted_evaluator.enqueue_repeatability(
                candidate_snapshot_id=champion["snapshot_id"], tier="holdout",
            )
            for job_id in job_ids:
                job = self.queue.get_job(job_id)
                if job["state"] == "READY":
                    self.trusted_evaluator.run_one(job_id, worker_id=worker_id)
                elif job["state"] != "COMPLETED":
                    raise V2OptimizationError(
                        f"HOLDOUT job is {job['state']}; result is indeterminate"
                    )
            receipt = self.trusted_evaluator.finalize_repeatability(job_ids)
        except (TrustedEvaluatorError, WorkerQueueError) as exc:
            raise V2OptimizationError(
                f"trusted HOLDOUT finalization failed closed: {exc}"
            ) from exc
        if receipt.tier != "holdout" or receipt.candidate_snapshot_id != champion["snapshot_id"]:
            raise V2OptimizationError("HOLDOUT receipt is outside the final champion scope")
        return {
            "status": "VERIFIED", "tier": "holdout",
            "candidate_snapshot_id": receipt.candidate_snapshot_id,
            "receipt_digest": receipt.receipt_digest,
            "case_count": receipt.case_count,
            "repeatability_runs": receipt.repeatability_runs,
            "tier_manifest_sha256": receipt.tier_manifest_sha256,
            "metric": self.trusted_evaluator.owner_metric_summary(receipt),
        }

    def _finish_campaign_run(self, result: dict[str, Any]) -> dict[str, Any]:
        """Persist a bounded public Optimization outcome after each run call."""
        snapshot = self.graph.scheduling_snapshot()
        nodes = []
        for node in snapshot["nodes"]:
            transition = node["payload"].get("last_phase_transition", {})
            nodes.append({
                "node_id": node["node_id"], "phase": node["phase"],
                "status": node["status"],
                "candidate_snapshot_id": node["payload"].get("candidate_snapshot_id"),
                "guard": transition.get("guard"),
                "evidence_refs": list(transition.get("evidence_refs", ())),
            })
        spec = self.graph.campaign_spec()
        from .champion_promotion import ChampionPromotionError, ChampionPromotionService

        try:
            champion_report = ChampionPromotionService(
                self.graph.conn, EvaluatorProtocolAuthority(self.graph.conn),
            ).current()
        except ChampionPromotionError:
            champion_report = None
        baseline_candidate_delta = self._build_baseline_candidate_delta(champion_report)
        evaluation_receipts = []
        for row in self.graph.conn.execute(
            "SELECT receipt_digest,candidate_snapshot_id,receipt_json "
            "FROM evaluator_run_receipts WHERE campaign_id=? ORDER BY rowid",
            (self.campaign_id,),
        ):
            receipt = json.loads(row["receipt_json"])
            if receipt.get("tier") not in {"dev", "validation"}:
                continue
            evaluation_receipts.append({
                "receipt_digest": row["receipt_digest"],
                "candidate_snapshot_id": row["candidate_snapshot_id"],
                "tier": receipt["tier"], "result_digest": receipt["result_digest"],
            })
        integration_evidence = [
            {"evidence_digest": row["evidence_digest"], "decision": row["decision"],
             "baseline_run_receipt_digest": row["baseline_run_receipt_digest"],
             "candidate_run_receipt_digest": row["candidate_run_receipt_digest"]}
            for row in self.graph.conn.execute(
                "SELECT evidence_digest,decision,baseline_run_receipt_digest,"
                "candidate_run_receipt_digest FROM evaluator_integration_evidence "
                "WHERE campaign_id=? ORDER BY rowid", (self.campaign_id,),
            )
        ]
        provider_calls = _provider_call_summaries(self.graph.conn)
        report = {
            "schema_version": 1, "campaign_id": self.campaign_id,
            "mode": "OPTIMIZATION", "goal": spec.goal,
            "status": result["status"], "graph_revision": snapshot["graph_revision"],
            "steps": result.get("steps", []), "nodes": nodes,
            "evaluation_receipts": evaluation_receipts,
            "baseline_candidate_delta": baseline_candidate_delta,
            "integration_evidence": integration_evidence,
            "provider_calls": provider_calls,
            "parent": spec.parent.model_dump(mode="json") if spec.parent else None,
            "champion": champion_report,
            "verification": [step for step in result.get("steps", [])
                             if str(step.get("status", "")).startswith("INDEPENDENT_VERIFICATION_")],
            "limitations": [
                "Exploration evidence remains unverified PUBLIC_DEV evidence.",
                "This report contains public receipt references and no private evaluator data.",
            ],
            "as_of": utc_now(),
        }
        if result.get("holdout") is not None:
            report["holdout"] = result["holdout"]
        reports = self.state_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        target = reports / f"v2-optimization-{self.campaign_id}.json"
        raw = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        _atomic_write_report(target, raw)
        markdown_target = target.with_suffix(".md")
        _atomic_write_report(
            markdown_target, _render_final_report_markdown(report).encode("utf-8"),
        )
        result["report_markdown_path"] = str(markdown_target)
        if spec.parent is not None:
            from .registry import CampaignRegistry, RegistryError

            source_repo = Path(spec.source_repo)
            # The worker queue points at the exact project registry selected by
            # the CLI, including an operator-supplied --state-dir. Reconstruct
            # RuntimePaths from that registry so parent lookup stays inside the
            # same durable campaign store as this child.
            registry_root = Path(self.queue.path).resolve().parent
            registry_paths = RuntimePaths.for_repo(source_repo, registry_root)
            parent_campaign_id = spec.parent.parent_campaign_id
            try:
                parent_state, _ = CampaignRegistry(
                    source_repo, paths=registry_paths,
                ).resolve_readonly(parent_campaign_id)
            except RegistryError:
                # Direct runtime integrations can place the child queue outside
                # the project registry (for example, a prepared benchmark
                # campaign). Preserve the CLI-selected registry as first choice,
                # then resolve an existing parent from the repository default.
                default_paths = RuntimePaths.for_repo(source_repo)
                if default_paths.root == registry_paths.root:
                    raise
                parent_state, _ = CampaignRegistry(
                    source_repo, paths=default_paths,
                ).resolve_readonly(parent_campaign_id)
            parent_db = parent_state / "campaign.sqlite3"
            parent_provider_calls = []
            verifier_row = None
            owner_row = None
            if parent_db.is_symlink():
                raise V2OptimizationError("parent campaign database is unsafe")
            if parent_db.is_file():
                parent_conn = sqlite3.connect(parent_db.resolve().as_uri() + "?mode=ro", uri=True)
                parent_conn.row_factory = sqlite3.Row
                try:
                    parent_identity = parent_conn.execute(
                        "SELECT spec_hash FROM campaigns WHERE id=1",
                    ).fetchone()
                    parent_spec_hash = getattr(spec.parent, "parent_spec_hash", None)
                    if (parent_identity is None or (parent_spec_hash is not None
                            and parent_identity["spec_hash"] != parent_spec_hash)):
                        raise V2OptimizationError(
                            "parent campaign spec identity does not match frozen child lineage",
                        )
                    parent_provider_calls = _provider_call_summaries(parent_conn)
                    tables = {row[0] for row in parent_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'",
                    )}
                    verifier_row = (parent_conn.execute(
                        "SELECT proposal_digest,protocol_digest,approval_digest,decision,receipt_digest "
                        "FROM evaluator_verifier_receipts WHERE campaign_id=? "
                        "ORDER BY rowid DESC LIMIT 1", (spec.parent.parent_campaign_id,),
                    ).fetchone() if "evaluator_verifier_receipts" in tables else None)
                    pivot_decision_id = getattr(spec.parent, "pivot_decision_id", None)
                    if pivot_decision_id and "v2_pivot_decisions" in tables:
                        owner_row = parent_conn.execute(
                            "SELECT proposal_id,digest,decision,status FROM v2_pivot_decisions "
                            "WHERE request_id=?", (pivot_decision_id,),
                        ).fetchone()
                finally:
                    parent_conn.close()
            parent_report_path = (parent_state / "reports" /
                                  f"v2-exploration-{spec.parent.parent_campaign_id}.json")
            exploration_report = None
            if parent_report_path.is_file():
                exploration_report = json.loads(parent_report_path.read_text(encoding="utf-8"))
            else:
                parent_db = parent_state / "campaign.sqlite3"
                if parent_db.is_file():
                    from .graph_store import GraphRepository

                    parent_conn = sqlite3.connect(parent_db.resolve().as_uri() + "?mode=ro", uri=True)
                    try:
                        parent_graph = GraphRepository(parent_conn)
                        if parent_graph.campaign_id != spec.parent.parent_campaign_id:
                            raise V2OptimizationError("parent graph campaign identity does not match frozen child lineage")
                        parent_snapshot = parent_graph.scheduling_snapshot()
                        parent_spec = parent_graph.campaign_spec()
                        exploration_report = {
                            "schema_version": 1,
                            "campaign_id": spec.parent.parent_campaign_id,
                            "goal": parent_spec.goal,
                            "status": "PERSISTED_GRAPH_SNAPSHOT",
                            "scientific_conclusion": (
                                "No verified scientific conclusion is asserted; parent findings are unverified PUBLIC_DEV evidence."
                            ),
                            "completed_experiments": [
                                {
                                    "node_id": node["node_id"],
                                    "evidence_refs": node["payload"].get("completion_evidence", {}).get("evidence_refs", []),
                                    "limitations": node["payload"].get("completion_evidence", {}).get("limitations", []),
                                }
                                for node in parent_snapshot["nodes"]
                                if node["status"] == "COMPLETED"
                                and isinstance(node["payload"].get("completion_evidence"), dict)
                            ],
                            "graph_revision": parent_snapshot["graph_revision"],
                            "source": "READ_ONLY_CAMPAIGN_GRAPH",
                        }
                    finally:
                        parent_conn.close()
            if (isinstance(exploration_report, dict)
                    and exploration_report.get("status") == "PROTOCOL_REVIEW_PENDING"
                    and verifier_row is not None
                    and verifier_row["decision"] == "APPROVE"):
                exploration_report = dict(exploration_report)
                exploration_report["status_at_exploration_stop"] = "PROTOCOL_REVIEW_PENDING"
                exploration_report["status"] = "PROTOCOL_APPROVED"
                exploration_report["protocol_review"] = {
                    "status": "APPROVED",
                    "proposal_digest": verifier_row["proposal_digest"],
                    "protocol_digest": verifier_row["protocol_digest"],
                    "approval_digest": verifier_row["approval_digest"],
                    "receipt_digest": verifier_row["receipt_digest"],
                }
            if isinstance(exploration_report, dict) and owner_row is not None:
                exploration_report = dict(exploration_report)
                exploration_report["owner_pivot"] = {
                    "status": owner_row["status"],
                    "decision": owner_row["decision"],
                    "proposal_id": owner_row["proposal_id"],
                    "proposal_digest": owner_row["digest"],
                }
            final_report = {
                "schema_version": 1,
                "campaign_id": spec.parent.parent_campaign_id,
                "status": result["status"],
                "exploration_campaign_id": spec.parent.parent_campaign_id,
                "optimization_campaign_id": self.campaign_id,
                "campaign_terminal_status": "PENDING_PARENT_FINALIZATION",
                "exploration_report": exploration_report,
                "optimization_report": report,
                "provider_calls": [
                    {"campaign_id": spec.parent.parent_campaign_id, **call}
                    for call in parent_provider_calls
                ] + [
                    {"campaign_id": self.campaign_id, **call}
                    for call in provider_calls
                ],
                "limitations": report["limitations"] + ([] if exploration_report else [
                    "Parent Exploration report was unavailable when this Optimization run completed.",
                ]),
                "as_of": utc_now(),
            }
            parent_reports = parent_state / "reports"
            parent_reports.mkdir(parents=True, exist_ok=True)
            final_target = parent_reports / f"v2-final-{spec.parent.parent_campaign_id}.json"
            final_raw = (json.dumps(final_report, sort_keys=True, indent=2,
                                    ensure_ascii=False) + "\n").encode("utf-8")
            _atomic_write_report(final_target, final_raw)
            final_markdown_target = final_target.with_suffix(".md")
            _atomic_write_report(
                final_markdown_target,
                _render_final_report_markdown(final_report).encode("utf-8"),
            )
            result["final_report_path"] = str(final_target)
            result["final_report_markdown_path"] = str(final_markdown_target)
        return {**result, "report_path": str(target),
                "report_markdown_path": str(markdown_target)}

    def _build_baseline_candidate_delta(self, champion_report: dict[str, Any] | None) -> dict[str, Any]:
        """Build an owner-only baseline/candidate comparison from VALIDATION receipts."""
        incomplete = {
            "status": "INCOMPLETE", "tier": "validation",
            "comparison_table": [],
        }
        if not isinstance(champion_report, dict):
            return {**incomplete, "reason": "campaign has no pinned champion"}
        champion_id = champion_report.get("snapshot_id")
        if not isinstance(champion_id, str):
            return {**incomplete, "reason": "champion snapshot identity is unavailable"}

        from .evaluator_verification import EvaluatorProtocolAuthority
        from .trusted_evaluator import EvaluatorRunReceiptV1

        spec = self.graph.campaign_spec()
        authority = EvaluatorProtocolAuthority(self.graph.conn)
        proposal, _ = authority.approved_protocol(spec.evaluation.approval_digest)
        protocol = proposal.protocol
        if champion_id != protocol.source_snapshot_id:
            self.trusted_evaluator._snapshot_files(
                spec, protocol.source_snapshot_id, champion_id,
            )
        receipt_rows = self.graph.conn.execute(
            "SELECT receipt_json FROM evaluator_run_receipts "
            "WHERE campaign_id=? ORDER BY rowid",
            (self.campaign_id,),
        ).fetchall()
        receipts = []
        for row in receipt_rows:
            try:
                receipt = EvaluatorRunReceiptV1.model_validate_json(row["receipt_json"])
            except Exception:
                raise V2OptimizationError("durable evaluator receipt is malformed while building final comparison") from None
            if receipt.tier == "validation":
                receipts.append(receipt)
        baseline = next((item for item in reversed(receipts)
                         if item.candidate_snapshot_id == protocol.source_snapshot_id), None)
        candidate = next((item for item in reversed(receipts)
                          if item.candidate_snapshot_id == champion_id), None)
        if baseline is None or candidate is None:
            missing = []
            if baseline is None:
                missing.append("baseline VALIDATION receipt")
            if candidate is None:
                missing.append("champion VALIDATION receipt")
            return {**incomplete, "reason": " and ".join(missing) + " unavailable"}

        baseline_metric = self.trusted_evaluator.owner_validation_metric_summary(baseline)
        candidate_metric = self.trusted_evaluator.owner_validation_metric_summary(candidate)
        if (baseline.tier_manifest_sha256 != candidate.tier_manifest_sha256
                or baseline.case_count != candidate.case_count
                or baseline.protocol_digest != candidate.protocol_digest
                or baseline_metric["metric_id"] != candidate_metric["metric_id"]
                or baseline_metric["direction"] != candidate_metric["direction"]
                or baseline_metric["unit"] != candidate_metric["unit"]):
            raise V2OptimizationError("baseline and champion VALIDATION receipts have different frozen scopes")
        absolute_delta = candidate_metric["value"] - baseline_metric["value"]
        directional_improvement = (
            absolute_delta if candidate_metric["direction"] == "MAXIMIZE" else -absolute_delta
        )
        relative_improvement = (
            directional_improvement / abs(baseline_metric["value"]) * 100
            if baseline_metric["value"] != 0 else None
        )
        row = {
            "tier": "VALIDATION",
            "metric_id": candidate_metric["metric_id"],
            "metric_field": candidate_metric["metric_field"],
            "unit": candidate_metric["unit"],
            "direction": candidate_metric["direction"],
            "baseline": baseline_metric["value"],
            "candidate": candidate_metric["value"],
            "delta": absolute_delta,
            "directional_improvement": directional_improvement,
        }
        return {
            "status": "VERIFIED",
            "tier": "validation",
            "protocol_digest": protocol.content_digest(),
            "tier_manifest_sha256": baseline.tier_manifest_sha256,
            "case_count": baseline.case_count,
            "repeatability_runs": {
                "baseline": baseline.repeatability_runs,
                "candidate": candidate.repeatability_runs,
            },
            "baseline_receipt_digest": baseline.receipt_digest,
            "candidate_receipt_digest": candidate.receipt_digest,
            "baseline_snapshot_id": baseline.candidate_snapshot_id,
            "candidate_snapshot_id": candidate.candidate_snapshot_id,
            "baseline_metric": baseline_metric,
            "candidate_metric": candidate_metric,
            "delta": {
                "absolute": absolute_delta,
                "directional_improvement": directional_improvement,
                "relative_improvement_percent": relative_improvement,
            },
            "comparison_table": [row],
        }

    def _baseline_validation_receipt(self, *, worker_id: str):
        spec = self.graph.campaign_spec()
        if not isinstance(spec, OptimizationCampaignSpecV2):
            raise V2OptimizationError("baseline validation requires the frozen Optimization protocol")
        authority = EvaluatorProtocolAuthority(self.graph.conn)
        proposal, _ = authority.approved_protocol(spec.evaluation.approval_digest)
        source_snapshot_id = proposal.protocol.source_snapshot_id
        job_ids = self.trusted_evaluator.enqueue_repeatability(
            candidate_snapshot_id=source_snapshot_id, tier="validation",
        )
        for job_id in job_ids:
            job = self.queue.get_job(job_id)
            if job["state"] == "READY":
                self.trusted_evaluator.run_one(job_id, worker_id=worker_id)
            elif job["state"] != "COMPLETED":
                raise V2OptimizationError(
                    f"baseline validation job is {job['state']}; result is indeterminate"
                )
        receipt = self.trusted_evaluator.finalize_repeatability(job_ids)
        if (receipt.tier != "validation" or receipt.candidate_snapshot_id != source_snapshot_id):
            raise V2OptimizationError("baseline VALIDATION receipt is outside its frozen source scope")
        return receipt
