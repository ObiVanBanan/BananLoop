# v0.2 Full Linux acceptance status — 2026-11-08

## Exact revision and verification

Implementation/test revision: `12e457cfd764ca726a1c169e7e14f4e6f615775f`.

The full offline Linux suite was run on this exact checkout with:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 63.88 seconds. The skips are the unavailable pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` and the Windows ACL/reparse-point contract, which the owner deferred.

The integrated Exploration handoff and Optimization child E2E has both deterministic test-runner and production bubblewrap variants. It uses mocked SIWC optimizer/verifier transports and runs the actual parsed `v2 optimize` command through the role registry, queue, candidate write/commit, DEV and VALIDATION evaluation, independent verifier, champion promotion, trusted HOLDOUT, final report, and API/CLI read-model agreement. No external model calls were made in this offline test. Browser-enabled acceptance also passed both variants locally on the preceding code revision; that browser probe adjustment is included in this revision.

## CI

For this exact PR head, GitHub reports Linux pytest on Python 3.11, 3.12 and 3.13, Chromium, and mutate-policy as successful. Windows Python 3.12 was still in progress when checked. Windows remains deferred for this acceptance stage.

## Live benchmark and limits

The single owner-authorized Titanic HOLDOUT run scored **153/196 = 78.06%**, below the 95% aspiration. No retry or additional paid call was made; provider cost remains `UNKNOWN` where unavailable. This does not demonstrate a successful 95% benchmark, nor a live multi-iteration Exploration campaign.

The PR remains Draft and unmerged. The goal remains active: this report demonstrates the Linux offline integrated path, not complete v0.2 Full acceptance. Remaining items include broader live multi-iteration evidence, Windows acceptance (deferred), and the unavailable pinned selector source revision.
