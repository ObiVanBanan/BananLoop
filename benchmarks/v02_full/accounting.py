"""Hash-chained resource receipts for future blinded benchmark actions.

This module validates submitted accounting evidence. It neither launches an
arm nor proves that an external provider reported every billable action.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .prereg import ResourceCaps


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UsageValue = Annotated[int, Field(strict=True, ge=0)]


class ReceiptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ResourceUsage(ReceiptModel):
    agent_calls: UsageValue | None = None
    evaluator_calls: UsageValue | None = None
    experiments: UsageValue | None = None
    retries: UsageValue | None = None
    input_tokens: UsageValue | None = None
    output_tokens: UsageValue | None = None
    cached_tokens: UsageValue | None = None
    wall_seconds: UsageValue | None = None
    spend_microusd: UsageValue | None = None

    def unknown_dimensions(self) -> tuple[str, ...]:
        return tuple(name for name in ResourceCaps.model_fields if getattr(self, name) is None)


class ActionReceipt(ReceiptModel):
    schema_version: Literal[1] = 1
    pair_id: Digest
    arm_label: Literal["A", "B"]
    sequence: Annotated[int, Field(strict=True, ge=1)]
    action_id: Digest
    action_kind: Literal["MODEL_CALL", "TOOL_CALL", "EVALUATOR_CALL", "RETRY", "EXPERIMENT", "OTHER"]
    actor_id_digest: Digest
    usage: ResourceUsage
    previous_receipt_sha256: Digest | None = None
    receipt_sha256: Digest

    @model_validator(mode="after")
    def verify_receipt_digest(self) -> "ActionReceipt":
        expected = receipt_digest(self)
        if self.receipt_sha256 != expected:
            raise ValueError("action receipt digest does not match canonical receipt content")
        if self.sequence == 1 and self.previous_receipt_sha256 is not None:
            raise ValueError("first action receipt cannot have a predecessor")
        if self.sequence > 1 and self.previous_receipt_sha256 is None:
            raise ValueError("noninitial action receipt must bind its predecessor")
        return self

    def content_payload(self) -> dict:
        value = self.model_dump(mode="json")
        value.pop("receipt_sha256")
        return value


class AccountingResult(ReceiptModel):
    status: Literal["ELIGIBLE", "INELIGIBLE_UNKNOWN_ACCOUNTING", "INELIGIBLE_OVER_CAP"]
    action_count: int
    final_receipt_sha256: Digest | None
    totals: dict[str, int | None]
    unknown_dimensions: tuple[str, ...]
    over_cap_dimensions: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def receipt_digest(receipt: ActionReceipt | dict) -> str:
    payload = receipt.content_payload() if isinstance(receipt, ActionReceipt) else dict(receipt)
    payload.pop("receipt_sha256", None)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def make_receipt(**payload) -> ActionReceipt:
    """Create one canonical test/operator receipt; caller owns evidence truth."""
    complete = {"schema_version": 1, **payload}
    return ActionReceipt.model_validate({**complete, "receipt_sha256": receipt_digest(complete)})


def validate_accounting_chain(
    receipts: tuple[ActionReceipt, ...] | list[ActionReceipt],
    *,
    pair_id: str,
    arm_label: Literal["A", "B"],
    caps: ResourceCaps,
    expected_final_receipt_sha256: str,
) -> AccountingResult:
    """Validate order, identity, hash links and caps; UNKNOWN always ineligible."""
    if not receipts:
        raise ValueError("accounting chain must contain every action receipt")
    if len(set(item.action_id for item in receipts)) != len(receipts):
        raise ValueError("action IDs must be unique within an arm pair")
    previous = None
    totals = {name: 0 for name in ResourceCaps.model_fields}
    unknown: set[str] = set()
    for index, receipt in enumerate(receipts, start=1):
        if receipt.receipt_sha256 != receipt_digest(receipt):
            raise ValueError("action receipt content does not match its digest")
        if receipt.pair_id != pair_id or receipt.arm_label != arm_label:
            raise ValueError("receipt is bound to a different blinded pair or arm label")
        if receipt.sequence != index:
            raise ValueError("receipt sequence must be contiguous starting at one")
        if receipt.previous_receipt_sha256 != previous:
            raise ValueError("receipt chain predecessor does not match the prior receipt")
        for dimension in ResourceCaps.model_fields:
            value = getattr(receipt.usage, dimension)
            if value is None:
                unknown.add(dimension)
            elif dimension not in unknown:
                totals[dimension] += value
        required_dimension = {
            "MODEL_CALL": "agent_calls",
            "TOOL_CALL": "agent_calls",
            "EVALUATOR_CALL": "evaluator_calls",
            "RETRY": "retries",
            "EXPERIMENT": "experiments",
        }.get(receipt.action_kind)
        if required_dimension is not None:
            action_count = getattr(receipt.usage, required_dimension)
            if action_count is not None and action_count != 1:
                raise ValueError(
                    f"{receipt.action_kind} receipt must account for exactly one {required_dimension}"
                )
        previous = receipt.receipt_sha256
    if previous != expected_final_receipt_sha256:
        raise ValueError("accounting chain head does not match the frozen final receipt pin")
    over_cap = tuple(sorted(
        name for name in ResourceCaps.model_fields
        if name not in unknown and totals[name] > getattr(caps, name)
    ))
    unknown_dims = tuple(sorted(unknown))
    result_totals = {name: (None if name in unknown else totals[name]) for name in ResourceCaps.model_fields}
    if unknown_dims:
        status = "INELIGIBLE_UNKNOWN_ACCOUNTING"
    elif over_cap:
        status = "INELIGIBLE_OVER_CAP"
    else:
        status = "ELIGIBLE"
    return AccountingResult(status=status, action_count=len(receipts),
                           final_receipt_sha256=previous, totals=result_totals,
                           unknown_dimensions=unknown_dims, over_cap_dimensions=over_cap)
