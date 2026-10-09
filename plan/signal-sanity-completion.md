# M3 completion: evaluator signal sanity

Completed 2026-10-05.

## Delivered

- Bootstrap can declare evidence kind, primary metric, and positive/negative evaluator case IDs, or return `UNAVAILABLE` with an explicit reason and no fabricated labels.
- Core checks the claimed groups' directed primary-metric separation on every bounded A/A repeat. It rejects a wrong metric, missing cases, inverted/constant separation, and malformed or overlapping IDs.
- The canonical readiness receipt digest and status are stored with `CampaignSpec`; optional `None` values are omitted from the approved hash to preserve historical hash semantics.
- Operator JSON/Markdown reports expose the signal status, receipt digest, and evidence level.
- A passing separation check is `DIAGNOSTIC_ONLY` because the bootstrap agent supplies labels and the evaluator supplies values. It does not upgrade campaign assessment. `UNAVAILABLE` keeps a target-reaching holdout at `DEV_ONLY`.

## Verification

- Core regression set (`test_readiness.py`, `test_bootstrap.py`, `test_completion.py`): 50 passed, 1 skipped.
- Full suite: 173 tests collected, 170 passed, 2 failed, 1 skipped. The failures are the pre-existing Windows child-process cancellation cases in `tests/test_process.py` (`timeout` and `stop`); both also failed in the pre-M3 baseline. Symlink coverage was skipped because this host denied symlink creation (`WinError 1314`).
- `python -m compileall -q src/bananloop` passed.
- `git diff --check` passed; only line-ending conversion warnings were reported.

## Review and security

One review pass and a focused security pass found a trust-boundary issue: agent-declared labels could otherwise be presented as verified signal evidence. The implementation now reports only diagnostic separation, and a regression proves that adding this receipt does not change the campaign's final assessment. No second review pass was run. The security pass found no path traversal or workspace escape issue in the M3 additions.

The receipt proves only that declared groups separate numerically under repeated smoke evaluation. It does not prove the labels are true or that the scorer measures the intended quality. Independent signal validation remains deferred until fixture and label provenance can be verified by core-owned evidence.

## Rollback

Revert only the M3 signal-sanity source, prompt, tests, documentation, and this record. Preserve M1/M2 changes, historical campaign data, and user-owned dirty files. No dependency or database migration was introduced.

No commit or push was performed.
