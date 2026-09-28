from __future__ import annotations

import json
from typing import Any

from .contracts import CampaignSpec, EvaluationResult
from .policy import metric_value, target_met
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

    resource_summary = store.resource_summary()
    research_state = store.research_state()
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
        "measurements": [
            {"experiment_id": row["experiment_id"], "candidate_snapshot": row["candidate_snapshot"],
             "tier": row["tier"], "result": json.loads(row["result_json"])}
            for row in store.evaluations()
        ],
        "experiments": experiments,
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

