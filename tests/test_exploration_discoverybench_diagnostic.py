"""Offline checks for a pinned, PUBLIC DiscoveryBench diagnostic CSV.

These tests do not call an LLM, an official HMS judge, or a hidden evaluator.
"""
import csv
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "examples" / "exploration_diagnostic_v1" / "discoverybench_boardgames"


def load():
    with (ROOT / "data.csv").open("r", newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        return list(reader.fieldnames or []), list(reader)


def test_dataset_complete():
    fields, rows = load()
    assert len(fields) == 42
    assert len(rows) == 500
    assert all(len(r) == 42 and None not in r and None not in r.values() for r in rows)


def test_question_columns_present():
    fields, rows = load()
    assert {"tournament_win_ratio", "unique_strategies_developed"} <= set(fields)
    for key in ("tournament_win_ratio", "unique_strategies_developed"):
        assert all(r[key] not in ("", "nan", "NaN") and math.isfinite(float(r[key])) for r in rows)


def test_no_gold_in_agent_task():
    description = (ROOT / "TASK.md").read_text(encoding="utf-8")
    assert "Is there a relationship between tournament win ratio and the number of unique strategies developed" in description
    assert "true_hypothesis" not in description
    assert "answer_key" not in (ROOT / "data.csv").read_text(encoding="utf-8")
    attribution = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ODC-BY" in attribution
    assert "c31fcf011e070f021a5f5b906896d0821f6880e8" in attribution
