"""Owner-facing execution of an already-approved v2 Optimization child."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
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
from .trusted_evaluator import TrustedEvaluatorAdapter, TrustedEvaluatorError
from .v2_optimization_runtime import V2OptimizationError, V2OptimizationRuntime, _safe_path


class V2OptimizationCLIError(ValueError):
    """The requested v2 Optimization run is not safely executable."""


class _CallAllowanceReached(V2OptimizationCLIError):
    pass


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
    result["campaign_terminal_status"] = "GOAL_MET"
    result["child_allocation_closed"] = allocation_closed
    return result


def _saved_completed_run_report(state_root: Path, campaign_id: str) -> dict[str, Any] | None:
    path = state_root / "reports" / f"v2-optimization-{campaign_id}.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(report, dict) or report.get("campaign_id") != campaign_id
            or report.get("status") != "ALL_NODES_COMPLETE"
            or not isinstance(report.get("baseline_candidate_delta"), dict)
            or report["baseline_candidate_delta"].get("status") != "VERIFIED"
            or not any(item.get("status") == "INDEPENDENT_VERIFICATION_PASS"
                       for item in report.get("verification", []) if isinstance(item, dict))
            or not isinstance(report.get("holdout"), dict)
            or report["holdout"].get("status") != "VERIFIED"):
        return None
    return report


def _role_transport(role: Any, role_id: str) -> None:
    if (role.role_id != role_id or role.adapter != "codex" or role.provider != "openai"
            or not role.model.strip()):
        raise V2OptimizationCLIError(
            f"pinned {role_id} must use the official OpenAI Codex transport",
        )


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
    existing = [node for node in nodes if node["phase"] == "OPTIMIZATION"]
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
                    verifier_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Run one bounded Optimization child using its frozen spec and role registry.

    Provider request allowance is shared by optimizer and integration verifier.
    Trusted evaluation runs in Core and does not spend model requests.
    """
    if (isinstance(args.approve_up_to_calls, bool)
            or not 0 <= args.approve_up_to_calls <= 100
            or isinstance(args.max_steps, bool) or not 1 <= args.max_steps <= 100
            or isinstance(args.timeout_seconds, bool) or not 1 <= args.timeout_seconds <= 300):
        raise V2OptimizationCLIError("call, step, or timeout bounds are invalid")
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
        if spec.source_repo != str(repo):
            raise V2OptimizationCLIError("campaign source repository does not match --repo")
        try:
            reference = spec.evaluation
            EvaluatorProtocolAuthority(graph.conn).require_frozen_reference(
                reference.protocol_id, reference.version, reference.content_sha256,
                reference.approval_digest, campaign_id=args.campaign_id,
                spec_hash=spec.approved_hash(),
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

        trusted = TrustedEvaluatorAdapter(
            graph=graph, queue=queue, campaign_id=args.campaign_id, state_root=state_root,
        )
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
                    max_steps=args.max_steps, worker_id="v2-optimization-cli-recovery",
                )
                result.update({
                    "mode": "OPTIMIZATION", "profile": "FULL",
                    "role_registry_digest": role_registry.digest(),
                    "live_model_called": False, "provider_call_attempts": 0,
                    "request_history": {"optimizer": [], "integration_verifier": []},
                    "automatic_replay": False,
                    "recovery_mode": "FINALIZATION_ONLY",
                    "campaign_provider_call_count": len(saved_report.get("provider_calls", [])),
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
            timeout_seconds=args.timeout_seconds,
            call_observer=observe("optimizer"),
        )
        verifier = verifier_ctor(
            auth, approved_model=verifier_role.model,
            timeout_seconds=args.timeout_seconds,
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
            consume_approval()
            result = optimizer(public_input, **pin)
            bounded_optimizer.last_usage = getattr(optimizer, "last_usage", None)
            return result

        bounded_optimizer.last_usage = None

        def bounded_verifier(request: dict[str, Any], gateway: Any, **pin: Any) -> dict[str, Any]:
            consume_approval()
            result = verifier._dispatch_verifier(request, gateway, **pin)
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
                max_steps=args.max_steps, worker_id="v2-optimization-cli",
            )
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
            result = _close_completed_child_allocation(
                result, spec=spec, database=database, registry=registry,
            )
            return result
        except _CallAllowanceReached as exc:
            report = {
                "status": "OWNER_APPROVAL_REQUIRED", "mode": "OPTIMIZATION",
                "campaign_id": args.campaign_id, "provider_call_attempts": spent,
                "failure_type": type(exc).__name__, "usage": "UNKNOWN",
                "automatic_replay": False,
                "request_history": {
                    name: list(getattr(provider, "request_history", []))
                    for name, provider in providers.items()
                },
            }
            report["report_path"] = _write_cli_report(
                state_root, report, campaign_id=args.campaign_id,
            )
            return {**report, "state_dir": str(state_root), "live_model_called": spent > 0}
        except Exception as exc:
            # The worker queue preserves dispatched outcomes. The CLI returns a
            # typed summary and never retries a provider request after uncertainty.
            report = {
                "status": "RUN_FAILED", "mode": "OPTIMIZATION", "profile": "FULL",
                "campaign_id": args.campaign_id, "failure_type": type(exc).__name__,
                "failure_detail": str(exc)[:500] if isinstance(
                    exc, (V2OptimizationError, TrustedEvaluatorError)
                ) else None,
                "provider_call_attempts": spent, "usage": "UNKNOWN",
                "live_model_called": spent > 0, "automatic_replay": False,
                "request_history": {
                    name: list(getattr(provider, "request_history", []))
                    for name, provider in providers.items()
                },
            }
            report["report_path"] = _write_cli_report(
                state_root, report, campaign_id=args.campaign_id,
            )
            return {**report, "state_dir": str(state_root)}
    finally:
        queue.close()
        graph.close()
