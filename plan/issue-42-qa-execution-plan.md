# Issue #42 QA execution plan

This plan separates protocol and safety checks from live campaign evidence.

1. **Codex portfolio wire contract** — adapter/schema layer; automated pytest with a stubbed process runner. Expect the exact schema passed via `--output-schema` to accept both v2 `candidates` and legacy single-hypothesis output. Capture the schema and normalized response.
2. **Minimum-cost and evidence-ref gates** — selector layer; automated pytest. Expect zero-cost estimates to be rejected and configured validation/holdout plus work-unit reserves to be included. Expect content-addressed capsule refs to remain stable when the sliding memory window reorders.
3. **Portfolio durability** — orchestration/storage layer; interruption/resume E2E. Expect the portfolio event and completed proposal action to survive a stop immediately after commit, and resume to reuse the stored decision. Capture persisted event count, stage, and call counts. This scenario may be blocked if Windows sandbox prevents fixture Git clones.
4. **Two real low-cost projects** — full BananLoop/agent/evaluator orchestration; run two independent disposable toy repositories with a local deterministic evaluator and bounded budgets. Use the actual Codex CLI portfolio adapter if available. Expect the selected experiment to be implemented, measured by BananLoop, and reported from each project; preserve campaign reports and provenance. Do not count canned or mocked outputs as live evidence. A real two-project proposal-only CLI smoke has passed (2 candidates for the text formatter, 1 for the CSV summarizer); this checks schema acceptance and is not a completed campaign or empirical comparison.

No production project is a target for these dogfood campaigns. The live scenarios use temporary repositories and tiny deterministic evaluators; each campaign is separately identified as real, mocked, or blocked.

## Execution results — 2026-10-18

- **Unit/schema**: 20 tests passed across `tests/test_codex_adapter.py` and `tests/test_research.py`; compileall and `git diff --check` passed.
- **CI**: GitHub Actions run #379 passed Linux Python 3.11, 3.12, 3.13 and Windows Python 3.12 on the current PR head.
- **Live Codex schema**: two real proposal-only adapter calls succeeded after replacing the unsupported top-level union with the strict object schema. The formatter context returned 2 candidates and the CSV context returned 1.
- **Full local dogfood**: `examples/issue42/run_dogfood.py --work-dir .tmp-issue42-qa-stub-dogfood` reached `GOAL_MET`: 1 accepted experiment, 2 candidates considered, 2 agent calls and 2 evaluator calls. Agent was a local deterministic stub; this validates the engine lifecycle, not real-model implementation quality.
- **Real Codex campaigns**: two corrected bounded campaigns are retained under `.tmp-issue42-live-campaigns-v2`. Both produced and selected real Codex portfolios. Both implementation calls were blocked before file access by the Codex CLI workspace helper (`helper_unknown_error: setup refresh had errors`); no candidate code changed and no scientific candidate evaluation ran. An isolated write-mode adapter call against the same disposable workspace reproduced that environment failure. The CSV baseline measured 3310.6 KiB; its QA fixture check also needs adjustment for the header row before any rerun.
- **Open acceptance item**: there is still no empirical two-project improvement comparison from completed real-agent experiments. The draft PR stays open for that follow-up; the two prior deterministic examples are not counted as such evidence.
