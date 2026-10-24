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
    count = 0
    for line in stdout.splitlines():
        try:
            event: Any = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        cached = usage.get("cached_input_tokens", 0)
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0
               for x in (input_tokens, output_tokens, cached)):
            continue
        if cached > input_tokens:
            continue
        count += 1
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["cached_input_tokens"] += cached
    return totals if count else None


def model_tokens(usage: dict[str, int]) -> int:
    return usage["input_tokens"] + usage["output_tokens"]
