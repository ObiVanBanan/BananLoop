from __future__ import annotations

import json

from bananloop.contracts import (
    BudgetSpec,
    CampaignSpec,
    Direction,
    Hypothesis,
    MetricSpec,
)
from bananloop.research import (
    agent_evaluation_view,
    build_history,
    build_research_state,
    planning_mode,
    redundancy_reason,
)


def spec(**overrides) -> CampaignSpec:
    data = {
        "goal": "improve quality",
        "source_repo": ".",
        "base_commit": "abc",
        "evaluation_command": ["eval"],
        "primary_metric": MetricSpec(
            name="quality",
            direction=Direction.MAXIMIZE,
            target=1.0,
            min_improvement=0.01,
        ),
        "budget": BudgetSpec(
            max_experiments=5,
            max_attempts=8,
            max_agent_calls=20,
            max_evaluator_calls=12,
            plateau_experiments=3,
        ),
    }
    data.update(overrides)
    return CampaignSpec(**data)


def campaign(**overrides):
    data = {
        "experiment_count": 1,
        "attempt_count": 1,
        "agent_calls": 2,
        "evaluator_calls": 2,
        "non_improve_streak": 1,
        "created_at": "2099-01-01T00:00:00+00:00",
    }
    data.update(overrides)
    return data


def test_planning_mode_escalates_before_plateau():
    assert planning_mode(0, 3) == "exploit"
    assert planning_mode(1, 3) == "explore"
    assert planning_mode(2, 3) == "reframe"


def test_validation_evidence_never_enters_adaptive_research_state():
    s = spec(
        agent_visible_metrics=["quality"],
        agent_visible_diagnostics=["wrong_not_found"],
    )
    row = {
        "ordinal": 1,
        "status": "REJECTED",
        "hypothesis_json": json.dumps(
            {
                "hypothesis": "normalize threads",
                "rationale": "DEV failures suggest representation mismatch",
                "expected_effect": "higher quality",
                "files_or_area": ["src"],
                "falsification_condition": "no DEV gain",
                "mechanism_key": "thread-normalization",
            }
        ),
        "evaluation_json": json.dumps(
            {
                "status": "ok",
                "metrics": {"quality": 0.73},
                "diagnostics": {
                    "wrong_not_found": 0.20,
                    "secret_case_signal": 123,
                },
            }
        ),
        "dev_decision_json": None,
        "decision_json": json.dumps(
            {
                "accepted": False,
                "primary_value": 0.73,
                "champion_value": 0.70,
                "delta": 0.03,
                "predicates": {
                    "minimum_improvement": True,
                    "validation": False,
                },
                "reasons": [
                    "VALIDATION_REJECTED",
                    "VALIDATION:PRIMARY_NOT_IMPROVED",
                ],
                "validation": {
                    "accepted": False,
                    "primary_value": 0.70,
                    "champion_value": 0.73,
                    "reasons": ["PRIMARY_NOT_IMPROVED"],
                },
            }
        ),
        "failure_category": "VALIDATION_REJECTED",
    }

    history = build_history(s, [row])
    rendered = json.dumps(history, sort_keys=True)
    assert "VALIDATION" not in rendered
    assert "secret_case_signal" not in rendered
    assert history[0]["dev_outcome"] == "DEV_IMPROVEMENT"
    assert history[0]["dev_evaluation"]["diagnostics"] == {"wrong_not_found": 0.2}

    state = build_research_state(s, campaign(non_improve_streak=99), [row])
    assert state.dev_non_improve_streak == 0
    assert state.planning_mode == "exploit"
    assert state.failure_counts == {"DEV_IMPROVEMENT": 1}
    assert "VALIDATION" not in state.model_dump_json()


def test_diagnostics_require_explicit_allowlist():
    s = spec()
    view = agent_evaluation_view(
        s,
        json.dumps(
            {
                "status": "ok",
                "metrics": {"quality": 0.5},
                "diagnostics": {"cluster_a": 3, "cluster_b": 4},
                "artifacts": ["private.json"],
            }
        ),
    )
    assert view["diagnostics"] == {}
    assert view["artifacts"] == []


