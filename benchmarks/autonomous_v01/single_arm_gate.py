"""Offline, fail-closed validation gate for a prepared Full arm.

This verifies supplied evidence only. A passing gate is not launch authorization.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .runner import canonical_sha


class GateError(ValueError):
    """Evidence did not satisfy the single-arm launch preconditions."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} cannot be read as JSON") from exc
    if not isinstance(value, dict):
        raise GateError(f"{label} must be a JSON object")
    return value


def _git(checkout: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"candidate checkout git {args[0]} check failed") from exc
    return completed.stdout.strip()


def validate_single_arm_gate(
    *, manifest_path: Path, preflight_path: Path, candidate_checkout: Path,
    expected_manifest_sha256: str, task_id: str, candidate_source_sha: str,
    arm_id: str, max_preflight_age_seconds: int = 3600,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Validate frozen manifest, selected Full arm, clean source and fresh preflight.

    The caller must pin ``expected_manifest_sha256`` independently. ``now`` is
    injectable for deterministic offline tests. Authorization is deliberately
    never inferred or granted here.
    """
    manifest = _read_object(Path(manifest_path), "manifest")
    if manifest.get("schema") != "bananloop-autonomous-v01-experiment-manifest-v1":
        raise GateError("unsupported manifest schema")
    digest = canonical_sha({key: value for key, value in manifest.items() if key != "manifest_sha256"})
    if manifest.get("manifest_sha256") != digest or digest != expected_manifest_sha256:
        raise GateError("manifest canonical SHA does not match the pinned SHA")
    if manifest.get("kind") != "ARE_PREPARATION_ONLY":
        raise GateError("manifest is not preparation-only")
    entries = manifest.get("tasks")
    if not isinstance(entries, list) or len(entries) != 12:
        raise GateError("manifest must contain exactly 12 tasks")
    ids = [entry.get("task", {}).get("task_id") for entry in entries
           if isinstance(entry, dict) and isinstance(entry.get("task"), dict)]
    if len(ids) != 12 or len(set(ids)) != 12:
        raise GateError("manifest task identities are malformed or duplicated")
    matches = [entry for entry in entries if entry.get("task", {}).get("task_id") == task_id]
    if len(matches) != 1:
        raise GateError("selected task is not uniquely present in manifest")
    pair = matches[0]
    full_arms = [a for a in pair.get("arms", []) if isinstance(a, dict) and a.get("system") == "bananloop_full"]
    expected_arm_id = f"{pair.get('pair_id')}-bananloop_full"
    if len(full_arms) != 1 or arm_id != expected_arm_id:
        raise GateError("selected arm is not the manifest Full arm")
    if manifest.get("candidate_source_sha") != candidate_source_sha:
        raise GateError("candidate source SHA differs from manifest")
    checkout = Path(candidate_checkout).resolve()
    head = _git(checkout, "rev-parse", "--verify", "HEAD")
    if head != candidate_source_sha:
        raise GateError("candidate checkout HEAD differs from candidate source SHA")
    if _git(checkout, "status", "--porcelain", "--untracked-files=all"):
        raise GateError("candidate checkout is dirty")

    preflight = _read_object(Path(preflight_path), "preflight")
    if preflight.get("schema") != "bananloop-autonomous-v01-preflight-v1":
        raise GateError("unsupported preflight schema")
    if (preflight.get("status") != "READY_NON_RPDI_DIAGNOSTIC"
            or preflight.get("blockers") != []):
        raise GateError("preflight is not READY_NON_RPDI_DIAGNOSTIC with no blockers")
    if preflight.get("manifest_sha256") != digest:
        raise GateError("preflight is not bound to this manifest")
    if preflight.get("candidate_source_sha") != candidate_source_sha:
        raise GateError("preflight is not bound to this candidate source SHA")
    stamp = preflight.get("generated_at")
    if not isinstance(stamp, str):
        raise GateError("preflight timestamp is missing")
    try:
        generated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise GateError("preflight timestamp is invalid") from exc
    current = (now or (lambda: datetime.now(timezone.utc)))()
    age = (current - generated.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > max_preflight_age_seconds:
        raise GateError("preflight is stale or dated in the future")
    return {
        "status": "GATE_PASSED",
        "manifest_sha256": digest,
        "task_id": task_id,
        "arm_id": arm_id,
        "candidate_source_sha": candidate_source_sha,
        "preflight_status": "READY_NON_RPDI_DIAGNOSTIC",
        "authorization": "NOT_ESTABLISHED",
    }
