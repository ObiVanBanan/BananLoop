from __future__ import annotations

import json
import hashlib
from typing import Any

from .contracts import CampaignSpec, DecisionReceipt, EvaluationResult
from .evaluator_binding import EvaluationReceipt, canonical_hash, validate_receipt_linkage
from .failures import classify_failure
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
            "failure_classification": None if accepted else classify_failure(
                row["failure_category"]
                or next(
                    (reason for reason in (json.loads(row["decision_json"] or "{}").get("reasons") or []) if reason != "ACCEPTED"),
                    None,
                )
            ),
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
    champion_was_promoted = (
        bool(baseline and champion)
        and campaign["baseline_snapshot"] != campaign["champion_snapshot"]
        and any(item["status"] == "ACCEPTED" for item in experiments)
    )
    if baseline is None:
        improvement_status = "NOT_EVALUATED"
    elif champion_was_promoted:
        improvement_status = "CHAMPION_IMPROVED"
    elif target_met(spec, baseline):
        improvement_status = "BASELINE_ALREADY_MET_TARGET"
    else:
        improvement_status = "NO_VALIDATED_IMPROVEMENT"
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
    call_stage_labels = {
        "PROPOSE": "planning", "ANALYZE": "analyze", "EXECUTE": "implement",
        "EVALUATE": "evaluate", "VALIDATE": "evaluate", "FINAL_HOLDOUT": "evaluate",
        "BASELINE": "evaluate", "BOOTSTRAP": "planning",
    }
    action_stages = {
        row["id"]: row["stage"]
        for row in store.conn.execute("SELECT id,stage FROM actions")
    }
    calls_by_stage: dict[str, dict[str, int]] = {
        name: {"agent_calls": 0, "evaluator_calls": 0, "unknown_outcomes": 0}
        for name in ("planning", "analyze", "implement", "evaluate", "other")
    }
    for reservation in store.reservations():
        if reservation["resource"] not in {"agent_call", "evaluator_call"}:
            continue
        label = call_stage_labels.get(action_stages.get(reservation["action_id"], ""), "other")
        resource_name = "agent_calls" if reservation["resource"] == "agent_call" else "evaluator_calls"
        calls_by_stage[label][resource_name] += int(float(reservation["amount"]))
        if reservation["status"] == "UNKNOWN":
            calls_by_stage[label]["unknown_outcomes"] += 1
    resource_summary["calls_by_stage"] = calls_by_stage
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
    decision_receipts = []
    verified_evaluation_receipts = {
        (item["action_id"], item["receipt_hash"]): item
        for item in receipts
        if item["status"] == "RECORDED" and item.get("receipt_hash")
    }
    for stored in store.decision_receipts():
        receipt = stored["receipt"]
        body = dict(receipt)
        identity = body.pop("receipt_sha256", None)
        status = "VERIFIED"
        try:
            DecisionReceipt.model_validate(receipt)
            if identity != stored["receipt_hash"] or canonical_hash(body) != identity:
                raise ValueError("decision receipt hash mismatch")
            if receipt.get("experiment_id") != stored["experiment_id"] or receipt.get("tier") != stored["tier"]:
                raise ValueError("decision receipt row linkage mismatch")
            if receipt.get("spec_sha256") != campaign["spec_hash"]:
                raise ValueError("campaign spec linkage mismatch")
            if receipt.get("candidate_snapshot") != stored["candidate_snapshot"]:
                raise ValueError("candidate snapshot linkage mismatch")
            binding_hash = binding_row["binding_hash"] if binding_row else None
            if receipt.get("evaluator_binding_sha256") != binding_hash:
                raise ValueError("evaluator binding linkage mismatch")
            for ref in receipt.get("evaluator_receipts", []):
                item = verified_evaluation_receipts.get((ref["action_id"], ref["receipt_sha256"]))
                if not item or item.get("tier") != ref.get("tier") or item.get("candidate_snapshot") != receipt.get("candidate_snapshot"):
                    raise ValueError("evaluator receipt linkage mismatch")
        except (KeyError, TypeError, ValueError):
            status = "INVALID"
        decision_receipts.append({
            "experiment_id": stored["experiment_id"],
            "tier": stored["tier"],
            "candidate_snapshot": stored["candidate_snapshot"],
            "receipt_hash": stored["receipt_hash"],
            "status": status,
            "receipt": receipt,
        })
    recorded_experiment_decisions = {
        (item["experiment_id"], item["candidate_snapshot"])
        for item in decision_receipts
        if item["experiment_id"] is not None
    }
    for row in store.experiments():
        if row["decision_json"] and (row["id"], row["candidate_snapshot"]) not in recorded_experiment_decisions:
            decision_receipts.append({
                "experiment_id": row["id"],
                "tier": "unknown",
                "candidate_snapshot": row["candidate_snapshot"],
                "receipt_hash": None,
                "status": "LEGACY_UNRECEIPTED",
                "receipt": None,
            })
    decision_receipts.sort(key=lambda item: (item["experiment_id"] or 0, item["tier"]))
    operator_summary = _operator_summary(
        campaign=campaign,
        spec=spec,
        experiments=experiments,
        measurements=store.evaluations(),
        baseline_value=baseline_value,
        final_value=final_value,
        improvement_status=improvement_status,
        final_assessment=final_assessment,
        decision_receipts=decision_receipts,
        evaluation_receipts=receipts,
        resources=resource_summary,
    )
    return {
        "campaign_outcome": campaign["status"],
        "improvement_status": improvement_status,
        "champion_was_promoted": champion_was_promoted,
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
        "decision_receipts": decision_receipts,
        "operator_summary": operator_summary,
        "research_state": research_state,
        "resources": {
            "agent_calls": campaign["agent_calls"],
            "evaluation_calls": campaign["evaluator_calls"],
            "experiments": campaign["experiment_count"],
            "attempts": campaign["attempt_count"],
            **resource_summary,
        },
    }


