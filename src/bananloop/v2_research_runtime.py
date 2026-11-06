"""RPDI-13R: explicit, offline-testable v2 Exploration execution.

This module does not invoke Codex CLI. A future trusted, isolated provider
broker must supply model_reply; passing a host-capable CodexAdapter directly
would bypass the capability boundary and is not a supported integration.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from .budget_ledger import BudgetLedger
from .contracts import ExplorationCampaignSpecV2
from .exploration import EvidenceOnlyAttemptAdapter, EvidenceOnlyOutputV2
from .graph_store import GraphRepository
from .research_ledger import ResearchLedger
from .roles import PinnedRoleExecutorV1, RoleRegistryV1, TeamManifestAuthority
from .scheduler import HybridScheduler
from .worker_queue import DurableWorkerQueue


class V2RuntimeError(RuntimeError):
    """The requested work is not an authorized v2 Exploration attempt."""


class ResearcherReplyV1(BaseModel):
    """Agent-authored research text only; artifacts are digested by trusted Core."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    next_experiment: str = Field(min_length=1, max_length=4000)
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    research_findings: tuple[dict[str, Any], ...] = ()
    artifact_text: dict[str, str] = Field(default_factory=dict)


# The adapter is a synchronous, one-model-call protocol: one durable
# agent-call reservation corresponds to one model action, not an unmetered
# conversation of hidden tool calls.
ModelReply = Callable[..., dict[str, Any]]
MAX_VISIBLE_FILES = 16
MAX_FILE_CHARS = 64_000
MAX_ALL_VISIBLE_CHARS = 160_000
MAX_ARTIFACT_TEXT_BYTES = 96_000


def make_researcher_invoke(model_reply: ModelReply) -> Callable[..., dict[str, Any]]:
    """Adapt a one-shot *trusted isolated model transport* to a pinned role.

    The model receives only public DEV role context and explicitly allowlisted
    file text read through ToolGateway; it does not receive the raw queue
    request, host paths, grant, eval assets or executable tool access.
    The callable supplied here is not by itself proof of model isolation.
    """
    if not callable(model_reply):
        raise TypeError("a trusted one-shot model transport is required")

    def invoke(request: dict[str, Any], gateway: Any, *, adapter: str, provider: str,
               model: str, model_version: str, prompt_digest: str,
               max_model_tokens: int) -> dict[str, Any]:
        if adapter != "codex" or provider != "openai":
            raise V2RuntimeError("this bridge only accepts the pinned OpenAI Codex researcher")
        if type(max_model_tokens) is not int or max_model_tokens < 1:
            raise V2RuntimeError("invalid pinned per-call model-token ceiling")
        role_binding = request.get("role_binding")
        if (not isinstance(role_binding, dict)
                or role_binding.get("role_id") != "researcher"
                or any(role_binding.get(k) != v for k, v in (
                    ("adapter", adapter), ("provider", provider), ("model", model),
                    ("model_version", model_version), ("prompt_digest", prompt_digest)))):
            raise V2RuntimeError("model request is not bound to the pinned researcher")
        cap = request.get("capability_manifest")
        if not isinstance(cap, dict):
            raise V2RuntimeError("missing trusted capability manifest")
        paths = cap.get("visible_paths")
        if (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_VISIBLE_FILES
                or any(not isinstance(p, str) for p in paths)):
            raise V2RuntimeError("invalid bounded visibility allowlist")
        documents: dict[str, str] = {}
        total = 0
        for name in paths:
            # Trust only ToolGateway to authorize and read; no direct Path.open().
            content = gateway.read_file(name)
            if not isinstance(content, str) or len(content) > MAX_FILE_CHARS:
                raise V2RuntimeError("visible file exceeds safe prompt ceiling")
            total += len(content)
            if total > MAX_ALL_VISIBLE_CHARS:
                raise V2RuntimeError("combined public source exceeds safe prompt ceiling")
            documents[name] = content
        role_context = request.get("role_context")
        if not isinstance(role_context, dict):
            raise V2RuntimeError("missing validated public role context")
        public_input = {
            "protocol": "rpdi13r-researcher-one-shot-v1",
            "role_context": role_context,
            "visible_documents": documents,
            "instruction": (
                "Return a strictly structured research finding with a falsifiable "
                "next experiment and optional text artifacts. Do not claim "
                "independent evaluator approval, validation or HOLDOUT scores. "
                "These observations are unverified PUBLIC_DEV evidence."
            ),
        }
        # The broker controls provider/model selection; worker content cannot.
        raw = model_reply(
            public_input, provider=provider, model=model,
            model_version=model_version, prompt_digest=prompt_digest,
            max_model_tokens=max_model_tokens,
        )
        reply = ResearcherReplyV1.model_validate(raw)
        if len(reply.artifact_text) > MAX_VISIBLE_FILES:
            raise V2RuntimeError("too many researcher artifacts")
        artifacts: list[dict[str, str]] = []
        bytes_total = 0
        for name, text in sorted(reply.artifact_text.items()):
            if not isinstance(name, str) or not isinstance(text, str):
                raise V2RuntimeError("artifact text must use UTF-8 names and strings")
            raw_bytes = text.encode("utf-8")
            bytes_total += len(raw_bytes)
            if bytes_total > MAX_ARTIFACT_TEXT_BYTES:
                raise V2RuntimeError("researcher artifacts exceed text budget")
            # ToolGateway is the only write authority.
            gateway.write_artifact(name, raw_bytes)
            artifacts.append({"path": name, "sha256": hashlib.sha256(raw_bytes).hexdigest()})
        output = EvidenceOnlyOutputV2.model_validate({
            "node_id": request["node_id"],
            "evidence_refs": reply.evidence_refs,
            "artifacts": artifacts,
            "limitations": reply.limitations,
            "next_experiment": reply.next_experiment,
            "research_findings": reply.research_findings,
        })
        if not output.artifacts and not output.research_findings and not output.evidence_refs:
            raise V2RuntimeError("researcher returned no auditable evidence")
        return output.model_dump(mode="json")

    return invoke


