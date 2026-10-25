# Fresh-perspective proposer (GVS5H-inspired, opt-in)

This experiment borrows the **fresh-context / independent attempt** idea from
GVS5H. It does **not** import their scheduler, pretend that agents are parallel,
or replace BananLoop's evaluator, DEV/VALIDATION/HOLDOUT separation or budget policy.

With `--fresh-perspective-on-reframe`, after the normal proposer has finished
and the DEV-only planning mode reaches `reframe`, the supervisor reserves
`exp:<id>:propose-fresh` as a separate agent call. The additional proposer sees
the approved goal, code scope and measurement contract, but not failed experiment
history, hypotheses, DEV failure capsules or prior analysis notes. It can inspect
the same sanitized repository without making any edits. The supervisor adds at most
one alternative mechanism to the candidate portfolio (four candidates maximum).
A candidate proven to originate from the fresh call is exempt **only** from
the old DEV failure-signal citation requirement; it cannot cite signals it
was never shown. All other candidates and all downstream checks retain the
existing deterministic scope, evidence-reference, redundancy, budget and DEV
gates. The fresh call is checkpointed as a normal action;
a completed result is reused on resume. When fewer than two unreserved agent
calls remain (fresh proposal + implementation), the extra proposal is skipped.
Feature-off behavior is unchanged.

## Running an A/B campaign

Both arms should use an identical frozen evaluator, base commit, model/provider
and the same predeclared budgets. Run into **separate state directories**.
Example, after creating a trustworthy target repository and evaluation contract:

```powershell
# Control, using your established init arguments:
bananloop init --repo . --state-dir .bl-control YOUR_EXISTING_INIT_ARGUMENTS
bananloop run --repo . --state-dir .bl-control

# Treatment: the only additional flag is opt-in fresh perspective.
bananloop init --repo . --state-dir .bl-fresh --fresh-perspective-on-reframe YOUR_EXISTING_INIT_ARGUMENTS
bananloop run --repo . --state-dir .bl-fresh
```

The same flag is available in `bananloop start` or `bananloop goal`; use
`--state-dir` with `start`, or a distinct `--request-id` for `goal`, to
avoid reusing an existing campaign.

Compare DEV scientific trials, validated champion metric, total agent calls,
wall time, rejected/repeated mechanisms and plateau behavior. Read the durable
`exp:<id>:propose-fresh` action and `PORTFOLIO_DECISION` events to verify the
extra call and candidate selection. If plateau is never reached, the two
campaigns should behave identically.

**Warning:** the extra call spends an agent action. The treatment is not a
free win, and is expected to be skipped near budget exhaustion. Isolation is
context-level, not a security boundary against hostile agents.

## LiveCodeBench compatibility

BananLoop is a *repository optimizer*, not a direct inference-time coding
benchmark runner. It cannot directly report GVS5H's LCB pass@1 from an ordinary
campaign. A fair coding-task comparison needs an adapter that materializes one
LCB problem as a temporary Git repository with editable `solution.py` and a
**read-only core-owned** evaluator. Public samples may be supplied to the
implementer. Hidden tests and expected outputs must be held outside agent
workspaces, Git history, generated logs and all adaptive DEV data.

Start with a fixed, preselected 10-20 problem pilot under CPU-only constraints
(`--parallel 1`, 2 threads, explicit runtime timeout). Run multiple independent
campaigns for each problem, using matched budgets; score final submissions
with separately held out hidden tests, not with public samples. Collect paired
per-problem success/failure and token/call costs, and compare to one-shot model
and GVS5H under matched model/provider, output budget and test set. Never feed
hidden-test failures back into further hypothesis proposals.

This repository patch supplies the fresh-proposal lane, **not** the
LiveCodeBench-to-BananLoop adapter or a reproduced LCB score.
