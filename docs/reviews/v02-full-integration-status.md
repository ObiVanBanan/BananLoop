# BananLoop v0.2 integration status

**Tested code commit:** `0afedb6473ea118b4e5522d7e93d64f82dc1bd10`  
**Branch:** `codex/v02-full-integration`  
**Base:** current GitHub `main` at `6c89975870b0da8060146953faea966e203ba857` (PR #130)

This is an incremental engineering checkpoint, **not v0.2 Full acceptance**.
The machine-readable results are in
[`v02-full-integration-results-0afedb6.json`](v02-full-integration-results-0afedb6.json).

## Implemented in this integration branch

- Exploration campaign loop creates durable follow-up nodes from completed
  worker output, carries public Research Ledger memory into the next context,
  rebuilds context from trusted provenance, and recovers completed queue work
  without another model dispatch.
- Queue budgets pass through to researcher roles. Pause, resume, and stop state
  is persisted in SQLite; claims are gated transactionally, and control changes
  have immutable history. Paused READY work resumes without a duplicate model
  call or a new budget hold.
- The Optimization adapter accepts bounded full-file replacements, applies
  writes through ToolGateway, commits private candidate snapshots, routes DEV
  evaluation and independent verification through durable components, and
  promotes only a verified candidate. A bounded `run_campaign` advances the
  scheduler through proposal, evaluation, and verification steps.
- Official SIWC Responses transports now support pinned optimizer and
  tool-free integration-verifier protocols in addition to the researcher
  protocol. No API-key fallback or additional authorization flow was added.
- The offline Exploration diagnostic accepts an owner-provided goal and writes
  a JSON result with the tested revision, job IDs, evidence counts, and
  explicit Full-qualification status.

## Verification

On the tested code commit:

```bash
PYTHONPATH="src:<cached dependency paths>" python3.12 -m pytest -q \
  tests/test_chatgpt_plan.py tests/test_worker_queue.py tests/test_node_scheduler.py \
  tests/test_optimization_lifecycle.py tests/test_evaluator_verification.py \
  tests/test_v2_optimization_runtime.py tests/test_v2_research_runtime.py \
  tests/test_exploration_adapter.py tests/test_research.py
```

Result: **145 passed**.

The reproducible offline Exploration diagnostic also passed on that commit:

```bash
python examples/v2_research_diagnostic/run_offline.py \
  --work-dir /tmp/bananloop-v2-offline-acceptance \
  --goal "Investigate whether an intervention changes a controlled toy outcome"
```

Result: two durable Exploration jobs, one evidence item per turn, public
Research Memory visible on the second turn, and `live_model_called: false`.
The diagnostic correctly records `complete_full_pipeline_proven: false`.

Repository-wide test run on the same commit collected 913 tests:

- 885 passed.
- 7 failed and 16 errored while trying to bind loopback sockets. A representative
  failure is `PermissionError: [Errno 1] Operation not permitted` in
  `ThreadingHTTPServer`; all 16 setup errors occur at that boundary.
- 5 skipped: one unavailable pinned selector source commit, three sandbox
  socket-dependent benchmark checks, and one Windows ACL contract on Linux.

Trusted evaluator isolation could not be executed in this container. The
available `bwrap` binary exits with
`loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted`.
The Optimization lifecycle integration test therefore uses a deterministic
test runner for the isolated subprocess result; it verifies queue, candidate,
protocol, receipts, guardrails, verification, and promotion wiring but does not
prove that the sandboxed evaluator process runs here. No security checks were
weakened.

## Remaining acceptance gates

- There is not yet a public v2 campaign creation/run/resume CLI. The offline
  diagnostic and one-call researcher smoke are examples, not a general
  campaign launcher.
- Exploration currently receives a selected task and returns one `next_experiment`
  at a time. It does not yet ask for and rank several competing hypotheses at
  each decision, detect convergence, or emit a campaign final report.
- The full Exploration → Optimization handoff → real isolated DEV evaluation →
  independent verification → final report has not passed as one offline E2E.
  Current Optimization integration coverage uses a deterministic evaluator
  runner because this host denies the required network namespace.
- The SIWC optimizer and integration-verifier transports are preflight-tested
  with mocked HTTP responses. No paid ChatGPT request has been made; the
  one-request live command remains behind `--approve-one-call`.
- Full API tests require loopback socket creation, and trusted evaluator tests
  require bubblewrap user/network namespaces. They must pass in CI or another
  Linux environment that grants those operations before Full acceptance.

This status is intentionally explicit: the code and evidence advance the
integration, but the required executor and end-to-end gates above remain open.
