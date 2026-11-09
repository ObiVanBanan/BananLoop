"""Small loopback-first REST/OpenAPI and replayable SSE surface."""

from __future__ import annotations

import hmac
import json
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .application import CampaignApplication
from .ui_read_models import campaign_ui_view, list_ui_campaigns, codex_connection_status
from .ui_codex_login import CodexDeviceLogin, LoginUnavailable
from .ui_studio import StudioLauncher, StudioError, StudioUnavailable, discuss_via_codex
from .contracts import PivotDecisionCommand, PivotProposal
from .graph_store import GraphStoreError
from .registry import RegistryError
from .store import StoreError

MAX_BODY_BYTES = 64 * 1024
_CAMPAIGN_PATH = re.compile(r"^/v1/campaigns/([^/]+)(?:/(snapshot|view|events|events/stream|graph|graph/commands|commands/(pause|resume|stop)|pivot-decisions|v2/pivot-proposals|v2/pivot-decisions|v2/pivot-executions))?$")


def _openapi() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "BananLoop Local API", "version": "0.2.0"},
        "paths": {
            "/healthz": {"get": {"operationId": "health", "responses": {"200": {"description": "Healthy"}}}},
            "/v1/campaigns": {"get": {"operationId": "uiCampaignList", "responses": {"200": {"description": "Safe campaign registry summary"}}}},
            "/v1/studio/info": {"get": {"operationId": "studioInfo", "responses": {"200": {"description": "Local managed Studio availability"}}}},
            "/v1/studio/discuss": {"post": {"operationId": "discussStudioCampaign", "responses": {"200": {"description": "Real, tool-isolated Codex planning reply"}}}},
            "/v1/studio/launch": {"post": {"operationId": "launchStudioCampaign", "responses": {"200": {"description": "Bounded Engine launch receipt"}}}},
            "/v1/studio/launch/confirm": {"post": {"operationId": "confirmStudioLaunch", "responses": {"200": {"description": "Explicit prepared campaign execution"}}}},
            "/v1/studio/launch/status": {"get": {"operationId": "studioLaunchStatus", "responses": {"200": {"description": "Current local launch process status"}}}},
            "/v1/providers/codex/status": {"get": {"operationId": "codexConnectionStatus", "responses": {"200": {"description": "Codex CLI authentication mode (no credentials)"}}}},
            "/v1/providers/codex/login/status": {"get": {"operationId": "codexDeviceLoginStatus", "responses": {"200": {"description": "Device login progress (no credentials)"}}}},
            "/v1/providers/codex/login/start": {"post": {"operationId": "startCodexDeviceLogin", "responses": {"200": {"description": "Official verification URL and one-time user code"}}}},
            "/v1/providers/codex/login/cancel": {"post": {"operationId": "cancelCodexDeviceLogin", "responses": {"200": {"description": "Device login cancelled"}}}},
            "/v1/campaigns/{campaign_id}/view": {"get": {"operationId": "campaignReadOnlyView", "responses": {"200": {"description": "Safe read-only UI graph projection"}}}},
            "/v1/campaigns/{campaign_id}/snapshot": {"get": {"operationId": "campaignSnapshot", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Redacted campaign snapshot"}}}},
            "/v1/campaigns/{campaign_id}/graph": {"get": {"operationId": "researchGraphSnapshot", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Redacted v2 graph scheduling snapshot"}}}},
            "/v1/campaigns/{campaign_id}/graph/commands": {"post": {"operationId": "mutateResearchGraph", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GraphMutation"}}}}, "responses": {"200": {"description": "Canonical graph mutation receipt"}}}},
            "/v1/campaigns/{campaign_id}/events": {"get": {"operationId": "campaignEvents", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "after", "in": "query", "schema": {"type": "integer", "minimum": 0}}, {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000}}], "responses": {"200": {"description": "Redacted cursor page"}}}},
            "/v1/campaigns/{campaign_id}/events/stream": {"get": {"operationId": "campaignEventStream", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "after", "in": "query", "schema": {"type": "integer", "minimum": 0}}], "responses": {"200": {"description": "Server-sent event stream", "content": {"text/event-stream": {"schema": {"type": "string"}}}}}}},
            "/v1/campaigns/{campaign_id}/commands/{command}": {"post": {"operationId": "submitControlCommand", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "command", "in": "path", "required": True, "schema": {"type": "string", "enum": ["pause", "resume", "stop"]}}, {"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ControlRequest"}}}}, "responses": {"200": {"description": "Durable command receipt"}}}},
            "/v1/campaigns/{campaign_id}/pivot-decisions": {"post": {"operationId": "recordPivotDecision", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PivotDecision"}}}}, "responses": {"200": {"description": "Decision receipt"}}}},
            "/v1/campaigns/{campaign_id}/v2/pivot-proposals": {"post": {"operationId": "recordV2PivotProposal", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/V2PivotProposal"}}}}, "responses": {"200": {"description": "Durable v2 proposal receipt"}}}},
            "/v1/campaigns/{campaign_id}/v2/pivot-decisions": {"post": {"operationId": "recordV2PivotDecision", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PivotDecision"}}}}, "responses": {"200": {"description": "Durable v2 decision receipt"}}}},
            "/v1/campaigns/{campaign_id}/v2/pivot-executions": {"post": {"operationId": "executeOrResumeV2Pivot", "parameters": [{"name": "campaign_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PivotExecutionRequest"}}}}, "responses": {"200": {"description": "Active child campaign receipt"}}}},
        },
        "security": [{}, {"BearerToken": []}],
        "components": {"securitySchemes": {"BearerToken": {"type": "http", "scheme": "bearer"}}, "schemas": {
            "ControlRequest": {"type": "object", "additionalProperties": False, "properties": {"request_id": {"type": "string", "minLength": 1, "maxLength": 200}}, "required": ["request_id"]},
            "PivotDecision": {"type": "object", "additionalProperties": False, "properties": {"campaign_id": {"type": "string"}, "request_id": {"type": "string"}, "proposal_id": {"type": "string"}, "digest": {"type": "string"}, "decision": {"type": "string", "enum": ["approve", "reject"]}, "rejection_action": {"type": ["string", "null"], "enum": ["continue_old_plan", "change_conditions", "end_campaign", None]}}, "required": ["campaign_id", "request_id", "proposal_id", "digest", "decision"]},
            "V2PivotProposal": {"type": "object", "additionalProperties": False, "properties": {"proposal_id": {"type": "string"}, "digest": {"type": "string"}, "spec_hash": {"type": "string"}, "trigger_experiment_ordinal": {"type": "integer"}, "current_architecture": {"type": "string"}, "proposed_architecture": {"type": "string"}, "rationale": {"type": "string"}, "expected_effect": {"type": "string"}, "changed_scope": {"type": "array", "items": {"type": "string"}}, "resources": {"type": "object"}, "execution_plan": {"type": ["object", "null"], "properties": {"schema_version": {"type": "integer", "const": 1}, "child_spec_json": {"type": "string"}, "changed_paths": {"type": "array", "items": {"type": "string"}}, "resource_vector_json": {"type": "string"}}, "required": ["schema_version", "child_spec_json", "changed_paths", "resource_vector_json"]}}, "required": ["proposal_id", "digest", "spec_hash", "trigger_experiment_ordinal", "current_architecture", "proposed_architecture", "rationale", "expected_effect", "changed_scope", "resources"]},
            "PivotExecutionRequest": {"type": "object", "additionalProperties": False, "properties": {"proposal_id": {"type": "string"}, "digest": {"type": "string"}}, "required": ["proposal_id", "digest"]},
            "GraphMutation": {"type": "object", "additionalProperties": False, "properties": {"command_id": {"type": "string", "minLength": 1, "maxLength": 200}, "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200}, "expected_graph_revision": {"type": "integer", "minimum": 0}, "changes": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/GraphChange"}}, "evidence_refs": {"type": "array", "items": {"type": "string"}}}, "required": ["command_id", "idempotency_key", "expected_graph_revision", "changes"]},
            "GraphChange": {"oneOf": [{"$ref": "#/components/schemas/AddNode"}, {"$ref": "#/components/schemas/AddEdge"}, {"$ref": "#/components/schemas/AddGroup"}, {"$ref": "#/components/schemas/TransitionNodePhase"}, {"$ref": "#/components/schemas/TransitionNodeStatus"}], "discriminator": {"propertyName": "type"}},
            "AddNode": {"type": "object", "additionalProperties": False, "properties": {"type": {"const": "AddNode"}, "node_id": {"type": "string", "maxLength": 200}, "phase": {"type": "string", "enum": ["EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION", "CONSOLIDATION"]}, "payload": {"type": "object"}}, "required": ["type", "node_id", "phase"]},
            "AddEdge": {"type": "object", "additionalProperties": False, "properties": {"type": {"const": "AddEdge"}, "source_id": {"type": "string"}, "target_id": {"type": "string"}, "edge_type": {"type": "string", "enum": ["REQUIRES", "DERIVED_FROM", "RELATED_TO"]}, "constraint": {"type": "object"}}, "required": ["type", "source_id", "target_id", "edge_type"]},
            "AddGroup": {"type": "object", "additionalProperties": False, "properties": {"type": {"const": "AddGroup"}, "group_id": {"type": "string"}, "owner_node_id": {"type": "string"}, "kind": {"type": "string", "enum": ["AND", "OR"]}, "member_node_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["type", "group_id", "owner_node_id", "kind", "member_node_ids"]},
            "TransitionNodePhase": {"type": "object", "additionalProperties": False, "properties": {"type": {"const": "TransitionNodePhase"}, "node_id": {"type": "string"}, "expected_node_revision": {"type": "integer", "minimum": 0}, "from_phase": {"type": "string"}, "to_phase": {"type": "string"}, "guard": {"type": "string"}, "actor": {"type": "string", "enum": ["CORE", "VERIFIER"]}, "evidence_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}}}, "required": ["type", "node_id", "expected_node_revision", "from_phase", "to_phase", "guard", "actor", "evidence_refs"]},
            "TransitionNodeStatus": {"type": "object", "additionalProperties": False, "properties": {"type": {"const": "TransitionNodeStatus"}, "node_id": {"type": "string"}, "expected_node_revision": {"type": "integer", "minimum": 0}, "expected_status": {"type": "string"}, "to_status": {"type": "string"}, "guard": {"type": "string"}, "actor": {"type": "string", "enum": ["CORE", "HUMAN"]}, "evidence_refs": {"type": "array", "items": {"type": "string"}}}, "required": ["type", "node_id", "expected_node_revision", "expected_status", "to_status", "guard", "actor"]},
        }},
    }


def _safe_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def create_server(
    repo: str, host: str = "127.0.0.1", port: int = 8765, *, token: str | None = None,
    poll_seconds: float = 0.5, studio_mode: bool = False,
) -> ThreadingHTTPServer:
    """Create a loopback-only HTTP server for local clients and dashboards."""
    if not 0.05 <= poll_seconds <= 10:
        raise ValueError("SSE poll interval must be between 0.05 and 10 seconds")
    if host.lower() not in {"127.0.0.1", "localhost"}:
        raise ValueError("API binds are restricted to loopback until TLS transport is configured")

    device_login = CodexDeviceLogin()
    studio = StudioLauncher(repo) if studio_mode else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "BananLoopAPI/0.2"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Avoid logging URLs that may include campaign identifiers or cursors.
            return

        def _authorized(self) -> bool:
            if token is None:
                return True
            supplied = self.headers.get("Authorization", "")
            prefix = "Bearer "
            return supplied.startswith(prefix) and hmac.compare_digest(supplied[len(prefix):], token)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = _safe_json(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None or not raw_length.isdecimal():
                raise ValueError
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                raise OverflowError
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError
            return value

        def _application(self, campaign_id: str) -> CampaignApplication:
            return CampaignApplication(repo, campaign_id=campaign_id)

        def _events(self, campaign_id: str, after: int):
            return self._application(campaign_id).events(after, 100)

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if parsed.path == "/openapi.json":
                self._json(HTTPStatus.OK, _openapi())
                return
            if parsed.path == "/v1/studio/info":
                self._json(HTTPStatus.OK, {"enabled": studio is not None,
                    "workspace_kind": "managed_local" if studio else None,
                    "planning_provider": "codex_cli" if studio else None,
                    "modes": ["full_auto", "supervised"] if studio else []})
                return
            if parsed.path == "/v1/studio/launch/status":
                if studio is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "studio_disabled"})
                    return
                try:
                    request_ids = parse_qs(parsed.query).get("request_id", [])
                    if len(request_ids) != 1:
                        raise StudioError("one request_id is required")
                    self._json(HTTPStatus.OK, studio.status(request_ids[0]))
                except StudioError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_studio_request"})
                return
            if parsed.path in {"/v1/campaigns", "/v1/providers/codex/status", "/v1/providers/codex/login/status"}:
                try:
                    if parsed.path == "/v1/campaigns":
                        self._json(HTTPStatus.OK, list_ui_campaigns(repo))
                    elif parsed.path == "/v1/providers/codex/login/status":
                        self._json(HTTPStatus.OK, device_login.status())
                    else:
                        self._json(HTTPStatus.OK, codex_connection_status())
                except (RegistryError, StoreError, ValueError):
                    self._json(HTTPStatus.CONFLICT, {"error": "registry_unavailable"})
                except Exception:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})
                return
            match = _CAMPAIGN_PATH.fullmatch(parsed.path)
            if not match:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            campaign_id, endpoint = match.group(1), match.group(2)
            app = self._application(campaign_id)
            query = parse_qs(parsed.query, strict_parsing=False)
            try:
                if endpoint == "snapshot":
                    self._json(HTTPStatus.OK, app.snapshot())
                elif endpoint == "view":
                    self._json(HTTPStatus.OK, campaign_ui_view(repo, campaign_id))
                elif endpoint == "graph":
                    self._json(HTTPStatus.OK, app.graph_snapshot())
                elif endpoint == "events":
                    after = int(query.get("after", ["0"])[0])
                    limit = int(query.get("limit", ["100"])[0])
                    self._json(HTTPStatus.OK, app.events(after, limit))
                elif endpoint == "events/stream":
                    after = int(self.headers.get("Last-Event-ID", query.get("after", ["0"])[0]))
                    if after < 0:
                        raise ValueError
                    app.events(after, 1)  # Validate the cursor before committing SSE headers.
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    self.wfile.write(b"retry: 1000\n\n")
                    self.wfile.flush()
                    last_heartbeat = time.monotonic()
                    while not self.server.shutdown_requested.is_set():
                        try:
                            page = self._events(campaign_id, after)
                        except (RegistryError, StoreError, ValueError):
                            return
                        if page["events"]:
                            for item in page["events"]:
                                raw_type = str(item["type"])
                                event_type = raw_type if re.fullmatch(r"[A-Z0-9_]{1,80}", raw_type) else "message"
                                frame = (f"id: {item['seq']}\nevent: {event_type}\ndata: "
                                         f"{_safe_json(item).decode('utf-8')}\n\n").encode("utf-8")
                                self.wfile.write(frame)
                                after = int(item["seq"])
                            self.wfile.flush()
                            last_heartbeat = time.monotonic()
                        elif time.monotonic() - last_heartbeat >= 15:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                            last_heartbeat = time.monotonic()
                        time.sleep(poll_seconds)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except (RegistryError, StoreError):
                self._json(HTTPStatus.NOT_FOUND, {"error": "campaign_unavailable"})
            except (ValueError, TypeError, OverflowError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            studio_path = urlsplit(self.path).path
            if studio_path in {"/v1/studio/discuss", "/v1/studio/launch", "/v1/studio/launch/confirm"}:
                if studio is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "studio_disabled"})
                    return
                allowed = {
                    "http://127.0.0.1:5173", "http://localhost:5173",
                    f"http://127.0.0.1:{self.server.server_address[1]}",
                    f"http://localhost:{self.server.server_address[1]}",
                }
                origin = self.headers.get("Origin", "")
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if origin not in allowed or content_type != "application/json":
                    self._json(HTTPStatus.FORBIDDEN, {"error": "origin_or_content_type_rejected"})
                    return
                try:
                    data = self._body()
                    if studio_path == "/v1/studio/discuss":
                        response = discuss_via_codex(data)
                    elif studio_path == "/v1/studio/launch":
                        response = studio.launch(data)
                    else:
                        response = studio.launch(data, confirm=True)
                    self._json(HTTPStatus.OK, response)
                except OverflowError:
                    self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
                except (StudioError, TypeError, ValueError):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_studio_request"})
                except StudioUnavailable as exc:
                    known = {"unsafe_config", "app_server_unavailable", "model_timeout",
                             "model_incomplete", "provider_unavailable"}
                    code = str(exc) if str(exc) in known else "studio_provider_unavailable"
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": code})
                except Exception:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "studio_internal_error"})
                return
            if studio_path in {"/v1/providers/codex/login/start", "/v1/providers/codex/login/cancel"}:
                # An explicit browser gesture is required. Reject cross-site
                # form posts even though the API is loopback-only.
                origin = self.headers.get("Origin", "")
                allowed_origins = {
                    "http://127.0.0.1:5173", "http://localhost:5173",
                    f"http://127.0.0.1:{self.server.server_address[1]}",
                    f"http://localhost:{self.server.server_address[1]}",
                }
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if origin not in allowed_origins or content_type != "application/json":
                    self._json(HTTPStatus.FORBIDDEN, {"error": "origin_or_content_type_rejected"})
                    return
                try:
                    if self._body():
                        raise ValueError
                    payload = device_login.start() if self.path == "/v1/providers/codex/login/start" else device_login.cancel()
                    self._json(HTTPStatus.OK, payload)
                except LoginUnavailable:
                    self._json(HTTPStatus.CONFLICT, {"error": "codex_login_unavailable"})
                except (ValueError, TypeError):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
                except Exception:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})
                return
            match = _CAMPAIGN_PATH.fullmatch(urlsplit(self.path).path)
            if not match:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            campaign_id, endpoint, control_kind = match.group(1), match.group(2), match.group(3)
            try:
                body = self._body()
                idempotency_key = self.headers.get("Idempotency-Key", "")
                if not idempotency_key:
                    raise ValueError
                app = self._application(campaign_id)
                if endpoint == "graph/commands":
                    receipt = app.mutate_graph(body, idempotency_key)
                    self._json(HTTPStatus.OK, receipt)
                    return
                if endpoint == "v2/pivot-proposals":
                    proposal = PivotProposal.model_validate(body)
                    if idempotency_key != proposal.proposal_id:
                        raise ValueError
                    receipt = app.v2_record_pivot_proposal(proposal)
                    self._json(HTTPStatus.OK, receipt)
                    return
                if endpoint == "v2/pivot-decisions":
                    command = PivotDecisionCommand.model_validate(body)
                    if command.campaign_id != campaign_id or command.request_id != idempotency_key:
                        raise ValueError
                    receipt = app.v2_pivot_decision(command)
                    self._json(HTTPStatus.OK, receipt)
                    return
                if endpoint == "v2/pivot-executions":
                    if set(body) != {"proposal_id", "digest"}:
                        raise ValueError
                    receipt = app.v2_execute_pivot(
                        body["proposal_id"], body["digest"], idempotency_key,
                    )
                    self._json(HTTPStatus.OK, receipt)
                    return
                with app.open_store() as (store, selected_id):
                    if selected_id != campaign_id:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "campaign_unavailable"})
                        return
                    if control_kind is not None:
                        if set(body) != {"request_id"} or body["request_id"] != idempotency_key:
                            raise ValueError
                        receipt = app.control("end" if control_kind == "stop" else control_kind,
                                               idempotency_key, store=store)
                        self._json(HTTPStatus.OK, receipt)
                        return
                    if endpoint == "pivot-decisions":
                        command = PivotDecisionCommand.model_validate(body)
                        if command.campaign_id != campaign_id or command.request_id != idempotency_key:
                            raise ValueError
                        receipt = app.pivot_decision(command, store=store)
                        self._json(HTTPStatus.OK, CampaignApplication.pivot_receipt(campaign_id, receipt, store))
                        return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except OverflowError:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            except (ValueError, ValidationError, TypeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            except (RegistryError, StoreError):
                self._json(HTTPStatus.CONFLICT, {"error": "command_rejected"})
            except GraphStoreError:
                self._json(HTTPStatus.CONFLICT, {"error": "graph_command_rejected"})
            except Exception:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.shutdown_requested = threading.Event()  # type: ignore[attr-defined]
    original_shutdown = server.shutdown

    def shutdown() -> None:
        device_login.close()
        server.shutdown_requested.set()  # type: ignore[attr-defined]
        original_shutdown()

    server.shutdown = shutdown  # type: ignore[method-assign]
    return server

