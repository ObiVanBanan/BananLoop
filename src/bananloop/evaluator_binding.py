from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import CampaignSpec, EvaluationResult
from .integrity import evaluation_fingerprint


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_executable(executable: str) -> str | None:
    """Resolve a command the same way receipts describe it before Popen search."""
    resolved = shutil.which(executable)
    if resolved is not None:
        return str(Path(resolved).resolve())
    candidate = Path(executable)
    if candidate.is_file():
        return str(candidate.resolve())
    return None


class EvaluatorBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    binding_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    goal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot: str
    tier_commands: dict[str, list[str]]
    tier_timeouts: dict[str, int]
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_fingerprint: str | None
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: dict[str, Any]
    runtime_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_identity(self):
        body = self.model_dump(mode="json")
        identity = body.pop("binding_id")
        if identity != canonical_hash(body):
            raise ValueError("evaluator binding identity hash mismatch")
        if self.runtime_fingerprint != canonical_hash(self.runtime):
            raise ValueError("evaluator binding runtime fingerprint mismatch")
        return self


class EvaluationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_id: str
    tier: str
    candidate_snapshot: str
    candidate_proof_sha256: str | None
    binding_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_input_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    exit_code: int = Field(ge=0)

    @model_validator(mode="after")
    def verify_identity(self):
        body = self.model_dump(mode="json")
        identity = body.pop("receipt_hash")
        if identity != canonical_hash(body):
            raise ValueError("evaluation receipt hash mismatch")
        return self


def validate_receipt_linkage(
    receipt_data: dict[str, Any],
    *,
    stored_receipt_hash: str,
    action: dict[str, Any],
    binding_data: dict[str, Any],
    stored_binding_hash: str,
) -> EvaluationReceipt:
    """Validate a receipt against its durable action and campaign binding."""
    receipt = EvaluationReceipt.model_validate(receipt_data)
    binding = EvaluatorBinding.model_validate(binding_data)
    if canonical_hash(binding.model_dump(mode="json")) != stored_binding_hash:
        raise ValueError("stored binding hash mismatch")
    if receipt.receipt_hash != stored_receipt_hash:
        raise ValueError("stored receipt hash mismatch")
    if action.get("status") != "COMPLETED" or not action.get("response_json"):
        raise ValueError("receipt action is not completed")
    request = json.loads(action.get("request_json") or "{}")
    result = EvaluationResult.model_validate_json(action["response_json"])
    proof = request.get("candidate_proof")
    inputs = [
        {"case_id": case.case_id, "repetition": case.repetition, "input_sha256": case.input_sha256}
        for case in result.cases
    ]
    artifacts = [
        {"case_id": item.case_id, "repetition": item.repetition, "path": item.path, "sha256": item.sha256}
        for item in result.artifact_manifest
    ]
    expected = {
        "action_id": action["id"],
        "tier": request.get("tier", "dev"),
        "candidate_snapshot": request.get("snapshot"),
        "candidate_proof_sha256": canonical_hash(proof) if proof is not None else None,
        "binding_id": binding.binding_id,
        "binding_hash": stored_binding_hash,
        "protocol_hash": result.protocol_sha256 or binding.protocol_hash,
        "runtime_fingerprint": binding.runtime_fingerprint,
        "case_input_manifest_hash": canonical_hash(inputs),
        "result_hash": canonical_hash(result.model_dump(mode="json")),
        "artifact_manifest_hash": canonical_hash(artifacts),
        "exit_code": 0,
    }
    for key, value in expected.items():
        if getattr(receipt, key) != value:
            raise ValueError(f"receipt linkage mismatch: {key}")
    return receipt


