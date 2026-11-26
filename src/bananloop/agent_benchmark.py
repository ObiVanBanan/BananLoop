"""Offline DEV-only agent capability benchmark. Not an RPDI-14 release scorer.

Runs a deterministic fixture in a subprocess or scores recorded role responses.
No live provider transport exists in this module by design.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

from .agent_benchmark_metrics import OracleContractError, score_oracle, validate_oracle
from .contracts import CapabilityPolicyV2
from .roles import RoleDefinitionV1, RoleRegistryV1, TeamFactoryV1, TeamManifestV1, TeamRequestV1

MAX_FILE_BYTES = 2_000_000
MAX_CASES = 200
WORKER = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_capability_v0.2" / "fixture_worker.py"


class BenchmarkError(ValueError):
    """An offline test input is invalid or violates a fail-closed boundary."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path: str | Path) -> Any:
    source = Path(path)
    if not source.is_file() or source.is_symlink() or source.stat().st_size > MAX_FILE_BYTES:
        raise BenchmarkError(f"missing, linked or oversized input file: {source.name}")
    try:
        return json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(BenchmarkError("nonfinite number")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid input JSON: {source.name}") from exc


def _write(path: Path, data: Any) -> None:
    path.write_bytes(canonical(data) + b"\n")


def _check_bundle(cases: dict, oracle: dict, registry: RoleRegistryV1,
                  team: TeamManifestV1, *, allowed_splits: frozenset[str] = frozenset({"DEV"}),
                  evaluator_pin_sha256: str | None = None) -> list[dict]:
    if not isinstance(cases, dict) or set(cases) != {"schema_version", "cases"} or cases["schema_version"] != 1:
        raise BenchmarkError("invalid case suite envelope")
    if not isinstance(oracle, dict) or set(oracle) != {"schema_version", "cases"} or oracle["schema_version"] != 1:
        raise BenchmarkError("invalid evaluator-only oracle envelope")
    data = cases["cases"]
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_CASES:
        raise BenchmarkError("suite requires 1..200 cases")
    if not isinstance(oracle["cases"], dict) or team.registry_digest != registry.digest():
        raise BenchmarkError("evaluator data or pinned role registry does not match")
    allowed_roles = {member.role.role_id: member.role for member in team.members}
    if len(allowed_roles) != len(team.members):
        raise BenchmarkError("one role per demo manifest is required")
    ids = set()
    for case in data:
        if not isinstance(case, dict) or any(key not in case for key in (
            "case_id", "suite", "role_id", "split", "agent_input", "evaluator_binding",
            "resource_budget", "provenance", "output_contract_id", "capability_profile_id",
        )):
            raise BenchmarkError("missing case contract fields")
        cid, role = case["case_id"], case["role_id"]
        if not isinstance(cid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", cid) or cid in ids:
            raise BenchmarkError("invalid or duplicate case ID")
        ids.add(cid)
        if case["split"] not in allowed_splits or case["suite"] != "role_capability":
            raise BenchmarkError("runner refuses unapproved split or system-level task dispatch")
        if role not in allowed_roles:
            raise BenchmarkError(f"case role is not in frozen team manifest: {role}")
        effective = allowed_roles[role]
        defined = registry.role(role)
        if any(getattr(effective, attr) != getattr(defined, attr) for attr in
               ("adapter", "provider", "model", "model_version", "prompt_digest")):
            raise BenchmarkError("role has drifted from the pinned registry")
        if not set(effective.capability_policy.tools) <= set(defined.capability_policy.tools):
            raise BenchmarkError("team role tool grant exceeds registry")
        if not isinstance(case["agent_input"], dict) or digest(case["agent_input"]) != case["provenance"].get("content_sha256"):
            raise BenchmarkError("case public input does not match provenance digest")
        if case["provenance"].get("license_status") not in {"APPROVED", "INTERNAL_SANITIZED"}:
            raise BenchmarkError("task data reuse rights are not approved")
        binding = case["evaluator_binding"]
        expected_pin = evaluator_pin_sha256 if evaluator_pin_sha256 is not None else digest(oracle)
        if binding.get("method") != "deterministic" or binding.get("sha256") != expected_pin:
            raise BenchmarkError("evaluator pin does not match independently committed oracle")
        if cid not in oracle["cases"]:
            raise BenchmarkError("evaluator lacks a case oracle")
        try:
            validate_oracle(oracle["cases"][cid])
        except OracleContractError as exc:
            raise BenchmarkError(f"invalid offline evaluator contract: {exc}") from exc
        limits = case["resource_budget"]
        if not isinstance(limits, dict) or any(type(limits.get(k)) is not int or limits[k] < 1 for k in
                                               ("wall_clock_seconds", "max_agent_calls")):
            raise BenchmarkError("case lacks positive resource ceilings")
        if type(limits.get("max_tool_calls")) is not int or limits["max_tool_calls"] < 0:
            raise BenchmarkError("case lacks bounded tools")
    if ids != set(oracle["cases"]):
        raise BenchmarkError("case and evaluator-only oracle ID lists differ")
    return data


def _fixture_reply(case: dict) -> dict:
    payload = canonical({"role_id": case["role_id"], "agent_input": case["agent_input"]})
    with tempfile.TemporaryDirectory(prefix="bananloop-role-fixture-") as directory:
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(WORKER)], input=payload, capture_output=True,
                timeout=case["resource_budget"]["wall_clock_seconds"], cwd=directory,
                env={"PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"}, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BenchmarkError("offline role worker did not finish") from exc
    if completed.returncode or len(completed.stdout) > 65536:
        raise BenchmarkError("offline role worker returned an invalid response")
    try:
        answer = json.loads(completed.stdout, object_pairs_hook=_unique)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BenchmarkError("offline role worker returned malformed JSON") from None
    if not isinstance(answer, dict):
        raise BenchmarkError("offline role worker returned a non-object")
    return answer


def _report(results: list[dict], *, run_id: str, suite_digest: str, mode: str,
            sealed: bool = False) -> tuple[dict, str]:
    groups: dict[str, list[dict]] = {}
    for result in results:
        groups.setdefault(result["role_identity"]["role_id"], []).append(result)
    summary = {"schema_version": 1, "run_id": run_id, "qualification": "DEV_DIAGNOSTIC_ONLY",
               "adapter": mode, "suite_sha256": suite_digest, "roles": {}}
    md = ["# Offline role-capability diagnostic", "",
          "**Not an independent RPDI-14 result. The scorer does not invoke an LLM.**", "",
          ("Sealed-split offline replay: oracle and case split were provided to a separate scoring stage."
           if sealed else
           "Public DEV diagnostic: fixture or recorded outputs, not a validated live role benchmark."), "",
          "| Role | Completed | Exact matches | Accuracy | Failed/incomplete |",
          "| --- | ---: | ---: | ---: | ---: |"]
    for role, group in sorted(groups.items()):
        completed = [r for r in group if r["status"] == "COMPLETED"]
        correct = sum(r["metric_results"][0]["value"] == 1 for r in completed)
        accuracy = correct / len(completed) if completed else None
        summary["roles"][role] = {"attempted": len(group), "completed": len(completed),
                                  "exact_matches": correct, "accuracy": accuracy,
                                  "failures": len(group) - len(completed)}
        md.append(f"| {role} | {len(completed)} | {correct} | "
                  f"{accuracy:.1%} | {len(group) - len(completed)} |" if accuracy is not None else
                  f"| {role} | 0 | 0 | N/A | {len(group)} |")
    md += ["", "There is deliberately **no cross-role aggregate winner**.",
           "Model/token/spend figures for replayed responses are **UNKNOWN**, not zero.", ""]
    return summary, "\n".join(md)


def run_suite(*, cases_path: str | Path, oracle_path: str | Path, registry_path: str | Path,
              team_path: str | Path, output_dir: str | Path, adapter: str = "fixture",
              responses_path: str | Path | None = None,
              allowed_splits: frozenset[str] = frozenset({"DEV"}),
              evaluator_pin_sha256: str | None = None,
              allow_incomplete_replay: bool = False) -> dict:
    if adapter not in {"fixture", "replay"} or bool(responses_path) != (adapter == "replay"):
        raise BenchmarkError("select fixture or replay with exactly one recorded responses file")
    suite = read_json(cases_path)
    oracle = read_json(oracle_path)
    registry = RoleRegistryV1.model_validate(read_json(registry_path))
    team = TeamManifestV1.model_validate(read_json(team_path))
    if allowed_splits != frozenset({"DEV"}) and (adapter != "replay" or evaluator_pin_sha256 is None):
        raise BenchmarkError("VALIDATION/HOLDOUT scoring requires a sealed private replay and evaluator commitment")
    data = _check_bundle(suite, oracle, registry, team,
                         allowed_splits=allowed_splits, evaluator_pin_sha256=evaluator_pin_sha256)
    replay = read_json(responses_path) if adapter == "replay" else None
    frozen_ids = {case["case_id"] for case in data}
    if replay is not None and (not isinstance(replay, dict) or set(replay) != {"schema_version", "responses"}
                               or replay["schema_version"] != 1 or not isinstance(replay["responses"], dict)
                               or (not set(replay["responses"]) <= frozen_ids if allow_incomplete_replay
                                   else set(replay["responses"]) != frozen_ids)):
        raise BenchmarkError("recorded outputs must cover exactly the frozen case IDs (or an authorized incomplete subset)")
    target = Path(output_dir)
    if target.exists():
        raise BenchmarkError("output directory already exists (overwrite disabled)")
    target.mkdir(parents=True, mode=0o700)
    run_id = digest({"cases": suite, "oracle_sha256": digest(oracle),
                     "role_registry_sha256": registry.digest(), "team_sha256": team.manifest_digest,
                     "adapter": adapter})[:24]
    records = []
    members = {member.role.role_id: member.role for member in team.members}
    for case in data:
        role = registry.role(case["role_id"])
        effective = members[case["role_id"]]
        started = time.monotonic()
        status, answer = "COMPLETED", None
        try:
            if adapter == "fixture":
                answer = _fixture_reply(case)
            elif case["case_id"] not in replay["responses"]:
                if not allow_incomplete_replay:
                    raise BenchmarkError("missing recorded response")
                status = "INCOMPLETE"
            else:
                answer = replay["responses"][case["case_id"]]
                if not isinstance(answer, dict) or len(canonical(answer)) > 65536:
                    raise BenchmarkError("recorded output must be a bounded JSON object")
        except (BenchmarkError, ValueError, TypeError):
            status = "AGENT_ERROR"
        seconds = round(time.monotonic() - started, 6)
        expected = oracle["cases"][case["case_id"]]
        score, checks = score_oracle(expected, answer) if status == "COMPLETED" else (None, [])
        resource = {"input_tokens": None, "output_tokens": None, "cached_tokens": None,
                    "agent_calls": 0, "tool_calls": 0, "evaluator_calls": 1,
                    "wall_clock_seconds": seconds,
                    "cost_usd": 0.0 if adapter == "fixture" else None,
                    "cost_status": "NOT_APPLICABLE" if adapter == "fixture" else "UNKNOWN"}
        receipt = digest({"expected_sha256": digest(expected), "output_sha256": digest(answer),
                          "case_id": case["case_id"], "status": status, "score": score})
        record = {"schema_version": 1, "run_id": run_id, "case_id": case["case_id"],
                  "seed": case.get("seed"),
                  "role_identity": {
                      "role_id": role.role_id, "role_version": None, "registry_version": str(registry.version),
                      "registry_sha256": registry.digest(), "team_manifest_sha256": team.manifest_digest,
                      "output_contract_sha256": digest(case["output_contract_id"]),
                      "capability_profile_sha256": digest(effective.capability_policy.model_dump(mode="json")),
                      "role_definition_sha256": digest(role.model_dump(mode="json")),
                  },
                  "agent_identity": {"provider": role.provider, "model_id": role.model,
                                     "model_revision": role.model_version, "prompt_sha256": role.prompt_digest,
                                     "adapter_version": f"offline-{adapter}-v1", "reasoning_effort": None},
                  "evaluator_identity": {"evaluator_id": case["evaluator_binding"]["evaluator_id"],
                                         "version": case["evaluator_binding"]["version"],
                                         "sha256": digest(oracle), "receipt_sha256": receipt},
                  "status": status,
                  "metric_results": [{"metric_id": "task_success", "value": score,
                                      "direction": "maximize", "source": "evaluator"}, *checks],
                  "resource_usage": resource, "policy_violations": [],
                  "artifact_sha256": [digest(answer)] if answer is not None else [],
                  "notes": "Offline public DEV diagnostic only; no model invocation or trusted usage receipt."}
        records.append(record)
    summary, markdown = _report(records, run_id=run_id, suite_digest=digest(suite),
                                 mode=adapter, sealed=(allowed_splits != frozenset({"DEV"})))
    _write(target / "results.json", {"schema_version": 1, "results": records})
    _write(target / "summary.json", summary)
    (target / "report.md").write_text(markdown, encoding="utf-8")
    return summary


def prepare_demo(directory: str | Path) -> dict:
    """Generate a pinned four-role, eight-case, offline-only DEV suite."""
    base = Path(directory)
    if base.exists():
        raise BenchmarkError("demo directory already exists (overwrite disabled)")
    base.mkdir(parents=True, mode=0o700)
    role_ids = ("researcher", "optimizer", "independent_verifier", "integration_verifier")
    policy = CapabilityPolicyV2(tools=(), read_paths=(), write_paths=(), backend="ISOLATED", network_access=False)
    registry = RoleRegistryV1(registry_id="agent-benchmark-offline-demo", version=1, roles=tuple(
        RoleDefinitionV1(role_id=name, adapter="command", provider="offline-fixture",
                         model="deterministic-demo", model_version="1",
                         prompt_digest=digest(f"demo:{name}"), capability_policy=policy,
                         context_fields=("campaign_goal",), priority=index, min_instances=1,
                         max_instances=1, calls_per_instance=1, tokens_per_instance=32)
        for index, name in enumerate(role_ids)
    ))
    request = TeamRequestV1(campaign_id="agent-benchmark-offline-demo",
                            spec_hash=digest("agent-benchmark-offline-demo"),
                            available_agent_calls=4, available_model_tokens=128)
    team = TeamFactoryV1.build(registry, request, expected_registry_digest=registry.digest())
    examples = (
        ("researcher", {"candidates": ["repeat", "alternative"], "refuted": ["repeat"]}, {"selected": "alternative"}),
        ("researcher", {"candidates": ["first", "second"], "refuted": []}, {"selected": "first"}),
        ("optimizer", {"values": [2, 7], "operation": "sum"}, {"result": 9}),
        ("optimizer", {"values": [-3, 5], "operation": "sum"}, {"result": 2}),
        ("independent_verifier", {"pins_valid": True, "isolation_valid": True}, {"decision": "PASS"}),
        ("independent_verifier", {"pins_valid": True, "isolation_valid": False}, {"decision": "REJECT"}),
        ("integration_verifier", {"candidate_valid": True, "tests_pass": True, "provenance_valid": True},
         {"decision": "ACCEPT"}),
        ("integration_verifier", {"candidate_valid": True, "tests_pass": False, "provenance_valid": True},
         {"decision": "REJECT"}),
    )
    oracle = {"schema_version": 1, "cases": {f"demo-{index:02d}": {"expected_output": expected}
                                               for index, (_, _, expected) in enumerate(examples)}}
    oracle_sha = digest(oracle)
    cases = []
    for index, (role, public, _) in enumerate(examples):
        cases.append({"schema_version": 1, "case_id": f"demo-{index:02d}",
                      "suite": "role_capability", "role_id": role, "role_version": None,
                      "task_family": "offline_contract", "domain": "synthetic", "split": "DEV",
                      "agent_input": public, "allowed_context": [],
                      "capability_profile_id": f"offline-{role}", "output_contract_id": f"offline-{role}-v1",
                      "evaluator_binding": {"evaluator_id": "offline-exact", "version": "1",
                                            "sha256": oracle_sha, "method": "deterministic"},
                      "provenance": {"source_id": "bananloop-owned-synthetic-demo",
                                     "source_revision": "1", "content_sha256": digest(public),
                                     "license_status": "INTERNAL_SANITIZED"},
                      "resource_budget": {"wall_clock_seconds": 5, "max_agent_calls": 1, "max_tool_calls": 0},
                      "seed": 0, "tags": ["offline", "diagnostic"]})
    _write(base / "registry.json", registry.model_dump(mode="json"))
    _write(base / "team.json", team.model_dump(mode="json"))
    _write(base / "oracle.dev.json", oracle)
    _write(base / "cases.dev.json", {"schema_version": 1, "cases": cases})
    return {"case_count": len(cases), "role_count": len(role_ids), "registry_digest": registry.digest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline-only BananLoop v0.2 role diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("prepare-demo", help="Create pinned synthetic DEV demo inputs")
    demo.add_argument("--out", required=True)
    execute = commands.add_parser("run", help="Execute fixture or replay recorded role outputs")
    for name in ("cases", "oracle", "registry", "team", "out"):
        execute.add_argument(f"--{name}", required=True)
    execute.add_argument("--adapter", choices=("fixture", "replay"), default="fixture")
    execute.add_argument("--responses", help="Required only for replay; JSON object keyed by case ID")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-demo":
            result = prepare_demo(args.out)
        else:
            result = run_suite(cases_path=args.cases, oracle_path=args.oracle,
                               registry_path=args.registry, team_path=args.team,
                               output_dir=args.out, adapter=args.adapter,
                               responses_path=args.responses)
    except (BenchmarkError, ValueError) as exc:
        parser.exit(2, f"benchmark refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
