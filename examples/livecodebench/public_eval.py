"""Public stdin/stdout smoke evaluator. NOT an official LiveCodeBench judge.

This program runs agent-authored code; use only in a disposable restricted VM.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def normalize(value: str) -> list[str]:
    """Loose whitespace token matching for a DEV proxy, not contest judging."""
    return value.split()


def main() -> None:
    root = Path(__file__).resolve().parent
    data = (root / "public_cases.json").read_bytes()
    samples = json.loads(data)
    if not isinstance(samples, list) or not samples:
        raise ValueError("public tests missing")
    correct = 0
    # Fixed upper bound; materializer allows at most 12 samples.
    for item in samples[:12]:
        try:
            run = subprocess.run(
                [sys.executable, str(root / "solution.py")],
                input=item["input"], text=True, encoding="utf-8",
                capture_output=True, timeout=5, cwd=root,
            )
            if run.returncode == 0 and normalize(run.stdout) == normalize(item["output"]):
                correct += 1
        except (subprocess.TimeoutExpired, OSError):
            pass
    score = correct / min(len(samples), 12)
    digest = hashlib.sha256(b"lcb-public-stdin-v1\0" + data).hexdigest()
    print(json.dumps({
        "status": "ok",
        "metrics": {"public_pass_rate": score},
        "checks": {"sample_suite": True},
        "protocol_sha256": digest,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
