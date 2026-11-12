"""Restricted JSON-lines RPC worker for a candidate CovType policy."""
from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        return _jsonable(item())
    raise TypeError("candidate result is not JSON serializable")


def _load_policy():
    candidate = Path("/input/policy.py")
    if not candidate.is_file():
        raise RuntimeError("candidate file is not mounted")
    spec = importlib.util.spec_from_file_location("candidate_policy", candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
    policy_type = getattr(module, "Policy", None)
    if not isinstance(policy_type, type):
        raise RuntimeError("candidate does not define Policy")
    with contextlib.redirect_stdout(sys.stderr):
        return policy_type("/data")


def _handle(policy: Any, request: dict[str, Any]) -> Any:
    operation = request.get("op")
    if operation == "select_queries":
        labeled = {int(k): int(v) for k, v in request["labeled"].items()}
        prices = {int(k): float(v) for k, v in request["prices"].items()}
        with contextlib.redirect_stdout(sys.stderr):
            return policy.select_queries(labeled, float(request["budget_left"]), prices)
    if operation == "select_next":
        observed = {int(k): float(v) for k, v in request["observed"].items()}
        prices = {int(k): float(v) for k, v in request["prices"].items()}
        with contextlib.redirect_stdout(sys.stderr):
            return policy.select_next(observed, float(request["budget_left"]), prices)
    if operation == "predict":
        observed = {int(k): float(v) for k, v in request["observed"].items()}
        with contextlib.redirect_stdout(sys.stderr):
            return policy.predict(observed)
    raise RuntimeError("unknown candidate operation")


def main() -> None:
    policy = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("request must be an object")
            if request.get("op") == "init":
                if policy is not None:
                    raise RuntimeError("policy is already initialized")
                policy = _load_policy()
                response = {"ok": True, "value": None}
            else:
                if policy is None:
                    raise RuntimeError("policy is not initialized")
                response = {"ok": True, "value": _jsonable(_handle(policy, request))}
        except Exception as exc:
            response = {"ok": False, "error": type(exc).__name__}
        sys.__stdout__.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.__stdout__.flush()


if __name__ == "__main__":
    main()
