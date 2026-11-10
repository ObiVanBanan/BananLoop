"""Explicitly billable Responses API role diagnostics with auditable host receipts.

Provider usage is read from an authenticated HTTPS response and checked against
locally pinned rates. Ed25519 signature authenticates the *operator host*, NOT
OpenAI; pricing is an estimate until reconciled with the actual provider bill.
No ChatGPT Plus subscription entitlements apply to this API path.
"""
from __future__ import annotations

import argparse
import base64
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .agent_benchmark import BenchmarkError, canonical, digest, read_json, _write
from .benchmark_live_codex import _preflight, validate_role_reply
from .roles import RoleRegistryV1, TeamManifestV1

URL = "https://api.openai.com/v1/responses"
MAX_REQUEST_BYTES = 150_000
MAX_RESPONSE_BYTES = 2_000_000


class MeteringError(BenchmarkError):
    """A metered provider call cannot be safely charged, verified or continued."""


def _price(pricing: dict, model_id: str) -> tuple[Decimal, Decimal, str]:
    if (not isinstance(pricing, dict) or set(pricing) != {
            "schema_version", "model", "input_usd_per_million", "output_usd_per_million"}
            or pricing["schema_version"] != 1 or pricing["model"] != model_id):
        raise MeteringError("pricing rate card does not match the frozen model ID")
    try:
        inbound, outbound = (Decimal(pricing[field]) for field in
                             ("input_usd_per_million", "output_usd_per_million"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MeteringError("pricing is not a frozen decimal rate card") from exc
    if any(not v.is_finite() or v <= 0 for v in (inbound, outbound)):
        raise MeteringError("pricing rates must be positive finite decimals")
    return inbound, outbound, digest(pricing)


def _micro_cost(input_tokens: int, output_tokens: int,
                input_price: Decimal, output_price: Decimal) -> int:
    # USD per million tokens * tokens == microUSD.
    return int((Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price)
               .to_integral_value(rounding=ROUND_CEILING))


def _strict_usage(response: dict, expected_model: str) -> tuple[dict, str, str]:
    if (not isinstance(response, dict) or response.get("status") != "completed"
            or response.get("model") != expected_model
            or not isinstance(response.get("id"), str)
            or not response["id"].startswith("resp_")):
        raise MeteringError("provider response status, model or ID does not match the frozen request")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise MeteringError("provider supplied no measured token usage")
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if type(usage.get(field)) is not int or usage[field] < 0:
            raise MeteringError("provider token usage is missing or malformed")
    if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        raise MeteringError("provider total tokens do not reconcile")
    cached = usage.get("input_tokens_details", {}).get("cached_tokens")
    if type(cached) is not int or not 0 <= cached <= usage["input_tokens"]:
        raise MeteringError("provider cache usage is missing or inconsistent")
    outputs = response.get("output")
    if not isinstance(outputs, list):
        raise MeteringError("provider message output missing")
    parts = []
    for message in outputs:
        if not isinstance(message, dict):
            raise MeteringError("provider output item is malformed")
        if message.get("type") == "reasoning":
            continue  # Non-text reasoning metadata is not a tool or an answer.
        if message.get("type") != "message":
            raise MeteringError("provider returned a tool or non-message output")
        for piece in message.get("content", []):
            if not isinstance(piece, dict) or piece.get("type") != "output_text":
                raise MeteringError("provider output contains non-text content")
            parts.append(piece.get("text"))
    if len(parts) != 1 or not isinstance(parts[0], str) or len(parts[0].encode("utf-8")) > 128_000:
        raise MeteringError("provider returned missing/oversized model reply")
    return {"input_tokens": usage["input_tokens"], "cached_input_tokens": cached,
            "output_tokens": usage["output_tokens"]}, response["id"], parts[0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise MeteringError("refusing provider redirect: API authorization cannot move hosts")


def https_provider_once(payload: bytes, *, api_key: str, timeout_seconds: int) -> dict:
    """One fixed-origin HTTPS POST. No retries and no user-supplied target URL."""
    if not api_key or len(payload) > MAX_REQUEST_BYTES:
        raise MeteringError("API credential or bounded request missing")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect(),
    )
    req = urllib.request.Request(
        URL, data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "Idempotency-Key": hashlib.sha256(payload).hexdigest()},
    )
    try:
        with opener.open(req, timeout=timeout_seconds) as reply:
            if reply.status != 200:
                raise MeteringError("provider returned a non-success HTTP status")
            raw = reply.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        # Even a read timeout can have incurred provider usage: do not retry.
        raise MeteringError("provider status/settlement unknown; do not retry") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MeteringError("provider response too large; settlement unknown")
    try:
        response = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MeteringError("provider returned unparseable JSON; settlement unknown") from exc
    return response


def _operator_signing_key(path: str | Path) -> Ed25519PrivateKey:
    if os.name != "posix":
        raise MeteringError("API meter signer requires reviewed POSIX private-key permission checks")
    source = Path(path)
    if not source.is_file() or source.is_symlink() or source.stat().st_mode & 0o077:
        raise MeteringError("operator Ed25519 signing key must be a private regular file, mode 0600")
    try:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(source.read_text().strip()))
    except (ValueError, OSError) as exc:
        raise MeteringError("invalid operator Ed25519 private key") from exc