def test_exact_mechanism_repeat_requires_genealogy_relation():
    previous = [
        {
            "ordinal": 4,
            "hypothesis_json": json.dumps(
                {
                    "hypothesis": "cache parsed data",
                    "rationale": "avoid repeated work",
                    "expected_effect": "lower latency",
                    "files_or_area": ["src"],
                    "falsification_condition": "no latency change",
                    "mechanism_key": "parse-cache",
                }
            ),
        }
    ]

    duplicate = Hypothesis(
        hypothesis="cache again",
        rationale="same mechanism",
        expected_effect="lower latency",
        files_or_area=["src"],
        falsification_condition="no change",
        mechanism_key="parse-cache",
    )
    assert redundancy_reason(duplicate, previous) is not None

    refinement = duplicate.model_copy(
        update={
            "relation": "refine",
            "prior_experiment_refs": [4],
            "novelty_rationale": "cache only immutable parse products",
        }
    )
    assert redundancy_reason(refinement, previous) is None


def test_same_mechanism_with_different_effect_or_area_is_not_false_duplicate():
    previous = [{
        "ordinal": 4,
        "hypothesis_json": json.dumps({
            "hypothesis": "cache parsed data",
            "rationale": "avoid repeated work",
            "expected_effect": "lower latency",
            "files_or_area": ["src/parser"],
            "falsification_condition": "no latency change",
            "mechanism_key": "parse-cache",
        }),
    }]
    different_area = Hypothesis(
        hypothesis="cache image transforms", rationale="reduce decoding cost",
        expected_effect="lower memory use", files_or_area=["src/images"],
        falsification_condition="no memory change", mechanism_key="parse-cache",
    )
    assert redundancy_reason(different_area, previous) is None
    case_sensitive_area = different_area.model_copy(update={
        "expected_effect": "lower latency",
        "files_or_area": ["src/Parser"],
    })
    assert redundancy_reason(case_sensitive_area, previous) is None


def _research_row(ordinal, *, reason="PRIMARY_NOT_IMPROVED", failure="", mechanism="cache lifecycle"):
    return {
        "ordinal": ordinal,
        "status": "REJECTED",
        "failure_category": failure or None,
        "hypothesis_json": json.dumps({
            "hypothesis": "change cache lifecycle", "rationale": "test cache lifecycle",
            "expected_effect": "lower latency", "files_or_area": ["src/cache"],
            "falsification_condition": "latency unchanged", "mechanism_key": f"cache-{ordinal}",
        }),
        "evaluation_json": json.dumps({"status": "ok", "metrics": {"quality": 0.5}}),
        "decision_json": json.dumps({"accepted": False, "reasons": [reason]}),
        "dev_decision_json": None,
        "failure_analysis_json": json.dumps({"capsules": [{
            "title": "cache issue", "failure_class": "CACHE", "observation": "latency did not move",
            "likely_mechanism": mechanism, "general_rule": "test cache lifecycle", "confidence": 0.8,
        }]}),
    }


def test_planning_uses_failure_class_repeated_cause_and_available_budget():
    s = spec(budget=BudgetSpec(
        max_experiments=6, max_attempts=8, max_agent_calls=20,
        max_evaluator_calls=12, plateau_experiments=5,
    ))
    rows = [_research_row(1), _research_row(2)]
    state = build_research_state(s, campaign(experiment_count=2, attempt_count=2, agent_calls=4, evaluator_calls=2), rows)
    assert state.planning_mode == "reframe"
    assert "repeated the same suspected mechanism" in state.planning_rationale
    assert state.last_scientific_outcome == "DEV_NO_EFFECT"
    assert state.reframe_available is True
    assert state.expected_next_test_cost["agent_calls"] == 2

    guarded = build_research_state(
        s, campaign(experiment_count=1, attempt_count=1, agent_calls=2, evaluator_calls=1),
        [_research_row(1, reason="CHECK_FAILED:tests", mechanism="different cause")],
    )
    assert guarded.last_scientific_outcome == "DEV_GUARD_REJECT"
    assert guarded.planning_mode == "explore"

    no_budget = build_research_state(
        s, campaign(experiment_count=6, attempt_count=8, agent_calls=20, evaluator_calls=12), rows,
    )
    assert no_budget.planning_mode == "reframe"
    assert no_budget.reframe_available is False
    assert "no minimum experiment budget remains" in no_budget.planning_rationale


def test_infrastructure_failures_do_not_increase_scientific_streak():
    s = spec()
    row = _research_row(1, failure="INVALID_EVALUATION")
    state = build_research_state(s, campaign(experiment_count=0, attempt_count=1, agent_calls=2, evaluator_calls=1), [row])
    assert state.dev_non_improve_streak == 0
    assert state.last_scientific_outcome is None
    assert state.planning_mode == "exploit"


