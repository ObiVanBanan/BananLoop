"""Checks that a Harbor task config matches its frozen BananLoop child."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from bananloop.contracts import OptimizationCampaignSpecV2, parse_campaign_spec_document


def require_frozen_optimization_child(config: Mapping[str, Any]) -> OptimizationCampaignSpecV2:
    """Fail closed unless the persisted child actually funds the configured run.

    Harbor's one-hour and five-candidate config is not sufficient by itself:
    the approved BananLoop child must freeze the same cap and enough role,
    experiment, and wall-time budget to execute it.
    """
    campaign_id = config.get("campaign_id")
    if not isinstance(campaign_id, str) or not re.fullmatch(r"c-[0-9a-f]{32}", campaign_id):
        raise ValueError("BananLoop campaign ID is malformed")
    try:
        candidate_cap = config.get("max_candidate_iterations", 5)
        time_limit = config.get("time_limit_seconds", 3600)
        if (isinstance(candidate_cap, bool) or not isinstance(candidate_cap, int)
                or not 1 <= candidate_cap <= 5):
            raise ValueError("candidate iteration cap must be from 1 to 5")
        if (isinstance(time_limit, bool) or not isinstance(time_limit, int)
                or not 1 <= time_limit <= 3600):
            raise ValueError("task time limit must be from 1 to 3600 seconds")
        state_root = Path(str(config["state_dir"])).expanduser().resolve(strict=True)
        campaign_dir = state_root / "campaigns" / campaign_id
        database = campaign_dir / "campaign.sqlite3"
        if campaign_dir.is_symlink() or database.is_symlink():
            raise ValueError("campaign state path must not contain symlinks")
        if not database.is_file() or not database.resolve().is_relative_to(state_root):
            raise ValueError("frozen BananLoop child database is missing or outside state_dir")
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10) as conn:
            rows = conn.execute("SELECT spec_json FROM campaigns ORDER BY id").fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str):
            raise ValueError("campaign database must contain exactly one frozen spec")
        payload = json.loads(rows[0][0])
        try:
            spec = parse_campaign_spec_document(payload)
        except Exception as exc:
            raise ValueError("frozen BananLoop child spec is invalid") from exc
        if not isinstance(spec, OptimizationCampaignSpecV2):
            raise ValueError("Harbor requires a frozen Optimization campaign")
        if spec.max_candidate_iterations != candidate_cap:
            raise ValueError("frozen BananLoop child candidate cap differs from Harbor config")
        if (spec.budget.deadline_seconds is None
                or spec.budget.deadline_seconds > time_limit):
            raise ValueError("frozen BananLoop child deadline exceeds the Harbor task limit")
        if (spec.budget.max_agent_calls < 2 * candidate_cap
                or spec.budget.max_experiments < candidate_cap):
            raise ValueError("frozen BananLoop child budget cannot fund its candidate cap")
        if spec.evaluation is None or spec.evaluation.approval_digest is None:
            raise ValueError("frozen BananLoop child has no independently approved evaluator protocol")
        return spec
    except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("cannot validate the frozen BananLoop child against Harbor limits") from None
