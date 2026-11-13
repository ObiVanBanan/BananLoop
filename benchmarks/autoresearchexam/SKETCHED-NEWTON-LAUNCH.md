# Sketched Newton — one-task BananLoop v0.2 → Harbor handoff

**Scope:** one approved parent pivot, one existing frozen Optimization child, and one safe Harbor task. This file describes a no-model-call handoff. It does not start a ten-task series or claim an official AutoResearchExam result.

The operator's local campaign registry is authoritative. GitHub cannot tell whether a locally recorded pivot was executed or whether a previous Harbor request has uncertain settlement.

## 0. Inspect and preserve the current campaign

Use the checkout and state root belonging to the existing campaign. Do not delete, reinitialize or overwrite SQLite state, public-submission ledgers, or untracked local .agents/skills/ and openspec/ files.

Before any mutation, record outside Git the parent campaign ID, actual state root, proposal ID, approved digest, owner decision, original idempotency key, existing pivot/child receipts, child campaign ID (if any), budget and unknown allocations, source SHA, role-registry JSON, evaluator approval and public task pins.

An already recorded/executed pivot must not be used to create another child. Reconcile ambiguous persisted receipts first.

## 1. Complete the existing pivot only if necessary

Exploration and protocol approval were reported complete; the local child creation was reported pending. Read the pivot saga state before issuing any mutation.

If the approval is durable, no child was created, and the operator explicitly authorizes execution, use the existing CLI with values from the stored saga:

    python -m bananloop.cli v2-pivot execute \
      --repo "$REPO" --state-dir "$STATE_DIR" \
      --campaign-id "$PARENT_ID" \
      --proposal-id "$PROPOSAL_ID" \
      --digest "$APPROVED_PROPOSAL_DIGEST" \
      --idempotency-key "$DURABLE_IDEMPOTENCY_KEY"

Reuse the original idempotency key after uncertain execution. Do not launch Exploration again, create a child manually, or change a frozen protocol.

## 2. Prepare one pinned Harbor job without dispatch

This requires an existing approved frozen Optimization child, a task-specific DEV/VALIDATION evaluator, matching role registry, observed public MCP contract, a reviewed safe overlay index, and the pinned public Newton source.

    python -m benchmarks.autoresearchexam.prepare_sketched_newton_job \
      --repo "$REPO" \
      --state-dir "$STATE_DIR" \
      --campaign-id "$CHILD_ID" \
      --role-registry "$CHILD_ROLE_REGISTRY_JSON" \
      --source-task "$ARE_SOURCE/sketched-newton-cov-estimator" \
      --mcp-contract "$OBSERVED_NEWTON_PUBLIC_MCP_CONTRACT" \
      --overlay-index "$SAFE_OVERLAYS/index.json" \
      --jobs-dir "$RUN_ROOT/harbor-jobs" \
      --out "$RUN_ROOT/newton-prepared-001"

The output directory must be fresh and outside the checkout. The tool writes exactly three JSON files:

- bananloop.json: the child identity, task-pinned candidate path, role/model call cap, observed public contract, frozen image ID, and isolated public submission ledger.
- harbor.json: exactly one safe overlay and one bridge agent, no retries, no concurrency.
- preparation.json: the pinned summary and suggested command.

The generated candidate limit is taken from the frozen child (1–5); a one-iteration campaign is never silently upgraded to five. The prepare command makes zero model/evaluator/grader calls.

## 3. Preflight before authorizing a live attempt

1. Confirm child ID, parent lineage, approved protocol digest, role registry digest, task/source hashes, write path, model limit and unspent budget.
2. Use docker image inspect to compare the **exact** local image ID with the one printed in preparation.json. A mismatch is a blocker; do not silently change the pin.
3. Confirm Harbor version, public MCP contract, safe overlay, exclusion of the grader sidecar and verifier.collect hook, and absence of prior ambiguous grading submissions.
4. Run Harbor configuration dry-run with no model/grader calls. Dry-run validates metadata only, not the real evaluation.
5. Obtain explicit operator approval for the specific run and call budget. The JSON generation itself authorizes no spending.

## 4. Run one bounded Harbor attempt

Only after operator authorization:

    harbor run \
      --config "$RUN_ROOT/newton-prepared-001/harbor.json" \
      --disable-verification

Do not rerun a prior unknown Harbor or public MCP submission. Stop and inspect durable ledgers if status is SUBMISSION_UNKNOWN, budget is UNKNOWN, child identity mismatches, or the task times out. Never access private grader artifacts or claim a hidden HOLDOUT score.

The one-hour local time limit and frozen 1–5 iterations are a diagnostic pilot, not the official 24-hour AutoResearchExam setting.

## 5. Capture the result

Archive source SHA, parent/child campaign IDs, proposal and evaluator digests, baseline and candidate DEV score, public VALIDATION only when its receipt is settled, Integration Verifier result, exact model/evaluator calls, token observations, and the final campaign status.

A negative candidate is still a legitimate completed experiment. The separate series_runner.py requires independently prepared five-iteration job configs, and does not prepare/approve ten campaigns.

Focused offline tests:

    python -m pytest \
      tests/test_prepare_sketched_newton_job.py \
      tests/test_trusted_evaluator.py \
      tests/test_are_series_runner.py -q

These tests do not invoke an LLM or public grader.
