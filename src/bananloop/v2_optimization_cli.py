"""Owner-facing execution of an already-approved v2 Optimization child."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable

from .chatgpt_plan import ChatGPTPlanAuth, SIWCError
from .chatgpt_responses import ChatGPTPlanIntegrationVerifier, ChatGPTPlanOptimizer
from .budget_ledger import BudgetLedger
from .contracts import OptimizationCampaignSpecV2, RuntimePaths, utc_now
from .evaluator_verification import (
    EvaluatorVerificationError, EvaluatorProtocolAuthority,
    IntegrationVerificationAdapter,
)
from .graph_store import AddNode, GraphRepository, MutateGraph
from .registry import CampaignRegistry, RegistryError
from .roles import (
    PinnedRoleExecutorV1, RoleContractError, RoleRegistryV1, TeamFactoryV1,
    TeamManifestAuthority, TeamRequestV1,
)
from .trusted_evaluator import (
    TrustedEvaluatorAdapter, TrustedEvaluatorError, TrustedPublicEvaluatorFailure,
)
from .v2_optimization_runtime import (
    V2OptimizationError, V2OptimizationRuntime, _atomic_write_report,
    _render_final_report_markdown, _safe_path,
)


class V2OptimizationCLIError(ValueError):
    """The requested v2 Optimization run is not safely executable."""


class _CallAllowanceReached(V2OptimizationCLIError):
    pass


def _aggregate_successful_request_usage(
    request_history: dict[str, list[dict[str, Any]]], *, spent: int,
) -> dict[str, int] | str:
    """Aggregate only a complete set of dispatched requests with full receipts."""
    if isinstance(spent, bool) or not isinstance(spent, int) or spent < 0:
        return "UNKNOWN"
    if not isinstance(request_history, dict) or any(
        not isinstance(history, list) for history in request_history.values()
    ):
        return "UNKNOWN"
    records = [item for history in request_history.values() for item in history]
    if (len(records) != spent or not records
            or any(not isinstance(item, dict) or item.get("dispatched") is not True
                   for item in records)):
        return "UNKNOWN"
    totals = {key: 0 for key in ("input_tokens", "output_tokens", "total_tokens")}
    for record in records:
        if record.get("status") != "COMPLETED":
            return "UNKNOWN"
        usage = record.get("usage")
        if not isinstance(usage, dict):
            return "UNKNOWN"
        for key in totals:
            value = usage.get(key)
            if type(value) is not int or value < 0:
                return "UNKNOWN"
            totals[key] += value
    return totals


def _request_usage_summary(
    request_history: dict[str, list[dict[str, Any]]], *, spent: int,
) -> dict[str, Any]:
    """Retain known provider usage independently of semantic or job success."""
    totals = {key: 0 for key in ("input_tokens", "output_tokens", "total_tokens")}
    known_fields = dict.fromkeys(totals, 0)
    records = [item for history in request_history.values()
               if isinstance(history, list) for item in history]
    seen = set()
    unknown = max(0, spent - len(records))
    dispatched = known = 0
    for record in records:
        if not isinstance(record, dict) or record.get("dispatched") is not True:
            unknown += 1
            continue
        rid = record.get("request_id")
        if isinstance(rid, str) and rid:
            if rid in seen:
                unknown += 1
                continue
            seen.add(rid)
        dispatched += 1
        usage = record.get("usage")
        if record.get("status") not in {"COMPLETED", "FAILED"} or not isinstance(usage, dict):
            unknown += 1
            continue
        complete = True
        for key in totals:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                totals[key] += value
                known_fields[key] += 1
            else:
                complete = False
        if complete:
            known += 1
        else:
            unknown += 1
    return {"known_usage": totals, "known_field_counts": known_fields,
            "known_usage_requests": known, "unknown_usage_requests": unknown,
            "dispatched_requests": dispatched, "attempted_calls": spent,
            "complete": spent > 0 and len(records) == spent and known == spent and unknown == 0}


def _attach_request_usage(report: dict[str, Any], *, spent: int) -> None:
    summary = _request_usage_summary(report["request_history"], spent=spent)
    report["usage_summary"] = summary
    report["usage"] = summary["known_usage"] if summary["complete"] else "UNKNOWN"


def _public_evaluator_failure_detail(exc: BaseException) -> str | None:
    """Return only a Core-allowlisted public evaluator code from an exception chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TrustedPublicEvaluatorFailure):
            detail = getattr(current, "failure_detail", None)
            if detail in TrustedPublicEvaluatorFailure.ALLOWED_FAILURE_DETAILS:
                return detail
        current = current.__cause__ or current.__context__
    return None


