# v0.2 Full integration follow-up — 2026-11-07

Implementation commit: `ae3feb1be339ece91bc74976670b031b2c03e711`.

## Changes

- Titanic's parent protocol discovery now runs through the v2 `V2ResearchRuntime`, a durable Exploration worker result, the actual `EvaluatorProtocolAuthority.record_completed_proposal` path, and `EvaluatorVerificationAdapter` with a pinned `independent_verifier` role.
- The test checks that researcher context contains only `data/train.csv`, and that verifier context omits the HOLDOUT case label and digest.
- Frozen benchmark assets are attached to the Exploration completion as Core-owned trusted SHA-256 bindings. The worker cannot declare those bindings; completion verifies the configured allowlist and the published content-addressed files. The bindings are excluded from public evidence refs and Research Ledger findings.
- The child Optimization campaign installs the real handoff receipt produced by the parent workflow; the test no longer fabricates a receipt or protocol proposal.

## Verification

Full offline Linux suite on this exact implementation commit:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **949 passed, 2 skipped, 7 subtests passed** in 68.73 seconds. The skips are the unavailable pinned selector source commit and the Windows ACL/reparse-point contract, which cannot run on Linux. The run included the production bubblewrap evaluator path.

`git diff --check` passed before commit. GitHub CI for this revision awaits publication.

## Live benchmark evidence retained

The separately authorized one-call Titanic run completed with terminal trusted HOLDOUT **153/196 = 0.7806122448979592**, below the 95% target. Provider-reported usage was 14,059 tokens; monetary cost remains `UNKNOWN`. No additional live call was made for this revision.

## Remaining limitations

- The live benchmark did not meet the 95% target and was not retried.
- The one-call live result is not evidence of an autonomous live Exploration proposal; this revision validates the production runtime/queue/verifier/handoff path with a deterministic test model.
- Windows remains deferred per the Linux-first scope. The previously observed Windows CI failure on `00a56ae` concerned candidate snapshot commit on Windows and Titanic source digest changes under Windows checkout behavior; this revision still needs separate Windows work.
- ChatGPT transport cancellation and exact monetary cost are not guaranteed/reported; missing expense values remain `UNKNOWN`.
- PR #135 remains Draft and unmerged. The integration work does not establish that every v0.2 Full acceptance criterion is complete.
