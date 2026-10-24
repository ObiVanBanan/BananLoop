from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .agents import AgentAdapter, AgentError, CodexAdapter, CommandAgentAdapter, PiAdapter
from .budget import deadline_reached, pre_action_stop
from .token_budget import ModelTokenBudgetExceeded
from .artifacts import retain_artifacts
from .contracts import (
    CampaignSpec,
    CampaignStatus,
    EvaluationResult,
    FailureCapsule,
    FailureAnalysisResult,
    Hypothesis,
    HypothesisPortfolio,
    ImplementResult,
    PortfolioRecord,
    ResearchState,
    StopReason,
)
from .envfiles import project_environment
from .evaluators import CommandEvaluator, EvaluatorError
from .integrity import evaluation_fingerprint, verify_evaluation_assets
from .evaluator_binding import (
    EvaluationReceipt, EvaluatorBinding, canonical_hash, make_binding,
    make_receipt, runtime_descriptor, validate_receipt_linkage,
)
from .leakage import scan_forbidden_diff_patterns
from .policy import InvalidEvaluation, decide, target_met, validate_evaluation
from .process import ProcessCancelled, check_process_control, process_control
from .research import (
    attempt_bucket,
    agent_decision_view,
    agent_evaluation_view,
    build_history,
    build_research_state,
    capsule_evidence_ref,
    classify_dev_outcome,
    redundancy_reason,
    normalize_portfolio,
    select_candidate,
)
from .store import CampaignStore
from .workspace import WorkspaceError, WorkspaceManager

logger = logging.getLogger(__name__)


class ControlledInterruption(RuntimeError):
    pass


class EngineError(RuntimeError):
    pass


class UnknownActionOutcome(EngineError):
    pass


class CampaignHalted(Exception):
    """A budget gate has durably stopped the campaign; unwind the active stage."""


class BindingRuntimeMismatch(EngineError):
    pass


