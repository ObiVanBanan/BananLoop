"""Official ChatGPT plan one-shot researcher transport over Responses SSE.

Only the trusted BananLoop owner process may create this client. The model
receives exactly the public role context and approved texts passed by
make_researcher_invoke; it gets no filesystem, shell or network tool handles.
This is a subscription diagnostic, NOT authoritative RPDI-14 metering.
"""
from __future__ import annotations

import http.client
import json
import time
from typing import Any, Callable

from .chatgpt_plan import ChatGPTPlanAuth, SIWCError


INSTRUCTIONS = (
    "You are the research role in BananLoop v0.2. Only PUBLIC_DEV "
    "information in the supplied JSON is available. Use that information "
    "to propose a portfolio of 2-4 materially different, bounded, falsifiable "
    "hypotheses and select one using the supplied evidence and research memory. "
    "Each candidate must have id, hypothesis, rationale, next_experiment, "
    "falsification_condition, and expected_evidence. Return ONE strict JSON object, "
    "with keys hypotheses (array of 2-4 such objects), selected_hypothesis_id, "
    "and optional next_experiment only if it exactly matches the selected candidate, "
    "artifact_text (mapping of relative file names to UTF-8 text, optional), "
    "research_findings (array of objects, optional), evidence_refs (array, "
    "optional), limitations (array, optional). Findings need claim_key, "
    "statement, stance (SUPPORTS/REFUTES/UNCERTAIN), kind (OBSERVATION or "
    "CAUSAL_INFERENCE), confidence 0..1. Provide a matching artifact_text entry for every "
    "finding; the trusted Core assigns content-addressed evidence_refs. Make no claims "
    "of external validation, independent evaluator scores, or causal proof "
    "without controlled evidence. Do not output Markdown or commentary. "
    "If evidence is insufficient, identify the limitation and propose a "
    "direct falsification experiment."
)
OPTIMIZER_INSTRUCTIONS = (
    "You are the pinned BananLoop v0.2 optimizer. Return one strict JSON object with "
    "hypothesis_id, rationale, changes (a mapping of allowlisted relative paths to "
    "complete UTF-8 replacement text), and optional limitations. Do not return commands, "
    "scores, tests, evaluator output, credentials, or private data. Make only the smallest "
    "change that tests the stated hypothesis."
)
INTEGRATION_VERIFIER_INSTRUCTIONS = (
    "You are an independent reviewer of trusted Core evaluation evidence. Return one strict "
    "JSON object with candidate_snapshot_id, protocol_digest, decision (PASS or FAIL), "
    "checks, and optional diagnostics. Review only the supplied public evidence summary. "
    "PASS requires all six checks: candidate_identity, protocol_identity, evaluation_receipt, "
    "budget_receipt, artifact_integrity, regression_policy. If any supplied Core check failed, "
    "or evidence is insufficient, return FAIL and explain why. Never invent evidence or scores."
)
MAX_REPLY_BYTES = 256_000
MAX_ERROR_BYTES = 4_096
MAX_EVENT_BYTES = 512_000
MAX_INPUT_BYTES = 512_000


class ChatGPTPlanResponseError(SIWCError):
    """An authorized subscription request failed or had no terminal result."""


def _output_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                if isinstance(content.get("text"), str):
                    chunks.append(content["text"])
    return "".join(chunks)


def _event_stream(response: Any):
    """Yield parsed SSE data JSON; the stream must end in completed."""
    lines: list[bytes] = []
    seen_bytes = 0
    while True:
        line = response.readline(MAX_EVENT_BYTES + 1)
        if not line:
            if lines:
                # A final event without a blank terminator is a truncation.
                raise ChatGPTPlanResponseError("ChatGPT SSE stream ended mid-event")
            break
        seen_bytes += len(line)
        if seen_bytes > MAX_REPLY_BYTES * 8:
            raise ChatGPTPlanResponseError("ChatGPT SSE stream exceeds byte ceiling")
        if len(line) > MAX_EVENT_BYTES:
            raise ChatGPTPlanResponseError("oversized ChatGPT SSE line")
        if line in {b"\n", b"\r\n"}:
            data_lines = [l[5:].lstrip(b" ") for l in lines if l.startswith(b"data:")]
            lines.clear()
            if not data_lines:
                continue
            joined = b"\n".join(data_lines)
            if joined == b"[DONE]":
                break
            try:
                event = json.loads(joined)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ChatGPTPlanResponseError("invalid ChatGPT SSE event JSON") from exc
            if not isinstance(event, dict):
                raise ChatGPTPlanResponseError("unexpected ChatGPT SSE payload")
            yield event
        elif line.startswith((b"data:", b"event:", b"id:", b"retry:", b":")):
            lines.append(line.rstrip(b"\r\n"))
        else:
            raise ChatGPTPlanResponseError("invalid ChatGPT SSE framing")


