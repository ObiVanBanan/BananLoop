import numpy as np
import pytest

from examples.autoresearchexam.tasks.metric_contracts import (
    aggregate_risk_cells,
    risk_cell_ratio,
    risk_exact,
    risk_reference_indices,
    risk_reference_squared_errors,
    risk_seed_squared_error,
    score_imputation_call,
)


def test_imputation_perfect_mean_and_negative_scores():
    truth = np.array([[0., 0.], [1., 1.], [2., 2.], [3., 3.]])
    mask = np.ones_like(truth, dtype=bool)
    assert score_imputation_call(truth.copy(), truth, mask) == (1.0, True)
    mean_prediction = np.full_like(truth, 1.5)
    assert score_imputation_call(mean_prediction, truth, mask) == (0.0, True)
    assert score_imputation_call(np.zeros_like(truth), truth, mask)[0] < 0.0


def test_imputation_skips_constant_and_single_masked_features():
    truth = np.array([[1., 3., 1.], [1., 4., 2.], [1., 5., 3.], [1., 6., 4.]])
    mask = np.array([
        [True, True, True], [True, False, True],
        [True, False, False], [False, False, False],
    ])
    prediction = np.zeros_like(truth)
    prediction[:2, 2] = 1.5
    assert score_imputation_call(prediction, truth, mask) == (0.0, True)


@pytest.mark.parametrize("raw", [np.array([1.0]), np.array([[np.nan]]), object()])
def test_invalid_imputation_output_scores_zero(raw):
    truth = np.array([[1.], [2.]])
    mask = np.ones_like(truth, dtype=bool)
    assert score_imputation_call(raw, truth, mask) == (0.0, False)


def test_risk_exact_matches_hand_computed_binary_losses():
    p = np.array([[0.5, 0.5], [0.25, 0.75]])
    labels = np.array([0, 1])
    assert risk_exact(p, labels) == pytest.approx((np.log(2) + np.log(4 / 3)) / 2)


def test_risk_reference_is_paired_deterministic_and_without_replacement():
    n_pool = 2000
    p0 = np.linspace(0.1, 0.9, n_pool)
    probs = np.column_stack([p0, 1 - p0]).astype(np.float64)
    labels = np.zeros(n_pool, dtype=np.int64)
    cell_seed = 812734
    first = risk_reference_indices(cell_seed, 17, n_pool, 50)
    assert len(np.unique(first)) == 50
    assert np.array_equal(first, risk_reference_indices(cell_seed, 17, n_pool, 50))
    errors = risk_reference_squared_errors(probs, labels, 50, cell_seed, [17, 18, 19])
    assert np.isfinite(errors).all() and np.all(errors >= 0)
    assert np.array_equal(errors, risk_reference_squared_errors(probs, labels, 50, cell_seed, [17, 18, 19]))


def test_risk_ratio_uses_paired_medians_and_aggregate_is_lower_is_better():
    ratio = risk_cell_ratio(np.array([1., 4., 9.]), np.array([2., 8., 18.]))
    assert ratio == pytest.approx(0.5)
    assert aggregate_risk_cells([0.2, 0.8, 2.0]) == pytest.approx(0.8)


def test_risk_invalid_seed_and_squared_error_cap_match_grader():
    assert risk_seed_squared_error(1.0, 1.0) == 0.0
    assert risk_seed_squared_error(float("nan"), 1.0) == 100.0
    assert risk_seed_squared_error(100.0, 0.0) == 100.0
    assert risk_seed_squared_error(1.0, 0.0, valid=False) == 100.0
