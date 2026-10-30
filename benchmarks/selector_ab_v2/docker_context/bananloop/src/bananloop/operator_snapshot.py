"""Committed, DEV-only campaign view for a local operator terminal."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .contracts import PivotProposal
from .store import CampaignStore, StoreError


SCHEMA_VERSION = 1
DISPLAY_STAGE = {
    "PROPOSE": "PROPOSE",
    "SELECT": "SELECT",
    "EXECUTE": "IMPLEMENT",
    "EVALUATE": "EVALUATE",
    "VALIDATE": "EVALUATE",
    "ANALYZE": "LEARN",
    "RECORD": "LEARN",
}
SAFE_EVENT_FIELDS = {
    "STAGE_ENTERED": {"stage", "attempt_id"},
    "ATTEMPT_STARTED": {"experiment_id", "attempt_id"},
    "ATTEMPT_COMPLETED": {"experiment_id", "attempt_id", "status"},
    "CAMPAIGN_PAUSED": {"reason", "status"},
    "CAMPAIGN_STOPPED": {"reason", "status"},
    "PAUSE_REQUESTED": set(),
    "PAUSE_CLEARED": set(),
}


def _metric(payload: str | None, name: str) -> float | None:
    if not payload:
        return None
    try:
        result = json.loads(payload)
        value = result.get("metrics", {}).get(name)
        if isinstance(value, dict):
            value = value.get("value")
        number = float(value) if value is not None else None
        return number if number is not None and math.isfinite(number) else None
    except (ValueError, TypeError, AttributeError):
        return None


def _hypothesis(experiment: dict[str, Any], metric_name: str) -> dict[str, Any]:
    try:
        raw = json.loads(experiment["hypothesis_json"]) if experiment["hypothesis_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    return {
        "experiment_id": experiment["id"],
        "ordinal": experiment["ordinal"],
        "title": raw.get("hypothesis") if isinstance(raw, dict) else None,
        "status": experiment["status"],
        "dev_score": _metric(experiment["evaluation_json"], metric_name),
        "candidate_snapshot": experiment["candidate_snapshot"],
    }


def read_snapshot(store: CampaignStore, campaign_id: str, *, stale_after_seconds: int = 30) -> dict[str, Any]:
    """Read one consistent SQLite revision; no validation or holdout rows escape."""
    if not campaign_id:
        raise StoreError("campaign id is required")
    store.conn.execute("SAVEPOINT operator_snapshot")
    try:
        campaign = store.campaign()
        spec = store.spec()
        experiments = store.experiments()
        composite_rows = [dict(row) for row in store.conn.execute(
            "SELECT * FROM composite_sequences ORDER BY created_at DESC LIMIT 10"
        )] if store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='composite_sequences'"
        ).fetchone() else []
        composites = []
        experiments_by_id = {int(row["id"]): row for row in experiments}
        for sequence in composite_rows:
            steps = []
            for step in store.conn.execute(
                "SELECT * FROM composite_steps WHERE composite_id=? ORDER BY step_order",
                (sequence["id"],),
            ):
                item = dict(step)
                experiment = experiments_by_id.get(int(item["experiment_id"]))
                try:
                    hypothesis = json.loads(experiment["hypothesis_json"] or "{}") if experiment else {}
                except (ValueError, TypeError):
                    hypothesis = {}
                steps.append({
                    "step_order": int(item["step_order"]),
                    "experiment_id": int(item["experiment_id"]),
                    "title": hypothesis.get("hypothesis") if isinstance(hypothesis, dict) else None,
                    "status": item["status"],
                    "dev_score": _metric(experiment["evaluation_json"], spec.primary_metric.name) if experiment else None,
                })
            composites.append({
                "id": sequence["id"],
                "status": sequence["status"],
                "base_snapshot": sequence["base_snapshot"],
                "tip_snapshot": sequence["tip_snapshot"],
                "current_step": int(sequence["current_step"]),
                "max_steps": int(sequence["max_steps"]),
                "steps": steps,
                "final_experiment_id": sequence["final_experiment_id"],
            })
        completed = store.conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE status='COMPLETED'"
        ).fetchone()[0]
        seq = store.conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        last_event_at = store.conn.execute("SELECT created_at FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        resources = store.resource_summary()
        pivot_row = store.conn.execute(
            "SELECT * FROM pivot_proposals ORDER BY created_at DESC LIMIT 1"
        ).fetchone() if store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pivot_proposals'"
        ).fetchone() else None
        pivot_review = None
        if pivot_row is not None:
            pivot_data = dict(pivot_row)
            try:
                proposal = PivotProposal.model_validate_json(pivot_data["proposal_json"])
                valid = bool(
                    proposal.proposal_id == pivot_data["proposal_id"]
                    and proposal.digest == pivot_data["digest"]
                    and proposal.spec_hash == campaign["spec_hash"]
                )
                proposal_payload = {
                    "proposal_id": proposal.proposal_id,
                    "digest": proposal.digest,
                    "spec_hash": proposal.spec_hash,
                    "trigger_experiment_ordinal": proposal.trigger_experiment_ordinal,
                    "current_architecture": proposal.current_architecture,
                    "proposed_architecture": proposal.proposed_architecture,
                    "rationale": proposal.rationale,
                    "expected_effect": proposal.expected_effect,
                    "changed_scope": list(proposal.changed_scope),
                    "resources": proposal.resources.model_dump(mode="json"),
                }
            except (ValueError, TypeError):
                valid = False
                proposal_payload = None
            decision_row = store.conn.execute(
                "SELECT request_id,decision,status,result_json,created_at FROM pivot_decisions "
                "WHERE proposal_id=? ORDER BY created_at DESC LIMIT 1",
                (pivot_data["proposal_id"],),
            ).fetchone()
            decision = dict(decision_row) if decision_row else None
            if decision and decision.get("result_json"):
                try:
                    decision["result"] = json.loads(decision.pop("result_json"))
                except (ValueError, TypeError):
                    decision["result"] = {"integrity_valid": False}
                    decision.pop("result_json", None)
            pivot_review = {
                "status": pivot_data["status"],
                "pause_reason": pivot_data["pause_reason"],
                "integrity_valid": valid,
                "proposal": proposal_payload,
                "decision": decision,
            }
        dev_rows = store.evaluations("dev")
        stage = campaign["stage"]
        if stage is None and campaign["active_attempt_id"] is not None:
            stage = store.attempt(campaign["active_attempt_id"])["stage"]
    finally:
        store.conn.execute("RELEASE SAVEPOINT operator_snapshot")

    metric_name = spec.primary_metric.name
    points = []
    for row in dev_rows:
        score = _metric(row["result_json"], metric_name)
        if score is not None:
            points.append({"evaluation_id": row["id"], "experiment_id": row["experiment_id"], "score": score})
    baseline = _metric(campaign["baseline_json"], metric_name)
    champion = _metric(campaign["champion_json"], metric_name)
    missing = {}
    if baseline is None:
        missing["baseline_score"] = "baseline DEV evaluation unavailable"
    if champion is None:
        missing["champion_score"] = "champion DEV evaluation unavailable"
    if stage is None:
        missing["display_stage"] = "no active engine stage"
    elif stage not in DISPLAY_STAGE:
        missing["display_stage"] = f"engine stage {stage} has no six-stage projection"
    updated = datetime.fromisoformat(campaign["updated_at"])
    if last_event_at is not None:
        updated = max(updated, datetime.fromisoformat(last_event_at[0]))
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "revision": int(seq),
        "goal": spec.goal,
        "status": campaign["status"],
        "stop_reason": campaign["stop_reason"],
        "engine_stage": stage,
        "display_stage": DISPLAY_STAGE.get(stage),
        "active_experiment_id": campaign["active_experiment_id"],
        "active_attempt_id": campaign["active_attempt_id"],
        "completed_epochs": int(completed),
        "attempt_count": campaign["attempt_count"],
        "experiment_count": campaign["experiment_count"],
        "metric": {"name": metric_name, "direction": spec.primary_metric.direction.value, "baseline": baseline, "champion": champion, "target": spec.primary_metric.target},
        "dev_points": points,
        "hypotheses": [_hypothesis(row, metric_name) for row in experiments],
        "composites": composites,
        "pivot_review": pivot_review,
        "budget": {"agent_calls": campaign["agent_calls"], "agent_call_limit": spec.budget.max_agent_calls, "evaluator_calls": campaign["evaluator_calls"], "evaluator_call_limit": spec.budget.max_evaluator_calls, "experiments": campaign["experiment_count"], "experiment_limit": spec.budget.max_experiments, "evaluation_units_reserved": resources["evaluation_units_reserved"], "evaluation_units_limit": resources["evaluation_units_limit"]},
        "updated_at": campaign["updated_at"],
        "as_of": datetime.now(timezone.utc).isoformat(),
        "stale": campaign["status"] == "RUNNING" and age > stale_after_seconds,
        "missing": missing,
    }


def read_events_after(store: CampaignStore, after_seq: int, *, limit: int = 100) -> dict[str, Any]:
    """Return a cursor page of committed events in order for reconnect."""
    if after_seq < 0 or not 1 <= limit <= 1000:
        raise ValueError("invalid event cursor or limit")
    store.conn.execute("SAVEPOINT operator_events")
    try:
        latest = int(store.conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
        rows = [dict(row) for row in store.conn.execute(
            "SELECT seq,event_type,payload_json,created_at FROM events WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?",
            (after_seq, latest, limit),
        )]
    finally:
        store.conn.execute("RELEASE SAVEPOINT operator_events")
    events = []
    for row in rows:
        raw = json.loads(row["payload_json"])
        allowed = SAFE_EVENT_FIELDS.get(row["event_type"], set())
        payload = {key: raw[key] for key in allowed if isinstance(raw, dict) and key in raw}
        events.append({"seq": row["seq"], "type": row["event_type"], "payload": payload, "created_at": row["created_at"]})
    cursor = events[-1]["seq"] if events else after_seq
    return {"events": events, "cursor": cursor, "latest_seq": latest, "has_more": cursor < latest}
