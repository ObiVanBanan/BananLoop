"""Small CSV helpers shared by the model-facing project code."""

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load_training_data() -> tuple[list[dict[str, str]], list[int]]:
    rows = load_rows(ROOT / "data" / "train.csv")
    labels = [int(row.pop("y") == "yes") for row in rows]
    return rows, labels


def load_split(split: str) -> tuple[list[dict[str, str]], list[int]]:
    if split not in {"dev", "validation", "holdout"}:
        raise ValueError(f"unknown evaluation split: {split}")
    if split == "dev":
        path = ROOT / "data" / "dev.csv"
    else:
        path = ROOT / ".bananloop_eval" / f"{split}.csv"
    rows = load_rows(path)
    labels = [int(row.pop("y") == "yes") for row in rows]
    return rows, labels