def _task_request_timeout(deadline_epoch: float | None, ceiling: int,
                          *, now: float | None = None) -> int:
    """Bound each model request by the durable outer task deadline."""
    if deadline_epoch is None:
        return ceiling
    current = time.time() if now is None else now
    remaining = int(float(deadline_epoch) - current)
    if remaining < 1:
        raise V2OptimizationCLIError("AutoResearchExam task deadline expired before model dispatch")
    return min(ceiling, remaining)


def _record_provider_call(database: Path, receipt: dict[str, Any]) -> None:
    conn = sqlite3.connect(database, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
            ("V2_PROVIDER_CALL", json.dumps(receipt, sort_keys=True, separators=(",", ":")),
             utc_now()),
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _write_cli_report(state_root: Path, report: dict[str, Any], *, campaign_id: str,
                      suffix: str | None = None) -> str:
    reports = state_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    label = f"v2-optimization-cli-{campaign_id}"
    if suffix:
        label += f"-{suffix}"
    target = reports / f"{label}.json"
    payload = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=reports)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def _mark_optimization_complete(database: Path, spec: OptimizationCampaignSpecV2,
                                report: dict[str, Any]) -> None:
    """Record terminal campaign state only after the scheduler reports completion."""
    from .lock import CampaignLock

    if report.get("status") != "ALL_NODES_COMPLETE":
        raise V2OptimizationCLIError("only a fully completed optimization can become terminal")
    report_digest = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    lock = CampaignLock(database.parent / "campaign.lock")
    lock.acquire()
    conn = None
    try:
        conn = sqlite3.connect(database, timeout=30, isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT spec_hash,status FROM campaigns WHERE id=1").fetchone()
        if row is None or row[0] != spec.approved_hash():
            raise V2OptimizationCLIError("campaign identity changed before terminal recording")
        if row[1] == "GOAL_MET":
            conn.commit()
            return
        if row[1] not in {"READY", "RUNNING"}:
            raise V2OptimizationCLIError("campaign cannot enter GOAL_MET from its current state")
        now = utc_now()
        conn.execute("UPDATE campaigns SET status='GOAL_MET',updated_at=? WHERE id=1", (now,))
        conn.execute(
            "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
            ("V2_CAMPAIGN_TERMINAL", json.dumps({
                "campaign_id": report.get("campaign_id"),
                "spec_hash": spec.approved_hash(), "status": "GOAL_MET",
                "scheduler_status": report["status"], "report_digest": report_digest,
            }, sort_keys=True, separators=(",", ":")), now),
        )
        conn.commit()
    except Exception:
        if conn is not None and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()
        lock.release()


def _close_completed_child_allocation(result: dict[str, Any], *,
                                      spec: OptimizationCampaignSpecV2,
                                      database: Path, registry: CampaignRegistry) -> dict[str, Any]:
    if result.get("status") != "ALL_NODES_COMPLETE":
        return result
    _mark_optimization_complete(database, spec, result)
    allocation_closed = False
    if spec.parent is not None:
        settlement_ledger = BudgetLedger(registry.path)
        try:
            settlement_ledger.close_child(
                spec.parent.allocation_id, terminal_status="GOAL_MET",
            )
        finally:
            settlement_ledger.close()
        allocation_closed = True
        result = _mark_parent_campaign_complete(
            result, spec=spec, database=database, registry=registry,
        )
    result["campaign_terminal_status"] = "GOAL_MET"
    result["child_allocation_closed"] = allocation_closed
    return result


def _mark_parent_campaign_complete(result: dict[str, Any], *,
                                   spec: OptimizationCampaignSpecV2,
                                   database: Path,
                                   registry: CampaignRegistry) -> dict[str, Any]:
    """Finalize the parent after its authorized child and evaluator handoff complete."""
    parent_ref = spec.parent
    if parent_ref is None or result.get("status") != "ALL_NODES_COMPLETE":
        return result
    if not (parent_ref.parent_spec_hash and parent_ref.pivot_decision_id
            and parent_ref.pivot_proposal_id and parent_ref.pivot_proposal_digest
            and parent_ref.protocol_handoff_digest):
        result["parent_campaign_terminal_status"] = "NOT_FINALIZED_MISSING_PIVOT_LINEAGE"
        return result
    child_id = result.get("campaign_id")
    if not isinstance(child_id, str) or not child_id:
        raise V2OptimizationCLIError("completed optimization result lacks its campaign identity")

    parent_state, resolved_id = registry.resolve_readonly(parent_ref.parent_campaign_id)
    if resolved_id != parent_ref.parent_campaign_id:
        raise V2OptimizationCLIError("resolved parent campaign identity does not match child lineage")
    parent_db = parent_state / "campaign.sqlite3"
    report_path = parent_state / "reports" / f"v2-final-{parent_ref.parent_campaign_id}.json"
    if (not parent_db.is_file() or parent_db.is_symlink()
            or not report_path.is_file() or report_path.is_symlink()):
        raise V2OptimizationCLIError("completed child has no safe parent database and final report")
    try:
        final_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise V2OptimizationCLIError("parent final report is unreadable") from None
    if (not isinstance(final_report, dict)
            or final_report.get("campaign_id") != parent_ref.parent_campaign_id
            or final_report.get("optimization_campaign_id") != child_id
            or final_report.get("status") != "ALL_NODES_COMPLETE"):
        raise V2OptimizationCLIError("parent final report does not prove this completed optimization")

    if not database.is_file() or database.is_symlink():
        raise V2OptimizationCLIError("completed child database is missing or unsafe")
    child_conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    child_conn.row_factory = sqlite3.Row
    try:
        child = child_conn.execute(
            "SELECT spec_hash,status FROM campaigns WHERE id=1",
        ).fetchone()
        handoff = child_conn.execute(
            "SELECT handoff_digest,child_spec_hash,approval_digest "
            "FROM evaluator_protocol_handoffs WHERE parent_campaign_id=? AND child_campaign_id=?",
            (parent_ref.parent_campaign_id, child_id),
        ).fetchone()
    finally:
        child_conn.close()
    exploration = final_report.get("exploration_report")
    review = exploration.get("protocol_review", {}) if isinstance(exploration, dict) else {}
    if (child is None or child["spec_hash"] != spec.approved_hash()
            or child["status"] != "GOAL_MET"
            or handoff is None or handoff["handoff_digest"] != parent_ref.protocol_handoff_digest
            or handoff["child_spec_hash"] != spec.approved_hash()
            or handoff["approval_digest"] != review.get("approval_digest")):
        raise V2OptimizationCLIError("parent protocol handoff does not match the completed child")

    from .lock import CampaignLock

    lock = CampaignLock(parent_db.parent / "campaign.lock")
    lock.acquire()
    conn = None
    try:
        conn = sqlite3.connect(parent_db, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        campaign = conn.execute(
            "SELECT spec_hash,status FROM campaigns WHERE id=1",
        ).fetchone()
        if campaign is None or campaign["spec_hash"] != parent_ref.parent_spec_hash:
            raise V2OptimizationCLIError("parent campaign spec changed before finalization")
        if (parent_ref.pivot_decision_id == f"auto:{parent_ref.pivot_proposal_digest}"
                and spec.benchmark_integration_tier == "dev"
                and parent_ref.approval_request_id is None):
            parent_spec_json = conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()[0]
            if json.loads(parent_spec_json).get("autonomy", {}).get("mode") != "FULL_AUTO":
                raise V2OptimizationCLIError("parent campaign does not authorize Full Auto")
            authorization = conn.execute(
                """SELECT decision_json,proposal_json FROM v2_pivot_sagas
                   WHERE allocation_id=? AND parent_campaign_id=? AND parent_spec_hash=?
                     AND child_id=? AND child_spec_hash=? AND state='ACTIVE'""",
                (parent_ref.allocation_id, parent_ref.parent_campaign_id,
                 parent_ref.parent_spec_hash, child_id, spec.approved_hash()),
            ).fetchone()
            if (authorization is None
                    or json.loads(authorization["decision_json"]) != {
                        "authorization": "FULL_AUTO_SCOPE", "approval": None,
                        "decision_id": parent_ref.pivot_decision_id,
                    }):
                raise V2OptimizationCLIError("parent Full Auto authorization does not match frozen child lineage")
            proposal = json.loads(authorization["proposal_json"])
            if (proposal.get("proposal_id") != parent_ref.pivot_proposal_id
                    or proposal.get("digest") != parent_ref.pivot_proposal_digest
                    or proposal.get("spec_hash") != parent_ref.parent_spec_hash):
                raise V2OptimizationCLIError("parent Full Auto proposal does not match frozen child lineage")
            allowed_status = "READY"
        else:
            decision = conn.execute(
                "SELECT proposal_id,digest,decision,status FROM v2_pivot_decisions WHERE request_id=?",
                (parent_ref.pivot_decision_id,),
            ).fetchone()
            if (decision is None or decision["proposal_id"] != parent_ref.pivot_proposal_id
                    or decision["digest"] != parent_ref.pivot_proposal_digest
                    or decision["decision"] != "approve" or decision["status"] != "APPROVED"):
                raise V2OptimizationCLIError("parent owner approval does not match frozen child lineage")
            allowed_status = "PAUSED_NEEDS_INPUT"
        if campaign["status"] not in {allowed_status, "GOAL_MET"}:
            raise V2OptimizationCLIError("parent campaign cannot enter GOAL_MET from its current state")
        if campaign["status"] != "GOAL_MET":
            now = utc_now()
            final_report["campaign_terminal_status"] = "GOAL_MET"
            final_report["terminalized_at"] = now
            digest = hashlib.sha256(json.dumps(
                final_report, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")).hexdigest()
            conn.execute(
                "UPDATE campaigns SET status='GOAL_MET',updated_at=? WHERE id=1", (now,),
            )
            conn.execute(
                "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
                ("V2_CAMPAIGN_TERMINAL", json.dumps({
                    "campaign_id": parent_ref.parent_campaign_id,
                    "spec_hash": parent_ref.parent_spec_hash,
                    "status": "GOAL_MET", "scheduler_status": "ALL_NODES_COMPLETE",
                    "child_campaign_id": child_id, "report_digest": digest,
                }, sort_keys=True, separators=(",", ":")), now),
            )
        else:
            terminal_event = conn.execute(
                "SELECT created_at FROM events WHERE event_type='V2_CAMPAIGN_TERMINAL' "
                "ORDER BY seq DESC LIMIT 1",
            ).fetchone()
            final_report["campaign_terminal_status"] = "GOAL_MET"
            final_report.setdefault(
                "terminalized_at", terminal_event["created_at"] if terminal_event else utc_now(),
            )
        conn.commit()
    except Exception:
        if conn is not None and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()
        lock.release()

    raw = (json.dumps(final_report, sort_keys=True, indent=2,
                      ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_write_report(report_path, raw)
    _atomic_write_report(
        report_path.with_suffix(".md"),
        _render_final_report_markdown(final_report).encode("utf-8"),
    )
    result["parent_campaign_terminal_status"] = "GOAL_MET"
    result["final_report_path"] = str(report_path)
    result["final_report_markdown_path"] = str(report_path.with_suffix(".md"))
    return result


def _approved_protocol_has_no_holdout(
    state_root: Path, campaign_id: str, expected_protocol_digest: str,
) -> bool:
    database = state_root / "campaign.sqlite3"
    if not database.is_file() or database.is_symlink():
        return False
    graph = None
    try:
        graph = GraphRepository.open(str(database), campaign_id=campaign_id)
        spec = graph.campaign_spec()
        if not isinstance(spec, OptimizationCampaignSpecV2):
            return False
        reference = spec.evaluation
        authority = EvaluatorProtocolAuthority(graph.conn)
        authority.require_frozen_reference(
            reference.protocol_id, reference.version, reference.content_sha256,
            reference.approval_digest, campaign_id=campaign_id,
            spec_hash=spec.approved_hash(),
        )
        proposal, _ = authority.approved_protocol(reference.approval_digest)
        return (
            proposal.protocol.content_digest() == expected_protocol_digest
            and not any(item.tier == "holdout" for item in proposal.protocol.tiers)
        )
    except Exception:
        return False
    finally:
        if graph is not None:
            graph.close()


def _saved_completed_run_report(state_root: Path, campaign_id: str) -> dict[str, Any] | None:
    path = state_root / "reports" / f"v2-optimization-{campaign_id}.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    verification_pass = (isinstance(report, dict) and any(
        item.get("status") == "INDEPENDENT_VERIFICATION_PASS"
        for item in report.get("verification", []) if isinstance(item, dict)
    ))
    integration_pass = (isinstance(report, dict) and any(
        item.get("decision") == "PASS"
        for item in report.get("integration_evidence", []) if isinstance(item, dict)
    ))
    holdout = report.get("holdout") if isinstance(report, dict) else None
    holdout_verified = isinstance(holdout, dict) and holdout.get("status") == "VERIFIED"
    holdout_not_configured = (
        isinstance(holdout, dict)
        and holdout.get("status") == "NOT_CONFIGURED"
        and holdout.get("tier") == "holdout"
        and isinstance(holdout.get("protocol_digest"), str)
        and _approved_protocol_has_no_holdout(
            state_root, campaign_id, holdout["protocol_digest"],
        )
    )
    if (not isinstance(report, dict) or report.get("campaign_id") != campaign_id
            or report.get("status") != "ALL_NODES_COMPLETE"
            or not isinstance(report.get("baseline_candidate_delta"), dict)
            or report["baseline_candidate_delta"].get("status") != "VERIFIED"
            or not (verification_pass or integration_pass)
            or not (holdout_verified or holdout_not_configured)):
        return None
    return report


def _role_transport(role: Any, role_id: str) -> None:
    if (role.role_id != role_id or role.adapter != "codex" or role.provider != "openai"
            or not role.model.strip()):
        raise V2OptimizationCLIError(
            f"pinned {role_id} must use the official OpenAI Codex transport",
        )


def _initial_optimization_task_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select only the Researcher-bound root task, excluding candidate nodes."""
    return [
        node for node in nodes
        if node.get("phase") == "OPTIMIZATION"
        and isinstance(node.get("payload"), dict)
        and isinstance(node["payload"].get("hypothesis"), str)
        and node["payload"].get("owner_approved_task") is not False
    ]


def _ensure_researcher_selected_task(graph: GraphRepository,
                                    spec: OptimizationCampaignSpecV2) -> None:
    """Seed the child graph from the Researcher task frozen by owner approval."""
    task = spec.optimization_task
    if task is None:
        return  # Backward-compatible for existing hand-created Optimization specs.
    authority = EvaluatorProtocolAuthority(graph.conn)
    proposal, _receipt = authority.approved_protocol(spec.evaluation.approval_digest)
    if task.source_snapshot_id != proposal.protocol.source_snapshot_id:
        raise V2OptimizationCLIError(
            "approved Researcher task is pinned to a different Optimization source snapshot",
        )
    node_id = f"optimization-task-{task.hypothesis_id}"
    nodes = graph.scheduling_snapshot()["nodes"]
    # Candidate snapshots are also OPTIMIZATION graph nodes. Only count the
    # root task node seeded from Researcher evidence when checking this
    # owner-approved child binding; otherwise a resumed child with a candidate
    # is incorrectly rejected as having multiple initial tasks.
    existing = _initial_optimization_task_nodes(nodes)
    if existing:
        if len(existing) != 1:
            raise V2OptimizationCLIError("approved child has multiple initial Optimization tasks")
        payload = existing[0]["payload"]
        if (existing[0]["node_id"] != node_id
                or payload.get("hypothesis_id") != task.hypothesis_id
                or payload.get("hypothesis") != task.hypothesis
                or payload.get("source_snapshot_id") != task.source_snapshot_id):
            raise V2OptimizationCLIError(
                "child Optimization node differs from its owner-approved Researcher task",
            )
        return
    graph.mutate(MutateGraph(
        command_id=f"seed-approved-optimization-task-{task.hypothesis_id}",
        idempotency_key=f"seed-approved-optimization-task-{task.hypothesis_id}",
        expected_graph_revision=graph.graph_revision(),
        changes=(AddNode(
            type="AddNode", node_id=node_id, phase="OPTIMIZATION",
            payload={
                "source_snapshot_id": task.source_snapshot_id,
                "hypothesis_id": task.hypothesis_id,
                "hypothesis": task.hypothesis,
                "evidence_refs": list(task.evidence_refs),
                "owner_approved_task": True,
                "candidate_iteration": 1,
                "research_source": {
                    "campaign_id": task.source_campaign_id,
                    "node_id": task.source_node_id,
                    "job_id": task.source_job_id,
                    "portfolio_sha256": task.source_artifact_sha256,
                },
            },
        ),),
    ))


def _safe_paths(visible_paths: list[str], write_paths: list[str]) -> tuple[list[str], list[str]]:
    if not 1 <= len(visible_paths) <= 16 or not 1 <= len(write_paths) <= 16:
        raise V2OptimizationCLIError("provide 1 to 16 --visible-path and --write-path values")
    visible = [_safe_path(path) for path in visible_paths]
    writes = [_safe_path(path) for path in write_paths]
    if (len(visible) != len(set(visible)) or len(writes) != len(set(writes))
            or not set(writes) <= set(visible)):
        raise V2OptimizationCLIError("candidate write paths must be unique members of visible paths")
    return sorted(visible), sorted(writes)


def run_v2_optimize(args: Namespace, *,
                    optimizer_factory: Callable[..., Any] | None = None,
                    verifier_factory: Callable[..., Any] | None = None,
                    evaluator_runner_factory: Callable[[Any], Any] | None = None) -> dict[str, Any]:
    """Run one bounded Optimization child using its frozen spec and role registry.

    Provider request allowance is shared by optimizer and integration verifier.
    Trusted evaluation runs in Core and does not spend model requests.
    """
    benchmark_time_only = getattr(args, "benchmark_time_only", False)
    if not isinstance(benchmark_time_only, bool):
        raise V2OptimizationCLIError("benchmark_time_only must be a boolean")
    execution_ceiling = 1000 if benchmark_time_only else 100
    if (isinstance(args.approve_up_to_calls, bool)
            or not 0 <= args.approve_up_to_calls <= execution_ceiling
            or isinstance(args.max_steps, bool) or not 1 <= args.max_steps <= execution_ceiling
            or isinstance(args.timeout_seconds, bool) or not 1 <= args.timeout_seconds <= 300):
        raise V2OptimizationCLIError("call, step, or timeout bounds are invalid")
    task_deadline_epoch = getattr(args, "task_deadline_epoch", None)
    if task_deadline_epoch is not None and (
        isinstance(task_deadline_epoch, bool)
        or not isinstance(task_deadline_epoch, (int, float))
        or not math.isfinite(task_deadline_epoch)
        or task_deadline_epoch <= 0
    ):
        raise V2OptimizationCLIError("task_deadline_epoch must be a positive finite epoch")
    initial_model_timeout = _task_request_timeout(
        float(task_deadline_epoch) if task_deadline_epoch is not None else None,
        args.timeout_seconds,
    )
    if not isinstance(args.campaign_id, str) or not args.campaign_id.strip():
        raise V2OptimizationCLIError("--campaign-id is required")
    visible_paths, write_paths = _safe_paths(args.visible_path, args.write_path)
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir() or repo.is_symlink():
        raise V2OptimizationCLIError("--repo must be a real local repository directory")
    role_path = Path(args.role_registry).expanduser().resolve()
    paths = RuntimePaths.for_repo(
        repo, Path(args.state_dir).expanduser() if args.state_dir else None,
    )
    registry = CampaignRegistry(repo, paths=paths)
    try:
        state_root, resolved_id = registry.resolve_readonly(args.campaign_id)
    except RegistryError as exc:
        raise V2OptimizationCLIError(str(exc)) from exc
    if resolved_id != args.campaign_id:
        raise V2OptimizationCLIError("resolved campaign identity does not match --campaign-id")
    database = state_root / "campaign.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise V2OptimizationCLIError("campaign has no safe initialized v2 database")

    graph = GraphRepository.open(str(database), campaign_id=args.campaign_id)
    queue = registry.worker_queue()
    try:
        spec = graph.campaign_spec()
        if not isinstance(spec, OptimizationCampaignSpecV2):
            raise V2OptimizationCLIError("campaign is not an approved v2 Optimization child")
        if benchmark_time_only and (
                spec.autonomy.mode != "FULL_AUTO"
                or spec.max_candidate_iterations is not None
                or spec.budget.max_agent_calls > 1000
                or spec.budget.max_evaluator_calls > 1000):
            raise V2OptimizationCLIError(
                "time-only execution requires a benchmark Full Auto child without an iteration cap "
                "and with disclosed technical budgets at or below 1000"
            )
        if spec.source_repo != str(repo):
            raise V2OptimizationCLIError("campaign source repository does not match --repo")
        try:
            reference = spec.evaluation
            protocol_authority = EvaluatorProtocolAuthority(graph.conn)
            protocol_authority.require_frozen_reference(
                reference.protocol_id, reference.version, reference.content_sha256,
                reference.approval_digest, campaign_id=args.campaign_id,
                spec_hash=spec.approved_hash(),
            )
            protocol_proposal, _ = protocol_authority.approved_protocol(
                reference.approval_digest,
            )
        except EvaluatorVerificationError as exc:
            raise V2OptimizationCLIError(
                "Optimization child lacks its exact independently approved evaluator handoff",
            ) from exc
        try:
            role_registry = RoleRegistryV1.load(
                role_path, expected_digest=spec.role_registry_digest,
            )
        except RoleContractError as exc:
            raise V2OptimizationCLIError(str(exc)) from exc
        optimizer_role = role_registry.role("optimizer")
        verifier_role = role_registry.role("integration_verifier")
        _role_transport(optimizer_role, "optimizer")
        _role_transport(verifier_role, "integration_verifier")

        evaluator_runner = (
            evaluator_runner_factory(protocol_proposal.protocol)
            if evaluator_runner_factory is not None else None
        )
        trusted_kwargs = {
            "graph": graph, "queue": queue, "campaign_id": args.campaign_id,
            "state_root": state_root,
        }
        if evaluator_runner_factory is not None:
            trusted_kwargs["runner"] = evaluator_runner
        trusted = TrustedEvaluatorAdapter(**trusted_kwargs)
        try:
            trusted._require_execution_backend(protocol_proposal.protocol)
        except TrustedEvaluatorError as exc:
            raise V2OptimizationCLIError(str(exc)) from exc
        integration = IntegrationVerificationAdapter(
            graph=graph, queue=queue, campaign_id=args.campaign_id,
            trusted_evaluator=trusted,
        )
        runtime = V2OptimizationRuntime(
            graph=graph, queue=queue, campaign_id=args.campaign_id,
            state_root=state_root, role_registry=role_registry,
            trusted_evaluator=trusted, integration_verifier=integration,
        )
        plan = runtime.plan()
        if args.approve_up_to_calls == 0:
            saved_report = _saved_completed_run_report(state_root, args.campaign_id)
            if plan.reason == "ALL_NODES_COMPLETE" and saved_report is not None:
                # A completed graph with durable verification/HOLDOUT evidence
                # can recover terminal state without auth, model calls, or
                # repeating evaluator work after a report-writing failure.
                result = runtime.run_campaign(
                    visible_paths=visible_paths, write_paths=write_paths,
                    max_steps=args.max_steps, benchmark_time_only=benchmark_time_only,
                    worker_id="v2-optimization-cli-recovery",
                )
                campaign_call_count = len(saved_report.get("provider_calls", []))
                final_report_path = result.get("final_report_path")
                if isinstance(final_report_path, str):
                    try:
                        final_report = json.loads(Path(final_report_path).read_text(encoding="utf-8"))
                        if isinstance(final_report, dict):
                            campaign_call_count = len(final_report.get("provider_calls", []))
                    except (OSError, ValueError):
                        pass
                result.update({
                    "mode": "OPTIMIZATION", "profile": "FULL",
                    "role_registry_digest": role_registry.digest(),
                    "live_model_called": False, "provider_call_attempts": 0,
                    "request_history": {"optimizer": [], "integration_verifier": []},
                    "automatic_replay": False,
                    "recovery_mode": "FINALIZATION_ONLY",
                    "campaign_provider_call_count": campaign_call_count,
                    "optimization_child_provider_call_count": len(saved_report.get("provider_calls", [])),
                    "recovered_report_path": str(
                        state_root / "reports" / f"v2-optimization-{args.campaign_id}.json"
                    ),
                })
                result = _close_completed_child_allocation(
                    result, spec=spec, database=database, registry=registry,
                )
                result["recovery_report_path"] = str(
                    state_root / "reports" /
                    f"v2-optimization-cli-{args.campaign_id}-recovery.json"
                )
                result["state_dir"] = str(state_root)
                _write_cli_report(
                    state_root, result, campaign_id=args.campaign_id, suffix="recovery",
                )
                return result
            return {
                "status": "READY_FOR_OWNER_APPROVAL", "mode": "OPTIMIZATION",
                "profile": "FULL", "campaign_id": args.campaign_id,
                "spec_hash": spec.approved_hash(), "role_registry_digest": role_registry.digest(),
                "visible_paths": visible_paths, "write_paths": write_paths,
                "scheduler_status": plan.reason, "state_dir": str(state_root),
                "live_model_called": False,
            }

        _ensure_researcher_selected_task(graph, spec)
        plan = runtime.plan()
        try:
            auth = ChatGPTPlanAuth()
            if not auth.status()["connected"]:
                raise V2OptimizationCLIError(
                    "ChatGPT is not connected; use bananloop-chatgpt status first",
                )
        except SIWCError as exc:
            raise V2OptimizationCLIError(str(exc)) from exc
        def observe(role_id: str):
            return lambda receipt: _record_provider_call(
                database, {"role_id": role_id, **receipt},
            )

        optimizer_ctor = optimizer_factory or ChatGPTPlanOptimizer
        verifier_ctor = verifier_factory or ChatGPTPlanIntegrationVerifier
        optimizer = optimizer_ctor(
            auth, approved_model=optimizer_role.model,
            timeout_seconds=initial_model_timeout,
            call_observer=observe("optimizer"),
        )
        verifier = verifier_ctor(
            auth, approved_model=verifier_role.model,
            timeout_seconds=initial_model_timeout,
            call_observer=observe("integration_verifier"),
        )
        try:
            optimizer.check_model()
            verifier.check_model()
        except SIWCError as exc:
            raise V2OptimizationCLIError(str(exc)) from exc

        team_request = TeamRequestV1(
            campaign_id=args.campaign_id, spec_hash=spec.approved_hash(),
            # This role registry is shared with the Exploration parent, where
            # these roles are intentionally optional. The Optimization child
            # must instantiate its two execution roles explicitly.
            requested_instances={"optimizer": 1, "integration_verifier": 1},
            available_agent_calls=spec.budget.max_agent_calls,
            available_model_tokens=spec.budget.max_model_tokens,
        )
        try:
            built_team = TeamFactoryV1.build(
                role_registry, team_request,
                expected_registry_digest=spec.role_registry_digest,
            )
            authority = TeamManifestAuthority(queue.path)
            try:
                manifest = authority.pin(built_team)
            finally:
                authority.close()
        except RoleContractError as exc:
            raise V2OptimizationCLIError(str(exc)) from exc
        verifier_members = [member for member in manifest.members
                            if member.role.role_id == "integration_verifier"]
        if len(verifier_members) != 1:
            raise V2OptimizationCLIError("pinned team must include exactly one integration verifier")

        spent = 0

        def consume_approval() -> None:
            nonlocal spent
            if spent >= args.approve_up_to_calls:
                raise _CallAllowanceReached(
                    "owner-approved model-call allowance is exhausted; resume with a new explicit allowance",
                )
            spent += 1

        def bounded_optimizer(public_input: dict[str, Any], **pin: Any) -> dict[str, Any]:
            timeout = _task_request_timeout(
                float(task_deadline_epoch) if task_deadline_epoch is not None else None,
                args.timeout_seconds,
            )
            consume_approval()
            result = optimizer(public_input, **{**pin, "timeout_seconds": timeout})
            bounded_optimizer.last_usage = getattr(optimizer, "last_usage", None)
            return result

        bounded_optimizer.last_usage = None

        def bounded_verifier(request: dict[str, Any], gateway: Any, **pin: Any) -> dict[str, Any]:
            timeout = _task_request_timeout(
                float(task_deadline_epoch) if task_deadline_epoch is not None else None,
                args.timeout_seconds,
            )
            consume_approval()
            result = verifier._dispatch_verifier(
                request, gateway, **{**pin, "timeout_seconds": timeout},
            )
            bounded_verifier.last_usage = getattr(verifier, "last_usage", None)
            return result

        bounded_verifier.last_usage = None

        verifier_executor = PinnedRoleExecutorV1.from_manifest(
            manifest, verifier_members[0].instance_id, bounded_verifier,
        )
        providers = {"optimizer": optimizer, "integration_verifier": verifier}
        try:
            result = runtime.run_campaign(
                visible_paths=visible_paths, write_paths=write_paths,
                model_reply=bounded_optimizer,
                integration_verifier_executor=verifier_executor,
                max_steps=args.max_steps, benchmark_time_only=benchmark_time_only,
                worker_id="v2-optimization-cli",
            )
            if benchmark_time_only and (
                result.get("status") in {"STEP_LIMIT", "BUDGET_EXHAUSTED"}
                or spent >= execution_ceiling
            ):
                result["technical_limit_status"] = result.get("status")
                result["status"] = "LIMIT_EXHAUSTED"
                result["technical_call_ceiling"] = execution_ceiling
                result["technical_step_ceiling"] = args.max_steps
            result.update({
                "mode": "OPTIMIZATION", "profile": "FULL",
                "role_registry_digest": role_registry.digest(),
                "provider": "chatgpt_plan", "live_model_called": spent > 0,
                "provider_call_attempts": spent,
                "request_history": {
                    name: list(getattr(provider, "request_history", []))
                    for name, provider in (("optimizer", optimizer),
                                           ("integration_verifier", verifier))
                },
                "automatic_replay": False,
            })
            _attach_request_usage(result, spent=spent)
            result = _close_completed_child_allocation(
                result, spec=spec, database=database, registry=registry,
            )
            return result
        except _CallAllowanceReached as exc:
            report = {
                "status": "LIMIT_EXHAUSTED" if benchmark_time_only else "OWNER_APPROVAL_REQUIRED",
                "mode": "OPTIMIZATION",
                "campaign_id": args.campaign_id, "provider_call_attempts": spent,
                "failure_type": type(exc).__name__, "usage": "UNKNOWN",
                "technical_call_ceiling": 1000 if benchmark_time_only else None,
                "automatic_replay": False,
                "request_history": {
                    name: list(getattr(provider, "request_history", []))
                    for name, provider in providers.items()
                },
            }
            _attach_request_usage(report, spent=spent)
            report["report_path"] = _write_cli_report(
                state_root, report, campaign_id=args.campaign_id,
            )
            return {**report, "state_dir": str(state_root), "live_model_called": spent > 0}
        except Exception as exc:
            # The worker queue preserves dispatched outcomes. The CLI returns a
            # typed summary and never retries a provider request after uncertainty.
            from .provider_diagnostics import safe_chatgpt_failure_diagnostics, safe_failure_site

            report = {
                "status": "RUN_FAILED", "mode": "OPTIMIZATION", "profile": "FULL",
                "campaign_id": args.campaign_id, "failure_type": type(exc).__name__,
                "failure_detail": (_public_evaluator_failure_detail(exc)
                                   or getattr(exc, "failure_detail", None)
                                   or (str(exc)[:500] if isinstance(
                                       exc, (V2OptimizationError, TrustedEvaluatorError)
                                   ) else None)),
                "provider_call_attempts": spent, "usage": "UNKNOWN",
                "live_model_called": spent > 0, "automatic_replay": False,
                "request_history": {
                    name: list(getattr(provider, "request_history", []))
                    for name, provider in providers.items()
                },
            }
            site = safe_failure_site(exc)
            if site is not None:
                report["failure_site"] = site
            diagnostics = safe_chatgpt_failure_diagnostics(exc)
            if diagnostics:
                report["failure_diagnostics"] = diagnostics
            _attach_request_usage(report, spent=spent)
            report["report_path"] = _write_cli_report(
                state_root, report, campaign_id=args.campaign_id,
            )
            return {**report, "state_dir": str(state_root)}
    finally:
        queue.close()
        graph.close()