def collect_completed(response: Any, *, deadline: float | None = None) -> tuple[str, dict[str, Any] | None, str | None]:
    """Reject nonterminal, failed or incomplete responses and preserve true usage."""
    chunks: list[str] = []
    completed: dict[str, Any] | None = None
    request_id: str | None = None
    for event in _event_stream(response):
        if deadline is not None and time.monotonic() > deadline:
            raise ChatGPTPlanResponseError("ChatGPT request exceeded its deadline")
        kind = event.get("type")
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise ChatGPTPlanResponseError("non-text response delta")
            chunks.append(delta)
            if sum(len(x.encode("utf-8")) for x in chunks) > MAX_REPLY_BYTES:
                raise ChatGPTPlanResponseError("ChatGPT model output exceeds byte ceiling")
        elif kind in {"response.failed", "response.incomplete", "error"}:
            code = None
            if isinstance(event.get("response"), dict):
                err = event["response"].get("error")
                if isinstance(err, dict):
                    code = err.get("code")
            if isinstance(event.get("error"), dict):
                code = event["error"].get("code") or code
            clean_code = code if isinstance(code, str) and len(code) < 120 else kind
            raise ChatGPTPlanResponseError(f"ChatGPT model request did not complete: {clean_code}")
        elif kind == "response.completed":
            candidate = event.get("response")
            if not isinstance(candidate, dict):
                raise ChatGPTPlanResponseError("ChatGPT terminal response is malformed")
            if candidate.get("status") != "completed":
                raise ChatGPTPlanResponseError("ChatGPT terminal response is not completed")
            completed = candidate
            rid = candidate.get("id")
            request_id = rid if isinstance(rid, str) and len(rid) < 140 else None
            break
    if completed is None:
        raise ChatGPTPlanResponseError("ChatGPT stream closed without response.completed")
    text = "".join(chunks) or _output_text(completed)
    if not text or len(text.encode("utf-8")) > MAX_REPLY_BYTES:
        raise ChatGPTPlanResponseError("ChatGPT response has no bounded text output")
    usage = completed.get("usage")
    return text, usage if isinstance(usage, dict) else None, request_id


