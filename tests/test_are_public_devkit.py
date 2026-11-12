from __future__ import annotations

import pytest

from benchmarks.autoresearchexam.public_devkit import PublicDevkitError, run_public_devkit
from benchmarks.autoresearchexam.public_devkit import _run_bounded_container


def test_public_devkit_checks_image_pin_and_returns_only_frozen_aggregate(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:3] == ["image", "inspect"]:
            return type("Result", (), {
                "returncode": 0,
                "stdout": "sha256:" + "a" * 64 + "\n",
                "stderr": "",
            })()
        return type("Result", (), {"returncode": 0, "stdout": "sha256:" + "a" * 64 + "\n"})()

    monkeypatch.setattr("benchmarks.autoresearchexam.public_devkit.subprocess.run", fake_run)
    def fake_container(argv, **kwargs):
        calls.append((argv, kwargs))
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "--read-only" in argv and "--pull=never" in argv
        assert "readonly" in argv[argv.index("--mount") + 1]
        assert argv[argv.index("--memory") + 1] == "1g"
        assert argv[argv.index("--cpus") + 1] == "1"
        assert 1 <= kwargs["timeout_seconds"] <= 120
        return "dev1 0 0.04 0.2\npanel mean over 42 cells: 0.1234\n"
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit._run_bounded_container", fake_container,
    )
    result = run_public_devkit(
        b"def impute(): pass\n", image="are-public:test",
        expected_image_id="sha256:" + "a" * 64, timeout_seconds=120,
    )
    assert result == {"status": "GRADED", "dev_score": 0.1234, "dev_cells": 42}
    assert len(calls) == 2
    assert "private diagnostic must not escape" not in repr(result)


def test_public_devkit_fails_closed_on_image_digest_mismatch(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.subprocess.run",
        lambda *_args, **_kwargs: type("Result", (), {
            "returncode": 0, "stdout": "sha256:" + "b" * 64 + "\n", "stderr": "",
        })(),
    )
    with pytest.raises(PublicDevkitError, match="image ID differs"):
        run_public_devkit(
            b"candidate", image="are-public:test",
            expected_image_id="sha256:" + "a" * 64, timeout_seconds=120,
        )


def test_public_devkit_rejects_missing_or_wrong_panel_aggregate(monkeypatch):
    outputs = iter([
        "panel mean over 41 cells: 0.1\n",
        "panel mean over 42 cells: nan\n",
        "no aggregate\n",
    ])

    def fake_run(argv, **kwargs):
        return type("Result", (), {
            "returncode": 0, "stdout": "sha256:" + "a" * 64 + "\n", "stderr": "",
        })()

    monkeypatch.setattr("benchmarks.autoresearchexam.public_devkit.subprocess.run", fake_run)
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit._run_bounded_container",
        lambda *_args, **_kwargs: next(outputs),
    )
    for _ in range(3):
        with pytest.raises(PublicDevkitError):
            run_public_devkit(
                b"candidate", image="are-public:test",
                expected_image_id="sha256:" + "a" * 64, timeout_seconds=120,
            )


def test_dev_container_timeout_explicitly_kills_container_and_cli(monkeypatch):
    stopped = []

    class FakeStdout:
        def fileno(self):
            return 91

        def close(self):
            pass

    class FakeProcess:
        stdout = FakeStdout()
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.time.monotonic",
        iter([10.0, 12.0]).__next__,
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit._stop_container",
        lambda docker, name: stopped.append((docker, name)),
    )
    with pytest.raises(PublicDevkitError, match="timed out"):
        _run_bounded_container(
            ["docker", "run"], docker_binary="docker", container_name="are-test",
            timeout_seconds=1,
        )
    assert stopped == [("docker", "are-test")]


def test_dev_container_output_is_bounded_and_stopped(monkeypatch):
    stopped = []

    class FakeStdout:
        def fileno(self):
            return 92

        def close(self):
            pass

    class FakeProcess:
        stdout = FakeStdout()
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.time.monotonic", lambda: 10.0,
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.select.select",
        lambda *_args, **_kwargs: ([92], [], []),
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.os.read",
        lambda _fd, size: b"x" * size,
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit._stop_container",
        lambda docker, name: stopped.append((docker, name)),
    )
    with pytest.raises(PublicDevkitError, match="output exceeded"):
        _run_bounded_container(
            ["docker", "run"], docker_binary="docker", container_name="are-test",
            timeout_seconds=30,
        )
    assert stopped == [("docker", "are-test")]


def test_dev_container_nonzero_exit_explicitly_stops_named_container(monkeypatch):
    stopped = []

    class FakeStdout:
        def fileno(self):
            return 93

        def close(self):
            pass

    class FakeProcess:
        stdout = FakeStdout()

        def poll(self):
            return 17

        def wait(self, timeout=None):
            return 17

    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.time.monotonic", lambda: 10.0,
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.select.select",
        lambda *_args, **_kwargs: ([93], [], []),
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit.os.read", lambda *_args: b"",
    )
    monkeypatch.setattr(
        "benchmarks.autoresearchexam.public_devkit._stop_container",
        lambda docker, name: stopped.append((docker, name)),
    )
    assert _run_bounded_container(
        ["docker", "run"], docker_binary="docker", container_name="are-test",
        timeout_seconds=30,
    ) is None
    assert stopped == [("docker", "are-test")]
