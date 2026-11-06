"""RPDI-13R: explicit, offline-testable v2 Exploration execution.

This module does not invoke Codex CLI. A future trusted, isolated provider
broker must supply model_reply; passing a host-capable CodexAdapter directly
would bypass the capability boundary and is not a supported integration.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from .budget_ledger import BudgetLedger
from .contracts import ExplorationCampaignSpecV2
from .exploration import EvidenceAdapterError, EvidenceOnlyAttemptAdapter, EvidenceOnlyOutputV2
from .graph_store import AddEdge, AddNode, GraphRepository, MutateGraph
from .research_ledger import ResearchLedger
from .roles import PinnedRoleExecutorV1, RoleRegistryV1, TeamManifestAuthority
from .scheduler import HybridScheduler, SchedulerPlan
from .worker_queue import DurableWorkerQueue, WorkerBudgetError, WorkerQueueError


class V2RuntimeError(RuntimeError):
    """The requested work is not an authorized v2 Exploration attempt."""


class ResearchHypothesisV1(BaseModel):
    """One bounded candidate and its directly testable next action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    hypothesis: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=1000)
    next_experiment: str = Field(min_length=1, max_length=4000)
    falsification_condition: str = Field(min_length=1, max_length=1000)
    expected_evidence: str = Field(min_length=1, max_length=1000)


