"""Pinned v2 role registries, deterministic team manifests, and fresh contexts.

The v1 Engine's mutable AgentConfig/profile path is intentionally separate.
Every v2 worker must bind its immutable job request to one TeamManifestV1.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import CapabilityPolicyV2


_ROLE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_CONTEXT_FIELDS = frozenset({
    "campaign_goal", "node_id", "node_phase", "public_task", "evidence_refs",
    "dev_summary", "constraints", "budget_remaining", "specialization_notes",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class RoleContractError(ValueError):
    """A role registry, specialization, team, or context violates its pin."""


class RoleDefinitionV1(BaseModel):
    """Immutable model/tool/prompt contract for one canonical v2 role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    adapter: Literal["codex", "pi", "command"]
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=120)
    prompt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_policy: CapabilityPolicyV2
    context_fields: tuple[str, ...] = Field(min_length=1, max_length=16)
    priority: int = Field(default=100, ge=0, le=10000)
    min_instances: int = Field(default=1, ge=1, le=64)
    max_instances: int = Field(default=1, ge=1, le=64)
    calls_per_instance: int = Field(default=1, ge=1, le=10000)
    tokens_per_instance: int = Field(default=1, ge=1, le=100_000_000)

    @field_validator("context_fields")
    @classmethod
    def valid_context_fields(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or not set(values) <= _SAFE_CONTEXT_FIELDS:
            raise ValueError("role context fields must be unique members of the safe context allowlist")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def valid_instance_range(self) -> "RoleDefinitionV1":
        if self.min_instances > self.max_instances:
            raise ValueError("min_instances cannot exceed max_instances")
        if self.tokens_per_instance < self.calls_per_instance:
            raise ValueError("tokens_per_instance must cover at least one token per allocated call")
        return self


class RoleRegistryV1(BaseModel):
    """Content-addressed registry snapshot; model/provider selection is pinned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_id: str = Field(min_length=1, max_length=120)
    version: Literal[1] = 1
    roles: tuple[RoleDefinitionV1, ...] = Field(min_length=1, max_length=128)

    @field_validator("roles")
    @classmethod
    def canonical_roles(cls, values: tuple[RoleDefinitionV1, ...]) -> tuple[RoleDefinitionV1, ...]:
        ids = [item.role_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("role IDs must be unique")
        return tuple(sorted(values, key=lambda item: (item.priority, item.role_id)))

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))

    def role(self, role_id: str) -> RoleDefinitionV1:
        for item in self.roles:
            if item.role_id == role_id:
                return item
        raise RoleContractError(f"role is not present in pinned registry: {role_id}")

    @classmethod
    def load(cls, path: str | Path, *, expected_digest: str | None = None) -> "RoleRegistryV1":
        """Load one explicit JSON/TOML registry file and verify its campaign pin."""
        source = Path(path)
        try:
            raw_bytes = source.read_bytes()
            if source.suffix.lower() == ".json":
                document = json.loads(raw_bytes)
            elif source.suffix.lower() == ".toml":
                document = tomllib.loads(raw_bytes.decode("utf-8"))
            else:
                raise RoleContractError("role registry must be a .json or .toml file")
            registry = cls.model_validate(document)
        except RoleContractError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
            raise RoleContractError(f"cannot load role registry: {type(exc).__name__}") from None
        if expected_digest is not None and registry.digest() != expected_digest:
            raise RoleContractError("role registry digest does not match the frozen campaign spec")
        return registry


class RoleSpecializationV1(BaseModel):
    """A strictly narrower role view; it cannot change adapter/model/provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    specialization_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    base_role_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    remove_tools: tuple[str, ...] = ()
    remove_read_paths: tuple[str, ...] = ()
    remove_write_paths: tuple[str, ...] = ()
    disable_network: bool = True
    require_isolation: bool = False
    max_instances: int | None = Field(default=None, ge=1, le=64)
    calls_per_instance: int | None = Field(default=None, ge=1, le=10000)
    tokens_per_instance: int | None = Field(default=None, ge=1, le=100_000_000)
    context_fields: tuple[str, ...] | None = None
    notes: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def valid_narrowing(self) -> "RoleSpecializationV1":
        if len(set(self.remove_tools)) != len(self.remove_tools):
            raise ValueError("remove_tools must be unique")
        if len(set(self.remove_read_paths)) != len(self.remove_read_paths):
            raise ValueError("remove_read_paths must be unique")
        if len(set(self.remove_write_paths)) != len(self.remove_write_paths):
            raise ValueError("remove_write_paths must be unique")
        if self.context_fields is not None and len(set(self.context_fields)) != len(self.context_fields):
            raise ValueError("specialization context_fields must be unique")
        return self

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class TeamRequestV1(BaseModel):
    """Deterministic resource-limited team request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_instances: dict[str, int] = Field(default_factory=dict)
    specializations: tuple[RoleSpecializationV1, ...] = ()
    available_agent_calls: int = Field(ge=1)
    available_model_tokens: int | None = Field(default=None, ge=1)

    @field_validator("requested_instances")
    @classmethod
    def valid_instance_requests(cls, value: dict[str, int]) -> dict[str, int]:
        for key, count in value.items():
            if not _ROLE_ID.fullmatch(key) or isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("requested_instances must map role IDs to positive integers")
        return dict(sorted(value.items()))

    @field_validator("specializations")
    @classmethod
    def canonical_specializations(cls, value: tuple[RoleSpecializationV1, ...]) -> tuple[RoleSpecializationV1, ...]:
        return tuple(sorted(value, key=lambda item: (item.base_role_id, item.specialization_id)))


class EffectiveRoleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: str
    specialization_id: str | None
    adapter: Literal["codex", "pi", "command"]
    provider: str
    model: str
    model_version: str
    prompt_digest: str
    capability_policy: CapabilityPolicyV2
    context_fields: tuple[str, ...]
    calls_per_instance: int
    tokens_per_instance: int
    context_policy_digest: str


class TeamMemberV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(pattern=r"^agent-[0-9a-f]{24}$")
    role: EffectiveRoleV1


class TeamManifestV1(BaseModel):
    """Immutable generated team binding used by every queued agent job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_id: str
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    members: tuple[TeamMemberV1, ...] = Field(min_length=1)
    allocated_agent_calls: int = Field(ge=1)
    allocated_model_tokens: int = Field(ge=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(cls, *, campaign_id: str, spec_hash: str, registry: RoleRegistryV1,
              members: tuple[TeamMemberV1, ...]) -> "TeamManifestV1":
        payload = {
            "campaign_id": campaign_id,
            "spec_hash": spec_hash,
            "registry_id": registry.registry_id,
            "registry_digest": registry.digest(),
            "members": [member.model_dump(mode="json") for member in members],
            "allocated_agent_calls": sum(m.role.calls_per_instance for m in members),
            "allocated_model_tokens": sum(m.role.tokens_per_instance for m in members),
        }
        digest = _digest(payload)
        return cls.model_validate({**payload, "manifest_digest": digest})

    @model_validator(mode="after")
    def verify_digest(self) -> "TeamManifestV1":
        payload = self.model_dump(mode="json")
        expected = payload.pop("manifest_digest")
        if _digest(payload) != expected:
            raise ValueError("team manifest digest does not match its canonical content")
        ids = [member.instance_id for member in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("team member instance IDs must be unique")
        return self

    def member(self, instance_id: str) -> TeamMemberV1:
        for member in self.members:
            if member.instance_id == instance_id:
                return member
        raise RoleContractError("agent instance is not present in the pinned team manifest")

    def job_binding(self, instance_id: str, *, action_id: str, context_digest: str) -> dict[str, Any]:
        member = self.member(instance_id)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", action_id):
            raise RoleContractError("invalid role job action ID")
        if not re.fullmatch(r"[0-9a-f]{64}", context_digest):
            raise RoleContractError("role job context digest must be SHA-256")
        return {
            "role_id": member.role.role_id,
            "role_instance_id": member.instance_id,
            "team_manifest_digest": self.manifest_digest,
            "role_registry_digest": self.registry_digest,
            "spec_hash": self.spec_hash,
            "provider": member.role.provider,
            "model": member.role.model,
            "model_version": member.role.model_version,
            "prompt_digest": member.role.prompt_digest,
            "adapter": member.role.adapter,
            "allocated_agent_calls": member.role.calls_per_instance,
            "allocated_model_tokens": member.role.tokens_per_instance,
            "per_call_model_token_limit": member.role.tokens_per_instance // member.role.calls_per_instance,
            "action_id": action_id,
            "context_digest": context_digest,
            "capability_policy_digest": _digest(member.role.capability_policy.model_dump(mode="json")),
            "context_policy_digest": member.role.context_policy_digest,
        }


class TeamFactoryV1:
    """Builds a deterministic team without provider fallback or policy widening."""

    @staticmethod
    def build(registry: RoleRegistryV1, request: TeamRequestV1, *, expected_registry_digest: str) -> TeamManifestV1:
        actual_digest = registry.digest()
        if actual_digest != expected_registry_digest:
            raise RoleContractError("role registry digest does not match the frozen campaign spec")
        role_ids = {role.role_id for role in registry.roles}
        unknown = set(request.requested_instances) - role_ids
        if unknown:
            raise RoleContractError(f"requested roles are not in the pinned registry: {', '.join(sorted(unknown))}")
        specializations = {spec.specialization_id: spec for spec in request.specializations}
        if len(specializations) != len(request.specializations):
            raise RoleContractError("specialization IDs must be unique")
        for spec in request.specializations:
            base = registry.role(spec.base_role_id)
            if not set(spec.remove_tools) <= set(base.capability_policy.tools):
                raise RoleContractError("specialization cannot grant tools absent from its base role")
            if not set(spec.remove_read_paths) <= set(base.capability_policy.read_paths):
                raise RoleContractError("specialization cannot broaden read paths")
            if not set(spec.remove_write_paths) <= set(base.capability_policy.write_paths):
                raise RoleContractError("specialization cannot broaden write paths")
            if spec.context_fields is not None and not set(spec.context_fields) <= set(base.context_fields):
                raise RoleContractError("specialization cannot add role context fields")
            if spec.max_instances is not None and spec.max_instances > base.max_instances:
                raise RoleContractError("specialization cannot exceed its base role instance ceiling")
            if spec.calls_per_instance is not None and spec.calls_per_instance > base.calls_per_instance:
                raise RoleContractError("specialization cannot increase its agent-call allocation")
            if spec.tokens_per_instance is not None and spec.tokens_per_instance > base.tokens_per_instance:
                raise RoleContractError("specialization cannot increase its model-token allocation")

        candidates: dict[str, list[tuple[RoleDefinitionV1, RoleSpecializationV1 | None]]] = {}
        mandatory: list[tuple[RoleDefinitionV1, RoleSpecializationV1 | None]] = []
        for role in registry.roles:
            matching = [item for item in request.specializations if item.base_role_id == role.role_id]
            desired = request.requested_instances.get(role.role_id, role.min_instances)
            desired = min(role.max_instances, desired)
            if desired < role.min_instances:
                raise RoleContractError(f"requested team falls below the minimum for role {role.role_id}")
            # Keep each required base instance explicit. Additional instances may
            # use specializations in request order, with a stable cap per variant.
            base_count = min(role.min_instances, desired)
            role_candidates = [(role, None) for _ in range(base_count)]
            mandatory.extend(role_candidates)
            remaining = desired - base_count
            for item in matching:
                cap = min(remaining, item.max_instances or remaining)
                role_candidates.extend((role, item) for _ in range(cap))
                remaining -= cap
                if remaining == 0:
                    break
            role_candidates.extend((role, None) for _ in range(remaining))
            candidates[role.role_id] = role_candidates

        # First honor each base role's minimum membership, then fill remaining requested capacity
        # in stable registry priority / role ID order.
        selected = list(mandatory)
        used_calls = sum(role.calls_per_instance for role, _ in selected)
        used_tokens = sum(role.tokens_per_instance for role, _ in selected)
        if used_calls > request.available_agent_calls or (
                request.available_model_tokens is not None and used_tokens > request.available_model_tokens):
            raise RoleContractError("minimum team exceeds available agent-call/model-token budget")
        for role in registry.roles:
            optional = candidates[role.role_id][role.min_instances:]
            for candidate in optional:
                candidate_calls = candidate[0].calls_per_instance
                candidate_tokens = candidate[0].tokens_per_instance
                token_fits = (request.available_model_tokens is None
                              or used_tokens + candidate_tokens <= request.available_model_tokens)
                if used_calls + candidate_calls <= request.available_agent_calls and token_fits:
                    selected.append(candidate)
                    used_calls += candidate_calls
                    used_tokens += candidate_tokens

        def effective(role: RoleDefinitionV1, specialization: RoleSpecializationV1 | None) -> EffectiveRoleV1:
            policy = role.capability_policy
            context_fields = role.context_fields
            calls, tokens = role.calls_per_instance, role.tokens_per_instance
            if specialization is not None:
                if specialization.base_role_id != role.role_id:
                    raise RoleContractError("specialization base role mismatch")
                if not set(specialization.remove_tools) <= set(policy.tools):
                    raise RoleContractError("specialization cannot grant tools absent from its base role")
                if not set(specialization.remove_read_paths) <= set(policy.read_paths):
                    raise RoleContractError("specialization cannot broaden read paths")
                if not set(specialization.remove_write_paths) <= set(policy.write_paths):
                    raise RoleContractError("specialization cannot broaden write paths")
                narrowed_context = set(context_fields)
                if specialization.context_fields is not None:
                    if not set(specialization.context_fields) <= narrowed_context:
                        raise RoleContractError("specialization cannot add role context fields")
                    narrowed_context = set(specialization.context_fields)
                policy = CapabilityPolicyV2.model_validate({
                    **policy.model_dump(mode="json"),
                    "tools": tuple(item for item in policy.tools if item not in specialization.remove_tools),
                    "read_paths": tuple(item for item in policy.read_paths if item not in specialization.remove_read_paths),
                    "write_paths": tuple(item for item in policy.write_paths if item not in specialization.remove_write_paths),
                    "network_access": policy.network_access and not specialization.disable_network,
                    "backend": "ISOLATED" if specialization.require_isolation else policy.backend,
                })
                context_fields = tuple(sorted(narrowed_context))
                calls = min(calls, specialization.calls_per_instance or calls)
                tokens = min(tokens, specialization.tokens_per_instance or tokens)
            return EffectiveRoleV1(
                role_id=role.role_id,
                specialization_id=specialization.specialization_id if specialization else None,
                adapter=role.adapter, provider=role.provider, model=role.model,
                model_version=role.model_version, prompt_digest=role.prompt_digest,
                capability_policy=policy, context_fields=context_fields,
                calls_per_instance=calls, tokens_per_instance=tokens,
                context_policy_digest=_digest({"fields": context_fields, "prompt_digest": role.prompt_digest}),
            )

        members: list[TeamMemberV1] = []
        for role, specialization in selected:
            effective_role = effective(role, specialization)
            identity = _digest({
                "campaign_id": request.campaign_id, "spec_hash": request.spec_hash,
                "role_id": effective_role.role_id,
                "specialization_id": effective_role.specialization_id,
                "registry_digest": actual_digest,
                "ordinal": sum(1 for item in members if item.role.role_id == role.role_id),
            })
            members.append(TeamMemberV1(instance_id=f"agent-{identity[:24]}", role=effective_role))
        members.sort(key=lambda item: (registry.role(item.role.role_id).priority, item.role.role_id,
                                       item.role.specialization_id or "", item.instance_id))
        total_calls = sum(member.role.calls_per_instance for member in members)
        total_tokens = sum(member.role.tokens_per_instance for member in members)
        if total_calls > request.available_agent_calls:
            raise RoleContractError("requested team exceeds available agent-call budget")
        if request.available_model_tokens is not None and total_tokens > request.available_model_tokens:
            raise RoleContractError("requested team exceeds available model-token budget")
        return TeamManifestV1.build(campaign_id=request.campaign_id, spec_hash=request.spec_hash,
                                    registry=registry, members=tuple(members))


class RoleContextV1(BaseModel):
    """One invocation's reconstructed, allowlisted, role-scoped context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    campaign_goal: str = Field(min_length=1, max_length=4000)
    node_id: str | None = Field(default=None, max_length=200)
    node_phase: str | None = Field(default=None, max_length=40)
    public_task: str | None = Field(default=None, max_length=12000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=256)
    dev_summary: str | None = Field(default=None, max_length=12000)
    constraints: tuple[str, ...] = Field(default=(), max_length=128)
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    specialization_notes: str | None = Field(default=None, max_length=500)

    @field_validator("evidence_refs", "constraints")
    @classmethod
    def canonical_sequences(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 2000 for item in values):
            raise ValueError("context items must be nonempty and bounded")
        return tuple(sorted(set(values)))

    @field_validator("budget_remaining")
    @classmethod
    def safe_budget_values(cls, values: dict[str, int]) -> dict[str, int]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
               or isinstance(value, bool) or not isinstance(value, int) or value < 0
               for key, value in values.items()):
            raise ValueError("budget_remaining values must be nonnegative integers keyed by safe resource IDs")
        return dict(sorted(values.items()))

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class RoleContextValueV1(BaseModel):
    """Value plus Core-assigned provenance; worker text cannot choose its class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Any
    classification: Literal[
        "PUBLIC_SPEC", "PUBLIC_NODE", "DEV_AGGREGATE", "CORE_BUDGET",
        "ROLE_SPECIALIZATION", "PUBLIC_EVIDENCE", "VALIDATION", "HOLDOUT", "SECRET",
    ]
    source_ref: str = Field(pattern=r"^(campaign|graph|research|budget|role|evidence)://[^\s]{1,500}$")

    @model_validator(mode="after")
    def source_matches_classification(self) -> "RoleContextValueV1":
        prefixes = {
            "PUBLIC_SPEC": "campaign://spec/",
            "PUBLIC_NODE": "graph://nodes/",
            "DEV_AGGREGATE": "research://",
            "CORE_BUDGET": "budget://",
            "ROLE_SPECIALIZATION": "role://specialization/",
            "PUBLIC_EVIDENCE": "evidence://integration/",
            "VALIDATION": "evidence://validation/",
            "HOLDOUT": "evidence://holdout/",
            "SECRET": "role://private/",
        }
        if not self.source_ref.startswith(prefixes[self.classification]):
            raise ValueError("role context source reference does not match its classification")
        return self


class RoleContextSourceV1(BaseModel):
    """Trusted-Core projection of data eligible for role-context construction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_goal: RoleContextValueV1
    fields: dict[str, RoleContextValueV1] = Field(default_factory=dict)


_SAFE_CLASSIFICATIONS = {
    "campaign_goal": {"PUBLIC_SPEC"},
    "node_id": {"PUBLIC_NODE"},
    "node_phase": {"PUBLIC_NODE"},
    "public_task": {"PUBLIC_SPEC", "PUBLIC_NODE", "PUBLIC_EVIDENCE"},
    "evidence_refs": {"PUBLIC_NODE", "DEV_AGGREGATE"},
    "dev_summary": {"DEV_AGGREGATE"},
    "constraints": {"PUBLIC_SPEC", "PUBLIC_NODE"},
    "budget_remaining": {"CORE_BUDGET"},
    "specialization_notes": {"ROLE_SPECIALIZATION"},
}


def build_role_context(member: TeamMemberV1, source: RoleContextSourceV1) -> RoleContextV1:
    """Rebuild a fresh role-only view from typed Core projections, excluding restricted classes."""
    if not isinstance(source, RoleContextSourceV1):
        raise RoleContractError("role context requires a typed Core provenance projection")
    if source.campaign_goal.classification != "PUBLIC_SPEC":
        raise RoleContractError("campaign goal context must come from the approved public spec")
    allowed = set(member.role.context_fields)
    selected: dict[str, Any] = {
        "role_id": member.role.role_id,
        "campaign_goal": source.campaign_goal.value,
    }
    for name in allowed - {"campaign_goal"}:
        datum = source.fields.get(name)
        if datum is None or datum.classification not in _SAFE_CLASSIFICATIONS[name]:
            continue
        selected[name] = datum.value
    context = RoleContextV1.model_validate(selected)
    return RoleContextV1.model_validate(context.model_dump(mode="python", exclude_unset=True))


@dataclass(frozen=True, slots=True)
class PinnedRoleExecutorV1:
    """Trusted adapter binding created from a verified manifest member.

    The dispatcher receives provider/model/version as immutable keywords and
    cannot choose a fallback through worker request data.
    """

    manifest_digest: str
    member: TeamMemberV1
    _invoke: Callable[..., dict[str, Any]] = field(repr=False, compare=False)

    @classmethod
    def from_manifest(cls, manifest: TeamManifestV1, instance_id: str,
                      invoke: Callable[..., dict[str, Any]]) -> "PinnedRoleExecutorV1":
        return cls(manifest_digest=manifest.manifest_digest,
                   member=manifest.member(instance_id), _invoke=invoke)

    def dispatch(self, request: dict[str, Any], gateway: Any) -> dict[str, Any]:
        binding = request.get("role_binding")
        role = self.member.role
        if (not isinstance(binding, dict)
                or binding.get("team_manifest_digest") != self.manifest_digest
                or binding.get("role_instance_id") != self.member.instance_id
                or binding.get("role_id") != role.role_id
                or binding.get("provider") != role.provider
                or binding.get("model") != role.model
                or binding.get("model_version") != role.model_version
                or binding.get("adapter") != role.adapter
                or binding.get("prompt_digest") != role.prompt_digest):
            raise RoleContractError("job does not match this pinned role executor")
        return self._invoke(
            request, gateway,
            adapter=role.adapter, provider=role.provider, model=role.model,
            model_version=role.model_version, prompt_digest=role.prompt_digest,
            max_model_tokens=role.tokens_per_instance // role.calls_per_instance,
        )


class TeamManifestAuthority:
    """Durable one-manifest-per-campaign pin, colocated with registry/worker DB."""

    def __init__(self, registry_path: str | Path):
        self.path = Path(registry_path)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS team_manifest_pins (
          campaign_id TEXT PRIMARY KEY,
          spec_hash TEXT NOT NULL,
          registry_digest TEXT NOT NULL,
          manifest_digest TEXT NOT NULL,
          manifest_json TEXT NOT NULL,
          created_at REAL NOT NULL
        )
        """)
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS team_role_call_allocations (
          campaign_id TEXT NOT NULL,
          manifest_digest TEXT NOT NULL,
          role_instance_id TEXT NOT NULL,
          role_id TEXT NOT NULL,
          calls_limit INTEGER NOT NULL CHECK(calls_limit>0),
          PRIMARY KEY(campaign_id,manifest_digest,role_instance_id)
        )
        """)
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS team_role_token_allocations (
          campaign_id TEXT NOT NULL,
          manifest_digest TEXT NOT NULL,
          role_instance_id TEXT NOT NULL,
          role_id TEXT NOT NULL,
          tokens_limit INTEGER NOT NULL CHECK(tokens_limit>0),
          PRIMARY KEY(campaign_id,manifest_digest,role_instance_id)
        )
        """)
        self.conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS team_manifest_pins_no_update
        BEFORE UPDATE ON team_manifest_pins BEGIN SELECT RAISE(ABORT,'team manifest pins are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_manifest_pins_no_delete
        BEFORE DELETE ON team_manifest_pins BEGIN SELECT RAISE(ABORT,'team manifest pins are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_allocations_no_update
        BEFORE UPDATE ON team_role_call_allocations BEGIN SELECT RAISE(ABORT,'team role allocations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_allocations_no_delete
        BEFORE DELETE ON team_role_call_allocations BEGIN SELECT RAISE(ABORT,'team role allocations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_token_allocations_no_update
        BEFORE UPDATE ON team_role_token_allocations BEGIN SELECT RAISE(ABORT,'team role token allocations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_token_allocations_no_delete
        BEFORE DELETE ON team_role_token_allocations BEGIN SELECT RAISE(ABORT,'team role token allocations are immutable'); END;
        """)

    def close(self) -> None:
        self.conn.close()

    def pin(self, manifest: TeamManifestV1) -> TeamManifestV1:
        encoded = _canonical(manifest.model_dump(mode="json"))
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM team_manifest_pins WHERE campaign_id=?",
                                    (manifest.campaign_id,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO team_manifest_pins(campaign_id,spec_hash,registry_digest,manifest_digest,manifest_json,created_at) VALUES(?,?,?,?,?,?)",
                    (manifest.campaign_id, manifest.spec_hash, manifest.registry_digest,
                     manifest.manifest_digest, encoded, time.time()),
                )
                self.conn.executemany(
                    "INSERT INTO team_role_call_allocations(campaign_id,manifest_digest,role_instance_id,role_id,calls_limit) VALUES(?,?,?,?,?)",
                    [(manifest.campaign_id, manifest.manifest_digest, member.instance_id,
                      member.role.role_id, member.role.calls_per_instance) for member in manifest.members],
                )
                self.conn.executemany(
                    "INSERT INTO team_role_token_allocations(campaign_id,manifest_digest,role_instance_id,role_id,tokens_limit) VALUES(?,?,?,?,?)",
                    [(manifest.campaign_id, manifest.manifest_digest, member.instance_id,
                      member.role.role_id, member.role.tokens_per_instance) for member in manifest.members],
                )
            elif (row["spec_hash"], row["registry_digest"], row["manifest_digest"], row["manifest_json"]) != (
                    manifest.spec_hash, manifest.registry_digest, manifest.manifest_digest, encoded):
                raise RoleContractError("campaign already has a different pinned team manifest")
            self._verify_allocations(manifest)
            self.conn.commit()
            return manifest
        except Exception:
            self.conn.rollback()
            raise

    def load(self, campaign_id: str, *, spec_hash: str, registry_digest: str) -> TeamManifestV1:
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("SELECT * FROM team_manifest_pins WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row is None:
            raise RoleContractError("campaign has no pinned team manifest")
        if row["spec_hash"] != spec_hash or row["registry_digest"] != registry_digest:
            raise RoleContractError("pinned team manifest does not match campaign spec/registry")
        manifest = TeamManifestV1.model_validate_json(row["manifest_json"])
        if manifest.manifest_digest != row["manifest_digest"]:
            raise RoleContractError("stored team manifest digest is inconsistent")
        self._verify_allocations(manifest)
        return manifest

    def _verify_allocations(self, manifest: TeamManifestV1) -> None:
        rows = self.conn.execute(
            "SELECT role_instance_id,role_id,calls_limit FROM team_role_call_allocations WHERE campaign_id=? AND manifest_digest=? ORDER BY role_instance_id",
            (manifest.campaign_id, manifest.manifest_digest),
        ).fetchall()
        actual = [(row["role_instance_id"], row["role_id"], int(row["calls_limit"])) for row in rows]
        expected = sorted((member.instance_id, member.role.role_id, member.role.calls_per_instance)
                          for member in manifest.members)
        if actual != expected:
            raise RoleContractError("durable role call allocations do not match the pinned manifest")
        token_rows = self.conn.execute(
            "SELECT role_instance_id,role_id,tokens_limit FROM team_role_token_allocations WHERE campaign_id=? AND manifest_digest=? ORDER BY role_instance_id",
            (manifest.campaign_id, manifest.manifest_digest),
        ).fetchall()
        actual_tokens = [(row["role_instance_id"], row["role_id"], int(row["tokens_limit"]))
                         for row in token_rows]
        expected_tokens = sorted((member.instance_id, member.role.role_id, member.role.tokens_per_instance)
                                 for member in manifest.members)
        if actual_tokens != expected_tokens:
            raise RoleContractError("durable role token allocations do not match the pinned manifest")
