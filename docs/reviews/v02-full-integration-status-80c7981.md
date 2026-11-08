# v0.2 Full integration follow-up — 2026-11-07

Implementation commit: `80c7981289d9aafd9be9797e61959ece57aaf479`.

## Changes

- Researcher replies can carry bounded, canonical base64 binary artifacts and a typed evaluator protocol draft. Artifact bytes still pass through `ToolGateway`; malformed or oversized payloads fail after dispatch and remain accounted as unknown work.
- When a pinned role registry defines `independent_verifier`, Exploration includes that role in the durable team manifest so the protocol handoff can use the pinned verifier.
- The composed Exploration → protocol proposal → independent verification → Optimization child acceptance now records the proposal from the actual completed Exploration job and obtains its approval through `EvaluatorVerificationAdapter` and the durable worker queue. The verifier context is checked for hidden case bytes and labels.
- Protocol source review now resolves the same `{evaluator}/...` and `/workspace/evaluator/...` entrypoint forms accepted by the trusted evaluator, while rejecting absolute paths and traversal.
- Added a malformed binary artifact regression proving that invalid base64 never publishes an artifact or completes the node.

## Verification

Full offline Linux suite on the exact implementation tree:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **949 passed, 2 skipped, 7 subtests passed** in 69.80 seconds. The skips are the unavailable pinned selector source commit and the Windows ACL/reparse-point contract, which is not runnable on Linux.

The composed API/CLI acceptance passed in both `test-runner` and `real-bwrap` modes. Research Runtime, Evaluator Verification, and Exploration Adapter tests also passed. No live model call was made for this change.

GitHub CI for this new commit is pending publication. The preceding PR revision `5036a1b5498ca36ae6c445de11303b774a94e88a` passed Linux Python 3.11–3.13, Chromium dashboard, and mutation-policy jobs; Windows was still running at that observation.

## Live benchmark evidence retained

The separately authorized one-call Titanic run completed with terminal trusted HOLDOUT **153/196 = 0.7806122448979592**, below the aspirational 95% target. Provider-reported usage was 14,059 tokens; monetary cost remains `UNKNOWN`. The run and provider receipt remain under `benchmarks/titanic_survival/results/`.

## Remaining limitations

- The Titanic live result does not meet 95%; the run was not retried and no further paid calls are authorized.
- Windows CI is not part of this Linux-first acceptance result.
- Cancellation and exact monetary cost are not guaranteed or reported by the ChatGPT transport; unavailable usage fields remain `UNKNOWN`.
- PR #135 remains Draft and unmerged. This report does not claim v0.2 Full complete beyond the tested integration slice.
