# AutoResearchExam v1.1 Luna pilot preflight

This directory records the Issue #64 pilot contract and its current eligibility
gates. The manifest pins the upstream commit and instruction blobs, states the
requested six-arm protocol and limits, and leaves unresolved dataset, case,
scorer, seed, billing, and sandbox evidence explicitly unset.

Run the offline report from PowerShell:

```powershell
python examples/autoresearchexam/pilot.py preflight --config examples/autoresearchexam/manifest.json
```

Exit code `2` and decision `BLOCKED / INELIGIBLE` mean one or more recorded
gates are not `PASS` with evidence. The current manifest is expected to return
that result. The preflight reporter reads only the local JSON file; it does not
download task data, inspect account credentials, execute candidate/evaluator
code, contact a model provider, start paid arms, or score HOLDOUT.

There is deliberately no `run`, `freeze`, or `score` command in this blocked
adapter. The manifest is an evidence register, not an OS security boundary.
Changing a gate to `PASS` requires independent supporting evidence and review;
the reporter alone cannot establish rights, sandbox safety, or billed USD cost.
Do not copy official `hidden_data`, sealed evaluation pools, grader state, or
private labels into any agent-visible workspace.

Run its tests with:

```powershell
python -m pytest -q tests/test_are_pilot.py
```
