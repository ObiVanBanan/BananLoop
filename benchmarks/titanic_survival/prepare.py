"""Prepare a leakage-aware Titanic mini-benchmark using only the stdlib."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .features import MODEL_FEATURES

EXPECTED_MD5 = "60ac7205eee0ba5045c90b3bba95b1c4"
FEATURES = ("pclass", "sex", "age", "sibsp", "parch", "fare", "embarked")
SPLITS = (("train", 0.60), ("dev", 0.15), ("validation", 0.10), ("holdout", 0.15))


class TitanicPreparationError(ValueError):
    pass


def _read_arff(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    md5 = hashlib.md5(path.read_bytes()).hexdigest()
    if md5 != EXPECTED_MD5:
        raise TitanicPreparationError("OpenML Titanic input checksum does not match dataset 40945 v1")
    names: list[str] = []
    rows: list[dict[str, str]] = []
    data_started = False
    attribute = re.compile(r"^@attribute\s+('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\S+)", re.I)
    with path.open("r", encoding="utf-8", newline="") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("%"):
                continue
            if not data_started and line.lower() == "@data":
                data_started = True
                continue
            if not data_started:
                match = attribute.match(line)
                if match:
                    name = match.group(1).strip("'\"").replace("''", "'")
                    names.append(name)
                continue
            values = next(csv.reader([line], skipinitialspace=True))
            if len(values) != len(names):
                raise TitanicPreparationError("ARFF row width differs from its frozen attributes")
            rows.append(dict(zip(names, values)))
    required = set(FEATURES) | {"survived", "ticket"}
    if not data_started or not rows or not required <= set(names):
        raise TitanicPreparationError("OpenML Titanic file is missing required fields")
    if any(row["survived"] not in {"0", "1"} for row in rows):
        raise TitanicPreparationError("survival labels must be binary and nonmissing")
    return names, rows


def _split_groups(rows: list[dict[str, str]], seed: int) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for index, row in enumerate(rows):
        ticket = row["ticket"].strip()
        groups[ticket if ticket and ticket != "?" else f"__missing_ticket_{index}"] .append(row)

    rng = random.Random(seed)
    keyed = list(groups.items())
    rng.shuffle(keyed)
    keyed.sort(key=lambda item: len(item[1]), reverse=True)
    total_classes = Counter(row["survived"] for row in rows)
    proportions = dict(SPLITS)
    targets = {
        split: {label: count * proportions[split] for label, count in total_classes.items()}
        for split, _ in SPLITS
    }
    output = {split: [] for split, _ in SPLITS}
    counts = {split: Counter() for split, _ in SPLITS}
    total_targets = {split: len(rows) * proportions[split] for split, _ in SPLITS}

    for _, group in keyed:
        group_counts = Counter(row["survived"] for row in group)

        def projected_error(target_split: str) -> float:
            error = 0.0
            for split, _ in SPLITS:
                for label in ("0", "1"):
                    value = counts[split][label] + (group_counts[label] if split == target_split else 0)
                    target = max(targets[split][label], 1.0)
                    error += ((value - targets[split][label]) / target) ** 2
                size = len(output[split]) + (len(group) if split == target_split else 0)
                error += 0.2 * ((size - total_targets[split]) / max(total_targets[split], 1)) ** 2
            return error

        destination = min((split for split, _ in SPLITS), key=projected_error)
        output[destination].extend(group)
        counts[destination].update(group_counts)

    # Persist an order independent of dictionary iteration or upstream file order.
    for split in output:
        output[split].sort(key=lambda row: (row["ticket"], row["name"]))
    return output


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    import io

    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=(*FEATURES, "survived"), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "?") for field in (*FEATURES, "survived")})
    return target.getvalue().encode("utf-8")


def _case_bytes(rows: list[dict[str, str]]) -> bytes:
    payload = {
        "rows": [{field: row.get(field, "?") for field in FEATURES} for row in rows],
        "labels": [int(row["survived"]) for row in rows],
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def prepare(source: str | Path, output: str | Path, *, seed: int = 2026) -> dict[str, Any]:
    """Write candidate-visible training data and tier-private labeled inputs."""
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    repository_root = Path(__file__).resolve().parents[2]
    if output_path.is_relative_to(repository_root):
        raise TitanicPreparationError(
            "benchmark output must stay outside the candidate source repository"
        )
    names, rows = _read_arff(source_path)
    split_rows = _split_groups(rows, seed)
    candidate = output_path / "candidate"
    private = output_path / "evaluator-private"
    output_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_path.chmod(0o700)
    candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate.chmod(0o700)
    private.chmod(0o700)
    public_data = candidate / "data" / "train.csv"
    public_data.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    public_data.write_bytes(_csv_bytes(split_rows["train"]))
    initial_model = candidate / "src" / "model.json"
    initial_model.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    initial_model.write_text(json.dumps({
        "schema": "titanic-linear-v1",
        "intercept": -0.5,
        # Zero-initialized keys publish the permitted feature vocabulary to
        # the optimizer without exposing the evaluator implementation.
        "coefficients": {
            feature: 1.0 if feature == "sex_female" else 0.0
            for feature in MODEL_FEATURES
        },
    }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    initial_model.chmod(0o600)

    tier_metadata: dict[str, Any] = {}
    ticket_tier: dict[str, str] = {}
    for tier in ("dev", "validation", "holdout"):
        path = private / f"{tier}.json"
        raw = _case_bytes(split_rows[tier])
        path.write_bytes(raw)
        path.chmod(0o600)
        for row in split_rows[tier]:
            ticket = row["ticket"].strip()
            if ticket == "?":
                continue
            previous = ticket_tier.setdefault(ticket, tier)
            if previous != tier:
                raise TitanicPreparationError("a ticket group crossed public/private evaluator tiers")
        tier_metadata[tier] = {
            "rows": len(split_rows[tier]),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "class_counts": dict(sorted(Counter(row["survived"] for row in split_rows[tier]).items())),
        }
    result = {
        "schema": "bananloop-titanic-mini-benchmark-v1",
        "dataset": "OpenML Titanic 40945 v1",
        "source_md5": EXPECTED_MD5,
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "target": "survived",
        "excluded_fields": sorted(set(names) - set(FEATURES) - {"survived"}),
        "features": list(FEATURES),
        "seed": seed,
        "split": {tier: {"rows": len(items)} for tier, items in split_rows.items()},
        "evaluator_tiers": tier_metadata,
        "candidate_training_csv": str(public_data),
        "candidate_initial_model": str(initial_model),
        "private_evaluator_dir": str(private),
        "metric": "accuracy",
        "stretch_target": 0.95,
        "target_is_guaranteed": False,
        "created_by": "benchmarks/titanic_survival/prepare.py",
    }
    metadata_path = output_path / "benchmark.json"
    metadata_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata_path.chmod(0o600)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="OpenML 40945 v1 ARFF file")
    parser.add_argument("--output", required=True, help="private benchmark workspace outside candidate source repo")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, seed=args.seed), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
