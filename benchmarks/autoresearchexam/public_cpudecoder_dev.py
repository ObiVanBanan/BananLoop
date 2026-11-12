"""Public-only bounded DEV pilot for cpu-decoder-graph-executor.

Runs the task's public benchmark on three fixed public seeds inside the pinned
networkless image. The official task uses all 16 public seeds and sealed seeds;
this smaller panel is diagnostic only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .public_devkit import _run_bounded_container, _stop_container

TASK_ID = "cpu-decoder-graph-executor"
TASK_VERSION = "1.1.0"
TASK_SHA256 = "ce70b7172769b2e569c3bbcd93644a185f5480bb15299bae9de7c374761ec4e4"
INSTRUCTION_SHA256 = "826bde245fa3fa4eac4235b3cf0fc7f95d06a2d4c643d5ccab3b63c5a71840f7"
TASK_DOCKERFILE_SHA256 = "d3474350fb3e93949afd1b29a8eaf952b62ef08d8cc93c268a26dde82ac59123"
TASK_DOCKERIGNORE_SHA256 = "172c9772dd4171f99e71b3648c2da214e5e24c74d8b8166159cddfca1acb64bb"
TASK_REQUIREMENTS_SHA256 = "e79f852cbfe98fab4d4d3d99100f306aadcb58e15f139d9663682393f867022b"
PUBLIC_BENCH_SHA256 = "cd52ae6e0dd172c5b8d0b2a930fdfc8b6b15c2eae7f5b3f67aa6b43231dbe465"
PUBLIC_HARNESS_SHA256 = "e68f7429c55b47d4b97e285944837c05c7b77f6d7f58d0f5e8d151bbf43c9825"
PUBLIC_GRAPH_SPEC_SHA256 = "31f6366dab5179b76a96ef2b6aeb5cade3ce086d78e9267b41dba03c4ffe6601"
PUBLIC_REFERENCE_SHA256 = "be8e00ad88eb480806d34d4f17313d1510685f8a3321a57f07fe9be6b0014b10"
PUBLIC_WORKER_SHA256 = "179d72e86a548dcb7e96ea98a54f4b5b33f10c3d608ec65eafb8b59637be91a6"
SELECTION_SHA256 = "071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316"
DEFAULT_IMAGE = "bananloop-are-cpudecoder-public-dev:1.1.0"
DEFAULT_IMAGE_ID = "sha256:dd54ec300f39cb3bb9766592fcde5e92fce7b09df64364a21551cb8eb04757f7"
PUBLIC_SEEDS = (0, 7, 15)
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESULT_PREFIX = "BANANLOOP_CPUDECODER_DEV="

_PUBLIC_FILES = {
    "task.toml": ("task.toml", TASK_SHA256),
    "instruction.md": ("instruction.md", INSTRUCTION_SHA256),
    "environment/Dockerfile": ("environment/Dockerfile", TASK_DOCKERFILE_SHA256),
    "environment/.dockerignore": ("environment/.dockerignore", TASK_DOCKERIGNORE_SHA256),
    "environment/requirements.txt": ("environment/requirements.txt", TASK_REQUIREMENTS_SHA256),
    "environment/app/bench.py": ("environment/app/bench.py", PUBLIC_BENCH_SHA256),
    "environment/app/harness.py": ("environment/app/harness.py", PUBLIC_HARNESS_SHA256),
    "environment/app/graph_spec.py": ("environment/app/graph_spec.py", PUBLIC_GRAPH_SPEC_SHA256),
    "environment/app/reference_executor.py": ("environment/app/reference_executor.py", PUBLIC_REFERENCE_SHA256),
    "environment/app/worker.py": ("environment/app/worker.py", PUBLIC_WORKER_SHA256),
}


class PublicCPUDecoderError(RuntimeError):
    """The public CPU-decoder inputs or DEV result failed validation."""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_public_inputs(task_root: Path) -> dict[str, Any]:
    root = task_root.expanduser()
    if root.is_symlink() or not root.is_dir() or root.name != TASK_ID:
        raise PublicCPUDecoderError("public CPU-decoder task root is missing or unsafe")
    root = root.resolve()
    for rel, expected in _PUBLIC_FILES.values():
        path = root / rel
        if path.is_symlink() or not path.is_file() or _sha(path) != expected:
            raise PublicCPUDecoderError("public CPU-decoder source differs from its frozen digest")
    if (root / "environment/.dockerignore").read_text(encoding="utf-8").strip() != "are-grader-ctx":
        raise PublicCPUDecoderError("public Docker context does not exclude the verifier directory")
    try:
        import tomllib
        task = tomllib.loads((root / "task.toml").read_text(encoding="utf-8"))
        selection_path = Path(__file__).with_name("task-selection.json")
        if selection_path.is_symlink() or _sha(selection_path) != SELECTION_SHA256:
            raise ValueError
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        matches = [item for item in selection["tasks"] if item.get("task_id") == TASK_ID]
    except (OSError, ValueError, KeyError, TypeError):
        raise PublicCPUDecoderError("public task manifest or selected task pin is malformed") from None
    if (task.get("task", {}).get("version") != TASK_VERSION or len(matches) != 1
            or matches[0].get("version") != TASK_VERSION
            or matches[0].get("task_sha256") != TASK_SHA256
            or matches[0].get("instruction_sha256") != INSTRUCTION_SHA256
            or matches[0].get("resources", {}).get("cpus") != 8
            or matches[0].get("resources", {}).get("memory_mb") != 14336):
        raise PublicCPUDecoderError("public task identity or frozen resource envelope changed")
    return {"task_sha256": TASK_SHA256, "instruction_sha256": INSTRUCTION_SHA256,
            "public_files": len(_PUBLIC_FILES), "public_seeds": list(PUBLIC_SEEDS)}


def _container_entry(candidate_dir: Path, controller: Path, *, timeout_seconds: int) -> dict[str, Any]:
    """Entry point invoked inside the task image; public benchmark only."""
    import json as _json
    import subprocess as _subprocess
    result_path = "/tmp/bananloop-cpudecoder-result.json"
    run = _subprocess.run(
        ["python3", "/app/bench.py", "--dir", str(candidate_dir),
         "--seeds", ",".join(map(str, PUBLIC_SEEDS)), "--json", result_path],
        capture_output=True, text=True, timeout=timeout_seconds, check=False,
    )
    if run.returncode != 0:
        raise PublicCPUDecoderError("public benchmark command failed: " + run.stderr[-2000:])
    with open(result_path, encoding="utf-8") as stream:
        record = _json.load(stream)
    print(_RESULT_PREFIX + _json.dumps(record, sort_keys=True, separators=(",", ":")))
    return record


def evaluate_candidate(candidate: bytes, *, image: str = DEFAULT_IMAGE,
                       expected_image_id: str = DEFAULT_IMAGE_ID,
                       timeout_seconds: int = 600,
                       docker_binary: str = "docker") -> dict[str, Any]:
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > MAX_CANDIDATE_BYTES:
        raise PublicCPUDecoderError("candidate is empty or exceeds the source size limit")
    if (not isinstance(image, str) or not image or image.startswith("-")
            or any(ch.isspace() for ch in image) or not _IMAGE_ID.fullmatch(expected_image_id)):
        raise PublicCPUDecoderError("public CPU-decoder image pin is malformed")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise PublicCPUDecoderError("public CPU-decoder timeout must be between 1 and 3600 seconds")
    started = time.monotonic()
    try:
        inspected = subprocess.run([docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
                                   capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PublicCPUDecoderError("could not inspect pinned CPU-decoder image") from None
    if inspected.returncode or inspected.stdout.strip() != expected_image_id:
        raise PublicCPUDecoderError("public CPU-decoder image ID differs from the frozen pin")
    with tempfile.TemporaryDirectory(prefix="bananloop-cpudecoder-") as tmp_name:
        temp = Path(tmp_name)
        temp.chmod(0o755)
        output = temp / "output"
        output.mkdir(mode=0o755)
        candidate_path = output / "executor.py"
        candidate_path.write_bytes(candidate)
        candidate_path.chmod(0o444)
        # A mounted public controller keeps the image immutable and the scoring process bounded.
        controller = temp / "entry.py"
        controller.write_text(_container_script(), encoding="utf-8")
        controller.chmod(0o444)
        cname = "bananloop-are-cpudecoder-" + uuid4().hex
        remaining = timeout_seconds - int(time.monotonic() - started)
        argv = [docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", cname, "--pids-limit", "96", "--memory", "14g", "--cpus", "8",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g,mode=1777",
                "--mount", f"type=bind,src={output},dst=/app/output,readonly",
                "--mount", f"type=bind,src={controller},dst=/tmp/bananloop-cpudecoder-entry.py,readonly",
                "--entrypoint", "python3", image, "/tmp/bananloop-cpudecoder-entry.py"]
        try:
            stdout = _run_bounded_container(argv, docker_binary=docker_binary,
                                            container_name=cname, timeout_seconds=max(1, remaining))
        except Exception as exc:
            raise PublicCPUDecoderError("public CPU-decoder DEV evaluation failed") from exc
        if stdout is None:
            raise PublicCPUDecoderError("public CPU-decoder container returned an error")
        lines = [line[len(_RESULT_PREFIX):] for line in stdout.splitlines() if line.startswith(_RESULT_PREFIX)]
        if len(lines) != 1:
            raise PublicCPUDecoderError("public benchmark returned no unique aggregate")
        try:
            result = json.loads(lines[0])
            metric = result["metric"]
            instances = result["per_instance"]
            if (not isinstance(metric, (int, float)) or isinstance(metric, bool)
                    or not math.isfinite(metric) or len(instances) != len(PUBLIC_SEEDS)
                    or result.get("n_instances") != len(PUBLIC_SEEDS)
                    or result.get("n_equivalent") != len(PUBLIC_SEEDS)
                    or result.get("failure") not in (None, "")):
                raise ValueError
            if [item["seed"] for item in instances] != list(PUBLIC_SEEDS):
                raise ValueError
            if any(not item["equivalent"] for item in instances):
                raise ValueError
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise PublicCPUDecoderError("public benchmark result is malformed or not equivalent") from None
        return {"status": "GRADED", "metric": float(metric), "cell_count": len(instances),
                "seeds": list(PUBLIC_SEEDS), "elapsed_seconds": round(time.monotonic() - started, 3),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
                "task_sha256": TASK_SHA256, "instruction_sha256": INSTRUCTION_SHA256,
                "equivalent_instances": len(instances), "official_seed_count": 16,
                "pilot_only": True, "per_instance": instances}


def _container_script() -> str:
    # Keep the in-image bridge public and small. No grader endpoint or private files are used.
    return f'''import json, subprocess, sys\n\np = subprocess.run(["python3", "/app/bench.py", "--dir", "/app/output", "--seeds", "{','.join(map(str, PUBLIC_SEEDS))}", "--json", "/tmp/bananloop-cpudecoder-result.json"], capture_output=True, text=True, timeout=570, check=False)\nif p.returncode:\n    sys.stderr.write(p.stderr[-2000:])\n    raise SystemExit(p.returncode)\nwith open("/tmp/bananloop-cpudecoder-result.json", encoding="utf-8") as f:\n    record = json.load(f)\nprint("{_RESULT_PREFIX}" + json.dumps(record, sort_keys=True, separators=(",", ":")))\n'''


def run_public_cpudecoder_dev(candidate: bytes, *, task_root: Path,
                              image: str = DEFAULT_IMAGE,
                              expected_image_id: str = DEFAULT_IMAGE_ID,
                              timeout_seconds: int = 600) -> dict[str, Any]:
    verify_public_inputs(task_root)
    return evaluate_candidate(candidate, image=image, expected_image_id=expected_image_id,
                              timeout_seconds=timeout_seconds)
