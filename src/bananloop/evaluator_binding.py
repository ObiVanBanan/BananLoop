from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import CampaignSpec, EvaluationResult
from .integrity import evaluation_fingerprint


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        resolved = shutil.which(executable)
        if resolved is None:
            candidate = Path(executable)
            if candidate.is_file():
                resolved = str(candidate.resolve())
        runner_record: dict[str, Any] = {
            "configured_executable": executable,
            "resolved_executable": str(Path(resolved).resolve()) if resolved else None,
        }
        invocation = _python_runtime_invocation(executable, argv[1:])
        if resolved and invocation is not None:
            runner_record["effective_python"] = _probe_python_runtime(
                str(Path(resolved).resolve()), tuple(invocation)
            )
        else:
            runner_record["effective_python"] = {"status": "UNKNOWN"}
        runner[tier] = runner_record
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "runners": runner,
    }


def _python_runtime_invocation(executable: str, arguments: list[str]) -> list[str] | None:
    name = executable.replace("\\", "/").rsplit("/", 1)[-1]
    launcher = re.fullmatch(r"py(?:\.exe)?", name, flags=re.IGNORECASE)
    if launcher:
        selector = next((arg for arg in arguments if re.fullmatch(
            r"-(?:[0-9]+(?:\.[0-9]+)*(?:-(?:32|64))?|V:.+)", arg, flags=re.IGNORECASE
        )), None)
        return [selector] if selector else []
    if re.fullmatch(r"pythonw?(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?", name, flags=re.IGNORECASE):
        return []
    return None


@lru_cache(maxsize=16)
def _probe_python_runtime(resolved_executable: str, selectors: tuple[str, ...]) -> dict[str, Any]:
    """Fingerprint a direct Python evaluator without importing project code."""
    probe = (
        "import hashlib, importlib.metadata as m, json, platform, sys; "
        "packages=sorted((d.metadata.get('Name','').strip().lower(),d.version) "
        "for d in m.distributions() if d.metadata.get('Name')); "
        "raw=json.dumps(packages,separators=(',',':'),ensure_ascii=True).encode(); "
        "print(json.dumps({'python_version':platform.python_version(),"
        "'implementation':platform.python_implementation(),"
        "'python_build_fingerprint':hashlib.sha256(json.dumps(platform.python_build()).encode()).hexdigest(),"
        "'python_compiler_fingerprint':hashlib.sha256(platform.python_compiler().encode()).hexdigest(),"
        "'sys_platform':sys.platform,"
        "'os':platform.system(),'os_release':platform.release(),"
        "'architecture':platform.machine(),'cache_tag':sys.implementation.cache_tag,"
        "'dependency_count':len(packages),"
        "'dependency_fingerprint':hashlib.sha256(raw).hexdigest()}))"
    )
    argv = [resolved_executable, *selectors, "-I", "-c", probe]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=10,
        )
        data = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or not re.fullmatch(
        r"[0-9a-f]{64}", str(data.get("dependency_fingerprint", ""))
    ):
        return {"status": "UNKNOWN"}
    return {
        "status": "VERIFIED",
        "python_version": data.get("python_version"),
        "implementation": data.get("implementation"),
        "python_build_fingerprint": data.get("python_build_fingerprint"),
        "python_compiler_fingerprint": data.get("python_compiler_fingerprint"),
        "sys_platform": data.get("sys_platform"),
        "os": data.get("os"),
        "os_release": data.get("os_release"),
        "architecture": data.get("architecture"),
        "cache_tag": data.get("cache_tag"),
        "dependency_count": data.get("dependency_count"),
        "dependency_fingerprint": data["dependency_fingerprint"],
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
    if spec.smoke_command:
        commands["smoke"] = spec.smoke_command
        timeouts["smoke"] = spec.smoke_timeout_seconds or 90
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
