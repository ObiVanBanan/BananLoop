"""Optional local, user-initiated Studio onboarding for the BananLoop CLI.

The Studio is NOT an evaluator, campaign scheduler, or alternative Engine.
It delegates review/chat to the hardened Codex operator bridge and explicit
campaign preparation/execution to the existing `bananloop start` command.
No user-supplied path, command, executable, or evaluator is executed here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any


class StudioError(ValueError):
    pass


class StudioUnavailable(RuntimeError):
    pass


BUDGET_FIELDS: dict[str, tuple[int, int, int]] = {
    "max_experiments": (1, 100, 4),
    "max_attempts": (1, 200, 7),
    "max_agent_calls": (1, 500, 18),
    "max_evaluator_calls": (1, 500, 16),
    "deadline_seconds": (60, 86400, 14400),
}
REQUEST_RE = re.compile(r"^[a-zA-Z0-9_-]{8,100}$")


def validate_plan(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"goal", "mode", "budgets", "request_id"}:
        raise StudioError("invalid campaign launch document")
    goal = payload["goal"]
    if not isinstance(goal, str) or not 12 <= len(goal.strip()) <= 1200 or "\x00" in goal:
        raise StudioError("goal must contain 12..1200 characters")
    mode = payload["mode"]
    if mode not in {"full_auto", "supervised"}:
        raise StudioError("invalid launch mode")
    request_id = payload["request_id"]
    if not isinstance(request_id, str) or REQUEST_RE.fullmatch(request_id) is None:
        raise StudioError("invalid idempotency request id")
    budgets = payload["budgets"]
    if not isinstance(budgets, dict) or set(budgets) != set(BUDGET_FIELDS):
        raise StudioError("missing or unknown budget field")
    safe = {}
    for field, (lowest, highest, _) in BUDGET_FIELDS.items():
        val = budgets[field]
        if isinstance(val, bool) or not isinstance(val, int) or not lowest <= val <= highest:
            raise StudioError(f"invalid {field}")
        safe[field] = val
    if safe["max_attempts"] < safe["max_experiments"]:
        raise StudioError("max_attempts must be at least max_experiments")
    return {"goal": goal.strip(), "mode": mode, "budgets": safe, "request_id": request_id}


def default_budget() -> dict[str, int]:
    return {name: triple[2] for name, triple in BUDGET_FIELDS.items()}


def studio_path() -> Path:
    """No project-folder input. User's own application data directory."""
    if os.name == "nt":
        home = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return home / "BananLoop" / "Studio"
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return home / "bananloop" / "studio"


def ensure_studio_repo(root: Path) -> Path:
    """Initialize a *new*, minimal source checkout; never overwrite an existing repo."""
    root = root.expanduser().absolute()
    if root.is_symlink() or root.parent.is_symlink():
        raise StudioError("Studio workspace must not be a symbolic link")
    marker = root / ".bananloop-studio-owner"
    if root.exists() and any(root.iterdir()):
        if not marker.is_file() or not (root / ".git").is_dir():
            raise StudioError("Studio path already contains unrelated files")
        return root
    if not shutil.which("git"):
        raise StudioUnavailable("git is required to create a local Studio workspace")
    root.mkdir(parents=True, exist_ok=True)
    # Commit a genuinely minimal source repo. Bootstrap must still independently
    # discover and verify a real evaluator; don't fabricate one.
    (root / "README.md").write_text(
        "# BananLoop Studio\n\nThis is a locally managed research workspace.\n"
        "Campaign goals come from the Studio UI. No evaluator, benchmark, "
        "dataset, research result or metric is pre-certified here.\n"
        "Engine bootstrap must check and may request missing resources.\n", encoding="utf-8")
    (root / ".gitignore").write_text(".bananloop-studio/\n.bananloop-studio-owner\n", encoding="utf-8")
    try:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=12,
                       stdin=subprocess.DEVNULL, capture_output=True)
        subprocess.run(["git", "add", "README.md", ".gitignore"], cwd=root,
                       check=True, timeout=12, stdin=subprocess.DEVNULL, capture_output=True)
        subprocess.run(["git", "-c", "user.name=BananLoop Studio", "-c", "user.email=studio@localhost",
                        "commit", "-q", "-m", "Initialize local Studio workspace"], cwd=root,
                       check=True, timeout=12, stdin=subprocess.DEVNULL, capture_output=True)
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise StudioUnavailable("Studio workspace initialization failed") from exc
    marker.write_text("bananloop-studio-v1\n", encoding="utf-8")
    return root


