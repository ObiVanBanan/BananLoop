# Live E2E: real runtime acceptance and BL-049 triage

This is **not** the fake 24-arm series, offline acceptance, or official RPDI-14 scoring.
Every dispatched model role uses the production ChatGPT-subscription path, and the
public ARE DEV evaluator/Harbor path is real. No scripted model answers, forced
verifier outcomes, injected scores, retries, or automatic launches of subsequent cases.

## Cases (one invocation = one independent arm)

| Case | Default frozen ARE task | Required observation |
| --- | --- | --- |
| full | shortest-valid-ci-l2-ece | Real researcher, protocol verifier, optimizer and integration verifier receipts; auto-pivot; completed child with verified DEV comparison; archived verified Champion and clean Harbor inventory. |
| multi | budgeted-imputation-mcar50 | Same as full, plus >=3 completed optimizer calls and >=3 durable integration decisions. Improvement is reported separately, not implied by execution. |
| rejection | carps-star-discrepancy-subset-select | Observe a naturally rejected candidate, then inspect whether the next optimizer input uses the rejection evidence and a genuinely different hypothesis; verify actual next evaluation. No forced rejection. |
| verifier | budgeted-imputation-mcar50 | Observe a natural unknown integration-verifier job; locate provider call/validation/persistence failure; verify no duplicate dispatch, preserve prior Champion and unknown usage. No automatic replay. |

The last two return OBSERVED_REVIEW_REQUIRED or NOT_OBSERVED (nonzero exit).
They cannot pass merely because the arm completed. Inspect actual traces before
claiming reflection or recovery. A successful arm without a failure does not
exercise failure recovery. A timeout is not a full pass, even if a useful
candidate was saved. No synthetic evidence counts as live qualification.

Run other pinned tasks with --task, e.g. sketched-newton-cov-estimator.
The default configured model is gpt-6-luna; record the actual provider receipts
and pinned role registry to confirm the model used. The reasoning argument is a
manifest label, not a new override of native role routing.

## Linux / existing ARE environment

Use a **clean** checkout, the installed Harbor venv with BananLoop importable,
working Docker, validated DEV-only runtime descriptors and ChatGPT Codex login.
Unset OPENAI_API_KEY. Do not load replay sitecustomize or fake providers.
This is real quota consumption; the explicit --approve-live applies to one run.
The backend uses the existing time-only ARE policy (3600 s and technical
ceilings of 1000 agent/evaluator calls and 100000 model tokens; actual
subscription consumption is not a guaranteed provider-side cap).

    export ENV_ROOT="$HOME/bananloop-benchmark-env"
    export PY="$ENV_ROOT/harbor-venv/bin/python"
    export HARBOR="$ENV_ROOT/harbor-venv/bin/harbor"
    # Set this to your currently validated local runtime directory.
    export RUNTIME="$ENV_ROOT/runs/bl044-safe-20261123/runtime"
    export OUT="$ENV_ROOT/runs/live-e2e-$(date +%Y%m%d-%H%M%S)"
    env -u OPENAI_API_KEY PYTHONPATH=src:. "$PY" -m benchmarks.autonomous_v01.live_e2e run \
      --case full --model gpt-6-luna --runtime-root "$RUNTIME" \
      --harbor "$HARBOR" --out "$OUT" --approve-live

For BL-049 start with --case multi or --case verifier and a new OUT.
Default deadline is 3600 s; --seconds may shorten a diagnostic but that is not
the one-hour comparative benchmark. Each invocation has a fresh manifest,
bound preflight, Git snapshot, isolated workspace and state. No hidden or paid
evaluation fallback is introduced. Existing preflight checks all 12 descriptors
and Docker images, so an unrelated bad pin can block even one selected arm.
If blocked, read preflight-diagnostics.json, repair the real descriptor/image,
and use a new OUT. Never bypass the gate or edit a receipt to pass.

## Inspect while running or after a failure (zero model calls)

    PYTHONPATH=src:. "$PY" -m benchmarks.autonomous_v01.live_e2e diagnose --out "$OUT"

For historical artifacts use --arm-root /path/to/arm and --state-root /path/to/state
with --out pointing to the historical run. If that run lacks our selection or
manifest files, the model/source identity remains unknown. Inspecting does not
resume the run, mutate its DB, call a model, evaluate a candidate, or clean Docker.

The report includes terminal and current stage, typed exploration/optimization
failures, bridge receipt, last worker event for unresolved jobs, job ID, action
ID, role, campaign ID, provider request IDs/usage, DEV integration decisions,
artifact integrity and log locations. SQLite is read in read-only mode with WAL
visibility. Incomplete or locked state is reported; absence of evidence is not
success. No raw request prompts or model responses are copied into the summary.
Keep original archives local; review diagnostics for sensitive content before
sharing.

The final live-e2e-report.json is written after the launcher returns (also on
setup/launch exceptions where possible). A SIGKILL may prevent that final write;
use diagnose to read the remaining state instead. If a failure site is unknown,
inspect the exact worker/Harbor logs indicated in the report. The native
runtime may still omit a useful failure reason; this wrapper does not repair
that or inject faults.

Exit 0 = full/multi acceptance checks passed; 1 = scenario not proven;
2 = setup or preflight failure. The evidence report is not an independent
attestation against tampering. It assumes the clean production environment.

## What must remain open

- Live semantic reflection, memory consumption, natural stall/reframe and
  supervisor correction require trace-level assertions and separate live
  qualification; the supervisor branch is not integrated into this base.
- BL-049 must not close on offline pass or a partial archive. Use the actual
  run evidence and inspect UNKNOWN dispatch history and Champion preservation.
- Public DEV results are diagnostic, not sealed HOLDOUT or comparative scores.

Implementation base: codex/bl049-verifier-recovery at 290ef9a4.
