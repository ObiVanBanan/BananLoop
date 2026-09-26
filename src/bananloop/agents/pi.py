from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .base import AgentAdapter, AgentError
from ..process import (
    ProcessError, check_process_control, process_group_options,
    run_process, terminate_process_tree,
)


class _JsonlReader:
    def __init__(self, stream):
        self.items: queue.Queue[str | None] = queue.Queue()
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                self.items.put(line.rstrip("\n").rstrip("\r"))
        finally:
            self.items.put(None)


class PiAdapter(AgentAdapter):
    """Pi Coding Agent adapter using the official RPC JSONL protocol."""

    def __init__(
        self,
        model: str | None = None,
        extra_args: list[str] | None = None,
        command: list[str] | None = None,
    ):
        self.model = model
        self.extra_args = extra_args or []
        self.command = command or ["pi"]
        self._active: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    def _resolved_command(self) -> list[str]:
        executable = self.command[0]
        if Path(executable).is_absolute():
            resolved = executable if Path(executable).exists() else None
        else:
            resolved = shutil.which(executable)
        if not resolved:
            raise AgentError(f"Pi executable not found: {executable!r}")

        suffix = Path(resolved).suffix.lower()
        tail = self.command[1:]
        if os.name == "nt" and suffix in {".cmd", ".bat"}:
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            return [comspec, "/d", "/s", "/c", resolved, *tail]
        if os.name == "nt" and suffix == ".ps1":
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved, *tail]
        return [resolved, *tail]

    def probe(self) -> dict[str, Any]:
        try:
            resolved_command = self._resolved_command()
        except AgentError:
            return {"available": False, "kind": "pi", "command": self.command}
        version = "unknown"
        try:
            version = run_process([*resolved_command, "--version"], cwd=Path.cwd(), timeout=10).stdout.strip()
        except ProcessError:
            pass
        return {
            "available": True,
            "kind": "pi",
            "command": self.command,
            "resolved_command": resolved_command,
            "version": version,
            "model": self.model,
        }

    @staticmethod
    def _action_id(task: dict[str, Any]) -> str:
        experiment_id = task.get("experiment_id", "unknown")
        role = task.get("role", "unknown")
        return f"exp:{experiment_id}:{role}"

    def _argv(self) -> list[str]:
        argv = [*self._resolved_command(), "--mode", "rpc", "--no-session", "--no-approve"]
        if self.model:
            argv += ["--model", self.model]
        argv += self.extra_args
        return argv

    @staticmethod
    def _prompt(task: dict[str, Any]) -> str:
        role = task.get("role")
        if role == "propose":
            instruction = (
                "You are the proposer inside BananLoop. Inspect the repository and propose exactly one "
                "bounded, falsifiable hypothesis. Do not modify files during this role. "
                "Use the supplied research_state and planning_mode. Avoid repeating a mechanism unless "
                "you explicitly mark it as refine, retry, or supersede and reference the prior experiment ordinal. "
                "Your final assistant message MUST be exactly one JSON object with these keys: "
                "hypothesis, rationale, expected_effect, files_or_area, falsification_condition, mechanism_key, "
                "relation, prior_experiment_refs, novelty_rationale. "
                "If no useful hypothesis exists, return exactly {\"status\":\"NO_HYPOTHESIS\"}. "
                "Do not use markdown fences and do not include prose outside the JSON object."
            )
        elif role == "analyze":
            instruction = (
                "You are the DEV failure analyst inside BananLoop. Analyze the supplied DEV failure signals, "
                "public DEV teacher examples, and the current candidate repository. Do not modify files. "
                "Use the exact public DEV expected answers to diagnose the root cause, then distill causal, "
                "reusable lessons rather than memorizing benchmark cases. Do not copy case IDs, product IDs, "
                "exact expected answers, or case-specific constants into the resulting capsules. Focus on stage attribution, "
                "state/provenance conflicts, violated invariants, and generic regression tests. If evidence is "
                "insufficient, lower confidence instead of inventing a cause. Your final assistant message MUST "
                "be exactly one JSON object with key capsules. Each capsule must contain title, failure_class, "
                "stage, affected_fields, observation, likely_mechanism, general_rule, suggested_regression, "
                "confidence. Return {\"capsules\":[]} if no safe general lesson can be supported. "
                "Do not use markdown fences and do not include prose outside the JSON object."
            )
        elif role == "implement":
            instruction = (
                "You are the implementer inside BananLoop. Implement exactly the approved hypothesis in the "
                "current managed workspace. You may inspect the repository and use coding tools. "
                "Modify only editable_scope and never modify protected_paths. Do not decide whether the change "
                "is better; BananLoop evaluates it independently. Your final assistant message MUST be exactly "
                "one JSON object with these keys: summary, changed_paths, verification_notes, blockers. "
                "changed_paths and blockers MUST be JSON arrays of strings. If there are no blockers, use "
                "\"blockers\": [] and never use strings such as \"None\" or \"null\". "
                "Do not use markdown fences and do not include prose outside the JSON object."
            )
        else:
            raise AgentError(f"unsupported Pi role: {role!r}")
        return instruction + "\n\nBANANLOOP TASK:\n" + json.dumps(task, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_single_json_object(text: str) -> dict[str, Any]:
        spans: list[str] = []
        start: int | None = None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif char == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append(text[start:index + 1])
                    start = None

        objects: list[dict[str, Any]] = []
        for span in spans:
            try:
                value = json.loads(span)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                objects.append(value)

        if len(objects) != 1:
            raise AgentError(
                "Pi final response does not contain exactly one unambiguous JSON object"
            )
        return objects[0]

    @staticmethod
    def _parse_final_json(text: str) -> dict[str, Any]:
        candidate = text.strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            if "</think>" in candidate:
                candidate = candidate.rsplit("</think>", 1)[1].strip()
            data = PiAdapter._extract_single_json_object(candidate)
        if not isinstance(data, dict):
            raise AgentError("Pi final response must be a JSON object")
        return data

    @staticmethod
    def _repair_prompt(task: dict[str, Any]) -> str:
        role = task.get("role")
        if role == "propose":
            schema = (
                '{"hypothesis":"...","rationale":"...","expected_effect":"...",'
                '"files_or_area":["..."],"falsification_condition":"...","mechanism_key":"...",'
                '"relation":"new","prior_experiment_refs":[],"novelty_rationale":"..."}'
            )
        elif role == "analyze":
            schema = (
                '{"capsules":[{"title":"...","failure_class":"...","stage":"...",'
                '"affected_fields":["..."],"observation":"...","likely_mechanism":"...",'
                '"general_rule":"...","suggested_regression":"...","confidence":0.8}]}'
            )
        elif role == "implement":
            schema = (
                '{"summary":"...","changed_paths":["..."],"verification_notes":"...",'
                '"blockers":[]}'
            )
        else:
            raise AgentError(f"unsupported Pi role for JSON repair: {role!r}")
        return (
            "BANANLOOP FORMAT REPAIR. Your previous final message was not valid protocol JSON. "
            "Do not redo the analysis, do not inspect more files, and do not modify files. "
            "Only convert the conclusion you already reached into exactly one strict JSON object. "
            f"Required shape: {schema} "
            "Return JSON only: no markdown fence, no commentary, no <think> text."
        )

    @staticmethod
    def normalize_result(task: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """Normalize harmless weak-model empty-value variants without repairing semantic errors."""
        normalized = dict(data)
        role = task.get("role")
        if role == "propose":
            files_or_area = normalized.get("files_or_area")
            if isinstance(files_or_area, str) and files_or_area.strip():
                normalized["files_or_area"] = [files_or_area.strip()]
        if role == "implement":
            changed_paths = normalized.get("changed_paths")
            if isinstance(changed_paths, str) and changed_paths.strip():
                normalized["changed_paths"] = [changed_paths.strip()]
            blockers = normalized.get("blockers")
            if blockers is None:
                normalized["blockers"] = []
            elif isinstance(blockers, str) and blockers.strip().lower() in {
                "",
                "none",
                "null",
                "n/a",
                "no blockers",
                "no blocker",
            }:
                normalized["blockers"] = []
            if normalized.get("verification_notes") is None:
                normalized["verification_notes"] = ""
        return normalized

    @staticmethod
    def _stderr_reader(stream, sink: list[str]) -> None:
        for line in iter(stream.readline, ""):
            sink.append(line.rstrip())
            if len(sink) > 100:
                del sink[:-100]

    @staticmethod
    def _send(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
        if proc.stdin is None:
            raise AgentError("Pi RPC stdin is unavailable")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    @staticmethod
    def _terminate(proc: subprocess.Popen[str]) -> None:
        terminate_process_tree(proc)

    def _next_event(
        self,
        proc: subprocess.Popen[str],
        reader: _JsonlReader,
        deadline: float,
        stderr_lines: list[str],
    ) -> dict[str, Any]:
        while True:
            check_process_control()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(proc)
                raise AgentError("Pi RPC timed out")
            try:
                line = reader.items.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if proc.poll() is not None:
                    tail = "\n".join(stderr_lines[-20:])
                    raise AgentError(f"Pi RPC exited with code {proc.returncode}: {tail}")
                continue
            if line is None:
                tail = "\n".join(stderr_lines[-20:])
                raise AgentError(f"Pi RPC stdout closed unexpectedly: {tail}")
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AgentError(f"Pi RPC emitted non-JSON line: {line[:500]!r}") from exc
            if not isinstance(event, dict):
                raise AgentError("Pi RPC event must be a JSON object")
            if event.get("type") == "extension_ui_request":
                request_id = event.get("id")
                if request_id:
                    self._send(proc, {"type": "extension_ui_response", "id": request_id, "cancelled": True})
                continue
            return event

    def _wait_response(
        self,
        proc: subprocess.Popen[str],
        reader: _JsonlReader,
        deadline: float,
        stderr_lines: list[str],
        request_id: str,
    ) -> dict[str, Any]:
        while True:
            event = self._next_event(proc, reader, deadline, stderr_lines)
            if event.get("type") == "response" and event.get("id") == request_id:
                if not event.get("success", False):
                    raise AgentError(f"Pi RPC command failed: {event.get('error', 'unknown error')}")
                return event

    def _wait_prompt_settled(
        self,
        proc: subprocess.Popen[str],
        reader: _JsonlReader,
        deadline: float,
        stderr_lines: list[str],
        prompt_id: str,
    ) -> None:
        prompt_accepted = False
        settled = False
        while not settled:
            event = self._next_event(proc, reader, deadline, stderr_lines)
            if event.get("type") == "response" and event.get("id") == prompt_id:
                if not event.get("success", False):
                    raise AgentError(f"Pi rejected prompt: {event.get('error', 'unknown error')}")
                prompt_accepted = True
            elif event.get("type") == "agent_settled":
                settled = True
        if not prompt_accepted:
            raise AgentError("Pi settled without acknowledging the prompt")

    def _last_assistant_text(
        self,
        proc: subprocess.Popen[str],
        reader: _JsonlReader,
        deadline: float,
        stderr_lines: list[str],
        request_id: str,
    ) -> str:
        self._send(proc, {"id": request_id, "type": "get_last_assistant_text"})
        response = self._wait_response(proc, reader, deadline, stderr_lines, request_id)
        text = ((response.get("data") or {}).get("text"))
        if not isinstance(text, str) or not text.strip():
            raise AgentError("Pi completed without a final assistant text result")
        return text

    def execute(self, task: dict[str, Any], workspace: Path, timeout: int) -> dict[str, Any]:
        check_process_control()
        argv = self._argv()
        action_id = self._action_id(task)
        stderr_lines: list[str] = []
        try:
            proc = subprocess.Popen(
                argv,
                cwd=workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **process_group_options(),
            )
        except OSError as exc:
            raise AgentError(f"cannot start Pi RPC: {argv!r}: {exc}") from exc

        if proc.stdout is None or proc.stderr is None:
            self._terminate(proc)
            raise AgentError("Pi RPC pipes are unavailable")

        with self._lock:
            self._active[action_id] = proc

        reader = _JsonlReader(proc.stdout)
        stderr_thread = threading.Thread(
            target=self._stderr_reader,
            args=(proc.stderr, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()
        deadline = time.monotonic() + timeout

        try:
            prompt_id = f"{action_id}:prompt"
            self._send(proc, {"id": prompt_id, "type": "prompt", "message": self._prompt(task)})
            self._wait_prompt_settled(proc, reader, deadline, stderr_lines, prompt_id)

            result_id = f"{action_id}:result"
            text = self._last_assistant_text(proc, reader, deadline, stderr_lines, result_id)
            try:
                data = self._parse_final_json(text)
            except AgentError as first_error:
                repair_id = f"{action_id}:json-repair"
                self._send(
                    proc,
                    {
                        "id": repair_id,
                        "type": "prompt",
                        "message": self._repair_prompt(task),
                    },
                )
                self._wait_prompt_settled(proc, reader, deadline, stderr_lines, repair_id)
                repair_text = self._last_assistant_text(
                    proc,
                    reader,
                    deadline,
                    stderr_lines,
                    f"{action_id}:repair-result",
                )
                try:
                    data = self._parse_final_json(repair_text)
                except AgentError as repair_error:
                    raise AgentError(
                        f"{first_error}; Pi JSON repair also failed: {repair_error}"
                    ) from repair_error
            return self.normalize_result(task, data)
        finally:
            with self._lock:
                self._active.pop(action_id, None)
            self._terminate(proc)

    def cancel(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            proc = self._active.get(action_id)
        if proc is None or proc.poll() is not None:
            return {"action_id": action_id, "status": "not_running"}
        try:
            self._send(proc, {"id": f"{action_id}:abort", "type": "abort"})
        except AgentError:
            self._terminate(proc)
            return {"action_id": action_id, "status": "terminated"}
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
        return {"action_id": action_id, "status": "cancelled"}
