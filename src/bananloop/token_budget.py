"""Strict Codex JSONL token usage parsing for opt-in model budgets.

A missing final usage receipt is unknown, never zero. Cached input is already
included in input_tokens and must not be counted again.
"""
from __future__ import annotations

import json
from typing import Any


class ModelTokenBudgetExceeded(RuntimeError):
    """A model call was charged beyond the approved campaign token budget."""


def parse_codex_usage(stdout: str) -> dict[str, int] | None:
    totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    completed: list[dict[str, Any] | None] = []
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event: Any = json.loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(event, dict):
            return None
        events.append(event)
        if event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            completed.append(None)
            continue
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        cached = usage.get("cached_input_tokens", 0)
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0
               for x in (input_tokens, output_tokens, cached)):
            completed.append(None)
            continue
        if cached > input_tokens:
            completed.append(None)
            continue
        completed.append({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached,
        })
    # Codex exec emits the terminal receipt last. An earlier completed turn
    # cannot stand in for a missing or malformed final receipt.
    if (
        not events
        or events[-1].get("type") != "turn.completed"
        or not completed
        or any(usage is None for usage in completed)
    ):
        return None
    for usage in completed:
        totals["input_tokens"] += usage["input_tokens"]
        totals["output_tokens"] += usage["output_tokens"]
        totals["cached_input_tokens"] += usage["cached_input_tokens"]
    return totals


def model_tokens(usage: dict[str, int]) -> int:
    return usage["input_tokens"] + usage["output_tokens"]
