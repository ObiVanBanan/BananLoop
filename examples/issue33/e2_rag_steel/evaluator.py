from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path.cwd()
FIXTURE_DIR = ROOT / "examples/issue33/e2_rag_steel/fixtures"
sys.path.insert(0, str(ROOT / "src"))

from rag_steel.normalization import normalize_article  # noqa: E402
from rag_steel.search_engine import SearchEngine  # noqa: E402


MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
os.environ.setdefault("EMBEDDING_REVISION", MODEL_REVISION)
CATALOG_SHA256 = "2b2b952609dfff21c1fe595451704f3b7a835000a146e304a8ccdcf33079e22c"
SPLIT_SHA256 = {
    "dev": "9228fa09387daa0ac2e653e402f2e14dad0b651c84e72acd5d2167254c99fbdf",
    "validation": "a20f562ef7b6a247c7159bae1aa639a643cd6b7b38c206cf833ea1d63a05d75b",
}


class TracingSearchEngine(SearchEngine):
    __slots__ = ("_last_source_candidate_count",)

    def __post_init__(self) -> None:
        super().__post_init__()
        self._last_source_candidate_count = 0

    def _fallback_collection_metadata_from_report(self) -> dict:
        return json.loads((FIXTURE_DIR / "index_build.json").read_text(encoding="utf-8"))

    def _rank_ld_candidates(self, processed, source_results):
        self._last_source_candidate_count = len(source_results)
        return super()._rank_ld_candidates(processed, source_results)


def _normalized(value: str) -> str:
    result = normalize_article(value)
    return result.article_norm or str(value).strip().casefold()


def _load_cases(split: str) -> tuple[list[dict], str]:
    path = FIXTURE_DIR / f"issue33_{split}.jsonl"
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != SPLIT_SHA256[split]:
        raise RuntimeError(f"fixed {split} query set identity mismatch")
    cases = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return cases, actual


