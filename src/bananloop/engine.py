from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .agents import AgentAdapter, AgentError, CodexAdapter, CommandAgentAdapter, PiAdapter
from .budget import pre_action_stop
from .contracts import (
    CampaignSpec,
    CampaignStatus,
    EvaluationResult,
    FailureAnalysisResult,
    Hypothesis,
    ImplementResult,
    StopReason,
)
from .evaluators import CommandEvaluator, EvaluatorError
from .integrity import evaluation_fingerprint, verify_evaluation_assets
from .leakage import scan_forbidden_diff_patterns
from .policy import InvalidEvaluation, decide, target_met, validate_evaluation
from .research import (
    agent_decision_view,
    agent_evaluation_view,
    build_history,
    build_research_state,
    redundancy_reason,
)
from .store import CampaignStore
from .workspace import WorkspaceError, WorkspaceManager


class ControlledInterruption(RuntimeError):
    pass


class EngineError(RuntimeError):
    pass


class UnknownActionOutcome(EngineError):
    pass


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
        self.spec = store.spec()
        self.evaluator = CommandEvaluator(self.spec.evaluation_command)
        self.validation_evaluator = (
            CommandEvaluator(self.spec.validation_command)
            if self.spec.validation_command
            else None
        )
        self.holdout_evaluator = (
            CommandEvaluator(self.spec.holdout_command)
            if self.spec.holdout_command
            else None
        )
        self.agent: AgentAdapter
        if self.spec.agent.kind == "codex":
            self.agent = CodexAdapter(self.spec.agent.model, self.spec.agent.extra_args)
        elif self.spec.agent.kind == "pi":
            self.agent = PiAdapter(self.spec.agent.model, self.spec.agent.extra_args)
        else:
            self.agent = CommandAgentAdapter(self.spec.agent.command)

    def _progress(self, message: str) -> None:
        if self._progress_callback:
            self._progress_callback(message)

    def _agent_timeout(self, default: int) -> int:
        return self._agent_timeout_seconds or default

    def _agent_hidden_paths(self) -> list[str]:
        frozen = [asset.path for asset in self.spec.evaluation_assets]
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
            result = self.agent.execute(task, agent_workspace, timeout=timeout)
            if apply_changes:
                self.workspace.apply_agent_changes(agent_workspace, hidden)
            return result
        finally:
            self.workspace.cleanup_agent_workspace()

    def _checkpoint(self, stage: str) -> None:
        if os.getenv("BANANLOOP_TEST_STOP_AFTER_STAGE") == stage:
            raise ControlledInterruption(f"forced interruption after durable stage {stage}")

    def _stop(self, reason: StopReason, status: CampaignStatus = CampaignStatus.STOPPED) -> dict[str, Any]:
        finalizable = reason not in {
            StopReason.PAUSED_INFRA,
            StopReason.PAUSED_NEEDS_INPUT,
            StopReason.USER_STOPPED,
        }
        final_holdout: dict[str, Any] | None = None
        if finalizable and self.holdout_evaluator is not None:
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

        if reason == StopReason.GOAL_MET:
            status = CampaignStatus.GOAL_MET
        if reason == StopReason.PAUSED_INFRA:
            status = CampaignStatus.PAUSED_INFRA
        if reason == StopReason.PAUSED_NEEDS_INPUT:
            status = CampaignStatus.PAUSED_NEEDS_INPUT
        self.store.update_campaign(status=status.value, stop_reason=reason.value, stage=None)
        event_payload: dict[str, Any] = {"reason": reason.value, "status": status.value}
        if final_holdout is not None:
            event_payload["final_holdout_target_met"] = final_holdout["target_met"]
            event_payload["final_holdout_snapshot"] = final_holdout["snapshot"]
        self.store.event("CAMPAIGN_STOPPED", event_payload)
        self.store.conn.commit()
        return self.store.campaign()

    def _budget_or_stop(self, kind: str) -> bool:
        if kind == "final_evaluator":
            return False
        reason = pre_action_stop(self.store.campaign(), self.spec.budget, kind)
        if reason:
            self._stop(reason)
            return True
        return False

    def _new_attempt_fits_evaluator_budget(self) -> bool:
        campaign = self.store.campaign()
        required = 1  # dev evaluation
        if self.validation_evaluator is not None:
            required += 1  # worst case: dev ACCEPT also needs validation
        if self.holdout_evaluator is not None:
            required += 1  # reserved final-only holdout slot
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
        existing = self.store.action(action_id)
        request_hash = _hash_payload(request)
        if existing:
            if existing["request_hash"] != request_hash:
                raise EngineError(f"action input mismatch for {action_id}")
            if existing["status"] == "COMPLETED":
                self._progress(f"{action_id}: reuse completed result")
                return json.loads(existing["response_json"])
            if existing["status"] == "CANCELLED":
                if self._budget_or_stop(kind):
                    raise EngineError("budget exhausted before action retry")
                self._progress(f"{action_id}: retry cancelled action")
                self.store.restart_action(action_id)
            elif existing["status"] == "RUNNING":
                if not self._retry_running_actions:
                    raise UnknownActionOutcome(
                        f"action {action_id} was running when process stopped; "
                        "resume with --retry-running-action only if you know the previous local process is gone"
                    )
                if self._budget_or_stop(kind):
                    raise EngineError("budget exhausted before action retry")
                self._progress(f"{action_id}: explicitly retry orphaned RUNNING action")
                self.store.restart_action(action_id)
            elif existing["status"] == "FAILED":
                if not self._retry_failed_actions:
                    raise EngineError(
                        f"action {action_id} previously failed: {existing['error']}; "
                        "resume with --retry-failed-action to retry it"
                    )
                if self._budget_or_stop(kind):
                    raise EngineError("budget exhausted before failed action retry")
                self._progress(f"{action_id}: explicitly retry FAILED action")
                self.store.restart_action(action_id)
            else:
                raise EngineError(
                    f"action {action_id} has unsupported prior status {existing['status']}"
                )
        else:
            if self._budget_or_stop(kind):
                raise EngineError("budget exhausted before action")
            self.store.start_action(action_id, experiment_id, stage, kind, request_hash)
        self._progress(f"{action_id}: invoke {kind}")
        try:
            response = invoke()
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

    def _evaluation_request(
        self,
        snapshot: str,
        *,
        tier: str = "dev",
        command: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "snapshot": snapshot,
            "tier": tier,
            "command": command or self.spec.evaluation_command,
            "evaluation_fingerprint": evaluation_fingerprint(self.spec.evaluation_assets),
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
        validate_evaluation(self.spec, result)
        return {
            "snapshot": self.store.campaign()["champion_snapshot"],
            "target_met": target_met(self.spec, result),
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

        if campaign["evaluator_calls"] >= self.spec.budget.max_evaluator_calls:
            raise EngineError(
                "final holdout could not run: evaluator-call budget has no reserved slot"
            )

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
                validate_evaluation(self.spec, validation_result)
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
        self._refresh_research_state()
        self._checkpoint("BASELINE")

    def _history(self) -> list[dict[str, Any]]:
        return build_history(self.spec, self.store.experiments(), limit=8)

    def _refresh_research_state(self) -> dict[str, Any]:
        state = build_research_state(
            self.spec,
            self.store.campaign(),
            self.store.experiments(),
        )
        payload = state.model_dump(mode="json")
        self.store.set_research_state(payload)
        return payload

    def _task_context(self, role: str, experiment_id: int) -> dict[str, Any]:
        research_state = self._refresh_research_state()
        campaign = self.store.campaign()
        return {
            "schema_version": 1,
            "role": role,
            "goal": self.spec.goal,
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
            "baseline": self._agent_evaluation_view(campaign["baseline_json"]),
            "champion": self._agent_evaluation_view(campaign["champion_json"]),
            "champion_snapshot": campaign["champion_snapshot"],
            "history": self._history(),
            "research_state": research_state,
            "planning_mode": research_state["planning_mode"],
            "planning_requirements": {
                "exploit": "build on the strongest DEV-supported mechanism without duplicating prior work",
                "explore": "test a materially different mechanism family from recent failed attempts",
                "reframe": "challenge the current framing and choose a new mechanism family before plateau",
                "genealogy": (
                    "If reusing a mechanism_key, set relation to refine/retry/supersede "
                    "and cite a matching prior experiment ordinal."
                ),
            },
            "experiment_id": experiment_id,
        }

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
            hypothesis = Hypothesis.model_validate(raw)
        except (AgentError, ValidationError, UnknownActionOutcome, WorkspaceError) as exc:
            self.store.update_experiment(experiment_id, failure_category="AGENT_PROTOCOL_ERROR", lesson=str(exc)[:1000])
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"propose failed: {exc}") from exc

        duplicate = redundancy_reason(hypothesis, self.store.experiments())
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
            ImplementResult.model_validate(raw)
            self.store.update_experiment(
                experiment_id,
                failure_category=None,
                lesson=None,
            )
        except (AgentError, ValidationError, UnknownActionOutcome, WorkspaceError) as exc:
            self.store.update_experiment(experiment_id, failure_category="AGENT_PROTOCOL_ERROR", lesson=str(exc)[:1000])
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"implement failed: {exc}") from exc
        self.store.set_stage(attempt_id, "SNAPSHOT")
        self._checkpoint("SNAPSHOT")

    def _stage_snapshot(self, attempt_id: int, experiment_id: int) -> None:
        snapshot = self.workspace.snapshot(f"bananloop experiment {experiment_id}")
        self.store.update_experiment(experiment_id, candidate_snapshot=snapshot)
        self.store.set_stage(attempt_id, "VERIFY")
        self._checkpoint("VERIFY")

    def _stage_verify(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        self.workspace.checkout(exp["candidate_snapshot"])
        changed = self.workspace.changed_paths(exp["parent_snapshot"], exp["candidate_snapshot"])
        valid, violations = self.workspace.verify_scope(changed, self.spec.editable_scope, self.spec.protected_paths)
        integrity_violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
        if integrity_violations:
            decision = {
                "accepted": False,
                "primary_value": None,
                "champion_value": None,
                "delta": None,
                "predicates": {"evaluation_integrity": False},
                "reasons": ["EVALUATOR_CHANGED", *integrity_violations],
            }
            self.store.update_experiment(
                experiment_id,
                decision_json=json.dumps(decision),
                failure_category="EVALUATOR_CHANGED",
                lesson="Frozen evaluation assets changed",
            )
            self.store.record_decision(experiment_id, exp["candidate_snapshot"], decision)
            self.store.set_stage(attempt_id, "REJECT")
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
        self.workspace.checkout(exp["candidate_snapshot"])
        integrity_violations = verify_evaluation_assets(self.workspace.workspace, self.spec.evaluation_assets)
        if integrity_violations:
            self.store.update_experiment(
                experiment_id,
                failure_category="EVALUATOR_CHANGED",
                lesson="Frozen evaluation assets changed before measurement: " + ", ".join(integrity_violations),
            )
            self.store.set_stage(attempt_id, "REJECT")
            return
        request = self._evaluation_request(exp["candidate_snapshot"])
        try:
            raw = self._action(
                action_id=f"exp:{experiment_id}:evaluate",
                experiment_id=experiment_id,
                stage="EVALUATE",
                kind="evaluator",
                request=request,
                invoke=lambda: self.evaluator.evaluate(
                    self.workspace.workspace,
                    timeout=self.spec.evaluator_timeout_seconds,
                ).model_dump(mode="json"),
            )
            result = EvaluationResult.model_validate(raw)
            validate_evaluation(self.spec, result)
        except (EvaluatorError, InvalidEvaluation, ValidationError) as exc:
            self.store.update_experiment(experiment_id, failure_category="INVALID_EVALUATION", lesson=str(exc)[:1000])
            self.store.set_stage(attempt_id, "REJECT")
            return
        except UnknownActionOutcome as exc:
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(str(exc)) from exc
        self.store.record_evaluation(
            experiment_id,
            exp["candidate_snapshot"],
            result.model_dump(mode="json"),
            tier="dev",
        )
        self.store.update_experiment(experiment_id, evaluation_json=result.model_dump_json())
        self.store.set_stage(attempt_id, "DECIDE")
        self._checkpoint("DECIDE")

    @staticmethod
    def _redact_failure_text(value: str) -> str:
        # Remove case-specific numeric/model/article tokens before adaptive LLM
        # analysis. Field names and the shape of the contradiction remain visible.
        value = re.sub(
            r"(?<!\\w)(?=[\\w.:/\\\\-]*\\d)[\\w.:/\\\\-]*\\d[\\w.:/\\\\-]*(?!\\w)",
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
        task["analysis_requirements"] = {
            "dev_only": True,
            "generalize_not_memorize": True,
            "forbid_case_ids": True,
            "forbid_product_ids": True,
            "forbid_gold_labels": True,
            "forbid_exact_expected_answers": True,
            "prefer_invariants_and_provenance": True,
        }
        return task

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
        decision = decide(self.spec, champion, candidate)
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
        self.workspace.checkout(exp["candidate_snapshot"])
        request = self._evaluation_request(
            exp["candidate_snapshot"],
            tier="validation",
            command=self.spec.validation_command,
        )
        try:
            raw = self._action(
                action_id=f"exp:{experiment_id}:validation",
                experiment_id=experiment_id,
                stage="VALIDATE",
                kind="evaluator",
                request=request,
                invoke=lambda: self.validation_evaluator.evaluate(
                    self.workspace.workspace,
                    timeout=(
                        self.spec.validation_timeout_seconds
                        or self.spec.evaluator_timeout_seconds
                    ),
                ).model_dump(mode="json"),
            )
            result = EvaluationResult.model_validate(raw)
            validate_evaluation(self.spec, result)
            reference = self._validation_reference()
            validation_decision = decide(self.spec, reference, result)
        except (EvaluatorError, InvalidEvaluation, ValidationError) as exc:
            combined = json.loads(exp["decision_json"])
            combined["accepted"] = False
            combined["reasons"] = [
                reason
                for reason in combined.get("reasons", [])
                if reason != "ACCEPTED"
            ]
            combined.setdefault("predicates", {})["validation"] = False
            combined.setdefault("reasons", []).append("INVALID_VALIDATION")
            combined["validation"] = {
                "accepted": False,
                "reasons": ["INVALID_VALIDATION"],
            }
            self.store.update_experiment(
                experiment_id,
                decision_json=json.dumps(combined),
                failure_category="INVALID_VALIDATION",
                lesson=str(exc)[:1000],
            )
            self.store.record_decision(
                experiment_id,
                exp["candidate_snapshot"],
                combined,
            )
            self.store.set_stage(attempt_id, "REJECT")
            return
        except UnknownActionOutcome as exc:
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(str(exc)) from exc

        self.store.record_evaluation(
            experiment_id,
            exp["candidate_snapshot"],
            result.model_dump(mode="json"),
            tier="validation",
        )

        combined = json.loads(exp["decision_json"])
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
        exp = self.store.experiment(experiment_id)
        self.store.update_campaign(champion_snapshot=exp["candidate_snapshot"], champion_json=exp["evaluation_json"])
        self.store.event("CHAMPION_PROMOTED", {"experiment_id": experiment_id, "snapshot": exp["candidate_snapshot"]})
        self.store.conn.commit()
        self.store.set_stage(attempt_id, "RECORD")
        self._checkpoint("RECORD")

    def _stage_reject(self, attempt_id: int, experiment_id: int) -> None:
        self.workspace.checkout(self.store.campaign()["champion_snapshot"])
        self.store.set_stage(attempt_id, "RECORD")
        self._checkpoint("RECORD")

    def _stage_record(self, attempt_id: int, experiment_id: int) -> None:
        exp = self.store.experiment(experiment_id)
        scientific = bool(exp["evaluation_json"])
        accepted = False
        if exp["decision_json"]:
            accepted = bool(json.loads(exp["decision_json"]).get("accepted"))
        status = "ACCEPTED" if accepted else "REJECTED"
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
        )
        self._refresh_research_state()

    def _post_iteration_stop(self) -> bool:
        campaign = self.store.campaign()
        champion = EvaluationResult.model_validate_json(campaign["champion_json"])
        goal_met = target_met(self.spec, champion)
        if goal_met and self.validation_evaluator is not None:
            goal_met = target_met(self.spec, self._validation_reference())
        if goal_met:
            self._stop(StopReason.GOAL_MET, CampaignStatus.GOAL_MET)
            return True
        if campaign["non_improve_streak"] >= self.spec.budget.plateau_experiments:
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
        self._retry_running_actions = retry_running_actions
        self._retry_failed_actions = retry_failed_actions
        self._agent_timeout_seconds = agent_timeout_seconds
        campaign = self.store.campaign()
        if campaign["spec_hash"] != self.spec.approved_hash():
            raise EngineError("approved campaign spec hash mismatch")
        if campaign["status"] in {CampaignStatus.GOAL_MET.value, CampaignStatus.STOPPED.value}:
            return campaign
        if resume and campaign["status"] in {CampaignStatus.PAUSED_INFRA.value, CampaignStatus.PAUSED_NEEDS_INPUT.value}:
            self.store.update_campaign(status=CampaignStatus.RUNNING.value, stop_reason=None)
        probe = self.agent.probe()
        if not probe.get("available"):
            self._stop(StopReason.PAUSED_INFRA, CampaignStatus.PAUSED_INFRA)
            raise EngineError(f"agent unavailable: {probe}")
        self.workspace.ensure(self.spec.base_commit)
        self._baseline()
        if self._post_iteration_stop():
            return self.store.campaign()

        while True:
            campaign = self.store.campaign()
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
