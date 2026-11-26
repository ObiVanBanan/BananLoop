"""Public-only batching and lossless result collation for private sealed scoring.

Never reads the private oracle. A failed/unknown provider dispatch leaves its
case missing in the response union; the separate scorer records INCOMPLETE.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_benchmark import BenchmarkError, digest, read_json, _write


def stage_batches(*, public_dir: str, output_dir: str, batch_size: int = 2) -> dict:
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        raise BenchmarkError("live model batches must contain 1..20 public tasks")
    source = Path(public_dir)
    suite = read_json(source / "cases.json")
    pin = read_json(source / "commitment.json")
    if (not isinstance(suite, dict) or suite.get("schema_version") != 1
            or not isinstance(suite.get("cases"), list)
            or pin.get("public_suite_sha256") != digest(suite)):
        raise BenchmarkError("sealed public suite differs from immutable commitment")
    root = Path(output_dir)
    if root.exists():
        raise BenchmarkError("existing public batch directory cannot be overwritten")
    root.mkdir(parents=True, mode=0o700)
    entries = []
    for index, offset in enumerate(range(0, len(suite["cases"]), batch_size)):
        part = {"schema_version": 1, "cases": suite["cases"][offset: offset + batch_size]}
        name = f"batch-{index:03d}.json"
        _write(root / name, part)
        entries.append({"path": name, "suite_sha256": digest(part),
                        "case_ids": [case["case_id"] for case in part["cases"]]})
    manifest = {"schema_version": 1, "full_suite_sha256": digest(suite),
                "registry_sha256": pin.get("registry_sha256"),
                "team_manifest_sha256": pin.get("team_manifest_sha256"),
                "batches": entries, "run_authorized": False}
    _write(root / "batches.json", manifest)
    return {"batch_count": len(entries), "case_count": len(suite["cases"]),
            "run_authorized": False}


def merge_recorded(*, public_dir: str, batches_dir: str, run_dirs: list[str],
                   output_dir: str) -> dict:
    original = read_json(Path(public_dir) / "cases.json")
    manifest = read_json(Path(batches_dir) / "batches.json")
    if manifest.get("full_suite_sha256") != digest(original):
        raise BenchmarkError("batch manifest does not bind to the sealed public suite")
    approved = {batch["suite_sha256"]: set(batch["case_ids"]) for batch in manifest["batches"]}
    if not run_dirs:
        raise BenchmarkError("no model run archives supplied")
    combined = {}
    attempted = set()
    run_summary = []
    seen_batches = set()
    for directory in run_dirs:
        root = Path(directory)
        status = read_json(root / "run-status.json")
        replies = read_json(root / "responses.json")
        sha = status.get("suite_sha256")
        if sha not in approved or sha in seen_batches:
            raise BenchmarkError("model run refers to an unknown or repeated public task batch")
        seen_batches.add(sha)
        subset = approved[sha]
        values = replies.get("responses")
        if not isinstance(values, dict) or not set(values) <= subset:
            raise BenchmarkError("recorded outputs contain unexpected or duplicate case identities")
        if any(case_id in combined for case_id in values):
            raise BenchmarkError("repeated case ID in different model runs")
        if status.get("completed_cases", status.get("completed_calls")) != len(values):
            raise BenchmarkError("model status and recorded response count disagree")
        combined.update(values)
        attempted.update(subset)
        run_summary.append({
            "batch_sha256": sha, "status": status.get("status"),
            "completed": len(values), "assigned": len(subset),
            "meter_file_sha256": (
                digest(read_json(root / "api-meter.json")) if (root / "api-meter.json").exists()
                else digest(read_json(root / "usage.observed.json")) if (root / "usage.observed.json").exists()
                else None
            ),
        })
    target = Path(output_dir)
    if target.exists():
        raise BenchmarkError("collated response output already exists")
    target.mkdir(parents=True, mode=0o700)
    _write(target / "responses.json", {"schema_version": 1, "responses": combined})
    result = {"schema_version": 1, "full_suite_sha256": digest(original),
              "assigned_cases": len(attempted), "completed_cases": len(combined),
              "missing_cases": len(original["cases"]) - len(combined),
              "runs": run_summary, "release_eligible": False,
              "incomplete_cases_are_retained_in_private_scoring": True}
    _write(target / "merge-status.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stage public benchmark cases and collate responses")
    cmd = p.add_subparsers(dest="command", required=True)
    stage = cmd.add_parser("stage")
    stage.add_argument("--public-dir", required=True)
    stage.add_argument("--out", required=True)
    stage.add_argument("--batch-size", type=int, default=2)
    merge = cmd.add_parser("merge")
    merge.add_argument("--public-dir", required=True)
    merge.add_argument("--batches-dir", required=True)
    merge.add_argument("--run-dir", action="append", required=True)
    merge.add_argument("--out", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "stage":
            result = stage_batches(public_dir=args.public_dir,
                                   output_dir=args.out, batch_size=args.batch_size)
        else:
            result = merge_recorded(public_dir=args.public_dir, batches_dir=args.batches_dir,
                                    run_dirs=args.run_dir, output_dir=args.out)
    except (BenchmarkError, ValueError) as exc:
        p.exit(2, f"batch operation refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
