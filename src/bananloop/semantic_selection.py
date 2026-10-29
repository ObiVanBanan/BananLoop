"""Pure, deterministic verification of advisory LLM portfolio selections.

The LLM never grants admissibility. Only IDs already approved by the core
selector may be chosen, and all evidentiary references must be public and
attached to that candidate.
"""
from __future__ import annotations

from typing import Any

from .contracts import PortfolioChoice, PortfolioRecord


def apply_semantic_choice(
    base: PortfolioRecord, raw: dict[str, Any]
) -> tuple[PortfolioRecord, PortfolioChoice]:
    choice = PortfolioChoice.model_validate(raw)
    eligible = {d.candidate_id for d in base.decisions if d.admissible}
    if choice.selected_candidate_id not in eligible:
        raise ValueError("semantic selector chose an ineligible or unknown candidate")
    matches = [c for c in base.candidates if c.candidate_id == choice.selected_candidate_id]
    if len(matches) != 1:
        raise ValueError("semantic selector candidate ID is ambiguous")
    if any(ref not in matches[0].evidence_refs for ref in choice.evidence_refs):
        raise ValueError("semantic selector fabricated or mismatched a DEV evidence reference")

    result = base.model_copy(deep=True)
    result.selected_candidate_id = choice.selected_candidate_id
    for decision in result.decisions:
        if not decision.admissible:
            continue
        if decision.candidate_id == choice.selected_candidate_id:
            decision.selection_reason = "Selected by verified semantic portfolio comparison (advisory prediction)."
        else:
            decision.selection_reason = "Admissible; semantic portfolio comparison preferred another candidate."
    return result, choice
