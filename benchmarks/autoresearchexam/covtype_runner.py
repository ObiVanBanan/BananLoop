"""Public external DEV proxy + official public VALIDATION adapter for CovType."""
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

from . import public_covtype_dev as dev
from .optimization_runner import AREPublicEvaluatorRunner
from .public_mcp_contract import validate_public_mcp_contract


TASK_ID = dev.TASK_ID
TASK_VERSION = dev.TASK_VERSION
CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/covtype/policy.py"
PUBLIC_OUTPUT_PATH = "/app/output/policy.py"
BACKEND_ID = "autoresearchexam-covtype-external-proxy-v1"
BACKEND_MANIFEST = "are-public-covtype-backend.json"
DEV_CASE_ID = "public-covtype-external-dev-aggregate"
VALIDATION_CASE_ID = "public-covtype-validation-aggregate"
METRIC_ID = "covtype_balanced_accuracy"
METRIC_FIELD = "balanced_accuracy"
METRIC_DIRECTION = "MAXIMIZE"
METRIC_AGGREGATION = "MEAN"
VALIDATION_METRIC_SEMANTICS = "public_are.validation_score=higher_is_better_balanced_accuracy.v1"
PROXY_MANIFEST_SHA256 = "7714bccea3285d349bd4288bded200ba06ad97385256491ec8f9e6ebc1370d48"
TASK_SHA256 = "445e36e14db8c39ee02d137a26b878d880648d14b90f8fe164c275de03d0a1e9"
INSTRUCTION_SHA256 = "0cf06c38988de7b02072c8aaa3f07e2b2c1253392d6b11dd760206919a1e0fa9"
PREPARE_DATA_SHA256 = "c2c7a8235d8ddeed17708622051bc34216e851347937de513ae7994a1106744c"
SELECTION_SHA256 = "071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316"

_RUNTIME_FILES = {
    "covtype_runner.py": Path(__file__),
    "public_covtype_dev.py": Path(dev.__file__),
    "build_covtype_proxy.py": Path(__file__).with_name("build_covtype_proxy.py"),
    "covtype-worker.py": Path(__file__).parent / "covtype_worker" / "worker.py",
    "covtype-worker.Dockerfile": Path(__file__).parent / "covtype_worker" / "Dockerfile",
    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py"),
    "public_grader.py": Path(__file__).with_name("public_grader.py"),
    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py"),
    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py"),
}
_TASK_FILES = {
    "task.toml": "task.toml",
    "task-instruction.md": "instruction.md",
    "public-data-prep.py": "environment/prepare_agent_data.py",
}
_BUNDLE_NAMES = {
    BACKEND_MANIFEST, "task-selection.json", "public-mcp-contract.json",
    "proxy-manifest.json", *_TASK_FILES, *_RUNTIME_FILES,
}