class V2ResearchRuntime:
    """Connect v2 scheduling, pinned roles, queue, gateway, and public ledger."""

    def __init__(self, *, graph: GraphRepository, queue: DurableWorkerQueue,
                 campaign_id: str, state_root: str | Path, role_registry: RoleRegistryV1):
        self.graph = graph
        self.queue = queue
        self.campaign_id = campaign_id
        self.state_root = Path(state_root)
        self.role_registry = role_registry
        self.adapter = EvidenceOnlyAttemptAdapter(
            graph=graph, queue=queue, campaign_id=campaign_id,
            state_root=state_root, role_registry=role_registry,
        )

    def plan(self):
        spec = self.graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2):
            raise V2RuntimeError("v2 Research runtime requires a persisted Exploration v2 spec")
        if spec.feature_flags.profile != "FULL":
            raise V2RuntimeError("diagnostic or partial profile cannot run as v0.2 Full")
        if spec.role_registry_digest != self.role_registry.digest():
            raise V2RuntimeError("role registry differs from the frozen campaign spec")
        budget = BudgetLedger(self.queue.path)
        try:
            snapshot = budget.snapshot(self.campaign_id)
        finally:
            budget.close()
        return HybridScheduler(self.graph).plan(budget=snapshot)

    def run_one(self, *, node_id: str, visible_paths: list[str] | tuple[str, ...],
                invoke: Callable[..., dict[str, Any]], worker_id: str = "v2-research",
                lease_seconds: int = 300) -> dict[str, Any]:
        plan = self.plan()
        eligible = [item for item in plan.candidates
                    if item.node_id == node_id and item.lane == "EVIDENCE_WORKER"
                    and item.phase == "EXPLORATION"]
        if not eligible:
            raise V2RuntimeError(
                f"node {node_id!r} is not a schedulable v2 Exploration job; scheduler: {plan.reason}"
            )
        if not callable(invoke):
            raise V2RuntimeError("a pinned, tested model-invoke boundary is required")
        job = self.adapter.enqueue(node_id=node_id, visible_paths=visible_paths)
        manifest_authority = TeamManifestAuthority(self.queue.path)
        try:
            spec = self.graph.campaign_spec()
            manifest = manifest_authority.load(
                self.campaign_id, spec_hash=spec.approved_hash(),
                registry_digest=self.role_registry.digest(),
            )
        finally:
            manifest_authority.close()
        member = manifest.member(job["request"]["role_binding"]["role_instance_id"])
        if member.role.role_id != "researcher":
            raise V2RuntimeError("scheduled role is not the canonical researcher")
        executor = PinnedRoleExecutorV1.from_manifest(manifest, member.instance_id, invoke)
        self.adapter.run_one(job_id=job["job_id"], worker_id=worker_id,
                             executor=executor, lease_seconds=lease_seconds)
        published = self.adapter.publish_completed(job["job_id"])
        evidence = ResearchLedger(self.graph.conn).ingest_completed_job(
            self.graph, self.queue, job["job_id"],
        )
        return {
            "status": "EXPLORATION_COMPLETED",
            "mode": "EXPLORATION",
            "profile": "FULL",
            "campaign_id": self.campaign_id,
            "node_id": node_id,
            "job_id": job["job_id"],
            "published": published,
            "research_evidence_ids": list(evidence),
        }
