from __future__ import annotations

import subprocess

from benchmarks.autonomous_v01.harbor_cleanup import cleanup_compose_project

PROJECT = "trial-a17"
CID = "a" * 64
CID_2 = "b" * 64
CID_NEW = "c" * 64
LABEL = "com.docker.compose.project"


class DockerMock:
    def __init__(self, *, label=PROJECT, failure=None, remains=False, lowercase_absence=False):
        self.label = label
        self.failure = failure
        self.remains = remains
        self.lowercase_absence = lowercase_absence
        self.calls = []
        self.removed = []
        self.list_outputs = None
        self.inspect_count = 0

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        args = argv[1:]
        if self.failure == "list" and args[:2] == ["ps", "-aq"]:
            return self.result(1, stderr="daemon unavailable")
        if args[:2] == ["ps", "-aq"]:
            if self.list_outputs is not None:
                output = self.list_outputs.pop(0) if self.list_outputs else ""
                return self.result(0, stdout=output)
            if self.removed and not self.remains:
                return self.result(0)
            return self.result(0, stdout=f"{CID}\n")
        if args[0] == "inspect" and "--format" in args:
            self.inspect_count += 1
            if self.failure == "inspect":
                return self.result(1, stderr="daemon unavailable")
            if self.failure == "second_inspect" and self.inspect_count == 2:
                return self.result(1, stderr="daemon unavailable")
            return self.result(0, stdout=f"{self.label}\n")
        if args[:2] == ["rm", "-f"]:
            self.removed.append(args[2])
            if self.failure == "remove":
                return self.result(1, stderr="remove failed")
            return self.result(0)
        if args[0] == "inspect":
            if self.remains:
                return self.result(0, stdout=CID)
            if self.lowercase_absence:
                return self.result(1, stderr=f"error: no such object: {args[1]}")
            return self.result(1, stderr=f"Error: No such object: {args[1]}")
        raise AssertionError(f"unexpected docker command: {argv}")

    @staticmethod
    def result(returncode, stdout="", stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_rejects_wrong_label_without_removing_any_container():
    docker = DockerMock(label="some-other-project")

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result["status"] == "UNKNOWN"
    assert docker.removed == []
    assert not any(call[1:3] == ["rm", "-f"] for call in docker.calls)


def test_empty_and_invalid_project_ids_fail_closed_without_docker_calls():
    for project_id in ("", "../unsafe", "UPPER", "has spaces"):
        docker = DockerMock()
        result = cleanup_compose_project(project_id, run=docker)
        assert result["status"] == "UNKNOWN"
        assert docker.calls == []


def test_command_failure_is_unknown_and_does_not_remove():
    docker = DockerMock(failure="list")

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result["status"] == "UNKNOWN"
    assert docker.removed == []


def test_removes_exact_project_container_and_verifies_absence():
    docker = DockerMock()

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result == {"status": "CLEANED", "removed": [CID]}
    assert docker.removed == [CID]
    assert ["docker", "rm", "-f", CID] in docker.calls
    assert ["docker", "inspect", CID] in docker.calls


def test_accepts_exact_lowercase_docker_no_such_object_message():
    docker = DockerMock(lowercase_absence=True)

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result == {"status": "CLEANED", "removed": [CID]}


def test_never_deletes_unrelated_container_even_if_it_appears_in_list():
    docker = DockerMock(label="unrelated")

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result["status"] == "UNKNOWN"
    assert docker.removed == []


def test_failure_to_confirm_absence_returns_unknown():
    docker = DockerMock(remains=True)

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result["status"] == "UNKNOWN"
    assert result["removed"] == []


def test_partial_failure_preserves_confirmed_removed_ids():
    docker = DockerMock(failure="second_inspect")
    docker.list_outputs = [f"{CID}\n{CID_2}\n", ""]

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result["status"] == "UNKNOWN"
    assert result["removed"] == [CID]
    assert docker.removed == [CID]


def test_new_container_after_initial_list_makes_result_unknown():
    docker = DockerMock()
    docker.list_outputs = [f"{CID}\n", f"{CID_NEW}\n"]

    result = cleanup_compose_project(PROJECT, run=docker)

    assert result["status"] == "UNKNOWN"
    assert result["removed"] == [CID]
    assert docker.removed == [CID]
