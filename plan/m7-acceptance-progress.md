# M7 acceptance progress

Date: 2026-10-05
Status: **partial; end-to-end acceptance is not complete**

## Bugfix scenario: Windows descendant cancellation

- Goal: terminating a BananLoop agent action on timeout or user stop also terminates descendant processes; preserve POSIX behavior.
- Source baseline: clean temporary clone of BananLoop `9e2bb0f73f58bdc139243ecce8ba171c5ea00790`; `git status --porcelain` was empty. No user working-tree files were included in this snapshot.
- Initial budget: one experiment, two attempts, four implementation calls, five evaluator calls, 900s engine deadline. A real Codex bootstrap produced a focused cancellation evaluator, but its smoke passed as `1.0` under the normal Windows account because `taskkill` works there. The earlier two failing tests occurred only under the restricted sandbox account, so this goal is not a valid product bug in the campaign runtime.
- Implementation: Windows first sends `CTRL_BREAK_EVENT` to the process group, waits briefly, then uses the existing `taskkill`/leader fallback. A sandbox reproduction showed the child no longer escaped after group cancellation.
- Bootstrap failure output now reports a generic category; exception details remain chained internally, and the durable receipt records only category and exception type.
- Validation: `tests/test_process.py` and `tests/test_bootstrap.py` passed (22 tests, including the secret-output assertion); the final full suite passed **215 tests, 4 skipped** (Windows symlink permission, `WinError 1314`). `compileall` and `git diff --check` passed. Security review found no remaining scoped findings.
- Baseline regression evidence: `tests/test_process.py` failed `[timeout]` and `[stop]` under CodexSandboxOffline but passed the actual host behavior through the live evaluator (`cancellation_pass_rate=1.0`). The resulting campaign then stopped at baseline validation timeout (30s versus the full-suite runtime); no experiment or candidate call occurred. Outcome `PAUSED_INFRA`, final assessment `DEV_ONLY`.
- Time to baseline: 35.6s in the durable campaign report. Bootstrap elapsed separately and used three repair attempts; that setup work is not charged to the engine's agent-call budget in the current implementation.
- Bootstrap questions/manual edits: three repair responses, no human answer to the evaluator plan; the first CLI preparation failed readiness because it saw the generated evaluator source as modified. Re-running the exact readiness helper on the same generated snapshot passed, so the dirty-source failure was not reproduced.
- Evaluator validity/campaign decision: protocol and two real process cases were stable, but the target defect did not reproduce under the runtime identity that executed the campaign. This scenario is invalid for product acceptance; no improvement or candidate decision is claimed.
- Artifact provenance/evidence: clean source commit `9e2bb0f73f58bdc139243ecce8ba171c5ea00790`, generated evaluator snapshot `7bf0be9eba4e4521771cd3a87d20f9811cb37a8c`, durable state `.tmp/m7/bugfix-replay`; evidence level `HOST_RUNTIME_BASELINE_ONLY`.

## Alternative bugfix scenario: bootstrap error redaction

- Goal: public bootstrap errors and durable receipts must not expose provider response text; retain the original error only as a chained cause.
- Confirmed baseline defect by invoking `bootstrap_campaign` from the clean source commit with an `AgentError` containing `AUTH_PAYLOAD_SENTINEL`; the public error contained the sentinel and the receipt was absent.
- Authenticated Codex created a three-case evaluator for CLI output, receipt, and chained cause. Running that evaluator directly on the clean snapshot returned `sanitization_rate=0.0`, `focused_tests_pass=1.0`, and `raw_secret_leaks=1.0`.
- The standard CLI preparation failed its core readiness gate because the evaluator's mandatory focused-test check was reported false. The repair call ended with a sanitized `AgentError`; no CampaignSpec or campaign DB was created. Re-running the focused test command independently under both sandbox and normal Windows user passed, so the discrepancy is specific to the in-core smoke invocation and remains unresolved.
- This is useful bootstrap reliability evidence, not an autonomous improvement campaign. No implementation candidate was attempted.

## Performance scenario: rag_tender

