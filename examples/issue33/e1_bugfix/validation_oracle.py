from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from slug import slugify


CASES = [
    ("single-ascii-space", "Alpha Beta", "alpha-beta"),
    ("repeated-ascii-space-boundary", " Alpha  Beta ", "alpha-beta"),
    ("tab-separator", "Alpha\tBeta", "alpha-beta"),
    ("newline-separator", "Alpha\nBeta", "alpha-beta"),
]
PROTOCOL_SHA256 = hashlib.sha256(
    b"operator-authored-validation: slug whitespace normalization; four frozen cases"
).hexdigest()


def main() -> None:
    case_results = []
    passed = 0
    for case_id, value, expected in CASES:
        actual = slugify(value)
        case_passed = actual == expected
        passed += int(case_passed)
        input_hash = hashlib.sha256(
            json.dumps(
                {"case_id": case_id, "input": value, "expected": expected},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        case_results.append(
            {
                "case_id": case_id,
                "repetition": 0,
                "input_sha256": input_hash,
                "metrics": {"quality": float(case_passed)},
            }
        )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ok",
                "protocol_sha256": PROTOCOL_SHA256,
                "metrics": {
                    "quality": {
                        "value": passed / len(CASES),
                        "unit": "pass_fraction",
                        "n": len(CASES),
                    }
                },
                "checks": {
                    "regression_test_executed": {
                        "passed": True,
                        "executed": len(CASES),
                        "skipped": 0,
                    }
                },
                "cases": case_results,
            }
        )
    )


if __name__ == "__main__":
    main()
