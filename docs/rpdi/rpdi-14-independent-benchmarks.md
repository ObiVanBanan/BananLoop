# RPDI-14 — independent blind benchmarks for v0.2 Full

**Status:** preregistration and harness acceptance contract. No v0.2 benchmark
has been authorized or run. This document is not benchmark evidence and does
not make the current system eligible for a trial.

## Decision

Compare the integrated **BananLoop v0.2 Full** system with **Codex Direct** in
two independent paired tracks: Optimization and Exploratory Research. Keep the
task sets, primary outcomes, scoring, and reports separate. Do not combine the
tracks into a single score. Both systems may iterate on the same approved DEV
split and feedback policy; Codex Direct must not be reduced to a one-shot
baseline. Do not share one arm's outputs, observations, or feedback with the
other arm.

Each BananLoop Optimization trial uses the selected v0.2 Full Exploration
campaign and, only after its frozen verification gate passes, a distinct
Optimization campaign carrying the exact approved evaluator protocol and
assets. Its Optimization manifest pins the selected Exploration campaign ID,
campaign spec, exact source/Full identity, verification receipt, and evaluator
protocol/bundle digests; the run gate requires a signed verification artifact
over that complete anchor. No mode or evaluator protocol may be changed in place. Full means the
complete approved feature-flag hash, not a diagnostic subset.

## Run-order gates

The candidate task pool may be identified and sealed for preregistration before
these gates pass. No task may be assigned to an arm, allocated, executed, or
scored until all prerequisites below are evidenced at exact immutable
revisions:

1. RPDI-00–13 implementation contracts and required Definition of Done are
   satisfied, including the integrated Exploration → Research Intelligence →
   Optimization/Verification → child-campaign E2E.
2. Exact-head CI and technical review pass for the integrated candidate on all
   supported Linux and Windows configurations. The final source SHA, Full
   feature-flag hash, campaign/spec hashes, and evaluator digests are recorded.
3. The evaluator, sealed VALIDATION/HOLDOUT data, accounting, and execution
   boundary are independently verified. The scorer cannot access agent
   planning context, credentials, private prompts, or mutable source state.
4. Task-use and redistribution rights are cleared, spend and resource ceilings
   are explicitly authorized, and an independent benchmark operator accepts
   the frozen preregistration.

Any missing, UNKNOWN, or unverifiable prerequisite makes the benchmark
`BLOCKED` or `INELIGIBLE`. Do not substitute v0.1 results, a DEV-only mini-run,
or a same-host evaluator.

## Preregistration manifest

Before revealing tasks to either arm, create an access-controlled canonical
manifest that binds the items below. Publish only a sanitized preregistration
summary and the manifest digest. Keep task IDs, task hashes, assignments, and
evaluator identity sealed until both arms have frozen their final artifacts.

- Track, protocol version, task IDs and task-data hashes, rights/consent
  evidence, initial repository/tree hashes, allowed task statement, and task
  inclusion/exclusion criteria.
- A single A/B arm-label mapping chosen with the operator's cryptographic
  random number generator before either track is frozen. Bind its canonical
  digest into both track manifests and the signed run-gate evidence; keep the
  mapping sealed from the arms and scorer. The final signed unblinding must
  reveal that same mapping and bind the frozen score-package digest. A changed
  or missing mapping invalidates the report.
- Exact BananLoop source SHA, complete Full feature-flag set/hash, approved
  campaign spec/hash, pinned role/model/provider/version roster, reasoning
  settings, tool permissions, evaluator image/binary/source hashes, and
  evaluator protocol/approval digests.
- Codex Direct model/provider/version and reasoning settings, tool allowlist,
  initial state, and iteration policy. Match these to the BananLoop arm wherever
  the two systems expose the same control; disclose unavoidable differences.
  The frozen manifest hashes every exposed provider/model/reasoning/tool,
  iteration, role-roster, and resource-cap value on both arms, checks each
  `MATCHED`/`UNMATCHED` label against those hashes, and requires a disclosure
  for every difference. It also pins the exact approved DEV information digest
  shared by both arms; the `APPROVED_DEV_ONLY` policy label alone is insufficient.
- Per-arm ceilings for calls, input/output/cached tokens, wall time, evaluator
  calls, experiments, retries, and spend. Freeze both the worker memory ceiling
  and process-count ceiling; enforce them for the whole process tree with a
  delegated cgroup v2 (`memory.max`, `pids.max`, `memory.oom.group`, and
  `cgroup.kill`). Refuse to launch if those controllers cannot be delegated.
  Per-process `RLIMIT_AS` alone is not an aggregate memory bound. Record a
  resource receipt for every action. Action kinds must reconcile with their corresponding call, retry, or
  experiment counters. Missing spend/token data is `UNKNOWN`, never zero or free.
