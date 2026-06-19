# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, report them privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
go to the repository's **Security** tab and click **Report a vulnerability**.

Please include as much of the following as you can:

- the type of issue (e.g. authentication bypass, signature forgery, denial of
  service, injection),
- the affected component (node API, signing-key auth, CLI, peer gossip, …),
- steps to reproduce or a proof of concept,
- the impact and how an attacker might exploit it.

We will acknowledge your report, keep you informed of progress, and credit you
in the advisory once a fix ships (unless you prefer to remain anonymous).

## Supported versions

GumptionChain is pre-1.x in spirit but versioned from 1.0.0. Security fixes
target the latest released version.

| Version | Supported |
| ------- | --------- |
| 1.0.x   | ✅        |
| < 1.0   | ❌        |

## Scope notes

GumptionChain is a permissioned proof-of-work chain. A few security-relevant
design facts worth knowing before reporting:

- **API authentication** is a stateless per-request signing-key signature
  (`gc-sig-v1`): every request is signed and node-bound. See
  [`docs/api-auth-protocol.md`](docs/api-auth-protocol.md) for the full
  protocol.
- **Roles** (`READER` < `TRANSACTOR` < `MILLER` < `ADMIN`) are enforced against
  exact-address allowlists re-checked on every request, so revocations take
  effect immediately.
- **Open transacting** (`GC_TRANSACTOR_ADDRESSES='["*"]'`) exposes load, not
  theft: balance, ownership, and double-spend validation still hold. Operators
  running the wildcard should keep the `MAX_PENDING_TXNS` cap and a per-IP rate
  limit at the reverse proxy.

## Supply-chain practices

- `uv.lock` is committed and authoritative; CVE remediation goes through
  `uv lock --upgrade-package <name>`.
- pip-audit runs in CI on every PR, on push to `main`, and on a weekly cron.
- Dependabot watches pip, Docker, and GitHub Actions dependencies.
- Third-party GitHub Actions are pinned to commit SHAs.