def _hash_payload(payload: dict[str, Any]) -> str:
    durable = dict(payload)
    # candidate_proof is derived deterministically from the immutable snapshot.
    # Excluding it preserves replay compatibility with evaluator actions created
    # before provenance proofs were added; the proof is re-verified before every
    # candidate evaluation.
    durable.pop("candidate_proof", None)
    return hashlib.sha256(
        json.dumps(durable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Engine:
    def __init__(
        self,
        store: CampaignStore,
        workspace: WorkspaceManager,
        progress: Callable[[str], None] | None = None,
    ):
        self.store = store
        self.workspace = workspace
        self._progress_callback = progress
        self._retry_running_actions = False
        self._retry_failed_actions = False
        self._agent_timeout_seconds: int | None = None
        self._active_model_token_action_id: str | None = None
        self.spec = store.spec()
        self._evaluator_binding: EvaluatorBinding | None = None
        trusted_env = project_environment(Path(self.spec.source_repo))
        self.evaluator = CommandEvaluator(
            self.spec.evaluation_command, base_env=trusted_env
        )
        self.validation_evaluator = (
            CommandEvaluator(self.spec.validation_command, base_env=trusted_env)
            if self.spec.validation_command
            else None
        )
        self.holdout_evaluator = (
            CommandEvaluator(self.spec.holdout_command, base_env=trusted_env)
            if self.spec.holdout_command
            else None
        )
        self.agent: AgentAdapter = self._make_agent_adapter(self.spec.agent)

    def _progress(self, message: str) -> None:
        if self._progress_callback:
            self._progress_callback(message)

    def _agent_timeout(self, default: int) -> int:
        return self._agent_timeout_seconds or default

    def _agent_hidden_paths(self) -> list[str]:
        visible_training = set(self.spec.agent_visible_training_paths)
        frozen = [
            asset.path for asset in self.spec.evaluation_assets
            if asset.path not in visible_training
        ]
        return list(dict.fromkeys([*self.spec.agent_hidden_paths, *frozen]))

    def _agent_protected_paths(self) -> list[str]:
        hidden = self._agent_hidden_paths()
        return [
            path
            for path in self.spec.protected_paths
            if not self.workspace._matches(path, hidden)
        ]

    def _agent_evaluation_view(self, payload_json: str | None) -> dict[str, Any] | None:
        return agent_evaluation_view(self.spec, payload_json)

    def _agent_decision_view(self, decision_json: str | None) -> dict[str, Any] | None:
        if not decision_json:
            return None
        decision = json.loads(decision_json)
        decision.pop("validation", None)
        predicates = dict(decision.get("predicates") or {})
        predicates.pop("validation", None)
        decision["predicates"] = predicates
        decision["reasons"] = [
            reason
            for reason in (decision.get("reasons") or [])
            if reason not in {"VALIDATION_REJECTED", "INVALID_VALIDATION"}
            and not reason.startswith("VALIDATION:")
        ]
        return agent_decision_view(self.spec, decision)

    def _invoke_agent(
        self,
        task: dict[str, Any],
        *,
        timeout: int,
        apply_changes: bool,
    ) -> dict[str, Any]:
        hidden = self._agent_hidden_paths()
        agent_workspace = self.workspace.prepare_agent_workspace(hidden)
        try:
            role = str(task.get("role", "propose"))
            adapter = (
                self._make_agent_adapter(self.spec.agent.for_role(role))
                if role in self.spec.agent.roles
                else self.agent
            )
            metered = self.spec.budget.max_model_tokens is not None
            if metered:
                if not isinstance(adapter, CodexAdapter):
                    raise AgentError("model token cap requires a metered CodexAdapter; non-Codex usage is UNKNOWN")
                adapter.meter_tokens = True
            try:
                result = adapter.execute(task, agent_workspace, timeout=timeout)
            except Exception:
                if metered and adapter.last_token_usage is not None:
                    # Preserve reported usage even when the agent returned an error.
                    self.store.settle_model_tokens(self._active_model_token_action_id, adapter.last_token_usage)
                raise
            if metered:
                usage = adapter.last_token_usage
                if usage is None:
                    raise AgentError("model token usage UNKNOWN; refusing to apply unmetered candidate")
                exceeded = self.store.settle_model_tokens(self._active_model_token_action_id, usage)
                if exceeded:
                    raise ModelTokenBudgetExceeded(
                        f"token_budget_overshoot for {self._active_model_token_action_id}: "
                        f"{self.store.model_tokens_reserved()} > {self.spec.budget.max_model_tokens}"
                    )
            if apply_changes:
                self.workspace.apply_agent_changes(agent_workspace, hidden)
            return result
        finally:
            try:
                self.workspace.cleanup_agent_workspace()
            except WorkspaceError as exc:
                # Codex may create sandbox-owned cache directories inside its
                # disposable checkout. Keep a completed agent response usable;
                # candidate materialization and evaluator gates still run next.
                logger.warning("temporary agent workspace cleanup failed: %s", exc)

    @staticmethod
    def _make_agent_adapter(config):
        if config.kind == "codex":
            return CodexAdapter(config.model, config.extra_args, config.reasoning_effort)
        if config.kind == "pi":
            return PiAdapter(config.model, config.extra_args, config.command, config.provider, config.reasoning_effort)
        return CommandAgentAdapter(config.command)

    def _checkpoint(self, stage: str) -> None:
        if os.getenv("BANANLOOP_TEST_STOP_AFTER_STAGE") == stage:
            raise ControlledInterruption(f"forced interruption after durable stage {stage}")

    def _stop(self, reason: StopReason, status: CampaignStatus = CampaignStatus.STOPPED) -> dict[str, Any]:
        finalizable = reason not in {
            StopReason.PAUSED_INFRA,
            StopReason.PAUSED_NEEDS_INPUT,
            StopReason.USER_STOPPED,
            StopReason.DEADLINE_REACHED,
        }
        final_holdout: dict[str, Any] | None = None
        if finalizable and self.holdout_evaluator is not None:
            # Persist the terminal intent before any holdout observation. Resume
            # can only finish this assessment, never return to adaptive planning.
            self.store.update_campaign(stage="FINAL_HOLDOUT", stop_reason=reason.value)
            self._progress("final holdout: evaluate champion")
            try:
                final_holdout = self._finalize_holdout()
            except (
                EvaluatorError,
                InvalidEvaluation,
                ValidationError,
                UnknownActionOutcome,
                WorkspaceError,
                EngineError,
            ) as exc:
                self.store.update_campaign(
                    status=CampaignStatus.PAUSED_INFRA.value,
                    stop_reason=reason.value,
                    stage="FINAL_HOLDOUT",
                )
                self.store.event(
                    "FINAL_HOLDOUT_FAILED",
                    {"pending_stop_reason": reason.value, "error": str(exc)[:1000]},
                )
                self.store.conn.commit()
                raise EngineError(f"final holdout failed: {exc}") from exc

        if reason == StopReason.GOAL_MET and self.holdout_evaluator is not None:
            if final_holdout is None or not final_holdout["target_met"]:
                reason = StopReason.FINAL_HOLDOUT_REJECTED
                status = CampaignStatus.STOPPED

        if reason == StopReason.GOAL_MET:
            status = CampaignStatus.GOAL_MET
        if reason == StopReason.PAUSED_INFRA:
            status = CampaignStatus.PAUSED_INFRA
        if reason == StopReason.PAUSED_NEEDS_INPUT:
            status = CampaignStatus.PAUSED_NEEDS_INPUT
        if status in {CampaignStatus.STOPPED, CampaignStatus.GOAL_MET}:
            campaign = self.store.campaign()
            if campaign["active_attempt_id"] is not None:
                attempt = self.store.attempt(campaign["active_attempt_id"])
                if attempt["stage"] == "RECORD":
                    self._stage_record(attempt["id"], attempt["experiment_id"])
                else:
                    self.store.abandon_active_attempt(reason.value)
            snapshot = self.store.campaign()["champion_snapshot"]
            if snapshot and self.workspace.workspace.exists():
                self.workspace.checkout(snapshot)
        self.store.update_campaign(status=status.value, stop_reason=reason.value, stage=None)
        event_payload: dict[str, Any] = {"reason": reason.value, "status": status.value}
        if final_holdout is not None:
            event_payload["final_holdout_target_met"] = final_holdout["target_met"]
            event_payload["final_holdout_snapshot"] = final_holdout["snapshot"]
        self.store.event("CAMPAIGN_STOPPED", event_payload)
        self.store.conn.commit()
        return self.store.campaign()

    def _budget_or_stop(self, kind: str, stage: str | None = None) -> bool:
        control_reason = self._control_reason()
        if control_reason:
            raise ProcessCancelled(control_reason)
        campaign = self.store.campaign()
        if kind == "agent" and self.spec.budget.max_model_tokens is not None:
            reserve = self.store.model_token_reserve_for_stage(stage or "PROPOSE")
            available = self.spec.budget.max_model_tokens - self.store.model_tokens_reserved()
            if reserve > available:
                self.store.event("MODEL_TOKEN_RESERVE_BLOCKED", {
                    "stage": stage, "required_tokens": reserve,
                    "available_tokens": max(0, available),
                    "reason": "insufficient_token_reserve",
                })
                self.store.conn.commit()
                self._stop(StopReason.BUDGET_EXHAUSTED)
                return True
        if kind in {"evaluator", "final_evaluator"}:
            reserve = int(kind != "final_evaluator" and self.holdout_evaluator is not None)
            if campaign["evaluator_calls"] + 1 + reserve > self.spec.budget.max_evaluator_calls:
                if kind == "final_evaluator":
                    raise EngineError("no reserved evaluator call for final holdout")
                self._stop(StopReason.BUDGET_EXHAUSTED)
                return True
        if kind in {"evaluator", "final_evaluator"} and self.spec.budget.max_evaluation_units is not None:
            calls = 1 + int(kind != "final_evaluator" and self.holdout_evaluator is not None)
            if self.store.evaluation_work_reserved() + calls * self.spec.budget.evaluation_units_per_call > self.spec.budget.max_evaluation_units:
                if kind == "final_evaluator":
                    raise EngineError("no reserved work units for final holdout")
                self._stop(StopReason.BUDGET_EXHAUSTED)
                return True
        if kind == "final_evaluator":
            return False
        reason = pre_action_stop(self.store.campaign(), self.spec.budget, kind)
        if reason:
            self._stop(reason)
            return True
        return False

    def _control_reason(self) -> str | None:
        campaign = self.store.campaign()
        if campaign.get("stop_requested"):
            return StopReason.USER_STOPPED.value
        if deadline_reached(campaign["created_at"], self.spec.budget):
            return StopReason.DEADLINE_REACHED.value
        return None

    def _new_attempt_fits_evaluator_budget(self) -> bool:
        campaign = self.store.campaign()
        required = 1  # dev evaluation
        if self.validation_evaluator is not None:
            required += 1  # worst case: dev ACCEPT also needs validation
        if self.holdout_evaluator is not None:
            required += 1  # reserved final-only holdout slot
        if self.spec.budget.max_evaluation_units is not None:
            if self.store.evaluation_work_reserved() + required * self.spec.budget.evaluation_units_per_call > self.spec.budget.max_evaluation_units:
                return False
        return (
            campaign["evaluator_calls"] + required
            <= self.spec.budget.max_evaluator_calls
        )

    def _action(
        self,
        *,
        action_id: str,
        experiment_id: int | None,
        stage: str,
        kind: str,
        request: dict[str, Any],
        invoke: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if kind != "agent":
            violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
            if violations:
                raise EvaluatorError("frozen evaluation assets changed before measurement")
        existing = self.store.action(action_id)
        request_hash = _hash_payload(request)
        if existing:
            if existing["request_hash"] != request_hash:
                raise EngineError(f"action input mismatch for {action_id}")
            if existing["status"] == "COMPLETED":
                self._progress(f"{action_id}: reuse completed result")
                response = json.loads(existing["response_json"])
                if kind != "agent" and response.get("artifact_manifest"):
                    root = self.store.path.parent / "artifacts" / hashlib.sha256(action_id.encode()).hexdigest()[:24]
                    retained = EvaluationResult.model_validate(response)
                    retain_artifacts(retained, root, experiment_id, request["snapshot"])
                if kind != "agent":
                    self._verify_completed_receipt(action_id)
                return response
            if kind != "agent":
                self._ensure_evaluator_binding()
            if existing["status"] == "CANCELLED":
                if self._budget_or_stop(kind, stage):
                    raise CampaignHalted()
                self._progress(f"{action_id}: retry cancelled action")
                self.store.restart_action(action_id)
            elif existing["status"] == "RUNNING":
                if not self._retry_running_actions:
                    raise UnknownActionOutcome(
                        f"action {action_id} was running when process stopped; "
                        "resume with --retry-running-action only if you know the previous local process is gone"
                    )
                if self._budget_or_stop(kind, stage):
                    raise CampaignHalted()
                self._progress(f"{action_id}: explicitly retry orphaned RUNNING action")
                self.store.restart_action(action_id)
            elif existing["status"] == "FAILED":
                if not self._retry_failed_actions:
                    raise EngineError(
                        f"action {action_id} previously failed: {existing['error']}; "
                        "resume with --retry-failed-action to retry it"
                    )
                if self._budget_or_stop(kind, stage):
                    raise CampaignHalted()
                self._progress(f"{action_id}: explicitly retry FAILED action")
                self.store.restart_action(action_id)
            else:
                raise EngineError(
                    f"action {action_id} has unsupported prior status {existing['status']}"
                )
        else:
            if kind != "agent":
                self._ensure_evaluator_binding()
            if self._budget_or_stop(kind, stage):
                raise CampaignHalted()
            self.store.start_action(action_id, experiment_id, stage, kind, request_hash, request)
        self._progress(f"{action_id}: invoke {kind}")
        if kind == "agent":
            self._active_model_token_action_id = action_id
        output_dir = self.store.path.parent / "artifacts" / hashlib.sha256(action_id.encode()).hexdigest()[:24]
        if kind != "agent":
            output_dir.mkdir(parents=True, exist_ok=True)
            candidate_proof = request.get("candidate_proof")
            if candidate_proof:
                (output_dir / "candidate-proof.json").write_text(
                    json.dumps(candidate_proof, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            context = {
                "BANANLOOP_ARTIFACT_DIR": str(output_dir.resolve()),
                "BANANLOOP_EVALUATION_ID": action_id,
                "BANANLOOP_SNAPSHOT": request["snapshot"],
                "BANANLOOP_EVALUATION_TIER": request.get("tier", "dev"),
                "BANANLOOP_WORKSPACE": str(self.workspace.workspace.resolve()),
            }
            context.update(self._protocol_manifest_context(request.get("tier", "dev")))
            if candidate_proof:
                context["BANANLOOP_PARENT_SNAPSHOT"] = str(
                    candidate_proof["parent_snapshot"]
                )
                context["BANANLOOP_CANDIDATE_TREE"] = str(candidate_proof["tree"])
                context["BANANLOOP_CANDIDATE_DIFF_SHA256"] = str(
                    candidate_proof["diff_sha256"]
                )
                context["BANANLOOP_CANDIDATE_CHANGED_PATHS"] = json.dumps(
                    candidate_proof["changed_paths"],
                    ensure_ascii=False,
                )
            if self.spec.budget.evaluation_units_per_call is not None:
                context["BANANLOOP_EVALUATION_UNIT_LIMIT"] = str(self.spec.budget.evaluation_units_per_call)
            for evaluator in (self.evaluator, self.validation_evaluator, self.holdout_evaluator):
                if evaluator is not None:
                    evaluator.context = context
        try:
            with process_control(self._control_reason):
                check_process_control()
                response = invoke()
                check_process_control()
                if kind != "agent":
                    self._protocol_manifest_context(request.get("tier", "dev"))
            if kind != "agent":
                result = EvaluationResult.model_validate(response)
                ceiling = self.spec.budget.evaluation_units_per_call
                if ceiling is not None and (result.resource_units is None or result.resource_units > ceiling):
                    raise EvaluatorError("missing resource_units or reported work exceeds reserved limit")
                retain_artifacts(result, output_dir, experiment_id, request["snapshot"])
                violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
                if violations:
                    raise EvaluatorError(
                        "frozen evaluation assets changed during measurement: " + ", ".join(violations)
                    )
                receipt = make_receipt(
                    action_id=action_id,
                    tier=request.get("tier", "dev"),
                    snapshot=request["snapshot"],
                    candidate_proof=request.get("candidate_proof"),
                    binding=self._ensure_evaluator_binding(),
                    result=result,
                )
                sidecar = output_dir / "evaluation-receipt.json"
                tmp_sidecar = output_dir / ".evaluation-receipt.json.tmp"
                tmp_sidecar.write_text(
                    json.dumps(receipt.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                tmp_sidecar.replace(sidecar)
                response = result.model_dump(mode="json")
                response["_evaluation_receipt"] = receipt.model_dump(mode="json")
        except ModelTokenBudgetExceeded as exc:
            self.store.fail_action(action_id, str(exc))
            self.store.event("MODEL_TOKEN_OVERSHOOT", {
                "action_id": action_id,
                "stage": stage,
                "consumed_tokens": self.store.model_tokens_reserved(),
                "limit_tokens": self.spec.budget.max_model_tokens,
            })
            self.store.conn.commit()
            self._stop(StopReason.BUDGET_EXHAUSTED)
            raise CampaignHalted() from exc
        except ProcessCancelled as exc:
            self.store.cancel_action(action_id, exc.reason)
            raise
        except KeyboardInterrupt:
            self.store.cancel_action(action_id, "user interrupt")
            self._progress(f"{action_id}: cancelled by user")
            raise
        except Exception as exc:
            self.store.fail_action(action_id, str(exc))
            raise
        self.store.finish_action(action_id, response)
        self._progress(f"{action_id}: completed")
        return response

    def _ensure_evaluator_binding(self, *, verify_runtime: bool = True) -> EvaluatorBinding:
        commands = {"dev": self.spec.evaluation_command}
        if self.spec.validation_command:
            commands["validation"] = self.spec.validation_command
        if self.spec.holdout_command:
            commands["holdout"] = self.spec.holdout_command
        current = make_binding(self.spec, runtime_descriptor(commands))
        row = self.store.evaluator_binding()
        if row is None:
            self.store.save_evaluator_binding(
                current.binding_id,
                canonical_hash(current.model_dump(mode="json")),
                json.dumps(current.model_dump(mode="json"), sort_keys=True),
            )
            self._evaluator_binding = current
            self._write_binding_sidecar(current)
            return current
        stored = EvaluatorBinding.model_validate_json(row["binding_json"])
        if row["binding_hash"] != canonical_hash(stored.model_dump(mode="json")):
            raise BindingRuntimeMismatch("persisted evaluator binding hash is invalid")
        if stored.approved_spec_hash != self.spec.approved_hash() or stored.binding_id != current.binding_id and verify_runtime:
            raise BindingRuntimeMismatch("immutable evaluator binding does not match campaign specification/runtime")
        if verify_runtime and stored.runtime_fingerprint != current.runtime_fingerprint:
            raise BindingRuntimeMismatch("bounded evaluator runtime differs from immutable campaign binding")
        sidecar = self.store.path.parent / "artifacts" / "evaluator-binding.json"
        if sidecar.exists():
            try:
                sidecar_binding = EvaluatorBinding.model_validate_json(sidecar.read_text(encoding="utf-8"))
            except Exception as exc:
                raise BindingRuntimeMismatch("evaluator binding sidecar is invalid") from exc
            if sidecar_binding != stored:
                raise BindingRuntimeMismatch("evaluator binding sidecar differs from immutable database binding")
        else:
            self._write_binding_sidecar(stored)
        self._evaluator_binding = stored
        return stored

    def _write_binding_sidecar(self, binding: EvaluatorBinding) -> None:
        path = self.store.path.parent / "artifacts" / "evaluator-binding.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(".evaluator-binding.json.tmp")
        temp.write_text(
            json.dumps(binding.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temp.replace(path)

    def _verify_completed_receipt(self, action_id: str) -> None:
        stored = self.store.evaluation_receipt(action_id)
        if stored is None:  # Historical completed actions intentionally remain unreceipted.
            return
        action = self.store.action(action_id)
        binding_row = self.store.evaluator_binding()
        if binding_row is None or action is None:
            raise EvaluatorError(f"evaluation receipt has no binding or action for {action_id}")
        try:
            receipt = validate_receipt_linkage(
                stored["receipt"],
                stored_receipt_hash=stored["receipt_hash"],
                action=action,
                binding_data=json.loads(binding_row["binding_json"]),
                stored_binding_hash=binding_row["binding_hash"],
            )
        except Exception as exc:
            raise EvaluatorError(f"evaluation receipt integrity failure for {action_id}: {exc}") from exc
        sidecar = self.store.path.parent / "artifacts" / hashlib.sha256(action_id.encode()).hexdigest()[:24] / "evaluation-receipt.json"
        if sidecar.exists():
            try:
                sidecar_receipt = EvaluationReceipt.model_validate_json(sidecar.read_text(encoding="utf-8"))
            except Exception as exc:
                raise EvaluatorError(f"evaluation receipt sidecar is invalid for {action_id}") from exc
            if sidecar_receipt.receipt_hash != receipt.receipt_hash or sidecar_receipt != receipt:
                raise EvaluatorError(f"evaluation receipt sidecar mismatch for {action_id}")

    def _evaluation_request(
        self,
        snapshot: str,
        *,
        tier: str = "dev",
        command: list[str] | None = None,
        candidate_proof: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = {
            "snapshot": snapshot,
            "tier": tier,
            "command": command or self.spec.evaluation_command,
            "evaluation_fingerprint": evaluation_fingerprint(self.spec.evaluation_assets),
        }
        manifest = self.spec.protocol_manifests.get(tier)
        if manifest is not None:
            serialized = json.dumps(
                manifest.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            request["protocol_manifest_sha256"] = hashlib.sha256(serialized).hexdigest()
        if candidate_proof is not None:
            request["candidate_proof"] = candidate_proof
        return request

    def _protocol_manifest_context(self, tier: str) -> dict[str, str]:
        manifest = self.spec.protocol_manifests.get(tier)
        if manifest is None:
            return {}
        root = Path(self.store.path).parent / "protocol-manifests"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{tier}.json"
        serialized = json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        if path.is_symlink():
            raise EvaluatorError("frozen protocol manifest cannot be a symlink")
        if path.exists():
            if path.read_text(encoding="utf-8") != serialized:
                raise EvaluatorError("frozen protocol manifest changed after campaign initialization")
        else:
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(serialized, encoding="utf-8")
            temporary.replace(path)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return {
            "BANANLOOP_PROTOCOL_MANIFEST": str(path.resolve()),
            "BANANLOOP_PROTOCOL_MANIFEST_SHA256": digest,
        }

    def _validation_reference(self) -> EvaluationResult:
        snapshot = self.store.campaign()["champion_snapshot"]
        row = self.store.latest_evaluation(snapshot, "validation")
        if row is None:
            raise EngineError(
                f"validation reference missing for champion snapshot {snapshot}"
            )
        return EvaluationResult.model_validate_json(row["result_json"])

    def _holdout_assessment(self, result: EvaluationResult) -> dict[str, Any]:
        validate_evaluation(self.spec, result, tier="holdout")
        return {
            "snapshot": self.store.campaign()["champion_snapshot"],
            "target_met": target_met(self.spec, result, tier="holdout"),
            "result": result.model_dump(mode="json"),
        }

    def _finalize_holdout(self) -> dict[str, Any] | None:
        if self.holdout_evaluator is None:
            return None

        campaign = self.store.campaign()
        snapshot = campaign["champion_snapshot"]
        if not snapshot:
            return None

        existing = self.store.latest_evaluation(snapshot, "holdout")
        if existing is not None:
            result = EvaluationResult.model_validate_json(existing["result_json"])
            return self._holdout_assessment(result)

        self.workspace.checkout(snapshot)
        integrity_violations = verify_evaluation_assets(
            self.workspace.workspace, self.spec.evaluation_assets
        )
        if integrity_violations:
            raise EngineError(
                "evaluation assets changed before final holdout: "
                + ", ".join(integrity_violations)
            )

        request = self._evaluation_request(
            snapshot,
            tier="holdout",
            command=self.spec.holdout_command,
        )
        raw = self._action(
            action_id="final:holdout",
            experiment_id=None,
            stage="FINAL_HOLDOUT",
            kind="final_evaluator",
            request=request,
            invoke=lambda: self.holdout_evaluator.evaluate(
                self.workspace.workspace,
                timeout=(
                    self.spec.holdout_timeout_seconds
                    or self.spec.evaluator_timeout_seconds
                ),
            ).model_dump(mode="json"),
        )
        result = EvaluationResult.model_validate(raw)
        validate_evaluation(self.spec, result)
        self.store.record_evaluation(
            None,
            snapshot,
            result.model_dump(mode="json"),
            tier="holdout",
        )
        assessment = self._holdout_assessment(result)
        self.store.event(
            "FINAL_HOLDOUT_RECORDED",
            {
                "snapshot": snapshot,
                "target_met": assessment["target_met"],
            },
        )
        self.store.conn.commit()
        return assessment

    def _baseline(self) -> None:
        campaign = self.store.campaign()
        if campaign["baseline_json"]:
            baseline = EvaluationResult.model_validate_json(campaign["baseline_json"])
            primary = baseline.metrics[self.spec.primary_metric.name].value
            self._progress(
                f"baseline {self.spec.primary_metric.name}: {primary:.6g} (reused)"
            )
            return
        self.store.update_campaign(status=CampaignStatus.RUNNING.value, stage="BASELINE")
        self.workspace.ensure(self.spec.base_commit)
        integrity_violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
        if integrity_violations:
            error = "evaluation assets changed before baseline: " + ", ".join(integrity_violations)
            self.store.event("BASELINE_FAILED", {"error": error})
            self.store.conn.commit()
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(error)
        self._progress("baseline: starting full evaluator")
        request = self._evaluation_request(self.spec.base_commit)
        try:
            raw = self._action(
                action_id="baseline:evaluate",
                experiment_id=None,
                stage="BASELINE",
                kind="evaluator",
                request=request,
                invoke=lambda: self.evaluator.evaluate(
                    self.workspace.workspace,
                    timeout=self.spec.evaluator_timeout_seconds,
                ).model_dump(mode="json"),
            )
            result = EvaluationResult.model_validate(raw)
            validate_evaluation(self.spec, result)
        except (EvaluatorError, InvalidEvaluation, ValidationError, UnknownActionOutcome) as exc:
            self.store.event("BASELINE_FAILED", {"error": str(exc)})
            self.store.conn.commit()
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"baseline failed: {exc}") from exc
        snapshot = self.workspace.head()
        self.store.record_evaluation(
            None,
            snapshot,
            result.model_dump(mode="json"),
            tier="dev",
        )

        if self.validation_evaluator is not None:
            validation_request = self._evaluation_request(
                snapshot,
                tier="validation",
                command=self.spec.validation_command,
            )
            try:
                validation_raw = self._action(
                    action_id="baseline:validation",
                    experiment_id=None,
                    stage="BASELINE_VALIDATION",
                    kind="evaluator",
                    request=validation_request,
                    invoke=lambda: self.validation_evaluator.evaluate(
                        self.workspace.workspace,
                        timeout=(
                            self.spec.validation_timeout_seconds
                            or self.spec.evaluator_timeout_seconds
                        ),
                    ).model_dump(mode="json"),
                )
                validation_result = EvaluationResult.model_validate(validation_raw)
                validate_evaluation(self.spec, validation_result, tier="validation")
            except (
                EvaluatorError,
                InvalidEvaluation,
                ValidationError,
                UnknownActionOutcome,
            ) as exc:
                self.store.event("BASELINE_VALIDATION_FAILED", {"error": str(exc)})
                self.store.conn.commit()
                self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
                raise EngineError(f"baseline validation failed: {exc}") from exc
            self.store.record_evaluation(
                None,
                snapshot,
                validation_result.model_dump(mode="json"),
                tier="validation",
            )
            self.store.event("BASELINE_VALIDATION_RECORDED", {"snapshot": snapshot})

        self.store.update_campaign(
            baseline_json=result.model_dump_json(),
            baseline_snapshot=snapshot,
            champion_json=result.model_dump_json(),
            champion_snapshot=snapshot,
            stage=None,
            status=CampaignStatus.RUNNING.value,
            stop_reason=None,
        )
        self.store.event("BASELINE_RECORDED", {"snapshot": snapshot})
        self.store.conn.commit()
        primary = result.metrics[self.spec.primary_metric.name].value
        self._progress(f"baseline {self.spec.primary_metric.name}: {primary:.6g}")
        visible_metrics = [
            f"{name}={value.value:.6g}"
            for name, value in result.metrics.items()
            if name != self.spec.primary_metric.name
        ]
        if visible_metrics:
            self._progress("baseline metrics: " + ", ".join(visible_metrics[:8]))
        self._refresh_research_state()
        self._checkpoint("BASELINE")

    def _history(self) -> list[dict[str, Any]]:
        return build_history(self.spec, self.store.experiments(), limit=8)

    def _refresh_research_state(self) -> dict[str, Any]:
        remaining_work_units = None
        if self.spec.budget.max_evaluation_units is not None:
            remaining_work_units = max(
                0,
                self.spec.budget.max_evaluation_units - self.store.evaluation_work_reserved(),
            )
        state = build_research_state(
            self.spec,
            self.store.campaign(),
            self.store.experiments(),
            self.store.events(),
            remaining_work_units,
        )
        payload = state.model_dump(mode="json")
        self.store.set_research_state(payload)
        return payload

    def _task_context(self, role: str, experiment_id: int) -> dict[str, Any]:
        action_role = "analyze-dev-failures" if role == "analyze" else role
        action = self.store.action(f"exp:{experiment_id}:{action_role}")
        if action and action.get("request_json"):
            request = json.loads(action["request_json"])
            if _hash_payload(request) != action["request_hash"]:
                raise EngineError("stored agent request hash mismatch")
            # Call reservations change remaining_budget. Replays must use the
            # originally approved input, not a newly generated planning context.
            return request
        research_state = self._refresh_research_state()
        research_state = dict(research_state)
        research_state["recent_failure_capsules"] = [
            {**capsule, "evidence_id": capsule_evidence_ref(FailureCapsule.model_validate(capsule))}
            for capsule in research_state.get("recent_failure_capsules", [])
        ]
        campaign = self.store.campaign()
        return {
            "schema_version": 1,
            "role": role,
            "goal": self.spec.goal,
            "project_context": self.spec.project_context,
            "primary_metric": self.spec.primary_metric.model_dump(mode="json"),
            "guardrails": [
                g.model_dump(mode="json")
                for g in self.spec.guardrails
                if not self.spec.agent_visible_metrics or g.metric in self.spec.agent_visible_metrics
            ],
            "non_regression": [
                g.model_dump(mode="json")
                for g in self.spec.non_regression
                if not self.spec.agent_visible_metrics or g.metric in self.spec.agent_visible_metrics
            ],
            "editable_scope": self.spec.editable_scope,
            "protected_paths": self._agent_protected_paths(),
            "agent_visible_training_paths": self.spec.agent_visible_training_paths,
            "baseline": self._agent_evaluation_view(campaign["baseline_json"]),
            "champion": self._agent_evaluation_view(campaign["champion_json"]),
            "champion_snapshot": campaign["champion_snapshot"],
            "history": self._history(),
            "research_state": research_state,
            "planning_mode": research_state["planning_mode"],
            "portfolio_contract": {
                "max_candidates": 4,
                "candidate_fields": [
                    "candidate_id", "hypothesis", "evidence_refs", "suspected_failure_mode",
                    "predicted_observable", "discriminating_test", "scope_eligibility",
                    "estimated_agent_calls", "estimated_evaluator_calls", "estimated_work_units",
                    "rationale", "uncertainty", "inference=agent_inference",
                ],
                "evidence_ref_format": "exp:<DEV ordinal>, signal:<allowlisted DEV signal_key>, capsule:<stable 16-character content hash from the supplied capsule evidence_id>, or INSUFFICIENT_EVIDENCE",
                "costs": "Estimate remaining implementation/evaluation calls after this proposal action, and optional work units; do not count this proposal twice or invent USD. Core enforces actual approved budgets.",
                "legacy_fallback": "A single existing Hypothesis JSON object remains valid.",
            },
            "planning_requirements": {
                "exploit": "build on the strongest DEV-supported mechanism without duplicating prior work",
                "explore": "test a materially different mechanism family from recent failed attempts",
                "reframe": "challenge the current framing and choose a new mechanism family before plateau",
                "genealogy": (
                    "If reusing a mechanism_key, set relation to refine/retry/supersede "
                    "and cite a matching prior experiment ordinal."
                ),
                "causal_link": (
                    "When research_state.failure_signals is nonempty, choose one "
                    "signal_key as target_failure_signal and explain its causal "
                    "failure_mechanism. When experiment_evidence exists, cite at "
                    "least one prior ordinal in prior_experiment_refs and explain "
                    "in novelty_rationale how its result changes this proposal."
                ),
            },
            "experiment_id": experiment_id,
        }

    @staticmethod
    def _hypothesis_grounding_error(
        hypothesis: Hypothesis,
        task: dict[str, Any],
    ) -> str | None:
        if not hypothesis.files_or_area:
            return "hypothesis has no concrete files_or_area grounding"
        signals = (task.get("research_state") or {}).get("failure_signals") or []
        if signals:
            allowed = {item.get("signal_key") for item in signals}
            if hypothesis.target_failure_signal not in allowed:
                return "hypothesis must target one supplied DEV failure signal"
            if not hypothesis.failure_mechanism.strip():
                return "hypothesis must explain the causal failure mechanism"
            evidence = (task.get("research_state") or {}).get("experiment_evidence") or []
            if evidence:
                prior_ordinals = {item.get("ordinal") for item in evidence}
                if not prior_ordinals.intersection(hypothesis.prior_experiment_refs):
                    return "hypothesis must cite a prior DEV experiment ordinal"
                if not hypothesis.novelty_rationale.strip():
                    return "hypothesis must explain how prior DEV evidence changes the proposal"
        supplied = {
            "research_state": bool(task.get("research_state")),
            "planning_mode": bool((task.get("research_state") or {}).get("planning_mode")),
            "project_context": bool(task.get("project_context")),
        }
        text = " ".join(
            [
                hypothesis.hypothesis,
                hypothesis.rationale,
                hypothesis.expected_effect,
                hypothesis.novelty_rationale,
            ]
        ).lower()
        patterns = (
            ("research_state", r"(research_state|research state).{0,100}(missing|not provided|unavailable|absent|not supplied)"),
            ("research_state", r"(missing|not provided|unavailable|absent|not supplied).{0,100}(research_state|research state)"),
            ("planning_mode", r"(planning_mode|planning mode).{0,100}(missing|not provided|unavailable|absent|not supplied)"),
            ("planning_mode", r"(missing|not provided|unavailable|absent|not supplied).{0,100}(planning_mode|planning mode)"),
            ("project_context", r"(project_context|project context|campaign context).{0,100}(missing|not provided|unavailable|absent|not supplied|insufficient)"),
            ("project_context", r"(missing|not provided|unavailable|absent|not supplied|insufficient).{0,100}(project_context|project context|campaign context)"),
        )
        for key, pattern in patterns:
            if supplied[key] and re.search(pattern, text):
                return f"hypothesis falsely claims supplied {key} is missing"
        return None

    def _candidate_materialization_proof(
        self,
        experiment_id: int,
        *,
        require_changes: bool = True,
    ) -> dict[str, Any]:
        exp = self.store.experiment(experiment_id)
        candidate = exp.get("candidate_snapshot")
        parent = exp.get("parent_snapshot")
        if not candidate or not parent:
            raise WorkspaceError(
                f"experiment {experiment_id} is missing parent/candidate snapshot"
            )
        self.workspace.checkout(candidate)
        proof = self.workspace.snapshot_proof(parent, candidate)
        if require_changes and not proof["changed_paths"]:
            raise WorkspaceError(
                f"candidate snapshot {candidate} has no diff from parent {parent}"
            )
        return proof

    def _stage_propose(self, attempt_id: int, experiment_id: int) -> None:
        self.workspace.checkout(self.store.campaign()["champion_snapshot"])
        task = self._task_context("propose", experiment_id)
        try:
            raw = self._action(
                action_id=f"exp:{experiment_id}:propose",
                experiment_id=experiment_id,
                stage="PROPOSE",
                kind="agent",
                request=task,
                invoke=lambda: self._invoke_agent(
                    task,
                    timeout=self._agent_timeout(900),
                    apply_changes=False,
                ),
            )
            raw = self.agent.normalize_result(task, raw)
            if raw.get("status") == "NO_HYPOTHESIS":
                self.store.update_experiment(experiment_id, failure_category="NO_HYPOTHESIS", lesson="Agent proposed no further hypothesis")
                self.store.finish_attempt(attempt_id, experiment_id, status="NO_HYPOTHESIS", scientific=False, improved=False)
                self._refresh_research_state()
                self._stop(StopReason.NO_HYPOTHESIS)
                return
            portfolio = normalize_portfolio(raw)
        except (AgentError, ValidationError, ValueError, UnknownActionOutcome, WorkspaceError) as exc:
            self.store.update_experiment(experiment_id, failure_category="AGENT_PROTOCOL_ERROR", lesson=str(exc)[:1000])
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"propose failed: {exc}") from exc

        action_id = f"exp:{experiment_id}:propose"
        saved_event = next((
            event for event in self.store.events()
            if event.get("event_type") == "PORTFOLIO_DECISION"
            and json.loads(event["payload_json"]).get("action_id") == action_id
        ), None)
        if saved_event is not None:
            # The adviser response and selected decision were durably committed
            # together. Resume from that decision instead of recomputing against
            # a possibly newer research window.
            record = PortfolioRecord.model_validate(json.loads(saved_event["payload_json"]))
            portfolio = HypothesisPortfolio(candidates=record.candidates)
        else:
            current_state = self._refresh_research_state()
            record = select_candidate(
                portfolio,
                spec=self.spec,
                state=ResearchState.model_validate(current_state),
                experiments=[row for row in self.store.experiments() if row["id"] != experiment_id],
                experiment_ordinal=int(self.store.experiment(experiment_id)["ordinal"]),
                action_id=action_id,
                task=task,
                grounding_check=self._hypothesis_grounding_error,
            )
            self.store.event("PORTFOLIO_DECISION", record.model_dump(mode="json"))
            self.store.conn.commit()
            self._checkpoint("PORTFOLIO_SAVED")
        if record.selected_candidate_id is None:
            ungrounded = next((
                reason.removeprefix("UNGROUNDED_EVIDENCE: ")
                for decision in record.decisions
                for reason in decision.rejection_reasons
                if reason.startswith("UNGROUNDED_EVIDENCE: ")
            ), None)
            failure_category = "UNGROUNDED_HYPOTHESIS" if ungrounded else "NO_ADMISSIBLE_HYPOTHESIS"
            attempt_status = "UNGROUNDED" if ungrounded else "NO_ADMISSIBLE_HYPOTHESIS"
            self.store.update_experiment(
                experiment_id,
                failure_category=failure_category,
                lesson=(ungrounded or record.stop_reason)[:1000],
            )
            self.store.finish_attempt(
                attempt_id, experiment_id, status=attempt_status,
                scientific=False, improved=False,
            )
            self._refresh_research_state()
            self._stop(StopReason.NO_HYPOTHESIS)
            return
        selected = next(item for item in portfolio.candidates if item.candidate_id == record.selected_candidate_id)
        hypothesis = selected.hypothesis
        self._refresh_research_state()

        grounding_error = self._hypothesis_grounding_error(hypothesis, task)
        if grounding_error:
            self.store.update_experiment(
                experiment_id,
                hypothesis_json=hypothesis.model_dump_json(),
                failure_category="UNGROUNDED_HYPOTHESIS",
                lesson=grounding_error[:1000],
            )
            self.store.event(
                "UNGROUNDED_HYPOTHESIS_REJECTED",
                {
                    "experiment_id": experiment_id,
                    "ordinal": self.store.experiment(experiment_id)["ordinal"],
                    "reason": grounding_error[:1000],
                },
            )
            self.store.finish_attempt(
                attempt_id,
                experiment_id,
                status="UNGROUNDED",
                scientific=False,
                improved=False,
            )
            self._refresh_research_state()
            return

        duplicate = redundancy_reason(
            hypothesis,
            [row for row in self.store.experiments() if row["id"] != experiment_id],
        )
        if duplicate:
            self.store.update_experiment(
                experiment_id,
                hypothesis_json=hypothesis.model_dump_json(),
                failure_category="REDUNDANT_HYPOTHESIS",
                lesson=duplicate[:1000],
            )
            self.store.event(
                "REDUNDANT_HYPOTHESIS_REJECTED",
                {
                    "experiment_id": experiment_id,
                    "ordinal": self.store.experiment(experiment_id)["ordinal"],
                    "mechanism_key": hypothesis.mechanism_key,
                    "reason": duplicate[:1000],
                },
            )
            self.store.finish_attempt(
                attempt_id,
                experiment_id,
                status="REDUNDANT",
                scientific=False,
                improved=False,
            )
            self._refresh_research_state()
            return

        self.store.update_experiment(
            experiment_id,
            hypothesis_json=hypothesis.model_dump_json(),
            failure_category=None,
            lesson=None,
        )
        self._progress(
            f"experiment {self.store.experiment(experiment_id)['ordinal']} hypothesis: "
            f"{hypothesis.hypothesis}"
        )
        if hypothesis.files_or_area:
            self._progress("target area: " + ", ".join(hypothesis.files_or_area[:8]))
        self.store.set_stage(attempt_id, "EXECUTE")
        self._checkpoint("EXECUTE")

    def _stage_execute(self, attempt_id: int, experiment_id: int) -> None:
        campaign = self.store.campaign()
        action_id = f"exp:{experiment_id}:implement"
        existing_action = self.store.action(action_id)
        if not (existing_action and existing_action["status"] == "COMPLETED"):
            self.workspace.checkout(campaign["champion_snapshot"])
        exp = self.store.experiment(experiment_id)
        task = self._task_context("implement", experiment_id)
        task["hypothesis"] = json.loads(exp["hypothesis_json"])
        try:
            raw = self._action(
                action_id=action_id,
                experiment_id=experiment_id,
                stage="EXECUTE",
                kind="agent",
                request=task,
                invoke=lambda: self._invoke_agent(
                    task,
                    timeout=self._agent_timeout(1200),
                    apply_changes=True,
                ),
            )
            raw = self.agent.normalize_result(task, raw)
            implementation = ImplementResult.model_validate(raw)
            if implementation.blockers:
                self.store.update_experiment(
                    experiment_id,
                    failure_category="IMPLEMENTATION_BLOCKED",
                    lesson="; ".join(implementation.blockers)[:1000],
                )
                self.store.event(
                    "IMPLEMENTATION_BLOCKED",
                    {
                        "experiment_id": experiment_id,
                        "blockers": implementation.blockers[:8],
                    },
                )
                self.store.conn.commit()
                self.store.set_stage(attempt_id, "REJECT")
                self._checkpoint("REJECT")
                return

            observed_changes = self.workspace.working_tree_changed_paths()
            if not observed_changes:
                if implementation.changed_paths:
                    error = (
                        "implementer reported changed_paths but no changes reached the "
                        "supervisor workspace: " + ", ".join(implementation.changed_paths)
                    )
                    self.store.update_experiment(
                        experiment_id,
                        failure_category="CANDIDATE_NOT_MATERIALIZED",
                        lesson=error[:1000],
                    )
                    self.store.event(
                        "CANDIDATE_TRANSFER_FAILED",
                        {
                            "experiment_id": experiment_id,
                            "reported_paths": implementation.changed_paths,
                        },
                    )
                    self.store.conn.commit()
                    self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
                    raise EngineError(error)
                self.store.update_experiment(
                    experiment_id,
                    failure_category="NO_CHANGES",
                    lesson="Implementer produced no material repository changes",
                )
                self.store.event(
                    "NO_CHANGES_REJECTED",
                    {"experiment_id": experiment_id},
                )
                self.store.conn.commit()
                self.store.set_stage(attempt_id, "REJECT")
                self._checkpoint("REJECT")
                return

            self.store.event(
                "IMPLEMENTATION_TRANSFER_VERIFIED",
                {
                    "experiment_id": experiment_id,
                    "observed_paths": observed_changes,
                    "reported_paths": implementation.changed_paths,
                },
            )
            self.store.update_experiment(
                experiment_id,
                failure_category=None,
                lesson=None,
            )
            self._progress(
                f"implementation: {implementation.summary}; changed "
                f"{len(observed_changes)} file(s): " + ", ".join(observed_changes[:8])
            )
            if implementation.verification_notes:
                test_counts = re.findall(
                    r"\b\d+\s+(?:passed|failed|skipped|errors?)\b",
                    implementation.verification_notes,
                    flags=re.IGNORECASE,
                )
                if test_counts:
                    self._progress("worker-reported checks: " + ", ".join(test_counts[:6]))
        except (AgentError, ValidationError, UnknownActionOutcome, WorkspaceError) as exc:
            self.store.update_experiment(experiment_id, failure_category="AGENT_PROTOCOL_ERROR", lesson=str(exc)[:1000])
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"implement failed: {exc}") from exc
        self.store.set_stage(attempt_id, "SNAPSHOT")
        self._checkpoint("SNAPSHOT")

    def _stage_snapshot(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        snapshot = self.workspace.snapshot(f"bananloop experiment {experiment_id}")
        self.store.update_experiment(experiment_id, candidate_snapshot=snapshot)
        try:
            proof = self.workspace.snapshot_proof(exp["parent_snapshot"], snapshot)
            if not proof["changed_paths"]:
                raise WorkspaceError(
                    "snapshot persisted no candidate diff after a material implementation"
                )
        except WorkspaceError as exc:
            self.store.update_experiment(
                experiment_id,
                failure_category="CANDIDATE_NOT_MATERIALIZED",
                lesson=str(exc)[:1000],
            )
            self.store.event(
                "CANDIDATE_SNAPSHOT_FAILED",
                {
                    "experiment_id": experiment_id,
                    "parent_snapshot": exp["parent_snapshot"],
                    "candidate_snapshot": snapshot,
                    "error": str(exc)[:1000],
                },
            )
            self.store.conn.commit()
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"candidate snapshot failed: {exc}") from exc

        self.store.event(
            "CANDIDATE_SNAPSHOT_RECORDED",
            {"experiment_id": experiment_id, **proof},
        )
        self.store.conn.commit()
        self.store.set_stage(attempt_id, "VERIFY")
        self._checkpoint("VERIFY")

    def _stage_verify(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        self.workspace.checkout(exp["candidate_snapshot"])
        changed = self.workspace.changed_paths(exp["parent_snapshot"], exp["candidate_snapshot"])
        valid, violations = self.workspace.verify_scope(changed, self.spec.editable_scope, self.spec.protected_paths)
        integrity_violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
        if integrity_violations:
            self.store.update_experiment(
                experiment_id,
                failure_category="EVALUATOR_CHANGED",
                lesson="Frozen evaluation assets changed",
            )
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(
                "frozen evaluation assets changed before measurement: "
                + ", ".join(integrity_violations)
            )
        elif not valid:
            decision = {
                "accepted": False,
                "primary_value": None,
                "champion_value": None,
                "delta": None,
                "predicates": {"scope": False},
                "reasons": ["SCOPE_VIOLATION", *violations],
            }
            self.store.update_experiment(experiment_id, decision_json=json.dumps(decision), failure_category="SCOPE_VIOLATION", lesson="Candidate changed protected/out-of-scope paths")
            self.store.record_decision(experiment_id, exp["candidate_snapshot"], decision)
            self.store.set_stage(attempt_id, "REJECT")
        elif self.spec.forbidden_diff_patterns:
            leakage_violations = scan_forbidden_diff_patterns(
                self.workspace.diff_text(
                    exp["parent_snapshot"],
                    exp["candidate_snapshot"],
                ),
                self.spec.forbidden_diff_patterns,
            )
            if leakage_violations:
                decision = {
                    "accepted": False,
                    "primary_value": None,
                    "champion_value": None,
                    "delta": None,
                    "predicates": {"anti_gaming": False},
                    "reasons": ["LEAKAGE_GUARD"],
                }
                self.store.update_experiment(
                    experiment_id,
                    decision_json=json.dumps(decision),
                    failure_category="LEAKAGE_GUARD",
                    lesson="; ".join(leakage_violations)[:1000],
                )
                self.store.record_decision(
                    experiment_id,
                    exp["candidate_snapshot"],
                    decision,
                )
                self.store.event(
                    "LEAKAGE_GUARD_REJECTED",
                    {
                        "experiment_id": experiment_id,
                        "snapshot": exp["candidate_snapshot"],
                        "violations": leakage_violations,
                    },
                )
                self.store.conn.commit()
                self.store.set_stage(attempt_id, "REJECT")
            else:
                self.store.set_stage(attempt_id, "EVALUATE")
        else:
            self.store.set_stage(attempt_id, "EVALUATE")
        self._checkpoint(self.store.attempt(attempt_id)["stage"])

    def _stage_evaluate(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        try:
            candidate_proof = self._candidate_materialization_proof(experiment_id)
        except WorkspaceError as exc:
            self.store.update_experiment(
                experiment_id,
                failure_category="CANDIDATE_NOT_MATERIALIZED",
                lesson=str(exc)[:1000],
            )
            self.store.event(
                "CANDIDATE_EVALUATION_BLOCKED",
                {"experiment_id": experiment_id, "error": str(exc)[:1000]},
            )
            self.store.conn.commit()
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(
                f"candidate is not materialized in evaluator workspace: {exc}"
            ) from exc
        self.store.event(
            "CANDIDATE_EVALUATION_READY",
            {"experiment_id": experiment_id, **candidate_proof},
        )
        self.store.conn.commit()
        integrity_violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
        if integrity_violations:
            self.store.update_experiment(
                experiment_id,
                failure_category="EVALUATOR_CHANGED",
                lesson="Frozen evaluation assets changed before measurement: " + ", ".join(integrity_violations),
            )
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(
                "frozen evaluation assets changed before measurement: "
                + ", ".join(integrity_violations)
            )
        request = self._evaluation_request(
            exp["candidate_snapshot"],
            candidate_proof=candidate_proof,
        )
        try:
            raw = self._action(
                action_id=f"exp:{experiment_id}:evaluate",
                experiment_id=experiment_id,
                stage="EVALUATE",
                kind="evaluator",
                request=request,
                invoke=lambda: self._checked_candidate_evaluation(
                    self.evaluator,
                    self.spec.evaluator_timeout_seconds,
                    EvaluationResult.model_validate_json(self.store.campaign()["champion_json"]),
                ),
            )
            result = EvaluationResult.model_validate(raw)
            validate_evaluation(self.spec, result)
        except (EvaluatorError, InvalidEvaluation, ValidationError, EngineError) as exc:
            self.store.update_experiment(experiment_id, failure_category="INVALID_EVALUATION", lesson=str(exc)[:1000])
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(str(exc)) from exc
        self.store.record_evaluation(
            experiment_id,
            exp["candidate_snapshot"],
            result.model_dump(mode="json"),
            tier="dev",
        )
        self.store.update_experiment(experiment_id, evaluation_json=result.model_dump_json(), failure_category=None, lesson=None)
        self.store.set_stage(attempt_id, "DECIDE")
        self._checkpoint("DECIDE")

    def _checked_candidate_evaluation(
        self,
        evaluator: CommandEvaluator,
        timeout: int,
        reference: EvaluationResult,
        *,
        tier: str = "dev",
    ) -> dict[str, Any]:
        # Validate before completing the action receipt, so a known invalid
        # measurement can use the explicit FAILED-action retry path.
        result = evaluator.evaluate(self.workspace.workspace, timeout=timeout)
        decision = decide(self.spec, reference, result, tier=tier)
        if "PAIRED_INPUT_MISMATCH" in decision.reasons:
            raise InvalidEvaluation("PAIRED_INPUT_MISMATCH")
        return result.model_dump(mode="json")

    @staticmethod
    def _redact_failure_text(value: str) -> str:
        # Remove case-specific numeric/model/article tokens before adaptive LLM
        # analysis. Field names and the shape of the contradiction remain visible.
        value = re.sub(
            r"(?<!\w)(?=[\w.:/\\-]*\d)[\w.:/\\-]*\d[\w.:/\\-]*(?!\w)",
            "<redacted>",
            value,
        )
        return value

    def _dev_failure_analysis_task(
        self,
        experiment_id: int,
        candidate: EvaluationResult,
    ) -> dict[str, Any]:
        exp = self.store.experiment(experiment_id)
        task = self._task_context("analyze", experiment_id)
        task["hypothesis"] = (
            json.loads(exp["hypothesis_json"]) if exp.get("hypothesis_json") else None
        )
        task["dev_evaluation"] = self._agent_evaluation_view(exp.get("evaluation_json"))
        task["dev_decision"] = self._agent_decision_view(exp.get("dev_decision_json"))

        safe_signals: list[dict[str, Any]] = []
        for signal in candidate.failure_signals:
            item = signal.model_dump(mode="json")
            for field in ("symptom", "expected_behavior", "observed_behavior"):
                item[field] = self._redact_failure_text(str(item.get(field) or ""))
            item["evidence"] = [
                self._redact_failure_text(str(value))
                for value in item.get("evidence") or []
            ]
            safe_signals.append(item)
        task["dev_failure_signals"] = safe_signals
        task["dev_failure_examples"] = [
            example.model_dump(mode="json")
            for example in candidate.failure_examples
        ]
        task["analysis_requirements"] = {
            "dev_only": True,
            "generalize_not_memorize": True,
            "do_not_copy_case_ids_into_capsules": True,
            "do_not_copy_product_ids_into_capsules": True,
            "do_not_copy_gold_labels_into_capsules": True,
            "teacher_examples_are_public_dev_only": True,
            "may_use_exact_expected_answers_for_diagnosis": True,
            "do_not_copy_case_specific_answers_into_capsules": True,
            "prefer_invariants_and_provenance": True,
        }
        return task

    def _sanitize_failure_analysis(
        self,
        analysis: FailureAnalysisResult,
        candidate: EvaluationResult,
    ) -> FailureAnalysisResult:
        queries = [
            example.query.strip()
            for example in candidate.failure_examples
            if example.query.strip()
        ]
        # Analyzers tend to paraphrase, so the exact query may survive only as
        # one of its tokens (e.g. an article number like VT.217.N.05). Those
        # digit-bearing tokens are still case-specific, so they must be
        # scrubbed as case-specific data, not just as generic numbers.
        query_tokens: list[str] = []
        for query in queries:
            for token in re.findall(r"[\w.:/\\-]+", query):
                if any(char.isdigit() for char in token) and token not in query_tokens:
                    query_tokens.append(token)
        query_tokens.sort(key=len, reverse=True)
        payload = analysis.model_dump(mode="json")
        for capsule in payload.get("capsules") or []:
            for field in (
                "title",
                "failure_class",
                "stage",
                "observation",
                "likely_mechanism",
                "general_rule",
                "suggested_regression",
            ):
                value = str(capsule.get(field) or "")
                for query in queries:
                    value = value.replace(query, "<case-specific-query>")
                for token in query_tokens:
                    if token in value:
                        value = value.replace(token, "<case-specific-query>")
                capsule[field] = self._redact_failure_text(value)
        return FailureAnalysisResult.model_validate(payload)

    def _route_after_dev_analysis(self, attempt_id: int, decision: dict[str, Any]) -> None:
        if decision.get("accepted") and self.validation_evaluator is not None:
            self.store.set_stage(attempt_id, "VALIDATE")
        else:
            self.store.set_stage(
                attempt_id,
                "PROMOTE" if decision.get("accepted") else "REJECT",
            )

    def _stage_decide(self, attempt_id: int, experiment_id: int) -> None:
        campaign = self.store.campaign()
        exp = self.store.experiment(experiment_id)
        champion = EvaluationResult.model_validate_json(campaign["champion_json"])
        candidate = EvaluationResult.model_validate_json(exp["evaluation_json"])
        try:
            decision = decide(self.spec, champion, candidate)
            if "PAIRED_INPUT_MISMATCH" in decision.reasons:
                raise InvalidEvaluation("PAIRED_INPUT_MISMATCH")
        except (InvalidEvaluation, ValidationError) as exc:
            self.store.update_experiment(experiment_id, failure_category="INVALID_EVALUATION", lesson=str(exc)[:1000])
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(str(exc)) from exc
        self.store.update_experiment(experiment_id, failure_category=None, lesson=None)
        self.store.update_experiment(
            experiment_id,
            dev_decision_json=decision.model_dump_json(),
            decision_json=decision.model_dump_json(),
        )
        self.store.record_decision(
            experiment_id,
            exp["candidate_snapshot"],
            decision.model_dump(mode="json"),
        )
        verdict = "ACCEPT" if decision.accepted else "REJECT"
        self._progress(
            f"{self.spec.primary_metric.name}: {decision.champion_value:.6g} "
            f"-> {decision.primary_value:.6g} "
            f"({decision.delta:+.6g}); {verdict}"
        )
        if decision.reasons:
            self._progress("decision reasons: " + ", ".join(decision.reasons[:8]))
        changed_metrics = []
        for name, value in candidate.metrics.items():
            previous = champion.metrics.get(name)
            if previous is not None:
                changed_metrics.append(
                    f"{name} {previous.value:.6g} -> {value.value:.6g}"
                )
        if changed_metrics:
            self._progress("metrics: " + "; ".join(changed_metrics[:8]))
        if candidate.failure_signals:
            # Failure analysis is DEV-only and optional. Never consume validation
            # or holdout evidence for adaptive research memory.
            stop_reason = pre_action_stop(
                self.store.campaign(),
                self.spec.budget,
                "agent",
            )
            if stop_reason is None:
                self.store.set_stage(attempt_id, "ANALYZE")
            else:
                self.store.event(
                    "DEV_FAILURE_ANALYSIS_SKIPPED",
                    {
                        "experiment_id": experiment_id,
                        "reason": stop_reason.value,
                    },
                )
                self.store.conn.commit()
                self._route_after_dev_analysis(
                    attempt_id,
                    decision.model_dump(mode="json"),
                )
        else:
            self._route_after_dev_analysis(
                attempt_id,
                decision.model_dump(mode="json"),
            )
        self._checkpoint(self.store.attempt(attempt_id)["stage"])

    def _stage_analyze(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        candidate = EvaluationResult.model_validate_json(exp["evaluation_json"])
        decision = json.loads(exp["dev_decision_json"])
        if not candidate.failure_signals:
            self._route_after_dev_analysis(attempt_id, decision)
            return

        self.workspace.checkout(exp["candidate_snapshot"])
        task = self._dev_failure_analysis_task(experiment_id, candidate)
        try:
            raw = self._action(
                action_id=f"exp:{experiment_id}:analyze-dev-failures",
                experiment_id=experiment_id,
                stage="ANALYZE",
                kind="agent",
                request=task,
                invoke=lambda: self._invoke_agent(
                    task,
                    timeout=self._agent_timeout(900),
                    apply_changes=False,
                ),
            )
            raw = self.agent.normalize_result(task, raw)
            analysis = FailureAnalysisResult.model_validate(raw)
            analysis = self._sanitize_failure_analysis(analysis, candidate)
            self.store.update_experiment(
                experiment_id,
                failure_analysis_json=analysis.model_dump_json(),
            )
            self.store.event(
                "DEV_FAILURE_ANALYSIS_RECORDED",
                {
                    "experiment_id": experiment_id,
                    "capsule_count": len(analysis.capsules),
                    "failure_classes": sorted(
                        {capsule.failure_class for capsule in analysis.capsules}
                    ),
                },
            )
            self.store.conn.commit()
        except UnknownActionOutcome as exc:
            raise EngineError(str(exc)) from exc
        except (AgentError, ValidationError, WorkspaceError) as exc:
            # Analysis improves research intelligence but is not part of the
            # scientific acceptance gate. A bad analysis must not invalidate a
            # valid DEV measurement.
            self.store.update_experiment(
                experiment_id,
                failure_analysis_json=json.dumps({"capsules": []}),
            )
            self.store.event(
                "DEV_FAILURE_ANALYSIS_FAILED",
                {
                    "experiment_id": experiment_id,
                    "error": str(exc)[:1000],
                },
            )
            self.store.conn.commit()

        self._refresh_research_state()
        self._route_after_dev_analysis(attempt_id, decision)
        self._checkpoint(self.store.attempt(attempt_id)["stage"])

    def _stage_validate(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        try:
            candidate_proof = self._candidate_materialization_proof(experiment_id)
        except WorkspaceError as exc:
            self.store.update_experiment(
                experiment_id,
                failure_category="CANDIDATE_NOT_MATERIALIZED",
                lesson=str(exc)[:1000],
            )
            self.store.event(
                "CANDIDATE_VALIDATION_BLOCKED",
                {"experiment_id": experiment_id, "error": str(exc)[:1000]},
            )
            self.store.conn.commit()
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(
                f"candidate is not materialized in validation workspace: {exc}"
            ) from exc
        request = self._evaluation_request(
            exp["candidate_snapshot"],
            tier="validation",
            command=self.spec.validation_command,
            candidate_proof=candidate_proof,
        )
        try:
            raw = self._action(
                action_id=f"exp:{experiment_id}:validation",
                experiment_id=experiment_id,
                stage="VALIDATE",
                kind="evaluator",
                request=request,
                invoke=lambda: self._checked_candidate_evaluation(
                    self.validation_evaluator,
                    self.spec.validation_timeout_seconds or self.spec.evaluator_timeout_seconds,
                    self._validation_reference(),
                    tier="validation",
                ),
            )
            result = EvaluationResult.model_validate(raw)
            validate_evaluation(self.spec, result, tier="validation")
            reference = self._validation_reference()
            validation_decision = decide(self.spec, reference, result, tier="validation")
            if "PAIRED_INPUT_MISMATCH" in validation_decision.reasons:
                raise InvalidEvaluation("PAIRED_INPUT_MISMATCH")
        except (EvaluatorError, InvalidEvaluation, ValidationError, EngineError) as exc:
            self.store.update_experiment(
                experiment_id,
                decision_json=None,
                failure_category="INVALID_VALIDATION",
                lesson=str(exc)[:1000],
            )
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(str(exc)) from exc

        self.store.update_experiment(experiment_id, failure_category=None, lesson=None)
        self.store.record_evaluation(
            experiment_id,
            exp["candidate_snapshot"],
            result.model_dump(mode="json"),
            tier="validation",
        )

        combined = json.loads(exp["dev_decision_json"] or exp["decision_json"])
        combined["validation"] = validation_decision.model_dump(mode="json")
        combined.setdefault("predicates", {})["validation"] = validation_decision.accepted
        if not validation_decision.accepted:
            combined["accepted"] = False
            combined["reasons"] = [
                reason
                for reason in combined.get("reasons", [])
                if reason != "ACCEPTED"
            ]
            combined.setdefault("reasons", []).append("VALIDATION_REJECTED")
            combined["reasons"].extend(
                f"VALIDATION:{reason}"
                for reason in validation_decision.reasons
                if reason != "ACCEPTED"
            )
            self.store.update_experiment(
                experiment_id,
                decision_json=json.dumps(combined),
                failure_category="VALIDATION_REJECTED",
                lesson="Dev improvement did not reproduce on validation tier",
            )
            self.store.record_decision(
                experiment_id,
                exp["candidate_snapshot"],
                combined,
            )
            self.store.set_stage(attempt_id, "REJECT")
        else:
            self.store.update_experiment(
                experiment_id,
                decision_json=json.dumps(combined),
            )
            self.store.record_decision(
                experiment_id,
                exp["candidate_snapshot"],
                combined,
            )
            self.store.set_stage(attempt_id, "PROMOTE")
        self._checkpoint(self.store.attempt(attempt_id)["stage"])

    def _stage_promote(self, attempt_id: int, experiment_id: int) -> None:
        self.store.promote_attempt(attempt_id, experiment_id)
        self._checkpoint("RECORD")

    def _stage_reject(self, attempt_id: int, experiment_id: int) -> None:
        self.workspace.checkout(self.store.campaign()["champion_snapshot"])
        self.store.set_stage(attempt_id, "RECORD")
        self._checkpoint("RECORD")

    def _stage_record(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        accepted = False
        if exp["decision_json"]:
            accepted = bool(json.loads(exp["decision_json"]).get("accepted"))
        status = "ACCEPTED" if accepted else "REJECTED"
        self._progress(f"experiment {self.store.experiment(experiment_id)['ordinal']} result: {status}")
        bucket = attempt_bucket({**exp, "status": status, "failure_category": None if accepted else exp["failure_category"]})
        scientific = bucket == "scientific_trials"
        if not accepted:
            status = {
                "no_changes": "NO_CHANGES",
                "implementation_blocked": "IMPLEMENTATION_BLOCKED",
                "pre_evaluation_rejected": "PRE_EVALUATION_REJECTED",
                "infra_failures": "INFRA_FAILED",
            }.get(bucket, status)
        if accepted:
            failure = None
            lesson = "Promoted as new champion"
        else:
            failure = exp["failure_category"]
            if not failure:
                decision = json.loads(exp["decision_json"]) if exp["decision_json"] else {}
                failure = (decision.get("reasons") or ["REJECTED"])[0]
            lesson = exp["lesson"] or f"Rejected: {failure}"
        self.store.update_experiment(experiment_id, failure_category=failure, lesson=lesson)
        self.store.finish_attempt(
            attempt_id,
            experiment_id,
            status=status,
            scientific=scientific,
            improved=accepted,
            dev_progress=(
                scientific
                and classify_dev_outcome(self.spec, {**exp, "status": status})
                in {"DEV_IMPROVEMENT", "DEV_PARTIAL_IMPROVEMENT"}
            ),
        )
        self._refresh_research_state()

    def _post_iteration_stop(self) -> bool:
        campaign = self.store.campaign()
        if campaign["active_attempt_id"] is not None:
            return False
        champion = EvaluationResult.model_validate_json(campaign["champion_json"])
        goal_met = target_met(self.spec, champion)
        if goal_met and self.validation_evaluator is not None:
            goal_met = target_met(
                self.spec,
                self._validation_reference(),
                tier="validation",
            )
        if goal_met:
            self._stop(StopReason.GOAL_MET, CampaignStatus.GOAL_MET)
            return True
        if campaign["non_improve_streak"] >= self.spec.budget.plateau_experiments:
            state = ResearchState.model_validate(self._refresh_research_state())
            last_progress = max(
                (int(row["ordinal"]) for row in self.store.experiments()
                 if attempt_bucket(row) == "scientific_trials"
                 and classify_dev_outcome(self.spec, row)
                 in {"DEV_IMPROVEMENT", "DEV_PARTIAL_IMPROVEMENT"}),
                default=0,
            )
            experiments_by_ordinal = {
                int(row["ordinal"]): row for row in self.store.experiments()
            }
            reframe_used = any(
                item.strategy == "reframe" and item.selected_candidate_id is not None
                and item.experiment_ordinal > last_progress
                and item.experiment_ordinal in experiments_by_ordinal
                and attempt_bucket(experiments_by_ordinal[item.experiment_ordinal]) == "scientific_trials"
                for item in state.portfolios
            )
            if not reframe_used:
                budget_reason = pre_action_stop(campaign, self.spec.budget, "agent")
                evaluator_fits = self._new_attempt_fits_evaluator_budget()
                if budget_reason is None and evaluator_fits:
                    self.store.event("PLATEAU_REFRAME_ALLOWED", {
                        "non_improve_streak": campaign["non_improve_streak"],
                        "trigger_experiment_ordinal": max((int(row["ordinal"]) for row in self.store.experiments()), default=0),
                        "remaining_agent_calls": max(0, self.spec.budget.max_agent_calls - campaign["agent_calls"]),
                    })
                    self.store.conn.commit()
                    return False
                reason = str(budget_reason or StopReason.BUDGET_EXHAUSTED)
                self.store.event("PLATEAU_REFRAME_BLOCKED", {
                    "reason": f"Plateau reached; no budget remains for one bounded reframe: {reason}"
                })
                self.store.conn.commit()
                self._refresh_research_state()
            self._stop(StopReason.PLATEAU)
            return True
        reason = pre_action_stop(campaign, self.spec.budget, "agent")
        if reason and campaign["active_attempt_id"] is None:
            self._stop(reason)
            return True
        return False

    def run(
        self,
        *,
        resume: bool = False,
        retry_running_actions: bool = False,
        retry_failed_actions: bool = False,
        agent_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        try:
            return self._run(
                resume=resume,
                retry_running_actions=retry_running_actions,
                retry_failed_actions=retry_failed_actions,
                agent_timeout_seconds=agent_timeout_seconds,
            )
        except CampaignHalted:
            return self.store.campaign()
        except BindingRuntimeMismatch as exc:
            self.store.event("EVALUATOR_BINDING_MISMATCH", {"error": str(exc)[:500]})
            self.store.conn.commit()
            return self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
        except ProcessCancelled as exc:
            return self._stop(StopReason(exc.reason))

    def _run(
        self,
        *,
        resume: bool,
        retry_running_actions: bool,
        retry_failed_actions: bool,
        agent_timeout_seconds: int | None,
    ) -> dict[str, Any]:
        self._retry_running_actions = retry_running_actions
        self._retry_failed_actions = retry_failed_actions
        self._agent_timeout_seconds = agent_timeout_seconds
        campaign = self.store.campaign()
        if campaign["spec_hash"] != self.spec.approved_hash():
            raise EngineError("approved campaign spec hash mismatch")
        if campaign["status"] in {CampaignStatus.GOAL_MET.value, CampaignStatus.STOPPED.value}:
            return campaign
        control_reason = self._control_reason()
        if control_reason:
            return self._stop(StopReason(control_reason))
        if campaign["stage"] == "FINAL_HOLDOUT":
            return self._stop(StopReason(campaign["stop_reason"]))
        if resume and campaign["status"] in {CampaignStatus.PAUSED_INFRA.value, CampaignStatus.PAUSED_NEEDS_INPUT.value}:
            self.store.update_campaign(status=CampaignStatus.RUNNING.value, stop_reason=None)
        launch_roles = {}
        for role in ("propose", "implement", "analyze"):
            config = self.spec.agent.for_role(role)
            probe = self._make_agent_adapter(config).probe()
            if not probe.get("available"):
                self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
                raise EngineError(f"agent unavailable for {role}: {probe}")
            launch_roles[role] = {
                "kind": config.kind,
                "model": config.model or "external-cli-default",
                "provider": config.provider,
                "reasoning_effort": config.reasoning_effort,
                "cli_version": probe.get("version", "unknown"),
            }
        self.store.event("AGENT_LAUNCH_PROFILE", {"roles": launch_roles})
        self.store.conn.commit()
        self._ensure_evaluator_binding(verify_runtime=False)
        self.workspace.ensure(self.spec.base_commit)
        self._baseline()
        if self._post_iteration_stop():
            return self.store.campaign()

        while True:
            campaign = self.store.campaign()
            control_reason = self._control_reason()
            if control_reason:
                return self._stop(StopReason(control_reason))
            if campaign["status"] not in {CampaignStatus.RUNNING.value, CampaignStatus.READY.value}:
                return campaign
            if campaign["active_attempt_id"] is None:
                reason = pre_action_stop(campaign, self.spec.budget, "agent")
                if reason:
                    return self._stop(reason)
                if not self._new_attempt_fits_evaluator_budget():
                    return self._stop(StopReason.BUDGET_EXHAUSTED)
                self.workspace.checkout(campaign["champion_snapshot"])
                self.store.create_attempt(campaign["champion_snapshot"])
                campaign = self.store.campaign()
            attempt_id = int(campaign["active_attempt_id"])
            experiment_id = int(campaign["active_experiment_id"])
            stage = self.store.attempt(attempt_id)["stage"]
            ordinal = self.store.experiment(experiment_id)["ordinal"]
            self._progress(f"experiment {ordinal}: {stage}")
            if stage == "PROPOSE":
                self._stage_propose(attempt_id, experiment_id)
            elif stage == "EXECUTE":
                self._stage_execute(attempt_id, experiment_id)
            elif stage == "SNAPSHOT":
                self._stage_snapshot(attempt_id, experiment_id)
            elif stage == "VERIFY":
                self._stage_verify(attempt_id, experiment_id)
            elif stage == "EVALUATE":
                self._stage_evaluate(attempt_id, experiment_id)
            elif stage == "DECIDE":
                self._stage_decide(attempt_id, experiment_id)
            elif stage == "ANALYZE":
                self._stage_analyze(attempt_id, experiment_id)
            elif stage == "VALIDATE":
                self._stage_validate(attempt_id, experiment_id)
            elif stage == "PROMOTE":
                self._stage_promote(attempt_id, experiment_id)
            elif stage == "REJECT":
                self._stage_reject(attempt_id, experiment_id)
            elif stage == "RECORD":
                self._stage_record(attempt_id, experiment_id)
                if self._post_iteration_stop():
                    return self.store.campaign()
            else:
                raise EngineError(f"unknown stage {stage}")