- Per-track estimand and primary outcome, score direction, deterministic
  seeds, permitted DEV feedback, stopping rule, tie rule, minimum usable pairs,
  and exclusion criteria. Freeze the inference method too: paired estimator,
  uncertainty interval method and coverage level, one- or two-sided decision,
  and treatment of task strata, clustering, missing pairs, and ties. State an
  a priori sample-size target using either a power target with its assumed
  effect and variance or a precision target for the interval width. Do not
  infer adequacy from a historical ten-task plan or a small DEV fixture.
  Specify how Optimization and Exploration decisions will be reported
  separately; any joint superiority claim across both tracks requires a
  preregistered multiplicity rule. Freeze all of this before any scored task is
  run; later changes require a new protocol version and a new untouched task
  set.
- The v1 preregistration harness currently supports paired mean differences
  with paired-t intervals and exactly one paired observation per independent
  task; repeated observations within a task are rejected because v1 does not
  model within-task correlation. Its sample-size helper uses a Cornish–Fisher
  approximation for the t critical value and a normal approximation to power;
  these calculations are planning estimates, not an exact guarantee of power.
  Before freezing either track, an independent statistical reviewer must
  verify the method and sample-size result for its stated assumptions and sign
  the exact canonical manifest digest. Unsupported designs require a separately
  reviewed method/version, not a hand-edited pair count.
- The offline gate verifier authenticates canonical, size-bounded evidence
  documents against a protected local trust policy. It checks artifact bytes,
  Ed25519 signature, authorized public key and role, exact subject and conclusion, signed
  CI job/head payloads, and distinct principals/keys for technical, statistical,
  evaluator-isolation, and benchmark-operator attestations. The trust policy is
  verifier-only and must be provisioned through a protected channel. Issuers
  keep Ed25519 private keys outside the verifier; the trust policy contains
  public keys only. The author/operator exclusion set is mandatory and independent
  reviewers/operators must use distinct principals and keys.
  On platforms without descriptor-relative no-follow file opens, the evidence
  root must be protected by filesystem ACLs from modification throughout gate
  verification.
  Synthetic fixtures demonstrate the mechanics only and provide no real gate
  evidence. No run is eligible until actual artifact files are reviewed, signed,
  and verified for the exact immutable inputs.
- Sealed task assignment, anonymized arm labels, evaluator identity and
  isolation attestation, artifact-retention plan, and canonical hashes for
  every final result artifact.

The preregistration must show that its planned number of usable task pairs
meets the chosen power or precision target under stated assumptions. If rights,
task availability, or the attainable sample size cannot support that target,
classify the track as underpowered/ineligible rather than silently relaxing
the target. Do not use AutoResearchExam or any dataset with uncleared
use/redistribution rights.

## Arm and scoring controls

- Begin each pair from the same task state, repository snapshot, approved DEV
  information, model capabilities, tool permissions, evaluator, and available
  resource envelope. Codex Direct may iterate and repair from approved DEV
  observations under the same preregistered caps.
- Keep validation and HOLDOUT labels, inputs, scores, and case identifiers
  sealed from both arms and all agents. Freeze final artifacts before the
  independent scorer receives anonymized outputs with arm-identifying
  metadata removed. Randomize arm labels. The benchmark operator and scorer
  cannot be authors or operators of the BananLoop arm. The scorer returns only
  preregistered aggregate results after both arms are final.
- The independent scorer attestation binds a canonical score projection:
  track/manifest/evaluator pins, scored blinded pair digests, anonymized A/B
  output digests, and scores. It excludes action receipts, accounting
  summaries, artifact paths, failure reasons, and unblinding data. The scorer
  request contains output bytes and nonsensitive outcome status; a verified
  anonymization pass is still required before any real run. The current adapter
  rejects literal pinned arm/provider/model fingerprints, but does not remove
  semantic or compressed identity leaks. An independent review and task/output
  specific transform are required. The host joins
  separately verified accounting after scoring. The operator's
  unblinding attestation binds the final package digest.
- The evaluator pin freezes its in-bundle executable argv plus scorer timeout,
  memory, and process-count ceilings. The bundle digest is computed over a
  canonical sorted tree of relative paths, file bytes, and modes. A fresh
  bubblewrap process receives only that read-only bundle and the bounded blind
  request; a delegated cgroup v2 enforces memory and process limits for the
  full process tree. Missing isolation fails closed with no host fallback.
  The bundle must contain `protocol.json`, `rubric.json`, `validation-set.bin`,
  and `holdout-set.bin`; each file is checked against its individual manifest
  digest, and the full tree is copied to a private snapshot and rehashed before
  launch. The scorer input is bounded to 500 pairs and 45 MB of output bytes.
  The manifest also pins hashes for the host read-only runtime trees, launcher
  interpreter, launcher code, and bubblewrap binary; the runner verifies them
  before and after scoring. These hashes bind the environment, but the host
  runtime is not yet delivered as a portable immutable image, so cross-host
  replay is not established.
- Optimization scoring uses the frozen protocol and identical evaluator
  version for both arms. Exploration scoring uses a separate rubric and task
  set fixed before the run. Do not let Research Intelligence findings count as
  evaluator approval or as scored success by themselves.
- Preserve every attempt, failure, timeout, budget exhaustion, invalid output,
  and exclusion with a reason. A pair with unknown/over-limit accounting,
  broken blinding, evaluator leakage, or mismatched immutable inputs is
  `INELIGIBLE`; it cannot be silently replaced after outcomes are known.
