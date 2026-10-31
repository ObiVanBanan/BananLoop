from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.autoresearchexam.sandbox import (
    CandidateSandbox,
    SandboxError,
    docker_argv,
    run_candidate,
    stage_candidate_artifact,
)


IMAGE = "python:3.13-slim@sha256:" + "a" * 64


def _spec(root: Path, **kwargs) -> CandidateSandbox:
    root.mkdir(exist_ok=True)
    (root / "candidate.py").write_text("print('candidate')\n", encoding="utf-8")
    return CandidateSandbox(
        image=IMAGE,
        artifact_dir=root,
        command=("python", "-I", "/candidate/candidate.py"),
        **kwargs,
    )


def test_docker_argv_applies_container_limits_and_only_mounts_candidate(tmp_path):
    spec = _spec(tmp_path / "artifact")
    argv = docker_argv(spec, name="bananloop-candidate-" + "1" * 32)

    for required in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user=65532:65532",
        "--pids-limit",
        "--memory",
        "--cpus",
        "--tmpfs",
        "--shm-size=64m",
    ):
        assert required in argv
    mounts = [argv[index + 1] for index, part in enumerate(argv[:-1]) if part == "--mount"]
    assert len(mounts) == 1
    assert "target=/candidate,readonly" in mounts[0]
    assert "evaluator" not in " ".join(argv).lower()
    assert "--env" not in argv and "-e" not in argv
    assert "--entrypoint" in argv
    assert "--log-driver=local" in argv
    assert "--log-opt=max-size=1m" in argv
    assert "--log-opt=compress=false" in argv


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image": "python:latest"},
        {"cpu_count": 2.1},
        {"memory_bytes": 9 * 1024**3},
        {"pids_limit": 65},
        {"tmpfs_bytes": 25 * 1024**3},
    ],
)
def test_invalid_sandbox_policy_fails_closed(tmp_path, kwargs):
    spec = _spec(tmp_path / "artifact")
    spec = replace(spec, **kwargs)
    with pytest.raises(SandboxError):
        docker_argv(spec, name="bananloop-candidate-" + "1" * 32)


def test_run_candidate_does_not_forward_campaign_secrets_or_mount_grader(tmp_path, monkeypatch):
    spec = _spec(tmp_path / "artifact")
    captured = {}

    class FakeProcess:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs["env"]
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b'{"ok":true}')
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-forward")
    monkeypatch.setenv("CAMPAIGN_PRIVATE_VALUE", "must-not-forward")
    monkeypatch.setattr("examples.autoresearchexam.sandbox.subprocess.Popen", FakeProcess)
    monkeypatch.setattr(
        "examples.autoresearchexam.sandbox.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="null|[]"),
    )
    result = run_candidate(spec, case_input=b'{"public_case":1}')

    assert result.returncode == 0
    assert result.stdout == b'{"ok":true}'
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "CAMPAIGN_PRIVATE_VALUE" not in captured["env"]
    assert "evaluator" not in " ".join(captured["argv"]).lower()


def test_oversized_case_input_rejected_before_docker(tmp_path, monkeypatch):
    spec = _spec(tmp_path / "artifact", input_limit_bytes=3)
    monkeypatch.setattr(
        "examples.autoresearchexam.sandbox.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Docker must not start"),
    )
    with pytest.raises(SandboxError, match="input exceeds"):
        run_candidate(spec, case_input=b"four")


@pytest.mark.parametrize(
    "metadata",
    [
        '{"/data":{}}|[]',
        'null|["OPENAI_API_KEY=not-logged"]',
    ],
)
def test_image_metadata_rejects_implicit_volumes_and_credentials(tmp_path, monkeypatch, metadata):
    spec = _spec(tmp_path / "artifact")
    monkeypatch.setattr(
        "examples.autoresearchexam.sandbox.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=metadata),
    )
    from examples.autoresearchexam.sandbox import _validate_image

    with pytest.raises(SandboxError):
        _validate_image(spec)


def test_bounded_output_stops_an_overproducing_docker_client(tmp_path, monkeypatch):
    spec = _spec(tmp_path / "artifact", output_limit_bytes=4)

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"0123456789")
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("examples.autoresearchexam.sandbox.subprocess.Popen", FakeProcess)
    monkeypatch.setattr(
        "examples.autoresearchexam.sandbox.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="null|[]"),
    )
    with pytest.raises(SandboxError, match="output limit"):
        run_candidate(spec, case_input=b"")


def test_stage_candidate_copies_only_allowlisted_files(tmp_path):
    source = tmp_path / "agent"
    (source / "output").mkdir(parents=True)
    (source / "output" / "solution.py").write_text("def solve(x): return x\n", encoding="utf-8")
    (source / "grader.py").write_text("private grader", encoding="utf-8")
    artifact = stage_candidate_artifact(
        source,
        tmp_path / "frozen-artifact",
        ("output/solution.py",),
    )

    assert (artifact / "output" / "solution.py").read_text(encoding="utf-8").startswith("def solve")
    assert not (artifact / "grader.py").exists()
    assert not ((artifact / "output" / "solution.py").stat().st_mode & 0o222)


def test_stage_candidate_rejects_symlink_source(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    secret = tmp_path / "private.txt"
    secret.write_text("must not stage", encoding="utf-8")
    link = source / "solution.py"
    try:
        link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    with pytest.raises(SandboxError, match="symlinks"):
        stage_candidate_artifact(source, tmp_path / "artifact", ("solution.py",))


@pytest.mark.parametrize("path", ["../grader.py", "/absolute.py", "C:grader.py", "output\\solution.py"])
def test_stage_candidate_rejects_noncanonical_paths(tmp_path, path):
    source = tmp_path / "agent"
    source.mkdir()
    with pytest.raises(SandboxError):
        stage_candidate_artifact(source, tmp_path / "artifact", (path,))
