"""Opt-in real ARE Full E2E and read-only failure triage.

No fake backend, injected response, forced verifier decision or automatic retry.
Use the normal production launcher, gated on the actual local runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

CASES = {
    "full": "shortest-valid-ci-l2-ece",
    "multi": "budgeted-imputation-mcar50",
    "rejection": "carps-star-discrepancy-subset-select",
    "verifier": "budgeted-imputation-mcar50",
}
ROLES = {"researcher", "independent_verifier", "optimizer", "integration_verifier"}
SAFE_FIELDS = ("status", "failure_type", "failure_detail", "failure_site",
               "failure_diagnostics", "error_type", "stage", "failure_stage",
               "schema_categories", "exception_type", "error_class", "termination_reason")


def read_object(path):
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def safe_fields(value):
    # Never dump raw model inputs, outputs, request_json or credentials.
    return {key: value[key] for key in SAFE_FIELDS if key in value}


def git(checkout, *args):
    return subprocess.check_output(
        ["git", "-C", str(checkout), *args], text=True, timeout=15
    ).strip()


def read_db(path):
    """Read live SQLite state, including WAL; do not use immutable=1."""
    calls, jobs, errors, timeline, integration, verifier_receipts = [], [], [], [], [], []
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "events" in tables:
                columns = {r[1] for r in db.execute("PRAGMA table_info(events)")}
                if {"seq", "event_type", "payload_json"} <= columns:
                    for seq, event, raw in db.execute(
                        "SELECT seq,event_type,payload_json FROM events ORDER BY seq"):
                        if event == "V2_PROVIDER_CALL":
                            item = json.loads(raw)
                            calls.append({k: item.get(k) for k in (
                                "role_id", "request_id", "status", "dispatched", "usage")})
                        elif any(s in event for s in ("PIVOT", "SUPERVISOR", "VERIF",
                                                      "EVALUAT", "CHAMPION", "TERMINAL",
                                                      "REFLECT", "STRATEG", "RESEARCH")):
                            timeline.append({"seq": seq, "event": event})
            if "evaluator_integration_evidence" in tables:
                for cid, digest, decision in db.execute(
                    "SELECT campaign_id,evidence_digest,decision FROM evaluator_integration_evidence"
                ):
                    integration.append({"campaign_id": cid, "digest": digest, "decision": decision})
            if "evaluator_integration_receipts" in tables:
                for cid, digest, decision in db.execute(
                    "SELECT campaign_id,receipt_digest,decision FROM evaluator_integration_receipts"
                ):
                    verifier_receipts.append({"campaign_id": cid, "digest": digest, "decision": decision})
            if {"worker_jobs", "worker_events"} <= tables:
                role_join = ("LEFT JOIN team_role_call_jobs r ON r.job_id=j.job_id"
                             if "team_role_call_jobs" in tables else "")
                role = "r.role_id" if role_join else "NULL"
                query = (
                    "SELECT j.job_id,j.campaign_id,j.action_id,j.resource,j.state,"
                    "j.updated_at," + role + ",e.event_type,e.payload_json "
                    "FROM worker_jobs j " + role_join +
                    " LEFT JOIN worker_events e ON e.event_id=(SELECT MAX(e2.event_id)"
                    " FROM worker_events e2 WHERE e2.job_id=j.job_id)"
                    " ORDER BY j.updated_at,j.job_id")
                for jid, cid, aid, resource, state, updated, rid, event, raw in db.execute(query):
                    item = {"job_id": jid, "campaign_id": cid, "action_id": aid,
                            "resource": resource, "state": state, "updated_at": updated,
                            "role_id": rid, "last_event": event}
                    if state in {"UNKNOWN", "DISPATCHED", "CANCELLED"} and raw:
                        item["diagnostics"] = safe_fields(json.loads(raw))
                    jobs.append(item)
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        errors.append(type(exc).__name__)
    return {"db": str(path), "calls": calls, "jobs": jobs,
            "timeline": timeline[-50:], "integration": integration,
            "verifier_receipts": verifier_receipts, "read_errors": errors}


def _runtime_dirs(state):
    """Enumerate actual registry/campaign roots, never candidate Git snapshots."""
    if not state.is_dir() or state.is_symlink():
        return []
    roots = {state}
    for path in state.iterdir():
        if (path.is_dir() and not path.is_symlink()
                and ((path / "campaigns").is_dir()
                     or (path / "campaign-registry.sqlite3").is_file())):
            roots.add(path)
    for root in tuple(roots):
        campaigns = root / "campaigns"
        if campaigns.is_dir() and not campaigns.is_symlink():
            roots.update(p for p in campaigns.iterdir()
                         if p.is_dir() and not p.is_symlink()
                         and (p / "campaign.sqlite3").is_file())
    return sorted(roots)


def diagnose(root, *, arm=None, state=None):
    """Summarize durable evidence. Never submit, resume, score or mutate a run."""
    root = Path(root)
    arm = Path(arm) if arm else root / "arm"
    state = Path(state) if state else root / "state"
    manifest = read_object(root / "manifest.json")
    selection = read_object(root / "selection.json")
    launcher = read_object(arm / "result.json")
    worker = read_object(arm / "worker-result.json")
    result = launcher.get("result") or worker
    if not isinstance(result, dict):
        result = {}
    bridge = result.get("harbor_bridge_receipt") or {}
    if not isinstance(bridge, dict):
        bridge = {}
    exploration = read_object(arm / "bananloop-exploration.jsonl")
    if not exploration:
        path = arm / "bananloop-exploration.jsonl"
        if path.is_file() and not path.is_symlink():
            try:
                from .runner import BananLoopFullBackend
                exploration = BananLoopFullBackend._read_json_object_from_output(path)
            except (OSError, ValueError):
                pass
    runtime_dirs = _runtime_dirs(state)
    dbs = [read_db(p) for p in sorted({
        p for folder in runtime_dirs for p in folder.iterdir()
        if p.name in {"campaign.sqlite3", "campaign-registry.sqlite3", "registry.sqlite3"}
        and p.is_file() and not p.is_symlink()
    })]
    calls = {}
    pending = []
    counts = Counter()
    for db in dbs:
        for item in db["calls"]:
            rid = item.get("request_id")
            if isinstance(rid, str) and rid:
                calls[(item.get("role_id"), rid)] = item
        counts.update(j["state"] for j in db["jobs"])
        pending.extend(j for j in db["jobs"]
                       if j["state"] in {"UNKNOWN", "DISPATCHED", "LEASED"})
    role_counts = Counter(c["role_id"] for c in calls.values()
                          if c["dispatched"] is True and c["status"] == "COMPLETED"
                          and c["role_id"] in ROLES)
    child = result.get("child_campaign_id")
    if not child:
        children = [p.get("child_campaign_id") for p in exploration.get("optimization_pivot", [])
                    if isinstance(p, dict) and p.get("status") == "BENCHMARK_AUTO_CHILD_CREATED"]
        if len(children) == 1:
            child = children[0]
    reports = []
    cli_failures = []
    recovered_comparisons = []
    for path in sorted(p for folder in runtime_dirs
                       if not (folder / "reports").is_symlink()
                       for p in (folder / "reports").glob("v2-optimization-*.json")):
        value = read_object(path)
        if path.name.startswith("v2-optimization-cli-"):
            if child and value.get("campaign_id") == child:
                cli_failures.append({"path": str(path), **safe_fields(value)})
                comparison = value.get("baseline_candidate_delta")
                if isinstance(comparison, dict):
                    recovered_comparisons.append(comparison)
        elif value.get("campaign_id") == child and child:
            reports.append(value)
    report = reports[-1] if len(reports) == 1 else {}
    comparison = report.get("baseline_candidate_delta") or (
        recovered_comparisons[0] if len(recovered_comparisons) == 1 else {}
    )
    if not isinstance(comparison, dict):
        comparison = {}
    decisions = Counter(e.get("decision") for e in report.get("integration_evidence", [])
                        if isinstance(e, dict) and isinstance(e.get("decision"), str))
    durable_decisions = {e["digest"]: e["decision"] for d in dbs for e in d["integration"]
                         if child and e["campaign_id"] == child}
    if durable_decisions:
        decisions = Counter(durable_decisions.values())
    verifier_decisions = Counter({
        e["digest"]: e["decision"] for d in dbs for e in d["verifier_receipts"]
        if child and e["campaign_id"] == child
    }.values())
    artifact = result.get("final_artifact") or {}
    artifact_ok = isinstance(artifact, dict) and artifact.get("status") == "CAPTURED"
    if artifact_ok:
        files = artifact.get("files", [])
        artifact_ok = bool(files)
        for f in files:
            try:
                path = arm / "final-artifacts" / f["path"]
                if (path.is_symlink() or not path.resolve().is_relative_to(
                        (arm / "final-artifacts").resolve())
                        or hashlib.sha256(path.read_bytes()).hexdigest() != f["sha256"]):
                    artifact_ok = False
            except (OSError, TypeError, KeyError):
                artifact_ok = False
    pivots = [p for p in exploration.get("optimization_pivot", [])
              if isinstance(p, dict) and p.get("status") == "BENCHMARK_AUTO_CHILD_CREATED"]
    full_checks = {
        "terminal_launcher": bool(launcher),
        "worker_completed": result.get("status") == "COMPLETED",
        "harbor_completed": bridge.get("bananloop_status") == "ALL_NODES_COMPLETE",
        "auto_pivot_observed": len(pivots) == 1 and pivots[0].get("child_campaign_id") == child,
        "four_roles_with_completed_request_ids": ROLES <= set(role_counts),
        "dev_comparison_verified": comparison.get("status") == "VERIFIED"
                                   and comparison.get("tier") == "dev",
        "independent_integration_pass": verifier_decisions["PASS"] > 0,
        "artifact_hashes_match": artifact_ok,
        "verified_champion_archived": result.get("final_artifact_source") == "VERIFIED_CHAMPION",
        "cleanup_verified": launcher.get("cleanup_status") == "CLEANED",
        "no_unknown_jobs_or_usage": not pending and result.get("usage_unknown") is False
                                    and not any(d["read_errors"] for d in dbs),
    }
    # Rejection and UNKNOWN are not manufactured. They are observations, not
    # claims that the next prompt consumed memory or that recovery succeeded.
    checkpoints = {
        "full": "PASS" if all(full_checks.values()) else "NOT_PASSED",
        "multi": ("PASS" if all(full_checks.values()) and role_counts["optimizer"] >= 3
                  and sum(verifier_decisions.values()) >= 3 else "NOT_PASSED"),
        "rejection": "OBSERVED_REVIEW_REQUIRED" if decisions["FAIL"] or decisions["REJECT"]
                     else "NOT_OBSERVED",
        "verifier": "OBSERVED_REVIEW_REQUIRED" if any(
            j["role_id"] == "integration_verifier" and j["state"] == "UNKNOWN" for j in pending)
                    else "NOT_OBSERVED",
    }
    logs = {}
    for filename in ("bananloop-exploration.jsonl", "bananloop-exploration.stderr",
                     "harbor-preparation.stdout", "harbor-preparation.stderr",
                     "bananloop-optimization.stdout", "bananloop-optimization.stderr",
                     "worker-result.json", "result.json"):
        path = arm / filename
        if path.is_file() and not path.is_symlink():
            logs[filename] = {"path": str(path), "bytes": path.stat().st_size}
    if launcher:
        stage = "TERMINAL"
    elif read_object(root / "preflight-diagnostics.json").get("status") == "PREP_BLOCKED":
        stage = "BLOCKED_PREFLIGHT"
    elif "bananloop-optimization.stdout" in logs:
        stage = "HARBOR_OPTIMIZATION"
    elif "harbor-preparation.stdout" in logs:
        stage = "HARBOR_PREPARATION"
    elif "bananloop-exploration.jsonl" in logs:
        stage = "EXPLORATION_OR_PIVOT"
    else:
        stage = "BEFORE_EXPLORATION_OR_UNKNOWN"
    return {
        "schema": "bananloop-live-e2e-diagnostic-v1", "selection": selection,
        "candidate_source_sha": manifest.get("candidate_source_sha"),
        "model": manifest.get("model"), "manifest_sha256": manifest.get("manifest_sha256"),
        "stage": stage, "worker_status": result.get("status", "MISSING_OUTPUT"),
        "worker_diagnostics": safe_fields(result), "exploration_diagnostics": safe_fields(exploration),
        "optimization_cli_diagnostics": cli_failures,
        "harbor_bridge": bridge, "cleanup_status": launcher.get("cleanup_status", "UNKNOWN"),
        "champion_recovery": result.get("champion_recovery"),
        "usage": result.get("usage"),
        "role_completed_request_counts": dict(role_counts), "provider_receipts": list(calls.values()),
        "job_state_counts": dict(counts), "unresolved_jobs": pending,
        "database_evidence": [{"db": d["db"], "timeline": d["timeline"],
                               "read_errors": d["read_errors"]} for d in dbs],
        "comparison": comparison, "integration_decisions": dict(decisions),
        "verifier_receipt_decisions": dict(verifier_decisions),
        "artifact_hashes_match": artifact_ok, "artifact_source": result.get("final_artifact_source"),
        "checks": full_checks, "scenario_results": checkpoints, "logs": logs,
        "limit": "Public DEV diagnostic; semantic reflection, recovery and model provenance need trace review. No blind/HOLDOUT claim.",
    }


def probe_codex():
    status = subprocess.run(["codex", "login", "status"], capture_output=True,
                            text=True, timeout=15, check=False)
    lines = [s.strip() for s in (status.stdout + "\n" + status.stderr).splitlines()
             if s.strip() and not s.strip().startswith("WARNING:")]
    if status.returncode or lines != ["Logged in using ChatGPT"]:
        raise ValueError("Codex must be logged in using ChatGPT subscription")
    return subprocess.check_output(["codex", "--version"], text=True, timeout=15).strip()


def run(args):
    from . import runner
    from .arm_watchdog import ArmDeadline
    from .bound_preflight import create_bound_preflight
    from .full_arm_launch import launch_full_arm

    if not args.approve_live:
        raise ValueError("Explicit --approve-live is required; this consumes real subscription quota")
    if os.name != "posix":
        raise ValueError("Use Linux/POSIX for the production watchdog and Harbor")
    if os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Unset OPENAI_API_KEY; subscription route only")
    checkout = Path(__file__).resolve().parents[2]
    sha = git(checkout, "rev-parse", "HEAD")
    if git(checkout, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Run from a clean checkout")
    root = args.out.expanduser().absolute()
    if root.resolve().is_relative_to(checkout):
        raise ValueError("--out must be outside the Git checkout")
    runtime = args.runtime_root.expanduser().absolute()
    harbor = args.harbor.expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    model = args.model
    task_id = args.task or CASES[args.case]
    selection = {"case": args.case, "task_id": task_id, "model": model,
                 "seconds": args.seconds, "source_sha": sha,
                 "mode": "REAL_PROVIDER_AND_PUBLIC_DEV", "automatic_retry": False}
    runner._atomic_json(root / "selection.json", selection, replace=False)
    print(json.dumps(selection), flush=True)
    print("Artifacts: " + str(root), flush=True)
    try:
        version = probe_codex()
        harbor_version = runner.inspect_harbor(harbor)
        manifest = runner._prepare_series_manifest(
            catalogue=runner.read_json(runner.CATALOGUE), seed=7, model=model,
            reasoning=args.reasoning, codex_version=version, candidate_source_sha=sha)
        digest = manifest["manifest_sha256"]
        runner._atomic_json(root / "manifest.json", manifest, replace=False)
        pairs = [x for x in manifest["tasks"] if x["task"]["task_id"] == task_id]
        if len(pairs) != 1:
            raise ValueError("Selected task must be in the pinned ARE catalogue")
        pair = pairs[0]
        arm_id = pair["pair_id"] + "-bananloop_full"
        offline = runner.offline_preflight(
            runtime_root=runtime, codex_auth_status="Logged in using ChatGPT",
            codex_version=version, harbor_version=harbor_version,
            image_inspector=runner.inspect_docker_image)
        runner._atomic_json(root / "preflight-diagnostics.json", offline, replace=False)
        if offline.get("status") != "READY_NON_RPDI_DIAGNOSTIC" or offline.get("blockers"):
            print(json.dumps({"status": "BLOCKED_PREFLIGHT", "blockers": offline.get("blockers")}))
            return 2
        create_bound_preflight(
            manifest_path=root / "manifest.json", expected_manifest_sha256=digest,
            candidate_checkout=checkout, candidate_source_sha=sha, task_id=task_id,
            arm_id=arm_id, runtime_root=runtime, generated_at=datetime.now(timezone.utc).isoformat(),
            output_path=root / "preflight.json", codex_auth_status="Logged in using ChatGPT",
            codex_version=version, harbor_version=harbor_version,
            image_inspector=runner.inspect_docker_image)
        config = {"backend_kwargs": {
            "initial_repo": str(checkout), "base_sha": sha, "runtime_root": str(runtime),
            "state_root": str(root / "state"), "model": model,
            "harbor_binary": str(harbor), "python_binary": sys.executable}}
        print("Preflight passed; starting one real Full arm", flush=True)
        launch_full_arm(
            manifest_path=root / "manifest.json", preflight_path=root / "preflight.json",
            candidate_checkout=checkout, expected_manifest_sha256=digest, task_id=task_id,
            candidate_source_sha=sha, arm_id=arm_id, backend_config=config,
            arm_root=root / "arm", deadline=ArmDeadline.start(args.seconds))
    finally:
        evidence = diagnose(root)
        runner._atomic_json(root / "live-e2e-report.json", evidence, replace=False)
        print(json.dumps({"stage": evidence["stage"], "worker_status": evidence["worker_status"],
                          "scenario_results": evidence["scenario_results"],
                          "report": str(root / "live-e2e-report.json")}), flush=True)
    return 0 if evidence["scenario_results"][args.case] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("run", help="explicitly start one real Full arm")
    launch.add_argument("--case", choices=CASES, required=True)
    launch.add_argument("--model", default="gpt-6-luna")
    launch.add_argument("--task", help="override default with another pinned ARE task ID")
    launch.add_argument("--reasoning", default="medium", help="manifest label; runtime role policy remains authoritative")
    launch.add_argument("--runtime-root", type=Path, required=True)
    launch.add_argument("--harbor", type=Path, required=True)
    launch.add_argument("--out", type=Path, required=True)
    launch.add_argument("--seconds", type=int, default=3600, choices=range(1, 3601),
                        metavar="1..3600")
    launch.add_argument("--approve-live", action="store_true")
    inspect = sub.add_parser("diagnose", help="inspect current or historical artifacts without calls")
    inspect.add_argument("--out", type=Path, required=True)
    inspect.add_argument("--arm-root", type=Path)
    inspect.add_argument("--state-root", type=Path)
    args = parser.parse_args()
    if args.command == "diagnose":
        print(json.dumps(diagnose(args.out, arm=args.arm_root, state=args.state_root),
                         ensure_ascii=False, indent=2))
        return 0
    try:
        return run(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Live E2E setup/launch failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
