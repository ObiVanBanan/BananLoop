# BananLoop — project description and closed-preview copy

This file holds copy for the repository's GitHub **About**, a short announcement to invited testers, and draft release notes. Nothing here is evidence of a published version or a public release. GitHub reports repository visibility as **private** (checked 2026-10-30 22:09 UTC), consistent with the closed-preview intent. Do not invite users until collaborator access and exposed-artifact review are complete.

## GitHub About — short description

> Goal-driven experiments for coding agents. Fresh context, persistent memory, independent evaluation, measurable progress.

Suggested topics (maintainer to apply after review): `coding-agents`, `autonomous-agents`, `developer-tools`, `experimentation`, `evaluation`, `python`, `llm`.

## One-sentence pitch

> BananLoop turns a coding agent into a measured, resumable improvement loop for software repositories.

## One-minute description

BananLoop is a local, open-source autonomous improvement harness for developers using Codex and other supported coding agents. Supply a repository, an approved measurable goal and an evaluation contract. BananLoop runs bounded cycles of hypothesis proposal, implementation and independent evaluation, retaining the best eligible candidate and recording lessons from failed experiments.

**Why use it with Codex?** Direct coding agents can already reason, iterate and run tests. BananLoop adds a supervisor-owned evaluation policy, durable experimental memory, explicit budgets and reproducible accept/reject decisions around those agents. It is most useful when improvement can be measured repeatedly and independently — not for every one-off coding question.

**Research is a use case, not a product boundary.** Retrieval quality, bug reduction, performance tuning and other executable software metrics can fit the same loop.

## Closed v0.1.0 Research Preview — draft notes

**Status:** candidate only; no tag or GitHub Release is implied.

### Included

- Goal-driven, bounded experimentation and baseline/champion tracking.
- Agent proposals and implementations with supervisor-owned promotion decisions.
- DEV-only research evidence and strategies to exploit, explore or reframe.
- Independent evaluator boundaries, configurable checks, durable journal and resume.
- Codex, Pi and command-adapter integration; optional role-specific models and semantic hypothesis selection.
- CLI quick start, expert mode, and deterministic demo fixture.

### Known limits and scope

- Requires a trustworthy executable/approved evaluation contract; does not guarantee finding an improvement.
- Git workspaces are **not** a hardened sandbox for untrusted generated code.
- Model cost/token accounting can be incomplete depending on the provider; no universal hard financial cap is claimed.
- The v0.1 user interface is the existing CLI. Experimental Pi terminal UI and Codex chat development are deferred. Read-only snapshots, persisted events and typed controls remain interface-independent. A future graphical UI is a separate desktop application with no technology selected.
- Major architecture pivots are deferred to v0.2.0. Existing experimental code does not make these v0.1 acceptance criteria.
- Earlier exploratory DEV comparisons (including a reported 7:3 AutoResearchExam result) do **not** establish a cost-matched head-to-head advantage. See [benchmark register](BENCHMARK_STATUS.md).

### Closed-preview qualification

Before invitation, resolve the observed public-visibility/closed-preview discrepancy, complete the final-SHA installation/tests and provide a reproducible paired ordinary-Codex vs BananLoop benchmark with its exact protocol and limitations. The required benchmark is currently blocked by missing confirmed spend authorization and verified evaluator isolation. If a future eligible head-to-head does not demonstrate an advantage, report that outcome accurately. Do not change repository visibility as part of this documentation work.

## Suggested closed-preview invitation (after release approval and access verification)

> BananLoop v0.1.0 is available as a private Research Preview for invited collaborators. It coordinates goal-driven experiments around coding agents, with independent evaluation, research memory and reproducible campaign history. This is an experimental developer tool for trusted evaluation-ready repositories, not a promise of better results than standalone Codex. The installation guide, verified benchmark report and known limitations are linked from the repository.
