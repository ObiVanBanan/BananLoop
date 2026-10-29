# BananLoop calibration: fast matcher

This fixture is **not** an official AutoResearchExam task. It is a tiny
deterministic CPU-only smoke test for hypothesis formation, safe task-context
delivery, implementation and independent DEV grading.

- `TASK.md`: public source requirement, embedded in proposer/implementer context
- `matcher.py`: the only editable candidate file, initially returns `None`
- `evaluator.py`: private fixed DEV evaluator, keep inaccessible to agents

Run from Windows PowerShell with the BananLoop CLI installed. Use a **short path**
to avoid Windows MAX_PATH errors. The following commands assume the current
directory is the BananLoop checkout:

```powershell
$BananLoopRepo = (Get-Location).Path
New-Item -ItemType Directory -Force C:\bl-cal | Out-Null
Copy-Item examples/calibration/mini_matcher C:\bl-cal\matcher -Recurse
Set-Location C:\bl-cal\matcher
git init
git config user.name "Calibration"
git config user.email "calibration@localhost"
git add .
git commit -m "mini matcher baseline"
Set-Location $BananLoopRepo

bananloop init --repo C:\bl-cal\matcher --state-dir C:\bl-cal\matcher-state `
  --goal "Read TASK.md and improve analog matching on DEV" `
  --eval-cmd "python evaluator.py" --metric quality --direction maximize `
  --target 0.99 --min-improvement 0.02 --check cases_valid `
  --editable matcher.py --protect evaluator.py --agent-hide evaluator.py `
  --agent codex --model gpt-6-luna `
  --max-experiments 3 --max-attempts 5 --max-agent-calls 10 `
  --max-evaluator-calls 6 --deadline-seconds 21600 --plateau 3

bananloop run --repo C:\bl-cal\matcher --state-dir C:\bl-cal\matcher-state
bananloop report --repo C:\bl-cal\matcher --state-dir C:\bl-cal\matcher-state `
  --json --output C:\bl-cal\matcher-report.json
```

The script restores the original BananLoop checkout from `$BananLoopRepo`.
You can instead run the CLI from any directory if it is installed on PATH.

**Do not set token ceilings.** Experiment and agent-call counts bound the
number of attempts only; collected usage is an observation, not a stop condition.
No GPU, Ollama, Qdrant or external database is needed by the evaluator.
Check that the proposer action request in the campaign SQLite DB includes
`task_document.status="available"` and that its SHA matches `TASK.md`.
If not, stop and report the problem; do not run a full benchmark.
