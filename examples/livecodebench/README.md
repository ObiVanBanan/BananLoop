# LiveCodeBench paired BananLoop pilot

This opt-in fixture compares BananLoop's standard proposer with the
GVS5H-inspired fresh-context proposer from PR #62. **It is NOT an official
LiveCodeBench score and does not run hidden tests.** During a campaign it uses
public stdin/stdout samples as a DEV proxy and exports the terminal champions
for later grading by the official LiveCodeBench checker.

## Requirements and data handling

- Python 3.11–3.13, Git, installed BananLoop test extra, Pi or Codex.
- **A disposable restricted VM/container with no host credentials, sensitive
  mounts or production network access.** Executing generated Python is unsafe
  outside a sandbox. BananLoop's Git isolation is not an OS security sandbox.
- A **public-only** JSONL export of a pinned LiveCodeBench release. Allowed
  fields: question_id, question_title, question_content, platform, contest_id,
  contest_date, starter_code, difficulty, public_test_cases, metadata.
  Import refuses unknown/private fields, and skips function-call cases.
  Only stdin/stdout cases with public cases containing input/output strings
  are supported. Never copy private_test_cases or hidden answers to the pilot.
- Official LCB dataset is multi-GB. Download/prepare in a separate environment,
  and transfer **only sanitized public fields** to the experiment machine.

## Windows PowerShell

From the experimental checkout including PR #62:

```powershell
uv sync --extra test
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:OMP_NUM_THREADS = "2"
$env:OPENBLAS_NUM_THREADS = "2"

python examples/livecodebench/pilot.py prepare --public-jsonl C:\benchdata\lcb-v6-public-only.jsonl --work-dir .tmp-lcb-pilot --limit 10

python examples/livecodebench/pilot.py run --work-dir .tmp-lcb-pilot --arm both --agent pi --provider ollama-cloud --model YOUR_MODEL --max-experiments 3 --max-agent-calls 10 --deadline-seconds 1500 --plateau 2

python examples/livecodebench/pilot.py export --work-dir .tmp-lcb-pilot --arm control
python examples/livecodebench/pilot.py export --work-dir .tmp-lcb-pilot --arm fresh
```

Alternatively, use ``--agent codex --model YOUR_MODEL`` (omit provider).
Use ``--arm control`` and ``--arm fresh`` separately to run one arm.
The runner executes tasks sequentially and refuses to overwrite state.
Start with 3–10 tasks on limited CPU, then extend to 20+ when stable.

The manifest saves a SHA256 of the public input and frozen source commit.
Both arms share the same baseline Git repository and evaluator, but use
separate BananLoop state dirs. Each public evaluation runs at most twelve
examples (5s each). Whitespace token equality is merely a DEV proxy;
floating-point tolerance and custom constructive validators are unsupported.

Exported predictions use the upstream documented JSON form:
``[{"question_id":"...","code_list":["..."]}]``. The matching
``accounting_<arm>.json`` records campaign-level agent and evaluator calls.
Exact billed tokens/costs are **not** measured here.

## External official scoring

Keep the official judge and private tests outside the agent Git history,
agent workspaces, tools, logs and adaptive research state. Grade final
predictions **only once per arm**, after optimization has terminated. Never
send hidden feedback into further campaigns.

Pin the official checker revision and release, and verify partial-subset
support of its custom evaluator before passing a 10-item export. Some checker
versions expect the complete ordered release, so use full aligned output or
a pinned official per-task route when required. A public proxy is **not**
official pass@1, and this pilot does not automatically grade hidden tests.

Record exact case IDs, model/provider, settings, evaluator revision, resource
limits, wall time, calls, and paired per-task hidden correctness. Repeat on
fixed IDs with matched budgets; never compare this 10-item convenience sample
directly to GVS5H's five-pass 100-task study.

## Tests

```powershell
uv run pytest -q tests/test_lcb_pilot.py
uv run pytest -q
```

For the GVS5H mechanism itself, confirm at least one run actually reaches
DEV-only reframe and records ``exp:<id>:propose-fresh``. Existing unit tests
alone do not establish a quality improvement.
