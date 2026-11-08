"""Bounded, deterministic DEV-only oracle checks for recorded role outputs.

No dynamic Python expressions, callbacks, shell commands, network or model judges.
A check requires its expected values to remain evaluator-side.
"""
from __future__ import annotations

import math
from typing import Any


class OracleContractError(ValueError):
    pass


KINDS = {"field_equals", "contains_all", "numeric_close", "nonempty"}


def validate_oracle(entry: Any) -> None:
    if not isinstance(entry, dict):
        raise OracleContractError("oracle case must be a JSON object")
    if set(entry) == {"expected_output"}:
        return
    if set(entry) != {"checks"} or not isinstance(entry["checks"], list) or not 1 <= len(entry["checks"]) <= 16:
        raise OracleContractError("oracle requires expected_output or 1..16 fixed checks")
    seen = set()
    for check in entry["checks"]:
        if not isinstance(check, dict) or check.get("type") not in KINDS:
            raise OracleContractError("unknown evaluator check")
        mid = check.get("metric_id")
        if not isinstance(mid, str) or not (1 <= len(mid) <= 60) or not mid.replace("_", "").isalnum() or mid in seen:
            raise OracleContractError("invalid or duplicate metric ID")
        seen.add(mid)
        path = check.get("path")
        if not isinstance(path, list) or not 1 <= len(path) <= 8 or any(
                not isinstance(k, str) or not (1 <= len(k) <= 80) or k.startswith("_") for k in path):
            raise OracleContractError("metric path must name bounded public output fields")
        kind = check["type"]
        permitted = {"metric_id", "path", "type"}
        if kind == "field_equals":
            permitted.add("expected")
            if "expected" not in check:
                raise OracleContractError("field_equals lacks expected value")
        elif kind == "contains_all":
            permitted.add("required")
            value = check.get("required")
            if not isinstance(value, list) or len(value) > 32 or any(not isinstance(x, str) for x in value) or len(value) != len(set(value)):
                raise OracleContractError("contains_all requires a bounded unique string list")
        elif kind == "numeric_close":
            permitted.update({"expected", "absolute_tolerance"})
            target, tol = check.get("expected"), check.get("absolute_tolerance")
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (target, tol)) or tol < 0:
                raise OracleContractError("numeric_close requires finite numeric target and tolerance")
        if set(check) != permitted:
            raise OracleContractError("unexpected or missing oracle check keys")


def score_oracle(entry: dict, answer: Any) -> tuple[int, list[dict]]:
    """Return all-checks success and individually named binary metrics."""
    validate_oracle(entry)
    if set(entry) == {"expected_output"}:
        ok = int(answer == entry["expected_output"])
        return ok, [{"metric_id": "exact_match", "value": ok, "direction": "maximize", "source": "evaluator"}]
    metrics = []
    for check in entry["checks"]:
        value: Any = answer
        for key in check["path"]:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if check["type"] == "field_equals":
            passed = value == check["expected"]
        elif check["type"] == "contains_all":
            passed = isinstance(value, list) and all(isinstance(x, str) for x in value) and set(check["required"]) <= set(value)
        elif check["type"] == "numeric_close":
            passed = (not isinstance(value, bool) and isinstance(value, (int, float))
                      and math.isfinite(value) and abs(value - check["expected"]) <= check["absolute_tolerance"])
        else:
            passed = bool(value)
        metrics.append({"metric_id": check["metric_id"], "value": int(passed),
                        "direction": "maximize", "source": "evaluator"})
    return int(all(item["value"] == 1 for item in metrics)), metrics
