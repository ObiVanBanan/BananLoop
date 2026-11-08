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


def test_optimizer_proposal_normalizes_one_string_limitation_losslessly():
    proposal = OptimizationCandidateProposalV1.model_validate({
        "hypothesis_id": "h1", "rationale": "bounded change",
        "changes": {"src/app.py": "candidate\n"},
        "limitations": "Needs follow-up testing.",
    })
    assert proposal.limitations == ("Needs follow-up testing.",)


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


def test_optimizer_can_read_training_data_without_write_capability():
    class Gateway:
        def __init__(self):
            self.writes = {}

        def read_file(self, path):
            return {"data/train.csv": "sex,survived\nfemale,1\n",
                    "src/model.json": '{"schema":"titanic-linear-v1"}'}[path]

        def write_file(self, path, content):
            self.writes[path] = content

    seen = {}

    def model_reply(public_input, **_pinned):
        seen["input"] = public_input
        return {
            "hypothesis_id": "h1", "rationale": "fit bounded model weights",
            "changes": {"src/model.json": '{"schema":"titanic-linear-v1","weights":{}}'},
        }

    gateway = Gateway()
    request = {
        "hypothesis_id": "h1",
        "role_binding": {
            "role_id": "optimizer", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "pin-1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {
            "visible_paths": ["data/train.csv", "src/model.json"],
            "write_paths": ["src/model.json"],
        },
        "role_context": {"public_task": "improve Titanic accuracy"},
    }
    invoke = make_optimizer_invoke(model_reply)
    invoke(request, gateway, adapter="codex", provider="openai", model="gpt-6-luna",
           model_version="pin-1", prompt_digest="a" * 64, max_model_tokens=500)

    assert "female,1" in seen["input"]["visible_documents"]["data/train.csv"]
    assert seen["input"]["writable_paths"] == ["src/model.json"]
    assert gateway.writes == {
        "src/model.json": '{"schema":"titanic-linear-v1","weights":{}}',
    }


def test_optimizer_cannot_write_read_only_training_data():
    class Gateway:
        def read_file(self, _path):
            return "training data"

        def write_file(self, *_args):
            pytest.fail("read-only training data must not reach write_file")

    def model_reply(_public_input, **_pinned):
        return {
            "hypothesis_id": "h1", "rationale": "try to tamper with training data",
            "changes": {"data/train.csv": "changed\n"},
        }

    request = {
        "hypothesis_id": "h1",
        "role_binding": {
            "role_id": "optimizer", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "pin-1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {
            "visible_paths": ["data/train.csv", "src/model.json"],
            "write_paths": ["src/model.json"],
        },
        "role_context": {"public_task": "improve Titanic accuracy"},
    }
    invoke = make_optimizer_invoke(model_reply)
    with pytest.raises(V2OptimizationError, match="write allowlist"):
        invoke(request, Gateway(), adapter="codex", provider="openai", model="gpt-6-luna",
               model_version="pin-1", prompt_digest="a" * 64, max_model_tokens=500)


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

