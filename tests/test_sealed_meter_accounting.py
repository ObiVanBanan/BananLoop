"""Offline verification of accounting provenance joined to private role scoring."""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import pytest

from bananloop.agent_benchmark import BenchmarkError, _write, canonical, digest, read_json
from bananloop.benchmark_sealed import score_sealed
from test_benchmark_batches import _sealed


def _api_ledger(path, case_id):
    key = Ed25519PrivateKey.generate()
    row = {
        "case_id": case_id, "role_id": "researcher",
        "model": "deterministic-demo",
        "status": "COMPLETED_METERED_API",
        "prev_sha256": "0" * 64,
        "provider_usage": {"input_tokens": 21, "output_tokens": 8,
                           "cached_input_tokens": 0},
        "calculated_cost_microusd": 3,
        "usage_source": "AUTHENTICATED_OPENAI_RESPONSES_API",
    }
    row["operator_signature_b64"] = base64.b64encode(key.sign(canonical(row))).decode("ascii")
    row["receipt_sha256"] = digest(row)
    _write(path, {"schema_version": 1, "records": [row],
                  "signature_source": "OPERATOR_NOT_PROVIDER",
                  "chain_head_sha256": row["receipt_sha256"]})
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def test_signed_api_usage_is_attributed_without_claiming_provider_signature(tmp_path):
    public, private = _sealed(tmp_path)
    case_id = read_json(public / "cases.json")["cases"][0]["case_id"]
    ledger = tmp_path / "api-meter.json"
    pubkey = _api_ledger(ledger, case_id)
    responses = tmp_path / "responses.json"
    _write(responses, {"schema_version": 1, "responses": {}})
    result = score_sealed(public_dir=public, private_dir=private,
                          responses_path=responses, output_dir=tmp_path / "scored",
                          meter_ledgers=[str(ledger)], operator_public_key_hex=pubkey)
    accounting = result["accounting"]
    assert accounting["attempted"] == 1
    assert accounting["measured_calls"] == 1
    assert accounting["api_operator_signed_calls"] == 1
    assert accounting["api_locally_priced_microusd"] == 3
    assert accounting["provider_cryptographically_signed"] is False
    assert accounting["provider_invoice_reconciled"] is False
    assert sum(x["completed"] for x in result["splits"].values()) == 0


def test_unsigned_api_meter_is_rejected_before_scoring(tmp_path):
    public, private = _sealed(tmp_path)
    case_id = read_json(public / "cases.json")["cases"][0]["case_id"]
    ledger = tmp_path / "api-meter.json"
    key = _api_ledger(ledger, case_id)
    contents = read_json(ledger)
    contents["records"][0]["provider_usage"]["input_tokens"] = 999
    _write(ledger, contents)
    responses = tmp_path / "responses.json"
    _write(responses, {"schema_version": 1, "responses": {}})
    with pytest.raises(BenchmarkError):
        score_sealed(public_dir=public, private_dir=private,
                     responses_path=responses, output_dir=tmp_path / "scored",
                     meter_ledgers=[str(ledger)], operator_public_key_hex=key)
    assert not (tmp_path / "scored").exists()


def test_codex_unsigned_usage_not_misrepresented_as_provider_cost(tmp_path):
    public, private = _sealed(tmp_path)
    case_id = read_json(public / "cases.json")["cases"][0]["case_id"]
    ledger = tmp_path / "codex-observed.json"
    row = {
        "case_id": case_id, "role_id": "researcher",
        "model_id": "deterministic-demo",
        "status": "COMPLETED", "previous_sha256": "0" * 64,
        "usage": {"input_tokens": 70, "cached_input_tokens": 3, "output_tokens": 18},
    }
    row["entry_sha256"] = digest(row)
    _write(ledger, {
        "schema_version": 1, "usage_authority": "UNSIGNED_OBSERVATION_NOT_PROVIDER_RECEIPT",
        "records": [row], "chain_head_sha256": row["entry_sha256"],
    })
    responses = tmp_path / "responses.json"
    _write(responses, {"schema_version": 1, "responses": {}})
    result = score_sealed(public_dir=public, private_dir=private,
                          responses_path=responses, output_dir=tmp_path / "scored",
                          meter_ledgers=[str(ledger)])
    assert result["accounting"]["cli_observed_calls"] == 1
    assert result["accounting"]["api_locally_priced_microusd"] == 0
    assert result["accounting"]["provider_invoice_reconciled"] is False
