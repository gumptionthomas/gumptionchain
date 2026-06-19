# Changelog

All notable changes to GumptionChain are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0]

First public release of GumptionChain under its own name and package.

### Highlights

- **Proof-of-work ledger** assigning tokens to subjects (UTF-8 strings,
  1–79 chars) as `opposition` or `support`, each rescindable. Units are
  GRIT/grains (1 GRIT = 100 grains).
- **Dual surface:** a Flask web app (browser explorer + JSON API) and a
  `gumptionchain` CLI (`gc` alias).
- **Permissioned network** with role-based API access
  (`READER` < `TRANSACTOR` < `MILLER` < `ADMIN`) keyed off signing-key
  addresses.
- **Stateless per-request authentication** (`gc-sig-v1`): every request is
  signed with an RSA signing key and bound to the target node. See
  [`docs/api-auth-protocol.md`](docs/api-auth-protocol.md).
- **Anti-spam controls:** global mempool cap (`MAX_PENDING_TXNS`),
  per-transactor in-flight quota (`MAX_PENDING_PER_TRANSACTOR`), and
  per-app submission accounting.
- **Schema migrations** via Flask-Migrate/Alembic, with a `db check` CI gate
  enforcing model/migration parity.
- **Supply-chain hardening:** committed `uv.lock`, pip-audit CVE scanning,
  Dependabot, and SHA-pinned GitHub Actions.

[Unreleased]: https://github.com/gumptionthomas/gumptionchain/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/gumptionthomas/gumptionchain/releases/tag/v1.0.0
