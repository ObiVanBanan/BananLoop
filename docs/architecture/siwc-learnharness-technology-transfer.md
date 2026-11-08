# LearnHarness → BananLoop: SIWC technology transfer

Reference: https://github.com/amosblomqvist/learnharness
(source code license: **AGPL-3.0**, not automatically compatible with
copying into BananLoop's MIT source tree).

| Mechanism | LearnHarness evidence | BananLoop decision |
| --- | --- | --- |
| Official OpenAI OAuth with dynamic registration | `electron/chatgpt.cjs` | **REIMPLEMENT** independently from official OpenAI SIWC docs |
| Loopback callback, PKCE, nonce, stable host ID | `electron/chatgpt.cjs` | **REIMPLEMENT**, no copied code |
| Trusted owner and request lease | `src/agent/chatgpt-bridge.ts`, `electron/chatgpt-ipc.cjs` | **ADAPT** to trusted Python Core and v2 job/pinned-role authority |
| Public Responses streaming | `src/agent/chatgpt-stream.ts` | **REIMPLEMENT**, stateless one-shot SSE with fail-closed terminal event |
| Subscription account and model catalog | `src/agent/subscription-connections.ts` | **ADAPT** for explicit owner-selected SIWC model; never API fallback |
| LearnHarness teaching/practice tools and UI | `src/agent/teacher-agent.ts` | **REJECT**, irrelevant to BananLoop |
| Direct host/agent tool access | Not copied | **REJECT**: only BananLoop ToolGateway handles allowed actions |

Official documentation:
https://developers.openai.com/siwc/token-sharing-open-source
https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference

**Security difference:** In BananLoop the model context must contain only
allowlisted public/DEV research evidence. A subscription token is held
outside of the sandbox and must never be bound into a worker job payload.
