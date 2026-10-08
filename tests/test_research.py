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


def test_dogfood_partial_gain_preserves_dev_failure_mechanism_evidence():
    s = spec(agent_visible_metrics=[
        "quality", "retrieval_miss_count", "rerank_selection_count",
    ])
    baseline = json.dumps({"metrics": {
        "quality": 0.30, "retrieval_miss_count": 37,
        "rerank_selection_count": 32, "private_failure_count": 99,
    }})
    rows = []
    for ordinal, quality, selection in ((1, 0.30, 32), (2, 0.30, 32), (3, 0.33, 29)):
        rows.append({
            "ordinal": ordinal,
            "status": "REJECTED",
            "failure_category": "PRIMARY_NOT_IMPROVED",
            "hypothesis_json": json.dumps({"mechanism_key": f"candidate-{ordinal}"}),
            "evaluation_json": json.dumps({
                "metrics": {
                    "quality": quality, "retrieval_miss_count": 37,
                    "rerank_selection_count": selection, "private_failure_count": 99,
                },
                "failure_signals": [{"symptom": "private case identity"}],
            }),
            "dev_decision_json": json.dumps({
                "accepted": False,
                "primary_value": quality,
                "champion_value": 0.30,
                "delta": quality - 0.30,
                "predicates": {"minimum_improvement": False},
                "reasons": ["PRIMARY_NOT_IMPROVED"],
            }),
            "decision_json": json.dumps({"validation": {"secret": "hidden tier"}}),
        })

    state = build_research_state(s, campaign(
        experiment_count=3, non_improve_streak=3,
        baseline_json=baseline, champion_json=baseline,
    ), rows)
    assert state.failure_counts == {"DEV_NO_EFFECT": 2, "DEV_PARTIAL_IMPROVEMENT": 1}
    assert state.dev_non_improve_streak == 0
    assert state.planning_mode == "exploit"
    assert [item.signal_key for item in state.failure_signals[:2]] == [
        "metric:retrieval_miss_count", "metric:rerank_selection_count",
    ]
    selection = state.failure_signals[1]
    assert selection.best_candidate_value == 29
    assert selection.best_experiment_ordinal == 3
    assert selection.changed_experiment_ordinals == [3]
    assert state.experiment_evidence[2].primary_delta > 0
    assert state.experiment_evidence[2].failure_signal_deltas == {
        "metric:rerank_selection_count": -3,
    }
    rendered = state.model_dump_json()
    assert "private_failure_count" not in rendered
    assert "private case identity" not in rendered
    assert "hidden tier" not in rendered
