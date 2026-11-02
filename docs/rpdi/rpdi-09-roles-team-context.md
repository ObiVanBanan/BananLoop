# RPDI-09: pinned roles, deterministic teams, and fresh context

## Contract

V2 role execution is separate from the mutable v1 `AgentConfig` and
`agent_profiles.toml` path. A v2 campaign's `role_registry_digest` resolves one
immutable `RoleRegistryV1`. Every role pins its adapter, provider, model,
model-version label, prompt digest, capability ceiling, context allowlist and
resource defaults. A missing role or digest mismatch fails closed. Provider
errors never trigger a different model or provider.

`TeamFactoryV1` expands the frozen registry and requested counts in canonical
registry priority/ID order. It honors each role's minimum instance count and
fits optional instances under the supplied agent-call and model-token ceilings.
Each member has a content-derived stable ID. The final `TeamManifestV1` binds
the campaign, approved spec hash, registry digest, every effective role and the
aggregate call/token allocation. `TeamManifestAuthority` pins that manifest
durably once per campaign; retries replay the same bytes, while a replacement
or resume against another spec/registry is rejected.

Specializations only remove base tools, read/write path patterns, context
fields, calls or tokens; they may disable network or require a stronger
backend. Path narrowing is intentionally conservative: it removes exact
patterns instead of trying to prove arbitrary glob-language inclusion. A
specialization has no provider, model, adapter, prompt or extra-argument field.
Model selection is therefore unchanged by the Team Factory.

`build_role_context` reconstructs a new immutable object for each invocation
from fields explicitly allowed by that role. Unknown fields, including
validation/holdout and another role's private notes, are discarded. Each worker
job must persist `TeamManifestV1.job_binding(...)` in its immutable queue
request, including the manifest, registry, spec and role-context digests. The
durable worker queue remains the final campaign-call reservation and fencing
authority. Role-instance call and per-call token ceilings are reserved
transactionally at queue insertion; identical retries reuse the same
reservation. The trusted executor receives the pinned per-call token ceiling.
Provider-receipt token settlement and ambiguous-call reconciliation remain
owned by the RPDI-03/04 runtime path.

## Legacy compatibility

V1 role names, profile parsing, approved hashes, and Engine behavior are
unchanged. Existing aliases (`bootstrap`, `propose`, `select`, `implement`,
`analyze`, and adapter-only `pivot_advice`) are not silently converted into
v2 roles. Any v1-to-v2 adapter must use an explicit versioned mapping and a
registry whose provider/model/prompt bindings have already been frozen.

## Acceptance

- Registry digest and version are canonical, and unknown/mismatched pins fail.
- Rebuilding a team after restart produces the identical manifest and instance
  IDs; durable pinning rejects replacement and mismatched spec/registry.
- Dynamic sizing is deterministic and stays within call/token ceilings while
  preserving minimum roles; retries count the same persisted instances once.
- Specializations cannot widen permissions/context, increase resource limits,
  or change provider/model/adapter.
- Every queued job binds the member, registry, team, approved spec, effective
  permissions and freshly reconstructed context by digest.
- No provider/model substitution occurs on failure.
- V1 approved-hash, profile, and Engine regressions remain green.

## Current implementation boundary

This change supplies strict registry/team/context contracts and durable team
pinning. The evidence-only exploration adapter consumes `job_binding`, requires
an executor declaration matching the pinned adapter/provider/model/version,
and applies the effective role policy through the RPDI-06 `PermissionEngine`
before dispatch. Dashboard team controls and role contribution measurements
remain later integration work; benchmark role labels remain provisional until
mapped to the canonical registry.
