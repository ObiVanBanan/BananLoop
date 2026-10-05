from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from .contracts import RuntimePaths
from .lock import CampaignLock, CampaignLockError
from .registry import CampaignRegistry, RegistryError, assert_safe_local_path


_CAMPAIGN_MARKER = re.compile(r"\bBananLoop campaign:\s*(legacy|c-[0-9a-f]{32})\b")


def _read_campaign_state(state_root: Path) -> tuple[str, bool] | None:
    database = state_root / "campaign.sqlite3"
    if not database.is_file():
        return None
    uri = database.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.15)
    try:
        row = conn.execute(
            "SELECT status,stop_requested FROM campaigns WHERE id=1"
        ).fetchone()
        return (str(row[0]), bool(row[1])) if row else None
    finally:
        conn.close()


def _campaign_lock_is_free(state_root: Path) -> bool:
    assert_safe_local_path(state_root, "campaign state directory")
    lock_path = state_root / "campaign.lock"
    assert_safe_local_path(lock_path, "campaign lock")
    lock = CampaignLock(lock_path)
    try:
        lock.acquire()
    except CampaignLockError:
        return False
    else:
        lock.release()
        return True


def stop_hook_decision(event: dict[str, Any]) -> dict[str, str] | None:
    """Read durable campaign state and request one bounded Codex continuation."""
    if event.get("hook_event_name") != "Stop" or event.get("stop_hook_active") is True:
        return None
    session_id = event.get("session_id")
    cwd = event.get("cwd")
    if not isinstance(session_id, str) or not isinstance(cwd, str):
        return None
    repo = Path(cwd)
    if not repo.is_dir():
        return None

    paths = RuntimePaths.for_repo(repo)
    registry = CampaignRegistry(repo, paths)
    try:
        bound_id = registry.codex_session_campaign(session_id)
        last_message = event.get("last_assistant_message")
        marker = _CAMPAIGN_MARKER.search(last_message) if isinstance(last_message, str) else None
        explicit_id = marker.group(1) if marker else None
        campaign_id = explicit_id or bound_id
        if campaign_id is None:
            return None
        state_root = registry.codex_campaign_state_path(campaign_id)
        if state_root is None:
            return None
        state = _read_campaign_state(state_root)
        if state is None:
            return None
        status, stop_requested = state
        if status != "RUNNING" or stop_requested:
            return None
        # A session is bound once. Conflicting markers are ignored rather than
        # allowing arbitrary assistant text to retarget a running hook.
        if bound_id is not None and explicit_id is not None and explicit_id != bound_id:
            return None
        if explicit_id is not None and bound_id is None:
            registry.bind_codex_session(session_id, explicit_id)
        if not _campaign_lock_is_free(state_root):
            return None
    except (OSError, sqlite3.Error, RegistryError, ValueError):
        # Hooks are advisory: a missing package/state, timeout, or malformed event
        # must never prevent the user from stopping their Codex turn.
        return None

    return {
        "decision": "block",
        "reason": (
            f"BananLoop campaign {campaign_id} is durably RUNNING and has no live supervisor lock. "
            f"Inspect it with `bananloop status --campaign-id {campaign_id}`. "
            "If its durable receipts show an ambiguous RUNNING action, do not retry that action; "
            f"report the unknown outcome. Otherwise continue with `bananloop resume --campaign-id {campaign_id}`. "
            "The hook must not launch agents or evaluators."
        ),
    }


def main() -> int:
    started = time.monotonic()
    try:
        raw = sys.stdin.read(1024 * 1024)
        event = json.loads(raw)
        if not isinstance(event, dict):
            return 0
        decision = stop_hook_decision(event)
        if decision and time.monotonic() - started < 2.5:
            sys.stdout.write(json.dumps(decision, ensure_ascii=False) + "\n")
    except Exception:
        # The plugin hook is fail-open for trust, timeout, parse, and IO failures.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
