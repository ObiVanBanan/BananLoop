"""Owner-facing execution of an already-approved v2 Optimization child."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable

from .chatgpt_plan import ChatGPTPlanAuth, SIWCError
from .chatgpt_responses import ChatGPTPlanIntegrationVerifier, ChatGPTPlanOptimizer
from .contracts import OptimizationCampaignSpecV2, RuntimePaths, utc_now
from .evaluator_verification import (
    EvaluatorVerificationError, EvaluatorProtocolAuthority,
    IntegrationVerificationAdapter,
)
from .graph_store import GraphRepository
from .registry import CampaignRegistry, RegistryError
from .roles import (
    PinnedRoleExecutorV1, RoleContractError, RoleRegistryV1, TeamFactoryV1,
    TeamManifestAuthority, TeamRequestV1,
)
from .trusted_evaluator import TrustedEvaluatorAdapter
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


def _write_cli_report(state_root: Path, report: dict[str, Any], *, campaign_id: str) -> str:
    reports = state_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    target = reports / f"v2-optimization-cli-{campaign_id}.json"
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


def _role_transport(role: Any, role_id: str) -> None:
    if (role.role_id != role_id or role.adapter != "codex" or role.provider != "openai"
            or not role.model.strip()):
        raise V2OptimizationCLIError(
            f"pinned {role_id} must use the official OpenAI Codex transport",
        )


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
            return {
                "status": "READY_FOR_OWNER_APPROVAL", "mode": "OPTIMIZATION",
                "profile": "FULL", "campaign_id": args.campaign_id,
                "spec_hash": spec.approved_hash(), "role_registry_digest": role_registry.digest(),
                "visible_paths": visible_paths, "write_paths": write_paths,
                "scheduler_status": plan.reason, "state_dir": str(state_root),
                "live_model_called": False,
            }

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
            requested_instances={role.role_id: role.min_instances for role in role_registry.roles},
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
            return optimizer(public_input, **pin)

        def bounded_verifier(request: dict[str, Any], gateway: Any, **pin: Any) -> dict[str, Any]:
            consume_approval()
            return verifier._dispatch_verifier(request, gateway, **pin)

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
                "failure_detail": str(exc)[:500] if isinstance(exc, V2OptimizationError) else None,
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
