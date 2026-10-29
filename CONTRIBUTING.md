# Contributing to BananLoop

BananLoop is a Research Preview. Issues, bug reproductions, benchmark fixtures, documentation improvements, and focused pull requests are welcome.

## Set up

- Python 3.11+ and Git.
- A clean, isolated checkout; do not run test campaigns inside your main project checkout.
- Install: `python -m pip install -e ".[test]"`.
- Run tests: `python -m pytest -q -o addopts=''`.
- Run static compilation: `python -m compileall -q src`.
- Run `git diff --check` before submitting a PR.

## Conventions

- Keep each PR scoped and explain its relation to the project goal.
- Add deterministic regression tests for fixes.
- For agent-related work, include a reproducible fixture with exact commit, environment, metrics, budget and logs.
- Explicitly distinguish DEV observations from validation/final holdout and from illustrative UI data.
- Do not expose secrets, proprietary benchmarks, personal data, evaluator GOLD labels, hidden answers or credentials in issues and PRs.
- Core gate, privacy, budget or resume changes require tests covering failures and retries.
- Record known limitations; do not claim unsupported general performance.
- Document new command flags and configuration schemas.

## Review & conduct

Use the GitHub pull request template. Maintainers review for correctness, reproducibility, security and evaluator integrity. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

For vulnerabilities, use private disclosure per [SECURITY.md](SECURITY.md), not a public issue.
