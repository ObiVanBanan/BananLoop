from __future__ import annotations

from bananloop.metric_semantics import metric_failure_guidance


def test_pr_auc_guidance_prohibits_cutoff_only_causal_claims():
    for metric in ("pr_auc", "PR-AUC", "average_precision", "AUPRC"):
        guidance = metric_failure_guidance(metric)
        assert guidance["kind"] == "ranking_average_precision"
        assert guidance["threshold_invariant"] is True
        assert "False negatives" in guidance["caution"]
        assert "cutoff" in guidance["caution"]
        assert "rank" in guidance["causal_test"].lower()
        assert guidance["scope"] == "public_dev_only"


def test_roc_auc_and_generic_guidance_do_not_invent_data():
    roc = metric_failure_guidance("roc_auc")
    assert roc["kind"] == "ranking_roc_auc"
    assert roc["threshold_invariant"] is True
    generic = metric_failure_guidance("balanced_accuracy")
    assert generic["kind"] == "generic"
    assert generic["threshold_invariant"] is None
    assert generic["do_not_infer_hidden_split_performance"] is True
