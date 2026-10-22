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
        with pytest.raises(CampaignLockError):
            second.acquire()
    finally:
        first.release()

    owner = json.loads(path.read_text(encoding="utf-8"))
    assert owner["pid"] > 0
    assert owner["acquired_at"]

    second.acquire()
    second.release()


def test_campaign_lock_wraps_open_permission_error(tmp_path: Path, monkeypatch):
    path = tmp_path / "campaign.lock"

    def denied(self, *args, **kwargs):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(Path, "open", denied)

    with pytest.raises(CampaignLockError, match="already locked or inaccessible"):
        CampaignLock(path).acquire()
