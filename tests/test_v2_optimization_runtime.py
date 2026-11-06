from __future__ import annotations

import pytest

from bananloop.v2_optimization_runtime import (
    OptimizationCandidateProposalV1,
    V2OptimizationError,
    _safe_path,
    make_optimizer_invoke,
)


@pytest.mark.parametrize("path", [
    "../outside.py", "/absolute.py", "C:\\outside.py", ".git/config",
    "src/secrets.py", "src/evaluation_helper.py", "src/my_credentials.py",
    "src/.env.local", "src\\windows.py",
])
def test_optimizer_path_scope_rejects_traversal_and_private_material(path):
    with pytest.raises(V2OptimizationError):
        _safe_path(path)


def test_optimizer_proposal_is_bounded_and_rejects_empty_replacements():
    with pytest.raises(ValueError):
        OptimizationCandidateProposalV1(
            hypothesis_id="h1", rationale="bounded change", changes={"src/app.py": ""},
        )
    with pytest.raises(ValueError):
        OptimizationCandidateProposalV1(
            hypothesis_id="h1", rationale="bounded change",
            changes={f"src/file-{index}.py": "x" for index in range(17)},
        )


def test_optimizer_model_bridge_exposes_only_allowlisted_text_and_gateway_writes():
    class Gateway:
        def __init__(self):
            self.writes = {}

        def read_file(self, path):
            assert path == "src/app.py"
            return "base\n"

        def write_file(self, path, content):
            self.writes[path] = content

    seen = {}

    def model_reply(public_input, **pinned):
        seen["input"] = public_input
        seen["pinned"] = pinned
        return {
            "hypothesis_id": "h1", "rationale": "improve ranker",
            "changes": {"src/app.py": "candidate\n"},
        }

    model_reply.last_usage = {"total_tokens": 12}
    gateway = Gateway()
    invoke = make_optimizer_invoke(model_reply)
    request = {
        "hypothesis_id": "h1",
        "role_binding": {
            "role_id": "optimizer", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "pin-1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {"visible_paths": ["src/app.py"]},
        "role_context": {"public_task": "improve the ranker"},
        "private_path": "/home/user/.ssh/id_ed25519",
        "evaluator_secret": "must-not-be-forwarded",
    }
    result = invoke(
        request, gateway, adapter="codex", provider="openai", model="gpt-6-luna",
        model_version="pin-1", prompt_digest="a" * 64, max_model_tokens=500,
    )

    assert seen["input"]["visible_documents"] == {"src/app.py": "base\n"}
    assert "private_path" not in seen["input"] and "evaluator_secret" not in seen["input"]
    assert gateway.writes == {"src/app.py": "candidate\n"}
    assert result["model_tokens"] == 12


def test_optimizer_model_bridge_rejects_changed_role_or_unapproved_path():
    def model_reply(*_args, **_kwargs):
        return {"hypothesis_id": "h1", "rationale": "x", "changes": {"src/app.py": "y"}}

    invoke = make_optimizer_invoke(model_reply)
    request = {
        "hypothesis_id": "h1", "role_binding": {"role_id": "optimizer"},
        "capability_manifest": {"visible_paths": ["src/app.py"]},
        "role_context": {},
    }
    with pytest.raises(V2OptimizationError, match="pinned OpenAI role"):
        invoke(request, object(), adapter="legacy", provider="openai", model="x",
               model_version="v", prompt_digest="a" * 64, max_model_tokens=10)

