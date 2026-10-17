from __future__ import annotations

import json

from bananloop.contracts import (
    BudgetSpec,
    CampaignSpec,
    DevExperimentEvidence,
    Direction,
    Hypothesis,
    HypothesisCandidate,
    HypothesisPortfolio,
    MetricSpec,
    ResearchState,
)
from bananloop.research import (
    agent_evaluation_view,
    build_history,
    build_research_state,
    planning_mode,
    redundancy_reason,
    normalize_portfolio,
    select_candidate,
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


def _portfolio_candidate(candidate_id: str, mechanism: str, **overrides) -> HypothesisCandidate:
    data = {
        "candidate_id": candidate_id,
        "hypothesis": Hypothesis(
            hypothesis=f"change {mechanism}", rationale="bounded experiment",
            expected_effect="observable metric changes", files_or_area=["src"],
            falsification_condition="metric unchanged on fixed fixture",
            mechanism_key=mechanism,
        ),
        "evidence_refs": ["INSUFFICIENT_EVIDENCE"],
        "predicted_observable": "quality metric delta",
        "discriminating_test": "compare same frozen input before and after",
        "scope_eligibility": True,
        "estimated_agent_calls": 1,
        "estimated_evaluator_calls": 1,
        "estimated_work_units": None,
        "rationale": "test the mechanism",
        "uncertainty": "causal link is uncertain",
        "inference": "agent_inference",
    }
    data.update(overrides)
    return HypothesisCandidate(**data)


def _selection_state(**overrides) -> ResearchState:
    data = {
        "planning_mode": "reframe",
        "remaining_budget": {"experiments": 2, "attempts": 2, "agent_calls": 2, "evaluator_calls": 2},
        "dev_non_improve_streak": 2,
    }
    data.update(overrides)
    return ResearchState(**data)


def test_portfolio_selector_is_deterministic_and_prefers_new_mechanism_on_reframe():
    portfolio = HypothesisPortfolio(candidates=[
        _portfolio_candidate("retry-cache", "cache"),
        _portfolio_candidate("new-index", "index"),
    ])
    state = _selection_state(mechanisms=[
        {"mechanism_key": "cache", "experiment_ordinals": [1], "dev_outcomes": {"DEV_NO_IMPROVEMENT": 2}},
    ])
    experiments = [{"ordinal": 1, "hypothesis_json": json.dumps({"mechanism_key": "cache"})}]
    first = select_candidate(portfolio, spec=spec(), state=state, experiments=experiments,
                             experiment_ordinal=3, action_id="exp:3:propose")
    again = select_candidate(portfolio, spec=spec(), state=state, experiments=experiments,
                             experiment_ordinal=3, action_id="exp:3:propose")
    assert first.model_dump(mode="json") == again.model_dump(mode="json")
    assert first.selected_candidate_id == "new-index"
    rejected = next(item for item in first.decisions if item.candidate_id == "retry-cache")
    assert "REDUNDANT_MECHANISM" in rejected.rejection_reasons


def test_repeated_no_effect_reframe_selects_materially_different_mechanism():
    repeated = _portfolio_candidate(
        "refine-cache", "cache",
        evidence_refs=["exp:2"],
        hypothesis=Hypothesis(
            hypothesis="test bounded cache invalidation", rationale="two no-effect results",
            expected_effect="quality metric changes", files_or_area=["src"],
            falsification_condition="metric unchanged on fixed fixture", mechanism_key="cache",
            relation="refine", prior_experiment_refs=[1, 2], novelty_rationale="add a discriminating invalidation probe",
        ),
        discriminating_test="separate warm and invalidated cache cases",
    )
    alternative = _portfolio_candidate(
        "new-index", "index", evidence_refs=["exp:2"],
        hypothesis=Hypothesis(
            hypothesis="use a selective index", rationale="different retrieval mechanism",
            expected_effect="fewer retrieval misses", files_or_area=["src"],
            falsification_condition="recall unchanged on the frozen fixture", mechanism_key="index",
            prior_experiment_refs=[2], novelty_rationale="the latest no-effect result weakens cache-only search",
        ),
    )
    state = _selection_state(
        failure_signals=[],
        experiment_evidence=[
            DevExperimentEvidence(ordinal=1, mechanism_key="cache", dev_outcome="DEV_NO_IMPROVEMENT"),
            DevExperimentEvidence(ordinal=2, mechanism_key="cache", dev_outcome="DEV_NO_IMPROVEMENT"),
        ],
        mechanisms=[{"mechanism_key": "cache", "experiment_ordinals": [1, 2],
                     "dev_outcomes": {"DEV_NO_IMPROVEMENT": 2}}],
    )
    rows = [
        {"ordinal": ordinal, "hypothesis_json": json.dumps({"mechanism_key": "cache", "prior_experiment_refs": []})}
        for ordinal in (1, 2)
    ]
    record = select_candidate(HypothesisPortfolio(candidates=[repeated, alternative]),
                              spec=spec(), state=state, experiments=rows,
                              experiment_ordinal=3, action_id="exp:3:propose")
    assert record.selected_candidate_id == "new-index"


def test_portfolio_selector_blocks_unknown_evidence_scope_and_unaffordable_candidate():
    candidate = _portfolio_candidate(
        "unsafe", "shell-out", evidence_refs=["exp:999"],
        hypothesis=Hypothesis(
            hypothesis="edit outside", rationale="", expected_effect="", files_or_area=["../secret"],
            falsification_condition="", mechanism_key="shell-out",
        ), estimated_agent_calls=4,
    )
    result = select_candidate(HypothesisPortfolio(candidates=[candidate]), spec=spec(),
                              state=_selection_state(), experiments=[], experiment_ordinal=1,
                              action_id="exp:1:propose")
    assert result.selected_candidate_id is None
    reasons = result.decisions[0].rejection_reasons
    assert "UNKNOWN_EVIDENCE_REFERENCE" in reasons
    assert "UNSAFE_OR_UNGROUNDED_AREA" in reasons
    assert "INSUFFICIENT_AGENT_BUDGET" in reasons
    assert result.stop_reason


def test_portfolio_parser_records_malformed_candidate_fields_without_leaking_values():
    valid = _portfolio_candidate("good", "good-mechanism").model_dump(mode="json")
    malformed = {**valid, "candidate_id": "bad", "predicted_observable": "", "private_payload": "HOLDOUT_CANARY"}
    leaking = _portfolio_candidate("safe-ref", "safe-ref", evidence_refs=["case:private-GOLD-42"]).model_dump(mode="json")
    portfolio = normalize_portfolio({"candidates": [valid, malformed, leaking]})
    assert [item.candidate_id for item in portfolio.candidates] == ["good", "safe-ref"]
    assert portfolio.malformed_candidates[0]["candidate_id"] == "bad"
    assert portfolio.candidates[-1].evidence_refs == ["<invalid>"]
    assert "HOLDOUT_CANARY" not in json.dumps(portfolio.model_dump(mode="json"))
    assert "private-GOLD-42" not in json.dumps(portfolio.model_dump(mode="json"))


def test_legacy_hypothesis_normalizes_to_stable_single_candidate():
    old = {
        "hypothesis": "cache immutable objects", "rationale": "avoid repeated parse",
        "expected_effect": "lower latency", "files_or_area": ["src"],
        "falsification_condition": "latency unchanged", "mechanism_key": "parse-cache",
    }
    first = normalize_portfolio(old)
    second = normalize_portfolio(old)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.candidates[0].evidence_refs == ["INSUFFICIENT_EVIDENCE"]



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

