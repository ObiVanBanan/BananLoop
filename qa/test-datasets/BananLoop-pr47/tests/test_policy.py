from bananloop.contracts import CampaignSpec, Direction, EvaluationResult, GuardrailSpec, MetricSpec, NonRegressionSpec
from bananloop.policy import decide, target_met, validate_evaluation


def spec() -> CampaignSpec:
    return CampaignSpec(
        goal="test",
        source_repo="/tmp/x",
        base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=3, min_improvement=0.5),
        guardrails=[GuardrailSpec(metric="regressions", operator="<=", bound=0)],
        mandatory_checks=["tests"],
    )


def result(q: float, regressions: float = 0) -> EvaluationResult:
    return EvaluationResult(metrics={"quality": q, "regressions": regressions}, checks={"tests": True})


def test_accept_and_target():
    d = decide(spec(), result(1), result(3))
    assert d.accepted
    assert d.delta == 2
    assert target_met(spec(), result(3))


def test_guardrail_rejects_even_when_primary_improves():
    d = decide(spec(), result(1), result(2, regressions=1))
    assert not d.accepted
    assert "GUARDRAIL_FAILED:regressions" in d.reasons



def test_non_regression_rejects_candidate_that_improves_primary_but_worsens_secondary():
    s = spec()
    s.guardrails = []
    s.non_regression = [
        NonRegressionSpec(
            metric="regressions",
            direction=Direction.MINIMIZE,
        )
    ]

    d = decide(s, result(1, regressions=0), result(2, regressions=1))

    assert not d.accepted
    assert "REGRESSION:regressions" in d.reasons


def test_non_regression_accepts_equal_or_better_secondary():
    s = spec()
    s.guardrails = []
    s.non_regression = [
        NonRegressionSpec(
            metric="regressions",
            direction=Direction.MINIMIZE,
        )
    ]

    d = decide(s, result(1, regressions=1), result(2, regressions=1))

    assert d.accepted


def test_numeric_diagnostics_can_drive_guardrails_without_becoming_quality_metrics():
    s = spec()
    s.guardrails = [GuardrailSpec(metric="elapsed_seconds", operator="<=", bound=60)]
    s.non_regression = []
    baseline = EvaluationResult(
        metrics={"quality": 1},
        diagnostics={"elapsed_seconds": 2.5, "split": "dev"},
        checks={"tests": True},
    )
    slow_candidate = EvaluationResult(
        metrics={"quality": 2},
        diagnostics={"elapsed_seconds": 61.0, "split": "dev"},
        checks={"tests": True},
    )

    decision = decide(s, baseline, slow_candidate)

    assert not decision.accepted
    assert "GUARDRAIL_FAILED:elapsed_seconds" in decision.reasons


def test_validation_prefixed_non_regression_uses_validation_tier_only():
    s = spec()
    s.guardrails = []
    s.non_regression = [
        NonRegressionSpec(
            metric="validation_pr_auc",
            direction=Direction.MAXIMIZE,
        )
    ]
    dev = EvaluationResult(
        metrics={"quality": 1},
        checks={"tests": True},
    )
    baseline_validation = EvaluationResult(
        metrics={"quality": 1, "pr_auc": 0.40},
        checks={"tests": True},
    )
    candidate_validation = EvaluationResult(
        metrics={"quality": 2, "pr_auc": 0.39},
        checks={"tests": True},
    )

    validate_evaluation(s, dev, tier="dev")
    decision = decide(
        s,
        baseline_validation,
        candidate_validation,
        tier="validation",
    )

    assert not decision.accepted
    assert "REGRESSION:validation_pr_auc" in decision.reasons


def test_validation_prefixed_guardrail_is_scoped_to_validation_tier():
    s = spec()
    s.guardrails = [GuardrailSpec(metric="validation_pr_auc", operator=">=", bound=0.4)]
    s.non_regression = []
    dev = EvaluationResult(metrics={"quality": 3}, checks={"tests": True})
    validate_evaluation(s, dev, tier="dev")
    assert target_met(s, dev, tier="holdout")

    baseline = EvaluationResult(
        metrics={"quality": 1, "pr_auc": 0.42}, checks={"tests": True}
    )
    candidate = EvaluationResult(
        metrics={"quality": 2, "pr_auc": 0.39}, checks={"tests": True}
    )
    decision = decide(s, baseline, candidate, tier="validation")

    assert not decision.accepted
    assert "GUARDRAIL_FAILED:validation_pr_auc" in decision.reasons
    assert not target_met(s, candidate, tier="validation")


def test_legacy_dotted_validation_guardrail_is_scoped_to_validation_tier():
    s = spec()
    s.guardrails = [GuardrailSpec(metric="validation.pr_auc", operator=">=", bound=0.4)]
    s.non_regression = []
    dev = EvaluationResult(metrics={"quality": 3}, checks={"tests": True})
    validate_evaluation(s, dev, tier="dev")
    assert target_met(s, dev, tier="holdout")

    baseline = EvaluationResult(
        metrics={"quality": 1, "pr_auc": 0.42}, checks={"tests": True}
    )
    candidate = EvaluationResult(
        metrics={"quality": 2, "pr_auc": 0.39}, checks={"tests": True}
    )
    decision = decide(s, baseline, candidate, tier="validation")

    assert not decision.accepted
    assert "GUARDRAIL_FAILED:validation.pr_auc" in decision.reasons


def test_legacy_dotted_validation_non_regression_is_scoped_to_validation_tier():
    s = spec()
    s.guardrails = []
    s.non_regression = [
        NonRegressionSpec(metric="validation.pr_auc", direction=Direction.MAXIMIZE)
    ]
    dev = EvaluationResult(metrics={"quality": 1}, checks={"tests": True})
    validate_evaluation(s, dev, tier="dev")

    baseline = EvaluationResult(
        metrics={"quality": 1, "pr_auc": 0.42}, checks={"tests": True}
    )
    candidate = EvaluationResult(
        metrics={"quality": 2, "pr_auc": 0.39}, checks={"tests": True}
    )
    decision = decide(s, baseline, candidate, tier="validation")

    assert not decision.accepted
    assert "REGRESSION:validation.pr_auc" in decision.reasons
