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
    "For every ordinary research response, ALWAYS return at least one "
    "research_findings item with claim_key, statement, stance, kind, and confidence. "
    "If no experiment was run, record only an UNCERTAIN OBSERVATION that clearly "
    "says the experiment remains unexecuted; never describe a proposed test as a result. "
    "Write a matching artifact_text entry for each finding. On later turns, use "
    "the supplied dev_summary and evidence_refs, cite exact prior evidence IDs "
    "on any candidate that relies on them. If prior findings are available, at "
    "least one candidate MUST cite the matching target claim and exact evidence ID. "
    "Make the next proposed experiment "
    "a distinct follow-up unless the prior evidence explicitly justifies replication. "
    "Each candidate must have id, hypothesis, rationale, next_experiment, "
    "falsification_condition, and expected_evidence. next_experiment may be concise text or a bounded JSON object/array; Core preserves structured plans as canonical JSON text. "
    "Optional candidate "
    "target_claim_key is one string and optional candidate evidence_refs is an array "
    "of evidence ID strings. Never put claim-key/evidence-ID objects inside "
    "evidence_refs. Return ONE strict JSON object, "
    "Candidate ids must match ^[a-z0-9][a-z0-9_-]{0,79}$ exactly; use lowercase "
    "letters only (for example, h1_mediated_effect, never H1_mediated_effect). "
    "with keys hypotheses (array of 2-4 such objects), selected_hypothesis_id, "
    "and optional next_experiment only if it exactly matches the selected candidate, "
    "artifact_text (mapping of relative file names to UTF-8 text, optional), "
    "artifact_base64 (mapping of relative file names to canonical base64 strings, optional), "
    "and protocol_draft or protocol_intent. Omit both only when no trusted evaluator "
    "fixture or public protocol proposal is requested. When a trusted evaluator "
    "fixture is available, the first response MUST include protocol_intent with "
    "protocol_id, version, metric_id, metric_field, direction "
    "(MAXIMIZE or MINIMIZE), aggregation (MEAN, MEDIAN, MIN, or MAX), "
    "repeatability_runs, and timeout_seconds. Do not return hidden tier inputs, "
    "hashes, manifests, or evaluator bytes; trusted Core binds those after your response. "
    "Without a trusted fixture, include protocol_draft only when every included "
    "artifact and case is explicitly public and available in your supplied context. "
    "The draft has exactly two keys: protocol and evidence_refs. protocol is a complete "
    "EvaluatorProtocolV1 object; its source_snapshot_id must copy the exact trusted value "
    "provided in the campaign instruction. Publish and content-bind every referenced "
    "public bundle, manifest, and input artifact in artifact_base64. Never invent hashes, "
    "and never include, request, infer, or generate validation or HOLDOUT case data, "
    "labels, evaluator secrets, or private evaluator code. If a valid proposal requires "
    "assets outside the supplied public context, omit protocol_draft and explain the "
    "limitation in limitations; Core must arrange trusted private-tier binding. "
    "research_findings (array with at least one object on every ordinary reply), "
    "optional JSON property named exactly `evidence_refs` (array of strings only), "
    "and optional limitations (array). For prior evidence, attach target_claim_key "
    "plus its exact evidence ID strings to the candidate's fields; `evidence_refs` "
    "must contain strings only, never objects. "
    "If a fundamental strategy change "
    "is needed, return only strategy_change_request with current_strategy, "
    "proposed_strategy, rationale, expected_effect, and changed_scope. This is "
    "a request for owner review, never authorization or execution. Findings need claim_key, "
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
    "hypothesis_id copied exactly from selected_hypothesis_id, rationale, changes (a mapping of allowlisted relative paths to "
    "complete UTF-8 replacement text), and optional limitations as an array of strings "
    "(omit limitations when there are none; never return a single string there). Do not return commands, "
    "scores, tests, evaluator output, credentials, or private data. When editing numeric "
    "model parameters, preserve the visible schema and use only existing coefficient keys; "
    "do not invent feature names. Make only the smallest change that tests the hypothesis."
)
INTEGRATION_VERIFIER_INSTRUCTIONS = (
    "You are an independent reviewer of trusted Core evaluation evidence. Return one strict "
    "JSON object with candidate_snapshot_id, protocol_digest, decision (PASS or FAIL), "
    "checks, and optional diagnostics. The checks value MUST be a JSON array of check-name "
    "strings, never an object/map of names to booleans. Use only these exact names: "
    "candidate_identity, protocol_identity, evaluation_receipt, budget_receipt, "
    "artifact_integrity, regression_policy. A PASS requires all six names in that array. "
    "Review only the supplied public evidence summary. If any supplied Core check failed, "
    "or evidence is insufficient, return FAIL and explain why. Never invent evidence or scores."
)
PROTOCOL_VERIFIER_INSTRUCTIONS = (
    "You are an independent reviewer of a proposed public evaluator protocol. "
    "Return one JSON object only: no markdown, prose outside JSON, nulls, or extra keys. "
    "The exact keys are schema_version, proposal_digest, decision, checks, rationale, "
    "and public_diagnostics. schema_version is integer 1. proposal_digest is the exact "
    "64-character lowercase hexadecimal digest supplied in the input; copy it exactly. "
    "decision is exactly APPROVE or REJECT. checks is an array of strings, never an object. "
    "rationale is a string. public_diagnostics is an array of strings; use an empty array "
    "when there are no diagnostics. Do not output placeholder text. Allowed check names are "
    "protocol_identity, evaluator_bundle_review, metric_contract, tier_manifests, "
    "frozen_case_scope, no_hidden_data_dependency, fair_comparison, error_semantics. "
    "For APPROVE, include all eight allowed checks exactly once. For REJECT, include "
    "the allowed checks you could verify and name the missing or unsafe evidence in rationale. "
    "Approve only when "
    "the supplied public protocol and source evidence "
    "support protocol_identity, evaluator_bundle_review, metric_contract, tier_manifests, "
    "frozen_case_scope, no_hidden_data_dependency, fair_comparison, and error_semantics. "
    "If any point is missing, unclear, or unsafe, reject. You receive no case records, "
    "labels, hidden evaluator, tools, filesystem, or network access. Do not invent evidence."
    " Treat sandbox_contract and tier_manifest_summaries as authoritative Core facts. "
    "Do not require evaluator source to duplicate manifest, input-digest, or repeatability "
    "checks that the supplied Core contract explicitly guarantees. The protocol_binding "
    "section records whether Core bound the Researcher's separate protocol intent to this "
    "protocol. For fixture-backed campaigns the intent and protocol are shown separately; "
    "do not reject solely because the intent is not repeated inside the protocol object."
)
MAX_REPLY_BYTES = 256_000
MAX_ERROR_BYTES = 4_096
MAX_EVENT_BYTES = 512_000
MAX_INPUT_BYTES = 512_000


