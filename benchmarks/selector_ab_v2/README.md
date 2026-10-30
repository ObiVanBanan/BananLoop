# Selector A/B v2

This is a new execution preregistration after v1 failed before candidate evaluation. v1 artifacts remain unchanged. v2 uses five fresh paired labels and the same manually frozen three-candidate portfolio.

## Result

The campaign scored all ten trial slots in the frozen order. See `manifest.json`, `trials.jsonl`, `report.json`, and compact `run_summary.json`. The recorded Docker build recipe and focused tests are in `docker_context/`. Each trial directory contains the SQLite campaign state, campaign report, execution record, and Docker console output where captured. The Docker image build and a separate offline, network-disabled, read-only-container run passed the process/workspace suite: 15 passed. The only warning was pytest being unable to write its cache under the read-only root filesystem.

| Label | LLM DEV | Deterministic DEV | LLM − deterministic |
|---:|---:|---:|---:|
| 503 | 0.4375 | 1.0000 | -0.5625 |
| 607 | 1.0000 | 1.0000 | 0.0000 |
| 709 | 1.0000 | 1.0000 | 0.0000 |
| 811 | 1.0000 | 1.0000 | 0.0000 |
| 919 | 1.0000 | 1.0000 | 0.0000 |

The descriptive mean delta is -0.1125. The fixture has 16 DEV cases and no HOLDOUT. `winner_claim` is null; these results do not establish a general selector winner or cross-task performance.

## Resource accounting

- Codex subscription quota only; no separately billed API use was authorized or observed ($0).
- 15 Codex agent calls; 20 evaluator calls.
- Summed trial time: 312.67 seconds.
- Token usage was not exposed by the campaign adapter and remains unknown.
- v2 maximum: 15 Codex calls, 21 evaluator calls, 1 hour; no retries.

## Reproduce the offline report

```powershell
python -m benchmarks.selector_ab_v2.report `
  --root . `
  --manifest benchmarks/selector_ab_v2/manifest.json `
  --portfolio benchmarks/selector_ab_v2/frozen_portfolio.json `
  --trials benchmarks/selector_ab_v2/trials.jsonl `
  --output benchmarks/selector_ab_v2/report.json
python -m pytest tests/test_selector_report.py tests/test_selector_preregistration.py -q
```

This command validates saved data only. It does not start models or evaluators.
