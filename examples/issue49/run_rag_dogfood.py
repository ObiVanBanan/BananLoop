"""Run a frozen Qdrant-backed retrieval campaign through the public CLI.

The fixture is synthetic and deliberately small. It proves that the ordinary
BananLoop engine can evaluate a candidate against a live vector service while
keeping validation and final holdout assets out of the agent workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


BASELINE_SOURCE = r'''from __future__ import annotations
import hashlib, json, math, os, re, urllib.request, uuid

BASE = os.environ.get("BANANLOOP_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")

def _request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = response.read()
    return json.loads(payload) if payload else {}

def _vector(text):
    features = re.findall(r"[a-zа-яё0-9]+", text.casefold())
    result = [0.0] * 128
    for feature in features:
        raw = hashlib.blake2b(feature.encode(), digest_size=4).digest()
        slot = int.from_bytes(raw[:2], "big") % len(result)
        result[slot] += 1.0 if raw[2] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in result)) or 1.0
    return [value / norm for value in result]

class Retriever:
    def __init__(self, products):
        self.collection = "bl_rag_" + uuid.uuid4().hex
        self.products = products
        _request("PUT", "/collections/" + self.collection,
                 {"vectors": {"size": 128, "distance": "Cosine"}})
        _request("PUT", "/collections/" + self.collection + "/points?wait=true", {
            "points": [{"id": i + 1, "vector": _vector(item["name"]),
                        "payload": {"product_id": item["product_id"]}}
                       for i, item in enumerate(products)]})

    def search(self, query):
        result = _request("POST", "/collections/" + self.collection + "/points/search", {
            "vector": _vector(query), "limit": 1, "with_payload": True})
        points = result.get("result", [])
        return (points[0]["payload"]["product_id"], points[0]["score"]) if points else (None, 0.0)

    def close(self):
        try:
            _request("DELETE", "/collections/" + self.collection)
        except Exception:
            pass
'''


CANDIDATE_SOURCE = r'''from __future__ import annotations
import hashlib, json, math, os, re, urllib.request, uuid

BASE = os.environ.get("BANANLOOP_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
ALIASES = {
    "шаровой": "шаровой ball valve", "шаровый": "шаровой ball valve",
    "кран": "кран valve", "стальной": "сталь steel", "сталь": "сталь steel",
    "латунный": "латунь brass", "латунь": "латунь brass",
    "фланцевый": "фланец flange flanged", "фланец": "фланец flange flanged",
    "муфтовый": "муфта threaded", "резьбовой": "резьба threaded",
    "плоский": "плоский flat", "тройник": "тройник tee",
    "равнопроходный": "равнопроходный welded", "фильтр": "фильтр filter",
    "сетчатый": "сетка mesh", "ду": "dn", "дн": "dn",
}

def _request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = response.read()
    return json.loads(payload) if payload else {}

def _vector(text):
    normalized = text.casefold().replace("ё", "е")
    normalized = re.sub(r"(?:ду|дн|dn|d\s*n|d\s*u)\s*[-:]?\s*(\d+)", r" dn\1 ", normalized)
    normalized = re.sub(r"(?:pn)\s*[-:]?\s*(\d+)", r" pn\1 ", normalized)
    tokens = re.findall(r"[a-zа-я0-9]+", normalized)
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(ALIASES.get(token, "").split())
    features = set(expanded)
    for token in expanded:
        padded = "^" + token + "$"
        features.update(padded[i:i + 3] for i in range(max(1, len(padded) - 2)))
    result = [0.0] * 256
    for feature in features:
        raw = hashlib.blake2b(feature.encode(), digest_size=4).digest()
        slot = int.from_bytes(raw[:2], "big") % len(result)
        result[slot] += 1.0 if raw[2] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in result)) or 1.0
    return [value / norm for value in result]

class Retriever:
    def __init__(self, products):
        self.collection = "bl_rag_" + uuid.uuid4().hex
        self.products = products
        _request("PUT", "/collections/" + self.collection,
                 {"vectors": {"size": 256, "distance": "Cosine"}})
        points = []
        for i, item in enumerate(products):
            text = " ".join(str(item.get(key, "")) for key in
                            ("name", "family", "dn", "pn", "material", "joint"))
            points.append({"id": i + 1, "vector": _vector(text),
                           "payload": {"product_id": item["product_id"]}})
        _request("PUT", "/collections/" + self.collection + "/points?wait=true",
                 {"points": points})

    def search(self, query):
        result = _request("POST", "/collections/" + self.collection + "/points/search", {
            "vector": _vector(query), "limit": 1, "with_payload": True})
        points = result.get("result", [])
        if not points or points[0]["score"] < 0.24:
            return None, points[0]["score"] if points else 0.0
        return points[0]["payload"]["product_id"], points[0]["score"]

    def close(self):
        try:
            _request("DELETE", "/collections/" + self.collection)
        except Exception:
            pass
'''


AGENT_SOURCE = r'''import json, sys
from pathlib import Path

task = json.load(sys.stdin)
if Path(".bananloop_eval").exists():
    raise SystemExit("hidden evaluator assets are visible to the agent workspace")
if task.get("role") == "propose":
    hypothesis = {
        "hypothesis": "Normalize Russian and English catalog terms and add character n-gram features before Qdrant cosine retrieval",
        "rationale": "A live Qdrant-backed unigram baseline misses cross-language aliases and spelling variants; a bounded normalization and n-gram representation should improve top-1 retrieval while retaining abstention on unrelated queries.",
        "expected_effect": "Higher recall_at_1 with zero false matches and no meaningful p95 latency regression",
        "files_or_area": ["src/retrieval.py"],
        "falsification_condition": "DEV recall does not improve or validation guardrails fail",
        "mechanism_key": "normalized-character-ngram-qdrant-retrieval",
        "relation": "new", "prior_experiment_refs": [],
        "novelty_rationale": "The baseline uses raw unigram vectors without normalization or alias features.",
    }
    print(json.dumps({"candidates": [{
        "candidate_id": "z-normalize-qdrant-vectors", "hypothesis": hypothesis,
        "evidence_refs": ["INSUFFICIENT_EVIDENCE"],
        "suspected_failure_mode": "cross-language and notation variants have no shared unigram features",
        "predicted_observable": "recall_at_1 rises while false_match_rate stays zero",
        "discriminating_test": "same hidden frozen tiers and live Qdrant service before and after the vectorizer change",
        "scope_eligibility": True, "estimated_agent_calls": 1,
        "estimated_evaluator_calls": 1, "estimated_work_units": None,
        "rationale": "one bounded retrieval representation change measured by an independent evaluator",
        "uncertainty": "small synthetic product catalog and one local Qdrant service",
        "inference": "agent_inference",
    }]}))
else:
    Path("src/retrieval.py").write_text(Path("candidate_source.py").read_text(encoding="utf-8"), encoding="utf-8")
    print(json.dumps({"summary": "add normalized n-gram vectors and abstention to Qdrant retrieval",
                      "changed_paths": ["src/retrieval.py"],
                      "verification_notes": "frozen service-backed evaluator measures retrieval and guardrails",
                      "blockers": []}))
'''


EVALUATOR_SOURCE = r'''import json, math, os, statistics, sys, time, urllib.request, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from retrieval import Retriever

base = os.environ.get("BANANLOOP_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
with urllib.request.urlopen(base + "/", timeout=3) as response:
    service = json.loads(response.read())
if service.get("title") != "qdrant - vector search engine":
    raise SystemExit("Qdrant service identity check failed")
tier = os.environ.get("BANANLOOP_EVALUATION_TIER", "dev")
workload = json.loads((Path(__file__).parent / "workloads" / f"{tier}.json").read_text(encoding="utf-8"))
retriever = Retriever(workload["products"])
latencies, results = [], []
try:
    for case in workload["queries"]:
        start = time.perf_counter_ns()
        observed, score = retriever.search(case["query"])
        latencies.append((time.perf_counter_ns() - start) / 1_000_000)
        results.append((case, observed, score))
finally:
    retriever.close()
valid = [(case, observed, score) for case, observed, score in results if case["expected_product_id"] is not None]
unknown = [(case, observed, score) for case, observed, score in results if case["expected_product_id"] is None]
correct = sum(observed == case["expected_product_id"] for case, observed, _ in valid)
false_matches = sum(observed is not None for _, observed, _ in unknown)
false_rejects = sum(observed is None for _, observed, _ in valid)
p95 = sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1]
print(json.dumps({
    "status": "ok", "metrics": {
        "recall_at_1": correct / len(valid) if valid else 0.0,
        "latency_p95_ms": p95,
        "false_match_rate": false_matches / len(unknown) if unknown else 0.0,
        "false_reject_rate": false_rejects / len(valid) if valid else 0.0,
    },
    "checks": {"service_identity": True, "nonempty_workload": bool(results),
               "all_queries_measured": len(results) == len(workload["queries"])},
    "diagnostics": {"query_count": len(results), "valid_query_count": len(valid),
                    "unknown_query_count": len(unknown), "service_version": service.get("version")},
    "resource_units": len(results) + 2,
}, sort_keys=True))
'''


PRODUCTS = [
    {"product_id": "LD-1001", "name": "Кран шаровой стальной фланцевый DN25 PN16", "family": "ball_valve", "dn": "25", "pn": "16", "material": "steel", "joint": "flanged"},
    {"product_id": "LD-1002", "name": "Кран шаровой стальной фланцевый DN80 PN16", "family": "ball_valve", "dn": "80", "pn": "16", "material": "steel", "joint": "flanged"},
    {"product_id": "LD-1003", "name": "Кран шаровой стальной фланцевый DN80 PN40", "family": "ball_valve", "dn": "80", "pn": "40", "material": "steel", "joint": "flanged"},
    {"product_id": "LD-1004", "name": "Кран шаровой латунный муфтовый DN25 PN16", "family": "ball_valve", "dn": "25", "pn": "16", "material": "brass", "joint": "threaded"},
    {"product_id": "LD-2001", "name": "Фланец плоский стальной DN100 PN16", "family": "flange", "dn": "100", "pn": "16", "material": "steel", "joint": "flanged"},
    {"product_id": "LD-2002", "name": "Фланец плоский стальной DN80 PN10", "family": "flange", "dn": "80", "pn": "10", "material": "steel", "joint": "flanged"},
    {"product_id": "LD-3001", "name": "Тройник стальной равнопроходный DN50", "family": "tee", "dn": "50", "pn": "", "material": "steel", "joint": "welded"},
    {"product_id": "LD-4001", "name": "Фильтр сетчатый латунный муфтовый DN25 PN16", "family": "filter", "dn": "25", "pn": "16", "material": "brass", "joint": "threaded"},
]


WORKLOADS = {
    "dev": [
        ("steel ball valve flanged DN25 PN16", "LD-1001"),
        ("стальной шаровый кран фланцевый Ду80 PN16", "LD-1002"),
        ("кран шаровой сталь фланец DN80 PN40", "LD-1003"),
        ("латунный резьбовой valve DN25 PN16", "LD-1004"),
        ("плоский стальной фланец DN100 PN16", "LD-2001"),
        ("flat steel flange DN80 PN10", "LD-2002"),
        ("стальной равнопроходный тройник DN50", "LD-3001"),
        ("латунный сетчатый фильтр муфтовый DN25 PN16", "LD-4001"),
        ("саморез по дереву 4x40", None),
        ("usb cable type c", None),
    ],
    "validation": [
        ("ball valve steel flange d n 80 pn16", "LD-1002"),
        ("кран шаровый стальной фланцевый DN80 PN40", "LD-1003"),
        ("brass threaded ball valve DN25 PN16", "LD-1004"),
        ("фланец плоский из стали ду100 pn16", "LD-2001"),
        ("стальной фланец плоский DN80 PN10", "LD-2002"),
        ("равнопроходный стальной tee dn50", "LD-3001"),
        ("brass mesh filter threaded DN25 PN16", "LD-4001"),
        ("шаровой кран сталь фланцы dn25 pn16", "LD-1001"),
        ("ceramic coffee mug", None),
        ("wireless mouse", None),
    ],
    "holdout": [
        ("steel flanged ball valve DN80 PN16", "LD-1002"),
        ("стальной фланцевый шаровой кран ду80 pn40", "LD-1003"),
        ("муфтовый латунный кран шаровый DN25 PN16", "LD-1004"),
        ("flat flange steel DN100 PN16", "LD-2001"),
        ("плоский стальной flange ду80 pn10", "LD-2002"),
        ("сталь равнопроходный тройник Ду50", "LD-3001"),
        ("латунный threaded mesh filter DN25 PN16", "LD-4001"),
        ("кран ball valve сталь DN25 PN16 flanged", "LD-1001"),
        ("garden hose 20 meters", None),
        ("smartphone screen protector", None),
    ],
}


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, check=True, text=True, capture_output=True)


def qdrant_probe(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url.rstrip("/") + "/", timeout=3) as response:
        info = json.loads(response.read())
    if info.get("title") != "qdrant - vector search engine":
        raise RuntimeError("configured service is not the expected Qdrant API")
    return {"version": info.get("version"), "commit": info.get("commit")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True, help="new or empty fixture/state directory")
    parser.add_argument("--qdrant-url", default=os.environ.get("BANANLOOP_QDRANT_URL", "http://127.0.0.1:6333"))
    args = parser.parse_args()
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {work}")
    service = qdrant_probe(args.qdrant_url)
    project, state = work / "project", work / "state"
    evaluation = project / ".bananloop_eval"
    (project / "src").mkdir(parents=True)
    (evaluation / "workloads").mkdir(parents=True)
    (project / "src" / "retrieval.py").write_text(BASELINE_SOURCE, encoding="utf-8")
    (project / "candidate_source.py").write_text(CANDIDATE_SOURCE, encoding="utf-8")
    (project / "agent.py").write_text(AGENT_SOURCE, encoding="utf-8")
    (evaluation / "evaluate.py").write_text(EVALUATOR_SOURCE, encoding="utf-8")
    for tier, pairs in WORKLOADS.items():
        payload = {"products": PRODUCTS, "queries": [
            {"query": query, "expected_product_id": expected} for query, expected in pairs
        ]}
        (evaluation / "workloads" / f"{tier}.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["BANANLOOP_QDRANT_URL"] = args.qdrant_url.rstrip("/")
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cli = [sys.executable, "-m", "bananloop.cli"]
    run(["git", "init"], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "add", "."], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "frozen Qdrant retrieval fixture"], cwd=project, env=env)

    run(cli + [
        "init", "--repo", str(project), "--state-dir", str(state),
        "--goal", "Improve frozen product retrieval recall while rejecting unrelated queries",
        "--eval-cmd", "python .bananloop_eval/evaluate.py",
        "--validation-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--holdout-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--eval-asset", ".bananloop_eval/evaluate.py",
        *[arg for tier in WORKLOADS for arg in ("--eval-asset", f".bananloop_eval/workloads/{tier}.json")],
        "--agent-hide", ".bananloop_eval/**",
        "--metric", "recall_at_1", "--direction", "maximize", "--target", "0.95",
        "--min-improvement", "0.05", "--guard", "false_match_rate<=0",
        "--guard", "false_reject_rate<=0.25", "--guard", "latency_p95_ms<=500",
        "--check", "service_identity", "--check", "nonempty_workload", "--check", "all_queries_measured",
        "--non-regression", "latency_p95_ms:minimize:50",
        "--editable", "src/retrieval.py", "--protect", "agent.py", "--protect", "candidate_source.py",
        "--protect", ".bananloop_eval/**",
        "--max-experiments", "1", "--max-attempts", "2", "--max-agent-calls", "4",
        "--max-evaluator-calls", "6", "--max-evaluation-units", "60",
        "--evaluation-units-per-call", "12", "--deadline-seconds", "180",
        "--agent", "command", "--agent-cmd", "python agent.py", "--yes",
    ], cwd=project, env=env)
    run(cli + ["run", "--repo", str(project), "--state-dir", str(state)], cwd=project, env=env)
    status = json.loads(run(cli + ["status", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    run(cli + ["resume", "--repo", str(project), "--state-dir", str(state)], cwd=project, env=env)
    after_resume = json.loads(run(cli + ["status", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    if (status["agent_calls"], status["evaluator_calls"]) != (after_resume["agent_calls"], after_resume["evaluator_calls"]):
        raise RuntimeError("terminal resume duplicated completed agent or evaluator actions")
    resources = status["resources"]
    if (resources["evaluation_units_limit"] != 60
            or resources["evaluation_units_reported"] > 60
            or resources["evaluation_units_reserved"] > 60):
        raise RuntimeError("campaign exceeded its configured evaluation-unit budget")
    if resources["open_reservations"] != 0:
        raise RuntimeError("campaign left an open resource reservation")
    report = json.loads(run(cli + ["report", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    protocol = report["evaluation_protocol"]
    output = {
        "domain": "qdrant_service_backed_product_retrieval",
        "fixture_commit": run(["git", "rev-parse", "HEAD"], cwd=project, env=env).stdout.strip(),
        "qdrant": service,
        "status": status["status"], "stop_reason": status.get("stop_reason"),
        "campaign_id": status["campaign_id"],
        "campaign_calls": {"agent": status["agent_calls"], "evaluator": status["evaluator_calls"]},
        "campaign_budget": {"experiments": 1, "agent_calls": 4, "evaluator_calls": 6,
                            "evaluation_units": 60, "units_per_evaluator_call": 12},
        "resources": resources,
        "terminal_resume": {"agent_calls_unchanged": True, "evaluator_calls_unchanged": True},
        "hidden_evaluator_assets_absent_from_agent_workspace": True,
        "dev_metrics": [item["metrics"] for item in status["evaluations"] if item["tier"] == "dev"],
        "validation": protocol["validation"], "holdout": protocol["holdout"],
        "final_assessment": report.get("final_assessment"),
        "changed_paths": report["experiments"][0]["candidate_proof"]["changed_paths"],
        "receipts": [{"tier": item["tier"], "action_id": item["action_id"], "receipt_hash": item["receipt_hash"]}
                     for item in report["evaluation_receipts"]],
        "claim_limit": "synthetic 8-product fixture, command adapter, and one local Qdrant service; not evidence about a production RAG system",
    }
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
