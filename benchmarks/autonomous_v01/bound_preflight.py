"""Create a fresh offline preflight bound to a frozen manifest and checkout.

This module only runs the existing offline checks. It never starts a model,
Harbor evaluation, or benchmark backend.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import runner
from .single_arm_gate import GateError, validate_single_arm_gate


class BoundPreflightError(ValueError):
    """A required binding or offline preflight check is invalid."""


def _manifest(path: Path, expected_sha256: str, candidate_sha: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundPreflightError("manifest cannot be read as JSON") from exc
    if not isinstance(value, dict):
        raise BoundPreflightError("manifest must be a JSON object")
    digest = runner.canonical_sha({k: v for k, v in value.items() if k != "manifest_sha256"})
    if (not isinstance(expected_sha256, str) or digest != expected_sha256
            or value.get("manifest_sha256") != digest):
        raise BoundPreflightError("manifest digest does not match the explicit pin")
    if value.get("candidate_source_sha") != candidate_sha:
        raise BoundPreflightError("manifest candidate source SHA differs from explicit pin")
    return value


def _checkout_sha_and_clean(path: Path) -> tuple[str, str]:
    checkout = Path(path)
    if checkout.is_symlink() or not checkout.is_dir():
        raise BoundPreflightError("candidate checkout is missing or unsafe")
    try:
        head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain",
                                "--untracked-files=all"], check=True, capture_output=True,
                               text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BoundPreflightError("candidate checkout git verification failed") from exc
    return head, dirty


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise BoundPreflightError("generated_at must be an explicit ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BoundPreflightError("generated_at is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BoundPreflightError("generated_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def create_bound_preflight(
    *, manifest_path: Path, expected_manifest_sha256: str,
    candidate_checkout: Path, candidate_source_sha: str, task_id: str,
    arm_id: str, runtime_root: Path, generated_at: str, output_path: Path,
    codex_auth_status: str | None = None, codex_version: str | None = None,
    harbor_version: str | None = None,
    image_inspector: Callable[[str], str | None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run the repository's offline preflight and emit single-arm-gate evidence.

    Runtime root, manifest/candidate pins, timestamp, and destination are all
    explicit required inputs. The offline runner is called directly.
    """
    if not isinstance(runtime_root, Path) or runtime_root.is_symlink() or not runtime_root.is_dir():
        raise BoundPreflightError("explicit runtime root must be an existing regular directory")
    if not isinstance(candidate_source_sha, str) or len(candidate_source_sha) != 40:
        raise BoundPreflightError("candidate source SHA must be a 40-character git SHA")
    stamp = _timestamp(generated_at)
    clock = (now or (lambda: datetime.now(timezone.utc)))()
    if (clock.tzinfo is None or stamp > clock.astimezone(timezone.utc)):
        raise BoundPreflightError("generated_at is invalid or in the future")
    _manifest(Path(manifest_path), expected_manifest_sha256, candidate_source_sha)
    head, dirty = _checkout_sha_and_clean(candidate_checkout)
    if head != candidate_source_sha:
        raise BoundPreflightError("candidate checkout HEAD differs from candidate source SHA")
    if dirty:
        raise BoundPreflightError("candidate checkout is dirty")

    # Calling without a runtime root default is intentional: this binds every
    # descriptor check to the path selected by the caller.
    report = runner.offline_preflight(
        runtime_root=runtime_root, codex_auth_status=codex_auth_status,
        codex_version=codex_version, harbor_version=harbor_version,
        image_inspector=image_inspector,
    )
    if not isinstance(report, dict):
        raise BoundPreflightError("offline_preflight returned a malformed report")
    report = dict(report)
    report.update({"manifest_sha256": expected_manifest_sha256,
                   "candidate_source_sha": candidate_source_sha,
                   "generated_at": stamp.isoformat().replace("+00:00", "Z")})
    if (report.get("schema") != "bananloop-autonomous-v01-preflight-v1"
            or report.get("status") != "READY_NON_RPDI_DIAGNOSTIC"
            or report.get("blockers") != []):
        raise BoundPreflightError("fresh offline preflight is blocked or malformed")

    # Recheck source and manifest after the potentially lengthy validation to
    # avoid binding evidence across concurrent changes.
    head_after, dirty_after = _checkout_sha_and_clean(candidate_checkout)
    if head_after != candidate_source_sha or dirty_after:
        raise BoundPreflightError("candidate checkout changed during offline preflight")
    _manifest(Path(manifest_path), expected_manifest_sha256, candidate_source_sha)

    output = Path(output_path)
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise BoundPreflightError("output path must be a regular nonsymlink file")
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        validate_single_arm_gate(
            manifest_path=Path(manifest_path), preflight_path=output,
            candidate_checkout=Path(candidate_checkout),
            expected_manifest_sha256=expected_manifest_sha256, task_id=task_id,
            candidate_source_sha=candidate_source_sha, arm_id=arm_id,
            now=lambda: clock,
        )
    except GateError as exc:
        output.unlink(missing_ok=True)
        raise BoundPreflightError(f"single-arm gate rejected generated evidence: {exc}") from exc
    return report
