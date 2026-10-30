"""Evaluator-owned files, kept outside candidate and agent workspaces."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import EvaluationResult
from .evaluators.command import EvaluatorError


def retain_artifacts(result: EvaluationResult, root: Path, experiment_id: int | None, snapshot: str) -> None:
    root = root.resolve()
    cases = {(c.case_id, c.repetition): c for c in result.cases}
    seen = set()
    for artifact in result.artifact_manifest:
        case = cases.get((artifact.case_id, artifact.repetition))
        if case is None:
            raise EvaluatorError("artifact references an unknown case/repetition")
        path = Path(artifact.path)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(root) or not path.is_file() or path in seen:
            raise EvaluatorError("artifact must be a unique file inside BANANLOOP_ARTIFACT_DIR")
        seen.add(path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != artifact.sha256:
            raise EvaluatorError("artifact SHA-256 mismatch")
        artifact.path = str(path)
        artifact.experiment_id = experiment_id
        artifact.candidate_snapshot = snapshot
        # Bind measurements to the validated case, never trust duplicate metadata.
        artifact.metrics = dict(case.metrics)
