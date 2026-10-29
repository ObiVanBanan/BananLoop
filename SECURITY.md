# Security policy

## Supported versions

| Version | Status |
| --- | --- |
| v0.1.x | Research Preview; security fixes are best-effort |
| Earlier experimental branches | No guaranteed security support |

## Reporting

Please report security vulnerabilities **privately** using the repository's GitHub Security tab and "Report a vulnerability" / private advisory workflow when available. If that workflow is unavailable, do not post an exploit or secret in a public Issue; ask a maintainer for a private reporting channel first.

## Trust boundary

BananLoop targets **trusted local projects**. Its sanitized working copies help prevent accidental exposure of evaluator/GOLD files, but they are **not an OS-grade sandbox** and cannot guarantee isolation from a malicious process.

Agent implementations may execute commands with the user's credentials and permissions. Review provider capabilities, use disposable environments, protect secret material, and do not point agents at untrusted repositories with privileged tokens.

Never include raw holdout labels, private benchmark answers or credentials in agent prompts, logs or bug reports.
