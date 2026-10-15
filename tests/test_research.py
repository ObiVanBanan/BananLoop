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


def test_repeated_retrieval_misses_create_evidence_brief_not_reranker_advice():
    s = spec(
        agent_visible_metrics=["quality", "RETRIEVAL_MISS", "initial_recall", "rerank_recall"],
        editable_scope=["src/nomenclature_matcher/"],
        protected_paths=["data/competitor_catalogs/**"],
    )
    rows = []
    for ordinal, mechanism in [(1, "query-expansion"), (2, "cross-encoder-rerank")]:
        rows.append({
            "ordinal": ordinal,
            "status": "REJECTED",
            "failure_category": "PRIMARY_NOT_IMPROVED",
            "hypothesis_json": json.dumps({"mechanism_key": mechanism}),
            "evaluation_json": json.dumps({
                "status": "ok",
                "metrics": {
                    "quality": {"value": 0.3},
                    "RETRIEVAL_MISS": {"value": 37},
                    "initial_recall": {"value": 0.63},
                    "rerank_recall": {"value": 0.63},
                },
            }),
            "decision_json": json.dumps({"accepted": False, "reasons": ["PRIMARY_NOT_IMPROVED"]}),
        })
    state = build_research_state(s, campaign(), rows)
    diagnosis = state.repeated_failure_diagnosis
    assert diagnosis["kind"] == "repeated_retrieval_miss"
    assert [item["retrieval_miss"] for item in diagnosis["evidence"]] == [37, 37]
    assert "before reranking" in diagnosis["finding"]
    assert "catalog coverage versus Qdrant/index retrieval loss" in diagnosis["unknowns"][1]
    assert "ranking-only" in diagnosis["required_next_step"]
    assert diagnosis["scope_constraints"]["catalogs_editable"] is False
    assert diagnosis["scope_constraints"]["operator_action_if_data_change_is_supported"] == (
        "expand editable_scope to the relevant catalog path"
    )


def test_research_state_reports_remaining_effective_call_budget():
    s = spec()
    state = build_research_state(
        s,
        campaign(experiment_count=4, attempt_count=5, agent_calls=7, evaluator_calls=8),
        [],
        budget=s.budget.model_copy(update={
            "max_experiments": 9,
            "max_attempts": 10,
            "max_agent_calls": 12,
            "max_evaluator_calls": 13,
        }),
    )
    assert state.remaining_budget["experiments"] == 5
    assert state.remaining_budget["attempts"] == 5
    assert state.remaining_budget["agent_calls"] == 5
    assert state.remaining_budget["evaluator_calls"] == 5
