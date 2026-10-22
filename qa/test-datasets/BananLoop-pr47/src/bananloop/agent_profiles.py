from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .contracts import AgentRoleOverride


class AgentProfileError(ValueError):
    pass


def load_agent_profiles(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"default": {}, "roles": {}}
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentProfileError(f"cannot read agent profiles {path}: {exc}") from exc
    if set(data) - {"default", "roles"}:
        raise AgentProfileError("agent profile file accepts only [default] and [roles.<role>] tables")
    default = data.get("default", {})
    roles = data.get("roles", {})
    if not isinstance(default, dict) or not isinstance(roles, dict):
        raise AgentProfileError("default and roles must be TOML tables")
    try:
        validated_default = AgentRoleOverride.model_validate(default)
        validated_roles = {
            role: AgentRoleOverride.model_validate(config)
            for role, config in roles.items()
        }
    except Exception as exc:
        details = getattr(exc, "errors", None)
        if callable(details):
            safe_errors = details(include_input=False, include_context=False)
            summary = "; ".join(
                f"{'.'.join(str(part) for part in item.get('loc', ())) or '<root>'}: {item.get('msg', 'invalid value')}"
                for item in safe_errors
            )
        else:
            summary = type(exc).__name__
        raise AgentProfileError(f"invalid agent profile: {summary}") from None
    allowed = {"bootstrap", "propose", "implement", "analyze"}
    unknown = set(validated_roles) - allowed
    if unknown:
        raise AgentProfileError(f"unsupported agent roles: {', '.join(sorted(unknown))}")
    return {
        "default": validated_default.model_dump(exclude_unset=True),
        "roles": validated_roles,
    }
