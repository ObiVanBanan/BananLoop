import hashlib
import json

import pytest
from types import SimpleNamespace

from benchmarks.autoresearchexam.public_task_evaluator import AREPublicTaskEvaluatorRunner


@pytest.mark.parametrize("adapter_id,task_id,output_path,direction,metric", [
    ("act-tensor-public-v1", "act-tensor-sparse-panel-imputation-r2",
     "/app/solution/impute.py", "MAXIMIZE", "imputation_r2"),
    ("causalpfn-public-v1", "causalpfn-cate-pehe-ihdp-surfaceb",
     "/app/output/estimator.py", "MINIMIZE", "mean_pehe"),
    ("hicard-public-v1", "hicard-latent-encoder",
     "/app/output/encoder.py", "MAXIMIZE", "onehot_mse_reduction_pct"),
])
def test_public_task_adapter_pins_task_output_metric_and_direction(
    adapter_id, task_id, output_path, direction, metric,
):
    spec = AREPublicTaskEvaluatorRunner.spec(adapter_id)
    assert spec["task_id"] == task_id
    assert spec["output_path"] == output_path
    assert spec["direction"] == direction
    assert spec["metric"] == metric
    assert len(spec["image_id"].removeprefix("sha256:")) == 64


def test_cate_fixture_scope_preserves_minimize_direction_and_exact_output_path():
    raw = AREPublicTaskEvaluatorRunner.aggregate_case_input(
        adapter_id="causalpfn-public-v1", tier="dev",
        task_id="causalpfn-cate-pehe-ihdp-surfaceb", task_version="1.1.0",
        contract_sha256="a" * 64,
        dev_image_id=AREPublicTaskEvaluatorRunner.spec("causalpfn-public-v1")["image_id"],
        dev_cell_count=100, task_sha256="b" * 64, instruction_sha256="c" * 64,
    )
    scope = json.loads(raw)
    assert scope["metric_direction"] == "MINIMIZE"
    assert scope["metric_field"] == "mean_pehe"
    assert scope["public_output_path"] == "/app/output/estimator.py"
    assert hashlib.sha256(raw).hexdigest()


def test_fixture_scope_rejects_wrong_image_and_malformed_digest():
    spec = AREPublicTaskEvaluatorRunner.spec("act-tensor-public-v1")
    common = dict(
        adapter_id="act-tensor-public-v1", tier="dev",
        task_id=spec["task_id"], task_version="1.1.0",
        contract_sha256="a" * 64, dev_image_id=spec["image_id"],
        dev_cell_count=8, task_sha256="b" * 64, instruction_sha256="c" * 64,
    )
    with pytest.raises(ValueError):
        AREPublicTaskEvaluatorRunner.aggregate_case_input(**{**common, "dev_image_id": "sha256:" + "0" * 64})
    with pytest.raises(ValueError):
        AREPublicTaskEvaluatorRunner.aggregate_case_input(**{**common, "task_sha256": "x" * 64})


def test_bundle_digest_pins_public_scorer_and_fixture_files():
    spec = AREPublicTaskEvaluatorRunner.spec("hicard-public-v1")
    files = [{"path": f"environment/app/data/dev/instance_{index:05d}.npz",
              "sha256": f"{index:064x}", "size_bytes": 12}
             for index in range(spec["count"])]
    task_contract = AREPublicTaskEvaluatorRunner.task_contract_digest(
        adapter_id="hicard-public-v1", task_version="1.1.0",
        task_sha256="b" * 64, instruction_sha256="c" * 64,
        dev_files=files,
    )
    bundle = AREPublicTaskEvaluatorRunner.build_bundle(
        adapter_id="hicard-public-v1", task_id=spec["task_id"],
        task_version="1.1.0", contract_sha256=task_contract,
        dev_image_id=spec["image_id"], task_sha256="b" * 64,
        instruction_sha256="c" * 64, dev_files=files,
    )
    manifest = json.loads(bundle)
    assert manifest["dev_files"] == files
    assert manifest["metric_direction"] == "MAXIMIZE"
    assert manifest["public_output_path"] == "/app/output/encoder.py"
    assert len(hashlib.sha256(bundle).hexdigest()) == 64


@pytest.mark.parametrize("adapter_id", [
    "act-tensor-public-v1", "causalpfn-public-v1", "hicard-public-v1",
])
def test_runner_rejects_unpinned_task_source(tmp_path, adapter_id):
    from benchmarks.autoresearchexam.public_task_evaluator import AREPublicTaskEvaluatorRunner

    spec = AREPublicTaskEvaluatorRunner.spec(adapter_id)
    task_root = tmp_path / spec["task_id"]
    task_root.mkdir()
    (task_root / "task.toml").write_text("not the pinned task", encoding="utf-8")
    (task_root / "instruction.md").write_text("not the pinned instructions", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen catalogue revision"):
        AREPublicTaskEvaluatorRunner(
            adapter_id=adapter_id,
            grader=SimpleNamespace(deadline_epoch=200.0, repeatability_runs=1,
                                   contract_sha256="a" * 64),
            publish_candidate=lambda _data: None, task_version="1.1.0",
            contract_sha256="a" * 64, dev_image=spec["image"],
            dev_image_id=spec["image_id"], public_dev_cell_count=spec["count"],
            public_task_root=task_root, deadline_epoch=200.0,
        )
