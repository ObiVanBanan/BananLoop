"""Offline preflight reporter for the blocked AutoResearchExam pilot.

This module intentionally has no arm runner. It reads a JSON manifest, records
non-PASS gates as blockers, and always reports that no execution was started.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REQUIRED_GATES = (
    "clean_integration_base",
    "task_cases_scorer_and_dataset_hashes",
    "data_rights_and_privacy",
    "agent_evaluator_and_proxy_isolation",
    "target_windows_sandbox_preflight",
    "provider_usd_admission_and_settlement",
    "luna_and_codex_cli_support",
)


def evaluate_preflight(manifest: dict[str, Any], *, manifest_bytes: bytes) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    gates = manifest.get("gates")
    if not isinstance(gates, list):
        raise ValueError("manifest.gates must be a list")

    blockers: list[dict[str, str]] = []
    seen: set[str] = set()
    for gate in gates:
        if not isinstance(gate, dict):
            raise ValueError("each gate must be an object")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id.strip():
            raise ValueError("each gate requires a nonempty id")
        if gate_id in seen:
            raise ValueError(f"duplicate gate id: {gate_id}")
        seen.add(gate_id)
        status = gate.get("status")
        reason = gate.get("reason")
        evidence = gate.get("evidence")
        if status != "PASS":
            blockers.append({
                "gate": gate_id,
                "status": status if isinstance(status, str) else "UNKNOWN",
                "reason": reason if isinstance(reason, str) else "No evidence recorded.",
            })
        elif (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
        ):
            blockers.append({
                "gate": gate_id,
                "status": "UNKNOWN",
                "reason": "PASS has no evidence reference.",
            })

    for required_gate in REQUIRED_GATES:
        if required_gate not in seen:
            blockers.append({
                "gate": required_gate,
                "status": "UNKNOWN",
                "reason": "Required preflight gate is missing from the manifest.",
            })

    decision = "BLOCKED / INELIGIBLE" if blockers else "PRECHECKS_RECORDED_NOT_EXECUTED"
    return {
        "schema_version": 1,
        "protocol_id": manifest.get("protocol_id"),
        "decision": decision,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "blockers": blockers,
        "execution_started": False,
        "runner_implemented": False,
        "model_calls_made": 0,
        "paid_arms_started": 0,
        "holdout_scored": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="report recorded pilot gates without running an arm")
    preflight.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("manifest.json"),
        help="JSON preflight manifest",
    )
    args = parser.parse_args(argv)
    try:
        manifest_bytes = args.config.read_bytes()
        manifest = json.loads(manifest_bytes)
        result = evaluate_preflight(manifest, manifest_bytes=manifest_bytes)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"decision": "INVALID_PREFLIGHT", "error": str(exc)}, indent=2))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "PRECHECKS_RECORDED_NOT_EXECUTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
