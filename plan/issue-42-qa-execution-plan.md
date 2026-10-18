# Issue #42 QA execution plan

This plan separates protocol and safety checks from live campaign evidence.

1. **Codex portfolio wire contract** — adapter/schema layer; automated pytest with a stubbed process runner. Expect the exact schema passed via `--output-schema` to accept both v2 `candidates` and legacy single-hypothesis output. Capture the schema and normalized response.
2. **Minimum-cost and evidence-ref gates** — selector layer; automated pytest. Expect zero-cost estimates to be rejected and configured validation/holdout plus work-unit reserves to be included. Expect content-addressed capsule refs to remain stable when the sliding memory window reorders.
3. **Portfolio durability** — orchestration/storage layer; interruption/resume E2E. Expect the portfolio event and completed proposal action to survive a stop immediately after commit, and resume to reuse the stored decision. Capture persisted event count, stage, and call counts. This scenario may be blocked if Windows sandbox prevents fixture Git clones.
4. **Two real low-cost projects** — full BananLoop/agent/evaluator orchestration; run two independent disposable toy repositories with a local deterministic evaluator and bounded budgets. Use the actual Codex CLI portfolio adapter if available. Expect the selected experiment to be implemented, measured by BananLoop, and reported from each project; preserve campaign reports and provenance. Do not count canned or mocked outputs as live evidence. A real two-project proposal-only CLI smoke has passed (2 candidates for the text formatter, 1 for the CSV summarizer); this checks schema acceptance and is not a completed campaign or empirical comparison.

No production project is a target for these dogfood campaigns. The live scenarios use temporary repositories and tiny deterministic evaluators; each campaign is separately identified as real, mocked, or blocked.