def _registered_research_response_format(registered: list[dict[str, str]]) -> dict[str, Any]:
    ids = sorted(item["id"] for item in registered)
    candidate = {
        "type": "object", "additionalProperties": False,
        "required": [
            "id", "hypothesis", "rationale", "next_experiment",
            "falsification_condition", "expected_evidence", "experiment_id",
            "evidence_refs", "target_claim_key",
        ],
        "properties": {
            "id": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,79}$"},
            "hypothesis": {"type": "string", "minLength": 1, "maxLength": 4000},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
            "next_experiment": {"type": "string", "minLength": 1, "maxLength": 4000},
            "falsification_condition": {"type": "string", "minLength": 1, "maxLength": 1000},
            "expected_evidence": {"type": "string", "minLength": 1, "maxLength": 1000},
            "experiment_id": {"type": "string", "enum": ids},
            "evidence_refs": {"type": "array", "maxItems": 16,
                              "items": {"type": "string"}},
            "target_claim_key": {"anyOf": [
                {"type": "string", "pattern": r"^[a-z][a-z0-9_.:-]{0,119}$"},
                {"type": "null"},
            ]},
        },
    }
    finding = {
        "type": "object", "additionalProperties": False,
        "required": ["claim_key", "statement", "stance", "kind", "confidence"],
        "properties": {
            "claim_key": {"type": "string", "pattern": r"^[a-z][a-z0-9_.:-]{0,119}$"},
            "statement": {"type": "string", "minLength": 1, "maxLength": 2000},
            "stance": {"type": "string", "enum": ["SUPPORTS", "REFUTES", "UNCERTAIN"]},
            "kind": {"type": "string", "enum": ["OBSERVATION", "CAUSAL_INFERENCE"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    return {"format": {
        "type": "json_schema", "name": "bananloop_registered_research_v1",
        "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "required": ["hypotheses", "selected_hypothesis_id", "research_findings"],
            "properties": {
                "hypotheses": {"type": "array", "minItems": 2, "maxItems": 4,
                                "items": candidate},
                "selected_hypothesis_id": {"type": "string", "minLength": 1, "maxLength": 100},
                "research_findings": {"type": "array", "minItems": 1, "maxItems": 64,
                                      "items": finding},
            },
        },
    }}


def _fixture_research_response_format() -> dict[str, Any]:
    candidate = {
        "type": "object", "additionalProperties": False,
        "required": [
            "id", "hypothesis", "rationale", "next_experiment",
            "falsification_condition", "expected_evidence", "experiment_id",
            "evidence_refs", "target_claim_key",
        ],
        "properties": {
            "id": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,79}$"},
            "hypothesis": {"type": "string", "minLength": 1, "maxLength": 4000},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
            "next_experiment": {"type": "string", "minLength": 1, "maxLength": 4000},
            "falsification_condition": {"type": "string", "minLength": 1, "maxLength": 1000},
            "expected_evidence": {"type": "string", "minLength": 1, "maxLength": 1000},
            "experiment_id": {"anyOf": [
                {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,79}$"},
                {"type": "null"},
            ]},
            "evidence_refs": {"type": "array", "maxItems": 16,
                              "items": {"type": "string"}},
            "target_claim_key": {"anyOf": [
                {"type": "string", "pattern": r"^[a-z][a-z0-9_.:-]{0,119}$"},
                {"type": "null"},
            ]},
        },
    }
    finding = {
        "type": "object", "additionalProperties": False,
        "required": ["claim_key", "statement", "stance", "kind", "confidence"],
        "properties": {
            "claim_key": {"type": "string", "pattern": r"^[a-z][a-z0-9_.:-]{0,119}$"},
            "statement": {"type": "string", "minLength": 1, "maxLength": 2000},
            "stance": {"type": "string", "enum": ["SUPPORTS", "REFUTES", "UNCERTAIN"]},
            "kind": {"type": "string", "enum": ["OBSERVATION", "CAUSAL_INFERENCE"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    intent = {
        "type": "object", "additionalProperties": False,
        "required": [
            "protocol_id", "version", "metric_id", "metric_field", "direction",
            "aggregation", "repeatability_runs", "timeout_seconds",
        ],
        "properties": {
            "protocol_id": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9._:-]{0,119}$"},
            "version": {"type": "integer", "minimum": 1},
            "metric_id": {"type": "string", "pattern": r"^[a-z][a-z0-9_.:-]{0,119}$"},
            "metric_field": {"type": "string", "pattern": r"^[a-z][a-z0-9_.:-]{0,119}$"},
            "direction": {"type": "string", "enum": ["MAXIMIZE", "MINIMIZE"]},
            "aggregation": {"type": "string", "enum": ["MEAN", "MEDIAN", "MIN", "MAX"]},
            "repeatability_runs": {"type": "integer", "minimum": 2, "maximum": 10},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
        },
    }
    return {"format": {
        "type": "json_schema", "name": "bananloop_fixture_research_v1",
        "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "required": [
                "hypotheses", "selected_hypothesis_id", "research_findings",
                "protocol_intent",
            ],
            "properties": {
                "hypotheses": {"type": "array", "minItems": 2, "maxItems": 4,
                                "items": candidate},
                "selected_hypothesis_id": {"type": "string", "minLength": 1, "maxLength": 100},
                "research_findings": {"type": "array", "minItems": 1, "maxItems": 64,
                                      "items": finding},
                "protocol_intent": intent,
            },
        },
    }}


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
                 call_observer: Callable[[dict[str, Any]], None] | None = None,
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
        self.call_observer = call_observer
        self.request_history: list[dict[str, Any]] = []
        self.input_fields = input_fields
        self.last_usage: dict[str, Any] | None = None
        self.last_request_id: str | None = None
        self.last_status = "NOT_STARTED"
        self.last_response: dict[str, Any] | None = None

    def check_model(self) -> None:
        if self.approved_model not in {m["slug"] for m in self.auth.models()}:
            raise SIWCError("pinned ChatGPT model is missing from account's current visible catalog")

    def __call__(self, public_input: dict[str, Any], *, provider: str,
                 model: str, model_version: str, prompt_digest: str,
                 max_model_tokens: int, timeout_seconds: int | None = None) -> dict[str, Any]:
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
        requires_protocol_intent = public_input.get("requires_protocol_intent", False)
        if type(requires_protocol_intent) is not bool:
            raise SIWCError("fixture protocol-intent flag must be boolean")
        if requires_protocol_intent and "registered_experiments" in public_input:
            raise SIWCError("fixture and registered-experiment output modes cannot be combined")
        request_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if (isinstance(request_timeout, bool) or not isinstance(request_timeout, int)
                or not 1 <= request_timeout <= self.timeout_seconds):
            raise SIWCError("dynamic researcher timeout must be within the configured request ceiling")

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
        if requires_protocol_intent:
            body["text"] = _fixture_research_response_format()
        elif "registered_experiments" in public_input:
            registered = public_input["registered_experiments"]
            if (not isinstance(registered, list) or not registered
                    or any(not isinstance(item, dict)
                           or set(item) != {"id", "description"}
                           or not isinstance(item.get("id"), str)
                           or not item["id"]
                           or not isinstance(item.get("description"), str)
                           for item in registered)
                    or len({item["id"] for item in registered}) != len(registered)):
                raise SIWCError("registered research catalog is malformed")
            body["text"] = _registered_research_response_format(registered)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise SIWCError("research request exceeds bounded input bytes")
        self.last_usage = None
        self.last_request_id = None
        self.last_response = None
        self.last_status = "PREPARING"
        # Connection is never opened until local input and pinned identity validate.
        access_token = self.auth.access_token()
        connection = self.transport_factory("api.openai.com", timeout=request_timeout)
        self.last_status = "DISPATCHED"
        dispatched = True
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
                reply, deadline=time.monotonic() + request_timeout,
            )
            self.last_usage = usage
            self.last_request_id = request_id
            try:
                parsed = json.loads(text)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ChatGPTPlanResponseError("model did not produce strict JSON") from exc
            if not isinstance(parsed, dict):
                raise ChatGPTPlanResponseError("model response must be a JSON object")
            self.last_response = parsed
            self.last_status = "COMPLETED"
            return parsed
        except Exception:
            self.last_status = "FAILED"
            raise
        finally:
            connection.close()
            receipt = {
                "request_id": self.last_request_id,
                "status": self.last_status,
                "dispatched": dispatched,
                "usage": self.last_usage if self.last_usage is not None else "UNKNOWN",
            }
            self.request_history.append(receipt)
            if self.call_observer is not None:
                self.call_observer(dict(receipt))


class ChatGPTPlanOptimizer(ChatGPTPlanResearcher):
    """Official SIWC Responses caller for the bounded v2 optimizer protocol."""

    def __init__(self, auth: ChatGPTPlanAuth, *, approved_model: str,
                 timeout_seconds: int = 120,
                 transport_factory: Callable[..., Any] = http.client.HTTPSConnection,
                 call_observer: Callable[[dict[str, Any]], None] | None = None):
        super().__init__(auth, approved_model=approved_model,
                         timeout_seconds=timeout_seconds,
                         transport_factory=transport_factory,
                         protocol="v2-optimizer-one-shot-v2",
                         instructions=OPTIMIZER_INSTRUCTIONS,
                         call_observer=call_observer,
                         input_fields=frozenset({
                             "protocol", "role_context", "selected_hypothesis_id", "visible_documents",
                             "writable_paths", "instruction",
                         }))


class ChatGPTPlanIntegrationVerifier(ChatGPTPlanResearcher):
    """Tool-free SIWC executor for the independent integration-verifier role."""

    def __init__(self, auth: ChatGPTPlanAuth, *, approved_model: str,
                 timeout_seconds: int = 120,
                 transport_factory: Callable[..., Any] = http.client.HTTPSConnection,
                 call_observer: Callable[[dict[str, Any]], None] | None = None):
        super().__init__(auth, approved_model=approved_model,
                         timeout_seconds=timeout_seconds,
                         transport_factory=transport_factory,
                         protocol="v2-integration-verifier-one-shot-v1",
                         instructions=INTEGRATION_VERIFIER_INSTRUCTIONS,
                         call_observer=call_observer,
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
            timeout_seconds=pin.get("timeout_seconds"),
        )


class ChatGPTPlanProtocolVerifier(ChatGPTPlanResearcher):
    """Tool-free SIWC executor for the independent evaluator-protocol role."""

    def __init__(self, auth: ChatGPTPlanAuth, *, approved_model: str,
                 timeout_seconds: int = 120,
                 transport_factory: Callable[..., Any] = http.client.HTTPSConnection,
                 call_observer: Callable[[dict[str, Any]], None] | None = None):
        super().__init__(auth, approved_model=approved_model,
                         timeout_seconds=timeout_seconds,
                         transport_factory=transport_factory,
                         protocol="evaluator-protocol-verification-v1",
                         instructions=PROTOCOL_VERIFIER_INSTRUCTIONS,
                         call_observer=call_observer,
                         input_fields=frozenset({"protocol", "role_context", "proposal_digest"}))

    def executor(self, manifest: Any, instance_id: str):
        """Bind this transport to a pinned, capability-free verifier role."""
        from .roles import PinnedRoleExecutorV1

        return PinnedRoleExecutorV1.from_manifest(manifest, instance_id, self._dispatch_verifier)

    def _dispatch_verifier(self, request: dict[str, Any], gateway: Any, **pin: Any) -> dict[str, Any]:
        if gateway is not None or request.get("schema") != "evaluator-protocol-verification-v1":
            raise SIWCError("protocol verifier accepts only the bounded Core review request")
        allowed = {
            "schema", "campaign_id", "source_spec_hash", "source_snapshot_id", "protocol_id",
            "protocol_version", "protocol_digest", "proposal_digest", "review_material_digest",
            "role_binding", "role_context", "team_manifest_digest", "role_registry_digest",
        }
        if set(request) != allowed:
            raise SIWCError("protocol verifier request contains unexpected fields")
        model = pin.get("model")
        if not isinstance(model, str):
            raise SIWCError("pinned protocol verifier model is missing")
        return self(
            {"protocol": self.protocol, "role_context": request["role_context"],
             "proposal_digest": request["proposal_digest"]},
            provider=pin.get("provider"), model=model,
            model_version=pin.get("model_version"), prompt_digest=pin.get("prompt_digest"),
            max_model_tokens=int(pin.get("max_model_tokens") or 1),
        )
