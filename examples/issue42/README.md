# Issue #42 offline dogfood

Run the real BananLoop CLI against a freshly generated local Git fixture. It uses a Python command agent and deterministic CPU-only evaluator; it does not call a hosted model or rag_tender.

From the repository root:

```powershell
python examples/issue42/run_dogfood.py --work-dir .tmp-issue42-cli
```

The directory must be new or empty. The command prints the fixture path, state path, status, call counts, selected candidate, and report check. Inspect the durable operator report with:

```powershell
python -m bananloop.cli report --repo .tmp-issue42-cli/project --state-dir .tmp-issue42-cli/state
python -m bananloop.cli status --repo .tmp-issue42-cli/project --state-dir .tmp-issue42-cli/state --json
```

Expected terminal status is `GOAL_MET`, with `z-direct-fix` selected from two considered candidates, one scientific trial, two agent calls (proposal and implementation), and two evaluator calls (baseline and candidate). The agent/evaluator use standard local Python only.

