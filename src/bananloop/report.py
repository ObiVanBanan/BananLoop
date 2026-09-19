from __future__ import annotations

import json
from typing import Any

from .contracts import CampaignSpec, EvaluationResult
from .policy import metric_value
from .store import CampaignStore


def build_report(store: CampaignStore) -> dict[str, Any]:
    campaign = store.campaign()
    spec = CampaignSpec.model_validate_json(campaign["spec_json"])
    baseline = EvaluationResult.model_validate_json(campaign["baseline_json"]) if campaign["baseline_json"] else None
    champion = EvaluationResult.model_validate_json(campaign["champion_json"]) if campaign["champion_json"] else None
    experiments: list[dict[str, Any]] = []
    for row in store.experiments():
        accepted = row["status"] == "ACCEPTED"
        item = {
            "id": row["id"],
            "ordinal": row["ordinal"],
            "status": row["status"],
            "hypothesis": json.loads(row["hypothesis_json"]) if row["hypothesis_json"] else None,
            "candidate_snapshot": row["candidate_snapshot"],
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "failure_category": None if accepted else row["failure_category"],
            "lesson": "Promoted as new champion" if accepted else row["lesson"],
        }
        experiments.append(item)
    baseline_value = metric_value(baseline, spec.primary_metric.name) if baseline else None
    final_value = metric_value(champion, spec.primary_metric.name) if champion else None
    resource_summary = store.resource_summary()
    return {
        "campaign_outcome": campaign["status"],
        "stop_reason": campaign["stop_reason"],
        "goal": spec.goal,
        "primary_metric": spec.primary_metric.name,
        "baseline": baseline_value,
        "final_champion": final_value,
        "delta": None if baseline_value is None or final_value is None else final_value - baseline_value,
        "baseline_snapshot": campaign["baseline_snapshot"],
        "champion_snapshot": campaign["champion_snapshot"],
        "guardrails": [g.model_dump(mode="json") for g in spec.guardrails],
        "non_regression": [g.model_dump(mode="json") for g in spec.non_regression],
        "experiments": experiments,
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
        f"- Goal: {report['goal']}",
        f"- Primary metric: `{report['primary_metric']}`",
        f"- Baseline → final: `{report['baseline']}` → `{report['final_champion']}` (delta `{report['delta']}`)",
        f"- Baseline snapshot: `{report['baseline_snapshot']}`",
        f"- Champion snapshot: `{report['champion_snapshot']}`",
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
    r = report["resources"]
    lines += [
        "",
        "## Resources",
        "",
        f"- Agent calls: {r['agent_calls']}",
        f"- Evaluation calls: {r['evaluation_calls']}",
        f"- Scientific experiments: {r['experiments']}",
        f"- Attempts: {r['attempts']}",
        f"- Action wall time: {r['wall_time_seconds']:.3f} s",
        f"- Resource reservations: {r['reservation_count']} ({r['open_reservations']} open)",
        f"- Unknown action outcomes: {r['unknown_outcomes']}",
        "",
    ]
    return "\n".join(lines)
