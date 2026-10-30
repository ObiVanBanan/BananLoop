"""Typed local commands for the single-operator campaign surface.

Acceptance is a durable receipt. It is deliberately separate from the later
engine outcome, which may remain unknown after a process failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .contracts import PivotDecisionCommand
from .store import CampaignStore, StoreError


ControlKind = Literal["pause", "resume", "end"]


@dataclass(frozen=True, slots=True)
class OperatorCommand:
    campaign_id: str
    request_id: str
    kind: ControlKind


def submit_control(
    store: CampaignStore, selected_campaign_id: str, command: OperatorCommand,
) -> dict[str, object]:
    """Validate and persist a control request without executing model output."""
    if not selected_campaign_id or command.campaign_id != selected_campaign_id:
        raise StoreError("operator command targets a different campaign")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", command.request_id):
        raise StoreError("invalid operator request id")
    if command.kind not in {"pause", "resume", "end"}:
        raise StoreError("invalid operator control kind")
    payload = {"campaign_id": command.campaign_id}
    old = store.control_request(command.request_id)
    if old is not None:
        receipt = store.record_control_request(command.request_id, command.kind, payload)
        if receipt["status"] == "ACCEPTED":
            campaign = store.campaign()
            if command.kind == "pause" and campaign["status"] in {"READY", "RUNNING"} and not campaign["pause_requested"]:
                store.request_pause()
            if command.kind == "end" and campaign["status"] not in {"STOPPED", "GOAL_MET"} and not campaign["stop_requested"]:
                store.request_stop()
        return receipt
    status = store.campaign()["status"]
    if command.kind == "pause" and status not in {"READY", "RUNNING"}:
        raise StoreError(f"cannot pause campaign from {status}")
    if command.kind == "resume" and status not in {"PAUSED_USER", "PAUSED_INFRA", "PAUSED_NEEDS_INPUT"}:
        raise StoreError(f"cannot resume campaign from {status}")
    if command.kind == "end" and status in {"STOPPED", "GOAL_MET"}:
        raise StoreError(f"campaign is already terminal: {status}")
    receipt = store.record_control_request(command.request_id, command.kind, payload)
    if command.kind == "pause":
        if not store.request_pause():
            return store.finish_control_request(command.request_id, "REJECTED", {"reason": "pause no longer available"})
    elif command.kind == "end":
        if not store.request_stop():
            return store.finish_control_request(command.request_id, "REJECTED", {"reason": "stop no longer available"})
    return receipt


def reconcile_control(store: CampaignStore, request_id: str) -> dict[str, object]:
    """Record an observed engine status, never infer completion from acceptance."""
    receipt = store.control_request(request_id)
    if receipt is None:
        raise StoreError("operator request missing")
    if receipt["status"] != "ACCEPTED":
        return receipt
    status = store.campaign()["status"]
    expected = {"pause": "PAUSED_USER", "end": "STOPPED"}
    if receipt["kind"] in expected and status == expected[receipt["kind"]]:
        return store.finish_control_request(request_id, "COMPLETED", {"status": status})
    if receipt["kind"] == "pause" and status in {"STOPPED", "GOAL_MET", "PAUSED_INFRA", "PAUSED_NEEDS_INPUT"}:
        return store.finish_control_request(request_id, "REJECTED", {"observed_status": status})
    if receipt["kind"] == "end" and status == "GOAL_MET":
        return store.finish_control_request(request_id, "REJECTED", {"observed_status": status})
    if receipt["kind"] == "resume" and status not in {"PAUSED_USER", "PAUSED_INFRA", "PAUSED_NEEDS_INPUT"}:
        return store.finish_control_request(request_id, "COMPLETED", {"status": status})
    return receipt


def submit_pivot_decision(
    store: CampaignStore, selected_campaign_id: str, command: PivotDecisionCommand,
) -> dict[str, object]:
    """Apply an explicit approval/rejection bound to one proposal digest."""
    if not selected_campaign_id or command.campaign_id != selected_campaign_id:
        raise StoreError("pivot decision targets a different campaign")
    return store.apply_pivot_decision(selected_campaign_id, command)
