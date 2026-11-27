"""Public-only evaluator adapters for the three v0.1 CPU task additions.

These adapters expose the same trusted-evaluator seam as the existing ARE
runners. Their DEV implementation is backed by the networkless task image;
VALIDATION, when explicitly selected by an approved fixture, uses the ARE
public MCP endpoint through ``PublicAREGrader``. No HOLDOUT tier is offered.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

from .optimization_runner import AREPublicEvaluatorRunner
from benchmarks.autonomous_v01.public_task_runner import run_public_task_dev


_SPECS: dict[str, dict[str, Any]] = {
    "act-tensor-public-v1": {
        "task_id": "act-tensor-sparse-panel-imputation-r2",
        "candidate_path": "benchmarks/autoresearchexam/solutions/act_tensor/impute.py",
        "output_path": "/app/solution/impute.py", "count": 8,
        "metric": "imputation_r2", "unit": "R2", "direction": "MAXIMIZE",
        "image": "bananloop-v01-act-tensor-sparse-panel-imputation-r2:1.1.0",
        "image_id": "sha256:f8609c86115e4f304fc0c9e1e9d176693d3c11e53f7986a07d3bdbece23d6875",
    },
    "causalpfn-public-v1": {
        "task_id": "causalpfn-cate-pehe-ihdp-surfaceb",
        "candidate_path": "benchmarks/autoresearchexam/solutions/causalpfn/estimator.py",
        "output_path": "/app/output/estimator.py", "count": 100,
        "metric": "mean_pehe", "unit": "PEHE", "direction": "MINIMIZE",
        "image": "bananloop-v01-causalpfn-cate-pehe-ihdp-surfaceb:1.1.0",
        "image_id": "sha256:3b043696a33b787b0e05f960a7c180e47378016c0c7c0e72ec2cf658af795bac",
    },
    "hicard-public-v1": {
        "task_id": "hicard-latent-encoder",
        "candidate_path": "benchmarks/autoresearchexam/solutions/hicard/encoder.py",
        "output_path": "/app/output/encoder.py", "count": 24,
        "metric": "onehot_mse_reduction_pct", "unit": "percent", "direction": "MAXIMIZE",
        "image": "bananloop-v01-hicard-latent-encoder:1.1.0",
        "image_id": "sha256:4ac7196375b04b6804f2b9ccf0059a64fdfc0e1fb0058ee5d7275dbdb0e6198a",
    },
}


class AREPublicTaskEvaluatorRunner(AREPublicEvaluatorRunner):
    """Task-scoped public DEV adapter with exact task and image binding."""

    def __init__(self, *, adapter_id: str, grader: Any, publish_candidate: Any,
                 task_version: str, contract_sha256: str, dev_image: str,
                 dev_image_id: str, public_dev_cell_count: int,
                 public_task_root: Path, **kwargs: Any):
        if adapter_id not in _SPECS:
            raise ValueError("public task adapter is not allowlisted")
        spec = _SPECS[adapter_id]
        if (dev_image != spec["image"] or dev_image_id != spec["image_id"]
                or public_dev_cell_count != spec["count"]):
            raise ValueError("public task adapter differs from the frozen image/count pin")
        task_root_in = public_task_root.expanduser()
        if task_root_in.is_symlink() or not task_root_in.is_dir():
            raise ValueError("public task source directory is missing or unsafe")
        task_root = task_root_in.resolve(strict=True)
        if task_root.name != spec["task_id"]:
            raise ValueError("public task source directory does not match the selected adapter")
        task_file = task_root / "task.toml"
        instruction_file = task_root / "instruction.md"
        if any(path.is_symlink() or not path.is_file() for path in (task_file, instruction_file)):
            raise ValueError("public task manifest or instruction is missing or unsafe")
        self._task_sha256 = hashlib.sha256(task_file.read_bytes()).hexdigest()
        self._instruction_sha256 = hashlib.sha256(instruction_file.read_bytes()).hexdigest()
        catalogue = json.loads((Path(__file__).parents[1] / "autonomous_v01"
                                / "task-catalogue.json").read_text(encoding="utf-8"))
        records = [row for row in catalogue["tasks"] if row.get("task_id") == spec["task_id"]]
        if (len(records) != 1 or task_version != "1.1.0"
                or records[0].get("version") != task_version
                or records[0].get("task_sha256") != self._task_sha256
                or records[0].get("instruction_sha256") != self._instruction_sha256):
            raise ValueError("public task source differs from the frozen catalogue revision")
        self._dev_files = self._public_dev_manifest(task_root, spec["count"])
        expected_contract = self.task_contract_digest(
            adapter_id=adapter_id, task_version=task_version,
            task_sha256=self._task_sha256,
            instruction_sha256=self._instruction_sha256,
            dev_files=self._dev_files,
        )
        if contract_sha256 != expected_contract:
            raise ValueError("public task contract digest differs from task and DEV asset pins")

        def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
            result = run_public_task_dev(
                spec["task_id"], candidate, image=dev_image,
                expected_image_id=dev_image_id,
                timeout_seconds=min(3600, timeout_seconds),
            )
            return {"status": "GRADED", "dev_score": result["metric"],
                    "dev_cells": result["dev_cases"]}

        for name in ("task_id", "candidate_path", "dev_image", "dev_image_id",
                     "dev_cell_count", "evaluate_dev"):
            kwargs.pop(name, None)
        super().__init__(
            grader=grader, publish_candidate=publish_candidate,
            task_id=spec["task_id"], task_version=task_version,
            contract_sha256=contract_sha256,
            candidate_path=spec["candidate_path"], dev_image=dev_image,
            dev_image_id=dev_image_id, dev_cell_count=spec["count"],
            evaluate_dev=evaluate_dev, **kwargs,
        )
        self.adapter_id = adapter_id
        self.backend_id = f"autoresearchexam-{adapter_id}"
        self.public_task_root = task_root
        self.public_output_path = spec["output_path"]
        self.metric_field = spec["metric"]
        self.metric_unit = spec["unit"]
        self.metric_direction = spec["direction"]

    @classmethod
    def spec(cls, adapter_id: str) -> dict[str, Any]:
        if adapter_id not in _SPECS:
            raise ValueError("public task adapter is not allowlisted")
        return dict(_SPECS[adapter_id])

    @classmethod
    def task_contract_digest(cls, *, adapter_id: str, task_version: str,
                             task_sha256: str, instruction_sha256: str,
                             dev_files: list[dict[str, Any]]) -> str:
        spec = cls.spec(adapter_id)
        value = {
            "schema": "bananloop-are-local-public-dev-task-contract-v1",
            "adapter_id": adapter_id, "task_id": spec["task_id"],
            "task_version": task_version, "task_sha256": task_sha256,
            "instruction_sha256": instruction_sha256,
            "candidate_path": spec["candidate_path"],
            "public_output_path": spec["output_path"],
            "metric_field": spec["metric"], "metric_direction": spec["direction"],
            "public_dev_image_id": spec["image_id"], "dev_files": dev_files,
        }
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _public_dev_manifest(task_root: Path, expected_count: int) -> list[dict[str, Any]]:
        import json
        catalogue = Path(__file__).parents[1] / "autonomous_v01" / "task-catalogue.json"
        body = json.loads(catalogue.read_text(encoding="utf-8"))
        rows = [item for item in body["tasks"] if item.get("task_id") == task_root.name]
        if len(rows) != 1 or len(rows[0].get("dev_files", [])) != expected_count:
            raise ValueError("public DEV fixture does not match the curated task catalogue")
        files = []
        for item in rows[0]["dev_files"]:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts or "are-grader-ctx" in relative.parts:
                raise ValueError("public DEV fixture contains an unsafe path")
            path = task_root / relative
            current = task_root
            if any((current := current / part).is_symlink() for part in relative.parts):
                raise ValueError("public DEV fixture contains a symlinked path component")
            if not path.is_file() or not path.resolve(strict=True).is_relative_to(task_root.resolve()):
                raise ValueError("public DEV input is missing or unsafe")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("public DEV input digest differs from the curated catalogue")
            files.append({"path": relative.as_posix(), "sha256": digest,
                          "size_bytes": path.stat().st_size})
        return files

    def _verify_backend_bundle(self, protocol: Any, bundle: bytes) -> None:
        expected = self.build_bundle(
            adapter_id=self.adapter_id, task_id=self.task_id,
            task_version=self.task_version, contract_sha256=self.contract_sha256,
            dev_image_id=self.dev_image_id, task_sha256=self._task_sha256,
            instruction_sha256=self._instruction_sha256, dev_files=self._dev_files,
        )
        if (protocol.execution_backend_id != self.backend_id
                or hashlib.sha256(bundle).hexdigest() != protocol.evaluator_bundle_sha256
                or bundle != expected):
            from bananloop.trusted_evaluator import TrustedEvaluatorError
            raise TrustedEvaluatorError("public task evaluator bundle differs from its frozen public pins")

    @classmethod
    def build_bundle(cls, *, adapter_id: str, task_id: str, task_version: str,
                     contract_sha256: str, dev_image_id: str, task_sha256: str,
                     instruction_sha256: str,
                     dev_files: list[dict[str, Any]]) -> bytes:
        import json
        spec = cls.spec(adapter_id)
        if (task_id != spec["task_id"] or task_version != "1.1.0"
                or dev_image_id != spec["image_id"]):
            raise ValueError("public task bundle identity differs from frozen pins")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in (task_sha256, instruction_sha256, contract_sha256)):
            raise ValueError("public task bundle digest is malformed")
        if len(dev_files) != spec["count"]:
            raise ValueError("public task bundle has the wrong public DEV panel count")
        for item in dev_files:
            if not isinstance(item, dict):
                raise ValueError("public task bundle contains a malformed DEV file record")
            relative = Path(item.get("path", ""))
            if (relative.is_absolute() or ".." in relative.parts
                    or "are-grader-ctx" in relative.parts
                    or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
                    or not isinstance(item.get("size_bytes"), int)
                    or item["size_bytes"] < 0):
                raise ValueError("public task bundle contains an invalid DEV file record")
        if contract_sha256 != cls.task_contract_digest(
                adapter_id=adapter_id, task_version=task_version,
                task_sha256=task_sha256,
                instruction_sha256=instruction_sha256, dev_files=dev_files):
            raise ValueError("public task bundle contract digest differs from its frozen scope")
        value = {
            "schema": "bananloop-are-public-task-evaluator-bundle-v1",
            "backend_id": f"autoresearchexam-{adapter_id}",
            "adapter_id": adapter_id, "task_id": task_id,
            "task_version": task_version, "task_sha256": task_sha256,
            "instruction_sha256": instruction_sha256,
            "candidate_path": spec["candidate_path"],
            "public_output_path": spec["output_path"],
            "metric_field": spec["metric"], "metric_direction": spec["direction"],
            "dev_image": spec["image"], "dev_image_id": dev_image_id,
            "contract_sha256": contract_sha256,
            "dev_files": dev_files,
            "public_task_dev_runner_sha256": hashlib.sha256(
                (Path(__file__).parents[1] / "autonomous_v01" / "public_task_runner.py").read_bytes()).hexdigest(),
            "public_task_dev_scorer_sha256": hashlib.sha256(
                (Path(__file__).parents[1] / "autonomous_v01" / "public_task_dev.py").read_bytes()).hexdigest(),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def aggregate_case_input(cls, *, adapter_id: str, tier: str, task_id: str,
                             task_version: str, contract_sha256: str,
                             dev_image_id: str, dev_cell_count: int,
                             task_sha256: str, instruction_sha256: str) -> bytes:
        spec = cls.spec(adapter_id)
        if (task_id != spec["task_id"] or dev_image_id != spec["image_id"]
                or dev_cell_count != spec["count"] or tier not in {"dev", "validation"}
                or not all(len(value) == 64 for value in
                           (task_sha256, instruction_sha256, contract_sha256))):
            raise ValueError("aggregate evaluator scope does not match its task pins")
        value = {
            "schema": "autoresearchexam-public-task-aggregate-v1",
            "adapter_id": adapter_id, "task_id": task_id,
            "task_version": task_version, "tier": tier,
            "candidate_path": spec["candidate_path"],
            "public_output_path": spec["output_path"],
            "metric_field": spec["metric"], "metric_direction": spec["direction"],
            "dev_cases": spec["count"], "dev_image_id": dev_image_id,
            "task_sha256": task_sha256,
            "instruction_sha256": instruction_sha256,
            "contract_sha256": contract_sha256,
            "measurement": "public DEV aggregate" if tier == "dev"
            else "public DEV repeated for the Optimization lifecycle; not a holdout",
        }
        # Avoid accepting a malformed or noncanonical digest-shaped input.
        if any(not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in (task_sha256, instruction_sha256, contract_sha256)):
            raise ValueError("aggregate evaluator digest is malformed")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def _execution_from_score(**kwargs: Any):
        protocol = kwargs["protocol"]
        unit = {"mean_pehe": "PEHE", "imputation_r2": "R2",
                "onehot_mse_reduction_pct": "percent"}.get(
                    protocol.metric_field, "public_task_metric")
        return AREPublicEvaluatorRunner._execution_from_score(
            **kwargs, metric_unit=unit,
        )

    def _validate_aggregate_case(self, cases: Any, case_inputs: Mapping[tuple[str, int], bytes],
                                 *, tier: str) -> None:
        # Reuse base case integrity logic after binding to this task-specific scope.
        expected_id = f"{self.task_id}-public-{tier}"
        if len(cases) != 1 or cases[0].case_id != expected_id or cases[0].repetition != 1:
            from bananloop.trusted_evaluator import TrustedEvaluatorError
            raise TrustedEvaluatorError("public task protocol requires one aggregate case per tier")
        identity = (expected_id, 1)
        payload = case_inputs.get(identity)
        expected = self.aggregate_case_input(
            adapter_id=self.adapter_id, tier=tier, task_id=self.task_id,
            task_version=self.task_version, contract_sha256=self.contract_sha256,
            dev_image_id=self.dev_image_id, dev_cell_count=self.dev_cell_count,
            task_sha256=self._task_sha256, instruction_sha256=self._instruction_sha256,
        )
        if (set(case_inputs) != {identity} or payload != expected
                or hashlib.sha256(payload).hexdigest() != cases[0].input_sha256):
            from bananloop.trusted_evaluator import TrustedEvaluatorError
            raise TrustedEvaluatorError("public task aggregate case changed after protocol freeze")

    def run(self, *, protocol: Any, tier: str, tier_manifest: bytes,
            case_inputs: Mapping[tuple[str, int], bytes], **kwargs: Any):
        """Use the same public DEV panels for lifecycle validation; never call a grader sidecar."""
        if tier == "dev":
            return super().run(protocol=protocol, tier=tier,
                               tier_manifest=tier_manifest,
                               case_inputs=case_inputs, **kwargs)
        from bananloop.trusted_evaluator import (
            TrustedEvaluatorError, _case_protocol_digest, _manifest_from_bytes,
        )
        if tier != "validation":
            raise TrustedEvaluatorError("local public task adapter has no HOLDOUT tier")
        self._verify_backend_bundle(protocol, kwargs["evaluator_bundle"])
        if protocol.execution_backend_id != self.backend_id:
            raise TrustedEvaluatorError("public task evaluator backend differs from its frozen protocol")
        candidate = kwargs["candidate_files"].get(self.candidate_path)
        if not isinstance(candidate, bytes) or not candidate:
            raise TrustedEvaluatorError("candidate snapshot lacks the task-specific output")
        reference = next((item for item in protocol.tiers if item.tier == "validation"), None)
        if reference is None:
            raise TrustedEvaluatorError("public task fixture lacks its lifecycle validation tier")
        manifest = _manifest_from_bytes(
            tier_manifest, expected_sha256=reference.manifest_sha256,
            expected_cases=reference.case_count,
        )
        if manifest.protocol_sha256 != _case_protocol_digest(protocol):
            raise TrustedEvaluatorError("public task validation manifest differs from its frozen protocol")
        self._validate_aggregate_case(manifest.cases, case_inputs, tier="validation")
        if self.clock() >= self.deadline_epoch:
            from .public_grader import PublicGraderError
            raise PublicGraderError("campaign deadline expired before public DEV validation")
        response = self.evaluate_dev(
            candidate, max(1, min(3600, int(self.deadline_epoch - self.clock()) - 1)),
        )
        score = response.get("dev_score") if isinstance(response, Mapping) else None
        if (not isinstance(response, Mapping) or response.get("status") != "GRADED"
                or response.get("dev_cells") != self.dev_cell_count
                or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(float(score))):
            raise TrustedEvaluatorError("public DEV validation pass lacks its pinned aggregate")
        return self._execution_from_score(
            protocol=protocol, approval_digest=kwargs["approval_digest"],
            campaign_id=kwargs["campaign_id"], spec_hash=kwargs["spec_hash"],
            candidate_snapshot_id=kwargs["candidate_snapshot_id"],
            tier_manifest=tier_manifest, tier="validation",
            split="public_dev_reused_for_lifecycle_validation", score=float(score),
        )


class AREACTPublicEvaluatorRunner(AREPublicTaskEvaluatorRunner):
    backend_id = "autoresearchexam-act-tensor-public-v1"
    default_dev_image = _SPECS["act-tensor-public-v1"]["image"]
    default_dev_image_id = _SPECS["act-tensor-public-v1"]["image_id"]


class ARECausalPFNPublicEvaluatorRunner(AREPublicTaskEvaluatorRunner):
    backend_id = "autoresearchexam-causalpfn-public-v1"
    default_dev_image = _SPECS["causalpfn-public-v1"]["image"]
    default_dev_image_id = _SPECS["causalpfn-public-v1"]["image_id"]


class AREHiCardPublicEvaluatorRunner(AREPublicTaskEvaluatorRunner):
    backend_id = "autoresearchexam-hicard-public-v1"
    default_dev_image = _SPECS["hicard-public-v1"]["image"]
    default_dev_image_id = _SPECS["hicard-public-v1"]["image_id"]
