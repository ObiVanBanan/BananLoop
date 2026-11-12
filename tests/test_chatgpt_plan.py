"""Offline SIWC contract tests: no browser sign-in, network or quota use."""
import hashlib
import io
import json
import os
import stat
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from bananloop.chatgpt_plan import (
    ChatGPTPlanAuth, SIWCVault, SIWCError, authorization_parameters,
    _validate_token_body, verify_id_token, JWKS, MODELS, TOKEN, SCOPES,
)
from bananloop.chatgpt_responses import (
    ChatGPTPlanResearcher, ChatGPTPlanOptimizer, ChatGPTPlanIntegrationVerifier,
    ChatGPTPlanProtocolVerifier, ChatGPTPlanResponseError, INSTRUCTIONS,
    collect_completed, _event_stream,
)


def _keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public["kid"] = "test-key-1"
    return private, {"keys": [public]}


class FakeHTTP:
    def __init__(self, *, claims=None, permission=True):
        self.private, self.jwks = _keys()
        self.claims = claims or {}
        self.permission = permission
        self.calls = []

    def id_token(self, *, client_id, nonce="nonce", sub="subject-1"):
        return jwt.encode({
            "iss": "https://auth.openai.com",
            "aud": client_id, "sub": sub, "nonce": nonce,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
            **self.claims,
        }, self.private, algorithm="RS256", headers={"kid": "test-key-1"})

    def request_json(self, method, url, *, data=None, headers=None):
        self.calls.append((method, url, data, headers))
        if url == JWKS:
            return self.jwks
        if url == MODELS:
            assert headers["Authorization"].startswith("Bearer ")
            return {"models": [
                {"slug": "gpt-6-luna", "display_name": "GPT-6 Luna", "visibility": "list"},
                {"slug": "hidden", "visibility": "hidden"},
            ]}
        if url == TOKEN:
            body = parse_qs(data.decode())
            client_id = body["client_id"][0]
            nonce = "nonce" if body["grant_type"][0] == "authorization_code" else None
            return {
                "access_token": "oauth-access-token", "refresh_token": "oauth-refresh",
                "id_token": self.id_token(client_id=client_id, nonce=nonce or "nonce"),
                "scope": SCOPES if self.permission else "openid profile email",
                "token_type": "Bearer", "expires_in": 3600,
            }
        raise AssertionError("unexpected network call")


def _auth(tmp_path, *, permission=True):
    http = FakeHTTP(permission=permission)
    auth = ChatGPTPlanAuth(SIWCVault(tmp_path / "vault" / "chatgpt.json"), http=http)
    return auth, http


def _connect(auth):
    auth.complete_login(
        {"state": "state", "code": "code", "client_id": "issued-client-id"},
        expected_state="state", expected_nonce="nonce",
        verifier="verifier", redirect_uri="http://127.0.0.1:34567/auth/callback",
    )


def test_official_pkce_dynamic_registration():
    data = authorization_parameters(
        "urn:uuid:16d5e285-35cd-4a2d-91b3-6e667dd01e8a",
        redirect_uri="http://127.0.0.1:12345/auth/callback",
        state="state", nonce="nonce", code_verifier="verifier",
    )
    assert data["client_id"] == "dynamic_agent_client"
    assert data["agent_name_hint"] == "BananLoop"
    assert data["resource"] == "https://api.openai.com/v1"
    assert data["code_challenge_method"] == "S256"
    assert data["code_challenge"] == "iMnq5o6zALKXGivsnlom_0F5_WYda32GHkxlV7mq7hQ"
    assert "chatgpt.tokens.use.direct" in data["scope"]
    returned = authorization_parameters(
        "urn:uuid:16d5e285-35cd-4a2d-91b3-6e667dd01e8a",
        client_id="issued-client-id", redirect_uri="http://127.0.0.1:12345/auth/callback",
        state="state", nonce="nonce", code_verifier="verifier",
    )
    assert "agent_name_hint" not in returned