class ResearcherReplyV1(BaseModel):
    """Agent-authored research text only; artifacts are digested by trusted Core."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    next_experiment: str | None = Field(default=None, min_length=1, max_length=4000)
    hypotheses: tuple[ResearchHypothesisV1, ...] = Field(default=(), max_length=4)
    selected_hypothesis_id: str | None = Field(default=None, min_length=1, max_length=100)
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
                "Return 2-4 materially different, bounded, falsifiable hypotheses, "
                "each with id and hypothesis, plus selected_hypothesis_id identifying "
                "the one best supported for the next experiment. Include a falsifiable "
                "next_experiment for that selection and optional text artifacts. "
                "Do not claim "
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
        if reply.hypotheses:
            if not 2 <= len(reply.hypotheses) <= 4:
                raise V2RuntimeError("hypothesis portfolio must contain at least two candidates")
            ids = [item.id for item in reply.hypotheses]
            if (len(set(ids)) != len(ids)
                    or reply.selected_hypothesis_id not in ids):
                raise V2RuntimeError("hypothesis selection must identify one unique portfolio candidate")
            selected = next(item for item in reply.hypotheses if item.id == reply.selected_hypothesis_id)
            next_experiment = selected.next_experiment
            if reply.next_experiment is not None and reply.next_experiment != next_experiment:
                raise V2RuntimeError("next experiment must match the selected hypothesis candidate")
            portfolio = json.dumps({
                "candidates": [item.model_dump(mode="json") for item in reply.hypotheses],
                "selected_hypothesis_id": reply.selected_hypothesis_id,
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        else:
            if not reply.next_experiment:
                raise V2RuntimeError("researcher must return a hypothesis portfolio or next experiment")
            next_experiment = reply.next_experiment
            portfolio = None
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
        if portfolio is not None:
            name = "research/hypothesis-portfolio.json"
            gateway.write_artifact(name, portfolio)
            artifacts.append({"path": name, "sha256": hashlib.sha256(portfolio).hexdigest()})
        # Only trusted Core may bind claims to produced artifact digests.
        # The LLM need not guess SHA-256 values and may not invent refs to
        # unavailable artifacts. Findings remain unverified.
        produced_refs = {f"artifact://sha256/{item['sha256']}" for item in artifacts}
        findings: list[dict[str, Any]] = []
        for raw_finding in reply.research_findings:
            finding = dict(raw_finding)
            supplied = finding.get("evidence_refs")
            if supplied is None or supplied == [] or supplied == ():
                if not produced_refs:
                    raise V2RuntimeError("research finding requires a produced evidence artifact")
                finding["evidence_refs"] = sorted(produced_refs)
            elif (not isinstance(supplied, (list, tuple))
                  or not set(supplied) <= produced_refs):
                raise V2RuntimeError("research finding cites evidence not produced by this job")
            findings.append(finding)
        output = EvidenceOnlyOutputV2.model_validate({
            "node_id": request["node_id"],
            "evidence_refs": reply.evidence_refs,
            "artifacts": artifacts,
            "limitations": reply.limitations,
            "next_experiment": next_experiment,
            "research_findings": findings,
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

    def run_one(self, *, node_id: str, visible_paths: list[str] | tuple[str, ...],
                invoke: Callable[..., dict[str, Any]], worker_id: str = "v2-research",
                lease_seconds: int = 300) -> dict[str, Any]:
        plan = self.plan()
        eligible = [item for item in plan.candidates
                    if item.node_id == node_id and item.lane == "EVIDENCE_WORKER"
                    and item.phase == "EXPLORATION"]
        if not eligible:
            if plan.reason in {"PAUSED", "STOP_REQUESTED"}:
                return {"status": plan.reason, "campaign_id": self.campaign_id}
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
        state = self.queue.get_job(job["job_id"])["state"]
        if state == "READY":
            self.adapter.run_one(job_id=job["job_id"], worker_id=worker_id,
                                 executor=executor, lease_seconds=lease_seconds)
        elif state != "COMPLETED":
            raise V2RuntimeError(
                f"research job is {state}; uncertain work is never dispatched twice"
            )
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

    def run_campaign(
        self, *, visible_paths: list[str] | tuple[str, ...],
        invoke: Callable[..., dict[str, Any]], max_iterations: int = 10,
        worker_id: str = "v2-research", lease_seconds: int = 300,
    ) -> dict[str, Any]:
        """Advance a bounded sequence of Exploration nodes and persist each next step.

        The next node is derived from the previous durable completion record,
        linked to its predecessor, and recovered idempotently after restart.
        Existing budget reservations remain the authority for stopping work.
        """
        if (isinstance(max_iterations, bool) or not isinstance(max_iterations, int)
                or not 1 <= max_iterations <= 100):
            raise V2RuntimeError("max_iterations must be an integer from 1 to 100")
        self.plan()  # Validate the persisted Full spec and pinned roles before any graph mutation.
        completed: list[dict[str, Any]] = []
        self._recover_followups()
        while len(completed) < max_iterations:
            plan = self.plan()
            selected = next((item for item in plan.candidates
                             if item.phase == "EXPLORATION" and item.lane == "EVIDENCE_WORKER"), None)
            if selected is None:
                return {
                    "status": plan.reason, "campaign_id": self.campaign_id,
                    "iterations_this_call": len(completed), "completed": completed,
                    "graph_revision": plan.graph_revision,
                }
            try:
                result = self.run_one(
                    node_id=selected.node_id, visible_paths=visible_paths,
                    invoke=invoke, worker_id=worker_id, lease_seconds=lease_seconds,
                )
            except WorkerBudgetError as exc:
                current = self.plan()
                status = ("ROLE_ALLOCATION_EXHAUSTED" if "role instance" in str(exc)
                          else current.reason if current.reason == "BUDGET_EXHAUSTED"
                          else "BUDGET_RESERVATION_REJECTED")
                return {
                    "status": status,
                    "campaign_id": self.campaign_id,
                    "iterations_this_call": len(completed), "completed": completed,
                    "graph_revision": current.graph_revision,
                }
            except WorkerQueueError:
                current = self.plan()
                if current.reason not in {"PAUSED", "STOP_REQUESTED"}:
                    raise
                return {
                    "status": current.reason, "campaign_id": self.campaign_id,
                    "iterations_this_call": len(completed), "completed": completed,
                    "graph_revision": current.graph_revision,
                }
            except EvidenceAdapterError as exc:
                if "allocation exhausted" not in str(exc):
                    raise
                current = self.plan()
                return {
                    "status": "ROLE_ALLOCATION_EXHAUSTED" if "role instance" in str(exc)
                              else current.reason,
                    "campaign_id": self.campaign_id,
                    "iterations_this_call": len(completed), "completed": completed,
                    "graph_revision": current.graph_revision,
                }
            completed.append(result)
            self._recover_followups()
        return {
            "status": "ITERATION_LIMIT", "campaign_id": self.campaign_id,
            "iterations_this_call": len(completed), "completed": completed,
            "graph_revision": self.graph.graph_revision(),
        }

    def _recover_followups(self) -> None:
        snapshot = self.graph.scheduling_snapshot()
        edges = {(edge["source_id"], edge["target_id"], edge["type"])
                 for edge in snapshot["edges"]}
        nodes = {node["node_id"]: node for node in snapshot["nodes"]}
        for parent in snapshot["nodes"]:
            evidence = parent["payload"].get("completion_evidence")
            if (parent["status"] != "COMPLETED" or not isinstance(evidence, dict)
                    or not isinstance(evidence.get("job_id"), str)
                    or not isinstance(evidence.get("next_experiment"), str)):
                continue
            job_id = evidence["job_id"]
            child_id = "explore-next-" + hashlib.sha256(job_id.encode()).hexdigest()[:32]
            if child_id in nodes:
                if (parent["node_id"], child_id, "REQUIRES") not in edges:
                    raise V2RuntimeError("persisted Exploration follow-up is missing its prerequisite edge")
                continue
            durable = self.queue.get_result(job_id)
            result = durable.get("result") if isinstance(durable, dict) else None
            if (not isinstance(result, dict)
                    or result.get("next_experiment") != evidence["next_experiment"]):
                raise V2RuntimeError("completed exploration has no matching durable next-step result")
            key = "exploration-followup:" + hashlib.sha256(job_id.encode()).hexdigest()
            parent_priority = int(parent["payload"].get("scheduler", {}).get("priority", 0))
            parent_iteration = int(parent["payload"].get("iteration", 0))
            self.graph.mutate(MutateGraph(
                command_id=key, idempotency_key=key,
                expected_graph_revision=self.graph.graph_revision(),
                changes=(
                    AddNode(
                        type="AddNode", node_id=child_id, phase="EXPLORATION",
                        payload={
                            "hypothesis": evidence["next_experiment"],
                            "parent_node_id": parent["node_id"],
                            "iteration": parent_iteration + 1,
                            "scheduler": {"priority": parent_priority},
                        },
                    ),
                    AddEdge(type="AddEdge", source_id=parent["node_id"],
                            target_id=child_id, edge_type="REQUIRES"),
                ),
            ))
