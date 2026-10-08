from __future__ import annotations

import json
import hashlib
from typing import Any

from .contracts import CampaignSpec, EvaluationResult
from .evaluator_binding import EvaluationReceipt, canonical_hash, validate_receipt_linkage
from .policy import metric_value, target_met
from .research import attempt_bucket
from .store import CampaignStore


def build_report(store: CampaignStore) -> dict[str, Any]:
    campaign = store.campaign()
    spec = CampaignSpec.model_validate_json(campaign["spec_json"])
    baseline = EvaluationResult.model_validate_json(campaign["baseline_json"]) if campaign["baseline_json"] else None
    champion = EvaluationResult.model_validate_json(campaign["champion_json"]) if campaign["champion_json"] else None
    experiments: list[dict[str, Any]] = []
    for row in store.experiments():
        accepted = row["status"] == "ACCEPTED"
        evaluation_action = store.action(f"exp:{row['id']}:evaluate")
        evaluation_request = (
            json.loads(evaluation_action["request_json"])
            if evaluation_action and evaluation_action.get("request_json")
            else {}
        )
        item = {
            "id": row["id"],
            "ordinal": row["ordinal"],
            "status": row["status"],
            "hypothesis": json.loads(row["hypothesis_json"]) if row["hypothesis_json"] else None,
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
    protocol_manifests = {
        tier: {
            "protocol_sha256": manifest.protocol_sha256,
            "manifest_sha256": canonical_hash(manifest.model_dump(mode="json")),
            "case_count": len(manifest.cases),
            "path": str(store.path.parent / "protocol-manifests" / f"{tier}.json"),
        }
        for tier, manifest in spec.protocol_manifests.items()
    }
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
                "ACCEPTED" if target_met(spec, final_holdout, tier="holdout") else "REJECTED"
            )
    else:
        final_assessment = "DEV_ONLY"
    # An unavailable signal check is an explicit lack of scorer-validity
    # evidence. It keeps even a target-reaching holdout at DEV_ONLY. None is
    # reserved for historical campaigns and retains their original semantics.
    if spec.signal_sanity_status == "UNAVAILABLE":
        final_assessment = "DEV_ONLY"

    resource_summary = store.resource_summary()
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
    return {
        "campaign_outcome": campaign["status"],
        "stop_reason": campaign["stop_reason"],
        "final_assessment": final_assessment,
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
            "frozen_manifests": protocol_manifests,
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
                "target_met": target_met(spec, final_holdout, tier="holdout") if final_holdout else None,
            },
        },
        "paired_comparison": spec.paired_comparison.model_dump(mode="json") if spec.paired_comparison else None,
        "signal_sanity": {
            "status": spec.signal_sanity_status,
            "receipt_sha256": spec.signal_sanity_receipt_sha256,
            "evidence_level": (
                "diagnostic_only_unverified_labels" if spec.signal_sanity_status == "DIAGNOSTIC_ONLY"
                else "unavailable" if spec.signal_sanity_status == "UNAVAILABLE"
                else None
            ),
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
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BananLoop Campaign Report",
        "",
        f"- Outcome: **{report['campaign_outcome']}**",
        f"- Stop reason: **{report['stop_reason'] or '-'}**",
        f"- Final assessment: **{report['final_assessment']}**",
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
        "## Experiments",
        "",
    ]
    signal = report.get("signal_sanity", {})
    lines += [
        "",
        "## Evaluator signal sanity",
        "",
        f"- Status: `{signal.get('status') or 'legacy/unspecified'}`; evidence level: `{signal.get('evidence_level') or 'legacy/unspecified'}`",
        f"- Readiness receipt SHA-256: `{signal.get('receipt_sha256') or '-'}`",
    ]
    if not report["experiments"]:
        lines.append("No experiments recorded.")
    for exp in report["experiments"]:
        hypothesis = (exp["hypothesis"] or {}).get("hypothesis", "-")
        decision = exp["decision"] or {}
        lines.append(f"- #{exp['ordinal']} **{exp['status']}** — {hypothesis}; delta={decision.get('delta', '-')}; failure={exp['failure_category'] or '-'}")
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
        "",
    ]
    return "\n".join(lines)
