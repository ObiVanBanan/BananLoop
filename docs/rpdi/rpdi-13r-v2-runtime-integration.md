# RPDI-13R — v0.2 Research Runtime Integration

**Status: IMPLEMENTATION IN PROGRESS.** This RPDI is inserted after RPDI-13
and before RPDI-14 as an integration prerequisite. It does not replace the
independent benchmark or release process.

## Problem and outcome

The legacy `bananloop run` enters v1 `CampaignStore`/`Engine`. A v2
Exploration worker exists, but `EvidenceOnlyAttemptAdapter.run_one`
requires a previously pinned `PinnedRoleExecutorV1`. `CodexAdapter`
does not implement the researcher role, and direct Codex execution must not
bypass `ToolGateway`. Therefore green component tests do not demonstrate
a live, multi-iteration v0.2 Full research run.

Deliver an explicit, separate v2 research execution boundary, preserving
v2 spec/graph/queue/role/budget authority and isolating the model from direct
host tool access. The first acceptance slice must run with an offline
scripted/fake model, exercising actual persistence. Only then may a
subscription-compatible provider transport be connected and tested with
explicit owner authorization.

## Scope and phases

### Phase A — Offline trusted research execution (prerequisite)

- Keep v1 CLI and DB schemas unchanged; v2 never falls back to legacy.
- Require a persisted `ExplorationCampaignSpecV2` and the exact Full
  feature-flag profile. Pin immutable role provider/model/version/prompt.
- Plan only ready Exploration nodes using `HybridScheduler` and the
  authoritative budget snapshot; then use `EvidenceOnlyAttemptAdapter`
  for job admission, lease/fence, capability grant, evidence publication and
  Research Ledger ingestion. If another node phase requires an unimplemented
  lane, report that limitation instead of silently executing v1.
- The research model sees only allowlisted public role context, bounded
  tool observations, and prior public DEV findings; never send the raw
  queue request, campaign filesystem paths, provider credentials, hidden
  validation/HOLDOUT, or the evaluator.
- Every model-suggested read/write must run through `ToolGateway`.
  Reject arbitrary shell/network/direct file operations. Reject unexpected
  tool commands, oversized requests, score fields and unsafe paths.
- Preserve versioned evidence schemas and deterministic/idempotent Core
  publication, including duplicate and delayed attempts. Catch failures
  without inventing a successful completion.
- Demonstrate two distinct nodes and memory propagation to the second
  researcher invocation with a scripted model; zero subscription calls.

### Phase B — Model transport and operator CLI (not implied by Phase A)

- Implement an *actual* provider/model transport pinned to the researcher
  manifest that cannot independently read host files or execute tools.
- An agent request must go through a trusted one-shot broker and enforce
  authenticated usage, hard deadline and cancellation; if the Codex Plus
  subscription cannot supply these controls, the execution gate remains
  closed. A protected credential channel must not be mounted in the agent
  worker environment. Do not automatically switch to paid API.
- Provide an explicit v2 entrypoint, preflight and bounded supervised
  stop/resume; never reinterpret a v1 campaign as Full.
- Provide one command for a prepared v2 campaign only after the transport
  and isolation tests are green. No model call is authorized by this RPDI.

### Phase C — Full capability declaration and qualification

- Verify every Full-flag mechanism is **implemented and exercised**,
  not merely enabled. Optimization and Independent Verification cannot
  be claimed complete from planner-lane tests; integrate real execution
  adapters and the frozen evaluator protocol before declaring complete Full.
- An independent v0.2 vs Codex benchmark remains RPDI-14, and release
  remains RPDI-15. Do not use this RPDI's mocked results as quality evidence.

## Required negative tests

1. V1 or Diagnostic spec is rejected on the v2 execution entrypoint.
2. Wrong role/model/manifest/prompt identity fails closed before dispatch.
3. An unapproved read, path traversal or remote/shell command is denied;
   hidden gold/evaluator paths never enter model-visible context.
4. Token/agent budget exhaustion or inactive graph prerequisites block
   enqueue; never silently fall back to v1.
5. Completion publication is durable/idempotent; replaying a finished job
   does not consume another subscription call.
6. Contradictory research evidence remains unverified and is carried
   through the Research Ledger, not promoted to trusted evaluation truth.
7. The model transport cannot read private credentials or the evaluator,
   including after cancellation and across crash/resume.
8. Cross-platform Python 3.11/3.12/3.13 and current browser/API regression
   CI is green at the exact integrated head.

## Owner acceptance

- **Offline test first**: a reproducible test command and exact code SHA
  proving the whole pinned v2 Exploration → Queue → Gateway → evidence →
  Research Ledger route, using no external LLM.
- **Supervised live smoke only after explicit owner approval**: one real
  subscriber-authenticated researcher invocation with verified isolation
  and bounded resources; report UNKNOWN metering honestly.
- **Full sign-off separately**: all Full execution lanes (not only
  Exploration) and the RPDI-14 gates have independent evidence.

No keys, OAuth material, answer keys, private datasets or raw hidden scores
are allowed in Git commits, PR bodies or CI logs.
