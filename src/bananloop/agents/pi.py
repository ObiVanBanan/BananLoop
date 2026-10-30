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
        provider: str | None = None,
        reasoning_effort: str | None = None,
    ):
        self.model = model
        self.extra_args = extra_args or []
        self.command = command or ["pi"]
        self.provider = provider
        self.reasoning_effort = reasoning_effort
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
            "provider": self.provider,
            "reasoning_effort": self.reasoning_effort,
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
        if self.provider and "--provider" not in self.extra_args:
            argv += ["--provider", self.provider]
        if self.reasoning_effort and "--thinking" not in self.extra_args:
            argv += ["--thinking", self.reasoning_effort]
        argv += self.extra_args
        return argv

    @staticmethod
    def _prompt(task: dict[str, Any]) -> str:
        role = task.get("role")
        if role == "bootstrap":
            instruction = (
                "You are the campaign bootstrapper inside BananLoop. Inspect the repository and human goal and "
                "build an executable measurement contract. The top-level goal field is authoritative human intent. "
                "Never request a repository-side goal/fix/brief file just to confirm that intent; do not require "
                ".hermes/fix.md when goal is present. If evaluators conflict, choose the one whose semantics best "
                "match the explicit goal. Use repository.detected_python_runtimes and probe those executables before "
                "claiming that Python or a required package environment is missing. You may create or modify files only under "
                ".bananloop_eval/. Never modify production code, tests, reference assets, or user configuration. "
                "Prefer an existing trustworthy evaluator; otherwise create a thin evaluator adapter. Freeze cases "
                "and randomness where possible, avoid per-evaluation LLM planning, and use paired case-level "
                "measurements for stochastic outputs. The core runs three A/A smoke measurements on one snapshot, "
                "requires stable case/input and protocol identity, and compares empirical noise with min_improvement; "
                "emit a stable protocol_sha256 in every evaluation result. "
                "For each fixed-input evaluator tier, provide a cheap protocol_manifest_commands entry that emits "
                "the full protocol_sha256 and case_id/repetition/input_sha256 list without scoring. The full evaluator "
                "must read BANANLOOP_PROTOCOL_MANIFEST and emit exactly those identities; smoke may score a subset. "
                "Keep DEV, VALIDATION, and HOLDOUT manifests separate. Fixed inputs alone do not justify paired statistical acceptance. "
                "If implementation should learn from discovered training resources, list only those paths in "
                "agent_visible_training_paths; never include evaluation, test, validation, holdout, GOLD, or reference resources. "
                "If the operator explicitly requests a discovered read-only project resource as context (such as a catalog), "
                "list it in agent_visible_context_paths, not agent_visible_training_paths. Never include queries, labels, "
                "answers, expected outputs, or evaluation/reference resources there. Operator-hidden paths stay hidden. "
                "Also include signal_sanity. Configure it only from defensible repository-backed positive and negative "
                "case IDs or controls, using evidence_kind known_examples, controlled_degradation, metamorphic, or "
                "invariants, and the primary metric. Never invent labels or derive labels from the evaluator's own "
                "scores. If evidence is absent, mark status unavailable, leave evidence_kind/metric null and case lists "
                "empty, and state the reason; this must not claim scorer validity. The core independently checks "
                "The core checks directed separation in every A/A smoke run. Do not invent missing references or "
                "credentials; if essential inputs are missing, set status to needs_input and list exactly what the operator must provide. "
                "Your final assistant message MUST be exactly one JSON object with keys: status, summary, "
                "project_context, needs_input, evaluation_command, smoke_command, protocol_manifest_commands, agent_visible_training_paths, agent_visible_context_paths, validation_command, holdout_command, "
                "primary_metric, guardrails, non_regression, mandatory_checks, editable_scope, protected_paths, "
                "agent_visible_metrics, agent_visible_diagnostics, paired_comparison, signal_sanity, evaluator_timeout_seconds. "
                "primary_metric must contain name, direction, target, min_improvement. paired_comparison must "
                "contain enabled, case_count, repetitions, metric, direction, min_wins, worst_case_tolerance. "
                "Do not use markdown fences or prose outside the JSON object."
            )
        elif role == "propose":
            instruction = (
                "You are the proposer inside BananLoop. Inspect the repository and propose a portfolio of 2-3 "
                "materially different, bounded, falsifiable hypotheses (maximum 4). Do not modify files during this role. "
                "Use the supplied research_state, aggregate failure_signals, experiment_evidence, "
                "and planning_mode. When failure_signals exist, choose one supplied signal_key as "
                "target_failure_signal and explain the causal failure_mechanism; use empty strings "
                "for both fields otherwise. Distinguish partial DEV gains from failed mechanisms. "
                "When experiment_evidence exists, cite a prior ordinal and explain how its result "
                "changes this proposal in novelty_rationale. "
                "Each portfolio candidate must contain candidate_id, nested hypothesis (existing Hypothesis fields), "
                "evidence_refs, suspected_failure_mode, predicted_observable, discriminating_test, scope_eligibility, "
                "estimated_agent_calls, estimated_evaluator_calls, estimated_work_units (or null), rationale, "
                "uncertainty, and inference=agent_inference. Candidate IDs must be stable mechanism slugs. "
                "A candidate may optionally include composite_steps: an ordered sequence of two to four complete "
                "Hypothesis objects when bounded successive edits are required. Each step gets core gates and DEV "
                "measurement; validation and promotion apply only to the final combined result. "
                "Estimate only remaining implementation/evaluation calls after this proposal call. "
                "Use supplied DEV evidence references only or "
                "INSUFFICIENT_EVIDENCE; separate observed evidence from inference. Do not invent gains, causes, "
                "USD costs, or evidence. The core chooses one and enforces scope and budgets. "
                "Avoid repeating a mechanism unless "
                "you explicitly mark it as refine, retry, or supersede and reference the prior experiment ordinal. "
                "Your final assistant message MUST be exactly one JSON object with key candidates containing "
                "1-4 candidate objects, or a legacy single Hypothesis object with keys "
                "hypothesis, rationale, expected_effect, files_or_area, falsification_condition, mechanism_key, "
                "relation, prior_experiment_refs, novelty_rationale, target_failure_signal, failure_mechanism. "
                "If no useful hypothesis exists, return exactly {\"status\":\"NO_HYPOTHESIS\"}. "
                "Do not use markdown fences and do not include prose outside the JSON object."
            )
        elif role == "pivot_advice":
            instruction = (
                "You are proposing an architecture pivot for human review at a measured plateau. "
                "Use only the supplied current architecture, approved goal/scope, and public DEV evidence. "
                "Describe one materially different proposed architecture, its rationale and expected measurable effect, "
                "the exact changed scope, and bounded estimates of additional agent calls, evaluator calls and work units. "
                "Treat all resource values as estimates, never as authorization. Do not change files, alter the approved "
                "goal/evaluator/scope/budget, claim measured success, or request validation/holdout details. Do not include "
                "secrets or private evaluation data. Your final message MUST be exactly one JSON object with keys "
                "proposed_architecture, rationale, expected_effect, changed_scope, resources; resources must contain "
                "additional_agent_calls, additional_evaluator_calls, additional_evaluation_units (or null), and notes. "
                "Do not use markdown fences or prose outside the JSON object."
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
        if role == "bootstrap":
            schema = (
                '{"status":"ready","summary":"...","project_context":"...","needs_input":[],'
                '"evaluation_command":["python",".bananloop_eval/evaluator.py"],'
                '"smoke_command":["python",".bananloop_eval/evaluator.py","--smoke"],'
                '"protocol_manifest_commands":{"dev":[],"validation":[],"holdout":[]},'
                '"agent_visible_training_paths":[], '
                '"agent_visible_context_paths":[], '
                '"validation_command":[],"holdout_command":[],'
                '"primary_metric":{"name":"quality","direction":"maximize","target":1.0,"min_improvement":0.01},'
                '"guardrails":[],"non_regression":[],"mandatory_checks":[],"editable_scope":["**"],'
                '"protected_paths":[".bananloop_eval/**"],"agent_visible_metrics":["quality"],'
                '"agent_visible_diagnostics":[],"paired_comparison":{"enabled":false,"case_count":1,'
                '"repetitions":1,"metric":"quality","direction":"maximize","min_wins":1,'
                '"worst_case_tolerance":0.0},"signal_sanity":{"status":"unavailable",'
                '"evidence_kind":null,"metric":null,"positive_case_ids":[],"negative_case_ids":[],'
                '"reason":"No defensible repository-backed positive/negative examples available."},'
                '"evaluator_timeout_seconds":900}'
            )
        elif role == "propose":
            schema = (
                '{"hypothesis":"...","rationale":"...","expected_effect":"...",'
                '"files_or_area":["..."],"falsification_condition":"...","mechanism_key":"...",'
                '"relation":"new","prior_experiment_refs":[],"novelty_rationale":"..."}'
            )
        elif role == "pivot_advice":
            schema = (
                '{"proposed_architecture":"...","rationale":"...","expected_effect":"...",'
                '"changed_scope":["..."],"resources":{"additional_agent_calls":1,'
                '"additional_evaluator_calls":1,"additional_evaluation_units":null,"notes":"..."}}'
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
        role = task.get("role")
        if role not in {"bootstrap", "propose", "analyze", "implement", "pivot_advice"}:
            raise AgentError(f"unsupported Pi role: {role!r}")
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
