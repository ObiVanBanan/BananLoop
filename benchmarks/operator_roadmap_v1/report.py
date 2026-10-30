"""Validate and summarize paired benchmark trial records without running agents.

Usage: python -m benchmarks.operator_roadmap_v1.report manifest.json trials.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any


class ReportError(ValueError):
    """An input cannot support a trustworthy paired comparison."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError(f"{label} must be an object")
    return value


def _number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ReportError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ReportError(f"{label} must be finite and >= {minimum}")
    return result


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _object(value, "manifest")
    if manifest.get("schema_version") != 1:
        raise ReportError("unsupported manifest schema_version")
    tasks = manifest.get("tasks")
    arms = manifest.get("arms")
    repeats = manifest.get("repeats")
    if not isinstance(tasks, list) or not tasks:
        raise ReportError("tasks must be a nonempty list")
    task_ids = []
    for task in tasks:
        task = _object(task, "task")
        if not isinstance(task.get("id"), str) or not task["id"]:
            raise ReportError("task id must be nonempty")
        if not isinstance(task.get("version"), str) or not task["version"]:
            raise ReportError("task version must be nonempty")
        for field in ("task_toml_sha256", "grader_core_sha256"):
            digest = task.get(field)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ReportError(f"{task['id']}.{field} must be a lowercase SHA-256")
        task_ids.append(task["id"])
    if len(set(task_ids)) != len(task_ids):
        raise ReportError("duplicate task id")
    if not isinstance(arms, list) or len(arms) != 2 or any(not isinstance(a, str) or not a for a in arms):
        raise ReportError("arms must include exactly two named variants")
    if len(set(arms)) != len(arms):
        raise ReportError("duplicate arm")
    arm_configs = _object(manifest.get("arm_configs"), "arm_configs")
    if set(arm_configs) != set(arms):
        raise ReportError("arm_configs must match the two arms")
    for arm, config in arm_configs.items():
        config = _object(config, f"arm_configs.{arm}")
        digest = config.get("config_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ReportError(f"arm_configs.{arm}.config_sha256 must be a lowercase SHA-256")
        payload = {key: value for key, value in config.items() if key != "config_sha256"}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != expected:
            raise ReportError(f"arm_configs.{arm}.config_sha256 does not match config")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ReportError("repeats must be a positive integer")
    budgets = _object(manifest.get("common_budget"), "common_budget")
    for field in ("max_experiments", "max_agent_calls", "max_evaluator_calls", "wall_seconds"):
        value = budgets.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReportError(f"common_budget.{field} must be a positive integer")
    if not isinstance(manifest.get("dataset_revision"), str) or not manifest["dataset_revision"]:
        raise ReportError("dataset_revision is required")
    if not isinstance(manifest.get("suite_id"), str) or not manifest["suite_id"]:
        raise ReportError("suite_id is required")
    return manifest


def validate_trial(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    row = _object(value, "trial")
    if row.get("task_id") not in {task["id"] for task in manifest["tasks"]}:
        raise ReportError("trial references unknown task")
    if row.get("arm") not in manifest["arms"]:
        raise ReportError("trial references unknown arm")
    repeat = row.get("repeat")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1 or repeat > manifest["repeats"]:
        raise ReportError("trial repeat is out of range")
    status = row.get("status")
    if status not in {"completed", "failed", "incomplete"}:
        raise ReportError("trial status must be completed, failed or incomplete")
    if row.get("dataset_revision") != manifest["dataset_revision"]:
        raise ReportError("trial dataset revision differs from frozen manifest")
    if row.get("budget") != manifest["common_budget"]:
        raise ReportError("trial budget differs from frozen common budget")
    if not isinstance(row.get("config_sha256"), str) or len(row["config_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in row["config_sha256"]):
        raise ReportError("trial requires a lowercase configuration SHA-256")
    if row["config_sha256"] != manifest["arm_configs"][row["arm"]]["config_sha256"]:
        raise ReportError("trial configuration differs from frozen manifest")
    for key in ("cost_usd", "duration_seconds", "tokens"):
        if key in row and row[key] is not None:
            if key == "tokens":
                if isinstance(row[key], bool) or not isinstance(row[key], int) or row[key] < 0:
                    raise ReportError("tokens must be a nonnegative integer")
            else:
                _number(row[key], key)
    if status == "completed":
        _number(row.get("reward"), "reward")
        if row["reward"] > 1:
            raise ReportError("reward must be <= 1")
        if row.get("failure_reason") is not None:
            raise ReportError("completed trial cannot have failure_reason")
        if not isinstance(row.get("evidence_path"), str) or not row["evidence_path"]:
            raise ReportError("completed trial requires evidence_path")
    else:
        if row.get("reward") is not None:
            raise ReportError("failed/incomplete trial cannot claim reward")
        if status == "failed" and (not isinstance(row.get("failure_reason"), str) or not row["failure_reason"].strip()):
            raise ReportError("failed trial requires nonempty failure_reason")
    return row


def build_report(manifest: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    indexed: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        row = validate_trial(row, manifest)
        key = (row["task_id"], row["repeat"], row["arm"])
        if key in indexed:
            raise ReportError(f"duplicate trial: {key}")
        indexed[key] = row
    pairs = []
    completed_deltas = []
    for task in manifest["tasks"]:
        for repeat in range(1, manifest["repeats"] + 1):
            cells = {arm: indexed.get((task["id"], repeat, arm)) for arm in manifest["arms"]}
            complete = all(cell is not None and cell["status"] == "completed" for cell in cells.values())
            pair = {"task_id": task["id"], "repeat": repeat,
                    "arms": {arm: {"status": cell["status"], "reward": cell.get("reward"),
                                    "cost_usd": cell.get("cost_usd"), "tokens": cell.get("tokens"),
                                    "duration_seconds": cell.get("duration_seconds"),
                                    "failure_reason": cell.get("failure_reason")}
                             if cell else {"status": "missing", "reward": None, "cost_usd": None,
                                           "tokens": None, "duration_seconds": None, "failure_reason": None}
                             for arm, cell in cells.items()},
                    "complete": complete, "reward_delta": None}
            if complete:
                first, second = manifest["arms"][:2]
                pair["reward_delta"] = cells[second]["reward"] - cells[first]["reward"]
                completed_deltas.append(pair["reward_delta"])
            pairs.append(pair)
    known_costs = [row["cost_usd"] for row in rows if row.get("cost_usd") is not None]
    return {"schema_version": 1, "suite_id": manifest["suite_id"], "expected_pairs": len(pairs),
            "completed_pairs": len(completed_deltas), "mean_reward_delta": fmean(completed_deltas) if completed_deltas else None,
            "known_cost_usd": sum(known_costs), "unknown_cost_trials": len(rows) - len(known_costs),
            "missing_trial_slots": len(pairs) * len(manifest["arms"]) - len(rows), "pairs": pairs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("trials", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in args.trials.read_text(encoding="utf-8").splitlines() if line.strip()]
        result = build_report(manifest, rows)
    except (OSError, json.JSONDecodeError, ReportError) as exc:
        parser.error(str(exc))
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
