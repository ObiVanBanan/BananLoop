# PR #141 Codex CLI live researcher diagnostic

## Result

One public synthetic researcher case completed through the PR #141 Codex CLI
role-diagnostic runner using the existing ChatGPT login and `gpt-6-luna`.

- Runner status: `COMPLETED_DIAGNOSTIC`.
- Attempted/completed cases: 1 / 1.
- Reply: validated against the actual `ResearcherReplyV1` Pydantic schema.
- CLI-observed usage: 14,054 input and 482 output tokens. These are unsigned
  Codex CLI observations, not a provider receipt.
- Provider cost: `UNKNOWN`; there is no billing receipt.
- RPDI-14 qualification / independent benchmark score: **not established**.
- The case contains no answer key, oracle, or holdout label. It was a diagnostic
  request for falsifiable explanations of a train-to-DEV score gap; no empirical
  experiment was performed or claimed by the model.

The model proposed three hypotheses (overfitting, distribution shift, and an
evaluation mismatch), selected overfitting, proposed a training-only
cross-validation diagnostic, and stated the absent data and procedure details
as limitations.

## Reproducible artifacts

Exact public inputs and outputs are committed under
[`benchmarks/live_role_diagnostic/pr141-codex-luna-20261108/`](../../benchmarks/live_role_diagnostic/pr141-codex-luna-20261108/).

- `public/` contains the exact public case suite, role registry, and team pin;
  `authorization.json` binds the one-case call budget to their digests.
- `runs/run-002/` contains the successful status, hash-chained CLI usage ledger,
  and validated model reply.
- `runs/run-001/` preserves an earlier runner failure with unknown usage; its
  exact provider settlement is unknown.

The exact suite digest is
`d96ffed24acb815d8f5bbcaa22a5107f243085ee9d8d967d62952d117b2a34ed`;
the registry digest is
`98fdb4fbea61bc6caba7f817a0057d9b42badbc4f3dc6c3ed86dcba79c1d7a58`;
the team manifest digest is
`a5108c4302bdeff83404af74aa444da3664b916e35e0b4c2c63e28135ced6ea8`.

## Runner fix and verification

The first real CLI attempt exposed two host integration issues. The Codex
in-process app server could not initialize under the read-only default
`CODEX_HOME`; after moving transient CLI state into a private temporary home,
an unprivileged probe reached the CLI but failed workspace routing because the
execution sandbox denied outbound network access. The successful run used the
same temporary writable Codex home, with the existing `auth.json` linked into
it (not copied or read by the runner), and an explicitly network-enabled
execution context. No OpenAI API key was used.

`run_codex_case` now creates a private temporary work directory, uses temporary
`CODEX_HOME` and XDG state directories, and symlinks the existing login file so
the CLI can initialize without writing into a read-only home. BananLoop still
starts one Codex process per case and does not retry a failed or ambiguous
result. The runner test file passes: **14 passed**.

The code ran from worktree HEAD `a7e93aae719bf3a7c5fbeb5f9c53380357c47063`
plus local changes now recorded in the PR. SHA-256 of the updated runner source:
`a4ed414a3ff9706accc3f7ae93eb63dda4bd60426d5ccad7447be973c1556b26`.
The diagnostic code and evidence were subsequently added to the Draft PR #141;
its exact commit SHA is recorded in the pull request.

## Remaining limits

This confirms one real LLM role reply through the diagnostic runner. It does
not execute the durable v2 campaign worker, tool gateway, evaluator, multiple
optimization epochs, pause/resume, or an independent sealed benchmark. Earlier
failed attempts had no token usage receipt; their provider settlement remains
unknown. Do not use this single case as a quality score.
