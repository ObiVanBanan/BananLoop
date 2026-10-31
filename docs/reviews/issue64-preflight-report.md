# Issue #64 preflight report — 2026-11-01

**Decision: BLOCKED / INELIGIBLE.** Do not start any of the six paid A/B arms. No benchmark candidate, model call, treatment score, validation score, or HOLDOUT score was run.

## Requested experiment

Issue #64 specifies the AutoResearchExam v1.1 CPU pilot on Carps, Budgeted Imputation, and Risk Estimator. It sets a shared external spend maximum of **$3 USD**, per-arm token ceilings, sequential execution, and a requirement that BananLoop spend no more than the corresponding ordinary-Codex control. The $3 maximum is a protocol limit; it is not proof of an enforceable or independently settled USD meter.

## Preflight evidence

| Gate | Evidence | Result |
| --- | --- | --- |
| Candidate runtime | PR #83 adds a digest-pinned Docker candidate runner. PR #84 head `97894f13ab463850b5a6901e4dcbcb017d8616a1` passed hosted Ubuntu run [#41241757018](https://github.com/ObiVanBanan/BananLoop/actions/runs/41241757018) and duplicate push run [#41241752717](https://github.com/ObiVanBanan/BananLoop/actions/runs/41241752717), using only the trusted inert probe. It checked UID 65532, zero effective capabilities, no-new-privileges, seccomp, read-only root/artifact, no external network, and CPU/memory/PID limits. | Partial; one Linux candidate-container policy only |
| Current task host | Docker and Podman commands and Docker sockets are absent. A trusted `/bin/true` invocation under `unshare --user --map-root-user --net --mount --pid --fork` succeeded. The resulting network namespace had a distinct inode, only loopback, and no route. `bwrap --unshare-net` failed while creating a NETLINK_ROUTE socket with `Operation not permitted`. These probes ran no candidate code and did not test egress by sending packets. | Not a candidate sandbox or target-host preflight |
| Windows target | The earlier Windows desktop session could not access its Docker Desktop pipe. Windows Docker Desktop behavior is not covered by the Ubuntu smoke. | Unverified |
| Agent/evaluator boundary | The Carps adapter scores outside Docker and requires `evaluator_root` to be private/non-agent-visible, while documenting that it cannot prove those host-level facts. The candidate runner is not integrated with `CommandEvaluator` or readiness; same-user agent access, evaluator ACL/process/storage isolation, and model-proxy isolation are unproven. | Blocked |
| USD accounting | Issue #64 sets the $3 maximum. Current token receipts are post-call accounting and do not establish provider-billed USD, enforce the $3 total, or enforce B ≤ A by task. Bootstrap accounting is still a draft change outside `main`. | Ineligible for the cost-gated claim |
| Data rights/privacy | The pinned AutoResearchExam repository license does not establish rights for every bundled dataset. Carps example-cloud provenance/license is not established; the Imputation manifest does not identify sources/licenses for all datasets; the Risk Estimator corpora are unnamed. | Incomplete |

## Decision and reopening criteria

No unsandboxed fallback is permitted. The hosted inert probe is useful evidence for one Linux candidate boundary, but it does not establish safe agent/evaluator separation or Windows target readiness. The Linux namespace probes above are diagnostic only.

Reopen the pilot only after the Windows target-host negative preflight passes, the agent and evaluator are isolated with tested denial of filesystem/process/network access, the model proxy and exact USD admission/settlement meet Issue #64's limits, and source/data rights are resolved. Then run the deterministic fake-agent/fake-meter one-task smoke before considering any paid arm.

**Current benchmark state remains BLOCKED / INELIGIBLE.** The full evidence and recommendation are tracked in the [core readiness audit](core-readiness-audit.md) and [benchmark status register](../BENCHMARK_STATUS.md).
