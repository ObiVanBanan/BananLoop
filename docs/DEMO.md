# BananLoop: reproducible demo

## Option A — no external model required

The repository includes a small deterministic command-agent fixture. It creates a disposable Git project, then demonstrates:

1. baseline quality 0;
2. accepted candidate quality 1;
3. rejected candidate quality 2 because guardrail regressions=1;
4. accepted candidate quality 3 (the new champion).

Run from the BananLoop checkout (after installing \`python -m pip install -e .\`):

\`\`\`bash
python examples/fixture/create_fixture.py /tmp/bananloop-fixture

bananloop init \
  --repo /tmp/bananloop-fixture \
  --goal "Raise quality to 3 while regressions stay at zero" \
  --eval-cmd "python evaluator.py" \
  --metric quality \
  --direction maximize \
  --target 3 \
  --min-improvement 0.5 \
  --guard "regressions<=0" \
  --check tests \
  --editable "value.txt" \
  --editable "guard.txt" \
  --protect "evaluator.py" \
  --protect "agent.py" \
  --agent command \
  --agent-cmd "python agent.py" \
  --max-experiments 5 \
  --max-attempts 6 \
  --max-agent-calls 12 \
  --max-evaluator-calls 8

bananloop run --repo /tmp/bananloop-fixture
bananloop status --repo /tmp/bananloop-fixture
bananloop report --repo /tmp/bananloop-fixture
\`\`\`

The original fixture repository remains unchanged. BananLoop uses its managed workspace. See the [full CLI guide](reference/cli-and-campaigns.md) for Windows equivalents and advanced options.

## Option B — independent Luna smoke evidence

A QA agent tested the opt-in semantic selector on \`examples/calibration/mini_matcher\` on October 29, 2026. This was one fresh CPU-only campaign:

| Property | Observed |
| --- | --- |
| Model | \`gpt-6-luna\` |
| Proposer candidates | 2, both admissible |
| Selected mechanism | \`normalized_attribute_extraction\` |
| Selector evidence | \`INSUFFICIENT_EVIDENCE\` |
| Fallback | No |
| DEV quality | \`0.4375 → 1.0\` |
| Agent calls | 3 |
| Evaluator calls | 2 |
| Outcome | Accepted and promoted |

These measurements come from the [Semantic Selector QA v2](https://github.com/ObiVanBanan/BananLoop/blob/codex/llm-semantic-selector-v1/docs/calibration/SEMANTIC_SELECTOR_QA.md) artifacts, not a hidden official benchmark. The model did not prove superiority over baseline coding agents. No broad performance claims are made.

## A note on demos

An animated terminal recording is planned for the README. Do not confuse staged UI mockups or illustrative charts with measured benchmark results. A stable demo clip should be recorded from a real disposable run and captioned with its exact commit and evaluator.
