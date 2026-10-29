# DEV implementation smoke

Each candidate used a separate copy of the same baseline. The implementer received only `TASK.md`, `solution.py`, and one frozen hypothesis; `evaluator.py` stayed outside its workspace.

| Hypothesis | Mechanism | DEV reward | Calls | Tokens | Time (s) | Error |
|---|---|---:|---:|---:|---:|---|
| synonym-canonicalization | synonym-canonicalization | 0.5 | 1 | unavailable | 26.8 |  |
| ordered-composition | ordered-composition | 0.5 | 1 | unavailable | 16.0 |  |
| negation-polarity | negation-polarity | 0.625 | 1 | unavailable | 24.1 |  |

The evaluator has 8 cases, so accuracy changes in steps of 0.125. These results are a smoke check, not reliable generalization estimates. No candidate was selected or revised after seeing DEV results.
