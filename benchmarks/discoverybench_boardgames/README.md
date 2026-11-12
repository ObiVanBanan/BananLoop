# Board Games 10-question diagnostic pilot

This is an operator-composed set of ten distinct, reproducible questions over the public 500-row, 42-column Board Games sample. Only `bg-01` corresponds to the upstream public DiscoveryBench question (qid 10). The other nine are explicitly pilot questions derived from available columns; this is **not** ten official DiscoveryBench tasks and none of the results is an official DiscoveryBench score. The upstream repository and answers are public, so this dataset is not sealed.

The immutable source revision, local CSV SHA-256, question text, variable names, pilot versions, and statistical criteria are recorded in `pilot_manifest.json`. Each task uses pairwise-complete rows, Spearman rank correlation with average ranks for ties, and a paired row-bootstrap 95% interval with 10,000 draws and seed `20261111`. An interval crossing zero is reported as inconclusive. The report must state that correlation does not establish causality.

For a BananLoop campaign, pass `--boardgames-task-id`. The Researcher selects a registered `experiment_id`; Core runs the selected analysis, hashes the JSON output, and records a Core generated research finding. The next iteration receives that finding and evidence ID from the durable Research Ledger. A completed analysis ID is removed from the next iteration's offered catalog and cannot be run twice in the same campaign. Five registered analyses fit the one-hour, five-iteration pilot cap.

Example one-task live smoke after the code and pilot inputs are committed:

```bash
uv run bananloop v2 explore \
  --repo "$PWD" \
  --state-dir "$HOME/bananloop-state/boardgames-pilot" \
  --request-id bg-01-smoke-v1 \
  --goal "Is the number of unique strategies developed associated with tournament win ratio?" \
  --read-path examples/exploration_diagnostic_v1/discoverybench_boardgames/TASK.md \
  --read-path examples/exploration_diagnostic_v1/discoverybench_boardgames/README.md \
  --read-path benchmarks/discoverybench_boardgames/pilot_manifest.json \
  --boardgames-task-id bg-01-strategies-tournament-win \
  --model gpt-6-luna \
  --max-agent-calls 8 \
  --max-model-tokens 2048 \
  --max-iterations 5 \
  --deadline-seconds 3600 \
  --timeout-seconds 300 \
  --approve-up-to-calls 6
```

The campaign's one-hour clock starts on the first approved run and persists across resume. Each model request is shortened to the remaining time; no request is started after expiry.

The standalone runner executes the registered statistic and emits a content-addressed JSON result:

```bash
python benchmarks/discoverybench_boardgames/runner.py \
  --task-id bg-01-strategies-tournament-win \
  --analysis-id primary_association \
  --bootstrap-resamples 10000 \
  --out /tmp/bg-01-result.json
```

The runner has been exercised against all ten task definitions. Offline v2 runtime coverage confirms the selected result is persisted and cited by the following research iteration.

Run the ten-question series sequentially after the pilot code and inputs are
committed:

```bash
python benchmarks/discoverybench_boardgames/series_runner.py \
  --repo "$PWD" \
  --series-dir "$HOME/bananloop-state/boardgames-10-pilot" \
  --run-id boardgames-10-2026-11-12 \
  --cli "uv run bananloop" \
  --model gpt-6-luna \
  --max-agent-calls 8 \
  --max-model-tokens 2048 \
  --approve-up-to-calls 6
```

It writes `series.json` plus stdout, stderr, and result JSON for each task. The
series continues after a task error. A zero exit code with a pending verifier or
exhausted call allowance is recorded as incomplete; reaching five iterations is
recorded as a limit stop, not a scientific success. Interrupted ambiguous tasks
are not automatically replayed. Per-task states, token usage, and model-call
counts remain visible in the aggregate report.

## Live pilot attempts

