"""Locally authored, randomized four-role synthetic cases for private smoke tests.

The full authored fixture includes expected answers: keep it on the evaluator
host only, never in Git or the Codex worker workspace. These tasks are not an
external benchmark, a sealed official test set or evidence of research ability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets

from .agent_benchmark import BenchmarkError, _write

ROLE_IDS = ("researcher", "optimizer", "independent_verifier", "integration_verifier")
PROTOCOL_CHECKS = [
    "protocol_identity", "evaluator_bundle_review", "metric_contract",
    "tier_manifests", "frozen_case_scope", "no_hidden_data_dependency",
    "fair_comparison", "error_semantics",
]
INTEGRATION_CHECKS = [
    "candidate_identity", "protocol_identity", "evaluation_receipt",
    "budget_receipt", "artifact_integrity", "regression_policy",
]


def generate_private_smoke(*, path: str | Path, per_role: int = 10) -> dict:
    """New cryptographic randomness, distinct DEV/VALIDATION/HOLDOUT case IDs."""
    target = Path(path)
    if target.exists() or not 5 <= per_role <= 50:
        raise BenchmarkError("private synthetic file exists or per-role count must be 5..50")
    rng = secrets.SystemRandom()
    cases = []
    split_plan = (["DEV"] * max(1, per_role // 4)
                  + ["VALIDATION"] * max(1, per_role // 4))
    split_plan += ["HOLDOUT"] * (per_role - len(split_plan))
    for role in ROLE_IDS:
        splits = split_plan.copy()
        rng.shuffle(splits)
        for index, split in enumerate(splits):
            cid = f"private-{role}-{index:03d}-{secrets.token_hex(5)}"
            strength = rng.randrange(5, 100)
            valid = rng.choice((True, False))
            proto = hashlib.sha256(secrets.token_bytes(48)).hexdigest()
            if role == "researcher":
                evidence_id = "e" + secrets.token_hex(6)
                candidate_good = rng.choice(("h1", "h2"))
                alternative = "h1" if candidate_good == "h2" else "h2"
                task = {
                    "goal": "Choose a bounded follow-up after controlled evidence.",
                    "candidate_ids": ["h1", "h2"],
                    "prior_observation": {
                        "claim_id": "claim-" + secrets.token_hex(3),
                        "evidence_id": evidence_id,
                        "refuted_candidate": alternative,
                        "observed_support_percent": strength,
                    },
                    "instruction": ("Return 2 falsifiable hypotheses with exactly the IDs h1 and h2, "
                                    "select the non-refuted follow-up as selected_hypothesis_id, "
                                    "cite the exact observation evidence_id in evidence_refs, "
                                    "and include one research_finding stating uncertainty."),
                }
                oracle = {"checks": [
                    {"type": "field_equals", "metric_id": "evidence_based_choice",
                     "path": ["selected_hypothesis_id"], "expected": candidate_good},
                    {"type": "contains_all", "metric_id": "evidence_provenance",
                     "path": ["evidence_refs"], "required": [evidence_id]},
                    {"type": "nonempty", "metric_id": "hypothesis_portfolio",
                     "path": ["hypotheses"]},
                ]}
            elif role == "optimizer":
                delta = rng.choice((-5, -3, -2, 2, 3, 5))
                hypothesis = "opt-" + secrets.token_hex(5)
                path_name = "src/ranker.py"
                task = {
                    "hypothesis_id": hypothesis,
                    "writable_paths": [path_name],
                    "visible_documents": {path_name: "def adjust(x):\n    return x\n"},
                    "goal": f"Return a full-file replacement for adjust(x) that adds {delta} to x.",
                    "instruction": ("Return OptimizationCandidateProposalV1 fields, not shell commands. "
                                    "Change only src/ranker.py, keeping the function name adjust."),
                }
                oracle = {"checks": [
                    {"type": "field_equals", "metric_id": "frozen_hypothesis_id",
                     "path": ["hypothesis_id"], "expected": hypothesis},
                    {"type": "nonempty", "metric_id": "bounded_file_replacement",
                     "path": ["changes", path_name]},
                ]}
            elif role == "independent_verifier":
                issue = rng.choice(PROTOCOL_CHECKS)
                packet = {k: True for k in PROTOCOL_CHECKS}
                if not valid:
                    packet[issue] = False
                task = {
                    "proposal_digest": proto,
                    "public_protocol_checks": packet,
                    "instruction": ("Decide APPROVE only if all eight immutable protocol checks pass; "
                                    "include the names of every checked condition in checks and a rationale. "
                                    "Never assert hidden evaluator results."),
                }
                oracle = {"checks": [
                    {"type": "field_equals", "metric_id": "protocol_verdict",
                     "path": ["decision"], "expected": "APPROVE" if valid else "REJECT"},
                    {"type": "field_equals", "metric_id": "proposal_pin",
                     "path": ["proposal_digest"], "expected": proto},
                ]}
            else:
                issue = rng.choice(INTEGRATION_CHECKS)
                check_results = {k: True for k in INTEGRATION_CHECKS}
                if not valid:
                    check_results[issue] = False
                candidate = secrets.token_hex(20)
                task = {
                    "candidate_snapshot_id": candidate, "protocol_digest": proto,
                    "core_check_results": check_results,
                    "instruction": ("Decide PASS only if all six Core checks pass; "
                                    "include all check names for PASS and output the pinned "
                                    "candidate_snapshot_id and protocol_digest."),
                }
                oracle = {"checks": [
                    {"type": "field_equals", "metric_id": "integration_verdict",
                     "path": ["decision"], "expected": "PASS" if valid else "FAIL"},
                    {"type": "field_equals", "metric_id": "candidate_pin",
                     "path": ["candidate_snapshot_id"], "expected": candidate},
                ]}
            cases.append({
                "case_id": cid, "role_id": role, "split": split,
                "agent_input": task, "oracle": oracle,
                "source_id": "bananloop-new-synthetic-private-smoke",
                "source_revision": "local-rng-v1",
                "license_status": "INTERNAL_SANITIZED",
                "resource_budget": {
                    "wall_clock_seconds": 120,
                    "max_agent_calls": 1, "max_tool_calls": 0,
                },
                "task_family": "synthetic_role_contract",
            })
    target.parent.mkdir(parents=True, exist_ok=True)
    _write(target, {"schema_version": 1, "cases": cases})
    target.chmod(0o600)
    return {"private_case_count": len(cases), "per_role": per_role,
            "splits": {split: sum(c["split"] == split for c in cases)
                       for split in ("DEV", "VALIDATION", "HOLDOUT")},
            "external_independence": False, "official_benchmark": False}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate locally held-out synthetic role contract checks")
    p.add_argument("--private-source", required=True)
    p.add_argument("--per-role", type=int, default=10)
    args = p.parse_args(argv)
    try:
        result = generate_private_smoke(path=args.private_source, per_role=args.per_role)
    except BenchmarkError as exc:
        p.exit(2, f"generator refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
