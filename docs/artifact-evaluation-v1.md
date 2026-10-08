# Domain-neutral artifact evaluation v1

## Integration base

Reviewed on 2026-09-25:

- main: `cb7e621ab9e908ecaed37e059234fb94bc716731`
- PR #27: `0a24b11a9ce9010299abb98a18185eb3ef166908` (DEV / VALIDATION / FINAL HOLDOUT)
- PR #29: `3608a4cd4b0c92907dd938669e72237f4a4e2294` (Experimentation Intelligence)
- PR #30: `0a3b26ceef751530db13b5048d954fa3bc55c13a` (DEV failure reasoning)

PR30 is 70 commits ahead of main, zero behind; it includes PR27 and PR29.
The integration preserves this stack. No rewrite of its stages, research model,
analyzer roles or adapters. The merge commit records main and PR30 as parents.

## Optional evaluator extension

The existing simple JSON contract, including `artifacts: ["legacy reference"]`,
continues to work without changes. New evaluators can additionally return:

```json
{
  "status": "ok",
  "metrics": {"quality": 0.7},
  "protocol_sha256": "<SHA-256 of frozen inputs/settings/scorer/runtime>",
  "cases": [
    {"case_id": "case-a", "repetition": 0,
     "input_sha256": "<SHA-256 of input/reference/randomness/fixed settings>",
     "metrics": {"quality": 0.7, "errors": 0}}
  ],
  "artifact_manifest": [
    {"case_id": "case-a", "repetition": 0,
     "path": "result.bin", "sha256": "<SHA-256 of file bytes>"}
  ],
  "resource_units": 1
}
```

The evaluator owns cases, randomness, aggregate calculations, units and scoring.
Core checks unique case/repetition keys, complete configured sets, finite values,
matching protocol/input identities and the deterministic paired policy. It has no
domain imports or domain dispatch. Set identity mismatch rejects a candidate.

For fixed-input campaigns, bootstrap may provide a cheap `protocol_manifest_commands`
entry for each configured tier. Each command emits a `protocol_sha256` and the full
case identity list (`case_id`, `repetition`, `input_sha256`) without scoring. Core
validates and stores each tier manifest in the approved campaign spec, then passes
the private manifest file path and digest to that tier's evaluator as
`BANANLOOP_PROTOCOL_MANIFEST` and `BANANLOOP_PROTOCOL_MANIFEST_SHA256`. Readiness
may score a subset, but every returned smoke identity must belong to the manifest;
full DEV, VALIDATION, and HOLDOUT results must match their tier manifest exactly.
The manifest contains identities only, not labels or expected outputs. The
implementation worker receives neither the manifest nor per-case identity rows.

Fixed protocol identity is independent of `paired_comparison`. Core enforces the
frozen case set and input fingerprints even when paired statistical acceptance is
disabled. Paired acceptance is an optional policy for matched case-level outcome
comparisons; merely fixing inputs does not enable it.

`--paired-policy policy.json` selects optional paired acceptance. A policy uses
`case_count`, `repetitions`, `metric`, `direction`, `min_wins`,
`worst_case_tolerance`, optional per-measurement `guardrails` and
`non_regression`. Global primary improvement, checks, absolute guardrails and
champion-relative non-regression remain conjunctive requirements.

For seven cases with two repetitions: compare each candidate measurement with
the champion at the same input fingerprint. Count positive changes in the mean
of each case's two repetitions, require at least four winning cases, and require
that no individual repetition falls beyond the configured tolerance. A single
lucky seed cannot satisfy majority wins. No p-values or population claims.

## Artifacts and durable work

Each evaluator process receives these environment variables (never set in the
agent process environment):

- `BANANLOOP_ARTIFACT_DIR`: persistent action-specific directory outside workspaces
- `BANANLOOP_EVALUATION_ID`: stable action identifier
- `BANANLOOP_SNAPSHOT`: candidate commit
- `BANANLOOP_EVALUATION_TIER`: dev / validation / holdout
- `BANANLOOP_EVALUATION_UNIT_LIMIT`: approved per-call upper bound, when configured

