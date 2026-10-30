import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bananloop.contracts import HypothesisPortfolio


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "selector_ab_v2" / "manifest.json"
FIXTURE = ROOT / "examples" / "calibration" / "mini_matcher"
PORTFOLIO = ROOT / "benchmarks" / "selector_ab_v2" / "frozen_portfolio.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_line_ending_variants(path: Path) -> set[str]:
    """Hash unchanged text content under either platform newline convention."""
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return {hashlib.sha256(value).hexdigest() for value in (raw, lf, crlf)}


def test_selector_preregistration_hashes_and_portfolio_contract_are_frozen():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))

    assert manifest["status"] == "authorized_frozen_portfolio_not_historical"
    assert manifest["portfolio"]["status"] == "FROZEN_MANUAL_PREREGISTERED_NOT_HISTORICAL"
    assert manifest["portfolio"]["sha256"] in sha256_line_ending_variants(PORTFOLIO)
    assert portfolio["portfolio_id"] == "mini-matcher-public-manual-portfolio-v1"
    assert portfolio["candidate_count"] == 3
    assert "not the historical smoke" in portfolio["provenance"]
    HypothesisPortfolio.model_validate({"candidates": portfolio["candidates"]})

    for name, expected in manifest["task"]["input_sha256"].items():
        assert sha256(FIXTURE / name) == expected
    revision = manifest["source_checkout"]["revision"]
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not exists:
        pytest.skip(f"pinned selector source commit {revision} is unavailable")
    for name, expected in manifest["selector_implementation_sha256"].items():
        blob = subprocess.run(
            ["git", "show", f"{revision}:{name}"], cwd=ROOT, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
        assert hashlib.sha256(blob).hexdigest() == expected


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
