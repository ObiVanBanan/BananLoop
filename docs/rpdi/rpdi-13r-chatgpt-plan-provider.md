# RPDI-13R Addendum — official Sign in with ChatGPT provider

**Status: implementation in progress; no live subscription usage.**
This design adds an opt-in ChatGPT-plan provider to the v0.2 Research
Runtime, as requested by the owner. It does not qualify the complete
v0.2 Full or satisfy the independent RPDI-14 gates on its own.

## Verified official contract and reference

- [OpenAI: OSS Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source)
- [OpenAI: registration](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [OpenAI: models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [OpenAI: preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [OpenAI: token renewal](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [LearnHarness](https://github.com/amosblomqvist/learnharness) uses
  OAuth and the public Responses API; its source is **AGPL-3.0**.
  This implementation must be independently written from official docs,
  not copied from LearnHarness into MIT BananLoop.

## Architecture

```text
Operator CLI/UI -- explicit consent
       |
 Local OAuth (PKCE/S256, state, nonce, ID-token JWT verify, dynamic registration)
       |
 Host-only protected credential vault (outside repo and sandbox)
       |
 Trusted SIWC Responses client -- GET /v1/models + POST /v1/responses
    |  store=false; stream=true; input[]; no API-key fallback
    |  only complete on response.completed
    v
 Pinned researcher one-shot model_reply(public role_context + approved documents)
       |
 V2ResearchRuntime -> HybridScheduler -> DurableWorkerQueue
       |              -> PinnedRoleExecutorV1
       v
 ToolGateway -> EvidenceOnlyAttemptAdapter -> Research Ledger
```

The user must actively sign in using the system browser and grant
`chatgpt.tokens.use.direct`. Persist `ext_agent_host_id` across sign-ins.
The registration callback returns the actual issued OAuth `client_id`,
which is **not** `dynamic_agent_client`. Exchange using that client ID and
the exact loopback URI; verify signature/issuer/audience/expiry/nonce for
the ID token via official JWKS and the granted scope.

Credential persistence: owner-only 0600 file under a private home
directory, atomic replace; never in repo, worker snapshot, queue request,
logs, artifacts or PR. An explicit operator logout must clear usable
credentials. Refresh is separate from model inference.

Model selection: query `GET https://api.openai.com/v1/models` using the
selected account. Only a returned model slug is a valid pinned model for
that account; even catalog presence does not prove live entitlement.

Responses HTTP contract: OAuth Bearer to the public endpoint, explicit
`store:false`, `stream:true`, stateless `input` array, developer
instructions (not system role), no unsupported `max_output_tokens`,
`background`, or `previous_response_id`. Reject incomplete, failed,
nonterminal, malformed or oversized streams, and avoid API-key fallback.
Streaming usage receipts should be reported if returned, otherwise UNKNOWN.

## Safety, cost and acceptance

- Do not pass OAuth credentials or direct filesystem/shell access to any
  model-controlled worker; Core alone manages the bearer credential.
- Reject privileged paths and unknown tools through the ToolGateway.
  Never put HOLDOUT, evaluator, seed or auth credentials into the
  model's user-facing input.
- Explicit affirmative operator action must precede the **first**
  subscription-consuming model request. Login and catalog lookup
  do not constitute a paid API request, but may contact OpenAI.
- ChatGPT-plan use is subject to account/workspace and app eligibility.
  It consumes the user's shared plan limits; do not claim a dedicated
  free token quota or a hard monetary cost meter. No alternate API
  billing or hidden API-key path is permitted.
- Record model slug, timestamp, completed/failed status, request ID,
  usage if actually supplied, and UNKNOWN otherwise. Do not log tokens.
- A one-shot researcher response is an **exploration transport smoke**,
  not a proof that all Full lanes (Optimization, Verification, pivot)
  are implemented, not evidence of superiority and not RPDI-14.

## Definition of Done

1. Offline tests for OAuth PKCE/callback validation, JWKS ID-token
   verification, permission rejection, credential permissions,
   refresh/logout and cache failure.
2. Offline mocked SSE tests including incomplete/error/limit streams,
   oversized output, malformed structured result and model pin mismatch.
3. Dummy model-model bridge respects pinned identity and ToolGateway
   deny rules, preserving the existing two-node Research Ledger test.
4. Local Manjaro instructions for `login`, `status`, `models` without
   model inference. Live owner-authorized single call gated separately.
5. Current branch exact-head CI green and code review before moving
   PR #134 out of Draft.
