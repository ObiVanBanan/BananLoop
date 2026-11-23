# Paired final-artifact contract

**Scope:** internal, non-RPDI-14 comparison of Codex Direct and BananLoop Full on the curated AutoResearchExam v1.1 task set. This is a public-DEV diagnostic. It is not a live result, a blind evaluation, or evidence from an independent/private holdout.

## Comparison unit and shared inputs

The unit is one task paired across two independent arms: `codex_direct` and `bananloop_full`. Each arm gets 3,600 seconds of monotonic wall-clock research time, the same task goal, model, execution environment, public DEV access and allowed tools. The same deadline spans all BananLoop stages and children. The comparison budget is elapsed time. The current Full harness also has defensive technical ceilings tracked under BL-042. Record observed turns, tokens, retries, errors and unknown usage. Do not use a separately billed API or fallback.

Both arms may use public DEV feedback during their allotted time. They do not receive VALIDATION or HOLDOUT material. Once both arms for a task are terminal and their deliverables are frozen, evaluate each deliverable with the same pinned public DEV scorer, inputs and metric contract. Do not show this final score or the other arm's deliverable to either arm. Preserve raw numeric score, metric name, direction, aggregation, status, and scorer receipt separately; do not silently convert missing/error outcomes into scores.

## Native final workflows

Codex Direct is an iterative coding process. At its deadline, terminate its process group and snapshot the bytes at the catalogue's required public output path. It is a final-as-is snapshot: do not select a prior best checkpoint, repair, or rerun after timeout.

BananLoop Full follows its native Exploration-to-Optimization flow under the same one-hour budget. At its deadline, snapshot the selected champion/final deliverable BananLoop would normally produce. Do not add a post-hoc optimizer or checkpoint-selection method to either arm.

This is a comparison of native end-to-end workflows under matched time and public DEV access. The artifact selection rules differ by design and must be disclosed with results; the comparison does not claim identical checkpoint algorithms.

## Pins required for every pair

Freeze the run manifest before launching an arm and include its canonical digest in the archive. The manifest must bind:

- repository/source HEAD SHA and the exact task catalogue digest;
- upstream AutoResearchExam revision and task ID/version, plus task, instruction, license, Dockerfile, `.dockerignore`, requirements and public DEV file hashes where present;
- public DEV image tag **and actual local image ID** (content digest), adapter ID, output path, entrypoint, metric ID/field, direction and aggregation;
- model identifier, CLI/runtime versions, tools, environment, permission and workspace boundaries, and the 3,600-second deadline policy;
- public DEV scorer/protocol and fixture/data identities, including their hashes and task-specific timeout settings;
- each arm's terminal status, timeout phase, start/deadline timestamps, artifact inventory, per-file byte count and SHA-256, observed usage (or `UNKNOWN`), and error/log references;
- post-run evaluator version/configuration and the exact scorer result/receipt for each artifact.

Use the values from the frozen catalogue and run manifest, not mutable image tags or values copied from an older report. The current catalogue declares upstream revision `23c0d92e11af1dea821f972b5f0a739060958002` and catalogue digest `b27bc81a5d335923846e21674a968dba7bd4e77508f3f0e25145686622dbf17b`; the actual local image IDs and the current source/catalogue-file digests must still be checked and recorded for each run. Any mismatch blocks readiness or is explicitly recorded as a mismatch; it is not repaired by substituting a different pin after execution.

## Artifact and outcome handling

Collect only the task's declared deliverable path(s), after stopping the arm, with path-safety and size checks. Hash the exact copied bytes. Preserve stdout/stderr, structured status, usage observations and error evidence in the arm archive. Archive all 24 terminal records, including failed, partial, missing and unknown outcomes; never omit a pair because it scored poorly or has no score.

Use explicit artifact states:

| State | Meaning and treatment |
| --- | --- |
| `CAPTURED` | Required deliverable path(s) exist, are regular safe files, and were copied and hashed. Submit these exact bytes to the common post-run DEV scorer. |
| `PARTIAL_INVALID` | Some bytes exist, but required files are absent, unsafe, oversized, corrupted, or structurally invalid. Preserve and hash what can safely be retained; mark invalid and do not repair. |
| `MISSING_OUTPUT` | No required deliverable exists at the deadline. Keep the terminal arm record; score as no result, not as zero or a successful baseline. |
| `UNKNOWN` | Collection or execution outcome cannot be established (for example, an interrupted arm with no terminal record, ambiguous scorer response, or unobserved usage). Preserve the uncertainty and do not replay/resubmit automatically. |

At timeout, stop the full process tree and any arm-owned containers, then snapshot whatever is present. A partial write stays partial; there is no post-timeout repair. Record a scorer error separately from the arm and artifact statuses. A missing or ambiguous metric has no numeric score. Do not resend an ambiguous request blindly. Record any independently established numeric metric alongside its status; a score never erases a failed, partial, or unknown artifact state.

## Disclosure and interpretation limits

The owner accepted the recorded operator exposure for an internal diagnostic. `act-tensor-sparse-panel-imputation-r2`, `causalpfn-cate-pehe-ihdp-surfaceb`, and `hicard-latent-encoder` have known prior private grader-context snippet exposure. Preserve `benchmarks/autonomous_v01/source-access-declaration.json` with the run archive and disclose this limitation anywhere their results are reported. Do not call the evaluation blind, pristine, independently held out, or RPDI-14 eligible. No private answer data or exact private test seed was intentionally opened according to that operator declaration; it is not an independent attestation.

Offline synthetic/fake runs can establish runner, snapshot, scoring-contract and archive behavior only. They are not model outcomes, public task scores, or a live Codex-vs-BananLoop comparison. As of the recorded offline preflight, the current environment remains `PREP_BLOCKED`. The Docker socket blocks verification of all local public DEV image IDs. The Codex preflight auth check also remains blocked. No live comparison is claimed by this contract.

## Current offline evidence

The repository contains a fake-series archive index at `benchmarks/autonomous_v01/fake-24-arm-e2e-index.json` (manifest digest `079f63ad07ae36e886d82e7319793f301ed0bc02ea9c2411e9b7bb915a9204cf`). It indexes the fake terminal arm artifacts and records no external model, Harbor evaluator, or AI Judge calls. These synthetic outputs do not establish benchmark scores or complete readiness. A current preflight report is `benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json`; it remains blocked and must be regenerated from the exact source and environment before any separately authorized live run.


The offline scorer first verifies the manifest seal and both terminal arm
receipts. It materializes both artifact snapshots as immutable byte mappings
before invoking the same supplied scorer for either system. It preserves
both rows and records `MISSING_OUTPUT`, `INVALID_OUTPUT`,
`UNKNOWN_OUTPUT`, `CORRUPT_OUTPUT`, or `SCORER_FAILED` when there is
no numeric result. Observed usage remains separate from the score.

The synthetic reference scorer counted archived bytes for all 12 paired
tasks and produced 24 numeric results. Its fixture tests cover a missing
or nonterminal second receipt, corruption, scorer failure, and exact
binary bytes after timeout. The combined paired scoring, dry-run, watchdog,
runner and v2 CLI suite passed 105 tests. The numeric results test the contract. The public
DEV scorer adapters and real-execution gate remain separate integration work.