def runtime_descriptor(commands: dict[str, list[str]]) -> dict[str, Any]:
    runner: dict[str, Any] = {}
    for tier, argv in sorted(commands.items()):
        executable = argv[0] if argv else ""
        runner[tier] = {
            "configured_executable": executable,
            "resolved_executable": resolve_executable(executable),
        }
    python_distributions = sorted(
        {
            (str(item.metadata.get("Name") or "").lower(), str(item.version))
            for item in distributions()
            if item.metadata.get("Name")
        }
    )
    # Fingerprint only environment variables that select executable/runtime
    # resolution. Keep their values hashed so paths remain useful evidence
    # without copying arbitrary environment data into durable receipts.
    runtime_env = {
        key: hashlib.sha256(os.environ[key].encode("utf-8", errors="replace")).hexdigest()
        for key in (
            "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH", "VIRTUAL_ENV",
            "CONDA_PREFIX", "NODE_PATH", "NODE_ENV",
        )
        if key in os.environ
    }
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_distributions": python_distributions,
        "runtime_environment_sha256": canonical_hash(runtime_env),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "runners": runner,
    }


def make_binding(spec: CampaignSpec, runtime: dict[str, Any]) -> EvaluatorBinding:
    commands = {"dev": spec.evaluation_command}
    timeouts = {"dev": spec.evaluator_timeout_seconds}
    if spec.validation_command:
        commands["validation"] = spec.validation_command
        timeouts["validation"] = spec.validation_timeout_seconds or spec.evaluator_timeout_seconds
    if spec.holdout_command:
        commands["holdout"] = spec.holdout_command
        timeouts["holdout"] = spec.holdout_timeout_seconds or spec.evaluator_timeout_seconds
    approved = spec.approved_hash()
    goal_fp = canonical_hash(spec.goal)
    policy = {
        "primary_metric": spec.primary_metric.model_dump(mode="json"),
        "guardrails": [x.model_dump(mode="json") for x in spec.guardrails],
        "non_regression": [x.model_dump(mode="json") for x in spec.non_regression],
        "paired_comparison": spec.paired_comparison.model_dump(mode="json") if spec.paired_comparison else None,
        "mandatory_checks": spec.mandatory_checks,
    }
    body = {
        "schema_version": 1,
        "approved_spec_hash": approved,
        "goal_fingerprint": goal_fp,
        "source_snapshot": spec.base_commit,
        "tier_commands": commands,
        "tier_timeouts": timeouts,
        "protocol_hash": approved,
        "evaluation_fingerprint": evaluation_fingerprint(spec.evaluation_assets),
        "policy_hash": canonical_hash(policy),
        "runtime": runtime,
        "runtime_fingerprint": canonical_hash(runtime),
    }
    return EvaluatorBinding(binding_id=canonical_hash(body), **body)


def make_receipt(*, action_id: str, tier: str, snapshot: str, candidate_proof: dict[str, Any] | None,
                 binding: EvaluatorBinding, result: EvaluationResult) -> EvaluationReceipt:
    inputs = [{"case_id": c.case_id, "repetition": c.repetition, "input_sha256": c.input_sha256}
              for c in result.cases]
    artifacts = [{"case_id": a.case_id, "repetition": a.repetition, "path": a.path, "sha256": a.sha256}
                 for a in result.artifact_manifest]
    body = {
        "schema_version": 1,
        "action_id": action_id,
        "tier": tier,
        "candidate_snapshot": snapshot,
        "candidate_proof_sha256": canonical_hash(candidate_proof) if candidate_proof is not None else None,
        "binding_id": binding.binding_id,
        "binding_hash": canonical_hash(binding.model_dump(mode="json")),
        "protocol_hash": result.protocol_sha256 or binding.protocol_hash,
        "runtime_fingerprint": binding.runtime_fingerprint,
        "case_input_manifest_hash": canonical_hash(inputs),
        "result_hash": canonical_hash(result.model_dump(mode="json")),
        "artifact_manifest_hash": canonical_hash(artifacts),
        "exit_code": 0,
    }
    return EvaluationReceipt(receipt_hash=canonical_hash(body), **body)