An earlier code revision (`7a1a57639122497286479815115514adf82244a1`) was used for
13 live campaign attempts across the ten questions. It dispatched 39 model calls
and persisted 31 registered analyses. Three campaigns reached the five-iteration
limit (`bg-01`, `bg-06`, `bg-09`); the other task groups stopped on schema,
evidence-provenance, or budget errors. This is partial execution, not ten
successful campaigns and not an official DiscoveryBench score. Reported provider
usage totals were 193,031 tokens; one zero-call failed attempt had unknown usage.

The review archive is `/tmp/bananloop-run-review-2026-11-12-updated.zip` (982,020
bytes). It contains the traces and per-attempt diagnostics. The current isolated
work copy has additional code changes that were not included in that archive, so
the archived results do not verify the current uncommitted tree. Rerun the pilot
after freezing the current code and benchmark inputs in a local commit.

## Current-revision smoke diagnosis

The first acceptance attempt on source revision `1c0614283d9420315e4553e424e8726150c5448c`
did not call a model. The Board Games runtime adds `registered_experiments` to
the researcher input, while the SIWC transport's exact input allowlist omitted
that Core-generated field and rejected the payload before opening a provider
connection. The durable worker therefore became `UNKNOWN`; it was not replayed.
The code now adds that one field only when a registered experiment executor is
configured. The affected campaign state is
`/home/sonik/bananloop-benchmark-env/runs/boardgames-one-task-smoke` and must not
be resumed; the next smoke uses a fresh request ID and state directory.

The next smoke on `68fc291d1082a55f584f43bd32a6590f2a5f5baf` completed one
model response but failed validation before executing an analysis: all three
candidate `next_experiment` values were structured JSON rather than strings.
The durable record contains the schema locations and usage (6,242 input, 925
output, 7,167 total tokens); response text was not persisted. Core now bounds
and canonicalizes structured plans as JSON text, and the prompt documents that
contract. This failed campaign is also not resumed; the next acceptance run
uses a fresh campaign state.

A fourth smoke on `10591b4` completed one Luna response (6,266 input and 1,012
output tokens) but the selected hypothesis omitted its registered `experiment_id`.
No analysis ran. A fifth fresh smoke completed two responses (13,020 input and
2,127 output tokens): Core executed and recorded `primary_association`, then the
follow-up stopped because the evidence-ranked candidate had no executable
registered ID. A sixth smoke completed four responses (27,176 input and 4,621
output tokens) and three registered analyses: primary association, outlier
sensitivity, and permutation. Each result was in Research Memory for the next
request. The fourth response failed because a redundant top-level
`next_experiment` disagreed with the selected candidate. Its completed analysis
receipts remain durable; the campaign is not resumed.

The current runtime change makes Core choose only evidence-grounded candidates
with an available registered analysis, treats the selected candidate's action
as canonical when a redundant top-level action disagrees, and removes only
pre-execution “not run” statements made stale by a measurement within the same
job. The focused two-iteration real-runner regression passes. A fresh live smoke
is required to verify this revision; prior campaigns remain immutable and are
not replayed.

On the `7157479` acceptance smoke, Luna completed three calls (19,820 input and
3,283 output tokens). Core completed `primary_association` and
`data_quality_audit`, then stopped the third response because one candidate
attached a trusted evidence ID to a different claim. The failed candidate was
not executed; no automatic replay occurred. The next Core revision now strips
and labels a malformed link `REJECTED_INVALID_CLAIM_LINK`, assigning it no
evidence weight; it still requires at least one correctly grounded executable
candidate when Research Memory exists. Regression coverage confirms the
rejected link is absent from durable evidence. The full offline suite reached
100% with no failures and two skipped tests using an isolated `--basetemp`.

A third smoke on `8c6fdbcb437b35e6df92b37cebb1c6b15e369fdc` completed one
model call (6,267 input and 1,029 output tokens) and then raised a Core
`V2RuntimeError`. The worker is `UNKNOWN`, the answer was not replayed, and
there is no durable analysis receipt, so whether the local analysis function
started is recorded as unknown. The report contains no specific runtime reason
yet. Revision `10591b4` now stores only the fixed Core error message for this
exception type, without model text, so a new clean smoke can identify the
failing invariant.
