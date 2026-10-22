"""Frozen split evaluator. BananLoop should protect this directory."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data import load_split, load_training_data  # noqa: E402
from model import build_model  # noqa: E402
from sklearn.metrics import average_precision_score, balanced_accuracy_score  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "validation", "holdout"), required=True)
    args = parser.parse_args()

    train_x, train_y = load_training_data()
    eval_x, eval_y = load_split(args.split)
    if not train_x or not eval_x or len(set(eval_y)) < 2:
        raise SystemExit("training and evaluation data must be nonempty and contain both classes")
    if any("duration" in row for row in train_x + eval_x):
        raise SystemExit("leakage check failed: duration must be absent")

    started = time.perf_counter()
    model = build_model()
    model.fit(train_x, train_y)
    probabilities = model.predict_proba(eval_x)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    elapsed = time.perf_counter() - started
    result = {
        "schema_version": 1,
        "status": "ok",
        "metrics": {
            "pr_auc": float(average_precision_score(eval_y, probabilities)),
            "balanced_accuracy": float(balanced_accuracy_score(eval_y, predictions)),
        },
        "checks": {"nonempty_split": True, "duration_excluded": True},
        "diagnostics": {
            "split": args.split,
            "rows": len(eval_y),
            "positive_rate": float(sum(eval_y) / len(eval_y)),
            "prediction_rate_at_0_5": float(sum(predictions) / len(predictions)),
            "elapsed_seconds": round(elapsed, 3),
        },
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
