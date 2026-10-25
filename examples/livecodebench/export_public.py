"""Export only stdin-style, public LiveCodeBench data from a pinned HF revision.

Install datasets separately, and run this in a data preparation environment.
Do not run the loader where BananLoop agents can see the private source data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .pilot import PUBLIC_FIELDS, stdin_samples
else:
    from pilot import PUBLIC_FIELDS, stdin_samples


def sanitized(record: dict, *, difficulty: str) -> dict | None:
    if difficulty != "any" and str(record.get("difficulty", "")).lower() != difficulty:
        return None
    # Project only an explicit allowlist; do not serialize private_test_cases.
    public = {key: record[key] for key in PUBLIC_FIELDS if key in record}
    if stdin_samples(public) is None:
        return None
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release", default="v6")
    parser.add_argument("--revision", required=True,
                        help="full pinned huggingface dataset Git commit SHA")
    parser.add_argument("--difficulty", choices=("hard", "medium", "easy", "any"),
                        default="hard")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("refusing to overwrite a public data export")
    if not args.revision or len(args.revision) != 40 or any(
        x not in "0123456789abcdef" for x in args.revision.lower()
    ):
        raise ValueError("--revision must be a pinned 40-character commit SHA")
    if args.limit < 1:
        raise ValueError("--limit must be >= 1")
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("install Hugging Face datasets in the preparation environment") from exc
    stream = load_dataset(
        "livecodebench/code_generation_lite", args.release,
        split="test", revision=args.revision, streaming=True,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_suffix(args.out.suffix + ".partial")
    count = 0
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as output:
            for record in stream:
                projected = sanitized(record, difficulty=args.difficulty)
                if projected is None:
                    continue
                output.write(json.dumps(projected, ensure_ascii=False) + "\n")
                count += 1
                if count >= args.limit:
                    break
        if count == 0:
            raise RuntimeError("no stdin public tasks found for requested constraints")
        temp.replace(args.out)
    finally:
        if temp.exists():
            temp.unlink()
    print(json.dumps({
        "out": str(args.out), "count": count,
        "dataset": "livecodebench/code_generation_lite",
        "release": args.release, "revision": args.revision,
        "difficulty": args.difficulty,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
