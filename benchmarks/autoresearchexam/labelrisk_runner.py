"""Trusted public DEV + ARE VALIDATION adapter for label-efficient-risk-estimator."""
from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any, Mapping

from bananloop.evaluator_verification import EvaluatorProtocolV1
from bananloop.trusted_evaluator import EvaluatorExecutionV1, TrustedEvaluatorError

from .optimization_runner import AREPublicEvaluatorRunner
from .public_labelrisk_dev import (
    DEFAULT_IMAGE, DEFAULT_IMAGE_ID, INSTRUCTION_SHA256,
    LABEL_BUDGETS, PUBLIC_DEV_MANIFEST_SHA256, PUBLIC_POOL_IDS,
    ROOT_SEED, SEEDS_PER_CELL, TASK_DOCKERFILE_SHA256,
    TASK_REQUIREMENTS_SHA256, SELECTION_SHA256, TASK_ID, TASK_SHA256, TASK_VERSION,
    verify_public_inputs, run_public_labelrisk_dev,
)
from .public_mcp_contract import validate_public_mcp_contract

BACKEND_ID = "autoresearchexam-labelrisk-public-v1"
CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/label_risk/estimator.py"
PUBLIC_OUTPUT_PATH = "/app/output/estimator.py"
DEV_CASE_ID = "public-labelrisk-dev-aggregate"
VALIDATION_CASE_ID = "public-labelrisk-validation-aggregate"
METRIC_ID = "labelrisk_median_relative_squared_error"
METRIC_FIELD = "median_relative_squared_error_ratio"
VALIDATION_METRIC_SEMANTICS = "public_are.validation_score=lower_is_better_task_score.v1"
BACKEND_MANIFEST = "are-public-labelrisk-backend.json"

_RUNTIME_FILES = {
    "labelrisk_runner.py": Path(__file__),
    "public_labelrisk_dev.py": Path(__file__).with_name("public_labelrisk_dev.py"),
    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py"),
    "public_grader.py": Path(__file__).with_name("public_grader.py"),
    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py"),
    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py"),
}
_PUBLIC_TASK_FILES = {
    "task.toml": "task.toml",
    "task-instruction.md": "instruction.md",
    "task-Dockerfile": "environment/Dockerfile",
    "task-requirements.txt": "environment/requirements.txt",
}
_DEV_FILE_NAMES = ("labels.npy", "meta.json", "surrogate_probs.npy", "target_probs.npy")
_BUNDLE_NAMES = {
    BACKEND_MANIFEST, "public-mcp-contract.json", "task-selection.json",
    *_PUBLIC_TASK_FILES,
    *[f"dev/{pool_id}/{name}" for pool_id in PUBLIC_POOL_IDS for name in _DEV_FILE_NAMES],
    *_RUNTIME_FILES,
}


