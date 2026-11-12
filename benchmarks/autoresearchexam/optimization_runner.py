"""Public AutoResearchExam runner for the existing trusted-evaluator seam.

DEV runs the public task devkit in a network-isolated Docker container;
VALIDATION submits only the candidate output to the task's public grader.
HOLDOUT is forbidden for this benchmark backend.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from bananloop.contracts import CaseMeasurement, EvaluationResult, MetricValue
from bananloop.evaluator_verification import EvaluatorProtocolV1
from bananloop.trusted_evaluator import (
    EvaluatorExecutionV1, TrustedEvaluatorError, _case_protocol_digest,
    _manifest_from_bytes,
)

from .public_grader import PublicAREGrader, PublicGraderError


class AREPublicEvaluatorRunner:
    """Dispatch public DEV in a networkless container and VALIDATION through MCP."""

    backend_id = "autoresearchexam-public-v2"
    default_dev_image = "bananloop-are-public-devkit-probe:2026-11-12"
    default_dev_image_id = "sha256:5d2bac54769dc124be218efc1a1495210197a25652f6005ad327d9fa5bc8f9f4"
    default_dev_cell_count = 42
    dev_aggregate_case_id = "public-dev-aggregate"
    validation_aggregate_case_id = "public-validation-aggregate"

    @classmethod
    def aggregate_case_input(
        cls, *, tier: str, task_id: str, task_version: str,
        contract_sha256: str, dev_image_id: str, dev_cell_count: int,
    ) -> bytes:
        """Canonical one-case scope for a tier returning an aggregate score."""
        if tier == "dev":
            value = {
                "schema": "autoresearchexam-public-aggregate-v1",
                "task_id": task_id,
                "task_version": task_version,
                "tier": tier,
                "metric_field": "imputation_r2",
                "measurement": "unweighted mean R2 over the public DEV panel",
                "dev_image_id": dev_image_id,
                "dev_cell_count": dev_cell_count,
            }
        elif tier == "validation":
            value = {
                "schema": "autoresearchexam-public-aggregate-v1",
                "task_id": task_id,
                "task_version": task_version,
                "tier": tier,
                "metric_field": "imputation_r2",
                "measurement": "one aggregate score returned by the public ARE validation endpoint",
                "mcp_endpoint": "http://grader:8000/mcp",
                "mcp_contract_sha256": contract_sha256,
            }
        else:
            raise ValueError("aggregate case scope only supports DEV and VALIDATION")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def __init__(
        self,
        *,
        grader: PublicAREGrader,
        publish_candidate: Callable[[bytes], None],
        task_id: str,
        task_version: str,
        contract_sha256: str,
        candidate_path: str,
        dev_image: str = default_dev_image,
        dev_image_id: str = default_dev_image_id,
        dev_cell_count: int = default_dev_cell_count,
        evaluate_dev: Callable[[bytes, int], Mapping[str, Any]] | None = None,
        deadline_epoch: float | None = None,
        poll_interval_seconds: float = 2.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        path = PurePosixPath(candidate_path)
        if (path.is_absolute() or not path.parts or path.as_posix() != candidate_path
                or any(part in {"", ".", ".."} for part in path.parts)):
            raise ValueError("candidate_path must be a canonical repository-relative path")
        if not callable(publish_candidate):
            raise TypeError("publish_candidate must be callable")
        persisted_deadline = grader.deadline_epoch
        if deadline_epoch is None:
            deadline_epoch = persisted_deadline
        if (isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float))
                or not math.isfinite(deadline_epoch) or deadline_epoch <= 0
                or float(deadline_epoch) != persisted_deadline):
            raise ValueError("deadline_epoch must match the ledger's persisted task deadline")
        if (isinstance(poll_interval_seconds, bool)
                or not isinstance(poll_interval_seconds, (int, float))
                or not math.isfinite(poll_interval_seconds)
                or not 0.05 <= poll_interval_seconds <= 30):
            raise ValueError("poll_interval_seconds must be between 0.05 and 30")
        self.grader = grader
        self.publish_candidate = publish_candidate
        self.task_id = task_id
        self.task_version = task_version
        self.contract_sha256 = contract_sha256
        self.candidate_path = path.as_posix()
        self.dev_image = dev_image
        self.dev_image_id = dev_image_id
        self.dev_cell_count = dev_cell_count
        self.evaluate_dev = evaluate_dev
        self.deadline_epoch = float(deadline_epoch)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.clock = clock
        self.sleep = sleep

    def run(
        self,
        *,
        protocol: EvaluatorProtocolV1,
        approval_digest: str,
        campaign_id: str,
        spec_hash: str,
        candidate_snapshot_id: str,
        candidate_files: Mapping[str, bytes],
        evaluator_bundle: bytes,
        tier: str,
        tier_manifest: bytes,
        case_inputs: Mapping[tuple[str, int], bytes],
    ) -> EvaluatorExecutionV1:
        if protocol.execution_backend_id != self.backend_id:
            raise TrustedEvaluatorError("public ARE evaluator backend is not pinned by the approved protocol")
        self._verify_backend_bundle(protocol, evaluator_bundle)
        if tier == "holdout":
            raise TrustedEvaluatorError("AutoResearchExam public grader has no HOLDOUT tier")
        if tier == "dev":
            if self.evaluate_dev is None:
                raise TrustedEvaluatorError("public ARE DEV evaluator is not configured")
            candidate = candidate_files.get(self.candidate_path)
            if not isinstance(candidate, bytes) or not candidate:
                raise TrustedEvaluatorError("candidate snapshot lacks the approved imputer output file")
            if self.clock() >= self.deadline_epoch:
                raise PublicGraderError("campaign deadline expired before public DEV evaluation")
            remaining = int(self.deadline_epoch - self.clock())
            if remaining <= 25:
                raise PublicGraderError("campaign deadline is too close for safe public DEV cleanup")
            dev_ref = next((item for item in protocol.tiers if item.tier == "dev"), None)
            if dev_ref is None:
                raise TrustedEvaluatorError("approved evaluator protocol lacks public DEV")
            manifest = _manifest_from_bytes(
                tier_manifest, expected_sha256=dev_ref.manifest_sha256,
                expected_cases=dev_ref.case_count,
            )
            if manifest.protocol_sha256 != _case_protocol_digest(protocol):
                raise TrustedEvaluatorError("public DEV manifest is not bound to the approved protocol")
            self._validate_aggregate_case(
                manifest.cases, case_inputs, tier="dev",
            )
            try:
                response = self.evaluate_dev(
                    candidate, min(3600, remaining - 25),
                )
            except Exception:
                raise TrustedEvaluatorError("public ARE DEV evaluation failed; details withheld") from None
            if not isinstance(response, Mapping) or response.get("status") != "GRADED":
                raise TrustedEvaluatorError("public ARE DEV evaluator returned no terminal result")
            if response.get("dev_cells") != self.dev_cell_count:
                raise TrustedEvaluatorError("public ARE DEV evaluator returned the wrong panel size")
            score = response.get("dev_score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise TrustedEvaluatorError("public ARE DEV evaluator returned an invalid score")
            return self._execution_from_score(
                protocol=protocol, approval_digest=approval_digest,
                campaign_id=campaign_id, spec_hash=spec_hash,
                candidate_snapshot_id=candidate_snapshot_id,
                tier_manifest=tier_manifest, tier="dev", split="public_dev",
                score=float(score),
            )
        if tier != "validation":
            raise TrustedEvaluatorError("public ARE runner only supports DEV and VALIDATION")
        candidate = candidate_files.get(self.candidate_path)
        if not isinstance(candidate, bytes) or not candidate:
            raise TrustedEvaluatorError("candidate snapshot lacks the approved imputer output file")
        validation_ref = next((item for item in protocol.tiers if item.tier == "validation"), None)
        if validation_ref is None:
            raise TrustedEvaluatorError("approved evaluator protocol lacks public VALIDATION")
        validation_manifest = _manifest_from_bytes(
            tier_manifest, expected_sha256=validation_ref.manifest_sha256,
            expected_cases=validation_ref.case_count,
        )
        if validation_manifest.protocol_sha256 != _case_protocol_digest(protocol):
            raise TrustedEvaluatorError("public VALIDATION manifest is not bound to the approved protocol")
        self._validate_aggregate_case(
            validation_manifest.cases, case_inputs, tier="validation",
        )
        if self.clock() >= self.deadline_epoch:
            raise PublicGraderError("campaign deadline expired before public grader submission")

        pending = self.grader.pending_snapshot(candidate_snapshot_id)
        cached = None if pending is not None else self.grader.completed_snapshot_result(
            candidate_snapshot_id, candidate,
        )
        if cached is not None:
            pending = cached
        else:
            # The public scorer snapshots /app/output at submit time. Refresh
            # only this candidate file; never transfer evaluator assets.
            self.publish_candidate(candidate)
            if pending is None:
                pending = self.grader.submit_snapshot_once(
                    candidate_snapshot_id=candidate_snapshot_id,
                    source_snapshot_id=protocol.source_snapshot_id,
                    candidate=candidate,
                    note=f"BananLoop {campaign_id} public validation",
                )
            while pending["state"] != "GRADED":
                if self.clock() >= self.deadline_epoch:
                    # Keep the durable submission ID and resume by polling; never
                    # dispatch a second submit when the outcome remains in flight.
                    raise PublicGraderError("campaign deadline expired while public validation was pending")
                pending = self.grader.poll(
                    int(pending["candidate_iteration"]), int(pending["repetition"]),
                )
                if pending["state"] != "GRADED":
                    self.sleep(min(self.poll_interval_seconds,
                                   max(0.0, self.deadline_epoch - self.clock())))
        return self._execution_from_score(
            protocol=protocol, approval_digest=approval_digest,
            campaign_id=campaign_id, spec_hash=spec_hash,
            candidate_snapshot_id=candidate_snapshot_id,
            tier_manifest=tier_manifest, tier="validation",
            split="public_validation",
            score=self._public_score(pending.get("public_result", pending)),
        )

    @staticmethod
    def _public_score(public_result: Mapping[str, Any]) -> float:
        score = public_result.get("validation_score")
        if (public_result.get("status") != "GRADED" or isinstance(score, bool)
                or not isinstance(score, (int, float)) or not math.isfinite(score)):
            raise TrustedEvaluatorError("public grader result lacks an approved finite score")
        return float(score)

    def _validate_aggregate_case(
        self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes], *, tier: str,
    ) -> None:
        expected_id = (
            self.dev_aggregate_case_id if tier == "dev"
            else self.validation_aggregate_case_id
        )
        if len(cases) != 1 or cases[0].case_id != expected_id or cases[0].repetition != 1:
            raise TrustedEvaluatorError(
                f"public {tier.upper()} protocol must declare exactly one aggregate case"
            )
        identity = (expected_id, 1)
        if set(case_inputs) != {identity}:
            raise TrustedEvaluatorError(f"public {tier.upper()} inputs do not match its aggregate case")
        expected_input = self.aggregate_case_input(
            tier=tier, task_id=self.task_id, task_version=self.task_version,
            contract_sha256=self.contract_sha256, dev_image_id=self.dev_image_id,
            dev_cell_count=self.dev_cell_count,
        )
        case = cases[0]
        payload = case_inputs[identity]
        if (not isinstance(payload, bytes) or payload != expected_input
                or hashlib.sha256(payload).hexdigest() != case.input_sha256):
            raise TrustedEvaluatorError(
                f"public {tier.upper()} case does not bind the exact approved aggregate scope"
            )

    def _verify_backend_bundle(self, protocol: EvaluatorProtocolV1, bundle: bytes) -> None:
        if hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256:
            raise TrustedEvaluatorError("ARE public backend bundle digest differs from approved protocol")
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                if set(archive.namelist()) != {
                    "are-public-backend.json", "public_devkit.py", "task-devkit.py",
                    "public-mcp-contract.json", "optimization_runner.py",
                    "public_grader.py", "are_public_mcp_client.py",
                } or len(archive.infolist()) != 7:
                    raise ValueError
                infos = [item for item in archive.infolist()
                         if item.filename == "are-public-backend.json" and not item.is_dir()]
                if len(infos) != 1 or infos[0].file_size > 64 * 1024:
                    raise ValueError
                manifest = json.loads(archive.read(infos[0]).decode("utf-8"))
                runtime_source = Path(__file__).with_name("public_devkit.py").read_bytes()
                runner_infos = [item for item in archive.infolist()
                                if item.filename == "public_devkit.py" and not item.is_dir()]
                task_infos = [item for item in archive.infolist()
                              if item.filename == "task-devkit.py" and not item.is_dir()]
                contract_infos = [item for item in archive.infolist()
                                  if item.filename == "public-mcp-contract.json" and not item.is_dir()]
                backend_infos = [item for item in archive.infolist()
                                 if item.filename == "optimization_runner.py" and not item.is_dir()]
                grader_infos = [item for item in archive.infolist()
                                if item.filename == "public_grader.py" and not item.is_dir()]
                mcp_client_infos = [item for item in archive.infolist()
                                    if item.filename == "are_public_mcp_client.py" and not item.is_dir()]
                runtime_backend_source = Path(__file__).read_bytes()
                runtime_grader_source = Path(__file__).with_name("public_grader.py").read_bytes()
                runtime_mcp_client_source = Path(__file__).with_name(
                    "are_public_mcp_client.py"
                ).read_bytes()
                if (len(runner_infos) != 1 or len(task_infos) != 1 or len(contract_infos) != 1
                        or len(backend_infos) != 1 or len(grader_infos) != 1
                        or len(mcp_client_infos) != 1
                        or runner_infos[0].file_size > 32 * 1024
                        or task_infos[0].file_size > 64 * 1024
                        or contract_infos[0].file_size > 128 * 1024
                        or backend_infos[0].file_size > 32 * 1024
                        or grader_infos[0].file_size > 32 * 1024
                        or mcp_client_infos[0].file_size > 12 * 1024
                        or archive.read(runner_infos[0]) != runtime_source):
                    raise ValueError
                task_source = archive.read(task_infos[0])
                contract_source = archive.read(contract_infos[0])
                backend_source = archive.read(backend_infos[0])
                grader_source = archive.read(grader_infos[0])
                mcp_client_source = archive.read(mcp_client_infos[0])
                contract = json.loads(contract_source.decode("utf-8"))
                if (not task_source or len(task_source) > 64 * 1024
                        or backend_source != runtime_backend_source):
                    raise ValueError
                if (grader_source != runtime_grader_source
                        or mcp_client_source != runtime_mcp_client_source):
                    raise ValueError
                if (not isinstance(contract, dict)
                        or contract.get("contract_sha256") != self.contract_sha256
                        or contract.get("task_id") != self.task_id
                        or contract.get("task_version") != self.task_version
                        or contract.get("public_endpoint") != "http://grader:8000/mcp"
                        or not {item.get("name") for item in contract.get("discovered_tools", [])
                                if isinstance(item, dict)} >= {"submit", "result"}):
                    raise ValueError
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
            raise TrustedEvaluatorError("ARE public backend bundle manifest is missing or malformed") from None
        expected = {
            "schema_version": 1,
            "backend_id": self.backend_id,
            "task_id": self.task_id,
            "task_version": self.task_version,
            "mcp_contract_sha256": self.contract_sha256,
            "mcp_endpoint": "http://grader:8000/mcp",
            "candidate_path": self.candidate_path,
            "public_output_path": "/app/output/impute.py",
            "public_runner_sha256": hashlib.sha256(runtime_source).hexdigest(),
            "optimization_runner_sha256": hashlib.sha256(backend_source).hexdigest(),
            "public_grader_sha256": hashlib.sha256(grader_source).hexdigest(),
            "mcp_client_sha256": hashlib.sha256(mcp_client_source).hexdigest(),
            "task_devkit_sha256": hashlib.sha256(task_source).hexdigest(),
            "mcp_contract_source_sha256": hashlib.sha256(contract_source).hexdigest(),
        }
        expected.update({
            "public_dev_image": self.dev_image,
            "public_dev_image_id": self.dev_image_id,
            "public_dev_cell_count": self.dev_cell_count,
            "dev_execution": {
                "container_network": "none",
                "root_filesystem_read_only": True,
                "mounted_candidate_path": "/app/output/impute.py",
            },
            "aggregate_case_ids": {
                "dev": self.dev_aggregate_case_id,
                "validation": self.validation_aggregate_case_id,
            },
        })
        if manifest != expected or self.grader.contract_sha256 != self.contract_sha256:
            raise TrustedEvaluatorError("ARE public backend pin differs from task, contract, or candidate")

    @staticmethod
    def _execution_from_score(
        *, protocol: EvaluatorProtocolV1, approval_digest: str,
        campaign_id: str, spec_hash: str, candidate_snapshot_id: str,
        tier_manifest: bytes, tier: str, split: str, score: float,
    ) -> EvaluatorExecutionV1:
        tier_ref = next((item for item in protocol.tiers if item.tier == tier), None)
        if tier_ref is None:
            raise TrustedEvaluatorError(f"approved evaluator protocol lacks {tier.upper()}")
        manifest = _manifest_from_bytes(
            tier_manifest, expected_sha256=tier_ref.manifest_sha256,
            expected_cases=tier_ref.case_count,
        )
        if manifest.protocol_sha256 != _case_protocol_digest(protocol):
            raise TrustedEvaluatorError("public validation manifest is not bound to the approved protocol")
        if not manifest.cases:
            raise TrustedEvaluatorError("approved public validation manifest has no cases")
        evaluations = EvaluationResult(
            status="ok",
            metrics={protocol.metric_field: MetricValue(
                value=float(score), unit="R2", n=len(manifest.cases),
            )},
            diagnostics={
                "split": split,
            },
            protocol_sha256=manifest.protocol_sha256,
            cases=[CaseMeasurement(
                case_id=case.case_id, repetition=case.repetition,
                input_sha256=case.input_sha256,
                # Public response is an aggregate metric. Do not fabricate
                # per-case metric values from that aggregate.
                metrics={},
            ) for case in manifest.cases],
        )
        private_result_bytes = json.dumps(
            evaluations.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        return EvaluatorExecutionV1(
            campaign_id=campaign_id,
            spec_hash=spec_hash,
            approval_digest=approval_digest,
            protocol_id=protocol.protocol_id,
            protocol_version=protocol.version,
            protocol_digest=protocol.content_digest(),
            source_snapshot_id=protocol.source_snapshot_id,
            candidate_snapshot_id=candidate_snapshot_id,
            tier=tier,
            evaluator_bundle_sha256=protocol.evaluator_bundle_sha256,
            tier_manifest_sha256=tier_ref.manifest_sha256,
            case_count=len(manifest.cases),
            result_digest=hashlib.sha256(private_result_bytes).hexdigest(),
            repeatability_runs=protocol.repeatability_runs,
            output_artifact_digests=(),
            artifacts=(),
            private_result_bytes=private_result_bytes,
        )
