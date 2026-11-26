"""Export the EXACT saved v0.2 RoleRegistryV1 / TeamManifestV1 of one campaign.

No model calls, network access, fallback roster or unrelated campaign state.
Default is *strictly read-only*: the registry JSON must exist, and the
TeamManifestV1 must already have a durable pin in campaign-registry.sqlite3.

An explicit --compose-and-pin-if-missing is available only for v2 campaigns
with an already-frozen spec and registry: it applies the same deterministic
TeamFactoryV1 request vector as the v2 Exploration/Optimization runtimes.
A newly composed pin is labeled as such, never misrepresented as recovered.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from .contracts import (
    ExplorationCampaignSpecV2, OptimizationCampaignSpecV2, RuntimePaths,
    parse_campaign_spec_document,
)
from .registry import CampaignRegistry, RegistryError
from .roles import (
    RoleContractError, RoleRegistryV1, TeamFactoryV1, TeamManifestAuthority,
    TeamManifestV1, TeamRequestV1,
)


class RosterExportError(ValueError):
    """The selected campaign has no exact, valid saved roster to export."""


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise RosterExportError("required campaign SQLite DB does not exist as a regular file")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _load_spec(database: Path):
    try:
        with _connect_readonly(database) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
            ).fetchone()
            if table is None:
                raise RosterExportError("selected campaign has no v2 campaign spec table")
            rows = conn.execute("SELECT spec_json,spec_hash FROM campaigns").fetchall()
            if len(rows) != 1:
                raise RosterExportError("selected campaign must have exactly one immutable spec")
            spec = parse_campaign_spec_document(rows[0]["spec_json"])
            if not isinstance(spec, (ExplorationCampaignSpecV2, OptimizationCampaignSpecV2)):
                raise RosterExportError("selected campaign is not a v2 campaign")
            if ("spec_hash" in rows[0].keys()
                    and rows[0]["spec_hash"] != spec.approved_hash()):
                raise RosterExportError("stored campaign spec_hash disagrees with frozen spec JSON")
            return spec
    except (sqlite3.DatabaseError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, RosterExportError):
            raise
        raise RosterExportError("campaign spec cannot be verified in read-only SQLite") from exc


def _read_pin(database: Path, campaign_id: str, *, spec_hash: str,
              registry: RoleRegistryV1) -> TeamManifestV1 | None:
    if not database.is_file() or database.is_symlink():
        return None
    try:
        with _connect_readonly(database) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "team_manifest_pins" not in tables:
                return None
            row = conn.execute(
                "SELECT spec_hash,registry_digest,manifest_digest,manifest_json "
                "FROM team_manifest_pins WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if row is None:
                return None
            if (row["spec_hash"] != spec_hash or row["registry_digest"] != registry.digest()):
                raise RosterExportError("stored team pin belongs to a different frozen spec/registry")
            manifest = TeamManifestV1.model_validate_json(row["manifest_json"])
            if (manifest.campaign_id != campaign_id
                    or manifest.spec_hash != spec_hash
                    or manifest.registry_digest != registry.digest()
                    or manifest.manifest_digest != row["manifest_digest"]):
                raise RosterExportError("durable team manifest digest or campaign identity differs")
            needed = {"team_role_call_allocations", "team_role_token_allocations"}
            if not needed <= tables:
                raise RosterExportError("durable team pin is missing role budget allocation tables")
            calls = conn.execute(
                "SELECT role_instance_id,role_id,calls_limit FROM team_role_call_allocations "
                "WHERE campaign_id=? AND manifest_digest=? ORDER BY role_instance_id",
                (campaign_id, manifest.manifest_digest),
            ).fetchall()
            tokens = conn.execute(
                "SELECT role_instance_id,role_id,tokens_limit FROM team_role_token_allocations "
                "WHERE campaign_id=? AND manifest_digest=? ORDER BY role_instance_id",
                (campaign_id, manifest.manifest_digest),
            ).fetchall()
            expected_calls = sorted((m.instance_id, m.role.role_id, m.role.calls_per_instance)
                                    for m in manifest.members)
            expected_tokens = sorted((m.instance_id, m.role.role_id, m.role.tokens_per_instance)
                                     for m in manifest.members)
            if ([(row[0], row[1], row[2]) for row in calls] != expected_calls
                    or [(row[0], row[1], row[2]) for row in tokens] != expected_tokens):
                raise RosterExportError("team pin role call/token allocations differ from saved manifest")
            for member in manifest.members:
                try:
                    definition = registry.role(member.role.role_id)
                except RoleContractError as exc:
                    raise RosterExportError("team contains an unknown role outside the pinned registry") from exc
                if any(getattr(member.role, key) != getattr(definition, key) for key in (
                    "adapter", "provider", "model", "model_version", "prompt_digest"
                )):
                    raise RosterExportError("team member provider/model/prompt differs from role registry")
                if (member.role.calls_per_instance > definition.calls_per_instance
                        or member.role.tokens_per_instance > definition.tokens_per_instance
                        or not set(member.role.context_fields) <= set(definition.context_fields)):
                    raise RosterExportError("team instance capacity/context exceeds role registry")
                member_policy = member.role.capability_policy
                policy = definition.capability_policy
                if (not set(member_policy.tools) <= set(policy.tools)
                        or not set(member_policy.read_paths) <= set(policy.read_paths)
                        or not set(member_policy.write_paths) <= set(policy.write_paths)
                        or (member_policy.network_access and not policy.network_access)):
                    raise RosterExportError("pinned team grants capabilities beyond its role definition")
            return manifest
    except sqlite3.DatabaseError as exc:
        raise RosterExportError("saved roster database cannot be read safely") from exc


def _compose_exact_runtime_pin(database: Path, campaign_id: str, spec,
                               registry: RoleRegistryV1) -> TeamManifestV1:
    """Only on explicit request: mirror v2 runtime TeamRequestV1, not a fake roster."""
    if isinstance(spec, ExplorationCampaignSpecV2):
        requested = {"researcher": 1}
        if any(role.role_id == "independent_verifier" for role in registry.roles):
            requested["independent_verifier"] = 1
    elif isinstance(spec, OptimizationCampaignSpecV2):
        requested = {"optimizer": 1, "integration_verifier": 1}
    else:
        raise RosterExportError("v2 runtime mode cannot be inferred from frozen spec")
    request = TeamRequestV1(
        campaign_id=campaign_id,
        spec_hash=spec.approved_hash(),
        requested_instances=requested,
        available_agent_calls=spec.budget.max_agent_calls,
        available_model_tokens=spec.budget.max_model_tokens,
    )
    expected = TeamFactoryV1.build(
        registry, request, expected_registry_digest=spec.role_registry_digest,
    )
    authority = TeamManifestAuthority(database)
    try:
        try:
            existing = authority.load(
                campaign_id, spec_hash=spec.approved_hash(),
                registry_digest=spec.role_registry_digest,
            )
            if existing.manifest_digest != expected.manifest_digest:
                raise RosterExportError("existing pin conflicts with deterministic v2 runtime composition")
            return existing
        except RoleContractError as exc:
            if "has no pinned" not in str(exc):
                raise
            return authority.pin(expected)
    finally:
        authority.close()


def export_campaign_roster(*, repo: str | Path, campaign_id: str, output_dir: str | Path,
                           state_dir: str | Path | None = None,
                           compose_and_pin_if_missing: bool = False) -> dict[str, Any]:
    """Export two checked files with no model invocation. Select campaign explicitly."""
    source = Path(repo).expanduser().resolve()
    if not source.is_dir():
        raise RosterExportError("the requested repository directory does not exist")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise RosterExportError("an explicit campaign ID is mandatory")
    paths = RuntimePaths.for_repo(
        source, Path(state_dir).expanduser() if state_dir is not None else None,
    )
    campaign_registry = CampaignRegistry(source, paths=paths)
    try:
        state_root, resolved = campaign_registry.resolve_readonly(campaign_id)
    except RegistryError as exc:
        raise RosterExportError(f"campaign cannot be resolved read-only: {exc}") from exc
    if resolved != campaign_id:
        raise RosterExportError("campaign ID resolved to a different or legacy campaign")
    spec = _load_spec(state_root / "campaign.sqlite3")
    role_source = state_root / "role-registry.json"
    if not role_source.is_file() or role_source.is_symlink():
        raise RosterExportError("frozen role-registry.json is absent; not reconstructing role definitions")
    try:
        registry = RoleRegistryV1.load(role_source, expected_digest=spec.role_registry_digest)
    except (RoleContractError, ValueError, OSError) as exc:
        raise RosterExportError("registry JSON does not match the immutable v2 campaign pin") from exc
    manifest = _read_pin(campaign_registry.path, campaign_id,
                         spec_hash=spec.approved_hash(), registry=registry)
    origin = "EXISTING_DURABLE_PIN"
    if manifest is None:
        if not compose_and_pin_if_missing:
            raise RosterExportError(
                "NO_DURABLE_TEAM_PIN: existing RoleRegistryV1 found, but TeamManifestV1 "
                "was never pinned; retry with --compose-and-pin-if-missing only if "
                "authorizing the exact deterministic v2 runtime TeamFactoryV1 composition"
            )
        try:
            manifest = _compose_exact_runtime_pin(
                campaign_registry.path, campaign_id, spec, registry,
            )
        except (RoleContractError, ValueError, sqlite3.DatabaseError) as exc:
            raise RosterExportError("cannot compose an exact team from the frozen v2 spec") from exc
        manifest = _read_pin(campaign_registry.path, campaign_id,
                             spec_hash=spec.approved_hash(), registry=registry)
        if manifest is None:
            raise RosterExportError("new durable team pin could not be verified read-only")
        origin = "EXPLICIT_DETERMINISTIC_RUNTIME_PIN_CREATED"
    destination = Path(output_dir).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise RosterExportError("output directory must not exist, to avoid overwrites")
    if destination.is_relative_to(source):
        raise RosterExportError("do not export campaign roster into the tracked repository")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise RosterExportError("export parent must be an existing regular directory")
    destination.mkdir(mode=0o700)
    targets = [
        (destination / "registry.json", registry.model_dump(mode="json")),
        (destination / "team.json", manifest.model_dump(mode="json")),
    ]
    for path, content in targets:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(content, handle, sort_keys=True, ensure_ascii=False, indent=2)
            handle.write("\n")
        if os.name == "posix":
            path.chmod(0o600)
    return {
        "status": "EXPORTED_PINNED_CAMPAIGN_ROSTER",
        "campaign_id": campaign_id,
        "mode": spec.mode,
        "origin": origin,
        "registry_path": str(targets[0][0]),
        "team_path": str(targets[1][0]),
        "registry_sha256": registry.digest(),
        "team_manifest_sha256": manifest.manifest_digest,
        "spec_hash": spec.approved_hash(),
        "role_ids_in_team": [member.role.role_id for member in manifest.members],
        "live_model_called": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export verified RoleRegistryV1 and TeamManifestV1 for exactly one v2 campaign",
    )
    parser.add_argument("--repo", required=True, help="exact source repo path used by campaign")
    parser.add_argument("--campaign-id", required=True, help="explicit v2 campaign ID, never guess active")
    parser.add_argument("--state-dir", help="same explicit state-dir override used at campaign creation")
    parser.add_argument("--out", required=True, help="NEW export directory outside checkout")
    parser.add_argument(
        "--compose-and-pin-if-missing", action="store_true",
        help="explicitly create deterministic runtime team pin from already-frozen campaign/registry",
    )
    args = parser.parse_args(argv)
    try:
        result = export_campaign_roster(
            repo=args.repo, campaign_id=args.campaign_id, state_dir=args.state_dir,
            output_dir=args.out, compose_and_pin_if_missing=args.compose_and_pin_if_missing,
        )
    except (RosterExportError, RoleContractError, ValueError) as exc:
        parser.exit(2, f"roster export refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
