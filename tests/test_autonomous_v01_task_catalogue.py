from __future__ import annotations

import pytest
from pathlib import Path

from benchmarks.autonomous_v01.task_catalogue import NEW_TASKS, build_catalogue


SOURCE = "/home/sonik/bananloop-benchmark-env/sources/AutoResearchExam"
IMAGE_PINS = {
    "act-tensor-sparse-panel-imputation-r2":
        "sha256:f8609c86115e4f304fc0c9e1e9d176693d3c11e53f7986a07d3bdbece23d6875",
    "causalpfn-cate-pehe-ihdp-surfaceb":
        "sha256:3b043696a33b787b0e05f960a7c180e47378016c0c7c0e72ec2cf658af795bac",
    "hicard-latent-encoder":
        "sha256:4ac7196375b04b6804f2b9ccf0059a64fdfc0e1fb0058ee5d7275dbdb0e6198a",
}


def test_catalogue_binds_all_three_built_images_and_public_dev_files():
    catalogue = build_catalogue(Path(SOURCE), new_image_ids=IMAGE_PINS)
    entries = {item["task_id"]: item for item in catalogue["tasks"]}
    for task_id in NEW_TASKS:
        item = entries[task_id]
        assert item["public_dev_image_id"] == IMAGE_PINS[task_id]
        assert item["integration_status"] == "PINNED_DEV_IMAGE_PUBLIC_SCORER_FIXTURE_AND_HARBOR_ADAPTER_PENDING"
        assert item["dev_files"]


def test_catalogue_rejects_malformed_or_unknown_local_image_pins():
    with pytest.raises(ValueError, match="SHA-256 image ID"):
        build_catalogue(Path(SOURCE), new_image_ids={
            "act-tensor-sparse-panel-imputation-r2": "latest",
        })
    with pytest.raises(ValueError, match="unknown task ID"):
        build_catalogue(Path(SOURCE), new_image_ids={"unknown": "sha256:" + "0" * 64})
