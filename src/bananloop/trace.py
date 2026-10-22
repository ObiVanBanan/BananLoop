"""Shareable, read-only trace export projected from durable campaign state."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .report import build_report
from .evaluator_binding import EvaluatorBinding, canonical_hash
from .store import CampaignStore, StoreError


_HASH = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_STAGES = {"READY", "BASELINE", "PROPOSE", "SELECT", "EXECUTE", "VERIFY", "EVALUATE", "VALIDATE", "ANALYZE", "FINAL_HOLDOUT", "REPORT", "STOPPED", "GOAL_MET", "REJECT"}
_STATUSES = {"READY", "RUNNING", "PAUSED", "STOPPED", "GOAL_MET", "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN", "ACCEPTED", "REJECTED"}
_KINDS = {"agent", "evaluator", "final_evaluator", "bootstrap", "planning"}
_STOP_REASONS = {
    "GOAL_MET", "BUDGET_EXHAUSTED", "DEADLINE_EXCEEDED", "PLATEAU", "NO_HYPOTHESIS",
    "BASELINE_ALREADY_MET_TARGET", "STOP_REQUESTED", "FINAL_HOLDOUT_REJECTED",
    "PAUSED_NEEDS_INPUT", "PAUSED_INFRA", "UNKNOWN",
}
_FAILURE_CATEGORIES = {
    "IMPLEMENTATION_BLOCKED", "NO_CHANGES", "CANDIDATE_NOT_MATERIALIZED", "AGENT_PROTOCOL_ERROR",
    "INVALID_EVALUATION", "INVALID_VALIDATION", "EVALUATOR_CHANGED", "SCOPE_VIOLATION",
    "LEAKAGE_GUARD", "VALIDATION_REJECTED", "PRIMARY_NOT_IMPROVED",
    "PAIRED_REGRESSION_OR_INSUFFICIENT_WINS", "UNGROUNDED_HYPOTHESIS", "REDUNDANT_HYPOTHESIS",
    "NO_HYPOTHESIS", "BUDGET_EXHAUSTED", "UNKNOWN",
}
_SENSITIVE_PATH = re.compile(r"(?:^|[/_.-])(validation|holdout|gold|secret|credential|token|evaluator)(?:$|[/_.-])", re.I)
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_SENSITIVE_TEXT = re.compile(r"(?:gold[_ -]?canary|secret|credential|password|api[_ -]?key|token)", re.I)


def _safe_name(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_NAME.fullmatch(value) and not _SENSITIVE_TEXT.search(value):
        return value
    return None


def _safe_campaign_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) and not _SENSITIVE_TEXT.search(value):
        return value
    return "campaign-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _hash_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and _HASH.fullmatch(value) else None


def _safe_path_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({
        item for item in value
        if isinstance(item, str) and _SAFE_PATH.fullmatch(item)
        and not _SENSITIVE_PATH.search(item) and ".." not in item.split("/")
    })[:100]


def _configured_roles(spec: Any) -> dict[str, Any]:
    agent = spec.agent.model_dump(mode="json")

    def safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("kind", "model", "provider", "reasoning_effort"):
            value = profile.get(key)
            if value is None:
                result[key] = "unknown" if key in {"model", "provider", "reasoning_effort"} else None
            elif (isinstance(value, str) and len(value) <= 100
                  and re.fullmatch(r"[A-Za-z0-9_.:/+@-]+", value)
                  and not _SENSITIVE_TEXT.search(value)):
                result[key] = value
            else:
                result[key] = "redacted"
        return result

    return {
        "default": safe_profile(agent),
        "roles": {name: safe_profile(profile) for name, profile in sorted((agent.get("roles") or {}).items())
                  if _safe_name(name)},
        "effective_model": "unknown",
    }


def _tier_summary(report: dict[str, Any], snapshot: str | None) -> dict[str, Any]:
    tiers: dict[str, Any] = {}
    for tier in ("dev", "validation", "holdout"):
        match = next((item for item in report["measurements"]
                      if item.get("tier") == tier and item.get("candidate_snapshot") == snapshot), None)
        if match is None:
            tiers[tier] = None
            continue
        result = match.get("result") or {}
        tiers[tier] = {
            "metrics": {
                _safe_name(name): metric.get("value")
                for name, metric in sorted((result.get("metrics") or {}).items())
                if _safe_name(name) and isinstance(metric, dict)
                and isinstance(metric.get("value"), (int, float, type(None)))
            },
            "checks": {
                _safe_name(name): check.get("passed")
                for name, check in sorted((result.get("checks") or {}).items())
                if _safe_name(name) and isinstance(check, dict) and isinstance(check.get("passed"), bool)
            },
            "receipt_sha256": next((item["receipt_hash"] for item in report["evaluation_receipts"]
                                    if item.get("candidate_snapshot") == snapshot and item.get("tier") == tier
                                    and item.get("receipt_hash")), None),
        }
    return tiers


def _runtime_projection(store: CampaignStore) -> dict[str, Any]:
    """Expose verified runtime fingerprints without host paths or argv."""
    row = store.evaluator_binding()
    unknown = {
        "status": "UNKNOWN",
        "binding_hash": None,
        "runtime_fingerprint": None,
        "supervisor": {"python_version": "unknown", "os": "unknown", "architecture": "unknown"},
        "tiers": {tier: {"status": "UNKNOWN"} for tier in ("dev", "validation", "holdout")},
    }
    if not row:
        return unknown
    try:
        binding = EvaluatorBinding.model_validate_json(row["binding_json"])
        binding_hash = canonical_hash(binding.model_dump(mode="json"))
        if binding.binding_id != row["binding_id"] or binding_hash != row["binding_hash"]:
            return unknown
    except Exception:
        return unknown

    runtime = binding.runtime
    version_pattern = re.compile(r"^[A-Za-z0-9.+_-]{1,40}$")

    def safe_atom(value: Any) -> str:
        return value if isinstance(value, str) and version_pattern.fullmatch(value) else "unknown"

    system = safe_atom(runtime.get("os"))
    if system not in {"Linux", "Windows", "Darwin"}:
        system = "other" if system != "unknown" else "unknown"
    runners = runtime.get("runners") if isinstance(runtime.get("runners"), dict) else {}
    tiers: dict[str, Any] = {}
    for tier in ("dev", "validation", "holdout"):
        runner = runners.get(tier) if isinstance(runners.get(tier), dict) else {}
        effective = runner.get("effective_python") if isinstance(runner.get("effective_python"), dict) else {}
        verified = effective.get("status") == "VERIFIED"
        executable = runner.get("configured_executable")
        executable_name = Path(executable).name if isinstance(executable, str) else ""
        tiers[tier] = {
            "configured": tier in binding.tier_commands,
            "status": "VERIFIED" if verified else "UNKNOWN",
            "executable_name": _safe_name(executable_name) or "redacted",
            "python_version": safe_atom(effective.get("python_version")) if verified else "unknown",
            "implementation": safe_atom(effective.get("implementation")) if verified else "unknown",
            "python_build_fingerprint": _hash_or_none(effective.get("python_build_fingerprint")) if verified else None,
            "python_compiler_fingerprint": _hash_or_none(effective.get("python_compiler_fingerprint")) if verified else None,
            "sys_platform": safe_atom(effective.get("sys_platform")) if verified else "unknown",
            "os": safe_atom(effective.get("os")) if verified else "unknown",
            "architecture": safe_atom(effective.get("architecture")) if verified else "unknown",
            "cache_tag": safe_atom(effective.get("cache_tag")) if verified else "unknown",
            "dependency_count": effective.get("dependency_count")
                if verified and isinstance(effective.get("dependency_count"), int) else None,
            "dependency_fingerprint": _hash_or_none(effective.get("dependency_fingerprint")) if verified else None,
        }
    configured_tiers = [tier for tier in binding.tier_commands if tier in tiers]
    verified_count = sum(tiers[tier]["status"] == "VERIFIED" for tier in configured_tiers)
    status = "VERIFIED" if configured_tiers and verified_count == len(configured_tiers) else (
        "PARTIAL" if configured_tiers else "UNKNOWN"
    )
    return {
        "status": status,
        "binding_id": _hash_or_none(binding.binding_id),
        "binding_hash": _hash_or_none(row.get("binding_hash")),
        "runtime_fingerprint": _hash_or_none(binding.runtime_fingerprint),
        "supervisor": {
            "python_version": safe_atom(runtime.get("python_version")),
            "os": system,
            "architecture": safe_atom(runtime.get("architecture")),
        },
        "tiers": tiers,
    }


def _safe_experiments(report: dict[str, Any]) -> list[dict[str, Any]]:
    safe = []
    for item in report["experiments"]:
        proof = item.get("candidate_proof") or {}
        decision = item.get("decision") or {}
        reasons = [reason for reason in decision.get("reasons", [])
                   if isinstance(reason, str) and reason in _FAILURE_CATEGORIES | {"ACCEPTED", "CANDIDATE_PREFLIGHT_FAILED"}]
        classification = item.get("failure_classification") or {}
        safe.append({
            "experiment_id": item.get("id"),
            "ordinal": item.get("ordinal"),
            "status": item.get("status") if item.get("status") in {"ACCEPTED", "REJECTED", "ACTIVE", "CANCELLED"} else "UNKNOWN",
            "parent_snapshot": _hash_or_none(proof.get("parent_snapshot")),
            "candidate_snapshot": _hash_or_none(item.get("candidate_snapshot")),
            "changed_paths": _safe_path_list(proof.get("changed_paths")),
            "diff_sha256": _hash_or_none(proof.get("diff_sha256")),
            "primary_metric_value": decision.get("primary_value") if isinstance(decision.get("primary_value"), (int, float)) else None,
            "champion_metric_value": decision.get("champion_value") if isinstance(decision.get("champion_value"), (int, float)) else None,
            "delta": decision.get("delta") if isinstance(decision.get("delta"), (int, float)) else None,
            "verdict_reasons": reasons,
            "failure_category": item.get("failure_category") if item.get("failure_category") in _FAILURE_CATEGORIES else None,
            "failure_kind": classification.get("kind") if classification.get("kind") in {
                "implementation", "infrastructure", "scientific_outcome", "policy", "planning", "budget", "unknown"
            } else None,
            "next_step": classification.get("suggested_remediation") if item.get("failure_category") in _FAILURE_CATEGORIES else None,
        })
    return safe


def _safe_event(row: dict[str, Any], campaign_id: str, receipt_by_action: dict[str, dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        payload = {}
    action_id = payload.get("action_id") if isinstance(payload.get("action_id"), str) else None
    item: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "event_id": hashlib.sha256(f"{campaign_id}:{row['seq']}".encode()).hexdigest(),
        "seq": int(row["seq"]),
        "timestamp": row["created_at"],
        "event_type": (row["event_type"] if isinstance(row["event_type"], str)
                       and re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", row["event_type"])
                       and not _SENSITIVE_TEXT.search(row["event_type"]) else "UNCLASSIFIED_EVENT"),
        "stage": payload.get("stage") if payload.get("stage") in _STAGES else None,
        "experiment_id": payload.get("experiment_id") if isinstance(payload.get("experiment_id"), int) else None,
        "action_id": None,
        "receipt_sha256": None,
    }
    if (action_id and re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", action_id)
            and not _SENSITIVE_TEXT.search(action_id)):
        item["action_id"] = action_id
    for key in ("experiment_id", "attempt_id", "experiment_ordinal"):
        value = payload.get(key)
        if isinstance(value, int) and value >= 0:
            item[key] = value
    kind = payload.get("kind")
    if kind in _KINDS:
        item["action_kind"] = kind
    status = payload.get("status")
    if status in _STATUSES:
        item["status"] = status
    snapshot_keys = ("snapshot", "candidate_snapshot", "parent_snapshot", "tree", "diff_sha256", "spec_hash")
    snapshots = {key: _hash_or_none(payload.get(key)) for key in snapshot_keys}
    snapshots = {key: value for key, value in snapshots.items() if value is not None}
    if snapshots:
        item["snapshots"] = snapshots
    stop_reason = payload.get("reason") or payload.get("stop_reason")
    if stop_reason in _STOP_REASONS:
        item["stop_reason"] = stop_reason
    failure_category = payload.get("failure_category")
    if failure_category in _FAILURE_CATEGORIES:
        item["failure_category"] = failure_category
    if isinstance(payload.get("target_met"), bool):
        item["target_met"] = payload["target_met"]
    if action_id and action_id in receipt_by_action:
        item["receipt_sha256"] = receipt_by_action[action_id]
    return item


def _build_trace(store: CampaignStore, campaign_id: str) -> dict[str, str]:
    campaign_id = _safe_campaign_id(campaign_id)
    report = build_report(store)
    campaign = store.campaign()
    spec = store.spec()
    goal_hash = hashlib.sha256(spec.goal.encode("utf-8")).hexdigest()
    validation = report["evaluation_protocol"].get("validation") or {}
    holdout = report["evaluation_protocol"].get("holdout") or {}
    receipt_records = [
        {
            "action_id": receipt.get("action_id") if isinstance(receipt.get("action_id"), str)
                         and re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", receipt["action_id"])
                         and not _SENSITIVE_TEXT.search(receipt["action_id"]) else None,
            "tier": receipt.get("tier") if receipt.get("tier") in {"dev", "validation", "holdout"} else "unknown",
            "status": receipt.get("status") if receipt.get("status") in {
                "RECORDED", "INVALID", "ORPHANED", "LEGACY_UNRECEIPTED"
            } else "UNKNOWN",
            "receipt_hash": _hash_or_none(receipt.get("receipt_hash")),
            "candidate_snapshot": _hash_or_none(receipt.get("candidate_snapshot")),
            "binding_hash": _hash_or_none(receipt.get("binding_hash")),
        }
        for receipt in report["evaluation_receipts"]
    ]
    sanity = report.get("signal_sanity") or {}
    primary = spec.primary_metric
    runtime = _runtime_projection(store)
    summary = {
        "schema_version": 2,
        "campaign_id": campaign_id,
        "created_at": campaign["created_at"],
        "updated_at": campaign["updated_at"],
        "status": campaign["status"] if campaign["status"] in _STATUSES else "UNKNOWN",
        "stage": campaign["stage"] if campaign["stage"] in _STAGES else "UNKNOWN",
        "stop_reason": campaign["stop_reason"] if campaign["stop_reason"] in _STOP_REASONS else "UNKNOWN",
        "goal": {"text": "omitted from shareable trace", "sha256": goal_hash},
        "source": {"base_commit": _hash_or_none(spec.base_commit), "campaign_spec_sha256": campaign["spec_hash"]},
        "runtime": runtime,
        "contract": {
            "primary_metric": {
                "name": _safe_name(primary.name) or "redacted",
                "direction": primary.direction.value,
                "target": primary.target,
                "min_improvement": primary.min_improvement,
            },
            "guardrails": [
                {"metric": _safe_name(item.metric) or "redacted", "operator": item.operator, "bound": item.bound}
                for item in spec.guardrails
            ],
            "non_regression": [
                {"metric": _safe_name(item.metric) or "redacted", "direction": item.direction.value,
                 "tolerance": item.tolerance}
                for item in spec.non_regression
            ],
            "mandatory_checks": [_safe_name(name) or "redacted" for name in spec.mandatory_checks],
            "frozen_assets_sha256": [item.sha256 for item in spec.evaluation_assets],
            "protocol_sha256": {tier: manifest.protocol_sha256 for tier, manifest in sorted(spec.protocol_manifests.items())},
        },
        "scientific_assessment": report["final_assessment"],
        "evaluator_trust": {
            "signal_sanity_status": sanity.get("status") if sanity.get("status") in {
                "VERIFIED", "DIAGNOSTIC_ONLY", "UNAVAILABLE", "FAILED", "CALIBRATED", "UNKNOWN"
            } else "UNKNOWN",
            "signal_evidence_level": sanity.get("evidence_level") if sanity.get("evidence_level") in {
                "diagnostic_only_unverified_labels", "unavailable"
            } else "unknown",
            "independent_reference": "not recorded by this campaign contract",
        },
        "improvement_status": report["improvement_status"],
        "champion_was_promoted": report["champion_was_promoted"],
        "snapshots": {
            "baseline": _hash_or_none(campaign["baseline_snapshot"]),
            "champion": _hash_or_none(campaign["champion_snapshot"]),
        },
        "tiers": {
            "baseline": _tier_summary(report, campaign["baseline_snapshot"]),
            "champion": _tier_summary(report, campaign["champion_snapshot"]),
            "validation_gate": {key: validation.get(key) for key in ("configured", "baseline", "champion", "delta")},
            "final_holdout": {key: holdout.get(key) for key in ("configured", "evaluated", "snapshot", "value", "target_met")},
        },
        "budget": spec.budget.model_dump(mode="json"),
        "usage": {
            "agent_calls": report["resources"]["agent_calls"],
            "evaluator_calls": report["resources"]["evaluation_calls"],
            "experiments": report["resources"]["experiments"],
            "attempts": report["resources"]["attempts"],
            "wall_time_seconds": report["resources"]["wall_time_seconds"],
            "open_reservations": report["resources"]["open_reservations"],
            "unknown_outcomes": report["resources"]["unknown_outcomes"],
            "usage_cost": "unknown",
        },
        "agent_roles": _configured_roles(spec),
        "experiments": _safe_experiments(report),
        "evaluation_receipts": receipt_records,
    }
    receipt_by_action = {
        item["action_id"]: item["receipt_hash"]
        for item in receipt_records if item.get("action_id") and _hash_or_none(item.get("receipt_hash"))
    }
    events = [json.dumps(_safe_event(row, campaign_id, receipt_by_action), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")) for row in store.events()]
    # The ordinary report can include user goal text and agent-authored rationales.
    # A shareable trace is built only from the allowlisted summary above.
    markdown = _render_trace_markdown(summary)
    manifest = {
        "format": "bananloop-shareable-trace-v2",
        "campaign_id": campaign_id,
        "files": {
            "events.jsonl": hashlib.sha256(("\n".join(events) + ("\n" if events else "")).encode()).hexdigest(),
            "summary.json": hashlib.sha256((json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()).hexdigest(),
            "report.md": hashlib.sha256(markdown.encode()).hexdigest(),
        },
    }
    return {
        "events.jsonl": "\n".join(events) + ("\n" if events else ""),
        "summary.json": json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        "report.md": markdown,
        "manifest.json": json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    }


def _render_trace_markdown(summary: dict[str, Any]) -> str:
    metric = summary["contract"]["primary_metric"]
    tiers = summary["tiers"]
    lines = [
        "# BananLoop campaign trace",
        "",
        f"- Campaign: `{summary['campaign_id']}`",
        f"- Status: `{summary['status']}`; stage: `{summary['stage']}`; stop reason: `{summary['stop_reason']}`",
        f"- Goal text is omitted from this shareable export; SHA-256: `{summary['goal']['sha256']}`",
        f"- Scientific assessment: `{summary['scientific_assessment']}`; improvement status: `{summary['improvement_status']}`",
        f"- Runtime binding: `{summary['runtime']['status']}`; fingerprint `{summary['runtime']['runtime_fingerprint'] or 'unknown'}`.",
        f"- Primary metric: `{metric['name']}` ({metric['direction']}); target `{metric['target']}`, minimum change `{metric['min_improvement']}`",
        "",
        "## Measurements",
        "",
        "Only tier aggregates are included; case inputs, labels, and per-case results are omitted.",
        "",
        "| Tier | Baseline metrics | Champion metrics |",
        "|---|---|---|",
    ]
    for tier in ("dev", "validation", "holdout"):
        baseline = tiers["baseline"].get(tier)
        champion = tiers["champion"].get(tier)
        lines.append(f"| {tier} | `{json.dumps((baseline or {}).get('metrics'), sort_keys=True)}` | `{json.dumps((champion or {}).get('metrics'), sort_keys=True)}` |")
    lines.extend([
        "",
        "## Decisions",
        "",
        "| Experiment | Status | Changed paths | Metric delta | Decision reason |",
        "|---:|---|---|---:|---|",
    ])
    for item in summary["experiments"]:
        paths = ", ".join(f"`{p}`" for p in item["changed_paths"]) or "(none recorded)"
        lines.append(f"| {item['ordinal']} | `{item['status']}` | {paths} | {item['delta']} | {', '.join(item['verdict_reasons']) or item['failure_category'] or 'recorded'} |")
    lines.extend([
        "",
        "## Budget and evidence",
        "",
        f"- Agent calls: {summary['usage']['agent_calls']}; evaluator calls: {summary['usage']['evaluator_calls']}; attempts: {summary['usage']['attempts']}; experiments: {summary['usage']['experiments']}.",
        f"- Wall time: {summary['usage']['wall_time_seconds']} seconds; cost: unknown unless the provider supplied metering.",
        f"- Validation gate: `{json.dumps(tiers['validation_gate'], sort_keys=True)}`.",
        f"- Final holdout: `{json.dumps(tiers['final_holdout'], sort_keys=True)}` (terminal aggregate only).",
        "- Receipt digests and frozen protocol fingerprints are in `summary.json`; this export contains no evaluator payloads.",
        "",
    ])
    return "\n".join(lines)


def export_trace(store: CampaignStore, campaign_id: str, output: Path) -> Path:
    """Refresh a generated shareable trace directory, rolling back a failed swap."""
    output = output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise StoreError(f"trace output is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        manifest = output / "manifest.json"
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise StoreError(f"refusing to replace a non-BananLoop trace directory: {output}") from None
        if existing.get("format") not in {"bananloop-shareable-trace-v1", "bananloop-shareable-trace-v2"}:
            raise StoreError(f"refusing to replace a non-BananLoop trace directory: {output}")
        generated_names = {"events.jsonl", "summary.json", "report.md", "manifest.json"}
        if any(path.name not in generated_names for path in output.iterdir()):
            raise StoreError(f"refusing to remove files not generated by BananLoop: {output}")
    files = _build_trace(store, campaign_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}"
    temp.mkdir()
    try:
        for name, content in files.items():
            path = temp / name
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        if output.exists():
            output.rename(backup)
        try:
            temp.rename(output)
        except OSError:
            if backup.exists() and not output.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
        if backup.exists() and output.exists():
            shutil.rmtree(backup)
    return output
