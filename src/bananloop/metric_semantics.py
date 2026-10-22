"""Metric semantics used by the DEV analyst and hypothesis planner.

This is a diagnostic instruction, not a new scorer or an inference from unseen
validation/holdout records. Never infer root causes from a single aggregate.
"""
from __future__ import annotations

import re


def metric_failure_guidance(
    name: str,
    *,
    diagnostic_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return stable, metric-specific, DEV-only hypotheses and discriminators."""
    token = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    compact = token.replace("_", "")
    common = {
        "scope": "public_dev_only",
        "distinguish_observation_from_inference": True,
        "do_not_infer_hidden_split_performance": True,
        "require_measured_primary_metric_delta": True,
    }
    evidence = diagnostic_evidence or []
    if compact in {"prauc", "aucpr", "auprc", "averageprecision", "averageprecisionscore"}:
        ranking_evidence = any(
            item.get("kind") == "ranking" and item.get("metric", "").lower() == name.lower()
            for item in evidence
        )
        return {
            **common,
            "kind": "ranking_average_precision",
            "primary_mechanism": "ranking of positive examples relative to negative examples across score thresholds",
            "threshold_invariant": True,
            "metric_specific_evidence_available": ranking_evidence,
            "causal_confidence_ceiling": None if ranking_evidence else 0.35,
            "evidence_status": "available" if ranking_evidence else "unknown_without_aggregate_ranking_evidence",
            "caution": (
                "False negatives at a fixed decision cutoff alone cannot explain a PR-AUC "
                "loss. Changing only a cutoff leaves average precision/PR-AUC unchanged "
                "when continuous scores and labels stay fixed. Do not claim that a "
                "threshold-only change will improve the primary metric."
            ),
            "dev_diagnostics": [
                "public DEV precision-recall curve or aggregate average-precision contributions",
                "public DEV positive/negative score-order violations (aggregate, no case IDs)",
                "public DEV score distributions and time/cohort slices if already available",
                "compare frozen DEV rankings before and after, not only cutoff confusion counts",
            ],
            "causal_test": (
                "Compare the primary PR-AUC on exactly the same frozen public DEV cases; "
                "check whether candidate scores actually change the positive-vs-negative ranking."
            ),
        }
    if compact in {"rocauc", "aucroc", "rocaucs", "rocaucscore"}:
        ranking_evidence = any(
            item.get("kind") == "ranking" and item.get("metric", "").lower() == name.lower()
            for item in evidence
        )
        return {
            **common,
            "kind": "ranking_roc_auc",
            "primary_mechanism": "positive-vs-negative score ordering",
            "threshold_invariant": True,
            "metric_specific_evidence_available": ranking_evidence,
            "causal_confidence_ceiling": None if ranking_evidence else 0.35,
            "evidence_status": "available" if ranking_evidence else "unknown_without_aggregate_ranking_evidence",
            "caution": (
                "A fixed-cutoff false-negative count does not diagnose ROC-AUC. "
                "Changing only the decision cutoff does not improve ROC-AUC "
                "when continuous scores and labels remain fixed."
            ),
            "dev_diagnostics": [
                "public DEV rank-order inversions",
                "public DEV score distributions by class",
                "frozen DEV ROC-AUC changes rather than one-threshold confusion counts",
            ],
            "causal_test": "Measure changed ranking on exactly the same frozen public DEV cases.",
        }
    return {
        **common,
        "kind": "generic",
        "primary_mechanism": "unknown; inspect the metric definition and evaluator protocol",
        "threshold_invariant": None,
        "metric_specific_evidence_available": False,
        "causal_confidence_ceiling": 0.35,
        "evidence_status": "unknown_without_metric_specific_diagnostics",
        "caution": (
            "Do not assume confusion-matrix changes explain the primary metric. "
            "Separate measurements from inferred causes and verify using the frozen DEV evaluator."
        ),
        "dev_diagnostics": [
            "aggregate DEV error categories explicitly allowed by the evaluator",
            "measure the primary metric and permitted guardrails on the same frozen DEV input",
        ],
        "causal_test": "State a falsifiable expected change in the configured primary DEV metric.",
    }
