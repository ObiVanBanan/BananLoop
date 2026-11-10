"""Opt-in preparation of PR #141 role and DiscoveryBench diagnostics.

No provider call, credential access, official RPDI-14 authorization, or scoring
of system-level research quality happens in this entrypoint. The role scorer is
intended for a physically separate operator/scorer host.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

from .agent_benchmark import BenchmarkError, _write, read_json
from .benchmark_batches import merge_recorded, stage_batches
from .benchmark_generate import ROLE_IDS, generate_private_smoke
from .benchmark_sealed import score_sealed, seal_suite
from .roles import RoleRegistryV1, TeamManifestV1


def _checkout() -> Path:
    return Path(__file__).resolve().parents[2]


def _outside_checkout(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    checkout = _checkout()
    if resolved == checkout or checkout in resolved.parents:
        raise BenchmarkError("generated benchmark outputs must be outside the source checkout")
    return resolved


def _separate_fresh_roots(*paths: Path) -> tuple[Path, ...]:
    roots = tuple(_outside_checkout(p) for p in paths)
    for index, root in enumerate(roots):
        if root.exists():
            raise BenchmarkError("benchmark output root already exists (overwrite disabled)")
        if any(root == other or root in other.parents or other in root.parents
               for other in roots[index + 1:]):
            raise BenchmarkError("operator/private/public/run roots must be non-overlapping")
    return roots


def _frozen_members(registry_path: str, team_path: str) -> tuple[set[str], str, str]:
    registry = RoleRegistryV1.model_validate(read_json(registry_path))
    team = TeamManifestV1.model_validate(read_json(team_path))
    if team.registry_digest != registry.digest():
        raise BenchmarkError("selected campaign role registry and team manifest do not match")
    members = {member.role.role_id for member in team.members}
    if not members or len(members) != len(team.members) or not members <= set(ROLE_IDS):
        raise BenchmarkError("selected team must contain unique supported v0.2 benchmark roles")
    for member in team.members:
        role = registry.role(member.role.role_id)
        if any(getattr(role, key) != getattr(member.role, key)
               for key in ("provider", "adapter", "model", "model_version", "prompt_digest")):
            raise BenchmarkError("selected team identity differs from frozen registry")
    return members, registry.digest(), team.manifest_digest


def prepare_roles(*, registry_path: str, team_path: str,
                  private_root: str, public_root: str, batches_root: str,
                  per_role: int = 10, batch_size: int = 2) -> dict:
    """Create 40 cases for a four-role team; reduce scope for an actually smaller team.

    Never invent unpinned roles. Keep authored answers/salt private, separate from
    worker-facing public cases. No provider calls or implicit approval files.
    """
    if type(per_role) is not int or not 5 <= per_role <= 50:
        raise BenchmarkError("per_role must be 5..50")
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        raise BenchmarkError("batch_size must be 1..20")
    members, reg_digest, team_digest = _frozen_members(registry_path, team_path)
    private, public, batches = _separate_fresh_roots(
        Path(private_root), Path(public_root), Path(batches_root))
    # Establish the private boundary before generating any answer key.
    private.mkdir(parents=True, mode=0o700)
    authored = private / "authored.json"
    generated = generate_private_smoke(path=authored, per_role=per_role)
    data = read_json(authored)
    included = [case for case in data["cases"] if case["role_id"] in members]
    if not included:
        raise BenchmarkError("selected team has no case-compatible pinned roles")
    if len(included) != len(data["cases"]):
        _write(private / "authored-selected.json",
               {"schema_version": 1, "cases": included})
        selected = private / "authored-selected.json"
        selected.chmod(0o600)
    else:
        selected = authored
    manifest = seal_suite(
        authored_suite_path=str(selected), registry_path=registry_path,
        team_path=team_path, public_dir=str(public),
        private_dir=str(private / "oracle"))
    batch_result = stage_batches(
        public_dir=str(public), output_dir=str(batches), batch_size=batch_size)
    by_role = {role: sum(c["role_id"] == role for c in included)
               for role in ROLE_IDS if role in members}
    result = {
        "schema_version": 1,
        "status": "PREPARED_NO_MODEL_CALLS",
        "qualification": "SEALED_LOCAL_SYNTHETIC_DIAGNOSTIC_NOT_RPDI14",
        "team_roles": sorted(members),
        "cases": len(included),
        "generated_total_before_role_filter": generated["private_case_count"],
        "roles": by_role,
        "splits": manifest["splits"],
        "batch_count": batch_result["batch_count"],
        "registry_sha256": reg_digest,
        "team_manifest_sha256": team_digest,
        "public_suite_sha256": manifest["public_suite_sha256"],
        "private_root": str(private),
        "public_root": str(public),
        "batches_root": str(batches),
        "model_calls": 0,
        "release_eligible": False,
    }
    _write(private / "prepare-summary.json", result)
    # Public plan contains no answer key, nonce or private path.
    _write(public / "diagnostic-plan.json", {
        key: value for key, value in result.items()
        if key not in {"private_root", "batches_root"}})
    return result


def score_roles(*, private_root: str, public_root: str, batches_root: str,
                run_dirs: list[str], merged_root: str, score_root: str,
                meter_ledgers: list[str] | None = None,
                operator_public_key_hex: str | None = None) -> dict:
    """Collect completed public batches and score on the separate private host.

    Missing answers remain INCOMPLETE; no secret is copied to the worker.
    """
    if not run_dirs:
        raise BenchmarkError("score requires at least one recorded batch run directory")
    merged, scores = _separate_fresh_roots(Path(merged_root), Path(score_root))
    private = Path(private_root).expanduser().resolve()
    public = Path(public_root).expanduser().resolve()
    batches = Path(batches_root).expanduser().resolve()
    oracle = private / "oracle"
    if not (oracle / "oracle.json").is_file() or not (oracle / "nonce.json").is_file():
        raise BenchmarkError("private scoring oracle/nonce are missing")
    if any(private == p or private in p.parents or p in private.parents
           for p in (public, batches, merged, scores)):
        raise BenchmarkError("private scoring root overlaps a public/worker root")
    merge = merge_recorded(
        public_dir=str(public), batches_dir=str(batches),
        run_dirs=run_dirs, output_dir=str(merged))
    score = score_sealed(
        public_dir=str(public), private_dir=str(oracle),
        responses_path=str(merged / "responses.json"),
        output_dir=str(scores), meter_ledgers=meter_ledgers,
        operator_public_key_hex=operator_public_key_hex)
    return {
        "schema_version": 1,
        "qualification": "SEALED_LOCAL_DIAGNOSTIC_NOT_RPDI14",
        "total_cases": sum(v["total"] for v in score["splits"].values()),
        "completed_cases": merge["completed_cases"],
        "missing_cases": merge["missing_cases"],
        "splits": score["splits"],
        "accounting": score["accounting"],
        "report": str(scores / "report.md"),
        "model_calls_dispatched_by_this_command": 0,
        "release_eligible": False,
    }


def stage_discoverybench(*, source_dir: str, output_root: str) -> dict:
    """Create byte-identical DEV arm inputs from the bundled board-games task.

    No gold, scorer, or model is supplied. This cannot qualify as hidden HOLDOUT.
    """
    source = Path(source_dir).expanduser().resolve()
    root, = _separate_fresh_roots(Path(output_root))
    files = ("TASK.md", "data.csv")
    for name in files:
        item = source / name
        if item.is_symlink() or not item.is_file() or item.stat().st_size > 10_000_000:
            raise BenchmarkError("DiscoveryBench public task file is missing or unsafe")
    with (source / "data.csv").open(encoding="utf-8", newline="") as inp:
        reader = csv.DictReader(inp)
        if not reader.fieldnames or len(reader.fieldnames) != 42:
            raise BenchmarkError("bundled DiscoveryBench CSV schema differs from pinned sample")
        rows = sum(1 for _ in reader)
    if rows != 500:
        raise BenchmarkError("bundled DiscoveryBench CSV has a changed row count")
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
              for name in files}
    for arm in ("bananloop", "codex"):
        dest = root / "arms" / arm
        dest.mkdir(parents=True, mode=0o700)
        for name in files:
            shutil.copyfile(source / name, dest / name)
            if hashlib.sha256((dest / name).read_bytes()).hexdigest() != hashes[name]:
                raise BenchmarkError("arm file copy failed immutable digest check")
    result = {
        "schema_version": 1, "track": "EXPLORATION",
        "task_id": "discoverybench-board-games-qid-10",
        "dataset": "DiscoveryBench", "source_revision":
            "c31fcf011e070f021a5f5b906896d0821f6880e8",
        "source_license": "ODC-BY (source repository; task use must be reviewed)",
        "files_sha256": hashes, "rows": rows, "columns": 42,
        "arm_names": ["bananloop", "codex"],
        "arm_inputs_identical": True, "split": "PUBLIC_DEV",
        "answer_key_staged": False, "model_calls": 0,
        "paired_scores_available": False, "independent_evaluator_attested": False,
        "qualification": "PUBLIC_DIAGNOSTIC_NOT_RPDI14",
        "release_eligible": False,
    }
    _write(root / "manifest.json", result)
    (root / "RUNBOOK.md").write_text(
        "# DiscoveryBench diagnostic, NOT an RPDI-14 trial\n\n"
        "Two byte-identical public DEV inputs are under arms/bananloop and arms/codex.\n"
        "Do not include upstream answer keys or hidden labels on either worker.\n"
        "Run BananLoop v2 Full and iterative Codex Direct in isolated worktrees;\n"
        "pin identical models, budgets, available DEV context and stopping rules.\n"
        "BananLoop requires separately approved explore / v2-pivot / optimize CLI\n"
        "steps; Codex Direct must be allowed multiple iterations, not one-shot.\n"
        "Do not score against the public source metadata as a sealed test.\n"
        "Use the independent RPDI-14 scorer and signed gates for any release claim.\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and score bounded PR #141 diagnostics; never run models implicitly")
    sub = parser.add_subparsers(dest="command", required=True)
    role = sub.add_parser("prepare-roles", help="generate and seal synthetic role cases")
    for key in ("registry", "team", "private-root", "public-root", "batches-root"):
        role.add_argument("--" + key, required=True)
    role.add_argument("--per-role", type=int, default=10)
    role.add_argument("--batch-size", type=int, default=2)
    score = sub.add_parser("score-roles", help="private host: merge recorded batches and score")
    for key in ("private-root", "public-root", "batches-root", "merged-root", "score-root"):
        score.add_argument("--" + key, required=True)
    score.add_argument("--run-dir", action="append", required=True)
    score.add_argument("--meter-ledger", action="append", default=[])
    score.add_argument("--operator-public-key-hex")
    discovery = sub.add_parser("stage-discoverybench", help="copy public DEV task to paired arms")
    discovery.add_argument("--source-dir", required=True)
    discovery.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-roles":
            result = prepare_roles(
                registry_path=args.registry, team_path=args.team,
                private_root=args.private_root, public_root=args.public_root,
                batches_root=args.batches_root, per_role=args.per_role,
                batch_size=args.batch_size)
        elif args.command == "score-roles":
            result = score_roles(
                private_root=args.private_root, public_root=args.public_root,
                batches_root=args.batches_root, run_dirs=args.run_dir,
                merged_root=args.merged_root, score_root=args.score_root,
                meter_ledgers=args.meter_ledger,
                operator_public_key_hex=args.operator_public_key_hex)
        else:
            result = stage_discoverybench(
                source_dir=args.source_dir, output_root=args.out)
    except (BenchmarkError, ValueError, OSError) as exc:
        parser.exit(2, "benchmark-suite refused: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
