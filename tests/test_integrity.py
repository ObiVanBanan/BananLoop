from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bananloop.contracts import AgentConfig, BudgetSpec, CampaignSpec, Direction, EvaluationAsset, MetricSpec
from bananloop.integrity import (
    EvaluationIntegrityError,
    discover_evaluation_assets,
    evaluation_fingerprint,
    verify_evaluation_assets,
)


def test_discover_evaluation_assets_hashes_command_files_and_explicit_assets(tmp_path: Path):
    repo = tmp_path
    evaluator = repo / "evaluator.py"
    scorer = repo / "scorer.json"
    evaluator.write_text("print('ok')\n", encoding="utf-8")
    scorer.write_text('{"version":1}\n', encoding="utf-8")

    assets = discover_evaluation_assets(
        repo,
        ["python", "evaluator.py"],
        ["scorer.json"],
    )

    assert [asset.path for asset in assets] == ["evaluator.py", "scorer.json"]
    assert assets[0].sha256 == hashlib.sha256(evaluator.read_bytes()).hexdigest()
    assert evaluation_fingerprint(assets)


def test_verify_evaluation_assets_detects_drift(tmp_path: Path):
    path = tmp_path / "evaluator.py"
    path.write_text("one\n", encoding="utf-8")
    asset = EvaluationAsset(
        path="evaluator.py",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    assert verify_evaluation_assets(tmp_path, [asset]) == []

    path.write_text("two\n", encoding="utf-8")
    assert verify_evaluation_assets(tmp_path, [asset]) == ["hash_mismatch:evaluator.py"]


def test_empty_evaluation_assets_preserve_legacy_spec_hash():
    legacy = {
        "schema_version": 1,
        "goal": "test",
        "source_repo": "repo",
        "base_commit": "abc",
        "evaluation_command": ["python", "evaluator.py"],
        "primary_metric": {
            "name": "quality",
            "direction": "maximize",
            "target": 1.0,
            "min_improvement": 0.1,
            "unit": None,
            "minimum": None,
            "maximum": None,
        },
        "guardrails": [],
        "mandatory_checks": [],
        "editable_scope": ["**"],
        "protected_paths": [],
        "budget": BudgetSpec().model_dump(mode="json"),
        "agent": AgentConfig().model_dump(mode="json"),
    }
    expected = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    spec = CampaignSpec.model_validate(legacy)

    assert spec.evaluation_assets == []
    assert spec.approved_hash() == expected



def test_discovery_ignores_external_existing_command_files(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    evaluator = repo / "evaluator.py"
    evaluator.write_text("print('ok')\n", encoding="utf-8")
    external = tmp_path / "hidden.json"
    external.write_text('{"cases": []}\n', encoding="utf-8")

    assets = discover_evaluation_assets(
        repo,
        ["python", "evaluator.py", "--holdout", str(external)],
    )

    assert [asset.path for asset in assets] == ["evaluator.py"]


def test_explicit_external_eval_asset_is_rejected(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "hidden.json"
    external.write_text("{}\n", encoding="utf-8")

    with pytest.raises(EvaluationIntegrityError, match="must be inside repository"):
        discover_evaluation_assets(repo, ["python"], [str(external)])
