# PR #141 live SIWC smoke and Linux acceptance

## Tested runtime revision

- Runtime code SHA: `e73c0612719e8a23604b6ca5db814ea741383351`
- Runtime tree SHA: `27c31a89d2f095c3ce0862da55f8ae4a63c9a34b`
- Pull request: [#141](https://github.com/ObiVanBanan/BananLoop/pull/141), Draft, unmerged.

The complete local source tree used for the Linux run was compared with the
PR tree: all five locally changed tracked files matched byte-for-byte, and
the remaining tracked files came from the same parent revision. The final
GitHub tree SHA is recorded above.

## One real model request

The owner-authorized smoke used BananLoop's v2 Exploration runtime and the
existing Sign in with ChatGPT (SIWC) connection. It sent exactly one inference
request to `gpt-6-luna`; there was no automatic retry and no API key.

- Result: `SIWC_ONE_SHOT_EXPLORATION_COMPLETED`
- Input/output/total usage reported by SIWC: 1,036 / 1,238 / 2,274 tokens.
- Durable research evidence: `2dbefa37c8b080fb92cf293bbc6668ae0a154c79955dbb25d651647569f9183d`.
- The response proposed a controlled 2×2 experiment and three falsifiable
  hypotheses. No experiment was run, so the evidence correctly records that
  no empirical conclusion is available.
- SQLite `PRAGMA integrity_check`: `ok`.
- Monetary cost: `UNKNOWN`; the SIWC response did not provide a billing receipt.

This was a one-step live transport/runtime smoke, not a full live multi-epoch
campaign or RPDI-14 benchmark score. The model had no evaluator, holdout,
credentials, shell, or network tools.

Machine-readable result: [acceptance JSON](pr141-live-smoke-acceptance-e73c061.json).

## Separate live Titanic Optimization

The repository also contains a completed one-call live v2 Optimization run on
the frozen Titanic seed-2026 split:

- Runtime SHA: `5104f22d00eb0adcf5105475f45b8cc88ee3c248`.
- One GPT-6 Luna optimizer inference completed; there was no retry.
- The real optimizer reply produced a candidate. Trusted DEV evaluation,
  independent integration verification, VALIDATION, and terminal trusted
  HOLDOUT all completed. The optimizer did not receive validation or HOLDOUT
  rows or labels.
- HOLDOUT accuracy was **153/196 = 0.780612**, below the requested 0.95 stretch
  target. The run is evidence that the live Optimization executor works, not
  evidence that the target was reached.
- Parent protocol approval was fixture-seeded and the independent integration
  verifier was deterministic; the whole parent Exploration-to-Optimization
  campaign was not live. Monetary cost is `UNKNOWN`.

The public result, provider receipt, and campaign report are committed in
`benchmarks/titanic_survival/results/live-siwc-optimization-20261107*.json`.

## Separate two-iteration live Exploration

A second, independent SIWC run exercised two real GPT-6 Luna Researcher jobs
through the v2 Exploration runtime:

- Both requests completed; 2 research evidence records were persisted.
- The second worker received the prior evidence reference and a 339-character
  Research Memory summary.
- Aggregate SIWC usage was 2,156 input and 2,108 output tokens (4,264 total).
- The campaign stopped at its configured two-iteration limit, as expected;
  automatic replay was false and SQLite integrity was `ok`.
- No dataset or experiment was supplied. The report therefore asserts no
  scientific conclusion; this verifies real multi-step Exploration and memory
  handoff, not Optimization or the full campaign-to-final-report loop.

The archived run contains the campaign SQLite database, published evidence,
and machine-readable summary. The aggregate verification is also captured in
the acceptance JSON below.

## Offline suite

Full Linux offline suite on the source tree matching the runtime SHA above:

```sh
PYTHONPATH=tests/plugins:tests:src:. python -m pytest \
  -p isolate_home -o addopts='' -q -ra --tb=short
```

Result on Python 3.12.15: **1,015 passed, 2 skipped, 7 subtests passed** in
66.83 seconds. The two skips are the unavailable pinned selector source commit
`cd753c97c62148c3ccd3f030251e96a4d19b23bd` and the deferred Windows
ACL/reparse-point contract. Loopback was enabled only for local API fixtures;
this run made no model-provider requests.

## CI snapshot

At the time this report was prepared, PR #141's Linux pytest jobs for Python
3.11, 3.12, and 3.13 passed; Chromium passed; and the no-provider-request jobs
passed on Linux 3.11–3.13 and Windows 3.12. The full Windows pytest job was
still running. Check the PR for its current status.

## Remaining acceptance limits

- Multi-epoch Exploration/Optimization and durable pause/resume are proven by
  the separate offline integration report in PR #135; their model replies are
  deterministic fixtures.
- This smoke does not prove multi-epoch live model quality or an independent
  Codex benchmark comparison.
- The Windows full suite and the pinned selector revision remain unresolved.

## Later local Codex CLI diagnostic

A one-case public researcher diagnostic later completed through the Codex CLI
runner using `gpt-6-luna`. It returned a schema-valid `ResearcherReplyV1`;
CLI-observed usage was 14,054 input and 482 output tokens. This is a
non-qualifying diagnostic, not an independent benchmark score. It ran from an
uncommitted worktree and has separate source and artifact hashes. See [the
Codex CLI diagnostic report](pr141-codex-live-role-diagnostic-20261108.md) and
[machine-readable result](pr141-codex-live-role-diagnostic-20261108.json).
