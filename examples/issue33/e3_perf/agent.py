from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def main() -> None:
    task = json.load(sys.stdin)
    role = task.get("role")
    if role == "bootstrap":
        evaluator = Path(".bananloop_eval/evaluator.py")
        evaluator.parent.mkdir(parents=True, exist_ok=True)
        evaluator.write_text(
            '''from __future__ import annotations
import hashlib, json, statistics, sys, time
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path.cwd()))
from dedupe import unique_count
values = [i for i in range(2400) for _ in range(2)]
expected = 2400
durations = []
for _ in range(5):
    start = time.perf_counter_ns()
    actual = unique_count(values)
    durations.append((time.perf_counter_ns() - start) / 1_000_000)
latency_ms = statistics.median(durations)
protocol = hashlib.sha256(b"issue-33-e3:2400 unique integers duplicated once; median of 5 timings; expected unique_count=2400").hexdigest()
case_input = hashlib.sha256(json.dumps({"domain": [0, 2399], "copies": 2, "order": "ascending-pairs", "expected": expected}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
print(json.dumps({"schema_version":1,"status":"ok","protocol_sha256":protocol,"metrics":{"latency":{"value":latency_ms,"unit":"ms","n":5}},"checks":{"correctness_guardrail":{"passed":actual == expected,"actual":actual,"expected":expected}},"cases":[{"case_id":"fixed-unique-count-4800","repetition":0,"input_sha256":case_input,"metrics":{"latency":latency_ms}}]}))
''',
            encoding="utf-8",
        )
        emit({
            "status": "ready",
            "summary": "Fixed CPU microbenchmark for duplicate counting with a correctness guardrail.",
            "project_context": "dedupe.py counts unique integer values. The evaluator measures the median of five calls on 4800 fixed values and checks the exact result.",
            "needs_input": [],
            "evaluation_command": [sys.executable, ".bananloop_eval/evaluator.py"],
            "validation_command": [],
            "holdout_command": [],
            "primary_metric": {"name": "latency", "direction": "minimize", "target": 0.0, "min_improvement": 10.0, "unit": "ms"},
            "guardrails": [],
            "non_regression": [],
            "mandatory_checks": ["correctness_guardrail"],
            "editable_scope": ["dedupe.py"],
            "protected_paths": [],
            "agent_visible_metrics": ["latency"],
            "agent_visible_diagnostics": [],
            "signal_sanity": {"status": "unavailable", "reason": "This microbenchmark has one fixed workload and no independent positive/negative quality catalog."},
            "evaluator_timeout_seconds": 120,
        })
        return
    if role == "propose":
        emit({
            "hypothesis": "Use a set to count distinct values in one pass.",
            "rationale": "The current list membership scan compares each new value against all previously seen values.",
            "expected_effect": "Lower median latency for the fixed workload while preserving the exact unique count.",
            "files_or_area": ["dedupe.py"],
            "falsification_condition": "The fixed workload improves by less than the predeclared 10 ms threshold or the unique count changes.",
            "mechanism_key": "hash-set-membership",
            "relation": "new",
            "prior_experiment_refs": [],
            "novelty_rationale": "One data-structure change targets the measured repeated linear membership cost.",
        })
        return
    if role == "implement":
        Path("dedupe.py").write_text(
            "def unique_count(values):\n"
            "    return len(set(values))\n",
            encoding="utf-8",
        )
        emit({"summary": "Counted unique values with a set.", "changed_paths": ["dedupe.py"], "verification_notes": "The fixed workload's expected result is 2400.", "blockers": []})
        return
    emit({"status": "NO_HYPOTHESIS", "reason": f"Unsupported fixture role: {role}"})


if __name__ == "__main__":
    main()
