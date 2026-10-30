"""Opt-in web viewer does not alter the BananLoop CLI research engine."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bananloop import web_ui


def args(tmp_path: Path, **kwargs):
    defaults = dict(repo=str(tmp_path), campaign_id=None, state_dir=None,
                    demo=False, port=8787, node="node", python=None)
    return argparse.Namespace(**(defaults | kwargs))


def test_demo_has_no_campaign_args(tmp_path):
    command = web_ui.build_command(args(tmp_path, demo=True))
    assert command[:1] == ["node"]
    assert "--repo" not in command
    assert "--python" not in command
    assert command[-2:] == ["--port", "8787"]


def test_explicit_campaign_routes_to_read_only_server(tmp_path):
    command = web_ui.build_command(args(tmp_path, campaign_id="c-abc"))
    assert command[command.index("--repo") + 1] == str(tmp_path)
    assert command[command.index("--campaign-id") + 1] == "c-abc"
    assert command[command.index("--python") + 1]


def test_active_campaign_is_discovered(tmp_path, monkeypatch):
    registry = MagicMock()
    registry.resolve.return_value = (tmp_path / "state", "c-active")
    monkeypatch.setattr(web_ui, "CampaignRegistry", lambda repo: registry)
    command = web_ui.build_command(args(tmp_path))
    assert command[command.index("--campaign-id")+1] == "c-active"


def test_invalid_or_ambiguous_selection_fails_closed(tmp_path, monkeypatch):
    registry=MagicMock()
    registry.resolve.return_value = (tmp_path, None)
    monkeypatch.setattr(web_ui, "CampaignRegistry", lambda repo: registry)
    with pytest.raises(ValueError, match="no active campaign"):
        web_ui.build_command(args(tmp_path))
    with pytest.raises(ValueError, match="choose"):
        web_ui.build_command(args(tmp_path, campaign_id="a", state_dir="b"))
    with pytest.raises(ValueError, match="demo"):
        web_ui.build_command(args(tmp_path, demo=True, campaign_id="a"))
    with pytest.raises(ValueError, match="port"):
        web_ui.build_command(args(tmp_path, demo=True, port=20))