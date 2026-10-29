# BananLoop V3 — weak-agent calibration runbook

## Role
You are a constrained QA executor, **not** a code developer or hypothesis author.
Your task is to pull the calibration branch, run small existing checks and one
small DEV campaign, preserve all evidence, and report exact observations.
Do not “fix” source code or the evaluator, alter benchmark scope, change rewards,
or claim tests passed without evidence. Do not run AutoResearchExam yet.

## 0. Branch and isolation
- Repository: https://github.com/ObiVanBanan/BananLoop
- Branch: `codex/calibration-task-context-champion-v1`
- Base: `codex/are-portfolio-repair-v2` (PR #66)
- Confirm `git status --porcelain` is empty; do not discard local changes.
- Use short Windows paths such as `C:\bl-cal\` for generated repos and state.
- Keep the original repository unmodified. No PRs, commits, pushes or merges.
- Do not introduce hard model-token budgets. Record usage when available.

## 1. Quick regressions
Run:
```powershell
python -m pytest tests/test_research.py tests/test_engine.py -q
python -m pytest -q
```
If the full suite fails because of Windows paths, save the traceback and rerun
the failing test in a short local checkout. Do not mark the failure as passed.

Expected checks:
- Unknown-cause DEV capsules are not actionable causal signals.
- Three partial DEV gains without new champion cause research reframe.
- Real champion acceptance resets champion stagnation.
- Missing/hidden/oversized required TASK.md produces safe behavior.
- The original portfolio repair remains protected and idempotent.

## 2. Deterministic runner test
Use the existing `examples/fixture/create_fixture.py` and the documented
`bananloop init` command from README.md around lines 100–138.
Create the fixture at `C:\bl-cal\fixture` with a separate short state path.
Use `--agent command --agent-cmd "python agent.py"`.
Expected: ACCEPT -> REJECT by guard -> ACCEPT with GOAL_MET.
Report any deviation with the exact event and traceback.

## 3. Small real-model smoke (optional if Codex credentials available)
Follow `examples/calibration/mini_matcher/README.md` exactly.
This is a new 16-case, CPU-only DEV fixture with fixed evaluator and root TASK.md.
Use GPT-6 Luna and exactly 3 max experiments. Do not cap tokens.
Save `C:\bl-cal\matcher-report.json`, run logs and campaign SQLite.

Before accepting the result, inspect the first proposal action request:
`task_document.status` must be `available`, and the embedded `sha256` must
equal the SHA-256 of the public root TASK.md. The model must not see evaluator.py.

## 4. Report format
Return **one compact factual report** with:
- branch and exact SHA, OS, Python version, Codex CLI version;
- commands, exit codes, pytest passed/failed/skipped counts;
- deterministic fixture campaign status, stages, champion, errors;
- mini matcher baseline/best reward, accepted/rejected experiments, proposer
  and implement calls, total input/output/cached tokens where known;
- per-experiment hypothesis mechanism_key, accepted, DEV delta,
  research_state.champion_stagnation_streak and planning_mode;
- whether TASK.md was embedded, and whether evaluator remained inaccessible;
- full raw log/SQLite paths, including unknown/missing usage;
- blockers only, each with reproducible minimal steps.

Stop on missing required task context, leak of evaluator/holdout data, corrupt
campaign DB or a failed evaluator-integrity check. Never silently repair the
score, data, or frozen protocol. Do not interpret one smoke result as proof that
BananLoop beats Codex.

## Next (only after the operator approves)
Repeat 2–3 short calibration runs with separate fresh states and documented
seeds. Later, run BananLoop and plain Codex **concurrently or sequentially on
the same frozen environment** with identical input data and evaluator limits.
Compare quality, token usage and time, but do not artificially truncate calls.
