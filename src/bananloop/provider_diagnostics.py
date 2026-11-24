"""Safe, bounded diagnostics for provider response failures."""
from __future__ import annotations

import re

from .chatgpt_responses import ChatGPTPlanResponseError


def safe_chatgpt_failure_diagnostics(exc: Exception) -> list[dict[str, object]]:
    """Keep fixed parser categories/statuses; never persist provider response text."""
    if not isinstance(exc, ChatGPTPlanResponseError):
        return []
    message = str(exc)
    status = re.fullmatch(r"ChatGPT Responses admission failed \(HTTP ([1-5][0-9]{2})\)", message)
    fixed = {
        "model did not produce strict JSON", "model response must be a JSON object",
        "ChatGPT stream closed without response.completed", "ChatGPT request exceeded its deadline",
        "ChatGPT SSE stream ended mid-event", "ChatGPT SSE stream exceeds byte ceiling",
        "oversized ChatGPT SSE line", "invalid ChatGPT SSE event JSON",
        "unexpected ChatGPT SSE payload", "invalid ChatGPT SSE framing",
        "ChatGPT terminal response is malformed", "ChatGPT terminal response is not completed",
        "ChatGPT response has no bounded text output", "non-text response delta",
        "ChatGPT model output exceeds byte ceiling",
    }
    category = f"admission HTTP {status.group(1)}" if status else (
        message if message in fixed else "provider failure, details withheld"
    )
    return [{"location": [], "type": "chatgpt_response", "message": category}]


def safe_failure_site(exc: Exception) -> str | None:
    """Return the innermost project frame without exception text or local values."""
    site = None
    cause: BaseException | None = exc
    for _ in range(5):
        if cause is None:
            break
        traceback = cause.__traceback__
        while traceback is not None:
            module = traceback.tb_frame.f_globals.get("__name__", "")
            if isinstance(module, str) and module.startswith(("bananloop.", "benchmarks.")):
                function = traceback.tb_frame.f_code.co_name
                site = f"{module}:{function}:{traceback.tb_lineno}"
            traceback = traceback.tb_next
        cause = cause.__cause__ or cause.__context__
    return site
