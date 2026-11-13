"""Public-only bounded DEV pilot for CPU LLM decode throughput.

The official grader uses the median of three held-out 128-request round
speedups. This pilot uses 16 requests from each of two fixed public draws,
reports their median, and labels the shortened score diagnostic only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .public_devkit import _run_bounded_container

TASK_ID = "cpu-llm-decode-throughput"
TASK_VERSION = "1.1.0"
TASK_SHA256 = "fdc497deb6874fb72c2549a272c6cf866d3e6f29789be2b5c74a0df475b8042e"
INSTRUCTION_SHA256 = "e3150dff47a1ac0e18360f59fc62bbb495ec94011806f277656799d4478ab4e1"
TASK_DOCKERFILE_SHA256 = "32e1ba814715ba1515f9b8c2456ba772c970b3eadd137e0530dcd1182d56cb98"
TASK_DOCKERIGNORE_SHA256 = "172c9772dd4171f99e71b3648c2da214e5e24c74d8b8166159cddfca1acb64bb"
TASK_REQUIREMENTS_SHA256 = "a71fb82e7c13bf3a11d80d857efddd1fccb2315b3af33be9976896bea04fbbfd"
PUBLIC_BENCH_SHA256 = "512eb2fc6c4d3b81795a110202879846774bbafeaae1e0f6832eaeabe0964ade"
PUBLIC_METRIC_SHA256 = "824b135cb3d76dc81cb0dbe1196b8256b64ad52fcddbd04f6708dcf34f4ebfc3"
PUBLIC_RUNNER_SHA256 = "7422ce9035e23b50479a7e994fcdbcb74de8361f20438625bdee4e2a480909a7"
PUBLIC_REFERENCE_SHA256 = "24318cd300495f11acffd4720daf3273ec46526081d6bc47f4e14b451127a247"
DEV_WORKLOAD_SHA256 = {
    0: "ac49e4d2487a2b8fec831099ecc507d5673257143695563d3ef637f346505f0c",
    1: "9b090a1c1aedeaebcd88cdb60fdaa2626ef10a223457953dca99fe4c9048d586",
    2: "b9c78e39177809a02c63c1d5a71c514a9499bf7b4ddcf9219368bb729687288d",
    3: "0b6cf8ab86e284141bf1e5acee70aaa8c3dc5e41b6229139fd451b48eab5895e",
}
WARMUP_SHA256 = "dcf97dd744cbeb24dad3256f79c5576ab3c9f4750637ba76c57dbb75db9e0a8d"
SELECTION_SHA256 = "071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316"
MODEL_CONFIG_SHA256 = "0daed7749b4f02b8f76240d5444551d7b08712dab4d0adb8239c56ba823bb7b4"
MODEL_WEIGHTS_SHA256 = "248dfc3911869ec493c76e65bf2fcf7f615828b0254c12b473182f0f81d3a707"
DEFAULT_IMAGE = "bananloop-are-cpullm-public-dev:1.1.0"
DEFAULT_IMAGE_ID = "sha256:682b01ecc8b472b9cf422bf2732d6f30a08c9a6fbc7d75703a5e4b7028c02ddd"
PILOT_DRAWS = (0, 2)
PILOT_REQUEST_LIMIT = 16
MAX_CANDIDATE_BYTES = 4 * 1024 * 1024
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RESULT_PREFIX = "BANANLOOP_CPULLM_DEV="
AGREEMENT_RE = re.compile(r"(?m)^token agreement\s*:\s*([0-9.]+)")
SPEEDUP_RE = re.compile(r"(?m)^speedup S\s*:\s*([0-9.]+)")
ROUND_RE = re.compile(
    r"(?m)^\s*round\s+(?P<round>\d+)\s+\((?P<workload>dev_workload_\d+)\):\s+"
    r"reference\s+(?P<ref>[0-9.]+)\s+tok/s,\s+candidate\s+(?P<cand>[0-9.]+)\s+tok/s,\s+"
    r"speedup\s+(?P<speed>[0-9.]+)x,\s+agreement\s+(?P<matched>\d+)/(?P<total>\d+)"
)

_PUBLIC_FILES = {
    "task.toml": ("task.toml", TASK_SHA256),
    "instruction.md": ("instruction.md", INSTRUCTION_SHA256),
    "environment/Dockerfile": ("environment/Dockerfile", TASK_DOCKERFILE_SHA256),
    "environment/.dockerignore": ("environment/.dockerignore", TASK_DOCKERIGNORE_SHA256),
    "environment/requirements.txt": ("environment/requirements.txt", TASK_REQUIREMENTS_SHA256),
    "environment/app/harness/bench.py": ("environment/app/harness/bench.py", PUBLIC_BENCH_SHA256),
    "environment/app/harness/metric_core.py": ("environment/app/harness/metric_core.py", PUBLIC_METRIC_SHA256),
    "environment/app/harness/runner.py": ("environment/app/harness/runner.py", PUBLIC_RUNNER_SHA256),
    "environment/app/reference/reference_engine.py": ("environment/app/reference/reference_engine.py", PUBLIC_REFERENCE_SHA256),
    **{f"environment/app/data/dev_workload_{i}.json":
       (f"environment/app/data/dev_workload_{i}.json", digest)
       for i, digest in DEV_WORKLOAD_SHA256.items()},
    "environment/app/data/warmup_workload.json":
        ("environment/app/data/warmup_workload.json", WARMUP_SHA256),
}


class PublicCPULLMError(RuntimeError):
    """The public CPU LLM task inputs or benchmark result failed validation."""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_public_inputs(task_root: Path) -> dict[str, Any]:
    root = task_root.expanduser()
    if root.is_symlink() or not root.is_dir() or root.name != TASK_ID:
        raise PublicCPULLMError("public CPU LLM task root is missing or unsafe")
    root = root.resolve()
    for rel, expected in _PUBLIC_FILES.values():
        path = root / rel
        if path.is_symlink() or not path.is_file() or _sha(path) != expected:
            raise PublicCPULLMError("public CPU LLM source differs from its frozen digest")
    if (root / "environment/.dockerignore").read_text(encoding="utf-8").strip() != "are-grader-ctx":
        raise PublicCPULLMError("public Docker context does not exclude verifier files")
    selection_path = Path(__file__).with_name("task-selection.json")
    if selection_path.is_symlink() or _sha(selection_path) != SELECTION_SHA256:
        raise PublicCPULLMError("frozen AutoResearchExam task selection changed")
    try:
        import tomllib
        manifest = tomllib.loads((root / "task.toml").read_text(encoding="utf-8"))
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        tasks = [x for x in selection["tasks"] if x.get("task_id") == TASK_ID]
    except (OSError, ValueError, KeyError, TypeError):
        raise PublicCPULLMError("public CPU LLM task manifest is malformed") from None
    if (manifest.get("task", {}).get("version") != TASK_VERSION or len(tasks) != 1
            or tasks[0].get("version") != TASK_VERSION or tasks[0].get("task_sha256") != TASK_SHA256
            or tasks[0].get("instruction_sha256") != INSTRUCTION_SHA256
            or tasks[0].get("resources", {}).get("cpus") != 8
            or tasks[0].get("resources", {}).get("memory_mb") != 14336):
        raise PublicCPULLMError("CPU LLM task identity or resource envelope differs from selection pin")
    return {"task_sha256": TASK_SHA256, "instruction_sha256": INSTRUCTION_SHA256,
            "dev_draws": list(PILOT_DRAWS), "request_limit": PILOT_REQUEST_LIMIT,
            "public_files": len(_PUBLIC_FILES)}


def evaluate_candidate(candidate: bytes, *, image: str = DEFAULT_IMAGE,
                       expected_image_id: str = DEFAULT_IMAGE_ID,
                       timeout_seconds: int = 600, docker_binary: str = "docker") -> dict[str, Any]:
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > MAX_CANDIDATE_BYTES:
        raise PublicCPULLMError("candidate engine is empty or oversized")
    if (not isinstance(image, str) or not image or image.startswith("-")
            or any(ch.isspace() for ch in image) or not IMAGE_ID_RE.fullmatch(expected_image_id)):
        raise PublicCPULLMError("public CPU LLM image pin is malformed")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise PublicCPULLMError("CPU LLM DEV timeout must be 1 to 3600 seconds")
    started = time.monotonic()
    try:
        inspected = subprocess.run([docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
                                   capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PublicCPULLMError("could not inspect pinned CPU LLM public image") from None
    if inspected.returncode or inspected.stdout.strip() != expected_image_id:
        raise PublicCPULLMError("CPU LLM image ID differs from the frozen pin")
    with tempfile.TemporaryDirectory(prefix="bananloop-cpullm-") as name:
        temp = Path(name)
        temp.chmod(0o755)
        output = temp / "output"
        output.mkdir(mode=0o755)
        path = output / "engine.py"
        path.write_bytes(candidate)
        path.chmod(0o444)
        container = "bananloop-are-cpullm-" + uuid4().hex
        remaining = max(1, timeout_seconds - int(time.monotonic() - started))
        argv = [docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", container, "--pids-limit", "128", "--memory", "14g", "--cpus", "8",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g,mode=1777",
                "--mount", f"type=bind,src={output},dst=/app/output,readonly",
                "--entrypoint", "python3", image,
                "-c", ("import hashlib,runpy,sys; "
                       "pins={'/opt/model/config.json':'" + MODEL_CONFIG_SHA256 + "',"
                       "'/opt/model/model.safetensors':'" + MODEL_WEIGHTS_SHA256 + "'}; "
                       "[(None if hashlib.file_digest(open(p,'rb'),'sha256').hexdigest()==h else "
                       "(_ for _ in ()).throw(SystemExit('pinned model asset mismatch'))) "
                       "for p,h in pins.items()]; "
                       "sys.argv=['/app/harness/bench.py','--draws','" + ",".join(map(str, PILOT_DRAWS)) +
                       "','--limit','" + str(PILOT_REQUEST_LIMIT) + "']; "
                       "runpy.run_path('/app/harness/bench.py',run_name='__main__')")]
        stdout = _run_bounded_container(argv, docker_binary=docker_binary,
                                        container_name=container, timeout_seconds=remaining)
        if stdout is None:
            raise PublicCPULLMError("CPU LLM public benchmark container returned an error")
        try:
            agreement_match = AGREEMENT_RE.search(stdout)
            speed_match = SPEEDUP_RE.search(stdout)
            rounds = list(ROUND_RE.finditer(stdout))
            agreement = float(agreement_match.group(1)) if agreement_match else None
            speedup = float(speed_match.group(1)) if speed_match else None
            if (agreement is None or speedup is None or not math.isfinite(agreement)
                    or not math.isfinite(speedup) or agreement < 0.99 or speedup <= 0
                    or len(rounds) != len(PILOT_DRAWS)):
                raise ValueError
            per_draw = []
            for expected_index, match in zip(PILOT_DRAWS, rounds):
                if match.group("workload") != f"dev_workload_{expected_index}":
                    raise ValueError
                total, matched = int(match.group("total")), int(match.group("matched"))
                ratio = matched / total if total else 0.0
                if (total <= 0 or ratio < 0.99 or not math.isfinite(float(match.group("speed")))):
                    raise ValueError
                per_draw.append({"draw": expected_index, "speedup": float(match.group("speed")),
                                 "matched_tokens": matched, "total_tokens": total,
                                 "agreement": ratio})
        except (ValueError, TypeError):
            raise PublicCPULLMError("CPU LLM public benchmark aggregate is invalid or below token-agreement threshold") from None
        return {"status": "GRADED", "metric": speedup, "token_agreement": agreement,
                "aggregation": "MEDIAN",
                "cell_count": len(PILOT_DRAWS), "draws": list(PILOT_DRAWS),
                "request_limit": PILOT_REQUEST_LIMIT, "per_draw": per_draw,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
                "task_sha256": TASK_SHA256, "instruction_sha256": INSTRUCTION_SHA256,
                "official_draws": 3, "official_requests_per_draw": 128, "pilot_only": True}


def run_public_cpullm_dev(candidate: bytes, *, task_root: Path,
                          image: str = DEFAULT_IMAGE,
                          expected_image_id: str = DEFAULT_IMAGE_ID,
                          timeout_seconds: int = 600) -> dict[str, Any]:
    verify_public_inputs(task_root)
    return evaluate_candidate(candidate, image=image, expected_image_id=expected_image_id,
                              timeout_seconds=timeout_seconds)
