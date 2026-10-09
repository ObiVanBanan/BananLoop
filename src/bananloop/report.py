from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from typing import Any

from .contracts import CampaignSpec, EvaluationResult
from .evaluator_binding import EvaluationReceipt, canonical_hash, validate_receipt_linkage
from .policy import metric_value, target_met
from .replay import ReplayReceipt
from .research import attempt_bucket
from .store import CampaignStore


def build_report(store: CampaignStore) -> dict[str, Any]:
    campaign = store.campaign()
    spec = CampaignSpec.model_validate_json(campaign["spec_json"])
    evidence = spec.evaluation_evidence
    trust_level = evidence.trust_level if evidence is not None else "UNVERIFIED"
    baseline = EvaluationResult.model_validate_json(campaign["baseline_json"]) if campaign["baseline_json"] else None
    champion = EvaluationResult.model_validate_json(campaign["champion_json"]) if campaign["champion_json"] else None
    experiments: list[dict[str, Any]] = []
    actions_by_id = {row["id"]: row for row in store.actions()}
    selection_by_experiment = {
        payload.get("experiment_id"): payload
        for event in store.events()
        if event["event_type"] == "HYPOTHESIS_SELECTION"
        for payload in [json.loads(event["payload_json"])]
    }
    for row in store.experiments():
        accepted = row["status"] == "ACCEPTED"
        evaluation_action = store.action(f"exp:{row['id']}:evaluate")
        evaluation_request = (
            json.loads(evaluation_action["request_json"])
            if evaluation_action and evaluation_action.get("request_json")
            else {}
        )
        propose_action = actions_by_id.get(f"exp:{row['id']}:propose")
        propose_request = (
            json.loads(propose_action["request_json"])
            if propose_action and propose_action.get("request_json")
            else {}
        )
        hypothesis = json.loads(row["hypothesis_json"]) if row["hypothesis_json"] else None
        proof = evaluation_request.get("candidate_proof")
        decision = json.loads(row["decision_json"]) if row["decision_json"] else None
        trace_action_ids = sorted(action["id"] for action in actions_by_id.values() if action.get("experiment_id") == row["id"])
        selection_event = selection_by_experiment.get(row["id"], {})
        validation_decision = (decision or {}).get("validation") or {}
        if row["status"] == "REJECTED" and validation_decision:
            scientific_outcome = "DEV_ACCEPT_VALIDATION_REJECT"
        elif row["status"] == "NO_CHANGES" or row["failure_category"] == "NO_CHANGES":
            scientific_outcome = "NO_CHANGES"
        elif row["failure_category"] in {"INVALID_EVALUATION", "INVALID_VALIDATION", "EVALUATOR_CHANGED", "AGENT_PROTOCOL_ERROR", "CANDIDATE_NOT_MATERIALIZED", "PAIRED_INPUT_MISMATCH"}:
            scientific_outcome = "INFRA_FAILED"
        elif row["status"] == "CANCELLED":
            scientific_outcome = "BLOCKED"
        elif row["status"] == "ACCEPTED":
            scientific_outcome = (
                ("GOAL_MET" if trust_level == "INDEPENDENTLY_VALIDATED" else "UNVERIFIED_EVIDENCE")
                if campaign["status"] == "GOAL_MET" and row.get("candidate_snapshot") == campaign.get("champion_snapshot")
                else "DEV_ACCEPTED"
            )
        elif row["status"] == "REJECTED":
            scientific_outcome = "SCIENTIFIC_REJECT"
        else:
            scientific_outcome = row["failure_category"] or row["status"]
        causal_record = {
            "selection_reason": {
                "planning_mode": propose_request.get("planning_mode"),
                "research_state": propose_request.get("research_state"),
                "predecessor_experiments": (hypothesis or {}).get("prior_experiment_refs", []),
                "relation": (hypothesis or {}).get("relation", "new"),
                "mechanism_key": (hypothesis or {}).get("mechanism_key"),
                "rationale": selection_event.get("selection_rationale") or (hypothesis or {}).get("novelty_rationale"),
                "redundancy_check": selection_event.get("redundancy_check"),
            },
            "expected_change": (hypothesis or {}).get("expected_effect"),
            "actual_changed_paths": proof.get("changed_paths", []) if proof else [],
            "diff_signature": canonical_hash(sorted(proof.get("changed_paths", []))) if proof and proof.get("changed_paths") else None,
            "measurement": {
                "decision": decision,
                "metrics": (json.loads(row["evaluation_json"]).get("metrics", {}) if row["evaluation_json"] else {}),
                "checks": (json.loads(row["evaluation_json"]).get("checks", {}) if row["evaluation_json"] else {}),
            },
            "stage_rejection": {
                "category": row["failure_category"],
                "reason": row["lesson"],
                "validation_reasons": validation_decision.get("reasons", []),
            },
            "scientific_outcome": scientific_outcome,
            "next_hypothesis_rationale": (row.get("failure_analysis_json") and [capsule.get("suggested_regression") for capsule in json.loads(row["failure_analysis_json"]).get("capsules", []) if capsule.get("suggested_regression")]) or None,
            "observation": (
                "; ".join(capsule.get("observation", "") for capsule in (json.loads(row["failure_analysis_json"]).get("capsules", []) if row.get("failure_analysis_json") else []) if capsule.get("observation"))
                or row.get("failure_category")
                or ("DEV measurement recorded" if row.get("evaluation_json") else "No scientific measurement recorded")
            ),
            "suspected_cause": (
                [{"cause": capsule.get("likely_mechanism"), "confidence": capsule.get("confidence", 0.0)} for capsule in (json.loads(row["failure_analysis_json"]).get("capsules", []) if row.get("failure_analysis_json") else [])]
                or [{"cause": "Cause not established; no generalized failure analysis was recorded.", "confidence": 0.0}]
            ),
            "proposed_test": [capsule.get("suggested_regression") for capsule in (json.loads(row["failure_analysis_json"]).get("capsules", []) if row.get("failure_analysis_json") else []) if capsule.get("suggested_regression")],
            "evidence_ids": trace_action_ids,
        }
        item = {
            "id": row["id"],
            "ordinal": row["ordinal"],
            "status": row["status"],
            "hypothesis": hypothesis,
            "candidate_snapshot": row["candidate_snapshot"],
            "candidate_proof": evaluation_request.get("candidate_proof"),
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "failure_category": None if accepted else row["failure_category"],
            "lesson": "Promoted as new champion" if accepted else row["lesson"],
            "failure_analysis": (
                json.loads(row["failure_analysis_json"])
                if row.get("failure_analysis_json")
                else None
            ),
            "causal_record": causal_record,
            "trace_action_ids": trace_action_ids,
        }
        experiments.append(item)
    accounting_buckets = (
        "scientific_trials",
        "ungrounded",
        "redundant",
        "no_hypothesis",
        "no_changes",
        "implementation_blocked",
        "pre_evaluation_rejected",
        "infra_failures",
        "active",
        "cancelled",
        "other",
    )
    attempt_accounting = {"total_attempts": 0, **dict.fromkeys(accounting_buckets, 0)}
    for row in store.experiments():
        attempt_accounting[attempt_bucket(row)] += 1
        attempt_accounting["total_attempts"] += 1
    # The campaign counter is retained for old state files. For campaigns
    # created before attempt classification existed, it can include invalid
    # measurements that older versions incorrectly counted as science.
    attempt_accounting["durable_experiment_counter"] = campaign["experiment_count"]
    attempt_accounting["counter_matches_classification"] = (
        campaign["experiment_count"] == attempt_accounting["scientific_trials"]
    )
    baseline_value = metric_value(baseline, spec.primary_metric.name) if baseline else None
    final_value = metric_value(champion, spec.primary_metric.name) if champion else None
    validation_baseline = None
    validation_champion = None
    final_holdout = None
    if campaign["baseline_snapshot"]:
        row = store.latest_evaluation(campaign["baseline_snapshot"], "validation")
        if row is not None:
            validation_baseline = EvaluationResult.model_validate_json(row["result_json"])
    if campaign["champion_snapshot"]:
        row = store.latest_evaluation(campaign["champion_snapshot"], "validation")
        if row is not None:
            validation_champion = EvaluationResult.model_validate_json(row["result_json"])
        row = store.latest_evaluation(campaign["champion_snapshot"], "holdout")
        if row is not None:
            final_holdout = EvaluationResult.model_validate_json(row["result_json"])

    validation_baseline_value = (
        metric_value(validation_baseline, spec.primary_metric.name)
        if validation_baseline
        else None
    )
    validation_champion_value = (
        metric_value(validation_champion, spec.primary_metric.name)
        if validation_champion
        else None
    )
    holdout_value = (
        metric_value(final_holdout, spec.primary_metric.name)
        if final_holdout
        else None
    )

    if spec.holdout_command:
        if final_holdout is None:
            final_assessment = "PENDING"
        else:
            final_assessment = (
                "ACCEPTED" if target_met(spec, final_holdout) else "REJECTED"
            )
    else:
        final_assessment = "DEV_ONLY"
    # An unavailable signal check is an explicit lack of scorer-validity
    # evidence. It keeps even a target-reaching holdout at DEV_ONLY. None is
    # reserved for historical campaigns and retains their original semantics.
    if spec.signal_sanity_status == "UNAVAILABLE" or (
        spec.signal_sanity_status == "REFERENCE_VERIFIED"
        and spec.holdout_command
        and "holdout" not in (spec.evaluation_evidence.independently_validated_tiers if spec.evaluation_evidence else [])
    ):
        final_assessment = "DEV_ONLY"

    if trust_level == "INDEPENDENTLY_VALIDATED":
        if final_assessment == "ACCEPTED":
            scientific_assessment = "VALIDATED"
        elif final_assessment == "REJECTED":
            scientific_assessment = "REJECTED"
        elif final_assessment == "DEV_ONLY":
            scientific_assessment = "DEV_ONLY"
        else:
            scientific_assessment = "UNKNOWN"
    elif final_assessment == "DEV_ONLY":
        scientific_assessment = "DEV_ONLY"
    else:
        # A target-reaching technical outcome cannot establish business value
        # when the evaluator's cases/labels have no independent provenance.
        scientific_assessment = "UNKNOWN"

    resource_summary = store.resource_summary()
    action_rows = {row["id"]: row for row in store.actions()}
    stage_resources: dict[str, dict[str, Any]] = defaultdict(lambda: {"agent_calls": 0, "evaluator_calls": 0, "wall_time_seconds": 0.0, "unknown_outcomes": 0})
    for reservation in store.reservations():
        action = action_rows.get(reservation["action_id"])
        if action is None or reservation["resource"] not in {"agent_call", "evaluator_call"}:
            continue
        bucket = stage_resources[action["stage"]]
        count_key = "agent_calls" if reservation["resource"] == "agent_call" else "evaluator_calls"
        bucket[count_key] += 1
        bucket["wall_time_seconds"] += float(reservation.get("duration_ms") or 0.0) / 1000.0
        if reservation["status"] == "UNKNOWN":
            bucket["unknown_outcomes"] += 1
    for bucket in stage_resources.values():
        bucket["wall_time_seconds"] = round(bucket["wall_time_seconds"], 3)
    bootstrap_path = store.path.parent / "bootstrap-plan.json"
    bootstrap_record = json.loads(bootstrap_path.read_text(encoding="utf-8")) if bootstrap_path.is_file() else {}
    bootstrap_attempts = bootstrap_record.get("bootstrap_attempts", [])
    bootstrap_calls = len(bootstrap_attempts)
    bootstrap_readiness_runs = sum(len((attempt.get("readiness") or {}).get("runs", [])) for attempt in bootstrap_attempts)
    bootstrap_full_evaluations = sum(
        1 for attempt in bootstrap_attempts
        if (attempt.get("readiness") or {}).get("full_evaluation")
        or (attempt.get("readiness") or {}).get("full_evaluation_error")
    )
    replay_receipts = []
    replay_wall_time = 0.0
    for receipt_path in sorted((store.path.parent / "replays").glob("*/replay-receipt.json")):
        try:
            replay = json.loads(receipt_path.read_text(encoding="utf-8"))
            ReplayReceipt.model_validate(replay)
            replay_receipts.append({"status": replay.get("status"), "source_action_id": replay.get("source_action_id"), "receipt_sha256": replay.get("receipt_sha256"), "duration_ms": replay.get("duration_ms")})
            replay_wall_time += float(replay.get("duration_ms") or 0.0)
        except (OSError, ValueError):
            replay_receipts.append({"status": "INVALID", "receipt_sha256": None, "duration_ms": None})
    bootstrap_receipt_hash = bootstrap_record.get("bootstrap_receipt_sha256")
    bootstrap_receipt_body = dict(bootstrap_record)
    bootstrap_receipt_body.pop("bootstrap_receipt_sha256", None)
    bootstrap_receipt_valid = bool(bootstrap_receipt_hash and canonical_hash(bootstrap_receipt_body) == bootstrap_receipt_hash)
    bootstrap_event = next((event for event in store.events() if event["event_type"] == "CAMPAIGN_BOOTSTRAPPED"), None)
    if bootstrap_event and json.loads(bootstrap_event["payload_json"]).get("bootstrap_receipt_sha256") != bootstrap_receipt_hash:
        bootstrap_receipt_valid = False
    research_state = store.research_state()
    binding_row = store.evaluator_binding()
    receipt_by_action = {}
    for item in store.evaluation_receipts():
        receipt_by_action[item["action_id"]] = (item, item["receipt"])
    receipts = []
    for action in store.actions():
        if action["kind"] not in {"evaluator", "final_evaluator"}:
            continue
        row = receipt_by_action.get(action["id"])
        if row:
            item, receipt = row
            status = "ORPHANED"
            binding_id = receipt.get("binding_id")
            binding_hash = receipt.get("binding_hash")
            if action["status"] == "COMPLETED":
                try:
                    if binding_row is None:
                        raise ValueError("missing evaluator binding")
                    validated = validate_receipt_linkage(
                        receipt,
                        stored_receipt_hash=item["receipt_hash"],
                        action=action,
                        binding_data=json.loads(binding_row["binding_json"]),
                        stored_binding_hash=binding_row["binding_hash"],
                    )
                    sidecar = (
                        store.path.parent / "artifacts"
                        / hashlib.sha256(action["id"].encode()).hexdigest()[:24]
                        / "evaluation-receipt.json"
                    )
                    if sidecar.exists():
                        if EvaluationReceipt.model_validate_json(sidecar.read_text(encoding="utf-8")) != validated:
                            raise ValueError("receipt sidecar differs from SQLite")
                    status = "RECORDED"
                except Exception:
                    status = "INVALID"
            receipts.append({
                "action_id": action["id"],
                "status": status,
                "receipt_hash": item["receipt_hash"],
                "tier": receipt.get("tier"),
                "candidate_snapshot": receipt.get("candidate_snapshot"),
                "binding_id": binding_id,
                "binding_hash": binding_hash,
            })
        elif action["status"] == "COMPLETED":
            request = json.loads(action["request_json"] or "{}")
            receipts.append({
                "action_id": action["id"],
                "status": "LEGACY_UNRECEIPTED",
                "receipt_hash": None,
                "tier": request.get("tier", "dev"),
                "candidate_snapshot": request.get("snapshot"),
                "binding_id": None,
                "binding_hash": None,
            })
    campaign_scientific_outcome = (
        "UNVERIFIED_EVIDENCE"
        if campaign["status"] == "GOAL_MET" and trust_level != "INDEPENDENTLY_VALIDATED"
        else "GOAL_MET" if campaign["status"] == "GOAL_MET"
        else "BUDGET_EXHAUSTED" if campaign.get("stop_reason") in {"BUDGET_EXHAUSTED", "ATTEMPT_LIMIT", "EVALUATOR_CALL_LIMIT", "AGENT_CALL_LIMIT"}
        else "BLOCKED" if campaign["status"] == "PAUSED_NEEDS_INPUT" or campaign.get("stop_reason") in {"NO_HYPOTHESIS", "USER_STOPPED", "PAUSED_NEEDS_INPUT"}
        else "INFRA_FAILED" if campaign["status"] == "PAUSED_INFRA"
        else "SCIENTIFIC_REJECT" if campaign["status"] == "STOPPED" and campaign.get("stop_reason") in {"FINAL_HOLDOUT_REJECTED", "PLATEAU"}
        else "NO_EXPERIMENTS" if not experiments
        else campaign["status"]
    )
    return {
        "campaign_outcome": campaign["status"],
        "technical_outcome": campaign["status"],
        "scientific_outcome": campaign_scientific_outcome,
        "stop_reason": campaign["stop_reason"],
        "final_assessment": final_assessment,
        "scientific_assessment": scientific_assessment,
        "evaluator_evidence": (
            evidence.model_dump(mode="json")
            if evidence is not None
            else {
                "technical_readiness": "UNKNOWN",
                "trust_level": "UNVERIFIED",
                "case_source": None,
                "label_source": None,
                "protocol_sha256": None,
                "case_input_manifest_sha256": None,
                "runtime": {},
                "runtime_fingerprint": None,
                "metric_tolerances": {},
                "metric_relative_tolerances": {},
                "readiness_receipt_sha256": None,
                "verified_invariants": [],
                "limitations": ["legacy campaign has no persisted evaluator evidence"],
            }
        ),
        "goal": spec.goal,
        "primary_metric": spec.primary_metric.name,
        "baseline": baseline_value,
        "final_champion": final_value,
        "delta": None if baseline_value is None or final_value is None else final_value - baseline_value,
        "baseline_snapshot": campaign["baseline_snapshot"],
        "champion_snapshot": campaign["champion_snapshot"],
        "guardrails": [g.model_dump(mode="json") for g in spec.guardrails],
        "non_regression": [g.model_dump(mode="json") for g in spec.non_regression],
        "evaluation_protocol": {
            "dev": {
                "configured": True,
                "baseline": baseline_value,
                "champion": final_value,
            },
            "validation": {
                "configured": bool(spec.validation_command),
                "baseline": validation_baseline_value,
                "champion": validation_champion_value,
                "delta": (
                    None
                    if validation_baseline_value is None or validation_champion_value is None
                    else validation_champion_value - validation_baseline_value
                ),
            },
            "holdout": {
                "configured": bool(spec.holdout_command),
                "evaluated": final_holdout is not None,
                "snapshot": campaign["champion_snapshot"] if final_holdout else None,
                "value": holdout_value,
                "target_met": target_met(spec, final_holdout) if final_holdout else None,
            },
        },
        "paired_comparison": spec.paired_comparison.model_dump(mode="json") if spec.paired_comparison else None,
        "signal_sanity": {
            "status": spec.signal_sanity_status,
            "receipt_sha256": spec.signal_sanity_receipt_sha256,
            "evidence_level": (
                "diagnostic_only_unverified_labels" if spec.signal_sanity_status == "DIAGNOSTIC_ONLY"
                else "unavailable" if spec.signal_sanity_status == "UNAVAILABLE"
                else "operator_reference_manifest" if spec.signal_sanity_status == "REFERENCE_VERIFIED"
                else None
            ),
            **({
                "reference_manifest_sha256": spec.evaluation_evidence.reference_manifest_sha256,
                "reference_approved_by": spec.evaluation_evidence.reference_approved_by,
                "reference_receipt_sha256": spec.evaluation_evidence.reference_receipt_sha256,
                "validated_tiers": spec.evaluation_evidence.independently_validated_tiers,
            } if spec.signal_sanity_status == "REFERENCE_VERIFIED" and spec.evaluation_evidence else {}),
        },
        "measurements": [
            {"experiment_id": row["experiment_id"], "candidate_snapshot": row["candidate_snapshot"],
             "tier": row["tier"], "result": json.loads(row["result_json"])}
            for row in store.evaluations()
        ],
        "experiments": experiments,
        "attempt_accounting": attempt_accounting,
        "evaluator_binding": (
            {"binding_id": binding_row["binding_id"], "binding_hash": binding_row["binding_hash"]}
            if binding_row else None
        ),
        "evaluation_receipts": receipts,
        "research_state": research_state,
        "resources": {
            "agent_calls": campaign["agent_calls"],
            "evaluation_calls": campaign["evaluator_calls"],
            "experiments": campaign["experiment_count"],
            "attempts": campaign["attempt_count"],
            **resource_summary,
            "limits": {
                "max_agent_calls": spec.budget.max_agent_calls,
                "max_evaluator_calls": spec.budget.max_evaluator_calls,
                "max_experiments": spec.budget.max_experiments,
                "max_attempts": spec.budget.max_attempts,
                "deadline_seconds": spec.budget.deadline_seconds,
                "bootstrap_max_attempts": 3,
                "replay_calls": "separately counted; do not consume campaign budget",
            },
            "by_stage": dict(sorted(stage_resources.items())),
            "bootstrap": {
                "receipt_sha256": bootstrap_receipt_hash,
                "receipt_status": "RECORDED" if bootstrap_receipt_valid else "MISSING_OR_INVALID" if bootstrap_attempts else "NOT_APPLICABLE",
                "agent_calls": bootstrap_calls,
                "repairs": max(0, bootstrap_calls - 1),
                "readiness_runs": bootstrap_readiness_runs,
                "readiness_full_evaluations": bootstrap_full_evaluations,
                "readiness_evaluator_calls": bootstrap_readiness_runs + bootstrap_full_evaluations,
                "duration_seconds": round(sum(float(item.get("duration_ms") or 0.0) for item in bootstrap_attempts) / 1000.0, 3),
                "readiness_duration_seconds": round(sum(float(item.get("readiness_duration_ms") or 0.0) for item in bootstrap_attempts) / 1000.0, 3),
                "unknown_duration_calls": sum(1 for item in bootstrap_attempts if item.get("duration_ms") is None),
            },
            "replay": {
                "calls": len(replay_receipts),
                "wall_time_seconds": round(replay_wall_time / 1000.0, 3),
                "receipts": replay_receipts,
            },
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BananLoop Campaign Report",
        "",
        f"- Outcome: **{report['campaign_outcome']}**",
        f"- Stop reason: **{report['stop_reason'] or '-'}**",
        f"- Final assessment: **{report['final_assessment']}**",
        f"- Scientific assessment: **{report['scientific_assessment']}**",
        f"- Scientific outcome: **{report['scientific_outcome']}**",
        f"- Evaluator trust: **{report['evaluator_evidence']['trust_level']}**; technical readiness: **{report['evaluator_evidence']['technical_readiness']}**",
        f"- Goal: {report['goal']}",
        f"- Primary metric: `{report['primary_metric']}`",
        f"- Baseline → final: `{report['baseline']}` → `{report['final_champion']}` (delta `{report['delta']}`)",
        f"- Baseline snapshot: `{report['baseline_snapshot']}`",
        f"- Champion snapshot: `{report['champion_snapshot']}`",
        "",
        "## Evaluation protocol",
        "",
    ]
    protocol = report["evaluation_protocol"]
    validation = protocol["validation"]
    holdout = protocol["holdout"]
    lines += [
        f"- Dev: `{protocol['dev']['baseline']}` → `{protocol['dev']['champion']}`",
        (
            f"- Validation: `{validation['baseline']}` → `{validation['champion']}` "
            f"(delta `{validation['delta']}`)"
            if validation["configured"]
            else "- Validation: not configured"
        ),
        (
            f"- Final holdout: `{holdout['value']}`; target_met=`{holdout['target_met']}`; "
            f"snapshot=`{holdout['snapshot']}`"
            if holdout["evaluated"]
            else (
                "- Final holdout: configured but not evaluated"
                if holdout["configured"]
                else "- Final holdout: not configured"
            )
        ),
        "",
    ]
    lines += ["", "## Decision summary", ""]
    accounting = report.get("attempt_accounting", {})
    resources = report.get("resources", {})
    lines.append(
        f"- Attempts: {accounting.get('total_attempts', 0)} total; "
        f"{accounting.get('scientific_trials', 0)} scientific; "
        f"{accounting.get('infra_failures', 0)} infrastructure failures; "
        f"bootstrap calls {resources.get('bootstrap', {}).get('agent_calls', 0)}; "
        f"replays {resources.get('replay', {}).get('calls', 0)}."
    )
    for exp in report.get("experiments", [])[-5:]:
        hypothesis = exp.get("hypothesis") or {}
        causal = exp.get("causal_record") or {}
        lines.append(
            f"- #{exp['ordinal']} {causal.get('scientific_outcome', exp['status'])}: "
            f"{hypothesis.get('hypothesis', 'no hypothesis')}; "
            f"expected: {causal.get('expected_change') or '-'}; "
            f"changed: {', '.join(causal.get('actual_changed_paths') or []) or '-'}; "
            f"reason: {causal.get('stage_rejection', {}).get('reason') or '-'}; "
            f"trace: {', '.join(exp.get('trace_action_ids') or []) or '-'}"
        )
    lines.append(
        "- Not proven: "
        + ("; ".join(report["evaluator_evidence"].get("limitations", [])) or "none recorded")
    )
    signal = report.get("signal_sanity", {})
    lines += [
        "",
        "## Evaluator signal sanity",
        "",
        f"- Status: `{signal.get('status') or 'legacy/unspecified'}`; evidence level: `{signal.get('evidence_level') or 'legacy/unspecified'}`",
        f"- Readiness receipt SHA-256: `{signal.get('receipt_sha256') or '-'}`",
        "",
        "## Experiments",
        "",
    ]
    if not report["experiments"]:
        lines.append("No experiments recorded.")
    for exp in report["experiments"]:
        hypothesis = (exp["hypothesis"] or {}).get("hypothesis", "-")
        decision = exp["decision"] or {}
        causal = exp.get("causal_record", {})
        lines.append(f"- #{exp['ordinal']} **{exp['status']}** ({causal.get('scientific_outcome', 'UNKNOWN')}) — {hypothesis}; delta={decision.get('delta', '-')}; failure={exp['failure_category'] or '-'}")
        if causal.get("expected_change"):
            lines.append(f"  - Expected change: {causal['expected_change']}")
        if causal.get("actual_changed_paths"):
            lines.append(f"  - Changed paths: {', '.join(causal['actual_changed_paths'])}")
        proof = exp.get("candidate_proof")
        if proof:
            lines.append(
                f"  - Candidate proof: tree=`{proof['tree']}`; diff=`{proof['diff_sha256']}`; "
                f"paths=`{json.dumps(proof['changed_paths'])}`"
            )
    binding = report.get("evaluator_binding")
    lines += ["", "## Evaluator provenance", ""]
    if binding:
        lines.append(f"- Binding: `{binding['binding_id']}`; hash=`{binding['binding_hash']}`")
    else:
        lines.append("- Binding: not recorded")
    for receipt in report.get("evaluation_receipts", []):
        lines.append(
            f"- {receipt['action_id']} ({receipt['tier']}): {receipt['status']}; "
            f"receipt=`{receipt['receipt_hash'] or '-'}`; binding=`{receipt['binding_id'] or '-'}`"
        )
    accounting = report.get("attempt_accounting")
    if accounting:
        lines += [
            "",
            "## Attempt accounting",
            "",
            f"- Total attempts: {accounting['total_attempts']}",
            f"- Scientific trials: {accounting['scientific_trials']}",
            f"- Stored experiment counter: {accounting['durable_experiment_counter']} (matches classified rows: {accounting['counter_matches_classification']})",
            f"- Ungrounded / redundant / no hypothesis: {accounting['ungrounded']} / {accounting['redundant']} / {accounting['no_hypothesis']}",
            f"- No changes / implementation blocked: {accounting['no_changes']} / {accounting['implementation_blocked']}",
            f"- Pre-evaluation rejects / infrastructure failures: {accounting['pre_evaluation_rejected']} / {accounting['infra_failures']}",
            f"- Active / cancelled / other: {accounting['active']} / {accounting['cancelled']} / {accounting['other']}",
        ]
    lines += ["", "## Measurements (operator-only)", ""]
    for evaluation in report.get("measurements", []):
        result = evaluation["result"]
        lines += [f"### {evaluation['tier']} / experiment {evaluation['experiment_id']} / {evaluation['candidate_snapshot']}", "",
                  f"Aggregate: `{json.dumps(result['metrics'], sort_keys=True)}`; reported units: `{result.get('resource_units')}`", "",
                  "| Case | Repetition | Input SHA-256 | Measurements |", "|---|---:|---|---|"]
        for case in result.get("cases", []):
            lines.append(f"| {case['case_id']} | {case['repetition']} | {case['input_sha256']} | {json.dumps(case['metrics'], sort_keys=True)} |")
        for artifact in result.get("artifact_manifest", []):
            lines.append(f"- Artifact ({artifact['case_id']}): `{artifact['path']}`; SHA-256 `{artifact['sha256']}`")
    research = report.get("research_state")
    if research:
        lines += [
            "",
            "## Research state",
            "",
            f"- Planning mode: `{research['planning_mode']}`",
            f"- DEV non-improve streak: {research['dev_non_improve_streak']}",
            f"- Failure classes: `{json.dumps(research['failure_counts'], sort_keys=True)}`",
            f"- Remaining budget: `{json.dumps(research['remaining_budget'], sort_keys=True)}`",
        ]
        if research.get("recent_lessons"):
            lines.append("- Recent lessons:")
            lines.extend(f"  - {lesson}" for lesson in research["recent_lessons"])
        if research.get("recent_failure_capsules"):
            lines.append("- Recent DEV failure capsules:")
            for capsule in research["recent_failure_capsules"]:
                lines.append(
                    f"  - [{capsule['failure_class']}] {capsule['title']}: "
                    f"{capsule['general_rule']}"
                )

    r = report["resources"]
    lines += [
        "",
        "## Resources",
        "",
        f"- Agent calls: {r['agent_calls']}",
        f"- Evaluation calls: {r['evaluation_calls']}",
        f"- Evaluation units: reserved {r['evaluation_units_reserved']} / {r['evaluation_units_limit']}; reported {r['evaluation_units_reported']}",
        f"- Scientific experiments: {r['experiments']}",
        f"- Attempts: {r['attempts']}",
        f"- Action wall time: {r['wall_time_seconds']:.3f} s",
        f"- Resource reservations: {r['reservation_count']} ({r['open_reservations']} open)",
        f"- Unknown action outcomes: {r['unknown_outcomes']}",
        f"- Bootstrap agent calls / repairs / smoke runs / full preflights / evaluator calls / agent time / readiness time: {r['bootstrap']['agent_calls']} / {r['bootstrap']['repairs']} / {r['bootstrap']['readiness_runs']} / {r['bootstrap']['readiness_full_evaluations']} / {r['bootstrap']['readiness_evaluator_calls']} / {r['bootstrap']['duration_seconds']:.3f} s / {r['bootstrap']['readiness_duration_seconds']:.3f} s",
        f"- Replay calls / time: {r['replay']['calls']} / {r['replay']['wall_time_seconds']:.3f} s",
        "",
    ]
    return "\n".join(lines)

