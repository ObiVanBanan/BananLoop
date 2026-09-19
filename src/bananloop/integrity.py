from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import EvaluationAsset


class EvaluationIntegrityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_repo_file(repo: Path, value: str) -> tuple[str, Path]:
    repo = repo.resolve()
    candidate = Path(value)
    path = candidate if candidate.is_absolute() else repo / candidate
    path = path.resolve()
    try:
        relative = path.relative_to(repo)
    except ValueError as exc:
        raise EvaluationIntegrityError(f"evaluation asset must be inside repository: {value}") from exc
    if not path.is_file():
        raise EvaluationIntegrityError(f"evaluation asset is not a file: {value}")
    return relative.as_posix(), path


def discover_evaluation_assets(
    repo: Path,
    evaluation_command: list[str],
    explicit_assets: list[str] | None = None,
) -> list[EvaluationAsset]:
    repo = repo.resolve()
    assets: dict[str, EvaluationAsset] = {}

    # Explicit evaluator assets are part of the trusted repository boundary.
    # Keep rejecting external paths here so callers cannot accidentally freeze
    # arbitrary host files into a portable CampaignSpec.
    for value in explicit_assets or []:
        relative, path = normalize_repo_file(repo, value)
        assets[relative] = EvaluationAsset(path=relative, sha256=sha256_file(path))

    # Command arguments can legitimately reference external inputs such as a
    # hidden holdout. Auto-discover only files that live inside the source repo;
    # external inputs must carry their own identity/check (for example a SHA).
    for token in evaluation_command:
        token_path = Path(token)
        possible = token_path if token_path.is_absolute() else repo / token_path
        try:
            path = possible.resolve()
            if not path.is_file():
                continue
            relative = path.relative_to(repo).as_posix()
        except (OSError, ValueError):
            continue
        assets[relative] = EvaluationAsset(path=relative, sha256=sha256_file(path))

    return [assets[key] for key in sorted(assets)]


def evaluation_fingerprint(assets: list[EvaluationAsset]) -> str | None:
    if not assets:
        return None
    digest = hashlib.sha256()
    for asset in sorted(assets, key=lambda item: item.path):
        digest.update(asset.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(asset.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def verify_evaluation_assets(root: Path, assets: list[EvaluationAsset]) -> list[str]:
    violations: list[str] = []
    for asset in assets:
        path = (root / asset.path).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            violations.append(f"outside_root:{asset.path}")
            continue
        if not path.is_file():
            violations.append(f"missing:{asset.path}")
            continue
        actual = sha256_file(path)
        if actual != asset.sha256:
            violations.append(f"hash_mismatch:{asset.path}")
    return violations
