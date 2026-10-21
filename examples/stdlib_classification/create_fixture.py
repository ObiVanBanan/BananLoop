from __future__ import annotations

import argparse
import csv
import itertools
import json
import subprocess
import textwrap
from pathlib import Path


VALUES = (-2.0, -1.0, 1.0, 2.0)


def _label(x1: float, x2: float, x3: float, x4: float) -> int:
    del x4
    return int((x1 + x2 + 0.5 * x3) > 0.0)


def _write_dataset(path: Path, *, split: str, x4_values: tuple[float, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    index = 0
    for x1, x2, x3, x4 in itertools.product(VALUES, VALUES, VALUES, x4_values):
        rows.append(
            {
                "case_id": f"{split}-{index:04d}",
                "x1": x1,
                "x2": x2,
                "x3": x3,
                "x4": x4,
                "label": _label(x1, x2, x3, x4),
            }
        )
        index += 1
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "x1", "x2", "x3", "x4", "label"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_dataset(
        root / "data" / "train.csv",
        split="train",
        x4_values=(-1.0, 1.0),
    )
    _write_dataset(
        root / "data" / "eval.csv",
        split="eval",
        x4_values=(-0.5, 0.5),
    )

    (root / "model.py").write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations


            def predict(features: tuple[float, float, float, float]) -> int:
                \"""Intentionally weak but valid baseline classifier.\"""

                x1, _x2, _x3, _x4 = features
                return int(x1 > 0.0)
            """
        ),
        encoding="utf-8",
    )

    evaluator = textwrap.dedent(
        """\
        from __future__ import annotations

        import csv
        import json
        from pathlib import Path

        from model import predict


        ROOT = Path(__file__).resolve().parent


        def load_rows(path: Path) -> list[tuple[str, tuple[float, float, float, float], int]]:
            rows = []
            with path.open(newline="", encoding="utf-8") as handle:
                for item in csv.DictReader(handle):
                    rows.append(
                        (
                            item["case_id"],
                            (
                                float(item["x1"]),
                                float(item["x2"]),
                                float(item["x3"]),
                                float(item["x4"]),
                            ),
                            int(item["label"]),
                        )
                    )
            return rows


        def balanced_accuracy(path: Path) -> tuple[float, int]:
            rows = load_rows(path)
            recalls = []
            for label in (0, 1):
                subset = [(features, expected) for _case_id, features, expected in rows if expected == label]
                correct = sum(predict(features) == expected for features, expected in subset)
                recalls.append(correct / len(subset))
            return sum(recalls) / 2.0, len(rows)


        def main() -> None:
            score, count = balanced_accuracy(ROOT / "data" / "eval.csv")
            print(json.dumps({"balanced_accuracy": score, "count": count}, sort_keys=True))


        if __name__ == "__main__":
            main()
        """
    )
    (root / "evaluate.py").write_text(evaluator, encoding="utf-8")

    (root / "score_train.py").write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import json
            from pathlib import Path

            from evaluate import balanced_accuracy


            score, count = balanced_accuracy(Path(__file__).resolve().parent / "data" / "train.csv")
            print(json.dumps({"balanced_accuracy": score, "count": count}, sort_keys=True))
            """
        ),
        encoding="utf-8",
    )

    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "bananloop-stdlib-ml-dogfood"
            version = "0.1.0"
            requires-python = ">=3.11"
            dependencies = []
            """
        ),
        encoding="utf-8",
    )

    (root / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.bananloop/\n",
        encoding="utf-8",
    )

    (root / "README.md").write_text(
        textwrap.dedent(
            """\
            # Stdlib classification dogfood fixture

            A tiny deterministic binary-classification project for BananLoop end-to-end dogfood.
            It uses only the Python standard library and never needs network access, package
            installation, Docker, databases, API keys, or model downloads.

            ## Normal project workflow

            - `python score_train.py` evaluates the current classifier on visible training data.
            - `python evaluate.py` is the authoritative fixed benchmark and reports
              `balanced_accuracy`.
            - Production classifier code lives in `model.py`.

            The initial model is intentionally valid but mediocre. General improvements should
            be learned from `data/train.csv`, not from benchmark answers.

            ## Evaluation boundary

            `data/eval.csv` is evaluator-owned fixed benchmark data. A BananLoop campaign must
            freeze and protect it together with the evaluator adapter. Implementation workers
            must not inspect benchmark labels, case IDs, or hardcode predictions from it.

            Baseline and every candidate must be measured on the exact same evaluation rows.
            Fixed input identity does not imply paired statistical acceptance.

            The useful optimization target is `balanced_accuracy >= 0.98`, but a scientifically
            valid ACCEPT/REJECT/PLATEAU cycle is more important than reaching the target.
            """
        ),
        encoding="utf-8",
    )

    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_model.py").write_text(
        textwrap.dedent(
            """\
            from model import predict


            def test_predict_returns_binary_label():
                assert predict((1.0, 0.0, 0.0, 0.0)) in {0, 1}
                assert predict((-1.0, 0.0, 0.0, 0.0)) in {0, 1}
            """
        ),
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def create_fixture(root: Path) -> dict[str, object]:
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"target directory is not empty: {root}")
    _write_project(root)
    _git(root, "init")
    _git(root, "config", "user.email", "bananloop-dogfood@localhost")
    _git(root, "config", "user.name", "BananLoop Dogfood")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Initial deterministic stdlib ML fixture")

    result = subprocess.run(
        ["python", "evaluate.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    baseline = json.loads(result.stdout)
    return {
        "path": str(root.resolve()),
        "baseline": baseline,
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default="/tmp/bananloop-stdlib-ml-dogfood",
        help="empty directory to create",
    )
    args = parser.parse_args()
    print(json.dumps(create_fixture(Path(args.path)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