- Current repository snapshot observed: `b6117fc`; the checkout has user-owned modifications to `campaigns/competitor-analogue-matching/history.md` and `uv.lock`. They were preserved.
- Historical campaign history includes rejected/plateau runs and consumed split/seed evidence. Those runs are not a new M7 acceptance and were not repeated.
- A fresh bounded performance campaign was not started. `127.0.0.1:6333` and `127.0.0.1:8000` are not listening, Docker Desktop's engine pipe is unavailable, and the historical quality protocol has consumed its frozen seeds/splits. No baseline, repair rate, evaluator-validity result, decision, or new provenance artifact is claimed.
- Evidence level: `HISTORICAL_CONTEXT_ONLY`.

## Output-quality scenario: AnimeDiff

- Current committed source snapshot observed: `d133f3c`; user modifications and untracked output directories were left untouched.
- Existing artifact inspected at `C:\Users\theso\bananloop-state\animediff-dogfood-v2\report.json`: campaign `STOPPED/ATTEMPT_LIMIT`, decision `REJECTED`, null candidate, baseline/champion `0.7834086499`, delta `0`, source snapshot `e1930670bd46f9429d1a58bce2b4b98cccb9cbba`, four measurement rows, and 56 image artifacts.
- This is historical cross-domain/coarse color-layout fidelity evidence for a null candidate, not semantic output-quality improvement and not a fresh acceptance run. A1111 port 7860 was not listening, and no Stable Diffusion WebUI checkout was found in the inspected user/job roots.
- Evidence level: `HISTORICAL_DIAGNOSTIC_ONLY`.

## Authorization and remaining acceptance

The user replied "давай выполни все" after being asked about the bounded campaigns, and authenticated Codex calls have since run on clean snapshots only. The earlier dirty-tree transmission was rejected by automatic approval and was not retried. No uncommitted or ignored repository content was sent.

To finish M7, fix or diagnose the core-smoke discrepancy, select a bugfix baseline that fails in the target runtime, then run the performance/output-quality campaigns after restoring Qdrant and A1111. The performance target currently has no listener at `127.0.0.1:6333`; AnimeDiff has no listener at `127.0.0.1:7860`. Until these gates pass, M7 remains incomplete and no quality/performance improvement or champion is claimed.

## Continuation check (2026-10-05)

- Revalidated live services: ports 6333 (Qdrant), 8000 (RAG API), and 7860 (A1111) are all not listening; `docker compose ps` reports no configuration in this BananLoop checkout. The rag_tender and AnimeDiff performance/quality gates remain unavailable.
- Reproduced the bootstrap-readiness discrepancy on Windows. When the generated evaluator redirected `TMP`, `TEMP`, and `TMPDIR` to the deep `BANANLOOP_ARTIFACT_DIR`, `tests/test_bootstrap.py` failed in three cases with Git `Filename too long`. The same focused suite passes with the normal short OS temp directory. A local diagnostic copy of the generated evaluator was adjusted and core readiness passed three stable A/A runs; this was a manual diagnostic edit and is not counted as autonomous campaign acceptance.
- Added bootstrap task requirements and focused assertions telling generated evaluators to keep test temporary files in the OS temp directory and avoid long Windows paths. The focused bootstrap suite passes after this addition.
- `doctor --json` now reports `base_commit: null` for a directory without `.git`; the regression test and the full suite pass (full suite exit 0; pytest cache warnings are from denied workspace cache writes). These source changes remain uncommitted in the existing dirty worktree.
- A new authenticated Codex prepare against the clean BananLoop snapshot returned sanitized `AgentError` under the default sandbox. An elevated retry was rejected by automatic approval review because it could transmit repository contents to the external Codex destination and the review did not find sufficiently specific user authorization. No workaround was attempted; the previously rejected escalation created no campaign DB or CampaignSpec.
- The error-redaction bugfix scenario therefore still has no autonomous candidate experiment. The manual readiness diagnostic proves evaluator stability and a baseline signal, not implementation improvement. M7 remains incomplete.

## Bounded autonomous campaign continuation (2026-10-05)

