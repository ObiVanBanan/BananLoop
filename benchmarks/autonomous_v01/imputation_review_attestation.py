"""Generate a bounded owner-side public image audit before a paid live arm.

Only the pinned public file allowlist is copied from a stopped container.
No model, evaluator, image entrypoint, or private grader is executed.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from .runner import inspect_docker_image, read_json


PUBLIC_FILES = {
    "cellrunner.py", "cellspec.py", "devkit.py", "output/impute.py",
    "data/dev/manifest.json", *(f"data/dev/dev{i}.npz" for i in range(1, 7)),
}


def _file_sha(path: Path, *, root: Path) -> str:
    if (path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError("public audit source is missing or unsafe")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_image_files(*, image_id: str, expected: dict[str, str],
                        source_app: Path, docker_binary: str) -> dict[str, str]:
    if set(expected) != PUBLIC_FILES or any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in expected.values()
    ):
        raise ValueError("public audit file allowlist or digest is invalid")
    for relative, digest in sorted(expected.items()):
        if _file_sha(source_app / relative, root=source_app) != digest:
            raise ValueError("public task source differs from pinned audit: " + relative)

    # Unique name is controlled by this process; never inspect or remove
    # another campaign's container. Do not start the container.
    name = "bananloop-public-audit-" + uuid.uuid4().hex
    created = False
    observed: dict[str, str] = {}
    try:
        create = subprocess.run(
            [docker_binary, "create", "--pull=never", "--network=none",
             "--name", name, "--entrypoint", "/bin/true", image_id],
            check=True, capture_output=True, text=True, timeout=30,
        )
        created = True
        container_id = create.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ValueError("local audit container identity is invalid")
        with tempfile.TemporaryDirectory(prefix="bananloop-public-audit-") as directory:
            root = Path(directory)
            for index, (relative, digest) in enumerate(sorted(expected.items())):
                destination = root / str(index)
                subprocess.run(
                    [docker_binary, "cp", f"{container_id}:/app/{relative}", str(destination)],
                    check=True, capture_output=True, timeout=30,
                )
                actual = _file_sha(destination, root=root)
                if actual != digest:
                    raise ValueError("DEV image public file hash mismatch: " + relative)
                observed[relative] = actual
    finally:
        # Even if docker create timed out after admission, the unique name is
        # safe to reconcile. A successful create must have confirmed removal.
        cleanup = subprocess.run(
            [docker_binary, "rm", "-f", name], capture_output=True, timeout=30,
        )
        if created and cleanup.returncode != 0:
            raise ValueError("local audit container cleanup is unconfirmed")
    return observed


def prepare_review_runtime(*, checkout: Path, runtime_root: Path,
                           output_root: Path, catalogue: dict, task: dict,
                           docker_binary: str = "docker") -> Path:
    task_id = "budgeted-imputation-mcar50"
    if task.get("task_id") != task_id:
        raise ValueError("image audit is scoped to the pinned Imputation task")
    are = checkout / "benchmarks" / "autoresearchexam"
    evidence = read_json(are / "imputation-public-dev-image-evidence.json")
    descriptor = read_json(runtime_root / f"{task_id}.json")
    image_id = task["public_dev_image_id"]
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or evidence["image_id"] != image_id
            or evidence["task_source_revision"] != catalogue["source_revision"]
            or evidence["dockerfile_sha256"] != task["dockerfile_sha256"]
            or evidence["public_dev_cells"] != 42
            or descriptor["public_dev_image_id"] != image_id
            or inspect_docker_image(task["public_dev_image"], docker_binary) != image_id):
        raise ValueError("local image or audit pins differ from the frozen task")
    source = Path(descriptor["source_task_root"]).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError("public task source is missing or unsafe")
    if _file_sha(source / "environment" / "Dockerfile", root=source) != task["dockerfile_sha256"]:
        raise ValueError("public Dockerfile differs from the frozen task")

    # Preserve the official-source attestation, but distinguish a fresh
    # local observation from the published contract and historical claims.
    attestation_path = are / "public-evaluator-attestations" / f"{task_id}.json"
    index = read_json(are / "public-evaluator-attestations" / "index.json")
    entries = [item for item in index["attestations"] if item["task_id"] == task_id]
    if len(entries) != 1 or _file_sha(attestation_path, root=are) != entries[0]["sha256"]:
        raise ValueError("official public documentation attestation pin differs")
    attestation = read_json(attestation_path)
    scope = attestation["scope"]
    if (scope["task_id"] != task_id or scope["task_version"] != task["version"]
            or scope["task_instruction_sha256"] != task["instruction_sha256"]
            or scope["task_source_revision"] != catalogue["source_revision"]):
        raise ValueError("official attestation scope differs from the frozen task")

    observed = _verify_image_files(
        image_id=image_id, expected=evidence["copied_app_file_sha256"],
        source_app=source / "environment" / "app", docker_binary=docker_binary,
    )
    audit = {
        "schema": "local-public-image-file-audit-v1",
        "image_id": image_id, "dockerfile_sha256": task["dockerfile_sha256"],
        "source_revision": catalogue["source_revision"],
        "matched_public_file_sha256": observed,
        "container_started": False,
        "scope": "allowlisted public file matches only",
    }
    # The existing review projector includes the attestation summary and
    # verifies the artifact digest. It does not expose arbitrary extra fields.
    attestation["summary"] += (
        " Fresh owner-side runtime audit: local Docker image ID, public build "
        "input hashes and the following image file hashes matched. The files "
        "were copied from a stopped container, which was removed. "
        "This is a digest-bound local observation, not proof of every image "
        "file or independent server behavior. No Codex comparison is claimed. "
        + json.dumps(audit, sort_keys=True, separators=(",", ":"))
    )
    raw = json.dumps(attestation, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8") + b"\n"
    if len(raw) > 4096:
        raise ValueError("local protocol review attestation exceeds 4 KiB")
    target = output_root / "protocol-review-attestation.json"
    with target.open("xb") as stream:
        stream.write(raw)
    target.chmod(0o400)

    # Private per-run descriptor snapshot. The original runtime is untouched.
    pinned = output_root / "pinned-runtime"
    pinned.mkdir(mode=0o700)
    for entry in catalogue["tasks"]:
        name = f'{entry["task_id"]}.json'
        original = runtime_root / name
        value = read_json(original)
        if entry["task_id"] == task_id:
            value["protocol_review_attestation_path"] = str(target)
            value["protocol_review_attestation_sha256"] = hashlib.sha256(raw).hexdigest()
        destination = pinned / name
        destination.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n",
                               encoding="utf-8")
        destination.chmod(0o400)
    return pinned
