# Security Policy

## Reporting a vulnerability

Please report security issues privately. **Do not open a public GitHub issue for a vulnerability.**

Email **security@lucidprinciples.com** with:

- A description of the issue and its impact
- Steps to reproduce (proof of concept if possible)
- Affected version / commit
- Any suggested remediation

We aim to acknowledge reports within a few business days and will keep you updated as we investigate. Please give us reasonable time to release a fix before any public disclosure.

## Scope

cove-core is self-hosted software. Operators are responsible for their own deployment: secrets management, network exposure, reverse-proxy/TLS configuration, and keeping dependencies updated. Reports most useful to us include:

- Authentication / authorization bypass in the dashboard or API
- Prompt-injection paths that escalate to code execution, file access, or data exfiltration beyond intended tool scope
- Secret leakage (logs, responses, repo)
- Sandbox or container escape in the agent execution paths

## Hardening notes for operators

- Set strong values for `POSTGRES_PASSWORD`, `NC_ADMIN_PASSWORD`, and `SHARED_CONTAINER_SECRET`.
- Never commit your `.env` or instance config (both are gitignored by default).
- Put the app behind a reverse proxy with TLS; do not expose it directly to the internet over plain HTTP.
- Third-party skills run through a safety/prompt-injection gate and stay hidden until an operator approves them — review before approving.
- Keep the image and dependencies updated.

This is pre-release software (v1.0.0); treat production exposure accordingly.