- Report per-task paired outcomes, wins/ties/losses, paired deltas and
  uncertainty, measured resources and costs, all failures/exclusions,
  protocol deviations, and limitations. Claim superiority only if the frozen
  decision rule supports it. Neutral or negative results are valid outcomes.

## Required implementation evidence

| Requirement | Evidence required before an authorized run |
| --- | --- |
| Two separate benchmark tracks | Distinct manifests, task sets, primary outcomes, evaluator/rubric pins, and reports for Optimization and Exploration |
| Frozen protocol and Full identity | Canonical manifest validation rejects changed source, flags, spec, model, worker bundle, evaluator, seed, task, or budget after freeze; authenticated independent statistical review signs the exact manifest digest |
| Matched iterative baseline | Tests prove each exposed arm-control pin matches its `MATCHED`/`UNMATCHED` classification, all differences are disclosed, task/DEV starting state and resource ceilings align, and Codex Direct can iterate within its frozen policy |
| Complete resource accounting | Hash-chained action receipts reconcile every role, retry, tool, token class, evaluator call, elapsed time, and spend against each arm cap; unknowns or overages make a pair ineligible. The report verifier checks chains and claimed summaries against per-arm caps and a signed independent-operator final-head pin; each action receipt must also have a 1:1 signed metering attestation binding its exact receipt, pair, arm, sequence, action ID, and usage digest. This authenticates the configured meter's attestation, not the external provider's usage truth or completeness. The metered worker dispatch requires complete pre-dispatch bounds and reconciles host-observed elapsed wall time with provider-reported usage before settlement. |
| Blind isolated scoring | Every scored output has a separately signed `INDEPENDENT_ANONYMIZER` attestation binding the retained raw and sanitized bytes, pair/arm label, and frozen anonymizer digest. The scorer-input builder and report aggregator reverify it; only sanitized bytes enter the isolated scorer. Negative tests cover raw-byte exclusion, direct pinned-identifier rejection, signature/payload mismatch, and aggregate-time tampering. Semantic identity leaks still require independent human review of task-specific outputs. |
| Honest eligibility and reports | Tests preserve failures/exclusions and produce paired per-task results with uncertainty, resource totals, and deviation records. The offline report aggregator revalidates signed run-gate evidence; the assignment entrypoint and metered isolated-worker dispatch consume signed run authorization. Worker result identity/canonical JSON and broker-receipt equality are checked before sealing. External provider completeness remains unverified. |
| Rights and spend clearance | Task-level rights register and owner spend authorization are complete before allocation |
| Run gate | `benchmarks/v02_full/execution.py` loads the canonical trust policy from a protected local file, validates the frozen Optimization/Exploration pair and both signed run gates, then emits a canonical Ed25519 run-start authorization bound to both manifests, both gate-evidence digests, and the trust-policy digest. `assign_tasks_after_run_authorization` re-verifies that authorization, the protected evidence root, and both signed gates before loading task material; it checks task/rights bytes and the trusted loader's initial-tree digest against the pins, then invokes the assignment hook only after all checks pass. The metered boundary revalidates the signed pair authorization while loading only its own track manifest and gate evidence; its task projection contains one assigned task and a Merkle proof against the signed track root, not the pair plan or track task list. `benchmarks/v02_full/worker.py` prepares that one-task input for a frozen worker bundle and provides a fail-closed bubblewrap launch with cleared environment, read-only workspace/input, no network, bounded output/time, cgroup v2 process-tree memory/PID ceilings, and a private provider-broker socket. It requires a pre-delegated subtree and fails closed when the limits cannot be applied. `benchmarks/v02_full/provider_broker.py` serves one action from the host, binds it to the action ID and frozen arm config, passes all known action resource ceilings to the provider adapter, and returns the provider receipt to the worker without mounting credentials. The adapter contract requires one call, provider-side ceiling enforcement, a hard timeout, and cancellation. `MeteredDispatchBoundary.dispatch_isolated_model_worker` checks that the worker returns that exact receipt before meter verification. A real provider-specific adapter with authoritative receipt verification is still required. The trusted assignment broker still holds both tracks while preparing projections. Bubblewrap user namespaces are unavailable in the current development host, so neither arm nor scorer process was run; both fail closed there. The scorer boundary now verifies the content/mode-bound evaluator tree and pins its entrypoint and process limits. A real provider-specific adapter with authoritative receipt verification remains required before any blind run. |
| Reproducibility | A clean replay of the frozen manifest reproduces eligibility decisions, score aggregation, and artifact hashes without rerunning paid arms |

## Release boundary

Passing RPDI-14 means the protocol, harness, and evidence are auditable. It
does not mean either benchmark passed, prove BananLoop is superior, or approve
Closed Research Preview. Do not publish benchmark results until both tracks
finish and RPDI-15 approves the release evidence. Keep existing v0.1.0
benchmark registers explicitly labeled historical and ineligible for v0.2
qualification.
