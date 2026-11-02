# RPDI-01 — v2 campaign specification and v1 compatibility

**Status:** implementation PR; independent RPDI-00 review requirement was explicitly waived by the project owner on 2026-11-02 after the owner's review of PR #117. This waiver is an owner decision, not an independent review. The owner accepted the documented downstream ownership plan and instructed work to proceed. Per-RPDI review, CI, tests, and Definition of Done remain required.

## Scope

- Add strict `schema_version=2` Exploration and Optimization model variants.
- Dispatch on `schema_version` before parsing; unknown versions and v2-only fields cannot be silently dropped by the permissive v1 model.
- Compute a distinct canonical v2 approved hash. The v1 `CampaignSpec.approved_hash()` algorithm and serialized output remain unchanged.
- Bind a versioned feature-flag set into that hash. `FULL` is the default and requires every RPDI-v02 mechanism flag; `DIAGNOSTIC` supports isolated debugging and is separately hash-bound. Legacy v1 campaigns remain on their existing v1 adapter and do not masquerade as a v2 profile.
- Keep existing Engine/store/report consumers on the v1 adapter. A v1 store rejects a v2 spec explicitly; v5 persistence and v2 adapter reopen belong to RPDI-02.

## Mode rules

- `EXPLORATION` requires `evaluation=null`; it does not introduce a synthetic metric or reward.
- `OPTIMIZATION` requires an evaluator reference with protocol ID, version, and content SHA-256.
- The evaluator reference is shape-validated only. Trusted verifier approval and worker capability enforcement remain RPDI-06/11 and keep their own acceptance tests.
- V2 models are immutable Pydantic models with unknown fields forbidden. Their canonical hash includes the complete normalized v2 document.
- `feature_flags.flag_hash()` exposes a stable digest for frozen run manifests and paired benchmark evidence. Full vs diagnostic configuration changes the campaign approved hash.

## Owner-directed gate exception

The owner reviewed PR #117, accepted the three fixes and downstream deferrals in comment #6577579990, then instructed this workstream to proceed without another independent RPDI-00 reviewer. This is recorded as an explicit exception to the earlier independent-sign-off gate; it must not be described as independent approval. It does not waive CI, per-RPDI reviews, Full E2E, independent paired benchmarks, or release criteria. Benchmark and release gates remain unchanged.

## Verification required for this PR

- Historical v1 hash fixture and v1 serialization regression remain byte-for-byte stable.
- V2 dispatch, mode validation, unknown-version/field rejection, frozen evaluator shape, and canonical hash tests pass.
- Full defaults enable the complete versioned v2 feature set; diagnostic flag selection changes both the flag hash and approved spec hash.
- Legacy store/report route remains v1-only and fails closed on a v2 document.
- Linux and Windows CI pass before merge.
