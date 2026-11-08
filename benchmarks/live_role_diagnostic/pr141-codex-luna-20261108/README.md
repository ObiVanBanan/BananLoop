# One-case GPT-6 Luna researcher diagnostic

This directory contains only public task material, pinned role metadata, the
owner's exact one-case authorization, and CLI output ledgers. It includes no
oracle, expected answer, nonce, API key, or login token.

`runs/run-002/` is the completed no-tools diagnostic. `runs/run-001/` preserves
the earlier failure with unknown settlement; do not replay that case as an
automatic retry. The model reply is not a quality score or an RPDI-14 result.

The response was produced from the PR #141 code plus local uncommitted changes
identified in `docs/reviews/pr141-codex-live-role-diagnostic-20261108.md`.
Reproduction requires an existing Codex ChatGPT login, Codex CLI, and outbound
workspace routing. The command is:

```sh
python -m bananloop.benchmark_live_codex \
  --cases benchmarks/live_role_diagnostic/pr141-codex-luna-20261108/public/cases.json \
  --registry benchmarks/live_role_diagnostic/pr141-codex-luna-20261108/public/registry.json \
  --team benchmarks/live_role_diagnostic/pr141-codex-luna-20261108/public/team.json \
  --authorization benchmarks/live_role_diagnostic/pr141-codex-luna-20261108/authorization.json \
  --out /tmp/pr141-codex-luna-rerun \
  --i-authorize-codex-exec
```

Running the command again consumes another ChatGPT subscription request. The
saved successful output is the evidence; rerunning is not required.
