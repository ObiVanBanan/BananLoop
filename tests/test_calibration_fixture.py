"""Sub-second, CPU-only contract check for the calibration DEV fixture."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_mini_matcher_fixture_baseline_is_deterministic():
    fixture = Path(__file__).resolve().parents[1] / "examples" / "calibration" / "mini_matcher"
    assert (fixture / "TASK.md").is_file()
    result = subprocess.run(
        [sys.executable, "evaluator.py"],
        cwd=fixture,
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["checks"]["cases_valid"] is True
    assert payload["metrics"]["quality"] == 7 / 16
    assert payload["diagnostics"]["total"] == 16
