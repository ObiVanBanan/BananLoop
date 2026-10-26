# LiveCodeBench v6 Hard pilot: 60 paired tasks

## Result

The paired run completed for all 60 selected tasks. Both arms used the same public-only task set and evaluator.

| Arm | Public samples passed | Public sample pass rate | Tasks with all public samples passed | Agent calls | Campaign outcomes |
| --- | ---: | ---: | ---: | ---: | --- |
| Control | 152 / 154 | 98.70% | 58 / 60 | 128 | 58 `GOAL_MET`, 2 `STOPPED` (`PLATEAU`) |
| Fresh | 140 / 154 | 90.91% | 53 / 60 | 129 | 53 `GOAL_MET`, 7 `STOPPED` (3 `DEADLINE_REACHED`, 3 `PLATEAU`, 1 `NO_HYPOTHESIS`) |

These percentages are the aggregate fraction of the selected **public examples** passed by the final campaign champions. They are not LiveCodeBench `pass@1` scores and are not comparable to a result computed with hidden tests. By task-level public-example completion, Control passed all examples on 58/60 tasks and Fresh on 53/60. The Fresh arm also had one task with a partial public score (0.75); its other six non-full tasks scored 0.

## Benchmark slice and protocol

- Dataset: `livecodebench/code_generation_lite`, configuration `v6`, `test` split, `hard` difficulty.
- Dataset revision used by the export command: `175d0ac20500fd7aa0813f7a2e6ae7ecad3c76c9`.
- Selection: 60 records accepted by the pilot's public-only stdin/stdout filter, in dataset order. All selected records are AtCoder tasks. The run manifest reports 0 skipped records within this prepared selection.
- Public examples: 154 total across the 60 tasks, with no more than 12 examples per task exposed to the local evaluator.
- Input provenance: the prepared JSONL SHA256 is `e42f013320d2513a4c5a5e61873a06741ca3ee5aaf685b57e732bfa98d6dbba0`.
- Arms: ordinary BananLoop (`control`) and BananLoop with `--fresh-perspective-on-reframe` (`fresh`). The Fresh option is enabled in the approved campaign spec; the Control option is disabled.
- Agent: Codex for both arms. Campaign specs leave the explicit model name unset, so the effective Codex model identifier is not recorded in this report.
- Per-campaign limits: 3 experiments, 10 agent calls, 6 evaluator calls, 1,500-second deadline, plateau threshold 2. Actual aggregate agent calls were 128 and 129.
- Campaign period in recorded UTC timestamps: 2026-10-25 16:05 to 2026-10-26 13:18. Runs were sequential and resumed after interruptions.

## Limits and interpretation

The public evaluator is a pilot signal, not the official LiveCodeBench grader. It checks the selected public stdin/stdout examples and its comparison is whitespace-token based. Hidden tests were not run. No official `pass@1`, token usage, or billed cost was measured. The 60-task `v6` slice also differs from the 100-task `release_v6` GVS5H evaluation, so these scores do not establish which system performs better on a matched benchmark.

The benchmark export command pinned the dataset revision, while the generated manifest stores the JSONL hash but not the revision string. The revision above is therefore recorded from the preparation command/runner instructions, not independently reconstructed from the manifest.

## Reproduction artifacts

The local run exported 60 final predictions and per-arm accounting files with the pilot's `export` command. The predictions are intentionally not committed here; they contain generated solutions and are preserved in the isolated run directory. The aggregate values above were read from all 120 terminal campaign databases and the frozen public-example counts.