def _verify_accounting_authorization(auth: dict, public: dict, registry: RoleRegistryV1,
                                     team: TeamManifestV1, pricing_sha256: str) -> list[dict]:
    required = {"schema_version", "mode", "suite_sha256", "registry_sha256",
                "team_manifest_sha256", "pricing_sha256", "max_calls", "max_spend_microusd",
                "max_input_token_bound_per_call", "max_output_tokens_per_call",
                "max_wall_seconds_per_call", "owner_approves_separate_api_billing"}
    if (not isinstance(auth, dict) or set(auth) != required
            or auth["schema_version"] != 1 or auth["mode"] != "OPENAI_API_SEPARATELY_BILLED_DIAGNOSTIC"
            or auth["owner_approves_separate_api_billing"] is not True
            or auth["suite_sha256"] != digest(public) or auth["registry_sha256"] != registry.digest()
            or auth["team_manifest_sha256"] != team.manifest_digest or auth["pricing_sha256"] != pricing_sha256):
        raise MeteringError("separately billed API run is not explicitly authorized for exact pins")
    for key in ("max_calls", "max_spend_microusd", "max_input_token_bound_per_call",
                "max_output_tokens_per_call", "max_wall_seconds_per_call"):
        if type(auth[key]) is not int or auth[key] < 1:
            raise MeteringError("API budget missing or invalid")
    if not 1 <= auth["max_output_tokens_per_call"] <= 8192:
        raise MeteringError("output token ceiling must be bounded to 8192")
    # The shared model-only role preflight verifies pins, split, rights and tools.
    role_auth = {
        "schema_version": 1, "mode": "CODEX_SUBSCRIPTION_NONQUALIFYING_DIAGNOSTIC",
        "suite_sha256": digest(public), "registry_sha256": registry.digest(),
        "team_manifest_sha256": team.manifest_digest,
        "max_cases": auth["max_calls"],
        "max_observed_input_tokens": auth["max_input_token_bound_per_call"] * auth["max_calls"],
        "max_observed_output_tokens": auth["max_output_tokens_per_call"] * auth["max_calls"],
        "max_wall_seconds_per_call": auth["max_wall_seconds_per_call"],
        "acknowledge_subscription_usage_unverified": True,
        "acknowledge_token_caps_observational": True,
    }
    return _preflight(public, registry, team, role_auth)


