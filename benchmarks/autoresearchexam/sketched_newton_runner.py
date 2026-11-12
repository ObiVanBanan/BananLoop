"""Trusted public DEV + ARE VALIDATION adapter for sketched Newton v1.1.0.

DEV is one frozen public 300,000-step development stream and is explicitly a
pilot diagnostic. VALIDATION uses the task's public ARE endpoint and the
existing at-most-once submission ledger. No HOLDOUT or private grader input is
available to this backend.
"""
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

from . import public_sketched_newton_dev as newton_dev
from .optimization_runner import AREPublicEvaluatorRunner
from .public_mcp_contract import validate_public_mcp_contract


TASK_ID = newton_dev.TASK_ID
TASK_VERSION = newton_dev.TASK_VERSION
CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/sketched_newton/estimator.py"
PUBLIC_OUTPUT_PATH = "/app/output/estimator.py"
BACKEND_MANIFEST = "are-public-sketched-newton-backend.json"
BACKEND_ID = "autoresearchexam-sketched-newton-public-v1"
DEV_CASE_ID = "public-sketched-newton-dev-aggregate"
VALIDATION_CASE_ID = "public-sketched-newton-validation-aggregate"
METRIC_FIELD = "task_score"
METRIC_DIRECTION = "MAXIMIZE"
METRIC_AGGREGATION = "MEAN"
VALIDATION_METRIC_SEMANTICS = "public_are.validation_score=higher_is_better_task_reward.v1"

_RUNTIME_FILES = {
    "sketched_newton_runner.py": Path(__file__),
    "public_sketched_newton_dev.py": Path(newton_dev.__file__),
    "public_devkit.py": Path(__file__).with_name("public_devkit.py"),
    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py"),
    "public_grader.py": Path(__file__).with_name("public_grader.py"),
    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py"),
    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py"),
}
_BUNDLE_NAMES = {
    BACKEND_MANIFEST, "public-mcp-contract.json", "task-selection.json",
    "task.toml", "task-instruction.md", "task-Dockerfile",
    "task-dockerignore", "task-requirements.txt", "public-runner.py",
    "dev-instance-0.json", "dev-xi-0.npy", *_RUNTIME_FILES,
}


