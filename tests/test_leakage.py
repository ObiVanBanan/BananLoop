from bananloop.leakage import added_diff_lines, scan_forbidden_diff_patterns


def test_scanner_checks_only_added_lines():
    diff = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-from secret_gold import labels
+from production.rules import normalize
"""
    assert added_diff_lines(diff) == ["from production.rules import normalize"]
    assert scan_forbidden_diff_patterns(diff, [r"secret_gold"]) == []


def test_scanner_reports_added_benchmark_reference():
    diff = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1,2 @@
 value = 1
+from harness_gold import calibrate
"""
    violations = scan_forbidden_diff_patterns(
        diff,
        [r"\b(?:harness_gold|deep_gold)\b"],
    )
    assert len(violations) == 1
    assert "harness_gold" in violations[0]
