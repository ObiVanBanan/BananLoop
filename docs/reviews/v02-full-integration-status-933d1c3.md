# v0.2 Full Optimization CLI and browser E2E — 2026-11-08

Implementation revision: `933d1c343ddef3e0c02f7eff5f1fd7e710064283`.

## End-to-end evidence

The integrated acceptance test now runs the approved Optimization child through the parsed `v2 optimize` CLI, with mock SIWC optimizer and integration-verifier transports. Real Core components cover the frozen role registry and TeamFactory manifest, durable queue, candidate write and commit, DEV/VALIDATION evaluation, independent verifier, champion promotion, trusted HOLDOUT, final report, and API/CLI read-model agreement.

Both integrated variants pass: deterministic test runner and production `TrustedEvaluatorRunner` under bubblewrap isolation. A separate browser-enabled run also passed both variants after updating the Chromium probe to check the generated candidate node ID prefix instead of the removed hand-authored node. The UI itself was not changed. Playwright was installed offline from the local npm cache. No external model requests were made.

## Tests

Full offline Linux suite on the exact implementation tree:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 64.01 seconds. Focused CLI/Optimization/lifecycle/integrated suite: **42 passed**. Browser-enabled integrated acceptance: **2 passed**. Skips: unavailable pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` and Windows ACL/reparse-point contract.

## CI and limits

The previous PR head's Chromium failure was caused by the browser fixture expecting a node ID that the new optimizer now generates dynamically. The probe has been corrected and passed locally; CI for this revision is pending publication.

The previously authorized Titanic benchmark remains 153/196 (78.06%), below 95%. No new live calls were made. Windows and other v0.2 Full acceptance criteria remain open; PR #135 stays Draft and unmerged.
