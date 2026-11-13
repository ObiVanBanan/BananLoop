"""Task-specific trusted evaluator backend for public dctabeval v1.1.0.

DEV scores the pinned six-draw public panel with mean ROC-AUC. VALIDATION is
submitted through the existing at-most-once :class:`PublicAREGrader`. No
HOLDOUT path exists, and this module never reads private grader material.
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

from . import public_dctabeval_dev
from .optimization_runner import AREPublicEvaluatorRunner
from .public_dctabeval_dev import (
    PANEL_COUNT,
    PANEL_DRAWS,
    PublicDCTABEvalError,
    public_dctabeval_pins,
    run_public_dctabeval_dev,
)
from .public_mcp_contract import validate_public_mcp_contract


TASK_ID = public_dctabeval_dev.TASK_ID
TASK_VERSION = public_dctabeval_dev.TASK_VERSION
CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/dctabeval/solution.py"
PUBLIC_OUTPUT_PATH = "/app/output/solution.py"
BACKEND_MANIFEST = "are-public-dctabeval-backend.json"
BACKEND_ID = "autoresearchexam-dctabeval-public-v1"
DEV_CASE_ID = "public-dctabeval-dev-aggregate"
VALIDATION_CASE_ID = "public-dctabeval-validation-aggregate"
METRIC_FIELD = "roc_auc"
METRIC_DIRECTION = "MAXIMIZE"
METRIC_AGGREGATION = "MEAN"


class AREPublicDCTABEvalEvaluatorRunner(AREPublicEvaluatorRunner):
    """Bind dctabeval output, score semantics, public DEV and public VALIDATION."""

    backend_id = BACKEND_ID
    default_dev_image = "bananloop-are-dctabeval-public-dev:2026-11-13"
    default_dev_image_id = "sha256:7c4cefdf35f1455fb2817780d50360401d257709ec8f72f392e67c069e977e68"
    default_dev_cell_count = PANEL_COUNT
    dev_aggregate_case_id = DEV_CASE_ID
    validation_aggregate_case_id = VALIDATION_CASE_ID
    validation_score_bounds = (0.0, 1.0)

    def __init__(self, *, source_task_root: Path, dev_image: str,
                 dev_image_id: str, **kwargs: Any):
        root = source_task_root.expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("dctabeval source task root is missing or unsafe")
        self.source_task_root = root.resolve()
        if (not isinstance(dev_image, str) or not dev_image or dev_image.startswith("-")
                or any(char.isspace() for char in dev_image)
                or not isinstance(dev_image_id, str)
                or not public_dctabeval_dev.IMAGE_ID_RE.fullmatch(dev_image_id)):
            raise ValueError("dctabeval public DEV image pin is malformed")
        try:
            self.input_pins = public_dctabeval_pins(self.source_task_root)
        except PublicDCTABEvalError:
            raise ValueError("dctabeval public task inputs do not match their frozen pins") from None

        def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = run_public_dctabeval_dev(
                candidate,
                task_root=self.source_task_root,
                image=dev_image,
                expected_image_id=dev_image_id,
                timeout_seconds=min(1200, timeout_seconds),
            )
            return {
                "status": result["status"],
                "dev_score": result["metric"],
                "dev_cells": result["panel_count"],
            }

        super().__init__(
            task_id=TASK_ID,
            task_version=TASK_VERSION,
            candidate_path=CANDIDATE_PATH,
            dev_image=dev_image,
            dev_image_id=dev_image_id,
            dev_cell_count=PANEL_COUNT,
            evaluate_dev=evaluate_dev,
            **kwargs,
        )

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        if task_id != TASK_ID or task_version != TASK_VERSION:
            raise ValueError("dctabeval aggregate scope has the wrong task identity")
        base = {
            "schema": "autoresearchexam-public-dctabeval-aggregate-v1",
            "task_id": TASK_ID,
            "task_version": TASK_VERSION,
            "tier": tier,
            "candidate_path": CANDIDATE_PATH,
            "public_output_path": PUBLIC_OUTPUT_PATH,
            "metric_field": METRIC_FIELD,
            "metric_direction": METRIC_DIRECTION,
            "metric_aggregation": METRIC_AGGREGATION,
            "metric": "mean ROC-AUC",
            "task_selection_sha256": public_dctabeval_dev.EXPECTED_SELECTION_SHA256,
            "task_sha256": public_dctabeval_dev.EXPECTED_TASK_SHA256,
            "instruction_sha256": public_dctabeval_dev.EXPECTED_INSTRUCTION_SHA256,
            "local_panel_sha256": public_dctabeval_dev.EXPECTED_LOCAL_PANEL_SHA256,
            "dev_csv_sha256": public_dctabeval_dev.EXPECTED_DEV_CSV_SHA256,
        }
        if tier == "dev":
            if dev_cell_count != PANEL_COUNT:
                raise ValueError("dctabeval DEV scope must use its full six-draw panel")
            base.update({
                "measurement": "unweighted mean ROC-AUC over pinned six-of-twelve local pilot draws",
                "pilot_only": True,
                "official_result_comparable": False,
                "draws": list(PANEL_DRAWS),
                "panel_count": PANEL_COUNT,
                "eval_rows_per_draw": 800,
                "public_dev_image": cls.default_dev_image,
                "public_dev_image_id": dev_image_id,
                "execution": {
                    "container_network": "none",
                    "root_filesystem_read_only": True,
                    "per_draw_timeout_seconds": 100,
                    "mounted_inputs": ["local_panel.py", "data/dev.csv"],
                },
            })
        elif tier == "validation":
            base.update({
                "measurement": (
                    "public ARE validation reward, retained separately from this bounded six-of-twelve, "
                    "one-hour pilot and not claimed comparable to the official 24-hour benchmark"
                ),
                "pilot_only": True,
                "official_result_comparable": False,
                "mcp_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
                "validation_bridge": {
                    "executor": "BananLoop trusted host-side AREPublicEvaluatorRunner",
                    "sandbox_network_access": False,
                    "allowed_public_mcp_tools": ["submit", "result", "status"],
                    "submitted_material": "candidate file only",
                    "private_verifier_or_holdout_access": False,
                },
            })
        else:
            raise ValueError("dctabeval public backend supports only DEV and VALIDATION")
        return json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("dctabeval evaluator bundle digest differs from approved protocol")
        expected_names = {
            BACKEND_MANIFEST,
            "dctabeval_runner.py",
            "public_dctabeval_dev.py",
            "public_devkit.py",
            "optimization_runner.py",
            "public_grader.py",
            "are_public_mcp_client.py",
            "public_mcp_contract.py",
            "public-mcp-contract.json",
            "task-selection.json",
            "task.toml",
            "task-instruction.md",
            "local_panel.py",
            "task-Dockerfile",
            "task-requirements.txt",
        }
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if (set(archive.namelist()) != expected_names
                        or len(archive.infolist()) != len(expected_names)):
                    raise ValueError
                contract_bytes = archive.read("public-mcp-contract.json")
                contract = validate_public_mcp_contract(
                    json.loads(contract_bytes), task_id=TASK_ID,
                    task_version=TASK_VERSION,
                    task_sha256=public_dctabeval_dev.EXPECTED_TASK_SHA256,
                    task_instruction_sha256=public_dctabeval_dev.EXPECTED_INSTRUCTION_SHA256,
                )
                runtime_sources = {
                    "dctabeval_runner.py": Path(__file__).read_bytes(),
                    "public_dctabeval_dev.py": Path(public_dctabeval_dev.__file__).read_bytes(),
                    "public_devkit.py": Path(__file__).with_name("public_devkit.py").read_bytes(),
                    "optimization_runner.py": Path(__file__).with_name("optimization_runner.py").read_bytes(),
                    "public_grader.py": Path(__file__).with_name("public_grader.py").read_bytes(),
                    "are_public_mcp_client.py": Path(__file__).with_name("are_public_mcp_client.py").read_bytes(),
                    "public_mcp_contract.py": Path(__file__).with_name("public_mcp_contract.py").read_bytes(),
                }
                for name, content in runtime_sources.items():
                    if archive.read(name) != content:
                        raise ValueError
                if hashlib.sha256(archive.read("task-selection.json")).hexdigest() != \
                        public_dctabeval_dev.EXPECTED_SELECTION_SHA256:
                    raise ValueError
                manifest = json.loads(archive.read(BACKEND_MANIFEST))
                if not isinstance(manifest, dict):
                    raise ValueError
                task_material = {
                    "task_sha256": hashlib.sha256(archive.read("task.toml")).hexdigest(),
                    "instruction_sha256": hashlib.sha256(archive.read("task-instruction.md")).hexdigest(),
                    "local_panel_sha256": hashlib.sha256(archive.read("local_panel.py")).hexdigest(),
                    "dockerfile_sha256": hashlib.sha256(archive.read("task-Dockerfile")).hexdigest(),
                    "requirements_sha256": hashlib.sha256(archive.read("task-requirements.txt")).hexdigest(),
                }
                expected_manifest = {
                    "schema_version": 1,
                    "backend_id": self.backend_id,
                    "task_id": TASK_ID,
                    "task_version": TASK_VERSION,
                    "candidate_path": CANDIDATE_PATH,
                    "public_output_path": PUBLIC_OUTPUT_PATH,
                    "metric_field": METRIC_FIELD,
                    "metric_direction": METRIC_DIRECTION,
                    "metric_aggregation": METRIC_AGGREGATION,
                    "task_selection_sha256": public_dctabeval_dev.EXPECTED_SELECTION_SHA256,
                    "task_sha256": public_dctabeval_dev.EXPECTED_TASK_SHA256,
                    "instruction_sha256": public_dctabeval_dev.EXPECTED_INSTRUCTION_SHA256,
                    "local_panel_sha256": public_dctabeval_dev.EXPECTED_LOCAL_PANEL_SHA256,
                    "dev_csv_sha256": public_dctabeval_dev.EXPECTED_DEV_CSV_SHA256,
                    "task_material_sha256": task_material,
                    "mcp_contract_sha256": self.contract_sha256,
                    "mcp_contract_source_sha256": hashlib.sha256(contract_bytes).hexdigest(),
                    "mcp_endpoint": "http://grader:8000/mcp",
                    "public_dev_image": self.dev_image,
                    "public_dev_image_id": self.dev_image_id,
                    "public_dev_panel": {
                        "draws": list(PANEL_DRAWS),
                        "panel_count": PANEL_COUNT,
                        "eval_rows_per_draw": 800,
                    },
                    "dev_execution": {
                        "container_network": "none",
                        "root_filesystem_read_only": True,
                        "mounted_candidate_path": PUBLIC_OUTPUT_PATH,
                        "mounted_public_inputs": ["local_panel.py", "data/dev.csv"],
                        "per_draw_timeout_seconds": 100,
                    },
                    "aggregate_case_ids": {"dev": DEV_CASE_ID, "validation": VALIDATION_CASE_ID},
                }
                if contract.get("contract_sha256") != self.contract_sha256:
                    raise ValueError
                if manifest != expected_manifest:
                    raise ValueError
                pins = public_dctabeval_pins(self.source_task_root)
                if pins["dev_csv_sha256"] != public_dctabeval_dev.EXPECTED_DEV_CSV_SHA256:
                    raise ValueError
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError,
                zipfile.BadZipFile, KeyError, PublicDCTABEvalError):
            raise TrustedEvaluatorError("dctabeval public backend bundle is missing or malformed") from None
        if self.grader.contract_sha256 != self.contract_sha256:
            raise TrustedEvaluatorError("dctabeval public grader contract differs from the protocol pin")

    def _validate_aggregate_case(
        self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes], *, tier: str,
    ) -> None:
        if tier not in {"dev", "validation"}:
            raise TrustedEvaluatorError("dctabeval public backend does not support HOLDOUT")
        expected_id = DEV_CASE_ID if tier == "dev" else VALIDATION_CASE_ID
        if len(cases) != 1 or cases[0].case_id != expected_id or cases[0].repetition != 1:
            raise TrustedEvaluatorError(f"dctabeval {tier.upper()} must declare one aggregate case")
        identity = (expected_id, 1)
        if set(case_inputs) != {identity}:
            raise TrustedEvaluatorError(f"dctabeval {tier.upper()} inputs do not match its aggregate case")
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
            raise TrustedEvaluatorError(f"dctabeval {tier.upper()} scope differs from its approved input")

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
            metric_unit="ROC-AUC",
        )
