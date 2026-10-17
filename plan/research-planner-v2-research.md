# Research Planner v2: repository facts

Date: 2026-10-16

## Checkout and GitHub state

- The checkout is `codex/evaluator-binding-v1` at `e6cc6c37075bd6070264b6e7d021313761198a73`; its tracked working tree was clean at audit time.
- The configured `origin` is `https://github.com/ObiVanBanan/BananLoop.git`.
- The checkout contains local untracked worktrees, pytest/temp directories, a RAG experiment archive, and `examples/manual_codex_campaign/`; those paths are pre-existing and outside this change.
- Issue #42 is open. Its requirements call for a bounded portfolio, deterministic evidence-aware selection, durable lineage, pre-stop plateau reframe, reports, deterministic CPU tests, cross-domain fixtures, A/B evidence and a draft PR.
- Open PR search returned #41 (Issue #40 diagnosis, stacked on #36), #36, #35, #34, #32, #31, #30, #29 and #27. PR #35 contains earlier experimentation-planning work; PRs #29/#30 contain the current v1 research/capsule design.
- The checkout is not an ancestor of the local remote-tracking refs `origin/feat/autopilot-campaign-bootstrap-v1` and `origin/codex/issue-40-evidence-driven-diagnosis`; their merge base is `331bd9b075952572102e97d5b7751884e7752fe2`. HEAD has 42 commits outside the former and 42 outside the latter, so these are diverged lines, not an integrated linear head.
- PR #41 describes typed, hashed DEV retrieval stage evidence, effective remaining budgets, diagnostic gating, and explicit unknown evidence. This checkout has evidence summaries/capsules in `research.py` and evaluator binding, but is not based on PR #41's branch.

## Existing contracts and flow

- `src/bananloop/contracts.py` defines `Hypothesis` with mechanism, relation (`new/refine/retry/supersede`), prior experiment refs, falsification condition and evidence-grounding fields. `ResearchState` contains planning mode, DEV streak, mechanism summaries, lessons, failure capsules/signals, experiment evidence and remaining budget.
- `src/bananloop/research.py` builds adaptive state from persisted experiments and explicitly classifies scientific trial buckets. `planning_mode` maps the streak to exploit/explore/reframe. `redundancy_reason` rejects a repeated mechanism unless it is a referenced refinement/retry/supersede.
- `src/bananloop/store.py` persists campaigns, experiments, actions, evaluations, decisions, reservations and events in SQLite. Additive schema migrations currently support old databases; action records persist request/response JSON and stable IDs.
- `src/bananloop/engine.py` constructs proposer context in `_task_context`, executes one proposer call in `_stage_propose`, validates a single `Hypothesis`, checks grounding and redundancy before implementation, and records experiment/campaign events. `_post_iteration_stop` stops at the configured non-improve streak.
- Existing engine execution reserves agent/evaluator work, persists actions for crash recovery, sanitizes agent workspaces and reconstructs research state from DEV records. `agent_evaluation_view` filters metrics/diagnostics through explicit allowlists; validation and holdout decisions are separate from `dev_decision_json`.
- `src/bananloop/report.py` includes serialized `research_state` in JSON and renders mode, DEV streak, failure classes, remaining budgets, lessons and failure capsules as Markdown.
- Codex/Pi/generic command adapters use shared role/task JSON contracts. Existing `Hypothesis` payloads are accepted as single-proposal responses.
- `tests/test_research.py` covers DEV isolation, planning modes, mechanism redundancy and evidence summarization. `tests/test_engine.py` covers action replay, budgets, research visibility, grounding, validation/holdout isolation, integrity and lifecycle.

## Known integration and verification gaps

- No portfolio response model, candidate-level admissibility/selection record or durable portfolio lineage contract exists in the audited checkout.
- The proposer context asks for one hypothesis, not multiple alternatives. Existing action identity can replay the original response, but it does not expose a selected portfolio decision.
- Reports do not show alternatives considered, criterion-level reasons, or a concise observation/support/unknown/falsification learning record.
- Reframe is represented in planning state before plateau, but there is no selected alternate portfolio decision specifically coupled to the deterministic plateau stop gate.
- Existing CPU tests are project-local and deterministic. Issue #42's requested old-vs-new A/B comparison and two-domain evidence are not present as issue-specific artifacts.

## Sources

- GitHub Issue #42 and its comment list, fetched 2026-10-16.
- GitHub open PR search, fetched 2026-10-16.
- The file/function references above in the audited checkout.
