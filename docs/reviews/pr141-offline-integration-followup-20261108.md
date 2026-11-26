# PR #141 offline integration follow-up — 2026-11-08

## Result

Focused acceptance on the code-bearing PR revision `a492d0181a36f451f67db29fccc40c9ede558c16` passed:

```text
4 passed in 2.90s
```

Command:

```bash
HOME=/tmp/bananloop-pr141-acceptance-home .venv/bin/python -m pytest -o addopts= -q \
  tests/test_rpdi13_integrated_acceptance.py::test_exploration_research_handoff_child_agrees_through_api_and_cli \
  tests/test_v2_research_runtime.py::test_campaign_pause_and_resume_survive_runtime_restart \
  tests/test_v2_research_runtime.py::test_completed_research_job_is_projected_after_runtime_restart_without_redelivery
```

The integrated test ran in both deterministic test-runner and real bubblewrap isolation modes. It exercised durable Exploration evidence, approved frozen Optimization child handoff, candidate evaluation, independent verification, and API/CLI read-model agreement. The recovery tests confirmed durable pause/resume after queue restart and projection of a completed job without redelivery.

Only local API sockets and the local evaluator sandbox were used. There were no external network calls, subscription requests, or LLM inferences.

## Scope limits

This is offline integration evidence. Researcher, optimizer, and verifier model replies in this acceptance fixture are deterministic. It does not prove a fully live Exploration-to-Optimization campaign or multiple live LLM epochs. The separately recorded SIWC Titanic smoke proves one live optimizer inference through the Optimization runtime.

The full Windows Python 3.12 CI job for this code revision was cancelled once and has been rerun; its status is tracked separately in PR #141.