def _sanitize_draft(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    goal = value.get("goal")
    if not isinstance(goal, str) or not 12 <= len(goal.strip()) <= 1200:
        return None
    numbers = {}
    raw = value.get("budgets") if isinstance(value.get("budgets"), dict) else {}
    for field, (low, high, fallback) in BUDGET_FIELDS.items():
        v = raw.get(field, fallback)
        numbers[field] = v if type(v) is int and low <= v <= high else fallback
    numbers["max_attempts"] = max(numbers["max_experiments"], numbers["max_attempts"])
    gaps = value.get("missing")
    missing = [str(item)[:200] for item in gaps if isinstance(item, str)][:5] if isinstance(gaps, list) else []
    return {"goal": goal.strip(), "budgets": numbers, "missing": missing,
            "ready_for_bootstrap": value.get("ready_for_bootstrap") is True}


def _validate_chat_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {"message", "history"}:
        raise StudioError("invalid discussion request")
    message, history = body["message"], body["history"]
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 3000:
        raise StudioError("message must contain 1..3000 characters")
    if not isinstance(history, list) or len(history) > 16:
        raise StudioError("discussion history limit exceeded")
    safe = []
    for item in history:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise StudioError("invalid history item")
        if item["role"] not in {"user", "assistant"} or not isinstance(item["content"], str) or len(item["content"]) > 3500:
            raise StudioError("invalid history content")
        safe.append({"role": item["role"], "content": item["content"]})
    return {"message": message.strip(), "history": safe}


def discuss_via_codex(body: Any, *, worker: Path | None = None, runner: Any = subprocess.run) -> dict[str, Any]:
    """One real, read-only Codex model turn. Never infer success on failure."""
    request = _validate_chat_body(body)
    node = shutil.which("node")
    if not node:
        raise StudioUnavailable("Node.js is required for the isolated Codex planning bridge")
    worker = worker or Path(__file__).resolve().parents[2] / "operator-ui" / "studio-chat-worker.mjs"
    if not worker.is_file():
        raise StudioUnavailable("Codex Studio chat worker was not installed")
    try:
        result = runner([node, str(worker)], input=json.dumps(request, ensure_ascii=False),
                        capture_output=True, text=True, timeout=155, check=False,
                        cwd=worker.parent, stdin=None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StudioUnavailable("Codex planning turn is unavailable or timed out") from exc
    if result.returncode != 0:
        # The worker reports a bounded code, never raw CLI output or credentials.
        match = re.search(r"STUDIO_ERROR_CODE:([a-z_]{3,40})", result.stderr[-1500:])
        known = {"unsafe_config", "app_server_unavailable", "model_timeout",
                 "model_incomplete", "provider_unavailable"}
        code = match.group(1) if match and match.group(1) in known else "provider_unavailable"
        raise StudioUnavailable(code)
    try:
        parsed = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StudioUnavailable("Codex planning response was invalid") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("reply"), str) or not parsed["reply"].strip():
        raise StudioUnavailable("Codex planning reply was empty")
    return {"reply": parsed["reply"][:3500], "draft": _sanitize_draft(parsed.get("draft")),
            "provider": "codex_cli", "model_called": True}


def _campaign_id(request_id: str) -> str:
    # Use the exact existing CampaignRegistry canonical request-id identity.
    from .registry import request_key
    key = request_key("unused goal", request_id)
    return "c-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


class StudioLauncher:
    """One bounded, local operator-launch owner for this API server."""

    def __init__(self, repo: str | Path, *, process_factory: Any = subprocess.Popen) -> None:
        self.repo = Path(repo).resolve()
        self._lock = threading.RLock()
        self._active: dict[str, Any] | None = None
        self._spawn = process_factory

    def _stage(self, item: dict[str, Any]) -> dict[str, Any]:
        child = item["process"]
        code = child.poll()
        if code is None:
            phase = "PREPARING" if item["prepare_only"] else "BOOTSTRAPPING_OR_RUNNING"
        elif code == 3:
            phase = "NEEDS_INPUT"
        elif code != 0:
            phase = "FAILED"
        elif item["prepare_only"]:
            phase = "AWAITING_CONFIRMATION"
        else:
            phase = "FINISHED"
        return {"request_id": item["request_id"], "campaign_id": item["campaign_id"],
                "phase": phase, "mode": item["plan"]["mode"], "exit_code": code,
                "note": "Engine bootstrap may require a real evaluator or additional project resources." if phase == "NEEDS_INPUT" else None}

    def status(self, request_id: str) -> dict[str, Any]:
        if REQUEST_RE.fullmatch(request_id) is None:
            raise StudioError("invalid request ID")
        with self._lock:
            if not self._active or self._active["request_id"] != request_id:
                return {"phase": "UNKNOWN", "request_id": request_id}
            return self._stage(self._active)

    def launch(self, body: Any, *, confirm: bool = False) -> dict[str, Any]:
        with self._lock:
            if confirm:
                if not isinstance(body, dict) or set(body) != {"request_id"}:
                    raise StudioError("confirmation requires only request_id")
                rid = body["request_id"]
                if not isinstance(rid, str) or REQUEST_RE.fullmatch(rid) is None:
                    raise StudioError("invalid request id")
                old = self._active
                if not old or old["request_id"] != rid or not old["prepare_only"]:
                    raise StudioError("no matching prepared campaign")
                if self._stage(old)["phase"] != "AWAITING_CONFIRMATION":
                    raise StudioError("campaign is not ready for explicit execution")
                plan = old["plan"]
                return self._execute(plan, prepare_only=False)
            plan = validate_plan(body)
            active = self._active
            if active is not None:
                if active["request_id"] == plan["request_id"]:
                    if active["plan"] != plan:
                        raise StudioError("idempotency key reused with different plan")
                    return self._stage(active)
                if active["process"].poll() is None:
                    raise StudioError("another campaign bootstrap or execution is still active")
            return self._execute(plan, prepare_only=plan["mode"] == "supervised")

    def _execute(self, plan: dict[str, Any], *, prepare_only: bool) -> dict[str, Any]:
        if not (self.repo / ".bananloop-studio-owner").is_file():
            raise StudioUnavailable("not a managed Studio workspace")
        logs = self.repo / ".bananloop-studio"
        logs.mkdir(exist_ok=True)
        request_id = plan["request_id"]
        argv = [sys.executable, "-m", "bananloop.cli", "start", "--repo", str(self.repo),
                "--goal", plan["goal"], "--request-id", request_id, "--agent", "codex"]
        for field, value in plan["budgets"].items():
            argv.extend(["--" + field.replace("_", "-"), str(value)])
        if prepare_only:
            argv.append("--prepare-only")
        # Never use shell=True or accept CLI arguments, repo paths, commands or
        # evaluators from the browser. The only worker is the audited Engine CLI.
        path = logs / f"{request_id}.log"
        try:
            with path.open("ab") as logfile:
                process = self._spawn(argv, cwd=self.repo, stdin=subprocess.DEVNULL,
                                      stdout=logfile, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise StudioUnavailable("BananLoop Engine process could not start") from exc
        self._active = {"process": process, "request_id": request_id,
                        "campaign_id": _campaign_id(request_id), "plan": plan,
                        "prepare_only": prepare_only}
        return self._stage(self._active)