# v0.1.0 Research Preview — release checklist

The preview is intended for early developers to try on trusted repositories. This checklist is an operational release gate; file creation alone does not mean the version is published.

## Code / evaluation

- [x] Independent QA of semantic selector patch on SHA `c698e4966a0cf8621dbfbff8145c1397b278c2e8`: 335 pytest passed, six fresh Luna replay selections accepted, one CPU mini matcher smoke passed.
- [x] Version in `pyproject.toml` is 0.1.0.
- [ ] Run full suite on the exact final main release commit (documentation-only changes should not alter logic but warrant verification).
- [ ] Check clean install and fixture on Linux / Windows; record actual stdout and versions.
- [ ] Review private evaluator and HOLDOUT isolation using the final tree.
- [ ] Resolve CI infrastructure/billing issue, or explicitly document verified substitute checks before publishing.

## GitHub presentation

- [x] Distinctive banner, short marketing README, honest DEV demo.
- [x] MIT license, contributing guide, security policy, code of conduct.
- [x] Issue and PR templates, changelog, roadmap.
- [ ] Verify README/banner display in GitHub dark **and** light theme.
- [ ] Set repository About description and topics (maintainer GitHub settings).
- [ ] If desired, intentionally change repository visibility from private to public after reviewing secrets/history.
- [ ] Check all historical branches, tags, PRs and commit history for credentials before making repository public.

## Publish

- [ ] Confirm release owner wants to publish `v0.1.0` as a tag and GitHub Release.
- [ ] Create annotated `v0.1.0` tag from audited main SHA.
- [ ] Publish GitHub Release titled “BananLoop v0.1.0 — Research Preview”, with `CHANGELOG.md` summary and truthful limitations.
- [ ] Run benchmarks and publish their actual environment, budgets, seeds and DEV/validation/holdout scopes separately from preview demo.

No continuous benchmark winner or general Codex performance advantage has been established by the mini matcher fixture.
