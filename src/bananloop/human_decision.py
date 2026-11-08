"""Bounded, auditable requests for owner decisions in v2 research."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrategyChangeProposalV1(BaseModel):
    """A researcher proposal only; it grants no authority to change or run code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_strategy: str = Field(min_length=1, max_length=1000)
    proposed_strategy: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_effect: str = Field(min_length=1, max_length=1000)
    changed_scope: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("changed_scope")
    @classmethod
    def bounded_scope_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("strategy-change scope items must be nonempty and bounded")
        return tuple(sorted(set(values)))


class HumanDecisionRequestV1(StrategyChangeProposalV1):
    """Content-addressed proposal durably exposed to the campaign owner."""

    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
