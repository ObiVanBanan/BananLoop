from __future__ import annotations

import hashlib
import subprocess

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
def source_root(tmp_path, monkeypatch):
    """Minimal pinned-source-shaped Git tree, with no host checkout dependency."""
    root = tmp_path / "AutoResearchExam"
    root.mkdir()

    # These checks concern task parsing, public build inputs and DEV hashing. The
    # frozen upstream commit/license and pre-existing image pins are external
    # evidence, so substitute deterministic fixture values for those boundaries.
    license_bytes = b"synthetic Apache license fixture\n"
    (root / "LICENSE").write_bytes(license_bytes)
    monkeypatch.setattr(task_catalogue, "APACHE_LICENSE_SHA256",
                        hashlib.sha256(license_bytes).hexdigest())
    monkeypatch.setattr(task_catalogue, "EXISTING_TASKS", {})
    monkeypatch.setattr(task_catalogue, "_existing_image_pins", lambda: {})

    for task_id in NEW_TASKS:
        task_root = root / task_id
        environment = task_root / "environment"
        dev_root = environment / "app" / "data" / "dev"
        dev_root.mkdir(parents=True)
        (task_root / "task.toml").write_text(
            "[task]\nversion = \"1.1.0\"\n\n"
            "[environment]\ngpus = 0\ncpus = 8\nmemory_mb = 14336\n"
            "storage_mb = 1024\n",
            encoding="utf-8",
        )
        (task_root / "instruction.md").write_text(
            f"Synthetic public instruction for {task_id}.\n", encoding="utf-8"
        )
        (environment / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
        (environment / "requirements.txt").write_text("numpy==1.26.4\n", encoding="utf-8")
        (environment / ".dockerignore").write_text("are-grader-ctx\n", encoding="utf-8")
        (dev_root / "panel.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Catalogue Test Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", "commit",
                    "--no-verify", "--quiet", "-m", "fixture"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    monkeypatch.setattr(task_catalogue, "SOURCE_REVISION", revision)
    return root


def test_catalogue_binds_all_three_built_images_and_public_dev_files(source_root):
    catalogue = build_catalogue(source_root, new_image_ids=IMAGE_PINS)
    entries = {item["task_id"]: item for item in catalogue["tasks"]}
    for task_id in NEW_TASKS:
        item = entries[task_id]
        assert item["public_dev_image_id"] == IMAGE_PINS[task_id]
        assert item["integration_status"] == "PINNED_DEV_IMAGE_PUBLIC_SCORER_FIXTURE_AND_HARBOR_ADAPTER_PENDING"
        assert item["dev_files"]


def test_catalogue_rejects_malformed_or_unknown_local_image_pins(source_root):
    with pytest.raises(ValueError, match="SHA-256 image ID"):
        build_catalogue(source_root, new_image_ids={
            "act-tensor-sparse-panel-imputation-r2": "latest",
        })
    with pytest.raises(ValueError, match="unknown task ID"):
        build_catalogue(source_root, new_image_ids={"unknown": "sha256:" + "0" * 64})
