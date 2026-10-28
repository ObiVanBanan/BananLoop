from __future__ import annotations

import json

from bananloop.contracts import (
    BudgetSpec,
    CampaignSpec,
    DevExperimentEvidence,
    Direction,
    FailureCapsule,
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
    capsule_evidence_ref,
    planning_mode,
    effective_reframe_threshold,
    synthesize_dev_failure_analysis,
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


def test_portfolio_budget_gates_reject_zero_and_reserve_actual_staged_costs():
    candidate = _portfolio_candidate(
        "understated", "understated", estimated_agent_calls=0,
        estimated_evaluator_calls=0,
    )
    configured = spec(validation_command=["validate"], holdout_command=["holdout"])
    state = _selection_state(remaining_budget={
        "experiments": 1, "attempts": 1, "agent_calls": 1, "evaluator_calls": 2,
        "work_units": 2,
    })
    result = select_candidate(HypothesisPortfolio(candidates=[candidate]), spec=configured,
                              state=state, experiments=[], experiment_ordinal=1,
                              action_id="exp:1:propose")
    reasons = result.decisions[0].rejection_reasons
    assert "ESTIMATED_AGENT_COST_BELOW_MINIMUM" in reasons
    assert "ESTIMATED_EVALUATOR_COST_BELOW_MINIMUM" in reasons
    assert "INSUFFICIENT_EVALUATOR_BUDGET" in reasons
    assert "INSUFFICIENT_EVALUATION_WORK_UNITS" in reasons


def test_capsule_evidence_reference_is_content_stable_across_window_reordering():
    capsule = FailureCapsule(
        title="stale cache", failure_class="wrong result", stage="selection",
        affected_fields=["cache_key"], observation="same input gave different result",
        likely_mechanism="stale entry", general_rule="include identity in key",
        suggested_regression="vary identity with same value", confidence=0.8,
    )
    other = capsule.model_copy(update={"title": "other"})
    original_ref = capsule_evidence_ref(capsule)
    assert original_ref.startswith("capsule:")
    assert capsule_evidence_ref(capsule) == original_ref
    assert [capsule_evidence_ref(item) for item in [other, capsule]][1] == original_ref
    assert original_ref != capsule_evidence_ref(other)
    normalized = normalize_portfolio({"candidates": [
        _portfolio_candidate("stable", "stable", evidence_refs=[original_ref]).model_dump(mode="json")
    ]})
    assert normalized.candidates[0].evidence_refs == [original_ref]


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


def test_auto_diagnosis_persists_unknown_cause_without_hidden_data():
    s = spec(agent_visible_metrics=["quality"])
    analysis = synthesize_dev_failure_analysis(s, {
        "accepted": False,
        "primary_value": 0.1,
        "champion_value": 0.6,
        "delta": -0.5,
        "predicates": {"minimum_improvement": False},
        "reasons": ["PRIMARY_NOT_IMPROVED", "VALIDATION:PRIVATE_RESULT"],
        "validation": {"secret_answer": "DO_NOT_LEAK"},
    })
    assert analysis is not None
    capsule = analysis.capsules[0]
    assert capsule.failure_class == "DEV_METRIC_REGRESSION"
    assert capsule.likely_mechanism == "UNKNOWN"
    assert capsule.confidence == 0.0
    assert "delta=-0.5" in capsule.observation
    assert "PRIVATE" not in analysis.model_dump_json()
    assert "DO_NOT_LEAK" not in analysis.model_dump_json()
    assert synthesize_dev_failure_analysis(s, {"accepted": True}) is None


def test_auto_diagnosis_respects_primary_metric_visibility():
    s = spec(agent_visible_metrics=["other_metric"])
    analysis = synthesize_dev_failure_analysis(s, {
        "accepted": False,
        "primary_value": 123456.0,
        "champion_value": 1.0,
        "delta": 123455.0,
        "reasons": ["PRIMARY_NOT_IMPROVED"],
    })
    assert analysis is not None
    assert "123456" not in analysis.model_dump_json()
    assert analysis.capsules[0].failure_class == "DEV_UNEXPLAINED_REJECTION"


def test_auto_diagnosis_separates_check_failures_from_metric_regressions():
    s = spec()
    analysis = synthesize_dev_failure_analysis(s, {
        "accepted": False,
        "primary_value": 0.7,
        "champion_value": 0.6,
        "delta": 0.1,
        "predicates": {"check:valid": False},
        "reasons": ["CHECK_FAILED:valid"],
    })
    assert analysis.capsules[0].failure_class == "DEV_CHECK_FAILURE"
    assert "not proof" in analysis.capsules[0].general_rule


def test_bounded_reframe_reachable_in_six_round_campaign():
    s = spec(budget=BudgetSpec(
        max_experiments=6, max_attempts=12, max_agent_calls=20,
        max_evaluator_calls=15, plateau_experiments=7,
    ))
    assert effective_reframe_threshold(s) == 4
    rows = [
        {
            "ordinal": i, "status": "REJECTED",
            "hypothesis_json": json.dumps({
                "mechanism_key": f"mechanism-{i}",
                "relation": "new",
            }),
            "evaluation_json": json.dumps({
                "status": "ok", "metrics": {"quality": 0.0}
            }),
            "dev_decision_json": json.dumps({
                "accepted": False, "delta": -0.01,
                "primary_value": 0.0, "champion_value": 0.01,
                "reasons": ["PRIMARY_NOT_IMPROVED"],
            }),
            "decision_json": json.dumps({
                "accepted": False, "delta": -0.01,
                "reasons": ["PRIMARY_NOT_IMPROVED"],
            }),
        }
        for i in range(1, 4)
    ]
    state = build_research_state(s, campaign(experiment_count=3), rows)
    assert state.planning_mode == "reframe"
    assert state.remaining_budget["experiments"] == 3
    assert state.remaining_budget["dev_experiments_until_reframe"] == 0


def test_auto_diagnosis_survives_rejected_trial_and_enters_proposer_memory():
    s = spec()
    analysis = synthesize_dev_failure_analysis(s, {
        "accepted": False, "primary_value": 0.2,
        "champion_value": 0.4, "delta": -0.2,
        "reasons": ["PRIMARY_NOT_IMPROVED"],
    })
    row = {
        "ordinal": 1, "status": "REJECTED", "failure_category": None,
        "hypothesis_json": json.dumps({
            "mechanism_key": "neighbor-damping",
        }),
        "evaluation_json": json.dumps({
            "status": "ok", "metrics": {"quality": 0.2},
            "diagnostics": {"hidden_holdout": 555},
        }),
        "dev_decision_json": json.dumps({
            "accepted": False, "primary_value": 0.2,
            "champion_value": 0.4, "delta": -0.2,
            "reasons": ["PRIMARY_NOT_IMPROVED"],
        }),
        "decision_json": json.dumps({"accepted": False}),
        "failure_analysis_json": analysis.model_dump_json(),
    }
    history = build_history(s, [row])
    assert history[0]["failure_capsules"][0]["failure_class"] == "DEV_METRIC_REGRESSION"
    assert "hidden_holdout" not in json.dumps(history)
    research = build_research_state(s, campaign(), [row])
    assert research.recent_failure_capsules[0].likely_mechanism == "UNKNOWN"
    assert any(f.signal_key == "capsule:DEV_METRIC_REGRESSION" for f in research.failure_signals)




def test_raw_dev_metric_requires_opt_in_and_helps_with_clipped_reward():
    champion = json.dumps({
        "status": "ok",
        "metrics": {"quality": 0.0},
        "diagnostics": {"primary_raw_metric": -3.0, "hidden_case": 123456},
    })
    candidate = json.dumps({
        "status": "ok",
        "metrics": {"quality": 0.0},
        "diagnostics": {"primary_raw_metric": 0.15, "hidden_case": 789101},
    })
    decision = {
        "accepted": False, "primary_value": 0.0,
        "champion_value": 0.0, "delta": 0.0,
        "reasons": ["PRIMARY_NOT_IMPROVED"],
    }
    hidden = synthesize_dev_failure_analysis(
        spec(), decision, champion_dev_json=champion,
        candidate_dev_json=candidate,
    )
    assert "primary_raw_metric" not in hidden.model_dump_json()
    visible = synthesize_dev_failure_analysis(
        spec(agent_visible_diagnostics=["primary_raw_metric"]),
        decision,
        champion_dev_json=champion, candidate_dev_json=candidate,
    )
    text = visible.model_dump_json()
    assert "primary_raw_metric" in text
    assert "delta=+3.15" in text
    assert "hidden_case" not in text
    assert "123456" not in text
    assert "789101" not in text


def test_unknown_cause_capsule_remains_memory_not_enforced_failure_signal():
    s = spec(agent_visible_metrics=["quality"])
    analysis = synthesize_dev_failure_analysis(s, {
        "accepted": False, "primary_value": 0.0,
        "champion_value": 0.25, "delta": -0.25,
        "reasons": ["PRIMARY_NOT_IMPROVED"],
    })
    row = {
        "ordinal": 1, "status": "REJECTED", "failure_category": None,
        "hypothesis_json": json.dumps({"mechanism_key": "unknown-cause-test"}),
        "evaluation_json": json.dumps({"status": "ok", "metrics": {"quality": 0.0}}),
        "dev_decision_json": json.dumps({
            "accepted": False, "primary_value": 0.0,
            "champion_value": 0.25, "delta": -0.25,
            "reasons": ["PRIMARY_NOT_IMPROVED"],
        }),
        "failure_analysis_json": analysis.model_dump_json(),
    }
    state = build_research_state(s, campaign(), [row])
    assert state.failure_signals == []
    assert state.experiment_evidence[0].ordinal == 1
    assert state.recent_failure_capsules[0].likely_mechanism == "UNKNOWN"
    assert build_history(s, [row])[0]["failure_capsules"][0]["confidence"] == 0.0


def test_repairable_portfolio_never_weakens_scope_or_budget_policy():
    from bananloop.contracts import CandidateDecision, PortfolioRecord
    from bananloop.research import repairable_portfolio_failure, portfolio_repair_context
    record = PortfolioRecord(
        experiment_ordinal=1, action_id="exp:1:propose", strategy="exploit",
        decisions=[CandidateDecision(
            candidate_id="bad-path", admissible=False,
            rejection_reasons=["OUT_OF_SCOPE"],
        )],
    )
    assert repairable_portfolio_failure(record)
    context = portfolio_repair_context(record, spec(editable_scope=["output/**"]))
    assert context["attempt"] == context["limit"] == 1
    assert context["allowed_editable_scope"] == ["output/**"]
    assert context["previous_rejections"][0]["rejection_reasons"] == ["OUT_OF_SCOPE"]
    denied = record.model_copy(update={
        "decisions": [CandidateDecision(
            candidate_id="no-budget", admissible=False,
            rejection_reasons=["INSUFFICIENT_AGENT_BUDGET"],
        )]
    })
    assert not repairable_portfolio_failure(denied)
    valid = record.model_copy(update={"selected_candidate_id": "good"})
    assert not repairable_portfolio_failure(valid)


def test_real_causal_capsule_still_becomes_actionable_signal():
    s = spec(agent_visible_metrics=["quality"])
    row = {
        "ordinal": 1, "status": "REJECTED", "failure_category": None,
        "hypothesis_json": json.dumps({"mechanism_key": "causal-failure"}),
        "evaluation_json": json.dumps({"status": "ok", "metrics": {"quality": 0.0}}),
        "failure_analysis_json": json.dumps({"capsules": [{
            "title": "Valid DEV mismatch",
            "failure_class": "KNOWN_DEV_CONSTRAINT",
            "observation": "normalized aggregates disagree",
            "likely_mechanism": "normalization mismatch",
            "general_rule": "compare aggregate normalization paths",
            "confidence": 0.9,
        }]}),
    }
    state = build_research_state(s, campaign(), [row])
    assert any(signal.signal_key == "capsule:KNOWN_DEV_CONSTRAINT"
               for signal in state.failure_signals)


def test_repaired_portfolio_is_visible_to_reframe_planner():
    from bananloop.contracts import PortfolioRecord
    s = spec()
    events = [{
        "event_type": "PORTFOLIO_REPAIR_DECISION",
        "payload_json": PortfolioRecord(
            experiment_ordinal=2, action_id="exp:2:propose-repair",
            strategy="reframe", selected_candidate_id="fixed-idea",
        ).model_dump_json(),
    }]
    state = build_research_state(s, campaign(), [], events=events)
    assert len(state.portfolios) == 1
    assert state.portfolios[0].selected_candidate_id == "fixed-idea"
    assert state.portfolios[0].strategy == "reframe"

