from __future__ import annotations

import base64
import hashlib

import pytest

from bananloop.contracts import ProtocolManifest
from bananloop.evaluator_fixture import EvaluatorFixtureV1
from bananloop.evaluator_verification import EvaluatorProtocolIntentV1
from bananloop.trusted_evaluator import _case_protocol_digest
from bananloop.v2_research_runtime import V2RuntimeError, make_researcher_invoke


def _fixture() -> EvaluatorFixtureV1:
    encode = lambda value: base64.b64encode(value).decode("ascii")
    return EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(b"trusted evaluator archive"),
        "entrypoint": ["python3", "{evaluator}/evaluate.py"],
        "tiers": [
            {"tier": "dev", "cases": [
                {"case_id": "dev-a", "input_base64": encode(b"public input")},
            ]},
            {"tier": "holdout", "cases": [
                {"case_id": "holdout-a", "input_base64": encode(b"private input")},
            ]},
            {"tier": "validation", "cases": [
                {"case_id": "validation-a", "input_base64": encode(b"validation input")},
            ]},
        ],
    })


def test_fixture_binds_metric_intent_to_trusted_bundle_and_tier_manifests():
    fixture = _fixture()
    intent = EvaluatorProtocolIntentV1(
        protocol_id="fixture-smoke", version=1, metric_id="accuracy",
        metric_field="accuracy", direction="MAXIMIZE", aggregation="MEAN",
    )
    source = "a" * 40
    draft, artifacts = fixture.bind(intent, source_snapshot_id=source)

    protocol = draft.protocol
    assert protocol.source_snapshot_id == source
    assert protocol.evaluator_bundle_sha256 == hashlib.sha256(b"trusted evaluator archive").hexdigest()
    assert draft.evidence_refs == (f"artifact://sha256/{protocol.evaluator_bundle_sha256}",)
    assert {item.tier for item in protocol.tiers} == {"dev", "validation", "holdout"}
    assert "protocol" not in fixture.model_dump()
    tier_names = ("dev", "holdout", "validation")
    for tier_index, tier_name in enumerate(tier_names):
        tier = next(item for item in protocol.tiers if item.tier == tier_name)
        tier_key = f"tier-{tier_index}"
        manifest_bytes = artifacts[f"core-assets/manifests/{tier_key}.json"]
        assert hashlib.sha256(manifest_bytes).hexdigest() == tier.manifest_sha256
        manifest = ProtocolManifest.model_validate_json(manifest_bytes)
        assert manifest.protocol_sha256 == _case_protocol_digest(protocol)
        assert len(manifest.cases) == tier.case_count == 1
        case_digest = manifest.cases[0].input_sha256
        assert artifacts[f"core-assets/inputs/{tier_key}/{case_digest}.bin"]
    assert all("evaluator" not in name and "holdout" not in name and "validation" not in name
               for name in artifacts)


def test_fixture_digest_is_canonical_and_rejects_model_created_hashes():
    fixture = _fixture()
    assert hashlib.sha256(fixture.canonical_bytes()).hexdigest() == fixture.content_digest()
    payload = fixture.model_dump(mode="json")
    payload["protocol"] = {"protocol_id": "model-controlled"}
    with pytest.raises(ValueError):
        EvaluatorFixtureV1.model_validate(payload)


def test_fixture_requires_canonical_three_tier_scope():
    payload = _fixture().model_dump(mode="json")
    payload["tiers"] = payload["tiers"][1:]
    with pytest.raises(ValueError):
        EvaluatorFixtureV1.model_validate(payload)


def test_public_only_fixture_binds_dev_and_validation_without_holdout():
    fixture = _fixture().model_copy(update={
        "tiers": (_fixture().tiers[0], _fixture().tiers[2]),
        "execution_backend_id": "autoresearchexam-public-v1",
    })
    intent = EvaluatorProtocolIntentV1(
        protocol_id="public-only", version=1, metric_id="r2",
        metric_field="r2", direction="MAXIMIZE", aggregation="MEAN",
    )
    draft, artifacts = fixture.bind(intent, source_snapshot_id="a" * 40)
    assert tuple(tier.tier for tier in draft.protocol.tiers) == ("dev", "validation")
    assert draft.protocol.execution_backend_id == "autoresearchexam-public-v1"
    assert "core-assets/manifests/tier-0.json" in artifacts
    assert "core-assets/manifests/tier-1.json" in artifacts
    assert not any("holdout" in key for key in artifacts)


