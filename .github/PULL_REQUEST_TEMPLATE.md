## Summary

What changed and why?

## Related issue

Closes #

## Verification

- [ ] `python -m compileall -q src`
- [ ] `python -m pytest -q -o addopts=''`
- [ ] `git diff --check`
- [ ] Disposable fixture/integration smoke (if relevant)

**Exact SHA / environment / command / result:**

## Safety & evaluation boundaries

- [ ] No hidden validation/holdout labels entered agent-visible prompts.
- [ ] No evaluator, metric or budget rule changed without dedicated regression tests.
- [ ] No secrets, credentials, private benchmarks or personal data are included.
- [ ] Documentation and changelog reflect user-visible behavior.

## Evidence / limitations

What did you measure? What is still unverified?
