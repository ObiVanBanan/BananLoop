# M5 execution profiles: review report

- Review pass: one, after scoped tests; no second review was run.
- Initial finding [P1]: a campaign pinned only a Codex named-profile label while the external profile file could change between start and resume. Resolution: removed mutable named profiles from the BananLoop profile contract; role configs use explicit model/provider/reasoning values and preserve the user's external default only when these are absent.
- Initial finding [P2]: ADR advertised arbitrary command/argv fields that the profile loader did not accept. Resolution: narrowed the contract and docs to typed backend/model/provider/reasoning fields.
- Repair validation: `tests/test_agent_profiles.py tests/test_codex_adapter.py tests/test_pi_adapter.py tests/test_bootstrap.py tests/test_engine.py tests/test_cli.py tests/test_integrity.py` — 92 passed.
- Full suite result is recorded in `plan/execution-profiles-completion.md` after the final run.
