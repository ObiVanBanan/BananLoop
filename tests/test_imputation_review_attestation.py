"""Offline checks for the local attestation boundary, not a live audit."""
import hashlib
import subprocess
from pathlib import Path

import pytest

from benchmarks.autonomous_v01 import imputation_review_attestation as audit


def test_audit_rejects_source_mismatch_before_container_creation(tmp_path, monkeypatch):
    expected = {name: "a" * 64 for name in audit.PUBLIC_FILES}
    def unexpected(*args, **kwargs):
        pytest.fail("Docker must not run when source evidence is missing")
    monkeypatch.setattr(audit.subprocess, "run", unexpected)
    with pytest.raises(ValueError, match="missing or unsafe"):
        audit._verify_image_files(image_id="sha256:" + "a" * 64,
                                  expected=expected, source_app=tmp_path,
                                  docker_binary="docker")


def test_audit_copies_only_public_files_and_removes_container(tmp_path, monkeypatch):
    data = {}
    for name in sorted(audit.PUBLIC_FILES):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        data[name] = hashlib.sha256(name.encode()).hexdigest()
    commands = []
    def docker(argv, **kwargs):
        commands.append(argv)
        if argv[1] == "create":
            return subprocess.CompletedProcess(argv, 0, stdout="b" * 64 + "\n")
        if argv[1] == "cp":
            relative = argv[2].split(":/app/", 1)[1]
            Path(argv[3]).write_bytes((tmp_path / relative).read_bytes())
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(audit.subprocess, "run", docker)
    assert audit._verify_image_files(
        image_id="sha256:" + "a" * 64, expected=data,
        source_app=tmp_path, docker_binary="docker",
    ) == data
    assert commands[0][1] == "create"
    assert commands[-1][1:3] == ["rm", "-f"]
    assert {cmd[2].split(":/app/", 1)[1] for cmd in commands if cmd[1] == "cp"} == audit.PUBLIC_FILES
    assert not any(cmd[1] in {"run", "start", "exec"} for cmd in commands)
