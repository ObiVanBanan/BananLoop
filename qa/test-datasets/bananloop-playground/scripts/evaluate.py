"""Run the fixed catalog matching benchmark and print BananLoop JSON."""
import argparse, csv, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from matcher import best_match
parser = argparse.ArgumentParser()
parser.add_argument("--split", choices=("dev", "validation", "holdout"), default="dev")
args = parser.parse_args()
with (ROOT / "data/products.csv").open(encoding="utf-8", newline="") as f:
    products = list(csv.DictReader(f))
with (ROOT / "data/queries.csv").open(encoding="utf-8", newline="") as f:
    rows = [r for r in csv.DictReader(f) if r["split"] == args.split]
if not rows:
    raise SystemExit(f"no rows for split {args.split}")
start = time.perf_counter()
correct = sum(best_match(r["query"], products) == r["expected_product_id"] for r in rows)
elapsed_ms = (time.perf_counter() - start) * 1000
print(json.dumps({"schema_version":1,"status":"ok","metrics":{"accuracy":correct/len(rows),"latency_ms":elapsed_ms},"checks":{"benchmark_nonempty":True},"diagnostics":{"cases":len(rows)}},ensure_ascii=False))
