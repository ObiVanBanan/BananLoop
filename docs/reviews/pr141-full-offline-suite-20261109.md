# PR #141 full offline suite — 2026-11-09

## Result

The full collected repository test suite passed using a temporary home/state
directory and with live-model flags unset:

```text
1021 passed, 2 skipped, 7 subtests passed in 65.20s
```

Reproduction:

```sh
env -u BANANLOOP_LIVE_INTEGRATED_OPTIMIZATION -u BANANLOOP_TITANIC_LIVE \
  HOME=/tmp/bananloop-full-offline-home \
  XDG_CONFIG_HOME=/tmp/bananloop-full-offline-home/.config \
  XDG_STATE_HOME=/tmp/bananloop-full-offline-home/.local/state \
  .venv/bin/python -m pytest -o addopts= -vv -ra
```

The run used Python 3.12.15, pytest 9.2.0, and the repository's `.venv`.
`git diff --check` passed. No live model or paid API requests were enabled.

## Skips

- `tests/test_selector_preregistration.py`: pinned selector source commit
  `cd753c97c62148c3ccd3f030251e96a4d19b23bd` is unavailable in this checkout.
- `tests/test_v02_full_prereg.py`: Windows ACL/reparse-point contract.

## Isolation note

An earlier attempt with the normal user `HOME` was stopped after process
inspection showed a test writing SQLite WAL data under `/home/sonik/.bananloop`.
The completed run above was repeated with `HOME` and XDG paths under `/tmp`.
The earlier interrupted state was left untouched rather than deleting a path
that could contain pre-existing user campaign data.

## Scope limit

This proves the current offline repository suite only. It does not prove the
requested autonomous live Researcher → Protocol Verifier → owner approval →
Optimizer → Integration Verifier run; the integrated acceptance harness still
uses a deterministic Researcher protocol draft and deterministic integration
verifier in offline mode.

Base checkout: `a7e93aae719bf3a7c5fbeb5f9c53380357c47063` plus local changes.
SHA-256 of tracked `src/` and `tests/` diff at verification time:
`301443c1b831a5c0ea1fe95a0c1f26f5779ffa55f682cc88708f9274b44d59d5`.
