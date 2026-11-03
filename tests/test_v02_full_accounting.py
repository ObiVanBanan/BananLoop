import hashlib

import pytest

from benchmarks.v02_full.accounting import (
    ResourceUsage, make_receipt, validate_accounting_chain,
)
from benchmarks.v02_full.prereg import ResourceCaps


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _caps(**overrides):
    return ResourceCaps(**{
        "agent_calls": 10, "evaluator_calls": 5, "experiments": 5,
        "retries": 2, "input_tokens": 1000, "output_tokens": 1000,
        "cached_tokens": 500, "wall_seconds": 100, "spend_microusd": 10000,
        **overrides,
    })


def _chain(*, unknown=(), agent_calls=2, pair_id=None, label="A"):
    pair_id = pair_id or _sha("pair")
    first_usage = {name: 0 for name in ResourceCaps.model_fields}
    first_usage.update({"agent_calls": agent_calls, "input_tokens": 200,
                       "output_tokens": 100, "wall_seconds": 10, "spend_microusd": 500})
    second_usage = {name: 0 for name in ResourceCaps.model_fields}
    second_usage.update({"agent_calls": 1, "input_tokens": 100, "cached_tokens": 50,
                         "wall_seconds": 5, "spend_microusd": 100})
    for name in unknown:
        first_usage[name] = None
    first = make_receipt(
        pair_id=pair_id, arm_label=label, sequence=1, action_id=_sha("action-1"),
        action_kind="MODEL_CALL", actor_id_digest=_sha("actor"), usage=first_usage,
        previous_receipt_sha256=None,
    )
    second = make_receipt(
        pair_id=pair_id, arm_label=label, sequence=2, action_id=_sha("action-2"),
        action_kind="TOOL_CALL", actor_id_digest=_sha("actor"), usage=second_usage,
        previous_receipt_sha256=first.receipt_sha256,
    )
    return first, second


def test_hash_chained_receipts_reconcile_every_dimension_with_blinded_arm_caps():
    first, second = _chain()
    result = validate_accounting_chain(
        [first, second], pair_id=first.pair_id, arm_label="A", caps=_caps(),
        expected_final_receipt_sha256=second.receipt_sha256,
    )
    assert result.status == "ELIGIBLE"
    assert result.action_count == 2
    assert result.totals["agent_calls"] == 3
    assert result.totals["input_tokens"] == 300
    assert result.totals["spend_microusd"] == 600


def test_unknown_accounting_is_not_zero_and_makes_pair_ineligible():
    first, second = _chain(unknown=("spend_microusd",))
    result = validate_accounting_chain(
        [first, second], pair_id=first.pair_id, arm_label="A", caps=_caps(),
        expected_final_receipt_sha256=second.receipt_sha256,
    )
    assert result.status == "INELIGIBLE_UNKNOWN_ACCOUNTING"
    assert result.totals["spend_microusd"] is None
    assert result.totals["agent_calls"] == 3
    assert result.unknown_dimensions == ("spend_microusd",)


def test_any_cap_exceedance_makes_pair_ineligible():
    first, second = _chain(agent_calls=11)
    result = validate_accounting_chain(
        [first, second], pair_id=first.pair_id, arm_label="A", caps=_caps(agent_calls=10),
        expected_final_receipt_sha256=second.receipt_sha256,
    )
    assert result.status == "INELIGIBLE_OVER_CAP"
    assert result.over_cap_dimensions == ("agent_calls",)


def test_action_kind_must_be_reflected_in_resource_usage():
    first, second = _chain(agent_calls=0)
    with pytest.raises(ValueError, match="MODEL_CALL receipt must account for agent_calls"):
        validate_accounting_chain(
            [first, second], pair_id=first.pair_id, arm_label="A", caps=_caps(),
            expected_final_receipt_sha256=second.receipt_sha256,
        )


def test_chain_rejects_tampering_reordering_duplicate_actions_and_wrong_pair():
    first, second = _chain()
    tampered = first.model_copy(update={"usage": ResourceUsage(agent_calls=99)})
    with pytest.raises(ValueError, match="does not match its digest"):
        validate_accounting_chain(
            [tampered, second], pair_id=first.pair_id, arm_label="A", caps=_caps(),
            expected_final_receipt_sha256=second.receipt_sha256,
        )
    with pytest.raises(ValueError, match="sequence"):
        validate_accounting_chain(
            [second, first], pair_id=first.pair_id, arm_label="A", caps=_caps(),
            expected_final_receipt_sha256=first.receipt_sha256,
        )
    duplicate = second.model_copy(update={"action_id": first.action_id})
    with pytest.raises(ValueError, match="unique"):
        validate_accounting_chain(
            [first, duplicate], pair_id=first.pair_id, arm_label="A", caps=_caps(),
            expected_final_receipt_sha256=duplicate.receipt_sha256,
        )
    with pytest.raises(ValueError, match="different blinded"):
        validate_accounting_chain(
            [first, second], pair_id=_sha("other-pair"), arm_label="A", caps=_caps(),
            expected_final_receipt_sha256=second.receipt_sha256,
        )
