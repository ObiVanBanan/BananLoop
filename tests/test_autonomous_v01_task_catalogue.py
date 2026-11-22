from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.autonomous_v01 import task_catalogue
from benchmarks.autonomous_v01.task_catalogue import NEW_TASKS, build_catalogue


IMAGE_PINS = {
    "act-tensor-sparse-panel-imputation-r2":
        "sha256:f8609c86115e4f304fc0c9e1e9d176693d3c11e53f7986a07d3bdbece23d6875",
    "causalpfn-cate-pehe-ihdp-surfaceb":
        "sha256:3b043696a33b787b0e05f960a7c180e47378016c0c7c0e72ec2cf658af795bac",
    "hicard-latent-encoder":
        "sha256:4ac7196375b04b6804f2b9ccf0059a64fdfc0e1fb0058ee5d7275dbdb0e6198a",
}


@pytest.fixture
def synthetic_source(tmp_path, monkeypatch):
    """Construct a portable pinned source tree; never require a developer checkout."""
    source = tmp_path / "AutoResearchExam"
    source.mkdir()
    license_bytes = b"Synthetic Apache-2.0 test fixture\n"
    (source / "LICENSE").write_bytes(license_bytes)
    old_tasks = []
    for task_id in (*task_catalogue.EXISTING_TASKS, *NEW_TASKS):
        task_root = source / task_id
        task_root.mkdir()
        task_toml = (
            '[task]\nversion = "1.1.0"\n'
            '[environment]\ngpus = 0\ncpus = 8\n'
            'memory_mb = 14336\nstorage_mb = 1024\n'
        ).encode("utf-8")
        instruction = f"Public instructions for {task_id}\n".encode("utf-8")
        (task_root / "task.toml").write_bytes(task_toml)
        (task_root / "instruction.md").write_bytes(instruction)
        env = task_root / "environment"
        env.mkdir()
        (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (env / "requirements.txt").write_text("# synthetic\n", encoding="utf-8")
        (env / ".dockerignore").write_text("are-grader-ctx\n", encoding="utf-8")
        if task_id in NEW_TASKS:
            dev = env / "app" / "data" / "dev"
            dev.mkdir(parents=True)
            (dev / "public.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        else:
            old_tasks.append({
                "task_id": task_id,
                "task_sha256": hashlib.sha256(task_toml).hexdigest(),
                "instruction_sha256": hashlib.sha256(instruction).hexdigest(),
            })
    selection = tmp_path / "existing-selection.json"
    selection.write_text(json.dumps({"tasks": old_tasks}), encoding="utf-8")
    monkeypatch.setattr(task_catalogue, "OLD_SELECTION", selection)
    monkeypatch.setattr(
        task_catalogue, "APACHE_LICENSE_SHA256", hashlib.sha256(license_bytes).hexdigest(),
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=CI",
        "-c", "user.email=ci@example.invalid", "commit", "-qm", "synthetic source",
    ], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
    ).strip()
    monkeypatch.setattr(task_catalogue, "SOURCE_REVISION", head)
    return source


def test_catalogue_binds_all_three_built_images_and_public_dev_files(synthetic_source):
    catalogue = build_catalogue(synthetic_source, new_image_ids=IMAGE_PINS)
    entries = {item["task_id"]: item for item in catalogue["tasks"]}
    for task_id in NEW_TASKS:
        item = entries[task_id]
        assert item["public_dev_image_id"] == IMAGE_PINS[task_id]
        assert item["integration_status"] == "PINNED_DEV_IMAGE_PUBLIC_SCORER_FIXTURE_AND_HARBOR_ADAPTER_PENDING"
        assert item["dev_files"]


def test_catalogue_rejects_malformed_or_unknown_local_image_pins(synthetic_source):
    with pytest.raises(ValueError, match="SHA-256 image ID"):
        build_catalogue(synthetic_source, new_image_ids={
            "act-tensor-sparse-panel-imputation-r2": "latest",
        })
    with pytest.raises(ValueError, match="unknown task ID"):
        build_catalogue(synthetic_source, new_image_ids={"unknown": "sha256:" + "0" * 64})
