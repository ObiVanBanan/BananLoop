"""Trusted image-side shim for one Carps case; no grader code or labels."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import sys
import time
from pathlib import Path

import numpy as np


def _encode(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return [_encode(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    try:
        return [_encode(item) for item in list(value)]
    except Exception:
        return {"__unencodable__": type(value).__name__}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        points = np.ascontiguousarray(request["points"], dtype=np.float64)
        k = int(request["k"])
        seed = int(request["seed"])
        time_budget_s = float(request["time_budget_s"])
        if points.ndim != 2 or points.shape[1] != 3 or not 800 <= len(points) <= 4000:
            raise ValueError("invalid one-case point cloud")
        if k not in {30, 50, 65} or k >= len(points) or not math.isfinite(time_budget_s):
            raise ValueError("invalid one-case call parameters")
        entry = Path("/candidate/select_subset.py")
        spec = importlib.util.spec_from_file_location("candidate_select_subset", entry)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load candidate select_subset.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        function = getattr(module, "select")
        captured = io.StringIO()
        clock = time.monotonic
        start = clock()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            returned = function(points, k, seed, time_budget_s)
        elapsed = clock() - start
        response = {"ok": True, "call_s": elapsed, "result": _encode(returned)}
    except BaseException as exc:
        response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:240]}
    sys.__stdout__.write(json.dumps(response, allow_nan=False, separators=(",", ":")) + "\n")
    sys.__stdout__.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
