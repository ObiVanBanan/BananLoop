from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    task = json.load(sys.stdin)
    role = task.get("role")
    if role == "bootstrap":
        source = Path("examples/issue33/e2_rag_steel/evaluator.py")
        target = Path(".bananloop_eval/evaluator.py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        python = sys.executable
        emit(
            {
                "status": "ready",
                "summary": "Pinned rag_steel retrieval and technical-constraint evaluator with separate DEV and validation product groups.",
                "project_context": (
                    "The unified search pipeline retrieves steel source records and ranks linked LD candidates. "
                    "The fixed benchmark includes positive analog queries, no-match controls, and deliberately "
                    "contradictory technical constraints. The evaluator distinguishes source retrieval misses, "
                    "wrong LD selection, and returned candidates that violate requested hard constraints. "
                    "Improve the general candidate-selection behavior while preserving positive recall."
                ),
                "needs_input": [],
                "evaluation_command": [python, ".bananloop_eval/evaluator.py", "--split", "dev"],
                "smoke_command": [python, ".bananloop_eval/evaluator.py", "--split", "smoke"],
                "validation_command": [],
                "holdout_command": [],
                "primary_metric": {
                    "name": "hard_pass_rate",
                    "direction": "maximize",
                    "target": 1.0,
                    "min_improvement": 0.1,
                    "unit": "fraction",
                },
                "paired_comparison": {
                    "enabled": True,
                    "case_count": 54,
                    "repetitions": 1,
                    "metric": "case_quality",
                    "direction": "maximize",
                    "min_wins": 6,
                    "worst_case_tolerance": 0.0,
                },
                "guardrails": [
                    {"metric": "positive_recall", "operator": ">=", "bound": 1.0},
                    {"metric": "false_match_rate", "operator": "<=", "bound": 0.5},
                    {"metric": "contradiction_violation_rate", "operator": "<=", "bound": 0.0},
                ],
                "non_regression": [
                    {"metric": "positive_recall", "direction": "maximize", "tolerance": 0.0},
                    {"metric": "no_match_false_match_rate", "direction": "minimize", "tolerance": 0.0},
                ],
                "mandatory_checks": [
                    "fixed_query_set_complete",
                    "no_duplicate_ld_articles",
                    "pinned_catalog_model_index_identity",
                ],
                "editable_scope": ["src/rag_steel/search_engine.py"],
                "protected_paths": [
                    "examples/issue33/e2_rag_steel/fixtures/**",
                    "examples/issue33/e2_rag_steel/**",
                    "mapping_results.csv",
                ],
                "agent_visible_metrics": [
                    "hard_pass_rate",
                    "false_match_rate",
                    "positive_recall",
                    "contradiction_violation_rate",
                    "no_match_false_match_rate",
                ],
                "agent_visible_diagnostics": [
                    "retrieval_miss_count",
                    "wrong_ld_selection_count",
                    "constraint_mismatch_count",
                ],
                "signal_sanity": {
                    "status": "unavailable",
                    "reason": "The public benchmark labels are not an operator-approved independent reference manifest.",
                },
                "evaluator_timeout_seconds": 120,
            }
        )
        return

    if role == "propose":
        baseline = task.get("baseline") or {}
        diagnostics = baseline.get("diagnostics") or {}
        mismatch_count = float(diagnostics.get("constraint_mismatch_count") or 0.0)
        if mismatch_count <= 0:
            emit({"status": "NO_HYPOTHESIS", "reason": "No baseline technical-constraint violations were measured."})
            return
        emit(
            {
                "hypothesis": "Reject LD candidates that contradict an explicitly requested hard technical constraint.",
                "rationale": (
                    f"The independent baseline reports {int(mismatch_count)} returned technical-constraint violations. "
                    "The selector already computes per-candidate mismatch evidence but currently returns those candidates."
                ),
                "expected_effect": "Remove contradictory LD candidates while keeping positive recall and unrelated no-match behavior unchanged.",
                "files_or_area": ["src/rag_steel/search_engine.py", "LD candidate selector"],
                "falsification_condition": (
                    "Any positive-recall regression, fewer than six paired technical-control wins, "
                    "or a remaining returned candidate with a hard-constraint mismatch."
                ),
                "mechanism_key": "filter-hard-technical-constraint-mismatches",
                "relation": "new",
                "prior_experiment_refs": [],
                "novelty_rationale": "This tests selector evidence directly instead of tuning retrieval weights or score thresholds.",
            }
        )
        return

    if role == "implement":
        path = Path("src/rag_steel/search_engine.py")
        source = path.read_text(encoding="utf-8")
        needle = (
            "                ld_match_reasons, ld_mismatches = self._build_ld_match_reasons(\n"
            "                    processed,\n"
            "                    product,\n"
            "                    ld_field_score=ld_field_score,\n"
            "                )\n"
        )
        replacement = needle + "                if ld_mismatches:\n                    continue\n"
        if source.count(needle) != 1:
            emit({"summary": "Could not identify one unique LD selector insertion point.", "changed_paths": [], "verification_notes": "No source change was made.", "blockers": ["ambiguous selector source"]})
            return
        path.write_text(source.replace(needle, replacement), encoding="utf-8")
        emit(
            {
                "summary": "Filtered LD candidates when their requested hard technical fields produce mismatch evidence.",
                "changed_paths": [str(path)],
                "verification_notes": "The independent fixed evaluator checks positive recall, no-match behavior, and contradictory DN/PN controls.",
                "blockers": [],
            }
        )
        return

    emit({"status": "NO_HYPOTHESIS", "reason": f"Unsupported fixture role: {role}"})


if __name__ == "__main__":
    main()
