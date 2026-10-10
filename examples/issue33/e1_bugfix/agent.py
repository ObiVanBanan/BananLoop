from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


PROTOCOL_SHA256 = hashlib.sha256(
    b"issue-33-e1:slugify repeated ASCII spaces collapse to one hyphen; tests/test_slug.py"
).hexdigest()
CASE_INPUT_SHA256 = hashlib.sha256(
    json.dumps(
        {"input": "Alpha  Beta", "expected": "alpha-beta", "policy": "slug-v1"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def evaluator_source() -> str:
    return '''from __future__ import annotations
import hashlib, json, os, subprocess, sys
from pathlib import Path
root = Path.cwd()
test_path = sys.argv[1]
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test_path], cwd=root, capture_output=True, text=True)
quality = 1.0 if run.returncode == 0 else 0.0
protocol = hashlib.sha256(b"issue-33-e1:slugify repeated ASCII spaces collapse to one hyphen; tests/test_slug.py").hexdigest()
case_input = hashlib.sha256(json.dumps({"input":"Alpha  Beta","expected":"alpha-beta","policy":"slug-v1"}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
print(json.dumps({
  "schema_version": 1,
  "status": "ok",
  "protocol_sha256": protocol,
  "metrics": {"quality": {"value": quality, "unit": "pass_fraction", "n": 1}},
  "checks": {"regression_test_executed": {"passed": True, "executed": 1, "skipped": 0}},
  "cases": [{"case_id": "repeated-ascii-space", "repetition": 0, "input_sha256": case_input, "metrics": {"quality": quality}}]
}))
'''


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def main() -> None:
    task = json.load(sys.stdin)
    role = task.get("role")
    if role == "bootstrap":
        evaluator = Path(".bananloop_eval/evaluator.py")
        evaluator.parent.mkdir(parents=True, exist_ok=True)
        evaluator.write_text(evaluator_source(), encoding="utf-8")
        emit({
            "status": "ready",
            "summary": "Fixed CPU regression test for repeated-space slug normalization.",
            "project_context": "slug.py is production behavior. tests/test_slug.py is the frozen DEV regression contract. A separate operator-authored validation oracle is protected under tests/ and hidden from the agent.",
            "needs_input": [],
            "evaluation_command": [sys.executable, ".bananloop_eval/evaluator.py", "tests/test_slug.py"],
            "validation_command": [sys.executable, "tests/validation_oracle.py"],
            "holdout_command": [],
            "primary_metric": {"name": "quality", "direction": "maximize", "target": 1.0, "min_improvement": 0.5, "unit": "pass_fraction"},
            "guardrails": [],
            "non_regression": [],
            "mandatory_checks": ["regression_test_executed"],
            "editable_scope": ["slug.py"],
            "protected_paths": ["tests/**"],
            "agent_visible_metrics": ["quality"],
            "agent_visible_diagnostics": [],
            "signal_sanity": {"status": "unavailable", "reason": "This deterministic fixture has one frozen regression oracle and no separate positive/negative case catalog."},
            "evaluator_timeout_seconds": 120,
        })
        return
    if role == "propose":
        emit({
            "hypothesis": "Collapse consecutive spaces before joining slug words.",
            "rationale": "The frozen regression fails because split(' ') retains empty tokens.",
            "expected_effect": "The repeated-space regression passes without changing lowercase behavior.",
            "files_or_area": ["slug.py"],
            "falsification_condition": "The fixed regression still fails or the output changes beyond the specified normalization.",
            "mechanism_key": "normalize-whitespace-before-join",
            "relation": "new",
            "prior_experiment_refs": [],
            "novelty_rationale": "One bounded implementation change directly tests the reproduced failure mechanism.",
        })
        return
    if role == "implement":
        Path("slug.py").write_text(
            "def slugify(value):\n"
            "    return '-'.join(value.lower().split())\n",
            encoding="utf-8",
        )
        test = subprocess.run(
            [sys.executable, "-c", "from slug import slugify; assert slugify('Alpha  Beta') == 'alpha-beta'"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if test.returncode != 0:
            emit({"summary": "Regression did not pass", "changed_paths": ["slug.py"], "verification_notes": test.stdout + test.stderr, "blockers": ["frozen regression still fails"]})
        else:
            emit({"summary": "Collapsed consecutive whitespace before joining slug tokens.", "changed_paths": ["slug.py"], "verification_notes": "The frozen regression passes.", "blockers": []})
        return
    emit({"status": "NO_HYPOTHESIS", "reason": f"Unsupported fixture role: {role}"})


if __name__ == "__main__":
    main()
