import json
from pathlib import Path

import numpy as np
import pytest

from examples.autoresearchexam.tasks import carps
from examples.autoresearchexam.sandbox import CandidateResult, CandidateSandbox


def test_upstream_exact_star_discrepancy_single_point():
    assert carps.star_discrepancy_3d(np.array([[0.5, 0.5, 0.5]], dtype=np.float64)) == pytest.approx(0.875)


def test_random_reference_is_deterministic_for_a_case():
    points = np.random.default_rng(2).random((800, 3), dtype=np.float64)
    case = carps.CarpsCase("dev-reference", points, 30, 0)
    assert carps.random_subset_baseline(case) == carps.random_subset_baseline(case)


def test_panel_aggregation_requires_exact_case_set_and_worst_invalid_penalty():
    good = carps.CarpsEvaluation("dev-a", 0.2, True, None, 0.1, 0)
    invalid = carps.CarpsEvaluation("dev-b", 1.0, False, "invalid", None, 1)
    summary = carps.summarize_panel(("dev-a", "dev-b"), (good, invalid))
    assert summary.mean_discrepancy == pytest.approx(0.6)
    assert summary.case_count == 2 and summary.valid_runs == 1
    with pytest.raises(carps.CarpsError, match="exactly match"):
        carps.summarize_panel(("dev-a", "dev-b"), (good,))


@pytest.mark.parametrize(
    "indices",
    [
        [True] + list(range(1, 30)),
        list(range(29)),
        list(range(29)) + [29.5],
        list(range(29)) + [800],
        list(range(29)) + [28],
    ],
)
def test_invalid_candidate_indices_are_rejected(indices):
    with pytest.raises(carps.CarpsError):
        carps.validate_indices(indices, n=800, k=30)


def test_public_case_partition_is_frozen_and_disjoint():
    from examples.autoresearchexam.tasks.prepare_carps import PUBLIC_CLOUDS

    grouped = {
        split: {case.case_id for case in PUBLIC_CLOUDS if case.split == split}
        for split in ("dev", "validation", "holdout")
    }
    assert grouped == {
        "dev": {"cloud_00", "cloud_01", "cloud_02", "cloud_03", "cloud_04"},
        "validation": {"cloud_05"},
        "holdout": {"cloud_06", "cloud_07"},
    }
    assert len(set.union(*grouped.values())) == 8
    assert {case.seed for case in PUBLIC_CLOUDS} == {0}


def test_public_preparer_fails_closed_on_changed_upstream_blob(tmp_path):
    from io import BytesIO

    from examples.autoresearchexam.tasks.prepare_carps import prepare

    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="blob mismatch"):
        prepare(output, opener=lambda *_args, **_kwargs: BytesIO(b"not the pinned cloud"))
    assert not output.exists()
    assert not list(tmp_path.glob(".prepared-prepare-*"))


def test_broker_stages_only_candidate_and_sends_one_case(monkeypatch, tmp_path: Path):
    candidate_root = tmp_path / "agent-artifact"
    candidate_root.mkdir()
    (candidate_root / "select_subset.py").write_text("# frozen candidate\n", encoding="utf-8")
    (candidate_root / "grader.txt").write_text("must not be staged", encoding="utf-8")
    evaluator_root = tmp_path / "private-evaluator"
    evaluator_root.mkdir()
    points = np.random.default_rng(14).random((800, 3), dtype=np.float64)
    case = carps.CarpsCase("dev-case-1", points, 30, 17)
    sandbox = CandidateSandbox(
        image="local/carps-runtime@sha256:" + "a" * 64,
        artifact_dir=tmp_path / "unused",
        command=("python", "-I", "/opt/are/carps_runner.py"),
    )
    observed: dict[str, object] = {}

    def fake_run(spec, *, case_input):
        observed["files"] = sorted(path.name for path in spec.artifact_dir.iterdir())
        observed["request"] = json.loads(case_input)
        return CandidateResult(0, json.dumps({"ok": True, "call_s": 0.25, "result": list(range(30))}).encode(), b"")

    monkeypatch.setattr(carps, "run_candidate", fake_run)
    result = carps.evaluate_case(sandbox, case, evaluator_root=evaluator_root, candidate_root=candidate_root)

    assert result.valid
    assert result.case_id == "dev-case-1"
    assert result.discrepancy < carps.INVALID_DISCREPANCY
    assert observed["files"] == ["select_subset.py"]
    request = observed["request"]
    assert isinstance(request, dict)
    assert set(request) == {"points", "k", "seed", "time_budget_s"}
    assert request["k"] == 30 and request["seed"] == 17
    assert len(request["points"]) == 800
    assert list(evaluator_root.iterdir()) == []


def test_invalid_result_gets_worst_metric_and_never_scores(monkeypatch, tmp_path: Path):
    candidate_root = tmp_path / "agent-artifact"
    candidate_root.mkdir()
    (candidate_root / "select_subset.py").write_text("# frozen candidate\n", encoding="utf-8")
    evaluator_root = tmp_path / "private-evaluator"
    evaluator_root.mkdir()
    points = np.random.default_rng(3).random((800, 3), dtype=np.float64)
    sandbox = CandidateSandbox(
        image="local/carps-runtime@sha256:" + "b" * 64,
        artifact_dir=tmp_path / "unused",
        command=("python", "-I", "/opt/are/carps_runner.py"),
    )
    monkeypatch.setattr(
        carps,
        "run_candidate",
        lambda *_args, **_kwargs: CandidateResult(0, b'{"ok":true,"call_s":0.1,"result":[1,1]}', b""),
    )

    result = carps.evaluate_case(
        sandbox,
        carps.CarpsCase("dev-case-2", points, 30, 19),
        evaluator_root=evaluator_root,
        candidate_root=candidate_root,
    )

    assert not result.valid
    assert result.discrepancy == carps.INVALID_DISCREPANCY
    assert result.reason == "candidate must return a flat sequence of exactly 30 indices"
