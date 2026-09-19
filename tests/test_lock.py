from __future__ import annotations

import json
from pathlib import Path

import pytest

from bananloop.lock import CampaignLock, CampaignLockError


def test_campaign_lock_is_single_writer_and_reusable(tmp_path: Path):
    path = tmp_path / "campaign.lock"
    first = CampaignLock(path)
    second = CampaignLock(path)

    first.acquire()
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
        assert owner["pid"] > 0
        assert owner["acquired_at"]

        with pytest.raises(CampaignLockError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
