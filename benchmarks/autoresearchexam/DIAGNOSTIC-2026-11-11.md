# AutoResearchExam single-task diagnostic — 2026-11-11

Task: `budgeted-imputation-mcar50` (AutoResearchExam 1.1.0, source revision `23c0d92e11af1dea821f972b5f0a739060958002`).

The Harbor task container and BananLoop Exploration started successfully. The former source-visibility error did not recur. Researcher and independent verifier completed, but the verifier rejected the frozen public evaluator protocol. The campaign stopped before creating an Optimization child; the optimizer and integration verifier were never called.

- Parent campaign: `c-3ad1a9ac343cb94016e28fbc38ed8795`
- Harbor task/campaign: `c-d61b99d02b4ad663ed003a45fbe25fa9`
- One task iteration reserved; task cap was one hour and five iterations; no retry.
- Two live calls, 16,660 provider-reported tokens; subscription cost is unknown.

Verifier rationale from the durable worker result:

> The supplied material identifies the protocol and names the metric field, backend, and public routes, but does not establish a complete metric contract or the evaluator’s scoring and error behavior. The evaluator source is explicitly descriptive and delegates to a Harbor host adapter whose behavior is not provided. Although private grader access is declared false, the evaluator has network access to a score endpoint, and the evidence does not establish whether that endpoint or its validation_score depends on hidden data. The tier summaries also show the same manifest digest for dev and validation, without evidence clarifying whether this overlap is intentional and fair. These gaps prevent approval.

Interpretation: this is an evaluator-evidence and tier-separation failure, not a Docker setup failure and not the earlier source-visibility failure. The rejection was correctly fail-closed. The full trace, campaign database and report are in `/home/sonik/bananloop-benchmark-env/runs/autoresearchexam-diagnostic-20261111.zip` (2.9 MB). The archive excludes Harbor grader artifacts that could contain sealed results.

Required implementation work before a new live attempt:

1. Give the independent verifier a complete, task-pinned public metric/endpoint contract and the exact BananLoop host adapter source or auditable digest-bound behavior.
2. Separate DEV from VALIDATION in both the actual evaluator runner and its manifests. DEV should use only the task's public development evaluator; VALIDATION may use only Harbor's public score endpoint. Neither lane may access private grader routes or outputs.
3. Add regression tests proving protocol review sees the source and distinct tier scopes, and that a rejected review prevents Optimization dispatch while preserving the rationale in the owner report.
