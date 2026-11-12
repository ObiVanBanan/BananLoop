"""Task-specific trusted evaluator backend for shortest-valid-ci-l2-ece."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from bananloop.evaluator_verification import EvaluatorProtocolV1
from bananloop.trusted_evaluator import (
    EvaluatorExecutionV1,
    TrustedEvaluatorError,
    _case_protocol_digest,
    _manifest_from_bytes,
)

from .optimization_runner import AREPublicEvaluatorRunner
from .public_mcp_contract import validate_public_mcp_contract
from .public_shortest_ci_dev import (
    DEFAULT_REPLICATIONS_PER_SETTING,
    DEV_PANEL_SHA256,
    EXPECTED_SETTINGS,
    INSTRUCTION_SHA256,
    PANEL_SOURCE_SHA256,
    ROOT_SEED,
    TASK_ID,
    TASK_SHA256,
    TASK_VERSION,
)


class AREPublicShortestCIEvaluatorRunner(AREPublicEvaluatorRunner):
    """Public DEV + public VALIDATION adapter for the shortest-CI task.

    ``evaluate_dev`` follows the existing runner seam and must map the public
    runner result to ``{"status": "GRADED", "dev_score": metric,
    "dev_cells": 24}``. Public VALIDATION continues through
    ``PublicAREGrader`` and its durable at-most-once submission ledger.
    """

    backend_id = "autoresearchexam-shortest-ci-public-v1"
    default_dev_image = "bananloop-are-shortest-ci-public-dev:1.1.0"
    default_dev_image_id = "sha256:926252b1f9c047d96fac104aebbcd9128739d23c22a7bb3a7de9ddb1f9682fe7"
    default_dev_cell_count = EXPECTED_SETTINGS
    default_candidate_path = (
        "benchmarks/autoresearchexam/solutions/shortest_valid_ci/interval.py"
    )
    public_output_path = "/app/output/interval.py"
    metric_id = "shortest_ci_task_score"
    metric_field = "task_score"
    validation_metric_semantics = (
        "public_are.validation_score=higher_is_better_task_reward.v1"
    )
    dev_aggregate_case_id = "public-shortest-ci-dev-aggregate"
    validation_aggregate_case_id = "public-shortest-ci-validation-aggregate"

    def __init__(
        self, *, dev_image: str, dev_image_id: str,
        validation_metric_semantics: str,
        evaluate_dev: Any,
        dev_cell_count: int = EXPECTED_SETTINGS, **kwargs: Any,
    ):
        if not isinstance(dev_image, str) or not dev_image:
            raise ValueError("shortest-CI public DEV image name is required")
        if (not isinstance(dev_image_id, str) or len(dev_image_id) != 71
                or not dev_image_id.startswith("sha256:")
                or any(char not in "0123456789abcdef" for char in dev_image_id[7:])):
            raise ValueError("shortest-CI public DEV image ID must be pinned by SHA-256")
        if type(dev_cell_count) is not int or dev_cell_count != EXPECTED_SETTINGS:
            raise ValueError("shortest-CI public DEV panel must contain 24 settings")
        if validation_metric_semantics != type(self).validation_metric_semantics:
            raise ValueError("shortest-CI validation score semantics must be explicitly pinned")
        if not callable(evaluate_dev):
            raise TypeError("evaluate_dev must call the public shortest-CI DEV runner")
        self.validation_metric_semantics = validation_metric_semantics

        def evaluate_task_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = evaluate_dev(candidate, timeout_seconds)
            if (not isinstance(result, Mapping) or result.get("status") != "GRADED"
                    or result.get("task_id") != TASK_ID
                    or result.get("task_version") != TASK_VERSION
                    or result.get("panel_count") != EXPECTED_SETTINGS
                    or result.get("replications_per_setting") != DEFAULT_REPLICATIONS_PER_SETTING
                    or result.get("total_replications")
                    != EXPECTED_SETTINGS * DEFAULT_REPLICATIONS_PER_SETTING
                    or result.get("root_seed") != ROOT_SEED
                    or result.get("task_sha256") != TASK_SHA256
                    or result.get("instruction_sha256") != INSTRUCTION_SHA256
                    or result.get("panel_source_sha256") != PANEL_SOURCE_SHA256
                    or result.get("dev_panel_sha256") != DEV_PANEL_SHA256):
                raise TrustedEvaluatorError("shortest-CI public DEV result is not bound to its frozen panel")
            metric = result.get("metric")
            if (isinstance(metric, bool) or not isinstance(metric, (int, float))
                    or not 0.0 <= metric <= 1.0):
                raise TrustedEvaluatorError("shortest-CI public DEV metric is invalid")
            return {"status": "GRADED", "dev_score": 1.0 - float(metric),
                    "dev_cells": EXPECTED_SETTINGS}

        super().__init__(
            dev_image=dev_image,
            dev_image_id=dev_image_id,
            dev_cell_count=dev_cell_count,
            evaluate_dev=evaluate_task_dev,
            **kwargs,
        )

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("shortest-CI aggregate case has the wrong task identity")
        if dev_cell_count != EXPECTED_SETTINGS:
            raise ValueError("shortest-CI aggregate case has the wrong public panel count")
        common = {
            "schema": "autoresearchexam-public-shortest-ci-aggregate-v1",
            "task_id": TASK_ID,
            "task_version": TASK_VERSION,
            "metric_id": cls.metric_id,
            "metric_field": cls.metric_field,
            "direction": "MAXIMIZE",
            "validation_metric_semantics": cls.validation_metric_semantics,
        }
        if tier == "dev":
            value = {
                **common,
                "tier": tier,
                "public_metric_field": "relative_interval_length",
                "public_metric_direction": "MINIMIZE",
                "measurement": "task_score = 1 - public metric; public metric is the mean setting score, where a setting scores mean relative interval length if sampled coverage is at least 0.88, otherwise 1.0",
                "public_dev_image_id": dev_image_id,
                "panel_source_sha256": PANEL_SOURCE_SHA256,
                "dev_panel_sha256": DEV_PANEL_SHA256,
                "replications_per_setting": DEFAULT_REPLICATIONS_PER_SETTING,
                "root_seed": ROOT_SEED,
                "panel_count": EXPECTED_SETTINGS,
            }
        elif tier == "validation":
            value = {
                **common,
                "tier": tier,
                "measurement": "one official higher-is-better task reward returned in validation_score by the public ARE validation endpoint",
                "public_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
                "candidate_path": cls.default_candidate_path,
                "public_output_path": cls.public_output_path,
            }
        else:
            raise ValueError("shortest-CI public backend only supports DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def run(self, *, protocol: EvaluatorProtocolV1, **kwargs: Any) -> EvaluatorExecutionV1:
        if (protocol.metric_id != self.metric_id
                or protocol.metric_field != self.metric_field
                or protocol.direction != "MAXIMIZE"
                or protocol.aggregation != "MEAN"):
            raise TrustedEvaluatorError("shortest-CI protocol must maximize mean normalized task_score")
        if protocol.execution_backend_id != self.backend_id:
            raise TrustedEvaluatorError("shortest-CI protocol is not pinned to this public backend")
        return super().run(protocol=protocol, **kwargs)

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("shortest-CI evaluator bundle digest differs from approved protocol")
        expected_names = {
            "are-public-shortest-ci-backend.json",
            "public_shortest_ci_dev.py",
            "public_devkit.py",
            "optimization_runner.py",
            "shortest_ci_runner.py",
            "public_grader.py",
            "are_public_mcp_client.py",
            "public_mcp_contract.py",
            "public-mcp-contract.json",
            "task-instruction.md",
            "task.toml",
            "task-Dockerfile",
            "task-requirements.txt",
            "task-panel.py",
            "task-dev-panel.json",
        }
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                infos = archive.infolist()
                if set(archive.namelist()) != expected_names or len(infos) != len(expected_names):
                    raise ValueError
                payload = {name: archive.read(name) for name in expected_names}
                manifest = json.loads(payload["are-public-shortest-ci-backend.json"])
                contract = validate_public_mcp_contract(
                    json.loads(payload["public-mcp-contract.json"]),
                    task_id=TASK_ID,
                    task_version=TASK_VERSION,
                    task_sha256=TASK_SHA256,
                    task_instruction_sha256=INSTRUCTION_SHA256,
                )
                runtime_names = {
                    "public_shortest_ci_dev.py": "public_shortest_ci_dev.py",
                    "public_devkit.py": "public_devkit.py",
                    "optimization_runner.py": "optimization_runner.py",
                    "shortest_ci_runner.py": "shortest_ci_runner.py",
                    "public_grader.py": "public_grader.py",
                    "are_public_mcp_client.py": "are_public_mcp_client.py",
                    "public_mcp_contract.py": "public_mcp_contract.py",
                }
                for archive_name, local_name in runtime_names.items():
                    runtime_path = Path(__file__).with_name(local_name)
                    if runtime_path.is_symlink() or not runtime_path.is_file():
                        raise ValueError
                    if payload[archive_name] != runtime_path.read_bytes():
                        raise ValueError
                task_toml = payload["task.toml"].decode("utf-8")
                if (hashlib.sha256(payload["task.toml"]).hexdigest() != TASK_SHA256
                        or hashlib.sha256(payload["task-instruction.md"]).hexdigest() != INSTRUCTION_SHA256
                        or hashlib.sha256(payload["task-panel.py"]).hexdigest() != PANEL_SOURCE_SHA256
                        or hashlib.sha256(payload["task-dev-panel.json"]).hexdigest() != DEV_PANEL_SHA256
                        or f'version = "{TASK_VERSION}"' not in task_toml
                        or contract.get("contract_sha256") != self.contract_sha256
                        or self.grader.contract_sha256 != self.contract_sha256):
                    raise ValueError
                task_file_hashes = {
                    name: hashlib.sha256(payload[name]).hexdigest()
                    for name in ("task.toml", "task-Dockerfile", "task-requirements.txt")
                }
                runtime_hashes = {
                    f"{name}_sha256": hashlib.sha256(payload[archive_name]).hexdigest()
                    for archive_name, name in (
                        ("public_shortest_ci_dev.py", "public_runner"),
                        ("public_devkit.py", "public_devkit"),
                        ("optimization_runner.py", "optimization_runner"),
                        ("shortest_ci_runner.py", "backend_runner"),
                        ("public_grader.py", "public_grader"),
                        ("are_public_mcp_client.py", "mcp_client"),
                        ("public_mcp_contract.py", "mcp_contract_source"),
                    )
                }
                expected = {
                    "schema_version": 1,
                    "backend_id": self.backend_id,
                    "task_id": TASK_ID,
                    "task_version": TASK_VERSION,
                    "task_sha256": TASK_SHA256,
                    "task_instruction_sha256": INSTRUCTION_SHA256,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_contract_file_sha256": hashlib.sha256(
                        payload["public-mcp-contract.json"],
                    ).hexdigest(),
                    "public_endpoint": "http://grader:8000/mcp",
                    "candidate_path": self.default_candidate_path,
                    "public_output_path": self.public_output_path,
                    "metric_id": self.metric_id,
                    "metric_field": self.metric_field,
                    "direction": "MAXIMIZE",
                    "aggregation": "MEAN",
                    "validation_metric_semantics": self.validation_metric_semantics,
                    "public_dev_image": self.dev_image,
                    "public_dev_image_id": self.dev_image_id,
                    "public_dev_panel_count": EXPECTED_SETTINGS,
                    "replications_per_setting": DEFAULT_REPLICATIONS_PER_SETTING,
                    "root_seed": ROOT_SEED,
                    "panel_source_sha256": PANEL_SOURCE_SHA256,
                    "dev_panel_sha256": DEV_PANEL_SHA256,
                    "task_file_sha256": task_file_hashes,
                    "task_instruction_sha256_verified": hashlib.sha256(
                        payload["task-instruction.md"],
                    ).hexdigest(),
                    "public_dev_panel_execution": {
                        "container_network": "none",
                        "root_filesystem_read_only": True,
                        "candidate_call_timeout_seconds": 2.0,
                        "mounted_candidate_path": self.public_output_path,
                    },
                    "public_dev_score_transform": {
                        "source_metric": "relative_interval_length",
                        "source_direction": "MINIMIZE",
                        "task_score": "1 - relative_interval_length",
                        "task_score_direction": "MAXIMIZE",
                    },
                    "aggregate_case_ids": {
                        "dev": self.dev_aggregate_case_id,
                        "validation": self.validation_aggregate_case_id,
                    },
                    **runtime_hashes,
                }
                if manifest != expected:
                    raise ValueError
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError,
                zipfile.BadZipFile, KeyError):
            raise TrustedEvaluatorError("shortest-CI public backend bundle is missing or malformed") from None

    def _validate_aggregate_case(
        self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes], *, tier: str,
    ) -> None:
        expected_id = self.dev_aggregate_case_id if tier == "dev" else self.validation_aggregate_case_id
        if len(cases) != 1 or cases[0].case_id != expected_id or cases[0].repetition != 1:
            raise TrustedEvaluatorError(f"shortest-CI {tier.upper()} must declare one aggregate case")
        identity = (expected_id, 1)
        if set(case_inputs) != {identity}:
            raise TrustedEvaluatorError(f"shortest-CI {tier.upper()} inputs do not match its aggregate case")
        expected = self.aggregate_case_input(
            tier=tier,
            task_id=self.task_id,
            task_version=self.task_version,
            contract_sha256=self.contract_sha256,
            dev_image_id=self.dev_image_id,
            dev_cell_count=self.dev_cell_count,
        )
        payload = case_inputs[identity]
        if (not isinstance(payload, bytes) or payload != expected
                or hashlib.sha256(payload).hexdigest() != cases[0].input_sha256):
            raise TrustedEvaluatorError(f"shortest-CI {tier.upper()} scope differs from its approved input")

    @staticmethod
    def _execution_from_score(
        *, protocol: EvaluatorProtocolV1, approval_digest: str,
        campaign_id: str, spec_hash: str, candidate_snapshot_id: str,
        tier_manifest: bytes, tier: str, split: str, score: float,
    ) -> EvaluatorExecutionV1:
        return AREPublicEvaluatorRunner._execution_from_score(
            protocol=protocol,
            approval_digest=approval_digest,
            campaign_id=campaign_id,
            spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest,
            tier=tier,
            split=split,
            score=score,
            metric_unit="normalized_task_score",
        )
