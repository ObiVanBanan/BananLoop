# Security review: M3 evaluator signal sanity

Date: 2026-10-05

## Initial finding and disposition

Bootstrap-selected labels and evaluator-produced values are untrusted. A malicious or mistaken bootstrap could select convenient case IDs and receive a numerical separation pass. The implementation now labels this result `DIAGNOSTIC_ONLY` / `diagnostic_only_unverified_labels`, excludes it from final-assessment decisions, and has a regression proving it cannot upgrade campaign assessment.

## Other checks

- Case IDs are treated as strings and matched to evaluator output; they are not used as filesystem paths.
- A/A evaluator runs use bounded disposable workspaces; M3 adds no path-based artifact handling.
- No additional path traversal, credential exposure, or workspace escape issue was found in the M3 additions.

Independent evidence promotion remains blocked on a future core-verifiable fixture and label provenance contract. This is a product-evidence limitation, not an unresolved security vulnerability in this implementation.
