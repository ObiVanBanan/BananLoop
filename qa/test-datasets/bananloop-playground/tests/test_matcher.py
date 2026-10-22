import csv, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from matcher import best_match
class MatcherSmokeTest(unittest.TestCase):
    def test_returns_a_catalog_id_for_nonempty_query(self):
        with (ROOT / "data/products.csv").open(encoding="utf-8", newline="") as f:
            products = list(csv.DictReader(f))
        self.assertIn(best_match("Кран шаровой DN25 PN16", products), {p["product_id"] for p in products})
    def test_empty_query_has_no_match(self):
        self.assertIsNone(best_match("   !!!", []))
if __name__ == "__main__":
    unittest.main()