class AREPublicLabelRiskEvaluatorRunner(AREPublicEvaluatorRunner):
    backend_id = BACKEND_ID
    default_dev_image = DEFAULT_IMAGE
    default_dev_image_id = DEFAULT_IMAGE_ID
    default_dev_cell_count = len(PUBLIC_POOL_IDS) * len(LABEL_BUDGETS)
    dev_aggregate_case_id = DEV_CASE_ID
    validation_aggregate_case_id = VALIDATION_CASE_ID
    metric_id = METRIC_ID
    metric_field = METRIC_FIELD
    candidate_path = CANDIDATE_PATH
    public_output_path = PUBLIC_OUTPUT_PATH
    validation_metric_semantics = VALIDATION_METRIC_SEMANTICS

    def __init__(self, *, source_task_root: Path, dev_image: str,
                 dev_image_id: str, **kwargs: Any):
        root = source_task_root.expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("label-risk public task root is missing or unsafe")
        self.source_task_root = root.resolve()
        try:
            verify_public_inputs(self.source_task_root)
        except Exception as exc:
            raise ValueError("label-risk public DEV inputs do not match their frozen pins") from exc
        if dev_image != DEFAULT_IMAGE or dev_image_id != DEFAULT_IMAGE_ID:
            raise ValueError("label-risk DEV image does not match the frozen image pin")
        for key in ("task_id", "task_version", "candidate_path", "dev_image",
                    "dev_image_id", "dev_cell_count", "evaluate_dev"):
            kwargs.pop(key, None)

        def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            return run_public_labelrisk_dev(
                candidate, task_root=self.source_task_root,
                image=dev_image, expected_image_id=dev_image_id,
                timeout_seconds=min(3600, timeout_seconds),
            )

        super().__init__(
            task_id=TASK_ID, task_version=TASK_VERSION,
            candidate_path=CANDIDATE_PATH,
            dev_image=dev_image, dev_image_id=dev_image_id,
            dev_cell_count=self.default_dev_cell_count,
            evaluate_dev=self._validate_dev_result(evaluate_dev), **kwargs,
        )

    @staticmethod
    def _validate_dev_result(evaluate_dev: Any):
        def checked(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = evaluate_dev(candidate, timeout_seconds)
            if not isinstance(result, Mapping):
                raise TrustedEvaluatorError("label-risk public DEV aggregate is malformed")
            metric = result.get("metric") if isinstance(result, Mapping) else None
            if (result.get("status") != "GRADED"
                    or result.get("cell_count") != 48
                    or result.get("seeds_per_cell") != SEEDS_PER_CELL
                    or result.get("task_sha256") != TASK_SHA256
                    or result.get("instruction_sha256") != INSTRUCTION_SHA256
                    or result.get("public_dev_manifest_sha256") != PUBLIC_DEV_MANIFEST_SHA256
                    or isinstance(metric, bool) or not isinstance(metric, (int, float))
                    or not math.isfinite(metric) or metric < 0):
                raise TrustedEvaluatorError("label-risk public DEV aggregate is not bound to its frozen panel")
            return {"status": "GRADED", "dev_score": float(metric), "dev_cells": 48}
        return checked

    @staticmethod
    def _execution_from_score(*, protocol: EvaluatorProtocolV1, approval_digest: str,
                              campaign_id: str, spec_hash: str, candidate_snapshot_id: str,
                              tier_manifest: bytes, tier: str, split: str, score: float):
        return AREPublicEvaluatorRunner._execution_from_score(
            protocol=protocol, approval_digest=approval_digest,
            campaign_id=campaign_id, spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest, tier=tier, split=split,
            score=score, metric_unit="relative_squared_error_ratio",
        )

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("label-risk aggregate has the wrong task identity")
        common = {
            "schema": "autoresearchexam-public-labelrisk-aggregate-v1",
            "task_id": TASK_ID, "task_version": TASK_VERSION,
            "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
            "direction": "MINIMIZE", "aggregation": "MEAN",
            "score_definition": "median of 48 cell ratios; each cell ratio is candidate median squared error divided by matched random-reference median squared error",
        }
        if tier == "dev":
            if dev_cell_count != cls.default_dev_cell_count or dev_image_id != DEFAULT_IMAGE_ID:
                raise ValueError("label-risk DEV scope must pin all 48 public pool-budget cells")
            value = {
                **common, "tier": tier,
                "task_sha256": TASK_SHA256,
                "instruction_sha256": INSTRUCTION_SHA256,
                "public_dev_manifest_sha256": PUBLIC_DEV_MANIFEST_SHA256,
                "public_pool_ids": list(PUBLIC_POOL_IDS),
                "budgets": list(LABEL_BUDGETS),
                "seeds_per_cell": SEEDS_PER_CELL,
                "root_seed": ROOT_SEED,
                "official_seeds_per_cell": 4000,
                "public_dev_image": DEFAULT_IMAGE,
                "public_dev_image_id": dev_image_id,
                "pilot_only": True,
            }
        elif tier == "validation":
            value = {
                **common, "tier": tier,
                "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS,
                "mcp_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
                "candidate_path": CANDIDATE_PATH,
                "public_output_path": PUBLIC_OUTPUT_PATH,
            }
        else:
            raise ValueError("label-risk backend supports only DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    def run(self, *, protocol: EvaluatorProtocolV1, **kwargs: Any) -> EvaluatorExecutionV1:
        if (protocol.metric_id != METRIC_ID or protocol.metric_field != METRIC_FIELD
                or protocol.direction != "MINIMIZE" or protocol.aggregation != "MEAN"
                or protocol.execution_backend_id != self.backend_id):
            raise TrustedEvaluatorError("label-risk protocol does not match the public median-ratio backend")
        return super().run(protocol=protocol, **kwargs)

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("label-risk bundle digest differs from the approved protocol")
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if set(archive.namelist()) != _BUNDLE_NAMES or len(archive.infolist()) != len(_BUNDLE_NAMES):
                    raise ValueError
                payload = {name: archive.read(name) for name in _BUNDLE_NAMES}
                contract = validate_public_mcp_contract(
                    json.loads(payload["public-mcp-contract.json"]),
                    task_id=TASK_ID, task_version=TASK_VERSION,
                    task_sha256=TASK_SHA256,
                    task_instruction_sha256=INSTRUCTION_SHA256,
                )
                if contract.get("contract_sha256") != self.contract_sha256:
                    raise ValueError
                for name, path in _RUNTIME_FILES.items():
                    if path.is_symlink() or payload[name] != path.read_bytes():
                        raise ValueError
                if hashlib.sha256(payload["task-selection.json"]).hexdigest() != SELECTION_SHA256:
                    raise ValueError
                manifest = json.loads(payload[BACKEND_MANIFEST])
                expected = {
                    "schema_version": 1, "backend_id": self.backend_id,
                    "task_id": TASK_ID, "task_version": TASK_VERSION,
                    "task_sha256": TASK_SHA256,
                    "instruction_sha256": INSTRUCTION_SHA256,
                    "dev_manifest_sha256": PUBLIC_DEV_MANIFEST_SHA256,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_contract_file_sha256": hashlib.sha256(payload["public-mcp-contract.json"]).hexdigest(),
                    "public_dev_image": self.dev_image,
                    "public_dev_image_id": self.dev_image_id,
                    "candidate_path": CANDIDATE_PATH,
                    "public_output_path": PUBLIC_OUTPUT_PATH,
                    "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
                    "direction": "MINIMIZE", "aggregation": "MEAN",
                    "pools": list(PUBLIC_POOL_IDS), "budgets": list(LABEL_BUDGETS),
                    "seeds_per_cell": SEEDS_PER_CELL, "root_seed": ROOT_SEED,
                }
                if manifest != expected:
                    raise ValueError
                task_files = {
                    "task.toml": TASK_SHA256,
                    "task-instruction.md": INSTRUCTION_SHA256,
                    "task-Dockerfile": TASK_DOCKERFILE_SHA256,
                    "task-requirements.txt": TASK_REQUIREMENTS_SHA256,
                }
                for name, digest in task_files.items():
                    if hashlib.sha256(payload[name]).hexdigest() != digest:
                        raise ValueError
                dev_entries = []
                for pool_id in PUBLIC_POOL_IDS:
                    for name in ("labels.npy", "meta.json", "surrogate_probs.npy", "target_probs.npy"):
                        member = f"dev/{pool_id}/{name}"
                        dev_entries.append({"path": f"{pool_id}/{name}",
                                            "sha256": hashlib.sha256(payload[member]).hexdigest(),
                                            "size": len(payload[member])})
                canonical = json.dumps(dev_entries, sort_keys=True, separators=(",", ":")).encode()
                if hashlib.sha256(canonical).hexdigest() != PUBLIC_DEV_MANIFEST_SHA256:
                    raise ValueError
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile):
            raise TrustedEvaluatorError("label-risk public backend bundle is missing or malformed") from None
