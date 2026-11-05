from __future__ import annotations

import sqlite3
import threading

import pytest

from bananloop.champion_promotion import ChampionPromotionError, ChampionPromotionService
from bananloop.evaluator_verification import EvaluatorVerificationError


CAMPAIGN = "campaign-1"
SOURCE = "1" * 40
CANDIDATE = "2" * 40
SPEC = "3" * 64
APPROVAL = "4" * 64
PROTOCOL = "5" * 64
RECEIPT = "6" * 64


class _TrustedAuthority:
    """Test double for the already-tested durable integration authority boundary."""

    def __init__(self, *, accept: bool = True, variants=None):
        self.accept = accept
        self.variants = variants or {RECEIPT: CANDIDATE}
        self.calls = []

    def require_integration_decision(self, receipt_digest, **bindings):
        self.calls.append((receipt_digest, bindings))
        candidate = self.variants.get(receipt_digest)
        expected = {
            "decision": "PASS", "campaign_id": CAMPAIGN, "approval_digest": APPROVAL,
            "spec_hash": SPEC, "source_snapshot_id": SOURCE,
            "protocol_digest": PROTOCOL, "candidate_snapshot_id": candidate,
        }
        if not self.accept or candidate is None or bindings != expected:
            raise EvaluatorVerificationError("no exact trusted PASS receipt")


def _service(path, authority=None):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    return conn, ChampionPromotionService(conn, authority or _TrustedAuthority())


def _promote(service, **overrides):
    values = {
        "campaign_id": CAMPAIGN, "expected_snapshot_id": SOURCE, "expected_revision": 0,
        "candidate_snapshot_id": CANDIDATE, "integration_receipt_digest": RECEIPT,
        "spec_hash": SPEC, "source_snapshot_id": SOURCE,
        "approval_digest": APPROVAL, "protocol_digest": PROTOCOL,
    }
    values.update(overrides)
    return service.promote(**values)


def test_promotion_requires_exact_trusted_pass_receipt(tmp_path):
    conn, service = _service(tmp_path / "campaign.sqlite3", _TrustedAuthority(accept=False))
    try:
        service.initialize(campaign_id=CAMPAIGN, source_snapshot_id=SOURCE)
        with pytest.raises(ChampionPromotionError, match="exact trusted integration PASS"):
            _promote(service)
        assert service.current() == {
            "campaign_id": CAMPAIGN, "snapshot_id": SOURCE, "revision": 0,
        }
    finally:
        conn.close()


def test_promotion_passes_all_candidate_bindings_to_trusted_authority(tmp_path):
    conn, service = _service(tmp_path / "campaign.sqlite3")
    try:
        service.initialize(campaign_id=CAMPAIGN, source_snapshot_id=SOURCE)
        result = _promote(service)
        assert result == {
            "campaign_id": CAMPAIGN, "snapshot_id": CANDIDATE, "revision": 1,
        }
        receipt, bindings = service.authority.calls[0]
        assert receipt == RECEIPT
        assert bindings == {
            "decision": "PASS", "campaign_id": CAMPAIGN, "approval_digest": APPROVAL,
            "spec_hash": SPEC, "source_snapshot_id": SOURCE,
            "protocol_digest": PROTOCOL, "candidate_snapshot_id": CANDIDATE,
        }
    finally:
        conn.close()


def test_stale_expected_champion_conflicts_without_overwriting_winner(tmp_path):
    conn, service = _service(tmp_path / "campaign.sqlite3")
    try:
        service.initialize(campaign_id=CAMPAIGN, source_snapshot_id=SOURCE)
        with pytest.raises(ChampionPromotionError, match="stale champion expectation"):
            _promote(service, expected_snapshot_id="9" * 40)
        assert service.current()["snapshot_id"] == SOURCE
        winner = _promote(service)
        assert winner["snapshot_id"] == CANDIDATE
        replay = _promote(service)
        assert replay == {
            "campaign_id": CAMPAIGN, "snapshot_id": CANDIDATE,
            "revision": 1, "replayed": True,
        }
        assert service.current()["revision"] == 1
    finally:
        conn.close()


def test_concurrent_promotions_have_one_cas_winner(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    conn, service = _service(path)
    service.initialize(campaign_id=CAMPAIGN, source_snapshot_id=SOURCE)
    conn.close()
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt():
        worker_conn, worker = _service(path)
        try:
            barrier.wait(timeout=5)
            result = _promote(worker)
            outcomes.append(("replayed" if result.get("replayed") else "won", result))
        except ChampionPromotionError as exc:
            outcomes.append(("conflict", str(exc)))
        finally:
            worker_conn.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert [kind for kind, _ in outcomes].count("won") == 1
    assert [kind for kind, _ in outcomes].count("replayed") == 1
    final_conn, final = _service(path)
    try:
        assert final.current()["revision"] == 1
        assert final_conn.execute("SELECT COUNT(*) FROM champion_promotion_history").fetchone()[0] == 1
    finally:
        final_conn.close()



def test_distinct_candidate_promotions_race_with_one_cas_winner(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    second_candidate = "7" * 40
    second_receipt = "8" * 64
    authority = _TrustedAuthority(variants={
        RECEIPT: CANDIDATE, second_receipt: second_candidate,
    })
    conn, service = _service(path, authority)
    service.initialize(campaign_id=CAMPAIGN, source_snapshot_id=SOURCE)
    conn.close()
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt(candidate, receipt):
        worker_conn, worker = _service(path, authority)
        try:
            barrier.wait(timeout=5)
            result = _promote(
                worker, candidate_snapshot_id=candidate,
                integration_receipt_digest=receipt,
            )
            outcomes.append(("won", result))
        except ChampionPromotionError as exc:
            outcomes.append(("conflict", str(exc)))
        finally:
            worker_conn.close()

    threads = [
        threading.Thread(target=attempt, args=(CANDIDATE, RECEIPT)),
        threading.Thread(target=attempt, args=(second_candidate, second_receipt)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert [kind for kind, _ in outcomes].count("won") == 1
    assert [kind for kind, _ in outcomes].count("conflict") == 1
    final_conn, final = _service(path, authority)
    try:
        assert final.current()["snapshot_id"] in {CANDIDATE, second_candidate}
        assert final.current()["revision"] == 1
        assert final_conn.execute("SELECT COUNT(*) FROM champion_promotion_history").fetchone()[0] == 1
    finally:
        final_conn.close()

def test_candidate_graph_success_does_not_promote_champion_implicitly(tmp_path):
    conn, service = _service(tmp_path / "campaign.sqlite3")
    try:
        service.initialize(campaign_id=CAMPAIGN, source_snapshot_id=SOURCE)
        # The graph lifecycle only records node/integration success. Promotion
        # requires an explicit call to ChampionPromotionService.promote().
        assert service.current()["snapshot_id"] == SOURCE
        assert service.current()["revision"] == 0
    finally:
        conn.close()
