"""Offline validator and reporter for the frozen selector A/B trial ledger.

This module only reads the manifest, frozen portfolio and JSONL trial rows.
It never invokes a selector, model provider, campaign runner or evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


class SelectorReportError(ValueError):
    """The frozen experiment inputs or trial ledger are inconsistent."""


ARMS = {"deterministic", "llm"}
STATUSES = {"completed", "failed", "incomplete"}
MANIFEST_STATUS_BY_AUTH = {
    "NOT_AUTHORIZED_NOT_RUN": "prepared_not_authorized_frozen_portfolio_not_historical",
    "AUTHORIZED": "authorized_frozen_portfolio_not_historical",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectorReportError(f"cannot read JSON {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SelectorReportError(f"cannot hash {path}: {exc}") from exc


def _finite_number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SelectorReportError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise SelectorReportError(f"{name} must be finite and >= {minimum}")
    return number


def validate_inputs(manifest_path: Path, portfolio_path: Path, project_root: Path) -> dict[str, Any]:
    """Validate manifest, portfolio, and current task/code bytes against hashes."""
    manifest = _read_json(manifest_path)
    portfolio = _read_json(portfolio_path)
    if manifest.get("schema_version") != 1:
        raise SelectorReportError("unsupported manifest schema_version")
    task = manifest.get("task")
    if not isinstance(task, dict) or task.get("direction") not in {"maximize", "minimize"}:
        raise SelectorReportError("task and scoring direction are required")
    inputs = task.get("input_sha256")
    if not isinstance(inputs, dict) or not inputs:
        raise SelectorReportError("task input hashes are required")
    fixture_root = project_root / task.get("path", "")
    for relative, expected in inputs.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise SelectorReportError("task input hashes must map paths to SHA-256 strings")
        actual = _sha256(fixture_root / relative)
        if actual != expected:
            raise SelectorReportError(f"task input hash mismatch: {relative}")

    implementations = manifest.get("selector_implementation_sha256")
    if not isinstance(implementations, dict) or not implementations:
        raise SelectorReportError("selector implementation hashes are required")
    for relative, expected in implementations.items():
        actual = _sha256(project_root / relative)
        if actual != expected:
            raise SelectorReportError(f"selector implementation hash mismatch: {relative}")

    portfolio_meta = manifest.get("portfolio")
    if not isinstance(portfolio_meta, dict) or portfolio_meta.get("status") != "FROZEN_MANUAL_PREREGISTERED_NOT_HISTORICAL":
        raise SelectorReportError("portfolio must be explicitly frozen and labeled non-historical")
    if _sha256(portfolio_path) != portfolio_meta.get("sha256"):
        raise SelectorReportError("frozen portfolio SHA-256 mismatch")
    if portfolio.get("schema_version") != 1:
        raise SelectorReportError("unsupported portfolio schema_version")
    if portfolio.get("source_task_sha256") != inputs.get("TASK.md"):
        raise SelectorReportError("portfolio source task hash differs from frozen task")
    candidates = portfolio.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != portfolio_meta.get("candidate_count"):
        raise SelectorReportError("portfolio candidate count differs from manifest")
    candidate_ids = [c.get("candidate_id") for c in candidates if isinstance(c, dict)]
    if len(candidate_ids) != len(candidates) or not all(isinstance(cid, str) and cid for cid in candidate_ids):
        raise SelectorReportError("every portfolio candidate requires an ID")
    if len(set(candidate_ids)) != len(candidate_ids):
        raise SelectorReportError("duplicate frozen portfolio candidate ID")

    arms = [item.get("id") for item in manifest.get("arms", []) if isinstance(item, dict)]
    if set(arms) != ARMS or len(arms) != 2:
        raise SelectorReportError("manifest must define deterministic and llm arms exactly once")
    design = manifest.get("replicate_design")
    seeds = design.get("paired_seeds") if isinstance(design, dict) else None
    if not isinstance(seeds, list) or len(seeds) != 5 or any(isinstance(x, bool) or not isinstance(x, int) for x in seeds):
        raise SelectorReportError("exactly five integer paired seeds are required")
    if len(set(seeds)) != 5:
        raise SelectorReportError("paired seeds must be unique")
    expected_order = design.get("arm_order_by_replicate", {})
    if set(expected_order) != {str(seed) for seed in seeds} or any(
        expected_order[str(seed)] not in (["deterministic", "llm"], ["llm", "deterministic"])
        for seed in seeds
    ):
        raise SelectorReportError("each seed must have an explicit two-arm randomized order")
    if design.get("campaigns") != 10:
        raise SelectorReportError("campaign ceiling must match five seeds times two arms")
    authorization = manifest.get("resource_authorization")
    if not isinstance(authorization, dict):
        raise SelectorReportError("resource_authorization is required")
    auth_status = authorization.get("status")
    expected_manifest_status = MANIFEST_STATUS_BY_AUTH.get(auth_status)
    if expected_manifest_status is None:
        raise SelectorReportError("resource authorization status must be NOT_AUTHORIZED_NOT_RUN or AUTHORIZED")
    if manifest.get("status") != expected_manifest_status:
        raise SelectorReportError("manifest status contradicts resource authorization status")
    if auth_status == "NOT_AUTHORIZED_NOT_RUN":
        for field in ("max_provider_cost_usd", "max_total_tokens", "max_wall_clock_hours"):
            if authorization.get(field) is not None:
                raise SelectorReportError(f"{field} must remain null while not authorized")
    elif auth_status == "AUTHORIZED":
        if not isinstance(authorization.get("authorization_reference"), str) or not authorization["authorization_reference"].strip():
            raise SelectorReportError("authorized manifest requires an authorization_reference")
        _finite_number(authorization.get("max_provider_cost_usd"), "max_provider_cost_usd")
        tokens = authorization.get("max_total_tokens")
        if tokens is None:
            if authorization.get("token_cap_basis") != "provider_enforced_codex_subscription_quota":
                raise SelectorReportError(
                    "null max_total_tokens requires provider_enforced_codex_subscription_quota"
                )
        elif isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
            raise SelectorReportError("max_total_tokens must be a positive integer or null with a provider cap basis")
        _finite_number(authorization.get("max_wall_clock_hours"), "max_wall_clock_hours", minimum=0.001)
    else:
        raise SelectorReportError("resource authorization status must be NOT_AUTHORIZED_NOT_RUN or AUTHORIZED")
    return {
        "manifest": manifest,
        "portfolio": portfolio,
        "manifest_sha256": _sha256(manifest_path),
        "portfolio_sha256": _sha256(portfolio_path),
        "candidate_ids": candidate_ids,
    }


def validate_trial(row: Any, frozen: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise SelectorReportError("each trial row must be an object")
    manifest = frozen["manifest"]
    design = manifest["replicate_design"]
    if row.get("experiment_id") != manifest["experiment_id"]:
        raise SelectorReportError("trial experiment_id differs from manifest")
    if row.get("seed") not in design["paired_seeds"]:
        raise SelectorReportError("trial references unknown seed")
    if row.get("arm") not in ARMS:
        raise SelectorReportError("trial references unknown arm")
    execution_ordinal = row.get("execution_ordinal")
    if isinstance(execution_ordinal, bool) or execution_ordinal not in {1, 2}:
        raise SelectorReportError("execution_ordinal must be 1 or 2")
    seed_order = manifest["replicate_design"]["arm_order_by_replicate"][str(row["seed"])]
    if seed_order[execution_ordinal - 1] != row["arm"]:
        raise SelectorReportError("trial execution_ordinal contradicts frozen arm order")
    if row.get("manifest_sha256") != frozen["manifest_sha256"]:
        raise SelectorReportError("trial manifest hash differs from frozen manifest")
    if row.get("portfolio_sha256") != frozen["portfolio_sha256"]:
        raise SelectorReportError("trial portfolio hash differs from frozen portfolio")
    if row.get("task_input_sha256") != manifest["task"]["input_sha256"]:
        raise SelectorReportError("trial task input hashes differ from frozen inputs")
    if row.get("selector_implementation_sha256") != manifest["selector_implementation_sha256"]:
        raise SelectorReportError("trial selector implementation hashes differ from manifest")
    status = row.get("status")
    if status not in STATUSES:
        raise SelectorReportError("trial status must be completed, failed or incomplete")
    reward = row.get("dev_reward")
    if status == "completed":
        _finite_number(reward, "dev_reward")
        if reward > 1:
            raise SelectorReportError("dev_reward must be <= 1")
        selected = row.get("selected_candidate_id")
        if selected not in frozen["candidate_ids"]:
            raise SelectorReportError("completed trial requires a selected ID from the frozen portfolio")
        if row.get("failure_reason") is not None:
            raise SelectorReportError("completed trial cannot have failure_reason")
    else:
        if reward is not None:
            raise SelectorReportError("failed/incomplete trial cannot claim a DEV reward")
        if status == "failed" and (not isinstance(row.get("failure_reason"), str) or not row["failure_reason"].strip()):
            raise SelectorReportError("failed trial requires a failure_reason")
    for field in ("cost_usd", "duration_seconds"):
        value = row.get(field)
        if value is not None:
            _finite_number(value, field)
    tokens = row.get("tokens")
    if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0):
        raise SelectorReportError("tokens must be a nonnegative integer or null")
    return row


def build_report(frozen: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = frozen["manifest"]
    design = manifest["replicate_design"]
    indexed: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        row = validate_trial(row, frozen)
        key = (row["seed"], row["arm"])
        if key in indexed:
            raise SelectorReportError(f"duplicate trial row: seed={key[0]} arm={key[1]}")
        indexed[key] = row
    auth = manifest["resource_authorization"]
    if rows and auth["status"] != "AUTHORIZED":
        raise SelectorReportError("trial rows are forbidden while resource authorization is not granted")

    pairs: list[dict[str, Any]] = []
    completed_pairs = 0
    llm_deltas: list[float] = []
    for seed in design["paired_seeds"]:
        det = indexed.get((seed, "deterministic"))
        llm = indexed.get((seed, "llm"))
        complete = bool(det and llm and det["status"] == "completed" and llm["status"] == "completed")
        delta = None
        if complete:
            completed_pairs += 1
            direction = manifest["task"]["direction"]
            delta = llm["dev_reward"] - det["dev_reward"] if direction == "maximize" else det["dev_reward"] - llm["dev_reward"]
            llm_deltas.append(delta)
        pairs.append({
            "seed": seed,
            "arm_order": design["arm_order_by_replicate"][str(seed)],
            "complete": complete,
            "observed_arm_order": [row["arm"] for row in sorted(
                (item for item in (det, llm) if item is not None), key=lambda item: item["execution_ordinal"]
            )],
            "llm_advantage_on_dev": delta,
            "arms": {arm: _trial_summary(indexed.get((seed, arm))) for arm in ("deterministic", "llm")},
        })

    present = list(indexed.values())
    totals = {
        "cost_usd": _sum_known(present, "cost_usd"),
        "tokens": _sum_known(present, "tokens"),
        "duration_seconds": _sum_known(present, "duration_seconds"),
    }
    if auth["status"] == "AUTHORIZED":
        if totals["cost_usd"] is not None and totals["cost_usd"] > auth["max_provider_cost_usd"]:
            raise SelectorReportError("observed provider cost exceeds authorized cap")
        if (totals["tokens"] is not None and auth["max_total_tokens"] is not None
                and totals["tokens"] > auth["max_total_tokens"]):
            raise SelectorReportError("observed token usage exceeds authorized cap")
        if totals["duration_seconds"] is not None and totals["duration_seconds"] > auth["max_wall_clock_hours"] * 3600:
            raise SelectorReportError("observed duration exceeds authorized cap")
    missing_slots = 10 - len(present)
    unknown_cost = sum(row.get("cost_usd") is None for row in present)
    unknown_tokens = sum(row.get("tokens") is None for row in present)
    unknown_duration = sum(row.get("duration_seconds") is None for row in present)
    if auth["status"] != "AUTHORIZED":
        cap_status = "not_authorized"
    elif missing_slots or unknown_cost or unknown_tokens or unknown_duration:
        cap_status = "unknown_measurements"
    else:
        cap_status = "within_known_usage_caps"
    return {
        "experiment_id": manifest["experiment_id"],
        "status": "no_trials" if not present else ("complete" if len(present) == 10 and completed_pairs == 5 else "incomplete"),
        "resource_authorization": manifest["resource_authorization"],
        "manifest_sha256": frozen["manifest_sha256"],
        "portfolio_sha256": frozen["portfolio_sha256"],
        "expected_trial_slots": 10,
        "present_trial_rows": len(present),
        "missing_trial_slots": missing_slots,
        "failed_trials": sum(row["status"] == "failed" for row in present),
        "incomplete_trials": sum(row["status"] == "incomplete" for row in present),
        "completed_pairs": completed_pairs,
        "descriptive_mean_llm_advantage_on_dev": sum(llm_deltas) / len(llm_deltas) if llm_deltas else None,
        "known_provider_cost_usd": totals["cost_usd"],
        "unknown_provider_cost_trials": unknown_cost,
        "unknown_provider_cost_slots": unknown_cost + missing_slots,
        "known_tokens": totals["tokens"],
        "unknown_token_trials": unknown_tokens,
        "unknown_token_slots": unknown_tokens + missing_slots,
        "known_duration_seconds": totals["duration_seconds"],
        "unknown_duration_trials": unknown_duration,
        "unknown_duration_slots": unknown_duration + missing_slots,
        "resource_cap_status": cap_status,
        "winner_claim": None,
        "interpretation": "Descriptive DEV evidence for one small fixture only; no selector winner or generalization claim.",
        "pairs": pairs,
    }


def _sum_known(rows: list[dict[str, Any]], field: str) -> float | int | None:
    values = [row[field] for row in rows if row.get(field) is not None]
    return sum(values) if values else None


def _trial_summary(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"status": "missing", "dev_reward": None, "selected_candidate_id": None,
                "cost_usd": None, "tokens": None, "duration_seconds": None, "failure_reason": None}
    return {key: row.get(key) for key in (
        "status", "dev_reward", "selected_candidate_id", "cost_usd", "tokens", "duration_seconds", "failure_reason"
    )}


def load_trials(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SelectorReportError(f"invalid JSONL row at line {line_number}: {exc}") from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/selector_ab_v1/manifest.json"))
    parser.add_argument("--portfolio", type=Path, default=Path("benchmarks/selector_ab_v1/frozen_portfolio.json"))
    parser.add_argument("--trials", type=Path, default=Path("benchmarks/selector_ab_v1/trials.jsonl"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root used to verify frozen input hashes")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args(argv)
    try:
        frozen = validate_inputs(args.manifest, args.portfolio, args.root)
        report = build_report(frozen, load_trials(args.trials))
    except SelectorReportError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
