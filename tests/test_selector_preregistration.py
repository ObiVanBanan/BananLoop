import hashlib
import json
from pathlib import Path

from bananloop.contracts import HypothesisPortfolio


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "selector_ab_v2" / "manifest.json"
FIXTURE = ROOT / "examples" / "calibration" / "mini_matcher"
PORTFOLIO = ROOT / "benchmarks" / "selector_ab_v2" / "frozen_portfolio.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_selector_preregistration_hashes_and_portfolio_contract_are_frozen():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))

    assert manifest["status"] == "authorized_frozen_portfolio_not_historical"
    assert manifest["portfolio"]["status"] == "FROZEN_MANUAL_PREREGISTERED_NOT_HISTORICAL"
    assert manifest["portfolio"]["sha256"] == sha256(PORTFOLIO)
    assert portfolio["portfolio_id"] == "mini-matcher-public-manual-portfolio-v1"
    assert portfolio["candidate_count"] == 3
    assert "not the historical smoke" in portfolio["provenance"]
    HypothesisPortfolio.model_validate({"candidates": portfolio["candidates"]})

    for name, expected in manifest["task"]["input_sha256"].items():
        assert sha256(FIXTURE / name) == expected
    for name, expected in manifest["selector_implementation_sha256"].items():
        assert sha256(ROOT / name) == expected


def test_selector_preregistration_records_codex_subscription_quota_authorization():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    authorization = manifest["resource_authorization"]
    assert authorization["status"] == "AUTHORIZED"
    assert authorization["max_provider_cost_usd"] == 0
    assert authorization["max_total_tokens"] is None
    assert authorization["token_cap_basis"] == "provider_enforced_codex_subscription_quota"
    assert authorization["max_wall_clock_hours"] <= 1
    assert "subscription quota" in authorization["authorization_reference"]
    assert manifest["v1_supersession"]["status"] == "ABORTED_AFTER_TWO_FAILED_SEED53_SLOTS"
    assert manifest["container_runtime"]["image_id"]
    assert manifest["replicate_design"]["paired_seeds"] == [503, 607, 709, 811, 919]
