"""Private-operator sealed role-task preparation and offline scoring.

Keep the oracle and its 256-bit salt off the model-runner host entirely. A
public task prompt necessarily becomes model-visible. 'Sealed' here refers
only to expected outputs/rubrics and operator-only validation/holdout state.
This is NOT an independent third-party audit or an RPDI-14 certified score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from .agent_benchmark import BenchmarkError, _write, canonical, digest, read_json, run_suite
from .agent_benchmark_metrics import OracleContractError, validate_oracle
from .roles import RoleRegistryV1, TeamManifestV1

_SPLITS = {"DEV", "VALIDATION", "HOLDOUT"}
_RIGHTS = {"APPROVED", "INTERNAL_SANITIZED"}
_REQUIRED = {"case_id", "role_id", "split", "agent_input", "oracle", "source_id",
             "source_revision", "license_status", "resource_budget", "task_family"}


def _distinct_directories(public_dir: Path, private_dir: Path) -> None:
    a, b = public_dir.resolve(), private_dir.resolve()
    if a == b or a in b.parents or b in a.parents:
        raise BenchmarkError("public and private evaluator roots must be distinct non-nested directories")
    checkout = Path(__file__).resolve().parents[2]
    if b == checkout or checkout in b.parents:
        raise BenchmarkError("private evaluator assets must never be inside the repository checkout")
    if a.exists() or b.exists():
        raise BenchmarkError("sealed public/private root already exists (overwrite disabled)")


def evaluator_commitment(nonce: str, oracle: dict) -> str:
    if not isinstance(nonce, str) or len(nonce) != 64:
        raise BenchmarkError("missing 256-bit private evaluator nonce")
    try:
        raw = bytes.fromhex(nonce)
    except ValueError as exc:
        raise BenchmarkError("malformed private evaluator nonce") from exc
    return hashlib.sha256(raw + canonical(oracle)).hexdigest()


def seal_suite(*, authored_suite_path: str, registry_path: str, team_path: str,
               public_dir: str, private_dir: str) -> dict:
    """Trusted operator writes separate public tasks and private evaluator-only oracle."""
    source = read_json(authored_suite_path)
    registry = RoleRegistryV1.model_validate(read_json(registry_path))
    team = TeamManifestV1.model_validate(read_json(team_path))
    if (not isinstance(source, dict) or set(source) != {"schema_version", "cases"}
            or source["schema_version"] != 1 or not isinstance(source["cases"], list)
            or not 1 <= len(source["cases"]) <= 200 or team.registry_digest != registry.digest()):
        raise BenchmarkError("invalid independently authored suite or pinned role manifest")
    public_root, private_root = Path(public_dir), Path(private_dir)
    _distinct_directories(public_root, private_root)
    role_ids = {member.role.role_id for member in team.members}
    public_cases = []
    oracle_cases = {}
    for authored in source["cases"]:
        if not isinstance(authored, dict) or set(authored) != _REQUIRED:
            raise BenchmarkError("authored case missing explicit public/private fields")
        cid, role, split = authored["case_id"], authored["role_id"], authored["split"]
        if (not isinstance(cid, str) or not cid or cid in oracle_cases
                or role not in role_ids or split not in _SPLITS
                or not isinstance(authored["agent_input"], dict)):
            raise BenchmarkError("invalid/duplicate case, role or split in private source")
        if authored["license_status"] not in _RIGHTS:
            raise BenchmarkError("case lacks operator-approved usage and redistribution rights")
        try:
            validate_oracle(authored["oracle"])
        except OracleContractError as exc:
            raise BenchmarkError(f"invalid independently authored evaluator contract: {exc}") from exc
        oracle_cases[cid] = authored["oracle"]
    oracle = {"schema_version": 1, "cases": oracle_cases}
    nonce = secrets.token_hex(32)
    commitment = evaluator_commitment(nonce, oracle)
    for authored in source["cases"]:
        public_cases.append({
            "schema_version": 1,
            "case_id": authored["case_id"],
            "suite": "role_capability",
            "role_id": authored["role_id"],
            "task_family": authored["task_family"],
            "split": authored["split"],
            "agent_input": authored["agent_input"],
            "allowed_context": [],
            "capability_profile_id": "pinned-role-contract",
            "output_contract_id": f"v0.2-{authored['role_id']}-model-reply",
            "evaluator_binding": {
                "evaluator_id": "separate-operator-fixed-oracle",
                "version": "1",
                "sha256": commitment,
                "method": "deterministic",
            },
            "provenance": {
                "source_id": authored["source_id"],
                "source_revision": authored["source_revision"],
                "content_sha256": digest(authored["agent_input"]),
                "license_status": authored["license_status"],
            },
            "resource_budget": authored["resource_budget"],
        })
    public = {"schema_version": 1, "cases": public_cases}
    manifest = {"schema_version": 1, "source_type": "OPERATOR_AUTHORED_PRIVATE_ORACLE",
                "public_suite_sha256": digest(public),
                "evaluator_commitment_sha256": commitment,
                "registry_sha256": registry.digest(),
                "team_manifest_sha256": team.manifest_digest,
                "case_count": len(public_cases),
                "splits": {split: sum(c["split"] == split for c in public_cases)
                           for split in sorted(_SPLITS)},
                "independently_verified": False,
                "rpdi14_qualified": False}
    public_root.mkdir(parents=True, mode=0o700)
    private_root.mkdir(parents=True, mode=0o700)
    _write(public_root / "cases.json", public)
    _write(public_root / "registry.json", registry.model_dump(mode="json"))
    _write(public_root / "team.json", team.model_dump(mode="json"))
    _write(public_root / "commitment.json", manifest)
    _write(private_root / "oracle.json", oracle)
    _write(private_root / "nonce.json", {"schema_version": 1, "nonce": nonce})
    for name in ("oracle.json", "nonce.json"):
        (private_root / name).chmod(0o600)
    return manifest


def score_sealed(*, public_dir: str, private_dir: str, responses_path: str,
                 output_dir: str) -> dict:
    """Second-stage scorer: privately verify salt/commitment before showing no gold to agents."""
    public_root, private_root = Path(public_dir), Path(private_dir)
    if public_root.resolve() == private_root.resolve() or (
            public_root.resolve() in private_root.resolve().parents
            or private_root.resolve() in public_root.resolve().parents):
        raise BenchmarkError("private evaluator root may not be nested in public task root")
    public = read_json(public_root / "cases.json")
    manifest = read_json(public_root / "commitment.json")
    oracle = read_json(private_root / "oracle.json")
    nonce = read_json(private_root / "nonce.json")
    registry = RoleRegistryV1.model_validate(read_json(public_root / "registry.json"))
    team = TeamManifestV1.model_validate(read_json(public_root / "team.json"))
    if (manifest.get("public_suite_sha256") != digest(public)
            or manifest.get("registry_sha256") != registry.digest()
            or manifest.get("team_manifest_sha256") != team.manifest_digest
            or manifest.get("evaluator_commitment_sha256") != evaluator_commitment(nonce.get("nonce"), oracle)):
        raise BenchmarkError("public manifest or private oracle has been replaced since sealing")
    if not isinstance(public["cases"], list) or any(
            c.get("evaluator_binding", {}).get("sha256") != manifest["evaluator_commitment_sha256"]
            for c in public["cases"]):
        raise BenchmarkError("case evaluator commitment mismatch")
    result = run_suite(
        cases_path=public_root / "cases.json",
        oracle_path=private_root / "oracle.json",
        registry_path=public_root / "registry.json",
        team_path=public_root / "team.json",
        output_dir=output_dir,
        adapter="replay",
        responses_path=responses_path,
        allowed_splits=frozenset(_SPLITS),
        evaluator_pin_sha256=manifest["evaluator_commitment_sha256"],
    )
    score_doc = read_json(Path(output_dir) / "results.json")
    splits = {}
    for case, record in zip(public["cases"], score_doc["results"], strict=True):
        if case["case_id"] != record["case_id"]:
            raise BenchmarkError("scorer case/result ordering mismatch")
        key = f"{case['split']}:{case['role_id']}"
        item = splits.setdefault(key, {"total": 0, "completed": 0, "passed": 0})
        item["total"] += 1
        if record["status"] == "COMPLETED":
            item["completed"] += 1
            item["passed"] += int(record["metric_results"][0]["value"] == 1)
    result["splits"] = splits
    result["evaluator_commitment_sha256"] = manifest["evaluator_commitment_sha256"]
    result["independently_verified"] = False
    result["release_eligible"] = False
    result["qualification"] = "SEALED_LOCAL_DIAGNOSTIC_NOT_RPDI14"
    _write(Path(output_dir) / "summary.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Separate-operator sealed benchmark authoring/scoring")
    cmd = parser.add_subparsers(dest="command", required=True)
    seal = cmd.add_parser("seal", help="Seal operator-authored tasks to distinct public/private roots")
    for field in ("authored-suite", "registry", "team", "public-dir", "private-dir"):
        seal.add_argument("--" + field, required=True)
    score = cmd.add_parser("score", help="Score all splits with private evaluator-only oracle")
    for field in ("public-dir", "private-dir", "responses", "out"):
        score.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            result = seal_suite(authored_suite_path=args.authored_suite,
                                registry_path=args.registry, team_path=args.team,
                                public_dir=args.public_dir, private_dir=args.private_dir)
        else:
            result = score_sealed(public_dir=args.public_dir, private_dir=args.private_dir,
                                  responses_path=args.responses, output_dir=args.out)
    except (BenchmarkError, ValueError) as exc:
        parser.exit(2, f"sealed benchmark refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
