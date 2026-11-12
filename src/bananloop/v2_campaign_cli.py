"""Owner-facing v2 Exploration campaign creation and bounded SIWC execution."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .budget_ledger import BudgetLedger
from .chatgpt_plan import ChatGPTPlanAuth, SIWCError
from .chatgpt_responses import (
    ChatGPTPlanProtocolVerifier, ChatGPTPlanResearcher, INSTRUCTIONS,
    INTEGRATION_VERIFIER_INSTRUCTIONS, OPTIMIZER_INSTRUCTIONS,
    PROTOCOL_VERIFIER_INSTRUCTIONS,
)
from .contracts import (
    AutonomyScopeV2, ExplorationCampaignSpecV2, OptimizationCampaignSpecV2,
    OptimizationTaskV2,
    PivotExecutionPlanV2, PivotProposal, PivotResourceRequest, RuntimePaths, utc_now,
)
from .evaluator_fixture import EvaluatorFixtureV1
from .graph_store import GraphRepository
from .migrations import create_v5_graph_schema
from .registry import CampaignRegistry
from .roles import RoleDefinitionV1, RoleRegistryV1, TeamManifestAuthority
from .capabilities import CapabilityPolicyV2
from .v2_research_runtime import V2ResearchRuntime, V2RuntimeError, make_researcher_invoke
from .worker_queue import DurableWorkerQueue


class V2CampaignCLIError(ValueError):
    """A v2 campaign could not be safely prepared or advanced."""


def _required_optimization_evaluator_calls(
    repeatability_runs: int, *, has_holdout: bool = True,
) -> int:
    """Minimum evaluator jobs for one candidate lifecycle.

    A public-only protocol uses DEV plus baseline/candidate VALIDATION and must
    not synthesize or invoke a HOLDOUT tier. Existing sealed protocols retain
    their final HOLDOUT reservation by default.
    """
    if (isinstance(repeatability_runs, bool) or not isinstance(repeatability_runs, int)
            or not 2 <= repeatability_runs <= 10):
        raise V2CampaignCLIError("approved protocol repeatability count is invalid")
    if not isinstance(has_holdout, bool):
        raise V2CampaignCLIError("approved protocol HOLDOUT declaration is invalid")
    # Candidate DEV, baseline/candidate VALIDATION, optional final HOLDOUT.
    return (3 + int(has_holdout)) * repeatability_runs


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.PIPE, text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise V2CampaignCLIError("source repository Git operation failed") from exc


def _read_paths(repo: Path, paths: list[str]) -> tuple[str, ...]:
    if not paths or len(paths) > 16:
        raise V2CampaignCLIError("supply 1 to 16 exact --read-path values")
    normalized: list[str] = []
    for value in paths:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            raise V2CampaignCLIError("read paths must be nonempty POSIX-relative files")
        path = PurePosixPath(value)
        if (path.is_absolute() or PureWindowsPath(value).drive
                or any(part in {".", ".."} for part in path.parts)
                or any(part.lower() == ".git" or part.lower().startswith(".env")
                       or any(marker in part.lower() for marker in (
                           "evaluator", "evaluation", "validation", "holdout",
                           "secret", "credential",
                       )) for part in path.parts)):
            raise V2CampaignCLIError("read path is absolute, protected, or traverses its repository")
        candidate = repo.joinpath(*path.parts)
        current = repo
        contains_symlink = False
        for part in path.parts:
            current = current / part
            contains_symlink = contains_symlink or current.is_symlink()
        if (contains_symlink or not candidate.is_file()
                or not candidate.resolve().is_relative_to(repo)):
            raise V2CampaignCLIError(f"read path is not a safe repository file: {value}")
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise V2CampaignCLIError("read paths must be unique")
    return tuple(sorted(normalized))


def _researcher_input_fields(*, allow_registered_experiments: bool,
                             require_protocol_intent: bool = False) -> frozenset[str]:
    fields = {
        "protocol", "role_context", "visible_documents", "instruction",
        "available_research_memory",
    }
    if allow_registered_experiments:
        fields.add("registered_experiments")
    if require_protocol_intent:
        fields.add("requires_protocol_intent")
    return frozenset(fields)


def _require_committed_pilot_inputs(repo: Path, paths: tuple[str, ...]) -> None:
    """Prevent executing mutable benchmark code/data outside the frozen Git base."""
    for relative in paths:
        candidate = repo / relative
        if (candidate.is_symlink() or not candidate.is_file()
                or not candidate.resolve().is_relative_to(repo)):
            raise V2CampaignCLIError(f"registered pilot input is missing or unsafe: {relative}")
        try:
            committed = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise V2CampaignCLIError(f"registered pilot input is not in the frozen Git base: {relative}") from exc
        if candidate.read_bytes() != committed:
            raise V2CampaignCLIError(f"registered pilot input differs from the frozen Git base: {relative}")


def _campaign_deadline(state_root: Path, *, campaign_id: str, spec_hash: str,
                       time_limit_seconds: int) -> float:
    """Create once and reuse the active-run deadline across campaign resumes."""
    target = state_root / "campaign-run-deadline.json"
    if target.is_symlink():
        raise V2CampaignCLIError("campaign deadline record is a symlink")
    if target.exists():
        try:
            record = json.loads(target.read_text(encoding="utf-8"))
            if (record.get("schema") != "bananloop-campaign-deadline-v1"
                    or record.get("campaign_id") != campaign_id
                    or record.get("spec_hash") != spec_hash
                    or record.get("time_limit_seconds") != time_limit_seconds
                    or isinstance(record.get("started_epoch"), bool)
                    or not isinstance(record.get("started_epoch"), (float, int))
                    or isinstance(record.get("deadline_epoch"), bool)
                    or not isinstance(record.get("deadline_epoch"), (float, int))
                    or not math.isfinite(float(record["started_epoch"]))
                    or not math.isfinite(float(record["deadline_epoch"]))
                    or float(record["deadline_epoch"]) != float(record["started_epoch"]) + time_limit_seconds):
                raise ValueError
            return float(record["deadline_epoch"])
        except Exception as exc:
            raise V2CampaignCLIError("persisted campaign deadline record is invalid") from exc
    started = time.time()
    record = {
        "schema": "bananloop-campaign-deadline-v1", "campaign_id": campaign_id,
        "spec_hash": spec_hash, "time_limit_seconds": time_limit_seconds,
        "started_epoch": started, "deadline_epoch": started + time_limit_seconds,
    }
    raw = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _campaign_deadline(
            state_root, campaign_id=campaign_id, spec_hash=spec_hash,
            time_limit_seconds=time_limit_seconds,
        )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return float(record["deadline_epoch"])


def _build_spec(repo: Path, goal: str, paths: tuple[str, ...], *,
                max_agent_calls: int, max_model_tokens: int, model: str,
                max_evaluator_calls: int = 16,
                max_iterations: int = 2,
                deadline_seconds: int = 3600,
                optimization_write_paths: tuple[str, ...] = (),
                evaluator_fixture_digest: str | None = None):
    if max_agent_calls < 4:
        raise V2CampaignCLIError("--max-agent-calls must be at least 4 to fund the four pinned v2 roles")
    if not set(optimization_write_paths) <= set(paths):
        raise V2CampaignCLIError("optimization write paths must be included in the read-path allowlist")
    policy = {
        "policy_version": 1,
        "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
        "read_paths": list(paths),
        "write_paths": ["**"],
        "backend": "LOCAL",
        "network_access": False,
    }
    # A bounded Optimization epoch needs one optimizer call and one independent
    # integration-verifier call. Allocate these as matched pairs, while keeping
    # room for the requested Exploration iterations and one protocol verifier.
    researcher_calls = min(max_iterations, max_agent_calls - 3)
    optimization_pair_budget = max_agent_calls - researcher_calls - 1
    optimizer_calls = max(1, optimization_pair_budget // 2)
    integration_calls = optimizer_calls
    optimizer_policy = CapabilityPolicyV2(
        tools=("read_file", "write_file", "commit_candidate"),
        read_paths=paths, write_paths=optimization_write_paths,
        backend="LOCAL", network_access=False,
    )
    verifier_policy = CapabilityPolicyV2(
        tools=(), read_paths=(), write_paths=(), backend="ISOLATED", network_access=False,
    )
    roles = RoleRegistryV1(
        registry_id="bananloop-v2-owner-researcher", version=1,
        roles=(
            RoleDefinitionV1(
                role_id="researcher", adapter="codex", provider="openai", model=model,
                model_version="siwc-responses-v1",
                prompt_digest=hashlib.sha256(INSTRUCTIONS.encode("utf-8")).hexdigest(),
                capability_policy=policy,
                context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                                "evidence_refs", "dev_summary", "budget_remaining"),
                min_instances=0, max_instances=1, calls_per_instance=researcher_calls,
                tokens_per_instance=researcher_calls * max_model_tokens,
            ),
            RoleDefinitionV1(
                role_id="independent_verifier", adapter="codex", provider="openai", model=model,
                model_version="siwc-responses-v1",
                prompt_digest=hashlib.sha256(PROTOCOL_VERIFIER_INSTRUCTIONS.encode("utf-8")).hexdigest(),
                capability_policy=verifier_policy,
                context_fields=("campaign_goal", "public_task"),
                min_instances=0, max_instances=1, calls_per_instance=1,
                tokens_per_instance=max_model_tokens,
            ),
            RoleDefinitionV1(
                role_id="optimizer", adapter="codex", provider="openai", model=model,
                model_version="siwc-responses-v1",
                prompt_digest=hashlib.sha256(OPTIMIZER_INSTRUCTIONS.encode("utf-8")).hexdigest(),
                capability_policy=optimizer_policy,
                context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                                "constraints", "evidence_refs", "dev_summary", "budget_remaining"),
                min_instances=0, max_instances=1, calls_per_instance=optimizer_calls,
                tokens_per_instance=optimizer_calls * max_model_tokens,
            ),
            RoleDefinitionV1(
                role_id="integration_verifier", adapter="codex", provider="openai", model=model,
                model_version="siwc-responses-v1",
                prompt_digest=hashlib.sha256(INTEGRATION_VERIFIER_INSTRUCTIONS.encode("utf-8")).hexdigest(),
                capability_policy=verifier_policy,
                context_fields=("campaign_goal", "public_task"),
                min_instances=0, max_instances=1, calls_per_instance=integration_calls,
                tokens_per_instance=integration_calls * max_model_tokens,
            ),
        ),
    )
    scope_digest = hashlib.sha256(json.dumps({
        "repo": str(repo), "goal": goal, "read_paths": paths,
        "model": model, "version": 1,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION", "goal": goal,
        "source_repo": str(repo), "base_commit": _git(repo, "rev-parse", "HEAD"),
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": scope_digest, "scope_version": 1,
            "capability_policy": policy,
        },
        "budget": {
            "max_agent_calls": max_agent_calls,
            "max_evaluator_calls": max_evaluator_calls,
            "deadline_seconds": deadline_seconds,
            "max_model_tokens": max_agent_calls * max_model_tokens,
        },
        "role_registry_digest": roles.digest(), "evaluation": None,
        "evaluator_fixture_digest": evaluator_fixture_digest,
    })
    return spec, roles, policy


def _initialize_campaign(database: Path, spec: ExplorationCampaignSpecV2) -> None:
    existed = database.exists()
    conn = sqlite3.connect(database, timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )}
        if "campaigns" in names:
            rows = conn.execute("SELECT spec_json,spec_hash FROM campaigns ORDER BY id").fetchall()
            if (len(rows) != 1 or rows[0][1] != spec.approved_hash()
                    or ExplorationCampaignSpecV2.model_validate_json(rows[0][0]).approved_hash()
                    != spec.approved_hash()):
                raise V2CampaignCLIError("existing v2 campaign spec differs from this request")
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 5:
                raise V2CampaignCLIError("existing campaign schema version is not v5")
            conn.commit()
            return
        if existed and names:
            raise V2CampaignCLIError("campaign database is partially initialized; preserve it for recovery")
        conn.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        create_v5_graph_schema(conn)
        conn.execute(
            "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,"
            "spec_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        now = utc_now()
        conn.execute(
            "INSERT INTO campaigns VALUES(1,?,?,?,?,?)",
            (json.dumps(spec.model_dump(mode="json"), sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False),
             spec.approved_hash(), "READY", now, now),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _write_live_report(state_root: Path, report: dict[str, Any]) -> str:
    reports = state_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    target = reports / ("v2-owner-run-" + str(report["attempt_id"]) + ".json")
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return str(target)


def _persist_role_registry(state_root: Path, roles: RoleRegistryV1) -> Path:
    target = state_root / "role-registry.json"
    encoded = roles.model_dump_json(indent=2) + "\n"
    if target.exists():
        try:
            stored = RoleRegistryV1.load(target, expected_digest=roles.digest())
        except Exception as exc:
            raise V2CampaignCLIError("persisted role registry differs from the frozen campaign") from exc
        if stored.digest() != roles.digest():
            raise V2CampaignCLIError("persisted role registry differs from the frozen campaign")
        return target
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, target)
    return target


def _stage_protocol_review_attestation(
    *, repo: Path, state_root: Path, spec_hash: str, requested_path: str | None,
) -> tuple[str, ...]:
    """Pin an owner-selected public benchmark attestation for verifier context."""
    from urllib.parse import urlparse

    evidence_root = state_root / "rpdi05"
    published_root = evidence_root / "published"
    record_path = evidence_root / "trusted-public-review-evidence.json"
    evidence_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    published_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    requested_digest = None
    if requested_path:
        supplied = Path(requested_path).expanduser()
        source = supplied.resolve() if supplied.is_absolute() else (repo / supplied).resolve()
        if supplied.is_symlink() or not source.is_relative_to(repo.resolve()) or not source.is_file():
            raise V2CampaignCLIError("protocol review attestation must be a regular file inside --repo")
        raw = source.read_bytes()
        if len(raw) > 4096:
            raise V2CampaignCLIError("protocol review attestation exceeds the 4 KiB bound")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise V2CampaignCLIError("protocol review attestation must be UTF-8 JSON") from None
        if not isinstance(payload, dict) or payload.get("schema") != "public-evaluator-attestation-v1":
            raise V2CampaignCLIError("protocol review attestation has an unsupported schema")
        sources = payload.get("sources")
        if not isinstance(payload.get("summary"), str) or not payload["summary"].strip() or not isinstance(sources, list) or not sources:
            raise V2CampaignCLIError("protocol review attestation lacks summary or pinned sources")
        for item in sources:
            if not isinstance(item, dict):
                raise V2CampaignCLIError("protocol review attestation source record is malformed")
            source_url = item.get("url")
            parsed = urlparse(source_url) if isinstance(source_url, str) else None
            if (parsed is None or parsed.scheme != "https"
                    or parsed.hostname not in {"github.com", "raw.githubusercontent.com"}
                    or not parsed.path.startswith("/bespokelabsai/")
                    or not isinstance(item.get("commit"), str)
                    or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", item["commit"])
                    or not isinstance(item.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                raise V2CampaignCLIError("protocol review attestation must pin official immutable source bytes")
        requested_digest = hashlib.sha256(raw).hexdigest()
        asset_path = published_root / requested_digest
        if asset_path.is_symlink():
            raise V2CampaignCLIError("protocol review attestation artifact is unsafe")
        if asset_path.exists():
            if not asset_path.is_file() or hashlib.sha256(asset_path.read_bytes()).hexdigest() != requested_digest:
                raise V2CampaignCLIError("persisted protocol review attestation failed integrity check")
        else:
            asset_path.write_bytes(raw)
            asset_path.chmod(0o400)

    existing = None
    if record_path.exists():
        if record_path.is_symlink() or not record_path.is_file():
            raise V2CampaignCLIError("persisted protocol review evidence record is unsafe")
        try:
            existing = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise V2CampaignCLIError("persisted protocol review evidence record is invalid") from None
        if (not isinstance(existing, dict)
                or existing.get("schema") != "trusted-public-review-evidence-v1"
                or existing.get("spec_hash") != spec_hash
                or not isinstance(existing.get("digests"), list)
                or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                       for item in existing["digests"])):
            raise V2CampaignCLIError("persisted protocol review evidence differs from the frozen campaign")
    previous = tuple(existing["digests"]) if existing else ()
    if requested_digest and previous and previous != (requested_digest,):
        raise V2CampaignCLIError("protocol review attestation changed after the campaign was frozen")
    digests = previous or ((requested_digest,) if requested_digest else ())
    if digests and existing is None:
        encoded = json.dumps({
            "schema": "trusted-public-review-evidence-v1",
            "spec_hash": spec_hash,
            "digests": list(digests),
        }, sort_keys=True, separators=(",", ":")) + "\n"
        temporary = record_path.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, record_path)
        record_path.chmod(0o400)
    for digest in digests:
        asset_path = published_root / digest
        if (asset_path.is_symlink() or not asset_path.is_file()
                or hashlib.sha256(asset_path.read_bytes()).hexdigest() != digest):
            raise V2CampaignCLIError("trusted public protocol-review evidence is missing or changed")
    return digests


def _selected_optimization_task(*, proposal: Any, graph: GraphRepository,
                                state_root: Path,
                                queue: DurableWorkerQueue,
                                campaign_id: str) -> OptimizationTaskV2:
    """Load the Core-ranked Researcher hypothesis that this approved protocol came with."""
    job = queue.get_job(proposal.source_job_id)
    durable = queue.get_result(proposal.source_job_id)
    if (job.get("campaign_id") != campaign_id or job.get("state") != "COMPLETED"
            or int(durable.get("fence", -1)) != int(job.get("fence", -2))
            or durable.get("output_digest") != hashlib.sha256(json.dumps(
                durable.get("result"), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")).hexdigest()):
        raise V2CampaignCLIError("approved protocol source Researcher job has no current durable receipt")
    result = durable.get("result")
    if not isinstance(result, dict):
        raise V2CampaignCLIError("approved protocol source Researcher output is missing")
    artifact = next((item for item in result.get("artifacts", ())
                     if item.get("path") == "research/hypothesis-portfolio.json"), None)
    if not isinstance(artifact, dict) or not isinstance(artifact.get("sha256"), str):
        raise V2CampaignCLIError("approved protocol source has no Core-ranked hypothesis portfolio")
    artifact_ref = f"artifact://sha256/{artifact['sha256']}"
    row = graph.conn.execute(
        "SELECT payload_json FROM events WHERE seq=? AND event_type='EXPLORATION_COMPLETED'",
        (proposal.source_event_cursor,),
    ).fetchone()
    if row is None:
        raise V2CampaignCLIError("Researcher portfolio has no committed completion event")
    graph_event = json.loads(row[0])
    if (graph_event.get("job_id") != proposal.source_job_id
            or artifact["sha256"] not in graph_event.get("artifact_digests", ())):
        raise V2CampaignCLIError("Researcher portfolio is absent from its committed evidence event")
    artifact_path = (state_root / "rpdi05" / "artifacts"
                     / f"{proposal.source_job_id}-f{durable['fence']}"
                     / "research" / "hypothesis-portfolio.json")
    if (artifact_path.is_symlink() or not artifact_path.is_file()
            or not artifact_path.resolve().is_relative_to(
                (state_root / "rpdi05" / "artifacts").resolve(),
            )):
        raise V2CampaignCLIError("Researcher hypothesis portfolio escaped its artifact root")
    raw = artifact_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
        raise V2CampaignCLIError("Researcher hypothesis portfolio digest changed after completion")
    try:
        portfolio = json.loads(raw)
        selected_id = portfolio["core_selection"]["selected_hypothesis_id"]
        selected = next(item for item in portfolio["candidates"] if item["id"] == selected_id)
        resolved_evidence_refs: set[str] = {artifact_ref}
        for evidence_id in selected.get("evidence_refs", ()):
            if isinstance(evidence_id, str) and evidence_id.startswith("artifact://sha256/"):
                resolved_evidence_refs.add(evidence_id)
                continue
            if not isinstance(evidence_id, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_id):
                raise V2CampaignCLIError("selected hypothesis cites an invalid research evidence id")
            evidence = graph.conn.execute(
                "SELECT campaign_id, source_job_id, source_fence, output_digest, event_cursor, "
                "evidence_refs_json, classification FROM research_evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
            if evidence is None or evidence["campaign_id"] != campaign_id or evidence["classification"] != "PUBLIC_DEV":
                raise V2CampaignCLIError("selected hypothesis cites evidence outside public DEV scope")
            evidence_job = queue.get_job(evidence["source_job_id"])
            evidence_result = queue.get_result(evidence["source_job_id"])
            if (evidence_job.get("campaign_id") != campaign_id
                    or evidence_job.get("state") != "COMPLETED"
                    or int(evidence_job.get("fence", -1)) != int(evidence["source_fence"])
                    or int(evidence_result.get("fence", -1)) != int(evidence["source_fence"])
                    or evidence_result.get("output_digest") != evidence["output_digest"]):
                raise V2CampaignCLIError("selected hypothesis evidence has no current durable job receipt")
            evidence_event_row = graph.conn.execute(
                "SELECT payload_json FROM events WHERE seq=? AND event_type='EXPLORATION_COMPLETED'",
                (evidence["event_cursor"],),
            ).fetchone()
            evidence_event = json.loads(evidence_event_row[0]) if evidence_event_row else None
            if (not isinstance(evidence_event, dict)
                    or evidence_event.get("job_id") != evidence["source_job_id"]
                    or evidence_event.get("output_digest") != evidence["output_digest"]):
                raise V2CampaignCLIError("selected hypothesis evidence is absent from committed graph history")
            evidence_result_body = evidence_result.get("result")
            if not isinstance(evidence_result_body, dict):
                raise V2CampaignCLIError("selected hypothesis evidence job has no durable result body")
            source_artifacts = {
                item.get("sha256"): item.get("path")
                for item in evidence_result_body.get("artifacts", ())
                if isinstance(item, dict)
            }
            evidence_refs = json.loads(evidence["evidence_refs_json"])
            if not isinstance(evidence_refs, list):
                raise V2CampaignCLIError("selected hypothesis evidence references are malformed")
            for evidence_ref in evidence_refs:
                match = re.fullmatch(r"artifact://sha256/([0-9a-f]{64})", evidence_ref)
                if not match or match.group(1) not in source_artifacts:
                    raise V2CampaignCLIError("selected hypothesis evidence does not resolve to a source artifact")
                evidence_job_id = evidence["source_job_id"]
                evidence_fence = evidence["source_fence"]
                source_path = (state_root / "rpdi05" / "artifacts"
                               / f"{evidence_job_id}-f{evidence_fence}"
                               / source_artifacts[match.group(1)])
                artifact_root = (state_root / "rpdi05" / "artifacts").resolve()
                if (source_path.is_symlink() or not source_path.is_file()
                        or not source_path.resolve().is_relative_to(artifact_root)
                        or hashlib.sha256(source_path.read_bytes()).hexdigest() != match.group(1)):
                    raise V2CampaignCLIError("selected hypothesis evidence artifact failed integrity check")
                resolved_evidence_refs.add(evidence_ref)
        task = OptimizationTaskV2(
            hypothesis_id=selected_id, hypothesis=selected["hypothesis"],
            source_campaign_id=campaign_id,
            source_node_id=proposal.source_node_id,
            source_job_id=proposal.source_job_id,
            source_snapshot_id=proposal.source_snapshot_id,
            source_artifact_sha256=artifact["sha256"],
            evidence_refs=tuple(resolved_evidence_refs),
        )
    except Exception as exc:
        raise V2CampaignCLIError("Core-ranked Researcher hypothesis portfolio is malformed") from exc
    return task


def _record_verified_optimization_pivot(*, repo: Path, state_root: Path,
                                        registry_root: Path,
                                        campaign_id: str, parent_spec: ExplorationCampaignSpecV2,
                                        graph: GraphRepository, queue: DurableWorkerQueue,
                                        roles: RoleRegistryV1,
                                        visible_paths: tuple[str, ...],
                                        write_paths: tuple[str, ...],
                                        approval_digest: str) -> dict[str, Any]:
    from .evaluator_verification import EvaluatorProtocolAuthority
    from .v2_pivot_saga import V2PivotSaga, _budget_caps

    if not write_paths:
        return {"status": "NEEDS_EXPLICIT_OPTIMIZATION_WRITE_PATH"}
    authority = EvaluatorProtocolAuthority(graph.conn)
    proposal, receipt = authority.approved_protocol(approval_digest)
    optimization_task = _selected_optimization_task(
        proposal=proposal, graph=graph, state_root=state_root, queue=queue,
        campaign_id=campaign_id,
    )
    researcher_calls = roles.role("researcher").calls_per_instance
    optimizer_calls = roles.role("optimizer").calls_per_instance
    integration_calls = roles.role("integration_verifier").calls_per_instance
    child_calls = optimizer_calls + integration_calls
    if researcher_calls + roles.role("independent_verifier").calls_per_instance + child_calls > parent_spec.budget.max_agent_calls:
        raise V2CampaignCLIError("frozen role call vector exceeds the parent budget")
    scope = AutonomyScopeV2(
        version=1, allowed_paths=write_paths, max_child_depth=0, max_child_campaigns=0,
    )
    child_policy = CapabilityPolicyV2(
        tools=("read_file", "write_file", "commit_candidate"),
        read_paths=visible_paths, write_paths=write_paths,
        backend="LOCAL", network_access=False,
    )
    payload = parent_spec.model_dump(mode="json")
    payload["mode"] = "OPTIMIZATION"
    payload["goal"] = (
        f"Optimize Researcher hypothesis {optimization_task.hypothesis_id}: "
        f"{optimization_task.hypothesis}"
    )[:4000]
    payload["optimization_task"] = optimization_task.model_dump(mode="json")
    payload["evaluation"] = {
        "protocol_id": proposal.protocol.protocol_id,
        "version": proposal.protocol.version,
        "content_sha256": proposal.protocol.content_digest(),
        "approval_digest": receipt.approval_digest,
    }
    payload["autonomy"] = {
        "mode": parent_spec.autonomy.mode,
        "scope_digest": scope.content_digest(), "scope_version": scope.version,
        "scope": scope.model_dump(mode="json"),
        "capability_policy": child_policy.model_dump(mode="json"),
    }
    payload["budget"]["max_agent_calls"] = child_calls
    # One supervised pivot currently represents one candidate experiment. Fund
    # its complete trusted lifecycle: candidate DEV and baseline/candidate
    # VALIDATION, plus HOLDOUT only when the frozen protocol defines that tier.
    # Each receipt requires the protocol's fixed repeatability count.
    # The scheduler records the Optimization task itself and the single
    # candidate as separate experiment work, so reserve two slots here.
    payload["budget"]["max_experiments"] = 2
    has_holdout = any(item.tier == "holdout" for item in proposal.protocol.tiers)
    required_evaluator_calls = _required_optimization_evaluator_calls(
        proposal.protocol.repeatability_runs, has_holdout=has_holdout,
    )
    if parent_spec.budget.max_evaluator_calls < required_evaluator_calls:
        raise V2CampaignCLIError(
            "parent evaluator budget cannot fund the approved one-candidate lifecycle: "
            f"requires {required_evaluator_calls} calls for repeatability="
            f"{proposal.protocol.repeatability_runs}, has "
            f"{parent_spec.budget.max_evaluator_calls}"
        )
    payload["budget"]["max_evaluator_calls"] = required_evaluator_calls
    if parent_spec.budget.max_model_tokens is not None:
        per_call = parent_spec.budget.max_model_tokens // parent_spec.budget.max_agent_calls
        payload["budget"]["max_model_tokens"] = child_calls * per_call
    child = OptimizationCampaignSpecV2.model_validate(payload)
    plan = PivotExecutionPlanV2.build(
        child_spec=child.model_dump(mode="json"), changed_paths=write_paths,
        resource_vector=_budget_caps(child),
    )
    completed_count = sum(
        node.get("status") == "COMPLETED"
        for node in graph.scheduling_snapshot()["nodes"]
    )
    pivot = PivotProposal.build(
        spec_hash=parent_spec.approved_hash(),
        trigger_experiment_ordinal=max(1, completed_count),
        current_architecture="Exploration campaign without an Optimization child",
        proposed_architecture="Protocol-backed v2 Optimization child",
        rationale=("A distinct pinned independent_verifier approved the frozen public evaluator protocol "
                   f"with approval digest {receipt.approval_digest}. The child is bound to the "
                   f"Core-selected Researcher hypothesis {optimization_task.hypothesis_id}: "
                   f"{optimization_task.hypothesis}"),
        expected_effect="Run bounded candidate edits against the frozen trusted evaluator.",
        changed_scope=write_paths,
        resources=PivotResourceRequest(
            additional_agent_calls=child_calls,
            additional_evaluator_calls=child.budget.max_evaluator_calls,
            notes="Exact child limits are frozen in execution_plan.",
        ),
        execution_plan=plan,
    )
    queue.set_campaign_control(
        campaign_id, "PAUSED", reason=f"verified Optimization pivot {pivot.proposal_id} requires owner decision",
    )
    with sqlite3.connect(state_root / "campaign.sqlite3", timeout=30) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()
        if row is None or row[0] not in {"READY", "PAUSED_NEEDS_INPUT"}:
            conn.rollback()
            raise V2CampaignCLIError("parent campaign cannot enter supervised pivot review from its current status")
        if row[0] == "READY":
            conn.execute(
                "UPDATE campaigns SET status='PAUSED_NEEDS_INPUT',updated_at=? WHERE id=1",
                (utc_now(),),
            )
            conn.execute(
                "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
                ("V2_PIVOT_REVIEW_REQUIRED", json.dumps({
                    "campaign_id": campaign_id, "proposal_id": pivot.proposal_id,
                    "proposal_digest": pivot.digest,
                }, sort_keys=True, separators=(",", ":")), utc_now()),
            )
        conn.commit()
    saga = V2PivotSaga(repo=repo, parent_campaign_id=campaign_id, state_root=registry_root)
    saga.record_pivot_proposal(pivot)
    roles_path = _persist_role_registry(state_root, roles)
    with sqlite3.connect(state_root / "campaign.sqlite3", timeout=30) as conn:
        pivot_status = conn.execute(
            "SELECT status FROM v2_pivot_proposals WHERE proposal_id=?", (pivot.proposal_id,),
        ).fetchone()
    if pivot_status is None:
        raise V2CampaignCLIError("durable Optimization pivot proposal disappeared after recording")
    status = {
        "PENDING": "PENDING_OWNER_PIVOT_DECISION",
        "APPROVED": "OWNER_APPROVED_CHILD_READY_TO_EXECUTE",
        "REJECTED": "OWNER_REJECTED_PIVOT",
        "STALE": "STALE_PIVOT_REQUIRES_REVIEW",
    }[pivot_status[0]]
    return {
        "status": status, "proposal_id": pivot.proposal_id,
        "proposal_digest": pivot.digest, "protocol_approval_digest": receipt.approval_digest,
        "child_spec_hash": child.approved_hash(), "changed_paths": list(write_paths),
        "child_agent_call_budget": child_calls, "role_registry_path": str(roles_path),
    }


def _record_provider_call(database: Path, receipt: dict[str, Any]) -> None:
    conn = sqlite3.connect(database, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
            ("V2_PROVIDER_CALL", json.dumps(receipt, sort_keys=True, separators=(",", ":")),
             utc_now()),
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _complete_pending_protocol_reviews(*, graph: GraphRepository, queue: DurableWorkerQueue,
                                       campaign_id: str, published_root: Path,
                                       auth: ChatGPTPlanAuth, model: str, timeout_seconds: int,
                                       remaining_calls: int,
                                       call_observer: Any,
                                       trusted_public_evidence_digests: tuple[str, ...] = ()) -> dict[str, Any]:
    """Recover proposal records and spend only the explicitly available verifier calls."""
    from .evaluator_verification import EvaluatorProtocolAuthority, EvaluatorVerificationAdapter

    authority = EvaluatorProtocolAuthority(graph.conn)
    for row in queue.conn.execute(
        "SELECT job_id FROM worker_jobs WHERE campaign_id=? AND state='COMPLETED' ORDER BY job_id",
        (campaign_id,),
    ):
        job_id = row[0]
        try:
            durable = queue.get_result(job_id)
        except Exception:
            continue
        output = durable.get("result") if isinstance(durable, dict) else None
        if isinstance(output, dict) and output.get("protocol_draft") is not None:
            authority.record_completed_proposal(graph, queue, job_id)

    adapter = EvaluatorVerificationAdapter(
        graph=graph, queue=queue, campaign_id=campaign_id,
        published_artifact_root=published_root,
        trusted_public_evidence_digests=trusted_public_evidence_digests,
    )
    verifier = ChatGPTPlanProtocolVerifier(
        auth, approved_model=model, timeout_seconds=timeout_seconds,
        call_observer=call_observer,
    )
    results: list[dict[str, Any]] = []
    used_calls = 0
    for proposal in authority.pending_proposals(campaign_id):
        job = adapter.enqueue_verifier(proposal.proposal_digest)
        verifier_job_id = job["job_id"]
        if job["state"] == "COMPLETED":
            receipt = authority.record_completed_verifier_job(queue, verifier_job_id)
            results.append({"proposal_digest": proposal.proposal_digest,
                            "verifier_job_id": verifier_job_id, "decision": receipt.decision,
                            "approval_digest": receipt.approval_digest,
                            "checks": receipt.checks, "rationale": receipt.rationale,
                            "public_diagnostics": receipt.public_diagnostics})
            continue
        if job["state"] != "READY":
            results.append({"proposal_digest": proposal.proposal_digest,
                            "verifier_job_id": verifier_job_id, "status": job["state"]})
            continue
        if used_calls >= remaining_calls:
            results.append({"proposal_digest": proposal.proposal_digest,
                            "verifier_job_id": verifier_job_id,
                            "status": "AWAITING_OWNER_CALL_APPROVAL"})
            continue
        binding = job["request"].get("role_binding")
        manifest_authority = TeamManifestAuthority(queue.path)
        try:
            manifest = manifest_authority.load(
                campaign_id, spec_hash=binding["spec_hash"],
                registry_digest=binding["role_registry_digest"],
            )
        finally:
            manifest_authority.close()
        executor = verifier.executor(manifest, binding["role_instance_id"])
        history_before = len(verifier.request_history)
        receipt = adapter.run_verifier_one(
            verifier_job_id=verifier_job_id, worker_id="v2-protocol-verifier",
            executor=executor,
        )
        used_calls += sum(
            bool(item.get("dispatched")) for item in verifier.request_history[history_before:]
        )
        results.append({"proposal_digest": proposal.proposal_digest,
                        "verifier_job_id": verifier_job_id, "decision": receipt.decision,
                        "approval_digest": receipt.approval_digest,
                        "checks": receipt.checks, "rationale": receipt.rationale,
                        "public_diagnostics": receipt.public_diagnostics})
    return {"calls_used": used_calls, "reviews": results,
            "pending_proposal_digests": [item.proposal_digest
                                          for item in authority.pending_proposals(campaign_id)],
            "approved_protocols": [
                {"proposal_digest": row[0],
                 "approval_digest": json.loads(row[1])["approval_digest"]}
                for row in graph.conn.execute(
                    "SELECT proposal_digest,receipt_json FROM evaluator_verifier_receipts "
                    "WHERE campaign_id=? AND decision='APPROVE' ORDER BY proposal_digest",
                    (campaign_id,),
                )
            ]}


def _remaining_researcher_calls(queue: DurableWorkerQueue, campaign_id: str,
                                capacity: int) -> int:
    used = queue.conn.execute(
        "SELECT COALESCE(SUM(t.upper_bound),0) FROM team_role_call_jobs t "
        "JOIN worker_jobs j USING(job_id) "
        "WHERE t.campaign_id=? AND t.role_id='researcher' AND j.state!='READY' "
        "AND t.job_id NOT IN (SELECT job_id FROM team_role_allocation_releases)",
        (campaign_id,),
    ).fetchone()[0]
    return max(0, capacity - int(used))


def _aggregate_reported_usage(call_history: list[dict[str, Any]]) -> dict[str, int | str] | str:
    """Sum known scalar token counters while ignoring provider-specific nested detail."""
    records = [item.get("usage") for item in call_history if item.get("dispatched")]
    if not records or any(not isinstance(item, dict) or not item for item in records):
        return "UNKNOWN"
    totals: dict[str, int | str] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [item.get(key) for item in records]
        if all(type(value) is int and value >= 0 for value in values):
            totals[key] = sum(values)
        else:
            totals[key] = "UNKNOWN"
    if all(value == "UNKNOWN" for value in totals.values()):
        return "UNKNOWN"
    return totals


def _model_token_budget_status(
    call_history: list[dict[str, Any]], *, per_call_limit: int,
    campaign_limit: int,
) -> dict[str, Any]:
    """Report observed provider-token overruns, which SIWC cannot prevent before dispatch."""
    records = [item for item in call_history if item.get("dispatched")]
    token_counts = [
        item.get("usage", {}).get("total_tokens")
        if isinstance(item.get("usage"), dict) else None
        for item in records
    ]
    known = bool(records) and all(type(value) is int and value >= 0 for value in token_counts)
    total = sum(token_counts) if known else None
    per_call_overrun = (
        any(value > per_call_limit for value in token_counts) if known else None
    )
    campaign_overrun = total > campaign_limit if total is not None else None
    breached = (per_call_overrun is True or campaign_overrun is True)
    return {
        "model_token_usage_known": known,
        "model_tokens_observed": total,
        "per_call_model_token_limit": per_call_limit,
        "campaign_model_token_limit": campaign_limit,
        "per_call_model_token_limit_breached": per_call_overrun,
        "campaign_model_token_limit_breached": campaign_overrun,
        "budget_breached": breached,
    }


def _protocol_review_terminal_status(
    *, pending: list[str], reviews: list[dict[str, Any]],
    approved_protocols: list[dict[str, Any]], has_write_scope: bool,
) -> str | None:
    """Derive the owner-facing status from durable verifier receipts."""
    if pending:
        return "PROTOCOL_REVIEW_PENDING"
    if approved_protocols:
        return (
            "PROTOCOL_APPROVED_AWAITING_OWNER_PIVOT_DECISION"
            if has_write_scope else "PROTOCOL_APPROVED_AWAITING_OPTIMIZATION_SCOPE"
        )
    if any(item.get("decision") == "REJECT" for item in reviews):
        return "PROTOCOL_REJECTED"
    return None


def _safe_failure_diagnostics(exc: Exception) -> list[dict[str, Any]]:
    """Return schema locations/messages without copying untrusted response values."""
    if isinstance(exc, V2RuntimeError):
        # V2RuntimeError messages are fixed Core contract failures; they never
        # interpolate model-authored text, source contents, or provider bodies.
        return [{"location": [], "type": "v2_runtime_contract",
                 "message": str(exc)[:400]}]
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return []
    try:
        records = errors(include_input=False, include_context=False, include_url=False)
    except TypeError:
        records = errors()
    return [{
        "location": [str(part)[:120] for part in item.get("loc", ())[:12]],
        "type": str(item.get("type", "unknown"))[:120],
        "message": str(item.get("msg", "validation failed"))[:400],
    } for item in records[:12] if isinstance(item, dict)]


def run_v2_explore(args: Any) -> dict[str, Any]:
    """Create/reopen one frozen Full v2 Exploration campaign and advance it."""
    goal = args.goal
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000 or "\x00" in goal:
        raise V2CampaignCLIError("--goal must contain 1 to 4000 non-NUL characters")
    if (isinstance(args.max_agent_calls, bool) or not 4 <= args.max_agent_calls <= 100
            or isinstance(getattr(args, "max_evaluator_calls", 16), bool)
            or not 1 <= getattr(args, "max_evaluator_calls", 16) <= 1000
            or isinstance(args.max_model_tokens, bool) or not 1 <= args.max_model_tokens <= 100_000
            or isinstance(getattr(args, "deadline_seconds", 3600), bool)
            or not 1 <= getattr(args, "deadline_seconds", 3600) <= 86_400
            or isinstance(args.approve_up_to_calls, bool)
            or not 0 <= args.approve_up_to_calls <= args.max_agent_calls
            or isinstance(args.max_iterations, bool) or not 1 <= args.max_iterations <= 100
            or isinstance(args.timeout_seconds, bool) or not 1 <= args.timeout_seconds <= 300
            or not isinstance(args.model, str) or not args.model.strip() or len(args.model) > 200):
        raise V2CampaignCLIError("v2 call, token, or iteration bounds are invalid")
    if args.approve_up_to_calls > args.max_agent_calls:
        raise V2CampaignCLIError("--approve-up-to-calls cannot exceed --max-agent-calls")
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir() or repo.is_symlink():
        raise V2CampaignCLIError("--repo must be a real local Git repository directory")
    paths = _read_paths(repo, args.read_path)
    experiment_executor = None
    experiment_catalog: tuple[dict[str, str], ...] = ()
    boardgames_task_id = getattr(args, "boardgames_task_id", None)
    if boardgames_task_id is not None:
        if args.max_iterations > 5 or getattr(args, "deadline_seconds", 3600) > 3600:
            raise V2CampaignCLIError(
                "Board Games pilot is capped at five iterations and one hour per campaign"
            )
        _require_committed_pilot_inputs(repo, (
            "benchmarks/discoverybench_boardgames/runner.py",
            "benchmarks/discoverybench_boardgames/pilot_manifest.json",
            "examples/exploration_diagnostic_v1/discoverybench_boardgames/data.csv",
        ))
        runner_path = repo / "benchmarks" / "discoverybench_boardgames" / "runner.py"
        if runner_path.is_symlink() or not runner_path.is_file() or not runner_path.resolve().is_relative_to(repo):
            raise V2CampaignCLIError("registered Board Games experiment runner is missing or unsafe")
        runner_spec = importlib.util.spec_from_file_location(
            "bananloop_boardgames_registered_runner", runner_path,
        )
        if runner_spec is None or runner_spec.loader is None:
            raise V2CampaignCLIError("registered Board Games experiment runner cannot be loaded")
        runner_module = importlib.util.module_from_spec(runner_spec)
        runner_spec.loader.exec_module(runner_module)
        try:
            experiment_executor = runner_module.BoardGamesExperimentExecutor(repo, boardgames_task_id)
        except (OSError, ValueError) as exc:
            raise V2CampaignCLIError(f"--boardgames-task-id is invalid: {exc}") from exc
        experiment_catalog = experiment_executor.catalog
    requested_write_paths = list(getattr(args, "optimization_write_path", ()) or ())
    write_paths = _read_paths(repo, requested_write_paths) if requested_write_paths else ()
    if not set(write_paths) <= set(paths):
        raise V2CampaignCLIError("--optimization-write-path values must also appear in --read-path")
    fixture = None
    fixture_path_arg = getattr(args, "evaluator_fixture", None)
    if fixture_path_arg:
        try:
            fixture = EvaluatorFixtureV1.load(fixture_path_arg)
        except ValueError as exc:
            raise V2CampaignCLIError(f"--evaluator-fixture is invalid: {exc}") from exc
    spec, roles, _policy = _build_spec(
        repo, goal, paths, max_agent_calls=args.max_agent_calls,
        max_model_tokens=args.max_model_tokens, model=args.model,
        max_evaluator_calls=getattr(args, "max_evaluator_calls", 16),
        max_iterations=args.max_iterations,
        deadline_seconds=getattr(args, "deadline_seconds", 3600),
        optimization_write_paths=write_paths,
        evaluator_fixture_digest=fixture.content_digest() if fixture else None,
    )
    runtime_paths = RuntimePaths.for_repo(
        repo, Path(args.state_dir).expanduser() if args.state_dir else None,
    )
    registry = CampaignRegistry(repo, paths=runtime_paths)
    entry, state_root, _created = registry.reserve(goal, args.request_id, activate=False)
    database = state_root / "campaign.sqlite3"
    _initialize_campaign(database, spec)
    trusted_public_evidence_digests = _stage_protocol_review_attestation(
        repo=repo, state_root=state_root, spec_hash=spec.approved_hash(),
        requested_path=getattr(args, "protocol_review_attestation", None),
    )
    if fixture is not None:
        fixture_bytes = fixture.canonical_bytes()
        fixture_digest = fixture.content_digest()
        fixture_dir = state_root / "rpdi05" / "published"
        fixture_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fixture_asset = fixture_dir / fixture_digest
        if fixture_asset.is_symlink():
            raise V2CampaignCLIError("persisted evaluator fixture asset is a symlink")
        if fixture_asset.exists():
            if not fixture_asset.is_file() or hashlib.sha256(fixture_asset.read_bytes()).hexdigest() != fixture_digest:
                raise V2CampaignCLIError("persisted evaluator fixture asset failed integrity check")
        else:
            fixture_asset.write_bytes(fixture_bytes)
            fixture_asset.chmod(0o400)
        fixture_copy = state_root / "trusted-evaluator-fixture.json"
        if fixture_copy.is_symlink():
            raise V2CampaignCLIError("persisted evaluator fixture copy is a symlink")
        if fixture_copy.exists():
            try:
                if EvaluatorFixtureV1.load(fixture_copy).content_digest() != fixture_digest:
                    raise V2CampaignCLIError("evaluator fixture differs from the frozen campaign digest")
            except ValueError as exc:
                raise V2CampaignCLIError("persisted evaluator fixture is invalid") from exc
        else:
            fixture_copy.write_bytes(fixture_bytes)
            fixture_copy.chmod(0o600)
    roles_path = _persist_role_registry(state_root, roles)

    ledger = registry.budget_ledger()
    try:
        root_caps = {
            "agent_calls": args.max_agent_calls,
            "evaluator_calls": spec.budget.max_evaluator_calls,
            "experiments": spec.budget.max_experiments,
            "attempts": spec.budget.max_attempts,
            "model_tokens": args.max_agent_calls * args.max_model_tokens,
        }
        if spec.budget.max_evaluation_units is not None:
            root_caps["evaluation_units"] = spec.budget.max_evaluation_units
        ledger.create_root(entry.campaign_id, root_caps)
    finally:
        ledger.close()

    graph = GraphRepository.open(str(database), campaign_id=entry.campaign_id)
    queue = registry.worker_queue()
    try:
        if not graph.scheduling_snapshot()["nodes"]:
            graph.mutate({
                "command_id": "owner-initial-exploration-" + entry.campaign_id,
                "idempotency_key": "owner-initial-exploration-" + entry.campaign_id,
                "expected_graph_revision": graph.graph_revision(),
                "changes": [{
                    "type": "AddNode", "node_id": "explore-initial", "phase": "EXPLORATION",
                    "payload": {"hypothesis": goal, "iteration": 0},
                }],
            })
        runtime = V2ResearchRuntime(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            state_root=state_root, role_registry=roles,
            trusted_artifact_digests=((fixture.content_digest(),) if fixture else ()),
        )
        if args.approve_up_to_calls == 0:
            return {
                "status": "READY_FOR_OWNER_APPROVAL", "mode": "EXPLORATION",
                "profile": "FULL", "campaign_id": entry.campaign_id,
                "spec_hash": spec.approved_hash(), "base_commit": spec.base_commit,
                "read_paths": list(paths), "state_dir": str(state_root),
                "role_registry_path": str(roles_path),
                "protocol_review_evidence_digests": list(trusted_public_evidence_digests),
                "live_model_called": False,
            }

        auth = ChatGPTPlanAuth()
        if not auth.status()["connected"]:
            raise V2CampaignCLIError("ChatGPT is not connected; use bananloop-chatgpt status first")
        attempt_id = "a-" + hashlib.sha256(
            f"{entry.campaign_id}:{args.request_id}:{utc_now()}".encode(),
        ).hexdigest()[:24]

        call_history: list[dict[str, Any]] = []

        def observe_call(role_id: str, receipt: dict[str, Any]) -> None:
            recorded = {"attempt_id": attempt_id, "role_id": role_id, **receipt}
            call_history.append(recorded)
            _record_provider_call(database, recorded)

        # Core inserts this exact registered catalog after reading the frozen
        # Board Games task config; it is not worker-selected data.
        researcher = ChatGPTPlanResearcher(
            auth, approved_model=args.model, timeout_seconds=args.timeout_seconds,
            call_observer=lambda receipt: observe_call("researcher", receipt),
            input_fields=_researcher_input_fields(
                allow_registered_experiments=experiment_executor is not None,
                require_protocol_intent=fixture is not None,
            ),
        )
        try:
            researcher.check_model()
        except SIWCError as exc:
            raise V2CampaignCLIError(str(exc)) from exc
        deadline_epoch = _campaign_deadline(
            state_root, campaign_id=entry.campaign_id, spec_hash=spec.approved_hash(),
            time_limit_seconds=spec.budget.deadline_seconds,
        )
        runtime.adapter.campaign_deadline_epoch = deadline_epoch
        try:
            verifier_calls = 0
            reviews_before = _complete_pending_protocol_reviews(
                graph=graph, queue=queue, campaign_id=entry.campaign_id,
                published_root=state_root / "rpdi05" / "published", auth=auth,
                trusted_public_evidence_digests=trusted_public_evidence_digests,
                model=args.model, timeout_seconds=args.timeout_seconds,
                remaining_calls=args.approve_up_to_calls,
                call_observer=lambda receipt: observe_call("independent_verifier", receipt),
            )
            verifier_calls += int(reviews_before["calls_used"])
            remaining_approved = max(0, args.approve_up_to_calls - verifier_calls)
            researcher_capacity = _remaining_researcher_calls(
                queue, entry.campaign_id, args.max_agent_calls - 1,
            )
            protocol_gate_closed = bool(
                reviews_before["pending_proposal_digests"]
                or (fixture is not None and reviews_before["approved_protocols"])
            )
            iterations = (0 if protocol_gate_closed else min(
                args.max_iterations, remaining_approved, researcher_capacity,
            ))
            if iterations:
                result = runtime.run_campaign(
                    visible_paths=paths, invoke=make_researcher_invoke(
                        researcher,
                        protocol_binder=(
                            (lambda intent, source: fixture.bind(
                                intent, source_snapshot_id=source,
                            )) if fixture else None
                        ),
                        experiment_executor=experiment_executor,
                        experiment_catalog=experiment_catalog,
                    ),
                    max_iterations=iterations,
                )
            else:
                result = {
                    "status": (
                        "PROTOCOL_REVIEW_PENDING" if reviews_before["pending_proposal_digests"]
                        else "PROTOCOL_APPROVED_AWAITING_OWNER" if protocol_gate_closed
                        else "CALL_ALLOWANCE_EXHAUSTED" if remaining_approved == 0
                        else "RESEARCH_ROLE_ALLOCATION_EXHAUSTED"
                    ),
                    "campaign_id": entry.campaign_id, "iterations_this_call": 0,
                    "completed": [],
                }
            researcher_calls = sum(
                bool(item.get("dispatched")) for item in call_history
                if item.get("role_id") == "researcher"
            )
            reviews_after = _complete_pending_protocol_reviews(
                graph=graph, queue=queue, campaign_id=entry.campaign_id,
                published_root=state_root / "rpdi05" / "published", auth=auth,
                trusted_public_evidence_digests=trusted_public_evidence_digests,
                model=args.model, timeout_seconds=args.timeout_seconds,
                remaining_calls=max(0, args.approve_up_to_calls - verifier_calls - researcher_calls),
                call_observer=lambda receipt: observe_call("independent_verifier", receipt),
            )
            result["protocol_reviews"] = reviews_after["reviews"]
            result["pending_protocol_proposals"] = reviews_after["pending_proposal_digests"]
            terminal_protocol_status = _protocol_review_terminal_status(
                pending=result["pending_protocol_proposals"],
                reviews=reviews_after["reviews"],
                approved_protocols=reviews_after["approved_protocols"],
                has_write_scope=bool(write_paths),
            )
            if terminal_protocol_status is not None:
                result["status"] = terminal_protocol_status
            result["optimization_pivot"] = []
            approval_digests = {
                item["approval_digest"]
                for source in (reviews_before, reviews_after)
                for item in source.get("approved_protocols", ())
            }
            for approval_digest in sorted(approval_digests):
                result["optimization_pivot"].append(_record_verified_optimization_pivot(
                    repo=repo, state_root=state_root, campaign_id=entry.campaign_id,
                    registry_root=runtime_paths.root,
                    parent_spec=spec, graph=graph, queue=queue, roles=roles,
                    visible_paths=paths, write_paths=write_paths,
                    approval_digest=approval_digest,
                ))
            if any(item.get("status") == "PENDING_OWNER_PIVOT_DECISION"
                   for item in result["optimization_pivot"]):
                result["status"] = "PENDING_OWNER_PIVOT_DECISION"
        except Exception as exc:
            token_budget = _model_token_budget_status(
                call_history, per_call_limit=args.max_model_tokens,
                campaign_limit=int(spec.budget.max_model_tokens or 0),
            )
            report = {
                "attempt_id": attempt_id, "campaign_id": entry.campaign_id,
                "status": "BUDGET_BREACHED" if token_budget["budget_breached"] else "RUN_FAILED",
                "provider": "chatgpt_plan", "model": args.model,
                "request_history": call_history,
                "usage": _aggregate_reported_usage(call_history),
                "failure_type": type(exc).__name__,
                "automatic_replay": False,
                **token_budget,
            }
            diagnostics = _safe_failure_diagnostics(exc)
            if diagnostics:
                report["failure_diagnostics"] = diagnostics
            report["report_path"] = _write_live_report(state_root, report)
            return {
                **report, "state_dir": str(state_root),
                "live_model_called": bool(report["request_history"]),
            }
        token_budget = _model_token_budget_status(
            call_history, per_call_limit=args.max_model_tokens,
            campaign_limit=int(spec.budget.max_model_tokens or 0),
        )
        if token_budget["budget_breached"]:
            result["status"] = "BUDGET_BREACHED"
        response = {
            **result, "mode": "EXPLORATION", "profile": "FULL",
            "spec_hash": spec.approved_hash(), "base_commit": spec.base_commit,
            "read_paths": list(paths), "optimization_write_paths": list(write_paths),
            "role_registry_path": str(roles_path), "state_dir": str(state_root),
            "protocol_review_evidence_digests": list(trusted_public_evidence_digests),
            "provider": "chatgpt_plan", "model": args.model,
            "live_model_called": any(item.get("dispatched") for item in call_history),
            "usage": _aggregate_reported_usage(call_history),
            "request_id": researcher.last_request_id,
            "request_history": call_history,
            "attempt_id": attempt_id,
            "automatic_replay": False,
            **token_budget,
        }
        response["report_path"] = _write_live_report(state_root, {
            **response, "status": response.get("status", "UNKNOWN"),
        })
        return response
    finally:
        queue.close()
        graph.close()
