# BananLoop v0.1.0 — closed-preview qualification protocol

**Status:** preparation only. The owner intent recorded on 2026-10-29 was a **closed Research Preview**, but GitHub currently reports repository visibility as **public**. This discrepancy is unresolved. No public release, visibility change, tag, distribution or invitation is authorized by this document. A completed, independently reviewable Codex Direct vs BananLoop benchmark is a required gate.

This protocol complements [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). The release owner must approve the final SHA and explicitly authorize **each** irreversible publication step. Do not create a tag, GitHub Release, or change repository visibility as part of a documentation update.

## 1. Freeze the release candidate

On a trusted machine with Git, Python 3.11+ and an authenticated supported coding agent, check out the exact intended commit.

```bash
git fetch --all --tags --prune
git switch main
git pull --ff-only
git status --porcelain
git rev-parse HEAD
git show -s --format=fuller HEAD
```

Record the **exact SHA**, OS, Python version, Git version, agent/provider version, date and any known configuration differences in the release evidence. Working tree must be clean. If a documentation commit lands after QA, either rerun QA on the new SHA or explicitly document the exact untested difference; never describe older QA as a run against the new SHA.

## 2. Install and test on the final SHA

Use a clean environment, rather than relying only on a contributor's preinstalled packages. Linux/macOS example:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m pip check
python -m compileall -q src
python -m pytest -q -o addopts=''
bananloop --help
bananloop doctor --json
```

Windows PowerShell equivalent:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m compileall -q src
.\.venv\Scripts\python -m pytest -q -o addopts=''
.\.venv\Scripts\bananloop --help
.\.venv\Scripts\bananloop doctor --json
```

Record both success and failure output. If a test environment, hosted CI runner or dependency mirror is unavailable, mark that check **NOT RUN** rather than **PASS**. Previous verified test counts do not certify the final commit.

## 3. Verify the actual CLI workflow

Run [DEMO.md](DEMO.md), **Option A**, in a fresh disposable repository. It is deterministic and does not require model credits. Capture baseline score, accepted/rejected experiments, latest champion, final status and the report path. Confirm that the project being evaluated is not modified directly.

Then perform a short **opt-in** real-agent smoke on a trusted disposable fixture only if the provider is available. Record model identity, call count, cost/usage availability, protocol, and DEV-only nature. Never use hidden benchmarks or publish unreviewed execution logs.

Release acceptance checks:
- New campaigns can be initialized and executed with a frozen evaluation contract.
- An invalid candidate cannot bypass scope/guard gates.
- An accepted candidate is recorded and resumed from durable state.
- Protected evaluator files and withheld labels stay out of the agent-visible workspace.
- Budget exhaustion, pause-for-input and interruption are represented truthfully.

## 3a. Required benchmark against ordinary iterative Codex