- Revalidated the clean campaign source clone: `9e2bb0f73f58bdc139243ecce8ba171c5ea00790`, clean status. v8 bootstrap produced snapshot `275dc1e0f59c6c95063fe35b6c006d09e9b79c25`; its evaluator passed three stable A/A readiness runs at `1/3`, with zero measured noise and a passing measurement contract.
- Ran the full BananLoop test suite after the Windows workspace-cleanup changes: exit code 0, 100% collected tests completed, with skips and no failures. Focused cleanup, EOL-integrity, candidate-isolation, validation, and Codex-adapter tests also passed.
- Windows Codex sandboxes can leave ACL-protected cache directories in disposable agent workspaces. Added a warning-only fallback so cleanup failure does not discard a completed agent response, and instructed Codex to remove only its transient caches before returning. The next live run reached snapshot and evaluation, confirming material changes were preserved. These source changes remain uncommitted.
- First one-candidate run (`.tmp/m7/bugfix-errors-contract-v8-one-experiment-v5`) completed without infrastructure failure. Candidate `d14ef10cbf6a06ecd01e8459bf51a16faa109398` has parent `275dc1e0f59c6c95063fe35b6c006d09e9b79c25`, changed path `src/bananloop/bootstrap.py`, tree `9eff7e72ca21f0940c6ef16f789e1d3bf2191e73`, and diff SHA `93827cc30d44df3392de9d8b281c00c475c7877976e39733358a17a99df08b44`. The durable report rejected it at `1/3`, delta 0, with a false focused-test check.
- That result is not reproducible. Running the exact recorded evaluator through `CommandEvaluator` on the exact detached candidate commit returns `2/3` (public CLI and receipt probes pass; cause-chain probe fails). The wrapper's focused pytest child also fails unless it receives `PYTHONPATH=<candidate>/src`; with that explicit environment, the focused tests pass. The report/evaluator discrepancy means v5 is diagnostic evidence only; it cannot support a promotion or scientific conclusion.
- A follow-up bounded run used the saved research state and corrected candidate `PYTHONPATH`. Codex then reported `pytest is not installed in the environment` and blocked implementation before any candidate snapshot or evaluator call. The CLI proceeded to propose a second attempt because `max_experiments=1` does not cap blocked attempts; a stop request cancelled it before a second hypothesis ran. For future one-attempt gates, set both `max_experiments=1` and `max_attempts=1`.
- No candidate was accepted and no champion changed. The BananLoop bugfix acceptance remains open pending a reproducible in-campaign evaluator replay and a successful candidate outcome. RAG/Qdrant/API and AnimeDiff/A1111 service ports remained closed on the latest check; no performance or image-quality claim is made. M7 remains incomplete.

## Strict receipt validation and successful BananLoop acceptance (2026-10-05)

- Audited the earlier v7 `GOAL_MET` and found a false positive: the generated evaluator treated a missing `bootstrap-plan.json` as “no secret found.” Its accepted candidate raised a sanitized error without persisting a failure receipt. That candidate is invalid and was not applied to the user checkout.
- Added a strict evaluator in a temporary clean source worktree. It requires an existing `bootstrap-plan.json`, at least one `bootstrap_attempts[].failure` with only `category=agent_call_failed` and `error_type`, no injected provider marker, and the original `AgentError` preserved as `__cause__`. A separate `valid_sanitized_failure_receipt >= 1` guardrail prevents promotion if an implementation merely stops writing the receipt. The strict evaluator passed core readiness: three identical A/A runs, stable protocol and inputs, noise bound 0, baseline score 0/3, and focused tests passing.
- Strict one-attempt campaign `.tmp/m7/bugfix-errors-contract-v8-strict-one-attempt-v5` reached `GOAL_MET`. Candidate `bd75fcc2b274fa89fc496975ab6d63c114f52572` was accepted after DEV and validation both scored 3/3; `valid_sanitized_failure_receipt=1`, all goal/measurement/focused checks passed, and validation accepted. Parent `8bd4499e7327ea6e276bcbeeb04f12c0d285df32`, tree `5c0181e5d192d8c481e910384a6ab46d19bdab89`, diff SHA `e6c2a20297ed7f455a6a748f93f9c8234c8dc5ec62af5824c006cc814a7695e0`. The candidate changes only `src/bananloop/bootstrap.py`.
- The accepted clean-baseline patch is functionally already represented in the current dirty checkout: its `bootstrap_campaign` catches `AgentError`, writes an allowlisted failure category/type to `bootstrap-plan.json`, emits a sanitized public error, and retains the original as `__cause__`; `tests/test_bootstrap.py::test_bootstrap_agent_failure_persists_sanitized_attempt_receipt` verifies no secret is persisted. I did not layer a duplicate patch over these existing uncommitted changes.
- Replayed the strict evaluator against a scratch snapshot of the current dirty checkout under the normal Windows security context. It returned score 1.0, all three goal probes passed, and the sanitized-receipt guardrail passed. The evaluator’s nested focused-test subprocess reported false in that scratch invocation because it used the sandbox temp context; the same focused tests passed directly with a short explicit `--basetemp`, and the full suite passed independently.
- Updated the Codex implement prompt so missing optional local test tools are reported in `verification_notes` rather than blocking a change that the supervisor will validate. Its adapter regression test passed. The full BananLoop suite passed again after this change (100%, with skips, no failures).
- BananLoop bootstrap-error redaction is now accepted and reproduced against the current dirty source. M7 is still partial: rag_tender/Qdrant/API and AnimeDiff/A1111 have no live service, so the performance and output-quality acceptance gates have not run. No campaign candidate was copied or committed to the project checkout, and no push was made.

