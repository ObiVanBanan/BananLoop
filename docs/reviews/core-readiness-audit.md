# Core readiness audit — v0.1 Research Preview

**Audit base:** `origin/main` / `b4abbf1488296ff43a4f4c3c639ed24e54a3764e` (`feat: implement operator roadmap and record evidence`).
**Scope:** current source and tracked documentation at that SHA, local benchmark artifacts explicitly identified below, GitHub state read on 2026-10-30. This audit does not treat roadmap checkboxes or PR descriptions as proof of implementation.

## Confirmed capabilities on the audit base

- CLI is implemented in `src/bananloop/cli.py`; campaigns use SQLite state and `src/bananloop/engine.py` owns the research lifecycle. `src/bananloop/store.py` persists actions, reservations, receipts, events and champion state.
- Action replay uses request hashes and durable action statuses in `src/bananloop/engine.py`; unknown `RUNNING` and failed actions require explicit retry flags. These mechanics are not token metering.
- SQLite migrations in `src/bananloop/store.py` add columns/tables and migrate legacy evaluation rows. Process termination in `src/bananloop/process.py` handles process groups, Windows break/kill fallbacks, and Unix process-group termination. Existing unit tests exercise local child processes and recovery; this is not evidence of a supported Windows/Linux acceptance matrix.
- `src/bananloop/operator_control.py` provides typed pause/resume/end commands and durable request receipts. `src/bananloop/operator_snapshot.py` projects committed state and DEV-only evaluation rows; `read_events_after()` supports cursor paging and filters event payloads.
- Champion promotion is guarded by validation/policy checks and persisted atomically by the store. Terminal cleanup checks out the champion snapshot in `src/bananloop/engine.py`.
- Composite hypotheses and architecture-pivot records exist on this branch. They are deferred product scope under the current plan; existence in code does not make them v0.1 release requirements.

## Incomplete or unverified implementations

- **Token-aware model budget:** not integrated in this `main`. `CampaignSpec` exposes call/evaluation limits, while draft PR #61 / issue #60 describe a separate opt-in Codex token metering implementation. The PR itself documents post-call overshoot, unknown usage, and missing real Windows receipt validation. Do not describe current main as enforcing a strict token cap.
- **Interactive UI scope:** README and `docs/ROADMAP.md` still describe the Pi terminal and `/chat` as available capabilities. The user-approved product boundary in the stabilization plan is CLI only; a future desktop application is deferred. Existing snapshot/control/events remain useful headless interfaces and should be retained.
- **Benchmark status document:** `docs/BENCHMARK_STATUS.md` is absent on the audit base.
- **Lifecycle acceptance:** tests cover local subprocesses, pause/stop and recovery paths, but no evidence in the inspected tree establishes a reproducible full campaign E2E matrix on both Windows and Linux with final report and crash/restart.
- **Secret/history audit:** no dedicated scanner was available in the execution environment. A limited local pattern scan of reachable Git objects across refs known to this clone checked 3,999 object IDs for private-key headers, AWS access-key IDs, GitHub/Slack token formats and quoted credential assignments; it reported zero matches. It did not inspect untracked artifacts, refs absent from this clone, every provider-specific format, or GitHub secret-scanning alerts. This is not a complete clearance; no secret values belong in this report.

## Reproduced consistency defect (fixed in this branch)

`CampaignStore.update_campaign()` in the audit base updated campaign rows and committed without inserting an event. `set_research_state()` delegates to that method. `read_snapshot()` derived `revision` from `MAX(events.seq)`. A consumer could therefore keep the same event cursor while `status`, stop reason, or research state changed. The current audit branch fixes this by recording a payload-free `CAMPAIGN_STATE_UPDATED` event atomically with the row update; a regression test checks the snapshot revision and event cursor. No SQLite schema migration is introduced.

## Bugs and release risks

- Strict per-campaign token admission/accounting is absent from audit-base `main`; provider cost/token use may remain unknown. This blocks claims of a strict token cap and a fair budget-matched H2H.
- Codex CLI process cancellation/recovery must still be accepted with real processes on supported Windows and Linux configurations. Existing local process tests alone do not establish this release criterion.
- The repository's agent/evaluator isolation is not an OS-grade hostile-code sandbox. Generated benchmark code must not be executed on the host or via an unsandboxed fallback.
- H2H artifact in the local audit evidence is absent. Issue #60 documents an exploratory shared pair: Codex used 706,403 tokens and reached DEV quality 0.70718697; BananLoop used 751,298 against a nominal 750,000, so the arm was ineligible after a 1,298-token overshoot and its next candidate was unmeasured. Equal visible champion scores do not demonstrate a harness advantage.
- `docs/benchmarks/operator-roadmap.md` reports an AutoResearchExam BananLoop-only intermediate DEV run, an empty planned paired suite, and a separate selector mini-fixture. These are distinct experiments and cannot satisfy the Codex Direct vs BananLoop gate. The five-pair selector DEV fixture reported four ties and one lower LLM-selector score; it has no HOLDOUT and no winner claim.
- PR #63 is a public-only LiveCodeBench pilot with no official hidden score, no billed-token accounting, and explicit warnings that generated code is unsafe to run on the host. It is not the required H2H.
- Hosted CI is not established as green for the final SHA. The release checklist itself leaves final-SHA suite, clean Windows/Linux install, evaluator/HOLDOUT review, CI substitute, privacy/history audit, and publish gates open.

