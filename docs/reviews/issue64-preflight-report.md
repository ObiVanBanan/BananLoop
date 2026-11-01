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
| Agent/evaluator boundary | The Carps adapter scores outside Docker and requires `evaluator_root` to be private/non-agent-visible, while documenting that it cannot prove those host-level facts. Draft PR #102 exact head `9102240f3cc5a0d49250e42721f730076eaa6d8f` adds Carps-specific resource clamps and fails closed when Docker cleanup cannot be confirmed; hosted run `41268664322` passed the Ubuntu/Windows pytest matrix and inert Docker smoke, with tests mocking candidate/Docker execution. The runner remains unintegrated with `CommandEvaluator` or readiness; same-user agent access, evaluator ACL/process/storage isolation, and model-proxy isolation are unproven. | Blocked |
| USD accounting | Issue #64 sets the $3 maximum. Current token receipts are post-call accounting and do not establish provider-billed USD, enforce the $3 total, or enforce B ≤ A by task. Bootstrap accounting is still a draft change outside `main`. | Ineligible for the cost-gated claim |
| Data rights/privacy | The pinned AutoResearchExam root Apache-2.0 license establishes code terms, not file-level rights for bundled task data. Source-level follow-up below found incomplete provenance and license/attribution/privacy metadata for all three tasks; two UCI records have CC BY 4.0 terms, but the manifests do not establish that the bundled bytes are those exact records. | Incomplete |

## Source-level data rights and provenance follow-up

The upstream task source is pinned to commit [`23c0d92e11af1dea821f972b5f0a739060958002`](https://github.com/bespokelabsai/AutoResearchExam/tree/23c0d92e11af1dea821f972b5f0a739060958002). Its root [Apache-2.0 license](https://github.com/bespokelabsai/AutoResearchExam/blob/23c0d92e11af1dea821f972b5f0a739060958002/LICENSE) applies to repository code under its terms; it does not establish provenance or rights for every bundled data file.

- **Carps / Star Discrepancy:** PR #84 pins the task instructions and scorer, and hashes eight named `.npy` clouds (5 DEV, 1 validation, 2 public-pool HOLDOUT). The upstream [example manifest](https://github.com/bespokelabsai/AutoResearchExam/blob/23c0d92e11af1dea821f972b5f0a739060958002/carps-star-discrepancy-subset-select/environment/app/data/examples/manifest.json) calls these “example clouds” but gives no generating source, per-file license, or attribution. The HOLDOUT is public and operationally withheld only; it is not a confidential hidden set. Checksums and pin establish file identity, not usage rights.
- **Budgeted Imputation / MCAR50:** The pinned [DEV manifest](https://github.com/bespokelabsai/AutoResearchExam/blob/23c0d92e11af1dea821f972b5f0a739060958002/budgeted-imputation-mcar50/environment/app/data/dev/manifest.json) names six corpus labels but supplies no source URLs, exact source revisions, licenses, attribution, or privacy notes. The official UCI records for [Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset) and [Wine Quality](https://archive.ics.uci.edu/dataset/186/wine+quality) state CC BY 4.0. This is conditional evidence for those upstream records only: the manifest does not link its bundled NPZ bytes to those exact records/revisions, and the Bike Sharing row count differs. A [NeurIPS 2024 supplementary source table](https://papers.nips.cc/paper_files/paper/2ee1c87245956e3eaa71aaba5f5753eb-Paper-Conference.pdf) suggests likely OpenML IDs for several labels, but no exact OpenML revisions or transformation lineage ties them to the bundled files.
- **Label-Efficient Risk Estimator:** The pinned [instruction](https://github.com/bespokelabsai/AutoResearchExam/blob/23c0d92e11af1dea821f972b5f0a739060958002/label-efficient-risk-estimator/instruction.md) describes 12 labeled DEV pools and disjoint held-out shards from “the same corpora,” but names no corpora and provides no source manifest, revisions, license/attribution, or privacy metadata. Dataset provenance and permitted use remain unverified.

These findings do not establish that the files are unlicensed; they establish that exact source lineage, applicable terms, attribution requirements, and privacy review are not evidenced by the current task manifests. No benchmark or task code ran during this review.

## Decision and reopening criteria

No unsandboxed fallback is permitted. The hosted inert probe is useful evidence for one Linux candidate boundary, but it does not establish safe agent/evaluator separation or Windows target readiness. The Linux namespace probes above are diagnostic only.

Reopen the pilot only after the Windows target-host negative preflight passes, the agent and evaluator are isolated with tested denial of filesystem/process/network access, the model proxy and exact USD admission/settlement meet Issue #64's limits, and source/data rights are resolved. Then run the deterministic fake-agent/fake-meter one-task smoke before considering any paid arm.

**Current benchmark state remains BLOCKED / INELIGIBLE.** The full evidence and recommendation are tracked in the [core readiness audit](core-readiness-audit.md) and [benchmark status register](../BENCHMARK_STATUS.md).
