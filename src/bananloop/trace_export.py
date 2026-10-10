"""Portable, shareable export of allowlisted durable campaign evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .contracts import EvaluationResult
from .evaluator_binding import EvaluationReceipt, canonical_hash
from .policy import target_met
from .store import CampaignStore, StoreError


FORMAT = "bananloop.trace.v1"
_HASH = re.compile(r"^[0-9a-f]{40,64}$")
_METRIC = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_SENSITIVE = re.compile(r"secret|token|password|credential|prompt|private|api.?key|email|phone|address", re.I)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,119}$")
_ACTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_EVENT_TYPES = {
    "CAMPAIGN_CREATED", "CAMPAIGN_BOOTSTRAPPED", "STOP_REQUESTED", "CAMPAIGN_STOPPED",
    "ATTEMPT_STARTED", "ATTEMPT_COMPLETED", "ATTEMPT_CANCELLED", "STAGE_ENTERED",
    "CHAMPION_PROMOTED", "ACTION_STARTED", "ACTION_COMPLETED", "ACTION_FAILED",
    "ACTION_CANCELLED", "ACTION_RETRIED", "HYPOTHESIS_SELECTION", "UNGROUNDED_HYPOTHESIS_REJECTED",
    "REDUNDANT_HYPOTHESIS_REJECTED", "IMPLEMENTATION_BLOCKED", "IMPLEMENTATION_TRANSFER_VERIFIED",
    "CANDIDATE_TRANSFER_FAILED", "NO_CHANGES_REJECTED", "CANDIDATE_SNAPSHOT_FAILED",
    "CANDIDATE_SNAPSHOT_RECORDED", "CANDIDATE_EVALUATION_BLOCKED", "CANDIDATE_EVALUATION_READY",
    "LEAKAGE_GUARD_REJECTED", "BASELINE_FAILED", "BASELINE_VALIDATION_FAILED",
    "BASELINE_VALIDATION_RECORDED", "BASELINE_RECORDED", "FINAL_HOLDOUT_FAILED",
    "FINAL_HOLDOUT_RECORDED", "AGENT_LAUNCH_PROFILE", "EVALUATOR_BINDING_MISMATCH",
}
_FAILURES = {
    "INVALID_EVALUATION", "INVALID_VALIDATION", "EVALUATOR_CHANGED", "AGENT_PROTOCOL_ERROR",
    "CANDIDATE_NOT_MATERIALIZED", "PAIRED_INPUT_MISMATCH", "NO_CHANGES", "IMPLEMENTATION_BLOCKED",
    "UNGROUNDED_HYPOTHESIS", "REDUNDANT_HYPOTHESIS", "NO_HYPOTHESIS", "SCOPE_VIOLATION",
    "LEAKAGE_GUARD", "CANDIDATE_TRANSFER_FAILED", "PRIMARY_NOT_IMPROVED", "FINAL_HOLDOUT_REJECTED",
}
_CAMPAIGN_STATUSES = {"READY", "RUNNING", "PAUSED_INFRA", "PAUSED_NEEDS_INPUT", "GOAL_MET", "STOPPED"}
_STOP_REASONS = {"GOAL_MET", "FINAL_HOLDOUT_REJECTED", "BUDGET_EXHAUSTED", "DEADLINE_REACHED", "PLATEAU", "NO_HYPOTHESIS", "PAUSED_INFRA", "PAUSED_NEEDS_INPUT", "USER_STOPPED", "ATTEMPT_LIMIT", "EVALUATOR_CALL_LIMIT", "AGENT_CALL_LIMIT"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_model(value: Any) -> str | None:
    if not isinstance(value, str) or not _MODEL_ID.fullmatch(value) or _SENSITIVE.search(value):
        return None
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:[/\\]", value):
        return None
    if re.search(r"(?:^sk-[A-Za-z0-9_-]{16,}$|^Bearer$|^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$)", value):
        return None
    return value


def _safe_changed_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    safe = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 240:
            continue
        path = item.replace("\\", "/")
        parts = path.split("/")
        if (
            path.startswith("/")
            or re.match(r"^[A-Za-z]:", path)
            or any(part in {"", ".", ".."} for part in parts)
            or _SENSITIVE.search(path)
            or re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", path)
        ):
            continue
        safe.append(path)
    return sorted(set(safe))


def _safe_action_id(value: str) -> str:
    if _ACTION_ID.fullmatch(value) and not _SENSITIVE.search(value):
        return value
    return "action-" + _sha(value.encode("utf-8"))[:24]


def _safe_metrics(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, item in sorted(value.items()):
        if not isinstance(name, str) or not _METRIC.fullmatch(name) or _SENSITIVE.search(name) or not isinstance(item, dict):
            continue
        number = item.get("value")
        if isinstance(number, (int, float)) and math.isfinite(number):
            result[name] = {
                "value": number,
                "unit": item.get("unit") if isinstance(item.get("unit"), str) and len(item["unit"]) <= 40 and not _SENSITIVE.search(item["unit"]) else None,
                "n": item.get("n") if isinstance(item.get("n"), int) and item["n"] >= 0 else None,
            }
    return result


def _safe_checks(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for name, item in sorted(value.items()):
        if not isinstance(name, str) or not _METRIC.fullmatch(name) or _SENSITIVE.search(name) or not isinstance(item, dict):
            continue
        if not isinstance(item.get("passed"), bool):
            continue
        result[name] = {
            "passed": item["passed"],
            "executed": item.get("executed") if isinstance(item.get("executed"), int) else None,
            "skipped": item.get("skipped") if isinstance(item.get("skipped"), int) else None,
        }
    return result


def _model_roles(store: CampaignStore) -> dict[str, Any]:
    spec = store.spec()
    roles = {}
    for role in ("bootstrap", "propose", "implement", "analyze"):
        config = spec.agent.for_role(role)
        roles[role] = {
            "kind": config.kind,
            "provider": _safe_model(config.provider),
            "requested_model": _safe_model(config.model),
            "reasoning_effort": config.reasoning_effort,
            "effective_model": None,
            "effective_model_status": "unknown",
        }
    for event in _safe_events(store):
        if event["event_type"] != "AGENT_LAUNCH_PROFILE":
            continue
        try:
            profiles = json.loads(event["payload_json"]).get("roles", {})
        except (TypeError, ValueError):
            continue
        for role, profile in profiles.items() if isinstance(profiles, dict) else []:
            if role not in roles or not isinstance(profile, dict):
                continue
            # The current adapters record requested/configured identity only; they
            # do not claim provider-observed model identity or token usage.
            version = profile.get("cli_version")
            if isinstance(version, str) and len(version) <= 80 and not _SENSITIVE.search(version):
                roles[role]["cli_version"] = version
            roles[role]["launch_profile_recorded"] = True
    return roles


def _campaign_id(campaign: dict[str, Any]) -> str:
    identity = f"{campaign['spec_hash']}\0{campaign['created_at']}".encode("utf-8")
    return "campaign-" + _sha(identity)[:24]


def _table_exists(store: CampaignStore, name: str) -> bool:
    return bool(store.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _safe_receipts(store: CampaignStore) -> list[dict[str, Any]]:
    if not _table_exists(store, "evaluation_receipts"):
        return []
    try:
        rows = store.evaluation_receipts()
    except (KeyError, ValueError):
        return []
    valid = []
    for row in rows:
        try:
            receipt = EvaluationReceipt.model_validate(row["receipt"])
        except (KeyError, ValueError):
            continue
        if row.get("receipt_hash") == receipt.receipt_hash:
            valid.append(row)
    return valid


def _safe_events(store: CampaignStore) -> list[dict[str, Any]]:
    return store.events() if _table_exists(store, "events") else []


def _safe_actions(store: CampaignStore) -> list[dict[str, Any]]:
    return store.actions() if _table_exists(store, "actions") else []


def _safe_experiments(store: CampaignStore) -> list[dict[str, Any]]:
    return store.experiments() if _table_exists(store, "experiments") else []


def _safe_evaluations(store: CampaignStore) -> list[dict[str, Any]]:
    return store.evaluations() if _table_exists(store, "evaluations") else []


def _binding_hash(store: CampaignStore) -> str | None:
    if not _table_exists(store, "evaluator_bindings"):
        return None
    try:
        value = store.evaluator_binding()
    except (KeyError, ValueError):
        return None
    digest = value.get("binding_hash") if value else None
    return digest if isinstance(digest, str) and _HASH.fullmatch(digest) else None


def _reference_integrity(store: CampaignStore, spec: Any) -> bool:
    evidence = spec.evaluation_evidence
    if not evidence or spec.signal_sanity_status != "REFERENCE_VERIFIED":
        return False
    try:
        root = store.path.parent.resolve()
        manifest = (root / "reference-evidence" / "operator-reference.json").resolve(strict=True)
        readiness = (root / "reference-evidence" / "readiness-receipt.json").resolve(strict=True)
        receipt_path = (root / (evidence.reference_receipt_path or "")).resolve(strict=True)
        if any(root not in item.parents for item in (manifest, readiness, receipt_path)):
            return False
        digest = _sha(manifest.read_bytes())
        readiness_digest = _sha(readiness.read_bytes())
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        claimed = receipt.pop("receipt_sha256")
        return bool(
            digest == evidence.reference_manifest_sha256
            and readiness_digest == evidence.readiness_receipt_sha256
            and claimed == evidence.reference_receipt_sha256
            and claimed == canonical_hash(receipt)
            and claimed == spec.signal_sanity_receipt_sha256
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _resource_projection(store: CampaignStore, campaign: dict[str, Any]) -> dict[str, Any]:
    actions = _safe_actions(store)
    actions_by_id = {item["id"]: item for item in actions}
    reservations = store.reservations() if _table_exists(store, "reservations") else []
    by_stage: dict[str, dict[str, Any]] = {}
    for action in actions:
        stage = action.get("stage") or "unknown"
        bucket = by_stage.setdefault(stage, {"agent_calls": 0, "evaluator_calls": 0, "wall_time_seconds": 0.0, "unknown_outcomes": 0})
        key = "agent_calls" if action.get("kind") == "agent" else "evaluator_calls"
        bucket[key] += 1
    for reservation in reservations:
        action = actions_by_id.get(reservation.get("action_id"))
        if action is None or reservation.get("resource") not in {"agent_call", "evaluator_call"}:
            continue
        bucket = by_stage.setdefault(action.get("stage") or "unknown", {"agent_calls": 0, "evaluator_calls": 0, "wall_time_seconds": 0.0, "unknown_outcomes": 0})
        bucket["wall_time_seconds"] += float(reservation.get("duration_ms") or 0.0) / 1000.0
        bucket["unknown_outcomes"] += reservation.get("status") == "UNKNOWN"
    for bucket in by_stage.values():
        bucket["wall_time_seconds"] = round(bucket["wall_time_seconds"], 3)
    try:
        unit_rows = store.conn.execute(
            "SELECT response_json FROM actions WHERE kind!='agent' AND status='COMPLETED'"
        ).fetchall() if _table_exists(store, "actions") else []
        evaluation_units = sum(float(json.loads(row[0]).get("resource_units") or 0) for row in unit_rows)
    except (ValueError, TypeError, KeyError):
        evaluation_units = None
    bootstrap: dict[str, Any] = {"agent_calls": 0, "repairs": 0, "readiness_evaluator_calls": 0, "readiness_evaluator_completed": 0, "readiness_evaluator_failed": 0, "readiness_evaluator_unknown": 0}
    try:
        path = store.path.parent / "bootstrap-plan.json"
        if path.is_file():
            record = json.loads(path.read_text(encoding="utf-8"))
            attempts = record.get("bootstrap_attempts", [])
            bootstrap["agent_calls"] = len(attempts)
            bootstrap["repairs"] = max(0, len(attempts) - 1)
            invocations = [entry for attempt in attempts for entry in (attempt.get("readiness") or {}).get("evaluator_invocations", [])]
            bootstrap["readiness_evaluator_calls"] = len(invocations) if invocations else sum(len((attempt.get("readiness") or {}).get("runs", [])) + int(bool((attempt.get("readiness") or {}).get("full_evaluation") or (attempt.get("readiness") or {}).get("full_evaluation_error"))) for attempt in attempts)
            bootstrap["readiness_evaluator_completed"] = sum(entry.get("status") == "COMPLETED" for entry in invocations)
            bootstrap["readiness_evaluator_failed"] = sum(entry.get("status") == "FAILED" for entry in invocations)
            bootstrap["readiness_evaluator_unknown"] = sum(entry.get("status") in {"UNKNOWN", "RUNNING"} for entry in invocations)
    except (OSError, ValueError, TypeError):
        pass
    return {
        "agent_calls": campaign["agent_calls"],
        "evaluator_calls": campaign["evaluator_calls"],
        "attempts": campaign["attempt_count"],
        "scientific_trials": campaign["experiment_count"],
        "evaluation_units_reported": evaluation_units,
        "evaluation_units_reserved": sum(float(row.get("amount") or 0.0) for row in reservations if row.get("resource") == "evaluation_unit"),
        "by_stage": by_stage,
        "bootstrap": bootstrap,
        "unknown_outcomes": sum(row.get("status") == "UNKNOWN" for row in reservations),
        "open_reservations": sum(row.get("status") == "RESERVED" for row in reservations),
        "cost": None,
        "cost_status": "unavailable_without_metering",
    }


def _event_records(store: CampaignStore, campaign_id: str) -> list[dict[str, Any]]:
    actions = {row["id"]: row for row in _safe_actions(store)}
    experiments = {row["id"]: row for row in _safe_experiments(store)}
    receipts = {row["action_id"]: row for row in _safe_receipts(store)}
    records = []
    for row in _safe_events(store):
        kind = row["event_type"]
        if kind not in _EVENT_TYPES:
            continue
        try:
            raw = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        payload: dict[str, Any] = {}
        if row["event_type"] == "CANDIDATE_SNAPSHOT_RECORDED":
            paths = raw.get("changed_paths")
            if isinstance(paths, list) and all(isinstance(path, str) for path in paths):
                payload["changed_path_count"] = len(paths)
                payload["changed_paths_sha256"] = _sha("\n".join(sorted(paths)).encode("utf-8"))
                payload["changed_paths"] = _safe_changed_paths(paths)
            digest = raw.get("diff_sha256")
            if isinstance(digest, str) and _HASH.fullmatch(digest):
                payload["diff_sha256"] = digest
            tree = raw.get("tree")
            if isinstance(tree, str) and _HASH.fullmatch(tree):
                payload["candidate_tree"] = tree
        for key in ("experiment_id", "attempt_id", "ordinal"):
            value = raw.get(key)
            if isinstance(value, int) and value >= 0:
                payload[key] = value
        raw_action_id = raw.get("action_id") if isinstance(raw.get("action_id"), str) else None
        action = actions.get(raw_action_id) if raw_action_id else None
        action_id = _safe_action_id(raw_action_id) if raw_action_id else None
        if action:
            payload.update({"action_id": action_id, "action_kind": action["kind"], "action_status": action["status"]})
            stage = action["stage"]
            if stage in {"BOOTSTRAP", "PROPOSE", "EXECUTE", "ANALYZE", "BASELINE", "BASELINE_VALIDATION", "EVALUATE", "VALIDATE", "FINAL_HOLDOUT", "REPLAY"}:
                payload["stage"] = stage
            if action["kind"] == "agent":
                payload["role"] = {"PROPOSE": "propose", "EXECUTE": "implement", "ANALYZE": "analyze"}.get(stage, "bootstrap" if stage == "BOOTSTRAP" else "unknown")
            if action["kind"] != "agent" and action.get("request_json"):
                try:
                    request = json.loads(action["request_json"])
                except (TypeError, ValueError):
                    request = {}
                tier = request.get("tier") if isinstance(request, dict) else None
                if tier in {"dev", "validation", "holdout"}:
                    payload["tier"] = tier
                    if kind == "ACTION_COMPLETED":
                        kind = f"EVALUATION_{tier.upper()}"
                snapshot = request.get("snapshot") if isinstance(request, dict) else None
                if isinstance(snapshot, str) and _HASH.fullmatch(snapshot):
                    payload["candidate_snapshot"] = snapshot
                receipt = receipts.get(action_id)
                if receipt:
                    payload["receipt_sha256"] = receipt["receipt_hash"]
                    try:
                        result = json.loads(action.get("response_json") or "{}")
                    except (TypeError, ValueError):
                        result = {}
                    if isinstance(result, dict):
                        payload["metrics"] = _safe_metrics(result.get("metrics"))
                        payload["checks"] = _safe_checks(result.get("checks"))
        experiment_id = payload.get("experiment_id")
        if experiment_id is None and action and isinstance(action.get("experiment_id"), int):
            experiment_id = action["experiment_id"]
            payload["experiment_id"] = experiment_id
        experiment = experiments.get(experiment_id) if experiment_id is not None else None
        if experiment:
            payload["experiment_ordinal"] = experiment["ordinal"]
            if experiment.get("parent_snapshot") and _HASH.fullmatch(experiment["parent_snapshot"]):
                payload["parent_snapshot"] = experiment["parent_snapshot"]
            candidate = experiment.get("candidate_snapshot")
            if isinstance(candidate, str) and _HASH.fullmatch(candidate):
                payload["candidate_snapshot"] = candidate
            payload["status"] = experiment["status"]
            if experiment.get("failure_category") in _FAILURES:
                payload["failure_category"] = experiment["failure_category"]
            try:
                hypothesis = json.loads(experiment.get("hypothesis_json") or "{}")
            except (TypeError, ValueError):
                hypothesis = {}
            mechanism = hypothesis.get("mechanism_key") if isinstance(hypothesis, dict) else None
            if isinstance(mechanism, str):
                payload["mechanism_sha256"] = _sha(mechanism.encode("utf-8"))
                relation = hypothesis.get("relation")
                if relation in {"new", "refine", "retry", "supersede"}:
                    payload["relation"] = relation
            if row["event_type"] == "HYPOTHESIS_SELECTION":
                mode = raw.get("planning_mode")
                if mode in {"exploit", "explore", "reframe"}:
                    payload["planning_mode"] = mode
                    relation = payload.get("relation", "new")
                    payload["selection_rationale_code"] = f"{mode}:{relation}"
                redundancy = raw.get("redundancy_check")
                if redundancy in {"DISTINCT_TEST", "REJECTED"}:
                    payload["redundancy_check"] = redundancy
        safe_timestamp = row["created_at"] if isinstance(row.get("created_at"), str) else None
        event_seq = int(row["seq"])
        event_id = "event-" + _sha(f"{campaign_id}\0{event_seq}\0{row['event_type']}".encode())[:24]
        records.append({
            "schema_version": 1,
            "campaign_id": campaign_id,
            "event_id": event_id,
            "seq": event_seq,
            "timestamp": safe_timestamp,
            "event_type": kind,
            "stage": payload.pop("stage", None),
            "experiment_id": payload.get("experiment_id"),
            "action_id": payload.get("action_id"),
            "receipt_sha256": payload.get("receipt_sha256"),
            "payload": payload,
        })
    replay_records = _replay_records(store, campaign_id, max((record["seq"] for record in records), default=0))
    records.extend(replay_records)
    next_seq = max((record["seq"] for record in records), default=0) + 1
    export_id = "event-" + _sha(f"{campaign_id}\0EXPORT".encode())[:24]
    records.append({
        "schema_version": 1,
        "campaign_id": campaign_id,
        "event_id": export_id,
        "seq": next_seq,
        "timestamp": None,
        "event_type": "EXPORT",
        "stage": None,
        "experiment_id": None,
        "action_id": None,
        "receipt_sha256": None,
        "payload": {"synthetic": True, "source": "read_only_snapshot"},
    })
    return records


def _replay_records(store: CampaignStore, campaign_id: str, after_seq: int) -> list[dict[str, Any]]:
    from .replay import ReplayReceipt

    replay_root = store.path.parent / "replays"
    receipts: list[dict[str, Any]] = []
    for path in sorted(replay_root.glob("*/replay-receipt.json")) if replay_root.exists() else []:
        try:
            replay = ReplayReceipt.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        receipts.append(replay.model_dump(mode="json"))
    receipts.sort(key=lambda item: (item.get("created_at") or "", item["receipt_sha256"]))
    output = []
    for index, receipt in enumerate(receipts, 1):
        action_id = _safe_action_id(receipt["source_action_id"])
        event_id = "event-" + _sha(f"{campaign_id}\0replay\0{receipt['receipt_sha256']}".encode())[:24]
        output.append({
            "schema_version": 1,
            "campaign_id": campaign_id,
            "event_id": event_id,
            "seq": after_seq + index,
            "timestamp": receipt["created_at"],
            "event_type": "REPLAY",
            "stage": "REPLAY",
            "experiment_id": None,
            "action_id": action_id,
            "receipt_sha256": receipt["receipt_sha256"],
            "payload": {
                "source_kind": receipt["source_kind"],
                "status": receipt["status"],
                "candidate_snapshot": receipt["candidate_snapshot"] if isinstance(receipt.get("candidate_snapshot"), str) and _HASH.fullmatch(receipt["candidate_snapshot"]) else None,
                "original_result_sha256": receipt["original_result_sha256"],
                "replay_result_sha256": receipt["replay_result_sha256"],
                "case_input_manifest_sha256": receipt["case_input_manifest_sha256"],
                "protocol_sha256": receipt["protocol_sha256"],
                "runtime_fingerprint": receipt["runtime_fingerprint"],
                "duration_ms": receipt["duration_ms"],
            },
        })
    return output


def build_trace_package(store: CampaignStore) -> dict[str, Any]:
    campaign = store.campaign()
    spec = store.spec()
    campaign_id = _campaign_id(campaign)
    receipts = _safe_receipts(store)
    evaluations = _safe_evaluations(store)
    evidence = spec.evaluation_evidence
    receipt_integrity = _reference_integrity(store, spec)
    trust_level = evidence.trust_level if evidence else "UNVERIFIED"
    if trust_level == "INDEPENDENTLY_VALIDATED" and not receipt_integrity:
        trust_level = "UNVERIFIED"
    independently_validated = set(evidence.independently_validated_tiers if evidence and receipt_integrity else [])
    validation_rows = [row for row in evaluations if row.get("tier", "dev") == "validation"]
    holdout_rows = [row for row in evaluations if row.get("tier", "dev") == "holdout"]
    holdout_met: bool | None = None
    if spec.holdout_command and holdout_rows:
        try:
            result = EvaluationResult.model_validate_json(holdout_rows[-1]["result_json"])
            holdout_met = target_met(spec, result)
        except (ValueError, TypeError, KeyError):
            holdout_met = None
    if not spec.holdout_command:
        final_assessment = "DEV_ONLY"
    elif holdout_met is None:
        final_assessment = "PENDING"
    else:
        final_assessment = "ACCEPTED" if holdout_met else "REJECTED"
    if spec.signal_sanity_status == "UNAVAILABLE" or (
        spec.signal_sanity_status == "REFERENCE_VERIFIED" and "holdout" not in independently_validated
    ):
        final_assessment = "DEV_ONLY"
    if trust_level == "INDEPENDENTLY_VALIDATED":
        scientific_assessment = {"ACCEPTED": "VALIDATED", "REJECTED": "REJECTED", "DEV_ONLY": "DEV_ONLY"}.get(final_assessment, "UNKNOWN")
    else:
        scientific_assessment = "DEV_ONLY" if final_assessment == "DEV_ONLY" else "UNKNOWN"
    scientific_outcome = (
        "GOAL_MET" if campaign["status"] == "GOAL_MET" and scientific_assessment == "VALIDATED"
        else "UNVERIFIED_EVIDENCE" if campaign["status"] == "GOAL_MET"
        else "BUDGET_EXHAUSTED" if campaign.get("stop_reason") in {"BUDGET_EXHAUSTED", "ATTEMPT_LIMIT", "EVALUATOR_CALL_LIMIT", "AGENT_CALL_LIMIT"}
        else "BLOCKED" if campaign["status"] == "PAUSED_NEEDS_INPUT"
        else "INFRA_FAILED" if campaign["status"] == "PAUSED_INFRA"
        else "SCIENTIFIC_REJECT" if campaign["status"] == "STOPPED" and campaign.get("stop_reason") in {"FINAL_HOLDOUT_REJECTED", "PLATEAU"}
        else "NO_EXPERIMENTS" if not _safe_experiments(store)
        else campaign["status"]
    )
    evaluations = []
    for row in _safe_evaluations(store):
        try:
            result = json.loads(row["result_json"])
        except (TypeError, ValueError):
            result = {}
        tier = row.get("tier", "dev")
        if tier not in {"dev", "validation", "holdout"}:
            tier = "unknown"
        evaluations.append({
            "tier": tier,
            "candidate_snapshot": row["candidate_snapshot"] if _HASH.fullmatch(row["candidate_snapshot"] or "") else None,
            "metrics": _safe_metrics(result.get("metrics") if isinstance(result, dict) else None),
            "checks": _safe_checks(result.get("checks") if isinstance(result, dict) else None),
            "protocol_sha256": result.get("protocol_sha256") if isinstance(result, dict) and _HASH.fullmatch(result.get("protocol_sha256") or "") else None,
            "artifact_sha256s": sorted(
                artifact["sha256"]
                for artifact in (result.get("artifact_manifest", []) if isinstance(result, dict) else [])
                if isinstance(artifact, dict) and isinstance(artifact.get("sha256"), str) and _HASH.fullmatch(artifact["sha256"])
            ),
            "receipt_sha256": next((receipt["receipt_hash"] for receipt in receipts if receipt["receipt"].get("candidate_snapshot") == row["candidate_snapshot"] and receipt["receipt"].get("tier") == tier), None),
        })
    roles = _model_roles(store)
    resources = _resource_projection(store, campaign)
    metric_name = spec.primary_metric.name
    safe_metric_name = metric_name if _METRIC.fullmatch(metric_name) and not _SENSITIVE.search(metric_name) else "primary_metric"
    summary = {
        "schema_version": 1,
        "format": FORMAT,
        "campaign_id": campaign_id,
        "goal_summary": f"Optimize primary metric {safe_metric_name} toward {spec.primary_metric.target}.",
        "goal_sha256": _sha(spec.goal.encode("utf-8")),
        "created_at": campaign["created_at"],
        "updated_at": campaign["updated_at"],
        "source_repository_fingerprint": _sha(str(Path(spec.source_repo).resolve()).casefold().encode("utf-8")),
        "source_base_commit": spec.base_commit if isinstance(spec.base_commit, str) and _HASH.fullmatch(spec.base_commit) else None,
        "champion_commit": campaign.get("champion_snapshot") if isinstance(campaign.get("champion_snapshot"), str) and _HASH.fullmatch(campaign["champion_snapshot"]) else None,
        "campaign_outcome": campaign["status"] if campaign["status"] in _CAMPAIGN_STATUSES else "UNKNOWN",
        "stop_reason": campaign.get("stop_reason") if campaign.get("stop_reason") in _STOP_REASONS else None,
        "final_assessment": final_assessment,
        "scientific_assessment": scientific_assessment,
        "scientific_outcome": scientific_outcome,
        "validation_aggregate_available": bool(validation_rows),
        "holdout_aggregate_available": bool(holdout_rows),
        "evaluator_trust_by_tier": {
            tier: "INDEPENDENTLY_VALIDATED" if tier in independently_validated else trust_level
            for tier in ("dev", "validation", "holdout")
        },
        "baseline_metrics": _safe_metrics(json.loads(campaign["baseline_json"]).get("metrics", {}) if campaign.get("baseline_json") else {}),
        "champion_metrics": _safe_metrics(json.loads(campaign["champion_json"]).get("metrics", {}) if campaign.get("champion_json") else {}),
        "evaluations": evaluations,
        "model_roles": roles,
        "resources": resources,
        "evidence": {
            "evaluator_binding_sha256": _binding_hash(store),
            "receipt_sha256s": sorted(row["receipt_hash"] for row in receipts),
            "raw_prompts_included": False,
            "case_level_validation_or_holdout_included": False,
            "artifacts_included": False,
        },
        "limitations": [
            "Raw goal text, agent prompts/responses, filesystem paths, case-level validation/holdout data, and raw artifacts are omitted from this shareable export.",
            "Configured model identity is not proof of provider-reported effective identity; unknown usage and cost remain unknown.",
            "Evaluator trust is upgraded only when the persisted operator reference manifest and its verification/readiness receipts verify in campaign state.",
        ],
    }
    events = _event_records(store, campaign_id)
    report_md = _render_summary(summary)
    return {"events.jsonl": events, "summary.json": summary, "report.md": report_md}


def _render_summary(summary: dict[str, Any]) -> str:
    resources = summary["resources"]
    lines = [
        "# BananLoop campaign trace",
        "",
        f"- Campaign: `{summary['campaign_id']}`",
        f"- Goal: {summary['goal_summary']}",
        f"- Outcome: `{summary['campaign_outcome']}` / scientific assessment `{summary['scientific_assessment']}`",
        f"- Evaluator trust by tier: {json.dumps(summary['evaluator_trust_by_tier'], sort_keys=True)}",
        f"- Source commit: `{summary['source_base_commit']}`; champion: `{summary['champion_commit'] or 'unknown'}`",
        f"- Baseline metrics: `{json.dumps(summary['baseline_metrics'], sort_keys=True)}`",
        f"- Champion metrics: `{json.dumps(summary['champion_metrics'], sort_keys=True)}`",
        f"- Calls: {resources['agent_calls']} agent, {resources['evaluator_calls']} evaluator; {resources['attempts']} attempts, {resources['scientific_trials']} scientific trials",
        f"- Evaluation units: {resources['evaluation_units_reported']}; cost: unknown without metering",
        "- Raw prompts, local paths, per-case validation/holdout data, and raw artifacts are not included.",
        "",
    ]
    return "\n".join(lines)


def _write_file(path: Path, data: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _verify_existing_export(output: Path) -> None:
    if not output.exists():
        return
    if not output.is_dir() or output.is_symlink():
        raise StoreError("trace export destination must be a regular directory")
    try:
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        manifest_hash = (output / "manifest.sha256").read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        raise StoreError("refusing to replace a directory that is not a verified BananLoop trace export")
    if not isinstance(manifest, dict):
        raise StoreError("refusing to replace an unrecognized or modified trace export")
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    files = manifest.get("files") if isinstance(manifest, dict) else None
    expected_files = {"events.jsonl", "summary.json", "report.md"}
    expected_entries = expected_files | {"manifest.json", "manifest.sha256"}
    if (
        manifest.get("schema_version") != 1
        or manifest.get("format") != FORMAT
        or _sha(payload) != manifest_hash
        or not isinstance(files, dict)
        or set(files) != expected_files
    ):
        raise StoreError("refusing to replace an unrecognized or modified trace export")
    try:
        actual_entries = {entry.name for entry in output.iterdir()}
    except OSError as exc:
        raise StoreError("refusing to replace an unreadable trace export") from exc
    if actual_entries != expected_entries:
        raise StoreError("refusing to replace a trace export with missing or extra files")
    for name, metadata in files.items():
        if name not in {"events.jsonl", "summary.json", "report.md"} or not isinstance(metadata, dict):
            raise StoreError("refusing to replace a modified trace export manifest")
        if (output / name).is_symlink():
            raise StoreError("refusing to replace a trace export containing symlinks")
        try:
            data = (output / name).read_bytes()
        except OSError as exc:
            raise StoreError("refusing to replace an incomplete trace export") from exc
        if _sha(data) != metadata.get("sha256") or len(data) != metadata.get("size_bytes"):
            raise StoreError("refusing to replace a modified trace export")


def export_trace(store: CampaignStore, output: Path, source_repo: Path) -> Path:
    """Write a deterministic shareable package, then swap it into place."""
    output = output.expanduser().absolute()
    for parent in (output, *output.parents):
        if parent.exists() and parent.is_symlink():
            raise StoreError("trace export destination cannot traverse symlinks")
    source_repo = source_repo.resolve()
    resolved_output = output.resolve(strict=False)
    if resolved_output.is_relative_to(source_repo / ".bananloop_eval"):
        raise StoreError("trace export cannot be written into the evaluator or candidate workspace")
    if resolved_output.is_relative_to(source_repo / ".git"):
        raise StoreError("trace export cannot be written into Git metadata")
    try:
        relative = resolved_output.relative_to(source_repo)
    except ValueError:
        relative = None
    if relative is not None:
        if len(relative.parts) < 3 or relative.parts[:2] != (".bananloop", "exports"):
            raise StoreError("in-repository trace exports must be under .bananloop/exports/")
        from .process import run_process

        ignored = run_process(["git", "check-ignore", "-q", str(relative)], cwd=source_repo, timeout=10, check=False)
        if ignored.returncode != 0:
            raise StoreError("in-repository trace export must be gitignored; add .bananloop/exports/ to .gitignore")
        output = source_repo / relative
    state_root = store.path.parent.resolve()
    if output.resolve(strict=False).is_relative_to(state_root):
        raise StoreError("trace export destination must be outside campaign state")
    _verify_existing_export(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    package = build_trace_package(store)
    event_bytes = b"".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for event in package["events.jsonl"]
    )
    summary_bytes = json.dumps(package["summary.json"], ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    report_bytes = package["report.md"].encode("utf-8")
    contents = {"events.jsonl": event_bytes, "summary.json": summary_bytes, "report.md": report_bytes}
    manifest = {"schema_version": 1, "format": FORMAT, "files": {name: {"sha256": _sha(data), "size_bytes": len(data)} for name, data in sorted(contents.items())}}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    backup: Path | None = None
    try:
        for name, data in {**contents, "manifest.json": manifest_bytes, "manifest.sha256": (_sha(manifest_bytes) + "\n").encode("ascii")}.items():
            _write_file(stage / name, data)
        if output.exists():
            backup = output.with_name(f".{output.name}.backup-{uuid.uuid4().hex}")
            output.replace(backup)
        stage.replace(output)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return output
    except BaseException:
        if backup is not None and backup.exists() and not output.exists():
            backup.replace(output)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