def test_reframe_requires_reserved_work_units_and_deadline():
    s = spec(budget=BudgetSpec(
        max_experiments=5, max_attempts=8, max_agent_calls=20,
        max_evaluator_calls=12, max_evaluation_units=4,
        evaluation_units_per_call=2, plateau_experiments=3,
    ))
    rows = [_research_row(1), _research_row(2)]
    state = build_research_state(
        s,
        campaign(experiment_count=2, attempt_count=2, agent_calls=4, evaluator_calls=2),
        rows,
        evaluation_units_reserved=3,
    )
    assert state.planning_mode == "reframe"
    assert state.reframe_available is False
    assert state.expected_next_test_cost["evaluation_units"] == 2

    expired = campaign(
        experiment_count=2, attempt_count=2, agent_calls=4, evaluator_calls=2,
        created_at="2000-01-01T00:00:00+00:00",
    )
    state = build_research_state(s, expired, rows)
    assert state.reframe_available is False
    assert "deadline has elapsed" in state.planning_rationale



def test_raw_failure_signals_never_enter_normal_agent_evaluation_view():
    s = spec(agent_visible_metrics=["quality"])
    view = agent_evaluation_view(
        s,
        json.dumps(
            {
                "status": "ok",
                "metrics": {"quality": 0.5},
                "failure_signals": [
                    {
                        "failure_type": "SOURCE_CONFLICT",
                        "stage": "enrichment",
                        "symptom": "specific benchmark identity",
                    }
                ],
                "failure_examples": [
                    {
                        "query": "secret public DEV query",
                        "verdict": "FAIL_WRONG_NOT_FOUND",
                        "expected": {"answer": 42},
                        "actual": {"answer": 7},
                        "pipeline_trace": {"field": "secret"},
                    }
                ],
            }
        ),
    )
    assert view["failure_signals"] == []
    assert view["failure_examples"] == []


def test_failure_capsules_enter_history_and_research_state_without_raw_dev_signal():
    s = spec(agent_visible_metrics=["quality"])
    row = {
        "ordinal": 1,
        "status": "REJECTED",
        "hypothesis_json": json.dumps(
            {
                "hypothesis": "reconcile evidence",
                "rationale": "avoid stale constraints",
                "expected_effect": "higher quality",
                "files_or_area": ["src"],
                "falsification_condition": "no DEV gain",
                "mechanism_key": "constraint-provenance",
            }
        ),
        "evaluation_json": json.dumps(
            {
                "status": "ok",
                "metrics": {"quality": 0.5},
                "failure_signals": [
                    {
                        "failure_type": "SOURCE_CONFLICT",
                        "stage": "enrichment",
                        "symptom": "raw case-specific evidence must stay hidden",
                    }
                ],
            }
        ),
        "dev_decision_json": json.dumps(
            {
                "accepted": False,
                "primary_value": 0.5,
                "champion_value": 0.6,
                "delta": -0.1,
                "predicates": {"minimum_improvement": False},
                "reasons": ["PRIMARY_NOT_IMPROVED"],
            }
        ),
        "decision_json": None,
        "failure_category": "PRIMARY_NOT_IMPROVED",
        "failure_analysis_json": json.dumps(
            {
                "capsules": [
                    {
                        "title": "Reconcile hard-field provenance",
                        "failure_class": "STALE_CONSTRAINT_AFTER_ENRICHMENT",
                        "stage": "enrichment",
                        "affected_fields": ["dn"],
                        "observation": "later evidence contradicted a frozen hard field",
                        "likely_mechanism": "hard constraints were materialized too early",
                        "general_rule": "explicitly arbitrate provenance conflicts before filtering",
                        "suggested_regression": "exercise conflicting sources without case constants",
                        "confidence": 0.9,
                    }
                ]
            }
        ),
    }

    history = build_history(s, [row])
    rendered = json.dumps(history)
    assert "raw case-specific evidence" not in rendered
    assert history[0]["failure_capsules"][0]["failure_class"] == (
        "STALE_CONSTRAINT_AFTER_ENRICHMENT"
    )

    state = build_research_state(s, campaign(), [row])
    assert state.recent_failure_capsules[0].affected_fields == ["dn"]
    assert "raw case-specific evidence" not in state.model_dump_json()
