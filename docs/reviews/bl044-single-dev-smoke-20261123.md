# BL-041/044 single public DEV smoke · 2026-11-23

## Scope

One deterministic starter-baseline evaluation for `budgeted-imputation-mcar50` using the existing public DEV CLI. This is a smoke of the prepared public evaluator path and its pinned task/image inputs. It is not a BananLoop campaign or autonomous optimization result. No model, Harbor, public VALIDATION grader, private grader, or multi-task series was run.

The task and pins were taken from `/home/sonik/bananloop-benchmark-env/runs/bl044-safe-20261123/runtime/budgeted-imputation-mcar50.json`. The candidate was the repository's existing starter at `benchmarks/autoresearchexam/solutions/impute.py` (SHA-256 `4d5ff8734d84aa2f6c6e4fd148aad7fb54ae6f6fc13ae1bd8703c9138f5ff0ab`). Runtime descriptor SHA-256: `558569c48e02b603ccb4cb7fb4efd9043843ac04f6513553cc47da5fb7171e2d`.

## Runner check and command

The applicable existing entry point was `benchmarks.autoresearchexam.public_dev_cli score`. It accepts this task's frozen source, image name and image digest, and an explicit evaluator timeout. Before execution, `docker image inspect --format '{{.Id}}' bananloop-are-public-devkit-probe:2026-11-12` returned the descriptor's expected image ID `sha256:5d2bac54769dc124be218efc1a1495210197a25652f6005ad327d9fa5bc8f9f4`.

From the repository root, the exact bounded command was:

```sh
timeout --signal=TERM --kill-after=10s 150s .venv/bin/python -m benchmarks.autoresearchexam.public_dev_cli score \
  --task-id budgeted-imputation-mcar50 \
  --candidate benchmarks/autoresearchexam/solutions/impute.py \
  --source-task /home/sonik/bananloop-benchmark-env/sources/AutoResearchExam/budgeted-imputation-mcar50 \
  --image bananloop-are-public-devkit-probe:2026-11-12 \
  --image-id sha256:5d2bac54769dc124be218efc1a1495210197a25652f6005ad327d9fa5bc8f9f4 \
  --timeout-seconds 120
```

The shell watchdog was 150 seconds; the evaluator's own timeout was 120 seconds. Exactly one task and one public DEV scoring invocation were requested.

## Result

- Exit code: `0`.
- Result: `GRADED`, 42 public DEV cells, `dev_score=-0.0017`.
- Errors: none; stderr was empty.
- This reproduces the starter's public DEV baseline only. It is not a BananLoop score, official task score, or evidence about private/holdout performance.

## Cleanup and artifacts

The post-run Docker query for containers using the selected image returned no rows; there was no remaining smoke container to remove. The evaluator manages its temporary scoring container. The smoke artifacts are in `/home/sonik/bananloop-benchmark-env/runs/bl044-single-dev-smoke-20261123/`: captured stdout, stderr, exit code, observed image ID, input hashes, and post-run container query. No repository source files were edited for this smoke. The working tree already contained unrelated edits before the run; they were left untouched.

## Limits

This validates only the runnable public DEV scorer and pinned baseline inputs. The runtime descriptor supplied the task/image selection but the BananLoop campaign config-builder and Harbor Optimization path were not exercised. The result therefore does not validate an end-to-end BL-041/044 campaign, candidate iteration, or any grader tier beyond public DEV.
