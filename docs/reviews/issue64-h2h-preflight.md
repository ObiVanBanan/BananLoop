# Issue #64 — AutoResearchExam H2H preflight

**Review date:** 2026-10-31
**BananLoop code base checked:** `44614ea22bc5967adb7ee607bce2472cc66487ee`
**Disposition:** preflight advanced, H2H remains `BLOCKED / INELIGIBLE`. No model calls, task execution, HOLDOUT scoring, or paid activity occurred.

## Protocol sources pinned for preparation

Issue #64 specifies the v1.1 CPU-budget pilot. Upstream `bespokelabsai/AutoResearchExam` `main` resolved to commit `23c0d92e11af1dea821f972b5f0a739060958002` when inspected on 2026-10-31. The README at that commit identifies the project as AutoResearchExam v1.1. The repository root `LICENSE` is Apache-2.0. Task instruction blobs at that revision:

| Task | Instruction blob SHA |
| --- | --- |
| Carps star discrepancy | `83960625fccf0d0287a88a9a4def0828d242f136` |
| Budgeted imputation MCAR50 | `f84ec423ac8078a03282890c7b000594230f982e` |
| Label-efficient risk estimator | `b1bef81331df5879fe215a4885d24b503144bdae` |

These are preparation references, not a frozen trial manifest. Dataset case IDs, grader files and third-party data licenses remain to be reviewed and hashed before any model call. No official hidden data, dataset bundle, grader, or candidate was downloaded or executed during this preflight.

## Environment and isolation evidence

- Docker Desktop 29.9.2 is available. The engine reports 12 CPUs and about 16 GiB RAM; host `C:` had about 82 GB free at check time.
- A digest-pinned official Python image passed the limited isolation smoke described in [the isolation report](are-isolation-smoke.md).
- The smoke enforced 2 CPUs, 8 GiB memory, 64 PIDs, no network, read-only root, no host mounts, zero effective capabilities, `no-new-privileges`, and a bounded temporary filesystem. Docker accepted `--storage-opt size=24g`, but enforcement and persistence on candidate data were not tested.
- No H2H task adapter, pilot runner, agent/grader split, or restricted model proxy exists in the main tree. The container has no model connectivity by design. Codex CLI 0.165.0 exists on the host, but no credential was mounted into the container and no API endpoint was contacted.
- The MLflow Python package is installed locally. A zero-cost SQLite-backed tracking smoke logged and read back one parameter and metric using a temporary database; that database was removed after the smoke. No persistent `MLFLOW_TRACKING_URI`, campaign tracking configuration, or container-to-supervisor tracking path is configured yet.

## Remaining gates before a run

1. Implement and review the three exact-version adapters and independent scorer; freeze task source IDs, public DEV/VALIDATION/HOLDOUT partitions, grader hashes, dependency lock and baseline artifacts. Check licenses for every included dataset asset.
2. Build the restricted runner: separate agent and evaluator containers and storage, protect grader/HOLDOUT data, deny traversal/symlinks and host credentials, disable candidate egress, and negatively test all prohibited paths.
3. Verify a narrowly allowlisted model proxy that cannot relay arbitrary traffic or expose its provider credential. Reconcile the experiment's $3 total cap with #61's post-call receipts and prove attributable per-call USD accounting. Current #61 aggregates input and output tokens, has no USD ledger, does not enforce the 800k-input / 100k-output dimensions separately, and may overshoot its token reserve after a call; the automatic bootstrap path is not metered. Token limits alone are not a hard dollar ceiling.
4. Enforce and verify per-arm disk quota, CPU/RAM/process/wall-clock limits and cleanup. Docker accepted the requested storage option, but disk-cap enforcement has not been demonstrated.
5. Implement and test identical iterative Codex Control and BananLoop treatment harnesses, fake-agent/fake-meter smoke, immutable manifests, event/cost logs and independent post-freeze scoring. Configure and verify the persistent MLflow SQLite backend and its supervisor-only write path before baseline execution.
6. Only after all gates pass, use the Issue #64 $3 cap and run the baseline first. Mark missing or unverifiable cost as `INELIGIBLE`; do not score any overspending arm or use an unsandboxed fallback.

The host sandbox smoke proves only that Docker can apply basic controls to a harmless probe. It does not qualify the proposed evaluator boundary or authorize a paid H2H.
