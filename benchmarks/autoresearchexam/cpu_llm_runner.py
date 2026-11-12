"""Trusted public DEV + ARE VALIDATION adapter for CPU LLM decode throughput."""
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

from . import public_cpu_llm_dev as dev
from .optimization_runner import AREPublicEvaluatorRunner
from .public_mcp_contract import validate_public_mcp_contract

TASK_ID = dev.TASK_ID
TASK_VERSION = dev.TASK_VERSION
CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/cpu_llm/engine.py"
PUBLIC_OUTPUT_PATH = "/app/output/engine.py"
BACKEND_ID = "autoresearchexam-cpu-llm-public-v1"
BACKEND_MANIFEST = "are-public-cpu-llm-backend.json"
DEV_CASE_ID = "public-cpu-llm-dev-aggregate"
VALIDATION_CASE_ID = "public-cpu-llm-validation-aggregate"
METRIC_ID = "cpu_llm_decode_speedup"
METRIC_FIELD = "speedup"
METRIC_DIRECTION = "MAXIMIZE"
METRIC_AGGREGATION = "MEAN"
VALIDATION_METRIC_SEMANTICS = "public_are.validation_score=higher_is_better_task_reward.v1"

_RUNTIME_FILES = {
    "cpu_llm_runner.py": Path(__file__),
    "public_cpu_llm_dev.py": Path(dev.__file__),
    "public_devkit.py": Path(__file__).with_name("public_devkit.py"),
    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py"),
    "public_grader.py": Path(__file__).with_name("public_grader.py"),
    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py"),
    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py"),
}
_PUBLIC_TASK_FILES = {
    "task.toml": "task.toml",
    "task-instruction.md": "instruction.md",
    "task-Dockerfile": "environment/Dockerfile",
    "task-dockerignore": "environment/.dockerignore",
    "task-requirements.txt": "environment/requirements.txt",
    "public-bench.py": "environment/app/harness/bench.py",
    "public-metric-core.py": "environment/app/harness/metric_core.py",
    "public-runner.py": "environment/app/harness/runner.py",
    "public-reference-engine.py": "environment/app/reference/reference_engine.py",
    **{f"public-dev-workload-{i}.json": f"environment/app/data/dev_workload_{i}.json"
       for i in range(4)},
    "public-warmup-workload.json": "environment/app/data/warmup_workload.json",
}
_BUNDLE_NAMES = {
    BACKEND_MANIFEST, "public-mcp-contract.json", "task-selection.json",
    *_PUBLIC_TASK_FILES, *_RUNTIME_FILES,
}


