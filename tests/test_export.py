import hashlib
import json

import pytest

from bananloop.cli import cmd_export, parser
from bananloop.export import export_champion
from bananloop.lock import CampaignLock
from bananloop.store import StoreError
from test_engine import git, make_engine


def test_export_contains_only_champion_changes_and_does_not_touch_source(tmp_path):
    store, engine, repo, base = make_engine(tmp_path)
    try:
        engine.run()
        output = export_champion(store, engine.workspace.workspace, tmp_path / "export")
        patch = output / "champion.patch"
        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["base_commit"] == base
        assert manifest["champion_commit"] == store.campaign()["champion_snapshot"]
        assert manifest["patch_sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()
        assert manifest["artifact_files_included"] is False
        assert (output / "report.md").exists()
        assert len(json.loads((output / "report.json").read_text())["experiments"]) == 3
        git(["apply", "--check", str(patch)], repo)
        assert git(["rev-parse", "HEAD"], repo) == base
        assert git(["status", "--porcelain"], repo) == ""
        applied = tmp_path / "applied"
        git(["clone", str(repo), str(applied)], tmp_path)
        git(["apply", str(patch)], applied)
        assert (applied / "value.txt").read_text().strip() == "3"
        assert (applied / "guard.txt").read_text().strip() == "0"
        with pytest.raises(StoreError, match="already exists"):
            export_champion(store, engine.workspace.workspace, output)
    finally:
        store.close()


def test_export_rejects_unmeasured_state_and_live_owner(tmp_path):
    store, engine, repo, _ = make_engine(tmp_path)
    try:
        with pytest.raises(StoreError, match="no measured champion"):
            export_champion(store, engine.workspace.workspace, tmp_path / "empty")
        assert not (tmp_path / "empty").exists()
        args = parser().parse_args(["export", "--repo", str(repo), "--state-dir", str(store.path.parent), "--output", str(tmp_path / "export")])
        with CampaignLock(store.path.parent / "campaign.lock"):
            with pytest.raises(StoreError, match="stop or pause"):
                cmd_export(args)
    finally:
        store.close()
