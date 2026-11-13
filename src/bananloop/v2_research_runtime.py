"""RPDI-13R: explicit, offline-testable v2 Exploration execution.

This module does not invoke Codex CLI. A future trusted, isolated provider
broker must supply model_reply; passing a host-capable CodexAdapter directly
would bypass the capability boundary and is not a supported integration.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .budget_ledger import BudgetLedger
from .contracts import ExplorationCampaignSpecV2, utc_now
from .exploration import EvidenceAdapterError, EvidenceOnlyAttemptAdapter, EvidenceOnlyOutputV2
from .experiment_intent import same_experiment_intent
from .evaluator_verification import EvaluatorProtocolDraftV1, EvaluatorProtocolIntentV1
from .graph_store import AddEdge, AddNode, GraphRepository, MutateGraph
from .human_decision import HumanDecisionRequestV1, StrategyChangeProposalV1
from .research_ledger import ResearchLedger
from .roles import PinnedRoleExecutorV1, RoleRegistryV1, TeamManifestAuthority
from .scheduler import HybridScheduler, SchedulerPlan
from .worker_queue import DurableWorkerQueue, WorkerBudgetError, WorkerQueueError


class V2RuntimeError(RuntimeError):
    """The requested work is not an authorized v2 Exploration attempt."""


def _canonical_experiment_text(value: Any) -> Any:
    """Preserve a structured experiment plan as bounded canonical JSON text."""
    if isinstance(value, (dict, list)):
        try:
            encoded = json.dumps(
                value, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ValueError("structured next_experiment must be JSON-compatible") from None
        if len(encoded) > 4000:
            raise ValueError("structured next_experiment exceeds 4000 characters")
        return encoded
    return value


class ResearchHypothesisV1(BaseModel):
    """One bounded candidate and its directly testable next action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    hypothesis: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=1000)
    next_experiment: str = Field(min_length=1, max_length=4000)
    falsification_condition: str = Field(min_length=1, max_length=1000)
    expected_evidence: str = Field(min_length=1, max_length=1000)
    experiment_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    target_claim_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")

    @field_validator("next_experiment", mode="before")
    @classmethod
    def canonical_structured_experiment(cls, value: Any) -> Any:
        return _canonical_experiment_text(value)