class AREPublicCPULLMEvaluatorRunner(AREPublicEvaluatorRunner):
    backend_id = BACKEND_ID
    default_dev_image = dev.DEFAULT_IMAGE
    default_dev_image_id = dev.DEFAULT_IMAGE_ID
    default_dev_cell_count = len(dev.PILOT_DRAWS)
    dev_aggregate_case_id = DEV_CASE_ID
    validation_aggregate_case_id = VALIDATION_CASE_ID
    candidate_path = CANDIDATE_PATH
    public_output_path = PUBLIC_OUTPUT_PATH
    metric_id = METRIC_ID
    metric_field = METRIC_FIELD
    validation_metric_semantics = VALIDATION_METRIC_SEMANTICS

    def __init__(self, *, source_task_root: Path, dev_image: str,
                 dev_image_id: str, **kwargs: Any):
        root = source_task_root.expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("CPU LLM public task root is missing or unsafe")
        self.source_task_root = root.resolve()
        try:
            self.input_pins = dev.verify_public_inputs(self.source_task_root)
        except dev.PublicCPULLMError:
            raise ValueError("CPU LLM public files differ from their frozen pins") from None
        if dev_image != dev.DEFAULT_IMAGE or dev_image_id != dev.DEFAULT_IMAGE_ID:
            raise ValueError("CPU LLM public image differs from its frozen pin")
        for key in ("task_id", "task_version", "candidate_path", "dev_image",
                    "dev_image_id", "dev_cell_count", "evaluate_dev"):
            kwargs.pop(key, None)

        def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = dev.run_public_cpullm_dev(
                candidate, task_root=self.source_task_root,
                image=dev_image, expected_image_id=dev_image_id,
                timeout_seconds=min(3600, timeout_seconds),
            )
            metric = result.get("metric") if isinstance(result, Mapping) else None
            if (not isinstance(result, Mapping) or result.get("status") != "GRADED"
                    or result.get("task_sha256") != dev.TASK_SHA256
                    or result.get("instruction_sha256") != dev.INSTRUCTION_SHA256
                    or result.get("draws") != list(dev.PILOT_DRAWS)
                    or result.get("request_limit") != dev.PILOT_REQUEST_LIMIT
                    or result.get("official_draws") != 3
                    or result.get("official_requests_per_draw") != 128
                    or result.get("pilot_only") is not True
                    or isinstance(metric, bool) or not isinstance(metric, (int, float))
                    or not math.isfinite(metric) or metric <= 0
                    or result.get("token_agreement", 0) < 0.99):
                raise TrustedEvaluatorError("CPU LLM DEV result is not bound to its frozen public panel")
            return {"status": "GRADED", "dev_score": float(metric),
                    "dev_cells": len(dev.PILOT_DRAWS)}

        super().__init__(
            grader=kwargs.pop("grader"), publish_candidate=kwargs.pop("publish_candidate"),
            task_id=TASK_ID, task_version=TASK_VERSION,
            contract_sha256=kwargs.pop("contract_sha256"),
            candidate_path=CANDIDATE_PATH,
            dev_image=dev_image, dev_image_id=dev_image_id,
            dev_cell_count=len(dev.PILOT_DRAWS), evaluate_dev=evaluate_dev,
            **kwargs,
        )

    @classmethod
    def aggregate_case_input(cls, *, tier: str, task_id: str, task_version: str,
                             contract_sha256: str, dev_image_id: str,
                             dev_cell_count: int) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("CPU LLM aggregate scope has the wrong task identity")
        common = {"schema": "autoresearchexam-public-cpu-llm-aggregate-v1",
                  "task_id": TASK_ID, "task_version": TASK_VERSION,
                  "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
                  "direction": METRIC_DIRECTION, "aggregation": METRIC_AGGREGATION,
                  "candidate_path": CANDIDATE_PATH, "public_output_path": PUBLIC_OUTPUT_PATH,
                  "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS}
        if tier == "dev":
            if dev_cell_count != len(dev.PILOT_DRAWS) or dev_image_id != dev.DEFAULT_IMAGE_ID:
                raise ValueError("CPU LLM DEV scope must pin the two public draws and image")
            value = {**common, "tier": tier,
                     "measurement": "median generated-token throughput speedup over the public reference",
                     "task_sha256": dev.TASK_SHA256,
                     "instruction_sha256": dev.INSTRUCTION_SHA256,
                     "public_bench_sha256": dev.PUBLIC_BENCH_SHA256,
                     "public_metric_core_sha256": dev.PUBLIC_METRIC_SHA256,
                     "public_draws": list(dev.PILOT_DRAWS),
                     "requests_per_draw": dev.PILOT_REQUEST_LIMIT,
                     "token_agreement_minimum": 0.99,
                     "official_draws": 3, "official_requests_per_draw": 128,
                     "public_dev_image": dev.DEFAULT_IMAGE,
                     "public_dev_image_id": dev_image_id,
                     "model_config_sha256": dev.MODEL_CONFIG_SHA256,
                     "model_weights_sha256": dev.MODEL_WEIGHTS_SHA256,
                     "pilot_only": True}
        elif tier == "validation":
            value = {**common, "tier": tier,
                     "measurement": "one official higher-is-better task reward from public ARE VALIDATION",
                     "mcp_endpoint": "http://grader:8000/mcp",
                     "mcp_contract_sha256": contract_sha256}
        else:
            raise ValueError("CPU LLM backend supports only DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    def run(self, *, protocol: EvaluatorProtocolV1, **kwargs: Any) -> EvaluatorExecutionV1:
        if (protocol.metric_id != METRIC_ID or protocol.metric_field != METRIC_FIELD
                or protocol.direction != METRIC_DIRECTION
                or protocol.aggregation != METRIC_AGGREGATION
                or protocol.execution_backend_id != BACKEND_ID):
            raise TrustedEvaluatorError("CPU LLM protocol does not match its public throughput metric")
        return super().run(protocol=protocol, **kwargs)

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("CPU LLM bundle digest differs from approved protocol")
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if set(archive.namelist()) != _BUNDLE_NAMES or len(archive.infolist()) != len(_BUNDLE_NAMES):
                    raise ValueError
                payload = {name: archive.read(name) for name in _BUNDLE_NAMES}
                contract_bytes = payload["public-mcp-contract.json"]
                contract = validate_public_mcp_contract(
                    json.loads(contract_bytes), task_id=TASK_ID,
                    task_version=TASK_VERSION, task_sha256=dev.TASK_SHA256,
                    task_instruction_sha256=dev.INSTRUCTION_SHA256)
                if contract.get("contract_sha256") != self.contract_sha256:
                    raise ValueError
                for name, path in _RUNTIME_FILES.items():
                    if path.is_symlink() or path.read_bytes() != payload[name]:
                        raise ValueError
                if hashlib.sha256(payload["task-selection.json"]).hexdigest() != dev.SELECTION_SHA256:
                    raise ValueError
                for name, rel in _PUBLIC_TASK_FILES.items():
                    expected = next(digest for source, digest in dev._PUBLIC_FILES.values()
                                    if source == rel)
                    if hashlib.sha256(payload[name]).hexdigest() != expected:
                        raise ValueError
                manifest = json.loads(payload[BACKEND_MANIFEST])
                expected_manifest = {
                    "schema_version": 1, "backend_id": BACKEND_ID,
                    "task_id": TASK_ID, "task_version": TASK_VERSION,
                    "task_sha256": dev.TASK_SHA256,
                    "instruction_sha256": dev.INSTRUCTION_SHA256,
                    "selection_sha256": dev.SELECTION_SHA256,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_contract_file_sha256": hashlib.sha256(contract_bytes).hexdigest(),
                    "dev_image": self.dev_image, "dev_image_id": self.dev_image_id,
                    "candidate_path": CANDIDATE_PATH, "public_output_path": PUBLIC_OUTPUT_PATH,
                    "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
                    "direction": METRIC_DIRECTION, "aggregation": METRIC_AGGREGATION,
                    "public_draws": list(dev.PILOT_DRAWS),
                    "requests_per_draw": dev.PILOT_REQUEST_LIMIT,
                    "model_config_sha256": dev.MODEL_CONFIG_SHA256,
                    "model_weights_sha256": dev.MODEL_WEIGHTS_SHA256,
                }
                if manifest != expected_manifest:
                    raise ValueError
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile):
            raise TrustedEvaluatorError("CPU LLM public backend bundle is missing or malformed") from None

    @staticmethod
    def _execution_from_score(*, protocol: EvaluatorProtocolV1,
                              approval_digest: str, campaign_id: str,
                              spec_hash: str, candidate_snapshot_id: str,
                              tier_manifest: bytes, tier: str, split: str,
                              score: float) -> EvaluatorExecutionV1:
        return AREPublicEvaluatorRunner._execution_from_score(
            protocol=protocol, approval_digest=approval_digest,
            campaign_id=campaign_id, spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest, tier=tier, split=split,
            score=score, metric_unit="throughput_speedup")
