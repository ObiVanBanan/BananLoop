# BananLoop v0.1.0 — publication protocol

**Status:** preparation only. A checked box or a GitHub commit is not a published release.

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

## 4. Security and public-opening audit

**Do not treat GitHub's current-file view as a secret-history scan.** Before changing visibility to public, audit all local branches, remote refs, tags, commit history, release artifacts, PR attachments, GitHub Actions logs and historical sample datasets.

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
| Historical secret scan | NOT VERIFIED |
| README desktop/mobile review | NOT VERIFIED |
| Hosted CI state or documented substitute | NOT VERIFIED |
| Release owner approval | NOT GIVEN |
| Decision | BLOCKED UNTIL EVIDENCE IS ENTERED |

Attach exact logs or a sanitized evidence file, including negative findings and known limitations. Each `NOT RUN` or `NOT VERIFIED` row blocks a claim of complete release readiness; a maintainer may explicitly accept a documented preview risk, but it cannot be silently turned into green.

## 7. Publish — only after explicit approval

After the owner approves an audited SHA:

```bash
git fetch --all --tags
git switch main
git status --porcelain
git rev-parse HEAD
git tag -a v0.1.0 <APPROVED_SHA> -m "BananLoop v0.1.0 Research Preview"
git push origin v0.1.0
```

Then create a GitHub Release from this exact tag titled **BananLoop v0.1.0 — Research Preview** using [CHANGELOG.md](../CHANGELOG.md) and explicit limitations. Do not imply a PyPI release without publishing and checking the package. Changing repository visibility to public is a separate, deliberate owner action after the historical security audit.

## 8. Follow-up after release

- Confirm archive/install instructions against the published SHA.
- File known limitations and roadmap items without inventing benchmark wins.
- Publish benchmark evidence only with exact evaluator, dataset split, seeds, budgets and agent comparison protocol.
- Recheck GitHub Actions status; CI failing to start because of billing is not equivalent to passing CI.