def _operator_summary(
    *, campaign: dict[str, Any], spec: CampaignSpec, experiments: list[dict[str, Any]],
    measurements: list[dict[str, Any]], baseline_value: float | None,
    final_value: float | None, improvement_status: str, final_assessment: str,
    decision_receipts: list[dict[str, Any]], evaluation_receipts: list[dict[str, Any]],
    resources: dict[str, Any],
) -> dict[str, Any]:
    unknowns: list[str] = []
    if not spec.validation_command:
        unknowns.append("Validation tier is not configured.")
    if not spec.holdout_command:
        unknowns.append("Final holdout is not configured.")
    elif final_assessment == "DEV_ONLY":
        unknowns.append("Final holdout does not support a scientific conclusion under the current signal-sanity status.")
    if spec.signal_sanity_status in {"UNAVAILABLE", "DIAGNOSTIC_ONLY"}:
        unknowns.append(f"Evaluator signal sanity is {spec.signal_sanity_status.lower()}.")
    if resources.get("unknown_outcomes", 0):
        unknowns.append(f"{resources['unknown_outcomes']} action outcome(s) remain unknown.")
    invalid = sum(item["status"] == "INVALID" for item in decision_receipts)
    legacy = sum(item["status"] == "LEGACY_UNRECEIPTED" for item in decision_receipts)
    invalid_evaluations = sum(item["status"] == "INVALID" for item in evaluation_receipts)
    legacy_evaluations = sum(item["status"] == "LEGACY_UNRECEIPTED" for item in evaluation_receipts)
    if invalid:
        unknowns.append(f"{invalid} decision receipt(s) failed integrity or linkage checks.")
    if legacy:
        unknowns.append(f"{legacy} historical decision(s) have no decision receipt.")
    if invalid_evaluations:
        unknowns.append(f"{invalid_evaluations} evaluator receipt(s) failed integrity or linkage checks.")
    if legacy_evaluations:
        unknowns.append(f"{legacy_evaluations} historical evaluator action(s) have no evaluation receipt.")
    why = []
    for exp in experiments:
        decision = exp.get("decision") or {}
        why.append({
            "experiment_id": exp["id"],
            "verdict": "ACCEPT" if decision.get("accepted") else "REJECT",
            "predicates": decision.get("predicates", {}),
            "reasons": decision.get("reasons", []),
            "failure_category": exp.get("failure_category"),
            "failure_kind": (exp.get("failure_classification") or {}).get("kind"),
            "retryable": (exp.get("failure_classification") or {}).get("retryable"),
            "suggested_remediation": (exp.get("failure_classification") or {}).get("suggested_remediation"),
        })
    for item in decision_receipts:
        if item["tier"] != "holdout" or not item.get("receipt"):
            continue
        receipt = item["receipt"]
        why.append({
            "experiment_id": None,
            "verdict": receipt.get("verdict", "UNKNOWN"),
            "predicates": receipt.get("predicate_results", {}),
            "reasons": receipt.get("reasons", []),
            "failure_category": None,
            "failure_kind": None,
            "retryable": None,
            "suggested_remediation": None,
        })
    tiers = sorted({row.get("tier") for row in measurements})
    if campaign["status"] in {"PAUSED_INFRA", "FAILED"}:
        next_action = "Resolve the recorded operational incident, then resume the campaign from its durable checkpoint."
    elif improvement_status == "IMPROVED":
        next_action = "Review the validated candidate and its receipts; any merge or deployment decision remains an explicit operator action."
    elif campaign["status"] in {"GOAL_MET", "STOPPED"}:
        next_action = "Review the terminal evidence and choose whether to stop or approve a materially different experiment."
    else:
        next_action = "Inspect the current checkpoint and remaining budget, then continue or stop the campaign explicitly."
    return {
        "what_improved": {
            "status": improvement_status,
            "metric": spec.primary_metric.name,
            "baseline": baseline_value,
            "final_champion": final_value,
            "delta": None if baseline_value is None or final_value is None else final_value - baseline_value,
            "champion_was_promoted": campaign["champion_snapshot"] != campaign["baseline_snapshot"],
        },
        "what_checked": {
            "tiers_configured": ["dev"] + (["validation"] if spec.validation_command else []) + (["holdout"] if spec.holdout_command else []),
            "guardrails": [item.model_dump(mode="json") for item in spec.guardrails],
            "non_regression": [item.model_dump(mode="json") for item in spec.non_regression],
            "mandatory_checks": list(spec.mandatory_checks),
            "signal_sanity_status": spec.signal_sanity_status,
            "decision_receipts": {status: sum(item["status"] == status for item in decision_receipts) for status in ("VERIFIED", "INVALID", "LEGACY_UNRECEIPTED")},
            "failure_kinds": sorted({
                item["failure_classification"]["kind"]
                for item in experiments if item.get("failure_classification")
            }),
        },
        "what_measured": {
            "tiers_present": tiers,
            "measurement_count": len(measurements),
            "experiment_count": len(experiments),
            "aggregate_measurements": [
                {
                    "experiment_id": row.get("experiment_id"),
                    "tier": row.get("tier"),
                    "candidate_snapshot": row.get("candidate_snapshot"),
                    "metrics": {
                        name: metric.get("value")
                        for name, metric in sorted((json.loads(row["result_json"]).get("metrics") or {}).items())
                        if isinstance(metric, dict)
                    },
                    "resource_units": json.loads(row["result_json"]).get("resource_units"),
                }
                for row in measurements
            ],
        },
        "why_accept_reject": why,
        "unknowns": unknowns,
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BananLoop Campaign Report",
        "",
        f"- Outcome: **{report['campaign_outcome']}**",
        f"- Actual improvement: **{report['improvement_status']}** (promoted candidate: {report['champion_was_promoted']})",
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
        classification = exp.get("failure_classification")
        if classification:
            lines.append(
                f"  - Failure kind: `{classification['kind']}`; retryable=`{classification['retryable']}`; "
                f"risk=`{classification['risk']}`; next step: {classification['suggested_remediation']}"
            )
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
    lines += ["", "## Decision receipts", ""]
    for item in report.get("decision_receipts", []):
        receipt = item.get("receipt") or {}
        lines.append(
            f"- Experiment `{item['experiment_id'] if item['experiment_id'] is not None else 'final'}` "
            f"({item['tier']}): **{item['status']}**; verdict=`{receipt.get('verdict', '-')}`; "
            f"receipt=`{item.get('receipt_hash') or '-'}`; snapshot=`{item.get('candidate_snapshot') or '-'}`"
        )
    summary = report.get("operator_summary") or {}
    lines += ["", "## Operator summary", ""]
    improved = summary.get("what_improved", {})
    lines.append(
        f"- What improved: `{improved.get('status', 'unknown')}`; metric `{improved.get('metric', '-')}`; "
        f"baseline `{improved.get('baseline')}` → champion `{improved.get('final_champion')}`; "
        f"delta `{improved.get('delta')}`; promoted=`{improved.get('champion_was_promoted')}`"
    )
    checked = summary.get("what_checked", {})
    lines.append(
        f"- What checked: tiers `{', '.join(checked.get('tiers_configured', []))}`; "
        f"mandatory checks `{json.dumps(checked.get('mandatory_checks', []), ensure_ascii=False)}`; "
        f"signal sanity `{checked.get('signal_sanity_status') or 'unknown'}`; "
        f"decision receipts `{json.dumps(checked.get('decision_receipts', {}), sort_keys=True)}`; "
        f"failure kinds `{', '.join(checked.get('failure_kinds', [])) or 'none recorded'}`"
    )
    measured = summary.get("what_measured", {})
    lines.append(
        f"- What measured: tiers `{', '.join(measured.get('tiers_present', []))}`; "
        f"{measured.get('measurement_count', 0)} measurements across {measured.get('experiment_count', 0)} experiments; "
        f"aggregates `{json.dumps(measured.get('aggregate_measurements', []), sort_keys=True)}`"
    )
    lines.append("- Why accept/reject:")
    for item in summary.get("why_accept_reject", []):
        lines.append(
            f"  - Experiment `{item['experiment_id']}`: `{item['verdict']}`; "
            f"predicates `{json.dumps(item['predicates'], sort_keys=True)}`; "
            f"reasons `{json.dumps(item['reasons'], ensure_ascii=False)}`; "
            f"failure `{item.get('failure_category') or '-'}`"
            + f"; type `{item.get('failure_kind') or '-'}`; retryable=`{item.get('retryable')}`"
        )
    unknowns = summary.get("unknowns", [])
    lines.append("- Unknowns: " + ("; ".join(unknowns) if unknowns else "none recorded by the current campaign contract"))
    lines.append(f"- Next action: {summary.get('next_action', 'Review the durable campaign checkpoint.')}")
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
        if research.get("stagnant_mechanisms"):
            lines.append(
                "- Repeated identical DEV mechanisms (reframe required): "
                + ", ".join(f"`{key}`" for key in research["stagnant_mechanisms"])
            )
        if research.get("failed_dev_families"):
            lines.append(
                "- DEV families with non-improving results (avoid during reframe): "
                + ", ".join(f"`{key}`" for key in research["failed_dev_families"])
            )
        if research.get("recent_lessons"):
            lines.append("- Recent lessons:")
            lines.extend(f"  - {lesson}" for lesson in research["recent_lessons"])
        if research.get("recent_failure_capsules"):
            lines.append("- Recent DEV failure capsules:")
            for capsule in research["recent_failure_capsules"]:
                lines.append(
                    f"  - [{capsule['failure_class']}] {capsule['title']}: "
                    f"observed={capsule['observation']}; "
                    f"inferred mechanism={capsule['likely_mechanism']}; "
                    f"rule={capsule['general_rule']}; "
                    f"next DEV test={capsule['suggested_regression']}; "
                    f"confidence={capsule['confidence']}"
                )
        if research.get("portfolios"):
            lines += ["", "### Planning portfolios", ""]
            for portfolio in research["portfolios"]:
                lines.append(
                    f"- Experiment {portfolio['experiment_ordinal']} ({portfolio['strategy']}), "
                    f"selected `{portfolio.get('selected_candidate_id') or 'none'}`; "
                    f"trigger: {'; '.join(portfolio.get('trigger_evidence', []))}"
                )
                for candidate, decision in zip(portfolio["candidates"], portfolio["decisions"]):
                    status = "selected" if candidate["candidate_id"] == portfolio.get("selected_candidate_id") else (
                        "admissible" if decision["admissible"] else "rejected"
                    )
                    lines.append(
                        f"  - `{candidate['candidate_id']}` ({candidate['hypothesis']['mechanism_key']}): "
                        f"{status}; {decision.get('selection_reason') or decision.get('rejection_reasons', [])}; "
                        f"estimated cost {candidate['estimated_agent_calls']} agent calls + "
                        f"{candidate['estimated_evaluator_calls']} evaluator calls + "
                        f"{candidate.get('estimated_work_units') if candidate.get('estimated_work_units') is not None else 'unknown'} work units"
                    )
                for decision in portfolio.get("invalid_candidates", []):
                    lines.append(
                        f"  - `{decision['candidate_id']}`: rejected malformed candidate; "
                        f"{decision['selection_reason']}"
                    )
        if research.get("dev_lessons"):
            lines += ["", "### DEV lessons", ""]
            for lesson in research["dev_lessons"]:
                lines.append(
                    f"- Experiment {lesson['experiment_ordinal']} / `{lesson['mechanism_key']}`: "
                    f"{lesson['observation']} Interpretation `{lesson['interpretation']}`; "
                    f"{lesson['inference']} Next discriminator: {lesson['next_discriminator']} "
                    f"(evidence: {', '.join(lesson['evidence_refs'])})."
                )
        if research.get("stop_explanation"):
            lines.append(f"- Stop explanation: {research['stop_explanation']}")

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
    if r.get("calls_by_stage"):
        lines += ["", "### Calls by stage", ""]
        for stage, counts in r["calls_by_stage"].items():
            if counts["agent_calls"] or counts["evaluator_calls"] or counts["unknown_outcomes"]:
                lines.append(
                    f"- {stage}: {counts['agent_calls']} agent calls, "
                    f"{counts['evaluator_calls']} evaluator calls, "
                    f"{counts['unknown_outcomes']} unknown outcomes"
                )
    return "\n".join(lines)