def test_login_signature_verification_and_file_mode(tmp_path):
    auth, http = _auth(tmp_path)
    _connect(auth)
    state = auth.status()
    assert state["connected"]
    assert state["active"]["client_id"] == "issued-client-id"
    assert auth.models() == [{"slug": "gpt-6-luna", "display_name": "GPT-6 Luna"}]
    assert not any("sk-" in repr(call) for call in http.calls)
    if os.name != "nt":
        assert stat.S_IMODE(auth.vault.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(auth.vault.path.parent.stat().st_mode) == 0o700
    raw = auth.vault.read()
    assert raw["host_id"].startswith("urn:uuid:")
    assert raw["active_client_id"] == "issued-client-id"
    assert "oauth-access-token" not in json.dumps(auth.status())


def test_fail_closed_on_wrong_state_or_dynamic_client_id(tmp_path):
    auth, http = _auth(tmp_path)
    for callback in (
        {"state": "wrong", "code": "code", "client_id": "issued-client-id"},
        {"state": "state", "code": "code", "client_id": "dynamic_agent_client"},
        {"state": "state", "code": "code"},
    ):
        with pytest.raises(SIWCError):
            auth.complete_login(
                callback, expected_state="state", expected_nonce="nonce",
                verifier="verifier", redirect_uri="http://127.0.0.1:12345/auth/callback",
            )
    assert not auth.status()["connected"]


def test_rejected_scope_and_id_token_nonce(tmp_path):
    auth, http = _auth(tmp_path, permission=False)
    with pytest.raises(SIWCError, match="permission"):
        _connect(auth)
    assert not auth.status()["connected"]
    auth2, http2 = _auth(tmp_path / "other")
    with pytest.raises(SIWCError, match="nonce"):
        auth2.complete_login(
            {"state": "state", "code": "code", "client_id": "issued-client-id"},
            expected_state="state", expected_nonce="different-nonce",
            verifier="verifier", redirect_uri="http://127.0.0.1:12345/auth/callback",
        )


def test_jwks_rejects_invalid_signature_or_issuer(tmp_path):
    http = FakeHTTP()
    token = http.id_token(client_id="issued-client-id")
    assert verify_id_token(
        token, client_id="issued-client-id", expected_nonce="nonce", http=http
    )["sub"] == "subject-1"
    with pytest.raises(SIWCError):
        verify_id_token(token, client_id="other-client", expected_nonce="nonce", http=http)
    forged_http = FakeHTTP()
    with pytest.raises(SIWCError):
        verify_id_token(token, client_id="issued-client-id", expected_nonce="nonce",
                        http=forged_http)


def test_refresh_rotates_access_token_but_preserves_subject(tmp_path):
    auth, http = _auth(tmp_path)
    _connect(auth)
    data = auth.vault.read()
    data["accounts"][0]["expires_at"] = time.time() - 3
    auth.vault.write(data)
    assert auth.access_token() == "oauth-access-token"
    assert auth.vault.read()["active_client_id"] == "issued-client-id"
    assert any(call[1] == TOKEN and
               b"grant_type=refresh_token" in call[2] for call in http.calls)


def test_logout_clears_usable_secrets(tmp_path):
    auth, http = _auth(tmp_path)
    _connect(auth)
    auth.logout()
    assert auth.status()["connected"] is False
    assert "access_token" not in auth.vault.read()["accounts"][0]
    with pytest.raises(SIWCError):
        auth.access_token()


def test_symlink_or_world_readable_vault_is_rejected(tmp_path):
    vault = SIWCVault(tmp_path / "private" / "credentials.json")
    vault.ensure()
    if os.name != "nt":
        os.chmod(vault.path, 0o644)
        with pytest.raises(SIWCError):
            vault.read()
        os.chmod(vault.path, 0o600)
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(vault.path)
    except (OSError, NotImplementedError):
        pytest.skip("Windows runner does not permit creating symlinks")
    with pytest.raises(SIWCError, match="symlink"):
        SIWCVault(link).read()


def _sse(*events):
    body = b"".join(
        b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
    ) + b"data: [DONE]\n\n"
    return io.BytesIO(body)


def _finished(body='{"next_experiment":"repeat","artifact_text":{"note.txt":"proof"}}',
              usage=None):
    return [
        {"type": "response.output_text.delta", "delta": body},
        {"type": "response.completed", "response": {
            "id": "resp_mock", "status": "completed", "output": [],
            "usage": usage,
        }},
    ]


def test_sse_completes_with_real_usage():
    output, usage, rid = collect_completed(
        _sse(*_finished(usage={"input_tokens": 10, "output_tokens": 20}))
    )
    assert json.loads(output)["next_experiment"] == "repeat"
    assert usage["input_tokens"] == 10 and rid == "resp_mock"


@pytest.mark.parametrize("events", [
    ({"type": "response.output_text.delta", "delta": "partial"},),
    ({"type": "response.failed", "response": {"error": {"code": "quota"}}},),
    ({"type": "response.incomplete", "response": {}},),
])
def test_sse_fails_closed_without_completed(events):
    with pytest.raises(ChatGPTPlanResponseError):
        collect_completed(_sse(*events))


def test_sse_rejects_unframed_and_oversized_text():
    with pytest.raises(ChatGPTPlanResponseError):
        collect_completed(io.BytesIO(b"not an SSE line\n"))
    with pytest.raises(ChatGPTPlanResponseError):
        collect_completed(_sse(*_finished(body="x" * 270_000)))


class FakeResponse:
    status = 200

    def __init__(self, events):
        self.stream = _sse(*events)

    def readline(self, limit):
        return self.stream.readline(limit)

    def read(self, limit):
        return self.stream.read(limit)


class FakeConnection:
    def __init__(self, events):
        self.events = events
        self.sent = []
        self.closed = False

    def request(self, method, url, *, body, headers):
        self.sent.append((method, url, json.loads(body), headers))

    def getresponse(self):
        return FakeResponse(self.events)

    def close(self):
        self.closed = True


def test_official_response_payload_is_subscription_only(tmp_path):
    auth, http = _auth(tmp_path)
    _connect(auth)
    conn = FakeConnection(_finished())
    researcher = ChatGPTPlanResearcher(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
    )
    public = {"protocol": "rpdi13r-researcher-one-shot-v1",
              "role_context": {"public_task": "investigate"},
              "visible_documents": {"README.md": "hello"},
              "instruction": "test"}
    result = researcher(
        public, provider="openai", model="gpt-6-luna", model_version="siwc-v1",
        prompt_digest="a" * 64, max_model_tokens=100,
    )
    assert result["next_experiment"] == "repeat"
    assert researcher.last_status == "COMPLETED"
    assert researcher.last_usage is None  # unknown, not 0
    method, url, body, headers = conn.sent[0]
    assert (method, url) == ("POST", "/v1/responses")
    assert body["store"] is False and body["stream"] is True
    assert set(body) == {"model", "input", "store", "stream"}
    assert headers["Authorization"] == "Bearer oauth-access-token"
    assert "access_token" not in json.dumps(body)
    assert conn.closed


def test_registered_experiment_catalog_is_opt_in_to_researcher_transport(tmp_path):
    auth, _http = _auth(tmp_path)
    _connect(auth)
    conn = FakeConnection(_finished())
    catalog = [{"id": "primary_association", "description": "Estimate Spearman rho."}]
    public = {
        "protocol": "rpdi13r-researcher-one-shot-v1",
        "role_context": {"public_task": "investigate"},
        "visible_documents": {"TASK.md": "public task"},
        "instruction": "choose one registered analysis",
        "registered_experiments": catalog,
    }
    unconfigured = ChatGPTPlanResearcher(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
    )
    with pytest.raises(SIWCError, match="non-approved fields"):
        unconfigured(
            public, provider="openai", model="gpt-6-luna", model_version="siwc-v1",
            prompt_digest="a" * 64, max_model_tokens=100,
        )
    assert conn.sent == []  # schema mismatch fails before opening a provider request
    configured = ChatGPTPlanResearcher(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
        input_fields=frozenset({
            "protocol", "role_context", "visible_documents", "instruction",
            "registered_experiments",
        }),
    )
    configured(
        public, provider="openai", model="gpt-6-luna", model_version="siwc-v1",
        prompt_digest="a" * 64, max_model_tokens=100,
    )
    request_json = conn.sent[0][2]
    model_input = json.loads(request_json["input"][1]["content"])
    assert model_input["registered_experiments"] == catalog


def test_research_prompt_states_exact_lowercase_candidate_id_contract():
    assert "^[a-z0-9][a-z0-9_-]{0,79}$" in INSTRUCTIONS
    assert "never H1_mediated_effect" in INSTRUCTIONS
    assert "top-level evidence_refs " in INSTRUCTIONS
    assert "array of strings only" in INSTRUCTIONS
    assert "Never put claim-key/evidence-ID objects" in INSTRUCTIONS
    assert "ALWAYS return at least one" in INSTRUCTIONS
    assert "supplied dev_summary and evidence_refs" in INSTRUCTIONS
    assert "distinct follow-up" in INSTRUCTIONS
    assert "Core preserves structured plans as canonical JSON text" in INSTRUCTIONS


def test_provider_call_observer_preserves_receipt_without_model_text(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    conn = FakeConnection(_finished(usage={"input_tokens": 9, "output_tokens": 3}))
    receipts = []
    researcher = ChatGPTPlanResearcher(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
        call_observer=receipts.append,
    )
    public = {"protocol": "rpdi13r-researcher-one-shot-v1",
              "role_context": {"public_task": "investigate"},
              "visible_documents": {"README.md": "hello"}, "instruction": "test"}
    researcher(public, provider="openai", model="gpt-6-luna", model_version="siwc-v1",
               prompt_digest="a" * 64, max_model_tokens=100)
    assert receipts == [{"request_id": "resp_mock", "status": "COMPLETED",
                         "dispatched": True,
                         "usage": {"input_tokens": 9, "output_tokens": 3}}]
    assert "repeat" not in json.dumps(receipts)


def test_provider_call_observer_records_completed_response_before_schema_failure(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    receipts = []
    conn = FakeConnection(_finished(body="[]", usage={"input_tokens": 4}))
    researcher = ChatGPTPlanResearcher(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
        call_observer=receipts.append,
    )
    public = {"protocol": "rpdi13r-researcher-one-shot-v1",
              "role_context": {"public_task": "investigate"},
              "visible_documents": {"README.md": "hello"}, "instruction": "test"}
    with pytest.raises(ChatGPTPlanResponseError):
        researcher(public, provider="openai", model="gpt-6-luna", model_version="siwc-v1",
                   prompt_digest="a" * 64, max_model_tokens=100)
    assert receipts == [{"request_id": "resp_mock", "status": "FAILED",
                         "dispatched": True, "usage": {"input_tokens": 4}}]


def test_optimizer_siwc_protocol_is_pinned_and_tool_free(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    body = json.dumps({
        "hypothesis_id": "hyp-1", "rationale": "test a small change",
        "changes": {"src/app.py": "candidate\n"}, "limitations": [],
    })
    conn = FakeConnection(_finished(body=body))
    receipts = []
    optimizer = ChatGPTPlanOptimizer(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
        call_observer=receipts.append,
    )
    result = optimizer(
        {"protocol": "v2-optimizer-one-shot-v2", "role_context": {"public_task": "hyp-1"},
         "selected_hypothesis_id": "hyp-1",
         "visible_documents": {"src/app.py": "base\n"},
         "writable_paths": ["src/app.py"], "instruction": "bounded"},
        provider="openai", model="gpt-6-luna", model_version="pin-v1",
        prompt_digest="a" * 64, max_model_tokens=100,
    )
    assert result["hypothesis_id"] == "hyp-1"
    assert len(receipts) == 1 and receipts[0]["status"] == "COMPLETED"
    sent = conn.sent[0][2]
    assert sent["store"] is False and sent["stream"] is True
    assert "commands" in sent["input"][0]["content"]
    assert "Authorization" not in json.dumps(sent)
    user_input = json.loads(sent["input"][1]["content"])
    assert user_input["writable_paths"] == ["src/app.py"]
    assert user_input["selected_hypothesis_id"] == "hyp-1"
    assert optimizer.last_response == result


def test_integration_verifier_siwc_receives_only_core_summary(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    output = {
        "candidate_snapshot_id": "a" * 40, "protocol_digest": "b" *  64,
        "decision": "FAIL", "checks": ["candidate_identity"],
        "diagnostics": ["Core evidence reports a failed check"],
    }
    conn = FakeConnection(_finished(body=json.dumps(output)))
    verifier = ChatGPTPlanIntegrationVerifier(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
    )
    request = {
        "schema": "integration-verification-v1", "campaign_id": "c1",
        "spec_hash": "c" * 64, "source_snapshot_id": "d" * 40,
        "approval_digest": "e" * 64, "protocol_id": "p", "protocol_version": 1,
        "protocol_digest": "b" * 64, "candidate_snapshot_id": "a" * 40,
        "core_evidence_digest": "f" * 64, "role_binding": {},
        "role_context": {"public_task": "Core evidence summary"},
        "team_manifest_digest": "1" * 64, "role_registry_digest": "2" * 64,
    }
    result = verifier._dispatch_verifier(
        request, None, provider="openai", model="gpt-6-luna",
        model_version="pin-v1", prompt_digest="3" * 64, max_model_tokens=100,
    )
    assert result["decision"] == "FAIL"
    user_input = json.loads(conn.sent[0][2]["input"][1]["content"])
    assert user_input["protocol"] == "v2-integration-verifier-one-shot-v1"
    assert user_input["role_context"] == request["role_context"]
    assert set(user_input) == {
        "protocol", "role_context", "candidate_snapshot_id", "protocol_digest",
    }
    assert "access_token" not in json.dumps(user_input)


def test_protocol_verifier_siwc_receives_only_bound_public_review_material(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    proposal_digest = "a" * 64
    output = {
        "proposal_digest": proposal_digest, "decision": "REJECT",
        "checks": ["protocol_identity"], "rationale": "The public evidence is insufficient.",
    }
    conn = FakeConnection(_finished(body=json.dumps(output)))
    verifier = ChatGPTPlanProtocolVerifier(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
    )
    request = {
        "schema": "evaluator-protocol-verification-v1", "campaign_id": "c1",
        "source_spec_hash": "b" * 64, "source_snapshot_id": "c" * 40,
        "protocol_id": "toy-v1", "protocol_version": 1,
        "protocol_digest": "d" * 64, "proposal_digest": proposal_digest,
        "review_material_digest": "e" * 64, "role_binding": {},
        "role_context": {"public_task": "Bounded public review material"},
        "team_manifest_digest": "f" * 64, "role_registry_digest": "1" * 64,
    }
    result = verifier._dispatch_verifier(
        request, None, provider="openai", model="gpt-6-luna",
        model_version="pin-v1", prompt_digest="2" * 64, max_model_tokens=100,
    )
    assert result["decision"] == "REJECT"
    user_input = json.loads(conn.sent[0][2]["input"][1]["content"])
    assert user_input == {
        "protocol": "evaluator-protocol-verification-v1",
        "role_context": request["role_context"], "proposal_digest": proposal_digest,
    }
    assert "holdout" not in json.dumps(user_input).lower()


def test_wrong_pin_or_unapproved_input_avoids_model_call(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    conn = FakeConnection(_finished())
    researcher = ChatGPTPlanResearcher(
        auth, approved_model="gpt-6-luna", transport_factory=lambda *args, **kwargs: conn,
    )
    with pytest.raises(SIWCError, match="match frozen"):
        researcher(
            {"protocol": "rpdi13r-researcher-one-shot-v1", "role_context": {},
             "visible_documents": {}, "instruction": "x"},
            provider="openai", model="other", model_version="v",
            prompt_digest="a" * 64, max_model_tokens=100,
        )
    assert conn.sent == []


def test_catalog_must_include_pinned_slug(tmp_path):
    auth, _ = _auth(tmp_path)
    _connect(auth)
    researcher = ChatGPTPlanResearcher(auth, approved_model="unavailable")
    with pytest.raises(SIWCError, match="missing"):
        researcher.check_model()


def test_live_owner_smoke_requires_explicit_approval_before_any_state(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1]
    work_dir = tmp_path / "untouched"
    script = source / "examples" / "v2_research_diagnostic" / "run_one_chatgpt.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--work-dir", str(work_dir)],
        cwd=source, capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode != 0
    assert "NO MODEL CALL MADE" in completed.stderr
    assert not work_dir.exists()


def test_live_smoke_result_is_atomically_persisted_with_unknown_usage(tmp_path):
    from examples.v2_research_diagnostic.run_one_chatgpt import _persist_result

    state_root = tmp_path / "campaign"
    state_root.mkdir()
    result = {"status": "SIWC_ONE_SHOT_EXPLORATION_FAILED", "usage": "UNKNOWN"}
    target = _persist_result(state_root, result)
    assert target == state_root / "live-smoke-result.json"
    assert json.loads(target.read_text()) == result
    assert result["result_path"] == str(target)