def run_api(*, cases_path: str, registry_path: str, team_path: str,
            authorization_path: str, pricing_path: str, signing_key_path: str,
            output_dir: str, api_key: str,
            transport: Callable[..., dict] = https_provider_once) -> dict:
    public, auth = read_json(cases_path), read_json(authorization_path)
    registry, team = (RoleRegistryV1.model_validate(read_json(registry_path)),
                      TeamManifestV1.model_validate(read_json(team_path)))
    pricing = read_json(pricing_path)
    model_ids = {registry.role(c["role_id"]).model for c in public["cases"]}
    if len(model_ids) != 1:
        raise MeteringError("all cases must share one pinned priced model")
    model_id = next(iter(model_ids))
    in_rate, out_rate, price_pin = _price(pricing, model_id)
    cases = _verify_accounting_authorization(auth, public, registry, team, price_pin)
    key = _operator_signing_key(signing_key_path)
    if not api_key or not api_key.startswith("sk-"):
        raise MeteringError("OPENAI_API_KEY missing; subscription logins cannot be billed as API")
    output = Path(output_dir)
    if output.exists():
        raise MeteringError("existing API output cannot be overwritten")
    output.mkdir(parents=True, mode=0o700)
    responses, receipts = {}, []
    used_microusd = 0
    state = "IN_PROGRESS"
    prev_sha = "0" * 64
    try:
        for case in cases:
            prompt = canonical({
                "role_protocol": "v0.2-model-reply-api-diagnostic",
                "role_id": case["role_id"], "case_id": case["case_id"],
                "task": case["agent_input"],
                "instruction": ("Return only a JSON object matching the pinned role's model reply contract. "
                                "Do not infer private oracle results."),
            })
            payload = canonical({
                "model": model_id, "input": prompt.decode("utf-8"),
                "max_output_tokens": auth["max_output_tokens_per_call"],
                "store": False, "truncation": "disabled",
                "text": {"format": {"type": "json_object"}},
                "tools": [],
            })
            if len(payload) > MAX_REQUEST_BYTES:
                raise MeteringError("provider payload too large before dispatch")
            # Conservative local reservation; not a provider-certified input token
            # ceiling. Abort if the bound is larger than the owner-approved cap.
            input_bound = len(payload) + 1024
            if input_bound > auth["max_input_token_bound_per_call"]:
                raise MeteringError("input byte-derived bound exceeds approved input ceiling")
            reserved = _micro_cost(input_bound, auth["max_output_tokens_per_call"], in_rate, out_rate)
            if used_microusd + reserved > auth["max_spend_microusd"]:
                state = "STOPPED_BEFORE_OVERSPEND"
                break
            cid = case["case_id"]
            attempt = {"case_id": cid, "role_id": case["role_id"], "model": model_id,
                       "request_sha256": hashlib.sha256(payload).hexdigest(),
                       "prev_sha256": prev_sha,
                       "status": "STARTED_UNKNOWN_SETTLEMENT",
                       "reserved_worst_case_microusd": reserved}
            receipts.append(attempt)
            _write(output / "api-meter.json", {"schema_version": 1, "records": receipts})
            try:
                raw = transport(payload, api_key=api_key,
                                timeout_seconds=case["resource_budget"]["wall_clock_seconds"])
                usage, response_id, answer_text = _strict_usage(raw, model_id)
                measured_cost = _micro_cost(usage["input_tokens"], usage["output_tokens"], in_rate, out_rate)
                # Account for a billable provider response BEFORE evaluating
                # model quality. Invalid answers still consume real tokens.
                attempt.update({
                    "status": "METERED_PENDING_ROLE_VALIDATION", "provider_response_id": response_id,
                    "provider_usage": usage, "calculated_cost_microusd": measured_cost,
                    "provider_response_sha256": digest(raw), "pricing_sha256": price_pin,
                    "usage_source": "AUTHENTICATED_OPENAI_RESPONSES_API",
                    "provider_crypto_signature": False,
                })
                used_microusd += measured_cost
                if usage["input_tokens"] > input_bound or measured_cost > reserved:
                    raise MeteringError("provider usage exceeded conservative local reservation; stop")
                answer = json.loads(answer_text)
                validated = validate_role_reply(case["role_id"], answer, case["agent_input"])
                attempt["status"] = "COMPLETED_METERED_API"
                attempt["response_sha256"] = digest(validated)
                responses[cid] = validated
            except Exception as exc:
                attempt["status"] = ("STOPPED_METERED_AGENT_OR_BOUND_ERROR"
                                     if "provider_usage" in attempt else
                                     "STOPPED_UNKNOWN_OR_INVALID_SETTLEMENT")
                attempt["error_type"] = type(exc).__name__
                state = ("STOPPED_METERED_INVALID_OUTPUT_OR_BOUND"
                         if "provider_usage" in attempt else "STOPPED_UNKNOWN_SETTLEMENT")
                break
            finally:
                signed_bytes = canonical(attempt)
                attempt["operator_signature_b64"] = base64.b64encode(key.sign(signed_bytes)).decode("ascii")
                attempt["receipt_sha256"] = digest(attempt)
                prev_sha = attempt["receipt_sha256"]
                _write(output / "api-meter.json", {"schema_version": 1, "records": receipts,
                                                   "chain_head_sha256": prev_sha,
                                                   "signature_source": "OPERATOR_NOT_PROVIDER"})
                _write(output / "responses.json", {"schema_version": 1, "responses": responses})
        else:
            state = "COMPLETED_METERED_API_DIAGNOSTIC"
    finally:
        _write(output / "run-status.json", {
            "schema_version": 1, "status": state,
            "suite_sha256": digest(public),
            "registry_sha256": registry.digest(),
            "team_manifest_sha256": team.manifest_digest,
            "attempted_calls": len(receipts), "completed_calls": len(responses),
            "measured_priced_cost_microusd": used_microusd,
            "actual_provider_invoice_verified": False,
            "provider_reported_usage_observed": any("provider_usage" in row for row in receipts),
            "usage_receipts_operator_signed": len(receipts) > 0,
            "release_eligible": False,
        })
    return read_json(output / "run-status.json")


