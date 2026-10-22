from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .contracts import BudgetSpec, StopReason


def deadline_reached(created_at: str, budget: BudgetSpec) -> bool:
    started = datetime.fromisoformat(created_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= started + timedelta(seconds=budget.deadline_seconds)


def pre_action_stop(campaign: dict, budget: BudgetSpec, kind: str) -> StopReason | None:
    if deadline_reached(campaign["created_at"], budget):
        return StopReason.DEADLINE_REACHED
    if campaign["attempt_count"] >= budget.max_attempts and campaign.get("active_attempt_id") is None:
        return StopReason.ATTEMPT_LIMIT
    if campaign["experiment_count"] >= budget.max_experiments and campaign.get("active_attempt_id") is None:
        return StopReason.BUDGET_EXHAUSTED
    if kind == "agent" and campaign["agent_calls"] >= budget.max_agent_calls:
        return StopReason.BUDGET_EXHAUSTED
    if kind in {"evaluator", "smoke"} and campaign["evaluator_calls"] >= budget.max_evaluator_calls:
        return StopReason.BUDGET_EXHAUSTED
    return None
