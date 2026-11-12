from __future__ import annotations

import subprocess

import pytest

from benchmarks.autoresearchexam import public_sopcc_dev


IMAGE = "bananloop-are-sopcc-public-dev:2026-11-12"
IMAGE_ID = "sha256:" + "a" * 64


def _stdout(*, count: int = 1, episodes: int = 4, total: int = 4) -> str:
    return (
        "deliverable: /app/solution (830 bytes)\n\n"
        f"{count} instances x {episodes} episodes = {total} episodes\n"
        "pooled metric 3.3136  (R=3.3136, F=0.0000, per-episode delivered sd=2.346)\n"
    )


def _patch_docker(monkeypatch, stdout: str) -> None:
    monkeypatch.setattr(
        public_sopcc_dev.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, IMAGE_ID + "\n", ""),
    )
    monkeypatch.setattr(public_sopcc_dev, "_run_bounded_container", lambda *args, **kwargs: stdout)


def test_public_sopcc_runner_returns_only_frozen_aggregate(monkeypatch):
    _patch_docker(monkeypatch, _stdout())
    result = public_sopcc_dev.run_public_sopcc_dev(
        b"class Policy: pass\n", image=IMAGE, expected_image_id=IMAGE_ID,
        timeout_seconds=60,
    )
    assert result == {
        "status": "GRADED", "metric": 3.3136, "mean_delivered_reward": 3.3136,
        "failure_rate": 0.0, "delivered_reward_sd": 2.346, "draws": [0],
        "instances_per_draw": 1, "episodes_per_instance": 4, "episodes": 4,
    }


def test_public_sopcc_runner_rejects_panel_mismatch(monkeypatch):
    _patch_docker(monkeypatch, _stdout(total=8))
    with pytest.raises(public_sopcc_dev.PublicSOPCCError, match="frozen panel"):
        public_sopcc_dev.run_public_sopcc_dev(
            b"candidate", image=IMAGE, expected_image_id=IMAGE_ID, timeout_seconds=60,
        )


def test_public_sopcc_runner_refuses_unpinned_image_before_running(monkeypatch):
    calls = []
    def inspect(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args[0], 0, "sha256:" + "b" * 64 + "\n", "")
    monkeypatch.setattr(public_sopcc_dev.subprocess, "run", inspect)
    with pytest.raises(public_sopcc_dev.PublicSOPCCError, match="image ID differs"):
        public_sopcc_dev.run_public_sopcc_dev(
            b"candidate", image=IMAGE, expected_image_id=IMAGE_ID,
            timeout_seconds=60,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("kwargs", [
    {"draws": ()}, {"draws": (0, 0)}, {"draws": (4,)},
    {"instances_per_draw": 51}, {"episodes_per_instance": 33},
    {"workers": 0},
])
def test_public_sopcc_runner_rejects_invalid_panel_before_docker(monkeypatch, kwargs):
    monkeypatch.setattr(public_sopcc_dev.subprocess, "run", lambda *a, **k: pytest.fail("Docker called"))
    with pytest.raises(public_sopcc_dev.PublicSOPCCError):
        public_sopcc_dev.run_public_sopcc_dev(
            b"candidate", image=IMAGE, expected_image_id=IMAGE_ID,
            timeout_seconds=60, **kwargs,
        )

