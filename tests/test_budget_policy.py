from datetime import datetime, timedelta, timezone

import pytest

import bananloop.budget as budget_module
from bananloop.budget import deadline_reached, pre_action_stop
from bananloop.contracts import BudgetSpec, StopReason


def campaign(**changes):
    state = {
        "created_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        "attempt_count": 0,
        "experiment_count": 0,
        "agent_calls": 0,
        "evaluator_calls": 0,
        "active_attempt_id": None,
    }
    state.update(changes)
    return state


@pytest.mark.parametrize("counter,kind,limit,reason", [
    ("attempt_count", "agent", "max_attempts", StopReason.ATTEMPT_LIMIT),
    ("experiment_count", "agent", "max_experiments", StopReason.BUDGET_EXHAUSTED),
    ("agent_calls", "agent", "max_agent_calls", StopReason.BUDGET_EXHAUSTED),
    ("evaluator_calls", "evaluator", "max_evaluator_calls", StopReason.BUDGET_EXHAUSTED),
])
def test_pre_action_stop_at_exact_budget_boundary(counter, kind, limit, reason):
    budget = BudgetSpec(deadline_seconds=3600)
    maximum = getattr(budget, limit)
    assert pre_action_stop(campaign(**{counter: maximum - 1}), budget, kind) is None
    assert pre_action_stop(campaign(**{counter: maximum}), budget, kind) == reason


def test_attempt_limits_wait_until_active_attempt_finishes():
    budget = BudgetSpec(deadline_seconds=3600)
    active = campaign(
        attempt_count=budget.max_attempts,
        experiment_count=budget.max_experiments,
        active_attempt_id="active-1",
    )
    assert pre_action_stop(active, budget, "agent") is None
    assert pre_action_stop({**active, "active_attempt_id": None}, budget, "agent") == StopReason.ATTEMPT_LIMIT


def test_agent_and_evaluator_call_budgets_are_independent():
    budget = BudgetSpec(deadline_seconds=3600)
    exhausted_agent = campaign(agent_calls=budget.max_agent_calls)
    exhausted_evaluator = campaign(evaluator_calls=budget.max_evaluator_calls)
    assert pre_action_stop(exhausted_agent, budget, "agent") == StopReason.BUDGET_EXHAUSTED
    assert pre_action_stop(exhausted_agent, budget, "evaluator") is None
    assert pre_action_stop(exhausted_evaluator, budget, "evaluator") == StopReason.BUDGET_EXHAUSTED
    assert pre_action_stop(exhausted_evaluator, budget, "agent") is None


def test_expired_deadline_blocks_actions_before_other_budgets():
    budget = BudgetSpec(deadline_seconds=5)
    expired = campaign(attempt_count=budget.max_attempts)
    assert deadline_reached(expired["created_at"], budget)
    assert pre_action_stop(expired, budget, "agent") == StopReason.DEADLINE_REACHED


def test_deadline_reached_at_exact_instant(monkeypatch):
    instant = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(budget_module, "datetime", FrozenDateTime)
    budget = BudgetSpec(deadline_seconds=5)
    assert deadline_reached((instant - timedelta(seconds=5)).isoformat(), budget)
    assert not deadline_reached((instant - timedelta(seconds=5) + timedelta(microseconds=1)).isoformat(), budget)