Follow [BENCHMARK_STATUS.md](BENCHMARK_STATUS.md) and the frozen paired protocol in [Issue #64](https://github.com/ObiVanBanan/BananLoop/issues/64), adapted to the actual approved task set. The first preview must not be approved until **a new repeat** is complete, its results have been independently checked and every arm's budget/protocol eligibility is documented. Current main lacks the PR #61 token-metering implementation, and even that opt-in design documents post-call overshoot rather than a provider-enforced spending stop. No confirmed spend budget or verified isolated evaluator is available for this task; do not run paid model calls or generated code on the host.

- Same pinned baseline, model/reasoning settings, allowed DEV data, tool permissions, evaluation and per-arm resource caps.
- Allow ordinary Codex to iterate, test and repair; count *all* BananLoop agent/model roles.
- Keep independent holdout outside agent-visible workspaces and research memory; never adapt after seeing it.
- Preserve per-task final artifacts, independent scoring, wins/ties/losses, tokens, real costs when measurable, time, failures and incomplete/ineligible runs.
- Keep the historical **reported** AutoResearchExam 7:3 DEV result separate: mismatched resources and unlinked underlying evidence make it exploratory, not a release-pass benchmark.
- A neutral or negative outcome does not automatically bar a preview; an unrepeatable, unsafe or misleading result **does** block approval.

## 4. Security, visibility and preview access audit

The repository is currently reported as **public** despite the closed-preview intent. Do not invite preview users or recommend a release until the owner resolves this discrepancy. Before any preview recommendation, audit relevant branches, history, datasets, logs and artifacts for secrets or withheld evaluation data, and rotate anything exposed. A full history-aware secrets/rights scan of *all* refs is also mandatory before any future public-opening decision.

A limited local pattern scan on 2026-10-30 checked selected private-key, AWS, GitHub, Slack and credential-assignment formats across 3,999 reachable Git object IDs and found zero matches. It did not cover untracked artifacts, refs absent from the clone, all provider formats or GitHub secret-scanning alerts; it is not a completed secrets clearance.

```bash
git fetch --all --tags --prune
git branch -a
git tag -l
git log --all --oneline --decorate -30
git ls-files
```

Use a history-aware secrets scanner (for example, Gitleaks) configured to traverse all Git refs; record tool/version, scanned refs, findings and disposition. Inspect old API tokens, `.env`, credentials, private endpoints, personal paths, synthetic vs genuine secrets, and commit/PR comments. Do **not** paste discovered secrets into reports or GitHub Issues. Revoke/rotate exposed credentials before opening; history rewriting needs separate coordination, since cached commits and PR references can survive a rewrite.

Independently review:
- Whether protected DEV/validation/HOLDOUT sources and GOLD labels were ever committed or placed in traces.
- Evaluation isolation and agent permissions; local workspace filtering is **not** an OS security sandbox.
- License/dependency notices and any rights to redistribute example datasets.
- All README statements, badges and artwork for accuracy and rights.

A screenshot concept is **illustrative**: stars/forks, performance curves and polished UI controls must not be presented as live measurements or shipped features.

## 5. Presentation verification

Check README on GitHub in **light and dark theme** and on narrow mobile widths. Verify image paths, alt text, link destinations and code block copying. The conceptual landing page is not a functioning GitHub README: GitHub does not run arbitrary page scripts or provide interactive controls inside README content.

For manually provided replacement artwork:
1. Place the final approved banner in `assets/` and update README's image path only when that exact file is committed.
2. Keep the banner legible at approximately 320px display width; avoid relying on tiny text in artwork.
3. Use a genuine ASCII or text diagram alongside the visual asset for accessible architecture description.
4. Preserve readable contrast, the friendly banana explorer, pixel-art planets and dark/gold visual vocabulary.

## 6. Release decision record

Fill in this receipt before any publish action:

| Field | Value |
| --- | --- |
| Intended release | `v0.1.0` Research Preview |
| Exact main SHA | NOT RECORDED |
| OS / Python / agent CLI | NOT RECORDED |
| Full tests / compile / pip check | NOT RUN ON FINAL SHA |
| Deterministic demo | NOT RUN ON FINAL SHA |
| Independent evaluator/privacy check | NOT VERIFIED ON FINAL SHA |
| Private-preview credential/protected-data check | NOT VERIFIED |
| Complete history-wide secret/privacy audit | NOT VERIFIED; limited selected-format scan only |
| Paired Codex Direct vs BananLoop report | NOT VERIFIED |
| Benchmark protocol/budget/holdout eligibility | NOT VERIFIED |
| README desktop/mobile review | NOT VERIFIED |
| Hosted CI state or documented substitute | NOT VERIFIED |
| Release owner approval | NOT GIVEN |
| Decision | BLOCKED UNTIL EVIDENCE IS ENTERED |

Attach exact logs or a sanitized evidence file, including negative findings and known limitations. Any required preview check marked `NOT RUN` or `NOT VERIFIED` blocks a claim of complete readiness. The current public-visibility vs closed-preview discrepancy, incomplete history audit, and blocked benchmark gate are release blockers.

## 7. Optional tag / GitHub Release — only after explicit approval

Only **after** the owner resolves the repository visibility discrepancy and explicitly approves the final tested SHA, independent H2H report and intended audience, may the owner optionally create a tag and GitHub Release:

```bash
git fetch --all --tags
git switch main
git status --porcelain
git rev-parse HEAD
git tag -a v0.1.0 <APPROVED_SHA> -m "BananLoop v0.1.0 Research Preview"
git push origin v0.1.0
```

If approved, create a GitHub Release from this exact tag titled **BananLoop v0.1.0 — Research Preview**, with actual benchmark results, [CHANGELOG.md](../CHANGELOG.md) and limitations. No external publication, PyPI claim, or visibility change is part of this work. Any future public-opening decision requires separate owner approval and a complete history audit.

## 8. Follow-up after release

- Confirm archive/install instructions against the published SHA.
- File known limitations and roadmap items without inventing benchmark wins.
- Share benchmark evidence with invited collaborators only after review, including exact evaluator, dataset split, seeds, budgets, costs, protocol deviations and limitations.
- Recheck GitHub Actions status; CI failing to start because of billing is not equivalent to passing CI.
