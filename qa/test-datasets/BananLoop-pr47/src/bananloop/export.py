"""Reviewable champion export; never applies changes to the source repository."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .process import run_process
from .report import build_report, render_markdown
from .store import CampaignStore, StoreError


def export_champion(store: CampaignStore, workspace: Path, output: Path) -> Path:
    campaign = store.campaign()
    baseline = campaign["baseline_snapshot"]
    champion = campaign["champion_snapshot"]
    if not baseline or not champion:
        raise StoreError("no measured champion to export")
    output = output.resolve()
    if output.is_relative_to(workspace.resolve()):
        raise StoreError("export destination must be outside the evaluation workspace")
    if output.exists():
        raise StoreError("export destination already exists; choose a new directory")
    patch = run_process(
        ["git", "diff", "--binary", "--full-index", "--no-ext-diff", baseline, champion, "--"],
        cwd=workspace, timeout=30,
    ).stdout
    report = build_report(store)
    manifest = {
        "schema_version": 1,
        "spec_sha256": campaign["spec_hash"],
        "base_commit": baseline,
        "champion_commit": champion,
        "campaign_outcome": campaign["status"],
        "stop_reason": campaign["stop_reason"],
        "final_assessment": report["final_assessment"],
        "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "has_changes": bool(patch),
        "artifact_files_included": False,
    }
    output.mkdir(parents=True)
    (output / "champion.patch").write_bytes(patch.encode("utf-8"))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return output