class ChatGPTPlanResearcher:
    """Pinned provider/model one-shot bridge, with no backend tool execution."""

    def __init__(self, auth: ChatGPTPlanAuth, *, approved_model: str,
                 timeout_seconds: int = 120,
                 transport_factory: Callable[..., Any] = http.client.HTTPSConnection,
                 protocol: str = "rpdi13r-researcher-one-shot-v1",
                 instructions: str = INSTRUCTIONS,
                 input_fields: frozenset[str] = frozenset({
                     "protocol", "role_context", "visible_documents", "instruction",
                 })):
        if not isinstance(approved_model, str) or not approved_model:
            raise SIWCError("a pinned model slug is required")
        if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 300:
            raise SIWCError("model request timeout must be 1..300 seconds")
        self.auth = auth
        self.approved_model = approved_model
        self.timeout_seconds = timeout_seconds
        self.transport_factory = transport_factory
        self.protocol = protocol
        self.instructions = instructions
        self.input_fields = input_fields
        self.last_usage: dict[str, Any] | None = None
        self.last_request_id: str | None = None
        self.last_status = "NOT_STARTED"

    def check_model(self) -> None:
        if self.approved_model not in {m["slug"] for m in self.auth.models()}:
            raise SIWCError("pinned ChatGPT model is missing from account's current visible catalog")

    def __call__(self, public_input: dict[str, Any], *, provider: str,
                 model: str, model_version: str, prompt_digest: str,
                 max_model_tokens: int) -> dict[str, Any]:
        if provider != "openai" or model != self.approved_model:
            raise SIWCError("researcher provider/model does not match frozen SIWC selection")
        if (not isinstance(model_version, str) or not model_version
                or not isinstance(prompt_digest, str) or len(prompt_digest) != 64
                or isinstance(max_model_tokens, bool) or not isinstance(max_model_tokens, int)
                or max_model_tokens < 1):
            raise SIWCError("researcher provider configuration is incomplete")
        if not isinstance(public_input, dict) or public_input.get("protocol") != self.protocol:
            raise SIWCError("untrusted or unversioned pinned-role input")
        if set(public_input) != self.input_fields:
            raise SIWCError("pinned-role model input contains non-approved fields")

        # Note: ChatGPT-plan Responses currently rejects max_output_tokens.
        # A pinned token allowance here is not a provider-enforced hard cap:
        # do NOT claim authoritative token metering for RPDI-14.
        body = {
            "model": model,
            "input": [
                {"role": "developer", "content": self.instructions},
                {"role": "user", "content": json.dumps(public_input, ensure_ascii=False, allow_nan=False)},
            ],
            "store": False, "stream": True,
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise SIWCError("research request exceeds bounded input bytes")
        self.last_usage = None
        self.last_request_id = None
        self.last_status = "PREPARING"
        # Connection is never opened until local input and pinned identity validate.
        access_token = self.auth.access_token()
        connection = self.transport_factory("api.openai.com", timeout=self.timeout_seconds)
        self.last_status = "DISPATCHED"
        try:
            connection.request(
                "POST", "/v1/responses", body=payload,
                headers={
                    "Authorization": "Bearer " + access_token,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "User-Agent": "BananLoop-SIWC/0.1",
                },
            )
            reply = connection.getresponse()
            if reply.status != 200:
                reply.read(MAX_ERROR_BYTES)
                raise ChatGPTPlanResponseError(
                    f"ChatGPT Responses admission failed (HTTP {reply.status})"
                )
            text, usage, request_id = collect_completed(
                reply, deadline=time.monotonic() + self.timeout_seconds,
            )
            try:
                parsed = json.loads(text)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ChatGPTPlanResponseError("model did not produce strict JSON") from exc
            if not isinstance(parsed, dict):
                raise ChatGPTPlanResponseError("model response must be a JSON object")
            self.last_usage = usage
            self.last_request_id = request_id
            self.last_status = "COMPLETED"
            return parsed
        except Exception:
            self.last_status = "FAILED"
            raise
        finally:
            connection.close()


class ChatGPTPlanOptimizer(ChatGPTPlanResearcher):
    """Official SIWC Responses caller for the bounded v2 optimizer protocol."""

    def __init__(self, auth: ChatGPTPlanAuth, *, approved_model: str,
                 timeout_seconds: int = 120,
                 transport_factory: Callable[..., Any] = http.client.HTTPSConnection):
        super().__init__(auth, approved_model=approved_model,
                         timeout_seconds=timeout_seconds,
                         transport_factory=transport_factory,
                         protocol="v2-optimizer-one-shot-v1",
                         instructions=OPTIMIZER_INSTRUCTIONS)


class ChatGPTPlanIntegrationVerifier(ChatGPTPlanResearcher):
    """Tool-free SIWC executor for the independent integration-verifier role."""

    def __init__(self, auth: ChatGPTPlanAuth, *, approved_model: str,
                 timeout_seconds: int = 120,
                 transport_factory: Callable[..., Any] = http.client.HTTPSConnection):
        super().__init__(auth, approved_model=approved_model,
                         timeout_seconds=timeout_seconds,
                         transport_factory=transport_factory,
                         protocol="v2-integration-verifier-one-shot-v1",
                         instructions=INTEGRATION_VERIFIER_INSTRUCTIONS,
                         input_fields=frozenset({
                             "protocol", "role_context", "candidate_snapshot_id",
                             "protocol_digest",
                         }))

    def executor(self, manifest: Any, instance_id: str):
        """Bind this transport to one already-pinned, no-capability verifier role."""
        from .roles import PinnedRoleExecutorV1

        return PinnedRoleExecutorV1.from_manifest(
            manifest, instance_id, self._dispatch_verifier,
        )

    def _dispatch_verifier(self, request: dict[str, Any], gateway: Any, **pin: Any) -> dict[str, Any]:
        if gateway is not None or request.get("schema") != "integration-verification-v1":
            raise SIWCError("integration verifier accepts only the bounded Core evidence request")
        allowed = {
            "schema", "campaign_id", "spec_hash", "source_snapshot_id", "approval_digest",
            "protocol_id", "protocol_version", "protocol_digest", "candidate_snapshot_id",
            "core_evidence_digest", "role_binding", "role_context", "team_manifest_digest",
            "role_registry_digest",
        }
        if set(request) != allowed:
            raise SIWCError("integration verifier request contains unexpected fields")
        model = pin.get("model")
        if not isinstance(model, str):
            raise SIWCError("pinned integration verifier model is missing")
        public_input = {
            "protocol": self.protocol,
            "role_context": request["role_context"],
            "candidate_snapshot_id": request["candidate_snapshot_id"],
            "protocol_digest": request["protocol_digest"],
        }
        return self(
            public_input, provider=pin.get("provider"), model=model,
            model_version=pin.get("model_version"), prompt_digest=pin.get("prompt_digest"),
            max_model_tokens=int(pin.get("max_model_tokens") or 1),
        )