def evaluate(split: str) -> dict:
    if split not in {"smoke", "dev", "validation"}:
        raise ValueError("split must be smoke, dev, or validation")
    selected_split = "dev" if split == "smoke" else split
    records, split_sha256 = _load_cases(selected_split)
    if split == "smoke":
        records = records[:9]

    report_path = FIXTURE_DIR / "index_build.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    catalog_path = FIXTURE_DIR / "catalog70.csv"
    catalog_sha = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    engine = TracingSearchEngine(
        model_name=MODEL,
        qdrant_url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"),
        collection_alias=os.environ.get("QDRANT_COLLECTION_ALIAS", "issue33_e2_base"),
    )
    ready, readiness = engine.readiness_status()
    alias_targets = {
        item.alias_name: item.collection_name
        for item in engine._get_client().get_aliases().aliases
    }
    identity_pass = bool(
        ready
        and catalog_sha == CATALOG_SHA256
        and report.get("csv_sha256") == CATALOG_SHA256
        and report.get("embedding_model") == MODEL
        and report.get("embedding_revision") == MODEL_REVISION
        and report.get("embedding_dimension") == 384
        and report.get("document_count") == 72
        and readiness.get("details", {}).get("index_revision") == MODEL_REVISION
        and alias_targets.get(engine.collection_alias) == report.get("collection_name")
    )
    if not identity_pass:
        raise RuntimeError("pinned source catalog, Qdrant index, or embedding identity does not match")

    positive_total = 0
    positive_hits = 0
    negative_total = 0
    false_matches = 0
    contradiction_total = 0
    contradiction_violations = 0
    retrieval_misses = 0
    wrong_ld_selection = 0
    constraint_mismatches = 0
    case_quality: list[float] = []
    case_rows: list[dict] = []
    result_hashes: dict[str, set[str]] = {}

    for index, record in enumerate(records):
        query = str(record["query"])
        expected = {_normalized(item) for item in record["expected_ld_articles"]}
        input_identity = hashlib.sha256(
            json.dumps(
                {"query": query, "expected": sorted(expected), "split": selected_split},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        response = engine.search(query, limit=20)
        predicted = [
            _normalized(str(result.product.get("article_norm") or result.product.get("article")))
            for result in response.results
            if result.product.get("article_norm") or result.product.get("article")
        ]
        if len(predicted) != len(set(predicted)):
            raise RuntimeError("search response contains duplicate LD articles")
        found = bool(set(predicted) & expected) if expected else not predicted
        case_quality.append(1.0 if found else 0.0)
        category = str(record["category"])
        case_id = f"{selected_split}-{index:03d}"
        case_rows.append(
            {
                "case_id": case_id,
                "category": category,
                "expected_match": bool(expected),
                "passed": found,
                "source_candidate_count": engine._last_source_candidate_count,
                "returned_count": len(predicted),
            }
        )
        result_hashes[case_id] = set(predicted)

        if expected:
            positive_total += 1
            positive_hits += int(found)
            if not found:
                if engine._last_source_candidate_count == 0:
                    retrieval_misses += 1
                else:
                    wrong_ld_selection += 1
        else:
            negative_total += 1
            false_matches += int(bool(predicted))
            if category == "technical_contradiction":
                contradiction_total += 1
                contradiction_violations += int(bool(predicted))
                constraint_mismatches += int(
                    bool(predicted) and any(result.mismatches for result in response.results)
                )

    if len(case_rows) != len(records) or not records:
        raise RuntimeError("fixed query set was not completely evaluated")
    hard_pass_rate = sum(case_quality) / len(case_quality)
    false_match_rate = false_matches / negative_total if negative_total else 0.0
    contradiction_rate = (
        contradiction_violations / contradiction_total if contradiction_total else 0.0
    )
    no_match_total = sum(row["category"] == "no_match" for row in records)
    no_match_false_matches = sum(
        1
        for row, result_set in zip(records, result_hashes.values())
        if row["category"] == "no_match" and result_set
    )
    metrics = {
        "hard_pass_rate": hard_pass_rate,
        "false_match_rate": false_match_rate,
        "positive_recall": positive_hits / positive_total if positive_total else 0.0,
        "contradiction_violation_rate": contradiction_rate,
        "no_match_false_match_rate": no_match_false_matches / no_match_total if no_match_total else 0.0,
    }
    protocol_identity = {
        "catalog_sha256": catalog_sha,
        "index_collection": report["collection_name"],
        "index_alias": report["collection_alias"],
        "embedding_model": MODEL,
        "embedding_revision": MODEL_REVISION,
        "split": selected_split,
        "split_sha256": split_sha256,
        "hard_pass_contract": "expected article intersection for positives; empty result for negatives",
        "retrieval_limit": 20,
    }
    protocol_sha256 = hashlib.sha256(
        json.dumps(protocol_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary_path = Path(os.environ["BANANLOOP_ARTIFACT_DIR"]) / "e2_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "protocol": protocol_identity,
                "metrics": metrics,
                "diagnostics": {
                    "retrieval_miss_count": retrieval_misses,
                    "wrong_ld_selection_count": wrong_ld_selection,
                    "constraint_mismatch_count": constraint_mismatches,
                    "technical_contradiction_count": contradiction_total,
                    "case_count": len(case_rows),
                },
                "cases": case_rows,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    artifact_sha = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    metric_units = {
        "hard_pass_rate": ("fraction", len(case_rows)),
        "false_match_rate": ("fraction", negative_total),
        "positive_recall": ("fraction", positive_total),
        "contradiction_violation_rate": ("fraction", contradiction_total),
        "no_match_false_match_rate": ("fraction", no_match_total),
    }
    return {
        "schema_version": 1,
        "status": "ok",
        "protocol_sha256": protocol_sha256,
        "metrics": {
            name: {"value": value, "unit": metric_units[name][0], "n": metric_units[name][1]}
            for name, value in metrics.items()
        },
        "diagnostics": {
            "retrieval_miss_count": float(retrieval_misses),
            "wrong_ld_selection_count": float(wrong_ld_selection),
            "constraint_mismatch_count": float(constraint_mismatches),
            "technical_contradiction_count": float(contradiction_total),
        },
        "checks": {
            "fixed_query_set_complete": {"passed": len(case_rows) == len(records), "executed": len(case_rows)},
            "no_duplicate_ld_articles": {"passed": True, "executed": len(case_rows)},
            "pinned_catalog_model_index_identity": {"passed": identity_pass, "executed": 1},
        },
        "cases": [
            {
                "case_id": f"{selected_split}-{index:03d}",
                "repetition": 0,
                "input_sha256": case["input_sha256"],
                "metrics": {"case_quality": case_quality[index]},
            }
            for index, case in enumerate(
                [
                    {
                        "input_sha256": hashlib.sha256(
                            json.dumps(
                                {
                                    "query": str(record["query"]),
                                    "expected": sorted(
                                        _normalized(item)
                                        for item in record["expected_ld_articles"]
                                    ),
                                    "split": selected_split,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()
                    }
                    for record in records
                ]
            )
        ],
        "artifacts": ["e2_summary.json"],
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) != 2 or args[0] != "--split":
        raise SystemExit("usage: evaluator.py --split smoke|dev|validation")
    result = evaluate(args[1])
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