class AREPublicCovTypeEvaluatorRunner(AREPublicEvaluatorRunner):
    backend_id = BACKEND_ID
    default_dev_image = dev.DEFAULT_IMAGE
    default_dev_image_id = dev.DEFAULT_IMAGE_ID
    default_dev_cell_count = 1
    dev_aggregate_case_id = DEV_CASE_ID
    validation_aggregate_case_id = VALIDATION_CASE_ID
    candidate_path = CANDIDATE_PATH
    public_output_path = PUBLIC_OUTPUT_PATH
    metric_id = METRIC_ID
    metric_field = METRIC_FIELD
    validation_metric_semantics = VALIDATION_METRIC_SEMANTICS

    def __init__(self, *, source_task_root: Path, proxy_root: Path,
                 dev_image: str, dev_image_id: str, **kwargs: Any):
        root = source_task_root.expanduser()
        proxy = proxy_root.expanduser()
        if root.is_symlink() or not root.is_dir() or proxy.is_symlink() or not proxy.is_dir():
            raise ValueError("CovType public task or external proxy root is missing or unsafe")
        self.source_task_root = root.resolve()
        self.proxy_root = proxy.resolve()
        if dev_image != dev.DEFAULT_IMAGE or dev_image_id != dev.DEFAULT_IMAGE_ID:
            raise ValueError("CovType worker image differs from its immutable local pin")
        try:
            self.proxy_manifest = dev.verify_proxy_panel(self.proxy_root)
        except dev.PublicCovTypeDevError:
            raise ValueError("CovType external proxy data differs from its frozen manifest") from None
        for key in ("task_id", "task_version", "candidate_path", "dev_image",
                    "dev_image_id", "dev_cell_count", "evaluate_dev"):
            kwargs.pop(key, None)

        def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = dev.run_public_covtype_dev(
                candidate, proxy_dir=self.proxy_root, image=dev_image,
                expected_image_id=dev_image_id,
                timeout_seconds=min(3600, max(30, timeout_seconds)),
            )
            metric = result.get("score") if isinstance(result, Mapping) else None
            if (not isinstance(result, Mapping) or result.get("status") != "GRADED"
                    or result.get("task_id") != TASK_ID
                    or result.get("task_version") != TASK_VERSION
                    or result.get("official_benchmark_compatible") is not False
                    or result.get("proxy_manifest_sha256") != PROXY_MANIFEST_SHA256
                    or result.get("evaluation_rows") != 10_000
                    or result.get("label_rows_acquired", -1) > 2_000
                    or isinstance(metric, bool) or not isinstance(metric, (int, float))
                    or not math.isfinite(metric) or not 0.0 <= metric <= 1.0):
                raise TrustedEvaluatorError("CovType external proxy result differs from its pinned scope")
            return {"status": "GRADED", "dev_score": float(metric), "dev_cells": 1}

        super().__init__(
            grader=kwargs.pop("grader"), publish_candidate=kwargs.pop("publish_candidate"),
            task_id=TASK_ID, task_version=TASK_VERSION,
            contract_sha256=kwargs.pop("contract_sha256"),
            candidate_path=CANDIDATE_PATH, dev_image=dev_image,
            dev_image_id=dev_image_id, dev_cell_count=1,
            evaluate_dev=evaluate_dev, **kwargs,
        )

    @classmethod
    def aggregate_case_input(cls, *, tier: str, task_id: str, task_version: str,
                             contract_sha256: str, dev_image_id: str,
                             dev_cell_count: int) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("CovType aggregate scope has the wrong task identity")
        common = {
            "schema": "autoresearchexam-covtype-aggregate-v1",
            "task_id": TASK_ID, "task_version": TASK_VERSION, "tier": tier,
            "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
            "direction": METRIC_DIRECTION, "aggregation": METRIC_AGGREGATION,
            "candidate_path": CANDIDATE_PATH,
            "public_output_path": PUBLIC_OUTPUT_PATH,
            "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS,
        }
        if tier == "dev":
            if dev_cell_count != 1 or dev_image_id != dev.DEFAULT_IMAGE_ID:
                raise ValueError("CovType DEV scope must pin its one external proxy image and aggregate")
            value = {
                **common, "tier": tier,
                "measurement": "balanced accuracy on 10,000 independent public UCI rows",
                "proxy_manifest_sha256": PROXY_MANIFEST_SHA256,
                "source_sha256": dev.SOURCE_SHA256,
                "official_generator_excluded_rows": 250_000,
                "label_budget": dev.LABEL_BUDGET,
                "label_price_model": "static 1.0 per label",
                "feature_budget_per_case": dev.FEATURE_BUDGET,
                "feature_price_model": "fixed public task metadata costs",
                "image": dev.DEFAULT_IMAGE, "image_id": dev.DEFAULT_IMAGE_ID,
                "pilot_only": True, "official_benchmark_compatible": False,
            }
        elif tier == "validation":
            value = {
                **common, "tier": tier,
                "measurement": "one official task balanced-accuracy reward from public ARE VALIDATION",
                "mcp_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
            }
        else:
            raise ValueError("CovType backend supports only DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    def run(self, *, protocol: EvaluatorProtocolV1, **kwargs: Any) -> EvaluatorExecutionV1:
        if (protocol.metric_id != METRIC_ID or protocol.metric_field != METRIC_FIELD
                or protocol.direction != METRIC_DIRECTION
                or protocol.aggregation != METRIC_AGGREGATION
                or protocol.execution_backend_id != BACKEND_ID):
            raise TrustedEvaluatorError("CovType protocol does not match its public balanced-accuracy metric")
        return super().run(protocol=protocol, **kwargs)

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("CovType public bundle digest differs from approved protocol")
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if (set(archive.namelist()) != _BUNDLE_NAMES
                        or len(archive.infolist()) != len(_BUNDLE_NAMES)):
                    raise ValueError
                payload = {name: archive.read(name) for name in _BUNDLE_NAMES}
                contract = validate_public_mcp_contract(
                    json.loads(payload["public-mcp-contract.json"]),
                    task_id=TASK_ID, task_version=TASK_VERSION,
                    task_sha256=TASK_SHA256,
                    task_instruction_sha256=INSTRUCTION_SHA256,
                )
                if (contract.get("contract_sha256") != self.contract_sha256
                        or hashlib.sha256(payload["task-selection.json"]).hexdigest()
                            != SELECTION_SHA256):
                    raise ValueError
                if (hashlib.sha256(payload["task.toml"]).hexdigest() != TASK_SHA256
                        or hashlib.sha256(payload["task-instruction.md"]).hexdigest()
                            != INSTRUCTION_SHA256
                        or hashlib.sha256(payload["public-data-prep.py"]).hexdigest()
                            != PREPARE_DATA_SHA256):
                    raise ValueError
                for name, path in _RUNTIME_FILES.items():
                    if path.is_symlink() or path.read_bytes() != payload[name]:
                        raise ValueError
                for name, relative in _TASK_FILES.items():
                    source = self.source_task_root / relative
                    if source.is_symlink() or source.read_bytes() != payload[name]:
                        raise ValueError
                manifest = json.loads(payload["proxy-manifest.json"])
                if (_sha256(payload["proxy-manifest.json"]) != PROXY_MANIFEST_SHA256
                        or manifest.get("official_benchmark_compatible") is not False
                        or manifest.get("source_sha256") != dev.SOURCE_SHA256
                        or manifest != self.proxy_manifest):
                    raise ValueError
                backend = json.loads(payload[BACKEND_MANIFEST])
                if backend != self._expected_backend_manifest(contract["contract_sha256"]):
                    raise ValueError
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
                zipfile.BadZipFile):
            raise TrustedEvaluatorError("CovType public backend bundle is missing or malformed") from None

    def _expected_backend_manifest(self, contract_sha256: str) -> dict[str, Any]:
        return {
            "schema_version": 1, "backend_id": BACKEND_ID,
            "task_id": TASK_ID, "task_version": TASK_VERSION,
            "task_sha256": TASK_SHA256,
            "instruction_sha256": INSTRUCTION_SHA256,
            "selection_sha256": SELECTION_SHA256,
            "prepare_data_sha256": PREPARE_DATA_SHA256,
            "proxy_manifest_sha256": PROXY_MANIFEST_SHA256,
            "source_archive_sha256": dev.SOURCE_SHA256,
            "mcp_contract_sha256": contract_sha256,
            "dev_image": dev.DEFAULT_IMAGE,
            "dev_image_id": dev.DEFAULT_IMAGE_ID,
            "candidate_path": CANDIDATE_PATH,
            "public_output_path": PUBLIC_OUTPUT_PATH,
            "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
            "direction": METRIC_DIRECTION, "aggregation": METRIC_AGGREGATION,
            "official_benchmark_compatible": False,
            "price_simulation": "static-public-proxy-v1",
        }

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
            score=score, metric_unit="balanced_accuracy",
        )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
