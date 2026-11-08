"""Mock-transport tests; NEVER call OpenAI or spend tokens during CI."""
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import pytest

from bananloop.agent_benchmark import _write, digest, read_json
from bananloop.benchmark_metered_api import (
    MeteringError, _price, _strict_usage, _micro_cost, run_api, verify_operator_meter,
)
from test_live_sealed_benchmark import _pinned_codex_one_case


def _approval(root):
    public, reg, team, _ = _pinned_codex_one_case(root)
    price = {"schema_version": 1, "model": "gpt-6-luna",
             "input_usd_per_million": "1", "output_usd_per_million": "2"}
    auth = {
        "schema_version": 1, "mode": "OPENAI_API_SEPARATELY_BILLED_DIAGNOSTIC",
        "suite_sha256": digest(public), "registry_sha256": reg.digest(),
        "team_manifest_sha256": team.manifest_digest, "pricing_sha256": digest(price),
        "max_calls": 1, "max_spend_microusd": 20000,
        "max_input_token_bound_per_call": 16000, "max_output_tokens_per_call": 128,
        "max_wall_seconds_per_call": 10,
        "owner_approves_separate_api_billing": True,
    }
    _write(root / "pricing.json", price)
    _write(root / "api-authorization.json", auth)
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    key = root / "signing-secret.key"
    key.write_text(raw.hex())
    key.chmod(0o600)
    pub = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return pub


def _provider_response(answer=None, usage=None):
    return {
        "id": "resp_test_001", "model": "gpt-6-luna", "status": "completed",
        "usage": usage if usage is not None else {
            "input_tokens": 100, "output_tokens": 35, "total_tokens": 135,
            "input_tokens_details": {"cached_tokens": 0},
        },
        "output": [
            {"type": "reasoning", "id": "rs_01"},
            {"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(answer or {
                    "research_findings": [{"claim": "sanitized DEV finding"}],
                })},
            ]},
        ],
    }


def _execute(root, transport):
    return run_api(
        cases_path=root / "cases-codex.json",
        registry_path=root / "registry-codex.json",
        team_path=root / "team-codex.json",
        authorization_path=root / "api-authorization.json",
        pricing_path=root / "pricing.json",
        signing_key_path=root / "signing-secret.key",
        output_dir=root / "api-output", api_key="sk-test-fake-never-transmitted",
        transport=transport,
    )


def test_authenticated_usage_and_operator_signed_accounting_without_any_real_api(tmp_path):
    root = tmp_path / "fixture"
    pub = _approval(root)
    messages = []
    def fake_https(payload, *, api_key, timeout_seconds):
        messages.append(json.loads(payload))
        assert api_key.startswith("sk-test")
        assert timeout_seconds == 5
        return _provider_response()
    status = _execute(root, fake_https)
    assert status["status"] == "COMPLETED_METERED_API_DIAGNOSTIC"
    assert status["measured_priced_cost_microusd"] == 170
    assert status["actual_provider_invoice_verified"] is False
    assert messages[0]["model"] == "gpt-6-luna"
    assert messages[0]["max_output_tokens"] == 128
    receipt = read_json(root / "api-output" / "api-meter.json")["records"][0]
    assert receipt["provider_response_id"] == "resp_test_001"
    assert receipt["usage_source"] == "AUTHENTICATED_OPENAI_RESPONSES_API"
    check = verify_operator_meter(receipts_path=root / "api-output" / "api-meter.json",
                                  public_key_hex=pub)
    assert check["verified_operator_receipts"] == 1
    assert check["provider_signed"] is False


def test_operator_receipt_tampering_is_detected(tmp_path):
    root = tmp_path / "fixture"
    pub = _approval(root)
    _execute(root, lambda *_a, **_kw: _provider_response())
    ledger_path = root / "api-output" / "api-meter.json"
    ledger = read_json(ledger_path)
    ledger["records"][0]["provider_usage"]["output_tokens"] = 99999
    _write(ledger_path, ledger)
    with pytest.raises(MeteringError, match="signature"):
        verify_operator_meter(receipts_path=ledger_path, public_key_hex=pub)


def test_provider_usage_missing_or_mismatched_fails_closed_without_retry(tmp_path):
    root = tmp_path / "fixture"
    _approval(root)
    seen = []
    def fake(*_a, **_kw):
        seen.append(1)
        response = _provider_response()
        del response["usage"]
        return response
    status = _execute(root, fake)
    assert seen == [1]
    assert status["status"] == "STOPPED_UNKNOWN_SETTLEMENT"
    assert status["completed_calls"] == 0
    assert read_json(root / "api-output" / "api-meter.json")["records"][0]["status"] == (
        "STOPPED_UNKNOWN_OR_INVALID_SETTLEMENT")


def test_api_explicit_authorization_prevents_any_transport(tmp_path):
    root = tmp_path / "fixture"
    _approval(root)
    auth_path = root / "api-authorization.json"
    auth = read_json(auth_path)
    auth["owner_approves_separate_api_billing"] = False
    _write(auth_path, auth)
    called = []
    with pytest.raises(MeteringError, match="authorized"):
        _execute(root, lambda *_a, **_kw: called.append(True))
    assert called == []


def test_priced_worst_case_and_provider_usage_checks_are_exact():
    price = {"schema_version": 1, "model": "gpt-6-luna",
             "input_usd_per_million": "1.25", "output_usd_per_million": "2.5"}
    inp, out, pin = _price(price, "gpt-6-luna")
    assert _micro_cost(1000, 300, inp, out) == 2000
    assert pin == digest(price)
    with pytest.raises(MeteringError, match="model"):
        _price(price, "different-model")
    response = _provider_response()
    response["usage"]["total_tokens"] += 1
    with pytest.raises(MeteringError, match="reconcile"):
        _strict_usage(response, "gpt-6-luna")
