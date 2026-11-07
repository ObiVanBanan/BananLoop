# BananLoop v0.2 live Optimization checkpoint — `5104f22`

This checkpoint records a successful single-inference SIWC Optimization run. It does not claim every v0.2 Full acceptance item is complete.

## Offline Linux verification

Live Optimization SHA: `5104f22d00eb0adcf5105475f45b8cc88ee3c248`.
Latest offline test SHA: `b3cebf2eb776aec943ae41e2f2b3c6adf5ff9bf4`.

- Full Linux suite: **950 collected, 948 passed, 0 failed, 0 errors, 2 skipped**.
- Skips: pinned selector source commit unavailable; Windows ACL/reparse contract is not runnable on Linux.
- The suite includes three-iteration Research Memory follow-up, malformed-response accounting, crash/restart no-redrive, cross-layer Exploration/Optimization/Evaluator/Verification/HOLDOUT, and Titanic mini-benchmark tests.
- Titanic optimizer calls are checked immediately before dispatch: the model sees only `data/train.csv` and `src/model.json`, can write only `src/model.json`, and receives no VALIDATION/HOLDOUT content.
- Reproduce: `PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -q -ra --tb=short`.

## Live SIWC Optimization

The user authorized one additional call. On runtime SHA `5104f22d00eb0adcf5105475f45b8cc88ee3c248`, the Titanic v2 Optimization E2E completed in **12.8 seconds** with **one dispatched GPT-6 Luna inference**, no retries, and pytest result `1 passed`.

- Campaign reached `ALL_NODES_COMPLETE`.
- Optimizer candidate was created and passed trusted DEV evaluation.
- Independent verification passed and the candidate became champion.
- VALIDATION completed and terminal trusted HOLDOUT was verified.
- HOLDOUT accuracy: **0.780612 (153/196)**, below the 0.95 stretch target.
- Observed usage: **13,722 input + 337 output = 14,059 tokens**; request ID `resp_03919cb951eec047016aef443035ec87d28562ebaa13404a1b`.
- Monetary cost: `UNKNOWN`.
- Parent evaluator-protocol approval remains fixture-seeded; the independent verifier is deterministic and private tiers stayed in Core.

Exact live result, raw provider receipt/model response, and public optimization campaign report are preserved in `benchmarks/titanic_survival/results/` as `live-siwc-optimization-20261107.json`, `live-siwc-optimization-20261107-receipt.json`, and `live-siwc-optimization-20261107-campaign-report.json`.

The two earlier one-call attempts failed before a successful campaign: first the model changed the selected hypothesis ID; second it invented unsupported feature `male_child`. Their records remain `siwc-optimizer-attempt-20261107.json` and `siwc-optimizer-attempt-20261107-02.json`. Across all three attempts, observed usage is **42,169 tokens** and monetary cost is `UNKNOWN`.

## CI

For PR head `5104f22`, GitHub Actions reported:

- [Linux and Windows test workflow](https://github.com/ObiVanBanan/BananLoop/actions/runs/42386544693): Linux Python 3.11, 3.12, and 3.13 passed; Windows Python 3.12 was still running when this checkpoint was prepared.
- [Dashboard browser workflow](https://github.com/ObiVanBanan/BananLoop/actions/runs/42386544678): passed.
- [Critical rule mutation workflow](https://github.com/ObiVanBanan/BananLoop/actions/runs/42386544690): passed.

## Remaining limits

- The live model-run achieved 78.06% HOLDOUT accuracy, not the 95% stretch target.
- Parent protocol approval in the Titanic live run is fixture-seeded; a complete live Exploration protocol-discovery-to-Optimization campaign is not established by this run.
- RPDI-14 monetary subscription-cost accounting is unavailable; cost remains `UNKNOWN`.
- Windows was not a requested implementation target; its current CI job is recorded separately from Linux validation.

PR #135 remains Draft and unmerged.
