# Stochastic readiness calibration completion

## Outcome

Implemented M2 bounded A/A noise calibration on `codex/evaluator-binding-v1`. Bootstrap readiness performs three independent smoke measurements by default, with a hard allowed range of two to five repeats. Each repeat uses a fresh clone at the frozen snapshot, and each run receives a separate artifact directory. The core checks stable case/input identities, requires one stable `protocol_sha256`, verifies `HEAD` before and after evaluator execution, and records repeat count, median, observed primary range, configured `min_improvement`, and gate result. If paired comparison uses another metric, its observed noise is compared to its own `worst_case_tolerance`.

No `CampaignSpec` field or approved-hash semantics changed; the successful-bootstrap regression verifies the stored spec hash remains equal. Noisy or unstable readiness results are preserved in the bootstrap receipt and passed to bounded repair; exhausted repairs do not create a campaign DB.

## Verification

- `uv run pytest tests/test_readiness.py tests/test_bootstrap.py -q -rA -p no:cacheprovider --basetemp .tmp/m2-exact-final` — **27 passed, 1 skipped**.
- The skipped test creates a read-only external sentinel symlink; Windows denied link creation with `WinError 1314`.
- Full suite on final implementation code reported only the two known Windows child-process cancellation failures: `tests/test_process.py::test_cancellation_stops_child_work[timeout]` and `[stop]`. These same baseline failures were observed before M2. (The full run preceded a test-only assertion addition; the affected bootstrap test and final targeted suite passed afterward.)
- `compileall` and `git diff --check` passed per implementation-agent run; `git diff --check` was also checked after final local edits.

## Review and security

- One review pass found two issues, both fixed: A/A repeats shared ignored workspace state; paired metric noise used primary metric units. No second review pass was run.
- Security gate found no blocking or major issue. It found a minor chmod-through-link concern in Windows cleanup; the cleanup now checks symlink/reparse points and fails safely, but the sentinel regression could not execute on this host due to link-creation permissions.
- Advisory: evaluator stdout/stderr is persisted and can be sent to the bounded bootstrap repair agent. Treat evaluator diagnostics as untrusted and avoid emitting secrets. This existing diagnostic flow was not redesigned in M2.

## Limits and rollback

The calibration's `empirical_noise_bound` is the observed max-min range across three measurements; it is not a confidence interval and does not establish that the scorer measures the intended product quality. Known-good/bad signal-sanity or controlled-degradation checks remain a later roadmap item.

Rollback only M2 changes in `readiness.py`, bootstrap prompt text, readiness/bootstrap tests, and the M2 section of `plan/implementation_plan.md`; preserve existing P0/P1 work and pre-existing user changes. No commit/push was performed.