## Duplicate / overlapping pull requests and issues

- PR #61 implements the P0 token-budget scope for issue #60 but is open and not integrated into audit-base `main`; it is not a strict external spending stop. Do not duplicate its approach without checking its branch and tests.
- PR #63 is a separate LiveCodeBench public-only experiment and must remain distinct from AutoResearchExam H2H evidence.
- PR #70 is an opt-in semantic selector proposal with its own hard gates/fallback. Its PR description says local tests are not yet verified; it is not H2H evidence.
- PR #72 is an open Draft documentation PR. On 2026-10-30 its branch was updated with current `main` and corrected scope/visibility/benchmark docs; GitHub reports head `9def13a5fb3dd1dee3ca07c3a801e65b309802f7`, `mergeable=true`. Final-SHA validation, platform installs, complete history audit and repeat H2H remain unfinished. GitHub metadata reports `visibility=public`, which conflicts with the intended closed preview; visibility was not changed.
- Issues #28 and #49 are open product/specification context; #60 is open and covers token admission; #64 is open and specifies the required paired AutoResearchExam study and isolation gate. These issues describe desired work, not proof it is present in `main`.

## Required for v0.1 vs deferred to v0.2+

**Required before a v0.1 Research Preview recommendation:** CLI-only truthful docs; correct state/event revision semantics; durable local campaign lifecycle and champion integrity; token accounting described honestly (unknown where unavailable) with no strict-cap claim; a deterministic CPU-only core research E2E with explicit pass/fail/skip outcomes; final-SHA suite and clean install on supported Windows/Linux; safe evaluator boundary; complete historical secret/privacy audit; and a reproducible matched-resource Codex Direct vs BananLoop comparison. If cost metering or sandbox is unavailable, the H2H gate is `BLOCKED`/`INELIGIBLE`; do not run paid or unsandboxed substitutes.

**Deferred to v0.2+:** Pi terminal development/expansion, desktop application, chat-first workflow, interactive charts, architecture-pivot campaign continuation, remote/multi-user dashboard, new model platforms, and additional benchmark classes without licensing and safe oracle boundaries. Preserve existing headless typed control, read-only snapshots, and event mechanisms.

## Visibility and secret review

GitHub repository metadata reported `visibility=public` on 2026-10-30. This contradicts the private-preview statement in Draft PR #72. The audit branch did not change repository visibility. No dedicated secret scanner was installed. A local pattern scan over reachable refs known to this clone checked 3,999 object IDs and reported zero matches for selected private-key/token/credential formats; it did not cover untracked files, remote refs absent from the clone, every provider format, or GitHub secret alerts. This is not a complete clearance.

## PR and release recommendation

The current audit branch contains a narrow event/snapshot consistency fix and regression coverage. Additional changes should be split by invariant and checked against the actual base before separate PRs. No merge, tag, release, or visibility change is part of this work. **Current recommendation: NOT READY; benchmark gate BLOCKED pending confirmed resources and a verified isolated evaluator.**

## Verification update — 2026-10-31

- The current PR #61 head is `3e9f8b7f99924432ae767c1e6d1b07bc70da9c71`, still Draft. It has been refreshed against this audit-base `main`, adds SELECT-role token attribution/config validation, strict final-receipt handling, and crash-retry coverage. Its Windows / Python 3.13 full suite collected 445 tests and exited 0; hosted CI and Linux verification remain unverified. The token cap is post-call accounting, not a provider-enforced spend limit.
- The event/snapshot consistency patch and CPU-only scenarios A–E are recorded in `docs/reviews/core-research-e2e-report.md`. The local full suite collected 432 tests and exited 0; seven selected E2E and DEV-only context cases passed. The preregistered selector source snapshot is absent, so its hash verification is explicitly skipped rather than comparing unrelated current files.
- These checks do not clear the release gate. No matched-resource H2H, Linux acceptance, complete secret-scanner run, or safe hostile-code evaluator boundary has been established.

## Source references

- `src/bananloop/engine.py`, `src/bananloop/store.py`, `src/bananloop/process.py`, `src/bananloop/operator_control.py`, `src/bananloop/operator_snapshot.py` at `b4abbf1488296ff43a4f4c3c639ed24e54a3764e`.
- `docs/ROADMAP.md`, `docs/RELEASE_CHECKLIST.md`, `docs/benchmarks/operator-roadmap.md` at the same SHA.
- GitHub PRs #61, #63, #70, #72 and issues #28, #49, #60, #64 as observed on 2026-10-30.
