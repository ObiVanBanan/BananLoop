# Autonomous Benchmark v0.1 — preparation

See [implementation handoff](../../docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-PLAN.md).

This branch intentionally contains a frozen **plan only**: the new two-arm runner does not yet exist; no provider or Harbor calls have been made. Keep all RPDI-14 requirements unchanged. The primary ARE track needs 12 eligible task adapters, a verified Codex Direct iterative adapter, Full-campaign budget semantics, a global 250-call ledger, best-verified-checkpoint persistence and final offline packaging. Do not claim the existing ARE `series_runner.py` can execute this new design.

Prepare and test offline first; require explicit approval of the exact immutable run manifest before spending any subscription calls.