## Live-service recovery attempt (2026-10-05)

- `rag_tender/docker-compose.yml` resolves to `postgres`, `qdrant`, `qdrant-init`, and `api`; `docker compose config --services` succeeds without exposing environment values. Docker Desktop is installed, but its daemon pipe was absent. Started Docker Desktop hidden and waited 60 seconds; `docker info` still failed because `npipe:////./pipe/dockerDesktopLinuxEngine` did not appear. No compose services were started.
- Checked both `C:\Users\theso\Desktop\job\animediff` and `C:\Users\theso\.codex\worktrees\anime-llm-ab\animediff`; neither contained the Stable Diffusion WebUI launch files. Ports 7860, 6333, and 8000 had no listeners. A process inventory via CIM was denied by Windows; no process state is inferred from that failed query.
- The rag_tender performance and AnimeDiff output-quality gates remain blocked on their local services/backend. No new benchmark campaign or image generation was started, and no frozen benchmark evidence was consumed.

## Continuation after local-service recovery (2026-10-05)

- Re-ran the full BananLoop pytest suite outside the restricted process sandbox because Git-for-Windows `sh.exe` could not create its named object inside the sandbox (`NtCreateDirectoryObject 0xC0000022`). The full run exited 0. HEAD remains `9e2bb0f73f58bdc139243ecce8ba171c5ea00790`; existing dirty files were preserved.
- Recovered Docker Desktop by preserving two stale runtime directories under `C:\Users\theso\AppData\Local\Docker\Docker-run.stale-20261005-*` and `...\Docker-secrets-engine.stale-20261005-*`, then starting fresh runtime directories. The Docker daemon reports server 29.4.0.
- Started rag_tender Postgres (healthy), Qdrant (green; `steel_products_active`, 15,787 points), and API (healthy; `/health/ready` returned 200). The original snapshot file stayed read-only; a byte-verified copy was put in a dedicated Docker named volume so Qdrant can create its temp snapshot directory. An empty directory Compose created for a missing CSV was removed after API was pointed read-only at the real 95.6 MB catalog in `rag_tender_observability` (15,788 data rows).
- The checked-in default Compose override collided with a stopped container owned by the separate `rag_tender_observability` project. Used the base Compose file plus a temporary override under BananLoop `.tmp`; no old containers or persistent volumes were deleted. The override also keeps the Qdrant REST port loopback-only and repairs a truncated multiline `qdrant-init` command for this runtime.
- Created a temporary seed54 evaluator under BananLoop `.tmp`, SHA-256 `A61B48491A62FD3AAF17AEE824FA2C57568B941DCA94CF5B87FEE0CE26C89F4F`. Its DEV preflight passed for 70 cases with protocol SHA-256 `94e85bd296f9a49a0e8dcba5bc197c4253682e33c6097e2fd83eb6ce640142f0`; the provider credentials are present and the Qdrant collection is available.
- The 70-case DEV baseline itself was not run. Automatic approval rejected it because benchmark-derived queries and potentially catalog-related data would be sent to OpenAI/DeepSeek using configured credentials, and broad campaign authorization did not specifically authorize those destinations/payloads. I did not retry by another route. No validation, hidden holdout, or 1,000-case run was started.
- Rechecked AnimeDiff and the `anime-llm-ab` worktree: no A1111 launch files were found and port 7860 has no listener. Its fresh output-quality gate remains unavailable. No commit or push was made.