def test_researcher_intent_is_bound_by_core_after_model_reply_without_asset_context():
    fixture = _fixture()
    exposed_inputs = []
    written = {}

    class Gateway:
        def read_file(self, name):
            return "public project readme"

        def write_artifact(self, name, data):
            written[name] = data

    def model_reply(public_input, **_pins):
        exposed_inputs.append(public_input)
        return {
            "next_experiment": "Run the bounded public experiment.",
            "artifact_text": {"research/finding.txt": "An uncertain observation."},
            "research_findings": [{
                "claim_key": "fixture:observation", "statement": "Unverified observation.",
                "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
            }],
            "protocol_intent": {
                "protocol_id": "live-fixture-test", "version": 1,
                "metric_id": "accuracy", "metric_field": "accuracy",
                "direction": "MAXIMIZE", "aggregation": "MEAN",
                "repeatability_runs": 2, "timeout_seconds": 60,
            },
        }

    invoke = make_researcher_invoke(
        model_reply,
        protocol_binder=lambda intent, snapshot: fixture.bind(
            intent, source_snapshot_id=snapshot,
        ),
    )
    result = invoke(
        {
            "role_binding": {
                "role_id": "researcher", "adapter": "codex", "provider": "openai",
                "model": "gpt-6-luna", "model_version": "siwc-responses-v1",
                "prompt_digest": "a" * 64,
            },
            "capability_manifest": {"visible_paths": ["README.md"]},
            "role_context": {"role_id": "researcher", "evidence_refs": []},
            "base_commit": "a" * 40, "node_id": "explore-test",
        }, Gateway(), adapter="codex", provider="openai", model="gpt-6-luna",
        model_version="siwc-responses-v1", prompt_digest="a" * 64,
        max_model_tokens=2048,
    )

    assert "private input" not in repr(exposed_inputs)
    assert "trusted evaluator fixture is available" in exposed_inputs[0]["instruction"]
    draft = result["protocol_draft"]
    assert result["protocol_intent"]["protocol_id"] == "live-fixture-test"
    assert draft["protocol"]["protocol_id"] == "live-fixture-test"
    assert set(draft["protocol"]["tiers"][0]) == {"tier", "manifest_sha256", "case_count"}
    assert "core-assets/inputs/tier-1" in " ".join(written)
    assert written["core-assets/inputs/tier-1/" + hashlib.sha256(b"private input").hexdigest() + ".bin"] == b"private input"


def test_fixture_campaign_rejects_first_researcher_reply_without_protocol_intent():
    fixture = _fixture()

    class Gateway:
        def read_file(self, _name):
            return "public project readme"

        def write_artifact(self, *_args):
            return None

    invoke = make_researcher_invoke(
        lambda *_args, **_kwargs: {
            "next_experiment": "Run a bounded public experiment.",
            "artifact_text": {"research/finding.txt": "Unverified observation."},
            "research_findings": [{
                "claim_key": "fixture:observation", "statement": "Unverified.",
                "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
            }],
        },
        protocol_binder=lambda intent, snapshot: fixture.bind(
            intent, source_snapshot_id=snapshot,
        ),
    )
    request = {
        "role_binding": {
            "role_id": "researcher", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "siwc-responses-v1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {"visible_paths": ["README.md"]},
        "role_context": {"role_id": "researcher", "evidence_refs": []},
        "base_commit": "a" * 40, "node_id": "explore-test",
    }
    with pytest.raises(V2RuntimeError, match="first ordinary Researcher response must propose protocol_intent"):
        invoke(
            request, Gateway(), adapter="codex", provider="openai", model="gpt-6-luna",
            model_version="siwc-responses-v1", prompt_digest="a" * 64,
            max_model_tokens=2048,
        )
