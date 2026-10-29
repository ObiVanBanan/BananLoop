"""Replay the six frozen proposer portfolios without invoking an LLM."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

from bananloop.research import normalize_portfolio, select_candidate
from runner import frozen_context, proposer_request


HERE = Path(__file__).resolve().parent
HISTORICAL = HERE / "results"


def content_key(candidate: Any) -> str:
    value = candidate.model_dump(mode="json", exclude={"candidate_id"})
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def legacy_winner_id(record: Any, state: Any) -> str | None:
    """Reconstruct the pre-v1 sort key from the recorded legacy criteria."""
    eligible = []
    for decision in record.decisions:
        if not decision.admissible:
            continue
        scores = decision.criterion_scores
        novelty = scores["mechanism_novelty"]
        strategy_priority = -novelty if state.planning_mode == "exploit" else novelty
        eligible.append((
            (strategy_priority, scores["dev_evidence"], scores["falsifiability"],
             scores["budget_feasibility"], scores["lower_estimated_cost"], decision.candidate_id),
            decision.candidate_id,
        ))
    return max(eligible, key=lambda item: item[0])[1] if eligible else None


def ordered_criteria(candidate: Any, scores: dict[str, int], strategy: str, tie_break: str) -> dict[str, Any]:
    novelty = scores["mechanism_novelty"]
    return {
        "strategy_aware_novelty": -novelty if strategy == "exploit" else novelty,
        "dev_evidence": scores["dev_evidence"],
        "falsifiability": scores["falsifiability"],
        "budget_feasibility": scores["budget_feasibility"],
        "lower_estimated_cost": scores["lower_estimated_cost"],
        "final_tie_break": tie_break,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "selector_calibration_v1")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    spec, _, experiments = frozen_context()
    report: list[dict[str, Any]] = []

    for run_dir in sorted(HISTORICAL.iterdir()):
        raw_path = run_dir / "response.json"
        if not raw_path.is_file():
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        original = normalize_portfolio(raw)
        historical = json.loads((run_dir / "selection.json").read_text(encoding="utf-8"))
        mode = run_dir.name.split("-", 1)[0]
        request, frozen_spec, state = proposer_request(mode)
        assert frozen_spec.model_dump(mode="json") == spec.model_dump(mode="json")

        def choose(portfolio):
            return select_candidate(
                portfolio, spec=spec, state=state, experiments=experiments,
                experiment_ordinal=4, action_id=f"calibration:{run_dir.name}", task=request,
            )

        current = choose(original)
        original_by_id = {item.candidate_id: item for item in original.candidates}
        old_winner_id = historical["selected_candidate_id"]
        old_winner = original_by_id[old_winner_id]
        assert legacy_winner_id(current, state) == old_winner_id, (
            f"reconstructed legacy selector disagrees with saved decision for {run_dir.name}"
        )
        current_winner = original_by_id[current.selected_candidate_id]
        old_decision_scores = next(
            item["criterion_scores"] for item in historical["decisions"]
            if item["candidate_id"] == old_winner_id
        )
        current_decision_scores = next(
            item.criterion_scores for item in current.decisions
            if item.candidate_id == current.selected_candidate_id
        )
        historical_decisions = {item["candidate_id"]: item for item in historical["decisions"]}
        current_decisions = {item.candidate_id: item for item in current.decisions}

        rename_case = None
        labels = ["alpha", "bravo", "charlie", "delta"][:len(original.candidates)]
        for order in itertools.permutations(labels):
            renamed_candidates = [
                candidate.model_copy(update={"candidate_id": order[index]})
                for index, candidate in enumerate(original.candidates)
            ]
            renamed_portfolio = original.model_copy(update={"candidates": renamed_candidates})
            renamed_record = choose(renamed_portfolio)
            renamed_winner_id = legacy_winner_id(renamed_record, state)
            winner = next(item for item in renamed_portfolio.candidates
                          if item.candidate_id == renamed_winner_id)
            if content_key(winner) != content_key(old_winner):
                rename_case = {
                    "candidate_id_mapping": {
                        original.candidates[index].candidate_id: order[index]
                        for index in range(len(order))
                    },
                    "winner_after_renaming": renamed_winner_id,
                    "winner_content_key": content_key(winner),
                    "winner_mechanism": winner.hypothesis.mechanism_key,
                    "winner_criterion_scores": next(
                        item.criterion_scores for item in renamed_record.decisions
                        if item.candidate_id == renamed_winner_id
                    ),
                }
                break

        permuted_record = choose(original.model_copy(update={"candidates": list(reversed(original.candidates))}))
        permuted_winner_id = legacy_winner_id(permuted_record, state)
        permuted_winner = original_by_id[permuted_winner_id]
        record = {
            "run": run_dir.name,
            "strategy": mode,
            "original_winner": old_winner_id,
            "original_winner_mechanism": old_winner.hypothesis.mechanism_key,
            "original_winner_criterion_scores": next(
                item["criterion_scores"] for item in historical["decisions"] if item["candidate_id"] == old_winner_id
            ),
            "original_winner_ordered_criteria": ordered_criteria(
                old_winner, old_decision_scores, mode, old_winner_id
            ),
            "new_winner": current.selected_candidate_id,
            "new_winner_mechanism": current_winner.hypothesis.mechanism_key,
            "new_winner_content_key": content_key(current_winner),
            "new_winner_criterion_scores": next(
                item.criterion_scores for item in current.decisions if item.candidate_id == current.selected_candidate_id
            ),
            "new_winner_ordered_criteria": ordered_criteria(
                current_winner, current_decision_scores, mode, content_key(current_winner)
            ),
            "mechanism_changed": old_winner.hypothesis.mechanism_key != current_winner.hypothesis.mechanism_key,
            "rename_changes_winner_content": rename_case is not None,
            "rename_replay": rename_case,
            "candidate_order_winner": permuted_winner_id,
            "candidate_order_changes_winner_content": content_key(permuted_winner) != content_key(old_winner),
            "eligibility_gates_preserved": all(
                historical_decisions[candidate.candidate_id]["admissible"]
                == current_decisions[candidate.candidate_id].admissible
                and historical_decisions[candidate.candidate_id]["rejection_reasons"]
                == current_decisions[candidate.candidate_id].rejection_reasons
                for candidate in original.candidates
            ),
            "new_ineligible_decision_selected": (
                current.selected_candidate_id is not None
                and not next(item.admissible for item in current.decisions
                             if item.candidate_id == current.selected_candidate_id)
            ),
            "decisions": [item.model_dump(mode="json") for item in current.decisions],
        }
        report.append(record)

    payload = {"schema_version": 1, "selector": "content-hash-tiebreak", "runs": report}
    (args.output / "replay.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Selector Calibration v1 — historical portfolio replay", "",
        "No LLM calls were made. Frozen DEV fixture spec, research state, and experiment history came from `runner.py`; the six raw proposer responses and original selector decisions are preserved under `results/`. The old ID-sort replay matches each saved original decision before rename probes.", "",
        "## Rename reproduction", "",
        "| Run | Original winner | Winner after ID renaming | Changed? | Reason |",
        "|---|---|---|---:|---|",
    ]
    for item in report:
        rename = item["rename_replay"]
        lines.append(
            f"| {item['run']} | `{item['original_winner']}` | "
            f"`{rename['winner_after_renaming'] if rename else 'no change found'}` | "
            f"{'yes' if rename else 'no'} | "
            f"{('all substantive ordering criteria tied; the final lexical candidate ID decided the winner' if rename else 'the winner remained ahead on an earlier substantive criterion')} |"
        )
    lines += ["", "## Old vs new selector", "", "Criteria order is strategy-aware novelty, DEV evidence, falsifiability, budget feasibility, estimated cost, then the old ID or the new neutral content hash.", "", "| Run | Old winner (mechanism) | Old criteria (novelty/evidence/falsif/budget/cost/tie) | New winner (mechanism) | New criteria (novelty/evidence/falsif/budget/cost/hash) | Mechanism changed? | Gates preserved? | New ineligible winner? |", "|---|---|---|---|---|---:|---:|---:|"]
    for item in report:
        def compact(criteria):
            return "/".join(str(value) for value in (
                criteria["strategy_aware_novelty"], criteria["dev_evidence"], criteria["falsifiability"],
                criteria["budget_feasibility"], criteria["lower_estimated_cost"], criteria["final_tie_break"],
            ))
        lines.append(
            f"| {item['run']} | `{item['original_winner']}` ({item['original_winner_mechanism']}) | `{compact(item['original_winner_ordered_criteria'])}` | "
            f"`{item['new_winner']}` ({item['new_winner_mechanism']}) | `{compact(item['new_winner_ordered_criteria'])}` | "
            f"{'yes' if item['mechanism_changed'] else 'no'} | "
            f"{'yes' if item['eligibility_gates_preserved'] else 'no'} | "
            f"{'yes' if item['new_ineligible_decision_selected'] else 'no'} |"
        )
    lines += ["", "Candidate-order permutation was checked for each run; see `replay.json`. The SHA-256 digest only resolves exact ties and has no scientific interpretation.", ""]
    (args.output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "runs": len(report),
        "rename_changed": sum(item["rename_changes_winner_content"] for item in report),
        "order_changed": sum(item["candidate_order_changes_winner_content"] for item in report),
        "new_ineligible_selected": sum(item["new_ineligible_decision_selected"] for item in report),
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