def verify_operator_meter(*, receipts_path: str, public_key_hex: str) -> dict:
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    except ValueError as exc:
        raise MeteringError("invalid registered operator public key") from exc
    document = read_json(receipts_path)
    previous = "0" * 64
    if not isinstance(document, dict) or not isinstance(document.get("records"), list):
        raise MeteringError("missing operator receipt ledger")
    for row in document["records"]:
        receipt = dict(row)
        signature = receipt.pop("operator_signature_b64", None)
        expected_sha = receipt.pop("receipt_sha256", None)
        if receipt.get("prev_sha256") != previous or not isinstance(signature, str):
            raise MeteringError("operator receipt chain is incomplete")
        try:
            key.verify(base64.b64decode(signature, validate=True), canonical(receipt))
        except Exception as exc:
            raise MeteringError("operator receipt has no valid registered signature") from exc
        if digest(row) == expected_sha:  # receipt hash excludes the hash itself
            raise MeteringError("receipt self-digest is not canonical")
        if digest({k: v for k, v in row.items() if k != "receipt_sha256"}) != expected_sha:
            raise MeteringError("operator receipt digest changed")
        previous = expected_sha
    if document.get("chain_head_sha256") != previous:
        raise MeteringError("final receipt chain head mismatch")
    return {"verified_operator_receipts": len(document["records"]),
            "chain_head_sha256": previous,
            "provider_signed": False, "invoice_reconciled": False}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Opt-in separately-billed Responses API diagnostic")
    cmd = p.add_subparsers(dest="command", required=True)
    run = cmd.add_parser("run", help="One-call per case, no automatic retries")
    for name in ("cases", "registry", "team", "authorization", "pricing", "signing-key", "out"):
        run.add_argument("--" + name, required=True)
    run.add_argument("--i-authorize-separate-api-charges", action="store_true")
    verify = cmd.add_parser("verify-meter", help="Verify independent operator signature chain")
    verify.add_argument("--receipts", required=True)
    verify.add_argument("--operator-public-key-hex", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "run":
            if not args.i_authorize_separate_api_charges:
                p.error("explicit --i-authorize-separate-api-charges is required")
            result = run_api(cases_path=args.cases, registry_path=args.registry,
                             team_path=args.team, authorization_path=args.authorization,
                             pricing_path=args.pricing, signing_key_path=args.signing_key,
                             output_dir=args.out, api_key=os.environ.get("OPENAI_API_KEY", ""))
        else:
            result = verify_operator_meter(receipts_path=args.receipts,
                                           public_key_hex=args.operator_public_key_hex)
    except (MeteringError, BenchmarkError, ValueError) as exc:
        p.exit(2, f"metered benchmark refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
