# M5 execution profiles: security review

- Focused pass covered profile parsing, launch receipts and model/provider argv propagation.
- Initial finding [P2]: profile validation included Pydantic `input_value` in user-visible errors and could echo a secret placed in a rejected field. Resolution: validation summaries omit input/context and suppress chained exception details; regression test asserts a sample secret is absent.
- Initial finding [P2]: raw extra args could shadow typed model/provider/reasoning while durable receipts recorded only typed settings. Resolution: `AgentConfig` rejects conflicting typed/raw model, provider and reasoning overrides; CLI provider/thinking options are represented as typed fields. Regression tests cover shadowing.
- No remaining findings in the scoped pass. No arbitrary command or raw argv is accepted from the TOML profile file; existing CLI command/extra-arg trust remains unchanged.
- Repair validation is included in the 92-passed scoped M5 test run.