class ResearcherReplyV1(BaseModel):
    """Agent-authored research text only; artifacts are digested by trusted Core."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    next_experiment: str | None = Field(default=None, min_length=1, max_length=4000)
    hypotheses: tuple[ResearchHypothesisV1, ...] = Field(default=(), max_length=4)
    selected_hypothesis_id: str | None = Field(default=None, min_length=1, max_length=100)
    experiment_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    research_findings: tuple[dict[str, Any], ...] = ()
    artifact_text: dict[str, str] = Field(default_factory=dict)
    artifact_base64: dict[str, str] = Field(default_factory=dict)
    protocol_draft: EvaluatorProtocolDraftV1 | None = None
    protocol_intent: EvaluatorProtocolIntentV1 | None = None
    strategy_change_request: StrategyChangeProposalV1 | None = None

    @field_validator("next_experiment", mode="before")
    @classmethod
    def canonical_structured_experiment(cls, value: Any) -> Any:
        return _canonical_experiment_text(value)


def _research_memory_claim_evidence(dev_summary: str) -> dict[str, set[str]]:
    """Extract trusted Core claim keys and evidence IDs from the role summary."""
    result: dict[str, set[str]] = {}
    for line in dev_summary.splitlines():
        match = re.match(
            r"^([a-z][a-z0-9_.:-]{0,119}) \[[A-Z_]+\] evidence_ids=([0-9a-f,]*):",
            line,
        )
        if match:
            result.setdefault(match.group(1), set()).update(
                filter(None, match.group(2).split(","))
            )
    return result


# The adapter is a synchronous, one-model-call protocol: one durable
# agent-call reservation corresponds to one model action, not an unmetered
# conversation of hidden tool calls.
ModelReply = Callable[..., dict[str, Any]]
MAX_VISIBLE_FILES = 16
MAX_FILE_CHARS = 64_000
MAX_ALL_VISIBLE_CHARS = 160_000
MAX_ARTIFACT_BYTES = 96_000


def make_researcher_invoke(
    model_reply: ModelReply, *,
    protocol_binder: Callable[[EvaluatorProtocolIntentV1, str], tuple[
        EvaluatorProtocolDraftV1, dict[str, bytes],
    ]] | None = None,
    experiment_executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    experiment_catalog: tuple[dict[str, str], ...] = (),
) -> Callable[..., dict[str, Any]]:
    """Adapt a one-shot *trusted isolated model transport* to a pinned role.

    The model receives only public DEV role context and explicitly allowlisted
    file text read through ToolGateway; it does not receive the raw queue
    request, host paths, grant, eval assets or executable tool access.
    The callable supplied here is not by itself proof of model isolation.
    """
    if not callable(model_reply):
        raise TypeError("a trusted one-shot model transport is required")
    if experiment_executor is not None:
        if (not callable(experiment_executor) or not experiment_catalog
                or any(not isinstance(item, dict)
                       or set(item) != {"id", "description"}
                       or not isinstance(item.get("id"), str)
                       or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", item["id"])
                       or not isinstance(item.get("description"), str)
                       or not item["description"].strip()
                       for item in experiment_catalog)):
            raise ValueError("a bounded unique experiment catalog is required for execution")
        ids = [item["id"] for item in experiment_catalog]
        if len(ids) != len(set(ids)):
            raise ValueError("experiment IDs must be unique")
    elif experiment_catalog:
        raise ValueError("experiment catalog requires a trusted experiment executor")
    protocol_intent_bound = False

    def invoke(request: dict[str, Any], gateway: Any, *, adapter: str, provider: str,
               model: str, model_version: str, prompt_digest: str,
               max_model_tokens: int) -> dict[str, Any]:
        nonlocal protocol_intent_bound
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
                "next_experiment for that selection and optional text artifacts. Always "
                "provide at least one research_finding. If no experiment was executed, "
                "state that as an UNCERTAIN OBSERVATION and do not report a result. "
                "When using prior findings, provide target_claim_key and cite its exact "
                "evidence_ids in evidence_refs; if prior findings are available, at least "
                "one candidate must cite a matching target claim and exact evidence ID. "
                "For a later iteration, copy an exact claim_key and one of its evidence_ids "
                "from available_research_memory onto the same hypothesis object. Do not use "
                "artifact:// references as evidence_ids. "
                "Use supplied memory to select a distinct "
                "follow-up experiment unless replication is specifically justified. "
                "Do not claim "
                "independent evaluator approval, validation or HOLDOUT scores. "
                "These observations are unverified PUBLIC_DEV evidence. If the evidence "
                "indicates that a fundamental strategy change is needed, you may instead "
                "return only strategy_change_request with current_strategy, proposed_strategy, "
                "rationale, expected_effect, and changed_scope. This creates a request for "
                "owner review only; it does not authorize a pivot or run another experiment."
                + "\nTrusted frozen source_snapshot_id: "
                + str(request.get("base_commit", ""))
                + ". Copy this exact value into any requested protocol_draft; do not "
                "guess or derive a different snapshot ID."
                + ("\nA trusted evaluator fixture is available. In this first response, "
                   "you MUST include protocol_intent with protocol_id, "
                   "version, metric_id, metric_field, direction, aggregation, "
                   "repeatability_runs (integer from 1 to 10), and timeout_seconds "
                   "(integer from 1 to 3600). This is mandatory, not "
                   "optional. Do not return a full "
                   "protocol_draft, hashes, manifests, evaluator code, or tier inputs; "
                   "Core binds those from the owner-supplied fixture after your response."
                   if protocol_binder is not None else "")
            ),
        }
        prior_memory = _research_memory_claim_evidence(
            str(role_context.get("dev_summary", "")),
        )
        public_input["available_research_memory"] = [
            {"claim_key": claim_key, "evidence_ids": sorted(evidence_ids)}
            for claim_key, evidence_ids in sorted(prior_memory.items())
        ]
        if protocol_binder is not None:
            public_input["requires_protocol_intent"] = True
        if experiment_executor is not None:
            prior_summary = str(role_context.get("dev_summary", ""))
            completed_ids = set(re.findall(
                r"^core_experiment\.([a-z][a-z0-9_-]{0,79}) \[",
                prior_summary, re.MULTILINE,
            ))
            available_experiments = [
                dict(item) for item in experiment_catalog if item["id"] not in completed_ids
            ]
            if not available_experiments:
                raise V2RuntimeError("registered experiment catalog is exhausted")
            public_input["registered_experiments"] = available_experiments
            public_input["instruction"] += (
                "\nChoose exactly one registered analysis for this iteration. In the selected "
                "hypothesis, set experiment_id to one of the listed IDs and describe its "
                "falsifiable question in next_experiment. Core executes that registered "
                "analysis and stores the measured result in Research Memory. Do not claim "
                "a measurement before the result is returned. A completed experiment ID "
                "cannot be selected again. `experiment_id` is distinct from the hypothesis `id`; "
                "copy the registered ID exactly (for example, `experiment_id`: "
                "`primary_association`). Include a currently available registered "
                "experiment_id on every candidate object, including candidates that cite "
                "Research Memory. Put the ID on the candidate, not just at the top level. "
                "A top-level experiment_id is accepted only when Core keeps your selected "
                "hypothesis. Do not return top-level next_experiment when using a "
                "hypothesis portfolio; Core uses the selected candidate's next_experiment. "
                "For a later iteration, copy one exact claim_key and one exact evidence_id "
                "from the same available_research_memory entry onto at least one hypothesis "
                "as target_claim_key and evidence_refs. Do not cite artifact:// URIs here. "
                "The `core_experiment.*` claim_key namespace is reserved for trusted Core "
                "results; never use it in research_findings. "
                "Do not omit an ID or invent one."
            )
        # The broker controls provider/model selection; worker content cannot.
        invoke.last_usage = None
        call_kwargs = {
            "provider": provider, "model": model,
            "model_version": model_version, "prompt_digest": prompt_digest,
            "max_model_tokens": max_model_tokens,
        }
        deadline_epoch = request.get("campaign_deadline_epoch")
        if deadline_epoch is not None:
            if (isinstance(deadline_epoch, bool)
                    or not isinstance(deadline_epoch, (int, float))):
                raise V2RuntimeError("persisted campaign deadline is invalid")
            remaining_seconds = int(deadline_epoch - time.time())
            if remaining_seconds < 1:
                raise V2RuntimeError("campaign deadline expired before model dispatch")
            configured_timeout = getattr(model_reply, "timeout_seconds", 300)
            if (isinstance(configured_timeout, bool)
                    or not isinstance(configured_timeout, int)
                    or not 1 <= configured_timeout <= 300):
                raise V2RuntimeError("configured model timeout is invalid")
            call_kwargs["timeout_seconds"] = min(configured_timeout, remaining_seconds)
        raw = model_reply(public_input, **call_kwargs)
        invoke.last_usage = getattr(model_reply, "last_usage", None)
        if isinstance(raw, dict) and "top-level evidence_refs" in raw:
            # One legacy prompt wording was occasionally copied as a literal
            # JSON key. It is not a schema field and carries no authority;
            # discard only this exact pseudo-alias, leaving all other unknown
            # keys subject to the strict response contract.
            raw = {key: value for key, value in raw.items()
                   if key != "top-level evidence_refs"}
        reply = ResearcherReplyV1.model_validate(raw)
        protocol_draft = reply.protocol_draft
        bound_protocol_artifacts: dict[str, bytes] = {}
        if (experiment_executor is not None
                and any(str(item.get("claim_key", "")).startswith("core_experiment.")
                        for item in reply.research_findings)):
            raise V2RuntimeError(
                "Core-reserved experiment claim keys cannot be authored by the Researcher"
            )
        if protocol_binder is not None and reply.strategy_change_request is None:
            if protocol_draft is not None:
                raise V2RuntimeError(
                    "fixture campaigns require a Researcher intent; full model-authored protocols are forbidden"
                )
            if reply.protocol_intent is None:
                if not protocol_intent_bound:
                    raise V2RuntimeError(
                        "fixture campaign's first ordinary Researcher response must propose protocol_intent"
                    )
            elif protocol_intent_bound:
                raise V2RuntimeError("fixture protocol intent may be proposed only once per runtime invocation")
            else:
                try:
                    protocol_draft, bound_protocol_artifacts = protocol_binder(
                        reply.protocol_intent, str(request.get("base_commit", "")),
                    )
                except Exception as exc:
                    raise V2RuntimeError(
                        "Core could not bind Researcher protocol intent to trusted evaluator assets"
                    ) from exc
                protocol_intent_bound = True
        elif reply.protocol_intent is not None:
            raise V2RuntimeError("protocol intent requires a trusted evaluator fixture")
        if reply.strategy_change_request is not None:
            if (reply.next_experiment is not None or reply.hypotheses
                    or reply.selected_hypothesis_id is not None or reply.evidence_refs
                    or reply.experiment_id is not None
                    or reply.limitations or reply.research_findings or reply.artifact_text
                    or reply.artifact_base64 or reply.protocol_draft or reply.protocol_intent):
                raise V2RuntimeError(
                    "strategy-change request must be the only researcher action"
                )
            job_node_id = request["node_id"]
            proposal = reply.strategy_change_request.model_dump(mode="json")
            request_id = hashlib.sha256(json.dumps(
                proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            request = HumanDecisionRequestV1(**proposal, request_id=request_id)
            raw_request = (json.dumps(
                request.model_dump(mode="json"), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False,
            ) + "\n").encode("utf-8")
            gateway.write_artifact("research/strategy-change-request.json", raw_request)
            digest = hashlib.sha256(raw_request).hexdigest()
            output = EvidenceOnlyOutputV2.model_validate({
                "node_id": job_node_id,
                "artifacts": [{"path": "research/strategy-change-request.json", "sha256": digest}],
                "limitations": ("No experiment was executed; owner review is required before strategy changes.",),
                "next_experiment": "Await owner decision on the proposed strategy change.",
                "human_decision_request": request.model_dump(mode="json"),
            })
            return output.model_dump(mode="json")
        artifact_text = dict(reply.artifact_text)
        research_findings = [dict(item) for item in reply.research_findings]
        allowed_evidence_refs = set(role_context.get("evidence_refs", ()))
        if reply.hypotheses:
            if not 2 <= len(reply.hypotheses) <= 4:
                raise V2RuntimeError("hypothesis portfolio must contain at least two candidates")
            ids = [item.id for item in reply.hypotheses]
            if (len(set(ids)) != len(ids)
                    or reply.selected_hypothesis_id not in ids):
                raise V2RuntimeError("hypothesis selection must identify one unique portfolio candidate")
            selected = next(item for item in reply.hypotheses if item.id == reply.selected_hypothesis_id)
            # The selected portfolio item is the canonical action. A redundant
            # top-level next_experiment can be inconsistent in model output; it
            # must never override or invalidate the selected candidate.
            claim_evidence = _research_memory_claim_evidence(
                str(role_context.get("dev_summary", "")),
            )
            evidence_scores: dict[str, int] = {}
            rejected_evidence_links: set[str] = set()
            for candidate in reply.hypotheses:
                refs = set(candidate.evidence_refs)
                claim_refs = claim_evidence.get(candidate.target_claim_key or "", set())
                if refs and (not refs <= allowed_evidence_refs
                             or candidate.target_claim_key is None
                             or not refs <= claim_refs):
                    # A bad citation on one portfolio item must never be
                    # trusted or poison a separate correctly grounded item.
                    # Core records and removes this link from the durable
                    # portfolio, then ranks the candidate as ungrounded.
                    rejected_evidence_links.add(candidate.id)
                    evidence_scores[candidate.id] = 0
                elif candidate.target_claim_key is not None:
                    evidence_scores[candidate.id] = len(refs)
                else:
                    evidence_scores[candidate.id] = 0
            executable_hypotheses = list(reply.hypotheses)
            if experiment_executor is not None:
                available_ids = {item["id"] for item in available_experiments}
                executable_hypotheses = [
                    item for item in reply.hypotheses
                    if item.experiment_id in available_ids
                    or (item.id == reply.selected_hypothesis_id
                        and reply.experiment_id in available_ids)
                ]
                if not executable_hypotheses:
                    raise V2RuntimeError(
                        "hypothesis portfolio has no candidate bound to an available registered experiment_id"
                    )
            ranked = [item for item in executable_hypotheses if evidence_scores[item.id] > 0]
            memory_citation_missing = bool(
                allowed_evidence_refs and not ranked and experiment_executor is not None
            )
            if allowed_evidence_refs and not ranked and not memory_citation_missing:
                raise V2RuntimeError(
                    "later hypothesis portfolio must cite available Research Memory on an executable candidate"
                )
            if ranked:
                # Candidate IDs and the model's preferred ID have no quality
                # weight. Core ranks only references already present in the
                # trusted context, then uses a content-only digest for ties.
                selected = max(ranked, key=lambda item: (
                    evidence_scores[item.id],
                    hashlib.sha256(json.dumps(
                        item.model_dump(mode="json", exclude={"id"}),
                        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    ).encode("utf-8")).hexdigest(),
                ))
                selection = {
                    "method": "CORE_PUBLIC_DEV_EVIDENCE_COVERAGE",
                    "selected_hypothesis_id": selected.id,
                    "scores": evidence_scores,
                    "reason": "Highest count of distinct prior public evidence references attached to its exact target claim; tied candidates use a stable content digest.",
                }
            else:
                preferred = next(
                    (item for item in executable_hypotheses
                     if item.id == reply.selected_hypothesis_id),
                    None,
                )
                selected = preferred or max(executable_hypotheses, key=lambda item: hashlib.sha256(
                    json.dumps(item.model_dump(mode="json", exclude={"id"}),
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    ).encode("utf-8")).hexdigest())
                if memory_citation_missing:
                    method = "CORE_MEMORY_AWARE_EXECUTABLE_HYPOTHESIS"
                    reason = (
                        "Researcher citations were absent or unverified. Core limited selection "
                        "to registered experiments not already present in Research Memory, then "
                        "used the Researcher preference among those executable candidates. "
                        "The selected hypothesis remains UNANCHORED; no prior evidence is "
                        "attributed to it."
                    )
                else:
                    method = ("RESEARCHER_INITIAL_SELECTION" if preferred is not None
                              else "CORE_EXECUTABLE_HYPOTHESIS")
                    reason = (
                        "No prior evidence was available to rank candidates; researcher preference is advisory for the initial step."
                        if preferred is not None else
                        "No prior evidence was available; Core selected the only or content-digest-first candidate bound to an available registered experiment."
                    )
                selection = {
                    "method": method,
                    "selected_hypothesis_id": selected.id,
                    "scores": evidence_scores,
                    "reason": reason,
                }
            next_experiment = selected.next_experiment
            portfolio_candidates = []
            for item in reply.hypotheses:
                candidate_value = item.model_dump(mode="json")
                if item.id in rejected_evidence_links:
                    candidate_value["evidence_refs"] = []
                    candidate_value["target_claim_key"] = None
                    candidate_value["core_evidence_validation"] = "REJECTED_INVALID_CLAIM_LINK"
                elif evidence_scores[item.id] > 0:
                    candidate_value["core_evidence_validation"] = "TRUSTED_CLAIM_LINK"
                else:
                    candidate_value["core_evidence_validation"] = "UNANCHORED"
                portfolio_candidates.append(candidate_value)
            portfolio = json.dumps({
                "candidates": portfolio_candidates,
                "selected_hypothesis_id": selected.id,
                "researcher_preferred_hypothesis_id": reply.selected_hypothesis_id,
                "core_selection": selection,
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        else:
            if allowed_evidence_refs:
                raise V2RuntimeError(
                    "later research response must return a hypothesis portfolio that uses available Research Memory"
                )
            if not reply.next_experiment:
                raise V2RuntimeError("researcher must return a hypothesis portfolio or next experiment")
            next_experiment = reply.next_experiment
            portfolio = None
        if experiment_executor is not None:
            if reply.hypotheses:
                # Execute the Core-selected evidence-grounded candidate, not
                # the Researcher's advisory preferred ID when those differ.
                chosen_id = selected.experiment_id
                # Some valid reply shapes put the explicit registered choice
                # at the top level. Use it only when Core retained the researcher's
                # selected hypothesis; otherwise it may refer to a rejected candidate.
                if (chosen_id is None and reply.experiment_id is not None
                        and selected.id == reply.selected_hypothesis_id):
                    chosen_id = reply.experiment_id
            else:
                chosen_id = reply.experiment_id
            allowed_ids = {item["id"] for item in available_experiments}
            if chosen_id not in allowed_ids:
                raise V2RuntimeError("researcher must select exactly one registered experiment_id")
            role_context_value = public_input.get("role_context", {})
            dev_summary = str(role_context_value.get("dev_summary", ""))
            if re.search(rf"^{re.escape('core_experiment.' + chosen_id)} ", dev_summary, re.MULTILINE):
                raise V2RuntimeError("a completed registered experiment cannot be repeated")
            try:
                experiment_result = experiment_executor(chosen_id, role_context_value)
                if (not isinstance(experiment_result, dict)
                        or experiment_result.get("status") != "COMPLETED"
                        or not isinstance(experiment_result.get("summary"), str)
                        or not experiment_result["summary"].strip()
                        or len(experiment_result["summary"]) > 1800):
                    raise ValueError("invalid experiment result contract")
                raw_experiment = json.dumps(
                    {"schema": "bananloop-executed-experiment-v1",
                     "experiment_id": chosen_id, **experiment_result},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            except V2RuntimeError:
                raise
            except Exception as exc:
                raise V2RuntimeError(
                    "registered experiment execution failed before evidence publication"
                ) from exc
            if len(raw_experiment) > MAX_ARTIFACT_BYTES:
                raise V2RuntimeError("registered experiment result exceeds the public artifact budget")
            artifact_path = "research/executed-experiment.json"
            if artifact_path in artifact_text or artifact_path in reply.artifact_base64:
                raise V2RuntimeError("researcher artifact collides with the trusted experiment result")
            artifact_text[artifact_path] = raw_experiment.decode("utf-8")
            result_ref = "artifact://sha256/" + hashlib.sha256(raw_experiment).hexdigest()
            stance = experiment_result.get("stance", "UNCERTAIN")
            if stance not in {"SUPPORTS", "REFUTES", "UNCERTAIN"}:
                stance = "UNCERTAIN"
            confidence = experiment_result.get("confidence", 0.8)
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                confidence = 0.8
            confidence = min(1.0, max(0.0, float(confidence)))
            research_findings.append({
                "claim_key": "core_experiment." + chosen_id,
                "statement": experiment_result["summary"],
                "stance": stance, "kind": "OBSERVATION", "confidence": confidence,
                "evidence_refs": [result_ref],
            })
        if not research_findings:
            raise V2RuntimeError(
                "ordinary research response must include at least one evidence-linked finding"
            )
        limitations = list(reply.limitations)
        if reply.hypotheses and memory_citation_missing:
            limitations.append(
                "The Researcher did not provide a verifiable link to available Research Memory. "
                "Core selected only among registered experiments not already completed; the "
                "selected hypothesis remains unanchored to prior evidence."
            )
        if experiment_executor is not None:
            # The Researcher responds before Core runs the registered analysis,
            # so its pre-execution uncertainty can become false within this
            # same durable job. Keep the trusted measured finding and drop only
            # claims that categorically say no experiment was executed.
            def says_no_experiment_ran(value: str) -> bool:
                lowered = value.casefold()
                negative = any(marker in lowered for marker in (
                    "unexecuted", "not executed", "not been executed", "not run",
                    "has not run", "no experiment was run", "no experiment has run",
                    "no registered experiment has been executed",
                    "no registered experiment was executed",
                    "no statistical experiment was executed",
                    "no follow-up experiment has been executed",
                    "no association result can be reported yet",
                ))
                generic_claim = any(marker in lowered for marker in (
                    "no registered experiment", "no statistical experiment",
                    "no follow-up experiment", "no experiment was", "no experiment has",
                    "registered experiment remains unexecuted",
                    "no association result can be reported yet",
                ))
                selected_aliases = {
                    chosen_id.casefold(),
                    chosen_id.replace("_", " ").casefold(),
                    chosen_id.replace("_", "-").casefold(),
                }
                if chosen_id == "primary_association":
                    selected_aliases.add("primary association")
                elif chosen_id == "permutation_test":
                    selected_aliases.add("permutation test")
                elif chosen_id == "split_half_robustness":
                    selected_aliases.update({"split-half", "split half"})
                elif chosen_id == "outlier_sensitivity":
                    selected_aliases.add("outlier sensitivity")
                mentions_selected = any(alias in lowered for alias in selected_aliases)
                return "experiment" in lowered and negative and (generic_claim or mentions_selected)

            research_findings = [
                item for item in research_findings
                if not says_no_experiment_ran(str(item.get("statement", "")))
            ]
            limitations = [
                item for item in limitations
                if not says_no_experiment_ran(item)
            ]
        if len(artifact_text) + len(reply.artifact_base64) > MAX_VISIBLE_FILES:
            raise V2RuntimeError("too many researcher artifacts")
        artifacts: list[dict[str, str]] = []
        bytes_total = 0
        researcher_bytes_total = 0
        artifact_bytes: dict[str, bytes] = {}
        for name, text in artifact_text.items():
            if not isinstance(name, str) or not isinstance(text, str):
                raise V2RuntimeError("artifact text must use UTF-8 names and strings")
            artifact_bytes[name] = text.encode("utf-8")
        for name, encoded in reply.artifact_base64.items():
            if (not isinstance(name, str) or not isinstance(encoded, str)
                    or name in artifact_bytes):
                raise V2RuntimeError("base64 artifacts require unique UTF-8 names and strings")
            if len(encoded) > 4 * ((MAX_ARTIFACT_BYTES + 2) // 3):
                raise V2RuntimeError("base64 artifact exceeds artifact budget")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise V2RuntimeError("base64 artifact is malformed") from exc
            if base64.b64encode(raw).decode("ascii") != encoded:
                raise V2RuntimeError("base64 artifact is not in canonical encoding")
            artifact_bytes[name] = raw
        if set(artifact_bytes) & set(bound_protocol_artifacts):
            raise V2RuntimeError("Researcher artifact path collides with a trusted protocol asset")
        researcher_bytes_total = sum(len(raw) for raw in artifact_bytes.values())
        if researcher_bytes_total > MAX_ARTIFACT_BYTES:
            raise V2RuntimeError("Researcher artifacts exceed the 96 KB model artifact budget")
        researcher_artifact_names = set(artifact_bytes)
        artifact_bytes.update(bound_protocol_artifacts)
        for name, raw_bytes in sorted(artifact_bytes.items()):
            bytes_total += len(raw_bytes)
            if bytes_total > 25_000_000:
                raise V2RuntimeError("Researcher and trusted protocol assets exceed artifact budget")
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
        if portfolio is not None:
            researcher_artifact_names.add("research/hypothesis-portfolio.json")
        produced_refs = {
            f"artifact://sha256/{item['sha256']}"
            for item in artifacts if item["path"] in researcher_artifact_names
        }
        findings: list[dict[str, Any]] = []
        for raw_finding in research_findings:
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
            "limitations": limitations,
            "next_experiment": next_experiment,
            "research_findings": findings,
            "protocol_draft": (protocol_draft.model_dump(mode="json")
                               if protocol_draft is not None else None),
            "protocol_intent": (reply.protocol_intent.model_dump(mode="json")
                                if protocol_binder is not None and reply.protocol_intent is not None
                                else None),
        })
        if not output.artifacts and not output.research_findings and not output.evidence_refs:
            raise V2RuntimeError("researcher returned no auditable evidence")
        return output.model_dump(mode="json")

    return invoke


class V2ResearchRuntime:
    """Connect v2 scheduling, pinned roles, queue, gateway, and public ledger."""

    def __init__(self, *, graph: GraphRepository, queue: DurableWorkerQueue,
                 campaign_id: str, state_root: str | Path, role_registry: RoleRegistryV1,
                 trusted_artifact_digests: tuple[str, ...] = (),
                 campaign_deadline_epoch: float | None = None):
        self.graph = graph
        self.queue = queue
        self.campaign_id = campaign_id
        self.state_root = Path(state_root)
        self.role_registry = role_registry
        self._convergence_reason: str | None = None
        self.adapter = EvidenceOnlyAttemptAdapter(
            graph=graph, queue=queue, campaign_id=campaign_id,
            state_root=state_root, role_registry=role_registry,
            trusted_artifact_digests=trusted_artifact_digests,
            campaign_deadline_epoch=campaign_deadline_epoch,
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
        deadline = self.adapter.campaign_deadline_epoch
        if deadline is not None and time.time() >= deadline:
            raise V2RuntimeError("campaign deadline expired before researcher dispatch")
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
        completion = next((node["payload"].get("completion_evidence")
                           for node in self.graph.scheduling_snapshot()["nodes"]
                           if node["node_id"] == node_id), None)
        human_request = (completion.get("human_decision_request")
                         if isinstance(completion, dict) else None)
        if isinstance(human_request, dict):
            self.queue.set_campaign_control(
                self.campaign_id, "PAUSED",
                reason=f"strategy decision required: {human_request['request_id']}",
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
            "human_decision_request": human_request,
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
        pending = self._pending_human_decision()
        if pending is not None:
            return self._finish_campaign_run("PAUSED_NEEDS_INPUT", completed)
        self._recover_followups()
        if self._convergence_reason is not None:
            return self._finish_campaign_run(
                "CONVERGED_REPEATED_EXPERIMENT", completed,
                convergence_reason=self._convergence_reason,
            )
        while len(completed) < max_iterations:
            deadline = self.adapter.campaign_deadline_epoch
            if deadline is not None and time.time() >= deadline:
                return self._finish_campaign_run("DEADLINE_EXCEEDED", completed)
            plan = self.plan()
            selected = next((item for item in plan.candidates
                             if item.phase == "EXPLORATION" and item.lane == "EVIDENCE_WORKER"), None)
            if selected is None:
                return self._finish_campaign_run(plan.reason, completed)
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
                return self._finish_campaign_run(status, completed)
            except WorkerQueueError:
                current = self.plan()
                if current.reason not in {"PAUSED", "STOP_REQUESTED"}:
                    raise
                return self._finish_campaign_run(current.reason, completed)
            except EvidenceAdapterError as exc:
                if "allocation exhausted" not in str(exc):
                    raise
                current = self.plan()
                status = ("ROLE_ALLOCATION_EXHAUSTED" if "role instance" in str(exc)
                          else current.reason)
                return self._finish_campaign_run(status, completed)
            completed.append(result)
            job_id = result.get("job_id")
            if isinstance(job_id, str):
                durable = self.queue.get_result(job_id)
                model_output = durable.get("result") if isinstance(durable, dict) else None
                if (isinstance(model_output, dict)
                        and model_output.get("protocol_draft") is not None):
                    # A protocol proposal changes what Researcher may do next:
                    # stop this lane so the pinned independent verifier reviews
                    # the frozen protocol before any further live exploration.
                    return self._finish_campaign_run("PROTOCOL_REVIEW_PENDING", completed)
            if result.get("human_decision_request") is not None:
                return self._finish_campaign_run("PAUSED_NEEDS_INPUT", completed)
            self._recover_followups()
            if self._convergence_reason is not None:
                return self._finish_campaign_run(
                    "CONVERGED_REPEATED_EXPERIMENT", completed,
                    convergence_reason=self._convergence_reason,
                )
        return self._finish_campaign_run("ITERATION_LIMIT", completed)

    @staticmethod
    def _normalized_experiment(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.casefold().split())
        return normalized or None

    def _finish_campaign_run(self, status: str, completed: list[dict[str, Any]],
                             *, convergence_reason: str | None = None) -> dict[str, Any]:
        snapshot = self.graph.scheduling_snapshot()
        evidence_nodes = []
        for node in snapshot["nodes"]:
            completion = node["payload"].get("completion_evidence")
            if node["status"] != "COMPLETED" or not isinstance(completion, dict):
                continue
            if completion.get("human_decision_request") is not None:
                continue
            evidence_nodes.append({
                "node_id": node["node_id"],
                "iteration": node["payload"].get("iteration", 0),
                "hypothesis": node["payload"].get("hypothesis"),
                "evidence_refs": completion.get("evidence_refs", []),
                "limitations": completion.get("limitations", []),
                "next_experiment": completion.get("next_experiment"),
            })
        human_decision_requests = [
            {
                "node_id": node["node_id"],
                "request": node["payload"]["completion_evidence"]["human_decision_request"],
            }
            for node in snapshot["nodes"]
            if node["status"] == "COMPLETED"
            and isinstance(node["payload"].get("completion_evidence"), dict)
            and node["payload"]["completion_evidence"].get("human_decision_request") is not None
        ]
        spec = self.graph.campaign_spec()
        report = {
            "schema_version": 1,
            "campaign_id": self.campaign_id,
            "goal": spec.goal,
            "status": status,
            "stop_reason": convergence_reason or status,
            "campaign_deadline_epoch": self.adapter.campaign_deadline_epoch,
            "campaign_time_limit_seconds": (
                spec.budget.deadline_seconds if self.adapter.campaign_deadline_epoch is not None else None
            ),
            "campaign_elapsed_seconds": (
                min(spec.budget.deadline_seconds,
                    max(0, int(time.time() - (self.adapter.campaign_deadline_epoch
                                               - spec.budget.deadline_seconds))))
                if self.adapter.campaign_deadline_epoch is not None else None
            ),
            "scientific_conclusion": (
                "No verified scientific conclusion is asserted; Exploration findings are unverified PUBLIC_DEV evidence."
            ),
            "completed_experiments": evidence_nodes,
            "human_decision_requests": human_decision_requests,
            "graph_revision": snapshot["graph_revision"],
            "as_of": utc_now(),
        }
        reports = self.state_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        target = reports / f"v2-exploration-{self.campaign_id}.json"
        raw = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=reports)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {
            "status": status, "campaign_id": self.campaign_id,
            "iterations_this_call": len(completed), "completed": completed,
            "graph_revision": snapshot["graph_revision"],
            "report_path": str(target),
        }

    def _recover_followups(self) -> None:
        snapshot = self.graph.scheduling_snapshot()
        edges = {(edge["source_id"], edge["target_id"], edge["type"])
                 for edge in snapshot["edges"]}
        nodes = {node["node_id"]: node for node in snapshot["nodes"]}
        completed_tasks: list[tuple[str, str]] = []
        for node in snapshot["nodes"]:
            if (node["status"] == "COMPLETED"
                    and isinstance(node["payload"].get("completion_evidence"), dict)):
                task = self._normalized_experiment(node["payload"].get("hypothesis"))
                if task is not None:
                    completed_tasks.append((node["node_id"], task))
        for parent in snapshot["nodes"]:
            evidence = parent["payload"].get("completion_evidence")
            if (parent["status"] != "COMPLETED" or not isinstance(evidence, dict)
                    or evidence.get("human_decision_request") is not None
                    or not isinstance(evidence.get("job_id"), str)
                    or not isinstance(evidence.get("next_experiment"), str)):
                continue
            job_id = evidence["job_id"]
            child_id = "explore-next-" + hashlib.sha256(job_id.encode()).hexdigest()[:32]
            durable = self.queue.get_result(job_id)
            result = durable.get("result") if isinstance(durable, dict) else None
            if (not isinstance(result, dict)
                    or result.get("next_experiment") != evidence["next_experiment"]):
                raise V2RuntimeError("completed exploration has no matching durable next-step result")
            selected_task = self._normalized_experiment(evidence["next_experiment"])
            direct_children = {
                node_id for node_id, node in nodes.items()
                if node["payload"].get("parent_node_id") == parent["node_id"]
            }
            prior_tasks = {
                task for node_id, task in completed_tasks
                if node_id not in direct_children
            }
            semantic_repeat = next((task for task in prior_tasks
                                    if same_experiment_intent(evidence["next_experiment"], task)), None)
            if ((selected_task is not None and selected_task in prior_tasks)
                    or semantic_repeat is not None):
                self._convergence_reason = (
                    "Selected next experiment repeats a previously completed task by exact normalization "
                    "or conservative controlled-variable intent matching."
                )
                continue
            if child_id in nodes:
                if (parent["node_id"], child_id, "REQUIRES") not in edges:
                    raise V2RuntimeError("persisted Exploration follow-up is missing its prerequisite edge")
                continue
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

    def _pending_human_decision(self) -> dict[str, Any] | None:
        snapshot = self.graph.scheduling_snapshot()
        for node in snapshot["nodes"]:
            completion = node["payload"].get("completion_evidence")
            if (node["status"] == "COMPLETED" and isinstance(completion, dict)
                    and isinstance(completion.get("human_decision_request"), dict)):
                request = completion["human_decision_request"]
                self.queue.set_campaign_control(
                    self.campaign_id, "PAUSED",
                    reason=f"strategy decision required: {request['request_id']}",
                )
                return request
        return None
