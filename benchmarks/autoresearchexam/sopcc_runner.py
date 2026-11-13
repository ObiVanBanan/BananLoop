"""Task-specific public SOPCC backend for BananLoop's trusted evaluator."""
from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any, Mapping

from bananloop.evaluator_verification import EvaluatorProtocolV1
from bananloop.trusted_evaluator import (
    EvaluatorExecutionV1, TrustedEvaluatorError, _case_protocol_digest,
    _manifest_from_bytes,
)

from .optimization_runner import AREPublicEvaluatorRunner
from .public_mcp_contract import validate_public_mcp_contract


def normalize_sopcc_dev_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Map the public SOPCC runner receipt to the shared evaluator contract.

    The mapping is part of the digest-bound evaluator source so the independent
    protocol reviewer can verify that the episode aggregate returned by the
    public task harness is the score actually recorded by BananLoop.
    """
    if not isinstance(result, Mapping) or result.get("status") != "GRADED":
        raise ValueError("public SOPCC DEV result is not terminal")
    score = result.get("metric")
    episodes = result.get("episodes")
    if (isinstance(score, bool) or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or isinstance(episodes, bool) or not isinstance(episodes, int)
            or episodes != AREPublicSOPCCEvaluatorRunner.default_dev_cell_count):
        raise ValueError("public SOPCC DEV result has an invalid aggregate or episode count")
    return {"status": "GRADED", "dev_score": float(score), "dev_cells": episodes}


class AREPublicSOPCCEvaluatorRunner(AREPublicEvaluatorRunner):
    """SOPCC-specific binding for the shared trusted-evaluator seam.

    It shares the public ARE grader ledger, but pins SOPCC's own output,
    score semantics, task image, and public DEV panel. It never accesses the
    task's private verifier context.
    """

    backend_id = "autoresearchexam-sopcc-public-v1"
    default_dev_image = "bananloop-are-sopcc-public-dev:2026-11-12"
    default_dev_image_id = "sha256:5fc63eccd0a3448db1dd9240c3fe7ecb4cef02511bafc77e2c91f294c3d82b8e"
    default_dev_cell_count = 4
    dev_aggregate_case_id = "public-sopcc-dev-aggregate"
    validation_aggregate_case_id = "public-sopcc-validation-aggregate"
    dev_draws = (0,)
    dev_instances_per_draw = 1
    dev_episodes_per_instance = 4

    def __init__(
        self, *, dev_image: str | None = None, dev_image_id: str | None = None,
        dev_cell_count: int | None = None, **kwargs: Any,
    ):
        # Base constructor defaults are bound to the imputation image at class
        # definition time, so bind SOPCC's class-level pins explicitly here.
        super().__init__(
            dev_image=self.default_dev_image if dev_image is None else dev_image,
            dev_image_id=self.default_dev_image_id if dev_image_id is None else dev_image_id,
            dev_cell_count=self.default_dev_cell_count if dev_cell_count is None else dev_cell_count,
            **kwargs,
        )

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        if tier == "dev":
            value = {
                "schema": "autoresearchexam-public-sopcc-aggregate-v1",
                "task_id": task_id, "task_version": task_version, "tier": tier,
                "metric_field": "sopcc_score",
                "measurement": "public DEV mean delivered reward with task failure penalty",
                "dev_image_id": dev_image_id,
                "draws": list(cls.dev_draws),
                "instances_per_draw": cls.dev_instances_per_draw,
                "episodes_per_instance": cls.dev_episodes_per_instance,
                "episode_count": dev_cell_count,
            }
        elif tier == "validation":
            value = {
                "schema": "autoresearchexam-public-sopcc-aggregate-v1",
                "task_id": task_id, "task_version": task_version, "tier": tier,
                "metric_field": "sopcc_score",
                "measurement": "one aggregate score from the public ARE validation endpoint",
                "mcp_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
            }
        else:
            raise ValueError("SOPCC public backend only supports DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("SOPCC evaluator bundle digest differs from approved protocol")
        expected_names = {
            "are-public-sopcc-backend.json", "public_sopcc_dev.py",
            "optimization_runner.py", "public_grader.py", "are_public_mcp_client.py",
            "sopcc_runner.py", "public_mcp_contract.py",
            "public-mcp-contract.json", "task-instruction.md", "task-Dockerfile",
            "task-requirements.txt", "task-dev_eval.py", "task-runner.py",
            "task-sopcc.py", "task-policy_worker.py",
        }
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if set(archive.namelist()) != expected_names or len(archive.infolist()) != len(expected_names):
                    raise ValueError
                manifest = json.loads(archive.read("are-public-sopcc-backend.json"))
                contract_bytes = archive.read("public-mcp-contract.json")
                contract = validate_public_mcp_contract(json.loads(contract_bytes))
                source_pairs = {
                    "public_sopcc_dev.py": Path(__file__).with_name("public_sopcc_dev.py").read_bytes(),
                    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py").read_bytes(),
                    "sopcc_runner.py": Path(__file__).read_bytes(),
                    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py").read_bytes(),
                    "public_grader.py": Path(__file__).with_name("public_grader.py").read_bytes(),
                    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py").read_bytes(),
                }
                for name, runtime_source in source_pairs.items():
                    if archive.read(name) != runtime_source:
                        raise ValueError
                contract_digest = hashlib.sha256(contract_bytes).hexdigest()
                task_instruction_digest = hashlib.sha256(archive.read("task-instruction.md")).hexdigest()
                task_file_digests = {
                    name: hashlib.sha256(archive.read(name)).hexdigest()
                    for name in ("task-Dockerfile", "task-requirements.txt", "task-dev_eval.py",
                                 "task-runner.py", "task-sopcc.py", "task-policy_worker.py")
                }
                if (contract.get("task_id") != self.task_id
                        or contract.get("task_version") != self.task_version
                        or contract.get("contract_sha256") != self.contract_sha256
                        or contract.get("public_endpoint") != "http://grader:8000/mcp"
                        or not {item.get("name") for item in contract.get("discovered_tools", [])
                                if isinstance(item, dict)} >= {"submit", "result"}):
                    raise ValueError
                expected = {
                    "schema_version": 1, "backend_id": self.backend_id,
                    "task_id": self.task_id, "task_version": self.task_version,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_endpoint": "http://grader:8000/mcp",
                    "candidate_path": self.candidate_path,
                    "public_output_path": "/app/solution/policy.py",
                    "public_runner_sha256": hashlib.sha256(source_pairs["public_sopcc_dev.py"]).hexdigest(),
                    "optimization_runner_sha256": hashlib.sha256(source_pairs["optimization_runner.py"]).hexdigest(),
                    "sopcc_runner_sha256": hashlib.sha256(source_pairs["sopcc_runner.py"]).hexdigest(),
                    "public_mcp_contract_sha256": hashlib.sha256(source_pairs["public_mcp_contract.py"]).hexdigest(),
                    "public_grader_sha256": hashlib.sha256(source_pairs["public_grader.py"]).hexdigest(),
                    "mcp_client_sha256": hashlib.sha256(source_pairs["are_public_mcp_client.py"]).hexdigest(),
                    "task_instruction_sha256": task_instruction_digest,
                    "task_file_sha256": task_file_digests,
                    "public_dev_image": self.dev_image,
                    "public_dev_image_id": self.dev_image_id,
                    "public_dev_episode_count": self.dev_cell_count,
                    "public_dev_panel": {
                        "draws": list(self.dev_draws),
                        "instances_per_draw": self.dev_instances_per_draw,
                        "episodes_per_instance": self.dev_episodes_per_instance,
                    },
                    "dev_execution": {
                        "container_network": "none", "root_filesystem_read_only": True,
                        "mounted_candidate_path": "/app/solution/policy.py",
                    },
                    "aggregate_case_ids": {
                        "dev": self.dev_aggregate_case_id,
                        "validation": self.validation_aggregate_case_id,
                    },
                }
                if manifest != expected:
                    raise ValueError
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError,
                zipfile.BadZipFile, KeyError):
            raise TrustedEvaluatorError("SOPCC public backend bundle is missing or malformed") from None
        if self.grader.contract_sha256 != self.contract_sha256:
            raise TrustedEvaluatorError("SOPCC public grader contract differs from the protocol pin")

    def _validate_aggregate_case(
        self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes], *, tier: str,
    ) -> None:
        expected_id = self.dev_aggregate_case_id if tier == "dev" else self.validation_aggregate_case_id
        if len(cases) != 1 or cases[0].case_id != expected_id or cases[0].repetition != 1:
            raise TrustedEvaluatorError(f"SOPCC public {tier.upper()} must declare one aggregate case")
        identity = (expected_id, 1)
        if set(case_inputs) != {identity}:
            raise TrustedEvaluatorError(f"SOPCC public {tier.upper()} inputs do not match its case")
        expected = self.aggregate_case_input(
            tier=tier, task_id=self.task_id, task_version=self.task_version,
            contract_sha256=self.contract_sha256, dev_image_id=self.dev_image_id,
            dev_cell_count=self.dev_cell_count,
        )
        payload = case_inputs[identity]
        if (not isinstance(payload, bytes) or payload != expected
                or hashlib.sha256(payload).hexdigest() != cases[0].input_sha256):
            raise TrustedEvaluatorError(f"SOPCC public {tier.upper()} scope differs from its approved input")

    @staticmethod
    def _execution_from_score(
        *, protocol: EvaluatorProtocolV1, approval_digest: str,
        campaign_id: str, spec_hash: str, candidate_snapshot_id: str,
        tier_manifest: bytes, tier: str, split: str, score: float,
    ) -> EvaluatorExecutionV1:
        # SOPCC scores are expected reward, not imputation R².
        return AREPublicEvaluatorRunner._execution_from_score(
            protocol=protocol, approval_digest=approval_digest,
            campaign_id=campaign_id, spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest, tier=tier, split=split,
            score=score, metric_unit="reward",
        )