New manifest files must remain inside that artifact directory. Core verifies
file bytes and SHA-256, rejects escape paths, symlinks escaping the directory,
duplicate paths and unknown case/repetition links. It binds experiment id,
candidate snapshot and associated case measurements itself. SQLite stores only
metadata/references; the files stay on disk. Completed action replay also checks
file existence/hashes and fails closed on corruption; it does not regenerate.
Back up the entire state directory, not SQLite alone. Legacy free-form artifact
strings are not automatically retained or verified.

`--max-evaluation-units N --evaluation-units-per-call M` are used together.
Core reserves M units before each invocation or explicitly requested retry.
Reservations reuse the existing SQLite ledger, so no new schema migration is
needed. Failed, interrupted and unknown work is never refunded. Completed action
replay reserves nothing. Reports distinguish reserved upper bounds and units
reported by successful invocations. Unreported failed work can still have been
consumed; it remains covered by the reservation.

Before a new hypothesis, both call and work budgets must cover DEV, possible
VALIDATION, and the reserved FINAL HOLDOUT. A retry cannot spend the final work
reservation. Over-limit or missing unit reports invalidate an evaluation.
An evaluator MUST honor the grant before every expensive operation and must
include any internal retry in it. Core cannot physically prevent an untrusted
external process/provider from exceeding a grant; this is a trusted evaluator
protocol, not an OS or provider quota. GPU seconds remain observational metrics;
use predictable operations for the approved unit.

An evaluator should atomically checkpoint individual measurements and artifacts,
with a durable intent before external work. Unknown outcomes require explicit
reconciliation; there is no exactly-once guarantee for a remote service without
its own idempotency API. The core reuses completed action responses after a crash.

## Information boundary

`agent_evaluation_view` now constructs a positive allowlist. Per-case rows,
protocol identities, manifests, resource reports and unknown future fields never
enter proposer/implementer inputs. Explicitly approved numeric DEV diagnostics
continue to work. Existing DEV analyzer signals/examples and generalized capsules
remain intact. VALIDATION and HOLDOUT payloads have no path into research state.

Bootstrap may explicitly expose a discovered resource whose path clearly
identifies it as training data through `agent_visible_training_paths`. Core
rejects paths that are not discovered training resources or whose names identify
evaluation, test, validation, holdout, GOLD, or reference data. These files remain
protected and hash-checked; the implementation worker can read them but cannot
change them. Evaluation resources remain hidden.

The operator report/status includes all tiers, per-case measurements, file hashes,
aggregates, decisions/predicates and resource totals. Do not expose these reports
or the state directory to an adaptive agent. This change preserves the existing
sanitized workspace boundary; it does not add a hostile-code sandbox. A process
running as the same OS user can potentially read external files. For adversarial
agents use OS/container isolation and restrict access to the evaluator service.

## Backward compatibility

- Old evaluator JSON and legacy artifact strings parse unchanged.
- Paired comparison and work budgets are opt-in; old calls preserve policy.
- Unused fields are excluded from approved-spec hashing, including new budget
  keys; old approved campaign hashes remain stable.
- Existing staged SQLite migrations stay additive. New work uses reservations.
- Existing campaigns are not silently upgraded to different cases/policies.
  Start a new state directory when approving a new protocol, environment or budget.
- Keep the operator's full report separate from adaptive research state.

## Proof and commands

`python -m pytest tests -q` runs old contract/staged/research/analyzer tests plus
binary-artifact campaigns. Coverage includes ACCEPT → REJECT → ACCEPT,
seven cases × two repetitions, repeatable baseline, durable replay, retained hash
verification, validation rejection without champion promotion, hidden diagnostics,
unknown-work accounting, final reservation, malformed sets and per-case guardrails.

See `docs/dogfood-proof.json` for an operator report from a real engine run against
the companion adapter in deterministic fixture mode. It is **not GPU evidence**.
Paths are normalized to `$STATE`; snapshot IDs identify local fixture commits.
The companion project contains runnable preparation and one-cycle instructions.

Limitations: two seeds and seven cases demonstrate protocol correctness, not
statistical generalization. Aggregate values are trusted evaluator calculations;
core validates their declared contract, not domain-specific arithmetic. Local
file artifacts are supported; fetching/verifying remote object-store references
is left to an evaluator that materializes files in its assigned directory.
