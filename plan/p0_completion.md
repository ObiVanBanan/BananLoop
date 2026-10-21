# P0 completion: portable plugin and truthful attempt accounting

Date: 2026-10-04  
Branch: `feat/autopilot-campaign-bootstrap-v1`  
Base: `f062cbaf1dc7a8c1d370d22d81fcd634a2efa0bf`

## Delivered

- Added a portable root plugin manifest and installation/layout documentation while retaining the Codex compatibility manifest.
- Bootstrap now repairs false requests for an already supplied goal or detected Python runtime, while preserving real missing reference, package, credential, and version prerequisites.
- No-change and implementation-blocked outcomes are non-scientific. Invalid evaluation, validation, paired-input mismatch, frozen evaluator drift, and lost candidate materialization pause as infrastructure failures.
- Research streaks and report accounting count valid measured decisions as scientific trials. Reports add mutually exclusive attempt categories and expose the stored historical counter without rewriting old campaigns.
- Added regressions for goal/runtime wording, true prerequisites, no-op/blocked outcomes, invalid validation, attempt accounting, and stale errors on historical accepted rows.

## Validation

- `tests/test_bootstrap.py tests/test_engine.py`: 44 passed.
- `tests/test_attempt_accounting.py`: 4 passed after the review fix; affected engine regressions: 2 passed.
- Full suite: 142 passed, 2 failed in Windows child-process cancellation tests. Both (`timeout` and `stop`) also failed at the unmodified baseline, which had 131 passed and the same two failures.
- `git diff --check`: passed. Both plugin manifests parse as JSON and their identity and user-facing descriptions align.
- AnimeDiff `doctor --json`: detected `C:\Python313\python.exe` 3.13.1. `goal --prepare-only` exited before bootstrap with `BananLoop error: source repository is dirty; commit/stash changes or pass --allow-dirty`. The current CLI exposes no `--allow-dirty` option. No state directory or `bootstrap-plan.json` was created; the AnimeDiff checkout was not changed.

## Review and limits

One review finding on legacy accepted rows with stale failure categories was fixed and covered. Security review found no new issue in this diff. It recorded a pre-existing P2 hardening opportunity: recheck frozen evaluator assets after the evaluator process returns.

This work does not establish live evaluator quality on AnimeDiff. The prepare-only check remains pending a clean source checkout; no full campaign was run.
