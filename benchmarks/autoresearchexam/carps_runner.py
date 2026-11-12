"""Task-specific public DEV + ARE validation backend for CARPS subset selection.

The public DEV diagnostic is ``-mean_exact_linf_star_discrepancy`` so larger
values are better. Public VALIDATION preserves the ARE grader's
``validation_score`` as the official task score (also larger is better); it is
not mislabeled as raw discrepancy. Both sources are recorded in evaluator
diagnostics because their numeric scales differ.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

from bananloop.evaluator_verification import EvaluatorProtocolV1
from bananloop.trusted_evaluator import EvaluatorExecutionV1, TrustedEvaluatorError

from .optimization_runner import AREPublicEvaluatorRunner
from .public_carps_dev import (
    EXAMPLE_CLOUD_SHA256,
    EXAMPLES_MANIFEST_SHA256,
    INSTRUCTION_SHA256,
    TASK_ID,
    TASK_TOML_SHA256,
    TASK_VERSION,
    PublicCARPSEvaluatorError,
    evaluate_carps_public_dev,
)


BACKEND_ID = "autoresearchexam-carps-public-v1"
CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/carps/select_subset.py"
PUBLIC_OUTPUT_PATH = "/app/output/select_subset.py"
PUBLIC_PANEL_DIGEST = "43c26fca97e46f5a715922a9f65744d24bbe8db18851fe65145380183f9ab82e"
PUBLIC_MCP_ENDPOINT = "http://grader:8000/mcp"
METRIC_ID = "autoresearchexam.carps.public_task_score"
METRIC_FIELD = "public_task_score"
METRIC_DIRECTION = "MAXIMIZE"
DEV_METRIC_SOURCE = "negative_mean_exact_linf_star_discrepancy_over_8_public_examples"
VALIDATION_METRIC_SOURCE = "public_are_validation_score_official_task_score"
VALIDATION_METRIC_SEMANTICS = (
    "public task docs say higher task score corresponds to lower discrepancy; "
    "the official validation_score is not interpreted as raw d-star"
)

_BACKEND_FILES = (
    "are-public-carps-backend.json",
    "carps_runner.py",
    "public_carps_dev.py",
    "optimization_runner.py",
    "public_grader.py",
    "are_public_mcp_client.py",
    "public_mcp_contract.py",
    "public-mcp-contract.json",
    "task.toml",
    "task-instruction.md",
    "task-README.md",
    "examples-manifest.json",
    *(f"examples/{name}" for name in EXAMPLE_CLOUD_SHA256),
)
_RUNTIME_SOURCES = {
    "carps_runner.py": Path(__file__),
    "public_carps_dev.py": Path(__file__).with_name("public_carps_dev.py"),
    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py"),
    "public_grader.py": Path(__file__).with_name("public_grader.py"),
    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py"),
    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py"),
}


class AREPublicCARPSEvaluatorRunner(AREPublicEvaluatorRunner):
    """Bind CARPS public example DEV and public ARE VALIDATION to one protocol."""

    backend_id = BACKEND_ID
    task_id = TASK_ID
    task_version = TASK_VERSION
    candidate_path = CANDIDATE_PATH
    public_output_path = PUBLIC_OUTPUT_PATH
    default_dev_cell_count = 8
    default_dev_image = "bananloop-are-carps-public-dev:1.1.0"
    default_dev_image_id = "sha256:59f83ddb37abcc1a284c673a02a8b811aea94e06e99ffc40f745921a13b5e2e2"
    dev_aggregate_case_id = "public-carps-dev-aggregate"
    validation_aggregate_case_id = "public-carps-validation-aggregate"
    default_total_dev_timeout_seconds = 180

    def __init__(
        self, *, public_dev_image: str, public_dev_image_id: str,
        active_public_task_root: Path | None = None,
        total_dev_timeout_seconds: int = default_total_dev_timeout_seconds,
        **kwargs: Any,
    ):
        if (isinstance(total_dev_timeout_seconds, bool)
                or not isinstance(total_dev_timeout_seconds, int)
                or not 1 <= total_dev_timeout_seconds <= 300):
            raise ValueError("CARPS public DEV panel timeout must be from 1 to 300 seconds")
        self._active_public_task_root = active_public_task_root
        self.total_dev_timeout_seconds = total_dev_timeout_seconds
        super().__init__(
            task_id=TASK_ID,
            task_version=TASK_VERSION,
            candidate_path=CANDIDATE_PATH,
            dev_image=public_dev_image,
            dev_image_id=public_dev_image_id,
            dev_cell_count=8,
            evaluate_dev=self._evaluate_public_dev,
            **kwargs,
        )

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("CARPS aggregate scope has a different task identity")
        if dev_cell_count != 8:
            raise ValueError("CARPS public DEV scope must contain eight example clouds")
        if tier == "dev":
            value = {
                "schema": "autoresearchexam-public-carps-aggregate-v1",
                "task_id": TASK_ID,
                "task_version": TASK_VERSION,
                "tier": "dev",
                "candidate_path": CANDIDATE_PATH,
                "public_output_path": PUBLIC_OUTPUT_PATH,
                "metric_id": METRIC_ID,
                "metric_field": METRIC_FIELD,
                "metric_direction": METRIC_DIRECTION,
                "metric_source": DEV_METRIC_SOURCE,
                "metric_transform": "dev_metric = -mean(exact_linf_star_discrepancy)",
                "public_panel_sha256": PUBLIC_PANEL_DIGEST,
                "public_examples_manifest_sha256": EXAMPLES_MANIFEST_SHA256,
                "public_example_count": 8,
                "public_dev_image_id": dev_image_id,
                "per_select_time_budget_seconds": 10,
                "per_select_process_wall_cap_seconds": 15,
                "validation_score_is_not_used": True,
            }
        elif tier == "validation":
            value = {
                "schema": "autoresearchexam-public-carps-aggregate-v1",
                "task_id": TASK_ID,
                "task_version": TASK_VERSION,
                "tier": "validation",
                "candidate_path": CANDIDATE_PATH,
                "public_output_path": PUBLIC_OUTPUT_PATH,
                "metric_id": METRIC_ID,
                "metric_field": METRIC_FIELD,
                "metric_direction": METRIC_DIRECTION,
                "metric_source": VALIDATION_METRIC_SOURCE,
                "validation_result_field": "validation_score",
                "validation_semantics": VALIDATION_METRIC_SEMANTICS,
                "public_mcp_endpoint": PUBLIC_MCP_ENDPOINT,
                "public_mcp_contract_sha256": contract_sha256,
            }
        else:
            raise ValueError("CARPS public backend supports only DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _evaluate_public_dev(self, candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
        if self._active_public_task_root is None:
            raise PublicCARPSEvaluatorError("CARPS public evaluator bundle assets are not mounted")
        budget = min(self.total_dev_timeout_seconds, max(1, min(timeout_seconds, 300)))
        response = evaluate_carps_public_dev(
            candidate,
            public_task_root=self._active_public_task_root,
            image=self.dev_image,
            expected_image_id=self.dev_image_id,
            total_timeout_seconds=budget,
        )
        if response.get("status") != "GRADED" or response.get("panel_count") != 8:
            raise PublicCARPSEvaluatorError("CARPS public example panel did not produce a complete score")
        score = response.get("dev_metric")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise PublicCARPSEvaluatorError("CARPS public panel score is invalid")
        return {"status": "GRADED", "dev_score": -float(score), "dev_cells": 8}

    @staticmethod
    def _public_score(public_result: Mapping[str, Any]) -> float:
        score = AREPublicEvaluatorRunner._public_score(public_result)
        if not math.isfinite(score):
            raise TrustedEvaluatorError("CARPS public task score is not finite")
        return score

    def _validate_aggregate_case(
        self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes], *, tier: str,
    ) -> None:
        if tier not in {"dev", "validation"}:
            raise TrustedEvaluatorError("CARPS public backend forbids HOLDOUT")
        super()._validate_aggregate_case(cases, case_inputs, tier=tier)

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if (protocol.metric_id != METRIC_ID or protocol.metric_field != METRIC_FIELD
                or protocol.direction != METRIC_DIRECTION
                or protocol.execution_backend_id != self.backend_id):
            raise TrustedEvaluatorError("CARPS protocol metric or backend identity differs from its pin")
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("CARPS public backend bundle digest differs from approved protocol")
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or set(names) != set(_BACKEND_FILES):
                    raise ValueError
                if any(archive.getinfo(name).file_size > 2_000_000 for name in names):
                    raise ValueError
                manifest = json.loads(archive.read("are-public-carps-backend.json"))
                task_toml = archive.read("task.toml")
                instruction = archive.read("task-instruction.md")
                task_readme = archive.read("task-README.md")
                examples_manifest = archive.read("examples-manifest.json")
                contract_bytes = archive.read("public-mcp-contract.json")
                cloud_digests = {
                    name.removeprefix("examples/"): hashlib.sha256(archive.read(name)).hexdigest()
                    for name in names if name.startswith("examples/")
                }
                runtime_sources = {name: archive.read(name) for name in _RUNTIME_SOURCES}
                contract = json.loads(contract_bytes)
                from .public_mcp_contract import validate_public_mcp_contract
                validate_public_mcp_contract(
                    contract, task_id=TASK_ID, task_version=TASK_VERSION,
                    task_sha256=TASK_TOML_SHA256,
                    task_instruction_sha256=INSTRUCTION_SHA256,
                )
                expected = {
                    "schema_version": 1,
                    "backend_id": self.backend_id,
                    "task_id": TASK_ID,
                    "task_version": TASK_VERSION,
                    "task_toml_sha256": TASK_TOML_SHA256,
                    "task_instruction_sha256": INSTRUCTION_SHA256,
                    "task_readme_sha256": hashlib.sha256(task_readme).hexdigest(),
                    "public_examples_manifest_sha256": EXAMPLES_MANIFEST_SHA256,
                    "public_example_cloud_sha256": EXAMPLE_CLOUD_SHA256,
                    "public_panel_sha256": PUBLIC_PANEL_DIGEST,
                    "mcp_endpoint": PUBLIC_MCP_ENDPOINT,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_contract_source_sha256": hashlib.sha256(contract_bytes).hexdigest(),
                    "candidate_path": CANDIDATE_PATH,
                    "public_output_path": PUBLIC_OUTPUT_PATH,
                    "metric_id": METRIC_ID,
                    "metric_field": METRIC_FIELD,
                    "metric_direction": METRIC_DIRECTION,
                    "dev_metric_source": DEV_METRIC_SOURCE,
                    "dev_metric_transform": "negative mean exact L-infinity star discrepancy",
                    "validation_metric_source": VALIDATION_METRIC_SOURCE,
                    "validation_result_field": "validation_score",
                    "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS,
                    "public_dev_image": self.dev_image,
                    "public_dev_image_id": self.dev_image_id,
                    "public_dev_example_count": 8,
                    "per_select_time_budget_seconds": 10,
                    "per_select_process_wall_cap_seconds": 15,
                    "runtime_source_sha256": {
                        name: hashlib.sha256(raw).hexdigest()
                        for name, raw in runtime_sources.items()
                    },
                    "task_assets_sha256": {
                        "task.toml": hashlib.sha256(task_toml).hexdigest(),
                        "instruction.md": hashlib.sha256(instruction).hexdigest(),
                        "examples/manifest.json": hashlib.sha256(examples_manifest).hexdigest(),
                        **{f"examples/{name}": digest for name, digest in cloud_digests.items()},
                    },
                    "dev_execution": {
                        "container_network": "none",
                        "root_filesystem_read_only": True,
                        "mounted_candidate_path": PUBLIC_OUTPUT_PATH,
                        "mounted_public_data": "one pinned example cloud per process",
                    },
                    "aggregate_case_ids": {
                        "dev": self.dev_aggregate_case_id,
                        "validation": self.validation_aggregate_case_id,
                    },
                }
                if (manifest != expected or contract.get("contract_sha256") != self.contract_sha256
                        or contract.get("public_endpoint") != PUBLIC_MCP_ENDPOINT):
                    raise ValueError
                for name, runtime_source in _RUNTIME_SOURCES.items():
                    if runtime_sources[name] != runtime_source.read_bytes():
                        raise ValueError
        except (OSError, ValueError, KeyError, UnicodeDecodeError,
                json.JSONDecodeError, zipfile.BadZipFile):
            raise TrustedEvaluatorError("CARPS public backend bundle is missing or malformed") from None
        if self.grader.contract_sha256 != self.contract_sha256:
            raise TrustedEvaluatorError("CARPS public grader contract differs from the protocol pin")

    def run(self, **kwargs: Any) -> EvaluatorExecutionV1:
        """Extract only the allowlisted public task assets for this evaluation."""
        bundle = kwargs.get("evaluator_bundle")
        if not isinstance(bundle, bytes):
            raise TrustedEvaluatorError("CARPS evaluator bundle is missing")
        self._verify_backend_bundle(kwargs["protocol"], bundle)
        try:
            with tempfile.TemporaryDirectory(prefix="bananloop-carps-fixture-") as temp_name:
                root = Path(temp_name)
                with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                    self._write_bundle_asset(root / "task.toml", archive.read("task.toml"))
                    self._write_bundle_asset(root / "instruction.md", archive.read("task-instruction.md"))
                    self._write_bundle_asset(
                        root / "environment/app/data/examples/manifest.json",
                        archive.read("examples-manifest.json"),
                    )
                    for cloud_name in EXAMPLE_CLOUD_SHA256:
                        self._write_bundle_asset(
                            root / "environment/app/data/examples" / cloud_name,
                            archive.read(f"examples/{cloud_name}"),
                        )
                self._active_public_task_root = root
                try:
                    return super().run(**kwargs)
                finally:
                    self._active_public_task_root = None
        except (OSError, KeyError, zipfile.BadZipFile):
            raise TrustedEvaluatorError("CARPS public task assets could not be staged") from None

    @staticmethod
    def _write_bundle_asset(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o444)

    @staticmethod
    def _execution_from_score(
        *, protocol: EvaluatorProtocolV1, approval_digest: str,
        campaign_id: str, spec_hash: str, candidate_snapshot_id: str,
        tier_manifest: bytes, tier: str, split: str, score: float,
    ) -> EvaluatorExecutionV1:
        execution = AREPublicEvaluatorRunner._execution_from_score(
            protocol=protocol,
            approval_digest=approval_digest,
            campaign_id=campaign_id,
            spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest,
            tier=tier,
            split=split,
            score=score,
            metric_unit="directional_task_score",
        )
        result = json.loads(execution.private_result_bytes)
        result.setdefault("diagnostics", {})["metric_source"] = (
            DEV_METRIC_SOURCE if tier == "dev" else VALIDATION_METRIC_SOURCE
        )
        result["diagnostics"]["metric_field"] = METRIC_FIELD
        result["diagnostics"]["metric_direction"] = METRIC_DIRECTION
        raw = json.dumps(
            result, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        return execution.model_copy(update={
            "private_result_bytes": raw,
            "result_digest": hashlib.sha256(raw).hexdigest(),
        })
