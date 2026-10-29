from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from bananloop.agents.codex import CodexAdapter
from bananloop.contracts import BudgetSpec, CampaignSpec, Direction, MetricSpec
from bananloop.research import build_research_state, normalize_portfolio, select_candidate

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
CONDITIONS = ("exploit", "reframe")
RUBRIC = {
    "scale": "0 = absent/invalid, 1 = partial, 2 = clear and actionable; human-review judgment, not an objective model score",
    "criteria": {
        "admissibility": "Passes the core selector scope, evidence, schema, redundancy, and budget gates.",
        "specificity": "Names a concrete mechanism and bounded editable area rather than a generic goal.",
        "novelty": "Uses a distinct mechanism family relative to frozen DEV mechanisms and this portfolio; semantic novelty needs reviewer judgment.",
        "DEV_evidence_use": "Cites valid DEV references and explains what the observed small gains/rejections imply.",
        "testability": "States a concrete predicted observable and discriminating falsification test.",
    },
}


def fixture_spec() -> CampaignSpec:
    return CampaignSpec(
        goal="Improve exact-match technical position matching on the public CPU DEV task.",
        source_repo="fixture", base_commit="frozen-fixture", project_context=(
            "Python matcher in solution.py uses normalized token overlap. CPU-only; "
            "likely error classes include synonyms, negation, acronym expansion, and word order."
        ), evaluation_command=["python", "evaluator.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=1.0, min_improvement=0.001),
        editable_scope=["solution.py"], protected_paths=["evaluator.py"],
        agent_hidden_paths=["evaluator.py"], agent_visible_metrics=["quality"],
        budget=BudgetSpec(max_experiments=8, max_attempts=12, max_agent_calls=24, max_evaluator_calls=20,
                          plateau_experiments=4),
    )


def frozen_context() -> tuple[CampaignSpec, dict[str, Any], list[dict[str, Any]]]:
    spec = fixture_spec()
    increments = (0.0002, 0.0003, 0.0004)
    keys = ("token-normalization", "stopword-filtering", "overlap-tie-break")
    rows = []
    for i, (delta, key) in enumerate(zip(increments, keys), start=1):
        rows.append({
            "ordinal": i, "status": "REJECTED", "failure_category": "MINIMUM_IMPROVEMENT",
            "hypothesis_json": json.dumps({"hypothesis": f"Improve {key}", "rationale": "Prior DEV evidence", "expected_effect": "quality rises", "files_or_area": ["solution.py"], "falsification_condition": "quality does not rise", "mechanism_key": key, "relation": "new", "prior_experiment_refs": [], "novelty_rationale": "frozen seed evidence"}),
            "evaluation_json": json.dumps({"status": "ok", "metrics": {"quality": 0.5 + delta}, "checks": {}}),
            "decision_json": json.dumps({"accepted": False, "primary_value": 0.5 + delta, "champion_value": 0.5, "delta": delta, "reasons": ["MINIMUM_IMPROVEMENT_NOT_MET"]}),
            "dev_decision_json": json.dumps({"accepted": False, "primary_value": 0.5 + delta, "champion_value": 0.5, "delta": delta, "reasons": ["MINIMUM_IMPROVEMENT_NOT_MET"]}),
        })
    campaign = {"experiment_count": 3, "attempt_count": 3, "agent_calls": 3, "evaluator_calls": 6, "non_improve_streak": 0,
                "baseline_json": json.dumps({"status": "ok", "metrics": {"quality": 0.5}, "checks": {}}),
                "champion_json": json.dumps({"status": "ok", "metrics": {"quality": 0.5}, "checks": {}}),
                "champion_snapshot": "fixture-baseline"}
    return spec, campaign, rows


def proposer_request(mode: str) -> tuple[dict[str, Any], Any, Any]:
    if mode not in CONDITIONS:
        raise ValueError(f"unsupported condition: {mode}")
    spec, campaign, experiments = frozen_context()
    state = build_research_state(spec, campaign, experiments)
    state = state.model_copy(update={"planning_mode": mode})
    data = state.model_dump(mode="json")
    data["planning_mode"] = mode
    task_md = (FIXTURE / "TASK.md").read_text(encoding="utf-8")
    request = {
        "schema_version": 1, "role": "propose", "goal": spec.goal, "project_context": spec.project_context,
        "primary_metric": spec.primary_metric.model_dump(mode="json"), "guardrails": [], "non_regression": [],
        "editable_scope": spec.editable_scope, "protected_paths": spec.protected_paths,
        "agent_visible_training_paths": [], "agent_visible_context_paths": [],
        "baseline": {"status": "ok", "metrics": {"quality": 0.5}, "checks": {}},
        "champion": {"status": "ok", "metrics": {"quality": 0.5}, "checks": {}},
        "champion_snapshot": "fixture-baseline",
        "history": [
            {"ordinal": r["ordinal"], "status": r["status"], "dev_outcome": "DEV_PARTIAL_IMPROVEMENT",
             "dev_delta": json.loads(r["decision_json"])["delta"], "mechanism_key": json.loads(r["hypothesis_json"])["mechanism_key"],
             "rejection_reasons": ["MINIMUM_IMPROVEMENT_NOT_MET"]} for r in experiments
        ],
        "research_state": data, "planning_mode": mode,
        "portfolio_contract": {"max_candidates": 4, "candidate_fields": ["candidate_id", "hypothesis", "evidence_refs", "suspected_failure_mode", "predicted_observable", "discriminating_test", "scope_eligibility", "estimated_agent_calls", "estimated_evaluator_calls", "estimated_work_units", "rationale", "uncertainty", "inference=agent_inference"]},
        "planning_requirements": {"exploit": "build on strongest DEV-supported mechanism", "explore": "test a different mechanism family", "reframe": "challenge current framing and choose a new mechanism family", "champion_progress": "positive DEV deltas did not promote champion", "causal_link": "cite prior experiment ordinal and explain measured result"},
        "experiment_id": "synthetic-proposer-ab",
        "task_document": {"status": "available", "path": "TASK.md", "content": task_md,
                          "sha256": hashlib.sha256(task_md.encode("utf-8")).hexdigest()},
    }
    return request, spec, state


def _public_workspace(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE / "TASK.md", destination / "TASK.md")
    shutil.copyfile(FIXTURE / "solution.py", destination / "solution.py")


def run(output: Path, adapter_factory: Callable[[], Any] | None = None, repetitions: int = 3) -> dict[str, Any]:
    if repetitions != 3:
        raise ValueError("protocol fixes exactly three independent calls per condition")
    output.mkdir(parents=True, exist_ok=True)
    factory = adapter_factory or (lambda: CodexAdapter(model="gpt-6-luna"))
    blind: list[dict[str, Any]] = []
    key: dict[str, Any] = {}
    for condition in CONDITIONS:
        for rep in range(1, repetitions + 1):
            run_id = f"{condition}-{rep}"
            folder = output / run_id
            folder.mkdir(parents=True, exist_ok=False)
            workspace = folder / "workspace"
            _public_workspace(workspace)
            if (workspace / "evaluator.py").exists():
                raise RuntimeError("isolation failure: evaluator.py present in proposer workspace")
            request, spec, state = proposer_request(condition)
            (folder / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
            started = time.monotonic()
            response = None
            error = None
            usage = None
            try:
                response = factory().execute(request, workspace, 900)
                (folder / "response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
                if isinstance(response.get("usage"), dict):
                    usage = response["usage"]
                portfolio = normalize_portfolio(response)
                record = select_candidate(portfolio, spec=spec, state=state, experiments=frozen_context()[2],
                                          experiment_ordinal=4, action_id=f"ab:{run_id}", task=request)
                (folder / "normalized_portfolio.json").write_text(portfolio.model_dump_json(indent=2), encoding="utf-8")
                (folder / "selection.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
                for candidate in portfolio.candidates:
                    blind_id = "H-" + hashlib.sha256((run_id + candidate.candidate_id).encode()).hexdigest()[:12]
                    blind.append({"blind_id": blind_id, "candidate": candidate.model_dump(mode="json"),
                                  "rubric_0_to_2": {"admissibility": None, "specificity": None, "novelty": None,
                                                     "DEV_evidence_use": None, "testability": None}})
                    key[blind_id] = {"condition": condition, "replicate": rep, "candidate_id": candidate.candidate_id}
            except Exception as exc:  # persisted failure is an artifact, not silently swallowed
                error = f"{type(exc).__name__}: {exc}"
                (folder / "stderr.txt").write_text(error + "\n", encoding="utf-8")
            run_record = {"condition": condition, "replicate": rep, "duration_seconds": time.monotonic() - started,
                          "usage": usage, "error": error, "response_received": response is not None}
            (folder / "run.json").write_text(json.dumps(run_record, indent=2), encoding="utf-8")
    (output / "blind_scores.json").write_text(json.dumps(blind, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "rubric.json").write_text(json.dumps(RUBRIC, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"calls": 6, "completed": sum(1 for d in output.glob("*/run.json") if json.loads(d.read_text()).get("response_received")),
            "conditions": list(CONDITIONS), "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))


if __name__ == "__main__":
    main()
