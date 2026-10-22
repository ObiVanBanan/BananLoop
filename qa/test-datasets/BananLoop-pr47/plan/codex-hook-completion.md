# M6 completion: Codex lifecycle bridge

## Behavior

The plugin packages a Windows-aware Codex Stop hook that invokes the hidden `bananloop codex-hook` dispatcher. It binds an exact campaign marker to a hashed Codex session ID, reads durable campaign status, and requests one bounded continuation only for a known RUNNING campaign with no stop request and no live supervisor lock. The dispatcher never starts a campaign, agent, evaluator, or subprocess. Errors, unknown state, repeated Stop events, conflicting bindings, lock contention, and late decisions fail open. Old registries gain the session-binding table lazily.

## Validation

- Focused command: `uv run pytest -p no:cacheprovider --basetemp .tmp\pytest-m6-scope tests/test_codex_hook.py tests/test_registry.py tests/test_cli.py -q -ra`.
- Result: 46 passed, 3 skipped (Windows symlink creation unavailable, `WinError 1314`).
- CLI protocol test exercises `bananloop codex-hook` dispatch and Stop JSON output. Tests cover session binding and old-registry upgrade, repeat callback, terminal/stop-requested state, lock contention, conflicting marker, status and registry symlink guard, and plugin manifest parity.
- Windows Codex CLI installed locally: `codex-cli 0.158.0`. Plugin trust approval and a real Codex Desktop hook invocation remain unverified; `/hooks` must trust the hook before it runs. Hook remains optional and direct `bananloop resume` works without it.
- Review found one major old-registry binding defect; repaired and covered with an integration test. Security pass found two P2 symlink risks plus a documentation mismatch; repaired and targeted tests rerun. Review/security outcomes are in `review_report-codex-hook.md` and `security_review-codex-hook.md`.
- Full suite: 218 collected; 212 passed, 4 skipped (Windows symlink creation unavailable), 2 failed in `tests/test_process.py::test_cancellation_stops_child_work[timeout|stop]`. Both failures are the same existing Windows child-process cancellation limitation recorded before M6. No M6 test failed.
- `uv run python -m compileall -q src\bananloop` and `git diff --check` passed.

## Rollback

Revert only the M6 dispatcher, registry session-binding table/methods, CLI hook command, plugin hook wiring and M6 docs/tests. The added registry table is additive and disposable; no campaign DB, campaign receipt, CampaignSpec, or evaluator state is rewritten. After rollback, ordinary CLI/skill usage remains available and no session binding is consulted. Verify with the focused registry/hook tests and the full suite.
