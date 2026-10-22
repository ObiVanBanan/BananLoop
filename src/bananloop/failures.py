"""Stable, operator-facing classification for durable experiment failures."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    IMPLEMENTATION = "implementation"
    INFRASTRUCTURE = "infrastructure"
    SCIENTIFIC_OUTCOME = "scientific_outcome"
    POLICY = "policy"
    PLANNING = "planning"
    BUDGET = "budget"
    UNKNOWN = "unknown"


_CLASSIFICATIONS: dict[str, tuple[FailureKind, str, bool, str]] = {
    "IMPLEMENTATION_BLOCKED": (FailureKind.IMPLEMENTATION, "Review the preflight or implementation errors; one bounded repair may be attempted.", True, "medium"),
    "NO_CHANGES": (FailureKind.IMPLEMENTATION, "Revise the approved implementation approach; no candidate diff was produced.", False, "low"),
    "CANDIDATE_NOT_MATERIALIZED": (FailureKind.INFRASTRUCTURE, "Restore or re-materialize the recorded candidate snapshot before retrying.", True, "medium"),
    "AGENT_PROTOCOL_ERROR": (FailureKind.INFRASTRUCTURE, "Check the agent adapter and structured response; resume only through the durable action journal.", True, "medium"),
    "INVALID_EVALUATION": (FailureKind.INFRASTRUCTURE, "Repair evaluator/runtime output without changing the frozen evaluation contract.", True, "high"),
    "INVALID_VALIDATION": (FailureKind.INFRASTRUCTURE, "Inspect validation execution and receipt linkage; do not treat this as a scientific result.", True, "high"),
    "EVALUATOR_CHANGED": (FailureKind.POLICY, "Restore frozen evaluator assets and investigate the integrity violation before continuing.", False, "high"),
    "SCOPE_VIOLATION": (FailureKind.POLICY, "Revert protected or out-of-scope edits and review the approved scope.", False, "high"),
    "LEAKAGE_GUARD": (FailureKind.POLICY, "Remove the forbidden pattern and inspect the candidate for evaluation leakage.", False, "high"),
    "VALIDATION_REJECTED": (FailureKind.SCIENTIFIC_OUTCOME, "Keep the current champion; use only allowed DEV evidence to design a new hypothesis.", False, "low"),
    "PRIMARY_NOT_IMPROVED": (FailureKind.SCIENTIFIC_OUTCOME, "Keep the current champion; use only allowed DEV evidence to design a materially different hypothesis.", False, "low"),
    "PAIRED_REGRESSION_OR_INSUFFICIENT_WINS": (FailureKind.SCIENTIFIC_OUTCOME, "Keep the current champion and inspect the paired DEV results before reframing.", False, "low"),
    "UNGROUNDED_HYPOTHESIS": (FailureKind.PLANNING, "Gather admissible DEV evidence or propose a falsifiable, evidence-linked mechanism.", False, "low"),
    "REDUNDANT_HYPOTHESIS": (FailureKind.PLANNING, "Choose a materially different typed intervention and explain its discriminator.", False, "low"),
    "NO_HYPOTHESIS": (FailureKind.PLANNING, "Review the DEV research state and stop honestly if no admissible candidate remains.", False, "low"),
}


def classify_failure(category: str | None) -> dict[str, Any] | None:
    """Return a stable taxonomy record without changing legacy stored categories."""
    if not category:
        return None
    if category.startswith(("GUARDRAIL_FAILED:", "REGRESSION:", "CHECK_FAILED:")):
        return {
            "kind": FailureKind.SCIENTIFIC_OUTCOME.value,
            "category": category,
            "suggested_remediation": "Keep the current champion and use allowed DEV evidence to design a candidate that preserves this constraint.",
            "retryable": False,
            "risk": "low",
        }
    kind, remediation, retryable, risk = _CLASSIFICATIONS.get(
        category,
        (FailureKind.UNKNOWN, "Inspect the durable event and action history before deciding whether to retry.", False, "medium"),
    )
    return {
        "kind": kind.value,
        "category": category,
        "suggested_remediation": remediation,
        "retryable": retryable,
        "risk": risk,
    }