class AREPublicSketchedNewtonEvaluatorRunner(AREPublicEvaluatorRunner):
    backend_id = BACKEND_ID
    default_dev_image = newton_dev.DEFAULT_IMAGE
    default_dev_image_id = newton_dev.DEFAULT_IMAGE_ID
    default_dev_cell_count = 1
    dev_aggregate_case_id = DEV_CASE_ID
    validation_aggregate_case_id = VALIDATION_CASE_ID
    candidate_path = CANDIDATE_PATH
    public_output_path = PUBLIC_OUTPUT_PATH

    def __init__(self, *, source_task_root: Path, dev_image: str,
                 dev_image_id: str, **kwargs: Any):
        root = source_task_root.expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("sketched-Newton public task root is missing or unsafe")
        self.source_task_root = root.resolve()
        if (dev_image != self.default_dev_image
                or dev_image_id != self.default_dev_image_id):
            raise ValueError("sketched-Newton DEV image does not match the frozen local image pin")
        try:
            self.input_pins = newton_dev.verify_public_newton_inputs(self.source_task_root)
        except newton_dev.PublicNewtonDevError:
            raise ValueError("sketched-Newton public DEV inputs do not match their frozen pins") from None

        def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = newton_dev.run_public_newton_dev(
                candidate, task_root=self.source_task_root,
                image=dev_image, expected_image_id=dev_image_id,
                timeout_seconds=min(1200, timeout_seconds),
            )
            return {"status": result["status"], "dev_score": result["metric"],
                    "dev_cells": result["panel_count"]}

        for key in ("task_id", "task_version", "candidate_path", "dev_image",
                    "dev_image_id", "dev_cell_count", "evaluate_dev"):
            kwargs.pop(key, None)
        super().__init__(
            task_id=TASK_ID, task_version=TASK_VERSION,
            candidate_path=CANDIDATE_PATH, dev_image=dev_image,
            dev_image_id=dev_image_id, dev_cell_count=1,
            evaluate_dev=evaluate_dev, **kwargs,
        )

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("sketched-Newton aggregate scope has the wrong task identity")
        common = {
            "schema": "autoresearchexam-public-sketched-newton-aggregate-v1",
            "task_id": TASK_ID, "task_version": TASK_VERSION, "tier": tier,
            "candidate_path": CANDIDATE_PATH, "public_output_path": PUBLIC_OUTPUT_PATH,
                "metric_field": METRIC_FIELD, "metric_direction": METRIC_DIRECTION,
                "metric_transform": "task_score = -absolute_relative_variance_error",
            "metric_aggregation": METRIC_AGGREGATION,
            "task_selection_sha256": newton_dev.SELECTION_SHA256,
            "task_sha256": newton_dev.TASK_SHA256,
            "instruction_sha256": newton_dev.INSTRUCTION_SHA256,
        }
        if tier == "dev":
            if dev_cell_count != 1 or dev_image_id != newton_dev.DEFAULT_IMAGE_ID:
                raise ValueError("sketched-Newton DEV scope must pin the one fixed public stream")
            common.update({
                "measurement": "negative absolute relative error on public dev0, seed_base and seed_base+1",
                "dev_instance_sha256": newton_dev.DEV0_JSON_SHA256,
                "dev_target_sha256": newton_dev.DEV0_XI_SHA256,
                "public_runner_sha256": newton_dev.RUNNER_SHA256,
                "steps": 300_000, "replications": 1,
                "public_dev_image": cls.default_dev_image,
                "public_dev_image_id": dev_image_id,
                "execution": {"container_network": "none", "root_filesystem_read_only": True,
                              "mounted_candidate": PUBLIC_OUTPUT_PATH},
            })
        elif tier == "validation":
            common.update({
                "measurement": "aggregate task reward from public ARE VALIDATION",
                "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS,
                "mcp_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
            })
        else:
            raise ValueError("sketched-Newton backend supports only DEV and VALIDATION")
        return json.dumps(common, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("sketched-Newton bundle digest differs from the approved protocol")
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if set(archive.namelist()) != _BUNDLE_NAMES or len(archive.infolist()) != len(_BUNDLE_NAMES):
                    raise ValueError
                contract_bytes = archive.read("public-mcp-contract.json")
                contract = validate_public_mcp_contract(
                    json.loads(contract_bytes), task_id=TASK_ID, task_version=TASK_VERSION,
                    task_sha256=newton_dev.TASK_SHA256,
                    task_instruction_sha256=newton_dev.INSTRUCTION_SHA256,
                )
                for name, path in _RUNTIME_FILES.items():
                    if archive.read(name) != path.read_bytes():
                        raise ValueError
                if hashlib.sha256(archive.read("task-selection.json")).hexdigest() != newton_dev.SELECTION_SHA256:
                    raise ValueError
                public_digests = {
                    "task.toml": newton_dev.TASK_SHA256,
                    "task-instruction.md": newton_dev.INSTRUCTION_SHA256,
                    "public-runner.py": newton_dev.RUNNER_SHA256,
                    "dev-instance-0.json": newton_dev.DEV0_JSON_SHA256,
                    "dev-xi-0.npy": newton_dev.DEV0_XI_SHA256,
                }
                for name, digest in public_digests.items():
                    if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                        raise ValueError
                manifest = json.loads(archive.read(BACKEND_MANIFEST))
                expected = {
                    "schema_version": 1, "backend_id": BACKEND_ID,
                    "task_id": TASK_ID, "task_version": TASK_VERSION,
                    "candidate_path": CANDIDATE_PATH,
                    "public_output_path": PUBLIC_OUTPUT_PATH,
                    "metric_field": METRIC_FIELD, "metric_direction": METRIC_DIRECTION,
                    "metric_aggregation": METRIC_AGGREGATION,
                    "metric_transform": "task_score = -absolute_relative_variance_error",
                    "task_selection_sha256": newton_dev.SELECTION_SHA256,
                    "task_sha256": newton_dev.TASK_SHA256,
                    "instruction_sha256": newton_dev.INSTRUCTION_SHA256,
                    "public_runner_sha256": newton_dev.RUNNER_SHA256,
                    "dev_instance_sha256": newton_dev.DEV0_JSON_SHA256,
                    "dev_target_sha256": newton_dev.DEV0_XI_SHA256,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_contract_source_sha256": hashlib.sha256(contract_bytes).hexdigest(),
                    "mcp_endpoint": "http://grader:8000/mcp",
                    "public_dev_image": self.dev_image,
                    "public_dev_image_id": self.dev_image_id,
                    "public_dev_scope": {"instance": "dev0", "steps": 300_000, "replications": 1},
                    "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS,
                    "aggregate_case_ids": {"dev": DEV_CASE_ID, "validation": VALIDATION_CASE_ID},
                }
                if manifest != expected or contract["contract_sha256"] != self.contract_sha256:
                    raise ValueError
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
                zipfile.BadZipFile, newton_dev.PublicNewtonDevError):
            raise TrustedEvaluatorError("sketched-Newton public backend bundle is missing or malformed") from None
        if self.grader.contract_sha256 != self.contract_sha256:
            raise TrustedEvaluatorError("sketched-Newton grader contract differs from the approved protocol")

    def _validate_aggregate_case(self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes], *, tier: str) -> None:
        if tier not in {"dev", "validation"}:
            raise TrustedEvaluatorError("sketched-Newton backend does not support HOLDOUT")
        case_id = DEV_CASE_ID if tier == "dev" else VALIDATION_CASE_ID
        if len(cases) != 1 or cases[0].case_id != case_id or cases[0].repetition != 1:
            raise TrustedEvaluatorError("sketched-Newton tier must have one aggregate case")
        identity = (case_id, 1)
        if set(case_inputs) != {identity}:
            raise TrustedEvaluatorError("sketched-Newton case input differs from its aggregate scope")
        expected = self.aggregate_case_input(
            tier=tier, task_id=self.task_id, task_version=self.task_version,
            contract_sha256=self.contract_sha256, dev_image_id=self.dev_image_id,
            dev_cell_count=self.dev_cell_count,
        )
        payload = case_inputs[identity]
        if payload != expected or hashlib.sha256(payload).hexdigest() != cases[0].input_sha256:
            raise TrustedEvaluatorError("sketched-Newton tier is not bound to the frozen scope")

    @staticmethod
    def _execution_from_score(
        *, protocol: EvaluatorProtocolV1, approval_digest: str, campaign_id: str,
        spec_hash: str, candidate_snapshot_id: str, tier_manifest: bytes,
        tier: str, split: str, score: float,
    ) -> EvaluatorExecutionV1:
        return AREPublicEvaluatorRunner._execution_from_score(
            protocol=protocol, approval_digest=approval_digest,
            campaign_id=campaign_id, spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest, tier=tier, split=split,
            score=score, metric_unit="task_score",
        )
